"""Explicaciones interactivas con Ollama (botones inline) —
`SDD_explicaciones_interactivas_ollama.md`.

Segundo caso de uso de la integración con Ollama ya existente en
`ai_rewrite.py`: en vez de reescribir el TONO de texto ya armado, esta capa
GENERA texto nuevo (una explicación corta) a partir de datos ya calculados
por `query_handler.fetch_and_analyze_parts`/`advanced_command._build_message`
— nunca vuelve a llamar a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR.

Un solo `CallbackQueryHandler` compartido (`build_explain_handler`, Decisión
de diseño #8) atiende los botones `xp:{context_id}:{question_code}` de AMBOS
flujos (texto libre y `/avanzado`) — el `ExplanationContext.kind` guardado,
no el módulo que lo generó, decide qué prompt/datos usar.

Guard de integridad (Decisión de diseño #4c) — distinto del guard de
`ai_rewrite.py` porque acá no hay "original" con el que comparar: la
respuesta puede usar cualquier SUBCONJUNTO de los tokens protegidos que le
pasamos, pero ningún token protegido en la respuesta puede estar ausente de
los datos originales (bloquea alucinación de números/tickers nuevos).

Hallazgo 1 BLOQUEANTE de `security`: `sector`/`industry` (texto libre de
FMP) nunca viajan crudos al prompt de Ollama. `sector` se valida contra una
allow-list GICS cerrada (`_SECTOR_ALLOWLIST`); `industry` se excluye
directamente del payload en todos los casos — el booleano `asset_light` ya
comunica lo esencial para la única pregunta que lo necesitaría (`mod`), y
excluirlo por completo es la única garantía robusta contra un valor
adversarial compuesto enteramente por caracteres "inocentes" (letras y
espacios) que un allow-list de caracteres no bloquearía.
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

from investbot import ai_rewrite
from investbot.summary import DISCLAIMER_NO_ASESORAMIENTO

logger = logging.getLogger(__name__)

# --- Logging seguro (duplicado local, no importado de query_handler.py) ----
#
# `query_handler.py` importa este módulo (Decisión de diseño #3/#8) — si
# `ai_explain.py` importara `query_handler` de vuelta para reusar
# `sanitize_for_log`/`RATE_LIMITED_MSG`, se formaría un ciclo de imports.
# Mismo criterio de bajo acoplamiento que ya usa `advanced_command.py` para
# `_TICKER_RE` (duplicado, no importado).
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]")


def _sanitize_for_log(text: str) -> str:
    return _CONTROL_CHARS_RE.sub(" ", text)


# Texto idéntico a `query_handler.RATE_LIMITED_MSG` — mismo balde compartido
# (Decisión de diseño #7 paso 3), mismo mensaje al usuario, símbolo propio
# por la razón de arriba (evitar el ciclo de imports).
RATE_LIMITED_MSG = "Estás consultando muy rápido — esperá un minuto antes de volver a intentar."

# --- Mensajes de fallback (Decisión de diseño #7) ---------------------------

EXPLAIN_UNAVAILABLE_MSG = "📋 Ollama no está disponible en este momento — probá de nuevo en un rato."
EXPLAIN_EXPIRED_MSG = "Este botón ya venció — pedí el análisis de nuevo para ver explicaciones."
# `callback_data` malformado, o `question_code` sintácticamente válido pero
# ausente de las tablas conocidas (hallazgo 5 de `security`) — mismo camino,
# distinto de "vencido" (acá el botón nunca fue válido, no es un problema de
# TTL/reinicio del proceso).
EXPLAIN_INVALID_MSG = "Ese botón no es válido — pedí el análisis de nuevo si querés una explicación."

# --- ExplanationContext / Store (Decisión de diseño #3) ---------------------


@dataclass(frozen=True)
class ExplanationContext:
    kind: str  # "texto_libre" | "avanzado"
    ticker: str
    company_name: str
    # --- payload específico de texto libre (kind="texto_libre") ---
    escenario_elegido: Optional[str] = None
    precio_actual: Optional[float] = None
    scenarios: Optional[dict] = None  # scenarios.as_dict() ya calculado
    pillars: Optional[dict] = None  # pillars_dict ya calculado
    veredicto_barata: Optional[bool] = None
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


@dataclass(frozen=True)
class _StoredEntry:
    context: ExplanationContext
    expires_at: float


class ExplanationContextStore:
    """Dict en memoria del proceso con tope de tamaño + expiración por
    tiempo — mismo espíritu de simplicidad que `security.InMemoryRateLimiter`.
    Se pierde en cada reinicio del proceso, a propósito (Restricciones de la
    spec: sin persistencia nueva).

    `now_fn` es un reloj inyectable (default `time.monotonic`, mismo criterio
    que el resto del proyecto usa para tiempo monotónico no-wall-clock) —
    permite testear TTL sin `sleep` real ni mockear `time` globalmente
    (requisito de testabilidad explícito de `qa`).
    """

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
        # dict preserva orden de inserción (Python 3.7+) -- con TTL uniforme
        # por store, "la entrada más vieja" y "la de TTL más próximo a
        # vencer" coinciden siempre, así que la evicción por orden de
        # inserción alcanza (Decisión de diseño #3).
        self._entries: dict[str, _StoredEntry] = {}

    def _evict_expired(self) -> None:
        now = self._now_fn()
        expired = [cid for cid, entry in self._entries.items() if now >= entry.expires_at]
        for cid in expired:
            del self._entries[cid]

    def put(self, context: ExplanationContext) -> str:
        """Genera un `context_id` nuevo (8 hex chars), guarda, evict-ea si
        hace falta, lo devuelve."""
        self._evict_expired()

        context_id = uuid.uuid4().hex[:8]
        while context_id in self._entries:  # colisión, extremadamente improbable -- defensivo
            context_id = uuid.uuid4().hex[:8]

        if len(self._entries) >= self.max_entries:
            oldest_id = next(iter(self._entries))
            del self._entries[oldest_id]

        self._entries[context_id] = _StoredEntry(
            context=context, expires_at=self._now_fn() + self.ttl_seconds
        )
        return context_id

    def get(self, context_id: str) -> Optional[ExplanationContext]:
        """`None` si no existe, venció el TTL, o fue evicted -- mismo camino
        en los 3 casos (Decisión de diseño #3)."""
        entry = self._entries.get(context_id)
        if entry is None:
            return None
        if self._now_fn() >= entry.expires_at:
            del self._entries[context_id]
            return None
        return entry.context


# --- Preguntas por flujo (Decisión de diseño #1) -----------------------------
# code -> (label del botón, pregunta fija enviada a Ollama).

QUESTIONS_TEXTO_LIBRE: dict[str, tuple[str, str]] = {
    "vf": (
        "💰 Valor Justo",
        "Explicá en 2 a 4 oraciones qué significa el rango de Valor Justo "
        "estimado para esta empresa y cómo se compara con el precio actual, "
        "para el escenario elegido, usando SOLO los datos del JSON.",
    ),
    "pil": (
        "🏛 Pilares",
        "Explicá en 2 a 4 oraciones qué son los 4 pilares de \"buena "
        "empresa\" evaluados y cuáles cumplió o no cumplió este ticker en "
        "particular, usando SOLO los datos del JSON.",
    ),
    "ver": (
        "⚖️ Veredicto",
        "Explicá en 2 a 4 oraciones por qué el veredicto (barata/cara/sin "
        "datos) salió así para este ticker, usando SOLO los datos del JSON.",
    ),
}

QUESTIONS_AVANZADO: dict[str, tuple[str, str]] = {
    "mod": (
        "❓ ¿Qué modelos aplican?",
        "Explicá en 2 a 4 oraciones qué modelos de los 5 (Altman Z-Score, "
        "Piotroski F-Score, Beneish M-Score, Magic Formula, Factores AQR) "
        "fueron calculables para este ticker en particular y por qué, "
        "usando SOLO los datos del JSON.",
    ),
    "alt": (
        "📐 Altman Z",
        "Explicá en 2 a 4 oraciones qué mide el Altman Z-Score (y Z'' si "
        "aplica) y qué significa el resultado de este ticker, usando SOLO "
        "los datos del JSON.",
    ),
    "pio": (
        "🧮 Piotroski F",
        "Explicá en 2 a 4 oraciones qué mide el Piotroski F-Score y qué "
        "significa el puntaje de este ticker, usando SOLO los datos del "
        "JSON.",
    ),
    "mag": (
        "🪄 Magic Formula",
        "Explicá en 2 a 4 oraciones qué miden ROIC y Earnings Yield y qué "
        "significan los valores de este ticker, usando SOLO los datos del "
        "JSON.",
    ),
    "aqr": (
        "📊 Factores AQR",
        "Explicá en 2 a 4 oraciones qué son los 4 factores (Value/Quality/"
        "Momentum/Low-vol) y qué significan las etiquetas de este ticker, "
        "usando SOLO los datos del JSON.",
    ),
}

_ALL_QUESTIONS: dict[str, tuple[str, str]] = {**QUESTIONS_TEXTO_LIBRE, **QUESTIONS_AVANZADO}


def build_keyboard(kind: str, context_id: str) -> InlineKeyboardMarkup:
    """3 botones en una fila (texto libre) o 5 botones -- `mod` solo en su
    fila, el resto en 2 filas de 2 (`/avanzado`) -- Decisión de diseño #1."""
    if kind == "texto_libre":
        row = [
            InlineKeyboardButton(label, callback_data=f"xp:{context_id}:{code}")
            for code, (label, _pregunta) in QUESTIONS_TEXTO_LIBRE.items()
        ]
        return InlineKeyboardMarkup([row])

    mod_label, _pregunta = QUESTIONS_AVANZADO["mod"]
    filas = [[InlineKeyboardButton(mod_label, callback_data=f"xp:{context_id}:mod")]]
    resto = [code for code in QUESTIONS_AVANZADO if code != "mod"]
    for i in range(0, len(resto), 2):
        par = resto[i : i + 2]
        filas.append(
            [
                InlineKeyboardButton(
                    QUESTIONS_AVANZADO[code][0], callback_data=f"xp:{context_id}:{code}"
                )
                for code in par
            ]
        )
    return InlineKeyboardMarkup(filas)


# --- Validación de callback_data (Decisión de diseño #2) --------------------

_CALLBACK_RE = re.compile(r"^xp:([0-9a-f]{8}):([a-z]{2,4})$")


# --- Sanitización de sector/industry (hallazgo 1 BLOQUEANTE de `security`) --

# Taxonomía GICS de 11 sectores tal como los devuelve FMP (mismos literales
# ya usados en `advanced_scoring.ASSET_LIGHT_SECTORES` para "Technology"/
# "Communication Services" -- confirmado contra fixtures reales del
# proyecto). Allow-list cerrada, no free-text: cualquier valor fuera de esta
# lista se trata como "no disponible", nunca se pasa el string crudo de FMP
# al prompt de Ollama.
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
    """`sector` contra la allow-list GICS cerrada -- nunca el string crudo
    de FMP si no matchea exactamente uno de los 11 valores conocidos."""
    if sector in _SECTOR_ALLOWLIST:
        return sector
    return _SECTOR_NO_DISPONIBLE


# --- Sub-dict por pregunta (Decisión de diseño #4a) -------------------------


def _build_explain_payload(context: ExplanationContext, question_code: str) -> dict:
    """Arma `datos_del_contexto` -- SOLO el sub-dict que la pregunta puntual
    necesita, nunca el `ExplanationContext` completo (superficie mínima).

    `industry` NUNCA se incluye acá bajo ningún `question_code` (hallazgo 1
    BLOQUEANTE de `security`, ver docstring del módulo) -- `asset_light` ya
    comunica lo esencial para `mod`, la única pregunta que podría
    necesitarlo. `sector` solo viaja validado contra la allow-list GICS
    cerrada (`_validated_sector`)."""
    if question_code == "vf":
        escenario = (context.scenarios or {}).get(context.escenario_elegido) or {}
        return {
            "escenario_elegido": context.escenario_elegido,
            "precio_actual": context.precio_actual,
            "valor_justo_multiplos": escenario.get("valor_justo_multiplos"),
            "valor_justo_graham": escenario.get("valor_justo_graham"),
            "valor_justo_dcf": escenario.get("valor_justo_dcf"),
            "valor_justo_total": escenario.get("valor_justo_total"),
        }
    if question_code == "pil":
        return {"pillars": context.pillars}
    if question_code == "ver":
        escenario = (context.scenarios or {}).get(context.escenario_elegido) or {}
        return {
            "veredicto_barata": context.veredicto_barata,
            "escenario_elegido": context.escenario_elegido,
            "precio_actual": context.precio_actual,
            "valor_justo_total": escenario.get("valor_justo_total"),
        }
    if question_code == "mod":
        return {"sector": _validated_sector(context.sector), "asset_light": context.asset_light}
    if question_code == "alt":
        return {"altman": context.altman, "altman_pp": context.altman_pp}
    if question_code == "pio":
        return {"piotroski": context.piotroski}
    if question_code == "mag":
        return {"magic": context.magic}
    if question_code == "aqr":
        return {"factors": context.factors}
    # Inalcanzable desde `build_explain_handler` (que ya valida
    # `question_code` contra `_ALL_QUESTIONS` antes de llegar acá) -- red de
    # seguridad para cualquier llamador directo futuro.
    raise ValueError(f"question_code desconocido: {question_code}")


# --- Prompt + guard de brevedad/integridad (Decisión de diseño #4) ----------

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
)

# Tope MUCHO más bajo que los 2000 de `ai_rewrite.MAX_OUTPUT_TOKENS` — una
# explicación corta no necesita más (Decisión de diseño #4a).
MAX_EXPLANATION_OUTPUT_TOKENS = 220

# Tope duro de caracteres por código -- defensa en profundidad, no depende
# únicamente de que Ollama respete `num_predict` (Decisión de diseño #4d).
_MAX_EXPLANATION_CHARS = 480


def _enforce_brevity(texto: str) -> str:
    if len(texto) <= _MAX_EXPLANATION_CHARS:
        return texto
    corte = texto.rfind(".", 0, _MAX_EXPLANATION_CHARS)
    return texto[: corte + 1] if corte > 0 else texto[:_MAX_EXPLANATION_CHARS] + "…"


def _no_new_protected_tokens(datos_tokens: set[str], respuesta: str) -> bool:
    """La respuesta puede usar cualquier subconjunto de los tokens que le
    pasamos (números, %, tickers, ✅/❌, SÍ/NO) -- pero NINGÚN token
    protegido en la respuesta puede estar ausente de los datos originales.
    Bloquea la alucinación de un número/ticker nuevo sin exigir que el
    modelo repita TODOS los datos que le dimos (Decisión de diseño #4c)."""
    return set(ai_rewrite.protected_tokens(respuesta)) <= datos_tokens


class _ExplainUnavailable(Exception):
    """Señal interna -- cualquier fallo de red/estructura/guard converge
    acá y nunca se propaga fuera de `build_explain_handler` (mismo criterio
    de fallback silencioso que `ai_rewrite.rewrite_parts`)."""


async def _fetch_explanation(
    *,
    clients,
    config: ai_rewrite.OllamaConfig,
    question_code: str,
    datos_del_contexto: dict,
    datos_tokens: set[str],
) -> str:
    """Llama a Ollama, valida estructura + guard de integridad + brevedad.
    Cualquier fallo (red/timeout/HTTP no-2xx/JSON malformado/guard fallido)
    converge en `_ExplainUnavailable` -- nunca se manda una respuesta a
    medio validar (Decisión de diseño #7)."""
    _label, pregunta_fija = _ALL_QUESTIONS[question_code]
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
        # `asyncio.CancelledError` (BaseException) no queda atrapado acá --
        # se propaga normalmente, mismo criterio que `ai_rewrite.py`.
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
        # Anomalía del modelo (no un estado esperado) -- WARNING, distinto
        # de los INFO de arriba, mismo criterio que `ai_rewrite.py`. La
        # respuesta cruda se sanitiza antes de loguearse (hallazgo 7 de
        # `security`, recomendación de logging).
        logger.warning(
            "Explicación generada no pasó el guard de integridad -- "
            "descartada. Respuesta cruda: %s", _sanitize_for_log(respuesta),
        )
        raise _ExplainUnavailable()

    return _enforce_brevity(respuesta)


# --- Handler compartido (Decisión de diseño #8) ------------------------------


def build_explain_handler(clients, rate_limiter, store: ExplanationContextStore) -> CallbackQueryHandler:
    """Un solo `CallbackQueryHandler` para el prefijo `xp:`, compartido por
    ambos flujos -- registrado una sola vez en `bot.py::build_application`.
    `clients`/`rate_limiter` son las MISMAS instancias ya construidas para
    el resto del bot (nunca instancias nuevas)."""

    async def handle_explain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        # Decisión de diseño #7, paso 1: responder el callback INMEDIATO,
        # antes de cualquier validación/llamada a Ollama -- evita que
        # Telegram muestre el ícono de carga colgado en el botón.
        await query.answer("Generando explicación…")

        chat = update.effective_chat
        chat_id = chat.id if chat is not None else None

        raw_data = query.data or ""
        match = _CALLBACK_RE.fullmatch(raw_data)
        if match is None:
            logger.warning(
                "callback_data de xp: malformado: %s", _sanitize_for_log(raw_data)
            )
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_INVALID_MSG)
            return

        context_id, question_code = match.group(1), match.group(2)

        if question_code not in _ALL_QUESTIONS:
            logger.warning(
                "question_code desconocido en callback xp:: %s",
                _sanitize_for_log(question_code),
            )
            await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_INVALID_MSG)
            return

        stored = store.get(context_id)
        if stored is None:
            await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
            return

        table = QUESTIONS_TEXTO_LIBRE if stored.kind == "texto_libre" else QUESTIONS_AVANZADO
        if question_code not in table:
            # question_code válido y conocido, pero de la tabla equivocada
            # para el kind del contexto recuperado (hallazgo 5 de `security`).
            await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
            return

        # Decisión de diseño #7, paso 3: MISMO rate limiter compartido, MISMA
        # clave (`str(chat_id)`) que el resto del bot.
        if not rate_limiter.allow(str(chat_id)):
            await context.bot.send_message(chat_id=chat_id, text=RATE_LIMITED_MSG)
            return

        datos_del_contexto = _build_explain_payload(stored, question_code)
        datos_tokens = set(
            ai_rewrite.protected_tokens(
                json.dumps(datos_del_contexto, ensure_ascii=False, default=str)
            )
        )
        config = clients.ollama_config or ai_rewrite.OllamaConfig(
            enabled=False, base_url="", model="", timeout_seconds=0.0
        )

        try:
            respuesta = await _fetch_explanation(
                clients=clients,
                config=config,
                question_code=question_code,
                datos_del_contexto=datos_del_contexto,
                datos_tokens=datos_tokens,
            )
        except _ExplainUnavailable:
            await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_UNAVAILABLE_MSG)
            return

        # Decisión de diseño #7, paso 5: SIEMPRE un mensaje nuevo, nunca
        # editando el análisis original -- los botones no se quitan.
        texto = f"{ai_rewrite.TRANSPARENCY_USED}\n\n{respuesta}\n\n{DISCLAIMER_NO_ASESORAMIENTO}"
        await context.bot.send_message(chat_id=chat_id, text=texto)

    return CallbackQueryHandler(handle_explain, pattern=r"^xp:")
