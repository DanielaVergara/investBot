"""Construcción del texto dummy-friendly con las analogías propias de Daniela.

Usa literalmente "el boletín" (Estado de Resultados), "la foto" (Balance
General), "el extracto" (Flujo de Efectivo), y referencia la analogía de
"Tienda de Limonada" al menos una vez. Indica explícitamente si la empresa
"encaja" o "no encaja" con el perfil de riesgo guardado.

`summary.py` (no `valuation.py`/`rules.py`) es responsable de convertir la
estructura de datos pura (`modelos_excluidos`, flags de `rules.py`) en texto
— mantiene la separación cálculo puro vs. presentación exigida por `qa`.
"""

from __future__ import annotations

MODELO_LABELS = {
    "multiplos": "el modelo de Múltiplos",
    "graham": "el modelo Graham (EPS Model)",
    "dcf": "el modelo DCF",
}

MOTIVO_LABELS = {
    "eps_ttm_no_positivo": "la empresa tiene EPS (ganancia por acción) negativo o cero",
    "eps_base_no_positivo": "hace unos años la empresa tenía pérdidas, así que no se puede calcular un crecimiento histórico confiable",
    "eps_reciente_no_positivo": "el año más reciente la empresa tuvo pérdidas",
    "fcf_base_no_positivo": "hace unos años el flujo de caja libre era negativo",
    "fcf_reciente_no_positivo": "el flujo de caja libre más reciente es negativo",
    "historial_insuficiente": "no hay suficiente historial financiero (menos de 3 años de datos)",
    "y_no_disponible": "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov)",
    "wacc_no_calculable": "no se pudo estimar el costo de capital (WACC) con los datos disponibles",
    "dcf_no_calculable": "no se pudo proyectar el flujo de caja con los datos disponibles",
    "per_peers_no_disponible": "no pude obtener el PER de los comparables del sector",
}


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def build_valuation_section(valuation: dict, precio_actual: float) -> str:
    """Sección de "Valor Justo" — maneja exclusión total/parcial de modelos (Spec Patch Iter-2)."""
    lines = ["*Valor Justo (motor propio, sin depender de /dcf de FMP):*"]

    if valuation.get("valor_justo_multiplos") is not None:
        lines.append(f"- Múltiplos: {_fmt_money(valuation['valor_justo_multiplos'])}")
    if valuation.get("valor_justo_graham") is not None:
        lines.append(
            f"- Graham EPS Model (g = CAGR histórico, Y = FRED/Treasury.gov): "
            f"{_fmt_money(valuation['valor_justo_graham'])}"
        )
    if valuation.get("valor_justo_dcf") is not None:
        lines.append(
            f"- DCF (proyección propia de flujo de caja + WACC simplificado): "
            f"{_fmt_money(valuation['valor_justo_dcf'])}"
        )

    excluidos = valuation.get("modelos_excluidos") or []
    for item in excluidos:
        modelo_label = MODELO_LABELS.get(item["modelo"], item["modelo"])
        motivo_label = MOTIVO_LABELS.get(item["motivo"], item["motivo"])
        lines.append(f"- {modelo_label} no se pudo calcular: {motivo_label}.")

    total = valuation.get("valor_justo_total")
    n_calculados = 3 - len(excluidos)

    if total is None:
        lines.append(
            "\nNo fue posible valorar la empresa con los datos disponibles "
            "(ningún modelo pudo calcularse). Igual te muestro el resto del "
            "análisis abajo."
        )
        return "\n".join(lines)

    if n_calculados == 1:
        lines.append(
            f"\n*Valor aproximado, basado en un solo modelo*: {_fmt_money(total)}"
        )
    else:
        lines.append(f"\n*Valor Justo Total (promedio): {_fmt_money(total)}*")

    diff_pct = (total - precio_actual) / precio_actual * 100
    barata = precio_actual < total
    etiqueta = "barata" if barata else "cara"
    lines.append(
        f"Precio actual: {_fmt_money(precio_actual)} → la empresa está "
        f"*{etiqueta}* ({_fmt_pct(diff_pct)} vs. el valor justo)."
    )
    return "\n".join(lines)


def build_pillars_section(pillars: dict) -> str:
    def check(value) -> str:
        if value is True:
            return "✅"
        if value is False:
            return "❌"
        return "➖"

    lines = [
        "*Pilares de buena empresa:*",
        f"{check(pillars['ingresos_crecientes'])} Ingresos que crecen año a año (según el boletín)",
        f"{check(pillars['utilidades_crecientes'])} Utilidades positivas y crecientes (según el boletín)",
        f"{check(pillars['deuda_controlada'])} Deuda controlada (según la foto)",
        f"{check(pillars['precio_razonable'])} Precio razonable (PER/múltiplos)",
        "➖ Ventaja competitiva difícil de copiar: revisar manualmente (no es un dato que se calcule)",
    ]
    return "\n".join(lines)


def build_risk_fit_section(risk_fit: dict) -> str:
    encaje_txt = "SÍ encaja" if risk_fit["encaja"] else "NO encaja"
    return (
        f"*Encaje con tu perfil de riesgo ({risk_fit['perfil']}):* {encaje_txt} — "
        f"es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}."
    )


def build_summary(
    *,
    ticker: str,
    company_name: str,
    precio_actual: float,
    ratios: dict,
    pillars: dict,
    valuation: dict,
    risk_fit: dict,
    treasury_source: str | None = None,
    peers_note: str = "PER promedio de un set fijo de comparables, no del sector completo.",
) -> str:
    """Arma la respuesta completa, estilo "explícamelo como si fuera tonto".

    Usa el boletín/la foto/el extracto y la analogía de Tienda de Limonada.
    """
    intro = (
        f"*{company_name} ({ticker})*\n\n"
        "Pensá en una empresa como una Tienda de Limonada: el *boletín* "
        "(Estado de Resultados) te dice cuánto vendió y ganó, *la foto* "
        "(Balance General) te dice qué tiene y qué debe en un momento dado, "
        "y *el extracto* (Flujo de Efectivo) te dice cuánta plata de verdad "
        "entró y salió de la caja."
    )

    ratios_lines = ["*Ratios clave:*"]
    if ratios.get("ratio_liquidez") is not None:
        ratios_lines.append(f"- Liquidez: {ratios['ratio_liquidez']:.2f} (según la foto)")
    elif ratios.get("liquidez_sin_pasivos_circulantes"):
        ratios_lines.append("- Liquidez: sin deuda de corto plazo — señal muy positiva")
    if ratios.get("margen_bruto") is not None:
        ratios_lines.append(f"- Margen bruto: {ratios['margen_bruto']*100:.1f}%")
    if ratios.get("per") is not None:
        ratios_lines.append(f"- PER: {ratios['per']:.2f}")
    elif ratios.get("per_no_aplicable"):
        ratios_lines.append(
            "- PER: no aplica (EPS negativo o cero) — mirá el P/S como referencia"
        )
    if ratios.get("ps") is not None:
        ratios_lines.append(f"- P/S (Precio-Ventas): {ratios['ps']:.2f}")

    valuation_section = build_valuation_section(valuation, precio_actual)
    pillars_section = build_pillars_section(pillars)
    risk_section = build_risk_fit_section(risk_fit)

    transparency_lines = [
        f"_Nota de transparencia: {peers_note}_",
    ]
    if treasury_source:
        transparency_lines.append(
            f"_Y (tasa libre de riesgo) obtenida de: {treasury_source}._"
        )
    transparency_lines.append(
        "_El DCF es una aproximación con supuestos simplificados de WACC._"
    )

    parts = [
        intro,
        "\n".join(ratios_lines),
        valuation_section,
        pillars_section,
        risk_section,
        "\n".join(transparency_lines),
    ]
    return "\n\n".join(parts)
