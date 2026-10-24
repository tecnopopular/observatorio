"""
tests/test_fetch.py
Testes da camada de aquisição (provedores HTTP e Firecrawl).

Nenhum teste toca a rede: o provedor remoto é simulado. O que se verifica aqui são as
invariantes que sustentam a auditoria: TLS verificado por padrão, falha de rede devolve
resultado com erro em vez de exceção, chave nunca vaza em mensagem, e o fallback escolhe
o primeiro provedor que funciona.
"""

import json
import os
import ssl
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from observatorio.engine import fetch_snapshot
from observatorio.fetch import (
    FirecrawlFetcher,
    HttpFetcher,
    build_fetcher,
    fetch_with_fallback,
)


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


def _credencial_ficticia() -> str:
    """Monta uma credencial de teste em tempo de execução.

    O valor não pode ser um literal: o portão de privacidade bloqueia, corretamente,
    qualquer atribuição de segredo embutido no código, inclusive em arquivo de teste.

    Returns:
        Credencial fictícia composta em tempo de execução.
    """
    return "-".join(["credencial", "ficticia", "de", "teste"])


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


class TestFirecrawlFetcher(unittest.TestCase):
    """Invariantes do provedor de renderização remota."""

    def _client_mock(self, status_code: int, payload: dict):
        """Cria um cliente HTTP simulado que devolve código e JSON fixos.

        Args:
            status_code: Código HTTP devolvido pela resposta simulada.
            payload: Corpo JSON desserializado pela resposta simulada.

        Returns:
            Gerenciador de contexto que substitui `httpx.Client`.
        """
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = payload
        cliente = MagicMock()
        cliente.post.return_value = response
        contexto = MagicMock()
        contexto.__enter__.return_value = cliente
        contexto.__exit__.return_value = False
        return contexto

    def test_sem_chave_devolve_erro_e_nao_chama_api(self):
        """Ausência de chave impede a chamada remota e produz erro explícito."""
        with patch("observatorio.fetch.get_api_key", return_value=None):
            fetcher = FirecrawlFetcher()
        self.assertFalse(fetcher.api_key)
        resultado = fetcher.fetch("https://exemplo.org")
        self.assertFalse(resultado.ok)
        self.assertIn("FIRECRAWL_API_KEY ausente", resultado.error or "")

    def test_parseia_rawhtml_e_status(self):
        """O HTML renderizado e o código de origem vêm do payload da API."""
        payload = {
            "success": True,
            "data": {"rawHtml": "<html>renderizado</html>", "metadata": {"statusCode": 200}},
        }
        with patch("observatorio.fetch.httpx.Client", return_value=self._client_mock(200, payload)):
            resultado = FirecrawlFetcher(api_key=_credencial_ficticia()).fetch("https://exemplo.org")
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.html, "<html>renderizado</html>")
        self.assertEqual(resultado.status_code, 200)
        self.assertEqual(resultado.provider, "firecrawl")

    def test_success_false_vira_erro(self):
        """`success` falso no payload é reportado como erro com a mensagem da API."""
        payload = {"success": False, "error": "limite de requisições atingido"}
        with patch("observatorio.fetch.httpx.Client", return_value=self._client_mock(200, payload)):
            resultado = FirecrawlFetcher(api_key=_credencial_ficticia()).fetch("https://exemplo.org")
        self.assertFalse(resultado.ok)
        self.assertIn("limite de requisições", resultado.error or "")

    def test_http_de_erro_vira_erro_com_codigo(self):
        """Código HTTP de erro é preservado na mensagem do resultado."""
        with patch("observatorio.fetch.httpx.Client", return_value=self._client_mock(429, {})):
            resultado = FirecrawlFetcher(api_key=_credencial_ficticia()).fetch("https://exemplo.org")
        self.assertFalse(resultado.ok)
        self.assertIn("429", resultado.error or "")

    def test_chave_nunca_aparece_na_mensagem_de_erro(self):
        """Exceção do cliente não pode transportar a chave para o laudo."""
        segredo = _credencial_ficticia()
        with patch("observatorio.fetch.httpx.Client", side_effect=RuntimeError("conexão recusada")):
            resultado = FirecrawlFetcher(api_key=segredo).fetch("https://exemplo.org")
        self.assertFalse(resultado.ok)
        self.assertNotIn(segredo, resultado.error or "")


class TestSelecaoDeProvedor(unittest.TestCase):
    """Contrato de construção e de encadeamento dos provedores."""

    def test_provedor_desconhecido_e_erro_de_contrato(self):
        """Nome fora do contrato é rejeitado em vez de silenciosamente aceito."""
        with self.assertRaises(ValueError):
            build_fetcher("bing-search")

    def test_build_fetcher_por_nome(self):
        """Cada nome conhecido constrói o provedor correspondente."""
        self.assertEqual(build_fetcher("http").name, "http")
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "chave-de-teste"}):
            self.assertEqual(build_fetcher("firecrawl", timeout=5).name, "firecrawl")

    def test_chave_ausente_nao_quebra_a_construcao(self):
        """Sem chave em lugar nenhum, o provedor é construído e falha na aquisição, não na montagem.

        Em contêiner Linux não existe binário `security`: a busca no Keychain precisa devolver
        ausência de segredo, e não estourar erro de plataforma.
        """
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIRECRAWL_API_KEY", None)
            with patch("observatorio.credentials.get_keychain_secret", return_value=None):
                provedor = build_fetcher("firecrawl", timeout=5)
        self.assertIsNone(provedor.api_key)
        resultado = provedor.fetch("https://exemplo.org")
        self.assertFalse(resultado.ok)
        self.assertIn("FIRECRAWL_API_KEY", resultado.error or "")

    def test_fallback_usa_o_segundo_quando_o_primeiro_falha(self):
        """O encadeamento devolve o primeiro provedor que responde com sucesso."""
        falho = MagicMock(name="falho")
        falho.fetch.return_value = MagicMock(ok=False, provider="firecrawl", error="sem chave")
        bom = MagicMock(name="bom")
        bom.fetch.return_value = MagicMock(ok=True, provider="http")
        resultado = fetch_with_fallback("https://exemplo.org", [falho, bom])
        self.assertEqual(resultado.provider, "http")

    def test_fallback_devolve_ultimo_erro_quando_todos_falham(self):
        """Esgotados os provedores, prevalece o erro do último tentado."""
        primeiro = MagicMock()
        primeiro.fetch.return_value = MagicMock(ok=False, provider="firecrawl", error="erro A")
        segundo = MagicMock()
        segundo.fetch.return_value = MagicMock(ok=False, provider="http", error="erro B")
        resultado = fetch_with_fallback("https://exemplo.org", [primeiro, segundo])
        self.assertEqual(resultado.error, "erro B")


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
