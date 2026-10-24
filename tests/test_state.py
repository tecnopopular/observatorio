"""
tests/test_state.py
Testes do manifesto compacto de estado do observatório.

O manifesto é a memória durável do motor: precisa conter o hash que decide o cache,
a nota e a data, e não pode carregar texto derivado (senão volta a inchar o repositório).
"""

import json
import tempfile
import unittest
from pathlib import Path

from observatorio.engine import _versao_dos_laudos
from observatorio.evaluator import CivicEvaluator
from observatorio.models import EVALUATOR_VERSION, AuditLevel
from observatorio.state import (
    MANIFEST_VERSION,
    build_manifest,
    changed_since,
    index_by_tool,
    load_manifest,
    write_manifest,
)

HTML = "<html><body><h1>Iniciativa</h1><p>Conteudo publico de teste.</p></body></html>"
QUANTIDADE = 175


def laudos_de_teste(quantidade: int = QUANTIDADE) -> list:
    """Produz laudos sintéticos pelo avaliador, sem depender de artefato publicado.

    O manifesto precisa ser testado sobre um conjunto do tamanho real do acervo, para que a
    asserção de tamanho signifique algo. Avaliar uma fixture uma vez e derivar as demais por
    cópia evita rede e mantém o teste independente de `dist/`, que não é versionado.

    Args:
        quantidade: Número de laudos a produzir.

    Returns:
        Laudos com identificadores distintos, mesmo conteúdo e mesma nota.
    """
    base = CivicEvaluator().evaluate_content(
        tool_id="base",
        tool_name="Iniciativa Base",
        target_url="https://exemplo.org",
        raw_html=HTML,
        response_time_ms=300,
        html_bytes=len(HTML.encode("utf-8")),
    )
    return [
        base.model_copy(
            update={
                "tool_id": f"iniciativa-{indice:03d}",
                "tool_name": f"Iniciativa {indice}",
                "target_url": f"https://exemplo{indice}.org",
            }
        )
        for indice in range(quantidade)
    ]


class TestManifesto(unittest.TestCase):
    """Conteúdo e forma do manifesto construído a partir do laudo publicado."""

    @classmethod
    def setUpClass(cls):
        """Constrói o manifesto usado pelos testes a partir de laudos sintéticos."""
        cls.reports = laudos_de_teste()
        cls.manifest = build_manifest(cls.reports)

    def test_uma_entrada_por_laudo(self):
        """O manifesto cobre exatamente o mesmo conjunto de iniciativas do laudo."""
        self.assertEqual(self.manifest.total, len(self.reports))
        ids_laudo = {r.tool_id for r in self.reports}
        ids_manifesto = {e.tool_id for e in self.manifest.entries}
        self.assertEqual(ids_laudo, ids_manifesto)

    def test_ordenacao_estavel(self):
        """Ordenação por tool_id evita diff ruidoso quando a nota muda de posição."""
        ids = [e.tool_id for e in self.manifest.entries]
        self.assertEqual(ids, sorted(ids))

    def test_campos_de_decisao_presentes(self):
        """Cada entrada traz o que decide a próxima execução: status, nota e data."""
        for entry in self.manifest.entries:
            self.assertEqual(entry.status.value in {nivel.value for nivel in AuditLevel}, True)
            if entry.status == AuditLevel.NOT_INSPECTED:
                self.assertIsNone(entry.total_score)
            else:
                self.assertGreaterEqual(entry.total_score, 0.0)
                self.assertLessEqual(entry.total_score, 100.0)
            self.assertTrue(entry.evaluated_at)
            self.assertEqual(len(entry.dimension_scores), 5)

    def test_sem_texto_derivado(self):
        """Manifesto não pode voltar a embutir orientação ou sumário em Markdown."""
        payload = json.dumps(self.manifest.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn("how_to_fix", payload)
        self.assertNotIn("what_is_evaluated", payload)
        self.assertNotIn("summary_markdown", payload)
        self.assertNotIn("findings", payload)

    def test_tamanho_compativel_com_versionamento(self):
        """Guarda o ganho real: manifesto com o tamanho do acervo fica abaixo de 100 KB."""
        payload = json.dumps(self.manifest.model_dump(mode="json"), ensure_ascii=False)
        self.assertLess(len(payload), 100_000, f"manifesto com {len(payload)} bytes")

    def test_versao_do_protocolo(self):
        """O manifesto declara a versão do protocolo em que foi gerado."""
        self.assertEqual(self.manifest.protocol_version, MANIFEST_VERSION)


class TestPersistencia(unittest.TestCase):
    """Gravação, leitura e comparação de manifestos em disco."""

    @classmethod
    def setUpClass(cls):
        """Produz o conjunto de laudos reutilizado pelos testes de persistência."""
        cls.reports = laudos_de_teste(4)

    def test_roundtrip_em_disco(self):
        """Gravar e reler preserva contagem, ordem, hash e tamanho declarado."""
        manifest = build_manifest(self.reports)
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "estado_hashes.json"
            tamanho = write_manifest(destino, manifest)
            self.assertEqual(tamanho, destino.stat().st_size)
            reaberto = load_manifest(destino)
            self.assertEqual(reaberto.total, manifest.total)
            self.assertEqual(
                [e.tool_id for e in reaberto.entries],
                [e.tool_id for e in manifest.entries],
            )
            primeiro = manifest.entries[0].tool_id
            self.assertEqual(index_by_tool(reaberto)[primeiro].html_sha256, manifest.entries[0].html_sha256)

    def test_manifesto_ausente_devolve_vazio(self):
        """Manifesto inexistente na primeira execução não é tratado como erro."""
        with tempfile.TemporaryDirectory() as tmp:
            vazio = load_manifest(Path(tmp) / "nao_existe.json")
            self.assertEqual(vazio.total, 0)
            self.assertEqual(vazio.entries, [])

    def test_changed_since_detecta_hash_alterado(self):
        """Hash diferente e iniciativa inédita contam como mudança; igual não conta."""
        manifest = build_manifest(self.reports)
        alvo = self.reports[0]
        alterado = alvo.model_copy(update={"html_sha256": "f" * 64})
        novos = alterado.model_copy(update={"tool_id": "iniciativa-inedita"})
        mudadas = changed_since(manifest, [alterado, *self.reports[1:], novos])
        self.assertIn(alvo.tool_id, mudadas)
        self.assertIn("iniciativa-inedita", mudadas)
        self.assertNotIn(self.reports[1].tool_id, mudadas)

    def test_changed_since_sem_alteracao(self):
        """Sem alteração de hash, nenhuma iniciativa precisa ser reavaliada."""
        manifest = build_manifest(self.reports)
        self.assertEqual(changed_since(manifest, self.reports), [])


class TestVersaoDoProtocolo(unittest.TestCase):
    """Verifica que laudo de versão anterior não é reaproveitado."""

    def test_versao_lida_do_laudo(self):
        """A versão vem do laudo anterior, não do manifesto."""
        anterior = {"a": {"tool_id": "a", "evaluator_version": "Protocolo TecnoPopular CTP-1.0"}}
        self.assertEqual(_versao_dos_laudos(anterior), "Protocolo TecnoPopular CTP-1.0")

    def test_sem_laudo_anterior_nao_ha_versao(self):
        """Primeiro ciclo não tem versão a comparar."""
        self.assertIsNone(_versao_dos_laudos({}))

    def test_versao_diferente_da_atual(self):
        """Versão gravada difere da versão corrente quando o critério mudou."""
        anterior = {"a": {"tool_id": "a", "evaluator_version": "Protocolo TecnoPopular CTP-1.0"}}
        self.assertNotEqual(_versao_dos_laudos(anterior), EVALUATOR_VERSION)


if __name__ == "__main__":
    unittest.main()
