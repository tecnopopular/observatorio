# Convenções de código e de texto

Este repositório tem duas línguas, e cada uma tem um lugar definido.

## 1. Código em inglês

Identificadores são em inglês, sempre: nomes de módulos, funções, classes, variáveis, parâmetros e
constantes internas. Quem lê o código — pessoa ou agente — não deveria precisar traduzir para
entender o que uma função faz.

```python
def register_withdrawal(registry, url, reason, requested_on):   # correto
def registrar_retirada(registro, url, motivo, solicitado_em):   # fora da convenção
```

## 2. Texto em português

Docstrings, mensagens de console e de log, mensagens de erro, README, specs e este arquivo são em
português. É o idioma de quem opera e de quem lê o resultado publicado.

```python
def register_withdrawal(registry, url, reason, requested_on):
    """Acrescenta um evento de retirada ao registro.      # texto em português
    ...
    """
    print("[-] Endereço já registrado como retirado")  # mensagem em português
```

## 3. Interface de linha de comando: português

Subcomandos e flags são texto para quem opera, não código: `adicionar`, `retirar`, `readmitir`,
`verify-privacy`, `--motivo`, `--catalogo`. Valores que são vocabulário de máquina ficam em inglês,
como `--fetch-provider http|firecrawl` e `--semantic off|jev`.

Comandos em inglês quando são ação técnica de auditoria (`audit-all`), em português quando são ato
editorial sobre o acervo (`adicionar`, `retirar`, `readmitir`).

## 4. O que não se renomeia

A regra do inglês vale para código, não para contrato de dados. Ficam como estão:

| Elemento | Por quê |
| :--- | :--- |
| Campos do JSON publicado (`tool_id`, `nome`, `url`, `dominio`, `categoria`, `descricao`, `fontes_primarias`, `html_sha256`, `total_score`) | contrato consumido pelo site e por releases já publicadas |
| `data/data_iniciativas.json`, `data/estado_hashes.json`, `data/retiradas.json` | caminhos citados em releases, documentação e hábitos de operação |
| Subcomandos e flags da CLI | texto de interface, não identificador de código |

Renomear qualquer item dessa tabela exige migração de dados e release nova, porque o estado publicado
passaria a existir em dois vocabulários. Enquanto não houver migração, o nome antigo é o nome correto.

## 5. Por que a distinção existe

Um repositório de reprodução precisa que o resultado publicado sobreviva à evolução do código. Se a
convenção de nomes alcançasse o JSON, qualquer refatoração quebraria laudo publicado, cache e
manifesto de uma vez. Separar código de contrato é o que permite renomear `chave` para `url_key` sem
tocar em um único arquivo de dado.
