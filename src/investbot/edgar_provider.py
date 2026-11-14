"""`EdgarProvider`: estados financieros desde SEC EDGAR `companyfacts`
(SDD_fuentes_datos_gratuitas.md, 4.2, 4.6, 15.1 CS-15, 15.4 D-18/D-19).

Une `sec_edgar_client` (red: ticker->CIK, `companyfacts`) con el parser puro
`edgar_xbrl` y entrega un `FundamentalsResult` con `source="sec_edgar"`. Si no
puede servir (sin CIK, sin cobertura, bloqueo, circuito abierto, `submissions`
de otro ticker...) lanza `ProviderUnavailable(code)` con un codigo del enum
cerrado y la cadena sigue con FMP: nunca inventa un dato.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Callable, Optional

import httpx

from investbot import rules, sec_edgar_client
from investbot.edgar_xbrl import FundamentalsPackage, parse_company_facts
from investbot.fundamentals import (
    FALLBACK_CODES,
    FundamentalsResult,
    ProviderUnavailable,
)
from investbot.sec_edgar_client import SecEdgarError


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _norm_ticker(raw: str) -> str:
    return str(raw).upper().replace(".", "-")


class EdgarProvider:
    name = "sec_edgar"
    cacheable = True  # `FundamentalsService` guarda su resultado parseado

    def __init__(
        self,
        http: httpx.AsyncClient,
        user_agent: Optional[str],
        *,
        today: Callable[[], date] = _today_utc,
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._today = today
        # CS-16: una sola descarga MAS parseo de `companyfacts` a la vez. El
        # semaforo del cliente cubre la descarga y el `json.loads`; este cubre
        # ademas `parse_company_facts`, que mantiene vivo el `dict` crudo.
        self._facts_lock = asyncio.Lock()

    async def fetch(
        self, ticker: str, *, quarters: int, skip_own_metrics: bool = False
    ) -> Optional[FundamentalsResult]:
        # `skip_own_metrics` solo ahorra una llamada a FMP; aqui las metricas
        # propias salen del mismo documento sin costo extra.
        if not self._user_agent:
            raise ProviderUnavailable("ua_no_configurado")

        cik = await sec_edgar_client.get_cik_for_ticker(self._http, self._user_agent, ticker)
        if cik is None:
            # `get_cik_for_ticker` no lanza: distingue un mapa que no pudo
            # cargarse porque el breaker esta abierto de un ticker sin CIK.
            gate = sec_edgar_client.gate_for(self._http)
            if gate.open_until is not None and gate.now() < gate.open_until:
                raise ProviderUnavailable("circuito_abierto")
            raise ProviderUnavailable("sin_cik")

        if not self._submissions_confirm(cik, ticker):
            raise ProviderUnavailable("cik_no_coincide")

        async with self._facts_lock:
            try:
                raw = await sec_edgar_client.get_company_facts(self._http, self._user_agent, cik)
            except SecEdgarError as exc:
                code = exc.code if exc.code in FALLBACK_CODES else "no_disponible_temporal"
                raise ProviderUnavailable(code) from None
            outcome = await asyncio.to_thread(
                parse_company_facts,
                raw,
                quarters=quarters,
                today=self._today(),
                expected_cik=cik,
            )
            del raw  # el documento crudo (hasta 12 MiB, ~70 MB parseado) no se retiene
        if outcome.result is None:
            raise ProviderUnavailable(outcome.code)  # type: ignore[arg-type]
        return _to_result(outcome.result)

    def _submissions_confirm(self, cik: str, ticker: str) -> bool:
        """CS-15: si `submissions` YA esta en cache, su lista `tickers` debe
        contener el ticker (`.`->`-`, mayusculas). Nunca se pide solo para esto."""
        cached = sec_edgar_client.gate_for(self._http).submissions.get(cik)
        if cached is None:
            return True
        tickers = cached[1].get("tickers")
        if not isinstance(tickers, list):
            return True  # no verificable: se confia en la comparacion de `cik` del parser
        buscado = _norm_ticker(ticker)
        return any(isinstance(t, str) and _norm_ticker(t) == buscado for t in tickers)


def _to_result(pkg: FundamentalsPackage) -> FundamentalsResult:
    """Adaptador al contrato que ya consume `query_handler` (mismos flags de
    fuente que calcula `FmpProvider`: trimestral si el paquete TTM esta
    disponible, si no anual)."""
    income_fuente = (
        rules.DATOS_FUENTE_TRIMESTRAL
        if rules.calculate_income_statement_ttm(pkg.income_q).disponible
        else rules.DATOS_FUENTE_ANUAL_FALLBACK
    )
    # M-1: con fuente SEC un faltante no es 0 (serie truncada -> respaldo anual).
    fcf_historial = rules.build_fcf_historial(pkg.cash_q, strict=True)
    cash_fuente = (
        rules.DATOS_FUENTE_TRIMESTRAL
        if rules.calculate_fcf_ttm(fcf_historial) is not None
        else rules.DATOS_FUENTE_ANUAL_FALLBACK
    )
    balance_fuente = (
        rules.DATOS_FUENTE_TRIMESTRAL if pkg.balance_q else rules.DATOS_FUENTE_ANUAL_FALLBACK
    )
    return FundamentalsResult(
        source="sec_edgar",
        income_q=pkg.income_q,
        income_a=pkg.income_a,
        balance_q=pkg.balance_q,
        balance_a=pkg.balance_a,
        cash_q=pkg.cash_q,
        cash_a=pkg.cash_a,
        own_metrics=pkg.own_metrics,
        income_fuente=income_fuente,
        cash_fuente=cash_fuente,
        balance_fuente=balance_fuente,
    )
