"""Wrapper HTTP a Financial Modeling Prep — solo endpoints gratuitos.

Usa la API "stable" de FMP (`/stable/...`, ticker como query param `symbol=`)
— la API legacy (`/api/v3/...`, ticker en el path) fue discontinuada por FMP
para cuentas creadas después del 31/08/2025 (verificado con una key real
durante el despliegue, error "Legacy Endpoint" en cualquier llamada a v3).

`/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`,
`/key-metrics`, `/key-metrics-ttm`, `/profile`, `/search-symbol`. Nunca usa
`/dcf`, `/sector-pe-ratio` ni `/treasury-rates` (Decisión de diseño #8).

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

BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_TIMEOUT = 10.0

# Cota de tamaño para el match del sub-caso "símbolo premium" de un 402
# (criterio de `security`): evita depender de un body anómalamente grande.
_MAX_BODY_CHARS_402 = 2000


class FMPError(Exception):
    """Error sanitizado de FMP — nunca incluye la URL ni la API key."""


def _is_symbol_premium_402(body_text: Optional[str]) -> bool:
    """True si el body de un 402 corresponde al sub-caso "símbolo puntual no
    disponible en el plan gratuito" (confirmado con `curl` real para
    MELI/DRAM sobre `/quote`), en vez de un 402 genérico no reconocido.

    Función pura, sin efectos secundarios — no loguea ni lee red. Evalúa,
    case-insensitive, si están presentes ambos substrings
    `"special endpoint"` y `"symbol"` (AND, no OR — evita falsos positivos
    sobre otros 402 no confirmados). Opera sobre como máximo los primeros
    `_MAX_BODY_CHARS_402` caracteres del body, truncado antes de aplicar
    `.lower()`/`in`.
    """
    if not body_text:
        return False
    truncated = body_text[:_MAX_BODY_CHARS_402].lower()
    return "special endpoint" in truncated and "symbol" in truncated


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
        if status == 402:
            try:
                body_text: Optional[str] = exc.response.text
            except (UnicodeDecodeError, RuntimeError):
                body_text = None

            if body_text is None:
                subcaso = "cuerpo_no_legible"
                es_simbolo_premium = False
            else:
                es_simbolo_premium = _is_symbol_premium_402(body_text)
                subcaso = "simbolo_premium" if es_simbolo_premium else "generico"

            logger.warning(
                "FMP error 402 — endpoint=%s subcaso=%s", endpoint_label, subcaso
            )

            if es_simbolo_premium:
                symbol = params.get("symbol")
                ticker_txt = f"El ticker {symbol}" if symbol else "Este ticker"
                raise FMPError(
                    f"{ticker_txt} no está disponible en el plan gratuito de "
                    "FMP para esta consulta. Prueba con otro ticker."
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
        "/search-symbol",
        {"query": query, "limit": limit, "apikey": api_key},
        endpoint_label="/search-symbol",
    )
    return data if isinstance(data, list) else []


async def get_quote(
    client: httpx.AsyncClient, api_key: str, ticker: str
) -> Optional[dict]:
    data = await _get(
        client,
        "/quote",
        {"symbol": ticker, "apikey": api_key},
        endpoint_label="/quote",
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


async def get_profile(
    client: httpx.AsyncClient, api_key: str, ticker: str
) -> Optional[dict]:
    data = await _get(
        client,
        "/profile",
        {"symbol": ticker, "apikey": api_key},
        endpoint_label="/profile",
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
        "/income-statement",
        {"symbol": ticker, "period": period, "limit": limit, "apikey": api_key},
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
        "/balance-sheet-statement",
        {"symbol": ticker, "period": period, "limit": limit, "apikey": api_key},
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
        "/cash-flow-statement",
        {"symbol": ticker, "period": period, "limit": limit, "apikey": api_key},
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
        "/key-metrics",
        {"symbol": ticker, "period": period, "limit": limit, "apikey": api_key},
        endpoint_label="/key-metrics",
    )
    return data if isinstance(data, list) else []


