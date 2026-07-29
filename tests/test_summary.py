"""Tests de `summary.py` — texto dummy-friendly con las analogías de Daniela.

Spec Patch [Iter-3] (rango Pesimista|Conservador|Optimista por modelo +
clasificación barata/cara por escenario + sección "Contexto de mercado") +
[Iter-4] (C1: se omite la clasificación cuando los 3 escenarios quedan sin
`valor_justo_total`).
"""

from __future__ import annotations

from investbot import summary


def _base_ratios():
    return {
        "ratio_liquidez": 1.5,
        "liquidez_sin_pasivos_circulantes": False,
        "margen_bruto": 0.4,
        "per": 15.0,
        "per_no_aplicable": False,
        "ps": 3.0,
    }


def _base_pillars():
    return {
        "ingresos_crecientes": True,
        "utilidades_crecientes": True,
        "deuda_controlada": True,
        "precio_razonable": True,
    }


def _scenario(multiplos, graham, dcf, total, modelos_excluidos=None):
    return {
        "valor_justo_multiplos": multiplos,
        "valor_justo_graham": graham,
        "valor_justo_dcf": dcf,
        "valor_justo_total": total,
        "modelos_excluidos": modelos_excluidos or [],
    }


def _base_scenarios():
    """Los 3 escenarios calculables, sin ninguna exclusión (caso Adobe)."""
    return {
        "pesimista": _scenario(600.0, 435.64, 225.64, 420.43),
        "conservador": _scenario(658.0, 555.64, 288.82, 500.82),
        "optimista": _scenario(714.0, 675.64, 376.50, 588.71),
        "modelos_excluidos_base": [],
    }


def _base_momentum():
    return {
        "pct_vs_year_high": -4.2,
        "pct_vs_year_low": 18.6,
        "pct_vs_avg_50": 3.5,
        "pct_vs_avg_200": 6.0,
        "etiqueta": "impulso_positivo",
    }


def _base_peer_comparison():
    return {
        "per_propio": 28.4,
        "per_minimo_peers": 22.1,
        "per_promedio_peers": 27.9,
        "per_maximo_peers": 33.5,
        "peers_usados": ["MSFT", "ORCL", "CRM"],
        "posicion": "en_linea",
        "motivo_no_comparable": None,
    }


def _base_risk_fit():
    return {"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"}


def _build_summary(**overrides):
    kwargs = dict(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        scenarios=_base_scenarios(),
        n_peers_validos=3,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        risk_fit=_base_risk_fit(),
    )
    kwargs.update(overrides)
    return summary.build_summary(**kwargs)


def test_summary_incluye_terminos_dummy_friendly():
    text = _build_summary(treasury_source="FRED (serie DGS20)")
    assert "el boletín" in text
    assert "la foto" in text
    assert "el extracto" in text
    assert "Tienda de Limonada" in text


def test_summary_indica_encaja():
    text = _build_summary()
    assert "SÍ encaja" in text


def test_summary_indica_no_encaja():
    risk_fit = _base_risk_fit()
    risk_fit["encaja"] = False
    text = _build_summary(risk_fit=risk_fit)
    assert "NO encaja" in text


def test_summary_ventaja_competitiva_siempre_revisar_manualmente():
    text = _build_summary()
    assert "revisar manualmente" in text


def test_summary_liquidez_sin_pasivos_circulantes_muestra_mensaje():
    ratios = _base_ratios()
    ratios["ratio_liquidez"] = None
    ratios["liquidez_sin_pasivos_circulantes"] = True
    text = _build_summary(ratios=ratios)
    assert "señal muy positiva" in text


def test_summary_per_no_aplicable_muestra_ps():
    ratios = _base_ratios()
    ratios["per"] = None
    ratios["per_no_aplicable"] = True
    text = _build_summary(ratios=ratios)
    assert "no aplica" in text.lower()
    assert "P/S" in text


# ---------------------------------------------------------------------------
# build_valuation_scenarios_section — desglose por modelo + total
# ---------------------------------------------------------------------------


def test_build_valuation_scenarios_section_desglosa_los_3_modelos():
    text = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )
    assert "Múltiplos" in text
    assert "Graham EPS Model" in text
    assert "DCF" in text
    assert "$600.00" in text and "$658.00" in text and "$714.00" in text
    assert "Valor Justo Total" in text


def test_build_valuation_scenarios_section_modelo_excluido_nivel1_no_muestra_fila():
    scenarios = _base_scenarios()
    scenarios["pesimista"] = _scenario(None, 435.64, 225.64, 330.64)
    scenarios["conservador"] = _scenario(
        None, 555.64, 288.82, 422.23, [{"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"}]
    )
    scenarios["optimista"] = _scenario(None, 675.64, 376.50, 526.07)
    scenarios["modelos_excluidos_base"] = [
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"}
    ]
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=3)
    assert "- Múltiplos:" not in text
    assert "el modelo de Múltiplos no se pudo calcular" in text
    assert "EPS" in text or "eps" in text.lower()


def test_build_valuation_scenarios_section_nd_puntual_por_exclusion_nivel2():
    scenarios = _base_scenarios()
    scenarios["optimista"] = _scenario(
        714.0, None, 376.50, 545.25, [{"modelo": "graham", "motivo": "graham_multiplicador_no_positivo"}]
    )
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=3)
    assert "N/D" in text
    assert "Graham EPS Model no disponible en el escenario Optimista" in text


def test_build_valuation_scenarios_section_degenerado_menos_de_2_peers():
    scenarios = _base_scenarios()
    scenarios["pesimista"] = _scenario(600.0, 435.64, 225.64, 420.43)
    scenarios["conservador"] = _scenario(600.0, 555.64, 288.82, 481.49)
    scenarios["optimista"] = _scenario(600.0, 675.64, 376.50, 550.71)
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=1)
    assert "no hay rango disponible para Múltiplos" in text
    assert "1 comparable" in text


def test_build_valuation_scenarios_section_0_de_3_modelos_no_fue_posible_valorar():
    scenarios = {
        "pesimista": _scenario(None, None, None, None, [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "historial_insuficiente"},
        ]),
        "conservador": _scenario(None, None, None, None, [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "historial_insuficiente"},
        ]),
        "optimista": _scenario(None, None, None, None, [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "historial_insuficiente"},
        ]),
        "modelos_excluidos_base": [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "historial_insuficiente"},
        ],
    }
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=100.0, n_peers_validos=0)
    assert "no fue posible valorar" in text.lower()


# ---------------------------------------------------------------------------
# Clasificación barata/cara por escenario — regla de combinación (Iter-3 +
# Iter-4/C1)
# ---------------------------------------------------------------------------


def test_combinar_clasificacion_consolidada_barata():
    """Los 3 escenarios dan `True` (barata) -> una sola frase, sin desglose."""
    text = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )
    assert "Barata en los 3 escenarios (Pesimista, Conservador y Optimista)" in text
    assert "- Pesimista:" not in text


def test_combinar_clasificacion_consolidada_cara():
    scenarios = {
        "pesimista": _scenario(60.0, 43.0, 22.0, 42.0),
        "conservador": _scenario(65.0, 55.0, 28.0, 49.0),
        "optimista": _scenario(71.0, 67.0, 37.0, 58.0),
        "modelos_excluidos_base": [],
    }
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=3)
    assert "Cara en los 3 escenarios (Pesimista, Conservador y Optimista)" in text


def test_combinar_clasificacion_desglosada():
    """Al menos un escenario difiere de los otros dos -> desglose explícito,
    ninguna línea se omite."""
    scenarios = {
        "pesimista": _scenario(225.64, 435.64, 225.64, 310.0),  # cara (333 > 310)
        "conservador": _scenario(658.0, 555.64, 288.82, 500.82),  # barata
        "optimista": _scenario(714.0, 675.64, 376.50, 535.0),  # barata
        "modelos_excluidos_base": [],
    }
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=3)
    assert "Precio actual: $333.00" in text
    assert "- Pesimista: Cara (valor justo $310.00)" in text
    assert "- Conservador: Barata (valor justo $500.82)" in text
    assert "- Optimista: Barata (valor justo $535.00)" in text


def test_combinar_clasificacion_con_none():
    """Un escenario con `valor_justo_total=None` (0 de 3 modelos en ESE
    escenario puntual, pero conservador sí tiene valor) -> "no se pudo
    determinar en este escenario", nunca clasificado por default."""
    scenarios = {
        "pesimista": _scenario(None, None, None, None, [
            {"modelo": "multiplos", "motivo": "per_peers_no_disponible"},
            {"modelo": "graham", "motivo": "graham_multiplicador_no_positivo"},
            {"modelo": "dcf", "motivo": "dcf_no_calculable"},
        ]),
        "conservador": _scenario(658.0, 555.64, 288.82, 500.82),
        "optimista": _scenario(714.0, 675.64, 376.50, 588.71),
        "modelos_excluidos_base": [],
    }
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=3)
    assert "- Pesimista: no se pudo determinar en este escenario" in text
    assert "- Conservador: Barata" in text
    assert "- Optimista: Barata" in text


def test_combinar_clasificacion_omitida_0_de_3_modelos():
    """Spec Patch Iter-4, C1: cuando el conservador ya tiene
    `valor_justo_total=None` (0 de 3 modelos, mismos datos que
    `test_valuation_0_de_3_modelos` de Iter-2, extendido a los 3 escenarios),
    no se muestra ninguna línea de clasificación — ni consolidada ni
    desglosada."""
    excluidos = [
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
        {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
        {"modelo": "dcf", "motivo": "historial_insuficiente"},
    ]
    scenarios = {
        "pesimista": _scenario(None, None, None, None, excluidos),
        "conservador": _scenario(None, None, None, None, excluidos),
        "optimista": _scenario(None, None, None, None, excluidos),
        "modelos_excluidos_base": excluidos,
    }
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=100.0, n_peers_validos=0)
    assert "Barata" not in text
    assert "Cara" not in text
    assert "no se pudo determinar en este escenario" not in text
    assert "no fue posible valorar" in text.lower()


# ---------------------------------------------------------------------------
# build_market_context_section — momentum + comparación con peers (Iter-3
# sección 6)
# ---------------------------------------------------------------------------


def test_market_context_section_incluye_nota_de_transparencia():
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison()
    )
    assert "proxy simple de precio" in text
    assert "VIX" in text


def test_market_context_section_impulso_positivo():
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison()
    )
    assert "impulso positivo" in text


def test_market_context_section_solo_pct_vs_year_high_disponible():
    momentum = _base_momentum()
    momentum["pct_vs_year_low"] = None
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=momentum, peer_comparison=_base_peer_comparison()
    )
    assert "por debajo de su máximo de 52 semanas" in text
    assert "mínimo de 52 semanas" not in text


def test_market_context_section_solo_pct_vs_year_low_disponible():
    momentum = _base_momentum()
    momentum["pct_vs_year_high"] = None
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=momentum, peer_comparison=_base_peer_comparison()
    )
    assert "por encima de su mínimo de 52 semanas" in text
    assert "máximo de 52 semanas" not in text


def test_market_context_section_no_disponible_se_omite():
    momentum = _base_momentum()
    momentum["etiqueta"] = "no_disponible"
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=momentum, peer_comparison=_base_peer_comparison()
    )
    assert "no disponible" not in text.lower()
    assert "impulso" not in text.lower()


def test_market_context_section_peer_comparison_en_linea_muestra_valores():
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison()
    )
    assert "en línea" in text
    assert "MSFT" in text


def test_market_context_section_peer_comparison_no_comparable_eps_negativo():
    peer_comparison = _base_peer_comparison()
    peer_comparison["posicion"] = "no_comparable"
    peer_comparison["motivo_no_comparable"] = "eps_no_positivo"
    peer_comparison["per_propio"] = None
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "no aplica" in text.lower() or "no positivo" in text.lower()
    assert "P/S" in text


def test_market_context_section_peer_comparison_un_solo_peer_valido():
    peer_comparison = _base_peer_comparison()
    peer_comparison["posicion"] = "no_comparable"
    peer_comparison["motivo_no_comparable"] = "un_solo_peer_valido"
    peer_comparison["peers_usados"] = ["MSFT"]
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "Solo 1 comparable con PER válido en tu set de peers" in text


def test_summary_incluye_seccion_contexto_de_mercado():
    text = _build_summary()
    assert "Contexto de mercado" in text
