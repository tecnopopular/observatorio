"""Consulta de `robots.txt` por host, com cache em memória.

O observatório pergunta ao site, antes de ler, se a leitura é permitida para o seu User-Agent.
A interpretação segue a RFC 9309, com uma escolha explícita: falha de rede não é proibição.

Um `robots.txt` que responde 404, 410, 401 ou 403 não contém regra de robô aplicável, então a
leitura é permitida. Um arquivo que responde 200 é interpretado. Já timeout, DNS ou 5xx não
expressam vontade do operador: tratar isso como proibição faria um host instável desaparecer do
acervo sem que ninguém tivesse dito nada, então a leitura segue e a pendência aparece onde deve,
na própria aquisição da página.
"""

import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

from .fetch import INSTITUTIONAL_USER_AGENT

LIMITE_DO_ARQUIVO = 200_000


@dataclass(frozen=True)
class RobotsDecision:
    """Decisão de leitura para um endereço, com o motivo que a sustenta.

    Attributes:
        allowed: Verdadeiro quando a leitura é permitida.
        reason: Categoria curta da decisão, usada no resumo do ciclo.
        detail: Frase legível que explica a decisão e serve ao relatório.
    """

    allowed: bool
    reason: str
    detail: str


class RobotsPolicy:
    """Aplica as regras de `robots.txt` de cada host, consultando uma vez por ciclo.

    O cache é por host porque as regras vivem no host, não na página: duas iniciativas no mesmo
    domínio não precisam de duas consultas.
    """

    def __init__(self, timeout: int = 8, user_agent: str = INSTITUTIONAL_USER_AGENT) -> None:
        """Prepara a política com tempo limite e agente a declarar.

        Args:
            timeout: Tempo limite da consulta ao `robots.txt`, em segundos.
            user_agent: Agente usado na consulta e nas regras.
        """
        self.timeout = timeout
        self.user_agent = user_agent
        self._cache: dict[str, tuple[int | None, urllib.robotparser.RobotFileParser | None, str | None]] = {}

    def _consultar(
        self, host: str
    ) -> tuple[int | None, urllib.robotparser.RobotFileParser | None, str | None]:
        """Busca e interpreta o `robots.txt` de um host, reaproveitando o cache.

        Args:
            host: Host a consultar, sem esquema nem caminho.

        Returns:
            Tripla com código HTTP, interpretador pronto (quando há arquivo válido) e erro de rede.
        """
        if host in self._cache:
            return self._cache[host]

        url = f"https://{host}/robots.txt"
        requisicao = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        codigo: int | None = None
        interpretador: urllib.robotparser.RobotFileParser | None = None
        erro: str | None = None
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                codigo = getattr(resposta, "status", 200)
                corpo = resposta.read(LIMITE_DO_ARQUIVO).decode("utf-8", errors="ignore")
                if codigo == 200 and corpo.strip():
                    interpretador = urllib.robotparser.RobotFileParser()
                    interpretador.parse(corpo.splitlines())
        except urllib.error.HTTPError as falha:
            codigo = falha.code
        except Exception as falha:
            erro = f"{type(falha).__name__}: {falha}"

        self._cache[host] = (codigo, interpretador, erro)
        return self._cache[host]

    def decidir(self, url: str) -> RobotsDecision:
        """Decide se a leitura de um endereço é permitida pelas regras do host.

        Args:
            url: Endereço público da iniciativa.

        Returns:
            Decisão com a categoria e o motivo legível.
        """
        host = urlparse(url).netloc.lower()
        codigo, interpretador, erro = self._consultar(host)

        if erro is not None:
            return RobotsDecision(True, "sem_consulta", f"robots.txt inacessível ({erro}); leitura mantida")
        if codigo in (404, 410):
            return RobotsDecision(True, "sem_arquivo", f"robots.txt respondeu {codigo}")
        if codigo in (401, 403):
            return RobotsDecision(True, "sem_regra", f"robots.txt respondeu {codigo}: sem regra aplicável")
        if codigo is not None and codigo >= 500:
            return RobotsDecision(True, "sem_consulta", f"robots.txt respondeu {codigo}; leitura mantida")
        if interpretador is None:
            return RobotsDecision(True, "sem_regra", f"robots.txt respondeu {codigo or 'sem corpo'}")

        if interpretador.can_fetch(self.user_agent, url):
            return RobotsDecision(True, "permitido", "regras do host permitem a leitura")
        if not interpretador.can_fetch("*", url):
            return RobotsDecision(False, "proibido", "robots.txt proíbe este endereço para todos os agentes")
        return RobotsDecision(False, "proibido", "robots.txt tem diretiva específica para o observatório")
