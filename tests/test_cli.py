"""Testes da superfície de argumentos da CLI.

Um `add_argument` malformado derruba a CLI inteira na primeira chamada, e nenhum teste de motor
pegaria isso: a esteira roda, o parser é que não abre. Aqui cada subcomando é construído e
analisado sem executar nada.
"""

import unittest

from observatorio.catalog import CATEGORIES
from observatorio.cli import build_parser


class TestParser(unittest.TestCase):
    """Verifica que todos os subcomandos abrem e aceitam seus argumentos."""

    def setUp(self):
        """Constrói o parser uma vez por teste."""
        self.parser = build_parser()

    def test_audit_all_abre_com_padroes(self):
        """Sem flags, a esteira assume os valores padrão documentados."""
        args = self.parser.parse_args(["audit-all"])
        self.assertEqual(args.max_bytes, 4_000_000)
        self.assertEqual(args.semantic, "off")
        self.assertEqual(args.timeout, 8)
        self.assertFalse(args.refresh)

    def test_audit_all_nao_expoe_provedor(self):
        """A aquisição tem um caminho só: não há flag de provedor a escolher."""
        args = self.parser.parse_args(["audit-all"])
        self.assertFalse(hasattr(args, "fetch_provider"))
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["audit-all", "--fetch-provider", "firecrawl"])

    def test_adicionar_exige_campos_editoriais(self):
        """URL, nome, categoria e descrição são obrigatórios na inclusão."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["adicionar", "--url", "https://exemplo.org"])
        args = self.parser.parse_args(
            [
                "adicionar",
                "--url",
                "https://exemplo.org",
                "--nome",
                "Iniciativa Exemplo",
                "--categoria",
                CATEGORIES[0],
                "--descricao",
                "Painel público com dados de votação por seção eleitoral.",
            ]
        )
        self.assertEqual(args.catalogo, "data/data_iniciativas.json")
        self.assertFalse(args.ignorar_robots)

    def test_retirar_e_readmitir_compartilham_registro(self):
        """Os dois comandos editoriais apontam para o mesmo registro por padrão."""
        retirada = self.parser.parse_args(["retirar", "--url", "https://exemplo.org", "--motivo", "pedido"])
        readmissao = self.parser.parse_args(
            ["readmitir", "--url", "https://exemplo.org", "--motivo", "volta"]
        )
        self.assertEqual(retirada.registro, "data/retiradas.json")
        self.assertEqual(readmissao.registro, retirada.registro)

    def test_verify_privacy_abre(self):
        """O portão de privacidade continua disponível como subcomando."""
        args = self.parser.parse_args(["verify-privacy", "--all-files"])
        self.assertTrue(args.all_files)


if __name__ == "__main__":
    unittest.main()
