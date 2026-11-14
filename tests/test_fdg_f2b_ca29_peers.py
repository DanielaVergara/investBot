"""F2b de SDD_fuentes_datos_gratuitas.md: CA-29 (PER de peers por `earningsYield = netIncome TTM (EDGAR) /
marketCap (Finnhub)`, `peers.py` sin cambios) y cobertura de `get_peers` con el breaker / kill-switch de
Finnhub (13.4). Un despachador por host, datos SINTETICOS; sin red ni tiempo reales."""

from __future__ import annotations

import httpx
import pytest

from investbot import finnhub_client, peers, rules
from investbot.edgar_xbrl import parse_company_facts

from .edgar_helpers import companyfacts
from .fdg_f2b_helpers import PEER_DATA, World, build_clients, finnhub_profile
from .test_edgar_xbrl import CIK, HOY, M, empresa, flujo, sin_tag
from .test_fdg_f1b3_edgar_provider import _reset_global_map  # noqa: F401
from .test_fdg_f2b_ca28_integracion import _analizar, _espiar
from .test_fdg_f2b_ca28_integracion import pytestmark  # noqa: F401


def _ni_ttm(hechos=None, cik=CIK) -> float:
    doc = companyfacts(*(hechos or empresa()), cik=cik)
    salida = parse_company_facts(doc, quarters=12, today=HOY, expected_cik=str(cik))
    assert salida.code is None
    ttm = rules.calculate_income_statement_ttm(salida.result.income_q)
    assert ttm.disponible
    return ttm.net_income_ttm


def _con_perdidas():
    anios = (2023, 2024, 2025)
    return sin_tag(empresa(), "NetIncomeLoss") + [h for y in anios for h in flujo("NetIncomeLoss", y, [-100 * M] * 4)]


# --- CA-29: earningsYield propio -----------------------------------------------------------------------------------


async def test_fdg_ca29_el_per_de_cada_peer_sale_de_la_utilidad_ttm_de_edgar_y_la_capitalizacion_de_finnhub(monkeypatch):
    world = World(fmp="down")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["fuente_peers"] == peers.PEERS_FUENTE_FINNHUB and pc["peers_usados"] == list(PEER_DATA)
    ni = _ni_ttm()
    for peer, (_cik, cap_m) in PEER_DATA.items():
        earnings_yield = ni / (cap_m * 1e6)
        assert pc["peers_pe"][peer] == pytest.approx(1.0 / earnings_yield)  # peers.py: PER = 1 / earningsYield
    assert pc["peers_no_usados"] == {}
    # ni una sola llamada a FMP (ni key-metrics) y el aviso del texto es el nuevo
    assert world.host_requests("financialmodelingprep.com") == []


async def test_fdg_ca29_el_texto_declara_que_el_earnings_yield_de_los_peers_lo_calculo_el_bot():
    clients, _ = build_clients(World(fmp="down"))
    parts, _ = await _analizar(clients)
    texto = "\n".join(parts)
    assert "utilidad TTM de la SEC / capitalización de Finnhub" in texto
    assert "earningsYield sí es un dato de FMP, el PER no" not in texto


async def test_fdg_ca29_un_peer_con_perdidas_ttm_es_earnings_yield_no_positivo(monkeypatch):
    world = World(fmp="down")
    cik_b = str(PEER_DATA["PEERB"][0]).zfill(10)
    world.sec_server.facts[cik_b] = companyfacts(*_con_perdidas(), cik=PEER_DATA["PEERB"][0])
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["peers_no_usados"] == {"PEERB": peers.PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO}
    assert "PEERB" not in pc["peers_pe"] and pc["peers_usados"] == ["PEERA", "PEERC"]


async def test_fdg_ca29_peer_sin_marketcap_es_sin_dato_nunca_un_per_inventado(monkeypatch):
    """Mutacion: aceptar `marketCap=None` dejaria un earningsYield (o un crash) en vez de `sin_dato`."""
    world = World(fmp="down")
    world.cap_overrides["PEERA"] = None
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["peers_no_usados"] == {"PEERA": peers.PEER_MOTIVO_SIN_DATO}
    assert "PEERA" not in pc["peers_pe"] and set(pc["peers_pe"]) == {"PEERB", "PEERC"}
    assert parts and "No pude obtener" not in parts[0]


async def test_fdg_ca29_peer_sin_marketcap_cae_al_key_metrics_de_fmp_si_fmp_responde(monkeypatch):
    world = World(fmp="ok")
    world.cap_overrides["PEERA"] = None
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["peers_pe"]["PEERA"] == pytest.approx(20.0)  # FMP: earningsYield 0.05
    assert world.paths("financialmodelingprep.com").count("/stable/key-metrics") == 1


async def test_fdg_ca29_peer_sin_cobertura_de_la_sec_cae_a_fmp_y_si_fmp_cae_es_sin_dato(monkeypatch):
    world = World(fmp="down")
    del world.sec_server.facts[str(PEER_DATA["PEERC"][0]).zfill(10)]  # 404 de companyfacts
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["peers_no_usados"] == {"PEERC": peers.PEER_MOTIVO_SIN_DATO}
    assert set(pc["peers_pe"]) == {"PEERA", "PEERB"}


async def test_fdg_ca29_sin_capa_de_mercado_los_peers_siguen_con_el_key_metrics_de_fmp_de_siempre(monkeypatch):
    world = World(fmp="ok")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world, market=False)
    await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["fuente_peers"] == peers.PEERS_FUENTE_FINNHUB  # peers dinamicos como hoy (sin breaker)
    assert world.paths("finnhub.io") == ["/api/v1/stock/peers"]  # NADA de quote/profile2/metric
    assert finnhub_client.gate_for(clients.finnhub_http).open_until is None
    assert all(v == pytest.approx(20.0) for v in pc["peers_pe"].values())
    assert cap["market_provenance"] is None


async def test_fdg_ca29_con_el_servicio_de_estados_ausente_el_peer_cae_a_fmp():
    import dataclasses

    world = World(fmp="ok")
    clients, _ = build_clients(world)
    sin_servicio = dataclasses.replace(clients, fundamentals=None)
    from investbot import query_handler

    assert await query_handler._peer_earnings_yield(sin_servicio, "PEERA", None) is None
    sin_finnhub = dataclasses.replace(clients, finnhub_http=None)
    assert await query_handler._peer_earnings_yield(sin_finnhub, "PEERA", None) is None


async def test_fdg_ca29_un_fallo_inesperado_del_servicio_o_de_finnhub_en_un_peer_nunca_rompe_el_analisis():
    from investbot import query_handler

    world = World(fmp="down")
    clients, _ = build_clients(world)

    class Roto:
        async def get(self, *a, **k):
            raise RuntimeError("boom")

    import dataclasses

    assert await query_handler._peer_earnings_yield(dataclasses.replace(clients, fundamentals=Roto()), "PEERA", None) is None
    # utilidad TTM no disponible (el paquete de EDGAR no trae 4 trimestres) -> None
    class SinTtm:
        async def get(self, *a, **k):
            from .fdg_f1b3_helpers import make_result

            return make_result("sec_edgar")

    assert await query_handler._peer_earnings_yield(dataclasses.replace(clients, fundamentals=SinTtm()), "PEERA", None) is None
    # el servicio responde con otra fuente -> None
    class DeFmp:
        async def get(self, *a, **k):
            from .fdg_f1b3_helpers import make_result

            return make_result("fmp")

    assert await query_handler._peer_earnings_yield(dataclasses.replace(clients, fundamentals=DeFmp()), "PEERA", None) is None
    class Ninguno:
        async def get(self, *a, **k):
            return None

    assert await query_handler._peer_earnings_yield(dataclasses.replace(clients, fundamentals=Ninguno()), "PEERA", None) is None
    # profile2 con rechazo real de Finnhub (lanza FinnhubError) -> None
    world.finnhub_status = 429
    assert await query_handler._peer_earnings_yield(clients, "PEERA", None) is None


# --- 13.4 / CS-27: get_peers dentro del breaker de Finnhub ------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 402, 403, 429])
async def test_fdg_ca29_un_rechazo_en_peers_abre_el_breaker_y_peers_cae_a_la_lista_fija(status, monkeypatch):
    world = World(fmp="ok")
    world.peers_status = status
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    pc = cap["peer_comparison"]
    assert pc["fuente_peers"] == peers.PEERS_FUENTE_FIJO  # peers no es fatal: lista fija del sector (SIC -> Technology)
    assert set(pc["peers_usados"]) == set(peers.PEERS_BY_SECTOR["Technology"])
    assert parts and "No pude obtener" not in parts[0]
    gate = finnhub_client.gate_for(clients.finnhub_http)
    assert gate.open_until is not None and gate.open_until > gate.now()
    assert "/api/v1/stock/peers" in world.paths("finnhub.io")


async def test_fdg_ca29_tras_el_rechazo_de_peers_la_siguiente_consulta_degrada_a_fmp_con_aviso(monkeypatch):
    world = World(fmp="ok")
    world.peers_status = 429
    clients, _ = build_clients(world)
    await _analizar(clients)
    antes = len(world.host_requests("finnhub.io"))
    cap = _espiar(monkeypatch)
    parts, sink = await _analizar(clients)
    assert len(world.host_requests("finnhub.io")) == antes  # breaker abierto: cero llamadas a Finnhub
    texto = "\n".join(parts)
    prov = cap["market_provenance"]
    assert prov["sources"]["quote"] == prov["sources"]["profile"] == prov["sources"]["metrics"] == "fmp"
    assert "circuito_abierto" in prov["codes"]
    assert f"_{__import__('investbot').summary.AVISO_FINNHUB_BLOQUEADO}_" in texto.split("\n")
    assert sink["market_provenance"]["sources"]["vix"] == "fred"  # FRED sigue sirviendo el VIX


async def test_fdg_ca29_un_5xx_en_peers_no_abre_el_breaker_de_inmediato(monkeypatch):
    world = World(fmp="ok")
    world.peers_status = 500
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    assert cap["peer_comparison"]["fuente_peers"] == peers.PEERS_FUENTE_FIJO
    assert finnhub_client.gate_for(clients.finnhub_http).open_until is None


async def test_fdg_ca29_peers_dinamicos_de_menos_de_tres_caen_a_la_lista_fija(monkeypatch):
    world = World(fmp="ok")
    world.peers_list = ["PEERA"]
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    await _analizar(clients)
    assert cap["peer_comparison"]["fuente_peers"] == peers.PEERS_FUENTE_FIJO


# --- finnhub_client.get_peers(guarded=True) -----------------------------------------------------------------------------


def _http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fdg_ca25_get_peers_guarded_devuelve_la_lista_y_usa_el_header():
    seen = {}

    def h(request):
        seen["h"] = request.headers.get("X-Finnhub-Token")
        seen["q"] = dict(request.url.params)
        return httpx.Response(200, json=["A", 1, "B"])

    got = await finnhub_client.get_peers(_http(h), "k" * 20, "ADBE", guarded=True, grouping="sector")
    assert got == ["A", "B"] and seen["h"] == "k" * 20 and seen["q"] == {"symbol": "ADBE", "grouping": "sector"}


@pytest.mark.parametrize("body", [{"a": 1}, "x", 5])
async def test_fdg_ca25_get_peers_guarded_con_cuerpo_que_no_es_lista_es_vacio(body):
    assert await finnhub_client.get_peers(_http(lambda r: httpx.Response(200, json=body)), "k" * 20, "ADBE", guarded=True) == []


async def test_fdg_ca25_get_peers_guarded_con_simbolo_o_key_invalidos_no_hace_peticion():
    llamadas = []

    def h(request):
        llamadas.append(request)
        return httpx.Response(200, json=["A"])

    http = _http(h)
    assert await finnhub_client.get_peers(http, "k" * 20, "../x", guarded=True) == []
    assert await finnhub_client.get_peers(http, "bad key\n", "ADBE", guarded=True) == []
    assert llamadas == []


async def test_fdg_ca25_get_peers_guarded_errores_reales_siguen_siendo_finnhub_error_sin_filtrar_la_key():
    key = "SECRETO-KEY-0123456789"
    for status in (402, 500):
        with pytest.raises(finnhub_client.FinnhubError) as exc:
            await finnhub_client.get_peers(_http(lambda r, s=status: httpx.Response(s)), key, "ADBE", guarded=True)
        assert key not in str(exc.value) and exc.value.code in finnhub_client.FINNHUB_CODES


async def test_fdg_ca25_get_peers_sin_guarded_no_toca_el_estado_compartido():
    http = _http(lambda r: httpx.Response(429))
    with pytest.raises(finnhub_client.FinnhubError):
        await finnhub_client.get_peers(http, "k" * 20, "ADBE")
    assert finnhub_client.gate_for(http).open_until is None  # comportamiento de siempre
    with pytest.raises(finnhub_client.FinnhubError):
        await finnhub_client.get_peers(http, "k" * 20, "ADBE", guarded=True)
    assert finnhub_client.gate_for(http).open_until is not None
    assert await finnhub_client.get_peers(http, "k" * 20, "ADBE", guarded=True) == []  # breaker abierto: lista vacia
