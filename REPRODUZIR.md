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

Não é preciso chave de API para o ciclo: a aquisição é HTTP direto. `--semantic jev` (camada
semântica calibrada) é o único modo que exige credencial própria, lida do Keychain do macOS no
ambiente local ou de variável de ambiente em CI; sem ela, esse modo não roda e o ciclo básico segue
funcionando.

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
| Ciclo | 2026-10-24T16:54:13Z |
| Versão do protocolo | 2 (`CTP-1.1`) |
| Iniciativas no catálogo | 174 |
| Laudos emitidos | 171 |
| Puladas por `robots.txt` | 3 |
| Certificadas (selo) | 22 |
| Catalogadas no diretório aberto | 119 |
| Rejeitadas | 25 |
| Não inspecionadas | 5 |
| Com hash de HTML registrado | 166 |
| Inspeções completas / parciais | 144 / 27 |
| Tamanho do manifesto | 89.731 bytes |

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

## 5. Reprodução das notas e o que o cache prova

A aquisição guarda o corpo lido em `data/raw/html_cache/<tool_id>.html`, com sidecar
`<tool_id>.meta.json` registrando tamanho declarado e marca de truncamento. Sem `--refresh`, o ciclo
reaproveita esse corpo: a avaliação passa a rodar sobre conteúdo fixo e a nota é reproduzível na
mesma máquina.

Esse cache **não é publicado**, nem versionado nem anexado a release. Ele é conteúdo de terceiros:
169 páginas, 15 MB de HTML alheio, com endereços de contato e outros dados que não nos pertencem e
que não temos como certificar. Publicá-lo foi tentado nas três primeiras releases e revertido; o
motivo está registrado no histórico do repositório e a verificação que faltava agora existe
(`tools/verify_attachments.py`, que recusa qualquer anexo que não seja JSON ou texto).

O que o repositório oferece no lugar, para tornar a nota conferível:

| O que | Onde | Para que serve |
| :--- | :--- | :--- |
| `html_sha256` de cada página avaliada | `data/estado_hashes.json` | dizer se o corpo que você baixar é o mesmo que foi avaliado |
| Notas por dimensão e status | `data/estado_hashes.json` | comparar com o valor que você recalculou |
| Laudo completo do ciclo | anexo da release | ler a evidência que sustentou cada ponto |
| Versão do protocolo | manifesto e laudo | saber sob qual critério a nota foi calculada |

O procedimento de conferência, então, é este:

```bash
gh release download v0.2.2
sha256sum -c SHA256SUMS
uv run python -c "import json;from pathlib import Path;print(json.loads(Path('estado_hashes.json').read_text())['entries'][0]['html_sha256'])"
curl -sL "$URL" | sha256sum   # confere contra o hash registrado
uv run python -m observatorio.cli audit-all --refresh
```

Duas leituras possíveis do resultado: se o hash bater, a nota registrada é recalculável byte a byte
na sua máquina, e qualquer divergência de nota indica defeito no motor. Se não bater, a página mudou
depois do ciclo — a nota registrada continua sendo a daquele snapshot datado, e a diferença é
informação sobre o site, não erro de reprodução.

A aquisição em rede é a fonte de variância que resta, e ela está declarada na seção 6.

## 6. O que este repositório não reproduz

| Elemento | Reproduz | Por quê |
| :--- | :--- | :--- |
| Catálogo, `tool_id`, validação de entrada | sim, byte a byte | arquivo versionado, sem dependência externa |
| Estrutura do manifesto e do laudo | sim | contrato definido em `specs/` |
| Notas, com o mesmo cache de HTML | sim, na mesma máquina | a avaliação é determinística sobre o corpo lido; o cache não é publicado |
| Notas, sem cache | não | o conteúdo dos sites muda entre execuções; é o caso de quem baixa a release sem ter o cache local |
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
