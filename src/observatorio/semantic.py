"""
src/observatorio/semantic.py
Camada semântica calibrada do Protocolo de Avaliação Cívica TecnoPopular (JEV / TypeSafe AI).

O motor determinístico decide o que é verificável por inspeção (links oficiais, trackers,
licença, latência). Esta camada cobre o que só se resolve por leitura de vocabulário: se o
texto se enquadra em utilidade pública e direitos materiais, e em que medida mobiliza ataque
pessoal ou hostilidade. Os dois enquadramentos não são exclusivos — um texto pode afirmar
direitos e atacar adversários ao mesmo tempo — por isso a classificação usa `noul` por rótulo,
e não uma escolha única.

Contrato verificado contra a API real (POST https://api.typesafe.ai/v1/systemone):

    questions["<nome>"] = {"type": "noul",  "instructions": "..."}
        -> answers["<nome>"] = {"type": "noul", "noul": 0.96}

    questions["<nome>"] = {"type": "score", "instructions": "...", "criteria": [lista de níveis]}
        -> answers["<nome>"] = {"score": 3.01, "confidence": 0.76, "probabilities": {...}, "legend": {...}}

Detalhes do contrato descobertos por sondagem, não por suposição:
- `score.criteria` precisa ser **lista** (dicionário devolve HTTP 422); o `legend` volta indexado de 0.
- `model: "jev-latest"` é alias: a resposta reporta o modelo resolvido (ex.: jev-1.13.0).
- credencial: `TYPESAFE_API_KEY` (convenção do SDK), com `JEV_API_KEY` como compatibilidade.

Regras:
- A camada fornece evidência; a nota continua sendo decidida pelo avaliador determinístico.
  Ajuste de nota exige limiar alto e é registrado em `semantic_adjustment_applied`.
- Falha de API é dado, não exceção: `SemanticResult` com `error`.
- A chave nunca aparece em mensagem de erro.
- Envia-se o texto visível truncado, nunca o HTML bruto inteiro.
"""

import time
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from .credentials import get_api_key

TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
MAX_INPUT_CHARS = 4_000

LABEL_PROPOSITIVO = "propositivo"
LABEL_POLARIZADOR = "polarizador"

LABEL_INSTRUCTIONS: dict[str, str] = {
    LABEL_PROPOSITIVO: (
        "O texto se enquadra em utilidade pública: direitos sociais, serviços essenciais, "
        "transparência de dados oficiais e controle social."
    ),
    LABEL_POLARIZADOR: (
        "O texto mobiliza ataque pessoal, rótulo pejorativo contra pessoas ou grupos, ou "
        "convocação de hostilidade como método de disputa."
    ),
}

INTENSITY_QUESTION = "intensidade_polarizadora"
INTENSITY_LEVELS = [
    "nenhum enquadramento polarizador",
    "residual",
    "moderado",
    "alto",
    "máximo",
]


class SemanticResult(BaseModel):
    """Resultado da classificação semântica, com rastro de auditoria."""

    provider: str = "off"
    model: str | None = None
    labels: dict[str, float] = Field(default_factory=dict, description="Probabilidade por rótulo (noul)")
    intensity: float | None = Field(
        default=None, description="Intensidade do enquadramento polarizador (score)"
    )
    intensity_confidence: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    input_chars: int = 0
    latency_ms: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Indica se a classificação produziu rótulos utilizáveis.

        Returns:
            Verdadeiro quando não há erro registrado e há ao menos um rótulo.
        """
        return not self.error and bool(self.labels)

    def label(self, nome: str) -> float:
        """Devolve a probabilidade de um rótulo, com zero como valor ausente.

        Args:
            nome: Nome do rótulo consultado.

        Returns:
            Probabilidade associada ao rótulo, ou 0.0 quando o rótulo não veio na resposta.
        """
        return float(self.labels.get(nome, 0.0))


class SemanticClassifier(Protocol):
    """Contrato mínimo de um classificador semântico."""

    name: str

    def classify_html(self, html: str) -> SemanticResult:
        """Classifica o enquadramento do conteúdo em HTML.

        Args:
            html: Marcação da página a classificar.

        Returns:
            `SemanticResult` do classificador, com erro registrado em caso de falha.
        """
        ...


def extract_visible_text(html: str, limit: int = MAX_INPUT_CHARS) -> str:
    """Reduz o HTML a texto visível legível, para não enviar marcação à API.

    Scripts, estilos, conteúdo alternativo e SVG são descartados antes da extração. Quando
    o analisador falha, o texto é obtido por colapso simples de espaços em branco, de modo
    que a classificação continue possível sem o parse completo.

    Args:
        html: Marcação da página.
        limit: Quantidade máxima de caracteres devolvida.

    Returns:
        Texto visível normalizado, truncado em `limit` caracteres; vazio quando não há HTML.
    """
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        sopa = BeautifulSoup(html, "html.parser")
        for tag in sopa(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(sopa.get_text(separator=" ").split())
    except Exception:
        text = " ".join(html.split())
    return text[:limit]


class NullClassifier:
    """Camada semântica desligada: comportamento determinístico puro."""

    name = "off"

    def classify_html(self, html: str) -> SemanticResult:
        """Devolve resultado neutro, sem consulta externa.

        Args:
            html: Marcação da página; não é analisada porque a camada está desligada.

        Returns:
            `SemanticResult` com o provedor `off` e nenhum rótulo.
        """
        return SemanticResult(provider=self.name)


class JevClassifier:
    """Cliente do JEV (TypeSafe AI) para classificação de enquadramento."""

    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str = TYPESAFE_ENDPOINT,
        timeout: int = 25,
        max_retries: int = 3,
    ):
        """Configura o cliente da camada semântica.

        Args:
            api_key: Chave da API; quando ausente, é buscada em `TYPESAFE_API_KEY` e, em
                seguida, em `JEV_API_KEY`, no ambiente ou no Keychain.
            model: Modelo solicitado; quando ausente, usa `DEFAULT_MODEL`.
            endpoint: URL do serviço de classificação.
            timeout: Tempo máximo, em segundos, de cada requisição.
            max_retries: Quantidade máxima de tentativas por classificação.
        """
        self.api_key = api_key or get_api_key("TYPESAFE_API_KEY") or get_api_key("JEV_API_KEY")
        self.model = model or DEFAULT_MODEL
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries

    def _payload(self, text: str) -> dict:
        """Monta o corpo da requisição de classificação.

        Cada rótulo vira uma pergunta `noul` própria porque os rótulos são independentes e
        podem valer ao mesmo tempo: um texto pode afirmar direitos e atacar adversários na
        mesma passagem, então a escolha única perderia informação. A intensidade é uma
        pergunta `score` separada, com os níveis em lista, formato que a API exige.

        Args:
            text: Texto visível já extraído da página.

        Returns:
            Dicionário com modelo, estado e perguntas no formato esperado pela API.
        """
        questions: dict[str, dict] = {
            nome: {"type": "noul", "instructions": instrucao}
            for nome, instrucao in LABEL_INSTRUCTIONS.items()
        }
        questions[INTENSITY_QUESTION] = {
            "type": "score",
            "instructions": "Qual a intensidade do enquadramento polarizador no texto.",
            "criteria": INTENSITY_LEVELS,
        }
        return {"model": self.model, "state": {"texto": text}, "questions": questions}

    def classify_html(self, html: str) -> SemanticResult:
        """Classifica o enquadramento do conteúdo e devolve a evidência produzida.

        O resultado é evidência para o avaliador determinístico, nunca substituto da nota:
        a camada semântica não sobrepõe a heurística, apenas informa a decisão registrada
        pelo motor. Erros definitivos de contrato ou credencial encerram a tentativa de
        imediato; erros transitórios são repetidos com espera crescente.

        Args:
            html: Marcação da página a classificar.

        Returns:
            `SemanticResult` com rótulos, intensidade, uso e latência, ou com a descrição
            da falha em `error`.
        """
        text = extract_visible_text(html)
        if not text:
            return SemanticResult(
                provider=self.name, model=self.model, error="sem texto visível para classificar"
            )
        if not self.api_key:
            return SemanticResult(
                provider=self.name,
                model=self.model,
                input_chars=len(text),
                error="TYPESAFE_API_KEY ausente (ambiente ou Keychain)",
            )

        payload = self._payload(text)
        inicio = time.perf_counter()
        last_error = "falha desconhecida"

        for tentativa in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self.endpoint,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                decorrido = int((time.perf_counter() - inicio) * 1000)

                if response.status_code >= 400:
                    last_error = f"typesafe HTTP {response.status_code}"
                    if response.status_code in (400, 401, 403, 404, 422):
                        return SemanticResult(
                            provider=self.name,
                            model=self.model,
                            input_chars=len(text),
                            latency_ms=decorrido,
                            error=last_error,
                        )
                    time.sleep(0.5 * (tentativa + 1))
                    continue

                body = response.json()
                answers = body.get("answers") or {}
                labels = {
                    nome: float(answers.get(nome, {}).get("noul") or 0.0) for nome in LABEL_INSTRUCTIONS
                }
                if not any(labels.values()):
                    last_error = "resposta sem rótulos noul"
                    time.sleep(0.5 * (tentativa + 1))
                    continue

                score = answers.get(INTENSITY_QUESTION) or {}
                return SemanticResult(
                    provider=self.name,
                    model=body.get("model", self.model),
                    labels=labels,
                    intensity=score.get("score"),
                    intensity_confidence=score.get("confidence"),
                    probabilities=score.get("probabilities", {}) or {},
                    input_chars=len(text),
                    latency_ms=decorrido,
                    usage=body.get("usage", {}) or {},
                )
            except Exception as exc:
                last_error = f"typesafe {type(exc).__name__}: {exc}"[:200]
                time.sleep(0.5 * (tentativa + 1))

        decorrido = int((time.perf_counter() - inicio) * 1000)
        return SemanticResult(
            provider=self.name,
            model=self.model,
            input_chars=len(text),
            latency_ms=decorrido,
            error=last_error,
        )


def build_classifier(name: str = "off") -> SemanticClassifier:
    """Instancia o classificador semântico pedido.

    Args:
        name: Nome do classificador; `off` e `none` desligam a camada, `jev` usa a API.

    Returns:
        Classificador correspondente ao nome informado.

    Raises:
        ValueError: Quando o nome não corresponde a nenhum classificador conhecido.
    """
    if name in ("off", "none"):
        return NullClassifier()
    if name == "jev":
        return JevClassifier()
    raise ValueError(f"classificador semântico desconhecido: {name}")
