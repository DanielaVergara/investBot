"""Tests de `bot.py` — arranque fail-closed, construcción de la `Application`,
logging de librerías ruidosas fijado a WARNING, manejo de Conflict (409).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from investbot import bot, security


def test_configure_logging_fija_httpx_httpcore_telegram_a_warning(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    bot.configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("telegram").level == logging.WARNING


def test_main_falla_sin_allowed_chat_id(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    with pytest.raises(security.ConfigurationError):
        bot.main()


def test_main_falla_sin_telegram_token(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        bot.main()
    assert exc_info.value.code == 1


def test_main_falla_sin_fmp_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        bot.main()
    assert exc_info.value.code == 1


def test_build_application_registra_handlers(tmp_path):
    db_path = str(tmp_path / "bot_test.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_id=12345,
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    assert application is not None
    # El gate de chat_id debe estar en group=-1
    assert -1 in application.handlers
    assert len(application.handlers[-1]) == 1


async def test_on_error_conflict_logueado_sin_token(caplog):
    from telegram.error import Conflict

    context = AsyncMock()
    context.error = Conflict("terminated by other getUpdates request")
    with caplog.at_level(logging.WARNING):
        await bot._on_error(update=None, context=context)
    assert "posible uso concurrente del token detectado" in caplog.text


async def test_on_error_excepcion_generica_se_loguea(caplog):
    context = AsyncMock()
    context.error = RuntimeError("boom")
    with caplog.at_level(logging.ERROR):
        await bot._on_error(update=None, context=context)
    assert "Error no manejado" in caplog.text
