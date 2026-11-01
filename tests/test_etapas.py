"""Testes das 5 etapas: cada uma deve ler o arquivo da anterior e deixar o seu."""

import json
import pathlib
from datetime import date

import pytest

from src import etapas

AMOSTRA = pathlib.Path(__file__).parent / "fixtures" / "atas_amostra.json"
DATA_INICIAL = date(2026, 10, 26)
DATA_FINAL = date(2026, 10, 28)


@pytest.fixture
def pagina_amostra():
    return json.loads(AMOSTRA.read_text(encoding="utf-8"))


@pytest.fixture
def pasta(tmp_path):
    return tmp_path / "execucao"


# conexão falsa: só anota o que a etapa tentou gravar (sem banco)
class CursorFalso:
    def __init__(self, anotacoes):
        self.anotacoes = anotacoes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, comando, linhas):
        self.anotacoes.append(list(linhas))


class ConexaoFalsa:
    def __init__(self):
        self.anotacoes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return CursorFalso(self.anotacoes)


def rodar_ate_transformar(pagina_amostra, pasta, monkeypatch):
    monkeypatch.setattr(etapas.extract, "buscar_paginas", lambda *a, **k: [pagina_amostra])
    resumo_extrair = etapas.extrair(DATA_INICIAL, DATA_FINAL, pasta)
    resumo_validar = etapas.validar(pasta)
    resumo_transformar = etapas.transformar(pasta)
    return resumo_extrair, resumo_validar, resumo_transformar


# ---------- período ----------

def test_periodo_manual_usa_as_datas_informadas():
    assert etapas.resolver_periodo("2026-10-01", "2026-10-05", date(2026, 1, 1), date(2026, 1, 2)) == (
        date(2026, 10, 1),
        date(2026, 10, 5),
    )


def test_periodo_agendado_usa_o_intervalo_do_airflow():
    # intervalo diário: começa dia 10 e termina (exclusivo) dia 11 -> período é o dia 10
    assert etapas.resolver_periodo(None, None, date(2026, 10, 10), date(2026, 10, 11)) == (
        date(2026, 10, 10),
        date(2026, 10, 10),
    )


@pytest.mark.parametrize(
    "inicial, final",
    [("2026-10-01", None), (None, "2026-10-05"), ("2026-10-05", "2026-10-01")],
)
def test_periodo_invalido(inicial, final):
    with pytest.raises(ValueError):
        etapas.resolver_periodo(inicial, final, date(2026, 10, 10), date(2026, 10, 11))


def test_pasta_da_execucao_limpa_caracteres_estranhos():
    assert ":" not in etapas.pasta_da_execucao("manual__2026-10-30T12:00:00+00:00").name


# ---------- etapas ----------

def test_extrair_validar_transformar_geram_os_arquivos(pagina_amostra, pasta, monkeypatch):
    ext, val, tra = rodar_ate_transformar(pagina_amostra, pasta, monkeypatch)

    assert ext["atas_extraidas"] == len(pagina_amostra["data"])
    assert val["recebidas"] == val["boas"] + val["rejeitadas"]
    assert tra["limpas"] == val["boas"] - tra["repetidas_descartadas"]
    for nome in (etapas.ARQ_BRUTO, etapas.ARQ_BOAS, etapas.ARQ_REJEITADAS, etapas.ARQ_LIMPAS):
        assert (pasta / nome).exists()


def test_nao_sobra_arquivo_temporario(pagina_amostra, pasta, monkeypatch):
    rodar_ate_transformar(pagina_amostra, pasta, monkeypatch)
    assert not list(pasta.glob("*.tmp"))


def test_refazer_uma_etapa_nao_exige_refazer_as_anteriores(pagina_amostra, pasta, monkeypatch):
    rodar_ate_transformar(pagina_amostra, pasta, monkeypatch)
    primeira = etapas.transformar(pasta)
    # a API nem é chamada de novo: a etapa só relê o arquivo da anterior
    monkeypatch.setattr(etapas.extract, "buscar_paginas", lambda *a, **k: pytest.fail("não devia chamar a API"))
    assert etapas.transformar(pasta) == primeira


def test_etapa_sem_o_arquivo_da_anterior_da_erro_claro(pasta):
    with pytest.raises(FileNotFoundError, match="etapa anterior"):
        etapas.validar(pasta)


def test_carregar_staging_envia_as_atas_brutas(pagina_amostra, pasta, monkeypatch):
    rodar_ate_transformar(pagina_amostra, pasta, monkeypatch)
    conexao = ConexaoFalsa()
    resumo = etapas.carregar_staging(DATA_INICIAL, DATA_FINAL, pasta, conectar=lambda: conexao)
    assert resumo["staging_gravadas"] == len(conexao.anotacoes[0])


def test_carregar_final_devolve_datas_como_datas(pagina_amostra, pasta, monkeypatch):
    rodar_ate_transformar(pagina_amostra, pasta, monkeypatch)
    conexao = ConexaoFalsa()
    resumo = etapas.carregar_final(DATA_INICIAL, DATA_FINAL, pasta, conectar=lambda: conexao)

    atas_enviadas = conexao.anotacoes[0]
    assert resumo["atas_gravadas"] == len(atas_enviadas)
    for ata in atas_enviadas:
        for coluna in etapas.COLUNAS_DATA:
            assert ata[coluna] is None or isinstance(ata[coluna], date)
