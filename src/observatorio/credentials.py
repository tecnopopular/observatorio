"""Gerenciamento de credenciais e chaves de API do Observatório TecnoPopular.

Precedência de carregamento:

    1. Variáveis de ambiente (`os.getenv`), usadas no GitHub Actions e em CI/CD.
    2. macOS Keychain nativo (`security find-generic-password`), usado no desenvolvimento local.
"""

import os
import subprocess


def _consultar(cmd: list[str]) -> str | None:
    """Executa um comando de consulta ao Keychain.

    Ausência do binário `security` não é erro de configuração: significa que a máquina não tem
    Keychain, como em contêiner Linux ou no CI. Nesse caso a resposta é "nenhum segredo aqui", e a
    variável de ambiente continua sendo a via canônica.

    Args:
        cmd: Linha de comando já montada.

    Returns:
        Saída do comando sem espaços nas pontas, ou None quando o comando não existe ou falha.
    """
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError, FileNotFoundError, PermissionError:
        return None
    return res.stdout.strip() or None


def get_keychain_secret(service_or_account: str, account: str | None = None) -> str | None:
    """Recupera um segredo salvo no macOS Keychain via CLI `security`.

    Quando o serviço não corresponde, a busca é repetida tratando o mesmo identificador como
    conta, porque registros antigos foram gravados apenas com `-a`.

    Args:
        service_or_account: Nome do serviço (`-s`) procurado no Keychain.
        account: Conta (`-a`) a restringir na busca; quando nulo, busca apenas pelo serviço.

    Returns:
        Valor do segredo, sem espaços nas pontas, ou None quando nenhuma das buscas encontra,
        quando o Keychain não existe na plataforma ou quando o comando falha.
    """
    cmd = ["security", "find-generic-password", "-w"]
    if account:
        cmd.extend(["-a", account])
    cmd.extend(["-s", service_or_account])

    encontrado = _consultar(cmd)
    if encontrado is not None:
        return encontrado
    return _consultar(["security", "find-generic-password", "-a", service_or_account, "-w"])


def get_api_key(key_name: str) -> str | None:
    """Carrega a chave da variável de ambiente e, se ausente, do macOS Keychain.

    A variável de ambiente tem precedência para que CI/CD nunca dependa do Keychain local.

    Args:
        key_name: Nome da variável de ambiente e do serviço no Keychain.

    Returns:
        Valor da chave, ou None quando não está definida em nenhuma das duas origens.
    """
    env_val = os.getenv(key_name)
    if env_val:
        return env_val
    return get_keychain_secret(key_name)
