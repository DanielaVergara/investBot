"""Contexto de mercado — momentum de precio + comparación explícita con peers.

Spec Patch [Iter-3] sección 6 + [Iter-4] (C2/C3). Funciones puras, sin I/O,
mismo estándar que `valuation.py`/`rules.py` (criterio de `qa`, Iter-1
sección 6) — reciben datos ya resueltos (`/quote` del ticker propio,
`peers.PeerAverageResult` ya calculado) como parámetros.

Bucket de cobertura: 70% ("resto", mismo nivel que `rules.py` — información
complementaria/contextual, no el motor de Valor Justo en sí, confirmado por
`qa`/`architect` en Iter-3/Iter-4).

Presupuesto de requests FMP: `calculate_momentum`/`compare_to_peers` siguen
sin llamadas HTTP nuevas — el momentum usa campos ya presentes en `/quote`
(`yearHigh`, `yearLow`, `priceAvg50`, `priceAvg200`), ya solicitado hoy; la
comparación con peers reutiliza el mismo array de PERs que `peers.py` ya
calcula para el modelo de Múltiplos. **Excepción (Ampliación de alcance,
VIX):** `extract_vix_context` en sí sigue siendo una función pura sin I/O,
pero el `quote` que recibe como parámetro (resuelto para `VIX_SYMBOL`,
"^VIX") sí requiere un fetch nuevo (`fmp_client.get_quote` en
`query_handler.py`, best-effort) — la afirmación de "cero llamadas HTTP
nuevas" ya no aplica al módulo en conjunto, solo a las funciones de
momentum/peers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


VIX_SYMBOL = "^VIX"


@dataclass
class VixResult:
    valor: Optional[float]
    disponible: bool


def extract_vix_context(quote: Optional[dict]) -> VixResult:
    """Lee el nivel del VIX (CBOE Volatility Index) desde un /quote ya
    resuelto para VIX_SYMBOL ("^VIX"). Función pura, sin I/O — el fetch
    ocurre en query_handler.py, best-effort (Decisión #7), mismo patrón
    que rules.extract_key_metrics_extras (guarda de tipo, nunca crashea).

    Deliberadamente NO clasifica el valor en "alta"/"baja"/"moderada"
    volatilidad — eso requeriría un umbral numérico nuevo no acordado
    con Daniela (mismo criterio que la Restricción de "no agregar
    interpretación numérica nueva" ya vigente para ROE/deuda/dividendos,
    Decisión #2/#6). El VIX se muestra como dato crudo con su
    explicación textual en summary.py, nunca como semáforo. Si Daniela
    quiere umbrales de "alta/baja volatilidad" más adelante, es una spec
    patch separada con el umbral acordado explícitamente.
    """
    if not quote:
        return VixResult(valor=None, disponible=False)
    valor = quote.get("price")
    valor = valor if isinstance(valor, (int, float)) else None
    return VixResult(valor=valor, disponible=valor is not None)


@dataclass
class MomentumResult:
    pct_vs_year_high: Optional[float]  # negativo o 0 si está en su máximo anual
    pct_vs_year_low: Optional[float]  # positivo típicamente
    pct_vs_avg_50: Optional[float]
    pct_vs_avg_200: Optional[float]
    etiqueta: str  # "impulso_positivo" | "impulso_negativo" | "mixto" | "no_disponible"


def _pct_vs(price: float, referencia: Optional[float]) -> Optional[float]:
    """`(price - referencia) / referencia * 100`, o `None` si `referencia`
    falta o es 0 (evita división por cero sin inventar un valor)."""
    if referencia is None or referencia == 0:
        return None
    return (price - referencia) / referencia * 100


def calculate_momentum(
    *,
    price: float,
    year_high: Optional[float],
    year_low: Optional[float],
    price_avg_50: Optional[float],
    price_avg_200: Optional[float],
) -> MomentumResult:
    """Nunca lanza excepción; campos faltantes producen `None` puntual.

    `etiqueta`:
      - `"no_disponible"` si falta `price_avg_50` **o** `price_avg_200`
        (cualquiera de los dos, no solo si faltan ambos — Spec Patch
        Iter-4, C2). Las 3 etiquetas cualitativas requieren la comparación
        simultánea contra ambos promedios; con un solo dato disponible no
        hay suficiente información para una lectura de tendencia confiable
        (ej. un rebote de corto plazo dentro de una tendencia bajista de
        largo plazo no se puede distinguir de un "impulso positivo" real
        con un solo promedio).
      - `"impulso_positivo"` si `price > price_avg_50` y `price > price_avg_200`.
      - `"impulso_negativo"` si `price < price_avg_50` y `price < price_avg_200`.
      - `"mixto"` en cualquier otro caso con ambos datos presentes.

    Los campos numéricos individuales (`pct_vs_avg_50`/`pct_vs_avg_200`/
    `pct_vs_year_high`/`pct_vs_year_low`) se calculan de forma independiente
    entre sí — si falta uno, los demás igual se calculan si su dato de
    origen está presente.
    """
    pct_vs_year_high = _pct_vs(price, year_high)
    pct_vs_year_low = _pct_vs(price, year_low)
    pct_vs_avg_50 = _pct_vs(price, price_avg_50)
    pct_vs_avg_200 = _pct_vs(price, price_avg_200)

    if price_avg_50 is None or price_avg_200 is None:
        etiqueta = "no_disponible"
    elif price > price_avg_50 and price > price_avg_200:
        etiqueta = "impulso_positivo"
    elif price < price_avg_50 and price < price_avg_200:
        etiqueta = "impulso_negativo"
    else:
        etiqueta = "mixto"

    return MomentumResult(
        pct_vs_year_high=pct_vs_year_high,
        pct_vs_year_low=pct_vs_year_low,
        pct_vs_avg_50=pct_vs_avg_50,
        pct_vs_avg_200=pct_vs_avg_200,
        etiqueta=etiqueta,
    )


@dataclass
class PeerComparisonResult:
    per_propio: Optional[float]
    per_minimo_peers: Optional[float]
    per_promedio_peers: Optional[float]
    per_maximo_peers: Optional[float]
    peers_usados: list[str]
    posicion: str  # "mas_barata" | "en_linea" | "mas_cara" | "no_comparable"
    motivo_no_comparable: Optional[str] = None
    # "eps_no_positivo" | "sin_peers_validos" | "un_solo_peer_valido"
    # (Spec Patch Iter-4, C3). `None` cuando `posicion != "no_comparable"`.


def compare_to_peers(
    *,
    per_propio: Optional[float],
    per_minimo_peers: Optional[float],
    per_promedio_peers: Optional[float],
    per_maximo_peers: Optional[float],
    peers_usados: list[str],
) -> PeerComparisonResult:
    """Función pura, sin I/O.

    `posicion="no_comparable"` con `motivo_no_comparable` en 3 casos (Spec
    Patch Iter-4 agrega el tercero):
      - `"eps_no_positivo"` si `per_propio` es `None`.
      - `"sin_peers_validos"` si `len(peers_usados) == 0`.
      - `"un_solo_peer_valido"` si `len(peers_usados) == 1` (`per_minimo_peers
        == per_promedio_peers == per_maximo_peers`, no hay rango real contra
        el cual comparar, aunque los 3 campos numéricos existan) — nunca
        `"mas_barata"`/`"en_linea"`/`"mas_cara"`, que sugerirían un rango
        real inexistente.

    Nunca inventa una posición cuando no hay dato suficiente.
    """
    if per_propio is None:
        return PeerComparisonResult(
            per_propio=per_propio,
            per_minimo_peers=per_minimo_peers,
            per_promedio_peers=per_promedio_peers,
            per_maximo_peers=per_maximo_peers,
            peers_usados=peers_usados,
            posicion="no_comparable",
            motivo_no_comparable="eps_no_positivo",
        )
    if len(peers_usados) == 0:
        return PeerComparisonResult(
            per_propio=per_propio,
            per_minimo_peers=per_minimo_peers,
            per_promedio_peers=per_promedio_peers,
            per_maximo_peers=per_maximo_peers,
            peers_usados=peers_usados,
            posicion="no_comparable",
            motivo_no_comparable="sin_peers_validos",
        )
    if len(peers_usados) == 1:
        return PeerComparisonResult(
            per_propio=per_propio,
            per_minimo_peers=per_minimo_peers,
            per_promedio_peers=per_promedio_peers,
            per_maximo_peers=per_maximo_peers,
            peers_usados=peers_usados,
            posicion="no_comparable",
            motivo_no_comparable="un_solo_peer_valido",
        )

    if per_propio < per_minimo_peers:
        posicion = "mas_barata"
    elif per_propio > per_maximo_peers:
        posicion = "mas_cara"
    else:
        posicion = "en_linea"

    return PeerComparisonResult(
        per_propio=per_propio,
        per_minimo_peers=per_minimo_peers,
        per_promedio_peers=per_promedio_peers,
        per_maximo_peers=per_maximo_peers,
        peers_usados=peers_usados,
        posicion=posicion,
        motivo_no_comparable=None,
    )
