"""Conferência dos anexos de uma release: tipo declarado e ausência de dado pessoal.

O portão de privacidade do repositório inspeciona arquivos rastreados pelo git. O que vai anexado a
uma release nasce fora disso — é gerado no runner — e por isso precisa de verificação própria antes
do upload. A lista de tipos aceitos é fechada de propósito: HTML bruto de terceiros não se publica,
nem como corpo de página, nem empacotado.
"""

from pathlib import Path

from .privacy_guard import CivicPrivacyGuard, ViolationSeverity

ATTACHMENT_TYPES = frozenset({".json", ".txt"})


def verify_attachments(paths: list[str | Path], guard: CivicPrivacyGuard | None = None) -> list[str]:
    """Confere tipo e conteúdo dos arquivos que serão anexados a uma release.

    Args:
        paths: Arquivos prestes a serem anexados.
        guard: Guarda de privacidade a reutilizar; quando nula, uma é construída.

    Returns:
        Lista de problemas encontrados, vazia quando todos os anexos passam.
    """
    guard = guard or CivicPrivacyGuard()
    problems: list[str] = []
    for path in paths:
        arquivo = Path(path)
        if not arquivo.is_file():
            problems.append(f"{arquivo}: arquivo ausente")
            continue
        if arquivo.suffix.lower() not in ATTACHMENT_TYPES:
            problems.append(
                f"{arquivo.name}: tipo {arquivo.suffix or 'sem extensão'} não é publicável; "
                "HTML bruto de terceiros fica fora da release"
            )
            continue
        for achado in guard.scan_file(arquivo):
            if achado.severity == ViolationSeverity.BLOCKER:
                problems.append(f"{arquivo.name}: {achado.message}")
    return problems
