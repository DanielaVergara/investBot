"""Reglas de validación de empresa: ratios financieros + pilares de "buena empresa".

Funciones puras (sin I/O), fórmulas exactas de la sección "Reglas de
validación de empresa" de la spec, con las guardas del Spec Patch Iter-2
(B3: pasivos circulantes = 0; B4: EPS TTM <= 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def calculate_eps(net_income: float, shares_outstanding: float) -> Optional[float]:
    """EPS = Ganancia Neta / Número de Acciones."""
    if shares_outstanding is None or shares_outstanding <= 0:
        return None
    return net_income / shares_outstanding


def calculate_gross_margin(revenue: float, cost_of_revenue: float) -> Optional[float]:
    """Margen Bruto = (Ventas - Costo de Ventas) / Ventas."""
    if revenue is None or revenue == 0:
        return None
    return (revenue - cost_of_revenue) / revenue


@dataclass
class LiquidityResult:
    ratio_liquidez: Optional[float]
    liquidez_sin_pasivos_circulantes: bool


def calculate_liquidity_ratio(
    current_assets: float, current_liabilities: float
) -> LiquidityResult:
    """Ratio de Liquidez = Activos Circulantes / Pasivos Circulantes.

    B3 (Spec Patch Iter-2): si `current_liabilities == 0`, nunca se ejecuta
    la división. Se retorna `ratio_liquidez=None` y el flag
    `liquidez_sin_pasivos_circulantes=True` — empresa sin deuda de corto
    plazo, señal positiva de negocio, no un error.
    """
    if current_liabilities == 0:
        return LiquidityResult(ratio_liquidez=None, liquidez_sin_pasivos_circulantes=True)
    return LiquidityResult(
        ratio_liquidez=current_assets / current_liabilities,
        liquidez_sin_pasivos_circulantes=False,
    )


@dataclass
class PerResult:
    per: Optional[float]
    per_no_aplicable: bool


def calculate_per(price: float, eps_ttm: float) -> PerResult:
    """PER (P/E) = Precio de la Acción / EPS.

    B4 (Spec Patch Iter-2): si `eps_ttm <= 0`, nunca se ejecuta la división
    (evita ZeroDivisionError y un PER negativo sin sentido de negocio). Se
    retorna `per=None` y el flag `per_no_aplicable=True`.
    """
    if eps_ttm is None or eps_ttm <= 0:
        return PerResult(per=None, per_no_aplicable=True)
    return PerResult(per=price / eps_ttm, per_no_aplicable=False)


def calculate_ps(market_cap: float, revenue: float) -> Optional[float]:
    """P/S (Precio-Ventas) = Capitalización de Mercado / Ventas Totales.

    Siempre se calcula y se muestra (B4) — útil cuando EPS es negativo, pero
    nunca participa del promedio de Valor Justo Total.
    """
    if revenue is None or revenue <= 0:
        return None
    return market_cap / revenue


@dataclass
class KeyMetricsExtras:
    roe: Optional[float]
    debt_to_equity: Optional[float]
    net_debt_to_ebitda: Optional[float]
    dividend_yield: Optional[float]
    payout_ratio: Optional[float]


def extract_key_metrics_extras(metrics: Optional[dict]) -> KeyMetricsExtras:
    """Lee ROE, Debt-to-Equity, Net Debt/EBITDA, Dividend Yield y Payout
    Ratio del dict más reciente de /key-metrics (anual) del ticker propio.
    No calcula nada — FMP ya precalcula estos campos. Guarda de tipo: si
    el campo está ausente, es None, o no es int/float, se descarta como
    None (nunca crashea, nunca inventa un valor, nunca intenta parsear
    strings). No filtra por signo ni rango — un ROE negativo, un
    Debt-to-Equity fuera de rango típico, o un Payout Ratio > 100% son
    señales financieras reales (patrimonio negativo, sobre-endeudamiento,
    reparto de dividendos pese a pérdidas) y se muestran tal cual, sin
    interpretación numérica adicional (eso sería agregar complejidad
    matemática nueva, fuera de alcance de esta spec).
    """

    def _num(key: str) -> Optional[float]:
        if not metrics:
            return None
        value = metrics.get(key)
        return value if isinstance(value, (int, float)) else None

    return KeyMetricsExtras(
        roe=_num("roe"),
        debt_to_equity=_num("debtToEquity"),
        net_debt_to_ebitda=_num("netDebtToEBITDA"),
        dividend_yield=_num("dividendYield"),
        payout_ratio=_num("payoutRatio"),
    )


@dataclass
class PillarsResult:
    ingresos_crecientes: bool
    utilidades_crecientes: bool
    deuda_controlada: bool
    precio_razonable: Optional[bool]
    ventaja_competitiva: str = "revisar_manualmente"


def _es_creciente(historial: list[float]) -> bool:
    """True si la serie (ordenada de más antiguo a más reciente) es no decreciente
    y el valor más reciente supera al más antiguo — crecimiento año a año."""
    if not historial or len(historial) < 2:
        return False
    return all(
        historial[i] <= historial[i + 1] for i in range(len(historial) - 1)
    ) and historial[-1] > historial[0]


def evaluate_pillars(
    *,
    revenue_historial: list[float],
    net_income_historial: list[float],
    liquidity: LiquidityResult,
    barata: Optional[bool],
) -> PillarsResult:
    """Evalúa los pilares de "buena empresa" (sección "Reglas de validación de empresa").

    - Ingresos que crecen año a año: serie de `/income-statement` no decreciente
      y el más reciente > el más antiguo.
    - Utilidades positivas y crecientes: mismo criterio + último valor > 0.
    - Deuda controlada: liquidez > 1, o `liquidez_sin_pasivos_circulantes=True`
      (B3 — sin deuda de corto plazo satisface trivialmente el criterio).
    - Precio razonable: `None` si no se pudo determinar "barata"/"cara" (ningún
      modelo de valoración calculable); si no, refleja esa clasificación.
    - Ventaja competitiva: siempre "revisar_manualmente" — nunca se deriva de datos.
    """
    ingresos_crecientes = _es_creciente(revenue_historial)
    utilidades_crecientes = _es_creciente(net_income_historial) and (
        net_income_historial[-1] > 0 if net_income_historial else False
    )
    deuda_controlada = liquidity.liquidez_sin_pasivos_circulantes or (
        liquidity.ratio_liquidez is not None and liquidity.ratio_liquidez > 1
    )
    return PillarsResult(
        ingresos_crecientes=ingresos_crecientes,
        utilidades_crecientes=utilidades_crecientes,
        deuda_controlada=deuda_controlada,
        precio_razonable=barata,
    )
