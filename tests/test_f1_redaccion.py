"""Tests de la FASE F1 de `SDD_redaccion_gemini_iter2.md` (secciones 16, 17, 18).

Cubre CA-70 a CA-85 y CS-23 a CS-26, CS-29 a CS-36: prompts y `SECTION_LABELS`,
`_normalize_layout`, `_fit_respuesta`/`_fit_leaf_message`, `_escalas_consistentes`,
`_present_payload`/`_PAYLOAD_KIND`/`_money_compact`, campos derivados y el
pipeline de `_fetch_explanation`. Sin red (MockTransport + fixture anti-red).

Nombres `test_ca<n>_` / `test_cs<n>_` (CA-64); los sub-criterios llevan guion
bajo (`test_cs23_a_...`). Los ids parametrizados por código usan los 25
códigos con llamada al modelo (`_PRESENTABLE_CODES`: sin `evt` ni `inf`).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import random
import re
import time
from collections import Counter
from itertools import product
from pathlib import Path

import httpx
import pytest

from investbot import ai_explain, ai_explain_content, ai_rewrite, gemini_client
from investbot.query_handler import TELEGRAM_MESSAGE_LIMIT
from investbot.summary import DISCLAIMER_NO_ASESORAMIENTO

from .gemini_helpers import gemini_response, request_body, system_text, user_text
from .test_ai_explain import (
    FakeRateLimiter,
    _avanzado_context,
    _build_callback,
    _client_with_handler,
    _CountingClient,
    _enabled_config,
    _fake_callback_update,
    _fetch_kwargs,
    _make_clients,
    _ok_handler,
    _texto_libre_context,
)

ROOT = Path(__file__).resolve().parent.parent

_PRESENTABLE_CODES = [
    c
    for c in list(ai_explain_content.QUESTIONS_TEXTO_LIBRE) + list(ai_explain_content.QUESTIONS_AVANZADO)
    if c not in ("evt", "inf")
]

_FRASE = "La empresa muestra un resultado estable y conviene mirarlo con calma. "


def _len16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _contexto_para(code: str):
    if code in ai_explain_content.QUESTIONS_AVANZADO:
        return _avanzado_context(precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0)
    return _texto_libre_context()


def _crudo_y_presentado(code: str):
    ctx = _contexto_para(code)
    crudo = ai_explain._build_explain_payload(ctx, code)
    spec = ai_explain_content.all_questions(ctx.kind)[code]
    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
        cuenta = ai_explain._build_cuenta_line(ctx.kind, code, crudo)
        if cuenta is not None:
            crudo["cuenta"] = cuenta
    return crudo, ai_explain._datos_para_el_modelo(code, crudo)


def _hojas(obj, clave=None):
    """(clave del dict padre más cercano, valor) de cada hoja, en orden."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _hojas(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _hojas(v, clave)
    else:
        yield clave, obj


async def _correr_boton(code, texto_modelo, *, paso_a_paso=True, ctx=None, http=None):
    ctx = ctx if ctx is not None else _texto_libre_context()
    store = ai_explain.ExplanationContextStore()
    cid = store.put(ctx)
    client = http if http is not None else _client_with_handler(_ok_handler(texto_modelo))
    clients = _make_clients(http_client=client, gemini_config=_enabled_config())
    callback = _build_callback(clients, FakeRateLimiter(), store)
    data = f"xp:{cid}:p:{code}" if paso_a_paso else f"xp:{cid}:{code}"
    update, query, context = _fake_callback_update(data)
    await callback(update, context)
    return context.bot


def _texto_final(bot) -> str:
    bot.edit_message_text.assert_awaited_once()
    return bot.edit_message_text.call_args.kwargs["text"]


def _parrafos_hasta(n_chars: int) -> str:
    """Texto de párrafos de ~3 oraciones; todo párrafo y oración termina en '.'."""
    parrafo = (_FRASE * 3).rstrip()
    partes = []
    while len("\n\n".join(partes)) < n_chars:
        partes.append(parrafo)
    return "\n\n".join(partes)


# ---------------------------------------------------------------------------
# CA-70 / CA-80 / constantes
# ---------------------------------------------------------------------------


def _coincidencias(prohibidos, fuentes):
    return [
        (nombre, p)
        for nombre, fuente in fuentes
        for p in prohibidos
        if p in fuente
    ]


def test_ca70_no_quedan_las_funciones_y_constantes_del_tope_viejo_y_existen_las_nuevas():
    # Las dos cadenas se arman por concatenación para que este archivo no se auto-matchee.
    prohibidos = ("_enforce" + "_brevity", "_MAX_EXPLANATION" + "_CHARS")
    fuentes = []
    for carpeta in ("src", "scripts", "tests"):
        for ruta in sorted((ROOT / carpeta).rglob("*.py")):
            fuentes.append((str(ruta.relative_to(ROOT)), ruta.read_text(encoding="utf-8")))
    assert fuentes  # el recorrido encuentra archivos
    assert _coincidencias(prohibidos, fuentes) == []
    # control positivo con fuente sintética
    assert _coincidencias(prohibidos, [("sintetico.py", "x = " + prohibidos[0])]) != []
    assert _coincidencias(prohibidos, [("sintetico.py", "y = " + prohibidos[1])]) != []
    assert not hasattr(ai_explain, prohibidos[0]) and not hasattr(ai_explain, prohibidos[1])
    # constantes y funciones nuevas
    assert ai_explain._MAX_RESPUESTA_CHARS == 1800
    assert ai_explain._MIN_RESPUESTA_CHARS == 400
    assert ai_explain._MAX_RAW_CHARS == 6000
    assert ai_explain._TELEGRAM_LIMIT == TELEGRAM_MESSAGE_LIMIT == 4096
    assert ai_explain._MARGEN_TELEGRAM == 100
    assert callable(ai_explain._fit_respuesta) and callable(ai_explain._fit_leaf_message)
    # `_TELEGRAM_LIMIT` es propia: el módulo no importa `query_handler`.
    assert not hasattr(ai_explain, "query_handler") and not hasattr(ai_explain, "TELEGRAM_MESSAGE_LIMIT")


def test_ca80_tope_de_tokens_de_explicaciones_es_2048():
    assert ai_explain.MAX_EXPLANATION_OUTPUT_TOKENS == 2048


async def test_ca80_la_request_de_explicacion_lleva_max_output_tokens_2048():
    capturas = []

    def handler(request):
        capturas.append(request)
        return httpx.Response(200, json=gemini_response("Corto."))

    await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=_client_with_handler(handler)))
    assert request_body(capturas[0])["generationConfig"]["maxOutputTokens"] == 2048


# ---------------------------------------------------------------------------
# CA-71 : sin tope de 480
# ---------------------------------------------------------------------------


def _texto_con_cifras(n_chars: int) -> str:
    base = "El valor ronda $285.75 y el margen es 15.3%. "
    relleno = _FRASE
    texto = base
    while len(texto) < n_chars:
        texto += relleno
    return texto.rstrip()[: n_chars]


@pytest.mark.parametrize("n_chars", [1200, 5000])
async def test_ca71_la_respuesta_larga_sale_identica_tras_normalizar_sin_cortar_a_480(n_chars):
    texto = _texto_con_cifras(n_chars)
    if not texto.endswith("."):
        texto = texto[: texto.rfind(".") + 1]
    assert len(texto) > 480
    presentado = {"precio_actual": "$285.75", "roe": "15.3%"}
    tokens = ai_explain._compute_datos_tokens({"precio_actual": 285.75, "roe": 0.153}, presentado)
    client = _client_with_handler(_ok_handler(texto))
    resultado = await ai_explain._fetch_explanation(
        **_fetch_kwargs(http_client=client, datos=presentado, tokens=tokens)
    )
    assert resultado == ai_explain._normalize_layout(texto)
    assert len(resultado) > 480  # la función NO corta (el recorte es del paso de entrega)


# ---------------------------------------------------------------------------
# CA-72 : _fit_respuesta (propiedad) + control positivo
# ---------------------------------------------------------------------------


def _legacy_cut(t: str, n: int) -> str:
    # Réplica local del corte viejo (sin usar el nombre prohibido por CA-70).
    corte = t.rfind(".", 0, n)
    return t[: corte + 1] if corte > 0 else t[:n] + "…"


def _cumple_ca72(resultado, texto, max_chars) -> bool:
    if resultado is None:
        return True
    if resultado == "" or not texto.startswith(resultado):
        return False
    if _len16(resultado) > max_chars:
        return False
    if Counter(ai_rewrite.protected_tokens(resultado)) - Counter(ai_rewrite.protected_tokens(texto)):
        return False
    if max_chars < _len16(texto) and (resultado[-1].isdigit() or resultado[-1] in "$%"):
        return False
    return True


def _texto_ca72() -> str:
    p1 = "💡 En simple\nEl valor justo ronda $285.75 hoy. El margen es 15.3% y mejora. ¿Se entiende? Sí, claro."
    p2 = "🔎 Qué dicen los datos\nEl precio actual es $1,234.56 según el dato. Es una estimación que depende de supuestos."
    p3 = "🧭 Cómo leerlo\nUn valor alto suele indicar buena salud. Conviene mirar la deuda: ¡cuidado con el 3.5% extra! Fin."
    return "\n\n".join([p1, p2, p3, p1, p2, p3])


def test_ca72_barrido_de_max_chars_nunca_corta_un_numero_ni_excede_utf16():
    texto = _texto_ca72()
    assert texto.endswith(".") and _len16(texto) > len(texto)  # hay emojis fuera del BMP
    for max_chars in range(50, _len16(texto) + 1):
        resultado = ai_explain._fit_respuesta(texto, max_chars)
        assert _cumple_ca72(resultado, texto, max_chars), (max_chars, resultado)
    # extremo: max_chars == len16 devuelve el texto entero
    assert ai_explain._fit_respuesta(texto, _len16(texto)) == texto


def test_ca72_control_positivo_el_corte_viejo_incumple_la_propiedad():
    # "." decimal en la posición 479 (k = 469): el corte por último "." deja "$285."
    texto = "A" * 469 + " vale $285.75 hoy. " + "B" * 100
    assert texto.index("285.75") + 3 == 479
    violan = [n for n in range(50, len(texto) + 1) if not _cumple_ca72(_legacy_cut(texto, n), texto, n)]
    assert 480 in violan
    # y la función nueva cumple en TODO el barrido de ese mismo texto
    for n in range(50, len(texto) + 1):
        assert _cumple_ca72(ai_explain._fit_respuesta(texto, n), texto, n)


def test_ca72_encabezado_huerfano_se_quita_y_sin_corte_valido_es_none():
    cuerpo = "Una oración larga que ocupa lugar suficiente. " * 6
    texto = cuerpo.rstrip() + "\n\n🔎 Qué dicen los datos\n\n" + cuerpo.rstrip()
    max_chars = len(cuerpo.rstrip()) + 30
    resultado = ai_explain._fit_respuesta(texto, max_chars)
    assert resultado == cuerpo.rstrip()  # el encabezado quedó solo y se quitó
    # nada en el rango [0.4*max, max] -> None, nunca un corte en espacio ni "…"
    assert ai_explain._fit_respuesta("palabra " * 200, 400) is None
    # el umbral 0.4: la única oración termina antes del piso -> None
    assert ai_explain._fit_respuesta("Corta. " + "x" * 500, 400) is None
    # encabezado como único bloque que queda -> None
    assert ai_explain._fit_respuesta("🧭 Cómo leerlo\n\n" + "y" * 500, 400) is None
    # el candidato es SOLO un encabezado (queda vacío al quitarlo) -> None, sin recortar nada
    assert ai_explain._fit_respuesta("🧭 Cómo leerlo\n\n" + "y" * 500, 30) is None


def test_ca72_el_decimal_no_es_fin_de_oracion_y_prefiere_parrafo_sobre_oracion():
    texto = "Primera oracion completa aqui. Segunda con $285.75 en medio y mas texto sin cierre"
    assert ai_explain._fit_respuesta(texto, 60) == "Primera oracion completa aqui."
    dos = "Uno dos tres cuatro cinco. Seis siete ocho nueve diez.\n\nOnce doce trece catorce quince. Final."
    assert ai_explain._fit_respuesta(dos, 90) == "Uno dos tres cuatro cinco. Seis siete ocho nueve diez."


# ---------------------------------------------------------------------------
# CA-73 : presupuesto de entrega (_fit_leaf_message), escenarios A-F
# ---------------------------------------------------------------------------

_DATO = "Valor Justo Total (Conservador): $496.00 — precio actual: $430.00"
_FUENTE = "Fuente de prueba."
_CUENTA = "(" + "$500.00 + " * 60 + "$1.00) / 61 = $496.00"


def _restante(formula, *, cuenta, desglose, grafico):
    base = ai_explain._build_leaf_message(
        _DATO, "", formula, _FUENTE, cuenta=cuenta, desglose=desglose, grafico=grafico
    )
    return 4096 - 100 - _len16(base)


def _formula_para(target, *, cuenta, desglose, grafico):
    """Fórmula sintética tal que `restante(con grafico y desglose) == target`."""
    n = _restante("f", cuenta=cuenta, desglose=desglose, grafico=grafico) - target + 1
    assert n >= 1
    return "f" * n


def _armar(target, **bloques):
    formula = _formula_para(target, **bloques)
    assert _restante(formula, **bloques) == target
    return formula


_G = "📊 Valor justo vs precio\n" + "Fila ████████████ $500.00\n" * 3
_D = "🔍 Desglose:\n" + "• Término — qué mide.\n" * 40


def _chequear_mensaje(msg, respuesta_o_inicio):
    assert msg is not None
    assert _len16(msg) <= 4096
    assert _DATO in msg and "🧮 Cuenta" in msg and DISCLAIMER_NO_ASESORAMIENTO in msg
    assert respuesta_o_inicio in msg


def test_ca73_a_restante_suficiente_sin_recorte_ni_omisiones():
    d_chico = "🔍 Desglose:\n" + "• Término — qué mide.\n" * 6
    bloques = dict(cuenta="($500.00 + $480.00) / 2 = $490.00", desglose=d_chico, grafico=_G)
    formula = _armar(1900, **bloques)
    respuesta = _parrafos_hasta(1500)
    msg = ai_explain._fit_leaf_message(_DATO, respuesta, formula, _FUENTE, **bloques)
    _chequear_mensaje(msg, respuesta)
    assert _G in msg and d_chico in msg and ai_explain._NOTA_ACORTADA not in msg


def test_ca73_b_con_400_a_1800_de_restante_recorta_sin_omitir_bloques():
    bloques = dict(cuenta=_CUENTA, desglose=_D, grafico=_G)
    formula = _armar(1000, **bloques)
    respuesta = _parrafos_hasta(1700)
    msg = ai_explain._fit_leaf_message(_DATO, respuesta, formula, _FUENTE, **bloques)
    _chequear_mensaje(msg, "La empresa muestra")
    assert _G in msg and _D in msg
    assert msg.count(ai_explain._NOTA_ACORTADA) == 1
    assert respuesta not in msg


def test_ca73_c_con_restante_menor_a_400_se_omite_solo_el_grafico():
    bloques = dict(cuenta=_CUENTA, desglose=_D, grafico=_G)
    formula = _armar(300, **bloques)
    # sin el gráfico el restante sube a >= 400 (el gráfico mide ~130 + separador)
    assert _restante(formula, cuenta=_CUENTA, desglose=_D, grafico=None) >= 400
    respuesta = (_FRASE * 4).rstrip()
    msg = ai_explain._fit_leaf_message(_DATO, respuesta, formula, _FUENTE, **bloques)
    _chequear_mensaje(msg, respuesta)
    assert _G not in msg and _D in msg


def test_ca73_d_tras_omitir_el_grafico_sigue_corto_se_omite_tambien_el_desglose():
    g_chico = "📊 Chico\nA ████ $1.00"
    d_grande = "🔍 Desglose:\n" + "• Término largo — qué mide con detalle.\n" * 40
    bloques = dict(cuenta=_CUENTA, desglose=d_grande, grafico=g_chico)
    formula = _armar(150, **bloques)
    assert _restante(formula, cuenta=_CUENTA, desglose=d_grande, grafico=None) < 400
    assert _restante(formula, cuenta=_CUENTA, desglose=None, grafico=None) >= 400
    respuesta = _parrafos_hasta(300)
    msg = ai_explain._fit_leaf_message(_DATO, respuesta, formula, _FUENTE, **bloques)
    _chequear_mensaje(msg, respuesta)
    assert g_chico not in msg and d_grande not in msg


def test_ca73_e_tras_omitir_ambos_con_restante_negativo_devuelve_none(caplog):
    caplog.set_level(logging.INFO, logger="investbot")
    g_chico = "📊 Chico\nA ████ $1.00"
    d_grande = "🔍 Desglose:\n" + "• Término largo — qué mide con detalle.\n" * 40
    bloques = dict(cuenta=_CUENTA, desglose=d_grande, grafico=g_chico)
    formula = _armar(-3000, **bloques)
    assert _restante(formula, cuenta=_CUENTA, desglose=None, grafico=None) < 0
    assert ai_explain._fit_leaf_message(_DATO, "Hola.", formula, _FUENTE, **bloques) is None
    assert any("GEMINI_ENTREGA_OMITIDA motivo=presupuesto" in r.getMessage() for r in caplog.records)


def test_ca73_f_con_restante_entre_0_y_400_solo_entra_la_respuesta_que_cabe_completa():
    d_grande = "🔍 Desglose:\n" + "• Término largo — qué mide con detalle.\n" * 40
    g_chico = "📊 Chico\nA ████ $1.00"
    bloques = dict(cuenta=_CUENTA, desglose=d_grande, grafico=g_chico)
    # restante final (sin g ni d) = 200
    sin_g_ni_d = dict(cuenta=_CUENTA, desglose=None, grafico=None)
    n = _restante("f", **sin_g_ni_d) - 200 + 1
    formula = "f" * n
    assert _restante(formula, **sin_g_ni_d) == 200
    assert _restante(formula, **bloques) < 200
    cabe = "Una respuesta que cabe completa."
    msg = ai_explain._fit_leaf_message(_DATO, cabe, formula, _FUENTE, **bloques)
    _chequear_mensaje(msg, cabe)
    assert ai_explain._NOTA_ACORTADA not in msg
    exige_recorte = _parrafos_hasta(300)
    assert ai_explain._fit_leaf_message(_DATO, exige_recorte, formula, _FUENTE, **bloques) is None


def test_ca73_defensa_final_si_el_mensaje_armado_igual_excede_4096_devuelve_none(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="investbot")
    original = ai_explain._build_leaf_message

    def inflado(dato_line, respuesta, *args, **kwargs):
        texto = original(dato_line, respuesta, *args, **kwargs)
        return texto + "x" * 300 if respuesta else texto  # solo la llamada final lleva respuesta

    monkeypatch.setattr(ai_explain, "_build_leaf_message", inflado)
    formula = _armar(600, cuenta=_CUENTA, desglose=None, grafico=None)
    assert ai_explain._fit_leaf_message(_DATO, _parrafos_hasta(590), formula, _FUENTE, cuenta=_CUENTA) is None
    assert any("GEMINI_ENTREGA_OMITIDA motivo=presupuesto" in r.getMessage() for r in caplog.records)


def test_ca73_con_los_maximos_reales_de_cuenta_desglose_y_grafico_entra_en_4096():
    cuenta = "c" * ai_explain._MAX_CUENTA_CHARS
    desglose = "🔍 Desglose:\n" + "d" * (ai_explain._MAX_DESGLOSE_CHARS - 14)
    grafico = "g" * 400
    respuesta = _parrafos_hasta(2500)
    msg = ai_explain._fit_leaf_message(
        _DATO, respuesta, "f" * 200, _FUENTE, cuenta=cuenta, desglose=desglose, grafico=grafico
    )
    assert msg is not None and _len16(msg) <= 4096


# ---------------------------------------------------------------------------
# CA-74 / CS-24(c) / CS-34 : _normalize_layout
# ---------------------------------------------------------------------------


def _oraculo(texto: str, etiquetas) -> str:
    """Réplica independiente del algoritmo de 18.2 (diferencial contra la implementación)."""
    marco = "\U0001F916\U0001F4CC\U0001F9EE\U0001F50D\U0001F4D0\U0001F4CA\U0001F4CB"
    t = texto.replace("\r\n", "\n").replace("\r", "\n").translate(str.maketrans("", "", "*_`" + marco))
    salida = []
    for linea in t.split("\n"):
        s = linea.lstrip(" \t")
        if s.startswith("#"):
            s = s.lstrip("# \t")
            linea = s
        if s[:1] in ("-", "–") and len(s) > 1 and s[1] in " \t":
            linea = "• " + s[1:].lstrip(" \t")
        linea = linea.rstrip(" \t")
        if salida and salida[-1] != "" and linea.startswith(tuple(etiquetas)):
            salida.append("")
        salida.append(linea)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(salida)).strip(" \t\n")


def _verificar_propiedades(entrada: str) -> None:
    f = ai_explain._normalize_layout
    salida = f(entrada)
    assert f(salida) == salida, repr(entrada)  # P1
    assert "**" not in salida and "__" not in salida and "`" not in salida, repr(entrada)  # P2
    assert set(salida) <= set(entrada) | {"•", "\n", " "}, repr(entrada)  # P3
    assert "\r" not in salida and "\n\n\n" not in salida, repr(entrada)  # P4
    for linea in salida.split("\n"):  # P5
        assert re.match(r"[ \t]*(#|[-–][ \t])", linea) is None, repr(entrada)


_ATOMOS_FUZZ = [
    "*", "_", "`", "#", "-", "–", "•", "\t", "\r", "\n", " ", "ab", "x",
    "\U0001F916", "\U0001F4CC", "\U0001F9EE", "\U0001F50D", "\U0001F4D0", "\U0001F4CA", "\U0001F4CB",
    *ai_explain._ALL_LABELS, "​", "@", "](", "http://", "1", "$", ".", "%",
    "\x0b", "\x0c", "\x1c", "\x85", " ", "　", " ",
]

_CASOS_NORMALIZADOR = [
    ("**a**", "a"),
    ("__b__", "b"),
    ("# T", "T"),
    ("* x", "x"),
    ("- y", "• y"),
    ("– z", "• z"),
    ("`c`", "c"),
    ("a\n\n\n\nb", "a\n\nb"),
    ("a\r\nb\rc", "a\nb\nc"),
    ("a\n💡 En simple\nb", "a\n\n💡 En simple\nb"),
    ("💡 En simple\nb\n🔎 Qué dicen los datos\nc", "💡 En simple\nb\n\n🔎 Qué dicen los datos\nc"),
    ("*_*_`_*_*", ""),
    ("# # # # - x", "• x"),
    ("#- #", "• #"),
    ("- \n", "•"),
    ("\xa0- x", "\xa0- x"),  # NBSP al inicio: no es viñeta
    ("  texto con espacios al final   \n", "texto con espacios al final"),
    ("Dato 🤖 📌 🧮 🔍 📐 📊 📋 fin", "Dato" + " " * 8 + "fin"),
    ("Lupa de etiqueta 🔎 queda", "Lupa de etiqueta 🔎 queda"),
]


@pytest.mark.parametrize("entrada,esperada", _CASOS_NORMALIZADOR, ids=range(len(_CASOS_NORMALIZADOR)))
def test_ca74_normalize_layout_tabla_de_casos_con_salida_exacta(entrada, esperada):
    assert ai_explain._normalize_layout(entrada) == esperada
    assert ai_explain._normalize_layout(esperada) == esperada  # idempotente


def test_ca74_propiedades_enumeracion_exhaustiva_de_cadenas_cortas():
    alfabeto = ["*", "_", "`", "#", "-", " ", "\n", "\U0001F4CC", "x", "\t"]
    f = ai_explain._normalize_layout
    n = 0
    for largo in range(0, 6):
        for tupla in product(alfabeto, repeat=largo):
            entrada = "".join(tupla)
            salida = f(entrada)
            assert f(salida) == salida, repr(entrada)
            assert "**" not in salida and "__" not in salida and "`" not in salida, repr(entrada)
            assert set(salida) <= set(entrada) | {"•", "\n", " "}, repr(entrada)
            n += 1
    assert n == sum(10 ** k for k in range(6))


def test_ca74_propiedades_contraejemplos_fijos_y_fuzz_de_20000_con_semilla_fija():
    for fija in ("*_*_`_*_*", "# # # # - x", "#- #", "\xa0- x", "- \n"):
        _verificar_propiedades(fija)
    rng = random.Random(1234)
    for _ in range(20000):
        entrada = "".join(rng.choice(_ATOMOS_FUZZ) for _ in range(rng.randint(0, 14)))
        _verificar_propiedades(entrada)
        assert ai_explain._normalize_layout(entrada) == _oraculo(entrada, ai_explain._ALL_LABELS)


def test_ca74_nunca_agrega_numeros_el_texto_normalizado_solo_pierde_o_conserva_cifras():
    entrada = "**Vale $285.75** y `15.3%` en # 2 - 3\n* 4"
    salida = ai_explain._normalize_layout(entrada)
    assert Counter(ai_rewrite.protected_tokens(salida)) == Counter(ai_rewrite.protected_tokens(entrada))


def test_cs24_c_propiedades_del_normalizador_con_otra_semilla():
    rng = random.Random(20261112)
    for _ in range(20000):
        _verificar_propiedades("".join(rng.choice(_ATOMOS_FUZZ) for _ in range(rng.randint(0, 16))))


def _min_de_7(f, arg):
    mejor = None
    for _ in range(7):
        t0 = time.perf_counter()
        f(arg)
        dt = time.perf_counter() - t0
        mejor = dt if mejor is None else min(mejor, dt)
    return mejor


_ENTRADAS_DEGENERADAS = {
    "saltos": lambda n: "\n" * n,
    "espacio_salto": lambda n: " \n" * (n // 2),
    "espacios": lambda n: " " * n,
    "a_espacios_x": lambda n: "a" + " " * n + "x",
    "a_espacio_tab_x": lambda n: "a" + " \t" * (n // 2) + "x",
    "numerales": lambda n: "# " * (n // 2) + "x",
}


@pytest.mark.parametrize("nombre", list(_ENTRADAS_DEGENERADAS))
def test_cs34_normalizador_lineal_en_menos_de_50_ms_y_escala_lineal(nombre):
    gen = _ENTRADAS_DEGENERADAS[nombre]
    f = ai_explain._normalize_layout
    chico = _min_de_7(f, gen(6000))
    grande = _min_de_7(f, gen(24000))
    assert chico < 0.05
    assert grande < 8 * max(chico, 1e-3)  # lineal ~4x; cuadrático ~16x


def test_cs34_el_cuerpo_del_normalizador_no_usa_s_ni_multilinea_ni_dolar_y_solo_un_regex():
    fuente = inspect.getsource(ai_explain._normalize_layout)
    assert "\\" + "s" not in fuente
    assert "(?" + "m)" not in fuente
    assert "$" not in fuente
    assert fuente.count("re.") == 0  # el único regex es la constante compilada `_TRES_SALTOS_RE`
    assert ai_explain._TRES_SALTOS_RE.pattern == "\\n{3,}"
    # control positivo del detector
    assert "\\" + "s" in "re.compile(r'^\\s*x')"


# ---------------------------------------------------------------------------
# CS-36 : emojis del marco
# ---------------------------------------------------------------------------


def test_cs36_se_quitan_los_emojis_del_marco_y_se_conserva_la_lupa_de_la_etiqueta():
    texto = "📐 Fórmula: algo\n\U0001F50D desglose \U0001F50E etiqueta\n🤖 📌 🧮 📊 📋 listo"
    salida = ai_explain._normalize_layout(texto)
    assert "\U0001F4D0" not in salida  # 📐
    assert "\U0001F50D" not in salida  # lupa del Desglose del marco: se borra
    assert "\U0001F50E" in salida  # lupa de la etiqueta "Qué dicen los datos": se conserva
    for emoji in "🤖📌🧮📊📋":
        assert emoji not in salida
    for etiqueta in ai_explain._ALL_LABELS:
        assert ai_explain._normalize_layout(etiqueta) == etiqueta  # 💡 🔎 🧭 intactos
    assert salida.startswith("Fórmula: algo")


# ---------------------------------------------------------------------------
# CA-76 / CS-35 : prompts y SECTION_LABELS
# ---------------------------------------------------------------------------

_PROMPTS = {
    "SYSTEM_PROMPT_EXPLAIN": "narrativa",
    "SYSTEM_PROMPT_PASO_A_PASO": "paso_a_paso",
}


@pytest.mark.parametrize("nombre", list(_PROMPTS))
def test_ca76_prompts_reglas_1_a_7_contenido_y_prohibiciones(nombre):
    prompt = getattr(ai_explain, nombre)
    numeros = [int(m.group(1)) for m in re.finditer(r"(?m)^(\d+)\. ", prompt)]
    assert numeros == [1, 2, 3, 4, 5, 6, 7]
    for requerido in ("modelo", "español rioplatense", "Markdown", "MAYÚSCULAS"):
        assert requerido in prompt
    for etiqueta in ai_explain.SECTION_LABELS[_PROMPTS[nombre]]:
        assert etiqueta in prompt
    for prohibido in (
        "2 a 4 oraciones", "cortas", "separador de miles", "punto decimal",
        '{"respuesta"', "###" + "RESPUESTA" + "###", "portugués",
    ):
        assert prohibido not in prompt
    # (iii) ninguna otra línea del prompt sustituido empieza con dígito + ". "
    lineas = [ln for ln in prompt.split("\n") if re.match(r"\d+\. ", ln)]
    assert len(lineas) == 7
    # sin llaves de plantilla sin sustituir
    assert "{S" not in prompt


def test_ca76_paso_a_paso_conserva_cuenta_y_ya_esta_calculada():
    assert "cuenta" in ai_explain.SYSTEM_PROMPT_PASO_A_PASO
    assert "YA ESTÁ CALCULADA" in ai_explain.SYSTEM_PROMPT_PASO_A_PASO


def test_ca76_section_labels_son_inocuas_ante_los_guards_y_estan_en_el_prompt_como_linea():
    assert set(ai_explain.SECTION_LABELS) == {"narrativa", "paso_a_paso"}
    assert len(ai_explain._ALL_LABELS) == 5
    for clave, trio in ai_explain.SECTION_LABELS.items():
        assert len(trio) == 3
        prompt = ai_explain.SYSTEM_PROMPT_EXPLAIN if clave == "narrativa" else ai_explain.SYSTEM_PROMPT_PASO_A_PASO
        lineas = [ln.strip() for ln in prompt.split("\n")]
        for etiqueta in trio:
            assert ai_rewrite.protected_tokens(etiqueta) == []
            assert not gemini_client.violates_output_filter(etiqueta)
            assert not re.search(r"\d", etiqueta) and "✅" not in etiqueta and "❌" not in etiqueta
            assert "‍" not in etiqueta  # sin ZWJ
            assert etiqueta in lineas  # aparece como línea completa
            assert not re.match(r"\d+\. ", etiqueta)


async def test_ca76_system_instruction_es_la_constante_del_modulo():
    capturas = []

    def handler(request):
        capturas.append(request)
        return httpx.Response(200, json=gemini_response("Corto."))

    for prompt in (ai_explain.SYSTEM_PROMPT_EXPLAIN, ai_explain.SYSTEM_PROMPT_PASO_A_PASO):
        await ai_explain._fetch_explanation(
            **_fetch_kwargs(http_client=_client_with_handler(handler)), system_prompt=prompt
        )
        assert system_text(request_body(capturas[-1])) == prompt


_PALABRAS_PROHIBIDAS_EN_PROMPT = (
    "rechaz", "guard", "filtro", "regex", "token", "bidi", "http", "límite de",
    "tasa", "rate", "key", "gemini",
)


@pytest.mark.parametrize("nombre", list(_PROMPTS))
@pytest.mark.parametrize("palabra", _PALABRAS_PROHIBIDAS_EN_PROMPT)
def test_cs35_los_prompts_no_filtran_el_mecanismo_de_defensa(nombre, palabra):
    assert palabra not in getattr(ai_explain, nombre).lower()


@pytest.mark.parametrize("nombre", list(_PROMPTS))
def test_cs35_los_prompts_incluyen_la_clausula_de_cantidades_con_letras_y_la_de_emojis(nombre):
    prompt = " ".join(getattr(ai_explain, nombre).split())
    assert (
        'No escribas cantidades con letras ("el doble", "la mitad", "diez por ciento"): '
        "comparar con palabras vagas sí, expresar una cantidad que no está en el JSON no."
    ) in prompt
    assert "Tampoco uses emojis en el texto, salvo los encabezados." in prompt


# ---------------------------------------------------------------------------
# CA-75 / CS-24(a) : el MISMO string en los cuatro puntos
# ---------------------------------------------------------------------------


async def test_ca75_los_guards_y_la_entrega_ven_el_mismo_string_normalizado(monkeypatch):
    vistos = {"tokens": [], "escala": [], "filtro": [], "fit": []}
    originales = (
        ai_explain._no_new_protected_tokens, ai_explain._escalas_consistentes,
        ai_explain.violates_output_filter, ai_explain._fit_leaf_message,
    )

    def tokens_spy(datos_tokens, respuesta):
        vistos["tokens"].append(respuesta)
        return originales[0](datos_tokens, respuesta)

    def escala_spy(payload, respuesta):
        vistos["escala"].append(respuesta)
        return originales[1](payload, respuesta)

    def filtro_spy(texto):
        vistos["filtro"].append(texto)
        return originales[2](texto)

    def fit_spy(dato_line, respuesta, *args, **kwargs):
        vistos["fit"].append(respuesta)
        return originales[3](dato_line, respuesta, *args, **kwargs)

    monkeypatch.setattr(ai_explain, "_no_new_protected_tokens", tokens_spy)
    monkeypatch.setattr(ai_explain, "_escalas_consistentes", escala_spy)
    monkeypatch.setattr(ai_explain, "violates_output_filter", filtro_spy)
    monkeypatch.setattr(ai_explain, "_fit_leaf_message", fit_spy)

    cruda = "💡 En simple\n**Texto** con markdown\n- viñeta uno\n\n\n\n🧭 Qué significa el resultado\nCierre.  "
    bot = await _correr_boton("vf", cruda)
    normalizada = ai_explain._normalize_layout(cruda)
    assert normalizada != cruda
    assert vistos["tokens"] == vistos["escala"] == vistos["filtro"] == vistos["fit"] == [normalizada]
    assert normalizada in _texto_final(bot)
    assert "**" not in _texto_final(bot).split("Qué significa el resultado")[0]


# ---------------------------------------------------------------------------
# CA-77 / CS-32 : payload presentado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
def test_ca77_payload_presentado(code):
    crudo, presentado = _crudo_y_presentado(code)
    pares = list(zip(_hojas(crudo), _hojas(presentado)))
    # la forma se conserva: mismas hojas salvo los campos derivados de vf/ver
    claves_derivadas = {"posicion_vs_precio", "diferencia_vs_precio_abs"}
    hojas_pres = [h for h in _hojas(presentado) if h[0] not in claves_derivadas]
    assert len(hojas_pres) == len(list(_hojas(crudo)))
    for (clave, valor_crudo), (_, valor_pres) in zip(_hojas(crudo), hojas_pres):
        if type(valor_crudo) in (int, float) and math.isfinite(valor_crudo) and clave in ai_explain._PAYLOAD_KIND:
            assert isinstance(valor_pres, str), (code, clave)  # (a)
        elif type(valor_crudo) is float:
            assert abs(valor_pres - valor_crudo) <= 5e-5 + 1e-12  # (b) round(x, 4)
    assert pares  # control: hubo hojas que comparar
    # (b) ninguna notación científica
    assert re.search(r"[eE][+-]?\d", json.dumps(presentado, ensure_ascii=False)) is None
    # (c) monotonía: la unión contiene exactamente lo que HEAD aceptaba
    head = {
        ai_explain._normalize_numeric_token(t)
        for t in ai_rewrite.protected_tokens(json.dumps(crudo, ensure_ascii=False, default=str))
    }
    assert ai_explain._compute_datos_tokens(crudo, presentado) >= head
    assert ai_explain._compute_datos_tokens(crudo) == head  # presentado=None equivale a HEAD


def test_ca77_hojas_extremas_sin_notacion_cientifica_ni_floats_largos():
    crudo = {
        "no_clasificado": 496.33333333333337, "chico": 1.5e-05, "enorme": 1e300, "inf": float("inf"),
        "nan": float("nan"), "grande": 394328000000.0, "entero": 7, "ent_enorme": 10 ** 400,
        "lista": [1.123456789, {"precio_actual": 1e20, "roe": 0.153}],
    }
    pres = ai_explain._present_payload(crudo)
    assert pres["no_clasificado"] == 496.3333 and pres["chico"] == 0.0
    assert pres["enorme"] is None and pres["inf"] is None and pres["nan"] is None  # >= 1e15 o no finito
    assert pres["ent_enorme"] is None
    assert pres["grande"] == 394328000000.0  # sin clasificar: numérico redondeado
    assert pres["entero"] == 7 and type(pres["entero"]) is int
    assert pres["lista"][0] == 1.1235
    assert pres["lista"][1] == {"precio_actual": None, "roe": "15.3%"}
    assert re.search(r"[eE][+-]?\d", json.dumps(pres)) is None


def test_ca77_present_payload_no_muta_la_entrada():
    crudo = {"precio_actual": 430.0, "anidado": {"roe": 0.28}, "lista": [1.5]}
    copia = json.loads(json.dumps(crudo))
    ai_explain._present_payload(crudo)
    assert crudo == copia


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
def test_cs32_fidelidad_presentado(code):
    crudo, presentado = _crudo_y_presentado(code)
    hojas_pres = [h for h in _hojas(presentado) if h[0] not in {"posicion_vs_precio", "diferencia_vs_precio_abs"}]
    for (clave, crudo_v), (_, pres_v) in zip(_hojas(crudo), hojas_pres):
        if type(crudo_v) not in (int, float):
            assert type(pres_v) is type(crudo_v) and pres_v == crudo_v  # bool, str, None no se tocan
            continue
        unidad = ai_explain._PAYLOAD_KIND.get(clave)
        if unidad == "money":
            assert pres_v == ai_explain._money_compact(crudo_v)
            if abs(crudo_v) < 1e6:
                assert pres_v == ai_explain._fmt_money_or_nd(crudo_v)
            else:
                m = re.fullmatch(r"\$(-?[\d,]+\.\d) (millones|mil millones)", pres_v)
                assert m, pres_v
                escala = 1e6 if m.group(2) == "millones" else 1e9
                assert abs(float(m.group(1).replace(",", "")) * escala - crudo_v) <= 0.05 * escala * (1 + 1e-9)
        elif unidad == "pct":
            assert pres_v == ai_explain._pct1(crudo_v)
        elif unidad == "ratio":
            assert pres_v == f"{crudo_v:.2f}"
        else:
            assert type(pres_v) in (int, float)  # sin clasificar: sigue numérica


def test_cs32_la_unidad_presentada_coincide_con_lo_que_ve_el_usuario_en_dato_cuenta_y_desglose():
    # Independiente de `_PAYLOAD_KIND`: la hoja presentada (str) debe aparecer tal cual en el
    # texto determinístico que ya ve quien lee, cuando ese texto muestra el valor.
    verificadas = 0
    for code in _PRESENTABLE_CODES:
        ctx = _contexto_para(code)
        crudo, presentado = _crudo_y_presentado(code)
        visible = "\n".join(
            x for x in (
                ai_explain._build_dato_line(ctx.kind, code, crudo),
                crudo.get("cuenta"),
                ai_explain._build_desglose_block(ctx.kind, code, crudo, context=ctx),
            ) if x
        )
        for (clave, antes), (_, despues) in zip(_hojas(crudo), _hojas(presentado)):
            if type(antes) in (int, float) and isinstance(despues, str) and "mil" not in despues and "millones" not in despues:
                if despues.lstrip("$-") in visible.replace(",", "") or despues in visible:
                    verificadas += 1
                else:
                    # el valor puede no mostrarse en estos textos: solo se exige si su unidad es pct o ratio
                    # y la clave se muestra en el Dato (no hay ningún caso hoy: ver el conteo de abajo).
                    assert clave not in ("margen_bruto", "roe", "roic", "earnings_yield", "beta", "per", "ps",
                                         "ratio_liquidez", "per_propio"), (code, clave, despues)
    assert verificadas >= 40


_PAYLOAD_KIND_ESPERADO = {
    "a": "ratio",
    "apalancamiento_t": "ratio",
    "apalancamiento_t1": "ratio",
    "b": "ratio",
    "beta": "ratio",
    "beta_umbral_alto": "ratio",
    "beta_umbral_bajo": "ratio",
    "c": "ratio",
    "capital_invertido": "money",
    "cash": "money",
    "cfo_t": "money",
    "conservador": "money",
    "cost_of_revenue": "money",
    "current_assets": "money",
    "current_liabilities": "money",
    "d": "ratio",
    "dcf_equity_value": "money",
    "dcf_fcf_base": "money",
    "dcf_g_fcf": "pct",
    "dcf_valor_presente_flujos": "money",
    "dcf_valor_terminal_descontado": "money",
    "dcf_wacc": "pct",
    "e": "ratio",
    "earnings_yield": "pct",
    "ebit": "money",
    "eps_ttm": "money",
    "ev": "money",
    "g_aplicado": "pct",
    "gross_margin": "pct",
    "gross_margin_umbral_alto": "pct",
    "gross_margin_umbral_bajo": "pct",
    "liquidez_t": "ratio",
    "liquidez_t1": "ratio",
    "margen_bruto": "pct",
    "margen_t": "pct",
    "margen_t1": "pct",
    "market_cap": "money",
    "net_income_antiguo": "money",
    "net_income_reciente": "money",
    "net_income_t": "money",
    "optimista": "money",
    "per": "ratio",
    "per_promedio_peers": "ratio",
    "per_propio": "ratio",
    "pesimista": "money",
    "piotroski_ratio": "pct",
    "piotroski_ratio_umbral_alto": "pct",
    "piotroski_ratio_umbral_bajo": "pct",
    "ppe_net": "money",
    "precio_actual": "money",
    "price_avg_200": "money",
    "price_avg_50": "money",
    "ps": "ratio",
    "ratio_liquidez": "ratio",
    "revenue": "money",
    "revenue_antiguo": "money",
    "revenue_reciente": "money",
    "roa_t": "ratio",
    "roa_t1": "ratio",
    "roe": "pct",
    "roe_umbral_alto": "pct",
    "roe_umbral_bajo": "pct",
    "roic": "pct",
    "rotacion_t": "ratio",
    "rotacion_t1": "ratio",
    "shares_t": "ratio",
    "shares_t1": "ratio",
    "total_debt": "money",
    "umbral_alto": "pct",
    "umbral_bajo": "pct",
    "valor_justo_dcf": "money",
    "valor_justo_graham": "money",
    "valor_justo_multiplos": "money",
    "valor_justo_total": "money",
    "y_value": "pct",
    "year_high": "money",
    "year_low": "money",
    "z": "ratio",
}


def test_cs32_la_tabla_de_unidades_esta_pineada_con_un_literal():
    assert len(_PAYLOAD_KIND_ESPERADO) == 78
    assert ai_explain._PAYLOAD_KIND == _PAYLOAD_KIND_ESPERADO


def test_cs32_no_hay_colision_de_unidad_entre_preguntas_y_el_union_coincide():
    vistas: dict[str, str] = {}
    for code, kinds in ai_explain._PAYLOAD_KIND_POR_PREGUNTA.items():
        for clave, unidad in kinds.items():
            assert unidad in ("money", "pct", "ratio")
            assert vistas.setdefault(clave, unidad) == unidad, (code, clave)
    assert vistas == ai_explain._PAYLOAD_KIND
    # el detector sí ve una colisión (control positivo)
    with pytest.raises(RuntimeError):
        ai_explain._unir_unidades({"a": {"k": "money"}, "b": {"k": "pct"}})


def test_cs32_hojas_no_numericas_y_casos_de_borde_de_money_compact():
    f = ai_explain._present_payload
    assert f({"precio_actual": True, "roe": "x", "beta": None}) == {"precio_actual": True, "roe": "x", "beta": None}
    assert f({"precio_actual": float("nan"), "roe": float("inf"), "beta": -math.inf}) == {
        "precio_actual": None, "roe": None, "beta": None,
    }
    assert f({"precio_actual": 1e15, "ebit": 999_999_999_999_999.0})["precio_actual"] is None
    mc = ai_explain._money_compact
    assert mc(394328000000.0) == "$394.3 mil millones"
    assert mc(999_960_000) == "$1.0 mil millones"  # promoción de unidad
    assert mc(999_940_000) == "$999.9 millones"
    assert mc(1.5e12) == "$1,500.0 mil millones"
    assert mc(2_500_000) == "$2.5 millones"
    assert mc(-5.0) == "$-5.00" == ai_explain._money(-5.0)
    assert mc(-3_200_000_000) == "$-3.2 mil millones"
    assert mc(999_999.99) == "$999,999.99"
    assert "billón" not in mc(1e12)
    # el token del monto compacto normaliza como cifra y la escala la verifica CS-31
    assert ai_explain._normalize_numeric_token("$394.3") == "394.3"


# ---------------------------------------------------------------------------
# CA-78 / CS-33 : campos derivados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor,precio,esperado",
    [
        (496.00, 430.00, ("por encima", "15.3%")),
        (400.00, 430.00, ("por debajo", "7.0%")),
        (430.01, 430.00, ("igual", "0.0%")),
        (430.00, 430.00, ("igual", "0.0%")),
        (496, 430, ("por encima", "15.3%")),
    ],
)
def test_ca78_tabla_de_verdad_de_posicion_vs_precio(valor, precio, esperado):
    assert ai_explain._derivar_posicion_vs_precio(valor, precio) == {
        "posicion_vs_precio": esperado[0], "diferencia_vs_precio_abs": esperado[1],
    }


@pytest.mark.parametrize(
    "valor,precio",
    [
        (496.0, None), (496.0, 0), (496.0, 0.0), (496.0, -430.0), (None, 430.0), ("x", 430.0),
        (496.0, True), (True, 430.0), (496.0, float("nan")), (float("inf"), 430.0),
        (496.0, float("inf")), (496.0, "430"), (None, None), (1e300, 1e-300),
    ],
)
def test_ca78_cs33_datos_invalidos_no_emiten_ningun_campo(valor, precio):
    assert ai_explain._derivar_posicion_vs_precio(valor, precio) == {}


def test_ca78_cs33_enteros_gigantes_no_lanzan_overflow():
    assert ai_explain._derivar_posicion_vs_precio(10**400, 430.0) == {}
    assert ai_explain._derivar_posicion_vs_precio(496.0, 10**400) == {}
    assert ai_explain._derivar_posicion_vs_precio(-(10**400), 430.0) == {}
    # ambos < 1e15 pero el cociente desborda el límite de hojas numéricas
    assert ai_explain._derivar_posicion_vs_precio(1e14, 1e-5) == {}


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
def test_ca78_el_campo_derivado_existe_solo_en_vf_y_ver(code):
    _, presentado = _crudo_y_presentado(code)
    tiene = {"posicion_vs_precio", "diferencia_vs_precio_abs"} <= set(presentado)
    assert tiene == (code in ("vf", "ver"))
    assert ("diferencia_vs_precio" not in presentado)  # la versión con signo no existe


def test_cs33_el_handler_calcula_la_posicion_sobre_el_valor_ya_redondeado_a_un_decimal():
    # 430.01 vs 430.00 -> +0.0023%: redondeado a 0.0% -> "igual"
    assert ai_explain._derivar_posicion_vs_precio(430.01, 430.0)["posicion_vs_precio"] == "igual"
    # 430.3 vs 430.0 -> +0.07%: redondeado 0.1% -> "por encima"
    assert ai_explain._derivar_posicion_vs_precio(430.3, 430.0) == {
        "posicion_vs_precio": "por encima", "diferencia_vs_precio_abs": "0.1%",
    }
    assert ai_explain._derivar_posicion_vs_precio(429.7, 430.0)["posicion_vs_precio"] == "por debajo"


# ---------------------------------------------------------------------------
# CA-79 : regresión de formato contra el guard (sin tocarlo)
# ---------------------------------------------------------------------------


async def _fetch(texto, crudo, presentado):
    tokens = ai_explain._compute_datos_tokens(crudo, presentado)
    client = _client_with_handler(_ok_handler(texto))
    return await ai_explain._fetch_explanation(
        **_fetch_kwargs(http_client=client, datos=presentado, tokens=tokens)
    )


@pytest.mark.parametrize(
    "texto,acepta",
    [
        ("El monto es $1,234.56 hoy.", True),
        ("El monto es 1234.56 hoy.", True),
        ("El monto es $1.234,56 hoy.", False),  # es-AR/es-CO: limitación documentada
        ("El monto es $1,234.57 hoy.", False),
        ("El monto es 0,125 hoy.", False),
        ("Hay 18 casos.", False),
    ],
)
async def test_ca79_formato_de_montos_aceptados_y_rechazados(texto, acepta):
    crudo, presentado = {"monto": 1234.56}, {"monto": "$1,234.56"}
    if acepta:
        assert await _fetch(texto, crudo, presentado) == texto
    else:
        with pytest.raises(ai_explain._ExplainUnavailable):
            await _fetch(texto, crudo, presentado)


async def test_ca79_g_15_3_sin_porcentaje_se_rechaza_con_payload_15_3_porciento():
    crudo, presentado = {"roe": 0.153}, {"roe": "15.3%"}
    tokens = ai_explain._compute_datos_tokens(crudo, presentado)
    assert "15.3" not in tokens  # el set NO contiene el número sin símbolo
    assert "15.3%" in tokens
    assert await _fetch("El retorno es 15.3% hoy.", crudo, presentado) == "El retorno es 15.3% hoy."
    with pytest.raises(ai_explain._ExplainUnavailable):
        await _fetch("El retorno es 15.3 hoy.", crudo, presentado)


async def test_ca79_f_monto_grande_presentado_en_mil_millones_se_acepta():
    crudo, presentado = {"market_cap": 394328000000.0}, {"market_cap": "$394.3 mil millones"}
    texto = "La capitalización es $394.3 mil millones."
    assert await _fetch(texto, crudo, presentado) == texto


# ---------------------------------------------------------------------------
# CA-81 : Dato/Cuenta/Desglose reciben el payload CRUDO
# ---------------------------------------------------------------------------


async def test_ca81_dato_cuenta_y_desglose_reciben_el_payload_crudo_y_el_modelo_el_presentado(monkeypatch):
    vistos = {}
    o_dato, o_cuenta, o_desglose, o_fetch = (
        ai_explain._build_dato_line, ai_explain._build_cuenta_line,
        ai_explain._build_desglose_block, ai_explain._fetch_explanation,
    )

    def dato_spy(kind, code, datos):
        vistos["dato"] = datos["precio_actual"]
        return o_dato(kind, code, datos)

    def cuenta_spy(kind, code, datos):
        vistos["cuenta"] = datos["precio_actual"]
        return o_cuenta(kind, code, datos)

    def desglose_spy(kind, code, datos, **kwargs):
        vistos["desglose"] = datos["precio_actual"]
        return o_desglose(kind, code, datos, **kwargs)

    async def fetch_spy(**kwargs):
        vistos["fetch"] = kwargs["datos_del_contexto"]["precio_actual"]
        vistos["fetch_extra"] = kwargs["datos_del_contexto"].get("posicion_vs_precio")
        vistos["tokens"] = kwargs["datos_tokens"]
        return await o_fetch(**kwargs)

    monkeypatch.setattr(ai_explain, "_build_dato_line", dato_spy)
    monkeypatch.setattr(ai_explain, "_build_cuenta_line", cuenta_spy)
    monkeypatch.setattr(ai_explain, "_build_desglose_block", desglose_spy)
    monkeypatch.setattr(ai_explain, "_fetch_explanation", fetch_spy)
    bot = await _correr_boton("vf", "Una explicación breve.")
    assert vistos["dato"] == vistos["cuenta"] == vistos["desglose"] == 550.0
    assert vistos["fetch"] == "$550.00" and vistos["fetch_extra"] == "por debajo"
    assert {"550.0", "550.00"} <= vistos["tokens"]  # unión de crudo y presentado
    assert "Valor Justo Total (Conservador): $496.00 — precio actual: $550.00" in _texto_final(bot)


# ---------------------------------------------------------------------------
# CA-82 : rewrite sin cambios
# ---------------------------------------------------------------------------


def test_ca82_el_system_prompt_del_rewrite_no_cambio():
    digest = hashlib.sha256(ai_rewrite.SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert digest == "dd84497126f760c0065490d06ea380efc049e2deb30f60447e1a0eada16546b9"


# ---------------------------------------------------------------------------
# CA-83 : rechazos nuevos (largo, escala, vacio), 1 registro cada uno
# ---------------------------------------------------------------------------


def _registros_guard(caplog):
    return [r for r in caplog.records if "GEMINI_GUARD_REJECT" in r.getMessage()]


async def test_ca83_largo_rechaza_con_un_solo_registro_solo_de_contadores(caplog):
    cruda = "CANARIO-DE-CONTENIDO " + "a" * 6000
    assert len(cruda) > 6000
    client = _client_with_handler(_ok_handler(cruda))
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client))
    registros = _registros_guard(caplog)
    assert len(registros) == 1
    assert re.fullmatch(r"GEMINI_GUARD_REJECT feature=explain guard=largo chars=\d+", registros[0].getMessage())
    assert "CANARIO" not in registros[0].getMessage()


async def test_ca83_escala_rechaza_con_un_solo_registro_solo_de_contadores(caplog):
    crudo, presentado = {"market_cap": 394328000000.0}, {"market_cap": "$394.3 mil millones"}
    with pytest.raises(ai_explain._ExplainUnavailable):
        await _fetch("Canario capitalización $394.3 millones.", crudo, presentado)
    registros = _registros_guard(caplog)
    assert len(registros) == 1
    assert re.fullmatch(r"GEMINI_GUARD_REJECT feature=explain guard=escala chars=\d+", registros[0].getMessage())


async def test_ca83_vacio_tras_normalizar_rechaza_con_un_solo_registro(caplog):
    client = _client_with_handler(_ok_handler("** __ `` # "))
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client))
    registros = _registros_guard(caplog)
    assert len(registros) == 1
    assert re.fullmatch(r"GEMINI_GUARD_REJECT feature=explain guard=vacio chars=\d+", registros[0].getMessage())


@pytest.mark.parametrize("cruda", ["a" * 6001, "***", "El monto es $5.00 millones."])
async def test_ca83_el_handler_responde_unavailable_y_no_otro_mensaje(cruda):
    ctx = _texto_libre_context(precio_actual=5.0)
    bot = await _correr_boton("vf", cruda, ctx=ctx)
    assert _texto_final(bot) == ai_explain.EXPLAIN_UNAVAILABLE_MSG
    bot.send_message.assert_awaited_once()  # solo el "Pensando…"


async def test_ca83_el_log_de_rechazo_por_numeros_y_salida_conserva_su_formato(caplog):
    client = _client_with_handler(_ok_handler("El dato es 999.0%, inventado."))
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client, tokens={"500.0"}))
    assert "guard=numeros respuesta=" in _registros_guard(caplog)[0].getMessage()


# ---------------------------------------------------------------------------
# CA-84 : sin parse_mode en ningún envío del flujo
# ---------------------------------------------------------------------------


def _sin_parse_mode(bot):
    for metodo in (bot.send_message, bot.edit_message_text):
        for llamada in metodo.call_args_list:
            assert "parse_mode" not in llamada.kwargs


async def test_ca84_ninguna_llamada_del_flujo_lleva_parse_mode():
    ok = _parrafos_hasta(900)
    recorte = _parrafos_hasta(3000)
    for texto in (ok, recorte, "a" * 6001):
        bot = await _correr_boton("vf", texto)
        _sin_parse_mode(bot)
        assert bot.edit_message_text.await_count == 1
    bot = await _correr_boton("ren", ok, paso_a_paso=False)
    _sin_parse_mode(bot)


# ---------------------------------------------------------------------------
# CA-85 : goldens (vf paso a paso y ren)
# ---------------------------------------------------------------------------

_RESPUESTA_VF = """💡 En simple
Este análisis usa el Valor Justo Total, que promedia tres formas distintas de estimar cuánto debería valer la acción: Múltiplos, Graham y flujo de caja descontado. La idea es tener un precio de referencia que no dependa de un solo método.
🔎 Qué es cada pieza
• Múltiplos ($500.00): compara a la empresa con otras parecidas del mismo sector.
• Graham ($480.00): una fórmula clásica que parte de lo que la empresa gana por acción y de cuánto se espera que crezca.
• Flujo de caja descontado ($510.00): suma el dinero que la empresa podría generar a futuro, traído a valor de hoy.
• Valor Justo Total ($496.00): el promedio de los tres métodos.
• Precio actual ($550.00): lo que cuesta hoy la acción en el mercado.
🧭 Qué significa el resultado
El valor justo estimado queda 9.8% por debajo del precio actual. Dicho simple: según estos tres métodos, la acción hoy se negocia por encima de lo que estaría valiendo. Es una estimación que depende de supuestos, no una predicción ni una recomendación."""

_GOLDEN_VF = """🤖 Con ayuda de Gemini

📌 Dato: Valor Justo Total (Conservador): $496.00 — precio actual: $550.00

🧮 Cuenta: ($500.00 + $480.00 + $510.00) / 3 = $496.00

🔍 Desglose:
• Múltiplos ($500.00) — cuánto debería valer la acción si cotizara al mismo múltiplo de ganancias (PER) que empresas parecidas del mismo sector.
  Cuenta: $8.20 × 24.00 = $500.00
• Graham (EPS) ($480.00) — cuánto debería valer la acción según una fórmula clásica que combina ganancias por acción y crecimiento histórico.
  Cuenta: EPS $8.20 → se proyecta con 9.4% de crecimiento (techo 15%) → se multiplica por 8.5+2×crecimiento y se ajusta por la tasa del bono a 10 años (4.2%, a mayor tasa, menor valor) = $480.00.
• DCF (Flujo de Caja Descontado) ($510.00) — cuánto vale la empresa hoy si se suma todo el efectivo que se espera que genere en el futuro, traído a valor de hoy.
  Cuenta: FCF hoy $109.00 → crece 8.3%/año durante 5 años → se descuentan esos flujos futuros a valor de hoy al 9.1% (costo de capital) → se suma el valor de seguir generando caja después del año 5 = $510.00 por acción.

💡 En simple
Este análisis usa el Valor Justo Total, que promedia tres formas distintas de estimar cuánto debería valer la acción: Múltiplos, Graham y flujo de caja descontado. La idea es tener un precio de referencia que no dependa de un solo método.

🔎 Qué es cada pieza
• Múltiplos ($500.00): compara a la empresa con otras parecidas del mismo sector.
• Graham ($480.00): una fórmula clásica que parte de lo que la empresa gana por acción y de cuánto se espera que crezca.
• Flujo de caja descontado ($510.00): suma el dinero que la empresa podría generar a futuro, traído a valor de hoy.
• Valor Justo Total ($496.00): el promedio de los tres métodos.
• Precio actual ($550.00): lo que cuesta hoy la acción en el mercado.

🧭 Qué significa el resultado
El valor justo estimado queda 9.8% por debajo del precio actual. Dicho simple: según estos tres métodos, la acción hoy se negocia por encima de lo que estaría valiendo. Es una estimación que depende de supuestos, no una predicción ni una recomendación.

📐 Fórmula: Promedio simple de los modelos calculables entre Múltiplos, Graham y DCF, para el escenario elegido
📊 Fuente del dato: Valor Justo Total = promedio simple de los modelos calculables entre Múltiplos, Graham y DCF (valuation.py, compute_valuation_scenarios).

"""

_RESPUESTA_REN = """💡 En simple
El retorno sobre el patrimonio mide cuánta ganancia genera la empresa con el dinero que aportan sus accionistas.

🔎 Qué dicen los datos
El retorno sobre el patrimonio de la empresa es 28.0%. Por cada dólar que ponen los accionistas, el negocio produce una ganancia del 28.0%.

🧭 Cómo leerlo
Un valor alto suele indicar que la empresa aprovecha bien el capital que tiene. Conviene mirarlo junto con el nivel de deuda, porque endeudarse también puede inflar este número. Es un dato para entender el negocio, no una señal de compra o de venta."""

_GOLDEN_REN = """🤖 Con ayuda de Gemini

📌 Dato: ROE: 28.0%

""" + _RESPUESTA_REN + """

📐 Fórmula: ROE = Ganancia Neta / Patrimonio de los Accionistas · Deuda/Patrimonio = Deuda Total / Patrimonio · Deuda Neta/EBITDA · Dividend Yield · Payout Ratio
📊 Fuente del dato: Los 5 campos vienen ya precalculados de /key-metrics de FMP — el bot no los recalcula, los muestra tal cual.

"""


async def test_ca85_golden_vf_paso_a_paso_sin_grafico(monkeypatch):
    # F2 agrega el Gráfico a `vf`; el golden de F1 fija el mensaje SIN él (el
    # golden CON Gráfico está en `tests/test_f2_graficos.py`).
    monkeypatch.setitem(ai_explain._GRAFICO_POR_PREGUNTA, "vf", lambda datos: None)
    bot = await _correr_boton("vf", _RESPUESTA_VF)
    assert _texto_final(bot) == _GOLDEN_VF + DISCLAIMER_NO_ASESORAMIENTO


async def test_ca85_golden_ren_narrativa():
    bot = await _correr_boton("ren", _RESPUESTA_REN, paso_a_paso=False)
    assert _texto_final(bot) == _GOLDEN_REN + DISCLAIMER_NO_ASESORAMIENTO


# ---------------------------------------------------------------------------
# CS-23 : longitud y entrega
# ---------------------------------------------------------------------------


async def test_cs23_a_tope_crudo_6001_no_normaliza_ni_calcula_tokens_y_6000_si(monkeypatch):
    llamadas = {"norm": 0, "tokens": 0}
    o_norm, o_tokens = ai_explain._normalize_layout, ai_rewrite.protected_tokens

    def norm_spy(t):
        llamadas["norm"] += 1
        return o_norm(t)

    def tokens_spy(t):
        llamadas["tokens"] += 1
        return o_tokens(t)

    monkeypatch.setattr(ai_explain, "_normalize_layout", norm_spy)
    monkeypatch.setattr(ai_rewrite, "protected_tokens", tokens_spy)
    exacto = ("x" * 5999) + "."
    assert len(exacto) == 6000
    assert await ai_explain._fetch_explanation(
        **_fetch_kwargs(http_client=_client_with_handler(_ok_handler(exacto)))
    ) == exacto
    assert llamadas["norm"] == 1 and llamadas["tokens"] >= 1
    llamadas.update(norm=0, tokens=0)
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(
            **_fetch_kwargs(http_client=_client_with_handler(_ok_handler(exacto + ".")))
        )
    assert llamadas == {"norm": 0, "tokens": 0}


@pytest.mark.parametrize("largo", [0, 1, 399, 400, 1799, 1800, 1801, 3000, 6000])
def test_cs23_b_el_mensaje_nunca_supera_4096_utf16_con_bloques_maximos(largo):
    bloques = dict(
        cuenta="c" * ai_explain._MAX_CUENTA_CHARS,
        desglose="🔍 Desglose:\n" + "d" * (ai_explain._MAX_DESGLOSE_CHARS - 14),
        grafico="g" * 400,
    )
    for emoji in ("", "😀"):
        respuesta = ((emoji * 20 + " Una oración. ") * 600)[:largo].rstrip()
        msg = ai_explain._fit_leaf_message(_DATO, respuesta, "f" * 427, _FUENTE, **bloques)
        if msg is not None:
            assert _len16(msg) <= 4096
            assert _DATO in msg and DISCLAIMER_NO_ASESORAMIENTO in msg and "🧮 Cuenta" in msg
            cuerpo = msg.split("\n\n")
            assert respuesta in msg or ai_explain._NOTA_ACORTADA in msg


def test_cs23_b_la_cuenta_en_utf16_discrimina_emojis_fuera_del_bmp():
    # restante = 1000: 600 emojis ocupan 1200 unidades UTF-16 aunque len() == 601.
    bloques = dict(cuenta=_CUENTA, desglose=None, grafico=None)
    formula = _armar(1000, **bloques)
    respuesta = "😀" * 600 + "."
    assert len(respuesta) == 601 and _len16(respuesta) == 1201
    msg = ai_explain._fit_leaf_message(_DATO, respuesta, formula, _FUENTE, **bloques)
    assert msg is None  # no cabe completa y no hay corte válido (un solo "párrafo" sin oraciones)
    assert ai_explain._fit_respuesta(respuesta, 1000) is None
    oraciones = ("😀" * 10 + ". ") * 100
    msg = ai_explain._fit_leaf_message(_DATO, oraciones.rstrip(), formula, _FUENTE, **bloques)
    assert msg is not None and _len16(msg) <= 4096 - 100


def test_cs23_c_sin_presupuesto_ni_siquiera_para_el_marco_devuelve_none():
    bloques = dict(cuenta=_CUENTA, desglose=_D, grafico=_G)
    formula = "f" * 4000
    assert ai_explain._fit_leaf_message(_DATO, "Hola.", formula, _FUENTE, **bloques) is None


async def test_cs23_c_el_handler_envia_unavailable_si_fit_leaf_message_da_none(monkeypatch):
    monkeypatch.setattr(ai_explain, "_fit_leaf_message", lambda *a, **k: None)
    bot = await _correr_boton("vf", "Una explicación breve.")
    assert _texto_final(bot) == ai_explain.EXPLAIN_UNAVAILABLE_MSG
    assert ai_explain.DETERMINISTIC_PREFIX not in _texto_final(bot)


async def test_cs23_d_los_logs_de_recorte_y_de_entrega_omitida_llevan_solo_enteros(caplog):
    caplog.set_level(logging.INFO, logger="investbot")
    canario = "CANARIO-SECRETO"
    larga = ("Esta oración lleva " + canario + " dentro del texto. ") * 80
    assert len(larga) > 1800
    bloques = dict(cuenta=_CUENTA, desglose=None, grafico=None)
    formula = _armar(1000, **bloques)
    msg = ai_explain._fit_leaf_message(_DATO, larga.rstrip(), formula, _FUENTE, **bloques)
    assert msg is not None
    recortes = [r.getMessage() for r in caplog.records if "GEMINI_RESPUESTA_RECORTADA" in r.getMessage()]
    assert len(recortes) == 1
    assert re.fullmatch(r"GEMINI_RESPUESTA_RECORTADA chars=\d+ max=\d+", recortes[0])
    assert ai_explain._fit_leaf_message(_DATO, "😀" * 1500 + ".", formula, _FUENTE, **bloques) is None
    omitidas = [r.getMessage() for r in caplog.records if "GEMINI_ENTREGA_OMITIDA" in r.getMessage()]
    assert omitidas and all(re.fullmatch(r"GEMINI_ENTREGA_OMITIDA motivo=(presupuesto|sin_corte) chars=\d+", m) for m in omitidas)
    assert all(canario not in r.getMessage() for r in caplog.records)


async def test_cs23_e_la_nota_va_despues_de_los_guards_cuenta_en_4096_y_no_en_1800():
    larga = _parrafos_hasta(3000)
    # `_fetch_explanation` NO la agrega ni recorta
    client = _client_with_handler(_ok_handler(larga))
    devuelta = await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client))
    assert devuelta == ai_explain._normalize_layout(larga) and ai_explain._NOTA_ACORTADA not in devuelta
    bloques = dict(cuenta=None, desglose=None, grafico=None)
    msg = ai_explain._fit_leaf_message(_DATO, devuelta, "f" * 10, _FUENTE, **bloques)
    assert msg.count(ai_explain._NOTA_ACORTADA) == 1
    cuerpo = msg.split("\n\n" + ai_explain._NOTA_ACORTADA)[0].split(f"📌 Dato: {_DATO}\n\n")[1]
    assert _len16(cuerpo) <= ai_explain._MAX_RESPUESTA_CHARS
    assert devuelta.startswith(cuerpo)
    assert _len16(msg) <= 4096
    # la nota va justo después de la respuesta, antes de la fórmula
    assert msg.index(ai_explain._NOTA_ACORTADA) < msg.index("📐 Fórmula")


# ---------------------------------------------------------------------------
# CS-24 : orden y bypass
# ---------------------------------------------------------------------------


def _bypass_filas():
    relleno = "Una frase de relleno sin datos. "
    return [
        ("backtick_en_esquema", "Mirá ht`tps://evil.com ahora.", True),
        ("backtick_en_www", "Mirá www`.evil.com ahora.", True),
        ("link_markdown", "Mirá [x]`(http://a) ahora.", False),
        ("corchete_parentesis", "mira ](`x", False),
        ("mencion_doble", "Hola @@ana", False),
        ("mencion_con_zwsp", "Hola @​ana", False),
        ("url_en_la_posicion_1700", relleno * 53 + "http://evil.example/x " + relleno, False),
        ("url_pasada_la_posicion_1800_en_2500", relleno * 57 + "http://evil.example/x " + relleno * 20, False),
    ]


@pytest.mark.parametrize("nombre,texto,discrimina", _bypass_filas(), ids=[f[0] for f in _bypass_filas()])
async def test_cs24_b_tabla_de_bypass_todas_rechazadas(nombre, texto, discrimina, caplog):
    if discrimina:
        # el filtro crudo da False y el normalizado True: por eso se normaliza ANTES
        assert gemini_client.violates_output_filter(texto) is False
        assert gemini_client.violates_output_filter(ai_explain._normalize_layout(texto)) is True
    client = _client_with_handler(_ok_handler(texto))
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client))
    assert "guard=salida" in _registros_guard(caplog)[0].getMessage()
    if nombre.startswith("url_"):
        assert texto.index("http://") > 1600


async def test_cs24_b_control_positivo_un_texto_limpio_si_pasa_el_pipeline():
    texto = "Una explicación limpia, sin enlaces ni menciones."
    client = _client_with_handler(_ok_handler(texto))
    assert await ai_explain._fetch_explanation(**_fetch_kwargs(http_client=client)) == texto


# ---------------------------------------------------------------------------
# CS-25 : guards intactos y unión sin lavado
# ---------------------------------------------------------------------------

_HASHES_GUARDS = {
    "_no_new_protected_tokens": "157f1d5ea1663d93d4ea2c54d91bdd40bb328edbd1c3fefe9abd856f78b4a423",
    "_normalize_numeric_token": "cac1ca3aeea9aa0e188dbaeb2b219b2a3c6decd77190c5ebeff11beb7637de4b",
    "_percent_token_a_decimal": "45f744706fa01f2ed6cf3422e053e2490b3d898da453d191c3d941f7b20ee4dd",
}


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def test_cs25_a_los_guards_no_cambiaron_hash_del_codigo_fuente():
    # Tocar cualquiera de estas funciones exige que `security` actualice el literal.
    for nombre, esperado in _HASHES_GUARDS.items():
        assert _sha(inspect.getsource(getattr(ai_explain, nombre))) == esperado, nombre
    assert _sha(inspect.getsource(ai_rewrite.protected_tokens)) == (
        "167a57784f516d3b6f88734605c0dbcbe6c2c57b8906ec65f0ac4012908644ec"
    )
    assert _sha(ai_rewrite._PROTECTED_TOKEN_RE.pattern) == (
        "44388801cee97911ef092c4e1e587487203c5662906f2912314b19a65c249878"
    )


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
async def test_cs25_c_cifra_inventada_rechazada(code):
    crudo, presentado = _crudo_y_presentado(code)
    tokens = ai_explain._compute_datos_tokens(crudo, presentado)
    assert "987654.31" not in tokens
    with pytest.raises(ai_explain._ExplainUnavailable):
        await ai_explain._fetch_explanation(
            **_fetch_kwargs(
                http_client=_client_with_handler(_ok_handler("El dato vale 987654.31 en total.")),
                datos=presentado, tokens=tokens,
            )
        )
    # cada token monetario presentado con el último dígito cambiado se rechaza (si no está en el conjunto)
    monetarios = [v for _, v in _hojas(presentado) if isinstance(v, str) and re.fullmatch(r"\$-?[\d,]+\.\d\d", v)]
    for monto in monetarios:
        ultimo = monto[-1]
        mutado = monto[:-1] + str((int(ultimo) + 1) % 10)
        if ai_explain._normalize_numeric_token(mutado) in tokens:
            continue
        assert ai_explain._no_new_protected_tokens(tokens, f"vale {mutado}") is False
        with pytest.raises(ai_explain._ExplainUnavailable):
            await ai_explain._fetch_explanation(
                **_fetch_kwargs(
                    http_client=_client_with_handler(_ok_handler(f"El monto es {mutado} hoy.")),
                    datos=presentado, tokens=tokens,
                )
            )


# ---------------------------------------------------------------------------
# CS-26 : superficie al modelo
# ---------------------------------------------------------------------------

_CANARIOS = ("CANARIOTK", "CANARIO_CO_INC", "CANARIO_IND_X", "CANARIO_SECTOR_X", "CANARIO_PERFIL_X", "CANARIO_PEER_X", "987654321")


def _contexto_con_canarios(code: str):
    comunes = dict(chat_id=987654321, ticker="CANARIOTK", company_name="CANARIO_CO_INC")
    if code in ai_explain_content.QUESTIONS_AVANZADO:
        return _avanzado_context(
            **comunes, industry="CANARIO_IND_X", sector="CANARIO_SECTOR_X",
            precio_actual=150.0, price_avg_50=140.0, price_avg_200=130.0,
        )
    return _texto_libre_context(
        **comunes, industry="CANARIO_IND_X", sector="CANARIO_SECTOR_X",
        risk_fit={"encaja": True, "perfil": "CANARIO_PERFIL_X", "beta": 1.15, "etiqueta_activo": "acción de riesgo moderado"},
        peer_comparison={
            "per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
            "per_maximo_peers": 30.0, "peers_usados": ["CANARIO_PEER_X", "MSFT"],
            "posicion": "en_linea", "motivo_no_comparable": None,
        },
    )


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
def test_cs26_a_canarios_ausentes(code):
    ctx = _contexto_con_canarios(code)
    crudo = ai_explain._build_explain_payload(ctx, code)
    presentado = ai_explain._datos_para_el_modelo(code, crudo)
    user = json.dumps({"pregunta": "pregunta fija", "datos": presentado}, ensure_ascii=False)
    for canario in _CANARIOS:
        assert canario not in user, (code, canario)
        assert canario not in ai_explain.SYSTEM_PROMPT_EXPLAIN
        assert canario not in ai_explain.SYSTEM_PROMPT_PASO_A_PASO


_CLAVES_STR = {
    "ver": {"cuenta", "diferencia_vs_precio_abs", "escenario_elegido", "modelo", "posicion_vs_precio", "precio_actual", "valor_justo_total"},
    "vf": {"cuenta", "diferencia_vs_precio_abs", "escenario_elegido", "modelo", "posicion_vs_precio", "precio_actual", "valor_justo_dcf", "valor_justo_graham", "valor_justo_multiplos", "valor_justo_total"},
    "gra": {"conservador", "cuenta", "eps_ttm", "escenario_elegido", "g_aplicado", "modelo", "optimista", "pesimista", "y_value"},
    "dcf": {"conservador", "cuenta", "dcf_equity_value", "dcf_fcf_base", "dcf_g_fcf", "dcf_valor_presente_flujos", "dcf_valor_terminal_descontado", "dcf_wacc", "escenario_elegido", "modelo", "optimista", "pesimista"},
    "mul": {"conservador", "cuenta", "eps_ttm", "escenario_elegido", "modelo", "optimista", "per_promedio_peers", "pesimista"},
    "rat": {"cost_of_revenue", "cuenta", "current_assets", "current_liabilities", "eps_ttm", "margen_bruto", "market_cap", "modelo", "per", "precio_actual", "ps", "ratio_liquidez", "revenue"},
    "pil": {"cuenta", "modelo", "net_income_antiguo", "net_income_reciente", "ratio_liquidez", "revenue_antiguo", "revenue_reciente"},
    "ren": {"modelo", "roe"},
    "rsk": {"beta", "beta_umbral_alto", "beta_umbral_bajo", "cuenta", "etiqueta_activo", "modelo", "perfil"},
    "mom": {"cuenta", "etiqueta", "modelo", "precio_actual", "price_avg_200", "price_avg_50", "year_high", "year_low"},
    "cmp": {"cuenta", "eps_ttm", "modelo", "per_promedio_peers", "per_propio", "posicion", "precio_actual"},
    "mod": {"sector"},
    "alz": {"a", "b", "c", "cuenta", "d", "e", "modelo", "z", "zona"},
    "azp": {"a", "b", "c", "cuenta", "d", "modelo", "z", "zona"},
    "pig": {"apalancamiento_t", "apalancamiento_t1", "cfo_t", "cuenta", "liquidez_t", "liquidez_t1", "margen_t", "margen_t1", "modelo", "net_income_t", "nombre", "roa_t", "roa_t1", "rotacion_t", "rotacion_t1", "shares_t", "shares_t1"},
    "pir": {"cfo_t", "cuenta", "modelo", "net_income_t", "nombre", "roa_t", "roa_t1"},
    "pia": {"apalancamiento_t", "apalancamiento_t1", "cuenta", "liquidez_t", "liquidez_t1", "modelo", "nombre", "shares_t", "shares_t1"},
    "pie": {"cuenta", "margen_t", "margen_t1", "modelo", "nombre", "rotacion_t", "rotacion_t1"},
    "ben": {"modelo", "motivo"},
    "mgr": {"capital_invertido", "cuenta", "current_assets", "current_liabilities", "ebit", "modelo", "ppe_net", "roic"},
    "mge": {"cash", "cuenta", "earnings_yield", "ebit", "ev", "market_cap", "modelo", "total_debt"},
    "aqv": {"cuenta", "earnings_yield", "modelo", "umbral_alto", "umbral_bajo", "value"},
    "aqq": {"cuenta", "gross_margin", "gross_margin_umbral_alto", "gross_margin_umbral_bajo", "modelo", "piotroski_ratio", "piotroski_ratio_umbral_alto", "piotroski_ratio_umbral_bajo", "quality", "roe", "roe_umbral_alto", "roe_umbral_bajo"},
    "aqm": {"cuenta", "modelo", "momentum", "precio_actual", "price_avg_200", "price_avg_50"},
    "aql": {"beta", "beta_umbral_alto", "beta_umbral_bajo", "cuenta", "low_vol", "modelo"},
}


def _claves_str(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                acc.add(k)
            else:
                _claves_str(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _claves_str(v, acc)
    return acc


def test_cs26_b_el_snapshot_cubre_los_25_codigos():
    assert set(_CLAVES_STR) == set(_PRESENTABLE_CODES)


@pytest.mark.parametrize("code", _PRESENTABLE_CODES)
def test_cs26_b_snapshot_claves_str(code):
    # Una clave `str` nueva rompe este test y exige revisión de `security`.
    _, presentado = _crudo_y_presentado(code)
    assert _claves_str(presentado) == _CLAVES_STR[code]


def test_cs26_d_los_campos_derivados_solo_se_agregan_en_vf_y_ver(monkeypatch):
    # Aunque el payload de otro código trajera `valor_justo_total` y `precio_actual`, no se derivan.
    monkeypatch.setattr(
        ai_explain, "_derivar_posicion_vs_precio",
        lambda *a: {"posicion_vs_precio": "por encima", "diferencia_vs_precio_abs": "1.0%"},
    )
    for code in _PRESENTABLE_CODES:
        crudo, _ = _crudo_y_presentado(code)
        presentado = ai_explain._datos_para_el_modelo(code, crudo)
        assert ("posicion_vs_precio" in presentado) == (code in ("vf", "ver")), code
        assert ("diferencia_vs_precio_abs" in presentado) == (code in ("vf", "ver")), code


def test_cs26_c_present_payload_solo_convierte_a_str_hojas_money_pct_ratio():
    for code in _PRESENTABLE_CODES:
        crudo, _ = _crudo_y_presentado(code)
        presentado = ai_explain._present_payload(crudo)  # sin campos derivados
        for (clave, antes), (_, despues) in zip(_hojas(crudo), _hojas(presentado)):
            if isinstance(despues, str) and not isinstance(antes, str):
                assert type(antes) in (int, float) and clave in ai_explain._PAYLOAD_KIND, (code, clave)
            if isinstance(antes, str):
                assert despues == antes
    vf_pres = ai_explain._present_payload(_crudo_y_presentado("vf")[0])
    assert "posicion_vs_precio" not in vf_pres  # los derivados los agrega el handler, no `_present_payload`


# ---------------------------------------------------------------------------
# CS-29 : IDOR y validaciones intactas
# ---------------------------------------------------------------------------


def test_cs29_idor_y_validaciones_siguen_funcionando_sin_cambios():
    store = ai_explain.ExplanationContextStore()
    cid = store.put(_texto_libre_context(chat_id=1))
    assert ai_explain._get_owned_context(store, cid, 1) is not None
    assert ai_explain._get_owned_context(store, cid, 2) is None  # otro chat -> como un id vencido
    assert ai_explain._get_owned_context(store, "deadbeef", 1) is None
    assert ai_explain._validated_perfil("CANARIO") == ai_explain._validated_perfil("otro-invalido")
    assert ai_explain._validated_sector("CANARIO") == ai_explain._SECTOR_NO_DISPONIBLE
    assert ai_explain._validated_peers(["MSFT", "CANARIO_PEER_X", "no valido", 5]) == ["MSFT"]
    assert "Technology" in ai_explain._SECTOR_ALLOWLIST


# ---------------------------------------------------------------------------
# CS-30 : una sola llamada por clic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    ["Texto sin ninguna etiqueta ni estructura.", _parrafos_hasta(3000), "Cifra inventada 987654.31 acá.", "***"],
    ids=["mala_estructura", "con_recorte", "rechazo_de_guard", "vacio"],
)
async def test_cs30_contador_de_llamadas_a_gemini_es_1_por_clic(texto):
    espia = _CountingClient(httpx.Response(200, json=gemini_response(texto)))
    bot = await _correr_boton("vf", texto, http=espia)
    assert espia.call_count == 1
    bot.edit_message_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# CS-31 : escala de montos
# ---------------------------------------------------------------------------

_PRESENTADO_ESCALA = {"market_cap": "$394.3 mil millones", "roe": "15.3%"}
_CRUDO_ESCALA = {"market_cap": 394328000000.0, "roe": 0.153}

_FILAS_ESCALA = [
    ("$394.3 mil millones", True),
    ("$394.3 millones", False),
    ("$394.3", False),
    ("394.3 billones", False),
    ("$394.3 mil  millones", False),  # dos espacios: fail-closed
    ("394.3\nmillones", False),
    ("$394.3 mil", False),
    ("$394.3 miles de millones", False),
]


@pytest.mark.parametrize("monto,acepta", _FILAS_ESCALA, ids=[f[0].replace(" ", "_").replace("\n", "NL") for f in _FILAS_ESCALA])
async def test_cs31_tabla_de_escalas_con_payload_en_mil_millones(monto, acepta, caplog):
    texto = f"La capitalización es {monto} según el dato."
    tokens = ai_explain._compute_datos_tokens(_CRUDO_ESCALA, _PRESENTADO_ESCALA)
    # el guard de números de HEAD acepta todas las filas: solo la escala discrimina
    assert ai_explain._no_new_protected_tokens(tokens, texto) is True
    assert ai_explain._escalas_consistentes(_PRESENTADO_ESCALA, texto) is acepta
    if acepta:
        assert await _fetch(texto, _CRUDO_ESCALA, _PRESENTADO_ESCALA) == texto
    else:
        with pytest.raises(ai_explain._ExplainUnavailable):
            await _fetch(texto, _CRUDO_ESCALA, _PRESENTADO_ESCALA)
        assert "guard=escala" in _registros_guard(caplog)[0].getMessage()


@pytest.mark.parametrize(
    "monto,acepta",
    [("$5.00", True), ("$5.00 millones", False), ("$5.00 mil", False), ("$5.00 mil millones", False)],
)
def test_cs31_payload_sin_sufijo_rechaza_cualquier_escala_agregada(monto, acepta):
    assert ai_explain._escalas_consistentes({"precio_actual": "$5.00"}, f"Vale {monto} hoy.") is acepta


def test_cs31_cifras_sin_sufijo_en_ambos_lados_no_se_tocan():
    assert ai_explain._escalas_consistentes({"a": "$5.00", "b": "15.3%"}, "Son $5.00 y 15.3%.")
    assert ai_explain._escalas_consistentes({"a": "$5.00"}, "No hay cifras aquí.")
    assert ai_explain._escalas_consistentes({"m": "$394.3 mil millones"}, "Alrededor de 394.3 mil millones.")


# ---------------------------------------------------------------------------
# Tipos de campos nuevos en el payload (sanidad extra)
# ---------------------------------------------------------------------------


def test_ca77_vf_presentado_contiene_exactamente_los_valores_esperados_para_el_modelo():
    crudo, presentado = _crudo_y_presentado("vf")
    assert presentado["precio_actual"] == "$550.00"
    assert presentado["valor_justo_total"] == "$496.00"
    assert presentado["posicion_vs_precio"] == "por debajo"
    assert presentado["diferencia_vs_precio_abs"] == "9.8%"
    assert crudo["precio_actual"] == 550.0  # el crudo no se toca
    assert "posicion_vs_precio" not in crudo
