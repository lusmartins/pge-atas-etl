"""Busca de atas de registro de preços na API do PNCP."""

import logging
import time
from datetime import date

import requests

logger = logging.getLogger(__name__)

URL_ATAS = "https://pncp.gov.br/api/consulta/v1/atas"

# erros "temporários": vale a pena tentar de novo
# 429 = muitos pedidos; 500, 502, 503, 504 = problema no servidor
STATUS_RETENTAVEIS = {429, 500, 502, 503, 504}


class ErroExtracao(Exception):
    """Levantado quando a busca falha mesmo depois de todas as tentativas."""


def _validar_corpo(corpo, pagina: int) -> dict:
    """Confere se a resposta tem o formato esperado e completa o que faltar."""
    if not isinstance(corpo, dict) or not isinstance(corpo.get("data"), list):
        raise ErroExtracao(f"página {pagina}: resposta fora do formato esperado")
    corpo.setdefault("numeroPagina", pagina)
    corpo.setdefault("empty", len(corpo["data"]) == 0)
    corpo.setdefault("paginasRestantes", 0)  # na dúvida, para de paginar
    return corpo


def buscar_pagina(
    data_inicial: date,
    data_final: date,
    pagina: int = 1,
    tamanho_pagina: int = 10,
    max_tentativas: int = 3,
    espera_inicial: float = 2.0,
) -> dict:
    """Busca UMA página de atas, tentando de novo se houver falha temporária."""
    parametros = {
        "dataInicial": data_inicial.strftime("%Y%m%d"),  # 2026-10-26 vira 20261026
        "dataFinal": data_final.strftime("%Y%m%d"),
        "pagina": pagina,
        "tamanhoPagina": tamanho_pagina,
    }

    for tentativa in range(1, max_tentativas + 1):
        try:
            resposta = requests.get(
                URL_ATAS,
                params=parametros,
                headers={"Accept": "application/json"},
                timeout=120,  # a API é lenta: esperamos até 2 minutos
            )
            resposta.raise_for_status()  # erro HTTP vira exceção
            try:
                corpo = resposta.json()
            except ValueError as erro:  # chegou algo que não é JSON (ex.: página de erro)
                raise ErroExtracao(
                    f"página {pagina}: resposta não é um JSON válido"
                ) from erro
            return _validar_corpo(corpo, pagina)

        except (requests.Timeout, requests.ConnectionError) as erro:
            motivo = f"falha de rede ({type(erro).__name__})"

        except requests.HTTPError as erro:
            status = erro.response.status_code
            if status not in STATUS_RETENTAVEIS:
                # erro "definitivo" (ex.: 400, 404): tentar de novo não adianta
                raise ErroExtracao(
                    f"página {pagina}: erro definitivo {status}"
                ) from erro
            motivo = f"erro temporário {status}"

        # chegou aqui: houve uma falha temporária
        if tentativa == max_tentativas:
            logger.error("página %s: desisti após %s tentativas", pagina, tentativa)
            raise ErroExtracao(
                f"página {pagina}: falhou após {max_tentativas} tentativas ({motivo})"
            )

        espera = espera_inicial * 2 ** (tentativa - 1)  # 2s, 4s, 8s...
        logger.warning(
            "página %s: %s (tentativa %s/%s). Nova tentativa em %.0fs",
            pagina, motivo, tentativa, max_tentativas, espera,
        )
        time.sleep(espera)


def buscar_paginas(
    data_inicial: date,
    data_final: date,
    tamanho_pagina: int = 10,
    max_paginas: int = 3,
    pausa_entre_paginas: float = 0.5,
) -> list[dict]:
    """Busca página após página, até acabarem ou até atingir o limite."""
    paginas = []
    pagina = 1

    while pagina <= max_paginas:  # repete enquanto não passar do limite
        resposta = buscar_pagina(data_inicial, data_final, pagina, tamanho_pagina)
        paginas.append(resposta)
        logger.info(
            "página %s buscada: %s atas (restam %s páginas na API)",
            pagina, len(resposta["data"]), resposta["paginasRestantes"],
        )

        # a API avisa quando acabou: lista vazia ou nenhuma página restante
        if resposta["empty"] or resposta["paginasRestantes"] == 0:
            break

        pagina += 1  # vira a página
        time.sleep(pausa_entre_paginas)  # respeita a fonte: não faz pedidos em rajada

    logger.info("busca concluída: %s páginas", len(paginas))
    return paginas
