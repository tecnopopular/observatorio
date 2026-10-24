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

from observatorio.catalog import (
    CATEGORIES,
    assign_tool_ids,
    ids_in_use,
    new_tool_id,
    slugify,
    strip_fragment,
    validate_catalog,
    validate_entry,
)
from observatorio.cli import cmd_add
from observatorio.robots import RobotsDecision
from observatorio.withdrawals import url_key

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO_REAL = RAIZ / "data" / "data_iniciativas.json"
DESCRICAO = "Painel público que reúne dados de votação por seção eleitoral."


def _executar(catalog, registry, url="https://novo-exemplo.org", nome="Iniciativa Nova", **extras):
    """Executa o subcomando de inclusão sobre arquivos de teste.

    Args:
        catalog: Caminho do catálogo.
        registry: Caminho do registro de retiradas.
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
        catalog=str(catalog),
        registry=str(registry),
        **padrao,
    )
    return cmd_add(args)


class TestValidacaoDoCatalogo(unittest.TestCase):
    """Verifica o portão que roda antes de qualquer ciclo."""

    def test_catalogo_valido_passa(self):
        """Entrada completa e única não gera problema."""
        catalog = [{"nome": "A", "url": "https://a.org", "categoria": "Jogos"}]
        self.assertEqual(validate_catalog(catalog), [])

    def test_sem_nome_e_sem_url(self):
        """Os dois campos que o motor lê diretamente são obrigatórios."""
        problemas = validate_catalog([{"url": "https://a.org"}, {"nome": "B"}])
        self.assertTrue(any("sem nome" in p for p in problemas))
        self.assertTrue(any("sem URL" in p for p in problemas))

    def test_url_sem_esquema(self):
        """URL sem esquema falharia em silêncio no ciclo; o portão recusa antes."""
        problemas = validate_catalog([{"nome": "A", "url": "a.org"}])
        self.assertTrue(any("esquema" in p for p in problemas))

    def test_fragmento_nao_separa_enderecos(self):
        """Ancora de página não cria iniciativa nova: o servidor nunca a recebe."""
        self.assertEqual(url_key("https://a.org/#secao"), url_key("https://a.org"))

    def test_endereco_repetido_por_variante(self):
        """Duas grafias do mesmo endereço são a mesma iniciativa."""
        catalog = [
            {"nome": "A", "url": "https://exemplo.org"},
            {"nome": "B", "url": "http://www.exemplo.org/"},
        ]
        problemas = validate_catalog(catalog)
        self.assertTrue(any("mesmo endereço" in p for p in problemas))

    def test_tool_id_repetido(self):
        """Dois registros com o mesmo identificador sobrescreveriam laudo e cache."""
        catalog = [
            {"nome": "A", "url": "https://a.org", "tool_id": "igual"},
            {"nome": "B", "url": "https://b.org", "tool_id": "igual"},
        ]
        problemas = validate_catalog(catalog)
        self.assertTrue(any("tool_id repetido" in p for p in problemas))

    def test_categoria_desconhecida(self):
        """Categoria fora da lista quebraria a navegação do site."""
        problemas = validate_catalog([{"nome": "A", "url": "https://a.org", "categoria": "Outra"}])
        self.assertTrue(any("categoria" in p for p in problemas))

    def test_entrada_sem_descricao(self):
        """A inclusão exige descrição com substância; o catálogo lido, não."""
        problemas = validate_entry(
            {"nome": "A", "url": "https://a.org", "categoria": "Jogos", "tool_id": "a"}, []
        )
        self.assertTrue(any("descri" in p for p in problemas))

    def test_entrada_com_tool_id_ocupado(self):
        """Identificador já usado é recusado mesmo com nome diferente."""
        catalog = [{"nome": "Outra", "url": "https://outra.org", "tool_id": "a"}]
        problemas = validate_entry(
            {
                "nome": "A",
                "url": "https://a.org",
                "categoria": "Jogos",
                "descricao": DESCRICAO,
                "tool_id": "a",
            },
            catalog,
        )
        self.assertTrue(any("tool_id já usado" in p for p in problemas))


class TestIdentidade(unittest.TestCase):
    """Verifica a atribuição e a estabilidade do `tool_id`."""

    def test_slug_remove_acento_e_espaco(self):
        """O identificador precisa ser seguro em URL."""
        self.assertEqual(slugify("Mapa do Orçamento Público"), "mapa-do-orcamento-publico")

    def test_tool_id_gravado_prevalece_sobre_o_nome(self):
        """Renomear a entrada não muda o endereço do laudo."""
        catalog = [{"nome": "Nome Novo", "url": "https://a.org", "tool_id": "nome-antigo"}]
        assign_tool_ids(catalog)
        self.assertEqual(catalog[0]["_tool_id"], "nome-antigo")

    def test_nome_repetido_desambigua(self):
        """Nome repetido continua recebendo sufixo quando não há identificador gravado."""
        catalog = [
            {"nome": "Igual", "url": "https://a.org"},
            {"nome": "Igual", "url": "https://b.org"},
        ]
        colisoes = assign_tool_ids(catalog)
        self.assertEqual([c["_tool_id"] for c in catalog], ["igual", "igual-2"])
        self.assertEqual(len(colisoes), 1)

    def test_novo_tool_id_desvia_de_ocupado(self):
        """Nome que colidiria com identificador existente recebe sufixo livre."""
        catalog = [{"nome": "Mapa", "url": "https://a.org", "tool_id": "mapa"}]
        self.assertEqual(new_tool_id("Mapa", catalog), "mapa-2")

    def test_ids_em_uso_nao_altera_catalogo(self):
        """A simulação de identificadores não deixa resíduo no catálogo real."""
        catalog = [{"nome": "A", "url": "https://a.org"}]
        ids_in_use(catalog)
        self.assertNotIn("_tool_id", catalog[0])


class TestComandoAdicionar(unittest.TestCase):
    """Verifica o subcomando de inclusão ponta a ponta em arquivos temporários."""

    def _preparar(self, tmp):
        """Cria catálogo vazio e registro de retiradas vazio.

        Args:
            tmp: Diretório temporário.

        Returns:
            Par com os caminhos do catálogo e do registro.
        """
        catalog = tmp / "catalogo.json"
        catalog.write_text("[]", encoding="utf-8")
        registry = tmp / "retiradas.json"
        registry.write_text('{"versao": 1, "eventos": []}', encoding="utf-8")
        return catalog, registry

    def test_inclui_com_identificador_e_data(self):
        """A entrada gravada traz tool_id congelado, domínio derivado e data de inclusão."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            codigo = _executar(catalog, registry, url="https://www.exemplo.org/painel")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(len(gravado), 1)
        entry = gravado[0]
        self.assertEqual(entry["tool_id"], "iniciativa-nova")
        self.assertEqual(entry["dominio"], "www.exemplo.org")
        self.assertEqual(entry["categoria"], "Dados e Mapas")
        self.assertIn("adicionado_em", entry)

    def test_endereco_repetido_e_recusado(self):
        """Incluir duas vezes o mesmo endereço é recusado, mesmo com outra grafia."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            primeiro = _executar(catalog, registry)
            segundo = _executar(catalog, registry, url="http://www.novo-exemplo.org/")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(primeiro, 0)
        self.assertEqual(segundo, 1)
        self.assertEqual(len(gravado), 1)

    def test_endereco_retirado_e_recusado(self):
        """Readmitir é outro comando; a inclusão não atropela o registro de retiradas."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            registry.write_text(
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
            codigo = _executar(catalog, registry, url="https://novo-exemplo.org")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_host_que_proibe_a_leitura_e_recusado(self):
        """Incluir host com Disallow: / criaria entrada que nunca recebe laudo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            proibido = RobotsDecision(
                False, "proibido", "robots.txt proíbe este endereço para todos os agentes"
            )
            with patch("observatorio.cli.RobotsPolicy") as politica:
                politica.return_value.decide.return_value = proibido
                codigo = _executar(catalog, registry, ignorar_robots=False)
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_host_permissivo_permite_inclusao(self):
        """A consulta a robots.txt não bloqueia quando o host permite."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            permitido = RobotsDecision(True, "permitido", "regras do host permitem a leitura")
            with patch("observatorio.cli.RobotsPolicy") as politica:
                politica.return_value.decide.return_value = permitido
                codigo = _executar(catalog, registry, ignorar_robots=False)
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(len(gravado), 1)

    def test_descricao_com_dado_pessoal_e_recusada(self):
        """Descrição é publicada no catálogo; dado pessoal é recusado."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            contato = "@".join(["maria.souza", "gmail" + ".com"])
            codigo = _executar(catalog, registry, descricao=f"Painel mantido por {contato} em 2026.")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 1)
        self.assertEqual(gravado, [])

    def test_tool_id_explicito_e_respeitado(self):
        """O operador pode fixar um identificador próprio, desde que seja slug livre."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            codigo = _executar(catalog, registry, tool_id="id-escolhido")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(gravado[0]["tool_id"], "id-escolhido")

    def test_fragmento_e_removido_ao_incluir(self):
        """O endereço gravado não carrega âncora de página."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._preparar(tmp)
            _executar(catalog, registry, url="https://exemplo.org/painel#secao")
            gravado = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(gravado[0]["url"], "https://exemplo.org/painel")
        self.assertEqual(strip_fragment("https://a.org/#x"), "https://a.org/")

    def test_categoria_obrigatoria_no_parser(self):
        """As categorias aceitas são as do catálogo, não texto livre."""
        self.assertIn("Dados e Mapas", CATEGORIES)
        self.assertEqual(len(CATEGORIES), 7)


class TestCatalogoVersionado(unittest.TestCase):
    """Verifica o catálogo real do repositório."""

    def test_catalogo_real_passa_no_portao(self):
        """O catálogo versionado precisa estar válido para o ciclo rodar."""
        catalog = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        self.assertEqual(validate_catalog(catalog), [])

    def test_todas_as_entradas_tem_identificador(self):
        """Sem identificador gravado, um rename mudaria o endereço do laudo."""
        catalog = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        sem_id = [item["nome"] for item in catalog if not item.get("tool_id")]
        self.assertEqual(sem_id, [])

    def test_identificadores_sao_unicos(self):
        """Identificador repetido sobrescreveria laudo no acervo publicado."""
        catalog = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
        ids = [item["tool_id"] for item in catalog]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
