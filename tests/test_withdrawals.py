"""Testes do registro de retiradas e readmissões.

Cobrem a normalização de endereços, a gravação dos eventos, o efeito no catálogo, o comportamento
do motor diante de uma iniciativa retirada e o retorno da iniciativa por readmissão.
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from pydantic import ValidationError

from observatorio.cli import build_parser, cmd_readmit, cmd_withdraw
from observatorio.engine import run_batch_audit
from observatorio.fetch import FetchResult
from observatorio.models import EventType, WithdrawalEvent, WithdrawalRegistry
from observatorio.withdrawals import (
    is_withdrawn,
    load,
    original_entry,
    register_readmission,
    register_withdrawal,
    remove_from_catalog,
    save,
    url_key,
    withdrawn_keys,
)

RAIZ = Path(__file__).resolve().parent.parent


def _provedor():
    """Monta um provedor de aquisição que devolve HTML fixo.

    Returns:
        Dublê de `ContentFetcher` com resposta de 200 e corpo mínimo.
    """
    provedor = MagicMock()
    provedor.fetch.side_effect = lambda url: FetchResult(
        url=url,
        provider="http",
        html="<html><body><p>Conteúdo público.</p></body></html>",
        status_code=200,
        latency_ms=100,
        content_bytes=60,
        declared_bytes=60,
    )
    return provedor


def motivo_com_dado_pessoal() -> str:
    """Monta um motivo com CPF e e-mail comercial sem gravar o literal no arquivo.

    O portão de privacidade varre o próprio repositório; um CPF válido escrito aqui seria
    corretamente bloqueado como violação. As partes são reunidas em tempo de execução.

    Returns:
        Texto de motivo que o portão deve recusar.
    """
    cpf = ".".join(["529", "982", "247", "25"])
    email = "@".join(["maria.souza", "gmail" + ".com"])
    return f"Pedido de Maria Souza, {email}, CPF {cpf}."


def _gravar_catalogo(path, registros):
    """Escreve um catálogo de teste em disco.

    Args:
        path: Arquivo JSON de destino.
        registros: Lista de registros do catálogo.
    """
    path.write_text(json.dumps(registros, ensure_ascii=False), encoding="utf-8")


def _executar(
    comando, catalog, registry, url, motivo="Pedido do operador do site.", solicitado_em="2026-10-24"
):
    """Executa um subcomando de registro sobre arquivos de teste.

    Os argumentos passam pelo parser real, e não por `Namespace` montado à mão: assim um destino
    de argumento renomeado quebra o teste em vez de quebrar a esteira em produção.

    Args:
        comando: Função do subcomando (`cmd_withdraw` ou `cmd_readmit`).
        catalog: Caminho do catálogo.
        registry: Caminho do registro.
        url: Endereço alvo.
        motivo: Motivo informado.
        solicitado_em: Data do pedido no formato AAAA-MM-DD.

    Returns:
        Código de saída do subcomando.
    """
    subcomando = "retirar" if comando is cmd_withdraw else "readmitir"
    argv = [
        subcomando,
        "--url",
        url,
        "--motivo",
        motivo,
        "--solicitado-em",
        solicitado_em,
        "--catalogo",
        str(catalog),
        "--registro",
        str(registry),
    ]
    return comando(build_parser().parse_args(argv))


class TestChaveDeEndereco(unittest.TestCase):
    """Verifica a forma comparável usada para casar catálogo e registro."""

    def test_ignora_esquema_www_e_barra_final(self):
        """As quatro grafias do mesmo site devem produzir a mesma chave."""
        variantes = [
            "https://exemplo.org",
            "http://exemplo.org/",
            "https://www.exemplo.org",
            "HTTPS://WWW.Exemplo.org/",
        ]
        self.assertEqual({url_key(v) for v in variantes}, {"exemplo.org"})

    def test_preserva_caminho(self):
        """Caminhos distintos identificam páginas distintas."""
        self.assertNotEqual(url_key("https://exemplo.org/a"), url_key("https://exemplo.org/b"))

    def test_caminho_em_caixa_baixa(self):
        """Caixa no caminho não separa dois endereços equivalentes."""
        self.assertEqual(url_key("https://exemplo.org/Painel"), url_key("https://exemplo.org/painel"))


class TestRegistro(unittest.TestCase):
    """Verifica leitura, escrita e acréscimo de eventos no registro."""

    def test_arquivo_ausente_devolve_registro_vazio(self):
        """Sem arquivo, não há evento registrado."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = load(Path(tmp) / "nao_existe.json")
        self.assertEqual(registry.eventos, [])
        self.assertEqual(registry.versao, 1)

    def test_salvar_e_carregar_preserva_motivo(self):
        """O motivo informado deve sobreviver ao ciclo de gravação e leitura."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "retiradas.json"
            registry = WithdrawalRegistry()
            register_withdrawal(
                registry,
                nome="Iniciativa Exemplo",
                url="https://exemplo.org",
                motivo="Pedido do operador, por e-mail, em nome do coletivo.",
                solicitado_em=date(2026, 10, 24),
                canal="email",
            )
            save(registry, path)
            recarregado = load(path)
        self.assertEqual(len(recarregado.eventos), 1)
        esperado = "Pedido do operador, por e-mail, em nome do coletivo."
        self.assertEqual(recarregado.eventos[0].motivo, esperado)
        self.assertEqual(recarregado.eventos[0].solicitado_em, date(2026, 10, 24))
        self.assertEqual(recarregado.eventos[0].tipo, EventType.WITHDRAWAL)
        self.assertIn("exemplo.org", withdrawn_keys(recarregado))

    def test_retirada_duplicada_e_recusada(self):
        """O mesmo endereço não pode ser retirado duas vezes seguidas."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "motivo", date(2026, 10, 1))
        with self.assertRaises(ValueError):
            register_withdrawal(registry, "A", "http://www.exemplo.org/", "outro motivo", date(2026, 10, 2))

    def test_motivo_curto_e_recusado(self):
        """Motivo vazio ou trivial não passa na validação do modelo."""
        with self.assertRaises(ValidationError):
            WithdrawalEvent(
                tipo=EventType.WITHDRAWAL,
                nome="A",
                url="https://exemplo.org",
                motivo="",
                solicitado_em=date(2026, 10, 1),
            )

    def test_esta_retirada_reconhece_variantes(self):
        """A consulta aceita o endereço grafado de outro jeito."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "motivo", date(2026, 10, 1))
        self.assertTrue(is_withdrawn("http://www.exemplo.org/", registry))

    def test_remover_do_catalogo_por_variante(self):
        """A remoção do catálogo casa pelo endereço normalizado."""
        catalog = [
            {"nome": "Fica", "url": "https://fica.org"},
            {"nome": "Sai", "url": "https://sai.org"},
        ]
        restante = remove_from_catalog(catalog, "http://www.sai.org/")
        self.assertEqual([i["nome"] for i in restante], ["Fica"])


class TestReadmissao(unittest.TestCase):
    """Verifica o evento de volta e seu efeito no estado vigente."""

    def test_readmissao_reverte_o_estado(self):
        """Depois da readmissão, o endereço deixa de constar como retirado."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "saiu", date(2026, 10, 1))
        register_readmission(registry, "https://exemplo.org", "voltou", date(2026, 10, 20))
        self.assertFalse(is_withdrawn("https://exemplo.org", registry))
        self.assertEqual(registry.eventos[-1].tipo, EventType.READMISSION)

    def test_historico_preserva_os_dois_motivos(self):
        """A retirada não é apagada para registrar a volta."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "pedido de saída", date(2026, 10, 1))
        register_readmission(registry, "https://exemplo.org", "pedido de volta", date(2026, 10, 20))
        motivos = [(e.tipo.value, e.motivo) for e in registry.eventos]
        self.assertEqual(
            motivos,
            [("retirada", "pedido de saída"), ("readmissao", "pedido de volta")],
        )

    def test_readmissao_sem_retirada_falha(self):
        """Não se readmite endereço que nunca saiu."""
        registry = WithdrawalRegistry()
        with self.assertRaises(ValueError):
            register_readmission(registry, "https://exemplo.org", "motivo", date(2026, 10, 1))

    def test_ciclo_seguinte_registra_novo_evento(self):
        """Retirar de novo depois da volta acrescenta um terceiro evento."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "primeira saída", date(2026, 10, 1))
        register_readmission(registry, "https://exemplo.org", "volta", date(2026, 10, 5))
        register_withdrawal(registry, "A", "https://exemplo.org", "segunda saída", date(2026, 10, 9))
        self.assertTrue(is_withdrawn("https://exemplo.org", registry))
        self.assertEqual(len(registry.eventos), 3)

    def test_entrada_original_recuperada(self):
        """A entrada guardada na retirada permite reconstruir o catálogo."""
        registry = WithdrawalRegistry()
        entry = {"nome": "A", "url": "https://exemplo.org", "categoria": "Dados"}
        register_withdrawal(registry, "A", "https://exemplo.org", "saiu", date(2026, 10, 1), entry=entry)
        self.assertEqual(original_entry(registry, "https://exemplo.org"), entry)

    def test_sem_entrada_original_devolve_nulo(self):
        """Registro antigo sem a cópia do catálogo não inventa a entrada."""
        registry = WithdrawalRegistry()
        register_withdrawal(registry, "A", "https://exemplo.org", "saiu", date(2026, 10, 1))
        self.assertIsNone(original_entry(registry, "https://exemplo.org"))


class TestMotorRespeitaRetirada(unittest.TestCase):
    """Verifica que uma iniciativa retirada não recebe laudo."""

    def test_retirada_ausente_do_catalogo_nao_e_auditada(self):
        """Ainda que a entrada permaneça no catálogo, o endereço retirado é ignorado."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / "catalogo.json"
            _gravar_catalogo(
                catalog,
                [
                    {"nome": "Retirada", "url": "https://retirada.org"},
                    {"nome": "Ativa", "url": "https://ativa.org"},
                ],
            )
            registry = WithdrawalRegistry()
            register_withdrawal(
                registry, "Retirada", "https://retirada.org", "pedido do coletivo", date(2026, 10, 1)
            )
            registry_path = tmp / "retiradas.json"
            save(registry, registry_path)

            provedor = _provedor()
            relatorios = run_batch_audit(
                input_file=catalog,
                output_file=tmp / "laudo.json",
                manifest_file=tmp / "estado.json",
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=provedor,
                respect_robots=False,
                withdrawals_file=registry_path,
                cache_dir=tmp / "cache",
            )

        self.assertEqual([r.tool_id for r in relatorios], ["ativa"])
        provedor.fetch.assert_called_once()

    def test_readmitida_volta_a_ser_auditada(self):
        """Depois da readmissão, a iniciativa é auditada de novo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / "catalogo.json"
            _gravar_catalogo(catalog, [{"nome": "Volta", "url": "https://volta.org"}])
            registry = WithdrawalRegistry()
            register_withdrawal(registry, "Volta", "https://volta.org", "saiu", date(2026, 10, 1))
            register_readmission(registry, "https://volta.org", "voltou", date(2026, 10, 5))
            registry_path = tmp / "retiradas.json"
            save(registry, registry_path)

            relatorios = run_batch_audit(
                input_file=catalog,
                output_file=tmp / "laudo.json",
                manifest_file=tmp / "estado.json",
                skip_archive=True,
                refresh_cache=True,
                max_workers=1,
                fetcher=_provedor(),
                respect_robots=False,
                withdrawals_file=registry_path,
                cache_dir=tmp / "cache",
            )

        self.assertEqual([r.tool_id for r in relatorios], ["volta"])


class TestComandoRetirar(unittest.TestCase):
    """Verifica o subcomando que registra o motivo e atualiza o catálogo."""

    def _preparar(self, tmp):
        """Cria catálogo e caminho de registro dentro do diretório temporário.

        Args:
            tmp: Diretório temporário.

        Returns:
            Par com os caminhos do catálogo e do registro.
        """
        catalog = tmp / "catalogo.json"
        _gravar_catalogo(catalog, [{"nome": "Iniciativa Exemplo", "url": "https://exemplo.org"}])
        return catalog, tmp / "retiradas.json"

    def test_registra_motivo_e_remove_do_catalogo(self):
        """O comando grava o motivo e esvazia a entrada do catálogo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            codigo = _executar(cmd_withdraw, catalog, registro_path, "https://exemplo.org")
            registry = load(registro_path)
            restante = json.loads(catalog.read_text(encoding="utf-8"))

        self.assertEqual(codigo, 0)
        self.assertEqual(len(registry.eventos), 1)
        self.assertEqual(registry.eventos[0].motivo, "Pedido do operador do site.")
        self.assertEqual(registry.eventos[0].solicitado_em, date(2026, 10, 24))
        self.assertEqual(restante, [])

    def test_entrada_original_fica_guardada(self):
        """A retirada guarda a entrada do catálogo para permitir a volta."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            _executar(cmd_withdraw, catalog, registro_path, "https://exemplo.org")
            registry = load(registro_path)
        guardada = registry.eventos[0].original_entry or {}
        self.assertEqual(guardada.get("url"), "https://exemplo.org")

    def test_endereco_ausente_nao_registra(self):
        """Retirar endereço que não está no catálogo falha sem gravar nada."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            codigo = _executar(cmd_withdraw, catalog, registro_path, "https://outro.org")
            registry = load(registro_path)
        self.assertEqual(codigo, 1)
        self.assertEqual(registry.eventos, [])

    def test_segunda_tentativa_informa_duplicidade(self):
        """Repetir a retirada deve dizer que já está registrada, não que sumiu do catálogo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            primeiro = _executar(cmd_withdraw, catalog, registro_path, "https://exemplo.org")
            segundo = _executar(
                cmd_withdraw, catalog, registro_path, "https://exemplo.org", motivo="repetido"
            )
            registry = load(registro_path)
        self.assertEqual(primeiro, 0)
        self.assertEqual(segundo, 1)
        self.assertEqual(len(registry.eventos), 1)

    def test_motivo_com_dado_pessoal_e_recusado(self):
        """O motivo é publicado; dado pessoal de pessoa física não pode ser gravado nele."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            codigo = _executar(
                cmd_withdraw, catalog, registro_path, "https://exemplo.org", motivo=motivo_com_dado_pessoal()
            )
            registry = load(registro_path)
            restante = json.loads(catalog.read_text(encoding="utf-8"))
        self.assertEqual(codigo, 1)
        self.assertEqual(registry.eventos, [])
        self.assertEqual(len(restante), 1)

    def test_data_invalida_nao_registra(self):
        """Data malformada é recusada antes de qualquer gravação."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registro_path = self._preparar(tmp)
            codigo = _executar(
                cmd_withdraw,
                catalog,
                registro_path,
                "https://exemplo.org",
                motivo="motivo qualquer",
                solicitado_em="24/10/2026",
            )
            registry = load(registro_path)
            restante = json.loads(catalog.read_text(encoding="utf-8"))
        self.assertEqual(codigo, 1)
        self.assertEqual(registry.eventos, [])
        self.assertEqual(len(restante), 1)


class TestComandoReadmitir(unittest.TestCase):
    """Verifica o subcomando que registra a volta e restaura a entrada."""

    def _retirar_primeiro(self, tmp):
        """Prepara catálogo com uma iniciativa e a retira.

        Args:
            tmp: Diretório temporário.

        Returns:
            Par com os caminhos do catálogo e do registro, já com a retirada feita.
        """
        catalog = tmp / "catalogo.json"
        _gravar_catalogo(
            catalog,
            [{"nome": "Iniciativa Exemplo", "url": "https://exemplo.org", "categoria": "Dados"}],
        )
        registry = tmp / "retiradas.json"
        _executar(cmd_withdraw, catalog, registry, "https://exemplo.org")
        return catalog, registry

    def test_devolve_entrada_ao_catalogo(self):
        """A readmissão restaura o registro guardado, com categoria e demais campos."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._retirar_primeiro(tmp)
            codigo = _executar(
                cmd_readmit,
                catalog,
                registry,
                "https://exemplo.org",
                motivo="O coletivo pediu a volta da ferramenta.",
            )
            restante = json.loads(catalog.read_text(encoding="utf-8"))
            eventos = load(registry).eventos

        self.assertEqual(codigo, 0)
        self.assertEqual(len(restante), 1)
        self.assertEqual(restante[0]["categoria"], "Dados")
        self.assertEqual([e.tipo for e in eventos], [EventType.WITHDRAWAL, EventType.READMISSION])
        self.assertEqual(eventos[1].motivo, "O coletivo pediu a volta da ferramenta.")

    def test_readmitir_endereco_ativo_falha(self):
        """Readmitir o que nunca saiu é recusado."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / "catalogo.json"
            _gravar_catalogo(catalog, [{"nome": "Ativa", "url": "https://ativa.org"}])
            codigo = _executar(cmd_readmit, catalog, tmp / "retiradas.json", "https://ativa.org")
            restante = json.loads(catalog.read_text(encoding="utf-8"))
        self.assertEqual(codigo, 1)
        self.assertEqual(len(restante), 1)

    def test_readmissao_duplicada_nao_duplica_entrada(self):
        """A segunda readmissão do mesmo endereço falha e não duplica o catálogo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._retirar_primeiro(tmp)
            primeiro = _executar(cmd_readmit, catalog, registry, "https://exemplo.org")
            segundo = _executar(cmd_readmit, catalog, registry, "https://exemplo.org")
            restante = json.loads(catalog.read_text(encoding="utf-8"))
            eventos = load(registry).eventos
        self.assertEqual(primeiro, 0)
        self.assertEqual(segundo, 1)
        self.assertEqual(len(restante), 1)
        self.assertEqual(len(eventos), 2)

    def test_motivo_com_dado_pessoal_e_recusado(self):
        """O motivo da volta também é publicado e passa pelo portão."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog, registry = self._retirar_primeiro(tmp)
            codigo = _executar(
                cmd_readmit,
                catalog,
                registry,
                "https://exemplo.org",
                motivo=motivo_com_dado_pessoal(),
            )
            restante = json.loads(catalog.read_text(encoding="utf-8"))
            eventos = load(registry).eventos
        self.assertEqual(codigo, 1)
        self.assertEqual(restante, [])
        self.assertEqual(len(eventos), 1)


class TestDadosDoRepositorio(unittest.TestCase):
    """Verifica a consistência entre o catálogo e o registro versionados."""

    def test_nenhuma_retirada_segue_no_catalogo(self):
        """Endereço retirado não pode continuar sendo auditado pelo catálogo real."""
        catalog = json.loads((RAIZ / "data" / "data_iniciativas.json").read_text(encoding="utf-8"))
        registry = load(RAIZ / "data" / "retiradas.json")
        presentes = [i["url"] for i in catalog if is_withdrawn(i["url"], registry)]
        self.assertEqual(presentes, [])

    def test_registro_versionado_valida(self):
        """O arquivo versionado deve corresponder ao esquema declarado."""
        path = RAIZ / "data" / "retiradas.json"
        self.assertTrue(path.is_file(), "data/retiradas.json deve existir e ser versionado")
        registry = load(path)
        self.assertEqual(registry.versao, 1)
        self.assertIsInstance(registry.eventos, list)


if __name__ == "__main__":
    unittest.main()
