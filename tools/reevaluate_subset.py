"""Reavalia um recorte do catálogo e compara com o laudo publicado.

Uso:
    uv run python tools/reevaluate_subset.py --status rejeitado [--limite 10]

Serve para medir o efeito de mudanças de heurística sobre avaliações já publicadas.
"""

import argparse
import json
import tempfile
from pathlib import Path

from observatorio.engine import assign_tool_ids, run_batch_audit

RAIZ = Path(__file__).resolve().parent.parent
REPORT = RAIZ / "dist" / "auditoria_iniciativas.json"
CATALOG = RAIZ / "data" / "data_iniciativas.json"


def main() -> None:
    """Reavalia um recorte do catálogo e compara com o laudo publicado.

    O recorte reaproveita `assign_tool_ids` do motor porque nomes repetidos no
    catálogo geram identificadores distintos, e a correspondência com o laudo
    publicado depende da mesma desambiguação.

    Raises:
        SystemExit: Quando o laudo publicado não existe.
    """
    parser = argparse.ArgumentParser(description="Reavalia um recorte do catálogo.")
    parser.add_argument("--status", default="rejeitado", help="status publicado a revisar")
    parser.add_argument("--limite", type=int, default=0, help="máximo de iniciativas (0 = todas)")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    if not REPORT.exists():
        raise SystemExit(f"laudo não encontrado: {REPORT}\nGere o ciclo antes de reavaliar um recorte.")
    antigos = json.loads(REPORT.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assign_tool_ids(catalog)
    por_id = {item["_tool_id"]: item for item in catalog}

    alvo_ids = [r["tool_id"] for r in antigos if r["status"] == args.status]
    if args.limite:
        alvo_ids = alvo_ids[: args.limite]
    alvos = [por_id[s] for s in alvo_ids if s in por_id]
    if not alvos:
        print("nenhuma iniciativa do recorte foi localizada no catálogo")
        return

    antes = {r["tool_id"]: r for r in antigos}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        entry = tmp / "cat.json"
        entry.write_text(json.dumps(alvos, ensure_ascii=False), encoding="utf-8")
        novos = run_batch_audit(
            input_file=entry,
            output_file=tmp / "laudo.json",
            manifest_file=tmp / "estado.json",
            max_workers=8,
            refresh_cache=True,
            skip_archive=True,
            timeout=args.timeout,
        )

    print(f"\n{'iniciativa':<34} {'antes':<12} {'depois':<12} motivo publicado")
    viradas = 0
    for r in sorted(novos, key=lambda x: x.tool_id):
        anterior = antes.get(r.tool_id, {})
        viradas += r.status.value != anterior.get("status")
        motivo = "; ".join(anterior.get("critical_blockers", []))[:44] or "-"
        marca = "  <== VIROU" if r.status.value != anterior.get("status") else ""
        print(f"{r.tool_id[:33]:<34} {anterior.get('status', '-'):<12} {r.status.value:<12} {motivo}{marca}")
    print(f"\navaliações alteradas: {viradas} de {len(novos)}")


if __name__ == "__main__":
    main()
