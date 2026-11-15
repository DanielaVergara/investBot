#!/usr/bin/env python3
"""Comparación VIVA de SEC EDGAR contra FMP (SDD_fuentes_datos_gratuitas.md, 14.8, CA-33).

NO es un test de pytest, NO se importa desde `src` y NO entra a la imagen (el
`Dockerfile` solo copia `src/`). Se corre a mano, una vez, desde la raíz del repo y
con las claves en el ENTORNO (nunca en el código ni en un archivo):

    SEC_EDGAR_USER_AGENT="<app> <alias@dominio-real>" FMP_API_KEY=<key> \\
        COMPARE_CONFIRM=yes python scripts/compare_edgar_fmp.py [NVDA ADBE KO]

Para cada ticker (por defecto NVDA, ADBE y KO) imprime las diferencias de los
últimos 4 trimestres entre `EdgarProvider` y la API de FMP, con las tolerancias de
14.8:

  * `revenue`, `netIncome`, `operatingCashFlow`, `capitalExpenditure`, `totalAssets`,
    `totalCurrentAssets`, `totalCurrentLiabilities`, `cashAndCashEquivalents`:
    exacto, o <= 0,5% si FMP reexpresó;
  * `epsDiluted`: +/- 0,01; acciones diluidas: <= 0,2% (el redondeo de FMP a
    múltiplos de 0,5 M llega a ~0,13%; ver sección 18 de la spec);
  * TTM de `netIncome`: <= 0,5%; EPS TTM, P/S y PER: <= 1%;
  * `ebit`, `totalDebt`, `propertyPlantEquipmentNet`: diferencia ESPERADA y
    documentada (3.5): se informa y no se rechaza; `interestExpense` de EDGAR en
    `None` contra un valor de FMP también es esperado (AAPL).

Cualquier otra diferencia fuera de tolerancia se informa como FUERA y el script
termina con 1: es evidencia para un spec patch, nunca se ajusta una tolerancia en
silencio. El script no decide nada por sí solo.

Seguridad: el User-Agent y la key solo se leen del entorno y NUNCA se imprimen ni se
loguean (solo cifras públicas de estados financieros); el UA debe pasar la misma
validación estricta que `bot.main()`; los clientes HTTP no siguen redirecciones; no
escribe archivos (solo stdout); NO corre en CI (`CI` en el entorno, o dentro de un
contenedor, aborta) y exige `COMPARE_CONFIRM=yes`. Con unas 5 llamadas a FMP por
ticker (3 estados, key-metrics no, 1 cotización), 3 tickers gastan ~15 de las 250
diarias.

Códigos de salida: 0 = todo dentro de tolerancia (o diferencias esperadas);
1 = hay diferencias FUERA de tolerancia; 2 = abortado por una guarda; 3 = config
inválida (UA o key); 5 = error inesperado; 64 = argumentos inválidos.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

DEFAULT_TICKERS = ("NVDA", "ADBE", "KO")
MAX_TICKERS = 5
QUARTERS_TO_COMPARE = 4
DATE_MATCH_DAYS = 3  # QB-8: el `end` coincide con abs(diferencia en días) <= 3

_TICKER_RE = re.compile(r"[A-Z0-9]{1,6}(-[A-Z0-9]{1,2})?", re.ASCII)

# (campo, tolerancia relativa). Exacto, o hasta esa fracción si FMP reexpresó.
EXACT_FIELDS: dict[str, dict[str, float]] = {
    "income": {"revenue": 0.005, "netIncome": 0.005},
    "cash": {"operatingCashFlow": 0.005, "capitalExpenditure": 0.005},
    "balance": {
        "totalAssets": 0.005,
        "totalCurrentAssets": 0.005,
        "totalCurrentLiabilities": 0.005,
        "cashAndCashEquivalents": 0.005,
    },
}
EPS_ABS_TOLERANCE = 0.01
SHARES_REL_TOLERANCE = 0.002
TTM_NET_INCOME_REL_TOLERANCE = 0.005
RATIO_REL_TOLERANCE = 0.01  # EPS TTM, P/S, PER

# Diferencias esperadas (3.5): se informan, no se rechazan.
EXPECTED_DIFF_FIELDS: dict[str, tuple[str, ...]] = {
    "income": ("ebit", "interestExpense"),
    "balance": ("totalDebt", "propertyPlantEquipmentNet"),
}

OK, ESPERADA, FUERA, SIN_DATO = "OK", "ESPERADA", "FUERA", "SIN_DATO"

CI_ABORT_RC = 2
CONFIG_RC = 3
UNEXPECTED_RC = 5
BAD_ARGS_RC = 64


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def classify_rel(edgar: Any, fmp: Any, tol: float) -> str:
    """Exacto, o dentro de `tol` relativo al valor de FMP. `SIN_DATO` si falta alguno."""
    if not _is_num(edgar) or not _is_num(fmp):
        return SIN_DATO
    if edgar == fmp:
        return OK
    if fmp == 0:
        return FUERA
    return OK if abs(edgar - fmp) / abs(fmp) <= tol else FUERA


def classify_abs(edgar: Any, fmp: Any, tol: float) -> str:
    if not _is_num(edgar) or not _is_num(fmp):
        return SIN_DATO
    return OK if abs(edgar - fmp) <= tol + 1e-9 else FUERA


def _days_apart(a: str, b: str) -> Optional[int]:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def match_row(edgar_row: dict, fmp_rows: list[dict]) -> Optional[dict]:
    """Fila de FMP cuyo `date` cae a <= 3 días del `date` de la fila de EDGAR."""
    for row in fmp_rows:
        gap = _days_apart(str(edgar_row.get("date")), str(row.get("date")))
        if gap is not None and gap <= DATE_MATCH_DAYS:
            return row
    return None


def compare_statement(
    kind: str, edgar_rows: list[dict], fmp_rows: list[dict]
) -> list[tuple[str, str, str, Any, Any]]:
    """Filas `(fecha_edgar, campo, veredicto, valor_edgar, valor_fmp)` de los últimos
    4 trimestres de `edgar_rows` (recent-first) contra las de FMP."""
    salida: list[tuple[str, str, str, Any, Any]] = []
    for erow in edgar_rows[:QUARTERS_TO_COMPARE]:
        frow = match_row(erow, fmp_rows)
        fecha = str(erow.get("date"))
        if frow is None:
            salida.append((fecha, "(periodo)", SIN_DATO, None, None))
            continue
        for campo, tol in EXACT_FIELDS.get(kind, {}).items():
            salida.append(
                (fecha, campo, classify_rel(erow.get(campo), frow.get(campo), tol),
                 erow.get(campo), frow.get(campo))
            )
        if kind == "income":
            salida.append(
                (fecha, "epsDiluted",
                 classify_abs(erow.get("epsDiluted"), frow.get("epsDiluted"), EPS_ABS_TOLERANCE),
                 erow.get("epsDiluted"), frow.get("epsDiluted"))
            )
            salida.append(
                (fecha, "weightedAverageShsOutDil",
                 classify_rel(erow.get("weightedAverageShsOutDil"),
                              frow.get("weightedAverageShsOutDil"), SHARES_REL_TOLERANCE),
                 erow.get("weightedAverageShsOutDil"), frow.get("weightedAverageShsOutDil"))
            )
        for campo in EXPECTED_DIFF_FIELDS.get(kind, ()):
            e, f = erow.get(campo), frow.get(campo)
            if not _is_num(e) or not _is_num(f):
                veredicto = ESPERADA if (e is None) != (f is None) else SIN_DATO
            else:
                veredicto = OK if e == f else ESPERADA
            salida.append((fecha, campo, veredicto, e, f))
    return salida


def compare_ttm(
    rules_module, edgar_income: list[dict], fmp_income: list[dict],
    price: Optional[float], market_cap: Optional[float],
) -> list[tuple[str, str, Any, Any]]:
    """TTM de `netIncome`, EPS TTM, P/S y PER de cada lado con las reglas del bot."""
    salida: list[tuple[str, str, Any, Any]] = []
    e = rules_module.calculate_income_statement_ttm(edgar_income)
    f = rules_module.calculate_income_statement_ttm(fmp_income)
    salida.append(("netIncome TTM", classify_rel(e.net_income_ttm, f.net_income_ttm,
                                                  TTM_NET_INCOME_REL_TOLERANCE),
                   e.net_income_ttm, f.net_income_ttm))
    eps_e = (e.net_income_ttm / e.shares_outstanding_reciente) if e.disponible else None
    eps_f = (f.net_income_ttm / f.shares_outstanding_reciente) if f.disponible else None
    salida.append(("EPS TTM", classify_rel(eps_e, eps_f, RATIO_REL_TOLERANCE), eps_e, eps_f))
    ps_e = rules_module.calculate_ps(market_cap, e.revenue_ttm) if e.disponible and market_cap else None
    ps_f = rules_module.calculate_ps(market_cap, f.revenue_ttm) if f.disponible and market_cap else None
    salida.append(("P/S", classify_rel(ps_e, ps_f, RATIO_REL_TOLERANCE), ps_e, ps_f))
    per_e = rules_module.calculate_per(price, eps_e).per if price and eps_e is not None else None
    per_f = rules_module.calculate_per(price, eps_f).per if price and eps_f is not None else None
    salida.append(("PER", classify_rel(per_e, per_f, RATIO_REL_TOLERANCE), per_e, per_f))
    return salida


def _fmt(value: Any) -> str:
    if value is None:
        return "N/D"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def evaluate_guards(env: Mapping[str, str], in_container: bool) -> tuple[bool, str]:
    """`(permitido, motivo)`. No corre en CI, ni dentro de un contenedor, ni sin confirmar."""
    if env.get("CI"):
        return False, "NO_SE_CORRE_EN_CI"
    if in_container:
        return False, "DENTRO_DE_UN_CONTENEDOR"
    if env.get("COMPARE_CONFIRM") != "yes":
        return False, "FALTA_COMPARE_CONFIRM_YES"
    return True, ""


def _ensure_src_on_path() -> None:
    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def parse_tickers(argv: list[str]) -> Optional[list[str]]:
    """Sin argumentos: NVDA, ADBE y KO. Cada ticker en mayúsculas ASCII estricto."""
    if not argv:
        return list(DEFAULT_TICKERS)
    if len(argv) > MAX_TICKERS:
        return None
    tickers: list[str] = []
    for raw in argv:
        t = raw.strip().upper()
        if not raw.isascii() or not _TICKER_RE.fullmatch(t):
            return None
        tickers.append(t)
    return tickers


async def _run(
    tickers: list[str],
    ua: str,
    fmp_key: str,
    sec_client_factory: Callable[[], Any],
    fmp_client_factory: Callable[[], Any],
    out: Callable[[str], None],
    today: Optional[Callable[[], date]] = None,
) -> int:
    from investbot import edgar_provider, fmp_client, rules
    from investbot.fundamentals import ProviderUnavailable

    fuera = 0
    async with sec_client_factory() as sec_http, fmp_client_factory() as fmp_http:
        provider = (
            edgar_provider.EdgarProvider(sec_http, ua, today=today)
            if today is not None
            else edgar_provider.EdgarProvider(sec_http, ua)
        )
        for ticker in tickers:
            out(f"=== {ticker} ===")
            try:
                edgar = await provider.fetch(ticker, quarters=12)
            except ProviderUnavailable as exc:
                out(f"  EDGAR no disponible: {exc.code}")
                continue
            if edgar is None:
                out("  EDGAR sin cobertura")
                continue
            fmp_income = await fmp_client.get_income_statement(
                fmp_http, fmp_key, ticker, period="quarter", limit=QUARTERS_TO_COMPARE
            )
            fmp_balance = await fmp_client.get_balance_sheet_statement(
                fmp_http, fmp_key, ticker, period="quarter", limit=QUARTERS_TO_COMPARE
            )
            fmp_cash = await fmp_client.get_cash_flow_statement(
                fmp_http, fmp_key, ticker, period="quarter", limit=QUARTERS_TO_COMPARE
            )
            quote = await fmp_client.get_quote(fmp_http, fmp_key, ticker) or {}
            for kind, e_rows, f_rows in (
                ("income", edgar.income_q, fmp_income),
                ("balance", edgar.balance_q, fmp_balance),
                ("cash", edgar.cash_q, fmp_cash),
            ):
                for fecha, campo, veredicto, e, f in compare_statement(kind, e_rows, f_rows):
                    out(f"  [{veredicto}] {kind} {fecha} {campo}: EDGAR={_fmt(e)} FMP={_fmt(f)}")
                    fuera += veredicto == FUERA
            for nombre, veredicto, e, f in compare_ttm(
                rules, edgar.income_q, fmp_income, quote.get("price"), quote.get("marketCap")
            ):
                out(f"  [{veredicto}] TTM {nombre}: EDGAR={_fmt(e)} FMP={_fmt(f)}")
                fuera += veredicto == FUERA
    out(f"RESUMEN: {fuera} diferencia(s) FUERA de tolerancia")
    return 1 if fuera else 0


def main(
    argv: list[str],
    env: Mapping[str, str],
    in_container: bool,
    sec_client_factory: Optional[Callable[[], Any]] = None,
    fmp_client_factory: Optional[Callable[[], Any]] = None,
    out: Callable[[str], None] = print,
    today: Optional[Callable[[], date]] = None,
) -> int:
    allowed, reason = evaluate_guards(env, in_container)
    if not allowed:
        out(f"ABORTADO: {reason}")
        return CI_ABORT_RC
    tickers = parse_tickers(argv)
    if tickers is None:
        out("ABORTADO: ARGUMENTOS_INVALIDOS")
        return BAD_ARGS_RC
    _ensure_src_on_path()
    import httpx

    from investbot import bot, sec_edgar_client

    ua = env.get("SEC_EDGAR_USER_AGENT", "")
    fmp_key = env.get("FMP_API_KEY", "")
    if not ua or not bot._valid_sec_user_agent(ua):
        out("ABORTADO: SEC_EDGAR_USER_AGENT_AUSENTE_O_INVALIDO")
        return CONFIG_RC
    if not fmp_key:
        out("ABORTADO: FMP_API_KEY_AUSENTE")
        return CONFIG_RC
    sec_factory = sec_client_factory or sec_edgar_client.build_sec_http_client
    fmp_factory = fmp_client_factory or (
        lambda: httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    )
    try:
        return asyncio.run(_run(tickers, ua, fmp_key, sec_factory, fmp_factory, out, today))
    except Exception as exc:  # noqa: BLE001 -- el script nunca imprime el mensaje (podría traer la key)
        out(f"ERROR_INESPERADO: {type(exc).__name__}")
        return UNEXPECTED_RC


if __name__ == "__main__":  # pragma: no cover - entrada manual
    import os

    sys.exit(main(sys.argv[1:], os.environ, os.path.exists("/.dockerenv")))
