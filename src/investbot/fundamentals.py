"""Capa de proveedores de estados financieros (SDD_fuentes_datos_gratuitas.md, 4.2, 4.3, 15.x).

`FundamentalsService.get` recorre la cadena `[EdgarProvider, FmpProvider]`: el
primero que entrega un paquete atomico gana; los que no pueden servir dejan un
codigo del enum cerrado `FALLBACK_CODES` (nunca texto libre). Si todos fallan se
re-lanza la excepcion del ULTIMO proveedor (el mensaje sanitizado de `FMPError`
no cambia).

El servicio guarda en memoria (estado por instancia, sin globales) el resultado
YA PARSEADO de los proveedores marcados `cacheable` (EDGAR; FMP no se cachea, su
comportamiento es el de siempre): TTL 6 h, negativo 24 h, stale-if-error 7 dias
con `cache_antiguo`, LRU 64, single-flight con `asyncio.shield` y presupuesto de
30 s por consulta (D-19). `now` y `sleep` son inyectables (CQ-4).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

from investbot import rules
from investbot.edgar_xbrl import PARSE_CODES

logger = logging.getLogger(__name__)

SOURCES = frozenset({"sec_edgar", "fmp"})

# Enum cerrado (15.3 fila 20). `edgar_xbrl` NO importa este modulo (CA-1).
FALLBACK_CODES = PARSE_CODES | frozenset(
    {
        "sin_cik",
        "sin_facts",
        "no_disponible_temporal",
        "circuito_abierto",
        "ua_no_configurado",
        "cache_antiguo",
        "respuesta_demasiado_grande",
        "redireccion_inesperada",
        "respuesta_inesperada",
        "bloqueado_por_sec",
    }
)

# Fallos del proveedor (no veredictos de cobertura): no se cachean en negativo y
# permiten servir una entrada vencida (stale-if-error).
TRANSIENT_CODES = frozenset(
    {
        "no_disponible_temporal",
        "circuito_abierto",
        "respuesta_inesperada",
        "redireccion_inesperada",
        "bloqueado_por_sec",
        "respuesta_demasiado_grande",
    }
)

TTL_POSITIVE = 6 * 3600.0
TTL_NEGATIVE = 24 * 3600.0
STALE_MAX = 7 * 24 * 3600.0
CACHE_MAX = 64  # CS-16 (antes 256)
BUDGET_SECONDS = 30.0  # D-19

_LOG_TICKER_RE = re.compile(r"[A-Za-z0-9.\-]{1,10}", re.ASCII)


class ProviderUnavailable(Exception):
    """El proveedor no puede servir este ticker ahora; `code` explica por que
    (enum cerrado). Cubre tanto "sin cobertura" como un fallo transitorio."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in FALLBACK_CODES:
            raise ValueError("codigo fuera del enum cerrado FALLBACK_CODES")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FundamentalsResult:
    """Paquete atomico por proveedor (4.2).

    `income_q`/`cash_q`/`balance_q` son lo pedido en modalidad trimestral
    (`[]` si falló o vino vacío); `income_a`/`cash_a`/`balance_a` solo se
    llenan cuando hizo falta el fallback anual (FMP) o son la serie anual
    (EDGAR). Los tres `*_fuente` son los flags de fuente que ya existían en
    `query_handler`. `source` y cada `fallback_codes` pertenecen a un enum
    cerrado (CS-24): un valor libre lanza `ValueError` al construir.
    """

    source: str
    income_q: list[dict]
    income_a: list[dict]
    balance_q: list[dict]
    balance_a: list[dict]
    cash_q: list[dict]
    cash_a: list[dict]
    own_metrics: Optional[dict]
    fallback_codes: tuple[str, ...] = ()
    income_fuente: str = rules.DATOS_FUENTE_ANUAL_FALLBACK
    cash_fuente: str = rules.DATOS_FUENTE_ANUAL_FALLBACK
    balance_fuente: str = rules.DATOS_FUENTE_ANUAL_FALLBACK

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or self.source not in SOURCES:
            raise ValueError("source fuera del enum cerrado")
        codes = tuple(self.fallback_codes)
        for code in codes:
            if not isinstance(code, str) or code not in FALLBACK_CODES:
                raise ValueError("fallback_code fuera del enum cerrado FALLBACK_CODES")
        object.__setattr__(self, "fallback_codes", codes)

    @property
    def income_statements(self) -> list[dict]:
        return self.income_q if self.income_fuente == rules.DATOS_FUENTE_TRIMESTRAL else self.income_a

    @property
    def cash_flows(self) -> list[dict]:
        return self.cash_q if self.cash_fuente == rules.DATOS_FUENTE_TRIMESTRAL else self.cash_a

    @property
    def balance_sheets(self) -> list[dict]:
        return self.balance_q if self.balance_fuente == rules.DATOS_FUENTE_TRIMESTRAL else self.balance_a


class FundamentalsProvider(Protocol):
    name: str

    async def fetch(
        self, ticker: str, *, quarters: int, skip_own_metrics: bool = False
    ) -> Optional[FundamentalsResult]:
        """None = sin cobertura (no es un error). `ProviderUnavailable(code)` =
        no puedo servir (cobertura o fallo transitorio) con su motivo. Cualquier
        otra excepcion se propaga."""


@dataclass(frozen=True)
class _Entry:
    stored_at: float
    result: Optional[FundamentalsResult]  # None = veredicto negativo
    code: Optional[str]


@dataclass(frozen=True)
class _Outcome:
    result: Optional[FundamentalsResult]
    code: Optional[str]


def _norm(ticker: str) -> str:
    return str(ticker).upper().replace(".", "-")


class FundamentalsService:
    def __init__(
        self,
        providers: list[FundamentalsProvider],
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        budget: float = BUDGET_SECONDS,
    ) -> None:
        self._providers = list(providers)
        self._now = now
        self._sleep = sleep
        self._budget = budget
        self._cache: "OrderedDict[tuple, _Entry]" = OrderedDict()
        self._inflight: dict[tuple, asyncio.Future] = {}

    # ------------------------------------------------------------------ cadena
    async def get(
        self,
        ticker: str,
        *,
        quarters: int,
        skip_own_metrics: bool = False,
        only: Optional[frozenset[str]] = None,
    ) -> Optional[FundamentalsResult]:
        """`only` (4.10 pt 7, `/avanzado`): nombres de proveedor a consultar; el
        default `None` recorre toda la cadena como siempre. Con `only` los demás
        proveedores NO se llaman (no gastan presupuesto de FMP): si ninguno de
        los pedidos sirve, devuelve `None` y quien llama sigue con su camino
        habitual."""
        started = self._now()
        codes: list[str] = []
        last_exc: Optional[Exception] = None
        result: Optional[FundamentalsResult] = None
        for provider in self._providers:
            if only is not None and provider.name not in only:
                continue
            try:
                if getattr(provider, "cacheable", False):
                    found = await self._cached(provider, ticker, quarters, skip_own_metrics)
                else:
                    found = await provider.fetch(
                        ticker, quarters=quarters, skip_own_metrics=skip_own_metrics
                    )
            except ProviderUnavailable as exc:
                codes.append(exc.code)
                continue
            except Exception as exc:
                last_exc = exc
                codes.append("no_disponible_temporal")
                continue
            if found is not None:
                result = found
                break
        if result is None:
            if last_exc is not None:
                raise last_exc
            return None
        previos = tuple(dict.fromkeys(codes))
        if previos:
            merged = tuple(dict.fromkeys((*previos, *result.fallback_codes)))
            result = dataclasses.replace(result, fallback_codes=merged)
        self._log_once(ticker, result, started)
        return result

    def _log_once(self, ticker: str, result: FundamentalsResult, started: float) -> None:
        """CA-33: UN log INFO por consulta, sin cuerpo, headers ni User-Agent."""
        safe = ticker if isinstance(ticker, str) and _LOG_TICKER_RE.fullmatch(ticker) else "?"
        fallback = ",".join(result.fallback_codes) or "-"
        ms = max(0, int((self._now() - started) * 1000))
        logger.info(
            "fundamentals ticker=%s source=%s fallback=%s ms=%d", safe, result.source, fallback, ms
        )

    # -------------------------------------------------------------------- caché
    async def _cached(
        self, provider: FundamentalsProvider, ticker: str, quarters: int, skip_own_metrics: bool
    ) -> Optional[FundamentalsResult]:
        key = (provider.name, _norm(ticker), quarters)
        entry = self._cache.get(key)
        if entry is not None:
            age = self._now() - entry.stored_at
            fresh = TTL_POSITIVE if entry.result is not None else TTL_NEGATIVE
            if age < fresh:
                self._cache.move_to_end(key)
                if entry.result is not None:
                    return entry.result
                if entry.code is not None:
                    raise ProviderUnavailable(entry.code)
                return None
        flight = self._inflight.get(key)
        if flight is None:
            flight = asyncio.ensure_future(
                self._refresh(key, provider, ticker, quarters, skip_own_metrics)
            )
            self._inflight[key] = flight
            flight.add_done_callback(_consume)
        outcome = await self._wait(flight)
        if outcome is None:
            raise ProviderUnavailable("no_disponible_temporal")  # D-19
        if outcome.result is not None:
            return outcome.result
        if outcome.code is not None:
            raise ProviderUnavailable(outcome.code)
        return None

    async def _wait(self, flight: asyncio.Future) -> Optional[_Outcome]:
        """Espera la tarea compartida hasta el presupuesto (`None` si se agota).
        Cancelar al que espera, o agotar el presupuesto, NO cancela la tarea."""
        waiter = asyncio.shield(flight)
        timer = asyncio.ensure_future(self._sleep(self._budget))
        try:
            await asyncio.wait({waiter, timer}, return_when=asyncio.FIRST_COMPLETED)
            return waiter.result() if waiter.done() else None
        finally:
            timer.cancel()
            waiter.cancel()

    async def _refresh(
        self,
        key: tuple,
        provider: FundamentalsProvider,
        ticker: str,
        quarters: int,
        skip_own_metrics: bool,
    ) -> _Outcome:
        try:
            code: Optional[str] = None
            try:
                found = await provider.fetch(
                    ticker, quarters=quarters, skip_own_metrics=skip_own_metrics
                )
            except ProviderUnavailable as exc:
                found, code = None, exc.code
            except Exception as exc:
                logger.warning("proveedor %s fallo inesperado (%s)", provider.name, type(exc).__name__)
                found, code = None, "no_disponible_temporal"
            now = self._now()
            if found is not None:
                self._store(key, _Entry(now, found, None))
                return _Outcome(found, None)
            if code in TRANSIENT_CODES:
                stale = self._cache.get(key)
                if stale is not None and stale.result is not None and now - stale.stored_at <= STALE_MAX:
                    viejo = dataclasses.replace(
                        stale.result,
                        fallback_codes=(*stale.result.fallback_codes, "cache_antiguo"),
                    )
                    return _Outcome(viejo, None)
                return _Outcome(None, code)
            self._store(key, _Entry(now, None, code))
            return _Outcome(None, code)
        finally:
            self._inflight.pop(key, None)

    def _store(self, key: tuple, entry: _Entry) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)


def _consume(task: asyncio.Future) -> None:
    """Marca la excepcion de la tarea compartida como recuperada (sin ruido)."""
    task.cancelled() or task.exception()
