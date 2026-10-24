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

from .withdrawals import url_key

CATEGORIES = (
    "Dados e Mapas",
    "Informação e Persuasão",
    "Plataformas e Mobilização",
    "Jogos",
    "Materiais de Comunicação",
    "Conversa e Diálogo",
    "Abstenção e Virada",
)

CATEGORIAS_CONHECIDAS = ", ".join(CATEGORIES)

ACCEPTED_SCHEMES = ("https",)

BARE_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+(?::\d+)?(?:[/?].*)?$", re.I)


def normalize_url(raw: str) -> tuple[str, str | None]:
    """Completa o esquema ausente e recusa o que não for https.

    Endereço sem esquema é ambíguo apenas quanto ao transporte; o host está declarado. Como o
    catálogo publica o endereço exato que o motor vai buscar, o esquema assumido precisa ser
    confirmado antes de gravar — quem chama é que faz a consulta, porque esta função não vai à rede.

    Args:
        raw: Endereço como informado pelo operador.

    Returns:
        Par com o endereço normalizado e o problema encontrado, quando houver.
    """
    texto = raw.strip()
    if not texto:
        return "", "sem endereço"

    partes = urlsplit(texto)
    esquema = partes.scheme.lower()
    if esquema == "https":
        if not partes.hostname or "." not in partes.hostname:
            return texto, f"endereço sem host utilizável ({texto})"
        return texto, None
    if esquema:
        return texto, f"o catálogo só aceita https, e chegou {esquema}:// ({texto})"
    if not BARE_HOST.match(texto):
        return texto, f"endereço sem esquema e sem forma de host ({texto})"
    return f"https://{texto}", None


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


def strip_fragment(url: str) -> str:
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


def extract_domain(url: str) -> str:
    """Extrai o domínio de um endereço público.

    Args:
        url: Endereço público de uma iniciativa.

    Returns:
        Host com porta, quando houver; texto vazio quando o endereço não tem host.
    """
    return urlsplit(url.strip()).netloc.lower()


def ids_in_use(catalog: list[dict]) -> set[str]:
    """Devolve todos os `tool_id` que o catálogo ocupa, inclusive os derivados do nome.

    A simulação passa por `assign_tool_ids` porque entradas antigas, sem `tool_id` gravado,
    recebem sufixo numérico quando o nome se repete — e esse sufixo também está ocupado.

    Args:
        catalog: Registros do catálogo.

    Returns:
        Conjunto de identificadores já atribuídos, sem modificar o catálogo recebido.
    """
    copia = [dict(item) for item in catalog]
    assign_tool_ids(copia)
    return {item["_tool_id"] for item in copia}


def new_tool_id(nome: str, catalog: list[dict]) -> str:
    """Propõe um `tool_id` livre a partir do nome, sem colidir com o catálogo.

    Args:
        nome: Nome público da iniciativa.
        catalog: Registros do catálogo onde o identificador precisa ser inédito.

    Returns:
        Slug derivado do nome, com sufixo numérico quando o slug já está em uso.
    """
    base = slugify(nome)
    ocupados = ids_in_use(catalog)
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


def validate_catalog(catalog: list[dict]) -> list[str]:
    """Verifica se o catálogo pode entrar em um ciclo de auditoria.

    Checa apenas o que quebraria o ciclo ou publicaria laudo enganoso: entrada sem nome ou URL,
    endereço sem esquema HTTP, dois registros do mesmo endereço e dois registros com o mesmo
    `tool_id`. Categoria e descrição são verificadas quando existem, mas não são obrigatórias,
    porque a auditoria de uma iniciativa sem descrição continua sendo possível.

    Args:
        catalog: Registros do catálogo como lidos do disco.

    Returns:
        Lista de problemas encontrados, vazia quando o catálogo está utilizável.
    """
    problemas: list[str] = []
    vistos_endereco: dict[str, int] = {}
    vistos_id: dict[str, int] = {}

    for posicao, item in enumerate(catalog, start=1):
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
        if esquema not in ACCEPTED_SCHEMES:
            problemas.append(f"{rotulo}: URL precisa de https ({url})")

        alvo = url_key(url)
        if alvo in vistos_endereco:
            problemas.append(f"{rotulo}: mesmo endereço do registro {vistos_endereco[alvo]} ({url})")
        vistos_endereco.setdefault(alvo, posicao)

        identificador = str(item.get("tool_id") or "").strip()
        if identificador:
            if identificador in vistos_id:
                problemas.append(
                    f"{rotulo}: tool_id repetido com o registro {vistos_id[identificador]} ({identificador})"
                )
            vistos_id.setdefault(identificador, posicao)

    return problemas


def validate_entry(entry: dict, catalog: list[dict]) -> list[str]:
    """Verifica uma entrada nova antes de acrescentá-la ao catálogo.

     É mais exigente que `validar_catalogo` apenas no que o ciclo realmente consome: identificador
    livre, endereço válido e nome presente. Descrição e eixo temático não entram aqui — são texto
    editorial, de responsabilidade do catálogo publicado, e nenhuma dimensão da nota os lê.

    Args:
        entry: Registro proposto.
        catalog: Catálogo onde ele seria acrescentado.

    Returns:
        Lista de problemas encontrados, vazia quando a entrada pode ser gravada.
    """
    problemas = validate_catalog([entry])

    identificador = str(entry.get("tool_id") or "").strip()
    if not identificador:
        problemas.append("entrada sem tool_id")
    elif identificador != slugify(identificador):
        problemas.append(f"tool_id fora do padrão de slug: {identificador}")
    elif identificador in ids_in_use(catalog):
        problemas.append(f"tool_id já usado no catálogo: {identificador}")

    alvo = url_key(str(entry.get("url") or ""))
    if any(url_key(str(item.get("url") or "")) == alvo for item in catalog):
        problemas.append(f"endereço já consta do catálogo: {entry.get('url')}")

    return problemas


def append_entry(catalog: list[dict], entry: dict) -> list[dict]:
    """Devolve o catálogo com a entrada nova ao final.

    Args:
        catalog: Registros do catálogo.
        entry: Registro validado a acrescentar.

    Returns:
        Nova lista de registros, com a entrada ao final.
    """
    return [*catalog, dict(entry)]
