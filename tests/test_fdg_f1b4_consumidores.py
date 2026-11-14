"""F1b-4 de SDD_fuentes_datos_gratuitas.md: consumidores de la capa de proveedores, de punta a punta
(`fetch_and_analyze_parts`, `/avanzado`) con `httpx.MockTransport`, empresas SINTETICAS y sin red real.

CA-17, 18, 19, 19b, 20, 21, 22, 24 y T-CS-7. Sin tiempo real (reloj inyectado).
"""

from __future__ import annotations

import copy
import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investbot import advanced_command, ai_explain, ai_explain_content, edgar_provider, fmp_provider
from investbot import fundamentals, query_handler, rules
from investbot import sec_edgar_client as sec
from investbot.fundamentals import FundamentalsResult, FundamentalsService

from .edgar_helpers import companyfacts
from .fdg_f1b3_helpers import Clock, Fake, never_sleep
from .test_edgar_xbrl import CIK, HOY, empresa
from .test_f1_redaccion import _CLAVES_STR, _claves_str
from .test_fdg_f1b3_edgar_provider import CIK10, UA, Server, cliente, seed
from .test_query_handler import _adobe_router, _fred_handler

pytestmark = pytest.mark.filterwarnings("ignore::telegram.warnings.PTBUserWarning")

FMP_PROPIOS = ("/stable/income-statement", "/stable/cash-flow-statement",
               "/stable/balance-sheet-statement", "/stable/key-metrics")


@pytest.fixture(autouse=True)
def _reset_mapa():
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None
    yield
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None


def _clients(fmp_handler, service) -> query_handler.Clients:
    return query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(fmp_handler)),
        fred_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"observations": []}))),
        treasury_gov_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        fmp_api_key="k", fred_api_key="f", fundamentals=service,
    )


def _con_fred(adobe_fixtures, fmp_handler, service):
    c = _clients(fmp_handler, service)
    return dataclasses.replace(
        c, fred_http=httpx.AsyncClient(transport=httpx.MockTransport(_fred_handler(adobe_fixtures)))
    )


def _cadena_edgar(fmp_handler, server, fmp_http):
    http, _ = cliente(server)
    clock = Clock()
    return FundamentalsService(
        [edgar_provider.EdgarProvider(http, UA, today=lambda: HOY),
         fmp_provider.FmpProvider(fmp_http, "k")],
        now=clock.now, sleep=never_sleep,
    )


def _entorno(adobe_fixtures, server, *, handler=None, ticker="SYNT"):
    base = handler or _adobe_router(adobe_fixtures)
    fmp_http = httpx.AsyncClient(transport=httpx.MockTransport(base))
    svc = _cadena_edgar(base, server, fmp_http)
    return dataclasses.replace(_con_fred(adobe_fixtures, base, svc), fmp_http=fmp_http)


def _srv(entity: str = "Sintetica SA", **kw) -> Server:
    s = Server()
    s.facts[CIK10] = companyfacts(*empresa(**kw), cik=CIK, entity=entity)
    seed(SYNT=CIK10)
    return s


def _captura(monkeypatch):
    cap: dict = {}
    real = query_handler.summary.build_summary_parts

    def espia(**kwargs):
        cap.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(query_handler.summary, "build_summary_parts", espia)
    return cap


def _espia_wacc(monkeypatch):
    vistos: list[dict] = []
    real = query_handler.valuation.compute_valuation_scenarios

    def envoltura(**kwargs):
        vistos.append(dict(kwargs["wacc_inputs"]))
        return real(**kwargs)

    monkeypatch.setattr(query_handler.valuation, "compute_valuation_scenarios", envoltura)
    return vistos


def _excluidos(cap):
    return {(e["modelo"], e["motivo"]) for e in cap["scenarios"]["conservador"]["modelos_excluidos"]}


def _anual_edgar(adobe_fixtures, *, interes="igual", corto="igual", largo="igual", source="sec_edgar") -> FundamentalsResult:
    inc = copy.deepcopy(adobe_fixtures["income_statement"])
    bal = copy.deepcopy(adobe_fixtures["balance_sheet"])
    for fila in inc:
        if interes != "igual":
            fila["interestExpense"] = interes
    for fila in bal:
        if corto != "igual":
            fila["shortTermDebt"] = corto
        if largo != "igual":
            fila["longTermDebt"] = largo
    return FundamentalsResult(
        source=source, income_q=[], income_a=inc, balance_q=[], balance_a=bal, cash_q=[],
        cash_a=copy.deepcopy(adobe_fixtures["cash_flow"]), own_metrics=None,
    )


def _con_resultado(adobe_fixtures, resultado):
    svc = FundamentalsService([Fake(resultado.source, resultado, cacheable=False)])
    return _con_fred(adobe_fixtures, _adobe_router(adobe_fixtures), svc)


# --- CA-19: AAPL de punta a punta, rama trimestral ------------------------------------

async def test_fdg_ca19_aapl_sin_intereses_conserva_el_eps_ttm_real_y_excluye_el_dcf(adobe_fixtures, monkeypatch):
    cap, wacc = _captura(monkeypatch), _espia_wacc(monkeypatch)
    clients = _entorno(adobe_fixtures, _srv(omitir=("InterestExpense",)))
    parts = await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert cap["source"] == "sec_edgar" and cap["income_statement_fuente"] == rules.DATOS_FUENTE_TRIMESTRAL
    assert wacc[0]["interest_expense"] is None and wacc[0]["total_debt"] == 550_000_000
    assert ("dcf", "wacc_no_calculable") in _excluidos(cap)
    assert cap["scenarios"]["conservador"]["dcf_wacc"] is None
    assert cap["ratios"]["per"] is not None  # EPS TTM real, no cae al fallback anual
    assert cap["intereses_ausentes"] is True and cap["deuda_ausente"] is False
    assert any("no reporta gasto por intereses" in p for p in parts)


# --- CA-19b: rama de respaldo anual (mata `or 0.0` en query_handler) -----------------------

async def test_fdg_ca19b_respaldo_anual_con_intereses_none_wacc_none_sin_kd_cero(adobe_fixtures, monkeypatch):
    cap, wacc = _captura(monkeypatch), _espia_wacc(monkeypatch)
    res = _anual_edgar(adobe_fixtures, interes=None)
    clients = _con_resultado(adobe_fixtures, res)
    parts = await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert cap["income_statement_fuente"] == rules.DATOS_FUENTE_ANUAL_FALLBACK
    assert res.income_statements[0]["interestExpense"] is None
    assert wacc[0]["interest_expense"] is None
    assert query_handler.valuation.calculate_wacc(y=0.04, **wacc[0]) is None
    assert ("dcf", "wacc_no_calculable") in _excluidos(cap)
    assert parts and "No pude obtener suficientes datos" not in parts[0]


async def test_fdg_ca19b_un_cero_reportado_en_intereses_sigue_siendo_valido(adobe_fixtures, monkeypatch):
    cap, wacc = _captura(monkeypatch), _espia_wacc(monkeypatch)
    clients = _con_resultado(adobe_fixtures, _anual_edgar(adobe_fixtures, interes=0))
    await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert wacc[0]["interest_expense"] == 0 and cap["intereses_ausentes"] is False
    assert ("dcf", "wacc_no_calculable") not in _excluidos(cap)


# --- CA-20 / M-9: deuda ausente ----------------------------------------------------------

@pytest.mark.parametrize("corto, largo", [(None, "igual"), ("igual", None)])
async def test_fdg_ca20_deuda_con_un_componente_none_es_total_none_wacc_none_y_dcf_excluido(
    adobe_fixtures, monkeypatch, corto, largo
):
    cap, wacc = _captura(monkeypatch), _espia_wacc(monkeypatch)
    clients = _con_resultado(adobe_fixtures, _anual_edgar(adobe_fixtures, corto=corto, largo=largo))
    await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert wacc[0]["total_debt"] is None
    assert ("dcf", "wacc_no_calculable") in _excluidos(cap)
    assert cap["deuda_ausente"] is True and cap["intereses_ausentes"] is False


async def test_fdg_ca20_un_componente_de_deuda_en_cero_reportado_es_cero_valido(adobe_fixtures, monkeypatch):
    cap, wacc = _captura(monkeypatch), _espia_wacc(monkeypatch)
    clients = _con_resultado(adobe_fixtures, _anual_edgar(adobe_fixtures, corto=0, largo=0))
    await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert wacc[0]["total_debt"] == 0 and cap["deuda_ausente"] is False
    assert ("dcf", "wacc_no_calculable") not in _excluidos(cap)


async def test_fdg_ca20_deuda_ausente_extremo_a_extremo_con_edgar_real_nd_en_ratios_y_aviso(adobe_fixtures, monkeypatch):
    cap = _captura(monkeypatch)
    clients = _entorno(adobe_fixtures, _srv(omitir=("DebtCurrent", "LongTermDebtNoncurrent")))
    parts = await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    assert cap["extras"]["debt_to_equity"] is None and cap["extras"]["net_debt_to_ebitda"] is None
    assert cap["deuda_ausente"] is True and ("dcf", "wacc_no_calculable") in _excluidos(cap)
    texto = "\n".join(parts)
    assert "Deuda/Patrimonio" not in texto and "no asumo deuda cero" in texto


# --- CA-17: regresion del 402 ----------------------------------------------------------------

def _402(adobe_fixtures):
    base = _adobe_router(adobe_fixtures)

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path in FMP_PROPIOS and r.url.params.get("symbol") == "SYNT":
            return httpx.Response(402, text="Payment Required")
        return base(r)

    return handler


async def test_fdg_ca17_con_fmp_en_402_y_edgar_sirviendo_el_analisis_sale_completo(adobe_fixtures, monkeypatch):
    cap = _captura(monkeypatch)
    clients = _entorno(adobe_fixtures, _srv(), handler=_402(adobe_fixtures))
    parts = await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    texto = "\n".join(parts)
    assert "No pude obtener suficientes datos" not in texto
    assert cap["source"] == "sec_edgar" and cap["ratios"]["per"] is not None and cap["ratios"]["ps"] is not None
    sc = cap["scenarios"]["conservador"]
    assert sc["valor_justo_graham"] is not None and sc["valor_justo_dcf"] is not None
    assert "SEC EDGAR" in texto and "obtenidos de la SEC EDGAR" in texto


async def test_fdg_ca17_control_negativo_sin_edgar_con_fmp_en_402_el_analisis_falla_como_siempre(adobe_fixtures):
    h = _402(adobe_fixtures)
    svc = FundamentalsService([fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(h)), "k")])
    with pytest.raises(Exception):
        await query_handler.fetch_and_analyze_parts("SYNT", _con_fred(adobe_fixtures, h, svc), "moderado")


# --- CA-18: sin cobertura -> FMP como hoy + nota fija ------------------------------------------

async def _texto(clients, ticker):
    return "\n\n".join(await query_handler.fetch_and_analyze_parts(ticker, clients, "moderado"))


async def test_fdg_ca18_sin_cik_cae_a_fmp_identico_al_de_siempre_mas_la_nota_fija(adobe_fixtures):
    base = _adobe_router(adobe_fixtures)
    solo_fmp = FundamentalsService([fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(base)), "k")])
    esperado = await _texto(_con_fred(adobe_fixtures, base, solo_fmp), "NVDA")
    seed(OTRO="0000000002")
    srv = Server()
    obtenido = await _texto(_entorno(adobe_fixtures, srv), "NVDA")
    nota = ("_No encontré reportes de NVDA en la SEC (empresa fuera de EEUU, sin 10-K reciente o tipo de "
            "empresa no soportado); usé FMP como respaldo._")
    assert nota in obtenido and srv.requests == []
    assert obtenido.replace(nota + "\n\n", "") == esperado
    assert "obtenidos de la SEC EDGAR" not in esperado


@pytest.mark.parametrize(
    "doc, nota",
    [
        (lambda: companyfacts(*empresa(omitir=("CostOfRevenue", "GrossProfit")), cik=CIK), "No encontré reportes de NVDA"),
        (lambda: companyfacts(*empresa(), cik=CIK + 1), "No pude consultar los reportes de NVDA"),
    ],
)
async def test_fdg_ca18_datos_insuficientes_y_documento_ajeno_usan_la_plantilla_que_corresponde(adobe_fixtures, doc, nota):
    srv = Server()
    srv.facts[CIK10] = doc()
    seed(NVDA=CIK10)
    texto = await _texto(_entorno(adobe_fixtures, srv), "NVDA")
    assert nota in texto and "usé FMP como respaldo" in texto


# --- CA-22: FMP gasta 4 llamadas menos ----------------------------------------------------------

def _contador(adobe_fixtures):
    base = _adobe_router(adobe_fixtures)
    visto: list[tuple[str, str]] = []

    def handler(r: httpx.Request) -> httpx.Response:
        visto.append((r.url.path, r.url.params.get("symbol")))
        return base(r)

    return handler, visto


def _propias(visto, ticker):
    return [p for p, s in visto if p in FMP_PROPIOS and s == ticker]


async def test_fdg_ca22_con_edgar_sirviendo_las_llamadas_propias_a_fmp_bajan_en_4(adobe_fixtures):
    h1, v1 = _contador(adobe_fixtures)
    svc = FundamentalsService([fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(h1)), "k")])
    await _texto(_con_fred(adobe_fixtures, h1, svc), "NVDA")
    h2, v2 = _contador(adobe_fixtures)
    srv = Server()
    srv.facts[CIK10] = companyfacts(*empresa(), cik=CIK)
    seed(NVDA=CIK10)
    await _texto(_entorno(adobe_fixtures, srv, handler=h2), "NVDA")
    assert len(_propias(v1, "NVDA")) == 4 and _propias(v2, "NVDA") == []
    assert [p for p, s in v2 if p == "/stable/quote" and s == "NVDA"] == ["/stable/quote"]
    assert [p for p, s in v2 if p == "/stable/profile"] == ["/stable/profile"]


# --- CA-24: beta ausente declarada ------------------------------------------------------------------

@pytest.mark.parametrize("beta, declara", [(None, True), (0, True), (1.3, False)])
async def test_fdg_ca24_si_falta_la_beta_y_se_uso_1_0_el_mensaje_lo_declara(adobe_fixtures, beta, declara):
    base = _adobe_router(adobe_fixtures)

    def handler(r):
        if r.url.path == "/stable/profile":
            perfil = dict(adobe_fixtures["profile"][0] if isinstance(adobe_fixtures["profile"], list) else adobe_fixtures["profile"])
            perfil["beta"] = beta
            return httpx.Response(200, json=[perfil])
        return base(r)

    svc = FundamentalsService([fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "k")])
    texto = await _texto(_con_fred(adobe_fixtures, handler, svc), "ADBE")
    assert ("usé beta 1.0" in texto) is declara


# --- CA-21: /avanzado ----------------------------------------------------------------------------------

def _avanzado(adobe_fixtures, srv, perfil, log):
    def handler(r):
        log.append((r.url.path, r.url.params.get("symbol")))
        if r.url.path == "/stable/profile":
            return httpx.Response(200, json=[perfil])
        if r.url.path == "/stable/quote":
            return httpx.Response(200, json=adobe_fixtures["quote"])
        if r.url.path == "/stable/income-statement":
            return httpx.Response(200, json=adobe_fixtures["income_statement"][:2])
        if r.url.path == "/stable/balance-sheet-statement":
            return httpx.Response(200, json=adobe_fixtures["balance_sheet"][:2])
        if r.url.path == "/stable/cash-flow-statement":
            return httpx.Response(200, json=adobe_fixtures["cash_flow"][:2])
        return httpx.Response(404)

    fmp_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = _cadena_edgar(handler, srv, fmp_http)
    return dataclasses.replace(_clients(handler, svc), fmp_http=fmp_http)


async def _correr(clients, ticker):
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()),
                             effective_chat=SimpleNamespace(id=7, type="private"))
    cb = advanced_command.build_advanced_command_handler(
        clients, SimpleNamespace(allow=lambda k: True)).callback
    await cb(update, SimpleNamespace(args=[ticker]))
    return "\n".join(c.args[0] for c in update.message.reply_text.await_args_list)


def _perfil(**kw):
    p = {"symbol": "SYNT", "companyName": "Sintetica", "sector": "Industrials", "industry": "Machinery",
         "beta": 1.0, "isEtf": False, "isFund": False, "isActivelyTrading": True}
    p.update(kw)
    return p


async def test_fdg_ca21_avanzado_con_edgar_toma_los_3_estados_anuales_de_la_sec(adobe_fixtures):
    log: list = []
    clients = _avanzado(adobe_fixtures, _srv(), _perfil(), log)
    texto = await _correr(clients, "SYNT")
    assert "Fuente de los datos: SEC EDGAR" in texto and "FMP (plan gratuito)" not in texto
    pedidos = [p for p, _ in log]
    assert not any(p in pedidos for p in FMP_PROPIOS[:3])  # ningun estado se pidio a FMP
    assert "Altman Z-Score" in texto and "Magic Formula" in texto


async def test_fdg_ca21_avanzado_con_jpm_sin_cobertura_cae_a_fmp_como_siempre(adobe_fixtures):
    log: list = []
    srv = Server()
    srv.facts[CIK10] = companyfacts(*empresa(omitir=("CostOfRevenue", "GrossProfit")), cik=CIK)
    seed(SYNT=CIK10)
    texto = await _correr(_avanzado(adobe_fixtures, srv, _perfil(), log), "SYNT")
    assert "Fuente de los datos: FMP (plan gratuito)" in texto
    assert [p for p, _ in log].count("/stable/income-statement") == 1


async def test_fdg_ca21_avanzado_etf_sigue_rechazando_por_isetf_sin_tocar_edgar(adobe_fixtures):
    log: list = []
    srv = _srv()
    texto = await _correr(_avanzado(adobe_fixtures, srv, _perfil(isEtf=True), log), "SYNT")
    assert texto == advanced_command.NOT_APPLICABLE_MSG and srv.requests == []


async def test_fdg_ca21_avanzado_etf_con_404_en_edgar_cae_a_fmp_y_la_red_de_seguridad_rechaza(adobe_fixtures):
    log: list = []
    seed(SYNT=CIK10)  # CIK en el mapa pero companyfacts 404 (ETF)
    clients = _avanzado(adobe_fixtures, Server(), _perfil(), log)

    def vacio(r):
        log.append((r.url.path, r.url.params.get("symbol")))
        if r.url.path == "/stable/profile":
            return httpx.Response(200, json=[_perfil()])
        if r.url.path == "/stable/quote":
            return httpx.Response(200, json=adobe_fixtures["quote"])
        return httpx.Response(200, json=[])

    clients = dataclasses.replace(clients, fmp_http=httpx.AsyncClient(transport=httpx.MockTransport(vacio)))
    assert await _correr(clients, "SYNT") == advanced_command.NOT_APPLICABLE_MSG


async def test_fdg_ca21_avanzado_un_fallo_inesperado_de_edgar_no_rompe_el_comando(adobe_fixtures):
    log: list = []
    clients = _avanzado(adobe_fixtures, _srv(), _perfil(), log)

    class Roto:
        async def get(self, *a, **k):
            raise RuntimeError("boom")

    clients = dataclasses.replace(clients, fundamentals=Roto())
    assert "Fuente de los datos: FMP (plan gratuito)" in await _correr(clients, "SYNT")


async def test_fdg_ca21_avanzado_sin_servicio_de_fundamentales_es_el_camino_de_siempre(adobe_fixtures):
    log: list = []
    clients = dataclasses.replace(_avanzado(adobe_fixtures, _srv(), _perfil(), log), fundamentals=None)
    assert "Fuente de los datos: FMP (plan gratuito)" in await _correr(clients, "SYNT")


async def test_fdg_ca21_avanzado_con_estado_anual_vacio_de_edgar_cae_a_fmp(adobe_fixtures):
    res = dataclasses.replace(_anual_edgar(adobe_fixtures), cash_a=[])
    log: list = []
    clients = dataclasses.replace(
        _avanzado(adobe_fixtures, _srv(), _perfil(), log),
        fundamentals=FundamentalsService([Fake("sec_edgar", res, cacheable=False)]),
    )
    assert "Fuente de los datos: FMP (plan gratuito)" in await _correr(clients, "SYNT")


async def test_fdg_ca21_el_servicio_con_only_no_consulta_a_fmp_ni_loguea_si_no_sirve():
    fmp = Fake("fmp", None, cacheable=False)
    svc = FundamentalsService([Fake("sec_edgar", None, cacheable=False), fmp])
    assert await svc.get("X", quarters=20, only=frozenset({"sec_edgar"})) is None
    assert fmp.calls == []


# --- T-CS-7: mismo conjunto de claves al modelo con origen EDGAR y FMP ----------------------------------------

def _ctx_desde_sink(sink):
    campos = {f.name for f in dataclasses.fields(ai_explain.ExplanationContext)}
    return ai_explain.ExplanationContext(
        chat_id=1, kind="texto_libre", ticker="SYNT",
        **{k: v for k, v in sink.items() if k in campos},
    )


def _claves_todas(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _claves_todas(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _claves_todas(v, acc)
    return acc


async def _sink(clients):
    sink: dict = {}
    await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado", explain_context_sink=sink)
    return sink


async def test_fdg_tcs7_el_payload_a_gemini_tiene_las_mismas_claves_con_origen_edgar_y_fmp(adobe_fixtures):
    edgar = await _sink(_entorno(adobe_fixtures, _srv(entity="CANARIO_ENT")))
    base = _adobe_router(adobe_fixtures)
    fmp = await _sink(_con_fred(adobe_fixtures, base, FundamentalsService(
        [fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(base)), "k")])))
    c_edgar, c_fmp = _ctx_desde_sink(edgar), _ctx_desde_sink(fmp)
    codigos = [c for c in ai_explain_content.QUESTIONS_TEXTO_LIBRE if c not in ("evt", "inf")]
    assert codigos
    for code in codigos:
        pe = ai_explain._datos_para_el_modelo(code, ai_explain._build_explain_payload(c_edgar, code))
        pf = ai_explain._datos_para_el_modelo(code, ai_explain._build_explain_payload(c_fmp, code))
        assert _claves_todas(pe) == _claves_todas(pf), code
        assert _claves_str(pe) <= _CLAVES_STR[code], code  # "cuenta" la agrega el handler
        assert "CANARIO_ENT" not in json.dumps(pe, ensure_ascii=False)
