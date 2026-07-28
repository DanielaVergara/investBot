"""Tests de `summary.py` — texto dummy-friendly con las analogías de Daniela."""

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


def _base_valuation():
    return {
        "valor_justo_multiplos": 658.0,
        "valor_justo_graham": 555.0,
        "valor_justo_dcf": 289.0,
        "valor_justo_total": 500.0,
        "modelos_excluidos": [],
    }


def _base_risk_fit():
    return {"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"}


def test_summary_incluye_terminos_dummy_friendly():
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
        treasury_source="FRED (serie DGS20)",
    )
    assert "el boletín" in text
    assert "la foto" in text
    assert "el extracto" in text
    assert "Tienda de Limonada" in text


def test_summary_indica_encaja():
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
    )
    assert "SÍ encaja" in text


def test_summary_indica_no_encaja():
    risk_fit = _base_risk_fit()
    risk_fit["encaja"] = False
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=risk_fit,
    )
    assert "NO encaja" in text


def test_summary_clasificacion_barata():
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,  # < 500 valor justo
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
    )
    assert "barata" in text


def test_summary_clasificacion_cara():
    valuation = _base_valuation()
    valuation["valor_justo_total"] = 100.0
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,  # > 100 valor justo
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=valuation,
        risk_fit=_base_risk_fit(),
    )
    assert "cara" in text


def test_summary_ventaja_competitiva_siempre_revisar_manualmente():
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=_base_ratios(),
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
    )
    assert "revisar manualmente" in text


def test_build_valuation_section_1_de_3_modelos_etiqueta_aproximado():
    valuation = {
        "valor_justo_multiplos": None,
        "valor_justo_graham": None,
        "valor_justo_dcf": 289.0,
        "valor_justo_total": 289.0,
        "modelos_excluidos": [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
        ],
    }
    text = summary.build_valuation_section(valuation, precio_actual=100.0)
    assert "aproximado, basado en un solo modelo" in text
    assert "EPS" in text or "eps" in text.lower()


def test_build_valuation_section_0_de_3_modelos():
    valuation = {
        "valor_justo_multiplos": None,
        "valor_justo_graham": None,
        "valor_justo_dcf": None,
        "valor_justo_total": None,
        "modelos_excluidos": [
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "historial_insuficiente"},
        ],
    }
    text = summary.build_valuation_section(valuation, precio_actual=100.0)
    assert "no fue posible valorar" in text.lower()


def test_summary_liquidez_sin_pasivos_circulantes_muestra_mensaje():
    ratios = _base_ratios()
    ratios["ratio_liquidez"] = None
    ratios["liquidez_sin_pasivos_circulantes"] = True
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=ratios,
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
    )
    assert "señal muy positiva" in text


def test_summary_per_no_aplicable_muestra_ps():
    ratios = _base_ratios()
    ratios["per"] = None
    ratios["per_no_aplicable"] = True
    text = summary.build_summary(
        ticker="ADBE",
        company_name="Adobe Inc.",
        precio_actual=333.0,
        ratios=ratios,
        pillars=_base_pillars(),
        valuation=_base_valuation(),
        risk_fit=_base_risk_fit(),
    )
    assert "no aplica" in text.lower()
    assert "P/S" in text
