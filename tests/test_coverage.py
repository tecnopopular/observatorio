"""
tests/test_coverage.py
Testes do metadado de cobertura: a tela entre o que foi lido e o que o laudo afirma.

Regra: um resultado obtido sobre leitura parcial precisa se declarar parcial — e a ressalva
aparece só nas dimensões cuja evidência depende do escopo que foi cortado.
"""

import unittest

from observatorio.evaluator import CivicEvaluator
from observatorio.models import DimensionType
from observatorio.semantic import NullClassifier

HTML_CURTO = "<html><body><h1>Dados abertos do orçamento</h1><p>Controle social.</p></body></html>"


def avaliar(html: str, **kwargs):
    """Avalia um HTML de teste com o avaliador padrão e classificador nulo.

    Args:
        html: Documento submetido à avaliação.
        **kwargs: Ajustes repassados ao avaliador, como `bytes_scanned` e
            `inspection_truncated`.

    Returns:
        Laudo produzido pelo avaliador para o documento informado.
    """
    return CivicEvaluator().evaluate_content(
        tool_id="cobertura",
        tool_name="Cobertura",
        target_url="https://exemplo.org",
        raw_html=html,
        response_time_ms=300,
        html_bytes=kwargs.pop("html_bytes", len(html.encode("utf-8"))),
        classifier=NullClassifier(),
        **kwargs,
    )


def findings(relatorio, dimension):
    """Lista os achados registrados em uma dimensão do laudo.

    Args:
        relatorio: Laudo devolvido pelo avaliador.
        dimension: Dimensão cujos achados serão lidos.

    Returns:
        Lista de achados textuais da dimensão.
    """
    return relatorio.dimension_scores[dimension].findings


def _cov(relatorio):
    """Extrai o metadado de cobertura do laudo, falhando alto quando ausente.

    Args:
        relatorio: Laudo devolvido pelo avaliador.

    Returns:
        Metadado de cobertura associado ao laudo.
    """
    cobertura = relatorio.coverage
    if cobertura is None:
        raise AssertionError("laudo sem metadado de cobertura")
    return cobertura


class TestCoberturaCompleta(unittest.TestCase):
    """Leitura integral declarada como completa, sem ressalvas."""

    def test_pagina_inteira_marca_completa(self):
        """Página lida por inteiro declara cobertura completa e 100 por cento dos bytes."""
        r = avaliar(HTML_CURTO)
        self.assertTrue(_cov(r).complete)
        self.assertFalse(_cov(r).text_truncated)
        self.assertFalse(_cov(r).inspection_truncated)
        self.assertEqual(_cov(r).byte_coverage, 1.0)

    def test_sem_ressalva_em_nenhuma_dimensao(self):
        """Nenhuma dimensão recebe ressalva de cobertura quando nada foi cortado."""
        r = avaliar(HTML_CURTO)
        for dimension in DimensionType:
            marcas = [f for f in findings(r, dimension) if "cobertura incompleta" in f]
            self.assertEqual(marcas, [], dimension.value)


class TestCorteDeBytes(unittest.TestCase):
    """Ressalvas decorrentes do corte por limite de bytes."""

    def test_cobertura_parcial_registrada(self):
        """Corte de bytes registra proporção lida e tamanho declarado."""
        r = avaliar(HTML_CURTO, html_bytes=1000, bytes_scanned=250, inspection_truncated=True)
        self.assertFalse(_cov(r).complete)
        self.assertEqual(_cov(r).byte_coverage, 0.25)
        self.assertEqual(_cov(r).bytes_declared, 1000)
        self.assertEqual(_cov(r).bytes_scanned, 250)

    def test_ressalva_atinge_dimensoes_que_leem_bytes(self):
        """Dimensões que dependem de bytes recebem a ressalva com os números lidos."""
        r = avaliar(HTML_CURTO, html_bytes=1000, bytes_scanned=250, inspection_truncated=True)
        for dimension in (
            DimensionType.PROVENIENCIA_DADOS,
            DimensionType.PRIVACIDADE_SOBERANIA,
            DimensionType.CODIGO_ABERTO_FLOSS,
            DimensionType.ACESSIBILIDADE_DESEMPENHO,
        ):
            marcas = [f for f in findings(r, dimension) if "cobertura incompleta" in f]
            self.assertTrue(marcas, f"{dimension.value} deveria registrar ressalva")
            self.assertIn("250", marcas[0])
            self.assertIn("1,000", marcas[0])

    def test_ressalva_nao_atinge_dimensao_de_texto_quando_so_bytes_foram_cortados(self):
        """Dimensão que lê texto visível não herda ressalva de corte apenas em bytes."""
        r = avaliar(HTML_CURTO, html_bytes=1000, bytes_scanned=250, inspection_truncated=True)
        marcas = [
            f for f in findings(r, DimensionType.ENQUADRAMENTO_REPUBLICANO) if "cobertura incompleta" in f
        ]
        self.assertEqual(marcas, [])

    def test_truncamento_sem_tamanho_declarado_ainda_gera_ressalva(self):
        """Sem tamanho declarado, a ressalva existe e informa cobertura desconhecida."""
        r = avaliar(HTML_CURTO, html_bytes=0, bytes_scanned=1_000_000, inspection_truncated=True)
        self.assertIsNone(_cov(r).byte_coverage)
        marcas = [f for f in findings(r, DimensionType.PRIVACIDADE_SOBERANIA) if "cobertura incompleta" in f]
        self.assertTrue(marcas)
        self.assertIn("desconhecido", marcas[0])


class TestCorteDeTexto(unittest.TestCase):
    """Ressalvas decorrentes do corte do texto visível."""

    def test_texto_truncado_registrado(self):
        """Corte de texto visível marca o laudo como incompleto e mede o excedente."""
        html = "<html><body><p>" + ("palavra " * 50_000) + "</p></body></html>"
        r = avaliar(html)
        self.assertTrue(_cov(r).text_truncated)
        self.assertFalse(_cov(r).complete)
        self.assertGreater(_cov(r).text_chars_total, _cov(r).text_chars_scanned)

    def test_ressalva_atinge_enquadramento_e_licenca(self):
        """Enquadramento e licença registram a ressalva de texto visível cortado."""
        html = "<html><body><p>" + ("palavra " * 50_000) + "</p></body></html>"
        r = avaliar(html)
        for dimension in (DimensionType.ENQUADRAMENTO_REPUBLICANO, DimensionType.CODIGO_ABERTO_FLOSS):
            marcas = [f for f in findings(r, dimension) if "cobertura incompleta" in f]
            self.assertTrue(marcas, dimension.value)
            self.assertIn("texto visível", marcas[0])

    def test_corte_de_texto_nao_marca_dimensao_de_bytes_puros(self):
        """Dimensão que lê apenas bytes não herda ressalva de texto cortado."""
        html = "<html><body><p>" + ("palavra " * 50_000) + "</p></body></html>"
        r = avaliar(html)
        marcas = [f for f in findings(r, DimensionType.PROVENIENCIA_DADOS) if "cobertura incompleta" in f]
        self.assertEqual(marcas, [])


class TestCorteDeScripts(unittest.TestCase):
    """Ressalvas decorrentes do corte de scripts embutidos."""

    def test_scripts_inline_truncados_afetam_privacidade(self):
        """Corte de scripts embutidos gera ressalva na dimensão de privacidade."""
        html = "<html><head><script>" + ("var a=1;" * 30_000) + "</script></head><body>x</body></html>"
        r = avaliar(html)
        self.assertTrue(_cov(r).inline_scripts_truncated)
        marcas = [f for f in findings(r, DimensionType.PRIVACIDADE_SOBERANIA) if "cobertura incompleta" in f]
        self.assertTrue(marcas)
        self.assertIn("scripts inline", marcas[0])


if __name__ == "__main__":
    unittest.main()
