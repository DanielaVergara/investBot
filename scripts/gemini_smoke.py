#!/usr/bin/env python3
"""Smoke VIVO contra Gemini (SDD_migracion_gemini.md, 3.11 / 13.2 E-5 / 15.2 P-6).

NO es un test de pytest, NO se importa desde `src` y NO entra a la imagen (el
`Dockerfile` solo copia `src/`). Se corre a mano, con una key de pruebas
propia, desde la raíz del repo:

    GEMINI_LIVE_KEY=<key de pruebas> GEMINI_LIVE_CONFIRM=yes \\
        python scripts/gemini_smoke.py [--explain-reps 5] [--rewrite-samples 10] [--rewrite-reps 3] \\
            [--show-text [--show-text-max 6]] [--thinking {minimal,low}]

`--show-text` (apagado por defecto; SDD Iter-2 CS-28 C1-C9) imprime el texto de
las explicaciones del modelo (nunca del rewrite) SOLO tras `_safe_display` (lista
blanca de categorías Unicode, tope de caracteres y de líneas) y nunca imprime un
texto que el filtro de salida rechaza. No escribe archivos.

Corre el camino REAL de las explicaciones (handler `xp:` -> `_fetch_explanation`)
y del rewrite (`rewrite_parts`) con contextos 100% SINTÉTICOS con canarios
visibles (`ZZCANARY`, `CANARY_EMPRESA_SA`): nunca datos de FMP ni un chat real.
Mide los criterios CA-S1 a CA-S6 y los imprime como PASA/FALLA; el script nunca
decide por sí solo cambiar nada (un FALLA es evidencia para un spec patch).

Seguridad: construye la `GeminiConfig` explícitamente desde `GEMINI_LIVE_KEY`
(no lee todo el entorno del proceso; la variable de la key de producción solo se
lee para la guarda de comparación); no carga archivos de entorno; nunca imprime la key, headers ni
el cuerpo de una respuesta (solo status, token, modelo, tokens y `ms`); tope
duro de 150 llamadas; no escribe archivos (solo stdout).

Códigos de salida de `main`: 0 = la corrida terminó (los PASA/FALLA están en el
resumen); 2 = abortado por una guarda; 3 = config inválida; 4 = tope de
llamadas excedido; 5 = error inesperado; 64 = argumentos inválidos.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import re
import sys
import time
from os import environ
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional

import httpx

CALL_LIMIT = 150
UNAVAILABLE_RC = 2
SHOW_TEXT_DEFAULT = 6
SHOW_TEXT_MAX = 30
SHOW_TEXT_CHARS = 2500
THINKING_CHOICES = ("minimal", "low")

# Tokens de razón fijos de las guardas (nunca contienen valores).
GUARD_OK = "GUARD_OK"
GUARD_NO_LIVE_KEY = "GUARD_NO_LIVE_KEY"
GUARD_NO_CONFIRM = "GUARD_NO_CONFIRM"
GUARD_CI = "GUARD_CI"
GUARD_GITHUB_ACTIONS = "GUARD_GITHUB_ACTIONS"
GUARD_SAME_KEY = "GUARD_SAME_KEY_AS_API_KEY"
GUARD_TELEGRAM_TOKEN = "GUARD_TELEGRAM_BOT_TOKEN_SET"
GUARD_FMP_KEY = "GUARD_FMP_API_KEY_SET"
GUARD_IN_CONTAINER = "GUARD_IN_CONTAINER"


class CallBudgetExceeded(RuntimeError):
    """Se superó el tope duro de llamadas a Gemini de una corrida."""


class CallBudget:
    def __init__(self, limit: int = CALL_LIMIT) -> None:
        self.limit = limit
        self.spent = 0

    def spend(self) -> None:
        """Registra una llamada; lanza en la llamada `limit + 1`."""
        if self.spent >= self.limit:
            raise CallBudgetExceeded("tope de llamadas excedido")
        self.spent += 1


def evaluate_guards(env: Mapping[str, str], in_container: bool) -> tuple[bool, str]:
    """Función pura: `(permitido, token_de_razon)`. El token es fijo y nunca
    contiene valores de variables."""
    if env.get("CI"):
        return False, GUARD_CI
    if env.get("GITHUB_ACTIONS"):
        return False, GUARD_GITHUB_ACTIONS
    if in_container:
        return False, GUARD_IN_CONTAINER
    if env.get("TELEGRAM_BOT_TOKEN"):
        return False, GUARD_TELEGRAM_TOKEN
    if env.get("FMP_API_KEY"):
        return False, GUARD_FMP_KEY
    live_key = (env.get("GEMINI_LIVE_KEY") or "").strip()
    if not live_key:
        return False, GUARD_NO_LIVE_KEY
    if env.get("GEMINI_LIVE_CONFIRM") != "yes":
        return False, GUARD_NO_CONFIRM
    if live_key == (env.get("GEMINI_API_KEY") or "").strip():
        return False, GUARD_SAME_KEY
    return True, GUARD_OK


# --- Telemetría -------------------------------------------------------------


class _MeteredClient:
    """Envuelve el `post` del cliente: cuenta contra el tope y registra, por
    llamada, solo metadatos (status, ms, finishReason, tokens, texto en
    memoria para clasificar). Nunca imprime ni guarda cuerpos de error."""

    def __init__(self, inner: httpx.AsyncClient, budget: CallBudget) -> None:
        self._inner = inner
        self._budget = budget
        self.records: list[dict[str, Any]] = []
        self.exceeded = False

    async def post(self, url, *, headers=None, json=None, timeout=None, follow_redirects=None):
        try:
            self._budget.spend()
        except CallBudgetExceeded:
            self.exceeded = True
            raise
        record: dict[str, Any] = {
            "status": None, "ms": None, "finish": None, "prompt": None,
            "output": None, "thoughts": None, "served": None, "text": None,
        }
        self.records.append(record)
        started = time.monotonic()
        try:
            response = await self._inner.post(
                url, headers=headers, json=json, timeout=timeout, follow_redirects=follow_redirects
            )
        finally:
            record["ms"] = int((time.monotonic() - started) * 1000)
        record["status"] = response.status_code
        if response.status_code == 200:
            _fill_from_body(record, response)
        return response


def _fill_from_body(record: dict[str, Any], response: httpx.Response) -> None:
    try:
        data = response.json()
        cand = data["candidates"][0]
    except Exception:
        return
    record["finish"] = cand.get("finishReason") if isinstance(cand, dict) else None
    usage = data.get("usageMetadata") if isinstance(data, dict) else None
    if isinstance(usage, dict):
        record["prompt"] = usage.get("promptTokenCount")
        record["output"] = usage.get("candidatesTokenCount")
        record["thoughts"] = usage.get("thoughtsTokenCount")
    if isinstance(data, dict) and isinstance(data.get("modelVersion"), str):
        record["served"] = data["modelVersion"]
    try:
        parts = cand["content"]["parts"]
        record["text"] = "".join(
            p["text"] for p in parts if isinstance(p, dict) and p.get("thought") is not True
            and isinstance(p.get("text"), str)
        ).strip()
    except Exception:
        record["text"] = None


def _p95(values: list[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)]


def _p50(values: list[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(0.5 * len(ordered)) - 1, 0)]


def _verdict(ok: bool) -> str:
    return "PASA" if ok else "FALLA"


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


# --- Contextos sintéticos (canarios visibles, cero datos reales) ---------------


def _texto_libre_context(ai_explain, chat_id: int):
    scen = lambda vj, g, d, tot, ga, w, gf, fb, pv, pt, eq: {  # noqa: E731
        "valor_justo_multiplos": vj, "valor_justo_graham": g, "valor_justo_dcf": d,
        "valor_justo_total": tot, "graham_g_aplicado": ga, "dcf_wacc": w, "dcf_g_fcf": gf,
        "dcf_fcf_base": fb, "dcf_valor_presente_flujos": pv, "dcf_valor_terminal_descontado": pt,
        "dcf_equity_value": eq,
    }
    return ai_explain.ExplanationContext(
        chat_id=chat_id, kind="texto_libre", ticker="ZZCANARY", company_name="CANARY_EMPRESA_SA",
        escenario_elegido="conservador", precio_actual=550.0,
        scenarios={
            "pesimista": scen(460.0, 440.0, 470.0, 456.7, 0.06, 0.10, 0.053, 100.0, 400.0, 900.0, 1300.0),
            "conservador": scen(500.0, 480.0, 510.0, 496.0, 0.094, 0.091, 0.083, 109.0, 612.0, 2100.0, 2712.0),
            "optimista": scen(540.0, 520.0, 550.0, 536.7, 0.124, 0.081, 0.113, 118.0, 800.0, 3200.0, 4000.0),
        },
        pillars={"ingresos_crecientes": True, "utilidades_crecientes": True,
                 "deuda_controlada": True, "precio_razonable": False},
        veredicto_barata=True,
        ratios={"ratio_liquidez": 1.8, "liquidez_sin_pasivos_circulantes": False,
                "margen_bruto": 0.65, "per": 22.5, "per_no_aplicable": False, "ps": 6.2},
        risk_fit={"encaja": True, "perfil": "moderado", "beta": 1.15,
                  "etiqueta_activo": "acción de riesgo moderado"},
        momentum={"pct_vs_year_high": -8.2, "pct_vs_year_low": 25.0, "pct_vs_avg_50": 3.1,
                  "pct_vs_avg_200": 9.4, "etiqueta": "impulso_positivo"},
        peer_comparison={"per_propio": 22.5, "per_minimo_peers": 18.0, "per_promedio_peers": 24.0,
                         "per_maximo_peers": 30.0, "peers_usados": ["ZZPEER", "ZZPEERB"],
                         "posicion": "en_linea", "motivo_no_comparable": None},
        extras={"roe": 0.28, "debt_to_equity": 0.4, "net_debt_to_ebitda": 0.9,
                "dividend_yield": 0.0, "payout_ratio": None},
        vix={"valor": 16.2, "disponible": True}, corporate_events=[],
        treasury_source="FRED", balance_sheet_fuente="trimestral",
        income_statement_fuente="trimestral", cash_flow_fuente="trimestral",
        peers_note="nota sintética", eps_ttm=8.2, y_value=0.042, current_assets=100.0,
        current_liabilities=50.0, revenue=1000.0, cost_of_revenue=400.0, market_cap=2_000_000.0,
        revenue_reciente=1000.0, revenue_antiguo=800.0, net_income_reciente=200.0,
        net_income_antiguo=150.0, year_high=600.0, year_low=400.0, price_avg_50=540.0,
        price_avg_200=520.0,
    )


def _avanzado_context(ai_explain, chat_id: int):
    crit = lambda n, c, v: {"nombre": n, "cumplido": c, "valores": v}  # noqa: E731
    return ai_explain.ExplanationContext(
        chat_id=chat_id, kind="avanzado", ticker="ZZCANARY", company_name="CANARY_EMPRESA_SA",
        sector="Technology", industry="CANARY_INDUSTRIA", asset_light=True,
        altman={"disponible": True, "z": 3.5, "zona": "segura", "campos_faltantes": [],
                "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": 0.87},
        altman_pp={"disponible": True, "z": 5.2, "zona": "segura", "campos_faltantes": [],
                   "a": 0.34, "b": 0.12, "c": 0.18, "d": 1.05, "e": None},
        piotroski={"puntaje": 7, "criterios_evaluables": 9, "criterios_totales": 9, "criterios": [
            crit("roa_positivo", True, {"net_income_t": 118.0}),
            crit("cfo_positivo", True, {"cfo_t": 118.0}),
            crit("roa_creciente", True, {"roa_t": 0.18, "roa_t1": 0.15}),
            crit("cfo_mayor_utilidad", False, {"cfo_t": 118.0, "net_income_t": 97.0}),
            crit("apalancamiento_decreciente", True, {"apalancamiento_t": 0.2, "apalancamiento_t1": 0.3}),
            crit("liquidez_creciente", False, {"liquidez_t": 1.1, "liquidez_t1": 1.3}),
            crit("sin_dilucion", True, {"shares_t": 100.0, "shares_t1": 102.0}),
            crit("margen_bruto_creciente", True, {"margen_t": 0.43, "margen_t1": 0.40}),
            crit("rotacion_activos_creciente", False, {"rotacion_t": 0.9, "rotacion_t1": 0.95}),
        ]},
        beneish={"disponible": False, "motivo": "no_calculable_con_datos_disponibles"},
        magic={"disponible": True, "roic": 0.2, "earnings_yield": 0.08, "campos_faltantes": [],
               "ebit": 114_000.0, "capital_invertido": 570_000.0, "ev": 1_425_000.0,
               "market_cap": 1_400_000.0, "total_debt": 100_000.0, "cash": 75_000.0,
               "current_assets": 650_000.0, "current_liabilities": 150_000.0, "ppe_net": 70_000.0},
        factors={"value": "alto", "quality": "alto", "momentum": "medio", "low_vol": "bajo"},
        roe=0.22, gross_margin=0.55, beta=1.05,
    )


# CA-S1: 10 preguntas representativas (QB-8: se agregan `vf` y `alz`). `pio` de
# la spec = Piotroski (`pig`).
_EXPLAIN_QUESTIONS = ("mod", "ben", "ren", "ver", "gra", "dcf", "rsk", "pig", "vf", "alz")
_EN_STOPWORDS = frozenset({"the", "and", "is", "of", "with", "this", "that", "are", "for"})


def _looks_non_spanish(text: str) -> bool:
    if any(ch in text for ch in "ãõÃÕ"):
        return True
    words = [w.strip(".,;:()").lower() for w in text.split()]
    return sum(1 for w in words if w in _EN_STOPWORDS) >= 3


def _looks_like_echo(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or '"pregunta"' in text or '"datos"' in text


# Métricas de redacción (SDD Iter-2, 17.8): regex PROPIAS sobre el texto crudo del
# modelo, sin importar el normalizador ni las etiquetas del código de producción:
# así el MISMO script mide el baseline (prompts viejos) y el nuevo.
_ETIQUETAS_RE = (
    re.compile(r"^💡 En simple[ \t]*$", re.MULTILINE),
    re.compile(r"^🔎 (?:Qué es cada pieza|Qué dicen los datos)[ \t]*$", re.MULTILINE),
    re.compile(r"^🧭 (?:Qué significa el resultado|Cómo leerlo)[ \t]*$", re.MULTILINE),
)
_MARKDOWN_RE = re.compile(r"\*\*|__|`|^[ \t]*#{1,6}[ \t]|^[ \t]*[*\-][ \t]", re.MULTILINE)
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _etiquetas_conformes(text: str) -> bool:
    return all(rx.search(text) for rx in _ETIQUETAS_RE)


def _tiene_markdown(text: str) -> bool:
    return _MARKDOWN_RE.search(text) is not None


def _tiene_snake_case(text: str) -> bool:
    return _SNAKE_CASE_RE.search(text) is not None


def _direccion_contradice(text: str, posicion: Optional[str]) -> bool:
    """Alarma heurística de dirección en `vf`/`ver` (SDD Iter-2, 17.8): el
    texto dice "por debajo" con `posicion_vs_precio == "por encima"` (o al
    revés) y no menciona la dirección correcta. No es un veredicto: la
    revisión manual con `--show-text` sigue siendo la que decide."""
    if posicion not in ("por encima", "por debajo"):
        return False
    otra = "por debajo" if posicion == "por encima" else "por encima"
    bajo = text.lower()
    return otra in bajo and posicion not in bajo


def _razon_de_rechazo(ai_explain, tokens: set, datos_modelo: dict, text: str) -> str:
    """Clasifica un rechazo con el MISMO orden que el pipeline de
    `_fetch_explanation`: largo -> vacio -> numeros -> escala -> salida."""
    if len(text) > ai_explain._MAX_RAW_CHARS:
        return "largo"
    normalizado = ai_explain._normalize_layout(text)
    if normalizado == "":
        return "vacio"
    if not ai_explain._no_new_protected_tokens(tokens, normalizado):
        return "guard_numeros"
    if not ai_explain._escalas_consistentes(datos_modelo, normalizado):
        return "guard_escala"
    if ai_explain.gemini_client.violates_output_filter(normalizado):
        return "filtro_salida"
    return "otro"


# --- Corrida -----------------------------------------------------------------


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[str] = []

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=1)

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kwargs):
        self.edited.append(text)


class _AlwaysAllow:
    def allow(self, key: str) -> bool:
        return True


async def _explain_once(ai_explain, clients, store, ctx, code, spec, chat_id):
    cid = store.put(ctx)
    handler = ai_explain.build_explain_handler(clients, _AlwaysAllow(), store).callback
    from investbot import ai_explain_content

    if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
        data = f"xp:{cid}:p:{code}"
    else:
        data = f"xp:{cid}:{code}"
    query = SimpleNamespace(data=data, answer=_async_noop)
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=chat_id, type="private"))
    bot = _FakeBot()
    await handler(update, SimpleNamespace(bot=bot))
    return bot.edited[-1] if bot.edited else None


async def _async_noop(*args, **kwargs):
    return None


def _synthetic_summaries(summary, n: int) -> list[list[str]]:
    out = []
    tickers = ["ZZA", "ZZCANARY", "ZZCANARYX", "ZZBCD", "ZZ"]
    for i in range(n):
        si = i % 2 == 0
        pillars = {
            "ingresos_crecientes": si, "utilidades_crecientes": si,
            "deuda_controlada": si or i % 3 == 0, "precio_razonable": si,
        }
        risk_fit = {"encaja": si, "perfil": "moderado", "beta": 1.0 + i / 10,
                    "etiqueta_activo": "acción sintética"}
        base = 400.0 + 25 * i
        scenarios = {
            "pesimista": {"valor_justo_total": base * 0.8},
            "conservador": {"valor_justo_total": base},
            "optimista": {"valor_justo_total": base * 1.2},
        }
        out.append(summary.build_summary_parts_short(
            ticker=tickers[i % len(tickers)], company_name=f"CANARY_EMPRESA_SA {i}",
            precio_actual=450.0 + 10 * i, pillars=pillars, risk_fit=risk_fit, scenarios=scenarios,
            escenario_elegido=("pesimista", "conservador", "optimista")[i % 3],
        ))
    return out


async def _run(
    explain_reps: int, rewrite_samples: int, rewrite_reps: int,
    env: Mapping[str, str], client_factory: Callable[[], httpx.AsyncClient],
    show_text: bool = False, show_text_max: int = SHOW_TEXT_DEFAULT,
    thinking: Optional[str] = None,
) -> int:
    # C8 (corrige E10): el logger `investbot` queda en WARNING pero sin handler,
    # `logging.lastResort` imprimiría a stderr el `GEMINI_GUARD_REJECT ... respuesta=`.
    # Un NullHandler con `propagate=False` lo evita; se restaura al salir.
    inv_logger = logging.getLogger("investbot")
    null_handler = logging.NullHandler()
    propagate_antes = inv_logger.propagate
    inv_logger.addHandler(null_handler)
    inv_logger.propagate = False
    try:
        return await _run_impl(
            explain_reps, rewrite_samples, rewrite_reps, env, client_factory,
            show_text, show_text_max, thinking,
        )
    finally:
        inv_logger.removeHandler(null_handler)
        inv_logger.propagate = propagate_antes


async def _run_impl(
    explain_reps: int, rewrite_samples: int, rewrite_reps: int,
    env: Mapping[str, str], client_factory: Callable[[], httpx.AsyncClient],
    show_text: bool, show_text_max: int, thinking: Optional[str],
) -> int:
    for name in ("httpx", "httpcore", "investbot"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _ensure_src_on_path()
    from investbot import ai_explain, ai_explain_content, ai_rewrite, gemini_client, summary

    # Config EXPLÍCITA desde GEMINI_LIVE_KEY (env armado a mano, no el entorno completo).
    config_env = {gemini_client.ENV_API_KEY: env["GEMINI_LIVE_KEY"], gemini_client.ENV_REWRITE_ENABLED: "true"}
    if thinking is not None:  # solo sobre ESTE mapping armado a mano; nada se toca en el proceso
        config_env[gemini_client.ENV_THINKING_LEVEL] = thinking
    config = gemini_client.load_config(config_env)
    if not config.enabled:
        print("ABORTADO: CONFIG_INVALIDA (GEMINI_LIVE_KEY no cumple el formato esperado)")
        return 3
    fake_cfg = gemini_client.load_config(
        {gemini_client.ENV_API_KEY: "FAKE-NEGATIVE-CONTROL-KEY-000000",
         gemini_client.ENV_REWRITE_ENABLED: "true"}
    )

    budget = CallBudget(CALL_LIMIT)
    chat_id = 987654321
    questions = {**ai_explain_content.QUESTIONS_TEXTO_LIBRE, **ai_explain_content.QUESTIONS_AVANZADO}
    tl_ctx = _texto_libre_context(ai_explain, chat_id)
    av_ctx = _avanzado_context(ai_explain, chat_id)
    store = ai_explain.ExplanationContextStore()
    explain_rows: dict[str, dict[str, Any]] = {}
    exit_code = 0
    shown_header = False

    async with client_factory() as raw_client:
        metered = _MeteredClient(raw_client, budget)
        clients = SimpleNamespace(gemini_http=metered, gemini_config=config)

        # CA-S1/S2/S3/S5: explicaciones.
        for code in _EXPLAIN_QUESTIONS:
            spec = questions[code]
            ctx = av_ctx if code in ai_explain_content.QUESTIONS_AVANZADO else tl_ctx
            row = {"n": 0, "ok200": 0, "accepted": 0, "max_tokens": 0, "echo": 0, "non_es": 0,
                   "truncated": 0, "reasons": {}, "ms": [], "finish": {}, "thoughts": [], "served": set(),
                   "stop_texts": 0, "guards_head": 0, "lens": [], "labels_ok": 0, "md": 0, "snake": 0,
                   "shown": 0, "dir_alarm": 0}
            datos = ai_explain._build_explain_payload(ctx, code)
            if spec.variant == ai_explain_content.VARIANT_DATO_Y_PASO_A_PASO:
                cuenta = ai_explain._build_cuenta_line(ctx.kind, code, datos)
                if cuenta is not None:
                    datos["cuenta"] = cuenta
            # CS-25b/A3: mismo payload presentado y mismos tokens que el handler.
            datos_modelo = ai_explain._datos_para_el_modelo(code, datos)
            tokens = ai_explain._compute_datos_tokens(datos, datos_modelo)
            posicion = datos_modelo.get("posicion_vs_precio")
            for _ in range(explain_reps):
                before = len(metered.records)
                final = await _explain_once(ai_explain, clients, store, ctx, code, spec, chat_id)
                if metered.exceeded:
                    print("ABORTADO: TOPE_DE_LLAMADAS_EXCEDIDO")
                    return 4
                recs = metered.records[before:]
                row["n"] += 1
                if not recs:
                    row["reasons"]["sin_llamada"] = row["reasons"].get("sin_llamada", 0) + 1
                    continue
                last = recs[-1]
                row["ms"].append(sum(r["ms"] or 0 for r in recs))
                row["ok200"] += 1 if last["status"] == 200 else 0
                row["finish"][_fmt(last["finish"])] = row["finish"].get(_fmt(last["finish"]), 0) + 1
                if last["thoughts"] is not None:
                    row["thoughts"].append(last["thoughts"])
                if last["served"]:
                    row["served"].add(last["served"])
                if last["finish"] == "MAX_TOKENS":
                    row["max_tokens"] += 1
                accepted = final is not None and final != ai_explain.EXPLAIN_UNAVAILABLE_MSG
                if accepted:
                    row["accepted"] += 1
                    text = last["text"] or ""
                    row["echo"] += 1 if _looks_like_echo(text) else 0
                    row["non_es"] += 1 if _looks_non_spanish(text) else 0
                    row["truncated"] += 1 if len(text) > ai_explain._MAX_RESPUESTA_CHARS else 0
                else:
                    text = last["text"]
                    if last["status"] != 200:
                        why = f"http_{last['status']}"
                    elif text is None or last["finish"] != "STOP":
                        why = f"finish_{_fmt(last['finish'])}"
                    else:
                        why = _razon_de_rechazo(ai_explain, tokens, datos_modelo, text)
                    row["reasons"][why] = row["reasons"].get(why, 0) + 1
                # Métricas de redacción sobre el texto crudo (solo respuestas completas, STOP).
                texto_modelo = last["text"] if (
                    last["status"] == 200 and last["finish"] == "STOP" and isinstance(last["text"], str)
                ) else None
                bloqueado_por_filtro = False
                ocultar_texto = False
                if texto_modelo is not None:
                    bloqueado_por_filtro = gemini_client.violates_output_filter(texto_modelo)
                    # la razón de rechazo se decide sobre el texto NORMALIZADO: ocultar también por él
                    ocultar_texto = bloqueado_por_filtro or gemini_client.violates_output_filter(
                        ai_explain._normalize_layout(texto_modelo)
                    )
                    row["stop_texts"] += 1
                    row["lens"].append(len(texto_modelo))
                    # aceptación por los DOS guards de HEAD (comparable baseline/nuevo)
                    row["guards_head"] += 1 if (
                        ai_explain._no_new_protected_tokens(tokens, texto_modelo) and not bloqueado_por_filtro
                    ) else 0
                    row["labels_ok"] += 1 if _etiquetas_conformes(texto_modelo) else 0
                    row["dir_alarm"] += 1 if _direccion_contradice(texto_modelo, posicion) else 0
                    row["md"] += 1 if _tiene_markdown(texto_modelo) else 0
                    row["snake"] += 1 if _tiene_snake_case(texto_modelo) else 0
                # --show-text (C2-C5): solo explicaciones, solo STOP, siempre saneado, y
                # NUNCA un texto que el filtro de salida rechaza (ni por otra razón).
                if show_text and texto_modelo is not None and row["shown"] < show_text_max:
                    if row["shown"] == 0 and not shown_header:
                        print("=== Textos del modelo (--show-text; sintéticos, saneados) ===")
                        shown_header = True
                    row["shown"] += 1
                    motivo = "ACEPTADO" if accepted else f"RECHAZADO:{why}"
                    etiqueta = f"[{code} rep {row['n']} {motivo}]"
                    if ocultar_texto:
                        print(f"{etiqueta} (texto no mostrado: filtro_salida; chars={len(texto_modelo)})")
                    else:
                        print(etiqueta)
                        print(ai_explain._safe_display(texto_modelo, max_chars=SHOW_TEXT_CHARS))
            explain_rows[code] = row

        # CA-S6: rewrite.
        rw = {"runs": 0, "ok200": 0, "parse_ok": 0, "sections": 0, "sections_ok": 0, "used": 0,
              "md_bad": 0, "max_tokens": 0, "ms": [], "thoughts": []}
        for parts in _synthetic_summaries(summary, rewrite_samples) * rewrite_reps:
            before = len(metered.records)
            outcome = await ai_rewrite.rewrite_parts(parts, config, http_client=metered)
            if metered.exceeded:
                print("ABORTADO: TOPE_DE_LLAMADAS_EXCEDIDO")
                return 4
            recs = metered.records[before:]
            rw["runs"] += 1
            rw["used"] += 1 if outcome.used_gemini else 0
            if not recs:
                continue
            last = recs[-1]
            rw["ms"].append(last["ms"] or 0)
            rw["ok200"] += 1 if last["status"] == 200 else 0
            rw["max_tokens"] += 1 if last["finish"] == "MAX_TOKENS" else 0
            if last["thoughts"] is not None:
                rw["thoughts"].append(last["thoughts"])
            body = parts[1:]
            sections = ai_rewrite._parse_json_sections(last["text"], len(body)) if last["text"] else None
            if sections is not None:
                rw["parse_ok"] += 1
                for original, new in zip(body, sections):
                    rw["sections"] += 1
                    _, line_map = ai_rewrite._classify_lines(original)
                    rec_sec = ai_rewrite._reconstruct_section(new, line_map, original)
                    ok = (
                        not gemini_client.violates_output_filter(new)
                        and rec_sec is not None
                        and len(rec_sec) <= ai_rewrite._length_limit(original)
                    )
                    rw["sections_ok"] += 1 if ok else 0
            for section in outcome.parts[1:]:
                if not ai_rewrite._markdown_balance_preserved("", section):
                    rw["md_bad"] += 1

        # CA-S4: control negativo con key falsa.
        fake_clients = SimpleNamespace(gemini_http=metered, gemini_config=fake_cfg)
        before = len(metered.records)
        neg_final = await _explain_once(
            ai_explain, fake_clients, store, tl_ctx, "ren", questions["ren"], chat_id
        )
        neg_outcome = await ai_rewrite.rewrite_parts(
            _synthetic_summaries(summary, 1)[0], fake_cfg, http_client=metered
        )
        if metered.exceeded:
            print("ABORTADO: TOPE_DE_LLAMADAS_EXCEDIDO")
            return 4
        neg_statuses = [r["status"] for r in metered.records[before:]]
        neg_original = neg_outcome.used_gemini is False
        neg_explain_fallback = neg_final == ai_explain.EXPLAIN_UNAVAILABLE_MSG

    # --- Resumen (sin key, headers ni cuerpos) ---
    print("=== Resumen del smoke de Gemini (datos sintéticos) ===")
    print(f"modelo={config.model} thinkingLevel={config.thinking_level} llamadas={budget.spent}/{budget.limit}")
    all_ms: list[int] = []
    s2_ok = s3_ok = True
    total_trunc = total_acc = 0
    for code, r in explain_rows.items():
        rate = r["accepted"] / r["n"] if r["n"] else 0.0
        all_ms += r["ms"]
        s2_ok &= rate >= 0.90
        s3_ok &= r["echo"] == 0 and r["non_es"] == 0 and r["max_tokens"] == 0
        total_trunc += r["truncated"]
        total_acc += r["accepted"]
        print(
            f"  [{code}] n={r['n']} http200={r['ok200']} aceptadas={r['accepted']} ({rate:.0%}) "
            f"eco={r['echo']} no_es={r['non_es']} max_tokens={r['max_tokens']} "
            f"truncadas={r['truncated']} p95ms={_fmt(_p95(r['ms']))} finish={r['finish']} "
            f"thoughts={r['thoughts'][:3]} servido={sorted(r['served'])} rechazos={r['reasons']}"
        )
        print(
            f"    [{code}] guards_head={r['guards_head']}/{r['stop_texts']} "
            f"largo_p50={_fmt(_p50(r['lens']))} largo_p95={_fmt(_p95(r['lens']))} "
            f"etiquetas_ok={r['labels_ok']}/{r['stop_texts']} markdown={r['md']} snake_case={r['snake']} "
            f"direccion_alarma={r['dir_alarm']}"
        )
    stop_total = sum(r["stop_texts"] for r in explain_rows.values())
    all_lens = [n for r in explain_rows.values() for n in r["lens"]]
    all_thoughts = [n for r in explain_rows.values() for n in r["thoughts"]]
    print(
        f"  [explicaciones-total] guards_head={sum(r['guards_head'] for r in explain_rows.values())}/{stop_total} "
        f"largo_p50={_fmt(_p50(all_lens))} largo_p95={_fmt(_p95(all_lens))} "
        f"etiquetas_ok={sum(r['labels_ok'] for r in explain_rows.values())}/{stop_total} "
        f"markdown={sum(r['md'] for r in explain_rows.values())} "
        f"snake_case={sum(r['snake'] for r in explain_rows.values())} "
        f"direccion_alarma={sum(r['dir_alarm'] for r in explain_rows.values())} "
        f"tokens_pensamiento_total={sum(all_thoughts)}"
    )
    s1_ok = bool(explain_rows) and all(r["ok200"] >= 1 for r in explain_rows.values())
    s3_ok &= total_trunc <= max(total_acc // 10, 0)
    p95 = _p95(all_ms)
    print(f"CA-S1 (10 preguntas, HTTP 200, thinkingLevel aceptado): {_verdict(s1_ok)}")
    print(f"CA-S2 (aceptación del guard >= 90% por pregunta): {_verdict(s2_ok)}")
    print(f"CA-S3 (0 eco, 0 idioma ajeno, 0 MAX_TOKENS, <=1/10 truncadas={total_trunc}/{total_acc}): {_verdict(s3_ok)}")
    print(f"CA-S4 (key falsa -> status {neg_statuses}, explicación y rewrite en fallback): "
          f"{_verdict(bool(neg_statuses) and all(s is not None and 400 <= s < 500 for s in neg_statuses) and neg_explain_fallback and neg_original)}")
    print(f"CA-S5 (explicaciones p95 <= 8000 ms; p95={_fmt(p95)} ms): {_verdict(p95 is not None and p95 <= 8000)}")
    runs = rw["runs"] or 1
    sec = rw["sections"] or 1
    rw_p95 = _p95(rw["ms"])
    print(
        f"  [rewrite] corridas={rw['runs']} http200={rw['ok200']} json_valido={rw['parse_ok']} "
        f"secciones_ok={rw['sections_ok']}/{rw['sections']} reescritas={rw['used']} "
        f"markdown_desparejado={rw['md_bad']} max_tokens={rw['max_tokens']} p95ms={_fmt(rw_p95)} "
        f"thoughts={rw['thoughts'][:3]}"
    )
    s6 = (
        rw["ok200"] == rw["runs"] and rw["parse_ok"] / runs >= 0.95
        and rw["sections_ok"] / sec >= 0.90 and rw["used"] / runs >= 0.70
        and rw["md_bad"] == 0 and rw_p95 is not None and rw_p95 <= 6000
    )
    print(f"CA-S6 (rewrite: 100% HTTP 200, JSON >= 95%, secciones >= 90%, reescritas >= 70%, "
          f"0 Markdown desparejado, p95 <= 6000 ms; MAX_TOKENS={rw['max_tokens']}): {_verdict(s6)}")
    print("Nota: un FALLA es evidencia para un spec patch; este script no cambia nada por sí solo.")
    return exit_code


def _ensure_src_on_path() -> None:
    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _parse_args(argv: list[str]) -> Optional[argparse.Namespace]:
    parser = argparse.ArgumentParser(prog="gemini_smoke.py", add_help=True, exit_on_error=False)
    parser.add_argument("--explain-reps", type=int, default=5)
    parser.add_argument("--rewrite-samples", type=int, default=10)
    parser.add_argument("--rewrite-reps", type=int, default=3)
    parser.add_argument("--show-text", action="store_true", default=False)
    parser.add_argument("--show-text-max", type=int, default=SHOW_TEXT_DEFAULT)
    parser.add_argument("--thinking", choices=THINKING_CHOICES, default=None)
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return None
    if min(args.explain_reps, args.rewrite_samples, args.rewrite_reps) < 1:
        return None
    if not 1 <= args.show_text_max <= SHOW_TEXT_MAX:
        return None
    return args


def main(
    argv: list[str],
    env: Mapping[str, str],
    in_container: bool,
    client_factory: Callable[[], httpx.AsyncClient],
) -> int:
    allowed, reason = evaluate_guards(env, in_container)
    if not allowed:
        print(f"ABORTADO: {reason}")
        return UNAVAILABLE_RC
    args = _parse_args(argv)
    if args is None:
        print("ABORTADO: ARGUMENTOS_INVALIDOS")
        return 64
    try:
        return asyncio.run(
            _run(
                args.explain_reps, args.rewrite_samples, args.rewrite_reps, env, client_factory,
                show_text=args.show_text, show_text_max=args.show_text_max, thinking=args.thinking,
            )
        )
    except Exception as exc:  # solo el tipo: nunca el texto (puede traer datos)
        print(f"ABORTADO: ERROR_INESPERADO ({type(exc).__name__})")
        return 5


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1:],
            environ,
            os.path.exists("/.dockerenv"),
            lambda: httpx.AsyncClient(follow_redirects=False),
        )
    )
