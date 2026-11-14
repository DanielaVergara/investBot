"""Helpers de los tests de F2a (SDD_fuentes_datos_gratuitas.md): reloj falso,
payloads SINTÉTICOS de Finnhub (CS-26; claves NO VERIFICADAS contra la API real) y
un handler que registra peticiones. Sin red ni tiempo reales."""

from __future__ import annotations

import asyncio

import httpx

KEY = "CANARY-FH-KEY-0123456789"
WALL0 = 1_780_000_000.0  # "ahora" sintético (epoch)


class Clock:
    """Reloj falso: `sleep` solo adelanta el reloj y nunca duerme de verdad."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def wall(self) -> float:
        return WALL0 + (self.t - 1000.0)

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.t += delay
        await asyncio.sleep(0)


def quote(c=187.5, t=WALL0 - 60) -> dict:
    return {"c": c, "d": 1.2, "dp": 0.64, "h": 190.0, "l": 185.0, "o": 186.0, "pc": 186.3, "t": t}


def profile(**over) -> dict:
    base = {
        "name": "Empresa Sintetica SA",
        "ticker": "SYNT",
        "exchange": "NASDAQ",
        "country": "US",
        "currency": "USD",
        "marketCapitalization": 2000.0,  # millones
        "shareOutstanding": 10.0,  # millones
        "finnhubIndustry": "CANARIO_FH",
    }
    base.update(over)
    return base


def metric(**over) -> dict:
    m = {"beta": 1.25, "52WeekHigh": 200.0, "52WeekLow": 120.0, "peTTM": 31.0}
    m.update(over)
    return {"metric": m, "series": {"annual": {}}, "metricType": "all", "symbol": "SYNT"}


ROUTES = {"/api/v1/quote": quote, "/api/v1/stock/profile2": profile, "/api/v1/stock/metric": metric}


class Recorder:
    """Handler que registra peticiones y responde según un guion o las rutas por defecto."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if self.script:
            item = self.script[min(len(self.requests), len(self.script)) - 1]
            if isinstance(item, Exception):
                raise item
            if callable(item):
                return item(request)
            return item
        return httpx.Response(200, json=ROUTES[request.url.path]())
