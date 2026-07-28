"""Tests de `security.py` — fail-closed, filtro global de chat_id, rate-limit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop

from investbot import security


# ---------------------------------------------------------------------------
# Fail-closed — TELEGRAM_ALLOWED_CHAT_ID ausente/inválida
# ---------------------------------------------------------------------------


def test_get_allowed_chat_id_variable_ausente():
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={})


def test_get_allowed_chat_id_variable_vacia():
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "  "})


def test_get_allowed_chat_id_no_entero():
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "no-es-un-numero"})


def test_get_allowed_chat_id_valido():
    assert security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "12345"}) == 12345


def test_get_allowed_chat_id_valido_con_espacios():
    assert security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": " 12345 "}) == 12345


# ---------------------------------------------------------------------------
# is_authorized_chat — chat_id + tipo "private"
# ---------------------------------------------------------------------------


def _update(chat_id, chat_type):
    update = SimpleNamespace()
    update.effective_chat = SimpleNamespace(id=chat_id, type=chat_type) if chat_id is not None else None
    return update


def test_is_authorized_chat_ok():
    assert security.is_authorized_chat(_update(111, "private"), 111) is True


def test_is_authorized_chat_id_distinto():
    assert security.is_authorized_chat(_update(222, "private"), 111) is False


def test_is_authorized_chat_no_privado():
    """Aunque el chat_id coincida, si no es un chat privado se rechaza."""
    assert security.is_authorized_chat(_update(111, "group"), 111) is False


def test_is_authorized_chat_sin_chat():
    assert security.is_authorized_chat(_update(None, None), 111) is False


# ---------------------------------------------------------------------------
# build_chat_id_gate — handler global (group=-1)
# ---------------------------------------------------------------------------


async def test_gate_autorizado_no_lanza():
    gate = security.build_chat_id_gate(111)
    update = _update(111, "private")
    update.callback_query = None
    result = await gate(update, context=SimpleNamespace())
    assert result is None  # deja pasar, no corta la cadena


async def test_gate_no_autorizado_lanza_stop():
    gate = security.build_chat_id_gate(111)
    update = _update(222, "private")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


async def test_gate_no_privado_lanza_stop():
    gate = security.build_chat_id_gate(111)
    update = _update(111, "group")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


async def test_gate_responde_callback_query_no_autorizado():
    gate = security.build_chat_id_gate(111)
    update = _update(222, "private")
    update.callback_query = SimpleNamespace(answer=AsyncMock())
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())
    update.callback_query.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# Rate limiter en memoria
# ---------------------------------------------------------------------------


def test_rate_limiter_permite_hasta_el_maximo():
    limiter = security.InMemoryRateLimiter(max_requests=3, window_seconds=60.0)
    key = "chat:111"
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is False  # 4ta excede el máximo


def test_rate_limiter_ventanas_independientes_por_key():
    limiter = security.InMemoryRateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("chat:111") is True
    assert limiter.allow("chat:222") is True  # otra key, otra ventana
    assert limiter.allow("chat:111") is False


def test_rate_limiter_libera_tras_expirar_ventana():
    limiter = security.InMemoryRateLimiter(max_requests=1, window_seconds=0.01)
    key = "chat:111"
    assert limiter.allow(key) is True
    import time

    time.sleep(0.02)
    assert limiter.allow(key) is True


# ---------------------------------------------------------------------------
# Log de Conflict (409) — nunca incluye el token
# ---------------------------------------------------------------------------


def test_log_conflict_error_no_incluye_token(caplog):
    import logging

    logger = logging.getLogger("test_conflict_logger")
    with caplog.at_level(logging.WARNING):
        security.log_conflict_error(logger)
    assert "posible uso concurrente del token detectado" in caplog.text
