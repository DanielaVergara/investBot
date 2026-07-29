"""Tests de `fmp_client.py` — cliente HTTP inyectado, sin red real.

Usa `httpx.MockTransport` para simular respuestas de FMP. Ningún test toca
`financialmodelingprep.com` de verdad.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from investbot import fmp_client

SECRET_KEY = "SECRETO123"

FMP_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "fmp"


def _read_fmp_fixture_text(name: str) -> str:
    return (FMP_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_search_company_usa_params_no_fstring(adobe_fixtures):
    """Verifica que la key/query viajan como params (nunca concatenados en la URL)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["query_param"] = request.url.params.get("query")
        return httpx.Response(200, json=[{"symbol": "ADBE", "name": "Adobe Inc."}])

    client = _client_with_handler(handler)
    result = await fmp_client.search_company(client, "test-key", "Adobe")
    assert result[0]["symbol"] == "ADBE"
    assert captured["query_param"] == "Adobe"
    assert "apikey=test-key" in captured["url"]  # httpx lo agrega vía params=, url-encoded


async def test_get_quote_devuelve_primer_elemento(adobe_fixtures):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=adobe_fixtures["quote"])

    client = _client_with_handler(handler)
    quote = await fmp_client.get_quote(client, "test-key", "ADBE")
    assert quote["symbol"] == "ADBE"
    assert quote["price"] == 333.00


async def test_get_quote_lista_vacia_devuelve_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    assert await fmp_client.get_quote(client, "test-key", "NOPE") is None


async def test_search_company_sin_resultados():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    result = await fmp_client.search_company(client, "test-key", "asdfasdfasdf")
    assert result == []


async def test_search_company_multiples_resultados():
    matches = [
        {"symbol": "AAPL", "name": "Apple Inc."},
        {"symbol": "APLE", "name": "Apple Hospitality REIT"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=matches)

    client = _client_with_handler(handler)
    result = await fmp_client.search_company(client, "test-key", "Apple")
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Manejo de errores HTTP — nunca propaga la excepción cruda de httpx
# ---------------------------------------------------------------------------


async def test_error_401_no_filtra_api_key():
    """Criterio de `security` sección 3: una respuesta 401 con la key en la
    URL mockeada no debe dejar la key en el mensaje de la excepción ni en
    ningún log."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert SECRET_KEY in str(request.url)  # confirma que la key sí viaja en la request
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, SECRET_KEY, "ADBE")

    assert SECRET_KEY not in str(exc_info.value)


async def test_error_401_no_filtra_api_key_en_logs(caplog):
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(fmp_client.FMPError):
            await fmp_client.get_quote(client, SECRET_KEY, "ADBE")

    assert SECRET_KEY not in caplog.text


async def test_error_429_rate_limit_mensaje_claro():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Limit reached"})

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert "cupo diario" in str(exc_info.value)


async def test_error_5xx_mensaje_generico():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert "500" in str(exc_info.value)


async def test_timeout_traducido_a_fmp_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert "no respondió a tiempo" in str(exc_info.value)


async def test_connect_error_traducido_a_fmp_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert "No pude conectarme" in str(exc_info.value)


async def test_respuesta_no_json_traducido_a_fmp_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError):
        await fmp_client.get_quote(client, "test-key", "ADBE")


async def test_income_statement_llama_endpoint_correcto():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["symbol"] = request.url.params.get("symbol")
        captured["period"] = request.url.params.get("period")
        return httpx.Response(200, json=[{"eps": 20.0}])

    client = _client_with_handler(handler)
    await fmp_client.get_income_statement(client, "test-key", "ADBE")
    assert captured["path"] == "/stable/income-statement"
    assert captured["symbol"] == "ADBE"
    assert captured["period"] == "annual"


async def test_balance_sheet_y_cash_flow_devuelven_listas():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"totalCurrentAssets": 100}])

    client = _client_with_handler(handler)
    balance = await fmp_client.get_balance_sheet_statement(client, "test-key", "ADBE")
    assert balance[0]["totalCurrentAssets"] == 100

    def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"operatingCashFlow": 100}])

    client2 = _client_with_handler(handler2)
    cash_flow = await fmp_client.get_cash_flow_statement(client2, "test-key", "ADBE")
    assert cash_flow[0]["operatingCashFlow"] == 100


async def test_get_profile_y_key_metrics():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"sector": "Technology"}])

    client = _client_with_handler(handler)
    profile = await fmp_client.get_profile(client, "test-key", "ADBE")
    assert profile["sector"] == "Technology"

    def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"roe": 0.5}])

    client2 = _client_with_handler(handler2)
    metrics = await fmp_client.get_key_metrics(client2, "test-key", "ADBE")
    assert metrics[0]["roe"] == 0.5


# ---------------------------------------------------------------------------
# 402 "símbolo premium" — SDD_fmp_402_simbolo_premium.md
# ---------------------------------------------------------------------------

SYMBOL_PREMIUM_BODY_REAL = _read_fmp_fixture_text("402_symbol_premium_real.txt")
GENERIC_402_MESSAGE = "FMP respondió con un error (402). Intenta más tarde."


# --- Unidad: _is_symbol_premium_402 (función pura, sin red) -----------------


@pytest.mark.parametrize(
    "body_text, expected",
    [
        (SYMBOL_PREMIUM_BODY_REAL, True),
        ('{"error": "Payment Required"}', False),
        ("", False),
        (None, False),
        # Mayúsculas/minúsculas mixtas E INDEPENDIENTES entre los dos
        # substrings (no solo "todo mayúsculas" u "todo minúsculas") — evita
        # que el test pase por casualidad con un solo .upper()/.lower()
        # aplicado a todo el body de una vez.
        ("Ocurrió: Special ENDPOINT y también SymBol no disponible", True),
        # Match parcial: un solo substring presente no debe disparar el caso.
        ("Special Endpoint sin la otra palabra clave", False),
        ("symbol sin la otra frase clave", False),
    ],
)
def test_is_symbol_premium_402_casos(body_text, expected):
    assert fmp_client._is_symbol_premium_402(body_text) is expected


def test_is_symbol_premium_402_trunca_y_no_matchea_si_esta_despues_de_2000():
    """Boundary del truncado (criterio de `security`): si ambos substrings
    aparecen después de la posición ~2000, el truncado los descarta."""
    body = ("a" * 2000) + " special endpoint symbol"
    assert len(body) > 2000
    assert fmp_client._is_symbol_premium_402(body) is False


def test_is_symbol_premium_402_matchea_si_esta_antes_de_2000():
    """Contraparte del test anterior: substrings antes de la posición ~2000
    en un body de >2000 caracteres siguen evaluando True."""
    body = "special endpoint symbol " + ("a" * 2500)
    assert len(body) > 2000
    assert fmp_client._is_symbol_premium_402(body) is True


# --- Integración: _get / FMPError vía MockTransport -------------------------


async def test_error_402_simbolo_premium_mensaje_especifico():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "MELI")

    message = str(exc_info.value)
    assert "MELI" in message  # (a) menciona el ticker puntual
    assert "plan gratuito" in message  # (a) plan gratuito explícito
    assert message != GENERIC_402_MESSAGE  # (b) no es el string genérico
    assert "cupo" not in message.lower()  # (c) no confunde con 429
    assert "límite" not in message.lower()  # (c) no confunde con cupo diario
    assert "api key" not in message.lower()  # (c) no confunde con 401/403
    assert "otro ticker" in message.lower()  # (d) sugiere otro ticker


async def test_error_402_generico_body_json_no_reconocido_mensaje_sin_cambios():
    """Regresión explícita: un 402 con body distinto sigue cayendo en el
    mensaje genérico actual, sin modificación."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "Payment Required"})

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert str(exc_info.value) == GENERIC_402_MESSAGE


async def test_error_402_generico_body_vacio_mensaje_sin_cambios():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "ADBE")
    assert str(exc_info.value) == GENERIC_402_MESSAGE


async def test_error_402_simbolo_premium_no_filtra_body_ni_api_key_en_logs(caplog):
    """Mismo patrón que `test_error_401_no_filtra_api_key_en_logs`, para el
    sub-caso símbolo premium: ni el body crudo ni la API key llegan a logs."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(fmp_client.FMPError):
            await fmp_client.get_quote(client, SECRET_KEY, "MELI")

    assert SECRET_KEY not in caplog.text
    assert "Special Endpoint" not in caplog.text
    assert "subscription page" not in caplog.text
    assert "subcaso=simbolo_premium" in caplog.text


@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"json": {"error": "Payment Required"}},
        {},  # body vacío
    ],
    ids=["body_json_generico", "body_vacio"],
)
async def test_error_402_generico_no_filtra_body_ni_api_key_en_logs(
    caplog, response_kwargs
):
    """Criterio de `security`: el body se lee (para poder decidir el match)
    en toda rama de 402, no solo en la que matchea — ambas deben probarse."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, **response_kwargs)

    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(fmp_client.FMPError):
            await fmp_client.get_quote(client, SECRET_KEY, "ADBE")

    assert SECRET_KEY not in caplog.text
    assert "Payment Required" not in caplog.text
    assert "subcaso=generico" in caplog.text


async def test_error_402_body_no_legible_labelea_distinto_y_cae_a_generico(
    monkeypatch, caplog
):
    """Criterio de `security`: si falla la lectura/decodificación del body,
    (a) no propaga la excepción sin capturar, (b) el mensaje al usuario cae
    en el genérico existente, (c) el log usa el label de "no legible", no el
    de "genérico"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, content=b"algo")

    def _raise_unicode_error(self):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "byte invalido")

    monkeypatch.setattr(httpx.Response, "text", property(_raise_unicode_error))

    client = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(fmp_client.FMPError) as exc_info:
            await fmp_client.get_quote(client, SECRET_KEY, "ADBE")

    assert str(exc_info.value) == GENERIC_402_MESSAGE
    assert "subcaso=cuerpo_no_legible" in caplog.text
    assert "subcaso=generico" not in caplog.text
    assert SECRET_KEY not in caplog.text


async def test_error_402_simbolo_premium_mensaje_no_incluye_apikey_ni_otros_params():
    """El mensaje se arma únicamente con `params.get("symbol")` — ninguna
    otra clave de `params` (representativo: `apikey`) debe aparecer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, SECRET_KEY, "MELI")

    message = str(exc_info.value)
    assert SECRET_KEY not in message
    assert "apikey" not in message.lower()


async def test_error_402_simbolo_premium_sin_symbol_en_params_no_lanza_keyerror():
    """Caso defensivo (QA): si `params` no trae `"symbol"`, el mensaje no
    debe crashear con `KeyError` — puede omitir el ticker."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client._get(
            client, "/quote", {"apikey": SECRET_KEY}, endpoint_label="/quote"
        )

    message = str(exc_info.value)
    assert "plan gratuito" in message
    assert message != GENERIC_402_MESSAGE


async def test_error_402_simbolo_premium_ticker_con_punto_aparece_verbatim():
    """Ticker con caracteres no alfanuméricos comunes (`BRK.B`) aparece igual
    en el mensaje — confirma interpolación simple, no regex."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await fmp_client.get_quote(client, "test-key", "BRK.B")

    assert "BRK.B" in str(exc_info.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda client: fmp_client.get_quote(client, "test-key", "MELI"),
        lambda client: fmp_client.get_profile(client, "test-key", "MELI"),
        lambda client: fmp_client.get_income_statement(client, "test-key", "MELI"),
    ],
    ids=["/quote", "/profile", "/income-statement"],
)
async def test_error_402_simbolo_premium_en_tres_endpoints_confirmados(call):
    """El `Estado objetivo` de la spec cita `/quote`, `/profile` e
    `/income-statement` como confirmados con MELI/DRAM — los tres deben
    pasar por la misma rama de `_get`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text=SYMBOL_PREMIUM_BODY_REAL)

    client = _client_with_handler(handler)
    with pytest.raises(fmp_client.FMPError) as exc_info:
        await call(client)

    message = str(exc_info.value)
    assert "MELI" in message
    assert "plan gratuito" in message
    assert message != GENERIC_402_MESSAGE
