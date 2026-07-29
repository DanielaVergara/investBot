"""Tests de `valuation.py` — motor de valoración.

Incluye el caso de regresión Adobe (tolerancia ±1%, spec + qa) y los tests
puntuales de B1/B2/B4 del Spec Patch [Iter-2].
"""

from __future__ import annotations

import pytest

from investbot import peers, valuation

ADOBE_TOLERANCE = 0.01  # ±1%


def _within_tolerance(value: float, target: float, tolerance: float = ADOBE_TOLERANCE) -> bool:
    return abs(value - target) / target <= tolerance


# ---------------------------------------------------------------------------
# calculate_cagr — B1 (base negativa/cero, reciente negativo/cero) + B2 (piso)
# ---------------------------------------------------------------------------


def test_calculate_cagr_base_negativa():
    assert valuation.calculate_cagr(valor_reciente=5, valor_antiguo=-2, n_años=5) is None


def test_calculate_cagr_base_cero():
    assert valuation.calculate_cagr(valor_reciente=5, valor_antiguo=0, n_años=5) is None


def test_calculate_cagr_reciente_negativo():
    assert valuation.calculate_cagr(valor_reciente=-1, valor_antiguo=5, n_años=5) is None


def test_calculate_cagr_reciente_cero():
    assert valuation.calculate_cagr(valor_reciente=0, valor_antiguo=5, n_años=5) is None


def test_calculate_cagr_valido():
    resultado = valuation.calculate_cagr(valor_reciente=8, valor_antiguo=5, n_años=4)
    assert resultado is not None
    assert isinstance(resultado, float)
    esperado = (8 / 5) ** (1 / 4) - 1
    assert resultado == pytest.approx(esperado)
    assert resultado > 0


def test_calculate_cagr_nunca_devuelve_complejo():
    # Caso clásico que en Python puro (base negativa ** exponente fraccionario)
    # produce un `complex` silencioso si no se guarda explícitamente.
    resultado = valuation.calculate_cagr(valor_reciente=5, valor_antiguo=-10, n_años=3)
    assert resultado is None
    assert not isinstance(resultado, complex)


def test_calculate_cagr_menos_de_3_años():
    """B2: n_años=1 (2 registros) está por debajo del piso de 3 registros."""
    assert valuation.calculate_cagr(valor_reciente=5, valor_antiguo=3, n_años=1) is None


def test_calculate_cagr_exactamente_piso():
    """B2: n_años=2 (3 registros) es el piso mínimo aceptable."""
    resultado = valuation.calculate_cagr(valor_reciente=5, valor_antiguo=3, n_años=2)
    assert resultado is not None
    assert isinstance(resultado, float)


def test_calculate_cagr_cero_o_un_registro_no_crashea():
    assert valuation.calculate_cagr(valor_reciente=5, valor_antiguo=3, n_años=0) is None


# ---------------------------------------------------------------------------
# Modelo de Múltiplos
# ---------------------------------------------------------------------------


def test_multiplos_caso_simple():
    assert valuation.calculate_multiplos_fair_value(eps_ttm=2, per_promedio_peers=15) == 30


def test_multiplos_otro_caso_simple():
    assert valuation.calculate_multiplos_fair_value(eps_ttm=4.5, per_promedio_peers=20) == 90.0


def test_multiplos_eps_negativo_excluido():
    """B4: EPS TTM <= 0 → Múltiplos se excluye (retorna None)."""
    assert valuation.calculate_multiplos_fair_value(eps_ttm=-1.5, per_promedio_peers=20) is None


def test_multiplos_eps_cero_excluido():
    assert valuation.calculate_multiplos_fair_value(eps_ttm=0, per_promedio_peers=20) is None


# ---------------------------------------------------------------------------
# Graham EPS Model
# ---------------------------------------------------------------------------


def test_graham_caso_simple_verificado_a_mano():
    # EPS=3, g=10% (g_pct=10), Y=4% (y_pct=4) → 3 * (8.5+20) * 4.4 / 4 = 3*28.5*1.1 = 94.05
    resultado = valuation.calculate_graham_fair_value(eps_ttm=3, g=0.10, y=0.04)
    esperado = 3 * (8.5 + 2 * 10) * 4.4 / 4
    assert resultado == pytest.approx(esperado)
    assert resultado == pytest.approx(94.05)


def test_graham_g_negativo_sigue_siendo_valido():
    """g negativo (EPS decreciente) no es bloqueante mientras (8.5+2*g_pct) > 0.

    g=-0.01 (1% decreciente/año) -> g_pct=-1 -> multiplicador=8.5-2=6.5 (> 0).
    """
    resultado = valuation.calculate_graham_fair_value(eps_ttm=5, g=-0.01, y=0.045)
    assert resultado is not None
    assert resultado > 0
    esperado = 5 * (8.5 + 2 * -1) * 4.4 / 4.5
    assert resultado == pytest.approx(esperado)


def test_graham_eps_no_positivo_excluido():
    assert valuation.calculate_graham_fair_value(eps_ttm=0, g=0.10, y=0.04) is None
    assert valuation.calculate_graham_fair_value(eps_ttm=-2, g=0.10, y=0.04) is None


def test_graham_g_none_excluido():
    """B1: si el CAGR no fue calculable (g=None), Graham se excluye."""
    assert valuation.calculate_graham_fair_value(eps_ttm=5, g=None, y=0.04) is None


def test_graham_y_no_disponible_excluido():
    assert valuation.calculate_graham_fair_value(eps_ttm=5, g=0.10, y=0) is None
    assert valuation.calculate_graham_fair_value(eps_ttm=5, g=0.10, y=None) is None


def test_graham_multiplicador_no_positivo_excluido():
    """Spec Patch Iter-3, sección 1 (cierra un hueco preexistente de Iter-2):
    g=-0.05 -> g_pct=-5 -> multiplicador=8.5-10=-1.5 <= 0 -> excluido."""
    assert valuation.calculate_graham_fair_value(eps_ttm=5, g=-0.05, y=0.04) is None


def test_graham_multiplicador_exactamente_cero_excluido():
    """Caso límite: multiplicador == 0 (no solo < 0) también se excluye."""
    assert valuation.calculate_graham_fair_value(eps_ttm=5, g=-0.0425, y=0.04) is None


# ---------------------------------------------------------------------------
# WACC
# ---------------------------------------------------------------------------


def test_wacc_verificado_a_mano():
    # Ke = Y + beta*premium = 0.044 + 1.1*0.055 = 0.1045
    # Kd_pretax = 150e6/4000e6 = 0.0375; tax_rate = 2185/11500 = 0.19; Kd_aftertax=0.030375
    # V = 153180e6 + 4000e6 = 157180e6; E/V=0.974551...; D/V=0.025449...
    # WACC = 0.974551*0.1045 + 0.025449*0.030375 = 0.102614 (aprox)
    wacc = valuation.calculate_wacc(
        interest_expense=150_000_000,
        total_debt=4_000_000_000,
        income_tax_expense=2_185_000_000,
        income_before_tax=11_500_000_000,
        beta=1.1,
        y=0.044,
        market_cap=153_180_000_000,
    )
    assert wacc == pytest.approx(0.10261362768800103, rel=1e-6)


def test_wacc_sin_deuda_colapsa_a_ke():
    wacc = valuation.calculate_wacc(
        interest_expense=0,
        total_debt=0,
        income_tax_expense=0,
        income_before_tax=1_000_000,
        beta=1.0,
        y=0.04,
        market_cap=1_000_000_000,
    )
    ke = 0.04 + 1.0 * valuation.MARKET_RISK_PREMIUM
    assert wacc == pytest.approx(ke)


def test_wacc_market_cap_invalido_none():
    assert (
        valuation.calculate_wacc(
            interest_expense=100,
            total_debt=100,
            income_tax_expense=10,
            income_before_tax=100,
            beta=1.0,
            y=0.04,
            market_cap=0,
        )
        is None
    )


def test_wacc_income_before_tax_no_positivo_usa_kd_pretax():
    """No crashea (tasa impositiva indefinida) — usa Kd pre-impuesto como aproximación."""
    wacc = valuation.calculate_wacc(
        interest_expense=100_000,
        total_debt=1_000_000,
        income_tax_expense=0,
        income_before_tax=-500_000,
        beta=1.0,
        y=0.04,
        market_cap=10_000_000,
    )
    assert wacc is not None


# ---------------------------------------------------------------------------
# DCF
# ---------------------------------------------------------------------------


def test_dcf_fcf_historial_insuficiente_excluido():
    assert (
        valuation.calculate_dcf_fair_value(
            fcf_historial=[100, 110],  # solo 2 registros, n_años=1 < piso
            wacc=0.10,
            shares_outstanding=1_000_000,
        )
        is None
    )


def test_dcf_wacc_none_excluido():
    assert (
        valuation.calculate_dcf_fair_value(
            fcf_historial=[100, 110, 120, 130, 140],
            wacc=None,
            shares_outstanding=1_000_000,
        )
        is None
    )


def test_dcf_wacc_menor_a_terminal_growth_excluido():
    assert (
        valuation.calculate_dcf_fair_value(
            fcf_historial=[100, 110, 120, 130, 140],
            wacc=0.01,  # menor al terminal growth (0.025) -> denominador negativo
            shares_outstanding=1_000_000,
        )
        is None
    )


def test_dcf_fcf_base_negativo_excluido():
    assert (
        valuation.calculate_dcf_fair_value(
            fcf_historial=[-100, 50, 60, 70, 80],
            wacc=0.10,
            shares_outstanding=1_000_000,
        )
        is None
    )


def test_dcf_verificado_a_mano_valor_presente_primer_flujo():
    """Verifica a mano el valor presente del primer flujo proyectado (t=1)."""
    fcf_historial = [100.0, 108.0, 116.64, 125.9712, 136.048896]  # CAGR=8% exacto
    wacc = 0.12
    resultado = valuation.calculate_dcf_fair_value(
        fcf_historial=fcf_historial,
        wacc=wacc,
        shares_outstanding=1000,
    )
    assert resultado is not None
    # Reconstrucción manual del primer término de la proyección:
    fcf_reciente = fcf_historial[-1]
    g_fcf = valuation.calculate_cagr(fcf_reciente, fcf_historial[0], 4)
    assert g_fcf == pytest.approx(0.08, rel=1e-6)
    fcf_t1 = fcf_reciente * (1 + g_fcf)
    pv_t1 = fcf_t1 / (1 + wacc) ** 1
    assert pv_t1 == pytest.approx(fcf_t1 / 1.12)


# ---------------------------------------------------------------------------
# compute_valuation — exclusión de modelo + promedio parcial (B1/B2/B4)
# ---------------------------------------------------------------------------


def _wacc_inputs_validos():
    return {
        "interest_expense": 150_000_000,
        "total_debt": 4_000_000_000,
        "income_tax_expense": 2_185_000_000,
        "income_before_tax": 11_500_000_000,
        "beta": 1.1,
        "market_cap": 153_180_000_000,
    }


def test_valuation_graham_excluido_por_eps_base_negativo():
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[-2.0, 3.0, 5.0, 7.0, 10.0],  # año base negativo
        per_promedio_peers=20.0,
        fcf_historial=[100, 110, 120, 130, 140],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_graham is None
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("graham") == "eps_base_no_positivo"
    assert result.valor_justo_multiplos is not None
    assert result.valor_justo_dcf is not None
    esperado = (result.valor_justo_multiplos + result.valor_justo_dcf) / 2
    assert result.valor_justo_total == pytest.approx(esperado)


def test_valuation_dcf_excluido_por_fcf_base_negativo():
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        per_promedio_peers=20.0,
        fcf_historial=[-100, 110, 120, 130, 140],  # FCF base negativo
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_dcf is None
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("dcf") == "fcf_base_no_positivo"
    assert result.valor_justo_multiplos is not None
    assert result.valor_justo_graham is not None
    esperado = (result.valor_justo_multiplos + result.valor_justo_graham) / 2
    assert result.valor_justo_total == pytest.approx(esperado)


def test_valuation_empresa_reciente_ipo():
    """B2: 1 año de historial (2 registros insuficientes) excluye Graham y DCF."""
    result = valuation.compute_valuation(
        eps_ttm=5.0,
        eps_historial=[5.0],  # 1 solo año disponible
        per_promedio_peers=15.0,
        fcf_historial=[80.0],  # 1 solo año disponible
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_graham is None
    assert result.valor_justo_dcf is None
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("graham") == "historial_insuficiente"
    assert motivos.get("dcf") == "historial_insuficiente"
    assert result.valor_justo_multiplos is not None
    assert result.valor_justo_total == pytest.approx(result.valor_justo_multiplos)


def test_valuation_0_de_3_modelos():
    """Historial insuficiente + EPS TTM <= 0 invalida los 3 modelos simultáneamente."""
    result = valuation.compute_valuation(
        eps_ttm=-1.0,  # invalida Múltiplos y Graham
        eps_historial=[-1.0],
        per_promedio_peers=15.0,
        fcf_historial=[80.0],  # invalida DCF (historial insuficiente)
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_multiplos is None
    assert result.valor_justo_graham is None
    assert result.valor_justo_dcf is None
    assert result.valor_justo_total is None
    assert len(result.modelos_excluidos) == 3


def test_valuation_multiplos_y_graham_excluidos_eps_no_positivo():
    result = valuation.compute_valuation(
        eps_ttm=0.0,
        eps_historial=[1.0, 2.0, 3.0, 4.0, 0.0],
        per_promedio_peers=15.0,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_multiplos is None
    assert result.valor_justo_graham is None
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("multiplos") == "eps_ttm_no_positivo"
    assert motivos.get("graham") == "eps_ttm_no_positivo"
    assert result.valor_justo_dcf is not None
    assert result.valor_justo_total == pytest.approx(result.valor_justo_dcf)


def test_valuation_graham_excluido_por_multiplicador_no_positivo():
    """`compute_valuation` reporta el motivo nuevo cuando el multiplicador de
    Graham `(8.5+2*g_pct)` da <= 0 con datos base por lo demás válidos."""
    result = valuation.compute_valuation(
        eps_ttm=10.0,
        eps_historial=[20.0, 16.0, 13.0, 11.0, 10.0],  # CAGR fuertemente negativo
        per_promedio_peers=15.0,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert result.valor_justo_graham is None
    motivos = {m.modelo: m.motivo for m in result.modelos_excluidos}
    assert motivos.get("graham") == "graham_multiplicador_no_positivo"
    assert result.valor_justo_multiplos is not None
    assert result.valor_justo_dcf is not None


def test_dcf_g_fcf_override_reemplaza_el_cagr_interno():
    """`g_fcf_override` (Spec Patch Iter-3) permite a `compute_valuation_scenarios`
    desplazar el crecimiento proyectado sin recalcularlo desde `fcf_historial`."""
    fcf_historial = [100.0, 108.0, 116.64, 125.9712, 136.048896]  # CAGR=8% exacto
    wacc = 0.12

    sin_override = valuation.calculate_dcf_fair_value(
        fcf_historial=fcf_historial, wacc=wacc, shares_outstanding=1000
    )
    con_override_mismo_valor = valuation.calculate_dcf_fair_value(
        fcf_historial=fcf_historial, wacc=wacc, shares_outstanding=1000, g_fcf_override=0.08
    )
    assert con_override_mismo_valor == pytest.approx(sin_override)

    con_override_distinto = valuation.calculate_dcf_fair_value(
        fcf_historial=fcf_historial, wacc=wacc, shares_outstanding=1000, g_fcf_override=0.11
    )
    assert con_override_distinto != pytest.approx(sin_override)
    assert con_override_distinto > sin_override  # más crecimiento -> más valor


# ---------------------------------------------------------------------------
# classify_scenario (Spec Patch Iter-3, sección 3)
# ---------------------------------------------------------------------------


def test_classify_scenario_barata():
    assert valuation.classify_scenario(precio_actual=100.0, valor_justo_total=150.0) is True


def test_classify_scenario_cara():
    assert valuation.classify_scenario(precio_actual=200.0, valor_justo_total=150.0) is False


def test_classify_scenario_none():
    assert valuation.classify_scenario(precio_actual=100.0, valor_justo_total=None) is None


# ---------------------------------------------------------------------------
# compute_valuation_scenarios — implicación estructural C1 (Spec Patch Iter-4)
# ---------------------------------------------------------------------------


def test_valuation_scenarios_0_de_3_modelos_los_3_escenarios_en_none():
    """Spec Patch Iter-4, C1: mismos datos que `test_valuation_0_de_3_modelos`
    (Iter-2) -> conservador con `valor_justo_total=None` implica, por
    construcción, que pesimista y optimista también lo son."""
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=15.0, per_maximo=15.0, peers_usados=["MSFT"]
    )
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=-1.0,  # invalida Múltiplos y Graham
        eps_historial=[-1.0],
        peer_average=peer_average,
        fcf_historial=[80.0],  # invalida DCF (historial insuficiente)
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert scenarios.pesimista.valor_justo_total is None
    assert scenarios.conservador.valor_justo_total is None
    assert scenarios.optimista.valor_justo_total is None
    assert len(scenarios.modelos_excluidos_base) == 3


# ---------------------------------------------------------------------------
# compute_valuation_scenarios — exclusiones de nivel 1 (dato base inválido,
# igual en los 3 escenarios, reportado una sola vez en modelos_excluidos_base)
# ---------------------------------------------------------------------------


def test_valuation_scenarios_multiplos_nivel1_sin_peers_validos():
    peer_average = peers.PeerAverageResult(
        per_promedio=None, per_minimo=None, per_maximo=None, peers_usados=[]
    )
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    motivos_base = {m.modelo: m.motivo for m in scenarios.modelos_excluidos_base}
    assert motivos_base.get("multiplos") == "per_peers_no_disponible"
    for escenario in (scenarios.pesimista, scenarios.conservador, scenarios.optimista):
        assert escenario.valor_justo_multiplos is None
        motivos = {m.modelo: m.motivo for m in escenario.modelos_excluidos}
        assert motivos.get("multiplos") == "per_peers_no_disponible"


def test_valuation_scenarios_graham_nivel1_historial_insuficiente():
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=13.0, per_maximo=17.0, peers_usados=["MSFT", "ORCL"]
    )
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=10.0,
        eps_historial=[10.0],  # 1 solo año -> historial insuficiente (B2)
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    motivos_base = {m.modelo: m.motivo for m in scenarios.modelos_excluidos_base}
    assert motivos_base.get("graham") == "historial_insuficiente"
    assert scenarios.pesimista.valor_justo_graham is None
    assert scenarios.optimista.valor_justo_graham is None


def test_valuation_scenarios_graham_nivel1_y_no_disponible():
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=13.0, per_maximo=17.0, peers_usados=["MSFT", "ORCL"]
    )
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=None,  # Y no disponible (FRED y Treasury.gov fallaron)
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    motivos_base = {m.modelo: m.motivo for m in scenarios.modelos_excluidos_base}
    assert motivos_base.get("graham") == "y_no_disponible"
    assert motivos_base.get("dcf") == "wacc_no_calculable"


def test_valuation_scenarios_dcf_nivel1_wacc_no_calculable():
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=13.0, per_maximo=17.0, peers_usados=["MSFT", "ORCL"]
    )
    wacc_inputs = _wacc_inputs_validos()
    wacc_inputs["market_cap"] = 0  # market_cap<=0 -> WACC no calculable
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=wacc_inputs,
        shares_outstanding=1_000_000,
    )
    motivos_base = {m.modelo: m.motivo for m in scenarios.modelos_excluidos_base}
    assert motivos_base.get("dcf") == "wacc_no_calculable"
    assert scenarios.pesimista.valor_justo_dcf is None
    assert scenarios.optimista.valor_justo_dcf is None


# ---------------------------------------------------------------------------
# compute_valuation_scenarios — exclusiones de nivel 2 (dato base válido, el
# desplazamiento de escenario empuja un valor intermedio fuera de rango)
# ---------------------------------------------------------------------------


def test_valuation_scenarios_graham_nivel2_excluido_solo_en_pesimista():
    """g_conservador=-0.02 -> multiplicador conservador=4.50 (válido) y
    optimista=10.50 (válido), pero pesimista (g-0.03=-0.05) da
    multiplicador=-1.50 <= 0 -> excluido solo en ese escenario."""
    eps_historial = [10.0, 9.8, 9.6, 9.4, 9.224]
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=13.0, per_maximo=17.0, peers_usados=["MSFT", "ORCL"]
    )
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=9.224,
        eps_historial=eps_historial,
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.044,
        wacc_inputs=_wacc_inputs_validos(),
        shares_outstanding=1_000_000,
    )
    assert scenarios.modelos_excluidos_base == []  # dato base válido (nivel 1 OK)
    assert scenarios.pesimista.valor_justo_graham is None
    motivos_pes = {m.modelo: m.motivo for m in scenarios.pesimista.modelos_excluidos}
    assert motivos_pes.get("graham") == "graham_multiplicador_no_positivo"
    assert scenarios.conservador.valor_justo_graham is not None
    assert scenarios.optimista.valor_justo_graham is not None


def test_valuation_scenarios_dcf_nivel2_excluido_solo_en_optimista():
    """WACC conservador=3% (beta=0 -> Ke=Y=3%, sin deuda): pesimista=4%,
    conservador=3%, ambos > TERMINAL_GROWTH_RATE (2.5%) -> válidos; optimista
    =2% <= 2.5% -> excluido solo en ese escenario (guarda ya existente)."""
    peer_average = peers.PeerAverageResult(
        per_promedio=15.0, per_minimo=13.0, per_maximo=17.0, peers_usados=["MSFT", "ORCL"]
    )
    wacc_inputs = {
        "interest_expense": 0,
        "total_debt": 0,
        "income_tax_expense": 0,
        "income_before_tax": 100,
        "beta": 0.0,
        "market_cap": 1000,
    }
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=10.0,
        eps_historial=[5.0, 6.0, 7.0, 8.0, 10.0],
        peer_average=peer_average,
        fcf_historial=[80.0, 90.0, 100.0, 110.0, 120.0],
        y=0.03,
        wacc_inputs=wacc_inputs,
        shares_outstanding=1_000_000,
    )
    assert scenarios.modelos_excluidos_base == []
    assert scenarios.pesimista.valor_justo_dcf is not None
    assert scenarios.conservador.valor_justo_dcf is not None
    assert scenarios.optimista.valor_justo_dcf is None
    motivos_opt = {m.modelo: m.motivo for m in scenarios.optimista.modelos_excluidos}
    assert motivos_opt.get("dcf") == "dcf_no_calculable"


# ---------------------------------------------------------------------------
# Caso de regresión Adobe — tolerancia ±1% (spec + qa)
# ---------------------------------------------------------------------------


def test_valuation_adobe_regression(adobe_fixtures):
    """Reproduce el caso de referencia de la spec:
    Múltiplos=658, DCF=289, EPS Model=555 → promedio=500, con tolerancia ±1%.

    Fixtures: `tests/fixtures/adobe/*.json` — reconstrucción sintética
    calibrada matemáticamente hacia los valores de referencia de la spec
    (ver `tests/fixtures/adobe/README.md`), no una captura real de FMP/FRED
    (sin acceso a red/API key real en este entorno de implementación).
    """
    income_statements = adobe_fixtures["income_statement"]
    cash_flows = adobe_fixtures["cash_flow"]
    balance_sheets = adobe_fixtures["balance_sheet"]
    quote = adobe_fixtures["quote"][0]
    profile = adobe_fixtures["profile"][0]
    peers_metrics = adobe_fixtures["peers_metrics"]

    # Orden cronológico (FMP entrega más reciente primero -> se invierte)
    eps_historial = [s["eps"] for s in reversed(income_statements)]
    fcf_historial = [
        s["operatingCashFlow"] + s["capitalExpenditure"]  # capex ya viene negativo
        for s in reversed(cash_flows)
    ]

    eps_ttm = income_statements[0]["eps"]
    shares_outstanding = income_statements[0]["weightedAverageShsOutDil"]

    # PER de cada peer derivado de earningsYield (1/earningsYield, /key-metrics
    # anual) — la API stable de FMP ya no expone `pe` directo, y
    # /key-metrics-ttm es de pago en el plan gratuito (ver peers.get_peer_pe_average).
    per_promedio_peers = sum(
        1 / v[0]["earningsYield"] for v in peers_metrics.values()
    ) / len(peers_metrics)

    y = 0.044  # FRED DGS20 fixture: 4.40% -> 0.044 decimal

    wacc_inputs = {
        "interest_expense": income_statements[0]["interestExpense"],
        "total_debt": balance_sheets[0]["shortTermDebt"] + balance_sheets[0]["longTermDebt"],
        "income_tax_expense": income_statements[0]["incomeTaxExpense"],
        "income_before_tax": income_statements[0]["incomeBeforeTax"],
        "beta": profile["beta"],
        "market_cap": quote["marketCap"],
    }

    result = valuation.compute_valuation(
        eps_ttm=eps_ttm,
        eps_historial=eps_historial,
        per_promedio_peers=per_promedio_peers,
        fcf_historial=fcf_historial,
        y=y,
        wacc_inputs=wacc_inputs,
        shares_outstanding=shares_outstanding,
    )

    assert result.modelos_excluidos == []
    assert result.valor_justo_multiplos is not None
    assert result.valor_justo_graham is not None
    assert result.valor_justo_dcf is not None
    assert result.valor_justo_total is not None

    assert _within_tolerance(result.valor_justo_multiplos, 658), (
        f"Múltiplos={result.valor_justo_multiplos}, esperado ≈658 (±1%)"
    )
    assert _within_tolerance(result.valor_justo_graham, 555), (
        f"Graham={result.valor_justo_graham}, esperado ≈555 (±1%)"
    )
    assert _within_tolerance(result.valor_justo_dcf, 289), (
        f"DCF={result.valor_justo_dcf}, esperado ≈289 (±1%)"
    )
    assert _within_tolerance(result.valor_justo_total, 500), (
        f"Promedio={result.valor_justo_total}, esperado ≈500 (±1%)"
    )

    precio_actual = quote["price"]
    assert precio_actual < result.valor_justo_total  # "barata", consistente con la spec


def test_valuation_adobe_scenarios(adobe_fixtures):
    """Spec Patch [Iter-3], criterio de aceptación: escenario conservador
    idéntico campo a campo a `compute_valuation()`, relación
    `pesimista <= conservador <= optimista` en `valor_justo_total`, y los 3
    modelos calculables en los 3 escenarios (sin exclusiones de nivel 2
    inesperadas) para el fixture de Adobe.

    Tabla verificada a mano por `qa` (Iter-3, sección "Criterios QA", punto 1)
    y confirmada sin ajuste por `architect` (Iter-4) con `DELTA_G=0.03`/
    `DELTA_WACC=0.01`:

    | Escenario    | Múltiplos | Graham (g∓0.03) | DCF (WACC±0.01, g_fcf∓0.03) | Total  |
    |--------------|-----------|------------------|------------------------------|--------|
    | Pesimista    | 600.00    | 435.64           | 225.64                       | 420.43 |
    | Conservador  | 658.00    | 555.64           | 288.82                       | 500.82 |
    | Optimista    | 714.00    | 675.64           | 376.50                       | 588.71 |

    Ninguna guarda nueva o existente se dispara con este fixture: el
    multiplicador de Graham da 21.78 (pesimista) y 33.78 (optimista), lejos
    de cruzar 0; el WACC optimista (9.26%) queda muy por encima del
    `TERMINAL_GROWTH_RATE` (2.5%); los 3 peers (MSFT/ORCL/CRM) tienen
    `earningsYield` válido (nunca el caso degenerado de <2 peers).
    """
    income_statements = adobe_fixtures["income_statement"]
    cash_flows = adobe_fixtures["cash_flow"]
    balance_sheets = adobe_fixtures["balance_sheet"]
    quote = adobe_fixtures["quote"][0]
    profile = adobe_fixtures["profile"][0]
    peers_metrics = adobe_fixtures["peers_metrics"]

    eps_historial = [s["eps"] for s in reversed(income_statements)]
    fcf_historial = [
        s["operatingCashFlow"] + s["capitalExpenditure"] for s in reversed(cash_flows)
    ]
    eps_ttm = income_statements[0]["eps"]
    shares_outstanding = income_statements[0]["weightedAverageShsOutDil"]

    pes = [1 / v[0]["earningsYield"] for v in peers_metrics.values()]
    peer_average = peers.PeerAverageResult(
        per_promedio=sum(pes) / len(pes),
        per_minimo=min(pes),
        per_maximo=max(pes),
        peers_usados=list(peers_metrics.keys()),
    )

    y = 0.044

    wacc_inputs = {
        "interest_expense": income_statements[0]["interestExpense"],
        "total_debt": balance_sheets[0]["shortTermDebt"] + balance_sheets[0]["longTermDebt"],
        "income_tax_expense": income_statements[0]["incomeTaxExpense"],
        "income_before_tax": income_statements[0]["incomeBeforeTax"],
        "beta": profile["beta"],
        "market_cap": quote["marketCap"],
    }

    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=eps_ttm,
        eps_historial=eps_historial,
        peer_average=peer_average,
        fcf_historial=fcf_historial,
        y=y,
        wacc_inputs=wacc_inputs,
        shares_outstanding=shares_outstanding,
    )

    # --- Conservador idéntico campo a campo a compute_valuation() ---
    conservador_directo = valuation.compute_valuation(
        eps_ttm=eps_ttm,
        eps_historial=eps_historial,
        per_promedio_peers=peer_average.per_promedio,
        fcf_historial=fcf_historial,
        y=y,
        wacc_inputs=wacc_inputs,
        shares_outstanding=shares_outstanding,
    )
    assert scenarios.conservador.valor_justo_multiplos == conservador_directo.valor_justo_multiplos
    assert scenarios.conservador.valor_justo_graham == conservador_directo.valor_justo_graham
    assert scenarios.conservador.valor_justo_dcf == conservador_directo.valor_justo_dcf
    assert scenarios.conservador.valor_justo_total == conservador_directo.valor_justo_total
    assert scenarios.conservador.modelos_excluidos == conservador_directo.modelos_excluidos

    # --- Los 3 modelos calculables en los 3 escenarios (sin exclusiones de nivel 2) ---
    assert scenarios.modelos_excluidos_base == []
    for escenario in (scenarios.pesimista, scenarios.conservador, scenarios.optimista):
        assert escenario.modelos_excluidos == []
        assert escenario.valor_justo_multiplos is not None
        assert escenario.valor_justo_graham is not None
        assert escenario.valor_justo_dcf is not None
        assert escenario.valor_justo_total is not None

    # --- Relación pesimista <= conservador <= optimista (no un número fijo) ---
    assert (
        scenarios.pesimista.valor_justo_total
        <= scenarios.conservador.valor_justo_total
        <= scenarios.optimista.valor_justo_total
    )

    # --- Tabla de la spec, verificada cifra por cifra (tolerancia ±1%) ---
    assert _within_tolerance(scenarios.pesimista.valor_justo_multiplos, 600.00)
    assert _within_tolerance(scenarios.conservador.valor_justo_multiplos, 658.00)
    assert _within_tolerance(scenarios.optimista.valor_justo_multiplos, 714.00)

    assert _within_tolerance(scenarios.pesimista.valor_justo_graham, 435.64)
    assert _within_tolerance(scenarios.conservador.valor_justo_graham, 555.64)
    assert _within_tolerance(scenarios.optimista.valor_justo_graham, 675.64)

    assert _within_tolerance(scenarios.pesimista.valor_justo_dcf, 225.64)
    assert _within_tolerance(scenarios.conservador.valor_justo_dcf, 288.82)
    assert _within_tolerance(scenarios.optimista.valor_justo_dcf, 376.50)

    assert _within_tolerance(scenarios.pesimista.valor_justo_total, 420.43)
    assert _within_tolerance(scenarios.conservador.valor_justo_total, 500.82)
    assert _within_tolerance(scenarios.optimista.valor_justo_total, 588.71)
