"""F1b-4: `scripts/compare_edgar_fmp.py` (SDD_fuentes_datos_gratuitas.md 14.8, CA-33). Sin red: clientes
con `httpx.MockTransport`; el script se carga con importlib sin ejecutarlo. Guardas y AST como en
`test_gemini_smoke_guards.py`."""

from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import re
from pathlib import Path

import httpx
import pytest

from investbot import edgar_provider, rules
from investbot import sec_edgar_client as sec

from .edgar_helpers import companyfacts
from .test_edgar_xbrl import CIK, HOY, empresa
from .test_fdg_f1b3_edgar_provider import CIK10, UA, Server, cliente, seed

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compare_edgar_fmp.py"
KEY = "FMP-KEY-PARA-TESTS-0123456789"


@pytest.fixture(autouse=True)
def _reset_mapa():
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None
    yield
    sec._ticker_cik_cache.clear()
    sec._cache_loaded_at = None


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("compare_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _en_hilo(fn, *args):
    """`asyncio.run` (directo o dentro de `script.main`) en un hilo propio: en el hilo principal
    `asyncio.run` hace `set_event_loop(None)` y descarta, sin cerrarlo, el bucle "limpio" que
    pytest-asyncio deja como actual (ResourceWarning que otro test dispara en su gc)."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


def _env(**extra):
    return {"SEC_EDGAR_USER_AGENT": UA_REAL, "FMP_API_KEY": KEY, "COMPARE_CONFIRM": "yes", **extra}


UA_REAL = "InvestBot " + "alias" + "@" + "dominio-real.org"  # armado a trozos: no es un email literal del repo


def _server():
    s = Server()
    s.facts[CIK10] = companyfacts(*empresa(), cik=CIK)
    seed(NVDA=CIK10)
    return s


def _filas_edgar():
    srv = _server()
    http, _ = cliente(srv)
    res = _en_hilo(asyncio.run, edgar_provider.EdgarProvider(http, UA, today=lambda: HOY).fetch("NVDA", quarters=12))
    return res


def _fmp_handler(res, *, cambios=None, status=200, ebit_distinto=False):
    cambios = cambios or {}

    def filas(lista):
        out = copy.deepcopy(lista[:4])
        for f in out:
            f.update(cambios)
            if ebit_distinto and "ebit" in f and f["ebit"] is not None:
                f["ebit"] = f["ebit"] * 2
        return out

    def handler(r: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        p = r.url.path
        if p.endswith("/income-statement"):
            return httpx.Response(200, json=filas(res.income_q))
        if p.endswith("/balance-sheet-statement"):
            return httpx.Response(200, json=filas(res.balance_q))
        if p.endswith("/cash-flow-statement"):
            return httpx.Response(200, json=filas(res.cash_q))
        if p.endswith("/quote"):
            return httpx.Response(200, json=[{"price": 100.0, "marketCap": 5e11}])
        return httpx.Response(404)

    return handler


def _correr(script, handler, argv=("NVDA",), env=None, in_container=False):
    salida: list[str] = []
    srv = _server()

    def sec_factory():
        http, _ = cliente(srv)
        return http

    def fmp_factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    rc = _en_hilo(script.main, list(argv), env or _env(), in_container, sec_factory, fmp_factory, salida.append, lambda: HOY)
    return rc, "\n".join(salida)


# --- guardas ------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env, contenedor, motivo",
    [
        ({"CI": "true"}, False, "NO_SE_CORRE_EN_CI"),
        ({}, True, "DENTRO_DE_UN_CONTENEDOR"),
        ({"COMPARE_CONFIRM": "no"}, False, "FALTA_COMPARE_CONFIRM_YES"),
    ],
)
def test_fdg_ca33_las_guardas_abortan_con_codigo_2_sin_tocar_la_red(script, env, contenedor, motivo):
    base = {"COMPARE_CONFIRM": "yes", "SEC_EDGAR_USER_AGENT": UA_REAL, "FMP_API_KEY": KEY}
    base.update(env)
    if env.get("COMPARE_CONFIRM") is None and "CI" not in env and not contenedor:
        base.pop("COMPARE_CONFIRM")
    rc, out = _correr(script, _fmp_handler(_filas_edgar()), env=base, in_container=contenedor)
    assert rc == 2 and motivo in out


@pytest.mark.parametrize("argv", [["aapl!"], ["A" * 8], ["Ä"], ["A", "B", "C", "D", "E", "F"]])
def test_fdg_ca33_argumentos_invalidos_rc_64(script, argv):
    rc, out = _correr(script, _fmp_handler(_filas_edgar()), argv=argv)
    assert rc == 64 and "ARGUMENTOS_INVALIDOS" in out


@pytest.mark.parametrize(
    "env, motivo",
    [
        (_env(SEC_EDGAR_USER_AGENT="InvestBot contacto@ejemplo.com"), "SEC_EDGAR_USER_AGENT_AUSENTE_O_INVALIDO"),
        (_env(SEC_EDGAR_USER_AGENT=""), "SEC_EDGAR_USER_AGENT_AUSENTE_O_INVALIDO"),
        (_env(FMP_API_KEY=""), "FMP_API_KEY_AUSENTE"),
    ],
)
def test_fdg_ca33_configuracion_invalida_rc_3_y_nunca_imprime_el_ua_ni_la_key(script, env, motivo):
    rc, out = _correr(script, _fmp_handler(_filas_edgar()), env=env)
    assert rc == 3 and motivo in out
    assert KEY not in out and "ejemplo.com" not in out


def test_fdg_ca33_sin_argumentos_usa_nvda_adbe_y_ko(script):
    assert script.parse_tickers([]) == ["NVDA", "ADBE", "KO"]
    assert script.parse_tickers(["brk-b"]) == ["BRK-B"]


# --- comparacion ----------------------------------------------------------------------------------------

def test_fdg_ca33_sin_diferencias_rc_0_y_no_filtra_ua_ni_key(script):
    rc, out = _correr(script, _fmp_handler(_filas_edgar()))
    assert rc == 0 and "=== NVDA ===" in out and "[FUERA]" not in out
    assert "RESUMEN: 0 diferencia(s) FUERA" in out and "[OK] TTM netIncome TTM" in out
    assert UA_REAL not in out and KEY not in out and "alias@" not in out


def test_fdg_ca33_una_diferencia_fuera_de_tolerancia_rc_1(script):
    res = _filas_edgar()
    rc, out = _correr(script, _fmp_handler(res, cambios={"revenue": 1.0}))
    assert rc == 1 and "[FUERA] income" in out and "revenue" in out


def test_fdg_ca33_las_diferencias_esperadas_de_ebit_se_informan_y_no_se_rechazan(script):
    rc, out = _correr(script, _fmp_handler(_filas_edgar(), ebit_distinto=True))
    assert rc == 0 and "[ESPERADA] income" in out and "ebit" in out


def test_fdg_ca33_edgar_none_contra_fmp_con_valor_es_esperado_en_intereses(script):
    srv = Server()
    srv.facts[CIK10] = companyfacts(*empresa(omitir=("InterestExpense",)), cik=CIK)
    seed(NVDA=CIK10)
    http, _ = cliente(srv)
    res = _en_hilo(asyncio.run, edgar_provider.EdgarProvider(http, UA, today=lambda: HOY).fetch("NVDA", quarters=12))
    fmp = copy.deepcopy(res)
    for f in fmp.income_q:
        f["interestExpense"] = 100.0
    salida: list[str] = []
    rc = _en_hilo(script.main, ["NVDA"], _env(), False, lambda: cliente(srv)[0],
                     lambda: httpx.AsyncClient(transport=httpx.MockTransport(_fmp_handler(fmp))), salida.append,
                     lambda: HOY)
    assert rc == 0 and any("[ESPERADA] income" in l and "interestExpense" in l for l in salida)


def test_fdg_ca33_ticker_sin_cobertura_de_edgar_se_informa_y_sigue(script):
    salida: list[str] = []
    handler = _fmp_handler(_filas_edgar())
    seed(OTRO="0000000002")
    rc = _en_hilo(script.main, ["NVDA"], _env(), False, lambda: cliente(Server())[0],
                     lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                     salida.append, lambda: HOY)
    assert rc == 0 and any("EDGAR no disponible: sin_cik" in l for l in salida)


def test_fdg_ca33_un_error_inesperado_rc_5_sin_imprimir_el_mensaje(script):
    rc, out = _correr(script, _fmp_handler(_filas_edgar(), status=500))
    assert rc == 5 and "ERROR_INESPERADO" in out and KEY not in out


def test_fdg_ca33_clasificadores_y_emparejado_de_fechas(script):
    assert script.classify_rel(100, 100, 0.005) == "OK" and script.classify_rel(100.4, 100, 0.005) == "OK"
    assert script.classify_rel(101, 100, 0.005) == "FUERA" and script.classify_rel(1, 0, 0.005) == "FUERA"
    assert script.classify_rel(None, 1, 0.005) == "SIN_DATO" and script.classify_rel(True, 1, 0.5) == "SIN_DATO"
    assert script.classify_abs(1.0, 1.01, 0.01) == "OK" and script.classify_abs(1.0, 1.02, 0.01) == "FUERA"
    assert script.match_row({"date": "2026-01-25"}, [{"date": "2026-01-28"}]) is not None
    assert script.match_row({"date": "2026-01-25"}, [{"date": "2026-01-29"}]) is None
    assert script.match_row({"date": "xx"}, [{"date": "2026-01-29"}]) is None
    filas = script.compare_statement("income", [{"date": "2026-01-25"}], [])
    assert filas == [("2026-01-25", "(periodo)", "SIN_DATO", None, None)]
    assert script._fmt(None) == "N/D" and script._fmt(1.5) == "1.5000" and script._fmt(3) == "3"
    ttm = script.compare_ttm(rules, [], [], None, None)
    assert {v for _, v, _, _ in ttm} == {"SIN_DATO"}


# --- AST / estructura -----------------------------------------------------------------------------------------

def test_fdg_ca33_estructura_sin_emails_ni_redirecciones_ni_escritura_ni_ssl_apagado():
    codigo = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(codigo)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", codigo)
    assert "follow_redirects=True" not in codigo and "verify=False" not in codigo
    llamadas = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    atributos = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "open" not in llamadas and not ({"write_text", "write_bytes", "write", "dump"} & atributos)
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not ({"pickle", "shelve", "sqlite3", "subprocess", "requests"} & imports)
    # el UA y la key solo se leen del entorno
    assert "SEC_EDGAR_USER_AGENT" in codigo and "FMP_API_KEY" in codigo
