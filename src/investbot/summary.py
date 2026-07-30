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

from typing import Optional

from investbot import peers
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
    "multiplos": (
        "EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector "
        "(el PER de cada peer es un cálculo del bot: 1 / earningsYield, no "
        "un campo directo de FMP)"
    ),
    "graham": "EPS (TTM) × (8.5 + 2×g) × 4.4 / Y, con g = CAGR histórico de EPS",
    "dcf": "proyección de Flujo de Caja Libre a 5 años + valor terminal, descontados al WACC",
}

_MOTIVO_PEER_LABELS = {
    "sin_dato": {
        "singular": "no devolvió un dato de FMP esta consulta",
        "plural": "no devolvieron un dato de FMP esta consulta",
    },
    "earnings_yield_no_positivo": {
        "singular": (
            "tiene pérdidas esta consulta (earningsYield negativo o cero) "
            "— no se puede calcular su PER"
        ),
        "plural": (
            "tienen pérdidas esta consulta (earningsYield negativo o cero) "
            "— no se puede calcular su PER"
        ),
    },
}


def _join_con_y(items: list[str]) -> str:
    """'A' | 'A y B' | 'A, B y C' — lista en castellano con conjunción final."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} y {items[-1]}"


def _agrupar_peers_por_motivo(peers_no_usados: dict[str, str]) -> dict[str, list[str]]:
    """Agrupa ticker->motivo en motivo->[tickers], preservando el orden de
    aparición (mismo orden que PEERS_BY_SECTOR, porque peers_no_usados se
    construye en ese orden en peers.py)."""
    grupos: dict[str, list[str]] = {}
    for peer, motivo in peers_no_usados.items():
        grupos.setdefault(motivo, []).append(peer)
    return grupos


def _build_peer_pe_breakdown_line(
    peers_pe: dict[str, float], peers_no_usados: dict[str, str]
) -> Optional[str]:
    """Detalle de PER individual por peer + motivo específico por peer que no
    aportó dato + procedencia explícita del cálculo. `None` si no hay
    absolutamente ningún dato de peers que mostrar (sector sin peers
    configurados en PEERS_BY_SECTOR)."""
    if not peers_pe and not peers_no_usados:
        return None

    clausulas: list[str] = []
    if peers_pe:
        listado = ", ".join(
            f"{nombre} {_fmt_ratio(valor)}" for nombre, valor in peers_pe.items()
        )
        clausulas.append(f"PER de tus comparables: {listado}")

    for motivo, nombres in _agrupar_peers_por_motivo(peers_no_usados).items():
        forma = "singular" if len(nombres) == 1 else "plural"
        texto_motivo = _MOTIVO_PEER_LABELS.get(motivo, {}).get(
            forma, "no tiene un PER válido esta consulta"
        )
        clausulas.append(f"{_join_con_y(nombres)} {texto_motivo}")

    texto = " — ".join(clausulas) + "."
    return (
        f"  _{texto} (PER individual calculado por el bot como "
        "1 / earningsYield — earningsYield sí es un dato de FMP, el PER no)._"
    )


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

    lines = ["*Rango de Valor Justo estimado (Pesimista | Conservador | Optimista):*"]
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
        f"\n*Valor Justo Total (estimado): {_cell(pesimista, 'valor_justo_total')} | "
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
    *,
    precio_actual: float,
    momentum: dict,
    peer_comparison: dict,
    vix: Optional[dict] = None,
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
    peers_pe = peer_comparison.get("peers_pe") or {}
    peers_no_usados = peer_comparison.get("peers_no_usados") or {}

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

    peer_breakdown_line = _build_peer_pe_breakdown_line(peers_pe, peers_no_usados)
    if peer_breakdown_line:
        lines.append(peer_breakdown_line)

    if vix and vix.get("disponible"):
        lines.append(f"- VIX (CBOE Volatility Index): {vix['valor']:.2f}")
        lines.append(
            "  _Estimado/aproximado — mide la volatilidad implícita "
            "esperada de opciones sobre el S&P 500 (el mercado en "
            "general), no del ticker que consultaste. NO es lo mismo "
            "que un índice compuesto de sentimiento tipo \"Fear & "
            "Greed\" (ese combina varias señales distintas; el VIX "
            "solo mide volatilidad). Dato de FMP (símbolo ^VIX)._"
        )

    lines.append(
        "\n_Nota: el momentum de arriba es un proxy simple de precio "
        "del ticker consultado, no del mercado en general. El VIX (si "
        "aparece más arriba) es una aproximación de la volatilidad "
        "esperada del mercado en general, no del ticker — tampoco es "
        "un índice de sentimiento compuesto._"
    )
    return "\n".join(lines)


def build_corporate_events_section(events: Optional[list[dict]]) -> Optional[str]:
    """Sección "Eventos corporativos recientes (SEC EDGAR)" (Parte 2,
    `SDD_peers_dinamicos_y_eventos_corporativos.md`). Se omite por completo
    (retorna `None`) si `events` está vacía o es `None` -- mismo criterio de
    "degradar con gracia sin ruido" que build_extras_section/bullet de VIX.
    """
    if not events:
        return None
    lines = ["*Eventos corporativos recientes (SEC EDGAR):*"]
    for ev in events:
        etiquetas = " + ".join(ev["labels"])
        lines.append(f"- {ev['filing_date']}: {etiquetas} — [ver el filing]({ev['filing_url']})")
    lines.append(
        "  _Fuente: SEC EDGAR (oficial, gratis, sin API key) — formularios "
        "8-K que la empresa está obligada a presentar por ley ante eventos "
        "materiales. El bot NO resume el contenido legal del filing (fuera "
        "de alcance, riesgo de alucinación sobre texto legal) — mostramos "
        "fecha + tipo de evento + link para que lo leas vos si te interesa._"
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
        f"es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}.\n"
        "_Renta variable = sos dueño de una parte de la empresa (una "
        "acción). A diferencia de la renta fija (bonos, plazo fijo), acá "
        "no hay un pago garantizado: ganás o perdés según cómo le va al "
        "negocio._\n"
        "_Beta mide qué tan volátil es esta acción comparada con el "
        "mercado en general: 1.0 = se mueve igual que el mercado; más de "
        "1.0 = se mueve más fuerte (para arriba y para abajo); menos de "
        "1.0 = se mueve menos. Dato de FMP._"
    )


_EXTRAS_CAMPOS = ["roe", "debt_to_equity", "net_debt_to_ebitda", "dividend_yield", "payout_ratio"]


def build_extras_section(extras: Optional[dict]) -> Optional[str]:
    """Sección "Rentabilidad, deuda de largo plazo y dividendos" (Decisión
    #3). Se omite por completo (retorna `None`) si los 5 campos vienen
    `None` — mismo criterio de "degradar con gracia sin ruido" que el resto
    del proyecto. Si al menos un campo está disponible, se muestran solo
    las líneas de los campos presentes, sin placeholder "N/D" para los
    ausentes.
    """
    if not extras or all(extras.get(campo) is None for campo in _EXTRAS_CAMPOS):
        return None

    lines = ["*Rentabilidad, deuda de largo plazo y dividendos:*"]

    roe = extras.get("roe")
    if roe is not None:
        lines.append(
            f"- ROE (Rentabilidad sobre el Patrimonio): {roe * 100:.1f}% "
            "_(fórmula: Ganancia Neta / Patrimonio de los Accionistas — "
            "dato ya calculado por FMP, el bot no lo recalcula)_"
        )
        lines.append(
            f"  _Por cada $100 que pusieron los dueños, la empresa ganó "
            f"${roe * 100:.0f} este año._"
        )

    debt_to_equity = extras.get("debt_to_equity")
    if debt_to_equity is not None:
        lines.append(
            f"- Deuda/Patrimonio (Debt-to-Equity): {debt_to_equity:.2f} "
            "_(fórmula: Deuda Total / Patrimonio de los Accionistas — dato de FMP)_"
        )
        lines.append(
            "  _Cuánta deuda tiene la empresa comparada con lo que es de los dueños._"
        )

    net_debt_to_ebitda = extras.get("net_debt_to_ebitda")
    if net_debt_to_ebitda is not None:
        lines.append(
            f"- Deuda Neta / EBITDA: {net_debt_to_ebitda:.2f}x _(dato de FMP)_"
        )
        lines.append(
            "  _Cuántos años de ganancia operativa le tomaría pagar toda su deuda._"
        )

    dividend_yield = extras.get("dividend_yield")
    if dividend_yield is not None:
        lines.append(f"- Dividend Yield: {dividend_yield * 100:.2f}% _(dato de FMP)_")
        if dividend_yield == 0:
            lines.append(
                "  _Esta empresa no reparte dividendos actualmente — reinvierte todo._"
            )
        else:
            lines.append(
                f"  _Por cada $100 invertidos, la empresa te devuelve "
                f"aproximadamente ${dividend_yield * 100:.2f} al año en "
                "dividendos, antes de impuestos._"
            )

    payout_ratio = extras.get("payout_ratio")
    if payout_ratio is not None:
        lines.append(f"- Payout Ratio: {payout_ratio * 100:.1f}% _(dato de FMP)_")
        lines.append(
            "  _% de la ganancia que reparte como dividendo — el resto lo "
            "reinvierte en el negocio._"
        )

    return "\n".join(lines)


def build_veredicto_section(*, pillars: dict, risk_fit: dict) -> str:
    """Resumen ejecutivo de una frase. Se arma puramente a partir de
    `pillars` (ya calculado por rules.evaluate_pillars) y `risk_fit` (ya
    calculado por risk_fit.evaluate_risk_fit) — los mismos dicts que
    build_summary ya recibe hoy. No lee ratios, no lee ROE/deuda/
    dividendos, no aplica ningún umbral nuevo sobre datos financieros:
    "qué mirar con cuidado" es una enumeración de qué pilares booleanos
    ya vienen en False, no una interpretación numérica nueva.
    """
    precio_razonable = pillars.get("precio_razonable")
    if precio_razonable is True:
        precio_txt = "parece *barata* según el valor justo estimado (escenario conservador)"
    elif precio_razonable is False:
        precio_txt = "parece *cara* según el valor justo estimado (escenario conservador)"
    else:
        precio_txt = "no pude determinar si está cara o barata con los datos disponibles"

    claves = ["ingresos_crecientes", "utilidades_crecientes", "deuda_controlada", "precio_razonable"]
    solidos = sum(1 for k in claves if pillars.get(k) is True)
    debiles = [k for k in claves if pillars.get(k) is False]

    encaje_txt = "SÍ encaja" if risk_fit.get("encaja") else "NO encaja"

    cuidado_txt = ""
    if debiles:
        etiquetas = {
            "ingresos_crecientes": "ingresos",
            "utilidades_crecientes": "utilidades",
            "deuda_controlada": "deuda",
            "precio_razonable": "precio",
        }
        cuidado_txt = f" Mirá con cuidado: {', '.join(etiquetas[k] for k in debiles)}."

    return (
        f"*En una frase:* {precio_txt}, con {solidos}/4 pilares sólidos, "
        f"y {encaje_txt} con tu perfil de riesgo ({risk_fit.get('perfil')})."
        f"{cuidado_txt}"
    )


_PEERS_NOTE_FIJO = (
    "PER promedio de un set fijo de comparables, no del sector completo. "
    "Esta lista de comparables (peers) por sector es fija y fue elegida a "
    "mano por quien construyó el bot (ver peers.py, diccionario "
    "PEERS_BY_SECTOR) — no la arma FMP, ni la elige ningún algoritmo "
    "dinámico, ni se actualiza sola; si una empresa deja de ser un buen "
    "comparable, hay que cambiarla manualmente en el código. (Si Finnhub "
    "está configurado y respondió, esta consulta usa sus peers dinámicos "
    "en su lugar — ver arriba si corresponde.)"
)

_PEERS_NOTE_FINNHUB = (
    "Esta consulta, la lista de comparables (peers) se obtuvo "
    "dinámicamente de Finnhub (agrupados por sub-industria, no por "
    "el sector completo) — no es la lista fija de peers.py. Si "
    "Finnhub no responde o no está configurado, el bot usa "
    "automáticamente un respaldo fijo elegido a mano por quien "
    "construyó el bot."
)


def _build_peers_note(fuente_peers: Optional[str]) -> str:
    """Texto de transparencia sobre la fuente de peers usada esta consulta
    (Parte 1, `SDD_peers_dinamicos_y_eventos_corporativos.md`, Decisión #6).

    El texto de la rama `else` (fuente fija/respaldo, o `None` para
    compatibilidad con llamadores viejos) sigue siendo literalmente cierto
    cuando `fuente_peers` es `peers.PEERS_FUENTE_FIJO` o no viene informado
    — la lista fija sigue siendo elegida a mano, solo que ahora es el
    respaldo en vez de la única fuente. Solo cuando `fuente_peers ==
    peers.PEERS_FUENTE_FINNHUB` el texto cambia.
    """
    if fuente_peers == peers.PEERS_FUENTE_FINNHUB:
        return _PEERS_NOTE_FINNHUB
    return _PEERS_NOTE_FIJO


def build_summary_parts(
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
    peers_note: Optional[str] = None,
    extras: Optional[dict] = None,
    vix: Optional[dict] = None,
    corporate_events: Optional[list[dict]] = None,
) -> list[str]:
    """Arma la respuesta completa, estilo "explícamelo como si fuera tonto",
    y devuelve la lista de secciones sin unir (ya filtrada de `None`) — es
    la partición segura que reutiliza `query_handler.chunk_for_telegram`
    para respetar el límite de 4096 caracteres de Telegram (Decisión 16.1):
    cada elemento es, por construcción, una sección completa.

    Usa el boletín/la foto/el extracto y la analogía de Tienda de Limonada.

    Orden de lectura (Decisión #5): Título → Veredicto → Intro Tienda de
    Limonada → Ratios clave → Rentabilidad/deuda/dividendos (se omite si no
    hay datos) → Valor Justo (3 escenarios) → Pilares de buena empresa →
    Contexto de mercado (incluye VIX) → Encaje de riesgo (incluye
    explicaciones dummy) → Notas de transparencia (incluye WACC expandido +
    disclaimer general).
    """
    titulo = f"*{company_name} ({ticker})*"
    veredicto_section = build_veredicto_section(pillars=pillars, risk_fit=risk_fit)

    intro = (
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

    extras_section = build_extras_section(extras)

    valuation_section = build_valuation_scenarios_section(
        scenarios, precio_actual, n_peers_validos
    )
    pillars_section = build_pillars_section(pillars)
    market_context_section = build_market_context_section(
        precio_actual=precio_actual,
        momentum=momentum,
        peer_comparison=peer_comparison,
        vix=vix,
    )
    corporate_events_section = build_corporate_events_section(corporate_events)
    risk_section = build_risk_fit_section(risk_fit)

    peers_note_final = peers_note if peers_note is not None else _build_peers_note(
        peer_comparison.get("fuente_peers")
    )
    transparency_lines = [
        "_Datos financieros (ingresos, deuda, flujo de caja, cotización, etc.) "
        "obtenidos de Financial Modeling Prep (FMP)._",
        f"_Nota de transparencia: {peers_note_final}_",
    ]
    if treasury_source:
        transparency_lines.append(
            f"_Y (tasa libre de riesgo) obtenida de: {treasury_source}._"
        )
    transparency_lines.append(
        "_El DCF es una aproximación con supuestos simplificados de WACC "
        "(Costo Promedio Ponderado de Capital): combina cuánto le cuesta a "
        "la empresa financiarse con capital propio (accionistas) y con "
        "deuda (bancos/bonistas), ponderado por cuánto usa de cada uno. Es "
        "un cálculo propio del bot (no viene de FMP), simplificado — no "
        "reemplaza el WACC que armaría un analista con datos de mercado "
        "más completos._"
    )
    transparency_lines.append(
        "_Esto es una síntesis de datos financieros históricos, no "
        "asesoramiento financiero profesional ni una recomendación de "
        "inversión. No incluye análisis de noticias ni del contexto "
        "cualitativo del negocio más allá de los eventos corporativos "
        "oficiales de SEC EDGAR listados arriba (si los hay) — y esos se "
        "muestran sin resumir, no reemplazan leer el filing completo. "
        "Revisá vos el resto del contexto cualitativo antes de decidir._"
    )

    parts = [
        titulo,
        veredicto_section,
        intro,
        "\n".join(ratios_lines),
        extras_section,
        valuation_section,
        pillars_section,
        market_context_section,
        corporate_events_section,
        risk_section,
        "\n".join(transparency_lines),
    ]
    return [part for part in parts if part is not None]


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
    peers_note: Optional[str] = None,
    extras: Optional[dict] = None,
    vix: Optional[dict] = None,
    corporate_events: Optional[list[dict]] = None,
) -> str:
    """Wrapper de una línea sobre `build_summary_parts` (Decisión 16.1) —
    puramente aditivo: todo lo que hoy llama `build_summary(...)` sigue
    recibiendo el mismo `str`."""
    return "\n\n".join(
        build_summary_parts(
            ticker=ticker,
            company_name=company_name,
            precio_actual=precio_actual,
            ratios=ratios,
            pillars=pillars,
            scenarios=scenarios,
            n_peers_validos=n_peers_validos,
            momentum=momentum,
            peer_comparison=peer_comparison,
            risk_fit=risk_fit,
            treasury_source=treasury_source,
            peers_note=peers_note,
            extras=extras,
            vix=vix,
            corporate_events=corporate_events,
        )
    )
