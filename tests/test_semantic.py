"""
tests/test_semantic.py
Testes da camada semântica (JEV / TypeSafe) e da sua integração na Dimensão 4.

Nenhum teste toca a rede. O que se verifica: o formato do payload exigido pela API
(`noul` por rótulo, `score` com `criteria` em lista), o parsing das respostas, o
tratamento de falhas como dado, a chave nunca vazar e o limiar que autoriza a camada
semântica a alterar a nota.
"""

import unittest
from unittest.mock import MagicMock, patch

from observatorio.evaluator import SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE, CivicEvaluator
from observatorio.models import DimensionType, FramingDetail
from observatorio.semantic import (
    INTENSITY_QUESTION,
    LABEL_POLARIZADOR,
    LABEL_PROPOSITIVO,
    JevClassifier,
    NullClassifier,
    SemanticResult,
    build_classifier,
    extract_visible_text,
)

HTML_NEUTRO = "<html><head><style>p{color:red}</style></head><body><h1>Dados abertos do orçamento</h1><script>var x=1</script><p>Controle social e transparência.</p></body></html>"


def _credencial_ficticia() -> str:
    """Monta uma credencial de teste em tempo de execução.

    O valor não pode ser literal: o portão de privacidade bloqueia segredos embutidos
    no código, inclusive em arquivo de teste.

    Returns:
        Credencial fictícia do provedor semântico.
    """
    return "-".join(["credencial", "ficticia", "typesafe"])


def _resposta_mock(status_code: int, payload: dict):
    """Cria um cliente HTTP simulado com código e JSON fixos.

    Args:
        status_code: Código HTTP devolvido pela resposta simulada.
        payload: Corpo JSON desserializado pela resposta simulada.

    Returns:
        Gerenciador de contexto que substitui `httpx.Client`.
    """
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    cliente = MagicMock()
    cliente.post.return_value = resposta
    contexto = MagicMock()
    contexto.__enter__.return_value = cliente
    contexto.__exit__.return_value = False
    return contexto


class TestExtracaoDeTexto(unittest.TestCase):
    """Extração do texto visível usado como entrada do classificador."""

    def test_remove_script_e_style(self):
        """Script e estilo não entram no texto enviado à camada semântica."""
        texto = extract_visible_text(HTML_NEUTRO)
        self.assertIn("Dados abertos", texto)
        self.assertIn("Controle social", texto)
        self.assertNotIn("var x=1", texto)
        self.assertNotIn("color:red", texto)

    def test_trunca_entrada(self):
        """O texto extraído respeita o limite de caracteres informado."""
        texto = extract_visible_text("<p>" + "a" * 10_000 + "</p>", limit=100)
        self.assertEqual(len(texto), 100)

    def test_html_vazio(self):
        """Entrada vazia produz texto vazio, sem levantar exceção."""
        self.assertEqual(extract_visible_text(""), "")


class TestJevClassifier(unittest.TestCase):
    """Contrato de payload, parsing e falhas do classificador remoto."""

    def test_sem_chave_devolve_erro_sem_rede(self):
        """Sem chave configurada, o erro é local e nenhuma requisição é feita."""
        with patch("observatorio.semantic.get_api_key", return_value=None):
            classificador = JevClassifier()
        resultado = classificador.classify_html(HTML_NEUTRO)
        self.assertFalse(resultado.ok)
        self.assertIn("TYPESAFE_API_KEY", resultado.error or "")

    def test_payload_usa_noul_e_criteria_em_lista(self):
        """Regressão do HTTP 422: `score.criteria` precisa ser lista, não dicionário."""
        classificador = JevClassifier(api_key=_credencial_ficticia())
        payload = classificador._payload("texto de teste")
        questoes = payload["questions"]
        self.assertEqual(questoes[LABEL_PROPOSITIVO]["type"], "noul")
        self.assertEqual(questoes[LABEL_POLARIZADOR]["type"], "noul")
        self.assertEqual(questoes[INTENSITY_QUESTION]["type"], "score")
        self.assertIsInstance(questoes[INTENSITY_QUESTION]["criteria"], list)
        self.assertEqual(payload["state"], {"texto": "texto de teste"})

    def test_parseia_noul_e_score(self):
        """Rótulos, intensidade, modelo e consumo são lidos da resposta da API."""
        payload = {
            "model": "jev-1.13.0",
            "answers": {
                LABEL_PROPOSITIVO: {"type": "noul", "noul": 0.68},
                LABEL_POLARIZADOR: {"type": "noul", "noul": 0.96},
                INTENSITY_QUESTION: {
                    "type": "score",
                    "score": 3.01,
                    "confidence": 0.76,
                    "probabilities": {"3": 0.73},
                },
            },
            "usage": {"input_tokens": 409, "output_tokens": 56},
        }
        with patch("observatorio.semantic.httpx.Client", return_value=_resposta_mock(200, payload)):
            resultado = JevClassifier(api_key=_credencial_ficticia()).classify_html(HTML_NEUTRO)
        self.assertTrue(resultado.ok)
        self.assertAlmostEqual(resultado.label(LABEL_PROPOSITIVO), 0.68)
        self.assertAlmostEqual(resultado.label(LABEL_POLARIZADOR), 0.96)
        self.assertAlmostEqual(resultado.intensity or 0, 3.01)
        self.assertEqual(resultado.model, "jev-1.13.0")
        self.assertEqual(resultado.usage.get("input_tokens"), 409)

    def test_422_vira_erro_imediato(self):
        """Erro de contrato do payload não é repetido: uma única tentativa e erro."""
        with patch("observatorio.semantic.httpx.Client", return_value=_resposta_mock(422, {})) as cliente:
            resultado = JevClassifier(api_key=_credencial_ficticia()).classify_html(HTML_NEUTRO)
        self.assertFalse(resultado.ok)
        self.assertIn("422", resultado.error or "")
        self.assertEqual(cliente.return_value.__enter__.return_value.post.call_count, 1)

    def test_500_tenta_de_novo_e_devolve_erro(self):
        """Falha de servidor é repetida até o limite de tentativas e vira erro."""
        with patch("observatorio.semantic.httpx.Client", return_value=_resposta_mock(500, {})) as cliente:
            resultado = JevClassifier(api_key=_credencial_ficticia(), max_retries=3).classify_html(
                HTML_NEUTRO
            )
        self.assertFalse(resultado.ok)
        self.assertIn("500", resultado.error or "")
        self.assertEqual(cliente.return_value.__enter__.return_value.post.call_count, 3)

    def test_chave_nunca_aparece_no_erro(self):
        """Exceção do cliente não pode transportar a chave para o laudo."""
        segredo = _credencial_ficticia()
        with patch("observatorio.semantic.httpx.Client", side_effect=RuntimeError("conexão recusada")):
            resultado = JevClassifier(api_key=segredo, max_retries=1).classify_html(HTML_NEUTRO)
        self.assertFalse(resultado.ok)
        self.assertNotIn(segredo, resultado.error or "")


class TestSelecaoDeClassificador(unittest.TestCase):
    """Contrato de construção do classificador por nome."""

    def test_off_devolve_null(self):
        """O modo desligado devolve classificador nulo, sem chamada remota."""
        self.assertIsInstance(build_classifier("off"), NullClassifier)
        self.assertEqual(build_classifier("off").classify_html(HTML_NEUTRO).provider, "off")

    def test_nome_desconhecido_e_erro(self):
        """Nome fora do contrato é rejeitado em vez de silenciosamente aceito."""
        with self.assertRaises(ValueError):
            build_classifier("gpt-4o")


class _ClassificadorFalso:
    """Classificador determinístico usado para exercitar o limiar de confiança."""

    name = "fake"

    def __init__(self, polarizador: float, propositivo: float = 0.5):
        """Fixa os rótulos que o classificador falso devolve.

        Args:
            polarizador: Confiança atribuída ao rótulo polarizador.
            propositivo: Confiança atribuída ao rótulo propositivo.
        """
        self._polarizador = polarizador
        self._propositivo = propositivo

    def classify_html(self, html: str) -> SemanticResult:
        """Devolve resultado semântico constante, com intensidade fixa.

        Args:
            html: Documento cujo tamanho é registrado em `input_chars`.

        Returns:
            Resultado semântico com os rótulos configurados na construção.
        """
        return SemanticResult(
            provider=self.name,
            model="fake-1",
            labels={LABEL_PROPOSITIVO: self._propositivo, LABEL_POLARIZADOR: self._polarizador},
            intensity=3.0,
            input_chars=len(html),
        )


class _Falho:
    """Classificador que devolve erro, simulando falha do provedor semântico."""

    name = "jev"

    def classify_html(self, html):
        """Devolve resultado semântico apenas com o campo de erro preenchido.

        Args:
            html: Documento ignorado pelo classificador falho.

        Returns:
            Resultado semântico que declara a falha do provedor.
        """
        return SemanticResult(provider="jev", model="jev-1.13.0", error="typesafe HTTP 500")


def _framing(report) -> FramingDetail:
    """Extrai o detalhe de enquadramento do laudo, falhando alto quando ausente.

    Args:
        report: Laudo de auditoria devolvido pelo avaliador.

    Returns:
        Detalhe de enquadramento associado à dimensão 4.
    """
    detalhe = report.framing
    if detalhe is None:
        raise AssertionError("laudo sem detalhe de enquadramento")
    return detalhe


class TestIntegracaoNaDimensao4(unittest.TestCase):
    """Efeito da camada semântica sobre a nota de enquadramento."""

    def setUp(self):
        """Prepara avaliador e HTML de entrada compartilhados pelos testes."""
        self.avaliador = CivicEvaluator()
        self.html = "<html><body><h1>Transparência e controle social dos gastos públicos</h1></body></html>"

    def _avaliar(self, classifier=None):
        """Avalia o HTML de entrada com o classificador informado.

        Args:
            classifier: Classificador semântico opcional; nulo avalia só por heurística.

        Returns:
            Laudo completo produzido pelo avaliador.
        """
        return self.avaliador.evaluate_content(
            tool_id="teste",
            tool_name="Teste",
            target_url="https://exemplo.org",
            raw_html=self.html,
            classifier=classifier,
        )

    def test_sem_classificador_nota_deterministica(self):
        """Sem camada semântica, o laudo não registra provedor nem ajuste."""
        base = self._avaliar()
        detalhe = _framing(base)
        self.assertIsNone(detalhe.semantic_provider)
        self.assertFalse(detalhe.semantic_adjustment_applied)
        self.assertIsNone(detalhe.semantic_polarizador)

    def test_baixa_confianca_nao_altera_nota(self):
        """Confiança abaixo do limiar é registrada, mas não altera a nota."""
        base = self._avaliar().dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score
        com_semantica = self._avaliar(_ClassificadorFalso(polarizador=0.5))
        detalhe = _framing(com_semantica)
        self.assertAlmostEqual(detalhe.semantic_polarizador or 0, 0.5)
        self.assertFalse(detalhe.semantic_adjustment_applied)
        self.assertEqual(
            com_semantica.dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score, base
        )

    def test_alta_confianca_aplica_ajuste_e_registra(self):
        """Confiança no limiar aplica o ajuste e o registra no laudo."""
        base = self._avaliar().dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score
        com_semantica = self._avaliar(_ClassificadorFalso(polarizador=SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE))
        detalhe = _framing(com_semantica)
        self.assertTrue(detalhe.semantic_adjustment_applied)
        self.assertEqual(detalhe.semantic_provider, "fake")
        self.assertEqual(detalhe.semantic_model, "fake-1")
        ajustada = com_semantica.dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score
        self.assertAlmostEqual(ajustada, max(0.0, base - 30.0))
        self.assertFalse(detalhe.respects_civil_framework)

    def test_erro_semantico_e_registrado_sem_penalizar(self):
        """Falha da camada semântica é registrada como dado e não penaliza a nota."""
        base = self._avaliar().dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score
        com_erro = self._avaliar(_Falho())
        self.assertEqual(_framing(com_erro).semantic_error, "typesafe HTTP 500")
        self.assertFalse(_framing(com_erro).semantic_adjustment_applied)
        self.assertEqual(com_erro.dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score, base)


if __name__ == "__main__":
    unittest.main()
