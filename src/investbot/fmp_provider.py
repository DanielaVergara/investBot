"""`FmpProvider`: extracción literal del bloque de estados financieros y
key-metrics propios que vivía en `query_handler.fetch_and_analyze_parts`
(SDD_fuentes_datos_gratuitas.md, 4.2 / F1b-0). Mismas llamadas a FMP, en el
mismo orden, con el mismo manejo de errores.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

import httpx

from investbot import fmp_client, rules
from investbot.fundamentals import FundamentalsResult


class FmpProvider:
    name = "fmp"

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch(
        self, ticker: str, *, quarters: int, skip_own_metrics: bool = False
    ) -> Optional[FundamentalsResult]:
        """`skip_own_metrics=True` reproduce el abort-check de quote/profile
        vacíos de `query_handler` (no se pide key-metrics en ese caso)."""
        # --- income-statement: trimestral primario, fallback anual condicional
        # (Decisión #8/#10) — diseño atómico (rules.calculate_income_statement_ttm).
        try:
            quarterly_income = await fmp_client.get_income_statement(
                self._http, self._api_key, ticker,
                period="quarter", limit=quarters,
            )
        except fmp_client.FMPError:
            quarterly_income = []

        annual_income: list[dict] = []
        if rules.calculate_income_statement_ttm(quarterly_income).disponible:
            income_fuente = rules.DATOS_FUENTE_TRIMESTRAL
        else:
            annual_income = await fmp_client.get_income_statement(
                self._http, self._api_key, ticker
            )
            income_fuente = rules.DATOS_FUENTE_ANUAL_FALLBACK

        # --- cash-flow-statement: mismo patrón (Decisión #14).
        try:
            quarterly_cash_flow = await fmp_client.get_cash_flow_statement(
                self._http, self._api_key, ticker,
                period="quarter", limit=quarters,
            )
        except fmp_client.FMPError:
            quarterly_cash_flow = []

        fcf_historial_trimestral = [
            (s.get("operatingCashFlow") or 0) - abs(s.get("capitalExpenditure") or 0)
            for s in reversed(quarterly_cash_flow)
        ]
        annual_cash_flow: list[dict] = []
        if rules.calculate_fcf_ttm(fcf_historial_trimestral) is not None:
            cash_fuente = rules.DATOS_FUENTE_TRIMESTRAL
        else:
            annual_cash_flow = await fmp_client.get_cash_flow_statement(
                self._http, self._api_key, ticker
            )
            cash_fuente = rules.DATOS_FUENTE_ANUAL_FALLBACK

        # --- balance-sheet-statement: snapshot del trimestre más reciente
        # (Decisión #16) — `limit=1`.
        try:
            quarterly_balance = await fmp_client.get_balance_sheet_statement(
                self._http, self._api_key, ticker, period="quarter", limit=1
            )
        except fmp_client.FMPError:
            quarterly_balance = []

        annual_balance: list[dict] = []
        if quarterly_balance:
            balance_fuente = rules.DATOS_FUENTE_TRIMESTRAL
        else:
            annual_balance = await fmp_client.get_balance_sheet_statement(
                self._http, self._api_key, ticker
            )
            balance_fuente = rules.DATOS_FUENTE_ANUAL_FALLBACK

        result = FundamentalsResult(
            source="fmp",
            income_q=quarterly_income,
            income_a=annual_income,
            balance_q=quarterly_balance,
            balance_a=annual_balance,
            cash_q=quarterly_cash_flow,
            cash_a=annual_cash_flow,
            own_metrics=None,
            income_fuente=income_fuente,
            cash_fuente=cash_fuente,
            balance_fuente=balance_fuente,
        )

        # Abort-check preservado (Decisión #8): con hueco de datos no se pide
        # key-metrics (el llamador devuelve el mensaje de datos insuficientes).
        if (
            skip_own_metrics
            or not result.income_statements
            or not result.balance_sheets
            or not result.cash_flows
        ):
            return result

        # Rentabilidad/deuda/dividendos del ticker propio (Decisión #1) —
        # best-effort, nunca bloquea el resto del análisis.
        try:
            own_metrics_list = await fmp_client.get_key_metrics(
                self._http, self._api_key, ticker, limit=1
            )
            own_metrics = own_metrics_list[0] if own_metrics_list else None
        except fmp_client.FMPError:
            own_metrics = None

        return dataclasses.replace(result, own_metrics=own_metrics)
