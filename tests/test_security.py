"""Tests de `security.py` — fail-closed, filtro global de chat_id, rate-limit.

Ver spec `contexto/specs/abiertas/SDD_multiusuario_chat_id.md` (sección `qa`,
"Casos obligatorios") para el mapeo test -> criterio. Los números de test en
los comentarios (#1-#33) corresponden a esa lista.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop

from investbot import security


# ---------------------------------------------------------------------------
# A. get_allowed_chat_id — parseo CSV y fail-closed
# ---------------------------------------------------------------------------


def test_get_allowed_chat_id_un_solo_entero():
    """#1 — backward compat exacta con el caso de un solo entero."""
    assert security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": "12345"}
    ) == frozenset({12345})


def test_get_allowed_chat_id_dos_elementos():
    """#2 — CSV de 2 elementos."""
    assert security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": "111,222"}
    ) == frozenset({111, 222})


def test_get_allowed_chat_id_tres_elementos():
    """#3 — caso de uso explícito "hasta 3" de Daniela."""
    assert security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": "111,222,333"}
    ) == frozenset({111, 222, 333})


def test_get_allowed_chat_id_con_espacios():
    """#4 — espacios alrededor de cada elemento, tolerados por strip()."""
    assert security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": " 111 , 222 ,333 "}
    ) == frozenset({111, 222, 333})


def test_get_allowed_chat_id_variable_ausente():
    """#5 — variable no seteada."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={})


def test_get_allowed_chat_id_variable_vacia():
    """#6 — string vacío."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": ""})


def test_get_allowed_chat_id_variable_solo_espacios():
    """#7 — solo espacios, caso distinto de vacío puro."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "   "})


def test_get_allowed_chat_id_elemento_no_numerico():
    """#8 — un elemento no numérico; el mensaje debe identificar el valor
    crudo inválido, no solo lanzar la excepción."""
    with pytest.raises(security.ConfigurationError) as exc_info:
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "111,abc,333"})
    assert "abc" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("111,,222", id="coma_vacia_interna"),
        pytest.param("111,222,", id="trailing_comma"),
        pytest.param(",111,222", id="leading_comma"),
        pytest.param(",", id="un_solo_elemento_vacio_coma_sola"),
    ],
)
def test_get_allowed_chat_id_elemento_vacio_por_coma(raw):
    """#9-#12 — comas vacías/duplicadas/trailing/leading, cada una como caso
    separado (vía parametrize con id legible, no un solo test genérico)."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": raw})


def test_get_allowed_chat_id_duplicados_no_lanza():
    """#13 — duplicados deduplican vía frozenset, sin excepción."""
    result = security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "111,111,222"})
    assert result == frozenset({111, 222})


def test_get_allowed_chat_id_mas_de_tres_ids():
    """#14 — más de 3 IDs, válido, sin tope duro."""
    result = security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": "111,222,333,444,555"}
    )
    assert result == frozenset({111, 222, 333, 444, 555})


def test_get_allowed_chat_id_unicode_arabigo_indico():
    """#15 — dígitos arábigo-índicos no-ASCII, rechazados por el guard isascii()."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "١١١"})


def test_get_allowed_chat_id_unicode_fullwidth():
    """#16 — dígitos fullwidth no-ASCII, rechazados por el guard isascii()."""
    with pytest.raises(security.ConfigurationError):
        security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "１２３"})


def test_get_allowed_chat_id_cero():
    """#17 — "0" como único elemento, válido a nivel de parseo."""
    assert security.get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "0"}) == frozenset({0})


def test_get_allowed_chat_id_negativo():
    """#18 — entero negativo como único elemento, válido a nivel de parseo."""
    assert security.get_allowed_chat_id(
        env={"TELEGRAM_ALLOWED_CHAT_ID": "-100"}
    ) == frozenset({-100})


def test_ensure_non_empty_lanza_si_vacio():
    """#19 — chequeo defensivo de conjunto vacío (security sección 1, criterio
    D), testeado directamente sobre `_ensure_non_empty` sin pasar por el
    parseo de CSV (no hay input público que llegue vacío tras los guards
    anteriores)."""
    with pytest.raises(security.ConfigurationError):
        security._ensure_non_empty(frozenset())


def test_ensure_non_empty_retorna_igual_si_no_vacio():
    """Complemento de #19: si el conjunto no está vacío, se retorna sin cambios."""
    result = security._ensure_non_empty(frozenset({111}))
    assert result == frozenset({111})


# ---------------------------------------------------------------------------
# B. is_authorized_chat — membership
# ---------------------------------------------------------------------------


def _update(chat_id, chat_type):
    update = SimpleNamespace()
    update.effective_chat = SimpleNamespace(id=chat_id, type=chat_type) if chat_id is not None else None
    return update


def test_is_authorized_chat_primer_id_del_conjunto():
    """#20 — el primer chat_id del conjunto es aceptado."""
    assert security.is_authorized_chat(_update(111, "private"), frozenset({111, 222, 333})) is True


@pytest.mark.parametrize("chat_id", [222, 333])
def test_is_authorized_chat_otros_ids_del_conjunto(chat_id):
    """#21 — los otros 2 IDs del conjunto también son aceptados
    individualmente, no alcanza con probar solo el primero."""
    assert security.is_authorized_chat(_update(chat_id, "private"), frozenset({111, 222, 333})) is True


def test_is_authorized_chat_id_fuera_del_conjunto():
    """#22 — chat_id fuera del conjunto autorizado."""
    assert security.is_authorized_chat(_update(999, "private"), frozenset({111, 222, 333})) is False


def test_is_authorized_chat_no_privado():
    """#23 — no privado, aunque el ID esté autorizado."""
    assert security.is_authorized_chat(_update(111, "group"), frozenset({111, 222, 333})) is False


def test_is_authorized_chat_sin_chat():
    """#24 — chat None."""
    assert security.is_authorized_chat(_update(None, None), frozenset({111})) is False


@pytest.mark.parametrize("chat_id", [111, 0, -100])
def test_is_authorized_chat_conjunto_vacio_nunca_autoriza(chat_id):
    """#25 — un conjunto vacío nunca autoriza a nadie, sea cual sea el
    chat_id (incluyendo 0 y un negativo)."""
    assert security.is_authorized_chat(_update(chat_id, "private"), frozenset()) is False


# ---------------------------------------------------------------------------
# C. build_chat_id_gate — handler global (group=-1)
# ---------------------------------------------------------------------------


async def test_gate_autorizado_no_lanza():
    """#26 — deja pasar el primer chat_id del conjunto en chat privado."""
    gate = security.build_chat_id_gate(frozenset({111, 222, 333}))
    update = _update(111, "private")
    update.callback_query = None
    result = await gate(update, context=SimpleNamespace())
    assert result is None  # deja pasar, no corta la cadena


@pytest.mark.parametrize("chat_id", [222, 333])
async def test_gate_autorizado_otros_ids_no_lanza(chat_id):
    """#27 — repetir el caso 26 para los otros 2 IDs del conjunto."""
    gate = security.build_chat_id_gate(frozenset({111, 222, 333}))
    update = _update(chat_id, "private")
    update.callback_query = None
    result = await gate(update, context=SimpleNamespace())
    assert result is None


async def test_gate_no_autorizado_lanza_stop():
    """#28 — corta con ApplicationHandlerStop para ID fuera del conjunto."""
    gate = security.build_chat_id_gate(frozenset({111, 222, 333}))
    update = _update(999, "private")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


async def test_gate_no_privado_lanza_stop():
    """#29 — corta con ApplicationHandlerStop para chat no privado."""
    gate = security.build_chat_id_gate(frozenset({111, 222, 333}))
    update = _update(111, "group")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


async def test_gate_responde_callback_query_no_autorizado():
    """#30 — callback_query.answer() best-effort en el camino de rechazo."""
    gate = security.build_chat_id_gate(frozenset({111, 222, 333}))
    update = _update(999, "private")
    update.callback_query = SimpleNamespace(answer=AsyncMock())
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())
    update.callback_query.answer.assert_awaited_once()


@pytest.mark.parametrize("chat_id", [111, 222, 333])
async def test_gate_loguea_aceptacion_sin_exponer_conjunto_completo(chat_id, caplog):
    """#31 — logging de aceptación en INFO: identifica el chat_id individual
    correcto en cada invocación, y el mensaje NO contiene la representación
    del frozenset completo de autorizados."""
    allowed_chat_ids = frozenset({111, 222, 333})
    gate = security.build_chat_id_gate(allowed_chat_ids)
    update = _update(chat_id, "private")
    update.callback_query = None
    with caplog.at_level("INFO"):
        await gate(update, context=SimpleNamespace())
    assert str(chat_id) in caplog.text
    assert str(allowed_chat_ids) not in caplog.text


@pytest.mark.parametrize("chat_id", [111, 222])
async def test_gate_conjunto_vacio_rechaza_cualquier_chat_id(chat_id):
    """#32 — build_chat_id_gate(frozenset()) rechaza cualquier update
    entrante, ninguno pasa."""
    gate = security.build_chat_id_gate(frozenset())
    update = _update(chat_id, "private")
    update.callback_query = None
    with pytest.raises(ApplicationHandlerStop):
        await gate(update, context=SimpleNamespace())


# ---------------------------------------------------------------------------
# D. Revisión de código — patrón permisivo "if not allowed_chat_ids"
# ---------------------------------------------------------------------------


def test_no_existe_patron_permisivo_if_not_allowed_chat_ids():
    """#33 — grep/code review explícito: confirma que no existe ningún
    patrón `if not allowed_chat_ids` (o equivalente) en security.py que
    retorne/permita autorización. Ver también el `grep` manual exigido por
    QA como evidencia adicional en el Momento 2."""
    import inspect

    source = inspect.getsource(security)
    assert "if not allowed_chat_id" not in source


# ---------------------------------------------------------------------------
# Rate limiter en memoria (sin cambios — no tocado por este SDD)
# ---------------------------------------------------------------------------


def test_rate_limiter_permite_hasta_el_maximo():
    limiter = security.InMemoryRateLimiter(max_requests=3, window_seconds=60.0)
    key = "chat:111"
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is False  # 4ta excede el máximo


def test_rate_limiter_ventanas_independientes_por_key():
    limiter = security.InMemoryRateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("chat:111") is True
    assert limiter.allow("chat:222") is True  # otra key, otra ventana
    assert limiter.allow("chat:111") is False


def test_rate_limiter_libera_tras_expirar_ventana():
    limiter = security.InMemoryRateLimiter(max_requests=1, window_seconds=0.01)
    key = "chat:111"
    assert limiter.allow(key) is True
    import time

    time.sleep(0.02)
    assert limiter.allow(key) is True


# ---------------------------------------------------------------------------
# Log de Conflict (409) — nunca incluye el token
# ---------------------------------------------------------------------------


def test_log_conflict_error_no_incluye_token(caplog):
    import logging

    logger = logging.getLogger("test_conflict_logger")
    with caplog.at_level(logging.WARNING):
        security.log_conflict_error(logger)
    assert "posible uso concurrente del token detectado" in caplog.text
