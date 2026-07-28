"""Fixtures compartidas de pytest.

No usa red real: los clientes HTTP se construyen con `httpx.MockTransport`
sobre datos leídos de `tests/fixtures/`. Ningún test depende de
`FMP_API_KEY`/`FRED_API_KEY`/`TELEGRAM_BOT_TOKEN` reales (correr con
`os.environ` limpio o con valores dummy funciona igual).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(*parts: str):
    path = FIXTURES_DIR.joinpath(*parts)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def adobe_fixtures():
    return {
        "quote": load_fixture("adobe", "quote.json"),
        "profile": load_fixture("adobe", "profile.json"),
        "income_statement": load_fixture("adobe", "income_statement.json"),
        "balance_sheet": load_fixture("adobe", "balance_sheet.json"),
        "cash_flow": load_fixture("adobe", "cash_flow.json"),
        "peers_metrics_ttm": load_fixture("adobe", "peers_metrics_ttm.json"),
        "fred_dgs20": load_fixture("adobe", "fred_dgs20.json"),
    }


@pytest.fixture
def in_memory_conn():
    from investbot import db

    conn = db.get_connection(":memory:")
    db.init_db(conn)
    yield conn
    conn.close()
