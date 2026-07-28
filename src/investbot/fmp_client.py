"""Wrapper HTTP a Financial Modeling Prep — solo endpoints gratuitos.

`/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`,
`/key-metrics`, `/profile`, `/search`. Nunca usa `/dcf`, `/sector-pe-ratio` ni
`/treasury-rates` (Decisión de diseño #8).

Cumple los criterios de `security` (sección 2 y 3):
- El cliente HTTP (`httpx.AsyncClient`) se recibe **inyectado**, nunca
  instanciado como global de módulo — permite mockear con
  `httpx.MockTransport`/`respx` en tests sin red real.
- Todas las llamadas usan `params=` de `httpx` (nunca f-string/concatenación
  de texto de usuario en la URL) — mitiga inyección y garantiza URL-encoding.
- Las excepciones de `httpx` se capturan en el punto de la llamada y se
  traducen a `FMPError`, que **nunca** incluye la URL ni los params
  originales (que contienen la API key) en su mensaje.
- Solo se loguea endpoint/ticker/status code, nunca la URL cruda.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://financialmodelingprep.com/api/v3"
DEFAULT_TIMEOUT = 10.0


class FMPError(Exception):
    """Error sanitizado de FMP — nunca incluye la URL ni la API key."""


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any],
    *,
    endpoint_label: str,
) -> Any:
    """GET genérico contra FMP. Sanitiza cualquier excepción antes de propagarla."""
    url = f"{BASE_URL}{path}"
    try:
        response = await client.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("FMP timeout — endpoint=%s", endpoint_label)
        raise FMPError(
            "FMP no respondió a tiempo. Intenta de nuevo en unos minutos."
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("FMP error HTTP — endpoint=%s status=%s", endpoint_label, status)
        if status == 429:
            raise FMPError(
                "Parece que se acabó el cupo diario de FMP (250 requests/día "
                "del plan gratuito). Intenta mañana."
            )
        if status in (401, 403):
            raise FMPError(
                "La API key de FMP parece inválida o vencida. Revisa la "
                "configuración del bot."
            )
        raise FMPError(f"FMP respondió con un error ({status}). Intenta más tarde.")
    except httpx.RequestError:
        logger.warning("FMP error de red — endpoint=%s", endpoint_label)
        raise FMPError("No pude conectarme a FMP ahora mismo. Intenta más tarde.")

    try:
        return response.json()
    except ValueError:
        logger.warning("FMP respuesta no-JSON — endpoint=%s", endpoint_label)
        raise FMPError("FMP devolvió una respuesta inesperada. Intenta más tarde.")


async def search_company(
    client: httpx.AsyncClient, api_key: str, query: str, limit: int = 10
) -> list[dict]:
    """Resolución nombre/ticker → lista de coincidencias (Decisión de diseño #2)."""
    data = await _get(
        client,
        "/search",
        {"query": query, "limit": limit, "apikey": api_key},
        endpoint_label="/search",
    )
    return data if isinstance(data, list) else []


async def get_quote(
    client: httpx.AsyncClient, api_key: str, ticker: str
) -> Optional[dict]:
    data = await _get(
        client, f"/quote/{ticker}", {"apikey": api_key}, endpoint_label="/quote"
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


async def get_profile(
    client: httpx.AsyncClient, api_key: str, ticker: str
) -> Optional[dict]:
    data = await _get(
        client, f"/profile/{ticker}", {"apikey": api_key}, endpoint_label="/profile"
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


async def get_income_statement(
    client: httpx.AsyncClient,
    api_key: str,
    ticker: str,
    *,
    period: str = "annual",
    limit: int = 5,
) -> list[dict]:
    data = await _get(
        client,
        f"/income-statement/{ticker}",
        {"period": period, "limit": limit, "apikey": api_key},
        endpoint_label="/income-statement",
    )
    return data if isinstance(data, list) else []


async def get_balance_sheet_statement(
    client: httpx.AsyncClient,
    api_key: str,
    ticker: str,
    *,
    period: str = "annual",
    limit: int = 5,
) -> list[dict]:
    data = await _get(
        client,
        f"/balance-sheet-statement/{ticker}",
        {"period": period, "limit": limit, "apikey": api_key},
        endpoint_label="/balance-sheet-statement",
    )
    return data if isinstance(data, list) else []


async def get_cash_flow_statement(
    client: httpx.AsyncClient,
    api_key: str,
    ticker: str,
    *,
    period: str = "annual",
    limit: int = 5,
) -> list[dict]:
    data = await _get(
        client,
        f"/cash-flow-statement/{ticker}",
        {"period": period, "limit": limit, "apikey": api_key},
        endpoint_label="/cash-flow-statement",
    )
    return data if isinstance(data, list) else []


async def get_key_metrics(
    client: httpx.AsyncClient,
    api_key: str,
    ticker: str,
    *,
    period: str = "annual",
    limit: int = 5,
) -> list[dict]:
    data = await _get(
        client,
        f"/key-metrics/{ticker}",
        {"period": period, "limit": limit, "apikey": api_key},
        endpoint_label="/key-metrics",
    )
    return data if isinstance(data, list) else []
