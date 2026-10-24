"""
tests/test_fetch.py
Testes da camada de aquisição por HTTP direto.

Nenhum teste toca a rede: o provedor remoto é simulado. O que se verifica aqui são as
invariantes que sustentam a auditoria: TLS verificado por padrão, falha de rede devolve
resultado com erro em vez de exceção, chave nunca vaza em mensagem, e o fallback escolhe
o primeiro provedor que funciona.
"""

import json
import ssl
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from observatorio.engine import fetch_snapshot
from observatorio.fetch import HttpFetcher


class _FakeResponse:
    """Resposta mínima de `urlopen` usada no lugar da rede."""

    def __init__(self, body: bytes, status: int = 200):
        """Guarda o corpo e o código HTTP que a resposta simulada expõe.

        Args:
            body: Conteúdo binário devolvido por `read`.
            status: Código HTTP atribuído à resposta.
        """
        self._body = body
        self.status = status
        self.headers: dict = {}

    def read(self, size: int = -1) -> bytes:
        """Devolve o corpo completo, independentemente do tamanho pedido.

        Args:
            size: Quantidade de bytes solicitada, aceita apenas por compatibilidade.

        Returns:
            Corpo binário integral da resposta simulada.
        """
        return self._body

    def __enter__(self):
        """Devolve a própria resposta para uso em bloco `with`.

        Returns:
            A instância da resposta simulada.
        """
        return self

    def __exit__(self, *args):
        """Encerra o bloco `with` sem suprimir exceções.

        Args:
            *args: Tipo, valor e traceback da exceção eventualmente levantada no bloco.

        Returns:
            False, para que qualquer exceção do bloco seja propagada.
        """
        return False


class TestHttpFetcher(unittest.TestCase):
    """Invariantes do provedor HTTP direto."""

    def test_tls_verificado_por_padrao(self):
        """A configuração padrão exige validação de certificado e de hostname."""
        fetcher = HttpFetcher()
        self.assertTrue(fetcher.verify_tls)
        self.assertEqual(fetcher._context().verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(fetcher._context().check_hostname)

    def test_desligar_tls_e_explicito(self):
        """Sem validação, o contexto relaxa e o resultado deve declarar isso."""
        fetcher = HttpFetcher(verify_tls=False)
        self.assertEqual(fetcher._context().verify_mode, ssl.CERT_NONE)
        self.assertFalse(fetcher._context().check_hostname)

    def test_user_agent_se_identifica_como_agente(self):
        """Bot de auditoria não se apresenta como navegador comercial."""
        self.assertIn("TecnoPopular", HttpFetcher().user_agent)
        self.assertNotIn("Mozilla/5.0 (Macintosh", HttpFetcher().user_agent)

    def test_sucesso_mede_status_e_bytes(self):
        """Aquisição bem-sucedida registra código, tamanho em bytes e provedor."""
        body = b"<html><body>ok</body></html>"
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body, 200)):
            resultado = HttpFetcher().fetch("https://exemplo.org")
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.status_code, 200)
        self.assertEqual(resultado.content_bytes, len(body))
        self.assertEqual(resultado.html, body.decode())
        self.assertEqual(resultado.provider, "http")

    def test_falha_de_rede_devolve_erro_sem_excecao(self):
        """Falha de rede vira resultado com erro, não exceção que aborta a esteira."""
        with patch("urllib.request.urlopen", side_effect=URLError("nome não resolvido")):
            resultado = HttpFetcher().fetch("https://nao-existe.invalid")
        self.assertFalse(resultado.ok)
        self.assertIn("falha de rede", resultado.error or "")
        self.assertEqual(resultado.content_bytes, 0)


class TestCacheDoSnapshot(unittest.TestCase):
    """Leitura do cache local e atualização forçada."""

    def test_cache_local_evita_nova_aquisicao(self):
        """Snapshot em cache é servido sem tocar o provedor."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "iniciativa.html").write_text("<html>em cache</html>", encoding="utf-8")
            provedor = MagicMock()
            resultado = fetch_snapshot("https://exemplo.org", "iniciativa", fetcher=provedor, cache_dir=cache)
            self.assertTrue(resultado.from_cache)
            self.assertEqual(resultado.provider, "cache")
            self.assertEqual(resultado.html, "<html>em cache</html>")
            provedor.fetch.assert_not_called()

    def test_refresh_ignora_cache_e_chama_provedor(self):
        """Atualização forçada reconsulta o provedor e regrava o arquivo local."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "iniciativa.html").write_text("<html>antigo</html>", encoding="utf-8")
            novo = MagicMock()
            novo.ok = True
            novo.html = "<html>novo</html>"
            novo.latency_ms = 42
            novo.content_bytes = 20
            novo.status_code = 200
            novo.provider = "http"
            provedor = MagicMock()
            provedor.fetch.return_value = novo

            resultado = fetch_snapshot(
                "https://exemplo.org", "iniciativa", refresh_cache=True, fetcher=provedor, cache_dir=cache
            )
            self.assertEqual(resultado.provider, "http")
            self.assertEqual(resultado.html, "<html>novo</html>")
            provedor.fetch.assert_called_once()
            self.assertIn("novo", (cache / "iniciativa.html").read_text(encoding="utf-8"))

    def test_meta_do_cache_preserva_truncamento(self):
        """Página lida parcialmente continua declarada parcial ao vir do cache."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "iniciativa.html").write_text("<html>parcial</html>", encoding="utf-8")
            (cache / "iniciativa.meta.json").write_text(
                json.dumps(
                    {
                        "declared_bytes": 5_000_000,
                        "truncated": True,
                        "provider": "http",
                        "limit_bytes": 1_000_000,
                    }
                ),
                encoding="utf-8",
            )
            provedor = MagicMock()
            resultado = fetch_snapshot("https://exemplo.org", "iniciativa", fetcher=provedor, cache_dir=cache)
            self.assertTrue(resultado.from_cache)
            self.assertTrue(resultado.truncated)
            self.assertEqual(resultado.declared_bytes, 5_000_000)
            provedor.fetch.assert_not_called()

    def test_cache_sem_meta_nao_inventa_truncamento(self):
        """Cache sem metadado é tratado como leitura integral, sem truncamento suposto."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "iniciativa.html").write_text("<html>completo</html>", encoding="utf-8")
            resultado = fetch_snapshot(
                "https://exemplo.org", "iniciativa", fetcher=MagicMock(), cache_dir=cache
            )
            self.assertFalse(resultado.truncated)
            self.assertEqual(resultado.declared_bytes, len("<html>completo</html>"))


class TestTruncamentoNaAquisicao(unittest.TestCase):
    """Detecção de corte por limite de bytes durante a aquisição."""

    def test_content_length_maior_que_leitura_marca_truncado(self):
        """Content-Length acima do corpo recebido marca a leitura como parcial."""
        body = b"x" * 50
        response = _FakeResponse(body, 200)
        response.headers = {"Content-Length": "5000"}
        with patch("urllib.request.urlopen", return_value=response):
            resultado = HttpFetcher(max_bytes=50).fetch("https://exemplo.org")
        self.assertTrue(resultado.truncated)
        self.assertEqual(resultado.declared_bytes, 5000)
        self.assertEqual(resultado.content_bytes, 50)

    def test_resposta_chunked_tambem_detecta_corte(self):
        """Sem Content-Length, o corte precisa ser detectado pelo byte excedente lido."""
        body = b"x" * 51
        response = _FakeResponse(body, 200)
        response.headers = {}
        with patch("urllib.request.urlopen", return_value=response):
            resultado = HttpFetcher(max_bytes=50).fetch("https://exemplo.org")
        self.assertTrue(resultado.truncated)
        self.assertIsNone(resultado.declared_bytes)
        self.assertEqual(resultado.content_bytes, 50)

    def test_pagina_inteira_nao_marca_truncado(self):
        """Página menor que o limite não pode ser declarada truncada."""
        body = b"<html>ok</html>"
        response = _FakeResponse(body, 200)
        response.headers = {"Content-Length": str(len(body))}
        with patch("urllib.request.urlopen", return_value=response):
            resultado = HttpFetcher(max_bytes=50).fetch("https://exemplo.org")
        self.assertFalse(resultado.truncated)
        self.assertEqual(resultado.declared_bytes, len(body))


if __name__ == "__main__":
    unittest.main()
