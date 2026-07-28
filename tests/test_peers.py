"""Tests de `peers.py` — set fijo de peers + promedio de PER (Decisión #9)."""

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
    quotes = {"MSFT": {"pe": 30.0}, "ORCL": {"pe": 34.0}, "CRM": {"pe": 32.0}}

    async def fake_get_quote(ticker: str):
        return quotes.get(ticker)

    result = await peers.get_peer_pe_average(
        get_quote_fn=fake_get_quote, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(32.0)
    assert set(result.peers_usados) == {"MSFT", "ORCL", "CRM"}


async def test_get_peer_pe_average_excluye_propio_ticker():
    quotes = {"MSFT": {"pe": 30.0}, "CRM": {"pe": 34.0}}

    async def fake_get_quote(ticker: str):
        return quotes.get(ticker)

    result = await peers.get_peer_pe_average(
        get_quote_fn=fake_get_quote, sector="Technology", own_ticker="ORCL"
    )
    assert "ORCL" not in result.peers_usados
    assert result.per_promedio == pytest.approx(32.0)


async def test_get_peer_pe_average_peer_sin_pe_se_descarta():
    quotes = {"MSFT": {"pe": 30.0}, "ORCL": None, "CRM": {"pe": None}}

    async def fake_get_quote(ticker: str):
        return quotes.get(ticker)

    result = await peers.get_peer_pe_average(
        get_quote_fn=fake_get_quote, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio == pytest.approx(30.0)
    assert result.peers_usados == ["MSFT"]


async def test_get_peer_pe_average_ningun_peer_disponible():
    async def fake_get_quote(ticker: str):
        return None

    result = await peers.get_peer_pe_average(
        get_quote_fn=fake_get_quote, sector="Technology", own_ticker="AAPL"
    )
    assert result.per_promedio is None
    assert result.peers_usados == []
