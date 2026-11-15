"""Meta-test de trazabilidad de SDD_motivos_nd_y_empresas_en_perdidas.md (15.2 punto 7).

Independiente de `tests/test_traceability.py` y de `tests/test_traceability_fuentes.py`
(que usan otros IDs y NO se tocan): los tests de esta spec se nombran
`test_nd_ca<n>_*` y `test_nd_cs<n>_*`. Exige al menos un test por cada ID AUTO.

CS-ND-2, CS-ND-6 y CS-ND-7 son manuales (diff, `git status` y suite completa) y se
verifican en el Momento 2 con evidencia, por eso no figuran en `CS_AUTO`.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

NAME_RE = re.compile(r"^test_nd_(ca|cs)(\d+)(?=_|$)")

CA_AUTO = tuple(range(1, 17))
CS_AUTO = (1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)


@lru_cache(maxsize=None)
def _ids_encontrados() -> dict[str, dict[int, list[str]]]:
    encontrados: dict[str, dict[int, list[str]]] = {"ca": {}, "cs": {}}
    for archivo in sorted(TESTS_DIR.glob("test_*.py")):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m = NAME_RE.match(nodo.name)
                if m:
                    encontrados[m.group(1)].setdefault(int(m.group(2)), []).append(
                        f"{archivo.name}::{nodo.name}"
                    )
    return encontrados


def test_traceability_nd_todo_ca_auto_tiene_al_menos_un_test():
    faltan = sorted(set(CA_AUTO) - set(_ids_encontrados()["ca"]))
    assert not faltan, f"CA-ND sin test: {faltan}"


def test_traceability_nd_todo_cs_auto_tiene_al_menos_un_test():
    faltan = sorted(set(CS_AUTO) - set(_ids_encontrados()["cs"]))
    assert not faltan, f"CS-ND sin test: {faltan}"


def test_traceability_nd_no_hay_ids_fuera_de_los_conjuntos_de_la_spec():
    encontrados = _ids_encontrados()
    extras_ca = sorted(set(encontrados["ca"]) - set(CA_AUTO))
    extras_cs = sorted(set(encontrados["cs"]) - set(CS_AUTO))
    assert not extras_ca and not extras_cs, (extras_ca, extras_cs)


def test_traceability_nd_el_regex_no_confunde_ids_con_prefijo_comun():
    # control positivo: `ca1` no debe contar como `ca10`..`ca16` ni viceversa
    assert NAME_RE.match("test_nd_ca1_algo").group(2) == "1"
    assert NAME_RE.match("test_nd_ca10_algo").group(2) == "10"
    assert NAME_RE.match("test_nd_ca12").group(2) == "12"
    assert NAME_RE.match("test_nd_cax_algo") is None
    assert NAME_RE.match("test_fdg_ca1_algo") is None
    assert NAME_RE.match("test_ca1_algo") is None
