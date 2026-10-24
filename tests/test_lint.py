"""Executa os verificadores estáticos dentro da suíte.

Regra que só existe em documento apodrece; esta suíte roda o lint e a detecção de código morto para
que uma entrega não passe com importação órfã, função sem consumidor ou formatação divergente.
"""

import subprocess
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _rodar(comando):
    """Executa um verificador na raiz do repositório.

    Args:
        comando: Lista com o executável e seus argumentos.

    Returns:
        Objeto com código de saída, stdout e stderr do processo.
    """
    return subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, check=False)


class TestLint(unittest.TestCase):
    """Verifica lint, formatação e ausência de código morto."""

    def test_ruff_nao_relata_problemas(self):
        """O lint não deve encontrar violações nas regras ativas."""
        resultado = _rodar([sys.executable, "-m", "ruff", "check", "."])
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_formatacao_estavel(self):
        """O código deve estar no formato canônico do ruff."""
        resultado = _rodar([sys.executable, "-m", "ruff", "format", "--check", "."])
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_sem_codigo_morto(self):
        """Não deve existir função, classe, importação ou constante sem consumidor."""
        resultado = _rodar([sys.executable, "tools/verificar_codigo_morto.py"])
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)


if __name__ == "__main__":
    unittest.main()
