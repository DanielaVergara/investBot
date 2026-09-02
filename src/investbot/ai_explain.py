"""Explicaciones interactivas con Ollama, menú por capas —
`SDD_menu_por_capas_explicaciones.md` (rediseño de
`SDD_explicaciones_interactivas_ollama.md`, cerrada, + 2 fixes de
producción del 2026-09-02 ya incorporados).

Un solo `CallbackQueryHandler` compartido (`build_explain_handler`) atiende
los botones `xp:{context_id}:...` de AMBOS flujos (texto libre y
`/avanzado`) — el `ExplanationContext.kind` guardado, no el módulo que lo
generó, decide qué tabla/prompt/datos usar.

3 formas de `callback_data` (Decisión de diseño #1 de la spec):
- `xp:{id}:m`        -> menú de Nivel 1 (categorías + leaves sueltos)
- `xp:{id}:c:{cat}`  -> botones de Nivel 2 de una categoría + "🔙 Menú"
- `xp:{id}:{code}`   -> pregunta puntual (leaf) — determinística o vía
  Ollama según `QuestionSpec.requires_ollama` (`ai_explain_content.py`)

Guard de integridad (heredado sin aflojar): la respuesta de Ollama puede
usar cualquier SUBCONJUNTO de los tokens protegidos que le pasamos, pero
ningún token protegido en la respuesta puede estar ausente de los datos
originales (bloquea alucinación de números/tickers nuevos). La fórmula y la
fuente del dato (Decisión de diseño #5) son SIEMPRE texto fijo agregado
DESPUÉS de la respuesta de Ollama — nunca pasan por este guard porque nunca
pasan por Ollama.

Hallazgo 1 BLOQUEANTE de `security` (spec anterior, sigue vigente):
`sector`/`industry` (texto libre de FMP) nunca viajan crudos al prompt de
Ollama. `sector` se valida contra una allow-list GICS cerrada
(`_SECTOR_ALLOWLIST`); `industry` se excluye directamente del payload en
todos los casos.

Hallazgo 9 MEDIO de `security` (esta spec, remediado en esta misma
iteración): `ExplanationContext` lleva el `chat_id` que lo generó —
`_get_owned_context` rechaza cualquier acceso desde un `chat_id` distinto,
con el MISMO mensaje y el MISMO log (nivel + string) que un `context_id`
vencido/inexistente — no debe poder inferirse por la respuesta si el
`context_id` probado existe pero es de otro usuario.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from investbot import ai_explain_content, ai_rewrite, summary

logger = logging.getLogger(__name__)

# --- Logging seguro (duplicado local, no importado de query_handler.py) ----
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]")


def _sanitize_for_log(text: str) -> str:
    return _CONTROL_CHARS_RE.sub(" ", text)


# Texto idéntico a `query_handler.RATE_LIMITED_MSG` — mismo balde compartido.
RATE_LIMITED_MSG = "Estás consultando muy rápido — esperá un minuto antes de volver a intentar."

# --- Mensajes de fallback ----------------------------------------------

EXPLAIN_UNAVAILABLE_MSG = "📋 Ollama no está disponible en este momento — probá de nuevo en un rato."
EXPLAIN_EXPIRED_MSG = "Este botón ya venció — pedí el análisis de nuevo para ver explicaciones."
EXPLAIN_PENDING_MSG = "🤔 Pensando la explicación…"
EXPLAIN_INVALID_MSG = "Ese botón no es válido — pedí el análisis de nuevo si querés una explicación."

# Prefijo de contenido 100% determinístico (Decisión de diseño #4 — `evt`/
# `inf`, nunca pasan por Ollama). Mismo espíritu que
# `advanced_command.TRANSPARENCY_FIXED_NO_BUTTONS`.
DETERMINISTIC_PREFIX = "📋 Contenido fijo (sin IA)."

_MENU_PROMPT = {
    "texto_libre": "👇 Elegí qué querés que te explique.",
    "avanzado": "👇 Elegí qué modelo querés ver en detalle.",
}

# --- ExplanationContext / Store (extendido — Decisión de diseño #8 y
# Hallazgo 9 de `security`) ---------------------------------------------


@dataclass(frozen=True)
class ExplanationContext:
    # Hallazgo 9 MEDIO de `security` — obligatorio, primer campo: el
    # `chat_id` que generó este contexto. `handle_explain` lo compara contra
    # el `chat_id` del `callback_query` antes de despachar CUALQUIERA de las
    # 3 formas de `callback_data`.
    chat_id: int
    kind: str  # "texto_libre" | "avanzado"
    ticker: str
    company_name: str
    # --- payload específico de texto libre (kind="texto_libre") ---
    escenario_elegido: Optional[str] = None
    precio_actual: Optional[float] = None
    scenarios: Optional[dict] = None  # scenarios.as_dict() ya calculado
    pillars: Optional[dict] = None  # pillars_dict ya calculado
    veredicto_barata: Optional[bool] = None
    ratios: Optional[dict] = None
    risk_fit: Optional[dict] = None
    momentum: Optional[dict] = None
    peer_comparison: Optional[dict] = None
    extras: Optional[dict] = None
    vix: Optional[dict] = None
    corporate_events: Optional[list] = None
    treasury_source: Optional[str] = None
    balance_sheet_fuente: Optional[str] = None
    income_statement_fuente: Optional[str] = None
    cash_flow_fuente: Optional[str] = None
    peers_note: Optional[str] = None
    # --- payload específico de /avanzado (kind="avanzado") ---
    sector: Optional[str] = None
    industry: Optional[str] = None
    asset_light: Optional[bool] = None
    altman: Optional[dict] = None
    altman_pp: Optional[dict] = None
    piotroski: Optional[dict] = None
    beneish: Optional[dict] = None
    magic: Optional[dict] = None
    factors: Optional[dict] = None
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    beta: Optional[float] = None


@dataclass(frozen=True)
class _StoredEntry:
    context: ExplanationContext
    expires_at: float


class ExplanationContextStore:
    """Dict en memoria del proceso con tope de tamaño + expiración por
    tiempo. `store.get()` es una recuperación por clave PURA — la
    verificación de que el `chat_id` que pide el contexto es el mismo que lo
    generó vive en `_get_owned_context`, no acá (Hallazgo 9 de `security`,
    confirmado en su revisión: la navegación nunca llama a `store.put()`,
    solo a `store.get()` sobre una entrada ya existente)."""

    def __init__(
        self,
        max_entries: int = 500,
        ttl_seconds: float = 12 * 3600,
        *,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._now_fn = now_fn
        self._entries: dict[str, _StoredEntry] = {}

    def _evict_expired(self) -> None:
        now = self._now_fn()
        expired = [cid for cid, entry in self._entries.items() if now >= entry.expires_at]
        for cid in expired:
            del self._entries[cid]

    def put(self, context: ExplanationContext) -> str:
        self._evict_expired()

        context_id = uuid.uuid4().hex[:8]
        while context_id in self._entries:
            context_id = uuid.uuid4().hex[:8]

        if len(self._entries) >= self.max_entries:
            oldest_id = next(iter(self._entries))
            del self._entries[oldest_id]

        self._entries[context_id] = _StoredEntry(
            context=context, expires_at=self._now_fn() + self.ttl_seconds
        )
        return context_id

    def get(self, context_id: str) -> Optional[ExplanationContext]:
        entry = self._entries.get(context_id)
        if entry is None:
            return None
        if self._now_fn() >= entry.expires_at:
            del self._entries[context_id]
            return None
        return entry.context


def _get_owned_context(
    store: ExplanationContextStore, context_id: str, chat_id
) -> Optional[ExplanationContext]:
    """`None` si el `context_id` no existe, venció, O pertenece a otro
    `chat_id` (Hallazgo 9 de `security`) — LOS 3 CASOS comparten el mismo
    log (mismo string, mismo nivel WARNING) para que no se pueda inferir,
    por la respuesta ni por el log, si un `context_id` probado existe pero
    es de otro usuario."""
    stored = store.get(context_id)
    if stored is None or stored.chat_id != chat_id:
        logger.warning(
            "context_id inválido, vencido, o de otro chat_id en callback xp:: %s",
            _sanitize_for_log(context_id),
        )
        return None
    return stored


# --- Validación de callback_data (Decisión de diseño #1 — 3 formas) --------

_CALLBACK_MENU_RE = re.compile(r"^xp:([0-9a-f]{8}):m$")
_CALLBACK_CATEGORY_RE = re.compile(r"^xp:([0-9a-f]{8}):c:([a-z]{2,4})$")
_CALLBACK_LEAF_RE = re.compile(r"^xp:([0-9a-f]{8}):([a-z]{2,4})$")

_ALL_CATEGORY_CODES = frozenset(ai_explain_content.CATEGORIES_TEXTO_LIBRE) | frozenset(
    ai_explain_content.CATEGORIES_AVANZADO
)


# --- Sanitización de sector/industry (hallazgo 1 BLOQUEANTE de `security`,
# spec anterior — sin cambios) -----------------------------------------

_SECTOR_ALLOWLIST = frozenset(
    {
        "Basic Materials",
        "Communication Services",
        "Consumer Cyclical",
        "Consumer Defensive",
        "Energy",
        "Financial Services",
        "Healthcare",
        "Industrials",
        "Real Estate",
        "Technology",
        "Utilities",
    }
)
_SECTOR_NO_DISPONIBLE = "no disponible"


def _validated_sector(sector: Optional[str]) -> str:
    if sector in _SECTOR_ALLOWLIST:
        return sector
    return _SECTOR_NO_DISPONIBLE


# --- Nombre del modelo financiero + constantes de conteo por pregunta
# (Bug 2 de producción, extendido — Decisión de diseño #11) -----------------

_TOTAL_MODELOS_AVANZADO = 5
_MODELO_VF = "Graham (EPS) + DCF (flujo de caja descontado)"
_MODELO_PIL = "los 4 pilares de calidad fundamental"
_MODELO_VER = f"{_MODELO_VF} + {_MODELO_PIL}"
_MODELO_PIO = "Piotroski F-Score"
_MODELO_MAG = "Magic Formula (Greenblatt)"
_MODELO_AQR = "scoring de factores estilo AQR"
_MODELO_ALZ = "Altman Z-Score"
_MODELO_AZP = "Altman Z'' (variante para empresas asset-light)"
_MODELOS_AVANZADO = ["Altman Z-Score", _MODELO_PIO, "Beneish M-Score", _MODELO_MAG, _MODELO_AQR]

_PIOTROSKI_RENTABILIDAD = frozenset(
    {"roa_positivo", "cfo_positivo", "roa_creciente", "cfo_mayor_utilidad"}
)
_PIOTROSKI_APALANCAMIENTO = frozenset(
    {"apalancamiento_decreciente", "liquidez_creciente", "sin_dilucion"}
)
_PIOTROSKI_EFICIENCIA = frozenset({"margen_bruto_creciente", "rotacion_activos_creciente"})

_ETIQUETA_LEGIBLE = {
    "alto": "alto",
    "medio": "medio",
    "bajo": "bajo",
    "no_disponible": "no disponible",
}

_ESCENARIO_TITULOS = {"pesimista": "Pesimista", "conservador": "Conservador", "optimista": "Optimista"}


# --- Sub-dict por pregunta (Decisión de diseño #11 — superficie mínima,
# nunca el ExplanationContext completo) --------------------------------


def _payload_texto_libre(context: ExplanationContext, question_code: str) -> dict:
    scenarios = context.scenarios or {}

    if question_code == "ver":
        escenario = scenarios.get(context.escenario_elegido) or {}
        return {
            "modelo": _MODELO_VER,
            "veredicto_barata": context.veredicto_barata,
            "escenario_elegido": context.escenario_elegido,
            "precio_actual": context.precio_actual,
            "valor_justo_total": escenario.get("valor_justo_total"),
        }
    if question_code == "vf":
        escenario = scenarios.get(context.escenario_elegido) or {}
        return {
            "modelo": _MODELO_VF,
            "escenario_elegido": context.escenario_elegido,
            "precio_actual": context.precio_actual,
            "valor_justo_multiplos": escenario.get("valor_justo_multiplos"),
            "valor_justo_graham": escenario.get("valor_justo_graham"),
            "valor_justo_dcf": escenario.get("valor_justo_dcf"),
            "valor_justo_total": escenario.get("valor_justo_total"),
        }
    if question_code in ("gra", "dcf", "mul"):
        campo = {
            "gra": "valor_justo_graham",
            "dcf": "valor_justo_dcf",
            "mul": "valor_justo_multiplos",
        }[question_code]
        modelo = {"gra": "Graham (EPS)", "dcf": "DCF", "mul": "Múltiplos"}[question_code]
        return {
            "modelo": modelo,
            "escenario_elegido": context.escenario_elegido,
            "pesimista": (scenarios.get("pesimista") or {}).get(campo),
            "conservador": (scenarios.get("conservador") or {}).get(campo),
            "optimista": (scenarios.get("optimista") or {}).get(campo),
        }
    if question_code == "rat":
        ratios = context.ratios or {}
        return {
            "modelo": "Ratios clave",
            "ratio_liquidez": ratios.get("ratio_liquidez"),
            "liquidez_sin_pasivos_circulantes": ratios.get("liquidez_sin_pasivos_circulantes"),
            "margen_bruto": ratios.get("margen_bruto"),
            "per": ratios.get("per"),
            "per_no_aplicable": ratios.get("per_no_aplicable"),
            "ps": ratios.get("ps"),
        }
    if question_code == "pil":
        return {
            "modelo": _MODELO_PIL,
            "pillars": context.pillars,
            "total_pilares": len(context.pillars) if context.pillars else 4,
        }
    if question_code == "ren":
        extras = context.extras or {}
        return {
            "modelo": "ROE y rentabilidad",
            "roe": extras.get("roe"),
            "debt_to_equity": extras.get("debt_to_equity"),
            "net_debt_to_ebitda": extras.get("net_debt_to_ebitda"),
            "dividend_yield": extras.get("dividend_yield"),
            "payout_ratio": extras.get("payout_ratio"),
        }
    if question_code == "rsk":
        risk_fit = context.risk_fit or {}
        return {
            "modelo": "Encaje con tu perfil de riesgo",
            "encaja": risk_fit.get("encaja"),
            "perfil": risk_fit.get("perfil"),
            "beta": risk_fit.get("beta"),
            "etiqueta_activo": risk_fit.get("etiqueta_activo"),
        }
    if question_code == "mom":
        momentum = context.momentum or {}
        vix = context.vix or {}
        return {
            "modelo": "Momentum y volatilidad",
            "pct_vs_year_high": momentum.get("pct_vs_year_high"),
            "pct_vs_year_low": momentum.get("pct_vs_year_low"),
            "pct_vs_avg_50": momentum.get("pct_vs_avg_50"),
            "pct_vs_avg_200": momentum.get("pct_vs_avg_200"),
            "etiqueta": momentum.get("etiqueta"),
            "vix_valor": vix.get("valor"),
            "vix_disponible": vix.get("disponible"),
        }
    if question_code == "cmp":
        pc = context.peer_comparison or {}
        return {
            "modelo": "Comparables del sector",
            "per_propio": pc.get("per_propio"),
            "per_minimo_peers": pc.get("per_minimo_peers"),
            "per_promedio_peers": pc.get("per_promedio_peers"),
            "per_maximo_peers": pc.get("per_maximo_peers"),
            "peers_usados": pc.get("peers_usados"),
            "posicion": pc.get("posicion"),
            "motivo_no_comparable": pc.get("motivo_no_comparable"),
        }
    raise ValueError(f"question_code desconocido: {question_code}")


def _criterios_de(criterios: Optional[list], nombres: frozenset) -> list:
    return [c for c in (criterios or []) if isinstance(c, dict) and c.get("nombre") in nombres]


def _payload_avanzado(context: ExplanationContext, question_code: str) -> dict:
    piotroski = context.piotroski or {}
    criterios = piotroski.get("criterios") or []

    if question_code == "mod":
        return {
            "modelos": _MODELOS_AVANZADO,
            "sector": _validated_sector(context.sector),
            "asset_light": context.asset_light,
            "total_modelos": _TOTAL_MODELOS_AVANZADO,
        }
    if question_code == "alz":
        return {"modelo": _MODELO_ALZ, "altman": context.altman}
    if question_code == "azp":
        return {"modelo": _MODELO_AZP, "altman_pp": context.altman_pp}
    if question_code == "pig":
        return {"modelo": _MODELO_PIO, "piotroski": context.piotroski}
    if question_code == "pir":
        return {
            "modelo": "Piotroski — Rentabilidad",
            "criterios": _criterios_de(criterios, _PIOTROSKI_RENTABILIDAD),
            "total_criterios_piotroski_rentabilidad": len(_PIOTROSKI_RENTABILIDAD),
        }
    if question_code == "pia":
        return {
            "modelo": "Piotroski — Apalancamiento y liquidez",
            "criterios": _criterios_de(criterios, _PIOTROSKI_APALANCAMIENTO),
            "total_criterios_piotroski_apalancamiento_liquidez": len(_PIOTROSKI_APALANCAMIENTO),
        }
    if question_code == "pie":
        return {
            "modelo": "Piotroski — Eficiencia",
            "criterios": _criterios_de(criterios, _PIOTROSKI_EFICIENCIA),
            "total_criterios_piotroski_eficiencia": len(_PIOTROSKI_EFICIENCIA),
        }
    if question_code == "ben":
        return {"modelo": "Beneish M-Score", "beneish": context.beneish}
    if question_code == "mgr":
        magic = context.magic or {}
        return {
            "modelo": "ROIC (Magic Formula)",
            "roic": magic.get("roic"),
            "disponible": magic.get("disponible"),
        }
    if question_code == "mge":
        magic = context.magic or {}
        return {
            "modelo": "Earnings Yield (Magic Formula)",
            "earnings_yield": magic.get("earnings_yield"),
            "disponible": magic.get("disponible"),
        }
    if question_code == "aqv":
        factors = context.factors or {}
        return {"modelo": "Factor Value (AQR)", "value": factors.get("value")}
    if question_code == "aqq":
        # Hallazgo señalado explícitamente por `architect` en el Handoff a
        # `security` — superficie mínima dedicada: SOLO quality/roe/
        # gross_margin/piotroski_ratio, nunca el resto del contexto.
        factors = context.factors or {}
        piotroski_ratio = None
        evaluables = piotroski.get("criterios_evaluables")
        if evaluables:
            piotroski_ratio = (piotroski.get("puntaje") or 0) / evaluables
        return {
            "modelo": "Factor Quality (AQR)",
            "quality": factors.get("quality"),
            "roe": context.roe,
            "gross_margin": context.gross_margin,
            "piotroski_ratio": piotroski_ratio,
        }
    if question_code == "aqm":
        factors = context.factors or {}
        return {"modelo": "Factor Momentum (AQR)", "momentum": factors.get("momentum")}
    if question_code == "aql":
        factors = context.factors or {}
        return {
            "modelo": "Factor Low-vol (AQR)",
            "low_vol": factors.get("low_vol"),
            "beta": context.beta,
        }
    raise ValueError(f"question_code desconocido: {question_code}")


def _build_explain_payload(context: ExplanationContext, question_code: str) -> dict:
    """Arma `datos_del_contexto` -- SOLO el sub-dict que la pregunta puntual
    necesita, nunca el `ExplanationContext` completo (superficie mínima,
    Decisión de diseño #11)."""
    if context.kind == "texto_libre":
        return _payload_texto_libre(context, question_code)
    return _payload_avanzado(context, question_code)


# --- Bloque "📌 Dato" (Decisión de diseño #5 — determinístico, sacado del
# mismo payload ya armado para Ollama, nunca recalculado) -------------------


def _fmt_money_or_nd(value: object) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


def _fmt_pct_or_nd(value: object) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


def _fmt_ratio_or_nd(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


def _dato_texto_libre(question_code: str, datos: dict) -> str:
    if question_code == "ver":
        veredicto = datos.get("veredicto_barata")
        etiqueta = "Barata" if veredicto is True else "Cara" if veredicto is False else "sin datos suficientes"
        return f"Veredicto: {etiqueta}"
    if question_code == "vf":
        esc = datos.get("escenario_elegido")
        titulo = _ESCENARIO_TITULOS.get(esc, esc or "")
        return (
            f"Valor Justo Total ({titulo}): {_fmt_money_or_nd(datos.get('valor_justo_total'))} "
            f"— precio actual: {_fmt_money_or_nd(datos.get('precio_actual'))}"
        )
    if question_code in ("gra", "dcf", "mul"):
        esc = datos.get("escenario_elegido")
        titulo = _ESCENARIO_TITULOS.get(esc, esc or "")
        valor = datos.get(esc) if esc in ("pesimista", "conservador", "optimista") else None
        return f"{datos.get('modelo')} ({titulo}): {_fmt_money_or_nd(valor)}"
    if question_code == "rat":
        piezas = []
        if datos.get("ratio_liquidez") is not None:
            piezas.append(f"Liquidez {datos['ratio_liquidez']:.2f}")
        if datos.get("margen_bruto") is not None:
            piezas.append(f"Margen bruto {_fmt_pct_or_nd(datos['margen_bruto'])}")
        if datos.get("per") is not None:
            piezas.append(f"PER {datos['per']:.2f}")
        if datos.get("ps") is not None:
            piezas.append(f"P/S {datos['ps']:.2f}")
        return " · ".join(piezas) if piezas else "sin datos disponibles esta consulta"
    if question_code == "pil":
        pillars = datos.get("pillars") or {}
        solidos = sum(1 for v in pillars.values() if v is True)
        total = datos.get("total_pilares") or 4
        return f"{solidos}/{total} pilares cumplidos"
    if question_code == "ren":
        return f"ROE: {_fmt_pct_or_nd(datos.get('roe'))}"
    if question_code == "rsk":
        encaja = datos.get("encaja")
        beta = datos.get("beta")
        beta_txt = f"{beta:.2f}" if isinstance(beta, (int, float)) else "N/D"
        return f"Encaje: {'SÍ' if encaja else 'NO'} (beta {beta_txt})"
    if question_code == "mom":
        etiqueta = datos.get("etiqueta") or "sin datos"
        return f"Momentum: {etiqueta} (vs. máx 52 sem.: {_fmt_pct_or_nd(datos.get('pct_vs_year_high'))})"
    if question_code == "cmp":
        return (
            f"PER propio: {_fmt_ratio_or_nd(datos.get('per_propio'))} vs. "
            f"promedio peers: {_fmt_ratio_or_nd(datos.get('per_promedio_peers'))}"
        )
    return "ver detalle abajo"


def _dato_avanzado(question_code: str, datos: dict) -> str:
    if question_code == "mod":
        return f"{len(datos.get('modelos') or [])} modelos en el framework"
    if question_code == "alz":
        altman = datos.get("altman") or {}
        if altman.get("disponible"):
            return f"Z: {altman.get('z'):.2f} (zona: {altman.get('zona')})"
        return "Altman Z: no calculable con los datos disponibles"
    if question_code == "azp":
        altman_pp = datos.get("altman_pp") or {}
        if altman_pp.get("disponible"):
            return f"Z'': {altman_pp.get('z'):.2f} (zona: {altman_pp.get('zona')})"
        return "Altman Z'': no calculable con los datos disponibles"
    if question_code == "pig":
        piotroski = datos.get("piotroski") or {}
        return f"F-Score: {piotroski.get('puntaje')}/{piotroski.get('criterios_evaluables')}"
    if question_code in ("pir", "pia", "pie"):
        criterios = datos.get("criterios") or []
        cumplidos = sum(1 for c in criterios if isinstance(c, dict) and c.get("cumplido") is True)
        return f"{cumplidos}/{len(criterios)} criterios cumplidos"
    if question_code == "ben":
        return "No calculable con este plan de FMP"
    if question_code == "mgr":
        if datos.get("disponible"):
            return f"ROIC: {_fmt_pct_or_nd(datos.get('roic'))}"
        return "ROIC: no calculable con los datos disponibles"
    if question_code == "mge":
        if datos.get("disponible"):
            return f"Earnings Yield: {_fmt_pct_or_nd(datos.get('earnings_yield'))}"
        return "Earnings Yield: no calculable con los datos disponibles"
    if question_code == "aqv":
        return f"Factor Value: {_ETIQUETA_LEGIBLE.get(datos.get('value'), datos.get('value'))}"
    if question_code == "aqq":
        return f"Factor Quality: {_ETIQUETA_LEGIBLE.get(datos.get('quality'), datos.get('quality'))}"
    if question_code == "aqm":
        return f"Factor Momentum: {_ETIQUETA_LEGIBLE.get(datos.get('momentum'), datos.get('momentum'))}"
    if question_code == "aql":
        return f"Factor Low-vol: {_ETIQUETA_LEGIBLE.get(datos.get('low_vol'), datos.get('low_vol'))}"
    return "ver detalle abajo"


def _build_dato_line(kind: str, question_code: str, datos: dict) -> str:
    if kind == "texto_libre":
        return _dato_texto_libre(question_code, datos)
    return _dato_avanzado(question_code, datos)


def _build_leaf_message(dato_line: str, respuesta: str, formula: Optional[str], fuente: Optional[str]) -> str:
    """Orden fijo (Decisión de diseño #5): header -> Dato -> respuesta de
    Ollama -> Fórmula/Fuente (si existen) -> disclaimer. Fórmula/Fuente NUNCA
    pasan por el guard de integridad (se agregan acá, después)."""
    partes = [ai_rewrite.TRANSPARENCY_USED, f"📌 Dato: {dato_line}", respuesta]
    formula_fuente_lines = []
    if formula:
        formula_fuente_lines.append(f"📐 Fórmula: {formula}")
    if fuente:
        formula_fuente_lines.append(f"📊 Fuente del dato: {fuente}")
    if formula_fuente_lines:
        partes.append("\n".join(formula_fuente_lines))
    partes.append(summary.DISCLAIMER_NO_ASESORAMIENTO)
    return "\n\n".join(partes)


def _build_deterministic_content(context: ExplanationContext, question_code: str) -> str:
    """`evt`/`inf` — Decisión de diseño #4: nunca llaman a Ollama, contenido
    ya construido por `summary.py`, prefijado con `DETERMINISTIC_PREFIX`."""
    if question_code == "evt":
        block = summary.build_corporate_events_section(context.corporate_events) or (
            "No hay eventos corporativos recientes registrados para este ticker."
        )
        return f"{DETERMINISTIC_PREFIX}\n\n{block}"
    if question_code == "inf":
        block = (
            summary.build_intro_section()
            + "\n\n"
            + summary.build_transparency_section(
                peers_note=context.peers_note,
                treasury_source=context.treasury_source,
                income_statement_fuente=context.income_statement_fuente,
                balance_sheet_fuente=context.balance_sheet_fuente,
                cash_flow_fuente=context.cash_flow_fuente,
            )
        )
        return f"{DETERMINISTIC_PREFIX}\n\n{block}"
    raise ValueError(f"question_code determinístico desconocido: {question_code}")


# --- Prompt + guard de brevedad/integridad (sin cambios de mecanismo) ------

SYSTEM_PROMPT_EXPLAIN = (
    "Sos un profesor de finanzas que explica en español rioplatense, en un\n"
    "mensaje de chat. Vas a recibir un JSON con una pregunta puntual y los\n"
    "datos ya calculados para responderla.\n\n"
    "Reglas estrictas:\n"
    "1. Respondé SOLO la pregunta puntual, en 2 a 4 oraciones cortas — nunca\n"
    "   más. No repitas la pregunta, no agregues introducción ni cierre.\n"
    "2. Usá ÚNICAMENTE los números/datos del JSON que te paso — nunca\n"
    "   inventes, estimes ni completes un dato que no esté ahí.\n"
    "3. Nunca dés una recomendación de compra/venta ni asesoramiento\n"
    "   financiero personalizado — solo explicá qué significa el dato.\n"
    "4. Respondé ÚNICAMENTE con un objeto JSON de la forma\n"
    "   {\"respuesta\": \"...\"}, sin texto antes ni después.\n"
    "5. Con tono de análisis de inversionista: si el JSON trae la clave\n"
    "   \"modelo\" o \"modelos\", nombrá ese/esos modelo(s) financiero(s) al\n"
    "   principio de tu respuesta y decí en general qué mide -- sin salirte\n"
    "   de las 2 a 4 oraciones de la regla 1.\n"
)

MAX_EXPLANATION_OUTPUT_TOKENS = 220
_MAX_EXPLANATION_CHARS = 480


def _enforce_brevity(texto: str) -> str:
    if len(texto) <= _MAX_EXPLANATION_CHARS:
        return texto
    corte = texto.rfind(".", 0, _MAX_EXPLANATION_CHARS)
    return texto[: corte + 1] if corte > 0 else texto[:_MAX_EXPLANATION_CHARS] + "…"


_NUMERIC_TOKEN_RE = re.compile(r"^[+-]?\$?\d[\d.,]*%?$")


def _normalize_numeric_token(token: str) -> str:
    if not _NUMERIC_TOKEN_RE.match(token):
        return token
    percent = token.endswith("%")
    body = token[:-1] if percent else token
    body = body.lstrip("+-").lstrip("$").rstrip(".,").replace(",", "")
    return body + "%" if percent else body


def _no_new_protected_tokens(datos_tokens: set[str], respuesta: str) -> bool:
    respuesta_tokens = {
        _normalize_numeric_token(token) for token in ai_rewrite.protected_tokens(respuesta)
    }
    return respuesta_tokens <= datos_tokens


class _ExplainUnavailable(Exception):
    """Señal interna -- cualquier fallo de red/estructura/guard converge
    acá y nunca se propaga fuera de `build_explain_handler`."""


async def _fetch_explanation(
    *,
    clients,
    config: ai_rewrite.OllamaConfig,
    question_code: str,
    pregunta_fija: str,
    datos_del_contexto: dict,
    datos_tokens: set[str],
) -> str:
    prompt = json.dumps(
        {"pregunta": pregunta_fija, "datos": datos_del_contexto}, ensure_ascii=False
    )

    http_client = clients.ollama_http if clients.ollama_http is not None else httpx.AsyncClient()
    timeout = httpx.Timeout(
        connect=ai_rewrite.CONNECT_TIMEOUT_SECONDS,
        read=config.timeout_seconds,
        write=config.timeout_seconds,
        pool=config.timeout_seconds,
    )

    try:
        response = await http_client.post(
            f"{config.base_url}/api/generate",
            json={
                "model": config.model,
                "system": SYSTEM_PROMPT_EXPLAIN,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"num_predict": MAX_EXPLANATION_OUTPUT_TOKENS},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["response"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.info(
            "Ollama no disponible o timeout generando explicación (%s)", type(exc).__name__
        )
        raise _ExplainUnavailable() from exc

    try:
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("respuesta"), str):
            raise ValueError("estructura inesperada -- falta la clave 'respuesta' string")
        respuesta = parsed["respuesta"]
    except (json.JSONDecodeError, ValueError) as exc:
        logger.info(
            "Respuesta de Ollama con estructura JSON inesperada generando "
            "explicación (%s)", type(exc).__name__,
        )
        raise _ExplainUnavailable() from exc

    if not _no_new_protected_tokens(datos_tokens, respuesta):
        logger.warning(
            "Explicación generada no pasó el guard de integridad -- "
            "descartada. Respuesta cruda: %s", _sanitize_for_log(respuesta),
        )
        raise _ExplainUnavailable()

    return _enforce_brevity(respuesta)


# --- Teclados (Nivel 1 / Nivel 2 — Decisión de diseño #1/#2/#3) ------------


def build_keyboard(kind: str, context_id: str) -> InlineKeyboardMarkup:
    """Nivel 1: leaves sueltos + categorías, en el orden fijo de
    `ai_explain_content.level1`. Ningún filtrado dinámico acá — las
    categorías siempre se muestran (Decisión de diseño #3: Altman siempre es
    categoría aunque `azp` no aplique); el filtrado de Nivel 2 vive en
    `build_category_keyboard`."""
    rows = []
    questions = ai_explain_content.all_questions(kind)
    categories = ai_explain_content.all_categories(kind)
    for item_kind, code in ai_explain_content.level1(kind):
        if item_kind == "leaf":
            spec = questions[code]
            rows.append([InlineKeyboardButton(spec.label, callback_data=f"xp:{context_id}:{code}")])
        else:
            cat = categories[code]
            rows.append(
                [InlineKeyboardButton(cat.label, callback_data=f"xp:{context_id}:c:{code}")]
            )
    return InlineKeyboardMarkup(rows)


def _leaf_visible(code: str, context: Optional[ExplanationContext]) -> bool:
    """Filtrado dinámico de Nivel 2 (criterios de aceptación de navegación):
    `azp` solo si `altman_pp is not None`; `evt` solo si hay eventos
    corporativos."""
    if context is None:
        return True
    if code == "azp":
        return context.altman_pp is not None
    if code == "evt":
        return bool(context.corporate_events)
    return True


def build_category_keyboard(
    kind: str, context_id: str, category_code: str, context: Optional[ExplanationContext]
) -> Optional[InlineKeyboardMarkup]:
    categories = ai_explain_content.all_categories(kind)
    cat = categories.get(category_code)
    if cat is None:
        return None
    questions = ai_explain_content.all_questions(kind)
    codes = [c for c in cat.question_codes if _leaf_visible(c, context)]
    rows = [
        [InlineKeyboardButton(questions[c].label, callback_data=f"xp:{context_id}:{c}")]
        for c in codes
    ]
    rows.append([InlineKeyboardButton("🔙 Menú", callback_data=f"xp:{context_id}:m")])
    return InlineKeyboardMarkup(rows)


# --- Dispatch de las 3 formas de callback_data (Decisión de diseño #1) -----


async def _dispatch_menu(bot, chat_id, store: ExplanationContextStore, context_id: str) -> None:
    stored = _get_owned_context(store, context_id, chat_id)
    if stored is None:
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
        return
    keyboard = build_keyboard(stored.kind, context_id)
    await bot.send_message(chat_id=chat_id, text=_MENU_PROMPT[stored.kind], reply_markup=keyboard)


async def _dispatch_category(
    bot, chat_id, store: ExplanationContextStore, context_id: str, category_code: str
) -> None:
    if category_code not in _ALL_CATEGORY_CODES:
        logger.warning(
            "categoría desconocida en callback xp:c:: %s", _sanitize_for_log(category_code)
        )
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_INVALID_MSG)
        return

    stored = _get_owned_context(store, context_id, chat_id)
    if stored is None:
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
        return

    categories = ai_explain_content.all_categories(stored.kind)
    cat = categories.get(category_code)
    if cat is None:
        # Categoría sintácticamente válida y conocida, pero de la tabla del
        # OTRO `kind` (mejora recomendada #1 de `security`) — mismo camino
        # que un context_id vencido, nunca un error no manejado.
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
        return

    keyboard = build_category_keyboard(stored.kind, context_id, category_code, stored)
    await bot.send_message(chat_id=chat_id, text=f"👇 {cat.label}", reply_markup=keyboard)


async def _dispatch_leaf(
    bot, chat_id, store: ExplanationContextStore, rate_limiter, clients,
    context_id: str, question_code: str,
) -> None:
    if (
        question_code not in ai_explain_content.QUESTIONS_TEXTO_LIBRE
        and question_code not in ai_explain_content.QUESTIONS_AVANZADO
    ):
        logger.warning(
            "question_code desconocido en callback xp:: %s", _sanitize_for_log(question_code)
        )
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_INVALID_MSG)
        return

    stored = _get_owned_context(store, context_id, chat_id)
    if stored is None:
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
        return

    questions = ai_explain_content.all_questions(stored.kind)
    spec = questions.get(question_code)
    if spec is None:
        # question_code válido y conocido, pero de la tabla equivocada para
        # el kind del contexto recuperado (comportamiento heredado).
        await bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
        return

    if not spec.requires_ollama:
        # Decisión de diseño #4 — determinístico: sin "🤔 Pensando…", sin
        # llamada HTTP, sin pasar por el guard, sin consumir el balde.
        texto = _build_deterministic_content(stored, question_code)
        await bot.send_message(chat_id=chat_id, text=texto)
        return

    # Decisión de diseño #10 — SOLO lo que llama a Ollama consume el balde.
    if not rate_limiter.allow(str(chat_id)):
        await bot.send_message(chat_id=chat_id, text=RATE_LIMITED_MSG)
        return

    datos_del_contexto = _build_explain_payload(stored, question_code)
    datos_tokens = {
        _normalize_numeric_token(token)
        for token in ai_rewrite.protected_tokens(
            json.dumps(datos_del_contexto, ensure_ascii=False, default=str)
        )
    }
    config = clients.ollama_config or ai_rewrite.OllamaConfig(
        enabled=False, base_url="", model="", timeout_seconds=0.0
    )

    pensando = await bot.send_message(chat_id=chat_id, text=EXPLAIN_PENDING_MSG)

    try:
        respuesta = await _fetch_explanation(
            clients=clients,
            config=config,
            question_code=question_code,
            pregunta_fija=spec.pregunta_fija,
            datos_del_contexto=datos_del_contexto,
            datos_tokens=datos_tokens,
        )
    except _ExplainUnavailable:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=pensando.message_id, text=EXPLAIN_UNAVAILABLE_MSG
        )
        return

    dato_line = _build_dato_line(stored.kind, question_code, datos_del_contexto)
    formula = ai_explain_content.formulas(stored.kind).get(question_code)
    fuente = ai_explain_content.fuentes(stored.kind).get(question_code)
    texto = _build_leaf_message(dato_line, respuesta, formula, fuente)
    await bot.edit_message_text(chat_id=chat_id, message_id=pensando.message_id, text=texto)


# --- Handler compartido -----------------------------------------------------


def build_explain_handler(clients, rate_limiter, store: ExplanationContextStore) -> CallbackQueryHandler:
    """Un solo `CallbackQueryHandler` para el prefijo `xp:`, compartido por
    ambos flujos -- registrado una sola vez en `bot.py::build_application`."""

    async def handle_explain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer("Generando explicación…")

        chat = update.effective_chat
        chat_id = chat.id if chat is not None else None

        raw_data = query.data or ""

        match_menu = _CALLBACK_MENU_RE.fullmatch(raw_data)
        if match_menu is not None:
            await _dispatch_menu(context.bot, chat_id, store, match_menu.group(1))
            return

        match_cat = _CALLBACK_CATEGORY_RE.fullmatch(raw_data)
        if match_cat is not None:
            await _dispatch_category(
                context.bot, chat_id, store, match_cat.group(1), match_cat.group(2)
            )
            return

        match_leaf = _CALLBACK_LEAF_RE.fullmatch(raw_data)
        if match_leaf is None:
            logger.warning(
                "callback_data de xp: malformado: %s", _sanitize_for_log(raw_data)
            )
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_INVALID_MSG)
            return

        await _dispatch_leaf(
            context.bot, chat_id, store, rate_limiter, clients,
            match_leaf.group(1), match_leaf.group(2),
        )

    return CallbackQueryHandler(handle_explain, pattern=r"^xp:")
