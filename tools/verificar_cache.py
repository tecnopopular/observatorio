"""Confere se o cache de HTML corresponde aos hashes do manifesto de estado.

Este é o elo que sustenta a reprodutibilidade: o manifesto afirma qual conteúdo foi avaliado, e o
cache guarda esse conteúdo. Divergência entre os dois significa que o laudo não pode ser recalculado
a partir do que foi publicado, e o portão existe para que isso seja descoberto antes da publicação,
não depois.

Roda na publicação de uma release e também localmente, depois de qualquer ciclo:

    uv run python tools/verificar_cache.py
"""

import argparse
import sys
from pathlib import Path

from observatorio.state import conferir_cache, load_manifest

MANIFESTO = Path("data/estado_hashes.json")
CACHE = Path("data/raw/html_cache")


def main(argv: list[str] | None = None) -> int:
    """Confronta o manifesto versionado com os corpos guardados em cache.

    Args:
        argv: Argumentos de linha de comando; quando nulo, usa `sys.argv`.

    Returns:
        0 quando todo corpo em cache reproduz o hash registrado; 1 quando o manifesto não existe,
        quando falta corpo para algum identificador ou quando algum corpo produz outro hash.
    """
    parser = argparse.ArgumentParser(
        description="Confere se o cache de HTML corresponde aos hashes do manifesto de estado."
    )
    parser.add_argument("--manifesto", default=str(MANIFESTO), help="Manifesto de estado a conferir")
    parser.add_argument("--cache", default=str(CACHE), help="Diretório do cache de HTML")
    args = parser.parse_args(argv)

    caminho_manifesto = Path(args.manifesto)
    caminho_cache = Path(args.cache)

    if not caminho_manifesto.is_file():
        print(f"[-] Manifesto de estado não encontrado: {caminho_manifesto}", file=sys.stderr)
        return 1
    if not caminho_cache.is_dir():
        print(f"[-] Cache de HTML não encontrado: {caminho_cache}", file=sys.stderr)
        return 1

    resultado = conferir_cache(load_manifest(caminho_manifesto), caminho_cache)

    print(f"[*] Corpos conferidos: {resultado.conferidos}")
    print(f"[*] Entradas sem hash registrado, fora da conferência: {len(resultado.sem_hash)}")

    if resultado.ausentes:
        print(
            f"[-] {len(resultado.ausentes)} identificador(es) com hash e sem corpo em cache:", file=sys.stderr
        )
        for tool_id in resultado.ausentes[:10]:
            print(f"    {tool_id}", file=sys.stderr)
    if resultado.divergentes:
        print(f"[-] {len(resultado.divergentes)} corpo(s) com hash diferente do manifesto:", file=sys.stderr)
        for tool_id in resultado.divergentes[:10]:
            print(f"    {tool_id}", file=sys.stderr)

    if not resultado.integro:
        print(
            "[*] Sem cache fiel ao manifesto, o laudo não é recalculável offline: "
            "refaça o ciclo com --refresh antes de publicar.",
            file=sys.stderr,
        )
        return 1

    print("[OK] Cache íntegro: todo corpo disponível reproduz o hash registrado no manifesto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
