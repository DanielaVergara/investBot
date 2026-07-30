"""Tests de `market_context.py` — momentum de precio + comparación con peers.

Spec Patch [Iter-3] sección 6 + [Iter-4] C2 (momentum, un solo promedio móvil
ausente) y C3 (comparación con 1 solo peer válido).
"""

from __future__ import annotations

import dataclasses

import pytest

from investbot import market_context


# ---------------------------------------------------------------------------
# calculate_momentum
# ---------------------------------------------------------------------------


def test_momentum_impulso_positivo():
    result = market_context.calculate_momentum(
        price=200.0, year_high=210.0, year_low=150.0, price_avg_50=180.0, price_avg_200=170.0
    )
    assert result.etiqueta == "impulso_positivo"
    assert result.pct_vs_avg_50 == pytest.approx((200.0 - 180.0) / 180.0 * 100)
    assert result.pct_vs_avg_200 == pytest.approx((200.0 - 170.0) / 170.0 * 100)
    assert result.pct_vs_year_high == pytest.approx((200.0 - 210.0) / 210.0 * 100)
    assert result.pct_vs_year_low == pytest.approx((200.0 - 150.0) / 150.0 * 100)


def test_momentum_impulso_negativo():
    result = market_context.calculate_momentum(
        price=100.0, year_high=210.0, year_low=90.0, price_avg_50=120.0, price_avg_200=130.0
    )
    assert result.etiqueta == "impulso_negativo"


def test_momentum_mixto():
    """Por encima del promedio de 50 días pero todavía por debajo del de 200 días."""
    result = market_context.calculate_momentum(
        price=150.0, year_high=210.0, year_low=90.0, price_avg_50=140.0, price_avg_200=170.0
    )
    assert result.etiqueta == "mixto"


def test_momentum_no_disponible_faltan_ambos():
    result = market_context.calculate_momentum(
        price=150.0, year_high=210.0, year_low=90.0, price_avg_50=None, price_avg_200=None
    )
    assert result.etiqueta == "no_disponible"
    assert result.pct_vs_avg_50 is None
    assert result.pct_vs_avg_200 is None
    # Los datos que sí están presentes (year_high/year_low) igual se calculan.
    assert result.pct_vs_year_high is not None
    assert result.pct_vs_year_low is not None


def test_momentum_no_disponible_falta_avg_50():
    """Spec Patch Iter-4, C2: falta solo uno de los dos promedios (empresa
    con 50-199 días de historial de cotización) -> también "no_disponible"."""
    result = market_context.calculate_momentum(
        price=150.0, year_high=210.0, year_low=90.0, price_avg_50=None, price_avg_200=140.0
    )
    assert result.etiqueta == "no_disponible"
    assert result.pct_vs_avg_50 is None
    # El dato presente (avg_200) sí se calcula, aunque la etiqueta sea "no_disponible".
    assert result.pct_vs_avg_200 == pytest.approx((150.0 - 140.0) / 140.0 * 100)


def test_momentum_no_disponible_falta_avg_200():
    """Spec Patch Iter-4, C2: falta solo el promedio de 200 días."""
    result = market_context.calculate_momentum(
        price=150.0, year_high=210.0, year_low=90.0, price_avg_50=140.0, price_avg_200=None
    )
    assert result.etiqueta == "no_disponible"
    assert result.pct_vs_avg_200 is None
    assert result.pct_vs_avg_50 == pytest.approx((150.0 - 140.0) / 140.0 * 100)


# ---------------------------------------------------------------------------
# compare_to_peers
# ---------------------------------------------------------------------------


def test_compare_to_peers_mas_barata():
    result = market_context.compare_to_peers(
        per_propio=20.0,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.posicion == "mas_barata"
    assert result.motivo_no_comparable is None


def test_compare_to_peers_en_linea():
    result = market_context.compare_to_peers(
        per_propio=28.4,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.posicion == "en_linea"
    assert result.motivo_no_comparable is None


def test_compare_to_peers_mas_cara():
    result = market_context.compare_to_peers(
        per_propio=40.0,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.posicion == "mas_cara"
    assert result.motivo_no_comparable is None


def test_compare_to_peers_no_comparable_eps_negativo():
    result = market_context.compare_to_peers(
        per_propio=None,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.posicion == "no_comparable"
    assert result.motivo_no_comparable == "eps_no_positivo"


def test_compare_to_peers_no_comparable_sin_peers_validos():
    result = market_context.compare_to_peers(
        per_propio=20.0,
        per_minimo_peers=None,
        per_promedio_peers=None,
        per_maximo_peers=None,
        peers_usados=[],
    )
    assert result.posicion == "no_comparable"
    assert result.motivo_no_comparable == "sin_peers_validos"


@pytest.mark.parametrize("per_propio", [20.0, 27.9, 40.0])
def test_compare_to_peers_no_comparable_un_solo_peer_valido(per_propio):
    """Spec Patch Iter-4, C3: con 1 solo peer válido, per_minimo==per_promedio
    ==per_maximo (mismo valor) — nunca "mas_barata"/"en_linea"/"mas_cara",
    independientemente de dónde caiga per_propio."""
    result = market_context.compare_to_peers(
        per_propio=per_propio,
        per_minimo_peers=27.9,
        per_promedio_peers=27.9,
        per_maximo_peers=27.9,
        peers_usados=["MSFT"],
    )
    assert result.posicion == "no_comparable"
    assert result.motivo_no_comparable == "un_solo_peer_valido"


# ---------------------------------------------------------------------------
# compare_to_peers — peers_pe / peers_no_usados (SDD_procedencia_peers_individuales)
# ---------------------------------------------------------------------------

_PEERS_PE_FIXTURE = {"ORCL": 24.3}
_PEERS_NO_USADOS_FIXTURE = {"MSFT": "sin_dato", "CRM": "earnings_yield_no_positivo"}


@pytest.mark.parametrize(
    "kwargs, posicion_esperada",
    [
        (
            dict(
                per_propio=None,
                per_minimo_peers=22.1,
                per_promedio_peers=27.9,
                per_maximo_peers=33.5,
                peers_usados=["MSFT", "ORCL", "CRM"],
            ),
            "no_comparable",  # eps_no_positivo
        ),
        (
            dict(
                per_propio=20.0,
                per_minimo_peers=None,
                per_promedio_peers=None,
                per_maximo_peers=None,
                peers_usados=[],
            ),
            "no_comparable",  # sin_peers_validos
        ),
        (
            dict(
                per_propio=20.0,
                per_minimo_peers=27.9,
                per_promedio_peers=27.9,
                per_maximo_peers=27.9,
                peers_usados=["MSFT"],
            ),
            "no_comparable",  # un_solo_peer_valido
        ),
        (
            dict(
                per_propio=28.4,
                per_minimo_peers=22.1,
                per_promedio_peers=27.9,
                per_maximo_peers=33.5,
                peers_usados=["MSFT", "ORCL", "CRM"],
            ),
            "en_linea",  # comparable
        ),
    ],
)
def test_compare_to_peers_propaga_peers_pe_y_no_usados_en_las_4_ramas(kwargs, posicion_esperada):
    """M1: las 4 ramas de retorno propagan peers_pe/peers_no_usados idénticos
    a los recibidos, incluyendo el motivo por peer, no solo nombres."""
    result = market_context.compare_to_peers(
        peers_pe=_PEERS_PE_FIXTURE,
        peers_no_usados=_PEERS_NO_USADOS_FIXTURE,
        **kwargs,
    )
    assert result.posicion == posicion_esperada
    assert result.peers_pe == _PEERS_PE_FIXTURE
    assert result.peers_no_usados == _PEERS_NO_USADOS_FIXTURE


def test_compare_to_peers_backward_compat_sin_pasar_peers_pe_ni_no_usados():
    """M2: no se pasan peers_pe/peers_no_usados (call site preexistente) ->
    ambos coercionan a {}."""
    result = market_context.compare_to_peers(
        per_propio=28.4,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.peers_pe == {}
    assert result.peers_no_usados == {}


def test_compare_to_peers_none_explicito_coerciona_a_dict_vacio():
    """M3: se pasa peers_pe=None/peers_no_usados=None explícitamente (no
    solo omitido) -> coerciona a {} igual que M2."""
    result = market_context.compare_to_peers(
        per_propio=28.4,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
        peers_pe=None,
        peers_no_usados=None,
    )
    assert result.peers_pe == {}
    assert result.peers_no_usados == {}


def test_compare_to_peers_campos_nuevos_no_condicionan_clasificacion():
    """M4: pasar peers_pe/peers_no_usados poblados en la rama
    sin_peers_validos no cambia posicion/motivo_no_comparable — son
    puramente informativos."""
    result = market_context.compare_to_peers(
        per_propio=20.0,
        per_minimo_peers=None,
        per_promedio_peers=None,
        per_maximo_peers=None,
        peers_usados=[],
        peers_pe={},
        peers_no_usados={"MSFT": "sin_dato", "ORCL": "sin_dato", "CRM": "sin_dato"},
    )
    assert result.posicion == "no_comparable"
    assert result.motivo_no_comparable == "sin_peers_validos"


# ---------------------------------------------------------------------------
# extract_vix_context (SDD_contenido_financiero_explicado.md, Decisión #8)
# ---------------------------------------------------------------------------


def test_extract_vix_context_disponible():
    result = market_context.extract_vix_context({"price": 18.42})
    assert result == market_context.VixResult(valor=18.42, disponible=True)


def test_extract_vix_context_quote_none():
    result = market_context.extract_vix_context(None)
    assert result == market_context.VixResult(valor=None, disponible=False)


def test_extract_vix_context_quote_vacio():
    result = market_context.extract_vix_context({})
    assert result == market_context.VixResult(valor=None, disponible=False)


def test_extract_vix_context_price_no_numerico():
    result = market_context.extract_vix_context({"price": "N/A"})
    assert result == market_context.VixResult(valor=None, disponible=False)


def test_extract_vix_context_price_cero_es_valido():
    """Guarda de tipo, no de rango — `0` es un dato válido (aunque sería
    anómalo financieramente, no es responsabilidad de esta función juzgarlo)."""
    result = market_context.extract_vix_context({"price": 0})
    assert result.valor == 0
    assert result.disponible is True


def test_vix_result_no_expone_clasificacion_cualitativa():
    """`VixResult` solo tiene `valor`/`disponible` — nunca una etiqueta de
    "alta"/"baja"/"moderada" volatilidad (Decisión #8, sin umbral nuevo)."""
    campos = {f.name for f in dataclasses.fields(market_context.VixResult)}
    assert campos == {"valor", "disponible"}


# ---------------------------------------------------------------------------
# compare_to_peers — fuente_peers (SDD_peers_dinamicos_y_eventos_corporativos,
# Parte 1). Matriz M1-M2.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, posicion_esperada",
    [
        (
            dict(
                per_propio=None,
                per_minimo_peers=22.1,
                per_promedio_peers=27.9,
                per_maximo_peers=33.5,
                peers_usados=["MSFT", "ORCL", "CRM"],
            ),
            "no_comparable",  # eps_no_positivo
        ),
        (
            dict(
                per_propio=20.0,
                per_minimo_peers=None,
                per_promedio_peers=None,
                per_maximo_peers=None,
                peers_usados=[],
            ),
            "no_comparable",  # sin_peers_validos
        ),
        (
            dict(
                per_propio=20.0,
                per_minimo_peers=27.9,
                per_promedio_peers=27.9,
                per_maximo_peers=27.9,
                peers_usados=["MSFT"],
            ),
            "no_comparable",  # un_solo_peer_valido
        ),
        (
            dict(
                per_propio=28.4,
                per_minimo_peers=22.1,
                per_promedio_peers=27.9,
                per_maximo_peers=33.5,
                peers_usados=["MSFT", "ORCL", "CRM"],
            ),
            "en_linea",  # comparable
        ),
    ],
)
def test_compare_to_peers_propaga_fuente_peers_finnhub_en_las_4_ramas(kwargs, posicion_esperada):
    """M1: fuente_peers="finnhub" se propaga en las 4 ramas de retorno."""
    result = market_context.compare_to_peers(fuente_peers="finnhub", **kwargs)
    assert result.posicion == posicion_esperada
    assert result.fuente_peers == "finnhub"


def test_compare_to_peers_fuente_peers_default_es_fijo_respaldo():
    """M2: sin pasar fuente_peers -> default "fijo_respaldo" (regresión de
    compatibilidad hacia atrás, mismo patrón que
    test_compare_to_peers_backward_compat_sin_pasar_peers_pe_ni_no_usados)."""
    result = market_context.compare_to_peers(
        per_propio=28.4,
        per_minimo_peers=22.1,
        per_promedio_peers=27.9,
        per_maximo_peers=33.5,
        peers_usados=["MSFT", "ORCL", "CRM"],
    )
    assert result.fuente_peers == "fijo_respaldo"
