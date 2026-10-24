"""Compara dois ciclos de auditoria e mostra o que mudou de status.

Uso:
    uv run python tools/compare_cycles.py                    # anterior automático vs dist atual
    uv run python tools/compare_cycles.py --anterior laudo.json
    uv run python tools/compare_cycles.py --revisao HEAD~1

O ciclo anterior vem, nesta ordem: do arquivo indicado, do manifesto versionado no git
(`data/estado_hashes.json`) ou de um laudo completo no git. O atual sai de
`dist/auditoria_iniciativas.json`. Nada é simulado: quando não há artefato, o script falha
dizendo o que fazer, em vez de inventar um ciclo.
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MANIFEST_IN_GIT = "data/estado_hashes.json"
LEGACY_REPORT_IN_GIT = "data/auditoria_iniciativas.json"
CURRENT_REPORT = RAIZ / "dist" / "auditoria_iniciativas.json"


def _git_show(path: str, revisao: str) -> str | None:
    """Lê um arquivo do histórico do git na revisão indicada.

    Args:
        path: Caminho do arquivo, relativo à raiz do repositório.
        revisao: Referência do git usada na leitura, como `HEAD` ou `HEAD~1`.

    Returns:
        Conteúdo do arquivo, ou None quando o caminho não existe na revisão.
    """
    resultado = subprocess.run(
        ["git", "show", f"{revisao}:{path}"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0 or not resultado.stdout.strip():
        return None
    return resultado.stdout


def normalize(dados) -> dict[str, dict]:
    """Converte laudo completo ou manifesto em um índice por identificador.

    Args:
        dados: Lista de registros do laudo completo, ou dicionário do manifesto,
            que traz os registros na chave `entries`.

    Returns:
        Dicionário que mapeia `tool_id` para status, pontuação total e hash do HTML.
    """
    itens = dados.get("entries", []) if isinstance(dados, dict) else dados
    current_state: dict[str, dict] = {}
    for r in itens:
        status = r.get("status")
        current_state[r["tool_id"]] = {
            "status": status.value if hasattr(status, "value") else status,
            "total_score": r.get("total_score"),
            "html_sha256": r.get("html_sha256"),
        }
    return current_state


def load_previous(path: str | None, revisao: str) -> tuple[dict[str, dict], str]:
    """Obtém o estado do ciclo anterior e descreve a origem dos dados.

    A busca respeita a ordem arquivo informado, manifesto versionado e laudo
    completo versionado. O laudo completo é consultado no caminho usado antes da
    separação entre insumo e laudo, o que permite comparar com revisões antigas.

    Args:
        path: Arquivo informado em `--anterior`, ou None para consultar o git.
        revisao: Revisão do git usada como referência do ciclo anterior.

    Returns:
        Par formado pelo índice de estados e pela descrição legível da origem.

    Raises:
        SystemExit: Quando o arquivo informado não existe ou quando nenhum
            artefato de estado anterior existe na revisão indicada.
    """
    if path:
        cache_file = Path(path)
        if not cache_file.exists():
            sys.exit(f"arquivo anterior não encontrado: {cache_file}")
        return normalize(json.loads(cache_file.read_text(encoding="utf-8"))), str(cache_file)

    text = _git_show(MANIFEST_IN_GIT, revisao)
    if text:
        return normalize(json.loads(text)), f"git {revisao}:{MANIFEST_IN_GIT}"

    text = _git_show(LEGACY_REPORT_IN_GIT, revisao)
    if text:
        return normalize(json.loads(text)), f"git {revisao}:{LEGACY_REPORT_IN_GIT}"

    sys.exit(
        f"não há estado anterior em {revisao}: nem {MANIFEST_IN_GIT} nem {LEGACY_REPORT_IN_GIT}.\n"
        f"Informe um arquivo com --anterior."
    )


def score_text(valor: float | None) -> str:
    """Formata uma pontuação para exibição nas tabelas do relatório.

    Args:
        valor: Pontuação total, ou None quando a iniciativa não foi avaliada.

    Returns:
        Pontuação com uma casa decimal, ou `n/d` quando o valor é nulo.
    """
    return f"{valor:.1f}" if valor is not None else "n/d"


def main() -> None:
    """Executa a comparação e imprime o relatório no terminal.

    Raises:
        SystemExit: Quando falta o estado anterior, quando falta o laudo do ciclo
            atual ou quando o arquivo apontado por `--atual` não existe.
    """
    parser = argparse.ArgumentParser(description="Compara dois ciclos de auditoria.")
    parser.add_argument("--anterior", help=f"laudo ou manifesto anterior (default: {MANIFEST_IN_GIT} no git)")
    parser.add_argument("--revisao", default="HEAD", help="revisão do git usada como anterior")
    parser.add_argument("--atual", default=str(CURRENT_REPORT), help="laudo do ciclo atual")
    args = parser.parse_args()

    antes, origem = load_previous(args.anterior, args.revisao)

    atual_path = Path(args.atual)
    if not atual_path.exists():
        sys.exit(
            f"laudo atual não encontrado: {atual_path}\n"
            f"Gere o ciclo com: uv run python -m observatorio.cli audit-all --refresh"
        )
    agora = normalize(json.loads(atual_path.read_text(encoding="utf-8")))

    print(f"anterior: {origem} ({len(antes)} iniciativas)")
    print(f"atual   : {atual_path} ({len(agora)} iniciativas)\n")

    print("=== distribuição de status ===")
    for rotulo, conjunto in (("anterior", antes), ("atual", agora)):
        contagem = Counter(e["status"] for e in conjunto.values())
        selos = contagem.get("auditado", 0)
        print(f"  {rotulo:<9} selos={selos:<4} " + " ".join(f"{k}={v}" for k, v in sorted(contagem.items())))

    novos = sorted(t for t in agora if t not in antes)
    sumidos = sorted(t for t in antes if t not in agora)
    if novos or sumidos:
        print(f"\n  só no atual: {novos[:8]} | só no anterior: {sumidos[:8]}")

    print("\n=== mudanças de status ===")
    migracoes = Counter(
        f"{antes[t]['status']} -> {agora[t]['status']}"
        for t in agora
        if t in antes and antes[t]["status"] != agora[t]["status"]
    )
    if not migracoes:
        print("  nenhuma")
    for url_key, n in migracoes.most_common():
        print(f"  {url_key:<40} {n}")

    perdeu = [
        t for t in agora if antes.get(t, {}).get("status") == "auditado" and agora[t]["status"] != "auditado"
    ]
    ganhou = [
        t for t in agora if antes.get(t, {}).get("status") != "auditado" and agora[t]["status"] == "auditado"
    ]

    print(f"\n=== selos perdidos ({len(perdeu)}) ===")
    for t in sorted(perdeu, key=lambda x: -(antes.get(x, {}).get("total_score") or 0)):
        print(
            f"  {t:<40} {score_text(antes.get(t, {}).get('total_score')):>6} -> "
            f"{score_text(agora[t]['total_score']):>6}  {agora[t]['status']}"
        )

    print(f"\n=== selos ganhos ({len(ganhou)}) ===")
    for t in sorted(ganhou, key=lambda x: -(agora[x]["total_score"] or 0)):
        print(
            f"  {t:<40} {score_text(antes.get(t, {}).get('total_score')):>6} -> {score_text(agora[t]['total_score']):>6}"
        )

    historico_novo = [t for t in agora if t in antes and agora[t]["html_sha256"] != antes[t]["html_sha256"]]
    print(f"\nconteúdo alterado desde o ciclo anterior: {len(historico_novo)} de {len(agora)}")

    bruto = json.loads(atual_path.read_text(encoding="utf-8"))
    if isinstance(bruto, list):
        sem_leitura = [r for r in bruto if r["status"] == "nao_inspecionado"]
        print(f"\n=== não inspecionados ({len(sem_leitura)}) ===")
        for r in sem_leitura:
            motivo = (r.get("coverage") or {}).get("reason") or "-"
            http = (r.get("performance") or {}).get("http_status")
            print(f"  {r['tool_id'][:38]:<40} http={http!s:<5} {motivo[:52]}")
        completos = sum(1 for r in bruto if (r.get("coverage") or {}).get("complete"))
        print(f"\ncobertura do ciclo atual: {completos} completos | {len(bruto) - completos} parciais")


if __name__ == "__main__":
    main()
