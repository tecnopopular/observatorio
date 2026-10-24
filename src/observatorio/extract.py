"""Extração de evidências com escopo: uma passada de parser por página, cinco visões distintas.

Motivo: o motor original aplicava regex sobre a string bruta do HTML. Isso confunde o que a
página **instala** com o que ela **menciona** — um `TODO` em comentário declarando remover o
Google Analytics era lido como Google Analytics instalado, rebaixando a ferramenta a rejeitada.

Aqui a página é parseada uma vez e cada detector recebe apenas o escopo em que sua evidência
faz sentido:

    visible_text    -> texto que o cidadão lê (sem script, style, noscript, svg, comentário)
    script_srcs     -> URLs de scripts externos (onde um tracker realmente é carregado)
    inline_scripts  -> corpos de <script> sem src (pixels e inicializações)
    links           -> valores de href de <a>, para proveniência e repositório
    meta            -> name/property -> content
    input_names     -> atributo name de <input>, para detecção de coleta de PII
    rel_license     -> href de links com rel="license"
    comments        -> comentários HTML, coletados apenas para diagnóstico

Comentário nunca é evidência. Menção em texto não é instalação.
"""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field

MAX_TEXT_CHARS = 200_000

OFFICIAL_SUFFIXES = (
    ".gov.br",
    ".jus.br",
    ".leg.br",
    ".mp.br",
    ".def.br",
)


class PageEvidence(BaseModel):
    """Visões escopadas de uma página, prontas para os detectores."""

    visible_text: str = ""
    script_srcs: list[str] = Field(default_factory=list)
    inline_scripts: str = ""
    links: list[str] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    input_names: list[str] = Field(default_factory=list)
    rel_license: list[str] = Field(default_factory=list)
    resource_urls: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    text_chars_total: int = 0
    text_truncated: bool = False
    inline_scripts_truncated: bool = False
    parse_error: str | None = None

    @property
    def is_empty(self) -> bool:
        """Verdadeiro quando não há absolutamente nada a inspecionar.

        Returns:
            True quando texto visível, scripts, links, inputs e metadados estão todos vazios.
        """
        return not (
            self.visible_text.strip()
            or self.script_srcs
            or self.inline_scripts.strip()
            or self.links
            or self.input_names
            or self.meta
        )

    @property
    def script_surface(self) -> str:
        """Superfície onde telemetria é instalada: srcs externos, corpos inline e recursos.

        Returns:
            Texto único com srcs de script, corpos inline e URLs de recursos, um por linha.
        """
        return "\n".join(self.script_srcs) + "\n" + self.inline_scripts + "\n" + "\n".join(self.resource_urls)


def parse_page(html: str, max_text_chars: int = MAX_TEXT_CHARS) -> PageEvidence:
    """Parseia o HTML uma vez e devolve as visões escopadas por tipo de evidência.

    Os comentários HTML são coletados em `comments` e removidos do documento antes da extração
    de texto: comentário nunca vale como evidência do que a página instala. Pixels e beacons
    também chegam por `<img>`, `<iframe>` e `<link>`, por isso essas tags alimentam
    `resource_urls` junto com os scripts.

    Args:
        html: Corpo HTML bruto da página.
        max_text_chars: Limite de caracteres aplicado a `visible_text` e `inline_scripts`.

    Returns:
        `PageEvidence` com as visões escopadas. HTML vazio devolve evidência vazia; falha de
        importação do bs4 ou de parsing devolve evidência vazia com `parse_error` preenchido,
        porque dependência ausente não deve derrubar a esteira.
    """
    if not html or not html.strip():
        return PageEvidence()

    try:
        from bs4 import BeautifulSoup, Comment
    except Exception as exc:
        return PageEvidence(parse_error=f"bs4 indisponível: {exc}")

    try:
        sopa = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        return PageEvidence(parse_error=f"falha de parsing: {exc}")

    comentarios: list[str] = []
    for node in sopa.find_all(string=lambda s: isinstance(s, Comment)):
        comentarios.append(str(node).strip())
        node.extract()

    script_srcs: list[str] = []
    inline: list[str] = []
    resource_urls: list[str] = []
    for tag in sopa.find_all("script"):
        src = (tag.get("src") or "").strip()
        if src:
            script_srcs.append(src)
        elif tag.string:
            inline.append(tag.string)

    for tag in sopa.find_all(["img", "iframe", "link"]):
        for atributo in ("src", "href"):
            valor = (tag.get(atributo) or "").strip()
            if valor:
                resource_urls.append(valor)

    links: list[str] = []
    rel_license: list[str] = []
    for a in sopa.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        links.append(href)
        rel = a.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        if any(r.lower() == "license" for r in rel):
            rel_license.append(href)

    meta: dict[str, str] = {}
    for tag in sopa.find_all("meta"):
        meta_key = (tag.get("name") or tag.get("property") or "").strip().lower()
        valor = (tag.get("content") or "").strip()
        if meta_key and valor and meta_key not in meta:
            meta[meta_key] = valor

    input_names = [
        (tag.get("name") or "").strip().lower()
        for tag in sopa.find_all("input")
        if (tag.get("name") or "").strip()
    ]

    for tag in sopa(["script", "style", "noscript", "svg"]):
        tag.decompose()
    full_text = " ".join(sopa.get_text(separator=" ").split())
    inline_completo = "\n".join(inline)

    return PageEvidence(
        visible_text=full_text[:max_text_chars],
        script_srcs=script_srcs,
        inline_scripts=inline_completo[:max_text_chars],
        links=links,
        meta=meta,
        input_names=input_names,
        rel_license=rel_license,
        resource_urls=resource_urls,
        comments=comentarios[:50],
        text_chars_total=len(full_text),
        text_truncated=len(full_text) > max_text_chars,
        inline_scripts_truncated=len(inline_completo) > max_text_chars,
    )


def host_of(url: str) -> str:
    """Host normalizado de uma URL, com `www.` removido e sem erro para entrada inválida.

    Args:
        url: URL ou host, com ou sem esquema.

    Returns:
        Host em minúsculas, ou string vazia quando não é possível resolver.
    """
    try:
        partes = urlsplit(url if "//" in url else f"//{url}")
        host = (partes.hostname or "").lower().strip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_official_host(host: str) -> bool:
    """Indica se o host pertence a custódia pública brasileira.

    A comparação usa `OFFICIAL_SUFFIXES`, os sufixos de domínio de custódia pública:
    `.gov.br`, `.jus.br`, `.leg.br`, `.mp.br` e `.def.br`.

    Args:
        host: Host já normalizado, sem esquema e sem caminho.

    Returns:
        True quando o host termina em um dos sufixos oficiais; False em qualquer outro caso.
    """
    return bool(host) and host.endswith(OFFICIAL_SUFFIXES)


def repo_candidates(
    links: list[str], hosts: tuple = ("github.com", "gitlab.com", "codeberg.org")
) -> list[str]:
    """Links que apontam para um repositório de código em um dos hosts aceitos.

    Exige host reconhecido e pelo menos dois segmentos de caminho: perfil de usuário
    (`github.com/fulano`) não é repositório.

    Args:
        links: Valores de href coletados na página.
        hosts: Hosts de forja de código aceitos como repositório.

    Returns:
        Links originais, na ordem recebida, que apontam para um repositório.
    """
    achados: list[str] = []
    for href in links:
        partes = urlsplit(href if "//" in href else f"https://{href}")
        host = (partes.hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if host not in hosts:
            continue
        segmentos = [s for s in partes.path.split("/") if s]
        if len(segmentos) >= 2:
            achados.append(href)
    return achados
