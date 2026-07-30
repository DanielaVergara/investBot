"""Extracción de eventos corporativos relevantes desde un historial de
filings de SEC EDGAR ya resuelto (Feature 2). Función pura, sin I/O — mismo
estándar que market_context.py: recibe datos ya obtenidos por
sec_edgar_client.py como parámetros.

NO resume el contenido del filing (decisión explícita, ver spec) — solo
extrae fecha + Item + URL pública del documento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# Items de un 8-K relevantes al pedido de Daniela (cambios de gerencia,
# contratos importantes, litigios) — clasificación estándar de la SEC
# (Regulation S-K / Form 8-K, estructura pública y estable desde la reforma
# de 2004), confirmada por investigación, no improvisada:
RELEVANT_8K_ITEMS: dict[str, str] = {
    "1.01": "Nuevo contrato importante",
    "1.02": "Terminación de un contrato importante",
    "1.03": "Posible bancarrota o reorganización",
    "5.02": "Cambio de directivos o ejecutivos",
    "8.01": "Otro evento relevante (puede incluir litigios/demandas — la SEC "
            "no exige reportar litigios en un Item propio salvo casos "
            "extremos, así que a veces caen acá y a veces no se reportan "
            "vía 8-K en absoluto)",
}
# NO se agrega ningún Item fuera de estos 5 sin acuerdo explícito de
# Daniela (ej. 2.01 "Acquisition/Disposition of Assets" queda afuera —
# no lo pidió, sería agregar alcance no acordado).


@dataclass
class CorporateEvent:
    filing_date: str  # "YYYY-MM-DD", tal como lo devuelve SEC EDGAR
    items: list[str]
    labels: list[str]
    filing_url: str


def _build_filing_url(cik10: str, accession_number: str, primary_document: str) -> Optional[str]:
    if not cik10 or not accession_number or not primary_document:
        return None
    try:
        cik_sin_ceros = str(int(cik10))
    except (TypeError, ValueError):
        return None
    accession_sin_guiones = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_sin_ceros}/"
        f"{accession_sin_guiones}/{primary_document}"
    )


def extract_relevant_8k_events(
    submissions: Optional[dict],
    cik10: Optional[str],
    *,
    max_events: int = 5,     # Pregunta 2.1, RESUELTA por Daniela: 5
    since_days: int = 180,   # Pregunta 2.1, RESUELTA por Daniela: 180 días
) -> list[CorporateEvent]:
    """Nunca lanza excepción — cualquier forma inesperada del JSON de SEC
    EDGAR (campo ausente, arrays de distinto largo, fecha no parseable)
    hace que esa entrada puntual se descarte, nunca que la función falle
    (mismo criterio B1-B4 del resto del proyecto: guardas de tipo, nunca
    asumir la forma exacta del payload sin verificarlo con curl real —
    mismo patrón que Resolución (a) de SDD_contenido_financiero_explicado).
    """
    if not submissions or not cik10:
        return []
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    items_raw = recent.get("items") or []

    n = min(len(forms), len(dates), len(accessions), len(docs), len(items_raw))
    cutoff = date.today() - timedelta(days=since_days)
    events: list[CorporateEvent] = []

    for i in range(n):
        if forms[i] != "8-K":
            continue
        try:
            filing_date = date.fromisoformat(dates[i])
        except (ValueError, TypeError):
            continue
        if filing_date < cutoff:
            continue

        codigos_raw = items_raw[i] if isinstance(items_raw[i], str) else ""
        codigos = [c.strip() for c in codigos_raw.split(",") if c.strip()]
        matched = [c for c in codigos if c in RELEVANT_8K_ITEMS]
        if not matched:
            continue

        url = _build_filing_url(cik10, accessions[i], docs[i])
        if url is None:
            continue

        events.append(
            CorporateEvent(
                filing_date=dates[i],
                items=matched,
                labels=[RELEVANT_8K_ITEMS[c] for c in matched],
                filing_url=url,
            )
        )
        if len(events) >= max_events:
            break

    return events
