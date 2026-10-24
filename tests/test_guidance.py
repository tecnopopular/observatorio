"""
tests/test_guidance.py
Testes de integridade da orientação metodológica e da projeção compacta de estado.

Premissa verificada aqui: o texto de orientação é função pura da dimensão (mais a flag
de indisponibilidade na privacidade). Se alguém interpolar achados, notas ou nomes de
terceiros nesse texto, estes testes quebram — e a quebra é intencional, porque a projeção
compacta do laudo depende dessa pureza para reconstruir o relatório publicado.
"""

import json
import unittest
from pathlib import Path

from observatorio.guidance import (
    build_report_from_state,
    get_guidance,
    known_texts,
)
from observatorio.models import CivicAuditReport, DimensionType

AUDIT_FILE = Path(__file__).resolve().parent.parent / "dist" / "auditoria_iniciativas.json"
CAMPOS = ("what_is_evaluated", "why_it_matters", "how_to_fix")


def _texto(dump: dict) -> str:
    """Serializa os três campos de orientação para comparação estável.

    Args:
        dump: Dicionário com os campos de orientação de uma dimensão.

    Returns:
        JSON com chaves ordenadas, restrito aos campos de orientação.
    """
    return json.dumps({k: dump[k] for k in CAMPOS}, sort_keys=True, ensure_ascii=False)


def _texto_da_dimensao(score) -> str:
    """Extrai o texto de orientação de uma dimensão, falhando alto quando ausente.

    Args:
        score: Pontuação de dimensão do laudo reconstruído.

    Returns:
        JSON dos campos de orientação da dimensão.
    """
    guidance = score.guidance
    if guidance is None:
        raise AssertionError(f"orientação ausente em {score.dimension.value}")
    return _texto(guidance.model_dump())


class TestGuidancePurity(unittest.TestCase):
    """Pureza do texto de orientação por dimensão."""

    def test_toda_dimensao_tem_orientacao(self):
        """Nenhuma dimensão pode ficar sem os três campos de orientação preenchidos."""
        for dimension in DimensionType:
            guidance = get_guidance(dimension)
            self.assertEqual(guidance.dimension, dimension)
            for campo in CAMPOS:
                self.assertTrue(getattr(guidance, campo).strip(), f"{dimension.value}: {campo} vazio")

    def test_instancias_nao_sao_compartilhadas(self):
        """Cada chamada devolve objeto novo: mutar um laudo não pode contaminar outro."""
        primeira = get_guidance(DimensionType.CODIGO_ABERTO_FLOSS)
        segunda = get_guidance(DimensionType.CODIGO_ABERTO_FLOSS)
        self.assertIsNot(primeira, segunda)
        primeira.how_to_fix = "alterado"
        self.assertNotEqual(segunda.how_to_fix, "alterado")

    def test_variante_indisponivel_so_muda_a_instrucao(self):
        """Na falta de resposta HTTP, o que se avalia e o porquê permanecem idênticos."""
        padrao = get_guidance(DimensionType.PRIVACIDADE_SOBERANIA)
        indisponivel = get_guidance(DimensionType.PRIVACIDADE_SOBERANIA, unavailable=True)
        self.assertEqual(padrao.what_is_evaluated, indisponivel.what_is_evaluated)
        self.assertEqual(padrao.why_it_matters, indisponivel.why_it_matters)
        self.assertNotEqual(padrao.how_to_fix, indisponivel.how_to_fix)

    def test_variante_indisponivel_inexistente_cai_no_padrao(self):
        """Flag aplicada a dimensão sem variante própria não pode levantar exceção."""
        sem_variante = get_guidance(DimensionType.PROVENIENCIA_DADOS, unavailable=True)
        padrao = get_guidance(DimensionType.PROVENIENCIA_DADOS)
        self.assertEqual(_texto(sem_variante.model_dump()), _texto(padrao.model_dump()))


class TestGuidanceNoLaudoPublicado(unittest.TestCase):
    """Compatibilidade entre o texto publicado e o repertório conhecido."""

    @classmethod
    def setUpClass(cls):
        """Carrega o laudo publicado usado como referência de comparação."""
        if not AUDIT_FILE.exists():
            raise unittest.SkipTest(f"laudo canônico ausente: {AUDIT_FILE}")
        cls.records = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))

    def test_texto_publicado_e_reconstruivel(self):
        """Todo texto gravado nos 175 laudos precisa ser uma das variantes conhecidas."""
        conhecidos = {_texto(t) for t in known_texts().values()}
        for record in self.records:
            for key, score in record["dimension_scores"].items():
                gravado = _texto(score["guidance"])
                self.assertIn(
                    gravado,
                    conhecidos,
                    f"{record['tool_id']}/{key}: texto fora do repertório conhecido (conteúdo dinâmico?)",
                )

    def test_reconstrucao_confere_com_o_gravado(self):
        """get_guidance reproduz byte a byte o texto de cada laudo já publicado."""
        for record in self.records:
            for key, score in record["dimension_scores"].items():
                dimension = DimensionType(key)
                gravado = _texto(score["guidance"])
                padrao = _texto(get_guidance(dimension).model_dump())
                indisponivel = _texto(get_guidance(dimension, unavailable=True).model_dump())
                self.assertIn(
                    gravado,
                    {padrao, indisponivel},
                    f"{record['tool_id']}/{key}: reconstrução divergiu do laudo publicado",
                )


class TestProjecaoDeEstado(unittest.TestCase):
    """Projeção compacta do laudo e reconstrução a partir dela."""

    @classmethod
    def setUpClass(cls):
        """Carrega o laudo publicado e o converte em relatórios validados."""
        if not AUDIT_FILE.exists():
            raise unittest.SkipTest(f"laudo canônico ausente: {AUDIT_FILE}")
        cls.serializados = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
        cls.reports = [CivicAuditReport.model_validate(r) for r in cls.serializados]

    def test_estado_omite_texto_derivado(self):
        """A projeção de estado não carrega sumário nem texto de orientação."""
        current_state = self.reports[0].model_dump_state()
        self.assertNotIn("summary_markdown", current_state)
        for score in current_state["dimension_scores"].values():
            self.assertNotIn("guidance", score)

    def test_estado_preserva_hash_e_pontuacao(self):
        """A projeção de estado precisa manter o que o motor usa para decidir o cache."""
        current_state = self.reports[0].model_dump_state()
        self.assertEqual(current_state["html_sha256"], self.reports[0].html_sha256)
        self.assertEqual(current_state["total_score"], self.reports[0].total_score)
        self.assertEqual(current_state["status"], self.reports[0].status.value)

    def test_reconstrucao_a_partir_do_estado(self):
        """O laudo reconstruído do estado coincide em identidade, nota e orientação."""
        original = self.reports[0]
        reconstruido = build_report_from_state(original.model_dump_state())
        self.assertEqual(reconstruido.tool_id, original.tool_id)
        self.assertEqual(reconstruido.total_score, original.total_score)
        self.assertEqual(set(reconstruido.dimension_scores), set(original.dimension_scores))
        for key, score in original.dimension_scores.items():
            self.assertEqual(
                _texto_da_dimensao(reconstruido.dimension_scores[key]),
                _texto_da_dimensao(score),
            )

    def test_projecao_nao_muta_o_laudo(self):
        """Projetar o estado não pode remover orientação nem sumário do laudo original."""
        report = self.reports[0]
        antes = {k: v.guidance is not None for k, v in report.dimension_scores.items()}
        report.model_dump_state()
        depois = {k: v.guidance is not None for k, v in report.dimension_scores.items()}
        self.assertEqual(antes, depois)
        self.assertIsNotNone(report.summary_markdown)

    def test_estado_e_ao_menos_metade_da_publicacao(self):
        """Guarda o ganho real: sem texto derivado o estado cai a menos de metade do laudo.

        A proporção medida também é registrada na asserção, para acompanhar regressão
        de tamanho ao longo do tempo.
        """
        publicacao = [r.model_dump(mode="json") for r in self.reports]
        current_state = [r.model_dump_state() for r in self.reports]
        tamanho_publicacao = len(json.dumps(publicacao, ensure_ascii=False))
        tamanho_estado = len(json.dumps(current_state, ensure_ascii=False))
        self.assertLess(tamanho_estado, tamanho_publicacao * 0.5)

        proporcao = tamanho_estado / tamanho_publicacao
        self.assertLess(proporcao, 0.40, f"estado ocupa {proporcao:.0%} da publicação")


if __name__ == "__main__":
    unittest.main()
