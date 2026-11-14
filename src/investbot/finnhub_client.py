"""Wrapper HTTP a Finnhub — peers dinámicos (Feature 1) y, desde F2a de
`SDD_fuentes_datos_gratuitas.md`, `/quote`, `/stock/profile2` y `/stock/metric`.

Fuente PRIMARIA para la lista de peers candidatos por ticker. Fallback si
falla o no está configurada: PEERS_BY_SECTOR hardcodeado en peers.py (la
lista fija pasa de "única fuente" a "respaldo", no se elimina).

Finnhub tiene antecedente documentado (GitHub issue finnhubio/Finnhub-API#271)
de mover endpoints de gratis a pago sin aviso -- ese issue puntual nombra
"Dividends"/"Major Developments", no "Company Peers", pero este cliente trata
CUALQUIER fallo (402/403/429/timeout/red/JSON inesperado) exactamente igual:
fallback silencioso a la lista fija, nunca un error visible al usuario, nunca
una excepción cruda propagada.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import unicodedata
import weakref
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 10.0


class FinnhubError(Exception):
    """Error sanitizado de Finnhub -- nunca incluye la URL ni la API key.

    `code` (opcional) es un código del enum cerrado de F2 (`CODE_*`); `get_peers`
    no lo usa."""

    def __init__(self, message: str = "", code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


async def get_peers(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    *,
    grouping: str = "subIndustry",
    guarded: bool = False,
    budget: Optional["QueryBudget"] = None,
) -> list[str]:
    """Peers dinámicos de Finnhub para `symbol`. `grouping`: "sector" |
    "industry" | "subIndustry" (default de Finnhub y de este wrapper --
    Pregunta 1.1 RESUELTA por Daniela: subIndustry, para atacar la causa
    raíz del caso NVIDIA -- comparables angostos por sub-industria en vez
    del sector GICS completo). `query_handler.py` no pasa un `grouping`
    distinto -- no hay override configurable, es el valor fijo a usar.

    Nunca propaga excepciones crudas de httpx -- las traduce a FinnhubError.
    El llamador (query_handler.py, vía closure inyectada a peers.py) es
    responsable de capturarla y devolver [] para activar el fallback
    (mismo contrato que _get_metrics_for_peer ya usa con FMPError hoy).

    `guarded` (F2b, SDD_fuentes_datos_gratuitas.md 13.4 / CA-30): con `True` la
    llamada pasa por el mismo kill-switch, límite de tasa y presupuesto que
    `/quote`, `/stock/profile2` y `/stock/metric`: un 401/402/403/429 abre el
    breaker de Finnhub (los demás bloques degradan a FMP), y con el breaker
    abierto, sin key válida, sin presupuesto o sin cupo de tasa devuelve `[]`
    (peers sigue siendo no fatal: el llamador cae a `PEERS_BY_SECTOR`). Con
    `False` (default) el comportamiento es el de siempre, sin estado compartido.
    """
    if guarded:
        sym = _valid_symbol(symbol)
        if sym is None:
            return []
        payload, _code = await _guarded_get(
            client, api_key, "/stock/peers", {"symbol": sym, "grouping": grouping}, budget,
            accept=list,
        )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, str)]
    url = f"{BASE_URL}/stock/peers"
    try:
        response = await client.get(
            url,
            params={"symbol": symbol, "grouping": grouping},
            # CS-19 (b): el token va por header, nunca en la URL (NO VERIFICADO
            # contra Finnhub real: la usuaria lo prueba antes de desplegar, H-5).
            headers={"X-Finnhub-Token": api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("Finnhub timeout — endpoint=/stock/peers")
        raise FinnhubError("Finnhub no respondió a tiempo.") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("Finnhub error HTTP — endpoint=/stock/peers status=%s", status)
        raise FinnhubError(f"Finnhub respondió con un error ({status}).") from None
    except httpx.RequestError:
        logger.warning("Finnhub error de red — endpoint=/stock/peers")
        raise FinnhubError("No pude conectarme a Finnhub ahora mismo.") from None

    try:
        data: Any = response.json()
    except ValueError:
        logger.warning("Finnhub respuesta no-JSON — endpoint=/stock/peers")
        raise FinnhubError("Finnhub devolvió una respuesta inesperada.") from None

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]


# =============================================================================
# F2a (SDD_fuentes_datos_gratuitas.md, CA-25, CA-30, CS-19, CS-27): `/quote`,
# `/stock/profile2` y `/stock/metric` con header `X-Finnhub-Token`, breaker /
# kill-switch, límite de tasa propio y presupuesto de espera por consulta.
#
# NO VERIFICADO contra la API real (la usuaria hará una captura local, sin
# commitear): (a) el header `X-Finnhub-Token` (H-5); (b) los nombres de claves
# de `/stock/profile2` (`name`, `currency`, `marketCapitalization` en millones,
# `shareOutstanding` en millones) y de `/stock/metric?metric=all`
# (`metric.beta`, `metric.52WeekHigh`, `metric.52WeekLow`): salen del swagger
# oficial y de su sample, no de una respuesta real; (c) que un símbolo
# desconocido devuelva ceros / `{}`; (d) el tope de 60 llamadas/min del plan.
# Si una clave real difiere, el campo queda `None` (N/D), nunca inventado.
#
# Estado (breaker, ventana de tasa) por cliente httpx, como `SecGate`: ninguna
# variable global; `now`, `sleep` y `wall` son inyectables (sin tiempo real).
# =============================================================================

# Códigos de resultado (enum cerrado; texto fijo, nunca contenido de red).
CODE_NO_KEY = "sin_key"
CODE_NO_DATA = "sin_dato"
CODE_STALE_QUOTE = "cotizacion_desactualizada"
CODE_BUDGET = "presupuesto_agotado"
CODE_RATE = "limite_de_tasa"
CODE_CIRCUIT_OPEN = "circuito_abierto"
CODE_REJECTED = "finnhub_rechazado"  # 401/402/403/429: abre el kill-switch
CODE_UNAVAILABLE = "finnhub_no_disponible"
CODE_NOT_USD = "moneda_no_usd"
CODE_CAP_INCOHERENT = "capitalizacion_incoherente"
FINNHUB_CODES = frozenset(
    {
        CODE_NO_KEY,
        CODE_NO_DATA,
        CODE_STALE_QUOTE,
        CODE_BUDGET,
        CODE_RATE,
        CODE_CIRCUIT_OPEN,
        CODE_REJECTED,
        CODE_UNAVAILABLE,
        CODE_NOT_USD,
        CODE_CAP_INCOHERENT,
    }
)

RATE_PER_MINUTE = 50  # por debajo de los 60/min del plan gratuito (NO VERIFICADO)
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_WAIT_SECONDS = 15.0  # 4.4: espera máxima por token del bucket
QUERY_BUDGET_SECONDS = 20.0  # CS-27: espera total de Finnhub por consulta
QUOTE_MAX_AGE_SECONDS = 5 * 86400  # CS-27: `t` con más de 5 días = sin dato
QUOTE_MAX_FUTURE_SECONDS = 86400  # `t` en el futuro lejano = dato corrupto
MARKET_CAP_TOLERANCE = 0.25  # CS-27: |marketCap - c x shares| / (c x shares)

_BREAKER_TRANSIENT_FAILURES = 3
_BREAKER_TRANSIENT_OPEN = 60.0
_BREAKER_BLOCK_OPEN = 300.0  # 4.7 / 13.4: sin cifra propia para Finnhub; igual que la SEC
_BLOCKING_STATUSES = frozenset({401, 402, 403, 429})

_OK = "ok"
_BLOCK = "bloqueo"
_TRANSIENT = "transitorio"

_SYMBOL_RE = re.compile(r"[A-Z0-9]{1,6}(?:[.-][A-Z0-9]{1,2})?", re.ASCII)
_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)
_MAX_NAME_CHARS = 100


@dataclass(frozen=True)
class FinnhubReply:
    """Exactamente uno de `data` y `code` es `None`. `code` ∈ `FINNHUB_CODES`."""

    data: Optional[dict]
    code: Optional[str]

    def __post_init__(self) -> None:
        if (self.data is None) == (self.code is None):
            raise ValueError("FinnhubReply: exactamente uno de data/code debe ser None")
        if self.code is not None and self.code not in FINNHUB_CODES:
            raise ValueError("FinnhubReply: código fuera del enum cerrado")


def _nd(code: str) -> FinnhubReply:
    return FinnhubReply(None, code)


class QueryBudget:
    """Presupuesto total de espera de Finnhub para UNA consulta (CS-27)."""

    def __init__(self, now: Callable[[], float], total: float = QUERY_BUDGET_SECONDS) -> None:
        self._now = now
        self._start = now()
        self.total = total

    def remaining(self) -> float:
        return self.total - (self._now() - self._start)


class FinnhubGate:
    """Estado de Finnhub de UN `httpx.AsyncClient`: límite de tasa propio
    (ventana deslizante de 60 s) y breaker / kill-switch."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall: Callable[[], float] = time.time,
        rate_per_minute: int = RATE_PER_MINUTE,
    ) -> None:
        self.now = now
        self.sleep = sleep
        self.wall = wall
        self.rate_per_minute = rate_per_minute
        self._stamps: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        self.open_until: Optional[float] = None
        self.failures = 0
        self.probing = False

    def new_budget(self, total: float = QUERY_BUDGET_SECONDS) -> QueryBudget:
        return QueryBudget(self.now, total)

    async def acquire_slot(self, budget: QueryBudget) -> bool:
        """Toma un token de la ventana de 60 s. Si hay que esperar más de
        `RATE_MAX_WAIT_SECONDS` o de lo que queda del presupuesto, NO espera y
        devuelve False (degrada a N/D, nunca a error)."""
        async with self._rate_lock:
            while True:
                t = self.now()
                while self._stamps and t - self._stamps[0] >= RATE_WINDOW_SECONDS:
                    self._stamps.popleft()
                if len(self._stamps) < self.rate_per_minute:
                    self._stamps.append(t)
                    return True
                wait = RATE_WINDOW_SECONDS - (t - self._stamps[0])
                if wait > min(RATE_MAX_WAIT_SECONDS, budget.remaining()):
                    return False
                await self.sleep(wait)

    def admit(self) -> Optional[bool]:
        """`None` = breaker abierto (rechazada). Si no, `True` cuando es la
        única sonda del estado semi-abierto y `False` en el caso normal."""
        if self.open_until is None:
            return False
        if self.now() < self.open_until or self.probing:
            return None
        self.probing = True
        return True

    def record(self, outcome: Optional[str], probe: bool) -> None:
        """`outcome`: `ok`, `bloqueo`, `transitorio` o `None` (degradada/cancelada)."""
        if probe:
            self.probing = False
        if outcome is None:
            return
        if outcome == _OK:
            self.failures = 0
            if probe:
                self.open_until = None
            return
        if outcome == _BLOCK:
            self.failures = 0
            self.open_until = self.now() + _BREAKER_BLOCK_OPEN
            return
        self.failures += 1
        if probe or self.failures >= _BREAKER_TRANSIENT_FAILURES:
            self.failures = 0
            self.open_until = self.now() + _BREAKER_TRANSIENT_OPEN


_GATES: "weakref.WeakKeyDictionary[httpx.AsyncClient, FinnhubGate]" = weakref.WeakKeyDictionary()


def gate_for(client: httpx.AsyncClient) -> FinnhubGate:
    gate = _GATES.get(client)
    if gate is None:
        gate = _GATES[client] = FinnhubGate()
    return gate


def install_gate(
    client: httpx.AsyncClient,
    *,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    wall: Callable[[], float] = time.time,
    rate_per_minute: int = RATE_PER_MINUTE,
) -> FinnhubGate:
    """Asocia a `client` un `FinnhubGate` nuevo con reloj/sleep propios (tests)."""
    gate = _GATES[client] = FinnhubGate(
        now=now, sleep=sleep, wall=wall, rate_per_minute=rate_per_minute
    )
    return gate


def _valid_key(api_key: Any) -> bool:
    """Opt-in (13.4): sin key utilizable no hay llamadas. Solo ASCII visible
    (sin CR/LF ni espacios) para que nunca inyecte headers."""
    return (
        isinstance(api_key, str)
        and 1 <= len(api_key) <= 256
        and all("!" <= ch <= "~" for ch in api_key)
    )


def _valid_symbol(symbol: Any) -> Optional[str]:
    if isinstance(symbol, str) and symbol.isascii() and _SYMBOL_RE.fullmatch(symbol):
        return symbol
    return None


def _num(value: Any) -> Optional[float]:
    """Número real finito (nunca `bool`, `NaN`, `inf` ni cadena) o `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive(value: Any) -> Optional[float]:
    number = _num(value)
    return number if number is not None and number > 0 else None


def _clean_text(value: Any) -> Optional[str]:
    """CS-11: quita caracteres de control, colapsa espacios y acota a 100."""
    if not isinstance(value, str):
        return None
    chars = (" " if unicodedata.category(ch)[0] == "C" else ch for ch in value)
    text = " ".join("".join(chars).split())[:_MAX_NAME_CHARS]
    return text or None


async def _guarded_get(
    client: httpx.AsyncClient,
    api_key: Any,
    endpoint: str,
    params: dict[str, str],
    budget: Optional[QueryBudget],
    accept: type = dict,
) -> tuple[Any, Optional[str]]:
    """GET protegido: opt-in, presupuesto, breaker, tasa. Devuelve `(payload,
    None)` o `(None, código)` para toda degradación a N/D. Lanza `FinnhubError`
    sanitizado ante un fallo real de HTTP/red (que además alimenta el breaker)."""
    if not _valid_key(api_key):
        return None, CODE_NO_KEY
    gate = gate_for(client)
    budget = budget or gate.new_budget()
    if budget.remaining() <= 0:
        return None, CODE_BUDGET
    probe = gate.admit()
    if probe is None:
        return None, CODE_CIRCUIT_OPEN
    outcome: Optional[str] = None
    try:
        if not await gate.acquire_slot(budget):
            return None, CODE_RATE
        remaining = budget.remaining()
        if remaining <= 0:
            return None, CODE_BUDGET
        try:
            response = await client.get(
                f"{BASE_URL}{endpoint}",
                params=params,
                # CS-19 (b): el token va por header, nunca en la URL ni en logs.
                headers={"X-Finnhub-Token": api_key},
                timeout=min(DEFAULT_TIMEOUT, remaining),
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            outcome = _TRANSIENT
            logger.warning("Finnhub timeout — endpoint=%s", endpoint)
            raise FinnhubError("Finnhub no respondió a tiempo.", CODE_UNAVAILABLE) from None
        except httpx.RequestError:
            outcome = _TRANSIENT
            logger.warning("Finnhub error de red — endpoint=%s", endpoint)
            raise FinnhubError(
                "No pude conectarme a Finnhub ahora mismo.", CODE_UNAVAILABLE
            ) from None
        status = response.status_code
        if status in _BLOCKING_STATUSES:
            outcome = _BLOCK
            logger.warning("Finnhub rechazó la consulta — endpoint=%s status=%s", endpoint, status)
            raise FinnhubError(f"Finnhub rechazó la consulta ({status}).", CODE_REJECTED) from None
        if status != 200:
            outcome = _TRANSIENT
            logger.warning("Finnhub error HTTP — endpoint=%s status=%s", endpoint, status)
            raise FinnhubError(
                f"Finnhub respondió con un error ({status}).", CODE_UNAVAILABLE
            ) from None
        try:
            data: Any = response.json()
        except ValueError:
            outcome = _TRANSIENT
            logger.warning("Finnhub respuesta no-JSON — endpoint=%s", endpoint)
            raise FinnhubError(
                "Finnhub devolvió una respuesta inesperada.", CODE_UNAVAILABLE
            ) from None
        outcome = _OK
        if not isinstance(data, accept):
            return None, CODE_NO_DATA
        return data, None
    finally:
        gate.record(outcome, probe)


async def get_quote(
    client: httpx.AsyncClient,
    api_key: Optional[str],
    symbol: str,
    *,
    budget: Optional[QueryBudget] = None,
) -> FinnhubReply:
    """`/quote` -> `{"price": c, "timestamp": t}`. `c <= 0` o ausente = sin dato
    (`sin_dato`); `t == 0`, de más de 5 días o futuro lejano = `cotizacion_desactualizada`.
    Claves (NO VERIFICADAS contra la API real): `c`, `t`."""
    sym = _valid_symbol(symbol)
    if sym is None:
        return _nd(CODE_NO_DATA)
    payload, code = await _guarded_get(client, api_key, "/quote", {"symbol": sym}, budget)
    if payload is None:
        return _nd(code or CODE_NO_DATA)
    price = _positive(payload.get("c"))
    if price is None:
        return _nd(CODE_NO_DATA)
    stamp = _positive(payload.get("t"))
    age = gate_for(client).wall() - stamp if stamp is not None else None
    if age is None or age > QUOTE_MAX_AGE_SECONDS or age < -QUOTE_MAX_FUTURE_SECONDS:
        return _nd(CODE_STALE_QUOTE)
    return FinnhubReply({"price": price, "timestamp": int(stamp)}, None)


async def get_profile2(
    client: httpx.AsyncClient,
    api_key: Optional[str],
    symbol: str,
    *,
    price: Optional[float] = None,
    budget: Optional[QueryBudget] = None,
) -> FinnhubReply:
    """`/stock/profile2` -> `{companyName, currency, marketCap, sharesOutstanding,
    marketCapCode}` (CS-27). `marketCap` (USD) es `None` si `currency != "USD"` o
    si difiere más de 25% de `price x shareOutstanding` (solo comprobable cuando
    se pasa `price` y hay acciones); `marketCapCode` dice por qué. `finnhubIndustry`
    NO se devuelve (taxonomía propia, no GICS). Claves NO VERIFICADAS: `name`,
    `currency`, `marketCapitalization` (millones), `shareOutstanding` (millones)."""
    sym = _valid_symbol(symbol)
    if sym is None:
        return _nd(CODE_NO_DATA)
    payload, code = await _guarded_get(client, api_key, "/stock/profile2", {"symbol": sym}, budget)
    if payload is None:
        return _nd(code or CODE_NO_DATA)
    name = _clean_text(payload.get("name"))
    currency = payload.get("currency")
    currency = currency if isinstance(currency, str) and _CURRENCY_RE.fullmatch(currency) else None
    price = _positive(price)
    cap_m = _positive(payload.get("marketCapitalization"))
    shares_m = _positive(payload.get("shareOutstanding"))
    if name is None and cap_m is None:
        return _nd(CODE_NO_DATA)
    market_cap: Optional[float] = cap_m * 1e6 if cap_m is not None else None
    cap_code: Optional[str] = None
    if market_cap is not None and currency != "USD":
        market_cap, cap_code = None, CODE_NOT_USD
    elif market_cap is not None and price is not None and shares_m is not None:
        implied = price * shares_m * 1e6
        if abs(market_cap - implied) / implied > MARKET_CAP_TOLERANCE:
            market_cap, cap_code = None, CODE_CAP_INCOHERENT
    return FinnhubReply(
        {
            "companyName": name,
            "currency": currency,
            "marketCap": market_cap,
            "sharesOutstanding": shares_m * 1e6 if shares_m is not None else None,
            "marketCapCode": cap_code,
        },
        None,
    )


async def get_metric(
    client: httpx.AsyncClient,
    api_key: Optional[str],
    symbol: str,
    *,
    budget: Optional[QueryBudget] = None,
) -> FinnhubReply:
    """`/stock/metric?metric=all` -> `{beta, yearHigh, yearLow}` (los ausentes,
    `None`). `yearHigh`/`yearLow` exigen ser positivos y `low <= high`. Claves
    NO VERIFICADAS contra la API real: `metric.beta`, `metric.52WeekHigh`,
    `metric.52WeekLow` (sample del swagger). `peTTM` y demás no se usan."""
    sym = _valid_symbol(symbol)
    if sym is None:
        return _nd(CODE_NO_DATA)
    payload, code = await _guarded_get(
        client, api_key, "/stock/metric", {"symbol": sym, "metric": "all"}, budget
    )
    if payload is None:
        return _nd(code or CODE_NO_DATA)
    metric = payload.get("metric")
    metric = metric if isinstance(metric, dict) else {}
    beta = _num(metric.get("beta"))
    high, low = _positive(metric.get("52WeekHigh")), _positive(metric.get("52WeekLow"))
    if high is None or low is None or low > high:
        high = low = None
    if beta is None and high is None:
        return _nd(CODE_NO_DATA)
    return FinnhubReply({"beta": beta, "yearHigh": high, "yearLow": low}, None)
