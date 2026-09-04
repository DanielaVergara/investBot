"""Tablas de contenido del menú por capas de explicaciones —
`SDD_menu_por_capas_explicaciones.md`.

Separado de `ai_explain.py` (Decisión de diseño, sección "Presupuesto/
impacto" — a discreción de `implementer`) para que el mecanismo (store,
keyboard, handler, guard) no quede enterrado entre 27 preguntas fijas y las
2 tablas de 13/14 entradas de fórmula/fuente. Nada acá hace I/O ni conoce
Telegram/Ollama — son estructuras de datos puras, importables directamente
en tests (criterio de Testabilidad de `qa`).

Ningún string de este módulo interpola texto libre de terceros (FMP) — todo
es texto fijo del código (Decisión de diseño #5, #6, #7 de la spec).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Decisión de diseño #1 (SDD_explicacion_paso_a_paso.md) -- 3 variantes,
# reemplazan el booleano `requires_ollama` de la spec anterior:
# - "dato_y_paso_a_paso": 2 botones -- "Ver dato" (determinístico, nunca
#   llama a Ollama) y "Explicame paso a paso" (llama a Ollama con la cuenta
#   ya resuelta como dato garantizado).
# - "narrativa": 1 botón, llama a Ollama, sin cuenta (no hay fórmula propia
#   del bot detrás -- mod/ben/ren).
# - "deterministico": 1 botón, nunca llama a Ollama (evt/inf).
VARIANT_DATO_Y_PASO_A_PASO = "dato_y_paso_a_paso"
VARIANT_NARRATIVA = "narrativa"
VARIANT_DETERMINISTICO = "deterministico"


@dataclass(frozen=True)
class QuestionSpec:
    label: str  # texto del botón (Nivel 2, o Nivel 1 si es leaf suelto)
    variant: str  # "dato_y_paso_a_paso" | "narrativa" | "deterministico"
    pregunta_narrativa: Optional[str] = None  # solo si variant == "narrativa"
    pregunta_paso_a_paso: Optional[str] = None  # solo si variant == "dato_y_paso_a_paso"


@dataclass(frozen=True)
class CategorySpec:
    label: str  # texto del botón de Nivel 1
    question_codes: tuple[str, ...]  # orden de presentación en Nivel 2


# --- Texto libre --------------------------------------------------------

QUESTIONS_TEXTO_LIBRE: dict[str, QuestionSpec] = {
    "ver": QuestionSpec(
        "⚖️ Veredicto",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones por qué el veredicto (barata/cara/sin "
            "datos) salió así para este ticker, usando SOLO los datos del JSON."
        ),
    ),
    "vf": QuestionSpec(
        "💰 Valor Justo Total",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué significa el rango de Valor Justo "
            "estimado para esta empresa y cómo se compara con el precio actual, "
            "para el escenario elegido, usando SOLO los datos del JSON."
        ),
    ),
    "gra": QuestionSpec(
        "Graham (EPS)",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué significa el resultado del modelo "
            "Graham (EPS) para este ticker en los 3 escenarios, usando SOLO los "
            "datos del JSON."
        ),
    ),
    "dcf": QuestionSpec(
        "DCF",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué significa el resultado del modelo "
            "DCF (flujo de caja descontado) para este ticker en los 3 "
            "escenarios, usando SOLO los datos del JSON."
        ),
    ),
    "mul": QuestionSpec(
        "Múltiplos",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué significa el resultado del modelo "
            "de Múltiplos para este ticker en los 3 escenarios, usando SOLO los "
            "datos del JSON."
        ),
    ),
    "rat": QuestionSpec(
        "Ratios clave",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué dicen los ratios clave (liquidez, "
            "margen bruto, PER, P/S) de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "pil": QuestionSpec(
        "🏛 Los 4 pilares",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué son los 4 pilares de \"buena "
            "empresa\" evaluados y cuáles cumplió o no cumplió este ticker en "
            "particular, usando SOLO los datos del JSON."
        ),
    ),
    "ren": QuestionSpec(
        "ROE y rentabilidad",
        VARIANT_NARRATIVA,
        pregunta_narrativa=(
            "Explicá en 2 a 4 oraciones qué dicen el ROE, la deuda y los "
            "dividendos de este ticker sobre su rentabilidad, usando SOLO los "
            "datos del JSON."
        ),
    ),
    "rsk": QuestionSpec(
        "Encaje con tu perfil",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones si este ticker encaja con el perfil de "
            "riesgo guardado y por qué, usando SOLO los datos del JSON."
        ),
    ),
    "mom": QuestionSpec(
        "Momentum y volatilidad",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué dice el momentum de precio (y el "
            "VIX si está disponible) sobre este ticker, usando SOLO los datos "
            "del JSON."
        ),
    ),
    "cmp": QuestionSpec(
        "Comparables del sector",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones cómo se compara el PER de este ticker "
            "con el de sus comparables del sector, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "evt": QuestionSpec("Eventos corporativos", VARIANT_DETERMINISTICO),
    "inf": QuestionSpec("ℹ️ Fuentes y transparencia", VARIANT_DETERMINISTICO),
}

CATEGORIES_TEXTO_LIBRE: dict[str, CategorySpec] = {
    "val": CategorySpec("💰 Valoración", ("vf", "gra", "dcf", "mul", "rat")),
    "cal": CategorySpec("🏛 Calidad del negocio", ("pil", "ren")),
    "rie": CategorySpec("📊 Riesgo y mercado", ("rsk", "mom", "cmp", "evt")),
}

# Nivel 1 de texto libre: leaves sueltos + categorías, en el orden en que se
# muestran los botones (Decisión de diseño #2 — Veredicto primero, Fuentes y
# transparencia al final, por ser meta-información sobre todo el análisis).
LEVEL1_TEXTO_LIBRE: tuple[tuple[str, str], ...] = (
    ("leaf", "ver"),
    ("cat", "val"),
    ("cat", "cal"),
    ("cat", "rie"),
    ("leaf", "inf"),
)

FORMULAS_TEXTO_LIBRE: dict[str, str] = {
    "ver": (
        "Precio actual < Valor Justo Total (escenario elegido) → Barata · "
        "Precio actual > Valor Justo Total → Cara · sin Valor Justo Total "
        "calculable → sin veredicto"
    ),
    "vf": "Promedio simple de los modelos calculables entre Múltiplos, Graham y DCF, para el escenario elegido",
    "gra": "EPS (TTM) × (8.5 + 2×g) × 4.4 / Y, con g = CAGR histórico de EPS",
    "dcf": "proyección de Flujo de Caja Libre a 5 años + valor terminal, descontados al WACC",
    "mul": "EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector",
    "rat": (
        "Liquidez = Activos Circulantes / Pasivos Circulantes · Margen "
        "bruto = (Ventas − Costo de Ventas) / Ventas · PER = Precio / EPS "
        "· P/S = Capitalización de Mercado / Ventas"
    ),
    "pil": (
        "Ingresos crecientes = ingresos del período más reciente > el más "
        "antiguo del historial · Utilidades crecientes = ídem + utilidad "
        "neta reciente > 0 · Deuda controlada = liquidez > 1 (o sin "
        "pasivos de corto plazo) · Precio razonable = clasificación "
        "barata/cara del escenario Conservador"
    ),
    "ren": (
        "ROE = Ganancia Neta / Patrimonio de los Accionistas · "
        "Deuda/Patrimonio = Deuda Total / Patrimonio · Deuda Neta/EBITDA "
        "· Dividend Yield · Payout Ratio"
    ),
    "rsk": (
        "Encaje = comparación de la beta del ticker contra el perfil de "
        "riesgo guardado (regla propia del bot, risk_fit.py) — sin una "
        "fórmula matemática única"
    ),
    "mom": (
        "% vs. máximo/mínimo de 52 semanas y vs. promedios móviles de "
        "50/200 días — comparaciones porcentuales directas, sin fórmula "
        "compuesta"
    ),
    "cmp": "PER propio = Precio / EPS (TTM) — comparado contra el PER de cada peer (1 / earningsYield)",
}

FUENTES_TEXTO_LIBRE: dict[str, str] = {
    "ver": (
        "Precio actual: cotización (quote) de FMP; Valor Justo Total: "
        "promedio de Múltiplos/Graham/DCF calculables (ver fuente de "
        "Valor Justo Total)."
    ),
    "vf": (
        "Valor Justo Total = promedio simple de los modelos calculables "
        "entre Múltiplos, Graham y DCF (valuation.py, "
        "compute_valuation_scenarios)."
    ),
    "gra": (
        "EPS (TTM) viene del estado de resultados (o del cálculo TTM "
        "propio del bot sobre 4 trimestres); g es el CAGR calculado por "
        "el bot sobre el historial de EPS (con techo de 15%); Y es la "
        "tasa del bono del Tesoro a 10 años, de FRED o Treasury.gov."
    ),
    "dcf": (
        "FCF histórico = Flujo de Caja Operativo − Gasto de Capital "
        "(CapEx), del estado de flujo de efectivo; WACC es un cálculo "
        "propio del bot con beta (dato de FMP), deuda del balance, y "
        "tasa impositiva efectiva del estado de resultados."
    ),
    "mul": (
        "EPS TTM del estado de resultados propio; el PER de cada peer es "
        "1 / earningsYield (earningsYield sí es un dato de FMP, el PER "
        "individual es un cálculo del bot)."
    ),
    "rat": (
        "Activos/Pasivos Circulantes del balance general; Ventas y Costo "
        "de Ventas del estado de resultados; Precio y Capitalización de "
        "Mercado de la cotización (quote)."
    ),
    "pil": (
        "Ingresos y Utilidad Neta del estado de resultados; "
        "Activos/Pasivos Circulantes del balance; clasificación de "
        "precio ya calculada en Valoración."
    ),
    "ren": (
        "Los 5 campos vienen ya precalculados de /key-metrics de FMP — "
        "el bot no los recalcula, los muestra tal cual."
    ),
    "rsk": "Beta es un dato de FMP (profile.beta); el perfil de riesgo es el que el usuario fijó con /start.",
    "mom": (
        "yearHigh/yearLow/priceAvg50/priceAvg200 son datos de la "
        "cotización (quote) de FMP; VIX es un dato de FMP (símbolo "
        "^VIX), volatilidad del S&P 500 en general, no del ticker."
    ),
    "cmp": (
        "PER propio: cálculo del bot con datos propios; PER de cada "
        "peer: cálculo del bot con earningsYield de /key-metrics de cada "
        "peer."
    ),
    "evt": "SEC EDGAR (oficial, gratis) — formularios 8-K que la empresa está obligada a presentar por ley.",
}

# --- /avanzado -----------------------------------------------------------

QUESTIONS_AVANZADO: dict[str, QuestionSpec] = {
    "mod": QuestionSpec(
        "❓ ¿Qué modelos aplican?",
        VARIANT_NARRATIVA,
        pregunta_narrativa=(
            "Explicá en 2 a 4 oraciones qué modelos de los 5 (Altman Z-Score, "
            "Piotroski F-Score, Beneish M-Score, Magic Formula, Factores AQR) "
            "fueron calculables para este ticker en particular y por qué, "
            "usando SOLO los datos del JSON."
        ),
    ),
    "alz": QuestionSpec(
        "Z (fórmula original)",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el Altman Z-Score y qué "
            "significa el resultado de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "azp": QuestionSpec(
        "Z'' (asset-light)",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide la variante Z'' del Altman "
            "Z-Score (para empresas asset-light) y qué significa el resultado "
            "de este ticker, usando SOLO los datos del JSON."
        ),
    ),
    "pig": QuestionSpec(
        "Puntaje general",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el Piotroski F-Score en "
            "general y qué significa el puntaje total de este ticker, usando "
            "SOLO los datos del JSON."
        ),
    ),
    "pir": QuestionSpec(
        "Rentabilidad",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué evalúan los criterios de "
            "rentabilidad del Piotroski F-Score y cuáles cumplió este ticker, "
            "usando SOLO los datos del JSON."
        ),
    ),
    "pia": QuestionSpec(
        "Apalancamiento y liquidez",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué evalúan los criterios de "
            "apalancamiento y liquidez del Piotroski F-Score y cuáles cumplió "
            "este ticker, usando SOLO los datos del JSON."
        ),
    ),
    "pie": QuestionSpec(
        "Eficiencia",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué evalúan los criterios de "
            "eficiencia del Piotroski F-Score y cuáles cumplió este ticker, "
            "usando SOLO los datos del JSON."
        ),
    ),
    "ben": QuestionSpec(
        "🚫 Beneish M",
        VARIANT_NARRATIVA,
        pregunta_narrativa=(
            "Explicá en 2 a 4 oraciones por qué el Beneish M-Score no es "
            "calculable para este ticker con los datos disponibles, usando "
            "SOLO los datos del JSON."
        ),
    ),
    "mgr": QuestionSpec(
        "ROIC",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el ROIC de la Magic Formula "
            "y qué significa el valor de este ticker, usando SOLO los datos "
            "del JSON."
        ),
    ),
    "mge": QuestionSpec(
        "Earnings Yield",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el Earnings Yield de la "
            "Magic Formula y qué significa el valor de este ticker, usando "
            "SOLO los datos del JSON."
        ),
    ),
    "aqv": QuestionSpec(
        "Value",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el factor Value y qué "
            "significa la etiqueta de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "aqq": QuestionSpec(
        "Quality",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el factor Quality y qué "
            "significa la etiqueta de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "aqm": QuestionSpec(
        "Momentum",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el factor Momentum y qué "
            "significa la etiqueta de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
    "aql": QuestionSpec(
        "Low-vol",
        VARIANT_DATO_Y_PASO_A_PASO,
        pregunta_paso_a_paso=(
            "Explicá en 2 a 4 oraciones qué mide el factor Low-vol y qué "
            "significa la etiqueta de este ticker, usando SOLO los datos del "
            "JSON."
        ),
    ),
}

CATEGORIES_AVANZADO: dict[str, CategorySpec] = {
    "alt": CategorySpec("📐 Altman Z", ("alz", "azp")),
    "pio": CategorySpec("🧮 Piotroski F", ("pig", "pir", "pia", "pie")),
    "mag": CategorySpec("🪄 Magic Formula", ("mgr", "mge")),
    "aqr": CategorySpec("📊 Factores AQR", ("aqv", "aqq", "aqm", "aql")),
}

LEVEL1_AVANZADO: tuple[tuple[str, str], ...] = (
    ("leaf", "mod"),
    ("cat", "alt"),
    ("cat", "pio"),
    ("leaf", "ben"),
    ("cat", "mag"),
    ("cat", "aqr"),
)

FORMULAS_AVANZADO: dict[str, str] = {
    "alz": (
        "Z = 1.2A + 1.4B + 3.3C + 0.6D + 1.0E — A=Capital de Trabajo/"
        "Activos Totales, B=Utilidades Retenidas/Activos Totales, "
        "C=EBIT/Activos Totales, D=Capitalización de Mercado/Pasivos "
        "Totales, E=Ventas/Activos Totales"
    ),
    "azp": (
        "Z'' = 6.56A + 3.26B + 6.72C + 1.05D — mismas A-D que Z, sin el "
        "factor E (ventas/activos), variante para empresas asset-light"
    ),
    "pig": "F-Score = suma de los 9 criterios binarios cumplidos (0 a 9)",
    "pir": (
        "4 criterios: ROA positivo (Ganancia Neta > 0) · CFO positivo "
        "(Flujo de Caja Operativo > 0) · ROA creciente (Ganancia "
        "Neta/Activos Totales sube vs. año anterior) · CFO > Utilidad Neta"
    ),
    "pia": (
        "3 criterios: apalancamiento decreciente (Deuda Largo "
        "Plazo/Activos Totales baja) · liquidez creciente "
        "(Activos/Pasivos Corrientes sube) · sin dilución (acciones en "
        "circulación no aumentan)"
    ),
    "pie": (
        "2 criterios: margen bruto creciente (Utilidad Bruta/Ventas "
        "sube) · rotación de activos creciente (Ventas/Activos Totales "
        "sube)"
    ),
    "mgr": "ROIC = EBIT / (Capital de Trabajo Neto + Activos Fijos Netos)",
    "mge": "Earnings Yield = EBIT / EV, con EV = Capitalización de Mercado + Deuda Total − Efectivo",
    "aqv": "Basado en Earnings Yield (mismo cálculo que mge) contra umbrales fijos: >8% alto, 4-8% medio, <4% bajo",
    "aqq": (
        "Combina ROE (>15% alto, <5% bajo), Margen Bruto (>40% alto, "
        "<20% bajo) y ratio de Piotroski (evaluables cumplidos/"
        "evaluables, >75% alto, <40% bajo) — cada sub-métrica disponible "
        "aporta ±1/0, suma positiva → alto, negativa → bajo"
    ),
    "aqm": "Reutiliza la etiqueta cualitativa de Momentum (mismo cálculo que mom del flujo de texto libre)",
    "aql": "Basado en beta: <0.8 bajo, 0.8-1.2 medio, >1.2 alto",
}

FUENTES_AVANZADO: dict[str, str] = {
    "alz": (
        "Capital de Trabajo = Activos Corrientes − Pasivos Corrientes "
        "(balance); Utilidades Retenidas, Activos/Pasivos Totales "
        "(balance); EBIT y Ventas (estado de resultados); "
        "Capitalización de Mercado (cotización)."
    ),
    "azp": "Mismos campos que alz, sin Ventas.",
    "pig": (
        "Cada criterio sale de balance/estado de resultados/flujo de "
        "efectivo del año actual y el anterior — ver pir/pia/pie para el "
        "detalle campo por campo."
    ),
    "pir": (
        "Ganancia Neta y Activos Totales del año actual y anterior "
        "(estado de resultados + balance); Flujo de Caja Operativo del "
        "estado de flujo de efectivo."
    ),
    "pia": (
        "Deuda Largo Plazo, Activos/Pasivos Totales y Corrientes del "
        "balance (año actual y anterior); acciones en circulación del "
        "estado de resultados."
    ),
    "pie": "Utilidad Bruta, Ventas y Activos Totales del estado de resultados y balance (año actual y anterior).",
    "ben": (
        "FMP en el plan gratuito no separa PP&E bruto ni depreciación "
        "pura de amortización, campos que el modelo original exige."
    ),
    "mgr": (
        "EBIT del estado de resultados; Capital de Trabajo Neto = "
        "Activos Corrientes − Pasivos Corrientes (balance); Activos "
        "Fijos Netos (PP&E neto) del balance."
    ),
    "mge": "EBIT del estado de resultados; Deuda Total y Efectivo del balance; Capitalización de Mercado de la cotización.",
    "aqv": "Mismo origen que mge; umbrales documentados en advanced_scoring.FACTOR_UMBRALES.",
    "aqq": "ROE de /key-metrics de FMP; Margen Bruto del estado de resultados; ratio de Piotroski del cálculo ya hecho en pig.",
    "aqm": "yearHigh/yearLow/priceAvg50/priceAvg200 de la cotización.",
    "aql": "Beta es un dato de FMP (profile.beta).",
}


# --- "🔍 Desglose" (SDD_desglose_terminos_formula.md) --------------------
# Texto fijo, 100% estático -- ningún string interpola `datos` del ticker
# (mismo criterio ya auditado que FORMULAS_AVANZADO/FUENTES_AVANZADO arriba).
# Solo 7 de las 27 preguntas (Decisión de diseño #1: `pig` se apoya en
# pir/pia/pie, no tiene desglose propio).


@dataclass(frozen=True)
class DesgloseTermino:
    letra: str  # símbolo tal cual aparece en la Fórmula/Cuenta
    campo_origen: str  # de dónde sale -- campo(s) de FMP o cálculo del bot
    nombre: str  # nombre legible del concepto
    que_mide: str  # 1 línea, lenguaje simple, sin jerga


_DESGLOSE_ALTMAN_A_D: tuple[DesgloseTermino, ...] = (
    DesgloseTermino(
        letra="A",
        campo_origen="Activos Corrientes − Pasivos Corrientes, sobre Activos Totales (balance)",
        nombre="Capital de Trabajo",
        que_mide=(
            "La plata \"líquida\" que le queda a la empresa para operar el "
            "día a día, en proporción a su tamaño"
        ),
    ),
    DesgloseTermino(
        letra="B",
        campo_origen="Utilidades Retenidas, sobre Activos Totales (balance)",
        nombre="Utilidades Retenidas",
        que_mide="Cuánta ganancia acumulada a lo largo de los años reinvirtió la empresa en sí misma",
    ),
    DesgloseTermino(
        letra="C",
        campo_origen="EBIT, sobre Activos Totales (estado de resultados + balance)",
        nombre="EBIT (Ganancia antes de Intereses e Impuestos)",
        que_mide="Qué tan rentable es el negocio en sí, sin el efecto de la deuda ni de los impuestos",
    ),
    DesgloseTermino(
        letra="D",
        campo_origen="Capitalización de Mercado, sobre Pasivos Totales (cotización + balance)",
        nombre="Capitalización de Mercado sobre Deuda",
        que_mide="Cuánto \"colchón\" de valor en bolsa tiene la empresa frente a lo que debe",
    ),
)

_DESGLOSE_ALTMAN_E = DesgloseTermino(
    letra="E",
    campo_origen="Ventas, sobre Activos Totales (estado de resultados + balance)",
    nombre="Rotación de Activos",
    que_mide="Cuánto factura la empresa por cada dólar que tiene invertido en el negocio",
)

DESGLOSE_AVANZADO: dict[str, tuple[DesgloseTermino, ...]] = {
    "alz": _DESGLOSE_ALTMAN_A_D + (_DESGLOSE_ALTMAN_E,),
    "azp": _DESGLOSE_ALTMAN_A_D,
    "pir": (
        DesgloseTermino(
            letra="ROA positivo",
            campo_origen="Ganancia Neta (estado de resultados)",
            nombre="Ganancia Neta",
            que_mide="Si la empresa ganó plata (no perdió) en el último año",
        ),
        DesgloseTermino(
            letra="CFO positivo",
            campo_origen="Flujo de Caja Operativo (estado de flujo de efectivo)",
            nombre="Flujo de Caja Operativo",
            que_mide=(
                "Si entró más efectivo real del que salió por operar el "
                "negocio, más allá de la ganancia contable"
            ),
        ),
        DesgloseTermino(
            letra="ROA creciente",
            campo_origen=(
                "Ganancia Neta / Activos Totales, año actual vs. anterior "
                "(estado de resultados + balance)"
            ),
            nombre="ROA (Retorno sobre Activos)",
            que_mide="Si la empresa se volvió más eficiente generando ganancia con lo que tiene",
        ),
        DesgloseTermino(
            letra="CFO > Utilidad",
            campo_origen="Flujo de Caja Operativo vs. Ganancia Neta (flujo de efectivo + resultados)",
            nombre="Calidad de la Ganancia",
            que_mide="Si la ganancia reportada está respaldada por efectivo real, no solo \"en papel\"",
        ),
    ),
    "pia": (
        DesgloseTermino(
            letra="Apalancamiento decreciente",
            campo_origen="Deuda de Largo Plazo / Activos Totales, año actual vs. anterior (balance)",
            nombre="Apalancamiento",
            que_mide="Si la empresa se está endeudando menos en relación a su tamaño",
        ),
        DesgloseTermino(
            letra="Liquidez creciente",
            campo_origen="Activos Corrientes / Pasivos Corrientes, año actual vs. anterior (balance)",
            nombre="Liquidez Corriente",
            que_mide="Si mejoró su capacidad de pagar deudas de corto plazo con lo que tiene a mano",
        ),
        DesgloseTermino(
            letra="Sin dilución",
            campo_origen="Acciones en Circulación, año actual vs. anterior (estado de resultados)",
            nombre="Acciones en Circulación",
            que_mide="Si la empresa emitió menos acciones nuevas, sin \"repartir la torta\" entre más dueños",
        ),
    ),
    "pie": (
        DesgloseTermino(
            letra="Margen bruto creciente",
            campo_origen="Utilidad Bruta / Ventas, año actual vs. anterior (estado de resultados)",
            nombre="Margen Bruto",
            que_mide="Si le queda más ganancia por cada venta después del costo directo de producir/vender",
        ),
        DesgloseTermino(
            letra="Rotación de activos creciente",
            campo_origen=(
                "Ventas / Activos Totales, año actual vs. anterior "
                "(estado de resultados + balance)"
            ),
            nombre="Rotación de Activos",
            que_mide="Si la empresa usa mejor sus activos para generar ventas",
        ),
    ),
    "mgr": (
        DesgloseTermino(
            letra="EBIT",
            campo_origen="Estado de resultados",
            nombre="EBIT (Ganancia antes de Intereses e Impuestos)",
            que_mide="La ganancia operativa del negocio, antes de intereses e impuestos",
        ),
        DesgloseTermino(
            letra="Capital de Trabajo Neto",
            campo_origen="Activos Corrientes − Pasivos Corrientes (balance)",
            nombre="Capital de Trabajo Neto",
            que_mide="La plata que la empresa necesita tener disponible para el día a día",
        ),
        DesgloseTermino(
            letra="Activos Fijos Netos",
            campo_origen="Propiedad, Planta y Equipo neto (balance)",
            nombre="Activos Fijos Netos (PP&E)",
            que_mide=(
                "Cuánto tiene invertido en cosas físicas — plantas, "
                "maquinaria, edificios — para operar"
            ),
        ),
    ),
    "mge": (
        DesgloseTermino(
            letra="EBIT",
            campo_origen="Estado de resultados",
            nombre="EBIT",
            que_mide="(mismo que en ROIC de la Magic Formula)",
        ),
        DesgloseTermino(
            letra="Capitalización de Mercado",
            campo_origen="Cotización en bolsa",
            nombre="Capitalización de Mercado",
            que_mide="Cuánto vale la empresa en bolsa hoy",
        ),
        DesgloseTermino(
            letra="Deuda Total",
            campo_origen="Balance",
            nombre="Deuda Total",
            que_mide="Cuánto debe en total la empresa",
        ),
        DesgloseTermino(
            letra="Efectivo",
            campo_origen="Balance",
            nombre="Efectivo y Equivalentes",
            que_mide="Cuánta plata líquida tiene disponible ahora mismo",
        ),
    ),
    # SDD_desglose_universal.md, Grupo E -- Factores AQR con dato ya expuesto.
    "aqv": (
        DesgloseTermino(
            letra="Earnings Yield",
            campo_origen="magic.earnings_yield — mismo cálculo que «Earnings Yield» de la Magic Formula (EBIT/EV)",
            nombre="Earnings Yield",
            que_mide="Cuánta ganancia operativa genera la empresa por cada dólar de su valor total (deuda incluida)",
        ),
        DesgloseTermino(
            letra="Umbrales",
            campo_origen='advanced_scoring.FACTOR_UMBRALES["value_earnings_yield"]',
            nombre="Umbrales de clasificación",
            que_mide="Los cortes fijos que definen si el Earnings Yield es alto, medio o bajo",
        ),
    ),
    "aqq": (
        DesgloseTermino(
            letra="ROE",
            campo_origen="roe — /key-metrics de FMP",
            nombre="ROE (Retorno sobre el Patrimonio)",
            que_mide="Cuánta ganancia genera la empresa por cada dólar que pusieron sus dueños",
        ),
        DesgloseTermino(
            letra="Margen bruto",
            campo_origen="gross_margin — estado de resultados",
            nombre="Margen bruto",
            que_mide="Cuánto le queda de cada venta después del costo directo de producir/vender",
        ),
        DesgloseTermino(
            letra="Ratio de Piotroski",
            campo_origen="piotroski_ratio — criterios cumplidos / evaluables del F-Score",
            nombre="Ratio de Piotroski",
            que_mide="Qué proporción de los criterios de calidad del F-Score cumplió la empresa",
        ),
    ),
    # SDD_desglose_universal.md, Grupo F -- único caso que expone un dato
    # nuevo (reutilizando campos ya declarados en ExplanationContext).
    "aqm": (
        DesgloseTermino(
            letra="vs. promedio 50 días",
            campo_origen="precio_actual vs. price_avg_50 — cotización (quote) de FMP",
            nombre="Precio vs. promedio de 50 días",
            que_mide="Cómo está el precio de hoy respecto a su tendencia de corto plazo",
        ),
        DesgloseTermino(
            letra="vs. promedio 200 días",
            campo_origen="precio_actual vs. price_avg_200 — cotización (quote) de FMP",
            nombre="Precio vs. promedio de 200 días",
            que_mide="Cómo está el precio de hoy respecto a su tendencia de largo plazo",
        ),
    ),
    "aql": (
        DesgloseTermino(
            letra="Beta",
            campo_origen="beta — dato de FMP (profile.beta)",
            nombre="Beta",
            que_mide="Qué tan volátil es la acción comparada con el mercado en general (1.0 = igual de volátil)",
        ),
    ),
}


DESGLOSE_TEXTO_LIBRE: dict[str, tuple[DesgloseTermino, ...]] = {
    "vf": (
        DesgloseTermino(
            letra="Múltiplos",
            campo_origen="fórmula y fuente completas: botón «Múltiplos»",
            nombre="Múltiplos",
            que_mide=(
                "cuánto debería valer la acción si cotizara al mismo múltiplo de "
                "ganancias (PER) que empresas parecidas del mismo sector"
            ),
        ),
        DesgloseTermino(
            letra="Graham",
            campo_origen="fórmula y fuente completas: botón «Graham»",
            nombre="Graham (EPS)",
            que_mide=(
                "cuánto debería valer la acción según una fórmula clásica que combina "
                "ganancias por acción y crecimiento histórico"
            ),
        ),
        DesgloseTermino(
            letra="DCF",
            campo_origen="fórmula y fuente completas: botón «DCF»",
            nombre="DCF (Flujo de Caja Descontado)",
            que_mide=(
                "cuánto vale la empresa hoy si se suma todo el efectivo que se espera "
                "que genere en el futuro, traído a valor de hoy"
            ),
        ),
    ),
    # SDD_desglose_universal.md, Grupo A -- Valoración con cuenta ya resuelta.
    "gra": (
        DesgloseTermino(
            letra="EPS",
            campo_origen="eps_ttm — estado de resultados (TTM, calculado por el bot)",
            nombre="EPS (Ganancia por Acción)",
            que_mide="Cuánto ganó la empresa por cada acción en los últimos 12 meses",
        ),
        DesgloseTermino(
            letra="g",
            campo_origen="g_aplicado del escenario elegido — CAGR histórico de EPS, con techo de 15% (valuation.py)",
            nombre="Crecimiento aplicado",
            que_mide="Qué tan rápido se espera que crezcan las ganancias por acción, según el historial",
        ),
        DesgloseTermino(
            letra="Y",
            campo_origen="y_value — tasa del bono del Tesoro a 10 años (FRED/Treasury.gov)",
            nombre="Tasa del bono a 10 años",
            que_mide='El retorno "sin riesgo" contra el que se compara la inversión en la acción',
        ),
    ),
    # Textos deliberadamente compactos (no el mismo detalle que la spec en
    # prosa): "dcf" es la única con 6 términos -- sin acortar campo_origen/
    # que_mide, el bloque supera `_MAX_DESGLOSE_CHARS=1200` con montos de
    # magnitud realista (medido, ver test de presupuesto de longitud).
    "dcf": (
        DesgloseTermino(
            letra="FCF base",
            campo_origen="dcf_fcf_base — Operativo − CapEx",
            nombre="Flujo de Caja Libre base",
            que_mide="Efectivo libre que le queda a la empresa tras operar e invertir",
        ),
        DesgloseTermino(
            letra="WACC",
            campo_origen="dcf_wacc — beta, deuda e impuestos",
            nombre="Costo de capital",
            que_mide="Tasa a la que se descuentan los flujos futuros",
        ),
        DesgloseTermino(
            letra="g",
            campo_origen="dcf_g_fcf — proyectado a 5 años",
            nombre="Crecimiento del FCF",
            que_mide="Qué tan rápido se espera que crezca el flujo de caja",
        ),
        DesgloseTermino(
            letra="Valor presente de los flujos",
            campo_origen="dcf_valor_presente_flujos — 5 años descontados al WACC",
            nombre="VP de los flujos",
            que_mide="Cuánto valen hoy los flujos de los próximos 5 años",
        ),
        DesgloseTermino(
            letra="Valor terminal descontado",
            campo_origen="dcf_valor_terminal_descontado — año 5+ descontado",
            nombre="Valor terminal",
            que_mide="Cuánto vale hoy lo que la empresa genere después del año 5",
        ),
        DesgloseTermino(
            letra="Valor de la empresa",
            campo_origen="dcf_equity_value — suma de los dos anteriores",
            nombre="Empresa (equity)",
            que_mide="Valor total estimado de la empresa hoy",
        ),
    ),
    "mul": (
        DesgloseTermino(
            letra="EPS",
            campo_origen="eps_ttm — estado de resultados (TTM, calculado por el bot)",
            nombre="EPS (Ganancia por Acción)",
            que_mide="Cuánto ganó la empresa por cada acción en los últimos 12 meses",
        ),
        DesgloseTermino(
            letra="PER promedio peers",
            campo_origen="per_promedio_peers — 1/earningsYield de cada peer del sector (/key-metrics de FMP)",
            nombre="PER promedio de los comparables",
            que_mide="A cuántas veces sus ganancias cotizan, en promedio, empresas parecidas del mismo sector",
        ),
    ),
    # SDD_desglose_universal.md, Grupo B -- Ratios y pilares.
    "rat": (
        DesgloseTermino(
            letra="Liquidez",
            campo_origen="current_assets / current_liabilities — balance general",
            nombre="Liquidez corriente",
            que_mide="Si la empresa puede pagar sus deudas de corto plazo con lo que tiene a mano",
        ),
        DesgloseTermino(
            letra="Margen bruto",
            campo_origen="(revenue − cost_of_revenue) / revenue — estado de resultados",
            nombre="Margen bruto",
            que_mide="Cuánto le queda de cada venta después del costo directo de producir/vender",
        ),
        DesgloseTermino(
            letra="PER",
            campo_origen="precio_actual / eps_ttm — cotización + estado de resultados",
            nombre="PER (Precio/Ganancia)",
            que_mide="A cuántas veces sus ganancias anuales cotiza la acción hoy",
        ),
        DesgloseTermino(
            letra="P/S",
            campo_origen="market_cap / revenue — cotización + estado de resultados",
            nombre="P/S (Precio/Ventas)",
            que_mide="A cuántas veces sus ventas anuales está valuada la empresa en bolsa",
        ),
    ),
    "pil": (
        DesgloseTermino(
            letra="Ingresos crecientes",
            campo_origen="revenue_reciente vs. revenue_antiguo — estado de resultados",
            nombre="Ingresos crecientes",
            que_mide="Si la empresa factura más ahora que al principio de su historial",
        ),
        DesgloseTermino(
            letra="Utilidades crecientes",
            campo_origen="net_income_reciente vs. net_income_antiguo — estado de resultados",
            nombre="Utilidades crecientes",
            que_mide="Si la empresa gana más plata ahora que al principio de su historial, y no está perdiendo",
        ),
        DesgloseTermino(
            letra="Deuda controlada",
            campo_origen="ratio_liquidez > 1 — balance general",
            nombre="Deuda controlada",
            que_mide="Si la empresa puede cubrir sus deudas de corto plazo con lo que tiene a mano",
        ),
        DesgloseTermino(
            letra="Precio razonable",
            campo_origen="clasificación barata/cara del escenario Conservador — botón «⚖️ Veredicto»",
            nombre="Precio razonable",
            que_mide="Si, según el modelo del bot, la acción cotiza por debajo de lo que vale hoy",
        ),
    ),
    # SDD_desglose_universal.md, Grupo C -- Riesgo y mercado.
    "rsk": (
        DesgloseTermino(
            letra="Beta",
            campo_origen="beta — dato de FMP (profile.beta)",
            nombre="Beta",
            que_mide="Qué tan volátil es la acción comparada con el mercado en general (1.0 = igual de volátil)",
        ),
        DesgloseTermino(
            letra="Perfil de riesgo",
            campo_origen="perfil guardado con /start",
            nombre="Perfil de riesgo",
            que_mide="El nivel de riesgo que el usuario eligió tolerar al configurar el bot",
        ),
    ),
    "mom": (
        DesgloseTermino(
            letra="vs. máx. 52 semanas",
            campo_origen="precio_actual vs. year_high — cotización (quote) de FMP",
            nombre="Precio vs. máximo anual",
            que_mide="Qué tan lejos está el precio de hoy de su punto más alto en el último año",
        ),
        DesgloseTermino(
            letra="vs. mín. 52 semanas",
            campo_origen="precio_actual vs. year_low — cotización (quote) de FMP",
            nombre="Precio vs. mínimo anual",
            que_mide="Qué tan lejos está el precio de hoy de su punto más bajo en el último año",
        ),
        DesgloseTermino(
            letra="vs. promedio 50 días",
            campo_origen="precio_actual vs. price_avg_50 — cotización (quote) de FMP",
            nombre="Precio vs. promedio de 50 días",
            que_mide="Cómo está el precio de hoy respecto a su tendencia de corto plazo",
        ),
        DesgloseTermino(
            letra="vs. promedio 200 días",
            campo_origen="precio_actual vs. price_avg_200 — cotización (quote) de FMP",
            nombre="Precio vs. promedio de 200 días",
            que_mide="Cómo está el precio de hoy respecto a su tendencia de largo plazo",
        ),
    ),
    "cmp": (
        DesgloseTermino(
            letra="PER propio",
            campo_origen="precio_actual / eps_ttm — cotización + estado de resultados",
            nombre="PER propio",
            que_mide="A cuántas veces sus ganancias anuales cotiza esta acción hoy",
        ),
        DesgloseTermino(
            letra="PER promedio peers",
            campo_origen="per_promedio_peers — 1/earningsYield de cada peer (/key-metrics de FMP)",
            nombre="PER promedio de los comparables",
            que_mide="A cuántas veces sus ganancias cotizan, en promedio, empresas parecidas del mismo sector",
        ),
    ),
    # SDD_desglose_universal.md, Grupo D -- `ver` recibe un desglose liviano
    # de 2 términos "puntero corto" (no repite el desglose completo de "vf").
    "ver": (
        DesgloseTermino(
            letra="Precio actual",
            campo_origen="precio_actual — cotización (quote) de FMP",
            nombre="Precio actual",
            que_mide="Lo que cuesta hoy 1 acción de la empresa en el mercado",
        ),
        DesgloseTermino(
            letra="Valor Justo Total",
            campo_origen="cálculo completo: botón «💰 Valor Justo Total»",
            nombre="Valor Justo Total",
            que_mide="Cuánto debería valer la acción según el promedio de los modelos calculables del bot",
        ),
    ),
}


def desglose(kind: str, code: str) -> tuple[DesgloseTermino, ...]:
    """`()` para cualquier `code` sin entrada en la tabla correspondiente --
    las 20 preguntas de `/avanzado` y las 21 de texto libre sin desglose no
    rompen nada, se comportan como hoy (Decisión de diseño #1: `pig` es una
    de esas 20 -- se apoya en pir/pia/pie, no tiene desglose propio).
    SDD_desglose_valor_justo_total.md [Iter-2]: agrega la rama `texto_libre`
    con el mismo criterio (`dict.get(code, ())`) que ya usaba `avanzado` --
    ninguna de las 21 preguntas de texto libre sin entrada nueva (todas menos
    `vf`) cambia de comportamiento."""
    tabla = DESGLOSE_AVANZADO if kind == "avanzado" else DESGLOSE_TEXTO_LIBRE
    return tabla.get(code, ())


def all_questions(kind: str) -> dict[str, QuestionSpec]:
    return QUESTIONS_TEXTO_LIBRE if kind == "texto_libre" else QUESTIONS_AVANZADO


def all_categories(kind: str) -> dict[str, CategorySpec]:
    return CATEGORIES_TEXTO_LIBRE if kind == "texto_libre" else CATEGORIES_AVANZADO


def level1(kind: str) -> tuple[tuple[str, str], ...]:
    return LEVEL1_TEXTO_LIBRE if kind == "texto_libre" else LEVEL1_AVANZADO


def formulas(kind: str) -> dict[str, str]:
    return FORMULAS_TEXTO_LIBRE if kind == "texto_libre" else FORMULAS_AVANZADO


def fuentes(kind: str) -> dict[str, str]:
    return FUENTES_TEXTO_LIBRE if kind == "texto_libre" else FUENTES_AVANZADO


# --- Decisión de diseño #2 (SDD_explicacion_paso_a_paso.md) -- el menú
# reaparece SIEMPRE tras cualquier respuesta: Nivel 2 de la categoría si la
# pregunta pertenece a una, Nivel 1 si es suelta. `category_of` es la
# búsqueda inversa, precomputada a nivel de módulo (O(1), función pura). ---


def _build_category_of_index(categories: dict[str, CategorySpec]) -> dict[str, str]:
    index: dict[str, str] = {}
    for cat_code, cat in categories.items():
        for question_code in cat.question_codes:
            index[question_code] = cat_code
    return index


_CATEGORY_OF_TEXTO_LIBRE: dict[str, str] = _build_category_of_index(CATEGORIES_TEXTO_LIBRE)
_CATEGORY_OF_AVANZADO: dict[str, str] = _build_category_of_index(CATEGORIES_AVANZADO)


def category_of(kind: str, code: str) -> Optional[str]:
    """`None` si `code` es una pregunta suelta (Nivel 1) de este `kind`; el
    código de categoría correspondiente en caso contrario."""
    index = _CATEGORY_OF_TEXTO_LIBRE if kind == "texto_libre" else _CATEGORY_OF_AVANZADO
    return index.get(code)
