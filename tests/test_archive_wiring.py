"""Testes da fase de preservação do ciclo.

O que se verifica aqui não é o cliente do Internet Archive (coberto em
`tests/test_wayback_preservation.py`), e sim a decisão da esteira: o que merece captura nova, quanto
se submete por ciclo e o que o laudo registra quando a captura não acontece.
"""

import unittest
from unittest.mock import patch

from observatorio.engine import _needs_capture, _preserve
from observatorio.models import AuditLevel, CivicAuditReport
from observatorio.preservation import ArchiveOutcome
from observatorio.state import ManifestEntry


def laudo(tool_id: str, sha: str = "a" * 64) -> CivicAuditReport:
    """Monta um laudo mínimo para exercitar a fase de preservação.

    Args:
        tool_id: Identificador da iniciativa.
        sha: Hash do conteúdo inspecionado.

    Returns:
        Laudo com nota e nível mínimos.
    """
    return CivicAuditReport(
        tool_id=tool_id,
        tool_name=tool_id,
        target_url=f"https://{tool_id}.org/",
        status=AuditLevel.CATALOGADO,
        total_score=70.0,
        dimension_scores={},
        badge_label="Diretório Aberto",
        html_sha256=sha,
    )


def entrada(url: str | None, sha: str = "a" * 64) -> ManifestEntry:
    """Monta uma entrada de manifesto anterior.

    Args:
        url: Endereço perene do snapshot, quando já existe.
        sha: Hash registrado no ciclo anterior.

    Returns:
        Entrada de manifesto com os campos usados na decisão de preservação.
    """
    return ManifestEntry(
        tool_id="painel",
        evaluated_at="2026-10-24T00:00:00+00:00",
        status=AuditLevel.CATALOGADO,
        html_sha256=sha,
        archive_url=url,
    )


class TestDecisaoDeCaptura(unittest.TestCase):
    """Só pede captura nova quando há conteúdo novo ou quando não há snapshot conhecido."""

    def test_sem_snapshot_anterior_precisa_capturar(self) -> None:
        """Endereço nunca preservado entra na fila."""
        self.assertTrue(_needs_capture(laudo("painel"), {}))

    def test_hash_igual_com_snapshot_nao_precisa_capturar(self) -> None:
        """Conteúdo idêntico com snapshot conhecido não gasta o limite do serviço."""
        manifesto = {"painel": entrada("https://web.archive.org/web/1/https://painel.org/")}
        self.assertFalse(_needs_capture(laudo("painel"), manifesto))

    def test_hash_diferente_precisa_capturar(self) -> None:
        """Conteúdo que mudou desde o último snapshot recebe captura nova."""
        manifesto = {"painel": entrada("https://web.archive.org/web/1/https://painel.org/", "b" * 64)}
        self.assertTrue(_needs_capture(laudo("painel"), manifesto))


class TestFaseDePreservacao(unittest.TestCase):
    """Envio serial, com limite por ciclo e situação registrada em cada laudo."""

    def test_limite_pretere_o_excedente_como_deferred(self) -> None:
        """O que passa do limite não é submetido e fica registrado como `deferred`."""
        relatorios = [laudo("alfa"), laudo("beta"), laudo("gama")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="pending", job_id="spn2-a")
            with patch("observatorio.engine.await_archives") as mock_await:
                mock_await.return_value = {
                    "alfa": ArchiveOutcome(
                        status="captured", url="https://web.archive.org/web/9/https://alfa.org/"
                    )
                }
                _preserve(relatorios, {}, skip_archive=False, limit=1, budget=60)

        situacoes = {r.tool_id: r.archive_status for r in relatorios}
        self.assertEqual(situacoes["alfa"], "captured")
        self.assertEqual(situacoes["beta"], "deferred")
        self.assertEqual(situacoes["gama"], "deferred")
        self.assertEqual(mock_submit.call_count, 1)

    def test_captura_concluida_grava_endereco(self) -> None:
        """Captura concluída grava o endereço perene no laudo."""
        relatorios = [laudo("painel")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="pending", job_id="spn2-a")
            with patch("observatorio.engine.await_archives") as mock_await:
                mock_await.return_value = {
                    "painel": ArchiveOutcome(
                        status="captured",
                        url="https://web.archive.org/web/20261024213206/https://painel.org/",
                    )
                }
                _preserve(relatorios, {}, skip_archive=False, limit=25, budget=60)

        self.assertEqual(relatorios[0].archive_status, "captured")
        self.assertEqual(
            relatorios[0].archive_url, "https://web.archive.org/web/20261024213206/https://painel.org/"
        )

    def test_pendente_mantem_endereco_anterior(self) -> None:
        """Captura não concluída registra `pending` e não inventa endereço novo."""
        relatorios = [laudo("painel", "c" * 64)]
        relatorios[0].archive_url = "https://web.archive.org/web/1/https://painel.org/"
        manifesto = {"painel": entrada("https://web.archive.org/web/1/https://painel.org/", "b" * 64)}
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="pending", job_id="spn2-a")
            with patch("observatorio.engine.await_archives") as mock_await:
                mock_await.return_value = {
                    "painel": ArchiveOutcome(status="pending", detail="não concluída no orçamento")
                }
                _preserve(relatorios, manifesto, skip_archive=False, limit=25, budget=60)

        self.assertEqual(relatorios[0].archive_status, "pending")
        self.assertEqual(relatorios[0].archive_url, "https://web.archive.org/web/1/https://painel.org/")

    def test_recusas_consecutivas_interrompem_os_envios(self) -> None:
        """Recusa repetida para de enviar, em vez de insistir contra o serviço."""
        relatorios = [laudo("alfa"), laudo("beta"), laudo("gama")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="refused", detail="HTTP 429")
            with patch("observatorio.engine.time.sleep"):
                _preserve(relatorios, {}, skip_archive=False, limit=25, budget=60)

        self.assertEqual(mock_submit.call_count, 2)
        self.assertEqual(relatorios[2].archive_status, "deferred")

    def test_skip_archive_registra_dispensa(self) -> None:
        """Com a preservação dispensada, cada laudo diz `skipped` em vez de ficar em silêncio."""
        relatorios = [laudo("alfa")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            _preserve(relatorios, {}, skip_archive=True, limit=25, budget=60)

        mock_submit.assert_not_called()
        self.assertEqual(relatorios[0].archive_status, "skipped")

    def test_falha_de_rede_ganha_segunda_tentativa(self) -> None:
        """Uma instabilidade passageira não derruba o ciclo: a submissão é repetida uma vez."""
        relatorios = [laudo("alfa")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.side_effect = [
                ArchiveOutcome(status="failed", detail="falha de rede na submissão: ConnectionReset"),
                ArchiveOutcome(status="pending", job_id="spn2-b"),
            ]
            with patch("observatorio.engine.await_archives") as mock_await:
                mock_await.return_value = {
                    "alfa": ArchiveOutcome(
                        status="captured", url="https://web.archive.org/web/9/https://alfa.org/"
                    )
                }
                with patch("observatorio.engine.time.sleep"):
                    _preserve(relatorios, {}, skip_archive=False, limit=10, budget=60)

        self.assertEqual(mock_submit.call_count, 2)
        self.assertEqual(relatorios[0].archive_status, "captured")

    def test_recusa_nao_ganha_segunda_tentativa(self) -> None:
        """Recusa do serviço não melhora com insistência: não é repetida."""
        relatorios = [laudo("alfa")]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="refused", detail="HTTP 429")
            with patch("observatorio.engine.time.sleep"):
                _preserve(relatorios, {}, skip_archive=False, limit=10, budget=60)

        self.assertEqual(mock_submit.call_count, 1)
        self.assertEqual(relatorios[0].archive_status, "refused")

    def test_endereco_do_manifesto_e_herdado(self) -> None:
        """Conteúdo inalterado herda o endereço já preservado, em vez de ficar sem estado."""
        relatorios = [laudo("painel")]
        manifesto = {"painel": entrada("https://web.archive.org/web/20261024213206/https://painel.org/")}
        with patch("observatorio.engine.submit_archive") as mock_submit:
            _preserve(relatorios, manifesto, skip_archive=False, limit=10, budget=60)

        mock_submit.assert_not_called()
        self.assertEqual(relatorios[0].archive_status, "reused")
        self.assertEqual(
            relatorios[0].archive_url,
            "https://web.archive.org/web/20261024213206/https://painel.org/",
        )


if __name__ == "__main__":
    unittest.main()
