# Contract: CLI de Monitoramento e Avaliacao

Este documento estabelece o contrato de interface da linha de comando (CLI) do Observatório TecnoPopular.

---

## 1. Ponto de Entrada Canônico

Toda execução é disparada a partir da raiz do repositório via `uv`:

```bash
uv run python -m observatorio.cli [COMANDO] [OPÇÕES]
```

---

## 2. Comandos Disponíveis

### `audit-all` (ou padrão)
Executa a varredura completa das iniciativas catalogadas.

* **Argumentos e Opções:**
  * `--input`, `-i` *(string, default: `data/data_iniciativas.json`)*: Caminho do arquivo de iniciativas base.
  * `--output`, `-o` *(string, default: `dist/auditoria_iniciativas.json`)*: Caminho do laudo completo (artefato de publicação, não versionado).
  * `--manifest`, `-m` *(string, default: `data/estado_hashes.json`)*: Caminho do manifesto compacto de estado (versionado no git).
  * `--refresh`, `-r` *(flag, default: false)*: Força re-inspeção completa, ignorando o cache local de hashes.
  * `--workers`, `-w` *(int, default: 10)*: Número de threads simultâneas para download e inspeção.
  * `--skip-archive` *(flag, default: false)*: Pula as chamadas assíncronas ao Internet Archive (útil em testes locais ou CI offline).
  * `--semantic` *(escolha, default: `off`)*: Camada semântica calibrada da Dimensão 4. `off` mantém a avaliação 100% determinística; `jev` consulta a API TypeSafe (`noul` por rótulo + `score` de intensidade) e consome tokens por iniciativa.
  * `--timeout`, `-t` *(int, default: 8)*: Tempo limite por requisição, em segundos.
  * `--max-bytes` *(int, default: `1000000`)*: Limite de leitura por página. Documentos maiores são inspecionados parcialmente e o laudo registra `inspection_truncated=true`, com `coverage.complete=false` e ressalva nas dimensões afetadas.

* **Registro de retiradas:** o `audit-all` lê `data/retiradas.json` (ajustável em `--retiradas`) e não audita endereço ali presente, mesmo que ainda conste do catálogo; nesse caso imprime `[RET ]` e avisa para remover a entrada.

* **Consulta de `robots.txt`:** antes de ler a página, o comando consulta o `robots.txt` do host, uma vez por ciclo. Quando as regras proíbem a leitura para o User-Agent institucional, a iniciativa não recebe laudo: ela permanece no catálogo e o resumo do ciclo informa quantas foram puladas. Resposta 404, 410, 401, 403 ou falha de rede não são tratadas como proibição.

* **Saída Padrão (stdout):**
  * Linhas estruturadas sem emojis informando o status de cada iniciativa:
    ```text
    [SELO] baralho-da-virada        | Score: 88.0 | AUDITADO          | Certificado TecnoPopular
    [DIR ] mapa-do-voto             | Score: 72.5 | CATALOGADO        | Catalogado no Diretório Aberto
    [S/LEI] dafem-observatorio      | Score:  n/d | NAO_INSPECIONADO  | Não Inspecionado (sem leitura útil)
    [BLOQ] ferramenta-com-tracker   | Score: 45.0 | REJEITADO         | Bloqueador Crítico de Privacidade
    [PULA] site-que-proibe-leitura  |            | robots.txt        | robots.txt proíbe este endereço para todos os agentes
    ```

  A nota aparece como `n/d` quando não houve leitura útil: nesse caso o laudo não atribui nota.

* **Códigos de Saída (Exit Codes):**
  * `0`: Sucesso total na execução e validação.
  * `1`: Erro fatal de configuração ou dados corrompidos.
  * `2`: Falha de bloqueio por violação do portão de privacidade (PII ou segredos detectados).

---

### `adicionar`

Inclui uma iniciativa no catálogo com identificador congelado.

```bash
uv run python -m observatorio.cli adicionar \
  --url https://exemplo.org \
  --nome "Iniciativa Exemplo" \
  --categoria "Dados e Mapas" \
  --descricao "Painel que reúne dados de votação por seção eleitoral."
```

| Flag | Padrão | Papel |
| :--- | :--- | :--- |
| `--url` | obrigatório | Endereço público da aplicação |
| `--nome` | obrigatório | Nome público da iniciativa |
| `--categoria` | obrigatório | Um dos sete eixos do catálogo; o parser recusa valor fora da lista |
| `--descricao` | obrigatório | O que a ferramenta faz; mínimo de 20 caracteres |
| `--repositorio` | vazio | URL pública do repositório git |
| `--fontes-primarias` | vazio | Órgãos públicos citados, separados por vírgula |
| `--tool-id` | slug do nome | Identificador explícito; precisa ser slug livre |
| `--ignorar-robots` | falso | Inclui mesmo quando o host proíbe a leitura |
| `--catalogo` | `data/data_iniciativas.json` | Catálogo a atualizar |
| `--registro` | `data/retiradas.json` | Registro de retiradas consultado |

A entrada gravada contém `nome`, `tool_id`, `url`, `dominio`, `categoria`, `descricao` e
`adicionado_em`, além dos campos opcionais informados. Fragmento na URL é removido antes de gravar:
`https://exemplo.org/#painel` e `https://exemplo.org` são a mesma página, e o servidor nunca recebe
o fragmento. O `tool_id` não é recalculado em ciclos
posteriores; ver §2.1 do modelo de dados.

Códigos de saída: `0` iniciativa incluída; `1` endereço já no catálogo, endereço retirado, host
proibido por `robots.txt`, data ou entrada inválida, ou texto com dado pessoal.

### `retirar`

Registra uma retirada a pedido e remove a iniciativa do catálogo.

```bash
uv run python -m observatorio.cli retirar \
  --url https://exemplo.org \
  --motivo "Pedido do coletivo, por e-mail." \
  --solicitado-em 2026-10-24 \
  --canal email
```

| Flag | Padrão | Papel |
| :--- | :--- | :--- |
| `--url` | obrigatório | Endereço da iniciativa, comparado de forma normalizada (esquema, `www`, barra final e caixa do caminho não distinguem) |
| `--motivo` | obrigatório | Motivo informado pelo solicitante, gravado sem edição |
| `--solicitado-em` | hoje | Data do pedido, `AAAA-MM-DD` |
| `--canal` | `email` | Origem do pedido |
| `--catalogo` | `data/data_iniciativas.json` | Catálogo a atualizar |
| `--registro` | `data/retiradas.json` | Registro de eventos a atualizar |

Ordem de execução: valida o endereço no registro, valida a presença no catálogo, valida a data,
inspeciona o motivo no portão de privacidade, grava o registro e só então remove a entrada do
catálogo.

A retirada guarda no evento a entrada do catálogo como estava, para que a readmissão possa devolvê-la
sem exigir que o operador reconstrua categoria e descrição.

Códigos de saída: `0` retirada registrada; `1` endereço já registrado, ausente do catálogo, data
inválida ou motivo com dado pessoal de pessoa física.

### `readmitir`

Registra a volta de uma iniciativa retirada e a devolve ao catálogo.

```bash
uv run python -m observatorio.cli readmitir \
  --url https://exemplo.org \
  --motivo "O coletivo pediu a volta da ferramenta." \
  --solicitado-em 2026-10-25
```

| Flag | Padrão | Papel |
| :--- | :--- | :--- |
| `--url` | obrigatório | Endereço retirado que volta |
| `--motivo` | obrigatório | Motivo do pedido de volta, gravado sem edição |
| `--solicitado-em` | hoje | Data do pedido de volta, `AAAA-MM-DD` |
| `--canal` | `email` | Origem do pedido |
| `--catalogo` | `data/data_iniciativas.json` | Catálogo a atualizar |
| `--registro` | `data/retiradas.json` | Registro de eventos a atualizar |

A readmissão é um evento novo: o pedido de saída e seu motivo permanecem no histórico. A entrada
devolvida ao catálogo é a cópia guardada na retirada e é acrescentada ao final da lista.

Códigos de saída: `0` readmissão registrada; `1` endereço não retirado, já presente no catálogo,
retirada sem entrada guardada, data inválida ou motivo com dado pessoal.

### `verify-privacy`
Executa o portão preventivo de privacidade (`CivicPrivacyGuard`) em arquivos alterados.

```bash
uv run python -m observatorio.cli verify-privacy [--staged | --all-files]
```
