"""F1b-4 de SDD_fuentes_datos_gratuitas.md: procedencia en el mensaje (CA-12, CA-23, CA-34 e, T-CS-11,
T-CS-13, T-CS-25). Sin red real."""

from __future__ import annotations

import httpx
import pytest

from investbot import ai_rewrite, fmp_provider, query_handler, summary
from investbot.fundamentals import FundamentalsService

from .edgar_helpers import companyfacts
from .test_edgar_xbrl import CIK, empresa
from .test_fdg_f1b3_edgar_provider import CIK10, Server, seed
from .test_fdg_f1b4_consumidores import _entorno, _reset_mapa, _srv, _texto  # noqa: F401
from .test_query_handler import _adobe_router, _make_clients_with_handler


def _fmp_solo(adobe_fixtures):
    from .test_fdg_f1b4_consumidores import _con_fred

    base = _adobe_router(adobe_fixtures)
    return _con_fred(adobe_fixtures, base, FundamentalsService(
        [fmp_provider.FmpProvider(httpx.AsyncClient(transport=httpx.MockTransport(base)), "k")]))


# --- CA-12: la cadena de un solo proveedor no cambia la secuencia de paths de FMP ----------------

_SEQ_ADBE = [
    ("/quote", "ADBE", None, None), ("/profile", "ADBE", None, None),
    ("/income-statement", "ADBE", "quarter", "20"), ("/income-statement", "ADBE", "annual", "5"),
    ("/cash-flow-statement", "ADBE", "quarter", "20"), ("/cash-flow-statement", "ADBE", "annual", "5"),
    ("/balance-sheet-statement", "ADBE", "quarter", "1"), ("/balance-sheet-statement", "ADBE", "annual", "5"),
    ("/key-metrics", "ADBE", "annual", "1"), ("/quote", "^VIX", None, None),
    ("/key-metrics", "MSFT", "annual", "1"), ("/key-metrics", "ORCL", "annual", "1"),
    ("/key-metrics", "CRM", "annual", "1"),
]
_SEQ_NVDA = [
    ("/quote", "NVDA", None, None), ("/profile", "NVDA", None, None),
    ("/income-statement", "NVDA", "quarter", "20"), ("/cash-flow-statement", "NVDA", "quarter", "20"),
    ("/balance-sheet-statement", "NVDA", "quarter", "1"), ("/key-metrics", "NVDA", "annual", "1"),
    ("/quote", "^VIX", None, None), ("/key-metrics", "MSFT", "annual", "1"),
    ("/key-metrics", "ORCL", "annual", "1"), ("/key-metrics", "CRM", "annual", "1"),
]


@pytest.mark.parametrize("ticker, esperada", [("ADBE", _SEQ_ADBE), ("NVDA", _SEQ_NVDA)])
async def test_fdg_ca12_secuencia_de_paths_de_fmp_identica_con_y_sin_cadena(adobe_fixtures, ticker, esperada):
    secuencias = []
    for con_cadena in (False, True):
        base, seq = _adobe_router(adobe_fixtures), []

        def h(r, base=base, seq=seq):
            seq.append((r.url.path.replace("/stable", ""), r.url.params.get("symbol"),
                        r.url.params.get("period"), r.url.params.get("limit")))
            return base(r)

        clients = _make_clients_with_handler(h, adobe_fixtures)
        if con_cadena:
            import dataclasses
            clients = dataclasses.replace(clients, fundamentals=FundamentalsService(
                [fmp_provider.FmpProvider(clients.fmp_http, "test-key")]))
        await query_handler.fetch_and_analyze_parts(ticker, clients, "moderado")
        secuencias.append(seq)
    assert secuencias[0] == secuencias[1] == esperada


# --- CA-23 -----------------------------------------------------------------------------------------

async def test_fdg_ca23_con_fuente_edgar_ningun_bloque_dice_dato_de_fmp_sobre_los_estados(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv()), "SYNT")
    assert "ya calculado por FMP" not in texto and "(dato de FMP)" not in texto
    assert "datos de FMP — no son campos" not in texto
    assert "_(calculados por el bot a partir de datos de la SEC EDGAR" in texto
    assert "cálculo del bot con datos de la SEC EDGAR" in texto


async def test_fdg_ca23_con_fuente_fmp_el_texto_es_el_de_siempre(adobe_fixtures):
    texto = await _texto(_fmp_solo(adobe_fixtures), "NVDA")
    assert "datos de FMP — no son campos directos de la API" in texto
    assert "dato ya calculado por FMP" in texto and "(dato de FMP)" in texto
    assert "obtenidos de Financial Modeling Prep (FMP)" in texto
    assert "SEC EDGAR; cotización" not in texto and "Fuente de este bloque" not in texto
    assert "respaldo" not in texto and "no asumo deuda cero" not in texto


def test_fdg_ca23_los_builders_con_defaults_conservan_el_texto_de_fmp():
    linea = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None,
        balance_sheet_fuente=None, cash_flow_fuente=None)
    assert "obtenidos de Financial Modeling Prep (FMP)" in linea
    extras = {"roe": 0.1, "debt_to_equity": 1.0, "net_debt_to_ebitda": 1.0, "dividend_yield": 0.01, "payout_ratio": 0.2}
    assert summary.build_extras_section(extras).count("FMP") == 5
    assert summary.build_extras_section(extras, source="sec_edgar").count("FMP") == 0


# --- T-CS-13: cada bloque lleva su linea de procedencia --------------------------------------------------

_BLOQUES = ("*Ratios clave:*", "*Rentabilidad, deuda", "*Rango de Valor Justo", "*Pilares de buena empresa:*",
            "*Contexto de mercado:*", "*Encaje con tu perfil", "*Notas de transparencia:*")


async def test_fdg_tcs13_con_fuente_edgar_cada_bloque_de_datos_lleva_su_linea_de_procedencia(adobe_fixtures):
    clients = _entorno(adobe_fixtures, _srv())
    partes = await query_handler.fetch_and_analyze_parts("SYNT", clients, "moderado")
    for prefijo in _BLOQUES:
        bloque = next(p for p in partes if p.startswith(prefijo))
        assert any(m in bloque for m in ("SEC EDGAR", "FMP", "Fuente de este bloque")), prefijo
    assert next(p for p in partes if p.startswith("*Rango")).splitlines()[-1].startswith("_Fuente de este bloque")
    assert next(p for p in partes if p.startswith("*Pilares")).splitlines()[-1].startswith("_Fuente de este bloque")
    assert next(p for p in partes if p.startswith("*Contexto")).splitlines()[-1].startswith("_Fuente de este bloque")


async def test_fdg_tcs13_con_respaldo_a_fmp_la_nota_de_transparencia_dice_por_que(adobe_fixtures):
    seed(OTRO="0000000002")
    texto = await _texto(_entorno(adobe_fixtures, Server()), "NVDA")
    assert "usé FMP como respaldo" in texto and "obtenidos de Financial Modeling Prep (FMP)" in texto


def test_fdg_tcs13_cache_antiguo_se_declara_con_fuente_sec():
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="sec_edgar", fallback_codes=("cache_antiguo",), ticker="AAPL")
    assert "copia guardada hace hasta 7 días" in s and "AAPL" in s


def test_fdg_tcs13_un_ticker_no_valido_se_muestra_como_interrogante():
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="fmp", fallback_codes=("sin_cik",), ticker="A*B\n_x")
    assert "reportes de ? en la SEC" in s and "A*B" not in s


# --- CA-34 e / T-CS-25 -------------------------------------------------------------------------------------------

AVISO = ("No pude determinar la deuda financiera de SYNT con los reportes de la SEC; el costo de capital, "
         "la relación deuda/patrimonio y la Magic Formula quedan como no disponibles (no asumo deuda cero).")
AVISO_INT = "SYNT no reporta gasto por intereses en la SEC; el costo de la deuda y el DCF quedan no disponibles."


def _protegida(linea: str) -> bool:
    texto, mapa = ai_rewrite._classify_lines(linea)
    return texto == "⟦PH0⟧" and mapa == {"⟦PH0⟧": linea}


async def test_fdg_ca34_e_deuda_ausente_con_fuente_edgar_el_mensaje_trae_la_linea_fija_protegida(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv(omitir=("DebtCurrent", "LongTermDebtNoncurrent"))), "SYNT")
    linea = f"_{AVISO}_"
    assert linea in texto.split("\n") and _protegida(linea)
    assert "no reporta gasto por intereses" not in texto  # con deuda desconocida no se afirma lo de intereses


async def test_fdg_ca34_e_intereses_ausentes_con_deuda_el_mensaje_trae_su_linea_fija_protegida(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv(omitir=("InterestExpense",))), "SYNT")
    linea = f"_{AVISO_INT}_"
    assert linea in texto.split("\n") and _protegida(linea) and AVISO not in texto


async def test_fdg_ca34_e_con_deuda_e_intereses_completos_no_hay_avisos(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv()), "SYNT")
    assert "no asumo deuda cero" not in texto and "no reporta gasto por intereses" not in texto


@pytest.mark.parametrize("flags", [dict(deuda_ausente=True), dict(intereses_ausentes=True),
                                   dict(deuda_ausente=True, intereses_ausentes=True)])
def test_fdg_ca34_e_con_fuente_fmp_los_avisos_nunca_se_muestran(flags):
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="fmp", ticker="SYNT", **flags)
    assert "no asumo deuda cero" not in s and "no reporta gasto" not in s


def test_fdg_ca34_e_si_faltan_deuda_e_intereses_gana_el_aviso_de_deuda():
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="sec_edgar", ticker="SYNT", deuda_ausente=True, intereses_ausentes=True)
    assert AVISO in s and AVISO_INT not in s


def test_fdg_tcs25_todas_las_plantillas_nuevas_pasan_classify_lines_como_lineas_protegidas():
    plantillas = [
        summary.AVISO_DEUDA_AUSENTE, summary.AVISO_INTERESES_AUSENTES, summary._NOTA_RESPALDO_SIN_COBERTURA,
        summary._NOTA_RESPALDO_TEMPORAL, summary._NOTA_CACHE_ANTIGUO,
    ]
    fijas = [summary.NOTA_DEFINICIONES_EDGAR, summary.NOTA_BETA_POR_DEFECTO]
    for p in plantillas:
        for ticker in ("A", "AAPL", "BRK-B", "?"):
            assert _protegida("_" + p.format(ticker=ticker) + "_"), (p, ticker)
    for p in fijas:
        assert _protegida(f"_{p}_"), p
    for linea in summary._FUENTE_BLOQUE_EDGAR.values():
        assert _protegida(linea), linea
    assert "\n" not in "".join(plantillas + fijas)


async def test_fdg_tcs25_en_el_mensaje_edgar_ninguna_linea_de_procedencia_llega_libre_a_gemini(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv(omitir=("InterestExpense",))), "SYNT")
    claves = ("SEC EDGAR", "usé FMP", "en la SEC", "Fuente de este bloque", "usé beta 1.0")
    lineas = [l for l in texto.split("\n") if any(c in l for c in claves)]
    assert lineas
    for linea in lineas:
        sustituida, _ = ai_rewrite._classify_lines(linea)
        assert sustituida.startswith("⟦PH"), linea


# --- T-CS-11 -----------------------------------------------------------------------------------------------------------

HOSTIL = "Evil *Corp* [x](http://a) `c` _u_\n\x00\x07‮\t" + "Z" * 200


def test_fdg_tcs11_entity_name_con_markdown_y_control_sale_escapado_y_acotado():
    limpio = summary.sanitize_third_party_name(HOSTIL)
    assert "\n" not in limpio and "\x00" not in limpio and "\x07" not in limpio and "\t" not in limpio
    assert "‮" not in limpio
    sin_escape = limpio.replace("\\", "")
    assert len(sin_escape) <= 100
    for c in "*_`[":
        assert limpio.count(c) == limpio.count("\\" + c)  # todo caracter especial escapado
    assert summary.sanitize_third_party_name("Z" * 100) == "Z" * 100
    assert summary.sanitize_third_party_name("Z" * 101) == "Z" * 100


def test_fdg_tcs11_un_nombre_normal_sale_identico_al_escape_de_siempre_y_lo_no_str_es_vacio():
    assert summary.sanitize_third_party_name("Adobe  Inc. & Co") == summary._escape_markdown_legacy("Adobe  Inc. & Co")
    assert summary.sanitize_third_party_name(None) == "" and summary.sanitize_third_party_name(5) == ""


def _args_resumen():
    esc = {"valor_justo_total": None, "modelos_excluidos": [], "valor_justo_multiplos": None,
           "valor_justo_graham": None, "valor_justo_dcf": None}
    return esc


def test_fdg_tcs11_el_titulo_del_mensaje_usa_el_nombre_sanitizado_y_cae_al_ticker_si_queda_vacio():
    pil = {"ingresos_crecientes": True, "utilidades_crecientes": True, "deuda_controlada": True, "precio_razonable": None}
    rf = {"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"}
    sc = {k: _args_resumen() for k in ("pesimista", "conservador", "optimista")}
    corto = summary.build_summary_parts_short(
        ticker="SYNT", company_name=HOSTIL, precio_actual=10.0, pillars=pil, risk_fit=rf, scenarios=sc)
    assert corto[0].count("\n") == 0 and len(corto[0]) < 200
    vacio = summary.build_summary_parts_short(
        ticker="SYNT", company_name="\u202e\x00\x07 ", precio_actual=10.0, pillars=pil, risk_fit=rf, scenarios=sc)
    assert vacio[0] == "*SYNT (SYNT)*"


async def test_fdg_tcs11_entity_name_hostil_de_la_sec_no_aparece_en_el_mensaje_ni_rompe_nada(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv(entity=HOSTIL)), "SYNT")
    assert "Evil" not in texto and "‮" not in texto


def test_fdg_tcs13_con_fuente_sec_y_un_codigo_que_no_es_cache_antiguo_no_agrega_nota_de_respaldo():
    s = summary.build_transparency_section(
        peers_note="p", treasury_source=None, income_statement_fuente=None, balance_sheet_fuente=None,
        cash_flow_fuente=None, source="sec_edgar", fallback_codes=("sin_cik",), ticker="AAPL")
    assert "respaldo" not in s and "copia guardada" not in s


async def test_fdg_ca34_e_largo_presente_y_corto_ausente_extremo_a_extremo_total_none_y_aviso(adobe_fixtures):
    texto = await _texto(_entorno(adobe_fixtures, _srv(omitir=("DebtCurrent",))), "SYNT")  # LongTermDebtNoncurrent sigue
    assert f"_{AVISO}_" in texto.split("\n") and "Deuda/Patrimonio" not in texto
