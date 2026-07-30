"""Tests de `peers.py` — set fijo de peers + promedio de PER (Decisión #9).

El PER de cada peer se deriva de `earningsYield` (`/key-metrics` anual) como
`1 / earningsYield` — la API stable de FMP ya no expone un campo `pe`
directo en `/quote`, y `/key-metrics-ttm` es de pago en el plan gratuito
actual (ver `peers.get_peer_pe_average`).
"""

from __future__ import annotations

import pytest

from investbot import peers


def test_get_peers_for_sector_excluye_propio_ticker():
    """El ticker consultado está hardcodeado como peer de su propio sector
    -> debe excluirse de su propio promedio."""
    resultado = peers.get_peers_for_sector("Technology", "ORCL")
    assert "ORCL" not in resultado
    assert set(resultado) == {"MSFT", "CRM"}


def test_get_peers_for_sector_sector_desconocido():
    assert peers.get_peers_for_sector("Sector Inexistente", "XXX") == []


async def test_get_peer_pe_average_caso_simple():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": 1 / 34.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(32.0)
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}
    # P1 (procedencia peers individuales): 3/3 válidos -> peers_pe tiene las
    # 3 entradas con el PER individual correcto, peers_no_usados vacío.
    assert result.peers_pe == {
        "MSFT": pytest.approx(30.0),
        "ORCL": pytest.approx(34.0),
        "CRM": pytest.approx(32.0),
    }
    assert result.peers_no_usados == {}


# ---------------------------------------------------------------------------
# per_minimo / per_maximo (Spec Patch [Iter-3], sección 2) — derivados del
# mismo array de PERs ya calculado, sin llamada adicional a /key-metrics.
# ---------------------------------------------------------------------------


async def test_get_peer_pe_average_expone_minimo_y_maximo():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},  # PER=30.0
        "ORCL": {"earningsYield": 1 / 34.0},  # PER=34.0
        "CRM": {"earningsYield": 1 / 32.0},  # PER=32.0
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_minimo == pytest.approx(30.0)
    assert result.per_promedio == pytest.approx(32.0)
    assert result.per_maximo == pytest.approx(34.0)


async def test_get_peer_pe_average_un_solo_peer_valido_minimo_igual_a_maximo():
    """Caso degenerado (sección 1 del Spec Patch Iter-3): con 1 solo peer
    válido, per_minimo == per_promedio == per_maximo — no hay rango real."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": None},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.peers_usados == ["MSFT"]
    assert result.per_minimo == result.per_promedio == result.per_maximo == pytest.approx(30.0)
    # Ambos peers descartados caen en "sin_dato": ORCL por metrics=None,
    # CRM por earningsYield=None (presente pero no numérico/utilizable).
    assert result.peers_pe == {"MSFT": pytest.approx(30.0)}
    assert result.peers_no_usados == {"ORCL": "sin_dato", "CRM": "sin_dato"}


async def test_get_peer_pe_average_ningun_peer_disponible_minimo_y_maximo_none():
    async def fake_get_metrics(ticker: str):
        return None

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_minimo is None
    assert result.per_maximo is None


async def test_get_peer_pe_average_excluye_propio_ticker():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "CRM": {"earningsYield": 1 / 34.0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="ORCL"
    )
    assert "ORCL" not in result.peers_usados
    assert result.per_promedio == pytest.approx(32.0)


async def test_get_peer_pe_average_peer_sin_metrics_se_descarta():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": None},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]
    assert result.peers_no_usados == {"ORCL": "sin_dato", "CRM": "sin_dato"}


async def test_get_peer_pe_average_earnings_yield_no_positivo_se_descarta():
    """Peer con utilidades negativas/nulas (earningsYield <= 0) se excluye
    en vez de invertirse a un PER negativo sin sentido."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": -0.02},
        "CRM": {"earningsYield": 0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]
    # P5: earningsYield negativo (ORCL) y earningsYield == 0 exacto (CRM)
    # caen en el mismo motivo "earnings_yield_no_positivo" (sin 3er motivo
    # para distinguir "cero" de "negativo").
    assert result.peers_no_usados == {
        "ORCL": "earnings_yield_no_positivo",
        "CRM": "earnings_yield_no_positivo",
    }


async def test_get_peer_pe_average_ningun_peer_disponible():
    async def fake_get_metrics(ticker: str):
        return None

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio is None
    assert result.peers_usados == []


# ---------------------------------------------------------------------------
# Spec: SDD_procedencia_peers_individuales — PER individual por peer
# (peers_pe) + motivo específico por peer descartado (peers_no_usados).
# ---------------------------------------------------------------------------


async def test_get_peer_pe_average_caso_mixto_motivo_distinguido():
    """P2: 1/3 válido, 1 fallido por metrics=None ("sin_dato") y 1 fallido
    por earningsYield<=0 ("earnings_yield_no_positivo") — caso mixto."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": -0.01},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.peers_pe == {"MSFT": pytest.approx(30.0)}
    assert result.peers_no_usados == {
        "ORCL": "sin_dato",
        "CRM": "earnings_yield_no_positivo",
    }


async def test_get_peer_pe_average_earnings_yield_ausente_vs_no_numerico_ambos_sin_dato():
    """P3/P4: earningsYield ausente (key no existe) y earningsYield presente
    pero no numérico (string) se clasifican ambos como "sin_dato", nunca
    como "earnings_yield_no_positivo" — test explícito que distingue ambos
    casos del motivo financiero real."""
    metrics_ausente = {
        "MSFT": {},  # key "earningsYield" no existe
        "ORCL": {"earningsYield": 1 / 30.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics_ausente(ticker: str):
        return metrics_ausente.get(ticker)

    result_ausente = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics_ausente, sector="Technology", own_ticker="AAPL"
    )
    assert result_ausente.peers_no_usados == {"MSFT": "sin_dato"}
    assert "MSFT" not in result_ausente.peers_pe

    metrics_no_numerico = {
        "MSFT": {"earningsYield": "N/A"},  # no numérico
        "ORCL": {"earningsYield": 1 / 30.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics_no_numerico(ticker: str):
        return metrics_no_numerico.get(ticker)

    result_no_numerico = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics_no_numerico, sector="Technology", own_ticker="AAPL"
    )
    assert result_no_numerico.peers_no_usados == {"MSFT": "sin_dato"}
    assert "MSFT" not in result_no_numerico.peers_pe


async def test_get_peer_pe_average_cero_de_tres_validos_motivo_mixto():
    """P6: 0/3 peers válidos, motivo mixto -> peers_pe vacío, los 3
    candidatos en peers_no_usados con su motivo correcto."""
    metrics = {
        "MSFT": None,
        "ORCL": {"earningsYield": 0},
        "CRM": {"earningsYield": "bad"},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.peers_pe == {}
    assert result.peers_no_usados == {
        "MSFT": "sin_dato",
        "ORCL": "earnings_yield_no_positivo",
        "CRM": "sin_dato",
    }


async def test_get_peer_pe_average_peers_no_usados_nunca_incluye_ticker_propio():
    """P7: el ticker propio nunca aparece en peers_no_usados ni en peers_pe,
    ya excluido por get_peers_for_sector."""
    metrics = {
        "MSFT": None,
        "CRM": None,
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="ORCL"
    )
    assert "ORCL" not in result.peers_no_usados
    assert "ORCL" not in result.peers_pe


async def test_get_peer_pe_average_regresion_numerica_sin_cambios():
    """P8: per_promedio/per_minimo/per_maximo/peers_usados calculan
    exactamente lo mismo que antes de esta spec — el único cambio es que
    ahora también se guardan peers_pe/peers_no_usados."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": -0.01},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.per_minimo == pytest.approx(30.0)
    assert result.per_maximo == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]


async def test_get_peer_pe_average_preserva_orden_de_peers_by_sector():
    """P9: el orden de las claves de peers_pe/peers_no_usados respeta el
    orden de PEERS_BY_SECTOR["Technology"] (MSFT, ORCL, CRM), no un orden
    alfabético (que sería CRM, MSFT, ORCL)."""
    metrics_validos = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": 1 / 34.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics_validos(ticker: str):
        return metrics_validos.get(ticker)

    result_validos = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics_validos, sector="Technology", own_ticker="AAPL"
    )
    assert list(result_validos.peers_pe.keys()) == ["MSFT", "ORCL", "CRM"]

    metrics_fallidos = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": 0},
    }

    async def fake_get_metrics_fallidos(ticker: str):
        return metrics_fallidos.get(ticker)

    result_fallidos = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics_fallidos, sector="Technology", own_ticker="AAPL"
    )
    assert list(result_fallidos.peers_no_usados.keys()) == ["ORCL", "CRM"]


# ---------------------------------------------------------------------------
# Spec: SDD_peers_dinamicos_y_eventos_corporativos (Parte 1) — peers dinámicos
# vía Finnhub, inyectados mediante get_dynamic_peers_fn. Matriz P1-P12.
# ---------------------------------------------------------------------------


def _all_valid_metrics_fn(tickers: list[str]):
    """Cada ticker en `tickers` devuelve un earningsYield distinto y válido
    (1/30, 1/31, ...) — suficiente para que get_peer_pe_average acepte todos
    como válidos, sin necesitar fixtures elaborados por test."""
    metrics = {t: {"earningsYield": 1.0 / (30.0 + i)} for i, t in enumerate(tickers)}

    async def fn(ticker: str):
        return metrics.get(ticker)

    return fn


async def test_get_peer_pe_average_dinamico_minimo_exacto_3_tickers():
    """P1: exactamente 3 tickers (mínimo exacto) -> se usa la lista dinámica."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(dynamic_tickers),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert set(result.peers_usados) == set(dynamic_tickers)


async def test_get_peer_pe_average_dinamico_4_tickers():
    """P2: 4 tickers -> se usan los 4."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM", "AVGO"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(dynamic_tickers),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert set(result.peers_usados) == set(dynamic_tickers)


async def test_get_peer_pe_average_dinamico_5_tickers_tope_exacto_sin_recorte():
    """P3: 5 tickers (tope exacto) -> ninguno se recorta."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM", "AVGO", "TXN"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(dynamic_tickers),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert set(result.peers_usados) == set(dynamic_tickers)


async def test_get_peer_pe_average_dinamico_6_tickers_se_recorta_a_5():
    """P4: 6 tickers -> se recorta a los primeros 5 (MAX_PEERS_DINAMICOS)."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM", "AVGO", "TXN", "MU"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(dynamic_tickers),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert set(result.peers_usados) == set(dynamic_tickers[:5])
    assert "MU" not in result.peers_usados


async def test_get_peer_pe_average_dinamico_1_ticker_no_se_usa():
    """P5: 1 ticker (por debajo del mínimo de 3) -> cae al respaldo fijo."""
    async def fake_dynamic(own_ticker):
        return ["NVDA"]

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(["NVDA", "MSFT", "ORCL", "CRM"]),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FIJO
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}


async def test_get_peer_pe_average_dinamico_2_tickers_no_se_usa():
    """P6: 2 tickers (por debajo del mínimo de 3) -> cae al respaldo fijo.
    Caso distinto de P5 (no colapsar ambos en un solo test)."""
    async def fake_dynamic(own_ticker):
        return ["NVDA", "AMD"]

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(["NVDA", "AMD", "MSFT", "ORCL", "CRM"]),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FIJO
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}


async def test_get_peer_pe_average_dinamico_lista_vacia_cae_a_fijo():
    """P7: [] -> cae al respaldo fijo. Caso distinto de P5/P6 (vacía vs corta)."""
    async def fake_dynamic(own_ticker):
        return []

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(["MSFT", "ORCL", "CRM"]),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FIJO
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}


async def test_get_peer_pe_average_dinamico_incluye_propio_ticker_limite_exacto():
    """P8: Finnhub devuelve 4 tickers incluyendo el propio -> se filtra
    primero -> quedan 3 -> SÍ se acepta (límite exacto)."""
    async def fake_dynamic(own_ticker):
        return ["AAPL", "NVDA", "AMD", "QCOM"]

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(["NVDA", "AMD", "QCOM"]),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert "AAPL" not in result.peers_usados
    assert set(result.peers_usados) == {"NVDA", "AMD", "QCOM"}


async def test_get_peer_pe_average_dinamico_filtra_propio_antes_de_topar():
    """P9: 7 tickers con el propio en posición intermedia -> se filtra el
    propio primero (quedan 6), luego se recorta a 5 -- el resultado son los
    primeros 5 de la lista YA filtrada, no los primeros 5 de la lista cruda."""
    async def fake_dynamic(own_ticker):
        return ["NVDA", "AMD", "AAPL", "QCOM", "AVGO", "TXN", "MU"]

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_all_valid_metrics_fn(
            ["NVDA", "AMD", "QCOM", "AVGO", "TXN", "MU"]
        ),
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert "AAPL" not in result.peers_usados
    # Lista cruda filtrada: [NVDA, AMD, QCOM, AVGO, TXN, MU] -> top 5:
    # [NVDA, AMD, QCOM, AVGO, TXN] -> MU queda afuera.
    assert set(result.peers_usados) == {"NVDA", "AMD", "QCOM", "AVGO", "TXN"}
    assert "MU" not in result.peers_usados


async def test_get_peer_pe_average_dinamico_none_comportamiento_identico_a_hoy():
    """P10: get_dynamic_peers_fn=None -> comportamiento idéntico al de antes
    de esta spec, fuente_peers == PEERS_FUENTE_FIJO."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": 1 / 34.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics,
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=None,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FIJO
    assert result.per_promedio == pytest.approx(32.0)


async def test_get_peer_pe_average_dinamico_cero_de_n_validos_mantiene_fuente_finnhub():
    """P11: la fuente dinámica se acepta (3-5 candidatos), pero ninguno
    devuelve un earningsYield utilizable -> per_promedio is None,
    peers_usados == [], PERO fuente_peers sigue siendo FINNHUB (la fuente
    de candidatos y la validez del PER son ejes independientes)."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    async def fake_get_metrics(ticker: str):
        return None  # ningún candidato aporta dato utilizable

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics,
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.per_promedio is None
    assert result.peers_usados == []
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert set(result.peers_no_usados.keys()) == set(dynamic_tickers)


async def test_get_peer_pe_average_dinamico_regresion_peers_pe_y_no_usados():
    """P12: peers_pe/peers_no_usados con motivo, usando get_dynamic_peers_fn
    en vez de la lista fija -- confirma que la Parte 1 no reinventa esa
    lógica (reutiliza los motivos PEER_MOTIVO_SIN_DATO/
    PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO ya existentes)."""
    dynamic_tickers = ["NVDA", "AMD", "QCOM"]

    async def fake_dynamic(own_ticker):
        return dynamic_tickers

    metrics = {
        "NVDA": {"earningsYield": 1 / 30.0},
        "AMD": None,
        "QCOM": {"earningsYield": -0.01},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics,
        sector="Technology",
        own_ticker="AAPL",
        get_dynamic_peers_fn=fake_dynamic,
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FINNHUB
    assert result.peers_pe == {"NVDA": pytest.approx(30.0)}
    assert result.peers_no_usados == {
        "AMD": "sin_dato",
        "QCOM": "earnings_yield_no_positivo",
    }


def test_peer_average_result_fuente_peers_default_es_fijo():
    """Default de PeerAverageResult.fuente_peers preserva el comportamiento
    de cualquier instanciación existente que no pase este campo."""
    result = peers.PeerAverageResult(
        per_promedio=None, per_minimo=None, per_maximo=None, peers_usados=[]
    )
    assert result.fuente_peers == peers.PEERS_FUENTE_FIJO
