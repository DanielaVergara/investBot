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

import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investbot import ai_explain, ai_explain_content, ai_rewrite
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
    assert set(ai_explain._build_explain_payload(ctx, "aqm")) == {"modelo", "momentum"}
    aql = ai_explain._build_explain_payload(ctx, "aql")
    assert set(aql) == {"modelo", "low_vol", "beta", "beta_umbral_bajo", "beta_umbral_alto"}
    assert aql["beta"] == ctx.beta


def test_payload_mgr_mge_solo_contienen_su_metrica():
    ctx = _avanzado_context()
    mgr = ai_explain._build_explain_payload(ctx, "mgr")
    assert set(mgr) == {"modelo", "roic", "disponible", "ebit", "capital_invertido"}
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


def test_bloque_formula_ausente_para_ver_y_mod():
    """`ver` (texto libre) y `mod` (avanzado) no tienen entrada en
    `FORMULAS` -- omitida, nunca vacía."""
    assert "ver" not in ai_explain_content.FORMULAS_TEXTO_LIBRE
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


def test_cuenta_aqm_reutiliza_etiqueta():
    ctx = _avanzado_context()
    datos = ai_explain._build_explain_payload(ctx, "aqm")
    cuenta = ai_explain._build_cuenta_line("avanzado", "aqm", datos)
    assert cuenta == "Factor Momentum: medio"


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
