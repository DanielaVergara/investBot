"""F1b-1 de SDD_fuentes_datos_gratuitas.md: `SecGate` por cliente (limitador de
8 req/s, breaker `sec_edgar`, cachés, single-flight), `get_company_facts` en
streaming, validación del mapa ticker->CIK (CS-14) y política de refresco (CS-18).

Sin red real (`httpx.MockTransport`) y sin tiempo real: `now`/`sleep` del
`SecGate` son inyectables y el single-flight se sincroniza con `asyncio.Event`.
Nombres `test_fdg_ca<n>_*` / `test_fdg_tcs<n>_*` (meta-test de trazabilidad de la spec).
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
import weakref
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from investbot import sec_edgar_client as sec
from investbot.sec_edgar_client import SecEdgarError

UA = "InvestBot contacto@ejemplo.test"
MIB = 1024 * 1024
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


@pytest.fixture(autouse=True)
def _reset_global_map():
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None
    yield
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None


class Clock:
    """Reloj falso: `sleep` solo adelanta el reloj; nunca duerme de verdad."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.t += delay
        await asyncio.sleep(0)


class TrackedStream(httpx.AsyncByteStream):
    """Cuerpo en chunks de 64 KiB que cuenta cuántos bytes se leyeron."""

    def __init__(self, total: int, fill: bytes = b"x") -> None:
        self.total = total
        self.fill = fill
        self.read_bytes = 0
        self.closed = False

    async def __aiter__(self):
        remaining = self.total
        while remaining > 0:
            n = min(65536, remaining)
            self.read_bytes += n
            remaining -= n
            yield self.fill * n

    async def aclose(self) -> None:
        self.closed = True


class Script:
    """Handler: registra las peticiones y responde en orden (la última se repite)."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item

    @property
    def n(self) -> int:
        return len(self.requests)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def make_client(handler, *, follow_redirects: bool = False):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=follow_redirects
    )
    clock = Clock()
    sec.install_gate(client, now=clock.now, sleep=clock.sleep)
    return client, clock


def json_resp(payload) -> httpx.Response:
    return httpx.Response(200, json=payload)


def exact_json(size: int) -> bytes:
    """JSON dict válido de exactamente `size` bytes."""
    head, tail = b'{"pad":"', b'"}'
    return head + b"a" * (size - len(head) - len(tail)) + tail


def tickers_payload(n: int) -> dict:
    return {str(i): {"ticker": f"T{i}", "cik_str": i + 1} for i in range(n)}


def expire_map() -> None:
    sec._cache_loaded_at = datetime.now(timezone.utc) - timedelta(hours=25)


def seed_map(n: int, *, age_hours: float = 25) -> None:
    sec._ticker_cik_cache.clear()
    sec._ticker_cik_cache.update({f"T{i}": str(i + 1).zfill(10) for i in range(n)})
    sec._cache_loaded_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)


# =========================== CA-13: get_company_facts ===========================


async def test_fdg_ca13_build_sec_http_client_sin_redirecciones_y_con_limites():
    client = sec.build_sec_http_client()
    try:
        assert client.follow_redirects is False
        pool = client._transport._pool
        assert pool._max_connections == 4
        assert pool._max_keepalive_connections == 2
        assert client.timeout == httpx.Timeout(sec.DEFAULT_TIMEOUT)
    finally:
        await client.aclose()


async def test_fdg_ca13_company_facts_url_fija_ua_inyectado_y_timeouts():
    h = Script(json_resp({"cik": 320193, "facts": {}}))
    client, _ = make_client(h)
    data = await sec.get_company_facts(client, UA, "320193")
    assert data == {"cik": 320193, "facts": {}}
    assert h.n == 1
    req = h.requests[0]
    assert str(req.url) == FACTS_URL
    assert req.method == "GET"
    assert req.headers["User-Agent"] == UA
    assert req.extensions["timeout"]["connect"] == 5.0
    assert req.extensions["timeout"]["read"] == 15.0


async def test_fdg_ca13_urls_de_submissions_y_mapa_son_plantillas_fijas():
    h = Script(lambda r: json_resp(tickers_payload(2) if "company_tickers" in r.url.path else {"a": 1}))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert await sec.get_submissions(client, UA, "796343") == {"a": 1}
    assert [str(r.url) for r in h.requests] == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000796343.json",
    ]


async def test_fdg_ca13_streaming_con_chunks_de_65536_y_un_solo_bytearray(monkeypatch):
    tamanos = []
    tipos = []
    original = httpx.Response.aiter_bytes

    def espia(self, chunk_size=None):
        tamanos.append(chunk_size)
        return original(self, chunk_size)

    monkeypatch.setattr(httpx.Response, "aiter_bytes", espia)
    real_loads = sec._loads_dict

    def loads_espia(body):
        tipos.append(type(body))
        return real_loads(body)

    monkeypatch.setattr(sec, "_loads_dict", loads_espia)
    h = Script(httpx.Response(200, stream=TrackedStream(300_000, b" ")))
    client, _ = make_client(h)
    # 300 KB de espacios no es JSON: lo que importa es lo que se leyó y cómo.
    with pytest.raises(SecEdgarError):
        await sec.get_company_facts(client, UA, "320193")
    assert tamanos == [65536]
    assert tipos == [bytearray]


async def test_fdg_ca13_nunca_usa_raise_for_status(monkeypatch):
    def prohibido(self):
        raise AssertionError("raise_for_status prohibido en el cliente nuevo")

    monkeypatch.setattr(httpx.Response, "raise_for_status", prohibido)
    for resp in (json_resp({"ok": 1}), httpx.Response(500), httpx.Response(403), httpx.Response(404)):
        client, _ = make_client(Script(resp))
        try:
            await sec.get_company_facts(client, UA, "320193")
        except SecEdgarError:
            pass


_FALLOS = [
    ("timeout", lambda req: httpx.ReadTimeout("t", request=req)),
    ("red", lambda req: httpx.ConnectError("c", request=req)),
    ("http500", lambda req: httpx.Response(500)),
    ("http403", lambda req: httpx.Response(403)),
    ("http429", lambda req: httpx.Response(429)),
    ("http302", lambda req: httpx.Response(302, headers={"Location": "https://x.test/"})),
    ("json_roto", lambda req: httpx.Response(200, content=b"{roto")),
    ("html", lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")),
]


@pytest.mark.parametrize("fabrica", [f for _, f in _FALLOS], ids=[i for i, _ in _FALLOS])
async def test_fdg_ca13_errores_sanitizados_con_from_none(fabrica):
    def handler(request):
        item = fabrica(request)
        if isinstance(item, Exception):
            raise item
        return item

    client, _ = make_client(handler)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    exc = info.value
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__ is True  # nada de httpx (lleva la URL)
    assert "sec.gov" not in str(exc) and UA not in str(exc)
    assert exc.code in {
        "no_disponible_temporal", "bloqueado_por_sec", "redireccion_inesperada", "respuesta_inesperada",
    }


async def test_fdg_ca13_company_facts_acepta_exactamente_12_mib_y_rechaza_uno_mas():
    cuerpo = exact_json(12 * MIB)
    client, _ = make_client(Script(httpx.Response(200, content=cuerpo)))
    assert (await sec.get_company_facts(client, UA, "1"))["pad"].startswith("aaa")
    client, _ = make_client(Script(httpx.Response(200, content=exact_json(12 * MIB + 1))))
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "1")
    assert info.value.code == "respuesta_demasiado_grande"


async def test_fdg_ca13_topes_distintos_para_submissions_y_mapa():
    # submissions: 4 MiB; company_tickers.json: 2 MiB.
    client, _ = make_client(Script(httpx.Response(200, content=exact_json(4 * MIB))))
    assert await sec.get_submissions(client, UA, "5") is not None
    client, _ = make_client(Script(httpx.Response(200, content=exact_json(4 * MIB + 1))))
    assert await sec.get_submissions(client, UA, "5") is None
    # Mapa válido (AAPL se resolvería) pero de 2 MiB + 1: se corta antes de parsear.
    valido = b'{"0": {"ticker": "AAPL", "cik_str": 320193}, "pad": "' + b"a" * 100
    cuerpo = valido + b"a" * (2 * MIB + 1 - len(valido) - 2) + b'"}'
    assert len(cuerpo) == 2 * MIB + 1
    client, _ = make_client(Script(httpx.Response(200, content=cuerpo)))
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    justo = valido + b"a" * (2 * MIB - len(valido) - 2) + b'"}'
    assert len(justo) == 2 * MIB
    client, _ = make_client(Script(httpx.Response(200, content=justo)))
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") == "0000320193"


# ================================ CA-7: SPY ====================================


async def test_fdg_ca7_spy_404_xml_es_sin_facts_sin_leer_el_cuerpo():
    xml = TrackedStream(13 * MIB, b"<")
    h = Script(httpx.Response(404, headers={"content-type": "application/xml"}, stream=xml))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "884394")
    assert info.value.code == "sin_facts"
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__ is True
    assert xml.read_bytes == 0 and xml.closed
    assert h.n == 1


# ========================== CA-15: breaker en SecGate ===========================


async def _fallar(client, n=1):
    for _ in range(n):
        with pytest.raises(SecEdgarError):
            await sec.get_company_facts(client, UA, "320193")


async def test_fdg_ca15_tres_fallos_transitorios_abren_60_s_y_la_sonda_pasa():
    h = Script(httpx.Response(500), httpx.Response(500), httpx.Response(500), json_resp({"ok": 1}))
    client, clock = make_client(h)
    await _fallar(client, 2)
    assert h.n == 2
    await _fallar(client)  # el 3º abre
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto" and h.n == 3
    clock.t += 59.9
    with pytest.raises(SecEdgarError):
        await sec.get_company_facts(client, UA, "320193")
    assert h.n == 3
    clock.t += 0.1  # 60 s exactos: semi-abierto, pasa una sonda
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}
    assert h.n == 4
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}  # cerrado


async def test_fdg_ca15_timeout_y_error_de_red_cuentan_como_fallo_transitorio():
    h = Script(
        httpx.ReadTimeout("t"), httpx.ConnectError("c"), httpx.Response(502), json_resp({"ok": 1})
    )
    client, _ = make_client(h)
    await _fallar(client, 3)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto" and h.n == 3


async def test_fdg_ca15_un_exito_corta_la_racha_de_fallos():
    h = Script(
        httpx.Response(500), httpx.Response(500), json_resp({"ok": 1}),
        httpx.Response(500), httpx.Response(500), json_resp({"ok": 2}),
    )
    client, _ = make_client(h)
    await _fallar(client, 2)
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}
    await _fallar(client, 2)
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 2}
    assert h.n == 6


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(403),
        httpx.Response(429),
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
        httpx.Response(200, content=b"no es json"),
        httpx.Response(200, content=b"[1, 2]"),
    ],
    ids=["403", "429", "html", "basura", "lista"],
)
async def test_fdg_ca15_bloqueos_y_respuesta_inesperada_abren_300_s(resp):
    h = Script(resp, json_resp({"ok": 1}))
    client, clock = make_client(h)
    await _fallar(client)
    assert h.n == 1
    clock.t += 299.9
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto" and h.n == 1
    clock.t += 0.1
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}


async def test_fdg_ca15_404_y_sin_cobertura_no_cuentan_como_fallo():
    h = Script(httpx.Response(404), httpx.Response(404), httpx.Response(404), httpx.Response(404),
               json_resp({"ok": 1}))
    client, _ = make_client(h)
    for _ in range(4):
        with pytest.raises(SecEdgarError) as info:
            await sec.get_company_facts(client, UA, "320193")
        assert info.value.code == "sin_facts"
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}
    assert h.n == 5


async def test_fdg_ca15_404_intercalado_corta_la_racha():
    h = Script(httpx.Response(500), httpx.Response(500), httpx.Response(404),
               httpx.Response(500), httpx.Response(500), json_resp({"ok": 1}))
    client, _ = make_client(h)
    await _fallar(client, 5)
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}
    assert h.n == 6


async def test_fdg_ca15_semi_abierto_deja_pasar_una_sola_sonda_y_reabre_si_falla():
    gate_evento = asyncio.Event()

    async def lenta(request):
        await gate_evento.wait()
        return httpx.Response(403)

    h = Script(httpx.Response(403), lenta, json_resp({"ok": 1}))
    client, clock = make_client(h)
    await _fallar(client)
    clock.t += 300
    sonda = asyncio.create_task(sec.get_company_facts(client, UA, "320193"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert h.n == 2
    # Segundo llamador durante la sonda (otro endpoint: companyfacts serializa por semáforo).
    assert await sec.get_submissions(client, UA, "320193") is None
    assert h.n == 2
    gate_evento.set()
    with pytest.raises(SecEdgarError):
        await sonda
    # La sonda falló con 403: reabre 300 s.
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto" and h.n == 2
    clock.t += 300
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}


async def test_fdg_ca15_sonda_con_fallo_transitorio_reabre_60_s():
    h = Script(*[httpx.Response(500)] * 4, json_resp({"ok": 1}))
    client, clock = make_client(h)
    await _fallar(client, 3)
    clock.t += 60
    await _fallar(client)  # la sonda falla: un solo fallo basta para reabrir
    assert h.n == 4
    clock.t += 59.9
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto"
    clock.t += 0.1
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 1}


async def test_fdg_ca15_tras_la_sonda_exitosa_el_circuito_queda_cerrado_para_todos():
    libre = asyncio.Event()

    async def esperando(request):
        await libre.wait()
        return json_resp({"ok": 1})

    h = Script(httpx.Response(403), json_resp({"ok": 0}), esperando)
    client, clock = make_client(h)
    await _fallar(client)
    clock.t += 300
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 0}  # sonda
    tareas = [asyncio.create_task(sec.get_submissions(client, UA, str(i))) for i in range(1, 4)]
    for _ in range(30):
        await asyncio.sleep(0)
    assert h.n == 5  # las 3 llegan a la vez: ya no hay sonda única
    libre.set()
    assert await asyncio.gather(*tareas) == [{"ok": 1}] * 3


async def test_fdg_ca15_con_el_circuito_abierto_no_se_gastan_turnos_del_limitador():
    h = Script(httpx.Response(403))
    client, clock = make_client(h)
    await _fallar(client)
    for i in range(1, 21):
        assert await sec.get_submissions(client, UA, str(i)) is None
    assert h.n == 1 and clock.sleeps == []


async def test_fdg_ca15_sonda_cancelada_libera_el_semi_abierto():
    parado = asyncio.Event()

    async def cuelga(request):
        await parado.wait()
        return httpx.Response(200, json={"ok": 1})

    h = Script(httpx.Response(403), cuelga, json_resp({"ok": 2}))
    client, clock = make_client(h)
    await _fallar(client)
    clock.t += 300
    sonda = asyncio.create_task(sec.get_company_facts(client, UA, "320193"))
    for _ in range(20):
        await asyncio.sleep(0)
    sonda.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sonda
    assert await sec.get_company_facts(client, UA, "320193") == {"ok": 2}


async def test_fdg_ca15_el_breaker_cubre_mapa_submissions_y_companyfacts():
    # 403 en companyfacts bloquea mapa y submissions (un único breaker).
    h = Script(httpx.Response(403))
    client, _ = make_client(h)
    await _fallar(client)
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    assert await sec.get_submissions(client, UA, "320193") is None
    assert h.n == 1
    # 403 en submissions bloquea mapa y companyfacts.
    h = Script(httpx.Response(403))
    client, _ = make_client(h)
    assert await sec.get_submissions(client, UA, "320193") is None
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    await _fallar(client)
    assert h.n == 1
    # 403 en el mapa bloquea submissions y companyfacts.
    h = Script(httpx.Response(403))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    assert await sec.get_submissions(client, UA, "320193") is None
    await _fallar(client)
    assert h.n == 1


async def test_fdg_ca15_cada_cliente_arranca_con_estado_limpio_y_se_libera():
    h = Script(httpx.Response(403), json_resp({"ok": 1}))
    a, _ = make_client(h)
    await _fallar(a)
    b = httpx.AsyncClient(transport=httpx.MockTransport(h))
    assert sec.gate_for(b) is not sec.gate_for(a)
    assert sec.gate_for(b) is sec.gate_for(b)
    assert await sec.get_company_facts(b, UA, "320193") == {"ok": 1}
    gate = sec.gate_for(b)
    ref = weakref.ref(b)
    del b
    gc.collect()
    assert ref() is None
    assert gate not in list(sec._GATES.values())


# ======================== CA-16: limitador y concurrencia =======================


async def test_fdg_ca16_30_llamadas_simultaneas_no_superan_8_req_s():
    tiempos: list[float] = []
    clock_holder = {}

    def handler(request):
        tiempos.append(clock_holder["c"].t)
        return json_resp({"ok": 1})

    client, clock = make_client(handler)
    clock_holder["c"] = clock
    resultados = await asyncio.gather(
        *(sec.get_submissions(client, UA, str(i + 1)) for i in range(30))
    )
    assert all(r == {"ok": 1} for r in resultados)
    assert len(tiempos) == 30
    for t in tiempos:
        assert sum(1 for u in tiempos if t <= u < t + 1.0) <= 8
    assert max(tiempos) - min(tiempos) >= 2.99  # 30 peticiones a 8/s tardan ~3 s
    assert sum(clock.sleeps) == pytest.approx(max(tiempos) - min(tiempos))


async def test_fdg_ca16_hasta_8_peticiones_no_esperan():
    h = Script(json_resp({"ok": 1}))
    client, clock = make_client(h)
    await asyncio.gather(*(sec.get_submissions(client, UA, str(i + 1)) for i in range(8)))
    assert clock.sleeps == []
    await sec.get_submissions(client, UA, "99")  # la 9ª espera la ventana
    assert clock.sleeps == [pytest.approx(1.0)]


async def test_fdg_ca16_nunca_mas_de_una_descarga_mas_parseo_de_companyfacts(monkeypatch):
    estado = {"activos": 0, "max": 0, "total": 0}
    lock = threading.Lock()
    real = sec._loads_dict

    async def handler(request):
        with lock:
            estado["activos"] += 1
            estado["total"] += 1
            estado["max"] = max(estado["max"], estado["activos"])
        for _ in range(3):
            await asyncio.sleep(0)  # da oportunidad a que otros intenten entrar
        return json_resp({"ok": 1})

    def loads(body):
        try:
            return real(body)
        finally:
            with lock:
                estado["activos"] -= 1

    monkeypatch.setattr(sec, "_loads_dict", loads)
    client, _ = make_client(handler)
    resultados = await asyncio.gather(
        *(sec.get_company_facts(client, UA, "320193") for _ in range(30))
    )
    assert len(resultados) == 30 and estado["total"] == 30
    assert estado["max"] == 1


async def test_fdg_ca16_semaforo_se_libera_cuando_termina_el_hilo_no_al_cancelar(monkeypatch):
    iniciado, liberar = threading.Event(), threading.Event()
    real = sec._loads_dict

    def loads(body):
        iniciado.set()
        liberar.wait(timeout=10)
        return real(body)

    monkeypatch.setattr(sec, "_loads_dict", loads)
    h = Script(json_resp({"ok": 1}))
    client, _ = make_client(h)
    primera = asyncio.create_task(sec.get_company_facts(client, UA, "320193"))
    while not iniciado.is_set():
        await asyncio.sleep(0)
    primera.cancel()  # equivale a vencer el wait_for del proveedor
    with pytest.raises(asyncio.CancelledError):
        await primera
    segunda = asyncio.create_task(sec.get_company_facts(client, UA, "320193"))
    for _ in range(50):
        await asyncio.sleep(0)
    assert h.n == 1  # la 2ª no arrancó: el hilo de parseo de la 1ª sigue vivo
    liberar.set()
    assert await segunda == {"ok": 1}
    assert h.n == 2


# ================== T-CS-1: tickers y CIK hostiles, hosts cerrados =============

_TICKERS_HOSTILES = [
    "../x", "AAPL\n", "A@B", "ſpy", "ıbm", "ﬁ", "ABCDEFGHIJK", "", " ", "AAPL ", "Á", "AB-CDE",
    "ABCDEFG", "A--B", None, 5, "a/b", "AAPL\x00",
]
_CIK_HOSTILES = [
    "../../x", "AAPL\n", "٣", "12345678901", "", "-5", "1.5", "1e5", " 1", "1 ", "0x1", "1\n",
    None, True, 1.5, b"1", ["1"],
]


@pytest.mark.parametrize("ticker", _TICKERS_HOSTILES, ids=repr)
async def test_fdg_tcs1_ticker_hostil_no_hace_ninguna_peticion(ticker):
    h = Script(json_resp(tickers_payload(3)))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, ticker) is None
    assert h.n == 0


@pytest.mark.parametrize("cik", _CIK_HOSTILES, ids=repr)
async def test_fdg_tcs1_cik_hostil_no_hace_ninguna_peticion(cik):
    h = Script(json_resp({"ok": 1}))
    client, _ = make_client(h)
    assert await sec.get_submissions(client, UA, cik) is None
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, cik)
    assert info.value.code == "sin_cik"
    assert h.n == 0


async def test_fdg_tcs1_ticker_valido_normaliza_mayusculas_y_punto():
    payload = {"0": {"ticker": "BRK-B", "cik_str": 1067983}, "1": {"ticker": "AAPL", "cik_str": 320193}}
    h = Script(json_resp(payload))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "brk.b") == "0001067983"
    assert await sec.get_cik_for_ticker(client, UA, "BRK-B") == "0001067983"
    assert await sec.get_cik_for_ticker(client, UA, "aapl") == "0000320193"
    assert h.n == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://data.sec.gov/submissions/CIK0000320193.json",
        "https://evil.example/submissions/CIK0000320193.json",
        "https://data.sec.gov.evil.example/x",
        "https://evil.example@data.sec.gov.evil.example/x",
        "https://finnhub.io/api/v1/quote",
        "ftp://www.sec.gov/files/company_tickers.json",
    ],
)
async def test_fdg_tcs1_hosts_de_lista_cerrada_y_solo_https(url):
    h = Script(json_resp({"ok": 1}))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError):
        await sec._get_json(client, url, UA)
    assert h.n == 0


async def test_fdg_tcs1_todas_las_peticiones_usan_https_y_hosts_exactos():
    h = Script(lambda r: json_resp(tickers_payload(2) if "company_tickers" in r.url.path else {"ok": 1}))
    client, _ = make_client(h)
    await sec.get_cik_for_ticker(client, UA, "T0")
    await sec.get_submissions(client, UA, "1")
    await sec.get_company_facts(client, UA, "1")
    assert h.n == 3
    for req in h.requests:
        assert req.url.scheme == "https"
        assert req.url.host in {"data.sec.gov", "www.sec.gov"}
        assert req.url.query == b""


# ================================ T-CS-2: redirects ============================


@pytest.mark.parametrize("cliente_sigue_redirects", [False, True], ids=["cliente_no", "cliente_si"])
async def test_fdg_tcs2_302_hacia_host_externo_no_se_sigue(cliente_sigue_redirects):
    def handler(request):
        if request.url.host != "data.sec.gov":
            return json_resp({"EXFILTRADO": 1})
        return httpx.Response(302, headers={"Location": "https://evil.example/robo"})

    reqs = []

    def registrando(request):
        reqs.append(request)
        return handler(request)

    client, _ = make_client(registrando, follow_redirects=cliente_sigue_redirects)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "redireccion_inesperada"
    assert len(reqs) == 1
    assert reqs[0].url.host == "data.sec.gov"


async def test_fdg_tcs2_redireccion_en_submissions_y_mapa_tampoco_se_sigue():
    h = Script(httpx.Response(301, headers={"Location": "https://evil.example/"}))
    client, _ = make_client(h, follow_redirects=True)
    assert await sec.get_submissions(client, UA, "320193") is None
    assert h.n == 1
    h = Script(httpx.Response(307, headers={"Location": "https://evil.example/"}))
    client, _ = make_client(h, follow_redirects=True)
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    assert h.n == 1


# ================================ T-CS-3: 13 MiB ===============================


async def test_fdg_tcs3_stream_falso_de_13_mib_se_corta():
    cuerpo = TrackedStream(13 * MIB)
    h = Script(httpx.Response(200, headers={"content-type": "application/json"}, stream=cuerpo))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "respuesta_demasiado_grande"
    assert cuerpo.read_bytes <= 12 * MIB + 65536  # nunca se lee hasta el final
    assert cuerpo.read_bytes < 13 * MIB
    assert cuerpo.closed


# =============================== T-CS-4: logs ==================================

_CANARIO = "CUERPO_SECRETO_ZZZ"


def _escenarios_de_log():
    def stream_grande(request):
        return httpx.Response(200, stream=TrackedStream(13 * MIB, b"S"))

    return [
        ("timeout", lambda r: httpx.ReadTimeout(_CANARIO, request=r)),
        ("403", lambda r: httpx.Response(403, text=_CANARIO)),
        ("429", lambda r: httpx.Response(429, text=_CANARIO)),
        ("5xx", lambda r: httpx.Response(503, text=_CANARIO)),
        ("json_roto", lambda r: httpx.Response(200, content=_CANARIO.encode())),
        ("tope", stream_grande),
        ("html", lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=_CANARIO)),
        ("red", lambda r: httpx.ConnectError(_CANARIO, request=r)),
        ("redirect", lambda r: httpx.Response(302, headers={"Location": _CANARIO}, text=_CANARIO)),
    ]


@pytest.mark.parametrize("fabrica", [f for _, f in _escenarios_de_log()], ids=[i for i, _ in _escenarios_de_log()])
async def test_fdg_tcs4_logs_sin_user_agent_ni_cuerpo(fabrica, caplog):
    def handler(request):
        item = fabrica(request)
        if isinstance(item, Exception):
            raise item
        return item

    client, _ = make_client(handler)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SecEdgarError) as info:
            await sec.get_company_facts(client, UA, "320193")
        await sec.get_submissions(client, UA, "320193")
        await sec.get_cik_for_ticker(client, UA, "AAPL")
    textos = caplog.text + " ".join(str(r.args) for r in caplog.records)
    for prohibido in (UA, "contacto@ejemplo.test", "ejemplo.test", _CANARIO, "User-Agent", "SSSSSSSS"):
        assert prohibido not in textos
        assert prohibido not in str(info.value)
    assert "url=" in caplog.text


# ============================= T-CS-14: mapa validado ==========================


def _mapa(entradas: list[dict]) -> dict:
    return {str(i): e for i, e in enumerate(entradas)}


async def _cargar(entradas: list[dict], ticker: str = "AAPL"):
    h = Script(json_resp(_mapa(entradas)))
    client, clock = make_client(h)
    cik = await sec.get_cik_for_ticker(client, UA, ticker)
    return cik, h, client, clock


@pytest.mark.parametrize(
    "ticker_malo", ["ſpy", "ıbm", "ﬁ", "AAPL\n", "A@B", "ABCDEFGHIJK", "", "../x", "A B", "ABCDEFG", 7, None],
    ids=repr,
)
async def test_fdg_tcs14_tickers_hostiles_del_mapa_se_descartan(ticker_malo):
    cik, _, _, _ = await _cargar(
        [{"ticker": "AAPL", "cik_str": 320193}, {"ticker": ticker_malo, "cik_str": 111}]
    )
    assert cik == "0000320193"
    assert list(sec._ticker_cik_cache) == ["AAPL"]


@pytest.mark.parametrize(
    "cik_malo",
    [True, False, "../../x", "12345678901234", 12345678901234, -5, 0, 10_000_000_000, 1.5, "٣", "", None, "1\n", " 7", [1]],
    ids=repr,
)
async def test_fdg_tcs14_cik_str_invalidos_se_descartan(cik_malo):
    cik, _, _, _ = await _cargar(
        [{"ticker": "AAPL", "cik_str": 320193}, {"ticker": "EVIL", "cik_str": cik_malo}]
    )
    assert cik == "0000320193"
    assert "EVIL" not in sec._ticker_cik_cache


async def test_fdg_tcs14_cik_str_validos_y_ticker_en_minusculas():
    cik, _, _, _ = await _cargar(
        [
            {"ticker": "aapl", "cik_str": 320193},
            {"ticker": "MIN", "cik_str": 1},
            {"ticker": "MAX", "cik_str": 9_999_999_999},
            {"ticker": "STR", "cik_str": "0000796343"},
            {"ticker": "STR2", "cik_str": "5"},
            {"ticker": "BRK.B", "cik_str": 1067983},
        ]
    )
    assert cik == "0000320193"
    assert sec._ticker_cik_cache == {
        "AAPL": "0000320193", "MIN": "0000000001", "MAX": "9999999999",
        "STR": "0000796343", "STR2": "0000000005", "BRK-B": "0001067983",
    }


async def test_fdg_tcs14_mapa_envenenado_no_genera_peticiones_a_submissions_ni_companyfacts():
    envenenado = [
        {"ticker": "EVIL", "cik_str": "../../x"},
        {"ticker": "EVIL2", "cik_str": True},
        {"ticker": "EVIL3", "cik_str": "٣"},
        {"ticker": "ſpy", "cik_str": 5},
        {"ticker": "OK", "cik_str": 7},
    ]
    h = Script(json_resp(_mapa(envenenado)))
    client, _ = make_client(h)
    for t in ("EVIL", "EVIL2", "EVIL3", "ſpy", "SPY"):
        cik = await sec.get_cik_for_ticker(client, UA, t)
        assert cik is None
        if cik:  # pragma: no cover - solo si el mapa fallara
            await sec.get_submissions(client, UA, cik)
    assert h.paths == ["/files/company_tickers.json"]
    assert not any("/submissions/" in p or "/companyfacts/" in p for p in h.paths)


async def test_fdg_tcs14_ticker_repetido_con_cik_distinto_se_descarta():
    cik, _, client, _ = await _cargar(
        [
            {"ticker": "DUP", "cik_str": 1},
            {"ticker": "DUP", "cik_str": 2},
            {"ticker": "DUP", "cik_str": 1},  # tercero, tras el conflicto: sigue descartado
            {"ticker": "SAME", "cik_str": 3},
            {"ticker": "SAME", "cik_str": "3"},  # mismo CIK: no es conflicto
            {"ticker": "AAPL", "cik_str": 320193},
        ]
    )
    assert cik == "0000320193"
    assert "DUP" not in sec._ticker_cik_cache
    assert sec._ticker_cik_cache["SAME"] == "0000000003"
    assert await sec.get_cik_for_ticker(client, UA, "DUP") is None


async def test_fdg_tcs14_tope_de_entradas_y_conteo_sin_valores(monkeypatch, caplog):
    monkeypatch.setattr(sec, "_MAX_ENTRADAS_MAPA", 3)
    entradas = [{"ticker": f"T{i}", "cik_str": i + 1} for i in range(6)] + [{"ticker": "ſ", "cik_str": 1}]
    h = Script(json_resp(_mapa(entradas)))
    client, _ = make_client(h)
    with caplog.at_level(logging.DEBUG):
        assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert sorted(sec._ticker_cik_cache) == ["T0", "T1", "T2"]
    assert sec.gate_for(client).map_rejected == 4
    assert "4 entradas descartadas" in caplog.text
    assert "ſ" not in caplog.text


async def test_fdg_tcs14_carga_inicial_de_2_entradas_se_acepta():
    cik, _, _, _ = await _cargar(
        [{"ticker": "AAPL", "cik_str": 320193}, {"ticker": "ADBE", "cik_str": 796343}]
    )
    assert cik == "0000320193" and len(sec._ticker_cik_cache) == 2
    assert sec._MIN_ENTRADAS_REFRESCO == 1000


async def test_fdg_tcs14_primera_carga_sin_ninguna_entrada_valida_se_rechaza():
    h = Script(json_resp(_mapa([{"ticker": "../x", "cik_str": 1}])), json_resp(tickers_payload(2)))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") is None
    assert sec._ticker_cik_cache == {} and sec._cache_loaded_at is None
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"  # sin limitar reintentos
    assert h.n == 2


async def test_fdg_tcs14_refresco_con_previo_de_1000_y_respuesta_de_3_conserva_el_previo():
    seed_map(1200)
    antes = sec._cache_loaded_at
    h = Script(json_resp(tickers_payload(3)))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T1199") == "0000001200"
    assert len(sec._ticker_cik_cache) == 1200 and sec._cache_loaded_at == antes
    assert h.n == 1


@pytest.mark.parametrize(
    "previo,nuevo,aceptado",
    [(1000, 999, False), (1000, 1000, True), (1200, 1000, True), (999, 3, True), (2, 1, True)],
)
async def test_fdg_tcs14_umbral_de_refresco_es_1000_con_previo_de_1000_o_mas(previo, nuevo, aceptado):
    seed_map(previo)
    h = Script(json_resp({str(i): {"ticker": f"N{i}", "cik_str": i + 1} for i in range(nuevo)}))
    client, _ = make_client(h)
    await sec.get_cik_for_ticker(client, UA, "N0")
    assert (len(sec._ticker_cik_cache) == nuevo) is aceptado
    assert ("N0" in sec._ticker_cik_cache) is aceptado


# =============================== T-CS-17: lectura ==============================


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8", "TEXT/HTML", "application/xhtml+xml"])
async def test_fdg_tcs17_200_html_se_rechaza_sin_leer_el_cuerpo(ctype):
    cuerpo = TrackedStream(5 * MIB, b"<")
    h = Script(httpx.Response(200, headers={"content-type": ctype}, stream=cuerpo))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "respuesta_inesperada"
    assert cuerpo.read_bytes == 0 and cuerpo.closed


async def test_fdg_tcs17_200_sin_content_type_con_json_valido_se_acepta():
    h = Script(httpx.Response(200, content=b'{"cik": 320193}'))
    client, _ = make_client(h)
    assert "content-type" not in h(httpx.Request("GET", "https://data.sec.gov/")).headers  # sanity
    assert await sec.get_company_facts(client, UA, "320193") == {"cik": 320193}


@pytest.mark.parametrize("ctype", ["text/plain", "application/octet-stream", "application/json"])
async def test_fdg_tcs17_content_type_es_informativo_para_json_valido(ctype):
    h = Script(httpx.Response(200, headers={"content-type": ctype}, content=b'{"a": 1}'))
    client, _ = make_client(h)
    assert await sec.get_company_facts(client, UA, "320193") == {"a": 1}


@pytest.mark.parametrize("cuerpo", [b"basura <html>", b"", b"[1]", b'"texto"', b"\xff\xfe\x00"])
async def test_fdg_tcs17_200_sin_content_type_con_basura_es_respuesta_inesperada(cuerpo):
    h = Script(httpx.Response(200, content=cuerpo), json_resp({"ok": 1}))
    client, clock = make_client(h)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "respuesta_inesperada"
    with pytest.raises(SecEdgarError) as info:  # y abrió el breaker
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto" and h.n == 1


@pytest.mark.parametrize("status", [302, 403, 429, 500, 404, 410, 400])
async def test_fdg_tcs17_estados_distintos_de_200_no_leen_el_cuerpo(status):
    cuerpo = TrackedStream(13 * MIB, b"<")
    headers = {"Location": "https://data.sec.gov/otra"} if status == 302 else {}
    h = Script(httpx.Response(status, headers=headers, stream=cuerpo))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError):
        await sec.get_company_facts(client, UA, "320193")
    assert cuerpo.read_bytes == 0 and cuerpo.closed
    assert h.n == 1


async def test_fdg_tcs17_stream_de_13_mib_no_se_lee_completo():
    cuerpo = TrackedStream(13 * MIB, b" ")
    h = Script(httpx.Response(200, stream=cuerpo))
    client, _ = make_client(h)
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "respuesta_demasiado_grande"
    assert cuerpo.read_bytes <= 12 * MIB + 65536


async def test_fdg_tcs17_otros_4xx_y_5xx_son_fallo_transitorio():
    h = Script(httpx.Response(400), httpx.Response(410), httpx.Response(504), json_resp({"ok": 1}))
    client, _ = make_client(h)
    for _ in range(3):
        with pytest.raises(SecEdgarError) as info:
            await sec.get_company_facts(client, UA, "320193")
        assert info.value.code == "no_disponible_temporal"
    with pytest.raises(SecEdgarError) as info:
        await sec.get_company_facts(client, UA, "320193")
    assert info.value.code == "circuito_abierto"


# =============================== T-CS-18: mapa y breaker =======================


async def test_fdg_tcs18_5_consultas_con_mapa_expirado_y_un_403_hacen_1_peticion():
    seed_map(3)
    h = Script(httpx.Response(403))
    client, clock = make_client(h)
    for _ in range(5):
        assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"  # mapa viejo
    assert h.n == 1
    assert len(sec._ticker_cik_cache) == 3


async def test_fdg_tcs18_5_consultas_sin_mapa_previo_y_un_403_hacen_1_peticion():
    h = Script(httpx.Response(403))
    client, _ = make_client(h)
    for _ in range(5):
        assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    assert h.n == 1


async def test_fdg_tcs18_el_8k_respeta_el_breaker_abierto():
    h = Script(httpx.Response(403))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "AAPL") is None
    assert await sec.get_submissions(client, UA, "320193") is None
    assert h.n == 1


async def test_fdg_tcs18_el_refresco_del_mapa_respeta_un_breaker_abierto_por_otro_endpoint():
    seed_map(3)
    h = Script(httpx.Response(403))
    client, clock = make_client(h)
    assert await sec.get_submissions(client, UA, "1") is None  # abre el breaker
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert h.n == 1


async def test_fdg_tcs18_sin_mapa_previo_un_fallo_aislado_no_limita_reintentos():
    h = Script(httpx.Response(500), json_resp(tickers_payload(2)))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") is None
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert h.n == 2


async def test_fdg_tcs18_con_mapa_previo_no_se_reintenta_hasta_pasados_5_minutos():
    seed_map(3)
    h = Script(httpx.Response(500), json_resp(tickers_payload(2)))
    client, clock = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert h.n == 1
    clock.t += 299
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert h.n == 1
    clock.t += 1
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"
    assert h.n == 2
    assert sorted(sec._ticker_cik_cache) == ["T0", "T1"]  # el refresco nuevo se aplicó


async def test_fdg_tcs18_refresco_rechazado_por_tamano_tambien_espera_5_minutos():
    seed_map(1200)
    h = Script(json_resp(tickers_payload(3)))
    client, clock = make_client(h)
    for _ in range(3):
        await sec.get_cik_for_ticker(client, UA, "T0")
    assert h.n == 1
    clock.t += 300
    await sec.get_cik_for_ticker(client, UA, "T0")
    assert h.n == 2


async def test_fdg_tcs18_mapa_de_mas_de_7_dias_no_se_sirve_si_el_refresco_falla():
    seed_map(3, age_hours=24 * 7 + 1)
    h = Script(httpx.Response(500))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") is None
    assert await sec.get_cik_for_ticker(client, UA, "T0") is None
    assert h.n == 2  # sin mapa servible no hay temporizador


async def test_fdg_tcs18_mapa_de_7_dias_exactos_si_se_sirve():
    seed_map(3, age_hours=24 * 7 - 1)
    h = Script(httpx.Response(500))
    client, _ = make_client(h)
    assert await sec.get_cik_for_ticker(client, UA, "T0") == "0000000001"


async def test_fdg_tcs18_el_temporizador_se_libera_cuando_el_breaker_cierra():
    seed_map(3)
    h = Script(httpx.Response(500), httpx.Response(500), httpx.Response(500), json_resp({"ok": 1}),
               json_resp(tickers_payload(2)))
    client, clock = make_client(h)
    await sec.get_cik_for_ticker(client, UA, "T0")  # 500 del mapa: temporizador de 5 min
    assert await sec.get_submissions(client, UA, "1") is None
    assert await sec.get_submissions(client, UA, "2") is None  # 3º fallo: breaker 60 s
    assert sec.gate_for(client).map_retry_at is not None
    clock.t += 60
    assert await sec.get_submissions(client, UA, "3") == {"ok": 1}  # la sonda cierra
    assert sec.gate_for(client).map_retry_at is None
    await sec.get_cik_for_ticker(client, UA, "T0")
    assert h.n == 5  # reintentó el mapa sin esperar los 5 min


async def test_fdg_tcs18_refrescos_simultaneos_del_mapa_comparten_una_peticion():
    seed_map(3)
    libre = asyncio.Event()

    async def handler(request):
        await libre.wait()
        return json_resp(tickers_payload(5))

    h = Script(handler)
    client, _ = make_client(h)
    tareas = [asyncio.create_task(sec.get_cik_for_ticker(client, UA, "T4")) for _ in range(5)]
    for _ in range(20):
        await asyncio.sleep(0)
    libre.set()
    assert await asyncio.gather(*tareas) == ["0000000005"] * 5
    assert h.n == 1


# ========================== submissions: caché 1 h y single-flight =============


async def test_fdg_ca15_submissions_cacheado_1_hora():
    h = Script(json_resp({"filings": 1}))
    client, clock = make_client(h)
    assert await sec.get_submissions(client, UA, "320193") == {"filings": 1}
    clock.t += 3599
    assert await sec.get_submissions(client, UA, "320193") == {"filings": 1}
    assert h.n == 1
    clock.t += 1
    await sec.get_submissions(client, UA, "320193")
    assert h.n == 2


async def test_fdg_ca15_submissions_fallo_no_se_cachea():
    h = Script(httpx.Response(500), json_resp({"filings": 1}))
    client, _ = make_client(h)
    assert await sec.get_submissions(client, UA, "1") is None
    assert await sec.get_submissions(client, UA, "1") == {"filings": 1}
    assert h.n == 2


async def test_fdg_ca15_submissions_single_flight_y_cancelacion_de_un_waiter():
    libre = asyncio.Event()

    async def handler(request):
        await libre.wait()
        return json_resp({"filings": 1})

    h = Script(handler)
    client, _ = make_client(h)
    tareas = [asyncio.create_task(sec.get_submissions(client, UA, "320193")) for _ in range(5)]
    for _ in range(20):
        await asyncio.sleep(0)
    tareas[0].cancel()  # un waiter cancelado no cancela la descarga compartida
    libre.set()
    resultados = await asyncio.gather(*tareas, return_exceptions=True)
    assert isinstance(resultados[0], asyncio.CancelledError)
    assert resultados[1:] == [{"filings": 1}] * 4
    assert h.n == 1
    assert sec.gate_for(client).inflight == {}


async def test_fdg_ca15_submissions_single_flight_con_error_llega_a_todos_los_waiters():
    libre = asyncio.Event()

    async def handler(request):
        await libre.wait()
        return httpx.Response(500)

    h = Script(handler)
    client, _ = make_client(h)
    tareas = [asyncio.create_task(sec.get_submissions(client, UA, "7")) for _ in range(3)]
    for _ in range(20):
        await asyncio.sleep(0)
    libre.set()
    assert await asyncio.gather(*tareas) == [None, None, None]
    assert h.n == 1


async def test_fdg_ca15_cache_de_submissions_acotada(monkeypatch):
    monkeypatch.setattr(sec, "_SUBMISSIONS_CACHE_MAX", 4)
    h = Script(json_resp({"filings": 1}))
    client, _ = make_client(h)
    for i in range(1, 8):
        await sec.get_submissions(client, UA, str(i))
    assert list(sec.gate_for(client).submissions) == [str(i).zfill(10) for i in (4, 5, 6, 7)]


async def test_fdg_ca15_single_flight_sin_waiters_no_deja_excepciones_sin_recuperar():
    libre = asyncio.Event()

    async def handler(request):
        await libre.wait()
        return httpx.Response(500)

    h = Script(handler)
    client, _ = make_client(h)
    tarea = asyncio.create_task(sec.get_submissions(client, UA, "9"))
    for _ in range(20):
        await asyncio.sleep(0)
    tarea.cancel()
    libre.set()
    for _ in range(50):
        await asyncio.sleep(0)
    assert sec.gate_for(client).inflight == {}
