"""
tests/test_score_consistency.py
Testes de consistência e integridade estrutural para os dados e laudos do Observatório.
"""

import json
import unittest
from pathlib import Path

from observatorio.models import AuditLevel, AuditRubric, DimensionType


class TestScoreConsistency(unittest.TestCase):
    """Consistência do catálogo e dos laudos versionados."""

    def setUp(self):
        """Define os caminhos de dados e o laudo usados pelos testes.

        O laudo completo é artefato de publicação: vive em `dist/` e não é versionado.
        A cópia legada em `data/` era aceita para repositórios ainda não migrados; a
        seleção atual considera apenas o caminho publicado.
        """
        self.root = Path(__file__).parent.parent
        self.data_dir = self.root / "data"
        self.iniciativas_file = self.data_dir / "data_iniciativas.json"
        candidatos = [
            self.root / "dist" / "auditoria_iniciativas.json",
        ]
        self.auditorias_file = next((p for p in candidatos if p.exists()), candidatos[0])
        self.rubric = AuditRubric()

    def test_iniciativas_catalog_format(self):
        """Verifica se o catálogo base data_iniciativas.json é válido e tem campos mínimos."""
        self.assertTrue(self.iniciativas_file.exists(), "data_iniciativas.json não encontrado")
        with open(self.iniciativas_file, encoding="utf-8") as f:
            items = json.load(f)

        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 10, "Catálogo deve conter iniciativas cívicas")

        for item in items:
            self.assertIn("nome", item, "Iniciativa deve conter 'nome'")
            self.assertIn("url", item, "Iniciativa deve conter 'url'")
            self.assertTrue(item["url"].startswith("http"), f"URL inválida: {item['url']}")

    def test_auditoria_report_consistency(self):
        """Valida estrutura e notas ponderadas de cada laudo, se o laudo existir.

        Cada laudo precisa declarar identificador, nota total e status válido, conter as
        cinco dimensões da rubrica e apresentar nota ponderada coerente com o peso da
        dimensão.
        """
        if not self.auditorias_file.exists():
            self.skipTest("auditoria_iniciativas.json ainda não gerado nesta rodada.")

        with open(self.auditorias_file, encoding="utf-8") as f:
            reports = json.load(f)

        self.assertIsInstance(reports, list)
        for r in reports:
            self.assertIn("tool_id", r)
            self.assertIn("total_score", r)
            self.assertIn("status", r)
            self.assertIn(r["status"], [lvl.value for lvl in AuditLevel])

            dim_scores = r.get("dimension_scores", {})
            for d in DimensionType:
                self.assertIn(d.value, dim_scores, f"Dimensão {d.value} ausente no laudo {r['tool_id']}")
                ds = dim_scores[d.value]
                expected_weighted = round(ds["raw_score"] * (self.rubric.weights[d] / 100.0), 2)
                self.assertAlmostEqual(
                    ds["weighted_score"],
                    expected_weighted,
                    places=1,
                    msg=f"Nota ponderada inconsistente em {r['tool_id']} para {d.value}",
                )


if __name__ == "__main__":
    unittest.main()
