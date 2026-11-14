"""Correcciones tras la validacion de security/QA de F2b (SDD_fuentes_datos_gratuitas.md):
M-1 (FCF TTM fabricado por None->0 con fuente SEC, T-CS-30), M-3 (`from None` en peers),
M-4 (ejemplo de User-Agent sin dominio real), aviso de deuda ausente con fuente FMP y deadline
global de la fase de peers. Sin red ni tiempo reales."""

from __future__ import annotations

import dataclasses

import pytest

from investbot import edgar_provider, finnhub_client, query_handler, rules, summary
from investbot import ai_rewrite
from investbot.edgar_xbrl import parse_company_facts
from investbot.fundamentals import FundamentalsService

from .edgar_helpers import companyfacts
from .fdg_f1b3_helpers import Fake
from .fdg_f2b_helpers import World, build_clients
from .test_edgar_xbrl import CIK, HOY, empresa
from .test_fdg_f1b3_edgar_provider import _reset_global_map  # noqa: F401
from .test_fdg_f2b_ca28_integracion import _analizar, _espiar
from .test_fdg_f2b_ca28_integracion import pytestmark  # noqa: F401


def _sin_capex_6m(anio="2025"):
    return [
        h for h in empresa()
        if not (h[1] == "PaymentsToAcquirePropertyPlantAndEquipment" and h[3]["end"] == f"{anio}-06-30")
    ]


def _paquete(hechos):
    salida = parse_company_facts(companyfacts(*hechos, cik=CIK), quarters=12, today=HOY, expected_cik=str(CIK))
    assert salida.code is None
    return salida.result


# --- build_fcf_historial -------------------------------------------------------------------------------------------


FILAS = [  # mas reciente primero
    {"operatingCashFlow": 400, "capitalExpenditure": -50},
    {"operatingCashFlow": 300, "capitalExpenditure": None},
    {"operatingCashFlow": None, "capitalExpenditure": -30},
    {"operatingCashFlow": 100, "capitalExpenditure": -10},
]


def test_fdg_tcs30_build_fcf_historial_no_estricto_reproduce_el_comportamiento_historico():
    historico = [(s.get("operatingCashFlow") or 0) - abs(s.get("capitalExpenditure") or 0) for s in reversed(FILAS)]
    assert rules.build_fcf_historial(FILAS, strict=False) == historico == [90, -30, 300, 350]
    assert rules.build_fcf_historial([], strict=False) == []


def test_fdg_tcs30_build_fcf_historial_estricto_corta_en_la_primera_fila_incompleta_desde_la_mas_reciente():
    assert rules.build_fcf_historial(FILAS, strict=True) == [350]
    assert rules.build_fcf_historial(FILAS[1:], strict=True) == []  # la mas reciente ya esta incompleta
    completas = [{"operatingCashFlow": 5, "capitalExpenditure": -1}, {"operatingCashFlow": 7, "capitalExpenditure": 2}]
    assert rules.build_fcf_historial(completas, strict=True) == [5, 4]  # antiguo -> reciente
    cero = [{"operatingCashFlow": 5, "capitalExpenditure": 0}]
    assert rules.build_fcf_historial(cero, strict=True) == [5]  # un 0 REPORTADO es valido
    assert rules.build_fcf_historial([], strict=True) == []


def test_fdg_tcs30_el_hecho_6m_de_capex_ausente_ya_no_fabrica_un_fcf_ttm_de_1358m():
    base = _paquete(empresa())
    roto = _paquete(_sin_capex_6m())
    assert rules.calculate_fcf_ttm(rules.build_fcf_historial(base.cash_q, strict=True)) == 1_258_000_000
    assert rules.calculate_fcf_ttm(rules.build_fcf_historial(roto.cash_q, strict=False)) == 1_358_000_000  # el bug
    assert rules.calculate_fcf_ttm(rules.build_fcf_historial(roto.cash_q, strict=True)) is None


def test_fdg_tcs30_el_proveedor_edgar_marca_el_flujo_de_caja_como_anual_fallback():
    assert edgar_provider._to_result(_paquete(empresa())).cash_fuente == rules.DATOS_FUENTE_TRIMESTRAL
    res = edgar_provider._to_result(_paquete(_sin_capex_6m()))
    assert res.cash_fuente == rules.DATOS_FUENTE_ANUAL_FALLBACK


async def test_fdg_tcs30_extremo_a_extremo_fcf_ttm_none_y_cash_fuente_anual_fallback(monkeypatch):
    world = World(fmp="down")
    world.sec_server.facts["0001234567"] = companyfacts(*_sin_capex_6m(), cik=CIK)
    cap = _espiar(monkeypatch)
    vistos: list[dict] = []
    real = query_handler.valuation.compute_valuation_scenarios

    def espia(**kw):
        vistos.append(kw)
        return real(**kw)

    monkeypatch.setattr(query_handler.valuation, "compute_valuation_scenarios", espia)
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    assert cap["source"] == "sec_edgar" and cap["cash_flow_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK
    assert vistos[0]["fcf_base"] is None
    assert "No pude obtener suficientes datos" not in "\n".join(parts)


def _resultado_edgar_forzado(cash_q, cash_a, fuente):
    base = edgar_provider._to_result(_paquete(empresa()))
    return dataclasses.replace(base, cash_q=cash_q, cash_a=cash_a, cash_fuente=fuente)


async def _con_resultado(res, monkeypatch):
    world = World(fmp="down")
    clients, _ = build_clients(world)
    clients = dataclasses.replace(clients, fundamentals=FundamentalsService([Fake("sec_edgar", res, cacheable=False)]))
    vistos: list[dict] = []
    real = query_handler.valuation.compute_valuation_scenarios

    def espia(**kw):
        vistos.append(kw)
        return real(**kw)

    monkeypatch.setattr(query_handler.valuation, "compute_valuation_scenarios", espia)
    await _analizar(clients)
    return vistos[0]


async def test_fdg_tcs30_query_handler_trimestral_con_fuente_sec_no_convierte_un_faltante_en_cero(monkeypatch):
    """Serie trimestral con CapEx None forzada como `trimestral_real`: el FCF TTM no se fabrica."""
    roto = _paquete(_sin_capex_6m())
    res = _resultado_edgar_forzado(roto.cash_q, roto.cash_a, rules.DATOS_FUENTE_TRIMESTRAL)
    kw = await _con_resultado(res, monkeypatch)
    assert kw["fcf_base"] is None  # con strict=False seria 1.358 M


async def test_fdg_tcs30_query_handler_anual_con_fuente_sec_no_convierte_un_faltante_en_cero(monkeypatch):
    anual = [dict(f) for f in _paquete(empresa()).cash_a]
    anual[0]["capitalExpenditure"] = None
    res = _resultado_edgar_forzado([], anual, rules.DATOS_FUENTE_ANUAL_FALLBACK)
    kw = await _con_resultado(res, monkeypatch)
    esperado = rules.build_fcf_historial(anual, strict=True)
    assert kw["fcf_historial"] == esperado and len(esperado) < len(anual)


async def test_fdg_tcs30_con_fuente_fmp_el_comportamiento_historico_se_conserva(monkeypatch):
    anual = [dict(f) for f in _paquete(empresa()).cash_a]
    anual[0]["capitalExpenditure"] = None
    base = edgar_provider._to_result(_paquete(empresa()))
    res = dataclasses.replace(base, source="fmp", cash_q=[], cash_a=anual, cash_fuente=rules.DATOS_FUENTE_ANUAL_FALLBACK)
    world = World(fmp="down")
    clients, _ = build_clients(world)
    clients = dataclasses.replace(clients, fundamentals=FundamentalsService([Fake("fmp", res, cacheable=False)]))
    vistos = []
    real = query_handler.valuation.compute_valuation_scenarios
    monkeypatch.setattr(query_handler.valuation, "compute_valuation_scenarios",
                        lambda **kw: (vistos.append(kw), real(**kw))[1])
    await _analizar(clients)
    assert kw_len(vistos) == len(anual)  # nada truncado: `or 0` como siempre


def kw_len(vistos):
    return len(vistos[0]["fcf_historial"])


# --- M-3: `from None` en la ruta de JSON roto de get_peers -------------------------------------------------------------


async def test_fdg_tcs19_get_peers_con_json_roto_suprime_el_contexto_de_la_excepcion():
    import httpx

    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"not json")))
    with pytest.raises(finnhub_client.FinnhubError) as exc:
        await finnhub_client.get_peers(http, "k" * 20, "ADBE")
    assert exc.value.__suppress_context__ is True
    await http.aclose()


# --- Aviso de deuda ausente con fuente FMP ---------------------------------------------------------------------------------

AVISO_FMP = "No pude determinar la deuda financiera de {ticker} con los datos de FMP; el costo de capital queda como no disponible (no asumo deuda cero)."


def _trans(**kw):
    base = dict(peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
                cash_flow_fuente=None, ticker="MELI")
    base.update(kw)
    return summary.build_transparency_section(**base)


def test_fdg_tcs25_el_aviso_de_deuda_ausente_con_fmp_tiene_el_texto_exacto_y_es_linea_protegida():
    assert summary.AVISO_DEUDA_AUSENTE_FMP.format(ticker="MELI") == AVISO_FMP.format(ticker="MELI")
    for ticker in ("A", "BRK-B", "?"):
        sustituida, _ = ai_rewrite._classify_lines("_" + summary.AVISO_DEUDA_AUSENTE_FMP.format(ticker=ticker) + "_")
        assert sustituida.startswith("⟦PH")
    assert "\n" not in summary.AVISO_DEUDA_AUSENTE_FMP


def test_fdg_tcs25_el_aviso_con_fmp_solo_sale_con_fuente_no_sec_y_el_default_reproduce_el_texto_de_siempre():
    con = _trans(source="fmp", deuda_ausente_fmp=True)
    assert f"_{AVISO_FMP.format(ticker='MELI')}_" in con.split("\n\n")
    assert summary.AVISO_DEUDA_AUSENTE_FMP.split("{")[0] not in _trans(source="fmp")  # default: nada nuevo
    assert _trans(source="fmp") == _trans(source="fmp", deuda_ausente_fmp=False)
    sec = _trans(source="sec_edgar", deuda_ausente_fmp=True, deuda_ausente=True)
    assert "con los datos de FMP; el costo" not in sec and summary.AVISO_DEUDA_AUSENTE.format(ticker="MELI") in sec


def _fmp_con_deuda_none(corto=None, largo=None):
    base = edgar_provider._to_result(_paquete(empresa()))
    balance = [dict(f, shortTermDebt=corto, longTermDebt=largo) for f in base.balance_q]
    return dataclasses.replace(base, source="fmp", balance_q=balance)


async def _texto_con(res, monkeypatch=None):
    world = World(fmp="down")
    clients, _ = build_clients(world)
    clients = dataclasses.replace(clients, fundamentals=FundamentalsService([Fake("fmp", res, cacheable=False)]))
    parts, _ = await _analizar(clients)
    return "\n".join(parts)


async def test_fdg_tcs25_con_fuente_fmp_y_deuda_none_el_mensaje_explica_por_que_no_hay_dcf():
    texto = await _texto_con(_fmp_con_deuda_none())
    assert f"_{AVISO_FMP.format(ticker='MELI')}_" in texto.split("\n")
    assert "no asumo deuda cero" in texto


@pytest.mark.parametrize("corto, largo", [(0, 0), (10, 20)])
async def test_fdg_tcs25_con_fuente_fmp_y_deuda_conocida_no_hay_aviso(corto, largo):
    texto = await _texto_con(_fmp_con_deuda_none(corto, largo))
    assert "con los datos de FMP; el costo de capital" not in texto


# --- Deadline global de la fase de peers ------------------------------------------------------------------------------------


class _Reloj:
    """Cada lectura avanza `paso` segundos: determinista y sin tiempo real."""

    def __init__(self, paso):
        self.t, self.paso = 0.0, paso

    def __call__(self):
        self.t += self.paso
        return self.t


async def test_fdg_ca29_superado_el_deadline_global_los_peers_restantes_van_directo_a_fmp(monkeypatch):
    world = World(fmp="ok")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    clients = dataclasses.replace(clients, now=_Reloj(paso=8.0))  # inicio=8; peer1: 16-8=8 ok; peer2: 24-8=16 > 15
    await _analizar(clients)
    pc = cap["peer_comparison"]
    edgar_facts = [p for p in world.paths("data.sec.gov") if "/companyfacts/" in p and p != "/api/xbrl/companyfacts/CIK0001234567.json"]
    assert edgar_facts == ["/api/xbrl/companyfacts/CIK0002000001.json"]  # solo el primer peer bajo companyfacts
    assert world.paths("financialmodelingprep.com").count("/stable/key-metrics") == 2  # los otros 2, a FMP
    assert pc["peers_pe"]["PEERB"] == pytest.approx(20.0) and pc["peers_pe"]["PEERC"] == pytest.approx(20.0)
    assert pc["peers_pe"]["PEERA"] != pytest.approx(20.0)


async def test_fdg_ca29_superado_el_deadline_con_fmp_caido_los_peers_son_sin_dato_y_sin_error(monkeypatch):
    world = World(fmp="down")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    clients = dataclasses.replace(clients, now=_Reloj(paso=100.0))  # ya vencido antes del primer peer
    parts, _ = await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["peers_usados"] == [] and set(pc["peers_no_usados"]) == {"PEERA", "PEERB", "PEERC"}
    assert all(m == "sin_dato" for m in pc["peers_no_usados"].values())
    assert [p for p in world.paths("data.sec.gov") if "/companyfacts/CIK000200" in p] == []
    assert parts and "Veredicto" in "\n".join(parts)


async def test_fdg_ca29_con_el_reloj_real_por_defecto_el_deadline_no_corta_una_consulta_normal(monkeypatch):
    world = World(fmp="down")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    assert clients.now.__name__ == "monotonic"
    await _analizar(clients)
    assert len(cap["peer_comparison"]["peers_usados"]) == 3
