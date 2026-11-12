"""Fixtures compartidas de pytest.

No usa red real: los clientes HTTP se construyen con `httpx.MockTransport`
sobre datos leídos de `tests/fixtures/`. Ningún test depende de
`FMP_API_KEY`/`FRED_API_KEY`/`TELEGRAM_BOT_TOKEN` reales (correr con
`os.environ` limpio o con valores dummy funciona igual).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

from investbot import gemini_client

from .gemini_helpers import SleepRecorder
from .network_guard import NetworkGuard

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(*parts: str):
    path = FIXTURES_DIR.joinpath(*parts)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def adobe_fixtures():
    return {
        "quote": load_fixture("adobe", "quote.json"),
        "profile": load_fixture("adobe", "profile.json"),
        "income_statement": load_fixture("adobe", "income_statement.json"),
        "balance_sheet": load_fixture("adobe", "balance_sheet.json"),
        "cash_flow": load_fixture("adobe", "cash_flow.json"),
        "peers_metrics": load_fixture("adobe", "peers_metrics.json"),
        "fred_dgs20": load_fixture("adobe", "fred_dgs20.json"),
        "key_metrics_own": load_fixture("adobe", "key_metrics_own.json"),
        "quote_vix": load_fixture("adobe", "quote_vix.json"),
        # SDD_eps_ttm_real.md — fixtures reales de NVDA (4 trimestres,
        # `period=quarter`), usadas por `_adobe_router` (tests/test_query_
        # handler.py) para servir el camino feliz trimestral cuando
        # `symbol=NVDA`. Origen: real (`curl` 2026-07-31, ver
        # tests/fixtures/fmp/README.md) — no confundir con los demás valores
        # de este fixture, que siguen siendo sintéticos calibrados para ADBE.
        "income_statement_quarterly_nvda": load_fixture(
            "fmp", "income_statement_quarterly_nvda_real.json"
        ),
        "balance_sheet_quarterly_nvda": load_fixture(
            "fmp", "balance_sheet_quarterly_nvda_real.json"
        ),
        "cash_flow_quarterly_nvda": load_fixture(
            "fmp", "cash_flow_quarterly_nvda_real.json"
        ),
    }


@pytest.fixture
def in_memory_conn():
    from investbot import db

    conn = db.get_connection(":memory:")
    db.init_db(conn)
    yield conn
    conn.close()


# --- Anti-red (CS-16, SDD_migracion_gemini.md) ------------------------------


@pytest.fixture(autouse=True)
def net_guard(monkeypatch):
    """Falla cualquier intento de red real, aun si el código lo traga.

    Parchea los dos transportes de httpx y `socket.socket.connect`/
    `connect_ex`. Cada parche registra el intento en la guarda y lanza
    `AssertionError`; el teardown afirma que no hubo intentos, así un
    `except Exception` del código bajo prueba no puede ocultarlos.
    `httpx.MockTransport` no pasa por estos parches. Limpia del entorno las
    variables del smoke vivo y de la URL base (no `GEMINI_API_KEY`).
    """
    guard = NetworkGuard()

    async def _blocked_async(self, request, *args, **kwargs):
        raise AssertionError(
            guard.record(f"httpx.AsyncHTTPTransport -> {request.method} {request.url.host}")
        )

    def _blocked_sync(self, request, *args, **kwargs):
        raise AssertionError(
            guard.record(f"httpx.HTTPTransport -> {request.method} {request.url.host}")
        )

    def _blocked_connect(self, address, *args, **kwargs):
        raise AssertionError(guard.record(f"socket.socket.connect -> {address!r}"))

    def _blocked_connect_ex(self, address, *args, **kwargs):
        raise AssertionError(guard.record(f"socket.socket.connect_ex -> {address!r}"))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_sync)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    for var in ("GEMINI_LIVE_KEY", "GEMINI_LIVE_CONFIRM", "GEMINI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    yield guard
    guard.assert_clean()


# --- Cero sleeps reales en rutas Gemini (P-14, CQ-12) --------------------------


@pytest.fixture(autouse=True)
def gemini_sleeps(monkeypatch):
    """Parchea `gemini_client._sleep` con un registrador que nunca duerme.

    Los tests de integración (p. ej. 500/503 vía `_fetch_explanation`) leen
    `gemini_sleeps.calls`; los tests del cliente inyectan su propio
    registrador por el parámetro `sleep`.
    """
    recorder = SleepRecorder()
    monkeypatch.setattr(gemini_client, "_sleep", recorder)
    return recorder
