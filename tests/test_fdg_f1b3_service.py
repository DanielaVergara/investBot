"""F1b-3 de SDD_fuentes_datos_gratuitas.md: `FundamentalsService` (cadena, cache,
single-flight, presupuesto D-19, log CA-33) y `FundamentalsResult` (enum cerrado, CS-24).

Sin red ni tiempo real: proveedores falsos, `Clock` inyectado y `asyncio.Event`.
Nombres `test_fdg_ca<n>_*` / `test_fdg_tcs<n>_*` (meta-test de trazabilidad de la spec).
"""

from __future__ import annotations

import asyncio
import ast
import dataclasses
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from investbot import fundamentals as F
from investbot.edgar_xbrl import PARSE_CODES
from investbot.fundamentals import (
    FALLBACK_CODES,
    FundamentalsResult,
    FundamentalsService,
    ProviderUnavailable,
)

from .fdg_f1b3_helpers import Budget, Clock, Fake, make_result, never_sleep, spin

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "investbot"
HORA = 3600.0
DIA = 24 * HORA
LOGGER = "investbot.fundamentals"


def servicio(*proveedores, clock=None, sleep=never_sleep):
    clock = clock or Clock()
    return FundamentalsService(list(proveedores), now=clock.now, sleep=sleep), clock


def fmp(*script, **kw):
    return Fake("fmp", *script, cacheable=False, **kw)


def edgar(*script, **kw):
    return Fake("sec_edgar", *script, **kw)


def indisponible(codigo):
    return ProviderUnavailable(codigo)


def _logs(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("fundamentals ")
    ]


# =============================== CA-14: cache ==================================


async def test_fdg_ca14_segunda_consulta_dentro_de_6_h_no_llama_al_proveedor():
    ed = edgar(make_result())
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    primero = await svc.get("NVDA", quarters=12)
    clock.advance(6 * HORA - 1)
    segundo = await svc.get("NVDA", quarters=12)
    assert primero.source == segundo.source == "sec_edgar"
    assert len(ed.calls) == 1


async def test_fdg_ca14_a_las_6_h_exactas_el_positivo_vence_y_se_refresca():
    ed = edgar(make_result(marca="a"), make_result(marca="b"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(6 * HORA)
    res = await svc.get("NVDA", quarters=12)
    assert len(ed.calls) == 2 and res.own_metrics == {"marca": "b"}


async def test_fdg_ca14_el_negativo_dura_24_h_y_no_6():
    ed = edgar(indisponible("sin_facts"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    r1 = await svc.get("SPY", quarters=12)
    clock.advance(7 * HORA)  # el positivo ya habria vencido; el negativo no
    r2 = await svc.get("SPY", quarters=12)
    assert len(ed.calls) == 1
    assert r1.fallback_codes == r2.fallback_codes == ("sin_facts",)
    clock.advance(17 * HORA - 1)
    await svc.get("SPY", quarters=12)
    assert len(ed.calls) == 1
    clock.advance(1)  # 24 h exactas: vence
    await svc.get("SPY", quarters=12)
    assert len(ed.calls) == 2


async def test_fdg_ca14_el_negativo_existe_y_vence_con_su_veredicto_nuevo():
    ed = edgar(indisponible("datos_insuficientes"), make_result())
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    assert (await svc.get("JPM", quarters=12)).source == "fmp"
    clock.advance(DIA)
    assert (await svc.get("JPM", quarters=12)).source == "sec_edgar"


async def test_fdg_ca14_un_fallo_transitorio_no_se_cachea_como_negativo():
    ed = edgar(indisponible("no_disponible_temporal"), make_result())
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    r1 = await svc.get("NVDA", quarters=12)
    r2 = await svc.get("NVDA", quarters=12)  # sin avanzar el reloj: reintenta
    assert r1.source == "fmp" and r1.fallback_codes == ("no_disponible_temporal",)
    assert r2.source == "sec_edgar" and len(ed.calls) == 2


@pytest.mark.parametrize("codigo", sorted(F.TRANSIENT_CODES))
async def test_fdg_ca14_los_codigos_transitorios_no_dejan_negativo_pero_los_de_cobertura_si(codigo):
    ed = edgar(indisponible(codigo))
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    await svc.get("X", quarters=12)
    await svc.get("X", quarters=12)
    assert len(ed.calls) == 2


@pytest.mark.parametrize("codigo", sorted(FALLBACK_CODES - F.TRANSIENT_CODES - {"cache_antiguo", "ua_no_configurado"}))
async def test_fdg_ca14_los_codigos_de_cobertura_si_se_cachean_en_negativo(codigo):
    ed = edgar(indisponible(codigo))
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    await svc.get("X", quarters=12)
    await svc.get("X", quarters=12)
    assert len(ed.calls) == 1


async def test_fdg_ca14_stale_if_error_sirve_la_entrada_vencida_con_cache_antiguo():
    ed = edgar(make_result(), indisponible("no_disponible_temporal"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * HORA)  # vencida, pero con menos de 7 dias
    res = await svc.get("NVDA", quarters=12)
    assert res.source == "sec_edgar"
    assert res.fallback_codes == ("cache_antiguo",)
    assert len(ed.calls) == 2


async def test_fdg_ca14_stale_if_error_acepta_hasta_7_dias_inclusive_y_no_mas():
    ed = edgar(make_result(), indisponible("circuito_abierto"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * DIA)
    assert (await svc.get("NVDA", quarters=12)).fallback_codes == ("cache_antiguo",)
    clock.advance(1)  # 7 dias + 1 s
    res = await svc.get("NVDA", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("circuito_abierto",)


async def test_fdg_ca14_stale_if_error_tambien_cubre_una_excepcion_inesperada():
    ed = edgar(make_result(), RuntimeError("boom"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * HORA)
    res = await svc.get("NVDA", quarters=12)
    assert res.source == "sec_edgar" and res.fallback_codes == ("cache_antiguo",)


async def test_fdg_ca14_cache_antiguo_no_contamina_la_entrada_guardada():
    ed = edgar(make_result(), indisponible("no_disponible_temporal"), make_result(marca="nuevo"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * HORA)
    assert (await svc.get("NVDA", quarters=12)).fallback_codes == ("cache_antiguo",)
    fresco = await svc.get("NVDA", quarters=12)  # el refresco ya funciona
    assert fresco.fallback_codes == () and fresco.own_metrics == {"marca": "nuevo"}


async def test_fdg_ca14_un_veredicto_de_cobertura_reemplaza_a_la_entrada_vencida():
    ed = edgar(make_result(), indisponible("datos_insuficientes"))
    svc, clock = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * HORA)
    res = await svc.get("NVDA", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("datos_insuficientes",)
    assert "cache_antiguo" not in res.fallback_codes


async def test_fdg_ca14_sin_entrada_previa_el_fallo_transitorio_cae_a_fmp_sin_cache_antiguo():
    svc, _ = servicio(edgar(indisponible("bloqueado_por_sec")), fmp(make_result("fmp")))
    res = await svc.get("NVDA", quarters=12)
    assert res.source == "fmp" and res.fallback_codes == ("bloqueado_por_sec",)


async def test_fdg_ca14_single_flight_dos_consultas_simultaneas_hacen_una_sola_llamada():
    gate = asyncio.Event()
    ed = edgar(make_result(), gate=gate)
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    t1 = asyncio.ensure_future(svc.get("NVDA", quarters=12))
    t2 = asyncio.ensure_future(svc.get("nvda", quarters=12))
    await spin()
    assert len(ed.calls) == 1 and len(svc._inflight) == 1
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 is r2 and len(ed.calls) == 1 and not svc._inflight


async def test_fdg_ca14_single_flight_cancelar_a_un_espectador_no_cancela_la_descarga():
    gate = asyncio.Event()
    ed = edgar(make_result(), gate=gate)
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    t1 = asyncio.ensure_future(svc.get("NVDA", quarters=12))
    t2 = asyncio.ensure_future(svc.get("NVDA", quarters=12))
    await spin()
    t1.cancel()
    await spin()
    gate.set()
    assert (await t2).source == "sec_edgar"
    with pytest.raises(asyncio.CancelledError):
        await t1
    assert len(ed.calls) == 1
    assert (await svc.get("NVDA", quarters=12)).source == "sec_edgar" and len(ed.calls) == 1


async def test_fdg_ca14_single_flight_retira_la_clave_aunque_la_tarea_muera_cancelada():
    ed = edgar(asyncio.CancelledError(), make_result())
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    with pytest.raises(asyncio.CancelledError):
        await svc.get("NVDA", quarters=12)
    assert not svc._inflight  # try/finally: la clave no queda colgada
    assert (await svc.get("NVDA", quarters=12)).source == "sec_edgar"
    assert len(ed.calls) == 2


async def test_fdg_ca14_single_flight_retira_la_clave_tras_un_fallo():
    ed = edgar(indisponible("no_disponible_temporal"), make_result())
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    assert not svc._inflight
    assert (await svc.get("NVDA", quarters=12)).source == "sec_edgar"


async def test_fdg_ca14_lru_de_64_expulsa_al_menos_reciente():
    ed = edgar(make_result())
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    assert F.CACHE_MAX == 64
    for i in range(64):
        await svc.get(f"T{i}", quarters=12)
    await svc.get("T0", quarters=12)  # T0 pasa a ser la mas reciente
    await svc.get("T64", quarters=12)  # entra una nueva: sale T1 (no T0)
    assert len(svc._cache) == 64
    llamadas = len(ed.calls)
    await svc.get("T0", quarters=12)
    assert len(ed.calls) == llamadas  # T0 sigue en cache
    await svc.get("T1", quarters=12)
    assert len(ed.calls) == llamadas + 1  # T1 fue expulsada


async def test_fdg_ca14_la_clave_normaliza_punto_y_mayusculas_y_separa_por_ventana():
    ed = edgar(make_result())
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    await svc.get("BRK.B", quarters=12)
    await svc.get("brk-b", quarters=12)
    assert len(ed.calls) == 1
    await svc.get("BRK-B", quarters=20)  # otra ventana: otro paquete
    assert len(ed.calls) == 2


async def test_fdg_ca14_fmp_no_se_cachea_y_cada_instancia_tiene_su_propio_estado():
    fp = fmp(make_result("fmp"))
    svc, _ = servicio(fp)
    await svc.get("ADBE", quarters=12)
    await svc.get("ADBE", quarters=12)
    assert len(fp.calls) == 2 and not svc._cache
    a, _ = servicio(edgar(make_result()), fmp(make_result("fmp")))
    b, _ = servicio(edgar(make_result()), fmp(make_result("fmp")))
    await a.get("NVDA", quarters=12)
    assert a._cache and not b._cache


async def test_fdg_ca14_un_proveedor_cacheable_sin_cobertura_deja_negativo_sin_codigo():
    ed = edgar(None)
    svc, _ = servicio(ed, fmp(make_result("fmp")))
    r1 = await svc.get("X", quarters=12)
    r2 = await svc.get("X", quarters=12)
    assert r1.source == r2.source == "fmp" and r1.fallback_codes == ()
    assert len(ed.calls) == 1


async def test_fdg_ca14_una_excepcion_inesperada_se_traduce_a_no_disponible_temporal(caplog):
    caplog.set_level(logging.INFO)
    canario = "CANARIO-MENSAJE-CON-URL-https://x"
    svc, _ = servicio(edgar(RuntimeError(canario)), fmp(make_result("fmp")))
    res = await svc.get("NVDA", quarters=12)
    assert res.fallback_codes == ("no_disponible_temporal",)
    assert canario not in caplog.text and "RuntimeError" in caplog.text


# ======================= D-19: presupuesto temporal de 30 s ====================


async def test_fdg_ca14_presupuesto_de_30_s_agotado_cae_a_fmp_y_la_tarea_llena_la_cache():
    gate = asyncio.Event()
    budget = Budget()
    ed = edgar(make_result(), gate=gate)
    svc, _ = servicio(ed, fmp(make_result("fmp")), sleep=budget.sleep)
    tarea = asyncio.ensure_future(svc.get("NVDA", quarters=12))
    await spin()
    assert not tarea.done() and budget.delays == [30.0]
    budget.released.set()
    res = await tarea
    assert res.source == "fmp" and res.fallback_codes == ("no_disponible_temporal",)
    gate.set()  # la descarga compartida sigue y termina despues
    await spin()
    asi_quedo = await svc.get("NVDA", quarters=12)
    assert asi_quedo.source == "sec_edgar" and len(ed.calls) == 1


async def test_fdg_ca14_dentro_del_presupuesto_no_se_pierde_el_resultado():
    gate = asyncio.Event()
    budget = Budget()
    svc, _ = servicio(edgar(make_result(), gate=gate), fmp(make_result("fmp")), sleep=budget.sleep)
    tarea = asyncio.ensure_future(svc.get("NVDA", quarters=12))
    await spin()
    gate.set()
    assert (await tarea).source == "sec_edgar"


async def test_fdg_ca14_el_presupuesto_corre_por_consulta_y_su_default_es_30_s():
    assert F.BUDGET_SECONDS == 30.0
    budget = Budget()
    svc = FundamentalsService([edgar(make_result())], sleep=budget.sleep)
    await svc.get("NVDA", quarters=12)
    assert budget.delays == [30.0]


# ============================ CA-33: log INFO unico ============================


async def test_fdg_ca33_un_unico_log_con_el_formato_exacto_para_edgar(caplog):
    caplog.set_level(logging.INFO)
    clock = Clock()
    svc, _ = servicio(edgar(make_result(), clock=clock, cost=1.5), fmp(make_result("fmp")), clock=clock)
    await svc.get("NVDA", quarters=12)
    assert _logs(caplog) == ["fundamentals ticker=NVDA source=sec_edgar fallback=- ms=1500"]
    assert [r.levelno for r in caplog.records if r.name == LOGGER] == [logging.INFO]


async def test_fdg_ca33_con_respaldo_el_log_lleva_los_codigos_y_es_uno_solo_no_uno_por_proveedor(caplog):
    caplog.set_level(logging.INFO)
    svc, _ = servicio(edgar(indisponible("sin_cik")), fmp(make_result("fmp")))
    await svc.get("ZZZZ", quarters=12)
    assert _logs(caplog) == ["fundamentals ticker=ZZZZ source=fmp fallback=sin_cik ms=0"]


async def test_fdg_ca33_codigos_multiples_separados_por_coma_y_sin_duplicados(caplog):
    caplog.set_level(logging.INFO)
    uno = edgar(indisponible("sin_cik"))
    dos = Fake("otro", indisponible("circuito_abierto"), cacheable=False)
    svc, _ = servicio(uno, dos, fmp(make_result("fmp", codes=("sin_cik",))))
    res = await svc.get("ZZZZ", quarters=12)
    assert res.fallback_codes == ("sin_cik", "circuito_abierto")
    assert _logs(caplog) == ["fundamentals ticker=ZZZZ source=fmp fallback=sin_cik,circuito_abierto ms=0"]


async def test_fdg_ca33_solo_fmp_loguea_fallback_guion_y_devuelve_el_mismo_objeto(caplog):
    caplog.set_level(logging.INFO)
    r = make_result("fmp")
    svc, _ = servicio(fmp(r))
    assert await svc.get("ADBE", quarters=12) is r
    assert _logs(caplog) == ["fundamentals ticker=ADBE source=fmp fallback=- ms=0"]


async def test_fdg_ca33_un_acierto_de_cache_tambien_emite_un_log(caplog):
    caplog.set_level(logging.INFO)
    svc, _ = servicio(edgar(make_result()), fmp(make_result("fmp")))
    await svc.get("NVDA", quarters=12)
    await svc.get("NVDA", quarters=12)
    assert len(_logs(caplog)) == 2


async def test_fdg_ca33_cache_antiguo_aparece_en_el_log(caplog):
    caplog.set_level(logging.INFO)
    svc, clock = servicio(
        edgar(make_result(), indisponible("no_disponible_temporal")), fmp(make_result("fmp"))
    )
    await svc.get("NVDA", quarters=12)
    clock.advance(7 * HORA)
    await svc.get("NVDA", quarters=12)
    assert _logs(caplog)[-1] == "fundamentals ticker=NVDA source=sec_edgar fallback=cache_antiguo ms=0"


async def test_fdg_ca33_sin_cuerpo_headers_ni_user_agent_y_ticker_hostil_no_inyecta_lineas(caplog):
    caplog.set_level(logging.DEBUG)
    ua = "CANARIO-UA ana@ejemplo.test"
    svc, _ = servicio(edgar(RuntimeError(f"User-Agent: {ua} cuerpo=<html>")), fmp(make_result("fmp")))
    await svc.get("AAPL\nfundamentals ticker=EVIL source=fmp fallback=- ms=0", quarters=12)
    assert _logs(caplog) == ["fundamentals ticker=? source=fmp fallback=no_disponible_temporal ms=0"]
    assert ua not in caplog.text and "<html>" not in caplog.text and "EVIL" not in caplog.text


async def test_fdg_ca33_si_todo_falla_se_relanza_la_excepcion_del_ultimo_y_no_hay_log(caplog):
    caplog.set_level(logging.INFO)
    final = RuntimeError("fallo de fmp")
    svc, _ = servicio(edgar(indisponible("sin_cik")), fmp(final))
    with pytest.raises(RuntimeError) as info:
        await svc.get("NVDA", quarters=12)
    assert info.value is final and _logs(caplog) == []


async def test_fdg_ca33_si_todos_declinan_sin_excepcion_devuelve_none_sin_log(caplog):
    caplog.set_level(logging.INFO)
    svc, _ = servicio(edgar(indisponible("sin_cik")), fmp(None))
    assert await svc.get("NVDA", quarters=12) is None and _logs(caplog) == []


async def test_fdg_ca33_el_servicio_pasa_ticker_ventana_y_skip_own_metrics_al_proveedor():
    fp = fmp(make_result("fmp"))
    svc, _ = servicio(fp)
    await svc.get("ADBE", quarters=20, skip_own_metrics=True)
    assert fp.calls == [("ADBE", 20, True)]


# ================== CS-24: enum cerrado en FundamentalsResult ===================

_ESPERADOS = {
    "no_us_gaap", "sin_usd", "datos_insuficientes", "desactualizado", "cik_no_coincide",
    "end_incoherente", "ttm_incoherente", "valor_atipico",
    "sin_cik", "sin_facts", "no_disponible_temporal", "circuito_abierto", "ua_no_configurado",
    "cache_antiguo", "respuesta_demasiado_grande", "redireccion_inesperada",
    "respuesta_inesperada", "bloqueado_por_sec",
}


def test_fdg_tcs24_el_enum_es_parse_codes_mas_los_codigos_de_servicio_y_nada_mas():
    assert FALLBACK_CODES == _ESPERADOS
    assert PARSE_CODES < FALLBACK_CODES
    assert F.SOURCES == {"sec_edgar", "fmp"}


@pytest.mark.parametrize("libre", ["texto libre", "", "SIN_CIK", "sin_cik ", "sin_cik\n", "valor_atipico2"])
def test_fdg_tcs24_un_codigo_libre_lanza_error_al_construir_el_resultado(libre):
    with pytest.raises(ValueError):
        make_result(codes=(libre,))
    with pytest.raises(ValueError):
        make_result(codes=("sin_cik", libre))


@pytest.mark.parametrize("raro", [None, 3, b"sin_cik", ["sin_cik"], ("sin_cik",)])
def test_fdg_tcs24_un_codigo_que_no_es_str_lanza_error(raro):
    with pytest.raises(ValueError):
        make_result(codes=(raro,))


@pytest.mark.parametrize("fuente", ["texto libre", "SEC_EDGAR", "edgar", "", None, 1])
def test_fdg_tcs24_una_fuente_libre_lanza_error_al_construir_el_resultado(fuente):
    with pytest.raises(ValueError):
        make_result(source=fuente)


@pytest.mark.parametrize("codigo", sorted(FALLBACK_CODES))
def test_fdg_tcs24_todo_codigo_del_enum_se_acepta(codigo):
    assert make_result(codes=(codigo,)).fallback_codes == (codigo,)
    assert ProviderUnavailable(codigo).code == codigo


@pytest.mark.parametrize("fuente", ["sec_edgar", "fmp"])
def test_fdg_tcs24_las_dos_fuentes_validas(fuente):
    assert make_result(source=fuente).source == fuente


def test_fdg_tcs24_replace_revalida_y_una_lista_se_normaliza_a_tupla():
    r = make_result()
    with pytest.raises(ValueError):
        dataclasses.replace(r, fallback_codes=("libre",))
    assert make_result(codes=["sin_cik", "sin_facts"]).fallback_codes == ("sin_cik", "sin_facts")


def test_fdg_tcs24_provider_unavailable_con_codigo_libre_lanza_error():
    for malo in ("libre", "", None, 3):
        with pytest.raises(ValueError):
            ProviderUnavailable(malo)


def test_fdg_tcs24_edgar_xbrl_no_importa_fundamentals():
    codigo = (
        "import sys; sys.path.insert(0, 'src'); import investbot.edgar_xbrl; "
        "assert 'investbot.fundamentals' not in sys.modules"
    )
    r = subprocess.run([sys.executable, "-I", "-c", codigo], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_fdg_tcs24_las_propiedades_siguen_los_flags_de_fuente():
    r = dataclasses.replace(make_result(), income_fuente="anual_fallback", income_a=[{"a": 1}])
    assert r.income_statements == [{"a": 1}] and r.cash_flows == r.cash_q and r.balance_sheets == r.balance_q
    anual = dataclasses.replace(r, cash_fuente="anual_fallback", balance_fuente="anual_fallback")
    assert anual.cash_flows == anual.cash_a == [] and anual.balance_sheets == anual.balance_a == []


# ====================== CS-12: AST sin pickle ni terceros =======================

_PROHIBIDOS = {"pickle", "cPickle", "shelve", "sqlite3", "marshal", "dbm", "dill", "joblib"}
_TERCEROS_OK = {"httpx"}  # ya estaba en requirements: ningun import de terceros nuevo
MODULOS_F1B3 = ("fundamentals.py", "edgar_provider.py", "fmp_provider.py", "edgar_xbrl.py")


def _imports_de(fuente: str) -> set[str]:
    raiz: set[str] = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Import):
            raiz |= {a.name.split(".")[0] for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            raiz.add(nodo.module.split(".")[0])
    return raiz


def _violaciones(fuente: str) -> set[str]:
    imports = _imports_de(fuente)
    permitidos = set(sys.stdlib_module_names) | _TERCEROS_OK | {"investbot"}
    return (imports & _PROHIBIDOS) | (imports - permitidos)


@pytest.mark.parametrize("modulo", MODULOS_F1B3)
def test_fdg_tcs12_los_modulos_nuevos_no_usan_pickle_shelve_sqlite3_ni_terceros_nuevos(modulo):
    assert _violaciones((SRC / modulo).read_text(encoding="utf-8")) == set()


def test_fdg_tcs12_el_detector_ve_pickle_sqlite3_y_terceros_control_positivo():
    assert _violaciones("import pickle\nfrom sqlite3 import connect\nimport requests\nimport os.path") == {
        "pickle", "sqlite3", "requests",
    }
    assert _violaciones("import httpx\nimport asyncio\nfrom investbot import rules") == set()


def test_fdg_tcs12_la_cache_vive_en_memoria_y_no_hay_escritura_a_disco():
    for modulo in ("fundamentals.py", "edgar_provider.py"):
        fuente = (SRC / modulo).read_text(encoding="utf-8")
        assert "open(" not in fuente and "write_text" not in fuente and "tempfile" not in fuente
