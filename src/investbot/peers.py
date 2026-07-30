"""Peers por sector + promedio de PER (Decisión de diseño #9; peers dinámicos
vía Finnhub, spec `SDD_peers_dinamicos_y_eventos_corporativos.md` Parte 1).

Fuente PRIMARIA (si `FINNHUB_API_KEY` está configurada y responde con al
menos `MIN_PEERS_DINAMICOS_PARA_USAR` candidatos): peers dinámicos de
Finnhub, agrupados por sub-industria (`grouping="subIndustry"`), inyectados
vía `get_dynamic_peers_fn`. Reemplaza el uso de `/sector-pe-ratio` (tier
pago de FMP) tanto para la fuente dinámica como para el respaldo.

Fuente de RESPALDO (si Finnhub no está configurada, falla, o devuelve menos
de `MIN_PEERS_DINAMICOS_PARA_USAR` candidatos válidos): el set fijo de 3 a 5
tickers peer hardcodeados por sector (`PEERS_BY_SECTOR`), mantenido
manualmente — ya no es la única fuente, pero sigue existiendo tal cual. Se
documenta explícitamente al usuario, por consulta, cuál de las 2 fuentes se
usó (`fuente_peers`, ver `PeerAverageResult` / `summary._build_peers_note`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# Motivos posibles por los que un peer candidato no aporta un PER válido esta
# consulta (Spec: SDD_procedencia_peers_individuales, Resolución Pregunta 2 —
# 2 motivos, ninguno más).
PEER_MOTIVO_SIN_DATO = "sin_dato"
# La llamada a /key-metrics para ese peer falló, vino vacía, o el campo
# earningsYield no vino en forma numérica utilizable. No se distingue el
# motivo técnico exacto (timeout/402/429/campo ausente/campo no numérico)
# — todos son "no llegó un dato utilizable", mismo criterio ya usado en
# rules.extract_key_metrics_extras para "ausente" vs "no numérico".
PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO = "earnings_yield_no_positivo"
# earningsYield sí vino como número, pero es <= 0 — utilidades negativas o
# nulas del peer. Señal financiera real, distinta de "sin dato".

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

# Fuente de la lista de peers candidatos usada en una consulta puntual (spec
# `SDD_peers_dinamicos_y_eventos_corporativos.md` Parte 1) — informativo,
# nunca condiciona ningún cálculo.
PEERS_FUENTE_FINNHUB = "finnhub"
PEERS_FUENTE_FIJO = "fijo_respaldo"
# Tope de candidatos dinámicos a consultar contra FMP por consulta —
# Pregunta 1.3, RESUELTA por Daniela: 5 (reutiliza el límite superior ya
# documentado hoy en README.md, "3-5 peers", no un número nuevo).
MAX_PEERS_DINAMICOS = 5
# Mínimo de peers dinámicos que Finnhub debe devolver (tras filtrar el
# propio ticker) para aceptar la lista dinámica esta consulta — Pregunta
# 1.2, RESUELTA por Daniela: 3, igual al tamaño de la lista fija actual.
# Más exigente que "cualquier lista no vacía": si Finnhub devuelve 1 o 2
# tickers, se descarta esa lista completa y se cae al respaldo fijo
# (nunca se combinan ambas fuentes en la misma consulta — Restricción ya
# vigente). Aceptado explícitamente por Daniela: prioriza no aceptar un
# set de comparables más pobre que el que el bot ya usa hoy, a costa de
# caer al respaldo más seguido cuando Finnhub responde con una lista corta.
MIN_PEERS_DINAMICOS_PARA_USAR = 3


@dataclass
class PeerAverageResult:
    per_promedio: Optional[float]
    per_minimo: Optional[float]  # Spec Patch Iter-3, sección 2 — derivado del mismo array
    per_maximo: Optional[float]  # de PERs válidos, sin llamada adicional a /key-metrics.
    peers_usados: list[str]
    peers_pe: dict[str, float] = field(default_factory=dict)
    # PER individual de cada peer que sí devolvió un dato válido esta
    # consulta, ya calculado como 1/earningsYield (mismo valor que ya se
    # promediaba y se descartaba — no es un cálculo nuevo, es guardar lo que
    # ya se calculaba). Preserva el orden de PEERS_BY_SECTOR.
    peers_no_usados: dict[str, str] = field(default_factory=dict)
    # ticker -> motivo, para cada peer candidato del sector (excluyendo el
    # ticker propio) que esta consulta NO devolvió un PER válido. Valores:
    # PEER_MOTIVO_SIN_DATO | PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO.
    fuente_peers: str = PEERS_FUENTE_FIJO  # NUEVO — default preserva el
    # comportamiento de cualquier test/código que instancie el dataclass
    # sin pasar este campo.


def get_peers_for_sector(sector: str, own_ticker: str) -> list[str]:
    """Peers hardcodeados del sector, excluyendo el propio ticker si coincide."""
    peers = PEERS_BY_SECTOR.get(sector, [])
    return [p for p in peers if p.upper() != own_ticker.upper()]


async def get_peer_pe_average(
    *,
    get_peer_metrics_fn: Callable[[str], Awaitable[Optional[dict]]],
    sector: str,
    own_ticker: str,
    get_dynamic_peers_fn: Optional[Callable[[str], Awaitable[list[str]]]] = None,
) -> PeerAverageResult:
    """Promedia el PER de los peers, excluyendo al propio ticker.

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

    `get_dynamic_peers_fn` es una función inyectada opcional (normalmente un
    closure que llama a `finnhub_client.get_peers`, capturando
    `finnhub_client.FinnhubError` y devolviendo `[]` — mismo contrato que
    `_get_metrics_for_peer` ya usa con `FMPError`) que devuelve la lista de
    peers candidatos dinámicos de Finnhub para `own_ticker`. Si es `None`
    (Finnhub no configurado), se usa directamente `get_peers_for_sector`
    (comportamiento idéntico al de antes de esta spec). Si no es `None`, se
    filtra el propio ticker de la lista dinámica, se recorta a
    `MAX_PEERS_DINAMICOS`, y solo se acepta si quedan al menos
    `MIN_PEERS_DINAMICOS_PARA_USAR` candidatos — de lo contrario se cae al
    respaldo fijo (`get_peers_for_sector`). Nunca se combinan ambas fuentes
    en la misma consulta.
    """
    fuente_peers = PEERS_FUENTE_FIJO
    peers_candidatos: list[str] = []

    if get_dynamic_peers_fn is not None:
        dynamic = await get_dynamic_peers_fn(own_ticker)
        # Filtra el propio ticker (Finnhub a veces lo incluye en su propia
        # respuesta) y aplica el tope — mismo principio de "propio ticker
        # excluido" que get_peers_for_sector ya aplica hoy.
        dynamic = [p for p in dynamic if p.upper() != own_ticker.upper()][:MAX_PEERS_DINAMICOS]
        if len(dynamic) >= MIN_PEERS_DINAMICOS_PARA_USAR:
            peers_candidatos = dynamic
            fuente_peers = PEERS_FUENTE_FINNHUB

    if not peers_candidatos:
        peers_candidatos = get_peers_for_sector(sector, own_ticker)
        fuente_peers = PEERS_FUENTE_FIJO

    pes: list[float] = []
    usados: list[str] = []
    peers_pe: dict[str, float] = {}
    peers_no_usados: dict[str, str] = {}
    for peer in peers_candidatos:
        metrics = await get_peer_metrics_fn(peer)
        if not metrics:
            peers_no_usados[peer] = PEER_MOTIVO_SIN_DATO
            continue
        earnings_yield = metrics.get("earningsYield")
        if not isinstance(earnings_yield, (int, float)):
            # Campo ausente o no numérico -> mismo balde que "sin dato
            # utilizable" (Resolución Pregunta 2, no se agrega un 3er motivo).
            peers_no_usados[peer] = PEER_MOTIVO_SIN_DATO
            continue
        if earnings_yield > 0:
            per_peer = 1.0 / float(earnings_yield)
            pes.append(per_peer)
            usados.append(peer)
            peers_pe[peer] = per_peer
        else:
            peers_no_usados[peer] = PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO
    if not pes:
        return PeerAverageResult(
            per_promedio=None,
            per_minimo=None,
            per_maximo=None,
            peers_usados=[],
            peers_pe={},
            peers_no_usados=peers_no_usados,
            fuente_peers=fuente_peers,
        )
    return PeerAverageResult(
        per_promedio=sum(pes) / len(pes),
        per_minimo=min(pes),
        per_maximo=max(pes),
        peers_usados=usados,
        peers_pe=peers_pe,
        peers_no_usados=peers_no_usados,
        fuente_peers=fuente_peers,
    )
