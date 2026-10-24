"""Regras de identidade, validação e inclusão de iniciativas no catálogo.

O catálogo é a única fonte de verdade sobre o que o observatório acompanha, e o `tool_id` de cada
iniciativa é o endereço público do laudo dela. Enquanto o identificador era derivado do nome a cada
ciclo, renomear uma entrada publicava o laudo seguinte em outro endereço e partia o histórico em
dois: o mesmo site aparecia como duas iniciativas, uma morta e uma nova.

Por isso o `tool_id` passou a ser gravado na entrada, congelado na inclusão. `assign_tool_ids`
respeita o valor gravado e só deriva quando a entrada ainda não tem um, o que mantém os laudos
antigos nos mesmos endereços.
"""

import re
import unicodedata
from urllib.parse import urlsplit

from .retiradas import chave

CATEGORIAS = (
    "Dados e Mapas",
    "Informação e Persuasão",
    "Plataformas e Mobilização",
    "Jogos",
    "Materiais de Comunicação",
    "Conversa e Diálogo",
    "Abstenção e Virada",
)

ESQUEMAS_ACEITOS = ("http", "https")


def slugify(text: str) -> str:
    """Normaliza texto para slug ASCII minúsculo.

    Remove acentos por decomposição canônica, descarta o que não for ASCII e reduz qualquer
    sequência não alfanumérica a um único hífen.

    Args:
        text: Texto de origem, tipicamente o nome da iniciativa.

    Returns:
        Slug em minúsculas, sem acentos e sem hífen nas extremidades.
    """
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def sem_fragmento(url: str) -> str:
    """Remove o fragmento de um endereço, que nunca é enviado ao servidor.

    `https://exemplo.org/#painel` e `https://exemplo.org` são a mesma página: o fragmento é
    interpretado pelo navegador, não pelo servidor. Guardá-lo no catálogo faria duas entradas
    apontarem para o mesmo conteúdo com aparência de endereços distintos.

    Args:
        url: Endereço como informado.

    Returns:
        Endereço sem a parte após o `#`.
    """
    return url.strip().split("#", 1)[0]


def extrair_dominio(url: str) -> str:
    """Extrai o domínio de um endereço público.

    Args:
        url: Endereço público de uma iniciativa.

    Returns:
        Host com porta, quando houver; texto vazio quando o endereço não tem host.
    """
    return urlsplit(url.strip()).netloc.lower()


def ids_em_uso(catalogo: list[dict]) -> set[str]:
    """Devolve todos os `tool_id` que o catálogo ocupa, inclusive os derivados do nome.

    A simulação passa por `assign_tool_ids` porque entradas antigas, sem `tool_id` gravado,
    recebem sufixo numérico quando o nome se repete — e esse sufixo também está ocupado.

    Args:
        catalogo: Registros do catálogo.

    Returns:
        Conjunto de identificadores já atribuídos, sem modificar o catálogo recebido.
    """
    copia = [dict(item) for item in catalogo]
    assign_tool_ids(copia)
    return {item["_tool_id"] for item in copia}


def novo_tool_id(nome: str, catalogo: list[dict]) -> str:
    """Propõe um `tool_id` livre a partir do nome, sem colidir com o catálogo.

    Args:
        nome: Nome público da iniciativa.
        catalogo: Registros do catálogo onde o identificador precisa ser inédito.

    Returns:
        Slug derivado do nome, com sufixo numérico quando o slug já está em uso.
    """
    base = slugify(nome)
    ocupados = ids_em_uso(catalogo)
    if base not in ocupados:
        return base
    sufixo = 2
    while f"{base}-{sufixo}" in ocupados:
        sufixo += 1
    return f"{base}-{sufixo}"


def assign_tool_ids(targets: list[dict]) -> list[tuple]:
    """Atribui a cada alvo um `_tool_id` único e estável na ordem do catálogo.

    Nomes repetidos colidiriam no cache de rede, no laudo publicado e no manifesto — um
    sobrescreveria o outro em silêncio. Desambigua com sufixo numérico e devolve as colisões,
    mas respeita o `tool_id` gravado na entrada: o identificador nasce na inclusão e não muda
    quando o nome muda.

    Args:
        targets: Registros do catálogo, modificados no lugar com a chave `_tool_id`.

    Returns:
        Triplas `(slug base, tool_id atribuído, url)` das colisões desambiguadas.
    """
    usados: dict[str, int] = {}
    colisoes: list[tuple] = []
    for alvo in targets:
        congelado = str(alvo.get("tool_id") or "").strip()
        base = congelado or slugify(alvo["nome"])
        usados[base] = usados.get(base, 0) + 1
        if usados[base] == 1:
            alvo["_tool_id"] = base
        else:
            alvo["_tool_id"] = f"{base}-{usados[base]}"
            colisoes.append((base, alvo["_tool_id"], alvo["url"]))
    return colisoes


def validar_catalogo(catalogo: list[dict]) -> list[str]:
    """Verifica se o catálogo pode entrar em um ciclo de auditoria.

    Checa apenas o que quebraria o ciclo ou publicaria laudo enganoso: entrada sem nome ou URL,
    endereço sem esquema HTTP, dois registros do mesmo endereço e dois registros com o mesmo
    `tool_id`. Categoria e descrição são verificadas quando existem, mas não são obrigatórias,
    porque a auditoria de uma iniciativa sem descrição continua sendo possível.

    Args:
        catalogo: Registros do catálogo como lidos do disco.

    Returns:
        Lista de problemas encontrados, vazia quando o catálogo está utilizável.
    """
    problemas: list[str] = []
    vistos_endereco: dict[str, int] = {}
    vistos_id: dict[str, int] = {}

    for posicao, item in enumerate(catalogo, start=1):
        if not isinstance(item, dict):
            problemas.append(f"registro {posicao}: não é um objeto")
            continue

        nome = str(item.get("nome") or "").strip()
        url = str(item.get("url") or "").strip()
        rotulo = nome or f"registro {posicao}"

        if not nome:
            problemas.append(f"{rotulo}: sem nome")
        if not url:
            problemas.append(f"{rotulo}: sem URL")
            continue

        esquema = urlsplit(url).scheme.lower()
        if esquema not in ESQUEMAS_ACEITOS:
            problemas.append(f"{rotulo}: URL sem esquema http/https ({url})")

        alvo = chave(url)
        if alvo in vistos_endereco:
            problemas.append(f"{rotulo}: mesmo endereço do registro {vistos_endereco[alvo]} ({url})")
        vistos_endereco.setdefault(alvo, posicao)

        categoria = item.get("categoria")
        if categoria and categoria not in CATEGORIAS:
            problemas.append(f"{rotulo}: categoria fora da lista conhecida ({categoria})")

        identificador = str(item.get("tool_id") or "").strip()
        if identificador:
            if identificador in vistos_id:
                problemas.append(
                    f"{rotulo}: tool_id repetido com o registro {vistos_id[identificador]} ({identificador})"
                )
            vistos_id.setdefault(identificador, posicao)

    return problemas


def validar_entrada(entrada: dict, catalogo: list[dict]) -> list[str]:
    """Verifica uma entrada nova antes de acrescentá-la ao catálogo.

    É mais exigente que `validar_catalogo`: uma inclusão precisa de categoria conhecida, descrição
    com substância e identificador livre, porque é a partir deles que o laudo é publicado e que a
    dimensão de enquadramento lê o propósito declarado da ferramenta.

    Args:
        entrada: Registro proposto.
        catalogo: Catálogo onde ele seria acrescentado.

    Returns:
        Lista de problemas encontrados, vazia quando a entrada pode ser gravada.
    """
    problemas = validar_catalogo([entrada])

    categoria = entrada.get("categoria")
    if not categoria:
        problemas.append("entrada sem categoria")
    descricao = str(entrada.get("descricao") or "").strip()
    if len(descricao) < 20:
        problemas.append("descrição ausente ou curta demais: escreva o que a ferramenta faz")

    identificador = str(entrada.get("tool_id") or "").strip()
    if not identificador:
        problemas.append("entrada sem tool_id")
    elif identificador != slugify(identificador):
        problemas.append(f"tool_id fora do padrão de slug: {identificador}")
    elif identificador in ids_em_uso(catalogo):
        problemas.append(f"tool_id já usado no catálogo: {identificador}")

    alvo = chave(str(entrada.get("url") or ""))
    if any(chave(str(item.get("url") or "")) == alvo for item in catalogo):
        problemas.append(f"endereço já consta do catálogo: {entrada.get('url')}")

    return problemas


def acrescentar(catalogo: list[dict], entrada: dict) -> list[dict]:
    """Devolve o catálogo com a entrada nova ao final.

    Args:
        catalogo: Registros do catálogo.
        entrada: Registro validado a acrescentar.

    Returns:
        Nova lista de registros, com a entrada ao final.
    """
    return [*catalogo, dict(entrada)]
