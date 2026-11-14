"""Parser puro de SEC EDGAR `companyfacts` (XBRL) a estados financieros.

SDD_fuentes_datos_gratuitas.md (secciones 3, 13, 14 y 15; precedencia 15 > 14 > 13).
Sin I/O: no importa `httpx` ni `asyncio` (CA-1). Recibe el `dict` ya parseado y
devuelve un `ParseOutcome` (resultado o codigo de "sin cobertura"); nunca lanza
excepcion por datos de entrada.

Orden de operaciones (D-16):
 1 raiz, `cik` contra `expected_cik`        8 corte por split
 2 `us-gaap` y unidad USD                   9 truncar a `quarters`
 3 forma y saneamiento de hechos            10 conceptos requeridos (`datos_insuficientes`)
 4 dedupe y cascada de tags por periodo     11 obsolescencia (`desactualizado`)
 5 duraciones y trimestres discretos        12 coherencia de `end` (`end_incoherente`)
 6 contiguidad                              13 control TTM (`ttm_incoherente`)
 7 `valor_atipico` (solo trimestrales)      14 metricas propias y series anuales
Si fallan varias, se devuelve el primer codigo en este orden.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Callable, Mapping, Optional

# --- Constantes publicas ---------------------------------------------------------------

SPLIT_RATIO_MAX = 1.2  # cociente de acciones > MAX => split (QB-2, CS-20)
SPLIT_RATIO_MIN = 0.83  # cociente de acciones < MIN => split
VENTANA_ANUAL = 5  # D-17

PARSE_CODES = frozenset(
    {
        "no_us_gaap",
        "sin_usd",
        "datos_insuficientes",
        "desactualizado",
        "cik_no_coincide",
        "end_incoherente",
        "ttm_incoherente",
        "valor_atipico",
    }
)

# --- Constantes internas ---------------------------------------------------------------

_FORMS_OK = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})
_ISO_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ACCN_RE = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_CIK_RE = re.compile(r"[0-9]{1,10}")

_OBSOLESCENCIA_DIAS = 180
_COHERENCIA_DIAS = 3
_TOL_TTM_USD = 4_000_000
_ATIPICO_MAX = 25
_MAX_USD = 1e13
_ACCIONES_MIN = 1e5
_ACCIONES_MAX = 5e11
_TOL_EPS = 0.05
_TOL_IDENTIDAD = 0.01

_TAGS_FLUJO_USD: Mapping[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "costOfRevenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"),
    "grossProfit": ("GrossProfit",),
    "operatingIncome": ("OperatingIncomeLoss",),
    "incomeBeforeTax": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "incomeTaxExpense": ("IncomeTaxExpenseBenefit",),
    "netIncome": ("NetIncomeLoss", "ProfitLoss"),
    "interestExpense": (
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    ),
    "depreciationAndAmortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    "operatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capitalExpenditure": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "commonDividendsPaid": ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"),
}
# Flujos de caja: el 10-Q solo trae acumulados (YTD) con la misma `start` (3.4.4).
_CAMPOS_YTD = frozenset(
    {"operatingCashFlow", "capitalExpenditure", "commonDividendsPaid", "depreciationAndAmortization"}
)
_TAGS_EPS: Mapping[str, tuple[str, ...]] = {
    "eps": ("EarningsPerShareBasic",),
    "epsDiluted": ("EarningsPerShareDiluted",),
}
_TAGS_ACCIONES: Mapping[str, tuple[str, ...]] = {
    "weightedAverageShsOut": ("WeightedAverageNumberOfSharesOutstandingBasic",),
    "weightedAverageShsOutDil": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
_BASE_EPS = {"eps": "weightedAverageShsOut", "epsDiluted": "weightedAverageShsOutDil"}
_TAGS_INSTANTE: Mapping[str, tuple[str, ...]] = {
    "totalAssets": ("Assets",),
    "totalCurrentAssets": ("AssetsCurrent",),
    "totalCurrentLiabilities": ("LiabilitiesCurrent",),
    "liabilities": ("Liabilities",),
    "liabilitiesAndEquity": ("LiabilitiesAndStockholdersEquity",),
    "stockholdersEquity": ("StockholdersEquity",),
    "equityInclNci": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",),
    "retainedEarnings": ("RetainedEarningsAccumulatedDeficit",),
    "cashAndCashEquivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "propertyPlantEquipmentNet": ("PropertyPlantAndEquipmentNet",),
    "debtCurrent": ("DebtCurrent",),
    "ltdCurrent": ("LongTermDebtCurrent",),
    "ltdCapCurrent": ("LongTermDebtAndCapitalLeaseObligationsCurrent",),
    "commercialPaper": ("CommercialPaper",),
    "shortBorrowings": ("ShortTermBorrowings",),
    "ltdNoncurrent": ("LongTermDebtNoncurrent",),
    "ltdCapNoncurrent": ("LongTermDebtAndCapitalLeaseObligations",),
    "ltd": ("LongTermDebt",),
}

_REQ_INCOME = ("revenue", "netIncome", "incomeBeforeTax", "incomeTaxExpense", "costOfRevenue")
_REQ_BALANCE = ("totalAssets", "totalCurrentAssets", "totalCurrentLiabilities", "totalEquity")
_REQ_CASH = ("operatingCashFlow", "capitalExpenditure")
_CAMPOS_ACCIONES = ("eps", "epsDiluted", "weightedAverageShsOut", "weightedAverageShsOutDil")
_CAMPOS_TTM_INCOME = (
    "revenue",
    "costOfRevenue",
    "netIncome",
    "incomeBeforeTax",
    "incomeTaxExpense",
    "interestExpense",
)
_CAMPOS_TTM_CASH = ("operatingCashFlow", "capitalExpenditure")


# --- Tipos publicos --------------------------------------------------------------------


@dataclass(frozen=True)
class FundamentalsPackage:
    """Paquete atomico: listas recent-first (`date` ISO, `period` "Q" o "FY")."""

    income_q: list
    income_a: list
    balance_q: list
    balance_a: list
    cash_q: list
    cash_a: list
    own_metrics: dict


@dataclass(frozen=True)
class ParseOutcome:
    """Exactamente uno de `result` y `code` es `None`; `rejected` cuenta rechazos sin valores."""

    result: Optional[FundamentalsPackage] = None
    code: Optional[str] = None
    rejected: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.result is None) == (self.code is None):
            raise ValueError("exactamente uno de result y code debe ser None")
        if self.code is not None and self.code not in PARSE_CODES:
            raise ValueError("code fuera del enum cerrado PARSE_CODES")
        object.__setattr__(self, "rejected", MappingProxyType(dict(self.rejected)))


class _Fail(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Utilidades de validacion ----------------------------------------------------------


def _fecha(valor: object) -> Optional[date]:
    if not isinstance(valor, str) or not _ISO_RE.fullmatch(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _cik(valor: object) -> Optional[int]:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if 1 <= valor <= 9_999_999_999 else None
    if isinstance(valor, str) and _CIK_RE.fullmatch(valor):
        n = int(valor)
        return n if n >= 1 else None
    return None


def _clase(dias: int) -> Optional[str]:
    if 80 <= dias <= 105:
        return "Q"
    if 170 <= dias <= 200:
        return "6M"
    if 260 <= dias <= 290:
        return "9M"
    if 350 <= dias <= 380:
        return "FY"
    return None


def _gap(a: date, b: date) -> bool:
    return 80 <= (b - a).days <= 105


def _num(valor: object) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _hecho_valido(h: object, tipo: str, instante: bool, rej: Counter):
    """Devuelve (clave, val, orden) o None. `clave` es `end` (instante) o `(start, end)`."""
    if not isinstance(h, dict):
        rej["hecho_invalido"] += 1
        return None
    form = h.get("form")
    if not isinstance(form, str) or form not in _FORMS_OK:
        rej["form_excluido"] += 1
        return None
    val = h.get("val")
    if isinstance(val, bool) or not isinstance(val, (int, float)) or (isinstance(val, float) and not math.isfinite(val)):
        rej["val_invalido"] += 1
        return None
    end = _fecha(h.get("end"))
    filed = _fecha(h.get("filed"))
    start = end if instante else _fecha(h.get("start"))
    if end is None or filed is None or start is None:
        rej["fecha_invalida"] += 1
        return None
    accn = h.get("accn")
    if not isinstance(accn, str) or not _ACCN_RE.fullmatch(accn):
        rej["accn_invalido"] += 1
        return None
    if tipo == "usd" and abs(val) >= _MAX_USD:
        rej["valor_fuera_de_rango"] += 1
        return None
    if tipo == "acciones" and not _ACCIONES_MIN <= val <= _ACCIONES_MAX:
        rej["valor_fuera_de_rango"] += 1
        return None
    return (end if instante else (start, end)), val, (filed, accn)


def _recoger(gaap: dict, tag: str, unidad: str, tipo: str, instante: bool, rej: Counter) -> dict:
    """Hechos validados de un tag, deduplicados por (start, end): gana `filed` mayor, luego `accn` mayor."""
    entrada = gaap.get(tag)
    unidades = entrada.get("units") if isinstance(entrada, dict) else None
    lista = unidades.get(unidad) if isinstance(unidades, dict) else None
    if not isinstance(lista, list):
        return {}
    mejores: dict = {}
    for h in lista:
        parsed = _hecho_valido(h, tipo, instante, rej)
        if parsed is None:
            continue
        clave, val, orden = parsed
        previo = mejores.get(clave)
        if previo is None or orden > previo[1]:
            mejores[clave] = (val, orden)
    return {k: v[0] for k, v in mejores.items()}


def _cascada(gaap: dict, tags: tuple, unidad: str, tipo: str, instante: bool, rej: Counter) -> dict:
    """Por periodo, el primer tag de la lista que lo tiene (D-2). Conflictos de valor se cuentan."""
    resultado: dict = {}
    for tag in tags:
        for clave, val in _recoger(gaap, tag, unidad, tipo, instante, rej).items():
            if clave not in resultado:
                resultado[clave] = val
            elif resultado[clave] != val:
                rej["tag_conflicto"] += 1
    return resultado


# --- Trimestres discretos --------------------------------------------------------------


def _trio(out: dict, inicio: date, fin_fy: date) -> Optional[list]:
    """Los 3 trimestres contiguos entre el inicio del FY y su cierre, o None."""
    previos = sorted(e for e in out if inicio < e < fin_fy)[-3:]
    if len(previos) != 3:
        return None
    c1, c2, c3 = previos
    if _gap(inicio, c1) and _gap(c1, c2) and _gap(c2, c3) and _gap(c3, fin_fy):
        return previos
    return None


def _clasificar(serie: dict):
    """Separa una serie {(start, end): v} por clase de duracion (3.4.2, QM-5)."""
    trimestres: dict = {}
    seis: dict = {}
    nueve: dict = {}
    anios: list = []
    for (s, e), v in sorted(serie.items()):
        c = _clase((e - s).days)
        if c == "Q":
            trimestres[s] = (e, v)
        elif c == "6M":
            seis[s] = (e, v)
        elif c == "9M":
            nueve[s] = (e, v)
        elif c == "FY":
            anios.append((s, e, v))
    # QM-5: un acumulado de ~6 meses sin hecho de 3 meses con la misma start se descarta.
    seis = {s: x for s, x in seis.items() if s in trimestres}
    return trimestres, seis, nueve, anios


def _trimestres(serie: dict, ytd: bool) -> dict:
    """{end: (valor, derivado)} de trimestres discretos de un flujo (3.4.3 y 3.4.4)."""
    q_por_inicio, seis, nueve, anios = _clasificar(serie)
    out = {e: (v, False) for e, v in q_por_inicio.values()}
    if ytd:
        for s, (e6, v6) in sorted(seis.items()):
            e3, v3 = q_por_inicio[s]
            if e6 not in out and _gap(e3, e6):
                out[e6] = (v6 - v3, True)
        for s, (e9, v9) in sorted(nueve.items()):
            if s in seis and e9 not in out and _gap(seis[s][0], e9):
                out[e9] = (v9 - seis[s][1], True)
    for s, e_fy, v_fy in sorted(anios, key=lambda t: t[1]):
        if e_fy in out:
            continue
        if s in nueve and _gap(nueve[s][0], e_fy):
            out[e_fy] = (v_fy - nueve[s][1], True)
            continue
        trio = _trio(out, s, e_fy)
        if trio is not None:
            out[e_fy] = (v_fy - sum(out[c][0] for c in trio), True)
    return out


def _misma_base(base_q: dict, base_fy: dict, trio: list, inicio: date, fin_fy: date) -> bool:
    """Acciones de FY y Q1-Q3 en la misma base: `MIN <= cociente <= MAX` (QB-2)."""
    fy = base_fy.get((inicio, fin_fy))
    if fy is None:
        return False
    for c in trio:
        if c not in base_q:
            return False
        if not SPLIT_RATIO_MIN <= base_q[c][0] / fy <= SPLIT_RATIO_MAX:
            return False
    return True


def _trimestres_especial(serie: dict, tipo: str, base_q: Optional[dict] = None, base_fy: Optional[dict] = None):
    """Acciones y EPS: nunca resta generica (3.4.7). Devuelve ({end: (v, derivado)}, {(s,e): v FY})."""
    q_por_inicio, _seis, _nueve, anios = _clasificar(serie)
    out = {e: (v, False) for e, v in q_por_inicio.values()}
    for s, e_fy, v_fy in sorted(anios, key=lambda t: t[1]):
        if e_fy in out:
            continue
        trio = _trio(out, s, e_fy)
        if trio is None:
            continue
        valores = [out[c][0] for c in trio]
        if tipo == "acciones":
            q4 = 4 * v_fy - sum(valores)
            # +-10% de Q3, inclusive; si no, el valor de FY.
            out[e_fy] = (q4 if abs(q4 - valores[2]) * 10 <= valores[2] else v_fy, True)
        elif _misma_base(base_q or {}, base_fy or {}, trio, s, e_fy):
            out[e_fy] = (round(v_fy - sum(valores), 6), True)
    return out, {(s, e): v for s, e, v in anios}


def _corrida(fechas) -> list:
    """Corrida mas reciente de fechas consecutivas a 80-105 dias (recent-first)."""
    orden = sorted(fechas, reverse=True)
    corrida = orden[:1]
    for e in orden[1:]:
        if not _gap(e, corrida[-1]):
            break
        corrida.append(e)
    return corrida


# --- Registros -------------------------------------------------------------------------


def _armar_income(end: date, periodo: str, g: Callable[[str], object], rej: Counter) -> dict:
    """Registro de income statement; aplica los saneamientos de signo y de EPS (3.7)."""
    ingresos = g("revenue")
    costo = g("costOfRevenue")
    bruta = g("grossProfit")
    if costo is None and ingresos is not None and bruta is not None:
        costo = ingresos - bruta
    if ingresos is not None and ingresos < 0:
        rej["valor_negativo"] += 1
        ingresos = None
    if costo is not None and costo < 0:
        rej["valor_negativo"] += 1
        costo = None
    operativa = g("operatingIncome")
    dya = g("depreciationAndAmortization")
    neta = g("netIncome")
    income = {
        "date": end.isoformat(),
        "period": periodo,
        "revenue": ingresos,
        "costOfRevenue": costo,
        "grossProfit": bruta,
        "operatingIncome": operativa,
        "ebit": operativa,
        "ebitda": operativa + dya if operativa is not None and dya is not None else None,
        "interestExpense": g("interestExpense"),
        "incomeBeforeTax": g("incomeBeforeTax"),
        "incomeTaxExpense": g("incomeTaxExpense"),
        "netIncome": neta,
    }
    for campo in _CAMPOS_ACCIONES:
        income[campo] = g(campo)
    # Coherencia de EPS (3.7): no se "corrige", se emite None.
    for campo, base in _BASE_EPS.items():
        eps, acciones = income[campo], income[base]
        if eps and neta is not None and acciones and abs(eps - neta / acciones) / abs(eps) > _TOL_EPS:
            rej["eps_incoherente"] += 1
            income[campo] = None
    return income


def _armar_cash(end: date, periodo: str, g: Callable[[str], object]) -> dict:
    ocf = g("operatingCashFlow")
    capex = g("capitalExpenditure")
    return {
        "date": end.isoformat(),
        "period": periodo,
        "operatingCashFlow": ocf,
        "capitalExpenditure": capex,
        "freeCashFlow": ocf - abs(capex) if ocf is not None and capex is not None else None,
        "depreciationAndAmortization": g("depreciationAndAmortization"),
        "commonDividendsPaid": g("commonDividendsPaid"),
    }


def _primero(g, *campos):
    for campo in campos:
        valor = g(campo)
        if valor is not None:
            return valor
    return None


def _armar_balance(end: date, periodo: str, g: Callable[[str], object], rej: Counter):
    """Registro de balance o None si rompe la identidad contable (3.7)."""
    activos = g("totalAssets")
    patrimonio = g("stockholdersEquity")
    total_patrimonio = _primero(g, "equityInclNci", "stockholdersEquity")
    pasivos = g("liabilities")
    if activos is not None and pasivos is not None and total_patrimonio is not None:
        if abs(activos - (pasivos + total_patrimonio)) / activos > _TOL_IDENTIDAD:
            rej["identidad_contable"] += 1
            return None
    total_pasivos = pasivos
    if total_pasivos is None:
        plp = g("liabilitiesAndEquity")
        if plp is not None and total_patrimonio is not None:
            total_pasivos = plp - total_patrimonio
    corto, largo = _deuda_corta(g), _deuda_larga(g)
    total_deuda = None
    if corto is not None and largo is not None:
        total_deuda = corto + largo
        if (total_pasivos is not None and total_deuda > total_pasivos) or (
            activos is not None and total_deuda > activos
        ):
            rej["deuda_incoherente"] += 1
            corto = largo = total_deuda = None
    return {
        "date": end.isoformat(),
        "period": periodo,
        "totalAssets": activos,
        "totalCurrentAssets": g("totalCurrentAssets"),
        "totalCurrentLiabilities": g("totalCurrentLiabilities"),
        "totalLiabilities": total_pasivos,
        "totalStockholdersEquity": patrimonio,
        "totalEquity": total_patrimonio,
        "retainedEarnings": g("retainedEarnings"),
        "cashAndCashEquivalents": g("cashAndCashEquivalents"),
        "propertyPlantEquipmentNet": g("propertyPlantEquipmentNet"),
        "shortTermDebt": corto,
        "longTermDebt": largo,
        "totalDebt": total_deuda,
    }


def _deuda_corta(g) -> Optional[float]:
    """`DebtCurrent`; si no, vencimiento corriente + papel comercial (al menos un componente presente)."""
    directa = g("debtCurrent")
    if directa is not None:
        return directa
    vencimiento = _primero(g, "ltdCurrent", "ltdCapCurrent")
    corto_plazo = _primero(g, "commercialPaper", "shortBorrowings")
    if vencimiento is None and corto_plazo is None:
        return None
    return (vencimiento or 0) + (corto_plazo or 0)


def _deuda_larga(g) -> Optional[float]:
    """Porcion no corriente. `LongTermDebt` solo bajo la heuristica H-deuda (regla, no verdad contable)."""
    no_corriente = _primero(g, "ltdNoncurrent", "ltdCapNoncurrent")
    if no_corriente is not None:
        return no_corriente
    total = g("ltd")
    if total is None:
        return None
    if g("ltdCurrent") is not None:
        return total - g("ltdCurrent")
    if g("debtCurrent") is not None:
        return total
    return None


# --- Saneamientos de series ------------------------------------------------------------


def _truncar_atipico(recs_asc: list, campo: str, rej: Counter) -> list:
    """QB-7: B/A > 25 o < 0,04 (estricto) descarta A y todo lo anterior. Solo trimestrales."""
    for i in range(len(recs_asc) - 1, 0, -1):
        a, b = recs_asc[i - 1][campo], recs_asc[i][campo]
        if a is not None and b is not None and a > 0 and b > 0 and (b > _ATIPICO_MAX * a or b * _ATIPICO_MAX < a):
            rej["valor_atipico"] += 1
            return recs_asc[i:]
    return recs_asc


def _anular_acciones(rec: dict) -> None:
    for campo in _CAMPOS_ACCIONES:
        rec[campo] = None


def _cortar_splits(recs_asc: list) -> None:
    """QB-2: cociente sobre acciones diluidas (o basicas de ambos); par no evaluable anula al mas antiguo."""
    for i in range(1, len(recs_asc)):
        a, b = recs_asc[i - 1], recs_asc[i]
        base = "weightedAverageShsOutDil"
        if a[base] is None or b[base] is None:
            base = "weightedAverageShsOut"
        if a[base] is None or b[base] is None:
            _anular_acciones(a)
            continue
        ratio = b[base] / a[base]
        if ratio > SPLIT_RATIO_MAX or ratio < SPLIT_RATIO_MIN:
            for previo in recs_asc[:i]:
                _anular_acciones(previo)


def _fechas(recs: list) -> list:
    return [date.fromisoformat(r["date"]) for r in recs[:1]]


def _coherentes(*listas: list) -> bool:
    ultimas = [d for lista in listas for d in _fechas(lista)]
    return not ultimas or (max(ultimas) - min(ultimas)).days <= _COHERENCIA_DIAS


def _suma4(recs: list, campo: str) -> Optional[float]:
    if len(recs) < 4:
        return None
    valores = [r[campo] for r in recs[:4]]
    return None if any(v is None for v in valores) else sum(valores)


def _fy_cercano(por_fin: dict, ultimo: date) -> Optional[dict]:
    candidatos = [e for e in por_fin if abs((ultimo - e).days) <= _COHERENCIA_DIAS]
    return por_fin[min(candidatos, key=lambda e: abs((ultimo - e).days))] if candidatos else None


def _control_ttm(income_q: list, cash_q: list, fy_income: dict, fy_cash: dict) -> bool:
    """QB-6: solo si el ultimo trimestre cierra FY; |suma de 4 - FY| <= 4.000.000 USD absolutos."""
    ultimo = date.fromisoformat(income_q[0]["date"])
    fines = set(fy_income) | set(fy_cash)
    if not any(abs((ultimo - e).days) <= _COHERENCIA_DIAS for e in fines):
        return True
    for trimestrales, anuales, campos in (
        (income_q, fy_income, _CAMPOS_TTM_INCOME),
        (cash_q, fy_cash, _CAMPOS_TTM_CASH),
    ):
        anual = _fy_cercano(anuales, ultimo)
        for campo in campos:
            suma = _suma4(trimestrales, campo)
            if anual is None or suma is None or anual[campo] is None:
                continue
            if abs(suma - anual[campo]) > _TOL_TTM_USD:
                return False
    return True


def _metricas_propias(income_q: list, balance_q: list, cash_q: list) -> dict:
    """3.5: lo que XBRL no trae. `dividendYield` necesita marketCap del llamador (ver `dividend_yield`)."""
    ni = _suma4(income_q, "netIncome")
    ebitda = _suma4(income_q, "ebitda")
    dividendos = _suma4(cash_q, "commonDividendsPaid")
    dividendos = abs(dividendos) if dividendos is not None else None
    balance = balance_q[0]
    patrimonio, deuda, caja = balance["totalEquity"], balance["totalDebt"], balance["cashAndCashEquivalents"]
    positivo = patrimonio is not None and patrimonio > 0
    return {
        "roe": ni / patrimonio if ni is not None and positivo else None,
        "debtToEquity": deuda / patrimonio if deuda is not None and positivo else None,
        "netDebtToEBITDA": (
            (deuda - caja) / ebitda
            if deuda is not None and caja is not None and ebitda is not None and ebitda > 0
            else None
        ),
        "dividendYield": None,
        "payoutRatio": dividendos / ni if dividendos is not None and ni is not None and ni > 0 else None,
        "dividendsPaidTTM": dividendos,
    }


def dividend_yield(dividends_paid_ttm: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    """Aproximacion declarada (3.5): dividendos pagados TTM / marketCap; `None` si falta algo (no 0)."""
    if dividends_paid_ttm is None or market_cap is None or not _num(market_cap) or market_cap <= 0:
        return None
    return dividends_paid_ttm / market_cap


# --- Punto de entrada ------------------------------------------------------------------


def parse_company_facts(raw: object, *, quarters: int, today: date, expected_cik: object) -> ParseOutcome:
    """Convierte `companyfacts` en un paquete atomico o en un codigo de sin cobertura. Nunca lanza por datos."""
    rej: Counter = Counter()
    try:
        paquete = _parse(raw, quarters, today, expected_cik, rej)
    except _Fail as falla:
        return ParseOutcome(code=falla.code, rejected=dict(rej))
    except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError, IndexError):
        return ParseOutcome(code="datos_insuficientes", rejected=dict(rej))
    return ParseOutcome(result=paquete, rejected=dict(rej))


def _parse(raw: object, quarters: int, today: date, expected_cik: object, rej: Counter) -> FundamentalsPackage:
    # 1. raiz y CIK (CS-5, CS-15)
    if not isinstance(raw, dict):
        raise _Fail("datos_insuficientes")
    pedido = _cik(expected_cik)
    if pedido is None or _cik(raw.get("cik")) != pedido:
        raise _Fail("cik_no_coincide")
    facts = raw.get("facts")
    if not isinstance(facts, dict):
        raise _Fail("datos_insuficientes")
    # 2. us-gaap y USD
    if "us-gaap" not in facts:
        raise _Fail("no_us_gaap")
    gaap = facts["us-gaap"]
    if not isinstance(gaap, dict):
        raise _Fail("datos_insuficientes")
    _exigir_usd(gaap)
    if isinstance(today, datetime):
        today = today.date()

    # 3-4. hechos validados, dedupe y cascada por periodo
    flujos = {c: _cascada(gaap, t, "USD", "usd", False, rej) for c, t in _TAGS_FLUJO_USD.items()}
    for clave, valor in list(flujos["capitalExpenditure"].items()):
        if valor > 0:
            rej["capex_normalizado"] += 1
        flujos["capitalExpenditure"][clave] = -abs(valor)
    for clave, valor in list(flujos["interestExpense"].items()):
        if valor < 0:
            rej["interes_normalizado"] += 1
        flujos["interestExpense"][clave] = abs(valor)
    flujos["commonDividendsPaid"] = {k: -abs(v) for k, v in flujos["commonDividendsPaid"].items()}
    eps = {c: _cascada(gaap, t, "USD/shares", "eps", False, rej) for c, t in _TAGS_EPS.items()}
    acciones = {c: _cascada(gaap, t, "shares", "acciones", False, rej) for c, t in _TAGS_ACCIONES.items()}
    instantes = {c: _cascada(gaap, t, "USD", "usd", True, rej) for c, t in _TAGS_INSTANTE.items()}
    instantes["totalAssets"] = {e: v for e, v in instantes["totalAssets"].items() if v > 0}

    # 5. trimestres discretos
    trim = {c: _trimestres(s, c in _CAMPOS_YTD) for c, s in flujos.items()}
    fy_acciones = {}
    for campo, serie in acciones.items():
        trim[campo], fy_acciones[campo] = _trimestres_especial(serie, "acciones")
    for campo, serie in eps.items():
        base = _BASE_EPS[campo]
        trim[campo], _ = _trimestres_especial(serie, "eps", trim[base], fy_acciones[base])
    anual = {c: {} for c in (*flujos, *eps, *acciones)}
    for campo, serie in {**flujos, **eps, **acciones}.items():
        for (s, e), v in sorted(serie.items()):
            if _clase((e - s).days) == "FY":
                anual[campo][e] = v

    def g_trim(end: date) -> Callable[[str], object]:
        return lambda campo: trim[campo].get(end, (None,))[0]

    def g_anual(end: date) -> Callable[[str], object]:
        return lambda campo: anual[campo].get(end)

    def g_inst(end: date) -> Callable[[str], object]:
        return lambda campo: instantes[campo].get(end)

    # Registros por fin de periodo (los que rompen una regla de 3.7 se descartan o se anulan).
    inc_all, cash_all = {}, {}
    for end in sorted(set(trim["revenue"]) & set(trim["netIncome"])):
        inc_all[end] = _armar_income(end, "Q", g_trim(end), rej)
    for end in sorted(trim["operatingCashFlow"]):
        cash_all[end] = _armar_cash(end, "Q", g_trim(end))
    bal_all = {}
    for end in sorted(instantes["totalAssets"]):
        rec = _armar_balance(end, "Q", g_inst(end), rej)
        if rec is not None:
            bal_all[end] = rec
    fy_inc, fy_cash, fy_bal = {}, {}, {}
    for end in sorted(set(anual["revenue"]) & set(anual["netIncome"])):
        fy_inc[end] = _armar_income(end, "FY", g_anual(end), rej)
    for end in sorted(anual["operatingCashFlow"]):
        fy_cash[end] = _armar_cash(end, "FY", g_anual(end))
    for end in sorted(set(fy_inc) | set(fy_cash)):
        if end in instantes["totalAssets"]:
            rec = _armar_balance(end, "FY", g_inst(end), rej)
            if rec is not None:
                fy_bal[end] = rec

    # 6-8. contiguidad, valor atipico y split sobre la serie completa (QB-10)
    income_q = _serie_trimestral(inc_all)
    balance_q = _serie_trimestral(bal_all)
    cash_q = _serie_trimestral(cash_all)
    income_q = _truncar_atipico(income_q, "revenue", rej)
    balance_q = _truncar_atipico(balance_q, "totalAssets", rej)
    _cortar_splits(income_q)
    fy_inc_asc = [fy_inc[e] for e in sorted(fy_inc)]
    _cortar_splits(fy_inc_asc)

    # 9. truncar (recent-first)
    income_q = income_q[::-1][:quarters]
    balance_q = balance_q[::-1][:quarters]
    cash_q = cash_q[::-1][:quarters]
    income_a = fy_inc_asc[::-1][:VENTANA_ANUAL]
    cash_a = [fy_cash[e] for e in sorted(fy_cash, reverse=True)][:VENTANA_ANUAL]
    balance_a = [fy_bal[e] for e in sorted(fy_bal, reverse=True)][:VENTANA_ANUAL]

    # 10. conceptos requeridos del periodo mas reciente
    if len(income_q) < 4 or len(cash_q) < 4 or not balance_q:
        raise _Fail("datos_insuficientes")
    ultimo_inc, ultimo_bal, ultimo_cash = income_q[0], balance_q[0], cash_q[0]
    if (
        any(ultimo_inc[c] is None for c in _REQ_INCOME)
        or all(ultimo_inc[c] is None for c in ("weightedAverageShsOutDil", "weightedAverageShsOut"))
        or any(ultimo_bal[c] is None for c in _REQ_BALANCE)
        or any(ultimo_cash[c] is None for c in _REQ_CASH)
    ):
        raise _Fail("datos_insuficientes")

    # 11. obsolescencia
    ultimo_fin = max(date.fromisoformat(ultimo_inc["date"]), date.fromisoformat(ultimo_cash["date"]))
    if (today - ultimo_fin).days > _OBSOLESCENCIA_DIAS:
        raise _Fail("desactualizado")

    # 12. coherencia de `end` entre estados (trimestral y anual)
    if not _coherentes(income_q, balance_q, cash_q) or not _coherentes(income_a, balance_a, cash_a):
        raise _Fail("end_incoherente")

    # 13. control TTM
    if not _control_ttm(income_q, cash_q, fy_inc, fy_cash):
        raise _Fail("ttm_incoherente")

    # 14. metricas propias
    propias = _metricas_propias(income_q, balance_q, cash_q)
    return FundamentalsPackage(
        income_q=income_q,
        income_a=income_a,
        balance_q=balance_q,
        balance_a=balance_a,
        cash_q=cash_q,
        cash_a=cash_a,
        own_metrics=propias,
    )


def _serie_trimestral(todos: dict) -> list:
    """Registros (ascendentes) de la corrida contigua mas reciente."""
    corrida = set(_corrida(todos))
    return [todos[e] for e in sorted(corrida)]


def _exigir_usd(gaap: dict) -> None:
    """`sin_usd` si los conceptos monetarios solo existen en otra unidad; nunca se convierte moneda."""
    hay_usd = hay_otra = False
    monetarios = [t for tags in (*_TAGS_FLUJO_USD.values(), *_TAGS_INSTANTE.values()) for t in tags]
    for tag in monetarios:
        entrada = gaap.get(tag)
        unidades = entrada.get("units") if isinstance(entrada, dict) else None
        if not isinstance(unidades, dict):
            continue
        for unidad, lista in unidades.items():
            if isinstance(lista, list) and lista:
                if unidad == "USD":
                    hay_usd = True
                else:
                    hay_otra = True
    if not hay_usd:
        raise _Fail("sin_usd" if hay_otra else "datos_insuficientes")
