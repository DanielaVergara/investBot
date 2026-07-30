"""Wrapper HTTP a SEC EDGAR (data.sec.gov / www.sec.gov) — fuente oficial y
gratuita de filings públicos (Feature 2: eventos corporativos vía 8-K).

Requiere SIEMPRE un header User-Agent con contacto identificable (requisito
de acceso justo de la SEC, no opcional) — ver SEC_EDGAR_USER_AGENT en
.env.example. Sin esta variable, la feature completa se omite (nunca se
manda una request sin el header configurado).

Rate limit documentado: 10 req/seg por IP, SIN límite diario (a diferencia
de FMP). No consume el presupuesto de 250 req/día de FMP — proveedor
distinto, mismo criterio que FRED/Treasury.gov/Finnhub.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
DEFAULT_TIMEOUT = 10.0
_CACHE_TTL_HOURS = 24  # mapeo ticker->CIK cambia muy poco; evita re-descargar
# un archivo de cientos de KB en cada consulta (mismo espíritu que el estado
# en memoria ya usado por security.InMemoryRateLimiter — proceso único, sin
# necesidad de un lock: en el peor caso 2 consultas simultáneas refrescan el
# caché 2 veces, sin efecto negativo).

_ticker_cik_cache: dict[str, str] = {}
_cache_loaded_at: Optional[datetime] = None


class SecEdgarError(Exception):
    """Error sanitizado de SEC EDGAR — nunca incluye la URL cruda."""


async def _get_json(client: httpx.AsyncClient, url: str, user_agent: str) -> Any:
    try:
        response = await client.get(
            url, headers={"User-Agent": user_agent}, timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("SEC EDGAR timeout — url=%s", url)
        raise SecEdgarError("SEC EDGAR no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        logger.warning("SEC EDGAR error HTTP — url=%s status=%s", url, exc.response.status_code)
        raise SecEdgarError(f"SEC EDGAR respondió con un error ({exc.response.status_code}).")
    except httpx.RequestError:
        logger.warning("SEC EDGAR error de red — url=%s", url)
        raise SecEdgarError("No pude conectarme a SEC EDGAR ahora mismo.")
    try:
        return response.json()
    except ValueError:
        logger.warning("SEC EDGAR respuesta no-JSON — url=%s", url)
        raise SecEdgarError("SEC EDGAR devolvió una respuesta inesperada.")


async def _ensure_ticker_cache(client: httpx.AsyncClient, user_agent: str) -> None:
    global _cache_loaded_at
    now = datetime.now(timezone.utc)
    if _cache_loaded_at and (now - _cache_loaded_at) < timedelta(hours=_CACHE_TTL_HOURS):
        return
    data = await _get_json(client, TICKERS_URL, user_agent)
    if not isinstance(data, dict):
        raise SecEdgarError("SEC EDGAR devolvió un formato inesperado para el mapeo de tickers.")
    nuevo_cache: dict[str, str] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if isinstance(ticker, str) and cik is not None:
            nuevo_cache[ticker.upper()] = str(cik).zfill(10)
    _ticker_cik_cache.clear()
    _ticker_cik_cache.update(nuevo_cache)
    _cache_loaded_at = now


async def get_cik_for_ticker(
    client: httpx.AsyncClient, user_agent: str, ticker: str
) -> Optional[str]:
    """CIK de 10 dígitos (con ceros a la izquierda) para `ticker`, o `None`
    si no se encuentra o si SEC EDGAR falla (nunca lanza SecEdgarError hacia
    afuera — best-effort, mismo criterio que own_metrics/VIX en query_handler.py)."""
    try:
        await _ensure_ticker_cache(client, user_agent)
    except SecEdgarError:
        return None
    return _ticker_cik_cache.get(ticker.upper())


async def get_submissions(
    client: httpx.AsyncClient, user_agent: str, cik10: str
) -> Optional[dict]:
    """Historial de filings de `cik10`. `None` si SEC EDGAR falla (best-effort,
    no lanza hacia afuera — mismo criterio que get_cik_for_ticker)."""
    try:
        data = await _get_json(client, SUBMISSIONS_URL.format(cik10=cik10), user_agent)
    except SecEdgarError:
        return None
    return data if isinstance(data, dict) else None
