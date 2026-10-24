"""Verifica que todo script de `run:` nos workflows é shell sintaticamente válido.

Um `run:` é um arquivo YAML que vira script: o YAML aceita a string, o GitHub aceita o arquivo, e
só o shell descobre que falta uma aspa. Este teste reproduz o que o runner faz — substitui as
expressões `${{ }}` por um valor neutro e roda `bash -n`, que analisa sem executar.

A substituição importa porque `${{ }}` não é shell: quem escreve `ARGS="${{ inputs.x }}"` produz
`ARGS=""` no runner, mas o texto do arquivo nunca seria válido para o `bash`. Trocar por um valor
neutro é o que transforma o arquivo publicado no script que roda.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"
EXPRESSAO = re.compile(r"\$\{\{[^}]*\}\}")


def scripts_do(texto: str) -> list[tuple[str, str]]:
    """Extrai os scripts de todos os passos que usam `run`.

    Args:
        texto: Conteúdo do arquivo de workflow.

    Returns:
        Lista de pares com o nome do passo e o script correspondente.
    """
    dados = yaml.safe_load(texto) or {}
    scripts: list[tuple[str, str]] = []
    for job in (dados.get("jobs") or {}).values():
        for passo in (job or {}).get("steps") or []:
            if isinstance(passo, dict) and "run" in passo:
                scripts.append((str(passo.get("name", "sem nome")), str(passo["run"])))
    return scripts


def renderizar(script: str) -> str:
    """Substitui as expressões do GitHub por um valor neutro.

    Args:
        script: Texto do `run:` como está no arquivo.

    Returns:
        Script equivalente ao que o runner entrega ao shell.
    """
    return EXPRESSAO.sub("valor", script)


def analisar(script: str) -> subprocess.CompletedProcess:
    """Roda `bash -n` sobre o script renderizado, sem executá-lo.

    Args:
        script: Texto do `run:` como está no arquivo.

    Returns:
        Resultado do processo, com o erro de sintaxe na saída de erro quando houver.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as arquivo:
        arquivo.write(renderizar(script))
        caminho = Path(arquivo.name)
    try:
        return subprocess.run(["bash", "-n", str(caminho)], capture_output=True, text=True, check=False)
    finally:
        caminho.unlink(missing_ok=True)


class TestShellDosWorkflows(unittest.TestCase):
    """Nenhum passo de workflow pode publicar um script que o shell recuse."""

    def test_todos_os_scripts_sao_shell_valido(self):
        """Cada `run:` de cada workflow deve passar pela análise do `bash`."""
        arquivos = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(arquivos, "nenhum workflow encontrado")

        falhas = []
        for arquivo in arquivos:
            for nome, script in scripts_do(arquivo.read_text(encoding="utf-8")):
                resultado = analisar(script)
                if resultado.returncode != 0:
                    falhas.append(f"{arquivo.name} / {nome}: {resultado.stderr.strip().splitlines()[-1]}")

        self.assertEqual(falhas, [], "scripts recusados pelo bash:\n" + "\n".join(falhas))

    def test_expressao_do_github_vira_valor(self):
        """A expressão é substituída antes da análise, como faz o runner."""
        self.assertEqual(renderizar('ARGS="${{ inputs.x }}"'), 'ARGS="valor"')

    def test_aspa_sem_par_e_reprovada(self):
        """O portão precisa reprovar o defeito que ele existe para pegar."""
        resultado = analisar('ARGS="\nif [ "valor" = "true" ]; then\n  ARGS="$ARGS --refresh"\nfi\n')
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("unexpected EOF", resultado.stderr)


if __name__ == "__main__":
    unittest.main()
