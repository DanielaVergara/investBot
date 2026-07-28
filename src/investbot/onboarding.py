"""Cuestionario de perfil de riesgo — 8 preguntas vía `ConversationHandler`.

Texto y puntajes reproducidos literalmente de la sección "Cuestionario de
perfil de riesgo" de la spec — no parafrasear. Cada pregunta se responde por
botones inline (nunca texto libre) para evitar puntajes inválidos por typos.

El cálculo de puntaje/perfil vive en funciones puras (`calcular_puntaje_total`,
`calcular_perfil`), testeables sin simular la conversación completa (criterio
de `qa`, sección 6).
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from typing import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from investbot import db

logger = logging.getLogger(__name__)

# Cada pregunta: (texto literal, [(etiqueta, puntaje), ...])
QUESTIONS: list[tuple[str, list[tuple[str, int]]]] = [
    (
        "1. Edad:",
        [
            (">60", 10),
            ("50-60", 20),
            ("40-49", 30),
            ("30-39", 40),
            ("<30", 50),
        ],
    ),
    (
        "2. Plazo de inversión:",
        [
            ("<1 año", 10),
            ("1-2 años", 20),
            ("2-5 años", 30),
            ("5-10 años", 40),
            (">10 años", 50),
        ],
    ),
    (
        "3. % de ahorros dispuesto a invertir:",
        [
            ("<30%", 10),
            ("30-60%", 30),
            (">60%", 50),
        ],
    ),
    (
        "4. Objetivo de inversión:",
        [
            ("Asegurar y mantener capital", 10),
            ("Ingresos moderados mensuales", 20),
            ("Aumentar patrimonio con retornos estables", 40),
            ("Aumentar patrimonio sin importar riesgos", 50),
        ],
    ),
    (
        "5. Fondo de emergencia:",
        [
            ("Nada", 10),
            ("1 mes de gastos", 20),
            ("3 meses", 30),
            ("6 meses", 40),
            ("Más de 1 año", 50),
        ],
    ),
    (
        "6. Experiencia en inversiones:",
        [
            ("Nunca he invertido", 10),
            ("Fiducuenta o CDT", 20),
            ("Presto plata a familia/amigos", 30),
            ("Finca raíz", 40),
            ("Bolsa", 50),
            ("Productos alternativos: cripto, crowdfunding", 60),
        ],
    ),
    (
        "7. Tolerancia a rendimientos negativos:",
        [
            ("Prefiero seguridad y disponibilidad a corto plazo", 10),
            ("Me interesan inversiones a mediano plazo con rentabilidad baja pero estable", 30),
            ("Prefiero rentabilidad alta aunque haya años con rendimientos negativos", 40),
        ],
    ),
    (
        "8. Reacción si la inversión pierde valor:",
        [
            ("Vende inmediatamente", 10),
            ("Consulta a un experto pero mantiene la calma", 30),
            ("Asume pérdidas a corto plazo, espera ganancias a largo plazo", 50),
        ],
    ),
]

PUNTAJE_MINIMO = sum(min(score for _, score in opts) for _, opts in QUESTIONS)
PUNTAJE_MAXIMO = sum(max(score for _, score in opts) for _, opts in QUESTIONS)

Q_STATES = list(range(len(QUESTIONS)))  # 0..7

PERFIL_LABELS = {
    "muy_conservador": "Muy Conservador",
    "conservador": "Conservador",
    "moderado": "Moderado",
    "agresivo": "Agresivo",
}


def calcular_puntaje_total(respuestas: list[int]) -> int:
    """Suma de las 8 respuestas."""
    return sum(respuestas)


def calcular_perfil(puntaje: int) -> str:
    """Mapeo puntaje → perfil, convención semiabierta confirmada en B5 (Spec Patch Iter-2).

    [80,120) Muy Conservador | [120,180) Conservador | [180,240) Moderado | [240,∞) Agresivo
    """
    if puntaje < 120:
        return "muy_conservador"
    elif puntaje < 180:
        return "conservador"
    elif puntaje < 240:
        return "moderado"
    else:
        return "agresivo"


def _keyboard_for(state: int) -> InlineKeyboardMarkup:
    _, opciones = QUESTIONS[state]
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"onb:{state}:{score}")]
        for label, score in opciones
    ]
    return InlineKeyboardMarkup(buttons)


async def _send_question(update_or_query, state: int) -> None:
    text, _ = QUESTIONS[state]
    keyboard = _keyboard_for(state)
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, reply_markup=keyboard)


def build_onboarding_handler(
    get_conn: Callable[[], sqlite3.Connection],
) -> ConversationHandler:
    """Construye el `ConversationHandler` de 8 preguntas.

    `get_conn` es una fábrica de conexión SQLite inyectada (no un cliente
    global de módulo) para poder testear con `:memory:`.
    """

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # Re-ejecutable: reinicia el estado sin mezclar respuestas de una
        # corrida anterior (allow_reentry=True permite volver a entrar aquí).
        context.user_data["respuestas"] = []
        await update.message.reply_text(
            "Vamos a fijar tu perfil de riesgo — 8 preguntas rápidas, respondé con los botones."
        )
        await _send_question(update, 0)
        return Q_STATES[0]

    def make_question_callback(state: int):
        async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
            query = update.callback_query
            await query.answer()
            try:
                _, state_str, score_str = query.data.split(":")
                score = int(score_str)
                assert int(state_str) == state
            except (ValueError, AssertionError, AttributeError):
                logger.warning("callback_data de onboarding inválido")
                return state

            respuestas = context.user_data.setdefault("respuestas", [])
            respuestas.append(score)

            next_state = state + 1
            if next_state < len(QUESTIONS):
                await _send_question(query, next_state)
                return Q_STATES[next_state]

            # Completó las 8 respuestas — calcular, persistir, confirmar.
            puntaje_total = calcular_puntaje_total(respuestas)
            perfil = calcular_perfil(puntaje_total)
            completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            conn = get_conn()
            try:
                db.init_db(conn)
                db.save_risk_profile(conn, respuestas, puntaje_total, perfil, completed_at)
            finally:
                conn.close()

            await query.edit_message_text(
                f"Listo — tu perfil de riesgo es *{PERFIL_LABELS[perfil]}* "
                f"(puntaje total: {puntaje_total}/{PUNTAJE_MAXIMO}). "
                "Ya podés mandarme un ticker o nombre de empresa para analizarlo.",
                parse_mode="Markdown",
            )
            context.user_data["respuestas"] = []
            return ConversationHandler.END

        return _handler

    states = {
        Q_STATES[i]: [
            CallbackQueryHandler(make_question_callback(i), pattern=rf"^onb:{i}:")
        ]
        for i in range(len(QUESTIONS))
    }

    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states=states,
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
        name="onboarding",
    )
