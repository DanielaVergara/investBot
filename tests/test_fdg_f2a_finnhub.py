"""F2a de SDD_fuentes_datos_gratuitas.md: `finnhub_client.get_quote`, `get_profile2`,
`get_metric` (CA-25), kill-switch / breaker (13.4, sección 16), integridad (CS-27),
límite de tasa propio (CA-30) y presupuesto de espera por consulta (T-CS-27).

FIXTURES SINTÉTICOS (CS-26): ningún payload de este archivo es una captura real de
Finnhub. Los nombres de clave de `/stock/profile2` y `/stock/metric` son NO
VERIFICADOS contra la API real (salen del swagger); la usuaria hará una captura local
sin commitear. Sin red real (`httpx.MockTransport`) y sin tiempo real: `now`, `sleep`
y `wall` del `FinnhubGate` son inyectables.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import json
import logging
import traceback
import weakref

import httpx
import pytest

from investbot import finnhub_client as fh
from investbot.finnhub_client import FinnhubError, FinnhubReply

from .fdg_f2a_helpers import KEY, ROUTES, WALL0, Clock, Recorder, metric, profile, quote


def make(handler, clock: Clock | None = None, **gate_kw):
    clock = clock or Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = fh.install_gate(client, now=clock.now, sleep=clock.sleep, wall=clock.wall, **gate_kw)
    return client, gate, clock


async def quote_of(client, symbol="SYNT", **kw):
    return await fh.get_quote(client, KEY, symbol, **kw)


# --------------------------------------------------------------------------- CA-25


async def test_fdg_ca25_quote_usa_header_y_devuelve_precio():
    rec = Recorder()
    client, _, _ = make(rec)
    reply = await quote_of(client)
    assert reply.code is None and reply.data == {"price": 187.5, "timestamp": int(WALL0 - 60)}
    (req,) = rec.requests
    assert req.url.path == "/api/v1/quote" and req.url.host == "finnhub.io"
    assert req.url.scheme == "https" and dict(req.url.params) == {"symbol": "SYNT"}
    assert req.headers["X-Finnhub-Token"] == KEY
    assert "token" not in req.url.params and KEY not in str(req.url)


async def test_fdg_ca25_profile2_y_metric_usan_header_y_params():
    rec = Recorder()
    client, _, _ = make(rec)
    await fh.get_profile2(client, KEY, "SYNT")
    await fh.get_metric(client, KEY, "SYNT")
    p2, mt = rec.requests
    assert p2.url.path == "/api/v1/stock/profile2" and dict(p2.url.params) == {"symbol": "SYNT"}
    assert mt.url.path == "/api/v1/stock/metric"
    assert dict(mt.url.params) == {"symbol": "SYNT", "metric": "all"}
    for req in rec.requests:
        assert req.headers["X-Finnhub-Token"] == KEY
        assert "token" not in req.url.params and KEY not in str(req.url)


@pytest.mark.parametrize("sym", ["BRK.B", "BRK-B", "A", "MELI", "CLPT", "ABCDEF", "ABCDEF.AB"])
async def test_fdg_ca25_simbolos_validos_se_consultan(sym):
    rec = Recorder()
    client, _, _ = make(rec)
    assert (await quote_of(client, sym)).data is not None
    assert rec.requests[0].url.params["symbol"] == sym


@pytest.mark.parametrize(
    "sym", ["", "aapl", "AAPL\n", "../x", "A@B", "ſpy", "ABCDEFG", "ABCDEF.ABC", "AAPL ", None, 7, ["A"], "A" * 40]
)
async def test_fdg_ca25_simbolo_hostil_no_hace_ninguna_peticion(sym):
    rec = Recorder()
    client, _, _ = make(rec)
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
        reply = await call(client, KEY, sym)
        assert (reply.data, reply.code) == (None, fh.CODE_NO_DATA)
    assert rec.requests == []


@pytest.mark.parametrize("key", [None, "", " ", "a b", "clave\nmala", "ñandú-key", "k" * 257, 123, b"bytes"])
async def test_fdg_ca25_sin_key_valida_no_hay_llamadas(key):
    rec = Recorder()
    client, _, _ = make(rec)
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
        reply = await call(client, key, "SYNT")
        assert (reply.data, reply.code) == (None, fh.CODE_NO_KEY)
    assert rec.requests == []


@pytest.mark.parametrize("c", [0, 0.0, -1, -0.01, None, "187.5", True, False, [], {}])
async def test_fdg_ca25_c_menor_o_igual_a_cero_o_invalido_es_sin_dato(c):
    client, _, _ = make(Recorder(httpx.Response(200, json=quote(c=c))))
    reply = await quote_of(client)
    assert (reply.data, reply.code) == (None, fh.CODE_NO_DATA)


@pytest.mark.parametrize("raw", [b'{"c": NaN, "t": 1780000000}', b'{"c": Infinity, "t": 1780000000}'])
async def test_fdg_ca25_c_no_finito_es_sin_dato(raw):
    client, _, _ = make(Recorder(httpx.Response(200, content=raw)))
    assert (await quote_of(client)).code == fh.CODE_NO_DATA


async def test_fdg_ca25_simbolo_desconocido_todo_ceros_es_sin_dato():
    # Comportamiento NO VERIFICADO de Finnhub ante un símbolo desconocido: ceros.
    zeros = {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0}
    client, _, _ = make(Recorder(httpx.Response(200, json=zeros)))
    assert (await quote_of(client)).code == fh.CODE_NO_DATA


@pytest.mark.parametrize("body", [[], "texto", 5, None])
async def test_fdg_ca25_json_que_no_es_objeto_es_sin_dato_sin_excepcion(body):
    client, gate, _ = make(Recorder(httpx.Response(200, content=json.dumps(body).encode())))
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
        reply = await call(client, KEY, "SYNT")
        assert (reply.data, reply.code) == (None, fh.CODE_NO_DATA)
    assert gate.failures == 0  # un JSON raro pero válido no alimenta el breaker


# --- CS-27: frescura de la cotización ---------------------------------------------


@pytest.mark.parametrize(
    ("edad", "ok"),
    [(0, True), (60, True), (5 * 86400 - 1, True), (5 * 86400, True), (5 * 86400 + 1, False), (30 * 86400, False)],
)
async def test_fdg_ca25_cotizacion_con_mas_de_5_dias_es_desactualizada(edad, ok):
    client, _, _ = make(Recorder(httpx.Response(200, json=quote(t=WALL0 - edad))))
    reply = await quote_of(client)
    if ok:
        assert reply.data is not None and reply.code is None
    else:
        assert (reply.data, reply.code) == (None, fh.CODE_STALE_QUOTE)


@pytest.mark.parametrize("t", [0, None, "1780000000", -5, True])
async def test_fdg_ca25_t_cero_o_ausente_es_desactualizada(t):
    client, _, _ = make(Recorder(httpx.Response(200, json=quote(t=t))))
    reply = await quote_of(client)
    assert (reply.data, reply.code) == (None, fh.CODE_STALE_QUOTE)


@pytest.mark.parametrize(("adelanto", "ok"), [(3600, True), (86400, True), (86401, False), (10 * 86400, False)])
async def test_fdg_ca25_t_en_el_futuro_lejano_es_dato_corrupto(adelanto, ok):
    client, _, _ = make(Recorder(httpx.Response(200, json=quote(t=WALL0 + adelanto))))
    reply = await quote_of(client)
    assert (reply.code is None) is ok


async def test_fdg_ca25_la_frescura_se_mide_con_el_reloj_inyectado():
    client, _, clock = make(Recorder())
    assert (await quote_of(client)).data is not None
    clock.t += 6 * 86400  # el mismo `t` del servidor ya tiene 6 días
    assert (await quote_of(client)).code == fh.CODE_STALE_QUOTE


# --- CS-27: integridad de profile2 -------------------------------------------------


async def test_fdg_ca25_profile2_happy_path_con_moneda_usd():
    client, _, _ = make(Recorder())
    reply = await fh.get_profile2(client, KEY, "SYNT", price=200.0)  # 200 x 10 M = 2.000 M
    assert reply.data == {
        "companyName": "Empresa Sintetica SA",
        "currency": "USD",
        "marketCap": 2_000_000_000.0,
        "sharesOutstanding": 10_000_000.0,
        "marketCapCode": None,
    }
    assert "CANARIO_FH" not in json.dumps(reply.data)  # finnhubIndustry no se propaga


@pytest.mark.parametrize("moneda", ["EUR", "COP", "usd", None, "", "US", 840, "USD "])
async def test_fdg_ca25_moneda_distinta_de_usd_deja_marketcap_en_none(moneda):
    client, _, _ = make(Recorder(httpx.Response(200, json=profile(currency=moneda))))
    reply = await fh.get_profile2(client, KEY, "SYNT", price=200.0)
    assert reply.data["marketCap"] is None and reply.data["marketCapCode"] == fh.CODE_NOT_USD
    assert reply.data["companyName"] == "Empresa Sintetica SA"  # el perfil sigue sirviendo


async def test_fdg_ca25_moneda_ausente_sin_marketcap_no_marca_codigo():
    body = profile(currency=None)
    del body["marketCapitalization"]
    client, _, _ = make(Recorder(httpx.Response(200, json=body)))
    reply = await fh.get_profile2(client, KEY, "SYNT", price=200.0)
    assert reply.data["marketCap"] is None and reply.data["marketCapCode"] is None


@pytest.mark.parametrize(
    ("cap_m", "ok"),
    [(1000.0, True), (1250.0, True), (1250.5, False), (750.0, True), (749.5, False), (1.0, False), (9000.0, False)],
)
async def test_fdg_ca25_tolerancia_de_25_por_ciento_con_bordes_exactos(cap_m, ok):
    # implícita = 100 x 10 M = 1.000 M; bordes: 750 y 1.250 (±25%).
    client, _, _ = make(Recorder(httpx.Response(200, json=profile(marketCapitalization=cap_m))))
    reply = await fh.get_profile2(client, KEY, "SYNT", price=100.0)
    assert (reply.data["marketCap"] is not None) is ok
    assert reply.data["marketCapCode"] == (None if ok else fh.CODE_CAP_INCOHERENT)


async def test_fdg_ca25_marketcap_sin_precio_o_sin_acciones_no_se_puede_contrastar():
    client, _, _ = make(Recorder())
    sin_precio = await fh.get_profile2(client, KEY, "SYNT")
    assert sin_precio.data["marketCap"] == 2_000_000_000.0
    for precio in (0, -3, "200", True, float("nan")):
        reply = await fh.get_profile2(client, KEY, "SYNT", price=precio)
        assert reply.data["marketCap"] == 2_000_000_000.0 and reply.data["marketCapCode"] is None
    body = profile()
    del body["shareOutstanding"]
    client2, _, _ = make(Recorder(httpx.Response(200, json=body)))
    sin_acciones = await fh.get_profile2(client2, KEY, "SYNT", price=999.0)
    assert sin_acciones.data["marketCap"] == 2_000_000_000.0
    assert sin_acciones.data["sharesOutstanding"] is None


async def test_fdg_ca25_profile2_vacio_es_sin_dato():
    client, _, _ = make(Recorder(httpx.Response(200, json={})))
    reply = await fh.get_profile2(client, KEY, "SYNT")
    assert (reply.data, reply.code) == (None, fh.CODE_NO_DATA)


async def test_fdg_ca25_profile2_solo_con_nombre_o_solo_con_capitalizacion_sirve():
    solo_nombre = {"name": "Solo Nombre"}
    solo_cap = {"marketCapitalization": 500.0, "currency": "USD"}
    client, _, _ = make(Recorder(httpx.Response(200, json=solo_nombre), httpx.Response(200, json=solo_cap)))
    a = await fh.get_profile2(client, KEY, "SYNT")
    assert a.data["companyName"] == "Solo Nombre" and a.data["marketCap"] is None
    client2, _, _ = make(Recorder(httpx.Response(200, json=solo_cap)))
    b = await fh.get_profile2(client2, KEY, "SYNT")
    assert b.data["companyName"] is None and b.data["marketCap"] == 500_000_000.0


@pytest.mark.parametrize("valor", [0, -5, "2000", None, True, float("inf")])
async def test_fdg_ca25_capitalizacion_no_positiva_o_invalida_es_none(valor):
    body = profile(marketCapitalization=valor)
    client, _, _ = make(Recorder(httpx.Response(200, content=json.dumps(body).encode())))
    reply = await fh.get_profile2(client, KEY, "SYNT", price=200.0)
    assert reply.data["marketCap"] is None and reply.data["marketCapCode"] is None


async def test_fdg_ca25_nombre_se_sanea_y_se_acota_a_100_caracteres():
    sucio = "Acme\x00\x1b[31m\nCorp\t*bold* [x](y)" + "z" * 300
    client, _, _ = make(Recorder(httpx.Response(200, json=profile(name=sucio))))
    nombre = (await fh.get_profile2(client, KEY, "SYNT")).data["companyName"]
    assert len(nombre) <= 100 and nombre.startswith("Acme [31m Corp *bold*")
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in nombre)
    for malo in ("", "   \n\t ", 12, None, ["x"]):
        c2, _, _ = make(Recorder(httpx.Response(200, json=profile(name=malo))))
        assert (await fh.get_profile2(c2, KEY, "SYNT")).data["companyName"] is None


# --- metric -------------------------------------------------------------------------


async def test_fdg_ca25_metric_extrae_beta_y_52_semanas():
    client, _, _ = make(Recorder())
    reply = await fh.get_metric(client, KEY, "SYNT")
    assert reply.data == {"beta": 1.25, "yearHigh": 200.0, "yearLow": 120.0}


@pytest.mark.parametrize(
    ("over", "esperado"),
    [
        ({"beta": None}, {"beta": None, "yearHigh": 200.0, "yearLow": 120.0}),
        ({"beta": -0.4}, {"beta": -0.4, "yearHigh": 200.0, "yearLow": 120.0}),
        ({"beta": "1.2"}, {"beta": None, "yearHigh": 200.0, "yearLow": 120.0}),
        ({"beta": True}, {"beta": None, "yearHigh": 200.0, "yearLow": 120.0}),
        ({"52WeekHigh": None}, {"beta": 1.25, "yearHigh": None, "yearLow": None}),
        ({"52WeekLow": 0}, {"beta": 1.25, "yearHigh": None, "yearLow": None}),
        ({"52WeekLow": -1.0}, {"beta": 1.25, "yearHigh": None, "yearLow": None}),
        ({"52WeekLow": 250.0}, {"beta": 1.25, "yearHigh": None, "yearLow": None}),  # low > high
        ({"52WeekHigh": 120.0}, {"beta": 1.25, "yearHigh": 120.0, "yearLow": 120.0}),  # low == high
        ({"52WeekHigh": "200"}, {"beta": 1.25, "yearHigh": None, "yearLow": None}),
    ],
)
async def test_fdg_ca25_metric_valida_cada_campo(over, esperado):
    client, _, _ = make(Recorder(httpx.Response(200, json=metric(**over))))
    assert (await fh.get_metric(client, KEY, "SYNT")).data == esperado


@pytest.mark.parametrize(
    "body",
    [{}, {"metric": {}}, {"metric": []}, {"metric": "x"}, {"metric": None}, metric(beta=None, **{"52WeekHigh": None})],
)
async def test_fdg_ca25_metric_sin_beta_ni_52_semanas_es_sin_dato(body):
    client, _, _ = make(Recorder(httpx.Response(200, json=body)))
    reply = await fh.get_metric(client, KEY, "SYNT")
    assert (reply.data, reply.code) == (None, fh.CODE_NO_DATA)


async def test_fdg_ca25_metric_beta_nan_o_infinito_no_se_acepta():
    raw = b'{"metric": {"beta": NaN, "52WeekHigh": Infinity, "52WeekLow": 1}}'
    client, _, _ = make(Recorder(httpx.Response(200, content=raw)))
    assert (await fh.get_metric(client, KEY, "SYNT")).code == fh.CODE_NO_DATA


# --- FinnhubReply / FinnhubError ----------------------------------------------------


def test_fdg_ca25_finnhubreply_exige_exactamente_un_campo_y_codigo_del_enum():
    assert FinnhubReply({"a": 1}, None).data == {"a": 1}
    assert FinnhubReply(None, fh.CODE_NO_DATA).code == fh.CODE_NO_DATA
    for args in (({"a": 1}, fh.CODE_NO_DATA), (None, None), (None, "texto libre"), ({"a": 1}, "x")):
        with pytest.raises(ValueError):
            FinnhubReply(*args)
    with pytest.raises(dataclasses.FrozenInstanceError):
        FinnhubReply(None, fh.CODE_NO_DATA).code = fh.CODE_RATE  # type: ignore[misc]


def test_fdg_ca25_finnhuberror_conserva_la_firma_antigua_y_agrega_code():
    assert FinnhubError("hola").code is None and str(FinnhubError("hola")) == "hola"
    assert FinnhubError("x", fh.CODE_REJECTED).code == fh.CODE_REJECTED
    assert str(FinnhubError()) == ""


# --- token nunca en URL ni logs (CS-19 en los endpoints nuevos) ---------------------

_ERRORES = {
    "timeout": lambda r: (_ for _ in ()).throw(httpx.TimeoutException("t", request=r)),
    "red": lambda r: (_ for _ in ()).throw(httpx.ConnectError("c", request=r)),
    "http500": httpx.Response(500, json={"error": "x"}),
    "http401": httpx.Response(401, json={"error": "x"}),
    "http429": httpx.Response(429, json={"error": "x"}),
    "nojson": httpx.Response(200, content=b"<html>no es json</html>"),
}


@pytest.mark.parametrize("escenario", sorted(_ERRORES))
@pytest.mark.parametrize("call", [fh.get_quote, fh.get_profile2, fh.get_metric])
async def test_fdg_ca25_la_key_no_aparece_en_ningun_camino_de_error(caplog, call, escenario):
    caplog.set_level(logging.DEBUG)
    item = _ERRORES[escenario]
    rec = Recorder(item)
    client, _, _ = make(rec)
    with pytest.raises(FinnhubError) as info:
        await call(client, KEY, "SYNT")
    exc = info.value
    completo = "".join(traceback.format_exception(exc))
    assert KEY not in caplog.text and KEY not in repr(exc) and KEY not in str(exc)
    assert KEY not in completo and "httpx" not in completo.replace("investbot", "")
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert exc.code in {fh.CODE_UNAVAILABLE, fh.CODE_REJECTED}
    propios = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("investbot"))
    assert "finnhub.io" not in propios and "http" not in propios  # tampoco la URL
    assert KEY not in str(rec.requests[0].url)


async def test_fdg_ca25_no_se_sigue_ninguna_redireccion():
    rec = Recorder(httpx.Response(302, headers={"location": "https://evil.example/steal"}))
    client, _, _ = make(rec)
    with pytest.raises(FinnhubError) as info:
        await quote_of(client)
    assert info.value.code == fh.CODE_UNAVAILABLE and len(rec.requests) == 1
    assert rec.requests[0].url.host == "finnhub.io"


async def test_fdg_ca25_el_timeout_de_la_peticion_es_de_10_segundos_como_maximo():
    seen = []

    def spy(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=quote())

    client, _, _ = make(Recorder(spy))
    await quote_of(client)
    assert seen == [10.0]


# --- kill-switch / breaker ----------------------------------------------------------


@pytest.mark.parametrize("status", [401, 402, 403, 429])
async def test_fdg_ca25_kill_switch_los_cuatro_estados_abren_el_breaker_300s(status):
    rec = Recorder(httpx.Response(status, json={"error": "x"}), httpx.Response(200, json=quote()))
    client, gate, clock = make(rec)
    with pytest.raises(FinnhubError) as info:
        await quote_of(client)
    assert info.value.code == fh.CODE_REJECTED and str(status) in str(info.value)
    # mientras está abierto: ni una petición más, ni de ningún endpoint
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
        reply = await call(client, KEY, "SYNT")
        assert (reply.data, reply.code) == (None, fh.CODE_CIRCUIT_OPEN)
    clock.t += 299.9
    assert (await quote_of(client)).code == fh.CODE_CIRCUIT_OPEN
    assert len(rec.requests) == 1
    clock.t += 0.2  # 300 s: semi-abierto, pasa UNA sonda y si sale bien cierra
    assert (await quote_of(client)).data is not None
    assert len(rec.requests) == 2 and gate.open_until is None
    assert (await quote_of(client)).data is not None and len(rec.requests) == 3


@pytest.mark.parametrize("status", [200, 301, 404, 500, 502, 503, 400])
async def test_fdg_ca25_otros_estados_no_abren_el_breaker_por_un_solo_fallo(status):
    rec = Recorder(httpx.Response(status, json=quote()))
    client, gate, _ = make(rec)
    try:
        await quote_of(client)
    except FinnhubError:
        pass
    assert gate.open_until is None


@pytest.mark.parametrize(
    "fallo",
    [
        httpx.Response(500, json={}),
        httpx.Response(404, json={}),
        httpx.Response(200, content=b"no json"),
        httpx.TimeoutException("t"),
        httpx.ConnectError("c"),
    ],
)
async def test_fdg_ca25_tres_fallos_transitorios_seguidos_abren_60s(fallo):
    rec = Recorder(fallo)
    client, gate, clock = make(rec)
    for _ in range(2):
        with pytest.raises(FinnhubError) as info:
            await quote_of(client)
        assert info.value.code == fh.CODE_UNAVAILABLE
    assert gate.open_until is None
    with pytest.raises(FinnhubError):
        await quote_of(client)
    assert gate.open_until == clock.t + 60.0
    assert (await quote_of(client)).code == fh.CODE_CIRCUIT_OPEN and len(rec.requests) == 3
    clock.t += 59.9
    assert (await quote_of(client)).code == fh.CODE_CIRCUIT_OPEN and len(rec.requests) == 3
    clock.t += 0.2
    with pytest.raises(FinnhubError):  # la sonda falla: reabre otros 60 s
        await quote_of(client)
    assert len(rec.requests) == 4 and gate.open_until == clock.t + 60.0
    assert (await quote_of(client)).code == fh.CODE_CIRCUIT_OPEN


async def test_fdg_ca25_un_exito_intermedio_reinicia_la_cuenta_de_fallos():
    ok = httpx.Response(200, json=quote())
    mal = httpx.Response(500, json={})
    rec = Recorder(mal, mal, ok, mal, mal, ok)
    client, gate, _ = make(rec)
    for i in range(6):
        try:
            await quote_of(client)
        except FinnhubError:
            pass
    assert gate.open_until is None and len(rec.requests) == 6


async def test_fdg_ca25_la_sonda_semi_abierta_es_unica():
    requests: list[httpx.Request] = []
    en_vuelo = asyncio.Event()
    liberar = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={})
        en_vuelo.set()
        await liberar.wait()
        return httpx.Response(200, json=quote())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clock = Clock()
    gate = fh.install_gate(client, now=clock.now, sleep=clock.sleep, wall=clock.wall)
    with pytest.raises(FinnhubError):
        await quote_of(client)
    clock.t += 301
    sonda = asyncio.ensure_future(quote_of(client))
    await en_vuelo.wait()
    segunda = await quote_of(client)  # mientras la sonda vuela: rechazada sin petición
    assert segunda.code == fh.CODE_CIRCUIT_OPEN and len(requests) == 2
    liberar.set()
    assert (await sonda).data is not None and gate.open_until is None and gate.probing is False


def test_fdg_ca25_gate_record_none_libera_la_sonda_sin_contar_fallo():
    clock = Clock()
    gate = fh.FinnhubGate(now=clock.now, sleep=clock.sleep, wall=clock.wall)
    assert gate.admit() is False  # cerrado
    gate.open_until = clock.t - 1  # venció: semi-abierto
    assert gate.admit() is True and gate.probing is True
    assert gate.admit() is None  # la sonda ya vuela
    gate.record(None, True)  # degradada o cancelada: no es un fallo
    assert gate.probing is False and gate.failures == 0 and gate.open_until is not None
    assert gate.admit() is True


async def test_fdg_ca25_degradar_a_na_en_sonda_no_deja_el_breaker_trabado():
    client, gate, clock = make(Recorder(), rate_per_minute=1)
    gate.open_until = clock.t - 1
    gate._stamps.append(clock.t)  # la ventana de tasa está llena: la sonda degrada
    reply = await quote_of(client, budget=gate.new_budget(total=0.5))
    assert reply.code == fh.CODE_RATE and gate.probing is False
    assert gate.admit() is True  # sigue habiendo una sonda disponible


async def test_fdg_ca25_cada_cliente_tiene_su_propio_estado_sin_globales():
    rec_a, rec_b = Recorder(httpx.Response(429, json={})), Recorder()
    a, gate_a, _ = make(rec_a)
    b, gate_b, _ = make(rec_b)
    with pytest.raises(FinnhubError):
        await quote_of(a)
    assert gate_a.open_until is not None and gate_b.open_until is None
    assert (await quote_of(b)).data is not None and fh.gate_for(a) is gate_a
    # un cliente nuevo sin `install_gate` arranca limpio y su estado se libera con él
    c = httpx.AsyncClient(transport=httpx.MockTransport(Recorder()))
    gate_c = fh.gate_for(c)
    assert gate_c is not gate_a and gate_c.open_until is None and fh.gate_for(c) is gate_c
    ref_cliente, ref_gate = weakref.ref(c), weakref.ref(gate_c)
    del c, gate_c
    gc.collect()
    assert ref_cliente() is None and ref_gate() is None  # el estado muere con su cliente


# --------------------------------------------------------------------------- CA-30


async def test_fdg_ca30_veinte_consultas_rapidas_no_superan_el_bucket_y_degradan_a_na():
    rec = Recorder()
    client, gate, clock = make(rec)
    resultados = []
    for _ in range(20):  # 20 consultas x (quote, profile2, metric) = 60 llamadas en 0 s
        presupuesto = gate.new_budget()
        for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
            resultados.append(await call(client, KEY, "SYNT", budget=presupuesto))
    assert len(rec.requests) == fh.RATE_PER_MINUTE == 50
    atendidas = [r for r in resultados if r.data is not None]
    degradadas = [r for r in resultados if r.data is None]
    assert len(atendidas) == 50 and len(degradadas) == 10
    assert {(r.data, r.code) for r in degradadas} == {(None, fh.CODE_RATE)}  # N/D, nunca excepción
    assert clock.sleeps == [] and clock.t == 1000.0  # no esperó: degradó en el acto


async def test_fdg_ca30_en_paralelo_tampoco_se_supera_el_bucket():
    rec = Recorder()
    client, gate, _ = make(rec)
    replies = await asyncio.gather(*(quote_of(client) for _ in range(60)))
    assert len(rec.requests) == 50
    assert sorted(r.code or "ok" for r in replies).count(fh.CODE_RATE) == 10


async def test_fdg_ca30_la_ventana_se_libera_a_los_60_segundos_exactos():
    rec = Recorder()
    client, gate, clock = make(rec, rate_per_minute=3)
    for _ in range(3):
        assert (await quote_of(client)).data is not None
        clock.t += 1.0  # llamadas en t=1000, 1001, 1002; ahora t=1003
    assert (await quote_of(client)).code == fh.CODE_RATE  # faltan 57 s: degrada
    clock.t = 1040.0
    assert (await quote_of(client)).code == fh.CODE_RATE  # faltan 20 s (> 15): degrada
    assert clock.sleeps == [] and len(rec.requests) == 3
    clock.t = 1046.0  # faltan 14 s: cabe -> espera 14 y pasa
    assert (await quote_of(client)).data is not None
    assert clock.sleeps == [pytest.approx(14.0)] and len(rec.requests) == 4
    # a los 60 s EXACTOS la entrada más vieja ya venció: no hay que esperar nada
    client2, _, clock2 = make(Recorder(), rate_per_minute=1)
    assert (await quote_of(client2)).data is not None
    clock2.t += 60.0
    assert (await quote_of(client2)).data is not None and clock2.sleeps == []


async def test_fdg_ca30_espera_solo_si_cabe_en_15s_y_en_el_presupuesto():
    rec = Recorder()
    client, gate, clock = make(rec, rate_per_minute=1)
    assert (await quote_of(client)).data is not None  # t=1000, ventana llena
    clock.t = 1000.0 + 50.0  # faltan 10 s: cabe en 15 s y en 20 s -> espera
    assert (await quote_of(client)).data is not None
    assert clock.sleeps == [pytest.approx(10.0)]
    clock.t += 40.0  # faltan 20 s: más de 15 -> degrada sin dormir
    antes = len(clock.sleeps)
    assert (await quote_of(client)).code == fh.CODE_RATE and len(clock.sleeps) == antes
    clock.t += 5.0  # faltan 15 s exactos: cabe (<= 15)
    assert (await quote_of(client, budget=gate.new_budget(total=16.0))).data is not None
    assert clock.sleeps[-1] == pytest.approx(15.0)
    clock.t += 50.0  # faltan 10 s pero el presupuesto solo tiene 9 -> degrada
    assert (await quote_of(client, budget=gate.new_budget(total=9.0))).code == fh.CODE_RATE


async def test_fdg_ca30_si_tras_esperar_se_agota_el_presupuesto_degrada_sin_llamar():
    rec = Recorder()
    client, gate, clock = make(rec, rate_per_minute=1)
    assert (await quote_of(client)).data is not None
    clock.t += 50.0
    reply = await quote_of(client, budget=gate.new_budget(total=10.0))  # espera 10 s: queda 0
    assert (reply.data, reply.code) == (None, fh.CODE_BUDGET)
    assert len(rec.requests) == 1 and clock.sleeps == [pytest.approx(10.0)]


# --------------------------------------------------------------------------- T-CS-27


class Slow:
    """Handler con latencia por llamada que respeta el timeout del request, como httpx."""

    def __init__(self, clock: Clock, latency: float) -> None:
        self.clock, self.latency = clock, latency
        self.timeouts: list[float] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        limite = request.extensions["timeout"]["read"]
        self.timeouts.append(limite)
        if self.latency > limite:
            self.clock.t += limite
            raise httpx.TimeoutException("lento", request=request)
        self.clock.t += self.latency
        return httpx.Response(200, json=ROUTES[request.url.path]())


async def _consulta(client, gate, symbol="SYNT"):
    presupuesto = gate.new_budget()
    resultados = []
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric, fh.get_quote, fh.get_metric):
        try:
            resultados.append(await call(client, KEY, symbol, budget=presupuesto))
        except FinnhubError as exc:
            resultados.append(exc)
    return resultados


@pytest.mark.parametrize("latencia", [0.2, 6.0, 7.5, 12.0, 30.0])
async def test_fdg_tcs27_veinte_consultas_respetan_el_presupuesto_de_20s(latencia):
    clock = Clock()
    slow = Slow(clock, latencia)
    client, gate, _ = make(slow, clock)
    for _ in range(20):
        inicio = clock.t
        resultados = await _consulta(client, gate)
        assert clock.t - inicio <= fh.QUERY_BUDGET_SECONDS + 1e-9  # nunca más de 20 s apiladas
        for r in resultados:  # lo que no entra se degrada a N/D (o falla sanitizado), jamás explota
            assert isinstance(r, (FinnhubReply, FinnhubError))
        clock.t += 1.0
    assert all(t <= 10.0 for t in slow.timeouts)


async def test_fdg_tcs27_latencia_12s_la_tercera_llamada_ya_no_sale():
    clock = Clock()
    slow = Slow(clock, 12.0)
    client, gate, _ = make(slow, clock)
    resultados = await _consulta(client, gate)
    assert slow.timeouts == [10.0, 10.0]  # la 3.ª llega con 0 s de presupuesto: no hay petición
    assert [type(r) for r in resultados[:2]] == [FinnhubError, FinnhubError]
    assert [(r.data, r.code) for r in resultados[2:]] == [(None, fh.CODE_BUDGET)] * 3
    assert clock.t - 1000.0 == pytest.approx(20.0)


async def test_fdg_tcs27_latencia_8s_el_timeout_se_recorta_al_presupuesto_restante():
    clock = Clock()
    slow = Slow(clock, 8.0)
    client, gate, _ = make(slow, clock)
    resultados = await _consulta(client, gate)
    assert slow.timeouts == [10.0, 10.0, 4.0]  # 20 - 16 = 4 s para la 3.ª
    assert isinstance(resultados[2], FinnhubError)  # no cabía en 4 s: corta ahí
    assert [(r.data, r.code) for r in resultados[3:]] == [(None, fh.CODE_BUDGET)] * 2
    assert clock.t - 1000.0 == pytest.approx(20.0)


async def test_fdg_tcs27_presupuesto_agotado_no_hace_peticion_y_degrada_a_na():
    rec = Recorder()
    client, gate, clock = make(rec)
    presupuesto = gate.new_budget()
    clock.t += 20.0
    for call in (fh.get_quote, fh.get_profile2, fh.get_metric):
        reply = await call(client, KEY, "SYNT", budget=presupuesto)
        assert (reply.data, reply.code) == (None, fh.CODE_BUDGET)
    assert rec.requests == []
    clock.t -= 0.5  # quedan 0,5 s: todavía cabe una llamada
    assert (await quote_of(client, budget=presupuesto)).data is not None
    assert rec.requests[0].extensions["timeout"]["read"] == pytest.approx(0.5)


async def test_fdg_tcs27_sin_presupuesto_explicito_cada_llamada_recibe_uno_nuevo_de_20s():
    rec = Recorder()
    client, gate, clock = make(rec)
    assert (await quote_of(client)).data is not None
    clock.t += 500.0
    assert (await quote_of(client)).code is None  # otra "consulta": presupuesto fresco


def test_fdg_tcs27_constantes_de_la_spec():
    assert fh.QUERY_BUDGET_SECONDS == 20.0 and fh.RATE_MAX_WAIT_SECONDS == 15.0
    assert fh.RATE_PER_MINUTE < 60 and fh.MARKET_CAP_TOLERANCE == 0.25
    assert fh.QUOTE_MAX_AGE_SECONDS == 5 * 86400
    assert fh.QueryBudget(lambda: 5.0).remaining() == 20.0
