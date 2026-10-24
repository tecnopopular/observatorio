# Como reproduzir

Este repositório é de **reprodução de pesquisa**: publica o motor que produziu um resultado e os
dados que sustentam as conclusões, não um projeto de software em desenvolvimento aberto. A distinção
importa para o que se pode esperar daqui.

Reproduzir o ciclo não é reproduzir as notas. O objeto auditado é um site vivo: o laudo é uma
fotografia datada de uma infraestrutura, e a mesma execução feita meses depois encontra outro
conteúdo. O que se reproduz de forma exata é o comportamento do motor sobre um conteúdo fixo, e a
estrutura dos artefatos. O restante está declarado na seção 6.

## 1. Ambiente

- Python 3.14, declarado em `.python-version`.
- `uv` como único gerenciador de dependências e de ambiente.
- Versões travadas em `uv.lock`: a instalação é reprodutível byte a byte.

```bash
uv sync
```

Não é preciso chave de API para o ciclo padrão. `--fetch-provider firecrawl` (aquisição por provedor
externo) e `--semantic jev` (camada semântica calibrada) exigem credencial própria, lida do Keychain
do macOS no ambiente local ou de variável de ambiente em CI; sem ela, esses modos não rodam e o
ciclo básico segue funcionando.

## 2. Portões de qualidade

Rodam antes de qualquer resultado ser considerado válido, e no CI a cada push:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python tools/verify_docstrings.py
uv run python tools/verify_dead_code.py
uv run python -m unittest discover tests
uv run python -m observatorio.cli verify-privacy --all-files
```

Saída esperada, no estado deste commit: 53 arquivos formatados, lint sem achados, docstrings
conformes em 41 arquivos, nenhum código morto acionável, 203 testes aprovados, portão de privacidade
aprovado. Os quatro primeiros portões são mecânicos de propósito: reprovar formatação, docstring
ausente ou código morto não depende de opinião.

## 3. Ciclo completo

```bash
uv run python -m observatorio.cli audit-all --workers 12 --timeout 12
```

Produz dois artefatos com papéis distintos:

| Artefato | Onde | Versionado | Papel |
| :--- | :--- | :--- | :--- |
| Laudo completo | `dist/auditoria_iniciativas.json` | não | publicação do ciclo, entregue ao site |
| Manifesto de estado | `data/estado_hashes.json` | sim | hash, nota e status por iniciativa |

O laudo não é versionado porque é saída de build, e saída de build não entra no histórico. O que entra
é o manifesto, que permite verificar o que mudou entre ciclos sem carregar o laudo inteiro.

`--skip-archive` desliga a preservação no Internet Archive; `--refresh` ignora o cache local e relê
cada página pela rede.

## 4. Números de referência

Estado do último ciclo registrado no manifesto:

| Medida | Valor |
| :--- | :--- |
| Ciclo | 2026-10-24T05:56:33Z |
| Iniciativas no catálogo | 174 |
| Laudos emitidos | 171 |
| Puladas por `robots.txt` | 3 |
| Certificadas (selo) | 28 |
| Catalogadas no diretório aberto | 112 |
| Rejeitadas | 25 |
| Não inspecionadas | 6 |
| Com hash de HTML registrado | 165 |
| Inspeções completas / parciais | 143 / 28 |
| Iniciativas com hash novo ou alterado no ciclo | 36 |
| Tamanho do manifesto | 89.805 bytes |

Para conferir localmente depois de um ciclo:

```bash
uv run python -c "
import collections, json
from pathlib import Path
m = json.loads(Path('data/estado_hashes.json').read_text(encoding='utf-8'))
print(m['generated_at'], m['total'])
print(dict(collections.Counter(e['status'] for e in m['entries'])))
"
```

Esses números mudam a cada ciclo, e mudam por três motivos distintos: o conteúdo dos sites muda, o
`robots.txt` de um host muda, ou uma iniciativa entra ou sai do catálogo. Divergência não é erro por
si; o que caracteriza erro é divergência sem nenhuma dessas três causas. Para comparar dois ciclos:

```bash
uv run python tools/compare_cycles.py
```

O comando compara o laudo do ciclo atual com o manifesto gravado no último commit e informa o que
mudou de nota, de status e de hash. `--anterior` e `--revisao` permitem escolher outra base.

## 5. Reprodução offline a partir do cache

Esta é a única forma de chegar às mesmas notas sem depender do estado atual dos sites.

A aquisição guarda o corpo lido em `data/raw/html_cache/<tool_id>.html`, com um sidecar
`<tool_id>.meta.json` que registra o tamanho declarado e a marca de truncamento. Sem `--refresh`, o
ciclo reaproveita esse corpo: a avaliação passa a rodar sobre conteúdo fixo, e a nota é reproduzível.

O cache não é versionado: é conteúdo de terceiros e não é nosso para publicar no histórico. Para
citar um resultado de forma verificável, o cache e o laudo são anexados à release da tag
correspondente (`.github/workflows/release.yml`), com o `html_sha256` do manifesto como prova de que
aquele corpo é o que foi avaliado.

Publicada a release, o conteúdo pode ser recuperado e conferido sem depender do estado atual dos
sites:

```bash
gh release download v0.1.0
sha256sum -c SHA256SUMS
tar -xzf cache-html.tar.gz -C data/raw
uv run python tools/verify_cache.py
uv run python -m observatorio.cli audit-all --refresh
```

O portão `tools/verify_cache.py` confronta cada corpo em cache com o hash registrado no
manifesto. No ciclo de referência, 166 corpos foram conferidos, nenhum divergiu e 5 entradas ficaram
fora por não terem hash registrado — são as não inspecionadas, que não produziram nota.

Uma leitura parcial continua parcial: o sidecar marca o truncamento, e o ciclo seguinte não
transforma uma página lida pela metade em leitura completa.

## 6. O que este repositório não reproduz

| Elemento | Reproduz | Por quê |
| :--- | :--- | :--- |
| Catálogo, `tool_id`, validação de entrada | sim, byte a byte | arquivo versionado, sem dependência externa |
| Estrutura do manifesto e do laudo | sim | contrato definido em `specs/` |
| Notas, com o mesmo cache de HTML | sim | a avaliação é determinística sobre o corpo lido |
| Notas, sem cache | não | o conteúdo dos sites muda entre execuções |
| Dimensão de acessibilidade e desempenho | não | mede a latência daquela requisição, com limiares em 1.500 ms e 3.000 ms |
| Disponibilidade de uma iniciativa | não | um site pode sair do ar entre ciclos e a nota cai sem que o código tenha mudado |
| Consulta de `robots.txt` | não | um host pode publicar ou revogar a diretiva a qualquer momento |
| Preservação no Internet Archive | não | a API recusa requisições não autenticadas, e o link pode não existir |
| Camada semântica (JEV) | não | depende de API externa e de chave; ajusta a nota só acima de confiança 0,90 |
| Números de um ciclo anterior | não | cada release carrega o ciclo em que foi gerada, com data própria no `PROCEDENCIA.txt` |

O uso de inteligência artificial generativa na produção deste repositório, e a camada semântica
opcional que pode ajustar notas, estão declarados em `DECLARACAO_IA.md`, conforme a Portaria CNPq
nº 2.664/2026.

O `html_sha256` do manifesto é o mecanismo de não-repúdio: ele identifica o conteúdo que foi avaliado.
Um hash histórico não é recalculado nem reescrito — se o site mudou, muda o hash do ciclo seguinte, e
o anterior permanece como registro do que foi lido naquela data.
