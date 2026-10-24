"""Modelos Pydantic do Protocolo de Avaliação Cívica TecnoPopular.

Define as cinco dimensões cívicas, a rubrica ponderada, os modelos de snapshot com hash SHA-256
e o laudo de diagnóstico. `DiagnosticoCivico` é alias de `CivicAuditReport`, e `STATE_EXCLUDE`
lista os campos derivados que a projeção compacta de estado deixa de fora.
"""

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

STATE_EXCLUDE = {
    "summary_markdown": True,
    "dimension_scores": {"__all__": {"guidance"}},
}


class AuditLevel(str, Enum):
    """Níveis de governança e reconhecimento do ecossistema TecnoPopular.

    Attributes:
        CATALOGADO: Diretório Aberto, com elegibilidade básica de utilidade pública.
        AUDITADO: Certificação TecnoPopular, com selo aprovado no protocolo.
        EM_REVISAO: Pendência técnica ou documental identificada.
        REJEITADO: Descumprimento grave de privacidade ou telemetria invasiva.
        NAO_INSPECIONADO: Não houve leitura útil, portanto não há nota de qualidade.
    """

    CATALOGADO = "catalogado"
    AUDITADO = "auditado"
    EM_REVISAO = "em_revisao"
    REJEITADO = "rejeitado"
    NOT_INSPECTED = "nao_inspecionado"


NivelConformidade = AuditLevel


class DimensionType(str, Enum):
    """As 5 dimensões avaliadas na régua do Protocolo de Avaliação Cívica TecnoPopular.

    Attributes:
        PROVENIENCIA_DADOS: Peso 25%, fontes públicas como TSE, IBGE, CGU e pedidos LAI.
        PRIVACIDADE_SOBERANIA: Peso 25%, zero trackers comerciais e zero dados pessoais.
        CODIGO_ABERTO_FLOSS: Peso 20%, repositório público e licença livre.
        ENQUADRAMENTO_REPUBLICANO: Peso 15%, utilidade pública segundo Lakoff e Marco Civil.
        ACESSIBILIDADE_DESEMPENHO: Peso 15%, baixa largura de banda, WCAG AAA e zero paywall.
    """

    PROVENIENCIA_DADOS = "proveniencia_dados"
    PRIVACIDADE_SOBERANIA = "privacidade_e_soberania"
    CODIGO_ABERTO_FLOSS = "codigo_aberto_floss"
    ENQUADRAMENTO_REPUBLICANO = "enquadramento_republicano"
    ACESSIBILIDADE_DESEMPENHO = "acessibilidade_desempenho"


class ActionableGuidance(BaseModel):
    """Instrução didática e imediata para o mantenedor elevar a nota da aplicação."""

    dimension: DimensionType
    what_is_evaluated: str = Field(..., description="O que o algoritmo avalia no código ou na página")
    why_it_matters: str = Field(..., description="Por que essa exigência protege o cidadão ou a democracia")
    how_to_fix: str = Field(..., description="Passo a passo prático para resolver no código")


class DimensionScore(BaseModel):
    """Pontuação detalhada de uma dimensão específica da avaliação."""

    dimension: DimensionType
    weight_percentage: float = Field(..., description="Peso percentual da dimensão no score final (ex: 25.0)")
    raw_score: float = Field(..., ge=0.0, le=100.0, description="Nota bruta obtida na dimensão (0 a 100)")
    weighted_score: float = Field(..., description="Nota ponderada: raw_score * (weight / 100)")
    is_passing: bool = Field(..., description="Indica se atingiu a nota de corte mínima na dimensão")
    findings: list[str] = Field(
        default_factory=list, description="Evidências empíricas constatadas na inspeção"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Orientações técnicas para aprimoramento"
    )
    guidance: ActionableGuidance | None = None


class ProvenanceDetail(BaseModel):
    """Detalhamento técnico da proveniência documental de dados (Aos Fatos / LAI)."""

    declared_sources: list[str] = Field(
        default_factory=list, description="Fontes declaradas (ex: TSE, IBGE, CGU)"
    )
    official_links_detected: list[str] = Field(
        default_factory=list, description="Hiperlinks para domínios *.gov.br / *.jus.br"
    )
    has_speculative_claims: bool = False
    reproducibility_documented: bool = True


class PrivacyDetail(BaseModel):
    """Detalhamento técnico da postura de privacidade e soberania (LGPD)."""

    detected_commercial_trackers: list[str] = Field(
        default_factory=list, description="GA4, Meta Pixel, Hotjar, Clarity, etc."
    )
    collects_sensitive_pII: bool = Field(
        default=False, description="Formulários que solicitam CPF, e-mail obrigatório ou telefone"
    )
    enforces_login: bool = Field(default=False, description="Exige autenticação para acesso a dados públicos")
    has_privacy_policy: bool = False
    is_local_first_or_ephemeral: bool = True


class FlossDetail(BaseModel):
    """Detalhamento técnico de código aberto e auditabilidade algorítmica."""

    public_repository_url: str | None = None
    spdx_license_detected: str | None = Field(
        default=None, description="Identificador SPDX detectado (ex: MIT, GPL-3.0, Apache-2.0)"
    )
    is_osi_approved: bool = False
    has_recent_commits: bool | None = Field(default=None, description="None significa não medido")


class FramingDetail(BaseModel):
    """Detalhamento semântico do enquadramento e conformidade republicana (Lakoff / Marco Civil)."""

    affirmative_public_value: bool = Field(
        default=True, description="Foco em direitos, serviços públicos e utilidade material"
    )
    polarizing_risk_keywords: list[str] = Field(default_factory=list)
    semantic_provider: str | None = Field(None, description="Classificador semântico usado: off, jev")
    semantic_model: str | None = Field(None, description="Modelo resolvido do classificador (ex: jev-1.13.0)")
    semantic_propositivo: float | None = Field(
        None, ge=0.0, le=1.0, description="Probabilidade (noul) de enquadramento propositivo"
    )
    semantic_polarizador: float | None = Field(
        None, ge=0.0, le=1.0, description="Probabilidade (noul) de enquadramento polarizador"
    )
    semantic_intensity: float | None = Field(
        None, description="Intensidade do enquadramento polarizador (score da API)"
    )
    semantic_usage: dict[str, int] = Field(
        default_factory=dict, description="Tokens consumidos na classificação (usage da API)"
    )
    semantic_adjustment_applied: bool = Field(
        False, description="Indica se a classificação semântica alterou a nota da dimensão"
    )
    semantic_error: str | None = Field(None, description="Motivo da indisponibilidade da classificação")
    respects_civil_framework: bool = Field(
        default=True, description="Conformidade com o Art. 19 do Marco Civil, sem ataques ad hominem"
    )
    mitigates_backfire_effect: bool = True


class PerformanceDetail(BaseModel):
    """Detalhamento de acessibilidade e desempenho em conexões periféricas e de baixa largura de banda."""

    response_time_ms: int = 0
    html_payload_bytes: int = 0
    is_low_bandwidth_friendly: bool = Field(
        default=True, description="Verdadeiro quando a carga inicial total fica abaixo de 500 KB"
    )
    has_commercial_paywall: bool = False
    has_ad_banners: bool = False
    fetch_provider: str | None = Field(
        None, description="Provedor que obteve o HTML: http, firecrawl ou cache"
    )
    http_status: int | None = Field(None, description="Código de status HTTP observado na aquisição")
    bytes_scanned: int = Field(0, description="Bytes efetivamente inspecionados (após corte de leitura)")
    payload_bytes_declared: int | None = Field(
        None, description="Tamanho declarado pelo servidor (Content-Length)"
    )
    inspection_truncated: bool = Field(
        False, description="Verdadeiro quando a inspeção cobriu apenas parte do documento"
    )
    inspection_limit_bytes: int | None = Field(None, description="Limite de leitura aplicado nesta inspeção")


class AuditRubric(BaseModel):
    """Configuração dos pesos, das notas de corte e dos limiares de selo da governança.

    `weights` e `min_dimension_score` cobrem as cinco dimensões. O corte de privacidade é o mais
    alto da rubrica, por rigor deliberado nessa dimensão. `seal_threshold` é a nota mínima para
    receber o Selo Certificado TecnoPopular e `directory_threshold` a nota mínima para permanecer
    elegível no catálogo aberto.
    """

    weights: dict[DimensionType, float] = {
        DimensionType.PROVENIENCIA_DADOS: 25.0,
        DimensionType.PRIVACIDADE_SOBERANIA: 25.0,
        DimensionType.CODIGO_ABERTO_FLOSS: 20.0,
        DimensionType.ENQUADRAMENTO_REPUBLICANO: 15.0,
        DimensionType.ACESSIBILIDADE_DESEMPENHO: 15.0,
    }

    min_dimension_score: dict[DimensionType, float] = {
        DimensionType.PROVENIENCIA_DADOS: 60.0,
        DimensionType.PRIVACIDADE_SOBERANIA: 80.0,
        DimensionType.CODIGO_ABERTO_FLOSS: 50.0,
        DimensionType.ENQUADRAMENTO_REPUBLICANO: 60.0,
        DimensionType.ACESSIBILIDADE_DESEMPENHO: 50.0,
    }

    seal_threshold: float = Field(
        default=80.0, description="Nota mínima para receber o Selo Certificado TecnoPopular"
    )
    directory_threshold: float = Field(
        default=50.0, description="Nota mínima para manter-se elegível no catálogo aberto"
    )


class Iniciativa(BaseModel):
    """Registro cadastral de uma ferramenta cívica no catálogo.

    O `tool_id` é gravado na entrada e não é recalculado: ele nomeia o laudo publicado
    (`auditoria/<tool_id>.html`), o manifesto de estado e o cache de rede. Derivá-lo do nome a cada
    ciclo faria uma correção de nome publicar o laudo seguinte em outro endereço e partir o
    histórico da iniciativa em dois.
    """

    tool_id: str = Field(..., description="Slug canônico em minúsculas, congelado na inclusão")
    nome: str = Field(..., description="Nome público da iniciativa")
    url: str = Field(..., description="URL direta e pública da aplicação")
    domain: str | None = Field(None, description="Domínio limpo da aplicação")
    categoria: str | None = Field("Iniciativa Cívica", description="Eixo temático no catálogo")
    descricao: str | None = Field("", description="Descrição e propósito da ferramenta")
    repositorio: str | None = Field(None, description="URL pública do repositório git declarado")
    fontes_primarias: list[str] | None = Field(default_factory=list, description="Órgãos públicos citados")
    adicionado_em: date | None = Field(
        None, description="Data de inclusão no catálogo; ausente nas entradas anteriores ao registro"
    )


class Snapshot(BaseModel):
    """Registro transitório pontual de uma inspeção de rede."""

    tool_id: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    http_status: int
    response_time_ms: int
    content_length_bytes: int
    html_sha256: str


class InspectionCoverage(BaseModel):
    """Quanto do documento foi efetivamente inspecionado.

    Duas limitações independentes compõem a cobertura: o corte de bytes na aquisição e o corte
    dos escopos de texto no parser. `complete` só é verdadeiro quando nenhuma das duas atuou.
    """

    bytes_scanned: int = 0
    bytes_declared: int | None = None
    byte_coverage: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="bytes_scanned / bytes_declared (None quando o tamanho é desconhecido)",
    )
    text_chars_scanned: int = 0
    text_chars_total: int = 0
    text_truncated: bool = False
    inline_scripts_truncated: bool = False
    inspection_truncated: bool = False
    inspection_limit_bytes: int | None = None
    inspected: bool = Field(
        True, description="Falso quando não houve leitura útil: nenhuma avaliação de qualidade se aplica"
    )
    reason: str | None = Field(None, description="Motivo da ausência de leitura útil")
    complete: bool = True


class CivicAuditReport(BaseModel):
    """Relatório final emitido pelo Protocolo de Avaliação Cívica TecnoPopular."""

    tool_id: str = Field(..., description="Identificador único (slug da ferramenta)")
    tool_name: str
    target_url: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evaluator_version: str = "Protocolo TecnoPopular CTP-1.0"

    status: AuditLevel
    total_score: float | None = Field(
        None,
        ge=0.0,
        le=100.0,
        description="Nota consolidada de 0 a 100. Nula quando não houve leitura útil: sem leitura não há nota",
    )

    html_sha256: str | None = Field(None, description="Hash SHA-256 do corpo HTTP bruto inspecionado")
    archive_url: str | None = Field(None, description="URL perene no Internet Archive (Wayback Machine)")

    has_critical_blocker: bool = False
    critical_blockers: list[str] = Field(default_factory=list)

    dimension_scores: dict[DimensionType, DimensionScore]

    provenance: ProvenanceDetail | None = None
    privacy: PrivacyDetail | None = None
    floss: FlossDetail | None = None
    framing: FramingDetail | None = None
    performance: PerformanceDetail | None = None

    badge_label: str
    summary_markdown: str | None = None
    coverage: InspectionCoverage | None = Field(
        None, description="Cobertura da inspeção: bytes lidos, escopos truncados e completude"
    )

    def model_dump_state(self) -> dict[str, Any]:
        """Produz a projeção compacta do laudo usada como estado do motor.

        Remove os textos derivados (sumário em Markdown e orientação por dimensão), que são
        reconstruíveis a partir dos campos estruturados e de `guidance.get_guidance`. Não serve
        para publicação: o laudo público precisa do texto completo.

        Returns:
            Dicionário serializável em JSON, sem os campos derivados listados em `STATE_EXCLUDE`.
        """
        return self.model_dump(mode="json", exclude=STATE_EXCLUDE)


class EventType(str, Enum):
    """Natureza do evento registrado sobre uma iniciativa.

    Attributes:
        RETIRADA: Pedido de retirada acatado, que tira a iniciativa do catálogo.
        READMISSAO: Pedido de volta, que devolve a iniciativa ao catálogo.
    """

    WITHDRAWAL = "retirada"
    READMISSION = "readmissao"


class WithdrawalEvent(BaseModel):
    """Evento de retirada ou readmissão, com o motivo informado por quem pediu.

    O registro guarda eventos, e não um estado: retirar uma iniciativa do catálogo apagaria
    também a razão da retirada, e uma readmissão posterior apagaria tanto a razão da volta
    quanto o próprio pedido de saída. Como lista append-only de eventos, o histórico dos dois
    movimentos sobrevive, e o estado vigente é sempre o último evento do endereço.

    Attributes:
        tipo: Se o evento retira a iniciativa do catálogo ou a devolve a ele.
        nome: Nome da iniciativa no momento do evento.
        url: Endereço do evento, como constava do catálogo.
        motivo: Texto informado pelo solicitante, preservado sem edição.
        canal: Origem do pedido, como `email` ou `formulario`.
        solicitado_em: Data em que o pedido foi feito.
        registrado_em: Data em que o observatório registrou o evento.
        entrada_original: Registro do catálogo como estava antes da retirada, para permitir
            a readmissão sem obrigar o operador a reconstruir categoria e descrição.
    """

    tipo: EventType = Field(..., description="Natureza do evento")
    nome: str = Field(..., min_length=1, description="Nome da iniciativa no momento do evento")
    url: str = Field(..., min_length=8, description="Endereço do evento, como constava do catálogo")
    motivo: str = Field(..., min_length=3, description="Motivo informado pelo solicitante")
    canal: str = Field(default="email", min_length=1, description="Origem do pedido")
    solicitado_em: date = Field(..., description="Data em que o pedido foi feito")
    registrado_em: date = Field(default_factory=lambda: datetime.now(UTC).date())
    original_entry: dict[str, Any] | None = Field(
        default=None, description="Registro do catálogo antes da retirada, usado na readmissão"
    )


class WithdrawalRegistry(BaseModel):
    """Registro append-only de retiradas e readmissões, versionado junto com o catálogo.

    Attributes:
        versao: Versão do esquema do arquivo, para permitir migração futura.
        eventos: Eventos em ordem cronológica de registro; nunca editados nem removidos.
    """

    versao: int = Field(default=1, description="Versão do esquema do arquivo")
    eventos: list[WithdrawalEvent] = Field(default_factory=list, description="Eventos registrados")


DiagnosticoCivico = CivicAuditReport
