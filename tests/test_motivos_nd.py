"""Tests de SDD_motivos_nd_y_empresas_en_perdidas.md (motivos concretos de N/D).

Nombres `test_nd_ca<n>_*` / `test_nd_cs<n>_*` (regex `^test_nd_(ca|cs)(\\d+)(?=_|$)`).
F1 (valuation.py): CA-ND-1..4. F2 (summary.py): CA-ND-5..8, CS-ND-3, 10, 11, 15.
F3 (Desglose y "Ver dato"): CA-ND-9, 10, 15, CS-ND-18. F4a (helpers puros y
tablas, sin cablear al handler): CA-ND-11, 13 parcial, CS-ND-3 (tablas ND_*),
5, 10, 13, 16 parcial, M-20. F4b (handler N/D, P-3): CA-ND-12, 14, 16, CS-ND-1,
4, 8, 9, 12, 14, 16, 17.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import itertools
import json
import logging
import math
from pathlib import Path

import httpx
import pytest
from telegram.error import TelegramError

from investbot import (
    ai_explain,
    ai_explain_content,
    ai_rewrite,
    peers,
    query_handler,
    summary,
    valuation,
)

from .gemini_helpers import gemini_response
from .test_ai_explain import (
    FakeRateLimiter,
    _avanzado_context,
    _build_callback,
    _client_with_handler,
    _enabled_config,
    _fake_callback_update,
    _make_clients,
    _texto_libre_context,
)

# ---------------------------------------------------------------------------
# F1 -- valuation.py (CA-ND-1..4)
# ---------------------------------------------------------------------------

# Copia VERBATIM del cuerpo de `calculate_wacc` de HEAD 9b81d3b (solo cambia el
# nombre y el prefijo `valuation.` de las 2 constantes de modulo).
def _wacc_referencia_head(
    *,
    interest_expense: float,
    total_debt: float,
    income_tax_expense: float,
    income_before_tax: float,
    beta: float,
    y: float,
    market_cap: float,
    market_risk_premium: float = valuation.MARKET_RISK_PREMIUM,
) -> Optional[float]:
    """WACC simplificado (Decisión de diseño #10). Devuelve fracción decimal.

    - Ke (CAPM simplificado) = Y + beta × prima_de_riesgo_de_mercado.
    - Kd = Gastos por intereses / Deuda total, ajustado por la tasa
      impositiva efectiva: Kd × (1 - tasa_impositiva_efectiva).
    - Ponderación por deuda total vs market cap.
    - `WACC = (E/V × Ke) + (D/V × Kd × (1 - t))`.

    Devuelve `None` (sin lanzar excepción) si los datos de entrada no
    permiten un cálculo con sentido (deuda total y market cap ambos <= 0, o
    `income_before_tax <= 0`, que haría la tasa impositiva efectiva
    indefinida).
    """
    if market_cap is None or market_cap <= 0:
        return None
    if total_debt is None:
        # Deuda desconocida (4.10 pt 3, D-20): N/D, nunca "sin deuda". Con
        # `or 0.0` el WACC colapsaria a Ke con una deuda inventada.
        return None
    if total_debt < 0:
        return None

    v = market_cap + total_debt
    if v <= 0:  # pragma: no cover - inalcanzable (market_cap>0 y total_debt>=0 ya garantizados arriba)
        return None

    ke = y + beta * market_risk_premium

    if total_debt == 0:
        # Sin deuda: WACC colapsa a Ke (no hay componente de deuda que ponderar).
        return ke

    if interest_expense is None:
        # Con deuda > 0 e intereses ausentes (AAPL en EDGAR) no hay costo de
        # deuda: Kd = 0 daria un WACC artificialmente bajo y un DCF inflado.
        return None

    if income_before_tax is None or income_before_tax <= 0:
        # Tasa impositiva efectiva indefinida con utilidad antes de impuestos <= 0.
        # Se usa Kd pre-impuesto como aproximación conservadora en vez de crashear.
        tax_rate = 0.0
    else:
        tax_rate = income_tax_expense / income_before_tax

    kd_pretax = interest_expense / total_debt
    if not math.isfinite(kd_pretax) or kd_pretax > valuation.KD_PRETAX_MAXIMO:
        # CS-28: un costo de deuda absurdo (dato corrupto o intereses de otra
        # base) no se promedia: WACC N/D (`wacc_no_calculable`). No cambia fórmulas.
        return None
    kd_aftertax = kd_pretax * (1 - tax_rate)

    e_weight = market_cap / v
    d_weight = total_debt / v

    return e_weight * ke + d_weight * kd_aftertax


_NAN = float("nan")
_INF = float("inf")
_MC = [None, -1.0, 0.0, 1e9, _NAN, _INF]
_DEBT = [None, -5.0, 0.0, 5e8, _NAN, _INF]
_INTEREST = [None, -1e6, 0.0, 1e7, 3e8, _NAN, _INF]
_TAX = [None, 0.0, 2e6, -1e6]
_IBT = [None, -1e6, 0.0, 1e7]


def _grilla():
    for mc, d, i, tx, ibt in itertools.product(_MC, _DEBT, _INTEREST, _TAX, _IBT):
        yield dict(
            market_cap=mc,
            total_debt=d,
            interest_expense=i,
            income_tax_expense=tx,
            income_before_tax=ibt,
            beta=1.0,
            y=0.04,
        )


def _run(fn, kw):
    try:
        return ("ok", fn(**kw))
    except Exception as exc:  # noqa: BLE001 -- comparamos el tipo de excepcion
        return ("exc", type(exc))


def _igual(a, b):
    if a is None or b is None:
        return a is b
    if math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _detalle_esperado(kw):
    """Oraculo INDEPENDIENTE del detalle (prioridad de 14.1 B-1). `None` = se
    espera un valor (sin detalle). Solo se invoca si la referencia no lanza."""
    mc, d, i = kw["market_cap"], kw["total_debt"], kw["interest_expense"]
    if mc is None or mc <= 0:
        return "wacc_capitalizacion_no_disponible"
    if d is None:
        return "wacc_deuda_no_disponible"
    if d < 0:
        return "wacc_deuda_invalida"
    if d == 0:
        return None
    if i is None:
        return "wacc_intereses_no_disponibles"
    kd = i / d
    if not math.isfinite(kd) or kd > valuation.KD_PRETAX_MAXIMO:
        return "wacc_costo_deuda_fuera_de_rango"
    return None


def test_nd_ca1_paridad_wacc_vs_referencia_head():
    n = 0
    n_exc = 0
    for kw in _grilla():
        n += 1
        ref = _run(_wacc_referencia_head, kw)
        pub = _run(valuation.calculate_wacc, kw)
        nuevo = _run(valuation._wacc_con_detalle, kw)
        if ref[0] == "exc":
            n_exc += 1
            assert pub == ref, kw
            assert nuevo == ref, kw
            continue
        assert pub[0] == "ok" and nuevo[0] == "ok", kw
        assert _igual(pub[1], ref[1]), kw
        assert _igual(nuevo[1][0], ref[1]), kw
    assert n == 4032
    assert n_exc == 54


def test_nd_ca1_oraculo_independiente_del_detalle():
    vistos = set()
    for kw in _grilla():
        if _run(_wacc_referencia_head, kw)[0] == "exc":
            continue
        wacc, detalle = valuation._wacc_con_detalle(**kw)
        esperado = _detalle_esperado(kw)
        assert detalle == esperado, kw
        if esperado is None:
            assert wacc is not None  # nan cuenta como valor (HEAD no lo vuelve None)
        else:
            assert wacc is None
        vistos.add(detalle)
    assert vistos == set(valuation.DETALLES_VALIDOS) - {
        "eps_no_disponible",
        "wacc_tasa_bono_no_disponible",
    } | {None}


def test_nd_ca1_calculate_wacc_sigue_publica_y_mantiene_firma():
    import inspect

    firma = inspect.signature(valuation.calculate_wacc)
    assert list(firma.parameters) == [
        "interest_expense",
        "total_debt",
        "income_tax_expense",
        "income_before_tax",
        "beta",
        "y",
        "market_cap",
        "market_risk_premium",
    ]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in firma.parameters.values())
    assert firma.parameters["market_risk_premium"].default == valuation.MARKET_RISK_PREMIUM
    kw = dict(
        interest_expense=1e7,
        total_debt=5e8,
        income_tax_expense=2e6,
        income_before_tax=1e7,
        beta=1.0,
        y=0.04,
        market_cap=1e9,
    )
    assert valuation.calculate_wacc(**kw) == pytest.approx(_wacc_referencia_head(**kw))
    assert valuation.calculate_wacc(**kw, market_risk_premium=0.08) == pytest.approx(
        _wacc_referencia_head(**kw, market_risk_premium=0.08)
    )


def test_nd_ca2_todo_none_lleva_detalle_valido_y_ningun_valor_lo_lleva():
    for kw in _grilla():
        if _run(_wacc_referencia_head, kw)[0] == "exc":
            continue
        wacc, detalle = valuation._wacc_con_detalle(**kw)
        if wacc is None:
            assert detalle in valuation.DETALLES_VALIDOS, kw
        else:
            assert detalle is None, kw


def test_nd_ca2_detalle_por_rama():
    base = dict(
        interest_expense=1e7,
        total_debt=5e8,
        income_tax_expense=2e6,
        income_before_tax=1e7,
        beta=1.0,
        y=0.04,
        market_cap=1e9,
    )
    casos = [
        (dict(market_cap=None), "wacc_capitalizacion_no_disponible"),
        (dict(market_cap=0.0), "wacc_capitalizacion_no_disponible"),
        (dict(market_cap=-1.0), "wacc_capitalizacion_no_disponible"),
        (dict(total_debt=None), "wacc_deuda_no_disponible"),
        (dict(total_debt=-5.0), "wacc_deuda_invalida"),
        (dict(interest_expense=None), "wacc_intereses_no_disponibles"),
        (dict(interest_expense=3e8), "wacc_costo_deuda_fuera_de_rango"),
        (dict(interest_expense=_NAN), "wacc_costo_deuda_fuera_de_rango"),
    ]
    for cambio, esperado in casos:
        wacc, detalle = valuation._wacc_con_detalle(**{**base, **cambio})
        assert wacc is None and detalle == esperado, cambio
    # sin deuda: valor (ke) y sin detalle, aunque falten intereses
    wacc, detalle = valuation._wacc_con_detalle(**{**base, "total_debt": 0.0, "interest_expense": None})
    assert wacc == pytest.approx(0.04 + 1.0 * valuation.MARKET_RISK_PREMIUM) and detalle is None


def test_nd_ca2_utilidad_antes_de_impuestos_no_positiva_no_es_motivo():
    base = dict(
        interest_expense=1e7,
        total_debt=5e8,
        income_tax_expense=2e6,
        beta=1.0,
        y=0.04,
        market_cap=1e9,
    )
    for ibt in (None, 0.0, -1e6):
        wacc, detalle = valuation._wacc_con_detalle(**base, income_before_tax=ibt)
        assert wacc is not None and detalle is None
        # Kd antes de impuestos (tax_rate = 0.0)
        kd = 1e7 / 5e8
        v = 1e9 + 5e8
        esperado = (1e9 / v) * (0.04 + valuation.MARKET_RISK_PREMIUM) + (5e8 / v) * kd
        assert wacc == pytest.approx(esperado)


def test_nd_ca2_nan_e_inf_que_head_no_convierte_en_none_no_llevan_detalle():
    base = dict(
        interest_expense=1e7,
        total_debt=5e8,
        income_tax_expense=2e6,
        income_before_tax=1e7,
        beta=1.0,
        y=0.04,
        market_cap=1e9,
    )
    for cambio in (dict(market_cap=_NAN), dict(market_cap=_INF), dict(total_debt=_INF, interest_expense=1e7)):
        kw = {**base, **cambio}
        ref = _wacc_referencia_head(**kw)
        wacc, detalle = valuation._wacc_con_detalle(**kw)
        assert detalle is None
        assert _igual(wacc, ref)


def test_nd_ca2_excepciones_de_head_se_conservan():
    # income_tax_expense None con utilidad > 0 y deuda > 0: TypeError en HEAD,
    # ANTES del chequeo de Kd (el refactor no la corrige).
    kw = dict(
        interest_expense=3e8,  # Kd fuera de rango: HEAD igual lanza primero
        total_debt=5e8,
        income_tax_expense=None,
        income_before_tax=1e7,
        beta=1.0,
        y=0.04,
        market_cap=1e9,
    )
    with pytest.raises(TypeError):
        _wacc_referencia_head(**kw)
    with pytest.raises(TypeError):
        valuation.calculate_wacc(**kw)
    with pytest.raises(TypeError):
        valuation._wacc_con_detalle(**kw)


# --- compute_valuation / compute_valuation_scenarios ---------------------------

_WACC_OK = dict(
    interest_expense=1e7,
    total_debt=5e8,
    income_tax_expense=2e6,
    income_before_tax=1e7,
    beta=1.0,
    market_cap=1e9,
)
_HIST = [100.0, 110.0, 121.0, 133.0, 146.0, 160.0]
_PEERS = peers.PeerAverageResult(
    per_promedio=15.0, per_minimo=10.0, per_maximo=20.0, peers_usados=["AAA", "BBB", "CCC"]
)


def _compute_ambos(*, eps_ttm=5.0, y=0.04, wacc=None):
    wacc_inputs = {**_WACC_OK, **(wacc or {})}
    comun = dict(
        eps_ttm=eps_ttm,
        eps_historial=_HIST,
        fcf_historial=_HIST,
        y=y,
        wacc_inputs=wacc_inputs,
        shares_outstanding=1e8,
    )
    directo = valuation.compute_valuation(
        per_promedio_peers=_PEERS.per_promedio, n_peers_validos=3, **comun
    )
    escenarios = valuation.compute_valuation_scenarios(peer_average=_PEERS, **comun)
    return directo, escenarios


_CAUSAS_WACC = [
    ("wacc_capitalizacion_no_disponible", dict(y=0.04, wacc=dict(market_cap=None))),
    ("wacc_deuda_no_disponible", dict(y=0.04, wacc=dict(total_debt=None))),
    ("wacc_deuda_invalida", dict(y=0.04, wacc=dict(total_debt=-5.0))),
    ("wacc_intereses_no_disponibles", dict(y=0.04, wacc=dict(interest_expense=None))),
    ("wacc_costo_deuda_fuera_de_rango", dict(y=0.04, wacc=dict(interest_expense=3e8))),
    ("wacc_tasa_bono_no_disponible", dict(y=None)),
]


def _dcf(excluidos):
    return [m for m in excluidos if m.modelo == "dcf"]


@pytest.mark.parametrize("detalle,kwargs", _CAUSAS_WACC, ids=[c[0] for c in _CAUSAS_WACC])
def test_nd_ca3_paridad_de_detalle_en_las_6_causas_de_wacc(detalle, kwargs):
    directo, escenarios = _compute_ambos(**kwargs)
    (d,) = _dcf(directo.modelos_excluidos)
    assert d.motivo == "wacc_no_calculable"
    assert d.detalle == detalle
    assert detalle in valuation.DETALLES_VALIDOS
    assert escenarios.conservador.modelos_excluidos == directo.modelos_excluidos
    (c,) = _dcf(escenarios.conservador.modelos_excluidos)
    assert (c.motivo, c.detalle) == (d.motivo, d.detalle)
    (b,) = _dcf(escenarios.modelos_excluidos_base)
    assert (b.motivo, b.detalle) == (d.motivo, d.detalle)
    # as_dict: el detalle viaja en los 3 dicts
    assert {"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": detalle} in directo.as_dict()["modelos_excluidos"]
    sd = escenarios.as_dict()
    assert {"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": detalle} in sd["conservador"]["modelos_excluidos"]
    assert {"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": detalle} in sd["modelos_excluidos_base"]


def test_nd_ca3_y_none_tiene_precedencia_aun_con_otros_datos_faltantes():
    directo, escenarios = _compute_ambos(y=None, wacc=dict(market_cap=None, total_debt=None))
    for excl in (directo.modelos_excluidos, escenarios.conservador.modelos_excluidos, escenarios.modelos_excluidos_base):
        (d,) = _dcf(excl)
        assert d.detalle == "wacc_tasa_bono_no_disponible"


def test_nd_ca3_eps_none_lleva_detalle_y_eps_no_positivo_no():
    directo, escenarios = _compute_ambos(eps_ttm=None)
    for excl in (directo.modelos_excluidos, escenarios.conservador.modelos_excluidos, escenarios.modelos_excluidos_base):
        for modelo in ("multiplos", "graham"):
            (m,) = [x for x in excl if x.modelo == modelo]
            assert m.motivo == "eps_ttm_no_positivo"
            assert m.detalle == "eps_no_disponible"
    assert escenarios.conservador.modelos_excluidos == directo.modelos_excluidos
    for eps in (-1.17, 0.0):
        directo, escenarios = _compute_ambos(eps_ttm=eps)
        for excl in (directo.modelos_excluidos, escenarios.conservador.modelos_excluidos, escenarios.modelos_excluidos_base):
            for modelo in ("multiplos", "graham"):
                (m,) = [x for x in excl if x.modelo == modelo]
                assert m.motivo == "eps_ttm_no_positivo"
                assert m.detalle is None
        assert escenarios.conservador.modelos_excluidos == directo.modelos_excluidos


def test_nd_ca3_as_dict_omite_detalle_none_tambien_en_base():
    directo, escenarios = _compute_ambos(eps_ttm=-1.17)
    for lista in (
        directo.as_dict()["modelos_excluidos"],
        escenarios.as_dict()["conservador"]["modelos_excluidos"],
        escenarios.as_dict()["modelos_excluidos_base"],
    ):
        assert {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"} in lista
        assert all("detalle" not in item for item in lista)
    directo, escenarios = _compute_ambos(eps_ttm=None)
    assert {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo", "detalle": "eps_no_disponible"} in (
        escenarios.as_dict()["modelos_excluidos_base"]
    )
    # sin ninguna exclusion con detalle, los dicts quedan con solo 2 claves
    m = valuation.ModeloExcluido("graham", "y_no_disponible")
    assert m.detalle is None
    assert valuation._excluido_dict(m) == {"modelo": "graham", "motivo": "y_no_disponible"}


def test_nd_ca3_modelo_excluido_detalle_default_none_y_comparable():
    assert valuation.ModeloExcluido("a", "b") == valuation.ModeloExcluido("a", "b", None)
    assert valuation.ModeloExcluido("a", "b") != valuation.ModeloExcluido("a", "b", "eps_no_disponible")


def test_nd_ca3_valor_calculable_sin_excluidos():
    directo, escenarios = _compute_ambos()
    assert directo.modelos_excluidos == []
    assert escenarios.modelos_excluidos_base == []
    assert directo.valor_justo_dcf is not None
    assert escenarios.conservador.valor_justo_dcf == directo.valor_justo_dcf


# --- CA-ND-4: motivos emitidos (derivados por AST) -------------------------------


def _motivos_emitidos_por_ast():
    src = Path(valuation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    motivos = set()
    for nodo in ast.walk(tree):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "ModeloExcluido"
            and len(nodo.args) >= 2
            and isinstance(nodo.args[1], ast.Constant)
            and isinstance(nodo.args[1].value, str)
        ):
            motivos.add(nodo.args[1].value)
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "_motivo_cagr_invalido":
            for sub in ast.walk(nodo):
                if not isinstance(sub, ast.Return):
                    continue
                v = sub.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    motivos.add(v.value)
                elif isinstance(v, ast.JoinedStr):
                    for prefijo in ("eps", "fcf"):
                        motivos.add(
                            "".join(
                                p.value
                                if isinstance(p, ast.Constant)
                                else prefijo
                                for p in v.values
                            )
                        )
    return motivos


def test_nd_ca4_set_de_motivos_emitidos_derivado_por_ast():
    motivos = _motivos_emitidos_por_ast()
    assert len(motivos) == 14
    assert {
        "eps_ttm_no_positivo",
        "peers_validos_insuficientes",
        "per_peers_no_disponible",
        "historial_insuficiente",
        "eps_base_no_positivo",
        "eps_reciente_no_positivo",
        "eps_cagr_no_calculable",
        "fcf_base_no_positivo",
        "fcf_reciente_no_positivo",
        "fcf_cagr_no_calculable",
        "y_no_disponible",
        "graham_multiplicador_no_positivo",
        "wacc_no_calculable",
        "dcf_no_calculable",
    } == motivos


def test_nd_ca4_todo_motivo_emitido_resuelve_a_texto_via_motivo_texto():
    """Completa CA-ND-4 (F2): cada motivo emitido por `valuation.py` (set derivado
    por AST, no hardcodeado) resuelve a texto. Excepcion unica:
    `peers_validos_insuficientes` (la seccion completa usa un texto dinamico
    propio; el teaser usa la etiqueta estatica, que tambien existe)."""
    excepciones = {"peers_validos_insuficientes"}
    for motivo in sorted(_motivos_emitidos_por_ast() - excepciones):
        texto = summary.motivo_texto(motivo)
        assert isinstance(texto, str) and texto, motivo
    assert isinstance(summary.motivo_texto("peers_validos_insuficientes"), str)


# ---------------------------------------------------------------------------
# F2 -- summary.py (CA-ND-5..8, CS-ND-3, 10, 11, 15)
# ---------------------------------------------------------------------------

_MOTIVO_LABELS_HEAD = {
    "eps_ttm_no_positivo": "la empresa tiene EPS (ganancia por acción) negativo o cero",
    "eps_base_no_positivo": "al inicio del historial disponible la empresa tenía pérdidas, así que no se puede calcular un crecimiento histórico confiable",
    "eps_reciente_no_positivo": "en el período más reciente disponible la empresa tuvo pérdidas",
    "fcf_base_no_positivo": "al inicio del historial disponible el flujo de caja libre era negativo",
    "fcf_reciente_no_positivo": "el flujo de caja libre del período más reciente disponible es negativo",
    "historial_insuficiente": "no hay suficiente historial financiero (se necesitan al menos ~2 años de historia, sea en reportes anuales o trimestrales)",
    "y_no_disponible": "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov)",
    "wacc_no_calculable": "no se pudo estimar el costo de capital (WACC) con los datos disponibles",
    "dcf_no_calculable": "no se pudo proyectar el flujo de caja con los datos disponibles",
    "per_peers_no_disponible": "no pude obtener el PER de los comparables del sector",
    "graham_multiplicador_no_positivo": "en este escenario el crecimiento estimado haría el múltiplo de Graham cero o negativo",
    "peers_validos_insuficientes": (
        "no hay suficientes comparables (peers) con PER válido esta consulta "
        "para un promedio confiable"
    ),
}

_DETALLE_LABELS_15_5 = {
    "eps_no_disponible": "no pude obtener la ganancia por acción (EPS) de la empresa",
    "wacc_capitalizacion_no_disponible": "no pude obtener el valor de mercado de la empresa, necesario para estimar el costo de capital (WACC)",
    "wacc_deuda_no_disponible": "no pude obtener la deuda total de la empresa, necesaria para estimar el costo de capital (WACC)",
    "wacc_deuda_invalida": "el dato de deuda total de la empresa es inválido (negativo), así que no se puede estimar el costo de capital (WACC)",
    "wacc_intereses_no_disponibles": "la empresa tiene deuda pero no pude obtener cuánto paga de intereses, necesario para estimar el costo de capital (WACC)",
    "wacc_costo_deuda_fuera_de_rango": "el costo de la deuda que sale de los datos está fuera de lo razonable (puede ser un dato erróneo o una deuda muy cara), así que no se usa para estimar el costo de capital (WACC)",
    "wacc_tasa_bono_no_disponible": "no pude obtener la tasa del bono del tesoro (FRED/Treasury.gov), necesaria para estimar el costo de capital (WACC)",
}

_EXTRA_15_5 = {
    "eps_cagr_no_calculable": "no se pudo calcular el crecimiento histórico de la ganancia con los datos disponibles",
    "fcf_cagr_no_calculable": "no se pudo calcular el crecimiento histórico del flujo de caja libre con los datos disponibles",
}

_TODOS_LOS_TEXTOS = (
    list(_MOTIVO_LABELS_HEAD.values())
    + list(_DETALLE_LABELS_15_5.values())
    + list(_EXTRA_15_5.values())
)


def _escenarios_dict(*, eps_ttm=None, y=0.04, wacc=None):
    _, esc = _compute_ambos(eps_ttm=eps_ttm, y=y, wacc=wacc)
    return esc.as_dict()


def _seccion(scenarios, escenario_elegido=None):
    return summary.build_valuation_scenarios_section(
        scenarios, precio_actual=10.0, n_peers_validos=3, escenario_elegido=escenario_elegido
    )


def _escenarios_manual(excluidos, *, total=None, base=None):
    esc = {
        "valor_justo_multiplos": None,
        "valor_justo_graham": None,
        "valor_justo_dcf": None,
        "valor_justo_total": total,
        "modelos_excluidos": excluidos,
    }
    return {
        "pesimista": dict(esc),
        "conservador": dict(esc),
        "optimista": dict(esc),
        "modelos_excluidos_base": excluidos if base is None else base,
    }


def test_nd_ca5_motivo_labels_byte_a_byte_igual_a_head():
    assert summary.MOTIVO_LABELS == _MOTIVO_LABELS_HEAD
    assert list(summary.MOTIVO_LABELS) == list(_MOTIVO_LABELS_HEAD)
    assert not (set(summary.MOTIVO_LABELS) & set(summary.MOTIVO_LABELS_EXTRA))


def test_nd_ca5_etiquetas_nuevas_son_las_exactas_de_15_5():
    assert summary.DETALLE_LABELS == _DETALLE_LABELS_15_5
    assert summary.MOTIVO_LABELS_EXTRA == _EXTRA_15_5
    assert summary.MOTIVO_GENERICO == "no pude identificar el motivo exacto con los datos de esta consulta"
    assert summary.MODELO_LABELS_RESUMEN == {"multiplos": "Múltiplos", "graham": "Graham", "dcf": "DCF"}
    assert set(summary.DETALLE_LABELS) == set(valuation.DETALLES_VALIDOS)
    for texto in _TODOS_LOS_TEXTOS + [summary.MOTIVO_GENERICO]:
        assert not texto.endswith(".")


def test_nd_ca5_motivo_texto_prioridad_detalle_y_fallbacks():
    for detalle, texto in _DETALLE_LABELS_15_5.items():
        assert summary.motivo_texto("wacc_no_calculable", detalle) == texto
        assert summary.motivo_texto("eps_ttm_no_positivo", detalle) == texto
    assert summary.motivo_texto("wacc_no_calculable") == _MOTIVO_LABELS_HEAD["wacc_no_calculable"]
    assert summary.motivo_texto("wacc_no_calculable", None) == _MOTIVO_LABELS_HEAD["wacc_no_calculable"]
    assert summary.motivo_texto("eps_ttm_no_positivo", "no_existe") == _MOTIVO_LABELS_HEAD["eps_ttm_no_positivo"]
    for motivo, texto in _EXTRA_15_5.items():
        assert summary.motivo_texto(motivo) == texto
    assert summary.motivo_texto("motivo_inventado") is None
    assert summary.motivo_texto(None) is None
    assert summary.motivo_texto("motivo_inventado", "wacc_deuda_invalida") == _DETALLE_LABELS_15_5["wacc_deuda_invalida"]


def test_nd_ca6_seccion_usa_el_texto_del_detalle():
    scen = _escenarios_dict(y=0.04, wacc=dict(total_debt=None))
    texto = _seccion(scen)
    assert f"- el modelo DCF no se pudo calcular: {_DETALLE_LABELS_15_5['wacc_deuda_no_disponible']}." in texto
    assert _MOTIVO_LABELS_HEAD["wacc_no_calculable"] not in texto
    scen = _escenarios_dict(eps_ttm=None)
    texto = _seccion(scen)
    assert f"- el modelo de Múltiplos no se pudo calcular: {_DETALLE_LABELS_15_5['eps_no_disponible']}." in texto
    assert f"- el modelo Graham (EPS Model) no se pudo calcular: {_DETALLE_LABELS_15_5['eps_no_disponible']}." in texto


def test_nd_ca6_seccion_sin_detalle_conserva_el_texto_de_head():
    scen = _escenarios_dict(eps_ttm=-1.17, y=0.04, wacc=dict(total_debt=None))
    for lista in (scen["modelos_excluidos_base"], scen["conservador"]["modelos_excluidos"]):
        for item in lista:
            if item["modelo"] != "dcf":
                assert "detalle" not in item
    scen["modelos_excluidos_base"] = [
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
        {"modelo": "dcf", "motivo": "wacc_no_calculable"},
    ]
    texto = _seccion(scen)
    assert f"- el modelo de Múltiplos no se pudo calcular: {_MOTIVO_LABELS_HEAD['eps_ttm_no_positivo']}." in texto
    assert f"- el modelo DCF no se pudo calcular: {_MOTIVO_LABELS_HEAD['wacc_no_calculable']}." in texto


def test_nd_ca6_seccion_motivo_o_modelo_desconocido_va_a_genericos_sin_clave_cruda():
    scen = _escenarios_manual(
        [
            {"modelo": "multiplos", "motivo": "motivo_raro_con_guion_bajo"},
            {"modelo": "modelo_raro_x", "motivo": "wacc_no_calculable"},
            {"modelo": "graham", "motivo": "eps_cagr_no_calculable"},
        ]
    )
    texto = _seccion(scen)
    assert f"- el modelo de Múltiplos no se pudo calcular: {summary.MOTIVO_GENERICO}." in texto
    assert f"- uno de los modelos no se pudo calcular: {_MOTIVO_LABELS_HEAD['wacc_no_calculable']}." in texto
    assert f"- el modelo Graham (EPS Model) no se pudo calcular: {_EXTRA_15_5['eps_cagr_no_calculable']}." in texto
    assert "motivo_raro" not in texto and "modelo_raro" not in texto and "eps_cagr" not in texto


def test_nd_ca6_seccion_peers_insuficientes_sigue_dinamico():
    scen = _escenarios_manual([{"modelo": "multiplos", "motivo": "peers_validos_insuficientes"}])
    texto = _seccion(scen)
    assert "(mínimo 2, hubo 3)" in texto


def test_nd_ca7_teaser_con_total_none_agrega_por_que_en_la_misma_linea():
    excluidos = [
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
        {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
        {"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"},
    ]
    scen = {"conservador": {"valor_justo_total": None, "modelos_excluidos": excluidos}}
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", 150.0)
    base = (
        "Valor Justo Total (Conservador): no calculable con los datos "
        "disponibles esta consulta — tu precio actual: $150.00."
    )
    assert linea == (
        base
        + f" Por qué: Múltiplos y Graham: {_MOTIVO_LABELS_HEAD['eps_ttm_no_positivo']}; "
        f"DCF: {_MOTIVO_LABELS_HEAD['fcf_reciente_no_positivo']}."
    )
    assert "\n" not in linea


def test_nd_ca7_teaser_sin_excluidos_conocidos_es_identico_a_head():
    base = (
        "Valor Justo Total (Conservador): no calculable con los datos "
        "disponibles esta consulta — tu precio actual: $150.00."
    )
    casos = [
        {"conservador": {"valor_justo_total": None}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": []}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": "texto"}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": None}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": [1, "x", None]}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": [{"modelo": "dcf", "motivo": "zzz"}]}},
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": {"modelo": "dcf"}}},
    ]
    for scen in casos:
        assert summary.build_valor_justo_teaser_line(scen, "conservador", 150.0) == base, scen


def test_nd_ca7_teaser_usa_el_escenario_elegido_y_no_la_lista_base():
    scen = {
        "pesimista": {
            "valor_justo_total": None,
            "modelos_excluidos": [{"modelo": "dcf", "motivo": "dcf_no_calculable"}],
        },
        "conservador": {
            "valor_justo_total": None,
            "modelos_excluidos": [{"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": "wacc_deuda_invalida"}],
        },
        "modelos_excluidos_base": [{"modelo": "dcf", "motivo": "y_no_disponible"}],
    }
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", 150.0)
    assert linea.endswith(f" Por qué: DCF: {_DETALLE_LABELS_15_5['wacc_deuda_invalida']}.")
    linea = summary.build_valor_justo_teaser_line(scen, "pesimista", 150.0)
    assert linea.endswith(f" Por qué: DCF: {_MOTIVO_LABELS_HEAD['dcf_no_calculable']}.")


def test_nd_ca7_teaser_caso_feliz_identico_y_sin_por_que():
    scen = {"conservador": {"valor_justo_total": 182.40, "modelos_excluidos": [{"modelo": "dcf", "motivo": "wacc_no_calculable"}]}}
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", 150.0)
    assert linea == "Valor Justo Total (Conservador): $182.40 — tu precio actual: $150.00."
    assert summary.build_valor_justo_teaser_line({}, "conservador", 150.0).endswith("$150.00.")


def test_nd_ca7_teaser_falla_en_lo_nuevo_cae_a_la_linea_de_head(monkeypatch):
    def _boom(_):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(summary, "build_motivos_resumen", _boom)
    scen = {"conservador": {"valor_justo_total": None, "modelos_excluidos": [{"modelo": "dcf", "motivo": "wacc_no_calculable"}]}}
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", 150.0)
    assert linea == (
        "Valor Justo Total (Conservador): no calculable con los datos "
        "disponibles esta consulta — tu precio actual: $150.00."
    )


def test_nd_ca8_resumen_vacio_desconocido_o_no_dicts_devuelve_none():
    assert summary.build_motivos_resumen([]) is None
    assert summary.build_motivos_resumen([{"modelo": "dcf", "motivo": "zzz"}]) is None
    assert summary.build_motivos_resumen([{"modelo": "dcf"}]) is None
    assert summary.build_motivos_resumen([1, "x", None, [], ("a",)]) is None
    assert summary.build_motivos_resumen([{"modelo": "otro", "motivo": "wacc_no_calculable"}]) is None
    assert summary.build_motivos_resumen(None) is None
    assert summary.build_motivos_resumen("abc") is None
    assert summary.build_motivos_resumen({"modelo": "dcf"}) is None


def test_nd_ca8_resumen_agrupa_por_texto_con_orden_fijo():
    t_eps = _MOTIVO_LABELS_HEAD["eps_ttm_no_positivo"]
    t_fcf = _MOTIVO_LABELS_HEAD["fcf_reciente_no_positivo"]
    # entrada en desorden: el orden de salida es multiplos -> graham -> dcf
    r = summary.build_motivos_resumen(
        [
            {"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
        ]
    )
    assert r == f"Múltiplos y Graham: {t_eps}; DCF: {t_fcf}"
    r = summary.build_motivos_resumen(
        [{"modelo": m, "motivo": "eps_ttm_no_positivo"} for m in ("dcf", "multiplos", "graham")]
    )
    assert r == f"Múltiplos, Graham y DCF: {t_eps}"
    r = summary.build_motivos_resumen(
        [
            {"modelo": "dcf", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "fcf_reciente_no_positivo"},
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
        ]
    )
    assert r == f"Múltiplos y DCF: {t_eps}; Graham: {t_fcf}"
    r = summary.build_motivos_resumen([{"modelo": "graham", "motivo": "eps_ttm_no_positivo", "detalle": "eps_no_disponible"}])
    assert r == f"Graham: {_DETALLE_LABELS_15_5['eps_no_disponible']}"
    assert not r.endswith(".")


def test_nd_ca8_resumen_modelo_repetido_usa_su_primera_entrada_y_salta_basura():
    r = summary.build_motivos_resumen(
        [
            "basura",
            {"modelo": ["x"], "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "dcf_no_calculable"},
            {"modelo": "dcf", "motivo": "wacc_no_calculable"},
            {"modelo": "otro_modelo", "motivo": "wacc_no_calculable"},
        ]
    )
    assert r == f"DCF: {_MOTIVO_LABELS_HEAD['dcf_no_calculable']}"
    assert "None" not in r


# --- CS-ND-3 ---------------------------------------------------------------------

_MD = "*_`["


def test_nd_cs3_labels_nuevos_sin_markdown_ni_digitos():
    textos = (
        list(summary.DETALLE_LABELS.values())
        + list(summary.MOTIVO_LABELS_EXTRA.values())
        + [summary.MOTIVO_GENERICO]
    )
    assert len(textos) == 7 + 2 + 1
    for texto in textos:
        assert not any(c in texto for c in "*_`[]"), texto
        assert not any(c.isdigit() for c in texto), texto


def test_nd_cs3_motivo_labels_sin_markdown_y_digitos_solo_en_historial_insuficiente():
    for clave, texto in summary.MOTIVO_LABELS.items():
        assert not any(c in texto for c in _MD), clave
        if clave != "historial_insuficiente":
            assert not any(c.isdigit() for c in texto), clave
    assert any(c.isdigit() for c in summary.MOTIVO_LABELS["historial_insuficiente"])


def test_nd_cs3_lineas_de_seccion_sin_markdown_por_motivo():
    for texto in _TODOS_LOS_TEXTOS:
        linea = f"- el modelo DCF no se pudo calcular: {texto}."
        assert not any(c in linea for c in _MD)


def _teaser_peor_caso():
    # los 3 textos MAS LARGOS y distintos entre si (un grupo por modelo)
    largos = sorted(set(_TODOS_LOS_TEXTOS), key=len, reverse=True)[:3]
    clave_de = {
        **{v: ("x", k, None) for k, v in _MOTIVO_LABELS_HEAD.items()},
        **{v: ("x", "wacc_no_calculable", k) for k, v in _DETALLE_LABELS_15_5.items()},
        **{v: ("x", k, None) for k, v in _EXTRA_15_5.items()},
    }
    excluidos = []
    for modelo, texto in zip(("multiplos", "graham", "dcf"), largos):
        _, motivo, detalle = clave_de[texto]
        item = {"modelo": modelo, "motivo": motivo}
        if detalle:
            item["detalle"] = detalle
        excluidos.append(item)
    scen = {"conservador": {"valor_justo_total": None, "modelos_excluidos": excluidos}}
    return scen


def test_nd_cs3_solo_el_teaser_con_por_que_da_exactamente_un_placeholder():
    scen = _teaser_peor_caso()
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", -1234.56)
    assert "Por qué:" in linea and "$-1,234.56" in linea
    _, line_map = ai_rewrite._classify_lines(linea)
    assert len(line_map) == 1
    assert not any(c in linea.replace("Por qué", "") for c in "`[")


def test_nd_cs3_lineas_de_motivo_de_la_seccion_solo_verifican_ausencia_de_markdown():
    scen = _escenarios_manual(
        [{"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": "wacc_costo_deuda_fuera_de_rango"}]
    )
    for linea in _seccion(scen).split("\n"):
        if "no se pudo calcular:" in linea:
            assert not any(c in linea for c in _MD)


# --- CS-ND-10 / CS-ND-11 -----------------------------------------------------------

_HOSTILES = [None, 0, 7, 3.5, [], ["a", "b"], {"a": 1}, ("t",), "motivo_desconocido_x", True, "", b"bytes"]


def _sin_crudo(texto):
    assert "None" not in texto
    for crudo in ("motivo_desconocido_x", "bytes", "['a'", "{'a'"):
        assert crudo not in texto


def test_nd_cs10_motivo_texto_nunca_lanza_con_motivo_o_detalle_hostil():
    for motivo in _HOSTILES:
        for detalle in _HOSTILES:
            r = summary.motivo_texto(motivo, detalle)
            assert r is None or r in _TODOS_LOS_TEXTOS


def test_nd_cs10_build_motivos_resumen_hostil_nunca_lanza_ni_muestra_crudo():
    for motivo in _HOSTILES:
        for detalle in _HOSTILES:
            for modelo in _HOSTILES + ["multiplos"]:
                r = summary.build_motivos_resumen([{"modelo": modelo, "motivo": motivo, "detalle": detalle}])
                if r is not None:
                    _sin_crudo(r)
    for excluidos in _HOSTILES:
        assert summary.build_motivos_resumen(excluidos) is None


def test_nd_cs10_seccion_completa_con_excluidos_hostiles_nunca_lanza():
    for motivo in _HOSTILES:
        for detalle in _HOSTILES:
            for modelo in _HOSTILES + ["dcf"]:
                item = {"modelo": modelo, "motivo": motivo, "detalle": detalle}
                texto = _seccion(_escenarios_manual([item]))
                _sin_crudo(texto)
    for base in _HOSTILES:
        texto = _seccion(_escenarios_manual([], base=base))
        _sin_crudo(texto)
    # lista con no-dicts mezclados con un item valido: el valido se muestra
    texto = _seccion(_escenarios_manual([], base=[1, None, "x", [], {"modelo": "dcf", "motivo": "dcf_no_calculable"}]))
    assert f"- el modelo DCF no se pudo calcular: {_MOTIVO_LABELS_HEAD['dcf_no_calculable']}." in texto


def test_nd_cs10_teaser_hostil_nunca_lanza_ni_muestra_crudo():
    for hostil in _HOSTILES:
        scen = {"conservador": {"valor_justo_total": None, "modelos_excluidos": hostil}}
        linea = summary.build_valor_justo_teaser_line(scen, "conservador", 150.0)
        _sin_crudo(linea)
        assert "Por qué" not in linea
        for motivo in _HOSTILES:
            scen = {"conservador": {"valor_justo_total": None, "modelos_excluidos": [{"modelo": "dcf", "motivo": motivo, "detalle": hostil}]}}
            _sin_crudo(summary.build_valor_justo_teaser_line(scen, "conservador", 150.0))


def test_nd_cs10_teaser_con_escenario_ausente_o_elegido_invalido_cae_a_head():
    base = (
        "no calculable con los datos disponibles esta consulta — tu precio actual: $150.00."
    )
    assert summary.build_valor_justo_teaser_line({"conservador": {}}, "pesimista", 150.0).endswith(base)
    assert summary.build_valor_justo_teaser_line({"conservador": None}, "conservador", 150.0).endswith(base)
    assert summary.build_valor_justo_teaser_line({}, None, 150.0).endswith(base)


def _balance_par(texto):
    return {c: texto.count(c) % 2 for c in _MD}


def _todos_los_motivos_emitidos_con_detalles():
    items = []
    for motivo in sorted(_motivos_emitidos_por_ast()) + sorted(_EXTRA_15_5):
        items.append({"motivo": motivo, "detalle": None})
    for detalle in sorted(valuation.DETALLES_VALIDOS):
        for motivo in ("wacc_no_calculable", "eps_ttm_no_positivo"):
            items.append({"motivo": motivo, "detalle": detalle})
    return items


def test_nd_cs11_detalle_fuera_de_validos_se_ignora_y_usa_el_motivo():
    scen = _escenarios_manual([{"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": "texto_inventado_x"}])
    texto = _seccion(scen)
    assert _MOTIVO_LABELS_HEAD["wacc_no_calculable"] in texto
    assert "texto_inventado_x" not in texto
    linea = summary.build_valor_justo_teaser_line(
        {"conservador": {"valor_justo_total": None, "modelos_excluidos": scen["conservador"]["modelos_excluidos"]}},
        "conservador",
        150.0,
    )
    assert _MOTIVO_LABELS_HEAD["wacc_no_calculable"] in linea and "texto_inventado_x" not in linea


def test_nd_cs11_motivo_y_modelo_desconocidos_con_guion_bajo_no_rompen_el_balance():
    base = _balance_par(_seccion(_escenarios_manual([])))
    scen = _escenarios_manual(
        [
            {"modelo": "dcf", "motivo": "motivo_raro_con_guion_bajo", "detalle": "detalle_raro_x"},
            {"modelo": "modelo_raro_x", "motivo": "otro_motivo_raro_y"},
        ]
    )
    seccion = _seccion(scen)
    assert _balance_par(seccion) == base
    for crudo in ("motivo_raro", "detalle_raro", "modelo_raro", "otro_motivo"):
        assert crudo not in seccion
    assert f"- el modelo DCF no se pudo calcular: {summary.MOTIVO_GENERICO}." in seccion


def test_nd_cs11_balance_markdown_de_teaser_y_seccion_para_todo_motivo_emitido():
    base_seccion = _balance_par(_seccion(_escenarios_manual([])))
    assert len(_todos_los_motivos_emitidos_con_detalles()) >= 14 + 2 + 14
    for item in _todos_los_motivos_emitidos_con_detalles():
        for modelo in ("multiplos", "graham", "dcf"):
            excl = [{"modelo": modelo, **item}]
            scen = _escenarios_manual(excl)
            seccion = _seccion(scen)
            assert _balance_par(seccion) == base_seccion, item
            for crudo in (item["motivo"], item["detalle"]):
                if crudo and "_" in crudo:
                    assert crudo not in seccion, item
            linea = summary.build_valor_justo_teaser_line(
                {"conservador": {"valor_justo_total": None, "modelos_excluidos": excl}}, "conservador", 150.0
            )
            assert _balance_par(linea) == {c: 0 for c in _MD}, item
            assert "\n" not in linea
            for crudo in (item["motivo"], item["detalle"]):
                if crudo and "_" in crudo:
                    assert crudo not in linea, item


# --- CS-ND-15 ---------------------------------------------------------------------


def test_nd_cs15_teaser_peor_caso_una_linea_un_placeholder_un_chunk():
    scen = _teaser_peor_caso()
    linea = summary.build_valor_justo_teaser_line(scen, "conservador", -1234.56)
    assert "\n" not in linea
    assert not any(c in linea for c in "*_`[")
    _, line_map = ai_rewrite._classify_lines(linea)
    assert len(line_map) == 1
    partes = summary.build_summary_parts_short(
        ticker="CLPT",
        company_name="Clearpoint Neuro",
        precio_actual=-1234.56,
        pillars={
            "ingresos_crecientes": True,
            "utilidades_crecientes": False,
            "deuda_controlada": True,
            "precio_razonable": False,
        },
        risk_fit={"encaja": True, "perfil": "moderado", "beta": 1.0, "etiqueta_activo": "renta variable"},
        scenarios=scen,
        escenario_elegido="conservador",
    )
    assert linea in partes
    assert len(query_handler.chunk_for_telegram(partes)) == 1
    # el teaser es la unica linea con "Por qué" de ese mensaje
    assert sum("Por qué" in parte for parte in partes) == 1


# ---------------------------------------------------------------------------
# F3 -- Desglose de `vf` con motivos y "Ver dato" (CA-ND-9, 10, 15, CS-ND-18)
# ---------------------------------------------------------------------------

_EX_CLPT = [
    {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
    {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
    {"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"},
]

_T_EPS = summary.MOTIVO_LABELS["eps_ttm_no_positivo"]
_T_FCF = summary.MOTIVO_LABELS["fcf_reciente_no_positivo"]

_VALORES_ND = {
    "valor_justo_multiplos": None,
    "valor_justo_graham": None,
    "valor_justo_dcf": None,
    "valor_justo_total": None,
}


def _nd_ctx(overrides=None, *, elegido="conservador", excluidos=None, base=None, **top):
    """Contexto de `texto_libre` (ADBE de los tests de HEAD) con `overrides`
    sobre el escenario elegido y, si se pasan, `modelos_excluidos` del
    escenario elegido y `modelos_excluidos_base` del dict padre."""
    ctx_base = _texto_libre_context()
    scenarios = copy.deepcopy(ctx_base.scenarios)
    scenarios[elegido].update(overrides or {})
    if excluidos is not None:
        scenarios[elegido]["modelos_excluidos"] = excluidos
    if base is not None:
        scenarios["modelos_excluidos_base"] = base
    return _texto_libre_context(scenarios=scenarios, escenario_elegido=elegido, **top)


def _nd_ctx_total_nd(excluidos=None, **kw):
    return _nd_ctx(_VALORES_ND, excluidos=excluidos, **kw)


def _partes(ctx):
    return ai_explain._build_desglose_vf_partes(ctx, ai_explain._payload_texto_libre(ctx, "vf"))


def _quitar_motivos(bloque):
    import re

    return re.sub(r"( Motivo: [^\n]*?\.(?=\n|$))|(\n  Motivo: [^\n]*?\.(?=\n|$))", "", bloque)


def test_nd_ca9_motivo_en_la_linea_generica_y_tras_la_cuenta():
    ctx = _nd_ctx(
        {"valor_justo_multiplos": None, "valor_justo_graham": None, "valor_justo_total": 510.0},
        excluidos=[
            {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "graham", "motivo": "eps_ttm_no_positivo"},
            {"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"},  # DCF tiene valor: sin Motivo
        ],
    )
    bloque, con_motivos = _partes(ctx)
    assert con_motivos is True
    assert f"• Múltiplos — no calculable con los datos disponibles. Motivo: {_T_EPS}." in bloque
    # Graham conserva su Cuenta ("= no calculable") y el Motivo va en la línea siguiente.
    assert f"= no calculable.\n  Motivo: {_T_EPS}." in bloque
    assert bloque.count("Motivo:") == 2
    assert "• DCF (Flujo de Caja Descontado) ($510.00)" in bloque
    assert _T_FCF not in bloque
    assert ai_explain._build_desglose_vf(ctx, ai_explain._payload_texto_libre(ctx, "vf")) == bloque
    assert "None" not in bloque


def test_nd_ca9_sin_modelos_excluidos_el_bloque_es_identico_a_head():
    ctx = _texto_libre_context()
    bloque, con_motivos = _partes(ctx)
    assert con_motivos is False
    assert len(bloque) == 910  # medido en HEAD
    assert "Motivo:" not in bloque
    # Con motivos conocidos, quitar los Motivo devuelve exactamente el bloque sin ellos.
    ctx_nd = _nd_ctx_total_nd(_EX_CLPT)
    con, ok = _partes(ctx_nd)
    sin, ok_sin = _partes(_nd_ctx_total_nd())
    assert ok is True and ok_sin is False
    assert _quitar_motivos(con) == sin
    assert sin.count("no calculable con los datos disponibles") == 1
    assert sin.count("= no calculable") == 2


def test_nd_ca9_con_valor_presente_nunca_hay_motivo():
    ctx = _nd_ctx(excluidos=_EX_CLPT)  # los 3 sub-modelos tienen valor
    bloque, con_motivos = _partes(ctx)
    assert "Motivo:" not in bloque
    assert con_motivos is False
    # Valor presente con generic (cuenta None forzada): tampoco.
    ctx2 = _nd_ctx(excluidos=_EX_CLPT)
    datos = ai_explain._payload_texto_libre(ctx2, "vf")
    assert ai_explain._motivo_vf_sin_valor(
        ai_explain._escenario_elegido(ctx2), "Múltiplos", "mul", datos
    ) is None


def test_nd_ca9_valor_ilegible_en_datos_no_lanza_ni_agrega_motivo():
    esc = {"modelos_excluidos": [{"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"}]}
    assert ai_explain._motivo_vf_sin_valor(esc, "Múltiplos", "mul", {"valor_justo_multiplos": "x"}) is None


def test_nd_ca9_sin_terminos_de_desglose_no_hay_bloque_ni_motivos(monkeypatch):
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    monkeypatch.setattr(ai_explain_content, "desglose", lambda kind, code: ())
    assert _partes(ctx) == (None, False)
    assert ai_explain._build_desglose_vf(ctx, {}) is None


def test_nd_ca9_una_entrada_por_modelo_la_primera():
    ctx = _nd_ctx_total_nd([
        {"modelo": "multiplos", "motivo": "per_peers_no_disponible"},
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"},
    ])
    bloque, _ = _partes(ctx)
    assert summary.MOTIVO_LABELS["per_peers_no_disponible"] in bloque
    assert _T_EPS not in bloque
    assert bloque.count("Motivo:") == 1


def test_nd_ca9_usa_el_escenario_elegido_y_nunca_la_lista_base():
    ctx = _nd_ctx_total_nd(None, base=[{"modelo": "multiplos", "motivo": "eps_ttm_no_positivo"}])
    bloque, con_motivos = _partes(ctx)
    assert con_motivos is False and "Motivo:" not in bloque
    # El escenario elegido manda (M-19): pesimista con motivos, conservador sin.
    ctx2 = _nd_ctx(_VALORES_ND, elegido="pesimista", excluidos=_EX_CLPT)
    assert ctx2.escenario_elegido == "pesimista"
    bloque2, con2 = _partes(ctx2)
    assert con2 is True and bloque2.count("Motivo:") == 3
    ctx3 = _nd_ctx(excluidos=_EX_CLPT, elegido="optimista")  # optimista tiene valores
    assert _partes(ctx3)[1] is False


_MUL_MOTIVOS = [
    ("eps_ttm_no_positivo", None),
    ("eps_ttm_no_positivo", "eps_no_disponible"),
    ("peers_validos_insuficientes", None),
    ("per_peers_no_disponible", None),
]
_GRA_MOTIVOS = [
    ("eps_ttm_no_positivo", None),
    ("eps_ttm_no_positivo", "eps_no_disponible"),
    ("historial_insuficiente", None),
    ("eps_base_no_positivo", None),
    ("eps_reciente_no_positivo", None),
    ("eps_cagr_no_calculable", None),
    ("y_no_disponible", None),
    ("graham_multiplicador_no_positivo", None),
]
_DCF_MOTIVOS = (
    [
        ("fcf_base_no_positivo", None),
        ("fcf_reciente_no_positivo", None),
        ("fcf_cagr_no_calculable", None),
        ("historial_insuficiente", None),
        ("wacc_no_calculable", None),
        ("dcf_no_calculable", None),
    ]
    + [("wacc_no_calculable", d) for d in sorted(valuation.DETALLES_VALIDOS - {"eps_no_disponible"})]
)


def _excluidos_de(m, g, d):
    return [
        {"modelo": "multiplos", "motivo": m[0], "detalle": m[1]},
        {"modelo": "graham", "motivo": g[0], "detalle": g[1]},
        {"modelo": "dcf", "motivo": d[0], "detalle": d[1]},
    ]


def _ctx_peor_caso(excluidos):
    # Los 3 valores N/D y wacc/g en None: las Cuentas de Graham y DCF SE
    # MUESTRAN igual con "no disp." (peor caso de B-2).
    return _nd_ctx(
        {**_VALORES_ND, "dcf_wacc": None, "dcf_g_fcf": None, "graham_g_aplicado": None},
        excluidos=excluidos,
    )


def test_nd_ca9_producto_de_linea_generica_y_con_cuenta_por_todos_los_motivos(monkeypatch):
    originales = dict(ai_explain._VF_SUB_MODELO_CUENTA)
    degradados = 0
    mas_largo_con_motivos = 0
    for variante in itertools.product([False, True], repeat=3):  # True = línea con Cuenta
        for code, con_cuenta in zip(("mul", "gra", "dcf"), variante):
            monkeypatch.setitem(
                ai_explain._VF_SUB_MODELO_CUENTA, code,
                originales[code] if con_cuenta else (lambda datos: None),
            )
        for m, g, d in itertools.product(_MUL_MOTIVOS, _GRA_MOTIVOS, _DCF_MOTIVOS):
            ctx = _ctx_peor_caso(_excluidos_de(m, g, d))
            datos = ai_explain._payload_texto_libre(ctx, "vf")
            bloque, con_motivos = ai_explain._build_desglose_vf_partes(ctx, datos)
            assert bloque is not None, (variante, m, g, d)
            assert len(bloque) <= 1200, (variante, m, g, d, len(bloque))
            assert "None" not in bloque and ".." not in bloque
            if con_motivos:
                assert bloque.count("Motivo:") == 3
            else:
                degradados += 1
                assert "Motivo:" not in bloque
                assert bloque.count("no calculable con los datos disponibles") >= variante.count(False)
            with monkeypatch.context() as mp:
                mp.setattr(ai_explain, "_MAX_DESGLOSE_CHARS", 10**6)
                completo, _ = ai_explain._build_desglose_vf_partes(ctx, datos)
            mas_largo_con_motivos = max(mas_largo_con_motivos, len(completo))
            assert (len(completo) > 1200) == (not con_motivos)
    # Peor caso real medido (pesos de HEAD + textos 15.5): ~1220 > 1200.
    assert mas_largo_con_motivos > 1200
    assert degradados >= 1


def test_nd_ca9_peor_caso_degrada_al_bloque_sin_motivos_y_no_lo_oculta(monkeypatch):
    peor = _excluidos_de(
        ("peers_validos_insuficientes", None),
        ("eps_base_no_positivo", None),
        ("wacc_no_calculable", "wacc_costo_deuda_fuera_de_rango"),
    )
    ctx = _ctx_peor_caso(peor)
    datos = ai_explain._payload_texto_libre(ctx, "vf")
    bloque, con_motivos = ai_explain._build_desglose_vf_partes(ctx, datos)
    assert bloque is not None and con_motivos is False
    assert len(bloque) <= 910
    # Idéntico al de HEAD: el mismo contexto sin modelos_excluidos.
    sin, _ = _partes(_ctx_peor_caso(None))
    assert bloque == sin
    with monkeypatch.context() as mp:
        mp.setattr(ai_explain, "_MAX_DESGLOSE_CHARS", 10**6)
        completo, ok = ai_explain._build_desglose_vf_partes(ctx, datos)
    assert ok is True and len(completo) > 1200
    # Un bloque que HEAD ocultaría (sobre el tope aun sin motivos) sigue ocultándose.
    with monkeypatch.context() as mp:
        mp.setattr(ai_explain, "_MAX_DESGLOSE_CHARS", 100)
        assert ai_explain._build_desglose_vf_partes(ctx, datos) == (None, False)


def test_nd_ca10_desglose_sin_none_sin_doble_punto_y_sin_frases_prohibidas():
    textos = {
        summary.motivo_texto(it["motivo"], it["detalle"])
        for it in _todos_los_motivos_emitidos_con_detalles()
    }
    assert None not in textos
    for texto in textos:
        assert "no calculable con los datos disponibles" not in texto.replace(
            summary.MOTIVO_LABELS_EXTRA["eps_cagr_no_calculable"], ""
        ).replace(summary.MOTIVO_LABELS_EXTRA["fcf_cagr_no_calculable"], "").replace(
            summary.MOTIVO_LABELS["wacc_no_calculable"], ""
        ).replace(summary.MOTIVO_LABELS["dcf_no_calculable"], "")
        assert "= no calculable" not in texto
        assert not texto.endswith(".")
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    bloque, _ = _partes(ctx)
    assert "None" not in bloque and ".." not in bloque
    for linea in bloque.splitlines():
        if "Motivo:" in linea:
            assert not linea.endswith("..")
    # Los count() de HEAD siguen valiendo con motivos (Ticker D).
    assert bloque.count("no calculable con los datos disponibles") == 1
    assert bloque.count("= no calculable") == 2


def _ver_dato(ctx, code):
    return ai_explain._build_ver_dato_content(ctx, code)


@pytest.mark.parametrize(
    "code, esperado",
    [
        ("vf", f"Múltiplos y Graham: {_T_EPS}; DCF: {_T_FCF}"),
        ("ver", f"Múltiplos y Graham: {_T_EPS}; DCF: {_T_FCF}"),
        ("gra", _T_EPS),
        ("mul", _T_EPS),
        ("dcf", _T_FCF),
    ],
)
def test_nd_ca15_ver_dato_nd_agrega_por_que_tras_el_dato(code, esperado):
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    texto = _ver_dato(ctx, code)
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX)
    assert f"\nPor qué: {esperado}." in texto
    assert texto.index("📌 Dato:") < texto.index("Por qué:")
    assert "None" not in texto
    # "Ver dato" de HEAD: lo demás del texto no cambia.
    sin = _ver_dato(_nd_ctx_total_nd(), code)
    assert "Por qué" not in sin
    assert texto.replace(f"\nPor qué: {esperado}.", "") == sin


def test_nd_ca15_ver_dato_sin_nd_o_fuera_de_los_5_codigos_o_sin_motivo_es_head():
    # Valor presente aunque haya excluidos: sin Por qué.
    assert "Por qué" not in _ver_dato(_nd_ctx(excluidos=_EX_CLPT), "vf")
    # Código fuera de los 5 con las claves N/D: sin Por qué.
    for code in ("rat", "pil", "cmp", "mom", "rsk"):
        assert "Por qué" not in _ver_dato(_nd_ctx_total_nd(_EX_CLPT), code)
    # Sin motivo conocido: idéntico a HEAD (sin la línea).
    desconocido = [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}]
    assert "Por qué" not in _ver_dato(_nd_ctx_total_nd(desconocido), "vf")
    # kind avanzado: nunca aplica.
    assert "Por qué" not in _ver_dato(_avanzado_context(), "alz")


def test_nd_ca15_ver_dato_si_el_helper_lanza_se_omite_y_no_cambia_el_resto(monkeypatch):
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    con = _ver_dato(ctx, "vf")
    assert "Por qué" in con

    def boom(*a, **k):
        raise RuntimeError("hostil")

    monkeypatch.setattr(ai_explain, "_nd_aplica", boom)
    assert _ver_dato(ctx, "vf") == _ver_dato(_nd_ctx_total_nd(), "vf")


@pytest.mark.parametrize("excluidos", [
    "texto", None, 7, [], ["a", 3, None], [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}],
    [{"modelo": "dcf", "motivo": ["lista"], "detalle": ["otra"]}],
    [{"modelo": "modelo_raro_x", "motivo": "eps_ttm_no_positivo"}],
    [{"modelo": ["dcf"], "motivo": 3}],
])
@pytest.mark.parametrize("code", ["vf", "ver", "gra", "dcf", "mul"])
def test_nd_cs18_ver_dato_con_motivo_hostil_o_desconocido_nunca_clave_cruda(code, excluidos):
    texto = _ver_dato(_nd_ctx_total_nd(excluidos), code)
    assert "None" not in texto
    for crudo in ("motivo_raro_x_y", "modelo_raro_x", "lista", "otra"):
        assert crudo not in texto
    if "Por qué" in texto:  # solo si algún motivo es conocido
        assert any(summary.motivo_texto(it.get("motivo"), it.get("detalle")) for it in excluidos if isinstance(it, dict))


def test_nd_cs10_desglose_vf_con_excluidos_hostiles_nunca_lanza_ni_muestra_crudo():
    base, _ = _partes(_nd_ctx_total_nd())
    for excluidos in ("texto", None, 7, ["a", 3], [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}],
                      [{"modelo": ["dcf"], "motivo": {"x": 1}, "detalle": [1]}]):
        bloque, con_motivos = _partes(_nd_ctx_total_nd(excluidos))
        assert bloque == base and con_motivos is False
        assert "motivo_raro_x_y" not in bloque
    # scenarios / escenario_elegido hostiles: el bloque se arma como el de HEAD.
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    for hostil in ({"escenario_elegido": ["conservador"]}, {"scenarios": ["x"]}):
        ctx_h = _texto_libre_context(**hostil)
        assert ai_explain._escenario_elegido(ctx_h) is None
    assert _partes(ctx)[1] is True


# ---------------------------------------------------------------------------
# F4a -- helpers puros y tablas ND_* (CA-ND-11, 13 parcial, CS-ND-3/5/10/13/16, M-20)
# ---------------------------------------------------------------------------

_ND_CODIGOS = ("vf", "ver", "gra", "dcf", "mul")
_ND_CLAVES = {
    "vf": "valor_justo_total", "ver": "valor_justo_total", "gra": "valor_justo_graham",
    "dcf": "valor_justo_dcf", "mul": "valor_justo_multiplos",
}
_ESCENARIOS = ("pesimista", "conservador", "optimista")
_TODAS_LAS_CLAVES = ("valor_justo_total", "valor_justo_graham", "valor_justo_dcf", "valor_justo_multiplos")


def _ctx_solo_nd(clave, elegido):
    """Solo `clave` es None, y solo en el escenario `elegido`."""
    return _nd_ctx({clave: None}, elegido=elegido)


@pytest.mark.parametrize("elegido", _ESCENARIOS)
@pytest.mark.parametrize("code", _ND_CODIGOS)
def test_nd_ca11_nd_aplica_por_pregunta_y_escenario_elegido(code, elegido):
    clave = _ND_CLAVES[code]
    assert ai_explain._nd_aplica(_ctx_solo_nd(clave, elegido), code) is True
    # El valor N/D en OTRO escenario no dispara (M-19: no usa siempre conservador).
    for otro in _ESCENARIOS:
        if otro != elegido:
            ctx = _nd_ctx({clave: None}, elegido=otro)
            ctx = _texto_libre_context(scenarios=ctx.scenarios, escenario_elegido=elegido)
            assert ai_explain._nd_aplica(ctx, code) is False
    # Las otras claves N/D no disparan este código (M-18: no ignora el código).
    otras = {c: None for c in _TODAS_LAS_CLAVES if c != clave}
    assert ai_explain._nd_aplica(_nd_ctx(otras, elegido=elegido), code) is False


def test_nd_ca11_20_codigos_fuera_de_los_5_siempre_false():
    todos_nd = _nd_ctx({c: None for c in _TODAS_LAS_CLAVES})
    negativos = [c for c in _PRESENTABLES if c not in _ND_CODIGOS]
    assert len(negativos) == 20
    for code in negativos:
        assert ai_explain._nd_aplica(todos_nd, code) is False, code
    for code in _ND_CODIGOS:
        assert ai_explain._nd_aplica(todos_nd, code) is True, code
    assert ai_explain._nd_aplica(todos_nd, "inexistente") is False


def test_nd_ca11_kind_avanzado_claves_ausentes_cero_y_datos_hostiles():
    assert ai_explain._nd_aplica(_avanzado_context(), "vf") is False
    # kind != texto_libre aunque el escenario tenga la clave None.
    ctx_av = _avanzado_context(
        scenarios={"conservador": {"valor_justo_total": None}}, escenario_elegido="conservador"
    )
    assert ai_explain._nd_aplica(ctx_av, "vf") is False
    for code in _ND_CODIGOS:
        parcial = _texto_libre_context(scenarios={"conservador": {}}, escenario_elegido="conservador")
        assert ai_explain._nd_aplica(parcial, code) is False  # M-2: clave ausente
        con_cero = _nd_ctx({_ND_CLAVES[code]: 0})
        assert ai_explain._nd_aplica(con_cero, code) is False  # 0 != None
        con_cero_f = _nd_ctx({_ND_CLAVES[code]: 0.0})
        assert ai_explain._nd_aplica(con_cero_f, code) is False
    base = _nd_ctx({"valor_justo_total": None})
    for hostil in (None, "pesimista_x", ["conservador"], {"a": 1}, 7):
        ctx = _texto_libre_context(scenarios=base.scenarios, escenario_elegido=hostil)
        assert ai_explain._nd_aplica(ctx, "vf") is False, hostil
    for scen in (None, [], ["x"], "texto", 3):
        ctx = _texto_libre_context(scenarios=scen, escenario_elegido="conservador")
        assert ai_explain._nd_aplica(ctx, "vf") is False
    escenario_no_dict = _texto_libre_context(scenarios={"conservador": ["x"]}, escenario_elegido="conservador")
    assert ai_explain._nd_aplica(escenario_no_dict, "vf") is False
    assert ai_explain._nd_aplica(base, ["vf"]) is False  # code no hashable
    assert ai_explain._nd_aplica(base, None) is False


def test_nd_ca11_escenario_elegido_y_motivo_modelo_son_puros_y_no_lanzan():
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    assert ai_explain._escenario_elegido(ctx) is ctx.scenarios["conservador"]
    assert ai_explain._motivo_modelo(ctx.scenarios["conservador"], "dcf") == _T_FCF
    assert ai_explain._motivo_modelo(ctx.scenarios["conservador"], "multiplos") == _T_EPS
    assert ai_explain._motivo_modelo(None, "dcf") is None
    assert ai_explain._motivo_modelo({"modelos_excluidos": "x"}, "dcf") is None
    assert ai_explain._motivo_modelo({"modelos_excluidos": [3, None]}, "dcf") is None
    assert ai_explain._motivo_modelo({"modelos_excluidos": []}, "dcf") is None
    assert ai_explain._motivo_modelo({}, "dcf") is None
    # Modelo conocido con motivo desconocido: None (nunca el crudo).
    assert ai_explain._motivo_modelo(
        {"modelos_excluidos": [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}]}, "dcf"
    ) is None


# --- Mensaje N/D ------------------------------------------------------------------

_NO_BMP = "😀"


def _msg(code, excluidos=_EX_CLPT, **kw):
    return ai_explain._build_nd_message(_nd_ctx_total_nd(excluidos, **kw), code)


@pytest.mark.parametrize("code", _ND_CODIGOS)
def test_nd_ca13_mensaje_nd_estructura_sin_none_sin_valor_sin_gemini(code):
    texto = _msg(code)
    assert texto is not None
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX + "\n\n📌 Dato: ")
    assert "None" not in texto
    assert f"⚠️ {ai_explain_content.ND_TITULO[code]}\n{ai_explain_content.ND_AVISO[code]}" in texto
    assert "🤖 Con ayuda de Gemini" not in texto
    assert ai_rewrite.TRANSPARENCY_USED not in texto
    assert ai_explain._len16(texto) <= 4096
    dato = texto.split("\n\n")[1]
    if code != "ver":
        assert "N/D" in dato  # no afirma un valor
    formula = ai_explain_content.formulas("texto_libre").get(code)
    fuente = ai_explain_content.fuentes("texto_libre").get(code)
    assert f"📐 Fórmula: {formula}" in texto
    assert f"📊 Fuente del dato: {fuente}" in texto
    assert texto.endswith(f"📊 Fuente del dato: {fuente}")
    # Orden: dato, aviso, por qué/desglose, (gráfico), fórmula.
    assert texto.index("📌 Dato:") < texto.index("⚠️") < texto.index("📐 Fórmula")


def test_nd_ca13_vf_con_motivos_muestra_el_desglose_y_no_el_por_que():
    texto = _msg("vf")
    assert "🔍 Desglose:" in texto
    assert ai_explain_content.ND_ENCABEZADO_POR_QUE not in texto
    assert f"Motivo: {_T_EPS}." in texto
    assert texto.index("⚠️") < texto.index("🔍 Desglose:") < texto.index("📐 Fórmula")
    assert "📊 Valor justo vs precio" not in texto  # vf no lleva gráfico en el N/D


def test_nd_ca13_vf_sin_motivos_conocidos_desglose_generico_y_por_que_sin_motivo():
    texto = _msg("vf", None)
    sin = ai_explain_content.ND_SIN_MOTIVO
    assert "🔍 Desglose:" in texto
    assert f"{ai_explain_content.ND_ENCABEZADO_POR_QUE} {sin}." in texto
    assert texto.count("no calculable con los datos disponibles") == 1
    assert texto.count("= no calculable") == 2
    assert texto.index("🔍 Desglose:") < texto.index(ai_explain_content.ND_ENCABEZADO_POR_QUE)


def test_nd_ca13_vf_degradado_usa_por_que_con_el_resumen(monkeypatch):
    peor = _excluidos_de(
        ("peers_validos_insuficientes", None),
        ("eps_base_no_positivo", None),
        ("wacc_no_calculable", "wacc_costo_deuda_fuera_de_rango"),
    )
    ctx = _ctx_peor_caso(peor)
    texto = ai_explain._build_nd_message(ctx, "vf")
    assert "Motivo:" not in texto
    resumen = summary.build_motivos_resumen(ctx.scenarios["conservador"]["modelos_excluidos"])
    assert f"{ai_explain_content.ND_ENCABEZADO_POR_QUE} {resumen}." in texto


def test_nd_ca13_ver_gra_dcf_mul_por_que_con_resumen_o_motivo_unico():
    resumen = f"Múltiplos y Graham: {_T_EPS}; DCF: {_T_FCF}"
    assert f"🔎 Por qué: {resumen}." in _msg("ver")
    assert f"🔎 Por qué: {_T_EPS}." in _msg("gra")
    assert f"🔎 Por qué: {_T_EPS}." in _msg("mul")
    assert f"🔎 Por qué: {_T_FCF}." in _msg("dcf")
    for code in ("ver", "gra", "dcf", "mul"):
        assert f"🔎 Por qué: {ai_explain_content.ND_SIN_MOTIVO}." in _msg(code, None)
        assert "🔍 Desglose" not in _msg(code)


@pytest.mark.parametrize("code, hay_grafico", [("vf", False), ("ver", False), ("gra", True), ("dcf", True), ("mul", True)])
def test_nd_ca13_grafico_solo_en_gra_dcf_mul_y_solo_si_existe(code, hay_grafico):
    texto = ai_explain._build_nd_message(_nd_ctx({_ND_CLAVES[code]: None}, excluidos=_EX_CLPT), code)
    assert ("📊 Escenarios vs precio" in texto) is hay_grafico
    assert "📊 Valor justo vs precio" not in texto  # vf/ver no llevan gráfico en el N/D
    if hay_grafico:
        assert texto.index("🔎 Por qué:") < texto.index("📊 Escenarios vs precio") < texto.index("📐 Fórmula")
    # Sin gráfico posible (los 3 escenarios N/D): el mensaje sale igual.
    todo_nd = copy.deepcopy(_texto_libre_context().scenarios)
    for esc in todo_nd.values():
        esc.update(_VALORES_ND)
    ctx = _texto_libre_context(scenarios=todo_nd)
    assert "📊 Escenarios vs precio" not in ai_explain._build_nd_message(ctx, code)


def test_nd_ca13_sin_formula_ni_fuente_no_las_agrega(monkeypatch):
    monkeypatch.setattr(ai_explain_content, "FORMULAS_TEXTO_LIBRE", {})
    monkeypatch.setattr(ai_explain_content, "FUENTES_TEXTO_LIBRE", {})
    texto = _msg("gra")
    assert "📐" not in texto and "Fuente del dato" not in texto
    assert texto.endswith(f"🔎 Por qué: {_T_EPS}.") or "📊 Escenarios" in texto


def _podas(ctx, code, monkeypatch):
    textos = [ai_explain._build_nd_message(ctx, code)]
    with monkeypatch.context() as mp:
        while True:
            mp.setattr(
                ai_explain, "_TELEGRAM_LIMIT", ai_explain._len16(textos[-1]) + ai_explain._MARGEN_TELEGRAM - 1
            )
            siguiente = ai_explain._build_nd_message(ctx, code)
            if siguiente == textos[-1]:
                return textos
            textos.append(siguiente)


def test_nd_ca13_poda_gra_primero_el_grafico_luego_el_por_que(monkeypatch):
    ctx = _nd_ctx({"valor_justo_graham": None}, excluidos=_EX_CLPT)
    completo, sin_grafico, sin_por_que = _podas(ctx, "gra", monkeypatch)
    assert "📊 Escenarios vs precio" in completo and "🔎 Por qué:" in completo
    assert "📊 Escenarios vs precio" not in sin_grafico and "🔎 Por qué:" in sin_grafico
    assert "🔎 Por qué:" not in sin_por_que and "📊 Escenarios" not in sin_por_que
    assert ai_explain_content.ND_AVISO["gra"] in sin_por_que  # el aviso NUNCA se omite
    assert "📐 Fórmula" in sin_por_que


def test_nd_ca13_poda_vf_primero_el_desglose_luego_el_por_que(monkeypatch):
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    completo, sin_desglose, sin_por_que = _podas(ctx, "vf", monkeypatch)
    assert "🔍 Desglose:" in completo and "🔎 Por qué:" not in completo
    assert "🔍 Desglose:" not in sin_desglose
    resumen = summary.build_motivos_resumen(ctx.scenarios["conservador"]["modelos_excluidos"])
    assert f"🔎 Por qué: {resumen}." in sin_desglose
    assert "🔎 Por qué:" not in sin_por_que and "🔍 Desglose" not in sin_por_que
    assert ai_explain_content.ND_AVISO["vf"] in sin_por_que
    # Sin motivos conocidos: al podar el Desglose queda el Por qué con ND_SIN_MOTIVO.
    ctx2 = _nd_ctx_total_nd(None)
    _, sin_desglose2, _ = _podas(ctx2, "vf", monkeypatch)
    assert f"🔎 Por qué: {ai_explain_content.ND_SIN_MOTIVO}." in sin_desglose2


def test_nd_ca13_con_tope_de_desglose_enorme_etiquetas_de_2000_y_no_bmp_sigue_en_4096(monkeypatch):
    monkeypatch.setattr(ai_explain, "_MAX_DESGLOSE_CHARS", 10**6)
    largas = {
        "eps_ttm_no_positivo": "á" * 2000,
        "fcf_reciente_no_positivo": "ñ" * 2000,
    }
    for clave, texto in largas.items():
        monkeypatch.setitem(summary.MOTIVO_LABELS, clave, texto)
    for code in _ND_CODIGOS:
        texto = _msg(code)
        assert ai_explain._len16(texto) <= 4096, code
        assert ai_explain_content.ND_AVISO[code] in texto
        assert "None" not in texto
    # Etiquetas no BMP: len() cabe pero _len16 no (mata "usar len en vez de _len16").
    monkeypatch.setitem(summary.MOTIVO_LABELS, "eps_ttm_no_positivo", _NO_BMP * 1900)
    monkeypatch.setitem(summary.MOTIVO_LABELS, "fcf_reciente_no_positivo", _NO_BMP * 1900)
    for code in _ND_CODIGOS:
        texto = _msg(code)
        assert ai_explain._len16(texto) <= 4096, code
        assert ai_explain_content.ND_AVISO[code] in texto
    # Una sola etiqueta compartida: queda el Por qué sin Desglose cuando entra.
    monkeypatch.setitem(summary.MOTIVO_LABELS, "eps_ttm_no_positivo", "á" * 1500)
    monkeypatch.setitem(summary.MOTIVO_LABELS, "fcf_reciente_no_positivo", "á" * 1500)
    texto = _msg("vf")
    assert "🔍 Desglose" not in texto and ("á" * 1500) in texto
    assert ai_explain._len16(texto) <= 4096


def test_nd_ca13_unica_via_de_none_es_una_excepcion(monkeypatch, caplog):
    ctx = _nd_ctx_total_nd(_EX_CLPT)
    assert ai_explain._build_nd_message(ctx, "inexistente") is None  # KeyError capturado
    assert ai_explain._build_nd_message(ctx, "gra") is not None

    def boom(*a, **k):
        raise RuntimeError("DATO-SECRETO")

    monkeypatch.setattr(ai_explain, "_build_explain_payload", boom)
    with caplog.at_level("WARNING"):
        assert ai_explain._build_nd_message(ctx, "gra") is None
    assert "DATO-SECRETO" not in caplog.text
    assert "EXPLAIN_ND_MENSAJE_FALLO tipo=RuntimeError" in caplog.text


def test_nd_cs5_el_mensaje_nd_no_interpola_ticker_ni_company_name():
    canarios = dict(ticker="CANARIOTK", company_name="CANARIO_CO_INC", chat_id=987654321)
    for code in _ND_CODIGOS:
        ctx = _nd_ctx_total_nd(_EX_CLPT, **canarios)
        for texto in (
            ai_explain._build_nd_message(ctx, code),
            ai_explain._build_nd_por_que(ctx, code) or "",
            _ver_dato(ctx, code),
        ):
            for c in ("CANARIOTK", "CANARIO_CO_INC", "987654321"):
                assert c not in texto, (code, c)


_ESCENARIOS_HOSTILES = [
    None, [], ["x"], "texto", 3, {"conservador": None}, {"conservador": []}, {"conservador": "x"},
    {"conservador": {"valor_justo_total": None, "modelos_excluidos": "x"}},
    {"conservador": {"valor_justo_total": None, "modelos_excluidos": [None, 3, "a"]}},
    {"conservador": {"valor_justo_total": None, "modelos_excluidos": [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}]}},
    {"conservador": {"valor_justo_total": None, "modelos_excluidos": [{"modelo": ["dcf"], "motivo": [1], "detalle": {}}]}},
]


def test_nd_cs10_helpers_con_datos_hostiles_nunca_lanzan_ni_muestran_crudo():
    for scen in _ESCENARIOS_HOSTILES:
        for elegido in ("conservador", None, ["conservador"], "otro"):
            ctx = _texto_libre_context(scenarios=scen, escenario_elegido=elegido)
            for code in _ND_CODIGOS + ("rat", "inexistente"):
                aplica = ai_explain._nd_aplica(ctx, code)
                assert isinstance(aplica, bool)
                por_que = ai_explain._build_nd_por_que(ctx, code)
                assert por_que is None or isinstance(por_que, str)
                if por_que:
                    _sin_crudo(por_que)
    for scen in _ESCENARIOS_HOSTILES:
        ctx = _texto_libre_context(scenarios=scen, escenario_elegido="conservador")
        for code in _ND_CODIGOS:
            if not ai_explain._nd_aplica(ctx, code):
                continue
            texto = ai_explain._build_nd_message(ctx, code)
            if texto is not None:  # solo una excepción interna da None
                _sin_crudo(texto)
                assert ai_explain_content.ND_AVISO[code] in texto
                assert f"{ai_explain_content.ND_ENCABEZADO_POR_QUE} {ai_explain_content.ND_SIN_MOTIVO}." in texto or "🔍 Desglose" in texto


def test_nd_cs10_motivo_desconocido_nunca_se_interpola_y_cae_al_texto_sin_motivo():
    ex = [{"modelo": "dcf", "motivo": "motivo_raro_x_y"}]
    for code in ("vf", "ver", "gra", "dcf", "mul"):
        texto = _msg(code, ex)
        assert "motivo_raro_x_y" not in texto
        assert f"{ai_explain_content.ND_ENCABEZADO_POR_QUE} {ai_explain_content.ND_SIN_MOTIVO}." in texto


def test_nd_cs10_los_motivos_se_leen_solo_del_escenario_elegido_no_de_la_lista_base():
    ctx = _nd_ctx_total_nd(None, base=[{"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"}])
    ctx.scenarios["conservador"]["modelos_excluidos_base"] = [{"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"}]
    for code in _ND_CODIGOS:
        assert ai_explain._build_nd_por_que(ctx, code) is None
        assert _T_FCF not in _msg(code, None, base=[{"modelo": "dcf", "motivo": "fcf_reciente_no_positivo"}])


# --- CS-ND-13: los motivos nunca viajan a Gemini ---------------------------------

_CANARIO_MOTIVO = "CANARIO_MOTIVO_ZZ"
_PRESENTABLES = [
    c
    for c in list(ai_explain_content.QUESTIONS_TEXTO_LIBRE) + list(ai_explain_content.QUESTIONS_AVANZADO)
    if c not in ("evt", "inf")
]


def _ctx_con_excluidos_y_detalle(code):
    if code in ai_explain_content.QUESTIONS_AVANZADO:
        return _avanzado_context()
    excluidos = [
        {"modelo": "multiplos", "motivo": "eps_ttm_no_positivo", "detalle": "eps_no_disponible"},
        {"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": "wacc_deuda_no_disponible"},
    ]
    ctx = _nd_ctx_total_nd(excluidos)
    scen = copy.deepcopy(ctx.scenarios)
    scen["modelos_excluidos_base"] = [{"modelo": "graham", "motivo": _CANARIO_MOTIVO}]
    for esc in _ESCENARIOS:
        scen[esc]["modelos_excluidos"] = excluidos
        scen[esc]["modelos_excluidos_base"] = [{"modelo": "graham", "motivo": _CANARIO_MOTIVO}]
    return _texto_libre_context(scenarios=scen, escenario_elegido="conservador")


def test_nd_cs13_el_conjunto_cubre_los_25_codigos():
    assert len(_PRESENTABLES) == 25


@pytest.mark.parametrize("code", _PRESENTABLES)
def test_nd_cs13_modelos_excluidos_y_detalle_no_viajan_al_modelo(code):
    ctx = _ctx_con_excluidos_y_detalle(code)
    crudo = ai_explain._build_explain_payload(ctx, code)
    presentado = ai_explain._datos_para_el_modelo(code, crudo)
    for payload in (crudo, presentado):
        js = json.dumps(payload, ensure_ascii=False, default=str)
        for prohibido in (
            "modelos_excluidos", "modelos_excluidos_base", "detalle", _CANARIO_MOTIVO,
            "eps_no_disponible", "wacc_deuda_no_disponible", "eps_ttm_no_positivo", "wacc_no_calculable",
        ):
            assert prohibido not in js, (code, prohibido)
        for texto in summary.DETALLE_LABELS.values():
            assert texto not in js


# --- CS-ND-3: tablas ND_* -----------------------------------------------------------

_ND_TABLAS_TEXTO = (
    list(ai_explain_content.ND_AVISO.values())
    + list(ai_explain_content.ND_TITULO.values())
    + [ai_explain_content.ND_ENCABEZADO_POR_QUE, ai_explain_content.ND_SIN_MOTIVO]
)


def test_nd_cs3_tablas_nd_sin_markdown_ni_digitos_ni_punto_final_de_motivo():
    assert set(ai_explain_content.ND_AVISO) == set(ai_explain_content.ND_TITULO) == set(_ND_CODIGOS)
    for texto in _ND_TABLAS_TEXTO:
        assert isinstance(texto, str) and texto
        assert not any(c in texto for c in "*_`[]"), texto
        assert not any(ch.isdigit() for ch in texto), texto
    assert not ai_explain_content.ND_SIN_MOTIVO.endswith(".")


def test_nd_cs3_nd_sin_motivo_es_igual_a_motivo_generico_m20():
    assert ai_explain_content.ND_SIN_MOTIVO == summary.MOTIVO_GENERICO
    assert ai_explain_content.ND_SIN_MOTIVO == "no pude identificar el motivo exacto con los datos de esta consulta"


def test_nd_cs3_tablas_nd_son_los_textos_exactos_de_15_5_y_no_estan_en_section_labels():
    assert ai_explain_content.ND_ENCABEZADO_POR_QUE == "🔎 Por qué:"
    assert ai_explain_content.ND_TITULO == {
        "vf": "No se pudo calcular el Valor Justo Total",
        "ver": "No hay veredicto de barata o cara",
        "gra": "Graham no pudo calcular un valor",
        "dcf": "DCF no pudo calcular un valor",
        "mul": "Múltiplos no pudo calcular un valor",
    }
    comun = "Eso no quiere decir que la empresa sea buena o mala: solo que "
    assert ai_explain_content.ND_AVISO["vf"] == (
        "Con los datos de hoy, ninguno de los modelos del bot (Múltiplos, Graham y DCF) pudo dar un valor "
        "para esta empresa, así que no hay un Valor Justo para explicar. " + comun +
        "estos modelos necesitan datos que hoy faltan o no aplican. Abajo ves qué le falta a cada uno."
    )
    assert ai_explain_content.ND_AVISO["ver"] == (
        "El veredicto compara el precio de hoy con el Valor Justo Total, y ese valor no se pudo calcular con "
        "ningún modelo. Sin esa referencia el bot no puede decirte si está barata o cara. "
        "Eso no dice nada sobre la empresa: solo que falta con qué comparar."
    )
    assert ai_explain_content.ND_AVISO["gra"] == (
        "El modelo de Graham no dio un valor para este escenario, así que no hay un resultado para explicar. "
        + comun + "este modelo no aplica o le faltan datos."
    )
    assert ai_explain_content.ND_AVISO["dcf"] == (
        "El modelo DCF (flujo de caja descontado) no dio un valor para este escenario, así que no hay un "
        "resultado para explicar. " + comun + "este modelo no aplica o le faltan datos."
    )
    assert ai_explain_content.ND_AVISO["mul"] == (
        "El modelo de Múltiplos no dio un valor para este escenario, así que no hay un resultado para "
        "explicar. " + comun + "este modelo no aplica o le faltan datos."
    )
    for code in ("ver", "gra", "dcf", "mul"):
        assert "Abajo ves" not in ai_explain_content.ND_AVISO[code]
    etiquetas = {e for trio in ai_explain.SECTION_LABELS.values() for e in trio}
    for texto in _ND_TABLAS_TEXTO:
        assert texto not in etiquetas
        assert texto not in ai_explain._ALL_LABELS
    for texto in ai_explain_content.ND_AVISO.values():
        assert texto not in ai_explain._ALL_LABELS


# --- CS-ND-16 (parcial): tokens protegidos de `_MODELO_VF` --------------------------


def test_nd_cs16_tokens_protegidos_de_modelo_vf_son_solo_eps_y_dcf():
    assert ai_rewrite._protected_tokens(ai_explain._MODELO_VF) == ["DCF", "EPS"]
    assert "Múltiplos" not in ai_rewrite._protected_tokens(ai_explain._MODELO_VF)


# ---------------------------------------------------------------------------
# F4b -- handler N/D (CA-ND-12, 14, 16; CS-ND-1, 4, 8, 9, 12, 14, 16, 17) y P-3
# ---------------------------------------------------------------------------

_CHAT = 424242
_TICK_CANARIO = "ZQXJ"
_EMPRESA_CANARIA = "Zqxj Canario Corporation"
_PRECIO_CANARIO = 98765.4321
_RESPUESTA_GEMINI = "💡 En simple\nRespuesta simulada de Gemini."


def _ctx_handler(valores=None, *, excluidos=_EX_CLPT, chat_id=_CHAT, **top):
    """Contexto de `texto_libre` con TODOS los valores N/D (o `valores`) en el escenario elegido."""
    top.setdefault("ticker", _TICK_CANARIO)
    top.setdefault("company_name", _EMPRESA_CANARIA)
    top.setdefault("precio_actual", _PRECIO_CANARIO)
    return _nd_ctx(_VALORES_ND if valores is None else valores, excluidos=excluidos, chat_id=chat_id, **top)


class _Harness:
    """Callback real de `build_explain_handler` con Gemini simulado que cuenta requests."""

    def __init__(self, ctx, *, limiter=None, chat_id=None):
        self.gemini_requests = []
        self.limiter = limiter if limiter is not None else FakeRateLimiter()
        self.store = ai_explain.ExplanationContextStore()
        self.cid = self.store.put(ctx)
        self.chat_id = ctx.chat_id if chat_id is None else chat_id

        def handler(request):
            self.gemini_requests.append(request)
            return httpx.Response(200, json=gemini_response(_RESPUESTA_GEMINI))

        clients = _make_clients(http_client=_client_with_handler(handler), gemini_config=_enabled_config())
        self.callback = _build_callback(clients, self.limiter, self.store)

    async def click(self, code, *, paso_a_paso=True):
        data = f"xp:{self.cid}:p:{code}" if paso_a_paso else f"xp:{self.cid}:{code}"
        update, _query, context = _fake_callback_update(data, chat_id=self.chat_id)
        await self.callback(update, context)
        self.bot = context.bot
        return context.bot

    def contenidos(self):
        return [c.kwargs["text"] for c in self.bot.send_message.call_args_list]


def _por_que_esperado(code):
    if code == "ver":
        return summary.build_motivos_resumen(_EX_CLPT)
    return {"gra": _T_EPS, "dcf": _T_FCF, "mul": _T_EPS}[code]


# --- CA-ND-12 ------------------------------------------------------------------


@pytest.mark.parametrize("code", _ND_CODIGOS)
async def test_nd_ca12_total_nd_responde_determinista_sin_gemini_ni_balde(code):
    h = _Harness(_ctx_handler())
    bot = await h.click(code)
    assert h.gemini_requests == []
    assert h.limiter.calls_with_key == []
    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == _CHAT
    assert kwargs["reply_markup"] is not None
    texto = kwargs["text"]
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX)
    assert ai_explain_content.ND_AVISO[code] in texto
    assert ai_explain_content.ND_TITULO[code] in texto
    if code == "vf":
        assert "🔍 Desglose" in texto
        assert "Motivo: " in texto
    else:
        assert f"{ai_explain_content.ND_ENCABEZADO_POR_QUE} {_por_que_esperado(code)}." in texto
    assert "🤖 Con ayuda de Gemini" not in texto
    assert ai_rewrite.TRANSPARENCY_USED not in texto
    assert "None" not in texto
    assert ai_explain.EXPLAIN_PENDING_MSG not in h.contenidos()


@pytest.mark.parametrize("code", ("gra", "dcf", "mul"))
async def test_nd_ca12_solo_el_escenario_elegido_dispara_con_total_valido(code):
    # El valor del modelo es N/D en el escenario elegido aunque el total exista (Q-5).
    clave = _ND_CLAVES[code]
    h = _Harness(_ctx_handler({clave: None}))
    bot = await h.click(code)
    assert h.gemini_requests == []
    assert h.limiter.calls_with_key == []
    bot.send_message.assert_awaited_once()
    assert ai_explain_content.ND_AVISO[code] in bot.send_message.call_args.kwargs["text"]


# --- CA-ND-14 / CS-ND-8 --------------------------------------------------------


async def _assert_cae_a_gemini_una_sola_vez(h, code="vf"):
    bot = await h.click(code)
    assert len(h.gemini_requests) == 1
    assert h.limiter.calls_with_key == [str(_CHAT)]
    bot.edit_message_text.assert_awaited_once()  # la única respuesta final
    assert bot.edit_message_text.call_args.kwargs["text"] != ai_explain.EXPLAIN_UNAVAILABLE_MSG
    # El único `send_message` es el "🤔 Pensando…" del flujo de Gemini; no hay mensaje N/D.
    assert h.contenidos() == [ai_explain.EXPLAIN_PENDING_MSG]
    return bot


async def test_nd_ca14_builder_que_devuelve_none_cae_a_gemini_una_sola_vez(monkeypatch):
    monkeypatch.setattr(ai_explain, "_build_nd_message", lambda context, code: None)
    await _assert_cae_a_gemini_una_sola_vez(_Harness(_ctx_handler()))


async def test_nd_cs8_nd_aplica_que_lanza_cae_a_gemini_una_sola_vez_y_loguea_sin_exc(monkeypatch, caplog):
    def explota(context, code):
        raise RuntimeError("SECRETO_DEL_EXC_ZZ")

    monkeypatch.setattr(ai_explain, "_nd_aplica", explota)
    caplog.set_level(logging.DEBUG, logger="investbot.ai_explain")
    await _assert_cae_a_gemini_una_sola_vez(_Harness(_ctx_handler()))
    recs = [r for r in caplog.records if "EXPLAIN_ND_FALLO" in r.getMessage()]
    assert [r.getMessage() for r in recs] == ["EXPLAIN_ND_FALLO code=vf tipo=RuntimeError"]
    assert recs[0].exc_info is None and recs[0].levelno == logging.WARNING
    assert "SECRETO_DEL_EXC_ZZ" not in caplog.text and "Traceback" not in caplog.text
    assert not any("EXPLAIN_ND_DETERMINISTICO" in r.getMessage() for r in caplog.records)


async def test_nd_cs8_builder_que_lanza_cae_a_gemini_una_sola_vez_y_loguea_sin_exc(monkeypatch, caplog):
    def explota(context, code):
        raise ValueError("SECRETO_DEL_BUILDER_ZZ")

    monkeypatch.setattr(ai_explain, "_build_nd_message", explota)
    caplog.set_level(logging.DEBUG, logger="investbot.ai_explain")
    await _assert_cae_a_gemini_una_sola_vez(_Harness(_ctx_handler()))
    msgs = [r.getMessage() for r in caplog.records if "EXPLAIN_ND_FALLO" in r.getMessage()]
    assert msgs == ["EXPLAIN_ND_FALLO code=vf tipo=ValueError"]
    assert "SECRETO_DEL_BUILDER_ZZ" not in caplog.text and "Traceback" not in caplog.text


@pytest.mark.parametrize("excluidos", ["no-es-lista", None, [1, "x", None], [{"modelo": ["x"], "motivo": 3, "detalle": []}]])
async def test_nd_cs8_excluidos_hostiles_con_total_nd_dan_una_sola_respuesta_determinista(excluidos):
    h = _Harness(_ctx_handler(excluidos=excluidos))
    bot = await h.click("vf")
    assert h.gemini_requests == [] and h.limiter.calls_with_key == []
    bot.send_message.assert_awaited_once()
    texto = bot.send_message.call_args.kwargs["text"]
    assert texto.startswith(ai_explain.DETERMINISTIC_PREFIX) and "None" not in texto


# --- CA-ND-16 ------------------------------------------------------------------


async def test_nd_ca16_total_valido_con_sub_modelos_nd_sigue_llamando_a_gemini(monkeypatch):
    llamadas = []
    real = ai_explain._fetch_explanation

    async def espia(**kw):
        llamadas.append(kw["question_code"])
        return await real(**kw)

    monkeypatch.setattr(ai_explain, "_fetch_explanation", espia)
    valores = {"valor_justo_multiplos": None, "valor_justo_graham": None}  # total válido (496.0)
    for code in ("vf", "ver"):
        h = _Harness(_ctx_handler(valores))
        bot = await h.click(code)
        bot.edit_message_text.assert_awaited_once()
        assert len(h.gemini_requests) == 1
        assert h.limiter.calls_with_key == [str(_CHAT)]
    assert llamadas == ["vf", "ver"]


async def test_nd_ca16_modelo_con_valor_en_el_escenario_elegido_sigue_llamando_a_gemini():
    # `dcf` válido en el escenario elegido aunque Graham y Múltiplos sean N/D.
    h = _Harness(_ctx_handler({"valor_justo_graham": None, "valor_justo_multiplos": None}))
    await h.click("dcf")
    assert len(h.gemini_requests) == 1


async def test_nd_ca16_fixture_parcial_sin_la_clave_no_dispara_la_rama():
    # R-2: escenarios parciales de HEAD (sin la clave) siguen el flujo normal.
    ctx = _texto_libre_context(chat_id=_CHAT, scenarios={"conservador": {}}, escenario_elegido="conservador")
    h = _Harness(ctx)
    await h.click("vf")
    assert len(h.gemini_requests) == 1


async def test_nd_ca16_kind_avanzado_con_paso_a_paso_no_consulta_la_rama_nd(monkeypatch):
    llamadas = []
    monkeypatch.setattr(ai_explain, "_nd_aplica", lambda context, code: llamadas.append(code) or True)
    h = _Harness(_avanzado_context(chat_id=_CHAT))
    await h.click("alz")
    assert llamadas == []
    assert len(h.gemini_requests) == 1


# --- CS-ND-4 (IDOR) ------------------------------------------------------------


@pytest.mark.parametrize("code", _ND_CODIGOS)
async def test_nd_cs4_contexto_de_otro_chat_da_expirado_sin_datos_del_dueno(code):
    ctx = _ctx_handler(chat_id=111)
    h = _Harness(ctx, chat_id=222)
    bot = await h.click(code)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["text"] == ai_explain.EXPLAIN_EXPIRED_MSG
    bot.edit_message_text.assert_not_awaited()
    assert h.gemini_requests == [] and h.limiter.calls_with_key == []
    for dato in (_TICK_CANARIO, _EMPRESA_CANARIA, str(_PRECIO_CANARIO), "Motivo", "Desglose"):
        assert dato not in bot.send_message.call_args.kwargs["text"]


async def test_nd_cs4_contexto_inexistente_o_vencido_da_expirado():
    h = _Harness(_ctx_handler())
    h.cid = "deadbeef"
    bot = await h.click("vf")
    assert bot.send_message.call_args.kwargs["text"] == ai_explain.EXPLAIN_EXPIRED_MSG
    assert h.gemini_requests == []


# --- CS-ND-9 -------------------------------------------------------------------


@pytest.mark.parametrize("code", _ND_CODIGOS)
async def test_nd_cs9_si_send_message_falla_no_cae_a_gemini_ni_toca_el_balde(code, caplog):
    h = _Harness(_ctx_handler())
    update, _q, context = _fake_callback_update(f"xp:{h.cid}:p:{code}", chat_id=_CHAT)
    context.bot.send_message.side_effect = TelegramError("RetryAfter simulado SECRETO_TG")
    caplog.set_level(logging.DEBUG, logger="investbot.ai_explain")
    await h.callback(update, context)  # la excepción no sube
    assert h.gemini_requests == []
    assert h.limiter.calls_with_key == []
    assert context.bot.send_message.await_count == 1  # sin segundo envío de contenido
    context.bot.edit_message_text.assert_not_awaited()
    msgs = [r.getMessage() for r in caplog.records if "EXPLAIN_ND_ENTREGA_FALLIDA" in r.getMessage()]
    assert msgs == [f"EXPLAIN_ND_ENTREGA_FALLIDA code={code}"]
    assert "SECRETO_TG" not in caplog.text


# --- CS-ND-12 (orden con spies) ------------------------------------------------


def _instalar_espias(monkeypatch, h):
    eventos = []

    def envolver(nombre, etiqueta=None):
        real = getattr(ai_explain, nombre)

        def espia(*a, **kw):
            eventos.append(etiqueta or nombre)
            return real(*a, **kw)

        monkeypatch.setattr(ai_explain, nombre, espia)

    for nombre in ("_get_owned_context", "_build_ver_dato_content", "_nd_aplica",
                   "_build_nd_message", "_build_explain_payload"):
        envolver(nombre)
    real_allow = h.limiter.allow

    def allow(key):
        eventos.append("allow")
        return real_allow(key)

    h.limiter.allow = allow
    return eventos


async def test_nd_cs12_orden_idor_antes_que_todo(monkeypatch):
    h = _Harness(_ctx_handler(chat_id=111), chat_id=222)
    eventos = _instalar_espias(monkeypatch, h)
    await h.click("vf")
    assert eventos == ["_get_owned_context"]


async def test_nd_cs12_orden_ver_dato_antes_de_la_rama_nd(monkeypatch):
    h = _Harness(_ctx_handler())
    eventos = _instalar_espias(monkeypatch, h)
    bot = await h.click("vf", paso_a_paso=False)
    assert eventos[0] == "_get_owned_context"
    assert "_build_ver_dato_content" in eventos
    assert "_build_nd_message" not in eventos and "allow" not in eventos
    assert "_build_explain_payload" not in eventos or eventos.index("_build_explain_payload") > eventos.index("_build_ver_dato_content")
    assert h.gemini_requests == []
    assert "Motivo" not in bot.send_message.call_args.kwargs["text"]  # es "Ver dato", no el mensaje N/D
    assert ai_explain_content.ND_AVISO["vf"] not in bot.send_message.call_args.kwargs["text"]


async def test_nd_cs12_orden_rama_nd_antes_del_balde_y_del_payload(monkeypatch):
    h = _Harness(_ctx_handler())
    eventos = _instalar_espias(monkeypatch, h)
    await h.click("vf")
    assert eventos[:3] == ["_get_owned_context", "_nd_aplica", "_build_nd_message"]
    assert "allow" not in eventos
    assert h.gemini_requests == []


async def test_nd_cs12_orden_flujo_normal_balde_antes_del_payload(monkeypatch):
    h = _Harness(_ctx_handler({"valor_justo_graham": None}))  # total válido: sin N/D
    eventos = _instalar_espias(monkeypatch, h)
    await h.click("vf")
    assert eventos[:3] == ["_get_owned_context", "_nd_aplica", "allow"]
    assert eventos.index("allow") < eventos.index("_build_explain_payload")
    assert "_build_nd_message" not in eventos


async def test_nd_cs12_rate_limited_sin_nd_responde_el_mensaje_de_limite():
    h = _Harness(_ctx_handler({"valor_justo_graham": None}), limiter=FakeRateLimiter(allow_value=False))
    bot = await h.click("vf")
    assert h.contenidos() == [ai_explain.RATE_LIMITED_MSG]
    assert h.gemini_requests == []


async def test_nd_cs12_con_balde_agotado_la_rama_nd_igual_responde():
    h = _Harness(_ctx_handler(), limiter=FakeRateLimiter(allow_value=False))
    bot = await h.click("vf")
    assert ai_explain.RATE_LIMITED_MSG not in h.contenidos()
    assert ai_explain_content.ND_AVISO["vf"] in h.contenidos()[0]


# --- CS-ND-14 ------------------------------------------------------------------


@pytest.mark.parametrize("code", _ND_CODIGOS)
async def test_nd_cs14_sin_parse_mode_en_limite_y_sin_marca_de_gemini(code):
    h = _Harness(_ctx_handler())
    bot = await h.click(code)
    kwargs = bot.send_message.call_args.kwargs
    assert "parse_mode" not in kwargs
    assert set(kwargs) == {"chat_id", "text", "reply_markup"}
    texto = kwargs["text"]
    assert ai_explain._len16(texto) <= 4096
    assert "🤖 Con ayuda de Gemini" not in texto
    assert ai_rewrite.TRANSPARENCY_USED not in texto


async def test_nd_cs14_no_bmp_y_tope_enorme_siguen_en_4096_por_el_handler(monkeypatch):
    monkeypatch.setattr(ai_explain, "_MAX_DESGLOSE_CHARS", 10**6)
    etiquetas = {k: "😀" * 2000 for k in summary.DETALLE_LABELS}
    monkeypatch.setattr(summary, "DETALLE_LABELS", etiquetas)
    excl = [{"modelo": "multiplos", "motivo": "eps_ttm_no_positivo", "detalle": "eps_no_disponible"},
            {"modelo": "dcf", "motivo": "wacc_no_calculable", "detalle": "wacc_deuda_no_disponible"}]
    h = _Harness(_ctx_handler(excluidos=excl))
    bot = await h.click("vf")
    texto = bot.send_message.call_args.kwargs["text"]
    assert ai_explain._len16(texto) <= 4096
    assert ai_explain_content.ND_AVISO["vf"] in texto


# --- CS-ND-17 ------------------------------------------------------------------


@pytest.mark.parametrize("code", _ND_CODIGOS)
async def test_nd_cs17_log_deterministico_sin_canarios(code, caplog):
    h = _Harness(_ctx_handler())
    caplog.set_level(logging.DEBUG)
    await h.click(code)
    msgs = [r.getMessage() for r in caplog.records if "EXPLAIN_ND_DETERMINISTICO" in r.getMessage()]
    assert msgs == [f"EXPLAIN_ND_DETERMINISTICO code={code}"]
    for prohibido in (str(_CHAT), h.cid, _TICK_CANARIO, _EMPRESA_CANARIA, str(_PRECIO_CANARIO), "98765"):
        assert prohibido not in caplog.text, prohibido


# --- CS-ND-16 / P-3 (string de `_MODELO_VF`) -------------------------------------

_MODELO_VF_HEAD = "Graham (EPS) + DCF (flujo de caja descontado)"


def test_nd_cs16_modelo_vf_y_ver_tienen_el_string_de_q6():
    assert ai_explain._MODELO_VF == "Múltiplos + Graham (EPS) + DCF (flujo de caja descontado)"
    assert ai_explain._MODELO_VER == f"{ai_explain._MODELO_VF} + {ai_explain._MODELO_PIL}"
    assert ai_explain._MODELO_VER == (
        "Múltiplos + Graham (EPS) + DCF (flujo de caja descontado) + los 4 pilares de calidad fundamental"
    )


def test_nd_cs16_tokens_protegidos_de_modelo_vf_iguales_a_head_y_sin_nada_nuevo():
    nuevo = ai_rewrite._protected_tokens(ai_explain._MODELO_VF)
    assert nuevo == ai_rewrite._protected_tokens(_MODELO_VF_HEAD) == ["DCF", "EPS"]
    assert set(nuevo) == set(ai_rewrite._protected_tokens(_MODELO_VF_HEAD))
    ver = ai_rewrite._protected_tokens(ai_explain._MODELO_VER)
    assert set(ver) == set(ai_rewrite._protected_tokens(f"{_MODELO_VF_HEAD} + {ai_explain._MODELO_PIL}"))


def test_nd_cs16_el_payload_de_vf_lleva_el_modelo_nuevo_en_la_clave_modelo():
    ctx = _texto_libre_context()
    assert ai_explain._build_explain_payload(ctx, "vf")["modelo"] == ai_explain._MODELO_VF
    assert ai_explain._build_explain_payload(ctx, "ver")["modelo"] == ai_explain._MODELO_VER


# --- CS-ND-1: prompts intactos ----------------------------------------------------


_SHA256_PROMPTS_HEAD = {
    "SYSTEM_PROMPT_PASO_A_PASO": "b2424ca37b9cbd1416462b05c4289f431b50098a360ca268d1dac80f1cac7124",
    "SYSTEM_PROMPT_EXPLAIN": "fc50b5dcef5278b9e25adc2535185092f96302bb1495ccffb005066fc31cb2f9",
}


@pytest.mark.parametrize("nombre", sorted(_SHA256_PROMPTS_HEAD))
def test_nd_cs1_sha256_completo_de_los_prompts_de_head(nombre):
    prompt = getattr(ai_explain, nombre)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == _SHA256_PROMPTS_HEAD[nombre]


def test_nd_cs1_el_snapshot_cs26_existe_y_cubre_los_25_codigos_sin_cambios():
    # El snapshot vive en tests/test_f1_redaccion.py (no se modifica); acá se fija que
    # sigue colectable y que sus 3 familias de tests están presentes.
    fuente = (Path(__file__).parent / "test_f1_redaccion.py").read_text(encoding="utf-8")
    for nombre in ("test_cs26_b_snapshot_claves_str", "test_cs26_b_el_snapshot_cubre_los_25_codigos",
                   "test_cs26_a_canarios_ausentes"):
        assert f"def {nombre}" in fuente
