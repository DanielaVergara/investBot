"""Constructores sinteticos de `companyfacts` para los tests del parser EDGAR.

SDD_fuentes_datos_gratuitas.md 14.7: `fact(...)` y `companyfacts(...)`. Datos
inventados (nada de red ni de archivos reales). `fact` devuelve una tupla
`(namespace, tag, unit, hecho)` que `companyfacts` agrupa en el formato real:
`{"cik", "entityName", "facts": {namespace: {tag: {"units": {unit: [hechos]}}}}}`.
"""

from __future__ import annotations

from typing import Optional


def _accn_por_defecto(filed: str) -> str:
    """accn valido (10-2-6) y determinista a partir de la fecha de presentacion."""
    return f"0000000001-{filed[2:4]}-{filed[5:7]}{filed[8:10]}00"


def fact(
    tag: str,
    unit: str,
    start: Optional[str],
    end: str,
    val,
    filed: str,
    form: str = "10-Q",
    accn: Optional[str] = None,
    *,
    namespace: str = "us-gaap",
    fy: Optional[int] = None,
    fp: Optional[str] = None,
) -> tuple:
    """Un hecho XBRL. `start=None` => instante (sin clave `start`)."""
    hecho = {
        "end": end,
        "val": val,
        "accn": _accn_por_defecto(filed) if accn is None else accn,
        "fy": int(end[:4]) if fy is None else fy,
        "fp": "FY" if fp is None else fp,
        "form": form,
        "filed": filed,
    }
    if start is not None:
        hecho["start"] = start
    return (namespace, tag, unit, hecho)


def companyfacts(*facts: tuple, cik=1234567, entity: str = "Sintetica SA") -> dict:
    """Arma el documento `companyfacts`. `cik=None` omite la clave."""
    espacios: dict = {"dei": {}}
    for namespace, tag, unit, hecho in facts:
        unidades = espacios.setdefault(namespace, {}).setdefault(tag, {"units": {}})["units"]
        unidades.setdefault(unit, []).append(hecho)
    doc: dict = {"entityName": entity, "facts": espacios}
    if cik is not None:
        doc["cik"] = cik
    return doc
