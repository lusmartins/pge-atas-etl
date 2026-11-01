"""DAG do pipeline de Atas de Registro de Preços (PNCP).

Este arquivo é só o "maestro": decide a ORDEM e as REGRAS DE EXECUÇÃO.
O trabalho de verdade está em src/etapas.py.

    extrair -> carregar_staging -> validar -> transformar -> carregar_final
"""

from datetime import timedelta

import pendulum
import psycopg
from airflow.decorators import dag, task
from airflow.hooks.base import BaseHook
from airflow.models import Variable
from airflow.models.param import Param
from airflow.operators.python import get_current_context

from src import etapas

CONEXAO_POSTGRES = "pge_postgres"  # Connection criada no Airflow (ver docker-compose.yml)


def _conectar():
    """Abre a conexão com o banco usando a Connection do Airflow (sem senha no código)."""
    c = BaseHook.get_connection(CONEXAO_POSTGRES)
    return psycopg.connect(
        host=c.host, port=c.port, dbname=c.schema, user=c.login, password=c.password
    )


def _contexto():
    """Descobre o período a buscar e a pasta de arquivos desta execução."""
    ctx = get_current_context()
    inicial, final = etapas.resolver_periodo(
        ctx["params"].get("data_inicial"),
        ctx["params"].get("data_final"),
        ctx["data_interval_start"].date(),
        ctx["data_interval_end"].date(),
    )
    return inicial, final, etapas.pasta_da_execucao(ctx["run_id"])


@dag(
    dag_id="atas_pncp",
    description="ETL de atas de registro de preços do PNCP para o PostgreSQL",
    schedule="@daily",  # roda sozinha todo dia
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Fortaleza"),
    catchup=False,  # não tenta "recuperar" os dias antigos de uma vez
    max_active_runs=1,  # uma execução por vez: evita duas escrevendo ao mesmo tempo
    default_args={
        "retries": 2,  # se uma etapa falhar, o Airflow tenta mais 2 vezes
        "retry_delay": timedelta(minutes=2),
    },
    params={  # preenchidos na execução manual (Trigger DAG w/ config); vazios = automático
        "data_inicial": Param(None, type=["null", "string"], description="AAAA-MM-DD"),
        "data_final": Param(None, type=["null", "string"], description="AAAA-MM-DD"),
    },
    tags=["pge", "pncp", "etl"],
)
def atas_pncp():
    @task(execution_timeout=timedelta(minutes=30))  # a API é lenta: damos tempo, mas não infinito
    def extrair() -> dict:
        inicial, final, pasta = _contexto()
        return etapas.extrair(
            inicial,
            final,
            pasta,
            # Variables do Airflow: dá para mudar na tela, sem mexer no código
            tamanho_pagina=int(Variable.get("pge_tamanho_pagina", default_var=10)),
            max_paginas=int(Variable.get("pge_max_paginas", default_var=3)),
        )

    @task
    def carregar_staging() -> dict:
        inicial, final, pasta = _contexto()
        return etapas.carregar_staging(inicial, final, pasta, conectar=_conectar)

    @task
    def validar() -> dict:
        _, _, pasta = _contexto()
        return etapas.validar(pasta)

    @task
    def transformar() -> dict:
        _, _, pasta = _contexto()
        return etapas.transformar(pasta)

    @task
    def carregar_final() -> dict:
        inicial, final, pasta = _contexto()
        return etapas.carregar_final(inicial, final, pasta, conectar=_conectar)

    # a ordem das etapas
    extrair() >> carregar_staging() >> validar() >> transformar() >> carregar_final()


atas_pncp()
