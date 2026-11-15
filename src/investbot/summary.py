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

import re
import unicodedata
from typing import Optional

from investbot import peers, rules
from investbot.valuation import (
    DETALLES_VALIDOS,
    MIN_PEERS_VALIDOS_PARA_MULTIPLOS,
    classify_scenario,
)

# Caracteres especiales del modo "Markdown" legado de Telegram (no el más
# nuevo "MarkdownV2", que tiene una lista mucho más larga) — documentados por
# la propia API de Telegram como los únicos 4 que hay que escapar con "\"
# para que un texto se muestre literal sin que el parser lo interprete como
# sintaxis de formato: https://core.telegram.org/bots/api#markdown-style
_MARKDOWN_LEGACY_ESPECIALES = ("_", "*", "`", "[")


def _escape_markdown_legacy(texto: str) -> str:
    """Escapa los 4 caracteres especiales del Markdown legado de Telegram en
    texto de terceros (FMP/Finnhub) antes de interpolarlo dentro de una
    sección que ya usa `parse_mode="Markdown"` (bug real en producción,
    2026-09-02: un `companyName` de FMP con un `*`/`_`/`` ` ``/`[` sin parear
    rompía el parseo completo del mensaje con `BadRequest: Can't parse
    entities`, y el usuario no recibía absolutamente nada).

    Aplica solo a texto que NO es parte de la sintaxis de formato que el bot
    arma a propósito (headers en negrita, cursivas de nota, etc.) — o sea,
    nunca se le aplica a los f-strings que ya traen `*`/`_` intencionales,
    solo al valor de terceros que se interpola adentro de ellos."""
    if not texto:
        return texto
    for char in _MARKDOWN_LEGACY_ESPECIALES:
        texto = texto.replace(char, f"\\{char}")
    return texto

# --- Procedencia de los datos (SDD_fuentes_datos_gratuitas.md, CS-11, CS-13, CA-23) ---
#
# Todo texto de procedencia es FIJO: se elige por el enum cerrado `source`
# ("fmp" | "sec_edgar") y por los `fallback_codes` (enum cerrado, 15.3 fila 20),
# nunca se interpola contenido de red. El único valor variable es el ticker, que
# ya pasó la regex estricta de la cadena (aquí se revalida y, si no cuadra, se
# muestra "?"). Con `source="fmp"` (el default) todo el texto es el de siempre.
SOURCE_FMP = "fmp"
SOURCE_EDGAR = "sec_edgar"

_TICKER_SEGURO_RE = re.compile(r"[A-Za-z0-9.\-]{1,10}", re.ASCII)

# CS-11: tope de caracteres de un nombre de tercero (entityName/name de la SEC o Finnhub).
MAX_NOMBRE_TERCERO = 100

_SEPARADORES_A_ESPACIO = frozenset({"Cc", "Zl", "Zp"})
_CONTROL_A_ELIMINAR = frozenset({"Cf", "Cs", "Co", "Cn"})


def sanitize_third_party_name(nombre: object, limite: int = MAX_NOMBRE_TERCERO) -> str:
    """CS-11 / T-CS-11: nombre de empresa de terceros (FMP, `entityName` de la
    SEC, `name` de Finnhub) listo para interpolar en un mensaje con
    `parse_mode="Markdown"`: (1) un valor que no es `str` es vacío; (2) los
    caracteres de control (saltos de línea, tabulaciones, NUL, marcas de
    dirección, etc.) se reemplazan por un espacio o se eliminan, para que el
    nombre no pueda partir ni falsear líneas del mensaje; (3) se acota a
    `limite` caracteres ANTES de escapar (así el escape nunca queda cortado a
    mitad de `\\*`); (4) se escapa el Markdown legado. Un nombre normal (sin
    control y de hasta 100 caracteres) sale idéntico a `_escape_markdown_legacy`."""
    if not isinstance(nombre, str):
        return ""
    limpio = []
    for char in nombre:
        categoria = unicodedata.category(char)
        if categoria in _SEPARADORES_A_ESPACIO:
            limpio.append(" ")
        elif categoria not in _CONTROL_A_ELIMINAR:
            limpio.append(char)
    return _escape_markdown_legacy("".join(limpio)[:limite])


def _nombre_para_titulo(company_name: object, ticker: str) -> str:
    """Nombre sanitizado para el titulo; si queda vacio o solo espacios, el ticker."""
    nombre = sanitize_third_party_name(company_name)
    return nombre if nombre.strip() else ticker


def _ticker_visible(ticker: object) -> str:
    return ticker if isinstance(ticker, str) and _TICKER_SEGURO_RE.fullmatch(ticker) else "?"


# Etiqueta de la fuente de los estados financieros según el enum `source`.
_FUENTE_ESTADOS_LABEL = {SOURCE_FMP: "FMP", SOURCE_EDGAR: "la SEC EDGAR"}

# Códigos de respaldo cuyo motivo es "la SEC no cubre esta empresa" (4.6); el
# resto (fallos transitorios, bloqueo, datos incoherentes...) usa la plantilla
# de "no pude consultar ahora".
_CODIGOS_SIN_COBERTURA = frozenset(
    {"sin_cik", "sin_facts", "no_us_gaap", "sin_usd", "datos_insuficientes", "desactualizado"}
)

_NOTA_RESPALDO_SIN_COBERTURA = (
    "No encontré reportes de {ticker} en la SEC (empresa fuera de EEUU, sin 10-K "
    "reciente o tipo de empresa no soportado); usé FMP como respaldo."
)
_NOTA_RESPALDO_TEMPORAL = (
    "No pude consultar los reportes de {ticker} en la SEC en esta consulta; "
    "usé FMP como respaldo."
)
_NOTA_CACHE_ANTIGUO = (
    "La SEC no respondió ahora; los reportes de {ticker} vienen de una copia "
    "guardada hace hasta 7 días."
)
# 15.5 (texto fijo, solo con fuente SEC y dato `None`; línea protegida para Gemini: CS-25).
AVISO_DEUDA_AUSENTE = (
    "No pude determinar la deuda financiera de {ticker} con los reportes de la "
    "SEC; el costo de capital, la relación deuda/patrimonio y la Magic Formula "
    "quedan como no disponibles (no asumo deuda cero)."
)
# Con fuente FMP y deuda `None` el DCF desaparece: se declara (texto fijo, línea protegida).
AVISO_DEUDA_AUSENTE_FMP = (
    "No pude determinar la deuda financiera de {ticker} con los datos de FMP; el "
    "costo de capital queda como no disponible (no asumo deuda cero)."
)
AVISO_INTERESES_AUSENTES = (
    "{ticker} no reporta gasto por intereses en la SEC; el costo de la deuda y "
    "el DCF quedan no disponibles."
)
# P-3 / R-8: las definiciones de EDGAR difieren de las de FMP (3.5).
NOTA_DEFINICIONES_EDGAR = (
    "Con datos de la SEC el EBIT es la utilidad operativa y la deuda total no "
    "incluye arrendamientos operativos; por eso Deuda Neta/EBITDA puede diferir "
    "levemente de FMP."
)
NOTA_BETA_POR_DEFECTO = (
    "FMP no informó la beta de esta empresa: usé beta 1.0 (igual al mercado) "
    "como supuesto neutro, no es un dato de la empresa."
)

# Una línea de procedencia por bloque (CS-13). Solo cuando los estados salen de
# la SEC; con FMP cada bloque ya dice "FMP" en su propio texto de siempre.
_FUENTE_BLOQUE_EDGAR = {
    "valoracion": (
        "_Fuente de este bloque: estados financieros de la SEC EDGAR; precio y "
        "comparables de FMP o Finnhub; tasa Y según las notas de transparencia._"
    ),
    "pilares": "_Fuente de este bloque: estados financieros de la SEC EDGAR; precio de FMP._",
    "mercado": (
        "_Fuente de este bloque: cotización y VIX de FMP; comparables según las "
        "notas de transparencia._"
    ),
}


# --- Procedencia de los datos de mercado (F2b, SDD_fuentes_datos_gratuitas.md
# 3.8/4.1/13.3 CS-27): "el mensaje declara la fuente de cada bloque". Todo es
# texto FIJO elegido por el enum cerrado de fuentes de `market_data`
# (finnhub | fmp | fred | None) y de sus códigos; nunca contenido de red. Solo se
# usa cuando el llamador pasa `market_provenance` (capa de mercado activa); sin
# él, todos los textos de arriba son los de siempre.
_FUENTE_MERCADO_LABEL = {"finnhub": "Finnhub", "fmp": "FMP", "fred": "CBOE vía FRED"}
_ND_LABEL = "no disponible"
_CODIGOS_FINNHUB_BLOQUEADO = frozenset({"finnhub_rechazado", "circuito_abierto"})
NOTA_BETA_POR_DEFECTO_MERCADO = (
    "No hay beta disponible para esta empresa en las fuentes consultadas "
    "(Finnhub y FMP): usé beta 1.0 (igual al mercado) como supuesto neutro, no "
    "es un dato de la empresa."
)
AVISO_FINNHUB_RESPALDO = (
    "Finnhub no estuvo disponible para parte de los datos de mercado en esta "
    "consulta; usé FMP como respaldo."
)
AVISO_FINNHUB_BLOQUEADO = (
    "Finnhub rechazó la consulta o quedó en pausa por un límite de uso; usé FMP "
    "como respaldo mientras tanto."
)
AVISO_MERCADO_ND = (
    "Algunos datos de mercado no estaban disponibles en Finnhub ni en FMP en "
    "esta consulta y se muestran como no disponibles."
)


def _fuentes_mercado(market_provenance: Optional[dict]) -> Optional[dict[str, str]]:
    """Etiqueta fija por bloque (`quote`/`profile`/`metrics`/`vix`) o `None` si
    no hay capa de mercado. Una fuente fuera del enum cuenta como no disponible."""
    if not market_provenance:
        return None
    fuentes = market_provenance.get("sources") or {}
    return {
        bloque: _FUENTE_MERCADO_LABEL.get(fuentes.get(bloque), _ND_LABEL)
        for bloque in ("quote", "profile", "metrics", "vix")
    }


def _lineas_mercado(market_provenance: dict) -> list[str]:
    """Línea de fuentes por bloque + avisos de respaldo / N/D (texto fijo)."""
    etiquetas = _fuentes_mercado(market_provenance)
    lineas = [
        f"_Datos de mercado — cotización: {etiquetas['quote']}; perfil: "
        f"{etiquetas['profile']}; beta y rango de 52 semanas: {etiquetas['metrics']}; "
        f"VIX: {etiquetas['vix']}._"
    ]
    fuentes = market_provenance.get("sources") or {}
    codigos = set(market_provenance.get("codes") or ())
    bloques = ("quote", "profile", "metrics")
    if any(fuentes.get(b) == "fmp" for b in bloques):
        aviso = AVISO_FINNHUB_BLOQUEADO if codigos & _CODIGOS_FINNHUB_BLOQUEADO else AVISO_FINNHUB_RESPALDO
        lineas.append(f"_{aviso}_")
    if any(fuentes.get(b) is None for b in bloques):
        lineas.append(f"_{AVISO_MERCADO_ND}_")
    return lineas


def build_market_source_line(market_provenance: Optional[dict]) -> Optional[str]:
    """Línea de texto plano con la fuente de cada bloque de mercado (`/avanzado`,
    que no usa Markdown). `None` sin capa de mercado."""
    etiquetas = _fuentes_mercado(market_provenance)
    if etiquetas is None:
        return None
    return (
        f"Fuente de datos de mercado: cotización {etiquetas['quote']} · perfil "
        f"{etiquetas['profile']} · beta y rango de 52 semanas {etiquetas['metrics']}"
    )


def _linea_bloque(bloque: str, *, edgar: bool, market_provenance: Optional[dict]) -> Optional[str]:
    """Línea "Fuente de este bloque" (CS-13): la de siempre con fuente SEC y sin
    capa de mercado; con capa de mercado, una línea que cita la fuente real de
    la cotización y del VIX (con SEC o FMP en los estados)."""
    etiquetas = _fuentes_mercado(market_provenance)
    if etiquetas is None:
        return _FUENTE_BLOQUE_EDGAR[bloque] if edgar else None
    estados = "la SEC EDGAR" if edgar else "FMP"
    if bloque == "valoracion":
        return (
            f"_Fuente de este bloque: estados financieros de {estados}; precio de "
            f"{etiquetas['quote']}; comparables y tasa Y según las notas de transparencia._"
        )
    if bloque == "pilares":
        return f"_Fuente de este bloque: estados financieros de {estados}; precio de {etiquetas['quote']}._"
    return (
        f"_Fuente de este bloque: cotización de {etiquetas['quote']}; VIX: "
        f"{etiquetas['vix']}; comparables según las notas de transparencia._"
    )


def _con_linea(seccion: Optional[str], linea: Optional[str]) -> Optional[str]:
    """`seccion` + una línea final (sin tocar la sección si no hay línea o no existe)."""
    if seccion is None or not linea:
        return seccion
    return f"{seccion}\n{linea}"


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
    # Las 5 entradas de abajo (SDD_eps_ttm_real.md, Decisión #17) usan wording
    # period-agnóstico: el historial que alimenta pilares/CAGR puede ser
    # anual o trimestral según qué fuente respondió esta consulta — el texto
    # ya no asume literalmente "años".
    "eps_base_no_positivo": "al inicio del historial disponible la empresa tenía pérdidas, así que no se puede calcular un crecimiento histórico confiable",
    "eps_reciente_no_positivo": "en el período más reciente disponible la empresa tuvo pérdidas",
    "fcf_base_no_positivo": "al inicio del historial disponible el flujo de caja libre era negativo",
    "fcf_reciente_no_positivo": "el flujo de caja libre del período más reciente disponible es negativo",
    "historial_insuficiente": "no hay suficiente historial financiero (se necesitan al menos ~2 años de historia, sea en reportes anuales o trimestrales)",
    "y_no_disponible": "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov)",
    "wacc_no_calculable": "no se pudo estimar el costo de capital (WACC) con los datos disponibles",
    "dcf_no_calculable": "no se pudo proyectar el flujo de caja con los datos disponibles",
    "per_peers_no_disponible": "no pude obtener el PER de los comparables del sector",
    "graham_multiplicador_no_positivo": "en este escenario el crecimiento estimado haría el múltiplo de Graham cero o negativo",
    # SDD_calidad_peers_multiplos.md, Decisión #4 — fallback estático (el
    # mensaje real que se muestra es el dinámico armado en el loop de
    # excluidos_base, con el detalle cuantitativo "mínimo X, hubo N"; esta
    # entrada solo cubre el caso defensivo de que ese caso especial no
    # aplique por algún motivo, mismo patrón que el resto del diccionario).
    "peers_validos_insuficientes": (
        "no hay suficientes comparables (peers) con PER válido esta consulta "
        "para un promedio confiable"
    ),
}

# SDD_motivos_nd_y_empresas_en_perdidas.md (15.5). `MOTIVO_LABELS` queda intacto
# (CA-ND-5): los textos nuevos viven en estos dicts. Fragmentos en minuscula,
# sin punto final, sin cifras ni `*`, `_`, backtick, corchetes (el compositor
# agrega el "." al armar la linea).
MOTIVO_LABELS_EXTRA = {
    "eps_cagr_no_calculable": "no se pudo calcular el crecimiento histórico de la ganancia con los datos disponibles",
    "fcf_cagr_no_calculable": "no se pudo calcular el crecimiento histórico del flujo de caja libre con los datos disponibles",
}

DETALLE_LABELS = {
    "eps_no_disponible": "no pude obtener la ganancia por acción (EPS) de la empresa",
    "wacc_capitalizacion_no_disponible": "no pude obtener el valor de mercado de la empresa, necesario para estimar el costo de capital (WACC)",
    "wacc_deuda_no_disponible": "no pude obtener la deuda total de la empresa, necesaria para estimar el costo de capital (WACC)",
    "wacc_deuda_invalida": "el dato de deuda total de la empresa es inválido (negativo), así que no se puede estimar el costo de capital (WACC)",
    "wacc_intereses_no_disponibles": "la empresa tiene deuda pero no pude obtener cuánto paga de intereses, necesario para estimar el costo de capital (WACC)",
    "wacc_costo_deuda_fuera_de_rango": "el costo de la deuda que sale de los datos está fuera de lo razonable (puede ser un dato erróneo o una deuda muy cara), así que no se usa para estimar el costo de capital (WACC)",
    "wacc_tasa_bono_no_disponible": "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov), necesaria para estimar el costo de capital (WACC)",
}

MOTIVO_GENERICO = "no pude identificar el motivo exacto con los datos de esta consulta"

MODELO_LABELS_RESUMEN = {
    "multiplos": "Múltiplos",
    "graham": "Graham",
    "dcf": "DCF",
}


def motivo_texto(motivo, detalle=None) -> Optional[str]:
    """Texto del motivo de exclusion de un modelo: el del `detalle` (si es una
    clave valida de `DETALLES_VALIDOS`), si no el de `MOTIVO_LABELS` /
    `MOTIVO_LABELS_EXTRA`; `None` si no se conoce. Nunca lanza ni devuelve el
    valor crudo: `isinstance` antes de usar como clave (un `detalle` tipo lista
    daria `TypeError: unhashable`)."""
    if isinstance(detalle, str) and detalle in DETALLES_VALIDOS and detalle in DETALLE_LABELS:
        return DETALLE_LABELS[detalle]
    if isinstance(motivo, str):
        return MOTIVO_LABELS.get(motivo) or MOTIVO_LABELS_EXTRA.get(motivo)
    return None


def build_motivos_resumen(excluidos) -> Optional[str]:
    """Resumen de una linea de por que no se calcularon los modelos:
    `Múltiplos y Graham: <t1>; DCF: <t2>` (sin punto final). Orden fijo
    multiplos -> graham -> dcf; agrupa por texto resuelto; un modelo repetido
    usa su primera entrada; salta entradas no-dict y modelos fuera de
    `MODELO_LABELS_RESUMEN`. `None` si no hay nada (nunca la cadena "None")."""
    if not isinstance(excluidos, list):
        return None
    por_modelo: dict[str, Optional[str]] = {}
    for item in excluidos:
        if not isinstance(item, dict):
            continue
        modelo = item.get("modelo")
        if not isinstance(modelo, str) or modelo not in MODELO_LABELS_RESUMEN or modelo in por_modelo:
            continue
        por_modelo[modelo] = motivo_texto(item.get("motivo"), item.get("detalle"))
    grupos: dict[str, list[str]] = {}
    for modelo, nombre in MODELO_LABELS_RESUMEN.items():
        texto = por_modelo.get(modelo)
        if texto is not None:
            grupos.setdefault(texto, []).append(nombre)
    if not grupos:
        return None
    return "; ".join(
        f"{', '.join(nombres[:-1]) + ' y ' + nombres[-1] if len(nombres) > 1 else nombres[0]}: {texto}"
        for texto, nombres in grupos.items()
    )


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
    peers_pe: dict[str, float],
    peers_no_usados: dict[str, str],
    *,
    ey_calculado_por_el_bot: bool = False,
) -> Optional[str]:
    """Detalle de PER individual por peer + motivo específico por peer que no
    aportó dato + procedencia explícita del cálculo. `None` si no hay
    absolutamente ningún dato de peers que mostrar (sector sin peers
    configurados en PEERS_BY_SECTOR)."""
    if not peers_pe and not peers_no_usados:
        return None

    # Los nombres/tickers de peers pueden venir de Finnhub (peers dinámicos,
    # fuente_peers == PEERS_FUENTE_FINNHUB, ver peers.py) — texto de tercero
    # sin sanitizar, mismo riesgo que company_name: se escapa antes de
    # interpolarlo en esta sección con parse_mode="Markdown".
    clausulas: list[str] = []
    if peers_pe:
        listado = ", ".join(
            f"{_escape_markdown_legacy(nombre)} {_fmt_ratio(valor)}"
            for nombre, valor in peers_pe.items()
        )
        clausulas.append(f"PER de tus comparables: {listado}")

    for motivo, nombres in _agrupar_peers_por_motivo(peers_no_usados).items():
        forma = "singular" if len(nombres) == 1 else "plural"
        texto_motivo = _MOTIVO_PEER_LABELS.get(motivo, {}).get(
            forma, "no tiene un PER válido esta consulta"
        )
        nombres_escapados = [_escape_markdown_legacy(n) for n in nombres]
        clausulas.append(f"{_join_con_y(nombres_escapados)} {texto_motivo}")

    texto = " — ".join(clausulas) + "."
    # F2b (CA-29): con la capa de mercado, el earningsYield de un peer puede ser
    # un cálculo del bot (utilidad TTM de la SEC / capitalización de Finnhub).
    origen_ey = (
        "earningsYield de cada peer es un dato de FMP o un cálculo del bot "
        "(utilidad TTM de la SEC / capitalización de Finnhub), el PER no"
        if ey_calculado_por_el_bot
        else "earningsYield sí es un dato de FMP, el PER no"
    )
    return (
        f"  _{texto} (PER individual calculado por el bot como "
        f"1 / earningsYield — {origen_ey})._"
    )


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_ratio(value: float) -> str:
    return f"{value:.1f}"


def _cell(escenario: dict, campo: str) -> str:
    value = escenario.get(campo)
    return _fmt_money(value) if value is not None else "N/D"


_ESCENARIOS_ORDEN = ("pesimista", "conservador", "optimista")
_ESCENARIO_TITULOS = {"pesimista": "Pesimista", "conservador": "Conservador", "optimista": "Optimista"}


def _escenario_header(nombre_clave: str, escenario_elegido: Optional[str]) -> str:
    """Título de columna, resaltado si `nombre_clave == escenario_elegido`
    (`SDD_eps_ttm_real.md`, Decisión #24, ronda 2 — botones inline). Puramente
    de presentación: no cambia ningún valor calculado, solo qué columna se
    ve resaltada en el encabezado del rango de Valor Justo."""
    titulo = _ESCENARIO_TITULOS[nombre_clave]
    if escenario_elegido == nombre_clave:
        return f"*{titulo}* ✅"
    return titulo


def build_valuation_scenarios_section(
    scenarios: dict,
    precio_actual: float,
    n_peers_validos: int,
    escenario_elegido: Optional[str] = None,
) -> str:
    """Sección de "Valor Justo" con rango Pesimista | Conservador | Optimista
    por modelo + total, y clasificación barata/cara por escenario (Spec Patch
    Iter-3, secciones 1-3 + Iter-4 C1).

    `scenarios` es la representación en dict de `valuation.ValuationScenarios`
    (`.as_dict()`): claves `pesimista`/`conservador`/`optimista` (cada una un
    dict con `valor_justo_multiplos/graham/dcf/total` + `modelos_excluidos`)
    y `modelos_excluidos_base` (nivel 1, reportado una sola vez).

    `escenario_elegido` (`SDD_eps_ttm_real.md`, Decisión #24 — ronda 2,
    botones inline): opcional, default `None` (comportamiento idéntico al de
    antes de la ronda 2 — se muestran los 3 escenarios sin resaltar ninguno).
    Si viene un valor del set `{"pesimista", "conservador", "optimista"}`,
    solo resalta visualmente esa columna en el encabezado — nunca oculta los
    otros 2, nunca cambia ningún valor numérico (los 3 escenarios siempre se
    calculan los 3, sin importar cuál se resalta).
    """
    pesimista = scenarios["pesimista"]
    conservador = scenarios["conservador"]
    optimista = scenarios["optimista"]
    excluidos_base = scenarios.get("modelos_excluidos_base") or []
    if not isinstance(excluidos_base, list):
        excluidos_base = []  # dato corrupto: se trata como "sin excluidos"
    excluidos_base_modelos = {
        item["modelo"]
        for item in excluidos_base
        if isinstance(item, dict) and isinstance(item.get("modelo"), str)
    }

    encabezado = " | ".join(
        _escenario_header(k, escenario_elegido) for k in _ESCENARIOS_ORDEN
    )
    lines = [f"*Rango de Valor Justo estimado ({encabezado}):*"]
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

    graham_g_cap_avisos: list[str] = []
    for escenario_nombre, escenario in (
        ("Pesimista", pesimista),
        ("Conservador", conservador),
        ("Optimista", optimista),
    ):
        if escenario.get("graham_g_capped"):
            g_original = escenario.get("graham_g_original")
            g_aplicado = escenario.get("graham_g_aplicado")
            graham_g_cap_avisos.append(
                f"- ℹ️ Graham ({escenario_nombre}): el crecimiento histórico (g) "
                f"usado se limitó de {g_original * 100:.1f}% a "
                f"{g_aplicado * 100:.0f}% porque la fórmula de Graham no puede "
                "modelar razonablemente crecimientos tan altos de forma "
                "sostenida — evita un Valor Justo por Graham artificialmente "
                "inflado."
            )
    lines.extend(graham_g_cap_avisos)

    for item in excluidos_base:
        if not isinstance(item, dict):
            continue
        modelo = item.get("modelo")
        modelo_label = (
            MODELO_LABELS.get(modelo, "uno de los modelos")
            if isinstance(modelo, str)
            else "uno de los modelos"
        )
        if item.get("motivo") == "peers_validos_insuficientes":
            # SDD_calidad_peers_multiplos.md, Decisión #4 — mensaje dinámico
            # que preserva el detalle cuantitativo que antes se mostraba como
            # nota al pie de la fila con valores (ahora la fila ni se
            # muestra, ver eliminación del bloque de arriba).
            motivo_label = (
                "no hay suficientes comparables con PER válido esta consulta "
                f"(mínimo {MIN_PEERS_VALIDOS_PARA_MULTIPLOS}, hubo {n_peers_validos})"
            )
        else:
            # SDD_motivos_nd (S-3): un motivo/detalle desconocido o corrupto
            # nunca se interpola crudo (rompe el Markdown legacy).
            motivo_label = (
                motivo_texto(item.get("motivo"), item.get("detalle")) or MOTIVO_GENERICO
            )
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
    vix_fuente: Optional[str] = None,
    peers_ey_calculado: bool = False,
) -> str:
    """Sección "Contexto de mercado" (Spec Patch Iter-3, sección 6): momentum
    de precio + comparación explícita con peers. Se ubica entre "Pilares de
    buena empresa" y "Encaje con tu perfil de riesgo".

    Parte 3 (`SDD_fix_crecimiento_y_redaccion.md`, Hallazgo 4): los 3
    sub-temas (momentum/precio, comparación con peers + desglose individual,
    VIX) se agrupan en sub-bloques separados por línea en blanco (`"\\n\\n"`)
    entre sí, preservando `"\\n"` simple dentro de cada sub-bloque — mismo
    tratamiento que ya usa `build_valuation_scenarios_section` para separar
    Rango de Valor Justo de Valor Justo Total.
    """
    bloque_momentum = ["*Contexto de mercado:*"]

    pct_high = momentum.get("pct_vs_year_high")
    pct_low = momentum.get("pct_vs_year_low")
    if pct_high is not None and pct_low is not None:
        bloque_momentum.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_high):.1f}% por "
            f"debajo de su máximo de 52 semanas y un {abs(pct_low):.1f}% por "
            "encima de su mínimo de 52 semanas."
        )
    elif pct_high is not None:
        bloque_momentum.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_high):.1f}% por "
            "debajo de su máximo de 52 semanas."
        )
    elif pct_low is not None:
        bloque_momentum.append(
            f"- Cotiza a {_fmt_money(precio_actual)}, un {abs(pct_low):.1f}% por "
            "encima de su mínimo de 52 semanas."
        )

    etiqueta = momentum.get("etiqueta")
    if etiqueta in ETIQUETA_MOMENTUM_LABELS:
        bloque_momentum.append(f"- {ETIQUETA_MOMENTUM_LABELS[etiqueta]}")
    # etiqueta == "no_disponible" -> se omite (criterio explícito Iter-3/6.3),
    # no se muestra como ruido "impulso: no disponible".

    bloque_peers: list[str] = []
    posicion = peer_comparison.get("posicion")
    peers_pe = peer_comparison.get("peers_pe") or {}
    peers_no_usados = peer_comparison.get("peers_no_usados") or {}

    if posicion == "no_comparable":
        motivo = peer_comparison.get("motivo_no_comparable")
        texto = MOTIVO_NO_COMPARABLE_LABELS.get(motivo, "no se pudo comparar con tus peers.")
        bloque_peers.append(f"- Comparada con sus comparables del sector: {texto}")
    else:
        # peers_usados puede venir de Finnhub (peers dinámicos) sin
        # sanitizar — mismo escape que el resto de nombres de peers en este
        # módulo, ver _escape_markdown_legacy.
        peers_str = ", ".join(
            _escape_markdown_legacy(p) for p in (peer_comparison.get("peers_usados") or [])
        )
        posicion_txt = POSICION_LABELS.get(posicion, posicion)
        per_propio = peer_comparison.get("per_propio")
        per_min = peer_comparison.get("per_minimo_peers")
        per_prom = peer_comparison.get("per_promedio_peers")
        per_max = peer_comparison.get("per_maximo_peers")
        bloque_peers.append(
            f"- Comparada con sus comparables del sector ({peers_str}): tu PER "
            f"({_fmt_ratio(per_propio)}) está {posicion_txt} con el rango de tus "
            f"peers (mínimo {_fmt_ratio(per_min)}, promedio {_fmt_ratio(per_prom)}, "
            f"máximo {_fmt_ratio(per_max)})."
        )

    peer_breakdown_line = _build_peer_pe_breakdown_line(
        peers_pe, peers_no_usados, ey_calculado_por_el_bot=peers_ey_calculado
    )
    if peer_breakdown_line:
        bloque_peers.append(peer_breakdown_line)

    bloque_vix: list[str] = []
    if vix and vix.get("disponible"):
        bloque_vix.append(f"- VIX (CBOE Volatility Index): {vix['valor']:.2f}")
        bloque_vix.append(
            "  _Estimado/aproximado — mide la volatilidad implícita "
            "esperada de opciones sobre el S&P 500 (el mercado en "
            "general), no del ticker que consultaste. NO es lo mismo "
            "que un índice compuesto de sentimiento tipo \"Fear & "
            "Greed\" (ese combina varias señales distintas; el VIX "
            "solo mide volatilidad). "
            + (
                "Dato: CBOE vía FRED (serie VIXCLS)._"
                if vix_fuente == "fred"
                else "Dato de FMP (símbolo ^VIX)._"
            )
        )

    nota_final = [
        "\n_Nota: el momentum de arriba es un proxy simple de precio "
        "del ticker consultado, no del mercado en general. El VIX (si "
        "aparece más arriba) es una aproximación de la volatilidad "
        "esperada del mercado en general, no del ticker — tampoco es "
        "un índice de sentimiento compuesto._"
    ]

    bloques = [b for b in (bloque_momentum, bloque_peers, bloque_vix, nota_final) if b]
    return "\n\n".join("\n".join(b) for b in bloques)


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
        if ev["filing_url"]:
            lines.append(f"- {ev['filing_date']}: {etiquetas} — [ver el filing]({ev['filing_url']})")
        else:  # CS-29: sin link si el accession/documento no tenía formato SEC
            lines.append(f"- {ev['filing_date']}: {etiquetas}")
    lines.append(
        "  _Fuente: SEC EDGAR (oficial, gratis, sin API key) — formularios "
        "8-K que la empresa está obligada a presentar por ley ante eventos "
        "materiales. El bot NO resume el contenido legal del filing (fuera "
        "de alcance, riesgo de alucinación sobre texto legal) — mostramos "
        "fecha + tipo de evento (la etiqueta es una traducción del bot del "
        "código oficial de Item de la SEC, no una cita textual) + link para "
        "que lo leas vos si te interesa._"
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


def build_risk_fit_section(
    risk_fit: dict,
    *,
    beta_por_defecto: bool = False,
    market_provenance: Optional[dict] = None,
) -> str:
    """`beta_por_defecto` (CA-24, P-8): `True` cuando la beta de la empresa no
    vino y el análisis usó 1.0; el mensaje lo declara con una línea fija.
    `market_provenance` (F2b): con la capa de mercado, la beta cita su fuente
    real (Finnhub o FMP) y el aviso de beta por defecto nombra a ambas."""
    etiquetas = _fuentes_mercado(market_provenance)
    fuente_beta = "FMP" if etiquetas is None or etiquetas["metrics"] == _ND_LABEL else etiquetas["metrics"]
    nota_beta = NOTA_BETA_POR_DEFECTO if etiquetas is None else NOTA_BETA_POR_DEFECTO_MERCADO
    encaje_txt = "SÍ encaja" if risk_fit["encaja"] else "NO encaja"
    seccion = (
        f"*Encaje con tu perfil de riesgo ({risk_fit['perfil']}):*\n"
        f"{encaje_txt} — "
        f"es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}.\n"
        "_Renta variable = sos dueño de una parte de la empresa (una "
        "acción). A diferencia de la renta fija (bonos, plazo fijo), acá "
        "no hay un pago garantizado: ganás o perdés según cómo le va al "
        "negocio._\n"
        "_Beta mide qué tan volátil es esta acción comparada con el "
        "mercado en general: 1.0 = se mueve igual que el mercado; más de "
        "1.0 = se mueve más fuerte (para arriba y para abajo); menos de "
        f"1.0 = se mueve menos. Dato de {fuente_beta}._"
    )
    return _con_linea(seccion, f"_{nota_beta}_" if beta_por_defecto else None)


_EXTRAS_CAMPOS = ["roe", "debt_to_equity", "net_debt_to_ebitda", "dividend_yield", "payout_ratio"]


def build_extras_section(extras: Optional[dict], *, source: str = SOURCE_FMP) -> Optional[str]:
    """Sección "Rentabilidad, deuda de largo plazo y dividendos" (Decisión
    #3). Se omite por completo (retorna `None`) si los 5 campos vienen
    `None` — mismo criterio de "degradar con gracia sin ruido" que el resto
    del proyecto. Si al menos un campo está disponible, se muestran solo
    las líneas de los campos presentes, sin placeholder "N/D" para los
    ausentes.

    `source` (CA-23): con `"sec_edgar"` cada línea dice que es un cálculo del
    bot con datos de la SEC en vez de "dato de FMP"; el default `"fmp"` conserva
    el texto de siempre.
    """
    if not extras or all(extras.get(campo) is None for campo in _EXTRAS_CAMPOS):
        return None
    edgar = source == SOURCE_EDGAR
    # Fragmentos de procedencia por línea (mismo lugar, mismo formato; solo
    # cambia la etiqueta de la fuente).
    origen_roe = (
        "cálculo del bot con datos de la SEC EDGAR"
        if edgar
        else "dato ya calculado por FMP, el bot no lo recalcula"
    )
    origen = "cálculo del bot con datos de la SEC EDGAR" if edgar else "dato de FMP"

    lines = ["*Rentabilidad, deuda de largo plazo y dividendos:*"]

    roe = extras.get("roe")
    if roe is not None:
        lines.append(
            f"- ROE (Rentabilidad sobre el Patrimonio): {roe * 100:.1f}% "
            "_(fórmula: Ganancia Neta / Patrimonio de los Accionistas — "
            f"{origen_roe})_"
        )
        lines.append(
            f"  _Por cada $100 que pusieron los dueños, la empresa ganó "
            f"${roe * 100:.0f} este año._"
        )

    debt_to_equity = extras.get("debt_to_equity")
    if debt_to_equity is not None:
        lines.append(
            f"- Deuda/Patrimonio (Debt-to-Equity): {debt_to_equity:.2f} "
            f"_(fórmula: Deuda Total / Patrimonio de los Accionistas — {origen})_"
        )
        lines.append(
            "  _Cuánta deuda tiene la empresa comparada con lo que es de los dueños._"
        )

    net_debt_to_ebitda = extras.get("net_debt_to_ebitda")
    if net_debt_to_ebitda is not None:
        lines.append(
            f"- Deuda Neta / EBITDA: {net_debt_to_ebitda:.2f}x _({origen})_"
        )
        lines.append(
            "  _Cuántos años de ganancia operativa le tomaría pagar toda su deuda._"
        )

    dividend_yield = extras.get("dividend_yield")
    if dividend_yield is not None:
        lines.append(f"- Dividend Yield: {dividend_yield * 100:.2f}% _({origen})_")
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
        lines.append(f"- Payout Ratio: {payout_ratio * 100:.1f}% _({origen})_")
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

    cuidado_txt = ""
    if debiles:
        etiquetas = {
            "ingresos_crecientes": "ingresos",
            "utilidades_crecientes": "utilidades",
            "deuda_controlada": "deuda",
            "precio_razonable": "precio",
        }
        cuidado_txt = f" Mirá con cuidado: {', '.join(etiquetas[k] for k in debiles)}."

    lineas = [
        "*Veredicto:*",
        f"{precio_txt.capitalize()}, con {solidos}/4 pilares sólidos.",
        f"Encaje de riesgo: {'SÍ' if risk_fit.get('encaja') else 'NO'} (detalle más abajo).",
    ]
    if cuidado_txt:
        lineas.append(cuidado_txt.strip())
    return "\n".join(lineas)


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
    "En esta consulta, la lista de comparables (peers) vino de "
    "Finnhub (agrupados por sub-industria, no por el sector completo) "
    "— no es la lista fija de peers.py. Si Finnhub no responde o no "
    "está configurado, el bot usa automáticamente un respaldo fijo "
    "elegido a mano por quien construyó el bot."
)

# Disclaimers de transparencia — hoisteados a constantes públicas (Spec Patch
# [Iter-3] de `SDD_redaccion_ia_ollama.md`, Opción C elegida por `architect`):
# `ai_rewrite.py` las importa directamente para protegerlas del guard de
# condensación (nunca se envían a Gemini como texto libre). Mismo patrón que
# `_PEERS_NOTE_FIJO`/`_PEERS_NOTE_FINNHUB` arriba, pero sin `_` inicial porque
# ahora son consumidas fuera de este módulo (`ai_rewrite.py`). Texto byte-idéntico al que antes
# vivía inline en `build_summary_parts` — cero cambio de comportamiento.
DISCLAIMER_WACC_DCF = (
    "_El DCF es una aproximación con supuestos simplificados de WACC "
    "(Costo Promedio Ponderado de Capital): combina cuánto le cuesta a "
    "la empresa financiarse con capital propio (accionistas) y con "
    "deuda (bancos/bonistas), ponderado por cuánto usa de cada uno. Es "
    "un cálculo propio del bot (no viene de FMP), simplificado — "
    "es una aproximación más simple, no un sustituto completo del "
    "WACC que armaría un analista con datos de mercado más "
    "completos._"
)
DISCLAIMER_NO_ASESORAMIENTO = (
    "_Esto es una síntesis de datos financieros históricos, no "
    "asesoramiento financiero profesional ni una recomendación de "
    "inversión. No incluye análisis de noticias ni del contexto "
    "cualitativo del negocio más allá de los eventos corporativos "
    "oficiales de SEC EDGAR listados arriba (si los hay) — y esos se "
    "muestran sin resumir, no reemplazan leer el filing completo. "
    "Revisá vos el resto del contexto cualitativo antes de decidir._"
)
# Invariante del que depende toda la protección de `ai_rewrite._classify_lines`
# (comparación por igualdad exacta de línea completa, `security` Iter-3
# sección 2): cada disclaimer debe ser una sola línea sin `\n` interno. Se
# verifica acá, a nivel de import, para que una futura edición que rompa esto
# falle ruidosamente al arrancar el bot, no en silencio.
assert "\n" not in DISCLAIMER_WACC_DCF
assert "\n" not in DISCLAIMER_NO_ASESORAMIENTO


def _build_balance_sheet_note(balance_sheet_fuente: Optional[str]) -> Optional[str]:
    """Texto de transparencia sobre si el balance general (la foto) mostrado
    esta consulta es del trimestre más reciente o del año fiscal más
    reciente (fallback) — `SDD_eps_ttm_real.md`, Pregunta abierta H.

    `None` (sin fuente informada, comportamiento de llamadores viejos o
    consultas donde el dato no aplicó) -> no se agrega ninguna línea, mismo
    criterio de "degradar con gracia sin ruido" que el resto de notas
    opcionales de este módulo."""
    if balance_sheet_fuente == rules.DATOS_FUENTE_TRIMESTRAL:
        return (
            "El balance general (la foto) usado en este análisis es del "
            "trimestre más reciente disponible."
        )
    if balance_sheet_fuente == rules.DATOS_FUENTE_ANUAL_FALLBACK:
        return (
            "El balance general (la foto) usado en este análisis es del "
            "año fiscal más reciente disponible (no se pudo obtener uno "
            "trimestral más reciente en esta consulta)."
        )
    return None


def _build_income_statement_note(income_statement_fuente: Optional[str]) -> Optional[str]:
    """Texto de transparencia sobre si el boletín (Estado de Resultados) usado
    esta consulta es un TTM real (últimos 4 trimestres reales sumados) o el
    último año fiscal reportado (fallback) — Spec Patch [Iter-3] de
    `SDD_eps_ttm_real.md`, mismo patrón que `_build_balance_sheet_note`.

    `None` (fuente no informada, llamadores viejos, o el dato no aplicó esta
    consulta) -> no agrega ninguna línea, mismo criterio de "degradar con
    gracia sin ruido" que el resto de notas opcionales de este módulo."""
    if income_statement_fuente == rules.DATOS_FUENTE_TRIMESTRAL:
        return (
            "El boletín (Estado de Resultados) usado en este análisis — EPS, "
            "PER, margen bruto, P/S y el WACC (costo de capital) — es TTM "
            "real: la suma de los últimos 4 trimestres reales reportados."
        )
    if income_statement_fuente == rules.DATOS_FUENTE_ANUAL_FALLBACK:
        return (
            "El boletín (Estado de Resultados) usado en este análisis — EPS, "
            "PER, margen bruto, P/S y el WACC (costo de capital) — es del "
            "último año fiscal reportado, no un TTM real de los últimos 12 "
            "meses (no se pudo obtener un TTM real de 4 trimestres en esta "
            "consulta)."
        )
    return None


def _build_cash_flow_note(cash_flow_fuente: Optional[str]) -> Optional[str]:
    """Texto de transparencia sobre si el extracto (Flujo de Efectivo) usado
    esta consulta es un TTM real (últimos 4 trimestres reales sumados) o el
    último año fiscal reportado (fallback) — Spec Patch [Iter-3] de
    `SDD_eps_ttm_real.md`, mismo patrón que `_build_balance_sheet_note`."""
    if cash_flow_fuente == rules.DATOS_FUENTE_TRIMESTRAL:
        return (
            "El extracto (Flujo de Efectivo) usado en este análisis — el FCF "
            "(Flujo de Caja Libre) y la proyección de Valor Justo por DCF — "
            "es TTM real: la suma de los últimos 4 trimestres reales "
            "reportados."
        )
    if cash_flow_fuente == rules.DATOS_FUENTE_ANUAL_FALLBACK:
        return (
            "El extracto (Flujo de Efectivo) usado en este análisis — el FCF "
            "(Flujo de Caja Libre) y la proyección de Valor Justo por DCF — "
            "es del último año fiscal reportado, no un TTM real de los "
            "últimos 12 meses (no se pudo obtener un TTM real de 4 "
            "trimestres en esta consulta)."
        )
    return None


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


def build_intro_section() -> str:
    """Intro "Tienda de Limonada" (Decisión de diseño #9 de
    `SDD_menu_por_capas_explicaciones.md`: hoisteada a función propia para
    que, con Gemini habilitado, pase a formar parte de la primera línea del
    leaf `inf` — texto byte-idéntico al que antes vivía inline acá, cero
    cambio de comportamiento en `build_summary_parts`)."""
    return (
        "*Cómo leer este análisis:*\n"
        "Pensá en una empresa como una Tienda de Limonada: el *boletín* "
        "(Estado de Resultados) te dice cuánto vendió y ganó, *la foto* "
        "(Balance General) te dice qué tiene y qué debe en un momento dado, "
        "y *el extracto* (Flujo de Efectivo) te dice cuánta plata de verdad "
        "entró y salió de la caja."
    )


def build_transparency_section(
    *,
    peers_note: str,
    treasury_source: Optional[str],
    income_statement_fuente: Optional[str],
    balance_sheet_fuente: Optional[str],
    cash_flow_fuente: Optional[str],
    source: str = SOURCE_FMP,
    fallback_codes: tuple = (),
    ticker: Optional[str] = None,
    deuda_ausente: bool = False,
    intereses_ausentes: bool = False,
    deuda_ausente_fmp: bool = False,
    market_provenance: Optional[dict] = None,
) -> str:
    """Sección "Notas de transparencia" (Decisión de diseño #9 de
    `SDD_menu_por_capas_explicaciones.md`: hoisteada a función propia para
    que el leaf determinístico `inf` de `ai_explain.py` la reutilice sin
    recalcular nada — texto byte-idéntico al que antes se armaba inline en
    `build_summary_parts`, cero cambio de comportamiento ahí).

    `peers_note` ya viene resuelto (el llamador decide si usa el que le
    pasaron o calcula el default con `_build_peers_note`) — esta función no
    conoce `peer_comparison`, solo el string final.

    Procedencia (CA-23, CS-13, 4.6, 15.5): `source`/`fallback_codes` son los
    enums cerrados de `FundamentalsResult`; con los defaults (`"fmp"`, sin
    códigos, sin avisos) la sección es byte-idéntica a la de siempre. Los avisos
    de deuda/intereses ausentes (`deuda_ausente`/`intereses_ausentes`, derivados
    por el llamador de un `None`) solo se muestran con fuente SEC: con FMP un
    dato ausente conserva su tratamiento de siempre. Todos los textos son fijos
    (plantillas con el ticker ya validado) y cada línea contiene tokens que
    `ai_rewrite._classify_lines` protege (CS-25): Gemini nunca las reescribe."""
    edgar = source == SOURCE_EDGAR
    ticker_txt = _ticker_visible(ticker)
    if market_provenance:
        # F2b: los estados y los datos de mercado citan cada uno su fuente real.
        primera = (
            "_Estados financieros (ingresos, deuda, flujo de caja) obtenidos de la "
            + ("SEC EDGAR._" if edgar else "Financial Modeling Prep (FMP)._")
        )
    elif edgar:
        primera = (
            "_Estados financieros (ingresos, deuda, flujo de caja) obtenidos de la "
            "SEC EDGAR; cotización, perfil y VIX de Financial Modeling Prep (FMP)._"
        )
    else:
        primera = (
            "_Datos financieros (ingresos, deuda, flujo de caja, cotización, etc.) "
            "obtenidos de Financial Modeling Prep (FMP)._"
        )
    transparency_lines = [
        "*Notas de transparencia:*",
        primera,
    ]
    if market_provenance:
        transparency_lines.extend(_lineas_mercado(market_provenance))
    if fallback_codes:
        if not edgar:
            plantilla = (
                _NOTA_RESPALDO_SIN_COBERTURA
                if fallback_codes[0] in _CODIGOS_SIN_COBERTURA
                else _NOTA_RESPALDO_TEMPORAL
            )
            transparency_lines.append(f"_{plantilla.format(ticker=ticker_txt)}_")
        elif "cache_antiguo" in fallback_codes:
            transparency_lines.append(f"_{_NOTA_CACHE_ANTIGUO.format(ticker=ticker_txt)}_")
    if edgar:
        transparency_lines.append(f"_{NOTA_DEFINICIONES_EDGAR}_")
        if deuda_ausente:
            transparency_lines.append(f"_{AVISO_DEUDA_AUSENTE.format(ticker=ticker_txt)}_")
        elif intereses_ausentes:
            transparency_lines.append(f"_{AVISO_INTERESES_AUSENTES.format(ticker=ticker_txt)}_")
    elif deuda_ausente_fmp:
        transparency_lines.append(f"_{AVISO_DEUDA_AUSENTE_FMP.format(ticker=ticker_txt)}_")
    transparency_lines.append(f"_Nota de transparencia: {peers_note}_")
    if treasury_source:
        transparency_lines.append(
            f"_Y (tasa libre de riesgo) obtenida de: {treasury_source}._"
        )
    income_statement_note = _build_income_statement_note(income_statement_fuente)
    if income_statement_note:
        transparency_lines.append(f"_{income_statement_note}_")
    balance_sheet_note = _build_balance_sheet_note(balance_sheet_fuente)
    if balance_sheet_note:
        transparency_lines.append(f"_{balance_sheet_note}_")
    cash_flow_note = _build_cash_flow_note(cash_flow_fuente)
    if cash_flow_note:
        transparency_lines.append(f"_{cash_flow_note}_")
    transparency_lines.append(DISCLAIMER_WACC_DCF)
    transparency_lines.append(DISCLAIMER_NO_ASESORAMIENTO)
    return "\n\n".join(transparency_lines)


def build_valor_justo_teaser_line(
    scenarios: dict, escenario_elegido: Optional[str], precio_actual: float
) -> str:
    """1 línea: Valor Justo Total del escenario elegido + precio actual —
    Decisión de diseño #9 (D1 resuelta por Daniela: sí lleva teaser) de
    `SDD_menu_por_capas_explicaciones.md`. Extrae solo el total ya
    calculado, nunca recalcula nada. Caso límite (escenario no calculable,
    `valor_justo_total is None`): no rompe ni muestra "None" — degrada a un
    texto explícito."""
    escenario = (scenarios.get(escenario_elegido) if escenario_elegido else None) or {}
    total = escenario.get("valor_justo_total")
    titulo = _ESCENARIO_TITULOS.get(escenario_elegido, escenario_elegido or "elegido")
    if total is None:
        linea = (
            f"Valor Justo Total ({titulo}): no calculable con los datos "
            f"disponibles esta consulta — tu precio actual: {_fmt_money(precio_actual)}."
        )
        # SDD_motivos_nd (P-5, Q-4, 14.8): el "por qué" se agrega en la MISMA
        # linea, solo si hay motivos conocidos en
        # `scenarios[escenario_elegido]['modelos_excluidos']`. Todo lo nuevo va
        # protegido: ausente / no-lista / corrupto => linea identica a HEAD.
        try:
            resumen = build_motivos_resumen(escenario.get("modelos_excluidos"))
            if resumen:
                linea += f" Por qué: {resumen}."
        except Exception:  # noqa: BLE001 -- defensivo: nunca romper el primer mensaje
            pass
        return linea
    return (
        f"Valor Justo Total ({titulo}): {_fmt_money(total)} — tu precio "
        f"actual: {_fmt_money(precio_actual)}."
    )


def build_summary_parts_short(
    *,
    ticker: str,
    company_name: str,
    precio_actual: float,
    pillars: dict,
    risk_fit: dict,
    scenarios: dict,
    escenario_elegido: Optional[str] = None,
) -> list[str]:
    """Versión acortada del primer mensaje (Decisión de diseño #9 de
    `SDD_menu_por_capas_explicaciones.md`) — SOLO se usa cuando Gemini está
    habilitado (regla de no-regresión D3: deshabilitado -> siempre
    `build_summary_parts`, el reporte completo de siempre). Título +
    Veredicto (ya corto, sin cambios) + teaser de Valor Justo + invitación a
    usar los botones. Todo lo demás (Ratios/Extras/tabla completa de Valor
    Justo/Pilares/Contexto de mercado/Eventos corporativos/Encaje de
    riesgo/Notas de transparencia) queda detrás de los botones de
    `ai_explain.py` — ninguna sección se recalcula acá, ninguna se pierde,
    solo se muestra bajo demanda."""
    # company_name viene sin sanitizar de FMP (profile.get("companyName")) —
    # escapado obligatorio, ver _escape_markdown_legacy.
    titulo = f"*{_nombre_para_titulo(company_name, ticker)} ({ticker})*"
    veredicto_section = build_veredicto_section(pillars=pillars, risk_fit=risk_fit)
    teaser_line = build_valor_justo_teaser_line(scenarios, escenario_elegido, precio_actual)
    invite = "👇 Elegí qué querés que te explique."
    return [titulo, veredicto_section, teaser_line, invite]


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
    escenario_elegido: Optional[str] = None,
    balance_sheet_fuente: Optional[str] = None,
    income_statement_fuente: Optional[str] = None,
    cash_flow_fuente: Optional[str] = None,
    source: str = SOURCE_FMP,
    fallback_codes: tuple = (),
    deuda_ausente: bool = False,
    intereses_ausentes: bool = False,
    deuda_ausente_fmp: bool = False,
    beta_por_defecto: bool = False,
    market_provenance: Optional[dict] = None,
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

    Procedencia (SDD_fuentes_datos_gratuitas.md, CA-23, CS-13): `source`,
    `fallback_codes`, `deuda_ausente`, `intereses_ausentes` y
    `beta_por_defecto` son keyword-only con defaults que reproducen el
    comportamiento anterior (QM-7: `source="fmp"`, sin avisos).
    `market_provenance` (F2b, `{"sources": {bloque: fuente}, "codes": (...)}`)
    solo lo pasa `query_handler` con la capa de mercado activa; `None` = textos
    de siempre.
    """
    # company_name es dato de terceros (FMP, o `entityName` de la SEC) —
    # sanitizado y acotado a 100 caracteres (CS-11), ver sanitize_third_party_name.
    titulo = f"*{_nombre_para_titulo(company_name, ticker)} ({ticker})*"
    veredicto_section = build_veredicto_section(pillars=pillars, risk_fit=risk_fit)

    intro = build_intro_section()

    ratios_lines = [
        "*Ratios clave:*",
        f"_(calculados por el bot a partir de datos de {_FUENTE_ESTADOS_LABEL[source]} — "
        "no son campos directos de la API)_",
    ]
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

    extras_section = build_extras_section(extras, source=source)

    edgar = source == SOURCE_EDGAR
    valuation_section = _con_linea(
        build_valuation_scenarios_section(
            scenarios, precio_actual, n_peers_validos, escenario_elegido=escenario_elegido
        ),
        _linea_bloque("valoracion", edgar=edgar, market_provenance=market_provenance),
    )
    pillars_section = _con_linea(
        build_pillars_section(pillars),
        _linea_bloque("pilares", edgar=edgar, market_provenance=market_provenance),
    )
    market_context_section = _con_linea(
        build_market_context_section(
            precio_actual=precio_actual,
            momentum=momentum,
            peer_comparison=peer_comparison,
            vix=vix,
            vix_fuente=(market_provenance or {}).get("sources", {}).get("vix"),
            peers_ey_calculado=bool((market_provenance or {}).get("peers_ey_bot")),
        ),
        _linea_bloque("mercado", edgar=edgar, market_provenance=market_provenance),
    )
    corporate_events_section = build_corporate_events_section(corporate_events)
    risk_section = build_risk_fit_section(
        risk_fit, beta_por_defecto=beta_por_defecto, market_provenance=market_provenance
    )

    peers_note_final = peers_note if peers_note is not None else _build_peers_note(
        peer_comparison.get("fuente_peers")
    )
    transparency_section = build_transparency_section(
        peers_note=peers_note_final,
        treasury_source=treasury_source,
        income_statement_fuente=income_statement_fuente,
        balance_sheet_fuente=balance_sheet_fuente,
        cash_flow_fuente=cash_flow_fuente,
        source=source,
        fallback_codes=fallback_codes,
        ticker=ticker,
        deuda_ausente=deuda_ausente,
        intereses_ausentes=intereses_ausentes,
        deuda_ausente_fmp=deuda_ausente_fmp,
        market_provenance=market_provenance,
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
        transparency_section,
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
    escenario_elegido: Optional[str] = None,
    balance_sheet_fuente: Optional[str] = None,
    income_statement_fuente: Optional[str] = None,
    cash_flow_fuente: Optional[str] = None,
    source: str = SOURCE_FMP,
    fallback_codes: tuple = (),
    deuda_ausente: bool = False,
    intereses_ausentes: bool = False,
    deuda_ausente_fmp: bool = False,
    beta_por_defecto: bool = False,
    market_provenance: Optional[dict] = None,
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
            escenario_elegido=escenario_elegido,
            balance_sheet_fuente=balance_sheet_fuente,
            income_statement_fuente=income_statement_fuente,
            cash_flow_fuente=cash_flow_fuente,
            source=source,
            fallback_codes=fallback_codes,
            deuda_ausente=deuda_ausente,
            intereses_ausentes=intereses_ausentes,
            deuda_ausente_fmp=deuda_ausente_fmp,
            beta_por_defecto=beta_por_defecto,
            market_provenance=market_provenance,
        )
    )
