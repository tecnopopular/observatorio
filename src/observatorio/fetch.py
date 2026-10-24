"""
src/observatorio/fetch.py
Camada de aquisição de conteúdo: provedores de download do HTML público.

Dois provedores com papéis distintos:

- `HttpFetcher`: requisição direta com TLS verificado. É o padrão, não custa cota e
  serve para a maioria das páginas renderizadas no servidor.
- `FirecrawlFetcher`: renderização remota via API do Firecrawl. Necessário para
  aplicações de página única (SPA), onde o HTML estático é um contêiner vazio.

Regras de segurança e honestidade embutidas aqui:

- TLS é verificado por padrão. Requisição sem validação é possível, mas explícita e
  registrada em `FetchResult.tls_verified=False` — o laudo nunca deve fingir que
  inspecionou uma cadeia de certificados que não foi verificada.
- O User-Agent identifica o observatório como agente automatizado. Não há personificação
  de navegador: um bot de auditoria cívica se apresenta como tal.
- Falha de rede devolve `FetchResult` com `error` preenchido, não exceção: uma iniciativa
  fora do ar não pode abortar a esteira das outras.
- Nenhuma mensagem de erro inclui chave de API.
"""

import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field

from .credentials import get_api_key

AUTH_HINTS = ("/auth", "/login", "/signin", "/sso", "share.streamlit.io/-/auth", "accounts.google.com")

INSTITUTIONAL_USER_AGENT = (
    "TecnoPopular-Observatorio/1.0 "
    "(+https://tecnopopular.org/metodologia; contato@tecnopopular.org) auditoria-civica"
)
MAX_PAYLOAD_BYTES = 1_000_000
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_MAX_ATTEMPTS = 3
FIRECRAWL_MAX_WAIT_SECONDS = 60


class FetchResult(BaseModel):
    """Resultado de uma tentativa de aquisição de conteúdo público."""

    url: str
    provider: str
    html: str = ""
    status_code: int | None = None
    latency_ms: int = 0
    content_bytes: int = 0
    declared_bytes: int | None = Field(
        default=None, description="Tamanho declarado pelo servidor (Content-Length), quando disponível"
    )
    truncated: bool = Field(
        default=False, description="Verdadeiro quando a leitura foi interrompida no limite"
    )
    tls_verified: bool = True
    from_cache: bool = False
    final_url: str | None = None
    access_gate: bool = Field(
        default=False, description="Verdadeiro quando o destino exige autenticação ou bloqueia a leitura"
    )
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Indica se a aquisição produziu conteúdo utilizável.

        Returns:
            Verdadeiro quando não há erro registrado e o HTML não está vazio.
        """
        return not self.error and bool(self.html)


class HttpFetcher:
    """Aquisição direta via HTTP, com TLS verificado por padrão."""

    name = "http"

    def __init__(
        self,
        timeout: int = 8,
        max_bytes: int = MAX_PAYLOAD_BYTES,
        verify_tls: bool = True,
        user_agent: str = INSTITUTIONAL_USER_AGENT,
        max_redirects: int = 5,
    ):
        """Configura o cliente HTTP direto.

        Args:
            timeout: Tempo máximo, em segundos, de cada requisição.
            max_bytes: Limite de bytes lidos do corpo da resposta.
            verify_tls: Verifica a cadeia de certificados quando verdadeiro; o valor usado
                é registrado em `FetchResult.tls_verified` para não alegar verificação que
                não ocorreu.
            user_agent: Cabeçalho `User-Agent` enviado na requisição.
            max_redirects: Quantidade máxima de redirecionamentos seguidos.
        """
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.verify_tls = verify_tls
        self.user_agent = user_agent
        self.max_redirects = max_redirects

    def _context(self) -> ssl.SSLContext:
        """Monta o contexto TLS usado na requisição.

        Returns:
            Contexto SSL com verificação de certificado e de hostname, desativadas apenas
            quando `verify_tls` é falso.
        """
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _looks_like_gate(self, url: str) -> bool:
        """Reconhece destino de tela de autenticação a partir das pistas de `AUTH_HINTS`.

        Um redirecionamento para `/login`, `/auth`, provedor de identidade ou equivalente
        não entrega o conteúdo da iniciativa: entrega a barreira de acesso. Por isso esse
        destino é classificado como acesso restrito, e não como página vazia, para que o
        diagnóstico distinga bloqueio de ausência de conteúdo.

        Args:
            url: Endereço de destino efetivo da resposta.

        Returns:
            Verdadeiro quando alguma pista de autenticação aparece no endereço.
        """
        alvo = (url or "").lower()
        return any(pista in alvo for pista in AUTH_HINTS)

    def fetch(self, url: str) -> FetchResult:
        """Baixa o HTML do endereço informado, seguindo redirecionamentos limitados.

        O corpo é lido com `max_bytes + 1` bytes de propósito: o byte excedente prova que
        houve corte mesmo quando a resposta vem em `chunked` e o servidor não declara
        `Content-Length` — situação em que o truncamento antes passava em silêncio.

        Falhas de rede, DNS, timeout, certificado inválido e payload ilegível são
        convertidas em `FetchResult` com `error` preenchido, em vez de propagadas: uma
        iniciativa indisponível não pode abortar a esteira das demais.

        Args:
            url: Endereço público a baixar.

        Returns:
            `FetchResult` com HTML, código de status, latência e marcação de truncamento,
            ou com a descrição da falha em `error`.
        """
        atual = url
        start = time.time()
        for _ in range(self.max_redirects + 1):
            request = urllib.request.Request(
                atual,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._context()
                ) as response:
                    elapsed = int((time.time() - start) * 1000)
                    final = getattr(response, "url", atual)
                    if self._looks_like_gate(final):
                        return FetchResult(
                            url=url,
                            provider=self.name,
                            status_code=getattr(response, "status", None),
                            latency_ms=elapsed,
                            tls_verified=self.verify_tls,
                            final_url=final,
                            access_gate=True,
                            error="acesso restrito: redirecionado para autenticação",
                        )
                    declarado = None
                    cabecalho = (
                        response.headers.get("Content-Length") if hasattr(response, "headers") else None
                    )
                    if cabecalho and str(cabecalho).isdigit():
                        declarado = int(cabecalho)
                    bruto = response.read(self.max_bytes + 1)
                    truncado = len(bruto) > self.max_bytes or bool(declarado and declarado > len(bruto))
                    raw = bruto[: self.max_bytes]
                    return FetchResult(
                        url=url,
                        provider=self.name,
                        html=raw.decode("utf-8", errors="ignore"),
                        status_code=getattr(response, "status", None),
                        latency_ms=elapsed,
                        content_bytes=len(raw),
                        declared_bytes=declarado
                        if declarado is not None
                        else (len(raw) if not truncado else None),
                        truncated=truncado,
                        tls_verified=self.verify_tls,
                        final_url=final,
                    )
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    destino = exc.headers.get("Location") if exc.headers else None
                    if not destino:
                        elapsed = int((time.time() - start) * 1000)
                        return FetchResult(
                            url=url,
                            provider=self.name,
                            status_code=exc.code,
                            latency_ms=elapsed,
                            tls_verified=self.verify_tls,
                            final_url=atual,
                            error=f"HTTP {exc.code} sem Location",
                        )
                    proximo = urljoin(atual, destino)
                    if self._looks_like_gate(proximo):
                        elapsed = int((time.time() - start) * 1000)
                        return FetchResult(
                            url=url,
                            provider=self.name,
                            status_code=exc.code,
                            latency_ms=elapsed,
                            tls_verified=self.verify_tls,
                            final_url=proximo,
                            access_gate=True,
                            error="acesso restrito: redirecionado para autenticação",
                        )
                    atual = proximo
                    continue
                elapsed = int((time.time() - start) * 1000)
                if exc.code in (401, 403):
                    return FetchResult(
                        url=url,
                        provider=self.name,
                        status_code=exc.code,
                        latency_ms=elapsed,
                        tls_verified=self.verify_tls,
                        final_url=atual,
                        access_gate=True,
                        error=f"HTTP {exc.code} (acesso restrito)",
                    )
                return FetchResult(
                    url=url,
                    provider=self.name,
                    status_code=exc.code,
                    latency_ms=elapsed,
                    tls_verified=self.verify_tls,
                    final_url=atual,
                    error=f"HTTP {exc.code}",
                )
            except urllib.error.URLError as exc:
                elapsed = int((time.time() - start) * 1000)
                return FetchResult(
                    url=url,
                    provider=self.name,
                    latency_ms=elapsed,
                    tls_verified=self.verify_tls,
                    final_url=atual,
                    error=f"falha de rede: {exc.reason}",
                )
            except Exception as exc:
                elapsed = int((time.time() - start) * 1000)
                return FetchResult(
                    url=url,
                    provider=self.name,
                    latency_ms=elapsed,
                    tls_verified=self.verify_tls,
                    final_url=atual,
                    error=f"{type(exc).__name__}: {exc}",
                )

        elapsed = int((time.time() - start) * 1000)
        return FetchResult(
            url=url,
            provider=self.name,
            latency_ms=elapsed,
            tls_verified=self.verify_tls,
            final_url=atual,
            error=f"excesso de redirecionamentos ({self.max_redirects})",
        )


class FirecrawlFetcher:
    """Aquisição com renderização remota via API do Firecrawl (SPA e conteúdo dinâmico)."""

    name = "firecrawl"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str = FIRECRAWL_ENDPOINT,
        timeout: int = 30,
        max_bytes: int = MAX_PAYLOAD_BYTES,
    ):
        """Configura o cliente de renderização remota.

        Args:
            api_key: Chave da API; quando ausente, é buscada em `FIRECRAWL_API_KEY` no
                ambiente ou no Keychain.
            endpoint: URL do serviço de renderização.
            timeout: Tempo máximo, em segundos, de cada requisição.
            max_bytes: Limite de caracteres do HTML devolvido.
        """
        self.api_key = api_key or get_api_key("FIRECRAWL_API_KEY")
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_bytes = max_bytes

    def _post_com_retry(self, payload: dict) -> tuple[object | None, list[int]]:
        """Envia a requisição de captura, repetindo quando o serviço pede espera.

        O limite do Firecrawl é por minuto e responde 429 com `Retry-After` curto. Esperar o
        tempo informado é melhor que um intervalo fixo: curto demais queima tentativas, longo
        demais custa minutos por página. Esgotadas as tentativas, devolve `None` para que o
        chamador registre limite de serviço, e não página ilegível.

        Args:
            payload: Corpo JSON da requisição de captura.

        Returns:
            Par com a resposta HTTP bem-sucedida, ou `None` quando o limite persiste, e a lista
            de esperas aplicadas em segundos.
        """
        esperas: list[int] = []
        for tentativa in range(1, FIRECRAWL_MAX_ATTEMPTS + 1):
            with httpx.Client(timeout=self.timeout) as client:
                resposta = client.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
            if resposta.status_code != 429:
                return resposta, esperas
            if tentativa == FIRECRAWL_MAX_ATTEMPTS:
                return None, esperas
            espera = self._espera_pedida(resposta)
            esperas.append(espera)
            time.sleep(espera)
        return None, esperas

    @staticmethod
    def _espera_pedida(resposta: object) -> int:
        """Lê o `Retry-After` da resposta, limitado ao teto configurado.

        Args:
            resposta: Resposta HTTP do serviço.

        Returns:
            Segundos a esperar; zero quando o cabeçalho está ausente ou ilegível.
        """
        cabecalho = getattr(resposta, "headers", {}) or {}
        bruto = cabecalho.get("retry-after") or cabecalho.get("Retry-After") or "0"
        try:
            return max(0, min(int(str(bruto).strip()), FIRECRAWL_MAX_WAIT_SECONDS))
        except ValueError:
            return 0

    def fetch(self, url: str) -> FetchResult:
        """Renderiza o endereço remotamente e devolve o HTML resultante.

        O HTML é cortado em `max_bytes` caracteres e a marcação `truncated` registra o
        corte, para que o diagnóstico não trate conteúdo incompleto como íntegro. Falhas
        do serviço são devolvidas como `error`, com a chave de API nunca incluída na
        mensagem.

        Args:
            url: Endereço público a renderizar.

        Returns:
            `FetchResult` com HTML renderizado e metadados, ou com a descrição da falha
            em `error`.
        """
        if not self.api_key:
            return FetchResult(
                url=url,
                provider=self.name,
                error="FIRECRAWL_API_KEY ausente (ambiente ou Keychain)",
            )

        payload = {
            "url": url,
            "formats": ["rawHtml"],
            "onlyMainContent": False,
            "timeout": self.timeout * 1000,
        }
        start = time.time()
        try:
            resposta, esperas = self._post_com_retry(payload)
            elapsed = int((time.time() - start) * 1000)

            if resposta is None:
                return FetchResult(
                    url=url,
                    provider=self.name,
                    status_code=429,
                    latency_ms=elapsed,
                    error=(
                        f"firecrawl HTTP 429: limite do serviço após {FIRECRAWL_MAX_ATTEMPTS} tentativas "
                        f"(esperas: {', '.join(str(s) for s in esperas)}s)"
                    ),
                )

            response = resposta
            if response.status_code >= 400:
                return FetchResult(
                    url=url,
                    provider=self.name,
                    status_code=response.status_code,
                    latency_ms=elapsed,
                    error=f"firecrawl HTTP {response.status_code}",
                )

            body = response.json()
            if not body.get("success", False):
                motivo = str(body.get("error") or "resposta sem sucesso")[:200]
                return FetchResult(
                    url=url, provider=self.name, latency_ms=elapsed, error=f"firecrawl: {motivo}"
                )

            data = body.get("data") or {}
            html = data.get("rawHtml") or data.get("html") or ""
            metadata = data.get("metadata") or {}
            return FetchResult(
                url=url,
                provider=self.name,
                html=html[: self.max_bytes],
                status_code=metadata.get("statusCode"),
                latency_ms=elapsed,
                content_bytes=len(html.encode("utf-8", errors="ignore")),
                declared_bytes=len(html.encode("utf-8", errors="ignore")),
                truncated=len(html) > self.max_bytes,
                tls_verified=True,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return FetchResult(
                url=url,
                provider=self.name,
                latency_ms=elapsed,
                error=f"firecrawl {type(exc).__name__}: {exc}"[:300],
            )


PROVIDERS = {
    HttpFetcher.name: HttpFetcher,
    FirecrawlFetcher.name: FirecrawlFetcher,
}


class ContentFetcher(Protocol):
    """Contrato mínimo de um provedor de aquisição."""

    name: str

    def fetch(self, url: str) -> FetchResult:
        """Baixa o conteúdo do endereço informado.

        Args:
            url: Endereço público a baixar.

        Returns:
            `FetchResult` do provedor, com erro registrado em caso de falha.
        """
        ...


def build_fetcher(
    provider: str = "http", timeout: int = 8, max_bytes: int = MAX_PAYLOAD_BYTES
) -> ContentFetcher:
    """Instancia o provedor de aquisição pedido.

    Args:
        provider: Nome do provedor, entre os registrados em `PROVIDERS`.
        timeout: Tempo máximo, em segundos, de cada requisição.
        max_bytes: Limite de bytes lidos do corpo da resposta.

    Returns:
        Instância do provedor correspondente ao nome informado.

    Raises:
        ValueError: Quando o nome não consta em `PROVIDERS`; provedor desconhecido é erro
            de contrato, não deve ser tratado como ausência silenciosa de conteúdo.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"provedor de aquisição desconhecido: {provider}")
    return PROVIDERS[provider](timeout=timeout, max_bytes=max_bytes)


def fetch_with_fallback(
    url: str,
    fetchers: Sequence[ContentFetcher],
) -> FetchResult:
    """Tenta os provedores em ordem e devolve o primeiro resultado bem-sucedido.

    Quando todos falham, devolve o último resultado, de modo que o erro relatado seja o
    do provedor mais recente tentado, e não um erro genérico.

    Args:
        url: Endereço público a baixar.
        fetchers: Provedores na ordem de preferência.

    Returns:
        Primeiro `FetchResult` com sucesso, ou o último resultado obtido.
    """
    last = FetchResult(url=url, provider="none", error="nenhum provedor configurado")
    for fetcher in fetchers:
        resultado = fetcher.fetch(url)
        if resultado.ok:
            return resultado
        last = resultado
    return last
