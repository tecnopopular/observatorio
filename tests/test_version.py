"""Testes da coerência entre a versão declarada no pacote e a do projeto."""

import tomllib
import unittest
from pathlib import Path

import observatorio

RAIZ = Path(__file__).resolve().parent.parent


class TestVersaoDeclarada(unittest.TestCase):
    """Garante que as duas declarações de versão não divirjam."""

    def test_pyproject_e_pacote_concordam(self):
        """A versão do `pyproject.toml` é a mesma exposta por `observatorio.__version__`."""
        projeto = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(observatorio.__version__, projeto["project"]["version"])

    def test_versao_tem_tres_numeros(self):
        """A versão segue o formato `MAJOR.MINOR.PATCH`."""
        partes = observatorio.__version__.split(".")
        self.assertEqual(len(partes), 3)
        self.assertTrue(all(parte.isdigit() for parte in partes))


if __name__ == "__main__":
    unittest.main()
