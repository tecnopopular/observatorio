# Research: Pipeline Automatizado de Monitoramento e Avaliacao Civica

**Feature**: `001-pipeline-monitoramento`
**Date**: 2026-10-20
**Status**: Completed

Este documento consolida as pesquisas técnicas, decisões arquiteturais e trade-offs avaliados para a implementação da esteira automatizada de monitoramento e avaliação do Observatório TecnoPopular.

---

## 1. Decisão: Estratégia de Invalidação de Cache e Detecção de Mudanças

### Decisão
Utilizar o hash criptográfico **SHA-256 do corpo HTTP bruto (HTML limpo)** decodificado como chave canônica de conteúdo (`html_sha256`).
* O hash atual é comparado com o `html_sha256` registrado no laudo anterior (`dist/auditoria_iniciativas.json`).
* Se idêntico: a iniciativa é considerada inalterada (`status: inalterado`). Pula-se 100% da avaliação semântica e do acionamento arquivístico.
* Se divergente ou inédito: a iniciativa entra no pipeline de reavaliação completa e arquivamento assíncrono.

### Rationale
* **Desempenho no GitHub Actions:** Em um catálogo de 100+ iniciativas onde mais de 90% das ferramentas permanecem estáveis semanalmente, o cálculo de SHA-256 leva menos de 1 milissegundo por item em memória. Isso reduz o tempo do runner de ~20 minutos para < 2 minutos.
* **Cadeia de Custódia Auditável:** O hash funciona como prova matemática de não-repúdio. Qualquer terceiro pode atestar que o laudo emitido reflete o arquivo real capturado naquele dia e hora.

### Alternativas Consideradas
* **HTTP Headers `ETag` / `Last-Modified`:** Rejeitado porque a maioria dos servidores de hospedagem estática gratuitos (Vercel, Netlify, GitHub Pages) gera ETags dinâmicos a cada requisição ou omite o cabeçalho `Last-Modified`. O SHA-256 do corpo é estritamente determinístico.
* **Diff Semântico por Árvore DOM:** Rejeitado pelo alto custo computacional. Se uma vírgula ou tracker mudar, o hash já sinaliza a necessidade de reavaliação.

---

## 2. Decisão: Arquivamento e Preservação Histórica

### Decisão
Integrar a API **Save Page Now (SPN)** do **Internet Archive (Wayback Machine)** pela interface de
tarefas, com orçamento de espera por ciclo:
* Submissão: `POST https://web.archive.org/save` com `url=<endereço>` e `if_not_archived_within=30d`,
  que faz o serviço reaproveitar captura recente em vez de criar outra; a resposta autenticada é JSON
  com `job_id`, e a anônima é uma página que carrega o identificador `spn2-...`.
* Acompanhamento: `GET https://web.archive.org/save/status/<job_id>` devolve JSON com `status`
  (`pending`, `success`, `error`), `timestamp` e `original_url`.
* Endereço perene: montado como `https://web.archive.org/web/<timestamp>/<original_url>` a partir da
  consulta de estado.
* Orçamento: 60 s de espera para o ciclo inteiro (`--archive-budget`), e não por iniciativa; as
  capturas que não concluírem ficam com `archive_status=pending`.
* Envio serial com intervalo de 12 s e teto de 10 capturas por ciclo (`--archive-limit`), inclusive
  com autenticação: medido em 2026-10-24, uma rajada de 6 envios simultâneos teve todos os pedidos
  recusados com `Connection refused`, 4 envios com 8 s de intervalo terminaram em `HTTP 429` e 6
  envios com 12 s de intervalo foram aceitos de um IP residencial, mas o mesmo intervalo rendeu 12
  capturas em 20 tentativas a partir do IP compartilhado do runner, o que levou o intervalo a 20 s.
* Estado entre ciclos: o manifesto versionado guarda `archive_url` e `archive_status` por
  iniciativa, para não recapturar conteúdo idêntico e para a fila não reiniciar sempre pelos mesmos
  endereços.
* Resiliência: HTTP 429, erro de rede ou resposta ilegível não derrubam o pipeline; a situação fica
  gravada em `archive_status` (`refused`, `failed`, `pending`, `deferred`) com o motivo no resumo do
  ciclo.

### Rationale
* A interface síncrona (`GET /save/<url>`) foi descartada por medição em 2026-10-24: ela só responde
  quando a captura termina, e uma página com aplicação JavaScript não devolveu nada em 90 s, enquanto
  a interface de tarefas concluiu `https://tecnopopular.org/` em 14,21 s.
* O `urllib` segue o redirecionamento do modo síncrono, de modo que o endereço do snapshot nunca
  aparecia nos cabeçalhos da resposta final: ler `Content-Location` ali é um contrato que não existe.
* Atende ao Princípio IV da Constituição: preservação perene sem violar direitos autorais nem inchar o
  Git com HTMLs proprietários de terceiros.
* Permite que cidadãos e pesquisadores naveguem visualmente na aplicação original mesmo se o domínio
  for extinto pós-eleição.

### Alternativas Consideradas
* **Armazenar HTML bruto em Git LFS:** Rejeitado por inchaço de repositório e alto risco de persistência irreversível de dados pessoais privados (PII) no versionamento.
* **Armazenar em bucket privado (Cloudflare R2):** Viável como backup frio de laboratório no futuro, mas não oferece a fé pública internacional e a interface de navegação do Internet Archive.

---

## 3. Decisão: Estrutura de Execução da Esteira (Iteração por Iniciativa)

### Decisão
Adotar o modelo de **Iteração por Iniciativa** com *worker pool* concorrente (`ThreadPoolExecutor`, máx. 10 workers no GitHub Actions):
* Cada thread realiza: Conexão HTTP com timeout de 8s $\rightarrow$ Cálculo do SHA-256 $\rightarrow$ Comparação de cache $\rightarrow$ Se alterado: inspeção das 5 dimensões e disparo ao Archive $\rightarrow$ Emissão de `CivicAuditReport`.

### Rationale
* **Localidade de Rede:** Apenas 1 requisição HTTP por iniciativa (ou 0 se o cache local estiver ativo).
* **Isolamento de Falhas:** Erro de DNS, timeout ou bloqueio de robô em um site isolado não impacta nenhuma outra iniciativa.
* **Checkpoints:** Permite gravação atômica em disco a cada conclusão.

### Alternativas Consideradas
* **Iteração por Teste (ex.: rodar trackers em todos, depois FLOSS em todos):** Rejeitado porque forçaria re-downloads ou exigiria manter centenas de megabytes de HTML simultaneamente na memória do runner.

---

## 4. Decisão: Gestão Segura de Credenciais e Portão de Privacidade

### Decisão
* O observatório herda a precedência estabelecida no projeto: `os.getenv` prioritário sobre o macOS Keychain.
* O portão `CivicPrivacyGuard` valida em pré-commit e CI:
  * Ausência de chaves de API / tokens.
  * Ausência de dados pessoais identificáveis (CPFs, telefones, números de WhatsApp, e-mails de pessoas físicas e chaves PIX).
  * URLs públicas de ferramentas e nomes institucionais são explicitamente permitidos.

## 5. Decisão: declaração de fonte só pontua com corroboração

### Decisão

O campo `fontes_primarias` do catálogo deixa de somar 25 pontos por si. A declaração passa a pontuar
apenas quando a própria página traz um link de custódia pública que a confirme. A confirmação é
registrada em `corroborated_sources`, ao lado de `declared_sources` e `official_links_detected`.

### Rationale

Medição sobre o acervo: das 23 iniciativas com fonte declarada, apenas 3 têm link que confirme a
declaração. As outras 20 somavam 25 pontos por afirmação, sem qualquer elemento reconferível no
documento publicado — o oposto do que o laudo promete, que é toda nota rastreável à evidência.

A alternativa de remover a declaração da nota por completo puniria a iniciativa que cita fonte em
texto ou PDF, sem link. A corroboração preserva esse caso quando o link existe e reduz o ponto a zero
quando não existe, mantendo o registro da declaração para leitura humana.

### Alternativas Consideradas

- **Manter como estava:** ponto por afirmação, inflação não auditável.
- **Remover a declaração da nota:** elimina a inflação, mas apaga o único caminho para registrar fonte
  que a página não linka.
- **Exigir URL estruturada no catálogo sem verificar a página:** transfere o problema para o formato do
  dado, sem criar evidência.

### Consequências

- `protocol_version` sobe para 2 e os laudos para `CTP-1.1`: a série anterior não é comparável.
- 20 iniciativas perdem 25 pontos na proveniência e caem abaixo do piso de 60 — o que reflete a
  ausência de fonte linkada na página, não um erro do motor.
- A regra de confirmação é heurística e pode errar nas duas direções; por isso as três listas ficam no
  laudo, permitindo conferir cada confirmação.
