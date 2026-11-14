"""F2b de SDD_fuentes_datos_gratuitas.md: unidades de la conexion (`market_data.compose`,
`build_fmp_fallbacks`, respaldo por consulta), cableado en `bot.py`, plantillas de procedencia en `summary`
y casos limite del flujo (capitalizacion N/D). Sin red ni tiempo reales."""

from __future__ import annotations

import httpx
import pytest

from investbot import bot, finnhub_client, market_data, summary
from investbot import market_data as md
from investbot.fmp_client import FMPError

from .fdg_f2a_helpers import KEY, WALL0, Clock, Recorder
from .fdg_f2b_helpers import World, build_clients, finnhub_profile, fmp_profile, fmp_quote
from .test_fdg_f1b2_hardening import _env_main, _spy_main
from .test_fdg_f1b3_edgar_provider import _reset_global_map  # noqa: F401
from .test_fdg_f2b_ca28_integracion import _analizar
from .test_fdg_f2b_ca28_integracion import pytestmark  # noqa: F401

N = md.Block


def _snap(quote=None, profile=None, metrics=None, vix=None):
    def blk(name, spec):
        if spec is None:
            return N(name, None, None, ())
        source, data = spec
        return N(name, source, data)

    return md.MarketSnapshot("SYNT", blk("quote", quote), blk("profile", profile), blk("metrics", metrics), blk("vix", vix))


# --- compose -----------------------------------------------------------------------------------------------------------


def test_fdg_ca28_compose_finnhub_arma_quote_y_perfil_con_las_claves_de_fmp_sin_promedios_moviles():
    snap = _snap(
        quote=("finnhub", {"price": 187.5, "timestamp": 1}),
        profile=("finnhub", {"companyName": "Sint", "marketCap": 2e9, "currency": "USD"}),
        metrics=("finnhub", {"beta": 1.2, "yearHigh": 200.0, "yearLow": 120.0}),
        vix=("fred", {"value": 17.5}),
    )
    c = md.compose(snap, sic="7372", sic_description="Services-Prepackaged Software")
    assert c.quote == {"price": 187.5, "yearHigh": 200.0, "yearLow": 120.0}
    assert "priceAvg50" not in c.quote and "priceAvg200" not in c.quote
    assert c.profile == {"companyName": "Sint", "marketCap": 2e9, "beta": 1.2, "sector": "Technology",
                         "industry": "Services-Prepackaged Software"}
    assert c.vix_quote == {"price": 17.5}
    assert c.provenance == {"sources": {"quote": "finnhub", "profile": "finnhub", "metrics": "finnhub", "vix": "fred"},
                            "codes": ()}


def test_fdg_ca28_compose_fmp_conserva_su_dict_crudo_y_su_sector_gana_sobre_la_tabla_sic():
    q, p = fmp_quote("X"), fmp_profile("X")
    snap = _snap(quote=("fmp", q), profile=("fmp", p), metrics=("fmp", {"beta": 1.1, "yearHigh": 210.0, "yearLow": 110.0}))
    c = md.compose(snap, sic="7372", sic_description="Services-Prepackaged Software")
    assert c.quote == q and c.quote["priceAvg50"] == 180.0
    assert c.profile["sector"] == "Healthcare" and c.profile["companyName"] == "FMP Sintetica SA"
    assert c.vix_quote is None
    assert q is not c.quote  # copia: no muta lo recibido


def test_fdg_ca28_compose_nunca_completa_un_bloque_vacio_con_beta_52_semanas_o_sic():
    snap = _snap(metrics=("finnhub", {"beta": 1.2, "yearHigh": 200.0, "yearLow": 120.0}))
    c = md.compose(snap, sic="7372", sic_description="Services-Prepackaged Software")
    assert c.quote == {} and c.profile == {}  # N/D: el analisis abortara con su mensaje de siempre


def test_fdg_ca28_compose_sic_hostil_o_ambiguo_no_agrega_sector_ni_texto_de_la_red():
    snap = _snap(quote=("finnhub", {"price": 1.0}), profile=("finnhub", {"companyName": "S", "marketCap": None}))
    for sic in ("7389", "../x", None, 7372.0):
        assert "sector" not in md.compose(snap, sic=sic).profile
    # industria: texto de la red acotado y sin caracteres de control
    industria = md.compose(snap, sic="7372", sic_description="Soft\nware\x00 " + "Z" * 200).profile["industry"]
    assert "\n" not in industria and "\x00" not in industria and len(industria) <= 100
    assert "industry" not in md.compose(snap, sic="7372", sic_description=None).profile


def test_fdg_ca28_compose_un_perfil_de_finnhub_sin_nombre_ni_capitalizacion_no_agrega_claves_none():
    snap = _snap(quote=("finnhub", {"price": 1.0}), profile=("finnhub", {"companyName": None, "marketCap": 5.0}))
    assert md.compose(snap).profile == {"marketCap": 5.0}


# --- build_fmp_fallbacks ---------------------------------------------------------------------------------------------------


async def test_fdg_ca28_los_respaldos_fmp_piden_cada_cosa_una_sola_vez_y_metrics_reutiliza_quote_y_profile():
    llamadas = []

    async def get_quote(sym):
        llamadas.append(("quote", sym))
        return fmp_quote(sym)

    async def get_profile(sym):
        llamadas.append(("profile", sym))
        return fmp_profile(sym)

    fb = md.build_fmp_fallbacks(get_quote, get_profile)
    assert set(fb) == {"quote", "profile", "metrics"}
    assert await fb["metrics"]("S") == {"beta": 1.1, "yearHigh": 210.0, "yearLow": 110.0}
    assert (await fb["quote"]("S"))["price"] == 190.0 and (await fb["profile"]("S"))["sector"] == "Healthcare"
    assert llamadas == [("quote", "S"), ("profile", "S")]


async def test_fdg_ca28_el_respaldo_de_metrics_sin_ningun_dato_es_none_y_un_fallo_de_fmp_se_reintenta():
    async def vacio(sym):
        return None

    assert await md.build_fmp_fallbacks(vacio, vacio)["metrics"]("S") is None
    intentos = []

    async def get_quote(sym):
        intentos.append(sym)
        if len(intentos) == 1:
            raise FMPError("boom")
        return fmp_quote(sym)

    fb = md.build_fmp_fallbacks(get_quote, vacio)
    with pytest.raises(FMPError):
        await fb["quote"]("S")
    assert (await fb["quote"]("S"))["price"] == 190.0  # no memoiza el fallo
    assert intentos == ["S", "S"]


async def test_fdg_ca26_el_respaldo_por_consulta_tampoco_admite_vix():
    service = md.MarketDataService(finnhub_http=None, finnhub_api_key=None)
    with pytest.raises(ValueError):
        await service.fetch("S", fmp_fallbacks={"vix": lambda s: None})


async def test_fdg_ca28_fetch_con_respaldos_propios_reemplaza_a_los_del_constructor():
    usados = []

    async def ctor(sym):
        usados.append("ctor")
        return {"price": 1.0}

    async def propio(sym):
        usados.append("propio")
        return {"price": 2.0}

    service = md.MarketDataService(finnhub_http=None, finnhub_api_key=None, fmp_fallbacks={"quote": ctor})
    snap = await service.fetch("S", fmp_fallbacks={"quote": propio})
    assert snap.quote.data == {"price": 2.0} and usados == ["propio"]
    assert (await service.fetch("S")).quote.data == {"price": 1.0}


# --- cableado en bot.py --------------------------------------------------------------------------------------------------


def test_fdg_ca28_bot_construye_market_data_solo_con_finnhub_api_key_valida(monkeypatch, tmp_path):
    _env_main(monkeypatch, tmp_path, FINNHUB_API_KEY="A" * 24)
    captured = _spy_main(monkeypatch)
    bot.main()
    servicio = captured["clients"].market_data
    assert isinstance(servicio, market_data.MarketDataService)
    assert servicio._finnhub_http is captured["clients"].finnhub_http
    assert servicio._fred_http is captured["clients"].fred_http


@pytest.mark.parametrize("extra", [{}, {"FINNHUB_API_KEY": "corta"}, {"FINNHUB_API_KEY": "A" * 20 + "\\" + "BBBB"}])
def test_fdg_ca28_bot_sin_key_valida_de_finnhub_no_hay_capa_de_mercado(monkeypatch, tmp_path, extra):
    _env_main(monkeypatch, tmp_path, **extra)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["clients"].market_data is None and captured["clients"].finnhub_api_key is None


# --- capitalizacion N/D (CS-27) y otros limites del flujo --------------------------------------------------------------------


@pytest.mark.parametrize("cambios", [{"currency": "EUR"}, {"marketCapitalization": 9000.0}])
async def test_fdg_ca28_capitalizacion_na_por_moneda_o_incoherencia_deja_ps_na_sin_crash(cambios):
    world = World(fmp="down")
    original = world._finnhub

    def raro(request):
        if request.url.path == "/api/v1/stock/profile2":
            return httpx.Response(200, json=finnhub_profile("MELI", **{"cap": 2000.0}) | cambios)
        return original(request)

    world._finnhub = raro
    clients, _ = build_clients(world)
    parts, sink = await _analizar(clients)
    texto = "\n".join(parts)
    assert "No pude obtener suficientes datos" not in texto and sink["market_cap"] is None
    assert "- P/S (Precio-Ventas)" not in texto
    assert "Veredicto" in texto  # el resto del analisis sigue


# --- summary: ramas de procedencia --------------------------------------------------------------------------------------------


def _prov(quote=None, profile=None, metrics=None, vix=None, codes=()):
    return {"sources": {"quote": quote, "profile": profile, "metrics": metrics, "vix": vix}, "codes": codes}


def test_fdg_ca28_las_lineas_de_mercado_eligen_el_aviso_segun_los_codigos_y_las_fuentes():
    todo_finnhub = summary._lineas_mercado(_prov("finnhub", "finnhub", "finnhub", "fred"))
    assert len(todo_finnhub) == 1
    respaldo = summary._lineas_mercado(_prov("fmp", "finnhub", "finnhub", "fred", ("finnhub_no_disponible",)))
    assert respaldo[1] == f"_{summary.AVISO_FINNHUB_RESPALDO}_" and len(respaldo) == 2
    bloqueado = summary._lineas_mercado(_prov("fmp", "fmp", "fmp", None, ("circuito_abierto",)))
    assert bloqueado[1] == f"_{summary.AVISO_FINNHUB_BLOQUEADO}_" and "VIX: no disponible" in bloqueado[0]
    rechazado = summary._lineas_mercado(_prov("fmp", "fmp", "fmp", None, ("finnhub_rechazado",)))
    assert rechazado[1] == f"_{summary.AVISO_FINNHUB_BLOQUEADO}_"
    nd = summary._lineas_mercado(_prov("finnhub", "finnhub", None, "fred", ("finnhub_no_disponible",)))
    assert nd[-1] == f"_{summary.AVISO_MERCADO_ND}_" and "beta y rango de 52 semanas: no disponible" in nd[0]
    ambos = summary._lineas_mercado(_prov("fmp", "finnhub", None, "fred", ("finnhub_no_disponible",)))
    assert ambos[1:] == [f"_{summary.AVISO_FINNHUB_RESPALDO}_", f"_{summary.AVISO_MERCADO_ND}_"]


def test_fdg_ca28_una_fuente_fuera_del_enum_se_muestra_como_no_disponible_nunca_como_texto_de_la_red():
    et = summary._fuentes_mercado({"sources": {"quote": "<script>", "profile": "finnhub"}})
    assert et == {"quote": "no disponible", "profile": "Finnhub", "metrics": "no disponible", "vix": "no disponible"}
    assert summary._fuentes_mercado(None) is None and summary._fuentes_mercado({}) is None
    assert summary.build_market_source_line(None) is None


def test_fdg_ca28_sin_procedencia_de_mercado_los_textos_de_siempre_no_cambian():
    assert summary._linea_bloque("pilares", edgar=False, market_provenance=None) is None
    assert summary._linea_bloque("mercado", edgar=True, market_provenance=None) == summary._FUENTE_BLOQUE_EDGAR["mercado"]
    rf = {"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"}
    base = summary.build_risk_fit_section(rf, beta_por_defecto=True)
    assert "Dato de FMP." in base and f"_{summary.NOTA_BETA_POR_DEFECTO}_" in base


def test_fdg_ca28_la_beta_sin_fuente_de_mercado_usa_fmp_como_etiqueta_y_la_nota_de_mercado():
    rf = {"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"}
    s = summary.build_risk_fit_section(rf, beta_por_defecto=True, market_provenance=_prov("finnhub", "finnhub", None, "fred"))
    assert "Dato de FMP." in s and f"_{summary.NOTA_BETA_POR_DEFECTO_MERCADO}_" in s
    s2 = summary.build_risk_fit_section(rf, market_provenance=_prov("fmp", "fmp", "fmp", None))
    assert "Dato de FMP." in s2 and summary.NOTA_BETA_POR_DEFECTO_MERCADO not in s2


def test_fdg_ca28_la_primera_linea_de_transparencia_con_mercado_y_estados_de_fmp():
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="fmp", ticker="MELI", market_provenance=_prov("finnhub", "finnhub", "finnhub", "fred"))
    assert "_Estados financieros (ingresos, deuda, flujo de caja) obtenidos de la Financial Modeling Prep (FMP)._" in s
    assert "cotización, perfil y VIX de Financial" not in s


def test_fdg_ca28_compose_con_metrics_parcial_solo_pisa_lo_que_trae():
    snap = _snap(
        quote=("finnhub", {"price": 1.0}),
        profile=("finnhub", {"companyName": "S", "marketCap": 5.0}),
        metrics=("finnhub", {"beta": None, "yearHigh": None, "yearLow": None}),
    )
    c = md.compose(snap)
    assert c.quote == {"price": 1.0} and "beta" not in c.profile
    snap = _snap(
        quote=("fmp", {"price": 1.0, "yearHigh": 9.0}),
        profile=("fmp", {"companyName": "S"}),
        metrics=("fmp", {"beta": 1.1, "yearHigh": None, "yearLow": 2.0}),
    )
    c = md.compose(snap)
    assert c.quote == {"price": 1.0, "yearHigh": 9.0, "yearLow": 2.0} and c.profile["beta"] == 1.1


async def test_fdg_ca28_sin_user_agent_de_la_sec_la_capa_de_mercado_funciona_sin_sector_sic(monkeypatch):
    import dataclasses

    from .test_fdg_f2b_ca28_integracion import _espiar

    world = World(fmp="down")
    clients, _ = build_clients(world)  # la cadena EDGAR sigue sirviendo estados
    sin_sec = dataclasses.replace(clients, sec_edgar_user_agent=None)
    cap = _espiar(monkeypatch)
    parts, sink = await _analizar(sin_sec)
    assert "/submissions/" not in "".join(world.paths("data.sec.gov"))  # ni sector SIC ni 8-K
    assert cap["sector"] == "" and sink["company_name"] == "Mercado Sintetico SA"
    assert "No pude obtener suficientes datos" not in "\n".join(parts)


async def test_fdg_ca28_submissions_caido_deja_el_sector_vacio_pero_el_analisis_sale(monkeypatch):
    from .test_fdg_f2b_ca28_integracion import _espiar

    world = World(fmp="down")
    del world.sec_server.subs["0001234567"]  # 404 de submissions
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    assert cap["sector"] == "" and "No pude obtener suficientes datos" not in "\n".join(parts)
    assert cap["corporate_events"] == []


async def test_fdg_ca28_sec_context_sin_cik_devuelve_vacio_y_se_memoiza_por_consulta():
    from investbot import query_handler

    world = World(fmp="down")
    clients, _ = build_clients(world)
    memo: dict = {}
    assert await query_handler.sec_context(clients, "NOEXISTE", memo) == (None, None)
    antes = len(world.requests)
    assert await query_handler.sec_context(clients, "NOEXISTE", memo) == (None, None)
    assert len(world.requests) == antes
