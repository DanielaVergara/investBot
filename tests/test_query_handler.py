"""Tests de `query_handler.py` — orquestación del análisis completo.

Sin red real: los clientes HTTP se arman con `httpx.MockTransport`, ruteando
por path a los fixtures de `tests/fixtures/adobe/`.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram.error import TelegramError

from investbot import db, query_handler
from investbot.fmp_client import FMPError

ALLOWED_CHAT_ID = 555


def _adobe_router(adobe_fixtures):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        symbol = request.url.params.get("symbol")
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
            if symbol == "ADBE":
                return httpx.Response(200, json=adobe_fixtures["key_metrics_own"])
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
    update.message.reply_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = update.message.reply_text.return_value
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


async def test_handle_disambiguation_resuelve_y_responde(adobe_fixtures, conn_factory):
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_disambiguation = handlers[1].callback

    update = SimpleNamespace()
    query = SimpleNamespace(
        data="tk:ADBE",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(return_value=_fake_message()),
    )
    update.callback_query = query
    update.effective_chat = SimpleNamespace(id=ALLOWED_CHAT_ID, type="private")

    await handle_disambiguation(update, context=SimpleNamespace())
    query.edit_message_text.assert_awaited_once()
    loading_msg = query.edit_message_text.return_value
    loading_msg.edit_text.assert_awaited_once()
    args, kwargs = loading_msg.edit_text.call_args
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

    # `_run_analysis` llama a `fetch_and_analyze_parts` (no `fetch_and_analyze`,
    # que ahora es solo un wrapper de compatibilidad) — Decisión 17 de
    # SDD_contenido_financiero_explicado.md.
    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_unexpected)
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
    update.message.reply_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = update.message.reply_text.return_value
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == query_handler.GENERIC_ERROR_MSG
    assert "parse_mode" not in kwargs


async def test_run_analysis_fmp_error_dentro_de_fetch_and_analyze(conn_factory, monkeypatch):
    """FMPError/TreasuryError lanzado dentro de `fetch_and_analyze` (no en
    `search_company`, que ya tiene su propio test) también debe llegar vía
    `.edit_text` sobre el mensaje de carga, no vía una segunda llamada
    directa a `reply_fn`."""
    _complete_onboarding(conn_factory)

    async def raise_fmp_error(ticker, clients_arg, perfil):
        raise FMPError("429 rate limited")

    async def fake_search(client, key, q):
        return [{"symbol": "ADBE", "name": "Adobe Inc."}]

    # `_run_analysis` llama a `fetch_and_analyze_parts` (no `fetch_and_analyze`,
    # que ahora es solo un wrapper de compatibilidad) — Decisión 17 de
    # SDD_contenido_financiero_explicado.md.
    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", raise_fmp_error)
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
    update.message.reply_text.assert_awaited_once_with(
        query_handler.LOADING_MSG.format(ticker="ADBE")
    )
    loading_msg = update.message.reply_text.return_value
    args, kwargs = loading_msg.edit_text.call_args
    assert args[0] == "429 rate limited"
    assert "parse_mode" not in kwargs


# ---------------------------------------------------------------------------
# Mensaje de carga — matriz envío/edit OK-falla (spec SDD_mensaje_cargando.md)
# ---------------------------------------------------------------------------


async def test_handle_text_falla_envio_mensaje_carga_no_bloquea_analisis(
    adobe_fixtures, conn_factory
):
    """Si el envío del mensaje de carga falla (TelegramError), el análisis
    real sigue igual y el resultado final llega por una llamada directa a
    `reply_fn` (sin `.edit_text`, porque no hay `Message` que editar)."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    update.message.reply_text = AsyncMock(
        side_effect=[TelegramError("boom"), _fake_message()]
    )

    await handle_text(update, context=SimpleNamespace())

    assert update.message.reply_text.await_count == 2
    first_args, _ = update.message.reply_text.call_args_list[0]
    assert first_args[0] == query_handler.LOADING_MSG.format(ticker="ADBE")
    final_args, final_kwargs = update.message.reply_text.call_args_list[1]
    assert "Adobe" in final_args[0]
    assert final_kwargs.get("parse_mode") == "Markdown"


async def test_handle_text_falla_edit_final_hace_fallback_a_reply_fn(
    adobe_fixtures, conn_factory
):
    """Si el edit final sobre el mensaje de carga falla (TelegramError), se
    hace un único intento de fallback llamando a `reply_fn` directamente con
    el contenido final — nunca se pierde la respuesta."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    loading_msg = _fake_message(edit_text=AsyncMock(side_effect=TelegramError("boom")))
    update.message.reply_text = AsyncMock(return_value=loading_msg)

    await handle_text(update, context=SimpleNamespace())

    assert update.message.reply_text.await_count == 2
    loading_msg.edit_text.assert_awaited_once()
    final_args, final_kwargs = update.message.reply_text.call_args_list[1]
    assert "Adobe" in final_args[0]
    assert final_kwargs.get("parse_mode") == "Markdown"


async def test_handle_text_excepcion_no_telegram_en_envio_carga_se_propaga(
    adobe_fixtures, conn_factory
):
    """El `except TelegramError` acotado no debe convertirse en un catch-all:
    una excepción que no sea de Telegram (p.ej. `ValueError`) debe propagarse
    sin ser tragada como si fuera un fallo best-effort de la API de Telegram."""
    _complete_onboarding(conn_factory)
    clients = _make_clients(adobe_fixtures)
    handlers = query_handler.build_query_handlers(conn_factory, clients, FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    update.message.reply_text = AsyncMock(side_effect=ValueError("no es un TelegramError"))

    with pytest.raises(ValueError):
        await handle_text(update, context=SimpleNamespace())


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
    async def fake_parts(ticker, clients_arg, perfil):
        return parts

    async def fake_search(client, key, q):
        return [{"symbol": "ADBE", "name": "Adobe Inc."}]

    monkeypatch.setattr(query_handler, "fetch_and_analyze_parts", fake_parts)
    monkeypatch.setattr(query_handler.fmp_client, "search_company", fake_search)


async def test_run_analysis_multichunk_feliz_edit_y_send_message_en_orden(conn_factory, monkeypatch):
    """Escenario 2/6: caso feliz multi-chunk. `loading_msg.edit_text` recibe
    el chunk 1 sin prefijo; cada chunk siguiente llega vía
    `loading_msg.chat.send_message`, en orden, con el prefijo correcto."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())

    loading_msg = update.message.reply_text.return_value
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
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    final_msg = _fake_message()
    update.message.reply_text = AsyncMock(side_effect=[TelegramError("boom carga"), final_msg])

    await handle_text(update, context=SimpleNamespace())

    assert update.message.reply_text.await_count == 2
    final_args, final_kwargs = update.message.reply_text.call_args_list[1]
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
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    loading_msg = _fake_message(edit_text=AsyncMock(side_effect=TelegramError("boom edit")))
    update.message.reply_text = AsyncMock(return_value=loading_msg)

    await handle_text(update, context=SimpleNamespace())

    assert update.message.reply_text.await_count == 2
    loading_msg.edit_text.assert_awaited_once()
    final_args, final_kwargs = update.message.reply_text.call_args_list[1]
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
    propaga fuera de `handle_text`. El mock de fallo se instala antes de
    invocar `handle_text`, sobre el mismo `_fake_message` que se devuelve
    como `loading_msg`."""
    _complete_onboarding(conn_factory)
    parts = _multichunk_parts()
    _patch_parts_and_search(monkeypatch, parts)

    handlers = query_handler.build_query_handlers(conn_factory, _empty_clients(), FakeRateLimiter())
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    loading_msg = _fake_message(
        chat=SimpleNamespace(
            send_message=AsyncMock(side_effect=[TelegramError("boom parte 2"), None])
        )
    )
    update.message.reply_text = AsyncMock(return_value=loading_msg)

    with caplog.at_level(logging.ERROR):
        await handle_text(update, context=SimpleNamespace())

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
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")

    with caplog.at_level(logging.ERROR):
        await handle_text(update, context=SimpleNamespace())

    loading_msg = update.message.reply_text.return_value
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
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    loading_msg = _fake_message(
        chat=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("bug no relacionado")))
    )
    update.message.reply_text = AsyncMock(return_value=loading_msg)

    with pytest.raises(RuntimeError):
        await handle_text(update, context=SimpleNamespace())


async def test_run_analysis_multichunk_cero_llamadas_http_nuevas(conn_factory, monkeypatch):
    """Cero llamadas nuevas a FMP/FRED/Treasury.gov en toda la Ampliación
    #2 — es 100% interacción con la API de Telegram. Se cuentan los
    requests capturados por el `MockTransport` (debería ser 0, porque tanto
    `fetch_and_analyze_parts` como `search_company` están mockeados sin red
    real en este test de integración de entrega multi-mensaje)."""
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
    handle_text = handlers[0].callback

    update = _fake_text_update("ADBE")
    await handle_text(update, context=SimpleNamespace())

    assert call_count["n"] == 0
