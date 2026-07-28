"""Acceso a SQLite — tabla `risk_profile` (fila única, un solo usuario).

Sin ORM (stdlib `sqlite3`), consistente con la Decisión de diseño #3 de la spec:
un único archivo SQLite, una única fila lógica (`id = 1`), sin justificar PostgreSQL
para un solo usuario/un solo registro.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  respuesta_1 INTEGER, respuesta_2 INTEGER, respuesta_3 INTEGER, respuesta_4 INTEGER,
  respuesta_5 INTEGER, respuesta_6 INTEGER, respuesta_7 INTEGER, respuesta_8 INTEGER,
  puntaje_total INTEGER NOT NULL,
  perfil TEXT NOT NULL CHECK (perfil IN ('muy_conservador','conservador','moderado','agresivo')),
  completed_at TEXT NOT NULL
);
"""

PERFILES_VALIDOS = ("muy_conservador", "conservador", "moderado", "agresivo")


def get_connection(db_path: str) -> sqlite3.Connection:
    """Abre (creando el directorio padre si hace falta) una conexión SQLite.

    `db_path` puede ser ":memory:" para tests.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Crea la tabla `risk_profile` si no existe. Idempotente."""
    conn.execute(_SCHEMA)
    conn.commit()


def save_risk_profile(
    conn: sqlite3.Connection,
    respuestas: list[int],
    puntaje_total: int,
    perfil: str,
    completed_at: str,
) -> None:
    """Persiste el perfil de riesgo, sobrescribiendo cualquier registro anterior.

    `respuestas` debe tener exactamente 8 enteros (respuesta_1..respuesta_8).
    Usa `INSERT OR REPLACE` sobre la fila fija `id=1` — vuelve a correr `/start`
    y completar el cuestionario reemplaza el perfil anterior (nunca duplica ni
    promedia con el resultado previo).
    """
    if len(respuestas) != 8:
        raise ValueError(f"Se esperaban 8 respuestas, se recibieron {len(respuestas)}")
    if perfil not in PERFILES_VALIDOS:
        raise ValueError(f"Perfil inválido: {perfil!r}")

    conn.execute(
        """
        INSERT OR REPLACE INTO risk_profile (
            id, respuesta_1, respuesta_2, respuesta_3, respuesta_4,
            respuesta_5, respuesta_6, respuesta_7, respuesta_8,
            puntaje_total, perfil, completed_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*respuestas, puntaje_total, perfil, completed_at),
    )
    conn.commit()


def get_risk_profile(conn: sqlite3.Connection) -> Optional[dict]:
    """Devuelve el perfil de riesgo persistido (fila id=1) o None si no existe."""
    row = conn.execute("SELECT * FROM risk_profile WHERE id = 1").fetchone()
    if row is None:
        return None
    return dict(row)


def has_completed_onboarding(conn: sqlite3.Connection) -> bool:
    """True si ya existe un perfil de riesgo persistido."""
    return get_risk_profile(conn) is not None
