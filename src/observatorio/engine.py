"""
src/observatorio/engine.py
Orquestrador em lote e pipeline de monitoramento contínuo do Observatório TecnoPopular.
Implementa a execução concorrente por iniciativa com invalidação de cache via SHA-256 e preservação assíncrona.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .catalog import assign_tool_ids, slugify, validate_catalog
from .evaluator import CivicEvaluator
from .fetch import MAX_PAYLOAD_BYTES, ContentFetcher, FetchResult, HttpFetcher
from .models import (
    EVALUATOR_VERSION,
    ArchiveStatus,
    AuditLevel,
    CivicAuditReport,
)
from .preservation import (
    DEFAULT_ARCHIVE_BUDGET,
    ArchiveJob,
    ArchiveOutcome,
    await_archives,
    calculate_sha256,
    check_content_changed,
    has_credentials,
    submit_archive,
)
from .robots import RobotsPolicy
from .semantic import SemanticClassifier, build_classifier
from .state import (
    ManifestEntry,
    build_manifest,
    changed_since,
    index_by_tool,
    load_manifest,
    write_manifest,
)
from .withdrawals import DEFAULT_REGISTRY, url_key, withdrawn_keys
from .withdrawals import load as carregar_retiradas

SUBMIT_INTERVAL = 12.0
SUBMIT_TIMEOUT = 40
SUBMIT_RETRY_PAUSE = 20.0
SUBMIT_REFUSALS_TO_STOP = 2
SUBMIT_NETWORK_FAILURES_TO_STOP = 3
DEFAULT_ARCHIVE_LIMIT = 10

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

    A aquisição real é delegada a `fetch.HttpFetcher`.
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

    provedor = fetcher or HttpFetcher(timeout=timeout, max_bytes=max_bytes)
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


def _versao_dos_laudos(previous_audits: dict[str, dict]) -> str | None:
    """Lê a versão do protocolo gravada nos laudos reaproveitáveis.

    Reaproveitar laudo de outra versão misturaria critérios no mesmo laudo publicado: a nota viria
    da regra antiga com o carimbo da nova. Quando a versão diverge, a esteira reavalia.

    Args:
        previous_audits: Laudos do ciclo anterior, indexados por `tool_id`.

    Returns:
        Versão do protocolo encontrada nos laudos, ou None quando não há laudo anterior.
    """
    for registro in previous_audits.values():
        versao = registro.get("evaluator_version")
        if versao:
            return str(versao)
    return None


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
    cache_dir: Path = DEFAULT_CACHE_DIR,
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
        cache_dir: Diretório do cache de corpos; testes passam um temporário para não escrever
            no cache do repositório.

    Returns:
        Laudo da iniciativa, reaproveitado do ciclo anterior ou recém-emitido. Nulo quando o
        site proíbe a leitura no `robots.txt`: nesse caso não há laudo, e a entrada permanece
        apenas no catálogo.
    """
    name = item["nome"]
    url = item["url"]
    slug = item.get("_tool_id") or slugify(name)

    if robots_policy is not None:
        decisao = robots_policy.decide(url)
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
        url,
        slug,
        timeout=timeout,
        refresh_cache=refresh_cache,
        fetcher=fetcher,
        cache_dir=cache_dir,
        max_bytes=max_bytes,
    )
    raw_html = snapshot.html
    latency_ms = snapshot.latency_ms
    html_bytes = snapshot.declared_bytes or snapshot.content_bytes
    current_sha256 = calculate_sha256(raw_html) if raw_html else ""

    inspecionado = bool(raw_html) and not snapshot.access_gate
    no_read_reason = None
    if snapshot.access_gate:
        no_read_reason = snapshot.error or "acesso restrito"
    elif not raw_html:
        no_read_reason = snapshot.error or "sem conteúdo legível"

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
    archive_status: ArchiveStatus | None = "reused" if prev_archive else None

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
        archive_status=archive_status,
        fetch_provider=snapshot.provider,
        http_status=snapshot.status_code,
        bytes_scanned=snapshot.content_bytes,
        inspection_truncated=snapshot.truncated,
        inspection_limit_bytes=max_bytes,
        inspected=inspecionado,
        inspection_reason=no_read_reason,
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
                else ("[S/LEI]" if report.status == AuditLevel.NOT_INSPECTED else "[BLOQ]")
            )
        )
    )
    score = f"{report.total_score:4.1f}" if report.total_score is not None else " n/d"
    print(
        f" {tag} {report.tool_name[:28]:<28} | Score: {score} | {report.status.value.upper():<10} | {report.badge_label}"
    )

    return report


def _needs_capture(report: CivicAuditReport, previous: dict[str, ManifestEntry]) -> bool:
    """Indica se o endereço precisa de uma captura nova no Internet Archive.

    Preservar de novo conteúdo idêntico gasta o limite do serviço sem acrescentar registro: o
    snapshot anterior representa exatamente os mesmos bytes. Por isso a captura só é pedida quando
    há conteúdo novo ou quando ainda não há snapshot conhecido.

    Args:
        report: Laudo do ciclo atual.
        previous: Entradas do manifesto anterior, indexadas por `tool_id`.

    Returns:
        True quando o endereço deve ser submetido ao Internet Archive.
    """
    if not report.target_url.startswith("http"):
        return False
    entrada = previous.get(report.tool_id)
    if entrada is None or not entrada.archive_url:
        return True
    return entrada.html_sha256 != report.html_sha256


def _archive_priority(report: CivicAuditReport, previous: dict[str, ManifestEntry]) -> int:
    """Ordena a fila de preservação por urgência de custódia.

    Args:
        report: Laudo do ciclo atual.
        previous: Entradas do manifesto anterior, indexadas por `tool_id`.

    Returns:
        Zero para conteúdo que mudou desde o último snapshot, um para endereço sem snapshot.
    """
    entrada = previous.get(report.tool_id)
    if entrada is not None and entrada.archive_url and entrada.html_sha256 != report.html_sha256:
        return 0
    return 1


@dataclass
class ArchiveBatch:
    """Resultado de uma rodada de submissões ao Internet Archive.

    Attributes:
        resultados: Situação por `tool_id` das capturas submetidas e acompanhadas.
        preteridos: `tool_id` não submetidos, por teto do ciclo ou por recusas consecutivas.
        detalhes: Motivo legível de cada falha, recusa ou reaproveitamento.
    """

    resultados: dict[str, ArchiveOutcome]
    preteridos: list[str]
    detalhes: dict[str, str]


def run_archive_queue(
    items: list[tuple[str, str]],
    limit: int,
    budget: float,
) -> ArchiveBatch:
    """Submete endereços ao Internet Archive em série e acompanha as capturas em andamento.

    O núcleo é compartilhado pelo ciclo de auditoria e pelo passo de preservação avulso: a
    política de ritmo é a mesma nos dois, porque o limite é do serviço, não de quem chama.

    Recusa (`refused`) e falha de rede (`failed`) têm tolerâncias diferentes: recusa é o serviço
    dizendo não, e insistir piora — para na segunda. Falha de rede costuma ser passageira — TLS
    que não completa, conexão derrubada —, então tolera três endereços seguidos antes de parar.

    Args:
        items: Pares (`tool_id`, endereço) na ordem de prioridade.
        limit: Número máximo de submissões nesta rodada.
        budget: Tempo total de espera pelas capturas em andamento, em segundos.

    Returns:
        Resultado da rodada, com os preteridos separados dos submetidos.
    """
    selecionados = items[: max(limit, 0)]
    preteridos = [tool_id for tool_id, _ in items[len(selecionados) :]]
    resultados: dict[str, ArchiveOutcome] = {}
    detalhes: dict[str, str] = {}
    pendentes: dict[str, ArchiveJob] = {}
    recusas_consecutivas = 0
    falhas_de_rede = 0

    for posicao, (tool_id, url) in enumerate(selecionados):
        if posicao:
            time.sleep(SUBMIT_INTERVAL)
        submissao = submit_archive(url, timeout=SUBMIT_TIMEOUT)
        if submissao.status == "failed":
            time.sleep(SUBMIT_RETRY_PAUSE)
            submissao = submit_archive(url, timeout=SUBMIT_TIMEOUT)
        resultados[tool_id] = submissao
        if submissao.job_id and not submissao.url:
            pendentes[tool_id] = ArchiveJob(job_id=submissao.job_id, target_url=url)
        if submissao.detail:
            detalhes[tool_id] = submissao.detail
        if submissao.status == "refused":
            recusas_consecutivas += 1
            falhas_de_rede = 0
        elif submissao.status == "failed":
            falhas_de_rede += 1
            recusas_consecutivas = 0
        else:
            recusas_consecutivas = 0
            falhas_de_rede = 0
        if (
            recusas_consecutivas >= SUBMIT_REFUSALS_TO_STOP
            or falhas_de_rede >= SUBMIT_NETWORK_FAILURES_TO_STOP
        ):
            restantes = selecionados[posicao + 1 :]
            preteridos.extend(tool_id for tool_id, _ in restantes)
            if restantes:
                detalhes[restantes[0][0]] = (
                    "envios interrompidos após falhas consecutivas; adiado para a próxima execução"
                )
            break

    if pendentes:
        print(f"[*] Preservação: acompanhando {len(pendentes)} captura(s) (orçamento de {budget:.0f}s)...")
        for tool_id, resultado in await_archives(pendentes, budget=budget).items():
            resultados[tool_id] = resultado
            if resultado.detail:
                detalhes[tool_id] = resultado.detail

    return ArchiveBatch(resultados=resultados, preteridos=preteridos, detalhes=detalhes)


def run_preservation_pass(
    input_file: Path,
    manifest_file: Path,
    limit: int = DEFAULT_ARCHIVE_LIMIT,
    budget: float = DEFAULT_ARCHIVE_BUDGET,
) -> ArchiveBatch:
    """Executa um passo de preservação sobre o que o manifesto ainda não tem snapshot.

    Não baixa página nenhuma e não emite laudo: lê o catálogo e o manifesto, submete o que está
    sem `archive_url` e grava o resultado no próprio manifesto. É o passo barato que pode rodar
    com frequência própria, porque o Internet Archive aceita poucas capturas por vez — e a
    auditoria completa, que custa uma leitura por iniciativa, continua semanal.

    Args:
        input_file: Catálogo de iniciativas em JSON.
        manifest_file: Manifesto compacto de estado, lido e reescrito.
        limit: Número máximo de submissões nesta execução.
        budget: Tempo total de espera pelas capturas em andamento, em segundos.

    Returns:
        Resultado da rodada, com contagem de capturados, pendentes, recusados e preteridos.

    Raises:
        FileNotFoundError: Quando o arquivo de catálogo não existe.
    """
    if not input_file.exists():
        raise FileNotFoundError(f"Arquivo de catálogo não encontrado: {input_file}")

    with open(input_file, encoding="utf-8") as arquivo:
        targets = json.load(arquivo)

    problemas = validate_catalog(targets)
    if problemas:
        detalhe = "\n".join(f"        {p}" for p in problemas)
        raise ValueError(f"Catálogo inválido em {input_file}:\n{detalhe}")

    assign_tool_ids(targets)
    manifesto = load_manifest(manifest_file)
    entradas = index_by_tool(manifesto)

    fila = sorted(
        (
            (item.get("_tool_id") or slugify(item["nome"]), item["url"])
            for item in targets
            if (item.get("_tool_id") or slugify(item["nome"])) in entradas
            and not entradas[item.get("_tool_id") or slugify(item["nome"])].archive_url
            and str(item.get("url", "")).startswith("http")
        ),
        key=lambda par: par[0],
    )

    modo = "conta do Internet Archive" if has_credentials() else "anônima"
    print(f"[*] Preservação: submissão com {modo}")
    print(
        f"[*] Preservação: {len(fila)} endereço(s) sem snapshot no manifesto | "
        f"submetendo {min(len(fila), max(limit, 0))} (limite {limit} por execução)..."
    )

    lote = run_archive_queue(fila, limit=limit, budget=budget)

    for tool_id, resultado in lote.resultados.items():
        entrada = entradas.get(tool_id)
        if entrada is None:
            continue
        entrada.archive_status = resultado.status
        if resultado.url:
            entrada.archive_url = resultado.url
    for tool_id in lote.preteridos:
        entrada = entradas.get(tool_id)
        if entrada is not None and entrada.archive_status is None:
            entrada.archive_status = "deferred"

    tamanho = write_manifest(manifest_file, manifesto)
    contagem: dict[str, int] = {}
    for resultado in lote.resultados.values():
        contagem[resultado.status] = contagem.get(resultado.status, 0) + 1
    resumo = " | ".join(f"{quantidade} {situacao}" for situacao, quantidade in sorted(contagem.items()))
    print(f"[*] Preservação: {resumo or 'nada submetido'} | {len(lote.preteridos)} preterido(s)")
    for tool_id, detalhe in list(lote.detalhes.items())[:3]:
        print(f"        {tool_id}: {detalhe}")
    print(f"[+] Manifesto de estado salvo em: {manifest_file} ({tamanho} bytes)")
    return lote


def _preserve(
    reports: list[CivicAuditReport],
    previous: dict[str, ManifestEntry],
    skip_archive: bool,
    limit: int,
    budget: float,
) -> None:
    """Preserva no Internet Archive o que o ciclo inspecionou, dentro do que o serviço aceita.

    A fase roda depois da avaliação, em série e com intervalo entre envios, por medições de
    2026-10-24: o Save Page Now não responde a captura síncrona em página pesada, recusa conexões
    quando recebe dezenas de submissões simultâneas (rajada de 6: todas recusadas) e aplica limite
    de requisições mesmo a quem está autenticado — 4 envios com 8 s de intervalo terminaram em
    HTTP 429, enquanto 6 envios com 12 s de intervalo foram aceitos.

    Por isso o ciclo submete poucas capturas por vez, prioriza o conteúdo que mudou e registra em
    cada laudo o que aconteceu: `captured`, `pending`, `refused`, `failed`, `deferred`, `skipped`
    ou `reused`. Endereço preterido pelo limite não se perde: sem snapshot gravado, ele volta à
    fila do ciclo seguinte.

    Falha de rede ganha uma segunda tentativa depois de uma pausa: derrubar o ciclo inteiro na
    primeira instabilidade é caro quando a causa costuma ser passageira, e recusa do serviço
    (`refused`) é outra coisa — essa não melhora com insistência.

    Args:
        reports: Laudos do ciclo, atualizados no lugar.
        previous: Entradas do manifesto anterior, indexadas por `tool_id`.
        skip_archive: Quando verdadeiro, nenhuma captura é pedida e o laudo registra `skipped`.
        limit: Número máximo de submissões por ciclo.
        budget: Tempo total de espera pelas capturas em andamento, em segundos.
    """
    if skip_archive:
        for relatorio in reports:
            if relatorio.archive_status is None:
                relatorio.archive_status = "skipped"
        print("[*] Preservação: dispensada por --skip-archive")
        return

    for relatorio in reports:
        entrada = previous.get(relatorio.tool_id)
        if entrada is None or not entrada.archive_url or _needs_capture(relatorio, previous):
            continue
        relatorio.archive_url = relatorio.archive_url or entrada.archive_url
        relatorio.archive_status = "reused"

    modo = "conta do Internet Archive" if has_credentials() else "anônima"
    print(f"[*] Preservação: submissão com {modo}")

    fila = [relatorio for relatorio in reports if _needs_capture(relatorio, previous)]
    fila.sort(key=lambda r: (_archive_priority(r, previous), r.tool_id))

    print(
        f"[*] Preservação: {len(fila)} endereço(s) a preservar | "
        f"submetendo {min(len(fila), max(limit, 0))} (limite {limit} por ciclo)..."
    )

    lote = run_archive_queue([(r.tool_id, r.target_url) for r in fila], limit=limit, budget=budget)
    por_id = {relatorio.tool_id: relatorio for relatorio in reports}

    for tool_id in lote.preteridos:
        por_id[tool_id].archive_status = "deferred"

    if lote.resultados:
        for tool_id, resultado in lote.resultados.items():
            relatorio = por_id[tool_id]
            relatorio.archive_status = resultado.status
            if resultado.url:
                relatorio.archive_url = resultado.url

    detalhes = lote.detalhes

    contagem: dict[str, int] = {}
    for relatorio in reports:
        situacao = relatorio.archive_status or "nao_tentado"
        contagem[situacao] = contagem.get(situacao, 0) + 1
    resumo = " | ".join(f"{quantidade} {situacao}" for situacao, quantidade in sorted(contagem.items()))
    print(f"[*] Preservação: {resumo}")

    exemplos: dict[str, list[str]] = {}
    for relatorio in reports:
        detalhe = detalhes.get(relatorio.tool_id)
        if not detalhe:
            continue
        grupo = exemplos.setdefault(relatorio.archive_status or "nao_tentado", [])
        if detalhe not in grupo and len(grupo) < 3:
            grupo.append(detalhe)
    for situacao, itens in sorted(exemplos.items()):
        for item in itens:
            print(f"        {situacao}: {item}")


def target_key(item: dict) -> str:
    """Devolve a chave comparável do endereço de um registro do catálogo.

    Args:
        item: Registro do catálogo, com a chave `url`.

    Returns:
        Chave normalizada, comparável com o registro de retiradas.
    """
    return url_key(item.get("url", ""))


def run_batch_audit(
    input_file: Path,
    output_file: Path,
    max_workers: int = 10,
    refresh_cache: bool = False,
    skip_archive: bool = False,
    timeout: int = 8,
    manifest_file: Path = Path("data/estado_hashes.json"),
    semantic: str = "off",
    max_bytes: int = MAX_PAYLOAD_BYTES,
    fetcher: ContentFetcher | None = None,
    robots_policy: RobotsPolicy | None = None,
    respect_robots: bool = True,
    withdrawals_file: Path = DEFAULT_REGISTRY,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    archive_budget: float = DEFAULT_ARCHIVE_BUDGET,
    archive_limit: int = DEFAULT_ARCHIVE_LIMIT,
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
        semantic: Camada semântica aplicada ao texto (`off` ou `jev`).
        max_bytes: Limite de bytes lidos por página.
        fetcher: Provedor de aquisição já construído; quando nulo, é construído a partir
        robots_policy: Política de `robots.txt` já construída; quando nula e
            `respect_robots` é verdadeiro, uma política nova é criada com o `timeout` do ciclo.
        respect_robots: Quando verdadeiro, sites que proíbem a leitura em `robots.txt` não
            recebem laudo e permanecem apenas no catálogo.
        cache_dir: Diretório do cache de corpos; testes passam um temporário para não escrever
            no cache do repositório.
        cache_dir: Diretório do cache de corpos; testes passam um temporário para não escrever
            no cache do repositório.
        withdrawals_file: Registro de retiradas acatadas. Um endereço retirado a pedido não é
            auditado, mesmo que ainda conste do catálogo.
        archive_budget: Tempo total, em segundos, que o ciclo espera pelas capturas do Internet
            Archive antes de registrar as pendentes como pendentes.
        archive_limit: Número máximo de capturas submetidas por ciclo, para não esgotar o limite
            do serviço; o restante fica registrado como `deferred` e volta no ciclo seguinte.

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

    problemas = validate_catalog(targets)
    if problemas:
        detalhe = "\n".join(f"        {p}" for p in problemas)
        raise ValueError(f"Catálogo inválido em {input_file}:\n{detalhe}")

    withdrawal_registry = carregar_retiradas(withdrawals_file)
    withdrawn = withdrawn_keys(withdrawal_registry)
    if withdrawn:
        remanescentes = [t for t in targets if target_key(t) in withdrawn]
        for item in remanescentes:
            print(f" [RET ] {item['nome'][:28]:<28} | retirada | pedido acatado, presente no catálogo")
        if remanescentes:
            print(
                f"[AVISO] {len(remanescentes)} iniciativa(s) retirada(s) ainda no catálogo: remova a entrada."
            )
        targets = [t for t in targets if target_key(t) not in withdrawn]

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

    versao_anterior = _versao_dos_laudos(previous_audits)
    if previous_audits and versao_anterior != EVALUATOR_VERSION:
        print(
            f"[AVISO] Laudos anteriores em versão diferente ({versao_anterior}); "
            "reavaliando em vez de reaproveitar, porque critério mudou."
        )
        previous_audits = {}

    print(f"[*] Iniciando esteira de monitoramento cívico sobre {len(targets)} iniciativas...")
    print(f"[*] Workers: {max_workers} | Refresh: {refresh_cache} | Skip Archive: {skip_archive}")
    print("[*] Aquisição: HTTP direto, TLS verificado")
    print(f"[*] Classificador semântico: {semantic}")
    print(f"[*] Limite de leitura por página: {max_bytes:,} bytes")
    print("-" * 80)

    evaluator = CivicEvaluator()
    fetcher = fetcher or HttpFetcher(timeout=timeout, max_bytes=max_bytes)
    classifier = build_classifier(semantic)
    if robots_policy is None and respect_robots:
        robots_policy = RobotsPolicy(timeout=timeout)
    print(f"[*] robots.txt: {'consultado' if robots_policy is not None else 'ignorado'}")
    reports: list[CivicAuditReport] = []
    skipped: list[tuple] = []
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
                cache_dir,
            ): t
            for t in targets
        }
        for fut in as_completed(futures):
            try:
                rep = fut.result()
                if rep is None:
                    skipped.append((futures[fut].get("nome"), "robots.txt"))
                else:
                    reports.append(rep)
            except Exception as e:
                t = futures[fut]
                print(f" [-] Erro ao processar {t.get('nome')}: {e}")

    _preserve(
        reports,
        index_by_tool(previous_manifest),
        skip_archive,
        archive_limit,
        archive_budget,
    )

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
        AuditLevel.NOT_INSPECTED.value: sum(1 for r in reports if r.status == AuditLevel.NOT_INSPECTED),
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
    if skipped:
        print(f"  [PULADAS ] Proibidas por robots.txt:             {len(skipped)}")
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
