"""Tests de la FASE F2 de `SDD_redaccion_gemini_iter2.md` (secciones 6, 16.2, 18.6-A7, 18.8).

Cubre CA-84 (variante con Gráfico), CA-85 (goldens con Gráfico), CA-86, CA-87,
CA-88 y CS-27 (a-g): `_bar`, `_build_grafico_vf`, `_build_grafico_escenarios`
(gra/dcf/mul), `_build_grafico_ver` y su integración en `_dispatch_leaf`.
Sin red (fixture `net_guard`) y sin FS.

Nombres `test_ca<n>_` / `test_cs<n>_` (CA-64).
"""

from __future__ import annotations

import builtins
import hashlib
import inspect
import json
import logging
import math
import pathlib
import re
from pathlib import Path

import pytest

from investbot import ai_explain
from investbot.summary import DISCLAIMER_NO_ASESORAMIENTO

from .test_ai_explain import _texto_libre_context
from .test_f1_redaccion import (
    _RESPUESTA_VF,
    _correr_boton,
    _len16,
    _parrafos_hasta,
    _sin_parse_mode,
    _texto_final,
)

ROOT = Path(__file__).resolve().parent.parent

_VF = {
    "valor_justo_multiplos": 500.0,
    "valor_justo_graham": 480.0,
    "valor_justo_dcf": 510.0,
    "valor_justo_total": 496.0,
    "precio_actual": 430.0,
}
_ESC = {"pesimista": 300.0, "conservador": 400.5, "optimista": 520.0, "precio_actual": 430.0}
_VER = {"valor_justo_total": 496.0, "precio_actual": 430.0}
_BUILDERS = {
    "vf": (ai_explain._build_grafico_vf, _VF),
    "escenarios": (ai_explain._build_grafico_escenarios, _ESC),
    "ver": (ai_explain._build_grafico_ver, _VER),
}
_LLENO, _VACIO = "█", "░"
_RESPUESTA_CORTA = (
    "💡 En simple\nEl valor justo total es $496.00 frente al precio actual de $550.00. "
    "Es una estimación que depende de supuestos, no una recomendación."
)


_RESPUESTA_SIN_CIFRAS = "💡 En simple\nEs una estimación que depende de supuestos, no una recomendación."


def _barra(n: int, ancho: int = 12) -> str:
    return _LLENO * n + _VACIO * (ancho - n)


def _sin_si_o_no(bloque: str) -> list[str]:
    return bloque.split("\n")


# ---------------------------------------------------------------------------
# CA-86 : golden de `_build_grafico_vf` y half-up
# ---------------------------------------------------------------------------


def test_ca86_golden_build_grafico_vf_con_valores_sinteticos():
    esperado = """📊 Valor justo vs precio
Múltiplos      ████████████  $500.00
Graham         ███████████░  $480.00
Flujo (DCF)    ████████████  $510.00
Valor Justo    ████████████  $496.00
Precio actual  ██████████░░  $430.00"""
    # Escala común al máximo del conjunto (510): 500/510*12 = 11.76 -> 12 bloques.
    assert ai_explain._build_grafico_vf(_VF) == esperado


def test_ca86_500_contra_430_con_maximo_500_da_12_y_10_bloques():
    assert ai_explain._bar(500.0, 500.0) == _barra(12)
    assert ai_explain._bar(430.0, 500.0) == _barra(10)
    bloque = ai_explain._build_grafico_vf({**_VF, "valor_justo_dcf": 500.0})
    lineas = _sin_si_o_no(bloque)
    assert lineas[1].count(_LLENO) == 12 and lineas[5].count(_LLENO) == 10


def test_ca86_half_up_explicito_5_de_24_con_ancho_12_da_3_bloques():
    # `round(2.5) == 2` (half-even): la versión con `round` daría 2.
    assert ai_explain._bar(5, 24, 12) == _barra(3)
    assert ai_explain._bar(5, 24) == _barra(3)
    assert ai_explain._bar(7, 24, 12) == _barra(4)  # 3.5 -> 4 (half-even daría 4 también)
    assert ai_explain._bar(1, 8, 12) == _barra(2)  # 1.5 -> 2 (half-even daría 2 también)
    assert ai_explain._bar(3, 8, 12) == _barra(5)  # 4.5 -> 5 (half-even daría 4)


def test_ca86_solo_contiene_bloques_texto_fijo_y_montos_y_mide_menos_que_el_tope():
    for builder, datos in _BUILDERS.values():
        bloque = builder(datos)
        assert bloque is not None and _len16(bloque) <= ai_explain._MAX_GRAFICO_CHARS
    assert ai_explain._MAX_GRAFICO_CHARS == 400


def test_ca86_escalas_comunes_y_todos_iguales_dan_barras_llenas():
    iguales = {k: 100.0 for k in _VF}
    bloque = ai_explain._build_grafico_vf(iguales)
    for linea in _sin_si_o_no(bloque)[1:]:
        assert _barra(12) in linea
    # El máximo del conjunto (no el primer valor) fija la escala.
    bloque = ai_explain._build_grafico_vf({**_VF, "valor_justo_multiplos": 100.0, "precio_actual": 500.0})
    lineas = _sin_si_o_no(bloque)
    assert lineas[1].count(_LLENO) == 2 and lineas[5].count(_LLENO) == 12


def test_ca86_escenarios_y_ver_golden():
    assert ai_explain._build_grafico_escenarios(_ESC) == """📊 Escenarios vs precio
Pesimista      ███████░░░░░  $300.00
Conservador    █████████░░░  $400.50
Optimista      ████████████  $520.00
Precio actual  ██████████░░  $430.00"""
    assert ai_explain._build_grafico_ver(_VER) == """📊 Valor justo vs precio
Valor Justo    ████████████  $496.00
Precio actual  ██████████░░  $430.00"""


def test_ca86_valor_mayor_al_maximo_se_acota_a_barra_llena():
    assert ai_explain._bar(10, 5) == _barra(12)
    assert ai_explain._bar(1e308, 1e-300) == _barra(12)


# ---------------------------------------------------------------------------
# CA-87 : datos insuficientes -> None y mensaje idéntico al de una versión sin gráfico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nombre", sorted(_BUILDERS))
def test_ca87_menos_de_dos_valores_validos_devuelve_none(nombre):
    builder, datos = _BUILDERS[nombre]
    claves = list(datos)
    assert builder({}) is None
    for conservar in claves:
        assert builder({k: (v if k == conservar else None) for k, v in datos.items()}) is None
    assert builder({k: (v if k == conservar else "x") for k, v in datos.items()}) is None


async def test_ca87_mensaje_final_identico_al_de_una_version_sin_grafico(monkeypatch):
    escenarios = {
        esc: {k: None for k in ("valor_justo_multiplos", "valor_justo_graham", "valor_justo_dcf", "valor_justo_total")}
        for esc in ("pesimista", "conservador", "optimista")
    }
    ctx = _texto_libre_context(scenarios=escenarios, precio_actual=550.0)
    assert ai_explain._build_grafico_vf({**ai_explain._build_explain_payload(ctx, "vf")}) is None
    respuesta = "💡 En simple\nNo hay datos suficientes para comparar los modelos entre sí."
    con = _texto_final(await _correr_boton("vf", respuesta, ctx=ctx))
    monkeypatch.setitem(ai_explain._GRAFICO_POR_PREGUNTA, "vf", lambda datos: None)
    sin = _texto_final(await _correr_boton("vf", respuesta, ctx=ctx))
    assert con == sin
    assert "📊 Valor justo vs precio" not in con and _LLENO not in con


# ---------------------------------------------------------------------------
# CA-88 : bordes
# ---------------------------------------------------------------------------


def test_ca88_valor_negativo_o_cero_es_nd_sin_barra():
    for malo in (0, 0.0, -1, -0.5, -1e300):
        assert ai_explain._bar(malo, 10) == "n/d"
        bloque = ai_explain._build_grafico_vf({**_VF, "valor_justo_graham": malo})
        graham = next(l for l in bloque.split("\n") if l.startswith("Graham"))
        assert graham == "Graham         n/d"
        assert _LLENO not in graham and _VACIO not in graham
        assert bloque.count(_LLENO) > 0  # las demás líneas sí tienen barra


def test_ca88_relacion_1000_a_1_da_minimo_un_bloque():
    assert ai_explain._bar(1, 1000) == _barra(1)
    assert ai_explain._bar(0.001, 1000) == _barra(1)
    bloque = ai_explain._build_grafico_vf({**_VF, "precio_actual": 1000.0, "valor_justo_graham": 1.0})
    graham = next(l for l in bloque.split("\n") if l.startswith("Graham"))
    assert graham.count(_LLENO) == 1 and graham.count(_VACIO) == 11


def test_ca88_un_solo_valor_valido_da_none():
    assert ai_explain._build_grafico_vf({"valor_justo_total": 496.0}) is None
    assert ai_explain._build_grafico_ver({"valor_justo_total": 496.0, "precio_actual": None}) is None
    assert ai_explain._build_grafico_escenarios({"optimista": 520.0}) is None


def test_ca88_none_mezclado_deja_n_d_en_su_linea_y_dibuja_el_resto():
    bloque = ai_explain._build_grafico_vf({**_VF, "valor_justo_dcf": None})
    assert [l for l in bloque.split("\n") if l.startswith("Flujo")] == ["Flujo (DCF)    n/d"]
    assert bloque.count("$") == 4


def test_ca88_maximo_cero_sobre_bar_es_nd_sin_excepcion():
    assert ai_explain._bar(5, 0) == "n/d"
    assert ai_explain._bar(5, -3) == "n/d"
    assert ai_explain._bar(5, None) == "n/d"
    assert ai_explain._bar(None, 5) == "n/d"


def test_ca88_1e300_hace_que_el_bloque_supere_400_y_devuelve_none_no_nd():
    assert len(ai_explain._money(1e300)) == 405
    assert ai_explain._build_grafico_ver({"valor_justo_total": 1e300, "precio_actual": 430.0}) is None
    assert ai_explain._build_grafico_vf({**_VF, "valor_justo_total": 1e300}) is None


@pytest.mark.parametrize("malo", [math.nan, math.inf, -math.inf, True, False, "x", None, [1], {"a": 1}, 10**400])
def test_ca88_valores_invalidos_no_levantan_y_quedan_n_d(malo):
    assert ai_explain._bar(malo, 10) == "n/d"
    assert ai_explain._bar(10, malo) == "n/d"
    bloque = ai_explain._build_grafico_vf({**_VF, "valor_justo_graham": malo})
    assert "Graham         n/d" in bloque.split("\n")


def test_ca88_sin_red_ni_fs_y_sin_acceso_a_contextos(net_guard, monkeypatch):
    llamadas = {"open": 0}
    abrir = builtins.open

    def _open_spy(*a, **k):
        llamadas["open"] += 1
        return abrir(*a, **k)

    monkeypatch.setattr(builtins, "open", _open_spy)
    for nombre in ("open", "read_text", "write_text", "read_bytes", "write_bytes"):
        original = getattr(pathlib.Path, nombre)

        def _spy(self, *a, _o=original, **k):
            llamadas["open"] += 1
            return _o(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, nombre, _spy)
    for builder, datos in _BUILDERS.values():
        assert builder(datos) is not None
        builder({"x": "y"})
    assert llamadas["open"] == 0
    assert net_guard.attempts == []


# ---------------------------------------------------------------------------
# CS-27 : gráfico
# ---------------------------------------------------------------------------

_BUILDERS_FUNCS = (
    ai_explain._build_grafico_vf,
    ai_explain._build_grafico_escenarios,
    ai_explain._build_grafico_ver,
)


def test_cs27_a_firma_datos_dict_a_optional_str_sin_store_chat_id_ni_context_id():
    for f in _BUILDERS_FUNCS:
        sig = inspect.signature(f)
        assert list(sig.parameters) == ["datos"]
        assert not {"store", "chat_id", "context_id"} & set(sig.parameters)
        assert sig.parameters["datos"].annotation in (dict, "dict")
        assert sig.return_annotation in ("Optional[str]",)
    # Todos los constructores registrados por pregunta cumplen lo mismo.
    assert set(ai_explain._GRAFICO_POR_PREGUNTA) == {"vf", "ver", "gra", "dcf", "mul"}
    for f in ai_explain._GRAFICO_POR_PREGUNTA.values():
        assert f in _BUILDERS_FUNCS


def test_cs27_b_solo_int_o_float_finitos_positivos_con_maximo_positivo():
    validos = (1, 0.5, 1e-300, 10**20, 3.0)
    for v in validos:
        assert ai_explain._bar(v, 10**21) != "n/d"
        assert ai_explain._bar(1, v) != "n/d"
    malos = (0, -1, math.nan, math.inf, -math.inf, True, False, "1", None, 10**400, 1 + 2j, b"1")
    for v in malos:
        assert ai_explain._bar(v, 10) == "n/d"
        assert ai_explain._bar(5, v) == "n/d"
    # `bool` es subclase de `int`: no entra (`type(v) in (int, float)`).
    assert ai_explain._build_grafico_ver({"valor_justo_total": True, "precio_actual": 430.0}) is None
    assert ai_explain._build_grafico_ver({"valor_justo_total": 496.0, "precio_actual": math.nan}) is None


@pytest.mark.parametrize("malo", [math.nan, math.inf, True, "x", 1e300])
def test_cs27_c_valores_hostiles_nunca_levantan_ni_dibujan_barra_para_ellos(malo):
    for builder, datos in _BUILDERS.values():
        for clave in datos:
            res = builder({**datos, clave: malo})
            assert res is None or isinstance(res, str)
            if isinstance(res, str):
                assert "nan" not in res and "inf" not in res and "True" not in res
    assert ai_explain._bar(malo, 0) == "n/d"
    assert ai_explain._bar(5, 0) == "n/d"


def test_cs27_c_excepcion_inesperada_devuelve_none_y_loguea_solo_el_tipo(monkeypatch, caplog):
    def _boom(x):
        raise ValueError("SECRETO-CANARIO-123")

    monkeypatch.setattr(ai_explain, "_money", _boom)
    for builder, datos in _BUILDERS.values():
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=ai_explain.logger.name):
            assert builder(datos) is None
        registros = [r.getMessage() for r in caplog.records if "GEMINI_GRAFICO_FALLO" in r.getMessage()]
        assert registros == ["GEMINI_GRAFICO_FALLO tipo=ValueError"]
        assert "SECRETO-CANARIO-123" not in caplog.text
    for malo in (None, 5, "x", [1]):
        for f in _BUILDERS_FUNCS:
            assert f(malo) is None


def test_cs27_d_si_supera_el_tope_devuelve_none_sin_truncar(monkeypatch):
    assert ai_explain._build_grafico_vf({**_VF, "valor_justo_total": 1e300}) is None
    # Justo en el tope: se entrega completo; un carácter más: None.
    bloque = ai_explain._build_grafico_ver(_VER)
    exceso = ai_explain._MAX_GRAFICO_CHARS - _len16(bloque)
    monkeypatch.setattr(ai_explain, "_GRAFICO_ETIQUETA_ANCHO", ai_explain._GRAFICO_ETIQUETA_ANCHO + exceso // 2)
    llenado = ai_explain._build_grafico_ver(_VER)
    assert llenado is not None and _len16(llenado) <= ai_explain._MAX_GRAFICO_CHARS
    monkeypatch.setattr(ai_explain, "_GRAFICO_ETIQUETA_ANCHO", 400)
    assert ai_explain._build_grafico_ver(_VER) is None


def test_cs27_e_caracteres_permitidos_solo_bloques_etiquetas_constantes_y_money():
    etiquetas = "📊 Valor justo vs precio Escenarios Múltiplos Graham Flujo (DCF) Valor Justo Precio actual Pesimista Conservador Optimista n/d"
    for builder, datos in _BUILDERS.values():
        for variante in (datos, {**datos, next(iter(datos)): None}):
            bloque = builder(variante)
            if bloque is None:
                continue
            permitidos = set(etiquetas) | {_LLENO, _VACIO, "\n", " ", "$", ",", ".", *"0123456789"}
            assert set(bloque) <= permitidos
            for linea in bloque.split("\n")[1:]:
                resto = linea[ai_explain._GRAFICO_ETIQUETA_ANCHO:]
                assert re.fullmatch(r"(?:[█░]{12}  \$[\d,]+\.\d{2}|n/d)", resto)


async def test_cs27_f_sin_red_ni_fs_sin_parse_mode_y_nunca_en_datos_del_contexto_ni_tokens(monkeypatch, net_guard):
    capturado = {}
    original = ai_explain._fetch_explanation

    async def _espia(**kwargs):
        capturado.update(kwargs)
        return await original(**kwargs)

    monkeypatch.setattr(ai_explain, "_fetch_explanation", _espia)
    bot = await _correr_boton("vf", _RESPUESTA_VF)
    texto = _texto_final(bot)
    assert _LLENO in texto  # el gráfico SÍ llegó al usuario...
    modelo = json.dumps(capturado["datos_del_contexto"], ensure_ascii=False)
    assert _LLENO not in modelo and _VACIO not in modelo and "grafico" not in capturado["datos_del_contexto"]
    assert all(_LLENO not in t and _VACIO not in t and "Valor justo vs precio" not in t for t in capturado["datos_tokens"])
    assert "grafico" not in modelo.lower()
    # ... pero no al modelo: el cuerpo enviado a Gemini no lo contiene.
    _sin_parse_mode(bot)
    assert net_guard.attempts == []
    fuente = inspect.getsource(ai_explain)
    for nombre in ("_bar", "_bloque_grafico", "_build_grafico_vf", "_build_grafico_escenarios", "_build_grafico_ver"):
        assert "parse_mode" not in inspect.getsource(getattr(ai_explain, nombre))
    assert fuente.count("_GRAFICO_POR_PREGUNTA.get(question_code)") == 1


async def test_cs27_f_el_texto_enviado_a_gemini_no_contiene_el_grafico():
    cuerpos = []
    from .test_ai_explain import _client_with_handler, _ok_handler

    def _handler(request):
        cuerpos.append(request.content.decode("utf-8"))
        return _ok_handler(_RESPUESTA_VF)(request)

    bot = await _correr_boton("vf", _RESPUESTA_VF, http=_client_with_handler(_handler))
    assert _LLENO in _texto_final(bot)
    assert len(cuerpos) == 1
    assert _LLENO not in cuerpos[0] and _VACIO not in cuerpos[0] and "Valor justo vs precio" not in cuerpos[0]


def test_cs27_f_el_gráfico_no_muta_el_datos_de_entrada():
    for builder, datos in _BUILDERS.values():
        copia = dict(datos)
        builder(datos)
        assert datos == copia


def test_cs27_g_requirements_y_dockerfile_sin_cambios_respecto_de_502d5df():
    esperado = {
        "requirements.txt": "357095f82b0bbd18a0cea79747db0db5a6f742e447f1269f8226a55fa86852a3",
        "Dockerfile": "5f372b339b2de9b95fd45ad8fd00f562e5420f7ff3011e0fdce2ae872860d8aa",
    }
    for archivo, sha in esperado.items():
        assert hashlib.sha256((ROOT / archivo).read_bytes()).hexdigest() == sha


# ---------------------------------------------------------------------------
# CA-84 (variante con Gráfico) y CA-85 (goldens con Gráfico)
# ---------------------------------------------------------------------------


async def test_ca84_con_grafico_exito_y_recorte_sin_parse_mode_y_orden_cuenta_grafico_desglose():
    for texto_modelo, recortado in ((_parrafos_hasta(900), False), (_parrafos_hasta(3000), True)):
        bot = await _correr_boton("vf", texto_modelo)
        _sin_parse_mode(bot)
        assert bot.edit_message_text.await_count == 1
        texto = _texto_final(bot)
        assert "📊 Valor justo vs precio" in texto
        assert texto.index("🧮 Cuenta") < texto.index("📊 Valor justo vs precio") < texto.index("🔍 Desglose")
        assert texto.index("🔍 Desglose") < texto.index(texto_modelo[:40])
        assert (ai_explain._NOTA_ACORTADA in texto) is recortado
        assert _len16(texto) <= 4096
    for codigo in ("ver", "dcf", "gra", "mul"):
        bot = await _correr_boton(codigo, _RESPUESTA_SIN_CIFRAS)
        _sin_parse_mode(bot)
        entre_cuenta_y_desglose = _texto_final(bot).split("🧮")[1].split("🔍")[0]
        assert "📊" in entre_cuenta_y_desglose
        # `gra`/`dcf`/`mul` no traen `precio_actual` en su payload: el gráfico lo toma del contexto.
        assert re.search(r"\nPrecio actual  [█░]{12}  \$550\.00(\n|$)", entre_cuenta_y_desglose)


async def test_ca84_sin_presupuesto_para_el_grafico_se_omite_primero_y_el_resto_se_entrega(monkeypatch):
    # Con una fórmula enorme el presupuesto de respuesta cae bajo 400: se omite el Gráfico.
    original = ai_explain.ai_explain_content.formulas

    def _formulas(kind):
        d = dict(original(kind))
        d["vf"] = "F" * 2800
        return d

    monkeypatch.setattr(ai_explain.ai_explain_content, "formulas", _formulas)
    bot = await _correr_boton("vf", _parrafos_hasta(900))
    _sin_parse_mode(bot)
    texto = _texto_final(bot)
    assert _len16(texto) <= 4096
    assert _LLENO not in texto and "🧮 Cuenta" in texto


_GOLDEN_VF_GRAFICO = """🤖 Con ayuda de Gemini

📌 Dato: Valor Justo Total (Conservador): $496.00 — precio actual: $550.00

🧮 Cuenta: ($500.00 + $480.00 + $510.00) / 3 = $496.00

📊 Valor justo vs precio
Múltiplos      ███████████░  $500.00
Graham         ██████████░░  $480.00
Flujo (DCF)    ███████████░  $510.00
Valor Justo    ███████████░  $496.00
Precio actual  ████████████  $550.00

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

_GOLDEN_VER_GRAFICO = """🤖 Con ayuda de Gemini

📌 Dato: Veredicto: Barata

🧮 Cuenta: Para saber si esta acción está barata o cara, el modelo hace 1 paso: compara el precio de hoy contra el Valor Justo Total que combina los 3 modelos (Múltiplos, Graham y DCF) — Precio actual $550.00 < Valor Justo Total $496.00 → Barata.

📊 Valor justo vs precio
Valor Justo    ███████████░  $496.00
Precio actual  ████████████  $550.00

🔍 Desglose:
• Precio actual (Precio actual) = $550.00 — sale de precio_actual — cotización (quote) de FMP. Lo que cuesta hoy 1 acción de la empresa en el mercado.
• Valor Justo Total (Valor Justo Total) = $496.00 — sale de cálculo completo: botón «💰 Valor Justo Total». Cuánto debería valer la acción según el promedio de los modelos calculables del bot.

💡 En simple
El valor justo total es $496.00 frente al precio actual de $550.00. Es una estimación que depende de supuestos, no una recomendación.

📐 Fórmula: Precio actual < Valor Justo Total (escenario elegido) → Barata · Precio actual > Valor Justo Total → Cara · sin Valor Justo Total calculable → sin veredicto
📊 Fuente del dato: Precio actual: cotización (quote) de FMP; Valor Justo Total: promedio de Múltiplos/Graham/DCF calculables (ver fuente de Valor Justo Total).

"""


async def test_ca85_golden_vf_paso_a_paso_con_grafico():
    bot = await _correr_boton("vf", _RESPUESTA_VF)
    assert _texto_final(bot) == _GOLDEN_VF_GRAFICO + DISCLAIMER_NO_ASESORAMIENTO


async def test_ca85_golden_ver_paso_a_paso_con_grafico():
    bot = await _correr_boton("ver", _RESPUESTA_CORTA)
    assert _texto_final(bot) == _GOLDEN_VER_GRAFICO + DISCLAIMER_NO_ASESORAMIENTO
