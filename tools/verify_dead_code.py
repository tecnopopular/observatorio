"""Verifica código morto no repositório, filtrando o ruído estrutural do vulture.

O vulture marca como morto tudo que nenhum código lê diretamente, o que inclui campos de modelo
Pydantic, membros de enumeração e aliases de tipo — todos populados ou consumidos por nome, não por
referência estática. Este verificador mantém apenas o que é de fato código sem consumidor:

    - funções, métodos, classes e propriedades nunca usados;
    - importações nunca usadas;
    - constantes em CAIXA ALTA nunca lidas.

Variáveis e atributos em minúsculas são ignorados de propósito: em modelos Pydantic e dataclasses
elas existem para o serializador, e sinalizá-las produziria centenas de falsos positivos que
treinariam quem lê a ignorar o verificador.

Uso:
    uv run python tools/verify_dead_code.py            # via pyproject.toml
    uv run python tools/verify_dead_code.py --min-confidence 80
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PATTERN = re.compile(
    r"^(?P<arquivo>.+?):(?P<linha>\d+): unused (?P<tipo>[a-z ]+) '(?P<nome>[^']+)' \((?P<confianca>\d+)% confidence\)$"
)
RELEVANT_KINDS = {"function", "method", "class", "import", "property"}


def run_vulture(min_confidence: int, caminhos: list[str]) -> str:
    """Executa o vulture e devolve sua saída bruta.

    Args:
        min_confidence: Confiança mínima das ocorrências relatadas, em porcentagem.
        caminhos: Diretórios a analisar.

    Returns:
        Texto produzido pelo vulture, com uma ocorrência por linha.
    """
    comando = [
        sys.executable,
        "-m",
        "vulture",
        *caminhos,
        "--min-confidence",
        str(min_confidence),
    ]
    processo = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, check=False)
    return processo.stdout + processo.stderr


def select(saida: str) -> list[str]:
    """Separa as ocorrências acionáveis das que são ruído estrutural.

    Args:
        saida: Texto bruto devolvido pelo vulture.

    Returns:
        Lista de ocorrências que indicam código realmente sem consumidor.
    """
    relevantes: list[str] = []
    for linha in saida.splitlines():
        achado = PATTERN.match(linha.strip())
        if not achado:
            continue
        tipo = achado.group("tipo")
        nome = achado.group("nome")
        if tipo in RELEVANT_KINDS or (tipo == "variable" and nome.isupper()):
            relevantes.append(linha.strip())
    return relevantes


def main(argv: list[str] | None = None) -> int:
    """Executa a verificação e devolve o código de saída.

    Args:
        argv: Argumentos de linha de comando; quando nulo, usa `sys.argv[1:]`.

    Returns:
        Zero quando não há código morto acionável; um quando há.
    """
    parser = argparse.ArgumentParser(description="Detecta código morto com o vulture.")
    parser.add_argument("--min-confidence", type=int, default=60, help="confiança mínima")
    parser.add_argument("caminhos", nargs="*", default=["src", "tools", "tests"], help="diretórios")
    args = parser.parse_args(argv)

    saida = run_vulture(args.min_confidence, args.caminhos)
    relevantes = select(saida)

    if not relevantes:
        print(f"[OK] Nenhum código morto acionável em {', '.join(args.caminhos)}.")
        return 0

    print(f"Código morto detectado em {len(relevantes)} ocorrência(s):\n")
    for item in relevantes:
        print(f"  {item}")
    print("\nRemova o código sem consumidor ou registre a razão de mantê-lo.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
