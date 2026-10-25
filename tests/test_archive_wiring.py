"""Testes da fase de preservação do ciclo.

O que se verifica aqui não é o cliente do Internet Archive (coberto em
`tests/test_wayback_preservation.py`), e sim a decisão da esteira: o que merece captura nova, quanto
se submete por ciclo e o que o laudo registra quando a captura não acontece.
"""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from observatorio.engine import _needs_capture, _preserve, run_preservation_pass
from observatorio.models import AuditLevel, CivicAuditReport
from observatorio.preservation import ArchiveOutcome
from observatorio.state import Manifest, ManifestEntry, load_manifest, write_manifest


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


class TestPassoDePreservacao(unittest.TestCase):
    """O passo avulso usa o manifesto como fila e não baixa página nenhuma."""

    def _cenario(self, limite: int) -> tuple[dict, dict]:
        """Monta catálogo e manifesto temporários e executa o passo de preservação.

        Args:
            limite: Número máximo de submissões na execução.

        Returns:
            Manifesto gravado, indexado por `tool_id`, e a lista de endereços submetidos.
        """
        catalogo = [
            {"nome": "Alfa", "url": "https://alfa.org/"},
            {"nome": "Beta", "url": "https://beta.org/"},
            {"nome": "Gama", "url": "https://gama.org/"},
        ]
        manifesto = Manifest(
            generated_at=datetime.now(UTC),
            total=2,
            entries=[
                ManifestEntry(
                    tool_id="alfa",
                    evaluated_at="2026-10-24T00:00:00+00:00",
                    status=AuditLevel.CATALOGADO,
                    html_sha256="a" * 64,
                    archive_url="https://web.archive.org/web/1/https://alfa.org/",
                    archive_status="captured",
                ),
                ManifestEntry(
                    tool_id="beta",
                    evaluated_at="2026-10-24T00:00:00+00:00",
                    status=AuditLevel.CATALOGADO,
                    html_sha256="b" * 64,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            catalogo_path = Path(tmp) / "catalogo.json"
            manifesto_path = Path(tmp) / "manifesto.json"
            catalogo_path.write_text(json.dumps(catalogo), encoding="utf-8")
            write_manifest(manifesto_path, manifesto)

            submetidos: list[str] = []

            def registro(url: str, timeout: int = 15) -> ArchiveOutcome:
                """Registra o endereço submetido e devolve trabalho pendente.

                Args:
                    url: Endereço submetido ao Internet Archive.
                    timeout: Tempo limite recebido, ignorado no dublê.

                Returns:
                    Resultado com trabalho pendente, para exercitar o acompanhamento.
                """
                submetidos.append(url)
                return ArchiveOutcome(status="pending", job_id=f"spn2-{len(submetidos)}")

            with (
                patch("observatorio.engine.submit_archive", side_effect=registro),
                patch("observatorio.engine.await_archives") as mock_await,
            ):
                mock_await.side_effect = lambda pendentes, budget: {
                    tool_id: ArchiveOutcome(
                        status="captured", url=f"https://web.archive.org/web/2/{job.target_url}"
                    )
                    for tool_id, job in pendentes.items()
                }
                run_preservation_pass(catalogo_path, manifesto_path, limit=limite, budget=10)

            gravado = load_manifest(manifesto_path)
        return {entrada.tool_id: entrada for entrada in gravado.entries}, submetidos

    def test_pula_o_que_ja_tem_snapshot(self) -> None:
        """Endereço com `archive_url` no manifesto não é submetido de novo."""
        entradas, submetidos = self._cenario(limite=10)

        self.assertNotIn("https://alfa.org/", submetidos)
        self.assertEqual(entradas["alfa"].archive_url, "https://web.archive.org/web/1/https://alfa.org/")

    def test_grava_endereco_no_manifesto(self) -> None:
        """A captura concluída entra no manifesto, sem tocar em laudo."""
        entradas, _ = self._cenario(limite=10)

        self.assertEqual(entradas["beta"].archive_status, "captured")
        self.assertEqual(entradas["beta"].archive_url, "https://web.archive.org/web/2/https://beta.org/")

    def test_limite_pretere_o_resto(self) -> None:
        """Acima do limite, o endereço fica registrado como `deferred`, sem submissão."""
        _, submetidos = self._cenario(limite=1)

        self.assertEqual(len(submetidos), 1)

    def test_entrada_ausente_no_manifesto_nao_e_submetida(self) -> None:
        """Iniciativa sem entrada no manifesto espera a avaliação do ciclo semanal."""
        _, submetidos = self._cenario(limite=10)

        self.assertNotIn("https://gama.org/", submetidos)

    def test_timeouts_seguidos_toleram_mais_que_recusas(self) -> None:
        """Falha de rede para após três endereços; recusa, após dois."""
        relatorios = [laudo(f"item{n}") for n in range(5)]
        with patch("observatorio.engine.submit_archive") as mock_submit:
            mock_submit.return_value = ArchiveOutcome(status="failed", detail="timed out")
            with patch("observatorio.engine.time.sleep"):
                _preserve(relatorios, {}, skip_archive=False, limit=10, budget=10)

        self.assertEqual(mock_submit.call_count, 6)
        self.assertEqual(relatorios[2].archive_status, "failed")
        self.assertEqual(relatorios[3].archive_status, "deferred")


if __name__ == "__main__":
    unittest.main()
