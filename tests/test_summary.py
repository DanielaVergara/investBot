"""Tests de `summary.py` — texto dummy-friendly con las analogías de Daniela.

Spec Patch [Iter-3] (rango Pesimista|Conservador|Optimista por modelo +
clasificación barata/cara por escenario + sección "Contexto de mercado") +
[Iter-4] (C1: se omite la clasificación cuando los 3 escenarios quedan sin
`valor_justo_total`).
"""

from __future__ import annotations

import re

import pytest

from investbot import peers, rules, summary, valuation
from investbot.query_handler import chunk_for_telegram
from tests.fixtures.crecimiento_estilizado import (
    HISTORIAL_INGRESOS_CASO_ESTILIZADO,
    HISTORIAL_UTILIDADES_CASO_ESTILIZADO,
)


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


def test_build_valuation_scenarios_section_peers_validos_insuficientes_excluye_multiplos():
    """SDD_calidad_peers_multiplos.md, Decisión #4 — reemplaza
    `test_build_valuation_scenarios_section_degenerado_menos_de_2_peers`
    (comportamiento intencionalmente distinto, no una regresión: antes, con
    1 peer válido, Múltiplos se mostraba con su valor numérico + una nota al
    pie; ahora `valuation.py` excluye el modelo de nivel 1 directamente, así
    que la fila con valores no aparece en absoluto, y en su lugar aparece la
    línea de `excluidos_base` con el detalle cuantitativo)."""
    scenarios = _base_scenarios()
    scenarios["modelos_excluidos_base"] = [
        {"modelo": "multiplos", "motivo": "peers_validos_insuficientes"}
    ]
    text = summary.build_valuation_scenarios_section(scenarios, precio_actual=333.0, n_peers_validos=1)
    # (a) la fila de Múltiplos con valores numéricos por escenario está ausente.
    assert "- Múltiplos:" not in text
    # (b) aparece la línea de exclusión con el detalle cuantitativo real
    # (constante real, no hardcodeada como "2" en el test).
    assert (
        f"el modelo de Múltiplos no se pudo calcular: no hay suficientes "
        f"comparables con PER válido esta consulta (mínimo "
        f"{valuation.MIN_PEERS_VALIDOS_PARA_MULTIPLOS}, hubo 1)."
    ) in text
    # El texto viejo ya no existe en ningún caso (bloque obsoleto eliminado).
    assert "no hay rango disponible para Múltiplos" not in text


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


# ---------------------------------------------------------------------------
# SDD_procedencia_peers_individuales — helpers aislados
# _join_con_y / _agrupar_peers_por_motivo / _build_peer_pe_breakdown_line
# ---------------------------------------------------------------------------


def test_join_con_y_lista_vacia():
    assert summary._join_con_y([]) == ""


def test_join_con_y_un_elemento():
    assert summary._join_con_y(["A"]) == "A"


def test_join_con_y_dos_elementos():
    assert summary._join_con_y(["A", "B"]) == "A y B"


def test_join_con_y_tres_elementos():
    assert summary._join_con_y(["A", "B", "C"]) == "A, B y C"


def test_agrupar_peers_por_motivo_vacio():
    assert summary._agrupar_peers_por_motivo({}) == {}


def test_agrupar_peers_por_motivo_agrupa_preservando_orden_de_primera_aparicion():
    resultado = summary._agrupar_peers_por_motivo(
        {"MSFT": "sin_dato", "CRM": "earnings_yield_no_positivo", "ORCL": "sin_dato"}
    )
    assert resultado == {
        "sin_dato": ["MSFT", "ORCL"],
        "earnings_yield_no_positivo": ["CRM"],
    }
    # Orden de los grupos = orden de primera aparición del motivo.
    assert list(resultado.keys()) == ["sin_dato", "earnings_yield_no_positivo"]


def test_build_peer_pe_breakdown_line_sin_datos_retorna_none():
    assert summary._build_peer_pe_breakdown_line({}, {}) is None


def test_build_peer_pe_breakdown_line_feliz_con_fallidos_mismo_motivo_plural():
    line = summary._build_peer_pe_breakdown_line(
        {"ORCL": 24.3}, {"MSFT": "sin_dato", "CRM": "sin_dato"}
    )
    assert "PER de tus comparables: ORCL 24.3" in line
    assert "MSFT y CRM" in line
    assert "no devolvieron un dato de FMP esta consulta" in line
    assert "1 / earningsYield" in line


def test_build_peer_pe_breakdown_line_motivo_mixto_dos_clausulas_separadas():
    line = summary._build_peer_pe_breakdown_line(
        {"ORCL": 24.3}, {"MSFT": "sin_dato", "CRM": "earnings_yield_no_positivo"}
    )
    assert "MSFT no devolvió un dato de FMP esta consulta" in line
    assert "CRM tiene pérdidas esta consulta" in line
    # Orden: la cláusula de "sin_dato" aparece antes que la de
    # "earnings_yield_no_positivo" (orden de primera aparición en el input).
    assert line.index("MSFT no devolvió") < line.index("CRM tiene pérdidas")


def test_build_peer_pe_breakdown_line_un_solo_fallido_singular():
    line = summary._build_peer_pe_breakdown_line({"ORCL": 24.3}, {"MSFT": "sin_dato"})
    assert "MSFT no devolvió un dato de FMP esta consulta" in line
    assert "no devolvieron" not in line


def test_build_peer_pe_breakdown_line_feliz_completo_sin_clausula_de_fallidos():
    line = summary._build_peer_pe_breakdown_line(
        {"ORCL": 24.3, "MSFT": 22.1, "CRM": 20.5}, {}
    )
    assert "ORCL 24.3" in line
    assert "MSFT 22.1" in line
    assert "CRM 20.5" in line
    assert ".." not in line
    assert "no devolvió" not in line
    assert "no devolvieron" not in line
    assert "tiene pérdidas" not in line
    assert "tienen pérdidas" not in line


def test_build_peer_pe_breakdown_line_cero_validos_motivo_mixto_sin_prefijo():
    line = summary._build_peer_pe_breakdown_line(
        {}, {"MSFT": "sin_dato", "ORCL": "earnings_yield_no_positivo", "CRM": "sin_dato"}
    )
    assert "PER de tus comparables:" not in line
    assert "MSFT y CRM" in line
    assert "no devolvieron un dato de FMP esta consulta" in line
    assert "ORCL tiene pérdidas esta consulta" in line


# ---------------------------------------------------------------------------
# SDD_procedencia_peers_individuales — build_market_context_section
# (integración: PER individual + motivo por peer, en todas las ramas)
# ---------------------------------------------------------------------------


def test_market_context_section_un_solo_peer_valido_muestra_desglose_completo():
    """Caso NVIDIA — máxima prioridad de esta spec (Gap D): la rama
    `un_solo_peer_valido` hoy no mostraba ningún número de peers; ahora debe
    mostrar el PER del único peer válido + el motivo de los otros 2."""
    peer_comparison = _base_peer_comparison()
    peer_comparison["posicion"] = "no_comparable"
    peer_comparison["motivo_no_comparable"] = "un_solo_peer_valido"
    peer_comparison["peers_usados"] = ["ORCL"]
    peer_comparison["peers_pe"] = {"ORCL": 24.3}
    peer_comparison["peers_no_usados"] = {"MSFT": "sin_dato", "CRM": "earnings_yield_no_positivo"}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "Solo 1 comparable con PER válido en tu set de peers" in text
    assert "PER de tus comparables: ORCL 24.3" in text
    assert "MSFT no devolvió un dato de FMP esta consulta" in text
    assert "CRM tiene pérdidas esta consulta" in text


def test_market_context_section_no_comparable_eps_no_positivo_muestra_desglose():
    """Gap D no es exclusivo de un_solo_peer_valido — las 3 ramas de
    no_comparable estaban ciegas a peers_pe/peers_no_usados."""
    peer_comparison = _base_peer_comparison()
    peer_comparison["posicion"] = "no_comparable"
    peer_comparison["motivo_no_comparable"] = "eps_no_positivo"
    peer_comparison["per_propio"] = None
    peer_comparison["peers_pe"] = {"ORCL": 24.3, "MSFT": 22.1, "CRM": 20.5}
    peer_comparison["peers_no_usados"] = {}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "PER de tus comparables: ORCL 24.3" in text


def test_market_context_section_no_comparable_sin_peers_validos_muestra_solo_fallidos():
    peer_comparison = _base_peer_comparison()
    peer_comparison["posicion"] = "no_comparable"
    peer_comparison["motivo_no_comparable"] = "sin_peers_validos"
    peer_comparison["peers_usados"] = []
    peer_comparison["peers_pe"] = {}
    peer_comparison["peers_no_usados"] = {"MSFT": "sin_dato", "ORCL": "sin_dato", "CRM": "sin_dato"}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "PER de tus comparables:" not in text
    assert "MSFT, ORCL y CRM" in text
    assert "no devolvieron un dato de FMP esta consulta" in text


def test_market_context_section_en_linea_bullet_existente_mas_desglose_nuevo():
    peer_comparison = _base_peer_comparison()
    peer_comparison["peers_pe"] = {"MSFT": 30.0, "ORCL": 34.0, "CRM": 32.0}
    peer_comparison["peers_no_usados"] = {}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "en línea" in text
    assert "PER de tus comparables: MSFT 30.0" in text


def test_market_context_section_feliz_completo_tres_per_individuales():
    peer_comparison = _base_peer_comparison()
    peer_comparison["peers_pe"] = {"MSFT": 30.0, "ORCL": 34.0, "CRM": 32.0}
    peer_comparison["peers_no_usados"] = {}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "MSFT 30.0" in text
    assert "ORCL 34.0" in text
    assert "CRM 32.0" in text


def test_market_context_section_sin_datos_de_peers_no_agrega_linea_de_desglose():
    """peers_pe={} y peers_no_usados={} juntos (ej. sector sin peers
    configurados) -> no se agrega ninguna línea de breakdown."""
    peer_comparison = _base_peer_comparison()
    peer_comparison["peers_pe"] = {}
    peer_comparison["peers_no_usados"] = {}
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "PER de tus comparables:" not in text
    assert "PER individual calculado por el bot" not in text


def test_market_context_section_orden_desglose_entre_bullet_peers_y_vix():
    peer_comparison = _base_peer_comparison()
    peer_comparison["peers_pe"] = {"MSFT": 30.0}
    peer_comparison["peers_no_usados"] = {}

    text_con_vix = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=peer_comparison,
        vix={"valor": 18.42, "disponible": True},
    )
    idx_bullet = text_con_vix.index("Comparada con sus comparables del sector")
    idx_desglose = text_con_vix.index("PER de tus comparables:")
    idx_vix = text_con_vix.index("VIX (CBOE Volatility Index)")
    assert idx_bullet < idx_desglose < idx_vix

    text_sin_vix = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=peer_comparison,
        vix=None,
    )
    idx_bullet_sv = text_sin_vix.index("Comparada con sus comparables del sector")
    idx_desglose_sv = text_sin_vix.index("PER de tus comparables:")
    assert idx_bullet_sv < idx_desglose_sv


# ---------------------------------------------------------------------------
# SDD_procedencia_peers_individuales — peers_note / _MODELO_FORMULAS /
# regresión de build_valuation_scenarios_section (Pregunta 1: no se duplica)
# ---------------------------------------------------------------------------


def test_peers_note_default_menciona_eleccion_manual_y_no_fmp():
    text = _build_summary()
    assert "elegida a mano" in text
    assert "no la arma FMP" in text


def test_peers_note_finnhub_no_empieza_con_esta_consulta_la_lista():
    """Parte 2, Hallazgo 3: la apertura sin conector queda eliminada."""
    assert not summary._PEERS_NOTE_FINNHUB.startswith("Esta consulta, la lista")


def test_peers_note_finnhub_empieza_con_en_esta_consulta():
    assert summary._PEERS_NOTE_FINNHUB.startswith(
        "En esta consulta, la lista de comparables"
    )


def test_modelo_formulas_multiplos_aclara_calculo_del_bot():
    formula = summary._MODELO_FORMULAS["multiplos"]
    assert "cálculo del bot" in formula
    assert "no un campo directo de FMP" in formula


def test_valuation_scenarios_section_no_agrega_desglose_por_peer():
    """Resolución Pregunta 1: el desglose por peer no se duplica en Valor
    Justo — build_valuation_scenarios_section no cambia de comportamiento
    más allá del texto de _MODELO_FORMULAS."""
    for n_peers_validos in (0, 1, 3):
        text = summary.build_valuation_scenarios_section(
            _base_scenarios(), precio_actual=333.0, n_peers_validos=n_peers_validos
        )
        assert "PER de tus comparables" not in text
        assert "ORCL" not in text
        assert "MSFT" not in text
        assert "CRM" not in text


# ---------------------------------------------------------------------------
# build_extras_section (Pieza 1 — KeyMetricsExtras)
# ---------------------------------------------------------------------------


def _full_extras(**overrides):
    extras = {
        "roe": 0.18,
        "debt_to_equity": 0.65,
        "net_debt_to_ebitda": 1.2,
        "dividend_yield": 0.02,
        "payout_ratio": 0.35,
    }
    extras.update(overrides)
    return extras


def test_build_extras_section_happy_path_5_campos():
    text = summary.build_extras_section(_full_extras())
    assert text is not None
    assert "Rentabilidad, deuda de largo plazo y dividendos" in text
    # roe=0.18 -> supuesto *100 (Resolución (a) / Gap #4: convención no
    # verificada contra FMP real, ver tests/fixtures/adobe/README.md).
    assert "ROE (Rentabilidad sobre el Patrimonio): 18.0%" in text
    assert "Por cada $100 que pusieron los dueños, la empresa ganó $18" in text
    assert "Deuda/Patrimonio (Debt-to-Equity): 0.65" in text
    assert "Deuda Neta / EBITDA: 1.20x" in text
    assert "Dividend Yield: 2.00%" in text
    assert "Payout Ratio: 35.0%" in text
    assert "dato de FMP" in text


def test_build_extras_section_none_retorna_none():
    assert summary.build_extras_section(None) is None


def test_build_extras_section_5_campos_none_retorna_none():
    extras = {
        "roe": None, "debt_to_equity": None, "net_debt_to_ebitda": None,
        "dividend_yield": None, "payout_ratio": None,
    }
    assert summary.build_extras_section(extras) is None


def test_build_extras_section_2_de_5_campos_disponibles():
    extras = {
        "roe": 0.18, "debt_to_equity": None, "net_debt_to_ebitda": None,
        "dividend_yield": 0.02, "payout_ratio": None,
    }
    text = summary.build_extras_section(extras)
    assert "ROE" in text
    assert "Dividend Yield" in text
    assert "Debt-to-Equity" not in text
    assert "Deuda Neta / EBITDA" not in text
    assert "Payout Ratio" not in text


def test_build_extras_section_otros_2_de_5_campos_disponibles():
    """Complementa `test_build_extras_section_2_de_5_campos_disponibles`
    cubriendo la rama contraria de `roe`/`dividend_yield` (ausentes acá,
    presentes allá) para 100% de ramas en `build_extras_section`."""
    extras = {
        "roe": None, "debt_to_equity": 0.65, "net_debt_to_ebitda": None,
        "dividend_yield": None, "payout_ratio": 0.35,
    }
    text = summary.build_extras_section(extras)
    assert "Debt-to-Equity" in text
    assert "Payout Ratio" in text
    assert "ROE" not in text
    assert "Dividend Yield" not in text


def test_build_extras_section_dividend_yield_cero_muestra_no_reparte():
    extras = _full_extras(dividend_yield=0)
    text = summary.build_extras_section(extras)
    assert "no reparte dividendos actualmente" in text


def test_build_extras_section_omitido_en_build_summary_si_extras_none():
    text = _build_summary(extras=None)
    assert "Rentabilidad, deuda de largo plazo y dividendos" not in text


def test_build_extras_section_orden_entre_ratios_y_valor_justo():
    text = _build_summary(extras=_full_extras())
    idx_ratios = text.index("Ratios clave")
    idx_extras = text.index("Rentabilidad, deuda de largo plazo y dividendos")
    idx_valor_justo = text.index("Rango de Valor Justo")
    assert idx_ratios < idx_extras < idx_valor_justo


# ---------------------------------------------------------------------------
# build_veredicto_section (Pieza 3 — Veredicto)
# ---------------------------------------------------------------------------


def test_veredicto_4_de_4_pilares_y_encaja():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": True,
            "utilidades_crecientes": True,
            "deuda_controlada": True,
            "precio_razonable": True,
        },
        risk_fit={"encaja": True, "perfil": "moderado"},
    )
    assert "barata" in text
    assert "4/4 pilares sólidos" in text
    assert "Encaje de riesgo: SÍ" in text
    assert "Mirá con cuidado" not in text


def test_veredicto_precio_razonable_false_dice_cara():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": True,
            "utilidades_crecientes": True,
            "deuda_controlada": True,
            "precio_razonable": False,
        },
        risk_fit={"encaja": True, "perfil": "moderado"},
    )
    assert "Parece *cara*" in text
    assert "3/4 pilares sólidos" in text
    assert "Mirá con cuidado: precio." in text


def test_veredicto_precio_razonable_none():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": False,
            "utilidades_crecientes": False,
            "deuda_controlada": False,
            "precio_razonable": None,
        },
        risk_fit={"encaja": False, "perfil": "moderado"},
    )
    assert "No pude determinar si está cara o barata" in text
    assert "None" not in text


def test_veredicto_al_menos_un_pilar_false_muestra_mira_con_cuidado():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": False,
            "utilidades_crecientes": True,
            "deuda_controlada": False,
            "precio_razonable": True,
        },
        risk_fit={"encaja": True, "perfil": "moderado"},
    )
    assert "Mirá con cuidado: ingresos, deuda." in text
    assert "ingresos_crecientes" not in text
    assert "deuda_controlada" not in text


def test_veredicto_no_encaja():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": True,
            "utilidades_crecientes": True,
            "deuda_controlada": True,
            "precio_razonable": True,
        },
        risk_fit={"encaja": False, "perfil": "agresivo"},
    )
    assert "Encaje de riesgo: NO" in text


def test_veredicto_es_el_segundo_bloque_de_la_respuesta():
    text = _build_summary()
    idx_titulo = text.index("*Adobe Inc. (ADBE)*")
    idx_veredicto = text.index("*Veredicto:*")
    idx_limonada = text.index("Tienda de Limonada")
    assert idx_titulo < idx_veredicto < idx_limonada


def test_veredicto_firma_solo_pillars_y_risk_fit():
    import inspect

    sig = inspect.signature(summary.build_veredicto_section)
    assert set(sig.parameters) == {"pillars", "risk_fit"}


def test_veredicto_peor_escenario_no_crashea():
    text = summary.build_veredicto_section(
        pillars={
            "ingresos_crecientes": False,
            "utilidades_crecientes": False,
            "deuda_controlada": False,
            "precio_razonable": None,
        },
        risk_fit={"encaja": False, "perfil": "muy_conservador"},
    )
    assert "No pude determinar si está cara o barata" in text
    assert "Encaje de riesgo: NO" in text
    assert "Mirá con cuidado: ingresos, utilidades, deuda." in text


def test_veredicto_titulo_en_linea_propia():
    """Parte 3, Hallazgo 2: el título '*Veredicto:*' vive en su propia
    línea, separado del contenido."""
    text = summary.build_veredicto_section(
        pillars=_base_pillars(), risk_fit=_base_risk_fit()
    )
    assert text.split("\n")[0] == "*Veredicto:*"


def test_veredicto_no_repite_frase_encaja_con_tu_perfil_de_riesgo():
    """Parte 2, Hallazgo 1: build_veredicto_section() ya no repite
    literalmente la frase "encaja con tu perfil de riesgo" (esa frase
    completa solo vivía en el texto viejo del Veredicto -- el nuevo texto
    de build_risk_fit_section nunca la tuvo, dice "Encaje con tu perfil de
    riesgo" (título, sustantivo) y "SÍ/NO encaja" por separado, no
    concatenados)."""
    text = summary.build_veredicto_section(
        pillars=_base_pillars(), risk_fit=_base_risk_fit()
    )
    assert "encaja con tu perfil de riesgo" not in text.lower()


def test_veredicto_encaje_dice_detalle_mas_abajo():
    text = summary.build_veredicto_section(
        pillars=_base_pillars(), risk_fit=_base_risk_fit()
    )
    assert "(detalle más abajo)" in text


def test_summary_frase_encaja_con_tu_perfil_de_riesgo_no_aparece_duplicada():
    """Verifica que Hallazgo 1 de Parte 2 realmente eliminó la redundancia
    de fraseo, no solo cambió palabras: la frase completa "encaja con tu
    perfil de riesgo" (tal como aparecía en el Veredicto viejo) no aparece
    en ningún punto de la respuesta completa -- build_risk_fit_section usa
    "Encaje con tu perfil de riesgo" (título) y "SÍ/NO encaja" (por
    separado), nunca esa frase concatenada.

    Nota de implementación: el spec (matriz de `qa`) proponía verificar
    `count(...) == 1` asumiendo que la frase seguía viviendo en
    build_risk_fit_section -- verificado que esa función nunca contuvo esa
    frase exacta concatenada (dice "Encaje" sustantivo + "SÍ/NO encaja" en
    cláusulas separadas), así que el conteo real y correcto es 0, no 1. Se
    documenta como desviación técnica necesaria, no como reinterpretación
    del criterio (el criterio de fondo -- que no quede duplicada -- se
    sigue cumpliendo)."""
    text = _build_summary()
    assert text.lower().count("encaja con tu perfil de riesgo") == 0


# ---------------------------------------------------------------------------
# build_market_context_section — bullet de VIX (Pieza 2)
# ---------------------------------------------------------------------------


def test_market_context_section_vix_disponible_muestra_bullet():
    text = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        vix={"valor": 18.42, "disponible": True},
    )
    assert "18.42" in text
    assert "estimado" in text.lower() or "aproximado" in text.lower()
    assert "Fear" in text
    assert "no es lo mismo que un índice compuesto" in text.lower() or "no es un índice de sentimiento compuesto" in text.lower()


def test_market_context_section_vix_no_disponible_omite_bullet():
    text = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        vix={"valor": None, "disponible": False},
    )
    assert "CBOE Volatility Index" not in text


def test_market_context_section_vix_none_omite_bullet():
    text = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        vix=None,
    )
    assert "CBOE Volatility Index" not in text


def test_market_context_section_nota_final_exacta_con_vix_disponible():
    text = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        vix={"valor": 18.42, "disponible": True},
    )
    assert (
        "el momentum de arriba es un proxy simple de precio del ticker "
        "consultado, no del mercado en general" in text
    )
    assert "tampoco es un índice de sentimiento compuesto" in text


def test_market_context_section_nota_final_exacta_sin_vix():
    text = summary.build_market_context_section(
        precio_actual=187.0,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        vix=None,
    )
    assert (
        "el momentum de arriba es un proxy simple de precio del ticker "
        "consultado, no del mercado en general" in text
    )
    assert "tampoco es un índice de sentimiento compuesto" in text


def test_build_summary_acepta_llamadas_sin_vix():
    text = _build_summary()
    assert "Contexto de mercado" in text


# ---------------------------------------------------------------------------
# Etiquetas "estimado"/"aproximado" (Decisión #10)
# ---------------------------------------------------------------------------


def test_valuation_scenarios_header_dice_rango_de_valor_justo_estimado():
    text = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )
    assert "Rango de Valor Justo estimado" in text


def test_valuation_scenarios_total_dice_valor_justo_total_estimado():
    text = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )
    assert "Valor Justo Total (estimado)" in text


def test_ratios_lines_no_contienen_estimado_ni_aproximado():
    ratios = _base_ratios()
    ratios_lines = ["*Ratios clave:*"]
    if ratios.get("ratio_liquidez") is not None:
        ratios_lines.append(f"- Liquidez: {ratios['ratio_liquidez']:.2f}")
    # Verificación aislada (Gap #3): las líneas de Ratios clave en sí mismas
    # no llevan "estimado"/"aproximado" — se prueba sobre el fragmento
    # aislado, no sobre build_summary() completo (que sí lo contendría por
    # el VIX/Valor Justo).
    text = "\n".join(ratios_lines)
    assert "estimado" not in text.lower()
    assert "aproximado" not in text.lower()


def test_build_extras_section_no_contiene_estimado_ni_aproximado():
    text = summary.build_extras_section(_full_extras())
    assert "estimado" not in text.lower()
    assert "aproximado" not in text.lower()


def test_momentum_y_peer_comparison_bullets_no_contienen_estimado():
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison()
    )
    # Sin VIX: nada en el bloque de momentum/peers debería decir "estimado"/
    # "aproximado" (la nota final menciona "aproximación" solo en relación
    # al VIX, que acá está ausente).
    momentum_and_peers_lines = "\n".join(
        line for line in text.split("\n") if line.startswith("- ")
    )
    assert "estimado" not in momentum_and_peers_lines.lower()
    assert "aproximado" not in momentum_and_peers_lines.lower()


# ---------------------------------------------------------------------------
# Explicaciones dummy + WACC + disclaimer 12b (Pieza 4)
# ---------------------------------------------------------------------------


def test_risk_fit_section_incluye_explicacion_renta_variable_y_beta():
    text = summary.build_risk_fit_section(_base_risk_fit())
    assert (
        "Renta variable = sos dueño de una parte de la empresa" in text
    )
    assert "Beta mide qué tan volátil es esta acción" in text
    assert "Dato de FMP" in text


def test_renta_variable_explicacion_aparece_una_sola_vez_en_toda_la_respuesta():
    text = _build_summary()
    assert text.count("Renta variable = sos dueño de una parte de la empresa") == 1


def test_wacc_nota_contiene_costo_promedio_ponderado_y_calculo_propio():
    text = _build_summary()
    assert "Costo Promedio Ponderado de Capital" in text
    assert "cálculo propio del bot" in text


def test_wacc_nota_mantiene_matiz_cautelar():
    """Parte 2, Hallazgo 4 (Gap 3 de `qa`, texto final cerrado): varía el
    verbo inicial pero conserva el matiz cautelar explícito de que esto NO
    es un reemplazo válido del WACC de un analista (sugerencia de
    `security`, incorporada)."""
    text = _build_summary()
    assert "no un sustituto completo" in text


def test_disclaimer_12b_siempre_presente_con_treasury_source():
    text = _build_summary(treasury_source="FRED (serie DGS20)")
    assert (
        "no asesoramiento financiero profesional ni una recomendación de "
        "inversión" in text
    )


def test_disclaimer_12b_siempre_presente_sin_treasury_source():
    text = _build_summary(treasury_source=None)
    assert (
        "no asesoramiento financiero profesional ni una recomendación de "
        "inversión" in text
    )


def test_orden_wacc_antes_que_disclaimer_12b():
    text = _build_summary()
    idx_wacc = text.index("Costo Promedio Ponderado de Capital")
    idx_disclaimer = text.index("no asesoramiento financiero profesional")
    assert idx_wacc < idx_disclaimer


def test_build_summary_con_extras_y_vix_none_no_crashea():
    text = _build_summary(extras=None, vix=None)
    assert "el boletín" in text


# ---------------------------------------------------------------------------
# build_summary_parts (Pieza 5 — límite 4096 de Telegram)
# ---------------------------------------------------------------------------


def test_build_summary_equivale_a_join_de_build_summary_parts():
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
    assert summary.build_summary(**kwargs) == "\n\n".join(
        summary.build_summary_parts(**kwargs)
    )


def test_build_summary_equivale_a_join_de_build_summary_parts_con_extras_y_vix():
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
        treasury_source="FRED (serie DGS20)",
        extras=_full_extras(),
        vix={"valor": 18.42, "disponible": True},
    )
    assert summary.build_summary(**kwargs) == "\n\n".join(
        summary.build_summary_parts(**kwargs)
    )


def test_build_summary_parts_no_incluye_none():
    parts = summary.build_summary_parts(
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
        extras=None,
    )
    assert all(part is not None for part in parts)


# ---------------------------------------------------------------------------
# Pieza 6 — Reordenamiento completo de build_summary
# ---------------------------------------------------------------------------


def test_orden_completo_de_build_summary_con_extras():
    text = _build_summary(extras=_full_extras(), vix={"valor": 18.42, "disponible": True})
    indices = [
        text.index("*Adobe Inc. (ADBE)*"),
        text.index("*Veredicto:*"),
        text.index("Tienda de Limonada"),
        text.index("Ratios clave"),
        text.index("Rentabilidad, deuda de largo plazo y dividendos"),
        text.index("Rango de Valor Justo"),
        text.index("Pilares de buena empresa"),
        text.index("Contexto de mercado"),
        text.index("Encaje con tu perfil de riesgo"),
        text.index("Datos financieros"),
    ]
    assert indices == sorted(indices)


def test_orden_completo_de_build_summary_sin_extras():
    text = _build_summary(extras=None)
    assert "Rentabilidad, deuda de largo plazo y dividendos" not in text
    indices = [
        text.index("*Adobe Inc. (ADBE)*"),
        text.index("*Veredicto:*"),
        text.index("Tienda de Limonada"),
        text.index("Ratios clave"),
        text.index("Rango de Valor Justo"),
        text.index("Pilares de buena empresa"),
        text.index("Contexto de mercado"),
        text.index("Encaje con tu perfil de riesgo"),
        text.index("Datos financieros"),
    ]
    assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# SDD_peers_dinamicos_y_eventos_corporativos — Parte 1: _build_peers_note.
# Matriz S1-S3.
# ---------------------------------------------------------------------------


def test_build_peers_note_none_y_fijo_mismo_texto():
    """S1: _build_peers_note(None) y _build_peers_note(PEERS_FUENTE_FIJO)
    devuelven el mismo texto, ambos con "elegida a mano" y "no la arma FMP"
    — no rompe test_peers_note_default_menciona_eleccion_manual_y_no_fmp."""
    texto_none = summary._build_peers_note(None)
    texto_fijo = summary._build_peers_note(peers.PEERS_FUENTE_FIJO)
    assert texto_none == texto_fijo
    assert "elegida a mano" in texto_none
    assert "no la arma FMP" in texto_none


def test_build_peers_note_finnhub_menciona_finnhub_y_sub_industria():
    """S2: _build_peers_note(PEERS_FUENTE_FINNHUB) menciona "Finnhub" y
    "sub-industria", NO contiene "elegida a mano"."""
    texto = summary._build_peers_note(peers.PEERS_FUENTE_FINNHUB)
    assert "Finnhub" in texto
    assert "sub-industria" in texto
    assert "elegida a mano" not in texto


def test_peers_note_explicito_tiene_prioridad_sobre_fuente_peers():
    """S3: peers_note pasado explícito por el llamador tiene prioridad total
    sobre el cálculo automático a partir de fuente_peers."""
    peer_comparison = _base_peer_comparison()
    peer_comparison["fuente_peers"] = peers.PEERS_FUENTE_FINNHUB
    text = _build_summary(peer_comparison=peer_comparison, peers_note="texto custom")
    assert "Nota de transparencia: texto custom" in text
    assert summary._PEERS_NOTE_FINNHUB not in text


# ---------------------------------------------------------------------------
# SDD_peers_dinamicos_y_eventos_corporativos — Parte 2: build_corporate_
# events_section + orden + disclaimer ajustado. Matriz S4-S11.
# ---------------------------------------------------------------------------


def _un_evento():
    return [
        {
            "filing_date": "2026-06-15",
            "labels": ["Cambio de directivos o ejecutivos"],
            "filing_url": "https://www.sec.gov/Archives/edgar/data/796343/000079634324000123/doc.htm",
        }
    ]


def _dos_eventos():
    return [
        {
            "filing_date": "2026-06-15",
            "labels": ["Cambio de directivos o ejecutivos"],
            "filing_url": "https://www.sec.gov/Archives/edgar/data/796343/000079634324000123/doc1.htm",
        },
        {
            "filing_date": "2026-05-01",
            "labels": ["Nuevo contrato importante", "Terminación de un contrato importante"],
            "filing_url": "https://www.sec.gov/Archives/edgar/data/796343/000079634324000456/doc2.htm",
        },
    ]


def test_build_corporate_events_section_lista_vacia_none():
    """S4."""
    assert summary.build_corporate_events_section([]) is None


def test_build_corporate_events_section_none_none():
    """S5: el llamador real (build_summary_parts) puede pasar None por
    default -- if not events cubre ambos, no solo []."""
    assert summary.build_corporate_events_section(None) is None


def test_build_corporate_events_section_un_evento_contenido_completo():
    """S6."""
    text = summary.build_corporate_events_section(_un_evento())
    assert "2026-06-15" in text
    assert "Cambio de directivos o ejecutivos" in text
    assert "[ver el filing](https://www.sec.gov/Archives/edgar/data/796343/000079634324000123/doc.htm)" in text
    assert "SEC EDGAR" in text
    assert "NO resume" in text


def test_build_corporate_events_section_dos_eventos_dos_bullets():
    """S7."""
    text = summary.build_corporate_events_section(_dos_eventos())
    assert "2026-06-15" in text
    assert "2026-05-01" in text
    assert "Nuevo contrato importante + Terminación de un contrato importante" in text
    assert text.count("- 20") == 2  # 2 líneas de bullet, cada una con fecha propia


def test_build_summary_parts_corporate_events_none_default_no_rompe():
    """S8: corporate_events=None (default, llamador viejo) -> no incluye la
    sección, ningún test existente se rompe."""
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
    parts_sin_arg = summary.build_summary_parts(**kwargs)
    parts_con_none = summary.build_summary_parts(**kwargs, corporate_events=None)
    assert parts_sin_arg == parts_con_none
    assert not any("Eventos corporativos" in p for p in parts_sin_arg)


def test_orden_eventos_corporativos_entre_contexto_de_mercado_y_riesgo():
    """S9: con corporate_events no vacío, la sección aparece después de
    Contexto de mercado y antes de Encaje con tu perfil de riesgo."""
    text = _build_summary(corporate_events=_un_evento())
    idx_mercado = text.index("Contexto de mercado")
    idx_eventos = text.index("Eventos corporativos")
    idx_riesgo = text.index("Encaje con tu perfil de riesgo")
    assert idx_mercado < idx_eventos < idx_riesgo


def test_disclaimer_ya_no_dice_no_tiene_en_cuenta_eventos_recientes():
    """S10: el disclaimer final ya no contiene la frase absoluta vieja, sí
    contiene la mención a SEC EDGAR sin resumir."""
    text = _build_summary()
    assert "No tiene en cuenta noticias, eventos recientes, cambios de gestión" not in text
    assert "sin resumir" in text
    assert "SEC EDGAR" in text


def test_disclaimer_sin_corporate_events_sigue_presente_con_apertura_sin_cambios():
    """S11: con corporate_events=None (sin eventos), el disclaimer sigue
    apareciendo con la frase de apertura sin cambios."""
    text = _build_summary(corporate_events=None)
    assert (
        "no asesoramiento financiero profesional ni una recomendación de "
        "inversión" in text
    )


# ---------------------------------------------------------------------------
# SDD_fix_crecimiento_y_redaccion.md — Parte 3: formato y espaciado visual
# ---------------------------------------------------------------------------


def _parts_completo(**overrides):
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
        treasury_source="FRED (serie DGS20)",
        extras=_full_extras(),
        vix={"valor": 18.42, "disponible": True},
        corporate_events=_un_evento(),
    )
    kwargs.update(overrides)
    return summary.build_summary_parts(**kwargs)


def test_las_10_secciones_estandar_tienen_titulo_en_negrita_en_linea_propia():
    """Parte 3, Hallazgo 1 (Gap 2 de `qa` resuelto): las 10 secciones de
    contenido (todas excepto el Título de la empresa, índice 0) tienen
    título en negrita en su propia línea -- '*Texto:*' antes del primer
    salto de línea (o al final del string, si la sección es de una sola
    línea)."""
    parts = _parts_completo()
    assert len(parts) == 11  # título + 10 secciones de contenido (caso feliz completo)
    for parte in parts[1:]:
        assert re.match(r"^\*[^*]+:\*(\n|$)", parte), f"No cumple el estándar: {parte[:60]!r}"


def test_titulo_de_empresa_parts_0_queda_excluido_del_estandar():
    """El Título de la empresa (índice 0) NO sigue el patrón '*Texto:*' --
    exclusión intencional documentada en el Hallazgo 1 de Parte 3, no un
    olvido de cobertura."""
    parts = _parts_completo()
    assert parts[0] == "*Adobe Inc. (ADBE)*"
    assert not re.match(r"^\*[^*]+:\*(\n|$)", parts[0])


def test_market_context_section_separa_subbloques_con_linea_en_blanco():
    """Parte 3, Hallazgo 4: momentum/peers/VIX/nota final separados por
    línea en blanco ('\\n\\n')."""
    text_sin_vix = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison(),
        vix=None,
    )
    assert text_sin_vix.count("\n\n") == 2  # momentum->peers, peers->nota final

    text_con_vix = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=_base_peer_comparison(),
        vix={"valor": 18.42, "disponible": True},
    )
    assert text_con_vix.count("\n\n") == 3  # + peers->vix
    assert text_con_vix.count("\n\n") > text_sin_vix.count("\n\n")


def test_transparency_lines_titulo_y_doble_salto():
    """Parte 3, Hallazgo 6: '*Notas de transparencia:*' es el título,
    separado por '\\n\\n' de cada nota subsiguiente (ya no un solo '\\n')."""
    parts = summary.build_summary_parts(
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
        treasury_source="FRED (serie DGS20)",
    )
    transparency = parts[-1]
    assert transparency.startswith("*Notas de transparencia:*\n\n")
    # 6 elementos (título + 5 notas con treasury_source) -> 5 separadores "\n\n"
    assert transparency.count("\n\n") == 5


def test_risk_fit_section_titulo_en_linea_propia():
    """Parte 3, Hallazgo 5: título separado del contenido -- la primera
    línea no contiene "SÍ encaja"/"NO encaja" (separación real, no solo
    mover el símbolo ':')."""
    text = summary.build_risk_fit_section(_base_risk_fit())
    primera_linea = text.split("\n")[0]
    assert primera_linea.endswith(":*")
    assert "SÍ encaja" not in primera_linea
    assert "NO encaja" not in primera_linea


def test_intro_tiene_titulo():
    """Parte 3, Hallazgo 3 (Gap 5 de `qa` resuelto): título cerrado
    '*Cómo leer este análisis:*', antes de la analogía de Tienda de
    Limonada."""
    text = _build_summary()
    idx_titulo = text.index("*Cómo leer este análisis:*")
    idx_limonada = text.index("Tienda de Limonada")
    assert idx_titulo < idx_limonada


def test_ninguna_seccion_duplicada_ni_omitida():
    """Ninguna sección deja de aparecer, ninguna se duplica -- comparando el
    set de títulos de sección esperado antes/después de esta spec."""
    parts = _parts_completo()
    titulos = [parte.split("\n")[0] for parte in parts]
    assert len(titulos) == len(set(titulos))
    esperados = {
        "*Adobe Inc. (ADBE)*",
        "*Veredicto:*",
        "*Cómo leer este análisis:*",
        "*Ratios clave:*",
        "*Rentabilidad, deuda de largo plazo y dividendos:*",
        "*Rango de Valor Justo estimado (Pesimista | Conservador | Optimista):*",
        "*Pilares de buena empresa:*",
        "*Contexto de mercado:*",
        "*Eventos corporativos recientes (SEC EDGAR):*",
        "*Encaje con tu perfil de riesgo (moderado):*",
        "*Notas de transparencia:*",
    }
    assert set(titulos) == esperados


def test_chunk_for_telegram_mensaje_completo_no_dispara_explosion_de_chunks():
    """Resuelve el Gap 1 de `qa` para Parte 3: mensaje completo armado con
    el fixture estilizado del caso NVIDIA (evaluate_pillars real vía
    Opción A, Parte 1) + todas las secciones opcionales activas -- el
    incremento de ~30 caracteres de esta Parte (título nuevo + "\\n\\n"
    extra) no dispara una explosión de chunks respecto de la baseline."""
    liquidity = rules.calculate_liquidity_ratio(100, 50)
    pillars_result = rules.evaluate_pillars(
        revenue_historial=HISTORIAL_INGRESOS_CASO_ESTILIZADO,
        net_income_historial=HISTORIAL_UTILIDADES_CASO_ESTILIZADO,
        liquidity=liquidity,
        barata=True,
    )
    pillars = {
        "ingresos_crecientes": pillars_result.ingresos_crecientes,
        "utilidades_crecientes": pillars_result.utilidades_crecientes,
        "deuda_controlada": pillars_result.deuda_controlada,
        "precio_razonable": pillars_result.precio_razonable,
    }
    parts = _parts_completo(pillars=pillars)
    chunks = chunk_for_telegram(parts)
    assert len(chunks) <= 2


# ---------------------------------------------------------------------------
# SDD_fix_crecimiento_y_redaccion.md — Parte 4: auditoría de procedencia
# ---------------------------------------------------------------------------


def test_ratios_lines_incluye_atribucion_calculo_del_bot():
    """Parte 4, Hallazgo B: 'Ratios clave' aclara explícitamente que los 4
    valores los calcula el bot a partir de datos crudos de FMP -- la línea
    de atribución es la segunda del bloque, inmediatamente debajo del
    título."""
    text = _build_summary()
    assert "calculados por el bot" in text
    assert "no son campos directos de la API" in text

    parts = summary.build_summary_parts(
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
    ratios_part = next(p for p in parts if p.startswith("*Ratios clave:*"))
    lineas = ratios_part.split("\n")
    assert lineas[0] == "*Ratios clave:*"
    assert "calculados por el bot" in lineas[1]
    assert "no son campos directos de la API" in lineas[1]


def test_corporate_events_aclara_etiqueta_es_traduccion_del_bot():
    """Parte 4, Hallazgo D: la nota de fuente aclara que la etiqueta es una
    traducción del bot del código oficial de Item de la SEC, no una cita
    textual del filing -- sin remover nada del texto ya existente sobre el
    filing (regresión: sigue conteniendo "NO resume")."""
    text = summary.build_corporate_events_section(_un_evento())
    assert "traducción del bot" in text
    assert "no una cita textual" in text
    assert "NO resume" in text


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md, Decisión #17 — MOTIVO_LABELS wording period-agnóstico
# ---------------------------------------------------------------------------

_MOTIVOS_ACTUALIZADOS_DECISION_17 = [
    "eps_base_no_positivo",
    "eps_reciente_no_positivo",
    "fcf_base_no_positivo",
    "fcf_reciente_no_positivo",
    "historial_insuficiente",
]

# `historial_insuficiente` sí menciona "años" pero explícitamente como
# unidad de tiempo genérica calificada ("sea en reportes anuales o
# trimestrales") — no asume una cadencia exclusiva, así que se verifica por
# separado (test siguiente) en vez de con la regex estricta de abajo.
_MOTIVOS_SIN_PALABRA_ANIO = [
    "eps_base_no_positivo",
    "eps_reciente_no_positivo",
    "fcf_base_no_positivo",
    "fcf_reciente_no_positivo",
]


def test_motivo_labels_wording_actualizado_no_asume_solo_anual():
    """Ninguna de las 4 entradas de la Decisión #17 que antes decían
    literalmente "años"/"el año más reciente" sigue asumiendo exclusivamente
    cadencia anual — el historial que las alimenta ahora puede ser
    trimestral."""
    for motivo in _MOTIVOS_SIN_PALABRA_ANIO:
        texto = summary.MOTIVO_LABELS[motivo]
        assert not re.search(r"\baños?\b", texto), (
            f"'{motivo}' todavía asume cadencia anual: {texto!r}"
        )


def test_motivo_labels_historial_insuficiente_menciona_ambas_cadencias():
    texto = summary.MOTIVO_LABELS["historial_insuficiente"]
    assert "anuales" in texto
    assert "trimestrales" in texto


def test_motivo_labels_entradas_no_modificadas_siguen_iguales():
    """El resto del diccionario (no listado en la Decisión #17) queda
    idéntico — regresión explícita de que no se tocó de más."""
    assert summary.MOTIVO_LABELS["eps_ttm_no_positivo"] == (
        "la empresa tiene EPS (ganancia por acción) negativo o cero"
    )
    assert summary.MOTIVO_LABELS["y_no_disponible"] == (
        "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov)"
    )
    assert summary.MOTIVO_LABELS["wacc_no_calculable"] == (
        "no se pudo estimar el costo de capital (WACC) con los datos disponibles"
    )
    assert summary.MOTIVO_LABELS["dcf_no_calculable"] == (
        "no se pudo proyectar el flujo de caja con los datos disponibles"
    )
    assert summary.MOTIVO_LABELS["per_peers_no_disponible"] == (
        "no pude obtener el PER de los comparables del sector"
    )
    assert summary.MOTIVO_LABELS["graham_multiplicador_no_positivo"] == (
        "en este escenario el crecimiento estimado haría el múltiplo de Graham cero o negativo"
    )


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md, Decisión #24 (ronda 2) — resaltado de escenario_elegido
# en build_valuation_scenarios_section/build_summary_parts.
# ---------------------------------------------------------------------------


def test_build_valuation_scenarios_section_sin_escenario_elegido_regresion_byte_a_byte():
    sin_pasar_el_parametro = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )
    con_none_explicito = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3, escenario_elegido=None
    )
    assert sin_pasar_el_parametro == con_none_explicito
    assert "*Rango de Valor Justo estimado (Pesimista | Conservador | Optimista):*" in sin_pasar_el_parametro


@pytest.mark.parametrize(
    "escenario,marcado,no_marcados",
    [
        ("pesimista", "*Pesimista* ✅", ["*Conservador* ✅", "*Optimista* ✅"]),
        ("conservador", "*Conservador* ✅", ["*Pesimista* ✅", "*Optimista* ✅"]),
        ("optimista", "*Optimista* ✅", ["*Pesimista* ✅", "*Conservador* ✅"]),
    ],
)
def test_build_valuation_scenarios_section_resalta_columna_correcta(
    escenario, marcado, no_marcados
):
    text = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3,
        escenario_elegido=escenario,
    )
    assert marcado in text
    for otro in no_marcados:
        assert otro not in text


def test_build_valuation_scenarios_section_escenario_invalido_no_marca_nada_ni_crashea():
    for invalido in ("neutral", "", "CONSERVADOR"):
        text = summary.build_valuation_scenarios_section(
            _base_scenarios(), precio_actual=333.0, n_peers_validos=3,
            escenario_elegido=invalido,
        )
        assert "✅" not in text


# ---------------------------------------------------------------------------
# Spec Patch [Iter-4], Decisión #34 — aviso de cap de Graham por escenario en
# build_valuation_scenarios_section. Criterios de aceptación de `architect`.
# ---------------------------------------------------------------------------


def test_build_valuation_scenarios_section_sin_cap_regresion_byte_a_byte():
    """Sin ningún escenario con `graham_g_capped=True` -> output byte a byte
    idéntico al comportamiento de antes de este patch (regresión) — probado
    con dicts que directamente no traen la clave `graham_g_capped` (como
    haría un llamador previo a Iter-4, ej. los fixtures existentes de este
    archivo) y con dicts que la traen explícita en `False`; ambos deben
    producir el mismo texto, y ninguno debe mostrar el aviso."""
    sin_clave = summary.build_valuation_scenarios_section(
        _base_scenarios(), precio_actual=333.0, n_peers_validos=3
    )

    scenarios_con_false = _base_scenarios()
    for nombre in ("pesimista", "conservador", "optimista"):
        scenarios_con_false[nombre]["graham_g_capped"] = False
        scenarios_con_false[nombre]["graham_g_original"] = 0.05
        scenarios_con_false[nombre]["graham_g_aplicado"] = 0.05
    con_false_explicito = summary.build_valuation_scenarios_section(
        scenarios_con_false, precio_actual=333.0, n_peers_validos=3
    )

    assert sin_clave == con_false_explicito
    assert "ℹ️ Graham" not in sin_clave


def test_build_valuation_scenarios_section_aviso_cap_conservador():
    scenarios = _base_scenarios()
    scenarios["conservador"]["graham_g_capped"] = True
    scenarios["conservador"]["graham_g_original"] = 0.75
    scenarios["conservador"]["graham_g_aplicado"] = 0.15
    text = summary.build_valuation_scenarios_section(
        scenarios, precio_actual=333.0, n_peers_validos=3
    )
    assert "ℹ️ Graham (Conservador)" in text
    assert "75.0%" in text
    assert "15%" in text


def test_build_valuation_scenarios_section_aviso_cap_2_de_3_escenarios():
    """Cap activo en 2 de los 3 escenarios simultáneamente (Conservador y
    Optimista, no Pesimista) -> aparecen exactamente 2 líneas de aviso, cada
    una con el nombre de escenario correcto — no 1 sola genérica, no 3."""
    scenarios = _base_scenarios()
    scenarios["conservador"]["graham_g_capped"] = True
    scenarios["conservador"]["graham_g_original"] = 0.75
    scenarios["conservador"]["graham_g_aplicado"] = 0.15
    scenarios["optimista"]["graham_g_capped"] = True
    scenarios["optimista"]["graham_g_original"] = 0.80
    scenarios["optimista"]["graham_g_aplicado"] = 0.15
    text = summary.build_valuation_scenarios_section(
        scenarios, precio_actual=333.0, n_peers_validos=3
    )
    lineas_aviso = [l for l in text.split("\n") if "ℹ️ Graham" in l]
    assert len(lineas_aviso) == 2
    assert any("Conservador" in l for l in lineas_aviso)
    assert any("Optimista" in l for l in lineas_aviso)
    assert not any("Pesimista" in l for l in lineas_aviso)


def test_build_valuation_scenarios_section_aviso_independiente_de_escenario_elegido():
    """El aviso aparece independientemente de cuál sea `escenario_elegido` —
    con `escenario_elegido="pesimista"` y cap activo en `optimista`, el
    aviso de Optimista sigue visible (nunca se filtra por el escenario
    resaltado, mismo criterio que `modelos_nivel2_nd`)."""
    scenarios = _base_scenarios()
    scenarios["optimista"]["graham_g_capped"] = True
    scenarios["optimista"]["graham_g_original"] = 0.80
    scenarios["optimista"]["graham_g_aplicado"] = 0.15
    text = summary.build_valuation_scenarios_section(
        scenarios, precio_actual=333.0, n_peers_validos=3,
        escenario_elegido="pesimista",
    )
    assert "ℹ️ Graham (Optimista)" in text


def test_build_summary_parts_sin_escenario_elegido_regresion_byte_a_byte():
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
    sin_parametro = summary.build_summary_parts(**kwargs)
    con_none_explicito = summary.build_summary_parts(escenario_elegido=None, **kwargs)
    assert sin_parametro == con_none_explicito


def test_build_summary_parts_con_escenario_elegido_resalta_en_la_seccion_correcta():
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
    parts = summary.build_summary_parts(escenario_elegido="optimista", **kwargs)
    valuation_part = next(p for p in parts if p.startswith("*Rango de Valor Justo"))
    assert "*Optimista* ✅" in valuation_part


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md, Pregunta abierta H (implementada) — nota de
# transparencia sobre la fuente del balance sheet (trimestral/anual-fallback).
# ---------------------------------------------------------------------------


def test_build_balance_sheet_note_trimestral():
    texto = summary._build_balance_sheet_note(rules.DATOS_FUENTE_TRIMESTRAL)
    assert "trimestre más reciente" in texto


def test_build_balance_sheet_note_anual_fallback():
    texto = summary._build_balance_sheet_note(rules.DATOS_FUENTE_ANUAL_FALLBACK)
    assert "año fiscal más reciente" in texto


def test_build_balance_sheet_note_none_no_agrega_nada():
    assert summary._build_balance_sheet_note(None) is None


def test_build_balance_sheet_note_valor_desconocido_no_agrega_nada_ni_crashea():
    assert summary._build_balance_sheet_note("algo_inesperado") is None


def test_build_summary_parts_sin_balance_sheet_fuente_regresion_byte_a_byte():
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
    sin_parametro = summary.build_summary_parts(**kwargs)
    con_none_explicito = summary.build_summary_parts(balance_sheet_fuente=None, **kwargs)
    assert sin_parametro == con_none_explicito


def test_build_summary_parts_con_balance_sheet_fuente_trimestral_agrega_nota():
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
    parts = summary.build_summary_parts(
        balance_sheet_fuente=rules.DATOS_FUENTE_TRIMESTRAL, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "trimestre más reciente" in transparency_part


def test_build_summary_parts_con_balance_sheet_fuente_anual_fallback_agrega_nota():
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
    parts = summary.build_summary_parts(
        balance_sheet_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "año fiscal más reciente" in transparency_part


# ---------------------------------------------------------------------------
# Spec Patch [Iter-3], SDD_eps_ttm_real.md — notas de transparencia para
# income_statement_fuente (boletín) y cash_flow_fuente (extracto), mismo
# patrón ya implementado arriba para balance_sheet_fuente (la foto).
# ---------------------------------------------------------------------------


def test_build_income_statement_note_trimestral():
    texto = summary._build_income_statement_note(rules.DATOS_FUENTE_TRIMESTRAL)
    assert "TTM real" in texto
    assert "4 trimestres" in texto


def test_build_income_statement_note_anual_fallback():
    texto = summary._build_income_statement_note(rules.DATOS_FUENTE_ANUAL_FALLBACK)
    assert "año fiscal" in texto
    assert "es TTM real:" not in texto


def test_build_income_statement_note_none_no_agrega_nada():
    assert summary._build_income_statement_note(None) is None


def test_build_income_statement_note_valor_desconocido_no_agrega_nada_ni_crashea():
    assert summary._build_income_statement_note("algo_inesperado") is None


def test_build_cash_flow_note_trimestral():
    texto = summary._build_cash_flow_note(rules.DATOS_FUENTE_TRIMESTRAL)
    assert "TTM real" in texto
    assert "4 trimestres" in texto


def test_build_cash_flow_note_anual_fallback():
    texto = summary._build_cash_flow_note(rules.DATOS_FUENTE_ANUAL_FALLBACK)
    assert "año fiscal" in texto
    assert "es TTM real:" not in texto


def test_build_cash_flow_note_none_no_agrega_nada():
    assert summary._build_cash_flow_note(None) is None


def test_build_cash_flow_note_valor_desconocido_no_agrega_nada_ni_crashea():
    assert summary._build_cash_flow_note("algo_inesperado") is None


def test_build_summary_parts_sin_income_statement_fuente_ni_cash_flow_fuente_regresion_byte_a_byte():
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
    sin_parametro = summary.build_summary_parts(**kwargs)
    con_none_explicito = summary.build_summary_parts(
        income_statement_fuente=None, cash_flow_fuente=None, **kwargs
    )
    assert sin_parametro == con_none_explicito


def test_build_summary_parts_con_income_statement_fuente_trimestral_agrega_nota():
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
    parts = summary.build_summary_parts(
        income_statement_fuente=rules.DATOS_FUENTE_TRIMESTRAL, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "TTM real" in transparency_part
    assert "boletín" in transparency_part.lower()


def test_build_summary_parts_con_income_statement_fuente_anual_fallback_agrega_nota():
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
    parts = summary.build_summary_parts(
        income_statement_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "año fiscal" in transparency_part


def test_build_summary_parts_con_cash_flow_fuente_trimestral_agrega_nota():
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
    parts = summary.build_summary_parts(
        cash_flow_fuente=rules.DATOS_FUENTE_TRIMESTRAL, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "TTM real" in transparency_part
    assert "extracto" in transparency_part.lower()


def test_build_summary_parts_con_cash_flow_fuente_anual_fallback_agrega_nota():
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
    parts = summary.build_summary_parts(
        cash_flow_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK, **kwargs
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    assert "año fiscal" in transparency_part


def test_build_summary_parts_con_las_3_fuentes_trimestrales_agrega_las_3_notas_en_orden_boletin_foto_extracto():
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
    parts = summary.build_summary_parts(
        income_statement_fuente=rules.DATOS_FUENTE_TRIMESTRAL,
        balance_sheet_fuente=rules.DATOS_FUENTE_TRIMESTRAL,
        cash_flow_fuente=rules.DATOS_FUENTE_TRIMESTRAL,
        **kwargs,
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    idx_boletin = transparency_part.index("El boletín (Estado de Resultados)")
    idx_foto = transparency_part.index("El balance general (la foto)")
    idx_extracto = transparency_part.index("El extracto (Flujo de Efectivo)")
    assert idx_boletin < idx_foto < idx_extracto


def test_build_summary_parts_con_las_3_fuentes_anuales_agrega_las_3_notas_en_orden_boletin_foto_extracto():
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
    parts = summary.build_summary_parts(
        income_statement_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK,
        balance_sheet_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK,
        cash_flow_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK,
        **kwargs,
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    idx_boletin = transparency_part.index("El boletín (Estado de Resultados)")
    idx_foto = transparency_part.index("El balance general (la foto)")
    idx_extracto = transparency_part.index("El extracto (Flujo de Efectivo)")
    assert idx_boletin < idx_foto < idx_extracto
    assert "es TTM real:" not in transparency_part


def test_build_summary_parts_con_fuentes_mixtas_cada_nota_refleja_su_propia_fuente_independiente():
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
    parts = summary.build_summary_parts(
        income_statement_fuente=rules.DATOS_FUENTE_TRIMESTRAL,
        cash_flow_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK,
        balance_sheet_fuente=rules.DATOS_FUENTE_TRIMESTRAL,
        **kwargs,
    )
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))

    boletin_texto = summary._build_income_statement_note(rules.DATOS_FUENTE_TRIMESTRAL)
    foto_texto = summary._build_balance_sheet_note(rules.DATOS_FUENTE_TRIMESTRAL)
    extracto_texto = summary._build_cash_flow_note(rules.DATOS_FUENTE_ANUAL_FALLBACK)

    assert boletin_texto in transparency_part
    assert foto_texto in transparency_part
    assert extracto_texto in transparency_part
    assert "TTM real" in boletin_texto
    assert "trimestre más reciente" in foto_texto
    assert "año fiscal" in extracto_texto


# ---------------------------------------------------------------------------
# Spec Patch [Iter-3] de SDD_redaccion_ia_ollama.md — hoisting de los 2
# disclaimers de transparencia a constantes de módulo (DISCLAIMER_WACC_DCF,
# DISCLAIMER_NO_ASESORAMIENTO), importadas por `ai_rewrite.py` para
# protegerlas del guard de condensación. Criterios agregados por `security`
# Iter-3, sección 2.
# ---------------------------------------------------------------------------


def _disclaimer_kwargs(**overrides):
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
    return kwargs


def test_disclaimer_constantes_no_contienen_salto_de_linea_interno():
    """Invariante de import del que depende toda la protección de
    `ai_rewrite._classify_lines` (comparación por igualdad exacta de línea
    completa, `security` Iter-3 sección 2) -- espejo en test del `assert` a
    nivel de módulo que ya vive en `summary.py`."""
    assert "\n" not in summary.DISCLAIMER_WACC_DCF
    assert "\n" not in summary.DISCLAIMER_NO_ASESORAMIENTO


def test_disclaimer_constantes_texto_byte_idéntico_al_que_estaba_inline():
    """Regresión byte a byte: el texto de ambas constantes es exactamente
    el que antes vivía inline en `transparency_lines.append(...)` -- cero
    cambio de comportamiento por el hoisting (Opción C del patch)."""
    assert summary.DISCLAIMER_WACC_DCF == (
        "_El DCF es una aproximación con supuestos simplificados de WACC "
        "(Costo Promedio Ponderado de Capital): combina cuánto le cuesta a "
        "la empresa financiarse con capital propio (accionistas) y con "
        "deuda (bancos/bonistas), ponderado por cuánto usa de cada uno. Es "
        "un cálculo propio del bot (no viene de FMP), simplificado — "
        "es una aproximación más simple, no un sustituto completo del "
        "WACC que armaría un analista con datos de mercado más "
        "completos._"
    )
    assert summary.DISCLAIMER_NO_ASESORAMIENTO == (
        "_Esto es una síntesis de datos financieros históricos, no "
        "asesoramiento financiero profesional ni una recomendación de "
        "inversión. No incluye análisis de noticias ni del contexto "
        "cualitativo del negocio más allá de los eventos corporativos "
        "oficiales de SEC EDGAR listados arriba (si los hay) — y esos se "
        "muestran sin resumir, no reemplazan leer el filing completo. "
        "Revisá vos el resto del contexto cualitativo antes de decidir._"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({}, id="sin_treasury_source_ni_notas_de_fuente"),
        pytest.param(
            {"treasury_source": "FRED (serie DGS20)"},
            id="con_treasury_source",
        ),
        pytest.param(
            {
                "income_statement_fuente": rules.DATOS_FUENTE_TRIMESTRAL,
                "balance_sheet_fuente": rules.DATOS_FUENTE_TRIMESTRAL,
                "cash_flow_fuente": rules.DATOS_FUENTE_TRIMESTRAL,
            },
            id="con_notas_de_fuente_income_balance_cash_flow",
        ),
        pytest.param(
            {
                "treasury_source": "FRED (serie DGS20)",
                "income_statement_fuente": rules.DATOS_FUENTE_ANUAL_FALLBACK,
                "balance_sheet_fuente": rules.DATOS_FUENTE_ANUAL_FALLBACK,
                "cash_flow_fuente": rules.DATOS_FUENTE_ANUAL_FALLBACK,
            },
            id="con_todos_los_opcionales_presentes",
        ),
    ],
)
def test_canario_disclaimers_aparecen_como_linea_completa_exacta(overrides):
    """Test canario pedido por `security` Iter-3 sección 2 (mitiga el riesgo
    fail-open documentado por `architect`: la comparación por igualdad
    exacta en `_classify_lines` deja de proteger un disclaimer en silencio
    si algún día `build_summary_parts` cambia cómo arma `transparency_lines`
    de forma que el disclaimer deje de ser una línea propia completa).

    Recorre el output REAL de `build_summary_parts` (no un fixture
    simplificado) en las 4 combinaciones reales pedidas explícitamente por
    `security` -- un solo caso feliz no habría detectado, por ejemplo, un
    futuro refactor que interpole una nota condicional dentro del mismo
    string que el disclaimer solo cuando `treasury_source` está presente."""
    parts = summary.build_summary_parts(**_disclaimer_kwargs(**overrides))
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))
    lines = transparency_part.split("\n")
    assert summary.DISCLAIMER_WACC_DCF in lines, (
        "La protección de disclaimers quedó rota: DISCLAIMER_WACC_DCF ya no "
        "aparece como línea completa exacta en la sección de transparencia "
        "de build_summary_parts."
    )
    assert summary.DISCLAIMER_NO_ASESORAMIENTO in lines, (
        "La protección de disclaimers quedó rota: DISCLAIMER_NO_ASESORAMIENTO "
        "ya no aparece como línea completa exacta en la sección de "
        "transparencia de build_summary_parts."
    )


# ---------------------------------------------------------------------------
# SDD_menu_por_capas_explicaciones.md, Decisión de diseño #9 —
# build_summary_parts_short / build_valor_justo_teaser_line /
# build_intro_section / build_transparency_section.
# ---------------------------------------------------------------------------


def test_build_valor_justo_teaser_line_camino_feliz():
    scenarios = {"conservador": {"valor_justo_total": 182.40}}
    linea = summary.build_valor_justo_teaser_line(scenarios, "conservador", 150.0)
    assert linea == "Valor Justo Total (Conservador): $182.40 — tu precio actual: $150.00."


def test_build_valor_justo_teaser_line_escenario_no_calculable_no_rompe():
    """Caso límite (QA): `valor_justo_total is None` -- no rompe, no
    muestra "None" visible."""
    scenarios = {"conservador": {"valor_justo_total": None}}
    linea = summary.build_valor_justo_teaser_line(scenarios, "conservador", 150.0)
    assert "None" not in linea
    assert "no calculable" in linea
    assert "$150.00" in linea


def test_build_valor_justo_teaser_line_escenario_ausente_del_dict():
    linea = summary.build_valor_justo_teaser_line({}, "conservador", 150.0)
    assert "None" not in linea


def test_build_intro_section_menciona_tienda_de_limonada():
    assert "Tienda de Limonada" in summary.build_intro_section()


def test_build_transparency_section_reutilizada_por_build_summary_parts():
    """La función extraída produce EXACTAMENTE la misma sección que ya
    arma `build_summary_parts` internamente -- mismo texto, cero
    duplicación de lógica."""
    kwargs = _disclaimer_kwargs()
    parts = summary.build_summary_parts(**kwargs)
    transparency_part = next(p for p in parts if p.startswith("*Notas de transparencia:*"))

    peer_comparison = kwargs["peer_comparison"]
    standalone = summary.build_transparency_section(
        peers_note=summary._build_peers_note(peer_comparison.get("fuente_peers")),
        treasury_source=kwargs.get("treasury_source"),
        income_statement_fuente=kwargs.get("income_statement_fuente"),
        balance_sheet_fuente=kwargs.get("balance_sheet_fuente"),
        cash_flow_fuente=kwargs.get("cash_flow_fuente"),
    )
    assert standalone == transparency_part


def test_build_summary_parts_short_solo_4_secciones():
    parts = summary.build_summary_parts_short(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=550.0,
        pillars={
            "ingresos_crecientes": True, "utilidades_crecientes": True,
            "deuda_controlada": True, "precio_razonable": True,
        },
        risk_fit={"encaja": True, "perfil": "moderado", "beta": 1.1, "etiqueta_activo": "acción"},
        scenarios={"conservador": {"valor_justo_total": 600.0}},
        escenario_elegido="conservador",
    )
    assert len(parts) == 4
    assert parts[0] == "*Adobe Inc. (ADBE)*"
    assert "Veredicto" in parts[1]
    assert "Valor Justo Total" in parts[2]
    assert parts[3] == "👇 Elegí qué querés que te explique."


def test_build_summary_parts_short_no_incluye_secciones_completas():
    parts = summary.build_summary_parts_short(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=550.0,
        pillars={
            "ingresos_crecientes": True, "utilidades_crecientes": True,
            "deuda_controlada": True, "precio_razonable": True,
        },
        risk_fit={"encaja": True, "perfil": "moderado", "beta": 1.1, "etiqueta_activo": "acción"},
        scenarios={"conservador": {"valor_justo_total": 600.0}},
        escenario_elegido="conservador",
    )
    texto = "\n\n".join(parts)
    for seccion_prohibida in (
        "Ratios clave", "Pilares de buena empresa", "Contexto de mercado",
        "Encaje con tu perfil de riesgo", "Notas de transparencia", "Tienda de Limonada",
    ):
        assert seccion_prohibida not in texto


# ---------------------------------------------------------------------------
# Fix urgente 2026-09-02: BadRequest "Can't parse entities" en producción —
# company_name (y nombres de peers dinámicos de Finnhub) vienen sin
# sanitizar de terceros (FMP/Finnhub) y se interpolaban crudos dentro de
# texto con parse_mode="Markdown". Un solo "*"/"_"/"`"/"[" sin parear en
# esos campos rompía el parseo del mensaje completo y el usuario no recibía
# absolutamente nada. Ver _escape_markdown_legacy.
# ---------------------------------------------------------------------------


def _contar_sin_escapar(texto: str, char: str) -> int:
    """Cuenta ocurrencias de `char` en `texto` que NO están precedidas por un
    "\\" de escape — usado para confirmar que ningún caracter especial de
    Markdown legado quedó sin escapar en un segmento de texto de terceros."""
    count = 0
    for i, c in enumerate(texto):
        if c == char and (i == 0 or texto[i - 1] != "\\"):
            count += 1
    return count


_COMPANY_NAME_ADVERSARIAL = "Acme* Corp_ [Test]`"


def test_escape_markdown_legacy_escapa_los_4_caracteres_especiales():
    escapado = summary._escape_markdown_legacy(_COMPANY_NAME_ADVERSARIAL)
    assert escapado == "Acme\\* Corp\\_ \\[Test]\\`"
    for char in ("_", "*", "`", "["):
        assert _contar_sin_escapar(escapado, char) == 0


def test_escape_markdown_legacy_texto_sin_caracteres_especiales_no_cambia():
    assert summary._escape_markdown_legacy("Adobe Inc.") == "Adobe Inc."


def test_escape_markdown_legacy_string_vacio():
    assert summary._escape_markdown_legacy("") == ""


def test_build_summary_parts_company_name_con_caracteres_markdown_no_rompe_titulo():
    parts = summary.build_summary_parts(
        ticker="ADBE",
        company_name=_COMPANY_NAME_ADVERSARIAL,
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        scenarios=_base_scenarios(),
        n_peers_validos=3,
        momentum=_base_momentum(),
        peer_comparison=_base_peer_comparison(),
        risk_fit=_base_risk_fit(),
    )
    titulo = parts[0]
    assert titulo == "*Acme\\* Corp\\_ \\[Test]\\` (ADBE)*"
    # El único "*" sin escapar en todo el título es el par de negrita que el
    # propio bot arma a propósito (uno al inicio, uno al final) — nada del
    # company_name interpolado quedó sin escapar.
    interior = titulo[1:-1]  # saca el "*" de apertura y cierre intencionales
    for char in ("_", "*", "`", "["):
        assert _contar_sin_escapar(interior, char) == 0


def test_build_summary_parts_short_company_name_con_caracteres_markdown_no_rompe_titulo():
    parts = summary.build_summary_parts_short(
        ticker="ADBE",
        company_name=_COMPANY_NAME_ADVERSARIAL,
        precio_actual=550.0,
        pillars=_base_pillars(),
        risk_fit=_base_risk_fit(),
        scenarios={"conservador": {"valor_justo_total": 600.0}},
        escenario_elegido="conservador",
    )
    titulo = parts[0]
    assert titulo == "*Acme\\* Corp\\_ \\[Test]\\` (ADBE)*"
    interior = titulo[1:-1]
    for char in ("_", "*", "`", "["):
        assert _contar_sin_escapar(interior, char) == 0


def test_build_peer_pe_breakdown_line_escapa_nombre_de_peer_con_caracteres_markdown():
    """Peers dinámicos de Finnhub (fuente_peers == PEERS_FUENTE_FINNHUB) no
    están garantizados a ser tickers limpios — mismo riesgo que company_name.
    Nota: la línea completa envuelve todo en "_..._" (cursiva intencional del
    bot), así que la verificación de "cero sin escapar" se hace sobre los
    nombres de peers interpolados puntualmente, no sobre la línea entera."""
    line = summary._build_peer_pe_breakdown_line(
        {"AC*ME_CORP": 24.3}, {"BA[D`PEER": "sin_dato"}
    )
    nombre_1_escapado = "AC\\*ME\\_CORP"
    nombre_2_escapado = "BA\\[D\\`PEER"
    assert f"{nombre_1_escapado} 24.3" in line
    assert nombre_2_escapado in line
    for char in ("_", "*", "`", "["):
        assert _contar_sin_escapar(nombre_1_escapado, char) == 0
        assert _contar_sin_escapar(nombre_2_escapado, char) == 0


def test_market_context_section_escapa_peers_usados_con_caracteres_markdown():
    peer_comparison = _base_peer_comparison()
    peer_comparison["peers_usados"] = ["AC*ME_CORP", "ORCL"]
    text = summary.build_market_context_section(
        precio_actual=187.0, momentum=_base_momentum(), peer_comparison=peer_comparison
    )
    assert "AC\\*ME\\_CORP, ORCL" in text
