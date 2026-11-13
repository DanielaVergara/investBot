"""Meta-test de trazabilidad (SDD_migracion_gemini.md: CA-64, CQ-6, A4).

Garantiza dos cosas, sin red y sin ejecutar nada:

1. Todo criterio AUTO de la spec tiene al menos un test cuyo nombre empieza con
   `test_ca<n>_` / `test_cs<n>_` (regex de CA-64) y `test_ca_s7_` para CA-S7.
   La lista AUTO excluye CA-7, CA-22 y CA-49 (reemplazados por CS-1, CS-3 y
   CS-16). Si falta el test de un ID no se acepta un test vacio: hay que
   escribir el test real.
2. Los CA que reusan tests existentes (25, 33, 34, 36, 42) siguen cubiertos: los
   nombres del manifiesto de tests conservados (tomados de HEAD 957f11d) existen
   y su cantidad de aserciones (`assert`, `pytest.raises`, `.assert_*`) no
   bajo. Si alguien borra o vacia uno de esos tests, estos meta-tests fallan.

No ejecuta los GREP de CA-50 a CA-52 (se auto-matchearian): no contiene esos
literales.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

NAME_RE = re.compile(r"^test_(ca|cs)(\d+)(?=_|$)")
S7_PREFIX = "test_ca_s7_"

CA_AUTO = (*range(1, 7), *range(8, 22), *range(23, 49), *range(65, 70))
# SDD_redaccion_gemini_iter2.md 17.4: F1 agrega CA-70..CA-85 (len(CA_AUTO) == 67).
CA_AUTO = (*CA_AUTO, *range(70, 86))
CS_AUTO = (1, 2, 3, 4, 5, 6, 13, 16, 17, 18, 19, 21, 22)
# SDD_redaccion_gemini_iter2.md 17.4: F0 agrega CS-25 (b) y CS-28 (len(CS_AUTO) == 15).
CS_AUTO = (*CS_AUTO, 25, 28)
# F1 agrega CS-23, 24, 26, 29, 30, 31, 32, 33, 34, 35, 36 (len(CS_AUTO) == 26).
CS_AUTO = (*CS_AUTO, 23, 24, 26, 29, 30, 31, 32, 33, 34, 35, 36)
# F2 agrega CA-86..CA-88 y CS-27 (barras Unicode).
CA_AUTO = (*CA_AUTO, 86, 87, 88)
CS_AUTO = (*CS_AUTO, 27)
CA_REEMPLAZADOS = (7, 22, 49)  # fuera de la lista: reemplazados por CS-1, CS-3, CS-16


def _assertion_count(node: ast.AST) -> int:
    """Aserciones de un test: `assert`, `pytest.raises` y llamadas `.assert_*`."""
    total = 0
    for n in ast.walk(node):
        if isinstance(n, ast.Assert):
            total += 1
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and ast.unparse(expr.func).endswith("raises"):
                    total += 1
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("assert_")
        ):
            total += 1
    return total


def _test_nodes(tree: ast.AST) -> list:
    """Tests colectables: funciones de nivel modulo y metodos de clases de nivel modulo."""
    found = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found.extend(node.body)
        else:
            found.append(node)
    return [
        node
        for node in found
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
    ]


@lru_cache(maxsize=None)
def _tree(archivo: str) -> ast.AST:
    return ast.parse((TESTS_DIR / archivo).read_text(encoding="utf-8"))


def _functions_from_source(source: str) -> dict[str, int]:
    """nombre de test -> cantidad de aserciones (incluye metodos de clases)."""
    return {node.name: _assertion_count(node) for node in _test_nodes(ast.parse(source))}


def _all_names() -> list[str]:
    """Nombres de test de todos los `tests/*.py` (sin contar aserciones: barato)."""
    return [
        node.name
        for path in sorted(TESTS_DIR.glob("*.py"))
        for node in _test_nodes(_tree(path.name))
    ]


@lru_cache(maxsize=None)
def _tests_of(archivo: str) -> dict[str, int]:
    """{test: aserciones} de un archivo de `tests/` (cacheado por archivo)."""
    return {node.name: _assertion_count(node) for node in _test_nodes(_tree(archivo))}


def _ids_cubiertos(names) -> set:
    ids = set()
    for name in names:
        m = NAME_RE.match(name)
        if m:
            ids.add((m.group(1), int(m.group(2))))
        if name.startswith(S7_PREFIX):
            ids.add(("ca", "s7"))
    return ids


def _faltantes(names) -> list[str]:
    cubiertos = _ids_cubiertos(names)
    faltan = [f"CA-{n}" for n in CA_AUTO if ("ca", n) not in cubiertos]
    faltan += [f"CS-{n}" for n in CS_AUTO if ("cs", n) not in cubiertos]
    if ("ca", "s7") not in cubiertos:
        faltan.append("CA-S7")
    return faltan


def _verificar_manifiesto(archivo: str, manifiesto: dict[str, int]) -> list[str]:
    actuales = _tests_of(archivo)
    problemas = []
    for nombre, minimo in manifiesto.items():
        if nombre not in actuales:
            problemas.append(f"falta {archivo}::{nombre}")
        elif actuales[nombre] < minimo:
            problemas.append(f"{archivo}::{nombre} bajo de {minimo} a {actuales[nombre]} aserciones")
    total_min = sum(manifiesto.values())
    total_hoy = sum(actuales.get(n, 0) for n in manifiesto)
    if total_hoy < total_min:
        problemas.append(f"{archivo}: total de aserciones {total_hoy} < {total_min}")
    return problemas


# --- CA-64: nombres ------------------------------------------------------------------


def test_ca64_todo_criterio_auto_tiene_un_test_con_su_numero_en_el_nombre():
    nombres = _all_names()
    assert len(nombres) > 500  # control positivo: el recorrido vio la suite real
    assert _faltantes(nombres) == []


def test_ca64_el_detector_encuentra_los_faltantes_control_positivo():
    # Con una suite sintetica incompleta el detector tiene que reportar lo que falta.
    sinteticos = ["test_ca1_algo", "test_ca2", "test_cs1_x", "test_ca10_y", "test_ca_s7_guardas"]
    faltan = _faltantes(sinteticos)
    assert "CA-3" in faltan and "CS-2" in faltan and "CA-69" in faltan
    assert "CA-1" not in faltan and "CA-2" not in faltan and "CA-10" not in faltan
    assert "CS-1" not in faltan and "CA-S7" not in faltan
    # `test_ca10_` no cuenta como CA-1 ni `test_ca1_` como CA-10 (lookahead exacto).
    assert _ids_cubiertos(["test_ca10_x"]) == {("ca", 10)}
    assert _ids_cubiertos(["test_ca1_x"]) == {("ca", 1)}
    assert _ids_cubiertos(["test_ca1x_x", "test_algo_ca1_x"]) == set()
    # Un CA reemplazado no es obligatorio.
    assert not any(f"CA-{n}" in _faltantes([]) for n in CA_REEMPLAZADOS)


def test_ca64_los_reemplazados_quedan_fuera_de_la_lista_auto():
    assert 7 not in CA_AUTO and 22 not in CA_AUTO and 49 not in CA_AUTO
    assert len(CA_AUTO) == 6 + 14 + 26 + 5 + 16 + 3
    assert len(CS_AUTO) == 15 + 11 + 1


# --- Manifiestos de tests conservados (aserciones minimas de HEAD 957f11d) ------------

M_CA25 = ("test_ai_explain.py", {
    "test_guard_adversarial_numero_realmente_distinto_sigue_rechazado": 1,
    "test_guard_caso_limite_suma_compuesta_35_ausente_rechazada": 1,
    "test_guard_coma_de_miles_sigue_funcionando_no_se_rompe_por_el_fix_decimal": 1,
    "test_guard_coma_decimal_numero_realmente_distinto_sigue_rechazado": 1,
    "test_guard_extendido_rechaza_alucinacion_en_los_20_codes_nuevos": 1,
    "test_guard_normaliza_coma_decimal_mismo_valor_pasa": 1,
    "test_guard_normaliza_porcentaje_a_proporcion_decimal_mismo_valor_pasa": 1,
    "test_guard_normaliza_separador_de_miles_mismo_valor_pasa": 1,
    "test_guard_normaliza_signo_mas_mismo_valor_pasa": 1,
    "test_guard_normaliza_simbolo_pesos_mismo_valor_pasa": 1,
    "test_guard_normalizacion_de_porcentaje_no_afloja_numero_realmente_distinto": 1,
    "test_guard_respuesta_vacia_es_true_caso_de_frontera": 2,
    "test_guard_subconjunto_de_tokens_es_true": 1,
    "test_guard_token_ausente_de_datos_es_false": 1,
    "test_normalize_numeric_token_no_toca_tokens_no_numericos": 1,
})

M_CA33_SAFE = ("test_ai_rewrite.py", {
    "test_is_safe_rewrite_agrega_numero_nuevo_es_false": 1,
    "test_is_safe_rewrite_cambia_check_por_cross_es_false": 2,
    "test_is_safe_rewrite_cambia_numero_es_false": 1,
    "test_is_safe_rewrite_cambia_si_no_es_false": 2,
    "test_is_safe_rewrite_identico_es_true": 1,
    "test_is_safe_rewrite_omite_numero_es_false": 1,
    "test_is_safe_rewrite_rango_reformulado_rechaza_fail_closed": 1,
    "test_is_safe_rewrite_reordena_oracion_preservando_multiset_es_true": 1,
    "test_is_safe_rewrite_separador_decimal_coma_vs_punto_rechaza_fail_closed": 1,
    "test_is_safe_rewrite_signo_unicode_rechaza_fail_closed": 1,
})

M_CA33_CLASSIFY = ("test_ai_rewrite.py", {
    "test_ai_rewrite_no_hardcodea_el_texto_de_los_disclaimers_como_literal_propio": 3,
    "test_classify_lines_indice_se_reinicia_por_seccion": 4,
    "test_classify_lines_linea_mixta_prosa_y_numero_placeholder_completo": 5,
    "test_classify_lines_pillars_reales_4_placeholders_unicos": 3,
    "test_classify_lines_protege_ambos_disclaimers_dentro_de_una_seccion_real": 7,
    "test_classify_lines_protege_disclaimer_no_asesoramiento_por_igualdad_exacta": 3,
    "test_classify_lines_protege_disclaimer_wacc_dcf_por_igualdad_exacta": 3,
    "test_classify_lines_protege_por_igualdad_exacta_incluso_sin_protected_tokens": 2,
    "test_classify_lines_sin_protected_tokens_no_cambia": 2,
    "test_classify_lines_variante_no_identica_del_disclaimer_no_se_protege_fail_open": 4,
    "test_protected_disclaimers_no_es_una_copia_hardcodeada_sino_importada_de_summary": 2,
    "test_rewrite_parts_adversarial_omite_placeholder_de_disclaimer_cae_a_fallback_completo": 4,
    "test_rewrite_parts_condensa_notas_de_transparencia_reales_preserva_ambos_disclaimers": 4,
    "test_rewrite_parts_condensacion_de_prosa_libre_sin_disclaimers_no_regresion": 2,
})

M_CA33_RECON = ("test_ai_rewrite.py", {
    "test_reconstruct_section_capa2_asterisco_desparejado_es_none": 2,
    "test_reconstruct_section_capa2_asterisco_duplicado_es_none": 1,
    "test_reconstruct_section_capa2_caso_artificial_bug_de_clasificacion": 1,
    "test_reconstruct_section_capa2_caso_realista_prosa_libre_alucina_numero": 2,
    "test_reconstruct_section_capa2_guion_bajo_italica_desparejado_es_none": 1,
    "test_reconstruct_section_capa2_markdown_balanceado_pero_distinto_es_ok": 1,
    "test_reconstruct_section_caso_momentum_52_semanas": 2,
    "test_reconstruct_section_orden_distinto_reconstruye_correctamente": 1,
    "test_reconstruct_section_placeholder_desconocido_es_none": 1,
    "test_reconstruct_section_placeholder_duplicado_es_none": 1,
    "test_reconstruct_section_placeholder_faltante_es_none": 1,
    "test_reconstruct_section_reproduce_escenario_bloqueante_de_security_pillars": 3,
})

M_CA34 = ("test_ai_rewrite.py", {
    "test_reconstruct_section_capa2_asterisco_desparejado_es_none": 2,
    "test_reconstruct_section_capa2_asterisco_duplicado_es_none": 1,
    "test_reconstruct_section_capa2_caso_artificial_bug_de_clasificacion": 1,
    "test_reconstruct_section_capa2_caso_realista_prosa_libre_alucina_numero": 2,
    "test_reconstruct_section_capa2_guion_bajo_italica_desparejado_es_none": 1,
    "test_reconstruct_section_capa2_markdown_balanceado_pero_distinto_es_ok": 1,
    "test_rewrite_parts_end_to_end_veredicto_asterisco_desparejado_cae_a_original": 3,
})

M_CA36 = ("test_ai_rewrite.py", {
    "test_rewrite_parts_system_prompt_incluye_regla_6_de_placeholders": 2,
    "test_rewrite_parts_system_prompt_incluye_regla_7_de_condensacion": 3,
    "test_system_prompt_regla_4_ajustada_para_no_contradecir_la_regla_7": 2,
})

M_CA42_CTX = ("test_ai_explain.py", {
    "test_gate_chat_id_no_autorizado_corta_antes_del_handler_xp": 2,
    "test_hallazgo9_context_id_de_otro_chat_id_responde_vencido": 2,
    "test_hallazgo9_mensaje_y_log_indistinguibles_de_context_id_inexistente": 4,
    "test_hallazgo9_no_consume_rate_limiter_para_navegacion_ajena": 1,
    "test_hallazgo9_propio_chat_id_sigue_funcionando_tras_intento_ajeno": 2,
})

M_CA42_STORE = ("test_ai_explain.py", {
    "test_store_evict_entrada_mas_vieja_con_max_entries_chico": 2,
    "test_store_get_devuelve_mismos_valores_que_put_ambos_kind": 2,
    "test_store_get_id_inexistente_devuelve_none": 1,
    "test_store_get_ttl_vencido_devuelve_none_via_reloj_inyectado": 2,
    "test_store_put_devuelve_context_ids_distintos_formato_valido": 3,
    "test_store_put_purga_entradas_vencidas_via_evict_expired": 1,
    "test_store_put_reintenta_context_id_en_colision": 2,
})

M_CA42_SEC = ("test_security.py", {
    "test_gate_autorizado_no_lanza": 1,
    "test_gate_autorizado_otros_ids_no_lanza": 1,
    "test_gate_conjunto_vacio_rechaza_cualquier_chat_id": 1,
    "test_gate_loguea_aceptacion_sin_exponer_conjunto_completo": 2,
    "test_gate_no_autorizado_lanza_stop": 1,
    "test_gate_no_privado_lanza_stop": 1,
    "test_gate_responde_callback_query_no_autorizado": 2,
    "test_get_allowed_chat_id_cero": 1,
    "test_get_allowed_chat_id_con_espacios": 1,
    "test_get_allowed_chat_id_dos_elementos": 1,
    "test_get_allowed_chat_id_duplicados_no_lanza": 1,
    "test_get_allowed_chat_id_elemento_no_numerico": 2,
    "test_get_allowed_chat_id_elemento_vacio_por_coma": 1,
    "test_get_allowed_chat_id_mas_de_tres_ids": 1,
    "test_get_allowed_chat_id_negativo": 1,
    "test_get_allowed_chat_id_tres_elementos": 1,
    "test_get_allowed_chat_id_un_solo_entero": 1,
    "test_get_allowed_chat_id_unicode_arabigo_indico": 1,
    "test_get_allowed_chat_id_unicode_fullwidth": 1,
    "test_get_allowed_chat_id_variable_ausente": 1,
    "test_get_allowed_chat_id_variable_solo_espacios": 1,
    "test_get_allowed_chat_id_variable_vacia": 1,
    "test_is_authorized_chat_conjunto_vacio_nunca_autoriza": 1,
    "test_is_authorized_chat_id_fuera_del_conjunto": 1,
    "test_is_authorized_chat_no_privado": 1,
    "test_is_authorized_chat_otros_ids_del_conjunto": 1,
    "test_is_authorized_chat_primer_id_del_conjunto": 1,
    "test_is_authorized_chat_sin_chat": 1,
})

M_CA42_BOT = ("test_bot.py", {
    "test_main_falla_sin_allowed_chat_id": 1,
})

M_BREV = ("test_ai_explain.py", {
    "test_fit_respuesta_texto_corto_se_devuelve_sin_modificar": 1,
    "test_fit_respuesta_texto_largo_corta_en_ultimo_fin_de_oracion": 3,
    "test_fit_respuesta_sin_corte_valido_devuelve_none": 1,
})



def test_ca25_guard_de_numeros_conservado_sin_perder_aserciones():
    archivo, manifiesto = M_CA25
    assert len(manifiesto) == 15 and sum(manifiesto.values()) == 16
    assert _verificar_manifiesto(archivo, manifiesto) == []


def test_ca33_is_safe_classify_reconstruct_y_disclaimers_conservados_sin_perder_aserciones():
    esperados = ((M_CA33_SAFE, 10, 12), (M_CA33_CLASSIFY, 14, 48), (M_CA33_RECON, 12, 17))
    for (archivo, manifiesto), tests, aserciones in esperados:
        assert len(manifiesto) == tests and sum(manifiesto.values()) == aserciones
        assert _verificar_manifiesto(archivo, manifiesto) == []


def test_ca34_balance_de_markdown_conservado_sin_perder_aserciones():
    archivo, manifiesto = M_CA34
    assert any("asterisco_desparejado" in n for n in manifiesto)
    assert any("end_to_end_veredicto" in n for n in manifiesto)
    assert _verificar_manifiesto(archivo, manifiesto) == []


def test_ca36_tests_del_system_prompt_conservados_sin_perder_aserciones():
    archivo, manifiesto = M_CA36
    assert len(manifiesto) == 3 and sum(manifiesto.values()) == 7
    assert _verificar_manifiesto(archivo, manifiesto) == []


def test_ca42_contexto_atado_a_chat_id_store_y_gate_fail_closed_conservados():
    for archivo, manifiesto in (M_CA42_CTX, M_CA42_STORE, M_CA42_SEC, M_CA42_BOT):
        assert manifiesto  # control positivo: el manifiesto no esta vacio
        assert _verificar_manifiesto(archivo, manifiesto) == []
    assert sum(M_CA42_STORE[1].values()) == 13


def test_fit_respuesta_conservado_sin_perder_aserciones():
    archivo, manifiesto = M_BREV
    assert len(manifiesto) == 3 and sum(manifiesto.values()) == 5
    assert _verificar_manifiesto(archivo, manifiesto) == []


def test_ca64_el_verificador_de_manifiestos_detecta_borrados_y_vaciados_control_positivo(monkeypatch):
    sintetico = {"test_a.py": {"test_uno": 2, "test_dos": 0}}
    monkeypatch.setattr(__name__ + "._tests_of", lambda archivo: sintetico[archivo])
    problemas = _verificar_manifiesto("test_a.py", {"test_uno": 2, "test_dos": 1, "test_tres": 1})
    assert any("falta test_a.py::test_tres" in p for p in problemas)
    assert any("test_dos" in p and "bajo" in p for p in problemas)
    assert _verificar_manifiesto("test_a.py", {"test_uno": 2}) == []
