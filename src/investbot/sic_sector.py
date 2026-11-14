"""Tabla SIC -> sector (F2a, CA-27 de `SDD_fuentes_datos_gratuitas.md`).

El código SIC de 4 dígitos viene de `submissions` de la SEC (campo `sic`, dato de
red no confiable). La salida es SIEMPRE un nombre de `_SECTOR_ALLOWLIST`
(`ai_explain`) o `None`; nunca texto de la red. La tabla es cerrada: grupo mayor
(2 dígitos) como base y rangos de 4 dígitos que la refinan. Un `None` en `_RANGES` fuerza N/D
para un código ambiguo. Un SIC sin entrada,
mal formado o fuera de rango devuelve `None` (el bot lo muestra como N/D).

Es una aproximación declarada, no una verdad contable: SIC es una clasificación
de 1937 y varios códigos (p. ej. 7389) agrupan negocios distintos. Se prefiere
`None` ante la duda razonable (grupos públicos 91-97, 99 y 89 sin mapear).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from investbot.ai_explain import _SECTOR_ALLOWLIST

BASIC = "Basic Materials"
COMM = "Communication Services"
CYCLICAL = "Consumer Cyclical"
DEFENSIVE = "Consumer Defensive"
ENERGY = "Energy"
FINANCIAL = "Financial Services"
HEALTH = "Healthcare"
INDUSTRIAL = "Industrials"
REAL_ESTATE = "Real Estate"
TECH = "Technology"
UTILITIES = "Utilities"

# Grupo mayor SIC (2 dígitos) -> sector. Los grupos ausentes devuelven `None`.
_MAJOR_GROUP: dict[int, str] = {
    1: DEFENSIVE, 2: DEFENSIVE, 7: DEFENSIVE, 8: BASIC, 9: DEFENSIVE,  # agricultura y pesca
    10: BASIC, 12: ENERGY, 13: ENERGY, 14: BASIC,  # minería
    15: CYCLICAL, 16: INDUSTRIAL, 17: INDUSTRIAL,  # construcción
    20: DEFENSIVE, 21: DEFENSIVE,  # alimentos y tabaco
    22: CYCLICAL, 23: CYCLICAL,  # textil y confección
    24: BASIC, 25: CYCLICAL, 26: BASIC, 27: COMM,  # madera, muebles, papel, imprenta
    28: BASIC, 29: ENERGY, 30: CYCLICAL, 31: CYCLICAL,  # químicos, refinación, plástico, cuero
    32: BASIC, 33: BASIC, 34: INDUSTRIAL, 35: INDUSTRIAL,  # minerales, metales, maquinaria
    36: INDUSTRIAL, 37: INDUSTRIAL, 38: INDUSTRIAL, 39: CYCLICAL,  # eléctrico, transporte, instrumentos
    40: INDUSTRIAL, 41: INDUSTRIAL, 42: INDUSTRIAL, 44: INDUSTRIAL, 45: INDUSTRIAL,
    46: ENERGY, 47: INDUSTRIAL, 48: COMM, 49: UTILITIES,  # transporte, oleoductos, comunicaciones
    50: INDUSTRIAL, 51: DEFENSIVE,  # mayoristas
    52: CYCLICAL, 53: CYCLICAL, 54: DEFENSIVE, 55: CYCLICAL, 56: CYCLICAL, 57: CYCLICAL,
    58: CYCLICAL, 59: CYCLICAL,  # minoristas
    60: FINANCIAL, 61: FINANCIAL, 62: FINANCIAL, 63: FINANCIAL, 64: FINANCIAL,
    65: REAL_ESTATE, 67: FINANCIAL,  # finanzas e inmobiliario
    70: CYCLICAL, 72: CYCLICAL, 73: INDUSTRIAL, 75: CYCLICAL, 76: INDUSTRIAL,
    78: COMM, 79: CYCLICAL, 80: HEALTH, 81: INDUSTRIAL, 82: DEFENSIVE, 83: DEFENSIVE,
    84: CYCLICAL, 86: INDUSTRIAL, 87: INDUSTRIAL,  # servicios
}

# Refinamientos de 4 dígitos: (desde, hasta, sector), inclusivos y SIN solaparse.
_RANGES: tuple[tuple[int, int, Optional[str]], ...] = (
    (1540, 1549, INDUSTRIAL),  # edificación no residencial
    (2400, 2400, BASIC),
    (2451, 2452, CYCLICAL),  # casas móviles / prefabricadas
    (2650, 2679, CYCLICAL),  # envases de cartón y papel convertido
    (2833, 2836, HEALTH),  # farmacéuticos, diagnósticos, biológicos
    (2840, 2844, DEFENSIVE),  # jabones, cosméticos, cuidado personal
    (3559, 3559, TECH),  # equipo de semiconductores
    (3570, 3579, TECH),  # computadores y periféricos
    (3630, 3639, CYCLICAL),  # electrodomésticos
    (3651, 3651, CYCLICAL),  # audio y video de consumo
    (3660, 3679, TECH),  # comunicaciones y componentes electrónicos, semiconductores
    (3695, 3695, TECH),
    (3711, 3711, CYCLICAL), (3714, 3714, CYCLICAL), (3716, 3716, CYCLICAL),  # automotriz
    (3751, 3751, CYCLICAL),  # motocicletas
    (3821, 3821, HEALTH), (3823, 3823, TECH), (3825, 3825, TECH), (3826, 3826, HEALTH),
    (3827, 3827, TECH),  # instrumentos de laboratorio, medición y óptica
    (3841, 3845, HEALTH), (3851, 3851, HEALTH),  # dispositivos médicos y oftálmicos
    (3861, 3861, TECH),  # equipo fotográfico
    (3873, 3873, CYCLICAL),  # relojes
    (4700, 4700, CYCLICAL),  # agencias de viajes
    (4950, 4959, INDUSTRIAL),  # saneamiento y residuos
    (5045, 5045, TECH), (5047, 5047, HEALTH), (5065, 5065, TECH),  # mayoristas especializados
    (5122, 5122, HEALTH), (5171, 5172, ENERGY),
    (5331, 5331, DEFENSIVE), (5399, 5399, DEFENSIVE),  # tiendas de variedades y clubes
    (5912, 5912, HEALTH),  # farmacias
    (6770, 6770, FINANCIAL),  # sociedades de propósito especial (cheque en blanco)
    (6792, 6792, ENERGY), (6795, 6795, BASIC),  # regalías
    (6798, 6798, REAL_ESTATE),  # REIT
    (7310, 7319, COMM),  # publicidad
    (7389, 7389, None),  # servicios comerciales NEC: agrupa negocios dispares (Visa, Uber, MELI)
    (7370, 7379, TECH),  # software, procesamiento de datos y servicios informáticos
    (8731, 8734, HEALTH),  # investigación biológica y laboratorios de pruebas
)

_SIC_RE = re.compile(r"[0-9]{4}", re.ASCII)
_SIC_MAX = 9999


def _normalize(raw: Any) -> Optional[int]:
    """`sic` como `int` de 1 a 9999. Acepta `int` (no `bool`) o 4 dígitos ASCII
    (`"7372"`, `"0100"`); todo lo demás -> `None`."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if 1 <= raw <= _SIC_MAX else None
    if isinstance(raw, str) and _SIC_RE.fullmatch(raw):
        return int(raw) or None
    return None


def sector_for_sic(raw: Any) -> Optional[str]:
    """Sector (nombre de `_SECTOR_ALLOWLIST`) para un código SIC, o `None`."""
    sic = _normalize(raw)
    if sic is None:
        return None
    sector = _MAJOR_GROUP.get(sic // 100)
    for low, high, refined in _RANGES:
        if low <= sic <= high:
            sector = refined
            break
    return sector if sector in _SECTOR_ALLOWLIST else None
