"""Grava `tool_id` nas entradas do catálogo que ainda não o têm.

Enquanto o identificador era derivado do nome a cada ciclo, renomear uma iniciativa publicava o
laudo seguinte em outro endereço. Este utilitário congela os identificadores em uso: os mesmos que
o último ciclo gerou e que o site publica hoje, de modo que nenhum laudo muda de endereço.

Roda uma única vez sobre o catálogo legado; entradas novas já nascem com `tool_id` gravado pelo
subcomando `adicionar`.
"""

import json
from pathlib import Path
from typing import Any

from observatorio.catalog import assign_tool_ids

CATALOG = Path("data/data_iniciativas.json")
DESIRED_ORDER = ("nome", "tool_id", "url", "dominio", "categoria", "descricao")


def reorder(entry: dict[str, Any]) -> dict[str, Any]:
    """Devolve a entrada com os campos de identificação à frente dos demais.

    Args:
        entry: Registro do catálogo com `tool_id` já gravado.

    Returns:
        Novo dicionário com `nome`, `tool_id`, `url`, `dominio`, `categoria` e `descricao` nas
        primeiras posições e os campos restantes na ordem original.
    """
    saida: dict[str, Any] = {}
    for campo in DESIRED_ORDER:
        if campo in entry:
            saida[campo] = entry[campo]
    for campo, valor in entry.items():
        if campo not in saida and campo != "_tool_id":
            saida[campo] = valor
    return saida


def main() -> int:
    """Lê o catálogo, congela os identificadores e grava o arquivo.

    Returns:
        0 quando o catálogo é regravado; 1 quando todas as entradas já têm `tool_id`.
    """
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    pendentes = [item for item in catalog if not str(item.get("tool_id") or "").strip()]
    if not pendentes:
        print("[*] Todas as entradas já têm tool_id gravado; nada a fazer.")
        return 1

    colisoes = assign_tool_ids(catalog)
    atualizado = [reorder({"tool_id": item["_tool_id"], **item}) for item in catalog]

    conteudo = json.dumps(atualizado, ensure_ascii=False, indent=2)
    CATALOG.write_text(conteudo + "\n", encoding="utf-8")

    print(f"[+] {len(pendentes)} entrada(s) passaram a ter tool_id gravado.")
    if colisoes:
        print(f"[*] {len(colisoes)} nome(s) repetido(s) mantiveram o sufixo do ciclo anterior:")
        for base, novo, url in colisoes:
            print(f"    {base} -> {novo} ({url})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
