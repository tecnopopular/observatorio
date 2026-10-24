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
Integrar a API **Save Page Now (SPN)** do **Internet Archive (Wayback Machine)** via chamadas assíncronas com timeout controlado:
* Endpoint canônico: `https://web.archive.org/save/{target_url}`
* Disparo restrito: Apenas quando `html_sha256` for diferente do anterior ou para ferramentas sem captura prévia.
* Formato do identificador temporal: O Internet Archive responde com redirecionamento contendo o timestamp ISO de 14 dígitos (`https://web.archive.org/web/YYYYMMDDhhmmss/{target_url}`).
* Resiliência: Se o Internet Archive responder com HTTP 429 (rate limit) ou timeout, o pipeline **não falha**; o laudo é emitido com o `html_sha256` local e a tentativa de preservação é reagendada para a rodada seguinte.

### Rationale
* Atende ao Princípio IV da Constituição: preservação perene sem violar direitos autorais nem inchar o Git com HTMLs proprietários de terceiros.
* Permite que cidadãos e pesquisadores naveguem visualmente na aplicação original mesmo se o domínio for extinto pós-eleição.

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
