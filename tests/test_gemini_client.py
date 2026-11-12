"""Tests del cliente de Gemini (SDD_migracion_gemini.md, fase F1).

Cubre CA-1 a CA-6, CA-8 a CA-21, CA-24, CA-66, CA-68, CA-69 y CS-1 (config),
CS-2, CS-3, CS-17, CS-18, CS-19(b). Todo con `httpx.MockTransport`: cero red
(la fixture autouse de `conftest.py` la bloquea) y cero sleeps reales (se
inyecta un registrador o se usa el parche autouse de `gemini_client._sleep`).
Convención de nombres: `test_ca<n>_...` / `test_cs<n>_...`.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import traceback
from pathlib import Path

import httpx
import pytest

from investbot import ai_rewrite, gemini_client
from investbot.gemini_client import GeminiConfig, GeminiError, generate_text, load_config

from .gemini_helpers import (
    CANARY_BODY,
    CANARY_KEY,
    DUMMY_KEY,
    FAILURE_TABLE,
    MODEL_DEFAULT,
    BASE_URL,
    SleepRecorder,
    FailureCase,
    gemini_response,
    generation_config,
    request_body,
    sequence_handler,
    system_text,
    usage_metadata,
    user_text,
)

# Referencia al `_sleep` real, capturada antes de que la fixture autouse lo parchee.
_REAL_SLEEP = gemini_client._sleep

REPO = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO / "src" / "investbot" / "gemini_client.py"
ENV_EXAMPLE = REPO / ".env.example"
FEATURES = ("explain", "rewrite")
TOKEN_RE = re.compile(r"GEMINI_[A-Z0-9_]+")
SUCCESS_RE = re.compile(
    r"Gemini OK feature=(explain|rewrite) model=\S+ served=\S+ promptTokens=\S+ "
    r"outputTokens=\S+ thoughtsTokens=\S+ finishReason=STOP ms=\d+"
)


@pytest.fixture(autouse=True)
def _logging_explicito(caplog):
    """CQ-17: niveles explícitos, `configure_logging()` de bot.py no contamina."""
    for name in ("investbot", "httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


def _env(**extra: str) -> dict[str, str]:
    return {"GEMINI_API_KEY": DUMMY_KEY, **extra}


def _records(caplog, level: int | None = None):
    return [
        r
        for r in caplog.records
        if r.name == "investbot.gemini_client" and (level is None or r.levelno == level)
    ]


def _config(**kwargs) -> GeminiConfig:
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("api_key", DUMMY_KEY)
    return GeminiConfig(**kwargs)


async def _call(
    handler,
    *,
    feature: str = "explain",
    config: GeminiConfig | None = None,
    retries: int | None = None,
    sleep=None,
    mime: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 12.0,
    system_prompt: str = "SISTEMA FIJO",
    user: str = '{"pregunta": "p", "datos": {}}',
    client_kwargs: dict | None = None,
):
    cfg = config or _config()
    if retries is None:
        retries = 1 if feature == "explain" else 0
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), **(client_kwargs or {})
    ) as client:
        return await generate_text(
            client,
            cfg,
            system_prompt=system_prompt,
            user_text=user,
            max_output_tokens=max_tokens,
            timeout_seconds=timeout,
            max_retries=retries,
            feature=feature,
            response_mime_type=mime,
            sleep=sleep,
        )


def _ok_handler(text: str = "hola", **kw):
    return sequence_handler((("body", gemini_response(text, **kw)),))


# =============================================================================
# Configuración
# =============================================================================


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "},
                                 {"GEMINI_API_KEY": "\n"}])
def test_ca1_key_ausente_vacia_o_en_blanco_deshabilita_sin_error(env, caplog):
    cfg = load_config({**env, "GEMINI_REWRITE_ENABLED": "true"})
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    info = [r.getMessage() for r in _records(caplog, logging.INFO)]
    assert "Gemini deshabilitado (GEMINI_API_KEY ausente)" in info  # control positivo
    assert _records(caplog, logging.ERROR) == []
    assert "GEMINI_KEY_INVALID" not in caplog.text


def test_ca1_load_config_nunca_lanza(caplog):
    class Explosivo(dict):
        def get(self, *a, **k):
            raise RuntimeError(f"boom {CANARY_KEY}")

    cfg = load_config(Explosivo())
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1  # control positivo
    assert "RuntimeError" in errors[0].getMessage()
    assert CANARY_KEY not in caplog.text


def test_ca1_load_config_sin_argumento_lee_os_environ(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", DUMMY_KEY)
    assert load_config().enabled is True
    monkeypatch.delenv("GEMINI_API_KEY")
    assert load_config().enabled is False


def test_ca2_defaults_con_key_sin_otras_variables():
    cfg = load_config(_env())
    assert cfg.enabled is True
    assert cfg.api_key == DUMMY_KEY
    assert cfg.model == "gemini-3.5-flash-lite"
    assert cfg.thinking_level == "minimal"
    assert cfg.timeout_seconds == 12.0
    assert cfg.rewrite_enabled is False
    assert cfg.rewrite_timeout_seconds == 8.0


def test_ca2_valores_en_blanco_equivalen_a_no_seteado(caplog):
    cfg = load_config(
        _env(
            GEMINI_MODEL="  ",
            GEMINI_THINKING_LEVEL="",
            GEMINI_TIMEOUT_SECONDS=" \n",
            GEMINI_REWRITE_TIMEOUT_SECONDS="",
            GEMINI_REWRITE_ENABLED="  ",
        )
    )
    assert cfg == load_config(_env())
    assert _records(caplog, logging.ERROR) == []
    assert _records(caplog, logging.WARNING) == []


MODELOS_INVALIDOS = [
    "gemini-2.5-flash",
    "gemini-1.5-pro",
    "../x",
    "gemini-3.5-flash-lite?key=x",
    "GEMINI-3.5-FLASH-LITE",
    "gemini-flash-latest",
    "gemini-3.5-" + "a" * 60,  # 71 caracteres
    "gemini-3.5-" + "a" * 54,  # 65 caracteres (borde)
    "gemini-0-x",
    "gemini-03.5-flash-lite",
    "gemini-00.1-x",
    "gemini-3.05-x",
    "gemini-3.5-flash\nlite",
]


@pytest.mark.parametrize("modelo", MODELOS_INVALIDOS)
def test_ca3_modelo_invalido_deshabilita_incluido_rewrite(modelo, caplog):
    cfg = load_config(_env(GEMINI_MODEL=modelo, GEMINI_REWRITE_ENABLED="true"))
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1  # control positivo
    assert "GEMINI_MODEL" in errors[0].getMessage()
    assert modelo not in caplog.text
    assert DUMMY_KEY not in caplog.text


@pytest.mark.parametrize(
    "modelo",
    ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-4-argon", "gemini-3.0-flash",
     "gemini-3.5-" + "a" * 53],  # exactamente 64 caracteres
)
def test_ca3_modelo_valido_pasa(modelo):
    assert len(modelo) <= 64
    cfg = load_config(_env(GEMINI_MODEL=modelo))
    assert cfg.enabled is True
    assert cfg.model == modelo


def test_ca3_modelo_con_salto_final_se_stripea_y_pasa():
    cfg = load_config(_env(GEMINI_MODEL="  gemini-3.5-flash-lite\n"))
    assert cfg.enabled is True
    assert cfg.model == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("nivel", ["ultra", "mi\nnimal", "extreme", "0", "low high"])
def test_ca4_nivel_invalido_deshabilita(nivel, caplog):
    cfg = load_config(_env(GEMINI_THINKING_LEVEL=nivel, GEMINI_REWRITE_ENABLED="true"))
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "GEMINI_THINKING_LEVEL" in errors[0].getMessage()
    assert nivel not in caplog.text


@pytest.mark.parametrize(
    "valor,esperado",
    [("minimal", "minimal"), ("LOW", "low"), (" High ", "high"), ("medium\n", "medium")],
)
def test_ca4_nivel_valido_tras_strip_y_lower(valor, esperado):
    cfg = load_config(_env(GEMINI_THINKING_LEVEL=valor))
    assert cfg.enabled is True
    assert cfg.thinking_level == esperado


@pytest.mark.parametrize("valor", ["abc", "0.0", "-1", "61", "nan", "inf", "1e999", "60.0001"])
def test_ca5_timeout_invalido_cae_a_12_con_warning(valor, caplog):
    cfg = load_config(_env(GEMINI_TIMEOUT_SECONDS=valor))
    assert cfg.enabled is True
    assert cfg.timeout_seconds == 12.0
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "GEMINI_TIMEOUT_SECONDS" in warnings[0].getMessage()
    assert valor not in caplog.text


@pytest.mark.parametrize("valor", ["abc", "0.0", "-7", "31", "nan", "30.5"])
def test_ca5_timeout_rewrite_invalido_cae_a_8_con_warning(valor, caplog):
    cfg = load_config(_env(GEMINI_REWRITE_TIMEOUT_SECONDS=valor))
    assert cfg.enabled is True
    assert cfg.rewrite_timeout_seconds == 8.0
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "GEMINI_REWRITE_TIMEOUT_SECONDS" in warnings[0].getMessage()
    assert valor not in caplog.text


@pytest.mark.parametrize("valor,esperado", [("60", 60.0), ("0.5", 0.5), (" 20 ", 20.0)])
def test_ca5_timeouts_validos_en_los_bordes(valor, esperado):
    assert load_config(_env(GEMINI_TIMEOUT_SECONDS=valor)).timeout_seconds == esperado
    assert load_config(
        _env(GEMINI_REWRITE_TIMEOUT_SECONDS=valor if esperado <= 30 else "30")
    ).rewrite_timeout_seconds == (esperado if esperado <= 30 else 30.0)


@pytest.mark.parametrize("valor", ["true", "1", "yes", "TRUE", "Yes", " true ", "YES\n"])
def test_ca6_flag_verdadero_con_key_habilita_rewrite(valor):
    cfg = load_config(_env(GEMINI_REWRITE_ENABLED=valor))
    assert cfg.enabled is True
    assert cfg.rewrite_enabled is True


@pytest.mark.parametrize("valor", ["false", "0", "no", "on", "2", "truee", "y", "enabled"])
def test_ca6_flag_cualquier_otro_valor_es_false(valor):
    cfg = load_config(_env(GEMINI_REWRITE_ENABLED=valor))
    assert cfg.enabled is True  # control: Gemini sigue habilitado
    assert cfg.rewrite_enabled is False


@pytest.mark.parametrize(
    "env",
    [
        {"GEMINI_REWRITE_ENABLED": "true"},
        _env(GEMINI_REWRITE_ENABLED="true", GEMINI_MODEL="gemini-2.5-flash"),
        _env(GEMINI_REWRITE_ENABLED="true", GEMINI_API_KEY="comillas'invalidas-0000000"),
        _env(GEMINI_REWRITE_ENABLED="true", GEMINI_THINKING_LEVEL="ultra"),
    ],
)
def test_ca6_flag_true_sin_gemini_valido_no_lanza_y_loguea_ignorado(env, caplog):
    cfg = load_config(env)
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    infos = [r.getMessage() for r in _records(caplog, logging.INFO)]
    assert "GEMINI_REWRITE_ENABLED ignorado: Gemini deshabilitado" in infos


def test_ca6_flag_false_sin_key_no_loguea_ignorado(caplog):
    load_config({"GEMINI_REWRITE_ENABLED": "false"})
    assert "ignorado" not in caplog.text
    assert "GEMINI_API_KEY ausente" in caplog.text  # control: sí hubo logs


def test_ca6_habilitado_loguea_modelo_y_estado_del_rewrite(caplog):
    load_config(_env(GEMINI_REWRITE_ENABLED="true"))
    load_config(_env())
    infos = [r.getMessage() for r in _records(caplog, logging.INFO)]
    assert f"Gemini habilitado model={MODEL_DEFAULT} rewrite=on" in infos
    assert f"Gemini habilitado model={MODEL_DEFAULT} rewrite=off" in infos


def _parse_env_example() -> dict[str, str]:
    """Solo líneas activas `^[A-Z_]+=`; ignora comentarios que mencionen GEMINI_*."""
    active: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line)
        if m:
            active[m.group(1)] = m.group(2)
    return active


def test_ca8_env_example_lista_exactamente_las_seis_variables_gemini():
    texto = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^#.*GEMINI_API_KEY", texto, re.M)  # control: hay comentarios que la mencionan
    gemini = {k: v for k, v in _parse_env_example().items() if k.startswith("GEMINI_")}
    assert set(gemini) == {
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_THINKING_LEVEL",
        "GEMINI_TIMEOUT_SECONDS",
        "GEMINI_REWRITE_ENABLED",
        "GEMINI_REWRITE_TIMEOUT_SECONDS",
    }
    assert len(re.findall(r"^GEMINI_[A-Z_]+=", texto, re.M)) == 6  # sin duplicados


def test_ca8_defaults_documentados_coinciden_con_el_codigo():
    gemini = {k: v for k, v in _parse_env_example().items() if k.startswith("GEMINI_")}
    base = load_config({"GEMINI_API_KEY": DUMMY_KEY})
    assert gemini["GEMINI_MODEL"] == base.model
    assert gemini["GEMINI_THINKING_LEVEL"] == base.thinking_level
    assert float(gemini["GEMINI_TIMEOUT_SECONDS"]) == base.timeout_seconds
    assert float(gemini["GEMINI_REWRITE_TIMEOUT_SECONDS"]) == base.rewrite_timeout_seconds
    assert gemini["GEMINI_REWRITE_ENABLED"] == "false"
    assert base.rewrite_enabled is False
    # Aplicar los defaults documentados (con la key de test) no cambia nada.
    documentados = {k: v for k, v in gemini.items() if k != "GEMINI_API_KEY"}
    assert load_config({"GEMINI_API_KEY": DUMMY_KEY, **documentados}) == base


def test_cs17_env_example_key_vacia_y_ningun_valor_con_forma_de_key():
    gemini = {k: v for k, v in _parse_env_example().items() if k.startswith("GEMINI_")}
    assert gemini["GEMINI_API_KEY"] == ""
    for nombre, valor in gemini.items():
        assert "AIza" not in valor, nombre
        assert not re.fullmatch(r"[A-Za-z0-9_-]{30,}", valor), nombre
    assert "AIza" not in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_cs17_dummy_key_de_tests_no_tiene_forma_de_key_real_y_es_valida():
    assert not DUMMY_KEY.startswith("AIza")
    assert not CANARY_KEY.startswith("AIza")
    assert load_config({"GEMINI_API_KEY": DUMMY_KEY}).enabled is True


@pytest.mark.parametrize("var", [
    "GEMINI_MODEL", "GEMINI_THINKING_LEVEL", "GEMINI_TIMEOUT_SECONDS",
    "GEMINI_REWRITE_TIMEOUT_SECONDS", "GEMINI_REWRITE_ENABLED",
])
def test_cs1_canario_en_cualquier_variable_no_aparece_en_logs_ni_repr(var, caplog):
    cfg = load_config(_env(**{var: CANARY_KEY}))
    assert cfg.api_key in ("", DUMMY_KEY)
    assert len(caplog.records) >= 1  # control positivo: algo se logueó
    exc_texts = "".join(str(r.exc_text) for r in caplog.records)
    for blob in (caplog.text, exc_texts, repr(cfg), str(cfg)):
        assert CANARY_KEY not in blob


def test_cs1_key_canario_no_aparece_en_repr_str_ni_logs(caplog):
    cfg = load_config({"GEMINI_API_KEY": CANARY_KEY})
    assert cfg.enabled is True and cfg.api_key == CANARY_KEY  # control positivo
    assert "GeminiConfig(" in repr(cfg)
    exc_texts = "".join(str(r.exc_text) for r in caplog.records)
    for blob in (caplog.text, exc_texts, repr(cfg), str(cfg), f"{cfg}", f"{cfg!r}"):
        assert CANARY_KEY not in blob


def test_cs1_key_invalida_pegada_con_basura_no_se_loguea(caplog):
    mala = f"{CANARY_KEY}\nCOSA=otra"
    cfg = load_config({"GEMINI_API_KEY": mala})
    assert cfg.enabled is False
    assert cfg.api_key == ""  # no se conserva una key inválida
    assert len(_records(caplog, logging.ERROR)) == 1
    assert CANARY_KEY not in caplog.text and CANARY_KEY not in repr(cfg)


KEYS_RECHAZADAS = [
    "AAAAAAAAAAAAAAAA\nB",
    "AAAAAAAAAAAAAAAA\rB",
    '"AAAAAAAAAAAAAAAA"',
    "AAAAAAAAAAAAAAAA'B",
    "AAAAAAAAAAAAAAAA`B",
    "AAAAAAAAAAAAAAAA\\B",
    "AAAAAAAA AAAAAAAA",
    "AAAAAAAAAAAAAAAA\x00B",
    "AAAAAAAAAAAAAAAAñ",
    "short",
    "A" * 15,
    "A" * 257,
]


@pytest.mark.parametrize("key", KEYS_RECHAZADAS)
def test_cs2_key_invalida_deshabilita_con_error_sin_valor(key, caplog):
    cfg = load_config({"GEMINI_API_KEY": key, "GEMINI_REWRITE_ENABLED": "true"})
    assert cfg.enabled is False
    assert cfg.rewrite_enabled is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1  # control positivo
    assert "GEMINI_KEY_INVALID" in errors[0].getMessage()
    assert key.strip() not in caplog.text


@pytest.mark.parametrize(
    "key",
    [DUMMY_KEY, "A" * 16, "A" * 256, "ab_cd-" * 6 + "abc",  # 39 con _ y -
     "AAAAAAAA.AAAAAAAAAA", DUMMY_KEY + "\n", "  " + DUMMY_KEY + " \r\n"],
)
def test_cs2_key_valida_pasa_tras_strip(key):
    cfg = load_config({"GEMINI_API_KEY": key})
    assert cfg.enabled is True
    assert cfg.api_key == key.strip()


def test_cs2_key_de_39_caracteres_con_guion_y_guion_bajo():
    key = "ab_cd-" * 6 + "abc"
    assert len(key) == 39
    assert load_config({"GEMINI_API_KEY": key}).api_key == key


# =============================================================================
# Contrato de request (ambas features)
# =============================================================================


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca9_post_a_url_fija_sin_query(feature):
    handler, seen = _ok_handler()
    result = await _call(handler, feature=feature)
    assert result.text == "hola"
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/{MODEL_DEFAULT}:generateContent"
    assert request.url.query == b""
    assert request.url.host == "generativelanguage.googleapis.com"


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca9_modelo_configurado_va_en_la_url(feature):
    handler, seen = _ok_handler()
    await _call(handler, feature=feature, config=_config(model="gemini-3.8-flash"))
    assert seen[0].url.path == "/v1beta/models/gemini-3.8-flash:generateContent"


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca10_key_en_header_y_en_ningun_otro_lado_exito(feature, caplog):
    handler, seen = _ok_handler(usage=usage_metadata(), model_version="gemini-3.5-flash-lite-001")
    await _call(handler, feature=feature, config=_config(api_key=CANARY_KEY))
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["x-goog-api-key"] == CANARY_KEY
    assert CANARY_KEY not in str(request.url)
    assert CANARY_KEY.encode() not in request.content
    assert "key=" not in str(request.url)
    assert len(caplog.records) >= 1  # control positivo: hay logs (incluidos los de httpx)
    assert any(r.name == "httpx" for r in caplog.records)
    assert CANARY_KEY not in caplog.text


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", FAILURE_TABLE, ids=lambda c: c.id)
async def test_ca10_key_y_cuerpo_de_respuesta_no_aparecen_en_logs_de_fallas(
    case: FailureCase, feature, caplog
):
    handler, seen = sequence_handler(case.steps)
    recorder = SleepRecorder()
    error = None
    try:
        await _call(
            handler, feature=feature, config=_config(api_key=CANARY_KEY), sleep=recorder
        )
    except GeminiError as exc:
        error = exc
    esperado = case.explain_token if feature == "explain" else case.rewrite_token
    if esperado is None:
        assert error is None  # fila de éxito (p. ej. 503 y luego 200)
    else:
        assert isinstance(error, GeminiError) and error.kind == esperado
    assert len(seen) >= 1
    assert seen[0].headers["x-goog-api-key"] == CANARY_KEY
    assert CANARY_KEY not in str(seen[0].url)
    assert CANARY_KEY.encode() not in seen[0].content
    exc_texts = "".join(str(r.exc_text) for r in caplog.records)
    assert len(caplog.records) >= 1
    for blob in (caplog.text, exc_texts):
        assert CANARY_KEY not in blob
        assert CANARY_BODY not in blob


def _forbidden_keys_in(obj) -> set[str]:
    prohibidas = {
        "temperature", "topP", "topK", "candidateCount", "thinkingBudget", "tools",
        "safetySettings", "responseSchema", "responseJsonSchema",
    }
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in prohibidas:
                found.add(k)
            found |= _forbidden_keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _forbidden_keys_in(v)
    return found


async def test_ca11_body_de_explicaciones_exacto():
    handler, seen = _ok_handler()
    await _call(
        handler, feature="explain", max_tokens=1024, system_prompt="SYS", user='{"pregunta":"q"}'
    )
    body = request_body(seen[0])
    assert set(body) == {"systemInstruction", "contents", "generationConfig"}
    assert generation_config(body) == {
        "maxOutputTokens": 1024,
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }
    assert _forbidden_keys_in(body) == set()
    assert system_text(body) == "SYS"
    assert user_text(body) == '{"pregunta":"q"}'
    assert len(body["contents"]) == 1
    assert "role" not in body["contents"][0]
    assert body["systemInstruction"] == {"parts": [{"text": "SYS"}]}


async def test_ca11_el_detector_de_campos_prohibidos_detecta(  # control positivo (CQ-8)
):
    assert _forbidden_keys_in({"generationConfig": {"temperature": 0.2}}) == {"temperature"}
    assert _forbidden_keys_in({"a": [{"thinkingBudget": 0}]}) == {"thinkingBudget"}


async def test_ca12_body_del_rewrite_exacto():
    handler, seen = _ok_handler('{"0": "x"}')
    await _call(
        handler,
        feature="rewrite",
        max_tokens=2048,
        mime="application/json",
        system_prompt=ai_rewrite.SYSTEM_PROMPT,
        timeout=8.0,
        user='{"0": "seccion"}',
    )
    body = request_body(seen[0])
    assert set(body) == {"systemInstruction", "contents", "generationConfig"}
    assert generation_config(body) == {
        "maxOutputTokens": 2048,
        "responseMimeType": "application/json",
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }
    assert _forbidden_keys_in(body) == set()
    assert system_text(body) == ai_rewrite.SYSTEM_PROMPT
    assert len(body["contents"]) == 1
    assert "role" not in body["contents"][0]


async def test_ca13_texto_concatena_partes_sin_thought_y_hace_strip():
    handler, seen = _ok_handler(
        " Hola ",
        thought_parts=("RAZONAMIENTO-INTERNO",),
        extra_parts=[{"text": "mundo\n"}, {"text": "pensando", "thought": True},
                     {"text": "!", "thought": False}],
    )
    result = await _call(handler)
    assert len(seen) == 1
    assert result.text == "Hola mundo\n!".strip()
    assert "RAZONAMIENTO-INTERNO" not in result.text
    assert "pensando" not in result.text
    assert result.finish_reason == "STOP"


async def test_ca13_partes_no_dict_o_sin_texto_se_ignoran():
    handler, _ = _ok_handler(
        "ok", extra_parts=[{"inlineData": {}}, {"text": 5}, "suelto"]  # type: ignore[list-item]
    )
    assert (await _call(handler)).text == "ok"


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca14_variables_de_url_en_el_entorno_no_cambian_la_url(feature, monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", "https://evil.example/v1")
    monkeypatch.setenv("GEMINI_API_URL", "https://evil.example/v1")
    assert load_config(_env()).enabled is True
    handler, seen = _ok_handler()
    await _call(handler, feature=feature)
    assert len(seen) == 1
    assert str(seen[0].url) == f"{BASE_URL}/{MODEL_DEFAULT}:generateContent"
    assert "evil.example" not in str(seen[0].url)


async def test_ca15_generate_text_nunca_crea_su_propio_cliente(monkeypatch):
    creados: list[tuple] = []
    real = httpx.AsyncClient

    class Espia(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            creados.append((a, k))
            super().__init__(*a, **k)

    handler, seen = _ok_handler()
    # El cliente del llamador se crea ANTES de espiar el constructor.
    async with real(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(httpx, "AsyncClient", Espia)
        result = await generate_text(
            client, _config(), system_prompt="s", user_text="u", max_output_tokens=10,
            timeout_seconds=5.0, max_retries=0, feature="explain",
        )
        with pytest.raises(GeminiError):
            fallo, _ = sequence_handler((("status", 500),))
            async with real(transport=httpx.MockTransport(fallo)) as c2:
                await generate_text(
                    c2, _config(), system_prompt="s", user_text="u", max_output_tokens=10,
                    timeout_seconds=5.0, max_retries=0, feature="explain",
                )
    assert result.text == "hola" and len(seen) == 1  # control positivo
    assert creados == []
    tree = ast.parse(CLIENT_SRC.read_text(encoding="utf-8"))
    llamadas = [
        ast.unparse(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and "AsyncClient" in ast.unparse(n.func)
    ]
    assert llamadas == []


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca16_thinking_level_configurado_llega_a_thinking_config(feature):
    handler, seen = _ok_handler()
    await _call(handler, feature=feature, config=_config(thinking_level="low"))
    body = request_body(seen[0])
    assert generation_config(body)["thinkingConfig"] == {"thinkingLevel": "low"}
    assert load_config(_env(GEMINI_THINKING_LEVEL="low")).thinking_level == "low"


# =============================================================================
# Errores: tabla única de fallas
# =============================================================================


def _rows(ca: str):
    return [pytest.param(c, id=c.id) for c in FAILURE_TABLE if ca in c.ca]


async def _run_case(case: FailureCase, feature: str):
    """Ejecuta una fila; devuelve `(resultado_o_None, error_o_None, requests, sleeps)`."""
    handler, seen = sequence_handler(case.steps)
    recorder = SleepRecorder()
    result = err = None
    try:
        result = await _call(handler, feature=feature, sleep=recorder)
    except GeminiError as exc:
        err = exc
    return result, err, seen, recorder.calls


def _expected(case: FailureCase, feature: str):
    if feature == "explain":
        return case.explain_token, case.explain_calls, list(case.explain_sleeps)
    return case.rewrite_token, case.rewrite_calls, list(case.rewrite_sleeps)


async def _assert_case(case: FailureCase, feature: str):
    result, err, seen, sleeps = await _run_case(case, feature)
    token, calls, expected_sleeps = _expected(case, feature)
    assert len(seen) == calls
    assert sleeps == expected_sleeps
    if token is None:
        assert err is None
        assert result is not None and result.text == "segundo intento ok"
    else:
        assert result is None
        assert err is not None and err.kind == token
    return result, err


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CA-17"))
async def test_ca17_402_una_llamada_sin_sleep_y_error_billing(case, feature, caplog):
    await _assert_case(case, feature)
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "GEMINI_BILLING_402" in errors[0].getMessage()


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CA-18"))
async def test_ca18_status_deterministicos_una_llamada_sin_sleep(case, feature, caplog):
    await _assert_case(case, feature)
    mensajes = [r.getMessage() for r in _records(caplog)]
    assert len(mensajes) == 1
    assert case.explain_token in mensajes[0]
    if case.id == "404":
        assert f"model={MODEL_DEFAULT}" in mensajes[0]  # GEMINI_MODEL_NOT_FOUND con id


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CA-19"))
async def test_ca19_500_503_reintento_solo_en_explicaciones(case, feature):
    result, err = await _assert_case(case, feature)
    if feature == "explain":
        # secuencia de 2 llamadas con exactamente un sleep(1.0)
        assert len(case.steps) == 2
    else:
        assert result is None and err is not None  # rewrite: su primera falla es definitiva


async def test_ca19_sleep_default_resuelve_el_parche_de_modulo_al_llamar(gemini_sleeps):
    handler, seen = sequence_handler((("status", 503), ("body", gemini_response("ok"))))
    result = await _call(handler, feature="explain")  # sleep=None -> gemini_client._sleep
    assert result.text == "ok"
    assert len(seen) == 2
    assert gemini_sleeps.calls == [1.0]


async def test_ca19_no_se_duerme_tras_el_ultimo_intento_fallido():
    handler, seen = sequence_handler((("status", 503), ("status", 503), ("status", 503)))
    recorder = SleepRecorder()
    with pytest.raises(GeminiError) as info:
        await _call(handler, feature="explain", retries=2, sleep=recorder)
    assert info.value.kind == "GEMINI_SERVER_ERROR"
    assert len(seen) == 3
    assert recorder.calls == [1.0, 1.0]  # dos reintentos, dos sleeps, ninguno tras el último


async def test_ca19_max_retries_cero_en_explicaciones_no_reintenta():
    handler, seen = sequence_handler((("status", 500), ("body", gemini_response("ok"))))
    recorder = SleepRecorder()
    with pytest.raises(GeminiError):
        await _call(handler, feature="explain", retries=0, sleep=recorder)
    assert len(seen) == 1 and recorder.calls == []


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CA-20"))
async def test_ca20_excepciones_y_status_inesperados_sin_reintento(case, feature, caplog):
    await _assert_case(case, feature)
    assert len(seen_tokens(caplog)) == 1


def seen_tokens(caplog):
    """Registros del logger del cliente con un token `GEMINI_*` en el mensaje."""
    return [r for r in _records(caplog) if TOKEN_RE.search(r.getMessage())]


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CA-21"))
async def test_ca21_200_inutilizable_falla_sin_reintento(case, feature):
    await _assert_case(case, feature)


@pytest.mark.parametrize(
    "finish,en_log",
    [("SAFETY", True), ("MAX_TOKENS", True), ("weird\nvalue", False), ("STOP\n", False),
     ("lower_case", False), ("A" * 33, False)],
)
async def test_ca21_finish_reason_solo_se_loguea_si_cumple_el_patron(finish, en_log, caplog):
    handler, _ = sequence_handler((("body", gemini_response("x", finish_reason=finish)),))
    with pytest.raises(GeminiError):
        await _call(handler, feature="rewrite")
    mensajes = [r.getMessage() for r in seen_tokens(caplog)]
    assert len(mensajes) == 1  # control positivo
    assert (f"finishReason={finish}" in mensajes[0]) is en_log
    if not en_log:
        assert "finishReason" not in mensajes[0]
        assert finish not in caplog.text


async def test_ca21_candidato_no_dict_es_malformed():
    handler, _ = sequence_handler((("body", {"candidates": ["no soy dict"]}),))
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_MALFORMED"


async def test_ca21_stop_sin_content_es_malformed():
    handler, _ = sequence_handler((("body", {"candidates": [{"finishReason": "STOP"}]}),))
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_MALFORMED"


async def test_ca21_finish_ausente_no_es_exito():
    handler, _ = sequence_handler(
        (("body", gemini_response("texto", finish_reason=None)),)
    )
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_BLOCKED"


async def test_ca21_el_bloqueo_del_prompt_gana_aunque_haya_candidatos():
    body = gemini_response("texto util", prompt_feedback={"blockReason": "OTHER"})
    handler, _ = sequence_handler((("body", body),))
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_BLOCKED"


def _success_lines(caplog):
    return [r.getMessage() for r in _records(caplog, logging.INFO) if r.getMessage().startswith("Gemini OK")]


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca24_exito_emite_info_con_el_formato_exacto(feature, caplog):
    handler, _ = _ok_handler(
        usage=usage_metadata(prompt=1500, output=300, thoughts=42),
        model_version="gemini-3.5-flash-lite-001",
    )
    result = await _call(handler, feature=feature)
    lineas = _success_lines(caplog)
    assert len(lineas) == 1
    linea = lineas[0]
    assert SUCCESS_RE.fullmatch(linea)
    assert f"feature={feature} " in linea
    assert f"model={MODEL_DEFAULT} " in linea
    assert "served=gemini-3.5-flash-lite-001 " in linea
    assert "promptTokens=1500 outputTokens=300 thoughtsTokens=42 finishReason=STOP" in linea
    assert result.served_model == "gemini-3.5-flash-lite-001"
    assert result.usage["promptTokenCount"] == 1500
    assert result.finish_reason == "STOP"
    # no se emite ningún token de falla en el éxito
    assert seen_tokens(caplog) == []


async def test_ca24_sobre_minimo_sin_usage_ni_model_version_da_na(caplog):
    handler, _ = sequence_handler((("body", {"candidates": [{
        "finishReason": "STOP", "content": {"parts": [{"text": "x"}]}}]}),))
    result = await _call(handler)
    assert result.text == "x" and result.served_model is None and result.usage == {}
    linea = _success_lines(caplog)[0]
    assert SUCCESS_RE.fullmatch(linea)
    assert "served=n/a promptTokens=n/a outputTokens=n/a thoughtsTokens=n/a" in linea


@pytest.mark.parametrize("malo", [-1, 10**9 + 1, "5", True, 3.5, None, [1]])
async def test_ca24_contadores_invalidos_dan_na(malo, caplog):
    usage = {"promptTokenCount": malo, "candidatesTokenCount": malo, "thoughtsTokenCount": malo}
    handler, _ = _ok_handler(usage=usage)
    await _call(handler)
    linea = _success_lines(caplog)[0]
    assert "promptTokens=n/a outputTokens=n/a thoughtsTokens=n/a" in linea


@pytest.mark.parametrize("valor", [0, 10**9])
async def test_ca24_contadores_en_los_bordes_validos_se_muestran(valor, caplog):
    usage = {"promptTokenCount": valor, "candidatesTokenCount": valor, "thoughtsTokenCount": valor}
    handler, _ = _ok_handler(usage=usage)
    await _call(handler)
    linea = _success_lines(caplog)[0]
    assert f"promptTokens={valor} outputTokens={valor} thoughtsTokens={valor}" in linea


@pytest.mark.parametrize("served", ["has space", "x\n", "a" * 65, "", 5, "ñandú", "a/b"])
async def test_ca24_served_invalido_da_na(served, caplog):
    body = gemini_response("x")
    body["modelVersion"] = served
    handler, _ = sequence_handler((("body", body),))
    result = await _call(handler)
    assert "served=n/a " in _success_lines(caplog)[0]
    assert result.served_model is None


async def test_ca24_usage_metadata_no_dict_se_ignora(caplog):
    body = gemini_response("x")
    body["usageMetadata"] = "no soy dict"
    handler, _ = sequence_handler((("body", body),))
    result = await _call(handler)
    assert result.usage == {}
    assert "promptTokens=n/a" in _success_lines(caplog)[0]


# --- CA-66: GeminiError siempre `from None` --------------------------------------


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", [pytest.param(c, id=c.id) for c in FAILURE_TABLE])
async def test_ca66_gemini_error_se_lanza_from_none(case, feature):
    _, err, seen, _ = await _run_case(case, feature)
    token, _, _ = _expected(case, feature)
    if token is None:
        assert err is None
        return
    assert err is not None and len(seen) >= 1
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
    assert err.__context__ is None


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca66_traceback_y_exc_info_del_llamador_no_filtran_la_key(feature, caplog):
    handler, _ = sequence_handler((("raise", "protocol"),))
    with pytest.raises(GeminiError) as info:
        await _call(handler, feature=feature, config=_config(api_key=CANARY_KEY))
    assert info.value.kind == "GEMINI_NETWORK"
    texto = "".join(traceback.format_exception(info.value))
    assert "GeminiError" in texto  # control positivo
    assert CANARY_KEY not in texto
    # Un llamador que loguea con exc_info=True tampoco la filtra.
    try:
        raise info.value
    except GeminiError:
        logging.getLogger("test_llamador").error("fallo del llamador", exc_info=True)
    assert "Traceback" in caplog.text and "GeminiError" in caplog.text  # control positivo
    assert CANARY_KEY not in caplog.text
    exc_texts = "".join(str(r.exc_text) for r in caplog.records)
    assert CANARY_KEY not in exc_texts


# --- CS-3: captura Exception, logs sin str(exc) -----------------------------------


@pytest.mark.parametrize("feature", FEATURES)
async def test_cs3_local_protocol_error_con_key_da_network_sin_fuga(feature, caplog):
    handler, seen = sequence_handler((("raise", "protocol"),))
    with pytest.raises(GeminiError) as info:
        await _call(handler, feature=feature, config=_config(api_key=CANARY_KEY))
    assert info.value.kind == "GEMINI_NETWORK"
    assert len(seen) == 1
    tokens = seen_tokens(caplog)
    assert len(tokens) == 1
    assert "error=LocalProtocolError" in tokens[0].getMessage()  # solo el tipo
    exc_texts = "".join(str(r.exc_text) for r in caplog.records)
    assert CANARY_KEY not in caplog.text
    assert CANARY_KEY not in exc_texts


async def test_cs3_excepcion_arbitraria_no_httpx_tambien_se_captura(caplog):
    handler, _ = sequence_handler((("raise", "generic"),))
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_NETWORK"
    assert "error=RuntimeError" in seen_tokens(caplog)[0].getMessage()
    assert CANARY_KEY not in caplog.text


def test_cs3_el_modulo_no_usa_exc_info_ni_str_de_excepcion_ni_logger_debug():
    fuente = CLIENT_SRC.read_text(encoding="utf-8")
    prohibido = re.compile(r"exc_info|logger\.exception|str\(exc|repr\(exc|\{exc|, exc\)")
    hits = [(n, l) for n, l in enumerate(fuente.splitlines(), 1) if prohibido.search(l)]
    assert hits == []
    assert "logger.debug" not in fuente
    # control positivo: el patrón sí detecta lo que debe detectar
    assert prohibido.search("logger.warning('x', exc_info=True)")
    assert prohibido.search("f'{exc}' + str(exc)")


async def test_cs3_asyncio_cancelled_error_se_propaga():
    import asyncio

    async def cancelado(request):  # MockTransport admite handlers async
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(transport=httpx.MockTransport(cancelado)) as client:
        with pytest.raises(asyncio.CancelledError):
            await generate_text(
                client, _config(), system_prompt="s", user_text="u", max_output_tokens=10,
                timeout_seconds=5.0, max_retries=1, feature="explain",
            )


# --- CS-18: sin redirects ----------------------------------------------------------


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", _rows("CS-18"))
async def test_cs18_302_con_cliente_follow_redirects_true_una_sola_request(case, feature):
    handler, seen = sequence_handler(case.steps)
    recorder = SleepRecorder()
    with pytest.raises(GeminiError) as info:
        await _call(
            handler, feature=feature, sleep=recorder, client_kwargs={"follow_redirects": True}
        )
    assert info.value.kind == "GEMINI_UNEXPECTED_STATUS"
    assert len(seen) == 1
    assert seen[0].url.host == "generativelanguage.googleapis.com"
    assert recorder.calls == []


def test_cs18_el_modulo_no_activa_redirects_ni_desactiva_verificacion():
    fuente = CLIENT_SRC.read_text(encoding="utf-8")
    patron = re.compile(r"follow_redirects\s*=\s*True|verify\s*=\s*False")
    assert patron.search(fuente) is None
    assert patron.search("client.post(follow_redirects=True)")  # control positivo
    assert "follow_redirects=False" in fuente


# --- CS-19(b): cuerpo 200 con anidado profundo ---------------------------------------


def test_cs19_control_el_anidado_de_100000_niveles_si_lanza_recursion_error():
    # Guarda contra un falso verde: el caso de la spec debe reproducir el defecto.
    with pytest.raises(RecursionError):
        json.loads("[" * 100000 + "]" * 100000)


@pytest.mark.parametrize("feature", FEATURES)
async def test_cs19_sobre_anidado_100000_niveles_da_malformed_sin_recursion_error(feature):
    handler, seen = sequence_handler(
        (("raw", ("[" * 100000 + "]" * 100000).encode()),)
    )
    with pytest.raises(GeminiError) as info:
        await _call(handler, feature=feature)
    assert info.value.kind == "GEMINI_MALFORMED"
    assert len(seen) == 1


async def test_cs19_control_5000_sin_cerrar_cae_a_json_decode_error():
    with pytest.raises(json.JSONDecodeError):
        json.loads("[" * 5000)
    handler, _ = sequence_handler((("raw", ("[" * 5000).encode()),))
    with pytest.raises(GeminiError) as info:
        await _call(handler)
    assert info.value.kind == "GEMINI_MALFORMED"


# --- CA-68: contrato exacto de la llamada al cliente ------------------------------------


class _EspiaCliente:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return httpx.Response(
            200,
            json=gemini_response("ok"),
            request=httpx.Request("POST", args[0]),
        )


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca68_el_cliente_recibe_exactamente_la_llamada_del_contrato(feature):
    espia = _EspiaCliente()
    await generate_text(
        espia,  # type: ignore[arg-type]
        _config(),
        system_prompt="s",
        user_text="u",
        max_output_tokens=7,
        timeout_seconds=3.5,
        max_retries=0,
        feature=feature,
    )
    assert len(espia.calls) == 1
    args, kwargs = espia.calls[0]
    assert args == (f"{BASE_URL}/{MODEL_DEFAULT}:generateContent",)  # solo url posicional
    assert set(kwargs) == {"headers", "json", "timeout", "follow_redirects"}
    assert kwargs["headers"] == {"x-goog-api-key": DUMMY_KEY}
    assert isinstance(kwargs["json"], dict)
    assert kwargs["timeout"] == httpx.Timeout(3.5)
    assert kwargs["follow_redirects"] is False


@pytest.mark.parametrize("feature", FEATURES)
async def test_ca68_timeout_llega_a_connect_read_write_y_pool(feature):
    handler, seen = _ok_handler()
    await _call(handler, feature=feature, timeout=7.5)
    assert seen[0].extensions["timeout"] == {
        "connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5,
    }


# --- CA-69: exactamente 1 registro con token por operación fallida ------------------------


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("case", [pytest.param(c, id=c.id) for c in FAILURE_TABLE])
async def test_ca69_una_falla_deja_exactamente_un_registro_con_token(case, feature, caplog):
    _, err, seen, _ = await _run_case(case, feature)
    token, calls, _ = _expected(case, feature)
    assert len(seen) == calls
    todos = [r for r in caplog.records if TOKEN_RE.search(r.getMessage())]
    cliente = seen_tokens(caplog)
    if token is None:
        assert todos == []  # éxito tras reintento: ningún token
        assert len(_success_lines(caplog)) == 1
    else:
        assert err is not None
        assert len(todos) == 1 and len(cliente) == 1  # ni httpx ni otros lo duplican
        mensaje = cliente[0].getMessage()
        assert token in mensaje  # el token del ÚLTIMO intento
        nivel = case.explain_level if feature == "explain" else case.rewrite_level
        assert cliente[0].levelname == nivel
        assert cliente[0].name == "investbot.gemini_client"
        assert _success_lines(caplog) == []
    reintentos = [r for r in _records(caplog, logging.INFO) if "reintento" in r.getMessage()]
    esperados = 1 if (feature == "explain" and calls == 2) else 0
    assert len(reintentos) == esperados
    for r in reintentos:
        assert TOKEN_RE.search(r.getMessage()) is None  # el reintento no lleva token


def test_ca69_la_tabla_cubre_los_status_y_excepciones_de_la_spec():
    ids = {c.id for c in FAILURE_TABLE}
    assert {"402", "400", "401", "403", "404", "429", "500_luego_200", "503_luego_200",
            "500_luego_400", "500_luego_timeout", "timeout", "connect_error", "read_error",
            "local_protocol_error", "302", "200_blockreason", "200_max_tokens",
            "200_cuerpo_no_json"} <= ids
    assert len(FAILURE_TABLE) == len(ids)


# --- Estructura del resultado ---------------------------------------------------------------


def test_gemini_error_expone_kind_y_su_texto_es_solo_el_token():
    err = GeminiError("GEMINI_EMPTY")
    assert err.kind == "GEMINI_EMPTY"
    assert str(err) == "GEMINI_EMPTY"


def test_gemini_config_es_inmutable_y_su_repr_omite_la_key():
    cfg = _config(api_key=CANARY_KEY)
    with pytest.raises(Exception):
        cfg.enabled = False  # type: ignore[misc]
    assert CANARY_KEY not in repr(cfg)
    assert "api_key" not in repr(cfg)


async def test_ca19_el_sleep_real_del_modulo_delega_en_asyncio_sleep(monkeypatch):
    # La fixture autouse reemplazó el `_sleep` del módulo; el real se ejercita
    # con `asyncio.sleep` parcheado para que la suite no duerma nunca.
    assert gemini_client._sleep is not _REAL_SLEEP  # control: el parche está activo
    registrado: list[float] = []

    async def falso(segundos):
        registrado.append(segundos)

    monkeypatch.setattr(gemini_client.asyncio, "sleep", falso)
    await _REAL_SLEEP(0.25)
    assert registrado == [0.25]


def test_log_no_client_emite_un_warning_con_token_y_sin_datos(caplog):
    gemini_client.log_no_client("explain")
    registros = _records(caplog)
    assert len(registros) == 1
    assert registros[0].levelno == logging.WARNING
    assert registros[0].getMessage() == "GEMINI_NO_CLIENT feature=explain"
