# Dados do Observatório TecnoPopular

Arquivos de dados canônicos do observatório. O código que os produz está sob
AGPL-3.0 (ver `../LICENSE`); os dados aqui são licenciados separadamente.

## Licença

**Creative Commons Atribuição 4.0 Internacional (CC-BY-4.0)** — texto integral em
[LICENSE](LICENSE).

Atribuição exigida em qualquer reutilização, redistribuição ou obra derivada:

```text
Fonte: Observatório TecnoPopular (https://tecnopopular.org), licenciado sob CC-BY-4.0.
```

A CC-BY-4.0 não impõe copyleft: relatórios jornalísticos, artigos acadêmicos,
dashboards e produtos derivados podem usar os dados livremente, inclusive com
fins comerciais, desde que a atribuição seja preservada.

## Arquivos

| Arquivo | Papel | Versionado |
| :--- | :--- | :--- |
| `data_iniciativas.json` | Catálogo canônico de entrada: iniciativas cívicas monitoradas (nome, URL, domínio, categoria, descrição) | sim |
| `estado_hashes.json` | Manifesto de estado do último ciclo: hash SHA-256, data da avaliação, nível de conformidade, notas por dimensão e completude da cobertura | sim |
| `retiradas.json` | Registro append-only de eventos de retirada e readmissão: tipo do evento, nome, URL, motivo informado, origem e datas, para que a razão de cada movimento sobreviva à edição do catálogo | sim |
| `raw/` | Cache bruto de rede (HTML de terceiros) e artefatos transitórios | não (gitignored) |

O laudo pode ter menos entradas que o catálogo: iniciativas cujo `robots.txt` proíbe a leitura não
recebem laudo e permanecem apenas em `data_iniciativas.json`. O manifesto segue a mesma regra, por
ser projeção do laudo.

`retiradas.json` é append-only: um evento registrado não é editado nem apagado, porque o registro é
o que resta de uma iniciativa que saiu do catálogo. O estado vigente de um endereço é o último evento
registrado para ele: `retirada` tira a iniciativa do catálogo, `readmissao` a devolve. Os dois
subcomandos que gravam neste arquivo, `retirar` e `readmitir`, recusam motivo com dado pessoal de
pessoa física — o arquivo é versionado e público.

O laudo completo (com achados, recomendações e texto de orientação) **não é versionado**:
sai em `dist/auditoria_iniciativas.json`, é publicado como artefato do ciclo no GitHub
Actions e entregue ao site. O manifesto é o que fica no histórico: compacto, revisável e
suficiente para decidir o cache e auditar a evolução das notas.

> Nota: as entradas atuais do manifesto têm `html_sha256: null` porque o laudo herdado foi
> gerado antes do carimbo de hash entrar em vigor. O primeiro ciclo com rede preenche os
> hashes; até lá, o motor trata todas as iniciativas como não avaliadas.

## Integridade

Cada laudo carrega o hash SHA-256 do snapshot inspecionado. O hash é o
mecanismo de não-repúdio: nunca recalcular, reescrever ou "corrigir" um hash
histórico. Conteúdo bruto de terceiros não é redistribuído neste repositório;
a preservação perene é delegada ao Internet Archive (Wayback Machine) e
referenciada no campo `archive_url`.

## Dados pessoais

Estes arquivos contêm exclusivamente metadados de ferramentas e
infraestruturas públicas. Nenhum dado pessoal identificável (CPF, telefone,
e-mail de pessoa física, chave PIX) é aceito neste diretório. O portão
`CivicPrivacyGuard` bloqueia a publicação caso detecte violação.
