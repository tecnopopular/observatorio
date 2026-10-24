"""Testes da conferência entre o manifesto de estado e o cache de HTML.

É o portão que garante que o laudo publicado pode ser recalculado a partir do que foi guardado:
quando o cache diverge do manifesto, a afirmação de integridade perde o lastro.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from observatorio.models import AuditLevel
from observatorio.state import (
    Manifest,
    ManifestEntry,
    body_digest,
    file_digest,
    verify_cache,
)

RAIZ = Path(__file__).resolve().parent.parent
FERRAMENTA = RAIZ / "tools" / "verify_cache.py"


def _manifesto(pares: list[tuple[str, str | None]]) -> Manifest:
    """Monta um manifesto de estado com os pares informados.

    Args:
        pares: Identificador e hash do corpo, ou None para entrada sem leitura útil.

    Returns:
        Manifesto pronto para a conferência de cache.
    """
    return Manifest(
        total=len(pares),
        entries=[
            ManifestEntry(
                tool_id=tool_id,
                html_sha256=hash_do_corpo,
                evaluated_at=datetime.now(UTC).isoformat(),
                status=AuditLevel.CATALOGADO,
                total_score=70.0,
            )
            for tool_id, hash_do_corpo in pares
        ],
    )


class TestConferenciaDoCache(unittest.TestCase):
    """Verifica a comparação entre hash registrado e corpo em cache."""

    def test_cache_identico_ao_manifesto(self):
        """Corpos iguais aos hasheados tornam o laudo recalculável."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            html = "<html><body>conteudo</body></html>"
            (cache / "a.html").write_text(html, encoding="utf-8")
            manifesto = _manifesto([("a", body_digest(html))])

            resultado = verify_cache(manifesto, cache)

        self.assertTrue(resultado.intact)
        self.assertEqual(resultado.conferidos, 1)
        self.assertEqual(resultado.divergentes, [])

    def test_corpo_alterado_e_apontado(self):
        """Cache com outro conteúdo é divergência, não detalhe."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "a.html").write_text("<html>mudou</html>", encoding="utf-8")
            manifesto = _manifesto([("a", body_digest("<html>original</html>"))])

            resultado = verify_cache(manifesto, cache)

        self.assertFalse(resultado.intact)
        self.assertEqual(resultado.divergentes, ["a"])

    def test_corpo_ausente_e_apontado(self):
        """Entrada com hash e sem corpo não é conferível."""
        with tempfile.TemporaryDirectory() as tmp:
            manifesto = _manifesto([("a", body_digest("<html>x</html>"))])
            resultado = verify_cache(manifesto, Path(tmp))

        self.assertFalse(resultado.intact)
        self.assertEqual(resultado.ausentes, ["a"])

    def test_entrada_sem_hash_fica_fora(self):
        """Iniciativa não inspecionada não tem o que conferir."""
        manifesto = _manifesto([("a", None)])
        with tempfile.TemporaryDirectory() as tmp:
            resultado = verify_cache(manifesto, Path(tmp))

        self.assertTrue(resultado.intact)
        self.assertEqual(resultado.sem_hash, ["a"])
        self.assertEqual(resultado.conferidos, 0)

    def test_corpo_com_crlf_confere(self):
        """Página servida com CRLF precisa fechar o hash: cache é corpo bruto, não texto traduzido.

        Ler o arquivo em modo texto converte `\r\n` em `\n` e muda o hash de toda página que usa
        fim de linha do Windows. O teste grava bytes e confere que a leitura não os altera.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            bruto = "<html>\r\n<body>linha</body>\r\n</html>"
            (cache / "a.html").write_bytes(bruto.encode("utf-8"))
            manifesto = _manifesto([("a", body_digest(bruto))])

            resultado = verify_cache(manifesto, cache)

        self.assertTrue(resultado.intact)
        self.assertEqual(resultado.conferidos, 1)

    def test_digest_do_arquivo_nao_traduz_fim_de_linha(self):
        """O digest do arquivo é o digest dos bytes gravados, não do texto relido."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "a.html"
            bruto = "<p>a</p>\r\n<p>b</p>"
            cache_file.write_bytes(bruto.encode("utf-8"))
            self.assertEqual(file_digest(cache_file), body_digest(bruto))
            self.assertNotEqual(file_digest(cache_file), body_digest(cache_file.read_text(encoding="utf-8")))

    def test_hash_usa_a_mesma_via_do_motor(self):
        """O digest é calculado sobre o texto em UTF-8, como o motor registra."""
        self.assertEqual(
            body_digest("Ação"),
            "1dfbdde6410ab51a811ae7e551c89cff5e8cb7153b7a28a9e178584430f844d4",
        )


class TestFerramentaDeConferencia(unittest.TestCase):
    """Verifica o comando usado no portão de publicação."""

    def test_sai_com_zero_quando_integro(self):
        """Cache fiel ao manifesto libera a publicação."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            html = "<html><body>ok</body></html>"
            (tmp / "cache").mkdir()
            (tmp / "cache" / "a.html").write_text(html, encoding="utf-8")
            manifesto = _manifesto([("a", body_digest(html))])
            path = tmp / "estado.json"
            path.write_text(json.dumps(manifesto.model_dump(mode="json")), encoding="utf-8")

            codigo = subprocess.run(
                [
                    sys.executable,
                    str(FERRAMENTA),
                    "--manifesto",
                    str(tmp / "estado.json"),
                    "--cache",
                    str(tmp / "cache"),
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(codigo.returncode, 0, codigo.stderr)
        self.assertIn("[OK] Cache íntegro", codigo.stdout)

    def test_sai_com_um_quando_divergente(self):
        """Divergência bloqueia a publicação."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "cache").mkdir()
            (tmp / "cache" / "a.html").write_text("<html>outro</html>", encoding="utf-8")
            manifesto = _manifesto([("a", body_digest("<html>original</html>"))])
            path = tmp / "estado.json"
            path.write_text(json.dumps(manifesto.model_dump(mode="json")), encoding="utf-8")

            codigo = subprocess.run(
                [
                    sys.executable,
                    str(FERRAMENTA),
                    "--manifesto",
                    str(tmp / "estado.json"),
                    "--cache",
                    str(tmp / "cache"),
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(codigo.returncode, 1)
        self.assertIn("hash diferente do manifesto", codigo.stderr)


if __name__ == "__main__":
    unittest.main()
