"""Wrapper HTTP a Finnhub — solo el endpoint de peers dinámicos (Feature 1).

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

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 10.0


class FinnhubError(Exception):
    """Error sanitizado de Finnhub -- nunca incluye la URL ni la API key."""


async def get_peers(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    *,
    grouping: str = "subIndustry",
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
    """
    url = f"{BASE_URL}/stock/peers"
    try:
        response = await client.get(
            url,
            params={"symbol": symbol, "grouping": grouping, "token": api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("Finnhub timeout — endpoint=/stock/peers")
        raise FinnhubError("Finnhub no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("Finnhub error HTTP — endpoint=/stock/peers status=%s", status)
        raise FinnhubError(f"Finnhub respondió con un error ({status}).")
    except httpx.RequestError:
        logger.warning("Finnhub error de red — endpoint=/stock/peers")
        raise FinnhubError("No pude conectarme a Finnhub ahora mismo.")

    try:
        data: Any = response.json()
    except ValueError:
        logger.warning("Finnhub respuesta no-JSON — endpoint=/stock/peers")
        raise FinnhubError("Finnhub devolvió una respuesta inesperada.")

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]
