"""Tests de `treasury_client.py` — FRED (primario) + Treasury.gov (fallback).

Sin red real — `httpx.MockTransport` simula ambas fuentes.
"""

from __future__ import annotations

import httpx
import pytest

from investbot import treasury_client

SECRET_FRED_KEY = "FRED_SECRETO456"


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_treasury_yield_fred_caso_normal(adobe_fixtures):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=adobe_fixtures["fred_dgs20"])

    client = _client_with_handler(handler)
    y = await treasury_client.get_treasury_yield_fred(client, "test-key")
    assert y == pytest.approx(0.044)  # "4.40" / 100


async def test_get_treasury_yield_fred_ignora_valores_faltantes():
    data = {
        "observations": [
            {"date": "2026-07-23", "value": "."},  # feriado, sin dato
            {"date": "2026-07-22", "value": "4.35"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    client = _client_with_handler(handler)
    y = await treasury_client.get_treasury_yield_fred(client, "test-key")
    assert y == pytest.approx(0.0435)


async def test_get_treasury_yield_fred_sin_observaciones_lanza_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"observations": [{"date": "x", "value": "."}]})

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_fred(client, "test-key")


async def test_get_treasury_yield_fred_error_401_no_filtra_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError) as exc_info:
        await treasury_client.get_treasury_yield_fred(client, SECRET_FRED_KEY)
    assert SECRET_FRED_KEY not in str(exc_info.value)


async def test_get_treasury_yield_fred_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_fred(client, "test-key")


async def test_get_treasury_yield_fred_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_fred(client, "test-key")


async def test_get_treasury_yield_fred_no_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_fred(client, "test-key")


# ---------------------------------------------------------------------------
# Fallback Treasury.gov (CSV)
# ---------------------------------------------------------------------------


def test_parse_treasury_gov_csv():
    csv_text = (
        "Date,1 Mo,2 Mo,3 Mo,1 Yr,2 Yr,5 Yr,10 Yr,20 Yr,30 Yr\n"
        "07/24/2026,4.20,4.25,4.30,4.10,4.00,4.05,4.20,4.45,4.50\n"
        "07/25/2026,4.19,4.24,4.29,4.09,3.99,4.04,4.19,4.44,4.49\n"
    )
    value = treasury_client._parse_treasury_gov_csv(csv_text)
    assert value == pytest.approx(0.0444)  # última fila del CSV


def test_parse_treasury_gov_csv_sin_columna_20yr():
    csv_text = "Date,10 Yr\n07/24/2026,4.20\n"
    assert treasury_client._parse_treasury_gov_csv(csv_text) is None


def test_parse_treasury_gov_csv_vacio():
    assert treasury_client._parse_treasury_gov_csv("") is None


async def test_get_treasury_yield_treasury_gov_caso_normal():
    csv_text = "Date,20 Yr\n07/24/2026,4.40\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=csv_text)

    client = _client_with_handler(handler)
    y = await treasury_client.get_treasury_yield_treasury_gov(client, year=2026)
    assert y == pytest.approx(0.044)


async def test_get_treasury_yield_treasury_gov_error_http():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_treasury_gov(client, year=2026)


async def test_get_treasury_yield_treasury_gov_sin_columna_parseable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Date,10 Yr\n07/24/2026,4.20\n")

    client = _client_with_handler(handler)
    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield_treasury_gov(client, year=2026)


# ---------------------------------------------------------------------------
# Orquestación: FRED primario -> Treasury.gov fallback -> error explícito
# ---------------------------------------------------------------------------


async def test_get_treasury_yield_usa_fred_si_disponible(adobe_fixtures):
    def fred_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=adobe_fixtures["fred_dgs20"])

    def gov_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debería llamarse a Treasury.gov si FRED responde")

    result = await treasury_client.get_treasury_yield(
        _client_with_handler(fred_handler), _client_with_handler(gov_handler), "test-key"
    )
    assert result.y == pytest.approx(0.044)
    assert result.source == treasury_client.SOURCE_FRED


async def test_get_treasury_yield_fallback_a_treasury_gov():
    def fred_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    def gov_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Date,20 Yr\n07/24/2026,4.40\n")

    result = await treasury_client.get_treasury_yield(
        _client_with_handler(fred_handler), _client_with_handler(gov_handler), "test-key"
    )
    assert result.y == pytest.approx(0.044)
    assert result.source == treasury_client.SOURCE_TREASURY_GOV


async def test_get_treasury_yield_sin_fred_api_key_usa_fallback():
    def gov_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Date,20 Yr\n07/24/2026,4.40\n")

    result = await treasury_client.get_treasury_yield(
        _client_with_handler(lambda r: httpx.Response(500)),
        _client_with_handler(gov_handler),
        None,
    )
    assert result.source == treasury_client.SOURCE_TREASURY_GOV


async def test_get_treasury_yield_ambas_fuentes_fallan_lanza_error_explicito():
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(treasury_client.TreasuryError):
        await treasury_client.get_treasury_yield(
            _client_with_handler(failing_handler),
            _client_with_handler(failing_handler),
            "test-key",
        )
