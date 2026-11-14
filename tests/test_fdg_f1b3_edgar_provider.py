"""F1b-3 de SDD_fuentes_datos_gratuitas.md: `EdgarProvider` + `FundamentalsService` reales
sobre `httpx.MockTransport` con empresas SINTETICAS (`tests/test_edgar_xbrl.empresa`).

Cubre la cadena EDGAR -> FMP con cobertura (CA-14), `submissions` (T-CS-15b), cero red para un
ticker inexistente (T-CS-10), memoria y concurrencia (T-CS-16) y el cableado en `bot.py`.
Sin red real, sin tiempo real (`now`/`sleep` inyectados; sincronizacion con `asyncio.Event`).
No hay fixtures reales de la SEC: los fixtures reales son la prueba de aceptacion F1a-2.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import json
import logging
import threading
import time
import tracemalloc
import weakref
from datetime import date, datetime, timezone

import httpx
import pytest

from investbot import bot, edgar_provider, rules, fundamentals, query_handler, sec_edgar_client as sec
from investbot.edgar_provider import EdgarProvider
from investbot.edgar_xbrl import FundamentalsPackage
from investbot.fundamentals import FundamentalsService, ProviderUnavailable
from investbot.sec_edgar_client import SecEdgarError

from .edgar_helpers import companyfacts
from .fdg_f1b3_helpers import Clock, Fake, make_result, never_sleep, spin
from .test_edgar_xbrl import CIK, HOY, empresa
from .test_fdg_f1b2_hardening import UA_VALIDO, _env_main, _spy_main
from .test_query_handler import _make_clients

pytestmark = pytest.mark.filterwarnings("ignore::telegram.warnings.PTBUserWarning")

UA = "InvestBot contacto@ejemplo.test"
MIB = 1024 * 1024
CIK10 = str(CIK).zfill(10)
LOGGER = "investbot.fundamentals"


@pytest.fixture(autouse=True)
def _reset_global_map():
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None
    yield
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None


def seed(**mapa: str) -> None:
    """Mapa ticker->CIK10 fresco (sin red)."""
    sec._ticker_cik_cache.clear()
    sec._ticker_cik_cache.update(mapa)
    sec._cache_loaded_at = datetime.now(timezone.utc)


class Server:
    """Handler de la SEC: `facts[cik10]`/`subs[cik10]` -> payload (dict/bytes/Response)."""

    def __init__(self) -> None:
        self.facts: dict = {}
        self.subs: dict = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        tabla = self.facts if "/companyfacts/" in path else self.subs
        cik10 = path.rsplit("CIK", 1)[1].split(".")[0]
        item = tabla.get(cik10)
        if item is None:
            return httpx.Response(404, content=b"<?xml version='1.0'?><Error/>")
        if isinstance(item, httpx.Response):
            return item
        if isinstance(item, bytes):
            return httpx.Response(200, content=item)
        return httpx.Response(200, json=item)

    def paths(self, fragmento: str) -> list[str]:
        return [r.url.path for r in self.requests if fragmento in r.url.path]


def cliente(server: Server):
    http = httpx.AsyncClient(transport=httpx.MockTransport(server), follow_redirects=False)
    clock = Clock()
    sec.install_gate(http, now=clock.now, sleep=_instant_sleep(clock))
    return http, clock


def _instant_sleep(clock: Clock):
    async def sleep(delay: float) -> None:
        clock.advance(delay)
        await asyncio.sleep(0)

    return sleep


def sintetica(cik: int = CIK, **kw) -> dict:
    return companyfacts(*empresa(**kw), cik=cik)


def proveedor(server: Server, *, ua: str | None = UA) -> tuple[EdgarProvider, httpx.AsyncClient]:
    http, _ = cliente(server)
    return EdgarProvider(http, ua, today=lambda: HOY), http


def cadena(server: Server, *respaldo):
    ed, http = proveedor(server)
    clock = Clock()
    svc = FundamentalsService(
        [ed, *(respaldo or (Fake("fmp", make_result("fmp"), cacheable=False),))],
        now=clock.now,
        sleep=never_sleep,
    )
    return svc, ed, http, clock


# ================= EdgarProvider: camino feliz y adaptador ======================


async def test_fdg_ca14_provider_camino_feliz_entrega_un_paquete_de_sec_edgar_con_ua_inyectado():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    res = await ed.fetch("SYNT", quarters=12)
    assert res.source == "sec_edgar" and res.fallback_codes == ()
    assert len(res.income_q) == 12 and len(res.cash_q) == 12 and res.balance_q
    assert res.income_q[0]["date"] > res.income_q[-1]["date"]  # recent-first
    assert res.own_metrics is not None and "roe" in res.own_metrics
    assert (res.income_fuente, res.cash_fuente, res.balance_fuente) == ("trimestral_real",) * 3
    assert res.income_statements is res.income_q
    assert [r.headers["User-Agent"] for r in srv.requests] == [UA]
    assert srv.paths("companyfacts") == [f"/api/xbrl/companyfacts/CIK{CIK10}.json"]


async def test_fdg_ca14_provider_respeta_la_ventana_de_trimestres_pedida():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    assert len((await ed.fetch("SYNT", quarters=6)).income_q) == 6


async def test_fdg_ca14_provider_sin_gasto_por_intereses_conserva_el_ttm_real_con_intereses_none():
    srv = Server()
    srv.facts[CIK10] = sintetica(omitir=("InterestExpense",))
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    res = await ed.fetch("SYNT", quarters=12)
    assert res.income_q[0]["interestExpense"] is None
    # D-8 / 4.10 pt 4 (F1b-4): `interestExpense` es opcional en el TTM; el EPS TTM real se conserva.
    assert res.income_fuente == "trimestral_real" and res.income_statements is res.income_q and res.income_q
    assert rules.calculate_income_statement_ttm(res.income_q).interest_expense_ttm is None
    assert res.cash_fuente == "trimestral_real"


def test_fdg_ca14_adaptador_marca_anual_cuando_no_hay_trimestres_de_caja_ni_de_balance():
    pkg = FundamentalsPackage(
        income_q=[], income_a=[{"x": 1}], balance_q=[], balance_a=[{"y": 1}],
        cash_q=[], cash_a=[{"z": 1}], own_metrics={},
    )
    res = edgar_provider._to_result(pkg)
    assert (res.income_fuente, res.cash_fuente, res.balance_fuente) == ("anual_fallback",) * 3
    assert res.balance_sheets == [{"y": 1}] and res.cash_flows == [{"z": 1}]


def test_fdg_ca14_today_por_defecto_es_la_fecha_utc_actual():
    assert isinstance(edgar_provider._today_utc(), date)


# ================= EdgarProvider: sin cobertura -> codigo cerrado ================


async def test_fdg_ca14_provider_sin_user_agent_no_hace_peticiones_y_declara_ua_no_configurado():
    srv = Server()
    seed(SYNT=CIK10)
    for ua in (None, ""):
        ed, _ = proveedor(srv, ua=ua)
        with pytest.raises(ProviderUnavailable) as info:
            await ed.fetch("SYNT", quarters=12)
        assert info.value.code == "ua_no_configurado"
    assert srv.requests == []


async def test_fdg_ca14_provider_ticker_sin_cik_declara_sin_cik_sin_ninguna_peticion():
    srv = Server()
    seed(OTRO="0000000002")
    ed, _ = proveedor(srv)
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == "sin_cik" and srv.requests == []


async def test_fdg_ca14_provider_con_el_circuito_abierto_declara_circuito_abierto():
    srv = Server()
    ed, http = proveedor(srv)  # mapa sin cargar: el refresco choca con el breaker
    gate = sec.gate_for(http)
    gate.open_until = gate.now() + 100
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == "circuito_abierto" and srv.requests == []


async def test_fdg_ca14_provider_con_el_circuito_ya_vencido_y_sin_cik_sigue_siendo_sin_cik():
    srv = Server()
    seed(OTRO="0000000002")
    ed, http = proveedor(srv)
    gate = sec.gate_for(http)
    gate.open_until = gate.now() - 1
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == "sin_cik"


@pytest.mark.parametrize(
    "codigo_cliente, esperado",
    [
        ("sin_facts", "sin_facts"),
        ("sin_cik", "sin_cik"),
        ("circuito_abierto", "circuito_abierto"),
        ("respuesta_demasiado_grande", "respuesta_demasiado_grande"),
        ("redireccion_inesperada", "redireccion_inesperada"),
        ("respuesta_inesperada", "respuesta_inesperada"),
        ("bloqueado_por_sec", "bloqueado_por_sec"),
        ("no_disponible_temporal", "no_disponible_temporal"),
        ("codigo_que_no_existe", "no_disponible_temporal"),  # nunca se propaga texto libre
    ],
)
async def test_fdg_ca14_provider_mapea_los_errores_del_cliente_al_enum_cerrado(
    monkeypatch, codigo_cliente, esperado
):
    async def falla(*a, **k):
        raise SecEdgarError("mensaje con https://data.sec.gov/x y el UA", codigo_cliente)

    monkeypatch.setattr(edgar_provider.sec_edgar_client, "get_company_facts", falla)
    seed(SYNT=CIK10)
    ed, _ = proveedor(Server())
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == esperado
    assert "data.sec.gov" not in str(info.value) and info.value.__cause__ is None


@pytest.mark.parametrize(
    "respuesta, esperado",
    [
        (None, "sin_facts"),  # 404 con cuerpo XML (ETF/SPY)
        (httpx.Response(403, content=b"blocked"), "bloqueado_por_sec"),
        (httpx.Response(429), "bloqueado_por_sec"),
        (httpx.Response(500), "no_disponible_temporal"),
        (httpx.Response(302, headers={"location": "https://evil.example/x"}), "redireccion_inesperada"),
        (httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html/>"), "respuesta_inesperada"),
        (httpx.Response(200, content=b"no es json"), "respuesta_inesperada"),
    ],
)
async def test_fdg_ca14_provider_errores_http_reales_del_cliente_llegan_como_codigo_cerrado(respuesta, esperado):
    srv = Server()
    if respuesta is not None:
        srv.facts[CIK10] = respuesta
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == esperado


def _a_ifrs(hechos):
    return [("ifrs-full", tag, unidad, h) for _, tag, unidad, h in hechos]


def _banco():
    """Sin costo de ventas ni utilidad bruta (como un banco): el parser no cubre el tipo de empresa."""
    return empresa(omitir=("CostOfRevenue", "GrossProfit"))


@pytest.mark.parametrize(
    "documento, esperado",
    [
        (lambda: companyfacts(*_banco(), cik=CIK), "datos_insuficientes"),  # JPM / banco
        (lambda: companyfacts(*_a_ifrs(empresa()), cik=CIK), "no_us_gaap"),  # TSM / extranjera
        (lambda: companyfacts(*empresa(), cik=CIK + 1), "cik_no_coincide"),  # documento de otra empresa
    ],
)
async def test_fdg_ca14_provider_banco_extranjera_o_documento_ajeno_degradan_con_el_codigo_del_parser(
    documento, esperado
):
    srv = Server()
    srv.facts[CIK10] = documento()
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == esperado


async def test_fdg_ca14_provider_datos_viejos_declaran_desactualizado():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    seed(SYNT=CIK10)
    http, _ = cliente(srv)
    ed = EdgarProvider(http, UA, today=lambda: date(2030, 1, 1))
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == "desactualizado"


async def test_fdg_ca14_cadena_banco_cae_a_fmp_con_aviso_honesto_y_sin_datos_de_edgar(caplog):
    caplog.set_level(logging.INFO)
    srv = Server()
    srv.facts[CIK10] = companyfacts(*_banco(), cik=CIK)
    seed(JPM=CIK10)
    svc, *_ = cadena(srv)
    res = await svc.get("JPM", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("datos_insuficientes",)
    assert res.own_metrics == {"marca": "x"}
    assert "fundamentals ticker=JPM source=fmp fallback=datos_insuficientes ms=0" in caplog.text


@pytest.mark.parametrize(
    "ticker, mapa, codigo",
    [("TSM", {}, "sin_cik"), ("SPY", {"SPY": CIK10}, "sin_facts")],
)
async def test_fdg_ca14_cadena_extranjera_sin_cik_o_etf_sin_facts_cae_a_fmp(ticker, mapa, codigo):
    srv = Server()  # sin companyfacts publicados: 404
    seed(**mapa, OTRO="0000000002")
    svc, *_ = cadena(srv)
    res = await svc.get(ticker, quarters=12)
    assert res.source == "fmp" and res.fallback_codes == (codigo,)


async def test_fdg_ca14_cadena_exitosa_entrega_edgar_y_la_segunda_consulta_no_pide_nada():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    seed(SYNT=CIK10)
    fp = Fake("fmp", make_result("fmp"), cacheable=False)
    svc, *_ = cadena(srv, fp)
    a = await svc.get("SYNT", quarters=12)
    b = await svc.get("SYNT", quarters=12)
    assert a.source == b.source == "sec_edgar" and fp.calls == []
    assert len(srv.paths("companyfacts")) == 1


# ===================== T-CS-15b: submissions de otro ticker ======================


async def _cargar_submissions(http, cik10=CIK10):
    assert await sec.get_submissions(http, UA, cik10) is not None


async def test_fdg_tcs15b_submissions_con_otro_ticker_degrada_a_fmp_sin_bajar_companyfacts():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    srv.subs[CIK10] = {"cik": CIK10, "tickers": ["OTRA", "OTRA-B"], "name": "Otra SA"}
    seed(SYNT=CIK10)
    svc, ed, http, _ = cadena(srv)
    await _cargar_submissions(http)
    res = await svc.get("SYNT", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("cik_no_coincide",)
    assert srv.paths("companyfacts") == []  # ni siquiera se descarga el documento
    assert res.own_metrics == {"marca": "x"}  # ningun numero de EDGAR


async def test_fdg_tcs15b_sin_submissions_en_cache_nunca_se_pide_solo_para_verificar():
    srv = Server()
    srv.facts[CIK10] = sintetica()
    srv.subs[CIK10] = {"tickers": ["OTRA"]}
    seed(SYNT=CIK10)
    ed, _ = proveedor(srv)
    res = await ed.fetch("SYNT", quarters=12)
    assert res.source == "sec_edgar"
    assert srv.paths("submissions") == []


@pytest.mark.parametrize(
    "ticker, mapa_clave, lista",
    [
        ("SYNT", "SYNT", ["synt"]),  # mayusculas
        ("BRK.B", "BRK-B", ["BRK.B"]),  # punto -> guion
        ("BRK-B", "BRK-B", ["brk-b", "BRK-A"]),
        ("SYNT", "SYNT", [3, None, "SYNT"]),  # entradas no-str se ignoran
    ],
)
async def test_fdg_tcs15b_submissions_que_contiene_el_ticker_normalizado_deja_pasar(ticker, mapa_clave, lista):
    srv = Server()
    srv.facts[CIK10] = sintetica()
    srv.subs[CIK10] = {"tickers": lista}
    seed(**{mapa_clave: CIK10})
    ed, http = proveedor(srv)
    await _cargar_submissions(http)
    assert (await ed.fetch(ticker, quarters=12)).source == "sec_edgar"


@pytest.mark.parametrize("tickers", [[], ["OTRA"], ["SYNTX"], ["SYN"]])
async def test_fdg_tcs15b_lista_vacia_o_sin_coincidencia_exacta_degrada(tickers):
    srv = Server()
    srv.facts[CIK10] = sintetica()
    srv.subs[CIK10] = {"tickers": tickers}
    seed(SYNT=CIK10)
    ed, http = proveedor(srv)
    await _cargar_submissions(http)
    with pytest.raises(ProviderUnavailable) as info:
        await ed.fetch("SYNT", quarters=12)
    assert info.value.code == "cik_no_coincide"


@pytest.mark.parametrize("subs", [{"name": "sin tickers"}, {"tickers": "SYNT"}, {"tickers": None}])
async def test_fdg_tcs15b_submissions_sin_lista_tickers_no_es_verificable_y_no_bloquea(subs):
    srv = Server()
    srv.facts[CIK10] = sintetica()
    srv.subs[CIK10] = subs
    seed(SYNT=CIK10)
    ed, http = proveedor(srv)
    await _cargar_submissions(http)
    assert (await ed.fetch("SYNT", quarters=12)).source == "sec_edgar"


# ===================== T-CS-10 (CS-8): ticker valido inexistente =================


async def test_fdg_tcs10_ticker_valido_inexistente_cero_red_y_negativo_cacheado_24_h(monkeypatch):
    srv = Server()
    seed(OTRO="0000000002")  # mapa fresco, sin el ticker
    svc, ed, http, clock = cadena(srv)
    llamadas = []
    real = sec.get_cik_for_ticker

    async def espia(*a, **k):
        llamadas.append(a[2])
        return await real(*a, **k)

    monkeypatch.setattr(edgar_provider.sec_edgar_client, "get_cik_for_ticker", espia)
    r1 = await svc.get("ZZZZ", quarters=12)
    assert r1.fallback_codes == ("sin_cik",) and srv.requests == []
    clock.advance(24 * 3600 - 1)
    r2 = await svc.get("ZZZZ", quarters=12)
    assert r2.fallback_codes == ("sin_cik",) and srv.requests == [] and llamadas == ["ZZZZ"]
    clock.advance(1)  # vence el negativo: se vuelve a resolver (siempre contra el mapa en memoria)
    await svc.get("ZZZZ", quarters=12)
    assert llamadas == ["ZZZZ", "ZZZZ"] and srv.requests == []


@pytest.mark.parametrize("hostil", ["../x", "AAPL\n", "A@B", "ſpy", "A" * 11, "", "AB CD"])
async def test_fdg_tcs10_tickers_hostiles_no_producen_ninguna_peticion(hostil):
    srv = Server()
    seed(OTRO="0000000002")
    svc, *_ = cadena(srv)
    res = await svc.get(hostil, quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("sin_cik",) and srv.requests == []


# ============================ T-CS-16 (CS-16): memoria ===========================


def _relleno_12_mib() -> bytes:
    """`companyfacts` valido de exactamente 12 MiB armado con bytes repetidos (no con un dict enorme)."""
    doc = sintetica()
    doc["facts"]["us-gaap"]["ZzRelleno"] = {"units": {"USD": ["@@"]}}
    base = json.dumps(doc).encode()
    cabeza, cola = base.split(b'"@@"')
    entrada = (
        b'{"end":"2019-12-31","val":1,"accn":"0000000001-20-000001",'
        b'"fy":2019,"fp":"FY","form":"10-K","filed":"2020-02-01"}'
    )
    n = (12 * MIB - len(cabeza) - len(cola)) // (len(entrada) + 1)
    cuerpo = b",".join([entrada] * n)
    resto = 12 * MIB - len(cabeza) - len(cuerpo) - len(cola)
    return cabeza + cuerpo + b" " * resto + cola


async def test_fdg_tcs16_respuesta_de_12_mib_parseada_y_cacheada_no_supera_100_mb_de_pico_ni_10_s():
    payload = _relleno_12_mib()
    assert len(payload) == 12 * MIB
    srv = Server()
    srv.facts[CIK10] = payload
    seed(SYNT=CIK10)
    svc, *_ = cadena(srv)
    inicio = time.perf_counter()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        res = await svc.get("SYNT", quarters=12)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    duracion = time.perf_counter() - inicio
    assert res.source == "sec_edgar" and len(svc._cache) == 1  # parseada y cacheada
    assert pico <= 100 * 10**6, f"pico {pico / 10**6:.1f} MB"
    assert duracion <= 10, f"{duracion:.1f} s"
    # lo cacheado son unos KB, nunca el documento ni el JSON crudo
    assert len(repr(next(iter(svc._cache.values())))) < 2 * MIB // 2


async def test_fdg_tcs16_un_documento_de_mas_de_12_mib_cae_a_fmp_con_su_codigo():
    srv = Server()
    srv.facts[CIK10] = b" " * (12 * MIB + 1)
    seed(SYNT=CIK10)
    svc, *_ = cadena(srv)
    res = await svc.get("SYNT", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("respuesta_demasiado_grande",)


async def test_fdg_tcs16_70_tickers_distintos_dejan_la_cache_en_64_o_menos():
    ed = Fake("sec_edgar", make_result())
    svc = FundamentalsService([ed, Fake("fmp", make_result("fmp"), cacheable=False)], sleep=never_sleep)
    for i in range(70):
        await svc.get(f"T{i}", quarters=12)
    assert len(ed.calls) == 70 and len(svc._cache) <= 64
    # tambien con veredictos negativos
    neg = Fake("sec_edgar", ProviderUnavailable("sin_cik"))
    svc2 = FundamentalsService([neg, Fake("fmp", make_result("fmp"), cacheable=False)], sleep=never_sleep)
    for i in range(70):
        await svc2.get(f"T{i}", quarters=12)
    assert len(svc2._cache) <= 64


class _Rastreo:
    def __init__(self) -> None:
        self.activas = 0
        self.max_activas = 0
        self.iniciadas = 0
        self.gate = asyncio.Event()


class _Gated(httpx.AsyncByteStream):
    def __init__(self, cuerpo: bytes, rastreo: _Rastreo) -> None:
        self.cuerpo, self.r, self.empezo = cuerpo, rastreo, False

    async def __aiter__(self):
        self.empezo = True
        self.r.iniciadas += 1
        self.r.activas += 1
        self.r.max_activas = max(self.r.max_activas, self.r.activas)
        await self.r.gate.wait()
        yield self.cuerpo

    async def aclose(self) -> None:
        if self.empezo:
            self.r.activas -= 1


class _SinCerrojo:
    """Reemplaza el cerrojo del proveedor: deja solo el semaforo de 1 del cliente SEC."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


@pytest.mark.parametrize("cerrojo_del_proveedor", [True, False], ids=["con_cerrojo", "solo_semaforo_del_cliente"])
async def test_fdg_tcs16_dos_tickers_distintos_nunca_tienen_dos_descargas_activas(cerrojo_del_proveedor):
    rastreo = _Rastreo()
    docs = {
        "0000000001": json.dumps(sintetica(cik=1)).encode(),
        "0000000002": json.dumps(sintetica(cik=2)).encode(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        cik10 = request.url.path.rsplit("CIK", 1)[1].split(".")[0]
        return httpx.Response(200, stream=_Gated(docs[cik10], rastreo))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    sec.install_gate(http, now=Clock().now, sleep=_instant_sleep(Clock()))
    seed(AAA="0000000001", BBB="0000000002")
    edgar = EdgarProvider(http, UA, today=lambda: HOY)
    if not cerrojo_del_proveedor:
        edgar._facts_lock = _SinCerrojo()
    svc = FundamentalsService([edgar, Fake("fmp", make_result("fmp"), cacheable=False)], sleep=never_sleep)
    t1 = asyncio.ensure_future(svc.get("AAA", quarters=12))
    t2 = asyncio.ensure_future(svc.get("BBB", quarters=12))
    await spin(50)
    assert rastreo.iniciadas == 1 and rastreo.activas == 1  # la segunda espera su turno
    rastreo.gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.source == r2.source == "sec_edgar"
    assert rastreo.iniciadas == 2 and rastreo.max_activas == 1


async def test_fdg_tcs16_la_segunda_descarga_espera_tambien_al_parseo_de_la_primera(monkeypatch):
    """El `dict` crudo de la primera sigue vivo mientras se parsea: la segunda no puede bajar otro."""
    loop = asyncio.get_running_loop()
    entro = asyncio.Event()
    soltar = threading.Event()
    real = edgar_provider.parse_company_facts

    def parse_lento(raw, **kw):
        loop.call_soon_threadsafe(entro.set)
        assert soltar.wait(timeout=30)
        return real(raw, **kw)

    monkeypatch.setattr(edgar_provider, "parse_company_facts", parse_lento)
    srv = Server()
    srv.facts["0000000001"] = sintetica(cik=1)
    srv.facts["0000000002"] = sintetica(cik=2)
    seed(AAA="0000000001", BBB="0000000002")
    ed, _ = proveedor(srv)
    t1 = asyncio.ensure_future(ed.fetch("AAA", quarters=12))
    await entro.wait()
    t2 = asyncio.ensure_future(ed.fetch("BBB", quarters=12))
    await spin(50)
    assert srv.paths("companyfacts") == ["/api/xbrl/companyfacts/CIK0000000001.json"]
    soltar.set()
    assert (await t1).source == (await t2).source == "sec_edgar"
    assert len(srv.paths("companyfacts")) == 2


async def test_fdg_tcs16_el_documento_crudo_se_libera_apenas_termina_el_parseo(monkeypatch):
    class Doc(dict):
        pass

    caja = [Doc(sintetica())]
    ref = weakref.ref(caja[0])

    async def entrega(*a, **k):
        return caja.pop()  # tras entregarlo, solo el proveedor lo referencia

    monkeypatch.setattr(edgar_provider.sec_edgar_client, "get_company_facts", entrega)
    original = edgar_provider._to_result
    vivo = []

    def espia(pkg):
        gc.collect()
        vivo.append(ref() is not None)
        return original(pkg)

    monkeypatch.setattr(edgar_provider, "_to_result", espia)
    seed(SYNT=CIK10)
    ed, _ = proveedor(Server())
    res = await ed.fetch("SYNT", quarters=12)
    assert res.source == "sec_edgar" and vivo == [False]


# ======================= Cableado: bot.py y query_handler ========================


def test_fdg_f1b3_bot_con_ua_valido_cablea_edgar_y_fmp_con_el_cliente_sec_compartido(monkeypatch, tmp_path):
    _env_main(monkeypatch, tmp_path, SEC_EDGAR_USER_AGENT=UA_VALIDO)
    captured = _spy_main(monkeypatch)
    bot.main()
    clients = captured["clients"]
    servicio = clients.fundamentals
    assert isinstance(servicio, FundamentalsService)
    assert [p.name for p in servicio._providers] == ["sec_edgar", "fmp"]
    edgar, fmp = servicio._providers
    assert edgar._http is clients.sec_edgar_http and edgar._user_agent == UA_VALIDO
    assert fmp._http is clients.fmp_http
    assert "ana@ejemplo.test" not in repr(clients)


def test_fdg_f1b3_bot_sin_user_agent_no_arma_servicio_y_query_handler_usa_solo_fmp(monkeypatch, tmp_path):
    _env_main(monkeypatch, tmp_path)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["clients"].fundamentals is None


async def test_fdg_f1b3_query_handler_con_edgar_que_no_cubre_cae_a_fmp_y_el_analisis_sale_completo(
    adobe_fixtures, caplog
):
    caplog.set_level(logging.INFO)
    srv = Server()
    seed(OTRO="0000000002")
    http, _ = cliente(srv)
    svc = FundamentalsService(
        [
            EdgarProvider(http, UA, today=lambda: HOY),
            fundamentals_fmp(adobe_fixtures),
        ],
        sleep=never_sleep,
    )
    clients = dataclasses.replace(_make_clients(adobe_fixtures), fundamentals=svc)
    partes = await query_handler.fetch_and_analyze_parts("ADBE", clients, perfil="moderado")
    texto = "\n".join(partes)
    assert "No pude obtener suficientes datos" not in texto and "Adobe" in texto
    assert srv.requests == []
    assert "fundamentals ticker=ADBE source=fmp fallback=sin_cik ms=" in caplog.text


def fundamentals_fmp(adobe_fixtures):
    from investbot.fmp_provider import FmpProvider

    clients = _make_clients(adobe_fixtures)
    return FmpProvider(clients.fmp_http, clients.fmp_api_key)


async def test_fdg_f1b3_query_handler_consume_el_resultado_de_edgar_sin_romper_y_sin_pedir_estados_a_fmp(
    adobe_fixtures, caplog
):
    caplog.set_level(logging.INFO)
    srv = Server()
    srv.facts[CIK10] = sintetica()
    seed(ADBE=CIK10)
    http, _ = cliente(srv)
    clients = _make_clients(adobe_fixtures)
    pedidos_fmp: list[str] = []
    router = clients.fmp_http._transport.handler

    def espia(request: httpx.Request) -> httpx.Response:
        pedidos_fmp.append(request.url.path)
        return router(request)

    fmp_http = httpx.AsyncClient(transport=httpx.MockTransport(espia))
    from investbot.fmp_provider import FmpProvider

    svc = FundamentalsService(
        [EdgarProvider(http, UA, today=lambda: HOY), FmpProvider(fmp_http, "test-key")],
        sleep=never_sleep,
    )
    clients = dataclasses.replace(clients, fmp_http=fmp_http, fundamentals=svc)
    partes = await query_handler.fetch_and_analyze_parts("ADBE", clients, perfil="moderado")
    texto = "\n".join(partes)
    assert "No pude obtener suficientes datos" not in texto
    for ruta in ("/stable/income-statement", "/stable/cash-flow-statement", "/stable/balance-sheet-statement"):
        assert ruta not in pedidos_fmp
    assert "fundamentals ticker=ADBE source=sec_edgar fallback=- ms=" in caplog.text
