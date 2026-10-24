"""Testes da correspondência entre o formulário de inclusão e a CLI.

O formulário do `workflow_dispatch` é a interface que a operação usa para incluir iniciativas, mas
ele é uma segunda declaração dos mesmos argumentos. Enquanto as duas declarações não forem
comparadas por teste, uma mudança na CLI deixa o formulário descrevendo um contrato que não existe
mais — foi o que aconteceu quando `categoria` deixou de ser lista fechada e continuou como
`choice` obrigatório de sete opções fixas.
"""

import argparse
import unittest
from pathlib import Path

import yaml

from observatorio.cli import build_parser

RAIZ = Path(__file__).resolve().parent.parent
FLUXO = RAIZ / ".github" / "workflows" / "adicionar.yml"

OPCOES_INTERNAS = frozenset({"catalogo", "registro", "help"})


def opcoes_da_cli() -> dict[str, argparse.Action]:
    """Lê as opções do subcomando `adicionar`, indexadas pelo nome do destino.

    Returns:
        Mapa de nome de destino para a ação correspondente do argparse.
    """
    parser = build_parser()
    for acao in parser._actions:
        if isinstance(acao, argparse._SubParsersAction):
            subcomando = acao.choices["adicionar"]
            return {
                opcao.dest: opcao
                for opcao in subcomando._actions
                if any(sinal.startswith("--") for sinal in opcao.option_strings)
            }
    raise AssertionError("subcomando adicionar ausente do parser")


class TestFormularioDeInclusao(unittest.TestCase):
    """Compara o formulário publicado com os argumentos que a CLI aceita."""

    def setUp(self):
        """Carrega o bloco de entradas do formulário."""
        conteudo = yaml.safe_load(FLUXO.read_text(encoding="utf-8"))
        self.entradas = conteudo[True]["workflow_dispatch"]["inputs"]
        self.opcoes = opcoes_da_cli()

    def test_toda_entrada_do_formulario_existe_na_cli(self):
        """Campo do formulário sem argumento correspondente na CLI é formulário morto."""
        for nome in self.entradas:
            self.assertIn(nome, self.opcoes, f"o formulário oferece '{nome}', a CLI não aceita")

    def test_toda_opcao_da_cli_tem_entrada_no_formulario(self):
        """Argumento da CLI fora do formulário só existe para quem executa por linha de comando."""
        for destino in self.opcoes:
            if destino in OPCOES_INTERNAS:
                continue
            self.assertIn(destino, self.entradas, f"a CLI aceita '--{destino}', o formulário não oferece")

    def test_obrigatoriedade_acompanha_a_cli(self):
        """Campo obrigatório no formulário precisa ser obrigatório na CLI, e vice-versa."""
        obrigatorios_cli = {destino for destino, acao in self.opcoes.items() if acao.required}
        obrigatorios_formulario = {nome for nome, campo in self.entradas.items() if campo.get("required")}
        self.assertEqual(obrigatorios_formulario, obrigatorios_cli)

    def test_categoria_e_texto_livre(self):
        """O eixo temático é texto editorial: o formulário não pode fechar a lista.

        `validate_entry` não valida eixo, e a CLI registra qualquer texto. Uma lista fechada no
        formulário recusaria um eixo novo que o catálogo aceitaria.
        """
        self.assertEqual(self.entradas["categoria"]["type"], "string")
        self.assertFalse(self.entradas["categoria"].get("required", False))

    def test_descricao_nao_e_obrigatoria(self):
        """Descrição é editorial e opcional na CLI; exigir no formulário bloqueia inclusão legítima."""
        self.assertFalse(self.entradas["descricao"].get("required", False))

    def test_toda_entrada_descreve_o_que_faz(self):
        """Descrição vazia deixa quem preenche o formulário sem saber o que o campo espera."""
        for nome, campo in self.entradas.items():
            self.assertTrue(campo.get("description"), f"entrada '{nome}' sem descrição")

    def test_fontes_primarias_declara_a_regra_de_corroboracao(self):
        """Declarar fonte sem link na página não pontua desde o CTP-1.1, e o formulário precisa dizer."""
        descricao = self.entradas["fontes_primarias"]["description"].lower()
        self.assertIn("link", descricao)
        self.assertIn("pontua", descricao)


if __name__ == "__main__":
    unittest.main()
