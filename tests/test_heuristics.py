"""
tests/test_heuristics.py
Fixtures de regressão das classes de falso positivo que o motor cometia ao aplicar regex
sobre a string bruta do HTML.

Cada teste aqui corresponde a um erro real observado: comentário virando evidência, menção
virando instalação, perfil de usuário virando repositório. O comportamento correto é
documentado como asserção para que a regressão quebre a suíte, não um laudo público.
"""

import unittest

from observatorio.evaluator import CivicEvaluator
from observatorio.models import DimensionType


def avaliar(html: str, **kwargs):
    """Avalia um HTML de fixture com o avaliador cívico e devolve o laudo.

    Args:
        html: Documento HTML bruto submetido à avaliação.
        **kwargs: Parâmetros adicionais repassados a `evaluate_content`.

    Returns:
        Laudo produzido pelo avaliador para a fixture.
    """
    ev = CivicEvaluator()
    return ev.evaluate_content(
        tool_id="fixture",
        tool_name="Fixture",
        target_url="https://exemplo.org",
        raw_html=html,
        response_time_ms=300,
        html_bytes=len(html.encode("utf-8")),
        **kwargs,
    )


def _priv(r):
    """Extrai o detalhe de privacidade do laudo, falhando quando ele não existe.

    Args:
        r: Laudo devolvido pelo avaliador.

    Returns:
        Detalhe da dimensão de privacidade e soberania.
    """
    d = r.privacy
    if d is None:
        raise AssertionError("laudo sem detalhe de privacidade")
    return d


def _floss(r):
    """Extrai o detalhe de FLOSS do laudo, falhando quando ele não existe.

    Args:
        r: Laudo devolvido pelo avaliador.

    Returns:
        Detalhe da dimensão de código aberto.
    """
    d = r.floss
    if d is None:
        raise AssertionError("laudo sem detalhe de FLOSS")
    return d


def _framing(r):
    """Extrai o detalhe de enquadramento do laudo, falhando quando ele não existe.

    Args:
        r: Laudo devolvido pelo avaliador.

    Returns:
        Detalhe da dimensão de enquadramento republicano.
    """
    d = r.framing
    if d is None:
        raise AssertionError("laudo sem detalhe de enquadramento")
    return d


def _perf(r):
    """Extrai o detalhe de desempenho do laudo, falhando quando ele não existe.

    Args:
        r: Laudo devolvido pelo avaliador.

    Returns:
        Detalhe da dimensão de acessibilidade e desempenho.
    """
    d = r.performance
    if d is None:
        raise AssertionError("laudo sem detalhe de desempenho")
    return d


def _prov(r):
    """Extrai o detalhe de proveniência do laudo, falhando quando ele não existe.

    Args:
        r: Laudo devolvido pelo avaliador.

    Returns:
        Detalhe da dimensão de proveniência de dados.
    """
    d = r.provenance
    if d is None:
        raise AssertionError("laudo sem detalhe de proveniência")
    return d


class TestTrackerEmComentario(unittest.TestCase):
    def test_comentario_nao_e_instalacao(self):
        """Um TODO dizendo 'remover o Google Analytics' não pode rejeitar a ferramenta."""
        html = """<html><body><h1>Ferramenta cívica</h1>
        <!-- TODO: remover googletagmanager.com/gtag/js?id=G-ABC123XYZ antes do lancamento -->
        <p>Transparência dos gastos públicos.</p></body></html>"""
        r = avaliar(html)
        self.assertEqual(_priv(r).detected_commercial_trackers, [])
        self.assertFalse(r.has_critical_blocker)
        self.assertEqual(r.dimension_scores[DimensionType.PRIVACIDADE_SOBERANIA].raw_score, 100.0)

    def test_script_real_continua_sendo_detectado(self):
        """Contraprova: tracker realmente carregado segue sendo bloqueador crítico."""
        html = """<html><head>
        <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>
        </head><body><h1>Ferramenta cívica</h1></body></html>"""
        r = avaliar(html)
        self.assertIn("Google Analytics (GA4/GTM)", _priv(r).detected_commercial_trackers)
        self.assertTrue(r.has_critical_blocker)
        self.assertEqual(r.dimension_scores[DimensionType.PRIVACIDADE_SOBERANIA].raw_score, 40.0)

    def test_pixel_em_img_continua_sendo_detectado(self):
        """Pixel de rastreamento carregado por tag img segue sendo detectado como tracker comercial."""
        html = """<html><body><h1>Ferramenta</h1>
        <img src="https://www.facebook.com/tr?id=123&amp;ev=PageView"><p>Dados abertos.</p></body></html>"""
        r = avaliar(html)
        self.assertTrue(_priv(r).detected_commercial_trackers)


class TestMencaoNaoEInstalacao(unittest.TestCase):
    def test_materia_sobre_redes_de_anuncio_nao_e_banner(self):
        """Menção textual a redes de anúncio não pode marcar a página como portadora de banner."""
        html = """<html><body><h1>Como funciona a publicidade programática</h1>
        <p>Redes como taboola e outbrain distribuem conteúdo patrocinado.</p></body></html>"""
        r = avaliar(html)
        self.assertFalse(_perf(r).has_ad_banners)

    def test_script_de_anuncio_real_continua_detectado(self):
        """Script de anúncio efetivamente carregado segue sendo detectado como banner."""
        html = """<html><head>
        <script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"></script>
        </head><body><h1>Portal</h1></body></html>"""
        r = avaliar(html)
        self.assertTrue(_perf(r).has_ad_banners)


class TestEnquadramentoEmTextoVisivel(unittest.TestCase):
    def test_termo_em_comentario_nao_penaliza(self):
        """Termo polarizador presente apenas em comentário HTML não pode reduzir a nota de enquadramento."""
        html = """<html><body><h1>Monitor de políticas públicas</h1>
        <!-- debate interno: o adversario chamou o outro de golpista -->
        <p>Controle social e serviços essenciais.</p></body></html>"""
        r = avaliar(html)
        self.assertEqual(_framing(r).polarizing_risk_keywords, [])
        self.assertEqual(r.dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score, 100.0)

    def test_termo_visivel_penaliza(self):
        """Termo polarizador em texto visível deve ser registrado e reduzir a nota de enquadramento."""
        html = "<html><body><p>Esse governo golpista destruiu tudo.</p></body></html>"
        r = avaliar(html)
        self.assertEqual(_framing(r).polarizing_risk_keywords, [r"golpista"])
        self.assertEqual(r.dimension_scores[DimensionType.ENQUADRAMENTO_REPUBLICANO].raw_score, 50.0)


class TestRepositorio(unittest.TestCase):
    def test_perfil_de_usuario_nao_e_repositorio(self):
        """Link para perfil de usuário do GitHub não pode ser lido como repositório de código."""
        html = """<html><body><p>Feito por
        <a href="https://github.com/fulano">fulano</a>. Dados abertos do orçamento.</p></body></html>"""
        r = avaliar(html)
        self.assertIsNone(_floss(r).public_repository_url)
        self.assertEqual(r.dimension_scores[DimensionType.CODIGO_ABERTO_FLOSS].raw_score, 40.0)

    def test_repositorio_real_e_detectado(self):
        """Link para repositório com organização e projeto deve ser registrado como código aberto."""
        html = """<html><body><p>Código em
        <a href="https://github.com/org/projeto">github.com/org/projeto</a>.</p></body></html>"""
        r = avaliar(html)
        self.assertEqual(_floss(r).public_repository_url, "https://github.com/org/projeto")
        self.assertEqual(r.dimension_scores[DimensionType.CODIGO_ABERTO_FLOSS].raw_score, 80.0)


class TestProvenienciaPorLink(unittest.TestCase):
    def test_link_oficial_conta(self):
        """Link para domínio oficial deve contar como evidência de proveniência de dados."""
        html = """<html><body><p>Fonte:
        <a href="https://dadosabertos.tse.jus.br">TSE</a></p></body></html>"""
        r = avaliar(html)
        self.assertTrue(_prov(r).official_links_detected)
        self.assertEqual(r.dimension_scores[DimensionType.PROVENIENCIA_DADOS].raw_score, 75.0)

    def test_dominio_parecido_nao_conta(self):
        """Domínio que apenas embute o sufixo oficial no meio não pode contar como fonte oficial."""
        html = """<html><body><p>Fonte:
        <a href="https://tse.jus.br.dados.example.com">TSE</a></p></body></html>"""
        r = avaliar(html)
        self.assertEqual(_prov(r).official_links_detected, [])


class TestPII(unittest.TestCase):
    def test_formulario_com_cpf_e_bloqueador(self):
        """Campo de formulário nomeado cpf deve acionar bloqueador crítico de privacidade."""
        html = """<html><body><form action="/x">
        <input name="cpf"><button>Enviar</button></form></body></html>"""
        r = avaliar(html)
        self.assertTrue(_priv(r).collects_sensitive_pII)
        self.assertTrue(r.has_critical_blocker)

    def test_mencao_a_cpf_em_texto_nao_e_formulario(self):
        """Menção textual a CPF, sem campo de formulário, não pode ser lida como coleta de dado pessoal."""
        html = "<html><body><p>Nunca pedimos CPF ou telefone.</p></body></html>"
        r = avaliar(html)
        self.assertFalse(_priv(r).collects_sensitive_pII)


class TestTruncamento(unittest.TestCase):
    def test_inspecao_parcial_e_registrada(self):
        """Inspeção parcial deve ser registrada no detalhe de desempenho e gerar achado explícito."""
        html = "<html><body><p>Dados abertos do orçamento.</p></body></html>"
        r = avaliar(html, bytes_scanned=len(html), inspection_truncated=True)
        self.assertTrue(_perf(r).inspection_truncated)
        self.assertEqual(_perf(r).bytes_scanned, len(html))
        self.assertTrue(
            any(
                "Inspeção parcial" in f
                for f in r.dimension_scores[DimensionType.ACESSIBILIDADE_DESEMPENHO].findings
            )
        )

    def test_inspecao_completa_nao_registra(self):
        """Inspeção completa não pode marcar o laudo como truncado."""
        html = "<html><body><p>Dados abertos do orçamento.</p></body></html>"
        r = avaliar(html)
        self.assertFalse(_perf(r).inspection_truncated)

    def test_privacidade_limpa_em_pagina_truncada_leva_ressalva(self):
        """Avaliação 'sem trackers' com parte do documento não lida precisa de ressalva explícita."""
        html = "<html><body><p>Dados abertos do orçamento.</p></body></html>"
        r = avaliar(html, bytes_scanned=2_500_000, inspection_truncated=True)
        findings = r.dimension_scores[DimensionType.PRIVACIDADE_SOBERANIA].findings
        self.assertTrue(any("Ressalva" in f for f in findings), findings)


class TestDividaConhecida(unittest.TestCase):
    @unittest.expectedFailure
    def test_licenca_mencionada_em_texto_nao_deveria_contar(self):
        """
        P4 pendente: 'MIT license' citada num artigo ainda é lida como licença do site.
        Quando a detecção passar a exigir evidência estrutural (rel="license" ou LICENSE do
        repositório), este teste passa a falhar como 'unexpected success' — sinal para removê-lo.
        """
        html = """<html><body><h1>Guia de licenças</h1>
        <p>Este artigo explica quando usar a MIT license e quando usar a Apache-2.0.</p></body></html>"""
        r = avaliar(html)
        self.assertIsNone(_floss(r).spdx_license_detected)


if __name__ == "__main__":
    unittest.main()
