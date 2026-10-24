"""Testes do cliente de preservação do Internet Archive.

O dublê fica no transporte (`urllib.request.urlopen`), nunca nas funções do módulo, e reproduz
o contrato medido no serviço em 2026-10-24: a submissão devolve uma página com o identificador
`spn2-...`, e a consulta de estado devolve JSON com `status`, `timestamp` e `original_url`.
O contrato anterior — endereço do snapshot em cabeçalho `Content-Location` — nunca existiu, e o
teste que o afirmava passava enquanto nenhuma captura era registrada.
"""

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from observatorio.fetch import INSTITUTIONAL_USER_AGENT
from observatorio.preservation import (
    ArchiveJob,
    await_archives,
    poll_archive,
    snapshot_url,
    submit_archive,
)


def resposta(corpo: str) -> io.BytesIO:
    """Monta um corpo de resposta legível pelo `urlopen`.

    Args:
        corpo: Texto devolvido pelo serviço.

    Returns:
        Fluxo de bytes com o corpo, aceito como resposta HTTP pelo dublê.
    """
    return io.BytesIO(corpo.encode("utf-8"))


PAGINA_DE_SUBMISSAO = (
    "<html><body><p>Saving page.</p>"
    '<a href="/save/status/spn2-b2a92f73e2fd57778579139f45b0b9bdad24b1be">status</a>'
    "</body></html>"
)

ESTADO_CONCLUIDO = json.dumps(
    {
        "status": "success",
        "timestamp": "20261024213206",
        "original_url": "https://exemplo.org/",
        "duration_sec": 14.21,
        "job_id": "spn2-b2a92f73e2fd57778579139f45b0b9bdad24b1be",
    }
)

ESTADO_PENDENTE = json.dumps({"status": "pending", "job_id": "spn2-b2a92f73e2fd57778579139f45b0b9bdad24b1be"})


class TestSubmissao(unittest.TestCase):
    """Submissão da captura e leitura do identificador de trabalho."""

    @patch("urllib.request.urlopen")
    def test_submissao_aceita_devolve_trabalho(self, mock_urlopen: MagicMock) -> None:
        """A submissão aceita devolve `pending` com o identificador extraído do corpo.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.return_value.__enter__.return_value = resposta(PAGINA_DE_SUBMISSAO)

        resultado = submit_archive("https://exemplo.org/")

        self.assertEqual(resultado.status, "pending")
        self.assertEqual(resultado.job_id, "spn2-b2a92f73e2fd57778579139f45b0b9bdad24b1be")

    @patch("urllib.request.urlopen")
    def test_submissao_usa_user_agent_institucional(self, mock_urlopen: MagicMock) -> None:
        """A chamada se identifica como robô institucional, não como navegador.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.return_value.__enter__.return_value = resposta(PAGINA_DE_SUBMISSAO)

        submit_archive("https://exemplo.org/")

        requisicao = mock_urlopen.call_args.args[0]
        self.assertEqual(requisicao.get_header("User-agent"), INSTITUTIONAL_USER_AGENT)
        self.assertNotIn("Mozilla", requisicao.get_header("User-agent"))
        self.assertEqual(requisicao.get_method(), "POST")

    @patch("urllib.request.urlopen")
    def test_submissao_recusada_por_limite(self, mock_urlopen: MagicMock) -> None:
        """Limite de requisições vira `refused`, e não falha silenciosa.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://web.archive.org/save", 429, "Too Many Requests", None, None
        )

        resultado = submit_archive("https://exemplo.org/")

        self.assertEqual(resultado.status, "refused")
        self.assertIn("429", resultado.detail or "")

    def test_submissao_sem_trabalho_registra_recusa(self) -> None:
        """Endereço para o qual o serviço não abre tarefa vira `refused`, não `pending`.

        É o caso dos domínios reservados de teste (`example.com`, `example.org`), em que o
        serviço devolve o formulário com erro de validação em vez de abrir captura.
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = resposta("<html>sem tarefa</html>")

            resultado = submit_archive("https://exemplo.org/")

        self.assertEqual(resultado.status, "refused")
        self.assertIsNone(resultado.job_id)

    @patch("urllib.request.urlopen")
    def test_snapshot_ja_existente_e_reaproveitado(self, mock_urlopen: MagicMock) -> None:
        """Quando o serviço devolve a captura existente, ela é adotada em vez de recusada.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        corpo = (
            "<html><body><p>This page has been captured recently.</p>"
            '<a href="https://web.archive.org/web/20261024213206/https://exemplo.org/">ver</a>'
            "</body></html>"
        )
        mock_urlopen.return_value.__enter__.return_value = resposta(corpo)

        resultado = submit_archive("https://exemplo.org/")

        self.assertEqual(resultado.status, "captured")
        self.assertEqual(resultado.url, "https://web.archive.org/web/20261024213206/https://exemplo.org/")

    @patch("urllib.request.urlopen")
    def test_submissao_resiliente_a_falha_de_rede(self, mock_urlopen: MagicMock) -> None:
        """Falha de rede na submissão não interrompe a esteira.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.side_effect = TimeoutError("timed out")

        resultado = submit_archive("https://exemplo.org/", timeout=1)

        self.assertEqual(resultado.status, "failed")
        self.assertIn("rede", resultado.detail or "")


class TestConsulta(unittest.TestCase):
    """Consulta do estado do trabalho e montagem do endereço perene."""

    @patch("urllib.request.urlopen")
    def test_captura_concluida_devolve_endereco_perene(self, mock_urlopen: MagicMock) -> None:
        """Captura concluída devolve o endereço montado a partir do carimbo e do original.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.return_value.__enter__.return_value = resposta(ESTADO_CONCLUIDO)

        resultado = poll_archive(ArchiveJob(job_id="spn2-abc", target_url="https://exemplo.org/"))

        self.assertEqual(resultado.status, "captured")
        self.assertEqual(resultado.url, "https://web.archive.org/web/20261024213206/https://exemplo.org/")

    @patch("urllib.request.urlopen")
    def test_captura_em_andamento_permanece_pendente(self, mock_urlopen: MagicMock) -> None:
        """Captura em andamento continua pendente, sem inventar endereço.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.return_value.__enter__.return_value = resposta(ESTADO_PENDENTE)

        resultado = poll_archive(ArchiveJob(job_id="spn2-abc", target_url="https://exemplo.org/"))

        self.assertEqual(resultado.status, "pending")
        self.assertIsNone(resultado.url)

    @patch("urllib.request.urlopen")
    def test_trabalho_sem_exito_registra_motivo(self, mock_urlopen: MagicMock) -> None:
        """Trabalho terminado sem êxito vira `failed` com o motivo do serviço.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        corpo = json.dumps({"status": "error", "status_ext": "blocked-by-robots.txt"})
        mock_urlopen.return_value.__enter__.return_value = resposta(corpo)

        resultado = poll_archive(ArchiveJob(job_id="spn2-abc", target_url="https://exemplo.org/"))

        self.assertEqual(resultado.status, "failed")
        self.assertIn("blocked-by-robots.txt", resultado.detail or "")

    @patch("urllib.request.urlopen")
    def test_estado_ilegivel_nao_derruba_o_ciclo(self, mock_urlopen: MagicMock) -> None:
        """Resposta que não é JSON vira falha registrada.

        Args:
            mock_urlopen: Substituto do transporte injetado pelo `patch`.
        """
        mock_urlopen.return_value.__enter__.return_value = resposta("<html>erro</html>")

        resultado = poll_archive(ArchiveJob(job_id="spn2-abc", target_url="https://exemplo.org/"))

        self.assertEqual(resultado.status, "failed")
        self.assertIn("ilegível", resultado.detail or "")

    def test_endereco_perene_segue_o_formato_do_wayback(self) -> None:
        """O endereço perene usa o formato observado no serviço."""
        self.assertEqual(
            snapshot_url("20261024213206", "https://exemplo.org/"),
            "https://web.archive.org/web/20261024213206/https://exemplo.org/",
        )


class TestOrcamento(unittest.TestCase):
    """Acompanhamento das capturas dentro do orçamento do ciclo."""

    @patch("observatorio.preservation.poll_archive")
    def test_concluida_antes_do_orcamento_nao_espera(self, mock_poll: MagicMock) -> None:
        """Trabalho concluído na primeira rodada não consome orçamento.

        Args:
            mock_poll: Substituto da consulta de estado.
        """
        from observatorio.preservation import ArchiveOutcome

        mock_poll.return_value = ArchiveOutcome(
            status="captured", url="https://web.archive.org/web/1/https://exemplo.org/", job_id="spn2-a"
        )

        resultados = await_archives(
            {"exemplo": ArchiveJob(job_id="spn2-a", target_url="https://exemplo.org/")}, budget=60, interval=0
        )

        self.assertEqual(resultados["exemplo"].status, "captured")
        self.assertEqual(mock_poll.call_count, 1)

    @patch("observatorio.preservation.poll_archive")
    def test_pendente_no_fim_do_orcamento_registra_motivo(self, mock_poll: MagicMock) -> None:
        """Quem não concluir no orçamento fica pendente, com o motivo escrito.

        Args:
            mock_poll: Substituto da consulta de estado.
        """
        from observatorio.preservation import ArchiveOutcome

        mock_poll.return_value = ArchiveOutcome(
            status="pending", job_id="spn2-a", detail="captura em andamento"
        )

        resultados = await_archives(
            {"exemplo": ArchiveJob(job_id="spn2-a", target_url="https://exemplo.org/")},
            budget=0,
            interval=0,
        )

        self.assertEqual(resultados["exemplo"].status, "pending")
        self.assertIn("orçamento", resultados["exemplo"].detail or "")


if __name__ == "__main__":
    unittest.main()
