"""Wrapper HTTP a SEC EDGAR (data.sec.gov / www.sec.gov) — fuente oficial y
gratuita de filings públicos (eventos corporativos vía 8-K y, desde F1b-1 de
`SDD_fuentes_datos_gratuitas.md`, estados financieros XBRL de `companyfacts`).

Requiere SIEMPRE un header User-Agent con contacto identificable (requisito
de acceso justo de la SEC, no opcional) — ver SEC_EDGAR_USER_AGENT en
.env.example. Sin esta variable, la feature completa se omite (nunca se
manda una request sin el header configurado).

Rate limit documentado: 10 req/seg por IP, SIN límite diario (a diferencia
de FMP). No consume el presupuesto de 250 req/día de FMP — proveedor
distinto, mismo criterio que FRED/Treasury.gov/Finnhub.

Todo acceso a `sec.gov` pasa por un `SecGate` asociado a la instancia de
`httpx.AsyncClient` (nunca una variable de módulo): limitador de 8 req/s,
breaker único `sec_edgar`, cachés de `submissions`, single-flight y el
temporizador de refresco del mapa. Un cliente nuevo arranca con estado limpio.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import time
import weakref
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
DEFAULT_TIMEOUT = 10.0
_FACTS_TIMEOUT = httpx.Timeout(20.0, connect=5.0, read=15.0)
_ALLOWED_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})  # CS-1: lista cerrada

# Topes de bytes ya descomprimidos (CS-16 / 15.7.2).
_MAX_BYTES_FACTS = 12 * 1024 * 1024
_MAX_BYTES_SUBMISSIONS = 4 * 1024 * 1024
_MAX_BYTES_TICKERS = 2 * 1024 * 1024
_CHUNK_SIZE = 65536

_CACHE_TTL_HOURS = 24  # mapeo ticker->CIK cambia muy poco; evita re-descargar
# un archivo de cientos de KB en cada consulta.
_STALE_MAP_MAX = timedelta(days=7)
_MAP_RETRY_SECONDS = 300.0  # CS-18: tras un refresco fallido con mapa previo
_MIN_ENTRADAS_REFRESCO = 1000  # QB-1 refinado: solo protege a un mapa previo grande
_MAX_ENTRADAS_MAPA = 50_000

_RATE_PER_SECOND = 8
_RATE_WINDOW = 1.0
_SUBMISSIONS_TTL = 3600.0
_SUBMISSIONS_CACHE_MAX = 64

_BREAKER_TRANSIENT_FAILURES = 3
_BREAKER_TRANSIENT_OPEN = 60.0
_BREAKER_BLOCK_OPEN = 300.0

# Códigos de resultado (subconjunto del enum cerrado FALLBACK_CODES de la spec).
CODE_TEMPORAL = "no_disponible_temporal"
CODE_BLOCKED = "bloqueado_por_sec"
CODE_UNEXPECTED = "respuesta_inesperada"
CODE_TOO_BIG = "respuesta_demasiado_grande"
CODE_REDIRECT = "redireccion_inesperada"
CODE_CIRCUIT_OPEN = "circuito_abierto"
CODE_NO_FACTS = "sin_facts"
CODE_NO_CIK = "sin_cik"
_NOT_FOUND = "no_encontrado"  # interno: 404 (no cuenta para el breaker)
_OK = "ok"
_NEUTRAL = frozenset({_OK, _NOT_FOUND})
_BLOCKING = frozenset({CODE_BLOCKED, CODE_UNEXPECTED})

_TICKER_RE = re.compile(r"[A-Z0-9]{1,6}(-[A-Z0-9]{1,2})?", re.ASCII)
_CIK_RE = re.compile(r"[0-9]{1,10}")
_CIK_MAX = 9_999_999_999

_ticker_cik_cache: dict[str, str] = {}
_cache_loaded_at: Optional[datetime] = None


class SecEdgarError(Exception):
    """Error sanitizado de SEC EDGAR — nunca incluye la URL cruda. `code`
    pertenece al enum cerrado de la spec (nunca texto libre)."""

    def __init__(self, message: str, code: str = CODE_TEMPORAL) -> None:
        super().__init__(message)
        self.code = code


class SecGate:
    """Estado de acceso a `sec.gov` de UN `httpx.AsyncClient` (QB-5): limitador
    de 8 req/s, breaker único, cachés y temporizador de refresco del mapa.
    `now` y `sleep` son inyectables; ningún test necesita tiempo real."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.now = now
        self.sleep = sleep
        self._stamps: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        # Breaker: `open_until` no nulo = abierto (o semi-abierto si ya venció).
        self.open_until: Optional[float] = None
        self.failures = 0
        self.probing = False
        self.facts_sem = asyncio.Semaphore(1)
        self.submissions: dict[str, tuple[float, dict]] = {}
        self.inflight: dict[Any, asyncio.Future] = {}
        self.map_retry_at: Optional[float] = None
        self.map_rejected = 0

    # --- limitador: nunca más de 8 inicios en cualquier ventana de 1 s -------
    async def acquire(self) -> None:
        async with self._rate_lock:
            while True:
                t = self.now()
                while self._stamps and t - self._stamps[0] >= _RATE_WINDOW:
                    self._stamps.popleft()
                if len(self._stamps) < _RATE_PER_SECOND:
                    self._stamps.append(t)
                    return
                await self.sleep(_RATE_WINDOW - (t - self._stamps[0]))

    # --- breaker --------------------------------------------------------------
    def admit(self) -> bool:
        """Deja pasar la petición o lanza `circuito_abierto`. Devuelve True si
        es la única sonda del estado semi-abierto."""
        if self.open_until is None:
            return False
        if self.now() < self.open_until or self.probing:
            raise SecEdgarError(
                "SEC EDGAR no está disponible temporalmente.", CODE_CIRCUIT_OPEN
            )
        self.probing = True
        return True

    def record(self, outcome: Optional[str], probe: bool) -> None:
        """`outcome`: `ok`, un código, o `None` si la llamada se canceló."""
        if probe:
            self.probing = False
        if outcome is None or outcome == CODE_CIRCUIT_OPEN:
            return  # cancelada, o rechazada por el propio breaker (no es un fallo nuevo)
        if outcome in _NEUTRAL:
            self.failures = 0
            if probe:
                self.open_until = None
                self.map_retry_at = None
            return
        if outcome in _BLOCKING:
            self.failures = 0
            self.open_until = self.now() + _BREAKER_BLOCK_OPEN
            return
        self.failures += 1
        if probe or self.failures >= _BREAKER_TRANSIENT_FAILURES:
            self.failures = 0
            self.open_until = self.now() + _BREAKER_TRANSIENT_OPEN

    # --- single-flight --------------------------------------------------------
    async def single_flight(self, key: Any, factory: Callable[[], Awaitable[Any]]) -> Any:
        task = self.inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self.inflight[key] = task
            task.add_done_callback(lambda t: self._finish(key, t))
        return await asyncio.shield(task)

    def _finish(self, key: Any, task: asyncio.Future) -> None:
        self.inflight.pop(key, None)
        task.cancelled() or task.exception()  # consume: sin "never retrieved"

    # --- temporizador de refresco del mapa -----------------------------------
    def map_retry_pending(self) -> bool:
        return self.map_retry_at is not None and self.now() < self.map_retry_at


_GATES: "weakref.WeakKeyDictionary[httpx.AsyncClient, SecGate]" = weakref.WeakKeyDictionary()


def gate_for(client: httpx.AsyncClient) -> SecGate:
    gate = _GATES.get(client)
    if gate is None:
        gate = _GATES[client] = SecGate()
    return gate


def install_gate(
    client: httpx.AsyncClient,
    *,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SecGate:
    """Asocia a `client` un `SecGate` nuevo con reloj/sleep propios (tests)."""
    gate = _GATES[client] = SecGate(now=now, sleep=sleep)
    return gate


def build_sec_http_client() -> httpx.AsyncClient:
    """Cliente dedicado a `sec.gov` (CS-2, CS-17): sin redirecciones y con
    pocas conexiones. Lo comparten el 8-K y el proveedor de fundamentales."""
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        follow_redirects=False,
        timeout=DEFAULT_TIMEOUT,
    )


def _check_url(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host not in _ALLOWED_HOSTS:
        raise SecEdgarError("URL no permitida para SEC EDGAR.", CODE_UNEXPECTED)


def _normalize_ticker(raw: Any) -> Optional[str]:
    """`isascii()` ANTES de `.upper()` (`ſ`.upper() == "S"); `.`→`-`; fullmatch."""
    if not isinstance(raw, str) or not raw.isascii():
        return None
    t = raw.upper().replace(".", "-")
    return t if _TICKER_RE.fullmatch(t) else None


def _valid_cik(raw: Any) -> Optional[str]:
    """CIK de 10 dígitos o `None`. Acepta `str` `[0-9]{1,10}` o `int` (no bool)
    en 1..9.999.999.999; se usa `[0-9]` y no `\\d` (dígitos Unicode)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw).zfill(10) if 1 <= raw <= _CIK_MAX else None
    if isinstance(raw, str) and _CIK_RE.fullmatch(raw):
        return raw.zfill(10)
    return None


def _loads_dict(body: Any) -> Optional[dict]:
    """`json.loads` desde el `bytearray` sin copiarlo a `bytes`. Nunca lanza."""
    try:
        data = json.loads(body)
    except Exception:  # JSON roto, anidación excesiva, codificación inválida
        return None
    return data if isinstance(data, dict) else None


async def _stream_body(
    client: httpx.AsyncClient, url: str, user_agent: str, max_bytes: int, timeout: Any
) -> Optional[bytearray]:
    """GET en streaming. `None` = 404. Solo lee el cuerpo si status 200 y el
    tipo no es HTML; corta antes de acumular más de `max_bytes`."""
    try:
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status = response.status_code
            if status == 404:
                logger.info("SEC EDGAR sin recurso — url=%s status=%s", url, status)
                return None
            if status != 200:
                logger.warning("SEC EDGAR error HTTP — url=%s status=%s", url, status)
                if 300 <= status < 400:
                    raise SecEdgarError("SEC EDGAR redirigió la petición.", CODE_REDIRECT)
                if status in (403, 429):
                    raise SecEdgarError(
                        f"SEC EDGAR respondió con un error ({status}).", CODE_BLOCKED
                    )
                raise SecEdgarError(
                    f"SEC EDGAR respondió con un error ({status}).", CODE_TEMPORAL
                )
            # `content-type` es informativo (15.3 fila 18): solo HTML se rechaza sin leer.
            ctype = response.headers.get("content-type", "").lower()
            if ctype.startswith(("text/html", "application/xhtml")):
                logger.warning("SEC EDGAR respuesta HTML — url=%s status=%s", url, status)
                raise SecEdgarError(
                    "SEC EDGAR devolvió una respuesta inesperada.", CODE_UNEXPECTED
                )
            body = bytearray()
            async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                if len(body) + len(chunk) > max_bytes:
                    logger.warning(
                        "SEC EDGAR respuesta demasiado grande — url=%s bytes>%s", url, max_bytes
                    )
                    raise SecEdgarError(
                        "SEC EDGAR devolvió una respuesta demasiado grande.", CODE_TOO_BIG
                    )
                body += chunk
            return body
    except httpx.TimeoutException:
        logger.warning("SEC EDGAR timeout — url=%s", url)
        raise SecEdgarError("SEC EDGAR no respondió a tiempo.", CODE_TEMPORAL) from None
    except httpx.RequestError:
        logger.warning("SEC EDGAR error de red — url=%s", url)
        raise SecEdgarError(
            "No pude conectarme a SEC EDGAR ahora mismo.", CODE_TEMPORAL
        ) from None


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    user_agent: str,
    *,
    max_bytes: int = _MAX_BYTES_SUBMISSIONS,
    timeout: Any = DEFAULT_TIMEOUT,
    sem: Optional[asyncio.Semaphore] = None,
) -> dict:
    """GET de `url` (lista cerrada de hosts) a través del `SecGate` del cliente:
    breaker, limitador y, si hay `sem`, una sola descarga+parseo a la vez (el
    semáforo se libera cuando el hilo de parseo termina, no al cancelarse).
    Devuelve un `dict`; lanza `SecEdgarError` con código. 404 → `no_encontrado`."""
    _check_url(url)
    gate = gate_for(client)
    if sem is not None:
        await sem.acquire()
    probe = False
    outcome: Optional[str] = None
    parse_task: Optional[asyncio.Future] = None
    try:
        probe = gate.admit()
        await gate.acquire()
        body = await _stream_body(client, url, user_agent, max_bytes, timeout)
        if body is None:
            raise SecEdgarError("SEC EDGAR no tiene ese recurso.", _NOT_FOUND)
        parse_task = asyncio.ensure_future(asyncio.to_thread(_loads_dict, body))
        data = await asyncio.shield(parse_task)
        del body
        if data is None:
            logger.warning("SEC EDGAR respuesta no-JSON — url=%s", url)
            raise SecEdgarError("SEC EDGAR devolvió una respuesta inesperada.", CODE_UNEXPECTED)
        outcome = _OK
        return data
    except SecEdgarError as exc:
        outcome = exc.code
        raise
    finally:
        gate.record(outcome, probe)
        if sem is not None:
            if parse_task is not None and not parse_task.done():
                parse_task.add_done_callback(lambda _t: sem.release())
            else:
                sem.release()


def _validate_map(data: dict) -> tuple[dict[str, str], int]:
    """CS-14: valida cada entrada de `company_tickers.json` (dato de red no
    confiable). Devuelve (mapa ticker→CIK10, entradas descartadas)."""
    nuevo: dict[str, str] = {}
    conflictos: set[str] = set()
    rejected = max(0, len(data) - _MAX_ENTRADAS_MAPA)
    for entry in itertools.islice(data.values(), _MAX_ENTRADAS_MAPA):
        if not isinstance(entry, dict):
            rejected += 1
            continue
        ticker = _normalize_ticker(entry.get("ticker"))
        cik = _valid_cik(entry.get("cik_str"))
        if ticker is None or cik is None or ticker in conflictos:
            rejected += 1
            continue
        previo = nuevo.get(ticker)
        if previo is not None and previo != cik:
            # Mismo ticker con CIK distintos: ambiguo, se descarta (sin_cik).
            del nuevo[ticker]
            conflictos.add(ticker)
            rejected += 2
            continue
        nuevo[ticker] = cik
    return nuevo, rejected


async def _refresh_map(client: httpx.AsyncClient, user_agent: str) -> None:
    global _cache_loaded_at
    data = await _get_json(client, TICKERS_URL, user_agent, max_bytes=_MAX_BYTES_TICKERS)
    nuevo, rejected = _validate_map(data)
    gate = gate_for(client)
    gate.map_rejected = rejected
    if rejected:
        logger.warning("SEC EDGAR mapa de tickers: %s entradas descartadas.", rejected)
    previas = len(_ticker_cik_cache) if _cache_loaded_at is not None else 0
    if previas >= _MIN_ENTRADAS_REFRESCO and len(nuevo) < _MIN_ENTRADAS_REFRESCO:
        raise SecEdgarError("SEC EDGAR devolvió un mapeo de tickers incompleto.", CODE_UNEXPECTED)
    if not nuevo:
        raise SecEdgarError("SEC EDGAR devolvió un mapeo de tickers vacío.", CODE_UNEXPECTED)
    _ticker_cik_cache.clear()
    _ticker_cik_cache.update(nuevo)
    _cache_loaded_at = datetime.now(timezone.utc)


async def _ensure_ticker_cache(client: httpx.AsyncClient, user_agent: str) -> None:
    now = datetime.now(timezone.utc)
    if _cache_loaded_at and (now - _cache_loaded_at) < timedelta(hours=_CACHE_TTL_HOURS):
        return
    gate = gate_for(client)
    # Mapa previo servible (CS-18): hasta 7 días de antigüedad.
    sirve_viejo = (
        _cache_loaded_at is not None
        and bool(_ticker_cik_cache)
        and (now - _cache_loaded_at) <= _STALE_MAP_MAX
    )
    if sirve_viejo and gate.map_retry_pending():
        return
    try:
        await gate.single_flight("map", lambda: _refresh_map(client, user_agent))
    except SecEdgarError:
        if sirve_viejo:
            gate.map_retry_at = gate.now() + _MAP_RETRY_SECONDS
            return
        raise


async def get_cik_for_ticker(
    client: httpx.AsyncClient, user_agent: str, ticker: str
) -> Optional[str]:
    """CIK de 10 dígitos (con ceros a la izquierda) para `ticker`, o `None`
    si no se encuentra, si el ticker no pasa la validación estricta (cero
    peticiones) o si SEC EDGAR falla (nunca lanza SecEdgarError hacia
    afuera — best-effort, mismo criterio que own_metrics/VIX en query_handler.py)."""
    normalizado = _normalize_ticker(ticker)
    if normalizado is None:
        return None
    try:
        await _ensure_ticker_cache(client, user_agent)
    except SecEdgarError:
        return None
    return _ticker_cik_cache.get(normalizado)


async def _fetch_submissions(client: httpx.AsyncClient, user_agent: str, cik: str) -> dict:
    gate = gate_for(client)
    data = await _get_json(
        client, SUBMISSIONS_URL.format(cik10=cik), user_agent, max_bytes=_MAX_BYTES_SUBMISSIONS
    )
    gate.submissions[cik] = (gate.now(), data)
    while len(gate.submissions) > _SUBMISSIONS_CACHE_MAX:
        del gate.submissions[next(iter(gate.submissions))]
    return data


async def get_submissions(
    client: httpx.AsyncClient, user_agent: str, cik10: str
) -> Optional[dict]:
    """Historial de filings de `cik10` (caché 1 h, single-flight). `None` si el
    CIK es inválido (cero peticiones) o si SEC EDGAR falla (best-effort, no
    lanza hacia afuera — mismo criterio que get_cik_for_ticker)."""
    cik = _valid_cik(cik10)
    if cik is None:
        return None
    gate = gate_for(client)
    cached = gate.submissions.get(cik)
    if cached is not None and gate.now() - cached[0] < _SUBMISSIONS_TTL:
        return cached[1]
    try:
        return await gate.single_flight(
            ("submissions", cik), lambda: _fetch_submissions(client, user_agent, cik)
        )
    except SecEdgarError:
        return None


async def get_company_facts(
    client: httpx.AsyncClient, user_agent: str, cik10: str
) -> dict:
    """`companyfacts` de `cik10` por streaming (tope 12 MiB). Devuelve el `dict`
    crudo; lanza `SecEdgarError` con `code` (`sin_facts` si 404, `sin_cik` si el
    CIK es inválido —sin petición—, `respuesta_demasiado_grande`, etc.)."""
    cik = _valid_cik(cik10)
    if cik is None:
        raise SecEdgarError("CIK inválido.", CODE_NO_CIK)
    try:
        return await _get_json(
            client,
            COMPANYFACTS_URL.format(cik10=cik),
            user_agent,
            max_bytes=_MAX_BYTES_FACTS,
            timeout=_FACTS_TIMEOUT,
            sem=gate_for(client).facts_sem,
        )
    except SecEdgarError as exc:
        if exc.code == _NOT_FOUND:
            raise SecEdgarError("SEC EDGAR no tiene datos para esa empresa.", CODE_NO_FACTS) from None
        raise
