"""Tests de las guardas y la estructura de `scripts/gemini_smoke.py`
(SDD_migracion_gemini.md: CS-12, CS-13, CS-14, CA-S7, P-6/A16, P-13).

El script se carga con `importlib.util.spec_from_file_location` SIN ejecutarlo.
Todo sin red: `main(...)` recibe un `client_factory` con `httpx.MockTransport`
(la fixture autouse anti-red sigue activa).
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import logging
import re
import sys
from pathlib import Path

import httpx
import pytest

from .gemini_helpers import CANARY_BODY, gemini_response

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gemini_smoke.py"
LIVE_KEY = "LIVE-SMOKE-KEY-FOR-TESTS-0123456789"
PROD_KEY = "PROD-KEY-FOR-TESTS-0123456789ABCDE"
SMALL_ARGS = ["--explain-reps", "1", "--rewrite-samples", "2", "--rewrite-reps", "1"]


def _load():
    spec = importlib.util.spec_from_file_location("gemini_smoke_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke():
    return _load()


@pytest.fixture(autouse=True)
def _restaura_niveles_de_logging():
    """`main` fija httpx/httpcore/investbot en WARNING a propósito; los tests
    restauran los niveles para no contaminar a los demás archivos (caplog)."""
    nombres = ("httpx", "httpcore", "investbot")
    antes = {n: logging.getLogger(n).level for n in nombres}
    yield
    for n, nivel in antes.items():
        logging.getLogger(n).setLevel(nivel)


def _good_env(**extra):
    return {"GEMINI_LIVE_KEY": LIVE_KEY, "GEMINI_LIVE_CONFIRM": "yes", **extra}


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _ok_handler(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = request.content.decode()
        if request.headers["x-goog-api-key"].startswith("FAKE-NEGATIVE"):
            return httpx.Response(400, json={"error": {"message": CANARY_BODY}})
        if "responseMimeType" in body:  # rewrite: 3 secciones del resumen corto
            texto = '{"0": "Veredicto reescrito sin datos.", "1": "x", "2": "y"}'
            return httpx.Response(200, json=gemini_response(texto, usage={
                "promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 0}))
        return httpx.Response(200, json=gemini_response("Explicación breve sin datos nuevos."))

    return handler


# --- CA-S7: no ejecuta nada al importarse ---------------------------------------------


def test_ca_s7_importar_el_script_no_ejecuta_nada(monkeypatch, capsys, net_guard):
    def boom(*args, **kwargs):  # pragma: no cover - no debe llamarse
        raise AssertionError("se ejecutó algo al importar")

    monkeypatch.setattr(asyncio, "run", boom)
    sys_path_antes = list(sys.path)
    module = _load()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert net_guard.attempts == []
    assert sys.path == sys_path_antes  # el import no toca `sys.path`
    for nombre in ("main", "evaluate_guards", "CallBudget", "CallBudgetExceeded"):
        assert hasattr(module, nombre)  # control positivo: la interfaz pública existe
    assert module.__name__ == "gemini_smoke_under_test"  # no corrió como __main__


def test_ca_s7_el_bloque_main_solo_corre_como_script():
    arbol = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    ifs = [n for n in arbol.body if isinstance(n, ast.If)]
    assert len(ifs) == 1
    assert "__name__" in ast.unparse(ifs[0].test) and "__main__" in ast.unparse(ifs[0].test)
    # nada más a nivel de módulo llama a main/asyncio.run
    for nodo in arbol.body:
        if isinstance(nodo, ast.Expr) and isinstance(nodo.value, ast.Call):
            pytest.fail(f"llamada a nivel de módulo: {ast.unparse(nodo)}")


# --- CS-13: evaluate_guards -----------------------------------------------------------

_CASOS_GUARDAS = [
    ({"GEMINI_LIVE_KEY": ""}, False, "GUARD_NO_LIVE_KEY"),
    ({"GEMINI_LIVE_KEY": None}, False, "GUARD_NO_LIVE_KEY"),
    ({"GEMINI_LIVE_CONFIRM": None}, False, "GUARD_NO_CONFIRM"),
    ({"GEMINI_LIVE_CONFIRM": "no"}, False, "GUARD_NO_CONFIRM"),
    ({"CI": "true"}, False, "GUARD_CI"),
    ({"GITHUB_ACTIONS": "true"}, False, "GUARD_GITHUB_ACTIONS"),
    ({"GEMINI_API_KEY": LIVE_KEY}, False, "GUARD_SAME_KEY_AS_API_KEY"),
    ({"TELEGRAM_BOT_TOKEN": "123:abc"}, False, "GUARD_TELEGRAM_BOT_TOKEN_SET"),
    ({"FMP_API_KEY": "fmp-key"}, False, "GUARD_FMP_API_KEY_SET"),
    ({}, True, "GUARD_OK"),
    ({"GEMINI_API_KEY": PROD_KEY}, True, "GUARD_OK"),  # key de producción distinta: permitido
]


@pytest.mark.parametrize("cambio,permitido,token", _CASOS_GUARDAS)
def test_cs13_evaluate_guards_tabla(smoke, cambio, permitido, token):
    env = {**_good_env(), **cambio}
    env = {k: v for k, v in env.items() if v is not None}
    assert smoke.evaluate_guards(env, False) == (permitido, token)


def test_cs13_evaluate_guards_in_container_bloquea(smoke):
    assert smoke.evaluate_guards(_good_env(), True) == (False, "GUARD_IN_CONTAINER")
    assert smoke.evaluate_guards(_good_env(), False) == (True, "GUARD_OK")  # control positivo


@pytest.mark.parametrize("cambio,permitido,token", _CASOS_GUARDAS)
def test_cs13_el_token_de_razon_es_fijo_y_nunca_contiene_valores(smoke, cambio, permitido, token):
    canarios = {"GEMINI_API_KEY": PROD_KEY, "TELEGRAM_BOT_TOKEN": "TG-CANARY-VALUE",
                "FMP_API_KEY": "FMP-CANARY-VALUE"}
    env = {**_good_env(), **canarios, **cambio}
    env = {k: v for k, v in env.items() if v is not None}
    _, razon = smoke.evaluate_guards(env, False)
    assert re.fullmatch(r"[A-Z_]+", razon)
    for valor in (*canarios.values(), LIVE_KEY, "123:abc"):
        assert valor not in razon


def test_cs13_evaluate_guards_es_pura_no_modifica_el_env(smoke):
    env = _good_env(CI="1")
    copia = dict(env)
    smoke.evaluate_guards(env, False)
    assert env == copia


# --- CS-13: CallBudget -----------------------------------------------------------------


def test_cs13_call_budget_la_llamada_151_lanza(smoke):
    budget = smoke.CallBudget(limit=150)
    for _ in range(150):
        budget.spend()  # las 150 primeras no lanzan
    assert budget.spent == 150
    with pytest.raises(smoke.CallBudgetExceeded):
        budget.spend()
    assert issubclass(smoke.CallBudgetExceeded, RuntimeError)
    assert smoke.CallBudget().limit == 150  # default


# --- CS-13: main -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "env,in_container,token",
    [
        ({"GEMINI_LIVE_CONFIRM": "yes"}, False, "GUARD_NO_LIVE_KEY"),
        (_good_env(CI="1"), False, "GUARD_CI"),
        (_good_env(), True, "GUARD_IN_CONTAINER"),
        (_good_env(GEMINI_API_KEY=LIVE_KEY), False, "GUARD_SAME_KEY_AS_API_KEY"),
    ],
)
def test_cs13_main_con_guarda_que_bloquea_sale_distinto_de_cero_sin_requests(
    smoke, capsys, env, in_container, token
):
    seen: list = []

    def factory():  # pragma: no cover - no debe crearse el cliente
        seen.append("cliente")
        return httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler(seen)))

    rc = smoke.main(SMALL_ARGS, env, in_container, factory)
    out = capsys.readouterr().out
    assert rc != 0
    assert seen == []
    assert token in out
    assert LIVE_KEY not in out


def test_cs13_main_exito_con_mock_termina_en_0_e_imprime_resumen_sin_secretos(smoke, capsys):
    seen: list = []
    rc = smoke.main(SMALL_ARGS, _good_env(), False, _factory(_ok_handler(seen)))
    out = capsys.readouterr().out
    assert rc == 0
    assert len(seen) > 0  # control positivo: hubo requests (todas al MockTransport)
    assert 0 < len(seen) <= 150
    for criterio in ("CA-S1", "CA-S2", "CA-S3", "CA-S4", "CA-S5", "CA-S6"):
        assert criterio in out
    assert re.search(r"(PASA|FALLA)", out)
    # la key de smoke viaja solo por el header, nunca al stdout
    assert all(r.headers["x-goog-api-key"] for r in seen)
    assert LIVE_KEY not in out
    assert CANARY_BODY not in out  # cuerpo de la 4xx del control negativo
    assert "CANARY_EMPRESA_SA" not in out and "x-goog-api-key" not in out.lower()
    assert "[400, 400]" in out  # el status real del control negativo sí se anota


def test_cs13_main_usa_la_key_de_smoke_y_no_la_de_produccion(smoke):
    seen: list = []
    smoke.main(SMALL_ARGS, _good_env(GEMINI_API_KEY=PROD_KEY), False, _factory(_ok_handler(seen)))
    claves = {r.headers["x-goog-api-key"] for r in seen}
    assert LIVE_KEY in claves
    assert PROD_KEY not in claves
    assert any(c.startswith("FAKE-NEGATIVE") for c in claves)  # control negativo


def test_cs13_main_tope_de_150_llamadas_aborta_de_forma_controlada(smoke, capsys):
    seen: list = []
    rc = smoke.main(
        ["--explain-reps", "30", "--rewrite-samples", "1", "--rewrite-reps", "1"],
        _good_env(), False, _factory(_ok_handler(seen)),
    )
    out = capsys.readouterr().out
    assert rc == 4
    assert "TOPE_DE_LLAMADAS_EXCEDIDO" in out
    assert len(seen) == 150  # ni una más
    assert LIVE_KEY not in out


def test_cs13_main_config_invalida_aborta_sin_imprimir_la_key_ni_hacer_requests(smoke, capsys):
    seen: list = []
    key_mala = "key con espacios y comillas \" no valida"
    rc = smoke.main(SMALL_ARGS, _good_env(GEMINI_LIVE_KEY=key_mala), False, _factory(_ok_handler(seen)))
    out = capsys.readouterr().out
    assert rc == 3 and seen == []
    assert "CONFIG_INVALIDA" in out
    assert key_mala not in out and "con espacios" not in out


def test_cs13_main_argumentos_invalidos_devuelven_64(smoke, capsys):
    seen: list = []
    for argv in (["--explain-reps", "0"], ["--explain-reps", "abc"], ["--no-existe"]):
        assert smoke.main(argv, _good_env(), False, _factory(_ok_handler(seen))) == 64
    assert seen == []
    assert capsys.readouterr().out.count("ARGUMENTOS_INVALIDOS") == 3


def test_cs13_main_error_inesperado_imprime_solo_el_tipo(smoke, capsys):
    def factory():
        raise ZeroDivisionError(f"detalle con la key {LIVE_KEY}")

    rc = smoke.main(SMALL_ARGS, _good_env(), False, factory)
    out = capsys.readouterr().out
    assert rc == 5
    assert "ZeroDivisionError" in out and LIVE_KEY not in out


def test_cs13_main_fija_los_loggers_ruidosos_en_warning(smoke):
    for nombre in ("httpx", "httpcore", "investbot"):
        logging.getLogger(nombre).setLevel(logging.DEBUG)
    smoke.main(SMALL_ARGS, _good_env(), False, _factory(_ok_handler([])))
    for nombre in ("httpx", "httpcore", "investbot"):
        assert logging.getLogger(nombre).level == logging.WARNING


def test_cs13_main_no_crea_archivos_en_el_repo(smoke, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = SCRIPT.parent.parent
    antes = {p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
             and ".venv" not in p.parts and "__pycache__" not in p.parts}
    smoke.main(SMALL_ARGS, _good_env(), False, _factory(_ok_handler([])))
    despues = {p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
               and ".venv" not in p.parts and "__pycache__" not in p.parts}
    assert despues == antes
    assert list(tmp_path.iterdir()) == []


# --- CS-12 / CS-14: GREP automatizado sobre el código del script -----------------------


def _fuente() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_cs12_el_script_no_carga_archivos_de_entorno():
    patron = re.compile("dot" + "env|" + r"\." + "env")
    assert patron.search(_fuente()) is None
    assert patron.search("load_" + "dot" + "env()")  # control positivo
    assert patron.search("open('.en" + "v')")


def test_cs12_la_key_de_produccion_solo_se_menciona_en_la_comparacion_de_la_guarda():
    arbol = ast.parse(_fuente())
    nombre_prod = "GEMINI_" + "API_KEY"
    ubicaciones = []
    for funcion in [n for n in ast.walk(arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for nodo in ast.walk(funcion):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and nombre_prod in nodo.value:
                ubicaciones.append(funcion.name)
    assert ubicaciones == ["evaluate_guards"]  # exactamente una, dentro de la guarda


def test_cs12_importa_solo_stdlib_httpx_e_investbot():
    arbol = ast.parse(_fuente())
    top = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            top |= {a.name.split(".")[0] for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
            top.add((nodo.module or "").split(".")[0])
    permitidos = set(sys.stdlib_module_names) | {"httpx", "investbot", "__future__"}
    assert top - permitidos == set()
    assert {"httpx", "investbot"} <= top  # control positivo: sí importa esos dos


def test_cs12_construye_la_config_desde_la_key_de_smoke_con_un_env_armado_a_mano():
    fuente = _fuente()
    assert "load_config(" in fuente and "GEMINI_LIVE_KEY" in fuente
    # el entorno completo del proceso solo se importa y se pasa a `main` en el bloque __main__
    assert len(re.findall(r"\benviron\b", fuente)) == 2
    # y `_run` solo recibe el mapping, nunca lo recorre entero
    assert "environ.items" not in fuente and "dict(env)" not in fuente


def test_cs14_el_script_no_escribe_archivos():
    arbol = ast.parse(_fuente())
    prohibidos = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if nombre in {"open", "write_text", "write_bytes", "mkdir", "touch", "unlink"}:
                prohibidos.append(ast.unparse(nodo))
    assert prohibidos == []
    # control positivo: el detector sí detecta una escritura
    detector = ast.parse("open('x', 'w')")
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open" for n in ast.walk(detector))


def test_cs14_no_hay_literal_de_key_con_forma_real_en_el_script():
    assert ("AI" + "za") not in _fuente()
    assert re.search(r"AIza[0-9A-Za-z_-]{20,}", _fuente()) is None
