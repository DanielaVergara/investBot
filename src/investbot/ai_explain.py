"""Explicaciones interactivas con Gemini, menú por capas —
`SDD_menu_por_capas_explicaciones.md` (rediseño de
`SDD_explicaciones_interactivas_ollama.md`, cerrada, + 2 fixes de
producción del 2026-09-02 ya incorporados).

Un solo `CallbackQueryHandler` compartido (`build_explain_handler`) atiende
los botones `xp:{context_id}:...` de AMBOS flujos (texto libre y
`/avanzado`) — el `ExplanationContext.kind` guardado, no el módulo que lo
generó, decide qué tabla/prompt/datos usar.

4 formas de `callback_data` (Decisión de diseño #1 de `SDD_menu_por_capas_
explicaciones.md`, extendida por `SDD_explicacion_paso_a_paso.md`):
- `xp:{id}:m`        -> menú de Nivel 1 (categorías + leaves sueltos)
- `xp:{id}:c:{cat}`  -> botones de Nivel 2 de una categoría + "🔙 Menú"
- `xp:{id}:{code}`   -> "Ver dato" -- pregunta puntual (leaf), según
  `QuestionSpec.variant` (`ai_explain_content.py`): determinístico sin
  Gemini para `dato_y_paso_a_paso`/`deterministico`, o llama a Gemini con
  `SYSTEM_PROMPT_EXPLAIN` para `narrativa`.
- `xp:{id}:p:{code}` -> "Explicame paso a paso" -- solo preguntas
  `variant="dato_y_paso_a_paso"`; llama a Gemini con la cuenta ya resuelta
  (100% Python) inyectada al payload y `SYSTEM_PROMPT_PASO_A_PASO`.

Guard de integridad (heredado sin aflojar): la respuesta de Gemini puede
usar cualquier SUBCONJUNTO de los tokens protegidos que le pasamos, pero
ningún token protegido en la respuesta puede estar ausente de los datos
originales (bloquea alucinación de números/tickers nuevos). La fórmula y la
fuente del dato (Decisión de diseño #5) son SIEMPRE texto fijo agregado
DESPUÉS de la respuesta de Gemini — nunca pasan por este guard porque nunca
pasan por Gemini.

Hallazgo 1 BLOQUEANTE de `security` (spec anterior, sigue vigente):
`sector`/`industry` (texto libre de FMP) nunca viajan crudos al prompt de
Gemini. `sector` se valida contra una allow-list GICS cerrada
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
import math
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from investbot import (
    advanced_scoring,
    ai_explain_content,
    ai_rewrite,
    db,
    gemini_client,
    risk_fit,
    summary,
    valuation,
)
from investbot.gemini_client import GeminiConfig, GeminiError, violates_output_filter

logger = logging.getLogger(__name__)

# --- Logging seguro (duplicado local, no importado de query_handler.py) ----
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]")


def _sanitize_for_log(text: str) -> str:
    return _CONTROL_CHARS_RE.sub(" ", text)


# --- Presentación segura de texto no confiable (SDD Iter-2, CS-28 C3 / M-6) ---
_SAFE_DISPLAY_MAX_LINES = 40
_SAFE_DISPLAY_KEEP_CATEGORIES = frozenset("LMNPS")


def _safe_display(texto: str, max_chars: int = 2500, keep_newlines: bool = True) -> str:
    """Texto no confiable (salida del modelo) -> string imprimible sin riesgo.

    Lista BLANCA por `unicodedata.category`: se conserva todo carácter L, M, N,
    P o S; los separadores Zs pasan a un espacio ASCII; el salto de línea se conserva (o pasa
    a espacio con `keep_newlines=False`, p. ej. para una línea de log); todo lo
    demás (Cc incl. ESC y C1, Cf incl. bidi/ZWJ/tag chars, Cs, Co, Cn, Zl, Zp)
    se escribe `<U+XXXX>`. Máximo `max_chars` caracteres de SALIDA (nunca se
    parte un `<U+XXXX>`) y `_SAFE_DISPLAY_MAX_LINES` líneas.
    """
    if texto is None:
        return ""
    if not isinstance(texto, str):
        texto = str(texto)
    partes: list[str] = []
    usados = 0
    lineas = 1
    for ch in texto:
        if ch == "\n":
            if keep_newlines:
                if lineas >= _SAFE_DISPLAY_MAX_LINES:
                    break
                pieza = "\n"
                lineas += 1
            else:
                pieza = " "
        else:
            categoria = unicodedata.category(ch)
            if categoria == "Zs":
                pieza = " "
            elif categoria[0] in _SAFE_DISPLAY_KEEP_CATEGORIES:
                pieza = ch
            else:
                pieza = f"<U+{ord(ch):04X}>"
        if usados + len(pieza) > max_chars:
            break
        partes.append(pieza)
        usados += len(pieza)
    return "".join(partes)


# Texto idéntico a `query_handler.RATE_LIMITED_MSG` — mismo balde compartido.
RATE_LIMITED_MSG = "Estás consultando muy rápido — esperá un minuto antes de volver a intentar."

# --- Mensajes de fallback ----------------------------------------------

EXPLAIN_UNAVAILABLE_MSG = (
    "📋 La explicación con IA no está disponible en este momento — probá de nuevo en un rato."
)
EXPLAIN_EXPIRED_MSG = "Este botón ya venció — pedí el análisis de nuevo para ver explicaciones."
EXPLAIN_PENDING_MSG = "🤔 Pensando la explicación…"
EXPLAIN_INVALID_MSG = "Ese botón no es válido — pedí el análisis de nuevo si querés una explicación."

# Fix urgente 2026-09-04 — paridad con `query_handler.GENERIC_ERROR_MSG`: si
# la entrega final de la explicación falla (`edit_message_text` sobre el
# mensaje "🤔 Pensando…"), este es el último recurso para que el usuario no
# se quede mirando ese mensaje para siempre sin ninguna respuesta.
EXPLAIN_DELIVERY_FAILED_MSG = "⚠️ Algo salió mal generando la explicación. Probá de nuevo en un rato."

# Prefijo de contenido 100% determinístico (Decisión de diseño #4 — `evt`/
# `inf`, nunca pasan por Gemini). Mismo espíritu que
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
    # --- campos nuevos para "Explicame paso a paso" (kind="texto_libre") --
    # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- todos ya se
    # calculaban en `query_handler.fetch_and_analyze_parts` y se descartaban;
    # cero llamadas HTTP nuevas.
    eps_ttm: Optional[float] = None
    y_value: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    revenue: Optional[float] = None
    cost_of_revenue: Optional[float] = None
    market_cap: Optional[float] = None
    revenue_reciente: Optional[float] = None
    revenue_antiguo: Optional[float] = None
    net_income_reciente: Optional[float] = None
    net_income_antiguo: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    price_avg_50: Optional[float] = None
    price_avg_200: Optional[float] = None
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
# 4ª forma (Decisión de diseño #1 de SDD_explicacion_paso_a_paso.md) --
# mutuamente excluyente con `_CALLBACK_LEAF_RE`: el segmento ":p:" siempre
# contiene un ":" (p. ej. "p:alz"), que rompe el patrón `[a-z]{2,4}$` de
# `_CALLBACK_LEAF_RE` (confirmado por `security`, sección "Confirmaciones").
_CALLBACK_PASO_A_PASO_RE = re.compile(r"^xp:([0-9a-f]{8}):p:([a-z]{2,4})$")

_ALL_CATEGORY_CODES = frozenset(ai_explain_content.CATEGORIES_TEXTO_LIBRE) | frozenset(
    ai_explain_content.CATEGORIES_AVANZADO
)
_ALL_QUESTIONS_COMBINED: dict[str, ai_explain_content.QuestionSpec] = {
    **ai_explain_content.QUESTIONS_TEXTO_LIBRE,
    **ai_explain_content.QUESTIONS_AVANZADO,
}


# --- Sanitización de sector/industry (hallazgo 1 BLOQUEANTE de `security`,
# spec anterior — sin cambios) -----------------------------------------
#
# Hoy `datos_del_contexto` no tiene NINGÚN campo de texto libre no
# sanitizado (todo lo demás son enums cerrados, numéricos o filtrados por
# `_validated_*`). Si en el futuro se agrega un campo de texto libre nuevo
# (ej. una descripción de FMP sin allowlist) a `datos_del_contexto`, hay que
# validarlo ANTES de sumarlo al payload: viaja a Google (CS-4).

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


# CS-4 (SDD_migracion_gemini.md, 13.2 E-6/E-8): `peers_usados` viene de
# Finnhub sin validar y `perfil` de la base; ambos viajan a Google en el
# payload, así que se filtran igual que `sector`. `re.fullmatch`, nunca `$`.
_PEER_TICKER_RE = re.compile(r"[A-Z0-9]{1,6}(?:[.\-][A-Z0-9]{1,2})?")
_MAX_PEERS_PAYLOAD = 10


def _validated_peers(peers) -> list[str]:
    """Conserva en orden los `str` que cumplen `_PEER_TICKER_RE`, sin
    duplicados, máximo 10; lo que no cumple se descarta."""
    if not isinstance(peers, (list, tuple)):
        return []
    validos: list[str] = []
    for peer in peers:
        if (
            isinstance(peer, str)
            and _PEER_TICKER_RE.fullmatch(peer)
            and peer not in validos
        ):
            validos.append(peer)
            if len(validos) == _MAX_PEERS_PAYLOAD:
                break
    return validos


def _validated_perfil(perfil) -> str:
    if isinstance(perfil, str) and perfil in db.PERFILES_VALIDOS:
        return perfil
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
    if question_code in ("gra", "mul"):
        campo = {"gra": "valor_justo_graham", "mul": "valor_justo_multiplos"}[question_code]
        modelo = {"gra": "Graham (EPS)", "mul": "Múltiplos"}[question_code]
        esc_elegido = scenarios.get(context.escenario_elegido) or {}
        payload = {
            "modelo": modelo,
            "escenario_elegido": context.escenario_elegido,
            "pesimista": (scenarios.get("pesimista") or {}).get(campo),
            "conservador": (scenarios.get("conservador") or {}).get(campo),
            "optimista": (scenarios.get("optimista") or {}).get(campo),
        }
        if question_code == "gra":
            payload["eps_ttm"] = context.eps_ttm
            payload["y_value"] = context.y_value
            payload["g_aplicado"] = esc_elegido.get("graham_g_aplicado")
        else:  # mul
            pc = context.peer_comparison or {}
            payload["eps_ttm"] = context.eps_ttm
            payload["per_promedio_peers"] = pc.get("per_promedio_peers")
        return payload
    if question_code == "dcf":
        esc_elegido = scenarios.get(context.escenario_elegido) or {}
        return {
            "modelo": "DCF",
            "escenario_elegido": context.escenario_elegido,
            "pesimista": (scenarios.get("pesimista") or {}).get("valor_justo_dcf"),
            "conservador": (scenarios.get("conservador") or {}).get("valor_justo_dcf"),
            "optimista": (scenarios.get("optimista") or {}).get("valor_justo_dcf"),
            "dcf_wacc": esc_elegido.get("dcf_wacc"),
            "dcf_g_fcf": esc_elegido.get("dcf_g_fcf"),
            "dcf_fcf_base": esc_elegido.get("dcf_fcf_base"),
            "dcf_valor_presente_flujos": esc_elegido.get("dcf_valor_presente_flujos"),
            "dcf_valor_terminal_descontado": esc_elegido.get("dcf_valor_terminal_descontado"),
            "dcf_equity_value": esc_elegido.get("dcf_equity_value"),
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
            "current_assets": context.current_assets,
            "current_liabilities": context.current_liabilities,
            "revenue": context.revenue,
            "cost_of_revenue": context.cost_of_revenue,
            "market_cap": context.market_cap,
            "eps_ttm": context.eps_ttm,
            "precio_actual": context.precio_actual,
        }
    if question_code == "pil":
        ratios = context.ratios or {}
        return {
            "modelo": _MODELO_PIL,
            "pillars": context.pillars,
            "total_pilares": len(context.pillars) if context.pillars else 4,
            "revenue_reciente": context.revenue_reciente,
            "revenue_antiguo": context.revenue_antiguo,
            "net_income_reciente": context.net_income_reciente,
            "net_income_antiguo": context.net_income_antiguo,
            "ratio_liquidez": ratios.get("ratio_liquidez"),
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
        risk_fit_d = context.risk_fit or {}
        return {
            "modelo": "Encaje con tu perfil de riesgo",
            "encaja": risk_fit_d.get("encaja"),
            "perfil": _validated_perfil(risk_fit_d.get("perfil")),
            "beta": risk_fit_d.get("beta"),
            "etiqueta_activo": risk_fit_d.get("etiqueta_activo"),
            "beta_umbral_bajo": risk_fit.BETA_UMBRAL_BAJO,
            "beta_umbral_alto": risk_fit.BETA_UMBRAL_ALTO,
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
            "precio_actual": context.precio_actual,
            "year_high": context.year_high,
            "year_low": context.year_low,
            "price_avg_50": context.price_avg_50,
            "price_avg_200": context.price_avg_200,
        }
    if question_code == "cmp":
        pc = context.peer_comparison or {}
        return {
            "modelo": "Comparables del sector",
            "per_propio": pc.get("per_propio"),
            "per_minimo_peers": pc.get("per_minimo_peers"),
            "per_promedio_peers": pc.get("per_promedio_peers"),
            "per_maximo_peers": pc.get("per_maximo_peers"),
            "peers_usados": _validated_peers(pc.get("peers_usados")),
            "posicion": pc.get("posicion"),
            "motivo_no_comparable": pc.get("motivo_no_comparable"),
            "eps_ttm": context.eps_ttm,
            "precio_actual": context.precio_actual,
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
            "ebit": magic.get("ebit"),
            "capital_invertido": magic.get("capital_invertido"),
            # SDD_desglose_con_valores_reales.md, Decisión de diseño #1 --
            # componentes de `capital_invertido` expuestos por separado para
            # el Desglose, sin cambiar la fórmula ya validada.
            "current_assets": magic.get("current_assets"),
            "current_liabilities": magic.get("current_liabilities"),
            "ppe_net": magic.get("ppe_net"),
        }
    if question_code == "mge":
        magic = context.magic or {}
        return {
            "modelo": "Earnings Yield (Magic Formula)",
            "earnings_yield": magic.get("earnings_yield"),
            "disponible": magic.get("disponible"),
            "ebit": magic.get("ebit"),
            "ev": magic.get("ev"),
            "market_cap": magic.get("market_cap"),
            "total_debt": magic.get("total_debt"),
            "cash": magic.get("cash"),
        }
    if question_code == "aqv":
        factors = context.factors or {}
        magic = context.magic or {}
        umbral_alto, umbral_bajo = advanced_scoring.FACTOR_UMBRALES["value_earnings_yield"]
        return {
            "modelo": "Factor Value (AQR)",
            "value": factors.get("value"),
            "earnings_yield": magic.get("earnings_yield"),
            "umbral_alto": umbral_alto,
            "umbral_bajo": umbral_bajo,
        }
    if question_code == "aqq":
        # Hallazgo señalado explícitamente por `architect` en el Handoff a
        # `security` — superficie mínima dedicada: SOLO quality/roe/
        # gross_margin/piotroski_ratio (+ umbrales, Decisión de diseño #3),
        # nunca el resto del contexto.
        factors = context.factors or {}
        piotroski_ratio = None
        evaluables = piotroski.get("criterios_evaluables")
        if evaluables:
            piotroski_ratio = (piotroski.get("puntaje") or 0) / evaluables
        roe_alto, roe_bajo = advanced_scoring.FACTOR_UMBRALES["quality_roe"]
        gm_alto, gm_bajo = advanced_scoring.FACTOR_UMBRALES["quality_gross_margin"]
        pr_alto, pr_bajo = advanced_scoring.FACTOR_UMBRALES["quality_piotroski_ratio"]
        return {
            "modelo": "Factor Quality (AQR)",
            "quality": factors.get("quality"),
            "roe": context.roe,
            "gross_margin": context.gross_margin,
            "piotroski_ratio": piotroski_ratio,
            "roe_umbral_alto": roe_alto,
            "roe_umbral_bajo": roe_bajo,
            "gross_margin_umbral_alto": gm_alto,
            "gross_margin_umbral_bajo": gm_bajo,
            "piotroski_ratio_umbral_alto": pr_alto,
            "piotroski_ratio_umbral_bajo": pr_bajo,
        }
    if question_code == "aqm":
        # SDD_desglose_universal.md, Grupo F, Cambio 3 -- superficie mínima
        # (Decisión de diseño #11 original, sin aflojar): solo los 3 campos
        # que determinan la etiqueta, no `year_high`/`year_low`/`pct_vs_*`.
        factors = context.factors or {}
        return {
            "modelo": "Factor Momentum (AQR)",
            "momentum": factors.get("momentum"),
            "precio_actual": context.precio_actual,
            "price_avg_50": context.price_avg_50,
            "price_avg_200": context.price_avg_200,
        }
    if question_code == "aql":
        factors = context.factors or {}
        return {
            "modelo": "Factor Low-vol (AQR)",
            "low_vol": factors.get("low_vol"),
            "beta": context.beta,
            "beta_umbral_bajo": advanced_scoring.LOW_VOL_BETA_UMBRAL_BAJO,
            "beta_umbral_alto": advanced_scoring.LOW_VOL_BETA_UMBRAL_ALTO,
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
# mismo payload ya armado para Gemini, nunca recalculado) -------------------


def _fmt_money_or_nd(value: object) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


def _fmt_pct_or_nd(value: object) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


def _fmt_pp_or_nd(value: object) -> str:
    """Como `_fmt_pct_or_nd` pero para valores que YA están en puntos
    porcentuales (p.ej. `pct_vs_*` de `market_context._pct_vs`): no multiplica."""
    return f"{value:.1f}%" if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/D"


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
        return f"Momentum: {etiqueta} (vs. máx 52 sem.: {_fmt_pp_or_nd(datos.get('pct_vs_year_high'))})"
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


def _build_leaf_message(
    dato_line: str,
    respuesta: str,
    formula: Optional[str],
    fuente: Optional[str],
    *,
    cuenta: Optional[str] = None,
    desglose: Optional[str] = None,
    grafico: Optional[str] = None,
) -> str:
    """Orden fijo (Decisión de diseño #5, extendido por SDD_explicacion_
    paso_a_paso.md y por SDD_desglose_terminos_formula.md): header -> Dato ->
    Cuenta (si está presente) -> Desglose (si está presente, Decisión de
    diseño #2 de esa spec: sección nueva, no reemplaza Cuenta) -> respuesta
    de Gemini -> Fórmula/Fuente (si existen) -> disclaimer. Fórmula/Fuente/
    Cuenta/Desglose NUNCA pasan por el guard de integridad tal cual (Cuenta
    SÍ entra a `datos_del_contexto` -- y por lo tanto a `datos_tokens` --
    ANTES de llamar a Gemini, Decisión de diseño #4 de la spec anterior;
    Desglose, igual que Fórmula/Fuente, se arma y se inserta en el texto
    final DESPUÉS de la respuesta de Gemini, nunca antes ni mezclado con el
    prompt -- Decisión de diseño #4/Revisión de seguridad de
    SDD_desglose_terminos_formula.md)."""
    partes = [ai_rewrite.TRANSPARENCY_USED, f"📌 Dato: {dato_line}"]
    if cuenta:
        partes.append(f"🧮 Cuenta: {cuenta}")
    if grafico:
        partes.append(grafico)
    if desglose:
        partes.append(desglose)
    partes.append(respuesta)
    formula_fuente_lines = []
    if formula:
        formula_fuente_lines.append(f"📐 Fórmula: {formula}")
    if fuente:
        formula_fuente_lines.append(f"📊 Fuente del dato: {fuente}")
    if formula_fuente_lines:
        partes.append("\n".join(formula_fuente_lines))
    partes.append(summary.DISCLAIMER_NO_ASESORAMIENTO)
    return "\n\n".join(partes)


def _build_ver_dato_content(context: ExplanationContext, question_code: str) -> str:
    """"Ver dato" (Decisión de diseño #9) — generalización de
    `DETERMINISTIC_PREFIX` + 📌 Dato + 📐 Fórmula/📊 Fuente (si existen) a las
    22 preguntas `dato_y_paso_a_paso`: nunca llama a Gemini, sin cuenta
    (exclusiva de "paso a paso"), sin disclaimer (no es contenido generado
    por IA)."""
    datos = _build_explain_payload(context, question_code)
    dato_line = _build_dato_line(context.kind, question_code, datos)
    formula = ai_explain_content.formulas(context.kind).get(question_code)
    fuente = ai_explain_content.fuentes(context.kind).get(question_code)
    partes = [DETERMINISTIC_PREFIX, f"📌 Dato: {dato_line}"]
    formula_fuente_lines = []
    if formula:
        formula_fuente_lines.append(f"📐 Fórmula: {formula}")
    if fuente:
        formula_fuente_lines.append(f"📊 Fuente del dato: {fuente}")
    if formula_fuente_lines:
        partes.append("\n".join(formula_fuente_lines))
    return "\n\n".join(partes)


# --- Bloque "🧮 Cuenta" (paso a paso) — Decisión de diseño #3/#4 -----------
# 100% Python, nunca por Gemini. Lee ÚNICAMENTE del `datos` ya armado por
# `_build_explain_payload` -- nunca recalcula nada, nunca vuelve a llamar a
# `advanced_scoring.py`/`valuation.py`. Si un campo necesario falta (modelo
# no calculable para ese ticker), devuelve `None` -- nunca arma un string
# con "None" visible.

_MAX_CUENTA_CHARS = 800  # antes 400 -- todas las Cuentas de dato_y_paso_a_paso son narración en
                          # prosa después de SDD_cuenta_narrada_universal.md (peor caso medido: 671,
                          # margen ~19%), salvo "vf" (fórmula corta, muy por debajo). Reemplaza a
                          # `_MAX_CUENTA_NARRADA_CHARS`, que se retira (dejó de ser una excepción).


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _ratio2(x: float) -> str:
    return f"{x:.2f}"


def _pct1(x: float) -> str:
    return f"{x * 100:.1f}%"


def _enforce_cuenta_length(cuenta: str, max_chars: int = _MAX_CUENTA_CHARS) -> Optional[str]:
    """`_MAX_CUENTA_CHARS=800` (Decisión de diseño #8, retomada como tope
    único por SDD_cuenta_narrada_universal.md Decisión #4). Mejora
    recomendada (c) de `security`: a diferencia de la prosa de Gemini, la
    "cuenta" es aritmética -- cortarla a mitad de un número mostraría un
    resultado incompleto y potencialmente engañoso. Si el límite se excede
    (no debería pasar con tickers reales, peor caso medido 671, margen
    ~19%), se omite el bloque completo (mismo tratamiento que "no
    calculable") en vez de truncar un número a la mitad."""
    if len(cuenta) > max_chars:
        logger.warning(
            "Cuenta de %d caracteres excede max_chars=%d -- bloque omitido",
            len(cuenta), max_chars,
        )
        return None
    return cuenta


# --- Bloque "📊 Gráfico" (barras Unicode) -- SDD_redaccion_gemini_iter2.md
# sección 6 + CS-27 + 18.6-A7. 100% Python, nunca por Gemini: se arma desde el
# `datos` CRUDO ya validado de `_dispatch_leaf` (sin store/chat_id/context_id),
# DESPUÉS de la respuesta del modelo, y NUNCA entra a `datos_del_contexto` ni a
# `datos_tokens`. Solo emite `█`, `░`, etiquetas constantes y salidas de
# `_money`. Sin red, sin FS, sin `parse_mode`.

_MAX_GRAFICO_CHARS = 400
_GRAFICO_ANCHO = 12
_GRAFICO_ETIQUETA_ANCHO = 15
_GRAFICO_ND = "n/d"


def _valor_grafico_valido(valor: object) -> bool:
    """CS-27(b): `type(v) in (int, float)` (excluye `bool`), finito y > 0."""
    try:
        return type(valor) in (int, float) and math.isfinite(valor) and valor > 0
    except OverflowError:  # int más grande que un float
        return False


def _bar(valor: object, maximo: object, ancho: int = _GRAFICO_ANCHO) -> str:
    """Barra `█`/`░` de `ancho` bloques, o el literal "n/d" si `valor` o
    `maximo` no cumplen CS-27(b). Half-up explícito (no `round`, que es
    half-even): `int(valor / maximo * ancho + 0.5)`, mínimo 1 bloque."""
    if not (_valor_grafico_valido(valor) and _valor_grafico_valido(maximo)):
        return _GRAFICO_ND
    proporcion = min(valor / maximo, 1.0)
    llenos = max(1, int(proporcion * ancho + 0.5))
    return "█" * llenos + "░" * (ancho - llenos)


def _bloque_grafico(titulo: str, filas: list[tuple[str, object]]) -> Optional[str]:
    """Escala común al máximo del conjunto. `None` si hay < 2 valores válidos
    o si el bloque supera `_MAX_GRAFICO_CHARS` (no se trunca)."""
    validos = [v for _, v in filas if _valor_grafico_valido(v)]
    if len(validos) < 2:
        return None
    maximo = max(validos)
    lineas = [titulo]
    for etiqueta, valor in filas:
        if _valor_grafico_valido(valor):
            lineas.append(f"{etiqueta.ljust(_GRAFICO_ETIQUETA_ANCHO)}{_bar(valor, maximo)}  {_money(valor)}")
        else:
            lineas.append(f"{etiqueta.ljust(_GRAFICO_ETIQUETA_ANCHO)}{_GRAFICO_ND}")
    bloque = "\n".join(lineas)
    if _len16(bloque) > _MAX_GRAFICO_CHARS:
        return None
    return bloque


def _build_grafico_vf(datos: dict) -> Optional[str]:
    """`vf`: Múltiplos, Graham, DCF, Valor Justo Total y precio actual."""
    try:
        return _bloque_grafico("📊 Valor justo vs precio", [
            ("Múltiplos", datos.get("valor_justo_multiplos")),
            ("Graham", datos.get("valor_justo_graham")),
            ("Flujo (DCF)", datos.get("valor_justo_dcf")),
            ("Valor Justo", datos.get("valor_justo_total")),
            ("Precio actual", datos.get("precio_actual")),
        ])
    except Exception as exc:
        logger.warning("GEMINI_GRAFICO_FALLO tipo=%s", type(exc).__name__)
        return None


def _build_grafico_escenarios(datos: dict) -> Optional[str]:
    """`gra`/`dcf`/`mul`: los 3 escenarios del modelo y el precio actual."""
    try:
        return _bloque_grafico("📊 Escenarios vs precio", [
            ("Pesimista", datos.get("pesimista")),
            ("Conservador", datos.get("conservador")),
            ("Optimista", datos.get("optimista")),
            ("Precio actual", datos.get("precio_actual")),
        ])
    except Exception as exc:
        logger.warning("GEMINI_GRAFICO_FALLO tipo=%s", type(exc).__name__)
        return None


def _build_grafico_ver(datos: dict) -> Optional[str]:
    """`ver`: Valor Justo Total vs precio actual (2 barras)."""
    try:
        return _bloque_grafico("📊 Valor justo vs precio", [
            ("Valor Justo", datos.get("valor_justo_total")),
            ("Precio actual", datos.get("precio_actual")),
        ])
    except Exception as exc:
        logger.warning("GEMINI_GRAFICO_FALLO tipo=%s", type(exc).__name__)
        return None


_GRAFICO_POR_PREGUNTA: dict[str, Callable[[dict], Optional[str]]] = {
    "vf": _build_grafico_vf,
    "ver": _build_grafico_ver,
    "gra": _build_grafico_escenarios,
    "dcf": _build_grafico_escenarios,
    "mul": _build_grafico_escenarios,
}


# --- Bloque "🔍 Desglose" (paso a paso) -- SDD_desglose_terminos_
# formula.md + SDD_desglose_con_valores_reales.md, Decisión de diseño
# #3/#4/#5. Texto fijo (nombre/origen/qué mide) + valor real puntual del
# ticker por línea, agregado en la spec de valores reales. Nunca por
# Gemini -- se arma DESPUÉS de `_fetch_explanation`, nunca es input suyo.

_MAX_DESGLOSE_CHARS = 1200

# SDD_desglose_con_valores_reales.md, Decisión de diseño #3 -- mapeo
# texto-de-letra (tal cual aparece en `DESGLOSE_AVANZADO`) -> clave interna
# del criterio de Piotroski, reutilizando las mismas 9 claves canónicas que
# `_PIOTROSKI_CUENTA_LABEL`. Vive acá (no en `ai_explain_content.py`) --
# esa fuente sigue siendo 100% texto fijo, sin lógica que dependa de `datos`.
_DESGLOSE_LETRA_A_NOMBRE_CRITERIO = {
    "ROA positivo": "roa_positivo",
    "CFO positivo": "cfo_positivo",
    "ROA creciente": "roa_creciente",
    "CFO > Utilidad": "cfo_mayor_utilidad",
    "Apalancamiento decreciente": "apalancamiento_decreciente",
    "Liquidez creciente": "liquidez_creciente",
    "Sin dilución": "sin_dilucion",
    "Margen bruto creciente": "margen_bruto_creciente",
    "Rotación de activos creciente": "rotacion_activos_creciente",
}


def _valor_desglose_alz(letra: str, datos: dict) -> Optional[str]:
    altman = datos.get("altman") or {}
    if not altman.get("disponible"):
        return None
    clave = {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}.get(letra)
    valor = altman.get(clave) if clave else None
    return _ratio2(valor) if valor is not None else None


def _valor_desglose_azp(letra: str, datos: dict) -> Optional[str]:
    altman_pp = datos.get("altman_pp") or {}
    if not altman_pp.get("disponible"):
        return None
    clave = {"A": "a", "B": "b", "C": "c", "D": "d"}.get(letra)
    valor = altman_pp.get(clave) if clave else None
    return _ratio2(valor) if valor is not None else None


def _valor_desglose_piotroski(letra: str, datos: dict) -> Optional[str]:
    nombre = _DESGLOSE_LETRA_A_NOMBRE_CRITERIO.get(letra)
    criterio = next(
        (c for c in (datos.get("criterios") or []) if isinstance(c, dict) and c.get("nombre") == nombre),
        None,
    )
    if criterio is None:
        return None
    cumplido = criterio.get("cumplido")
    if cumplido is None:
        return "➖ No evaluable"
    return "✅ Cumple" if cumplido else "❌ No cumple"


def _valor_desglose_mgr(letra: str, datos: dict) -> Optional[str]:
    if letra == "EBIT":
        v = datos.get("ebit")
        return _money(v) if v is not None else None
    if letra == "Capital de Trabajo Neto":
        ca, cl = datos.get("current_assets"), datos.get("current_liabilities")
        return _money(ca - cl) if ca is not None and cl is not None else None
    if letra == "Activos Fijos Netos":
        v = datos.get("ppe_net")
        return _money(v) if v is not None else None
    return None


def _valor_desglose_mge(letra: str, datos: dict) -> Optional[str]:
    campo = {
        "EBIT": "ebit", "Capitalización de Mercado": "market_cap",
        "Deuda Total": "total_debt", "Efectivo": "cash",
    }.get(letra)
    v = datos.get(campo) if campo else None
    return _money(v) if v is not None else None


# --- SDD_desglose_universal.md -- extractores de las 12 preguntas nuevas ---
# Mismo patrón genérico (1 valor por término, `try/except` amplio en
# `_build_desglose_block`) salvo lo documentado por grupo.


def _valor_desglose_gra(letra: str, datos: dict) -> Optional[str]:
    if letra == "EPS":
        v = datos.get("eps_ttm")
        return _money(v) if v is not None else None
    if letra == "g":
        v = datos.get("g_aplicado")
        return _pct1(v) if v is not None else None
    if letra == "Y":
        v = datos.get("y_value")
        return _pct1(v) if v is not None else None
    return None


def _valor_desglose_dcf(letra: str, datos: dict) -> Optional[str]:
    campo_fmt = {
        "FCF base": ("dcf_fcf_base", _money),
        "WACC": ("dcf_wacc", _pct1),
        "g": ("dcf_g_fcf", _pct1),
        "Valor presente de los flujos": ("dcf_valor_presente_flujos", _money),
        "Valor terminal descontado": ("dcf_valor_terminal_descontado", _money),
        "Valor de la empresa": ("dcf_equity_value", _money),
    }.get(letra)
    if campo_fmt is None:
        return None
    campo, fmt = campo_fmt
    v = datos.get(campo)
    return fmt(v) if v is not None else None


def _valor_desglose_mul(letra: str, datos: dict) -> Optional[str]:
    if letra == "EPS":
        v = datos.get("eps_ttm")
        return _money(v) if v is not None else None
    if letra == "PER promedio peers":
        v = datos.get("per_promedio_peers")
        return _ratio2(v) if v is not None else None
    return None


def _valor_desglose_rat(letra: str, datos: dict) -> Optional[str]:
    """Reusa exactamente las mismas condiciones que `_cuenta_rat` -- un
    término sin dato disponible se omite de la línea (PER omitido si
    `per_no_aplicable`, liquidez omitida si `current_liabilities` es
    0/`None`, mismo criterio que la Cuenta)."""
    if letra == "Liquidez":
        ca, cl = datos.get("current_assets"), datos.get("current_liabilities")
        if ca is None or not cl or datos.get("ratio_liquidez") is None:
            return None
        return _ratio2(datos["ratio_liquidez"])
    if letra == "Margen bruto":
        rev, cor = datos.get("revenue"), datos.get("cost_of_revenue")
        if not rev or cor is None or datos.get("margen_bruto") is None:
            return None
        return _pct1(datos["margen_bruto"])
    if letra == "PER":
        precio, eps = datos.get("precio_actual"), datos.get("eps_ttm")
        if (
            precio is None or not eps or datos.get("per") is None
            or datos.get("per_no_aplicable")
        ):
            return None
        return _ratio2(datos["per"])
    if letra == "P/S":
        mc, rev = datos.get("market_cap"), datos.get("revenue")
        if mc is None or not rev or datos.get("ps") is None:
            return None
        return _ratio2(datos["ps"])
    return None


def _valor_desglose_pil(letra: str, datos: dict) -> Optional[str]:
    """Mismo formato `"{a} > {b}"` que `_cuenta_pil` para "Ingresos
    crecientes"/"Utilidades crecientes" (consistencia Cuenta↔Desglose)."""
    pillars = datos.get("pillars") or {}
    if letra == "Ingresos crecientes":
        r, a = datos.get("revenue_reciente"), datos.get("revenue_antiguo")
        return f"{_money(r)} > {_money(a)}" if r is not None and a is not None else None
    if letra == "Utilidades crecientes":
        r, a = datos.get("net_income_reciente"), datos.get("net_income_antiguo")
        return f"{_money(r)} > {_money(a)}" if r is not None and a is not None else None
    if letra == "Deuda controlada":
        rl = datos.get("ratio_liquidez")
        return _ratio2(rl) if rl is not None else None
    if letra == "Precio razonable":
        v = pillars.get("precio_razonable")
        if v is None:
            return None
        return "✅ Cumple" if v else "❌ No cumple"
    return None


def _valor_desglose_rsk(letra: str, datos: dict) -> Optional[str]:
    if letra == "Beta":
        v = datos.get("beta")
        return _ratio2(v) if v is not None else None
    if letra == "Perfil de riesgo":
        v = datos.get("perfil")
        return str(v) if v else None
    return None


def _valor_desglose_mom(letra: str, datos: dict) -> Optional[str]:
    """Los `pct_vs_*` del payload de `mom` ya están en puntos porcentuales
    (`market_context._pct_vs` ya multiplica por 100) -- se formatean
    directamente, igual que `_cuenta_mom`, sin volver a pasar por `_pct1`
    (que multiplicaría por 100 una segunda vez)."""
    campo = {
        "vs. máx. 52 semanas": "pct_vs_year_high",
        "vs. mín. 52 semanas": "pct_vs_year_low",
        "vs. promedio 50 días": "pct_vs_avg_50",
        "vs. promedio 200 días": "pct_vs_avg_200",
    }.get(letra)
    v = datos.get(campo) if campo else None
    return f"{v:.1f}%" if v is not None else None


def _valor_desglose_cmp(letra: str, datos: dict) -> Optional[str]:
    if letra == "PER propio":
        v = datos.get("per_propio")
        return _ratio2(v) if v is not None else None
    if letra == "PER promedio peers":
        v = datos.get("per_promedio_peers")
        return _ratio2(v) if v is not None else None
    return None


def _valor_desglose_ver(letra: str, datos: dict) -> Optional[str]:
    """Patrón genérico de 1-valor-por-término (Grupo D) -- NO delega en
    `_build_desglose_vf`, `ver` no repite las sub-cuentas de "vf"."""
    if letra == "Precio actual":
        v = datos.get("precio_actual")
        return _money(v) if v is not None else None
    if letra == "Valor Justo Total":
        v = datos.get("valor_justo_total")
        return _money(v) if v is not None else None
    return None


def _valor_desglose_aqv(letra: str, datos: dict) -> Optional[str]:
    if letra == "Earnings Yield":
        v = datos.get("earnings_yield")
        return _pct1(v) if v is not None else None
    if letra == "Umbrales":
        ey, alto, bajo = datos.get("earnings_yield"), datos.get("umbral_alto"), datos.get("umbral_bajo")
        if None in (ey, alto, bajo):
            return None
        return _rango_pct(ey, alto, bajo)
    return None


def _valor_desglose_aqq(letra: str, datos: dict) -> Optional[str]:
    campo = {
        "ROE": "roe", "Margen bruto": "gross_margin", "Ratio de Piotroski": "piotroski_ratio",
    }.get(letra)
    v = datos.get(campo) if campo else None
    return _pct1(v) if v is not None else None


def _valor_desglose_aqm(letra: str, datos: dict) -> Optional[str]:
    """A diferencia de `mom`, acá se calcula el cociente directamente desde
    `precio_actual`/`price_avg_50`/`price_avg_200` (no reutiliza
    `context.momentum`, que trae `pct_vs_year_high/low` -- esos 2 campos NO
    participan de la clasificación de Momentum AQR, ver Grupo F de la
    spec)."""
    precio = datos.get("precio_actual")
    campo = {"vs. promedio 50 días": "price_avg_50", "vs. promedio 200 días": "price_avg_200"}.get(letra)
    avg = datos.get(campo) if campo else None
    if precio is None or not avg:
        return None
    return _pct1((precio - avg) / avg)


def _valor_desglose_aql(letra: str, datos: dict) -> Optional[str]:
    if letra == "Beta":
        v = datos.get("beta")
        return _ratio2(v) if v is not None else None
    return None


_DESGLOSE_VALOR_EXTRACTORS = {
    "alz": _valor_desglose_alz, "azp": _valor_desglose_azp,
    "pir": _valor_desglose_piotroski, "pia": _valor_desglose_piotroski,
    "pie": _valor_desglose_piotroski,
    "mgr": _valor_desglose_mgr, "mge": _valor_desglose_mge,
    "gra": _valor_desglose_gra, "dcf": _valor_desglose_dcf, "mul": _valor_desglose_mul,
    "rat": _valor_desglose_rat, "pil": _valor_desglose_pil, "rsk": _valor_desglose_rsk,
    "mom": _valor_desglose_mom, "cmp": _valor_desglose_cmp, "ver": _valor_desglose_ver,
    "aqv": _valor_desglose_aqv, "aqq": _valor_desglose_aqq, "aqm": _valor_desglose_aqm,
    "aql": _valor_desglose_aql,
}


def _enforce_desglose_length(bloque: str) -> Optional[str]:
    """`_MAX_DESGLOSE_CHARS=1200` (Decisión de diseño #6). Mismo criterio que
    `_enforce_cuenta_length`: si el bloque excede el tope, se omite el
    bloque COMPLETO (nunca se trunca a mitad de una línea) -- acá el único
    escenario que lo dispara es un error de edición futuro (una entrada de
    `DESGLOSE_AVANZADO` con descripciones demasiado largas), no un ticker
    con números grandes, porque el contenido es texto fijo."""
    if len(bloque) > _MAX_DESGLOSE_CHARS:
        logger.warning(
            "Desglose de %d caracteres excede _MAX_DESGLOSE_CHARS=%d -- bloque omitido",
            len(bloque), _MAX_DESGLOSE_CHARS,
        )
        return None
    return bloque


def _build_desglose_block(
    kind: str, question_code: str, datos: dict, context: Optional["ExplanationContext"] = None,
) -> Optional[str]:
    """Descripción fija (Decisión de diseño #3 de la spec original) + valor
    real puntual del ticker (SDD_desglose_con_valores_reales.md) por línea.
    No hace I/O. `None` si la pregunta no tiene desglose (20 de 27
    preguntas) -- comportamiento sin cambios. Un valor puntual faltante o un
    extractor que falla nunca le quita la línea a las demás letras, solo le
    quita el número a esa letra -- mismo `try/except` amplio que
    `_build_cuenta_line`.

    SDD_desglose_valor_justo_total.md [Iter-2]: caso especial de "vf" -- a
    diferencia del resto de las preguntas con Desglose, acá cada término
    necesita su propia cuenta resuelta completa, no solo 1 valor. Ese caso se
    delega a `_build_desglose_vf`, que recibe el `context` completo (opcional,
    `None` por default, retrocompatible con todos los call sites existentes
    que no lo pasan)."""
    if kind == "texto_libre" and question_code == "vf" and context is not None:
        return _build_desglose_vf(context, datos)
    terminos = ai_explain_content.desglose(kind, question_code)
    if not terminos:
        return None
    extractor = _DESGLOSE_VALOR_EXTRACTORS.get(question_code)
    lineas = []
    for t in terminos:
        valor = None
        if extractor is not None:
            try:
                valor = extractor(t.letra, datos)
            except Exception:  # noqa: BLE001 -- misma red de seguridad que _build_cuenta_line
                valor = None
        prefijo_valor = f" = {valor}" if valor else ""
        lineas.append(f"• {t.letra} ({t.nombre}){prefijo_valor} — sale de {t.campo_origen}. {t.que_mide}.")
    bloque = "🔍 Desglose:\n" + "\n".join(lineas)
    return _enforce_desglose_length(bloque)


_VF_SUB_MODELO_CODE = {"Múltiplos": "mul", "Graham": "gra", "DCF": "dcf"}


def _valor_desglose_vf_de_datos(letra: str, datos_vf: dict) -> Optional[str]:
    """Lee del `datos` de "vf" (`datos_vf`), nunca del `datos_sub` armado
    para la cuenta de cada sub-modelo -- el valor mostrado entre paréntesis
    tiene que ser exactamente el mismo número que ya muestra la Cuenta de
    "vf" (`_cuenta_vf`) para ese término (Decisión de diseño #3)."""
    campo = {
        "Múltiplos": "valor_justo_multiplos",
        "Graham": "valor_justo_graham",
        "DCF": "valor_justo_dcf",
    }.get(letra)
    v = datos_vf.get(campo) if campo else None
    return _money(v) if v is not None else None


def _build_desglose_vf(context: "ExplanationContext", datos_vf: dict) -> Optional[str]:
    """Caso especial de "vf" (SDD_desglose_valor_justo_total.md [Iter-2]): a
    diferencia del resto de las preguntas con Desglose (Altman/Piotroski/
    Magic Formula), acá cada término necesita su propia cuenta resuelta
    completa, no solo 1 valor -- y esa cuenta la arma una función que ya
    existe (`_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf`) pero que espera un
    `datos` propio de su pregunta, distinto del `datos` de "vf". Ese `datos`
    propio se arma con la MISMA `_payload_texto_libre(context, code)` que ya
    usa `_build_explain_payload` cuando el usuario toca el botón individual
    -- no se inventa ninguna fuente de dato nueva, y ese `datos` intermedio
    NUNCA se mezcla con `datos_del_contexto` (el que ve Gemini para "vf") --
    se descarta apenas se usa para armar el texto del Desglose, que se
    inserta DESPUÉS de la respuesta de Gemini, igual que el resto del
    mecanismo."""
    terminos = ai_explain_content.desglose("texto_libre", "vf")
    if not terminos:
        return None
    lineas = []
    for t in terminos:
        code = _VF_SUB_MODELO_CODE[t.letra]
        try:
            datos_sub = _payload_texto_libre(context, code)
            cuenta_sub = _VF_SUB_MODELO_CUENTA[code](datos_sub)
        except Exception:  # noqa: BLE001 -- misma red de seguridad que _build_cuenta_line
            cuenta_sub = None
        if cuenta_sub is None:
            lineas.append(f"• {t.nombre} — no calculable con los datos disponibles.")
            continue
        valor = _valor_desglose_vf_de_datos(t.letra, datos_vf)
        prefijo_valor = f" ({valor})" if valor else ""
        lineas.append(
            f"• {t.nombre}{prefijo_valor} — {t.que_mide}.\n  Cuenta: {cuenta_sub}"
        )
    bloque = "🔍 Desglose:\n" + "\n".join(lineas)
    return _enforce_desglose_length(bloque)


def _cuenta_ver(datos: dict) -> Optional[str]:
    precio = datos.get("precio_actual")
    total = datos.get("valor_justo_total")
    veredicto = datos.get("veredicto_barata")
    precio_ok, total_ok = precio is not None, total is not None
    if not precio_ok and not total_ok:
        return None
    precio_txt = _money(precio) if precio_ok else "no disponible"
    total_txt = _money(total) if total_ok else "no disponible"
    if precio_ok and total_ok and veredicto is not None:
        op = "<" if veredicto else ">"
        etiqueta = "Barata" if veredicto else "Cara"
        resultado = f"Precio actual {precio_txt} {op} Valor Justo Total {total_txt} → {etiqueta}"
    else:
        resultado = "no se puede calcular sin el dato faltante"
    return (
        f"Para saber si esta acción está barata o cara, el modelo hace 1 paso: "
        f"compara el precio de hoy contra el Valor Justo Total que combina los 3 "
        f"modelos (Múltiplos, Graham y DCF) — {resultado}."
    )


def _cuenta_vf(datos: dict) -> Optional[str]:
    valores = [
        v for v in (
            datos.get("valor_justo_multiplos"), datos.get("valor_justo_graham"), datos.get("valor_justo_dcf"),
        ) if v is not None
    ]
    total = datos.get("valor_justo_total")
    if not valores or total is None:
        return None
    terms = " + ".join(_money(v) for v in valores)
    return f"({terms}) / {len(valores)} = {_money(total)}"


def _valor_escenario_elegido(datos: dict) -> Optional[float]:
    esc = datos.get("escenario_elegido")
    if esc not in ("pesimista", "conservador", "optimista"):
        return None
    return datos.get(esc)


def _cuenta_gra(datos: dict) -> Optional[str]:
    """Narración completa de 3 pasos con "dato faltante por paso"
    (SDD_cuenta_narrada_universal.md, retrofit de Decisión #2/#3 Grupo C --
    reemplaza el todo-o-nada de SDD_cuenta_narrada_graham_dcf.md). El "techo
    del 15%" está escrito como texto fijo (no `valuation.GRAHAM_G_CAP * 100`):
    si esa constante cambia de valor en `valuation.py`, este texto debe
    actualizarse junto con ella."""
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    eps_ok, g_ok, y_ok = eps is not None, g is not None, (y is not None and y != 0)
    if not any((eps_ok, g_ok, y_ok)):
        return None
    valor = _valor_escenario_elegido(datos) if (eps_ok and g_ok and y_ok) else None
    eps_txt = _money(eps) if eps_ok else "no disponible"
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disponible"
    y_txt = f"{y * 100:.1f}%" if y_ok else "no disponible"
    resultado = _money(valor) if valor is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber cuánto vale la acción según este modelo clásico, el cálculo sigue 3 pasos: "
        f"1) toma cuánto ganó la empresa por acción en el último año ({eps_txt} de EPS), "
        f"2) proyecta cuánto puede crecer esa ganancia a futuro usando el crecimiento histórico "
        f"de sus ganancias, con un techo del 15% para no ser demasiado optimista (en este caso, "
        f"{g_txt}), y 3) multiplica todo por un factor fijo de la fórmula (8.5 + "
        f"2×crecimiento) y lo divide entre la tasa del bono del Tesoro a 10 años ({y_txt}) "
        f"— cuanto más alta esa tasa \"sin riesgo\", menor el valor justo, porque hay una "
        f"alternativa más segura disponible. El resultado: {resultado} por acción."
    )


def _cuenta_gra_corta(datos: dict) -> Optional[str]:
    """Versión corta con flechas, usada solo dentro de "vf" -- mismo
    retrofit de 3 reglas que `_cuenta_gra` (Decisión #6), impacto de
    presupuesto despreciable en "vf" (ya verificado por `architect`)."""
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    eps_ok, g_ok, y_ok = eps is not None, g is not None, (y is not None and y != 0)
    if not any((eps_ok, g_ok, y_ok)):
        return None
    valor = _valor_escenario_elegido(datos) if (eps_ok and g_ok and y_ok) else None
    eps_txt = _money(eps) if eps_ok else "no disp."
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disp."
    y_txt = f"{y * 100:.1f}%" if y_ok else "no disp."
    resultado = _money(valor) if valor is not None else "no calculable"
    return (
        f"EPS {eps_txt} → se proyecta con {g_txt} de crecimiento (techo 15%) → se "
        f"multiplica por 8.5+2×crecimiento y se ajusta por la tasa del bono a 10 años "
        f"({y_txt}, a mayor tasa, menor valor) = {resultado}."
    )


def _cuenta_mul(datos: dict) -> Optional[str]:
    """Narración completa de 2 pasos con "dato faltante por paso"
    (SDD_cuenta_narrada_universal.md, Decisión #3 Grupo B) -- reemplaza la
    fórmula comprimida de 1 línea que tenía hoy. La versión comprimida se
    conserva sin cambios en `_cuenta_mul_corta`, para el anidado en "vf"."""
    eps, per = datos.get("eps_ttm"), datos.get("per_promedio_peers")
    eps_ok, per_ok = eps is not None, per is not None
    if not eps_ok and not per_ok:
        return None
    valor = _valor_escenario_elegido(datos)
    p1 = _money(eps) if eps_ok else "dato de EPS no disponible"
    p2 = _ratio2(per) if per_ok else "dato de PER promedio de comparables no disponible"
    resultado = _money(valor) if (eps_ok and per_ok and valor is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"Para estimar el valor por este método, el modelo hace 2 pasos: "
        f"1) toma cuánto ganó la empresa por acción en el último año ({p1}), "
        f"y 2) lo multiplica por el PER promedio de empresas parecidas del mismo sector "
        f"({p2}) — así estima cuánto debería valer la acción si cotizara a un múltiplo "
        f"similar al de sus comparables. El resultado: {resultado} por acción."
    )


def _cuenta_mul_corta(datos: dict) -> Optional[str]:
    """Versión corta -- misma fórmula comprimida que `_cuenta_mul` tenía HOY,
    conservada tal cual para el anidado en "vf" (mismo criterio que
    `_cuenta_gra_corta`/`_cuenta_dcf_corta`: la narración completa nueva es
    demasiado larga para el presupuesto de `_MAX_DESGLOSE_CHARS=1200`)."""
    eps, per = datos.get("eps_ttm"), datos.get("per_promedio_peers")
    valor = _valor_escenario_elegido(datos)
    if None in (eps, per, valor):
        return None
    return f"{_money(eps)} × {_ratio2(per)} = {_money(valor)}"


def _cuenta_dcf(datos: dict) -> Optional[str]:
    """Narración completa de 4 pasos con "dato faltante por paso"
    (SDD_cuenta_narrada_universal.md, retrofit de Decisión #2/#3 Grupo D).
    `vp_flujos`/`vt_desc`/`equity` son resultados intermedios sin "paso
    texto" propio (ver Nota de diseño de la spec) -- solo participan de la
    guarda del resultado final."""
    wacc = datos.get("dcf_wacc")
    g = datos.get("dcf_g_fcf")
    base = datos.get("dcf_fcf_base")
    vp_flujos = datos.get("dcf_valor_presente_flujos")
    vt_desc = datos.get("dcf_valor_terminal_descontado")
    equity = datos.get("dcf_equity_value")
    base_ok, g_ok, wacc_ok = base is not None, g is not None, wacc is not None
    vp_ok, vt_ok, eq_ok = vp_flujos is not None, vt_desc is not None, equity is not None
    if not any((base_ok, g_ok, wacc_ok, vp_ok, vt_ok, eq_ok)):
        return None
    todos_ok = base_ok and g_ok and wacc_ok and vp_ok and vt_ok and eq_ok
    valor_accion = _valor_escenario_elegido(datos) if todos_ok else None
    years = valuation.DCF_PROJECTION_YEARS
    base_txt = _money(base) if base_ok else "no disponible"
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disponible"
    wacc_txt = f"{wacc * 100:.1f}%" if wacc_ok else "no disponible"
    resultado = _money(valor_accion) if valor_accion is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber cuánto vale la empresa hoy, el modelo hace 4 pasos: 1) toma cuánto efectivo "
        f"libre genera HOY el negocio ({base_txt}), 2) asume que va a crecer {g_txt} "
        f"por año durante {years} años, 3) \"trae\" cada uno de esos años futuros a su valor de "
        f"HOY (porque un peso dentro de {years} años vale menos que uno hoy — se descuenta al "
        f"{wacc_txt}, el costo de capital), y 4) le suma un \"valor terminal\" (lo que "
        f"vale seguir generando plata para siempre después del año {years}). Todo eso sumado y "
        f"dividido entre las acciones da {resultado} por acción."
    )


def _cuenta_dcf_corta(datos: dict) -> Optional[str]:
    """Versión corta con flechas, usada solo dentro de "vf" -- mismo
    retrofit de 3 reglas que `_cuenta_dcf` (Decisión #6)."""
    wacc = datos.get("dcf_wacc")
    g = datos.get("dcf_g_fcf")
    base = datos.get("dcf_fcf_base")
    vp_flujos = datos.get("dcf_valor_presente_flujos")
    vt_desc = datos.get("dcf_valor_terminal_descontado")
    equity = datos.get("dcf_equity_value")
    base_ok, g_ok, wacc_ok = base is not None, g is not None, wacc is not None
    vp_ok, vt_ok, eq_ok = vp_flujos is not None, vt_desc is not None, equity is not None
    if not any((base_ok, g_ok, wacc_ok, vp_ok, vt_ok, eq_ok)):
        return None
    todos_ok = base_ok and g_ok and wacc_ok and vp_ok and vt_ok and eq_ok
    valor_accion = _valor_escenario_elegido(datos) if todos_ok else None
    years = valuation.DCF_PROJECTION_YEARS
    base_txt = _money(base) if base_ok else "no disp."
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disp."
    wacc_txt = f"{wacc * 100:.1f}%" if wacc_ok else "no disp."
    resultado = _money(valor_accion) if valor_accion is not None else "no calculable"
    return (
        f"FCF hoy {base_txt} → crece {g_txt}/año durante {years} años → se descuentan "
        f"esos flujos futuros a valor de hoy al {wacc_txt} (costo de capital) → se suma el "
        f"valor de seguir generando caja después del año {years} = {resultado} por "
        f"acción."
    )


def _cuenta_rat(datos: dict) -> Optional[str]:
    ca, cl = datos.get("current_assets"), datos.get("current_liabilities")
    liq_ok = ca is not None and cl and datos.get("ratio_liquidez") is not None
    rev, cor = datos.get("revenue"), datos.get("cost_of_revenue")
    mb_ok = rev and cor is not None and datos.get("margen_bruto") is not None
    precio, eps = datos.get("precio_actual"), datos.get("eps_ttm")
    per_ok = precio is not None and eps and datos.get("per") is not None and not datos.get("per_no_aplicable")
    mc = datos.get("market_cap")
    ps_ok = mc is not None and rev and datos.get("ps") is not None
    if not any((liq_ok, mb_ok, per_ok, ps_ok)):
        return None
    liq_txt = f"{_money(ca)} entre {_money(cl)} = {_ratio2(datos['ratio_liquidez'])}" if liq_ok else "no disponible (falta activo o pasivo corriente)"
    mb_txt = f"({_money(rev)} − {_money(cor)}) sobre {_money(rev)} = {_pct1(datos['margen_bruto'])}" if mb_ok else "no disponible (falta ventas o costo de ventas)"
    per_txt = f"{_money(precio)} entre {_money(eps)} = {_ratio2(datos['per'])}" if per_ok else "no disponible o no aplica (empresa con pérdidas)"
    ps_txt = f"{_money(mc)} entre {_money(rev)} = {_ratio2(datos['ps'])}" if ps_ok else "no disponible (falta capitalización o ventas)"
    return (
        f"El modelo calcula hasta 4 ratios clave, cada uno con sus propios datos: "
        f"1) Liquidez corriente (activo corriente / pasivo corriente): {liq_txt}, "
        f"2) Margen bruto ((ventas − costo de ventas) / ventas): {mb_txt}, "
        f"3) PER (precio / ganancia por acción): {per_txt}, "
        f"4) P/S (capitalización / ventas): {ps_txt}."
    )


def _cuenta_pil(datos: dict) -> Optional[str]:
    pillars = datos.get("pillars") or {}
    rev_r, rev_a = datos.get("revenue_reciente"), datos.get("revenue_antiguo")
    ni_r, ni_a = datos.get("net_income_reciente"), datos.get("net_income_antiguo")
    ratio_liq = datos.get("ratio_liquidez")
    ing_ok = rev_r is not None and rev_a is not None and pillars.get("ingresos_crecientes") is not None
    util_ok = ni_r is not None and ni_a is not None and pillars.get("utilidades_crecientes") is not None
    deuda_ok = ratio_liq is not None and pillars.get("deuda_controlada") is not None
    precio_ok = pillars.get("precio_razonable") is not None
    if not any((ing_ok, util_ok, deuda_ok, precio_ok)):
        return None
    ing_txt = f"{_money(rev_r)} vs. {_money(rev_a)} → {'creciente' if pillars['ingresos_crecientes'] else 'no creciente'}" if ing_ok else "no disponible (falta historial de ventas)"
    util_txt = f"{_money(ni_r)} vs. {_money(ni_a)} → {'creciente' if pillars['utilidades_crecientes'] else 'no creciente'}" if util_ok else "no disponible (falta historial de utilidades)"
    deuda_txt = f"liquidez {_ratio2(ratio_liq)} → {'controlada' if pillars['deuda_controlada'] else 'no controlada'}" if deuda_ok else "no disponible (falta liquidez)"
    precio_txt = ("✅ razonable" if pillars["precio_razonable"] else "❌ no razonable") if precio_ok else "no disponible (falta Valor Justo Total)"
    return (
        f"El modelo evalúa 4 pilares de calidad, cada uno como sí/no: "
        f"1) ¿Ingresos crecientes? compara ventas recientes contra las más antiguas del historial: {ing_txt}. "
        f"2) ¿Utilidades crecientes? compara utilidad reciente contra la más antigua: {util_txt}. "
        f"3) ¿Deuda controlada? compara activo corriente contra pasivo corriente: {deuda_txt}. "
        f"4) ¿Precio razonable? compara el precio de hoy contra el Valor Justo Total: {precio_txt}."
    )


def _perfil_sugerido(beta: float, bajo: float, alto: float) -> str:
    """Perfil de riesgo IMPLICADO por la Beta de la acción (comparado contra
    los umbrales), no el perfil que el usuario eligió con `/start` -- mismo
    criterio que ya usaba `_cuenta_rsk` antes de esta spec, factorizado en un
    helper compartido con la narración nueva."""
    if beta < bajo:
        return "Muy Conservador / Conservador"
    if beta > alto:
        return "Agresivo"
    return "Moderado"


def _cuenta_rsk(datos: dict) -> Optional[str]:
    beta = datos.get("beta")
    bajo, alto = datos.get("beta_umbral_bajo"), datos.get("beta_umbral_alto")
    if beta is None and (bajo is None or alto is None):
        return None
    beta_txt = (
        f"{_ratio2(beta)}, que está {'por debajo de' if bajo is not None and beta < bajo else ('por encima de' if alto is not None and beta > alto else 'entre')} "
        f"{_ratio2(bajo) if bajo is not None else '?'} y {_ratio2(alto) if alto is not None else '?'}"
        if beta is not None and bajo is not None and alto is not None else "no disponible"
    )
    perfil = _perfil_sugerido(beta, bajo, alto) if beta is not None and bajo is not None and alto is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber si el riesgo de esta acción encaja con tu perfil, el modelo hace 2 pasos: "
        f"1) mide qué tan volátil es la acción comparada con el mercado en general (su Beta: {beta_txt}), "
        f"y 2) compara ese número contra los umbrales de tu perfil de riesgo elegido con /start "
        f"→ perfil sugerido: {perfil}."
    )


def _cuenta_mom(datos: dict) -> Optional[str]:
    precio = datos.get("precio_actual")
    refs = (
        ("máx. 52 sem.", datos.get("year_high"), datos.get("pct_vs_year_high")),
        ("mín. 52 sem.", datos.get("year_low"), datos.get("pct_vs_year_low")),
        ("promedio 50d", datos.get("price_avg_50"), datos.get("pct_vs_avg_50")),
        ("promedio 200d", datos.get("price_avg_200"), datos.get("pct_vs_avg_200")),
    )
    ok_flags = [precio is not None and ref_val is not None and pct_val is not None for _, ref_val, pct_val in refs]
    if not any(ok_flags):
        return None
    piezas = []
    for (label, ref_val, pct_val), ok in zip(refs, ok_flags):
        if ok:
            piezas.append(
                f"({_money(precio)} − {_money(ref_val)}) / {_money(ref_val)} × 100 = {pct_val:.1f}% vs. {label}"
            )
        else:
            piezas.append(f"vs. {label}: no disponible")
    return (
        f"El modelo compara el precio actual contra 4 referencias, cada una con sus propios datos: "
        + " · ".join(piezas) + "."
    )


def _cuenta_cmp(datos: dict) -> Optional[str]:
    precio, eps = datos.get("precio_actual"), datos.get("eps_ttm")
    per_propio, per_prom = datos.get("per_propio"), datos.get("per_promedio_peers")
    propio_ok = precio is not None and eps and per_propio is not None
    prom_ok = per_prom is not None
    if not propio_ok and not prom_ok:
        return None
    propio_txt = f"{_money(precio)} entre {_money(eps)} = {_ratio2(per_propio)}" if propio_ok else "no disponible (falta precio o EPS)"
    prom_txt = _ratio2(per_prom) if prom_ok else "no disponible (sin comparables con datos suficientes)"
    return (
        f"Para comparar esta acción con su sector, el modelo hace 2 pasos: "
        f"1) calcula el PER propio de la empresa (precio / ganancia por acción): {propio_txt}, "
        f"y 2) lo compara contra el PER promedio de empresas parecidas del mismo sector: {prom_txt}."
    )


# Dedicado a la versión anidada dentro de "vf" (SDD_cuenta_narrada_graham_
# dcf.md, Decisión #4) -- reusa `_cuenta_mul` tal cual, pero usa las
# versiones CORTAS de Graham/DCF (nunca la narración completa del botón
# individual) para que el bloque "🔍 Desglose:" de "vf" no exceda
# `_MAX_DESGLOSE_CHARS=1200`. `_CUENTA_TEXTO_LIBRE` (usado por el botón
# individual vía `_build_cuenta_line`) sigue apuntando a las versiones
# completas, sin cambios.
_VF_SUB_MODELO_CUENTA = {"mul": _cuenta_mul_corta, "gra": _cuenta_gra_corta, "dcf": _cuenta_dcf_corta}


_CUENTA_TEXTO_LIBRE = {
    "ver": _cuenta_ver, "vf": _cuenta_vf, "gra": _cuenta_gra, "dcf": _cuenta_dcf,
    "mul": _cuenta_mul, "rat": _cuenta_rat, "pil": _cuenta_pil, "rsk": _cuenta_rsk,
    "mom": _cuenta_mom, "cmp": _cuenta_cmp,
}


def _cuenta_alz(datos: dict) -> Optional[str]:
    altman = datos.get("altman") or {}
    if not altman.get("disponible"):
        return None
    a, b, c, d, e = (altman.get(k) for k in ("a", "b", "c", "d", "e"))
    labels_pesos = [
        ("Capital de Trabajo sobre Activos", a, 1.2),
        ("Utilidades Retenidas sobre Activos", b, 1.4),
        ("EBIT sobre Activos", c, 3.3),
        ("Capitalización de Mercado sobre Deuda", d, 0.6),
        ("Rotación de Activos (Ventas sobre Activos)", e, 1.0),
    ]
    if not any(v is not None for _, v, _ in labels_pesos):
        return None
    piezas = []
    for i, (label, val, peso) in enumerate(labels_pesos, start=1):
        piezas.append(f"{i}) {label} = {_ratio2(val)}, ×{peso} = {_ratio2(val * peso)}" if val is not None else f"{i}) {label}: no disponible")
    todos_ok = all(v is not None for _, v, _ in labels_pesos)
    z = altman.get("z")
    resultado = f"Z = {_ratio2(z)}" if todos_ok and z is not None else "Z no se puede calcular sin el dato faltante"
    return (
        f"El Altman Z-Score suma 5 factores financieros, cada uno multiplicado por un peso fijo "
        f"de la fórmula: " + ", ".join(piezas) + f". Sumando los 5 términos → {resultado}."
    )


def _cuenta_azp(datos: dict) -> Optional[str]:
    altman_pp = datos.get("altman_pp") or {}
    if not altman_pp.get("disponible"):
        return None
    a, b, c, d = (altman_pp.get(k) for k in ("a", "b", "c", "d"))
    labels_pesos = [
        ("Capital de Trabajo sobre Activos", a, 6.56),
        ("Utilidades Retenidas sobre Activos", b, 3.26),
        ("EBIT sobre Activos", c, 6.72),
        ("Capitalización de Mercado sobre Deuda", d, 1.05),
    ]
    if not any(v is not None for _, v, _ in labels_pesos):
        return None
    piezas = []
    for i, (label, val, peso) in enumerate(labels_pesos, start=1):
        piezas.append(f"{i}) {label} = {_ratio2(val)}, ×{peso} = {_ratio2(val * peso)}" if val is not None else f"{i}) {label}: no disponible")
    todos_ok = all(v is not None for _, v, _ in labels_pesos)
    z = altman_pp.get("z")
    resultado = f"Z'' = {_ratio2(z)}" if todos_ok and z is not None else "Z'' no se puede calcular sin el dato faltante"
    return (
        f"El Altman Z'' (variante asset-light) suma 4 factores financieros, cada uno multiplicado por un "
        f"peso fijo de la fórmula: " + ", ".join(piezas) + f". Sumando los 4 términos → {resultado}."
    )


def _cuenta_pig(datos: dict) -> Optional[str]:
    """`pig` (Grupo G) NO recibe el mecanismo de "dato faltante por paso" --
    decisión explícita de `architect`: es un conteo de criterios ya evaluados
    por `pir`/`pia`/`pie` (1 solo par de números que `advanced_scoring.py` ya
    calcula completo o no calcula), no una secuencia de pasos aritméticos con
    términos que puedan faltar por separado. Sigue con guarda todo-o-nada."""
    piotroski = datos.get("piotroski") or {}
    puntaje, evaluables = piotroski.get("puntaje"), piotroski.get("criterios_evaluables")
    if puntaje is None or not evaluables:
        return None
    return (
        f"El F-Score de Piotroski suma 1 punto por cada criterio de calidad contable que la "
        f"empresa cumple, evaluados en 3 grupos (rentabilidad, apalancamiento y liquidez, "
        f"eficiencia operativa) — de los {evaluables} criterios que se pudieron evaluar con los "
        f"datos disponibles de este ticker, cumplió {puntaje}. Cuantos más cumple, más sólida es "
        f"su calidad contable."
    )


_PIOTROSKI_CUENTA_LABEL = {
    "roa_positivo": "Ganancia Neta",
    "cfo_positivo": "CFO",
    "roa_creciente": "ROA",
    "cfo_mayor_utilidad": "CFO > Utilidad",
    "apalancamiento_decreciente": "Apalancamiento",
    "liquidez_creciente": "Liquidez",
    "sin_dilucion": "Acciones en circulación",
    "margen_bruto_creciente": "Margen bruto",
    "rotacion_activos_creciente": "Rotación de activos",
}


def _fmt_criterio_piotroski(criterio: dict) -> Optional[str]:
    """Retrofit `SDD_cuenta_narrada_universal.md`: un criterio con
    `cumplido is None`/`valores` vacío ya NO se descarta en silencio -- dice
    explícitamente "dato no disponible" (aparece en la lista unida por
    `_cuenta_piotroski_grupo`). Solo se sigue descartando (`None`) el caso de
    `nombre` desconocido -- eso es un bug de datos, no "falta info del
    ticker", y no tiene `label` para narrar."""
    nombre = criterio.get("nombre")
    label = _PIOTROSKI_CUENTA_LABEL.get(nombre)
    if label is None:
        return None  # nombre desconocido -- bug de datos, se sigue descartando
    cumplido, valores = criterio.get("cumplido"), criterio.get("valores")
    if cumplido is None or not valores:
        return f"{label}: dato no disponible"
    etiqueta = "cumplido" if cumplido else "no cumplido"
    if nombre == "roa_positivo":
        v = valores.get("net_income_t")
        return None if v is None else f"{label}: {_money(v)} > 0 → {etiqueta}"
    if nombre == "cfo_positivo":
        v = valores.get("cfo_t")
        return None if v is None else f"{label}: {_money(v)} > 0 → {etiqueta}"
    if nombre == "cfo_mayor_utilidad":
        cfo, ni = valores.get("cfo_t"), valores.get("net_income_t")
        if cfo is None or ni is None:
            return None
        return f"{label}: {_money(cfo)} > {_money(ni)} → {etiqueta}"
    if nombre == "roa_creciente":
        t, t1 = valores.get("roa_t"), valores.get("roa_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_ratio2(t)} > {_ratio2(t1)} → {etiqueta}"
    if nombre == "apalancamiento_decreciente":
        t, t1 = valores.get("apalancamiento_t"), valores.get("apalancamiento_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_ratio2(t)} < {_ratio2(t1)} → {etiqueta}"
    if nombre == "liquidez_creciente":
        t, t1 = valores.get("liquidez_t"), valores.get("liquidez_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_ratio2(t)} > {_ratio2(t1)} → {etiqueta}"
    if nombre == "sin_dilucion":
        t, t1 = valores.get("shares_t"), valores.get("shares_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_ratio2(t)} ≤ {_ratio2(t1)} → {etiqueta}"
    if nombre == "margen_bruto_creciente":
        t, t1 = valores.get("margen_t"), valores.get("margen_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_pct1(t)} > {_pct1(t1)} → {etiqueta}"
    if nombre == "rotacion_activos_creciente":
        t, t1 = valores.get("rotacion_t"), valores.get("rotacion_t1")
        if t is None or t1 is None:
            return None
        return f"{label}: {_ratio2(t)} > {_ratio2(t1)} → {etiqueta}"
    return None


def _cuenta_piotroski_grupo(datos: dict) -> Optional[str]:
    criterios = datos.get("criterios") or []
    piezas = [p for p in (_fmt_criterio_piotroski(c) for c in criterios if isinstance(c, dict)) if p]
    return " · ".join(piezas) if piezas else None


def _cuenta_mgr(datos: dict) -> Optional[str]:
    if not datos.get("disponible"):
        return None
    ebit, ci = datos.get("ebit"), datos.get("capital_invertido")
    ebit_ok, ci_ok = ebit is not None, ci is not None and ci != 0
    if not ebit_ok and not ci_ok:
        return None
    roic = datos.get("roic")
    ebit_txt = _money(ebit) if ebit_ok else "no disponible"
    ci_txt = _money(ci) if ci_ok else "no disponible"
    resultado = f"{_ratio2(roic)} = {_pct1(roic)}" if (ebit_ok and ci_ok and roic is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"El ranking Magic Formula ordena empresas por 2 factores; el de Retorno usa el ROIC, "
        f"en 2 pasos: 1) toma la ganancia operativa del negocio, antes de intereses e impuestos "
        f"({ebit_txt}), y 2) la divide entre el capital invertido (activos operativos menos "
        f"pasivos corrientes sin deuda) ({ci_txt}) → ROIC = {resultado}. Cuanto más alto, más "
        f"eficiente es la empresa generando ganancias con el capital que usa."
    )


def _cuenta_mge(datos: dict) -> Optional[str]:
    if not datos.get("disponible"):
        return None
    ebit, mc, td, cash, ey = (datos.get(k) for k in ("ebit", "market_cap", "total_debt", "cash", "earnings_yield"))
    ebit_ok, mc_ok, td_ok, cash_ok = ebit is not None, mc is not None, td is not None, cash is not None
    if not any((ebit_ok, mc_ok, td_ok, cash_ok)):
        return None
    todos_ok = ebit_ok and mc_ok and td_ok and cash_ok
    ebit_txt = _money(ebit) if ebit_ok else "no disponible"
    mc_txt = _money(mc) if mc_ok else "no disponible"
    td_txt = _money(td) if td_ok else "no disponible"
    cash_txt = _money(cash) if cash_ok else "no disponible"
    ev_txt = _money(mc + td - cash) if todos_ok else "no calculable"
    resultado = f"{_ratio2(ey)} = {_pct1(ey)}" if (todos_ok and ey is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"El ranking Magic Formula ordena empresas también por Earnings Yield, en 3 pasos: "
        f"1) toma la ganancia operativa del negocio ({ebit_txt}), "
        f"2) calcula el Valor de la Empresa (capitalización de mercado + deuda total − efectivo: "
        f"{mc_txt} + {td_txt} − {cash_txt} = {ev_txt}), "
        f"y 3) divide la ganancia operativa entre ese valor → Earnings Yield = {resultado}."
    )


def _rango_pct(valor: float, alto: float, bajo: float) -> str:
    """Compartido entre `_cuenta_aqv` y `_valor_desglose_aqv` (SDD_desglose_
    universal.md, Grupo E) -- misma lógica de rango, no se reescribe."""
    if valor > alto:
        return f"> {_pct1(alto)}"
    if valor < bajo:
        return f"< {_pct1(bajo)}"
    return f"entre {_pct1(bajo)} y {_pct1(alto)}"


def _cuenta_aqv(datos: dict) -> Optional[str]:
    ey = datos.get("earnings_yield")
    alto, bajo = datos.get("umbral_alto"), datos.get("umbral_bajo")
    ey_ok = ey is not None
    umb_ok = alto is not None and bajo is not None
    if not ey_ok and not umb_ok:
        return None
    ey_txt = _pct1(ey) if ey_ok else "no disponible"
    umb_txt = _rango_pct(ey, alto, bajo) if ey_ok and umb_ok else "no disponible"
    etiqueta = datos.get("value") if (ey_ok and umb_ok) else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Value (estilo AQR) hace 2 pasos: 1) calcula el Earnings Yield (mismo cálculo "
        f"que en la Magic Formula: EBIT sobre Valor de la Empresa) ({ey_txt}), y 2) lo compara "
        f"contra los umbrales fijos que separan valuaciones baratas/medias/caras dentro de este "
        f"análisis ({umb_txt}) → clasificación: {etiqueta}."
    )


def _puntos_umbral(valor: Optional[float], alto: Optional[float], bajo: Optional[float]) -> Optional[int]:
    if valor is None or alto is None or bajo is None:
        return None
    if valor > alto:
        return 1
    if valor < bajo:
        return -1
    return 0


def _cuenta_aqq(datos: dict) -> Optional[str]:
    sub_metricas = (
        ("ROE", datos.get("roe"), datos.get("roe_umbral_alto"), datos.get("roe_umbral_bajo"), _pct1),
        (
            "Margen bruto", datos.get("gross_margin"),
            datos.get("gross_margin_umbral_alto"), datos.get("gross_margin_umbral_bajo"), _pct1,
        ),
        (
            "Piotroski", datos.get("piotroski_ratio"),
            datos.get("piotroski_ratio_umbral_alto"), datos.get("piotroski_ratio_umbral_bajo"), _pct1,
        ),
    )
    ok_flags = [
        valor is not None and alto is not None and bajo is not None
        for _, valor, alto, bajo, _ in sub_metricas
    ]
    if not any(ok_flags):
        return None
    piezas = []
    suma = 0
    for (nombre, valor, alto, bajo, fmt), ok in zip(sub_metricas, ok_flags):
        if not ok:
            piezas.append(f"{nombre}: no disponible")
            continue
        p = _puntos_umbral(valor, alto, bajo)
        referencia = alto if p >= 0 else bajo
        signo = ">" if p == 1 else ("<" if p == -1 else "≈")
        piezas.append(f"{nombre} {fmt(valor)} {signo} {fmt(referencia)} ({p:+d})")
        suma += p
    todos_ok = all(ok_flags)
    etiqueta = datos.get("quality") if todos_ok else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Quality (estilo AQR) suma puntos por 3 sub-métricas, cada una comparada contra "
        f"umbrales fijos: " + " · ".join(piezas) + f" → suma {suma:+d} → {etiqueta}."
    )


def _cuenta_aqm(datos: dict) -> Optional[str]:
    """Retrofit `SDD_cuenta_narrada_universal.md`, Decisión #3 Grupo B --
    "dato faltante por paso" en vez del todo-o-nada que tenía desde
    `SDD_desglose_universal.md`."""
    precio = datos.get("precio_actual")
    avg50, avg200 = datos.get("price_avg_50"), datos.get("price_avg_200")
    p_ok, a50_ok, a200_ok = precio is not None, bool(avg50), bool(avg200)
    if not p_ok and not a50_ok and not a200_ok:
        return None
    avg50_txt = _money(avg50) if a50_ok else "no disponible"
    avg200_txt = _money(avg200) if a200_ok else "no disponible"
    precio_txt = _money(precio) if p_ok else "no disponible"
    etiqueta = datos.get("momentum") if (p_ok and a50_ok and a200_ok) else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Momentum (estilo AQR) hace 2 pasos: 1) compara el precio de hoy contra el "
        f"promedio de los últimos 50 días ({precio_txt} vs. {avg50_txt}), y 2) contra el promedio "
        f"de los últimos 200 días ({precio_txt} vs. {avg200_txt}) — si el precio está por encima "
        f"de ambos → impulso positivo. Clasificación: {etiqueta}."
    )


def _cuenta_aql(datos: dict) -> Optional[str]:
    beta = datos.get("beta")
    bajo, alto, etiqueta = datos.get("beta_umbral_bajo"), datos.get("beta_umbral_alto"), datos.get("low_vol")
    if beta is None and (bajo is None or alto is None):
        return None
    beta_txt = (
        f"{_ratio2(beta)}, que está {'por debajo de' if bajo is not None and beta < bajo else ('por encima de' if alto is not None and beta > alto else 'entre')} "
        f"{_ratio2(bajo) if bajo is not None else '?'} y {_ratio2(alto) if alto is not None else '?'}"
        if beta is not None and bajo is not None and alto is not None else "no disponible"
    )
    clasificacion = str(etiqueta) if beta is not None and bajo is not None and alto is not None else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Low-vol (estilo AQR) hace 2 pasos: "
        f"1) mide qué tan volátil es la acción comparada con el mercado en general (su Beta: {beta_txt}), "
        f"y 2) compara ese número contra los umbrales fijos que separan baja/media/alta volatilidad "
        f"dentro de este análisis → clasificación: {clasificacion}."
    )


_CUENTA_AVANZADO = {
    "alz": _cuenta_alz, "azp": _cuenta_azp, "pig": _cuenta_pig,
    "pir": _cuenta_piotroski_grupo, "pia": _cuenta_piotroski_grupo, "pie": _cuenta_piotroski_grupo,
    "mgr": _cuenta_mgr, "mge": _cuenta_mge, "aqv": _cuenta_aqv, "aqq": _cuenta_aqq,
    "aqm": _cuenta_aqm, "aql": _cuenta_aql,
}


def _build_cuenta_line(kind: str, question_code: str, datos: dict) -> Optional[str]:
    """Dispatch de las 22 preguntas `dato_y_paso_a_paso` -- función pura,
    `dict` de entrada -> `Optional[str]`, sin I/O (mismo criterio de
    testabilidad que `_build_dato_line`). Envuelto en un `try/except` amplio
    como red de seguridad adicional: cualquier error aritmético/de tipos
    inesperado se trata igual que "no calculable" (`None`), nunca deja
    escapar un string con un valor a medio calcular."""
    tabla = _CUENTA_TEXTO_LIBRE if kind == "texto_libre" else _CUENTA_AVANZADO
    fn = tabla.get(question_code)
    if fn is None:
        return None
    try:
        cuenta = fn(datos)
    except Exception:  # noqa: BLE001 -- red de seguridad amplia a propósito
        return None
    if not cuenta:
        return None
    return _enforce_cuenta_length(cuenta)


def _build_deterministic_content(context: ExplanationContext, question_code: str) -> str:
    """`evt`/`inf` — Decisión de diseño #4: nunca llaman a Gemini, contenido
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


# --- Prompts + redacción: etiquetas, normalizador y recorte (SDD Iter-2, F1) ---

# Encabezados de sección (16.2/18.6 A2). Sin dígitos, sin palabras en MAYÚSCULAS,
# sin ✅/❌ y sin emojis con ZWJ: pasan el guard de números y el filtro de salida.
# Los usan el prompt, el normalizador, `_fit_respuesta` y los tests.
SECTION_LABELS: dict[str, tuple[str, str, str]] = {
    "narrativa": ("💡 En simple", "🔎 Qué dicen los datos", "🧭 Cómo leerlo"),
    "paso_a_paso": ("💡 En simple", "🔎 Qué es cada pieza", "🧭 Qué significa el resultado"),
}
_ALL_LABELS: tuple[str, ...] = tuple(
    dict.fromkeys(etiqueta for trio in SECTION_LABELS.values() for etiqueta in trio)
)

# Reglas 3 y 4 son idénticas en ambos prompts (3.2: "idéntica a la regla 3/4 de
# EXPLAIN"); la regla 1 y las reglas 5/6 difieren.
_PROMPT_REGLA_3 = (
    "3. No escribas palabras enteras en MAYÚSCULAS ni siglas, salvo que\n"
    "   aparezcan tal cual en el JSON; si no, escribí el nombre completo en\n"
    "   palabras. No uses números ni listas numeradas para ordenar ideas:\n"
    "   usá viñetas con \"• \" y palabras como \"primero\" o \"después\".\n"
)
_PROMPT_REGLA_4 = (
    "4. Formato: texto plano. Nada de Markdown (ni asteriscos, ni guiones bajos,\n"
    "   ni numerales, ni tablas), ni enlaces, ni menciones con arroba. Nunca\n"
    "   escribas nombres de claves del JSON (los que llevan guion bajo): traducilos\n"
    "   a palabras normales. Tampoco uses emojis en el texto, salvo los encabezados.\n"
)
_PROMPT_COPIAR_VALORES = (
    "(por ejemplo \"$1,234.56\", \"15.3%\", \"$2.5 mil millones\"): copialos\n"
    "   exactamente como están, con su símbolo y sus decimales"
)
_PROMPT_CANTIDADES_CON_LETRAS = (
    " No escribas cantidades con letras (\"el doble\", \"la mitad\", \"diez por\n"
    "   ciento\"): comparar con palabras vagas sí, expresar una cantidad que no\n"
    "   está en el JSON no."
)

_PROMPT_EXPLAIN_PLANTILLA = (
    "Sos un profesor de finanzas que explica con claridad y calidez, en español rioplatense,\n"
    "por un chat de Telegram. Vas a recibir un JSON con una pregunta\n"
    "puntual y los datos ya calculados para responderla. Escribí como lo haría una\n"
    "persona que explica bien: frases completas y naturales, sin telegrafismo.\n\n"
    "Reglas, sin excepción:\n"
    "1. Integridad de los datos: usá ÚNICAMENTE las cifras y datos del JSON.\n"
    "   Nunca inventes, estimes ni calcules cifras nuevas (diferencias,\n"
    "   porcentajes, promedios, proyecciones, años, fechas). Para comparar usá\n"
    "   palabras (\"bastante por encima\", \"casi igual\") o citá cifras que ya\n"
    "   estén en el JSON. Los valores del JSON ya vienen escritos para leerse\n"
    "   " + _PROMPT_COPIAR_VALORES + ", sin cambiar los\n"
    "   separadores ni expresarlos de otra forma." + _PROMPT_CANTIDADES_CON_LETRAS + "\n"
    "2. Nunca des una recomendación de compra o venta ni asesoramiento\n"
    "   financiero personalizado: solo explicá qué significa el dato.\n"
    + _PROMPT_REGLA_3
    + _PROMPT_REGLA_4
    + "5. Nombrá el modelo financiero (\"modelo\" o \"modelos\" en el JSON) y decí en\n"
    "   general qué mide, con tono de análisis de inversionista, pero accesible\n"
    "   para quien no estudió finanzas. Una comparación cotidiana ayuda si es\n"
    "   corta.\n"
    "6. Estructura: tres bloques separados por una línea en blanco, cada uno\n"
    "   con su encabezado exacto en una línea:\n"
    "   {S1}\n"
    "   (qué mide el modelo y qué responde la pregunta)\n"
    "   {S2}\n"
    "   (las cifras clave del JSON y qué significa cada una; podés usar viñetas)\n"
    "   {S3}\n"
    "   (cómo interpretarlo, con matices y límites del modelo)\n"
    "   El largo es el que haga falta para que se entienda, sin relleno: no\n"
    "   repitas la pregunta, ni saludes, ni te despidas.\n"
    "7. Respondé siempre en español rioplatense, sin mezclar otros idiomas.\n"
)

_PROMPT_PASO_A_PASO_PLANTILLA = (
    "Sos un profesor de finanzas que explica con claridad y calidez, en español rioplatense,\n"
    "por un chat de Telegram. Vas a recibir un JSON con una pregunta\n"
    "puntual, los datos ya calculados y una clave \"cuenta\" con la fórmula YA\n"
    "RESUELTA paso a paso (números reales, cada término calculado, resultado\n"
    "final). El usuario ya ve esa cuenta arriba de tu respuesta.\n\n"
    "Reglas, sin excepción:\n"
    "1. La cuenta en \"cuenta\" YA ESTÁ CALCULADA Y ES CORRECTA: no la recalcules\n"
    "   ni la repitas entera. Usá ÚNICAMENTE las cifras y datos del JSON; nunca\n"
    "   inventes, estimes ni calcules cifras nuevas (diferencias, porcentajes,\n"
    "   promedios, proyecciones, años, fechas). Para comparar usá palabras o\n"
    "   citá cifras que ya estén en el JSON. Los valores vienen escritos para\n"
    "   leerse " + _PROMPT_COPIAR_VALORES + "." + _PROMPT_CANTIDADES_CON_LETRAS + "\n"
    "2. Nunca des una recomendación de compra o venta ni asesoramiento\n"
    "   financiero personalizado: solo explicá qué significa el resultado.\n"
    + _PROMPT_REGLA_3
    + _PROMPT_REGLA_4
    + "5. Nombrá el modelo financiero (\"modelo\" o \"modelos\" en el JSON) y decí\n"
    "   qué mide. Explicá QUÉ ES cada valor clave de la cuenta, uno por uno, y\n"
    "   no dejes ningún término sin explicar. Usá un lenguaje simple, con una\n"
    "   comparación cotidiana si ayuda (por ejemplo, pensar en la empresa como\n"
    "   una tienda de limonada).\n"
    "6. Estructura: tres bloques separados por una línea en blanco, cada uno\n"
    "   con su encabezado exacto en una línea:\n"
    "   {S1}\n"
    "   (qué mide el modelo, en lenguaje simple)\n"
    "   {S2}\n"
    "   (una viñeta \"• \" por cada término de la cuenta: \"Término (cifra): qué es\")\n"
    "   {S3}\n"
    "   (qué significa el resultado para quien lee, con sus límites)\n"
    "   El largo es el que haga falta; sin saludo, cierre ni repetir la pregunta.\n"
    "7. Respondé siempre en español rioplatense, sin mezclar otros idiomas.\n"
)


def _armar_prompt(plantilla: str, etiquetas: tuple[str, str, str]) -> str:
    # `str.replace`, no `.format` (la plantilla no escapa llaves). Se llama UNA
    # sola vez al importar (CA-41): `systemInstruction` es la constante del módulo.
    s1, s2, s3 = etiquetas
    return plantilla.replace("{S1}", s1).replace("{S2}", s2).replace("{S3}", s3)


SYSTEM_PROMPT_EXPLAIN = _armar_prompt(_PROMPT_EXPLAIN_PLANTILLA, SECTION_LABELS["narrativa"])

# Decisión de diseño #4 de SDD_explicacion_paso_a_paso.md -- NO reemplaza
# `SYSTEM_PROMPT_EXPLAIN` (ese sigue usándose para `mod`/`ben`/`ren`,
# variant="narrativa"): exclusivo del camino "paso a paso" de las 22
# preguntas `dato_y_paso_a_paso`.
SYSTEM_PROMPT_PASO_A_PASO = _armar_prompt(_PROMPT_PASO_A_PASO_PLANTILLA, SECTION_LABELS["paso_a_paso"])

MAX_EXPLANATION_OUTPUT_TOKENS = 2048

# --- Longitud (SDD Iter-2, 18.4) --------------------------------------------
_MAX_RESPUESTA_CHARS = 1800
_MIN_RESPUESTA_CHARS = 400
_MAX_RAW_CHARS = 6000
_TELEGRAM_LIMIT = 4096  # propio: un test lo iguala a `query_handler.TELEGRAM_MESSAGE_LIMIT`
_MARGEN_TELEGRAM = 100
_NOTA_ACORTADA = "(explicación acortada por el largo del mensaje)"
_FIT_PISO = 0.4
# Fin de oración: [.!?] seguido de espacio o fin de texto (el "." de un decimal va
# seguido de un dígito y no cuenta).
_FIN_ORACION_RE = re.compile(r"[.!?](?= |\Z)")


def _len16(texto: str) -> int:
    """Longitud en unidades UTF-16 (la que cuenta Telegram para los emojis
    fuera del BMP)."""
    return len(texto.encode("utf-16-le")) // 2


# Emojis del marco determinístico (CS-36): 🤖 📌 🧮 🔍(U+1F50D) 📐 📊 📋. NO incluye
# U+1F50E (la lupa de la etiqueta "Qué dicen los datos").
_MARCO_EMOJIS = "\U0001F916\U0001F4CC\U0001F9EE\U0001F50D\U0001F4D0\U0001F4CA\U0001F4CB"
_NORMALIZE_BORRAR = str.maketrans("", "", "*_`" + _MARCO_EMOJIS)
_TRES_SALTOS_RE = re.compile(r"\n{3,}")


def _normalize_layout(texto: str) -> str:
    """Limpia Markdown inofensivo y espaciado ANTES de los guards (18.2).

    Pura, idempotente en una pasada y lineal. Solo borra caracteres, une
    líneas o agrega viñeta, espacio y salto de línea. Puede crear
    coincidencias del filtro de salida: los guards corren SIEMPRE sobre su
    salida. Orden fijo."""
    t = texto.replace("\r\n", "\n").replace("\r", "\n").translate(_NORMALIZE_BORRAR)
    salida: list[str] = []
    for linea in t.split("\n"):
        s = linea.lstrip(" \t")
        if s.startswith("#"):
            s = s.lstrip("# \t")
            linea = s
        if s[:1] in ("-", "–") and len(s) > 1 and s[1] in " \t":
            linea = "• " + s[1:].lstrip(" \t")
        linea = linea.rstrip(" \t")
        if salida and salida[-1] != "" and linea.startswith(_ALL_LABELS):
            salida.append("")
        salida.append(linea)
    return _TRES_SALTOS_RE.sub("\n\n", "\n".join(salida)).strip(" \t\n")


def _candidato_en_rango(texto: str, fin: int, piso: float, max_chars: int) -> bool:
    return piso <= _len16(texto[:fin]) <= max_chars


def _fit_respuesta(texto: str, max_chars: int) -> Optional[str]:
    """Recorta la respuesta del modelo sin partir un número (18.4).

    Párrafo completo antes que oración; ambos con longitud UTF-16 en
    `[0.4 * max_chars, max_chars]`. NUNCA corta en un espacio ni agrega "…".
    `None` si no hay corte válido (el llamador entrega el mensaje de "no
    disponible"). Log solo de enteros."""
    if _len16(texto) <= max_chars:
        return texto
    piso = _FIT_PISO * max_chars
    candidato: Optional[str] = None
    i = texto.rfind("\n\n")
    while i != -1:
        if _candidato_en_rango(texto, i, piso, max_chars):
            candidato = texto[:i]
            break
        i = texto.rfind("\n\n", 0, i + 1)
    if candidato is None:
        for m in reversed(list(_FIN_ORACION_RE.finditer(texto))):
            if _candidato_en_rango(texto, m.end(), piso, max_chars):
                candidato = texto[: m.end()]
                break
    if candidato is None:
        return None
    res = candidato.rstrip()
    while res:
        ultima = res.rsplit("\n", 1)[-1].strip()
        if ultima not in _ALL_LABELS:
            break
        res = res[: len(res) - len(res.rsplit("\n", 1)[-1])].rstrip()
    if res == "":
        return None
    logger.info("GEMINI_RESPUESTA_RECORTADA chars=%d max=%d", _len16(texto), max_chars)
    return res


# --- Escala de montos (CS-31 / 18.5) -----------------------------------------
_ESCALA_RE = re.compile(
    r"(\$?\d[\d.,]*)(?:\s*(mil millones|miles de millones|millones|billones|trillones|mil)\b)?",
    re.IGNORECASE,
)


def _pares_escala(texto: str) -> set[tuple[str, Optional[str]]]:
    pares: set[tuple[str, Optional[str]]] = set()
    for m in _ESCALA_RE.finditer(texto):
        sufijo = m.group(2)
        pares.add((_normalize_numeric_token(m.group(1)), sufijo.lower() if sufijo else None))
    return pares


def _escalas_consistentes(payload_presentado: dict, respuesta: str) -> bool:
    """Tercer chequeo (DESPUÉS de `_no_new_protected_tokens`, que no se toca):
    el guard compara solo la cifra, no la escala (`$394.3 millones` pasaría
    con un payload de `$394.3 mil millones`). Regla 1: todo (cifra, sufijo)
    con sufijo de la respuesta debe estar en el payload presentado. Regla 2:
    una cifra que en el payload aparece SOLO con sufijo no puede aparecer sin
    ese sufijo."""
    p = _pares_escala(json.dumps(payload_presentado, ensure_ascii=False, default=str))
    for numero, sufijo in _pares_escala(respuesta):
        if sufijo is not None:
            if (numero, sufijo) not in p:
                return False
        elif (numero, None) not in p and any(n == numero and s is not None for n, s in p):
            return False
    return True


# --- Números presentados al modelo (SDD Iter-2, 18.3) -------------------------
_LIMITE_HOJA_NUMERICA = 1e15


def _money_compact(x: float) -> str:
    """Monto legible, en el formato del resto del bot: `$1,234.56` por debajo
    de un millón, `$X.X millones`, `$X.X mil millones` (nunca "billón"). El
    signo va como lo pone `_money` (`$-5.00`). `round(a/1e6, 1) >= 1000` promueve
    la unidad para no escribir `$1,000.0 millones`."""
    a = abs(x)
    if a >= 1e9 or round(a / 1e6, 1) >= 1000.0:
        return f"${x / 1e9:,.1f} mil millones"
    if a >= 1e6:
        return f"${x / 1e6:,.1f} millones"
    return _money(x)


# Unidad por clave, tomada de lo que YA muestran al usuario `_dato_*`, las
# `_cuenta_*` y los `_valor_desglose_*` (misma unidad que ve quien lee, no una
# heurística). Una clave no listada queda numérica y redondeada. Las `pct_vs_*`
# de `mom` NO se listan: `_dato_texto_libre` las multiplica por 100 y
# `_cuenta_mom`/`_valor_desglose_mom` no (están en puntos porcentuales).
_PAYLOAD_KIND_POR_PREGUNTA: dict[str, dict[str, str]] = {
    "ver": {"precio_actual": "money", "valor_justo_total": "money"},
    "vf": {
        "precio_actual": "money", "valor_justo_multiplos": "money", "valor_justo_graham": "money",
        "valor_justo_dcf": "money", "valor_justo_total": "money",
    },
    "gra": {
        "pesimista": "money", "conservador": "money", "optimista": "money",
        "eps_ttm": "money", "y_value": "pct", "g_aplicado": "pct",
    },
    "mul": {
        "pesimista": "money", "conservador": "money", "optimista": "money",
        "eps_ttm": "money", "per_promedio_peers": "ratio",
    },
    "dcf": {
        "pesimista": "money", "conservador": "money", "optimista": "money",
        "dcf_wacc": "pct", "dcf_g_fcf": "pct", "dcf_fcf_base": "money",
        "dcf_valor_presente_flujos": "money", "dcf_valor_terminal_descontado": "money",
        "dcf_equity_value": "money",
    },
    "rat": {
        "ratio_liquidez": "ratio", "margen_bruto": "pct", "per": "ratio", "ps": "ratio",
        "current_assets": "money", "current_liabilities": "money", "revenue": "money",
        "cost_of_revenue": "money", "market_cap": "money", "eps_ttm": "money",
        "precio_actual": "money",
    },
    "pil": {
        "revenue_reciente": "money", "revenue_antiguo": "money", "net_income_reciente": "money",
        "net_income_antiguo": "money", "ratio_liquidez": "ratio",
    },
    "ren": {"roe": "pct"},
    "rsk": {"beta": "ratio", "beta_umbral_bajo": "ratio", "beta_umbral_alto": "ratio"},
    "mom": {
        "precio_actual": "money", "year_high": "money", "year_low": "money",
        "price_avg_50": "money", "price_avg_200": "money",
    },
    "cmp": {
        "per_propio": "ratio", "per_promedio_peers": "ratio", "eps_ttm": "money",
        "precio_actual": "money",
    },
    "alz": {"z": "ratio", "a": "ratio", "b": "ratio", "c": "ratio", "d": "ratio", "e": "ratio"},
    "azp": {"z": "ratio", "a": "ratio", "b": "ratio", "c": "ratio", "d": "ratio"},
    "pig": {
        "net_income_t": "money", "cfo_t": "money", "roa_t": "ratio", "roa_t1": "ratio",
        "apalancamiento_t": "ratio", "apalancamiento_t1": "ratio", "liquidez_t": "ratio",
        "liquidez_t1": "ratio", "shares_t": "ratio", "shares_t1": "ratio", "margen_t": "pct",
        "margen_t1": "pct", "rotacion_t": "ratio", "rotacion_t1": "ratio",
    },
    "mgr": {
        "roic": "pct", "ebit": "money", "capital_invertido": "money", "current_assets": "money",
        "current_liabilities": "money", "ppe_net": "money",
    },
    "mge": {
        "earnings_yield": "pct", "ebit": "money", "ev": "money", "market_cap": "money",
        "total_debt": "money", "cash": "money",
    },
    "aqv": {"earnings_yield": "pct", "umbral_alto": "pct", "umbral_bajo": "pct"},
    "aqq": {
        "roe": "pct", "gross_margin": "pct", "piotroski_ratio": "pct", "roe_umbral_alto": "pct",
        "roe_umbral_bajo": "pct", "gross_margin_umbral_alto": "pct",
        "gross_margin_umbral_bajo": "pct", "piotroski_ratio_umbral_alto": "pct",
        "piotroski_ratio_umbral_bajo": "pct",
    },
    "aqm": {"precio_actual": "money", "price_avg_50": "money", "price_avg_200": "money"},
    "aql": {"beta": "ratio", "beta_umbral_bajo": "ratio", "beta_umbral_alto": "ratio"},
}
# `pir`/`pia`/`pie` comparten los `valores` de Piotroski con `pig`.
for _code in ("pir", "pia", "pie"):
    _PAYLOAD_KIND_POR_PREGUNTA[_code] = _PAYLOAD_KIND_POR_PREGUNTA["pig"]


def _unir_unidades(por_pregunta: dict[str, dict[str, str]]) -> dict[str, str]:
    union: dict[str, str] = {}
    for kinds in por_pregunta.values():
        for clave, unidad in kinds.items():
            if union.setdefault(clave, unidad) != unidad:
                # Colisión de unidad entre preguntas: habría que pasar a
                # `(question_code, clave)`. Hoy no ocurre (lo fija un test).
                raise RuntimeError(f"colisión de unidad para la clave {clave!r}")
    return union


_PAYLOAD_KIND: dict[str, str] = _unir_unidades(_PAYLOAD_KIND_POR_PREGUNTA)


def _presentar_hoja(valor, clave: Optional[str]):
    # `type(v) in (int, float)`: `bool` (subclase de int), `str`, `None` no se tocan.
    if type(valor) not in (int, float):
        return valor
    if abs(valor) >= _LIMITE_HOJA_NUMERICA or not math.isfinite(valor):
        return None
    unidad = _PAYLOAD_KIND.get(clave) if clave is not None else None
    if unidad == "money":
        return _money_compact(valor)
    if unidad == "pct":
        return _pct1(valor)
    if unidad == "ratio":
        return f"{valor:.2f}"
    return valor if type(valor) is int else round(valor, 4)


def _present_payload(datos, _clave: Optional[str] = None):
    """Copia del payload con los números ya escritos para leerse (`$1,234.56`,
    `15.3%`, `$2.5 mil millones`) en el formato del resto del bot. Pura; no
    muta la entrada. La clave de una hoja es la del dict padre más cercano.
    Toda hoja numérica no finita o con |v| >= 1e15 pasa a `None`; una
    numérica sin clasificar queda numérica (float redondeado a 4 decimales)."""
    if isinstance(datos, dict):
        return {k: _present_payload(v, k) for k, v in datos.items()}
    if isinstance(datos, list):
        return [_present_payload(v, _clave) for v in datos]
    return _presentar_hoja(datos, _clave)


def _derivar_posicion_vs_precio(valor_justo, precio) -> dict:
    """Campos derivados de `vf`/`ver` (CS-33), calculados en Python desde los
    valores CRUDOS: dirección y diferencia SIN signo, sobre el valor ya
    redondeado a 1 decimal. `{}` si algún dato falta o no es numérico válido."""
    for v in (valor_justo, precio):
        # abs(v) primero: math.isfinite(10**400) lanza OverflowError; la comparación int/float es exacta.
        if type(v) not in (int, float) or abs(v) >= _LIMITE_HOJA_NUMERICA or not math.isfinite(v):
            return {}
    if precio <= 0:
        return {}
    # Con ambos finitos y precio > 0 la división no lanza (desborda a inf): se filtra abajo.
    diferencia = float(f"{(valor_justo / precio - 1) * 100:.1f}")
    if not math.isfinite(diferencia) or abs(diferencia) >= _LIMITE_HOJA_NUMERICA:
        return {}
    if diferencia == 0:
        posicion = "igual"
    else:
        posicion = "por encima" if diferencia > 0 else "por debajo"
    return {"posicion_vs_precio": posicion, "diferencia_vs_precio_abs": f"{abs(diferencia):.1f}%"}


def _datos_para_el_modelo(question_code: str, datos_crudos: dict) -> dict:
    """Payload que viaja al modelo (QB-9/A3). `datos_crudos` queda intacto
    (Dato/Cuenta/Desglose lo reciben CRUDO). Al modelo va una COPIA presentada;
    solo `vf`/`ver` suman los campos derivados (`posicion_vs_precio`,
    `diferencia_vs_precio_abs`), calculados en Python desde los valores
    crudos. Lo usan el handler y `scripts/gemini_smoke.py`; los tokens del
    guard son `_compute_datos_tokens(crudo, presentado)`."""
    datos_modelo = _present_payload(datos_crudos)
    if question_code in ("vf", "ver"):
        datos_modelo.update(
            _derivar_posicion_vs_precio(datos_crudos.get("valor_justo_total"), datos_crudos.get("precio_actual"))
        )
    return datos_modelo


def _fit_leaf_message(
    dato_line: str,
    respuesta: str,
    formula: Optional[str],
    fuente: Optional[str],
    *,
    cuenta: Optional[str] = None,
    desglose: Optional[str] = None,
    grafico: Optional[str] = None,
) -> Optional[str]:
    """Presupuesto de entrega contra el límite de Telegram (UTF-16) y recorte
    de la respuesta (SDD Iter-2, 18.4). Omite bloques OPCIONALES completos
    (primero el Gráfico, luego el Desglose), nunca los trunca; `Dato`, `Cuenta`,
    respuesta y disclaimer no se omiten. `None` -> el handler envía
    `EXPLAIN_UNAVAILABLE_MSG`. La nota de recorte se agrega acá, DESPUÉS de los
    guards."""

    def restante(g: Optional[str], d: Optional[str]) -> int:
        base = _build_leaf_message(dato_line, "", formula, fuente, cuenta=cuenta, desglose=d, grafico=g)
        return _TELEGRAM_LIMIT - _MARGEN_TELEGRAM - _len16(base)

    def omitir(motivo: str) -> None:
        logger.info("GEMINI_ENTREGA_OMITIDA motivo=%s chars=%d", motivo, _len16(respuesta))

    g, d = grafico, desglose
    r = restante(g, d)
    if r < _MIN_RESPUESTA_CHARS and g:
        g = None
        r = restante(g, d)
    if r < _MIN_RESPUESTA_CHARS and d:
        d = None
        r = restante(g, d)
    if r < 0:
        omitir("presupuesto")
        return None
    if _len16(respuesta) <= min(_MAX_RESPUESTA_CHARS, r):
        cuerpo = respuesta
    else:
        if r < _MIN_RESPUESTA_CHARS:
            omitir("presupuesto")
            return None
        res = _fit_respuesta(respuesta, min(_MAX_RESPUESTA_CHARS, r - _len16("\n\n" + _NOTA_ACORTADA)))
        if res is None:
            omitir("sin_corte")
            return None
        cuerpo = res + "\n\n" + _NOTA_ACORTADA
    msg = _build_leaf_message(dato_line, cuerpo, formula, fuente, cuenta=cuenta, desglose=d, grafico=g)
    if _len16(msg) > _TELEGRAM_LIMIT:
        omitir("presupuesto")
        return None
    return msg


_NUMERIC_TOKEN_RE = re.compile(r"^[+-]?\$?\d[\d.,]*%?$")


def _normalize_numeric_token(token: str) -> str:
    """Normaliza formato (no valor) para comparar contra los tokens
    protegidos del payload real. Además del `$`/signo/separador de miles ya
    cubiertos, distingue coma DECIMAL de coma de MILES (incidente de
    producción 2026-09-04: el modelo a veces escribe montos con coma decimal,
    ej. "$285,75" en vez de "285.75" -- mezcla de convenio numérico de otro
    idioma/locale -- que antes se normalizaba mal a "28575", un valor
    distinto, y disparaba un rechazo del guard sobre un número que en
    realidad coincidía). Heurística segura (no hay forma exacta de
    distinguir los dos casos en un string aislado en el caso general): con
    EXACTAMENTE una coma en el número, si va seguida de 1-2 dígitos hasta
    el final se interpreta como coma decimal (se convierte a "."); si va
    seguida de 3 dígitos (patrón típico de miles, ej. "1,000") se sigue
    tratando como separador de miles (se quita), igual que con 2+ comas."""
    if not _NUMERIC_TOKEN_RE.match(token):
        return token
    percent = token.endswith("%")
    body = token[:-1] if percent else token
    body = body.lstrip("+-").lstrip("$").rstrip(".,")
    if body.count(",") == 1:
        entero, _, decimales = body.partition(",")
        if decimales.isdigit() and 1 <= len(decimales) <= 2:
            body = f"{entero}.{decimales}"
        else:
            body = body.replace(",", "")
    else:
        body = body.replace(",", "")
    return body + "%" if percent else body


def _percent_token_a_decimal(token: str) -> Optional[str]:
    """Si `token` (ya normalizado por `_normalize_numeric_token`) es un
    porcentaje, devuelve el string que tendría el mismo valor expresado
    como proporción decimal cruda (ej. "12%" -> "0.12"), matcheando cómo
    `json.dumps` serializa el float crudo del payload que el modelo recibió
    (ej. `net_debt_to_ebitda: 0.12`, un múltiplo -- no un porcentaje -- que
    varios campos vecinos del mismo payload SÍ son, roe/dividend_yield/
    payout_ratio). Extiende la normalización de formato ya existente
    ($/separador de miles) al mismo tipo de falso positivo: incidente real
    de producción 2026-09-03 donde el modelo redactó 0.12 como "12%" para
    `net_debt_to_ebitda` -- mismo valor, otra representación, no una
    alucinación. `None` si `token` no es un porcentaje o no es convertible."""
    if not token.endswith("%"):
        return None
    try:
        value = float(token[:-1])
    except ValueError:
        return None
    return repr(value / 100)


def _compute_datos_tokens(datos_crudos: dict, datos_presentados: Optional[dict] = None) -> set[str]:
    """Conjunto de tokens protegidos que el guard acepta en la respuesta.

    Única implementación, usada por el handler y por `scripts/gemini_smoke.py`
    (SDD Iter-2, CS-25b). Con `datos_presentados=None` es EXACTAMENTE el
    cálculo de HEAD: tokens de `json.dumps(crudo, ensure_ascii=False,
    default=str)` sin redondear. Con `datos_presentados` es la unión (nunca
    reemplaza) con los tokens del payload presentado al modelo."""
    tokens = {
        _normalize_numeric_token(token)
        for token in ai_rewrite.protected_tokens(
            json.dumps(datos_crudos, ensure_ascii=False, default=str)
        )
    }
    if datos_presentados is not None:
        tokens |= {
            _normalize_numeric_token(token)
            for token in ai_rewrite.protected_tokens(
                json.dumps(datos_presentados, ensure_ascii=False, default=str)
            )
        }
    return tokens


def _no_new_protected_tokens(datos_tokens: set[str], respuesta: str) -> bool:
    respuesta_tokens = {
        _normalize_numeric_token(token) for token in ai_rewrite.protected_tokens(respuesta)
    }
    for token in respuesta_tokens:
        if token in datos_tokens:
            continue
        decimal_equiv = _percent_token_a_decimal(token)
        if decimal_equiv is not None and decimal_equiv in datos_tokens:
            continue
        return False
    return True


_GUARD_LOG_MAX_CHARS = 500


def _log_guard_reject_contadores(guard: str, chars: int) -> None:
    """Rechazos nuevos (`largo`/`escala`/`vacio`): solo contadores enteros,
    nunca el texto del modelo (SDD Iter-2, CS-23a)."""
    logger.warning("GEMINI_GUARD_REJECT feature=explain guard=%s chars=%d", guard, chars)


class _ExplainUnavailable(Exception):
    """Señal interna -- cualquier fallo de red/estructura/guard converge
    acá y nunca se propaga fuera de `build_explain_handler`."""


async def _fetch_explanation(
    *,
    clients,
    config: GeminiConfig,
    question_code: str,
    pregunta_fija: str,
    datos_del_contexto: dict,
    datos_tokens: set[str],
    system_prompt: str = SYSTEM_PROMPT_EXPLAIN,
) -> str:
    """Una llamada a Gemini vía `gemini_client.generate_text` (1 reintento
    solo ante 500/503, hecho por el cliente). Todo fallo converge en
    `_ExplainUnavailable`. El cliente ya dejó su propio registro con token
    `GEMINI_*`: acá no se duplica. Nunca se crea un cliente HTTP propio."""
    http_client = clients.gemini_http
    if http_client is None or not config.enabled:
        gemini_client.log_no_client("explain")
        raise _ExplainUnavailable()

    user_text = json.dumps(
        {"pregunta": pregunta_fija, "datos": datos_del_contexto}, ensure_ascii=False
    )
    try:
        result = await gemini_client.generate_text(
            http_client,
            config,
            system_prompt=system_prompt,
            user_text=user_text,
            max_output_tokens=MAX_EXPLANATION_OUTPUT_TOKENS,
            timeout_seconds=config.timeout_seconds,
            max_retries=1,
            feature="explain",
        )
    except GeminiError:
        raise _ExplainUnavailable() from None

    # Pipeline fijo (SDD Iter-2, CS-24a): tope crudo -> normalizar -> guard de
    # números -> guard de escala -> filtro de salida -> devolver el texto
    # NORMALIZADO sin cortar (el recorte es de `_fit_leaf_message`). Los guards
    # corren sobre lo mismo que se entrega.
    crudo = result.text
    largo_crudo = len(crudo)
    if largo_crudo > _MAX_RAW_CHARS:
        _log_guard_reject_contadores("largo", largo_crudo)
        raise _ExplainUnavailable()
    respuesta = _normalize_layout(crudo)
    if respuesta == "":
        _log_guard_reject_contadores("vacio", largo_crudo)
        raise _ExplainUnavailable()
    if not _no_new_protected_tokens(datos_tokens, respuesta):
        guard = "numeros"
    elif not _escalas_consistentes(datos_del_contexto, respuesta):
        _log_guard_reject_contadores("escala", len(respuesta))
        raise _ExplainUnavailable()
    elif violates_output_filter(respuesta):
        guard = "salida"
    else:
        return respuesta
    logger.warning(
        "GEMINI_GUARD_REJECT feature=explain guard=%s respuesta=%s",
        guard,
        _safe_display(
            _sanitize_for_log(respuesta), max_chars=_GUARD_LOG_MAX_CHARS, keep_newlines=False
        ),
    )
    raise _ExplainUnavailable()


# --- Teclados (Nivel 1 / Nivel 2 — Decisión de diseño #1/#2/#3, extendidos
# por SDD_explicacion_paso_a_paso.md Decisión de diseño #1/#3) -------------

# Prefijos FIJOS de los 2 botones hermanos de toda pregunta
# `variant="dato_y_paso_a_paso"` -- el nombre propio de la pregunta
# (`spec.label`) se agrega a continuación para que cada fila sea
# distinguible a simple vista (bug de UX: antes los 2 botones eran
# idénticos en las 27 preguntas -- ver captura de pantalla de Daniela).
_PREFIX_VER_DATO = "📊"
_PREFIX_PASO_A_PASO = "🎓"

# Un token inicial separado por espacio que no tiene ningún caracter
# alfanumérico se interpreta como el emoji propio del label (ej. "💰 Valor
# Justo Total" -> "💰" + "Valor Justo Total") y se quita antes de anteponer
# nuestro propio prefijo, para no duplicar emoji en el botón.
_LEADING_EMOJI_RE = re.compile(r"^(\S+)\s+(.+)$")


def _label_sin_emoji_propio(label: str) -> str:
    match = _LEADING_EMOJI_RE.match(label)
    if match and not any(ch.isalnum() for ch in match.group(1)):
        return match.group(2)
    return label


def _leaf_rows(
    context_id: str, code: str, spec: ai_explain_content.QuestionSpec
) -> list[list[InlineKeyboardButton]]:
    """1 fila por pregunta: 2 botones hermanos si `variant="dato_y_paso_a_
    paso"` (Decisión de diseño #1 de SDD_explicacion_paso_a_paso.md), cada
    uno con el nombre propio de la pregunta (`spec.label`) para que se
    distingan entre sí (fix UX 2026-09-03); 1 botón con el label propio de
    la pregunta en caso contrario (narrativa/determinístico, sin cambios)."""
    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
        label = _label_sin_emoji_propio(spec.label)
        return [[
            InlineKeyboardButton(
                f"{_PREFIX_VER_DATO} {label}", callback_data=f"xp:{context_id}:{code}"
            ),
            InlineKeyboardButton(
                f"{_PREFIX_PASO_A_PASO} {label}", callback_data=f"xp:{context_id}:p:{code}"
            ),
        ]]
    return [[InlineKeyboardButton(spec.label, callback_data=f"xp:{context_id}:{code}")]]


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
            rows.extend(_leaf_rows(context_id, code, questions[code]))
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
    rows: list[list[InlineKeyboardButton]] = []
    for c in codes:
        rows.extend(_leaf_rows(context_id, c, questions[c]))
    rows.append([InlineKeyboardButton("🔙 Menú", callback_data=f"xp:{context_id}:m")])
    return InlineKeyboardMarkup(rows)


def build_response_keyboard(
    kind: str, context_id: str, question_code: str, context: Optional[ExplanationContext]
) -> InlineKeyboardMarkup:
    """Decisión de diseño #2 de SDD_explicacion_paso_a_paso.md — el menú
    reaparece SIEMPRE tras cualquier respuesta de leaf: el de Nivel 2 de SU
    categoría (con "🔙 Menú" al final) si la pregunta pertenece a una, el de
    Nivel 1 si es suelta. `category_of` es la fuente de verdad única."""
    cat_code = ai_explain_content.category_of(kind, question_code)
    if cat_code is not None:
        markup = build_category_keyboard(kind, context_id, cat_code, context)
        if markup is not None:
            return markup
    return build_keyboard(kind, context_id)


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
    context_id: str, question_code: str, *, paso_a_paso: bool = False,
) -> None:
    """Dispatchea las 3 variantes (`dato_y_paso_a_paso`/`narrativa`/
    `deterministico`) × la forma `:p:` nueva. Las 3 ramas de envío
    (determinístico/"Ver dato" vía `send_message`, narrativa/"paso a paso"
    vía `edit_message_text` tras "🤔 Pensando…") SIEMPRE adjuntan
    `reply_markup` (Decisión de diseño #2 de SDD_explicacion_paso_a_paso.md
    — el menú reaparece SIEMPRE)."""
    spec_global = _ALL_QUESTIONS_COMBINED.get(question_code)
    if spec_global is None or (
        paso_a_paso and spec_global.variant != ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO
    ):
        # `question_code` desconocido en ambas tablas, O la forma `:p:` se
        # usó con un `code` de variant distinta a "dato_y_paso_a_paso" (ej.
        # `xp:{id}:p:mod`) — mismo camino que un `question_code` desconocido
        # (criterio de aceptación explícito), sin excepción.
        logger.warning(
            "question_code desconocido o incompatible con :p: en callback xp:: %s",
            _sanitize_for_log(question_code),
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

    reply_markup = build_response_keyboard(stored.kind, context_id, question_code, stored)

    if spec.variant == ai_explain_content.VARIANT_DETERMINISTICO:
        # Decisión de diseño #4 (spec cerrada) — determinístico: sin "🤔
        # Pensando…", sin llamada HTTP, sin pasar por el guard, sin
        # consumir el balde.
        texto = _build_deterministic_content(stored, question_code)
        await bot.send_message(chat_id=chat_id, text=texto, reply_markup=reply_markup)
        return

    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO and not paso_a_paso:
        # Decisión de diseño #9 -- "Ver dato": mismo criterio de costo cero
        # que `evt`/`inf`, generalizado a las 22 preguntas.
        texto = _build_ver_dato_content(stored, question_code)
        await bot.send_message(chat_id=chat_id, text=texto, reply_markup=reply_markup)
        return

    # A partir de acá: `narrativa` (mod/ben/ren, sin cambios), o
    # `dato_y_paso_a_paso` con `paso_a_paso=True` ("Explicame paso a paso")
    # -- ambas llaman a Gemini y consumen el balde (Decisión de diseño #10).
    if not rate_limiter.allow(str(chat_id)):
        await bot.send_message(chat_id=chat_id, text=RATE_LIMITED_MSG)
        return

    datos_del_contexto = _build_explain_payload(stored, question_code)

    cuenta: Optional[str] = None
    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
        try:
            cuenta = _build_cuenta_line(stored.kind, question_code, datos_del_contexto)
        except Exception:
            # Mejora recomendada (b) de `security` -- un bug de programación
            # en el dispatch de `_build_cuenta_line` (~22 casos) nunca debe
            # dejar al usuario sin respuesta: cae al mismo mensaje que un
            # fallo de Gemini, nunca sube sin capturar hasta el handler
            # global de errores.
            logger.exception(
                "Fallo inesperado construyendo la cuenta de %s", _sanitize_for_log(question_code)
            )
            await bot.send_message(chat_id=chat_id, text=EXPLAIN_UNAVAILABLE_MSG)
            return
        if cuenta is not None:
            # Decisión de diseño #4 -- la cuenta entra al payload ANTES del
            # cálculo de `datos_tokens` del guard: Gemini la recibe como
            # dato garantizado, nunca la genera.
            datos_del_contexto["cuenta"] = cuenta

    # `datos_del_contexto` queda CRUDO para Dato/Cuenta/Desglose (CA-81); al
    # modelo viaja una COPIA presentada (`$1,234.56`, `15.3%`) con los campos
    # derivados de `vf`/`ver` ya calculados en Python, y `datos_tokens` es la
    # unión de ambos (SDD Iter-2, QB-9/A3).
    datos_modelo = _datos_para_el_modelo(question_code, datos_del_contexto)
    datos_tokens = _compute_datos_tokens(datos_del_contexto, datos_modelo)
    config = clients.gemini_config or GeminiConfig()

    pensando = await bot.send_message(chat_id=chat_id, text=EXPLAIN_PENDING_MSG)

    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
        pregunta_fija = spec.pregunta_paso_a_paso
        system_prompt = SYSTEM_PROMPT_PASO_A_PASO
    else:
        pregunta_fija = spec.pregunta_narrativa
        system_prompt = SYSTEM_PROMPT_EXPLAIN

    try:
        respuesta = await _fetch_explanation(
            clients=clients,
            config=config,
            question_code=question_code,
            pregunta_fija=pregunta_fija,
            datos_del_contexto=datos_modelo,
            datos_tokens=datos_tokens,
            system_prompt=system_prompt,
        )
    except _ExplainUnavailable:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=pensando.message_id, text=EXPLAIN_UNAVAILABLE_MSG
        )
        return

    dato_line = _build_dato_line(stored.kind, question_code, datos_del_contexto)
    formula = ai_explain_content.formulas(stored.kind).get(question_code)
    fuente = ai_explain_content.fuentes(stored.kind).get(question_code)
    desglose = _build_desglose_block(stored.kind, question_code, datos_del_contexto, context=stored)
    # Gráfico (F2): se arma DESPUÉS de la respuesta, con una COPIA del `datos`
    # crudo (+ `precio_actual` del contexto para gra/dcf/mul, cuyo payload no lo
    # trae); jamás entra a `datos_del_contexto`, `datos_tokens` ni al modelo.
    constructor_grafico = _GRAFICO_POR_PREGUNTA.get(question_code)
    grafico = None
    if constructor_grafico is not None:
        datos_grafico = dict(datos_del_contexto)
        datos_grafico.setdefault("precio_actual", stored.precio_actual)
        grafico = constructor_grafico(datos_grafico)
    texto = _fit_leaf_message(
        dato_line, respuesta, formula, fuente, cuenta=cuenta, desglose=desglose, grafico=grafico
    )
    if texto is None:
        # Sin presupuesto o sin corte válido: nunca se envía >4096 ni un texto a
        # medias -- mismo mensaje que cualquier otro fallo de la explicación.
        await bot.edit_message_text(
            chat_id=chat_id, message_id=pensando.message_id, text=EXPLAIN_UNAVAILABLE_MSG
        )
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=pensando.message_id, text=texto, reply_markup=reply_markup
        )
    except TelegramError as exc:
        # Fix urgente 2026-09-04 (paridad con `query_handler._deliver_all`):
        # antes, si esta edición fallaba (mensaje muy largo, Markdown roto
        # -- no debería darse acá porque este módulo nunca usa
        # `parse_mode`, o cualquier otro motivo de Telegram), la excepción
        # subía sin capturar y el usuario se quedaba mirando "🤔 Pensando…"
        # para siempre. Último recurso: un `send_message` nuevo con un
        # aviso genérico -- si también falla, se loguea a WARNING y se
        # descarta en silencio, nunca puede tumbar el callback por segunda
        # vez.
        logger.error(
            "No se pudo entregar la explicación final para %s — se le avisa "
            "al usuario con un mensaje genérico: %s", _sanitize_for_log(question_code), exc,
        )
        try:
            await bot.send_message(chat_id=chat_id, text=EXPLAIN_DELIVERY_FAILED_MSG)
        except TelegramError:
            logger.warning(
                "Tampoco se pudo avisarle al usuario con el mensaje genérico "
                "para la explicación de %s", _sanitize_for_log(question_code), exc_info=True,
            )


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

        match_paso_a_paso = _CALLBACK_PASO_A_PASO_RE.fullmatch(raw_data)
        if match_paso_a_paso is not None:
            await _dispatch_leaf(
                context.bot, chat_id, store, rate_limiter, clients,
                match_paso_a_paso.group(1), match_paso_a_paso.group(2),
                paso_a_paso=True,
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
