"""Construcción del texto dummy-friendly con las analogías propias de Daniela.

Usa literalmente "el boletín" (Estado de Resultados), "la foto" (Balance
General), "el extracto" (Flujo de Efectivo), y referencia la analogía de
"Tienda de Limonada" al menos una vez. Indica explícitamente si la empresa
"encaja" o "no encaja" con el perfil de riesgo guardado.

`summary.py` (no `valuation.py`/`rules.py`/`market_context.py`) es
responsable de convertir la estructura de datos pura (`modelos_excluidos`,
flags de `rules.py`, `MomentumResult`/`PeerComparisonResult`) en texto —
mantiene la separación cálculo puro vs. presentación exigida por `qa`.

Spec Patch [Iter-3] (escenarios Pesimista/Conservador/Optimista + Contexto de
mercado) + [Iter-4] (C1: se omite la sección de clasificación cuando los 3
escenarios quedan sin `valor_justo_total`).
"""

from __future__ import annotations

from investbot.valuation import classify_scenario

MODELO_LABELS = {
    "multiplos": "el modelo de Múltiplos",
    "graham": "el modelo Graham (EPS Model)",
    "dcf": "el modelo DCF",
}

MODELO_LABELS_CORTO = {
    "multiplos": "Múltiplos",
    "graham": "Graham EPS Model",
    "dcf": "DCF",
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
    "graham_multiplicador_no_positivo": "en este escenario el crecimiento estimado haría el múltiplo de Graham cero o negativo",
}

MOTIVO_NO_COMPARABLE_LABELS = {
    "eps_no_positivo": "tu PER no aplica por EPS negativo o cero — mirá el P/S como referencia.",
    "sin_peers_validos": "no hay comparables con PER válido en tu set de peers para comparar.",
    "un_solo_peer_valido": (
        "Solo 1 comparable con PER válido en tu set de peers — no hay rango "
        "suficiente para comparar."
    ),
}

POSICION_LABELS = {
    "mas_barata": "más barata",
    "en_linea": "en línea",
    "mas_cara": "más cara",
}

ETIQUETA_MOMENTUM_LABELS = {
    "impulso_positivo": "Por encima de su promedio de 50 días y de 200 días → impulso positivo.",
    "impulso_negativo": "Por debajo de su promedio de 50 días y de 200 días → impulso negativo.",
    "mixto": "Por encima de uno de sus promedios móviles pero no del otro → impulso mixto.",
}

# Orden fijo de presentación (Pesimista | Conservador | Optimista), Decisión
# (e) del Spec Patch Iter-3 — nunca la variante "peor caso/mejor caso".
_MODELOS_ORDEN = [
    ("multiplos", "valor_justo_multiplos"),
    ("graham", "valor_justo_graham"),
    ("dcf", "valor_justo_dcf"),
]

_MODELO_FORMULAS = {
    "multiplos": "EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector",
    "graham": "EPS (TTM) × (8.5 + 2×g) × 4.4 / Y, con g = CAGR histórico de EPS",
    "dcf": "proyección de Flujo de Caja Libre a 5 años + valor terminal, descontados al WACC",
}


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_ratio(value: float) -> str:
    return f"{value:.1f}"


def _cell(escenario: dict, campo: str) -> str:
    value = escenario.get(campo)
    return _fmt_money(value) if value is not None else "N/D"


def build_valuation_scenarios_section(
    scenarios: dict, precio_actual: float, n_peers_validos: int
) -> str:
    """Sección de "Valor Justo" con rango Pesimista | Conservador | Optimista
    por modelo + total, y clasificación barata/cara por escenario (Spec Patch
    Iter-3, secciones 1-3 + Iter-4 C1).

    `scenarios` es la representación en dict de `valuation.ValuationScenarios`
    (`.as_dict()`): claves `pesimista`/`conservador`/`optimista` (cada una un
    dict con `valor_justo_multiplos/graham/dcf/total` + `modelos_excluidos`)
    y `modelos_excluidos_base` (nivel 1, reportado una sola vez).
    """
    pesimista = scenarios["pesimista"]
    conservador = scenarios["conservador"]
    optimista = scenarios["optimista"]
    excluidos_base = scenarios.get("modelos_excluidos_base") or []
    excluidos_base_modelos = {item["modelo"] for item in excluidos_base}

    lines = ["*Rango de Valor Justo (Pesimista | Conservador | Optimista):*"]
    modelos_nivel2_nd: list[tuple[str, str]] = []

    for modelo_key, campo in _MODELOS_ORDEN:
        if modelo_key in excluidos_base_modelos:
            continue
        modelo_label = MODELO_LABELS_CORTO[modelo_key]
        lines.append(
            f"- {modelo_label}: {_cell(pesimista, campo)} | "
            f"{_cell(conservador, campo)} | {_cell(optimista, campo)}"
        )
        formula = _MODELO_FORMULAS.get(modelo_key)
        if formula:
            lines.append(f"  _(fórmula: {formula})_")
        for escenario_nombre, escenario in (
            ("Pesimista", pesimista),
            ("Conservador", conservador),
            ("Optimista", optimista),
        ):
            if escenario.get(campo) is None:
                modelos_nivel2_nd.append((modelo_label, escenario_nombre))
        if modelo_key == "multiplos" and n_peers_validos < 2:
            lines.append(
                f"  _(no hay rango disponible para Múltiplos: solo {n_peers_validos} "
                "comparable(s) válido(s))_"
            )

    for item in excluidos_base:
        modelo_label = MODELO_LABELS.get(item["modelo"], item["modelo"])
        motivo_label = MOTIVO_LABELS.get(item["motivo"], item["motivo"])
        lines.append(f"- {modelo_label} no se pudo calcular: {motivo_label}.")

    total_pes = pesimista.get("valor_justo_total")
    total_cons = conservador.get("valor_justo_total")
    total_opt = optimista.get("valor_justo_total")

    if total_cons is None:
        # Iter-2: ningún modelo pudo calcularse a nivel base -> por
        # construcción, los 3 escenarios quedan en None (Spec Patch Iter-4,
        # C1) -> se omite por completo la sección de clasificación abajo.
        lines.append(
            "\nNo fue posible valorar la empresa con los datos disponibles "
            "(ningún modelo pudo calcularse). Igual te muestro el resto del "
            "análisis abajo."
        )
        return "\n".join(lines)

    lines.append(
        f"\n*Valor Justo Total: {_cell(pesimista, 'valor_justo_total')} | "
        f"{_cell(conservador, 'valor_justo_total')} | "
        f"{_cell(optimista, 'valor_justo_total')}*"
    )

    for modelo_label, escenario_nombre in modelos_nivel2_nd:
        lines.append(
            f"_{modelo_label} no disponible en el escenario {escenario_nombre} "
            "con estos supuestos — se promedia sin él en ese caso._"
        )

    lines.extend(
        _build_classification_lines(precio_actual, total_pes, total_cons, total_opt)
    )

    return "\n".join(lines)


def _build_classification_lines(
    precio_actual: float,
    total_pesimista: float | None,
    total_conservador: float,
    total_optimista: float | None,
) -> list[str]:
    """Regla de combinación (Spec Patch Iter-3, sección 3): consolida si los
    3 escenarios coinciden, desglosa si no coinciden o si alguno es `None`.
    Se llama únicamente cuando `total_conservador` no es `None` (el llamador
    ya maneja el caso "0 de 3 modelos", Iter-4 C1)."""
    clasificaciones = {
        "Pesimista": (classify_scenario(precio_actual, total_pesimista), total_pesimista),
        "Conservador": (classify_scenario(precio_actual, total_conservador), total_conservador),
        "Optimista": (classify_scenario(precio_actual, total_optimista), total_optimista),
    }
    valores = [c for c, _ in clasificaciones.values()]

    if None not in valores and len(set(valores)) == 1:
        etiqueta = "Barata" if valores[0] else "Cara"
        return [
            f"\n{etiqueta} en los 3 escenarios (Pesimista, Conservador y Optimista) "
            "— señal de confianza adicional."
        ]

    lines = [f"\nPrecio actual: {_fmt_money(precio_actual)}"]
    for nombre, (clas, total) in clasificaciones.items():
        if clas is None:
            lines.append(f"- {nombre}: no se pudo determinar en este escenario")
        else:
            etiqueta = "Barata" if clas else "Cara"
            lines.append(f"- {nombre}: {etiqueta} (valor justo {_fmt_money(total)})")
    return lines


def build_market_context_section(
    *, precio_actual: float, momentum: dict, peer_comparison: dict
) -> str:
    """Sección "Contexto de mercado" (Spec Patch Iter-3, sección 6): momentum
    de precio + comparación explícita con peers. Se ubica entre "Pilares de
    buena empresa" y "Encaje con tu perfil de riesgo"."""
    lines = ["*Contexto de mercado:*"]

    pct_high = momentum.get("pct_vs_year_high")
    pct_low = momentum.get("pct_vs_year_low")
    if pct_high is not None and pct_low is not None:
        lines.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_high):.1f}% por "
            f"debajo de su máximo de 52 semanas y un {abs(pct_low):.1f}% por "
            "encima de su mínimo de 52 semanas."
        )
    elif pct_high is not None:
        lines.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_high):.1f}% por "
            "debajo de su máximo de 52 semanas."
        )
    elif pct_low is not None:
        lines.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_low):.1f}% por "
            "encima de su mínimo de 52 semanas."
        )

    etiqueta = momentum.get("etiqueta")
    if etiqueta in ETIQUETA_MOMENTUM_LABELS:
        lines.append(f"- {ETIQUETA_MOMENTUM_LABELS[etiqueta]}")
    # etiqueta == "no_disponible" -> se omite (criterio explícito Iter-3/6.3),
    # no se muestra como ruido "impulso: no disponible".

    posicion = peer_comparison.get("posicion")
    if posicion == "no_comparable":
        motivo = peer_comparison.get("motivo_no_comparable")
        texto = MOTIVO_NO_COMPARABLE_LABELS.get(motivo, "no se pudo comparar con tus peers.")
        lines.append(f"- Comparada con sus comparables del sector: {texto}")
    else:
        peers_str = ", ".join(peer_comparison.get("peers_usados") or [])
        posicion_txt = POSICION_LABELS.get(posicion, posicion)
        per_propio = peer_comparison.get("per_propio")
        per_min = peer_comparison.get("per_minimo_peers")
        per_prom = peer_comparison.get("per_promedio_peers")
        per_max = peer_comparison.get("per_maximo_peers")
        lines.append(
            f"- Comparada con sus comparables del sector ({peers_str}): tu PER "
            f"({_fmt_ratio(per_propio)}) está {posicion_txt} con el rango de tus "
            f"peers (mínimo {_fmt_ratio(per_min)}, promedio {_fmt_ratio(per_prom)}, "
            f"máximo {_fmt_ratio(per_max)})."
        )

    lines.append(
        "\n_Nota: el momentum es un proxy simple de precio, no un índice de "
        "sentimiento de mercado (VIX/Fear & Greed)._"
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
    scenarios: dict,
    n_peers_validos: int,
    momentum: dict,
    peer_comparison: dict,
    risk_fit: dict,
    treasury_source: str | None = None,
    peers_note: str = "PER promedio de un set fijo de comparables, no del sector completo.",
) -> str:
    """Arma la respuesta completa, estilo "explícamelo como si fuera tonto".

    Usa el boletín/la foto/el extracto y la analogía de Tienda de Limonada.

    Orden de lectura (Spec Patch Iter-3, sección 6.3): valor justo → pilares
    → contexto de mercado → encaje de riesgo → notas de transparencia.
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
        ratios_lines.append(
            f"- Liquidez: {ratios['ratio_liquidez']:.2f} (según la foto)"
            " _(fórmula: Activos Circulantes / Pasivos Circulantes)_"
        )
    elif ratios.get("liquidez_sin_pasivos_circulantes"):
        ratios_lines.append("- Liquidez: sin deuda de corto plazo — señal muy positiva")
    if ratios.get("margen_bruto") is not None:
        ratios_lines.append(
            f"- Margen bruto: {ratios['margen_bruto']*100:.1f}%"
            " _(fórmula: (Ventas − Costo de Ventas) / Ventas)_"
        )
    if ratios.get("per") is not None:
        ratios_lines.append(
            f"- PER: {ratios['per']:.2f}"
            " _(fórmula: Precio de la Acción / EPS)_"
        )
    elif ratios.get("per_no_aplicable"):
        ratios_lines.append(
            "- PER: no aplica (EPS negativo o cero) — mirá el P/S como referencia"
        )
    if ratios.get("ps") is not None:
        ratios_lines.append(
            f"- P/S (Precio-Ventas): {ratios['ps']:.2f}"
            " _(fórmula: Capitalización de Mercado / Ventas Totales)_"
        )

    valuation_section = build_valuation_scenarios_section(
        scenarios, precio_actual, n_peers_validos
    )
    pillars_section = build_pillars_section(pillars)
    market_context_section = build_market_context_section(
        precio_actual=precio_actual, momentum=momentum, peer_comparison=peer_comparison
    )
    risk_section = build_risk_fit_section(risk_fit)

    transparency_lines = [
        "_Datos financieros (ingresos, deuda, flujo de caja, cotización, etc.) "
        "obtenidos de Financial Modeling Prep (FMP)._",
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
        market_context_section,
        risk_section,
        "\n".join(transparency_lines),
    ]
    return "\n\n".join(parts)
