"""Datos de mercado (cotización, perfil, beta/52 semanas, VIX) con procedencia
por bloque. F2a de `SDD_fuentes_datos_gratuitas.md` (sección 4, CA-25/26/30).

Interfaz mínima para que F2b / F1b-4 conecten luego este módulo a
`query_handler`; en F2a NADIE lo importa desde el flujo del bot.

- Cotización (`quote`), perfil (`profile`) y beta + 52 semanas (`metrics`) salen
  de Finnhub (`finnhub_client`) y, si Finnhub no sirve, de un respaldo FMP
  INYECTADO (`fmp_fallbacks`; este módulo no importa `fmp_client`). Cada bloque
  declara su fuente (`Block.source`) y por qué se descartó la anterior
  (`Block.codes`, enum cerrado): "el mensaje declara la fuente de cada bloque".
- VIX sale SOLO de FRED `VIXCLS` ("CBOE vía FRED"); jamás pasa por FMP (CA-26): el
  constructor rechaza un respaldo FMP para `vix`.
- `priceAvg50`/`priceAvg200` no existen aquí: sin fuente gratuita verificada
  (3.8); `market_context.calculate_momentum` ya degrada a "no_disponible".
- Sin persistencia: todo es memoria del request (13.4, condición 2).
- Nada se loguea con URL, token ni cuerpo.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional

import httpx

from investbot import finnhub_client, sic_sector
from investbot.finnhub_client import FinnhubError, QueryBudget
from investbot.fmp_client import FMPError
from investbot.treasury_client import FRED_BASE_URL

logger = logging.getLogger(__name__)

SOURCE_FINNHUB = "finnhub"
SOURCE_FRED = "fred"
SOURCE_FMP = "fmp"
SOURCES = frozenset({SOURCE_FINNHUB, SOURCE_FRED, SOURCE_FMP})

BLOCK_QUOTE = "quote"
BLOCK_PROFILE = "profile"
BLOCK_METRICS = "metrics"
BLOCK_VIX = "vix"
BLOCK_NAMES = (BLOCK_QUOTE, BLOCK_PROFILE, BLOCK_METRICS, BLOCK_VIX)
FALLBACK_BLOCKS = frozenset({BLOCK_QUOTE, BLOCK_PROFILE, BLOCK_METRICS})  # VIX: nunca FMP

FRED_VIX_SERIES = "VIXCLS"
VIX_SOURCE_LABEL = "CBOE vía FRED"  # FRED: "Copyrighted: Citation Required"
VIX_MIN, VIX_MAX = 5.0, 150.0  # CS-27: fuera de rango = N/D
VIX_MAX_AGE_DAYS = 7  # CS-27: observación de menos de 7 días
DEFAULT_TIMEOUT = 10.0

CODE_VIX_UNAVAILABLE = "vix_no_disponible"
CODE_VIX_NO_DATA = "vix_sin_dato"
CODE_VIX_OUT_OF_RANGE = "vix_fuera_de_rango"
CODE_VIX_STALE = "vix_desactualizado"
CODE_FMP_UNAVAILABLE = "fmp_no_disponible"
MARKET_CODES = finnhub_client.FINNHUB_CODES | frozenset(
    {
        CODE_VIX_UNAVAILABLE,
        CODE_VIX_NO_DATA,
        CODE_VIX_OUT_OF_RANGE,
        CODE_VIX_STALE,
        CODE_FMP_UNAVAILABLE,
    }
)

_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)

# Respaldo FMP por bloque: `async (symbol) -> dict | None` (puede lanzar `FMPError`).
FmpFallback = Callable[[str], Awaitable[Optional[dict]]]


@dataclass(frozen=True)
class Block:
    """Un bloque de mercado con su procedencia. `source` y `data` son ambos
    `None` (N/D) o ambos presentes; `codes` explica qué proveedor se descartó."""

    name: str
    source: Optional[str]
    data: Optional[dict]
    codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in BLOCK_NAMES:
            raise ValueError("Block: nombre fuera del enum cerrado")
        if (self.source is None) != (self.data is None):
            raise ValueError("Block: source y data son ambos None o ambos presentes")
        if self.source is not None and self.source not in SOURCES:
            raise ValueError("Block: fuente fuera del enum cerrado")
        if not set(self.codes) <= MARKET_CODES:
            raise ValueError("Block: código fuera del enum cerrado")


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    quote: Block
    profile: Block
    metrics: Block
    vix: Block

    def provenance(self) -> dict[str, Optional[str]]:
        """Fuente de cada bloque (`None` = N/D)."""
        return {b.name: b.source for b in (self.quote, self.profile, self.metrics, self.vix)}

    def fallback_codes(self) -> tuple[str, ...]:
        """Códigos de todos los bloques, sin repetir y en orden."""
        vistos: list[str] = []
        for block in (self.quote, self.profile, self.metrics, self.vix):
            vistos += [c for c in block.codes if c not in vistos]
        return tuple(vistos)


def _fred_observations(data: Any) -> list[tuple[date, float]]:
    """Observaciones `(fecha, valor)` válidas: ignora `"."` (dato faltante de FRED),
    fechas mal formadas y valores no numéricos / no finitos."""
    raw_list = data.get("observations") if isinstance(data, dict) else None
    out: list[tuple[date, float]] = []
    for obs in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(obs, dict):
            continue
        raw_value = obs.get("value")
        if raw_value == ".":
            continue
        raw_date = obs.get("date")
        if not isinstance(raw_date, str) or not _ISO_DATE_RE.fullmatch(raw_date):
            continue
        try:
            observed = date.fromisoformat(raw_date)
            value = float(raw_value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(value):
            out.append((observed, value))
    return out


async def get_vix(
    client: Optional[httpx.AsyncClient], api_key: Optional[str], *, today: date
) -> Block:
    """VIX desde FRED `VIXCLS` (CA-26). Nunca lanza: N/D con un código. Usa la
    observación más reciente con valor distinto de `"."`; exige valor en
    [5, 150] y una observación de menos de 7 días respecto de `today`."""
    if client is None or not api_key:
        return Block(BLOCK_VIX, None, None, (finnhub_client.CODE_NO_KEY,))
    try:
        response = await client.get(
            FRED_BASE_URL,
            params={
                "series_id": FRED_VIX_SERIES,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,
            },
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        logger.warning("FRED timeout — serie=%s", FRED_VIX_SERIES)
        return Block(BLOCK_VIX, None, None, (CODE_VIX_UNAVAILABLE,))
    except httpx.RequestError:
        logger.warning("FRED error de red — serie=%s", FRED_VIX_SERIES)
        return Block(BLOCK_VIX, None, None, (CODE_VIX_UNAVAILABLE,))
    if response.status_code != 200:
        logger.warning(
            "FRED error HTTP — serie=%s status=%s", FRED_VIX_SERIES, response.status_code
        )
        return Block(BLOCK_VIX, None, None, (CODE_VIX_UNAVAILABLE,))
    try:
        data = response.json()
    except ValueError:
        logger.warning("FRED respuesta no-JSON — serie=%s", FRED_VIX_SERIES)
        return Block(BLOCK_VIX, None, None, (CODE_VIX_UNAVAILABLE,))
    observations = _fred_observations(data)
    if not observations:
        return Block(BLOCK_VIX, None, None, (CODE_VIX_NO_DATA,))
    observed, value = max(observations, key=lambda item: item[0])
    if not VIX_MIN <= value <= VIX_MAX:
        return Block(BLOCK_VIX, None, None, (CODE_VIX_OUT_OF_RANGE,))
    if not 0 <= (today - observed).days < VIX_MAX_AGE_DAYS:
        return Block(BLOCK_VIX, None, None, (CODE_VIX_STALE,))
    return Block(
        BLOCK_VIX,
        SOURCE_FRED,
        {"value": value, "observed_on": observed.isoformat(), "source": VIX_SOURCE_LABEL},
    )


def _checked_fallbacks(fmp_fallbacks: Optional[Mapping[str, FmpFallback]]) -> dict:
    fallbacks = dict(fmp_fallbacks or {})
    if not set(fallbacks) <= FALLBACK_BLOCKS:
        raise ValueError("fmp_fallbacks solo admite quote, profile y metrics (nunca vix)")
    return fallbacks


def build_fmp_fallbacks(
    get_quote: FmpFallback, get_profile: FmpFallback
) -> dict[str, FmpFallback]:
    """Respaldos FMP de UNA consulta (F2b, 4.8): `quote` y `profile` son las
    llamadas de siempre a FMP (`/quote`, `/profile`); `metrics` (beta + rango de
    52 semanas) se arma con esas MISMAS dos respuestas, sin pedir nada nuevo a
    FMP: la memoria vive solo en estas clausuras (una consulta, nunca entre
    consultas ni en disco). Un fallo de FMP (`FMPError`) en una de las dos
    llamadas se propaga solo al bloque que la pidió; el siguiente bloque que la
    necesite la vuelve a intentar."""
    memo: dict[str, Optional[dict]] = {}

    async def quote(symbol: str) -> Optional[dict]:
        if "quote" not in memo:
            memo["quote"] = await get_quote(symbol)
        return memo["quote"]

    async def profile(symbol: str) -> Optional[dict]:
        if "profile" not in memo:
            memo["profile"] = await get_profile(symbol)
        return memo["profile"]

    async def metrics(symbol: str) -> Optional[dict]:
        q, p = await quote(symbol), await profile(symbol)
        out = {
            "beta": finnhub_client._num((p or {}).get("beta")),
            "yearHigh": finnhub_client._positive((q or {}).get("yearHigh")),
            "yearLow": finnhub_client._positive((q or {}).get("yearLow")),
        }
        return out if any(v is not None for v in out.values()) else None

    return {BLOCK_QUOTE: quote, BLOCK_PROFILE: profile, BLOCK_METRICS: metrics}


@dataclass(frozen=True)
class ComposedMarket:
    """El snapshot en la FORMA que ya consume el flujo (dicts tipo FMP), más la
    procedencia por bloque. `quote`/`profile` vacíos = N/D."""

    quote: dict
    profile: dict
    vix_quote: Optional[dict]
    provenance: dict[str, Any]


def compose(
    snapshot: "MarketSnapshot",
    *,
    sic: Any = None,
    sic_description: Any = None,
) -> ComposedMarket:
    """Une los bloques del snapshot en `quote`/`profile` con las claves de FMP
    (`price`, `marketCap`, `yearHigh`, `yearLow`, `companyName`, `beta`,
    `sector`, `industry`). Sin `priceAvg50`/`priceAvg200` si la cotización no
    viene de FMP (3.8: sin fuente gratuita) -> momentum "no_disponible".

    - Bloque FMP: su dict crudo. Bloque Finnhub: solo los campos conocidos.
    - `beta`/52 semanas del bloque `metrics` (Finnhub primero) pisan lo del otro.
    - `sector`: el de FMP si lo trae; si no, el de la tabla SIC de la SEC
      (`sic_sector`, siempre dentro de `_SECTOR_ALLOWLIST` o `None`);
      `industry` = `sicDescription` acotada y sin caracteres de control.
    - VIX: `{"price": valor}` solo si hay dato de FRED."""
    q, p, m = snapshot.quote, snapshot.profile, snapshot.metrics
    if q.source == SOURCE_FMP:
        quote = dict(q.data or {})
    else:
        quote = {"price": q.data["price"]} if q.data else {}
    if p.source == SOURCE_FMP:
        profile = dict(p.data or {})
    elif p.data:
        profile = {
            key: p.data[src]
            for key, src in (("companyName", "companyName"), ("marketCap", "marketCap"))
            if p.data.get(src) is not None
        }
    else:
        profile = {}
    # Solo se completa un bloque que YA existe: un perfil (o una cotización) sin
    # dato propio sigue vacío (N/D) aunque haya beta, 52 semanas o SIC.
    if m.data and quote:
        for key in ("yearHigh", "yearLow"):
            if m.data.get(key) is not None:
                quote[key] = m.data[key]
    if profile:
        if m.data and m.data.get("beta") is not None:
            profile["beta"] = m.data["beta"]
        if not profile.get("sector"):
            sector = sic_sector.sector_for_sic(sic)
            if sector is not None:
                profile["sector"] = sector
        industry = finnhub_client._clean_text(sic_description)
        if industry and not profile.get("industry"):
            profile["industry"] = industry
    vix_quote = {"price": snapshot.vix.data["value"]} if snapshot.vix.data else None
    return ComposedMarket(
        quote,
        profile,
        vix_quote,
        {"sources": snapshot.provenance(), "codes": snapshot.fallback_codes()},
    )


class MarketDataService:
    """Orquesta Finnhub -> respaldo FMP por bloque, y FRED para el VIX.

    `finnhub_http`/`fred_http` pueden ser `None` y las keys pueden faltar: sin key
    válida no hay llamadas a ese proveedor (opt-in). `fmp_fallbacks` mapea
    `quote`/`profile`/`metrics` a una corrutina de respaldo; cualquier otra clave
    (en particular `vix`) se rechaza al construir (CA-26)."""

    def __init__(
        self,
        *,
        finnhub_http: Optional[httpx.AsyncClient],
        finnhub_api_key: Optional[str],
        fred_http: Optional[httpx.AsyncClient] = None,
        fred_api_key: Optional[str] = None,
        fmp_fallbacks: Optional[Mapping[str, FmpFallback]] = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        fallbacks = _checked_fallbacks(fmp_fallbacks)
        self._finnhub_http = finnhub_http
        self._finnhub_api_key = finnhub_api_key
        self._fred_http = fred_http
        self._fred_api_key = fred_api_key
        self._fallbacks = fallbacks
        self._wall = wall

    def new_budget(self) -> Optional[QueryBudget]:
        """Presupuesto de 20 s de espera de Finnhub para UNA consulta (CS-27);
        `None` si no hay cliente de Finnhub (no habrá llamadas)."""
        if self._finnhub_http is None:
            return None
        return finnhub_client.gate_for(self._finnhub_http).new_budget()

    async def _from_finnhub(
        self,
        name: str,
        symbol: str,
        call: Optional[Callable[[], Awaitable[Any]]],
        fallbacks: Mapping[str, FmpFallback],
    ) -> Block:
        codes: list[str] = []
        if call is None:
            codes.append(finnhub_client.CODE_NO_KEY)
        else:
            try:
                reply = await call()
            except FinnhubError as exc:
                codes.append(exc.code or finnhub_client.CODE_UNAVAILABLE)
            else:
                if reply.data is not None:
                    return Block(name, SOURCE_FINNHUB, reply.data)
                codes.append(reply.code)
        fallback = fallbacks.get(name)
        if fallback is not None:
            try:
                data = await fallback(symbol)
            except FMPError:
                codes.append(CODE_FMP_UNAVAILABLE)
            else:
                if isinstance(data, dict) and data:
                    return Block(name, SOURCE_FMP, data, tuple(codes))
                codes.append(CODE_FMP_UNAVAILABLE)
        return Block(name, None, None, tuple(codes))

    async def fetch_vix(self) -> Block:
        today = datetime.fromtimestamp(self._wall(), tz=timezone.utc).date()
        return await get_vix(self._fred_http, self._fred_api_key, today=today)

    async def fetch(
        self,
        symbol: str,
        *,
        budget: Optional[QueryBudget] = None,
        fmp_fallbacks: Optional[Mapping[str, FmpFallback]] = None,
    ) -> MarketSnapshot:
        """Los cuatro bloques de una consulta. Nunca lanza por un fallo de un
        proveedor: el bloque queda N/D con sus códigos. Todas las llamadas de
        Finnhub comparten UN presupuesto (`budget`, 20 s por defecto).
        `fmp_fallbacks` (solo esta consulta, mismas reglas que en el
        constructor) reemplaza a los del constructor."""
        fallbacks = self._fallbacks if fmp_fallbacks is None else _checked_fallbacks(fmp_fallbacks)
        enabled = self._finnhub_http is not None and bool(self._finnhub_api_key)
        client, key = self._finnhub_http, self._finnhub_api_key
        budget = budget or self.new_budget()

        quote = await self._from_finnhub(
            BLOCK_QUOTE,
            symbol,
            (lambda: finnhub_client.get_quote(client, key, symbol, budget=budget))
            if enabled
            else None,
            fallbacks,
        )
        price = finnhub_client._positive(quote.data.get("price")) if quote.data else None
        profile = await self._from_finnhub(
            BLOCK_PROFILE,
            symbol,
            (lambda: finnhub_client.get_profile2(client, key, symbol, price=price, budget=budget))
            if enabled
            else None,
            fallbacks,
        )
        metrics = await self._from_finnhub(
            BLOCK_METRICS,
            symbol,
            (lambda: finnhub_client.get_metric(client, key, symbol, budget=budget))
            if enabled
            else None,
            fallbacks,
        )
        vix = await self.fetch_vix()
        return MarketSnapshot(symbol, quote, profile, metrics, vix)
