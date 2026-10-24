"""Preservação histórica e integridade criptográfica do Observatório TecnoPopular.

Cobre o cálculo determinístico de SHA-256 sobre snapshots em memória, a invalidação de cache
por conteúdo (content-addressable) e o disparo assíncrono ao Internet Archive (Save Page Now).
"""

import hashlib
import urllib.error
import urllib.request

from .fetch import INSTITUTIONAL_USER_AGENT


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


def archive_target(target_url: str, timeout: int = 10) -> str | None:
    """Despacha solicitação de captura assíncrona ao Internet Archive (Wayback Machine).

    A URL é lida do cabeçalho `Content-Location`; quando ele não vem, aceita-se `Location`, que
    é o que o serviço devolve em redirecionamentos 302. Falha de rede ou limite de taxa do
    Internet Archive não deve bloquear a esteira, por isso o erro é convertido em None.

    Args:
        target_url: URL pública a preservar.
        timeout: Tempo limite da requisição, em segundos.

    Returns:
        URL do snapshot permanente, ou None quando a captura falha ou não informa localização.
    """
    save_endpoint = f"https://web.archive.org/save/{target_url}"
    req = urllib.request.Request(
        save_endpoint,
        headers={"User-Agent": INSTITUTIONAL_USER_AGENT},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_loc = resp.headers.get("Content-Location")
            if content_loc:
                if not content_loc.startswith("http"):
                    return f"https://web.archive.org{content_loc}"
                return content_loc
            loc = resp.headers.get("Location")
            if loc:
                if not loc.startswith("http"):
                    return f"https://web.archive.org{loc}"
                return loc
            return None
    except Exception:
        return None
