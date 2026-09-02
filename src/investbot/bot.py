"""Entrypoint — `Application` en modo long polling (Decisión de diseño #1).

Sin webhook, sin puerto expuesto, sin ruta en Traefik. El proceso falla al
arrancar (fail-closed) si `TELEGRAM_ALLOWED_CHAT_ID` no está seteada o
contiene algún elemento inválido — ver `security.get_allowed_chat_id()`.
"""

from __future__ import annotations

import logging
import os
import sys

import httpx
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, TypeHandler

from investbot import advanced_command, ai_explain, ai_rewrite, db, onboarding, query_handler, security

logger = logging.getLogger(__name__)


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


async def _on_error(update: object, context) -> None:
    error = context.error
    if isinstance(error, Conflict):
        security.log_conflict_error(logger)
        return
    logger.exception("Error no manejado procesando un update", exc_info=error)


def build_application(
    *,
    telegram_token: str,
    allowed_chat_ids: frozenset[int],
    db_path: str,
    fmp_api_key: str,
    fred_api_key: str | None,
    finnhub_api_key: str | None = None,
    sec_edgar_user_agent: str | None = None,
    ollama_http: httpx.AsyncClient | None = None,
    ollama_config: ai_rewrite.OllamaConfig | None = None,
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
    sec_edgar_http = httpx.AsyncClient()
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
        ollama_http=ollama_http,
        ollama_config=ollama_config,
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
    db_path = os.environ.get("INVESTBOT_DB_PATH", "/data/investbot.db")

    # SDD_redaccion_ia_ollama.md — mismo patrón best-effort que Finnhub/SEC
    # EDGAR: `load_config()` nunca lanza, y sin configuración completa
    # (`OLLAMA_REWRITE_ENABLED`/`OLLAMA_BASE_URL`) resuelve a `enabled=False`
    # sin abortar el arranque. Solo se abre un `httpx.AsyncClient` dedicado
    # si la feature está efectivamente habilitada.
    ollama_config = ai_rewrite.load_config()
    ollama_http = httpx.AsyncClient() if ollama_config.enabled else None

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
        ollama_http=ollama_http,
        ollama_config=ollama_config,
    )

    allowed_chat_ids_repr = ",".join(str(chat_id) for chat_id in sorted(allowed_chat_ids))
    logger.info(
        "InvestBot arrancando en modo long polling (chat_id=%s)", allowed_chat_ids_repr
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
