"""Mede quantas iniciativas do catálogo seriam bloqueadas se o motor consultasse robots.txt.

Para cada host presente no catálogo, busca `robots.txt` uma única vez, aplica as diretivas ao
User-Agent institucional e classifica a situação de cada iniciativa. Não altera o motor nem o
laudo: serve para decidir, com número, se a consulta deve existir.

Uso:
    uv run python tools/measure_robots.py                 # catálogo em data/data_iniciativas.json
    uv run python tools/measure_robots.py --catalogo x.json --saida y.json

A classificação segue a RFC 9309, que é o que os rastreadores aplicam na prática:

    - `robots.txt` ausente (404, 410) significa permissão total;
    - 401 e 403 significam permissão total, porque "não autorizado" não é regra de robô;
    - 5xx e falha de rede significam bloqueio, por precaução;
    - 200 é lido e as regras são aplicadas ao nosso User-Agent.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import urllib.robotparser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from observatorio.fetch import INSTITUTIONAL_USER_AGENT  # noqa: E402

DEFAULT_CATALOG = RAIZ / "data" / "data_iniciativas.json"
DEFAULT_OUTPUT = Path("/tmp/robots.json")


def fetch_robots(host: str, timeout: int) -> tuple[int | None, str | None, str | None]:
    """Busca o `robots.txt` de um host.

    Args:
        host: Nome do host, sem esquema nem caminho.
        timeout: Tempo limite da requisição, em segundos.

    Returns:
        Tripla com código HTTP, corpo do arquivo e mensagem de erro. Corpo e erro são mutuamente
        exclusivos; quando o arquivo não existe, o código vem preenchido e o corpo vem None.
    """
    url = f"https://{host}/robots.txt"
    requisicao = urllib.request.Request(url, headers={"User-Agent": INSTITUTIONAL_USER_AGENT})
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as response:
            body = response.read(200_000).decode("utf-8", errors="ignore")
            return getattr(response, "status", 200), body, None
    except urllib.error.HTTPError as erro:
        return erro.code, None, None
    except Exception as erro:
        return None, None, f"{type(erro).__name__}: {erro}"


def interpret(codigo: int | None, body: str | None, erro: str | None, url: str) -> tuple[str, str]:
    """Classifica a leitura de uma página à luz do `robots.txt` do host.

    Args:
        codigo: Código HTTP da resposta ao `robots.txt`, ou None em falha de rede.
        body: Conteúdo do arquivo, quando existir.
        erro: Mensagem de falha de rede, quando houver.
        url: Endereço da iniciativa que seria lido.

    Returns:
        Par com a categoria da situação e o detalhe legível que a justifica.
    """
    if erro is not None:
        return "bloqueado_por_falha", erro
    if codigo in (401, 403):
        return "permitido_sem_regras", f"robots.txt respondeu {codigo}: sem regras aplicáveis"
    if codigo in (404, 410):
        return "permitido_sem_regras", f"robots.txt respondeu {codigo}: não existe"
    if codigo is not None and codigo >= 500:
        return "bloqueado_por_falha", f"robots.txt respondeu {codigo}: falha do servidor"
    if codigo != 200 or not body:
        return "permitido_sem_regras", f"robots.txt respondeu {codigo or 'sem corpo'}: sem regras"

    interpretador = urllib.robotparser.RobotFileParser()
    interpretador.parse(body.splitlines())
    if interpretador.can_fetch(INSTITUTIONAL_USER_AGENT, url):
        return "permitido", "regras aplicadas ao User-Agent institucional permitem a leitura"

    regra_generica = urllib.robotparser.RobotFileParser()
    regra_generica.parse(body.splitlines())
    if not regra_generica.can_fetch("*", url):
        return "bloqueado_por_regra", "regras do robots.txt proíbem este endereço para todos os agentes"
    return "bloqueado_por_agente", "robots.txt tem diretiva específica que atinge o observatório"


def measure(catalog: list[dict], timeout: int, workers: int) -> dict[str, dict]:
    """Executa a medição sobre todos os hosts distintos do catálogo.

    Args:
        catalog: Registros do catálogo, com `nome` e `url`.
        timeout: Tempo limite por requisição, em segundos.
        workers: Número de threads simultâneas.

    Returns:
        Dicionário de host para o resultado da consulta e os registros que ele atende.
    """
    por_host: dict[str, list[dict]] = {}
    for item in catalog:
        host = urlparse(item["url"]).netloc.lower()
        por_host.setdefault(host, []).append(item)

    def processar(host: str) -> tuple[str, dict]:
        """Consulta um host e classifica cada iniciativa que ele atende.

        Args:
            host: Nome do host a consultar.

        Returns:
            Par com o host e o resultado da consulta, incluindo as iniciativas classificadas.
        """
        codigo, body, erro = fetch_robots(host, timeout)
        resultados = []
        for item in por_host[host]:
            categoria, detalhe = interpret(codigo, body, erro, item["url"])
            resultados.append(
                {
                    "nome": item["nome"],
                    "url": item["url"],
                    "categoria": categoria,
                    "detalhe": detalhe,
                }
            )
        return host, {"http": codigo, "erro": erro, "iniciativas": resultados}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(processar, sorted(por_host)))


def summarize(medicao: dict[str, dict]) -> int:
    """Imprime o resumo da medição e devolve o código de saída.

    Args:
        medicao: Resultado por host devolvido por `medir`.

    Returns:
        0 quando a medição termina, independentemente do resultado.
    """
    iniciativas = [i for host in medicao.values() for i in host["iniciativas"]]
    contagem = Counter(i["categoria"] for i in iniciativas)

    print(f"hosts consultados: {len(medicao)} | iniciativas: {len(iniciativas)}\n")
    for categoria, total in contagem.most_common():
        print(f"  {categoria:<24} {total:>4}")

    bloqueadas = [i for i in iniciativas if i["categoria"].startswith("bloqueado")]
    sem_arquivo = sum(1 for h in medicao.values() if h["http"] in (404, 410, 401, 403))
    com_arquivo = sum(1 for h in medicao.values() if h["http"] == 200)
    falhas = sum(1 for h in medicao.values() if h["erro"] or (h["http"] or 0) >= 500)

    print(f"\nhosts com robots.txt publicado: {com_arquivo}")
    print(f"hosts sem robots.txt utilizável: {sem_arquivo}")
    print(f"hosts com falha na consulta: {falhas}")

    if bloqueadas:
        print(f"\niniciativas que sairiam da auditoria ({len(bloqueadas)}):")
        for item in sorted(bloqueadas, key=lambda x: x["nome"]):
            print(f"  {item['nome'][:34]:<36} {item['categoria']:<20} {item['detalhe'][:44]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Executa a medição e grava o resultado detalhado em JSON.

    Args:
        argv: Argumentos de linha de comando; quando nulo, usa `sys.argv[1:]`.

    Returns:
        Código de saída do processo.
    """
    parser = argparse.ArgumentParser(description="Mede o efeito de consultar robots.txt.")
    parser.add_argument("--catalogo", default=str(DEFAULT_CATALOG), help="catálogo de iniciativas")
    parser.add_argument("--saida", default=str(DEFAULT_OUTPUT), help="arquivo JSON com o detalhe")
    parser.add_argument("--timeout", type=int, default=10, help="tempo limite por requisição")
    parser.add_argument("--workers", type=int, default=12, help="consultas simultâneas")
    args = parser.parse_args(argv)

    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    medicao = measure(catalog, args.timeout, args.workers)
    Path(args.saida).write_text(json.dumps(medicao, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"detalhe por host gravado em {args.saida}\n")
    return summarize(medicao)


if __name__ == "__main__":
    sys.exit(main())
