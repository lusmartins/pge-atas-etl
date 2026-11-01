"""Gravação das atas no PostgreSQL: staging, tabela final e rejeitadas."""

import hashlib
import json
import logging
import os
from datetime import date

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

# colunas da tabela "atas" (na mesma ordem em que o transform.py as devolve)
COLUNAS_ATAS = [
    "numero_controle_pncp_ata",
    "numero_ata_registro_preco",
    "ano_ata",
    "numero_controle_pncp_compra",
    "cancelado",
    "data_cancelamento",
    "data_assinatura",
    "vigencia_inicio",
    "vigencia_fim",
    "data_publicacao_pncp",
    "data_atualizacao_pncp",
    "cnpj_orgao",
    "nome_orgao",
    "codigo_unidade_orgao",
    "nome_unidade_orgao",
    "objeto_contratacao",
    "possibilidade_adesao",
]

# Staging: se a mesma ata do mesmo período já existe, atualiza em vez de duplicar.
SQL_STAGING = """
INSERT INTO staging_atas
    (numero_controle_pncp_ata, data_inicial_consulta, data_final_consulta, pagina, payload)
VALUES
    (%(numero)s, %(data_inicial)s, %(data_final)s, %(pagina)s, %(payload)s)
ON CONFLICT (numero_controle_pncp_ata, data_inicial_consulta, data_final_consulta)
DO UPDATE SET
    pagina = EXCLUDED.pagina,
    payload = EXCLUDED.payload,
    data_coleta = now()
"""

# Tabela final (UPSERT): ata nova é inserida; ata que já existe é atualizada.
# "primeira_coleta_em" não entra no UPDATE: ela guarda quando vimos a ata pela 1ª vez.
_colunas = ", ".join(COLUNAS_ATAS)
_valores = ", ".join(f"%({c})s" for c in COLUNAS_ATAS)
_atualizacoes = ", ".join(
    f"{c} = EXCLUDED.{c}" for c in COLUNAS_ATAS if c != "numero_controle_pncp_ata"
)
SQL_ATAS = f"""
INSERT INTO atas ({_colunas})
VALUES ({_valores})
ON CONFLICT (numero_controle_pncp_ata)
DO UPDATE SET {_atualizacoes}, atualizado_em = now()
"""

# Rejeitadas: se o mesmo problema já foi guardado, não guarda de novo.
SQL_REJEITADAS = """
INSERT INTO atas_rejeitadas
    (chave_dedup, numero_controle_pncp_ata, motivo, payload,
     data_inicial_consulta, data_final_consulta)
VALUES
    (%(chave)s, %(numero)s, %(motivo)s, %(payload)s, %(data_inicial)s, %(data_final)s)
ON CONFLICT (chave_dedup) DO NOTHING
"""


def conectar() -> psycopg.Connection:
    """Abre a conexão com o banco, lendo usuário e senha do arquivo .env."""
    load_dotenv()
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5433"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def gravar_staging(conn, paginas: list[dict], data_inicial: date, data_final: date) -> int:
    """Guarda cada ata como chegou da API. Devolve quantas linhas enviou."""
    linhas = []
    for pagina in paginas:
        for ata in pagina["data"]:
            numero = ata.get("numeroControlePNCPAta")
            if not numero:  # sem código não há como identificar a linha
                continue
            linhas.append(
                {
                    "numero": numero,
                    "data_inicial": data_inicial,
                    "data_final": data_final,
                    "pagina": pagina["numeroPagina"],
                    "payload": Jsonb(ata),
                }
            )
    with conn.cursor() as cur:
        cur.executemany(SQL_STAGING, linhas)
    return len(linhas)


def gravar_atas(conn, atas: list[dict]) -> int:
    """Grava as atas válidas na tabela final, sem duplicar (UPSERT)."""
    with conn.cursor() as cur:
        cur.executemany(SQL_ATAS, atas)
    return len(atas)


def _chave_dedup(rejeitada: dict) -> str:
    """Impressão digital do registro rejeitado (ata + motivo + conteúdo)."""
    texto = "|".join(
        [
            str(rejeitada.get("numero_controle_pncp_ata")),
            rejeitada["motivo"],
            json.dumps(rejeitada["payload"], sort_keys=True, ensure_ascii=False),
        ]
    )
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def gravar_rejeitadas(
    conn, rejeitadas: list[dict], data_inicial: date, data_final: date
) -> int:
    """Guarda as atas com problema, junto com o motivo."""
    linhas = [
        {
            "chave": _chave_dedup(r),
            "numero": r.get("numero_controle_pncp_ata"),
            "motivo": r["motivo"],
            "payload": Jsonb(r["payload"]),
            "data_inicial": data_inicial,
            "data_final": data_final,
        }
        for r in rejeitadas
    ]
    with conn.cursor() as cur:
        cur.executemany(SQL_REJEITADAS, linhas)
    return len(linhas)


def carregar(
    paginas: list[dict],
    validas: list[dict],
    rejeitadas: list[dict],
    data_inicial: date,
    data_final: date,
) -> dict:
    """Grava tudo numa única transação: ou entra tudo, ou não entra nada."""
    with conectar() as conn:  # no fim, confirma (commit) ou desfaz (rollback)
        resumo = {
            "staging": gravar_staging(conn, paginas, data_inicial, data_final),
            "atas": gravar_atas(conn, validas),
            "rejeitadas": gravar_rejeitadas(conn, rejeitadas, data_inicial, data_final),
        }
    logger.info("carga concluída: %s", resumo)
    return resumo
