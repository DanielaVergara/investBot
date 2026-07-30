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
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from investbot import (
    corporate_events,
    db,
    finnhub_client,
    fmp_client,
    market_context,
    peers,
    risk_fit,
    rules,
    sec_edgar_client,
    summary,
    treasury_client,
    valuation,
)

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 100
NO_ONBOARDING_MSG = "Antes necesito que corras /start para fijar tu perfil de riesgo."
NO_MATCHES_MSG = "No encontré ninguna empresa que coincida con \"{query}\". Probá con el ticker exacto."
INPUT_TOO_LONG_MSG = f"Ese texto es demasiado largo (máximo {MAX_INPUT_LENGTH} caracteres). Probá con un ticker o nombre corto."
RATE_LIMITED_MSG = "Estás consultando muy rápido — esperá un minuto antes de volver a intentar."
GENERIC_ERROR_MSG = "No pude completar el análisis ahora mismo. Intenta más tarde."
LOADING_MSG = "🔍 Analizando {ticker}, dame un toque..."

TELEGRAM_MESSAGE_LIMIT = 4096
# Margen reservado en cada chunk para el prefijo de continuación
# "_(cont. parte N/M)_\n\n" (Decisión 17.1) — conservador a propósito.
_CONTINUATION_PREFIX_RESERVE = 40
_CONTINUATION_PREFIX = "_(cont. parte {i}/{n})_\n\n"

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
    # NUEVO (SDD_peers_dinamicos_y_eventos_corporativos.md) — con default
    # `None` explícito: un campo de dataclass sin `= valor` es
    # posicional/keyword obligatorio aunque su tipo sea `Optional[...]`, y
    # eso rompería con `TypeError` los ~14 call sites existentes de
    # `Clients(...)` en tests que no conocen estos 2 proveedores nuevos
    # (criterio obligatorio de `qa`, sección 1.1 de la spec).
    finnhub_http: Optional[httpx.AsyncClient] = None
    finnhub_api_key: Optional[str] = None
    sec_edgar_http: Optional[httpx.AsyncClient] = None
    sec_edgar_user_agent: Optional[str] = None


def _annual_series(statements: list[dict], field: str) -> list[float]:
    """FMP devuelve los estados financieros con el año más reciente primero.
    Se invierte a orden cronológico (más antiguo → más reciente) para CAGR/pilares."""
    values = [s.get(field) for s in statements if s.get(field) is not None]
    return list(reversed(values))


async def fetch_and_analyze_parts(ticker: str, clients: Clients, perfil: str) -> list[str]:
    """Trae los datos de un ticker resuelto y arma la respuesta completa,
    devuelta como lista de secciones sin unir (`summary.build_summary_parts`)
    — permite a `_run_analysis` particionar en varios mensajes de Telegram
    sin cortar a mitad de sección (Decisión 16)."""
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
        return [f"No pude obtener suficientes datos de {ticker} para analizarlo ahora mismo."]

    # Rentabilidad/deuda/dividendos del ticker propio (Decisión #1) —
    # best-effort, nunca bloquea el resto del análisis.
    try:
        own_metrics_list = await fmp_client.get_key_metrics(
            clients.fmp_http, clients.fmp_api_key, ticker, limit=1
        )
        own_metrics = own_metrics_list[0] if own_metrics_list else None
    except fmp_client.FMPError:
        own_metrics = None

    # VIX (Decisión #7) — best-effort, nunca bloquea el resto del análisis.
    try:
        vix_quote = await fmp_client.get_quote(
            clients.fmp_http, clients.fmp_api_key, market_context.VIX_SYMBOL
        )
    except fmp_client.FMPError:
        vix_quote = None

    company_name = profile.get("companyName", ticker)
    sector = profile.get("sector", "")
    beta = profile.get("beta") or 1.0
    precio_actual = quote.get("price") or profile.get("price")
    market_cap = quote.get("marketCap") or profile.get("marketCap")

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

    # Peers (Decisión #9, PER derivado de key-metrics anual — key-metrics-ttm
    # es un endpoint de pago en el plan gratuito actual de FMP, ver peers.py)
    async def _get_metrics_for_peer(peer_ticker: str) -> Optional[dict]:
        try:
            data = await fmp_client.get_key_metrics(
                clients.fmp_http, clients.fmp_api_key, peer_ticker, limit=1
            )
        except fmp_client.FMPError:
            return None
        return data[0] if data else None

    # Peers dinámicos vía Finnhub (Parte 1, SDD_peers_dinamicos_y_eventos_
    # corporativos.md) — best-effort: si `clients.finnhub_api_key` no está
    # configurada, ni siquiera se intenta la llamada (mismo patrón que
    # treasury_client.get_treasury_yield con fred_api_key).
    async def _get_finnhub_peers(peer_ticker_query: str) -> list[str]:
        try:
            return await finnhub_client.get_peers(
                clients.finnhub_http, clients.finnhub_api_key, peer_ticker_query,
                grouping="subIndustry",
            )
        except finnhub_client.FinnhubError:
            return []

    peer_result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=_get_metrics_for_peer,
        sector=sector,
        own_ticker=ticker,
        get_dynamic_peers_fn=_get_finnhub_peers if clients.finnhub_api_key else None,
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

    # Spec Patch Iter-3: 3 escenarios (Pesimista/Conservador/Optimista) de los
    # 3 modelos, reutilizando exactamente los mismos datos ya resueltos
    # arriba — ninguna llamada HTTP adicional. `scenarios.conservador` es
    # campo a campo idéntico a lo que devolvía `compute_valuation(...)` antes
    # de este patch (sigue existiendo sin cambios, pero ya no hace falta
    # llamarla aparte: sería recalcular lo mismo).
    scenarios = valuation.compute_valuation_scenarios(
        eps_ttm=eps_ttm,
        eps_historial=eps_historial,
        peer_average=peer_result,
        fcf_historial=fcf_historial,
        y=y_value,
        wacc_inputs=wacc_inputs,
        shares_outstanding=shares_outstanding or 0.0,
    )
    conservador = scenarios.conservador

    pillars = rules.evaluate_pillars(
        revenue_historial=revenue_historial,
        net_income_historial=net_income_historial,
        liquidity=liquidity,
        barata=(
            precio_actual < conservador.valor_justo_total
            if conservador.valor_justo_total is not None
            else None
        ),
    )

    risk_fit_result = risk_fit.evaluate_risk_fit(beta, perfil)

    # Contexto de mercado (Spec Patch Iter-3, sección 6) — cero llamadas HTTP
    # nuevas: momentum usa campos ya presentes en `quote` (ya obtenido
    # arriba); la comparación con peers reutiliza `peer_result` (ya
    # calculado arriba para el modelo de Múltiplos).
    momentum_result = market_context.calculate_momentum(
        price=precio_actual or 0.0,
        year_high=quote.get("yearHigh"),
        year_low=quote.get("yearLow"),
        price_avg_50=quote.get("priceAvg50"),
        price_avg_200=quote.get("priceAvg200"),
    )
    peer_comparison_result = market_context.compare_to_peers(
        per_propio=per_result.per,
        per_minimo_peers=peer_result.per_minimo,
        per_promedio_peers=peer_result.per_promedio,
        per_maximo_peers=peer_result.per_maximo,
        peers_usados=peer_result.peers_usados,
        peers_pe=peer_result.peers_pe,
        peers_no_usados=peer_result.peers_no_usados,
        fuente_peers=peer_result.fuente_peers,
    )

    # Eventos corporativos vía SEC EDGAR (Parte 2, SDD_peers_dinamicos_y_
    # eventos_corporativos.md) — best-effort, deliberadamente no participa
    # del abort-check de arriba (mismo principio que own_metrics/VIX): si
    # SEC EDGAR falla, no responde, o el ticker no tiene CIK, el resto del
    # análisis sigue exactamente igual, la sección se omite sin ruido. Si
    # `sec_edgar_user_agent` no está configurada, cero llamadas de red.
    corporate_events_list: list[dict] = []
    if clients.sec_edgar_user_agent:
        cik10 = await sec_edgar_client.get_cik_for_ticker(
            clients.sec_edgar_http, clients.sec_edgar_user_agent, ticker
        )
        submissions = (
            await sec_edgar_client.get_submissions(
                clients.sec_edgar_http, clients.sec_edgar_user_agent, cik10
            )
            if cik10
            else None
        )
        events = corporate_events.extract_relevant_8k_events(submissions, cik10)
        corporate_events_list = [
            {"filing_date": e.filing_date, "labels": e.labels, "filing_url": e.filing_url}
            for e in events
        ]

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
    momentum_dict = {
        "pct_vs_year_high": momentum_result.pct_vs_year_high,
        "pct_vs_year_low": momentum_result.pct_vs_year_low,
        "pct_vs_avg_50": momentum_result.pct_vs_avg_50,
        "pct_vs_avg_200": momentum_result.pct_vs_avg_200,
        "etiqueta": momentum_result.etiqueta,
    }
    peer_comparison_dict = {
        "per_propio": peer_comparison_result.per_propio,
        "per_minimo_peers": peer_comparison_result.per_minimo_peers,
        "per_promedio_peers": peer_comparison_result.per_promedio_peers,
        "per_maximo_peers": peer_comparison_result.per_maximo_peers,
        "peers_usados": peer_comparison_result.peers_usados,
        "posicion": peer_comparison_result.posicion,
        "motivo_no_comparable": peer_comparison_result.motivo_no_comparable,
        "peers_pe": peer_comparison_result.peers_pe,
        "peers_no_usados": peer_comparison_result.peers_no_usados,
        "fuente_peers": peer_comparison_result.fuente_peers,
    }

    extras_result = rules.extract_key_metrics_extras(own_metrics)
    extras_dict = {
        "roe": extras_result.roe,
        "debt_to_equity": extras_result.debt_to_equity,
        "net_debt_to_ebitda": extras_result.net_debt_to_ebitda,
        "dividend_yield": extras_result.dividend_yield,
        "payout_ratio": extras_result.payout_ratio,
    }
    vix_result = market_context.extract_vix_context(vix_quote)
    vix_dict = {"valor": vix_result.valor, "disponible": vix_result.disponible}

    return summary.build_summary_parts(
        ticker=ticker,
        company_name=company_name,
        precio_actual=precio_actual or 0.0,
        ratios=ratios_dict,
        pillars=pillars_dict,
        scenarios=scenarios.as_dict(),
        n_peers_validos=len(peer_result.peers_usados),
        momentum=momentum_dict,
        peer_comparison=peer_comparison_dict,
        risk_fit=risk_fit_dict,
        treasury_source=treasury_source,
        extras=extras_dict,
        vix=vix_dict,
        corporate_events=corporate_events_list,
    )


async def fetch_and_analyze(ticker: str, clients: Clients, perfil: str) -> str:
    """Wrapper de compatibilidad — todo lo que hoy llama `fetch_and_analyze`
    sigue recibiendo el mismo `str` (mismo patrón que
    `summary.build_summary`/`summary.build_summary_parts`, Decisión 16.1)."""
    return "\n\n".join(await fetch_and_analyze_parts(ticker, clients, perfil))


def _split_oversized_part(part: str, limit: int) -> list[str]:
    """Parte una sección que por sí sola supera `limit`, probando cortes
    cada vez más finos: párrafo ("\\n\\n") -> línea ("\\n") -> oración
    (". ") -> corte duro con marcador visible + log. Nunca corta a mitad de
    palabra salvo el último recurso absoluto (Decisión 18)."""
    for separator in ("\n\n", "\n", ". "):
        pieces = part.split(separator)
        if len(pieces) > 1:
            chunks, current = [], ""
            for piece in pieces:
                candidate = current + (separator if current else "") + piece
                if len(candidate) > limit and current:
                    chunks.append(current)
                    current = piece
                else:
                    current = candidate
            if current:
                chunks.append(current)
            if all(len(c) <= limit for c in chunks):
                return chunks
    # Último recurso: no debería llegar acá con el contenido actual del
    # proyecto. Corte duro + marcador visible + log explícito (Decisión 19:
    # nunca perder contenido en silencio).
    logger.error(
        "Sección individual de %d caracteres no se pudo partir por párrafo/"
        "línea/oración dentro del límite de Telegram — corte duro aplicado. "
        "Texto completo:\n%s", len(part), part,
    )
    marker = "\n\n⚠️ _[recortado por límite de Telegram — ver logs]_"
    return [part[: limit - len(marker)] + marker]


def chunk_for_telegram(
    parts: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """Empaqueta `parts` (las secciones de summary.build_summary_parts) en
    la menor cantidad de mensajes de Telegram posible, sin superar `limit`
    caracteres por mensaje. Nunca corta el contenido de una sección salvo
    que una sección sola ya supere `limit` (Decisión 18). Determinístico,
    sin I/O, sin conocimiento de python-telegram-bot."""
    budget = limit - _CONTINUATION_PREFIX_RESERVE
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        if current:
            chunks.append("\n\n".join(current))

    for part in parts:
        if len(part) > budget:
            flush()
            current, current_len = [], 0
            chunks.extend(_split_oversized_part(part, budget))
            continue
        added_len = len(part) + (2 if current else 0)  # "\n\n" entre partes
        if current_len + added_len > budget:
            flush()
            current, current_len = [part], len(part)
        else:
            current.append(part)
            current_len += added_len
    flush()
    return chunks or [""]


def _with_continuation_prefixes(chunks: list[str]) -> list[str]:
    """Antepone el prefijo de continuación (Decisión 17.1) a los chunks 2..N.
    No-op si hay 1 solo chunk (caso de hoy)."""
    if len(chunks) <= 1:
        return chunks
    n = len(chunks)
    return [
        chunk if i == 1 else _CONTINUATION_PREFIX.format(i=i, n=n) + chunk
        for i, chunk in enumerate(chunks, start=1)
    ]


def _hard_truncate_with_marker(parts: list[str]) -> str:
    """Red de seguridad de último recurso (Decisión 19): un fallo inesperado
    de `chunk_for_telegram` en sí (bug propio, no un fallo de Telegram) nunca
    debe tumbar `_run_analysis` — se entrega un único mensaje truncado, con
    el contenido completo logueado antes de truncar."""
    full = "\n\n".join(parts)
    logger.error(
        "chunk_for_telegram falló de forma inesperada (%d caracteres, %d "
        "secciones) — se entrega un único mensaje truncado como último "
        "recurso. Texto completo:\n%s", len(full), len(parts), full,
    )
    marker = "\n\n⚠️ _[mensaje recortado por un error interno — el análisis completo quedó en los logs]_"
    return full[: TELEGRAM_MESSAGE_LIMIT - len(marker)] + marker


async def _deliver_all(reply_fn, first_msg, remaining_or_all, ticker, **kwargs) -> None:
    """Entrega los chunks restantes. Si `first_msg` es `None`,
    `remaining_or_all` incluye el chunk 0 y se manda por `reply_fn` (mismo
    comportamiento que hoy cuando no hay `loading_msg`); el resto se manda
    con `.chat.send_message` sobre el `Message` que devuelve esa primera
    llamada."""
    chunks = remaining_or_all
    if first_msg is None:
        first_msg = await reply_fn(chunks[0], **kwargs)
        chunks = chunks[1:]
    for i, chunk in enumerate(chunks, start=2):
        try:
            await first_msg.chat.send_message(chunk, **kwargs)
        except TelegramError as exc:
            logger.error(
                "No se pudo enviar la parte %d del análisis de %s — esa parte no "
                "llegó a Telegram: %s", i, ticker, exc,
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
        loading_msg = None
        try:
            loading_msg = await reply_fn(LOADING_MSG.format(ticker=ticker))
        except TelegramError as exc:
            logger.warning(
                "No se pudo enviar el mensaje de carga para %s — %s", ticker, exc
            )

        try:
            parts = await fetch_and_analyze_parts(ticker, clients, perfil)
        except (fmp_client.FMPError, treasury_client.TreasuryError) as exc:
            final_parts, kwargs = [str(exc)], {}
        except Exception:
            logger.exception("Error inesperado analizando %s", ticker)
            final_parts, kwargs = [GENERIC_ERROR_MSG], {}
        else:
            final_parts, kwargs = parts, {"parse_mode": "Markdown"}

        try:
            chunks = chunk_for_telegram(final_parts)
        except Exception:
            logger.exception("Fallo inesperado partiendo el mensaje para %s", ticker)
            chunks = [_hard_truncate_with_marker(final_parts)]

        chunks = _with_continuation_prefixes(chunks)

        if loading_msg is None:
            await _deliver_all(reply_fn, None, chunks, ticker, **kwargs)
            return

        try:
            await loading_msg.edit_text(chunks[0], **kwargs)
        except TelegramError as exc:
            logger.warning(
                "No se pudo editar el mensaje final para %s — %s", ticker, exc
            )
            await _deliver_all(reply_fn, None, chunks, ticker, **kwargs)
            return

        await _deliver_all(reply_fn, loading_msg, chunks[1:], ticker, **kwargs)

    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        CallbackQueryHandler(handle_disambiguation, pattern=r"^tk:"),
    ]
