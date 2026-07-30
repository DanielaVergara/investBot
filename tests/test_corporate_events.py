"""Tests de `corporate_events.py` — función pura, sin I/O, sin mocks de red.

Matriz C1-C18 (spec `SDD_peers_dinamicos_y_eventos_corporativos.md`, sección
QA). Toda fecha relativa a la ventana de 180 días se calcula en runtime con
`date.today()` (gap 5.4 de la spec) — nunca un literal hardcodeado, para que
el test no empiece a fallar solo con el paso del tiempo.
"""

from __future__ import annotations

from datetime import date, timedelta

from investbot import corporate_events

CIK10 = "0000796343"


def _fecha(dias_atras: int) -> str:
    return (date.today() - timedelta(days=dias_atras)).isoformat()


def _submissions(forms, dates, accessions, docs, items):
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "primaryDocument": docs,
                "items": items,
            }
        }
    }


# C1-C3 — guardas de entrada
def test_extract_relevant_8k_events_submissions_y_cik_none():
    assert corporate_events.extract_relevant_8k_events(None, None) == []


def test_extract_relevant_8k_events_submissions_none_cik_presente():
    assert corporate_events.extract_relevant_8k_events(None, "0000320193") == []


def test_extract_relevant_8k_events_submissions_presente_cik_none():
    submissions = _submissions(["8-K"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], ["5.02"])
    assert corporate_events.extract_relevant_8k_events(submissions, None) == []


# C4 — submissions sin "filings"/"recent"
def test_extract_relevant_8k_events_sin_filings_key():
    assert corporate_events.extract_relevant_8k_events({}, CIK10) == []


def test_extract_relevant_8k_events_sin_recent_key():
    assert corporate_events.extract_relevant_8k_events({"filings": {}}, CIK10) == []


# C5 — form != "8-K" se descarta
def test_extract_relevant_8k_events_forms_no_8k_se_descartan():
    submissions = _submissions(
        ["10-Q", "10-K", "4"],
        [_fecha(10), _fecha(20), _fecha(30)],
        ["a-1", "a-2", "a-3"],
        ["d1.htm", "d2.htm", "d3.htm"],
        ["5.02", "1.01", "1.02"],
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# C6 — 8-K con items no relevantes se descarta
def test_extract_relevant_8k_events_items_no_relevantes_se_descartan():
    submissions = _submissions(
        ["8-K"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], ["2.02,9.01"]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# C7 — 1 relevante + 1 no relevante -> solo la relevante en items/labels
def test_extract_relevant_8k_events_items_mixto_solo_relevante():
    submissions = _submissions(
        ["8-K"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], ["5.02,9.01"]
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10)
    assert len(result) == 1
    assert result[0].items == ["5.02"]
    assert result[0].labels == ["Cambio de directivos o ejecutivos"]


# C8 — 2 relevantes en el mismo filing -> 1 solo evento, 2 etiquetas
def test_extract_relevant_8k_events_dos_relevantes_mismo_filing():
    submissions = _submissions(
        ["8-K"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], ["1.01,5.02"]
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10)
    assert len(result) == 1
    assert result[0].items == ["1.01", "5.02"]
    assert len(result[0].labels) == 2


# C9 — límite exacto: día 180 se incluye
def test_extract_relevant_8k_events_borde_180_dias_incluido():
    submissions = _submissions(
        ["8-K"], [_fecha(180)], ["0000796343-24-000123"], ["doc.htm"], ["5.02"]
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10, since_days=180)
    assert len(result) == 1


# C10 — borde+1: día 181 se excluye
def test_extract_relevant_8k_events_borde_181_dias_excluido():
    submissions = _submissions(
        ["8-K"], [_fecha(181)], ["0000796343-24-000123"], ["doc.htm"], ["5.02"]
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10, since_days=180)
    assert result == []


# C11 — más de max_events se recorta
def test_extract_relevant_8k_events_recorta_a_max_events():
    n = 7
    submissions = _submissions(
        ["8-K"] * n,
        [_fecha(i) for i in range(1, n + 1)],
        [f"0000796343-24-{i:06d}" for i in range(n)],
        ["doc.htm"] * n,
        ["5.02"] * n,
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10, max_events=5)
    assert len(result) == 5


# C12 — arrays de distinto largo, usa el mínimo común, sin IndexError
def test_extract_relevant_8k_events_arrays_de_distinto_largo():
    submissions = _submissions(
        ["8-K", "8-K", "8-K", "8-K", "8-K"],  # 5
        [_fecha(10), _fecha(20), _fecha(30)],  # 3
        ["a-1", "a-2", "a-3", "a-4"],  # 4
        ["d1.htm", "d2.htm", "d3.htm", "d4.htm", "d5.htm"],  # 5
        ["5.02", "1.01"],  # 2 (el más corto -> n=2)
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10)
    assert len(result) == 2


# C13 — filingDate no parseable se descarta, el resto sigue procesándose
def test_extract_relevant_8k_events_filing_date_no_parseable_se_descarta():
    submissions = _submissions(
        ["8-K", "8-K", "8-K"],
        ["fecha-invalida", None, _fecha(10)],
        ["a-1", "a-2", "a-3"],
        ["d1.htm", "d2.htm", "d3.htm"],
        ["5.02", "5.02", "1.01"],
    )
    result = corporate_events.extract_relevant_8k_events(submissions, CIK10)
    assert len(result) == 1
    assert result[0].items == ["1.01"]


def test_extract_relevant_8k_events_filing_date_tipo_no_string():
    submissions = _submissions(
        ["8-K"], [123], ["a-1"], ["d1.htm"], ["5.02"]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# C14 — _build_filing_url con argumentos vacíos/None
def test_build_filing_url_cik10_vacio():
    assert corporate_events._build_filing_url("", "0000796343-24-000123", "doc.htm") is None


def test_build_filing_url_accession_none():
    assert corporate_events._build_filing_url("0000796343", None, "doc.htm") is None


def test_build_filing_url_primary_document_vacio():
    assert corporate_events._build_filing_url("0000796343", "0000796343-24-000123", "") is None


# C15 — feliz: normalización de cik (ceros) y accession (guiones)
def test_build_filing_url_normaliza_cik_y_accession():
    url = corporate_events._build_filing_url(
        "0000796343", "0000796343-24-000123", "doc.htm"
    )
    assert url == "https://www.sec.gov/Archives/edgar/data/796343/000079634324000123/doc.htm"


# C16 — cik10 no convertible a int
def test_build_filing_url_cik_no_convertible_a_int():
    assert corporate_events._build_filing_url("ABC", "0000796343-24-000123", "doc.htm") is None


# C17 — filing "8-K/A" (enmienda) se descarta, comparación exacta de string
def test_extract_relevant_8k_events_8ka_enmienda_se_descarta():
    submissions = _submissions(
        ["8-K/A"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], ["5.02"]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# C18 — 0 eventos en la ventana, caso feliz "sin eventos"
def test_extract_relevant_8k_events_cero_eventos_sin_excepcion():
    submissions = _submissions(
        ["10-K", "10-Q"], [_fecha(10), _fecha(20)], ["a-1", "a-2"], ["d1.htm", "d2.htm"], ["", ""]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# Cobertura de rama adicional: _build_filing_url devuelve None dentro del
# loop principal -> esa entrada se descarta (no genera un link roto).
def test_extract_relevant_8k_events_url_none_descarta_la_entrada():
    submissions = _submissions(
        ["8-K"], [_fecha(10)], [""], ["doc.htm"], ["5.02"]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


# Cobertura de rama: items_raw[i] no es un string (ej. None) -> codigos_raw="",
# sin match, se descarta sin excepción.
def test_extract_relevant_8k_events_items_no_string_se_descarta():
    submissions = _submissions(
        ["8-K"], [_fecha(10)], ["0000796343-24-000123"], ["doc.htm"], [None]
    )
    assert corporate_events.extract_relevant_8k_events(submissions, CIK10) == []


def test_relevant_8k_items_tiene_exactamente_los_5_items_acordados():
    assert set(corporate_events.RELEVANT_8K_ITEMS.keys()) == {
        "1.01", "1.02", "1.03", "5.02", "8.01",
    }
