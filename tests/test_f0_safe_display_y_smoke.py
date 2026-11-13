"""Tests de la FASE F0 de `SDD_redaccion_gemini_iter2.md` (secciones 16.4, 17.5, 18.9).

Cubre, sin red y sin modificar ningún test existente:

- CS-28 / C1-C9: `ai_explain._safe_display` y el flag `--show-text` del smoke.
- CS-25(b): `ai_explain._compute_datos_tokens` única, usada por el handler y por el
  smoke (el smoke no repite `json.dumps` + `protected_tokens`).
- Smoke de F0: 10 códigos (QB-8), `--thinking`, NullHandler (C8) y métricas de
  redacción comparables baseline/nuevo.

El script se carga con `importlib` SIN ejecutarlo; todo el tráfico va a un
`httpx.MockTransport` (la fixture autouse anti-red sigue activa).
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from investbot import ai_explain, ai_rewrite, gemini_client

from .gemini_helpers import CANARY_BODY, gemini_response

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gemini_smoke.py"
LIVE_KEY = "LIVE-SMOKE-KEY-FOR-TESTS-0123456789"
SMALL = ["--explain-reps", "1", "--rewrite-samples", "2", "--rewrite-reps", "1"]
CODES_10 = ("mod", "ben", "ren", "ver", "gra", "dcf", "rsk", "pig", "vf", "alz")
ESCAPE_RE = re.compile(r"<U\+[0-9A-F]{4,6}>")
LABEL_RE = re.compile(r"^\[(\w+) rep (\d+) (ACEPTADO|RECHAZADO:[a-z_]+)\]")


def _load():
    spec = importlib.util.spec_from_file_location("gemini_smoke_f0_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke():
    return _load()


@pytest.fixture(autouse=True)
def _restaura_logger_investbot():
    """`main` fija niveles y (C8) handlers/propagate del logger `investbot`."""
    nombres = ("httpx", "httpcore", "investbot")
    niveles = {n: logging.getLogger(n).level for n in nombres}
    inv = logging.getLogger("investbot")
    handlers, propagate = list(inv.handlers), inv.propagate
    yield
    for n, nivel in niveles.items():
        logging.getLogger(n).setLevel(nivel)
    inv.handlers[:] = handlers
    inv.propagate = propagate


def _env(**extra):
    return {"GEMINI_LIVE_KEY": LIVE_KEY, "GEMINI_LIVE_CONFIRM": "yes", **extra}


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _handler(explain_text, seen=None, finish="STOP"):
    """Explicaciones -> `explain_text` (str o callable(n)); rewrite -> JSON válido."""
    contador = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.headers["x-goog-api-key"].startswith("FAKE-NEGATIVE"):
            return httpx.Response(400, json={"error": {"message": CANARY_BODY}})
        if "responseMimeType" in request.content.decode():
            texto = '{"0": "REWRITE-MARCADOR", "1": "x", "2": "y"}'
            return httpx.Response(200, json=gemini_response(texto))
        contador[0] += 1
        texto = explain_text(contador[0]) if callable(explain_text) else explain_text
        return httpx.Response(200, json=gemini_response(texto, finish_reason=finish))

    return handler


def _run_main(smoke, argv, handler, capsys, env=None):
    rc = smoke.main(argv, env or _env(), False, _factory(handler))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _permitido(ch: str) -> bool:
    return ch == "\n" or ch == " " or unicodedata.category(ch)[0] in "LMNPS"


# ---------------------------------------------------------------------------
# CS-28 / C3 + C9: `_safe_display`
# ---------------------------------------------------------------------------

# (nombre, texto, code points que deben quedar escritos como <U+XXXX>)
_PELIGROSOS = [
    ("esc_csi", "a\x1b[31mrojo\x1b[0m", [0x1B]),
    ("osc52", "a\x1b]52;c;QUJD\x07b", [0x1B, 0x07]),
    ("c1_9b", "a\x9b31mb", [0x9B]),
    ("bidi_202e", "a‮b", [0x202E]),
    ("separador_2028", "a b", [0x2028]),
    ("separador_2029", "a b", [0x2029]),
    ("arabic_letter_mark_061c", "a؜b", [0x61C]),
    ("tag_chars", "a\U000e0041\U000e007fb", [0xE0041, 0xE007F]),
    ("zwj", "👨‍👩", [0x200D]),
    ("nul", "a\x00b", [0x00]),
    ("nel_0085", "a\x85b", [0x85]),
    ("soft_hyphen", "a­b", [0xAD]),
    ("bom_zwsp", "﻿a​b", [0xFEFF, 0x200B]),
    ("surrogate_suelto", "a\ud800b", [0xD800]),
    ("uso_privado", "ab", [0xE000]),
    ("no_asignado", "a͸b", [0x378]),
    ("tab_y_cr", "a\tb\rc", [0x09, 0x0D]),
]


@pytest.mark.parametrize("nombre,texto,escapados", _PELIGROSOS, ids=[c[0] for c in _PELIGROSOS])
def test_cs28_safe_display_c9_neutraliza_caracteres_peligrosos(nombre, texto, escapados):
    salida = ai_explain._safe_display(texto)
    for cp in escapados:
        assert f"<U+{cp:04X}>" in salida
    sin_escapes = ESCAPE_RE.sub("", salida)
    assert all(_permitido(ch) for ch in sin_escapes)  # lista blanca: nada más sobrevive
    assert len(salida) <= 2500


def test_cs28_safe_display_c3_conserva_texto_legible_y_saltos_de_linea():
    texto = "💡 En simple\nValor justo: $496.00 — 15.3% ✅ ñandú 日本語 é\n\n• ítem"
    assert ai_explain._safe_display(texto) == texto  # L, M, N, P, S y '\n' pasan intactos
    assert ai_explain._safe_display("a b　c d") == "a b c d"  # Zs -> ' '


def test_cs28_safe_display_c3_conserva_marcas_combinantes_categoria_m():
    # Mn (acento combinante, VS16 aislado) y Mc se conservan: la lista blanca incluye M.
    texto = "e\u0301 \u0903 a\ufe0f"
    assert ai_explain._safe_display(texto) == texto
    assert {unicodedata.category(c) for c in "\u0301\u0903\ufe0f"} == {"Mn", "Mc"}


def test_cs28_safe_display_c3_lista_blanca_exhaustiva_sobre_los_planos_relevantes():
    # TODOS los code points de los planos 0-2 más tag chars y planos privados (sin '\n').
    rangos = (range(0x0, 0x30000), range(0xE0000, 0xE1000), range(0xF0000, 0xF0100))
    todo = "".join(chr(cp) for r in rangos for cp in r if cp != 0x0A)
    salida = ai_explain._safe_display(todo, max_chars=10**8)
    assert len(salida) > 100_000  # control positivo: sí recorrió el rango
    assert all(_permitido(ch) for ch in ESCAPE_RE.sub("", salida))
    for cp in (0x1B, 0x9B, 0x202E, 0x2028, 0x61C, 0xE0041, 0x200D, 0x00, 0xD800):
        assert f"<U+{cp:04X}>" in salida


def test_cs28_safe_display_c3_tope_de_caracteres_con_10000_caracteres():
    assert len(ai_explain._safe_display("A" * 10_000)) == 2500  # defecto
    assert ai_explain._safe_display("A" * 10_000, max_chars=37) == "A" * 37
    peor = ai_explain._safe_display("\x1b" * 10_000)  # cada carácter cuesta 8
    assert len(peor) <= 2500 and peor == "<U+001B>" * (2500 // 8)
    assert ai_explain._safe_display("abc", max_chars=0) == ""


def test_cs28_safe_display_c3_nunca_parte_un_escape_a_la_mitad():
    for tope in range(0, 40):
        salida = ai_explain._safe_display("a\x1bb\U000e0041c‮d", max_chars=tope)
        assert len(salida) <= tope
        assert re.fullmatch(r"(?:<U\+[0-9A-F]{4,6}>|[a-d])*", salida), (tope, salida)


def test_cs28_safe_display_c3_maximo_40_lineas():
    texto = "\n".join(f"linea {i}" for i in range(100))
    salida = ai_explain._safe_display(texto, max_chars=10**6)
    assert salida.count("\n") == 39 and salida.splitlines()[-1] == "linea 39"
    justo = "\n".join("x" for _ in range(40))
    assert ai_explain._safe_display(justo) == justo  # 40 líneas exactas pasan completas


def test_cs28_safe_display_keep_newlines_false_aplana_a_una_linea():
    assert ai_explain._safe_display("a\nb\r\nc", keep_newlines=False) == "a b<U+000D> c"
    assert "\n" not in ai_explain._safe_display("\n" * 50, keep_newlines=False)


def test_cs28_safe_display_es_idempotente_y_tolera_entradas_no_str():
    mezcla = "a\x1b[0m‮\n؜\U000e0041 ñ"
    una_vez = ai_explain._safe_display(mezcla)
    assert ai_explain._safe_display(una_vez) == una_vez
    assert ai_explain._safe_display(None) == ""
    assert ai_explain._safe_display(12345) == "12345"


def test_cs28_m6_el_log_de_rechazo_de_produccion_pasa_por_safe_display(caplog):
    cruda = "@alguien \x1b[31m‮ ؜\x9b fin " + ("y" * 2000)
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=gemini_response(cruda))))
    clients = SimpleNamespace(gemini_http=http, gemini_config=None)
    config = gemini_client.GeminiConfig(enabled=True, api_key="TEST-DUMMY-KEY-0123456789ABCDEF")
    async def ir():
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                clients=clients, config=config, question_code="vf", pregunta_fija="p",
                datos_del_contexto={"precio_actual": 500.0}, datos_tokens=set())
        await http.aclose()

    with caplog.at_level(logging.WARNING, logger="investbot"):
        asyncio.run(ir())
    mensaje = [r.getMessage() for r in caplog.records if "GEMINI_GUARD_REJECT" in r.getMessage()][0]
    _, _, logueada = mensaje.partition("respuesta=")
    assert len(logueada) <= 500 and "<U+202E>" in logueada and "<U+0085>" not in logueada
    assert all(_permitido(ch) and ch != "\n" for ch in ESCAPE_RE.sub("", mensaje))


# ---------------------------------------------------------------------------
# CS-25(b): `_compute_datos_tokens` única
# ---------------------------------------------------------------------------


def _tokens_head(datos):
    """Expresión de HEAD copiada literal (la que antes vivía en el handler)."""
    return {
        ai_explain._normalize_numeric_token(token)
        for token in ai_rewrite.protected_tokens(json.dumps(datos, ensure_ascii=False, default=str))
    }


_DATOS_MUESTRA = [
    {"precio_actual": 500.0, "valor_justo": 496.0, "pct": "15.3%", "peers_usados": ["ZZPEER"]},
    {"a": {"b": [1.5, 2, {"c": "$1,234.56 y 7.0%"}]}, "ev": Decimal("123.45"), "t": None, "ok": True},
    {"texto": "Año ñ 日本 con 3.14 y -2.5 y 12,5%", "cuenta": "496.00 = 0.4 × 1240.0"},
    {},
]


@pytest.mark.parametrize("datos", _DATOS_MUESTRA, ids=["plano", "anidado_default_str", "texto_cuenta", "vacio"])
def test_cs25_b_compute_datos_tokens_sin_presentado_equivale_a_head(datos):
    assert ai_explain._compute_datos_tokens(datos) == _tokens_head(datos)
    assert ai_explain._compute_datos_tokens(datos, None) == _tokens_head(datos)
    assert isinstance(ai_explain._compute_datos_tokens(datos), set)


def test_cs25_b_compute_datos_tokens_con_presentado_es_la_union_sin_lavar():
    crudo = {"market_cap": 394328000000.0, "precio": 285.754321, "pct": 0.153}
    presentado = {"market_cap": "$394.3 mil millones", "precio": "$285.75", "pct": "15.3%"}
    union = ai_explain._compute_datos_tokens(crudo, presentado)
    assert _tokens_head(crudo) <= union  # nunca se pierde un token del crudo
    assert _tokens_head(presentado) <= union
    assert ai_explain._normalize_numeric_token("$285.75") in union
    # la unión no inventa nada: una cifra fuera de ambos sigue sin estar
    assert ai_explain._normalize_numeric_token("987654.31") not in union
    assert not ai_explain._no_new_protected_tokens(union, "El valor es 987654.31.")
    assert ai_explain._no_new_protected_tokens(union, "Capitalización de $394.3 mil millones.")


def test_cs25_b_el_handler_calcula_datos_tokens_con_la_funcion_unica(smoke, monkeypatch):
    llamadas = []
    original = ai_explain._compute_datos_tokens

    def espia(*args, **kwargs):
        llamadas.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(ai_explain, "_compute_datos_tokens", espia)
    ctx = smoke._texto_libre_context(ai_explain, 987654321)
    store = ai_explain.ExplanationContextStore()
    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler("Explicación breve sin cifras.")))
    config = gemini_client.load_config({
        gemini_client.ENV_API_KEY: LIVE_KEY, gemini_client.ENV_REWRITE_ENABLED: "true"})
    clients = SimpleNamespace(gemini_http=http, gemini_config=config)
    spec = ai_explain_content_spec("vf")

    async def ir():
        resultado = await smoke._explain_once(ai_explain, clients, store, ctx, "vf", spec, 987654321)
        await http.aclose()
        return resultado

    final = asyncio.run(ir())
    assert final is not None and final != ai_explain.EXPLAIN_UNAVAILABLE_MSG
    assert len(llamadas) == 1  # exactamente una vez por clic
    (args, kwargs) = llamadas[0]
    datos = args[0] if args else kwargs["datos_crudos"]
    assert isinstance(datos, dict) and "cuenta" in datos  # la cuenta entra ANTES del cálculo


def ai_explain_content_spec(code):
    from investbot import ai_explain_content

    return {**ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO}[code]


def test_cs25_b_el_smoke_no_repite_json_dumps_mas_protected_tokens():
    fuente = SCRIPT.read_text(encoding="utf-8")
    prohibidos = (
        re.compile(r"\bdumps\("),
        re.compile(r"(?<![A-Za-z_])protected" + r"_tokens\("),  # no matchea `_no_new_protected_tokens(`
        re.compile(r"\b_normalize" + r"_numeric_token\("),
    )

    def hay_calculo_duplicado(src: str) -> bool:
        return any(p.search(src) for p in prohibidos)

    assert not hay_calculo_duplicado(fuente)
    assert "_compute_datos_tokens(" in fuente  # y sí usa la función compartida
    # control positivo: el detector sí atrapa el cálculo duplicado de HEAD
    assert hay_calculo_duplicado("ai_rewrite." + "protected" + "_tokens(_json." + "dumps" + "(datos))")
    # ninguna línea combina ambos, aun si en el futuro se importaran con otro alias
    assert not any(("dumps" in ln and "protected_tokens" in ln) for ln in fuente.splitlines())


def test_cs25_b_smoke_y_handler_calculan_los_mismos_tokens_para_los_10_codigos(smoke, monkeypatch, capsys):
    vistos = []
    original = ai_explain._no_new_protected_tokens

    def espia(datos_tokens, respuesta):
        vistos.append(frozenset(datos_tokens))
        return original(datos_tokens, respuesta)

    monkeypatch.setattr(ai_explain, "_no_new_protected_tokens", espia)
    rc, out, _ = _run_main(smoke, ["--explain-reps", "1", "--rewrite-samples", "1", "--rewrite-reps", "1"],
                           _handler("Explicación breve sin cifras."), capsys)
    assert rc == 0
    # por código: primero el guard del handler y luego el de las métricas del smoke
    por_codigo = vistos[:2 * len(CODES_10)]
    assert len(por_codigo) == 2 * len(CODES_10)
    assert por_codigo[0::2] == por_codigo[1::2]
    assert sum(len(t) > 0 for t in por_codigo[0::2]) >= 8  # control positivo: conjuntos con cifras
    assert len(set(por_codigo[0::2])) > 1  # y distintos entre códigos (no un conjunto fijo)


# ---------------------------------------------------------------------------
# CS-28 / C1-C8: flag `--show-text` del smoke
# ---------------------------------------------------------------------------


def test_cs28_c1_show_text_apagado_por_defecto_y_flags_exactos(smoke):
    args = smoke._parse_args([])
    assert args.show_text is False and args.show_text_max == 6
    args = smoke._parse_args(["--show-text"])
    assert args.show_text is True and args.show_text_max == 6
    assert smoke._parse_args(["--show-text", "--show-text-max", "30"]).show_text_max == 30
    for malo in ("0", "31", "-1", "abc"):
        assert smoke._parse_args(["--show-text", "--show-text-max", malo]) is None


def test_cs28_c1_sin_el_flag_no_se_imprime_ningun_texto_del_modelo(smoke, capsys):
    rc, out, err = _run_main(smoke, SMALL, _handler("texto-del-modelo-xyz sin cifras."), capsys)
    assert rc == 0
    assert "texto-del-modelo-xyz" not in out and "texto-del-modelo-xyz" not in err
    assert "--show-text" not in out and "[mod rep" not in out


def test_cs28_c1_con_el_flag_imprime_etiqueta_y_texto_saneado(smoke, capsys):
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"], _handler("texto-del-modelo-xyz sin cifras."), capsys)
    assert rc == 0
    assert out.count("texto-del-modelo-xyz") == len(CODES_10)  # 1 rep x 10 códigos
    etiquetas = [LABEL_RE.match(ln) for ln in out.splitlines() if ln.startswith("[") and " rep " in ln]
    assert [m.group(1) for m in etiquetas if m] == list(CODES_10)  # C5: formato y orden
    assert all(m.group(3) == "ACEPTADO" for m in etiquetas)


def test_cs28_c1_show_text_max_limita_los_textos_por_codigo(smoke, capsys):
    argv = ["--explain-reps", "4", "--rewrite-samples", "1", "--rewrite-reps", "1", "--show-text",
            "--show-text-max", "2"]
    rc, out, _ = _run_main(smoke, argv, _handler("texto-del-modelo-xyz sin cifras."), capsys)
    assert rc == 0
    assert out.count("texto-del-modelo-xyz") == 2 * len(CODES_10)
    assert "[mod rep 3" not in out and "[mod rep 2 ACEPTADO]" in out
    rc, out, _ = _run_main(smoke, ["--show-text-max", "31", "--show-text"], _handler("x"), capsys)
    assert rc == 64 and "ARGUMENTOS_INVALIDOS" in out


def test_cs28_c2_solo_explicaciones_stop_y_nunca_secretos_ni_cuerpos(smoke, capsys):
    rc, out, err = _run_main(smoke, [*SMALL, "--show-text"], _handler("texto-del-modelo-xyz ok."), capsys)
    assert rc == 0
    assert "REWRITE-MARCADOR" not in out  # nunca el texto del rewrite
    for prohibido in (LIVE_KEY, CANARY_BODY, "x-goog-api-key", "CANARY_EMPRESA_SA", "ZZCANARY"):
        assert prohibido not in out and prohibido not in err
    # finishReason != STOP: el texto no se imprime
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"],
                           _handler("texto-truncado-maxtok", finish="MAX_TOKENS"), capsys)
    assert rc == 0 and "texto-truncado-maxtok" not in out and "[mod rep" not in out


def test_cs28_c3_integracion_la_consola_solo_recibe_categorias_permitidas(smoke, capsys):
    # Estos caracteres NO los rechaza `violates_output_filter` (brecha BL-1): el único
    # control es `_safe_display`.
    hostil = "inicio \x9b31m   ؜ \U000e0041 \x85 fin"
    rc, out, err = _run_main(smoke, [*SMALL, "--show-text"], _handler(hostil), capsys)
    assert rc == 0
    for cp in (0x9B, 0x2028, 0x61C, 0xE0041, 0x85):
        assert f"<U+{cp:04X}>" in out  # control positivo: se escribieron como texto
    assert all(_permitido(ch) for ch in out + err)  # sin ESC, C1, bidi, Zl/Zp ni tags


def test_cs28_c3_tope_de_2500_caracteres_y_40_lineas_en_la_consola(smoke, capsys):
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"], _handler("z" * 10_000), capsys)
    assert rc == 0
    cuerpos = [ln for ln in out.splitlines() if ln.startswith("zzz")]
    assert len(cuerpos) == len(CODES_10) and all(len(ln) == 2500 for ln in cuerpos)
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"],
                           _handler("\n".join(f"linea{i}" for i in range(100))), capsys)
    assert out.count("linea0") >= 1 and "linea39\n" in out and "linea40" not in out


def test_cs28_c4_un_texto_rechazado_por_el_filtro_de_salida_no_se_imprime(smoke, capsys):
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"],
                           _handler("mira http://evil.example marcador-filtro"), capsys)
    assert rc == 0
    assert "marcador-filtro" not in out and "evil.example" not in out
    assert "[mod rep 1 RECHAZADO:filtro_salida] (texto no mostrado: filtro_salida; chars=" in out
    # un texto que además falla el guard de números tampoco se muestra si el filtro lo rechaza
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"],
                           _handler("cifra 987654.31 http://evil.example marcador-doble"), capsys)
    assert "marcador-doble" not in out and "evil.example" not in out


def test_cs28_c4_el_filtro_se_evalua_tambien_sobre_el_texto_normalizado(smoke, capsys):
    # Se rechazan en producción tras normalizar (backtick intercalado): el smoke no los imprime.
    for crudo, oculto in (
        ("hola ht`tps://evil.com marcador-bt", "marcador-bt"),
        ("hola www`.evil.com marcador-www", "marcador-www"),
        ("987654.31 ht`tps://evil.com marcador-num", "marcador-num"),
    ):
        rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"], _handler(crudo), capsys)
        assert rc == 0
        assert oculto not in out and "evil.com" not in out
        # con cifra inventada la razón de rechazo es guard_numeros (precede), pero igual se oculta
        razon = "guard_numeros" if "987654" in crudo else "filtro_salida"
        assert f"[mod rep 1 RECHAZADO:{razon}] (texto no mostrado: filtro_salida; chars=" in out


def test_cs28_c4_un_texto_rechazado_por_guard_de_numeros_si_se_imprime_saneado(smoke, capsys):
    rc, out, _ = _run_main(smoke, [*SMALL, "--show-text"],
                           _handler("dato inventado 987654.31 marcador-numeros"), capsys)
    assert rc == 0
    assert "[mod rep 1 RECHAZADO:guard_numeros]" in out
    assert "dato inventado 987654.31 marcador-numeros" in out  # control positivo de C4


def test_cs28_c6_no_escribe_archivos_ni_imprime_variables_de_entorno(smoke, capsys, monkeypatch):
    intentos = []

    def open_prohibido(*args, **kwargs):
        intentos.append(args)
        raise AssertionError("el smoke no debe abrir archivos")

    env = _env(VARIABLE_SECRETA_CANARIO="VALOR-ENTORNO-SECRETO")
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", open_prohibido)
        rc = smoke.main([*SMALL, "--show-text"], env, False, _factory(_handler("Explicación breve.")))
    out = capsys.readouterr()
    assert rc == 0 and intentos == []
    assert "VALOR-ENTORNO-SECRETO" not in out.out + out.err and "VARIABLE_SECRETA" not in out.out + out.err
    assert "Explicación breve." in out.out  # control positivo: sí imprimió con --show-text
    fuente = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"print\([^)]*(?:environ|\benv\b)", fuente)


def test_cs28_c7_evaluate_guards_sin_diff_y_las_guardas_no_se_aflojan(smoke, capsys):
    # sha256 de `inspect.getsource(evaluate_guards)` capturado de HEAD 502d5df
    huella = hashlib.sha256(inspect.getsource(smoke.evaluate_guards).encode()).hexdigest()
    assert huella == "7c1a8b4563349d3eb4b69f8f677394a983799359faa458bf55576b8ba751afdd"
    assert smoke.CALL_LIMIT == 150
    for cambio, token in (({"CI": "true"}, "GUARD_CI"), ({"TELEGRAM_BOT_TOKEN": "1:a"}, "GUARD_TELEGRAM_BOT_TOKEN_SET"),
                          ({"FMP_API_KEY": "k"}, "GUARD_FMP_API_KEY_SET"),
                          ({"GEMINI_LIVE_CONFIRM": "no"}, "GUARD_NO_CONFIRM")):
        seen = []
        rc = smoke.main(["--show-text"], _env(**cambio), False, _factory(_handler("x", seen)))
        assert rc == 2 and seen == [] and token in capsys.readouterr().out
    seen = []
    assert smoke.main(["--show-text"], _env(), True, _factory(_handler("x", seen))) == 2 and seen == []


def test_cs28_c8_null_handler_y_propagate_false_durante_la_corrida_y_restaurados_al_salir(smoke, capsys):
    inv = logging.getLogger("investbot")
    antes = (list(inv.handlers), inv.propagate)
    estado = []

    def handler(request):
        estado.append((any(isinstance(h, logging.NullHandler) for h in inv.handlers), inv.propagate))
        return _handler("Explicación breve.")(request)

    rc, _, _ = _run_main(smoke, SMALL, handler, capsys)
    assert rc == 0 and estado
    assert all(nulo and not propaga for nulo, propaga in estado)  # durante `_run`
    assert (list(inv.handlers), inv.propagate) == antes  # restaurado al salir


def test_cs28_c8_restaura_el_logger_aun_si_la_corrida_falla(smoke, capsys):
    inv = logging.getLogger("investbot")
    antes = (list(inv.handlers), inv.propagate)

    def factory():
        raise ZeroDivisionError("falla")

    assert smoke.main(SMALL, _env(), False, factory) == 5
    assert (list(inv.handlers), inv.propagate) == antes
    assert "ZeroDivisionError" in capsys.readouterr().out


def test_cs28_c8_el_rechazo_del_guard_no_llega_a_stderr_ni_a_handlers_ancestros(smoke, capsys):
    registros = []

    class Espia(logging.Handler):
        def emit(self, record):
            registros.append(record.getMessage())

    raiz = logging.getLogger()
    espia = Espia(level=logging.DEBUG)
    raiz.addHandler(espia)
    try:
        rc, out, err = _run_main(smoke, SMALL, _handler("cifra inventada 987654.31 rara"), capsys)
    finally:
        raiz.removeHandler(espia)
    assert rc == 0
    assert not any("GEMINI_GUARD_REJECT" in m for m in registros)  # propagate=False
    assert "GEMINI_GUARD_REJECT" not in out and "GEMINI_GUARD_REJECT" not in err
    assert "guard_numeros" in out  # el rechazo sí se reporta, como razón fija


# ---------------------------------------------------------------------------
# Smoke de F0: 10 códigos, `--thinking`, métricas
# ---------------------------------------------------------------------------


def test_cs13_f0_el_smoke_cubre_10_codigos_con_vf_y_alz_y_80_llamadas_por_corrida(smoke, capsys):
    assert smoke._EXPLAIN_QUESTIONS == CODES_10
    seen = []
    rc, out, _ = _run_main(smoke, [], _handler("Explicación breve sin cifras.", seen), capsys)
    assert rc == 0
    for codigo in CODES_10:
        assert f"  [{codigo}] n=5 " in out
    assert "CA-S1 (10 preguntas" in out
    explicaciones = [r for r in seen if "responseMimeType" not in r.content.decode()
                     and not r.headers["x-goog-api-key"].startswith("FAKE-NEGATIVE")]
    rewrites = [r for r in seen if "responseMimeType" in r.content.decode()
                and not r.headers["x-goog-api-key"].startswith("FAKE-NEGATIVE")]
    assert len(explicaciones) == 10 * 5 and len(rewrites) == 10 * 3  # 80 con los defaults
    assert len(seen) <= 150


def test_cs12_f0_thinking_se_aplica_a_una_copia_y_no_toca_el_entorno_del_proceso(smoke, capsys, monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "high")  # el entorno real jamás debe influir
    antes = dict(os.environ)

    def nivel(argv):
        seen = []
        rc, out, _ = _run_main(smoke, argv, _handler("Explicación breve.", seen), capsys)
        assert rc == 0
        cuerpo = json.loads(seen[0].content.decode())
        return cuerpo["generationConfig"]["thinkingConfig"]["thinkingLevel"], out

    assert nivel(SMALL)[0] == "minimal"  # default None: no se toca
    nivel_low, out = nivel([*SMALL, "--thinking", "low"])
    assert nivel_low == "low" and "thinkingLevel=low" in out
    assert nivel([*SMALL, "--thinking", "minimal"])[0] == "minimal"
    assert dict(os.environ) == antes
    assert smoke._parse_args([]).thinking is None


def test_cs12_f0_thinking_solo_admite_minimal_y_low(smoke, capsys):
    seen = []
    for valor in ("high", "medium", "LOW", ""):
        assert smoke.main(["--thinking", valor], _env(), False, _factory(_handler("x", seen))) == 64
    assert seen == [] and capsys.readouterr().out.count("ARGUMENTOS_INVALIDOS") == 4


_CON_ETIQUETAS = "💡 En simple\nalgo\n\n🔎 Qué dicen los datos\nmás\n\n🧭 Cómo leerlo\nfin"


def test_cs13_f0_metricas_de_redaccion_con_regex_propias(smoke):
    assert smoke._etiquetas_conformes(_CON_ETIQUETAS)
    assert smoke._etiquetas_conformes(_CON_ETIQUETAS.replace("Qué dicen los datos", "Qué es cada pieza")
                                      .replace("Cómo leerlo", "Qué significa el resultado"))
    assert not smoke._etiquetas_conformes("Texto plano de la versión vieja.")  # baseline: 0 por definición
    assert not smoke._etiquetas_conformes(_CON_ETIQUETAS.replace("🧭 Cómo leerlo", "Cómo leerlo"))
    for md in ("**negrita**", "__x__", "usa `code`", "# titulo", "  ## sub", "- viñeta", "* viñeta"):
        assert smoke._tiene_markdown(md), md
    for limpio in ("• viñeta", "sin marcas 15-3 y 5*2", "guion - medio"):
        assert not smoke._tiene_markdown(limpio), limpio
    assert smoke._tiene_snake_case("el campo valor_justo_total") and smoke._tiene_snake_case("net_debt_to_ebitda")
    assert not smoke._tiene_snake_case("Valor Justo, sin guion_ bajo Mayúsc_Ula")
    assert (smoke._p50([]), smoke._p50([7]), smoke._p50([1, 2, 3, 4]), smoke._p50([9, 1, 5])) == (None, 7, 2, 5)


def test_cs13_f0_el_resumen_informa_aceptacion_por_los_dos_guards_de_head_y_razones_aparte(smoke, capsys):
    textos = ["limpio sin cifras", "http://evil.example", "cifra 987654.31", "**md** valor_justo_total"]
    rc, out, _ = _run_main(smoke, ["--explain-reps", "4", "--rewrite-samples", "1", "--rewrite-reps", "1"],
                           _handler(lambda n: textos[(n - 1) % 4]), capsys)
    assert rc == 0
    # por código: 4 reps -> 1 limpio + 1 con markdown/snake que sí pasa los guards = 2 aceptables; 2 rechazos
    linea = [ln for ln in out.splitlines() if ln.startswith("    [mod]")][0]
    assert "guards_head=2/4" in linea and "markdown=1" in linea and "snake_case=1" in linea
    assert "etiquetas_ok=0/4" in linea and "largo_p50=" in linea and "largo_p95=" in linea
    total = [ln for ln in out.splitlines() if ln.startswith("  [explicaciones-total]")][0]
    assert "guards_head=20/40" in total and "tokens_pensamiento_total=" in total
    detalle = [ln for ln in out.splitlines() if ln.startswith("  [mod]")][0]
    assert "'guard_numeros': 1" in detalle and "'filtro_salida': 1" in detalle  # razones aparte
    assert "MARCADOR" not in out and "evil.example" not in out  # sin --show-text no hay texto
