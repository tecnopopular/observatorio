"""
src/observatorio/fetch.py
Camada de aquisição de conteúdo: provedores de download do HTML público.

Dois provedores com papéis distintos:

- `HttpFetcher`: requisição direta com TLS verificado. É o padrão, não custa cota e
  serve para a maioria das páginas renderizadas no servidor.
  aplicações de página única (SPA), onde o HTML estático é um contêiner vazio.

Regras de segurança e honestidade embutidas aqui:

- TLS é verificado por padrão. Requisição sem validação é possível, mas explícita e
  registrada em `FetchResult.tls_verified=False` — o laudo nunca deve fingir que
  inspecionou uma cadeia de certificados que não foi verificada.
- O User-Agent identifica o crawler do observatório pelo nome. Não há personificação de
  navegador: o crawler de uma auditoria cívica se apresenta como tal, e é por esse nome
  (`CRAWLER_TOKEN`) que quem quiser ser excluído nos bloqueia no robots.txt.
- Falha de rede devolve `FetchResult` com `error` preenchido, não exceção: uma iniciativa
  fora do ar não pode abortar a esteira das outras.
- Nenhuma mensagem de erro inclui chave de API.
"""

import ssl
import time
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, Field

AUTH_HINTS = ("/auth", "/login", "/signin", "/sso", "share.streamlit.io/-/auth", "accounts.google.com")

CRAWLER_TOKEN = "TecnoPopular-Observatorio"

INSTITUTIONAL_USER_AGENT = (
    f"{CRAWLER_TOKEN}/1.0 (+https://tecnopopular.org/metodologia; contato@tecnopopular.org) auditoria-civica"
)
MAX_PAYLOAD_BYTES = 1_000_000


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


def probe_https(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Confirma que um endereço responde em https antes de gravá-lo no catálogo.

    Qualquer resposta HTTP serve como prova de que o host existe no esquema: um 403 ou um 404 são
    respostas de um servidor, e o que se quer descartar é host que não atende em https.

    Args:
        url: Endereço completo a sondar.
        timeout: Tempo máximo de espera em segundos.

    Returns:
        Par com indicador de resposta e motivo legível.
    """
    pedido = urllib.request.Request(url, headers={"User-Agent": INSTITUTIONAL_USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return True, f"HTTP {resposta.status}"
    except urllib.error.HTTPError as erro:
        return True, f"HTTP {erro.code}"
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as erro:
        return False, type(erro).__name__
