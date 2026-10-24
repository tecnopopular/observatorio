"""Preservação histórica e integridade criptográfica do Observatório TecnoPopular.

Cobre o cálculo determinístico de SHA-256 sobre snapshots em memória, a invalidação de cache
por conteúdo (content-addressable) e a preservação perene no Internet Archive.

A preservação usa a interface de tarefas do Save Page Now: a captura é submetida e responde com
um identificador de trabalho, que é consultado até concluir. A interface síncrona
(`GET /save/<url>`) não serve a este ciclo porque só responde depois de a captura terminar, e
uma página pesada leva minutos. Medido em 2026-10-24: `https://acervodavitoria.pages.dev/` não
devolveu nada em 90 s pelo modo síncrono, enquanto pelo modo de tarefas
`https://tecnopopular.org/` foi preservado em 14,21 s.

O endereço perene não vem em cabeçalho: a consulta de estado devolve `timestamp` e
`original_url`, e o endereço é montado a partir deles. O cliente anterior lia
`Content-Location` da resposta final, mas o `urllib` segue o redirecionamento do modo
síncrono, de modo que a resposta final nunca trazia o cabeçalho procurado.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .credentials import get_internet_archive_keys
from .fetch import INSTITUTIONAL_USER_AGENT
from .models import ArchiveStatus

SAVE_ENDPOINT = "https://web.archive.org/save"
STATUS_ENDPOINT = "https://web.archive.org/save/status/{job_id}"
SNAPSHOT_ENDPOINT = "https://web.archive.org/web/{timestamp}/{target}"
JOB_PATTERN = re.compile(r"spn2-[a-z0-9]+")
EXISTING_SNAPSHOT_PATTERN = re.compile(r"web\.archive\.org/web/(\d{14})/")

DEFAULT_ARCHIVE_BUDGET = 60.0
POLL_INTERVAL = 5.0


@dataclass(frozen=True)
class ArchiveOutcome:
    """Resultado de uma tentativa de preservação no Internet Archive.

    Attributes:
        status: Situação da captura: `captured` (concluída), `pending` (não terminou dentro do
            orçamento), `refused` (o serviço recusou o pedido) ou `failed` (erro de rede ou
            resposta ilegível).
        url: Endereço perene do snapshot, quando a captura concluiu.
        job_id: Identificador do trabalho no Internet Archive, quando houve submissão.
        detail: Motivo legível da situação, gravado para que a ausência de snapshot não fique
            indistinguível entre "não tentado", "recusado" e "falhou".
    """

    status: ArchiveStatus
    url: str | None = None
    job_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ArchiveJob:
    """Trabalho de captura submetido e ainda não concluído.

    Attributes:
        job_id: Identificador do trabalho no Internet Archive.
        target_url: Endereço original preservado, necessário para montar o endereço perene.
    """

    job_id: str
    target_url: str


def calculate_sha256(content: str | bytes) -> str:
    """Calcula o hash SHA-256 do conteúdo fornecido.

    Args:
        content: Conteúdo em texto (codificado em UTF-8) ou em bytes.

    Returns:
        Digest SHA-256 em hexadecimal minúsculo.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def check_content_changed(current_sha256: str, previous_sha256: str | None) -> bool:
    """Indica se o conteúdo deve ser considerado alterado em relação ao registro anterior.

    Args:
        current_sha256: Hash da inspeção atual.
        previous_sha256: Hash registrado no ciclo anterior; None quando não há registro.

    Returns:
        True quando o conteúdo mudou ou não há registro prévio; False quando os hashes coincidem.
    """
    if not previous_sha256:
        return True
    return current_sha256.strip().lower() != previous_sha256.strip().lower()


def snapshot_url(timestamp: str, target_url: str) -> str:
    """Monta o endereço perene de um snapshot do Wayback.

    Args:
        timestamp: Carimbo da captura no formato `AAAAMMDDhhmmss`.
        target_url: Endereço original preservado.

    Returns:
        Endereço permanente do snapshot no Internet Archive.
    """
    return SNAPSHOT_ENDPOINT.format(timestamp=timestamp, target=target_url)


def auth_headers() -> dict[str, str]:
    """Monta os cabeçalhos de identificação e autenticação do Save Page Now.

    A conta do Internet Archive eleva o limite de capturas, e a autenticação usa o esquema `LOW`
    com o par de chaves S3. Sem as chaves, a submissão segue anônima: o ciclo funciona, com o
    limite menor que o serviço aplica a quem não se identifica.

    A resposta JSON é pedida apenas com chaves: sem conta, a mesma interface devolve HTTP 401, e o
    único caminho anônimo disponível é a página HTML de submissão.

    Returns:
        Cabeçalhos HTTP com o User-Agent institucional e, quando houver chaves, o pedido de
        resposta JSON e o cabeçalho de autorização.
    """
    cabecalhos = {"User-Agent": INSTITUTIONAL_USER_AGENT}
    chaves = get_internet_archive_keys()
    if chaves:
        acesso, segredo = chaves
        cabecalhos["Authorization"] = f"LOW {acesso}:{segredo}"
        cabecalhos["Accept"] = "application/json"
    return cabecalhos


def submit_archive(target_url: str, timeout: int = 15) -> ArchiveOutcome:
    """Submete uma captura ao Save Page Now e devolve o identificador do trabalho.

    A resposta é uma página HTML que carrega o identificador `spn2-...` nos próprios links, por
    isso ele é extraído do corpo e não de cabeçalho.

    Args:
        target_url: URL pública a preservar.
        timeout: Tempo limite da submissão, em segundos.

    Returns:
        Resultado `pending` com o identificador do trabalho quando a submissão é aceita, ou
        `refused`/`failed` com o motivo quando não é.
    """
    dados = urllib.parse.urlencode({"url": target_url, "capture_all": "on"}).encode()
    cabecalhos = auth_headers()
    cabecalhos["Content-Type"] = "application/x-www-form-urlencoded"
    pedido = urllib.request.Request(SAVE_ENDPOINT, data=dados, headers=cabecalhos)
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            corpo = resposta.read(400_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as erro:
        return _outcome_from_http_error(erro)
    except Exception as erro:
        return ArchiveOutcome(status="failed", detail=f"falha de rede na submissão: {type(erro).__name__}")

    resposta_json = _json_da_submissao(corpo)
    if resposta_json is not None:
        return resposta_json

    achado = JOB_PATTERN.search(corpo)
    if achado:
        return ArchiveOutcome(status="pending", job_id=achado.group(0))

    existente = EXISTING_SNAPSHOT_PATTERN.search(corpo)
    if existente:
        return ArchiveOutcome(
            status="captured",
            url=snapshot_url(existente.group(1), target_url),
            detail="captura já existente, reaproveitada em vez de nova submissão",
        )
    return ArchiveOutcome(status="refused", detail="o Internet Archive não abriu tarefa para este endereço")


def poll_archive(job: ArchiveJob, timeout: int = 15) -> ArchiveOutcome:
    """Consulta o estado de uma captura submetida.

    Args:
        job: Trabalho submetido, com o identificador e o endereço original.
        timeout: Tempo limite da consulta, em segundos.

    Returns:
        Resultado `captured` com o endereço perene quando a captura concluiu, `pending` enquanto
        não concluiu, ou `failed` com o motivo quando o trabalho termina sem êxito.
    """
    pedido = urllib.request.Request(STATUS_ENDPOINT.format(job_id=job.job_id), headers=auth_headers())
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            corpo = resposta.read(400_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as erro:
        return _outcome_from_http_error(erro, job_id=job.job_id)
    except Exception as erro:
        return ArchiveOutcome(
            status="failed",
            job_id=job.job_id,
            detail=f"falha de rede na consulta: {_reason_text(erro)}",
        )

    try:
        estado = json.loads(corpo)
    except json.JSONDecodeError:
        return ArchiveOutcome(status="failed", job_id=job.job_id, detail="resposta de estado ilegível")

    situacao = str(estado.get("status") or "").lower()
    if situacao == "success":
        carimbo = estado.get("timestamp")
        if not carimbo:
            return ArchiveOutcome(
                status="failed", job_id=job.job_id, detail="captura concluída sem carimbo de data"
            )
        original = str(estado.get("original_url") or job.target_url)
        return ArchiveOutcome(status="captured", url=snapshot_url(str(carimbo), original), job_id=job.job_id)
    if situacao == "pending":
        return ArchiveOutcome(status="pending", job_id=job.job_id, detail="captura em andamento")
    motivo = estado.get("message") or estado.get("status_ext") or situacao or "sem motivo informado"
    return ArchiveOutcome(status="failed", job_id=job.job_id, detail=f"captura sem êxito: {motivo}")


def await_archives(
    jobs: dict[str, ArchiveJob],
    budget: float = DEFAULT_ARCHIVE_BUDGET,
    interval: float = POLL_INTERVAL,
) -> dict[str, ArchiveOutcome]:
    """Acompanha as capturas em andamento até concluírem ou até o orçamento do ciclo esgotar.

    O orçamento vale para o ciclo inteiro, não por iniciativa: as tarefas são submetidas em
    paralelo e consultadas em rodadas, para que a esteira semanal tenha duração previsível.
    Quem não concluir fica registrado como `pending`, com o motivo escrito no laudo, em vez de
    desaparecer como ausência de captura.

    Args:
        jobs: Trabalhos submetidos, indexados por `tool_id`.
        budget: Tempo total de espera do ciclo, em segundos.
        interval: Intervalo entre rodadas de consulta, em segundos.

    Returns:
        Resultado por `tool_id`; trabalhos não concluídos aparecem como `pending`.
    """
    pendentes = dict(jobs)
    resultados: dict[str, ArchiveOutcome] = {}
    limite = time.monotonic() + max(budget, 0.0)

    while pendentes:
        for tool_id, job in list(pendentes.items()):
            resultado = poll_archive(job)
            resultados[tool_id] = resultado
            if resultado.status != "pending":
                del pendentes[tool_id]
        if not pendentes:
            break
        if time.monotonic() + interval > limite:
            break
        time.sleep(interval)

    for tool_id, job in pendentes.items():
        resultados[tool_id] = ArchiveOutcome(
            status="pending",
            job_id=job.job_id,
            detail=f"captura não concluída no orçamento de {budget:.0f}s do ciclo",
        )
    return resultados


def _json_da_submissao(corpo: str) -> ArchiveOutcome | None:
    """Interpreta a resposta JSON da submissão, quando o serviço responde em JSON.

    Com `Accept: application/json` o serviço devolve o identificador do trabalho em JSON, e não a
    página HTML que o modo anônimo entrega. Respostas de erro também vêm em JSON, com `message` e
    `status_ext`, o que permite registrar o motivo real em vez de supor.

    Args:
        corpo: Corpo da resposta da submissão.

    Returns:
        Resultado da submissão, ou None quando o corpo não é JSON.
    """
    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None

    job_id = dados.get("job_id")
    if job_id:
        return ArchiveOutcome(status="pending", job_id=str(job_id))

    situacao = str(dados.get("status") or "").lower()
    motivo = dados.get("message") or dados.get("status_ext") or situacao or "sem motivo informado"
    if situacao in ("error", "fatal"):
        recusa = "invalid" in str(dados.get("status_ext") or "") or "limit" in str(motivo).lower()
        return ArchiveOutcome(
            status="refused" if recusa else "failed", detail=f"o Internet Archive recusou: {motivo}"
        )
    if dados.get("url"):
        return ArchiveOutcome(status="pending", detail="submissão aceita sem identificador de trabalho")
    return None


def _reason_text(erro: Exception) -> str:
    """Resume a causa de uma falha de rede sem derramar a resposta inteira no laudo.

    Args:
        erro: Exceção levantada pelo cliente HTTP.

    Returns:
        Texto curto com a causa, quando identificável, ou o nome do tipo do erro.
    """
    motivo = getattr(erro, "reason", None)
    if motivo is not None:
        return str(motivo)[:80]
    texto = str(erro).strip()
    return texto[:80] if texto else type(erro).__name__


def _outcome_from_http_error(erro: urllib.error.HTTPError, job_id: str | None = None) -> ArchiveOutcome:
    """Converte um erro HTTP do Internet Archive em resultado registrável.

    Args:
        erro: Erro HTTP levantado pelo cliente.
        job_id: Identificador do trabalho, quando o erro ocorre na consulta de estado.

    Returns:
        Resultado `refused` para limite de uso e pedido inválido, `failed` para falha do serviço.
    """
    if erro.code == 429:
        return ArchiveOutcome(
            status="refused",
            job_id=job_id,
            detail="o Internet Archive recusou por limite de requisições (HTTP 429)",
        )
    if 400 <= erro.code < 500:
        return ArchiveOutcome(
            status="refused",
            job_id=job_id,
            detail=f"o Internet Archive recusou o pedido (HTTP {erro.code})",
        )
    return ArchiveOutcome(
        status="failed", job_id=job_id, detail=f"o Internet Archive respondeu HTTP {erro.code}"
    )
