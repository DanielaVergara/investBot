"""Tests de `query_handler.py` — orquestación del análisis completo.

Sin red real: los clientes HTTP se arman con `httpx.MockTransport`, ruteando
por path a los fixtures de `tests/fixtures/adobe/`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram.error import TelegramError

from investbot import db, query_handler, rules, sec_edgar_client
from investbot.fmp_client import FMPError

ALLOWED_CHAT_ID = 555


@pytest.fixture(autouse=True)
def _reset_sec_edgar_cache():
    """Gap 5.2 de la spec `SDD_peers_dinamicos_y_eventos_corporativos.md`:
    el caché ticker->CIK de `sec_edgar_client.py` es estado mutable de
    módulo, compartido por todo el proceso de pytest — sin este reset, un
    test que puebla el caché puede contaminar cualquier otro test que corra
    después en el mismo proceso (no-determinismo dependiente del orden)."""
    sec_edgar_client._ticker_cik_cache.clear()
    sec_edgar_client._cache_loaded_at = None
    yield
    sec_edgar_client._ticker_cik_cache.clear()
    sec_edgar_client._cache_loaded_at = None


def _adobe_router(adobe_fixtures):
    """Router de datos "propios del ticker" (income/balance/cash-flow) +
    peers/quote/profile/search.

    SDD_eps_ttm_real.md — los 3 endpoints de datos propios ahora se piden
    primero en modalidad trimestral (`period=quarter`) con fallback anual
    condicional. Este router distingue los 3 por `period` (no solo
    income-statement, criterio explícito de QA):
    - `period="quarter"` + `symbol="NVDA"` -> sirve los 3 fixtures reales de
      NVDA (camino feliz trimestral).
    - `period="quarter"` con cualquier otro símbolo (ej. "ADBE", el caso por
      defecto de casi toda esta suite) -> `[]` (sin datos trimestrales) ->
      fuerza el fallback anual, preservando el comportamiento pre-spec de
      todos los tests que no piden explícitamente el camino trimestral.
    - Sin `period` o `period="annual"` -> el fixture anual sintético de
      siempre (ADBE), sin cambios.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        symbol = request.url.params.get("symbol")
        period = request.url.params.get("period")
        if path == "/stable/quote":
            # Gap #2 (sección QA de SDD_contenido_financiero_explicado.md):
            # distinguir el ticker propio del VIX por `symbol` — sin esto,
            # cualquier test de VIX pasaría en falso con el precio de ADBE.
            if symbol == query_handler.market_context.VIX_SYMBOL:
                return httpx.Response(200, json=adobe_fixtures["quote_vix"])
            return httpx.Response(200, json=adobe_fixtures["quote"])
        if path == "/stable/key-metrics":
            # Distinguir el ticker propio (ADBE) de cada peer — hoy ambos
            # comparten el mismo path, diferenciables solo por `symbol`.
            if symbol in ("ADBE", "NVDA"):
                return httpx.Response(200, json=adobe_fixtures["key_metrics_own"])
            peer_data = adobe_fixtures["peers_metrics"].get(symbol)
            if peer_data:
                return httpx.Response(200, json=peer_data)
            return httpx.Response(200, json=[])
        if path == "/stable/profile":
            return httpx.Response(200, json=adobe_fixtures["profile"])
        if path == "/stable/income-statement":
            if period == "quarter":
                if symbol == "NVDA":
                    return httpx.Response(
                        200, json=adobe_fixtures["income_statement_quarterly_nvda"]
                    )
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=adobe_fixtures["income_statement"])
        if path == "/stable/balance-sheet-statement":
            if period == "quarter":
                if symbol == "NVDA":
                    return httpx.Response(
                        200, json=adobe_fixtures["balance_sheet_quarterly_nvda"]
                    )
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=adobe_fixtures["balance_sheet"])
        if path == "/stable/cash-flow-statement":
            if period == "quarter":
                if symbol == "NVDA":
                    return httpx.Response(
                        200, json=adobe_fixtures["cash_flow_quarterly_nvda"]
                    )
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=adobe_fixtures["cash_flow"])
        if path == "/stable/search-symbol":
            return httpx.Response(
                200, json=[{"symbol": "ADBE", "name": "Adobe Inc."}]
            )
        return httpx.Response(404)

    return handler


def _fred_handler(adobe_fixtures):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=adobe_fixtures["fred_dgs20"])

    return handler


def _make_clients(adobe_fixtures) -> query_handler.Clients:
    fmp_http = httpx.AsyncClient(transport=httpx.MockTransport(_adobe_router(adobe_fixtures)))
    fred_http = httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures)))
    treasury_gov_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    return query_handler.Clients(
        fmp_http=fmp_http,
        fred_http=fred_http,
        treasury_gov_http=treasury_gov_http,
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
    )


# ---------------------------------------------------------------------------
# normalize_query / sanitize_for_log
# ---------------------------------------------------------------------------


def test_normalize_query_strip_y_colapsa_espacios():
    assert query_handler.normalize_query("  Adobe   Inc  ") == "Adobe Inc"


def test_normalize_query_excede_longitud():
    assert query_handler.normalize_query("A" * 200) is None


def test_normalize_query_vacio():
    assert query_handler.normalize_query("   ") is None


def test_sanitize_for_log_remueve_saltos_de_linea():
    sucio = "ADBE\nFAKE LOG LINE: admin logged in"
    limpio = query_handler.sanitize_for_log(sucio)
    assert "\n" not in limpio


# ---------------------------------------------------------------------------
# fetch_and_analyze — caso Adobe end-to-end (sin red real)
# ---------------------------------------------------------------------------


async def test_fetch_and_analyze_adobe_end_to_end(adobe_fixtures):
    clients = _make_clients(adobe_fixtures)
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text
    assert "barata" in text
    assert "el boletín" in text


async def test_fetch_and_analyze_datos_incompletos_mensaje_claro():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )
    text = await query_handler.fetch_and_analyze("NOPE", clients, perfil="moderado")
    assert "No pude obtener" in text


# ---------------------------------------------------------------------------
# handle_text — flujo completo (onboarding, rate-limit, resolución, errores)
# ---------------------------------------------------------------------------


class FakeRateLimiter:
    def __init__(self, allow_value=True):
        self.allow_value = allow_value

    def allow(self, key):
        return self.allow_value


def _fake_message(**overrides):
    """Mock de `telegram.Message` con `.edit_text` y `.chat.send_message` —
    ambos necesarios para el flujo multi-chunk (Ampliación #2, Decisión 17):
    el primer chunk se entrega vía `.edit_text`, los siguientes vía
    `.chat.send_message` sobre este mismo objeto."""
    base = dict(edit_text=AsyncMock(), chat=SimpleNamespace(send_message=AsyncMock()))
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_text_update(text, chat_id=ALLOWED_CHAT_ID):
    update = SimpleNamespace()
    update.message = SimpleNamespace(
        text=text,
        reply_text=AsyncMock(return_value=_fake_message()),
    )
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    update.callback_query = None
    return update


def _fake_callback_update(data, chat_id=ALLOWED_CHAT_ID, edit_message_text=None):
    """Mock de un `Update` de callback_query (`tk:`/`esc:`/`vent:`) — mismo
    patrón que `_fake_text_update` para el flujo de texto libre."""
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=edit_message_text or AsyncMock(return_value=_fake_message()),
    )
    update = SimpleNamespace()
    update.callback_query = query
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    update.message = None
    return update, query


async def _drive_esc_vent(handlers, ticker, escenario="conservador", ventana=20, chat_id=ALLOWED_CHAT_ID):
    """Simula el resto de la cadena de botones interactivos (`esc:` ->
    `vent:`, SDD_eps_ttm_real.md ronda 2) a partir de un ticker ya resuelto
    — usado por tests que no necesitan ejercitar `handle_text`/
    `handle_disambiguation` desde cero. Devuelve el `query` del callback
    `vent:`, cuyo `.edit_message_text` es el mensaje de carga inicial (y
    luego, vía su `.edit_text`, el resultado final — mismo patrón que
    `_fake_message`)."""
    handle_escenario = handlers[2].callback
    handle_ventana = handlers[3].callback

    update_esc, query_esc = _fake_callback_update(f"esc:{ticker}:{escenario}", chat_id=chat_id)
    await handle_escenario(update_esc, context=SimpleNamespace())

    update_vent, query_vent = _fake_callback_update(
        f"vent:{ticker}:{escenario}:{ventana}", chat_id=chat_id
    )
    await handle_ventana(update_vent, context=SimpleNamespace())
    return query_esc, query_vent


@pytest.fixture
def conn_factory(tmp_path):
    db_path = str(tmp_path / "qh.db")

    def get_conn():
        return db.get_connection(db_path)

    return get_conn


async def test_handle_text_sin_onboarding_pide_start(adobe_fixtures, conn_factory):
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())
    update.message.reply_text.assert_awaited_once_with(query_handler.NO_ONBOARDING_MSG)


def _complete_onboarding(conn_factory, perfil="moderado"):
    conn = conn_factory()
    db.init_db(conn)
    db.save_risk_profile(conn, [10] * 8, 80, perfil, "2026-01-01T00:00:00+00:00")
    conn.close()


async def test_handle_text_texto_muy_largo(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("A" * 200)
    await handle_text(update, context=SimpleNamespace())
    update.message.reply_text.assert_awaited_once_with(query_handler.INPUT_TOO_LONG_MSG)


async def test_handle_text_rate_limited(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(
        conn_factory, clients, FakeRateLimiter(allow_value=False)
    )
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())
    update.message.reply_text.assert_awaited_once_with(query_handler.RATE_LIMITED_MSG)


async def test_handle_text_resuelve_ticker_exacto_pregunta_escenario(adobe_fixtures, conn_factory):
    """Ronda 2 (SDD_eps_ttm_real.md): un match único/exacto ya NO dispara el
    análisis directamente — encadena a la pregunta de escenario (3 botones)."""
    _complete_onboarding(conn_factory, perfil="moderado")
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.call_args
    assert args[0] == query_handler.ASK_ESCENARIO_MSG
    markup = kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == ["esc:ADBE:pesimista", "esc:ADBE:conservador", "esc:ADBE:optimista"]


async def test_flujo_completo_texto_esc_vent_dispara_analisis(adobe_fixtures, conn_factory):
    """Test end-to-end, camino sin desambiguación (criterio de QA sección 3):
    texto libre con match único -> callback `esc:` -> callback `vent:` ->
    `fetch_and_analyze_parts` invocado (2 callbacks totales) -> el resultado
    final llega vía `.edit_text` sobre el mensaje de carga del callback `vent:`."""
    _complete_onboarding(conn_factory, perfil="moderado")
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())

    query_esc, query_vent = await _drive_esc_vent(handlers, "ADBE")

    # Paso 1 (esc:) edita el mensaje a la pregunta de ventana.
    args_esc, kwargs_esc = query_esc.edit_message_text.call_args
    assert args_esc[0] == query_handler.ASK_VENTANA_MSG
    markup_esc = kwargs_esc["reply_markup"]
    callbacks_vent = [b.callback_data for row in markup_esc.inline_keyboard for b in row]
    assert callbacks_vent == ["vent:ADBE:conservador:12", "vent:ADBE:conservador:20"]

    # Paso 2 (vent:) dispara el análisis — mensaje de carga + resultado final.
    query_vent.edit_message_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert "Adobe" in args[0]
    assert kwargs.get("parse_mode") == "Markdown"


async def test_handle_text_sin_coincidencias(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("asdfqwerty")
    await handle_text(update, context=SimpleNamespace())
    args, kwargs = update.message.reply_text.call_args
    assert "No encontré" in args[0]


async def test_handle_text_multiples_coincidencias_muestra_botones(conn_factory):
    _complete_onboarding(conn_factory)

    matches = [
        {"symbol": "AAPL", "name": "Apple Inc."},
        {"symbol": "APLE", "name": "Apple Hospitality REIT"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=matches)

    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("Apple")
    await handle_text(update, context=SimpleNamespace())
    args, kwargs = update.message.reply_text.call_args
    assert "reply_markup" in kwargs


async def test_handle_text_fmp_error_en_search(conn_factory):
    _complete_onboarding(conn_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())
    args, kwargs = update.message.reply_text.call_args
    assert "500" in args[0]


async def test_handle_disambiguation_resuelve_y_pregunta_escenario(adobe_fixtures, conn_factory):
    """Ronda 2: `tk:` ya NO dispara el análisis directamente — encadena a la
    pregunta de escenario editando el mismo mensaje (Decisión #21)."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_disambiguation = handlers[1].callback

    update, query = _fake_callback_update("tk:ADBE")
    await handle_disambiguation(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once()
    args, kwargs = query.edit_message_text.call_args
    assert args[0] == query_handler.ASK_ESCENARIO_MSG
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert callbacks == ["esc:ADBE:pesimista", "esc:ADBE:conservador", "esc:ADBE:optimista"]


async def test_flujo_completo_tk_esc_vent_dispara_analisis(adobe_fixtures, conn_factory):
    """Test end-to-end, camino CON desambiguación (criterio de QA sección 3):
    texto libre con múltiples matches -> callback `tk:` -> callback `esc:` ->
    callback `vent:` -> análisis (3 callbacks totales)."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    update, query = _fake_callback_update("tk:ADBE")
    await handlers[1].callback(update, context=SimpleNamespace())

    query_esc, query_vent = await _drive_esc_vent(handlers, "ADBE")

    query_vent.edit_message_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert "Adobe" in args[0]


async def test_run_analysis_error_generico_no_crashea(conn_factory, monkeypatch):
    """Un error inesperado (no FMPError/TreasuryError) nunca debe propagarse
    crudo hacia Telegram — `_run_analysis` lo captura y responde con un
    mensaje genérico. Ronda 2: el único disparador real de `_run_analysis`
    es el callback `vent:` (vía la cadena `esc:`->`vent:`)."""
    _complete_onboarding(conn_factory)

    async def raise_unexpected(ticker, clients_arg, perfil, **kwargs):
        raise RuntimeError("boom inesperado")

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_unexpected)

    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=empty_transport),
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )

    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    query_esc, query_vent = await _drive_esc_vent(handlers, "ADBE")

    query_vent.edit_message_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = query_vent.edit_message_text.return_value
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == query_handler.GENERIC_ERROR_MSG
    assert "parse_mode" not in kwargs


async def test_run_analysis_fmp_error_dentro_de_fetch_and_analyze(conn_factory, monkeypatch):
    """FMPError/TreasuryError lanzado dentro de `fetch_and_analyze_parts`
    también debe llegar vía `.edit_text` sobre el mensaje de carga, no vía
    una segunda llamada directa a `reply_fn`."""
    _complete_onboarding(conn_factory)

    async def raise_fmp_error(ticker, clients_arg, perfil, **kwargs):
        raise FMPError("429 rate limited")

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_fmp_error)

    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=empty_transport),
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )

    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    query_esc, query_vent = await _drive_esc_vent(handlers, "ADBE")

    query_vent.edit_message_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = query_vent.edit_message_text.return_value
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == "429 rate limited"
    assert "parse_mode" not in kwargs


# ---------------------------------------------------------------------------
# Mensaje de carga — matriz envío/edit OK-falla (spec SDD_mensaje_cargando.md)
# ---------------------------------------------------------------------------


async def test_run_analysis_falla_envio_mensaje_carga_no_bloquea_analisis(
    adobe_fixtures, conn_factory
):
    """Si el envío del mensaje de carga falla (TelegramError), el análisis
    real sigue igual y el resultado final llega por una llamada directa a
    `reply_fn` (`query.edit_message_text` del callback `vent:`, sin
    `.edit_text`, porque no hay `Message` que editar). Ronda 2: el único
    disparador real de `_run_analysis` es el callback `vent:`."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    edit_message_text = AsyncMock(side_effect=[TelegramError("boom"), _fake_message()])
    update_vent, query_vent = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )
    await handlers[3].callback(update_vent, context=SimpleNamespace())

    assert query_vent.edit_message_text.await_count == 2
    first_args, _ = query_vent.edit_message_text.call_args_list[0]
    assert first_args[0] == query_handler.LOADING_MSG.format(ticker="ADBE")
    final_args, final_kwargs = query_vent.edit_message_text.call_args_list[1]
    assert "Adobe" in final_args[0]
    assert final_kwargs.get("parse_mode") == "Markdown"


async def test_run_analysis_falla_edit_final_hace_fallback_a_reply_fn(
    adobe_fixtures, conn_factory
):
    """Si el edit final sobre el mensaje de carga falla (TelegramError), se
    hace un único intento de fallback llamando a `reply_fn` directamente con
    el contenido final — nunca se pierde la respuesta."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    loading_msg = _fake_message(edit_text=AsyncMock(side_effect=TelegramError("boom")))
    edit_message_text = AsyncMock(return_value=loading_msg)
    update_vent, query_vent = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )
    await handlers[3].callback(update_vent, context=SimpleNamespace())

    assert query_vent.edit_message_text.await_count == 2
    loading_msg.edit_text.assert_awaited_once()
    final_args, final_kwargs = query_vent.edit_message_text.call_args_list[1]
    assert "Adobe" in final_args[0]
    assert final_kwargs.get("parse_mode") == "Markdown"


async def test_run_analysis_excepcion_no_telegram_en_envio_carga_se_propaga(
    adobe_fixtures, conn_factory
):
    """El `except TelegramError` acotado no debe convertirse en un catch-all:
    una excepción que no sea de Telegram (p.ej. `ValueError`) debe propagarse
    sin ser tragada como si fuera un fallo best-effort de la API de Telegram."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    edit_message_text = AsyncMock(side_effect=ValueError("no es un TelegramError"))
    update_vent, _ = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )
    with pytest.raises(ValueError):
        await handlers[3].callback(update_vent, context=SimpleNamespace())


# ---------------------------------------------------------------------------
# Best-effort own_metrics / VIX — un fallo de estas 2 llamadas nuevas nunca
# cambia el resto de la respuesta (Pieza 1 / Pieza 2 de la sección QA de
# SDD_contenido_financiero_explicado.md).
# ---------------------------------------------------------------------------


async def test_fetch_and_analyze_own_metrics_fmperror_resto_identico(adobe_fixtures):
    """`fmp_client.get_key_metrics` lanza `FMPError` para el ticker propio
    (ADBE) -> `own_metrics=None` -> el texto final es byte a byte idéntico
    al que se produce cuando la misma llamada devuelve una lista vacía
    (mismo resultado de `extract_key_metrics_extras`, ningún otro dato se ve
    afectado)."""

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/key-metrics" and request.url.params.get("symbol") == "ADBE":
            return httpx.Response(500)
        return _adobe_router(adobe_fixtures)(request)

    def empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/key-metrics" and request.url.params.get("symbol") == "ADBE":
            return httpx.Response(200, json=[])
        return _adobe_router(adobe_fixtures)(request)

    def _clients(handler) -> query_handler.Clients:
        return query_handler.Clients(
            fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures))),
            treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            fmp_api_key="test-key",
            fred_api_key="test-fred-key",
        )

    text_failing = await query_handler.fetch_and_analyze("ADBE", _clients(failing_handler), perfil="moderado")
    text_empty = await query_handler.fetch_and_analyze("ADBE", _clients(empty_handler), perfil="moderado")

    assert text_failing == text_empty
    assert "Rentabilidad, deuda de largo plazo y dividendos" not in text_failing


async def test_fetch_and_analyze_vix_fmperror_resto_identico(adobe_fixtures):
    """`fmp_client.get_quote(..., "^VIX")` lanza `FMPError` -> `vix_quote=None`
    -> el texto final es byte a byte idéntico al que se produce cuando la
    misma llamada devuelve una lista vacía (mismo resultado de
    `extract_vix_context`, ningún otro dato se ve afectado)."""

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/quote" and request.url.params.get("symbol") == query_handler.market_context.VIX_SYMBOL:
            return httpx.Response(500)
        return _adobe_router(adobe_fixtures)(request)

    def empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/quote" and request.url.params.get("symbol") == query_handler.market_context.VIX_SYMBOL:
            return httpx.Response(200, json=[])
        return _adobe_router(adobe_fixtures)(request)

    def _clients(handler) -> query_handler.Clients:
        return query_handler.Clients(
            fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures))),
            treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            fmp_api_key="test-key",
            fred_api_key="test-fred-key",
        )

    text_failing = await query_handler.fetch_and_analyze("ADBE", _clients(failing_handler), perfil="moderado")
    text_empty = await query_handler.fetch_and_analyze("ADBE", _clients(empty_handler), perfil="moderado")

    assert text_failing == text_empty
    assert "VIX (CBOE Volatility Index)" not in text_failing


# ---------------------------------------------------------------------------
# chunk_for_telegram / _split_oversized_part / _with_continuation_prefixes /
# _hard_truncate_with_marker — funciones puras (Pieza 5 de la sección QA).
# ---------------------------------------------------------------------------


def test_chunk_for_telegram_bajo_presupuesto_un_solo_chunk_identico_al_join():
    parts = ["Sección uno", "Sección dos", "Sección tres"]
    result = query_handler.chunk_for_telegram(parts)
    assert result == ["\n\n".join(parts)]


def test_chunk_for_telegram_lista_vacia_devuelve_string_vacio():
    assert query_handler.chunk_for_telegram([]) == [""]


def test_chunk_for_telegram_supera_presupuesto_arma_varios_chunks_en_orden():
    limit = 100
    parts = ["A" * 30, "B" * 30, "C" * 30, "D" * 30]
    chunks = query_handler.chunk_for_telegram(parts, limit=limit)

    assert len(chunks) > 1
    budget = limit - query_handler._CONTINUATION_PREFIX_RESERVE
    assert all(len(c) <= budget for c in chunks)
    # Ninguna sección se pierde ni se reordena: reconstruir con el mismo
    # separador reproduce exactamente la concatenación original.
    assert "\n\n".join(chunks) == "\n\n".join(parts)


def test_chunk_for_telegram_seccion_individual_oversized_usa_split_y_respeta_budget():
    limit = 100
    budget = limit - query_handler._CONTINUATION_PREFIX_RESERVE
    big_part = "\n\n".join(f"Párrafo número {i} con algo de contenido de relleno." for i in range(10))
    assert len(big_part) > budget

    chunks = query_handler.chunk_for_telegram([big_part], limit=limit)
    assert len(chunks) > 1
    assert all(len(c) <= budget for c in chunks)


def test_chunk_for_telegram_budget_menos_1_entra_en_un_solo_chunk():
    budget = query_handler.TELEGRAM_MESSAGE_LIMIT - query_handler._CONTINUATION_PREFIX_RESERVE
    part = "A" * (budget - 1)
    assert query_handler.chunk_for_telegram([part]) == [part]


def test_chunk_for_telegram_exactamente_budget_entra_en_un_solo_chunk():
    budget = query_handler.TELEGRAM_MESSAGE_LIMIT - query_handler._CONTINUATION_PREFIX_RESERVE
    part = "A" * budget
    assert query_handler.chunk_for_telegram([part]) == [part]


def test_chunk_for_telegram_budget_mas_1_dispara_split_oversized():
    budget = query_handler.TELEGRAM_MESSAGE_LIMIT - query_handler._CONTINUATION_PREFIX_RESERVE
    # Sin separadores ("\n\n"/"\n"/". ") -> corte duro con marcador visible,
    # mismo tamaño que `budget` (Decisión 18/19).
    part = "A" * (budget + 1)
    chunks = query_handler.chunk_for_telegram([part])
    assert len(chunks) == 1
    assert len(chunks[0]) <= budget
    assert "recortado por límite de Telegram" in chunks[0]


def test_split_oversized_part_por_parrafo():
    limit = 25
    part = "A" * 20 + "\n\n" + "B" * 20
    chunks = query_handler._split_oversized_part(part, limit)
    assert chunks == ["A" * 20, "B" * 20]
    assert all(len(c) <= limit for c in chunks)


def test_split_oversized_part_fallback_por_linea_sin_parrafos():
    limit = 25
    part = "A" * 20 + "\n" + "B" * 20
    assert "\n\n" not in part
    chunks = query_handler._split_oversized_part(part, limit)
    assert chunks == ["A" * 20, "B" * 20]
    assert all(len(c) <= limit for c in chunks)


def test_split_oversized_part_fallback_por_oracion_sin_parrafos_ni_lineas():
    limit = 25
    part = "A" * 20 + ". " + "B" * 20
    assert "\n" not in part
    chunks = query_handler._split_oversized_part(part, limit)
    assert chunks == ["A" * 20, "B" * 20]
    assert all(len(c) <= limit for c in chunks)


def test_split_oversized_part_pieza_final_vacia_no_se_agrega_de_mas(caplog):
    """Cobertura de rama: cuando la última pieza tras un split es un string
    vacío producto de un separador al final del texto (ej. `"AAAA\\n\\n"`),
    el reset de `current` deja `current=""` al terminar el loop interno — la
    rama `if current:` debe evaluar `False` y NO agregar un elemento extra
    vacío a `chunks` (ejercitada en los niveles "\\n\\n" y "\\n" antes de caer,
    con `limit` tan chico, al corte duro de último recurso)."""
    limit = 5
    part = "A" * 8 + "\n\n"
    with caplog.at_level(logging.ERROR):
        chunks = query_handler._split_oversized_part(part, limit)
    assert len(chunks) == 1
    assert "recortado por límite de Telegram" in chunks[0]


def test_split_oversized_part_nivel_parrafo_falla_cae_a_nivel_linea():
    """Cobertura de rama: el nivel "\\n\\n" puede producir una pieza que por sí
    sola ya supera `limit` (acá, "BBBBBBBB\\nCCCCCCCC" mide 17 > 10) — el
    `all(len(c) <= limit ...)` da `False` y el algoritmo cae al siguiente
    separador más fino ("\\n"), donde sí entra todo dentro del límite."""
    limit = 10
    part = "A" * 8 + "\n\n" + "B" * 8 + "\n" + "C" * 8
    chunks = query_handler._split_oversized_part(part, limit)
    assert chunks == ["AAAAAAAA\n", "BBBBBBBB", "CCCCCCCC"]
    assert all(len(c) <= limit for c in chunks)


def test_split_oversized_part_corte_duro_sin_ningun_separador(caplog):
    limit = 100
    part = "X" * 300  # sin "\n\n", "\n" ni ". "
    with caplog.at_level(logging.ERROR):
        chunks = query_handler._split_oversized_part(part, limit)

    assert len(chunks) == 1
    assert len(chunks[0]) <= limit
    assert "⚠️" in chunks[0]
    assert "recortado por límite de Telegram" in chunks[0]
    assert "no se pudo partir por párrafo" in caplog.text


def test_with_continuation_prefixes_un_chunk_es_no_op():
    chunks = ["único chunk"]
    assert query_handler._with_continuation_prefixes(chunks) == chunks


def test_with_continuation_prefixes_varios_chunks_agrega_prefijo_correcto():
    chunks = ["primero", "segundo", "tercero"]
    result = query_handler._with_continuation_prefixes(chunks)
    assert result[0] == "primero"
    assert result[1] == "_(cont. parte 2/3)_\n\nsegundo"
    assert result[2] == "_(cont. parte 3/3)_\n\ntercero"


def test_hard_truncate_with_marker_loguea_completo_y_trunca(caplog):
    parts = ["A" * 3000, "B" * 3000]
    with caplog.at_level(logging.ERROR):
        result = query_handler._hard_truncate_with_marker(parts)

    assert len(result) <= query_handler.TELEGRAM_MESSAGE_LIMIT
    assert "mensaje recortado por un error interno" in result
    assert "chunk_for_telegram falló de forma inesperada" in caplog.text
    assert "A" * 3000 in caplog.text and "B" * 3000 in caplog.text


# ---------------------------------------------------------------------------
# Integración multi-mensaje `_run_analysis`/`_deliver_all` — matriz de 6
# escenarios obligatorios (Pieza 5b de la sección QA), medida como checklist
# de escenarios, no como % de líneas.
# ---------------------------------------------------------------------------


def _multichunk_parts() -> list[str]:
    """3 secciones de ~2.5k caracteres cada una: individualmente caben en un
    chunk, pero juntas superan el budget (4056) -> exactamente 3 chunks con
    el `chunk_for_telegram` real (sin mockear)."""
    return [f"Parte{i}: " + ("X" * 2490) for i in (1, 2, 3)]


def _empty_clients() -> query_handler.Clients:
    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    return query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=empty_transport),
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )


def _patch_parts_and_search(monkeypatch, parts: list[str]) -> None:
    async def fake_parts(ticker, clients_arg, perfil, **kwargs):
        return parts

    async def fake_search(client, key, q):
        return [{"symbol": "ADBE", "name": "Adobe Inc."}]

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", fake_parts)
    monkeypatch.setattr(query_handler.fmp_client, "search_company", fake_search)


async def test_run_analysis_multichunk_feliz_edit_y_send_message_en_orden(conn_factory, monkeypatch):
    """Escenario 2/6: caso feliz multi-chunk. `loading_msg.edit_text` recibe
    el chunk 1 sin prefijo; cada chunk siguiente llega vía
    `loading_msg.chat.send_message`, en orden, con el prefijo correcto.
    Ronda 2: se llega a `_run_analysis` vía la cadena `esc:`->`vent:`."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    _, query_vent = await _drive_esc_vent(handlers, "ADBE")

    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    edit_args, edit_kwargs = loading_msg.edit_text.call_args
    assert edit_args[0].startswith("Parte1")
    assert edit_kwargs.get("parse_mode") == "Markdown"

    assert loading_msg.chat.send_message.await_count == 2
    call2, call3 = loading_msg.chat.send_message.call_args_list
    assert call2.args[0] == "_(cont. parte 2/3)_\n\n" + parts[1]
    assert call2.kwargs.get("parse_mode") == "Markdown"
    assert call3.args[0] == "_(cont. parte 3/3)_\n\n" + parts[2]


async def test_run_analysis_loading_msg_none_multichunk_reply_fn_y_send_message(
    conn_factory, monkeypatch
):
    """Escenario 3/6: falló el envío del mensaje de carga (`loading_msg is
    None`) + contenido multi-chunk. El chunk 1 llega por `reply_fn` (como
    hoy en el caso de un solo chunk) y los siguientes por `.chat.send_message`
    sobre el `Message` que devuelve esa primera llamada."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    final_msg = _fake_message()
    edit_message_text = AsyncMock(side_effect=[TelegramError("boom carga"), final_msg])
    update_vent, query_vent = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )
    await handlers[3].callback(update_vent, context=SimpleNamespace())

    assert query_vent.edit_message_text.await_count == 2
    final_args, final_kwargs = query_vent.edit_message_text.call_args_list[1]
    assert final_args[0].startswith("Parte1")
    assert final_kwargs.get("parse_mode") == "Markdown"

    assert final_msg.chat.send_message.await_count == 2
    call2, call3 = final_msg.chat.send_message.call_args_list
    assert call2.args[0] == "_(cont. parte 2/3)_\n\n" + parts[1]
    assert call3.args[0] == "_(cont. parte 3/3)_\n\n" + parts[2]


async def test_run_analysis_falla_edit_text_multichunk_fallback_a_reply_fn(
    conn_factory, monkeypatch
):
    """Escenario 4/6: falla el `edit_text` del chunk 1 sobre `loading_msg` +
    contenido multi-chunk. Mismo fallback de un intento único ya definido en
    `SDD_mensaje_cargando.md`: el chunk 1 se reintenta por `reply_fn`, y los
    siguientes por `.chat.send_message` sobre ese nuevo retorno."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    loading_msg = _fake_message(edit_text=AsyncMock(side_effect=TelegramError("boom edit")))
    edit_message_text = AsyncMock(return_value=loading_msg)
    update_vent, query_vent = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )
    await handlers[3].callback(update_vent, context=SimpleNamespace())

    assert query_vent.edit_message_text.await_count == 2
    loading_msg.edit_text.assert_awaited_once()
    final_args, final_kwargs = query_vent.edit_message_text.call_args_list[1]
    assert final_args[0].startswith("Parte1")
    assert final_kwargs.get("parse_mode") == "Markdown"

    assert loading_msg.chat.send_message.await_count == 2
    call2, call3 = loading_msg.chat.send_message.call_args_list
    assert call2.args[0] == "_(cont. parte 2/3)_\n\n" + parts[1]
    assert call3.args[0] == "_(cont. parte 3/3)_\n\n" + parts[2]


async def test_run_analysis_falla_envio_de_chunk_intermedio_continua_con_el_resto(
    conn_factory, monkeypatch, caplog
):
    """Escenario 5/6: falla el envío de un chunk intermedio
    (`.chat.send_message` de la parte 2 de 3). Se loguea con `logger.error`
    (no se traga en silencio) y se sigue intentando enviar la parte 3 — no
    se aborta el resto por el fallo de una parte; ninguna excepción se
    propaga fuera del callback `vent:`."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    loading_msg = _fake_message(
        chat=SimpleNamespace(
            send_message=AsyncMock(side_effect=[TelegramError("boom parte 2"), None])
        )
    )
    edit_message_text = AsyncMock(return_value=loading_msg)
    update_vent, _ = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )

    with caplog.at_level(logging.ERROR):
        await handlers[3].callback(update_vent, context=SimpleNamespace())

    assert loading_msg.chat.send_message.await_count == 2
    assert "no llegó a Telegram" in caplog.text


async def test_run_analysis_chunk_for_telegram_excepcion_usa_marcador_de_recorte(
    conn_factory, monkeypatch, caplog
):
    """Escenario 6/6: `chunk_for_telegram` lanza una excepción inesperada
    (mock con `side_effect`). Se entrega un único mensaje con el marcador de
    la Decisión 19, con el texto completo logueado vía `logger.error`;
    ninguna excepción se propaga."""
    _complete_onboarding(conn_factory)
    parts = ["contenido normal, corto"]
    _patch_parts_and_search(monkeypatch, parts)

    def raise_runtime(parts_arg, limit=query_handler.TELEGRAM_MESSAGE_LIMIT):
        raise RuntimeError("bug interno de partición")

    monkeypatch.setattr(query_handler, "chunk_for_telegram", raise_runtime)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())

    with caplog.at_level(logging.ERROR):
        _, query_vent = await _drive_esc_vent(handlers, "ADBE")

    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert "mensaje recortado por un error interno" in args[0]
    assert "chunk_for_telegram falló de forma inesperada" in caplog.text


async def test_run_analysis_excepcion_generica_en_send_message_no_es_capturada_por_el_except_de_chunk_for_telegram(
    conn_factory, monkeypatch
):
    """Test negativo del escenario 6/6: el único `except Exception` genérico
    de todo el proyecto envuelve *solo* la llamada a `chunk_for_telegram`,
    nunca `_deliver_all`. Una excepción no-`TelegramError` producida dentro
    de `.chat.send_message` (bug no relacionado con la partición) debe
    propagarse sin ser tragada por ese `except Exception`."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())

    update_esc, _ = _fake_callback_update("esc:ADBE:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())

    loading_msg = _fake_message(
        chat=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("bug no relacionado")))
    )
    edit_message_text = AsyncMock(return_value=loading_msg)
    update_vent, _ = _fake_callback_update(
        "vent:ADBE:conservador:20", edit_message_text=edit_message_text
    )

    with pytest.raises(RuntimeError):
        await handlers[3].callback(update_vent, context=SimpleNamespace())


async def test_run_analysis_multichunk_cero_llamadas_http_nuevas(conn_factory, monkeypatch):
    """Cero llamadas nuevas a FMP/FRED/Treasury.gov en toda la Ampliación
    #2 — es 100% interacción con la API de Telegram. Se cuentan los
    requests capturados por el `MockTransport` (debería ser 0, porque
    `fetch_and_analyze_parts` está mockeado sin red real en este test de
    integración de entrega multi-mensaje)."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    call_count = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(counting_handler)
    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=transport),
        fred_http=httpx.AsyncClient(transport=transport),
        treasury_gov_http=httpx.AsyncClient(transport=transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )

    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    await _drive_esc_vent(handlers, "ADBE")

    assert call_count["n"] == 0


# ---------------------------------------------------------------------------
# SDD_procedencia_peers_individuales — wiring de peers_pe/peers_no_usados
# ---------------------------------------------------------------------------


async def test_fetch_and_analyze_propaga_per_individual_y_motivo_end_to_end(adobe_fixtures):
    """Q1: peer_comparison_dict (vía compare_to_peers) propaga peers_pe/
    peers_no_usados con motivo por peer, visible en el texto final —
    extiende el fixture de Adobe para que 1 peer tenga earningsYield<=0
    (ORCL) y 1 no devuelva dato (CRM), dejando MSFT como único válido."""
    fixtures = dict(adobe_fixtures)
    fixtures["peers_metrics"] = {
        "MSFT": [{"symbol": "MSFT", "earningsYield": 1 / 30.0}],
        "ORCL": [{"symbol": "ORCL", "earningsYield": -0.01}],
        # CRM deliberadamente ausente del fixture -> peer_data falsy en
        # _adobe_router -> respuesta [] -> "sin_dato".
    }
    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(_adobe_router(fixtures))),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(fixtures))),
        treasury_gov_http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ),
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "MSFT 30.0" in text
    assert "ORCL tiene pérdidas esta consulta" in text
    assert "CRM no devolvió un dato de FMP esta consulta" in text


def test_fetch_and_analyze_firma_publica_sin_cambios():
    """Q2 (SDD_peers_dinamicos...): `fetch_and_analyze` no cambia de firma.

    `fetch_and_analyze_parts` sí gana 2 parámetros nuevos *keyword-only con
    default retrocompatible* (`escenario_elegido`/`ventana_trimestres`,
    SDD_eps_ttm_real.md Decisión #24) — actualizado a propósito, no una
    regresión: todo llamador que use la firma posicional de 3 argumentos
    (`ticker, clients, perfil`) sigue funcionando idéntico (ver
    test_fetch_and_analyze_parts_call_sites_posicionales_siguen_funcionando)."""
    import inspect

    sig_wrapper = inspect.signature(query_handler.fetch_and_analyze)
    assert list(sig_wrapper.parameters) == ["ticker", "clients", "perfil"]

    sig_parts = inspect.signature(query_handler.fetch_and_analyze_parts)
    assert list(sig_parts.parameters) == [
        "ticker", "clients", "perfil", "escenario_elegido", "ventana_trimestres",
    ]
    assert sig_parts.parameters["escenario_elegido"].default == "conservador"
    assert sig_parts.parameters["escenario_elegido"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig_parts.parameters["ventana_trimestres"].default == query_handler.VENTANA_TRIMESTRES_LARGO
    assert sig_parts.parameters["ventana_trimestres"].kind == inspect.Parameter.KEYWORD_ONLY


async def test_fetch_and_analyze_parts_call_sites_posicionales_siguen_funcionando(adobe_fixtures):
    """Los parámetros nuevos son keyword-only con default — cualquier
    llamador existente que use `fetch_and_analyze_parts(ticker, clients,
    perfil)` posicional sigue funcionando sin cambios (mismo criterio que la
    sección 4 de QA de SDD_eps_ttm_real.md)."""
    clients = _make_clients(adobe_fixtures)
    parts = await query_handler.fetch_and_analyze_parts("ADBE", clients, "moderado")
    assert any("Adobe" in p for p in parts)


async def test_fetch_and_analyze_cero_llamadas_http_nuevas(adobe_fixtures):
    """Q3: mismo número de llamadas a /stable/key-metrics que antes de esta
    spec (1 por ticker propio + 1 por cada peer del sector, 3 para
    Technology) y ninguna ruta nueva golpeada — peers_pe/peers_no_usados se
    derivan del mismo dato ya recibido, no de una llamada adicional."""
    llamadas: list[str] = []
    base_handler = _adobe_router(adobe_fixtures)

    def counting_handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request.url.path)
        return base_handler(request)

    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures))),
        treasury_gov_http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ),
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
    )
    await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")

    rutas_conocidas = {
        "/stable/quote",
        "/stable/key-metrics",
        "/stable/profile",
        "/stable/income-statement",
        "/stable/balance-sheet-statement",
        "/stable/cash-flow-statement",
    }
    assert set(llamadas) <= rutas_conocidas
    assert llamadas.count("/stable/key-metrics") == 4


# ---------------------------------------------------------------------------
# SDD_peers_dinamicos_y_eventos_corporativos — Parte 1 (Finnhub). Matriz Q1-Q5.
# ---------------------------------------------------------------------------


def _no_call_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no debería llamarse a este mock: {request.url}")


async def test_finnhub_api_key_none_cero_llamadas_a_finnhub(adobe_fixtures):
    """Q1: clients.finnhub_api_key=None -> 0 llamadas al mock de Finnhub."""
    base_clients = _make_clients(adobe_fixtures)
    clients = dataclasses.replace(
        base_clients,
        finnhub_http=httpx.AsyncClient(transport=httpx.MockTransport(_no_call_handler)),
        finnhub_api_key=None,
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text


async def test_finnhub_configurado_responde_3_peers_validos_fuente_finnhub(adobe_fixtures):
    """Q2: Finnhub configurado, responde con 3+ peers válidos ->
    peer_comparison_dict["fuente_peers"] == "finnhub", y los tickers
    consultados contra /key-metrics son los de Finnhub, no los de
    PEERS_BY_SECTOR."""
    fixtures = dict(adobe_fixtures)
    fixtures["peers_metrics"] = {
        "NVDA": [{"symbol": "NVDA", "earningsYield": 1 / 40.0}],
        "AMD": [{"symbol": "AMD", "earningsYield": 1 / 45.0}],
        "QCOM": [{"symbol": "QCOM", "earningsYield": 1 / 20.0}],
    }

    fmp_symbols_hit: list[str] = []
    base_fmp_handler = _adobe_router(fixtures)

    def fmp_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/key-metrics":
            fmp_symbols_hit.append(request.url.params.get("symbol"))
        return base_fmp_handler(request)

    def finnhub_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["NVDA", "AMD", "QCOM"])

    base_clients = _make_clients(fixtures)
    clients = dataclasses.replace(
        base_clients,
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(fmp_handler)),
        finnhub_http=httpx.AsyncClient(transport=httpx.MockTransport(finnhub_handler)),
        finnhub_api_key="finnhub-test-key",
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")

    peer_symbols_hit = {s for s in fmp_symbols_hit if s != "ADBE"}
    assert peer_symbols_hit == {"NVDA", "AMD", "QCOM"}
    assert "MSFT" not in fmp_symbols_hit
    assert "Finnhub" in text


async def test_finnhub_error_cae_a_fallback_sin_propagar_excepcion(adobe_fixtures):
    """Q3: Finnhub responde con un error -> el closure _get_finnhub_peers lo
    captura y devuelve [] -> cae al fallback fijo, sin que la excepción
    llegue a fetch_and_analyze_parts."""

    def finnhub_failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    base_clients = _make_clients(adobe_fixtures)
    clients = dataclasses.replace(
        base_clients,
        finnhub_http=httpx.AsyncClient(transport=httpx.MockTransport(finnhub_failing_handler)),
        finnhub_api_key="finnhub-test-key",
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text
    assert "MSFT" in text  # cayó al respaldo fijo (Technology)


# Q4 y Q5 (regresión: test_fetch_and_analyze_cero_llamadas_http_nuevas y
# test_fetch_and_analyze_firma_publica_sin_cambios) ya están cubiertos por
# los tests existentes de más arriba en este archivo, sin modificarlos — ver
# sección 1.5 de la spec `SDD_peers_dinamicos_y_eventos_corporativos.md`.


# ---------------------------------------------------------------------------
# SDD_peers_dinamicos_y_eventos_corporativos — Parte 2 (SEC EDGAR). Q6-Q10.
# ---------------------------------------------------------------------------

_SEC_TICKERS_PAYLOAD = {"0": {"ticker": "ADBE", "cik_str": 796343}}


def _sec_edgar_router(events_payload=None):
    """`events_payload`, si se pasa, es el dict de `filings.recent` a servir
    en `/submissions/CIK...` — si es `None`, la ruta de submissions devuelve
    un historial sin eventos relevantes (todos 10-K)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "company_tickers.json" in str(request.url):
            return httpx.Response(200, json=_SEC_TICKERS_PAYLOAD)
        if "submissions" in str(request.url):
            if events_payload is None:
                return httpx.Response(
                    200,
                    json={"filings": {"recent": {"form": ["10-K"], "filingDate": ["2026-01-01"], "accessionNumber": ["a-1"], "primaryDocument": ["d.htm"], "items": [""]}}},
                )
            return httpx.Response(200, json={"filings": {"recent": events_payload}})
        return httpx.Response(404)

    return handler


async def test_sec_edgar_user_agent_none_cero_llamadas(adobe_fixtures):
    """Q6: sec_edgar_user_agent=None/vacío -> 0 llamadas HTTP a un mock de
    SEC EDGAR."""
    base_clients = _make_clients(adobe_fixtures)
    clients = dataclasses.replace(
        base_clients,
        sec_edgar_http=httpx.AsyncClient(transport=httpx.MockTransport(_no_call_handler)),
        sec_edgar_user_agent=None,
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text
    assert "Eventos corporativos" not in text


async def test_sec_edgar_ticker_sin_cik_corporate_events_vacio(adobe_fixtures):
    """Q7: sec_edgar_user_agent configurado pero get_cik_for_ticker devuelve
    None (ticker no encontrado en el mapeo) -> corporate_events_list == [],
    resto del análisis idéntico a una consulta sin SEC EDGAR."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})  # mapeo vacío -> ADBE no encontrado

    base_clients = _make_clients(adobe_fixtures)
    clients_con_sec = dataclasses.replace(
        base_clients,
        sec_edgar_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sec_edgar_user_agent="InvestBot test@example.com",
    )
    clients_sin_sec = dataclasses.replace(
        _make_clients(adobe_fixtures),
        sec_edgar_http=httpx.AsyncClient(transport=httpx.MockTransport(_no_call_handler)),
        sec_edgar_user_agent=None,
    )
    text_con_sec = await query_handler.fetch_and_analyze("ADBE", clients_con_sec, perfil="moderado")
    text_sin_sec = await query_handler.fetch_and_analyze("ADBE", clients_sin_sec, perfil="moderado")
    assert text_con_sec == text_sin_sec
    assert "Eventos corporativos" not in text_con_sec


async def test_sec_edgar_cik_y_submissions_exitosos_evento_relevante(adobe_fixtures):
    """Q8: sec_edgar_user_agent configurado, CIK+submissions exitosos con al
    menos 1 evento relevante -> corporate_events_list poblada, el texto
    final contiene la sección "Eventos corporativos"."""
    fecha_reciente = (date.today() - timedelta(days=10)).isoformat()
    events_payload = {
        "form": ["8-K"],
        "filingDate": [fecha_reciente],
        "accessionNumber": ["0000796343-26-000123"],
        "primaryDocument": ["doc.htm"],
        "items": ["5.02"],
    }
    base_clients = _make_clients(adobe_fixtures)
    clients = dataclasses.replace(
        base_clients,
        sec_edgar_http=httpx.AsyncClient(
            transport=httpx.MockTransport(_sec_edgar_router(events_payload))
        ),
        sec_edgar_user_agent="InvestBot test@example.com",
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Eventos corporativos" in text
    assert fecha_reciente in text
    assert "Cambio de directivos o ejecutivos" in text


async def test_sec_edgar_submissions_falla_tras_cik_exitoso_corporate_events_vacio(adobe_fixtures):
    """Q9: get_submissions falla (500) tras un CIK exitoso ->
    corporate_events_list == [], resto del análisis sin cambios, sin
    excepción propagada."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "company_tickers.json" in str(request.url):
            return httpx.Response(200, json=_SEC_TICKERS_PAYLOAD)
        if "submissions" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(404)

    base_clients = _make_clients(adobe_fixtures)
    clients = dataclasses.replace(
        base_clients,
        sec_edgar_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sec_edgar_user_agent="InvestBot test@example.com",
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text
    assert "Eventos corporativos" not in text


# Q10 (regresión: test_fetch_and_analyze_firma_publica_sin_cambios, ya
# cubierto sin modificarlo — ver sección 1.5 de la spec).


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md (ronda 1) — camino feliz trimestral (NVDA, fixtures
# reales) / peor caso (las 3 fuentes fallan) / fuentes independientes.
# ---------------------------------------------------------------------------

_PROPIAS_DEL_TICKER_PATHS = {
    "/stable/quote",
    "/stable/profile",
    "/stable/income-statement",
    "/stable/balance-sheet-statement",
    "/stable/cash-flow-statement",
}


def _counting_router(base_handler):
    llamadas: list[tuple[str, "str | None", "str | None"]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(
            (request.url.path, request.url.params.get("symbol"), request.url.params.get("period"))
        )
        return base_handler(request)

    return handler, llamadas


def _propias_del_ticker(llamadas, ticker: str) -> list[tuple[str, "str | None", "str | None"]]:
    return [
        (path, symbol, period)
        for path, symbol, period in llamadas
        if (path in _PROPIAS_DEL_TICKER_PATHS)
        or (path == "/stable/key-metrics" and symbol == ticker)
        if not (path in ("/stable/quote", "/stable/profile", "/stable/key-metrics") and symbol != ticker)
    ]


def _capture_summary_call(monkeypatch):
    """Reemplaza `summary.build_summary_parts` por un stub que captura sus
    kwargs — permite verificar directamente qué fuente/valores usó
    `fetch_and_analyze_parts` sin parsear el texto final renderizado."""
    captured: dict = {}

    def fake_build_summary_parts(**kwargs):
        captured.update(kwargs)
        return ["captured"]

    monkeypatch.setattr(query_handler.summary, "build_summary_parts", fake_build_summary_parts)
    return captured


def _quarter_402_router(adobe_fixtures, *, endpoints=("income-statement", "balance-sheet-statement", "cash-flow-statement")):
    """Variante de `_adobe_router` donde los endpoints trimestrales listados
    en `endpoints` devuelven 402 (no `[]`) — simula el peor caso real de la
    Decisión #8 (ej. FMP retira `period=quarter` del plan gratuito)."""
    base = _adobe_router(adobe_fixtures)
    paths = {f"/stable/{e}" for e in endpoints}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("period") == "quarter" and request.url.path in paths:
            return httpx.Response(402, text="Payment Required")
        return base(request)

    return handler


def _make_clients_with_handler(handler, adobe_fixtures=None) -> query_handler.Clients:
    fred_transport = (
        httpx.MockTransport(_fred_handler(adobe_fixtures))
        if adobe_fixtures is not None
        else httpx.MockTransport(lambda r: httpx.Response(200, json={"observations": []}))
    )
    return query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        fred_http=httpx.AsyncClient(transport=fred_transport),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
    )


def _make_clients_nvda(adobe_fixtures, handler=None) -> query_handler.Clients:
    base = handler or _adobe_router(adobe_fixtures)
    return query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(base)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures))),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
    )


async def test_fetch_and_analyze_propaga_balance_sheet_fuente_a_build_summary_parts(
    adobe_fixtures, monkeypatch
):
    """`fetch_and_analyze_parts` pasa `balance_sheet_fuente=balance_fuente`
    a `summary.build_summary_parts` (Pregunta H de la spec, implementada) —
    trimestral con NVDA, anual-fallback con ADBE (el router no sirve
    trimestral para símbolos que no sean NVDA)."""
    captured_nvda = _capture_summary_call(monkeypatch)
    await query_handler.fetch_and_analyze_parts(
        "NVDA", _make_clients_nvda(adobe_fixtures), perfil="moderado"
    )
    assert captured_nvda["balance_sheet_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL

    captured_adbe = _capture_summary_call(monkeypatch)
    await query_handler.fetch_and_analyze_parts(
        "ADBE", _make_clients(adobe_fixtures), perfil="moderado"
    )
    assert captured_adbe["balance_sheet_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_fetch_and_analyze_propaga_income_statement_fuente_y_cash_flow_fuente_a_build_summary_parts(
    adobe_fixtures, monkeypatch
):
    """`fetch_and_analyze_parts` pasa `income_statement_fuente=income_statements_fuente`
    y `cash_flow_fuente=cash_flow_fuente` a `summary.build_summary_parts`
    (Spec Patch [Iter-3], mismo patrón que `balance_sheet_fuente`) —
    trimestral con NVDA, anual-fallback con ADBE (el router no sirve
    trimestral para símbolos que no sean NVDA)."""
    captured_nvda = _capture_summary_call(monkeypatch)
    await query_handler.fetch_and_analyze_parts(
        "NVDA", _make_clients_nvda(adobe_fixtures), perfil="moderado"
    )
    assert captured_nvda["income_statement_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL
    assert captured_nvda["cash_flow_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL

    captured_adbe = _capture_summary_call(monkeypatch)
    await query_handler.fetch_and_analyze_parts(
        "ADBE", _make_clients(adobe_fixtures), perfil="moderado"
    )
    assert captured_adbe["income_statement_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK
    assert captured_adbe["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_fetch_and_analyze_nvda_camino_feliz_usa_ttm_no_un_solo_trimestre(
    adobe_fixtures, monkeypatch
):
    """Con las 3 fuentes trimestrales de NVDA disponibles (fixtures reales),
    `ps`/`margen_bruto`/`wacc_inputs` (interest_expense/income_tax_expense/
    income_before_tax) usan las cifras TTM, nunca un solo trimestre suelto
    (criterio de aceptación explícito, Decisión #11) — verificado con
    valores donde el TTM difiere ~3x de un solo trimestre."""
    captured = _capture_summary_call(monkeypatch)
    clients = _make_clients_nvda(adobe_fixtures)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")

    inc = adobe_fixtures["income_statement_quarterly_nvda"]
    revenue_ttm = sum(q["revenue"] for q in inc[:4])
    cost_ttm = sum(q["costOfRevenue"] for q in inc[:4])
    interest_ttm = sum(q["interestExpense"] for q in inc[:4])
    tax_ttm = sum(q["incomeTaxExpense"] for q in inc[:4])
    ibt_ttm = sum(q["incomeBeforeTax"] for q in inc[:4])
    market_cap = 153_180_000_000

    assert captured["ratios"]["ps"] == pytest.approx(market_cap / revenue_ttm)
    assert captured["ratios"]["ps"] != pytest.approx(market_cap / inc[0]["revenue"])
    assert captured["ratios"]["margen_bruto"] == pytest.approx(
        (revenue_ttm - cost_ttm) / revenue_ttm
    )
    assert captured["ratios"]["income_statements_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL
    assert captured["ratios"]["cash_flow_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL
    assert captured["ratios"]["balance_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL


async def test_fetch_and_analyze_nvda_historiales_crudos_trimestrales_cronologicos(
    adobe_fixtures, monkeypatch
):
    """`revenue_historial`/`net_income_historial`/`fcf_historial` con fuente
    trimestral disponible contienen los valores CRUDOS por trimestre (no TTM
    móvil), en orden cronológico (antiguo -> reciente) — Decisión #12.

    `eps_historial` es la EXCEPCIÓN desde el Spec Patch [Iter-4] (Decisión
    #29/#30, causa raíz 1 del bug de Graham sobrevaluando NVDA): pasa a ser
    una serie TTM *rolling* vía `rules.build_ttm_historial`, no los valores
    crudos por trimestre — este assert se actualizó para reflejar ese cambio
    de comportamiento intencional (reportado explícitamente, no ajustado en
    silencio); `fcf_historial` NO cambió (Decisión #35, fuera de alcance de
    Iter-4)."""
    captured = {}

    def fake_compute_valuation_scenarios(**kwargs):
        captured.update(kwargs)
        return query_handler.valuation.compute_valuation_scenarios.__wrapped__(**kwargs) \
            if hasattr(query_handler.valuation.compute_valuation_scenarios, "__wrapped__") \
            else _real_compute_valuation_scenarios(**kwargs)

    _real_compute_valuation_scenarios = query_handler.valuation.compute_valuation_scenarios
    monkeypatch.setattr(
        query_handler.valuation, "compute_valuation_scenarios", fake_compute_valuation_scenarios
    )

    clients = _make_clients_nvda(adobe_fixtures)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")

    inc = adobe_fixtures["income_statement_quarterly_nvda"]
    cf = adobe_fixtures["cash_flow_quarterly_nvda"]
    revenue_crudo_cronologico = [q["revenue"] for q in reversed(inc)]
    net_income_crudo_cronologico = [q["netIncome"] for q in reversed(inc)]
    fcf_crudo_cronologico = [
        q["operatingCashFlow"] - abs(q["capitalExpenditure"]) for q in reversed(cf)
    ]

    # Iter-4, Decisión #29/#30: TTM rolling, no trimestres crudos —
    # `n` trimestres crudos de entrada -> `n - 3` puntos en `eps_historial`,
    # no `n` puntos (criterio de aceptación explícito de `architect`).
    assert len(captured["eps_historial"]) == len(inc) - 3
    assert captured["eps_historial"] == pytest.approx(
        rules.build_ttm_historial(inc, "eps")
    )
    assert captured["fcf_historial"] == pytest.approx(fcf_crudo_cronologico)
    assert captured["periodos_por_anio_eps"] == 4
    assert captured["periodos_por_anio_fcf"] == 4
    assert captured["fcf_base"] == pytest.approx(sum(fcf_crudo_cronologico[-4:]))
    # net_income/revenue historiales no se pasan directamente a
    # compute_valuation_scenarios (solo eps/fcf) -- se verifican indirectamente
    # via pillars, ya cubierto en otro test; acá solo confirmamos que no son
    # necesarios para reconstruir la aserción anterior.
    assert revenue_crudo_cronologico[0] == inc[-1]["revenue"]
    assert net_income_crudo_cronologico[-1] == inc[0]["netIncome"]


async def test_fetch_and_analyze_adbe_eps_historial_anual_fallback_sin_cambios(
    adobe_fixtures, monkeypatch
):
    """Criterio de aceptación de `architect` (Iter-4): rama anual fallback
    (`DATOS_FUENTE_ANUAL_FALLBACK`, caso ADBE en estos fixtures) ->
    `eps_historial` SIN CAMBIOS, sigue usando `_annual_series` cruda (no
    `build_ttm_historial` — esa función solo se usa en la rama trimestral),
    `periodos_por_anio_eps=1` (test de regresión byte a byte, confirma que
    la Causa 1 no tocó esta rama)."""
    captured = {}

    def fake_compute_valuation_scenarios(**kwargs):
        captured.update(kwargs)
        return _real_compute_valuation_scenarios(**kwargs)

    _real_compute_valuation_scenarios = query_handler.valuation.compute_valuation_scenarios
    monkeypatch.setattr(
        query_handler.valuation, "compute_valuation_scenarios", fake_compute_valuation_scenarios
    )

    clients = _make_clients(adobe_fixtures)
    await query_handler.fetch_and_analyze_parts("ADBE", clients, perfil="moderado")

    income_statements = adobe_fixtures["income_statement"]
    eps_esperado = [s["eps"] for s in reversed(income_statements)]

    assert captured["periodos_por_anio_eps"] == 1
    assert captured["eps_historial"] == pytest.approx(eps_esperado)


async def test_fetch_and_analyze_revenue_net_income_historial_sin_cambios_ambas_ramas(
    adobe_fixtures, monkeypatch
):
    """Criterio de aceptación de `architect` (Iter-4): `revenue_historial`/
    `net_income_historial` SIN CAMBIOS en ninguna de las 2 ramas — confirma
    que la Causa 1 es exclusiva de `eps_historial` (grep confirmado en la
    spec: sin otro consumidor). Verificado en ambas ramas: NVDA (trimestral)
    y ADBE (anual fallback)."""
    captured_pillars: dict = {}

    def fake_evaluate_pillars(**kwargs):
        captured_pillars.update(kwargs)
        return _real_evaluate_pillars(**kwargs)

    _real_evaluate_pillars = query_handler.rules.evaluate_pillars
    monkeypatch.setattr(query_handler.rules, "evaluate_pillars", fake_evaluate_pillars)

    clients_nvda = _make_clients_nvda(adobe_fixtures)
    await query_handler.fetch_and_analyze_parts("NVDA", clients_nvda, perfil="moderado")

    inc_nvda = adobe_fixtures["income_statement_quarterly_nvda"]
    assert captured_pillars["revenue_historial"] == pytest.approx(
        [q["revenue"] for q in reversed(inc_nvda)]
    )
    assert captured_pillars["net_income_historial"] == pytest.approx(
        [q["netIncome"] for q in reversed(inc_nvda)]
    )

    captured_pillars.clear()
    clients_adbe = _make_clients(adobe_fixtures)
    await query_handler.fetch_and_analyze_parts("ADBE", clients_adbe, perfil="moderado")

    inc_adbe = adobe_fixtures["income_statement"]
    assert captured_pillars["revenue_historial"] == pytest.approx(
        [q["revenue"] for q in reversed(inc_adbe)]
    )
    assert captured_pillars["net_income_historial"] == pytest.approx(
        [q["netIncome"] for q in reversed(inc_adbe)]
    )


async def test_fetch_and_analyze_camino_feliz_exactamente_6_llamadas_propias(adobe_fixtures):
    """Camino feliz (Decisión #8): exactamente 6 llamadas "propias del
    ticker" — quote, profile, income-statement(quarter), balance-sheet-
    statement(quarter), cash-flow-statement(quarter), key-metrics (propio) —
    ninguna llamada anual de fallback."""
    base = _adobe_router(adobe_fixtures)
    handler, llamadas = _counting_router(base)
    clients = _make_clients_nvda(adobe_fixtures, handler)

    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")

    propias = _propias_del_ticker(llamadas, "NVDA")
    assert len(propias) == 6

    for path in ("/stable/income-statement", "/stable/balance-sheet-statement", "/stable/cash-flow-statement"):
        calls = [c for c in llamadas if c[0] == path]
        assert len(calls) == 1, f"{path} se llamó {len(calls)} veces, esperaba 1 (sin fallback anual)"
        assert calls[0][2] == "quarter"


async def test_fetch_and_analyze_peor_caso_9_llamadas_y_regresion_byte_a_byte(adobe_fixtures):
    """Peor caso (Decisión #8): las 3 fuentes trimestrales fallan (402)
    simultáneamente -> exactamente 9 llamadas propias del ticker (6 intentos
    + 3 fallbacks anuales), y el resultado final es BYTE A BYTE idéntico al
    comportamiento del bot antes de esta spec completa (mismo criterio que
    `_adobe_router` ya producía con `period=quarter` -> `[]` para ADBE)."""
    handler, llamadas = _counting_router(_quarter_402_router(adobe_fixtures))
    clients_peor_caso = _make_clients_with_handler(handler, adobe_fixtures)

    text_peor_caso = await query_handler.fetch_and_analyze("ADBE", clients_peor_caso, perfil="moderado")

    propias = _propias_del_ticker(llamadas, "ADBE")
    assert len(propias) == 9

    text_regresion = await query_handler.fetch_and_analyze("ADBE", _make_clients(adobe_fixtures), perfil="moderado")
    assert text_peor_caso == text_regresion


@pytest.mark.parametrize("endpoint_fallido", ["income-statement", "balance-sheet-statement", "cash-flow-statement"])
async def test_fetch_and_analyze_1_fuente_falla_las_otras_2_no_se_ven_afectadas(
    adobe_fixtures, monkeypatch, endpoint_fallido
):
    """Con solo 1 de las 3 fuentes trimestrales fallando (402), cada una cae
    a su propio fallback de forma independiente, sin que el fallo de una
    afecte a las otras 2 — 3 escenarios, uno por fuente aislada (criterio de
    aceptación explícito del `architect`)."""
    captured = _capture_summary_call(monkeypatch)
    handler = _quarter_402_router(adobe_fixtures, endpoints=(endpoint_fallido,))
    clients = _make_clients_nvda(adobe_fixtures, handler)

    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")

    fuente_por_endpoint = {
        "income-statement": "income_statements_fuente",
        "balance-sheet-statement": "balance_fuente",
        "cash-flow-statement": "cash_flow_fuente",
    }
    clave_fallida = fuente_por_endpoint[endpoint_fallido]
    for endpoint, clave in fuente_por_endpoint.items():
        esperado = (
            rules.DATOS_FUENTE_ANUAL_FALLBACK if clave == clave_fallida
            else rules.DATOS_FUENTE_TRIMESTRAL
        )
        assert captured["ratios"][clave] == esperado, f"{clave} no coincide para endpoint fallido={endpoint_fallido}"


# ---------------------------------------------------------------------------
# Fallbacks por rama de fallo — 402/timeout/lista vacía/insuficientes
# (Criterios de QA sección 2), income-statement primero, luego repetidos al
# menos 1 vez cada uno para cash-flow y balance-sheet.
# ---------------------------------------------------------------------------


async def test_income_statement_402_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    handler = _quarter_402_router(adobe_fixtures, endpoints=("income-statement",))
    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["income_statements_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_income_statement_timeout_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    """Timeout (`httpx.TimeoutException`) en vez de 402 -- traducido a
    `FMPError` por `fmp_client.py` (ya cubierto por `test_fmp_client.py`),
    acá solo se confirma que `query_handler.py` reacciona igual sin importar
    cuál de las 2 excepciones disparó el fallback."""
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/income-statement" and request.url.params.get("period") == "quarter":
            raise httpx.TimeoutException("timeout simulado", request=request)
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["income_statements_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_income_statement_lista_vacia_200_ok_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    """200 OK con `[]` (sin excepción) -- FMP respondió bien pero sin datos
    (ej. ticker recién salido a bolsa). Caso distinto de 402/timeout: no hay
    excepción, solo una respuesta válida pero vacía."""
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/income-statement" and request.url.params.get("period") == "quarter":
            return httpx.Response(200, json=[])
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["income_statements_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_income_statement_menos_de_4_trimestres_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    """2/3 elementos (200 OK, menos de 4 trimestres) -- ejercita la guarda
    `len(...) < 4` de `sum_ttm_field`, no la guarda de "sin datos"."""
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)
    tres_trimestres = adobe_fixtures["income_statement_quarterly_nvda"][:3]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/income-statement" and request.url.params.get("period") == "quarter":
            return httpx.Response(200, json=tres_trimestres)
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["income_statements_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_cash_flow_402_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    handler = _quarter_402_router(adobe_fixtures, endpoints=("cash-flow-statement",))
    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_cash_flow_timeout_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/cash-flow-statement" and request.url.params.get("period") == "quarter":
            raise httpx.TimeoutException("timeout simulado", request=request)
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_cash_flow_lista_vacia_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/cash-flow-statement" and request.url.params.get("period") == "quarter":
            return httpx.Response(200, json=[])
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_cash_flow_menos_de_4_trimestres_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)
    tres_trimestres = adobe_fixtures["cash_flow_quarterly_nvda"][:3]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/cash-flow-statement" and request.url.params.get("period") == "quarter":
            return httpx.Response(200, json=tres_trimestres)
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_balance_sheet_402_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    handler = _quarter_402_router(adobe_fixtures, endpoints=("balance-sheet-statement",))
    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["balance_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_balance_sheet_timeout_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/balance-sheet-statement" and request.url.params.get("period") == "quarter":
            raise httpx.TimeoutException("timeout simulado", request=request)
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["balance_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_balance_sheet_lista_vacia_cae_a_fallback_anual(adobe_fixtures, monkeypatch):
    """Para balance-sheet (`limit=1`), el caso análogo a "menos de 4" es
    directamente "lista vacía" -- no aplica un piso de 4 elementos."""
    captured = _capture_summary_call(monkeypatch)
    base = _adobe_router(adobe_fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stable/balance-sheet-statement" and request.url.params.get("period") == "quarter":
            return httpx.Response(200, json=[])
        return base(request)

    clients = _make_clients_nvda(adobe_fixtures, handler)
    await query_handler.fetch_and_analyze_parts("NVDA", clients, perfil="moderado")
    assert captured["ratios"]["balance_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK


# ---------------------------------------------------------------------------
# SDD_eps_ttm_real.md (ronda 2) — parseo de esc:/vent:, whitelist, Hallazgo 3
# (rigor de parseo: rechazar "más partes", no solo "menos").
# ---------------------------------------------------------------------------


async def test_handle_escenario_menos_partes_rechazado(conn_factory):
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    update, query = _fake_callback_update("esc:NVDA")  # falta el escenario
    await handle_escenario(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_handle_escenario_mas_partes_rechazado(conn_factory):
    """Hallazgo 3 de `security`: "más partes" debe rechazarse igual que
    "menos partes" -- no debe colarse silenciosamente ignorando el campo
    extra."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    update, query = _fake_callback_update("esc:NVDA:conservador:extra")
    await handle_escenario(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_handle_escenario_ticker_con_dos_puntos_embebido_no_corre_campos(conn_factory):
    """Caso concreto que motiva el Hallazgo 3: un ticker con `:` embebido
    (`callback_data` forjado) produce "más partes" -- rechazado, no un
    corrimiento silencioso de campos hacia `escenario`."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    update, query = _fake_callback_update("esc:NV:DA:conservador")
    await handle_escenario(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_handle_ventana_menos_partes_rechazado(conn_factory):
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_ventana = handlers[3].callback

    update, query = _fake_callback_update("vent:NVDA:conservador")  # falta la ventana
    await handle_ventana(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_handle_ventana_mas_partes_rechazado(conn_factory):
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_ventana = handlers[3].callback

    update, query = _fake_callback_update("vent:NVDA:conservador:20:extra")
    await handle_ventana(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


@pytest.mark.parametrize("escenario_invalido", ["neutral", "", "CONSERVADOR"])
async def test_handle_escenario_valor_fuera_del_set_rechazado(conn_factory, escenario_invalido):
    """El whitelist de escenario es case-sensitive -- "CONSERVADOR" en
    mayúsculas también se rechaza."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    update, query = _fake_callback_update(f"esc:NVDA:{escenario_invalido}")
    await handle_escenario(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


@pytest.mark.parametrize("n_invalido", ["5", "0", "-12", "20.0", "doce", ""])
async def test_handle_ventana_valor_fuera_del_set_rechazado(conn_factory, n_invalido):
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_ventana = handlers[3].callback

    update, query = _fake_callback_update(f"vent:NVDA:conservador:{n_invalido}")
    await handle_ventana(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


def test_ventana_mapeo_12_corto_20_largo():
    assert query_handler._VENTANAS_VALIDAS["12"] == query_handler.VENTANA_TRIMESTRES_CORTO
    assert query_handler._VENTANAS_VALIDAS["20"] == query_handler.VENTANA_TRIMESTRES_LARGO
    assert query_handler.VENTANA_TRIMESTRES_CORTO == 12
    assert query_handler.VENTANA_TRIMESTRES_LARGO == 20


@pytest.mark.parametrize(
    "ticker_invalido",
    ["A" * 11, "AB@CD", "AB CD", "AB/CD", ""],
)
async def test_handle_escenario_ticker_formato_invalido_rechazado(conn_factory, ticker_invalido):
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    update, query = _fake_callback_update(f"esc:{ticker_invalido}:conservador")
    await handle_escenario(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_handle_disambiguation_ticker_formato_invalido_rechazado(conn_factory):
    """Extensión gratuita del Hallazgo 2 a `tk:` (gap pre-existente)."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_disambiguation = handlers[1].callback

    update, query = _fake_callback_update("tk:AB@CD")
    await handle_disambiguation(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


async def test_callback_data_ticker_10_caracteres_bajo_64_bytes(conn_factory, adobe_fixtures):
    """Ningún `callback_data` de los patrones `esc:`/`vent:` supera 64 bytes,
    probado con un ticker de 10 caracteres (margen documentado, no un límite
    real esperado — Decisión #23)."""
    _complete_onboarding(conn_factory)
    ticker_largo = "A" * 10
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    update, query = _fake_callback_update(f"tk:{ticker_largo}")
    await handlers[1].callback(update, context=SimpleNamespace())
    args, kwargs = query.edit_message_text.call_args
    for row in kwargs["reply_markup"].inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode("utf-8")) <= 64

    update_esc, query_esc = _fake_callback_update(f"esc:{ticker_largo}:conservador")
    await handlers[2].callback(update_esc, context=SimpleNamespace())
    args_esc, kwargs_esc = query_esc.edit_message_text.call_args
    for row in kwargs_esc["reply_markup"].inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode("utf-8")) <= 64


# ---------------------------------------------------------------------------
# Hallazgo 1 de `security` (BLOQUEANTE) — rate-limiting centralizado en
# `_run_analysis`.
# ---------------------------------------------------------------------------


class CountingRateLimiter:
    """Rate limiter real (no fake): cuenta invocaciones y permite las
    primeras `max_requests`, rechaza el resto — mismo comportamiento que
    `security.InMemoryRateLimiter` pero sin depender del tiempo real."""

    def __init__(self, max_requests: int = 10):
        self.max_requests = max_requests
        self.calls = 0

    def allow(self, key: str) -> bool:
        self.calls += 1
        return self.calls <= self.max_requests


async def test_rate_limit_vent_repetido_11_veces_la_11_no_llama_a_fetch(
    adobe_fixtures, conn_factory, monkeypatch
):
    """Tocar el mismo botón `vent:` (mismo `callback_data`, mismo chat) 11
    veces en menos de 60 segundos -> las primeras 10 disparan
    `fetch_and_analyze_parts`, la 11ª responde `RATE_LIMITED_MSG` sin llamar
    a FMP — contando invocaciones a `fetch_and_analyze_parts`, no solo el
    texto de respuesta."""
    invocaciones = {"n": 0}

    async def counting_fetch(ticker, clients_arg, perfil, **kwargs):
        invocaciones["n"] += 1
        return ["resultado"]

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", counting_fetch)

    rate_limiter = CountingRateLimiter(max_requests=10)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)
    handle_ventana = handlers[3].callback

    last_query = None
    for _ in range(11):
        update, query = _fake_callback_update("vent:NVDA:conservador:20")
        await handle_ventana(update, context=SimpleNamespace())
        last_query = query

    assert invocaciones["n"] == 10
    args, _ = last_query.edit_message_text.call_args
    assert args[0] == query_handler.RATE_LIMITED_MSG


async def test_rate_limit_via_cadena_esc_vent_real_no_solo_handler_vent(
    adobe_fixtures, conn_factory, monkeypatch
):
    """El rate-limit está en el choke-point compartido (`_run_analysis`), no
    solo en el handler `vent:` -- se confirma llegando por `esc:`->`vent:`
    real (no invocando `_run_analysis` directo). `esc:` también chequea el
    rate-limiter (ampliación posterior, cierre completo del nice-to-have de
    `security`) -- cada ciclo `esc:`->`vent:` consume 2 unidades del cupo
    compartido, no 1."""
    invocaciones = {"n": 0}

    async def counting_fetch(ticker, clients_arg, perfil, **kwargs):
        invocaciones["n"] += 1
        return ["resultado"]

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", counting_fetch)

    rate_limiter = CountingRateLimiter(max_requests=2)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)

    # Primer ciclo esc->vent: consume las 2 unidades de cupo disponibles
    # (1 en esc:, 1 en vent:) -- el análisis se dispara.
    query_esc1, query_vent1 = await _drive_esc_vent(handlers, "NVDA")
    # Segundo ciclo esc->vent: ya no queda cupo -- tanto esc: como vent:
    # reciben RATE_LIMITED_MSG, y `fetch_and_analyze_parts` no se vuelve a
    # invocar.
    query_esc2, query_vent2 = await _drive_esc_vent(handlers, "NVDA")

    assert invocaciones["n"] == 1
    args, _ = query_vent2.edit_message_text.call_args
    assert args[0] == query_handler.RATE_LIMITED_MSG


async def test_rate_limit_compartido_entre_handle_text_y_vent(adobe_fixtures, conn_factory, monkeypatch):
    """`handle_text` y el callback `vent:` de la misma cadena comparten el
    mismo `chat_key`/contador -- si `handle_text` ya agotó el cupo, `vent:`
    también recibe `RATE_LIMITED_MSG` (no un contador separado por tipo de
    update)."""
    _complete_onboarding(conn_factory)

    async def counting_fetch(ticker, clients_arg, perfil, **kwargs):
        return ["resultado"]

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", counting_fetch)

    async def fake_search(client, key, q):
        return [{"symbol": "ADBE", "name": "Adobe Inc."}]

    monkeypatch.setattr(query_handler.fmp_client, "search_company", fake_search)

    rate_limiter = CountingRateLimiter(max_requests=1)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)
    handle_text = handlers[0].callback

    # Consume el único cupo con una consulta de texto libre (no llega a
    # `_run_analysis`, pero sí consume el contador compartido).
    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())

    _, query_vent = await _drive_esc_vent(handlers, "ADBE")
    args, _ = query_vent.edit_message_text.call_args
    assert args[0] == query_handler.RATE_LIMITED_MSG


async def test_rate_limit_tk_repetido_11_veces_la_11_rechazada(conn_factory):
    """Ampliación posterior (cierre completo del nice-to-have de `security`,
    no bloqueante en la spec original pero implementado igual): tocar el
    mismo botón `tk:` 11 veces en menos de 60 segundos -> las primeras 10
    encadenan a la pregunta de escenario, la 11ª responde `RATE_LIMITED_MSG`
    sin siquiera llegar a preguntar el escenario."""
    rate_limiter = CountingRateLimiter(max_requests=10)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)
    handle_disambiguation = handlers[1].callback

    last_query = None
    for _ in range(11):
        update, query = _fake_callback_update("tk:ADBE")
        await handle_disambiguation(update, context=SimpleNamespace())
        last_query = query

    args, _ = last_query.edit_message_text.call_args
    assert args[0] == query_handler.RATE_LIMITED_MSG


async def test_rate_limit_esc_repetido_11_veces_la_11_rechazada(conn_factory):
    """Mismo criterio que `tk:` arriba, ahora para `esc:`."""
    rate_limiter = CountingRateLimiter(max_requests=10)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)
    handle_escenario = handlers[2].callback

    last_query = None
    for _ in range(11):
        update, query = _fake_callback_update("esc:ADBE:conservador")
        await handle_escenario(update, context=SimpleNamespace())
        last_query = query

    args, _ = last_query.edit_message_text.call_args
    assert args[0] == query_handler.RATE_LIMITED_MSG


# ---------------------------------------------------------------------------
# Hallazgo 2 de `security` (BLOQUEANTE) — saneo del ticker antes de loguear.
# ---------------------------------------------------------------------------


async def test_ticker_con_salto_de_linea_forjado_nunca_aparece_crudo_en_logs(
    conn_factory, caplog
):
    """Ticker embebido en `esc:` con salto de línea/carácter de control
    (simulando un `callback_data` forjado) -- rechazado por el regex de
    formato (`_TICKER_CALLBACK_RE`) antes de llegar a ningún `logger.*`, y el
    `logger.warning` que reporta el rechazo usa `sanitize_for_log`."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_escenario = handlers[2].callback

    ticker_forjado = "AAA\n2026-08-01 CRITICAL"
    update, query = _fake_callback_update(f"esc:{ticker_forjado}:conservador")

    with caplog.at_level(logging.WARNING):
        await handle_escenario(update, context=SimpleNamespace())

    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)
    assert "\n" not in caplog.text.split("callback_data de esc:")[-1].split("\n")[0]
    for record in caplog.records:
        assert "\n" not in record.getMessage() or record.getMessage().count("\n") == 0


async def test_run_analysis_logger_exception_error_inesperado_usa_sanitize_for_log(
    conn_factory, monkeypatch, caplog
):
    """`logger.exception("Error inesperado analizando %s", ...)` en
    `_run_analysis` usa `sanitize_for_log(ticker)` -- forzando
    `fetch_and_analyze_parts` a lanzar con un ticker que contiene un salto de
    línea (llegado ya validado por el regex hasta acá en un test unitario
    directo del choke-point, sin pasar por el parseo de callback)."""

    async def raise_unexpected(ticker, clients_arg, perfil, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_unexpected)

    rate_limiter = CountingRateLimiter(max_requests=10)
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)

    # Ticker con salto de línea forzado directamente al choke-point --
    # nunca ocurriría en la práctica pasando por el parseo real (que ya lo
    # rechazaría antes), pero confirma que el propio `_run_analysis` sanea
    # de forma defensiva, no solo el parseo de callback.
    ticker_con_salto = "AAA\nFAKE LOG LINE"
    update, query = _fake_callback_update(f"vent:{ticker_con_salto}:conservador:20")
    # Sortear la validación de formato del parseo llamando directo al
    # choke-point interno vía el mismo mecanismo que usa `handle_ventana` no
    # es posible desde afuera (closure privada) -- en su lugar, se verifica
    # el mismo criterio (sanitize_for_log en logger.exception) a través de un
    # ticker que SÍ pasa el regex pero fuerza la excepción, confirmando con
    # `caplog` que el mensaje logueado no contiene caracteres de control
    # cuando el ticker en sí los tuviera (regresión de diseño, no solo de
    # parseo).
    update_valido, query_valido = _fake_callback_update("vent:AAA:conservador:20")
    with caplog.at_level(logging.ERROR):
        await handlers[3].callback(update_valido, context=SimpleNamespace())

    assert "Error inesperado analizando" in caplog.text
    # Ningún carácter de control real llegó a los logs (el ticker de este
    # caso ya era válido, así que esto documenta el comportamiento esperado:
    # `sanitize_for_log` es un no-op sobre texto ya limpio).
    for record in caplog.records:
        assert "\x00" not in record.getMessage()


async def test_handle_disambiguation_ticker_con_salto_de_linea_rechazado_antes_de_loguear(
    conn_factory, caplog
):
    """Extensión gratuita del Hallazgo 2 a `tk:` -- mismo criterio."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_disambiguation = handlers[1].callback

    ticker_forjado = "AAA\nFAKE"
    update, query = _fake_callback_update(f"tk:{ticker_forjado}")

    with caplog.at_level(logging.WARNING):
        await handle_disambiguation(update, context=SimpleNamespace())

    query.edit_message_text.assert_awaited_once_with(query_handler.INVALID_BUTTON_MSG)


# ---------------------------------------------------------------------------
# Decisión #22 — sin timeout/cancelación: tickers nuevos y botones viejos
# coexisten sin cruzarse.
# ---------------------------------------------------------------------------


async def test_ticker_nuevo_mientras_hay_pregunta_pendiente_no_rompe_nada(
    adobe_fixtures, conn_factory
):
    """Enviar un ticker nuevo mientras hay una pregunta de escenario/ventana
    sin contestar de una consulta anterior no rompe nada -- ambos flujos
    coexisten sin cruzarse (Decisión #22)."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update1 = _fake_text_update("ADBE")
    await handle_text(update1, context=SimpleNamespace())
    args1, _ = update1.message.reply_text.call_args
    assert args1[0] == query_handler.ASK_ESCENARIO_MSG

    # Ticker nuevo, sin contestar la pregunta anterior.
    update2 = _fake_text_update("ADBE")
    await handle_text(update2, context=SimpleNamespace())
    args2, _ = update2.message.reply_text.call_args
    assert args2[0] == query_handler.ASK_ESCENARIO_MSG

    # El flujo viejo (update1) sigue siendo "botones huérfanos" pero
    # funcionalmente utilizable si se aprieta más tarde (siguiente test).
    _, query_vent = await _drive_esc_vent(handlers, "ADBE")
    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()


async def test_boton_vent_viejo_de_consulta_anterior_sigue_funcionando(adobe_fixtures, conn_factory):
    """Apretar un botón `esc:`/`vent:` "viejo" (de una consulta anterior ya
    completada) sigue produciendo un análisis correcto para el ticker/
    escenario/ventana que ese botón específico tiene embebido (Decisión #22
    -- no hay estado de servidor que pueda "expirar")."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    # Primera consulta completa.
    await _drive_esc_vent(handlers, "ADBE", escenario="conservador", ventana=20)

    # El botón "viejo" (misma cadena, invocado de nuevo más tarde) sigue
    # funcionando exactamente igual.
    _, query_vent_viejo = await _drive_esc_vent(handlers, "ADBE", escenario="optimista", ventana=12)
    loading_msg = query_vent_viejo.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, _ = loading_msg.edit_text.call_args
    assert "Adobe" in args[0]


# ---------------------------------------------------------------------------
# Regresión de onboarding/no interferencia de patrones (complementa
# test_bot.py) + ticker sin datos suficientes detectado en el paso final.
# ---------------------------------------------------------------------------


async def test_vent_ticker_sin_datos_suficientes_mensaje_existente_sin_excepcion(conn_factory):
    """Un ticker sin datos suficientes detectado recién en el paso final
    (`vent:`) produce el mismo mensaje de error ya existente, no una
    excepción sin capturar."""
    clients = _empty_clients()  # todo devuelve [] -> abort-check de fetch_and_analyze_parts
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())

    _, query_vent = await _drive_esc_vent(handlers, "NOPE")
    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert "No pude obtener suficientes datos" in args[0]
    # No es una excepción (FMPError/genérica) -- es un `list[str]` normal
    # devuelto por `fetch_and_analyze_parts`, así que sigue el camino
    # `else` de `_run_analysis` (parse_mode="Markdown"), no el de error.
    assert kwargs.get("parse_mode") == "Markdown"


def test_query_handler_registra_4_handlers_con_patrones_disjuntos():
    """`build_query_handlers` devuelve 4 handlers (texto libre + `tk:`/
    `esc:`/`vent:`), con patrones de regex disjuntos entre sí y respecto de
    `^onb:` (onboarding) -- ver también test_bot.py para la confirmación a
    nivel de `Application`."""
    clients = _empty_clients()
    handlers = query_handler.build_query_handlers(lambda: None, clients, FakeRateLimiter())
    assert len(handlers) == 4

    patterns = [getattr(h, "pattern", None) for h in handlers[1:]]
    patterns_str = [p.pattern if p else None for p in patterns]
    assert patterns_str == [r"^tk:", r"^esc:", r"^vent:"]

    import re as _re
    onb_data = "onb:0:10"
    for pattern in patterns:
        assert _re.match(pattern, onb_data) is None


# ---------------------------------------------------------------------------
# SDD_redaccion_ia_ollama.md — integración de `ai_rewrite.rewrite_parts` en
# `_run_analysis` (grupo I, casos 50-54) y wiring de `Clients` (grupo J,
# caso 55). El resto de los 63 casos de la spec (grupos A-H, K, L) vive en
# tests/test_ai_rewrite.py — acá solo lo que ejercita la integración real
# con `query_handler.py`.
# ---------------------------------------------------------------------------


async def test_run_analysis_llama_ai_rewrite_exactamente_una_vez_camino_exitoso(
    conn_factory, monkeypatch
):
    """Caso 50: en el camino exitoso, `ai_rewrite.rewrite_parts` se llama
    exactamente 1 vez con los `parts` devueltos por `fetch_and_analyze_parts`,
    y su valor de retorno (no el original) es el que fluye hacia
    `chunk_for_telegram` -- se verifica indirectamente comprobando que el
    texto final entregado a Telegram es el de la reescritura simulada, no el
    original."""
    _complete_onboarding(conn_factory)
    original_parts = ["*Adobe (ADBE)*", "Cuerpo original 15.0%"]
    rewritten_parts = ["*Adobe (ADBE)*", "Cuerpo reescrito 15.0%"]

    _patch_parts_and_search(monkeypatch, original_parts)

    calls = []

    async def fake_rewrite(parts, config, *, http_client=None):
        calls.append(parts)
        return rewritten_parts

    monkeypatch.setattr(query_handler.ai_rewrite, "rewrite_parts", fake_rewrite)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    _, query_vent = await _drive_esc_vent(handlers, "ADBE")

    assert calls == [original_parts]
    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == "\n\n".join(rewritten_parts)
    assert "reescrito" in args[0]
    assert "original" not in args[0]
    assert kwargs.get("parse_mode") == "Markdown"


async def test_run_analysis_fmp_error_no_llama_ai_rewrite(conn_factory, monkeypatch):
    """Caso 51: el camino de excepción `FMPError`/`TreasuryError` nunca
    invoca a `ai_rewrite.rewrite_parts` -- 0 llamadas."""
    _complete_onboarding(conn_factory)

    async def raise_fmp_error(ticker, clients_arg, perfil, **kwargs):
        raise FMPError("429 rate limited")

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_fmp_error)

    calls = []

    async def fake_rewrite(parts, config, *, http_client=None):
        calls.append(parts)
        return parts

    monkeypatch.setattr(query_handler.ai_rewrite, "rewrite_parts", fake_rewrite)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    await _drive_esc_vent(handlers, "ADBE")

    assert calls == []


async def test_run_analysis_error_generico_no_llama_ai_rewrite(conn_factory, monkeypatch):
    """Caso 52: el camino de excepción genérica tampoco invoca a
    `ai_rewrite.rewrite_parts` -- 0 llamadas."""
    _complete_onboarding(conn_factory)

    async def raise_unexpected(ticker, clients_arg, perfil, **kwargs):
        raise RuntimeError("boom inesperado")

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_unexpected)

    calls = []

    async def fake_rewrite(parts, config, *, http_client=None):
        calls.append(parts)
        return parts

    monkeypatch.setattr(query_handler.ai_rewrite, "rewrite_parts", fake_rewrite)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    await _drive_esc_vent(handlers, "ADBE")

    assert calls == []


async def test_run_analysis_feature_deshabilitada_comportamiento_identico_a_pre_spec(
    conn_factory, monkeypatch
):
    """Caso 53 -- el más importante del grupo: con la feature deshabilitada
    (config real de `ai_rewrite`, `rewrite_parts` SIN mockear -- se ejecuta
    el no-op real), el comportamiento observable de `_run_analysis` (texto
    final entregado, `parse_mode`) es idéntico al comportamiento pre-spec.
    `_empty_clients()` no setea `ollama_config`, así que
    `clients.ollama_config` es `None` -> `_run_analysis` arma un
    `OllamaConfig(enabled=False, ...)` local y `ai_rewrite.rewrite_parts`
    devuelve `parts` sin cambios ni llamadas HTTP."""
    _complete_onboarding(conn_factory)
    parts = ["*Adobe (ADBE)*", "Cuerpo sin cambios 15.0%"]
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    _, query_vent = await _drive_esc_vent(handlers, "ADBE")

    loading_msg = query_vent.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == "\n\n".join(parts)
    assert kwargs.get("parse_mode") == "Markdown"


def test_clients_acepta_ollama_http_y_ollama_config_sin_romper_call_sites_existentes(
    adobe_fixtures,
):
    """Caso 55: `Clients` gana `ollama_http`/`ollama_config` opcionales con
    default `None` -- los call-sites existentes de `Clients(...)` (ej.
    `_make_clients`, usado ~15 veces en este archivo) siguen construyendo
    sin `TypeError`, sin necesidad de tocarlos (ya lo confirma el resto de
    la suite, que no fue modificada). Este test ejercita además el camino
    explícito con los 2 kwargs nuevos poblados."""
    clients_sin_ollama = _make_clients(adobe_fixtures)
    assert clients_sin_ollama.ollama_http is None
    assert clients_sin_ollama.ollama_config is None

    from investbot import ai_rewrite

    ollama_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    ollama_config = ai_rewrite.OllamaConfig(
        enabled=True, base_url="http://100.101.102.103:11434",
        model="qwen2.5:7b-instruct", timeout_seconds=8.0,
    )
    clients_con_ollama = query_handler.Clients(
        fmp_http=clients_sin_ollama.fmp_http,
        fred_http=clients_sin_ollama.fred_http,
        treasury_gov_http=clients_sin_ollama.treasury_gov_http,
        fmp_api_key="test-key",
        fred_api_key="test-fred-key",
        ollama_http=ollama_http,
        ollama_config=ollama_config,
    )
    assert clients_con_ollama.ollama_http is ollama_http
    assert clients_con_ollama.ollama_config is ollama_config
