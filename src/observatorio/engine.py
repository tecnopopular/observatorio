"""
src/observatorio/engine.py
Orquestrador em lote e pipeline de monitoramento contínuo do Observatório TecnoPopular.
Implementa a execução concorrente por iniciativa com invalidação de cache via SHA-256 e preservação assíncrona.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .catalogo import assign_tool_ids, slugify, validar_catalogo
from .evaluator import CivicEvaluator
from .fetch import MAX_PAYLOAD_BYTES, ContentFetcher, FetchResult, build_fetcher
from .models import (
    AuditLevel,
    CivicAuditReport,
)
from .preservation import archive_target, calculate_sha256, check_content_changed
from .retiradas import REGISTRO_PADRAO, chave, chaves_retiradas
from .retiradas import carregar as carregar_retiradas
from .robots import RobotsPolicy
from .semantic import SemanticClassifier, build_classifier
from .state import build_manifest, changed_since, load_manifest, write_manifest

DEFAULT_CACHE_DIR = Path("data/raw/html_cache")


def fetch_snapshot(
    target_url: str,
    tool_id: str,
    timeout: int = 8,
    refresh_cache: bool = False,
    fetcher: ContentFetcher | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> FetchResult:
    """
    Obtém o conteúdo público de uma iniciativa, com cache local por identificador.

    A aquisição real é delegada ao provedor (`fetch.HttpFetcher` ou `FirecrawlFetcher`).
    Falha de rede não levanta exceção: devolve `FetchResult` com `error` preenchido, para
    que uma iniciativa indisponível não interrompa a esteira das demais.

    O cache guarda o corpo e um sidecar `.meta.json` com o tamanho declarado e a marca de
    truncamento: uma página lida parcialmente continua declarada como parcial no ciclo seguinte,
    em vez de virar silenciosamente uma leitura completa.

    Args:
        target_url: URL pública da iniciativa.
        tool_id: Identificador estável, usado como nome dos arquivos de cache.
        timeout: Tempo limite, em segundos, da requisição de rede.
        refresh_cache: Quando verdadeiro, ignora o cache local e busca o conteúdo de novo.
        fetcher: Provedor de aquisição já construído; quando nulo, usa o provedor HTTP padrão.
        cache_dir: Diretório onde o corpo e os metadados do cache são gravados.
        max_bytes: Limite de bytes lidos por página.

    Returns:
        Resultado da aquisição, com `from_cache` verdadeiro quando veio do cache local.
    """
    cache_path = cache_dir / f"{tool_id}.html"
    meta_path = cache_dir / f"{tool_id}.meta.json"

    if cache_path.exists() and not refresh_cache:
        try:
            raw_html = cache_path.read_bytes().decode("utf-8", errors="ignore")
            if raw_html:
                metadados = {}
                if meta_path.exists():
                    try:
                        metadados = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        metadados = {}
                tamanho = len(raw_html.encode("utf-8"))
                return FetchResult(
                    url=target_url,
                    provider="cache",
                    html=raw_html,
                    latency_ms=0,
                    content_bytes=tamanho,
                    declared_bytes=metadados.get("declared_bytes") or tamanho,
                    truncated=bool(metadados.get("truncated")),
                    from_cache=True,
                )
        except Exception:
            pass

    provedor = fetcher or build_fetcher("http", timeout=timeout, max_bytes=max_bytes)
    resultado = provedor.fetch(target_url)
    if resultado.ok:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(resultado.html.encode("utf-8"))
            meta_path.write_text(
                json.dumps(
                    {
                        "declared_bytes": resultado.declared_bytes,
                        "truncated": resultado.truncated,
                        "provider": resultado.provider,
                        "limit_bytes": max_bytes,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    return resultado


def audit_single_initiative(
    item: dict,
    evaluator: CivicEvaluator,
    previous_audits: dict[str, dict],
    refresh_cache: bool = False,
    skip_archive: bool = False,
    timeout: int = 8,
    fetcher: ContentFetcher | None = None,
    classifier: SemanticClassifier | None = None,
    max_bytes: int = MAX_PAYLOAD_BYTES,
    robots_policy: RobotsPolicy | None = None,
) -> CivicAuditReport | None:
    """Inspeciona e avalia uma única iniciativa cívica.

    O ciclo tem três etapas, na ordem em que uma depende da outra: aquisição do HTML
    público da iniciativa, invalidação do laudo anterior por SHA-256 do corpo inspecionado
    e, quando o conteúdo mudou, reavaliação completa com preservação no Internet Archive.

    Nada é inferido da descrição do catálogo: fonte declarada é campo explícito do registro
    (`fontes_primarias`). Inventar "TSE" a partir da palavra "votação" transformaria o
    catálogo em evidência de si mesmo.

    O tamanho reportado vem do `Content-Length` quando existe, porque é o tamanho real do
    recurso; na ausência do cabeçalho, vale a contagem dos bytes efetivamente lidos.

    A leitura só é considerada útil quando houve corpo e o corpo é a página da iniciativa,
    não uma tela de autenticação ou bloqueio. Sem leitura útil, a nota permanece nula e o
    motivo é registrado no laudo, em vez de o silêncio do provedor virar nota zero.

    Conteúdo inalterado reaproveita o laudo anterior. Se esse laudo não validar contra o
    modelo atual, a iniciativa é reavaliada normalmente em vez de o ciclo falhar.

    Args:
        item: Registro do catálogo com `nome`, `url` e, opcionalmente, `repositorio`,
            `fontes_primarias` e `_tool_id`.
        evaluator: Avaliador que produz o laudo a partir do conteúdo coletado.
        previous_audits: Laudos do ciclo anterior indexados por `tool_id`.
        refresh_cache: Quando verdadeiro, ignora o cache local e busca o conteúdo de novo.
        skip_archive: Quando verdadeiro, não solicita preservação no Internet Archive.
        timeout: Tempo limite, em segundos, da requisição de rede.
        fetcher: Provedor de aquisição já construído; quando nulo, usa o provedor HTTP padrão.
        classifier: Classificador semântico opcional, aplicado sobre o texto da página.
        max_bytes: Limite de bytes lidos por página.
        robots_policy: Política de `robots.txt` a consultar antes da leitura; quando nula,
            a leitura não é verificada.

    Returns:
        Laudo da iniciativa, reaproveitado do ciclo anterior ou recém-emitido. Nulo quando o
        site proíbe a leitura no `robots.txt`: nesse caso não há laudo, e a entrada permanece
        apenas no catálogo.
    """
    name = item["nome"]
    url = item["url"]
    slug = item.get("_tool_id") or slugify(name)

    if robots_policy is not None:
        decisao = robots_policy.decidir(url)
        if not decisao.allowed:
            print(f" [PULA] {name[:28]:<28} | robots.txt | {decisao.detail}")
            return None

    declared_repo = item.get("repositorio")
    if not declared_repo and any(
        h in url for h in ["github.com", "gitlab.com", "codeberg.org", "huggingface.co"]
    ):
        declared_repo = url

    declared_sources = item.get("fontes_primarias", [])

    snapshot = fetch_snapshot(
        url, slug, timeout=timeout, refresh_cache=refresh_cache, fetcher=fetcher, max_bytes=max_bytes
    )
    raw_html = snapshot.html
    latency_ms = snapshot.latency_ms
    html_bytes = snapshot.declared_bytes or snapshot.content_bytes
    current_sha256 = calculate_sha256(raw_html) if raw_html else ""

    inspecionado = bool(raw_html) and not snapshot.access_gate
    motivo_sem_leitura = None
    if snapshot.access_gate:
        motivo_sem_leitura = snapshot.error or "acesso restrito"
    elif not raw_html:
        motivo_sem_leitura = snapshot.error or "sem conteúdo legível"

    prev = previous_audits.get(slug)
    prev_sha256 = prev.get("html_sha256") if prev else None
    prev_archive = prev.get("archive_url") if prev else None

    if (
        prev
        and not refresh_cache
        and current_sha256
        and not check_content_changed(current_sha256, prev_sha256)
    ):
        print(f" [SKIP] {name[:28]:<28} | Inalterado | SHA: {current_sha256[:10]}... (Cache)")
        try:
            return CivicAuditReport.model_validate(prev)
        except Exception:
            pass

    archive_url = prev_archive
    if not skip_archive and url.startswith("http"):
        new_archive = archive_target(url)
        if new_archive:
            archive_url = new_archive

    report = evaluator.evaluate_content(
        tool_id=slug,
        tool_name=name,
        target_url=url,
        raw_html=raw_html,
        declared_repo=declared_repo,
        declared_sources=declared_sources,
        response_time_ms=latency_ms,
        html_bytes=html_bytes,
        html_sha256=current_sha256 if current_sha256 else None,
        archive_url=archive_url,
        fetch_provider=snapshot.provider,
        http_status=snapshot.status_code,
        bytes_scanned=snapshot.content_bytes,
        inspection_truncated=snapshot.truncated,
        inspection_limit_bytes=max_bytes,
        inspected=inspecionado,
        inspection_reason=motivo_sem_leitura,
        classifier=classifier,
    )

    tag = (
        "[SELO]"
        if report.status == AuditLevel.AUDITADO
        else (
            "[DIR ]"
            if report.status == AuditLevel.CATALOGADO
            else (
                "[REV ]"
                if report.status == AuditLevel.EM_REVISAO
                else ("[S/LEI]" if report.status == AuditLevel.NAO_INSPECIONADO else "[BLOQ]")
            )
        )
    )
    nota = f"{report.total_score:4.1f}" if report.total_score is not None else " n/d"
    print(
        f" {tag} {report.tool_name[:28]:<28} | Score: {nota} | {report.status.value.upper():<10} | {report.badge_label}"
    )

    return report


def chave_do_alvo(item: dict) -> str:
    """Devolve a chave comparável do endereço de um registro do catálogo.

    Args:
        item: Registro do catálogo, com a chave `url`.

    Returns:
        Chave normalizada, comparável com o registro de retiradas.
    """
    return chave(item.get("url", ""))


def run_batch_audit(
    input_file: Path,
    output_file: Path,
    max_workers: int = 10,
    refresh_cache: bool = False,
    skip_archive: bool = False,
    timeout: int = 8,
    manifest_file: Path = Path("data/estado_hashes.json"),
    fetch_provider: str = "http",
    semantic: str = "off",
    max_bytes: int = MAX_PAYLOAD_BYTES,
    fetcher: ContentFetcher | None = None,
    robots_policy: RobotsPolicy | None = None,
    respect_robots: bool = True,
    retiradas_file: Path = REGISTRO_PADRAO,
) -> list[CivicAuditReport]:
    """
    Executa a bateria completa do observatório sobre as iniciativas catalogadas.

    Grava dois artefatos com papéis distintos:
    - output_file: laudo completo (publicação). Destinado ao site, release ou dataset.
    - manifest_file: manifesto compacto de estado (versionado no git).

    O `tool_id` é a identidade do laudo e do cache. Nomes repetidos no catálogo colidiriam
    entre si, e um sobrescreveria o outro em silêncio; por isso a atribuição desambigua com
    sufixo e as colisões são relatadas em vez de escondidas.

    O manifesto anterior é carregado antes da esteira porque é a memória durável do ciclo:
    guarda hash, nota e nível de cada iniciativa, e é o que permite contar quantas foram
    realmente reavaliadas.

    Args:
        input_file: Catálogo de iniciativas em JSON.
        output_file: Caminho do laudo completo a gravar.
        max_workers: Número de threads usadas na execução concorrente.
        refresh_cache: Quando verdadeiro, ignora o cache local e busca o conteúdo de novo.
        skip_archive: Quando verdadeiro, não solicita preservação no Internet Archive.
        timeout: Tempo limite, em segundos, de cada requisição de rede.
        manifest_file: Caminho do manifesto compacto de estado.
        fetch_provider: Provedor de aquisição do conteúdo (`http` ou `firecrawl`).
        semantic: Camada semântica aplicada ao texto (`off` ou `jev`).
        max_bytes: Limite de bytes lidos por página.
        fetcher: Provedor de aquisição já construído; quando nulo, é construído a partir
            de `fetch_provider`.
        robots_policy: Política de `robots.txt` já construída; quando nula e
            `respect_robots` é verdadeiro, uma política nova é criada com o `timeout` do ciclo.
        respect_robots: Quando verdadeiro, sites que proíbem a leitura em `robots.txt` não
            recebem laudo e permanecem apenas no catálogo.
        retiradas_file: Registro de retiradas acatadas. Um endereço retirado a pedido não é
            auditado, mesmo que ainda conste do catálogo.

    Returns:
        Laudos do ciclo, ordenados por pontuação total decrescente; iniciativas sem nota
        ficam ao final. Iniciativas puladas por `robots.txt` não aparecem na lista.

    Raises:
        FileNotFoundError: Quando o arquivo de catálogo não existe.
    """
    if not input_file.exists():
        raise FileNotFoundError(f"Arquivo de catálogo não encontrado: {input_file}")

    with open(input_file, encoding="utf-8") as f:
        targets = json.load(f)

    problemas = validar_catalogo(targets)
    if problemas:
        detalhe = "\n".join(f"        {p}" for p in problemas)
        raise ValueError(f"Catálogo inválido em {input_file}:\n{detalhe}")

    registro_retiradas = carregar_retiradas(retiradas_file)
    retirados = chaves_retiradas(registro_retiradas)
    if retirados:
        remanescentes = [t for t in targets if chave_do_alvo(t) in retirados]
        for item in remanescentes:
            print(f" [RET ] {item['nome'][:28]:<28} | retirada | pedido acatado, presente no catálogo")
        if remanescentes:
            print(
                f"[AVISO] {len(remanescentes)} iniciativa(s) retirada(s) ainda no catálogo: remova a entrada."
            )
        targets = [t for t in targets if chave_do_alvo(t) not in retirados]

    colisoes = assign_tool_ids(targets)
    if colisoes:
        print(f"[AVISO] {len(colisoes)} nome(s) repetido(s) no catálogo — tool_id desambiguado:")
        for base, novo, url in colisoes:
            print(f"        {base} -> {novo} ({url})")

    previous_audits: dict[str, dict] = {}
    if output_file.exists():
        try:
            with open(output_file, encoding="utf-8") as f:
                raw_prev = json.load(f)
                if isinstance(raw_prev, list):
                    previous_audits = {p["tool_id"]: p for p in raw_prev if "tool_id" in p}
        except Exception:
            previous_audits = {}

    previous_manifest = load_manifest(manifest_file)
    if previous_manifest.entries:
        print(f"[*] Manifesto anterior carregado: {previous_manifest.total} entradas ({manifest_file})")

    print(f"[*] Iniciando esteira de monitoramento cívico sobre {len(targets)} iniciativas...")
    print(f"[*] Workers: {max_workers} | Refresh: {refresh_cache} | Skip Archive: {skip_archive}")
    print(f"[*] Provedor de aquisição: {fetch_provider}")
    print(f"[*] Classificador semântico: {semantic}")
    print(f"[*] Limite de leitura por página: {max_bytes:,} bytes")
    print("-" * 80)

    evaluator = CivicEvaluator()
    fetcher = fetcher or build_fetcher(fetch_provider, timeout=timeout, max_bytes=max_bytes)
    classifier = build_classifier(semantic)
    if robots_policy is None and respect_robots:
        robots_policy = RobotsPolicy(timeout=timeout)
    print(f"[*] robots.txt: {'consultado' if robots_policy is not None else 'ignorado'}")
    reports: list[CivicAuditReport] = []
    pulados: list[tuple] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                audit_single_initiative,
                t,
                evaluator,
                previous_audits,
                refresh_cache,
                skip_archive,
                timeout,
                fetcher,
                classifier,
                max_bytes,
                robots_policy,
            ): t
            for t in targets
        }
        for fut in as_completed(futures):
            try:
                rep = fut.result()
                if rep is None:
                    pulados.append((futures[fut].get("nome"), "robots.txt"))
                else:
                    reports.append(rep)
            except Exception as e:
                t = futures[fut]
                print(f" [-] Erro ao processar {t.get('nome')}: {e}")

    reports.sort(key=lambda r: r.total_score if r.total_score is not None else -1.0, reverse=True)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    serialized = [r.model_dump(mode="json") for r in reports]
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(serialized, f, ensure_ascii=False, indent=2)

    counts = {
        AuditLevel.AUDITADO.value: sum(1 for r in reports if r.status == AuditLevel.AUDITADO),
        AuditLevel.CATALOGADO.value: sum(1 for r in reports if r.status == AuditLevel.CATALOGADO),
        AuditLevel.EM_REVISAO.value: sum(1 for r in reports if r.status == AuditLevel.EM_REVISAO),
        AuditLevel.REJEITADO.value: sum(1 for r in reports if r.status == AuditLevel.REJEITADO),
        AuditLevel.NAO_INSPECIONADO.value: sum(1 for r in reports if r.status == AuditLevel.NAO_INSPECIONADO),
    }

    print("\n" + "=" * 60)
    print("      CONSOLIDAÇÃO DO OBSERVATÓRIO TECNOPOPULAR")
    print("=" * 60)
    print(f"  [AUDITADO] Certificado TecnoPopular (Selo):     {counts['auditado']}")
    print(f"  [CATALOG ] Diretório Aberto:                     {counts['catalogado']}")
    print(f"  [REVISAO ] Pendências Técnicas:                  {counts['em_revisao']}")
    print(f"  [REJEITA ] Bloqueadores Críticos:                {counts['rejeitado']}")
    print(f"  [SEM LEIT] Não Inspecionadas:                    {counts['nao_inspecionado']}")
    print(f"  [TOTAL   ] Iniciativas Inspecionadas:            {len(reports)}")
    if pulados:
        print(f"  [PULADAS ] Proibidas por robots.txt:             {len(pulados)}")
    print(f"\n[+] Relatório estruturado salvo com sucesso em: {output_file}")

    completas = sum(1 for r in reports if r.coverage and r.coverage.complete)
    sem_cobertura = sum(1 for r in reports if not r.coverage)
    parciais = len(reports) - completas - sem_cobertura
    print(
        f"[*] Cobertura: {completas} inspeções completas | {parciais} parciais | {sem_cobertura} sem metadado"
    )

    manifest = build_manifest(reports)
    manifest_bytes = write_manifest(manifest_file, manifest)
    reavaliadas = changed_since(previous_manifest, reports)
    print(
        f"[+] Manifesto de estado salvo em: {manifest_file} ({manifest_bytes} bytes, {manifest.total} entradas)"
    )
    print(f"[+] Iniciativas novas ou com hash alterado neste ciclo: {len(reavaliadas)}")

    return reports
