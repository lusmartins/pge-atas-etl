"""Testes da transformação e da validação dos dados."""

import json
import pathlib
from datetime import date

import pytest

from src.transform import (
    ErroValidacao,
    converter_ano,
    converter_data,
    limpar_texto,
    remover_duplicadas,
    normalizar,
    transformar,
    validar,
    validar_ata,
    validar_cnpj,
)

AMOSTRA = pathlib.Path(__file__).parent / "fixtures" / "atas_amostra.json"


@pytest.fixture
def amostra():
    """5 atas reais da API (uma delas com vigência no ano 7203)."""
    return json.loads(AMOSTRA.read_text(encoding="utf-8"))["data"]


def ata_minima(**alteracoes):
    """Uma ata válida, com só o essencial. Cada teste muda o que quiser."""
    ata = {
        "numeroControlePNCPAta": "11111111000111-1-000001/2026-000001",
        "numeroControlePNCPCompra": "11111111000111-1-000001/2026",
        "cnpjOrgao": "11111111000111",
    }
    ata.update(alteracoes)
    return ata


# ---------- funções pequenas ----------

@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("  texto  ", "texto"),
        ("muitos     espaços", "muitos espaços"),
        ("linha 1\nlinha 2", "linha 1 linha 2"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_limpar_texto(entrada, esperado):
    assert limpar_texto(entrada) == esperado


def test_converter_data_valida():
    assert converter_data("2026-10-28", "campo") == date(2026, 10, 28)
    assert converter_data("2026-10-28T10:30:00", "campo") == date(2026, 10, 28)


def test_converter_data_ausente():
    assert converter_data(None, "campo") is None


@pytest.mark.parametrize("valor", ["não é data", "2026-13-45", "7203-07-27", "1850-01-01"])
def test_converter_data_invalida_ou_absurda(valor):
    with pytest.raises(ErroValidacao):
        converter_data(valor, "campo")


@pytest.mark.parametrize("valor", ["45.780.103/0001-50", "45780103000150"])
def test_cnpj_valido_fica_so_com_numeros(valor):
    assert validar_cnpj(valor) == "45780103000150"


@pytest.mark.parametrize("valor", [None, "", "123", "abc", "1234567890123456"])
def test_cnpj_invalido(valor):
    with pytest.raises(ErroValidacao):
        validar_cnpj(valor)


def test_converter_ano():
    assert converter_ano("2024") == 2024
    assert converter_ano(None) is None


@pytest.mark.parametrize("valor", ["abc", 1900, 2500])
def test_ano_invalido(valor):
    with pytest.raises(ErroValidacao):
        converter_ano(valor)


# ---------- validação de uma ata ----------

def test_ata_minima_e_valida_com_tipos_corretos():
    ata = validar_ata(ata_minima(vigenciaInicio="2026-01-01", cancelado=None))

    assert ata["vigencia_inicio"] == date(2026, 1, 1)
    assert ata["vigencia_fim"] is None
    assert ata["cancelado"] is False  # vazio vira False
    assert ata["possibilidade_adesao"] is None  # a fonte nem sempre informa


@pytest.mark.parametrize(
    "campo", ["numeroControlePNCPAta", "numeroControlePNCPCompra", "cnpjOrgao"]
)
def test_campo_obrigatorio_ausente(campo):
    ata = ata_minima()
    ata[campo] = None

    with pytest.raises(ErroValidacao, match=campo):
        validar_ata(ata)


def test_vigencia_que_termina_antes_de_comecar():
    ata = ata_minima(vigenciaInicio="2026-05-01", vigenciaFim="2026-04-01")

    with pytest.raises(ErroValidacao, match="anterior"):
        validar_ata(ata)


# ---------- lote de atas ----------

def test_lista_vazia():
    assert transformar([]) == ([], [])


def test_amostra_real_separa_validas_e_rejeitadas(amostra):
    validas, rejeitadas = transformar(amostra)

    assert len(validas) == 4
    assert len(rejeitadas) == 1
    assert "7203" in rejeitadas[0]["motivo"]  # vigência no ano 7203


def test_nada_e_descartado_em_silencio(amostra):
    validas, rejeitadas = transformar(amostra)

    assert len(validas) + len(rejeitadas) == len(amostra)


def test_rejeitada_guarda_o_original_para_rastreabilidade(amostra):
    _, rejeitadas = transformar(amostra)

    original = next(a for a in amostra if a["vigenciaFim"].startswith("7203"))
    assert rejeitadas[0]["payload"] == original
    assert rejeitadas[0]["numero_controle_pncp_ata"] == original["numeroControlePNCPAta"]


def test_textos_da_amostra_sao_limpos(amostra):
    validas, _ = transformar(amostra)
    por_codigo = {a["numero_controle_pncp_ata"]: a for a in validas}

    unidade = por_codigo["44926723000191-1-000006/2024-000001"]["nome_unidade_orgao"]
    assert unidade == "FUNDO MUNICIPAL DE SAÚDE"  # sem espaço no fim

    objeto = por_codigo["87958658000199-1-000023/2024-000001"]["objeto_contratacao"]
    assert "\n" not in objeto  # sem quebra de linha


def test_mesma_ata_rejeitada_aparece_uma_vez(amostra):
    _, rejeitadas = transformar(amostra + amostra)

    assert len(rejeitadas) == 1


# ---------- duplicadas ----------

def ata_limpa(atualizacao):
    return {"numero_controle_pncp_ata": "A-1", "data_atualizacao_pncp": atualizacao}


@pytest.mark.parametrize("ordem", [(0, 1), (1, 0)])
def test_duplicadas_ficam_com_a_versao_mais_recente(ordem):
    versoes = [ata_limpa(date(2026, 1, 1)), ata_limpa(date(2026, 5, 1))]

    resultado, repetidas = remover_duplicadas([versoes[i] for i in ordem])

    assert repetidas == 1
    assert resultado == [versoes[1]]  # a de maio, não importa a ordem


def test_duplicada_sem_data_perde_para_a_que_tem_data():
    resultado, _ = remover_duplicadas([ata_limpa(None), ata_limpa(date(2026, 5, 1))])

    assert resultado[0]["data_atualizacao_pncp"] == date(2026, 5, 1)


def test_transformar_remove_duplicadas_do_lote(amostra):
    validas, _ = transformar(amostra + amostra)

    assert len(validas) == 4


# --- etapas separadas (usadas pela DAG) ---

def test_validar_nao_altera_as_atas_boas(amostra):
    boas, rejeitadas = validar(amostra)
    assert len(boas) + len(rejeitadas) == len(amostra)
    assert all(b in amostra for b in boas)  # continuam exatamente como chegaram


def test_validar_e_normalizar_juntos_equivalem_a_transformar(amostra):
    boas, rejeitadas = validar(amostra)
    assert (normalizar(boas), rejeitadas) == transformar(amostra)


def test_normalizar_limpa_e_remove_duplicadas(amostra):
    boas, _ = validar(amostra)
    limpas = normalizar(boas + boas)
    codigos = [a["numero_controle_pncp_ata"] for a in limpas]
    assert len(codigos) == len(set(codigos))
