"""Tests de `advanced_command.py` — comando `/avanzado <TICKER>`
(`SDD_analisis_fundamental_avanzado.md`).

Integration: `httpx.MockTransport` (mismo patrón que `test_query_handler.py`)
+ objetos `Update`/`Context` simulados (mismo patrón que `test_bot.py`). El
efecto secundario de Telegram (`update.message.reply_text`) está siempre
mockeado.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram.ext import ApplicationHandlerStop

from investbot import advanced_command, query_handler, security
from investbot.fmp_client import FMPError
from tests.conftest import load_fixture

ALLOWED_CHAT_ID = 777


def _avanzado_fixture(nombre: str) -> dict:
    return {
        "profile": load_fixture("fmp", f"profile_{nombre}.json"),
        "quote": load_fixture("fmp", f"quote_{nombre}.json"),
        "income": load_fixture("fmp", f"income_annual_{nombre}.json"),
        "balance": load_fixture("fmp", f"balance_annual_{nombre}.json"),
        "cash_flow": load_fixture("fmp", f"cash_flow_annual_{nombre}.json"),
    }


@pytest.fixture
def empresa_completa():
    return _avanzado_fixture("empresa_completa")


@pytest.fixture
def empresa_asset_light():
    return _avanzado_fixture("empresa_asset_light")


@pytest.fixture
def etf_profile():
    return load_fixture("fmp", "profile_etf_spy.json")


def _router(empresa_completa, empresa_asset_light, etf_profile, request_log=None):
    """Router por símbolo — MFG (empresa completa, no asset-light), TCH
    (empresa asset-light), SPY (ETF), NOPE (ticker inexistente, `/profile`
    vacío), CRYP (sin flags de ETF/fondo pero sin estados financieros —
    red de seguridad del paso 4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request_log is not None:
            request_log.append(request.url.params.get("symbol"))
        path = request.url.path
        symbol = request.url.params.get("symbol")

        if path == "/stable/profile":
            if symbol == "MFG":
                return httpx.Response(200, json=[empresa_completa["profile"]])
            if symbol == "TCH":
                return httpx.Response(200, json=[empresa_asset_light["profile"]])
            if symbol == "SPY":
                return httpx.Response(200, json=[etf_profile])
            if symbol == "CRYP":
                return httpx.Response(
                    200,
                    json=[{"symbol": "CRYP", "companyName": "Crypto Pair", "isActivelyTrading": True}],
                )
            return httpx.Response(200, json=[])  # NOPE / cualquier otro -> inexistente

        if path == "/stable/quote":
            if symbol == "MFG":
                return httpx.Response(200, json=[empresa_completa["quote"]])
            if symbol == "TCH":
                return httpx.Response(200, json=[empresa_asset_light["quote"]])
            return httpx.Response(200, json=[])

        if path == "/stable/income-statement":
            if symbol == "MFG":
                return httpx.Response(200, json=empresa_completa["income"])
            if symbol == "TCH":
                return httpx.Response(200, json=empresa_asset_light["income"])
            return httpx.Response(200, json=[])

        if path == "/stable/balance-sheet-statement":
            if symbol == "MFG":
                return httpx.Response(200, json=empresa_completa["balance"])
            if symbol == "TCH":
                return httpx.Response(200, json=empresa_asset_light["balance"])
            return httpx.Response(200, json=[])

        if path == "/stable/cash-flow-statement":
            if symbol == "MFG":
                return httpx.Response(200, json=empresa_completa["cash_flow"])
            if symbol == "TCH":
                return httpx.Response(200, json=empresa_asset_light["cash_flow"])
            return httpx.Response(200, json=[])

        return httpx.Response(404)

    return handler


def _make_clients(
    empresa_completa, empresa_asset_light, etf_profile, request_log=None, ollama_config=None
) -> query_handler.Clients:
    fmp_http = httpx.AsyncClient(
        transport=httpx.MockTransport(_router(empresa_completa, empresa_asset_light, etf_profile, request_log))
    )
    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    return query_handler.Clients(
        fmp_http=fmp_http,
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
        ollama_config=ollama_config,
    )


class FakeRateLimiter:
    def __init__(self, allow_value=True):
        self.allow_value = allow_value
        self.calls_with_key = []

    def allow(self, key):
        self.calls_with_key.append(key)
        return self.allow_value


def _fake_avanzado_update(args, chat_id=ALLOWED_CHAT_ID):
    update = SimpleNamespace()
    update.message = SimpleNamespace(reply_text=AsyncMock())
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    context = SimpleNamespace(args=args)
    return update, context


def _handler_callback(clients, rate_limiter, explanation_store=None):
    return advanced_command.build_advanced_command_handler(
        clients, rate_limiter, explanation_store
    ).callback


# ---------------------------------------------------------------------------
# Criterio 1 — sin argumento / regex inválido -> mensaje de uso, 0 requests
# ---------------------------------------------------------------------------


async def test_sin_argumento_responde_uso_sin_llamar_fmp(empresa_completa, empresa_asset_light, etf_profile):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update([])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.USAGE_MSG)
    assert request_log == []


@pytest.mark.parametrize("args", [["AAPL!"], ["A" * 11], ["AAPL", "extra"]])
async def test_argumento_invalido_responde_uso_sin_llamar_fmp(args, empresa_completa, empresa_asset_light, etf_profile):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(args)
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.USAGE_MSG)
    assert request_log == []


async def test_ticker_10_caracteres_es_valido_llega_a_fmp(empresa_completa, empresa_asset_light, etf_profile):
    """Límite superior del regex (10 caracteres) -- se acepta y llega a FMP
    (ticker inexistente en el router, pero eso ya cuenta como "llegó")."""
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["A" * 10])
    await callback(update, context)

    assert request_log == ["A" * 10]
    update.message.reply_text.assert_awaited_once_with(advanced_command.NOT_FOUND_MSG)


async def test_ticker_11_caracteres_rechazado(empresa_completa, empresa_asset_light, etf_profile):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["A" * 11])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.USAGE_MSG)
    assert request_log == []


async def test_ticker_minusculas_se_normaliza_a_mayusculas(empresa_completa, empresa_asset_light, etf_profile):
    """Caso límite de `qa`: `/avanzado mfg` se normaliza a `MFG` antes de
    llamar a FMP (decisión explícita, documentada en `_parse_ticker`)."""
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["mfg"])
    await callback(update, context)

    assert "MFG" in request_log
    assert "mfg" not in request_log


# ---------------------------------------------------------------------------
# Criterio 2 — ticker inexistente -> "no encontré esa empresa", 1 llamada
# ---------------------------------------------------------------------------


async def test_ticker_inexistente_responde_no_encontrado_una_sola_llamada(
    empresa_completa, empresa_asset_light, etf_profile
):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["NOPE"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.NOT_FOUND_MSG)
    assert request_log == ["NOPE"]  # 1 sola llamada a FMP (/profile)


# ---------------------------------------------------------------------------
# Criterio 3 — ETF/fondo -> mensaje "no aplica", sin calcular modelos
# ---------------------------------------------------------------------------


async def test_etf_responde_no_aplica_sin_calcular_modelos(empresa_completa, empresa_asset_light, etf_profile):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["SPY"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.NOT_APPLICABLE_MSG)
    assert request_log == ["SPY"]  # solo /profile, no siguió a quote/income/etc.


# ---------------------------------------------------------------------------
# Criterio 4 — sin estados financieros (red de seguridad del paso 4)
# ---------------------------------------------------------------------------


async def test_sin_estados_financieros_responde_no_aplica_red_de_seguridad(
    empresa_completa, empresa_asset_light, etf_profile
):
    """CRYP no tiene flags de ETF/fondo en `/profile`, pero balance/income/
    cash-flow vienen vacíos -- misma respuesta que el caso ETF (Decisión de
    diseño #2, red de seguridad del paso 4)."""
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["CRYP"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.NOT_APPLICABLE_MSG)
    # profile + quote + income + balance + cash_flow = 5 llamadas (todas
    # vacías salvo profile), no se detuvo antes.
    assert request_log.count("CRYP") == 5


# ---------------------------------------------------------------------------
# Criterio 5 — empresa individual completa -> los 5 modelos en el formato
# ---------------------------------------------------------------------------


async def test_empresa_completa_incluye_los_5_modelos_en_formato(empresa_completa, empresa_asset_light, etf_profile):
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once()
    (mensaje,), kwargs = update.message.reply_text.call_args
    assert "MFG — Manufacturing Co Inc." in mensaje
    assert "Altman Z-Score:" in mensaje
    assert "zona: segura" in mensaje
    assert "Z''" not in mensaje  # no asset-light -> solo Z original
    assert "Piotroski F-Score: 9/9" in mensaje
    assert "Beneish M-Score:" in mensaje
    assert "no calculable" in mensaje
    assert "Magic Formula: ROIC" in mensaje
    assert "Earnings Yield" in mensaje
    assert "Factores: Value" in mensaje
    assert "Quality" in mensaje and "Momentum" in mensaje and "Low-vol" in mensaje
    assert "Fuente de los datos: FMP" in mensaje
    assert "parse_mode" not in kwargs  # hallazgo 4 de security


async def test_empresa_asset_light_incluye_z_y_z_doble_prima(empresa_completa, empresa_asset_light, etf_profile):
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["TCH"])
    await callback(update, context)

    (mensaje,), _ = update.message.reply_text.call_args
    assert "Altman Z-Score:" in mensaje
    assert "Z'':" in mensaje


# ---------------------------------------------------------------------------
# Caso límite de `security`/`qa` — valor 0 presente (no None) en un divisor
# ---------------------------------------------------------------------------


async def test_valor_cero_en_divisor_no_lanza_zerodivisionerror(empresa_completa, empresa_asset_light, etf_profile):
    completa_con_ceros = {
        **empresa_completa,
        "balance": [
            {**empresa_completa["balance"][0], "totalLiabilities": 0, "totalCurrentLiabilities": 0},
            empresa_completa["balance"][1],
        ],
    }
    clients = _make_clients(completa_con_ceros, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)  # no debe lanzar

    update.message.reply_text.assert_awaited_once()
    (mensaje,), _ = update.message.reply_text.call_args
    assert "no calculable" in mensaje  # Altman Z con pasivos totales en 0


# ---------------------------------------------------------------------------
# Rate limiter compartido (hallazgo 2 de `security`)
# ---------------------------------------------------------------------------


async def test_rate_limiter_bloqueado_no_llama_fmp(empresa_completa, empresa_asset_light, etf_profile):
    request_log = []
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile, request_log)
    callback = _handler_callback(clients, FakeRateLimiter(allow_value=False))

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(query_handler.RATE_LIMITED_MSG)
    assert request_log == []


async def test_rate_limiter_key_identica_a_query_handler(empresa_completa, empresa_asset_light, etf_profile):
    """Hallazgo 2 de `security` (el más importante): la clave debe ser
    EXACTAMENTE `str(update.effective_chat.id)`, sin prefijo ni
    transformación -- idéntica a `query_handler.py`."""
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    limiter = FakeRateLimiter()
    callback = _handler_callback(clients, limiter)

    update, context = _fake_avanzado_update(["MFG"], chat_id=999888)
    await callback(update, context)

    assert limiter.calls_with_key == ["999888"]
    assert isinstance(limiter.calls_with_key[0], str)


# ---------------------------------------------------------------------------
# Errores de FMP -> mensaje sanitizado, nunca traceback crudo
# ---------------------------------------------------------------------------


def _error_clients(status=None, timeout=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if timeout:
            raise httpx.TimeoutException("timed out", request=request)
        return httpx.Response(status, json={"error": "boom"})

    fmp_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    return query_handler.Clients(
        fmp_http=fmp_http,
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )


@pytest.mark.parametrize(
    "status,timeout",
    [(429, False), (402, False), (401, False), (403, False), (None, True), (500, False)],
    ids=["429", "402", "401", "403", "timeout", "generico_500"],
)
async def test_fmp_error_responde_mensaje_sanitizado(status, timeout):
    clients = _error_clients(status=status, timeout=timeout)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once()
    (mensaje,), _ = update.message.reply_text.call_args
    assert "Traceback" not in mensaje
    assert "test-key" not in mensaje
    assert "financialmodelingprep.com" not in mensaje


# ---------------------------------------------------------------------------
# Hallazgo 3 de `security` — except Exception genérico, nunca silencio
# ---------------------------------------------------------------------------


async def test_excepcion_no_fmp_error_responde_generic_error_msg(
    empresa_completa, empresa_asset_light, etf_profile, monkeypatch, caplog
):
    def raise_zero_division(**kwargs):
        raise ZeroDivisionError("boom inesperado")

    monkeypatch.setattr(advanced_command, "calculate_altman_z", raise_zero_division)

    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    with caplog.at_level(logging.ERROR):
        await callback(update, context)  # no debe propagar la excepción

    update.message.reply_text.assert_awaited_once_with(query_handler.GENERIC_ERROR_MSG)


# ---------------------------------------------------------------------------
# Hallazgo 4 de `security` — sin parse_mode="Markdown"
# ---------------------------------------------------------------------------


async def test_mensaje_final_se_envia_sin_parse_mode_markdown(empresa_completa, empresa_asset_light, etf_profile):
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    for call in update.message.reply_text.call_args_list:
        assert "parse_mode" not in call.kwargs


# ---------------------------------------------------------------------------
# Hallazgo 5 de `security` — truncar argumento inválido antes de loguear
# ---------------------------------------------------------------------------


async def test_argumento_larguisimo_se_trunca_antes_de_loguear(
    empresa_completa, empresa_asset_light, etf_profile, caplog
):
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    argumento_enorme = "X" * 5000
    update, context = _fake_avanzado_update([argumento_enorme])
    with caplog.at_level(logging.INFO):
        await callback(update, context)

    update.message.reply_text.assert_awaited_once_with(advanced_command.USAGE_MSG)
    for record in caplog.records:
        assert len(record.getMessage()) < 200  # muy por debajo de 5000 caracteres


def test_ticker_invalido_se_loguea_saneado(caplog):
    """El ticker inválido se sanea (`sanitize_for_log`) antes de loguearse
    -- sin saltos de línea que puedan falsificar líneas de log."""
    import asyncio

    clients = _error_clients(status=500)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["AAPL\nFAKE LOG LINE"])
    with caplog.at_level(logging.INFO):
        asyncio.run(callback(update, context))

    for record in caplog.records:
        assert "\n" not in record.getMessage()


# ---------------------------------------------------------------------------
# SDD_explicaciones_interactivas_ollama.md — línea de transparencia fija +
# botones de explicación en /avanzado (grupo J, casos 42-44 de QA).
# ---------------------------------------------------------------------------


def _enabled_ollama_config():
    from investbot import ai_rewrite

    return ai_rewrite.OllamaConfig(
        enabled=True, base_url="http://100.101.102.103:11434",
        model="qwen2.5:7b-instruct", timeout_seconds=8.0,
    )


async def test_transparencia_fija_con_botones_habilitados(empresa_completa, empresa_asset_light, etf_profile):
    """J.42a: con `clients.ollama_config.enabled=True`, el mensaje empieza
    con `TRANSPARENCY_FIXED_WITH_BUTTONS`."""
    clients = _make_clients(
        empresa_completa, empresa_asset_light, etf_profile, ollama_config=_enabled_ollama_config()
    )
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    (mensaje,), _ = update.message.reply_text.call_args_list[0]
    assert mensaje.startswith(advanced_command.TRANSPARENCY_FIXED_WITH_BUTTONS)
    from investbot import ai_rewrite
    assert ai_rewrite.TRANSPARENCY_USED not in mensaje
    assert ai_rewrite.TRANSPARENCY_NOT_USED not in mensaje


async def test_transparencia_fija_sin_botones_deshabilitado(empresa_completa, empresa_asset_light, etf_profile):
    """J.42b: sin la feature habilitada (default `ollama_config=None`),
    `TRANSPARENCY_FIXED_NO_BUTTONS`."""
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    (mensaje,), _ = update.message.reply_text.call_args_list[0]
    assert mensaje.startswith(advanced_command.TRANSPARENCY_FIXED_NO_BUTTONS)


async def test_mensaje_corto_con_ollama_habilitado_omite_desglose_completo(
    empresa_completa, empresa_asset_light, etf_profile
):
    """SDD_menu_por_capas_explicaciones.md, Decisión de diseño #9 — con
    Ollama habilitado, el primer (único) mensaje de `/avanzado` contiene
    SOLO título + síntesis de 1-2 líneas + invitación, nunca el desglose
    completo de los 5 modelos ni "Fuente de los datos"."""
    clients = _make_clients(
        empresa_completa, empresa_asset_light, etf_profile, ollama_config=_enabled_ollama_config()
    )
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    (mensaje,), _ = update.message.reply_text.call_args_list[0]
    assert "👇 Elegí qué modelo querés ver en detalle." in mensaje
    assert "Fuente de los datos" not in mensaje
    assert "Factores: Value [" not in mensaje
    assert "Piotroski F-Score: " not in mensaje or "evaluables (" not in mensaje


async def test_mensaje_completo_con_ollama_deshabilitado_sin_cambios(
    empresa_completa, empresa_asset_light, etf_profile
):
    """Regla de no-regresión D3 — sin Ollama habilitado, el desglose
    completo de siempre, byte-idéntico al comportamiento pre-spec."""
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    (mensaje,), _ = update.message.reply_text.call_args_list[0]
    assert "Fuente de los datos" in mensaje
    assert "Factores: Value [" in mensaje
    assert "👇 Elegí qué modelo querés ver en detalle." not in mensaje


async def test_explanation_context_avanzado_contiene_los_mismos_resultados_no_recalculados(
    empresa_completa, empresa_asset_light, etf_profile
):
    """J.43: `ExplanationContext(kind="avanzado", ...)` guardado contiene
    los mismos `altman`/`altman_pp`/`piotroski`/`beneish`/`magic`/`factors`/
    `asset_light` que ya se usaron para el texto del mensaje -- no
    recalculados (se verifica que el store recibió exactamente 1 `put`, con
    los campos poblados, para un ticker asset-light que sí calcula Z'')."""
    from investbot import ai_explain

    clients = _make_clients(
        empresa_completa, empresa_asset_light, etf_profile, ollama_config=_enabled_ollama_config()
    )
    store = ai_explain.ExplanationContextStore()
    callback = _handler_callback(clients, FakeRateLimiter(), store)

    update, context = _fake_avanzado_update(["TCH"])
    await callback(update, context)

    assert len(store._entries) == 1
    ((context_id, entry),) = store._entries.items()
    ctx = entry.context
    assert ctx.kind == "avanzado"
    assert ctx.ticker == "TCH"
    assert ctx.asset_light is True
    assert ctx.altman is not None
    assert ctx.altman_pp is not None  # asset-light -> Z'' calculado
    assert ctx.piotroski is not None
    assert ctx.beneish is not None
    assert ctx.magic is not None
    assert ctx.factors is not None

    # SDD_menu_por_capas_explicaciones.md, Decisión de diseño #9 — con
    # Ollama habilitado el mensaje base queda corto (el desglose de Z'' se
    # movió detrás del botón `azp`); el dato en sí sigue disponible, sin
    # recalcular, en el `ExplanationContext` guardado (ya verificado arriba).
    (mensaje,), _ = update.message.reply_text.call_args_list[0]
    assert "👇 Elegí qué modelo querés ver en detalle." in mensaje
    assert f"[Z'':" not in mensaje


async def test_reply_markup_con_5_botones_solo_en_ultimo_chunk(
    empresa_completa, empresa_asset_light, etf_profile
):
    """J.44: con botones habilitados, el teclado (5 botones) se adjunta
    únicamente al último chunk entregado -- acá `/avanzado` nunca parte en
    2+ en la práctica, así que el único chunk es también el último."""
    clients = _make_clients(
        empresa_completa, empresa_asset_light, etf_profile, ollama_config=_enabled_ollama_config()
    )
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.call_args
    keyboard = kwargs["reply_markup"]
    # Nivel 1 de /avanzado: mod/alt/pio/ben/mag/aqr (6 botones).
    assert sum(len(fila) for fila in keyboard.inline_keyboard) == 6


async def test_sin_botones_habilitados_ningun_reply_markup(empresa_completa, empresa_asset_light, etf_profile):
    """Regresión: con la feature deshabilitada, ningún chunk lleva
    `reply_markup` -- comportamiento idéntico al pre-spec salvo la primera
    línea de transparencia fija."""
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())

    update, context = _fake_avanzado_update(["MFG"])
    await callback(update, context)

    for call in update.message.reply_text.call_args_list:
        assert "reply_markup" not in call.kwargs


# ---------------------------------------------------------------------------
# Hallazgo 1 de `security` — gate de chat_id no autorizado corta antes de
# /avanzado (estructural: TypeHandler en group=-1 cubre TODOS los updates,
# incluyendo los de cualquier CommandHandler registrado en group=0).
# ---------------------------------------------------------------------------


async def test_avanzado_desde_chat_id_no_autorizado_no_se_invoca():
    gate = security.build_chat_id_gate(frozenset({ALLOWED_CHAT_ID}))
    update = SimpleNamespace()
    update.effective_chat = SimpleNamespace(id=999999, type="private")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


def test_bot_registra_avanzado_fuera_del_gate_group(tmp_path):
    """`/avanzado` se registra en `group=0` (default), DESPUÉS del gate
    (`group=-1`) -- nunca se ejecuta sin pasar antes por el gate."""
    from investbot import bot

    db_path = str(tmp_path / "bot_avanzado_test.db")
    application = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({ALLOWED_CHAT_ID}),
        db_path=db_path,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    comandos_avanzado = [
        h
        for h in application.handlers.get(0, [])
        if getattr(h, "commands", None) and "avanzado" in h.commands
    ]
    assert len(comandos_avanzado) == 1
    assert -1 in application.handlers  # el gate sigue en su propio grupo, antes


# ---------------------------------------------------------------------------
# Testabilidad -- efecto secundario de Telegram siempre mockeado (ya cumplido
# en todos los tests de arriba, vía update.message.reply_text = AsyncMock()).
# ---------------------------------------------------------------------------


async def test_reply_text_es_siempre_un_mock_nunca_red_real(empresa_completa, empresa_asset_light, etf_profile):
    clients = _make_clients(empresa_completa, empresa_asset_light, etf_profile)
    callback = _handler_callback(clients, FakeRateLimiter())
    update, context = _fake_avanzado_update(["MFG"])
    assert isinstance(update.message.reply_text, AsyncMock)
    await callback(update, context)
