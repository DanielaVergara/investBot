"""Tests de `query_handler.py` — orquestación del análisis completo.

Sin red real: los clientes HTTP se arman con `httpx.MockTransport`, ruteando
por path a los fixtures de `tests/fixtures/adobe/`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investbot import db, query_handler
from investbot.fmp_client import FMPError

ALLOWED_CHAT_ID = 555


def _adobe_router(adobe_fixtures):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        symbol = request.url.params.get("symbol")
        if path == "/stable/quote":
            return httpx.Response(200, json=adobe_fixtures["quote"])
        if path == "/stable/key-metrics":
            peer_data = adobe_fixtures["peers_metrics"].get(symbol)
            if peer_data:
                return httpx.Response(200, json=peer_data)
            return httpx.Response(200, json=[])
        if path == "/stable/profile":
            return httpx.Response(200, json=adobe_fixtures["profile"])
        if path == "/stable/income-statement":
            return httpx.Response(200, json=adobe_fixtures["income_statement"])
        if path == "/stable/balance-sheet-statement":
            return httpx.Response(200, json=adobe_fixtures["balance_sheet"])
        if path == "/stable/cash-flow-statement":
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


def _fake_text_update(text, chat_id=ALLOWED_CHAT_ID):
    update = SimpleNamespace()
    update.message = SimpleNamespace(text=text, reply_text=AsyncMock())
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    update.callback_query = None
    return update


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


async def test_handle_text_resuelve_ticker_exacto_y_responde(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory, perfil="moderado")
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.call_args
    assert "Adobe" in args[0]


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


async def test_handle_disambiguation_resuelve_y_responde(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_disambiguation = handlers[1].callback

    update = SimpleNamespace()
    query = SimpleNamespace(
        data="tk:ADBE", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update.callback_query = query
    update.effective_chat = SimpleNamespace(id=ALLOWED_CHAT_ID, type="private")

    await handle_disambiguation(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once()
    args, kwargs = query.edit_message_text.call_args
    assert "Adobe" in args[0]


async def test_run_analysis_error_generico_no_crashea(conn_factory, monkeypatch):
    """Un error inesperado (no FMPError/TreasuryError) nunca debe propagarse
    crudo hacia Telegram — `_run_analysis` lo captura y responde con un
    mensaje genérico."""
    _complete_onboarding(conn_factory)

    async def raise_unexpected(ticker, clients_arg, perfil):
        raise RuntimeError("boom inesperado")

    async def fake_search(client, key, q):
        return [{"symbol": "ADBE", "name": "Adobe Inc."}]

    monkeypatch.setattr(query_handler, "fetch_and_analyze", raise_unexpected)
    monkeypatch.setattr(query_handler.fmp_client, "search_company", fake_search)

    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    clients = query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=empty_transport),
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )

    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text_fn = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text_fn(update, context=SimpleNamespace())
    args, kwargs = update.message.reply_text.call_args
    assert args[0] == query_handler.GENERIC_ERROR_MSG
