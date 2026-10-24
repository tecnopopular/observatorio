"""Verifica o padrão de documentação e a ausência de comentários explicativos no código.

O padrão adotado é o Google Style para docstrings. O verificador aponta quatro tipos de problema:

    - função sem docstring;
    - docstring sem seção `Args:` para cada parâmetro da assinatura;
    - docstring sem `Returns:` quando a função devolve valor;
    - comentário no código (fora de pragmas e do cabeçalho de codificação).

Uso:
    uv run python tools/verify_docstrings.py            # src/ e tools/
    uv run python tools/verify_docstrings.py caminho...  # arquivos ou diretórios
"""

import ast
import io
import sys
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = [RAIZ / "src", RAIZ / "tools", RAIZ / "tests"]
ALLOWED_PRAGMAS = ("#!", "# -*- coding", "# noqa", "# type: ignore", "# pragma", "# fmt: off", "# fmt: on")
INPUT_SECTIONS = ("Args:", "Arguments:")
OUTPUT_SECTIONS = ("Returns:", "Yields:")


def iter_functions(arvore: ast.Module) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Percorre a árvore e devolve todos os nós de função, inclusive métodos e aninhadas.

    Args:
        arvore: Módulo já compilado por `ast.parse`.

    Yields:
        Nós `FunctionDef` e `AsyncFunctionDef` na ordem em que aparecem.
    """
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield no


def parameter_names(funcao: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Lista os nomes de parâmetros da assinatura, exceto `self` e `cls`.

    Args:
        funcao: Nó de função.

    Returns:
        Nomes dos parâmetros que precisam aparecer na seção `Args:`.
    """
    argumentos = funcao.args
    nomes = [a.arg for a in argumentos.posonlyargs + argumentos.args + argumentos.kwonlyargs]
    if argumentos.vararg:
        nomes.append(argumentos.vararg.arg)
    if argumentos.kwarg:
        nomes.append(argumentos.kwarg.arg)
    return [n for n in nomes if n not in ("self", "cls")]


class _RetornoProprio(ast.NodeVisitor):
    """Detecta `return` com valor apenas no corpo da própria função.

    Funções, métodos e classes aninhados têm o próprio retorno e não respondem pela assinatura
    da função externa: um `read` interno não obriga o teste que o contém a documentar `Returns:`.
    """

    def __init__(self) -> None:
        """Inicializa o visitante sem retorno encontrado."""
        self.encontrado = False

    def visit_Return(self, no: ast.Return) -> None:
        """Registra retorno com valor.

        Args:
            no: Nó de retorno visitado.
        """
        if no.value is not None:
            self.encontrado = True

    def visit_FunctionDef(self, no: ast.FunctionDef) -> None:
        """Interrompe a descida em funções aninhadas.

        Args:
            no: Função aninhada encontrada.
        """
        return

    def visit_AsyncFunctionDef(self, no: ast.AsyncFunctionDef) -> None:
        """Interrompe a descida em funções assíncronas aninhadas.

        Args:
            no: Função aninhada encontrada.
        """
        return

    def visit_ClassDef(self, no: ast.ClassDef) -> None:
        """Interrompe a descida em classes aninhadas.

        Args:
            no: Classe aninhada encontrada.
        """
        return


def returns_value(funcao: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Indica se a própria função possui algum `return` com valor.

    Args:
        funcao: Nó de função.

    Returns:
        True quando há `return` com valor no corpo da função; False caso contrário.
    """
    visitante = _RetornoProprio()
    for comando in funcao.body:
        visitante.visit(comando)
    return visitante.encontrado


def comments(path: Path) -> list[tuple[int, str]]:
    """Extrai comentários reais do arquivo, ignorando `#` dentro de strings.

    Args:
        path: Arquivo Python a inspecionar.

    Returns:
        Lista de pares (linha, texto do comentário), sem pragmas e sem o cabeçalho de codificação.
    """
    achados: list[tuple[int, str]] = []
    with path.open("rb") as fluxo:
        try:
            tokens = list(tokenize.tokenize(io.BytesIO(fluxo.read()).readline))
        except tokenize.TokenError:
            return achados
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        text = token.string.strip()
        if any(text.startswith(p) for p in ALLOWED_PRAGMAS):
            continue
        achados.append((token.start[0], text))
    return achados


def verify_file(path: Path) -> list[str]:
    """Verifica um arquivo e devolve a lista de violações encontradas.

    Args:
        path: Arquivo Python a verificar.

    Returns:
        Lista de mensagens legíveis, vazia quando o arquivo está conforme.
    """
    problemas: list[str] = []
    fonte = path.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    for funcao in iter_functions(arvore):
        nome = funcao.name
        linha = funcao.lineno
        doc = ast.get_docstring(funcao)
        if not doc:
            problemas.append(f"{path}:{linha}: {nome} sem docstring")
            continue
        if not doc.strip().splitlines()[0].strip().endswith("."):
            problemas.append(f"{path}:{linha}: {nome} resumo sem ponto final")
        nomes = parameter_names(funcao)
        if nomes:
            if not any(secao in doc for secao in INPUT_SECTIONS):
                problemas.append(f"{path}:{linha}: {nome} sem seção Args:")
            else:
                ausentes = [n for n in nomes if f"{n}:" not in doc]
                if ausentes:
                    problemas.append(f"{path}:{linha}: {nome} Args sem {', '.join(ausentes)}")
        if returns_value(funcao) and not any(secao in doc for secao in OUTPUT_SECTIONS):
            problemas.append(f"{path}:{linha}: {nome} devolve valor sem seção Returns:")

    for linha, text in comments(path):
        problemas.append(f"{path}:{linha}: comentário a remover: {text[:70]}")

    return problemas


def collect_files(alvos: Sequence[str]) -> list[Path]:
    """Expande diretórios em arquivos `.py`, preservando a ordem recebida.

    Args:
        alvos: Caminhos de arquivos ou diretórios informados na linha de comando.

    Returns:
        Lista de arquivos Python a verificar.
    """
    encontrados: list[Path] = []
    for alvo in alvos:
        path = Path(alvo)
        if path.is_dir():
            encontrados.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            encontrados.append(path)
    return encontrados


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a verificação e devolve o código de saída.

    Args:
        argv: Argumentos de linha de comando; quando nulo, usa `sys.argv[1:]`.

    Returns:
        0 quando não há violações; 1 quando há.
    """
    alvos = list(argv if argv is not None else sys.argv[1:]) or [str(p) for p in DEFAULT_TARGETS]
    violacoes: list[str] = []
    for cache_file in collect_files(alvos):
        violacoes.extend(verify_file(cache_file))

    if not violacoes:
        print(
            f"[OK] {len(collect_files(alvos))} arquivo(s) conformes: docstrings Google e nenhum comentário."
        )
        return 0

    por_arquivo: dict[str, int] = {}
    for v in violacoes:
        url_key = v.split(":")[0]
        por_arquivo[url_key] = por_arquivo.get(url_key, 0) + 1
    for linha in violacoes:
        print(linha)
    print(f"\n{len(violacoes)} violação(ões) em {len(por_arquivo)} arquivo(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
