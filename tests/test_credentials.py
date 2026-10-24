"""Testes do carregamento de credenciais em ambientes com e sem Keychain.

A precedência é variável de ambiente primeiro e Keychain depois. Em contêiner Linux e no CI não
existe o binário `security`: a ausência dele precisa significar "nenhum segredo aqui", não erro de
plataforma interrompendo a construção do provedor.
"""

import os
import subprocess
import unittest
from unittest.mock import patch

from observatorio.credentials import get_api_key, get_keychain_secret


class TestKeychain(unittest.TestCase):
    """Comportamento da consulta ao Keychain quando ele não existe ou falha."""

    def test_binario_ausente_devolve_nulo(self):
        """Máquina sem `security` responde ausência de segredo, sem estourar exceção."""
        with patch("subprocess.run", side_effect=FileNotFoundError("security")):
            self.assertIsNone(get_keychain_secret("QUALQUER_CHAVE"))

    def test_servico_inexistente_devolve_nulo(self):
        """Serviço não encontrado também é ausência, não erro."""
        erro = subprocess.CalledProcessError(44, "security")
        with patch("subprocess.run", side_effect=erro):
            self.assertIsNone(get_keychain_secret("QUALQUER_CHAVE"))

    def test_sem_permissao_devolve_nulo(self):
        """Keychain bloqueado não interrompe a execução da esteira."""
        with patch("subprocess.run", side_effect=PermissionError("bloqueado")):
            self.assertIsNone(get_keychain_secret("QUALQUER_CHAVE"))

    def test_servico_encontrado_devolve_valor(self):
        """Encontrando o item, o valor é devolvido sem espaços nas pontas."""
        saida = subprocess.CompletedProcess(args=["security"], returncode=0, stdout="  segredo  \n")
        with patch("subprocess.run", return_value=saida):
            self.assertEqual(get_keychain_secret("QUALQUER_CHAVE"), "segredo")


class TestPrecedencia(unittest.TestCase):
    """Ordem de leitura entre ambiente e Keychain."""

    def test_variavel_de_ambiente_tem_precedencia(self):
        """Em CI, a variável de ambiente decide e o Keychain nem é consultado."""
        os.environ["CHAVE_DE_TESTE_OBSERVATORIO"] = "do-ambiente"
        try:
            with patch("subprocess.run") as consulta:
                self.assertEqual(get_api_key("CHAVE_DE_TESTE_OBSERVATORIO"), "do-ambiente")
                consulta.assert_not_called()
        finally:
            os.environ.pop("CHAVE_DE_TESTE_OBSERVATORIO", None)

    def test_ambiente_ausente_cai_no_keychain(self):
        """Sem variável de ambiente, a busca vai para o Keychain."""
        os.environ.pop("CHAVE_DE_TESTE_OBSERVATORIO", None)
        with patch("observatorio.credentials.get_keychain_secret", return_value="do-keychain"):
            self.assertEqual(get_api_key("CHAVE_DE_TESTE_OBSERVATORIO"), "do-keychain")


if __name__ == "__main__":
    unittest.main()
