"""F2b de SDD_fuentes_datos_gratuitas.md: CA-28 de punta a punta (ticker estilo MELI/CLPT, cuyo `/quote` y
perfil devuelven 402 "simbolo premium" en FMP gratis), procedencia por bloque, respaldo FMP, sector SIC,
T-CS-7 / T-CS-25 con canarios de Finnhub. Un despachador por HOST sobre `httpx.MockTransport`
(finnhub.io, data.sec.gov, api.stlouisfed.org, financialmodelingprep.com); datos SINTETICOS (CS-26)."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter

import httpx
import pytest

from investbot import advanced_command, ai_explain, ai_explain_content, ai_rewrite, query_handler, summary
from investbot.ai_explain import _SECTOR_ALLOWLIST

from .fdg_f2b_helpers import CANARIO_ENT, CANARIO_FH, CANARIO_SIC, World, build_clients, fmp_profile, fmp_quote
from .test_f1_redaccion import _CLAVES_STR, _claves_str
from .test_fdg_f1b3_edgar_provider import _reset_global_map  # noqa: F401  (autouse: mapa global limpio)

pytestmark = pytest.mark.filterwarnings("ignore::telegram.warnings.PTBUserWarning")


def _espiar(monkeypatch):
    """Captura lo que recibe `summary.build_summary_parts` y el `sector` de `peers`."""
    cap: dict = {}
    real = query_handler.summary.build_summary_parts

    def espia(**kwargs):
        cap.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(query_handler.summary, "build_summary_parts", espia)
    real_peers = query_handler.peers.get_peer_pe_average

    async def espia_peers(**kwargs):
        cap["sector"] = kwargs["sector"]
        return await real_peers(**kwargs)

    monkeypatch.setattr(query_handler.peers, "get_peer_pe_average", espia_peers)
    return cap


async def _analizar(clients, ticker="MELI", **kw):
    sink: dict = {}
    parts = await query_handler.fetch_and_analyze_parts(
        ticker, clients, "moderado", explain_context_sink=sink, **kw
    )
    return parts, sink


# --- CA-28 ---------------------------------------------------------------------------------------------------------


async def test_fdg_ca28_con_fmp_totalmente_caido_y_finnhub_edgar_fred_sirviendo_el_analisis_sale_completo(monkeypatch):
    world = World(fmp="down")
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    parts, sink = await _analizar(clients)
    texto = "\n".join(parts)
    assert "No pude obtener suficientes datos" not in texto
    assert cap["source"] == "sec_edgar" and cap["precio_actual"] == 187.5
    assert cap["company_name"] == "Mercado Sintetico SA"  # perfil de Finnhub, no el entityName de la SEC
    assert cap["ratios"]["per"] is not None and cap["ratios"]["ps"] is not None
    conservador = cap["scenarios"]["conservador"]
    assert conservador["valor_justo_graham"] is not None and conservador["valor_justo_dcf"] is not None
    assert sink["vix"] == {"valor": 17.5, "disponible": True}
    # El analisis completo no necesito a FMP ni una sola vez (se le pidio todo a otro proveedor).
    assert world.host_requests("financialmodelingprep.com") == []
    assert world.paths("finnhub.io")[:3] == ["/api/v1/quote", "/api/v1/stock/profile2", "/api/v1/stock/metric"]
    assert {"VIXCLS", "DGS20"} == {r.url.params["series_id"] for r in world.host_requests("api.stlouisfed.org")}


async def test_fdg_ca28_sin_priceavg50_y_200_el_momentum_es_no_disponible_y_el_resto_se_calcula(monkeypatch):
    world = World(fmp="down")
    clients, _ = build_clients(world)
    _, sink = await _analizar(clients)
    m = sink["momentum"]
    assert m["etiqueta"] == "no_disponible"
    assert m["pct_vs_avg_50"] is None and m["pct_vs_avg_200"] is None
    assert m["pct_vs_year_high"] == pytest.approx((187.5 - 200.0) / 200.0 * 100)
    assert m["pct_vs_year_low"] is not None
    assert sink["price_avg_50"] is None and sink["price_avg_200"] is None
    assert sink["year_high"] == 200.0 and sink["year_low"] == 120.0


async def test_fdg_ca28_el_sector_sale_de_la_tabla_sic_de_la_sec_cuando_fmp_no_lo_da(monkeypatch):
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(World(fmp="down", sic="7372"))
    await _analizar(clients)
    assert cap["sector"] == "Technology"


@pytest.mark.parametrize("sic, esperado", [("2834", "Healthcare"), ("1311", "Energy"), ("6021", "Financial Services")])
async def test_fdg_ca28_el_sector_es_siempre_de_la_allowlist(monkeypatch, sic, esperado):
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(World(fmp="down", sic=sic))
    await _analizar(clients)
    assert cap["sector"] == esperado and cap["sector"] in _SECTOR_ALLOWLIST


@pytest.mark.parametrize("sic", ["7389", "9999", "abcd", "../x", None])
async def test_fdg_ca28_sic_ambiguo_o_hostil_deja_el_sector_vacio_nunca_texto_de_la_red(monkeypatch, sic):
    cap = _espiar(monkeypatch)
    world = World(fmp="down")
    world.sec_server.subs["0001234567"]["sic"] = sic
    clients, _ = build_clients(world)
    await _analizar(clients)
    assert cap["sector"] == ""


async def test_fdg_ca28_fmp_con_sector_propio_gana_sobre_la_tabla_sic(monkeypatch):
    cap = _espiar(monkeypatch)
    world = World(fmp="ok")
    world.finnhub_status = 429  # Finnhub fuera -> el perfil viene de FMP (sector "Healthcare")
    clients, _ = build_clients(world)
    await _analizar(clients)
    assert cap["sector"] == "Healthcare"  # no "Technology" (SIC 7372)


async def test_fdg_ca28_la_procedencia_de_cada_bloque_dice_finnhub_y_cboe_via_fred_no_fmp():
    world = World(fmp="down")
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    texto = "\n".join(parts)
    lineas = texto.split("\n")
    assert "_Datos de mercado — cotización: Finnhub; perfil: Finnhub; beta y rango de 52 semanas: Finnhub; VIX: CBOE vía FRED._" in lineas
    assert "Dato: CBOE vía FRED (serie VIXCLS)._" in texto and "Dato de FMP (símbolo ^VIX)" not in texto
    assert "Dato de Finnhub._" in texto  # beta
    assert "_Fuente de este bloque: cotización de Finnhub; VIX: CBOE vía FRED; comparables según las notas de transparencia._" in lineas
    assert "_Fuente de este bloque: estados financieros de la SEC EDGAR; precio de Finnhub._" in lineas
    assert "cotización, perfil y VIX de Financial Modeling Prep" not in texto
    assert "usé FMP como respaldo" not in texto  # nada cayo a FMP
    assert summary.AVISO_FINNHUB_RESPALDO not in texto and summary.AVISO_MERCADO_ND not in texto


async def test_fdg_ca28_el_titulo_y_el_nombre_de_finnhub_van_sanitizados_y_acotados():
    world = World(fmp="down")
    clients, _ = build_clients(world)
    # nombre de Finnhub con Markdown y control: sale limpio en el titulo
    original = world._finnhub

    def hostil(request):
        if request.url.path == "/api/v1/stock/profile2":
            from .fdg_f2b_helpers import finnhub_profile

            return httpx.Response(200, json=finnhub_profile("MELI", name="Evil *Corp* [x](http://a)\n\x00" + "Z" * 200))
        return original(request)

    world._finnhub = hostil
    parts, _ = await _analizar(clients)
    assert "Evil" in parts[0] and "\n" not in parts[0].split("\n")[0].replace("Evil", "")
    assert len(parts[0].split("\n")[0]) < 200


# --- CA-28: respaldo FMP por bloque, con aviso --------------------------------------------------------------------------


async def test_fdg_ca28_con_finnhub_rechazando_todo_cada_bloque_cae_a_fmp_con_aviso_y_procedencia_veraz(monkeypatch):
    world = World(fmp="ok")
    world.finnhub_status = 429
    cap = _espiar(monkeypatch)
    clients, _ = build_clients(world)
    parts, sink = await _analizar(clients)
    texto = "\n".join(parts)
    lineas = texto.split("\n")
    prov = cap["market_provenance"]
    assert prov["sources"] == {"quote": "fmp", "profile": "fmp", "metrics": "fmp", "vix": "fred"}
    assert "finnhub_rechazado" in prov["codes"] and "circuito_abierto" in prov["codes"]
    assert f"_{summary.AVISO_FINNHUB_BLOQUEADO}_" in lineas
    assert "_Datos de mercado — cotización: FMP; perfil: FMP; beta y rango de 52 semanas: FMP; VIX: CBOE vía FRED._" in lineas
    assert cap["company_name"] == "FMP Sintetica SA" and cap["precio_actual"] == 190.0
    # FMP trae priceAvg50/200: el momentum SI se calcula con su fuente
    assert sink["momentum"]["etiqueta"] == "impulso_positivo"
    # un 429 abrio el kill-switch: una sola llamada a Finnhub para los 3 bloques (mas peers, ya con breaker abierto)
    assert Counter(world.paths("finnhub.io")) == Counter(["/api/v1/quote"])
    # FMP: 1 quote + 1 profile (metrics reutiliza esas dos respuestas, sin pedirlas de nuevo)
    fmp_paths = world.paths("financialmodelingprep.com")
    assert fmp_paths.count("/stable/quote") == 1 and fmp_paths.count("/stable/profile") == 1


async def test_fdg_ca28_con_solo_la_cotizacion_caida_en_finnhub_los_demas_bloques_siguen_en_finnhub():
    world = World(fmp="ok")
    clients, _ = build_clients(world)
    original = world._finnhub

    def sin_quote(request):
        if request.url.path == "/api/v1/quote":
            return httpx.Response(500, json={})
        return original(request)

    world._finnhub = sin_quote
    parts, _ = await _analizar(clients)
    texto = "\n".join(parts)
    assert "_Datos de mercado — cotización: FMP; perfil: Finnhub; beta y rango de 52 semanas: Finnhub; VIX: CBOE vía FRED._" in texto
    assert f"_{summary.AVISO_FINNHUB_RESPALDO}_" in texto.split("\n")
    assert summary.AVISO_FINNHUB_BLOQUEADO not in texto  # un 500 es transitorio, no un rechazo


async def test_fdg_ca28_con_todo_caido_el_resultado_es_un_mensaje_de_datos_insuficientes_sin_excepcion():
    world = World(fmp="down")
    world.finnhub_status = 402
    clients, _ = build_clients(world)
    parts, sink = await _analizar(clients)
    assert parts == ["No pude obtener suficientes datos de MELI para analizarlo ahora mismo."]
    assert sink == {}


async def test_fdg_ca28_sin_perfil_ni_en_finnhub_ni_en_fmp_el_analisis_aborta_con_el_mensaje_de_siempre():
    world = World(fmp="down")
    clients, _ = build_clients(world)
    original = world._finnhub

    def sin_perfil(request):
        if request.url.path == "/api/v1/stock/profile2":
            return httpx.Response(200, json={})
        return original(request)

    world._finnhub = sin_perfil
    parts, _ = await _analizar(clients)
    assert parts == ["No pude obtener suficientes datos de MELI para analizarlo ahora mismo."]


async def test_fdg_ca28_el_vix_no_pide_nada_a_fmp_ni_siquiera_si_fred_cae():
    world = World(fmp="ok")
    world.fred_status = 500
    clients, _ = build_clients(world)
    parts, sink = await _analizar(clients)
    assert sink["vix"] == {"valor": None, "disponible": False}
    assert "VIX (CBOE Volatility Index)" not in "\n".join(parts)
    # FMP nunca recibio un `^VIX`
    assert all(r.url.params.get("symbol") != "^VIX" for r in world.host_requests("financialmodelingprep.com"))
    texto = "\n".join(parts)
    assert "VIX: no disponible" in texto


async def test_fdg_ca28_la_beta_ausente_en_ambas_fuentes_se_declara_con_la_plantilla_de_mercado():
    world = World(fmp="down")
    original = world._finnhub

    def sin_beta(request):
        if request.url.path == "/api/v1/stock/metric":
            return httpx.Response(200, json={"metric": {"52WeekHigh": 200.0, "52WeekLow": 120.0}})
        return original(request)

    world._finnhub = sin_beta
    clients, _ = build_clients(world)
    parts, _ = await _analizar(clients)
    texto = "\n".join(parts)
    assert f"_{summary.NOTA_BETA_POR_DEFECTO_MERCADO}_" in texto.split("\n")
    assert summary.NOTA_BETA_POR_DEFECTO not in texto
    assert "con beta de 1.00" in texto


async def test_fdg_ca28_sin_capa_de_mercado_el_texto_es_el_de_siempre_con_fmp_de_fuente_del_mercado():
    """Sin FINNHUB_API_KEY (market_data=None): quote/profile/VIX de FMP y textos sin cambios."""
    world = World(fmp="ok")
    clients, _ = build_clients(world, market=False)
    # peers de Finnhub no se usan sin la capa de mercado; FMP trae los datos
    parts, sink = await _analizar(clients)
    texto = "\n".join(parts)
    assert "Datos de mercado —" not in texto and "Dato de FMP (símbolo ^VIX)" in texto
    assert "Dato de FMP." in texto
    assert "cotización, perfil y VIX de Financial Modeling Prep (FMP)" in texto
    assert sink["market_provenance"] is None and sink["source"] == "sec_edgar"


# --- /avanzado con la capa de mercado --------------------------------------------------------------------------------


class _Msg:
    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class _Update:
    def __init__(self):
        self.message = _Msg()
        self.effective_chat = type("C", (), {"id": 42})()


class _Ctx:
    def __init__(self, *args):
        self.args = list(args)


class _Rate:
    def allow(self, key):
        return True


async def _avanzado(clients, ticker="MELI"):
    handler = advanced_command.build_advanced_command_handler(clients, _Rate())
    update = _Update()
    await handler.callback(update, _Ctx(ticker))
    return update.message.sent


async def test_fdg_ca28_avanzado_funciona_con_fmp_caido_y_cita_la_fuente_de_mercado():
    world = World(fmp="down")
    clients, _ = build_clients(world)
    sent = await _avanzado(clients)
    msg = "\n".join(sent)
    assert "Altman Z-Score" in msg and "Piotroski" in msg
    assert "Fuente de datos de mercado: cotización Finnhub · perfil Finnhub · beta y rango de 52 semanas Finnhub" in msg
    assert "Fuente de los datos: SEC EDGAR" in msg
    assert world.host_requests("financialmodelingprep.com") == []
    # sicDescription "software" activa asset-light: se calcula Altman Z''
    assert "Z''" in msg


async def test_fdg_ca28_avanzado_con_todo_caido_responde_que_no_aplica_sin_excepcion():
    world = World(fmp="down")
    world.finnhub_status = 429
    clients, _ = build_clients(world)
    sent = await _avanzado(clients)
    assert sent == [advanced_command.NOT_FOUND_MSG]


async def test_fdg_ca28_avanzado_sin_capa_de_mercado_conserva_las_llamadas_de_fmp_de_siempre():
    world = World(fmp="ok")
    clients, _ = build_clients(world, market=False)
    sent = await _avanzado(clients)
    assert "Fuente de datos de mercado" not in "\n".join(sent)
    assert {"/stable/profile", "/stable/quote"} <= set(world.paths("financialmodelingprep.com"))


async def test_fdg_ca28_avanzado_rechaza_un_etf_que_fmp_marca_como_tal():
    world = World(fmp=lambda r: httpx.Response(
        200, json=[fmp_profile("MELI", isEtf=True)] if r.url.path == "/stable/profile" else [fmp_quote("MELI")]))
    world.finnhub_status = 429
    clients, _ = build_clients(world)
    sent = await _avanzado(clients)
    assert sent == [advanced_command.NOT_APPLICABLE_MSG]


# --- botón `inf` (procedencia veraz) --------------------------------------------------------------------------------


async def test_fdg_ca28_el_boton_inf_cita_la_fuente_real_y_no_dice_fmp_para_todo():
    world = World(fmp="down")
    clients, _ = build_clients(world)
    _, sink = await _analizar(clients)
    ctx = ai_explain.ExplanationContext(
        chat_id=1, kind="texto_libre", ticker="MELI", company_name="x",
        treasury_source=sink["treasury_source"], balance_sheet_fuente=sink["balance_sheet_fuente"],
        income_statement_fuente=sink["income_statement_fuente"], cash_flow_fuente=sink["cash_flow_fuente"],
        peers_note=sink["peers_note"], source=sink["source"], fallback_codes=sink["fallback_codes"],
        market_provenance=sink["market_provenance"],
    )
    texto = ai_explain._build_deterministic_content(ctx, "inf")
    assert "obtenidos de la SEC EDGAR" in texto and "cotización: Finnhub" in texto
    assert "cotización, perfil y VIX de Financial Modeling Prep" not in texto


def test_fdg_ca28_el_boton_inf_sin_los_campos_nuevos_es_identico_al_de_siempre():
    base = dict(chat_id=1, kind="texto_libre", ticker="AAPL", company_name="x", peers_note="p",
                treasury_source=None, balance_sheet_fuente=None, income_statement_fuente=None, cash_flow_fuente=None)
    ctx = ai_explain.ExplanationContext(**base)
    assert (ctx.source, ctx.fallback_codes, ctx.market_provenance) == ("fmp", (), None)
    esperado = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None)
    assert esperado in ai_explain._build_deterministic_content(ctx, "inf")


# --- T-CS-7 / T-CS-25 con canarios de Finnhub ---------------------------------------------------------------------------


def _ctx_desde_sink(sink):
    campos = {f.name for f in dataclasses.fields(ai_explain.ExplanationContext)}
    return ai_explain.ExplanationContext(
        chat_id=1, kind="texto_libre", ticker="MELI", **{k: v for k, v in sink.items() if k in campos}
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


async def test_fdg_tcs7_los_canarios_de_finnhub_y_la_sec_no_viajan_en_ningun_payload_a_gemini():
    """T-CS-7 / T-CS-25 (F2): nombre e industria de Finnhub y de la SEC son canarios; el conjunto de claves
    del payload es el MISMO con origen Finnhub que con origen FMP; ningun canario ni codigo de respaldo
    ni campo de procedencia llega a Gemini."""
    from .fdg_f2b_helpers import finnhub_profile

    world = World(fmp="down", sic_desc=CANARIO_SIC)
    original = world._finnhub

    def con_canario(request):
        if request.url.path == "/api/v1/stock/profile2" and request.url.params["symbol"] == "MELI":
            return httpx.Response(200, json=finnhub_profile("MELI", name=CANARIO_FH))
        return original(request)

    world._finnhub = con_canario
    clients, _ = build_clients(world)
    _, sink_fh = await _analizar(clients)
    assert sink_fh["company_name"] == CANARIO_FH  # se muestra al usuario (escapado) ...
    clients_fmp, _ = build_clients(World(fmp="ok"), market=False)
    _, sink_fmp = await _analizar(clients_fmp)
    c_fh, c_fmp = _ctx_desde_sink(sink_fh), _ctx_desde_sink(sink_fmp)
    codigos = [c for c in ai_explain_content.QUESTIONS_TEXTO_LIBRE if c not in ("evt", "inf")]
    assert codigos
    for code in codigos:  # ... pero ningun payload lo recibe
        pf = ai_explain._datos_para_el_modelo(code, ai_explain._build_explain_payload(c_fh, code))
        pm = ai_explain._datos_para_el_modelo(code, ai_explain._build_explain_payload(c_fmp, code))
        crudo = json.dumps(pf, ensure_ascii=False, default=str)
        assert _claves_todas(pf) == _claves_todas(pm), code
        assert _claves_str(pf) <= _CLAVES_STR[code], code
        for prohibido in (CANARIO_FH, CANARIO_SIC, CANARIO_ENT, "finnhub", "Finnhub", "market_provenance",
                          "fallback_codes", "circuito_abierto", "sec_edgar"):
            assert prohibido not in crudo, (code, prohibido)


def test_fdg_tcs25_las_plantillas_nuevas_de_mercado_pasan_classify_lines_como_lineas_protegidas():
    plantillas = [
        summary.NOTA_BETA_POR_DEFECTO_MERCADO, summary.AVISO_FINNHUB_RESPALDO,
        summary.AVISO_FINNHUB_BLOQUEADO, summary.AVISO_MERCADO_ND,
    ]
    for p in plantillas:
        sustituida, _ = ai_rewrite._classify_lines(f"_{p}_")
        assert sustituida.startswith("⟦PH"), p
    prov = {"sources": {"quote": "finnhub", "profile": "finnhub", "metrics": None, "vix": None}, "codes": ()}
    lineas = summary._lineas_mercado(prov)
    lineas += [summary._linea_bloque(b, edgar=e, market_provenance=prov)
               for b in ("valoracion", "pilares", "mercado") for e in (True, False)]
    lineas.append(summary.build_market_source_line(prov))
    for linea in lineas:
        sustituida, _ = ai_rewrite._classify_lines(linea)
        assert sustituida.startswith("⟦PH"), linea
