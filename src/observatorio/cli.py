"""Interface de linha de comando do Observatório TecnoPopular.

Ponto de entrada canônico para automação em CI/CD e execução local:

    uv run python -m observatorio.cli audit-all
    uv run python -m observatorio.cli verify-privacy --all-files
    uv run python -m observatorio.cli retirar --url URL --motivo "texto do pedido"
    uv run python -m observatorio.cli readmitir --url URL --motivo "texto do pedido"
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from .catalog import (
    CATEGORIAS_CONHECIDAS,
    append_entry,
    extract_domain,
    new_tool_id,
    normalize_url,
    strip_fragment,
    validate_entry,
)
from .engine import generate_radar_artifacts, run_batch_audit, run_preservation_pass
from .fetch import probe_https
from .privacy_guard import CivicPrivacyGuard, ViolationSeverity
from .robots import RobotsPolicy
from .withdrawals import (
    is_withdrawn,
    load,
    original_entry,
    register_readmission,
    register_withdrawal,
    remove_from_catalog,
    restore_to_catalog,
    save,
    url_key,
)


def cmd_audit_all(args: argparse.Namespace) -> int:
    """Executa a varredura e avaliação em lote das iniciativas cívicas.

    Args:
        args: Namespace do argparse com `input`, `output`, `manifest`, `workers`, `refresh`,
            `skip_archive`, `archive_budget`, `archive_limit`, `timeout`, `semantic`,
            `max_bytes` e `no_radar`.

    Returns:
        0 quando a esteira conclui; 1 quando uma exceção interrompe a execução.
    """
    input_file = Path(args.input)
    output_file = Path(args.output)
    no_radar = getattr(args, "no_radar", False)

    try:
        run_batch_audit(
            input_file=input_file,
            output_file=output_file,
            max_workers=args.workers,
            refresh_cache=args.refresh,
            skip_archive=args.skip_archive,
            timeout=args.timeout,
            manifest_file=Path(args.manifest),
            semantic=args.semantic,
            max_bytes=args.max_bytes,
            withdrawals_file=Path(args.retiradas),
            archive_budget=args.archive_budget,
            archive_limit=args.archive_limit,
            generate_radar=not no_radar,
        )
        return 0
    except Exception as e:
        print(f"[-] Erro fatal na execução da esteira: {e}", file=sys.stderr)
        return 1


def cmd_gerar_radar(args: argparse.Namespace) -> int:
    """Gera os artefatos de telemetria e radar da infraestrutura cívica.

    Args:
        args: Namespace do argparse com `input`, `audits`, `radar`, `telemetry` e `cache_dir`.

    Returns:
        0 em caso de sucesso; 1 em caso de erro.
    """
    try:
        generate_radar_artifacts(
            input_file=Path(args.input),
            audits_file=Path(args.audits),
            radar_file=Path(args.radar),
            telemetry_file=Path(args.telemetry),
            cache_dir=Path(args.cache_dir),
        )
        print(f"[+] Radar da infraestrutura cívica salvo em: {args.radar}")
        print(f"[+] Telemetria consolidada salva em: {args.telemetry}")
        return 0
    except Exception as e:
        print(f"[-] Erro ao compilar radar: {e}", file=sys.stderr)
        return 1


def cmd_verify_privacy(args: argparse.Namespace) -> int:
    """Executa a verificação prévia do portão de privacidade (LGPD).

    Sem `--staged` a varredura percorre a árvore do diretório atual e ignora `.git`, `.venv`,
    `data/raw` e `__pycache__`, para não inspecionar cache de rede nem dependências instaladas.

    Args:
        args: Namespace do argparse com a flag `staged`.

    Returns:
        0 quando não há violação impeditiva; 2 quando há ao menos um bloqueador.
    """
    guard = CivicPrivacyGuard()
    violations = []
    root = Path.cwd()

    print("[*] Iniciando Portão de Privacidade do Observatório TecnoPopular...")

    if args.staged:
        violations = guard.scan_git_staged_files()
    else:
        files = []
        for p in root.rglob("*"):
            if (
                p.is_file()
                and not any(part.startswith(".") for part in p.parts)
                and "data/raw" not in str(p)
                and ".venv" not in str(p)
                and "__pycache__" not in str(p)
            ):
                files.append(p)

        for f in files:
            violations.extend(guard.scan_file(f))

    blockers = [v for v in violations if v.severity == ViolationSeverity.BLOCKER]
    warnings = [v for v in violations if v.severity == ViolationSeverity.WARNING]

    if warnings:
        print(f"\n[AVISO] {len(warnings)} aviso(s) de menor gravidade identificado(s):")
        for w in warnings:
            print(f"  {w}")

    if blockers:
        print(f"\n[BLOQUEIO] {len(blockers)} violação(ões) impeditiva(s) detectada(s):", file=sys.stderr)
        for b in blockers:
            print(f"  {b}", file=sys.stderr)
        print(
            "\n[FALHA] Portão de Privacidade reprovado. Sanitize os arquivos antes de prosseguir.",
            file=sys.stderr,
        )
        return 2

    print("[OK] Portão de Privacidade aprovado com sucesso: zero dados pessoais ou segredos identificados.")
    return 0


def cmd_withdraw(args: argparse.Namespace) -> int:
    """Registra uma retirada a pedido e remove a iniciativa do catálogo.

    A ordem importa: o registro é gravado antes da remoção do catálogo. Se a gravação falhar, o
    catálogo segue intacto; se a remoção falhar, existe registro de que a iniciativa não deve mais
    ser auditada, e o motor a ignora mesmo que a entrada permaneça.

    O motivo é publicado junto com o repositório, então passa pelo mesmo portão de privacidade dos
    demais artefatos: texto com dado pessoal de pessoa física é recusado em vez de gravado.

    Args:
        args: Namespace do argparse com `url`, `motivo`, `solicitado_em`, `canal`, `catalogo`
            e `registro`.

    Returns:
        0 quando a retirada é registrada; 1 quando o endereço não consta do catálogo, já consta do
        registro, a data informada é inválida ou o motivo traz dado pessoal que seria publicado.
    """
    catalog_path = Path(args.catalogo)
    registry_path = Path(args.registro)

    if not catalog_path.is_file():
        print(f"[-] Catálogo não encontrado: {catalog_path}", file=sys.stderr)
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    registry = load(registry_path)

    if is_withdrawn(args.url, registry):
        print(f"[-] Endereço já registrado como retirado: {args.url}", file=sys.stderr)
        return 1

    encontrados = [item for item in catalog if url_key(item.get("url", "")) == url_key(args.url)]
    if not encontrados:
        print(f"[-] Endereço não consta do catálogo: {args.url}", file=sys.stderr)
        return 1

    try:
        solicitado_em = (
            date.fromisoformat(args.solicitado_em) if args.solicitado_em else datetime.now(UTC).date()
        )
    except ValueError:
        print(f"[-] Data inválida (use AAAA-MM-DD): {args.solicitado_em}", file=sys.stderr)
        return 1

    achados = CivicPrivacyGuard().scan_text(f"{args.motivo}\n", file_path="motivo da retirada")
    if achados:
        print(
            "[-] O motivo é publicado em data/retiradas.json e traz dado que não pode ser versionado:",
            file=sys.stderr,
        )
        for achado in achados:
            print(f"    {achado.category.value}: {achado.message}", file=sys.stderr)
        print(
            "[*] Registre a substância do pedido, sem nome, e-mail, telefone ou documento de pessoa física.",
            file=sys.stderr,
        )
        return 1

    event = register_withdrawal(
        registry,
        encontrados[0]["nome"],
        args.url,
        args.motivo,
        solicitado_em,
        args.canal,
        entry=encontrados[0],
    )
    save(registry, registry_path)

    restantes = remove_from_catalog(catalog, args.url)
    conteudo = json.dumps(restantes, ensure_ascii=False, indent=2)
    catalog_path.write_text(conteudo + "\n", encoding="utf-8")

    print(f"[+] Retirada registrada em {registry_path}: {event.nome}")
    print(f"    motivo informado: {event.motivo[:80]}")
    print(f"    solicitado em {event.solicitado_em} | registrado em {event.registrado_em}")
    print(f"[+] Catálogo atualizado: {len(catalog)} -> {len(restantes)} iniciativas")
    print("[*] O manifesto de estado deixa de citar a iniciativa no próximo ciclo.")
    return 0


def cmd_readmit(args: argparse.Namespace) -> int:
    """Registra a readmissão de uma iniciativa retirada e a devolve ao catálogo.

    A readmissão é um evento novo, não a edição do pedido anterior: o histórico de saída e volta
    fica completo, e ambos os motivos permanecem públicos. A entrada devolvida ao catálogo é a
    cópia guardada no momento da retirada, para não depender de o operador reconstruir categoria
    e descrição de memória.

    Args:
        args: Namespace do argparse com `url`, `motivo`, `solicitado_em`, `canal`, `catalogo`
            e `registro`.

    Returns:
        0 quando a readmissão é registrada; 1 quando o endereço não está retirado, já consta do
        catálogo, a retirada não guardou a entrada original, a data é inválida ou o motivo traz
        dado pessoal que seria publicado.
    """
    catalog_path = Path(args.catalogo)
    registry_path = Path(args.registro)

    if not catalog_path.is_file():
        print(f"[-] Catálogo não encontrado: {catalog_path}", file=sys.stderr)
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    registry = load(registry_path)

    if not is_withdrawn(args.url, registry):
        print(f"[-] Endereço não está retirado no registro: {args.url}", file=sys.stderr)
        return 1
    if any(url_key(item.get("url", "")) == url_key(args.url) for item in catalog):
        print(f"[-] Endereço já consta do catálogo: {args.url}", file=sys.stderr)
        return 1

    entry = original_entry(registry, args.url)
    if entry is None:
        print(f"[-] A retirada de {args.url} não guardou a entrada original do catálogo.", file=sys.stderr)
        print(
            "[*] Reconstrua a entrada em data/data_iniciativas.json e registre a readmissão à mão.",
            file=sys.stderr,
        )
        return 1

    try:
        solicitado_em = (
            date.fromisoformat(args.solicitado_em) if args.solicitado_em else datetime.now(UTC).date()
        )
    except ValueError:
        print(f"[-] Data inválida (use AAAA-MM-DD): {args.solicitado_em}", file=sys.stderr)
        return 1

    achados = CivicPrivacyGuard().scan_text(f"{args.motivo}\n", file_path="motivo da readmissão")
    if achados:
        print(
            "[-] O motivo é publicado em data/retiradas.json e traz dado que não pode ser versionado:",
            file=sys.stderr,
        )
        for achado in achados:
            print(f"    {achado.category.value}: {achado.message}", file=sys.stderr)
        print(
            "[*] Registre a substância do pedido, sem nome, e-mail, telefone ou documento de pessoa física.",
            file=sys.stderr,
        )
        return 1

    evento = register_readmission(registry, args.url, args.motivo, solicitado_em, args.canal)
    save(registry, registry_path)

    atualizado = restore_to_catalog(catalog, entry)
    conteudo = json.dumps(atualizado, ensure_ascii=False, indent=2)
    catalog_path.write_text(conteudo + "\n", encoding="utf-8")

    print(f"[+] Readmissão registrada em {registry_path}: {evento.nome}")
    print(f"    motivo informado: {evento.motivo[:80]}")
    print(f"    solicitado em {evento.solicitado_em} | registrado em {evento.registrado_em}")
    print(f"[+] Catálogo atualizado: {len(catalog)} -> {len(atualizado)} iniciativas")
    print("[*] A iniciativa volta a ser auditada no próximo ciclo.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Inclui uma iniciativa no catálogo com identificador congelado.

    O `tool_id` é gravado na entrada e não muda depois: ele é o endereço público do laudo, e
    derivá-lo do nome a cada ciclo faria uma simples correção de nome publicar o laudo seguinte
    em outro endereço. A entrada também guarda a data de inclusão, para que o acervo saiba quando
    cada iniciativa passou a ser acompanhada.

    Três verificações antecedem a gravação: o endereço não pode constar do catálogo nem do
    registro de retiradas; o `robots.txt` do host precisa permitir a leitura, senão a iniciativa
    seria incluída para nunca receber laudo; e nome e descrição passam pelo portão de privacidade,
    porque são publicados.

    Args:
        args: Namespace do argparse com `url`, `nome`, `categoria`, `descricao`, `repositorio`,
            `fontes_primarias`, `tool_id`, `ignorar_robots` e `catalogo`.

    Returns:
        0 quando a iniciativa é incluída; 1 quando o endereço já existe, o host proíbe a leitura,
        a entrada é inválida ou o texto traz dado pessoal que seria publicado.
    """
    catalog_path = Path(args.catalogo)
    if not catalog_path.is_file():
        print(f"[-] Catálogo não encontrado: {catalog_path}", file=sys.stderr)
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    normalizada, problema = normalize_url(args.url)
    if problema:
        print(f"[-] Endereço recusado: {problema}", file=sys.stderr)
        return 1
    if not args.url.strip().lower().startswith("https://"):
        respondeu, motivo = probe_https(normalizada)
        if not respondeu:
            print(f"[-] Esquema assumido não respondeu: {normalizada} ({motivo})", file=sys.stderr)
            print(
                "[*] O endereço chegou sem esquema e este host não atende em https. "
                "Confira o endereço antes de incluir.",
                file=sys.stderr,
            )
            return 1
        print(f"[*] Esquema ausente: assumindo {normalizada} ({motivo})")

    url = strip_fragment(normalizada)
    alvo = url_key(url)

    if any(url_key(str(item.get("url") or "")) == alvo for item in catalog):
        print(f"[-] Endereço já consta do catálogo: {url}", file=sys.stderr)
        return 1

    registry = load(Path(args.registro))
    if is_withdrawn(url, registry):
        print(f"[-] Endereço consta do registro como retirado: {url}", file=sys.stderr)
        print("[*] Use o subcomando readmitir para devolvê-lo ao catálogo.", file=sys.stderr)
        return 1

    if not args.ignorar_robots:
        decisao = RobotsPolicy().decide(url)
        print(f"[*] robots.txt: {decisao.reason}")
        if not decisao.allowed:
            print(f"[-] O host não permite a leitura deste endereço: {url}", file=sys.stderr)
            print("[*] Incluir assim produziria uma entrada que nunca recebe laudo.", file=sys.stderr)
            print("[*] Para incluir de qualquer modo, repita com --ignorar-robots.", file=sys.stderr)
            return 1

    achados = CivicPrivacyGuard().scan_text(
        f"{args.nome}\n{args.descricao or ''}\n", file_path="entrada do catálogo"
    )
    if achados:
        print("[-] O catálogo é público e este texto traz dado que não pode ser versionado:", file=sys.stderr)
        for achado in achados:
            print(f"    {achado.category.value}: {achado.message}", file=sys.stderr)
        print(
            "[*] Descreva a ferramenta sem nome, e-mail, telefone ou documento de pessoa física.",
            file=sys.stderr,
        )
        return 1

    entry: dict[str, object] = {
        "nome": args.nome.strip(),
        "tool_id": (args.tool_id or new_tool_id(args.nome, catalog)).strip(),
        "url": url,
        "dominio": extract_domain(url),
        "adicionado_em": datetime.now(UTC).date().isoformat(),
    }
    if args.categoria:
        entry["categoria"] = args.categoria.strip()
    if args.descricao:
        entry["descricao"] = args.descricao.strip()
    if args.repositorio:
        entry["repositorio"] = args.repositorio.strip()
    if args.fontes_primarias:
        fontes = [parte.strip() for parte in args.fontes_primarias.split(",") if parte.strip()]
        if fontes:
            entry["fontes_primarias"] = fontes

    problemas = validate_entry(entry, catalog)
    if problemas:
        print("[-] Entrada recusada:", file=sys.stderr)
        for problema in problemas:
            print(f"    {problema}", file=sys.stderr)
        return 1

    atualizado = append_entry(catalog, entry)
    conteudo = json.dumps(atualizado, ensure_ascii=False, indent=2)
    catalog_path.write_text(conteudo + "\n", encoding="utf-8")

    print(f"[+] Iniciativa incluída: {entry['nome']}")
    print(f"    tool_id congelado: {entry['tool_id']}")
    print(f"    laudo público em: auditoria/{entry['tool_id']}.html")
    print(f"[+] Catálogo atualizado: {len(catalog)} -> {len(atualizado)} iniciativas")
    print("[*] A iniciativa entra no próximo ciclo de auditoria; para avaliar já, rode audit-all --refresh.")
    return 0


def cmd_preservar(args: argparse.Namespace) -> int:
    """Executa um passo de preservação no Internet Archive sobre o que o manifesto não tem.

    Não baixa página e não emite laudo: o único artefato que muda é o manifesto de estado. Existe
    separado da auditoria porque o Internet Archive aceita poucas capturas por vez, e amarrar a
    preservação ao ciclo semanal deixaria a fila andando devagar por meses.

    Args:
        args: Namespace do argparse com `input`, `manifest`, `limite` e `orcamento`.

    Returns:
        0 quando o passo conclui; 1 quando uma exceção interrompe a execução.
    """
    try:
        run_preservation_pass(
            input_file=Path(args.input),
            manifest_file=Path(args.manifest),
            limit=args.limite,
            budget=args.orcamento,
        )
        return 0
    except Exception as e:
        print(f"[-] Erro fatal no passo de preservação: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Monta o parser de subcomandos da CLI.

    Existe separado de `main` para que a superfície de argumentos possa ser testada sem executar
    a esteira: um `add_argument` malformado derruba a CLI inteira na primeira chamada.

    Returns:
        Parser pronto, com todos os subcomandos registrados.
    """
    parser = argparse.ArgumentParser(
        prog="observatorio",
        description="Observatório TecnoPopular: Monitoramento, telemetria e diagnóstico contínuo de iniciativas cívicas.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    audit_command_parser = subparsers.add_parser(
        "audit-all", help="Executa a varredura e avaliação em lote das iniciativas"
    )
    audit_command_parser.add_argument(
        "--input", "-i", default="data/data_iniciativas.json", help="Caminho do catálogo base"
    )
    audit_command_parser.add_argument(
        "--output",
        "-o",
        default="dist/auditoria_iniciativas.json",
        help="Caminho do laudo completo (publicação)",
    )
    audit_command_parser.add_argument(
        "--manifest", "-m", default="data/estado_hashes.json", help="Caminho do manifesto compacto de estado"
    )
    audit_command_parser.add_argument(
        "--workers", "-w", type=int, default=10, help="Número de threads simultâneas"
    )
    audit_command_parser.add_argument(
        "--refresh", "-r", action="store_true", help="Força reavaliação ignorando cache"
    )
    audit_command_parser.add_argument(
        "--skip-archive", action="store_true", help="Pula disparos ao Internet Archive"
    )
    audit_command_parser.add_argument(
        "--archive-budget",
        type=float,
        default=60.0,
        help="Segundos que o ciclo espera pelas capturas do Internet Archive antes de marcá-las como pendentes",
    )
    audit_command_parser.add_argument(
        "--archive-limit",
        type=int,
        default=10,
        help="Máximo de capturas submetidas por ciclo; o excedente fica como deferred e volta no ciclo seguinte",
    )
    audit_command_parser.add_argument(
        "--timeout", "-t", type=int, default=8, help="Timeout de conexão em segundos"
    )
    audit_command_parser.add_argument(
        "--max-bytes",
        type=int,
        default=4_000_000,
        help="Limite de leitura por página, em bytes (padrão 4 MB; páginas maiores são declaradas como inspeção parcial)",
    )
    audit_command_parser.add_argument(
        "--semantic",
        choices=["off", "jev"],
        default="off",
        help="Camada semântica calibrada: off (determinístico puro) ou jev (TypeSafe, consome tokens por iniciativa)",
    )

    audit_command_parser.add_argument(
        "--retiradas",
        default="data/retiradas.json",
        help="Registro de retiradas acatadas; endereços ali não são auditados",
    )
    audit_command_parser.add_argument(
        "--no-radar",
        action="store_true",
        help="Desativa a geração automática dos artefatos de radar e telemetria",
    )

    withdraw_command_parser = subparsers.add_parser(
        "retirar", help="Registra retirada a pedido e remove a iniciativa do catálogo"
    )
    withdraw_command_parser.add_argument("--url", required=True, help="Endereço da iniciativa a retirar")
    withdraw_command_parser.add_argument("--motivo", required=True, help="Motivo informado pelo solicitante")
    withdraw_command_parser.add_argument(
        "--solicitado-em", default=None, help="Data do pedido no formato AAAA-MM-DD (padrão: hoje)"
    )
    withdraw_command_parser.add_argument("--canal", default="email", help="Origem do pedido")
    withdraw_command_parser.add_argument(
        "--catalogo", default="data/data_iniciativas.json", help="Catálogo a atualizar"
    )
    withdraw_command_parser.add_argument(
        "--registro", default="data/retiradas.json", help="Registro de retiradas a atualizar"
    )

    add_command_parser = subparsers.add_parser(
        "adicionar", help="Inclui uma iniciativa no catálogo com identificador congelado"
    )
    add_command_parser.add_argument(
        "--url",
        required=True,
        help="Endereço público da aplicação, em https (o esquema é assumido se ausente)",
    )
    add_command_parser.add_argument("--nome", required=True, help="Nome público da iniciativa")
    add_command_parser.add_argument(
        "--categoria",
        default=None,
        help=f"Eixo temático, texto livre; eixos conhecidos hoje: {CATEGORIAS_CONHECIDAS}",
    )
    add_command_parser.add_argument(
        "--descricao", default=None, help="Descrição editorial; não entra na pontuação"
    )
    add_command_parser.add_argument("--repositorio", default=None, help="URL pública do repositório git")
    add_command_parser.add_argument(
        "--fontes-primarias", default=None, help="Órgãos públicos citados, separados por vírgula"
    )
    add_command_parser.add_argument(
        "--tool-id", default=None, help="Identificador explícito (padrão: slug do nome)"
    )
    add_command_parser.add_argument(
        "--ignorar-robots", action="store_true", help="Inclui mesmo quando o host proíbe a leitura"
    )
    add_command_parser.add_argument(
        "--catalogo", default="data/data_iniciativas.json", help="Catálogo a atualizar"
    )
    add_command_parser.add_argument(
        "--registro", default="data/retiradas.json", help="Registro de retiradas consultado"
    )

    readmit_command_parser = subparsers.add_parser(
        "readmitir", help="Registra a volta de uma iniciativa retirada e a devolve ao catálogo"
    )
    readmit_command_parser.add_argument("--url", required=True, help="Endereço da iniciativa a readmitir")
    readmit_command_parser.add_argument("--motivo", required=True, help="Motivo informado pelo solicitante")
    readmit_command_parser.add_argument(
        "--solicitado-em", default=None, help="Data do pedido no formato AAAA-MM-DD (padrão: hoje)"
    )
    readmit_command_parser.add_argument("--canal", default="email", help="Origem do pedido")
    readmit_command_parser.add_argument(
        "--catalogo", default="data/data_iniciativas.json", help="Catálogo a atualizar"
    )
    readmit_command_parser.add_argument(
        "--registro", default="data/retiradas.json", help="Registro de eventos a atualizar"
    )

    preserve_command_parser = subparsers.add_parser(
        "preservar", help="Executa um passo de preservação no Internet Archive sobre o que falta"
    )
    preserve_command_parser.add_argument(
        "--input", "-i", default="data/data_iniciativas.json", help="Caminho do catálogo base"
    )
    preserve_command_parser.add_argument(
        "--manifest", "-m", default="data/estado_hashes.json", help="Caminho do manifesto compacto de estado"
    )
    preserve_command_parser.add_argument(
        "--limite", type=int, default=10, help="Máximo de capturas submetidas nesta execução"
    )
    preserve_command_parser.add_argument(
        "--orcamento", type=float, default=60.0, help="Segundos de espera pelas capturas em andamento"
    )

    radar_command_parser = subparsers.add_parser(
        "gerar-radar", help="Gera os artefatos de telemetria e radar a partir dos laudos e cache"
    )
    radar_command_parser.add_argument(
        "--input", "-i", default="data/data_iniciativas.json", help="Caminho do catálogo base"
    )
    radar_command_parser.add_argument(
        "--audits", "-a", default="dist/auditoria_iniciativas.json", help="Caminho dos laudos consolidados"
    )
    radar_command_parser.add_argument(
        "--radar",
        "-r",
        default="dist/radar_iniciativas.json",
        help="Caminho de saída do radar_iniciativas.json",
    )
    radar_command_parser.add_argument(
        "--telemetry",
        "-t",
        default="dist/telemetria_iniciativas.json",
        help="Caminho de saída do telemetria_iniciativas.json",
    )
    radar_command_parser.add_argument(
        "--cache-dir", default="data/raw/html_cache", help="Diretório com o cache de corpos e metadados"
    )

    privacy_command_parser = subparsers.add_parser(
        "verify-privacy", help="Executa o portão de privacidade (LGPD)"
    )
    privacy_command_parser.add_argument(
        "--staged", action="store_true", help="Inspeciona apenas arquivos no stage do git"
    )
    privacy_command_parser.add_argument(
        "--all-files", action="store_true", help="Inspeciona todos os arquivos rastreados"
    )

    return parser


def main() -> None:
    """Despacha o subcomando escolhido sobre o parser construído.

    Sem subcomando explícito, assume `audit-all` com os mesmos valores padrão das flags.

    Raises:
        SystemExit: com o código de saída devolvido pelo subcomando executado.
    """
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        args.command = "audit-all"
        args.input = "data/data_iniciativas.json"
        args.output = "dist/auditoria_iniciativas.json"
        args.manifest = "data/estado_hashes.json"
        args.workers = 10
        args.refresh = False
        args.skip_archive = False
        args.timeout = 8
        args.semantic = "off"
        args.max_bytes = 4_000_000
        args.retiradas = "data/retiradas.json"

    if args.command == "audit-all":
        sys.exit(cmd_audit_all(args))
    elif args.command == "verify-privacy":
        sys.exit(cmd_verify_privacy(args))
    elif args.command == "adicionar":
        sys.exit(cmd_add(args))
    elif args.command == "retirar":
        sys.exit(cmd_withdraw(args))
    elif args.command == "readmitir":
        sys.exit(cmd_readmit(args))
    elif args.command == "preservar":
        sys.exit(cmd_preservar(args))
    elif args.command == "gerar-radar":
        sys.exit(cmd_gerar_radar(args))


if __name__ == "__main__":
    main()
