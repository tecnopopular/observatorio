"""Consulta de `robots.txt` por host, com cache em memória e casamento exato de agente.

O observatório pergunta ao site, antes de ler, se a leitura é permitida para o seu crawler. Quem
quer ser excluído precisa nomeá-lo: o nome é `CRAWLER_TOKEN`, o mesmo que vai no cabeçalho da
requisição, e o casamento é por token exato. Comparar por subcadeia transformaria
`User-agent: popular` numa regra sobre nós, bloqueando uma página que ninguém pediu para bloquear —
e faria o laudo afirmar que existe diretiva para o observatório onde só existe coincidência de
texto.

A interpretação segue a RFC 9309, com uma escolha explícita: falha de rede não é proibição. Um
`robots.txt` que responde 404, 410, 401 ou 403 não contém regra de robô aplicável, então a leitura é
permitida. Um arquivo que responde 200 é interpretado. Já timeout, DNS ou 5xx não expressam vontade
do operador: tratar isso como proibição faria um host instável desaparecer do acervo sem que ninguém
tivesse dito nada, então a leitura segue e a pendência aparece onde deve, na própria aquisição da
página.
"""

import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

from .fetch import CRAWLER_TOKEN, INSTITUTIONAL_USER_AGENT

FILE_LIMIT = 200_000

ORIGEM_TOKEN = "token"
ORIGEM_CURINGA = "curinga"
ORIGEM_AUSENTE = "ausente"


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


def _token_casa(agente: str, alvo: str) -> bool:
    """Compara um `User-agent` do arquivo com o nosso token, exigindo igualdade.

    A versão é aceita porque administrador costuma copiar o cabeçalho inteiro
    (`Token/1.0` casa com `Token`); o resto da linha não conta como nome.

    Args:
        agente: Valor do campo `User-agent` como escrito no arquivo.
        alvo: Nosso token, já em minúsculas.

    Returns:
        Verdadeiro quando o campo nomeia exatamente o nosso crawler.
    """
    normalizado = agente.strip().lower()
    return normalizado == alvo or normalizado.startswith(f"{alvo}/")


def _grupos(texto: str) -> list[tuple[list[str], list[str]]]:
    """Separa o arquivo em grupos de agente, na ordem em que aparecem.

    Linhas `User-agent` consecutivas formam um grupo; a primeira diretiva encerra a lista de nomes e
    um novo `User-agent` abre o grupo seguinte.

    Args:
        texto: Conteúdo do `robots.txt`.

    Returns:
        Lista de pares com os nomes do grupo e as diretivas que valem para eles.
    """
    grupos: list[tuple[list[str], list[str]]] = []
    agentes: list[str] = []
    diretivas: list[str] = []

    for linha in texto.splitlines():
        limpa = linha.split("#", 1)[0].strip()
        if not limpa:
            continue
        campo, _, valor = limpa.partition(":")
        campo = campo.strip().lower()
        valor = valor.strip()

        if campo == "user-agent":
            if diretivas:
                grupos.append((agentes, diretivas))
                agentes, diretivas = [], []
            agentes.append(valor)
        elif agentes:
            diretivas.append(limpa)

    if agentes:
        grupos.append((agentes, diretivas))
    return grupos


def _montar(token: str, blocos: list[list[str]]) -> str:
    """Reduz os grupos selecionados a um único grupo do nosso token.

    Args:
        token: Token do nosso crawler.
        blocos: Diretivas dos grupos aplicáveis, já na ordem original.

    Returns:
        Texto de `robots.txt` com um só grupo, o do nosso token.
    """
    linhas = [f"User-agent: {token}"]
    for diretivas in blocos:
        linhas.extend(diretivas)
    return "\n".join(linhas)


def select_rules(texto: str, token: str = CRAWLER_TOKEN) -> tuple[str, str]:
    """Seleciona as regras aplicáveis a um token de agente.

    Grupo nomeado para nós vence o curinga, como manda a RFC 9309: quem escreveu o nosso nome disse
    algo mais específico do que "todos os robôs".

    Args:
        texto: Conteúdo do `robots.txt`.
        token: Token do nosso crawler.

    Returns:
        Par com o texto reduzido ao grupo aplicável e a origem da seleção: `token` quando há grupo
        nomeado para nós, `curinga` quando só existe `*`, `ausente` quando não há nenhum dos dois.
    """
    alvo = token.lower()
    grupos = _grupos(texto)

    nossos = [diretivas for agentes, diretivas in grupos if any(_token_casa(a, alvo) for a in agentes)]
    if nossos:
        return _montar(token, nossos), ORIGEM_TOKEN

    curingas = [
        diretivas for agentes, diretivas in grupos if any(agente.strip() == "*" for agente in agentes)
    ]
    if curingas:
        return _montar(token, curingas), ORIGEM_CURINGA

    return "", ORIGEM_AUSENTE


class RobotsPolicy:
    """Aplica as regras de `robots.txt` de cada host, consultando uma vez por ciclo.

    O cache é por host porque as regras vivem no host, não na página: duas iniciativas no mesmo
    domínio não precisam de duas consultas.
    """

    def __init__(
        self,
        timeout: int = 8,
        user_agent: str = INSTITUTIONAL_USER_AGENT,
        token: str = CRAWLER_TOKEN,
    ) -> None:
        """Prepara a política com tempo limite e o nome pelo qual o crawler atende.

        Args:
            timeout: Tempo limite da consulta ao `robots.txt`, em segundos.
            user_agent: Agente usado no cabeçalho da consulta.
            token: Token pelo qual o crawler atende nas regras do arquivo.
        """
        self.timeout = timeout
        self.user_agent = user_agent
        self.token = token
        self._cache: dict[
            str, tuple[int | None, urllib.robotparser.RobotFileParser | None, str | None, str]
        ] = {}

    def _consultar(
        self, host: str
    ) -> tuple[int | None, urllib.robotparser.RobotFileParser | None, str | None, str]:
        """Busca e interpreta o `robots.txt` de um host, reaproveitando o cache.

        Args:
            host: Host a consultar, sem esquema nem caminho.

        Returns:
            Quádrupla com código HTTP, interpretador das regras aplicáveis (quando há arquivo
            válido), erro de rede e origem da seleção do grupo.
        """
        if host in self._cache:
            return self._cache[host]

        url = f"https://{host}/robots.txt"
        requisicao = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        codigo: int | None = None
        interpretador: urllib.robotparser.RobotFileParser | None = None
        erro: str | None = None
        origem = ORIGEM_AUSENTE
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as response:
                codigo = getattr(response, "status", 200)
                body = response.read(FILE_LIMIT).decode("utf-8", errors="ignore")
                if codigo == 200 and body.strip():
                    aplicavel, origem = select_rules(body, self.token)
                    if aplicavel:
                        interpretador = urllib.robotparser.RobotFileParser()
                        interpretador.parse(aplicavel.splitlines())
        except urllib.error.HTTPError as falha:
            codigo = falha.code
        except Exception as falha:
            erro = f"{type(falha).__name__}: {falha}"

        self._cache[host] = (codigo, interpretador, erro, origem)
        return self._cache[host]

    def decide(self, url: str) -> RobotsDecision:
        """Decide se a leitura de um endereço é permitida pelas regras do host.

        Args:
            url: Endereço público da iniciativa.

        Returns:
            Decisão com a categoria e o motivo legível.
        """
        host = urlparse(url).netloc.lower()
        codigo, interpretador, erro, origem = self._consultar(host)

        if erro is not None:
            return RobotsDecision(True, "sem_consulta", f"robots.txt inacessível ({erro}); leitura mantida")
        if codigo in (404, 410):
            return RobotsDecision(True, "sem_arquivo", f"robots.txt respondeu {codigo}")
        if codigo in (401, 403):
            return RobotsDecision(True, "sem_regra", f"robots.txt respondeu {codigo}: sem regra aplicável")
        if codigo is not None and codigo >= 500:
            return RobotsDecision(True, "sem_consulta", f"robots.txt respondeu {codigo}; leitura mantida")
        if interpretador is None:
            if origem == ORIGEM_AUSENTE and codigo == 200:
                return RobotsDecision(
                    True,
                    "sem_regra",
                    f"robots.txt não nomeia {self.token} nem todos os agentes",
                )
            return RobotsDecision(True, "sem_regra", f"robots.txt respondeu {codigo or 'sem corpo'}")

        permitido = interpretador.can_fetch(self.token, url)
        if origem == ORIGEM_TOKEN:
            if permitido:
                return RobotsDecision(True, "permitido", f"robots.txt tem grupo para {self.token} e permite")
            return RobotsDecision(False, "proibido", f"robots.txt tem grupo para {self.token} e proíbe")
        if permitido:
            return RobotsDecision(True, "permitido", "regras do host permitem a leitura")
        return RobotsDecision(False, "proibido", "robots.txt proíbe este endereço para todos os agentes")
