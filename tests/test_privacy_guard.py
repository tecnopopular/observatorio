"""
tests/test_privacy_guard.py
Testes unitários para o portão de privacidade CivicPrivacyGuard (LGPD e Segredos).
"""

import unittest

from observatorio.privacy_guard import CivicPrivacyGuard, ViolationSeverity


class TestCivicPrivacyGuard(unittest.TestCase):
    def setUp(self):
        """Prepara um portão de privacidade novo e sem estado compartilhado para cada teste."""
        self.guard = CivicPrivacyGuard()

    def test_detects_real_cpf(self):
        """Sequência com dígitos verificadores de CPF válidos deve ser bloqueada."""
        content = "O responsável pelo projeto é portador do documento 111.444.777-35."
        violations = self.guard.scan_text(content, "exemplo.json")
        blockers = [v for v in violations if v.severity == ViolationSeverity.BLOCKER]

        self.assertGreaterEqual(len(blockers), 1)
        self.assertTrue(any("CPF" in b.message for b in blockers))

    def test_detects_brazilian_phone(self):
        """Telefone celular brasileiro em texto deve ser bloqueado."""
        content = "Dúvidas falar com a coordenação no WhatsApp: (11) 98765-4321."
        violations = self.guard.scan_text(content, "exemplo.json")
        blockers = [v for v in violations if v.severity == ViolationSeverity.BLOCKER]

        self.assertGreaterEqual(len(blockers), 1)
        self.assertTrue(any("telefone" in b.message.lower() for b in blockers))

    def test_detects_api_secret(self):
        """Chave de API em texto deve ser bloqueada."""
        content = 'export HF_TOKEN="hf_' + "A" * 34 + '"'
        violations = self.guard.scan_text(content, "script.sh")
        blockers = [v for v in violations if v.severity == ViolationSeverity.BLOCKER]

        self.assertGreaterEqual(len(blockers), 1)
        self.assertTrue(any("Token" in b.message or "chave" in b.message.lower() for b in blockers))

    def test_whitelists_legitimate_civic_urls(self):
        """URLs públicas legítimas do catálogo não devem ser bloqueadas."""
        content = """
        [
            {"nome": "Radar da Virada", "url": "https://radardavirada.pages.dev"},
            {"nome": "Dados TSE", "url": "https://dadosabertos.tse.jus.br"}
        ]
        """
        violations = self.guard.scan_text(content, "data/data_iniciativas.json")
        blockers = [v for v in violations if v.severity == ViolationSeverity.BLOCKER]

        self.assertEqual(len(blockers), 0)


if __name__ == "__main__":
    unittest.main()
