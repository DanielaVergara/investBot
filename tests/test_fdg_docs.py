"""Documentación del cierre de SDD_fuentes_datos_gratuitas: variables, rollback, UA."""

from __future__ import annotations

import re
from pathlib import Path

from investbot import bot

REPO = Path(__file__).resolve().parent.parent
ENV = (REPO / ".env.example").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
SETUP = (REPO / "contexto" / "referencia" / "SETUP_TELEGRAM_BOT.md").read_text(encoding="utf-8")


def _comentario_previo(texto: str, variable: str) -> str:
    lineas = texto.splitlines()
    i = lineas.index(f"{variable}=")
    bloque = []
    while i > 0 and lineas[i - 1].startswith("#"):
        i -= 1
        bloque.append(lineas[i])
    return "\n".join(reversed(bloque))


def test_fdg_docs_env_example_describe_las_variables_nuevas():
    finnhub = _comentario_previo(ENV, "FINNHUB_API_KEY")
    assert "X-Finnhub-Token" in finnhub and "52 semanas" in finnhub and "opt-in" in finnhub
    sec = _comentario_previo(ENV, "SEC_EDGAR_USER_AGENT")
    assert "estados financieros" in sec and "8-K" in sec and "deshabilitada" in sec
    fred = _comentario_previo(ENV, "FRED_API_KEY")
    assert "VIX" in fred and "N/D" in fred and "20 años" in fred


def test_fdg_docs_env_example_mantiene_las_lineas_secretas_vacias():
    for linea in ENV.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", linea)
        if m and any(k in m.group(1) for k in ("KEY", "TOKEN", "USER_AGENT", "CHAT_ID")):
            assert m.group(2) == "", m.group(1)


def test_fdg_docs_ejemplo_de_ua_pasa_y_el_placeholder_sigue_rechazado():
    m = re.search(r'p\. ej\. "([^"]+)"', ENV)
    assert m and bot._valid_sec_user_agent(m.group(1))
    assert not bot._valid_sec_user_agent("InvestBot contacto@ejemplo.com")


def test_fdg_docs_readme_documenta_fuentes_rollback_y_script():
    for frag in (
        "investbot-bot:prev-8b74b58",
        "up -d --no-build --force-recreate",
        "SEC_EDGAR_USER_AGENT",
        "FINNHUB_API_KEY",
        "FRED_API_KEY",
        "scripts/compare_edgar_fmp.py",
        "COMPARE_CONFIRM=yes",
        "NO activar EDGAR",
        "NVDA, JPM, AAPL, MELI, CLPT, SPY",
        "fundamentals ticker=<T> source=<sec_edgar|fmp> fallback=<códigos o ->",
        "docker stats",
        "256m",
        "SIC",
        "N/D",
    ):
        assert frag in README, frag
    assert "no-build --force-recreate" in SETUP and "README.md" in SETUP


def test_fdg_docs_readme_ya_no_dice_que_el_vix_viene_de_fmp():
    assert "symbol=^VIX" not in README
    assert "ya no viene de FMP" in README
    assert "FRED" in README
