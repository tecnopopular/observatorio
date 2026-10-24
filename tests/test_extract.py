"""
tests/test_extract.py
Testes da extração com escopo: cada detector recebe apenas a visão em que sua evidência faz sentido.
"""

import unittest

from observatorio.extract import host_of, is_official_host, parse_page, repo_candidates

HTML = """
<html><head>
  <meta name="description" content="Monitor de políticas públicas">
  <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>
  <script>fbq('init', '123456789');</script>
  <style>.x{color:red}</style>
</head><body>
  <!-- TODO: remover googletagmanager.com/gtag/js?id=G-ABC123XYZ antes do lancamento -->
  <h1>Transparência do orçamento</h1>
  <p>Controle social e dados abertos.</p>
  <a href="https://dadosabertos.tse.jus.br">Fonte TSE</a>
  <a href="https://github.com/fulano">Colaborador</a>
  <a href="https://github.com/org/repo">Código</a>
  <a href="https://creativecommons.org/licenses/by/4.0/" rel="license">Licença</a>
  <img src="https://www.facebook.com/tr?id=999&ev=PageView">
  <form><input name="cpf"><input name="email"></form>
</body></html>
"""


class TestEscopos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Compila a página de fixture uma única vez para toda a classe."""
        cls.ev = parse_page(HTML)

    def test_texto_visivel_exclui_script_style_e_comentario(self):
        """Texto visível deve conter o conteúdo da página e excluir scripts, estilos e comentários."""
        self.assertIn("Transparência do orçamento", self.ev.visible_text)
        self.assertNotIn("googletagmanager", self.ev.visible_text)
        self.assertNotIn("fbq", self.ev.visible_text)
        self.assertNotIn("color:red", self.ev.visible_text)
        self.assertNotIn("TODO", self.ev.visible_text)

    def test_script_srcs_separados_dos_inline(self):
        """Scripts externos devem ir para script_srcs e os trechos inline para inline_scripts."""
        self.assertTrue(any("googletagmanager" in s for s in self.ev.script_srcs))
        self.assertIn("fbq", self.ev.inline_scripts)

    def test_comentario_e_coletado_a_parte(self):
        """Comentários HTML devem ser coletados em campo próprio e não contaminar a superfície de scripts."""
        self.assertTrue(any("TODO" in c for c in self.ev.comments))
        self.assertNotIn("TODO", self.ev.script_surface)

    def test_links_meta_inputs(self):
        """Links, metadados e nomes de campos de formulário devem ser extraídos na forma bruta."""
        self.assertIn("https://dadosabertos.tse.jus.br", self.ev.links)
        self.assertEqual(self.ev.meta.get("description"), "Monitor de políticas públicas")
        self.assertEqual(self.ev.input_names, ["cpf", "email"])

    def test_rel_license_identificado(self):
        """Link com rel=license deve ser identificado como evidência estrutural de licença."""
        self.assertTrue(any("creativecommons" in h for h in self.ev.rel_license))

    def test_resource_urls_incluem_pixel_em_img(self):
        """URLs de recurso devem incluir o pixel carregado por tag img e expô-lo na superfície de scripts."""
        self.assertTrue(any("facebook.com/tr" in u for u in self.ev.resource_urls))
        self.assertIn("facebook.com/tr", self.ev.script_surface)

    def test_pagina_vazia(self):
        """Página sem conteúdo útil deve ser marcada como vazia."""
        self.assertTrue(parse_page("").is_empty)
        self.assertTrue(parse_page("<html><body></body></html>").is_empty)


class TestHosts(unittest.TestCase):
    def test_host_normalizado(self):
        """O host deve ser extraído sem esquema, sem caminho e sem o prefixo www."""
        self.assertEqual(host_of("https://www.dadosabertos.tse.jus.br/x"), "dadosabertos.tse.jus.br")
        self.assertEqual(host_of("dadosabertos.tse.jus.br"), "dadosabertos.tse.jus.br")

    def test_sufixos_oficiais(self):
        """Apenas domínios sob sufixos oficiais são aceitos; sufixo embutido em domínio alheio é rejeitado."""
        for host in ["tse.jus.br", "ibge.gov.br", "camara.leg.br", "planalto.gov.br"]:
            self.assertTrue(is_official_host(host), host)
        for host in ["example.com", "github.com", "tse.jus.br.evil.com"]:
            self.assertFalse(is_official_host(host), host)

    def test_repo_candidates_ignora_perfil(self):
        """Apenas links com organização e projeto devem ser candidatos a repositório, ignorando perfis."""
        links = [
            "https://github.com/fulano",
            "https://github.com/org/repo",
            "https://gitlab.com/org/projeto",
        ]
        achados = repo_candidates(links)
        self.assertEqual(len(achados), 2)
        self.assertIn("https://github.com/org/repo", achados)
        self.assertNotIn("https://github.com/fulano", achados)


if __name__ == "__main__":
    unittest.main()
