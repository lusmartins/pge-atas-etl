"""Testes da carga no PostgreSQL, incluindo a idempotência.

Parte dos testes usa uma conexão falsa (rápidos, sem banco).
A outra parte usa o PostgreSQL de verdade, num banco temporário que é criado
e apagado a cada teste, sem mexer nos dados reais. Se o PostgreSQL não estiver
ligado, esses testes são pulados (suba com: docker compose up -d).
"""

import json
import os
import pathlib
import uuid
from datetime import date

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import sql

from src import load
from src.transform import transformar

load_dotenv()

RAIZ = pathlib.Path(__file__).resolve().parents[1]
AMOSTRA = pathlib.Path(__file__).parent / "fixtures" / "atas_amostra.json"
DATA_INICIAL = date(2026, 10, 26)
DATA_FINAL = date(2026, 10, 28)


@pytest.fixture
def pagina_amostra():
    return json.loads(AMOSTRA.read_text(encoding="utf-8"))


# ---------- sem banco: conexão falsa ----------

class CursorFalso:
    def __init__(self, registro):
        self.registro = registro

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, comando, linhas):
        self.registro.append((comando, list(linhas)))


class ConexaoFalsa:
    def __init__(self):
        self.chamadas = []

    def cursor(self):
        return CursorFalso(self.chamadas)


def test_staging_ignora_ata_sem_codigo_e_guarda_a_pagina():
    pagina = {"numeroPagina": 4, "data": [{"numeroControlePNCPAta": "A-1"}, {"outro": 1}]}
    conexao = ConexaoFalsa()

    enviadas = load.gravar_staging(conexao, [pagina], DATA_INICIAL, DATA_FINAL)

    _, linhas = conexao.chamadas[0]
    assert enviadas == 1
    assert linhas[0]["numero"] == "A-1"
    assert linhas[0]["pagina"] == 4


def test_comandos_sql_usam_upsert():
    assert "ON CONFLICT (numero_controle_pncp_ata)" in load.SQL_ATAS
    assert "DO UPDATE" in load.SQL_ATAS
    assert "ON CONFLICT" in load.SQL_STAGING
    assert "ON CONFLICT (chave_dedup) DO NOTHING" in load.SQL_REJEITADAS


def test_primeira_coleta_nunca_e_atualizada():
    # guarda a data em que vimos a ata pela primeira vez
    assert "primeira_coleta_em" not in load.SQL_ATAS


def test_chave_dedup_e_estavel_e_distingue_registros():
    a = {"numero_controle_pncp_ata": "A", "motivo": "m", "payload": {"x": 1, "y": 2}}
    mesma = {"numero_controle_pncp_ata": "A", "motivo": "m", "payload": {"y": 2, "x": 1}}
    outra = {"numero_controle_pncp_ata": "A", "motivo": "outro", "payload": {"x": 1, "y": 2}}

    assert load._chave_dedup(a) == load._chave_dedup(mesma)  # ordem das chaves não importa
    assert load._chave_dedup(a) != load._chave_dedup(outra)


# ---------- com banco de verdade ----------

def _parametros(nome_banco):
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5433"),
        "dbname": nome_banco,
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
    }


@pytest.fixture
def banco_teste(monkeypatch):
    """Cria um banco temporário com as nossas tabelas e o apaga no fim."""
    nome = f"teste_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(**_parametros("postgres"), autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL não está acessível (suba com: docker compose up -d)")

    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nome)))
    # só o script das tabelas do projeto: o 000_airflow_db.sql cria o banco do Airflow,
    # não tem relação com este teste e não pode rodar dentro de uma transação
    tabelas = RAIZ / "sql" / "001_create_tables.sql"
    with psycopg.connect(**_parametros(nome)) as conexao:
        conexao.execute(tabelas.read_text(encoding="utf-8"))

    monkeypatch.setenv("POSTGRES_DB", nome)  # o load.conectar() vai usar o banco temporário
    yield nome

    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(nome)))
    admin.close()


def consultar(nome_banco, comando):
    with psycopg.connect(**_parametros(nome_banco)) as conexao:
        return conexao.execute(comando).fetchall()


def contagens(nome_banco):
    return consultar(
        nome_banco,
        "SELECT (SELECT count(*) FROM staging_atas), (SELECT count(*) FROM atas), "
        "(SELECT count(*) FROM atas_rejeitadas)",
    )[0]


def carregar_amostra(pagina):
    validas, rejeitadas = transformar(pagina["data"])
    return validas, rejeitadas, load.carregar(
        [pagina], validas, rejeitadas, DATA_INICIAL, DATA_FINAL
    )


def test_carga_grava_nas_tres_tabelas(banco_teste, pagina_amostra):
    _, _, resumo = carregar_amostra(pagina_amostra)

    assert resumo == {"staging": 5, "atas": 4, "rejeitadas": 1}
    assert contagens(banco_teste) == (5, 4, 1)


def test_rodar_duas_vezes_nao_duplica(banco_teste, pagina_amostra):
    carregar_amostra(pagina_amostra)
    depois_da_primeira = contagens(banco_teste)

    carregar_amostra(pagina_amostra)

    assert contagens(banco_teste) == depois_da_primeira


def test_ata_alterada_na_fonte_e_atualizada_sem_criar_outra(banco_teste, pagina_amostra):
    validas, rejeitadas, _ = carregar_amostra(pagina_amostra)
    codigo = validas[0]["numero_controle_pncp_ata"]
    consulta = (
        "SELECT nome_orgao, primeira_coleta_em, atualizado_em FROM atas "
        f"WHERE numero_controle_pncp_ata = '{codigo}'"
    )
    antes = consultar(banco_teste, consulta)[0]

    alterada = dict(validas[0], nome_orgao="NOME ALTERADO NA FONTE")
    validas[0] = alterada
    load.carregar([pagina_amostra], validas, rejeitadas, DATA_INICIAL, DATA_FINAL)

    depois = consultar(banco_teste, consulta)[0]
    assert depois[0] == "NOME ALTERADO NA FONTE"  # atualizou
    assert depois[1] == antes[1]  # primeira coleta preservada
    assert depois[2] > antes[2]  # atualizado_em avançou
    assert contagens(banco_teste)[1] == 4  # continua com 4 atas


def test_falha_desfaz_tudo(banco_teste, pagina_amostra):
    validas, rejeitadas = transformar(pagina_amostra["data"])
    # vigência que termina antes de começar: o banco recusa (restrição ck_vigencia)
    invalida = dict(
        validas[0],
        vigencia_inicio=date(2030, 1, 1),
        vigencia_fim=date(2029, 1, 1),
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        load.carregar([pagina_amostra], [invalida], rejeitadas, DATA_INICIAL, DATA_FINAL)

    # nada ficou pela metade: nem staging, nem rejeitadas
    assert contagens(banco_teste) == (0, 0, 0)