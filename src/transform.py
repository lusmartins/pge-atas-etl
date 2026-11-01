"""Validação e limpeza das atas recebidas da API do PNCP."""

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

# datas fora dessa faixa são consideradas erro de digitação (ex.: ano 7203)
ANO_MINIMO = 2000
ANO_MAXIMO = 2100

# campos sem os quais a ata não faz sentido
CAMPOS_OBRIGATORIOS = (
    "numeroControlePNCPAta",
    "numeroControlePNCPCompra",
    "cnpjOrgao",
)


class ErroValidacao(ValueError):
    """Levantado quando uma ata tem um problema que impede aceitá-la."""


def limpar_texto(valor) -> str | None:
    """Tira espaços sobrando e quebras de linha. Texto vazio vira None."""
    if valor is None:
        return None
    texto = re.sub(r"\s+", " ", str(valor)).strip()  # vários espaços viram um só
    return texto or None


def converter_data(valor, nome_campo: str) -> date | None:
    """Converte '2026-10-28' em data. Datas absurdas viram erro."""
    if valor is None:
        return None
    try:
        data = date.fromisoformat(str(valor)[:10])
    except ValueError as erro:
        raise ErroValidacao(f"{nome_campo} inválida: {valor!r}") from erro

    if not ANO_MINIMO <= data.year <= ANO_MAXIMO:
        raise ErroValidacao(f"{nome_campo} fora do intervalo plausível: {valor}")
    return data


def validar_cnpj(valor) -> str:
    """Devolve o CNPJ só com números. Precisa ter exatamente 14 dígitos."""
    numeros = re.sub(r"\D", "", str(valor or ""))  # tira pontos, barras e traços
    if len(numeros) != 14:
        raise ErroValidacao(f"cnpjOrgao inválido: {valor!r}")
    return numeros


def converter_ano(valor) -> int | None:
    """Converte o ano da ata em número inteiro plausível."""
    if valor is None:
        return None
    try:
        ano = int(valor)
    except (TypeError, ValueError) as erro:
        raise ErroValidacao(f"anoAta inválido: {valor!r}") from erro
    if not ANO_MINIMO <= ano <= ANO_MAXIMO:
        raise ErroValidacao(f"anoAta fora do intervalo plausível: {ano}")
    return ano


def conferir_ata(bruta: dict) -> None:
    """Só CONFERE uma ata. Não devolve nada: se houver problema, levanta ErroValidacao."""
    for campo in CAMPOS_OBRIGATORIOS:
        if not limpar_texto(bruta.get(campo)):
            raise ErroValidacao(f"campo obrigatório ausente: {campo}")

    # chamar os conversores aqui faz eles levantarem o erro se algo estiver estranho
    vigencia_inicio = converter_data(bruta.get("vigenciaInicio"), "vigenciaInicio")
    vigencia_fim = converter_data(bruta.get("vigenciaFim"), "vigenciaFim")
    if vigencia_inicio and vigencia_fim and vigencia_fim < vigencia_inicio:
        raise ErroValidacao("vigenciaFim anterior a vigenciaInicio")
    for campo in ("dataCancelamento", "dataAssinatura", "dataPublicacaoPncp", "dataAtualizacao"):
        converter_data(bruta.get(campo), campo)
    converter_ano(bruta.get("anoAta"))
    validar_cnpj(bruta["cnpjOrgao"])


def limpar_ata(bruta: dict) -> dict:
    """Arruma UMA ata já conferida e devolve o registro com os nomes das colunas."""
    return {
        "numero_controle_pncp_ata": limpar_texto(bruta["numeroControlePNCPAta"]),
        "numero_ata_registro_preco": limpar_texto(bruta.get("numeroAtaRegistroPreco")),
        "ano_ata": converter_ano(bruta.get("anoAta")),
        "numero_controle_pncp_compra": limpar_texto(bruta["numeroControlePNCPCompra"]),
        "cancelado": bool(bruta.get("cancelado")),  # vazio vira False
        "data_cancelamento": converter_data(bruta.get("dataCancelamento"), "dataCancelamento"),
        "data_assinatura": converter_data(bruta.get("dataAssinatura"), "dataAssinatura"),
        "vigencia_inicio": converter_data(bruta.get("vigenciaInicio"), "vigenciaInicio"),
        "vigencia_fim": converter_data(bruta.get("vigenciaFim"), "vigenciaFim"),
        "data_publicacao_pncp": converter_data(bruta.get("dataPublicacaoPncp"), "dataPublicacaoPncp"),
        "data_atualizacao_pncp": converter_data(bruta.get("dataAtualizacao"), "dataAtualizacao"),
        "cnpj_orgao": validar_cnpj(bruta["cnpjOrgao"]),
        "nome_orgao": limpar_texto(bruta.get("nomeOrgao")),
        "codigo_unidade_orgao": limpar_texto(bruta.get("codigoUnidadeOrgao")),
        "nome_unidade_orgao": limpar_texto(bruta.get("nomeUnidadeOrgao")),
        "objeto_contratacao": limpar_texto(bruta.get("objetoContratacao")),
        "possibilidade_adesao": bruta.get("possibilidadeAdesao"),  # pode ser None
    }


def validar_ata(bruta: dict) -> dict:
    """Confere e arruma UMA ata (as duas coisas juntas)."""
    conferir_ata(bruta)
    return limpar_ata(bruta)


def remover_duplicadas(atas: list[dict]) -> tuple[list[dict], int]:
    """Se a mesma ata aparece mais de uma vez, fica a versão mais recente."""
    mais_recentes: dict[str, dict] = {}
    for ata in atas:
        codigo = ata["numero_controle_pncp_ata"]
        atual = mais_recentes.get(codigo)
        # data None conta como "a mais antiga possível"
        if atual is None or (ata["data_atualizacao_pncp"] or date.min) >= (
            atual["data_atualizacao_pncp"] or date.min
        ):
            mais_recentes[codigo] = ata
    return list(mais_recentes.values()), len(atas) - len(mais_recentes)


def validar(registros: list[dict]) -> tuple[list[dict], list[dict]]:
    """ETAPA VALIDAR: separa as atas boas das ruins. NÃO altera as boas (seguem como chegaram)."""
    boas: list[dict] = []
    rejeitadas: list[dict] = []
    ja_rejeitadas: set[tuple] = set()  # evita listar o mesmo problema duas vezes

    for bruta in registros:
        try:
            conferir_ata(bruta)
            boas.append(bruta)
        except ErroValidacao as erro:
            chave = (bruta.get("numeroControlePNCPAta"), str(erro))
            if chave in ja_rejeitadas:
                continue
            ja_rejeitadas.add(chave)
            rejeitadas.append(
                {
                    "numero_controle_pncp_ata": bruta.get("numeroControlePNCPAta"),
                    "motivo": str(erro),
                    "payload": bruta,  # guardamos o original para investigar depois
                }
            )
    logger.info("validação concluída: %s boas, %s rejeitadas", len(boas), len(rejeitadas))
    return boas, rejeitadas


def normalizar(validas_brutas: list[dict]) -> list[dict]:
    """ETAPA TRANSFORMAR: limpa as atas já validadas e descarta repetidas."""
    limpas = [limpar_ata(bruta) for bruta in validas_brutas]
    limpas, repetidas = remover_duplicadas(limpas)
    logger.info("transformação concluída: %s atas, %s repetidas descartadas", len(limpas), repetidas)
    return limpas


def transformar(registros: list[dict]) -> tuple[list[dict], list[dict]]:
    """Validar + normalizar de uma vez (atalho usado nos testes)."""
    boas, rejeitadas = validar(registros)
    return normalizar(boas), rejeitadas
