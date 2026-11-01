"""Testes da extração. A API pública é substituída por respostas falsas (mocks)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import extract

DATA_INICIAL = date(2026, 10, 26)
DATA_FINAL = date(2026, 10, 28)


def resposta_falsa(status=200, corpo=None, json_invalido=False):
    """Cria uma resposta HTTP de mentira, como se viesse da API."""
    resposta = MagicMock()
    resposta.status_code = status
    if json_invalido:
        resposta.json.side_effect = ValueError("não é JSON")
    else:
        resposta.json.return_value = corpo
    if status >= 400:
        resposta.raise_for_status.side_effect = requests.HTTPError(response=resposta)
    return resposta


def corpo_pagina(restantes, quantidade=1):
    return {
        "data": [{"numeroControlePNCPAta": f"A-{i}"} for i in range(quantidade)],
        "empty": False,
        "paginasRestantes": restantes,
    }


@pytest.fixture(autouse=True)
def sem_espera():
    """Nenhum teste espera de verdade: o time.sleep é trocado por um espião."""
    with patch("src.extract.time.sleep") as sleep:
        yield sleep


@pytest.fixture
def api():
    """A API falsa. Cada teste define o que ela responde (side_effect)."""
    with patch("src.extract.requests.get") as get:
        yield get


# ---------- pedido enviado à API ----------

def test_parametros_do_pedido(api):
    api.return_value = resposta_falsa(200, corpo_pagina(0))

    extract.buscar_pagina(DATA_INICIAL, DATA_FINAL, pagina=2, tamanho_pagina=50)

    chamada = api.call_args
    assert chamada.args[0] == extract.URL_ATAS
    assert chamada.kwargs["params"] == {
        "dataInicial": "20261026",  # formato AAAAMMDD exigido pela API
        "dataFinal": "20261028",
        "pagina": 2,
        "tamanhoPagina": 50,
    }
    assert chamada.kwargs["timeout"] > 0  # nunca esperar para sempre


# ---------- tentativas e erros ----------

def test_recupera_apos_falhas_temporarias(api, sem_espera):
    api.side_effect = [
        resposta_falsa(503),
        resposta_falsa(503),
        resposta_falsa(200, corpo_pagina(0)),
    ]

    corpo = extract.buscar_pagina(DATA_INICIAL, DATA_FINAL)

    assert corpo["data"]
    assert api.call_count == 3
    # a espera dobra a cada falha: 2s, depois 4s
    assert [c.args[0] for c in sem_espera.call_args_list] == [2.0, 4.0]


def test_falha_de_rede_tambem_tenta_de_novo(api):
    api.side_effect = [requests.ConnectionError(), resposta_falsa(200, corpo_pagina(0))]

    extract.buscar_pagina(DATA_INICIAL, DATA_FINAL)

    assert api.call_count == 2


def test_desiste_depois_de_todas_as_tentativas(api):
    api.side_effect = requests.Timeout()

    with pytest.raises(extract.ErroExtracao, match="3 tentativas"):
        extract.buscar_pagina(DATA_INICIAL, DATA_FINAL, max_tentativas=3)

    assert api.call_count == 3


@pytest.mark.parametrize("status", [400, 404])
def test_erro_definitivo_nao_tenta_de_novo(api, sem_espera, status):
    api.return_value = resposta_falsa(status)

    with pytest.raises(extract.ErroExtracao, match=str(status)):
        extract.buscar_pagina(DATA_INICIAL, DATA_FINAL)

    assert api.call_count == 1  # tentar de novo não adiantaria
    sem_espera.assert_not_called()


# ---------- respostas inválidas ou vazias ----------

def test_resposta_que_nao_e_json(api):
    api.return_value = resposta_falsa(200, json_invalido=True)

    with pytest.raises(extract.ErroExtracao, match="JSON"):
        extract.buscar_pagina(DATA_INICIAL, DATA_FINAL)


@pytest.mark.parametrize("corpo", [{"outra": 1}, [1, 2, 3], {"data": "texto"}])
def test_resposta_fora_do_formato_esperado(api, corpo):
    api.return_value = resposta_falsa(200, corpo)

    with pytest.raises(extract.ErroExtracao, match="formato"):
        extract.buscar_pagina(DATA_INICIAL, DATA_FINAL)


def test_resposta_vazia_e_reconhecida(api):
    api.return_value = resposta_falsa(200, {"data": []})

    corpo = extract.buscar_pagina(DATA_INICIAL, DATA_FINAL, pagina=7)

    assert corpo["empty"] is True
    assert corpo["numeroPagina"] == 7
    assert corpo["paginasRestantes"] == 0


# ---------- paginação ----------

def test_para_na_ultima_pagina(api):
    api.side_effect = [
        resposta_falsa(200, corpo_pagina(1)),
        resposta_falsa(200, corpo_pagina(0)),
    ]

    paginas = extract.buscar_paginas(DATA_INICIAL, DATA_FINAL, max_paginas=10)

    assert len(paginas) == 2
    assert api.call_count == 2


def test_respeita_o_limite_de_paginas(api):
    api.side_effect = [resposta_falsa(200, corpo_pagina(99)) for _ in range(5)]

    paginas = extract.buscar_paginas(DATA_INICIAL, DATA_FINAL, max_paginas=4)

    assert len(paginas) == 4


def test_primeira_pagina_vazia_encerra_a_busca(api):
    api.return_value = resposta_falsa(200, {"data": [], "empty": True})

    paginas = extract.buscar_paginas(DATA_INICIAL, DATA_FINAL, max_paginas=10)

    assert len(paginas) == 1
    assert api.call_count == 1


def test_pede_as_paginas_na_ordem(api):
    api.side_effect = [
        resposta_falsa(200, corpo_pagina(2)),
        resposta_falsa(200, corpo_pagina(1)),
        resposta_falsa(200, corpo_pagina(0)),
    ]

    extract.buscar_paginas(DATA_INICIAL, DATA_FINAL, max_paginas=10)

    pedidas = [c.kwargs["params"]["pagina"] for c in api.call_args_list]
    assert pedidas == [1, 2, 3]


def test_pausa_entre_paginas(api, sem_espera):
    api.side_effect = [
        resposta_falsa(200, corpo_pagina(2)),
        resposta_falsa(200, corpo_pagina(1)),
        resposta_falsa(200, corpo_pagina(0)),
    ]

    extract.buscar_paginas(DATA_INICIAL, DATA_FINAL, max_paginas=10, pausa_entre_paginas=0.7)

    # 3 páginas = 2 pausas (não há pausa depois da última)
    assert [c.args[0] for c in sem_espera.call_args_list] == [0.7, 0.7]
