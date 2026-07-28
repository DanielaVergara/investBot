"""Handler de texto libre (ticker/nombre de empresa) — orquesta todo el análisis.

Flujo: valida onboarding completado → normaliza/valida input → rate-limit →
resuelve ticker (`/search`) → trae datos crudos de FMP (solo endpoints
gratuitos) + peers + Y (FRED/Treasury.gov) → calcula ratios (`rules.py`) y
valor justo (`valuation.py`) → arma respuesta (`summary.py`).

Nunca deja "burbujear" una excepción cruda de `httpx` hacia Telegram — solo
conoce el mensaje ya sanitizado de `FMPError`/`TreasuryError` (criterio de
`security`, sección 3).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from investbot import db, fmp_client, peers, risk_fit, rules, summary, treasury_client, valuation

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 100
NO_ONBOARDING_MSG = "Antes necesito que corras /start para fijar tu perfil de riesgo."
NO_MATCHES_MSG = "No encontré ninguna empresa que coincida con \"{query}\". Probá con el ticker exacto."
INPUT_TOO_LONG_MSG = f"Ese texto es demasiado largo (máximo {MAX_INPUT_LENGTH} caracteres). Probá con un ticker o nombre corto."
RATE_LIMITED_MSG = "Estás consultando muy rápido — esperá un minuto antes de volver a intentar."
GENERIC_ERROR_MSG = "No pude completar el análisis ahora mismo. Intenta más tarde."

_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]")


def sanitize_for_log(text: str) -> str:
    """Remueve saltos de línea/caracteres de control antes de loguear texto libre
    (CWE-117, criterio de `security` sección 4)."""
    return _CONTROL_CHARS_RE.sub(" ", text)


def normalize_query(text: str) -> Optional[str]:
    """strip + colapsar espacios + límite de longitud. `None` si excede el límite."""
    normalized = re.sub(r"\s+", " ", text.strip())
    if len(normalized) == 0 or len(normalized) > MAX_INPUT_LENGTH:
        return None
    return normalized


@dataclass
class Clients:
    """Bundle de clientes HTTP + API keys, inyectado (nunca global de módulo)."""

    fmp_http: httpx.AsyncClient
    fred_http: httpx.AsyncClient
    treasury_gov_http: httpx.AsyncClient
    fmp_api_key: str
    fred_api_key: Optional[str]


def _annual_series(statements: list[dict], field: str) -> list[float]:
    """FMP devuelve los estados financieros con el año más reciente primero.
    Se invierte a orden cronológico (más antiguo → más reciente) para CAGR/pilares."""
    values = [s.get(field) for s in statements if s.get(field) is not None]
    return list(reversed(values))


async def fetch_and_analyze(ticker: str, clients: Clients, perfil: str) -> str:
    """Trae los datos de un ticker resuelto y arma la respuesta completa."""
    quote = await fmp_client.get_quote(clients.fmp_http, clients.fmp_api_key, ticker)
    profile = await fmp_client.get_profile(clients.fmp_http, clients.fmp_api_key, ticker)
    income_statements = await fmp_client.get_income_statement(
        clients.fmp_http, clients.fmp_api_key, ticker
    )
    balance_sheets = await fmp_client.get_balance_sheet_statement(
        clients.fmp_http, clients.fmp_api_key, ticker
    )
    cash_flows = await fmp_client.get_cash_flow_statement(
        clients.fmp_http, clients.fmp_api_key, ticker
    )

    if not quote or not profile or not income_statements or not balance_sheets or not cash_flows:
        return f"No pude obtener suficientes datos de {ticker} para analizarlo ahora mismo."

    company_name = profile.get("companyName", ticker)
    sector = profile.get("sector", "")
    beta = profile.get("beta") or 1.0
    precio_actual = quote.get("price") or profile.get("price")
    market_cap = quote.get("marketCap") or profile.get("mktCap")

    latest_income = income_statements[0]
    latest_balance = balance_sheets[0]
    net_income = latest_income.get("netIncome")
    shares_outstanding = latest_income.get("weightedAverageShsOutDil") or latest_income.get(
        "weightedAverageShsOut"
    )
    revenue = latest_income.get("revenue")
    cost_of_revenue = latest_income.get("costOfRevenue")
    current_assets = latest_balance.get("totalCurrentAssets")
    current_liabilities = latest_balance.get("totalCurrentLiabilities")

    eps_ttm = rules.calculate_eps(net_income, shares_outstanding)
    if eps_ttm is None:
        eps_ttm = latest_income.get("eps")

    gross_margin = rules.calculate_gross_margin(revenue, cost_of_revenue)
    liquidity = rules.calculate_liquidity_ratio(current_assets, current_liabilities)
    per_result = rules.calculate_per(precio_actual, eps_ttm)
    ps = rules.calculate_ps(market_cap, revenue)

    # Peers (Decisión #9)
    async def _get_quote_for_peer(peer_ticker: str) -> Optional[dict]:
        try:
            return await fmp_client.get_quote(clients.fmp_http, clients.fmp_api_key, peer_ticker)
        except fmp_client.FMPError:
            return None

    peer_result = await peers.get_peer_pe_average(
        get_quote_fn=_get_quote_for_peer, sector=sector, own_ticker=ticker
    )

    # Y (Decisión #7 revisada)
    treasury_source = None
    y_value = None
    try:
        treasury_result = await treasury_client.get_treasury_yield(
            clients.fred_http, clients.treasury_gov_http, clients.fred_api_key
        )
        y_value = treasury_result.y
        treasury_source = treasury_result.source
    except treasury_client.TreasuryError as exc:
        logger.warning("No se pudo obtener Y — %s", exc)

    eps_historial = _annual_series(income_statements, "eps") or _annual_series(
        income_statements, "netIncome"
    )
    fcf_historial = [
        (s.get("operatingCashFlow") or 0) - abs(s.get("capitalExpenditure") or 0)
        for s in reversed(cash_flows)
    ]
    revenue_historial = _annual_series(income_statements, "revenue")
    net_income_historial = _annual_series(income_statements, "netIncome")

    wacc_inputs = {
        "interest_expense": latest_income.get("interestExpense") or 0.0,
        "total_debt": (latest_balance.get("shortTermDebt") or 0.0)
        + (latest_balance.get("longTermDebt") or 0.0),
        "income_tax_expense": latest_income.get("incomeTaxExpense") or 0.0,
        "income_before_tax": latest_income.get("incomeBeforeTax") or 0.0,
        "beta": beta,
        "market_cap": market_cap or 0.0,
    }

    valuation_result = valuation.compute_valuation(
        eps_ttm=eps_ttm,
        eps_historial=eps_historial,
        per_promedio_peers=peer_result.per_promedio,
        fcf_historial=fcf_historial,
        y=y_value,
        wacc_inputs=wacc_inputs,
        shares_outstanding=shares_outstanding or 0.0,
    )

    pillars = rules.evaluate_pillars(
        revenue_historial=revenue_historial,
        net_income_historial=net_income_historial,
        liquidity=liquidity,
        barata=(
            precio_actual < valuation_result.valor_justo_total
            if valuation_result.valor_justo_total is not None
            else None
        ),
    )

    risk_fit_result = risk_fit.evaluate_risk_fit(beta, perfil)

    ratios_dict = {
        "ratio_liquidez": liquidity.ratio_liquidez,
        "liquidez_sin_pasivos_circulantes": liquidity.liquidez_sin_pasivos_circulantes,
        "margen_bruto": gross_margin,
        "per": per_result.per,
        "per_no_aplicable": per_result.per_no_aplicable,
        "ps": ps,
    }
    pillars_dict = {
        "ingresos_crecientes": pillars.ingresos_crecientes,
        "utilidades_crecientes": pillars.utilidades_crecientes,
        "deuda_controlada": pillars.deuda_controlada,
        "precio_razonable": pillars.precio_razonable,
    }
    risk_fit_dict = {
        "encaja": risk_fit_result.encaja,
        "perfil": risk_fit_result.perfil,
        "beta": risk_fit_result.beta,
        "etiqueta_activo": risk_fit_result.etiqueta_activo,
    }

    return summary.build_summary(
        ticker=ticker,
        company_name=company_name,
        precio_actual=precio_actual or 0.0,
        ratios=ratios_dict,
        pillars=pillars_dict,
        valuation=valuation_result.as_dict(),
        risk_fit=risk_fit_dict,
        treasury_source=treasury_source,
    )


def build_query_handlers(
    get_conn: Callable[[], sqlite3.Connection],
    clients: Clients,
    rate_limiter,
) -> list:
    """Devuelve los handlers de texto libre + callback de desambiguación."""

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        conn = get_conn()
        try:
            db.init_db(conn)
            profile = db.get_risk_profile(conn)
        finally:
            conn.close()

        if profile is None:
            await update.message.reply_text(NO_ONBOARDING_MSG)
            return
        perfil = profile["perfil"]

        raw_text = update.message.text or ""
        normalized = normalize_query(raw_text)
        if normalized is None:
            await update.message.reply_text(INPUT_TOO_LONG_MSG)
            return

        chat_key = str(update.effective_chat.id)
        if not rate_limiter.allow(chat_key):
            await update.message.reply_text(RATE_LIMITED_MSG)
            return

        logger.info("Consulta recibida: %s", sanitize_for_log(normalized))

        try:
            matches = await fmp_client.search_company(
                clients.fmp_http, clients.fmp_api_key, normalized
            )
        except fmp_client.FMPError as exc:
            await update.message.reply_text(str(exc))
            return

        exact = [m for m in matches if m.get("symbol", "").upper() == normalized.upper()]
        if exact:
            resolved = exact[0]["symbol"]
        elif len(matches) == 1:
            resolved = matches[0]["symbol"]
        elif len(matches) == 0:
            await update.message.reply_text(NO_MATCHES_MSG.format(query=normalized))
            return
        else:
            buttons = [
                [
                    InlineKeyboardButton(
                        f"{m.get('symbol')} — {m.get('name', '')}",
                        callback_data=f"tk:{m.get('symbol')}",
                    )
                ]
                for m in matches[:5]
            ]
            await update.message.reply_text(
                "Encontré varias coincidencias, elegí una:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        await _run_analysis(update.message.reply_text, resolved, perfil)

    async def handle_disambiguation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        ticker = query.data.split(":", 1)[1]

        conn = get_conn()
        try:
            db.init_db(conn)
            profile = db.get_risk_profile(conn)
        finally:
            conn.close()
        perfil = profile["perfil"] if profile is not None else "moderado"

        await _run_analysis(query.edit_message_text, ticker, perfil)

    async def _run_analysis(reply_fn, ticker: str, perfil: str) -> None:
        try:
            text = await fetch_and_analyze(ticker, clients, perfil)
        except (fmp_client.FMPError, treasury_client.TreasuryError) as exc:
            await reply_fn(str(exc))
            return
        except Exception:
            logger.exception("Error inesperado analizando %s", ticker)
            await reply_fn(GENERIC_ERROR_MSG)
            return
        await reply_fn(text, parse_mode="Markdown")

    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        CallbackQueryHandler(handle_disambiguation, pattern=r"^tk:"),
    ]
