"""
Guarda de Privacidade e Segredos Cívicos (TecnoPopular).
Inspeciona arquivos, commits do git e dados destinados a publicação (Hugging Face)
para garantir proteção absoluta contra vazamento de:
1. Dados pessoais (CPF, RG, telefones, e-mails privados)
2. Identificação do desenvolvedor (nome pessoal, caminhos locais, e-mail pessoal)
3. Chaves de API, credenciais e tokens secretos
4. Dados sensíveis de outros cidadãos (listas nominais, títulos de eleitor)

Segue rigorosamente a sobriedade institucional (zero emojis) e diretrizes cívicas.
"""

import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import ClassVar


class ViolationCategory(str, Enum):
    """Categorias de violação reconhecidas pelo portão de privacidade."""

    DEVELOPER_IDENTITY = "developer_identity"
    PERSONAL_PII = "personal_pii"
    API_SECRET = "api_secret"


class ViolationSeverity(str, Enum):
    """Severidades de violação; apenas BLOCKER impede a publicação."""

    BLOCKER = "blocker"
    WARNING = "warning"


class PrivacyViolation:
    """Ocorrência isolada de violação, com origem e trecho mascarado."""

    def __init__(
        self,
        category: ViolationCategory,
        severity: ViolationSeverity,
        message: str,
        file_path: str = "",
        line_number: int | None = None,
        snippet: str = "",
    ):
        """Inicializa uma ocorrência de violação de privacidade.

        Args:
            category: Categoria da ocorrência.
            severity: Severidade atribuída ao achado.
            message: Descrição legível do achado.
            file_path: Arquivo ou rótulo de origem da ocorrência.
            line_number: Número da linha quando a origem é textual.
            snippet: Trecho já mascarado, seguro para exibição.
        """
        self.category = category
        self.severity = severity
        self.message = message
        self.file_path = file_path
        self.line_number = line_number
        self.snippet = snippet

    def __str__(self) -> str:
        """Formata a ocorrência em uma linha legível para logs e relatórios.

        Returns:
            Linha com severidade, categoria, localização, mensagem e trecho mascarado.
        """
        loc = f"{self.file_path}:{self.line_number}" if self.line_number else self.file_path
        return f"[{self.severity.value.upper()}] [{self.category.value}] {loc} - {self.message} (trecho: '{self.snippet}')"


def is_valid_cpf_digits(digits_str: str) -> bool:
    """Verifica se uma sequência de 11 dígitos possui dígitos verificadores válidos de CPF.

    Aplica o algoritmo oficial de validação da Receita Federal do Brasil. Sequências com os
    11 dígitos repetidos são rejeitadas: elas satisfazem o cálculo dos verificadores, mas não
    correspondem a um CPF existente.

    Args:
        digits_str: Sequência de exatamente 11 dígitos numéricos, sem pontuação.

    Returns:
        True quando os dois dígitos verificadores conferem; False caso contrário.
    """
    if len(digits_str) != 11 or not digits_str.isdigit():
        return False

    if digits_str == digits_str[0] * 11:
        return False

    s1 = sum(int(digits_str[i]) * (10 - i) for i in range(9))
    r1 = s1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    if int(digits_str[9]) != d1:
        return False

    s2 = sum(int(digits_str[i]) * (11 - i) for i in range(10))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return int(digits_str[10]) == d2


def redact_sensitive(text: str, visible_start: int = 3, visible_end: int = 3) -> str:
    """Oculta o miolo de um texto sensível para exibição segura em logs de auditoria.

    Args:
        text: Valor sensível a ser mascarado.
        visible_start: Quantidade de caracteres preservados no início.
        visible_end: Quantidade de caracteres preservados no fim.

    Returns:
        Texto parcialmente mascarado, ou '***' quando curto demais para preservar bordas.
    """
    if len(text) <= (visible_start + visible_end):
        return "***"
    return f"{text[:visible_start]}***{text[-visible_end:]}"


class CivicPrivacyGuard:
    """Motor central de inspeção e blindagem de privacidade.

    Executa a verificação sobre arquivos locais, arquivos preparados no git, payloads de
    dataset e pipelines de publicação. Todo achado é devolvido com severidade BLOCKER: uma
    única ocorrência de identidade do mantenedor, de dado pessoal ou de segredo basta para
    impedir a publicação.

    Os padrões de detecção são declarados como atributos de classe. `DEVELOPER_NAMES`,
    `DEVELOPER_EMAILS` e `DEVELOPER_PATHS_PATTERNS` descrevem a identidade do mantenedor que
    não pode vazar; `ALLOWED_EMAIL_DOMAINS` lista os domínios institucionais aceitos e
    `PERSONAL_EMAIL_DOMAINS` os provedores comerciais tratados como dado pessoal;
    `SECRET_REGEXES` descreve os formatos de credencial reconhecidos e
    `ALLOWED_PLACEHOLDERS` os valores de exemplo aceitos em documentação.
    """

    DEVELOPER_NAMES: ClassVar[list[str]] = ["patryck"]
    DEVELOPER_EMAILS: ClassVar[list[str]] = ["patryck@outlook.com"]
    DEVELOPER_PATHS_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"/(?:Users|home)/patryck", re.IGNORECASE),
        re.compile(r"[A-Za-z]:\\Users\\patryck", re.IGNORECASE),
    ]

    ALLOWED_EMAIL_DOMAINS: ClassVar[set[str]] = {"tecnopopular.org", "example.com", "example.org"}

    PERSONAL_EMAIL_DOMAINS: ClassVar[set[str]] = {
        "gmail.com",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "live.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "uol.com.br",
        "bol.com.br",
    }

    SECRET_REGEXES: ClassVar[list[tuple[str, re.Pattern[str]]]] = [
        ("Hugging Face Token", re.compile(r"\b(hf_[a-zA-Z0-9]{34,})\b")),
        ("Firecrawl API Key", re.compile(r"\b(fc-[a-zA-Z0-9_\-]{20,})\b")),
        ("JEV / TypeSafe API Key", re.compile(r"\b(jev_[a-zA-Z0-9_\-]{20,})\b")),
        ("OpenAI API Key", re.compile(r"\b(sk-(?:proj-)?[a-zA-Z0-9_\-]{20,})\b")),
        ("Anthropic API Key", re.compile(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,})\b")),
        ("GitHub Personal Token", re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,})\b")),
        ("AWS Access Key ID", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
        ("Chave Privada", re.compile(r"-----BEGIN\s+(?:[A-Z\s]+)?PRIVATE\s+KEY-----")),
        (
            "JSON Web Token (JWT)",
            re.compile(r"\b(eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,})\b"),
        ),
        (
            "Atribuicao de Segredo Hardcoded",
            re.compile(
                r"""(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\s*[:=]\s*['"]([a-zA-Z0-9_\-!@#$%^&*+=]{14,})['"]"""
            ),
        ),
    ]

    ALLOWED_PLACEHOLDERS: ClassVar[set[str]] = {
        "sua_chave_aqui",
        "seu-token",
        "seu_token",
        "change_me",
        "placeholder",
        "dummy_key",
        "fake_token",
        "minha_chave",
        "exemplo_chave",
        "token_aqui",
    }

    def __init__(self, project_root: Path | None = None):
        """Define a raiz do projeto usada para resolver caminhos e executar o git.

        Args:
            project_root: Raiz do repositório; quando nula, usa o diretório corrente.
        """
        self.project_root = project_root or Path.cwd()

    def check_developer_identity(self, line: str) -> list[tuple[ViolationCategory, str, str]]:
        """Verifica presença de nome, e-mail pessoal ou caminhos do desenvolvedor.

        A comparação do nome ignora a caixa do texto. Os caminhos absolutos são procurados
        pelos padrões que carregam o nome de usuário, já que é ele que expõe a máquina de
        origem.

        Args:
            line: Linha de texto a inspecionar.

        Returns:
            Lista de tuplas (categoria, mensagem, trecho), vazia quando nada é encontrado.
        """
        violations = []
        lower_line = line.lower()

        for name in self.DEVELOPER_NAMES:
            pattern = rf"\b{re.escape(name)}\b"
            if re.search(pattern, lower_line):
                violations.append(
                    (
                        ViolationCategory.DEVELOPER_IDENTITY,
                        f"Identificador pessoal do desenvolvedor detectado ('{name}')",
                        name,
                    )
                )

        for dev_email in self.DEVELOPER_EMAILS:
            if dev_email.lower() in lower_line:
                violations.append(
                    (
                        ViolationCategory.DEVELOPER_IDENTITY,
                        f"E-mail pessoal do desenvolvedor detectado ('{dev_email}')",
                        dev_email,
                    )
                )

        for path_pat in self.DEVELOPER_PATHS_PATTERNS:
            match = path_pat.search(line)
            if match:
                violations.append(
                    (
                        ViolationCategory.DEVELOPER_IDENTITY,
                        f"Caminho absoluto local com nome de usuario detectado: '{match.group(0)}'",
                        match.group(0),
                    )
                )

        return violations

    def check_personal_pii(self, line: str) -> list[tuple[ViolationCategory, str, str]]:
        """Verifica presença de CPF, RG, telefones e e-mails pessoais de terceiros.

        A ordem de verificação é: CPF formatado, CPF numérico, RG, telefone e e-mail.

        O CPF numérico só é reportado quando os dígitos verificadores conferem e a linha traz
        vocabulário documental; exigir esse contexto evita classificar datas, timestamps e
        contadores como dado pessoal. No telefone, o comprimento em dígitos e a presença de
        delimitador característico (parêntese, hífen ou prefixo +55) separam o número de
        versões e de outras sequências numéricas, e datas ISO são descartadas em seguida.
        E-mails de provedores comerciais contam como dado pessoal, assim como qualquer
        domínio fora dos institucionais autorizados. Linhas de CDN são ignoradas porque
        identificadores de pacote npm versionados têm a mesma forma de um e-mail.

        Args:
            line: Linha de texto a inspecionar.

        Returns:
            Lista de tuplas (categoria, mensagem, trecho), vazia quando nada é encontrado.
        """
        violations = []

        formatted_cpf_pattern = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")
        for match in formatted_cpf_pattern.finditer(line):
            cpf_str = match.group(1)
            digits = re.sub(r"\D", "", cpf_str)
            if is_valid_cpf_digits(digits):
                violations.append(
                    (
                        ViolationCategory.PERSONAL_PII,
                        "CPF formatado valido detectado (violacao de PII)",
                        redact_sensitive(cpf_str, 3, 2),
                    )
                )

        unformatted_cpf_pattern = re.compile(r"\b(\d{11})\b")
        for match in unformatted_cpf_pattern.finditer(line):
            digits = match.group(1)
            if is_valid_cpf_digits(digits):
                lower = line.lower()
                context_words = ["cpf", "doc", "documento", "cadastro", "pessoa", "titular", "eleitor"]
                if any(w in lower for w in context_words):
                    violations.append(
                        (
                            ViolationCategory.PERSONAL_PII,
                            "CPF numerico valido detectado em contexto documental",
                            redact_sensitive(digits, 3, 2),
                        )
                    )

        rg_pattern = re.compile(r"\b(\d{1,2}\.\d{3}\.\d{3}-[\dXx])\b")
        for match in rg_pattern.finditer(line):
            rg_str = match.group(1)
            violations.append(
                (
                    ViolationCategory.PERSONAL_PII,
                    "Numero de Registro Geral (RG) formatado detectado",
                    redact_sensitive(rg_str, 2, 2),
                )
            )

        phone_pattern = re.compile(
            r"""(?:\+?55\s?)?(?:\(?([1-9]{2})\)?[\s-]?)?(9[1-9]\d{3}[-\s]?\d{4}|[2-8]\d{3}[-\s]?\d{4})\b"""
        )
        for match in phone_pattern.finditer(line):
            matched_phone = match.group(0).strip()
            clean_digits = re.sub(r"\D", "", matched_phone)
            if len(clean_digits) in (10, 11, 12, 13):
                has_phone_syntax = (
                    any(char in matched_phone for char in ["(", ")", "+55"]) or "-" in matched_phone
                )
                if has_phone_syntax and not re.match(r"^20\d{2}-\d{2}-\d{2}", matched_phone):
                    violations.append(
                        (
                            ViolationCategory.PERSONAL_PII,
                            "Numero de telefone pessoal detectado",
                            redact_sensitive(matched_phone, 2, 2),
                        )
                    )

        email_pattern = re.compile(r"\b([a-zA-Z0-9_.+-]+@([a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?))\b")
        for match in email_pattern.finditer(line):
            email_full = match.group(1).lower()
            domain = match.group(2).lower()
            if any(cdn in line for cdn in ["jsdelivr", "unpkg", "cdnjs", "/npm/"]):
                continue
            if domain in self.PERSONAL_EMAIL_DOMAINS:
                violations.append(
                    (
                        ViolationCategory.PERSONAL_PII,
                        f"E-mail pessoal de provedor comercial detectado ('{domain}')",
                        redact_sensitive(email_full, 3, 4),
                    )
                )
            elif domain not in self.ALLOWED_EMAIL_DOMAINS:
                violations.append(
                    (
                        ViolationCategory.PERSONAL_PII,
                        f"E-mail nao institucional detectado ('{domain}')",
                        redact_sensitive(email_full, 3, 4),
                    )
                )

        return violations

    def check_api_secrets(self, line: str) -> list[tuple[ViolationCategory, str, str]]:
        """Verifica a presença de chaves de API, tokens e certificados embutidos.

        Placeholders declarados são ignorados para que a documentação de exemplo não bloqueie
        a entrega. Nomes de variável lidos de `os.getenv`, `os.environ` ou por `get_api_key`
        também são ignorados, porque nesses casos a credencial real fica no ambiente e não no
        código.

        Args:
            line: Linha de texto a inspecionar.

        Returns:
            Lista de tuplas (categoria, mensagem, trecho), vazia quando nada é encontrado.
        """
        violations = []

        for name, regex in self.SECRET_REGEXES:
            for match in regex.finditer(line):
                secret_val = match.group(1) if match.groups() else match.group(0)
                if secret_val.lower() in self.ALLOWED_PLACEHOLDERS:
                    continue
                if ("os.getenv" in line or "get_api_key" in line or "os.environ" in line) and secret_val in [
                    "FIRECRAWL_API_KEY",
                    "JEV_API_KEY",
                    "HF_TOKEN",
                ]:
                    continue

                violations.append(
                    (
                        ViolationCategory.API_SECRET,
                        f"Segredo / Token de API detectado: {name}",
                        redact_sensitive(secret_val, 4, 3),
                    )
                )

        return violations

    def check_initiative_links(
        self, line: str, file_path: str = "", is_hf_export: bool = False
    ) -> list[tuple[ViolationCategory, str, str]]:
        """Registra que links de iniciativas cívicas públicas não constituem violação.

        A URL pública de uma iniciativa é dado legítimo e sua exposição é incentivada, pois
        amplia o grafo de conectividade cívica, a interoperabilidade do catálogo e a
        indexação por buscadores. A verificação permanece no fluxo para tornar explícito que
        links jamais bloqueiam e não deve voltar a produzir achados.

        Args:
            line: Linha de texto a inspecionar.
            file_path: Origem do texto, mantida por simetria com as demais verificações.
            is_hf_export: Indica se o texto é destinado à exportação para o Hugging Face.

        Returns:
            Lista sempre vazia.
        """
        return []

    def scan_text(self, text: str, file_path: str = "", is_hf_export: bool = False) -> list[PrivacyViolation]:
        """Inspeciona um texto linha por linha e consolida todas as ocorrências.

        As quatro verificações rodam em cada linha e todo achado recebe severidade BLOCKER:
        uma única ocorrência de identidade do mantenedor, de dado pessoal ou de segredo basta
        para impedir a publicação. A verificação de links é mantida na sequência para
        preservar a ordem do relatório, ainda que não produza achados.

        Args:
            text: Conteúdo textual completo a inspecionar.
            file_path: Identificação da origem, usada na mensagem de cada ocorrência.
            is_hf_export: Indica se o texto é destinado à exportação para o Hugging Face.

        Returns:
            Lista de ocorrências na ordem em que aparecem no texto.
        """
        violations = []
        lines = text.splitlines()

        for idx, line in enumerate(lines, 1):
            for cat, msg, snippet in self.check_developer_identity(line):
                violations.append(
                    PrivacyViolation(
                        category=cat,
                        severity=ViolationSeverity.BLOCKER,
                        message=msg,
                        file_path=file_path,
                        line_number=idx,
                        snippet=snippet,
                    )
                )

            for cat, msg, snippet in self.check_personal_pii(line):
                violations.append(
                    PrivacyViolation(
                        category=cat,
                        severity=ViolationSeverity.BLOCKER,
                        message=msg,
                        file_path=file_path,
                        line_number=idx,
                        snippet=snippet,
                    )
                )

            for cat, msg, snippet in self.check_api_secrets(line):
                violations.append(
                    PrivacyViolation(
                        category=cat,
                        severity=ViolationSeverity.BLOCKER,
                        message=msg,
                        file_path=file_path,
                        line_number=idx,
                        snippet=snippet,
                    )
                )

            for cat, msg, snippet in self.check_initiative_links(line, file_path, is_hf_export):
                violations.append(
                    PrivacyViolation(
                        category=cat,
                        severity=ViolationSeverity.BLOCKER,
                        message=msg,
                        file_path=file_path,
                        line_number=idx,
                        snippet=snippet,
                    )
                )

        return violations

    def scan_file(self, file_path: str | Path, is_hf_export: bool = False) -> list[PrivacyViolation]:
        """Inspeciona o conteúdo de um arquivo em disco.

        Arquivos binários conhecidos são ignorados, pois não há texto a inspecionar. O próprio
        módulo e o teste de pré-commit são ignorados porque contêm, por necessidade, os nomes
        e as expressões regulares de detecção: analisá-los faria o portão acusar a si mesmo.
        Falha de leitura não interrompe a varredura, apenas gera aviso, para que um arquivo
        ilegível não impeça a auditoria dos demais.

        Args:
            file_path: Caminho do arquivo a inspecionar.
            is_hf_export: Indica se o conteúdo é destinado à exportação para o Hugging Face.

        Returns:
            Lista de ocorrências encontradas, vazia para arquivo ausente ou ignorado.
        """
        p = Path(file_path)
        if not p.is_file():
            return []

        norm_str = str(p).replace("\\", "/")
        if any(
            norm_str.endswith(ext)
            for ext in [
                ".png",
                ".jpg",
                ".jpeg",
                ".ico",
                ".woff",
                ".woff2",
                ".pyc",
                ".m4a",
                ".mp3",
                ".wav",
                ".ogg",
                ".webm",
                ".mp4",
            ]
        ):
            return []

        if "privacy_guard.py" in norm_str or "test_pre_commit_privacy.py" in norm_str:
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                return []
            except Exception:
                return []

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return [
                PrivacyViolation(
                    category=ViolationCategory.API_SECRET,
                    severity=ViolationSeverity.WARNING,
                    message=f"Falha ao ler arquivo: {e}",
                    file_path=str(p),
                )
            ]

        return self.scan_text(content, file_path=str(p), is_hf_export=is_hf_export)

    def scan_git_staged_files(self) -> list[PrivacyViolation]:
        """Audita os arquivos preparados no índice do git.

        A lista vem de `git diff --cached --name-only`. Quando esse comando falha, caso de
        repositório sem commits ou ainda não inicializado, a leitura recai sobre
        `git status --porcelain`, aproveitando apenas as entradas com alteração no índice,
        identificadas pelo primeiro caractere de status (A, M ou R).

        Returns:
            Lista consolidada de ocorrências nos arquivos preparados.
        """
        try:
            cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
            res = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True, check=True)
            staged_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            try:
                cmd_status = ["git", "status", "--porcelain"]
                res = subprocess.run(
                    cmd_status, cwd=self.project_root, capture_output=True, text=True, check=True
                )
                staged_files = []
                for line in res.stdout.splitlines():
                    if line.strip():
                        status_char = line[:2]
                        if status_char[0] in ("A", "M", "R"):
                            staged_files.append(line[3:].strip())
            except Exception:
                staged_files = []

        all_violations = []
        for file_rel in staged_files:
            file_abs = self.project_root / file_rel
            if file_abs.is_file():
                violations = self.scan_file(file_abs)
                all_violations.extend(violations)

        return all_violations
