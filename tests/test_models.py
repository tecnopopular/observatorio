"""
tests/test_models.py
Testes unitários para validação dos modelos Pydantic e regras da rubrica cívica.
"""

import unittest
from datetime import UTC, datetime

from observatorio.models import (
    AuditLevel,
    AuditRubric,
    CivicAuditReport,
    DimensionScore,
    DimensionType,
    Iniciativa,
    Snapshot,
)


class TestObservatorioModels(unittest.TestCase):
    def test_rubric_weights_sum_to_100(self):
        """Garante que a soma dos pesos das 5 dimensões seja exatamente 100%."""
        rubric = AuditRubric()
        total_weight = sum(rubric.weights.values())
        self.assertEqual(total_weight, 100.0, "A soma dos pesos deve ser exatamente 100%")

    def test_rubric_has_all_five_dimensions(self):
        """Verifica se todas as 5 dimensões estão configuradas na rubrica."""
        rubric = AuditRubric()
        expected = {
            DimensionType.PROVENIENCIA_DADOS,
            DimensionType.PRIVACIDADE_SOBERANIA,
            DimensionType.CODIGO_ABERTO_FLOSS,
            DimensionType.ENQUADRAMENTO_REPUBLICANO,
            DimensionType.ACESSIBILIDADE_DESEMPENHO,
        }
        self.assertEqual(set(rubric.weights.keys()), expected)
        self.assertEqual(set(rubric.min_dimension_score.keys()), expected)

    def test_iniciativa_model(self):
        """Testa instanciação e validação do modelo Iniciativa."""
        ini = Iniciativa(
            tool_id="radar-da-virada",
            nome="Radar da Virada",
            url="https://radardavirada.pages.dev",
            domain="radardavirada.pages.dev",
            categoria="Dados e Mapas",
            descricao="Painel visual de acompanhamento da movimentação eleitoral.",
            repositorio="https://github.com/exemplo/radar",
            fontes_primarias=["TSE"],
        )
        self.assertEqual(ini.tool_id, "radar-da-virada")
        self.assertEqual(ini.fontes_primarias, ["TSE"])

    def test_snapshot_model(self):
        """Testa criação de Snapshot com hash SHA-256."""
        now = datetime.now(UTC)
        snap = Snapshot(
            tool_id="radar-da-virada",
            captured_at=now,
            http_status=200,
            response_time_ms=150,
            content_length_bytes=2048,
            html_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(snap.http_status, 200)
        self.assertTrue(snap.html_sha256.startswith("e3b0"))

    def test_civic_audit_report_serialization(self):
        """Testa instanciação e serialização do laudo de avaliação cívica."""
        rubric = AuditRubric()
        dim_scores = {}
        for d in DimensionType:
            dim_scores[d] = DimensionScore(
                dimension=d,
                weight_percentage=rubric.weights[d],
                raw_score=90.0,
                weighted_score=90.0 * (rubric.weights[d] / 100.0),
                is_passing=True,
                findings=["Critério plenamente atendido."],
                recommendations=[],
            )

        total = sum(ds.weighted_score for ds in dim_scores.values())

        rep = CivicAuditReport(
            tool_id="radar-da-virada",
            tool_name="Radar da Virada",
            target_url="https://radardavirada.pages.dev",
            status=AuditLevel.AUDITADO,
            total_score=total,
            badge_label="Certificado TecnoPopular",
            html_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            archive_url="https://web.archive.org/web/20261020000000/https://radardavirada.pages.dev",
            dimension_scores=dim_scores,
        )

        data = rep.model_dump(mode="json")
        self.assertEqual(data["tool_id"], "radar-da-virada")
        self.assertEqual(data["total_score"], 90.0)
        self.assertEqual(data["status"], "auditado")
        self.assertEqual(data["badge_label"], "Certificado TecnoPopular")
        self.assertIn("web.archive.org", data["archive_url"])


if __name__ == "__main__":
    unittest.main()
