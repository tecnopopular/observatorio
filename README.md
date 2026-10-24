# Observatório TecnoPopular

Repositório de monitoramento contínuo, preservação arquivística e diagnóstico técnico do ecossistema de tecnologias e iniciativas cívicas no Brasil.

Este repositório publica o motor e os dados que sustentam um resultado de pesquisa. Para executar o ciclo, conferir os portões e comparar com os números de referência, ver [`REPRODUZIR.md`](REPRODUZIR.md), que também declara o que o repositório **não** consegue reproduzir. O uso de inteligência artificial generativa na produção deste repositório está declarado em [`DECLARACAO_IA.md`](DECLARACAO_IA.md), conforme a Portaria CNPq nº 2.664/2026. As regras de idioma e de nomenclatura — código em inglês, texto em português, contrato de dados congelado — estão em [`CONVENCOES.md`](CONVENCOES.md).

---

## 1. Missão e Escopo

O **Observatório TecnoPopular** atua como uma infraestrutura pública de acompanhamento sistemático de iniciativas digitais de interesse público, desenvolvidas pela sociedade civil, movimentos populares e coletivos cívicos.

O observatório opera em três eixos principais:

1. **Monitoramento e Telemetria Contínua:** Rastreamento periódico de disponibilidade, conformidade de licenças, presença de rastreadores invasivos de terceiros e métricas de desempenho.
2. **Preservação Histórica e Não-Repúdio:** Registro de integridade matemática (hashes SHA-256) e disparo de capturas perenes junto ao Internet Archive (Wayback Machine) para combater a perda de dados e o desaparecimento de ferramentas digitais pós-eleições.
3. **Catálogo Cívico Interoperável:** Disponibilização de metadados abertos, dados estruturados (Schema.org / JSON-LD) e diagnósticos técnicos para jornalistas, pesquisadores e desenvolvedores.

---

## 2. Arquitetura do ciclo

O motor é uma esteira de sete etapas: o catálogo é a entrada, cada etapa pode recusar a iniciativa
ou registrar uma ressalva, e a saída são dois arquivos com papéis distintos. Nenhuma etapa escreve
no laudo o que não conseguiu observar — ausência de leitura vira `nao_inspecionado`, não nota baixa.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. CATÁLOGO   data/data_iniciativas.json — 174 endereços curados             │
│               engine.assign_tool_ids -> tool_id único (nome-repetido, -2)    │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. POLÍTICA   robots.RobotsPolicy — uma consulta por host, por ciclo         │
│               proibido -> [PULA]: fica no catálogo, não recebe laudo         │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. AQUISIÇÃO  fetch.HttpFetcher — HTTP direto, sem serviço externo           │
│               TLS verificado, User-Agent institucional, cap 4.000.000 B      │
│               gate de login -> nao_inspecionado                              │
│               cache data/raw/html_cache por tool_id; SHA igual = reaproveita │
│               preservação: Wayback archive_url (falha externa não aborta)    │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. EXTRAÇÃO   extract.parse_page -> PageEvidence                             │
│               texto até 200.000 chars, scripts, formulários, links, meta     │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 5. AVALIAÇÃO  evaluator.CivicEvaluator — 5 dimensões sobre a evidência       │
│               proveniência 25 · privacidade 25 · FLOSS 20 ·                  │
│               enquadramento 15 · desempenho 15                               │
│               semantic (TypeSafe) ajusta só com confiança >= 0,90            │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6. RESULTADO  >= 80 auditado (selo) · >= 50 catalogado ·                     │
│               < 50 rejeitado · sem leitura útil -> nao_inspecionado          │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7. SAÍDAS     dist/auditoria_iniciativas.json                                │
│                 laudo completo, não versionado, anexado à release da tag     │
│               data/estado_hashes.json                                        │
│                 manifesto de estado, versionado no repositório               │
└──────────────────────────────────────────────────────────────────────────────┘
```

Cada etapa é um módulo com teste próprio: `engine.py` (orquestração), `robots.py` (política),
`fetch.py` (aquisição), `extract.py` (evidência), `evaluator.py` (rubrica), `semantic.py` (camada
calibrada), `state.py` (manifesto) e `preservation.py` (captura perene). O avaliador não faz I/O de
rede: recebe o HTML adquirido e devolve o laudo, o que permite reavaliar um recorte sem tocar nos
sites.

---

## 3. Princípios Metodológicos

* **Sobriedade Institucional:** Análises baseadas em dados empíricos observáveis e verificáveis, sem emissão de juízos inquisitoriais ou terminologias hiperbólicas.
* **Transparência de Grafo:** Conectividade direta às iniciativas mapeadas, sem barreiras ou intermediações proprietárias.
* **Privacidade por Desenho (LGPD):** Bloqueio estrito de ingestão ou persistência de dados pessoais (PII) no versionamento público.

---

## 4. Licenciamento

O observatório separa a licença do **método** da licença dos **resultados**:

| Artefato | Licença | Arquivo |
| :--- | :--- | :--- |
| Código-fonte (`src/`, `tests/`, workflows) | **AGPL-3.0-or-later** | [LICENSE](LICENSE) |
| Dados e laudos (`data/`, laudo publicado em `dist/`) | **CC-BY-4.0** | [data/LICENSE](data/LICENSE) |

O laudo completo não é versionado: sai em `dist/auditoria_iniciativas.json`, é publicado como artefato do ciclo e entregue ao site. O que fica no histórico é o manifesto compacto `data/estado_hashes.json`.

**Por que AGPL e não uma licença permissiva:** o valor do observatório não está no código, e sim na autoridade de emitir diagnóstico técnico sobre iniciativas cívicas. Uma licença permissiva permitiria que um fork fechado fosse operado como serviço de certificação, sem auditabilidade pública da lógica de pontuação — exatamente o oposto do princípio de auditabilidade do método. A AGPL-3.0 preserva a liberdade de uso, estudo, modificação e execução local, e exige publicar o código apenas de quem distribui ou oferece o software modificado como serviço em rede.

**Por que os dados são CC-BY-4.0:** jornalistas, pesquisadores e gestores públicos devem poder reutilizar laudos e catálogos sem fricção de copyleft. A atribuição é a única exigência.

**Marca e selo:** a licença do código não concede uso do nome TecnoPopular, do selo "Certificado TecnoPopular" nem do nome do protocolo. Ver [NOTICE](NOTICE).

Detalhes de atribuição dos dados em [data/README.md](data/README.md).

---

## 5. Aquisição de conteúdo e integrações de API

O motor separa a **aquisição** do conteúdo da **avaliação** dele. A avaliação é 100% determinística
(heurísticas sobre o HTML); a aquisição é uma requisição HTTP direta, sem serviço intermediário e sem
credencial.

Garantias da camada de aquisição:

- **TLS verificado por padrão.** Requisição sem validação é possível, mas explícita e registrada em `FetchResult.tls_verified=False` — o laudo nunca declara ter inspecionado uma cadeia de certificados que não foi verificada.
- **User-Agent institucional.** Cada requisição se apresenta como robô de auditoria cívica, não como navegador: `TecnoPopular-Observatorio/1.0 (+https://tecnopopular.org/metodologia; contato@tecnopopular.org)`. O endereço de contato existe para que o operador do site saiba quem está lendo a página e possa pedir correção ou bloqueio. A mesma identificação é usada na leitura da iniciativa e na solicitação de captura ao Internet Archive.
- **Falha isolada.** Iniciativa fora do ar devolve resultado com erro e não interrompe a esteira das demais.
- **Nenhuma credencial em jogo.** A leitura do acervo não usa chave; a única chave do projeto é
da camada semântica opcional, e ela nunca aparece em mensagem de erro.

O laudo registra o provedor efetivo e o status HTTP em `performance.fetch_provider` e `performance.http_status`.

### Aquisição por HTTP direto

A aquisição é uma requisição HTTP direta por endereço, com TLS verificado e User-Agent
institucional, uma vez por ciclo — o cache por `tool_id` só relê a página quando o `html_sha256` muda.
Não há provedor alternativo, nem por parâmetro: é o único caminho de leitura do acervo.

A renderização remota por Firecrawl foi avaliada e retirada. O motivo é medido, em 2026-10-24, sobre
o mesmo acervo:

| Medida | HTTP direto | Firecrawl |
| :--- | :--- | :--- |
| Endereços com texto extraível (amostra de 30) | 29 | 16 |
| Falhas de aquisição | 1 | 14, todas HTTP 429 |
| Vazão aceita pelo serviço | sem limite do provedor | 6 requisições por minuto, `Retry-After` de 14 a 19 s |
| Nota final | — | mediana -1,5 ponto, entre -19,6 e +7,7 |

Três razões sustentam a retirada. A primeira é operacional: a 6 requisições por minuto, o acervo
levaria cerca de meia hora por ciclo, e paralelizar não ajuda — doze requisições simultâneas recebem
doze recusas. A segunda é metodológica: as duas vias não medem a mesma coisa, já que a nota mudou em
14 de 15 casos comparáveis, de modo que misturar as duas no mesmo acervo quebraria a série temporal.
A terceira é de escopo: o que o Firecrawl acrescentaria — enxergar conteúdo injetado por JavaScript —
não é o que o observatório mede hoje, e o preço seria tornar cada laudo dependente de serviço
externo pago.

O caso de uso que restaria é a leitura de páginas que só existem depois da execução de JavaScript.
Para isso, a via adequada é um diagnóstico pontual, fora do ciclo: o histórico de laudos fica com uma
única origem de dados, e a comparação entre ciclos continua válida.

### Consulta de `robots.txt`

Antes de ler uma página, o observatório consulta o `robots.txt` do host, uma vez por ciclo, e
aplica as regras ao seu próprio User-Agent. Se o site proíbe a leitura, **a iniciativa não recebe
laudo**: ela permanece no catálogo, sem diagnóstico, e o ciclo informa quantas foram puladas.

A decisão de consultar foi tomada depois de medir o efeito sobre o acervo
(`tools/measure_robots.py`, 170 hosts, 175 iniciativas no momento da medição):

| Situação | Iniciativas |
| :--- | ---: |
| Leitura permitida pelas regras | 86 |
| Sem `robots.txt` utilizável | 83 |
| Proibida por regra explícita | 3 |
| Inacessível na consulta | 3 |

Apenas 3 sites publicam bloqueio total — `Disallow: /` para todos os agentes. As três saíram do
laudo no primeiro ciclo com a regra ativa; uma delas era certificada e perdeu o selo.

Falha de rede não é proibição. Resposta 404, 410, 401 ou 403 ao `robots.txt` significa que não há
regra aplicável, e timeout, DNS ou 5xx não expressam vontade do operador: nesses casos a leitura
segue, porque tratar instabilidade como proibição faria um host sumir do acervo sem que ninguém
tivesse dito nada.

### Incluir nova iniciativa

O catálogo cresce por um subcomando, que valida a entrada antes de gravá-la:

```bash
uv run python -m observatorio.cli adicionar \
  --url https://exemplo.org \
  --nome "Iniciativa Exemplo"
```

`--categoria` e `--descricao` são opcionais e, quando informados, gravados como estão: são texto
editorial, responsabilidade do catálogo publicado, e **nenhuma dimensão da nota os lê**. O motor não
valida o eixo temático contra lista fechada — um eixo novo criado no site não derruba o ciclo aqui.

A inclusão grava `tool_id` e `adicionado_em` na entrada. O `tool_id` é o endereço público do laudo
(`auditoria/<tool_id>.html`) e **não muda depois**: ele nasce do slug do nome na inclusão e fica
gravado, para que corrigir o nome de uma iniciativa não publique o laudo seguinte em outro endereço.
`--tool-id` permite fixar um identificador próprio quando o slug do nome não serve.

Três verificações antecedem a gravação:

| Verificação | Motivo |
| :--- | :--- |
| Endereço já no catálogo, comparado sem esquema, `www`, barra final e caixa do caminho | duas entradas do mesmo site gerariam dois laudos idênticos para a mesma página |
| Endereço já no registro de retiradas | a volta de uma iniciativa retirada é o subcomando `readmitir`, que preserva o histórico |
| `robots.txt` do host | incluir um endereço proibido cria uma entrada que nunca recebe laudo |

Nome e, quando informada, descrição passam pelo portão de privacidade: são publicados no catálogo.

Em produção, o mesmo comando roda por workflow, sem depender de acesso local: em **Actions →
Incluir iniciativa no catálogo → Run workflow**, informe URL, nome, categoria e descrição. O
workflow executa os portões de qualidade e privacidade, inclui a entrada e grava o commit em `main`;
o ciclo de auditoria seguinte avalia a iniciativa nova.

### Contato e retirada do catálogo

A identificação no User-Agent existe para que o operador do site saiba quem leu a página e possa
responder. Pedido explícito de retirada, enviado ao endereço de contato, é acatado: o endereço sai do
catálogo e o pedido fica registrado em `data/retiradas.json`, com motivo, data e origem, como um
histórico de eventos em ordem — nunca um estado que sobrescreve o anterior.

```bash
uv run python -m observatorio.cli retirar \
  --url https://exemplo.org \
  --motivo "Pedido do coletivo, por e-mail, em nome do responsável pelo site." \
  --solicitado-em 2026-10-24
```

O comando grava o registro antes de tocar no catálogo, e essa ordem é deliberada. Se a gravação
falhar, nada muda. Se a remoção do catálogo falhar depois, já existe registro de que a iniciativa não
deve ser auditada, e o motor a ignora de todo modo: a esteira relê o registro a cada ciclo e imprime
`[RET ]` quando um endereço retirado ainda consta do catálogo, com aviso para remover a entrada.

O motivo entra no registro como foi informado, sem edição e sem exigência de justificativa técnica.
Ele passa pelo portão de privacidade antes de ser gravado, porque o arquivo é versionado e público:
motivo com nome, e-mail, telefone ou documento de pessoa física é recusado, e o comando orienta a
registrar a substância do pedido sem identificar quem pediu.

Se o site pedir para voltar, o evento é registrado sem apagar o pedido de saída:

```bash
uv run python -m observatorio.cli readmitir \
  --url https://exemplo.org \
  --motivo "O coletivo pediu a volta da ferramenta." \
  --solicitado-em 2026-10-25
```

A readmissão devolve ao catálogo a entrada guardada no momento da retirada — nome, domínio,
categoria e descrição, como estavam —, e a iniciativa volta a ser auditada no ciclo seguinte. O
registro fica com os dois eventos em ordem: a razão da saída e a razão da volta, ambas públicas.
Retirar de novo depois acrescenta o terceiro evento, e o estado vigente é sempre o último.

Uma iniciativa retirada deixa de receber laudo. O laudo completo do ciclo seguinte não a cita, e o
manifesto de estado perde a entrada — mas o ciclo anterior continua no histórico do git, e nenhum
hash já carimbado é recalculado ou apagado. O registro da retirada é o que permanece visível: quem
consultar o repositório vê que havia uma iniciativa ali, quando saiu e por quê.

### Escopos de evidência e inspeção parcial

Cada detector recebe apenas a visão da página em que sua evidência faz sentido, extraída por um
parser único (`extract.parse_page`):

| Escopo | Quem consome |
| :--- | :--- |
| `visible_text` | enquadramento (Dimensão 4) e declaração de licença |
| `script_srcs` + `inline_scripts` + `resource_urls` | telemetria comercial (Dimensão 2) e banners (Dimensão 5) |
| `links` (host resolvido) | proveniência oficial (Dimensão 1) e repositório (Dimensão 3) |
| `input_names` | coleta de PII em formulário (Dimensão 2) |
| `comments` | apenas diagnóstico — nunca evidência |

A regra que isso codifica: **menção não é instalação**. Um `TODO` em comentário não instala Google
Analytics; um artigo sobre redes de anúncio não exibe banners; um perfil `github.com/usuario` não é
repositório de código.

**Leitura limitada.** A aquisição lê até um limite configurável por página (`--max-bytes`, padrão
4 MB; medido no catálogo: p50 38 KB, p90 448 KB, 3% acima de 3 MB). Quando o documento excede o
limite, o laudo registra `performance.inspection_truncated=true`, `bytes_scanned`,
`payload_bytes_declared` e `inspection_limit_bytes`, e emite ressalva na Dimensão 2 — uma avaliação de
privacidade "limpa" com parte do documento não lida é explicitamente relativizado, não silenciado.
O cache local guarda um sidecar `.meta.json` com o tamanho declarado e a marca de truncamento, para
que a leitura parcial não se converta em silêncio numa leitura completa no ciclo seguinte.

**Metadado de cobertura.** Todo laudo carrega um bloco `coverage` com o que foi efetivamente
inspecionado:

| Campo | Significado |
| :--- | :--- |
| `bytes_scanned` / `bytes_declared` | lido / declarado pelo servidor |
| `byte_coverage` | razão lida (`null` quando o tamanho total é desconhecido) |
| `text_chars_scanned` / `text_chars_total` | texto visível analisado / existente |
| `text_truncated` | o escopo de texto foi cortado |
| `inline_scripts_truncated` | scripts inline foram analisados parcialmente |
| `inspection_truncated` | a leitura de bytes foi interrompida |
| `complete` | verdadeiro só quando nenhum dos cortes atuou |

A cobertura é **por escopo**, e a ressalva aparece apenas nas dimensões que leem o escopo cortado:
corte de bytes afeta proveniência, privacidade, licença e desempenho; corte de texto afeta
enquadramento e declaração de licença; corte de scripts inline afeta privacidade e desempenho.
Uma página pode ter `byte_coverage = 1.0` e ainda assim ser `complete = false` — foi o caso real de
uma iniciativa com 2,99 MB lidos por inteiro e mais de 200 mil caracteres de script inline, com
detecção de telemetria limitada ao trecho analisado. O manifesto de estado guarda
`coverage_complete` por iniciativa.

### Camada semântica calibrada (JEV / TypeSafe AI)

A Dimensão 4 combina duas camadas: termos determinísticos (sempre) e classificação semântica
(opcional, `--semantic jev`).

```bash
uv run python -m observatorio.cli audit-all --semantic jev
```

| Aspecto | Comportamento |
| :--- | :--- |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Credencial | `TYPESAFE_API_KEY` (ambiente ou Keychain; `JEV_API_KEY` aceito como compatibilidade) |
| Modelo | `jev-latest` (a resposta reporta o modelo resolvido, ex.: `jev-1.13.0`) |
| Primitiva | `noul` por rótulo (`propositivo`, `polarizador`) + `score` de intensidade |
| Custo | tokens por iniciativa (`usage` é registrado no resultado) |

Por que `noul` e não escolha única: um texto pode afirmar direitos e atacar adversários ao mesmo
tempo. Rótulos independentes preservam essa informação; uma escolha única forçaria o modelo a
descartar metade da evidência.

Limite de responsabilidade: a camada semântica **só altera a nota** quando a probabilidade de
enquadramento polarizador atinge `0.90` e a inspeção determinística não encontrou termos de ataque
(nunca há penalidade dupla). Abaixo do limiar, ela apenas registra evidência. O laudo guarda
`semantic_propositivo`, `semantic_polarizador`, `semantic_intensity`, `semantic_usage`,
`semantic_adjustment_applied` e `semantic_error` em `framing`, de modo que toda decisão — e todo
consumo de tokens — é rastreável por iniciativa.

Com `--semantic off` (padrão), o motor é 100% determinístico e nenhuma chamada externa é feita.

## Ferramentas de análise

| Ferramenta | Para quê |
| :--- | :--- |
| `uv run python tools/verify_cache.py` | Confere se o cache de HTML reproduz os hashes do manifesto — é o que sustenta recalcular as notas offline |
| `uv run python tools/compare_cycles.py` | Compara o laudo publicado (git `HEAD`) com o ciclo atual em `dist/`: mudanças de status, selos perdidos/ganhos e laudos sem leitura |
| `uv run python tools/reevaluate_subset.py --status rejeitado` | Reaudita um recorte do catálogo e mostra quais status mudaram |

As duas leem apenas artefatos reais e falham com mensagem explícita quando o laudo não existe —
nenhuma inventa um ciclo a partir de dados em `/tmp`.

## Verificações de qualidade

Quatro portões rodam em `uv run python -m unittest discover tests` e no CI, antes do ciclo:

| Verificação | Comando | O que impede |
| :--- | :--- | :--- |
| Lint | `uv run ruff check .` | importação órfã, nome indefinido, variável não usada, erro de exceção sem `from` |
| Formatação | `uv run ruff format --check .` | divergência de estilo entre arquivos |
| Código morto | `uv run python tools/verify_dead_code.py` | função, método, classe, importação ou constante sem consumidor |
| Docstrings | `uv run python tools/verify_docstrings.py` | função sem docstring Google e comentário `#` residual |

`ruff` e `vulture` são dependências do grupo `dev` e entram com `uv sync`.

O verificador de código morto filtra a saída do `vulture`: reporta funções, métodos, classes,
importações, propriedades e constantes em CAIXA ALTA, e ignora atributos e variáveis em minúsculas.
O motivo é concreto: campos de modelo Pydantic existem para o serializador, que os consome por nome,
e sinalizá-los produziria mais de vinte falsos positivos por arquivo. Um portão que grita sempre é um
portão que ninguém lê.

### Estados do laudo

| Estado | Significado |
| :--- | :--- |
| `auditado` | nota ≥ 80 e nenhum bloqueador crítico — Selo Certificado TecnoPopular |
| `catalogado` | nota ≥ 50 e < 80 — Diretório Aberto |
| `em_revisao` | nota < 50 |
| `rejeitado` | bloqueador crítico de privacidade |
| `nao_inspecionado` | **não houve leitura útil** (site fora do ar, 404/5xx, timeout ou redirecionamento para autenticação) |

`nao_inspecionado` não é um degrau da escala: é a ausência de avaliação. Página que não pôde ser lida
não recebe nota de qualidade, porque a nota calculada sobre conteúdo vazio mede o motor, não a
iniciativa. O motivo fica em `coverage.reason`.

