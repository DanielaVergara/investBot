"""Tests puntuales para cerrar huecos de cobertura en ramas de error/edge
cases de `valuation.py`, `rules.py`, `summary.py`, `onboarding.py`,
`fmp_client.py` y `treasury_client.py`.
"""

from __future__ import annotations

import httpx
import pytest

from investbot import fmp_client, rules, summary, treasury_client, valuation


# --- valuation.py ---


def test_calculate_cagr_valor_antiguo_none():
    assert valuation.calculate_cagr(valor_reciente=5, valor_antiguo=None, n_años=5) is None


def test_calculate_cagr_valor_reciente_none():
    assert valuation.calculate_cagr(valor_reciente=None, valor_antiguo=5, n_años=5) is None


def test_multiplos_per_peers_none_pero_eps_positivo():
    assert valuation.calculate_multiplos_fair_value(eps_ttm=10, per_promedio_peers=None) is None


def test_wacc_total_debt_negativo():
    assert (
        valuation.calculate_wacc(
            interest_expense=10,
            total_debt=-100,
            income_tax_expense=1,
            income_before_tax=10,
            beta=1.0,
            y=0.04,
            market_cap=1000,
        )
        is None
    )


def test_dcf_shares_outstanding_invalido():
    assert (
        valuation.calculate_dcf_fair_value(
            fcf_historial=[100, 110, 120, 130, 140],
            wacc=0.10,
            shares_outstanding=0,
        )
        == valuation._DCF_NO_CALCULABLE
    )


def test_valuation_result_as_dict_vacio():
    result = valuation.ValuationResult()
    d = result.as_dict()
    assert d["valor_justo_total"] is None
    assert d["modelos_excluidos"] == []


def test_valuation_multiplos_excluido_por_peers_no_disponibles():
    """eps_ttm > 0 pero no hay PER de peers disponible -> multiplos None con motivo específico."""
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=None,
        fcf_historial=[80, 90, 100, 110, 120],
        y=0.044,
        wacc_inputs={
            "interest_expense": 10,
            "total_debt": 100,
            "income_tax_expense": 2,
            "income_before_tax": 20,
            "beta": 1.0,
            "market_cap": 10_000,
        },
        shares_outstanding=1000,
    )
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("multiplos") == "per_peers_no_disponible"


def test_valuation_graham_excluido_por_y_no_disponible():
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=20.0,
        fcf_historial=[80, 90, 100, 110, 120],
        y=None,  # Y no disponible (FRED y Treasury.gov fallaron)
        wacc_inputs={
            "interest_expense": 10,
            "total_debt": 100,
            "income_tax_expense": 2,
            "income_before_tax": 20,
            "beta": 1.0,
            "market_cap": 10_000,
        },
        shares_outstanding=1000,
    )
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("graham") == "y_no_disponible"
    assert motivos.get("dcf") == "wacc_no_calculable"


def test_valuation_dcf_excluido_por_wacc_no_calculable():
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=20.0,
        fcf_historial=[80, 90, 100, 110, 120],
        y=0.044,
        wacc_inputs={
            "interest_expense": 10,
            "total_debt": 0,
            "income_tax_expense": 2,
            "income_before_tax": 20,
            "beta": 1.0,
            "market_cap": 0,  # market_cap inválido -> WACC no calculable
        },
        shares_outstanding=1000,
    )
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("dcf") == "wacc_no_calculable"


def test_valuation_dcf_excluido_por_dcf_no_calculable_shares_invalidas():
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=20.0,
        fcf_historial=[80, 90, 100, 110, 120],
        y=0.044,
        wacc_inputs={
            "interest_expense": 10,
            "total_debt": 100,
            "income_tax_expense": 2,
            "income_before_tax": 20,
            "beta": 1.0,
            "market_cap": 10_000,
        },
        shares_outstanding=0,  # invalida el cálculo final de DCF por acción
    )
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("dcf") == "dcf_no_calculable"


def test_valuation_graham_excluido_por_fcf_reciente_no_positivo_motivo():
    """Cubre la rama `reciente <= 0` de `_motivo_cagr_invalido` vía el CAGR de FCF."""
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=20.0,
        fcf_historial=[80, 90, 100, 110, -5],  # reciente negativo, base positiva
        y=0.044,
        wacc_inputs={
            "interest_expense": 10,
            "total_debt": 100,
            "income_tax_expense": 2,
            "income_before_tax": 20,
            "beta": 1.0,
            "market_cap": 10_000,
        },
        shares_outstanding=1000,
    )
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("dcf") == "fcf_reciente_no_positivo"


# --- rules.py ---


def test_es_creciente_lista_vacia_o_un_elemento():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=[],
        net_income_historial=[5],
        liquidity=liquidity,
        barata=None,
    )
    assert pillars.ingresos_crecientes is False
    assert pillars.utilidades_crecientes is False


# --- summary.py ---


def test_summary_pilar_indefinido_muestra_neutro():
    pillars = {
        "ingresos_crecientes": True,
        "utilidades_crecientes": True,
        "deuda_controlada": True,
        "precio_razonable": None,  # ningún modelo de valoración calculable
    }
    text = summary.build_pillars_section(pillars)
    assert "➖" in text


# --- onboarding.py ---


async def test_onboarding_callback_data_invalido_no_avanza(handler_factory):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from investbot import onboarding

    conv_handler = handler_factory
    context = SimpleNamespace(user_data={})
    entry_callback = conv_handler.entry_points[0].callback
    start_update = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=1, type="private"),
    )
    state = await entry_callback(start_update, context)

    bad_update = SimpleNamespace()
    bad_query = SimpleNamespace(data="malformed", answer=AsyncMock(), edit_message_text=AsyncMock())
    bad_update.callback_query = bad_query
    callback = conv_handler.states[state][0].callback
    result_state = await callback(bad_update, context)
    assert result_state == state  # se queda en el mismo estado, no avanza
    assert context.user_data.get("respuestas") == []


@pytest.fixture
def handler_factory(tmp_path):
    from investbot import db, onboarding

    db_path = str(tmp_path / "edge.db")

    def get_conn():
        return db.get_connection(db_path)

    return onboarding.build_onboarding_handler(get_conn)


# --- fmp_client.py ---


async def test_get_profile_lista_vacia_devuelve_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await fmp_client.get_profile(client, "test-key", "NOPE") is None


# --- treasury_client.py ---


async def test_fred_valor_no_parseable_continua_buscando():
    data = {
        "observations": [
            {"date": "x", "value": "no-numerico"},
            {"date": "y", "value": "4.30"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    y = await treasury_client.get_treasury_yield_fred(client, "test-key")
    assert y == pytest.approx(0.043)


def test_parse_treasury_gov_csv_valor_no_parseable_continua():
    # `_parse_treasury_gov_csv` recorre las filas en reversa (última fecha
    # primero) — la última fila del CSV tiene un valor no numérico, así que
    # debe descartarla (`continue`) y caer en la fila anterior (numérica).
    csv_text = (
        "Date,20 Yr\n"
        "07/23/2026,4.40\n"
        "07/24/2026,no-numerico\n"
    )
    value = treasury_client._parse_treasury_gov_csv(csv_text)
    assert value == pytest.approx(0.044)


async def test_treasury_gov_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_treasury_gov(client, year=2026)
