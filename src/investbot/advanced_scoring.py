"""5 modelos cuantitativos de análisis fundamental avanzado.

`SDD_analisis_fundamental_avanzado.md` — funciones puras, **sin I/O**, mismo
principio que `rules.py`/`valuation.py` (criterio de `qa`, "no hay lógica de
negocio escondida", testeable sin mockear HTTP). Fórmulas exactas de
`~/.claude/skills/analisis-fundamental-avanzado/SKILL.md` — no reinventadas.

Reciben los dicts crudos ya resueltos de balance/income/cash-flow (año más
reciente + año anterior, mismo orden que confirma la spec: "reciente"
primero) más precio/market cap/beta, y devuelven un resultado tipado según
el patrón del modelo (Decisión de diseño #4, no reabrir):

- **"Todo o nada"** (Altman Z/Z'', Beneish M-Score, Magic Formula): una única
  fórmula ponderada — si CUALQUIER campo de entrada falta/no es numérico, el
  modelo COMPLETO se reporta `disponible=False`, nunca con un valor
  sustituido ni parcial.
- **"Criterios independientes"** (Piotroski F-Score, factores estilo AQR):
  cada criterio/factor se evalúa por separado — el que falte se excluye sin
  afectar a los demás, reportando cuántos fueron evaluables.

D1 (RESUELTO por Daniela, no reabrir): Beneish M-Score se reporta SIEMPRE
como no calculable con este plan de FMP (PP&E bruto y depreciación pura no
están disponibles por separado) — nunca se calcula, ni con un proxy
documentado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers compartidos — duplicados localmente (no importados de rules.py),
# mismo criterio de bajo acoplamiento que ya usan peers.py/market_context.py
# (Decisión de diseño #1 de la spec).
# ---------------------------------------------------------------------------


def _is_valid_number(value: object) -> bool:
    """`True` solo para `int`/`float` reales — excluye `bool` explícitamente
    (mismo criterio que `rules._is_valid_number`: un booleano colándose como
    cifra financiera sería un bug silencioso, no un dato válido)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _get_num(source: Optional[dict], key: str) -> Optional[float]:
    """Lee `key` de `source` (dict crudo de FMP, puede ser `None`/`{}`).
    `None` si el dict falta, el campo está ausente, es `None`, o no es un
    número real — nunca lanza `KeyError`/`AttributeError`."""
    if not source:
        return None
    value = source.get(key)
    return value if _is_valid_number(value) else None


# ---------------------------------------------------------------------------
# 1. Altman Z-Score (+ variante Z'')
# ---------------------------------------------------------------------------

ALTMAN_Z_UMBRAL_SEGURO = 2.99
ALTMAN_Z_UMBRAL_GRIS = 1.81
ALTMAN_Z_PRIME_PRIME_UMBRAL_SEGURO = 2.6
ALTMAN_Z_PRIME_PRIME_UMBRAL_GRIS = 1.1

# D4 (no bloqueante, ajustable por `implementer` sin volver a preguntar):
# clasificación "asset-light" que dispara el cálculo de Z'' — sectores
# exactos de `profile.sector` (mismos nombres que `peers.PEERS_BY_SECTOR`,
# ya confirmados en producción) + palabras clave de `profile.industry`
# (ejemplos explícitos del `architect`: "Software", "Internet Content &
# Information", "Internet Retail").
ASSET_LIGHT_SECTORES = frozenset({"Technology", "Communication Services"})
ASSET_LIGHT_INDUSTRIA_KEYWORDS = ("software", "internet", "digital", "platform")


def is_asset_light(sector: Optional[str], industry: Optional[str]) -> bool:
    """D4: `True` si el sector/industria del ticker corresponde a una
    empresa "asset-light" (poco activo fijo) — dispara el cálculo de Z''
    además del Z original. Nunca lanza excepción con `None`."""
    if sector in ASSET_LIGHT_SECTORES:
        return True
    if industry:
        industry_lower = industry.lower()
        return any(kw in industry_lower for kw in ASSET_LIGHT_INDUSTRIA_KEYWORDS)
    return False


def _clasificar_zona(valor: float, umbral_seguro: float, umbral_gris: float) -> str:
    if valor > umbral_seguro:
        return "segura"
    if valor > umbral_gris:
        return "gris"
    return "riesgo"


@dataclass
class AltmanZResult:
    disponible: bool
    z: Optional[float] = None
    zona: Optional[str] = None  # "segura" | "gris" | "riesgo"
    campos_faltantes: list[str] = field(default_factory=list)
    # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- los 5
    # componentes A-E, EXACTAMENTE los mismos valores ya usados para sumar
    # `z`/`z''` (nunca recalculados aparte). `calculate_altman_z_prime_prime`
    # no usa `e` (sin el factor Ventas/Activos) -- queda `None` ahí.
    a: Optional[float] = None
    b: Optional[float] = None
    c: Optional[float] = None
    d: Optional[float] = None
    e: Optional[float] = None


def calculate_altman_z(
    *, balance: Optional[dict], income: Optional[dict], market_cap: Optional[float]
) -> AltmanZResult:
    """Z = 1.2A + 1.4B + 3.3C + 0.6D + 1.0E (fórmula original, empresas
    manufactureras/industriales). "Todo o nada": cualquier campo faltante o
    denominador en cero -> `disponible=False`, nunca un valor sustituido."""
    total_assets = _get_num(balance, "totalAssets")
    total_liabilities = _get_num(balance, "totalLiabilities")
    current_assets = _get_num(balance, "totalCurrentAssets")
    current_liabilities = _get_num(balance, "totalCurrentLiabilities")
    retained_earnings = _get_num(balance, "retainedEarnings")
    ebit = _get_num(income, "ebit")
    revenue = _get_num(income, "revenue")
    mc = market_cap if _is_valid_number(market_cap) else None

    campos = {
        "activos_totales": total_assets,
        "pasivos_totales": total_liabilities,
        "activos_corrientes": current_assets,
        "pasivos_corrientes": current_liabilities,
        "utilidades_retenidas": retained_earnings,
        "ebit": ebit,
        "ventas": revenue,
        "capitalizacion_mercado": mc,
    }
    faltantes = [nombre for nombre, valor in campos.items() if valor is None]
    if total_assets == 0 and "activos_totales" not in faltantes:
        faltantes.append("activos_totales_es_cero")
    if total_liabilities == 0 and "pasivos_totales" not in faltantes:
        faltantes.append("pasivos_totales_es_cero")
    if faltantes:
        return AltmanZResult(disponible=False, campos_faltantes=faltantes)

    working_capital = current_assets - current_liabilities
    a = working_capital / total_assets
    b = retained_earnings / total_assets
    c = ebit / total_assets
    d = mc / total_liabilities
    e = revenue / total_assets
    z = 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e
    return AltmanZResult(
        disponible=True,
        z=z,
        zona=_clasificar_zona(z, ALTMAN_Z_UMBRAL_SEGURO, ALTMAN_Z_UMBRAL_GRIS),
        a=a, b=b, c=c, d=d, e=e,
    )


def calculate_altman_z_prime_prime(
    *, balance: Optional[dict], income: Optional[dict], market_cap: Optional[float]
) -> AltmanZResult:
    """Z'' = 6.56A + 3.26B + 6.72C + 1.05D — variante sin el factor E
    (ventas/activos), pensada para empresas de servicios/tecnología con poco
    activo fijo (D4 decide cuándo se llama/muestra, no esta función). Mismo
    criterio "todo o nada" que `calculate_altman_z`."""
    total_assets = _get_num(balance, "totalAssets")
    total_liabilities = _get_num(balance, "totalLiabilities")
    current_assets = _get_num(balance, "totalCurrentAssets")
    current_liabilities = _get_num(balance, "totalCurrentLiabilities")
    retained_earnings = _get_num(balance, "retainedEarnings")
    ebit = _get_num(income, "ebit")
    mc = market_cap if _is_valid_number(market_cap) else None

    campos = {
        "activos_totales": total_assets,
        "pasivos_totales": total_liabilities,
        "activos_corrientes": current_assets,
        "pasivos_corrientes": current_liabilities,
        "utilidades_retenidas": retained_earnings,
        "ebit": ebit,
        "capitalizacion_mercado": mc,
    }
    faltantes = [nombre for nombre, valor in campos.items() if valor is None]
    if total_assets == 0 and "activos_totales" not in faltantes:
        faltantes.append("activos_totales_es_cero")
    if total_liabilities == 0 and "pasivos_totales" not in faltantes:
        faltantes.append("pasivos_totales_es_cero")
    if faltantes:
        return AltmanZResult(disponible=False, campos_faltantes=faltantes)

    working_capital = current_assets - current_liabilities
    a = working_capital / total_assets
    b = retained_earnings / total_assets
    c = ebit / total_assets
    d = mc / total_liabilities
    z_pp = 6.56 * a + 3.26 * b + 6.72 * c + 1.05 * d
    return AltmanZResult(
        disponible=True,
        z=z_pp,
        zona=_clasificar_zona(
            z_pp, ALTMAN_Z_PRIME_PRIME_UMBRAL_SEGURO, ALTMAN_Z_PRIME_PRIME_UMBRAL_GRIS
        ),
        a=a, b=b, c=c, d=d,
    )


# ---------------------------------------------------------------------------
# 2. Piotroski F-Score — 9 criterios independientes
# ---------------------------------------------------------------------------


@dataclass
class CriterioPiotroski:
    nombre: str
    cumplido: Optional[bool]  # `None` = no evaluable (dato faltante)
    # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- las magnitudes
    # reales que determinaron `cumplido` (nunca poblado si `cumplido is None`
    # -- criterio no evaluable, sin datos que mostrar).
    valores: Optional[dict[str, float]] = None


@dataclass
class PiotroskiResult:
    puntaje: int  # suma de criterios cumplidos entre los evaluables
    criterios_evaluables: int
    criterios_totales: int = 9
    criterios: list[CriterioPiotroski] = field(default_factory=list)


def _criterio_roa_positivo(net_income_t: Optional[float]) -> Optional[bool]:
    if net_income_t is None:
        return None
    return net_income_t > 0


def _criterio_cfo_positivo(cfo_t: Optional[float]) -> Optional[bool]:
    if cfo_t is None:
        return None
    return cfo_t > 0


def _criterio_roa_creciente(
    net_income_t: Optional[float],
    total_assets_t: Optional[float],
    net_income_t1: Optional[float],
    total_assets_t1: Optional[float],
) -> Optional[bool]:
    if None in (net_income_t, total_assets_t, net_income_t1, total_assets_t1):
        return None
    if total_assets_t == 0 or total_assets_t1 == 0:
        return None
    return (net_income_t / total_assets_t) > (net_income_t1 / total_assets_t1)


def _criterio_cfo_mayor_utilidad(
    cfo_t: Optional[float], net_income_t: Optional[float]
) -> Optional[bool]:
    if cfo_t is None or net_income_t is None:
        return None
    return cfo_t > net_income_t


def _criterio_apalancamiento_decreciente(
    lt_debt_t: Optional[float],
    total_assets_t: Optional[float],
    lt_debt_t1: Optional[float],
    total_assets_t1: Optional[float],
) -> Optional[bool]:
    if None in (lt_debt_t, total_assets_t, lt_debt_t1, total_assets_t1):
        return None
    if total_assets_t == 0 or total_assets_t1 == 0:
        return None
    return (lt_debt_t / total_assets_t) < (lt_debt_t1 / total_assets_t1)


def _criterio_liquidez_creciente(
    current_assets_t: Optional[float],
    current_liabilities_t: Optional[float],
    current_assets_t1: Optional[float],
    current_liabilities_t1: Optional[float],
) -> Optional[bool]:
    if None in (current_assets_t, current_liabilities_t, current_assets_t1, current_liabilities_t1):
        return None
    if current_liabilities_t == 0 or current_liabilities_t1 == 0:
        return None
    return (current_assets_t / current_liabilities_t) > (current_assets_t1 / current_liabilities_t1)


def _criterio_sin_dilucion(
    shares_t: Optional[float], shares_t1: Optional[float]
) -> Optional[bool]:
    if shares_t is None or shares_t1 is None:
        return None
    return shares_t <= shares_t1


def _criterio_margen_bruto_creciente(
    gross_profit_t: Optional[float],
    revenue_t: Optional[float],
    gross_profit_t1: Optional[float],
    revenue_t1: Optional[float],
) -> Optional[bool]:
    if None in (gross_profit_t, revenue_t, gross_profit_t1, revenue_t1):
        return None
    if revenue_t == 0 or revenue_t1 == 0:
        return None
    return (gross_profit_t / revenue_t) > (gross_profit_t1 / revenue_t1)


def _criterio_rotacion_activos_creciente(
    revenue_t: Optional[float],
    total_assets_t: Optional[float],
    revenue_t1: Optional[float],
    total_assets_t1: Optional[float],
) -> Optional[bool]:
    if None in (revenue_t, total_assets_t, revenue_t1, total_assets_t1):
        return None
    if total_assets_t == 0 or total_assets_t1 == 0:
        return None
    return (revenue_t / total_assets_t) > (revenue_t1 / total_assets_t1)


def calculate_piotroski_f_score(
    *,
    balance_reciente: Optional[dict],
    balance_anterior: Optional[dict],
    income_reciente: Optional[dict],
    income_anterior: Optional[dict],
    cash_flow_reciente: Optional[dict],
) -> PiotroskiResult:
    """9 criterios binarios, cada uno evaluado de forma INDEPENDIENTE
    (Decisión de diseño #4 — "criterios independientes", no reabrir). Si
    falta el dato de un criterio puntual, ese criterio se excluye (no suma
    ni resta) — se reporta explícitamente cuántos de los 9 fueron
    evaluables, nunca se asume 0 ni 1 para el faltante."""
    net_income_t = _get_num(income_reciente, "netIncome")
    net_income_t1 = _get_num(income_anterior, "netIncome")
    total_assets_t = _get_num(balance_reciente, "totalAssets")
    total_assets_t1 = _get_num(balance_anterior, "totalAssets")
    cfo_t = _get_num(cash_flow_reciente, "operatingCashFlow")
    lt_debt_t = _get_num(balance_reciente, "longTermDebt")
    lt_debt_t1 = _get_num(balance_anterior, "longTermDebt")
    current_assets_t = _get_num(balance_reciente, "totalCurrentAssets")
    current_assets_t1 = _get_num(balance_anterior, "totalCurrentAssets")
    current_liabilities_t = _get_num(balance_reciente, "totalCurrentLiabilities")
    current_liabilities_t1 = _get_num(balance_anterior, "totalCurrentLiabilities")
    shares_t = _get_num(income_reciente, "weightedAverageShsOut")
    shares_t1 = _get_num(income_anterior, "weightedAverageShsOut")
    gross_profit_t = _get_num(income_reciente, "grossProfit")
    gross_profit_t1 = _get_num(income_anterior, "grossProfit")
    revenue_t = _get_num(income_reciente, "revenue")
    revenue_t1 = _get_num(income_anterior, "revenue")

    def _valores_si_evaluable(cumplido: Optional[bool], **campos: Optional[float]) -> Optional[dict]:
        """`None` si el criterio no fue evaluable (`cumplido is None`) --
        nunca se arma un dict de magnitudes "reales" para un criterio sin
        datos. Los `campos` ya vienen ratios/montos calculados por el
        llamador (Decisión de diseño #3, "las magnitudes reales que
        determinaron `cumplido`")."""
        if cumplido is None:
            return None
        return campos

    def _ratio(numerador: Optional[float], denominador: Optional[float]) -> Optional[float]:
        if numerador is None or not denominador:
            return None
        return numerador / denominador

    roa_t = _ratio(net_income_t, total_assets_t)
    roa_t1 = _ratio(net_income_t1, total_assets_t1)
    apalancamiento_t = _ratio(lt_debt_t, total_assets_t)
    apalancamiento_t1 = _ratio(lt_debt_t1, total_assets_t1)
    liquidez_t = _ratio(current_assets_t, current_liabilities_t)
    liquidez_t1 = _ratio(current_assets_t1, current_liabilities_t1)
    margen_t = _ratio(gross_profit_t, revenue_t)
    margen_t1 = _ratio(gross_profit_t1, revenue_t1)
    rotacion_t = _ratio(revenue_t, total_assets_t)
    rotacion_t1 = _ratio(revenue_t1, total_assets_t1)

    cumplido_roa_positivo = _criterio_roa_positivo(net_income_t)
    cumplido_cfo_positivo = _criterio_cfo_positivo(cfo_t)
    cumplido_roa_creciente = _criterio_roa_creciente(
        net_income_t, total_assets_t, net_income_t1, total_assets_t1
    )
    cumplido_cfo_mayor_utilidad = _criterio_cfo_mayor_utilidad(cfo_t, net_income_t)
    cumplido_apalancamiento_decreciente = _criterio_apalancamiento_decreciente(
        lt_debt_t, total_assets_t, lt_debt_t1, total_assets_t1
    )
    cumplido_liquidez_creciente = _criterio_liquidez_creciente(
        current_assets_t, current_liabilities_t, current_assets_t1, current_liabilities_t1
    )
    cumplido_sin_dilucion = _criterio_sin_dilucion(shares_t, shares_t1)
    cumplido_margen_bruto_creciente = _criterio_margen_bruto_creciente(
        gross_profit_t, revenue_t, gross_profit_t1, revenue_t1
    )
    cumplido_rotacion_activos_creciente = _criterio_rotacion_activos_creciente(
        revenue_t, total_assets_t, revenue_t1, total_assets_t1
    )

    criterios = [
        CriterioPiotroski(
            "roa_positivo", cumplido_roa_positivo,
            valores=_valores_si_evaluable(cumplido_roa_positivo, net_income_t=net_income_t),
        ),
        CriterioPiotroski(
            "cfo_positivo", cumplido_cfo_positivo,
            valores=_valores_si_evaluable(cumplido_cfo_positivo, cfo_t=cfo_t),
        ),
        CriterioPiotroski(
            "roa_creciente", cumplido_roa_creciente,
            valores=_valores_si_evaluable(cumplido_roa_creciente, roa_t=roa_t, roa_t1=roa_t1),
        ),
        CriterioPiotroski(
            "cfo_mayor_utilidad", cumplido_cfo_mayor_utilidad,
            valores=_valores_si_evaluable(
                cumplido_cfo_mayor_utilidad, cfo_t=cfo_t, net_income_t=net_income_t
            ),
        ),
        CriterioPiotroski(
            "apalancamiento_decreciente", cumplido_apalancamiento_decreciente,
            valores=_valores_si_evaluable(
                cumplido_apalancamiento_decreciente,
                apalancamiento_t=apalancamiento_t, apalancamiento_t1=apalancamiento_t1,
            ),
        ),
        CriterioPiotroski(
            "liquidez_creciente", cumplido_liquidez_creciente,
            valores=_valores_si_evaluable(
                cumplido_liquidez_creciente, liquidez_t=liquidez_t, liquidez_t1=liquidez_t1
            ),
        ),
        CriterioPiotroski(
            "sin_dilucion", cumplido_sin_dilucion,
            valores=_valores_si_evaluable(cumplido_sin_dilucion, shares_t=shares_t, shares_t1=shares_t1),
        ),
        CriterioPiotroski(
            "margen_bruto_creciente", cumplido_margen_bruto_creciente,
            valores=_valores_si_evaluable(
                cumplido_margen_bruto_creciente, margen_t=margen_t, margen_t1=margen_t1
            ),
        ),
        CriterioPiotroski(
            "rotacion_activos_creciente", cumplido_rotacion_activos_creciente,
            valores=_valores_si_evaluable(
                cumplido_rotacion_activos_creciente, rotacion_t=rotacion_t, rotacion_t1=rotacion_t1
            ),
        ),
    ]

    evaluables = [c for c in criterios if c.cumplido is not None]
    puntaje = sum(1 for c in evaluables if c.cumplido is True)
    return PiotroskiResult(
        puntaje=puntaje,
        criterios_evaluables=len(evaluables),
        criterios_totales=9,
        criterios=criterios,
    )


# ---------------------------------------------------------------------------
# 3. Beneish M-Score — D1 (RESUELTO): SIEMPRE "no calculable" con este plan
# de FMP, sin excepción y sin proxy. No reabrir.
# ---------------------------------------------------------------------------


@dataclass
class BeneishMScoreResult:
    disponible: bool = False
    m_score: Optional[float] = None
    motivo: str = "no_calculable_con_datos_disponibles"


def calculate_beneish_m_score(
    *,
    balance_reciente: Optional[dict] = None,
    balance_anterior: Optional[dict] = None,
    income_reciente: Optional[dict] = None,
    income_anterior: Optional[dict] = None,
    cash_flow_reciente: Optional[dict] = None,
) -> BeneishMScoreResult:
    """D1 (RESUELTO por Daniela, no reabrir): FMP en el plan gratuito no
    separa PP&E bruto ni depreciación pura de amortización (`SKILL.md`
    requiere ambos) — se reporta SIEMPRE "no calculable con los datos
    disponibles", para cualquier ticker, sin excepción. Nunca se sustituye
    por un proxy documentado, aunque técnicamente se podría aproximar con
    `propertyPlantEquipmentNet`/`depreciationAndAmortization`.

    Acepta los mismos argumentos que el resto de los modelos (ignorados) solo
    para mantener una firma consistente en `advanced_command.py` — no lee
    ningún campo de ellos."""
    return BeneishMScoreResult()


# ---------------------------------------------------------------------------
# 4. Magic Formula de Greenblatt — ROIC + Earnings Yield, sin ranking (un
# solo ticker por consulta, Decisión de diseño #6). "Todo o nada".
# ---------------------------------------------------------------------------


@dataclass
class MagicFormulaResult:
    disponible: bool
    roic: Optional[float] = None
    earnings_yield: Optional[float] = None
    campos_faltantes: list[str] = field(default_factory=list)
    # SDD_explicacion_paso_a_paso.md, Decisión de diseño #3 -- los mismos
    # valores ya usados para armar `roic`/`earnings_yield` (nunca
    # recalculados aparte).
    ebit: Optional[float] = None
    capital_invertido: Optional[float] = None
    ev: Optional[float] = None
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None


def calculate_magic_formula_metrics(
    *, balance: Optional[dict], income: Optional[dict], market_cap: Optional[float]
) -> MagicFormulaResult:
    """ROIC = EBIT / (Capital de trabajo neto + Activos fijos netos).
    Earnings Yield = EBIT / EV. EV = Capitalización + Deuda total − Efectivo.

    "Todo o nada": cualquier campo de entrada faltante, o un denominador en
    cero (capital invertido o EV), -> `disponible=False`."""
    ebit = _get_num(income, "ebit")
    current_assets = _get_num(balance, "totalCurrentAssets")
    current_liabilities = _get_num(balance, "totalCurrentLiabilities")
    ppe_net = _get_num(balance, "propertyPlantEquipmentNet")
    total_debt = _get_num(balance, "totalDebt")
    cash = _get_num(balance, "cashAndCashEquivalents")
    mc = market_cap if _is_valid_number(market_cap) else None

    campos = {
        "ebit": ebit,
        "activos_corrientes": current_assets,
        "pasivos_corrientes": current_liabilities,
        "ppe_neto": ppe_net,
        "deuda_total": total_debt,
        "efectivo": cash,
        "capitalizacion_mercado": mc,
    }
    faltantes = [nombre for nombre, valor in campos.items() if valor is None]
    if faltantes:
        return MagicFormulaResult(disponible=False, campos_faltantes=faltantes)

    capital_invertido = (current_assets - current_liabilities) + ppe_net
    ev = mc + total_debt - cash
    if capital_invertido == 0 or ev == 0:
        return MagicFormulaResult(
            disponible=False, campos_faltantes=["capital_invertido_o_ev_resultan_en_cero"]
        )

    roic = ebit / capital_invertido
    earnings_yield = ebit / ev
    return MagicFormulaResult(
        disponible=True,
        roic=roic,
        earnings_yield=earnings_yield,
        ebit=ebit,
        capital_invertido=capital_invertido,
        ev=ev,
        market_cap=mc,
        total_debt=total_debt,
        cash=cash,
    )


# ---------------------------------------------------------------------------
# 5. Scoring de factores estilo AQR — 4 factores independientes.
# ---------------------------------------------------------------------------

# D3 (RESUELTO por Daniela): umbrales fijos predefinidos, documentados en una
# sola constante para poder ajustarlos después sin dispersión en el código.
# Cada entrada es `(umbral_alto, umbral_bajo)` — por encima de `umbral_alto`
# -> "alto"; por debajo de `umbral_bajo` -> "bajo"; en el medio -> "medio".
# `value_earnings_yield`/`quality_*` no tienen un número explícito de
# Daniela (solo dio el ejemplo de ROE) — se documentan acá como referencia
# estándar de value investing/quality investing, ajustable sin reabrir D3.
FACTOR_UMBRALES: dict[str, tuple[float, float]] = {
    "value_earnings_yield": (0.08, 0.04),  # >8% alto, 4-8% medio, <4% bajo
    "quality_roe": (0.15, 0.05),  # ejemplo explícito de Daniela en D3
    "quality_gross_margin": (0.40, 0.20),
    "quality_piotroski_ratio": (0.75, 0.40),  # evaluables cumplidos / evaluables
}
# Beta: caso especial (D3, ejemplo explícito de Daniela) — < 0.8 "bajo"
# (defensivo), 0.8-1.2 "medio", > 1.2 "alto" (más volátil).
LOW_VOL_BETA_UMBRAL_BAJO = 0.8
LOW_VOL_BETA_UMBRAL_ALTO = 1.2

_MOMENTUM_ETIQUETA_A_FACTOR = {
    "impulso_positivo": "alto",
    "mixto": "medio",
    "impulso_negativo": "bajo",
    "no_disponible": "no_disponible",
}


@dataclass
class FactorScoreResult:
    value: str  # "alto" | "medio" | "bajo" | "no_disponible"
    quality: str
    momentum: str
    low_vol: str


def _etiqueta_mayor_es_mejor(valor: Optional[float], umbral_alto: float, umbral_bajo: float) -> str:
    if valor is None:
        return "no_disponible"
    if valor > umbral_alto:
        return "alto"
    if valor < umbral_bajo:
        return "bajo"
    return "medio"


def _etiqueta_beta(beta: Optional[float]) -> str:
    if beta is None:
        return "no_disponible"
    if beta < LOW_VOL_BETA_UMBRAL_BAJO:
        return "bajo"
    if beta > LOW_VOL_BETA_UMBRAL_ALTO:
        return "alto"
    return "medio"


def calculate_factor_score(
    *,
    earnings_yield: Optional[float],
    roe: Optional[float],
    gross_margin: Optional[float],
    piotroski_score: Optional[int],
    piotroski_evaluables: Optional[int],
    momentum_etiqueta: str,
    beta: Optional[float],
) -> FactorScoreResult:
    """4 factores estilo AQR, cada uno evaluado de forma INDEPENDIENTE
    (Decisión de diseño #4 — "criterios independientes"). Un dato faltante
    excluye SOLO ese factor (o, dentro de Quality, solo esa sub-métrica), sin
    afectar a los demás.

    - **Value**: Earnings Yield (ya calculado por `calculate_magic_formula_
      metrics` — se reutiliza, no se recalcula acá).
    - **Quality**: combina ROE + margen bruto + ratio de Piotroski
      (evaluables cumplidos / evaluables) — cada sub-métrica disponible
      aporta ±1/0 según su propio umbral (D3); la etiqueta final es "alto" si
      la suma es positiva, "bajo" si es negativa, "medio" si es 0 o si hay
      empate; "no_disponible" solo si las 3 sub-métricas faltan.
    - **Momentum**: reutiliza la etiqueta cualitativa de
      `market_context.calculate_momentum` (D2 — proxy de costo cero, no
      "retorno de precio 6-12 meses" literal).
    - **Low-vol**: beta (D3, ejemplo explícito de Daniela).
    """
    value = _etiqueta_mayor_es_mejor(earnings_yield, *FACTOR_UMBRALES["value_earnings_yield"])

    puntos: list[int] = []
    if roe is not None:
        alto, bajo = FACTOR_UMBRALES["quality_roe"]
        puntos.append(1 if roe > alto else (-1 if roe < bajo else 0))
    if gross_margin is not None:
        alto, bajo = FACTOR_UMBRALES["quality_gross_margin"]
        puntos.append(1 if gross_margin > alto else (-1 if gross_margin < bajo else 0))
    if piotroski_score is not None and piotroski_evaluables:
        ratio = piotroski_score / piotroski_evaluables
        alto, bajo = FACTOR_UMBRALES["quality_piotroski_ratio"]
        puntos.append(1 if ratio > alto else (-1 if ratio < bajo else 0))

    if not puntos:
        quality = "no_disponible"
    else:
        suma = sum(puntos)
        quality = "alto" if suma > 0 else ("bajo" if suma < 0 else "medio")

    momentum = _MOMENTUM_ETIQUETA_A_FACTOR.get(momentum_etiqueta, "no_disponible")
    low_vol = _etiqueta_beta(beta)

    return FactorScoreResult(value=value, quality=quality, momentum=momentum, low_vol=low_vol)
