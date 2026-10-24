# Contract: GitHub Actions CI/CD Workflow

Este documento estabelece o contrato do fluxo de trabalho automatizado em `.github/workflows/monitoramento.yml`.

---

## 1. Gatilhos de Disparo (*Triggers*)

```yaml
on:
  schedule:
    # Executa semanalmente aos domingos às 03:00 UTC
    - cron: '0 3 * * 0'
  workflow_dispatch:
    inputs:
      refresh:
        description: 'Forçar reavaliação ignorando cache de SHA-256'
        required: false
        default: false
        type: boolean
  pull_request:
    paths:
      - 'data/data_iniciativas.json'
      - 'src/observatorio/**'
```

---

## 2. Segredos e Variáveis de Ambiente Necessárias

| Nome | Obrigatório | Descrição |
| :--- | :---: | :--- |
| `FIRECRAWL_API_KEY` | Não | Chave do provedor de renderização remota. Só é usada quando a execução pede `--fetch-provider firecrawl`; o padrão (`http`) não a consome. |
| `TYPESAFE_API_KEY` | Não | Chave da camada semântica calibrada (TypeSafe). Só é usada com `--semantic jev`; o workflow padrão roda em `off` e não a injeta. |

Nenhum outro segredo é necessário. As chaves nunca são impressas em log, relatório ou arquivo de saída.

---

## 3. Portões e Etapas Obrigatórias

1. **Checkout e uv:** configuração de cache de pacotes com `astral-sh/setup-uv@v3`.
2. **Portão de Privacidade (Quality Gate 1):** `verify-privacy --all-files` antes de qualquer geração de dado. Violação bloqueia o job com código de saída 2.
3. **Portão Estático (Quality Gate 2):** `uv run ruff check .`, `uv run ruff format --check .`, `uv run python tools/verificar_codigo_morto.py` e `uv run python tools/verificar_docstrings.py`. Qualquer achado bloqueia o job.
4. **Portão de Testes (Quality Gate 3):** `uv run python -m unittest discover tests`, incluindo consistência de pontuação entre laudo e manifesto e a execução dos verificadores estáticos dentro da suíte.
5. **Execução do Pipeline:** invocação da CLI com os argumentos do disparo.
6. **Publicação do laudo:** o laudo completo (`dist/auditoria_iniciativas.json`) é anexado como artefato `auditoria-iniciativas`, com retenção de 90 dias. Ele não é versionado.
7. **Autocommit (apenas em `schedule` ou `workflow_dispatch`):** quando o manifesto muda, o workflow comita **somente** `data/estado_hashes.json`, com a mensagem `chore(estado): atualiza manifesto de hashes e scores do ciclo semanal [skip ci]`. Em `pull_request` não há commit.

## 4. Workflow de inclusão (`adicionar.yml`)

Disparo manual (`workflow_dispatch`), sem agendamento: a inclusão é ato editorial, não periódico.

| Entrada | Obrigatória | Papel |
| :--- | :--- | :--- |
| `url` | sim | Endereço público da aplicação |
| `nome` | sim | Nome público da iniciativa |
| `categoria` | sim | Um dos sete eixos, escolhido em lista |
| `descricao` | sim | O que a ferramenta faz |
| `repositorio` | não | URL pública do repositório git |
| `fontes_primarias` | não | Órgãos públicos citados, separados por vírgula |
| `tool_id` | não | Identificador explícito |
| `ignorar_robots` | não | Inclui mesmo com `robots.txt` proibitivo; padrão falso |

Etapas obrigatórias, na ordem: formatação, lint, docstrings, código morto, suíte de testes e portão
de privacidade — antes da inclusão. A gravação é um commit direto em `main` com o nome da iniciativa
no assunto, feito apenas se a execução de `adicionar` e todos os portões passarem.

`concurrency: catalogo` impede duas inclusões simultâneas de disputarem o mesmo arquivo. O workflow
de monitoramento roda em seguida, no push, e avalia a iniciativa nova.

## 5. Workflow de publicação (`release.yml`)

Disparo por tag `v*` ou manualmente, informando a tag existente.

Etapas obrigatórias, na ordem: os seis portões de qualidade, o ciclo completo de auditoria e o portão
de integridade do cache. Anexos da release:

| Arquivo | Papel |
| :--- | :--- |
| `auditoria_iniciativas.json` | laudo completo do ciclo |
| `estado_hashes.json` | manifesto com hash, status e notas por iniciativa |
| `cache-html.tar.gz` | corpos lidos, que permitem recalcular as notas offline |
| `SHA256SUMS` | integridade dos arquivos anexados |
| `PROCEDENCIA.txt` | tag, commit, data do ciclo, contagens por status e instruções de recálculo |

O laudo não entra no histórico do repositório; o anexo da release é o destino permanente e
endereçável dele. A publicação só ocorre se o cache reproduzir os hashes do manifesto — sem isso, o
laudo anexado não seria recalculável e a afirmação de integridade perderia o lastro.

Se a release da tag já existir, os arquivos são acrescentados a ela em vez de criar outra.
