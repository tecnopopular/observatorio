# Feature Specification: Pipeline Automatizado de Monitoramento e Avaliacao Civica

**Feature Branch**: `001-pipeline-monitoramento`

**Created**: 2026-10-20

**Status**: Draft

**Input**: User description: "Criar a esteira automatizada de CI/CD para raspagem, telemetria e auditoria técnica contínua das iniciativas cívicas catalogadas, com invalidação de cache via hash SHA-256 e preservação perene no Internet Archive."

---

## User Scenarios & Testing

### User Story 1 - Revisão Autônoma e Atualização de Diagnósticos Técnicos (Priority: P1)

Como pesquisador ou mantenedor do Observatório TecnoPopular, quero que o sistema inspecione periodicamente as iniciativas catalogadas, recalcule sua conformidade cívica sob dimensões técnicas objetivas e gere laudos atualizados sem necessidade de operação manual no meu computador.

**Why this priority**: É a funcionalidade central do observatório. Sem ela, os diagnósticos tornam-se defasados rapidamente à medida que as aplicações mudam na reta eleitoral.

**Independent Test**: Pode ser testado executando a esteira sobre uma amostra de iniciativas catalogadas e verificando se os relatórios estruturados de conformidade são gerados com pontuações e recomendações práticas.

**Acceptance Scenarios**:

1. **Given** um catálogo de iniciativas cívicas cadastradas com URLs públicas ativas, **When** a esteira automatizada é disparada, **Then** o sistema inspeciona cada iniciativa e gera um diagnóstico técnico estruturado com nota total e notas por dimensão.
2. **Given** uma iniciativa com pendências técnicas prévias que corrigiu seus apontamentos (ex.: removeu rastreadores invasivos de terceiros), **When** a esteira executa a reavaliação, **Then** o status da ferramenta progride para o nível de conformidade correspondente e o selo é atualizado.

---

### User Story 2 - Invalidação Inteligente de Cache via Hash Criptográfico (Priority: P1)

Como operador da infraestrutura, quero que o sistema detecte se o conteúdo público de uma iniciativa sofreu qualquer alteração comparando o hash criptográfico do snapshot com o registro anterior, de modo que páginas inalteradas não consumam tempo de processamento nem cotas de inteligência artificial semântica.

**Why this priority**: Reduz o custo operacional e o tempo de execução do pipeline em mais de 90%, viabilizando execuções frequentes dentro dos limites do plano gratuito de automação.

**Independent Test**: Pode ser testado executando duas rodadas consecutivas da esteira sem alterações nos sites-alvo: a segunda rodada deve concluir em poucos segundos sem reprocessar as baterias semânticas.

**Acceptance Scenarios**:

1. **Given** uma iniciativa cujo conteúdo não sofreu nenhuma alteração desde a última checagem, **When** a esteira inspeciona o site e detecta hash idêntico, **Then** o sistema reutiliza o laudo existente, preservando o timestamp anterior e pulando etapas semânticas.
2. **Given** uma iniciativa que adicionou ou removeu código, texto ou scripts no HTML, **When** a esteira detecta um hash divergente, **Then** a ferramenta é marcada para reavaliação completa das 5 dimensões e atualização de laudo.

---

### User Story 3 - Preservação Histórica e Não-Repúdio em Arquivo Público (Priority: P2)

Como cidadão, jornalista ou auditor independente, quero ter acesso à prova histórica de funcionamento de cada ferramenta cívica por meio de um repositório arquivístico de fé pública (Internet Archive) e da cadeia de custódia do hash SHA-256, para que a memória da aplicação não desapareça mesmo que o domínio saia do ar após o período eleitoral.

**Why this priority**: Assegura a perenidade dos dados e o valor histórico do observatório sem incorrer no risco jurídico de hospedar cópias não autorizadas de código de terceiros no repositório do projeto.

**Independent Test**: Pode ser testado inspecionando o diagnóstico gerado para uma iniciativa alterada e confirmando a presença do hash de integridade e do link público perene de visualização histórica.

**Acceptance Scenarios**:

1. **Given** uma iniciativa recém-catalogada ou cujo hash indicou alteração de conteúdo, **When** a reavaliação é concluída com sucesso, **Then** o sistema despacha solicitação assíncrona de preservação ao Internet Archive e registra o endereço do snapshot permanente no laudo.
2. **Given** uma iniciativa que posteriormente foi desativada pelo autor, **When** um pesquisador consulta o laudo público no observatório, **Then** o link do arquivo histórico permite navegar na página exatamente como ela existia no dia da avaliação.

---

### User Story 4 - Blindagem de Privacidade e Bloqueio de Dados Pessoais (Priority: P3)

Como oficial de conformidade e privacidade (LGPD), quero garantir que nenhum dado pessoal identificado durante as raspagens (como números de WhatsApp de campanha, e-mails de pessoas físicas ou chaves PIX de doação presentes nas páginas) seja persistido nos relatórios públicos ou gravado no controle de versão.

**Why this priority**: Evita exposição indevida de voluntários e assegura conformidade estrita com a LGPD e as normas do projeto.

**Independent Test**: Pode ser testado submetendo uma página que contenha intencionalmente um número de telefone e chave PIX de teste e validando que o filtro de privacidade bloqueia ou higieniza esses dados antes de qualquer persistência.

**Acceptance Scenarios**:

1. **Given** uma página pública contendo contatos pessoais ou chaves de transferência no corpo do texto, **When** o pipeline extrai os metadados técnicos, **Then** esses dados pessoais são integralmente descartados da persistência pública.
2. **Given** qualquer relatório consolidado prestes a ser publicado, **When** o portão de privacidade detecta potencial identificador pessoal privado, **Then** o processo de publicação é bloqueado até a devida sanitização.

---

### Edge Cases

- **Iniciativa Indisponível (HTTP 4xx/5xx ou Falha de Conexão):** O sistema deve registrar status de indisponibilidade no snapshot, computar penalidade de tempo de resposta sem abortar a esteira das demais ferramentas e manter o histórico anterior sinalizado como "em verificação".
- **Timeout por Lentidão Extrema (> 8 segundos):** O sistema deve interromper a requisição daquela iniciativa específica, registrar o incidente e prosseguir imediatamente para a próxima ferramenta da fila.
- **Instabilidade ou Limite de Taxa no Internet Archive:** A indisponibilidade temporária do serviço de preservação externo não deve interromper a geração do laudo técnico local; o sistema deve registrar o hash e agendar a tentativa de arquivamento para o próximo ciclo.
- **Páginas com Aplicações de Página Única (SPA Dinâmicas):** Caso o HTML estático inicial contenha apenas contêiner vazio, o sistema deve registrar a necessidade de inspeção renderizada ou avaliar com base nas declarações estruturadas da iniciativa.

---

## Requirements

### Functional Requirements

- **FR-001**: O sistema DEVE iterar sobre todas as iniciativas cadastradas no catálogo canônico de forma isolada, garantindo que falhas em uma iniciativa não afetem o processamento das demais.
- **FR-002**: O sistema DEVE capturar o snapshot público de cada iniciativa respeitando identificação institucional transparente e limites estritos de tempo de resposta.
- **FR-003**: O sistema DEVE calcular e registrar o hash SHA-256 do conteúdo bruto obtido em cada inspeção.
- **FR-004**: O sistema DEVE comparar o hash calculado com o hash da avaliação imediatamente anterior, pulando reavaliações analíticas custosas se o conteúdo for idêntico.
- **FR-005**: O sistema DEVE executar a avaliação estruturada e auditável sob as **5 dimensões cívicas do Protocolo TecnoPopular**:
  - **FR-005A (Proveniência de Dados - Peso 25%):** Inspecionar se há declaração e links diretos para bases oficiais de custódia pública (TSE, IBGE, DATASUS, CGU), assegurando reprodutibilidade do dado.
  - **FR-005B (Privacidade e Soberania - Peso 25%):** Varrer o DOM em busca de pixels e rastreadores comerciais de Big Techs (Meta Pixel, Google Tag Manager, GA4, Hotjar, Clarity) e formulários de captura de dados pessoais (CPF/telefone), atribuindo bloqueador crítico (`rejeitado`) se identificada telemetria comercial invasiva.
  - **FR-005C (Código Aberto e FLOSS - Peso 20%):** Validar a presença de repositório público de código (GitHub, GitLab, Codeberg, Hugging Face) e presença de licença livre aprovada pela OSI para software (MIT, GPL-3.0, Apache-2.0). Licenças de conteúdo (Creative Commons) são registradas, mas não contam como código aberto.
  - **FR-005D (Enquadramento Propositivo - Peso 15%):** Avaliar a presença de vocabulário de utilidade pública e direitos materiais (transparência, cidadania, serviços essenciais) e penalizar terminologias de ataques *ad hominem* polarizadores que acionem o *backfire effect* cognitivo (Lakoff) ou riscos jurídicos.
  - **FR-005E (Acessibilidade e Desempenho - Peso 15%):** Mensurar latência de resposta, peso do payload transferido (< 500 KB) e ausência de paywalls comerciais ou banners publicitários, garantindo usabilidade em celulares periféricos.
- **FR-006**: O sistema DEVE calcular a nota final ponderada (0 a 100) e classificar o status cívico conforme a nota de corte da rubrica:
  - `auditado` (Selo Certificado TecnoPopular): Nota $\ge 80$ e zero bloqueadores críticos.
  - `catalogado` (Diretório Aberto): Nota $\ge 50$ e $< 80$.
  - `em_revisao`: Nota $< 50$.
  - `rejeitado`: Presença de bloqueador crítico de privacidade.
  - `nao_inspecionado`: ausência de leitura útil (sem resposta, erro HTTP, timeout ou redirecionamento para autenticação). Prevalece sobre as demais classificações e não expressa juízo de qualidade.
- **FR-007**: O sistema DEVE acionar a solicitação assíncrona de captura histórica no Internet Archive apenas quando for detectada alteração no hash ou em novas iniciativas cadastradas.
- **FR-008**: O sistema DEVE consolidar todos os resultados em relatório estruturado centralizado, mantendo consistência estrita de pontuações, datas e identificadores.
- **FR-009**: O sistema DEVE validar preventivamente a ausência de dados pessoais privados (PII) e segredos de ambiente antes de qualquer gravação ou publicação de dados.
- **FR-010**: O sistema DEVE suportar acionamento periódico automático (agendamento semanal) e disparo manual por operadores.
- **FR-011**: O sistema DEVE gerar automaticamente as páginas públicas de diagnóstico e atualizar o índice do catálogo após a consolidação dos dados.
- **FR-013**: O sistema DEVE registrar a readmissão de iniciativa retirada como evento novo no mesmo histórico, preservando o pedido de saída e seu motivo, e DEVE devolver ao catálogo a entrada guardada no momento da retirada.
- **FR-012**: O sistema DEVE acatar pedido explícito de retirada, registrando em arquivo versionado o motivo informado, a origem e as datas do pedido, e DEVE deixar de auditar o endereço retirado mesmo que a entrada permaneça no catálogo. Motivo que contenha dado pessoal de pessoa física DEVE ser recusado, por ser o registro público.

---

### Key Entities

- **Iniciativa Cívica**: Representa a aplicação ou ferramenta monitorada. Possui identificador único (*slug*), nome público, URL de acesso, indicação de repositório de código e declaração de fontes públicas oficiais.
- **Snapshot de Inspeção**: Registro pontual de uma coleta. Contém o carimbo de data/hora (ISO 8601), código de resposta da rede, tempo de resposta em milissegundos, tamanho em bytes e hash criptográfico SHA-256 do conteúdo.
- **Laudo de Diagnóstico Técnico**: Avaliação consolidada da iniciativa. Contém nota geral ponderada (0 a 100), notas individuais das 5 dimensões na mesma escala, nível de conformidade técnica atribuído, lista de orientações práticas para melhoria, hash SHA-256 do snapshot avaliado, cobertura da inspeção e URL do registro histórico no Internet Archive.
- **Catálogo Consolidado**: Conjunto de todos os laudos e metadados vigentes que alimentam as interfaces públicas, arquivos abertos e índices de busca.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: O pipeline completo para 100+ iniciativas deve executar em menos de 3 minutos no ambiente de automação em cenários onde a maioria dos sites não sofreu alterações.
- **SC-002**: 100% das iniciativas sem alterações de conteúdo devem ser processadas em modo cache instantâneo, com zero chamadas redundantes a modelos de inteligência artificial ou serviços de arquivamento.
- **SC-003**: Zero registros de dados pessoais privados (PII) ou segredos de ambiente devem ser gravados nos relatórios públicos ou no histórico de versões do repositório.
- **SC-004**: 100% dos laudos publicados devem exibir a data exata da última verificação, o hash SHA-256 auditável e o link perene para visualização histórica.
- **SC-005**: 100% das páginas de diagnóstico estáticas geradas devem manter exata correspondência matemática de pontuação com o catálogo central estruturado.

---

## Assumptions

- O catálogo inicial contém aproximadamente 100 iniciativas mapeadas com URLs públicas acessíveis via rede externa.
- A grande maioria das iniciativas cívicas é atualizada com frequência semanal ou quinzenal, tornando a estratégia de invalidação por hash altamente eficiente.
- A infraestrutura de automação pública (GitHub Actions em repositórios abertos) fornece minutos suficientes para execuções semanais regulares e disparos sob demanda.
- A preservação histórica apoia-se em serviços públicos de arquivamento digital perene (Wayback Machine), não necessitando de armazenamento em nuvem proprietário pago para retenção de arquivos brutos.
