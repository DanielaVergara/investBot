"""Auxiliares de los tests F1b-3 (`FundamentalsService`, `EdgarProvider`).

Sin tiempo real: `Clock` es el reloj inyectado; `Budget.sleep` solo termina cuando el
test lo libera (asi el presupuesto de 30 s se dispara a voluntad). Los proveedores
falsos se sincronizan con `asyncio.Event`, nunca con `sleep` real.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from investbot.fundamentals import FundamentalsResult


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def never_sleep(delay: float) -> None:
    """`sleep` que no termina: el presupuesto de 30 s no se dispara solo."""
    await asyncio.Event().wait()


class Budget:
    """`sleep` inyectado cuyo vencimiento controla el test (`released.set()`)."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.released = asyncio.Event()

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await self.released.wait()


def make_result(source: str = "sec_edgar", codes: tuple = (), marca: str = "x") -> FundamentalsResult:
    fila = [{"date": "2026-01-01", "marca": marca}]
    return FundamentalsResult(
        source=source,
        income_q=fila,
        income_a=[],
        balance_q=fila,
        balance_a=[],
        cash_q=fila,
        cash_a=[],
        own_metrics={"marca": marca},
        fallback_codes=codes,
        income_fuente="trimestral_real",
        cash_fuente="trimestral_real",
        balance_fuente="trimestral_real",
    )


class Fake:
    """Proveedor falso: responde en orden `script` (el ultimo se repite). Un item
    puede ser un resultado, `None`, o una excepcion (se lanza)."""

    def __init__(
        self,
        name: str,
        *script,
        cacheable: bool = True,
        gate: Optional[asyncio.Event] = None,
        clock: Optional[Clock] = None,
        cost: float = 0.0,
    ) -> None:
        self.name = name
        if cacheable:
            self.cacheable = True
        self.script = list(script)
        self.gate = gate
        self.clock = clock
        self.cost = cost
        self.calls: list[tuple] = []

    async def fetch(self, ticker: str, *, quarters: int, skip_own_metrics: bool = False):
        self.calls.append((ticker, quarters, skip_own_metrics))
        n = len(self.calls)
        if self.gate is not None:
            await self.gate.wait()
        if self.clock is not None and self.cost:
            self.clock.advance(self.cost)
        item = self.script[min(n, len(self.script)) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


async def spin(n: int = 20) -> None:
    """Cede el control `n` veces (deja avanzar a las demas tareas; no es tiempo real)."""
    for _ in range(n):
        await asyncio.sleep(0)
