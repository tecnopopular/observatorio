"""
tests/test_cache_invalidation.py
Testes unitários para cálculo determinístico de SHA-256 e lógica de invalidação de cache.
"""

import unittest

from observatorio.preservation import calculate_sha256, check_content_changed


class TestCacheInvalidation(unittest.TestCase):
    def test_sha256_deterministic(self):
        """O cálculo de hash para a mesma string deve ser estritamente idêntico."""
        content1 = "<html><body>Olá Mundo</body></html>"
        content2 = "<html><body>Olá Mundo</body></html>"
        hash1 = calculate_sha256(content1)
        hash2 = calculate_sha256(content2)

        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)

    def test_sha256_changes_on_single_char_diff(self):
        """Qualquer alteração mínima no conteúdo deve produzir um hash diferente."""
        content1 = "<html><body>Olá Mundo</body></html>"
        content2 = "<html><body>Olá Mundo!</body></html>"
        hash1 = calculate_sha256(content1)
        hash2 = calculate_sha256(content2)

        self.assertNotEqual(hash1, hash2)

    def test_check_content_changed_logic(self):
        """Classifica ausência de hash, hash divergente e hash idêntico na invalidação de cache.

        Sem hash anterior o conteúdo conta como alterado e força reavaliação; hashes
        divergentes indicam mudança; hashes idênticos, inclusive com caixa diferente,
        indicam conteúdo inalterado e permitem reaproveitar o cache.
        """
        h1 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        h2 = "d41d8cd98f00b204e9800998ecf8427e00000000000000000000000000000000"

        self.assertTrue(check_content_changed(h1, None))
        self.assertTrue(check_content_changed(h1, ""))

        self.assertTrue(check_content_changed(h1, h2))

        self.assertFalse(check_content_changed(h1, h1))
        self.assertFalse(check_content_changed(h1.upper(), h1.lower()))


if __name__ == "__main__":
    unittest.main()
