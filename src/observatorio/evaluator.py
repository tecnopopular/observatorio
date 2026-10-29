"""
Motor de Avaliação e Certificação Cívica TecnoPopular.
Executa inspeção determinística e estruturada (TypeSafe AI) em 5 dimensões sobre aplicações cívicas,
gerando diagnósticos com orientações práticas imediatas para voluntários e mantenedores.

Decisões de escopo registradas neste módulo:

- A camada semântica só altera a nota quando a confiança declarada alcança
  `SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE`; abaixo disso ela apenas informa.
- A superfície de telemetria inclui beacons disparados por `<img>`/`<noscript>`, que não
  aparecem como `src` de script, por isso o conjunto de assinaturas cobre pixel de conversão.
- Apenas licenças OSI contam como licença de software; Creative Commons é licença de conteúdo,
  ainda que permita uso amplo.
"""

import re
import unicodedata
from datetime import UTC, datetime
from typing import ClassVar
from urllib.parse import urlsplit

from .extract import PageEvidence, host_of, is_official_host, parse_page, repo_candidates
from .guidance import get_guidance
from .models import (
    ArchiveStatus,
    AuditLevel,
    AuditRubric,
    CivicAuditReport,
    DimensionScore,
    DimensionType,
    FlossDetail,
    FramingDetail,
    InspectionCoverage,
    PerformanceDetail,
    PrivacyDetail,
    ProvenanceDetail,
)
from .semantic import LABEL_POLARIZADOR, SemanticClassifier, SemanticResult

SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE = 0.90

COMMERCIAL_TRACKERS = {
    "Google Analytics (GA4/GTM)": [
        r"googletagmanager\.com/gtag/js\?id=(G-[A-Z0-9]+)",
        r"gtag\(['\"]config['\"],\s*['\"](G-[A-Z0-9]+)['\"]",
        r"googletagmanager\.com/gtm\.js\?id=(GTM-[A-Z0-9]+)",
        r"(UA-\d+-\d+)",
    ],
    "Meta Pixel (Facebook)": [
        r"connect\.facebook\.net/[a-zA-Z_]+/fbevents\.js",
        r"facebook\.com/tr\?id=\d+",
        r"fbq\(['\"]init['\"],\s*['\"](\d+)['\"]",
    ],
    "Hotjar / Clarity (Mapas de Calor)": [r"static\.hotjar\.com", r"www\.clarity\.ms/tag/([a-zA-Z0-9]+)"],
    "TikTok Pixel": [r"analytics\.tiktok\.com/i18n/pixel"],
    "Google AdSense": [r"pagead2\.googlesyndication\.com"],
}

IGNORED_REPO_ORGS = {
    "twbs",
    "facebook",
    "vercel",
    "google",
    "cdnjs",
    "fortawesome",
    "jquery",
    "leaflet",
    "chartjs",
    "hubspot",
    "microsoft",
    "angular",
    "vuejs",
    "tailwindlabs",
    "lodash",
    "moment",
    "axios",
    "actions",
    "features",
    "pricing",
    "security",
    "about",
    "contact",
    "join",
    "login",
    "topics",
    "collections",
    "trending",
    "explore",
    "search",
    "settings",
}


def clean_and_validate_repo_url(url: str | None) -> str | None:
    """Valida e normaliza uma URL candidata a repositório de código aberto.

    Rejeita perfis de usuário/organização (ex.: github.com/user), bibliotecas de terceiros
    (ex.: github.com/facebook/react) e URLs mal formatadas, de modo que a dimensão de código
    aberto não pontue por um link que não aponta para código da própria iniciativa.

    Args:
        url: URL bruta, declarada pela iniciativa ou extraída da página.

    Returns:
        URL canônica do repositório, ou None quando a URL não identifica um projeto de código.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()

    hf_space = re.match(r"https?://huggingface\.co/spaces/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)", url, re.I)
    if hf_space:
        return f"https://huggingface.co/spaces/{hf_space.group(1)}/{hf_space.group(2)}"

    hf_dataset = re.match(r"https?://huggingface\.co/datasets/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)", url, re.I)
    if hf_dataset:
        return f"https://huggingface.co/datasets/{hf_dataset.group(1)}/{hf_dataset.group(2)}"

    hf_model = re.match(r"https?://huggingface\.co/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)", url, re.I)
    if hf_model:
        owner = hf_model.group(1).lower()
        if owner not in {"datasets", "spaces", "docs", "blog", "models", "api", "pricing", "join", "login"}:
            return f"https://huggingface.co/{hf_model.group(1)}/{hf_model.group(2)}"

    gh_match = re.match(r"https?://github\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)", url, re.I)
    if gh_match:
        owner = gh_match.group(1)
        repo = gh_match.group(2).rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if owner.lower() in IGNORED_REPO_ORGS:
            return None
        return f"https://github.com/{owner}/{repo}"

    gl_match = re.match(r"https?://gitlab\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)", url, re.I)
    if gl_match:
        owner = gl_match.group(1)
        repo = gl_match.group(2).rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if owner.lower() in IGNORED_REPO_ORGS:
            return None
        return f"https://gitlab.com/{owner}/{repo}"

    cb_match = re.match(r"https?://codeberg\.org/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)", url, re.I)
    if cb_match:
        owner = cb_match.group(1)
        repo = cb_match.group(2).rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if owner.lower() in IGNORED_REPO_ORGS:
            return None
        return f"https://codeberg.org/{owner}/{repo}"

    return None


LICENSE_PATTERNS = {
    "GPL-3.0": [r"gpl-3\.0", r"gnu general public license"],
    "MIT": [r"mit license", r"licen[çc]a mit"],
    "Apache-2.0": [r"apache-2\.0", r"apache license"],
    "CC-BY-4.0": [r"creative commons", r"cc-by"],
}

OSI_SOFTWARE_LICENSES = {"MIT", "GPL-3.0", "Apache-2.0", "BSD-3-Clause", "MPL-2.0", "AGPL-3.0-or-later"}


def fontes_corroboradas(declared: list[str], hosts: set[str]) -> list[str]:
    """Seleciona as fontes declaradas que a própria página confirma por link.

    Declaração sem link correspondente não pontua: o valor de uma fonte está em ser verificável no
    documento avaliado, e não na afirmação de quem a declarou. A comparação aceita duas formas:

    - item com ponto, tratado como domínio ou URL, casa por igualdade ou por sufixo de host;
    - item sem ponto, tratado como nome de instituição, casa quando algum de seus termos, com ao
      menos quatro letras, aparece dentro de um host oficial ("senado federal" casa com
      `legis.senado.leg.br`); sem termos longos, vale o slug inteiro, com no mínimo três letras
      ("TSE" casa com `dadosabertos.tse.jus.br`).

    A regra é heurística e pode errar nas duas direções: por isso o laudo registra separadamente as
    fontes declaradas e as confirmadas, e a decisão de pontuar fica auditável.

    Args:
        declared: Fontes declaradas no catálogo, em texto livre.
        hosts: Hosts de custódia pública linkados pela página.

    Returns:
        Subconjunto das declarações que a página confirma, na ordem em que foram declaradas.
    """
    confirmadas: list[str] = []
    for item in declared:
        bruto = str(item).strip().lower()
        if not bruto:
            continue
        if "://" in bruto:
            bruto = urlsplit(bruto).netloc
        if "." in bruto:
            alvo = bruto.rstrip("/")
            achou = any(
                host == alvo or host.endswith("." + alvo) or alvo.endswith("." + host) for host in hosts
            )
        else:
            limpo = unicodedata.normalize("NFKD", bruto).encode("ascii", "ignore").decode().lower()
            termos = [termo for termo in re.split(r"[^a-z0-9]+", limpo) if len(termo) >= 4]
            if termos:
                achou = any(termo in host for termo in termos for host in hosts)
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", limpo).strip("-")
                achou = len(slug) >= 3 and any(slug in host for host in hosts)
        if achou:
            confirmadas.append(item)
    return confirmadas


class CivicEvaluator:
    """Motor determinístico para auditoria e concessão da Certificação TecnoPopular."""

    def __init__(self, rubric: AuditRubric | None = None):
        """Inicializa o motor com a rubrica de pesos e limiares.

        Args:
            rubric: Rubrica aplicada à avaliação; quando ausente, usa a rubrica padrão.
        """
        self.rubric = rubric or AuditRubric()

    def evaluate_content(
        self,
        tool_id: str,
        tool_name: str,
        target_url: str,
        raw_html: str,
        declared_repo: str | None = None,
        declared_sources: list[str] | None = None,
        response_time_ms: int = 0,
        html_bytes: int = 0,
        html_sha256: str | None = None,
        archive_url: str | None = None,
        archive_status: ArchiveStatus | None = None,
        fetch_provider: str | None = None,
        http_status: int | None = None,
        bytes_scanned: int = 0,
        inspection_truncated: bool = False,
        inspection_limit_bytes: int | None = None,
        inspected: bool = True,
        inspection_reason: str | None = None,
        classifier: SemanticClassifier | None = None,
        server: str | None = None,
    ) -> CivicAuditReport:
        """Executa a avaliação pura das 5 dimensões sobre o conteúdo HTML fornecido.

        Não há I/O de rede aqui: o HTML já adquirido é avaliado e o laudo é consolidado.
        Sem conteúdo legível não existe inspeção, e o motor não emite avaliação de qualidade
        sobre página vazia: a nota calculada nesse caso mede o motor, não a iniciativa, e
        por isso não é publicada.

        Args:
            tool_id: Identificador estável da iniciativa no catálogo.
            tool_name: Nome exibido da iniciativa.
            target_url: URL pública avaliada.
            raw_html: HTML da página, tal como adquirido.
            declared_repo: Repositório declarado pela iniciativa, quando houver.
            declared_sources: Fontes de dados declaradas pela iniciativa, quando houver.
            response_time_ms: Tempo de resposta da aquisição, em milissegundos.
            html_bytes: Tamanho declarado da resposta, em bytes.
            html_sha256: Impressão digital SHA-256 do snapshot avaliado.
            archive_url: Endereço do snapshot preservado no Internet Archive.
            archive_status: Situação da preservação no ciclo, registrada junto ao endereço.
            fetch_provider: Provedor usado na aquisição do HTML.
            http_status: Código de status HTTP observado na aquisição.
            bytes_scanned: Bytes efetivamente lidos; zero significa usar o tamanho do HTML.
            inspection_truncated: Indica que a aquisição foi interrompida por limite de tamanho.
            inspection_limit_bytes: Limite de bytes que interrompeu a aquisição, quando aplicável.
            inspected: Indica se houve leitura útil do documento.
            inspection_reason: Motivo registrado quando a inspeção não se realiza.
            classifier: Camada semântica opcional que complementa a dimensão de enquadramento.
            server: Identificação de servidor ou tecnologia extraída dos cabeçalhos HTTP.

        Returns:
            Laudo completo da iniciativa, com dimensões, cobertura e sumário em Markdown.
        """
        ev = parse_page(raw_html)

        if inspected and ev.is_empty:
            inspected = False
            inspection_reason = inspection_reason or "sem conteúdo legível"

        prov_score, prov_detail = self._eval_provenance(ev, declared_sources or [])

        priv_score, priv_detail, crit_blockers = self._eval_privacy(ev)

        floss_score, floss_detail = self._eval_floss(ev, declared_repo)

        semantic_result = classifier.classify_html(raw_html) if classifier else None
        framing_score, framing_detail = self._eval_framing(ev, semantic_result)

        perf_score, perf_detail = self._eval_performance(
            response_time_ms,
            html_bytes,
            ev,
            fetch_provider=fetch_provider,
            http_status=http_status,
            bytes_scanned=bytes_scanned or len(raw_html.encode("utf-8", errors="ignore")),
            inspection_truncated=inspection_truncated,
            inspection_limit_bytes=inspection_limit_bytes,
            server=server,
        )

        dim_scores: dict[DimensionType, DimensionScore] = {
            DimensionType.PROVENIENCIA_DADOS: prov_score,
            DimensionType.PRIVACIDADE_SOBERANIA: priv_score,
            DimensionType.CODIGO_ABERTO_FLOSS: floss_score,
            DimensionType.ENQUADRAMENTO_REPUBLICANO: framing_score,
            DimensionType.ACESSIBILIDADE_DESEMPENHO: perf_score,
        }

        cobertura = self._build_coverage(
            ev,
            bytes_scanned=bytes_scanned or len(raw_html.encode("utf-8", errors="ignore")),
            bytes_declared=html_bytes or None,
            inspection_truncated=inspection_truncated,
            inspection_limit_bytes=inspection_limit_bytes,
            inspected=inspected,
            reason=inspection_reason,
        )
        self._apply_coverage_caveats(dim_scores, cobertura)

        total_score = sum(ds.weighted_score for ds in dim_scores.values())
        has_critical = len(crit_blockers) > 0

        publishable_score: float | None = round(total_score, 1)
        if not inspected:
            status = AuditLevel.NOT_INSPECTED
            badge_label = "Não Inspecionado (sem leitura útil)"
            publishable_score = None
        elif has_critical:
            status = AuditLevel.REJEITADO
            badge_label = "Bloqueador Crítico de Privacidade"
        elif total_score >= self.rubric.seal_threshold:
            status = AuditLevel.AUDITADO
            badge_label = "Certificado TecnoPopular"
        elif total_score >= self.rubric.directory_threshold:
            status = AuditLevel.CATALOGADO
            badge_label = "Catalogado no Diretório Aberto"
        else:
            status = AuditLevel.EM_REVISAO
            badge_label = "Em Revisão Técnica"

        summary_md = self._render_markdown_summary(
            tool_name=tool_name,
            target_url=target_url,
            status=status,
            badge_label=badge_label,
            total_score=publishable_score,
            dim_scores=dim_scores,
            critical_blockers=crit_blockers,
        )

        return CivicAuditReport(
            tool_id=tool_id,
            tool_name=tool_name,
            target_url=target_url,
            evaluated_at=datetime.now(UTC),
            status=status,
            total_score=publishable_score,
            html_sha256=html_sha256,
            archive_url=archive_url,
            archive_status=archive_status,
            has_critical_blocker=has_critical,
            critical_blockers=crit_blockers,
            dimension_scores=dim_scores,
            provenance=prov_detail,
            privacy=priv_detail,
            floss=floss_detail,
            framing=framing_detail,
            performance=perf_detail,
            badge_label=badge_label,
            summary_markdown=summary_md,
            coverage=cobertura,
        )

    SCOPE_DEPENDENCY: ClassVar[dict[DimensionType, tuple[str, ...]]] = {
        DimensionType.PROVENIENCIA_DADOS: ("bytes",),
        DimensionType.CODIGO_ABERTO_FLOSS: ("bytes", "text"),
        DimensionType.ENQUADRAMENTO_REPUBLICANO: ("text",),
        DimensionType.PRIVACIDADE_SOBERANIA: ("bytes", "scripts"),
        DimensionType.ACESSIBILIDADE_DESEMPENHO: ("bytes", "scripts"),
    }

    def _build_coverage(
        self,
        ev: PageEvidence,
        bytes_scanned: int,
        bytes_declared: int | None,
        inspection_truncated: bool,
        inspection_limit_bytes: int | None,
        inspected: bool = True,
        reason: str | None = None,
    ) -> InspectionCoverage:
        """Consolida a cobertura: corte de bytes na aquisição e cortes de escopo no parser.

        Args:
            ev: Evidências extraídas da página.
            bytes_scanned: Bytes efetivamente lidos.
            bytes_declared: Tamanho declarado da resposta, quando conhecido.
            inspection_truncated: Indica interrupção da aquisição por limite de tamanho.
            inspection_limit_bytes: Limite de bytes que interrompeu a aquisição.
            inspected: Indica se houve leitura útil do documento.
            reason: Motivo registrado quando a inspeção não se realiza.

        Returns:
            Objeto de cobertura com as frações lidas e as ressalvas de escopo.
        """
        byte_coverage = None
        if bytes_declared and bytes_declared > 0:
            byte_coverage = min(1.0, bytes_scanned / bytes_declared)
        completo = inspected and not (
            inspection_truncated or ev.text_truncated or ev.inline_scripts_truncated
        )
        return InspectionCoverage(
            bytes_scanned=bytes_scanned,
            bytes_declared=bytes_declared,
            byte_coverage=byte_coverage,
            text_chars_scanned=len(ev.visible_text),
            text_chars_total=ev.text_chars_total,
            text_truncated=ev.text_truncated,
            inline_scripts_truncated=ev.inline_scripts_truncated,
            inspection_truncated=inspection_truncated,
            inspection_limit_bytes=inspection_limit_bytes,
            inspected=inspected,
            reason=reason,
            complete=completo,
        )

    def _coverage_limitations(self, coverage: InspectionCoverage) -> dict[str, str]:
        """Descreve, por escopo, o que ficou fora da inspeção.

        Args:
            coverage: Cobertura consolidada da inspeção.

        Returns:
            Mapa de escopo (`bytes`, `text`, `scripts`) para a descrição da limitação.
        """
        limitacoes: dict[str, str] = {}
        if coverage.bytes_declared and coverage.bytes_scanned < coverage.bytes_declared:
            limitacoes["bytes"] = (
                f"leitura interrompida em {coverage.bytes_scanned:,} de {coverage.bytes_declared:,} bytes"
            )
        elif coverage.inspection_limit_bytes and coverage.bytes_scanned >= coverage.inspection_limit_bytes:
            limitacoes["bytes"] = (
                f"leitura interrompida no limite de {coverage.inspection_limit_bytes:,} bytes "
                f"(tamanho total desconhecido)"
            )
        if coverage.text_truncated:
            limitacoes["text"] = (
                f"texto visível analisado até {coverage.text_chars_scanned:,} de "
                f"{coverage.text_chars_total:,} caracteres"
            )
        if coverage.inline_scripts_truncated:
            limitacoes["scripts"] = "scripts inline analisados parcialmente"
        if coverage.inspection_truncated and "bytes" not in limitacoes:
            limitacoes["bytes"] = (
                "leitura interrompida antes do fim do documento (tamanho total desconhecido)"
            )
        return limitacoes

    def _apply_coverage_caveats(
        self, dim_scores: dict[DimensionType, DimensionScore], coverage: InspectionCoverage
    ) -> None:
        """Acrescenta ressalva às dimensões cuja evidência veio de escopo incompleto.

        Uma avaliação "limpa" obtida sobre leitura parcial é uma afirmação sobre o trecho lido,
        não sobre o documento: a ressalva precisa estar no laudo, não só num metadado. O mapa
        `SCOPE_DEPENDENCY` define qual corte atinge cada dimensão, de modo que a ressalva só
        apareça onde a evidência daquela dimensão foi de fato limitada.

        Args:
            dim_scores: Pontuações por dimensão, alteradas no lugar com as ressalvas.
            coverage: Cobertura consolidada da inspeção.
        """
        if coverage.complete:
            return
        limitacoes = self._coverage_limitations(coverage)
        if not limitacoes:
            return
        for dimension, score in dim_scores.items():
            aplicaveis = [
                limitacoes[key] for key in self.SCOPE_DEPENDENCY.get(dimension, ()) if key in limitacoes
            ]
            if not aplicaveis:
                continue
            prefixo = (
                "Inspeção parcial" if dimension == DimensionType.ACESSIBILIDADE_DESEMPENHO else "Ressalva"
            )
            score.findings.append(f"{prefixo}: cobertura incompleta — {'; '.join(aplicaveis)}.")

    def _eval_provenance(
        self, ev: PageEvidence, declared: list[str]
    ) -> tuple[DimensionScore, ProvenanceDetail]:
        """Avalia a dimensão de proveniência dos dados primários.

        A evidência de proveniência é o hyperlink, não a menção em texto: apenas o destino de
        cada `<a>` é resolvido contra os hosts oficiais.

        Args:
            ev: Evidências extraídas da página.
            declared: Fontes de dados declaradas expressamente pela iniciativa.

        Returns:
            Pontuação da dimensão e o detalhamento de proveniência.
        """
        weight = self.rubric.weights[DimensionType.PROVENIENCIA_DADOS]
        findings = []
        recommendations = []
        score = 50.0

        detected_links = sorted({href for href in ev.links if is_official_host(host_of(href))})
        hosts_oficiais = {host_of(href) for href in detected_links}
        confirmadas = fontes_corroboradas(declared, hosts_oficiais)

        if confirmadas:
            score += 25.0
            findings.append(f"Fontes declaradas e confirmadas por link na página: {', '.join(confirmadas)}")
        if declared and not confirmadas:
            findings.append(f"Fontes declaradas sem link correspondente na página: {', '.join(declared)}")
            recommendations.append(
                "Linkar no texto a base oficial citada: a declaração só pontua com link de custódia pública."
            )

        if detected_links:
            score += 25.0
            findings.append(
                f"Hiperlinks diretos para bases oficiais identificados ({len(detected_links)} links)."
            )
        else:
            recommendations.append(
                "Incluir links diretos no rodapé para os dados abertos do TSE, IBGE ou CGU."
            )

        guidance = get_guidance(DimensionType.PROVENIENCIA_DADOS)

        score = min(100.0, score)
        detail = ProvenanceDetail(
            declared_sources=declared,
            corroborated_sources=confirmadas,
            official_links_detected=detected_links,
            has_speculative_claims=False,
            reproducibility_documented=True,
        )
        ds = DimensionScore(
            dimension=DimensionType.PROVENIENCIA_DADOS,
            weight_percentage=weight,
            raw_score=score,
            weighted_score=round(score * (weight / 100.0), 2),
            is_passing=score >= self.rubric.min_dimension_score[DimensionType.PROVENIENCIA_DADOS],
            findings=findings,
            recommendations=recommendations,
            guidance=guidance,
        )
        return ds, detail

    def _eval_privacy(self, ev: PageEvidence) -> tuple[DimensionScore, PrivacyDetail, list[str]]:
        """Avalia a dimensão de privacidade e soberania, com bloqueadores críticos.

        A telemetria considerada é o que a página carrega (src de script, corpo inline, pixel
        em img/iframe), não o que ela menciona no texto ou em comentário. Sem nada a inspecionar,
        a leitura de telemetria é inconclusiva e a dimensão não pode ser aprovada.

        Args:
            ev: Evidências extraídas da página.

        Returns:
            Pontuação da dimensão, detalhamento de privacidade e a lista de bloqueadores críticos.
        """
        weight = self.rubric.weights[DimensionType.PRIVACIDADE_SOBERANIA]
        findings = []
        recommendations = []
        critical_blockers = []
        score = 100.0

        if ev.is_empty:
            score = 50.0
            findings.append("Inspeção de telemetria inconclusiva: resposta vazia ou inacessível via HTTP.")
            recommendations.append(
                "Disponibilizar endpoint público acessível para auditoria ou fornecer espelho estático auditável."
            )
            detail = PrivacyDetail(
                detected_commercial_trackers=[],
                collects_sensitive_pII=False,
                enforces_login=False,
                has_privacy_policy=False,
                is_local_first_or_ephemeral=False,
            )
            ds = DimensionScore(
                dimension=DimensionType.PRIVACIDADE_SOBERANIA,
                weight_percentage=weight,
                raw_score=score,
                weighted_score=round(score * (weight / 100.0), 2),
                is_passing=False,
                findings=findings,
                recommendations=recommendations,
                guidance=get_guidance(DimensionType.PRIVACIDADE_SOBERANIA, unavailable=True),
            )
            return ds, detail, critical_blockers

        detected_commercial = []
        superficie = ev.script_surface
        for name, patterns in COMMERCIAL_TRACKERS.items():
            for p in patterns:
                if re.search(p, superficie, re.IGNORECASE):
                    detected_commercial.append(name)
                    break

        if detected_commercial:
            score -= 60.0
            critical_msg = (
                f"Detectado tracker comercial invasivo de telemetria: {', '.join(detected_commercial)}"
            )
            critical_blockers.append(critical_msg)
            findings.append(critical_msg)
            recommendations.append(
                "Remover pixels comerciais (Meta, GA4, Hotjar) para proteger a navegação do cidadão."
            )
        else:
            findings.append("Zero pixels comerciais identificados (livre de Meta Pixel, GA4 e Hotjar).")

        has_pII_inputs = any(nome.startswith(("cpf", "telefone", "celular")) for nome in ev.input_names)
        if has_pII_inputs:
            score -= 30.0
            blocker = "Presença de formulário solicitando CPF ou telefone de cidadãos."
            critical_blockers.append(blocker)
            findings.append(blocker)
            recommendations.append("Remover formulários de captura de dados pessoais (Privacy by Design).")
        else:
            findings.append("Ausência de formulários com requisição de CPF ou telefone.")

        guidance = get_guidance(DimensionType.PRIVACIDADE_SOBERANIA)

        score = max(0.0, score)
        detail = PrivacyDetail(
            detected_commercial_trackers=detected_commercial,
            collects_sensitive_pII=has_pII_inputs,
            enforces_login=False,
            has_privacy_policy=bool(
                re.search(r"privacidade|lgpd", ev.visible_text, re.IGNORECASE)
                or any("privacidade" in href.lower() for href in ev.links)
            ),
            is_local_first_or_ephemeral=True,
        )
        ds = DimensionScore(
            dimension=DimensionType.PRIVACIDADE_SOBERANIA,
            weight_percentage=weight,
            raw_score=score,
            weighted_score=round(score * (weight / 100.0), 2),
            is_passing=score >= self.rubric.min_dimension_score[DimensionType.PRIVACIDADE_SOBERANIA],
            findings=findings,
            recommendations=recommendations,
            guidance=guidance,
        )
        return ds, detail, critical_blockers

    def _eval_floss(self, ev: PageEvidence, declared_repo: str | None) -> tuple[DimensionScore, FlossDetail]:
        """Avalia a dimensão de código aberto e licença FLOSS.

        Repositório válido é link para host de código com caminho de projeto, nunca perfil de
        usuário. A licença é lida apenas no texto visível ou em link `rel="license"`, jamais em
        comentário ou script. Licença de conteúdo não pontua como código aberto: apenas licenças
        OSI aprovadas contam nesta dimensão. Atividade do repositório não é medida: o motor não
        consulta o repositório, de modo que `has_recent_commits` permanece sempre nulo.

        Args:
            ev: Evidências extraídas da página.
            declared_repo: Repositório declarado pela iniciativa; `NONE` indica declaração negativa.

        Returns:
            Pontuação da dimensão e o detalhamento de código aberto.
        """
        weight = self.rubric.weights[DimensionType.CODIGO_ABERTO_FLOSS]
        findings = []
        recommendations = []
        score = 40.0

        repo_url = clean_and_validate_repo_url(declared_repo)
        if not repo_url and declared_repo != "NONE":
            candidates = [
                validado
                for href in repo_candidates(
                    ev.links, hosts=("github.com", "gitlab.com", "codeberg.org", "huggingface.co")
                )
                if (validado := clean_and_validate_repo_url(href))
            ]
            if candidates:
                space_or_git = next(
                    (
                        c
                        for c in candidates
                        if "/spaces/" in c or "github.com" in c or "gitlab.com" in c or "codeberg.org" in c
                    ),
                    None,
                )
                repo_url = space_or_git or candidates[0]

        if not repo_url and declared_repo != "NONE":
            for pat in [
                r"https?://huggingface\.co/spaces/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)",
                r"https?://huggingface\.co/datasets/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)",
                r"https?://github\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)",
                r"https?://gitlab\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)",
                r"https?://codeberg\.org/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_.]+)",
            ]:
                m = re.search(pat, ev.visible_text, re.IGNORECASE)
                if m:
                    cand = clean_and_validate_repo_url(m.group(0))
                    if cand:
                        repo_url = cand
                        break

        if repo_url:
            score += 40.0
            findings.append(f"Repositório público de código localizado: {repo_url}")
        else:
            recommendations.append(
                "Publicar o código em repositório aberto (GitHub, GitLab ou Hugging Face)."
            )

        detected_license = None
        superficie_licenca = ev.visible_text + "\n" + "\n".join(ev.rel_license)
        for lic, patterns in LICENSE_PATTERNS.items():
            for p in patterns:
                if re.search(p, superficie_licenca, re.IGNORECASE):
                    detected_license = lic
                    break
            if detected_license:
                break

        if detected_license and detected_license in OSI_SOFTWARE_LICENSES:
            score += 20.0
            findings.append(f"Licença de código livre OSI identificada: {detected_license}")
        elif detected_license:
            findings.append(
                f"Licença detectada ({detected_license}) é de conteúdo, não de software: "
                f"não conta como código aberto para esta dimensão."
            )
            recommendations.append(
                "Publicar o código sob licença OSI (MIT, GPL-3.0 ou Apache-2.0), distinta da licença dos dados."
            )
        else:
            recommendations.append(
                "Adicionar arquivo LICENSE (MIT, GPL-3.0 ou Apache-2.0) na raiz do repositório."
            )

        guidance = get_guidance(DimensionType.CODIGO_ABERTO_FLOSS)

        score = min(100.0, score)
        detail = FlossDetail(
            public_repository_url=repo_url,
            spdx_license_detected=detected_license,
            is_osi_approved=detected_license in OSI_SOFTWARE_LICENSES,
            has_recent_commits=None,
        )
        ds = DimensionScore(
            dimension=DimensionType.CODIGO_ABERTO_FLOSS,
            weight_percentage=weight,
            raw_score=score,
            weighted_score=round(score * (weight / 100.0), 2),
            is_passing=score >= self.rubric.min_dimension_score[DimensionType.CODIGO_ABERTO_FLOSS],
            findings=findings,
            recommendations=recommendations,
            guidance=guidance,
        )
        return ds, detail

    def _eval_framing(
        self, ev: PageEvidence, semantic: SemanticResult | None = None
    ) -> tuple[DimensionScore, FramingDetail]:
        """Avalia a dimensão de enquadramento republicano do vocabulário.

        A camada semântica é evidência adicional, aplicada com limiar alto e sem duplicar a
        penalidade já dada pela detecção determinística de termos: o ajuste só ocorre quando
        a confiança alcança `SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE` e nenhum termo de risco foi
        encontrado antes.

        Args:
            ev: Evidências extraídas da página.
            semantic: Resultado da camada semântica, quando consultada.

        Returns:
            Pontuação da dimensão e o detalhamento de enquadramento.
        """
        weight = self.rubric.weights[DimensionType.ENQUADRAMENTO_REPUBLICANO]
        findings = []
        recommendations = []
        score = 80.0

        affirmative_terms = [
            "transparência",
            "participação",
            "cidadania",
            "dados abertos",
            "serviço público",
            "orçamento",
            "comunidade",
            "controle social",
            "direitos",
            "saúde",
            "educação",
            "transporte",
        ]
        found_terms = [t for t in affirmative_terms if re.search(rf"\b{t}\b", ev.visible_text, re.IGNORECASE)]
        if found_terms:
            score += 20.0
            findings.append(
                f"Enquadramento propositivo com vocabulário de utilidade pública: {', '.join(found_terms[:5])}"
            )

        toxic_risk_terms = [r"fascista", r"golpista", r"ladrão", r"genocida", r"vagabundo"]
        found_toxic = [t for t in toxic_risk_terms if re.search(rf"\b{t}\b", ev.visible_text, re.IGNORECASE)]
        if found_toxic:
            score -= 30.0
            findings.append(
                f"Atenção: terminologia com potencial de acionar guardrails ou alegações de calúnia: {', '.join(found_toxic)}"
            )
            recommendations.append(
                "Adotar vocabulário institucional ancorado em indicadores materiais e dados oficiais (Lakoff / E-E-A-T)."
            )

        guidance = get_guidance(DimensionType.ENQUADRAMENTO_REPUBLICANO)

        ajuste_semantico = False
        if semantic is not None and semantic.ok:
            polarizador = semantic.label(LABEL_POLARIZADOR)
            if polarizador >= SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE and not found_toxic:
                score -= 30.0
                ajuste_semantico = True
                findings.append(
                    f"Classificação semântica calibrada ({semantic.provider}/{semantic.model}) indicou "
                    f"enquadramento polarizador com probabilidade {polarizador:.2f}, acima do limiar de "
                    f"{SEMANTIC_ADJUSTMENT_MIN_CONFIDENCE:.2f}."
                )
                recommendations.append(
                    "Reformular o vocabulário para termos de utilidade pública, direitos e serviços essenciais."
                )

        score = max(0.0, min(100.0, score))
        detail = FramingDetail(
            affirmative_public_value=len(found_terms) > 0,
            polarizing_risk_keywords=found_toxic,
            semantic_provider=semantic.provider if semantic else None,
            semantic_model=semantic.model if semantic and semantic.ok else None,
            semantic_propositivo=semantic.label("propositivo") if semantic and semantic.ok else None,
            semantic_polarizador=semantic.label(LABEL_POLARIZADOR) if semantic and semantic.ok else None,
            semantic_intensity=semantic.intensity if semantic and semantic.ok else None,
            semantic_usage=semantic.usage if semantic and semantic.ok else {},
            semantic_adjustment_applied=ajuste_semantico,
            semantic_error=semantic.error if semantic else None,
            respects_civil_framework=len(found_toxic) == 0 and not ajuste_semantico,
            mitigates_backfire_effect=True,
        )
        ds = DimensionScore(
            dimension=DimensionType.ENQUADRAMENTO_REPUBLICANO,
            weight_percentage=weight,
            raw_score=score,
            weighted_score=round(score * (weight / 100.0), 2),
            is_passing=score >= self.rubric.min_dimension_score[DimensionType.ENQUADRAMENTO_REPUBLICANO],
            findings=findings,
            recommendations=recommendations,
            guidance=guidance,
        )
        return ds, detail

    def _eval_performance(
        self,
        latency_ms: int,
        html_bytes: int,
        ev: PageEvidence,
        fetch_provider: str | None = None,
        http_status: int | None = None,
        bytes_scanned: int = 0,
        inspection_truncated: bool = False,
        inspection_limit_bytes: int | None = None,
        server: str | None = None,
    ) -> tuple[DimensionScore, PerformanceDetail]:
        """Avalia a dimensão de acessibilidade e desempenho em conexões periféricas.

        Args:
            latency_ms: Tempo de resposta da aquisição, em milissegundos.
            html_bytes: Tamanho da resposta, em bytes.
            ev: Evidências extraídas da página.
            fetch_provider: Provedor usado na aquisição do HTML.
            http_status: Código de status HTTP observado na aquisição.
            bytes_scanned: Bytes efetivamente lidos na inspeção.
            inspection_truncated: Indica interrupção da aquisição por limite de tamanho.
            inspection_limit_bytes: Limite de bytes que interrompeu a aquisição.
            server: Identificação de servidor ou tecnologia extraída dos cabeçalhos HTTP.

        Returns:
            Pontuação da dimensão e o detalhamento de desempenho.
        """
        weight = self.rubric.weights[DimensionType.ACESSIBILIDADE_DESEMPENHO]
        findings = []
        recommendations = []
        score = 80.0

        guidance = get_guidance(DimensionType.ACESSIBILIDADE_DESEMPENHO)

        if ev.is_empty or html_bytes == 0:
            score = 30.0
            findings.append("Falha na coleta de carga inicial (0 KB transferidos).")
            recommendations.append(
                "Verificar estabilidade do servidor ou conectividade para conexões periféricas."
            )
            detail = PerformanceDetail(
                response_time_ms=latency_ms,
                html_payload_bytes=0,
                is_low_bandwidth_friendly=False,
                has_commercial_paywall=False,
                has_ad_banners=False,
                fetch_provider=fetch_provider,
                http_status=http_status,
                bytes_scanned=bytes_scanned,
                payload_bytes_declared=html_bytes or None,
                inspection_truncated=inspection_truncated,
                inspection_limit_bytes=inspection_limit_bytes,
                server=server,
            )
            ds = DimensionScore(
                dimension=DimensionType.ACESSIBILIDADE_DESEMPENHO,
                weight_percentage=weight,
                raw_score=score,
                weighted_score=round(score * (weight / 100.0), 2),
                is_passing=False,
                findings=findings,
                recommendations=recommendations,
                guidance=guidance,
            )
            return ds, detail

        if latency_ms > 0 and latency_ms < 1500:
            score += 10.0
            findings.append(f"Excelente tempo de resposta na borda: {latency_ms} ms.")
        elif latency_ms >= 3000:
            score -= 20.0
            recommendations.append(f"Otimizar tempo de resposta do servidor (atualmente {latency_ms} ms).")

        if html_bytes > 0 and html_bytes < 300_000:
            score += 10.0
            findings.append(f"Carga leve compatível com planos móveis periféricos ({html_bytes // 1024} KB).")
        elif html_bytes > 800_000:
            score -= 15.0
            recommendations.append(
                "Reduzir peso de assets iniciais para garantir inclusão digital em conexões 3G/4G."
            )

        has_ads = bool(re.search(r"adsbygoogle|taboola|outbrain", ev.script_surface, re.IGNORECASE))
        if has_ads:
            score -= 40.0
            findings.append("Identificada publicidade comercial invasiva de terceiros.")
            recommendations.append("Remover redes de anúncios para preservar o foco de utilidade pública.")
        else:
            findings.append("Interface livre de anúncios comerciais.")

        score = max(0.0, min(100.0, score))
        detail = PerformanceDetail(
            response_time_ms=latency_ms,
            html_payload_bytes=html_bytes,
            is_low_bandwidth_friendly=html_bytes < 500_000,
            has_commercial_paywall=False,
            has_ad_banners=has_ads,
            fetch_provider=fetch_provider,
            http_status=http_status,
            bytes_scanned=bytes_scanned,
            payload_bytes_declared=html_bytes or None,
            inspection_truncated=inspection_truncated,
            inspection_limit_bytes=inspection_limit_bytes,
            server=server,
        )
        ds = DimensionScore(
            dimension=DimensionType.ACESSIBILIDADE_DESEMPENHO,
            weight_percentage=weight,
            raw_score=score,
            weighted_score=round(score * (weight / 100.0), 2),
            is_passing=score >= self.rubric.min_dimension_score[DimensionType.ACESSIBILIDADE_DESEMPENHO],
            findings=findings,
            recommendations=recommendations,
            guidance=guidance,
        )
        return ds, detail

    def _render_markdown_summary(
        self,
        tool_name: str,
        target_url: str,
        status: AuditLevel,
        badge_label: str,
        total_score: float | None,
        dim_scores: dict[DimensionType, DimensionScore],
        critical_blockers: list[str],
    ) -> str:
        """Gera o sumário legível em Markdown auditável com guia prático para mantenedores.

        Args:
            tool_name: Nome exibido da iniciativa.
            target_url: URL pública avaliada.
            status: Nível de governança atribuído.
            badge_label: Rótulo do selo exibido no relatório.
            total_score: Nota consolidada; None quando não há leitura útil.
            dim_scores: Pontuações por dimensão.
            critical_blockers: Bloqueadores críticos que impedem a certificação.

        Returns:
            Relatório em Markdown, pronto para publicação.
        """
        lines = [
            f"# Relatório de Auditoria: {tool_name}",
            "",
            f"- **URL Avaliada:** `{target_url}`",
            f"- **Classificação:** **{badge_label}**",
            f"- **Pontuação Consolidada:** **{f'{total_score:.1f} / 100' if total_score is not None else 'não atribuída (sem leitura útil)'}**",
            f"- **Data da Avaliação:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "---",
            "",
            "## Desempenho nas 5 Dimensões da Certificação TecnoPopular",
            "",
            "| Dimensão Avaliada | Peso | Nota Bruta | Nota Ponderada | Status |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ]

        dim_names = {
            DimensionType.PROVENIENCIA_DADOS: "1. Proveniência de Dados Primários",
            DimensionType.PRIVACIDADE_SOBERANIA: "2. Privacidade Radical & Soberania",
            DimensionType.CODIGO_ABERTO_FLOSS: "3. Código Aberto & FLOSS",
            DimensionType.ENQUADRAMENTO_REPUBLICANO: "4. Enquadramento Republicano",
            DimensionType.ACESSIBILIDADE_DESEMPENHO: "5. Acessibilidade & Desempenho",
        }

        for dim, ds in dim_scores.items():
            st = "Aprovado" if ds.is_passing else "Ajuste Recomendado"
            lines.append(
                f"| **{dim_names.get(dim, dim.value)}** | {ds.weight_percentage:.0f}% | {ds.raw_score:.1f}/100 | {ds.weighted_score:.1f} pts | {st} |"
            )

        if critical_blockers:
            lines.extend(["", "### Bloqueadores Críticos de Certificação", ""])
            for b in critical_blockers:
                lines.append(f"- {b}")

        lines.extend(
            [
                "",
                "---",
                "",
                "## Guia Técnico do Mantenedor",
                "",
                "Detalhamento das dimensões avaliadas e recomendações de conformidade:",
                "",
            ]
        )

        for dim, ds in dim_scores.items():
            g = ds.guidance
            if g:
                lines.extend(
                    [
                        f"### {dim_names.get(dim, dim.value)}",
                        f"- **O que avaliamos:** {g.what_is_evaluated}",
                        f"- **Por que importa:** {g.why_it_matters}",
                    ]
                )
                if ds.is_passing:
                    lines.append(
                        "- **Conformidade técnica:** Critério atendido integralmente em conformidade com as diretrizes da plataforma."
                    )
                else:
                    lines.append(f"- **Como resolver na prática:** {g.how_to_fix}")
                lines.append("")

        return "\n".join(lines)
