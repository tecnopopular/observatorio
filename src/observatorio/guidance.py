"""
src/observatorio/guidance.py
Textos metodológicos das 5 dimensões do Protocolo de Avaliação Cívica TecnoPopular.

O texto de orientação é global: não varia por iniciativa, apenas por dimensão e por
uma única condição de indisponibilidade na dimensão de privacidade. Centralizá-lo aqui
evita reembutir o mesmo parágrafo em cada laudo e permite reconstruir a orientação a
partir do identificador da dimensão, sem depender do laudo anterior.
"""

from typing import Any

from .models import ActionableGuidance, CivicAuditReport, DimensionType

_DEFAULT: dict[DimensionType, dict[str, str]] = {
    DimensionType.PROVENIENCIA_DADOS: {
        "what_is_evaluated": "Verifica se os dados derivam de fontes primárias oficiais e se há links diretos para os órgãos de custódia.",
        "why_it_matters": "Inspirado na política editorial de checagem (Aos Fatos): o cidadão precisa conseguir 'refazer o caminho do dado' para confiar no resultado, blindando o projeto contra acusações de manipulação.",
        "how_to_fix": "Adicione no rodapé da sua aplicação ou na página de ajuda: 'Fonte primária: Dados Abertos do TSE (dadosabertos.tse.jus.br)'. Inclua o link direto com rel='noopener'.",
    },
    DimensionType.PRIVACIDADE_SOBERANIA: {
        "what_is_evaluated": "Varre o HTML em busca de scripts de Big Techs (Meta Pixel, Google Tag Manager) e formulários com dados identificáveis (CPF/e-mail).",
        "why_it_matters": "Consultar dados cívicos ou eleitorais não pode gerar rastreamento de perfil comercial nem expor eleitores a vazamentos ou perseguição.",
        "how_to_fix": "Exclua a tag `<script src='...googletagmanager...'>` ou o pixel do Facebook do seu HTML. Se quiser métricas de visitas, use Cloudflare Web Analytics (100% sem cookies e anônimo). Remova campos obrigatórios de cadastro.",
    },
    DimensionType.CODIGO_ABERTO_FLOSS: {
        "what_is_evaluated": "Verifica se há hiperlink para repositório de código aberto e presença de licença livre reconhecida pela OSI.",
        "why_it_matters": "A transparência de código garante que qualquer cidadão ou pesquisador possa auditar a lógica matemática e atestar que a ferramenta não distorce dados eleitorais.",
        "how_to_fix": "Suba o código no GitHub ou Hugging Face, crie um arquivo 'LICENSE' contendo a licença MIT ou GPL-3.0, e coloque no rodapé da página o link: 'Código aberto sob licença MIT'.",
    },
    DimensionType.ENQUADRAMENTO_REPUBLICANO: {
        "what_is_evaluated": "Analisa o texto da interface contra termos agressivos/polarizadores e presença de enquadramento material focado em bem público.",
        "why_it_matters": "A teoria cognitiva de George Lakoff demonstra que ataques ad hominem ativam o 'backfire effect' e fecham o diálogo, além de violar o Art. 19 do Marco Civil (risco de calúnia e censura jurídica).",
        "how_to_fix": "Troque acusações pessoais por dados objetivos. Em vez de 'denuncie o esquema de fulano', use 'compare o orçamento previsto com o executado no município'.",
    },
    DimensionType.ACESSIBILIDADE_DESEMPENHO: {
        "what_is_evaluated": "Mede latência de resposta, peso do payload transferido e ausência de banners publicitários ou paywalls.",
        "why_it_matters": "Ferramentas populares e cívicas devem operar com alta eficiência em celulares de entrada e redes móveis restritas nas periferias, sem consumir dados do cidadão nem aprisioná-lo com anúncios.",
        "how_to_fix": "Comprima imagens para WebP, minifique o CSS/JS e remova bibliotecas pesadas desnecessárias para manter a página inicial abaixo de 500 KB.",
    },
}

_UNAVAILABLE: dict[DimensionType, dict[str, str]] = {
    DimensionType.PRIVACIDADE_SOBERANIA: {
        "what_is_evaluated": _DEFAULT[DimensionType.PRIVACIDADE_SOBERANIA]["what_is_evaluated"],
        "why_it_matters": _DEFAULT[DimensionType.PRIVACIDADE_SOBERANIA]["why_it_matters"],
        "how_to_fix": "Garanta que o servidor responda a requisições com código 200 e sem bloqueios de IP/User-Agent para inspeção cívica.",
    },
}


def get_guidance(dimension: DimensionType, unavailable: bool = False) -> ActionableGuidance:
    """Devolve a orientação metodológica de uma dimensão.

    A orientação padrão por dimensão é texto fixo: nada de achados, notas ou nomes de
    terceiros é interpolado, porque a projeção compacta do laudo assume função pura.

    Quando a inspeção é inconclusiva (resposta vazia ou inacessível), a variante de
    indisponibilidade reaproveita "o que se avalia" e "por que importa" e troca apenas a
    instrução prática.

    A instância é nova a cada chamada: o modelo é mutável e não deve ser compartilhado
    entre laudos.

    Args:
        dimension: Dimensão do protocolo cuja orientação se deseja.
        unavailable: Quando verdadeiro, usa a variante de inspeção inconclusiva.

    Returns:
        Orientação acionável da dimensão, pronta para ser anexada ao laudo.
    """
    table = _UNAVAILABLE if unavailable else _DEFAULT
    texts = table.get(dimension) or _DEFAULT[dimension]
    return ActionableGuidance(dimension=dimension, **texts)


def known_texts() -> dict[str, dict[str, str]]:
    """Mapeia todas as combinações de dimensão e variante para verificação de integridade.

    Returns:
        Dicionário com chaves `dimensao:variante` para os textos de orientação conhecidos.
    """
    registry: dict[str, dict[str, str]] = {f"{d.value}:default": t for d, t in _DEFAULT.items()}
    registry.update({f"{d.value}:unavailable": t for d, t in _UNAVAILABLE.items()})
    return registry


def build_report_from_state(payload: dict[str, Any]) -> CivicAuditReport:
    """Reconstrói o laudo completo a partir da projeção compacta de estado.

    Reanexa a orientação metodológica a cada dimensão que não a trouxe. O sumário em
    Markdown não é restaurado: deve ser regenerado pelo motor, que é a única fonte
    legítima do texto de apresentação.

    Args:
        payload: Projeção compacta do laudo, já desserializada.

    Returns:
        Laudo completo, validado contra o modelo de domínio.
    """
    data = dict(payload)
    dimensions = data.get("dimension_scores") or {}
    for key, score in dimensions.items():
        if isinstance(score, dict) and not score.get("guidance"):
            score["guidance"] = get_guidance(DimensionType(key)).model_dump(mode="json")
    return CivicAuditReport.model_validate(data)
