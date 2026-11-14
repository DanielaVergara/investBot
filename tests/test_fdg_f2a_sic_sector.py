"""F2a de SDD_fuentes_datos_gratuitas.md, CA-27: tabla SIC -> sector cerrada.

Los SIC de los casos reales son los que la SEC asigna a esos emisores (dato público
de clasificación; se mapean a sectores de `_SECTOR_ALLOWLIST`). Es una aproximación:
la tabla prefiere `None` ante un código ambiguo.
"""

from __future__ import annotations

import pytest

from investbot import ai_explain, sic_sector

# (SIC, emisor de referencia, sector esperado). >= 10 SIC reales de sectores distintos.
SIC_REALES = [
    ("3571", "Apple (computadores)", "Technology"),
    ("7372", "Microsoft / Adobe (software)", "Technology"),
    ("3674", "Nvidia (semiconductores)", "Technology"),
    ("7370", "Alphabet / Meta (servicios informáticos)", "Technology"),
    ("2086", "Coca-Cola (bebidas)", "Consumer Defensive"),
    ("5331", "Walmart (tiendas de variedades)", "Consumer Defensive"),
    ("2911", "Exxon (refinación)", "Energy"),
    ("1311", "petróleo y gas crudo", "Energy"),
    ("6022", "JPMorgan (banco estatal)", "Financial Services"),
    ("6798", "REIT", "Real Estate"),
    ("2834", "Pfizer / Johnson & Johnson (farmacéuticos)", "Healthcare"),
    ("3841", "instrumentos quirúrgicos", "Healthcare"),
    ("5961", "Amazon (ventas por catálogo)", "Consumer Cyclical"),
    ("3711", "Tesla / Ford (vehículos)", "Consumer Cyclical"),
    ("5211", "Home Depot (materiales de construcción)", "Consumer Cyclical"),
    ("4911", "Duke Energy (electricidad)", "Utilities"),
    ("4813", "AT&T (telefonía)", "Communication Services"),
    ("7841", "Netflix (video)", "Communication Services"),
    ("3721", "Boeing (aeronaves)", "Industrials"),
    ("3531", "Caterpillar (maquinaria de construcción)", "Industrials"),
    ("1040", "minería de oro", "Basic Materials"),
    ("3310", "acero", "Basic Materials"),
]


@pytest.mark.parametrize(("sic", "emisor", "esperado"), SIC_REALES)
def test_fdg_ca27_sic_reales_mapean_al_sector_esperado(sic, emisor, esperado):
    assert sic_sector.sector_for_sic(sic) == esperado
    assert sic_sector.sector_for_sic(int(sic)) == esperado  # int y str dan lo mismo


def test_fdg_ca27_los_ejemplos_reales_cubren_los_once_sectores():
    assert {s for _, _, s in SIC_REALES} == set(ai_explain._SECTOR_ALLOWLIST) and len(SIC_REALES) >= 10


def test_fdg_ca27_toda_salida_es_un_sector_de_la_allowlist_o_none_para_los_10000_codigos():
    vistos = set()
    for n in range(0, 10_000):
        for entrada in (n, f"{n:04d}"):
            out = sic_sector.sector_for_sic(entrada)
            assert out is None or out in ai_explain._SECTOR_ALLOWLIST
            vistos.add(out)
    assert vistos - {None} == set(ai_explain._SECTOR_ALLOWLIST)  # y los 11 sectores se alcanzan


def test_fdg_ca27_la_allowlist_es_la_de_ai_explain_y_no_una_copia():
    assert sic_sector._SECTOR_ALLOWLIST is ai_explain._SECTOR_ALLOWLIST


def test_fdg_ca27_la_tabla_esta_bien_formada_rangos_sin_solapes_y_con_sectores_validos():
    for sector in sic_sector._MAJOR_GROUP.values():
        assert sector in ai_explain._SECTOR_ALLOWLIST
    previo = 0
    for low, high, sector in sorted(sic_sector._RANGES):
        assert 100 <= low <= high <= 9999 and low > previo, (low, high)
        assert sector is None or sector in ai_explain._SECTOR_ALLOWLIST
        previo = high
    assert all(0 < g < 100 for g in sic_sector._MAJOR_GROUP)


def test_fdg_ca27_codigo_ambiguo_o_no_mapeado_es_none():
    assert sic_sector.sector_for_sic("7389") is None  # servicios comerciales NEC (Visa, Uber, MELI)
    for sic in ("9100", "9995", "8900", "0000", "9999", "8800", "6600"):
        assert sic_sector.sector_for_sic(sic) is None


@pytest.mark.parametrize(
    "hostil",
    [
        None, True, False, "", " ", "7372\n", " 7372", "7372 ", "73720", "737", "abc", "7372.0", "-7372",
        "٧٣٧٢", "７３７２", "0x1c", "7372; DROP", 0, -1, 10_000, 7372.0, 7372.5, [], {}, b"7372", ("7372",),
    ],
)
def test_fdg_ca27_entradas_hostiles_o_mal_formadas_devuelven_none(hostil):
    assert sic_sector.sector_for_sic(hostil) is None


def test_fdg_ca27_una_entrada_corrupta_en_la_tabla_nunca_sale_de_la_allowlist(monkeypatch):
    """Defensa en profundidad: aunque alguien ensucie la tabla, la salida sigue
    siendo allowlist o None (mata la mutación "devolver el sector sin filtrar")."""
    monkeypatch.setitem(sic_sector._MAJOR_GROUP, 73, "Sector Inventado\n**x**")
    assert sic_sector.sector_for_sic("7399") is None  # no está en ningún rango: usa el grupo mayor
    monkeypatch.setattr(sic_sector, "_RANGES", ((7370, 7379, "Otro Sector"),))
    assert sic_sector.sector_for_sic("7372") is None
    monkeypatch.undo()
    assert sic_sector.sector_for_sic("7372") == "Technology"


def test_fdg_ca27_el_rango_fino_gana_al_grupo_mayor():
    assert sic_sector.sector_for_sic("3500") == "Industrials"  # grupo 35
    assert sic_sector.sector_for_sic("3571") == "Technology"  # rango 3570-3579
    assert sic_sector.sector_for_sic("2000") == "Consumer Defensive"
    assert sic_sector.sector_for_sic("2834") == "Healthcare"  # dentro del grupo 28 (Basic Materials)
    assert sic_sector.sector_for_sic("2800") == "Basic Materials"
    assert sic_sector.sector_for_sic("1540") == "Industrials" and sic_sector.sector_for_sic("1520") == "Consumer Cyclical"
