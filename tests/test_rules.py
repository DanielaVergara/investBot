"""Tests de `rules.py` — ratios financieros + pilares de buena empresa.

Incluye las guardas B3 (pasivos circulantes = 0) y B4 (EPS TTM <= 0) del
Spec Patch [Iter-2].
"""

from __future__ import annotations

from typing import Optional

import pytest

from investbot import rules
from tests.fixtures.crecimiento_estilizado import (
    HISTORIAL_INGRESOS_CASO_ESTILIZADO,
    HISTORIAL_UTILIDADES_CASO_ESTILIZADO,
)


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


# ---------------------------------------------------------------------------
# _es_creciente — Opción A confirmada (SDD_fix_crecimiento_y_redaccion.md,
# Parte 1): solo compara extremos, ya no exige monotonía año a año.
# ---------------------------------------------------------------------------


def test_es_creciente_dip_temprano_explosion_final():
    assert rules._es_creciente([100, 90, 80, 70, 200]) is True


def test_es_creciente_caso_nvidia_estilizado():
    assert rules._es_creciente([100, 200, 150, 300, 500]) is True


def test_es_creciente_caida_sostenida_sin_repunte_false():
    assert rules._es_creciente([100, 90, 80, 70, 60]) is False


def test_es_creciente_lista_vacia_false():
    assert rules._es_creciente([]) is False


def test_es_creciente_un_solo_elemento_false():
    assert rules._es_creciente([100]) is False


def test_es_creciente_extremos_iguales_false():
    assert rules._es_creciente([100, 100]) is False


def test_es_creciente_dos_elementos_creciente_true():
    assert rules._es_creciente([100, 200]) is True


def test_pilar_utilidades_crecientes_caso_nvidia_dip_intermedio():
    """End-to-end: reproduce el bug real reportado por Daniela con NVIDIA —
    una caída intermedia de utilidades rodeada de crecimiento fuerte ya no
    marca ❌ en el pilar."""
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=HISTORIAL_INGRESOS_CASO_ESTILIZADO,
        net_income_historial=HISTORIAL_UTILIDADES_CASO_ESTILIZADO,
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.utilidades_crecientes is True
    assert pillars.ingresos_crecientes is True


def test_evaluate_pillars_deuda_precio_ventaja_no_cambian_con_dip():
    """El fix de _es_creciente no cambia ninguna otra lógica de
    evaluate_pillars (deuda_controlada, precio_razonable, ventaja_competitiva)."""
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars = rules.evaluate_pillars(
        revenue_historial=HISTORIAL_INGRESOS_CASO_ESTILIZADO,
        net_income_historial=HISTORIAL_UTILIDADES_CASO_ESTILIZADO,
        liquidity=liquidity,
        barata=False,
    )
    assert pillars.deuda_controlada is True
    assert pillars.precio_razonable is False
    assert pillars.ventaja_competitiva == "revisar_manualmente"


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


# ---------------------------------------------------------------------------
# sum_ttm_field / calculate_income_statement_ttm / calculate_fcf_ttm
# (SDD_eps_ttm_real.md, Decisión #9 + Criterios de QA sección 1)
# ---------------------------------------------------------------------------


def _quarter(**overrides) -> dict:
    base = {
        "netIncome": 1000.0,
        "revenue": 5000.0,
        "costOfRevenue": 2000.0,
        "interestExpense": 100.0,
        "incomeTaxExpense": 200.0,
        "incomeBeforeTax": 1200.0,
        "weightedAverageShsOutDil": 500.0,
        "weightedAverageShsOut": 500.0,
    }
    base.update(overrides)
    return base


def _four_quarters(per_quarter_overrides: Optional[dict] = None) -> list[dict]:
    """4 trimestres válidos, recent-first — `per_quarter_overrides[i]` (si
    está) sobreescribe el trimestre de índice `i`."""
    quarters = [_quarter() for _ in range(4)]
    for i, overrides in (per_quarter_overrides or {}).items():
        quarters[i] = _quarter(**overrides)
    return quarters


def test_sum_ttm_field_cuatro_elementos_suma_correcta():
    quarters = _four_quarters()
    assert rules.sum_ttm_field(quarters, "netIncome") == pytest.approx(4000.0)


def test_sum_ttm_field_mas_de_cuatro_usa_solo_los_primeros_4():
    quarters = _four_quarters() + [_quarter(netIncome=999_999.0)]
    assert rules.sum_ttm_field(quarters, "netIncome") == pytest.approx(4000.0)


def test_sum_ttm_field_cero_elementos_none():
    assert rules.sum_ttm_field([], "netIncome") is None


def test_sum_ttm_field_un_elemento_none():
    assert rules.sum_ttm_field([_quarter()], "netIncome") is None


def test_sum_ttm_field_tres_elementos_none():
    assert rules.sum_ttm_field([_quarter(), _quarter(), _quarter()], "netIncome") is None


def test_sum_ttm_field_campo_none_explicito():
    quarters = _four_quarters({0: {"netIncome": None}})
    assert rules.sum_ttm_field(quarters, "netIncome") is None


def test_sum_ttm_field_campo_ausente_del_dict():
    quarters = _four_quarters()
    del quarters[0]["netIncome"]
    assert rules.sum_ttm_field(quarters, "netIncome") is None


def test_sum_ttm_field_campo_string_no_numerico():
    quarters = _four_quarters({0: {"netIncome": "N/A"}})
    assert rules.sum_ttm_field(quarters, "netIncome") is None


def test_sum_ttm_field_campo_booleano_no_se_acepta_por_accidente():
    """isinstance(True, int) es True en Python — la guarda no debe colar un
    booleano como si fuera una cifra financiera válida."""
    quarters = _four_quarters({0: {"netIncome": True}})
    assert rules.sum_ttm_field(quarters, "netIncome") is None


def test_sum_ttm_field_valores_negativos_suma_igual():
    quarters = _four_quarters({i: {"netIncome": -1000.0} for i in range(4)})
    assert rules.sum_ttm_field(quarters, "netIncome") == pytest.approx(-4000.0)


def test_calculate_income_statement_ttm_happy_path_disponible():
    quarters = _four_quarters()
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is True
    assert result.net_income_ttm == pytest.approx(4000.0)
    assert result.revenue_ttm == pytest.approx(20000.0)
    assert result.cost_of_revenue_ttm == pytest.approx(8000.0)
    assert result.interest_expense_ttm == pytest.approx(400.0)
    assert result.income_tax_expense_ttm == pytest.approx(800.0)
    assert result.income_before_tax_ttm == pytest.approx(4800.0)
    assert result.shares_outstanding_reciente == pytest.approx(500.0)


def test_calculate_income_statement_ttm_menos_de_4_lista_vacia():
    result = rules.calculate_income_statement_ttm([])
    assert result.disponible is False


def test_calculate_income_statement_ttm_menos_de_4_un_elemento():
    result = rules.calculate_income_statement_ttm([_quarter()])
    assert result.disponible is False


def test_calculate_income_statement_ttm_menos_de_4_tres_elementos():
    result = rules.calculate_income_statement_ttm([_quarter(), _quarter(), _quarter()])
    assert result.disponible is False


@pytest.mark.parametrize(
    "campo",
    [
        "netIncome",
        "revenue",
        "costOfRevenue",
        "interestExpense",
        "incomeTaxExpense",
        "incomeBeforeTax",
    ],
)
def test_calculate_income_statement_ttm_campo_faltante_es_atomico(campo):
    """Diseño atómico (Decisión #9): si CUALQUIERA de los 6 campos falla en
    1 de los 4 trimestres, el paquete completo se descarta — nunca se
    mezclan fuentes campo por campo."""
    quarters = _four_quarters({0: {campo: None}})
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is False
    assert result.net_income_ttm is None


def test_calculate_income_statement_ttm_net_income_negativo_disponible_true():
    """La función TTM es agnóstica al signo — el filtro por signo vive río
    abajo (summary.py, motivo eps_ttm_no_positivo)."""
    quarters = _four_quarters({i: {"netIncome": -1000.0} for i in range(4)})
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is True
    assert result.net_income_ttm == pytest.approx(-4000.0)


def test_calculate_income_statement_ttm_shares_diluidas_cero_no_disponible():
    """`weightedAverageShsOutDil=0` con el fallback (`weightedAverageShsOut`)
    también en 0 — confirma que la guarda final `shares <= 0` atrapa el
    caso, no solo el `or` (que por sí solo evaluaría el fallback igual que
    con `None`, pero acá el fallback tampoco es válido)."""
    quarters = _four_quarters(
        {0: {"weightedAverageShsOutDil": 0, "weightedAverageShsOut": 0}}
    )
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is False


def test_calculate_income_statement_ttm_shares_diluidas_negativas_no_disponible():
    quarters = _four_quarters(
        {0: {"weightedAverageShsOutDil": -100.0, "weightedAverageShsOut": -100.0}}
    )
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is False


def test_calculate_income_statement_ttm_shares_diluidas_ausentes_usa_fallback():
    quarters = _four_quarters()
    del quarters[0]["weightedAverageShsOutDil"]
    quarters[0]["weightedAverageShsOut"] = 750.0
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is True
    assert result.shares_outstanding_reciente == pytest.approx(750.0)


def test_calculate_income_statement_ttm_ambas_shares_ausentes_no_disponible():
    quarters = _four_quarters()
    del quarters[0]["weightedAverageShsOutDil"]
    del quarters[0]["weightedAverageShsOut"]
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is False


def test_calculate_income_statement_ttm_mas_de_4_usa_solo_primeros_4():
    quarters = _four_quarters() + [_quarter(netIncome=999_999.0)]
    result = rules.calculate_income_statement_ttm(quarters)
    assert result.disponible is True
    assert result.net_income_ttm == pytest.approx(4000.0)


def test_calculate_income_statement_ttm_dicts_vacios_no_lanza_excepcion():
    result = rules.calculate_income_statement_ttm([{}, {}, {}, {}])
    assert result.disponible is False


def test_calculate_fcf_ttm_cuatro_elementos_suma():
    assert rules.calculate_fcf_ttm([100.0, 200.0, 300.0, 400.0]) == pytest.approx(1000.0)


def test_calculate_fcf_ttm_mas_de_cuatro_usa_ultimos_4():
    assert rules.calculate_fcf_ttm([0.0, 100.0, 200.0, 300.0, 400.0]) == pytest.approx(1000.0)


def test_calculate_fcf_ttm_tres_elementos_none():
    assert rules.calculate_fcf_ttm([100.0, 200.0, 300.0]) is None


def test_calculate_fcf_ttm_lista_vacia_none():
    assert rules.calculate_fcf_ttm([]) is None


def test_calculate_fcf_ttm_negativo_en_alguno_suma_igual():
    assert rules.calculate_fcf_ttm([-100.0, 200.0, 300.0, 400.0]) == pytest.approx(800.0)


# ---------------------------------------------------------------------------
# _es_creciente/evaluate_pillars con listas largas (trimestrales, 12-20
# elementos) — regresión explícita de que el código no cambió (Decisión #12).
# ---------------------------------------------------------------------------


def test_es_creciente_lista_larga_20_elementos_trimestrales():
    historial = [float(100 + i * 7) for i in range(20)]  # creciente
    assert rules._es_creciente(historial) is True


def test_es_creciente_lista_larga_12_elementos_trimestrales_decreciente():
    historial = [float(500 - i * 5) for i in range(12)]  # decreciente
    assert rules._es_creciente(historial) is False


def test_evaluate_pillars_con_historial_trimestral_largo():
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    revenue_historial = [float(1000 + i * 50) for i in range(20)]
    net_income_historial = [float(100 + i * 5) for i in range(20)]
    pillars = rules.evaluate_pillars(
        revenue_historial=revenue_historial,
        net_income_historial=net_income_historial,
        liquidity=liquidity,
        barata=True,
    )
    assert pillars.ingresos_crecientes is True
    assert pillars.utilidades_crecientes is True
