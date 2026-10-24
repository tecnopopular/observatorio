"""Testes do carregamento de credenciais em ambientes com e sem Keychain.

A precedência é variável de ambiente primeiro e Keychain depois. Em contêiner Linux e no CI não
existe o binário `security`: a ausência dele precisa significar "nenhum segredo aqui", não erro de
plataforma interrompendo a construção do provedor.
"""

import os
import subprocess
import unittest
from unittest.mock import patch

from observatorio.credentials import get_api_key, get_internet_archive_keys, get_keychain_secret


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


class TestChavesDoInternetArchive(unittest.TestCase):
    """O par de chaves S3 só é usado quando as duas partes existem."""

    def test_uma_parte_ausente_e_ausencia_de_credencial(self) -> None:
        """Meia credencial não autentica: o cliente segue anônimo."""
        ambiente = {"IA_S3_ACCESS_KEY": "acesso"}
        with (
            patch.dict(os.environ, ambiente, clear=True),
            patch("observatorio.credentials.get_keychain_secret", return_value=None),
        ):
            self.assertIsNone(get_internet_archive_keys())

    def test_ambiente_tem_precedencia_sobre_keychain(self) -> None:
        """As duas variáveis de ambiente bastam, sem consultar o Keychain."""
        ambiente = {"IA_S3_ACCESS_KEY": "acesso", "IA_S3_SECRET_KEY": "segredo"}
        with (
            patch.dict(os.environ, ambiente, clear=True),
            patch("observatorio.credentials.get_keychain_secret") as mock_keychain,
        ):
            self.assertEqual(get_internet_archive_keys(), ("acesso", "segredo"))
        mock_keychain.assert_not_called()

    def test_par_completo_vindo_do_keychain(self) -> None:
        """Sem variáveis de ambiente, o par vem do Keychain local."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "observatorio.credentials.get_keychain_secret",
                side_effect=lambda nome: {"IA_S3_ACCESS_KEY": "a", "IA_S3_SECRET_KEY": "s"}.get(nome),
            ),
        ):
            self.assertEqual(get_internet_archive_keys(), ("a", "s"))
