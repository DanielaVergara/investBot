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
            },
            "conservador": {
                "valor_justo_multiplos": 500.0, "valor_justo_graham": 480.0,
                "valor_justo_dcf": 510.0, "valor_justo_total": 496.0,
            },
            "optimista": {
                "valor_justo_multiplos": 540.0, "valor_justo_graham": 520.0,
                "valor_justo_dcf": 550.0, "valor_justo_total": 536.7,
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
        altman={"disponible": True, "z": 3.5, "zona": "segura", "campos_faltantes": []},
        altman_pp={"disponible": True, "z": 5.2, "zona": "segura", "campos_faltantes": []},
        piotroski={
            "puntaje": 7,
            "criterios_evaluables": 9,
            "criterios_totales": 9,
            "criterios": [
                {"nombre": "roa_positivo", "cumplido": True},
                {"nombre": "cfo_positivo", "cumplido": True},
                {"nombre": "roa_creciente", "cumplido": True},
                {"nombre": "cfo_mayor_utilidad", "cumplido": False},
                {"nombre": "apalancamiento_decreciente", "cumplido": True},
                {"nombre": "liquidez_creciente", "cumplido": False},
                {"nombre": "sin_dilucion", "cumplido": True},
                {"nombre": "margen_bruto_creciente", "cumplido": True},
                {"nombre": "rotacion_activos_creciente", "cumplido": False},
            ],
        },
        beneish={"disponible": False, "motivo": "no_calculable_con_datos_disponibles"},
        magic={"disponible": True, "roic": 0.2, "earnings_yield": 0.08, "campos_faltantes": []},
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
    assert payload == {
        "modelo": ai_explain._MODELO_PIL,
        "pillars": ctx.pillars,
        "total_pilares": len(ctx.pillars),
    }


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
    }


def test_payload_ren_solo_contiene_extras():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "ren")
    assert set(payload) == {"modelo", "roe", "debt_to_equity", "net_debt_to_ebitda", "dividend_yield", "payout_ratio"}


def test_payload_rsk_solo_contiene_risk_fit():
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, "rsk")
    assert set(payload) == {"modelo", "encaja", "perfil", "beta", "etiqueta_activo"}


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
    assert set(payload) == {"modelo", "quality", "roe", "gross_margin", "piotroski_ratio"}
    assert payload["roe"] == ctx.roe
    assert payload["gross_margin"] == ctx.gross_margin
    assert payload["piotroski_ratio"] == pytest.approx(ctx.piotroski["puntaje"] / ctx.piotroski["criterios_evaluables"])


def test_payload_aqv_aqm_aql_superficie_minima():
    ctx = _avanzado_context()
    assert set(ai_explain._build_explain_payload(ctx, "aqv")) == {"modelo", "value"}
    assert set(ai_explain._build_explain_payload(ctx, "aqm")) == {"modelo", "momentum"}
    aql = ai_explain._build_explain_payload(ctx, "aql")
    assert set(aql) == {"modelo", "low_vol", "beta"}
    assert aql["beta"] == ctx.beta


def test_payload_mgr_mge_solo_contienen_su_metrica():
    ctx = _avanzado_context()
    mgr = ai_explain._build_explain_payload(ctx, "mgr")
    assert set(mgr) == {"modelo", "roic", "disponible"}
    mge = ai_explain._build_explain_payload(ctx, "mge")
    assert set(mge) == {"modelo", "earnings_yield", "disponible"}


@pytest.mark.parametrize(
    "question_code",
    [c for c, spec in ai_explain_content.QUESTIONS_TEXTO_LIBRE.items() if spec.requires_ollama],
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
    [c for c, spec in ai_explain_content.QUESTIONS_TEXTO_LIBRE.items() if spec.requires_ollama],
)
def test_payload_texto_libre_pregunta_ollama_incluye_modelo(question_code):
    """Regla 5 de `SYSTEM_PROMPT_EXPLAIN`: toda pregunta que llama a Ollama
    trae la clave "modelo" (síntesis narrativas "ver" incluida)."""
    ctx = _texto_libre_context()
    payload = ai_explain._build_explain_payload(ctx, question_code)
    assert "modelo" in payload


@pytest.mark.parametrize(
    "question_code",
    [c for c, spec in ai_explain_content.QUESTIONS_AVANZADO.items() if spec.requires_ollama and c != "mod"],
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
# H. Flujo completo del handler -- leaf con Ollama
# ---------------------------------------------------------------------------


async def test_handler_leaf_ollama_camino_feliz_orden_de_bloques():
    """Formato exacto de la Decisión de diseño #5: header -> Dato ->
    respuesta -> Fórmula/Fuente -> disclaimer, en ese orden."""
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    respuesta = "Graham estima un valor conservador cercano al dato mostrado arriba."
    client = _client_with_handler(_ok_handler(respuesta))
    clients = _make_clients(http_client=client, ollama_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)

    update, query, context = _fake_callback_update(f"xp:{cid}:gra")
    await callback(update, context)

    query.answer.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=_DEFAULT_CHAT_ID, text=ai_explain.EXPLAIN_PENDING_MSG
    )
    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    texto = kwargs["text"]

    idx_header = texto.index(ai_rewrite.TRANSPARENCY_USED)
    idx_dato = texto.index("📌 Dato:")
    idx_respuesta = texto.index(respuesta)
    idx_formula = texto.index("📐 Fórmula:")
    idx_fuente = texto.index("📊 Fuente del dato:")
    idx_disclaimer = texto.index(DISCLAIMER_NO_ASESORAMIENTO)
    assert idx_header < idx_dato < idx_respuesta < idx_formula < idx_fuente < idx_disclaimer
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
    update_a, _, context_a = _fake_callback_update(f"xp:{cid_a}:vf")
    await callback(update_a, context_a)
    texto_a = context_a.bot.edit_message_text.call_args.kwargs["text"]

    cid_b = store.put(_texto_libre_context(
        ticker="ZZZ",
        precio_actual=10.0,
        scenarios={"conservador": {"valor_justo_multiplos": 5, "valor_justo_graham": 5, "valor_justo_dcf": 5, "valor_justo_total": 5.0}},
    ))
    update_b, _, context_b = _fake_callback_update(f"xp:{cid_b}:vf")
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
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context())
    spy = _CountingClient()
    clients = _make_clients(http_client=spy, ollama_config=_enabled_config())
    limiter = FakeRateLimiter(allow_value=False)
    callback = _build_callback(clients, limiter, store)

    update, query, context = _fake_callback_update(f"xp:{cid}:vf")
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

    update, query, context = _fake_callback_update(f"xp:{cid}:vf")
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

    update, query, context = _fake_callback_update(f"xp:{cid}:pil")
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

    update, query, context = _fake_callback_update(f"xp:{cid}:vf")
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

    update, query, context = _fake_callback_update(f"xp:{cid}:aqv")
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
