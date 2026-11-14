"""Tests del parser puro `investbot.edgar_xbrl` (SDD_fuentes_datos_gratuitas.md, F1a-1).

Solo datos sinteticos construidos con `tests/edgar_helpers.py` (nada de red, ni de
archivos reales, ni de tiempo real: `today` siempre es inyectado). Nombres
`test_fdg_ca<n>[b]_*` y `test_fdg_tcs<n>[b]_*` (QB-11).

Convencion de IDs en este archivo:
  - `ca1_*`   contrato del parser (CA-1/QB-9) y reglas de extraccion de 3.3/3.4 con
              datos sinteticos (dedupe, Q4, YTD, acciones, EPS, unidades, requeridos).
              No son los criterios CA-2..7 (esos usan fixtures reales: F1a-2).
  - `ca8`, `ca9`, `ca10b`, `ca11`, `ca34_a..d` y `tcs9/15/20/21` como en 15.7.
"""

from __future__ import annotations

import math
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from investbot import edgar_xbrl as X
from investbot.edgar_xbrl import (
    PARSE_CODES,
    SPLIT_RATIO_MAX,
    SPLIT_RATIO_MIN,
    VENTANA_ANUAL,
    FundamentalsPackage,
    ParseOutcome,
    parse_company_facts,
)

from .edgar_helpers import companyfacts, fact

REPO = Path(__file__).resolve().parents[1]
M = 1_000_000
CIK = 1234567
HOY = date(2026, 3, 1)

# --- Generador de una empresa sintetica -------------------------------------------------


def flujo(tag, y, qs, unit="USD", *, ytd=False, fy=None):
    """Hechos de un flujo del anio `y`: Q1-Q3 (si no es YTD), acumulados 6M/9M y FY (10-K)."""
    fines = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30"]
    inicios = [f"{y}-01-01", f"{y}-04-01", f"{y}-07-01"]
    presentado = [f"{y}-05-01", f"{y}-08-01", f"{y}-11-01"]
    hechos = [fact(tag, unit, inicios[0], fines[0], qs[0], presentado[0])]
    if not ytd:
        hechos.append(fact(tag, unit, inicios[1], fines[1], qs[1], presentado[1]))
        hechos.append(fact(tag, unit, inicios[2], fines[2], qs[2], presentado[2]))
    hechos.append(fact(tag, unit, inicios[0], fines[1], qs[0] + qs[1], presentado[1]))
    hechos.append(fact(tag, unit, inicios[0], fines[2], qs[0] + qs[1] + qs[2], presentado[2]))
    total = sum(qs) if fy is None else fy
    hechos.append(fact(tag, unit, inicios[0], f"{y}-12-31", total, f"{y + 1}-02-15", "10-K"))
    return hechos


def instante(tag, y, vals, unit="USD"):
    fines = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"]
    presentado = [f"{y}-05-01", f"{y}-08-01", f"{y}-11-01", f"{y + 1}-02-15"]
    formas = ["10-Q", "10-Q", "10-Q", "10-K"]
    return [fact(tag, unit, None, e, v, f, form) for e, v, f, form in zip(fines, vals, presentado, formas)]


def _valores(y, acciones, bal):
    n0 = (y - 2020) * 4
    idx = [n0 + j for j in range(4)]
    mul, div = bal
    dil, bas = acciones.get(y, (1_000_000_000, 990_000_000))
    ni = [(150 + 2 * n) * M for n in idx]
    activos = [(5000 + 10 * n) * M * mul // div for n in idx]
    pasivos = [a * 2 // 5 for a in activos]
    return {
        "rev": [(1000 + 10 * n) * M for n in idx],
        "cost": [(600 + 6 * n) * M for n in idx],
        "gp": [(400 + 4 * n) * M for n in idx],
        "op": [(200 + 2 * n) * M for n in idx],
        "ni": ni,
        "ebt": [x + 30 * M for x in ni],
        "tax": [30 * M] * 4,
        "int": [5 * M] * 4,
        "dya": [20 * M] * 4,
        "ocf": [(300 + 3 * n) * M for n in idx],
        "capex": [50 * M] * 4,
        "div": [10 * M] * 4,
        "dil": dil,
        "bas": bas,
        "eps_dil": [round(x / dil, 4) for x in ni],
        "eps_bas": [round(x / bas, 4) for x in ni],
        "eps_dil_fy": round(sum(ni) / dil, 4),
        "eps_bas_fy": round(sum(ni) / bas, 4),
        "assets": activos,
        "liab": pasivos,
        "equity": [a - p for a, p in zip(activos, pasivos)],
        "ca": [(2000 + n) * M * mul // div for n in idx],
        "cl": [(1000 + n) * M * mul // div for n in idx],
    }


EBT_TAG = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"


def empresa(anios=(2023, 2024, 2025), *, acciones=None, bal=(1, 1), omitir=()):
    """Empresa de ano calendario con todos los conceptos requeridos y opcionales."""
    acciones = acciones or {}
    hechos = []
    for y in anios:
        v = _valores(y, acciones, bal)
        hechos += flujo("Revenues", y, v["rev"])
        hechos += flujo("CostOfRevenue", y, v["cost"])
        hechos += flujo("GrossProfit", y, v["gp"])
        hechos += flujo("OperatingIncomeLoss", y, v["op"])
        hechos += flujo(EBT_TAG, y, v["ebt"])
        hechos += flujo("IncomeTaxExpenseBenefit", y, v["tax"])
        hechos += flujo("NetIncomeLoss", y, v["ni"])
        hechos += flujo("InterestExpense", y, v["int"])
        hechos += flujo("DepreciationDepletionAndAmortization", y, v["dya"], ytd=True)
        hechos += flujo("NetCashProvidedByUsedInOperatingActivities", y, v["ocf"], ytd=True)
        hechos += flujo("PaymentsToAcquirePropertyPlantAndEquipment", y, v["capex"], ytd=True)
        hechos += flujo("PaymentsOfDividendsCommonStock", y, v["div"], ytd=True)
        hechos += flujo("EarningsPerShareDiluted", y, v["eps_dil"], "USD/shares", fy=v["eps_dil_fy"])
        hechos += flujo("EarningsPerShareBasic", y, v["eps_bas"], "USD/shares", fy=v["eps_bas_fy"])
        hechos += flujo("WeightedAverageNumberOfDilutedSharesOutstanding", y, [v["dil"]] * 4, "shares", fy=v["dil"])
        hechos += flujo("WeightedAverageNumberOfSharesOutstandingBasic", y, [v["bas"]] * 4, "shares", fy=v["bas"])
        hechos += instante("Assets", y, v["assets"])
        hechos += instante("AssetsCurrent", y, v["ca"])
        hechos += instante("LiabilitiesCurrent", y, v["cl"])
        hechos += instante("Liabilities", y, v["liab"])
        hechos += instante("StockholdersEquity", y, v["equity"])
        hechos += instante("RetainedEarningsAccumulatedDeficit", y, [800 * M] * 4)
        hechos += instante("CashAndCashEquivalentsAtCarryingValue", y, [500 * M] * 4)
        hechos += instante("PropertyPlantAndEquipmentNet", y, [1500 * M] * 4)
        hechos += instante("DebtCurrent", y, [50 * M] * 4)
        hechos += instante("LongTermDebtNoncurrent", y, [500 * M] * 4)
    return [h for h in hechos if h[1] not in omitir]


def sin_tag(hechos, *tags):
    return [h for h in hechos if h[1] not in tags]


def modificar(hechos, tag, *, hasta=None, desde=None, **cambios):
    """Copia con `cambios` aplicados a los hechos del `tag` (y de fin `hasta` / inicio `desde` si se indican)."""
    salida = []
    for ns, t, unit, h in hechos:
        coincide = t == tag and (hasta is None or h["end"] == hasta) and (desde is None or h.get("start") == desde)
        salida.append((ns, t, unit, dict(h, **cambios)) if coincide else (ns, t, unit, h))
    return salida


def parsear(hechos, *, quarters=12, today=HOY, cik=CIK, expected=CIK):
    return parse_company_facts(companyfacts(*hechos, cik=cik), quarters=quarters, today=today, expected_cik=expected)


def ok(hechos, **kw) -> FundamentalsPackage:
    salida = parsear(hechos, **kw)
    assert salida.code is None, salida.code
    return salida.result


def por_fecha(lista):
    return {r["date"]: r for r in lista}


# --- CA-1: contrato del parser -----------------------------------------------------------


def test_fdg_ca1_modulo_puro_sin_httpx_ni_asyncio_en_subproceso():
    codigo = (
        "import sys; sys.path.insert(0, 'src'); import investbot.edgar_xbrl; "
        "assert not {'httpx', 'asyncio'} & set(sys.modules)"
    )
    proc = subprocess.run([sys.executable, "-I", "-c", codigo], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_fdg_ca1_paquete_completo_con_seis_listas_recent_first():
    pkg = ok(empresa())
    fechas = [r["date"] for r in pkg.income_q]
    assert len(pkg.income_q) == len(pkg.balance_q) == len(pkg.cash_q) == 12
    assert fechas == sorted(fechas, reverse=True) and fechas[0] == "2025-12-31" and fechas[-1] == "2023-03-31"
    assert {r["period"] for r in pkg.income_q + pkg.balance_q + pkg.cash_q} == {"Q"}
    assert [r["date"] for r in pkg.income_a] == ["2025-12-31", "2024-12-31", "2023-12-31"]
    assert {r["period"] for r in pkg.income_a + pkg.balance_a + pkg.cash_a} == {"FY"}
    assert len(pkg.balance_a) == len(pkg.cash_a) == 3
    assert set(pkg.own_metrics) >= {"roe", "debtToEquity", "netDebtToEBITDA", "dividendYield", "payoutRatio"}


def test_fdg_ca1_quarters_trunca_y_la_ventana_anual_es_5():
    pkg = ok(empresa(anios=range(2019, 2026)), quarters=6)
    assert len(pkg.income_q) == len(pkg.balance_q) == len(pkg.cash_q) == 6
    assert VENTANA_ANUAL == 5
    assert len(pkg.income_a) == len(pkg.balance_a) == len(pkg.cash_a) == 5
    assert pkg.income_a[0]["date"] == "2025-12-31" and pkg.income_a[-1]["date"] == "2021-12-31"


def test_fdg_ca1_outcome_exactamente_uno_y_codigo_cerrado():
    pkg = ok(empresa(anios=(2025,)))
    assert isinstance(ParseOutcome(result=pkg), ParseOutcome)
    with pytest.raises(ValueError):
        ParseOutcome()
    with pytest.raises(ValueError):
        ParseOutcome(result=pkg, code="sin_usd")
    with pytest.raises(ValueError):
        ParseOutcome(code="codigo_libre")
    salida = ParseOutcome(code="sin_usd", rejected={"x": 1})
    assert salida.result is None and salida.rejected["x"] == 1
    with pytest.raises(TypeError):
        salida.rejected["y"] = 2  # solo lectura
    assert {"no_us_gaap", "sin_usd", "datos_insuficientes", "desactualizado", "cik_no_coincide"} <= PARSE_CODES
    assert {"end_incoherente", "ttm_incoherente", "valor_atipico"} <= PARSE_CODES


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "texto",
        7,
        {},
        {"cik": CIK},
        {"cik": CIK, "facts": []},
        {"cik": CIK, "facts": {"us-gaap": []}},
        {"cik": CIK, "facts": {"us-gaap": {"Revenues": 5, "Assets": {"units": 3}, "Liabilities": {"units": {"USD": 4}}}}},
        {"cik": CIK, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [None, 5, "x", {}]}}}}},
    ],
)
def test_fdg_ca1_nunca_lanza_por_datos_hostiles(raw):
    salida = parse_company_facts(raw, quarters=12, today=HOY, expected_cik=CIK)
    assert salida.result is None and salida.code in PARSE_CODES


def test_fdg_ca1_red_de_seguridad_convierte_un_error_inesperado_en_datos_insuficientes(monkeypatch):
    def explota(*args, **kwargs):
        raise TypeError("dato inesperado")

    monkeypatch.setattr(X, "_parse", explota)
    salida = parsear(empresa(anios=(2025,)))
    assert (salida.result, salida.code) == (None, "datos_insuficientes")


def test_fdg_ca1_q4_es_fy_menos_9m_y_se_usa_directo_el_trimestre_de_3_meses():
    pkg = ok(empresa())
    recs = por_fecha(pkg.income_q)
    n = (2025 - 2020) * 4  # Q1 2025 = n, Q4 2025 = n + 3
    assert recs["2025-03-31"]["revenue"] == (1000 + 10 * n) * M  # hecho directo
    assert recs["2025-12-31"]["revenue"] == (1000 + 10 * (n + 3)) * M  # FY - 9M
    assert recs["2025-12-31"]["netIncome"] == (150 + 2 * (n + 3)) * M
    # La suma de los 4 trimestres de un FY reproduce el FY anual
    assert sum(recs[d]["revenue"] for d in ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")) == (
        pkg.income_a[0]["revenue"]
    )


def test_fdg_ca1_q4_sin_9m_usa_fy_menos_trio_de_trimestres():
    hechos = [h for h in empresa() if not (h[1] == "Revenues" and h[3]["end"].endswith("-09-30") and h[3]["start"].endswith("01-01"))]
    pkg = ok(hechos)
    n = (2025 - 2020) * 4
    assert por_fecha(pkg.income_q)["2025-12-31"]["revenue"] == (1000 + 10 * (n + 3)) * M


def test_fdg_ca1_q4_no_se_deriva_sin_9m_si_el_trio_de_trimestres_no_es_contiguo():
    d = date.fromisoformat
    fy = {(d("2025-01-01"), d("2025-12-31")): 1000}
    q1, q2, q3 = ((d("2025-01-01"), d("2025-03-31")), (d("2025-04-01"), d("2025-06-30")), (d("2025-07-01"), d("2025-09-30")))
    completo = {**fy, q1: 100, q2: 200, q3: 300}
    assert X._trimestres(completo, False)[d("2025-12-31")] == (400, True)
    sin_q3 = {**fy, q1: 100, q2: 200}
    assert d("2025-12-31") not in X._trimestres(sin_q3, False)
    hueco = {**fy, q1: 100, q2: 200, (d("2025-08-01"), d("2025-10-31")): 300}  # Q3 corrido: no encadena con el cierre
    assert d("2025-12-31") not in X._trimestres(hueco, False)


def test_fdg_ca1_q4_reportado_directo_gana_sobre_el_derivado():
    base = empresa()
    q4 = fact("Revenues", "USD", "2025-10-01", "2025-12-31", 777 * M, "2026-02-15", "10-K")
    # FY ajustado para que la suma de los 4 trimestres cuadre con el FY y no dispare ttm_incoherente
    n = (2025 - 2020) * 4
    fy_nuevo = sum((1000 + 10 * (n + j)) * M for j in range(3)) + 777 * M
    hechos = modificar(base, "Revenues", desde="2025-01-01", hasta="2025-12-31", val=fy_nuevo) + [q4]
    assert por_fecha(ok(hechos).income_q)["2025-12-31"]["revenue"] == 777 * M


def test_fdg_ca1_flujo_de_caja_ytd_se_diferencia_en_trimestres_discretos_y_capex_sale_negativo():
    pkg = ok(empresa())
    n = (2025 - 2020) * 4
    cash = por_fecha(pkg.cash_q)
    esperado = [(300 + 3 * (n + j)) * M for j in range(4)]
    fechas = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    assert [cash[d]["operatingCashFlow"] for d in fechas] == esperado
    assert all(cash[d]["capitalExpenditure"] == -50 * M for d in fechas)
    assert cash["2025-06-30"]["freeCashFlow"] == esperado[1] - 50 * M


def test_fdg_ca1_flujo_ytd_sin_el_trimestre_de_3_meses_descarta_q2_y_q3():
    quitar = lambda h: h[1] == "NetCashProvidedByUsedInOperatingActivities" and h[3]["end"] == "2025-03-31"  # noqa: E731
    salida = parsear([h for h in empresa() if not quitar(h)])
    # QM-5: sin hecho de 3 meses el acumulado de 6M se descarta; la corrida de caja queda en 1 trimestre.
    assert (salida.result, salida.code) == (None, "datos_insuficientes")


def test_fdg_ca1_acciones_de_q4_por_la_formula_4fy_menos_trio():
    # Q1..Q3 = 1000 M, FY = 1010 M => Q4 = 4*1010 - 3000 = 1040 M (dentro del +-10% de Q3)
    hechos = empresa(anios=(2025,))
    hechos = sin_tag(hechos, "WeightedAverageNumberOfDilutedSharesOutstanding") + flujo(
        "WeightedAverageNumberOfDilutedSharesOutstanding", 2025, [1000 * M] * 4, "shares", fy=1010 * M
    )
    recs = por_fecha(ok(hechos).income_q)
    assert recs["2025-12-31"]["weightedAverageShsOutDil"] == 1040 * M
    assert recs["2025-09-30"]["weightedAverageShsOutDil"] == 1000 * M


@pytest.mark.parametrize(
    ("fy", "emitido"),
    [
        (1_025_000_000, 1_100_000_000),  # Q4 = 4*1.025 - 3.000 = 1.100 M: +10% exacto de Q3, inclusivo => derivado
        (1_025_000_001, 1_025_000_001),  # Q4 = 1.100.000.004: pasa el 10% => valor de FY
        (990_000_000, 960_000_000),  # Q4 = 960 M (-4%) => derivado
        (900_000_000, 900_000_000),  # Q4 = 600 M (-40%) => valor de FY
    ],
)
def test_fdg_ca1_acciones_de_q4_aceptadas_solo_dentro_de_10_por_ciento_de_q3(fy, emitido):
    hechos = sin_tag(empresa(anios=(2025,)), "WeightedAverageNumberOfDilutedSharesOutstanding") + flujo(
        "WeightedAverageNumberOfDilutedSharesOutstanding", 2025, [1_000_000_000] * 4, "shares", fy=fy
    )
    assert por_fecha(ok(hechos).income_q)["2025-12-31"]["weightedAverageShsOutDil"] == emitido


def test_fdg_ca1_eps_de_q4_es_fy_menos_trio_cuando_las_acciones_estan_en_la_misma_base():
    pkg = ok(empresa(anios=(2025,)))
    n = (2025 - 2020) * 4
    v = _valores(2025, {}, (1, 1))
    esperado = round(v["eps_dil_fy"] - sum(v["eps_dil"][:3]), 6)
    assert por_fecha(pkg.income_q)["2025-12-31"]["epsDiluted"] == pytest.approx(esperado, abs=1e-9)
    assert n == 20


def test_fdg_ca1_eps_de_q4_none_si_la_base_de_acciones_cambia_dentro_del_fy():
    # Acciones del FY el doble que las de Q1-Q3 (split dentro del ano): el EPS de Q4 no se deriva.
    hechos = sin_tag(empresa(anios=(2025,)), "WeightedAverageNumberOfDilutedSharesOutstanding") + flujo(
        "WeightedAverageNumberOfDilutedSharesOutstanding", 2025, [1_000_000_000] * 4, "shares", fy=2_000_000_000
    )
    recs = por_fecha(ok(hechos).income_q)
    assert recs["2025-12-31"]["epsDiluted"] is None
    assert recs["2025-12-31"]["weightedAverageShsOutDil"] == 2_000_000_000  # Q4 fuera de +-10%: valor de FY


@pytest.mark.parametrize("quitar", ["fy_acciones", "q2_acciones", "q2_eps"])
def test_fdg_ca1_eps_de_q4_none_si_falta_un_insumo(quitar):
    hechos = empresa(anios=(2025,))
    dil = "WeightedAverageNumberOfDilutedSharesOutstanding"
    if quitar == "fy_acciones":
        hechos = [h for h in hechos if not (h[1] == dil and h[3]["end"] == "2025-12-31")]
    elif quitar == "q2_acciones":
        hechos = [h for h in hechos if not (h[1] == dil and h[3]["end"] == "2025-06-30")]
    else:
        hechos = [h for h in hechos if not (h[1] == "EarningsPerShareDiluted" and h[3]["end"] == "2025-06-30")]
    salida = parsear(hechos)
    if salida.result is not None:
        assert por_fecha(salida.result.income_q)["2025-12-31"]["epsDiluted"] is None


def test_fdg_ca1_dedupe_gana_el_filed_mas_reciente_aunque_cambie_fy_y_aparezca_despues():
    base = sin_tag(empresa(), "Revenues")
    n = (2025 - 2020) * 4
    q1 = (1000 + 10 * n) * M
    viejo = fact("Revenues", "USD", "2025-01-01", "2025-03-31", 111 * M, "2025-05-01", "10-Q", fy=2025, fp="Q1")
    nuevo = fact("Revenues", "USD", "2025-01-01", "2025-03-31", q1, "2026-11-03", "10-K", fy=2026, fp="Q1")
    resto = [h for h in empresa() if h[1] == "Revenues" and not (h[3]["start"] == "2025-01-01" and h[3]["end"] == "2025-03-31")]
    for orden in ([nuevo, viejo], [viejo, nuevo]):
        recs = por_fecha(ok(base + resto + orden).income_q)
        assert recs["2025-03-31"]["revenue"] == q1


def test_fdg_ca1_dedupe_con_mismo_filed_gana_el_accn_mayor_en_cualquier_orden():
    base = sin_tag(empresa(), "NetIncomeLoss")
    n = (2025 - 2020) * 4
    verdadero = (150 + 2 * n) * M
    resto = [h for h in empresa() if h[1] == "NetIncomeLoss" and not (h[3]["start"] == "2025-01-01" and h[3]["end"] == "2025-03-31")]
    bajo = fact("NetIncomeLoss", "USD", "2025-01-01", "2025-03-31", 5 * M, "2025-05-01", accn="0000000001-25-000100")
    alto = fact("NetIncomeLoss", "USD", "2025-01-01", "2025-03-31", verdadero, "2025-05-01", accn="0000000001-25-000200")
    for orden in ([bajo, alto], [alto, bajo]):
        assert por_fecha(ok(base + resto + orden).income_q)["2025-03-31"]["netIncome"] == verdadero


def test_fdg_ca1_los_periodos_se_asignan_por_start_end_y_no_por_fy_fp():
    # Todos los hechos dicen fy=2099/fp=Q9: el resultado es el mismo que con fy/fp normales.
    sucios = [(ns, t, u, dict(h, fy=2099, fp="Q9")) for ns, t, u, h in empresa()]
    limpio, sucio = ok(empresa()), ok(sucios)
    assert limpio.income_q == sucio.income_q and limpio.cash_q == sucio.cash_q and limpio.balance_a == sucio.balance_a


def test_fdg_ca1_unidad_distinta_de_usd_no_se_acepta_ni_se_convierte():
    hechos = empresa()
    n = (2025 - 2020) * 4
    # El tag trae primero EUR (valor distinto) y luego USD: solo vale USD.
    eur = [fact("Revenues", "EUR", "2025-01-01", "2025-03-31", 1, "2025-05-01")]
    doc = companyfacts(*eur, *hechos, cik=CIK)
    ordenado = doc["facts"]["us-gaap"]["Revenues"]["units"]
    assert next(iter(ordenado)) == "EUR"
    salida = parse_company_facts(doc, quarters=12, today=HOY, expected_cik=CIK)
    assert por_fecha(salida.result.income_q)["2025-03-31"]["revenue"] == (1000 + 10 * n) * M


def test_fdg_ca1_sin_usd_cuando_los_conceptos_monetarios_solo_estan_en_otra_unidad():
    eur = [(ns, t, "EUR" if u == "USD" else u, h) for ns, t, u, h in empresa()]
    assert parsear(eur).code == "sin_usd"


def test_fdg_ca1_sin_us_gaap_y_namespace_ifrs():
    ifrs = [fact("Revenues", "USD", "2025-01-01", "2025-03-31", 1, "2025-05-01", namespace="ifrs-full")]
    assert parsear(ifrs).code == "no_us_gaap"
    assert parsear([]).code == "no_us_gaap"
    vacio = {"cik": CIK, "facts": {"us-gaap": {}}}
    assert parse_company_facts(vacio, quarters=12, today=HOY, expected_cik=CIK).code == "datos_insuficientes"


@pytest.mark.parametrize(
    "quitar",
    [
        ("Revenues",),
        ("NetIncomeLoss",),
        (EBT_TAG,),
        ("IncomeTaxExpenseBenefit",),
        ("CostOfRevenue", "GrossProfit"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"),
        ("Assets",),
        ("AssetsCurrent",),
        ("LiabilitiesCurrent",),
        ("StockholdersEquity",),
        ("NetCashProvidedByUsedInOperatingActivities",),
        ("PaymentsToAcquirePropertyPlantAndEquipment",),
    ],
)
def test_fdg_ca1_falta_un_concepto_requerido_es_datos_insuficientes(quitar):
    salida = parsear(sin_tag(empresa(), *quitar))
    assert (salida.result, salida.code) == (None, "datos_insuficientes")


def test_fdg_ca1_opcionales_ausentes_quedan_none_nunca_cero():
    opcionales = ("InterestExpense", "GrossProfit", "OperatingIncomeLoss", "RetainedEarningsAccumulatedDeficit",
                  "PropertyPlantAndEquipmentNet", "CashAndCashEquivalentsAtCarryingValue",
                  "DepreciationDepletionAndAmortization", "PaymentsOfDividendsCommonStock", "EarningsPerShareDiluted",
                  "EarningsPerShareBasic", "DebtCurrent", "LongTermDebtNoncurrent")
    pkg = ok(sin_tag(empresa(), *opcionales))
    inc, bal, cash = pkg.income_q[0], pkg.balance_q[0], pkg.cash_q[0]
    for campo in ("interestExpense", "grossProfit", "operatingIncome", "ebit", "ebitda", "eps", "epsDiluted"):
        assert inc[campo] is None
    for campo in ("retainedEarnings", "propertyPlantEquipmentNet", "cashAndCashEquivalents", "shortTermDebt",
                  "longTermDebt", "totalDebt"):
        assert bal[campo] is None
    assert cash["commonDividendsPaid"] is None and cash["depreciationAndAmortization"] is None
    assert pkg.own_metrics["payoutRatio"] is None and pkg.own_metrics["netDebtToEBITDA"] is None


def test_fdg_ca1_menos_de_cuatro_trimestres_es_datos_insuficientes():
    sin_10k = [h for h in empresa(anios=(2025,)) if h[3]["form"] != "10-K"]  # sin FY: solo Q1-Q3
    assert parsear(sin_10k).code == "datos_insuficientes"


def test_fdg_ca1_balance_sin_registros_es_datos_insuficientes():
    assert parsear(sin_tag(empresa(), "Assets")).code == "datos_insuficientes"


@pytest.mark.parametrize(("dias", "code"), [(180, None), (181, "desactualizado")])
def test_fdg_ca1_obsolescencia_de_180_dias_inclusiva(dias, code):
    hoy = date(2025, 12, 31).fromordinal(date(2025, 12, 31).toordinal() + dias)
    assert parsear(empresa(), today=hoy).code == code


def test_fdg_ca1_today_puede_ser_datetime():
    assert parsear(empresa(), today=datetime(2026, 3, 1, 12, 0)).code is None


def test_fdg_ca1_ebit_ebitda_y_fcf_con_definicion_propia():
    pkg = ok(empresa())
    n = (2025 - 2020) * 4
    q1 = pkg.income_q[3]
    assert q1["date"] == "2025-03-31"
    assert q1["ebit"] == q1["operatingIncome"] == (200 + 2 * n) * M
    assert q1["ebitda"] == (200 + 2 * n) * M + 20 * M
    assert q1["grossProfit"] == (400 + 4 * n) * M and q1["costOfRevenue"] == (600 + 6 * n) * M


def test_fdg_ca1_costo_de_ventas_cae_a_ingresos_menos_utilidad_bruta_y_utilidad_neta_a_profitloss():
    hechos = sin_tag(empresa(), "CostOfRevenue")
    n = (2025 - 2020) * 4
    assert ok(hechos).income_q[3]["costOfRevenue"] == (1000 + 10 * n) * M - (400 + 4 * n) * M
    perfil = [(ns, "ProfitLoss" if t == "NetIncomeLoss" else t, u, h) for ns, t, u, h in empresa()]
    assert ok(perfil).income_q[3]["netIncome"] == (150 + 2 * n) * M


def test_fdg_ca1_total_de_pasivos_se_deriva_de_pasivos_y_patrimonio_menos_patrimonio():
    v = _valores(2025, {}, (1, 1))
    plp = [a for a in v["assets"]]
    hechos = sin_tag(empresa(), "Liabilities") + instante("LiabilitiesAndStockholdersEquity", 2025, plp)
    assert ok(hechos).balance_q[0]["totalLiabilities"] == v["liab"][3]
    # Con patrimonio incluyendo participacion no controlante gana ese tag
    nci = [e + 10 * M for e in v["equity"]]
    hechos2 = hechos + instante("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 2025, nci)
    bal = ok(hechos2).balance_q[0]
    assert bal["totalLiabilities"] == v["assets"][3] - nci[3]
    assert bal["totalEquity"] == nci[3] and bal["totalStockholdersEquity"] == v["equity"][3]
    # Sin ningun insumo el total queda None
    assert ok(sin_tag(empresa(), "Liabilities")).balance_q[0]["totalLiabilities"] is None


def test_fdg_ca1_metricas_propias_roe_deuda_y_payout():
    pkg = ok(empresa())
    ni_ttm = sum((150 + 2 * (20 + j)) * M for j in range(4))
    ebitda_ttm = sum((200 + 2 * (20 + j)) * M + 20 * M for j in range(4))
    patrimonio = pkg.balance_q[0]["totalEquity"]
    m = pkg.own_metrics
    assert m["roe"] == pytest.approx(ni_ttm / patrimonio)
    assert m["debtToEquity"] == pytest.approx(550 * M / patrimonio)
    assert m["netDebtToEBITDA"] == pytest.approx((550 * M - 500 * M) / ebitda_ttm)
    assert m["payoutRatio"] == pytest.approx(40 * M / ni_ttm) and m["dividendsPaidTTM"] == 40 * M
    assert m["dividendYield"] is None  # necesita marketCap del llamador


def test_fdg_ca1_metricas_propias_con_patrimonio_negativo_o_ebitda_no_positivo_son_none():
    v = _valores(2025, {}, (1, 1))
    neg = [-x for x in v["equity"]]
    hechos = sin_tag(empresa(), "StockholdersEquity", "Liabilities") + instante("StockholdersEquity", 2025, neg)
    m = ok(hechos).own_metrics
    assert m["roe"] is None and m["debtToEquity"] is None
    sin_op = [(ns, t, u, dict(h, val=-abs(h["val"]))) if t == "OperatingIncomeLoss" else (ns, t, u, h) for ns, t, u, h in empresa()]
    assert ok(sin_op).own_metrics["netDebtToEBITDA"] is None
    perdida = [(ns, t, u, dict(h, val=-abs(h["val"]))) if t == "NetIncomeLoss" else (ns, t, u, h) for ns, t, u, h in empresa()]
    assert ok(perdida).own_metrics["payoutRatio"] is None


@pytest.mark.parametrize(
    ("div", "cap", "esperado"),
    [(40, 1000, 0.04), (None, 1000, None), (40, None, None), (40, 0, None), (40, -5, None), (40, True, None), (0, 1000, 0.0)],
)
def test_fdg_ca1_dividend_yield_es_none_si_falta_un_insumo_nunca_cero(div, cap, esperado):
    assert X.dividend_yield(div, cap) == esperado


def test_fdg_ca1_deuda_larga_por_cascada_y_heuristica_h_deuda():
    base = sin_tag(empresa(anios=(2025,)), "DebtCurrent", "LongTermDebtNoncurrent")
    # AAPL-like: solo LongTermDebt (total) con vencimiento corriente y papel comercial
    aapl = base + instante("LongTermDebt", 2025, [781 * M] * 4) + instante("LongTermDebtCurrent", 2025, [99 * M] * 4)
    aapl += instante("CommercialPaper", 2025, [19 * M] * 4)
    bal = ok(aapl).balance_q[0]
    assert (bal["shortTermDebt"], bal["longTermDebt"], bal["totalDebt"]) == (118 * M, 682 * M, 800 * M)
    # ADBE-like: LongTermDebt es la porcion no corriente cuando solo hay DebtCurrent
    adbe = base + instante("LongTermDebt", 2025, [476 * M] * 4) + instante("DebtCurrent", 2025, [159 * M] * 4)
    bal = ok(adbe).balance_q[0]
    assert (bal["shortTermDebt"], bal["longTermDebt"], bal["totalDebt"]) == (159 * M, 476 * M, 635 * M)
    # LongTermDebt sin ningun marcador de porcion corriente: ambiguo => None
    solo = base + instante("LongTermDebt", 2025, [476 * M] * 4) + instante("CommercialPaper", 2025, [10 * M] * 4)
    assert ok(solo).balance_q[0]["longTermDebt"] is None
    # LongTermDebtAndCapitalLeaseObligations antes que la heuristica
    cap = base + instante("LongTermDebtAndCapitalLeaseObligations", 2025, [300 * M] * 4) + instante("ShortTermBorrowings", 2025, [20 * M] * 4)
    bal = ok(cap).balance_q[0]
    assert (bal["longTermDebt"], bal["shortTermDebt"], bal["totalDebt"]) == (300 * M, 20 * M, 320 * M)


# --- CA-8: cascada de tags por periodo ----------------------------------------------------


def test_fdg_ca8_cascada_por_periodo_produce_una_serie_continua_sin_duplicar_ni_mezclar():
    todo = empresa()
    rev_antiguo = [h for h in todo if h[1] == "Revenues" and h[3]["end"] < "2025-01-01"]
    rev_nuevo = [h for h in todo if h[1] == "Revenues" and h[3]["end"] >= "2024-01-01"]
    renombrado = [(ns, "RevenueFromContractWithCustomerExcludingAssessedTax", u, h) for ns, _t, u, h in rev_nuevo]
    # 'Revenues' cubre 2023-2024 y el tag nuevo 2024-2025 (se solapan en 2024)
    hechos = sin_tag(todo, "Revenues") + [h for h in rev_antiguo if h[3]["end"] <= "2024-12-31"] + renombrado
    pkg = ok(hechos)
    assert [r["revenue"] for r in pkg.income_q] == [(1000 + 10 * (20 + 3 - i)) * M for i in range(12)]
    assert len({r["date"] for r in pkg.income_q}) == 12
    assert pkg.income_a[0]["revenue"] == sum((1000 + 10 * (20 + j)) * M for j in range(4))


def test_fdg_ca8_conflicto_de_tags_gana_la_prioridad_aunque_el_otro_sea_mas_reciente():
    # QM-1: 100 (Revenues, filed viejo) contra 98 (otro tag, filed posterior): gana 100 y se cuenta tag_conflicto.
    base = [h for h in empresa() if not (h[1] == "Revenues" and h[3]["end"] == "2024-03-31" and h[3]["start"] == "2024-01-01")]
    primero = fact("Revenues", "USD", "2024-01-01", "2024-03-31", 100 * M, "2024-05-01")
    segundo = fact("SalesRevenueNet", "USD", "2024-01-01", "2024-03-31", 98 * M, "2026-05-01")
    salida = parsear(base + [primero, segundo])
    assert por_fecha(salida.result.income_q)["2024-03-31"]["revenue"] == 100 * M
    assert salida.rejected["tag_conflicto"] == 1


def test_fdg_ca8_el_mismo_valor_en_dos_tags_no_es_conflicto():
    hechos = empresa() + [
        (ns, "SalesRevenueNet", u, h) for ns, t, u, h in empresa() if t == "Revenues"
    ]
    assert "tag_conflicto" not in parsear(hechos).rejected


# --- CA-9: contiguidad y clases de duracion ----------------------------------------------


def test_fdg_ca9_con_un_hueco_de_mas_de_120_dias_solo_se_devuelve_la_corrida_mas_reciente():
    pkg = ok(empresa(anios=(2023, 2025)))  # hueco 2023-12-31 -> 2025-03-31 = 456 dias
    assert [r["date"] for r in pkg.income_q] == ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
    assert [r["date"] for r in pkg.cash_q] == [r["date"] for r in pkg.income_q]
    assert [r["date"] for r in pkg.balance_q] == [r["date"] for r in pkg.income_q]


def test_fdg_ca9_un_trimestre_faltante_en_el_medio_corta_la_corrida():
    sin_q2_2024 = [h for h in empresa() if h[3]["end"] != "2024-06-30"]
    pkg = ok(sin_q2_2024)
    assert [r["date"] for r in pkg.income_q][-1] == "2024-09-30" and len(pkg.income_q) == 6
    assert len(pkg.balance_q) == 6 and all(r["date"] > "2024-06-30" for r in pkg.cash_q)


@pytest.mark.parametrize(("dias", "contiguos"), [(79, False), (80, True), (105, True), (106, False)])
def test_fdg_ca9_bordes_de_contiguidad_80_y_105_dias(dias, contiguos):
    a = date(2025, 1, 1)
    b = date.fromordinal(a.toordinal() + dias)
    assert (len(X._corrida([a, b])) == 2) == contiguos
    assert X._corrida([]) == []


@pytest.mark.parametrize(
    ("dias", "clase"),
    [(79, None), (80, "Q"), (98, "Q"), (105, "Q"), (106, None), (169, None), (170, "6M"), (183, "6M"), (200, "6M"),
     (201, None), (259, None), (260, "9M"), (290, "9M"), (291, None), (349, None), (350, "FY"), (371, "FY"),
     (380, "FY"), (381, None)],
)
def test_fdg_ca9_clasificacion_de_duraciones_acepta_trimestres_de_14_semanas_y_anios_de_53(dias, clase):
    assert X._clase(dias) == clase


def test_fdg_ca9_una_duracion_de_183_dias_sin_hecho_de_3_meses_se_descarta():
    # QM-5: periodo de transicion de 183 dias: no es FY ni acumulado utilizable.
    extra = [
        fact("Revenues", "USD", "2022-07-01", "2022-12-31", 5 * M, "2023-02-15", "10-K"),
        fact("NetIncomeLoss", "USD", "2022-07-01", "2022-12-31", 1 * M, "2023-02-15", "10-K"),
    ]
    pkg = ok(empresa() + extra)
    assert "2022-12-31" not in [r["date"] for r in pkg.income_a]
    assert "2022-12-31" not in [r["date"] for r in pkg.income_q]


# --- CA-10b y T-CS-20: split ----------------------------------------------------------------


def _con_split(ratio_num, ratio_den=1_000_000_000):
    """2024 con 1.000 M de acciones y 2025 con `ratio` veces esas acciones."""
    nuevo = 1_000_000_000 * ratio_num // ratio_den if ratio_den != 1_000_000_000 else ratio_num
    return empresa(anios=(2024, 2025), acciones={2025: (nuevo, nuevo * 99 // 100)})


@pytest.mark.parametrize(
    ("acciones_2025", "es_split"),
    [
        (1_200_000_000, False),  # 1,2 exacto NO es split (6:5 sin detectar por diseno)
        (1_201_000_000, True),
        (1_250_000_000, True),  # 5:4
        (1_500_000_000, True),  # 3:2
        (833_300_000, False),  # 0,8333 NO es split
        (829_000_000, True),
        (1_052_600_000, False),  # 1,0526 (recompra 5%)
    ],
)
def test_fdg_ca10b_cociente_de_acciones_con_las_constantes_unicas(acciones_2025, es_split):
    assert (SPLIT_RATIO_MAX, SPLIT_RATIO_MIN) == (1.2, 0.83)
    pkg = ok(empresa(anios=(2024, 2025), acciones={2025: (acciones_2025, acciones_2025 * 99 // 100)}))
    antes = [r for r in pkg.income_q if r["date"] <= "2024-12-31"]
    despues = [r for r in pkg.income_q if r["date"] > "2024-12-31"]
    assert len(antes) == 4 and len(despues) == 4
    campos = ("eps", "epsDiluted", "weightedAverageShsOut", "weightedAverageShsOutDil")
    for r in antes:
        assert all((r[c] is None) == es_split for c in campos)
        assert r["netIncome"] is not None and r["revenue"] is not None  # no dependen de acciones
    assert all(r[c] is not None for r in despues for c in campos)


def test_fdg_ca10b_el_split_de_la_serie_anual_tambien_corta_la_historia():
    pkg = ok(_con_split(1_500_000_000))
    assert pkg.income_a[0]["weightedAverageShsOutDil"] is not None
    assert pkg.income_a[1]["weightedAverageShsOutDil"] is None and pkg.income_a[1]["revenue"] is not None


def _rec(dil, bas=None):
    return {"weightedAverageShsOutDil": dil, "weightedAverageShsOut": bas, "eps": 1, "epsDiluted": 1}


def test_fdg_ca10b_si_faltan_diluidas_en_un_trimestre_se_usan_las_basicas_de_ambos():
    recs = [_rec(None, 1000), _rec(2000, 1010), _rec(2000, 1010)]  # basicas estables; diluidas no comparables
    X._cortar_splits(recs)
    assert all(r["eps"] == 1 for r in recs)
    recs = [_rec(None, 1000), _rec(2000, 2000)]  # basicas con split
    X._cortar_splits(recs)
    assert recs[0]["eps"] is None and recs[0]["weightedAverageShsOut"] is None and recs[1]["eps"] == 1


def test_fdg_ca10b_par_no_evaluable_deja_en_none_al_mas_antiguo_de_cada_par():
    recs = [_rec(1000, 1000), _rec(None, None), _rec(1000, 1000)]
    X._cortar_splits(recs)
    # pares (0,1) y (1,2) no evaluables: quedan en None los mas antiguos de cada par; el mas reciente no
    assert recs[0]["eps"] is None and recs[1]["eps"] is None and recs[2]["eps"] == 1


@pytest.mark.parametrize(
    ("nuevo", "corta"),
    [(1_500_000_000, True), (1_250_000_000, True), (950_000_000, False), (200_000_000, True)],
)
def test_fdg_tcs20_split_32_y_54_cortan_la_historia_y_una_recompra_de_5_no(nuevo, corta):
    pkg = ok(empresa(anios=(2024, 2025), acciones={2025: (nuevo, nuevo * 99 // 100)}))
    anterior = por_fecha(pkg.income_q)["2024-12-31"]
    assert (anterior["epsDiluted"] is None) == corta


# --- CA-11 y T-CS-9: saneamiento --------------------------------------------------------------


def _q2_2024_ingresos(**cambios):
    """Empresa con un campo del hecho de ingresos del Q2 2024 alterado."""
    return modificar(empresa(), "Revenues", desde="2024-04-01", hasta="2024-06-30", **cambios)


@pytest.mark.parametrize(
    ("cambios", "rechazo"),
    [
        ({"val": float("nan")}, "val_invalido"),
        ({"val": float("inf")}, "val_invalido"),
        ({"val": True}, "val_invalido"),
        ({"val": "123"}, "val_invalido"),
        ({"val": None}, "val_invalido"),
        ({"end": "2024-13-45"}, "fecha_invalida"),
        ({"end": "24-06-30"}, "fecha_invalida"),
        ({"end": 20240630}, "fecha_invalida"),
        ({"filed": "ayer"}, "fecha_invalida"),
        ({"start": "2024-02-30"}, "fecha_invalida"),
        ({"accn": "12345"}, "accn_invalido"),
        ({"accn": None}, "accn_invalido"),
        ({"val": 2 * 10**13}, "valor_fuera_de_rango"),
        ({"val": 10**400}, "valor_fuera_de_rango"),
        ({"form": "20-F"}, "form_excluido"),
        ({"form": ["10-K"]}, "form_excluido"),
    ],
)
def test_fdg_ca11_hechos_invalidos_se_rechazan_y_se_cuentan(cambios, rechazo):
    salida = parsear(_q2_2024_ingresos(**cambios))
    assert salida.code is None
    assert salida.rejected[rechazo] >= 1
    assert "2024-06-30" not in [r["date"] for r in salida.result.income_q]  # el hecho malo no se usa


def test_fdg_ca11_hecho_que_no_es_dict_y_flujo_sin_start_se_rechazan():
    doc = companyfacts(*empresa(), cik=CIK)
    doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append("basura")
    sin_start = [h for h in doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"] if isinstance(h, dict)
                 and h["end"] == "2024-06-30" and h["start"] == "2024-04-01"][0]
    del sin_start["start"]
    salida = parse_company_facts(doc, quarters=12, today=HOY, expected_cik=CIK)
    assert salida.rejected["hecho_invalido"] == 1 and salida.rejected["fecha_invalida"] == 1


@pytest.mark.parametrize("val", [49_999, 500_000_000_001, 0, -5])
def test_fdg_ca11_acciones_fuera_de_1e5_a_5e11_se_rechazan(val):
    mala = modificar(empresa(), "WeightedAverageNumberOfDilutedSharesOutstanding", desde="2024-04-01", hasta="2024-06-30", val=val)
    assert parsear(mala).rejected["valor_fuera_de_rango"] >= 1


def test_fdg_ca11_activos_no_positivos_se_descartan():
    mala = modificar(empresa(), "Assets", hasta="2024-03-31", val=0)
    pkg = ok(mala)
    assert "2024-03-31" not in [r["date"] for r in pkg.balance_q]


def test_fdg_ca11_ingresos_y_costo_negativos_se_anulan_y_se_cuentan():
    pkg = parsear(modificar(empresa(), "Revenues", desde="2024-04-01", hasta="2024-06-30", val=-5 * M)).result
    assert por_fecha(pkg.income_q)["2024-06-30"]["revenue"] is None
    salida = parsear(modificar(empresa(), "CostOfRevenue", desde="2024-04-01", hasta="2024-06-30", val=-5 * M))
    assert por_fecha(salida.result.income_q)["2024-06-30"]["costOfRevenue"] is None
    assert salida.rejected["valor_negativo"] == 1


def test_fdg_ca11_identidad_contable_rota_descarta_ese_balance():
    mala = modificar(empresa(), "Liabilities", hasta="2024-03-31", val=_valores(2024, {}, (1, 1))["liab"][0] * 107 // 100)
    salida = parsear(mala)
    assert salida.rejected["identidad_contable"] == 1
    assert "2024-03-31" not in [r["date"] for r in salida.result.balance_q]
    # Un desvio de 1% o menos se acepta
    leve = modificar(empresa(), "Liabilities", hasta="2024-03-31", val=_valores(2024, {}, (1, 1))["liab"][0] * 1004 // 1000)
    assert "identidad_contable" not in parsear(leve).rejected


def test_fdg_ca11_eps_incoherente_con_utilidad_sobre_acciones_se_emite_none():
    mala = modificar(empresa(), "EarningsPerShareDiluted", desde="2024-04-01", hasta="2024-06-30", val=9.99)
    salida = parsear(mala)
    rec = por_fecha(salida.result.income_q)["2024-06-30"]
    assert rec["epsDiluted"] is None and rec["eps"] is not None  # no se "corrige", solo se anula
    assert salida.rejected["eps_incoherente"] >= 1  # el Q2 malo tambien contamina el Q4 derivado
    # eps == 0 no se evalua
    cero = modificar(empresa(), "EarningsPerShareDiluted", desde="2024-04-01", hasta="2024-06-30", val=0)
    assert por_fecha(ok(cero).income_q)["2024-06-30"]["epsDiluted"] == 0  # no se evalua ni se anula


def test_fdg_ca11_capex_positivo_se_normaliza_a_negativo_y_se_cuenta():
    salida = parsear(empresa())
    assert salida.rejected["capex_normalizado"] >= 1
    assert all(r["capitalExpenditure"] < 0 for r in salida.result.cash_q)
    ya_negativo = modificar(empresa(), "PaymentsToAcquirePropertyPlantAndEquipment", val=-50 * M)
    assert "capex_normalizado" not in parsear(ya_negativo).rejected


def test_fdg_ca11_intereses_negativos_se_normalizan_a_positivo_y_se_cuentan():
    n = (2025 - 2020) * 4
    negativos = [(ns, t, u, dict(h, val=-h["val"])) if t == "InterestExpense" else (ns, t, u, h) for ns, t, u, h in empresa()]
    salida = parsear(negativos)
    assert salida.rejected["interes_normalizado"] >= 1
    assert all(r["interestExpense"] == 5 * M for r in salida.result.income_q)
    assert "interes_normalizado" not in parsear(empresa()).rejected
    assert n == 20


def test_fdg_ca11_dividendos_pagados_se_emiten_negativos():
    assert all(r["commonDividendsPaid"] == -10 * M for r in ok(empresa()).cash_q)


def test_fdg_tcs9_raiz_o_facts_con_tipos_invalidos_es_datos_insuficientes_sin_excepcion():
    for raw in (None, [], "x", 5, {"cik": CIK, "facts": None}, {"cik": CIK, "facts": [1]}):
        salida = parse_company_facts(raw, quarters=12, today=HOY, expected_cik=CIK)
        assert (salida.result, salida.code) == (None, "datos_insuficientes")


def test_fdg_tcs9_todos_los_hechos_con_val_de_tipo_invalido_es_datos_insuficientes():
    corruptos = [(ns, t, u, dict(h, val="NaN")) for ns, t, u, h in empresa()]
    salida = parsear(corruptos)
    assert (salida.result, salida.code) == (None, "datos_insuficientes")
    assert salida.rejected["val_invalido"] > 100


# --- T-CS-15: identidad del documento --------------------------------------------------------


@pytest.mark.parametrize(
    ("cik_doc", "esperado"),
    [(999, CIK), (None, CIK), (True, 1), ("abc", CIK), (str(CIK), 999), (0, 0), (10**11, 10**11), (CIK, None), (CIK, "x")],
)
def test_fdg_tcs15_cik_que_no_coincide_no_entrega_numeros(cik_doc, esperado):
    salida = parsear(empresa(), cik=cik_doc, expected=esperado)
    assert (salida.result, salida.code) == (None, "cik_no_coincide")


@pytest.mark.parametrize(("cik_doc", "esperado"), [(CIK, CIK), (str(CIK), CIK), (CIK, str(CIK)), (f"{CIK:010d}", CIK)])
def test_fdg_tcs15_cik_equivalente_en_int_o_texto_si_coincide(cik_doc, esperado):
    assert parsear(empresa(), cik=cik_doc, expected=esperado).code is None


# --- T-CS-21: coherencia, TTM y valor atipico (QB-6, QB-7, QB-8) --------------------------------


def _romper_fy(hechos, tag, delta, y=2025):
    """Q4 reportado directo + FY desviado `delta`: la suma de 4 trimestres no iguala al FY."""
    fy = next(h for h in hechos if h[1] == tag and h[3].get("start") == f"{y}-01-01" and h[3]["end"] == f"{y}-12-31")
    nueve = next(h for h in hechos if h[1] == tag and h[3].get("start") == f"{y}-01-01" and h[3]["end"] == f"{y}-09-30")
    q4 = fy[3]["val"] - nueve[3]["val"]
    directo = fact(tag, fy[2], f"{y}-10-01", f"{y}-12-31", q4, f"{y + 1}-02-15", "10-K")
    return modificar(hechos, tag, desde=f"{y}-01-01", hasta=f"{y}-12-31", val=fy[3]["val"] + delta) + [directo]


@pytest.mark.parametrize(
    "tag",
    [
        "Revenues",
        "CostOfRevenue",
        "NetIncomeLoss",
        EBT_TAG,
        "IncomeTaxExpenseBenefit",
        "InterestExpense",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
)
def test_fdg_tcs21_fy_que_no_cuadra_por_5_millones_es_ttm_incoherente(tag):
    assert parsear(_romper_fy(empresa(), tag, 5_000_000)).code == "ttm_incoherente"


@pytest.mark.parametrize(
    ("delta", "code"),
    [(3_999_999, None), (4_000_000, None), (4_000_001, "ttm_incoherente"), (5_000_000, "ttm_incoherente"), (-5_000_000, "ttm_incoherente")],
)
def test_fdg_tcs21_borde_exacto_de_la_tolerancia_absoluta_de_4_millones(delta, code):
    assert parsear(_romper_fy(empresa(), "Revenues", delta)).code == code


def test_fdg_tcs21_si_el_ultimo_trimestre_no_cierra_fy_el_control_no_aplica():
    sin_fy_2025 = [h for h in empresa() if not (h[3]["end"] >= "2025-10-01")]  # termina en 2025-09-30
    desviado = _romper_fy(sin_fy_2025, "Revenues", 50 * M, y=2024)
    assert parsear(desviado).code is None


def test_fdg_tcs21_si_falta_el_fy_o_un_trimestre_de_un_campo_no_se_controla_ese_campo():
    sin_interes_fy = [h for h in empresa() if not (h[1] == "InterestExpense" and h[3].get("start") == "2025-01-01"
                                                    and h[3]["end"] == "2025-12-31")]
    assert parsear(sin_interes_fy).code is None  # interestExpense sin FY: se omite
    sin_q1 = [h for h in empresa() if not (h[1] == "InterestExpense" and h[3]["end"] == "2025-03-31")]
    assert parsear(sin_q1).code is None  # un trimestre faltante: no hay suma de 4


@pytest.mark.parametrize(("dias", "code"), [(0, None), (1, None), (3, None), (4, "end_incoherente"), (5, "end_incoherente")])
def test_fdg_tcs21_end_del_balance_trimestral_coherente_con_tolerancia_de_3_dias_inclusive(dias, code):
    original = [h for h in empresa() if h[2] == "USD" and h[3]["end"] == "2025-12-31" and "start" not in h[3]]
    desplazado = [
        (ns, t, u, dict(h, end=date.fromordinal(date(2025, 12, 31).toordinal() + dias).isoformat()))
        for ns, t, u, h in original
    ]
    salida = parsear(empresa() + (desplazado if dias else []))
    assert salida.code == code


def test_fdg_tcs21_end_del_flujo_de_caja_trimestral_incoherente():
    quitar_q4_caja = [h for h in empresa() if not (h[1] in ("NetCashProvidedByUsedInOperatingActivities",
                                                           "PaymentsToAcquirePropertyPlantAndEquipment",
                                                           "DepreciationDepletionAndAmortization",
                                                           "PaymentsOfDividendsCommonStock")
                                                 and h[3]["end"] >= "2025-10-01")]
    # caja termina en 2025-09-30 y el income en 2025-12-31 (92 dias): end_incoherente
    assert parsear(quitar_q4_caja).code == "end_incoherente"


def test_fdg_tcs21_end_anual_incoherente_aunque_los_trimestrales_coincidan():
    base = empresa()
    tag = "NetCashProvidedByUsedInOperatingActivities"
    q4_directo = fact(tag, "USD", "2025-10-01", "2025-12-31", 900 * M, "2026-02-15", "10-K")
    sin_fy_caja = [h for h in base if not (h[1] == tag and h[3].get("start") == "2025-01-01" and h[3]["end"] == "2025-12-31")]
    pkg_o = parsear(sin_fy_caja + [q4_directo])
    assert pkg_o.code == "end_incoherente"
    assert X._coherentes([{"date": "2025-12-31"}], [{"date": "2025-12-28"}], []) is True
    assert X._coherentes([{"date": "2025-12-31"}], [{"date": "2024-12-31"}]) is False


def _ingresos_por_trimestre(a, b):
    """2023 y 2024 con A por trimestre; 2025 con B (balance e income restante intactos)."""
    base = sin_tag(empresa(anios=(2023, 2024, 2025)), "Revenues")
    return base + flujo("Revenues", 2023, [a] * 4) + flujo("Revenues", 2024, [a] * 4) + flujo("Revenues", 2025, [b] * 4)


@pytest.mark.parametrize(
    ("a", "b", "trunca"),
    [
        (1_000_000, 25_000_000, False),  # B/A = 25 exacto: no dispara
        (1_000_000, 25_010_000, True),  # 25,01
        (25_000_000, 1_000_000, False),  # B/A = 0,04 exacto: no dispara
        (25_000_000, 997_500, True),  # 0,0399
        (1_000_000, 0, False),  # un cero no dispara (revenue >= 0 es valido)
    ],
)
def test_fdg_tcs21_valor_atipico_en_los_bordes_exactos_25_y_0_04(a, b, trunca):
    pkg = ok(_ingresos_por_trimestre(a, b))
    if trunca:
        assert len(pkg.income_q) == 4 and all(r["date"] > "2024-12-31" for r in pkg.income_q)  # desde B hacia adelante
    else:
        assert len(pkg.income_q) == 12


def test_fdg_tcs21_salto_de_x40_en_ingresos_trunca_y_se_anota_valor_atipico():
    salida = parsear(_ingresos_por_trimestre(1_000_000, 40_000_000))
    assert len(salida.result.income_q) == 4 and salida.rejected["valor_atipico"] == 1
    assert len(salida.result.balance_q) == 12  # los otros estados no dependen de revenue


def test_fdg_tcs21_si_el_atipico_deja_menos_de_4_trimestres_es_datos_insuficientes():
    a = 1_000_000
    base = sin_tag(empresa(), "Revenues")
    # el salto ocurre en el ultimo trimestre: solo queda 1
    hechos = base + flujo("Revenues", 2023, [a] * 4) + flujo("Revenues", 2024, [a] * 4) + flujo("Revenues", 2025, [a, a, a, 40 * a])
    assert parsear(hechos).code == "datos_insuficientes"


def test_fdg_tcs21_activos_con_salto_de_x40_truncan_el_balance_trimestral():
    base = empresa(anios=(2023, 2024))
    salto = empresa(anios=(2025,), bal=(40, 1))
    pkg = ok(base + salto)
    assert len(pkg.balance_q) == 4 and all(r["date"] > "2024-12-31" for r in pkg.balance_q)
    assert len(pkg.income_q) == 12


def test_fdg_tcs21_activos_que_se_reducen_a_la_40ava_parte_tambien_truncan():
    base = empresa(anios=(2023, 2024))
    caida = empresa(anios=(2025,), bal=(1, 40))
    pkg = ok(base + caida)
    assert len(pkg.balance_q) == 4


# --- CA-34 (a-d): deuda ausente no es deuda cero (el cálculo vive en F1a-1) ----------------------


def _balance_con_deuda(*extra_tags_sin, agregar=()):
    base = sin_tag(empresa(anios=(2025,)), "DebtCurrent", "LongTermDebtNoncurrent", *extra_tags_sin)
    return ok(base + list(agregar)).balance_q[0]


def test_fdg_ca34_a_largo_presente_y_corto_ausente_deja_corto_y_total_en_none():
    bal = _balance_con_deuda(agregar=instante("LongTermDebtNoncurrent", 2025, [500 * M] * 4))
    assert bal["longTermDebt"] == 500 * M
    assert bal["shortTermDebt"] is None and bal["totalDebt"] is None


def test_fdg_ca34_b_corto_reportado_explicitamente_en_cero_es_un_cero_valido():
    for tag in ("DebtCurrent", "LongTermDebtCurrent"):
        bal = _balance_con_deuda(agregar=instante("LongTermDebtNoncurrent", 2025, [500 * M] * 4) + instante(tag, 2025, [0] * 4))
        assert bal["shortTermDebt"] == 0 and bal["totalDebt"] == 500 * M


def test_fdg_ca34_c_sin_ningun_tag_de_deuda_todo_queda_en_none_nunca_cero():
    bal = _balance_con_deuda()
    assert (bal["shortTermDebt"], bal["longTermDebt"], bal["totalDebt"]) == (None, None, None)


@pytest.mark.parametrize("sin_pasivos", [False, True])
def test_fdg_ca34_d_salvaguarda_deuda_mayor_que_pasivos_o_activos_invalida_el_trio(sin_pasivos):
    v = _valores(2025, {}, (1, 1))
    exceso = 3_000 * M if not sin_pasivos else 9_000 * M  # > pasivos (2.000 M) / > activos (5.000 M)
    agregar = instante("LongTermDebtNoncurrent", 2025, [exceso] * 4) + instante("DebtCurrent", 2025, [10 * M] * 4)
    extra = ("Liabilities",) if sin_pasivos else ()
    base = sin_tag(empresa(anios=(2025,)), "DebtCurrent", "LongTermDebtNoncurrent", *extra)
    salida = parsear(base + agregar)
    bal = salida.result.balance_q[0]
    assert (bal["shortTermDebt"], bal["longTermDebt"], bal["totalDebt"]) == (None, None, None)
    assert salida.rejected["deuda_incoherente"] >= 1
    assert v["liab"][3] == 2_092 * M


# --- Utilidades internas con ramas propias -------------------------------------------------------


def test_fdg_ca1_suma4_y_fy_cercano_cubren_los_bordes():
    assert X._suma4([{"x": 1}] * 3, "x") is None
    assert X._suma4([{"x": 1}] * 3 + [{"x": None}], "x") is None
    assert X._suma4([{"x": 1}] * 4, "x") == 4
    por_fin = {date(2025, 12, 31): "a", date(2025, 12, 29): "b"}
    assert X._fy_cercano(por_fin, date(2025, 12, 30)) == "a" or X._fy_cercano(por_fin, date(2025, 12, 30)) == "b"
    assert X._fy_cercano(por_fin, date(2025, 12, 28)) == "b"
    assert X._fy_cercano(por_fin, date(2025, 6, 1)) is None


def test_fdg_ca1_numeros_finitos_y_cik_validos():
    assert X._num(1) and X._num(1.5) and not X._num(True) and not X._num("1") and math.isfinite(1.0)
    assert X._cik(5) == 5 and X._cik("0000005") == 5 and X._cik(0) is None and X._cik("0") is None
    assert X._cik(10**10) is None and X._cik(True) is None and X._cik(1.5) is None and X._cik("٣") is None
    assert X._fecha("2025-02-30") is None and X._fecha("2025-02-28") == date(2025, 2, 28)


# --- Ramas de las reglas criticas (unidades puras) -------------------------------------------------


def test_fdg_ca1_trimestres_ignora_duraciones_fuera_de_clase_y_acumulados_sin_encadenar():
    d = date.fromisoformat
    ini = d("2025-01-01")
    serie = {
        (ini, d("2025-03-31")): 10,  # Q1 (YTD de 3M)
        (ini, d("2025-06-30")): 30,  # 6M
        (ini, d("2025-09-30")): 60,  # 9M
        (d("2025-02-01"), d("2025-06-01")): 999,  # 120 dias: sin clase, se ignora
    }
    out = X._trimestres(serie, True)
    assert out == {d("2025-03-31"): (10, False), d("2025-06-30"): (20, True), d("2025-09-30"): (30, True)}
    # Con un Q2 directo en el mismo fin, el directo gana al derivado
    con_directo = {**serie, (d("2025-04-01"), d("2025-06-30")): 25}
    assert X._trimestres(con_directo, True)[d("2025-06-30")] == (25, False)
    # 6M que no queda a 80-105 dias del Q1: no se deriva (y el 9M, sin 6M valido, tampoco)
    sin_encadenar = {(ini, d("2025-03-22")): 10, (ini, d("2025-07-20")): 30, (ini, d("2025-09-28")): 60}
    assert set(X._trimestres(sin_encadenar, True)) == {d("2025-03-22")}
    # 9M que no queda a 80-105 dias del 6M
    lejano = {(ini, d("2025-03-31")): 10, (ini, d("2025-06-30")): 30, (ini, d("2025-12-15")): 60}
    assert d("2025-12-15") not in X._trimestres(lejano, True)
    # En modo income (ytd=False) los acumulados no derivan Q2/Q3
    assert set(X._trimestres(serie, False)) == {d("2025-03-31")}


def test_fdg_ca1_q4_directo_de_acciones_y_de_eps_no_se_rederiva():
    hechos = empresa(anios=(2025,))
    q4_acciones = fact("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", "2025-10-01", "2025-12-31",
                       1_234_000_000, "2026-02-15", "10-K")
    q4_eps = fact("EarningsPerShareDiluted", "USD/shares", "2025-10-01", "2025-12-31", 0.159, "2026-02-15", "10-K")
    rec = por_fecha(ok(hechos + [q4_acciones, q4_eps]).income_q)["2025-12-31"]
    assert rec["weightedAverageShsOutDil"] == 1_234_000_000
    assert rec["epsDiluted"] == pytest.approx(0.159)


def test_fdg_ca11_balance_anual_con_identidad_rota_se_descarta_en_la_serie_anual():
    mala = modificar(empresa(), "Liabilities", hasta="2024-12-31", val=_valores(2024, {}, (1, 1))["liab"][3] * 2)
    pkg = ok(mala)
    assert [r["date"] for r in pkg.balance_a] == ["2025-12-31", "2023-12-31"]
    assert "2024-12-31" not in [r["date"] for r in pkg.balance_q]

