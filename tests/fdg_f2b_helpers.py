"""Helpers de los tests de F2b (SDD_fuentes_datos_gratuitas.md): un despachador por HOST
(finnhub.io, data.sec.gov / www.sec.gov, api.stlouisfed.org, financialmodelingprep.com) sobre
`httpx.MockTransport`, con empresas y payloads SINTETICOS (CS-26: ninguna captura real; las
claves de Finnhub siguen NO VERIFICADAS). Sin red ni tiempo reales."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from investbot import (
    edgar_provider,
    finnhub_client,
    fmp_provider,
    market_data,
    query_handler,
)
from investbot import sec_edgar_client as sec
from investbot.fundamentals import FundamentalsService

from .edgar_helpers import companyfacts
from .fdg_f1b3_helpers import Clock as SecClock
from .fdg_f1b3_helpers import never_sleep
from .fdg_f2a_helpers import KEY, WALL0, Clock
from .test_edgar_xbrl import CIK, HOY, empresa
from .test_fdg_f1b3_edgar_provider import CIK10, UA, Server, seed

HOY_UTC = datetime.fromtimestamp(WALL0, tz=timezone.utc).date()
CANARIO_FH = "CANARIO_FH"  # nombre / industria de Finnhub que NO debe llegar a Gemini
CANARIO_SIC = "CANARIO_SIC"
CANARIO_ENT = "CANARIO_ENT"

PEERS = ("PEERA", "PEERB", "PEERC")
# peer -> (CIK, marketCapitalization en millones de USD)
PEER_DATA = {"PEERA": (2000001, 400.0), "PEERB": (2000002, 800.0), "PEERC": (2000003, 1600.0)}


def finnhub_profile(symbol: str, *, cap: float = 2000.0, name: str = "Mercado Sintetico SA", currency: str = "USD"):
    shares = cap / 187.5  # coherente con c=187.5 (CS-27: dentro del 25%)
    return {
        "name": name,
        "ticker": symbol,
        "currency": currency,
        "marketCapitalization": cap,
        "shareOutstanding": shares,
        "finnhubIndustry": CANARIO_FH,
    }


def finnhub_quote() -> dict:
    return {"c": 187.5, "d": 1.2, "dp": 0.64, "h": 190.0, "l": 185.0, "o": 186.0, "pc": 186.3, "t": WALL0 - 60}


def finnhub_metric(**over) -> dict:
    m = {"beta": 1.25, "52WeekHigh": 200.0, "52WeekLow": 120.0, "peTTM": 31.0}
    m.update(over)
    return {"metric": m, "metricType": "all"}


def fmp_quote(symbol: str) -> dict:
    return {"symbol": symbol, "price": 190.0, "marketCap": 2.0e9, "yearHigh": 210.0, "yearLow": 110.0,
            "priceAvg50": 180.0, "priceAvg200": 170.0}


def fmp_profile(symbol: str, **over) -> dict:
    base = {"symbol": symbol, "companyName": "FMP Sintetica SA", "sector": "Healthcare", "beta": 1.1,
            "marketCap": 2.0e9, "price": 190.0, "isEtf": False, "isFund": False}
    base.update(over)
    return base


def fred_body(series: str) -> dict:
    if series == "VIXCLS":
        obs = [{"date": HOY_UTC.isoformat(), "value": "17.5"}, {"date": "2000-01-01", "value": "."}]
    else:  # DGS20
        obs = [{"date": HOY_UTC.isoformat(), "value": "4.50"}]
    return {"observations": obs}


def submissions(*, sic="7372", sic_desc="Services-Prepackaged Software", tickers=("MELI",)) -> dict:
    return {
        "cik": CIK10,
        "name": CANARIO_ENT,
        "tickers": list(tickers),
        "sic": sic,
        "sicDescription": sic_desc,
        "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": []}},
    }


class World:
    """Despachador por host. `fmp` es "down" (todo 402/500), "ok" (lo `fmp_ok`) o un callable."""

    def __init__(self, *, fmp="down", ticker="MELI", sic="7372", sic_desc="Services-Prepackaged Software") -> None:
        self.ticker = ticker
        self.requests: list[httpx.Request] = []
        self.sec_server = Server()
        self.sec_server.facts[CIK10] = companyfacts(*empresa(), cik=CIK, entity=CANARIO_ENT)
        self.sec_server.subs[CIK10] = submissions(sic=sic, sic_desc=sic_desc, tickers=(ticker,))
        mapa = {ticker: CIK10}
        for peer, (cik, _cap) in PEER_DATA.items():
            mapa[peer] = str(cik).zfill(10)
            self.sec_server.facts[str(cik).zfill(10)] = companyfacts(*empresa(), cik=cik, entity=CANARIO_ENT)
        seed(**mapa)
        self.fmp = fmp
        self.finnhub_status: Optional[int] = None  # fuerza un status en TODA ruta de Finnhub
        self.peers_status: Optional[int] = None  # fuerza un status solo en /stock/peers
        self.peers_list: list[str] = list(PEERS)
        self.cap_overrides: dict[str, Optional[float]] = {}
        self.fred_status: Optional[int] = None

    # ---- utilidades de inspección
    def host_requests(self, *hosts: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host in hosts]

    def paths(self, host: str) -> list[str]:
        return [r.url.path for r in self.requests if r.url.host == host]

    # ---- el despachador
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "finnhub.io":
            return self._finnhub(request)
        if host in ("data.sec.gov", "www.sec.gov"):
            return self.sec_server(request)
        if host == "api.stlouisfed.org":
            if self.fred_status is not None:
                return httpx.Response(self.fred_status)
            return httpx.Response(200, json=fred_body(request.url.params.get("series_id", "")))
        if host == "financialmodelingprep.com":
            return self._fmp(request)
        raise AssertionError(f"host inesperado: {host}")

    def _finnhub(self, request: httpx.Request) -> httpx.Response:
        path, symbol = request.url.path, request.url.params.get("symbol")
        assert request.headers.get("X-Finnhub-Token") == KEY and "token" not in request.url.params
        forced = self.peers_status if path == "/api/v1/stock/peers" and self.peers_status else self.finnhub_status
        if forced is not None:
            return httpx.Response(forced, json={"error": "x"})
        if path == "/api/v1/quote":
            return httpx.Response(200, json=finnhub_quote())
        if path == "/api/v1/stock/profile2":
            if symbol in PEER_DATA:
                cap = self.cap_overrides.get(symbol, PEER_DATA[symbol][1])
                body = finnhub_profile(symbol, cap=cap if cap is not None else 0.0)
                if cap is None:
                    body.pop("marketCapitalization")
                    body.pop("shareOutstanding")
                return httpx.Response(200, json=body)
            return httpx.Response(200, json=finnhub_profile(symbol))
        if path == "/api/v1/stock/metric":
            return httpx.Response(200, json=finnhub_metric())
        if path == "/api/v1/stock/peers":
            return httpx.Response(200, json=self.peers_list)
        raise AssertionError(f"ruta de Finnhub inesperada: {path}")

    def _fmp(self, request: httpx.Request) -> httpx.Response:
        if callable(self.fmp):
            return self.fmp(request)
        if self.fmp == "down":
            if request.url.path in ("/stable/quote", "/stable/profile"):
                return httpx.Response(402, text="Premium Query Parameter: symbol not available")
            return httpx.Response(500, text="boom")
        assert self.fmp == "ok", "modo FMP desconocido"
        path, symbol = request.url.path, request.url.params.get("symbol")
        if path == "/stable/quote":
            return httpx.Response(200, json=[fmp_quote(symbol)])
        if path == "/stable/profile":
            return httpx.Response(200, json=[fmp_profile(symbol)])
        if path == "/stable/key-metrics":
            return httpx.Response(200, json=[{"earningsYield": 0.05}])
        return httpx.Response(500, text="boom")


def build_clients(
    world: World,
    *,
    market: bool = True,
    edgar: bool = True,
    rate_per_minute: int = finnhub_client.RATE_PER_MINUTE,
    sleep: Optional[Callable] = None,
):
    """`Clients` con un cliente httpx por proveedor, todos sobre el mismo despachador."""

    def http() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(world), follow_redirects=False)

    fmp_http, fred_http, treasury_http = http(), http(), httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    finnhub_http, sec_http = http(), http()
    clock = Clock()  # reloj de Finnhub (monotonico + pared sintetico)
    finnhub_client.install_gate(
        finnhub_http, now=clock.now, sleep=sleep or clock.sleep, wall=clock.wall, rate_per_minute=rate_per_minute
    )
    sec_clock = SecClock()
    sec.install_gate(sec_http, now=sec_clock.now, sleep=never_sleep)
    providers = []
    if edgar:
        providers.append(edgar_provider.EdgarProvider(sec_http, UA, today=lambda: HOY))
    providers.append(fmp_provider.FmpProvider(fmp_http, "fmp-key"))
    service = FundamentalsService(providers, now=sec_clock.now, sleep=never_sleep)
    market_service = (
        market_data.MarketDataService(
            finnhub_http=finnhub_http,
            finnhub_api_key=KEY,
            fred_http=fred_http,
            fred_api_key="fred-key-0123456789",
            wall=clock.wall,
        )
        if market
        else None
    )
    clients = query_handler.Clients(
        fmp_http=fmp_http,
        fred_http=fred_http,
        treasury_gov_http=treasury_http,
        fmp_api_key="fmp-key",
        fred_api_key="fred-key-0123456789",
        finnhub_http=finnhub_http,
        finnhub_api_key=KEY,
        sec_edgar_http=sec_http,
        sec_edgar_user_agent=UA,
        fundamentals=service,
        market_data=market_service,
    )
    return clients, clock
