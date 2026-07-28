"""Tests de `db.py` — persistencia SQLite (fila única, sobrescritura)."""

from __future__ import annotations

import pytest

from investbot import db


def test_init_db_idempotente(in_memory_conn):
    db.init_db(in_memory_conn)  # segunda llamada no debe fallar
    assert db.get_risk_profile(in_memory_conn) is None


def test_save_and_get_risk_profile(in_memory_conn):
    respuestas = [10, 20, 30, 10, 20, 10, 10, 10]
    db.save_risk_profile(in_memory_conn, respuestas, 120, "conservador", "2026-01-01T00:00:00+00:00")
    profile = db.get_risk_profile(in_memory_conn)
    assert profile["puntaje_total"] == 120
    assert profile["perfil"] == "conservador"
    assert profile["respuesta_1"] == 10


def test_save_risk_profile_sobrescribe(in_memory_conn):
    db.save_risk_profile(
        in_memory_conn, [10] * 8, 80, "muy_conservador", "2026-01-01T00:00:00+00:00"
    )
    db.save_risk_profile(
        in_memory_conn, [50] * 8, 400, "agresivo", "2026-01-02T00:00:00+00:00"
    )
    profile = db.get_risk_profile(in_memory_conn)
    assert profile["puntaje_total"] == 400
    assert profile["perfil"] == "agresivo"

    count = in_memory_conn.execute("SELECT COUNT(*) as c FROM risk_profile").fetchone()["c"]
    assert count == 1


def test_save_risk_profile_respuestas_invalidas(in_memory_conn):
    with pytest.raises(ValueError):
        db.save_risk_profile(in_memory_conn, [10, 20], 30, "conservador", "2026-01-01")


def test_save_risk_profile_perfil_invalido(in_memory_conn):
    with pytest.raises(ValueError):
        db.save_risk_profile(in_memory_conn, [10] * 8, 80, "no_existe", "2026-01-01")


def test_has_completed_onboarding(in_memory_conn):
    assert db.has_completed_onboarding(in_memory_conn) is False
    db.save_risk_profile(
        in_memory_conn, [10] * 8, 80, "muy_conservador", "2026-01-01T00:00:00+00:00"
    )
    assert db.has_completed_onboarding(in_memory_conn) is True
