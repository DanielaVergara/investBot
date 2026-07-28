"""Set fijo de peers por sector + promedio de PER (Decisión de diseño #9).

Reemplaza el uso de `/sector-pe-ratio` (tier pago de FMP): el PER promedio del
sector se aproxima con un set fijo de 3 a 5 tickers peer hardcodeados por
sector, mantenido manualmente. Se documenta explícitamente al usuario como
"PER promedio de un set fijo de comparables, no del sector completo".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

# Set fijo de peers por sector (nombres de sector tal como los devuelve
# `/profile` de FMP). Mantenimiento manual — si un peer deja de cotizar o
# cambia de sector, es un ajuste de configuración, no un bug (Decisión #9).
PEERS_BY_SECTOR: dict[str, list[str]] = {
    "Technology": ["MSFT", "ORCL", "CRM"],
    "Communication Services": ["GOOGL", "META", "DIS"],
    "Consumer Cyclical": ["AMZN", "HD", "MCD"],
    "Consumer Defensive": ["PG", "KO", "WMT"],
    "Healthcare": ["JNJ", "PFE", "UNH"],
    "Financial Services": ["JPM", "BAC", "GS"],
    "Energy": ["XOM", "CVX", "COP"],
    "Industrials": ["HON", "UPS", "CAT"],
    "Utilities": ["NEE", "DUK", "SO"],
    "Real Estate": ["PLD", "AMT", "EQIX"],
    "Basic Materials": ["LIN", "SHW", "FCX"],
}


@dataclass
class PeerAverageResult:
    per_promedio: Optional[float]
    peers_usados: list[str]


def get_peers_for_sector(sector: str, own_ticker: str) -> list[str]:
    """Peers hardcodeados del sector, excluyendo el propio ticker si coincide."""
    peers = PEERS_BY_SECTOR.get(sector, [])
    return [p for p in peers if p.upper() != own_ticker.upper()]


async def get_peer_pe_average(
    *,
    get_peer_metrics_fn: Callable[[str], Awaitable[Optional[dict]]],
    sector: str,
    own_ticker: str,
) -> PeerAverageResult:
    """Promedia el PER de los peers del sector, excluyendo al propio ticker.

    `get_peer_metrics_fn` es una función inyectada (normalmente
    `fmp_client.get_key_metrics` — anual, `limit=1` — parcialmente aplicada
    con el cliente HTTP y la API key) que devuelve el dict más reciente de
    `/key-metrics` para un ticker, o `None` si falló. La API stable de FMP ya
    no expone un campo `pe` directo en `/quote` (deprecado junto con la API
    legacy) — el PER se deriva como `1 / earningsYield`. Nota: `/key-metrics-ttm`
    habría dado un PER más "en vivo" (marketCap actual en vez del cierre del
    último año fiscal) pero es un endpoint de pago en el plan gratuito actual
    de FMP (verificado con una key real: 402 Payment Required) — se usa la
    variante anual, que sí es gratuita, como aproximación aceptada (mismo
    principio que el resto del modelo de Múltiplos, ya documentado como
    aproximación). Los peers con error, sin `earningsYield` numérico, o con
    `earningsYield` <= 0 (utilidades negativas o nulas) se excluyen del
    promedio sin abortar la consulta completa.
    """
    peers = get_peers_for_sector(sector, own_ticker)
    pes: list[float] = []
    usados: list[str] = []
    for peer in peers:
        metrics = await get_peer_metrics_fn(peer)
        if not metrics:
            continue
        earnings_yield = metrics.get("earningsYield")
        if isinstance(earnings_yield, (int, float)) and earnings_yield > 0:
            pes.append(1.0 / float(earnings_yield))
            usados.append(peer)
    if not pes:
        return PeerAverageResult(per_promedio=None, peers_usados=[])
    return PeerAverageResult(per_promedio=sum(pes) / len(pes), peers_usados=usados)
