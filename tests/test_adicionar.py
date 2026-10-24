"""Testes da inclusão de iniciativas no catálogo.

Cobrem a validação do catálogo, a identidade congelada do `tool_id` e o subcomando `adicionar`,
incluindo as recusas que protegem o acervo: endereço repetido, host que proíbe a leitura e texto
com dado pessoal.
"""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from observatorio.catalogo import (
    CATEGORIAS,
    assign_tool_ids,
    ids_em_uso,
    novo_tool_id,
    sem_fragmento,
    slugify,
    validar_catalogo,
    validar_entrada,
)
from observatorio.cli import cmd_adicionar
from observatorio.retiradas import chave
from observatorio.robots import RobotsDecision

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO_REAL = RAIZ / "data" / "data_iniciativas.json"
DESCRICAO = "Painel público que reúne dados de votação por seção eleitoral."


def _executar(catalogo, registro, url="https://novo-exemplo.org", nome="Iniciativa Nova", **extras):
    """Executa o subcomando de inclusão sobre arquivos de teste.

    Args:
        catalogo: Caminho do catálogo.
        registro: Caminho do registro de retiradas.
        url: Endereço a incluir.
        nome: Nome a incluir.
        **extras: Campos opcionais sobrescritos, como `categoria`, `descricao` ou `tool_id`.

    Returns:
        Código de saída do subcomando.
    """
    padrao = {
        "categoria": "Dados e Mapas",
        "descricao": DESCRICAO,
        "repositorio": None,
        "fontes_primarias": None,
        "tool_id": None,
        "ignorar_robots": True,
    }
    padrao.update(extras)
    args = Namespace(
        url=url,
        nome=nome,
        catalogo=str(catalogo),
        registro=str(registro),
        **padrao,
    )
    return cmd_adicionar(args)


class TestValidacaoDoCatalogo(unittest.TestCase):
    """Verifica o portão que roda antes de qualquer ciclo."""

    def test_catalogo_valido_passa(self):
        """Entrada completa e única não gera problema."""
        catalogo = [{"nome": "A", "url": "https://a.org", "categoria": "Jogos"}]
        self.assertEqual(validar_catalogo(catalogo), [])

    def test_sem_nome_e_sem_url(self):
        """Os dois campos que o motor lê diretamente são obrigatórios."""
        problemas = validar_catalogo([{"url": "https://a.org"}, {"nome": "B"}])
        self.assertTrue(any("sem nome" in p for p in problemas))
        self.assertTrue(any("sem URL" in p for p in problemas))

    def test_url_sem_esquema(self):
        """URL sem esquema falharia em silêncio no ciclo; o portão recusa antes."""
        problemas = validar_catalogo([{"nome": "A", "url": "a.org"}])
        self.assertTrue(any("esquema" in p for p in problemas))

    def test_fragmento_nao_separa_enderecos(self):
        """Ancora de página não cria iniciativa nova: o servidor nunca a recebe."""
        self.assertEqual(chave("https://a.org/#secao"), chave("https://a.org"))

    def test_endereco_repetido_por_variante(self):
        """Duas grafias do mesmo endereço são a mesma iniciativa."""
        catalogo = [
            {"nome": "A", "url": "https://exemplo.org"},
            {"nome": "B", "url": "http://www.exemplo.org/"},
        ]
        problemas = validar_catalogo(catalogo)
        self.assertTrue(any("mesmo endereço" in p for p in problemas))

    def test_tool_id_repetido(self):
        """Dois registros com o mesmo identificador sobrescreveriam laudo e cache."""
        catalogo = [
            {"nome": "A", "url": "https://a.org", "tool_id": "igual"},
            {"nome": "B", "url": "https://b.org", "tool_id": "igual"},
        ]
        problemas = validar_catalogo(catalogo)
        self.assertTrue(any("tool_id repetido" in p for p in problemas))

    def test_categoria_desconhecida(self):
        """Categoria fora da lista quebraria a navegação do site."""
        problemas = validar_catalogo([{"nome": "A", "url": "https://a.org", "categoria": "Outra"}])
        self.assertTrue(any("categoria" in p for p in problemas))

    def test_entrada_sem_descricao(self):
        """A inclusão exige descrição com substância; o catálogo lido, não."""
        problemas = validar_entrada(
            {"nome": "A", "url": "https://a.org", "categoria": "Jogos", "tool_id": "a"}, []
        )
        self.assertTrue(any("descri" in p for p in problemas))

    def test_entrada_com_tool_id_ocupado(self):
        """Identificador já usado é recusado mesmo com nome diferente."""
        catalogo = [{"nome": "Outra", "url": "https://outra.org", "tool_id": "a"}]
        problemas = validar_entrada(
            {
                "nome": "A",
                "url": "https://a.org",
                "categoria": "Jogos",
                "descricao": DESCRICAO,
                "tool_id": "a",
            },
            catalogo,
        )
        self.assertTrue(any("tool_id já usado" in p for p in problemas))


class TestIdentidade(unittest.TestCase):
    """Verifica a atribuição e a estabilidade do `tool_id`."""

    def test_slug_remove_acento_e_espaco(self):
        """O identificador precisa ser seguro em URL."""
        self.assertEqual(slugify("Mapa do Orçamento Público"), "mapa-do-orcamento-publico")

    def test_tool_id_gravado_prevalece_sobre_o_nome(self):
        """Renomear a entrada não muda o endereço do laudo."""
        catalogo = [{"nome": "Nome Novo", "url": "https://a.org", "tool_id": "nome-antigo"}]
        assign_tool_ids(catalogo)
        self.assertEqual(catalogo[0]["_tool_id"], "nome-antigo")

    def test_nome_repetido_desambigua(self):
        """Nome repetido continua recebendo sufixo quando não há identificador gravado."""
        catalogo = [
            {"nome": "Igual", "url": "https://a.org"},
            {"nome": "Igual", "url": "https://b.org"},
        ]
        colisoes = assign_tool_ids(catalogo)
        self.assertEqual([c["_tool_id"] for c in catalogo], ["igual", "igual-2"])
        self.assertEqual(len(colisoes), 1)

    def test_novo_tool_id_desvia_de_ocupado(self):
        """Nome que colidiria com identificador existente recebe sufixo livre."""
        catalogo = [{"nome": "Mapa", "url": "https://a.org", "tool_id": "mapa"}]
        self.assertEqual(novo_tool_id("Mapa", catalogo), "mapa-2")

    def test_ids_em_uso_nao_altera_catalogo(self):
        """A simulação de identificadores não deixa resíduo no catálogo real."""
        catalogo = [{"nome": "A", "url": "https://a.org"}]
        ids_em_uso(catalogo)
        self.assertNotIn("_tool_id", catalogo[0])


class TestComandoAdicionar(unittest.TestCase):
    """Verifica o subcomando de inclusão ponta a ponta em arquivos temporários."""

    def _preparar(self, tmp):
        """Cria catálogo vazio e registro de retiradas vazio.

        Args:
            tmp: Diretório temporário.

        Returns:
            Par com os caminhos do catálogo e do registro.
        """
        catalogo = tmp / "catalogo.json"
        catalogo.write_text("[]", encoding="utf-8")
        registro = tmp / "retiradas.json"
        registro.write_text('{"versao": 1, "eventos": []}', encoding="utf-8")
        return catalogo, registro

    def test_inclui_com_identificador_e_data(self):
        """A entrada gravada traz tool_id congelado, domínio derivado e data de inclusão."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            codigo = _executar(catalogo, registro, url="https://www.exemplo.org/painel")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(len(gravado), 1)
        entrada = gravado[0]
        self.assertEqual(entrada["tool_id"], "iniciativa-nova")
        self.assertEqual(entrada["dominio"], "www.exemplo.org")
        self.assertEqual(entrada["categoria"], "Dados e Mapas")
        self.assertIn("adicionado_em", entrada)

    def test_endereco_repetido_e_recusado(self):
        """Incluir duas vezes o mesmo endereço é recusado, mesmo com outra grafia."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            primeiro = _executar(catalogo, registro)
            segundo = _executar(catalogo, registro, url="http://www.novo-exemplo.org/")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(primeiro, 0)
        self.assertEqual(segundo, 1)
        self.assertEqual(len(gravado), 1)

    def test_endereco_retirado_e_recusado(self):
        """Readmitir é outro comando; a inclusão não atropela o registro de retiradas."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            registro.write_text(
                json.dumps(
                    {
                        "versao": 1,
                        "eventos": [
                            {
                                "tipo": "retirada",
                                "nome": "Iniciativa Nova",
                                "url": "https://novo-exemplo.org",
                                "motivo": "Pedido do coletivo.",
                                "canal": "email",
                                "solicitado_em": "2026-10-24",
                                "registrado_em": "2026-10-24",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            codigo = _executar(catalogo, registro, url="https://novo-exemplo.org")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_host_que_proibe_a_leitura_e_recusado(self):
        """Incluir host com Disallow: / criaria entrada que nunca recebe laudo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            proibido = RobotsDecision(
                False, "proibido", "robots.txt proíbe este endereço para todos os agentes"
            )
            with patch("observatorio.cli.RobotsPolicy") as politica:
                politica.return_value.decidir.return_value = proibido
                codigo = _executar(catalogo, registro, ignorar_robots=False)
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_host_permissivo_permite_inclusao(self):
        """A consulta a robots.txt não bloqueia quando o host permite."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            permitido = RobotsDecision(True, "permitido", "regras do host permitem a leitura")
            with patch("observatorio.cli.RobotsPolicy") as politica:
                politica.return_value.decidir.return_value = permitido
                codigo = _executar(catalogo, registro, ignorar_robots=False)
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(len(gravado), 1)

    def test_descricao_com_dado_pessoal_e_recusada(self):
        """Descrição é publicada no catálogo; dado pessoal é recusado."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            contato = "@".join(["maria.souza", "gmail" + ".com"])
            codigo = _executar(catalogo, registro, descricao=f"Painel mantido por {contato} em 2026.")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_tool_id_explicito_e_respeitado(self):
        """O operador pode fixar um identificador próprio, desde que seja slug livre."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            codigo = _executar(catalogo, registro, tool_id="id-escolhido")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(gravado[0]["tool_id"], "id-escolhido")

    def test_fragmento_e_removido_ao_incluir(self):
        """O endereço gravado não carrega âncora de página."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalogo, registro = self._preparar(tmp)
            _executar(catalogo, registro, url="https://exemplo.org/painel#secao")
            gravado = json.loads(catalogo.read_text(encoding="utf-8"))

        self.assertEqual(gravado[0]["url"], "https://exemplo.org/painel")
        self.assertEqual(sem_fragmento("https://a.org/#x"), "https://a.org/")

    def test_categoria_obrigatoria_no_parser(self):
        """As categorias aceitas são as do catálogo, não texto livre."""
        self.assertIn("Dados e Mapas", CATEGORIAS)
        self.assertEqual(len(CATEGORIAS), 7)


class TestCatalogoVersionado(unittest.TestCase):
    """Verifica o catálogo real do repositório."""

    def test_catalogo_real_passa_no_portao(self):
        """O catálogo versionado precisa estar válido para o ciclo rodar."""
        catalogo = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        self.assertEqual(validar_catalogo(catalogo), [])

    def test_todas_as_entradas_tem_identificador(self):
        """Sem identificador gravado, um rename mudaria o endereço do laudo."""
        catalogo = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        sem_id = [item["nome"] for item in catalogo if not item.get("tool_id")]
        self.assertEqual(sem_id, [])

    def test_identificadores_sao_unicos(self):
        """Identificador repetido sobrescreveria laudo no acervo publicado."""
        catalogo = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        ids = [item["tool_id"] for item in catalogo]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
