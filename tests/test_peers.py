"""Tests de `peers.py` — set fijo de peers + promedio de PER (Decisión #9).

El PER de cada peer se deriva de `earningsYield` (`/key-metrics` anual) como
`1 / earningsYield` — la API stable de FMP ya no expone un campo `pe`
directo en `/quote`, y `/key-metrics-ttm` es de pago en el plan gratuito
actual (ver `peers.get_peer_pe_average`).
"""

from __future__ import annotations

import pytest

from investbot import peers


def test_get_peers_for_sector_excluye_propio_ticker():
    """El ticker consultado está hardcodeado como peer de su propio sector
    -> debe excluirse de su propio promedio."""
    resultado = peers.get_peers_for_sector("Technology", "ORCL")
    assert "ORCL" not in resultado
    assert set(resultado) == {"MSFT", "CRM"}


def test_get_peers_for_sector_sector_desconocido():
    assert peers.get_peers_for_sector("Sector Inexistente", "XXX") == []


async def test_get_peer_pe_average_caso_simple():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": 1 / 34.0},
        "CRM": {"earningsYield": 1 / 32.0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(32.0)
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}


async def test_get_peer_pe_average_excluye_propio_ticker():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "CRM": {"earningsYield": 1 / 34.0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="ORCL"
    )
    assert "ORCL" not in result.peers_usados
    assert result.per_promedio == pytest.approx(32.0)


async def test_get_peer_pe_average_peer_sin_metrics_se_descarta():
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": None,
        "CRM": {"earningsYield": None},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]


async def test_get_peer_pe_average_earnings_yield_no_positivo_se_descarta():
    """Peer con utilidades negativas/nulas (earningsYield <= 0) se excluye
    en vez de invertirse a un PER negativo sin sentido."""
    metrics = {
        "MSFT": {"earningsYield": 1 / 30.0},
        "ORCL": {"earningsYield": -0.02},
        "CRM": {"earningsYield": 0},
    }

    async def fake_get_metrics(ticker: str):
        return metrics.get(ticker)

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]


async def test_get_peer_pe_average_ningun_peer_disponible():
    async def fake_get_metrics(ticker: str):
        return None

    result = await peers.get_peer_pe_average(
        get_peer_metrics_fn=fake_get_metrics, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio is None
    assert result.peers_usados == []
