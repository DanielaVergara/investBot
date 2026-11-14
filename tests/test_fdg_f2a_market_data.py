"""F2a de SDD_fuentes_datos_gratuitas.md: VIX desde FRED `VIXCLS` (CA-26) y la
interfaz `market_data` (procedencia por bloque, respaldo FMP inyectado, opt-in).

FIXTURES SINTÉTICOS (CS-26): ni Finnhub ni FRED se capturaron; nada de licencia
ajena. Las claves de `/stock/profile2` y `/stock/metric` son NO VERIFICADAS contra
la API real. Sin red real (`httpx.MockTransport`) y sin tiempo real.
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from investbot import finnhub_client as fh
from investbot import market_data as md
from investbot.fmp_client import FMPError

from .fdg_f2a_helpers import KEY, ROUTES, WALL0, Clock, Recorder, quote

FRED_KEY = "CANARY-FRED-KEY-0123456789"
TODAY = date(2026, 11, 14)


def obs(*pairs) -> dict:
    return {"observations": [{"date": d, "value": v} for d, v in pairs]}


def vix_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Router:
    """Un único transporte para varios hosts; cuenta peticiones por host."""

    def __init__(self, *, fred=None, finnhub=None) -> None:
        self.fred = fred if fred is not None else (lambda r: httpx.Response(200, json=obs(("2026-11-13", "17.49"))))
        self.finnhub = finnhub or (lambda r: httpx.Response(200, json=ROUTES[r.url.path]()))
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.stlouisfed.org":
            return self.fred(request)
        if request.url.host == "finnhub.io":
            return self.finnhub(request)
        raise AssertionError(f"host inesperado: {request.url.host}")  # p. ej. FMP

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


# --------------------------------------------------------------------------- CA-26


async def test_fdg_ca26_vix_desde_fred_vixcls_cita_cboe_via_fred():
    router = Router()
    block = await md.get_vix(vix_client(router), FRED_KEY, today=TODAY)
    assert block.source == md.SOURCE_FRED and block.codes == ()
    assert block.data == {"value": 17.49, "observed_on": "2026-11-13", "source": "CBOE vía FRED"}
    assert md.VIX_SOURCE_LABEL == "CBOE vía FRED" and md.FRED_VIX_SERIES == "VIXCLS"
    (req,) = router.requests
    assert req.url.host == "api.stlouisfed.org" and req.url.scheme == "https"
    assert req.url.params["series_id"] == "VIXCLS" and req.url.params["file_type"] == "json"
    assert "financialmodelingprep" not in str(req.url)


async def test_fdg_ca26_ignora_el_punto_de_fred_y_usa_la_observacion_valida_mas_reciente():
    body = obs(("2026-11-14", "."), ("2026-11-13", "."), ("2026-11-12", "17.49"), ("2026-11-11", "18.0"))
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    assert block.data["value"] == 17.49 and block.data["observed_on"] == "2026-11-12"


async def test_fdg_ca26_todo_punto_es_sin_dato():
    body = obs(("2026-11-14", "."), ("2026-11-13", "."))
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    assert (block.source, block.data, block.codes) == (None, None, (md.CODE_VIX_NO_DATA,))


async def test_fdg_ca26_no_confia_en_el_orden_de_la_respuesta():
    body = obs(("2026-11-10", "16.0"), ("2026-11-13", "17.49"), ("2026-11-12", "99.0"))
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    assert block.data["value"] == 17.49


@pytest.mark.parametrize(
    ("valor", "ok"),
    [("5", True), ("5.0", True), ("4.99", False), ("0", False), ("-3", False), ("150", True), ("150.01", False), ("999", False)],
)
async def test_fdg_ca26_valor_fuera_de_5_150_es_na(valor, ok):
    body = obs(("2026-11-13", valor), ("2026-11-12", "17.49"))
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    if ok:
        assert block.data["value"] == float(valor)
    else:  # la observación más reciente es la inválida: no se salta a una anterior
        assert (block.data, block.codes) == (None, (md.CODE_VIX_OUT_OF_RANGE,))


@pytest.mark.parametrize(
    ("fecha", "ok"),
    [("2026-11-14", True), ("2026-11-08", True), ("2026-11-07", False), ("2026-10-01", False), ("2026-11-15", False), ("2030-01-01", False)],
)
async def test_fdg_ca26_observacion_de_7_dias_o_mas_o_futura_es_na(fecha, ok):
    body = obs((fecha, "17.49"))
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    assert (block.data is not None) is ok
    if not ok:
        assert block.codes == (md.CODE_VIX_STALE,)


async def test_fdg_ca26_descarta_basura_y_conserva_lo_valido():
    body = {
        "observations": [
            "texto",
            None,
            {"date": "2026-11-13", "value": "abc"},
            {"date": "2026-11-13", "value": "nan"},
            {"date": "2026-11-13", "value": "inf"},
            {"date": "2026-11-13", "value": None},
            {"date": "2026-11-13", "value": ["17"]},
            {"date": "13/11/2026", "value": "20"},
            {"date": "2026-11-31", "value": "20"},
            {"date": "٢٠٢٦-١١-١٣", "value": "20"},
            {"date": None, "value": "20"},
            {"value": "20"},
            {"date": "2026-11-12", "value": "17.49"},
        ]
    }
    block = await md.get_vix(vix_client(Router(fred=lambda r: httpx.Response(200, json=body))), FRED_KEY, today=TODAY)
    assert block.data["value"] == 17.49 and block.data["observed_on"] == "2026-11-12"


@pytest.mark.parametrize("body", [{}, {"observations": []}, {"observations": "x"}, {"observations": {}}, [], "x", 5])
async def test_fdg_ca26_forma_inesperada_es_sin_dato(body):
    block = await md.get_vix(
        vix_client(Router(fred=lambda r: httpx.Response(200, content=json.dumps(body).encode()))), FRED_KEY, today=TODAY
    )
    assert (block.data, block.codes) == (None, (md.CODE_VIX_NO_DATA,))


@pytest.mark.parametrize(
    "falla",
    [
        lambda r: httpx.Response(500, json={}),
        lambda r: httpx.Response(403, json={}),
        lambda r: httpx.Response(302, headers={"location": "https://evil.example/"}),
        lambda r: httpx.Response(200, content=b"<html>"),
        lambda r: (_ for _ in ()).throw(httpx.TimeoutException("t", request=r)),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("c", request=r)),
    ],
)
async def test_fdg_ca26_fallos_de_fred_son_na_sin_excepcion_y_sin_filtrar_la_key(falla, caplog):
    caplog.set_level(logging.DEBUG)
    router = Router(fred=falla)
    block = await md.get_vix(vix_client(router), FRED_KEY, today=TODAY)
    assert (block.source, block.data, block.codes) == (None, None, (md.CODE_VIX_UNAVAILABLE,))
    propios = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("investbot"))
    assert FRED_KEY not in propios and "stlouisfed" not in propios and len(router.requests) == 1


@pytest.mark.parametrize("key", [None, ""])
async def test_fdg_ca26_sin_key_de_fred_no_hay_llamada(key):
    router = Router()
    block = await md.get_vix(vix_client(router), key, today=TODAY)
    assert block.codes == (fh.CODE_NO_KEY,) and block.data is None and router.requests == []
    assert (await md.get_vix(None, FRED_KEY, today=TODAY)).codes == (fh.CODE_NO_KEY,)


async def test_fdg_ca26_el_vix_nunca_usa_fmp_ni_siquiera_como_respaldo():
    consultas: list[str] = []

    async def fmp(symbol):
        consultas.append(symbol)
        return {"price": 99.0}

    with pytest.raises(ValueError):  # no se puede registrar un respaldo FMP para el VIX
        md.MarketDataService(finnhub_http=None, finnhub_api_key=None, fmp_fallbacks={"vix": fmp})
    router = Router(fred=lambda r: httpx.Response(500, json={}))
    service = md.MarketDataService(
        finnhub_http=vix_client(router),
        finnhub_api_key=KEY,
        fred_http=vix_client(router),
        fred_api_key=FRED_KEY,
        fmp_fallbacks={"quote": fmp, "profile": fmp, "metrics": fmp},
    )
    snapshot = await service.fetch("SYNT")
    assert snapshot.vix.data is None and snapshot.vix.source is None
    assert "^VIX" not in consultas and all(s == "SYNT" for s in consultas)
    assert "financialmodelingprep.com" not in router.hosts()


async def test_fdg_ca26_fetch_vix_usa_el_reloj_inyectado_para_la_antiguedad():
    router = Router()
    ahora = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    service = md.MarketDataService(
        finnhub_http=None, finnhub_api_key=None, fred_http=vix_client(router), fred_api_key=FRED_KEY, wall=lambda: ahora
    )
    assert (await service.fetch_vix()).data["observed_on"] == "2026-11-13"
    tarde = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp()  # 7 días después
    service2 = md.MarketDataService(
        finnhub_http=None, finnhub_api_key=None, fred_http=vix_client(router), fred_api_key=FRED_KEY, wall=lambda: tarde
    )
    assert (await service2.fetch_vix()).codes == (md.CODE_VIX_STALE,)


# ------------------------------------------------------------------ interfaz market_data


def service_for(router, *, fallbacks=None, key=KEY, clock=None):
    clock = clock or Clock()
    finnhub_http = httpx.AsyncClient(transport=httpx.MockTransport(router))
    fh.install_gate(finnhub_http, now=clock.now, sleep=clock.sleep, wall=clock.wall)
    fred_http = httpx.AsyncClient(transport=httpx.MockTransport(router))
    return md.MarketDataService(
        finnhub_http=finnhub_http,
        finnhub_api_key=key,
        fred_http=fred_http,
        fred_api_key=FRED_KEY,
        fmp_fallbacks=fallbacks,
        wall=clock.wall,
    ), clock


def _fecha_del_reloj() -> str:
    return datetime.fromtimestamp(WALL0, tz=timezone.utc).date().isoformat()


def _router_en_fecha(**kw) -> Router:
    body = obs((_fecha_del_reloj(), "17.49"))
    return Router(fred=lambda r: httpx.Response(200, json=body), **kw)


async def test_fdg_market_data_snapshot_completo_declara_la_fuente_de_cada_bloque():
    router = _router_en_fecha()
    service, _ = service_for(router)
    snap = await service.fetch("SYNT")
    assert snap.provenance() == {"quote": "finnhub", "profile": "finnhub", "metrics": "finnhub", "vix": "fred"}
    assert snap.quote.data["price"] == 187.5 and snap.metrics.data["beta"] == 1.25
    assert snap.profile.data["marketCap"] == 2_000_000_000.0  # 187,5 x 10 M = 1.875 M: dentro del 25%
    assert snap.vix.data["source"] == "CBOE vía FRED" and snap.fallback_codes() == ()
    assert router.hosts().count("api.stlouisfed.org") == 1 and router.hosts().count("finnhub.io") == 3


async def test_fdg_market_data_el_precio_de_la_cotizacion_alimenta_el_contraste_del_perfil():
    def perfil_inflado(request):
        if request.url.path.endswith("profile2"):
            return httpx.Response(200, json={"name": "X", "currency": "USD", "marketCapitalization": 9999.0, "shareOutstanding": 10.0})
        return httpx.Response(200, json=ROUTES[request.url.path]())

    service, _ = service_for(_router_en_fecha(finnhub=perfil_inflado))
    snap = await service.fetch("SYNT")
    assert snap.profile.source == "finnhub" and snap.profile.data["marketCap"] is None
    assert snap.profile.data["marketCapCode"] == fh.CODE_CAP_INCOHERENT


async def test_fdg_market_data_sin_key_de_finnhub_no_hay_ninguna_llamada_a_finnhub():
    router = _router_en_fecha()
    for key in (None, ""):
        service, _ = service_for(router, key=key)
        snap = await service.fetch("SYNT")
        assert [b.codes for b in (snap.quote, snap.profile, snap.metrics)] == [(fh.CODE_NO_KEY,)] * 3
        assert snap.provenance()["quote"] is None and snap.vix.source == "fred"
    sin_cliente = md.MarketDataService(finnhub_http=None, finnhub_api_key=KEY)
    assert sin_cliente.new_budget() is None
    snap = await sin_cliente.fetch("SYNT")
    assert snap.quote.codes == (fh.CODE_NO_KEY,) and snap.vix.codes == (fh.CODE_NO_KEY,)
    assert "finnhub.io" not in router.hosts()


async def test_fdg_market_data_cae_a_fmp_por_bloque_con_su_procedencia():
    llamadas = []

    def respaldo(nombre, data):
        async def fn(symbol):
            llamadas.append((nombre, symbol))
            return data

        return fn

    service, _ = service_for(
        _router_en_fecha(),
        key=None,
        fallbacks={
            "quote": respaldo("quote", {"price": 50.0, "yearHigh": 60.0, "yearLow": 40.0}),
            "profile": respaldo("profile", {"companyName": "Respaldo SA", "beta": 0.9}),
            "metrics": respaldo("metrics", None),  # FMP tampoco tiene nada
        },
    )
    snap = await service.fetch("SYNT")
    assert snap.provenance() == {"quote": "fmp", "profile": "fmp", "metrics": None, "vix": "fred"}
    assert snap.quote.codes == (fh.CODE_NO_KEY,) and snap.metrics.codes == (fh.CODE_NO_KEY, md.CODE_FMP_UNAVAILABLE)
    assert [n for n, _ in llamadas] == ["quote", "profile", "metrics"]
    assert snap.fallback_codes() == (fh.CODE_NO_KEY, md.CODE_FMP_UNAVAILABLE)


async def test_fdg_market_data_fmp_que_falla_con_fmperror_deja_el_bloque_en_na():
    async def roto(symbol):
        raise FMPError("402")

    service, _ = service_for(_router_en_fecha(), key=None, fallbacks={"quote": roto})
    snap = await service.fetch("SYNT")
    assert snap.quote.data is None and snap.quote.codes == (fh.CODE_NO_KEY, md.CODE_FMP_UNAVAILABLE)


@pytest.mark.parametrize("devuelve", [{}, [], "x", None])
async def test_fdg_market_data_respaldo_con_forma_invalida_es_na(devuelve):
    async def fn(symbol):
        return devuelve

    service, _ = service_for(_router_en_fecha(), key=None, fallbacks={"quote": fn})
    assert (await service.fetch("SYNT")).quote.data is None


async def test_fdg_market_data_kill_switch_de_finnhub_manda_los_bloques_a_fmp():
    llamadas = []

    async def fmp(symbol):
        llamadas.append(symbol)
        return {"price": 10.0}

    rechazo = lambda r: httpx.Response(429, json={})  # noqa: E731
    service, _ = service_for(_router_en_fecha(finnhub=rechazo), fallbacks={"quote": fmp, "profile": fmp, "metrics": fmp})
    snap = await service.fetch("SYNT")
    assert snap.provenance() == {"quote": "fmp", "profile": "fmp", "metrics": "fmp", "vix": "fred"}
    assert snap.quote.codes == (fh.CODE_REJECTED,)
    assert snap.profile.codes == snap.metrics.codes == (fh.CODE_CIRCUIT_OPEN,)
    assert snap.fallback_codes() == (fh.CODE_REJECTED, fh.CODE_CIRCUIT_OPEN)
    assert len(llamadas) == 3


async def test_fdg_market_data_cotizacion_desactualizada_cae_a_fmp_y_lo_declara():
    async def fmp(symbol):
        return {"price": 188.0}

    vieja = lambda r: httpx.Response(200, json=quote(t=WALL0 - 9 * 86400)) if r.url.path.endswith("quote") else httpx.Response(200, json=ROUTES[r.url.path]())  # noqa: E731
    service, _ = service_for(_router_en_fecha(finnhub=vieja), fallbacks={"quote": fmp})
    snap = await service.fetch("SYNT")
    assert snap.quote.source == "fmp" and snap.quote.codes == (fh.CODE_STALE_QUOTE,)
    assert snap.profile.source == "finnhub"  # solo el bloque dañado cambia de fuente


async def test_fdg_market_data_el_precio_del_respaldo_solo_se_usa_si_es_un_numero_valido():
    def finnhub(request):
        if request.url.path.endswith("quote"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json=ROUTES[request.url.path]())

    async def fmp(symbol):
        return {"price": "no-numero"}

    service, _ = service_for(_router_en_fecha(finnhub=finnhub), fallbacks={"quote": fmp})
    snap = await service.fetch("SYNT")
    assert snap.quote.source == "fmp" and snap.profile.data["marketCapCode"] is None


async def test_fdg_market_data_las_tres_llamadas_comparten_un_presupuesto_de_20s():
    clock = Clock()

    def lento(request):
        limite = request.extensions["timeout"]["read"]
        clock.t += min(12.0, limite)
        if 12.0 > limite:
            raise httpx.TimeoutException("lento", request=request)
        return httpx.Response(200, json=ROUTES[request.url.path]())

    router = _router_en_fecha(finnhub=lento)
    service, _ = service_for(router, clock=clock)
    snap = await service.fetch("SYNT")
    assert router.hosts().count("finnhub.io") == 2  # la 3.ª degradó sin petición
    assert snap.quote.codes == (fh.CODE_UNAVAILABLE,) and snap.profile.codes == (fh.CODE_UNAVAILABLE,)
    assert snap.metrics.codes == (fh.CODE_BUDGET,)
    assert clock.t - 1000.0 == pytest.approx(20.0)
    # un presupuesto explícito se respeta (consulta compartida con otras llamadas, p. ej. peers en F2b)
    gastado = service.new_budget()
    clock.t += 20.0
    snap2 = await service.fetch("SYNT", budget=gastado)
    assert [b.codes for b in (snap2.quote, snap2.profile, snap2.metrics)] == [(fh.CODE_BUDGET,)] * 3


def test_fdg_market_data_los_bloques_validan_su_forma_y_el_enum_cerrado():
    md.Block("quote", None, None)
    md.Block("quote", "fmp", {"price": 1.0}, (fh.CODE_NO_KEY,))
    for args in (
        ("otro", None, None),
        ("quote", "fmp", None),
        ("quote", None, {"a": 1}),
        ("quote", "yahoo", {"a": 1}),
        ("quote", None, None, ("texto libre",)),
    ):
        with pytest.raises(ValueError):
            md.Block(*args)


def test_fdg_market_data_fallback_codes_no_repite_y_conserva_el_orden():
    n = md.Block
    snap = md.MarketSnapshot(
        "SYNT",
        n("quote", None, None, (fh.CODE_RATE, fh.CODE_NO_KEY)),
        n("profile", None, None, (fh.CODE_NO_KEY,)),
        n("metrics", None, None, ()),
        n("vix", None, None, (md.CODE_VIX_STALE,)),
    )
    assert snap.fallback_codes() == (fh.CODE_RATE, fh.CODE_NO_KEY, md.CODE_VIX_STALE)


# F2b: estos 3 modulos importan `market_data` POR DISEÑO (conexion al flujo, SDD 4.1).
_F2B_CONECTADOS = {"query_handler.py", "advanced_command.py", "bot.py"}


def test_fdg_market_data_f2b_solo_los_modulos_de_la_conexion_importan_market_data_y_sic_sector():
    """Ningun OTRO modulo de `src` importa market_data ni sic_sector (F2a lo exigia a todos;
    F2b exceptua solo a `query_handler.py`, `advanced_command.py` y `bot.py`)."""
    src = Path(__file__).resolve().parent.parent / "src" / "investbot"
    for path in sorted(src.glob("*.py")):
        if path.name in {"market_data.py", "sic_sector.py"} | _F2B_CONECTADOS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            nombres = []
            if isinstance(node, ast.Import):
                nombres = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                nombres = [node.module or "", *(a.name for a in node.names)]
            assert not {"market_data", "sic_sector", "investbot.market_data", "investbot.sic_sector"} & set(nombres), path.name


async def test_fdg_market_data_nada_se_persiste_ni_se_loguea(caplog, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)
    service, _ = service_for(_router_en_fecha(finnhub=lambda r: httpx.Response(401, json={})))
    await service.fetch("SYNT")
    propios = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("investbot"))
    assert KEY not in propios and FRED_KEY not in propios
    assert list(tmp_path.iterdir()) == []  # sin archivos: solo memoria
