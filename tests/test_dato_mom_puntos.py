"""Regresión: el Dato de `mom` mostraba pct_vs_year_high x100 (-8.2 -> -820.0%).
`market_context._pct_vs` ya devuelve puntos porcentuales."""
from investbot import ai_explain as ae
from investbot.market_context import _pct_vs

DATOS = {
    "etiqueta": "mixto",
    "precio_actual": 91.8,
    "year_high": 100.0,
    "year_low": 60.0,
    "price_avg_50": 95.0,
    "price_avg_200": 85.0,
    "pct_vs_year_high": -8.2,
    "pct_vs_year_low": 53.0,
    "pct_vs_avg_50": -3.4,
    "pct_vs_avg_200": 8.0,
}


def test_pct_vs_devuelve_puntos_porcentuales():
    assert abs(_pct_vs(91.8, 100.0) + 8.2) < 1e-9


def test_dato_mom_muestra_puntos_sin_multiplicar_por_100():
    dato = ae._dato_texto_libre("mom", DATOS)
    assert "-8.2%" in dato
    assert "820" not in dato


def test_dato_mom_consistente_con_cuenta_y_desglose():
    dato = ae._dato_texto_libre("mom", DATOS)
    assert "-8.2%" in ae._cuenta_mom(DATOS)
    assert ae._valor_desglose_mom("vs. máx. 52 semanas", DATOS) == "-8.2%"
    assert "-8.2%" in dato


def test_dato_mom_nd_si_falta():
    assert "N/D" in ae._dato_texto_libre("mom", {"etiqueta": "mixto"})
