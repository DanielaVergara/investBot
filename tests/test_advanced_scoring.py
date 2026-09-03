"""Tests de `advanced_scoring.py` — 5 modelos cuantitativos de análisis
fundamental avanzado (`SDD_analisis_fundamental_avanzado.md`).

Funciones puras, sin I/O — dicts de entrada armados a mano (mismo criterio
que `tests/test_rules.py`/`tests/test_valuation.py`), sin fixtures de
archivo ni `httpx.MockTransport`.
"""

from __future__ import annotations

import pytest

from investbot import advanced_scoring as scoring

# ---------------------------------------------------------------------------
# Datos base de una empresa manufacturera "completa" (2 años), reusados por
# varios tests. Mismos valores que
# tests/fixtures/fmp/balance_/income_/cash_flow_annual_empresa_completa.json
# (duplicados acá a propósito -- este módulo no lee fixtures de archivo).
# ---------------------------------------------------------------------------

BALANCE_RECIENTE = {
    "totalAssets": 1_000_000_000,
    "totalLiabilities": 400_000_000,
    "totalCurrentAssets": 300_000_000,
    "totalCurrentLiabilities": 150_000_000,
    "retainedEarnings": 200_000_000,
    "totalEquity": 600_000_000,
    "longTermDebt": 250_000_000,
    "shortTermDebt": 50_000_000,
    "totalDebt": 300_000_000,
    "cashAndCashEquivalents": 100_000_000,
    "propertyPlantEquipmentNet": 400_000_000,
}
BALANCE_ANTERIOR = {
    "totalAssets": 900_000_000,
    "totalLiabilities": 380_000_000,
    "totalCurrentAssets": 250_000_000,
    "totalCurrentLiabilities": 140_000_000,
    "retainedEarnings": 150_000_000,
    "totalEquity": 520_000_000,
    "longTermDebt": 260_000_000,
    "shortTermDebt": 40_000_000,
    "totalDebt": 300_000_000,
    "cashAndCashEquivalents": 80_000_000,
    "propertyPlantEquipmentNet": 380_000_000,
}
INCOME_RECIENTE = {
    "revenue": 800_000_000,
    "costOfRevenue": 500_000_000,
    "grossProfit": 300_000_000,
    "ebit": 150_000_000,
    "netIncome": 100_000_000,
    "weightedAverageShsOut": 100_000_000,
}
INCOME_ANTERIOR = {
    "revenue": 700_000_000,
    "costOfRevenue": 460_000_000,
    "grossProfit": 240_000_000,
    "ebit": 120_000_000,
    "netIncome": 80_000_000,
    "weightedAverageShsOut": 100_000_000,
}
CASH_FLOW_RECIENTE = {"operatingCashFlow": 130_000_000, "netIncome": 100_000_000}
MARKET_CAP = 5_000_000_000


# ---------------------------------------------------------------------------
# Altman Z-Score
# ---------------------------------------------------------------------------


def test_calculate_altman_z_caso_completo_zona_segura():
    result = scoring.calculate_altman_z(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    assert result.z == pytest.approx(9.255, rel=1e-3)
    assert result.zona == "segura"
    assert result.campos_faltantes == []


@pytest.mark.parametrize(
    "campo_faltante",
    ["totalAssets", "totalLiabilities", "totalCurrentAssets", "totalCurrentLiabilities", "retainedEarnings"],
)
def test_calculate_altman_z_campo_faltante_del_balance_no_calculable(campo_faltante):
    balance = {**BALANCE_RECIENTE, campo_faltante: None}
    result = scoring.calculate_altman_z(balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP)
    assert result.disponible is False
    assert result.z is None
    assert len(result.campos_faltantes) >= 1


def test_calculate_altman_z_campo_faltante_del_income_no_calculable():
    income = {**INCOME_RECIENTE, "ebit": None}
    result = scoring.calculate_altman_z(balance=BALANCE_RECIENTE, income=income, market_cap=MARKET_CAP)
    assert result.disponible is False
    assert "ebit" in result.campos_faltantes


def test_calculate_altman_z_market_cap_faltante_no_calculable():
    result = scoring.calculate_altman_z(balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=None)
    assert result.disponible is False
    assert "capitalizacion_mercado" in result.campos_faltantes


def test_calculate_altman_z_nunca_sustituye_valor_parcial():
    """Ningún campo faltante produce un `z` con un supuesto -- `z` es
    siempre `None` cuando `disponible=False` (regla dura "todo o nada")."""
    balance = {**BALANCE_RECIENTE, "retainedEarnings": None}
    result = scoring.calculate_altman_z(balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP)
    assert result.z is None


def test_calculate_altman_z_pasivos_totales_cero_no_calculable():
    """División por cero evitada explícitamente -- nunca lanza excepción."""
    balance = {**BALANCE_RECIENTE, "totalLiabilities": 0}
    result = scoring.calculate_altman_z(balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP)
    assert result.disponible is False


# ---------------------------------------------------------------------------
# Altman Z'' (variante asset-light)
# ---------------------------------------------------------------------------


def test_calculate_altman_z_prime_prime_caso_completo():
    result = scoring.calculate_altman_z_prime_prime(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    assert result.z == pytest.approx(15.769, rel=1e-3)
    assert result.zona == "segura"


def test_calculate_altman_z_prime_prime_campo_faltante_no_calculable():
    balance = {**BALANCE_RECIENTE, "retainedEarnings": None}
    result = scoring.calculate_altman_z_prime_prime(
        balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is False
    assert result.z is None
    assert "utilidades_retenidas" in result.campos_faltantes


def test_calculate_altman_z_prime_prime_no_requiere_revenue():
    """Z'' no usa el factor E (ventas/activos) -- un income sin `revenue`
    (pero con `ebit`) sigue siendo calculable."""
    income_sin_revenue = {"ebit": INCOME_RECIENTE["ebit"]}
    result = scoring.calculate_altman_z_prime_prime(
        balance=BALANCE_RECIENTE, income=income_sin_revenue, market_cap=MARKET_CAP
    )
    assert result.disponible is True


# ---------------------------------------------------------------------------
# is_asset_light (D4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sector,industry",
    [
        ("Technology", "Software - Application"),
        ("Communication Services", "Internet Content & Information"),
        (None, "Internet Retail"),
        ("Consumer Cyclical", "Software - Infrastructure"),
    ],
)
def test_is_asset_light_true(sector, industry):
    assert scoring.is_asset_light(sector, industry) is True


@pytest.mark.parametrize(
    "sector,industry",
    [
        ("Industrials", "Farm & Heavy Construction Machinery"),
        ("Energy", "Oil & Gas E&P"),
        (None, None),
        ("Financial Services", "Banks - Regional"),
    ],
)
def test_is_asset_light_false(sector, industry):
    assert scoring.is_asset_light(sector, industry) is False


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------


def test_calculate_piotroski_f_score_caso_completo_9_de_9():
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE,
        income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    assert result.puntaje == 9
    assert result.criterios_evaluables == 9
    assert result.criterios_totales == 9
    assert all(c.cumplido is True for c in result.criterios)


def test_calculate_piotroski_f_score_excluye_criterio_con_dato_faltante():
    """Falta `grossProfit` del año anterior -- excluye SOLO el criterio de
    margen bruto creciente, sin afectar a los otros 8."""
    income_anterior_incompleto = {**INCOME_ANTERIOR, "grossProfit": None}
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE,
        income_anterior=income_anterior_incompleto,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    assert result.criterios_evaluables == 8
    assert result.puntaje == 8  # los otros 8 criterios siguen cumpliéndose
    criterio_margen = next(c for c in result.criterios if c.nombre == "margen_bruto_creciente")
    assert criterio_margen.cumplido is None


def test_calculate_piotroski_f_score_reporta_evaluables_correctos_multiples_faltantes():
    balance_anterior_incompleto = {**BALANCE_ANTERIOR, "longTermDebt": None, "totalCurrentAssets": None}
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior=balance_anterior_incompleto,
        income_reciente=INCOME_RECIENTE,
        income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    # apalancamiento_decreciente (longTermDebt) + liquidez_creciente
    # (totalCurrentAssets) quedan excluidos -- 7 de 9 evaluables.
    assert result.criterios_evaluables == 7
    nombres_no_evaluables = {c.nombre for c in result.criterios if c.cumplido is None}
    assert nombres_no_evaluables == {"apalancamiento_decreciente", "liquidez_creciente"}


def test_calculate_piotroski_f_score_nunca_asume_0_ni_1_para_criterio_faltante():
    """Con TODOS los datos del año anterior faltantes, ningún criterio que
    dependa de comparación entre años se evalúa como False -- se excluye
    (`None`), no se asume incumplido."""
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior={},
        income_reciente=INCOME_RECIENTE,
        income_anterior={},
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    # Solo los 2 criterios que no dependen del año anterior son evaluables:
    # roa_positivo, cfo_positivo, cfo_mayor_utilidad (netIncome/CFO del año
    # reciente solamente).
    assert result.criterios_evaluables == 3
    assert result.puntaje == 3
    for c in result.criterios:
        if c.nombre not in ("roa_positivo", "cfo_positivo", "cfo_mayor_utilidad"):
            assert c.cumplido is None


def test_calculate_piotroski_f_score_pasivos_circulantes_cero_no_crashea():
    """Caso límite de `security`/`qa`: un divisor en 0 (no `None`) nunca
    lanza `ZeroDivisionError` -- el criterio se excluye."""
    balance_anterior_cero = {**BALANCE_ANTERIOR, "totalCurrentLiabilities": 0}
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior=balance_anterior_cero,
        income_reciente=INCOME_RECIENTE,
        income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    criterio_liquidez = next(c for c in result.criterios if c.nombre == "liquidez_creciente")
    assert criterio_liquidez.cumplido is None


# ---------------------------------------------------------------------------
# Beneish M-Score — D1 (RESUELTO): siempre "no calculable", sin excepción.
# ---------------------------------------------------------------------------


def test_beneish_m_score_siempre_no_calculable_con_datos_completos():
    """Incluso con los 2 años completos, D1 exige "no calculable" siempre."""
    result = scoring.calculate_beneish_m_score(
        balance_reciente=BALANCE_RECIENTE,
        balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE,
        income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    assert result.disponible is False
    assert result.m_score is None


def test_beneish_m_score_siempre_no_calculable_sin_ningun_dato():
    result = scoring.calculate_beneish_m_score()
    assert result.disponible is False
    assert result.m_score is None


# ---------------------------------------------------------------------------
# Magic Formula
# ---------------------------------------------------------------------------


def test_calculate_magic_formula_metrics_caso_completo():
    result = scoring.calculate_magic_formula_metrics(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    # capital_invertido = (300M-150M) + 400M = 550M ; roic = 150M/550M
    assert result.roic == pytest.approx(150_000_000 / 550_000_000)
    # ev = 5000M + 300M - 100M = 5200M ; earnings_yield = 150M/5200M
    assert result.earnings_yield == pytest.approx(150_000_000 / 5_200_000_000)


@pytest.mark.parametrize(
    "campo_faltante", ["propertyPlantEquipmentNet", "totalDebt", "cashAndCashEquivalents"]
)
def test_calculate_magic_formula_metrics_campo_faltante_del_balance_no_calculable(campo_faltante):
    balance = {**BALANCE_RECIENTE, campo_faltante: None}
    result = scoring.calculate_magic_formula_metrics(
        balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is False
    assert result.roic is None
    assert result.earnings_yield is None


def test_calculate_magic_formula_metrics_ebit_faltante_no_calculable():
    income = {**INCOME_RECIENTE, "ebit": None}
    result = scoring.calculate_magic_formula_metrics(
        balance=BALANCE_RECIENTE, income=income, market_cap=MARKET_CAP
    )
    assert result.disponible is False
    assert "ebit" in result.campos_faltantes


def test_calculate_magic_formula_metrics_market_cap_faltante_no_calculable():
    result = scoring.calculate_magic_formula_metrics(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=None
    )
    assert result.disponible is False


# ---------------------------------------------------------------------------
# Factores estilo AQR — criterios independientes
# ---------------------------------------------------------------------------


def test_calculate_factor_score_caso_completo():
    result = scoring.calculate_factor_score(
        earnings_yield=0.02885,
        roe=0.1667,
        gross_margin=0.375,
        piotroski_score=9,
        piotroski_evaluables=9,
        momentum_etiqueta="impulso_positivo",
        beta=1.0,
    )
    assert result.value == "bajo"
    assert result.quality == "alto"
    assert result.momentum == "alto"
    assert result.low_vol == "medio"


def test_calculate_factor_score_momentum_no_disponible_no_afecta_otros():
    """Criterios independientes: `momentum_etiqueta="no_disponible"`
    (faltaron `priceAvg50`/`priceAvg200`) no cambia Value/Quality/Low-vol."""
    result = scoring.calculate_factor_score(
        earnings_yield=0.10,
        roe=0.20,
        gross_margin=0.50,
        piotroski_score=8,
        piotroski_evaluables=9,
        momentum_etiqueta="no_disponible",
        beta=0.5,
    )
    assert result.momentum == "no_disponible"
    assert result.value == "alto"
    assert result.quality == "alto"
    assert result.low_vol == "bajo"


def test_calculate_factor_score_beta_faltante_low_vol_no_disponible():
    result = scoring.calculate_factor_score(
        earnings_yield=0.10, roe=0.20, gross_margin=0.50,
        piotroski_score=8, piotroski_evaluables=9,
        momentum_etiqueta="mixto", beta=None,
    )
    assert result.low_vol == "no_disponible"
    assert result.momentum == "medio"


def test_calculate_factor_score_quality_no_disponible_si_las_3_submetricas_faltan():
    result = scoring.calculate_factor_score(
        earnings_yield=None, roe=None, gross_margin=None,
        piotroski_score=None, piotroski_evaluables=None,
        momentum_etiqueta="no_disponible", beta=None,
    )
    assert result.quality == "no_disponible"
    assert result.value == "no_disponible"
    assert result.momentum == "no_disponible"
    assert result.low_vol == "no_disponible"


def test_calculate_factor_score_quality_parcial_con_1_de_3_submetricas():
    """Solo ROE disponible -- Quality se evalúa igual, con ese único dato."""
    result = scoring.calculate_factor_score(
        earnings_yield=None, roe=0.20, gross_margin=None,
        piotroski_score=None, piotroski_evaluables=None,
        momentum_etiqueta="no_disponible", beta=None,
    )
    assert result.quality == "alto"


@pytest.mark.parametrize(
    "beta,esperado", [(0.5, "bajo"), (1.0, "medio"), (1.5, "alto")]
)
def test_calculate_factor_score_umbrales_beta(beta, esperado):
    result = scoring.calculate_factor_score(
        earnings_yield=None, roe=None, gross_margin=None,
        piotroski_score=None, piotroski_evaluables=None,
        momentum_etiqueta="no_disponible", beta=beta,
    )
    assert result.low_vol == esperado


# ---------------------------------------------------------------------------
# Testabilidad — funciones puras, sin I/O (criterio de `qa`)
# ---------------------------------------------------------------------------


def test_advanced_scoring_no_importa_httpx_ni_fmp_client():
    import ast
    import inspect

    source = inspect.getsource(scoring)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert not any("httpx" in name or "fmp_client" in name for name in imported_names)


def test_calculate_altman_z_es_pura_no_muta_los_dicts_de_entrada():
    balance_copia = dict(BALANCE_RECIENTE)
    income_copia = dict(INCOME_RECIENTE)
    scoring.calculate_altman_z(balance=balance_copia, income=income_copia, market_cap=MARKET_CAP)
    assert balance_copia == BALANCE_RECIENTE
    assert income_copia == INCOME_RECIENTE


def test_calculate_piotroski_es_pura_mismo_input_mismo_output():
    r1 = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE, balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE, income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    r2 = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE, balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE, income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    assert r1.puntaje == r2.puntaje
    assert r1.criterios_evaluables == r2.criterios_evaluables


# ---------------------------------------------------------------------------
# SDD_explicacion_paso_a_paso.md, "Campos nuevos — sin cálculo nuevo, solo
# exposición" -- los campos nuevos expuestos por AltmanZResult/
# CriterioPiotroski.valores/MagicFormulaResult reconstruyen EXACTAMENTE el
# resultado ya calculado con la fórmula documentada.
# ---------------------------------------------------------------------------


def test_altman_z_campos_a_e_reconstruyen_z_con_los_coeficientes_documentados():
    result = scoring.calculate_altman_z(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    assert None not in (result.a, result.b, result.c, result.d, result.e)
    reconstruido = 1.2 * result.a + 1.4 * result.b + 3.3 * result.c + 0.6 * result.d + 1.0 * result.e
    assert reconstruido == pytest.approx(result.z)


def test_altman_z_prime_prime_campos_a_d_reconstruyen_z_pp_sin_e():
    result = scoring.calculate_altman_z_prime_prime(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    assert None not in (result.a, result.b, result.c, result.d)
    assert result.e is None  # Z'' no usa el factor E (ventas/activos)
    reconstruido = 6.56 * result.a + 3.26 * result.b + 6.72 * result.c + 1.05 * result.d
    assert reconstruido == pytest.approx(result.z)


def test_altman_z_no_disponible_campos_a_e_en_none():
    balance = {**BALANCE_RECIENTE, "totalAssets": None}
    result = scoring.calculate_altman_z(balance=balance, income=INCOME_RECIENTE, market_cap=MARKET_CAP)
    assert result.disponible is False
    assert (result.a, result.b, result.c, result.d, result.e) == (None, None, None, None, None)


def test_magic_formula_campos_nuevos_reconstruyen_roic_y_earnings_yield():
    result = scoring.calculate_magic_formula_metrics(
        balance=BALANCE_RECIENTE, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is True
    assert None not in (
        result.ebit, result.capital_invertido, result.ev,
        result.market_cap, result.total_debt, result.cash,
    )
    roic_reconstruido = result.ebit / result.capital_invertido
    ey_reconstruido = result.ebit / result.ev
    assert roic_reconstruido == pytest.approx(result.roic)
    assert ey_reconstruido == pytest.approx(result.earnings_yield)
    ev_reconstruido = result.market_cap + result.total_debt - result.cash
    assert ev_reconstruido == pytest.approx(result.ev)


def test_magic_formula_no_disponible_campos_nuevos_en_none():
    result = scoring.calculate_magic_formula_metrics(
        balance={**BALANCE_RECIENTE, "totalDebt": None}, income=INCOME_RECIENTE, market_cap=MARKET_CAP
    )
    assert result.disponible is False
    assert result.ebit is None
    assert result.capital_invertido is None
    assert result.ev is None


_PIOTROSKI_VALORES_ESPERADOS = {
    "roa_positivo": ("net_income_t",),
    "cfo_positivo": ("cfo_t",),
    "roa_creciente": ("roa_t", "roa_t1"),
    "cfo_mayor_utilidad": ("cfo_t", "net_income_t"),
    "apalancamiento_decreciente": ("apalancamiento_t", "apalancamiento_t1"),
    "liquidez_creciente": ("liquidez_t", "liquidez_t1"),
    "sin_dilucion": ("shares_t", "shares_t1"),
    "margen_bruto_creciente": ("margen_t", "margen_t1"),
    "rotacion_activos_creciente": ("rotacion_t", "rotacion_t1"),
}


@pytest.mark.parametrize("nombre,claves", list(_PIOTROSKI_VALORES_ESPERADOS.items()))
def test_piotroski_valores_presentes_por_criterio_evaluable(nombre, claves):
    """Para cada uno de los 9 criterios, el dict `valores` contiene las
    magnitudes reales que determinaron `cumplido` (test por criterio, con un
    fixture donde se conoce el resultado esperado)."""
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE, balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE, income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    criterio = next(c for c in result.criterios if c.nombre == nombre)
    assert criterio.cumplido is not None
    assert criterio.valores is not None
    for clave in claves:
        assert clave in criterio.valores
        assert criterio.valores[clave] is not None


def test_piotroski_criterio_no_evaluable_valores_en_none():
    """Ningún criterio no evaluable arma un dict de "magnitudes reales" --
    `valores` queda `None`, nunca un dict con `None` adentro."""
    balance_reciente_sin_lt_debt = {**BALANCE_RECIENTE, "longTermDebt": None}
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=balance_reciente_sin_lt_debt, balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE, income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    criterio = next(c for c in result.criterios if c.nombre == "apalancamiento_decreciente")
    assert criterio.cumplido is None
    assert criterio.valores is None


def test_piotroski_roa_creciente_valores_reconstruyen_la_comparacion():
    result = scoring.calculate_piotroski_f_score(
        balance_reciente=BALANCE_RECIENTE, balance_anterior=BALANCE_ANTERIOR,
        income_reciente=INCOME_RECIENTE, income_anterior=INCOME_ANTERIOR,
        cash_flow_reciente=CASH_FLOW_RECIENTE,
    )
    criterio = next(c for c in result.criterios if c.nombre == "roa_creciente")
    roa_t_esperado = INCOME_RECIENTE["netIncome"] / BALANCE_RECIENTE["totalAssets"]
    roa_t1_esperado = INCOME_ANTERIOR["netIncome"] / BALANCE_ANTERIOR["totalAssets"]
    assert criterio.valores["roa_t"] == pytest.approx(roa_t_esperado)
    assert criterio.valores["roa_t1"] == pytest.approx(roa_t1_esperado)
    assert criterio.cumplido == (roa_t_esperado > roa_t1_esperado)
