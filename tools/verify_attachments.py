"""Portão dos anexos de release: tipo declarado e ausência de dado pessoal."""

import argparse
import sys

from observatorio.attachments import verify_attachments


def main() -> int:
    """Executa o portão sobre os anexos indicados na linha de comando.

    Returns:
        Zero quando todos os anexos passam; 2 quando há bloqueio.
    """
    parser = argparse.ArgumentParser(description="Verifica os anexos antes da publicação.")
    parser.add_argument("arquivos", nargs="+", help="Arquivos que serão anexados à release")
    argumentos = parser.parse_args()

    problemas = verify_attachments(argumentos.arquivos)
    if problemas:
        print("[FALHA] Anexos reprovados:", file=sys.stderr)
        for problema in problemas:
            print(f"  - {problema}", file=sys.stderr)
        return 2

    print(f"[OK] {len(argumentos.arquivos)} anexo(s) conferidos: tipo permitido e sem dado pessoal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
