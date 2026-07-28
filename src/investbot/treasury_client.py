"""Cliente para el rendimiento del bono del tesoro EEUU a 20 años (Y).

Decisión de diseño #7 (revisada): fuente primaria FRED (serie `DGS20`, API
gratuita con key). Fallback: Treasury.gov (feed público CSV, sin key). Si
ninguna responde, se lanza `TreasuryError` explícito — nunca se usa un valor
hardcodeado en silencio. Esta llamada no consume el cupo de 250 req/día de
FMP (proveedor distinto).

Devuelve siempre `Y` como fracción decimal (ej. 0.044 = 4.4%) — consistente
con su uso en CAPM/WACC (`valuation.calculate_wacc`); `valuation.py` convierte
a puntos porcentuales solo dentro de la fórmula de Graham.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_ID = "DGS20"
TREASURY_GOV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
)
DEFAULT_TIMEOUT = 10.0

SOURCE_FRED = "FRED (serie DGS20)"
SOURCE_TREASURY_GOV = "Treasury.gov (fallback)"


class TreasuryError(Exception):
    """Error sanitizado de FRED/Treasury.gov — nunca incluye la URL ni la API key."""


@dataclass
class TreasuryYieldResult:
    y: float
    source: str


async def get_treasury_yield_fred(
    client: httpx.AsyncClient, api_key: str
) -> float:
    """Y desde FRED, serie DGS20. Devuelve fracción decimal."""
    try:
        response = await client.get(
            FRED_BASE_URL,
            params={
                "series_id": FRED_SERIES_ID,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        logger.warning("FRED timeout")
        raise TreasuryError("FRED no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        logger.warning("FRED error HTTP — status=%s", exc.response.status_code)
        raise TreasuryError(f"FRED respondió con un error ({exc.response.status_code}).")
    except httpx.RequestError:
        logger.warning("FRED error de red")
        raise TreasuryError("No pude conectarme a FRED ahora mismo.")
    except ValueError:
        logger.warning("FRED respuesta no-JSON")
        raise TreasuryError("FRED devolvió una respuesta inesperada.")

    observations = data.get("observations", []) if isinstance(data, dict) else []
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value and raw_value != ".":
            try:
                return float(raw_value) / 100
            except ValueError:
                continue
    logger.warning("FRED sin observaciones válidas de DGS20")
    raise TreasuryError("FRED no tiene un valor reciente disponible para DGS20.")


def _parse_treasury_gov_csv(text: str) -> Optional[float]:
    """Parsea el CSV de Treasury.gov, columna '20 Yr', última fila disponible."""
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return None
    header_20yr = None
    for key in rows[0].keys():
        if key and "20" in key and "yr" in key.lower():
            header_20yr = key
            break
    if header_20yr is None:
        return None
    for row in reversed(rows):  # última fecha disponible al final del CSV
        raw_value = row.get(header_20yr)
        if raw_value and raw_value.strip():
            try:
                return float(raw_value) / 100
            except ValueError:
                continue
    return None


async def get_treasury_yield_treasury_gov(
    client: httpx.AsyncClient, year: Optional[int] = None
) -> float:
    """Y desde el feed público de Treasury.gov (fallback de segundo nivel)."""
    import datetime

    year = year or datetime.date.today().year
    url = TREASURY_GOV_URL.format(year=year)
    try:
        response = await client.get(
            url,
            params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "page": "", "_format": "csv"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        text = response.text
    except httpx.TimeoutException:
        logger.warning("Treasury.gov timeout")
        raise TreasuryError("Treasury.gov no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        logger.warning("Treasury.gov error HTTP — status=%s", exc.response.status_code)
        raise TreasuryError(
            f"Treasury.gov respondió con un error ({exc.response.status_code})."
        )
    except httpx.RequestError:
        logger.warning("Treasury.gov error de red")
        raise TreasuryError("No pude conectarme a Treasury.gov ahora mismo.")

    value = _parse_treasury_gov_csv(text)
    if value is None:
        logger.warning("Treasury.gov CSV sin columna/valor de 20 años parseable")
        raise TreasuryError("Treasury.gov no tiene un valor de 20 años parseable hoy.")
    return value


async def get_treasury_yield(
    fred_client: httpx.AsyncClient,
    treasury_gov_client: httpx.AsyncClient,
    fred_api_key: Optional[str],
) -> TreasuryYieldResult:
    """Orquesta FRED (primario) → Treasury.gov (fallback). Nunca inventa un valor."""
    if fred_api_key:
        try:
            y = await get_treasury_yield_fred(fred_client, fred_api_key)
            return TreasuryYieldResult(y=y, source=SOURCE_FRED)
        except TreasuryError:
            logger.warning("FRED falló, intentando fallback Treasury.gov")
    else:
        logger.warning("FRED_API_KEY no configurada, intentando fallback Treasury.gov")

    try:
        y = await get_treasury_yield_treasury_gov(treasury_gov_client)
        return TreasuryYieldResult(y=y, source=SOURCE_TREASURY_GOV)
    except TreasuryError as exc:
        raise TreasuryError(
            "No pude obtener el rendimiento del bono del tesoro (FRED y "
            "Treasury.gov fallaron). No es posible calcular el modelo Graham "
            "ni el DCF para esta consulta."
        ) from exc
