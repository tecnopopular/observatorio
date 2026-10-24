"""
src/observatorio/state.py
Manifesto de estado do observatório: registro durável e revisável do último snapshot
avaliado de cada iniciativa.

O manifesto é a memória do motor. Ele não substitui o laudo publicado: guarda apenas o
que o pipeline precisa para decidir e para prestar contas — hash do conteúdo inspecionado,
data da avaliação, nível de conformidade e notas. Como é compacto, pode ser versionado no
repositório sem arrastar o laudo completo (texto derivado) para dentro do histórico git.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import AuditLevel, CivicAuditReport

MANIFEST_VERSION = "1"


class ManifestEntry(BaseModel):
    """Estado pontual de uma iniciativa no último ciclo de avaliação."""

    tool_id: str
    html_sha256: str | None = Field(None, description="Hash SHA-256 do último corpo HTTP inspecionado")
    evaluated_at: str = Field(..., description="Data ISO 8601 do laudo reaproveitado ou emitido")
    status: AuditLevel
    total_score: float | None = None
    dimension_scores: dict[str, float] = Field(
        default_factory=dict, description="Nota bruta por dimensão (0 a 100)"
    )
    coverage_complete: bool | None = Field(
        None, description="Cobertura da inspeção que produziu o laudo (None = laudo anterior ao metadado)"
    )


class Manifest(BaseModel):
    """Manifesto completo do observatório."""

    protocol_version: str = MANIFEST_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total: int = 0
    entries: list[ManifestEntry] = Field(default_factory=list)


def build_manifest(reports: Iterable[CivicAuditReport], generated_at: datetime | None = None) -> Manifest:
    """Projeta os laudos do ciclo no manifesto compacto de estado.

    Cada entrada reduz o laudo ao que o ciclo seguinte precisa comparar: hash do corpo
    inspecionado, data da avaliação, nível, nota total e nota bruta por dimensão.

    Args:
        reports: Laudos emitidos no ciclo atual.
        generated_at: Momento de geração do manifesto; quando nulo, usa o instante UTC atual.

    Returns:
        Manifesto com uma entrada por iniciativa, ordenada por `tool_id`.
    """
    entries = [
        ManifestEntry(
            tool_id=r.tool_id,
            html_sha256=r.html_sha256,
            evaluated_at=r.evaluated_at.isoformat(),
            status=r.status,
            total_score=round(r.total_score, 1) if r.total_score is not None else None,
            dimension_scores={d.value: round(ds.raw_score, 1) for d, ds in r.dimension_scores.items()},
            coverage_complete=r.coverage.complete if r.coverage else None,
        )
        for r in reports
    ]
    entries.sort(key=lambda e: e.tool_id)
    return Manifest(
        generated_at=generated_at or datetime.now(UTC),
        total=len(entries),
        entries=entries,
    )


def write_manifest(path: Path, manifest: Manifest) -> int:
    """Grava o manifesto em JSON canônico.

    Args:
        path: Caminho do arquivo de manifesto; diretórios ausentes são criados.
        manifest: Manifesto a serializar.

    Returns:
        Tamanho do arquivo gravado, em bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path.stat().st_size


def load_manifest(path: Path) -> Manifest:
    """Carrega o manifesto anterior.

    Ausência do arquivo devolve manifesto vazio: o primeiro ciclo não tem estado a comparar.

    Args:
        path: Caminho do arquivo de manifesto.

    Returns:
        Manifesto lido do disco, ou manifesto vazio quando o arquivo não existe.
    """
    if not path.exists():
        return Manifest(generated_at=datetime.now(UTC), total=0, entries=[])
    with open(path, encoding="utf-8") as f:
        return Manifest.model_validate(json.load(f))


def index_by_tool(manifest: Manifest) -> dict[str, ManifestEntry]:
    """Indexa as entradas por identificador de ferramenta.

    Args:
        manifest: Manifesto a indexar.

    Returns:
        Dicionário de `tool_id` para a entrada correspondente.
    """
    return {e.tool_id: e for e in manifest.entries}


def changed_since(manifest: Manifest, reports: Iterable[CivicAuditReport]) -> list[str]:
    """
    Lista iniciativas cujo hash mudou, ou que são novas, em relação ao manifesto anterior.
    Serve para auditoria do ciclo: quantas reavaliações o ciclo realmente produziu.

    Args:
        manifest: Manifesto anterior, base da comparação.
        reports: Laudos do ciclo atual.

    Returns:
        Identificadores das iniciativas novas ou com hash alterado, em ordem alfabética.
    """
    anteriores = index_by_tool(manifest)
    mudadas: list[str] = []
    for report in reports:
        anterior = anteriores.get(report.tool_id)
        if anterior is None or anterior.html_sha256 != report.html_sha256:
            mudadas.append(report.tool_id)
    return sorted(mudadas)


@dataclass(frozen=True)
class CacheCheck:
    """Resultado da conferência entre o manifesto de estado e o cache de HTML.

    Attributes:
        conferidos: Identificadores cujo hash recalculado coincide com o manifesto.
        divergentes: Identificadores cujo corpo em cache produz outro hash.
        ausentes: Identificadores com hash no manifesto e sem corpo em cache.
        sem_hash: Identificadores que não têm hash registrado e não entram na conferência.
    """

    conferidos: int
    divergentes: list[str]
    ausentes: list[str]
    sem_hash: list[str]

    @property
    def intact(self) -> bool:
        """Informa se o cache corresponde integralmente ao manifesto.

        Returns:
            Verdadeiro quando nenhum corpo diverge e nenhum identificador ficou sem cache.
        """
        return not self.divergentes and not self.ausentes


def file_digest(path: Path) -> str:
    """Calcula o SHA-256 de um corpo gravado em disco, sem tradução de fim de linha.

    Ler o arquivo em modo texto converteria `\r\n` em `\n` e mudaria o hash de qualquer página
    servida com CRLF. O cache é corpo bruto: bytes entram, bytes saem.

    Args:
        path: Arquivo de cache do corpo.

    Returns:
        Digest SHA-256 dos bytes gravados, em hexadecimal minúsculo.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def body_digest(text: str) -> str:
    """Calcula o SHA-256 de um corpo lido da web, como o motor o registra.

    O motor hasheia o texto decodificado em UTF-8, e não os bytes recebidos na rede. Recalcular
    pela mesma via é o que permite comparar o cache com o manifesto sem reabrir conexão.

    Args:
        text: Corpo da página como lido do cache ou da rede.

    Returns:
        Digest SHA-256 em hexadecimal minúsculo.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_cache(manifest: Manifest, cache_dir: Path) -> CacheCheck:
    """Confere se o cache de HTML reproduz os hashes registrados no manifesto.

    O manifesto afirma qual conteúdo foi avaliado; o cache guarda esse conteúdo. Quando os dois
    coincidem, o laudo pode ser recalculado offline a partir do que foi publicado. Quando não, a
    afirmação de integridade perde o lastro, e a publicação não deve seguir adiante.

    Args:
        manifest: Manifesto de estado do ciclo.
        cache_dir: Diretório com os corpos lidos, nomeados por `tool_id`.

    Returns:
        Resultado da conferência, com a contagem de corpos conferidos e as divergências.
    """
    conferidos = 0
    divergentes: list[str] = []
    ausentes: list[str] = []
    sem_hash: list[str] = []

    for entry in manifest.entries:
        if not entry.html_sha256:
            sem_hash.append(entry.tool_id)
            continue
        cache_file = cache_dir / f"{entry.tool_id}.html"
        if not cache_file.is_file():
            ausentes.append(entry.tool_id)
            continue
        if file_digest(cache_file) == entry.html_sha256:
            conferidos += 1
        else:
            divergentes.append(entry.tool_id)

    return CacheCheck(
        conferidos=conferidos,
        divergentes=sorted(divergentes),
        ausentes=sorted(ausentes),
        sem_hash=sorted(sem_hash),
    )
