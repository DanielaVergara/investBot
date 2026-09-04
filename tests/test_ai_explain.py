"""Tests de `ai_explain.py`/`ai_explain_content.py` — menú por capas de
explicaciones + fórmula/fuente determinística,
`SDD_menu_por_capas_explicaciones.md`.

Sin red real: mismo patrón `httpx.MockTransport` que `tests/test_ai_rewrite.py`.
Muchos tests de mecanismo puro (store TTL, guard de integridad, brevedad,
`_fetch_explanation`) se heredan sin cambios de la spec anterior — el
mecanismo no cambió, solo la superficie de contenido (27 `question_code`
contra 8, 3 formas de `callback_data` contra 1).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investbot import ai_explain, ai_explain_content, ai_rewrite
from investbot.query_handler import TELEGRAM_MESSAGE_LIMIT
from investbot.summary import DISCLAIMER_NO_ASESORAMIENTO


# ---------------------------------------------------------------------------
# Helpers / fixtures reutilizables
# ---------------------------------------------------------------------------


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _CountingClient:
    """Espía que cuenta requests — usado para verificar "0 llamadas a
    Ollama" sin depender de que la respuesta simulada sea válida."""

    def __init__(self, response: httpx.Response | None = None):
        self.call_count = 0
        self._response = response or httpx.Response(
            200, json={"response": json.dumps({"respuesta": "no debería llegar acá"})}
        )

    async def post(self, url, *, json=None, timeout=None):
        self.call_count += 1
        return self._response


def _enabled_config(**overrides) -> ai_rewrite.OllamaConfig:
    defaults = dict(
        enabled=True,
        base_url="http://100.101.102.103:11434",
        model="qwen2.5:7b-instruct",
        timeout_seconds=8.0,
    )
    defaults.update(overrides)
    return ai_rewrite.OllamaConfig(**defaults)


def _disabled_config() -> ai_rewrite.OllamaConfig:
    return ai_rewrite.OllamaConfig(
        enabled=False, base_url="", model="qwen2.5:7b-instruct", timeout_seconds=8.0
    )


def _ok_handler(respuesta_text: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": json.dumps({"respuesta": respuesta_text})})

    return handler


def _make_clients(http_client=None, ollama_config=None):
    return SimpleNamespace(ollama_http=http_client, ollama_config=ollama_config)


_DEFAULT_CHAT_ID = 777


def _texto_libre_context(**overrides) -> ai_explain.ExplanationContext:
    defaults = dict(
        chat_id=_DEFAULT_CHAT_ID,
        kind="texto_libre",
        ticker="ADBE",
        company_name="Adobe Inc.",
        escenario_elegido="conservador",
        precio_actual=550.0,
        scenarios={
            "pesimista": {
                "valor_justo_multiplos": 460.0, "valor_justo_graham": 440.0,
                "valor_justo_dcf": 470.0, "valor_justo_total": 456.7,
                "graham_g_aplicado": 0.06,
                "dcf_wacc": 0.10, "dcf_g_fcf": 0.053, "dcf_fcf_base": 100.0,
                "dcf_valor_presente_flujos": 400.0, "dcf_valor_terminal_descontado": 900.0,
                "dcf_equity_value": 1300.0,
            },
            "conservador": {
                "valor_justo_multiplos": 500.0, "valor_justo_graham": 480.0,
                "valor_justo_dcf": 510.0, "valor_justo_total": 496.0,
                "graham_g_aplicado": 0.094,
                "dcf_wacc": 0.091, "dcf_g_fcf": 0.083, "dcf_fcf_base": 109.0,
                "dcf_valor_presente_flujos": 612.0, "dcf_valor_terminal_descontado": 2100.0,
                "dcf_equity_value": 2712.0,
            },
            "optimista": {
                "valor_justo_multiplos": 540.0, "valor_justo_graham": 520.0,
                "valor_justo_dcf": 550.0, "valor_justo_total": 536.7,
                "graham_g_aplicado": 0.124,
                "dcf_wacc": 0.081, "dcf_g_fcf": 0.113, "dcf_fcf_base": 118.0,
                "dcf_valor_presente_flujos": 800.0, "dcf_valor_terminal_descontado": 3200.0,
                "dcf_equity_value": 4000.0,
            },
        },
        pillars={
            "ingresos_crecientes": True,
            "utilidades_crecientes": True,
            "deuda_controlada": True,
            "precio_razonable": False,
        },
        veredicto_barata=True,
        ratios={
            "ratio_liquidez": 1.8,
            "liquidez_sin_pasivos_circulantes": False,
            "margen_bruto": 0.65,
            "per": 22.5,
            "per_no_aplicable": False,
            "ps": 6.2,
        },
        risk_fit={"encaja": True, "perfil": "moderado", "beta": 1.15, "etiqueta_activo": "acción de riesgo moderado"},
        momentum={
            "pct_vs_year_high": -8.2, "pct_vs_year_low": 25.0,
            "pct_vs_avg_50": 3.1, "pct_vs_avg_200": 9.4, "etiqueta": "impulso_positivo",
        },
        peer_comparison={
            "per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
            "per_maximo_peers": 30.0, "peers_usados": ["MSFT", "CRM"],
            "posicion": "en_linea", "motivo_no_comparable": None,
        },
        extras={
            "roe": 0.28, "debt_to_equity": 0.4, "net_debt_to_ebitda": 0.9,
            "dividend_yield": 0.0, "payout_ratio": None,
        },
        vix={"valor": 16.2, "disponible": True},
        corporate_events=[
            {"filing_date": "2026-08-01", "labels": ["Fusión/adquisición"], "filing_url": "https://sec.gov/x"}
        ],
        treasury_source="FRED",
        balance_sheet_fuente="trimestral",
        income_statement_fuente="trimestral",
        cash_flow_fuente="trimestral",
        peers_note="nota de peers de prueba",
        # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- 15 campos
        # nuevos para el bloque "🧮 Cuenta" de "paso a paso".
        eps_ttm=8.2,
        y_value=0.042,
        current_assets=100.0,
        current_liabilities=50.0,
        revenue=1000.0,
        cost_of_revenue=400.0,
        market_cap=2_000_000.0,
        revenue_reciente=1000.0,
        revenue_antiguo=800.0,
        net_income_reciente=200.0,
        net_income_antiguo=150.0,
        year_high=600.0,
        year_low=400.0,
        price_avg_50=540.0,
        price_avg_200=520.0,
    )
    defaults.update(overrides)
    return ai_explain.ExplanationContext(**defaults)


def _avanzado_context(**overrides) -> ai_explain.ExplanationContext:
    defaults = dict(
        chat_id=_DEFAULT_CHAT_ID,
        kind="avanzado",
        ticker="MFG",
        company_name="Manufacturing Co Inc.",
        sector="Technology",
        industry="Software - Infrastructure",
        asset_light=True,
        altman={
            "disponible": True, "z": 3.5, "zona": "segura", "campos_faltantes": [],
            "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": 0.87,
        },
        altman_pp={
            "disponible": True, "z": 5.2, "zona": "segura", "campos_faltantes": [],
            "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": None,
        },
        piotroski={
            "puntaje": 7,
            "criterios_evaluables": 9,
            "criterios_totales": 9,
            "criterios": [
                {"nombre": "roa_positivo", "cumplido": True, "valores": {"net_income_t": 118.0}},
                {"nombre": "cfo_positivo", "cumplido": True, "valores": {"cfo_t": 118.0}},
                {
                    "nombre": "roa_creciente", "cumplido": True,
                    "valores": {"roa_t": 0.18, "roa_t1": 0.15},
                },
                {
                    "nombre": "cfo_mayor_utilidad", "cumplido": False,
                    "valores": {"cfo_t": 118.0, "net_income_t": 97.0},
                },
                {
                    "nombre": "apalancamiento_decreciente", "cumplido": True,
                    "valores": {"apalancamiento_t": 0.2, "apalancamiento_t1": 0.3},
                },
                {
                    "nombre": "liquidez_creciente", "cumplido": False,
                    "valores": {"liquidez_t": 1.1, "liquidez_t1": 1.3},
                },
                {
                    "nombre": "sin_dilucion", "cumplido": True,
                    "valores": {"shares_t": 100.0, "shares_t1": 102.0},
                },
                {
                    "nombre": "margen_bruto_creciente", "cumplido": True,
                    "valores": {"margen_t": 0.43, "margen_t1": 0.40},
                },
                {
                    "nombre": "rotacion_activos_creciente", "cumplido": False,
                    "valores": {"rotacion_t": 0.9, "rotacion_t1": 0.95},
                },
            ],
        },
        beneish={"disponible": False, "motivo": "no_calculable_con_datos_disponibles"},
        magic={
            "disponible": True, "roic": 0.2, "earnings_yield": 0.08, "campos_faltantes": [],
            "ebit": 114_000.0, "capital_invertido": 570_000.0, "ev": 1_425_000.0,
            "market_cap": 1_400_000.0, "total_debt": 100_000.0, "cash": 75_000.0,
            "current_assets": 650_000.0, "current_liabilities": 150_000.0, "ppe_net": 70_000.0,
        },
        factors={"value": "alto", "quality": "alto", "momentum": "medio", "low_vol": "bajo"},
        roe=0.22,
        gross_margin=0.55,
        beta=1.05,
    )
    defaults.update(overrides)
    return ai_explain.ExplanationContext(**defaults)


class FakeRateLimiter:
    def __init__(self, allow_value=True):
        self.allow_value = allow_value
        self.calls_with_key: list[str] = []

    def allow(self, key: str) -> bool:
        self.calls_with_key.append(key)
        return self.allow_value


_PENSANDO_MESSAGE_ID = 4242


def _fake_callback_update(data: str, chat_id: int = _DEFAULT_CHAT_ID):
    query = SimpleNamespace(data=data, answer=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    context = SimpleNamespace(
        bot=SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=_PENSANDO_MESSAGE_ID)
            ),
            edit_message_text=AsyncMock(),
        )
    )
    return update, query, context


def _build_callback(clients, rate_limiter, store):
    return ai_explain.build_explain_handler(clients, rate_limiter, store).callback


def _fetch_kwargs(http_client=None, config=None, question_code="vf", pregunta_fija="pregunta fija de prueba", datos=None, tokens=None):
    return dict(
        clients=_make_clients(http_client=http_client),
        config=config or _enabled_config(),
        question_code=question_code,
        pregunta_fija=pregunta_fija,
        datos_del_contexto=datos if datos is not None else {"precio_actual": 500.0},
        datos_tokens=tokens if tokens is not None else set(),
    )


# ---------------------------------------------------------------------------
# A. ExplanationContextStore -- mecánica del store (sin cambios de mecanismo)
# ---------------------------------------------------------------------------


def test_store_put_devuelve_context_ids_distintos_formato_valido():
    store = ai_explain.ExplanationContextStore()
    id1 = store.put(_texto_libre_context())
    id2 = store.put(_texto_libre_context())
    assert id1 != id2
    for cid in (id1, id2):
        assert len(cid) == 8
        assert all(c in "0123456789abcdef" for c in cid)


def test_store_get_id_inexistente_devuelve_none():
    store = ai_explain.ExplanationContextStore()
    assert store.get("deadbeef") is None


def test_store_get_ttl_vencido_devuelve_none_via_reloj_inyectado():
    clock = {"now": 0.0}
    store = ai_explain.ExplanationContextStore(ttl_seconds=10.0, now_fn=lambda: clock["now"])
    cid = store.put(_texto_libre_context())
    assert store.get(cid) is not None
    clock["now"] = 11.0
    assert store.get(cid) is None


def test_store_evict_entrada_mas_vieja_con_max_entries_chico():
    store = ai_explain.ExplanationContextStore(max_entries=3)
    ids = [store.put(_texto_libre_context(ticker=f"T{i}")) for i in range(4)]
    assert store.get(ids[0]) is None  # evicted
    for cid in ids[1:]:
        assert store.get(cid) is not None


def test_store_put_purga_entradas_vencidas_via_evict_expired():
    clock = {"now": 0.0}
    store = ai_explain.ExplanationContextStore(ttl_seconds=5.0, now_fn=lambda: clock["now"])
    store.put(_texto_libre_context())
    clock["now"] = 100.0
    store.put(_texto_libre_context())
    assert len(store._entries) == 1


def test_store_put_reintenta_context_id_en_colision(monkeypatch):
    store = ai_explain.ExplanationContextStore()
    existing_id = store.put(_texto_libre_context())

    class _FakeUUID:
        def __init__(self, hexvalue):
            self.hex = hexvalue

    valores = iter([existing_id + "x" * (32 - len(existing_id)), "f" * 32])
    monkeypatch.setattr(ai_explain.uuid, "uuid4", lambda: _FakeUUID(next(valores)))

    new_id = store.put(_texto_libre_context())
    assert new_id == "f" * 8
    assert new_id != existing_id


def test_store_get_devuelve_mismos_valores_que_put_ambos_kind():
    store = ai_explain.ExplanationContextStore()
    ctx_tl = _texto_libre_context()
    ctx_av = _avanzado_context()
    id_tl = store.put(ctx_tl)
    id_av = store.put(ctx_av)
    assert store.get(id_tl) == ctx_tl
    assert store.get(id_av) == ctx_av


# ---------------------------------------------------------------------------
# B. Teclados (Nivel 1 / Nivel 2) -- Decisión de diseño #1/#2/#3
# ---------------------------------------------------------------------------


def test_build_keyboard_texto_libre_nivel1_5_botones_ver_val_cal_rie_inf():
    markup = ai_explain.build_keyboard("texto_libre", "a1b2c3d4")
    filas = markup.inline_keyboard
    assert len(filas) == 5  # 1 botón por fila
    codes = [fila[0].callback_data for fila in filas]
    assert codes == [
        "xp:a1b2c3d4:ver",
        "xp:a1b2c3d4:c:val",
        "xp:a1b2c3d4:c:cal",
        "xp:a1b2c3d4:c:rie",
        "xp:a1b2c3d4:inf",
    ]


def test_build_keyboard_avanzado_nivel1_6_botones_mod_alt_pio_ben_mag_aqr():
    markup = ai_explain.build_keyboard("avanzado", "a1b2c3d4")
    filas = markup.inline_keyboard
    assert len(filas) == 6
    codes = [fila[0].callback_data for fila in filas]
    assert codes == [
        "xp:a1b2c3d4:mod",
        "xp:a1b2c3d4:c:alt",
        "xp:a1b2c3d4:c:pio",
        "xp:a1b2c3d4:ben",
        "xp:a1b2c3d4:c:mag",
        "xp:a1b2c3d4:c:aqr",
    ]


@pytest.mark.parametrize("kind", ["texto_libre", "avanzado"])
def test_build_keyboard_nivel1_callback_data_bajo_64_bytes(kind):
    markup = ai_explain.build_keyboard(kind, "deadbeef")
    for fila in markup.inline_keyboard:
        for boton in fila:
            assert len(boton.callback_data.encode("utf-8")) <= 64


def test_build_category_keyboard_val_incluye_menu_al_final():
    ctx = _texto_libre_context()
    markup = ai_explain.build_category_keyboard("texto_libre", "a1b2c3d4", "val", ctx)
    filas = markup.inline_keyboard
    codes = [fila[0].callback_data for fila in filas]
    assert codes == [
        "xp:a1b2c3d4:vf", "xp:a1b2c3d4:gra", "xp:a1b2c3d4:dcf",
        "xp:a1b2c3d4:mul", "xp:a1b2c3d4:rat", "xp:a1b2c3d4:m",
    ]
    assert filas[-1][0].text == "🔙 Menú"


def test_build_category_keyboard_desconocida_devuelve_none():
    assert ai_explain.build_category_keyboard("texto_libre", "a1b2c3d4", "zzz", None) is None


def test_build_category_keyboard_alt_sin_altman_pp_solo_alz():
    ctx = _avanzado_context(altman_pp=None)
    markup = ai_explain.build_category_keyboard("avanzado", "a1b2c3d4", "alt", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert codes == ["alz", "m"]


def test_build_category_keyboard_alt_con_altman_pp_incluye_azp():
    ctx = _avanzado_context()  # altman_pp is not None por default
    markup = ai_explain.build_category_keyboard("avanzado", "a1b2c3d4", "alt", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert codes == ["alz", "azp", "m"]


def test_build_category_keyboard_rie_sin_eventos_omite_evt():
    ctx = _texto_libre_context(corporate_events=[])
    markup = ai_explain.build_category_keyboard("texto_libre", "a1b2c3d4", "rie", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert "evt" not in codes
    assert codes == ["rsk", "mom", "cmp", "m"]


def test_build_category_keyboard_rie_con_eventos_incluye_evt():
    ctx = _texto_libre_context()  # corporate_events no vacío por default
    markup = ai_explain.build_category_keyboard("texto_libre", "a1b2c3d4", "rie", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert "evt" in codes


# ---------------------------------------------------------------------------
# C. Dispatch de las 3 formas de callback_data
# ---------------------------------------------------------------------------


async def test_dispatch_menu_manda_mensaje_nuevo_con_nivel1():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:m")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.call_args
    assert kwargs["chat_id"] == _DEFAULT_CHAT_ID
    assert kwargs["text"] == "👇 Elegí qué querés que te explique."
    assert len(kwargs["reply_markup"].inline_keyboard) == 5


async def test_dispatch_menu_avanzado_prompt_distinto():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_avanzado_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:m")
    await callback(update, context)

    _, kwargs = context.bot.send_message.call_args
    assert kwargs["text"] == "👇 Elegí qué modelo querés ver en detalle."


async def test_dispatch_categoria_valida_manda_mensaje_nuevo_con_nivel2():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:c:val")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.call_args
    assert "Valoración" in kwargs["text"]
    codes = [fila[0].callback_data.split(":")[-1] for fila in kwargs["reply_markup"].inline_keyboard]
    assert codes[-1] == "m"


async def test_dispatch_categoria_inexistente_en_ambas_tablas_es_invalido():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:c:zzz")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_INVALID_MSG
    )


@pytest.mark.parametrize(
    "context_kind,category_code",
    [("avanzado", "val"), ("avanzado", "cal"), ("avanzado", "rie"),
     ("texto_libre", "alt"), ("texto_libre", "pio"), ("texto_libre", "mag"), ("texto_libre", "aqr")],
)
async def test_dispatch_categoria_de_kind_equivocado_responde_vencido(context_kind, category_code):
    """Mejora recomendada #1 de `security` — categoría sintácticamente
    válida y conocida, pero de la tabla del OTRO `kind` -> mismo camino que
    un `context_id` vencido, nunca `EXPLAIN_INVALID_MSG` ni una excepción."""
    store = ai_explain.ExplanationContextStore()
    ctx = _avanzado_context() if context_kind == "avanzado" else _texto_libre_context()
    cid = store.put(ctx)
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:c:{category_code}")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_EXPIRED_MSG
    )


async def test_dispatch_menu_o_categoria_con_context_id_vencido():
    store = ai_explain.ExplanationContextStore()
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    for data in ("xp:deadbeef:m", "xp:deadbeef:c:val"):
        update, query, context = _fake_callback_update(data)
        await callback(update, context)
        context.bot.send_message.assert_awaited_once_with(
            chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_EXPIRED_MSG
        )


@pytest.mark.parametrize(
    "data",
    [
        "yz:a1b2c3d4:vf",  # prefijo distinto
        "xp:zzzzzzzz:vf",  # hex inválido
        "xp:a1b2c3d:vf",  # 7 hex chars
        "xp:a1b2c3d4:v",  # code de 1 char
        "xp:a1b2c3d4:vfvfvf",  # code de 6 chars
        "xp:a1b2c3d4",  # falta el question_code
        "xp:a1b2c3d4:c:",  # categoría vacía
        "xp:a1b2c3d4:c:VAL",  # categoría en mayúsculas
        "xp:a1b2c3d4:c:v:al",  # ":" extra
    ],
)
async def test_handler_callback_data_malformado_no_crashea_mensaje_corto(data, caplog):
    store = ai_explain.ExplanationContextStore()
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(data)
    with caplog.at_level(logging.WARNING):
        await callback(update, context)

    query.answer.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_INVALID_MSG
    )
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_handler_question_code_regex_valido_pero_desconocido():
    store = ai_explain.ExplanationContextStore()
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update("xp:a1b2c3d4:zzz")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_INVALID_MSG
    )


@pytest.mark.parametrize(
    "context_kind,question_code",
    [("avanzado", "vf"), ("texto_libre", "mod")],
)
async def test_handler_question_code_incompatible_con_kind_expired(context_kind, question_code):
    store = ai_explain.ExplanationContextStore()
    ctx = _avanzado_context() if context_kind == "avanzado" else _texto_libre_context()
    cid = store.put(ctx)
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:{question_code}")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_EXPIRED_MSG
    )


# ---------------------------------------------------------------------------
# D. `_build_explain_payload` -- superficie mínima
# ---------------------------------------------------------------------------


def test_payload_pil_contiene_pillars_y_total_pilares():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "pil")
    assert payload["modelo"] == ai_explain._MODELO_PIL
    assert payload["pillars"] == ctx.pillars
    assert payload["total_pilares"] == len(ctx.pillars)
    # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- campos nuevos
    # para la cuenta resuelta.
    assert payload["revenue_reciente"] == ctx.revenue_reciente
    assert payload["revenue_antiguo"] == ctx.revenue_antiguo
    assert payload["net_income_reciente"] == ctx.net_income_reciente
    assert payload["net_income_antiguo"] == ctx.net_income_antiguo


def test_payload_ver_contiene_veredicto_y_valor_justo_total():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "ver")
    assert payload["veredicto_barata"] == ctx.veredicto_barata
    assert payload["valor_justo_total"] == 496.0


def test_payload_gra_dcf_mul_contienen_los_3_escenarios_del_modelo():
    ctx = _texto_libre_context()
    for code, campo in (("gra", "valor_justo_graham"), ("dcf", "valor_justo_dcf"), ("mul", "valor_justo_multiplos")):
        payload = ai_explain._build_explain_payload(ctx, code)
        assert payload["pesimista"] == ctx.scenarios["pesimista"][campo]
        assert payload["conservador"] == ctx.scenarios["conservador"][campo]
        assert payload["optimista"] == ctx.scenarios["optimista"][campo]
        assert "modelo" in payload


def test_payload_rat_solo_contiene_ratios_relevantes():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "rat")
    assert set(payload) == {
        "modelo", "ratio_liquidez", "liquidez_sin_pasivos_circulantes",
        "margen_bruto", "per", "per_no_aplicable", "ps",
        "current_assets", "current_liabilities", "revenue", "cost_of_revenue",
        "market_cap", "eps_ttm", "precio_actual",
    }


def test_payload_ren_solo_contiene_extras():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "ren")
    assert set(payload) == {"modelo", "roe", "debt_to_equity", "net_debt_to_ebitda", "dividend_yield", "payout_ratio"}


def test_payload_rsk_solo_contiene_risk_fit():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "rsk")
    assert set(payload) == {
        "modelo", "encaja", "perfil", "beta", "etiqueta_activo",
        "beta_umbral_bajo", "beta_umbral_alto",
    }
    assert payload["beta_umbral_bajo"] == ai_explain.risk_fit.BETA_UMBRAL_BAJO
    assert payload["beta_umbral_alto"] == ai_explain.risk_fit.BETA_UMBRAL_ALTO


def test_payload_mom_contiene_momentum_y_vix():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "mom")
    assert payload["etiqueta"] == "impulso_positivo"
    assert payload["vix_valor"] == 16.2
    assert payload["vix_disponible"] is True


def test_payload_cmp_solo_contiene_peer_comparison_relevante():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "cmp")
    assert set(payload) == {
        "modelo", "per_propio", "per_minimo_peers", "per_promedio_peers",
        "per_maximo_peers", "peers_usados", "posicion", "motivo_no_comparable",
        "eps_ttm", "precio_actual",
    }


def test_payload_mod_sector_valido_de_allowlist_aparece_tal_cual():
    ctx = _avanzado_context(sector="Technology")
    payload = ai_explain._build_explain_payload(ctx, "mod")
    assert payload["sector"] == "Technology"
    assert payload["asset_light"] is True


def test_payload_mod_sector_fuera_de_allowlist_se_trata_como_no_disponible():
    ctx = _avanzado_context(sector="algo <script>adversarial</script>")
    payload = ai_explain._build_explain_payload(ctx, "mod")
    assert payload["sector"] == "no disponible"


def test_payload_mod_incluye_total_modelos_fijo():
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, "mod")
    assert payload["total_modelos"] == 5


def test_payload_alz_solo_contiene_datos_de_altman():
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, "alz")
    assert set(payload) == {"modelo", "altman"}


def test_payload_azp_solo_contiene_datos_de_altman_pp():
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, "azp")
    assert set(payload) == {"modelo", "altman_pp"}


def test_payload_pir_pia_pie_solo_contienen_sus_criterios_propios():
    ctx = _avanzado_context()
    pir = ai_explain._build_explain_payload(ctx, "pir")
    assert {c["nombre"] for c in pir["criterios"]} == ai_explain._PIOTROSKI_RENTABILIDAD
    pia = ai_explain._build_explain_payload(ctx, "pia")
    assert {c["nombre"] for c in pia["criterios"]} == ai_explain._PIOTROSKI_APALANCAMIENTO
    pie = ai_explain._build_explain_payload(ctx, "pie")
    assert {c["nombre"] for c in pie["criterios"]} == ai_explain._PIOTROSKI_EFICIENCIA


def test_payload_ben_contiene_beneish_siempre_no_calculable():
    """D1 (no reabrir): Beneish siempre "no calculable"."""
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, "ben")
    assert payload["beneish"]["disponible"] is False


def test_payload_aqq_superficie_minima_dedicada():
    """Caso dedicado señalado explícitamente por `architect` en el Handoff a
    `security` (línea 388) — `aqq` combina más sub-métricas que el resto:
    SOLO quality/roe/gross_margin/piotroski_ratio, nunca el `ExplanationContext`
    completo ni ningún otro campo."""
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, "aqq")
    assert set(payload) == {
        "modelo", "quality", "roe", "gross_margin", "piotroski_ratio",
        "roe_umbral_alto", "roe_umbral_bajo",
        "gross_margin_umbral_alto", "gross_margin_umbral_bajo",
        "piotroski_ratio_umbral_alto", "piotroski_ratio_umbral_bajo",
    }
    assert payload["roe"] == ctx.roe
    assert payload["gross_margin"] == ctx.gross_margin
    assert payload["piotroski_ratio"] == pytest.approx(ctx.piotroski["puntaje"] / ctx.piotroski["criterios_evaluables"])


def test_payload_aqv_aqm_aql_superficie_minima():
    ctx = _avanzado_context()
    aqv = ai_explain._build_explain_payload(ctx, "aqv")
    assert set(aqv) == {"modelo", "value", "earnings_yield", "umbral_alto", "umbral_bajo"}
    # SDD_desglose_universal.md, Grupo F, Cambio 3 -- "aqm" gana 3 campos
    # nuevos (precio_actual/price_avg_50/price_avg_200), pero NO
    # year_high/year_low/pct_vs_* (superficie mínima, Decisión de diseño
    # #11 original, sin aflojar).
    aqm = ai_explain._build_explain_payload(ctx, "aqm")
    assert set(aqm) == {"modelo", "momentum", "precio_actual", "price_avg_50", "price_avg_200"}
    for campo_no_expuesto in ("year_high", "year_low", "pct_vs_year_high", "pct_vs_year_low"):
        assert campo_no_expuesto not in aqm
    aql = ai_explain._build_explain_payload(ctx, "aql")
    assert set(aql) == {"modelo", "low_vol", "beta", "beta_umbral_bajo", "beta_umbral_alto"}
    assert aql["beta"] == ctx.beta


def test_payload_mgr_mge_solo_contienen_su_metrica():
    ctx = _avanzado_context()
    mgr = ai_explain._build_explain_payload(ctx, "mgr")
    assert set(mgr) == {
        "modelo", "roic", "disponible", "ebit", "capital_invertido",
        # SDD_desglose_con_valores_reales.md, Decisión de diseño #1 -- 2
        # componentes de `capital_invertido` expuestos por separado.
        "current_assets", "current_liabilities", "ppe_net",
    }
    mge = ai_explain._build_explain_payload(ctx, "mge")
    assert set(mge) == {
        "modelo", "earnings_yield", "disponible", "ebit", "ev", "market_cap", "total_debt", "cash",
    }


@pytest.mark.parametrize(
    "question_code",
    [c for c, spec in ai_explain_content.QUESTIONS_TEXTO_LIBRE.items()
     if spec.variant != ai_explain_content.VARIANT_DETERMINISTICO],
)
def test_payload_texto_libre_toda_pregunta_ollama_no_lanza_excepcion(question_code):
    """`evt`/`inf` (deterministic) quedan afuera -- nunca pasan por
    `_build_explain_payload`, usan `_build_deterministic_content`."""
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    assert isinstance(payload, dict)


@pytest.mark.parametrize("question_code", list(ai_explain_content.QUESTIONS_AVANZADO))
def test_payload_avanzado_toda_pregunta_no_lanza_excepcion(question_code):
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    assert isinstance(payload, dict)


def test_evt_inf_no_pasan_por_build_explain_payload():
    """`evt`/`inf` -- Decisión de diseño #4: 100% determinístico, ni
    siquiera arman `datos_del_contexto` para Ollama."""
    for code in ("evt", "inf"):
        with pytest.raises(ValueError):
            ai_explain._build_explain_payload(_texto_libre_context(), code)


@pytest.mark.parametrize(
    "question_code",
    [c for c, spec in ai_explain_content.QUESTIONS_TEXTO_LIBRE.items()
     if spec.variant != ai_explain_content.VARIANT_DETERMINISTICO],
)
def test_payload_texto_libre_pregunta_ollama_incluye_modelo(question_code):
    """Regla 5 de `SYSTEM_PROMPT_EXPLAIN`/`SYSTEM_PROMPT_PASO_A_PASO`: toda
    pregunta que llama a Ollama (narrativa o paso a paso) trae la clave
    "modelo" (síntesis narrativas "ver" incluida)."""
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    assert "modelo" in payload


@pytest.mark.parametrize(
    "question_code",
    [c for c, spec in ai_explain_content.QUESTIONS_AVANZADO.items()
     if spec.variant != ai_explain_content.VARIANT_DETERMINISTICO and c != "mod"],
)
def test_payload_avanzado_pregunta_ollama_incluye_modelo(question_code):
    ctx = _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    assert "modelo" in payload


def test_build_explain_payload_question_code_desconocido_lanza_value_error():
    with pytest.raises(ValueError):
        ai_explain._build_explain_payload(_texto_libre_context(), "zzz")
    with pytest.raises(ValueError):
        ai_explain._build_explain_payload(_avanzado_context(), "zzz")


def test_industry_nunca_aparece_en_ningun_payload():
    """Hallazgo 1 BLOQUEANTE de `security` (spec anterior, sigue vigente):
    `industry` nunca viaja al payload de Ollama bajo ningún `question_code`."""
    ctx = _avanzado_context(industry="algo adversarial <script>")
    for question_code in ai_explain_content.QUESTIONS_AVANZADO:
        payload = ai_explain._build_explain_payload(ctx, question_code)
        assert "industry" not in payload
        assert "algo adversarial" not in json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# E. Guard `_no_new_protected_tokens` (sin cambios de mecanismo)
# ---------------------------------------------------------------------------


def test_guard_subconjunto_de_tokens_es_true():
    datos_tokens = {"15.0%", "20.0%", "✅"}
    assert ai_explain._no_new_protected_tokens(datos_tokens, "El dato es 15.0%.") is True


def test_guard_token_ausente_de_datos_es_false():
    datos_tokens = {"15.0%"}
    assert ai_explain._no_new_protected_tokens(datos_tokens, "El dato sube a 35.0%.") is False


def test_guard_caso_limite_suma_compuesta_35_ausente_rechazada():
    datos_tokens = {"15%", "20%"}
    assert ai_explain._no_new_protected_tokens(datos_tokens, "15% + 20% = 35%") is False


def test_guard_respuesta_vacia_es_true_caso_de_frontera():
    assert ai_explain._no_new_protected_tokens({"15.0%"}, "") is True
    assert ai_explain._no_new_protected_tokens(set(), "   ") is True


def test_guard_normaliza_simbolo_pesos_mismo_valor_pasa():
    assert ai_explain._no_new_protected_tokens({"405.63"}, "Vale $405.63.") is True


def test_guard_normaliza_separador_de_miles_mismo_valor_pasa():
    assert ai_explain._no_new_protected_tokens({"1234.56"}, "Vale $1,234.56.") is True


def test_guard_normaliza_coma_decimal_mismo_valor_pasa():
    """Caso real de producción 2026-09-04: Ollama escribió "$285,75" (coma
    como separador DECIMAL, no de miles) para un monto que en el dato real
    es 285.75 -- mismo valor, otro convenio numérico, no una alucinación."""
    assert ai_explain._no_new_protected_tokens({"285.75"}, "Vale $285,75.") is True


def test_guard_coma_de_miles_sigue_funcionando_no_se_rompe_por_el_fix_decimal():
    """La heurística de coma decimal (1-2 dígitos tras la coma) no debe
    reabrir el caso ya cubierto de separador de miles (3 dígitos tras la
    coma) -- "$1,234.56" sigue normalizando a "1234.56", no a "1.234.56"."""
    assert ai_explain._no_new_protected_tokens({"1234.56"}, "Vale $1,234.56.") is True


def test_guard_coma_decimal_numero_realmente_distinto_sigue_rechazado():
    assert ai_explain._no_new_protected_tokens({"285.75"}, "Vale $999,99.") is False


def test_guard_normaliza_signo_mas_mismo_valor_pasa():
    assert ai_explain._no_new_protected_tokens({"5.2%"}, "Subió +5.2%.") is True


def test_guard_adversarial_numero_realmente_distinto_sigue_rechazado():
    assert ai_explain._no_new_protected_tokens({"405.63"}, "Vale $999.99.") is False


def test_guard_normaliza_porcentaje_a_proporcion_decimal_mismo_valor_pasa():
    """Caso real de producción 2026-09-03: `net_debt_to_ebitda` viaja al
    payload de Ollama como proporción decimal cruda (`0.12`, no un
    porcentaje -- es un múltiplo "0.12x") y Ollama lo redactó como "una
    tasa cercana a 12%" -- mismo valor, otra representación (igual que el
    caso ya cubierto de $/separador de miles), no una alucinación."""
    assert ai_explain._no_new_protected_tokens({"0.12"}, "una tasa cercana a 12%") is True


def test_guard_normalizacion_de_porcentaje_no_afloja_numero_realmente_distinto():
    """La extensión de normalización no abre una puerta trasera -- un
    porcentaje que no corresponde a NINGÚN dato real (ni como proporción
    decimal ni como porcentaje textual) se sigue rechazando."""
    assert ai_explain._no_new_protected_tokens({"0.12"}, "una tasa cercana a 87%") is False


def test_normalize_numeric_token_no_toca_tokens_no_numericos():
    for token in ("✅", "❌", "SÍ", "NO", "ADBE"):
        assert ai_explain._normalize_numeric_token(token) == token


@pytest.mark.parametrize(
    "question_code,kind",
    [
        ("gra", "texto_libre"), ("dcf", "texto_libre"), ("mul", "texto_libre"),
        ("rat", "texto_libre"), ("ren", "texto_libre"), ("rsk", "texto_libre"),
        ("mom", "texto_libre"), ("cmp", "texto_libre"),
        ("alz", "avanzado"), ("azp", "avanzado"), ("pig", "avanzado"),
        ("pir", "avanzado"), ("pia", "avanzado"), ("pie", "avanzado"),
        ("mgr", "avanzado"), ("mge", "avanzado"), ("aqv", "avanzado"),
        ("aqq", "avanzado"), ("aqm", "avanzado"), ("aql", "avanzado"),
    ],
)
def test_guard_extendido_rechaza_alucinacion_en_los_20_codes_nuevos(question_code, kind):
    """Decisión de diseño #11 — los `question_code` nuevos que llaman a
    Ollama pasan por el mismo guard, mismo criterio de subconjunto: una
    respuesta simulada que alucina un número nuevo se rechaza."""
    ctx = _texto_libre_context() if kind == "texto_libre" else _avanzado_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    datos_tokens = {
        ai_explain._normalize_numeric_token(t)
        for t in ai_rewrite.protected_tokens(json.dumps(payload, ensure_ascii=False, default=str))
    }
    assert ai_explain._no_new_protected_tokens(datos_tokens, "Esto inventa 987654.0% de la nada") is False


# ---------------------------------------------------------------------------
# F. `_enforce_brevity` (sin cambios de mecanismo)
# ---------------------------------------------------------------------------


def test_enforce_brevity_texto_corto_se_devuelve_sin_modificar():
    texto = "Una explicación breve."
    assert ai_explain._enforce_brevity(texto) == texto


def test_enforce_brevity_texto_largo_con_punto_corta_en_ultimo_punto():
    relleno = "Dato. " * 200
    resultado = ai_explain._enforce_brevity(relleno)
    assert len(resultado) <= ai_explain._MAX_EXPLANATION_CHARS
    assert resultado.endswith(".")
    assert "…" not in resultado


def test_enforce_brevity_texto_largo_sin_punto_corte_duro_con_puntos_suspensivos():
    relleno = "X" * (ai_explain._MAX_EXPLANATION_CHARS + 50)
    resultado = ai_explain._enforce_brevity(relleno)
    assert resultado == relleno[: ai_explain._MAX_EXPLANATION_CHARS] + "…"


# ---------------------------------------------------------------------------
# G. Llamada a Ollama -- timeout y fallback (`_fetch_explanation`)
# ---------------------------------------------------------------------------


async def test_fetch_explanation_connect_error(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_fetch_explanation_timeout(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout", request=request)

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_fetch_explanation_http_no_2xx(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_fetch_explanation_cuerpo_no_json(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"no es json", headers={"content-type": "text/plain"})

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_fetch_explanation_json_sin_clave_respuesta(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": json.dumps({"otra_clave": "algo"})})

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_fetch_explanation_reintenta_una_vez_y_se_recupera(caplog):
    """Fix 2026-09-03: si la 1a respuesta de Ollama tiene estructura JSON
    inesperada pero la 2a (reintento) es válida, `_fetch_explanation`
    devuelve la explicación del 2o intento -- no levanta
    `_ExplainUnavailable` -- y loguea el reintento a INFO."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, json={"response": "esto no es json"})
        return httpx.Response(200, json={"response": json.dumps({"respuesta": "Explicación ok."})})

    with caplog.at_level(logging.INFO):
        resultado = await ai_explain._fetch_explanation(
            **_fetch_kwargs(http_client=_client_with_handler(handler))
        )

    assert call_count["n"] == 2
    assert resultado == "Explicación ok."
    assert any("reintentando" in r.message for r in caplog.records)


async def test_fetch_explanation_ambos_intentos_fallan_cae_a_unavailable(caplog):
    """Fix 2026-09-03: si AMBOS intentos (original + reintento) devuelven
    estructura JSON inesperada, se hacen exactamente 2 llamadas HTTP y el
    resultado final sigue siendo `_ExplainUnavailable`."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"response": "esto no es json"})

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                **_fetch_kwargs(http_client=_client_with_handler(handler))
            )

    assert call_count["n"] == 2


async def test_fetch_explanation_guard_falla_warning():
    client = _client_with_handler(_ok_handler("El dato es 999.0%, un número inventado."))
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(
            **_fetch_kwargs(http_client=client, tokens={"500.0"})
        )


async def test_fetch_explanation_num_predict_y_formato_correctos():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": json.dumps({"respuesta": "Corto."})})

    client = _client_with_handler(handler)
    await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client))

    assert captured["body"]["options"]["num_predict"] == 220
    assert captured["body"]["format"] == "json"
    assert captured["body"]["stream"] is False


# ---------------------------------------------------------------------------
# G bis. Eco del JSON de entrada -- incidente de producción 2026-09-03
# (captura de Daniela: `qwen2.5:3b-instruct` repitió `datos_del_contexto`
# pegado adelante de la explicación real, separado por " -- ").
# ---------------------------------------------------------------------------

# `datos_del_contexto` EXACTO de la evidencia real (pregunta "ver", texto
# libre -- ver `_payload_texto_libre`).
_DATOS_EVIDENCIA_REAL = {
    "modelo": "Graham (EPS) + DCF (flujo de caja descontado) + los 4 pilares de calidad fundamental",
    "veredicto_barata": True,
    "escenario_elegido": "conservador",
    "precio_actual": 286.635,
    "valor_justo_total": 403.6118412598229,
}


def _respuesta_con_eco(datos: dict, texto_real: str) -> str:
    """Reproduce el patrón exacto de la evidencia: el JSON de `datos`
    serializado igual que `_fetch_explanation` arma el prompt, pegado
    adelante de la explicación real, separado por " -- "."""
    return f"{json.dumps(datos, ensure_ascii=False)} -- {texto_real}"


def _tokens_de(datos: dict) -> set[str]:
    """Mismo cálculo que `_dispatch_leaf` hace en producción antes de
    llamar a `_fetch_explanation` -- usado acá para que el guard de
    integridad no interfiera con los tests de detección de eco (que ya
    tienen su propia cobertura dedicada más arriba en el archivo)."""
    return {
        ai_explain._normalize_numeric_token(t)
        for t in ai_rewrite.protected_tokens(json.dumps(datos, ensure_ascii=False, default=str))
    }


def test_respuesta_es_eco_detecta_el_patron_exacto_de_la_evidencia_real():
    respuesta = _respuesta_con_eco(
        _DATOS_EVIDENCIA_REAL,
        "El veredicto de la inversión en este ticker como 'barata' se basa en que el "
        "precio actual ($286.63) es menor al valor justo total ($403.61).",
    )
    assert ai_explain._respuesta_es_eco_del_payload(respuesta, _DATOS_EVIDENCIA_REAL) is True


def test_respuesta_es_eco_no_da_falso_positivo_en_respuesta_limpia():
    respuesta = (
        "Graham + DCF + los 4 pilares evalúan si la acción cotiza por debajo de su "
        "valor justo estimado. Con el precio actual bajo el valor justo total, el "
        "modelo la marca como barata en el escenario conservador."
    )
    assert ai_explain._respuesta_es_eco_del_payload(respuesta, _DATOS_EVIDENCIA_REAL) is False


async def test_fetch_explanation_eco_del_payload_reintenta_y_se_recupera(caplog):
    """1er intento: mismo patrón exacto de la evidencia real (JSON de
    entrada + " -- " + explicación). 2o intento (reintento): respuesta
    limpia. `_fetch_explanation` NUNCA debe devolver el JSON crudo -- debe
    recuperarse con la respuesta limpia del reintento."""
    call_count = {"n": 0}
    texto_real = (
        "El veredicto de la inversión en este ticker como 'barata' se basa en que el "
        "precio actual ($286.635) es menor al valor justo total ($403.6118412598229)."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            eco = _respuesta_con_eco(_DATOS_EVIDENCIA_REAL, texto_real)
            return httpx.Response(200, json={"response": json.dumps({"respuesta": eco})})
        return httpx.Response(200, json={"response": json.dumps({"respuesta": texto_real})})

    with caplog.at_level(logging.INFO):
        resultado = await ai_explain._fetch_explanation(
            **_fetch_kwargs(
                http_client=_client_with_handler(handler),
                datos=_DATOS_EVIDENCIA_REAL,
                tokens=_tokens_de(_DATOS_EVIDENCIA_REAL),
            )
        )

    assert call_count["n"] == 2
    assert resultado == texto_real
    assert json.dumps(_DATOS_EVIDENCIA_REAL, ensure_ascii=False) not in resultado
    assert any("reintentando" in r.message for r in caplog.records)


async def test_fetch_explanation_eco_del_payload_en_ambos_intentos_cae_a_unavailable(caplog):
    """Si el eco del JSON de entrada se repite en el reintento también, se
    hacen exactamente 2 llamadas HTTP y el resultado final es
    `_ExplainUnavailable` -- el JSON crudo nunca llega al usuario."""
    call_count = {"n": 0}
    texto_real = "El precio actual es menor al valor justo total."

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        eco = _respuesta_con_eco(_DATOS_EVIDENCIA_REAL, texto_real)
        return httpx.Response(200, json={"response": json.dumps({"respuesta": eco})})

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                **_fetch_kwargs(
                    http_client=_client_with_handler(handler),
                    datos=_DATOS_EVIDENCIA_REAL,
                )
            )

    assert call_count["n"] == 2


async def test_fetch_explanation_respuesta_limpia_sin_eco_pasa_normal():
    """Sin el patrón de eco, una respuesta limpia sigue devolviéndose tal
    cual en el primer intento -- sin falsos positivos ni reintento."""
    call_count = {"n": 0}
    texto_real = "Graham + DCF miden el valor justo combinando múltiplos, flujo de caja y calidad fundamental."

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"response": json.dumps({"respuesta": texto_real})})

    resultado = await ai_explain._fetch_explanation(
        **_fetch_kwargs(
            http_client=_client_with_handler(handler),
            datos=_DATOS_EVIDENCIA_REAL,
            tokens=_tokens_de(_DATOS_EVIDENCIA_REAL),
        )
    )

    assert call_count["n"] == 1
    assert resultado == texto_real


# ---------------------------------------------------------------------------
# G ter. Mezcla de portugués -- incidente de producción 2026-09-04 (captura
# de Daniela: `qwen2.5:3b-instruct` mezcló portugués a mitad de la
# explicación de "Veredicto" pese a que el prompt pide español rioplatense).
# ---------------------------------------------------------------------------

# Texto EXACTO de la evidencia real de producción (2026-09-04).
_RESPUESTA_PORTUGUES_EVIDENCIA_REAL = (
    "Ésa é a tienda de limonada da perto do seu casa e o valor justo total "
    "representa quanto ela valeria se todos sabiam que era boa negocio. O "
    "veredicto da tienda d'áqui é barata, porque cê tá vendo-a por menos que "
    "ela vale realmente (se sábem todas as coisas), como num leva de limonada "
    "nesse bairro."
)


def test_respuesta_tiene_portugues_detecta_el_patron_exacto_de_la_evidencia_real():
    assert ai_explain._respuesta_tiene_portugues(_RESPUESTA_PORTUGUES_EVIDENCIA_REAL) is True


def test_respuesta_tiene_portugues_no_da_falso_positivo_en_respuesta_limpia():
    """Respuesta normal en español rioplatense -- cuidado con palabras que
    existen en ambos idiomas (ej. "también" en español, distinto de
    "também" en portugués) para no disparar un falso positivo."""
    respuesta = (
        "Graham + DCF miden el valor justo combinando múltiplos, flujo de caja y "
        "calidad fundamental. El veredicto de barata también depende de cuánto "
        "vale hoy comparado con ese valor justo estimado."
    )
    assert ai_explain._respuesta_tiene_portugues(respuesta) is False


async def test_fetch_explanation_portugues_reintenta_y_se_recupera(caplog):
    """1er intento: el texto exacto de la evidencia real en portugués. 2o
    intento (reintento): respuesta limpia en español. `_fetch_explanation`
    NUNCA debe devolver la mezcla de portugués -- debe recuperarse con la
    respuesta limpia del reintento."""
    call_count = {"n": 0}
    texto_real = "El veredicto de barata se basa en que el precio actual es menor al valor justo total."

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                200,
                json={"response": json.dumps({"respuesta": _RESPUESTA_PORTUGUES_EVIDENCIA_REAL})},
            )
        return httpx.Response(200, json={"response": json.dumps({"respuesta": texto_real})})

    with caplog.at_level(logging.INFO):
        resultado = await ai_explain._fetch_explanation(
            **_fetch_kwargs(
                http_client=_client_with_handler(handler),
                datos=_DATOS_EVIDENCIA_REAL,
                tokens=_tokens_de(_DATOS_EVIDENCIA_REAL),
            )
        )

    assert call_count["n"] == 2
    assert resultado == texto_real
    assert resultado != _RESPUESTA_PORTUGUES_EVIDENCIA_REAL
    assert any("reintentando" in r.message for r in caplog.records)


async def test_fetch_explanation_portugues_en_ambos_intentos_cae_a_unavailable(caplog):
    """Si la mezcla de portugués se repite en el reintento también, se
    hacen exactamente 2 llamadas HTTP y el resultado final es
    `_ExplainUnavailable` -- el texto en portugués nunca llega al usuario."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            200,
            json={"response": json.dumps({"respuesta": _RESPUESTA_PORTUGUES_EVIDENCIA_REAL})},
        )

    with caplog.at_level(logging.INFO):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                **_fetch_kwargs(
                    http_client=_client_with_handler(handler),
                    datos=_DATOS_EVIDENCIA_REAL,
                )
            )

    assert call_count["n"] == 2


async def test_fetch_explanation_respuesta_limpia_sin_portugues_pasa_normal():
    """Sin señales de portugués, una respuesta limpia en español sigue
    devolviéndose tal cual en el primer intento -- sin falsos positivos ni
    reintento."""
    call_count = {"n": 0}
    texto_real = (
        "Graham + DCF miden el valor justo combinando múltiplos, flujo de caja y "
        "calidad fundamental."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"response": json.dumps({"respuesta": texto_real})})

    resultado = await ai_explain._fetch_explanation(
        **_fetch_kwargs(
            http_client=_client_with_handler(handler),
            datos=_DATOS_EVIDENCIA_REAL,
            tokens=_tokens_de(_DATOS_EVIDENCIA_REAL),
        )
    )

    assert call_count["n"] == 1
    assert resultado == texto_real


# ---------------------------------------------------------------------------
# H. Flujo completo del handler -- leaf con Ollama ("Explicame paso a paso",
# callback_data=xp:{id}:p:{code})
# ---------------------------------------------------------------------------


async def test_handler_leaf_ollama_camino_feliz_orden_de_bloques():
    """Formato exacto de la Decisión de diseño #5 (extendida por
    SDD_explicacion_paso_a_paso.md): header -> Dato -> Cuenta -> respuesta ->
    Fórmula/Fuente -> disclaimer, en ese orden."""
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    respuesta = "Graham estima un valor conservador cercano al dato mostrado arriba."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:gra")
    await callback(update, context)

    query.answer.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_PENDING_MSG
    )
    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    assert kwargs["reply_markup"] is not None

    idx_header = texto.index(ai_rewrite.TRANSPARENCY_USED)
    idx_dato = texto.index("📌 Dato:")
    idx_cuenta = texto.index("🧮 Cuenta:")
    idx_respuesta = texto.index(respuesta)
    idx_formula = texto.index("📐 Fórmula:")
    idx_fuente = texto.index("📊 Fuente del dato:")
    idx_disclaimer = texto.index(DISCLAIMER_NO_ASESORAMIENTO)
    assert (
        idx_header < idx_dato < idx_cuenta < idx_respuesta
        < idx_formula < idx_fuente < idx_disclaimer
    )
    assert "Graham (EPS)" in texto
    assert "Conservador" in texto  # escenario elegido en el bloque Dato


async def test_handler_leaf_falla_entrega_final_avisa_con_mensaje_generico():
    """Fix urgente 2026-09-04 (paridad con `query_handler._deliver_all`):
    si el `edit_message_text` final (sobre el mensaje "🤔 Pensando…") falla,
    antes la excepción subía sin capturar y el usuario se quedaba mirando
    ese mensaje para siempre, sin ninguna respuesta. Ahora se intenta un
    `send_message` nuevo con `EXPLAIN_DELIVERY_FAILED_MSG`."""
    from telegram.error import TelegramError

    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    client = _client_with_handler(_ok_handler("Respuesta corta."))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:gra")
    context.bot.edit_message_text = AsyncMock(side_effect=TelegramError("boom entrega"))
    await callback(update, context)  # no debe propagar la excepción

    context.bot.edit_message_text.assert_awaited_once()
    assert context.bot.send_message.await_count == 2
    pending_call, generic_call = context.bot.send_message.call_args_list
    assert pending_call.kwargs["text"] == ai_explain.EXPLAIN_PENDING_MSG
    assert generic_call.kwargs["text"] == ai_explain.EXPLAIN_DELIVERY_FAILED_MSG


async def test_handler_leaf_falla_entrega_y_aviso_generico_no_lanza():
    """Peor caso: ni la edición final ni el aviso genérico logran
    entregarse -- igual no debe propagar la excepción."""
    from telegram.error import TelegramError

    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    client = _client_with_handler(_ok_handler("Respuesta corta."))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:gra")
    context.bot.edit_message_text = AsyncMock(side_effect=TelegramError("boom entrega"))
    # 1ra llamada real a `send_message` = "🤔 Pensando…" (debe seguir
    # funcionando, devuelve el mensaje "pensando"); 2da = el aviso genérico
    # de este fix, que en este test TAMBIÉN falla.
    pensando_msg = SimpleNamespace(message_id=999)
    context.bot.send_message = AsyncMock(side_effect=[pensando_msg, TelegramError("boom generico")])

    await callback(update, context)  # no debe lanzar
    assert context.bot.send_message.await_count == 2


async def test_handler_leaf_dato_cambia_segun_ticker():
    """El bloque "📌 Dato" refleja el valor real del `ExplanationContext`
    guardado -- 2 tickers distintos, 2 datos distintos."""
    store = ai_explain.ExplanationContextStore()
    client = _client_with_handler(_ok_handler("Corto."))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    cid_a = store.put(_texto_libre_context(ticker="ADBE"))
    update_a, _, context_a = _fake_callback_update(f"xp:{cid_a}:p:vf")
    await callback(update_a, context_a)
    texto_a = context_a.bot.edit_message_text.call_args.kwargs["text"]

    cid_b = store.put(_texto_libre_context(
        ticker="ZZZ",
        precio_actual=10.0,
        scenarios={"conservador": {"valor_justo_multiplos": 5, "valor_justo_graham": 5, "valor_justo_dcf": 5, "valor_justo_total": 5.0}},
    ))
    update_b, _, context_b = _fake_callback_update(f"xp:{cid_b}:p:vf")
    await callback(update_b, context_b)
    texto_b = context_b.bot.edit_message_text.call_args.kwargs["text"]

    assert "$550.00" in texto_a or "$496.00" in texto_a
    assert "$10.00" in texto_b or "$5.00" in texto_b
    assert texto_a != texto_b


def test_bloque_formula_presente_para_ver():
    """`ver` (Veredicto, texto libre) SÍ tiene fórmula/fuente propias
    (pedido explícito de Daniela 2026-09-03: aunque combina varios modelos,
    la comparación precio vs. Valor Justo Total es una regla concreta, no
    una síntesis narrativa sin cuenta detrás) -- a diferencia de `mod`
    (avanzado), que sigue siendo una síntesis narrativa de los 5 modelos
    sin fórmula/fuente propia."""
    assert "ver" in ai_explain_content.FORMULAS_TEXTO_LIBRE
    assert "ver" in ai_explain_content.FUENTES_TEXTO_LIBRE


def test_bloque_formula_ausente_para_mod():
    """`mod` (avanzado, "¿qué modelos aplican?") sigue sin entrada en
    `FORMULAS`/`FUENTES` -- es una síntesis narrativa de los 5 modelos, no
    una fórmula propia -- omitida, nunca vacía."""
    assert "mod" not in ai_explain_content.FORMULAS_AVANZADO
    assert "mod" not in ai_explain_content.FUENTES_AVANZADO


def test_build_leaf_message_omite_bloque_si_no_hay_formula_ni_fuente():
    texto = ai_explain._build_leaf_message("Dato de prueba", "Respuesta corta.", None, None)
    assert "📐 Fórmula:" not in texto
    assert "📊 Fuente del dato:" not in texto


def test_build_leaf_message_omite_solo_formula_si_falta():
    texto = ai_explain._build_leaf_message("Dato", "Respuesta.", None, "una fuente")
    assert "📐 Fórmula:" not in texto
    assert "📊 Fuente del dato: una fuente" in texto


# ---------------------------------------------------------------------------
# I. Contenido determinístico -- evt/inf (Decisión de diseño #4)
# ---------------------------------------------------------------------------


async def test_evt_responde_inmediato_sin_ollama_ni_pensando():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:evt")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.call_args
    assert kwargs["text"] != ai_explain.EXPLAIN_PENDING_MSG
    assert kwargs["text"].startswith(ai_explain.DETERMINISTIC_PREFIX)
    context.bot.edit_message_text.assert_not_called()
    assert spy.call_count == 0


async def test_inf_responde_inmediato_con_intro_y_transparencia():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:inf")
    await callback(update, context)

    _, kwargs = context.bot.send_message.call_args
    texto = kwargs["text"]
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX)
    assert "Tienda de Limonada" in texto
    assert "Notas de transparencia" in texto
    assert spy.call_count == 0


async def test_evt_sin_eventos_no_rompe():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context(corporate_events=[]))
    clients = _make_clients(http_client=_CountingClient(), ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:evt")
    await callback(update, context)

    _, kwargs = context.bot.send_message.call_args
    assert "No hay eventos corporativos" in kwargs["text"]


async def test_evt_inf_no_consumen_rate_limiter():
    """Decisión de diseño #10 -- `evt`/`inf` no consumen el balde
    compartido, ni siquiera con el balde ya agotado."""
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient(), ollama_config=_enabled_config())
    limiter = FakeRateLimiter(allow_value=False)  # balde agotado
    callback = _build_callback(clients, limiter, store)

    for code in ("evt", "inf"):
        update, query, context = _fake_callback_update(f"xp:{cid}:{code}")
        await callback(update, context)
        _, kwargs = context.bot.send_message.call_args
        assert kwargs["text"] != ai_explain.RATE_LIMITED_MSG
    assert limiter.calls_with_key == []  # nunca se consultó el balde


async def test_menu_y_categoria_no_consumen_rate_limiter():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    limiter = FakeRateLimiter(allow_value=False)
    callback = _build_callback(clients, limiter, store)

    for data in (f"xp:{cid}:m", f"xp:{cid}:c:val"):
        update, query, context = _fake_callback_update(data)
        await callback(update, context)
        _, kwargs = context.bot.send_message.call_args
        assert kwargs["text"] != ai_explain.RATE_LIMITED_MSG
    assert limiter.calls_with_key == []


async def test_leaf_con_ollama_si_consume_rate_limiter():
    """"Explicame paso a paso" (`:p:`) SÍ consume el balde compartido --
    "Ver dato" (`:vf:` a secas) NO lo consume, cubierto en la sección
    "Ver dato" más abajo."""
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    limiter = FakeRateLimiter(allow_value=False)
    callback = _build_callback(clients, limiter, store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.RATE_LIMITED_MSG
    )
    assert spy.call_count == 0
    assert limiter.calls_with_key == [str(_DEFAULT_CHAT_ID)]


# ---------------------------------------------------------------------------
# J. D1 -- Beneish siempre "no calculable" (no reabrir)
# ---------------------------------------------------------------------------


async def test_ben_siempre_no_calculable_via_handler():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_avanzado_context())
    respuesta = "El Beneish M-Score no se puede calcular con los datos disponibles."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:ben")
    await callback(update, context)

    _, kwargs = context.bot.edit_message_text.call_args
    assert "No calculable con este plan de FMP" in kwargs["text"] or respuesta in kwargs["text"]


# ---------------------------------------------------------------------------
# K. Hallazgo 9 MEDIO de `security` -- ExplanationContext atado al chat_id
# ---------------------------------------------------------------------------


CHAT_A = 111
CHAT_B = 222


@pytest.mark.parametrize("data_suffix", ["m", "c:val", "vf"])
async def test_hallazgo9_context_id_de_otro_chat_id_responde_vencido(data_suffix):
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context(chat_id=CHAT_A, ticker="AAPL"))
    clients = _make_clients(http_client=_CountingClient(), ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:{data_suffix}", chat_id=CHAT_B)
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=CHAT_B, text=ai_explain.EXPLAIN_EXPIRED_MSG
    )
    # Nunca aparece contenido del análisis ajeno (ticker/veredicto/botones de
    # Nivel 2 de AAPL) en ninguna llamada.
    for call in context.bot.send_message.call_args_list:
        assert "AAPL" not in str(call)


async def test_hallazgo9_propio_chat_id_sigue_funcionando_tras_intento_ajeno():
    store = ai_explain.ExplanationContextStore()
    cid_a = store.put(_texto_libre_context(chat_id=CHAT_A, ticker="AAPL"))
    cid_b = store.put(_texto_libre_context(chat_id=CHAT_B, ticker="MSFT"))
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    # B intenta acceder al contexto de A (falla).
    update, query, context = _fake_callback_update(f"xp:{cid_a}:m", chat_id=CHAT_B)
    await callback(update, context)
    context.bot.send_message.assert_awaited_once_with(
        chat_id=CHAT_B, text=ai_explain.EXPLAIN_EXPIRED_MSG
    )

    # El propio context_id de B sigue funcionando con normalidad.
    update2, query2, context2 = _fake_callback_update(f"xp:{cid_b}:m", chat_id=CHAT_B)
    await callback(update2, context2)
    _, kwargs = context2.bot.send_message.call_args
    assert kwargs["reply_markup"] is not None


async def test_hallazgo9_mensaje_y_log_indistinguibles_de_context_id_inexistente(caplog):
    store = ai_explain.ExplanationContextStore()
    cid_a = store.put(_texto_libre_context(chat_id=CHAT_A))
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    with caplog.at_level(logging.WARNING):
        update_ajeno, _, context_ajeno = _fake_callback_update(f"xp:{cid_a}:m", chat_id=CHAT_B)
        await callback(update_ajeno, context_ajeno)
    logs_ajeno = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        update_inexistente, _, context_inexistente = _fake_callback_update("xp:deadbeef:m", chat_id=CHAT_B)
        await callback(update_inexistente, context_inexistente)
    logs_inexistente = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    msg_ajeno = context_ajeno.bot.send_message.call_args.kwargs["text"]
    msg_inexistente = context_inexistente.bot.send_message.call_args.kwargs["text"]
    assert msg_ajeno == msg_inexistente == ai_explain.EXPLAIN_EXPIRED_MSG
    # Mismo formato de log (mismo template, ambos WARNING) -- no debe poder
    # inferirse por el log si el context_id probado existe.
    assert len(logs_ajeno) == len(logs_inexistente) == 1
    assert logs_ajeno[0].startswith("context_id inválido, vencido, o de otro chat_id")
    assert logs_inexistente[0].startswith("context_id inválido, vencido, o de otro chat_id")


async def test_hallazgo9_no_consume_rate_limiter_para_navegacion_ajena():
    """Decisión de diseño #10 amplía la ventana de explotación (más
    intentos/seg sin gastar el balde) -- confirmado que sigue así, el fix es
    la verificación de ownership, no el rate limiter."""
    store = ai_explain.ExplanationContextStore()
    cid_a = store.put(_texto_libre_context(chat_id=CHAT_A))
    clients = _make_clients(http_client=_CountingClient())
    limiter = FakeRateLimiter(allow_value=True)
    callback = _build_callback(clients, limiter, store)

    update, query, context = _fake_callback_update(f"xp:{cid_a}:m", chat_id=CHAT_B)
    await callback(update, context)
    assert limiter.calls_with_key == []


# ---------------------------------------------------------------------------
# L. Gate de chat_id (hallazgo 2 de `security`, spec anterior) -- regresión
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "callback_data",
    ["xp:a1b2c3d4:vf", "xp:a1b2c3d4:m", "xp:a1b2c3d4:c:val"],
)
async def test_gate_chat_id_no_autorizado_corta_antes_del_handler_xp(callback_data):
    """El gate global (`TypeHandler(Update, ...)` en `group=-1`) cubre las 3
    formas de `callback_data` `xp:` por igual -- la navegación nueva (`:m`,
    `:c:{cat}`) queda protegida automáticamente, sin código adicional."""
    from telegram.ext import ApplicationHandlerStop

    from investbot import security

    gate = security.build_chat_id_gate(frozenset({777}))
    update = SimpleNamespace()
    update.effective_chat = SimpleNamespace(id=999999, type="private")
    update.callback_query = SimpleNamespace(data=callback_data, answer=AsyncMock())

    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())

    update.callback_query.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# M. Logging seguro
# ---------------------------------------------------------------------------


async def test_log_de_guard_fallido_sanitiza_saltos_de_linea_y_control(caplog):
    respuesta_con_control = "Inventa 999.0%\ncon salto\x07de línea y control."
    client = _client_with_handler(_ok_handler(respuesta_con_control))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                **_fetch_kwargs(http_client=client, tokens={"500.0"})
            )

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    for record in warning_records:
        mensaje = record.getMessage()
        assert "\n" not in mensaje
        assert "\x07" not in mensaje


# ---------------------------------------------------------------------------
# N. Reproducción exacta de los 2 bugs de producción (log VPS 2026-09-02)
# ---------------------------------------------------------------------------


async def test_handler_bug1_produccion_simbolo_pesos_ya_no_se_rechaza():
    ctx = _texto_libre_context(
        escenario_elegido="conservador",
        precio_actual=282.03,
        scenarios={
            "conservador": {
                "valor_justo_multiplos": 400.0,
                "valor_justo_graham": 410.0,
                "valor_justo_dcf": 405.63,
                "valor_justo_total": 405.63,
            }
        },
    )
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta_real = (
        "El valor justo estimado para el escenario conservador es de $405.63, que se "
        "encuentra por encima del precio actual de $282.03. Este resultado muestra que "
        "la empresa tiene un potencial de crecimiento significativo."
    )
    client = _client_with_handler(_ok_handler(respuesta_real))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert respuesta_real in kwargs["text"]
    assert kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG


async def test_handler_bug2_produccion_4_pilares_ya_no_se_rechaza():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    respuesta_real = (
        "Los 4 pilares de una 'buena empresa' son ingresos crecientes, utilidades "
        "crecientes, deuda controlada y precio razonable. Este ticker cumple con los "
        "pilares de ingresos crecientes y precio razonable."
    )
    client = _client_with_handler(_ok_handler(respuesta_real))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:pil")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert respuesta_real in kwargs["text"]
    assert kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG


# ---------------------------------------------------------------------------
# O. Nombre del modelo + guard de integridad
# ---------------------------------------------------------------------------


async def test_handler_modelo_dcf_mencionado_por_ollama_no_se_rechaza():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    respuesta_real = (
        "Este dato sale del modelo de Graham (EPS) combinado con el DCF, "
        "que estima el valor a partir del flujo de caja descontado."
    )
    client = _client_with_handler(_ok_handler(respuesta_real))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert respuesta_real in kwargs["text"]
    assert kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG


async def test_handler_modelo_aqr_mencionado_por_ollama_no_se_rechaza():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_avanzado_context())
    respuesta_real = (
        "Este puntaje sale del scoring de factores estilo AQR, que combina "
        "Value, Quality, Momentum y Low-vol en una sola etiqueta."
    )
    client = _client_with_handler(_ok_handler(respuesta_real))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:aqv")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert respuesta_real in kwargs["text"]
    assert kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG


# ---------------------------------------------------------------------------
# Mensajes / constantes -- contenido literal, sin mockear HTTP
# ---------------------------------------------------------------------------


def test_mensajes_no_revelan_infraestructura():
    for msg in (ai_explain.EXPLAIN_UNAVAILABLE_MSG, ai_explain.EXPLAIN_EXPIRED_MSG):
        assert "100.101.102.103" not in msg
        assert "qwen2.5" not in msg
        assert "11434" not in msg


def test_system_prompt_explain_pide_json_y_brevedad():
    assert "{\"respuesta\"" in ai_explain.SYSTEM_PROMPT_EXPLAIN
    assert "2 a 4 oraciones" in ai_explain.SYSTEM_PROMPT_EXPLAIN


def test_system_prompt_explain_regla_5_pide_nombrar_el_modelo():
    assert "5." in ai_explain.SYSTEM_PROMPT_EXPLAIN
    assert "\"modelo\"" in ai_explain.SYSTEM_PROMPT_EXPLAIN


# ---------------------------------------------------------------------------
# P. Tablas de contenido -- cobertura completa (27 question_code)
# ---------------------------------------------------------------------------


def test_13_question_codes_texto_libre():
    assert len(ai_explain_content.QUESTIONS_TEXTO_LIBRE) == 13


def test_14_question_codes_avanzado():
    assert len(ai_explain_content.QUESTIONS_AVANZADO) == 14


def test_todas_las_categorias_referencian_solo_codes_conocidos():
    for cat in ai_explain_content.CATEGORIES_TEXTO_LIBRE.values():
        for code in cat.question_codes:
            assert code in ai_explain_content.QUESTIONS_TEXTO_LIBRE
    for cat in ai_explain_content.CATEGORIES_AVANZADO.values():
        for code in cat.question_codes:
            assert code in ai_explain_content.QUESTIONS_AVANZADO


def test_level1_cubre_todos_los_leaves_sueltos_y_categorias():
    codes_nivel1_tl = {code for _, code in ai_explain_content.LEVEL1_TEXTO_LIBRE}
    assert codes_nivel1_tl == {"ver", "val", "cal", "rie", "inf"}
    codes_nivel1_av = {code for _, code in ai_explain_content.LEVEL1_AVANZADO}
    assert codes_nivel1_av == {"mod", "alt", "pio", "ben", "mag", "aqr"}


# ---------------------------------------------------------------------------
# Q. QuestionSpec.variant -- 27 preguntas clasificadas (SDD_explicacion_
# paso_a_paso.md, Decisión de diseño #1)
# ---------------------------------------------------------------------------


def test_22_preguntas_dato_y_paso_a_paso():
    todas = {**ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO}
    con_paso_a_paso = [
        c for c, s in todas.items() if s.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO
    ]
    assert len(con_paso_a_paso) == 22
    for code in con_paso_a_paso:
        spec = todas[code]
        assert spec.pregunta_paso_a_paso
        assert spec.pregunta_narrativa is None


def test_3_preguntas_narrativa_mod_ben_ren():
    todas = {**ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO}
    narrativas = {
        c for c, s in todas.items() if s.variant == ai_explain_content.VARIANT_NARRATIVA
    }
    assert narrativas == {"mod", "ben", "ren"}
    for code in narrativas:
        assert todas[code].pregunta_narrativa
        assert todas[code].pregunta_paso_a_paso is None


def test_2_preguntas_deterministico_evt_inf():
    todas = {**ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO}
    deterministicas = {
        c for c, s in todas.items() if s.variant == ai_explain_content.VARIANT_DETERMINISTICO
    }
    assert deterministicas == {"evt", "inf"}


# ---------------------------------------------------------------------------
# R. 4ª forma de callback_data (xp:{id}:p:{code}) -- regex + dispatch
# ---------------------------------------------------------------------------

_TODAS_LAS_PREGUNTAS = {
    **ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO,
}
_CODES_DATO_Y_PASO_A_PASO = [
    c for c, s in _TODAS_LAS_PREGUNTAS.items() if s.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO
]
_CODES_NARRATIVA_O_DETERMINISTICO = [
    c for c, s in _TODAS_LAS_PREGUNTAS.items() if s.variant != ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO
]


@pytest.mark.parametrize("code", _CODES_DATO_Y_PASO_A_PASO)
def test_callback_paso_a_paso_regex_matchea_codes_dato_y_paso_a_paso(code):
    m = ai_explain._CALLBACK_PASO_A_PASO_RE.fullmatch(f"xp:a1b2c3d4:p:{code}")
    assert m is not None
    assert m.group(1) == "a1b2c3d4"
    assert m.group(2) == code


@pytest.mark.parametrize("code", list(_TODAS_LAS_PREGUNTAS))
def test_callback_paso_a_paso_nunca_matchea_el_regex_de_leaf(code):
    """`_CALLBACK_PASO_A_PASO_RE` es mutuamente excluyente con
    `_CALLBACK_LEAF_RE` -- ningún `callback_data` válido matchea las 2 formas
    a la vez (mismo criterio que la revisión de `security`)."""
    data_leaf = f"xp:a1b2c3d4:{code}"
    data_paso_a_paso = f"xp:a1b2c3d4:p:{code}"
    assert ai_explain._CALLBACK_LEAF_RE.fullmatch(data_leaf) is not None
    assert ai_explain._CALLBACK_PASO_A_PASO_RE.fullmatch(data_leaf) is None
    assert ai_explain._CALLBACK_PASO_A_PASO_RE.fullmatch(data_paso_a_paso) is not None
    assert ai_explain._CALLBACK_LEAF_RE.fullmatch(data_paso_a_paso) is None
    assert ai_explain._CALLBACK_MENU_RE.fullmatch(data_paso_a_paso) is None
    assert ai_explain._CALLBACK_CATEGORY_RE.fullmatch(data_paso_a_paso) is None


async def test_callback_paso_a_paso_con_code_de_variant_distinta_es_invalido():
    """`xp:{id}:p:{code}` con un `code` de `variant` distinta a
    `dato_y_paso_a_paso` (ej. `mod`, narrativa) responde `EXPLAIN_INVALID_MSG`
    -- mismo camino que un `question_code` desconocido."""
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_avanzado_context())
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:mod")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_INVALID_MSG
    )
    assert spy.call_count == 0


async def test_callback_paso_a_paso_con_code_inexistente_es_invalido():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:zzz")
    await callback(update, context)

    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_INVALID_MSG
    )


@pytest.mark.parametrize("code", _CODES_DATO_Y_PASO_A_PASO)
def test_2_botones_ver_dato_y_paso_a_paso_para_cada_pregunta(code):
    """Cada pregunta `dato_y_paso_a_paso` muestra 2 botones hermanos, ambos
    con el nombre propio de la pregunta (`spec.label`, sin duplicar su
    emoji si ya trae uno) para distinguirse de las demás filas de su misma
    categoría -- fix UX 2026-09-03 -- distinguidos entre sí por el prefijo
    "📊"/"🎓" y por `callback_data` (`xp:{id}:{code}` vs `xp:{id}:p:{code}`)
    -- test explícito por cada una de las 22 preguntas."""
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    spec = ai_explain_content.all_questions(kind)[code]
    fila = ai_explain._leaf_rows("a1b2c3d4", code, spec)[0]
    label = ai_explain._label_sin_emoji_propio(spec.label)
    assert len(fila) == 2
    assert fila[0].text == f"📊 {label}"
    assert fila[0].callback_data == f"xp:a1b2c3d4:{code}"
    assert fila[1].text == f"🎓 {label}"
    assert fila[1].callback_data == f"xp:a1b2c3d4:p:{code}"


def test_botones_distinguibles_entre_preguntas_de_una_misma_categoria():
    """Bug de UX confirmado con captura de pantalla (2026-09-03): las 5
    preguntas de la categoría "Valoración" (vf/gra/dcf/mul/rat) mostraban
    filas de botones visualmente IDÉNTICAS ("Ver dato" | "Explicame paso a
    paso" repetido 5 veces). Ahora cada fila debe ser identificable a
    simple vista -- ningún par de preguntas comparte el mismo texto de
    botón, ni en la columna "Ver dato" ni en la columna "paso a paso"."""
    cat = ai_explain_content.CATEGORIES_TEXTO_LIBRE["val"]
    questions = ai_explain_content.all_questions("texto_libre")
    filas = [
        ai_explain._leaf_rows("a1b2c3d4", code, questions[code])[0] for code in cat.question_codes
    ]
    assert len(cat.question_codes) == 5

    textos_ver_dato = [fila[0].text for fila in filas]
    textos_paso_a_paso = [fila[1].text for fila in filas]
    assert len(set(textos_ver_dato)) == 5
    assert len(set(textos_paso_a_paso)) == 5


@pytest.mark.parametrize("code", _CODES_NARRATIVA_O_DETERMINISTICO)
def test_1_boton_sin_p_para_narrativa_y_deterministico(code):
    """`mod`/`ben`/`ren`/`evt`/`inf` muestran exactamente 1 botón,
    `callback_data=xp:{id}:{code}` -- sin botón `:p:`."""
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    spec = ai_explain_content.all_questions(kind)[code]
    fila = ai_explain._leaf_rows("a1b2c3d4", code, spec)[0]
    assert len(fila) == 1
    assert fila[0].callback_data == f"xp:a1b2c3d4:{code}"
    assert ":p:" not in fila[0].callback_data


# ---------------------------------------------------------------------------
# S. Menú siempre presente -- Decisión de diseño #2
# ---------------------------------------------------------------------------


def test_category_of_exhaustivo_27_preguntas():
    esperado_texto_libre = {
        "ver": None, "vf": "val", "gra": "val", "dcf": "val", "mul": "val", "rat": "val",
        "pil": "cal", "ren": "cal", "rsk": "rie", "mom": "rie", "cmp": "rie", "evt": "rie",
        "inf": None,
    }
    for code, esperado in esperado_texto_libre.items():
        assert ai_explain_content.category_of("texto_libre", code) == esperado

    esperado_avanzado = {
        "mod": None, "alz": "alt", "azp": "alt", "pig": "pio", "pir": "pio", "pia": "pio",
        "pie": "pio", "ben": None, "mgr": "mag", "mge": "mag",
        "aqv": "aqr", "aqq": "aqr", "aqm": "aqr", "aql": "aqr",
    }
    for code, esperado in esperado_avanzado.items():
        assert ai_explain_content.category_of("avanzado", code) == esperado


def test_build_response_keyboard_pregunta_con_categoria_es_nivel2():
    ctx = _texto_libre_context()
    markup = ai_explain.build_response_keyboard("texto_libre", "a1b2c3d4", "gra", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert codes == ["vf", "gra", "dcf", "mul", "rat", "m"]  # Nivel 2 de "val"


def test_build_response_keyboard_pregunta_suelta_es_nivel1():
    ctx = _texto_libre_context()
    markup = ai_explain.build_response_keyboard("texto_libre", "a1b2c3d4", "ver", ctx)
    codes = [fila[0].callback_data.split(":")[-1] for fila in markup.inline_keyboard]
    assert codes == ["ver", "val", "cal", "rie", "inf"]  # Nivel 1


def test_build_response_keyboard_kind_correcto_no_se_cruza():
    """Caso explícito de `qa`: para las preguntas sueltas, el teclado de
    Nivel 1 adjunto es el de su `kind` correcto -- nunca se cruza con el
    teclado del otro `kind`."""
    markup_tl = ai_explain.build_response_keyboard("texto_libre", "a1b2c3d4", "ver", _texto_libre_context())
    codes_tl = {fila[0].callback_data.split(":")[-1] for fila in markup_tl.inline_keyboard}
    assert codes_tl == {"ver", "val", "cal", "rie", "inf"}

    markup_av = ai_explain.build_response_keyboard("avanzado", "a1b2c3d4", "mod", _avanzado_context())
    codes_av = {fila[0].callback_data.split(":")[-1] for fila in markup_av.inline_keyboard}
    assert codes_av == {"mod", "alt", "pio", "ben", "mag", "aqr"}


async def test_ver_dato_lleva_reply_markup_de_nivel2():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:gra")
    await callback(update, context)

    _, kwargs = context.bot.send_message.call_args
    assert kwargs["reply_markup"] is not None
    codes = [fila[0].callback_data.split(":")[-1] for fila in kwargs["reply_markup"].inline_keyboard]
    assert codes == ["vf", "gra", "dcf", "mul", "rat", "m"]


async def test_narrativa_lleva_reply_markup_de_nivel2():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    client = _client_with_handler(_ok_handler("Corto."))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:ren")
    await callback(update, context)

    _, kwargs = context.bot.edit_message_text.call_args
    assert kwargs["reply_markup"] is not None
    codes = [fila[0].callback_data.split(":")[-1] for fila in kwargs["reply_markup"].inline_keyboard]
    assert codes == ["pil", "ren", "m"]


async def test_evt_inf_llevan_reply_markup():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    for code in ("evt", "inf"):
        update, query, context = _fake_callback_update(f"xp:{cid}:{code}")
        await callback(update, context)
        _, kwargs = context.bot.send_message.call_args
        assert kwargs["reply_markup"] is not None


@pytest.mark.parametrize(
    "kind,category_code,codes_esperados",
    [
        ("texto_libre", "val", ["vf", "gra", "dcf", "mul", "rat"]),
        ("texto_libre", "cal", ["pil", "ren"]),
        ("texto_libre", "rie", ["rsk", "mom", "cmp", "evt"]),
        ("avanzado", "alt", ["alz", "azp"]),
        ("avanzado", "pio", ["pig", "pir", "pia", "pie"]),
        ("avanzado", "mag", ["mgr", "mge"]),
        ("avanzado", "aqr", ["aqv", "aqq", "aqm", "aql"]),
    ],
)
async def test_reply_markup_4_categorias_texto_libre_y_4_avanzado(kind, category_code, codes_esperados):
    """"Ver dato" y "Explicame paso a paso" de cualquier pregunta con
    categoría llevan el mismo teclado de Nivel 2 -- test por cada una de
    las 4 categorías de texto libre + 4 de /avanzado."""
    ctx = _texto_libre_context() if kind == "texto_libre" else _avanzado_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    code_muestra = codes_esperados[0]
    respuesta = "Respuesta corta de prueba."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    # "Ver dato"
    update, _, context = _fake_callback_update(f"xp:{cid}:{code_muestra}")
    await callback(update, context)
    _, kwargs = context.bot.send_message.call_args
    codes = [fila[0].callback_data.split(":")[-1] for fila in kwargs["reply_markup"].inline_keyboard]
    assert codes[:-1] == codes_esperados if kind == "avanzado" else True
    assert codes[-1] == "m"

    # "Explicame paso a paso"
    update2, _, context2 = _fake_callback_update(f"xp:{cid}:p:{code_muestra}")
    await callback(update2, context2)
    _, kwargs2 = context2.bot.edit_message_text.call_args
    codes2 = [fila[0].callback_data.split(":")[-1] for fila in kwargs2["reply_markup"].inline_keyboard]
    assert codes2[-1] == "m"


async def test_regresion_negativa_ningun_envio_de_dispatch_leaf_sin_reply_markup():
    """Test de regresión negativo: TODAS las llamadas a `send_message`/
    `edit_message_text` de `_dispatch_leaf` incluyen `reply_markup` no-`None`
    -- barrido de las 3 ramas (determinístico, narrativa, paso a paso)."""
    store = ai_explain.ExplanationContextStore()
    cid_tl = store.put(_texto_libre_context())
    cid_av = store.put(_avanzado_context())
    client = _client_with_handler(_ok_handler("Respuesta corta de prueba."))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    casos = [
        (cid_tl, "evt"), (cid_tl, "inf"),          # deterministico
        (cid_tl, "ren"), (cid_av, "mod"), (cid_av, "ben"),  # narrativa
        (cid_tl, "gra"), (cid_av, "alz"),          # "Ver dato"
        (cid_tl, "p:gra"), (cid_av, "p:alz"),      # "paso a paso"
    ]
    for cid, suffix in casos:
        update, _, context = _fake_callback_update(f"xp:{cid}:{suffix}")
        await callback(update, context)
        send_calls = context.bot.send_message.call_args_list
        edit_calls = context.bot.edit_message_text.call_args_list
        todas = send_calls + edit_calls
        assert todas, f"sin ninguna llamada para {suffix}"
        _, kwargs = todas[-1]
        assert kwargs.get("reply_markup") is not None, f"reply_markup ausente para {suffix}"


# ---------------------------------------------------------------------------
# T. "Ver dato" -- botón determinístico nuevo, generalizado a las 22
# preguntas dato_y_paso_a_paso (Decisión de diseño #9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", _CODES_DATO_Y_PASO_A_PASO)
async def test_ver_dato_nunca_llama_a_ollama_ni_manda_pensando_ni_consume_balde(code):
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    ctx = _texto_libre_context() if kind == "texto_libre" else _avanzado_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    limiter = FakeRateLimiter(allow_value=True)
    callback = _build_callback(clients, limiter, store)

    update, query, context = _fake_callback_update(f"xp:{cid}:{code}")
    await callback(update, context)

    assert spy.call_count == 0
    context.bot.edit_message_text.assert_not_called()
    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.call_args
    assert kwargs["text"] != ai_explain.EXPLAIN_PENDING_MSG
    assert limiter.calls_with_key == []


@pytest.mark.parametrize("code", _CODES_DATO_Y_PASO_A_PASO)
async def test_ver_dato_incluye_dato_formula_fuente_nunca_cuenta_ni_disclaimer(code):
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    ctx = _texto_libre_context() if kind == "texto_libre" else _avanzado_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    clients = _make_clients(http_client=_CountingClient())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:{code}")
    await callback(update, context)

    _, kwargs = context.bot.send_message.call_args
    texto = kwargs["text"]
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX)
    assert "📌 Dato:" in texto
    assert "🧮 Cuenta:" not in texto
    assert DISCLAIMER_NO_ASESORAMIENTO not in texto
    formula = ai_explain_content.formulas(kind).get(code)
    fuente = ai_explain_content.fuentes(kind).get(code)
    if formula:
        assert "📐 Fórmula:" in texto
    if fuente:
        assert "📊 Fuente del dato:" in texto


# ---------------------------------------------------------------------------
# U. "🧮 Cuenta" -- 100% Python, dispatch de las 22 preguntas
# (SDD_explicacion_paso_a_paso.md, Decisión de diseño #3/#4)
# ---------------------------------------------------------------------------


def test_build_cuenta_line_es_funcion_pura_sin_io():
    """Mismo criterio de testabilidad que `_build_dato_line` -- `dict` de
    entrada -> `Optional[str]`, callable directo sin mockear nada."""
    import inspect
    assert not inspect.iscoroutinefunction(ai_explain._build_cuenta_line)


def test_cuenta_alz_verificada_termino_a_termino_ejemplo_de_daniela():
    """Ejemplo textual EXACTO de Daniela (Contexto, punto 1 de la spec)."""
    a, b, c, d, e = 0.34, 0.12, 0.18, 1.05, 0.87
    z = 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e
    datos = {"altman": {"disponible": True, "a": a, "b": b, "c": c, "d": d, "e": e, "z": z}}
    cuenta = ai_explain._build_cuenta_line("avanzado", "alz", datos)
    assert cuenta == (
        "Z = 1.2×0.34 + 1.4×0.12 + 3.3×0.18 + 0.6×1.05 + 1.0×0.87 = "
        "0.41 + 0.17 + 0.59 + 0.63 + 0.87 = 2.67"
    )


def test_cuenta_azp_verificada_termino_a_termino_ejemplo_de_daniela():
    a, b, c, d = 0.34, 0.12, 0.18, 1.05
    z = 6.56 * a + 3.26 * b + 6.72 * c + 1.05 * d
    datos = {"altman_pp": {"disponible": True, "a": a, "b": b, "c": c, "d": d, "e": None, "z": z}}
    cuenta = ai_explain._build_cuenta_line("avanzado", "azp", datos)
    assert cuenta == (
        "Z'' = 6.56×0.34 + 3.26×0.12 + 6.72×0.18 + 1.05×1.05 = "
        "2.23 + 0.39 + 1.21 + 1.10 = 4.93"
    )


def test_cuenta_alz_no_calculable_ausente_sin_none_visible():
    datos = {"altman": {"disponible": False, "campos_faltantes": ["ebit"]}}
    assert ai_explain._build_cuenta_line("avanzado", "alz", datos) is None


def test_cuenta_azp_no_calculable_ausente():
    datos = {"altman_pp": {"disponible": False, "campos_faltantes": ["ebit"]}}
    assert ai_explain._build_cuenta_line("avanzado", "azp", datos) is None


def test_cuenta_pir_verificada_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "pir")
    cuenta = ai_explain._build_cuenta_line("avanzado", "pir", datos)
    assert cuenta == (
        "Ganancia Neta: $118.00 > 0 → cumplido · CFO: $118.00 > 0 → cumplido · "
        "ROA: 0.18 > 0.15 → cumplido · CFO > Utilidad: $118.00 > $97.00 → no cumplido"
    )


def test_cuenta_pia_verificada_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "pia")
    cuenta = ai_explain._build_cuenta_line("avanzado", "pia", datos)
    assert cuenta == (
        "Apalancamiento: 0.20 < 0.30 → cumplido · Liquidez: 1.10 > 1.30 → no cumplido · "
        "Acciones en circulación: 100.00 ≤ 102.00 → cumplido"
    )


def test_cuenta_pie_verificada_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "pie")
    cuenta = ai_explain._build_cuenta_line("avanzado", "pie", datos)
    assert cuenta == (
        "Margen bruto: 43.0% > 40.0% → cumplido · Rotación de activos: 0.90 > 0.95 → no cumplido"
    )


def test_cuenta_pig_conteo_simple():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "pig")
    cuenta = ai_explain._build_cuenta_line("avanzado", "pig", datos)
    assert cuenta == "7 de 9 criterios evaluables cumplidos"


def test_cuenta_pig_evaluables_0_no_calculable():
    datos = {"piotroski": {"puntaje": 0, "criterios_evaluables": 0}}
    assert ai_explain._build_cuenta_line("avanzado", "pig", datos) is None


@pytest.mark.parametrize("code", ["pir", "pia", "pie"])
def test_cuenta_piotroski_criterio_no_evaluable_se_omite_sin_none_visible(code):
    ctx = _avanzado_context(piotroski={
        "puntaje": 0, "criterios_evaluables": 0, "criterios_totales": 9,
        "criterios": [
            {"nombre": n, "cumplido": None, "valores": None}
            for n in ("roa_positivo", "cfo_positivo", "roa_creciente", "cfo_mayor_utilidad",
                      "apalancamiento_decreciente", "liquidez_creciente", "sin_dilucion",
                      "margen_bruto_creciente", "rotacion_activos_creciente")
        ],
    })
    datos = ai_explain._build_explain_payload(ctx, code)
    assert ai_explain._build_cuenta_line("avanzado", code, datos) is None


def test_cuenta_mgr_verificada_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "mgr")
    cuenta = ai_explain._build_cuenta_line("avanzado", "mgr", datos)
    assert cuenta == "ROIC = $114,000.00 / $570,000.00 = 0.20 = 20.0%"


def test_cuenta_mge_verificada_termino_a_termino_muestra_armado_de_ev():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "mge")
    cuenta = ai_explain._build_cuenta_line("avanzado", "mge", datos)
    assert cuenta == (
        "EY = $114,000.00 / ($1,400,000.00 + $100,000.00 − $75,000.00) = "
        "$114,000.00 / $1,425,000.00 = 0.08 = 8.0%"
    )


def test_cuenta_mgr_mge_no_disponible_ausente():
    ctx = _avanzado_context(magic={"disponible": False, "campos_faltantes": ["ebit"]})
    assert ai_explain._build_cuenta_line(
        "avanzado", "mgr", ai_explain._build_explain_payload(ctx, "mgr")
    ) is None
    assert ai_explain._build_cuenta_line(
        "avanzado", "mge", ai_explain._build_explain_payload(ctx, "mge")
    ) is None


def test_cuenta_aqv_entre_umbrales():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqv")
    cuenta = ai_explain._build_cuenta_line("avanzado", "aqv", datos)
    assert "Earnings Yield 8.0%" in cuenta
    assert "→ alto" in cuenta


def test_cuenta_aqq_suma_de_sub_metricas():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqq")
    cuenta = ai_explain._build_cuenta_line("avanzado", "aqq", datos)
    assert "ROE 22.0%" in cuenta
    assert "Margen bruto 55.0%" in cuenta
    assert "→ alto" in cuenta


def test_cuenta_aqm_sin_precio_avg_no_calculable():
    """SDD_desglose_universal.md, Grupo F, Cambio 4 -- con el fixture por
    defecto (sin `precio_actual`/`price_avg_50`/`price_avg_200`) `_cuenta_
    aqm` ya no puede armar la comparación real -- `None`, mismo criterio de
    "no calculable" que `market_context.calculate_momentum`."""
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    assert ai_explain._build_cuenta_line("avanzado", "aqm", datos) is None


@pytest.mark.parametrize(
    "precio, avg50, avg200, cmp50, cmp200",
    [
        (150.0, 140.0, 130.0, ">", ">"),
        (150.0, 160.0, 130.0, "<", ">"),
        (150.0, 140.0, 160.0, ">", "<"),
        (150.0, 160.0, 170.0, "<", "<"),
    ],
)
def test_cuenta_aqm_termino_a_termino_4_combinaciones(precio, avg50, avg200, cmp50, cmp200):
    """Branch coverage 100% pedido por QA -- las 4 combinaciones posibles de
    `>`/`<` para 50d y para 200d."""
    ctx = _avanzado_context(precio_actual=precio, price_avg_50=avg50, price_avg_200=avg200)
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    cuenta = ai_explain._build_cuenta_line("avanzado", "aqm", datos)
    assert cuenta == (
        f"Precio {ai_explain._money(precio)} {cmp50} promedio 50d {ai_explain._money(avg50)} y "
        f"{cmp200} promedio 200d {ai_explain._money(avg200)} → {datos['momentum']}"
    )


@pytest.mark.parametrize("campo_faltante", ["precio_actual", "price_avg_50", "price_avg_200"])
def test_cuenta_aqm_1_campo_faltante_no_calculable(campo_faltante):
    """QA -- los 3 casos por separado (no solo "todos ausentes")."""
    overrides = {"precio_actual": 150.0, "price_avg_50": 140.0, "price_avg_200": 130.0}
    overrides[campo_faltante] = None
    ctx = _avanzado_context(**overrides)
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    assert ai_explain._build_cuenta_line("avanzado", "aqm", datos) is None


def test_cuenta_aql_beta_bucket():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aql")
    cuenta = ai_explain._build_cuenta_line("avanzado", "aql", datos)
    assert cuenta == "Beta 1.05 está entre 0.80 y 1.20 → bajo"


def test_cuenta_ver_precio_vs_valor_justo():
    # `_texto_libre_context()` fija `veredicto_barata=True` directamente
    # (mismo valor ya calculado por `pillars.precio_razonable`, no
    # recomputado acá) -- la cuenta usa ese veredicto para elegir el
    # operador/etiqueta, nunca vuelve a comparar precio vs. valor justo.
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "ver")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "ver", datos)
    assert cuenta == "Precio actual $550.00 < Valor Justo Total $496.00 → Barata"


def test_cuenta_ver_cara_cuando_veredicto_es_false():
    ctx = _texto_libre_context(veredicto_barata=False)
    datos = ai_explain._build_explain_payload(ctx, "ver")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "ver", datos)
    assert cuenta == "Precio actual $550.00 > Valor Justo Total $496.00 → Cara"


def test_cuenta_vf_suma_de_modelos_calculables():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "vf")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "vf", datos)
    assert cuenta == "($500.00 + $480.00 + $510.00) / 3 = $496.00"


def test_cuenta_gra_con_numeros_reales():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "gra")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "gra", datos)
    assert cuenta == "$8.20 × (8.5 + 2×9.4) × 4.4 / 4.2 = $480.00"


def test_cuenta_mul_con_numeros_reales():
    ctx = _texto_libre_context(peer_comparison={
        "per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
        "per_maximo_peers": 30.0, "peers_usados": ["MSFT", "CRM"],
        "posicion": "en_linea", "motivo_no_comparable": None,
    })
    datos = ai_explain._build_explain_payload(ctx, "mul")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "mul", datos)
    assert cuenta == "$8.20 × 24.00 = $500.00"


def test_cuenta_rat_4_sub_cuentas():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "rat")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "rat", datos)
    assert "Liquidez = $100.00 / $50.00 = 1.80" in cuenta
    assert "Margen bruto = ($1,000.00 − $400.00) / $1,000.00 = 65.0%" in cuenta
    assert "PER = $550.00 / $8.20 = 22.50" in cuenta
    assert "P/S = $2,000,000.00 / $1,000.00 = 6.20" in cuenta


def test_cuenta_rat_solo_las_sub_cuentas_con_dato():
    ctx = _texto_libre_context(current_assets=None, current_liabilities=None)
    datos = ai_explain._build_explain_payload(ctx, "rat")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "rat", datos)
    assert "Liquidez" not in cuenta
    assert "Margen bruto" in cuenta


def test_cuenta_pil_4_criterios_con_numeros():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "pil")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "pil", datos)
    assert "Ingresos: $1,000.00 > $800.00 → creciente" in cuenta
    assert "Utilidades: $200.00 > 0 y > $150.00 → creciente" in cuenta
    assert "Deuda: liquidez 1.80 > 1 → controlada" in cuenta
    assert "Precio: → no razonable" in cuenta


def test_cuenta_rsk_beta_entre_umbrales():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "rsk")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "rsk", datos)
    assert cuenta == "Beta 1.15 está entre 0.80 y 1.20 → perfil Moderado"


def test_cuenta_mom_hasta_4_sub_cuentas():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "mom")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "mom", datos)
    assert "vs. máx. 52 sem." in cuenta
    assert "vs. mín. 52 sem." in cuenta
    assert "vs. promedio 50d" in cuenta
    assert "vs. promedio 200d" in cuenta


def test_cuenta_cmp_per_propio_y_peers():
    # `per_propio` ya viaja pre-calculado en `peer_comparison` (22.5 en el
    # fixture) -- la cuenta lo muestra tal cual, nunca recalcula
    # precio/eps_ttm (esos 2 números son solo para mostrar el armado).
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "cmp")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "cmp", datos)
    assert cuenta == "PER propio = $550.00 / $8.20 = 22.50 — PER promedio peers = 24.00"


# --- DCF -- cuenta PARCIAL (Decisión de diseño #7) -------------------------


def test_cuenta_dcf_wacc_y_g_sustituidos_proyeccion_resumida():
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "dcf")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "dcf", datos)
    assert "FCF base $109.00" in cuenta
    assert "g=8.3%" in cuenta
    assert "WACC=9.1%" in cuenta
    assert "FCF proyectado año 5" in cuenta
    assert "$510.00 por acción." in cuenta
    # No lista los 5 años individualmente -- comportamiento esperado (D1).
    assert "año 1" not in cuenta and "año 2" not in cuenta and "año 3" not in cuenta
    assert cuenta.count("año") == 1


def test_cuenta_dcf_no_calculable_ausente():
    ctx = _texto_libre_context(scenarios={
        "conservador": {"valor_justo_dcf": None, "dcf_wacc": None, "dcf_g_fcf": None},
    })
    datos = ai_explain._build_explain_payload(ctx, "dcf")
    assert ai_explain._build_cuenta_line("texto_libre", "dcf", datos) is None


def test_cuenta_dcf_usa_dcfbreakdown_no_recalcula(monkeypatch):
    """`_build_cuenta_line("texto_libre", "dcf", ...)` no vuelve a llamar
    `calculate_dcf_fair_value` -- lee únicamente del payload ya armado."""
    llamadas = []
    original = ai_explain.valuation.calculate_dcf_fair_value

    def spy(*args, **kwargs):
        llamadas.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ai_explain.valuation, "calculate_dcf_fair_value", spy)
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "dcf")
    ai_explain._build_cuenta_line("texto_libre", "dcf", datos)
    assert llamadas == []


# --- Cuenta entra al payload ANTES del guard (Decisión de diseño #4) ------


async def test_cuenta_entra_a_datos_del_contexto_antes_del_guard():
    """Un número presente SOLO en la cuenta (no en ningún otro campo del
    payload) es aceptado por `_no_new_protected_tokens` si Ollama lo cita."""
    ctx = _avanzado_context()
    respuesta = "El primer término de la cuenta, 0.41, refleja el capital de trabajo."
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:alz")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert respuesta in kwargs["text"]
    assert kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG


async def test_guard_extendido_sigue_rechazando_alucinacion_con_cuenta_presente():
    ctx = _avanzado_context()
    respuesta = "El resultado final es 987654.0%, un número inventado."
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:alz")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert kwargs["text"] == ai_explain.EXPLAIN_UNAVAILABLE_MSG


# --- _MAX_CUENTA_CHARS=400 -- omitir, nunca truncar un número a la mitad
# (mejora recomendada (c) de `security`) -----------------------------------


def test_max_cuenta_chars_cuenta_corta_no_se_toca():
    corta = "Z = 1.2×0.34 = 0.41"
    assert ai_explain._enforce_cuenta_length(corta) == corta


def test_max_cuenta_chars_cuenta_larga_se_omite_no_trunca(caplog):
    larga = "X" * (ai_explain._MAX_CUENTA_CHARS + 1)
    with caplog.at_level(logging.WARNING):
        resultado = ai_explain._enforce_cuenta_length(larga)
    assert resultado is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_build_cuenta_line_omite_si_la_cuenta_construida_excede_el_limite(monkeypatch):
    monkeypatch.setitem(ai_explain._CUENTA_TEXTO_LIBRE, "ver", lambda datos: "Y" * 500)
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "ver")
    assert ai_explain._build_cuenta_line("texto_libre", "ver", datos) is None


def test_build_cuenta_line_atrapa_excepciones_como_no_calculable(monkeypatch):
    def _explota(datos):
        raise KeyError("boom")

    monkeypatch.setitem(ai_explain._CUENTA_TEXTO_LIBRE, "ver", _explota)
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "ver")
    assert ai_explain._build_cuenta_line("texto_libre", "ver", datos) is None


def test_build_cuenta_line_question_code_sin_cuenta_devuelve_none():
    assert ai_explain._build_cuenta_line("texto_libre", "ren", {}) is None
    assert ai_explain._build_cuenta_line("avanzado", "mod", {}) is None


# --- SYSTEM_PROMPT_PASO_A_PASO exclusivo del camino dato_y_paso_a_paso ----


async def test_system_prompt_paso_a_paso_usado_solo_en_dato_y_paso_a_paso():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": json.dumps({"respuesta": "Corto."})})

    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    client = _client_with_handler(handler)
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)
    assert captured["body"]["system"] == ai_explain.SYSTEM_PROMPT_PASO_A_PASO


async def test_system_prompt_explain_usado_en_narrativa():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": json.dumps({"respuesta": "Corto."})})

    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    client = _client_with_handler(handler)
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:ren")
    await callback(update, context)
    assert captured["body"]["system"] == ai_explain.SYSTEM_PROMPT_EXPLAIN


def test_system_prompt_paso_a_paso_pide_json_y_no_recalcular():
    assert "{\"respuesta\"" in ai_explain.SYSTEM_PROMPT_PASO_A_PASO
    assert "cuenta" in ai_explain.SYSTEM_PROMPT_PASO_A_PASO
    assert "YA ESTÁ CALCULADA" in ai_explain.SYSTEM_PROMPT_PASO_A_PASO


# ---------------------------------------------------------------------------
# V. Barrido -- ningún mensaje generado contiene "None"/"null" como si fuera
# un valor calculado (caso de alto riesgo de negocio, criterio de `qa`)
# ---------------------------------------------------------------------------


_NONE_VISIBLE_RE = re.compile(r"\bNone\b|\bnull\b")


@pytest.mark.parametrize("code", _CODES_DATO_Y_PASO_A_PASO)
def test_ver_dato_nunca_muestra_none_ni_con_los_4_modelos_no_calculables(code):
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    if kind == "texto_libre":
        ctx = _texto_libre_context(
            scenarios={"conservador": {}}, ratios={}, risk_fit={}, momentum={}, peer_comparison={},
            pillars={}, veredicto_barata=None,
            current_assets=None, current_liabilities=None, revenue=None, cost_of_revenue=None,
            market_cap=None, eps_ttm=None, y_value=None,
            revenue_reciente=None, revenue_antiguo=None, net_income_reciente=None, net_income_antiguo=None,
            year_high=None, year_low=None, price_avg_50=None, price_avg_200=None,
        )
    else:
        ctx = _avanzado_context(
            altman={"disponible": False, "campos_faltantes": ["x"]},
            altman_pp={"disponible": False, "campos_faltantes": ["x"]},
            magic={"disponible": False, "campos_faltantes": ["x"]},
            piotroski={"puntaje": 0, "criterios_evaluables": 0, "criterios_totales": 9, "criterios": []},
            factors={"value": "no_disponible", "quality": "no_disponible", "momentum": "no_disponible", "low_vol": "no_disponible"},
            roe=None, gross_margin=None, beta=None,
        )
    texto = ai_explain._build_ver_dato_content(ctx, code)
    assert not _NONE_VISIBLE_RE.search(texto), f"'None'/'null' visible para {code}: {texto}"


@pytest.mark.parametrize("code", ["alz", "azp", "mgr", "mge", "pir", "pia", "pie", "pig"])
def test_cuenta_4_modelos_todo_o_nada_no_calculables_sin_none_visible(code):
    ctx = _avanzado_context(
        altman={"disponible": False, "campos_faltantes": ["x"]},
        altman_pp={"disponible": False, "campos_faltantes": ["x"]},
        magic={"disponible": False, "campos_faltantes": ["x"]},
        piotroski={"puntaje": 0, "criterios_evaluables": 0, "criterios_totales": 9, "criterios": []},
    )
    datos = ai_explain._build_explain_payload(ctx, code)
    cuenta = ai_explain._build_cuenta_line("avanzado", code, datos)
    assert cuenta is None


# ---------------------------------------------------------------------------
# V. "🔍 Desglose" -- SDD_desglose_terminos_formula.md
# ---------------------------------------------------------------------------

_CODES_CON_DESGLOSE = (
    "alz", "azp", "pir", "pia", "pie", "mgr", "mge",
    # SDD_desglose_universal.md, Grupo E/F -- 4 factores AQR.
    "aqv", "aqq", "aqm", "aql",
)

# SDD_desglose_valor_justo_total.md [Iter-2] -- "vf" (texto_libre) gana
# desglose propio, distinto del mecanismo genérico de `_CODES_CON_DESGLOSE`
# (que sigue siendo exclusivamente de `/avanzado`, ver los tests de abajo que
# llaman `ai_explain_content.desglose("avanzado", code)`/`_avanzado_context()`
# a propósito). Se mantiene como constante separada, no se mezcla en
# `_CODES_CON_DESGLOSE`, para no romper esos tests avanzado-específicos.
# SDD_desglose_universal.md, Grupos A-D -- 9 preguntas nuevas de texto libre
# (`ver` incluido, con su desglose liviano de 2 términos).
_CODES_CON_DESGLOSE_TEXTO_LIBRE = (
    "vf", "gra", "dcf", "mul", "rat", "pil", "rsk", "mom", "cmp", "ver",
)

# QA, "Fixtures mínimos que faltan" #2 -- lista explícita de las 6
# preguntas sin desglose, confirmada contra el código real (`_TODAS_LAS_
# PREGUNTAS`, 27 codes) en vez de contra el conteo de la spec.
_CODES_SIN_DESGLOSE = tuple(
    c for c in _TODAS_LAS_PREGUNTAS
    if c not in _CODES_CON_DESGLOSE and c not in _CODES_CON_DESGLOSE_TEXTO_LIBRE
)


def test_desglose_6_preguntas_sin_desglose_mas_11_avanzado_mas_10_texto_libre_suman_27():
    """SDD_desglose_universal.md -- tras agregar las 12 preguntas nuevas
    (11 de `/avanzado` + 10 de texto libre, incluyendo "vf"), quedan 6
    preguntas sin desglose (`mod`, `ben`, `ren`, `evt`, `inf`, `pig`)."""
    assert len(_CODES_SIN_DESGLOSE) == 6
    assert len(_CODES_CON_DESGLOSE) == 11
    assert len(_CODES_CON_DESGLOSE_TEXTO_LIBRE) == 10
    todos = set(_CODES_SIN_DESGLOSE) | set(_CODES_CON_DESGLOSE) | set(_CODES_CON_DESGLOSE_TEXTO_LIBRE)
    assert todos == set(_TODAS_LAS_PREGUNTAS)


def test_desglose_avanzado_11_entradas_exactas():
    assert set(ai_explain_content.DESGLOSE_AVANZADO) == set(_CODES_CON_DESGLOSE)


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
def test_desglose_entrada_sin_terminos_vacios_ni_none(code):
    """Ningún término del bloque tiene `letra`/`campo_origen`/`nombre`/
    `que_mide` vacío, `None`, o la palabra literal "None" visible."""
    terminos = ai_explain_content.desglose("avanzado", code)
    assert terminos, f"{code} debería tener desglose"
    for t in terminos:
        for campo in (t.letra, t.campo_origen, t.nombre, t.que_mide):
            assert campo, f"{code}: campo vacío en {t!r}"
            assert "None" not in campo, f"{code}: 'None' visible en {t!r}"


def test_desglose_azp_mismas_a_d_que_alz_sin_e():
    alz = ai_explain_content.desglose("avanzado", "alz")
    azp = ai_explain_content.desglose("avanzado", "azp")
    assert len(alz) == 5
    assert len(azp) == 4
    assert azp == alz[:4]


def test_desglose_pig_no_tiene_desglose_propio():
    """Decisión de diseño #1 -- test dedicado: `pig` se apoya en pir/pia/pie,
    no gana un desglose propio por simetría. Sin este test, un futuro
    `implementer` podría "completar" `pig` por simetría y violar la
    decisión de diseño."""
    assert ai_explain_content.desglose("avanzado", "pig") == ()


@pytest.mark.parametrize("code", ["xyz", "", "zzz"])
def test_desglose_code_inexistente_o_vacio_devuelve_vacio(code):
    assert ai_explain_content.desglose("avanzado", code) == ()


@pytest.mark.parametrize(
    "code",
    [
        c for c in list(_TODAS_LAS_PREGUNTAS) + ["alz", "cualquier_cosa"]
        if c not in _CODES_CON_DESGLOSE_TEXTO_LIBRE
    ],
)
def test_desglose_texto_libre_vacio_salvo_los_10_con_desglose(code):
    """`kind == "texto_libre"` devuelve `()` para cualquier `code` fuera de
    `_CODES_CON_DESGLOSE_TEXTO_LIBRE` (10 preguntas: "vf" +
    SDD_desglose_universal.md, Grupos A-D), incluso uno que sí tiene
    entrada en `DESGLOSE_AVANZADO` (ej. "alz") -- ningún `code` de "avanzado"
    se filtra por error a la tabla de texto_libre."""
    assert ai_explain_content.desglose("texto_libre", code) == ()


def test_desglose_texto_libre_vf_no_vacio():
    """"vf" es la única entrada de `DESGLOSE_TEXTO_LIBRE` con sub-cuentas
    anidadas -- 3 términos (Múltiplos/Graham/DCF), en ese orden."""
    terminos = ai_explain_content.desglose("texto_libre", "vf")
    assert [t.letra for t in terminos] == ["Múltiplos", "Graham", "DCF"]


def test_desglose_texto_libre_ver_2_terminos_livianos():
    """SDD_desglose_universal.md, Grupo D -- "ver" tiene exactamente 2
    `DesgloseTermino` (no 3, no un objeto anidado como "vf"), y ninguno de
    los 2 `campo_origen` menciona los escenarios de "vf" -- evita que una
    futura edición copie por error el patrón de sub-cuentas de "vf" dentro
    de "ver"."""
    terminos = ai_explain_content.desglose("texto_libre", "ver")
    assert len(terminos) == 2
    assert [t.letra for t in terminos] == ["Precio actual", "Valor Justo Total"]
    for t in terminos:
        for palabra in ("escenario", "conservador", "agresivo", "optimista"):
            assert palabra not in t.campo_origen.lower()


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
def test_build_desglose_block_happy_path_sin_terminos_faltantes(code):
    datos = ai_explain._build_explain_payload(_avanzado_context(), code)
    bloque = ai_explain._build_desglose_block("avanzado", code, datos)
    assert bloque is not None
    assert bloque.startswith("🔍 Desglose:\n")
    assert "None" not in bloque
    terminos = ai_explain_content.desglose("avanzado", code)
    for t in terminos:
        assert t.letra in bloque
        assert t.nombre in bloque


@pytest.mark.parametrize("code", _CODES_SIN_DESGLOSE)
def test_build_desglose_block_none_para_las_6_preguntas_sin_desglose(code):
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    assert ai_explain._build_desglose_block(kind, code, {}) is None


def test_build_leaf_message_inserta_desglose_entre_cuenta_y_respuesta():
    texto = ai_explain._build_leaf_message(
        "Dato de prueba", "Respuesta de Ollama.", None, None,
        cuenta="Z = 1.2×0.34 = 0.41", desglose="🔍 Desglose:\n• A (Capital de Trabajo) — sale de X. Mide Y.",
    )
    idx_cuenta = texto.index("🧮 Cuenta")
    idx_desglose = texto.index("🔍 Desglose")
    idx_respuesta = texto.index("Respuesta de Ollama.")
    assert idx_cuenta < idx_desglose < idx_respuesta


def test_build_leaf_message_sin_desglose_no_agrega_seccion():
    texto = ai_explain._build_leaf_message(
        "Dato", "Respuesta.", None, None, cuenta="Z = 0.41", desglose=None,
    )
    assert "🔍 Desglose" not in texto


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
async def test_mensaje_paso_a_paso_muestra_cuenta_y_desglose_en_orden(code):
    """Caso obligatorio de QA -- Happy path: para cada una de las 11
    preguntas de `/avanzado` con desglose, el mensaje completo de "Explicame
    paso a paso" muestra "🧮 Cuenta" seguido de "🔍 Desglose", ambos antes de
    la respuesta de Ollama."""
    ctx = _avanzado_context(
        # SDD_desglose_universal.md, Grupo F -- "aqm" necesita estos 3
        # campos para que su Cuenta/Desglose no sean `None`.
        precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0,
    )
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "Este modelo mide la salud financiera de la empresa en base a varios factores."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:{code}")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    assert texto != ai_explain.EXPLAIN_UNAVAILABLE_MSG
    assert "🧮 Cuenta" in texto
    assert "🔍 Desglose" in texto
    assert texto.index("🧮 Cuenta") < texto.index("🔍 Desglose") < texto.index(respuesta)
    assert "None" not in texto


@pytest.mark.parametrize(
    "code", [c for c in _CODES_CON_DESGLOSE_TEXTO_LIBRE if c != "vf"],
)
async def test_mensaje_paso_a_paso_texto_libre_muestra_cuenta_y_desglose_en_orden(code):
    """Caso obligatorio de QA -- Happy path: para cada una de las 9
    preguntas nuevas de texto libre con desglose (Grupos A-D), el mensaje
    completo de "Explicame paso a paso" muestra "🧮 Cuenta" seguido de "🔍
    Desglose", ambos antes de la respuesta de Ollama."""
    ctx = _texto_libre_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "Este modelo mide la valoración de la empresa en base a varios factores."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:{code}")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    assert texto != ai_explain.EXPLAIN_UNAVAILABLE_MSG
    assert "🧮 Cuenta" in texto
    assert "🔍 Desglose" in texto
    assert texto.index("🧮 Cuenta") < texto.index("🔍 Desglose") < texto.index(respuesta)
    assert "None" not in texto


@pytest.mark.parametrize("code", _CODES_SIN_DESGLOSE)
async def test_mensaje_paso_a_paso_sin_desglose_para_las_6_preguntas_regresion(code):
    """Regresión dirigida (QA, "Regression testing dirigida") -- las 6
    preguntas sin desglose no muestran la sección nueva. Contexto avanzado
    con todos los campos para los `question_code` de avanzado, texto_libre
    para el resto."""
    ctx = _avanzado_context() if code in ai_explain_content.QUESTIONS_AVANZADO else _texto_libre_context()
    spec = ai_explain_content.all_questions(ctx.kind)[code]
    if spec.variant == ai_explain_content.VARIANT_DETERMINISTICO:
        pytest.skip("evt/inf nunca llaman a Ollama -- sin mensaje 'paso a paso' que comparar")
    # `narrativa` (mod/ben/ren) usa el callback leaf `xp:{id}:{code}` (sin
    # `:p:`) -- no tiene 2º botón "paso a paso", es su único camino a Ollama.
    sufijo = code if spec.variant == ai_explain_content.VARIANT_NARRATIVA else f"p:{code}"
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "Explicación genérica sin números inventados."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:{sufijo}")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert "🔍 Desglose" not in kwargs["text"]


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
async def test_ver_dato_nunca_incluye_desglose_ni_para_las_11_preguntas_avanzado_con_desglose(code):
    """El botón "📊 Ver dato" no cambia (decisión ya resuelta: el desglose
    queda solo en "Explicame paso a paso") -- verificado explícitamente
    contra las 11 preguntas de `/avanzado` que SÍ tienen desglose, el caso
    de mayor riesgo de regresión por ser justo donde "podría" agregarse por
    error."""
    ctx = _avanzado_context()
    contenido = ai_explain._build_ver_dato_content(ctx, code)
    assert "🔍 Desglose" not in contenido
    assert "🧮 Cuenta" not in contenido


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE_TEXTO_LIBRE)
async def test_ver_dato_nunca_incluye_desglose_ni_para_las_10_preguntas_texto_libre_con_desglose(code):
    """Mismo criterio que arriba, para las 10 preguntas de texto libre con
    desglose (SDD_desglose_universal.md, Grupos A-D + "vf")."""
    ctx = _texto_libre_context()
    contenido = ai_explain._build_ver_dato_content(ctx, code)
    assert "🔍 Desglose" not in contenido
    assert "🧮 Cuenta" not in contenido


# --- _MAX_DESGLOSE_CHARS=1200 -- omitir, nunca truncar (Decisión de
# diseño #6) --------------------------------------------------------------


def test_max_desglose_chars_tope_exacto_1200_no_se_omite():
    bloque_1200 = "X" * ai_explain._MAX_DESGLOSE_CHARS
    assert ai_explain._enforce_desglose_length(bloque_1200) == bloque_1200


def test_max_desglose_chars_1201_se_omite_completo_no_trunca(caplog):
    bloque_1201 = "X" * (ai_explain._MAX_DESGLOSE_CHARS + 1)
    with caplog.at_level(logging.WARNING):
        resultado = ai_explain._enforce_desglose_length(bloque_1201)
    assert resultado is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_desglose_avanzado_11_entradas_todas_bajo_el_tope():
    """Test parametrizado en el sentido que pide QA: recorre las 11
    entradas y hace un `assert` explícito por entrada, para que el mensaje
    de fallo señale cuál entrada se pasó del límite, sin depurar las 11."""
    ctx = _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    for code, terminos in ai_explain_content.DESGLOSE_AVANZADO.items():
        datos = ai_explain._build_explain_payload(ctx, code)
        bloque = ai_explain._build_desglose_block("avanzado", code, datos)
        assert bloque is not None, f"{code}: desglose se omitió (excede el tope)"
        assert len(bloque) <= ai_explain._MAX_DESGLOSE_CHARS, (
            f"{code}: desglose de {len(bloque)} caracteres excede "
            f"_MAX_DESGLOSE_CHARS={ai_explain._MAX_DESGLOSE_CHARS}"
        )


def test_desglose_texto_libre_9_entradas_nuevas_todas_bajo_el_tope():
    """Mismo criterio que arriba, para las 9 preguntas nuevas de texto libre
    (Grupos A-D) -- "vf" queda afuera porque usa su propio mecanismo de
    sub-cuentas anidadas (`_build_desglose_vf`), ya cubierto por sus tests
    dedicados."""
    ctx = _texto_libre_context()
    for code in (c for c in _CODES_CON_DESGLOSE_TEXTO_LIBRE if c != "vf"):
        datos = ai_explain._build_explain_payload(ctx, code)
        bloque = ai_explain._build_desglose_block("texto_libre", code, datos)
        assert bloque is not None, f"{code}: desglose se omitió (excede el tope)"
        assert len(bloque) <= ai_explain._MAX_DESGLOSE_CHARS, (
            f"{code}: desglose de {len(bloque)} caracteres excede "
            f"_MAX_DESGLOSE_CHARS={ai_explain._MAX_DESGLOSE_CHARS}"
        )


# ---------------------------------------------------------------------------
# VI. "🔍 Desglose" con valores reales -- SDD_desglose_con_valores_reales.md
# ---------------------------------------------------------------------------


def test_build_desglose_block_es_funcion_pura_sin_io():
    """Mismo criterio de testabilidad que `_build_cuenta_line`/
    `_build_dato_line` -- callable directo, sin `await`, sin mockear nada."""
    import inspect
    assert not inspect.iscoroutinefunction(ai_explain._build_desglose_block)


def test_valor_desglose_alz_termino_a_termino_ejemplo_de_daniela():
    """Ejemplo textual EXACTO del Estado objetivo de la spec."""
    datos = {"altman": {"disponible": True, "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": 0.87}}
    assert ai_explain._valor_desglose_alz("A", datos) == "0.34"
    assert ai_explain._valor_desglose_alz("B", datos) == "0.12"
    assert ai_explain._valor_desglose_alz("C", datos) == "0.18"
    assert ai_explain._valor_desglose_alz("D", datos) == "1.05"
    assert ai_explain._valor_desglose_alz("E", datos) == "0.87"

    bloque = ai_explain._build_desglose_block("avanzado", "alz", datos)
    assert "• A (Capital de Trabajo) = 0.34 — sale de" in bloque
    assert "• E (Rotación de Activos) = 0.87 — sale de" in bloque


def test_valor_desglose_azp_termino_a_termino():
    datos = {"altman_pp": {"disponible": True, "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": None}}
    assert ai_explain._valor_desglose_azp("A", datos) == "0.34"
    assert ai_explain._valor_desglose_azp("B", datos) == "0.12"
    assert ai_explain._valor_desglose_azp("C", datos) == "0.18"
    assert ai_explain._valor_desglose_azp("D", datos) == "1.05"

    bloque = ai_explain._build_desglose_block("avanzado", "azp", datos)
    assert "• A (Capital de Trabajo) = 0.34 — sale de" in bloque
    # `azp` no usa E -- no debería aparecer una línea de E en absoluto.
    assert "(Rotación de Activos)" not in bloque


@pytest.mark.parametrize(
    "code,letra,nombre_criterio,esperado",
    [
        ("pir", "ROA positivo", "roa_positivo", "✅ Cumple"),
        ("pir", "CFO positivo", "cfo_positivo", "✅ Cumple"),
        ("pir", "ROA creciente", "roa_creciente", "✅ Cumple"),
        ("pir", "CFO > Utilidad", "cfo_mayor_utilidad", "❌ No cumple"),
        ("pia", "Apalancamiento decreciente", "apalancamiento_decreciente", "✅ Cumple"),
        ("pia", "Liquidez creciente", "liquidez_creciente", "❌ No cumple"),
        ("pia", "Sin dilución", "sin_dilucion", "✅ Cumple"),
        ("pie", "Margen bruto creciente", "margen_bruto_creciente", "✅ Cumple"),
        ("pie", "Rotación de activos creciente", "rotacion_activos_creciente", "❌ No cumple"),
    ],
)
def test_valor_desglose_piotroski_las_9_letras_contra_fixture_avanzado(code, letra, nombre_criterio, esperado):
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    assert ai_explain._valor_desglose_piotroski(letra, datos) == esperado

    bloque = ai_explain._build_desglose_block("avanzado", code, datos)
    assert f"{letra} = {esperado}" in bloque or f"({ai_explain_content.desglose('avanzado', code)[0].nombre})" in bloque


def test_valor_desglose_piotroski_no_evaluable_cumplido_none():
    datos = {"criterios": [{"nombre": "roa_positivo", "cumplido": None, "valores": None}]}
    assert ai_explain._valor_desglose_piotroski("ROA positivo", datos) == "➖ No evaluable"

    bloque = ai_explain._build_desglose_block("avanzado", "pir", datos)
    assert "➖ No evaluable" in bloque
    assert "None" not in bloque


def test_valor_desglose_piotroski_criterio_ausente_del_payload_sin_valor():
    """Caso de error obligatorio de QA -- ni `KeyError` ni `AttributeError`,
    ni tampoco 'None' visible; la letra sale sin `= valor`."""
    datos = {"criterios": []}
    assert ai_explain._valor_desglose_piotroski("ROA positivo", datos) is None

    bloque = ai_explain._build_desglose_block("avanzado", "pir", datos)
    assert bloque is not None
    assert "None" not in bloque
    assert " = " not in bloque.split("\n")[1]  # primera línea de término, sin segmento de valor


def test_valor_desglose_piotroski_letra_desconocida_sin_mapeo():
    """100% branch coverage (QA) -- letra sin entrada en
    `_DESGLOSE_LETRA_A_NOMBRE_CRITERIO` no revienta, devuelve `None`."""
    datos = {"criterios": [{"nombre": "roa_positivo", "cumplido": True, "valores": {}}]}
    assert ai_explain._valor_desglose_piotroski("Letra inexistente", datos) is None


def test_valor_desglose_piotroski_ignora_entradas_no_dict_en_criterios():
    """100% branch coverage (QA) -- una entrada no-dict en `criterios` no
    rompe el filtro `isinstance(c, dict)`."""
    datos = {"criterios": ["esto-no-es-un-dict", {"nombre": "roa_positivo", "cumplido": True, "valores": {}}]}
    assert ai_explain._valor_desglose_piotroski("ROA positivo", datos) == "✅ Cumple"


def test_valor_desglose_mgr_letra_desconocida_sin_mapeo():
    """100% branch coverage (QA) -- letra fuera de las 3 conocidas de `mgr`
    devuelve `None` sin reventar."""
    assert ai_explain._valor_desglose_mgr("Letra inexistente", {"ebit": 1.0}) is None


def test_valor_desglose_mgr_cada_sub_rama_con_dato_ausente():
    """100% branch coverage (QA) -- cada una de las 3 letras de `mgr` con su
    campo faltante devuelve `None` (rama negativa de cada `if`)."""
    assert ai_explain._valor_desglose_mgr("EBIT", {}) is None
    assert ai_explain._valor_desglose_mgr("Capital de Trabajo Neto", {"current_assets": 1.0}) is None
    assert ai_explain._valor_desglose_mgr("Capital de Trabajo Neto", {"current_liabilities": 1.0}) is None
    assert ai_explain._valor_desglose_mgr("Activos Fijos Netos", {}) is None


def test_valor_desglose_mge_letra_desconocida_sin_mapeo():
    assert ai_explain._valor_desglose_mge("Letra inexistente", {"ebit": 1.0}) is None


def test_valor_desglose_alz_letra_desconocida_y_altman_no_disponible():
    assert ai_explain._valor_desglose_alz("Z", {"altman": {"disponible": True, "a": 0.34}}) is None
    assert ai_explain._valor_desglose_alz("A", {"altman": {"disponible": False}}) is None


def test_valor_desglose_azp_letra_desconocida_y_altman_pp_no_disponible():
    assert ai_explain._valor_desglose_azp("E", {"altman_pp": {"disponible": True, "a": 0.34}}) is None
    assert ai_explain._valor_desglose_azp("A", {"altman_pp": {"disponible": False}}) is None


def test_valor_desglose_mgr_termino_a_termino():
    datos = {"ebit": 114_000.0, "current_assets": 650_000.0, "current_liabilities": 150_000.0, "ppe_net": 70_000.0}
    assert ai_explain._valor_desglose_mgr("EBIT", datos) == "$114,000.00"
    assert ai_explain._valor_desglose_mgr("Capital de Trabajo Neto", datos) == "$500,000.00"
    assert ai_explain._valor_desglose_mgr("Activos Fijos Netos", datos) == "$70,000.00"

    bloque = ai_explain._build_desglose_block("avanzado", "mgr", datos)
    assert "• EBIT (EBIT (Ganancia antes de Intereses e Impuestos)) = $114,000.00" in bloque
    assert "• Capital de Trabajo Neto (Capital de Trabajo Neto) = $500,000.00" in bloque
    assert "• Activos Fijos Netos (Activos Fijos Netos (PP&E)) = $70,000.00" in bloque


def test_valor_desglose_mgr_capital_de_trabajo_negativo_se_muestra_tal_cual():
    """Alto riesgo de negocio (QA) -- capital de trabajo negativo no se
    omite ni se muestra en valor absoluto."""
    datos = {"ebit": 114_000.0, "current_assets": 100_000.0, "current_liabilities": 150_000.0, "ppe_net": 70_000.0}
    # `_money` (mismo formateador que ya usa "🧮 Cuenta") antepone el signo
    # de moneda al signo del número -- "$-50,000.00", no "-$50,000.00".
    assert ai_explain._valor_desglose_mgr("Capital de Trabajo Neto", datos) == "$-50,000.00"


def test_valor_desglose_mge_termino_a_termino():
    datos = {"ebit": 114_000.0, "market_cap": 1_400_000.0, "total_debt": 100_000.0, "cash": 75_000.0}
    assert ai_explain._valor_desglose_mge("EBIT", datos) == "$114,000.00"
    assert ai_explain._valor_desglose_mge("Capitalización de Mercado", datos) == "$1,400,000.00"
    assert ai_explain._valor_desglose_mge("Deuda Total", datos) == "$100,000.00"
    assert ai_explain._valor_desglose_mge("Efectivo", datos) == "$75,000.00"

    bloque = ai_explain._build_desglose_block("avanzado", "mge", datos)
    assert "$114,000.00" in bloque
    assert "$1,400,000.00" in bloque
    assert "$100,000.00" in bloque
    assert "$75,000.00" in bloque


@pytest.mark.parametrize("code", ["alz", "azp", "mgr", "mge"])
def test_build_desglose_block_caso_limite_cero_real_no_ausente(code):
    """Caso límite obligatorio de QA -- un término en 0.0 real debe seguir
    mostrando `= 0.00`/`$0.00`, nunca omitirse por falsy-check."""
    if code == "alz":
        datos = {"altman": {"disponible": True, "a": 0.0, "b": 0.12, "c": 0.18, "d": 1.05, "e": 0.87}}
        bloque = ai_explain._build_desglose_block("avanzado", code, datos)
        assert "• A (Capital de Trabajo) = 0.00 —" in bloque
    elif code == "azp":
        datos = {"altman_pp": {"disponible": True, "a": 0.0, "b": 0.12, "c": 0.18, "d": 1.05, "e": None}}
        bloque = ai_explain._build_desglose_block("avanzado", code, datos)
        assert "• A (Capital de Trabajo) = 0.00 —" in bloque
    elif code == "mgr":
        datos = {"ebit": 0.0, "current_assets": 150_000.0, "current_liabilities": 150_000.0, "ppe_net": 70_000.0}
        bloque = ai_explain._build_desglose_block("avanzado", code, datos)
        assert "• EBIT (EBIT (Ganancia antes de Intereses e Impuestos)) = $0.00" in bloque
        assert "• Capital de Trabajo Neto (Capital de Trabajo Neto) = $0.00" in bloque
    else:  # mge
        datos = {"ebit": 0.0, "market_cap": 1_400_000.0, "total_debt": 100_000.0, "cash": 75_000.0}
        bloque = ai_explain._build_desglose_block("avanzado", code, datos)
        assert "= $0.00" in bloque


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
def test_build_desglose_block_modelo_no_disponible_lineas_sin_valor(code):
    """Caso de error obligatorio de QA -- `disponible=False` no rompe el
    bloque: cada línea se muestra sin el segmento `= valor` (distinto del
    comportamiento de "🧮 Cuenta", que omite la línea/bloque completo)."""
    if code in ("alz",):
        datos = {"altman": {"disponible": False, "campos_faltantes": ["ebit"]}}
    elif code in ("azp",):
        datos = {"altman_pp": {"disponible": False, "campos_faltantes": ["ebit"]}}
    elif code in ("pir", "pia", "pie"):
        datos = {"criterios": []}
    else:  # mgr, mge
        datos = {}
    bloque = ai_explain._build_desglose_block("avanzado", code, datos)
    assert bloque is not None, f"{code}: el bloque no debería omitirse por dato faltante"
    assert "None" not in bloque
    # Ninguna línea trae el segmento de valor `f" = {valor}"` -- ese
    # segmento va siempre pegado al `)` que cierra el nombre del término
    # (`") = "`); un `" = "` suelto puede venir del propio `que_mide` fijo
    # (ej. "aql": "1.0 = igual de volátil"), no es un valor sustituido.
    assert ") = " not in bloque
    terminos = ai_explain_content.desglose("avanzado", code)
    for t in terminos:
        assert f"• {t.letra} ({t.nombre}) — sale de" in bloque


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
def test_build_desglose_block_ningun_none_visible_en_ningun_caso(code):
    """Cubre transversalmente el criterio 'ningún "None" visible' con
    `datos={}` (peor caso: ningún campo presente para ninguna pregunta)."""
    bloque = ai_explain._build_desglose_block("avanzado", code, {})
    assert bloque is not None
    assert "None" not in bloque


def test_build_desglose_block_extractor_con_dato_malformado_no_propaga_excepcion():
    """Caso de error obligatorio de QA -- un `datos` con tipos inválidos no
    debe propagar la excepción del extractor: la línea sale sin `= valor`,
    el resto del bloque se arma normal (misma red de seguridad amplia que
    ya usa `_build_cuenta_line`)."""
    datos = {"altman": {"disponible": True, "a": "no-es-numero", "b": 0.12, "c": 0.18, "d": 1.05, "e": 0.87}}
    bloque = ai_explain._build_desglose_block("avanzado", "alz", datos)
    assert bloque is not None
    assert "None" not in bloque
    # La letra A queda sin valor (el `_ratio2` sobre un string revienta,
    # capturado por el try/except) pero el resto de las letras sigue con
    # su valor real.
    assert "• A (Capital de Trabajo) —" in bloque  # sin "= valor"
    assert "• B (Utilidades Retenidas) = 0.12 —" in bloque


def test_piotroski_letras_desglose_alineadas_con_mapeo_interno():
    """Mejora recomendada de `security`, incorporada como criterio de
    aceptación obligatorio -- las 9 letras que expone `DESGLOSE_AVANZADO`
    para pir/pia/pie deben mapear, todas, a uno de los 9 nombres canónicos
    de criterio de Piotroski que usa `_PIOTROSKI_CUENTA_LABEL` (fuente
    existente, no una lista nueva inventada en el test)."""
    nombres_canonicos = set(ai_explain._PIOTROSKI_CUENTA_LABEL)
    letras_piotroski = [
        t.letra
        for code in ("pir", "pia", "pie")
        for t in ai_explain_content.desglose("avanzado", code)
    ]
    assert len(letras_piotroski) == 9
    for letra in letras_piotroski:
        nombre = ai_explain._DESGLOSE_LETRA_A_NOMBRE_CRITERIO.get(letra)
        assert nombre is not None, f"letra '{letra}' sin entrada en _DESGLOSE_LETRA_A_NOMBRE_CRITERIO"
        assert nombre in nombres_canonicos, (
            f"letra '{letra}' mapea a '{nombre}', que no es un nombre canónico de criterio Piotroski"
        )
    # Y a la inversa -- el mapeo no tiene entradas huérfanas sin letra real.
    assert set(ai_explain._DESGLOSE_LETRA_A_NOMBRE_CRITERIO) == set(letras_piotroski)


def test_build_desglose_block_datos_no_es_input_de_ai_rewrite():
    """El guard de integridad (`ai_rewrite.protected_tokens`) sigue actuando
    ÚNICAMENTE sobre `datos_del_contexto`, nunca sobre el resultado de
    `_build_desglose_block` -- aserción estructural sobre el orden real de
    llamadas en `_dispatch_leaf` y sobre la firma de `_fetch_explanation`
    (sin mockear nada)."""
    import inspect

    firma_fetch = inspect.signature(ai_explain._fetch_explanation)
    assert "desglose" not in firma_fetch.parameters

    fuente = inspect.getsource(ai_explain._dispatch_leaf)
    idx_fetch = fuente.index("_fetch_explanation(")
    idx_desglose = fuente.index("_build_desglose_block(")
    assert idx_fetch < idx_desglose, (
        "_build_desglose_block debe llamarse DESPUÉS de _fetch_explanation "
        "-- nunca entra al prompt de Ollama"
    )


# --- Consistencia "🧮 Cuenta" vs. "🔍 Desglose" (punto explícito del pedido
# de Daniela) -- mismo `datos`, mismo string formateado para el mismo
# término, en el mismo test. ------------------------------------------------


def test_consistencia_cuenta_y_desglose_alz_mismo_valor_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "alz")
    cuenta = ai_explain._build_cuenta_line("avanzado", "alz", datos)
    desglose = ai_explain._build_desglose_block("avanzado", "alz", datos)
    assert "1.2×0.34" in cuenta  # término A de la Cuenta
    assert "• A (Capital de Trabajo) = 0.34 —" in desglose  # mismo A en el Desglose


def test_consistencia_cuenta_y_desglose_azp_mismo_valor_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "azp")
    cuenta = ai_explain._build_cuenta_line("avanzado", "azp", datos)
    desglose = ai_explain._build_desglose_block("avanzado", "azp", datos)
    assert "6.56×0.34" in cuenta
    assert "• A (Capital de Trabajo) = 0.34 —" in desglose


@pytest.mark.parametrize("code", ["pir", "pia", "pie"])
def test_consistencia_cuenta_y_desglose_piotroski_mismo_cumplido(code):
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    cuenta = ai_explain._build_cuenta_line("avanzado", code, datos)
    desglose = ai_explain._build_desglose_block("avanzado", code, datos)
    # El primer criterio de la Cuenta (cumplido=True en la fixture) debe
    # coincidir en sentido con el ✅/❌ del Desglose para el mismo criterio.
    assert "cumplido" in cuenta
    assert "✅ Cumple" in desglose or "❌ No cumple" in desglose


def test_consistencia_cuenta_y_desglose_mgr_capital_invertido_igual_a_suma_de_componentes():
    """Para `mgr` el criterio de consistencia es aritmético, no textual:
    `current_assets - current_liabilities` (Desglose) + `ppe_net`
    (Desglose) debe sumar exactamente el `capital_invertido` que usa
    "🧮 Cuenta", con la misma fixture."""
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "mgr")
    cuenta = ai_explain._build_cuenta_line("avanzado", "mgr", datos)
    assert "$570,000.00" in cuenta  # capital_invertido de la fixture

    ca, cl, ppe = datos["current_assets"], datos["current_liabilities"], datos["ppe_net"]
    assert (ca - cl) + ppe == pytest.approx(datos["capital_invertido"])

    desglose = ai_explain._build_desglose_block("avanzado", "mgr", datos)
    assert "Capital de Trabajo Neto (Capital de Trabajo Neto) = $500,000.00" in desglose
    assert "Activos Fijos Netos (Activos Fijos Netos (PP&E)) = $70,000.00" in desglose


def test_consistencia_cuenta_y_desglose_mge_mismos_4_valores():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "mge")
    cuenta = ai_explain._build_cuenta_line("avanzado", "mge", datos)
    desglose = ai_explain._build_desglose_block("avanzado", "mge", datos)
    for valor in ("$114,000.00", "$1,400,000.00", "$100,000.00", "$75,000.00"):
        assert valor in cuenta
        assert valor in desglose


@pytest.mark.parametrize("code", _CODES_CON_DESGLOSE)
async def test_mensaje_paso_a_paso_desglose_muestra_valores_reales_no_solo_texto_fijo(code):
    """Happy path por pregunta (11 casos, QA) -- en el mensaje real de
    "Explicame paso a paso" el bloque de Desglose ya no es 100% texto fijo:
    contiene al menos un valor real del ticker."""
    ctx = _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "Explicación genérica sin números inventados."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:{code}")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    idx_desglose = texto.index("🔍 Desglose")
    bloque_desglose = texto[idx_desglose:texto.index(respuesta)]
    assert " = " in bloque_desglose, f"{code}: el Desglose no muestra ningún valor real"
    assert "None" not in bloque_desglose


# ---------------------------------------------------------------------------
# VII. "🔍 Desglose" de "vf" (💰 Valor Justo Total) --
# SDD_desglose_valor_justo_total.md [Iter-2]
# ---------------------------------------------------------------------------


def _vf_context(escenario_overrides: dict, *, escenario_elegido: str = "conservador", **top_overrides):
    """Fixture base de "vf": parte de `_texto_libre_context()` (ADBE,
    escenario "conservador" con Múltiplos $500.00/Graham $480.00/DCF
    $510.00/Total $496.00 por default) y permite pisar el sub-dict del
    escenario elegido (para forzar "no calculable" en 1/2/3 sub-modelos)
    y/o campos de nivel superior."""
    ctx_base = _texto_libre_context()
    scenarios = copy.deepcopy(ctx_base.scenarios)
    scenarios[escenario_elegido].update(escenario_overrides)
    overrides = {"scenarios": scenarios, "escenario_elegido": escenario_elegido, **top_overrides}
    return _texto_libre_context(**overrides)


def _desglose_vf(ctx: ai_explain.ExplanationContext) -> str:
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    return bloque


# --- Fixtures mínimos que pide QA (Ticker A-E + escenario variable) --------


def test_build_desglose_vf_ticker_a_3_modelos_calculables_3_subsecciones_en_orden():
    """Ticker A -- happy path, las 3 sub-secciones en orden Múltiplos ->
    Graham -> DCF, cada una con valor entre paréntesis, 1 línea de "qué
    mide" y su cuenta resuelta completa."""
    ctx = _texto_libre_context()
    bloque = _desglose_vf(ctx)
    assert bloque.startswith("🔍 Desglose:\n")
    idx_mul = bloque.index("• Múltiplos")
    idx_gra = bloque.index("• Graham (EPS)")
    idx_dcf = bloque.index("• DCF (Flujo de Caja Descontado)")
    assert idx_mul < idx_gra < idx_dcf
    assert "• Múltiplos ($500.00) — cuánto debería valer la acción si cotizara" in bloque
    assert "Cuenta: $8.20 × 24.00 = $500.00" in bloque
    assert "• Graham (EPS) ($480.00) — cuánto debería valer la acción según" in bloque
    assert "Cuenta: $8.20 × (8.5 + 2×9.4) × 4.4 / 4.2 = $480.00" in bloque
    assert "• DCF (Flujo de Caja Descontado) ($510.00) — cuánto vale la empresa hoy" in bloque
    assert "Cuenta: FCF base $109.00" in bloque
    assert "no calculable" not in bloque
    assert "None" not in bloque


@pytest.mark.parametrize(
    "code,nombre",
    [("mul", "Múltiplos"), ("gra", "Graham (EPS)"), ("dcf", "DCF (Flujo de Caja Descontado)")],
)
def test_build_desglose_vf_cuenta_identica_byte_a_byte_al_boton_individual(code, nombre):
    """Caso obligatorio de QA -- la sub-cuenta de cada modelo dentro del
    Desglose de "vf" tiene que ser EXACTAMENTE la misma cuenta (comparación
    de string completa) que arma hoy el botón individual («Múltiplos»/
    «Graham»/«DCF») con el mismo `_payload_texto_libre`/`_cuenta_*`."""
    ctx = _texto_libre_context()
    bloque = _desglose_vf(ctx)
    esperado = ai_explain._CUENTA_TEXTO_LIBRE[code](ai_explain._payload_texto_libre(ctx, code))
    assert esperado is not None
    assert f"• {nombre}" in bloque
    assert f"Cuenta: {esperado}" in bloque


def test_build_desglose_vf_valor_entre_parentesis_igual_a_valor_de_cuenta_vf():
    """Caso obligatorio de QA -- el número entre paréntesis de cada
    sub-sección (`_valor_desglose_vf_de_datos`, que lee de `datos_vf`) debe
    coincidir con el número correspondiente que ya muestra la "🧮 Cuenta" de
    "vf" (`_cuenta_vf`, mismo `datos_vf`)."""
    ctx = _texto_libre_context()
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    cuenta_vf = ai_explain._cuenta_vf(datos_vf)
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert cuenta_vf == "($500.00 + $480.00 + $510.00) / 3 = $496.00"
    assert "($500.00)" in bloque
    assert "($480.00)" in bloque
    assert "($510.00)" in bloque


def test_build_desglose_vf_ticker_b_multiplos_no_calculable():
    """Ticker B -- 1 modelo no calculable (Múltiplos, escenario elegido sin
    valor_justo_multiplos): la línea se reemplaza completa por el texto
    explícito, nunca desaparece, nunca muestra "None" ni un valor
    inventado. Graham y DCF siguen con su cuenta completa. La Cuenta de
    "vf" refleja el promedio de los 2 calculables."""
    ctx = _vf_context({"valor_justo_multiplos": None, "valor_justo_total": (480.00 + 510.00) / 2})
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    assert "• Múltiplos — no calculable con los datos disponibles." in bloque
    assert "• Graham (EPS) ($480.00) —" in bloque
    assert "Cuenta: $8.20 × (8.5 + 2×9.4) × 4.4 / 4.2 = $480.00" in bloque
    assert "• DCF (Flujo de Caja Descontado) ($510.00) —" in bloque
    assert "None" not in bloque
    cuenta_vf = ai_explain._cuenta_vf(datos_vf)
    assert cuenta_vf == "($480.00 + $510.00) / 2 = $495.00"


def test_build_desglose_vf_ticker_c_2_modelos_no_calculables():
    """Ticker C -- 2 modelos no calculables (Múltiplos y Graham), DCF
    calculable -- evita el sesgo de solo probar "1 de 3 falla"."""
    ctx = _vf_context({
        "valor_justo_multiplos": None, "valor_justo_graham": None, "valor_justo_total": 510.00,
    })
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    assert "• Múltiplos — no calculable con los datos disponibles." in bloque
    assert "• Graham (EPS) — no calculable con los datos disponibles." in bloque
    assert "• DCF (Flujo de Caja Descontado) ($510.00) —" in bloque
    assert "Cuenta: FCF base $109.00" in bloque
    assert "None" not in bloque
    cuenta_vf = ai_explain._cuenta_vf(datos_vf)
    assert cuenta_vf == "($510.00) / 1 = $510.00"


def test_build_desglose_vf_ticker_d_3_modelos_no_calculables_sigue_mostrando_el_bloque():
    """Ticker D (caso extremo agregado por `qa`, resuelto por `architect`,
    Decisión de diseño #5) -- el Desglose se sigue mostrando completo (3
    líneas "no calculable"), aunque la Cuenta de "vf" sea `None`. Confirma
    que `_build_desglose_vf` no depende de `_cuenta_vf` para decidir si
    renderizarse."""
    ctx = _vf_context({
        "valor_justo_multiplos": None, "valor_justo_graham": None, "valor_justo_dcf": None,
        "valor_justo_total": None,
    })
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    assert ai_explain._cuenta_vf(datos_vf) is None
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    assert bloque.count("no calculable con los datos disponibles") == 3
    assert "None" not in bloque


@pytest.mark.parametrize("escenario", ["pesimista", "conservador", "optimista"])
def test_build_desglose_vf_fixture_escenario_variable_usa_siempre_el_mismo_escenario(escenario):
    """Fixture #6 de QA -- mismo ticker, `escenario_elegido` variando entre
    los 3 posibles: el valor entre paréntesis y la Cuenta usan siempre el
    mismo escenario elegido, sin importar cuál sea."""
    ctx = _texto_libre_context(escenario_elegido=escenario)
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    cuenta_vf = ai_explain._cuenta_vf(datos_vf)
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    esperado = ctx.scenarios[escenario]
    assert f"(${esperado['valor_justo_multiplos']:,.2f})" in bloque
    assert f"(${esperado['valor_justo_graham']:,.2f})" in bloque
    assert f"(${esperado['valor_justo_dcf']:,.2f})" in bloque
    assert f"${esperado['valor_justo_multiplos']:,.2f}" in cuenta_vf
    assert f"${esperado['valor_justo_graham']:,.2f}" in cuenta_vf
    assert f"${esperado['valor_justo_dcf']:,.2f}" in cuenta_vf


def test_build_desglose_vf_ticker_e_peor_caso_montos_extremos_bajo_el_tope():
    """Ticker E -- montos extremos (9-10 cifras en DCF, WACC/g de 2
    dígitos): mide el largo real del bloque con un test, no solo con el
    script Python que corrió `architect` una vez."""
    base_pequena = {
        "valor_justo_multiplos": 1.0, "valor_justo_graham": 1.0, "valor_justo_dcf": 1.0,
        "valor_justo_total": 1.0, "graham_g_aplicado": 0.01,
        "dcf_wacc": 0.08, "dcf_g_fcf": 0.03, "dcf_fcf_base": 1.0,
        "dcf_valor_presente_flujos": 1.0, "dcf_valor_terminal_descontado": 1.0, "dcf_equity_value": 1.0,
    }
    scenarios_extremos = {
        "pesimista": dict(base_pequena),
        "conservador": {
            "valor_justo_multiplos": 144.40, "valor_justo_graham": 130.00,
            "valor_justo_dcf": 999_999_999.99,
            "valor_justo_total": (144.40 + 130.00 + 999_999_999.99) / 3,
            "graham_g_aplicado": 0.035,
            "dcf_wacc": 0.999, "dcf_g_fcf": 0.999, "dcf_fcf_base": 999_999_999.99,
            "dcf_valor_presente_flujos": 999_999_999.99, "dcf_valor_terminal_descontado": 999_999_999.99,
            "dcf_equity_value": 1_999_999_999.98,
        },
        "optimista": dict(base_pequena),
    }
    ctx = _texto_libre_context(
        scenarios=scenarios_extremos, eps_ttm=9.50, y_value=0.082,
        peer_comparison={"per_promedio_peers": 15.20},
    )
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    assert len(bloque) <= ai_explain._MAX_DESGLOSE_CHARS

    cuenta = ai_explain._cuenta_vf(datos_vf)
    dato_line = ai_explain._build_dato_line("texto_libre", "vf", datos_vf)
    formula = ai_explain_content.formulas("texto_libre").get("vf")
    fuente = ai_explain_content.fuentes("texto_libre").get("vf")
    respuesta = "x" * ai_explain._MAX_EXPLANATION_CHARS
    mensaje_completo = ai_explain._build_leaf_message(
        dato_line, respuesta, formula, fuente, cuenta=cuenta, desglose=bloque,
    )
    assert len(mensaje_completo) <= TELEGRAM_MESSAGE_LIMIT


def test_build_desglose_vf_excepcion_en_un_sub_modelo_cae_a_no_calculable(monkeypatch):
    """Caso de error obligatorio de QA -- forzar que uno de los 3 sub-`datos`
    lance una excepción al construirse: esa sub-sección cae a "no
    calculable" en vez de propagar el error y romper todo el mensaje de
    "vf" (mismo `try/except` amplio que el resto del mecanismo de
    Desglose)."""
    ctx = _texto_libre_context()
    original = ai_explain._payload_texto_libre

    def _payload_que_falla_en_gra(context, code):
        if code == "gra":
            raise RuntimeError("fallo simulado de _payload_texto_libre")
        return original(context, code)

    monkeypatch.setattr(ai_explain, "_payload_texto_libre", _payload_que_falla_en_gra)
    datos_vf = original(ctx, "vf")
    bloque = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert bloque is not None
    assert "• Graham (EPS) — no calculable con los datos disponibles." in bloque
    assert "• Múltiplos ($500.00) —" in bloque
    assert "• DCF (Flujo de Caja Descontado) ($510.00) —" in bloque


def test_build_desglose_vf_no_muta_datos_vf():
    """El `datos_sub` intermedio (`_payload_texto_libre(context,
    "mul"/"gra"/"dcf")`) nunca se mezcla con `datos_vf` -- `_build_desglose_vf`
    no escribe ni retorna nada que se asigne de vuelta a `datos_vf` (que es
    `datos_del_contexto`, el que ve Ollama)."""
    ctx = _texto_libre_context()
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    snapshot = dict(datos_vf)
    ai_explain._build_desglose_vf(ctx, datos_vf)
    assert datos_vf == snapshot


def test_payload_texto_libre_vf_sin_cambios_antes_y_despues_de_construir_el_desglose():
    """"Ningún campo nuevo en `datos_del_contexto` (el payload que ve
    Ollama para "vf")" -- `_payload_texto_libre(context, "vf")` devuelve
    exactamente el mismo dict antes y después de ejercitar el mecanismo
    nuevo."""
    ctx = _texto_libre_context()
    datos_vf_1 = ai_explain._payload_texto_libre(ctx, "vf")
    ai_explain._build_desglose_vf(ctx, datos_vf_1)
    datos_vf_2 = ai_explain._payload_texto_libre(ctx, "vf")
    assert datos_vf_1 == datos_vf_2
    assert set(datos_vf_2) == {
        "modelo", "escenario_elegido", "precio_actual",
        "valor_justo_multiplos", "valor_justo_graham", "valor_justo_dcf", "valor_justo_total",
    }


# --- `_build_desglose_block` -- firma retrocompatible + rama de delegación -


def test_build_desglose_block_avanzado_3_posicionales_sin_context_sigue_igual():
    """Retrocompatibilidad de firma -- los tests existentes que llaman
    `_build_desglose_block("avanzado", code, datos)` con 3 argumentos
    posicionales (sin `context`) siguen pasando sin modificar ni una
    línea."""
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "alz")
    assert ai_explain._build_desglose_block("avanzado", "alz", datos) is not None


def test_build_desglose_block_texto_libre_vf_con_context_delega_a_build_desglose_vf():
    """Rama de delegación, combinación completa (las 3 condiciones AND
    verdaderas): `kind == "texto_libre" and question_code == "vf" and
    context is not None`."""
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "vf")
    resultado = ai_explain._build_desglose_block("texto_libre", "vf", datos, context=ctx)
    assert resultado is not None
    assert "Cuenta: $8.20 × 24.00 = $500.00" in resultado


def test_build_desglose_block_texto_libre_vf_sin_context_no_delega(monkeypatch):
    """Combinación "algún operando falso" #1: `context is None` (el
    default) -- no activa el caso especial de "vf", `_build_desglose_vf`
    nunca se ejecuta. El mecanismo genérico (mismo que Altman/Magic
    Formula) sigue corriendo con la entrada de `DESGLOSE_TEXTO_LIBRE["vf"]`
    -- no arma sub-cuentas (esas son exclusivas de `_build_desglose_vf`),
    pero tampoco rompe ni propaga excepción."""
    llamado = []
    monkeypatch.setattr(
        ai_explain, "_build_desglose_vf", lambda *a, **k: llamado.append(True) or "no debería usarse"
    )
    resultado = ai_explain._build_desglose_block("texto_libre", "vf", {}, context=None)
    assert not llamado, "_build_desglose_vf no debe ejecutarse sin context"
    assert resultado is not None
    assert "Cuenta:" not in resultado


def test_build_desglose_block_avanzado_con_context_no_delega():
    """Combinación "algún operando falso" #2: `kind == "avanzado"` (no
    `"texto_libre"`) -- pasar `context` no le cambia nada, mismo resultado
    que sin `context` (comportamiento real del call site de
    `handle_explain`, que pasa `context=stored` siempre, para las 27
    preguntas)."""
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "alz")
    sin_context = ai_explain._build_desglose_block("avanzado", "alz", datos)
    con_context = ai_explain._build_desglose_block("avanzado", "alz", datos, context=ctx)
    assert sin_context == con_context


def test_build_desglose_block_texto_libre_otro_code_con_context_no_delega():
    """Combinación "algún operando falso" #3: `question_code != "vf"` -- ni
    siquiera con `context` presente, "ren" (que no tiene entrada en
    `DESGLOSE_TEXTO_LIBRE`) sigue devolviendo `None`, igual que las demás
    preguntas de texto libre sin desglose."""
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "ren")
    assert ai_explain._build_desglose_block("texto_libre", "ren", datos, context=ctx) is None


def test_build_desglose_block_texto_libre_ver_con_context_no_delega_a_vf(monkeypatch):
    """SDD_desglose_universal.md, Grupo D -- criterio de QA explícito:
    agregar "ver" a `DESGLOSE_TEXTO_LIBRE` NO dispara la rama de delegación
    especial de "vf" (`_build_desglose_vf`), aunque "ver" y "vf" convivan
    en `texto_libre` y ambos reciban `context`."""
    llamado = []
    monkeypatch.setattr(
        ai_explain, "_build_desglose_vf", lambda *a, **k: llamado.append(True) or "no debería usarse"
    )
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "ver")
    resultado = ai_explain._build_desglose_block("texto_libre", "ver", datos, context=ctx)
    assert not llamado, "_build_desglose_vf no debe ejecutarse para 'ver'"
    assert resultado is not None
    assert resultado.startswith("🔍 Desglose:\n")
    assert "Cuenta:" not in resultado


@pytest.mark.parametrize("code", list(_CODES_SIN_DESGLOSE) + list(_CODES_CON_DESGLOSE))
def test_build_desglose_block_context_no_cambia_resultado_para_las_26_preguntas_restantes(code):
    """Regresión de las 26 preguntas restantes (7 de `/avanzado` con
    desglose + 19 sin desglose, "vf" aparte con su propio mecanismo) --
    `_build_desglose_block(kind, code, datos, context=stored)` (con
    `context` siempre pasado, como queda el call site real de
    `handle_explain`) devuelve exactamente lo mismo que devolvía
    `_build_desglose_block(kind, code, datos)` sin `context` antes del
    cambio."""
    kind = "texto_libre" if code in ai_explain_content.QUESTIONS_TEXTO_LIBRE else "avanzado"
    spec = ai_explain_content.all_questions(kind)[code]
    if spec.variant == ai_explain_content.VARIANT_DETERMINISTICO:
        pytest.skip("evt/inf nunca pasan por _build_explain_payload/_build_desglose_block")
    ctx = _avanzado_context() if kind == "avanzado" else _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    sin_context = ai_explain._build_desglose_block(kind, code, datos)
    con_context = ai_explain._build_desglose_block(kind, code, datos, context=ctx)
    assert sin_context == con_context


# --- "📊 Ver dato" de "vf" -- no cambia ------------------------------------


def test_ver_dato_vf_no_incluye_cuenta_ni_desglose():
    """"📊 Ver dato" de "vf" no cambia (sigue sin Cuenta ni Desglose) --
    ese flujo nunca llama a `_build_desglose_block` ni a
    `_build_desglose_vf`."""
    ctx = _texto_libre_context()
    contenido = ai_explain._build_ver_dato_content(ctx, "vf")
    assert "🔍 Desglose" not in contenido
    assert "🧮 Cuenta" not in contenido
    assert "📌 Dato:" in contenido


def test_build_ver_dato_content_nunca_llama_a_build_desglose_vf(monkeypatch):
    llamado = []
    monkeypatch.setattr(ai_explain, "_build_desglose_vf", lambda *a, **k: llamado.append(True))
    ai_explain._build_ver_dato_content(_texto_libre_context(), "vf")
    assert not llamado


# --- Mensaje completo "🎓 Explicame paso a paso" de "vf" (end-to-end) ------


async def test_mensaje_paso_a_paso_vf_muestra_cuenta_y_desglose_con_3_subcuentas():
    """Criterio de aceptación -- para un ticker con los 3 modelos
    calculables, "🎓 Explicame paso a paso" de "vf" muestra 🧮 Cuenta seguido
    de 🔍 Desglose (en ese orden, antes de la respuesta de Ollama), con 3
    sub-secciones."""
    ctx = _texto_libre_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "Los tres modelos coinciden en un rango de valor similar para esta acción."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    assert texto != ai_explain.EXPLAIN_UNAVAILABLE_MSG
    assert "🧮 Cuenta" in texto
    assert "🔍 Desglose" in texto
    assert texto.index("🧮 Cuenta") < texto.index("🔍 Desglose") < texto.index(respuesta)
    assert "None" not in texto
    assert "• Múltiplos ($500.00) —" in texto
    assert "• Graham (EPS) ($480.00) —" in texto
    assert "• DCF (Flujo de Caja Descontado) ($510.00) —" in texto
    assert len(texto) <= TELEGRAM_MESSAGE_LIMIT


async def test_mensaje_paso_a_paso_vf_3_no_calculables_omite_cuenta_pero_muestra_desglose():
    """[x] Criterio agregado por `qa`, resuelto por `architect` (Decisión
    de diseño #5) -- 3 sub-modelos no calculables: la "🧮 Cuenta" de "vf" se
    omite del mensaje en silencio (sin texto placeholder), y la "🔍
    Desglose" se sigue mostrando completa, con sus 3 sub-secciones en "no
    calculable"."""
    ctx = _vf_context({
        "valor_justo_multiplos": None, "valor_justo_graham": None, "valor_justo_dcf": None,
        "valor_justo_total": None,
    })
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    respuesta = "No fue posible estimar un valor justo con los modelos disponibles para este ticker."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:p:vf")
    await callback(update, context)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]
    assert "🧮 Cuenta" not in texto
    assert "🔍 Desglose" in texto
    assert texto.count("no calculable con los datos disponibles") == 3
    assert "None" not in texto


def test_build_cuenta_line_vf_none_y_leaf_message_omite_cuenta_pero_incluye_desglose_3_no_calculables():
    """Test de regresión dirigido, no end-to-end -- exactamente el que pide
    el criterio de aceptación nuevo: `_build_cuenta_line("texto_libre",
    "vf", datos)` devuelve `None` para el fixture de 3 no calculables, y
    `_build_leaf_message(..., cuenta=None, desglose=<bloque completo>)` no
    incluye la substring "🧮 Cuenta" en su salida."""
    ctx = _vf_context({
        "valor_justo_multiplos": None, "valor_justo_graham": None, "valor_justo_dcf": None,
        "valor_justo_total": None,
    })
    datos_vf = ai_explain._payload_texto_libre(ctx, "vf")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "vf", datos_vf)
    assert cuenta is None

    desglose = ai_explain._build_desglose_vf(ctx, datos_vf)
    assert desglose is not None
    assert desglose.count("no calculable con los datos disponibles") == 3

    texto = ai_explain._build_leaf_message(
        "Dato de prueba", "Respuesta.", None, None, cuenta=cuenta, desglose=desglose,
    )
    assert "🧮 Cuenta" not in texto
    assert "🔍 Desglose" in texto


# ---------------------------------------------------------------------------
# VII. "🔍 Desglose" universal -- SDD_desglose_universal.md (12 preguntas
# nuevas: gra, dcf, mul, rat, pil, rsk, mom, cmp, ver, aqv, aqq, aqm, aql)
# ---------------------------------------------------------------------------

_CODES_GRUPO_F_AVANZADO = ("aqv", "aqq", "aqm", "aql")
_CODES_NUEVOS_TEXTO_LIBRE = tuple(c for c in _CODES_CON_DESGLOSE_TEXTO_LIBRE if c != "vf")
_CODES_12_NUEVAS = _CODES_NUEVOS_TEXTO_LIBRE + _CODES_GRUPO_F_AVANZADO


# --- Valores reales, término a término, contra el fixture por defecto -----


@pytest.mark.parametrize(
    "code, letra, esperado",
    [
        ("gra", "EPS", "$8.20"),
        ("gra", "g", "9.4%"),
        ("gra", "Y", "4.2%"),
        ("dcf", "FCF base", "$109.00"),
        ("dcf", "WACC", "9.1%"),
        ("dcf", "g", "8.3%"),
        ("dcf", "Valor presente de los flujos", "$612.00"),
        ("dcf", "Valor terminal descontado", "$2,100.00"),
        ("dcf", "Valor de la empresa", "$2,712.00"),
        ("mul", "EPS", "$8.20"),
        ("mul", "PER promedio peers", "24.00"),
        ("rat", "Liquidez", "1.80"),
        ("rat", "Margen bruto", "65.0%"),
        ("rat", "PER", "22.50"),
        ("rat", "P/S", "6.20"),
        ("pil", "Ingresos crecientes", "$1,000.00 > $800.00"),
        ("pil", "Utilidades crecientes", "$200.00 > $150.00"),
        ("pil", "Deuda controlada", "1.80"),
        ("pil", "Precio razonable", "❌ No cumple"),
        ("rsk", "Beta", "1.15"),
        ("rsk", "Perfil de riesgo", "moderado"),
        ("mom", "vs. máx. 52 semanas", "-8.2%"),
        ("mom", "vs. mín. 52 semanas", "25.0%"),
        ("mom", "vs. promedio 50 días", "3.1%"),
        ("mom", "vs. promedio 200 días", "9.4%"),
        ("cmp", "PER propio", "22.50"),
        ("cmp", "PER promedio peers", "24.00"),
        ("ver", "Precio actual", "$550.00"),
        ("ver", "Valor Justo Total", "$496.00"),
    ],
)
def test_valor_desglose_texto_libre_termino_a_termino_fixture_por_defecto(code, letra, esperado):
    """QA -- valores reales, letra por letra, contra el fixture por
    defecto de `_texto_libre_context()` (mismo patrón que
    `test_valor_desglose_alz_termino_a_termino_ejemplo_de_daniela`)."""
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
    assert extractor(letra, datos) == esperado


def test_valor_desglose_aqv_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqv")
    assert ai_explain._valor_desglose_aqv("Earnings Yield", datos) == "8.0%"
    esperado_rango = ai_explain._rango_pct(
        datos["earnings_yield"], datos["umbral_alto"], datos["umbral_bajo"]
    )
    assert ai_explain._valor_desglose_aqv("Umbrales", datos) == esperado_rango


def test_valor_desglose_aqq_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqq")
    assert ai_explain._valor_desglose_aqq("ROE", datos) == "22.0%"
    assert ai_explain._valor_desglose_aqq("Margen bruto", datos) == "55.0%"
    assert ai_explain._valor_desglose_aqq("Ratio de Piotroski", datos) == "77.8%"


def test_valor_desglose_aql_termino_a_termino():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aql")
    assert ai_explain._valor_desglose_aql("Beta", datos) == "1.05"


def test_valor_desglose_aqm_termino_a_termino():
    ctx = _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    assert ai_explain._valor_desglose_aqm("vs. promedio 50 días", datos) == "7.1%"
    assert ai_explain._valor_desglose_aqm("vs. promedio 200 días", datos) == "15.4%"


# --- Dato faltante (1 campo, no todos) -- solo esa línea se omite ---------


@pytest.mark.parametrize(
    "code, letra, campo_ausente",
    [
        ("gra", "EPS", "eps_ttm"),
        ("dcf", "WACC", "dcf_wacc"),
        ("mul", "PER promedio peers", "per_promedio_peers"),
        ("rat", "PER", "per"),
        ("pil", "Deuda controlada", "ratio_liquidez"),
        ("rsk", "Beta", "beta"),
        ("mom", "vs. máx. 52 semanas", "pct_vs_year_high"),
        ("cmp", "PER promedio peers", "per_promedio_peers"),
        ("ver", "Valor Justo Total", "valor_justo_total"),
    ],
)
def test_valor_desglose_texto_libre_1_campo_faltante_omite_solo_esa_linea(code, letra, campo_ausente):
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    datos_con_hueco = dict(datos)
    datos_con_hueco[campo_ausente] = None
    extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
    assert extractor(letra, datos_con_hueco) is None
    # Las demás letras del mismo término siguen mostrando su valor real.
    bloque = ai_explain._build_desglose_block("texto_libre", code, datos_con_hueco)
    assert bloque is not None
    assert "None" not in bloque
    otras_letras = [t.letra for t in ai_explain_content.desglose("texto_libre", code) if t.letra != letra]
    for otra in otras_letras:
        assert extractor(otra, datos) is not None, f"{code}/{otra}: se esperaba un valor real en el fixture"


@pytest.mark.parametrize(
    "code, letra, campo_ausente",
    [
        ("aqv", "Earnings Yield", "earnings_yield"),
        ("aqq", "ROE", "roe"),
        ("aql", "Beta", "beta"),
    ],
)
def test_valor_desglose_avanzado_1_campo_faltante_omite_solo_esa_linea(code, letra, campo_ausente):
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    datos_con_hueco = dict(datos)
    datos_con_hueco[campo_ausente] = None
    extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
    assert extractor(letra, datos_con_hueco) is None
    bloque = ai_explain._build_desglose_block("avanzado", code, datos_con_hueco)
    assert bloque is not None
    assert "None" not in bloque


@pytest.mark.parametrize("campo_ausente", ["precio_actual", "price_avg_50", "price_avg_200"])
def test_valor_desglose_aqm_1_campo_faltante_omite_solo_esa_linea(campo_ausente):
    ctx = _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    datos_con_hueco = dict(datos)
    datos_con_hueco[campo_ausente] = None
    assert ai_explain._valor_desglose_aqm("vs. promedio 50 días", datos_con_hueco) == (
        None if campo_ausente in ("precio_actual", "price_avg_50") else "7.1%"
    )
    bloque = ai_explain._build_desglose_block("avanzado", "aqm", datos_con_hueco)
    assert bloque is not None
    assert "None" not in bloque


# --- Consistencia "🧮 Cuenta" vs. "🔍 Desglose" -----------------------------


@pytest.mark.parametrize("code", _CODES_NUEVOS_TEXTO_LIBRE)
def test_consistencia_cuenta_y_desglose_texto_libre_mismo_valor_termino_a_termino(code):
    """QA -- criterio agregado: el valor mostrado en cada línea del
    Desglose coincide EXACTAMENTE (mismo formato, mismo redondeo) con el
    que ya muestra la Cuenta de la misma pregunta."""
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, code)
    cuenta = ai_explain._build_cuenta_line("texto_libre", code, datos)
    bloque = ai_explain._build_desglose_block("texto_libre", code, datos)
    assert cuenta is not None
    assert bloque is not None
    extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
    for t in ai_explain_content.desglose("texto_libre", code):
        if code == "rsk" and t.letra == "Perfil de riesgo":
            # No es un término de la fórmula -- es el perfil que el propio
            # usuario eligió con /start, `_cuenta_rsk` nunca lo muestra
            # (muestra el perfil "implícito" derivado del beta, un dato
            # distinto por diseño). Ver Grupo C de la spec.
            continue
        if code == "pil" and t.letra == "Precio razonable":
            # `_cuenta_pil` usa el texto "razonable"/"no razonable"; el
            # Desglose usa ✅/❌ (mismo patrón que los criterios de
            # Piotroski, Grupo B de la spec) -- vocabulario distinto a
            # propósito, no una inconsistencia de valor.
            continue
        valor = extractor(t.letra, datos)
        if valor is None:
            continue
        if code == "gra" and t.letra in ("g", "Y"):
            # La Cuenta muestra `g`/`Y` sin el sufijo "%" (`2×{g_pct:.1f}`,
            # `/ {y_pct:.1f}`), el Desglose lo muestra con "%" para que la
            # línea sea legible de forma autocontenida. Mismo número, mismo
            # redondeo -- se compara el núcleo numérico, no el sufijo.
            assert valor[:-1] in cuenta, (
                f"{code}/{t.letra}: {valor!r} del Desglose no aparece en la Cuenta {cuenta!r}"
            )
        elif code == "pil" and " > " in valor:
            # "$200.00 > $150.00" en el Desglose vs. "$200.00 > 0 y >
            # $150.00" en la Cuenta -- mismos 2 montos, con un chequeo
            # intermedio adicional en la Cuenta que el Desglose no repite
            # (Decisión de diseño de `_cuenta_pil`). Se verifican los 2
            # montos por separado, no la substring contigua.
            for parte in valor.split(" > "):
                assert parte in cuenta, (
                    f"{code}/{t.letra}: {parte!r} (de {valor!r}) no aparece en la Cuenta {cuenta!r}"
                )
        else:
            assert valor in cuenta, (
                f"{code}/{t.letra}: {valor!r} del Desglose no aparece en la Cuenta {cuenta!r}"
            )


@pytest.mark.parametrize("code", _CODES_GRUPO_F_AVANZADO)
def test_consistencia_cuenta_y_desglose_grupo_f_avanzado_mismo_valor_termino_a_termino(code):
    ctx = _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    datos = ai_explain._build_explain_payload(ctx, code)
    cuenta = ai_explain._build_cuenta_line("avanzado", code, datos)
    bloque = ai_explain._build_desglose_block("avanzado", code, datos)
    assert cuenta is not None
    assert bloque is not None
    if code == "aqm":
        # "aqm" -- Cuenta y Desglose comparten literalmente los mismos 3
        # campos crudos (precio_actual/price_avg_50/price_avg_200), pero se
        # muestran en formatos distintos ($ en la Cuenta, % en el Desglose)
        # -- la consistencia acá es "mismos campos crudos", no "mismo
        # string", ver Grupo F de la spec.
        for campo in ("precio_actual", "price_avg_50", "price_avg_200"):
            assert ai_explain._money(datos[campo]) in cuenta
        return
    extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
    for t in ai_explain_content.desglose("avanzado", code):
        valor = extractor(t.letra, datos)
        if valor is not None:
            assert valor in cuenta, f"{code}/{t.letra}: {valor!r} del Desglose no aparece en la Cuenta {cuenta!r}"


# --- "ver" -- no delega a `_build_desglose_vf` (cubierto arriba también) --
# --- ya cubierto por test_desglose_texto_libre_ver_2_terminos_livianos y
# --- test_build_desglose_block_texto_libre_ver_con_context_no_delega_a_vf.


# --- "cmp" -- longitud constante, sin importar la cantidad de peers -------


def test_build_desglose_block_cmp_longitud_constante_sin_importar_cantidad_de_peers():
    """Test explícitamente pedido por Daniela -- el Desglose de "cmp" no
    crece con la cantidad de comparables disponibles (2, 5, 20 peers dan
    exactamente el mismo largo)."""

    def _bloque_con(cantidad_peers: int) -> str:
        peers = [f"PEER{i}" for i in range(cantidad_peers)]
        ctx = _texto_libre_context(
            peer_comparison={
                "per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
                "per_maximo_peers": 30.0, "peers_usados": peers,
                "posicion": "en_linea", "motivo_no_comparable": None,
            }
        )
        datos = ai_explain._build_explain_payload(ctx, "cmp")
        return ai_explain._build_desglose_block("texto_libre", "cmp", datos)

    bloque_2 = _bloque_con(2)
    bloque_5 = _bloque_con(5)
    bloque_20 = _bloque_con(20)
    assert bloque_2 is not None
    assert len(bloque_2) == len(bloque_5) == len(bloque_20)


def test_build_desglose_block_cmp_ningun_peer_individual_aparece_en_el_texto():
    """No solo la longitud constante -- ningún ticker de `peers_usados`
    (ni su nombre) se filtra al texto del Desglose por otro extractor mal
    escrito."""
    peers = ["MSFT", "CRM", "ORCL", "SAP", "NOW"]
    ctx = _texto_libre_context(
        peer_comparison={
            "per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
            "per_maximo_peers": 30.0, "peers_usados": peers,
            "posicion": "en_linea", "motivo_no_comparable": None,
        }
    )
    datos = ai_explain._build_explain_payload(ctx, "cmp")
    bloque = ai_explain._build_desglose_block("texto_libre", "cmp", datos)
    assert bloque is not None
    for peer in peers:
        assert peer not in bloque


# --- Presupuesto de longitud, peor caso para "dcf" (medido, no estimado) --


def test_build_desglose_block_dcf_peor_caso_montos_extremos_bajo_el_tope():
    """QA -- número real medido, no una estimación en comentario. Montos de
    9-10 cifras + WACC/g con decimales largos."""
    scenarios_extremos = {
        "pesimista": {
            "valor_justo_multiplos": 1.0, "valor_justo_graham": 1.0, "valor_justo_dcf": 1.0,
            "valor_justo_total": 1.0, "graham_g_aplicado": 0.01,
            "dcf_wacc": 0.08, "dcf_g_fcf": 0.03, "dcf_fcf_base": 1.0,
            "dcf_valor_presente_flujos": 1.0, "dcf_valor_terminal_descontado": 1.0,
            "dcf_equity_value": 1.0,
        },
        "conservador": {
            "valor_justo_multiplos": 144.40, "valor_justo_graham": 130.00,
            "valor_justo_dcf": 999_999_999.99,
            "valor_justo_total": (144.40 + 130.00 + 999_999_999.99) / 3,
            "graham_g_aplicado": 0.091234,
            "dcf_wacc": 0.091234, "dcf_g_fcf": 0.091234, "dcf_fcf_base": 999_999_999.99,
            "dcf_valor_presente_flujos": 999_999_999.99,
            "dcf_valor_terminal_descontado": 999_999_999.99,
            "dcf_equity_value": 1_999_999_999.98,
        },
        "optimista": {
            "valor_justo_multiplos": 1.0, "valor_justo_graham": 1.0, "valor_justo_dcf": 1.0,
            "valor_justo_total": 1.0, "graham_g_aplicado": 0.01,
            "dcf_wacc": 0.08, "dcf_g_fcf": 0.03, "dcf_fcf_base": 1.0,
            "dcf_valor_presente_flujos": 1.0, "dcf_valor_terminal_descontado": 1.0,
            "dcf_equity_value": 1.0,
        },
    }
    ctx = _texto_libre_context(scenarios=scenarios_extremos)
    datos = ai_explain._build_explain_payload(ctx, "dcf")
    bloque = ai_explain._build_desglose_block("texto_libre", "dcf", datos)
    assert bloque is not None, "el Desglose de 'dcf' se omitió por exceder el tope"
    assert len(bloque) < ai_explain._MAX_DESGLOSE_CHARS

    cuenta = ai_explain._build_cuenta_line("texto_libre", "dcf", datos)
    dato_line = ai_explain._build_dato_line("texto_libre", "dcf", datos)
    formula = ai_explain_content.formulas("texto_libre").get("dcf")
    fuente = ai_explain_content.fuentes("texto_libre").get("dcf")
    respuesta = "x" * ai_explain._MAX_EXPLANATION_CHARS
    mensaje_completo = ai_explain._build_leaf_message(
        dato_line, respuesta, formula, fuente, cuenta=cuenta, desglose=bloque,
    )
    assert len(mensaje_completo) < TELEGRAM_MESSAGE_LIMIT


# --- Funciones puras, sin I/O (extendido a las 13 preguntas nuevas) -------


def test_build_desglose_block_extractores_nuevos_son_funciones_puras_sin_io():
    import inspect
    for code in _CODES_12_NUEVAS:
        extractor = ai_explain._DESGLOSE_VALOR_EXTRACTORS[code]
        assert not inspect.iscoroutinefunction(extractor), f"{code}: extractor no debería ser async"


def test_cuenta_aqm_es_funcion_pura_dict_a_str_o_none():
    import inspect
    assert not inspect.iscoroutinefunction(ai_explain._cuenta_aqm)
    sig = inspect.signature(ai_explain._cuenta_aqm)
    assert list(sig.parameters) == ["datos"]


# --- Regresión "mom" (texto libre) -- no afectado por el cambio de "aqm" --


def test_cuenta_y_desglose_mom_sin_cambios_por_el_cambio_de_aqm():
    """Grupo F -- "aqm" ahora también popula `context.momentum`/`context.
    precio_actual`/etc. para `kind="avanzado"`, pero "mom" (texto libre)
    sigue usando exactamente el mismo `context.momentum` dict y el mismo
    resultado que antes del cambio."""
    ctx = _texto_libre_context()
    datos = ai_explain._build_explain_payload(ctx, "mom")
    cuenta = ai_explain._build_cuenta_line("texto_libre", "mom", datos)
    bloque = ai_explain._build_desglose_block("texto_libre", "mom", datos)
    assert cuenta == (
        "($550.00 − $600.00) / $600.00 × 100 = -8.2% vs. máx. 52 sem. · "
        "($550.00 − $400.00) / $400.00 × 100 = 25.0% vs. mín. 52 sem. · "
        "($550.00 − $540.00) / $540.00 × 100 = 3.1% vs. promedio 50d · "
        "($550.00 − $520.00) / $520.00 × 100 = 9.4% vs. promedio 200d"
    )
    assert bloque is not None
    assert "-8.2%" in bloque
    assert "25.0%" in bloque
