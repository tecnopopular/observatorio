"""
tests/test_wayback_preservation.py
Testes unitários com mocks para o cliente de preservação do Internet Archive.
"""

import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from observatorio.fetch import INSTITUTIONAL_USER_AGENT
from observatorio.preservation import archive_target


class TestWaybackPreservation(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_archive_success_relative_location(self, mock_urlopen):
        """Sucesso com cabeçalho Content-Location relativo deve retornar URL completa.

        Args:
            mock_urlopen: Substituto de `urllib.request.urlopen` injetado pelo `patch`.
        """
        mock_resp = MagicMock()
        mock_resp.headers.get.side_effect = lambda h: (
            "/web/20261020120000/https://exemplo.org" if h == "Content-Location" else None
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = archive_target("https://exemplo.org")
        self.assertEqual(result, "https://web.archive.org/web/20261020120000/https://exemplo.org")

    @patch("urllib.request.urlopen")
    def test_archive_success_absolute_location(self, mock_urlopen):
        """Sucesso com URL absoluta no cabeçalho deve retornar o link inalterado.

        Args:
            mock_urlopen: Substituto de `urllib.request.urlopen` injetado pelo `patch`.
        """
        mock_resp = MagicMock()
        mock_resp.headers.get.side_effect = lambda h: (
            "https://web.archive.org/web/20261020120000/https://exemplo.org"
            if h == "Content-Location"
            else None
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = archive_target("https://exemplo.org")
        self.assertEqual(result, "https://web.archive.org/web/20261020120000/https://exemplo.org")

    @patch("urllib.request.urlopen")
    def test_archive_resilient_on_network_timeout(self, mock_urlopen):
        """Falha de rede não deve quebrar a execução e precisa devolver None.

        Args:
            mock_urlopen: Substituto de `urllib.request.urlopen` injetado pelo `patch`.
        """
        mock_urlopen.side_effect = urllib.error.URLError("Connection timeout")

        result = archive_target("https://exemplo.org", timeout=1)
        self.assertIsNone(result)

    @patch("urllib.request.urlopen")
    def test_preservacao_usa_user_agent_institucional(self, mock_urlopen):
        """A chamada ao Internet Archive deve se identificar como robô, não como navegador.

        Args:
            mock_urlopen: Substituto de `urllib.request.urlopen` injetado pelo `patch`.
        """
        mock_resp = MagicMock()
        mock_resp.headers.get.side_effect = lambda h: (
            "/web/1/https://exemplo.org" if h == "Content-Location" else None
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        archive_target("https://exemplo.org")

        requisicao = mock_urlopen.call_args.args[0]
        agente = requisicao.get_header("User-agent")
        self.assertEqual(agente, INSTITUTIONAL_USER_AGENT)
        self.assertNotIn("Mozilla", agente)


if __name__ == "__main__":
    unittest.main()
