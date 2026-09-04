"""Comando `/avanzado <TICKER>` — orquestación de los 5 modelos cuantitativos
de `advanced_scoring.py` (`SDD_analisis_fundamental_avanzado.md`).

Mismo rol que `query_handler.py` pero acotado a este comando: parsea el
argumento, llama a `fmp_client`, valida "empresa individual con estados
financieros propios" (Decisión de diseño #2), llama a `advanced_scoring`,
arma el mensaje final (texto plano, SIN `parse_mode` — hallazgo 4 de
`security`) y maneja rate-limit/errores con el mismo criterio que
`query_handler._run_analysis`.

Reusa (no duplica) `query_handler.RATE_LIMITED_MSG`/`GENERIC_ERROR_MSG`/
`sanitize_for_log`/`chunk_for_telegram` — hallazgo 3 de `security`: "reusar
la constante de query_handler.py, no duplicarla". El resto de la
orquestación es independiente: este módulo no llama a `handle_text`, no
comparte los botones `tk:`/`esc:`/`vent:`, y no modifica `query_handler.py`.

El `rate_limiter`/`clients` se reciben inyectados en
`build_advanced_command_handler(clients, rate_limiter)` — la MISMA instancia
que ya usa el resto del bot (hallazgo 2 de `security`, el más importante:
la clave del balde compartido es `str(update.effective_chat.id)`, idéntica a
`query_handler.py`, sin prefijo ni transformación).
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

from investbot import ai_explain, fmp_client, market_context, query_handler, rules
from investbot.advanced_scoring import (
    calculate_altman_z,
    calculate_altman_z_prime_prime,
    calculate_beneish_m_score,
    calculate_factor_score,
    calculate_magic_formula_metrics,
    calculate_piotroski_f_score,
    is_asset_light,
)

logger = logging.getLogger(__name__)

# Mismo regex conservador que `query_handler._TICKER_CALLBACK_RE`, duplicado
# localmente (Decisión de diseño #1 — mismo criterio de bajo acoplamiento que
# peers.py/market_context.py, que tampoco importan de query_handler.py).
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")

# Hallazgo 5 de `security` (informativo): tope de longitud antes de loguear
# un argumento inválido — `context.args` no tiene el límite de 64 bytes de
# `callback_data`, un ticker válido nunca supera 10 caracteres.
_MAX_LOGGED_ARG_LENGTH = 40

USAGE_MSG = "Usá /avanzado TICKER, por ejemplo /avanzado AAPL"
NOT_FOUND_MSG = "No encontré esa empresa. Revisá que el ticker sea correcto."
NOT_APPLICABLE_MSG = (
    "Este análisis no aplica a ETFs, fondos ni criptomonedas — solo a "
    "empresas individuales con balance, estado de resultados y flujo de "
    "caja propios."
)

# Línea de transparencia fija (SDD_explicaciones_interactivas_ollama.md,
# Decisión de diseño #6) — `/avanzado` nunca usa Ollama para su mensaje base
# (decisión ya cerrada, no se reabre), así que esto no depende de ningún
# resultado de red: solo de si la feature de botones de explicación está
# habilitada, para no invitar a apretar un botón que siempre va a fallar.
TRANSPARENCY_FIXED_NO_BUTTONS = "📋 Análisis con formato fijo (sin IA)."
TRANSPARENCY_FIXED_WITH_BUTTONS = (
    "📋 Análisis con formato fijo — pedí una explicación con los botones de abajo."
)


def _parse_ticker(args: Optional[list[str]]) -> Optional[str]:
    """Exige EXACTAMENTE un argumento que matchee `_TICKER_RE` (Decisión de
    diseño #1 — ticker exacto, sin resolución difusa por nombre). Se
    normaliza a mayúsculas antes de llamar a FMP (caso límite de `qa`:
    `/avanzado aapl` en minúsculas se acepta igual que `AAPL`)."""
    if not args or len(args) != 1:
        return None
    candidate = args[0].strip().upper()
    if not _TICKER_RE.fullmatch(candidate):
        return None
    return candidate


def _is_etf_or_fund(profile: dict) -> bool:
    """D6 (no bloqueante): campos propuestos por `architect` para distinguir
    un ETF/fondo (`isEtf`/`isFund`/`isActivelyTrading`) — NO verificados con
    `curl` real en este entorno (sin acceso de red a
    site.financialmodelingprep.com, mismo bloqueo ya documentado en la spec).
    Si estos campos no existen o no aplican, la red de seguridad del paso 4
    (estados financieros vacíos, ver `_tiene_estados_financieros`) sigue
    detectando ETFs/fondos/cripto igual — deviation reportada, no
    bloqueante per D6."""
    if profile.get("isEtf") is True or profile.get("isFund") is True:
        return True
    if profile.get("isActivelyTrading") is False:
        return True
    return False


def _num_or_none(value: object) -> Optional[float]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _fmt_num(value: Optional[float]) -> str:
    return f"{value:.2f}" if value is not None else "no disponible"


def _fmt_pct(value: Optional[float]) -> str:
    return f"{value * 100:.1f}%" if value is not None else "no disponible"


_ETIQUETA_LEGIBLE = {
    "alto": "alto",
    "medio": "medio",
    "bajo": "bajo",
    "no_disponible": "no disponible",
}


def _build_message(
    *,
    ticker: str,
    profile: dict,
    quote: dict,
    income_statements: list[dict],
    balance_sheets: list[dict],
    cash_flows: list[dict],
    explain_context_sink: Optional[dict] = None,
    short: bool = False,
) -> str:
    """Arma el único mensaje de Telegram (Decisión de diseño #5), texto
    plano — nunca `parse_mode="Markdown"` (hallazgo 4 de `security`: el
    nombre de la empresa es dato de terceros vía FMP, no sanitizado para
    Markdown).

    `explain_context_sink` (`SDD_explicaciones_interactivas_ollama.md`,
    Decisión de diseño #3): mismo criterio del sink de
    `query_handler.fetch_and_analyze_parts` — keyword-only opcional, si se
    pasa un `dict` se lo puebla in-place con los mismos resultados
    (`altman`/`altman_pp`/`piotroski`/`beneish`/`magic`/`factors`/
    `asset_light`/`sector`/`roe`/`gross_margin`/`beta`) ya calculados acá,
    nunca recalculados.

    `short` (`SDD_menu_por_capas_explicaciones.md`, Decisión de diseño #9):
    default `False` (comportamiento idéntico al de siempre — desglose
    completo de los 5 modelos). `True` solo cuando el llamador ya confirmó
    que Ollama está habilitado — regla de no-regresión D3."""
    income_reciente = income_statements[0]
    income_anterior = income_statements[1] if len(income_statements) > 1 else {}
    balance_reciente = balance_sheets[0]
    balance_anterior = balance_sheets[1] if len(balance_sheets) > 1 else {}
    cash_flow_reciente = cash_flows[0]

    company_name = profile.get("companyName") or ticker
    sector = profile.get("sector")
    industry = profile.get("industry")
    beta = _num_or_none(profile.get("beta"))
    price = _num_or_none(quote.get("price")) or _num_or_none(profile.get("price"))
    market_cap = _num_or_none(quote.get("marketCap")) or _num_or_none(profile.get("marketCap"))

    altman = calculate_altman_z(balance=balance_reciente, income=income_reciente, market_cap=market_cap)
    asset_light = is_asset_light(sector, industry)
    altman_pp = (
        calculate_altman_z_prime_prime(
            balance=balance_reciente, income=income_reciente, market_cap=market_cap
        )
        if asset_light
        else None
    )

    piotroski = calculate_piotroski_f_score(
        balance_reciente=balance_reciente,
        balance_anterior=balance_anterior,
        income_reciente=income_reciente,
        income_anterior=income_anterior,
        cash_flow_reciente=cash_flow_reciente,
    )

    beneish = calculate_beneish_m_score(
        balance_reciente=balance_reciente,
        balance_anterior=balance_anterior,
        income_reciente=income_reciente,
        income_anterior=income_anterior,
        cash_flow_reciente=cash_flow_reciente,
    )

    magic = calculate_magic_formula_metrics(
        balance=balance_reciente, income=income_reciente, market_cap=market_cap
    )

    momentum_result = market_context.calculate_momentum(
        price=price or 0.0,
        year_high=quote.get("yearHigh"),
        year_low=quote.get("yearLow"),
        price_avg_50=quote.get("priceAvg50"),
        price_avg_200=quote.get("priceAvg200"),
    )

    net_income_reciente = _num_or_none(income_reciente.get("netIncome"))
    total_equity = _num_or_none(balance_reciente.get("totalEquity")) or _num_or_none(
        balance_reciente.get("totalStockholdersEquity")
    )
    roe = (
        net_income_reciente / total_equity
        if net_income_reciente is not None and total_equity not in (None, 0)
        else None
    )
    gross_margin = rules.calculate_gross_margin(
        income_reciente.get("revenue"), income_reciente.get("costOfRevenue")
    )

    factors = calculate_factor_score(
        earnings_yield=magic.earnings_yield,
        roe=roe,
        gross_margin=gross_margin,
        piotroski_score=piotroski.puntaje,
        piotroski_evaluables=piotroski.criterios_evaluables or None,
        momentum_etiqueta=momentum_result.etiqueta,
        beta=beta,
    )

    if short:
        # SDD_menu_por_capas_explicaciones.md, Decisión de diseño #9 — el
        # desglose completo de cada modelo (Altman con zona, Piotroski con
        # criterios, Magic Formula con ROIC/EY, Factores con las 4
        # etiquetas) y la línea de "Fuente de los datos" quedan detrás de
        # los botones (la tabla `FUENTES` de la Decisión #7 ya los cubre).
        lineas = [
            f"{ticker} — {company_name}",
            _build_short_synthesis_line(
                altman=altman, piotroski=piotroski, magic=magic, beneish=beneish
            ),
            "👇 Elegí qué modelo querés ver en detalle.",
        ]
        if explain_context_sink is not None:
            explain_context_sink.update(
                company_name=company_name,
                sector=sector,
                industry=industry,
                asset_light=asset_light,
                altman=dataclasses.asdict(altman),
                altman_pp=dataclasses.asdict(altman_pp) if altman_pp is not None else None,
                piotroski=dataclasses.asdict(piotroski),
                beneish=dataclasses.asdict(beneish),
                magic=dataclasses.asdict(magic),
                factors=dataclasses.asdict(factors),
                roe=roe,
                gross_margin=gross_margin,
                beta=beta,
            )
        return "\n".join(lineas)

    lineas = [f"{ticker} — {company_name}"]

    if altman.disponible:
        linea_altman = f"Altman Z-Score: {_fmt_num(altman.z)} (zona: {altman.zona})"
        if altman_pp is not None and altman_pp.disponible:
            linea_altman += f" [Z'': {_fmt_num(altman_pp.z)}, zona: {altman_pp.zona}]"
        elif altman_pp is not None:
            linea_altman += (
                f" [Z'': no calculable — faltan: {', '.join(altman_pp.campos_faltantes)}]"
            )
        lineas.append(linea_altman)
    else:
        lineas.append(
            "Altman Z-Score: no calculable con los datos disponibles "
            f"(faltan: {', '.join(altman.campos_faltantes)})"
        )

    if piotroski.criterios_evaluables == piotroski.criterios_totales:
        lineas.append(f"Piotroski F-Score: {piotroski.puntaje}/{piotroski.criterios_totales}")
    else:
        no_evaluables = [c.nombre for c in piotroski.criterios if c.cumplido is None]
        lineas.append(
            f"Piotroski F-Score: {piotroski.puntaje}/{piotroski.criterios_evaluables} "
            f"evaluables ({len(no_evaluables)} no disponibles: {', '.join(no_evaluables)})"
        )

    lineas.append(f"Beneish M-Score: {beneish.motivo.replace('_', ' ')}")

    if magic.disponible:
        lineas.append(
            f"Magic Formula: ROIC {_fmt_pct(magic.roic)} | "
            f"Earnings Yield {_fmt_pct(magic.earnings_yield)}"
        )
    else:
        lineas.append(
            "Magic Formula: no calculable con los datos disponibles "
            f"(faltan: {', '.join(magic.campos_faltantes)})"
        )

    lineas.append(
        "Factores: Value [{}] · Quality [{}] · Momentum [{}] · Low-vol [{}]".format(
            _ETIQUETA_LEGIBLE[factors.value],
            _ETIQUETA_LEGIBLE[factors.quality],
            _ETIQUETA_LEGIBLE[factors.momentum],
            _ETIQUETA_LEGIBLE[factors.low_vol],
        )
    )

    fecha_periodo = income_reciente.get("date") or "fecha no disponible"
    lineas.append(
        f"Fuente de los datos: FMP (plan gratuito), balance/income/cash-flow "
        f"más recientes disponibles, período fiscal {fecha_periodo}"
    )

    if explain_context_sink is not None:
        # SDD_explicaciones_interactivas_ollama.md, Decisión de diseño #3,
        # extendido por SDD_menu_por_capas_explicaciones.md Decisión de
        # diseño #8 (`roe`/`gross_margin`/`beta`, ya calculados arriba para
        # `factors`, hoy descartados) — los mismos objetos ya calculados
        # arriba, nunca recalculados. `sector`/`industry` viajan crudos
        # hasta acá (dato de terceros de FMP) — `ai_explain._build_explain_
        # payload` es quien los valida contra la allow-list GICS / los
        # excluye antes de tocar el prompt (hallazgo 1 BLOQUEANTE de
        # `security`), no este módulo.
        explain_context_sink.update(
            company_name=company_name,
            sector=sector,
            industry=industry,
            asset_light=asset_light,
            altman=dataclasses.asdict(altman),
            altman_pp=dataclasses.asdict(altman_pp) if altman_pp is not None else None,
            piotroski=dataclasses.asdict(piotroski),
            beneish=dataclasses.asdict(beneish),
            magic=dataclasses.asdict(magic),
            factors=dataclasses.asdict(factors),
            roe=roe,
            gross_margin=gross_margin,
            beta=beta,
        )

    return "\n".join(lineas)


# --- Mensaje corto (Decisión de diseño #9 de
# `SDD_menu_por_capas_explicaciones.md`) -------------------------------


def _build_short_synthesis_line(*, altman, piotroski, magic, beneish) -> str:
    """1-2 líneas: síntesis de qué modelos son calculables — nunca el
    desglose completo (eso queda detrás de los botones)."""
    altman_txt = f"zona {altman.zona}" if altman.disponible else "no calculable"
    if piotroski.criterios_evaluables == piotroski.criterios_totales:
        piotroski_txt = f"{piotroski.puntaje}/{piotroski.criterios_totales}"
    else:
        piotroski_txt = f"{piotroski.puntaje}/{piotroski.criterios_evaluables} evaluables"
    magic_txt = "calculable" if magic.disponible else "no calculable"
    beneish_txt = "no calculable con este plan" if not beneish.disponible else "calculable"
    return (
        f"Altman Z: {altman_txt} · Piotroski {piotroski_txt} · "
        f"Magic Formula {magic_txt} · Beneish: {beneish_txt}."
    )


def build_advanced_command_handler(
    clients, rate_limiter, explanation_store: Optional[ai_explain.ExplanationContextStore] = None
) -> CommandHandler:
    """Construye el `CommandHandler("avanzado", ...)`. `clients` es la MISMA
    instancia de `query_handler.Clients` ya construida en `bot.py`;
    `rate_limiter` es la MISMA instancia de `security.InMemoryRateLimiter`
    ya compartida con el resto del bot — ninguno de los dos se crea acá.

    `explanation_store` (`SDD_explicaciones_interactivas_ollama.md`,
    Decisión de diseño #3): keyword-only opcional con default `None` — mismo
    criterio retrocompatible que `query_handler.build_query_handlers`. En
    producción, `bot.py::build_application` siempre pasa la MISMA instancia
    ya compartida con `ai_explain.build_explain_handler` y con
    `query_handler.build_query_handlers`."""
    explanation_store = explanation_store or ai_explain.ExplanationContextStore()

    async def avanzado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ticker = _parse_ticker(context.args)
        if ticker is None:
            if context.args:
                raw = " ".join(context.args)
                logger.info(
                    "Argumento inválido para /avanzado: %s",
                    query_handler.sanitize_for_log(raw[:_MAX_LOGGED_ARG_LENGTH]),
                )
            await update.message.reply_text(USAGE_MSG)
            return

        chat_key = str(update.effective_chat.id)
        if not rate_limiter.allow(chat_key):
            await update.message.reply_text(query_handler.RATE_LIMITED_MSG)
            return

        # SDD_menu_por_capas_explicaciones.md, Decisión de diseño #9 —
        # calculado ANTES de `_build_message` (regla de no-regresión D3: el
        # acortamiento del mensaje solo aplica con Ollama habilitado).
        ollama_enabled = bool(clients.ollama_config and clients.ollama_config.enabled)

        try:
            profile = await fmp_client.get_profile(clients.fmp_http, clients.fmp_api_key, ticker)
            if not profile:
                await update.message.reply_text(NOT_FOUND_MSG)
                return

            if _is_etf_or_fund(profile):
                await update.message.reply_text(NOT_APPLICABLE_MSG)
                return

            quote = await fmp_client.get_quote(clients.fmp_http, clients.fmp_api_key, ticker)
            income_statements = await fmp_client.get_income_statement(
                clients.fmp_http, clients.fmp_api_key, ticker, period="annual", limit=2
            )
            balance_sheets = await fmp_client.get_balance_sheet_statement(
                clients.fmp_http, clients.fmp_api_key, ticker, period="annual", limit=2
            )
            cash_flows = await fmp_client.get_cash_flow_statement(
                clients.fmp_http, clients.fmp_api_key, ticker, period="annual", limit=2
            )

            # Red de seguridad del paso 4 (Decisión de diseño #2): un
            # símbolo raro/cripto sin flag de ETF/fondo en `/profile` pero
            # sin estados financieros propios también se rechaza acá.
            if not quote or not income_statements or not balance_sheets or not cash_flows:
                await update.message.reply_text(NOT_APPLICABLE_MSG)
                return

            explain_context_sink: dict = {}
            message = _build_message(
                ticker=ticker,
                profile=profile,
                quote=quote,
                income_statements=income_statements,
                balance_sheets=balance_sheets,
                cash_flows=cash_flows,
                explain_context_sink=explain_context_sink,
                short=ollama_enabled,
            )
        except fmp_client.FMPError as exc:
            await update.message.reply_text(str(exc))
            return
        except Exception:
            # Hallazgo 3 de `security` (paridad con `_run_analysis`): nunca
            # se deja al usuario sin respuesta ante un bug no previsto (ej.
            # `ZeroDivisionError`/`KeyError` de `advanced_scoring`).
            logger.exception(
                "Error inesperado en /avanzado para %s", query_handler.sanitize_for_log(ticker)
            )
            await update.message.reply_text(query_handler.GENERIC_ERROR_MSG)
            return

        # SDD_explicaciones_interactivas_ollama.md, Decisión de diseño #6 —
        # línea de transparencia FIJA (nunca depende de Ollama, `/avanzado`
        # no lo usa para su mensaje base): variante "con botones" solo si la
        # feature está efectivamente habilitada, para no invitar a apretar
        # un botón que siempre va a fallar.
        transparency = (
            TRANSPARENCY_FIXED_WITH_BUTTONS if ollama_enabled else TRANSPARENCY_FIXED_NO_BUTTONS
        )
        message = f"{transparency}\n\n{message}"

        keyboard = None
        if ollama_enabled and explain_context_sink:
            explanation_context = ai_explain.ExplanationContext(
                chat_id=update.effective_chat.id,
                kind="avanzado",
                ticker=ticker,
                company_name=explain_context_sink["company_name"],
                sector=explain_context_sink["sector"],
                industry=explain_context_sink["industry"],
                asset_light=explain_context_sink["asset_light"],
                altman=explain_context_sink["altman"],
                altman_pp=explain_context_sink["altman_pp"],
                piotroski=explain_context_sink["piotroski"],
                beneish=explain_context_sink["beneish"],
                magic=explain_context_sink["magic"],
                factors=explain_context_sink["factors"],
                roe=explain_context_sink["roe"],
                gross_margin=explain_context_sink["gross_margin"],
                beta=explain_context_sink["beta"],
            )
            context_id = explanation_store.put(explanation_context)
            keyboard = ai_explain.build_keyboard("avanzado", context_id)

        # Defensivo (Decisión de diseño #5): un solo ticker no debería
        # acercarse al límite de Telegram, a diferencia del análisis
        # completo — pero se reusa el mismo partidor por las dudas. Nunca
        # `parse_mode="Markdown"` (hallazgo 4 de `security`). El teclado
        # (si corresponde) se adjunta ÚNICAMENTE al último chunk.
        chunks = query_handler.chunk_for_telegram([message])
        last_index = len(chunks) - 1
        try:
            for idx, chunk in enumerate(chunks):
                if idx == last_index and keyboard is not None:
                    await update.message.reply_text(chunk, reply_markup=keyboard)
                else:
                    await update.message.reply_text(chunk)
        except TelegramError as exc:
            # Fix urgente 2026-09-04 (paridad con `query_handler._deliver_all`):
            # si la entrega del mensaje final de `/avanzado` falla a mitad de
            # camino, la excepción antes subía sin capturar hasta el error
            # handler global y el usuario se quedaba sin ninguna respuesta.
            # Último recurso: un intento de aviso genérico, sin `reply_markup`
            # ni ningún dato del mensaje que falló -- si también falla, se
            # loguea a WARNING y se descarta en silencio.
            logger.error(
                "No se pudo entregar el mensaje final de /avanzado para %s — "
                "se le avisa al usuario con un mensaje genérico: %s",
                query_handler.sanitize_for_log(ticker), exc,
            )
            try:
                await update.message.reply_text(query_handler.GENERIC_ERROR_MSG)
            except TelegramError:
                logger.warning(
                    "Tampoco se pudo avisarle al usuario con el mensaje "
                    "genérico para /avanzado %s",
                    query_handler.sanitize_for_log(ticker), exc_info=True,
                )

    return CommandHandler("avanzado", avanzado)
