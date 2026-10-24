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

from .models import EventoDeRetirada, RegistroDeRetiradas, TipoDeEvento

REGISTRO_PADRAO = Path("data/retiradas.json")


def chave(url: str) -> str:
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
    caminho = partes.path.rstrip("/").lower()
    return f"{host}{caminho}"


def carregar(caminho: Path = REGISTRO_PADRAO) -> RegistroDeRetiradas:
    """Lê o registro de eventos do disco.

    Args:
        caminho: Arquivo JSON do registro. Ausente significa nenhum evento registrado.

    Returns:
        Registro carregado, vazio quando o arquivo ainda não existe.

    Raises:
        ValueError: Quando o arquivo existe e não corresponde ao esquema esperado.
    """
    if not caminho.is_file():
        return RegistroDeRetiradas()
    return RegistroDeRetiradas.model_validate_json(caminho.read_text(encoding="utf-8"))


def salvar(registro: RegistroDeRetiradas, caminho: Path = REGISTRO_PADRAO) -> None:
    """Grava o registro em disco como JSON estável e legível em revisão.

    Args:
        registro: Registro a gravar.
        caminho: Arquivo de destino; diretórios intermediários são criados.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conteudo = json.dumps(registro.model_dump(mode="json"), ensure_ascii=False, indent=2)
    caminho.write_text(conteudo + "\n", encoding="utf-8")


def ultimo_evento(registro: RegistroDeRetiradas, url: str) -> EventoDeRetirada | None:
    """Devolve o evento mais recente registrado para um endereço.

    Args:
        registro: Registro de eventos carregado.
        url: Endereço a consultar.

    Returns:
        Evento mais recente, ou None quando o endereço nunca foi registrado.
    """
    alvo = chave(url)
    for evento in reversed(registro.eventos):
        if chave(evento.url) == alvo:
            return evento
    return None


def estado(registro: RegistroDeRetiradas, url: str) -> TipoDeEvento | None:
    """Informa a situação vigente de um endereço.

    Args:
        registro: Registro de eventos carregado.
        url: Endereço a consultar.

    Returns:
        Tipo do último evento do endereço, ou None quando nunca houve evento.
    """
    evento = ultimo_evento(registro, url)
    return evento.tipo if evento else None


def esta_retirada(url: str, registro: RegistroDeRetiradas) -> bool:
    """Informa se o endereço está retirado no momento.

    Args:
        url: Endereço a consultar.
        registro: Registro de eventos carregado.

    Returns:
        Verdadeiro quando o último evento do endereço é uma retirada.
    """
    return estado(registro, url) is TipoDeEvento.RETIRADA


def chaves_retiradas(registro: RegistroDeRetiradas) -> set[str]:
    """Devolve as chaves dos endereços atualmente retirados.

    Args:
        registro: Registro de eventos carregado.

    Returns:
        Conjunto de chaves cujo último evento é uma retirada.
    """
    vigente: dict[str, TipoDeEvento] = {}
    for evento in registro.eventos:
        vigente[chave(evento.url)] = evento.tipo
    return {alvo for alvo, tipo in vigente.items() if tipo is TipoDeEvento.RETIRADA}


def entrada_original(registro: RegistroDeRetiradas, url: str) -> dict | None:
    """Recupera o registro do catálogo guardado no momento da retirada.

    Args:
        registro: Registro de eventos carregado.
        url: Endereço retirado.

    Returns:
        Cópia do registro do catálogo, ou None quando nenhum evento o guardou.
    """
    alvo = chave(url)
    for evento in registro.eventos:
        if chave(evento.url) == alvo and evento.entrada_original:
            return dict(evento.entrada_original)
    return None


def registrar_retirada(
    registro: RegistroDeRetiradas,
    nome: str,
    url: str,
    motivo: str,
    solicitado_em: date,
    canal: str = "email",
    entrada: dict | None = None,
) -> EventoDeRetirada:
    """Acrescenta um evento de retirada ao registro.

    Args:
        registro: Registro a acrescentar; é modificado no lugar.
        nome: Nome da iniciativa no momento da retirada.
        url: Endereço retirado.
        motivo: Motivo informado pelo solicitante.
        solicitado_em: Data em que o pedido foi feito.
        canal: Origem do pedido.
        entrada: Registro do catálogo antes da retirada, guardado para permitir a readmissão.

    Returns:
        Evento acrescentado ao registro.

    Raises:
        ValueError: Quando o endereço já está retirado.
    """
    if esta_retirada(url, registro):
        raise ValueError(f"Endereço já registrado como retirado: {url}")
    evento = EventoDeRetirada(
        tipo=TipoDeEvento.RETIRADA,
        nome=nome,
        url=url,
        motivo=motivo,
        canal=canal,
        solicitado_em=solicitado_em,
        entrada_original=entrada,
    )
    registro.eventos.append(evento)
    return evento


def registrar_readmissao(
    registro: RegistroDeRetiradas,
    url: str,
    motivo: str,
    solicitado_em: date,
    canal: str = "email",
) -> EventoDeRetirada:
    """Acrescenta um evento de readmissão, devolvendo a iniciativa ao catálogo.

    Args:
        registro: Registro a acrescentar; é modificado no lugar.
        url: Endereço readmitido.
        motivo: Motivo informado pelo solicitante.
        solicitado_em: Data em que o pedido de volta foi feito.
        canal: Origem do pedido.

    Returns:
        Evento acrescentado ao registro.

    Raises:
        ValueError: Quando o endereço não está retirado no momento.
    """
    anterior = ultimo_evento(registro, url)
    if anterior is None or anterior.tipo is not TipoDeEvento.RETIRADA:
        raise ValueError(f"Endereço não está retirado: {url}")
    evento = EventoDeRetirada(
        tipo=TipoDeEvento.READMISSAO,
        nome=anterior.nome,
        url=url,
        motivo=motivo,
        canal=canal,
        solicitado_em=solicitado_em,
    )
    registro.eventos.append(evento)
    return evento


def remover_do_catalogo(catalogo: list[dict], url: str) -> list[dict]:
    """Devolve o catálogo sem as entradas que correspondem ao endereço retirado.

    Args:
        catalogo: Registros do catálogo de iniciativas.
        url: Endereço retirado.

    Returns:
        Nova lista de registros, sem o endereço retirado.
    """
    alvo = chave(url)
    return [item for item in catalogo if chave(item.get("url", "")) != alvo]


def devolver_ao_catalogo(catalogo: list[dict], entrada: dict) -> list[dict]:
    """Devolve o catálogo com a entrada readmitida acrescentada ao final.

    Args:
        catalogo: Registros do catálogo de iniciativas.
        entrada: Registro guardado no momento da retirada.

    Returns:
        Nova lista de registros, com a entrada readmitida ao final.
    """
    return [*catalogo, dict(entrada)]
