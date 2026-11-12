"""Tests de `bot.py` — arranque fail-closed, construcción de la `Application`,
logging de librerías ruidosas fijado a WARNING, manejo de Conflict (409).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Update

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
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    assert application is not None
    # El gate de chat_id debe estar en group=-1
    assert -1 in application.handlers
    assert len(application.handlers[-1]) == 1


def test_build_application_acepta_multiples_chat_ids(tmp_path):
    """Caso multi-usuario (SDD_multiusuario_chat_id.md): build_application
    acepta un frozenset de 3 elementos, no solo 1, y construye la Application
    sin error — ejercita el caso de integración bot.py <-> security.py con
    más de un chat_id autorizado."""
    db_path = str(tmp_path / "bot_test3.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({111, 222, 333}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    assert application is not None
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


# ---------------------------------------------------------------------------
# Fix urgente 2026-09-04 — red de seguridad: el usuario SIEMPRE tiene que
# recibir alguna respuesta ante un error no manejado, nunca quedarse mirando
# "🔄 Cargando..." sin ningún mensaje (incidente real: `BadRequest` de
# Telegram al editar el mensaje final de un análisis para ADBE).
# ---------------------------------------------------------------------------


def _update_con_chat(chat_id: int) -> Update:
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


async def test_on_error_excepcion_generica_le_avisa_al_usuario(caplog):
    """El error handler global, ante cualquier excepción no manejada (no
    `Conflict`), le manda al chat que originó el error un mensaje corto y
    genérico -- nunca un traceback ni detalle técnico."""
    context = AsyncMock()
    context.error = RuntimeError("boom")
    update = _update_con_chat(chat_id=12345)

    with caplog.at_level(logging.ERROR):
        await bot._on_error(update=update, context=context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=12345, text=bot.GENERIC_ERROR_MESSAGE
    )
    # El aviso al usuario nunca incluye el texto crudo de la excepción.
    assert "boom" not in bot.GENERIC_ERROR_MESSAGE


async def test_on_error_conflict_no_le_avisa_al_usuario():
    """`Conflict` (409 de Telegram, uso concurrente del token) sigue sin
    generar ningún mensaje al usuario -- es un problema de infraestructura,
    no algo que el usuario pueda "reintentar"."""
    from telegram.error import Conflict

    context = AsyncMock()
    context.error = Conflict("terminated by other getUpdates request")
    update = _update_con_chat(chat_id=12345)

    await bot._on_error(update=update, context=context)

    context.bot.send_message.assert_not_awaited()


async def test_on_error_update_sin_chat_no_intenta_avisar():
    """`update=None` (o sin `effective_chat`) -- no hay a quién avisarle,
    el error handler no intenta `send_message` y no lanza."""
    context = AsyncMock()
    context.error = RuntimeError("boom")

    await bot._on_error(update=None, context=context)

    context.bot.send_message.assert_not_awaited()


async def test_on_error_send_message_tambien_falla_se_loguea_a_warning_sin_propagar(caplog):
    """Red de seguridad del aviso mismo: si `context.bot.send_message`
    también falla (ej. el bot fue bloqueado por el usuario), el error
    handler global no puede romperse por eso -- se loguea a WARNING y se
    descarta en silencio."""
    context = AsyncMock()
    context.error = RuntimeError("boom")
    context.bot.send_message.side_effect = RuntimeError("chat blocked")
    update = _update_con_chat(chat_id=12345)

    with caplog.at_level(logging.WARNING):
        await bot._on_error(update=update, context=context)  # no debe lanzar

    assert "No se pudo avisarle al usuario del error no manejado" in caplog.text


# ---------------------------------------------------------------------------
# SDD_peers_dinamicos_y_eventos_corporativos — FINNHUB_API_KEY/
# SEC_EDGAR_USER_AGENT opcionales, main() arranca sin ellas (mismo patrón
# que FRED_API_KEY).
# ---------------------------------------------------------------------------


def test_build_application_acepta_finnhub_y_sec_edgar_opcionales(tmp_path):
    """Regresión (criterio 1.1 de la spec): build_application con los 2
    parámetros nuevos con default no rompe la construcción existente."""
    db_path = str(tmp_path / "bot_test2.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
        finnhub_api_key="test-finnhub-key",
        sec_edgar_user_agent="InvestBot test@example.com",
    )
    assert application is not None


def test_main_arranca_sin_finnhub_ni_sec_edgar_configuradas(monkeypatch, tmp_path):
    """FINNHUB_API_KEY/SEC_EDGAR_USER_AGENT son opcionales — main() llega
    hasta run_polling sin ellas configuradas (mismo patrón que FRED_API_KEY,
    ninguna aborta el arranque)."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / "bot_main_test.db"))

    from telegram.ext import Application

    run_polling_calls = {"n": 0}

    def fake_run_polling(self, **kwargs):
        run_polling_calls["n"] += 1

    monkeypatch.setattr(Application, "run_polling", fake_run_polling)

    bot.main()
    assert run_polling_calls["n"] == 1


# ---------------------------------------------------------------------------
# SDD_redaccion_ia_ollama.md (migrado por SDD_migracion_gemini.md) — wiring de
# `gemini_client.load_config()` + `httpx.AsyncClient` de Gemini en
# `main()`/`build_application` (grupo J,
# casos 56-57). Mismo patrón best-effort ya usado para Finnhub/SEC EDGAR:
# ninguna combinación de configuración de Gemini aborta el arranque.
# ---------------------------------------------------------------------------


def test_main_arranca_sin_gemini_configurado(monkeypatch, tmp_path):
    """Caso 56: sin `GEMINI_API_KEY` seteada, `main()` llega hasta
    `run_polling` sin abortar -- mismo patrón que Finnhub/SEC EDGAR
    ausentes."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_REWRITE_ENABLED", raising=False)
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / "bot_main_gemini_ausente.db"))

    from telegram.ext import Application

    run_polling_calls = {"n": 0}

    def fake_run_polling(self, **kwargs):
        run_polling_calls["n"] += 1

    monkeypatch.setattr(Application, "run_polling", fake_run_polling)

    bot.main()
    assert run_polling_calls["n"] == 1


def test_main_arranca_con_gemini_enabled_pero_base_url_incompleta(monkeypatch, tmp_path):
    """Caso 57: `GEMINI_REWRITE_ENABLED=true` sin `GEMINI_API_KEY` (config
    incompleta) -- `main()` sigue sin abortar, feature opcional no
    fail-closed (Decisión de diseño #5)."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_REWRITE_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / "bot_main_gemini_incompleto.db"))

    from telegram.ext import Application

    run_polling_calls = {"n": 0}

    def fake_run_polling(self, **kwargs):
        run_polling_calls["n"] += 1

    monkeypatch.setattr(Application, "run_polling", fake_run_polling)

    bot.main()
    assert run_polling_calls["n"] == 1


def test_build_application_acepta_gemini_http_y_config_opcionales(tmp_path):
    """Regresión: `build_application` con el parámetro `gemini_config` (con
    default `None`) no rompe la construcción existente, y con un valor real
    poblado tampoco."""
    from investbot import gemini_client

    db_path = str(tmp_path / "bot_test_gemini.db")
    gemini_config = gemini_client.GeminiConfig(enabled=True, api_key="TEST-DUMMY-KEY-0123456789ABCDEF")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
        gemini_config=gemini_config,
    )
    assert application is not None


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md (ronda 2) — los nuevos CallbackQueryHandler (`esc:`/
# `vent:`) no interceptan updates del ConversationHandler de onboarding
# (`^onb:`) — regex disjuntos, criterio explícito de `architect`/`security`.
# ---------------------------------------------------------------------------


def test_nuevos_handlers_esc_vent_no_interceptan_onboarding(tmp_path):
    """Test de regresión explícito (no solo inspección visual del código):
    los patrones `^esc:`/`^vent:` de `query_handler.build_query_handlers`
    son disjuntos de `^onb:` — un `callback_data` de onboarding nunca
    matchea contra ninguno de los 2 patrones nuevos."""
    db_path = str(tmp_path / "bot_test_disjuntos.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    import re

    onb_callback_data = "onb:0:10"
    patrones_nuevos = []
    for handlers_del_grupo in application.handlers.values():
        for handler in handlers_del_grupo:
            pattern = getattr(handler, "pattern", None)
            if pattern is not None and pattern.pattern in (r"^esc:", r"^vent:"):
                patrones_nuevos.append(pattern)

    assert len(patrones_nuevos) == 2
    for pattern in patrones_nuevos:
        assert re.match(pattern, onb_callback_data) is None


# ---------------------------------------------------------------------------
# SDD_explicaciones_interactivas_ollama.md -- wiring del ExplanationContextStore
# compartido + registro del CallbackQueryHandler `xp:` (Decisión de diseño #3/#8).
# ---------------------------------------------------------------------------


def test_build_application_registra_handler_xp_en_group_0(tmp_path):
    """El `CallbackQueryHandler` de `xp:` se registra en `group=0` (default),
    DESPUÉS del gate (`group=-1`) -- mismo criterio que `esc:`/`vent:`/`avanzado`."""
    db_path = str(tmp_path / "bot_test_xp.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    import re

    patrones_xp = [
        getattr(h, "pattern", None)
        for h in application.handlers.get(0, [])
        if getattr(h, "pattern", None) is not None and h.pattern.pattern == r"^xp:"
    ]
    assert len(patrones_xp) == 1
    assert re.match(patrones_xp[0], "xp:a1b2c3d4:vf") is not None
    assert re.match(patrones_xp[0], "onb:0:10") is None
    assert -1 in application.handlers  # el gate sigue en su propio grupo, antes


def test_build_application_comparte_una_sola_instancia_de_explanation_store(tmp_path, monkeypatch):
    """SDD_explicaciones_interactivas_ollama.md, Decisión de diseño #3/#8: un
    solo `ExplanationContextStore()` construido en `build_application`, MISMA
    instancia inyectada en `query_handler`, `advanced_command` y el
    `CallbackQueryHandler` compartido de `xp:` -- verificado por identidad de
    objeto, con espías sobre los 3 builders. Mismos `clients`/`rate_limiter`
    en los 3 también (mismo criterio ya exigido para `/avanzado`)."""
    from investbot import ai_explain, advanced_command, query_handler

    captured: dict = {}

    original_build_query_handlers = query_handler.build_query_handlers

    def spy_build_query_handlers(get_conn, clients, rate_limiter, explanation_store=None):
        captured["qh_store"] = explanation_store
        captured["qh_clients"] = clients
        captured["qh_rate_limiter"] = rate_limiter
        return original_build_query_handlers(get_conn, clients, rate_limiter, explanation_store)

    monkeypatch.setattr(query_handler, "build_query_handlers", spy_build_query_handlers)

    original_build_advanced = advanced_command.build_advanced_command_handler

    def spy_build_advanced(clients, rate_limiter, explanation_store=None):
        captured["adv_store"] = explanation_store
        captured["adv_clients"] = clients
        captured["adv_rate_limiter"] = rate_limiter
        return original_build_advanced(clients, rate_limiter, explanation_store)

    monkeypatch.setattr(advanced_command, "build_advanced_command_handler", spy_build_advanced)

    original_build_explain = ai_explain.build_explain_handler

    def spy_build_explain(clients, rate_limiter, store):
        captured["xp_store"] = store
        captured["xp_clients"] = clients
        captured["xp_rate_limiter"] = rate_limiter
        return original_build_explain(clients, rate_limiter, store)

    monkeypatch.setattr(ai_explain, "build_explain_handler", spy_build_explain)

    db_path = str(tmp_path / "bot_test_shared_store.db")
    bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )

    assert isinstance(captured["xp_store"], ai_explain.ExplanationContextStore)
    assert captured["qh_store"] is captured["adv_store"] is captured["xp_store"]
    assert captured["qh_clients"] is captured["adv_clients"] is captured["xp_clients"]
    assert captured["qh_rate_limiter"] is captured["adv_rate_limiter"] is captured["xp_rate_limiter"]


def test_main_arranca_con_finnhub_y_sec_edgar_configuradas(monkeypatch, tmp_path):
    """Caso feliz: con ambas variables configuradas, main() también llega
    hasta run_polling sin abortar."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub-key")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "InvestBot test@example.com")
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / "bot_main_test2.db"))

    from telegram.ext import Application

    run_polling_calls = {"n": 0}

    def fake_run_polling(self, **kwargs):
        run_polling_calls["n"] += 1

    monkeypatch.setattr(Application, "run_polling", fake_run_polling)

    bot.main()
    assert run_polling_calls["n"] == 1


# ---------------------------------------------------------------------------
# SDD_migracion_gemini.md (F2) — cableado de `gemini_client.load_config()`
# en `main()`: CS-21, CA-47 (parte de F2), CA-48.
# ---------------------------------------------------------------------------

_DUMMY_GEMINI_KEY = "TEST-DUMMY-KEY-0123456789ABCDEF"
# `build_application` emite el PTBUserWarning del ConversationHandler de
# onboarding (preexistente); los tests nuevos no suman al conteo de warnings.
_SIN_WARNING_PTB = pytest.mark.filterwarnings("ignore::telegram.warnings.PTBUserWarning")


def _env_main_base(monkeypatch, tmp_path, name):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    for var in (
        "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_THINKING_LEVEL",
        "GEMINI_TIMEOUT_SECONDS", "GEMINI_REWRITE_ENABLED",
        "GEMINI_REWRITE_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / name))


def _spy_main(monkeypatch):
    """Espía `build_application` y `gemini_client.load_config`; no hace polling."""
    from telegram.ext import Application

    captured: dict = {"build": None, "load_config_calls": 0}
    real_build = bot.build_application
    real_load = bot.gemini_client.load_config

    def spy_build(**kwargs):
        captured["build"] = kwargs
        return real_build(**kwargs)

    def spy_load(*args, **kwargs):
        captured["load_config_calls"] += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(bot, "build_application", spy_build)
    monkeypatch.setattr(bot.gemini_client, "load_config", spy_load)
    monkeypatch.setattr(Application, "run_polling", lambda self, **kw: None)
    return captured


def test_cs21_allowlist_invalida_aborta_antes_de_cargar_gemini(monkeypatch, tmp_path):
    _env_main_base(monkeypatch, tmp_path, "cs21_a.db")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "no-es-un-numero")
    monkeypatch.setenv("GEMINI_API_KEY", _DUMMY_GEMINI_KEY)
    captured = _spy_main(monkeypatch)
    with pytest.raises(security.ConfigurationError):
        bot.main()
    assert captured["load_config_calls"] == 0
    assert captured["build"] is None


@pytest.mark.parametrize("faltante", ["TELEGRAM_BOT_TOKEN", "FMP_API_KEY"])
def test_cs21_token_o_fmp_faltante_aborta_antes_de_cargar_gemini(monkeypatch, tmp_path, faltante):
    _env_main_base(monkeypatch, tmp_path, "cs21_b.db")
    monkeypatch.setenv("GEMINI_API_KEY", _DUMMY_GEMINI_KEY)
    monkeypatch.delenv(faltante)
    captured = _spy_main(monkeypatch)
    with pytest.raises(SystemExit):
        bot.main()
    assert captured["load_config_calls"] == 0


@_SIN_WARNING_PTB
def test_cs21_allowlist_valida_con_gemini_invalido_arranca_y_loguea_error_sin_valor(
    monkeypatch, tmp_path, caplog
):
    _env_main_base(monkeypatch, tmp_path, "cs21_c.db")
    valor_malo = "CANARY-MODELO-INVALIDO-NO-FILTRAR"
    monkeypatch.setenv("GEMINI_API_KEY", _DUMMY_GEMINI_KEY)
    monkeypatch.setenv("GEMINI_MODEL", valor_malo)
    caplog.set_level(logging.INFO, logger="investbot")
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["load_config_calls"] == 1
    assert captured["build"]["gemini_http"] is None
    assert captured["build"]["gemini_config"].enabled is False
    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("GEMINI_MODEL" in r.getMessage() for r in errores)
    assert valor_malo not in caplog.text
    assert _DUMMY_GEMINI_KEY not in caplog.text


@_SIN_WARNING_PTB
def test_ca47_sin_key_arranca_deshabilitado_sin_cliente_http(monkeypatch, tmp_path, caplog):
    _env_main_base(monkeypatch, tmp_path, "ca47_a.db")
    caplog.set_level(logging.INFO, logger="investbot")
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["gemini_http"] is None
    assert captured["build"]["gemini_config"].enabled is False
    assert "Gemini deshabilitado (GEMINI_API_KEY ausente)" in caplog.text
    assert not any(r.levelno >= logging.ERROR and "Gemini" in r.getMessage() for r in caplog.records)


@_SIN_WARNING_PTB
def test_ca47_con_key_valida_y_flag_crea_un_solo_cliente_y_loguea_habilitado(
    monkeypatch, tmp_path, caplog
):
    _env_main_base(monkeypatch, tmp_path, "ca47_b.db")
    monkeypatch.setenv("GEMINI_API_KEY", _DUMMY_GEMINI_KEY)
    monkeypatch.setenv("GEMINI_REWRITE_ENABLED", "true")
    caplog.set_level(logging.INFO, logger="investbot")
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["load_config_calls"] == 1
    assert captured["build"]["gemini_http"] is not None
    assert captured["build"]["gemini_config"].enabled is True
    assert "Gemini habilitado model=gemini-3.5-flash-lite rewrite=on" in caplog.text
    assert caplog.text.count("Gemini habilitado") == 1  # main() no duplica el log
    assert _DUMMY_GEMINI_KEY not in caplog.text


@_SIN_WARNING_PTB
def test_ca47_con_key_y_sin_flag_loguea_rewrite_off(monkeypatch, tmp_path, caplog):
    _env_main_base(monkeypatch, tmp_path, "ca47_c.db")
    monkeypatch.setenv("GEMINI_API_KEY", _DUMMY_GEMINI_KEY)
    caplog.set_level(logging.INFO, logger="investbot")
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["gemini_http"] is not None
    assert "Gemini habilitado model=gemini-3.5-flash-lite rewrite=off" in caplog.text


@_SIN_WARNING_PTB
def test_ca47_key_presente_pero_invalida_arranca_deshabilitado_con_error_sin_valor(
    monkeypatch, tmp_path, caplog
):
    _env_main_base(monkeypatch, tmp_path, "ca47_d.db")
    key_mala = "CANARY-KEY'CON-COMILLA-INVALIDA"
    monkeypatch.setenv("GEMINI_API_KEY", key_mala)
    caplog.set_level(logging.INFO, logger="investbot")
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["gemini_http"] is None
    assert "GEMINI_KEY_INVALID" in caplog.text
    assert key_mala not in caplog.text


@_SIN_WARNING_PTB
def test_ca48_build_application_acepta_gemini_http_y_config_opcionales(tmp_path):
    import httpx

    from investbot import gemini_client

    gemini_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    config = gemini_client.GeminiConfig(enabled=True, api_key=_DUMMY_GEMINI_KEY)
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=str(tmp_path / "ca48.db"),
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
        gemini_http=gemini_http,
        gemini_config=config,
    )
    assert application is not None
    sin_gemini = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=str(tmp_path / "ca48b.db"),
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    assert sin_gemini is not None


@_SIN_WARNING_PTB
def test_bloque_dunder_main_ejecuta_main_y_llega_a_run_polling(monkeypatch, tmp_path):
    """Cubre `if __name__ == "__main__": main()` (preexistente, sin cubrir):
    se ejecuta el archivo como script con `run_polling` parcheado, sin red."""
    import runpy

    from telegram.ext import Application

    _env_main_base(monkeypatch, tmp_path, "dunder_main.db")
    llamadas = []
    monkeypatch.setattr(Application, "run_polling", lambda self, **kw: llamadas.append(1))
    runpy.run_path(bot.__file__, run_name="__main__")
    assert llamadas == [1]
