"""Testes da validade dos arquivos de workflow.

Um YAML sintaticamente válido pode ser recusado pelo GitHub: chave duplicada no mesmo bloco é erro
fatal lá, enquanto o PyYAML padrão aceita em silêncio e fica com a última ocorrência. Foi assim que
um formulário com `required` e `type` duplicados passou pelos testes e derrubou a execução no
servidor — o arquivo inteiro é descartado, sem job nenhum para inspecionar. Por isso aqui o YAML é
lido por um carregador estrito, e o formato de `workflow_dispatch` é conferido contra o que o GitHub
aceita.
"""

import unittest
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
FLUXOS = RAIZ / ".github" / "workflows"

TIPOS_ACEITOS = frozenset({"boolean", "choice", "number", "environment", "string"})


class CarregadorEstrito(yaml.SafeLoader):
    """Carregador que recusa chave duplicada, como o GitHub faz."""


def _mapeamento_sem_duplicata(loader: CarregadorEstrito, node: yaml.MappingNode, deep: bool = False) -> dict:
    """Constrói o mapa recusando chaves repetidas no mesmo bloco.

    Args:
        loader: Carregador em uso.
        node: Nó de mapeamento do YAML.
        deep: Quando verdadeiro, constrói os nós filhos imediatamente.

    Returns:
        Dicionário resultante do mapeamento.

    Raises:
        yaml.constructor.ConstructorError: Quando há chave repetida.
    """
    vistas: set[str] = set()
    for chave, _ in node.value:
        valor = loader.construct_object(chave, deep=deep)
        if valor in vistas:
            raise yaml.constructor.ConstructorError(
                None, None, f"chave duplicada: {valor!r}", chave.start_mark
            )
        vistas.add(valor)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


CarregadorEstrito.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapeamento_sem_duplicata)


def carregar(caminho: Path) -> dict:
    """Lê um workflow exigindo YAML sem chave duplicada.

    Args:
        caminho: Arquivo de workflow.

    Returns:
        Conteúdo do arquivo decodificado.
    """
    return yaml.load(caminho.read_text(encoding="utf-8"), Loader=CarregadorEstrito)


def entradas_do_formulario(fluxo: dict) -> dict:
    """Extrai as entradas de `workflow_dispatch` de um workflow.

    Args:
        fluxo: Workflow já decodificado.

    Returns:
        Mapa de nome da entrada para a definição dela, vazio quando não há formulário.
    """
    gatilhos = fluxo.get(True) or fluxo.get("on") or {}
    if not isinstance(gatilhos, dict):
        return {}
    despacho = gatilhos.get("workflow_dispatch")
    if not isinstance(despacho, dict):
        return {}
    return despacho.get("inputs") or {}


class TestWorkflowsPublicados(unittest.TestCase):
    """Confere o que o GitHub recusaria sem deixar rastro útil."""

    def setUp(self):
        """Lista os arquivos de workflow versionados."""
        self.arquivos = sorted(FLUXOS.glob("*.yml")) + sorted(FLUXOS.glob("*.yaml"))
        self.assertTrue(self.arquivos, "nenhum workflow encontrado")

    def test_yaml_sem_chave_duplicada(self):
        """Chave repetida invalida o arquivo inteiro e a execução nasce sem job."""
        for arquivo in self.arquivos:
            with self.subTest(arquivo=arquivo.name):
                carregar(arquivo)

    def test_entradas_do_formulario_tem_forma_aceita(self):
        """`workflow_dispatch` só aceita tipos declarados, e `choice` exige opções."""
        for arquivo in self.arquivos:
            for nome, campo in entradas_do_formulario(carregar(arquivo)).items():
                with self.subTest(arquivo=arquivo.name, entrada=nome):
                    self.assertIn(campo.get("type"), TIPOS_ACEITOS, f"{nome}: tipo inválido")
                    self.assertIsInstance(campo.get("required", False), bool)
                    self.assertTrue(campo.get("description"), f"{nome}: sem descrição")
                    if campo.get("type") == "choice":
                        self.assertGreaterEqual(len(campo.get("options") or []), 1)

    def test_um_unico_rotulo_por_entrada(self):
        """Nome repetido de entrada deixaria dois campos na tela para o mesmo argumento."""
        for arquivo in self.arquivos:
            entradas = entradas_do_formulario(carregar(arquivo))
            with self.subTest(arquivo=arquivo.name):
                self.assertEqual(len(entradas), len(set(entradas)))


if __name__ == "__main__":
    unittest.main()
