"""Testes da consulta de `robots.txt`: casamento de agente, regras e efeito na esteira.

Cobrem a decisão de política adotada pelo observatório (regra explícita impede a leitura; ausência
de arquivo, 401/403 e falha de rede não impedem) e a regra de identificação: o crawler atende por um
token exato, e só um grupo que nomeie esse token pode falar por ele. A consulta é dublada no
transporte, nunca em `_consultar`, para que a seleção de grupo seja exercitada de verdade.
"""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from observatorio.engine import run_batch_audit
from observatorio.fetch import CRAWLER_TOKEN, FetchResult
from observatorio.robots import RobotsPolicy


class RespostaSimulada:
    """Resposta simulada de `robots.txt`, usada como gerenciador de contexto."""

    def __init__(self, status: int, body: str) -> None:
        """Guarda código e corpo da resposta simulada.

        Args:
            status: Código HTTP a devolver.
            body: Conteúdo do arquivo.
        """
        self.status = status
        self._corpo = body

    def read(self, n: int = -1) -> bytes:
        """Devolve o corpo configurado em bytes.

        Args:
            n: Quantidade de bytes solicitada, ignorada.

        Returns:
            Conteúdo do arquivo em bytes.
        """
        return self._corpo.encode("utf-8")

    def __enter__(self) -> RespostaSimulada:
        """Entra no contexto da resposta simulada.

        Returns:
            A própria resposta.
        """
        return self

    def __exit__(self, *a: object) -> bool:
        """Encerra o contexto sem suprimir exceções.

        Args:
            *a: Argumentos padrão de contexto.

        Returns:
            Falso.
        """
        return False


def _politica_com(status, body="", erro=None):
    """Monta uma política cujo host responde sempre o mesmo `robots.txt`.

    Args:
        status: Código HTTP devolvido pela consulta.
        body: Conteúdo do arquivo, quando houver.
        erro: Exceção a levantar no lugar da resposta.

    Returns:
        Instância de `RobotsPolicy` com o transporte simulado e a decisão já consultada.
    """
    politica = RobotsPolicy(timeout=1)
    alvo = "https://exemplo.org/pagina"

    if erro is not None:
        with patch("urllib.request.urlopen", side_effect=erro):
            politica.decide(alvo)
        return politica

    if status is not None and status >= 400:
        falha = urllib.error.HTTPError("https://exemplo.org/robots.txt", status, "erro", {}, None)
        with patch("urllib.request.urlopen", side_effect=falha):
            politica.decide(alvo)
        return politica

    with patch("urllib.request.urlopen", return_value=RespostaSimulada(status or 200, body)):
        politica.decide(alvo)
    return politica


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
        politica = _politica_com(None, erro=TimeoutError("tempo esgotado"))
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertTrue(decisao.allowed)
        self.assertEqual(decisao.reason, "sem_consulta")

    def test_erro_5xx_nao_vira_proibicao(self):
        """Falha do servidor ao servir `robots.txt` não é regra do operador."""
        self.assertTrue(_politica_com(503).decide("https://exemplo.org/pagina").allowed)

    def test_consulta_uma_vez_por_host(self):
        """Duas páginas no mesmo host devem gerar uma única consulta de rede."""
        politica = RobotsPolicy(timeout=1)
        resposta = RespostaSimulada(200, "User-agent: *\nDisallow: /\n")
        with patch("urllib.request.urlopen", return_value=resposta) as urlopen:
            politica.decide("https://exemplo.org/a")
            politica.decide("https://exemplo.org/b")
            politica.decide("https://exemplo.org/c")
        self.assertEqual(urlopen.call_count, 1)


class TestCasamentoDeAgente(unittest.TestCase):
    """O crawler atende por um token exato, nunca por coincidência de texto."""

    def test_grupo_com_o_nosso_token_bloqueia(self):
        """Nomear o crawler é o caminho para bloquear só ele."""
        politica = _politica_com(200, f"User-agent: {CRAWLER_TOKEN}\nDisallow: /\n")
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertFalse(decisao.allowed)
        self.assertIn(CRAWLER_TOKEN, decisao.detail)

    def test_token_com_versao_tambem_bloqueia(self):
        """Administrador copia o cabeçalho inteiro; a versão não descaracteriza o nome."""
        politica = _politica_com(200, f"User-agent: {CRAWLER_TOKEN}/1.0\nDisallow: /\n")
        self.assertFalse(politica.decide("https://exemplo.org/pagina").allowed)

    def test_token_sem_caixa_bloqueia(self):
        """O nome não distingue maiúsculas."""
        politica = _politica_com(200, f"User-agent: {CRAWLER_TOKEN.lower()}\nDisallow: /\n")
        self.assertFalse(politica.decide("https://exemplo.org/pagina").allowed)

    def test_subcadeia_do_token_nao_bloqueia(self):
        """`popular` está contido no nosso nome e mesmo assim não é uma regra sobre nós."""
        politica = _politica_com(200, "User-agent: popular\nDisallow: /\n")
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertTrue(decisao.allowed)
        self.assertEqual(decisao.reason, "sem_regra")

    def test_nome_institucional_abreviado_nao_bloqueia(self):
        """Só o token declarado bloqueia; apelido não é heurística que se aceite."""
        politica = _politica_com(200, "User-agent: TecnoPopular\nDisallow: /\n")
        self.assertTrue(politica.decide("https://exemplo.org/pagina").allowed)

    def test_grupo_do_token_vence_o_curinga(self):
        """Quem escreveu o nosso nome disse algo mais específico do que "todos"."""
        politica = _politica_com(
            200, f"User-agent: *\nDisallow: /\n\nUser-agent: {CRAWLER_TOKEN}\nAllow: /\n"
        )
        self.assertTrue(politica.decide("https://exemplo.org/pagina").allowed)

    def test_grupo_do_token_bloqueia_onde_o_curinga_permite(self):
        """Proibição nominal não é anulada pela permissão geral."""
        politica = _politica_com(
            200, f"User-agent: *\nAllow: /\n\nUser-agent: {CRAWLER_TOKEN}\nDisallow: /\n"
        )
        self.assertFalse(politica.decide("https://exemplo.org/pagina").allowed)

    def test_arquivo_sem_grupo_aplicavel_permite(self):
        """Sem grupo nosso e sem curinga, nada foi dito sobre a leitura."""
        politica = _politica_com(200, "User-agent: OutroRobo\nDisallow: /\n")
        decisao = politica.decide("https://exemplo.org/pagina")
        self.assertTrue(decisao.allowed)
        self.assertEqual(decisao.reason, "sem_regra")
        self.assertIn("não nomeia", decisao.detail)


class TestEsteiraRespeitaRobots(unittest.TestCase):
    """Verifica o efeito da proibição no ciclo completo."""

    def test_site_proibido_nao_gera_laudo(self):
        """Iniciativa proibida deve sair do laudo e não aparecer no manifesto."""

        def _resposta(request, timeout=None):
            """Serve `robots.txt` diferente por host.

            Args:
                request: Requisição montada pela política.
                timeout: Tempo limite, ignorado.

            Returns:
                Resposta simulada com as regras do host.
            """
            if "proibido.org" in request.full_url:
                return RespostaSimulada(200, "User-agent: *\nDisallow: /\n")
            raise urllib.error.HTTPError(request.full_url, 404, "sem arquivo", {}, None)

        catalog = [
            {"nome": "Proibido", "url": "https://proibido.org"},
            {"nome": "Livre", "url": "https://livre.org"},
        ]
        politica = RobotsPolicy(timeout=1)
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
        with (
            patch("urllib.request.urlopen", side_effect=_resposta),
            tempfile.TemporaryDirectory() as tmp,
        ):
            caminho = Path(tmp)
            entry = caminho / "cat.json"
            entry.write_text(json.dumps(catalog), encoding="utf-8")
            laudo = caminho / "laudo.json"
            manifesto = caminho / "estado.json"
            relatorios = run_batch_audit(
                input_file=entry,
                output_file=laudo,
                manifest_file=manifesto,
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=provedor,
                robots_policy=politica,
                cache_dir=caminho / "cache",
            )
            gravados = json.loads(laudo.read_text(encoding="utf-8"))
            current_state = json.loads(manifesto.read_text(encoding="utf-8"))

        self.assertEqual([r.tool_id for r in relatorios], ["livre"])
        self.assertEqual([r["tool_id"] for r in gravados], ["livre"])
        self.assertEqual([e["tool_id"] for e in current_state["entries"]], ["livre"])
        provedor.fetch.assert_called_once()

    def test_sem_politica_nada_e_pulado(self):
        """Sem consulta de `robots.txt`, todas as iniciativas são auditadas."""
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
                cache_dir=tmp / "cache",
            )
        self.assertEqual(len(relatorios), 1)


if __name__ == "__main__":
    unittest.main()
