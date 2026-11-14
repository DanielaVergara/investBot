"""Entrypoint — `Application` en modo long polling (Decisión de diseño #1).

Sin webhook, sin puerto expuesto, sin ruta en Traefik. El proceso falla al
arrancar (fail-closed) si `TELEGRAM_ALLOWED_CHAT_ID` no está seteada o
contiene algún elemento inválido — ver `security.get_allowed_chat_id()`.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import httpx
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, TypeHandler

from investbot import (
    advanced_command,
    ai_explain,
    db,
    edgar_provider,
    fmp_provider,
    fundamentals,
    gemini_client,
    market_data,
    onboarding,
    query_handler,
    sec_edgar_client,
    security,
)

logger = logging.getLogger(__name__)


# CS-19 (a): mismo criterio que `gemini_client._KEY_RE` -- solo ASCII visible
# (sin CR/LF ni espacios), longitud acotada, sin comillas/barra invertida.
_FINNHUB_KEY_RE = re.compile(r"[\x21-\x7E]{16,256}", re.ASCII)
_FINNHUB_KEY_FORBIDDEN = frozenset("\"'`\\")

# CS-23: User-Agent de la SEC `<app> <email>`; sin barra ni paréntesis.
_SEC_UA_RE = re.compile(
    r"[A-Za-z0-9._-]+(?: [A-Za-z0-9._-]+)* [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    re.ASCII,
)
_SEC_UA_PLACEHOLDER_DOMAIN_SUFFIXES = ("example.com", "example.org", "ejemplo.com", "localhost")


def _valid_finnhub_key(key: str) -> bool:
    return _FINNHUB_KEY_RE.fullmatch(key) is not None and not (
        _FINNHUB_KEY_FORBIDDEN & set(key)
    )


def _valid_sec_user_agent(ua: str) -> bool:
    """Validación estricta del UA de la SEC. SOLO se aplica en `main()`
    (CS-23): `Clients`, `query_handler` y `build_application` no validan."""
    if _SEC_UA_RE.fullmatch(ua) is None:
        return False
    if "tu-correo" in ua.lower():
        return False
    domain = ua.rsplit("@", 1)[1].lower()
    return not domain.endswith(_SEC_UA_PLACEHOLDER_DOMAIN_SUFFIXES)


def configure_logging() -> None:
    """Loggers de httpx/httpcore/telegram fijados a WARNING en producción
    (criterio de `security`, sección 2) — nunca DEBUG/INFO por defecto, porque
    filtrarían el token de Telegram (va en el path de la URL) y las API keys
    de FMP/FRED (van como query param) en texto plano.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy_logger in ("httpx", "httpcore", "telegram"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# Mensaje genérico ante cualquier error no manejado (fix urgente 2026-09-04,
# incidente de producción: un `BadRequest` de Telegram al editar el mensaje
# final de un análisis dejaba al usuario mirando "🔄 Cargando..." para
# siempre, sin ninguna respuesta). Corto, sin tecnicismos, nunca un
# traceback ni detalle interno — mismo estándar de `security` que ya rige
# `transparency_line` (nunca revelar infraestructura).
GENERIC_ERROR_MESSAGE = "⚠️ Algo salió mal procesando tu pedido. Probá de nuevo en un rato."


async def _notify_user_of_error(update: object, context) -> None:
    """Best-effort: si se puede identificar el chat que originó el error no
    manejado, le avisa con `GENERIC_ERROR_MESSAGE`. Nunca lanza — el
    llamador (`_on_error`) es el manejador de errores global, así que un
    fallo acá (chat_id no disponible, `send_message` también falla, lo que
    sea) se loguea a WARNING y se descarta en silencio, nunca puede tumbar
    el error handler mismo."""
    try:
        chat_id = None
        if isinstance(update, Update) and update.effective_chat is not None:
            chat_id = update.effective_chat.id
        if chat_id is None:
            return
        await context.bot.send_message(chat_id=chat_id, text=GENERIC_ERROR_MESSAGE)
    except Exception:
        logger.warning(
            "No se pudo avisarle al usuario del error no manejado", exc_info=True
        )


async def _on_error(update: object, context) -> None:
    error = context.error
    if isinstance(error, Conflict):
        security.log_conflict_error(logger)
        return
    logger.exception("Error no manejado procesando un update", exc_info=error)
    await _notify_user_of_error(update, context)


def build_application(
    *,
    telegram_token: str,
    allowed_chat_ids: frozenset[int],
    db_path: str,
    fmp_api_key: str,
    fred_api_key: str | None,
    finnhub_api_key: str | None = None,
    sec_edgar_user_agent: str | None = None,
    gemini_http: httpx.AsyncClient | None = None,
    gemini_config: gemini_client.GeminiConfig | None = None,
) -> Application:
    application = Application.builder().token(telegram_token).build()

    def get_conn():
        return db.get_connection(db_path)

    # Handler global de máxima prioridad — cubre TODOS los tipos de update
    # (mensajes, callback_query, etc.) antes de que lleguen a cualquier otro
    # handler (criterio de `security`, sección 1).
    application.add_handler(
        TypeHandler(Update, security.build_chat_id_gate(allowed_chat_ids)), group=-1
    )

    application.add_handler(onboarding.build_onboarding_handler(get_conn))

    fmp_http = httpx.AsyncClient()
    fred_http = httpx.AsyncClient()
    treasury_gov_http = httpx.AsyncClient()
    finnhub_http = httpx.AsyncClient()
    sec_edgar_http = sec_edgar_client.build_sec_http_client()
    # F1b-3 (SDD_fuentes_datos_gratuitas.md 4.2): cadena EDGAR -> FMP, solo con
    # un User-Agent válido (ya validado en `main()`). Sin él `fundamentals`
    # queda en `None` y `query_handler` arma la cadena solo-FMP de siempre.
    # EDGAR comparte el cliente `sec_edgar_http` con el 8-K.
    fundamentals_service = None
    if sec_edgar_user_agent:
        fundamentals_service = fundamentals.FundamentalsService(
            [
                edgar_provider.EdgarProvider(sec_edgar_http, sec_edgar_user_agent),
                fmp_provider.FmpProvider(fmp_http, fmp_api_key),
            ]
        )
    # F2b (SDD_fuentes_datos_gratuitas.md 4.1, 13.4): cotización/perfil/beta/52
    # semanas de Finnhub y VIX de FRED con respaldo FMP por bloque, opt-in por
    # `FINNHUB_API_KEY` (ya validada en `main()`): sin key válida `market_data`
    # queda en `None` y el flujo es el solo-FMP de siempre. Sin persistencia.
    market_service = None
    if finnhub_api_key:
        market_service = market_data.MarketDataService(
            finnhub_http=finnhub_http,
            finnhub_api_key=finnhub_api_key,
            fred_http=fred_http,
            fred_api_key=fred_api_key,
        )
    clients = query_handler.Clients(
        fmp_http=fmp_http,
        fred_http=fred_http,
        treasury_gov_http=treasury_gov_http,
        fmp_api_key=fmp_api_key,
        fred_api_key=fred_api_key,
        finnhub_http=finnhub_http,
        finnhub_api_key=finnhub_api_key,
        sec_edgar_http=sec_edgar_http,
        sec_edgar_user_agent=sec_edgar_user_agent,
        gemini_http=gemini_http,
        gemini_config=gemini_config,
        fundamentals=fundamentals_service,
        market_data=market_service,
    )
    rate_limiter = security.InMemoryRateLimiter(max_requests=10, window_seconds=60.0)

    # SDD_explicaciones_interactivas_ollama.md, Decisión de diseño #3 — un
    # solo `ExplanationContextStore()` construido acá, MISMA instancia
    # inyectada en ambos flujos y en el `CallbackQueryHandler` compartido de
    # `xp:` (Decisión de diseño #8), igual criterio que `clients`/`rate_limiter`.
    explanation_store = ai_explain.ExplanationContextStore()

    for handler in query_handler.build_query_handlers(
        get_conn, clients, rate_limiter, explanation_store
    ):
        application.add_handler(handler)

    # SDD_analisis_fundamental_avanzado.md — MISMA instancia de `clients`/
    # `rate_limiter` ya construida arriba, reusada sin cambios (hallazgo 2 de
    # `security`: el balde de rate-limit debe ser el mismo balde compartido,
    # nunca uno nuevo/namespaceado).
    application.add_handler(
        advanced_command.build_advanced_command_handler(clients, rate_limiter, explanation_store)
    )

    # SDD_explicaciones_interactivas_ollama.md, Decisión de diseño #8 — un
    # solo `CallbackQueryHandler` para el prefijo `xp:`, compartido por
    # ambos flujos (el `ExplanationContext.kind` guardado decide el prompt/
    # datos, no el módulo que lo generó). MISMOS `clients`/`rate_limiter` ya
    # construidos arriba — ninguna instancia nueva.
    application.add_handler(
        ai_explain.build_explain_handler(clients, rate_limiter, explanation_store)
    )

    application.add_error_handler(_on_error)

    return application


def main() -> None:
    configure_logging()

    # Fail-closed: si esto lanza, el proceso termina con traceback + exit != 0.
    allowed_chat_ids = security.get_allowed_chat_id()

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN no está seteada. Abortando.")
        sys.exit(1)

    fmp_api_key = os.environ.get("FMP_API_KEY")
    if not fmp_api_key:
        logger.error("FMP_API_KEY no está seteada. Abortando.")
        sys.exit(1)

    fred_api_key = os.environ.get("FRED_API_KEY")
    # Opcionales — ninguna de las 2 aborta el arranque si falta (mismo
    # patrón que FRED_API_KEY): sin ellas, el bot funciona igual, solo se
    # omite la feature correspondiente (peers dinámicos / eventos
    # corporativos), nunca un crash.
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
    sec_edgar_user_agent = os.environ.get("SEC_EDGAR_USER_AGENT")
    # Valores presentes pero inválidos: la feature queda deshabilitada y se
    # deja un ERROR SIN el valor (nunca se loguea la key ni el UA).
    if finnhub_api_key and not _valid_finnhub_key(finnhub_api_key):
        logger.error("Finnhub deshabilitado: FINNHUB_API_KEY con formato inválido.")
        finnhub_api_key = None
    if sec_edgar_user_agent and not _valid_sec_user_agent(sec_edgar_user_agent):
        logger.error(
            "SEC EDGAR deshabilitado: SEC_EDGAR_USER_AGENT inválido "
            "(formato esperado: '<app> <email>', sin barra ni dominio de ejemplo)."
        )
        sec_edgar_user_agent = None
    db_path = os.environ.get("INVESTBOT_DB_PATH", "/data/investbot.db")

    # SDD_migracion_gemini.md (CS-21) — se carga DESPUÉS de validar allowlist,
    # token y FMP. `load_config()` nunca lanza y ya loguea el estado de
    # arranque (habilitado/deshabilitado, sin valores). Solo se abre un
    # `httpx.AsyncClient` dedicado si Gemini está efectivamente habilitado.
    gemini_config = gemini_client.load_config()
    gemini_http = httpx.AsyncClient() if gemini_config.enabled else None

    conn = db.get_connection(db_path)
    db.init_db(conn)
    conn.close()

    application = build_application(
        telegram_token=telegram_token,
        allowed_chat_ids=allowed_chat_ids,
        db_path=db_path,
        fmp_api_key=fmp_api_key,
        fred_api_key=fred_api_key,
        finnhub_api_key=finnhub_api_key,
        sec_edgar_user_agent=sec_edgar_user_agent,
        gemini_http=gemini_http,
        gemini_config=gemini_config,
    )

    allowed_chat_ids_repr = ",".join(str(chat_id) for chat_id in sorted(allowed_chat_ids))
    logger.info(
        "InvestBot arrancando en modo long polling (chat_id=%s)", allowed_chat_ids_repr
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
