"""Reglas de validación de empresa: ratios financieros + pilares de "buena empresa".

Funciones puras (sin I/O), fórmulas exactas de la sección "Reglas de
validación de empresa" de la spec, con las guardas del Spec Patch Iter-2
(B3: pasivos circulantes = 0; B4: EPS TTM <= 0).

`SDD_eps_ttm_real.md` (Decisión #9) agrega las funciones TTM generalizadas
(`sum_ttm_field`/`calculate_income_statement_ttm`/`calculate_fcf_ttm`) usadas
por `query_handler.py` para derivar EPS TTM real, P/S y los inputs del WACC
de una suma de los últimos 4 trimestres, en vez de un solo reporte anual.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Constantes de fuente de datos (mismo patrón que peers.PEERS_FUENTE_*) —
# Decisión #9 de SDD_eps_ttm_real.md.
DATOS_FUENTE_TRIMESTRAL = "trimestral_real"
DATOS_FUENTE_ANUAL_FALLBACK = "anual_fallback"


def _is_valid_number(value: object) -> bool:
    """`True` solo para `int`/`float` reales — excluye `bool` explícitamente
    (`isinstance(True, int)` es `True` en Python, y un booleano colándose
    como cifra financiera sería un bug silencioso, no un dato válido)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sum_ttm_field(quarterly_statements: list[dict], field: str) -> Optional[float]:
    """Suma `field` de los primeros 4 elementos de `quarterly_statements`
    (recent-first, FMP confirmado con `curl` real). `None` si hay menos de 4
    elementos o si alguno de los 4 tiene el campo ausente/`None`/no numérico
    (incluye `bool`) — nunca suma parcialmente con menos de 4 trimestres
    reales."""
    primeros_4 = quarterly_statements[:4]
    valores = [q.get(field) for q in primeros_4]
    if len(quarterly_statements) < 4 or any(not _is_valid_number(v) for v in valores):
        return None
    return sum(valores)


@dataclass
class IncomeStatementTtmResult:
    disponible: bool
    net_income_ttm: Optional[float] = None
    revenue_ttm: Optional[float] = None
    cost_of_revenue_ttm: Optional[float] = None
    interest_expense_ttm: Optional[float] = None
    income_tax_expense_ttm: Optional[float] = None
    income_before_tax_ttm: Optional[float] = None
    shares_outstanding_reciente: Optional[float] = None


_INCOME_STATEMENT_TTM_CAMPOS = (
    "netIncome",
    "revenue",
    "costOfRevenue",
    "interestExpense",
    "incomeTaxExpense",
    "incomeBeforeTax",
)


def calculate_income_statement_ttm(
    quarterly_statements: list[dict],
) -> IncomeStatementTtmResult:
    """TTM real de los 6 campos de `/income-statement` que antes de esta spec
    se leían del último reporte anual. Diseño ATÓMICO por decisión explícita
    (Decisión #9, `SDD_eps_ttm_real.md`): si CUALQUIERA de los 6 campos no se
    puede sumar en TTM (falta un trimestre, un campo viene no numérico), se
    descarta el paquete COMPLETO — `disponible=False` — en vez de mezclar
    fuentes campo por campo (ej. EPS TTM real pero P/S con revenue anual).
    """
    sumas = {
        campo: sum_ttm_field(quarterly_statements, campo)
        for campo in _INCOME_STATEMENT_TTM_CAMPOS
    }
    if any(v is None for v in sumas.values()):
        return IncomeStatementTtmResult(disponible=False)

    shares = quarterly_statements[0].get("weightedAverageShsOutDil") or quarterly_statements[
        0
    ].get("weightedAverageShsOut")
    if not _is_valid_number(shares) or shares <= 0:
        return IncomeStatementTtmResult(disponible=False)

    return IncomeStatementTtmResult(
        disponible=True,
        net_income_ttm=sumas["netIncome"],
        revenue_ttm=sumas["revenue"],
        cost_of_revenue_ttm=sumas["costOfRevenue"],
        interest_expense_ttm=sumas["interestExpense"],
        income_tax_expense_ttm=sumas["incomeTaxExpense"],
        income_before_tax_ttm=sumas["incomeBeforeTax"],
        shares_outstanding_reciente=shares,
    )


def calculate_fcf_ttm(fcf_historial: list[float]) -> Optional[float]:
    """FCF TTM = suma de los últimos 4 valores de `fcf_historial` (ya
    construido con la fórmula existente `operatingCashFlow -
    abs(capitalExpenditure)`, aplicada por período, orden cronológico
    antiguo→reciente). `None` si hay menos de 4 períodos disponibles."""
    ultimos_4 = fcf_historial[-4:]
    if len(ultimos_4) < 4:
        return None
    return sum(ultimos_4)


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
    """True si el valor más reciente de la serie supera al más antiguo —
    crecimiento de punta a punta de la ventana disponible (hasta 5 años
    anuales de FMP), sin exigir que cada paso intermedio sea no decreciente.
    Mismo criterio de "extremos" que valuation.calculate_cagr() usa para
    Graham/DCF (Pregunta 1, RESUELTA por Daniela: Opción A) — sin heredar
    la guarda de signo en la base de calculate_cagr() (acá no importa que
    historial[0] sea <= 0, a diferencia de calculate_cagr, para no excluir
    a empresas en turnaround del pilar de crecimiento).
    """
    if not historial or len(historial) < 2:
        return False
    return historial[-1] > historial[0]


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
