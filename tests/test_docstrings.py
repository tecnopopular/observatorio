"""Garante o padrão Google de docstrings e a ausência de comentários explicativos.

O verificador vive em `tools/verify_docstrings.py` e é carregado aqui por caminho, para que
a regra seja a mesma usada na linha de comando. Sem este teste, a convenção volta a degradar
na primeira função nova que ninguém documentar.
"""

import importlib.util
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
VERIFICADOR = RAIZ / "tools" / "verify_docstrings.py"
ALVOS = (RAIZ / "src", RAIZ / "tools", RAIZ / "tests")


def _carregar_verificador():
    """Carrega o módulo verificador a partir do seu caminho no repositório.

    Returns:
        Módulo `verificar_docstrings` pronto para uso.
    """
    especificacao = importlib.util.spec_from_file_location("verify_docstrings", VERIFICADOR)
    if especificacao is None or especificacao.loader is None:
        raise AssertionError(f"não foi possível carregar {VERIFICADOR}")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


class TestPadraoDeDocumentacao(unittest.TestCase):
    """Impede que funções sem docstring Google ou comentários voltem ao código."""

    def test_sem_violacoes(self):
        """Nenhum arquivo de src, tools ou tests pode violar o padrão."""
        verificador = _carregar_verificador()
        violacoes: list[str] = []
        for alvo in ALVOS:
            for cache_file in verificador.collect_files([str(alvo)]):
                violacoes.extend(verificador.verify_file(cache_file))
        self.assertEqual(violacoes, [], "\n" + "\n".join(violacoes[:20]))


if __name__ == "__main__":
    unittest.main()
