"""F1b-4 (SDD_fuentes_datos_gratuitas.md 4.10 pts 3-5, D-8, D-20, CS-28): un dato ausente es N/D,
nunca 0. Unit sobre `rules` y `valuation` (sin red)."""

from __future__ import annotations

import math

import pytest

from investbot import rules, valuation


def _q(**kw):
    base = {
        "netIncome": 10.0, "revenue": 100.0, "costOfRevenue": 60.0, "interestExpense": 2.0,
        "incomeTaxExpense": 1.0, "incomeBeforeTax": 12.0, "weightedAverageShsOutDil": 5.0,
    }
    base.update(kw)
    return base


def _wacc(**kw):
    args = dict(interest_expense=5.0, total_debt=100.0, income_tax_expense=2.0,
                income_before_tax=10.0, beta=1.0, y=0.04, market_cap=900.0)
    args.update(kw)
    return valuation.calculate_wacc(**args)


# --- CA-20 -------------------------------------------------------------------------

@pytest.mark.parametrize("activos, pasivos", [(None, None), (None, 5.0), (5.0, None), (None, 0)])
def test_fdg_ca20_liquidez_con_entradas_none_es_nd_y_no_afirma_sin_pasivos(activos, pasivos):
    r = rules.calculate_liquidity_ratio(activos, pasivos)
    assert r.ratio_liquidez is None and r.liquidez_sin_pasivos_circulantes is False


def test_fdg_ca20_liquidez_el_pilar_de_deuda_controlada_queda_en_falso_sin_crash():
    liq = rules.calculate_liquidity_ratio(None, None)
    p = rules.evaluate_pillars(revenue_historial=[1, 2], net_income_historial=[1, 2], liquidity=liq, barata=None)
    assert p.deuda_controlada is False


def test_fdg_ca20_liquidez_un_cero_reportado_sigue_siendo_valido():
    assert rules.calculate_liquidity_ratio(10.0, 0).liquidez_sin_pasivos_circulantes is True
    r = rules.calculate_liquidity_ratio(0, 5.0)
    assert r.ratio_liquidez == 0 and r.liquidez_sin_pasivos_circulantes is False


def test_fdg_ca20_margen_bruto_con_costo_none_es_none_y_costo_cero_reportado_es_valido():
    assert rules.calculate_gross_margin(100.0, None) is None
    assert rules.calculate_gross_margin(None, 5.0) is None
    assert rules.calculate_gross_margin(100.0, 0) == 1.0
    assert rules.calculate_gross_margin(100.0, 60.0) == pytest.approx(0.4)


def test_fdg_ca20_wacc_con_deuda_none_devuelve_none_sin_excepcion():
    assert _wacc(total_debt=None) is None
    assert _wacc(total_debt=None, interest_expense=None) is None


def test_fdg_ca20_wacc_con_deuda_e_intereses_ausentes_es_none_y_con_cero_reportado_calcula():
    assert _wacc(interest_expense=None) is None  # deuda > 0 sin intereses: no hay Kd
    assert _wacc(interest_expense=0) is not None  # 0 reportado explicitamente: valido
    ke = 0.04 + 1.0 * valuation.MARKET_RISK_PREMIUM
    assert _wacc(interest_expense=0) == pytest.approx(0.9 * ke)
    assert _wacc(total_debt=0, interest_expense=None) == pytest.approx(ke)  # sin deuda reportada


# --- CS-28 / T-CS-28 --------------------------------------------------------------------

def test_fdg_tcs28_costo_de_deuda_045_devuelve_wacc_none():
    assert _wacc(interest_expense=45.0, total_debt=100.0) is None


@pytest.mark.parametrize(
    "interes, esperado_none",
    [(30.0, False), (30.01, True), (29.99, False), (math.inf, True), (math.nan, True)],
)
def test_fdg_tcs28_umbral_exacto_de_030(interes, esperado_none):
    assert (_wacc(interest_expense=interes) is None) is esperado_none


# --- D-8: interestExpense opcional dentro del paquete TTM --------------------------------

@pytest.mark.parametrize("faltante", [{0: None}, {2: None}])
def test_fdg_ca19_ttm_sin_intereses_sigue_disponible_con_el_resto_del_paquete(faltante):
    q = [_q() for _ in range(4)]
    for i in faltante:
        q[i] = _q(interestExpense=None)
    r = rules.calculate_income_statement_ttm(q)
    assert r.disponible is True and r.interest_expense_ttm is None
    assert r.net_income_ttm == 40.0 and r.revenue_ttm == 400.0 and r.shares_outstanding_reciente == 5.0


def test_fdg_ca19_ttm_con_clave_ausente_y_con_intereses_completos_o_cero_reportado():
    sin_clave = [{k: v for k, v in _q().items() if k != "interestExpense"} for _ in range(4)]
    assert rules.calculate_income_statement_ttm(sin_clave).interest_expense_ttm is None
    assert rules.calculate_income_statement_ttm([_q() for _ in range(4)]).interest_expense_ttm == 8.0
    ceros = rules.calculate_income_statement_ttm([_q(interestExpense=0) for _ in range(4)])
    assert ceros.interest_expense_ttm == 0 and ceros.interest_expense_ttm is not None


def test_fdg_ca19_ttm_los_demas_campos_siguen_siendo_atomicos():
    q = [_q() for _ in range(4)]
    q[1] = _q(revenue=None, interestExpense=None)
    assert rules.calculate_income_statement_ttm(q).disponible is False
