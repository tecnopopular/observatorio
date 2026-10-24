"""Registro de retiradas e readmissões, guardado fora do catálogo.

Uma iniciativa retirada sai de `data/data_iniciativas.json` e deixa de ser auditada, mas a razão
do pedido precisa sobreviver: o catálogo, ao perder a entrada, perde também qualquer vestígio de
que ela existiu e de por que foi retirada. Se depois o site pedir para voltar, apagar o pedido de
saída para registrar a volta apagaria o primeiro movimento — e com ele a explicação pública dele.

Por isso o arquivo guarda **eventos**, append-only, e nunca um estado: o estado vigente de um
endereço é o último evento registrado para ele. Retirada seguida de readmissão deixa os dois
eventos no histórico; uma nova retirada depois acrescenta o terceiro.

A comparação de endereços ignora esquema, `www`, barra final e caixa do caminho: o mesmo site
pedido como `http://www.exemplo.org/` e catalogado como `https://exemplo.org` é a mesma
iniciativa, e tratar os dois como distintos deixaria o pedido sem efeito.
"""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .models import EventType, WithdrawalEvent, WithdrawalRegistry

DEFAULT_REGISTRY = Path("data/retiradas.json")


def url_key(url: str) -> str:
    """Reduz um endereço à forma usada na comparação.

    Args:
        url: Endereço público de uma iniciativa.

    Returns:
        Host sem `www` e caminho sem barra final, ambos em minúsculas.
    """
    partes = urlsplit(url.strip())
    host = partes.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = partes.path.rstrip("/").lower()
    return f"{host}{path}"


def load(path: Path = DEFAULT_REGISTRY) -> WithdrawalRegistry:
    """Lê o registro de eventos do disco.

    Args:
        path: Arquivo JSON do registro. Ausente significa nenhum evento registrado.

    Returns:
        Registro carregado, vazio quando o arquivo ainda não existe.

    Raises:
        ValueError: Quando o arquivo existe e não corresponde ao esquema esperado.
    """
    if not path.is_file():
        return WithdrawalRegistry()
    return WithdrawalRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def save(registry: WithdrawalRegistry, path: Path = DEFAULT_REGISTRY) -> None:
    """Grava o registro em disco como JSON estável e legível em revisão.

    Args:
        registry: Registro a gravar.
        path: Arquivo de destino; diretórios intermediários são criados.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conteudo = json.dumps(registry.model_dump(mode="json"), ensure_ascii=False, indent=2)
    path.write_text(conteudo + "\n", encoding="utf-8")


def latest_event(registry: WithdrawalRegistry, url: str) -> WithdrawalEvent | None:
    """Devolve o evento mais recente registrado para um endereço.

    Args:
        registry: Registro de eventos carregado.
        url: Endereço a consultar.

    Returns:
        Evento mais recente, ou None quando o endereço nunca foi registrado.
    """
    alvo = url_key(url)
    for evento in reversed(registry.eventos):
        if url_key(evento.url) == alvo:
            return evento
    return None


def current_state(registry: WithdrawalRegistry, url: str) -> EventType | None:
    """Informa a situação vigente de um endereço.

    Args:
        registry: Registro de eventos carregado.
        url: Endereço a consultar.

    Returns:
        Tipo do último evento do endereço, ou None quando nunca houve evento.
    """
    evento = latest_event(registry, url)
    return evento.tipo if evento else None


def is_withdrawn(url: str, registry: WithdrawalRegistry) -> bool:
    """Informa se o endereço está retirado no momento.

    Args:
        url: Endereço a consultar.
        registry: Registro de eventos carregado.

    Returns:
        Verdadeiro quando o último evento do endereço é uma retirada.
    """
    return current_state(registry, url) is EventType.WITHDRAWAL


def withdrawn_keys(registry: WithdrawalRegistry) -> set[str]:
    """Devolve as chaves dos endereços atualmente retirados.

    Args:
        registry: Registro de eventos carregado.

    Returns:
        Conjunto de chaves cujo último evento é uma retirada.
    """
    vigente: dict[str, EventType] = {}
    for evento in registry.eventos:
        vigente[url_key(evento.url)] = evento.tipo
    return {alvo for alvo, tipo in vigente.items() if tipo is EventType.WITHDRAWAL}


def original_entry(registry: WithdrawalRegistry, url: str) -> dict | None:
    """Recupera o registro do catálogo guardado no momento da retirada.

    Args:
        registry: Registro de eventos carregado.
        url: Endereço retirado.

    Returns:
        Cópia do registro do catálogo, ou None quando nenhum evento o guardou.
    """
    alvo = url_key(url)
    for evento in registry.eventos:
        if url_key(evento.url) == alvo and evento.original_entry:
            return dict(evento.original_entry)
    return None


def register_withdrawal(
    registry: WithdrawalRegistry,
    nome: str,
    url: str,
    motivo: str,
    solicitado_em: date,
    canal: str = "email",
    entry: dict | None = None,
) -> WithdrawalEvent:
    """Acrescenta um evento de retirada ao registro.

    Args:
        registry: Registro a acrescentar; é modificado no lugar.
        nome: Nome da iniciativa no momento da retirada.
        url: Endereço retirado.
        motivo: Motivo informado pelo solicitante.
        solicitado_em: Data em que o pedido foi feito.
        canal: Origem do pedido.
        entry: Registro do catálogo antes da retirada, guardado para permitir a readmissão.

    Returns:
        Evento acrescentado ao registro.

    Raises:
        ValueError: Quando o endereço já está retirado.
    """
    if is_withdrawn(url, registry):
        raise ValueError(f"Endereço já registrado como retirado: {url}")
    evento = WithdrawalEvent(
        tipo=EventType.WITHDRAWAL,
        nome=nome,
        url=url,
        motivo=motivo,
        canal=canal,
        solicitado_em=solicitado_em,
        original_entry=entry,
    )
    registry.eventos.append(evento)
    return evento


def register_readmission(
    registry: WithdrawalRegistry,
    url: str,
    motivo: str,
    solicitado_em: date,
    canal: str = "email",
) -> WithdrawalEvent:
    """Acrescenta um evento de readmissão, devolvendo a iniciativa ao catálogo.

    Args:
        registry: Registro a acrescentar; é modificado no lugar.
        url: Endereço readmitido.
        motivo: Motivo informado pelo solicitante.
        solicitado_em: Data em que o pedido de volta foi feito.
        canal: Origem do pedido.

    Returns:
        Evento acrescentado ao registro.

    Raises:
        ValueError: Quando o endereço não está retirado no momento.
    """
    anterior = latest_event(registry, url)
    if anterior is None or anterior.tipo is not EventType.WITHDRAWAL:
        raise ValueError(f"Endereço não está retirado: {url}")
    evento = WithdrawalEvent(
        tipo=EventType.READMISSION,
        nome=anterior.nome,
        url=url,
        motivo=motivo,
        canal=canal,
        solicitado_em=solicitado_em,
    )
    registry.eventos.append(evento)
    return evento


def remove_from_catalog(catalog: list[dict], url: str) -> list[dict]:
    """Devolve o catálogo sem as entradas que correspondem ao endereço retirado.

    Args:
        catalog: Registros do catálogo de iniciativas.
        url: Endereço retirado.

    Returns:
        Nova lista de registros, sem o endereço retirado.
    """
    alvo = url_key(url)
    return [item for item in catalog if url_key(item.get("url", "")) != alvo]


def restore_to_catalog(catalog: list[dict], entry: dict) -> list[dict]:
    """Devolve o catálogo com a entrada readmitida acrescentada ao final.

    Args:
        catalog: Registros do catálogo de iniciativas.
        entry: Registro guardado no momento da retirada.

    Returns:
        Nova lista de registros, com a entrada readmitida ao final.
    """
    return [*catalog, dict(entry)]
