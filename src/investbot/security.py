"""Control de acceso multi-usuario (hasta N chat_id autorizados) + rate-limit
defensivo.

Cubre los criterios de `security`/`architect` (SDD_multiusuario_chat_id.md):

- Fail-closed: `TELEGRAM_ALLOWED_CHAT_ID` acepta uno o varios chat_id
  separados por coma (CSV) — un solo entero sigue siendo válido, backward
  compatible. Si la variable no está seteada, está vacía, o contiene algún
  elemento inválido (no numérico, no-ASCII, o vacío por coma duplicada/
  trailing/leading), `get_allowed_chat_id()` lanza `ConfigurationError`
  (subclase de `RuntimeError`) — el proceso de `bot.py` debe dejar que esa
  excepción se propague y termine el arranque con exit code != 0. Nunca se
  arranca en modo permisivo, y nunca se devuelve un conjunto vacío.
- El filtro se registra en `bot.py` como handler global de máxima prioridad
  (`group=-1`), cubriendo TODOS los tipos de update (mensajes, callback_query,
  edited_message, etc.) vía `TypeHandler(Update, ...)`.
- Valida membership (`chat.id in allowed_chat_ids`) y también
  `chat.type == "private"`.
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


def _ensure_non_empty(ids: frozenset[int]) -> frozenset[int]:
    """Defensa en profundidad (criterio `security` sección 1(D)).

    Chequeo defensivo independiente de la validación por elemento de
    `get_allowed_chat_id`: si el conjunto resultante está vacío justo antes
    de retornar, lanza `ConfigurationError` en vez de devolver `frozenset()`.
    Ningún input de CSV válido llega hoy hasta acá con un conjunto vacío (los
    guards de `get_allowed_chat_id` ya interceptan todos los casos edge
    conocidos) — esta función existe como red de seguridad ante un bug de
    refactor futuro, y para ser testeable de forma aislada sin depender de
    forzar un input de CSV imposible.
    """
    if not ids:
        raise ConfigurationError(
            f"{ENV_VAR_CHAT_ID} resultó en un conjunto vacío de chat_id "
            "autorizados. El bot no puede arrancar sin al menos un chat_id "
            "autorizado (fail-closed intencional)."
        )
    return ids


def get_allowed_chat_id(env: Optional[Mapping[str, str]] = None) -> frozenset[int]:
    """Lee y valida `TELEGRAM_ALLOWED_CHAT_ID`. Fail-closed.

    Acepta uno o varios chat_id separados por coma (CSV) — un solo entero
    (formato desplegado hoy) sigue siendo válido, backward compatible:
    `"12345".split(",")` produce `["12345"]`.

    Lanza `ConfigurationError` (subclase de `RuntimeError`) si la variable no
    está seteada, está vacía, o si algún elemento del CSV es no numérico,
    contiene caracteres no-ASCII, o queda vacío tras `strip()` (coma
    duplicada, trailing o leading) — nunca devuelve un valor "permisivo", ni
    None, ni un `frozenset` vacío.
    """
    source = env if env is not None else os.environ
    raw = source.get(ENV_VAR_CHAT_ID)
    if raw is None or raw.strip() == "":
        raise ConfigurationError(
            f"{ENV_VAR_CHAT_ID} no está seteada. El bot no puede arrancar sin "
            "al menos un chat_id autorizado explícito (fail-closed intencional)."
        )

    ids: set[int] = set()
    for element in raw.split(","):
        stripped = element.strip()
        if stripped == "":
            raise ConfigurationError(
                f"{ENV_VAR_CHAT_ID}={raw!r} contiene un elemento vacío (coma "
                "duplicada, trailing o leading). El bot no puede arrancar con "
                "una configuración de chat_id ambigua (fail-closed intencional)."
            )
        if not stripped.isascii():
            raise ConfigurationError(
                f"{ENV_VAR_CHAT_ID}={raw!r} contiene un elemento con "
                f"caracteres no-ASCII ({stripped!r}). El bot no puede arrancar "
                "con un chat_id autorizado ambiguo (fail-closed intencional)."
            )
        try:
            ids.add(int(stripped))
        except ValueError as exc:
            raise ConfigurationError(
                f"{ENV_VAR_CHAT_ID}={raw!r} contiene un elemento no numérico "
                f"({stripped!r}). El bot no puede arrancar con un chat_id "
                "autorizado ambiguo (fail-closed intencional)."
            ) from exc

    return _ensure_non_empty(frozenset(ids))


def is_authorized_chat(update: Update, allowed_chat_ids: frozenset[int]) -> bool:
    """True solo si el update viene de un chat privado autorizado (membership)."""
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.id not in allowed_chat_ids:
        return False
    if chat.type != "private":
        return False
    return True


def build_chat_id_gate(allowed_chat_ids: frozenset[int]):
    """Construye el handler global (group=-1) que filtra todos los updates.

    Devuelve una coroutine apta para `TypeHandler(Update, gate)` registrada en
    `group=-1` en `bot.py`. Si el update no está autorizado, corta la cadena
    de procesamiento con `ApplicationHandlerStop` — ningún otro handler
    (onboarding, query_handler, ConversationHandler) llega a ejecutarse.
    """

    async def _gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_authorized_chat(update, allowed_chat_ids):
            chat = update.effective_chat
            logger.info("Update aceptado — chat_id autorizado: %s", chat.id)
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
