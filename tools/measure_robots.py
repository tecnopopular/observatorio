"""Mede quantas iniciativas do catálogo seriam bloqueadas pela consulta de `robots.txt`.

Para cada host presente no catálogo, a política real do motor é aplicada uma única vez e a situação
de cada iniciativa é registrada. Não altera o motor nem o laudo: serve para medir, com número, o
efeito da consulta sobre o acervo.

Uso:
    uv run python tools/measure_robots.py                 # catálogo em data/data_iniciativas.json
    uv run python tools/measure_robots.py --catalogo x.json --saida y.json

A ferramenta **não** reimplementa a decisão: ela importa `RobotsPolicy`, a mesma que o ciclo usa.
Uma segunda cópia das regras divergiria da primeira no dia em que a política mudasse, e passaria a
publicar números confiantes sobre um comportamento que o motor não tem.
"""

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from observatorio.robots import RobotsPolicy  # noqa: E402

DEFAULT_CATALOG = RAIZ / "data" / "data_iniciativas.json"
DEFAULT_OUTPUT = Path("/tmp/robots.json")


def measure(catalog: list[dict], timeout: int, workers: int) -> dict[str, dict]:
    """Executa a medição sobre todos os hosts distintos do catálogo.

    Args:
        catalog: Registros do catálogo, com `nome` e `url`.
        timeout: Tempo limite por requisição, em segundos.
        workers: Número de threads simultâneas.

    Returns:
        Dicionário de host para as iniciativas que ele atende e a decisão de cada uma.
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
            Par com o host e as decisões das iniciativas atendidas por ele.
        """
        politica = RobotsPolicy(timeout=timeout)
        resultados = []
        for item in por_host[host]:
            decisao = politica.decide(item["url"])
            resultados.append(
                {
                    "nome": item["nome"],
                    "url": item["url"],
                    "permitido": decisao.allowed,
                    "motivo": decisao.reason,
                    "detalhe": decisao.detail,
                }
            )
        return host, {"iniciativas": resultados}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(processar, sorted(por_host)))


def summarize(medicao: dict[str, dict]) -> int:
    """Imprime o resumo da medição.

    Args:
        medicao: Resultado por host devolvido por `measure`.

    Returns:
        0 quando a medição termina, independentemente do resultado.
    """
    iniciativas = [i for host in medicao.values() for i in host["iniciativas"]]
    contagem = Counter(i["motivo"] for i in iniciativas)

    print(f"hosts consultados: {len(medicao)} | iniciativas: {len(iniciativas)}\n")
    for motivo, total in contagem.most_common():
        print(f"  {motivo:<16} {total:>4}")

    bloqueadas = [i for i in iniciativas if not i["permitido"]]
    print(f"\niniciativas que não recebem laudo: {len(bloqueadas)}")
    for item in sorted(bloqueadas, key=lambda x: x["nome"]):
        print(f"  {item['nome'][:34]:<36} {item['motivo']:<10} {item['detalhe'][:52]}")
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

    catalog = json.loads(Path(args.catalogo).read_text(encoding="utf-8"))
    medicao = measure(catalog, args.timeout, args.workers)
    Path(args.saida).write_text(json.dumps(medicao, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"detalhe por host gravado em {args.saida}\n")
    return summarize(medicao)


if __name__ == "__main__":
    sys.exit(main())
