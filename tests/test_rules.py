"""Tests de `rules.py` — ratios financieros + pilares de buena empresa.

Incluye las guardas B3 (pasivos circulantes = 0) y B4 (EPS TTM <= 0) del
Spec Patch [Iter-2].
"""

from __future__ import annotations

import pytest

from investbot import rules


def test_calculate_eps():
    assert rules.calculate_eps(net_income=1_000_000, shares_outstanding=500_000) == 2.0


def test_calculate_eps_sin_acciones():
    assert rules.calculate_eps(net_income=1_000_000, shares_outstanding=0) is None
    assert rules.calculate_eps(net_income=1_000_000, shares_outstanding=None) is None


def test_calculate_gross_margin():
    assert rules.calculate_gross_margin(revenue=100, cost_of_revenue=60) == pytest.approx(0.4)


def test_calculate_gross_margin_revenue_cero():
    assert rules.calculate_gross_margin(revenue=0, cost_of_revenue=10) is None


# ---------------------------------------------------------------------------
# B3 — Ratio de Liquidez, Pasivos Circulantes = 0
# ---------------------------------------------------------------------------


def test_ratio_liquidez_pasivos_circulantes_cero():
    result = rules.calculate_liquidity_ratio(current_assets=100, current_liabilities=0)
    assert result.ratio_liquidez is None
    assert result.liquidez_sin_pasivos_circulantes is True


def test_ratio_liquidez_caso_normal():
    result = rules.calculate_liquidity_ratio(current_assets=200, current_liabilities=100)
    assert result.ratio_liquidez == pytest.approx(2.0)
    assert result.liquidez_sin_pasivos_circulantes is False


def test_pilar_deuda_controlada_sin_pasivos_circulantes():
    liquidity = rules.calculate_liquidity_ratio(current_assets=100, current_liabilities=0)
    pillars = rules.evaluate_pillars(
        revenue_historial=[10, 20, 30],
        net_income_historial=[1, 2, 3],
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.deuda_controlada is True


def test_pilar_deuda_controlada_liquidez_normal_menor_a_1():
    liquidity = rules.calculate_liquidity_ratio(current_assets=50, current_liabilities=100)
    pillars = rules.evaluate_pillars(
        revenue_historial=[10, 20, 30],
        net_income_historial=[1, 2, 3],
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.deuda_controlada is False


# ---------------------------------------------------------------------------
# B4 — PER, EPS TTM <= 0
# ---------------------------------------------------------------------------


def test_per_eps_cero():
    result = rules.calculate_per(price=100, eps_ttm=0)
    assert result.per is None
    assert result.per_no_aplicable is True


def test_per_eps_negativo():
    result = rules.calculate_per(price=100, eps_ttm=-1.5)
    assert result.per is None
    assert result.per_no_aplicable is True


def test_per_caso_normal():
    result = rules.calculate_per(price=100, eps_ttm=5)
    assert result.per == pytest.approx(20.0)
    assert result.per_no_aplicable is False


def test_ps_se_muestra_cuando_eps_no_positivo():
    ps = rules.calculate_ps(market_cap=1_000_000, revenue=500_000)
    assert ps == pytest.approx(2.0)


def test_ps_revenue_no_positivo():
    assert rules.calculate_ps(market_cap=1_000_000, revenue=0) is None
    assert rules.calculate_ps(market_cap=1_000_000, revenue=-1) is None


# ---------------------------------------------------------------------------
# Pilares — crecimiento de ingresos/utilidades
# ---------------------------------------------------------------------------


def test_pilar_ingresos_crecientes_true():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=[10, 20, 30, 40, 50],
        net_income_historial=[1, 2, 3, 4, 5],
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.ingresos_crecientes is True
    assert pillars.utilidades_crecientes is True


def test_pilar_ingresos_decrecientes_false():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=[50, 40, 30, 20, 10],
        net_income_historial=[5, 4, 3, 2, 1],
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.ingresos_crecientes is False
    assert pillars.utilidades_crecientes is False


def test_pilar_utilidades_crecientes_pero_negativas_al_final_false():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=[10, 20, 30],
        net_income_historial=[-5, -3, -1],  # crece pero sigue en pérdidas
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.utilidades_crecientes is False


def test_pilar_ventaja_competitiva_siempre_revisar_manualmente():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=[10, 20],
        net_income_historial=[1, 2],
        liquidity=liquidity,
        barata=None,
    )
    assert pillars.ventaja_competitiva == "revisar_manualmente"
    assert pillars.precio_razonable is None


# ---------------------------------------------------------------------------
# extract_key_metrics_extras (SDD_contenido_financiero_explicado.md, Decisión #2)
# ---------------------------------------------------------------------------


def _full_metrics(**overrides):
    metrics = {
        "roe": 0.35,
        "debtToEquity": 0.42,
        "netDebtToEBITDA": 0.15,
        "dividendYield": 0.006,
        "payoutRatio": 0.25,
    }
    metrics.update(overrides)
    return metrics


def test_extract_key_metrics_extras_happy_path_5_campos_presentes():
    result = rules.extract_key_metrics_extras(_full_metrics())
    assert result.roe == pytest.approx(0.35)
    assert result.debt_to_equity == pytest.approx(0.42)
    assert result.net_debt_to_ebitda == pytest.approx(0.15)
    assert result.dividend_yield == pytest.approx(0.006)
    assert result.payout_ratio == pytest.approx(0.25)


def test_extract_key_metrics_extras_metrics_none():
    result = rules.extract_key_metrics_extras(None)
    assert result == rules.KeyMetricsExtras(
        roe=None, debt_to_equity=None, net_debt_to_ebitda=None,
        dividend_yield=None, payout_ratio=None,
    )


def test_extract_key_metrics_extras_metrics_dict_vacio():
    result = rules.extract_key_metrics_extras({})
    assert result == rules.KeyMetricsExtras(
        roe=None, debt_to_equity=None, net_debt_to_ebitda=None,
        dividend_yield=None, payout_ratio=None,
    )


def test_extract_key_metrics_extras_campo_ausente_del_dict():
    metrics = _full_metrics()
    del metrics["dividendYield"]
    result = rules.extract_key_metrics_extras(metrics)
    assert result.dividend_yield is None
    # el resto se lee normal
    assert result.roe == pytest.approx(0.35)
    assert result.debt_to_equity == pytest.approx(0.42)
    assert result.net_debt_to_ebitda == pytest.approx(0.15)
    assert result.payout_ratio == pytest.approx(0.25)


def test_extract_key_metrics_extras_campo_no_numerico():
    metrics = _full_metrics(roe="N/A")
    result = rules.extract_key_metrics_extras(metrics)
    assert result.roe is None


def test_extract_key_metrics_extras_dividend_yield_cero_no_es_none():
    metrics = _full_metrics(dividendYield=0)
    result = rules.extract_key_metrics_extras(metrics)
    assert result.dividend_yield == 0
    assert result.dividend_yield is not None


def test_extract_key_metrics_extras_payout_ratio_mayor_a_1_se_propaga():
    metrics = _full_metrics(payoutRatio=1.5)
    result = rules.extract_key_metrics_extras(metrics)
    assert result.payout_ratio == pytest.approx(1.5)


def test_extract_key_metrics_extras_debt_to_equity_negativo_se_propaga():
    metrics = _full_metrics(debtToEquity=-0.8)
    result = rules.extract_key_metrics_extras(metrics)
    assert result.debt_to_equity == pytest.approx(-0.8)
