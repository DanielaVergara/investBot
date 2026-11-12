"""Canarios de la fixture anti-red (CS-16).

Cada test provoca un intento de red a propósito: el parche lo registra y
lanza `AssertionError`. Al final se vacía el registro para que el teardown
de la fixture autouse no falle el propio canario.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from .network_guard import NetworkGuard


async def test_cs16_asynchttptransport_bloqueado(net_guard):
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="red real bloqueado"):
            await client.get("https://example.invalid/x")
    assert len(net_guard.attempts) == 1
    assert "AsyncHTTPTransport" in net_guard.attempts[0]
    net_guard.attempts.clear()


def test_cs16_httptransport_bloqueado(net_guard):
    with httpx.Client() as client:
        with pytest.raises(AssertionError, match="red real bloqueado"):
            client.get("https://example.invalid/x")
    assert len(net_guard.attempts) == 1
    assert "HTTPTransport" in net_guard.attempts[0]
    net_guard.attempts.clear()


def test_cs16_socket_connect_bloqueado(net_guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="red real bloqueado"):
            s.connect(("203.0.113.1", 9))
    finally:
        s.close()
    assert len(net_guard.attempts) == 1
    assert "socket.socket.connect " in net_guard.attempts[0]
    net_guard.attempts.clear()


def test_cs16_socket_connect_ex_bloqueado(net_guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="red real bloqueado"):
            s.connect_ex(("203.0.113.1", 9))
    finally:
        s.close()
    assert len(net_guard.attempts) == 1
    assert "connect_ex" in net_guard.attempts[0]
    net_guard.attempts.clear()


async def test_cs16_intento_tragado_por_except_exception_se_detecta(net_guard):
    swallowed = False
    async with httpx.AsyncClient() as client:
        try:
            await client.get("https://example.invalid/x")
        except Exception:  # el código bajo prueba traga el AssertionError
            swallowed = True
    assert swallowed is True
    assert len(net_guard.attempts) == 1
    with pytest.raises(AssertionError):
        net_guard.assert_clean()
    net_guard.attempts.clear()
    net_guard.assert_clean()  # control positivo: limpio tras vaciar


async def test_cs16_mocktransport_sigue_funcionando(net_guard):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await client.get("https://example.invalid/x")
    assert resp.json() == {"ok": True}
    assert net_guard.attempts == []


def test_cs16_networkguard_limpia_no_lanza_y_sucia_si_lanza():
    guard = NetworkGuard()
    guard.assert_clean()
    msg = guard.record("x")
    assert "x" in msg
    with pytest.raises(AssertionError):
        guard.assert_clean()


def test_cs16_entorno_gemini_live_limpio_y_api_key_intacta(monkeypatch):
    """Ejecuta el cuerpo de la fixture con el entorno sucio a propósito."""
    import os

    from . import conftest

    dummy = "TEST-DUMMY-KEY-0123456789ABCDEF"
    for var in ("GEMINI_LIVE_KEY", "GEMINI_LIVE_CONFIRM", "GEMINI_BASE_URL"):
        monkeypatch.setenv(var, "valor-sucio")
    monkeypatch.setenv("GEMINI_API_KEY", dummy)
    assert os.environ["GEMINI_LIVE_KEY"] == "valor-sucio"  # control previo

    gen = conftest.net_guard.__wrapped__(monkeypatch)
    inner_guard = next(gen)
    assert isinstance(inner_guard, NetworkGuard)
    assert "GEMINI_LIVE_KEY" not in os.environ
    assert "GEMINI_LIVE_CONFIRM" not in os.environ
    assert "GEMINI_BASE_URL" not in os.environ
    assert os.environ["GEMINI_API_KEY"] == dummy
    with pytest.raises(StopIteration):  # teardown limpio: sin intentos
        next(gen)
