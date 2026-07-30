"""Tests de `finnhub_client.py` — cliente HTTP inyectado, sin red real.

Matriz F1-F12 (spec `SDD_peers_dinamicos_y_eventos_corporativos.md`, sección
QA). Mismo patrón que `tests/test_fmp_client.py`/`tests/test_treasury_client.py`
— `httpx.MockTransport`, ningún test toca `finnhub.io` de verdad.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from investbot import finnhub_client

CANARY_KEY = "CANARY-KEY-12345"


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# F1 — happy path
async def test_get_peers_happy_path_devuelve_lista_en_orden():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["MSFT", "ORCL", "CRM", "IBM", "SAP"])

    client = _client_with_handler(handler)
    result = await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert result == ["MSFT", "ORCL", "CRM", "IBM", "SAP"]


# F2 — timeout
async def test_get_peers_timeout_traducido_a_finnhub_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(finnhub_client.FinnhubError) as exc_info:
        await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert "no respondió a tiempo" in str(exc_info.value)
    assert CANARY_KEY not in str(exc_info.value)


# F3 — HTTP error (402/403/429)
@pytest.mark.parametrize("status", [402, 403, 429])
async def test_get_peers_error_http_traducido_a_finnhub_error(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    client = _client_with_handler(handler)
    with pytest.raises(finnhub_client.FinnhubError) as exc_info:
        await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert str(status) in str(exc_info.value)
    assert CANARY_KEY not in str(exc_info.value)


# F4 — error de red
async def test_get_peers_error_de_red_traducido_a_finnhub_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(finnhub_client.FinnhubError) as exc_info:
        await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert "No pude conectarme" in str(exc_info.value)
    assert CANARY_KEY not in str(exc_info.value)


# F5 — respuesta no-JSON
async def test_get_peers_respuesta_no_json_traducido_a_finnhub_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_with_handler(handler)
    with pytest.raises(finnhub_client.FinnhubError) as exc_info:
        await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert CANARY_KEY not in str(exc_info.value)


# F6 — JSON pero no-lista
async def test_get_peers_respuesta_json_no_lista_devuelve_vacio_sin_excepcion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "unexpected shape"})

    client = _client_with_handler(handler)
    result = await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert result == []


# F7 — lista con elementos no-string
async def test_get_peers_filtra_elementos_no_string():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["MSFT", 123, None, "ORCL"])

    client = _client_with_handler(handler)
    result = await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert result == ["MSFT", "ORCL"]


# F8 — lista vacía (200 OK), no es un error
async def test_get_peers_lista_vacia_no_es_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    result = await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert result == []


# F9 — params= vía httpx, nunca f-string
async def test_get_peers_usa_params_no_fstring():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["symbol"] = request.url.params.get("symbol")
        captured["grouping"] = request.url.params.get("grouping")
        captured["token"] = request.url.params.get("token")
        captured["path"] = request.url.path
        return httpx.Response(200, json=["MSFT"])

    client = _client_with_handler(handler)
    await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert captured["symbol"] == "ADBE"
    assert captured["grouping"] == "subIndustry"
    assert captured["token"] == "test-key"
    assert captured["path"] == "/api/v1/stock/peers"


def _handler_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("timeout", request=request)


def _handler_http_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(402, json={"error": "nope"})


def _handler_network_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("failed", request=request)


def _handler_no_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"not json")


_ERROR_HANDLERS = [
    (_handler_timeout, "timeout"),
    (_handler_http_error, "http_error"),
    (_handler_network_error, "network_error"),
    (_handler_no_json, "no_json"),
]


# F10 — la API key no aparece en el mensaje de ningún FinnhubError
@pytest.mark.parametrize("handler,_id", _ERROR_HANDLERS, ids=[i for _, i in _ERROR_HANDLERS])
async def test_get_peers_api_key_nunca_en_mensaje_de_error(handler, _id):
    client = _client_with_handler(handler)
    with pytest.raises(finnhub_client.FinnhubError) as exc_info:
        await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert CANARY_KEY not in str(exc_info.value)


# F11 — ningún log incluye la API key ni la URL completa con params
@pytest.mark.parametrize("handler,_id", _ERROR_HANDLERS, ids=[i for _, i in _ERROR_HANDLERS])
async def test_get_peers_api_key_nunca_en_logs(handler, _id, caplog):
    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(finnhub_client.FinnhubError):
            await finnhub_client.get_peers(client, CANARY_KEY, "ADBE")
    assert CANARY_KEY not in caplog.text
    assert "token=" not in caplog.text


# F12 — grouping se envía tal cual se pasa (default "subIndustry")
async def test_get_peers_grouping_default_es_subindustry():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["grouping"] = request.url.params.get("grouping")
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    await finnhub_client.get_peers(client, "test-key", "ADBE")
    assert captured["grouping"] == "subIndustry"


async def test_get_peers_grouping_custom_se_respeta():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["grouping"] = request.url.params.get("grouping")
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    await finnhub_client.get_peers(client, "test-key", "ADBE", grouping="sector")
    assert captured["grouping"] == "sector"
