"""Módulo de auditoria de infraestrutura, telemetria de rede e agregação do Radar Cívico.

Executa a detecção de tecnologias de borda, cabeçalhos de servidores, rastreadores
comerciais e serviços de telemetria ética, sintetizando o diagnóstico contínuo do
ecossistema cívico brasileiro.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    EVALUATOR_VERSION,
    AuditLevel,
    CivicAuditReport,
    HostingProvider,
    TelemetryInspection,
)

COMMERCIAL_TRACKERS: dict[str, list[str]] = {
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
    "Hotjar / Clarity (Mapas de Calor)": [
        r"static\.hotjar\.com",
        r"www\.clarity\.ms/tag/([a-zA-Z0-9]+)",
    ],
    "TikTok Pixel": [
        r"analytics\.tiktok\.com/i18n/pixel",
    ],
    "Google AdSense": [
        r"pagead2\.googlesyndication\.com",
    ],
}

ETHICAL_ANALYTICS: dict[str, list[str]] = {
    "Cloudflare Web Analytics": [
        r"static\.cloudflareinsights\.com/beacon\.min\.js",
        r"cloudflareinsights.*token['\"]?:\s*['\"]([a-f0-9]+)['\"]",
    ],
    "Plausible Analytics": [
        r"plausible\.io/js/script.*\.js",
        r"data-domain=['\"]([^'\"]+)['\"].*plausible",
    ],
    "Umami Analytics": [
        r"umami\.is/script\.js",
        r"data-website-id=['\"]([a-f0-9\-]+)['\"]",
    ],
    "PostHog": [
        r"app\.posthog\.com",
        r"posthog\.init\(['\"]([^'\"]+)['\"]",
    ],
}

DIMENSION_LABELS: dict[str, tuple[str, str]] = {
    "acessibilidade_desempenho": (
        "Acessibilidade e Desempenho",
        "Leveza de carregamento, ausência de anúncios comerciais e latência na borda.",
    ),
    "enquadramento_republicano": (
        "Enquadramento Republicano",
        "Vocabulário de utilidade pública, respeito ao Marco Civil e foco em direitos.",
    ),
    "privacidade_e_soberania": (
        "Privacidade e Soberania",
        "Ausência de pixels comerciais de Big Techs e proteção contra coleta indevida de dados.",
    ),
    "proveniencia_dados": (
        "Proveniência de Dados Oficiais",
        "Citação expressa de órgãos públicos (TSE, IBGE, DATASUS) com links auditáveis.",
    ),
    "codigo_aberto_floss": (
        "Código Aberto (FLOSS)",
        "Disponibilização de repositório público com licença livre reconhecida pela OSI.",
    ),
}


def get_radar_timestamps(dt: datetime | None = None) -> tuple[str, str, str]:
    """Retorna timestamps formatados em UTC e horário de Brasília (BRT).

    Args:
        dt: Datahora opcional; quando ausente, usa o momento atual em UTC.

    Returns:
        Tupla contendo (string ISO, data formatada em português, datahora formatada BRT).
    """
    tz_br = timezone(timedelta(hours=-3))
    agora = (dt or datetime.now(UTC)).astimezone(tz_br)
    iso_str = agora.isoformat()
    meses = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    mes_nome = meses[agora.month - 1]
    formatted_date = f"{agora.day} de {mes_nome.capitalize()} de {agora.year}"
    formatted_datetime = (
        f"{agora.day:02d}/{agora.month:02d}/{agora.year} às {agora.hour:02d}:{agora.minute:02d} (BRT)"
    )
    return iso_str, formatted_date, formatted_datetime


def resolve_hosting_provider(
    server: str | None,
    headers: dict[str, str] | None = None,
    url: str = "",
    is_online: bool = True,
) -> str:
    """Classifica a infraestrutura de hospedagem da aplicação a partir de cabeçalhos e domínio.

    Args:
        server: Valor do cabeçalho Server ou x-powered-by.
        headers: Dicionário normalizado com todos os cabeçalhos HTTP recebidos.
        url: Endereço da aplicação.
        is_online: Indica se o serviço respondeu com status operacional.

    Returns:
        Nome padronizado do provedor segundo a taxonomia do radar.
    """
    if not is_online:
        return HostingProvider.INACTIVE.value

    srv = (server or "").lower()
    url_low = url.lower()
    hdr = {k.lower(): v.lower() for k, v in (headers or {}).items()}

    if "cloudflare" in srv or "cf-ray" in hdr or "pages.dev" in url_low or "workers.dev" in url_low:
        return HostingProvider.CLOUDFLARE.value

    if "vercel" in srv or "x-vercel-id" in hdr or "vercel.app" in url_low:
        return HostingProvider.VERCEL.value

    if "github" in srv or "github.io" in url_low:
        return HostingProvider.GITHUB_PAGES.value

    if "netlify" in srv or "x-nf-request-id" in hdr or "netlify.app" in url_low:
        return HostingProvider.NETLIFY.value

    if (
        "amazons3" in srv
        or "awselb" in srv
        or "cloudfront" in srv
        or "amazonaws.com" in url_low
        or "x-amz-cf-id" in hdr
    ):
        return HostingProvider.AWS.value

    if any(
        kw in srv
        for kw in (
            "nginx",
            "apache",
            "ubuntu",
            "debian",
            "caddy",
            "gunicorn",
            "uvicorn",
            "openresty",
        )
    ):
        return HostingProvider.SOVEREIGN_VPS.value

    return HostingProvider.OTHER.value


def detect_analytics(
    html_text: str,
) -> tuple[list[str], dict[str, list[str]], list[str], list[str]]:
    """Inspeciona o corpo HTML em busca de assinaturas de rastreamento comercial e telemetria ética.

    Args:
        html_text: Conteúdo HTML público da página inspecionada.

    Returns:
        Quádrupla contendo (todas as assinaturas, dicionário de evidências, comerciais, éticas).
    """
    if not html_text:
        return [], {}, [], []

    detected_all: list[str] = []
    details: dict[str, list[str]] = {}
    commercial_detected: list[str] = []
    ethical_detected: list[str] = []

    for name, patterns in COMMERCIAL_TRACKERS.items():
        matches: list[str] = []
        for p in patterns:
            found = re.findall(p, html_text, re.IGNORECASE)
            if found:
                for match_item in found:
                    token = match_item if isinstance(match_item, str) else match_item[0]
                    if token and token not in matches:
                        matches.append(token)
        if matches:
            detected_all.append(name)
            commercial_detected.append(name)
            details[name] = matches

    for name, patterns in ETHICAL_ANALYTICS.items():
        matches = []
        for p in patterns:
            found = re.findall(p, html_text, re.IGNORECASE)
            if found:
                for match_item in found:
                    token = match_item if isinstance(match_item, str) else match_item[0]
                    if token and token not in matches:
                        matches.append(token)
        if matches:
            detected_all.append(name)
            ethical_detected.append(name)
            details[name] = matches

    return detected_all, details, commercial_detected, ethical_detected


def inspect_target_telemetry(
    nome: str,
    url: str,
    tool_id: str | None = None,
    categoria: str = "Iniciativa Cívica",
    status_code: int | None = None,
    final_url: str | None = None,
    server: str | None = None,
    headers: dict[str, str] | None = None,
    raw_html: str = "",
    error: str | None = None,
) -> TelemetryInspection:
    """Consolida os achados de rede, hospedagem e rastreamento em um registro tipado.

    Args:
        nome: Nome público da iniciativa.
        url: Endereço web direto da aplicação.
        tool_id: Slug único estável da ferramenta.
        categoria: Categoria temática no catálogo.
        status_code: Código numérico de status HTTP observado.
        final_url: Endereço atingido após redirecionamentos.
        server: Identificador declarado do servidor HTTP.
        headers: Cabeçalhos HTTP capturados na resposta.
        raw_html: Conteúdo HTML público da página.
        error: Mensagem de erro de conexão, quando houver.

    Returns:
        Instância validada de TelemetryInspection.
    """
    is_online = bool(status_code and 200 <= status_code < 400 and not error)
    provider = resolve_hosting_provider(server, headers, url, is_online=is_online)
    detected_all, details, _commercial, _ethical = detect_analytics(raw_html)

    return TelemetryInspection(
        nome=nome,
        categoria=categoria,
        url=url,
        tool_id=tool_id,
        status_code=status_code,
        final_url=final_url or url,
        server=server,
        hosting_provider=provider,
        is_online=is_online,
        analytics_detected=detected_all,
        analytics_details=details,
        error=error,
    )


def build_radar_dataset(
    audits: list[CivicAuditReport],
    telemetrias: list[TelemetryInspection],
    datas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compila o conjunto consolidado de dados e métricas para renderização do Radar Cívico.

    Args:
        audits: Lista de laudos de auditoria emitidos pelo avaliador cívico.
        telemetrias: Lista de inspeções de telemetria e rede.
        datas: Lista cadastral original de iniciativas do catálogo.

    Returns:
        Dicionário conforme a especificação consumida pelo portal web.
    """
    iso_time, date_str, datetime_str = get_radar_timestamps()

    tel_by_tool: dict[str, TelemetryInspection] = {t.tool_id: t for t in telemetrias if t.tool_id}
    tel_by_url: dict[str, TelemetryInspection] = {t.url.rstrip("/").lower(): t for t in telemetrias}
    tel_by_name: dict[str, TelemetryInspection] = {t.nome.strip().lower(): t for t in telemetrias}

    data_by_tool: dict[str, dict[str, Any]] = {d.get("tool_id"): d for d in datas if d.get("tool_id")}
    data_by_url: dict[str, dict[str, Any]] = {d.get("url", "").rstrip("/").lower(): d for d in datas}
    data_by_name: dict[str, dict[str, Any]] = {d.get("nome", "").strip().lower(): d for d in datas}

    iniciativas: list[dict[str, Any]] = []
    for report in audits:
        tid = report.tool_id
        tname = report.tool_name
        turl = report.target_url.rstrip("/").lower()

        d_item = (
            data_by_tool.get(tid) or data_by_url.get(turl) or data_by_name.get(tname.strip().lower()) or {}
        )
        t_item = tel_by_tool.get(tid) or tel_by_url.get(turl) or tel_by_name.get(tname.strip().lower())

        analytics_detected = t_item.analytics_detected if t_item else []
        is_clean_analytics = len(analytics_detected) == 0
        is_online = (
            t_item.is_online if t_item else (report.status in (AuditLevel.AUDITADO, AuditLevel.CATALOGADO))
        )
        server_str = (
            (t_item.server if t_item else None)
            or (report.performance.server if report.performance else None)
            or "N/A"
        )
        status_code = (
            (t_item.status_code if t_item else None)
            or (report.performance.http_status if report.performance else None)
            or (200 if is_online else None)
        )

        categoria = d_item.get("categoria", "Iniciativa Cívica")
        descricao = d_item.get("descricao") or ""
        if not descricao and report.summary_markdown:
            descricao = report.summary_markdown.split("\n\n")[0]
        if not descricao:
            descricao = "Ferramenta auditada pelo Protocolo de Avaliação Cívica TecnoPopular."

        direct_url = report.target_url or d_item.get("url") or f"/auditoria/{tid}.html"
        dim_scores_dump = {
            k.value if hasattr(k, "value") else str(k): v.model_dump(mode="json")
            for k, v in report.dimension_scores.items()
        }

        iniciativas.append(
            {
                "id": tid,
                "tool_id": tid,
                "nome": tname,
                "categoria": categoria,
                "descricao": descricao,
                "url": direct_url,
                "url_egress": direct_url,
                "laudo_url": f"/auditoria/{tid}.html",
                "status": report.status.value,
                "total_score": round(float(report.total_score or 0.0), 1),
                "badge_label": report.badge_label,
                "has_critical_blocker": report.has_critical_blocker,
                "critical_blockers": report.critical_blockers,
                "dimension_scores": dim_scores_dump,
                "telemetria": {
                    "is_online": is_online,
                    "server": server_str,
                    "status_code": status_code,
                    "analytics_detected": analytics_detected,
                    "is_clean": is_clean_analytics,
                },
                "evaluated_at": report.evaluated_at.isoformat(),
            }
        )

    iniciativas.sort(key=lambda x: -x["total_score"])

    total_iniciativas = len(iniciativas)
    total_telemetria = len(telemetrias) if telemetrias else total_iniciativas
    status_counts = Counter(item["status"] for item in iniciativas)

    cnt_certified = status_counts.get(AuditLevel.AUDITADO.value, 0)
    cnt_cataloged = status_counts.get(AuditLevel.CATALOGADO.value, 0)
    cnt_rejected = status_counts.get(AuditLevel.REJEITADO.value, 0)
    cnt_review = status_counts.get(AuditLevel.EM_REVISAO.value, 0)

    pct_certified = round((cnt_certified / total_iniciativas) * 100, 1) if total_iniciativas else 0.0
    pct_cataloged = round((cnt_cataloged / total_iniciativas) * 100, 1) if total_iniciativas else 0.0
    pct_rejected = round((cnt_rejected / total_iniciativas) * 100, 1) if total_iniciativas else 0.0
    pct_review = round((cnt_review / total_iniciativas) * 100, 1) if total_iniciativas else 0.0

    online_count = (
        sum(1 for t in telemetrias if t.is_online)
        if telemetrias
        else sum(1 for item in iniciativas if item["telemetria"]["is_online"])
    )
    offline_count = max(0, total_telemetria - online_count)
    pct_online = round((online_count / total_telemetria) * 100, 1) if total_telemetria else 0.0
    pct_offline = round((offline_count / total_telemetria) * 100, 1) if total_telemetria else 0.0

    infra_groups: dict[str, int] = defaultdict(int)
    if telemetrias:
        for t in telemetrias:
            infra_groups[t.hosting_provider] += 1
    else:
        for item in iniciativas:
            srv = item["telemetria"].get("server")
            prov = resolve_hosting_provider(srv, url=item["url"], is_online=item["telemetria"]["is_online"])
            infra_groups[prov] += 1

    sorted_infra = [
        {
            "name": name,
            "count": cnt,
            "pct": round((cnt / total_telemetria) * 100, 1),
        }
        for name, cnt in sorted(infra_groups.items(), key=lambda x: -x[1])
    ]

    trackers_summary: Counter[str] = Counter()
    if telemetrias:
        for t in telemetrias:
            if not t.analytics_detected:
                trackers_summary["Zero Trackers (Nenhum rastreador identificado)"] += 1
            else:
                for d in t.analytics_detected:
                    trackers_summary[d] += 1
    else:
        for item in iniciativas:
            dets = item["telemetria"].get("analytics_detected", [])
            if not dets:
                trackers_summary["Zero Trackers (Nenhum rastreador identificado)"] += 1
            else:
                for d in dets:
                    trackers_summary[d] += 1

    sorted_trackers = []
    for tracker, cnt in trackers_summary.most_common():
        is_clean = (
            "Zero Trackers" in tracker
            or "Cloudflare Web Analytics" in tracker
            or "Plausible" in tracker
            or "Umami" in tracker
        )
        sorted_trackers.append(
            {
                "name": tracker,
                "count": cnt,
                "pct": round((cnt / total_telemetria) * 100, 1),
                "is_clean": is_clean,
            }
        )

    dim_scores: dict[str, list[float]] = defaultdict(list)
    dim_passing: dict[str, int] = defaultdict(int)
    for report in audits:
        for dim_key, dim_score in report.dimension_scores.items():
            key_str = dim_key.value if hasattr(dim_key, "value") else str(dim_key)
            dim_scores[key_str].append(dim_score.raw_score)
            if dim_score.is_passing:
                dim_passing[key_str] += 1

    dimension_metrics: dict[str, dict[str, Any]] = {}
    for dim_key, (dim_title, dim_desc) in DIMENSION_LABELS.items():
        vals = dim_scores.get(dim_key, [0.0])
        avg = sum(vals) / len(vals) if vals else 0.0
        passing = dim_passing.get(dim_key, 0)
        pct = (passing / len(vals)) * 100 if vals else 0.0
        dimension_metrics[dim_key] = {
            "title": dim_title,
            "description": dim_desc,
            "avg": round(avg, 1),
            "passing_count": passing,
            "passing_pct": round(pct, 1),
            "total": len(vals),
        }

    category_counts = Counter(item["categoria"] for item in iniciativas)
    sorted_categories = [
        {"name": cat, "count": cnt, "pct": round((cnt / total_iniciativas) * 100, 1)}
        for cat, cnt in category_counts.most_common()
    ]

    return {
        "metadata": {
            "generated_at": iso_time,
            "formatted_date": date_str,
            "formatted_datetime": datetime_str,
            "protocol_name": "Protocolo de Avaliação Cívica TecnoPopular",
            "protocol_version": EVALUATOR_VERSION,
            "total_iniciativas": total_iniciativas,
            "total_telemetria": total_telemetria,
            "license": "Creative Commons CC-BY 4.0",
        },
        "summary": {
            "total_iniciativas": total_iniciativas,
            "total_telemetria": total_telemetria,
            "certified_count": cnt_certified,
            "certified_pct": pct_certified,
            "cataloged_count": cnt_cataloged,
            "cataloged_pct": pct_cataloged,
            "rejected_count": cnt_rejected,
            "rejected_pct": pct_rejected,
            "review_count": cnt_review,
            "review_pct": pct_review,
            "online_count": online_count,
            "online_pct": pct_online,
            "offline_count": offline_count,
            "offline_pct": pct_offline,
            "infra_breakdown": sorted_infra,
            "trackers_breakdown": sorted_trackers,
            "dimensions": dimension_metrics,
            "categories": sorted_categories,
        },
        "iniciativas": iniciativas,
    }


def export_radar_artifacts(
    dataset: dict[str, Any],
    telemetrias: list[TelemetryInspection],
    radar_path: Path,
    telemetry_path: Path,
) -> None:
    """Grava os datasets JSON do radar e da telemetria no disco.

    Args:
        dataset: Estrutura agregada do radar com metadados e sumário.
        telemetrias: Lista de inspeções individuais de telemetria.
        radar_path: Caminho de saída do radar_iniciativas.json.
        telemetry_path: Caminho de saída do telemetria_iniciativas.json.
    """
    radar_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)

    with open(radar_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    serialized_tel = [t.model_dump(mode="json") for t in telemetrias]
    with open(telemetry_path, "w", encoding="utf-8") as f:
        json.dump(serialized_tel, f, ensure_ascii=False, indent=2)
