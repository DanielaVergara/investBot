"""Tests de `onboarding.py` — puntaje de perfil de riesgo + flujo conversacional.

Cubre:
- Rango de puntaje (mínimo 80 / máximo 400) y las 8 fronteras exactas de B5.
- Cobertura del flujo conversacional (avance estado a estado, reinicio,
  sobrescritura, escritura atómica) — sección 4 de `qa`.
- Test de chat_id no autorizado nunca entra al flujo (cruce con `security`).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from investbot import db, onboarding
from investbot.security import build_chat_id_gate, is_authorized_chat

ALLOWED_CHAT_ID = 12345


# ---------------------------------------------------------------------------
# Puntaje total — rango mínimo/máximo (criterio de `architect`)
# ---------------------------------------------------------------------------


def test_puntaje_minimo():
    respuestas = [min(score for _, score in opts) for _, opts in onboarding.QUESTIONS]
    total = onboarding.calcular_puntaje_total(respuestas)
    assert total == 80
    assert onboarding.calcular_perfil(total) == "muy_conservador"


def test_puntaje_maximo():
    respuestas = [max(score for _, score in opts) for _, opts in onboarding.QUESTIONS]
    total = onboarding.calcular_puntaje_total(respuestas)
    assert total == 400
    assert onboarding.calcular_perfil(total) == "agresivo"


def test_pregunta_7_tiene_3_opciones_maximo_40():
    texto, opciones = onboarding.QUESTIONS[6]
    assert len(opciones) == 3
    assert max(score for _, score in opciones) == 40
    assert min(score for _, score in opciones) == 10


# ---------------------------------------------------------------------------
# B5 — 8 valores límite de frontera (convención semiabierta)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "puntaje,perfil_esperado",
    [
        (80, "muy_conservador"),
        (119, "muy_conservador"),
        (120, "conservador"),
        (179, "conservador"),
        (180, "moderado"),
        (239, "moderado"),
        (240, "agresivo"),
        (400, "agresivo"),
    ],
)
def test_perfil_fronteras(puntaje, perfil_esperado):
    assert onboarding.calcular_perfil(puntaje) == perfil_esperado


# ---------------------------------------------------------------------------
# Flujo conversacional — simulado invocando los callbacks directamente
# (patrón estándar de test de python-telegram-bot, sin Application real)
# ---------------------------------------------------------------------------


def _fake_update_message():
    update = SimpleNamespace()
    update.message = SimpleNamespace(reply_text=AsyncMock())
    update.effective_chat = SimpleNamespace(id=ALLOWED_CHAT_ID, type="private")
    return update


def _fake_update_callback(callback_data: str):
    update = SimpleNamespace()
    query = SimpleNamespace(
        data=callback_data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update.callback_query = query
    update.message = None
    update.effective_chat = SimpleNamespace(id=ALLOWED_CHAT_ID, type="private")
    return update, query


class FakeContext:
    def __init__(self):
        self.user_data = {}


@pytest.fixture
def handler(tmp_path):
    # `:memory:` crea una DB nueva en cada conexión — el handler abre/cierra
    # su propia conexión por invocación, así que se usa un archivo temporal
    # para que las conexiones sucesivas vean el mismo estado (más fiel al
    # comportamiento real del bot, que usa un archivo en volumen Docker).
    db_path = str(tmp_path / "test_investbot.db")

    def get_conn():
        return db.get_connection(db_path)

    return onboarding.build_onboarding_handler(get_conn), get_conn


async def _run_full_conversation(handler, context, respuestas_indices):
    """Simula /start + las 8 respuestas (por índice de opción en cada pregunta)."""
    start_update = _fake_update_message()
    entry_callback = handler.entry_points[0].callback
    state = await entry_callback(start_update, context)

    for i, option_index in enumerate(respuestas_indices):
        _, opciones = onboarding.QUESTIONS[i]
        score = opciones[option_index][1]
        update, query = _fake_update_callback(f"onb:{i}:{score}")
        callback = handler.states[state][0].callback
        state = await callback(update, context)
    return state


async def test_flujo_conversacional_completo_8_preguntas(handler):
    conv_handler, get_conn = handler
    context = FakeContext()

    # Elige siempre la primera opción de cada pregunta (todas las respuestas
    # mínimas) -> puntaje total 80, perfil muy_conservador.
    indices = [0] * 8
    final_state = await _run_full_conversation(conv_handler, context, indices)

    from telegram.ext import ConversationHandler

    assert final_state == ConversationHandler.END

    conn = get_conn()
    profile = db.get_risk_profile(conn)
    conn.close()
    assert profile is not None
    assert profile["puntaje_total"] == 80
    assert profile["perfil"] == "muy_conservador"


async def test_flujo_sobrescritura_no_duplica(handler):
    conv_handler, get_conn = handler
    context = FakeContext()

    await _run_full_conversation(conv_handler, context, [0] * 8)
    context2 = FakeContext()
    await _run_full_conversation(conv_handler, context2, [-1] * 8)  # máximo de cada pregunta

    conn = get_conn()
    profile = db.get_risk_profile(conn)
    count = conn.execute("SELECT COUNT(*) as c FROM risk_profile").fetchone()["c"]
    conn.close()

    assert count == 1  # fila única, nunca duplica
    assert profile["puntaje_total"] == 400
    assert profile["perfil"] == "agresivo"


async def test_flujo_reinicio_no_mezcla_respuestas(handler):
    conv_handler, get_conn = handler
    context = FakeContext()

    entry_callback = conv_handler.entry_points[0].callback
    start_update = _fake_update_message()
    state = await entry_callback(start_update, context)

    # Responde 3 de 8 preguntas
    for i in range(3):
        _, opciones = onboarding.QUESTIONS[i]
        score = opciones[0][1]
        update, query = _fake_update_callback(f"onb:{i}:{score}")
        callback = conv_handler.states[state][0].callback
        state = await callback(update, context)

    assert len(context.user_data["respuestas"]) == 3

    # Reinicia con /start
    state = await entry_callback(start_update, context)
    assert context.user_data["respuestas"] == []


async def test_flujo_abandono_no_deja_fila_parcial(handler):
    conv_handler, get_conn = handler
    context = FakeContext()

    entry_callback = conv_handler.entry_points[0].callback
    start_update = _fake_update_message()
    state = await entry_callback(start_update, context)

    for i in range(4):  # responde 4 de 8, abandona
        _, opciones = onboarding.QUESTIONS[i]
        score = opciones[0][1]
        update, query = _fake_update_callback(f"onb:{i}:{score}")
        callback = conv_handler.states[state][0].callback
        state = await callback(update, context)

    conn = get_conn()
    db.init_db(conn)
    profile = db.get_risk_profile(conn)
    conn.close()
    assert profile is None  # nada persistido hasta completar las 8


# ---------------------------------------------------------------------------
# chat_id no autorizado nunca entra al flujo (cruce con `security`)
# ---------------------------------------------------------------------------


async def test_chat_id_no_autorizado_no_avanza_ni_escribe_en_db(handler):
    conv_handler, get_conn = handler
    gate = build_chat_id_gate(ALLOWED_CHAT_ID)

    unauthorized_update = SimpleNamespace()
    unauthorized_update.effective_chat = SimpleNamespace(id=99999, type="private")
    unauthorized_update.callback_query = None
    unauthorized_update.message = SimpleNamespace(reply_text=AsyncMock())

    from telegram.ext import ApplicationHandlerStop

    with pytest.raises(ApplicationHandlerStop):
        await gate(unauthorized_update, context=SimpleNamespace())

    # El gate corta la cadena antes de llegar a onboarding — nunca se invoca
    # el ConversationHandler ni se escribe en SQLite.
    conn = get_conn()
    db.init_db(conn)
    profile = db.get_risk_profile(conn)
    conn.close()
    assert profile is None


async def test_chat_id_no_autorizado_callback_query_rechazado():
    gate = build_chat_id_gate(ALLOWED_CHAT_ID)
    update, query = _fake_update_callback("onb:0:10")
    update.effective_chat = SimpleNamespace(id=99999, type="private")

    from telegram.ext import ApplicationHandlerStop

    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())

    query.answer.assert_awaited_once()  # no deja el spinner colgado
