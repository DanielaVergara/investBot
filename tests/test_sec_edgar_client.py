"""Tests de `sec_edgar_client.py` — mapeo ticker→CIK (cacheado, TTL 24h) +
historial de filings (`submissions`). Sin red real, `httpx.MockTransport`.

Matriz E1-E13 (spec `SDD_peers_dinamicos_y_eventos_corporativos.md`, sección
QA). Reset del caché de módulo obligatorio (gap 5.2 de la spec) — sin esto,
un test que puebla `_ticker_cik_cache` contamina cualquier otro test
(incluidos los de `test_query_handler.py`) que corra después en el mismo
proceso de pytest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from investbot import sec_edgar_client

USER_AGENT = "InvestBot test@example.com"


@pytest.fixture(autouse=True)
def _reset_sec_edgar_cache():
    sec_edgar_client._ticker_cik_cache.clear()
    sec_edgar_client._cache_loaded_at = None
    yield
    sec_edgar_client._ticker_cik_cache.clear()
    sec_edgar_client._cache_loaded_at = None


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_TICKERS_PAYLOAD = {
    "0": {"ticker": "ADBE", "cik_str": 796343},
    "1": {"ticker": "AAPL", "cik_str": 320193},
}


def _tickers_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_TICKERS_PAYLOAD)


# E1 — happy path get_cik_for_ticker
async def test_get_cik_for_ticker_happy_path():
    client = _client_with_handler(_tickers_handler)
    cik = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert cik == "0000796343"


# E2 — happy path get_submissions
async def test_get_submissions_happy_path():
    payload = {"filings": {"recent": {"form": ["8-K"]}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _client_with_handler(handler)
    result = await sec_edgar_client.get_submissions(client, USER_AGENT, "0000796343")
    assert result == payload


# E3 — get_cik_for_ticker: 4 fallos -> None, nunca excepción
def _handler_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("timeout", request=request)


def _handler_http_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500)


def _handler_network_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("failed", request=request)


def _handler_no_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"not json")


_FAILURE_HANDLERS = [
    (_handler_timeout, "timeout"),
    (_handler_http_error, "http_error"),
    (_handler_network_error, "network_error"),
    (_handler_no_json, "no_json"),
]


@pytest.mark.parametrize("handler,_id", _FAILURE_HANDLERS, ids=[i for _, i in _FAILURE_HANDLERS])
async def test_get_cik_for_ticker_fallos_devuelve_none(handler, _id):
    client = _client_with_handler(handler)
    result = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert result is None


# E4 — get_submissions: mismos 4 casos de fallo
@pytest.mark.parametrize("handler,_id", _FAILURE_HANDLERS, ids=[i for _, i in _FAILURE_HANDLERS])
async def test_get_submissions_fallos_devuelve_none(handler, _id):
    client = _client_with_handler(handler)
    result = await sec_edgar_client.get_submissions(client, USER_AGENT, "0000796343")
    assert result is None


# E5 — ticker no encontrado en el mapeo
async def test_get_cik_for_ticker_no_encontrado_devuelve_none():
    client = _client_with_handler(_tickers_handler)
    result = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ZZZZ")
    assert result is None


# E6 — header User-Agent presente en company_tickers.json
async def test_get_cik_for_ticker_envia_user_agent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(200, json=_TICKERS_PAYLOAD)

    client = _client_with_handler(handler)
    await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert captured["user_agent"] == USER_AGENT


# E7 — header User-Agent presente en submissions
async def test_get_submissions_envia_user_agent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(200, json={"filings": {}})

    client = _client_with_handler(handler)
    await sec_edgar_client.get_submissions(client, USER_AGENT, "0000796343")
    assert captured["user_agent"] == USER_AGENT


# E8 — caché dentro del TTL: 2 llamadas seguidas -> 1 sola request HTTP real
async def test_cache_dentro_de_ttl_una_sola_request():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_TICKERS_PAYLOAD)

    client = _client_with_handler(handler)
    await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "AAPL")
    assert call_count["n"] == 1


# E9 — caché expirado (asignación directa a _cache_loaded_at, sin mockear el
# reloj — técnica fijada en la sección 5.3 de la spec).
async def test_cache_expirado_vuelve_a_pedir_el_archivo():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_TICKERS_PAYLOAD)

    client = _client_with_handler(handler)
    await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert call_count["n"] == 1

    sec_edgar_client._cache_loaded_at = datetime.now(timezone.utc) - timedelta(hours=25)
    await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert call_count["n"] == 2


# E10 — company_tickers.json con formato inesperado (no es un dict)
async def test_ensure_ticker_cache_formato_no_dict_devuelve_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["no", "es", "un", "dict"])

    client = _client_with_handler(handler)
    result = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert result is None


# E11 — entradas del mapeo con forma inesperada se descartan, no rompen el resto
async def test_ensure_ticker_cache_entradas_con_forma_inesperada_se_descartan():
    payload = {
        "0": {"ticker": "ADBE", "cik_str": 796343},
        "1": "no es un dict",
        "2": {"ticker": None, "cik_str": 123},  # ticker no-string
        "3": {"ticker": "XXXX"},  # sin cik_str
        "4": {"cik_str": 999},  # sin ticker
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _client_with_handler(handler)
    result = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert result == "0000796343"
    assert "XXXX" not in sec_edgar_client._ticker_cik_cache


# E12 — fallo de red al construir el caché -> no hay negative-caching, la
# siguiente llamada reintenta
async def test_cache_fallo_no_deja_negative_caching_reintenta():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=_TICKERS_PAYLOAD)

    client = _client_with_handler(handler)
    first = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert first is None
    assert call_count["n"] == 1

    second = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert second == "0000796343"
    assert call_count["n"] == 2


# E13 — ningún log incluye el body completo de la respuesta
async def test_logs_no_incluyen_body_completo(caplog):
    sensitive_body = "SENSITIVE_CONTENT_" + ("x" * 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=sensitive_body)

    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        result = await sec_edgar_client.get_cik_for_ticker(client, USER_AGENT, "ADBE")
    assert result is None
    assert sensitive_body not in caplog.text
    assert "url=" in caplog.text
    assert "status=" in caplog.text
