"""As 5 etapas do pipeline, escritas como funções comuns de Python.

Não há nada do Airflow aqui dentro: a DAG só chama estas funções.
Isso permite testar cada etapa sozinha e reaproveitar o código fora do Airflow.

Como as etapas conversam: cada uma grava o resultado num ARQUIVO (JSON) dentro
de uma pasta da execução e devolve só um resumo pequeno (contagens).
A etapa seguinte lê o arquivo. Assim o Airflow (XCom) carrega apenas bilhetes
pequenos, e não centenas de atas.
"""

import json
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

from src import extract, load, transform

logger = logging.getLogger(__name__)

# colunas da tabela "atas" que são datas (no JSON elas viram texto e voltam a ser data)
COLUNAS_DATA = (
    "data_cancelamento",
    "data_assinatura",
    "vigencia_inicio",
    "vigencia_fim",
    "data_publicacao_pncp",
    "data_atualizacao_pncp",
)

# nomes dos arquivos que as etapas trocam entre si
ARQ_BRUTO = "01_bruto.json"          # saída da etapa 1 (extrair)
ARQ_BOAS = "03_boas.json"            # saída da etapa 3 (validar)
ARQ_REJEITADAS = "03_rejeitadas.json"
ARQ_LIMPAS = "04_limpas.json"        # saída da etapa 4 (transformar)


# ---------- utilitários ----------

def _como_data(valor) -> date:
    """Aceita uma data ou um texto '2026-10-28' e devolve sempre uma data."""
    return valor if isinstance(valor, date) else date.fromisoformat(str(valor))


def _para_json(objeto):
    """Ensina o JSON a escrever datas (viram '2026-10-28')."""
    if isinstance(objeto, date):
        return objeto.isoformat()
    raise TypeError(f"tipo não suportado no JSON: {type(objeto)}")


def _gravar_json(dados, caminho: Path) -> None:
    """Grava o arquivo de forma segura: escreve num temporário e só então troca.

    Se o processo cair no meio da escrita, o arquivo final nunca fica pela metade.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(
        json.dumps(dados, default=_para_json, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporario, caminho)


def _ler_json(caminho: Path):
    if not caminho.exists():
        raise FileNotFoundError(
            f"arquivo {caminho.name} não encontrado: a etapa anterior rodou com sucesso?"
        )
    return json.loads(caminho.read_text(encoding="utf-8"))


def pasta_da_execucao(run_id: str) -> Path:
    """Pasta própria de cada execução da DAG (uma não mistura com a outra)."""
    base = Path(os.getenv("PGE_DADOS_DIR", "dados"))
    return base / re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)  # tira caracteres problemáticos


def resolver_periodo(
    data_inicial_param, data_final_param, intervalo_inicio: date, intervalo_fim: date
) -> tuple[date, date]:
    """Decide qual período buscar.

    - Execução manual: o usuário informa as duas datas.
    - Execução agendada: usa o dia que o Airflow indica (início do intervalo até a véspera do fim).
    """
    if bool(data_inicial_param) != bool(data_final_param):
        raise ValueError("informe as duas datas (inicial e final) ou nenhuma")

    if data_inicial_param and data_final_param:
        inicial, final = _como_data(data_inicial_param), _como_data(data_final_param)
    else:
        inicial, final = _como_data(intervalo_inicio), _como_data(intervalo_fim) - timedelta(days=1)

    if final < inicial:
        raise ValueError(f"período inválido: {inicial} a {final}")
    return inicial, final


# ---------- as 5 etapas ----------

def extrair(
    data_inicial, data_final, pasta: Path, tamanho_pagina: int = 10, max_paginas: int = 3
) -> dict:
    """ETAPA 1: busca as páginas na API e guarda tudo, sem mexer, num arquivo."""
    paginas = extract.buscar_paginas(
        _como_data(data_inicial), _como_data(data_final), tamanho_pagina, max_paginas
    )
    _gravar_json(paginas, pasta / ARQ_BRUTO)
    resumo = {
        "paginas": len(paginas),
        "atas_extraidas": sum(len(p["data"]) for p in paginas),
    }
    logger.info("extrair: %s", resumo)
    return resumo


def carregar_staging(data_inicial, data_final, pasta: Path, conectar=load.conectar) -> dict:
    """ETAPA 2: copia as atas brutas para a tabela staging (cópia de segurança)."""
    paginas = _ler_json(pasta / ARQ_BRUTO)
    with conectar() as conn:  # no fim: commit; se der erro: rollback
        gravadas = load.gravar_staging(
            conn, paginas, _como_data(data_inicial), _como_data(data_final)
        )
    resumo = {"staging_gravadas": gravadas}
    logger.info("carregar_staging: %s", resumo)
    return resumo


def validar(pasta: Path) -> dict:
    """ETAPA 3: separa as atas boas das ruins (sem alterar as boas)."""
    paginas = _ler_json(pasta / ARQ_BRUTO)
    registros = [ata for pagina in paginas for ata in pagina["data"]]
    boas, rejeitadas = transform.validar(registros)
    _gravar_json(boas, pasta / ARQ_BOAS)
    _gravar_json(rejeitadas, pasta / ARQ_REJEITADAS)
    resumo = {"recebidas": len(registros), "boas": len(boas), "rejeitadas": len(rejeitadas)}
    logger.info("validar: %s", resumo)
    return resumo


def transformar(pasta: Path) -> dict:
    """ETAPA 4: limpa as atas boas e descarta repetidas."""
    boas = _ler_json(pasta / ARQ_BOAS)
    limpas = transform.normalizar(boas)
    _gravar_json(limpas, pasta / ARQ_LIMPAS)
    resumo = {"limpas": len(limpas), "repetidas_descartadas": len(boas) - len(limpas)}
    logger.info("transformar: %s", resumo)
    return resumo


def carregar_final(data_inicial, data_final, pasta: Path, conectar=load.conectar) -> dict:
    """ETAPA 5: grava as atas limpas em 'atas' e as ruins em 'atas_rejeitadas'."""
    limpas = _ler_json(pasta / ARQ_LIMPAS)
    for ata in limpas:  # no arquivo as datas são texto: voltam a ser datas
        for coluna in COLUNAS_DATA:
            if ata[coluna] is not None:
                ata[coluna] = date.fromisoformat(ata[coluna])
    rejeitadas = _ler_json(pasta / ARQ_REJEITADAS)

    # as duas gravações na MESMA transação: ou entram as duas, ou nenhuma
    with conectar() as conn:
        resumo = {
            "atas_gravadas": load.gravar_atas(conn, limpas),
            "rejeitadas_gravadas": load.gravar_rejeitadas(
                conn, rejeitadas, _como_data(data_inicial), _como_data(data_final)
            ),
        }
    logger.info("carregar_final: %s", resumo)
    return resumo
