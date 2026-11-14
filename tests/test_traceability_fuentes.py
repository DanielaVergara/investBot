"""Meta-test de trazabilidad de SDD_fuentes_datos_gratuitas.md (QB-11, 15.9).

Independiente de `tests/test_traceability.py` (que usa IDs de otras specs y NO se
toca): aqui los tests de esta spec se nombran `test_fdg_ca<n>[b]_*` y
`test_fdg_tcs<n>[b]_*`. Cada fase activa exige que exista al menos un test por ID.

`FASES_ACTIVAS` se amplia en cada sub-entrega:
  - F1a-1 (esta): `('F1a',)`, con los IDs del parser que se prueban con datos sinteticos.
  - F1a-2 (fixtures reales): agregar `'F1a-2'` (CA-2..7 y CA-10, que necesitan la captura).
  - F1b-4 (hecho): `'F1b'` activa.
  - F2b (hecho): `'F2'` activa (P-5 resuelta por la usuaria, seccion 16); F3 sigue diferida.
Los tests `test_fdg_ca34_a..d_*` del calculo de deuda viven en F1a-1; el ID `ca34` solo
se exige al activar F1b, y entonces tambien el sufijo `_e_` (linea de aviso al usuario).
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

NAME_RE = re.compile(r"^test_fdg_(ca|tcs)(\d+)(b?)(?=_|$)")

FASES_ACTIVAS = ("F1a", "F1b", "F2")

IDS_POR_FASE: dict[str, tuple[str, ...]] = {
    # F1a-1: parser con datos sinteticos (CA-1, 8, 9, 10b, 11; T-CS-9, 15, 20, 21)
    "F1a": ("ca1", "ca8", "ca9", "ca10b", "ca11", "tcs9", "tcs15", "tcs20", "tcs21"),
    # F1a-2: criterios con fixtures reales (necesitan la captura humana H-4)
    "F1a-2": ("ca2", "ca3", "ca4", "ca5", "ca6", "ca7", "ca10"),
    # F1b: cliente, servicio, cableado y consumidores (CA-12..24, 19b, 33, 34; T-CS-1..8, 10..14, 15b, 16..19, 22..26, 28, 29)
    "F1b": (
        *(f"ca{n}" for n in range(12, 25)),
        "ca19b",
        "ca33",
        "ca34",
        *(f"tcs{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14)),
        "tcs15b",
        *(f"tcs{n}" for n in (16, 17, 18, 19, 22, 23, 24, 25, 26, 28, 29)),
    ),
    # F2 (Finnhub + FRED, opt-in por FINNHUB_API_KEY): CA-25..30 y T-CS-27
    "F2": (*(f"ca{n}" for n in range(25, 31)), "tcs27"),
    # F3 (diferida): CA-31 y CA-32
    "F3": ("ca31", "ca32"),
}

# Sufijos de nombre que una fase exige ademas del ID: (id, fragmento obligatorio en algun nombre)
SUFIJOS_POR_FASE: dict[str, tuple[tuple[str, str], ...]] = {
    "F1b": (("ca34", "_e_"),),
}


@lru_cache(maxsize=None)
def _tree(archivo: str) -> ast.AST:
    return ast.parse((TESTS_DIR / archivo).read_text(encoding="utf-8"))


def _nombres_de_test(tree: ast.AST) -> list[str]:
    nombres = []
    for node in tree.body:
        candidatos = node.body if isinstance(node, ast.ClassDef) else [node]
        nombres += [
            c.name
            for c in candidatos
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c.name.startswith("test")
        ]
    return nombres


def _todos_los_nombres() -> list[str]:
    return [n for path in sorted(TESTS_DIR.glob("*.py")) for n in _nombres_de_test(_tree(path.name))]


def _ids_cubiertos(nombres) -> set[str]:
    ids = set()
    for nombre in nombres:
        m = NAME_RE.match(nombre)
        if m:
            ids.add(f"{m.group(1)}{m.group(2)}{m.group(3)}")
    return ids


def _faltantes(nombres, fases=FASES_ACTIVAS) -> list[str]:
    cubiertos = _ids_cubiertos(nombres)
    faltan = [i for fase in fases for i in IDS_POR_FASE[fase] if i not in cubiertos]
    for fase in fases:
        for id_, fragmento in SUFIJOS_POR_FASE.get(fase, ()):
            if not any(n.startswith(f"test_fdg_{id_}") and fragmento in n for n in nombres):
                faltan.append(f"{id_}{fragmento}")
    return faltan


def test_trazabilidad_fuentes_toda_fase_activa_tiene_un_test_por_cada_id():
    nombres = _todos_los_nombres()
    assert len(nombres) > 500  # control positivo: el recorrido vio la suite real
    assert FASES_ACTIVAS and set(FASES_ACTIVAS) <= set(IDS_POR_FASE)
    assert _faltantes(nombres) == []


def test_trazabilidad_fuentes_el_detector_reporta_lo_que_falta_control_positivo():
    sinteticos = ["test_fdg_ca1_algo", "test_fdg_ca8_x", "test_fdg_tcs9_y", "test_fdg_ca10_z"]
    faltan = _faltantes(sinteticos)
    assert "ca9" in faltan and "ca10b" in faltan and "tcs21" in faltan
    assert "ca1" not in faltan and "ca8" not in faltan and "tcs9" not in faltan
    # `ca10` (F1a-2) no cubre `ca10b` (F1a-1) ni al reves
    assert "ca10" in _faltantes([], fases=("F1a-2",))
    assert "ca10" not in _faltantes(["test_fdg_ca10_z"], fases=("F1a-2",))
    assert "ca10b" in _faltantes(["test_fdg_ca10_z"])


def test_trazabilidad_fuentes_los_ids_de_las_fases_tienen_forma_valida_y_no_se_repiten():
    todos = [i for ids in IDS_POR_FASE.values() for i in ids]
    assert all(re.fullmatch(r"(ca|tcs)\d+b?", i) for i in todos)
    assert len(todos) == len(set(todos))
    assert {"ca1", "ca2", "ca12", "ca25", "ca31"} <= set(todos)  # F1a, F1a-2, F1b, F2 y F3 estan definidas


def test_trazabilidad_fuentes_el_nombre_exacto_no_confunde_ids_ni_otras_specs():
    assert _ids_cubiertos(["test_fdg_ca10b_split", "test_fdg_ca19b_x", "test_fdg_tcs15b_x"]) == {
        "ca10b",
        "ca19b",
        "tcs15b",
    }
    assert _ids_cubiertos(["test_fdg_ca1_x"]) == {"ca1"} and _ids_cubiertos(["test_fdg_ca10_x"]) == {"ca10"}
    assert _ids_cubiertos(["test_ca12_x", "test_cs3_x", "test_fdg_ca3x_y", "test_algo_fdg_ca3_x", "test_fdg_ca"]) == set()
    assert _ids_cubiertos(["test_fdg_ca3", "test_fdg_tcs14_a"]) == {"ca3", "tcs14"}


def test_trazabilidad_fuentes_ca34_exige_el_caso_e_al_activar_f1b():
    base = ["test_fdg_ca34_a_x", "test_fdg_ca34_b_x", "test_fdg_ca34_c_x", "test_fdg_ca34_d_x"]
    assert "ca34" not in _faltantes(base, fases=("F1b",))
    assert "ca34_e_" in _faltantes(base, fases=("F1b",))
    assert "ca34_e_" not in _faltantes([*base, "test_fdg_ca34_e_mensaje"], fases=("F1b",))
