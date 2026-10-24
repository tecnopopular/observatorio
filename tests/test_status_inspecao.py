"""Testes das correções C1 (não inspecionado e acesso restrito), C2 (fonte fabricada) e C3 (OSI).

Cada classe cobre um defeito específico do motor de avaliação, para que a regressão apareça
como falha de teste e não como avaliação errada publicada.
"""

import email.message
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from observatorio.engine import audit_single_initiative
from observatorio.evaluator import CivicEvaluator
from observatorio.fetch import FetchResult, HttpFetcher
from observatorio.models import AuditLevel, DimensionType
from observatorio.semantic import NullClassifier


def _http_error(url: str, code: int, location: str | None = None) -> HTTPError:
    """Monta um HTTPError com cabeçalho Location opcional.

    Args:
        url: Endereço que respondeu o erro.
        code: Código HTTP devolvido.
        location: Destino do redirecionamento, quando houver.

    Returns:
        Exceção pronta para ser usada como efeito colateral em mocks.
    """
    cabecalhos = email.message.Message()
    if location:
        cabecalhos["Location"] = location
    return HTTPError(url, code, "teste", cabecalhos, None)


def _cov(relatorio):
    """Extrai a cobertura de inspeção do laudo, falhando quando ela não existe.

    Args:
        relatorio: Laudo produzido pelo avaliador.

    Returns:
        Objeto de cobertura do laudo.
    """
    cobertura = relatorio.coverage
    if cobertura is None:
        raise AssertionError("laudo sem cobertura")
    return cobertura


def _prov(relatorio):
    """Extrai o detalhe de proveniência do laudo, falhando quando ele não existe.

    Args:
        relatorio: Laudo produzido pelo avaliador.

    Returns:
        Objeto de proveniência do laudo.
    """
    detalhe = relatorio.provenance
    if detalhe is None:
        raise AssertionError("laudo sem proveniência")
    return detalhe


def _floss(relatorio):
    """Extrai o detalhe de código aberto do laudo, falhando quando ele não existe.

    Args:
        relatorio: Laudo produzido pelo avaliador.

    Returns:
        Objeto de FLOSS do laudo.
    """
    detalhe = relatorio.floss
    if detalhe is None:
        raise AssertionError("laudo sem FLOSS")
    return detalhe


class TestNaoInspecionado(unittest.TestCase):
    """Garante que ausência de leitura não recebe avaliação de qualidade."""

    def test_pagina_vazia_nao_recebe_avaliacao(self):
        """Página vazia deve resultar em não inspecionado, sem nota publicável."""
        r = CivicEvaluator().evaluate_content(
            tool_id="vazio",
            tool_name="Vazio",
            target_url="https://exemplo.org",
            raw_html="",
            response_time_ms=8000,
            html_bytes=0,
            classifier=NullClassifier(),
        )
        self.assertEqual(r.status, AuditLevel.NAO_INSPECIONADO)
        self.assertIn("Não Inspecionado", r.badge_label)
        self.assertIsNone(r.total_score)
        self.assertIn("não atribuída", r.summary_markdown or "")
        self.assertFalse(_cov(r).inspected)
        self.assertFalse(_cov(r).complete)
        self.assertEqual(_cov(r).reason, "sem conteúdo legível")

    def test_acesso_restrito_nao_recebe_avaliacao(self):
        """Página atrás de autenticação deve ser marcada como não inspecionada."""
        r = CivicEvaluator().evaluate_content(
            tool_id="gate",
            tool_name="Gate",
            target_url="https://exemplo.org",
            raw_html="",
            inspected=False,
            inspection_reason="acesso restrito: redirecionado para autenticação",
            classifier=NullClassifier(),
        )
        self.assertEqual(r.status, AuditLevel.NAO_INSPECIONADO)
        self.assertEqual(_cov(r).reason, "acesso restrito: redirecionado para autenticação")

    def test_pagina_lida_continua_com_avaliacao(self):
        """Página com conteúdo continua recebendo classificação normal."""
        html = "<html><body><h1>Dados abertos do orçamento</h1><p>Controle social.</p></body></html>"
        r = CivicEvaluator().evaluate_content(
            tool_id="ok",
            tool_name="Ok",
            target_url="https://exemplo.org",
            raw_html=html,
            response_time_ms=300,
            html_bytes=len(html),
            classifier=NullClassifier(),
        )
        self.assertNotEqual(r.status, AuditLevel.NAO_INSPECIONADO)
        self.assertTrue(_cov(r).inspected)


class TestRedirectEAcessoRestrito(unittest.TestCase):
    """Cobre o tratamento de redirecionamento e de tela de autenticação."""

    def test_303_para_tela_de_autenticacao_vira_acesso_restrito(self):
        """Redirecionamento 303 para login deve virar acesso restrito, não site fora do ar."""
        erro = _http_error(
            "https://app.streamlit.app", 303, "https://share.streamlit.io/-/auth/app?redirect_uri=x"
        )
        with patch("urllib.request.urlopen", side_effect=erro):
            resultado = HttpFetcher().fetch("https://app.streamlit.app")
        self.assertTrue(resultado.access_gate)
        self.assertIn("acesso restrito", resultado.error or "")
        self.assertEqual(resultado.content_bytes, 0)

    def test_403_vira_acesso_restrito(self):
        """Resposta 403 deve ser tratada como acesso restrito."""
        erro = _http_error("https://x.org", 403)
        with patch("urllib.request.urlopen", side_effect=erro):
            resultado = HttpFetcher().fetch("https://x.org")
        self.assertTrue(resultado.access_gate)

    def test_redirect_comum_e_seguido(self):
        """Redirecionamento comum deve ser seguido até o destino final."""

        class _Resp:
            status = 200
            headers: ClassVar[dict[str, str]] = {"Content-Length": "20"}
            url = "https://destino.org/final"

            def read(self, n=-1):
                """Devolve o corpo fixo usado como resposta do destino.

                Args:
                    n: Quantidade de bytes solicitada, ignorada.

                Returns:
                    Corpo HTML da resposta simulada.
                """
                return b"<html>conteudo</html>"

            def __enter__(self):
                """Entra no contexto da resposta simulada.

                Returns:
                    A própria resposta.
                """
                return self

            def __exit__(self, *a):
                """Encerra o contexto sem suprimir exceções.

                Args:
                    *a: Argumentos padrão de contexto.

                Returns:
                    Falso, para que exceções não sejam suprimidas.
                """
                return False

        redirecionamento = _http_error("https://origem.org", 301, "https://destino.org/final")
        with patch("urllib.request.urlopen", side_effect=[redirecionamento, _Resp()]):
            resultado = HttpFetcher().fetch("https://origem.org")
        self.assertFalse(resultado.access_gate)
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.final_url, "https://destino.org/final")


class TestFonteNaoFabricada(unittest.TestCase):
    """Garante que fonte declarada vem apenas do campo explícito do catálogo."""

    def _rodar(self, item):
        """Executa a avaliação de uma iniciativa com provedor de conteúdo simulado.

        Args:
            item: Registro do catálogo a avaliar.

        Returns:
            Laudo produzido para o registro.
        """
        provedor = MagicMock()
        provedor.fetch.return_value = FetchResult(
            url=item["url"],
            provider="http",
            html="<html><body><p>Dados.</p></body></html>",
            status_code=200,
            latency_ms=200,
            content_bytes=40,
            declared_bytes=40,
        )
        return audit_single_initiative(item, CivicEvaluator(), {}, skip_archive=True, fetcher=provedor)

    def test_descricao_com_tse_nao_declara_fonte(self):
        """Menção ao TSE na descrição não pode virar fonte declarada."""
        item = {
            "nome": "Painel de votação",
            "url": "https://exemplo.org",
            "descricao": "Painéis de votação por seção eleitoral e urnas do TSE",
        }
        r = self._rodar(item)
        self.assertEqual(_prov(r).declared_sources, [])
        self.assertEqual(r.dimension_scores[DimensionType.PROVENIENCIA_DADOS].raw_score, 50.0)

    def test_fonte_explicita_no_catalogo_conta(self):
        """Fonte presente em fontes_primarias deve contar na proveniência."""
        item = {"nome": "Painel", "url": "https://exemplo.org", "fontes_primarias": ["TSE"]}
        r = self._rodar(item)
        self.assertEqual(_prov(r).declared_sources, ["TSE"])
        self.assertEqual(r.dimension_scores[DimensionType.PROVENIENCIA_DADOS].raw_score, 75.0)


class TestLicencaOsi(unittest.TestCase):
    """Distingue licença de software aprovada pela OSI de licença de conteúdo."""

    def _avaliar(self, html):
        """Avalia um HTML mínimo e devolve o laudo correspondente.

        Args:
            html: Conteúdo HTML da página de teste.

        Returns:
            Laudo produzido pelo avaliador.
        """
        return CivicEvaluator().evaluate_content(
            tool_id="lic",
            tool_name="Lic",
            target_url="https://exemplo.org",
            raw_html=html,
            response_time_ms=300,
            html_bytes=len(html),
            classifier=NullClassifier(),
        )

    def test_creative_commons_nao_conta_como_osi(self):
        """Creative Commons deve ser registrada como conteúdo, sem pontuar como código aberto."""
        r = self._avaliar("<html><body><p>Conteúdo sob Creative Commons CC-BY-NC-ND.</p></body></html>")
        self.assertEqual(_floss(r).spdx_license_detected, "CC-BY-4.0")
        self.assertFalse(_floss(r).is_osi_approved)
        self.assertEqual(r.dimension_scores[DimensionType.CODIGO_ABERTO_FLOSS].raw_score, 40.0)
        self.assertTrue(
            any(
                "conteúdo, não de software" in f
                for f in r.dimension_scores[DimensionType.CODIGO_ABERTO_FLOSS].findings
            )
        )

    def test_mit_conta_como_osi(self):
        """Licença MIT deve pontuar como licença OSI de software."""
        r = self._avaliar("<html><body><p>Código sob MIT license.</p></body></html>")
        self.assertTrue(_floss(r).is_osi_approved)
        self.assertEqual(r.dimension_scores[DimensionType.CODIGO_ABERTO_FLOSS].raw_score, 60.0)

    def test_commits_recentes_nao_e_afirmado(self):
        """O motor não consulta o repositório, então não pode afirmar commits recentes."""
        r = self._avaliar("<html><body><p>Código sob MIT license.</p></body></html>")
        self.assertIsNone(_floss(r).has_recent_commits)


class TestNomeDuplicado(unittest.TestCase):
    """Garante identidade única para iniciativas homônimas no catálogo."""

    def test_nomes_iguais_recebem_ids_distintos(self):
        """Dois registros com o mesmo nome devem gerar tool_id e laudos distintos."""
        import json
        import tempfile
        from pathlib import Path

        from observatorio.engine import run_batch_audit

        catalogo = [
            {"nome": "Mapa do Orçamento", "url": "https://a.org"},
            {"nome": "Mapa do Orçamento", "url": "https://b.org"},
        ]
        provedor = MagicMock()
        provedor.fetch.side_effect = lambda url: FetchResult(
            url=url,
            provider="http",
            html=f"<html><body><p>{url}</p></body></html>",
            status_code=200,
            latency_ms=100,
            content_bytes=60,
            declared_bytes=60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            entrada = tmp / "catalogo.json"
            entrada.write_text(json.dumps(catalogo), encoding="utf-8")
            relatorios = run_batch_audit(
                input_file=entrada,
                output_file=tmp / "laudo.json",
                manifest_file=tmp / "estado.json",
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=provedor,
            )
        ids = [r.tool_id for r in relatorios]
        self.assertEqual(ids, ["mapa-do-orcamento", "mapa-do-orcamento-2"])
        self.assertEqual([r.target_url for r in relatorios], ["https://a.org", "https://b.org"])


if __name__ == "__main__":
    unittest.main()
