"""Testes da consulta de `robots.txt`: interpretação das regras e efeito na esteira.

Cobrem a decisão de política adotada pelo observatório: regra explícita de proibição impede a
leitura, enquanto ausência de arquivo, 401/403 e falha de rede não impedem.
"""

import unittest
from unittest.mock import MagicMock, patch

from observatorio.fetch import FetchResult
from observatorio.robots import RobotsPolicy


def _politica_com(status, body="", erro=None):
    """Monta uma política cujo host responde sempre o mesmo `robots.txt`.

    Args:
        status: Código HTTP devolvido pela consulta.
        body: Conteúdo do arquivo, quando houver.
        erro: Exceção a levantar no lugar da resposta.

    Returns:
        Instância de `RobotsPolicy` com a resposta simulada.
    """
    politica = RobotsPolicy(timeout=1)

    class _Resposta:
        def __init__(self):
            """Configura a resposta simulada com código e corpo."""
            self.status = status
            self._corpo = body

        def read(self, n=-1):
            """Devolve o corpo configurado em bytes.

            Args:
                n: Quantidade de bytes solicitada, ignorada.

            Returns:
                Conteúdo do arquivo em bytes.
            """
            return self._corpo.encode("utf-8")

        def __enter__(self):
            """Entra no contexto da resposta simulada.

            Returns:
                A própria resposta.
            """
            return self

        def __exit__(self, *a):
            """Encerra o contexto sem suprimir exceções.

            Args:
                *a: Argumentos padrão de contexto.

            Returns:
                Falso.
            """
            return False

    if erro is not None:
        politica._consultar = MagicMock(return_value=(None, None, erro))
    else:
        politica._consultar = MagicMock(return_value=(status, _montar_parser(body, status), None))
    return politica


def _montar_parser(body, status):
    """Interpreta o corpo quando a resposta é 200 com conteúdo.

    Args:
        body: Conteúdo do `robots.txt`.
        status: Código HTTP da resposta.

    Returns:
        Interpretador pronto, ou None quando não há arquivo válido.
    """
    if status != 200 or not body.strip():
        return None
    import urllib.robotparser

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    return parser


class TestDecisaoDeRobots(unittest.TestCase):
    """Verifica a decisão de leitura para cada tipo de resposta do host."""

    def test_disallow_total_proibe(self):
        """`Disallow: /` deve impedir a leitura."""
        politica = _politica_com(200, "User-agent: *\nDisallow: /\n")
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertFalse(decisao.allowed)
        self.assertEqual(decisao.reason, "proibido")

    def test_disallow_parcial_permite(self):
        """Regra que atinge apenas outro caminho não impede a leitura da página."""
        politica = _politica_com(200, "User-agent: *\nDisallow: /admin\n")
        self.assertTrue(politica.decide("https://exemplo.org/painel").allowed)

    def test_diretiva_para_outro_agente_permite(self):
        """Regra endereçada a outro robô não se aplica ao observatório."""
        politica = _politica_com(200, "User-agent: OutroRobo\nDisallow: /\n")
        self.assertTrue(politica.decide("https://exemplo.org/pagina").allowed)

    def test_arquivo_ausente_permite(self):
        """404 significa que não há regra a aplicar."""
        politica = _politica_com(404)
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertTrue(decisao.allowed)
        self.assertEqual(decisao.reason, "sem_arquivo")

    def test_403_no_arquivo_permite(self):
        """403 no `robots.txt` não é proibição de leitura declarada."""
        self.assertTrue(_politica_com(403).decide("https://exemplo.org/pagina").allowed)

    def test_falha_de_rede_nao_vira_proibicao(self):
        """Timeout na consulta não pode fazer o site desaparecer do acervo."""
        politica = _politica_com(None, erro="TimeoutError: timed out")
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertTrue(decisao.allowed)
        self.assertEqual(decisao.reason, "sem_consulta")

    def test_erro_5xx_nao_vira_proibicao(self):
        """Falha do servidor ao servir `robots.txt` não é regra do operador."""
        self.assertTrue(_politica_com(503).decide("https://exemplo.org/pagina").allowed)

    def test_consulta_uma_vez_por_host(self):
        """Duas páginas no mesmo host devem gerar uma única consulta de rede."""
        politica = RobotsPolicy(timeout=1)

        class _Resposta:
            status = 200

            def read(self, n=-1):
                """Devolve regras que proíbem todo o site.

                Args:
                    n: Quantidade de bytes solicitada, ignorada.

                Returns:
                    Conteúdo do arquivo em bytes.
                """
                return b"User-agent: *\nDisallow: /\n"

            def __enter__(self):
                """Entra no contexto da resposta simulada.

                Returns:
                    A própria resposta.
                """
                return self

            def __exit__(self, *a):
                """Encerra o contexto sem suprimir exceções.

                Args:
                    *a: Argumentos padrão de contexto.

                Returns:
                    Falso.
                """
                return False

        with patch("urllib.request.urlopen", return_value=_Resposta()) as urlopen:
            politica.decide("https://exemplo.org/a")
            politica.decide("https://exemplo.org/b")
            politica.decide("https://exemplo.org/c")
        self.assertEqual(urlopen.call_count, 1)


class TestEsteiraRespeitaRobots(unittest.TestCase):
    """Verifica o efeito da proibição no ciclo completo."""

    def test_site_proibido_nao_gera_laudo(self):
        """Iniciativa proibida deve sair do laudo e não aparecer no manifesto."""
        import json
        import tempfile
        from pathlib import Path

        from observatorio.engine import run_batch_audit

        catalog = [
            {"nome": "Proibido", "url": "https://proibido.org"},
            {"nome": "Livre", "url": "https://livre.org"},
        ]
        politica = RobotsPolicy(timeout=1)
        politica._consultar = MagicMock(
            side_effect=lambda host: (
                (200, _montar_parser("User-agent: *\nDisallow: /\n", 200), None)
                if host == "proibido.org"
                else (404, None, None)
            )
        )
        provedor = MagicMock()
        provedor.fetch.return_value = FetchResult(
            url="https://livre.org",
            provider="http",
            html="<html><body><p>Conteúdo público.</p></body></html>",
            status_code=200,
            latency_ms=120,
            content_bytes=60,
            declared_bytes=60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            entry = tmp / "cat.json"
            entry.write_text(json.dumps(catalog), encoding="utf-8")
            laudo = tmp / "laudo.json"
            manifesto = tmp / "estado.json"
            relatorios = run_batch_audit(
                input_file=entry,
                output_file=laudo,
                manifest_file=manifesto,
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=provedor,
                robots_policy=politica,
            )
            gravados = json.loads(laudo.read_text(encoding="utf-8"))
            current_state = json.loads(manifesto.read_text(encoding="utf-8"))

        self.assertEqual([r.tool_id for r in relatorios], ["livre"])
        self.assertEqual([r["tool_id"] for r in gravados], ["livre"])
        self.assertEqual([e["tool_id"] for e in current_state["entries"]], ["livre"])
        provedor.fetch.assert_called_once()

    def test_sem_politica_nada_e_pulado(self):
        """Sem consulta de `robots.txt`, todas as iniciativas são auditadas."""
        import json
        import tempfile
        from pathlib import Path

        from observatorio.engine import run_batch_audit

        catalog = [{"nome": "Livre", "url": "https://livre.org"}]
        provedor = MagicMock()
        provedor.fetch.return_value = FetchResult(
            url="https://livre.org",
            provider="http",
            html="<html><body><p>Conteúdo público.</p></body></html>",
            status_code=200,
            latency_ms=120,
            content_bytes=60,
            declared_bytes=60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            entry = tmp / "cat.json"
            entry.write_text(json.dumps(catalog), encoding="utf-8")
            relatorios = run_batch_audit(
                input_file=entry,
                output_file=tmp / "laudo.json",
                manifest_file=tmp / "estado.json",
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=provedor,
                respect_robots=False,
            )
        self.assertEqual(len(relatorios), 1)


if __name__ == "__main__":
    unittest.main()
