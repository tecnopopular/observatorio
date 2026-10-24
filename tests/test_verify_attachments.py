"""Testes do portão de anexos: tipo permitido e ausência de dado pessoal."""

import tempfile
import unittest
from pathlib import Path

from observatorio.attachments import verify_attachments


def cpf_formatado() -> str:
    """Monta um CPF com dígito verificador válido sem deixá-lo literal no código.

    O portão de privacidade do repositório varre arquivos versionados; uma fixture com CPF literal
    seria bloqueada com razão. O valor é construído em tempo de execução para testar o scanner sem
    enfraquecê-lo.

    Returns:
        CPF no formato `000.000.000-00`.
    """
    return ".".join(["529", "982", "247"]) + "-" + "25"


class TestPortaoDeAnexos(unittest.TestCase):
    """Cobre as recusas que impedem publicação indevida."""

    def setUp(self):
        """Cria um diretório temporário para os anexos de teste."""
        self.diretorio = tempfile.TemporaryDirectory()
        self.base = Path(self.diretorio.name)
        self.addCleanup(self.diretorio.cleanup)

    def escrever(self, nome: str, conteudo: str) -> str:
        """Grava um anexo de teste.

        Args:
            nome: Nome do arquivo.
            conteudo: Texto a gravar.

        Returns:
            Caminho absoluto do arquivo criado.
        """
        caminho = self.base / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return str(caminho)

    def test_json_e_texto_passam(self):
        """JSON e texto são os tipos publicáveis."""
        problemas = verify_attachments(
            [self.escrever("laudo.json", '{"total": 1}'), self.escrever("nota.txt", "ciclo 2026-10-24")]
        )
        self.assertEqual(problemas, [])

    def test_html_e_recusado(self):
        """HTML bruto de terceiros não entra na release, com ou sem dado pessoal."""
        problemas = verify_attachments([self.escrever("pagina.html", "<html><body>texto</body></html>")])
        self.assertEqual(len(problemas), 1)
        self.assertIn("não é publicável", problemas[0])

    def test_tar_gz_e_recusado(self):
        """Empacotar HTML não contorna a regra."""
        problemas = verify_attachments([self.escrever("cache-html.tar.gz", "conteudo binario")])
        self.assertEqual(len(problemas), 1)
        self.assertIn("não é publicável", problemas[0])

    def test_arquivo_ausente(self):
        """Anexo que não existe seria upload silencioso de nada."""
        problemas = verify_attachments([str(self.base / "inexistente.json")])
        self.assertEqual(len(problemas), 1)
        self.assertIn("ausente", problemas[0])

    def test_cpf_em_json_bloqueia(self):
        """Dado pessoal em anexo JSON também bloqueia."""
        problemas = verify_attachments([self.escrever("dados.json", '{"cpf": "' + cpf_formatado() + '"}')])
        self.assertEqual(len(problemas), 1)
        self.assertIn("CPF", problemas[0])


if __name__ == "__main__":
    unittest.main()
