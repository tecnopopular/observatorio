"""
tests/test_evaluator.py
Testes unitários para o motor de avaliação cívica nas 5 dimensões.
Executa avaliações puras sem chamadas de rede externas.
"""

import unittest

from observatorio.evaluator import CivicEvaluator
from observatorio.models import AuditLevel


class TestCivicEvaluator(unittest.TestCase):
    def setUp(self):
        """Prepara um avaliador novo e sem estado compartilhado para cada teste."""
        self.evaluator = CivicEvaluator()

    def test_compliant_tool_gets_certified(self):
        """Uma iniciativa com código aberto, fontes oficiais, sem trackers e propositiva deve receber Selo."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Monitor Eleitoral Cívico</title></head>
        <body>
            <h1>Monitor Eleitoral Popular</h1>
            <p>Plataforma para promover transparência, cidadania e dados abertos nas eleições.</p>
            <footer>
                <p>Fonte primária: <a href="https://dadosabertos.tse.jus.br">Dados Abertos do TSE</a></p>
                <p>Código aberto sob licença MIT no <a href="https://github.com/exemplo/monitor-civico">GitHub</a></p>
            </footer>
        </body>
        </html>
        """
        report = self.evaluator.evaluate_content(
            tool_id="monitor-civico",
            tool_name="Monitor Cívico",
            target_url="https://monitor.org",
            raw_html=html,
            declared_repo="https://github.com/exemplo/monitor-civico",
            declared_sources=["TSE"],
            response_time_ms=120,
            html_bytes=len(html.encode("utf-8")),
            html_sha256="abc123sha",
        )

        self.assertEqual(report.status, AuditLevel.AUDITADO)
        self.assertFalse(report.has_critical_blocker)
        self.assertGreaterEqual(report.total_score, 80.0)
        self.assertEqual(report.badge_label, "Certificado TecnoPopular")
        self.assertEqual(report.html_sha256, "abc123sha")

    def test_commercial_tracker_triggers_rejection(self):
        """Presença de Meta Pixel ou GA4 deve acionar bloqueador crítico (status rejeitado)."""
        html = """
        <html>
        <head>
            <script src="https://connect.facebook.net/en_US/fbevents.js"></script>
            <script>fbq('init', '1234567890');</script>
        </head>
        <body><p>Conteúdo de campanha</p></body>
        </html>
        """
        report = self.evaluator.evaluate_content(
            tool_id="rastreador-pixel",
            tool_name="App com Tracker",
            target_url="https://tracker.org",
            raw_html=html,
            response_time_ms=100,
            html_bytes=len(html.encode("utf-8")),
        )

        self.assertEqual(report.status, AuditLevel.REJEITADO)
        self.assertTrue(report.has_critical_blocker)
        self.assertEqual(report.badge_label, "Bloqueador Crítico de Privacidade")
        self.assertTrue(any("Meta Pixel" in b for b in report.critical_blockers))

    def test_cpf_input_triggers_rejection(self):
        """Presença de campo solicitando CPF de cidadãos deve acionar bloqueador crítico."""
        html = """
        <html>
        <body>
            <form>
                <label>Digite seu CPF:</label>
                <input type="text" name="cpf" />
            </form>
        </body>
        </html>
        """
        report = self.evaluator.evaluate_content(
            tool_id="coleta-cpf",
            tool_name="App Coleta CPF",
            target_url="https://cpf.org",
            raw_html=html,
            response_time_ms=100,
            html_bytes=len(html.encode("utf-8")),
        )

        self.assertEqual(report.status, AuditLevel.REJEITADO)
        self.assertTrue(report.has_critical_blocker)


if __name__ == "__main__":
    unittest.main()
