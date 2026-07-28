"""Control de acceso de un solo usuario + rate-limit defensivo.

Cubre los criterios de `security` (Iter-1, sección 1 y 5 de la spec):

- Fail-closed: si `TELEGRAM_ALLOWED_CHAT_ID` no está seteada o no es un entero
  válido, `get_allowed_chat_id()` lanza `RuntimeError` — el proceso de `bot.py`
  debe dejar que esa excepción se propague y termine el arranque con exit code
  != 0. Nunca se arranca en modo permisivo.
- El filtro se registra en `bot.py` como handler global de máxima prioridad
  (`group=-1`), cubriendo TODOS los tipos de update (mensajes, callback_query,
  edited_message, etc.) vía `TypeHandler(Update, ...)`.
- Valida también `chat.type == "private"`.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Mapping, Optional

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)

ENV_VAR_CHAT_ID = "TELEGRAM_ALLOWED_CHAT_ID"


class ConfigurationError(RuntimeError):
    """El bot no puede arrancar por configuración de seguridad ausente/inválida."""


def get_allowed_chat_id(env: Optional[Mapping[str, str]] = None) -> int:
    """Lee y valida `TELEGRAM_ALLOWED_CHAT_ID`. Fail-closed.

    Lanza `ConfigurationError` (subclase de `RuntimeError`) si la variable no
    está seteada, está vacía, o no es parseable como entero — nunca devuelve
    un valor "permisivo" ni None.
    """
    source = env if env is not None else os.environ
    raw = source.get(ENV_VAR_CHAT_ID)
    if raw is None or raw.strip() == "":
        raise ConfigurationError(
            f"{ENV_VAR_CHAT_ID} no está seteada. El bot no puede arrancar sin "
            "un chat_id autorizado explícito (fail-closed intencional)."
        )
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"{ENV_VAR_CHAT_ID}={raw!r} no es un entero válido. El bot no puede "
            "arrancar con un chat_id autorizado ambiguo (fail-closed intencional)."
        ) from exc


def is_authorized_chat(update: Update, allowed_chat_id: int) -> bool:
    """True solo si el update viene del chat privado autorizado."""
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.id != allowed_chat_id:
        return False
    if chat.type != "private":
        return False
    return True


def build_chat_id_gate(allowed_chat_id: int):
    """Construye el handler global (group=-1) que filtra todos los updates.

    Devuelve una coroutine apta para `TypeHandler(Update, gate)` registrada en
    `group=-1` en `bot.py`. Si el update no está autorizado, corta la cadena
    de procesamiento con `ApplicationHandlerStop` — ningún otro handler
    (onboarding, query_handler, ConversationHandler) llega a ejecutarse.
    """

    async def _gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_authorized_chat(update, allowed_chat_id):
            return
        chat = update.effective_chat
        chat_id_repr = chat.id if chat is not None else "desconocido"
        logger.warning("Update rechazado — chat_id no autorizado: %s", chat_id_repr)
        # Responder al callback_query para no dejar el spinner de Telegram
        # colgado en el cliente — no revela nada sensible.
        if update.callback_query is not None:
            try:
                await update.callback_query.answer()
            except Exception:  # pragma: no cover - best-effort, nunca debe crashear
                pass
        raise ApplicationHandlerStop

    return _gate


class InMemoryRateLimiter:
    """Rate-limit defensivo en memoria (sección 5 de `security`, no bloqueante).

    Protege el cupo de 250 req/día de FMP contra bugs propios (doble tap de un
    botón, loop accidental) y, en menor medida, contra un token filtrado que
    igual pasara el filtro de chat_id. No requiere Redis — un solo proceso,
    un solo usuario.
    """

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._events: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._events.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True


def log_conflict_error(logger_: logging.Logger) -> None:
    """Loguea un 409 Conflict de getUpdates como posible señal de token filtrado.

    Nunca incluye el token en el mensaje — ver sección 5 de `security`.
    """
    logger_.warning(
        "posible uso concurrente del token detectado (409 Conflict en getUpdates)"
    )
