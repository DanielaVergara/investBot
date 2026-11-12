"""Cliente REST mínimo de Gemini sobre httpx (SDD_migracion_gemini.md).

Compartido por las dos features (explicaciones y reescritura): configuración,
request, clasificación de errores, parseo y log de éxito. Sin SDK, sin
dependencias nuevas, sin capa multi-proveedor. NO crea clientes httpx: el
llamador pasa el suyo (`generate_text(client, ...)`).

Seguridad (secciones 3.3, 3.10, 12 y 13 de la spec):
- La key viaja SOLO en el header `x-goog-api-key`; la URL es una constante y
  no lleva query string.
- Ningún log lleva la key, el valor de ninguna variable rechazada, el cuerpo
  de una respuesta HTTP, el texto de una excepción ni un traceback: solo
  tokens fijos, el nombre del tipo de la excepción y el status. Por eso este
  módulo no emite logs de nivel debug ni adjunta información de excepción.
- Todo fallo se levanta con `raise GeminiError(kind) from None`: sin
  `__cause__`, porque la excepción original de httpx puede contener el valor
  de la key (p. ej. `Illegal header value`).
- Toda validación por regex usa `re.fullmatch` (nunca `$`, que acepta un
  `\\n` final).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

ENV_API_KEY = "GEMINI_API_KEY"
ENV_MODEL = "GEMINI_MODEL"
ENV_THINKING_LEVEL = "GEMINI_THINKING_LEVEL"
ENV_TIMEOUT = "GEMINI_TIMEOUT_SECONDS"
ENV_REWRITE_ENABLED = "GEMINI_REWRITE_ENABLED"
ENV_REWRITE_TIMEOUT = "GEMINI_REWRITE_TIMEOUT_SECONDS"

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_THINKING_LEVEL = "minimal"
DEFAULT_TIMEOUT_SECONDS = 12.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_REWRITE_TIMEOUT_SECONDS = 8.0
MAX_REWRITE_TIMEOUT_SECONDS = 30.0

THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
_TRUTHY_VALUES = frozenset({"true", "1", "yes"})
RETRY_SLEEP_SECONDS = 1.0

_KEY_RE = re.compile(r"[\x21-\x7E]{16,256}", re.ASCII)
_KEY_FORBIDDEN_CHARS = frozenset("\"'`\\")
# Major >= 3 (se rechazan ceros a la izquierda); se valida además el largo.
_MODEL_RE = re.compile(
    r"gemini-([1-9]\d?)(?:\.(0|[1-9]\d?))?-[a-z0-9]+(?:[.-][a-z0-9]+)*", re.ASCII
)
_MODEL_MAX_LEN = 64
_SERVED_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}", re.ASCII)
_FINISH_REASON_RE = re.compile(r"[A-Z_]{1,32}", re.ASCII)
_MAX_COUNTER = 10**9

# Tokens fijos de falla (3.10 + 13.2 E-4 + 15.2 P-5).
_STATUS_TOKENS = {
    400: "GEMINI_BAD_REQUEST",
    401: "GEMINI_AUTH",
    402: "GEMINI_BILLING_402",
    403: "GEMINI_AUTH",
    404: "GEMINI_MODEL_NOT_FOUND",
    429: "GEMINI_RATE_LIMIT",
}
_RETRYABLE_STATUSES = frozenset({500, 503})
_ERROR_LEVEL_TOKENS = frozenset(
    {
        "GEMINI_BAD_REQUEST",
        "GEMINI_AUTH",
        "GEMINI_BILLING_402",
        "GEMINI_MODEL_NOT_FOUND",
    }
)


@dataclass(frozen=True)
class GeminiConfig:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    thinking_level: str = DEFAULT_THINKING_LEVEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    rewrite_enabled: bool = False
    rewrite_timeout_seconds: float = DEFAULT_REWRITE_TIMEOUT_SECONDS


class GeminiError(Exception):
    """Falla de Gemini; `kind` es un token fijo `GEMINI_*` (sin datos)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class GeminiResult:
    text: str
    served_model: Optional[str]
    finish_reason: str
    usage: Mapping[str, Any]


# --- Configuración ------------------------------------------------------------


def _env_value(env: Mapping[str, str], name: str) -> Optional[str]:
    """Valor `strip()`-eado; en blanco equivale a no seteado (None)."""
    raw = env.get(name)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _valid_key(key: str) -> bool:
    return _KEY_RE.fullmatch(key) is not None and not (_KEY_FORBIDDEN_CHARS & set(key))


def _valid_model(model: str) -> bool:
    match = _MODEL_RE.fullmatch(model)
    return match is not None and len(model) <= _MODEL_MAX_LEN and int(match.group(1)) >= 3


def _timeout_from_env(
    env: Mapping[str, str], name: str, default: float, maximum: float
) -> float:
    raw = _env_value(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    # `not (0 < v <= max)` también descarta nan e inf.
    if not (0 < value <= maximum):
        logger.warning("%s inválida: se usa el default %s", name, default)
        return default
    return value


def _load(env: Mapping[str, str]) -> GeminiConfig:
    rewrite_flag = (_env_value(env, ENV_REWRITE_ENABLED) or "").lower() in _TRUTHY_VALUES
    key = _env_value(env, ENV_API_KEY)
    if key is None:
        logger.info("Gemini deshabilitado (GEMINI_API_KEY ausente)")
        if rewrite_flag:
            logger.info("GEMINI_REWRITE_ENABLED ignorado: Gemini deshabilitado")
        return GeminiConfig()

    valid = True
    if not _valid_key(key):
        logger.error(
            "Gemini deshabilitado (GEMINI_KEY_INVALID: formato de %s inválido)", ENV_API_KEY
        )
        valid = False

    model = DEFAULT_MODEL
    raw_model = _env_value(env, ENV_MODEL)
    if raw_model is not None:
        if _valid_model(raw_model):
            model = raw_model
        else:
            logger.error("Gemini deshabilitado (variable inválida: %s)", ENV_MODEL)
            valid = False

    level = DEFAULT_THINKING_LEVEL
    raw_level = _env_value(env, ENV_THINKING_LEVEL)
    if raw_level is not None:
        if raw_level.lower() in THINKING_LEVELS:
            level = raw_level.lower()
        else:
            logger.error("Gemini deshabilitado (variable inválida: %s)", ENV_THINKING_LEVEL)
            valid = False

    timeout = _timeout_from_env(env, ENV_TIMEOUT, DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS)
    rewrite_timeout = _timeout_from_env(
        env, ENV_REWRITE_TIMEOUT, DEFAULT_REWRITE_TIMEOUT_SECONDS, MAX_REWRITE_TIMEOUT_SECONDS
    )

    if not valid:
        if rewrite_flag:
            logger.info("GEMINI_REWRITE_ENABLED ignorado: Gemini deshabilitado")
        return GeminiConfig(
            thinking_level=level, timeout_seconds=timeout, rewrite_timeout_seconds=rewrite_timeout
        )

    logger.info("Gemini habilitado model=%s rewrite=%s", model, "on" if rewrite_flag else "off")
    return GeminiConfig(
        enabled=True,
        api_key=key,
        model=model,
        thinking_level=level,
        timeout_seconds=timeout,
        rewrite_enabled=rewrite_flag,
        rewrite_timeout_seconds=rewrite_timeout,
    )


def load_config(env: Optional[Mapping[str, str]] = None) -> GeminiConfig:
    """Carga la config desde el entorno. NUNCA lanza.

    Config inválida (key, modelo, nivel) = feature deshabilitada con un ERROR
    que nombra solo la variable, jamás el valor recibido.
    """
    try:
        return _load(os.environ if env is None else env)
    except Exception as exc:  # defensivo: una feature opcional no aborta el arranque
        logger.error("Gemini deshabilitado (error al cargar la config: %s)", type(exc).__name__)
        return GeminiConfig()


# --- Filtro de salida (CS-5, 13.2 E-7 y 15.2 P-7) -------------------------
#
# El texto del modelo es entrada no confiable: Telegram convierte en enlaces
# las URL y las @menciones aun sin `parse_mode`. Compartido por las dos
# features (explicaciones y rewrite). Se rechaza si contiene una URL, enlace o
# mención, un carácter de control o un invisible/bidi. `\n`, `\t` y `\r` se
# toleran; U+200D (unión de emoji) se rechaza A PROPÓSITO (un invisible puede
# partir un ticker y esquivar `_protected_tokens`; el costo de un falso
# positivo es el fallback); U+FE0F (como en "⚠️") no está en la lista y pasa.
_OUTPUT_FILTER_RE = re.compile(
    r"https?://"
    r"|(?<![A-Za-z0-9])www\."
    r"|(?<![A-Za-z0-9])t\.me/"
    r"|tg://"
    r"|@\w"
    r"|\]\("
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]",
    re.IGNORECASE,
)


def violates_output_filter(text: str) -> bool:
    return _OUTPUT_FILTER_RE.search(text) is not None


# --- Cliente ---------------------------------------------------------------------


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _counter(value: Any) -> str:
    # `bool` es subclase de `int`: se excluye con `type(...) is int`.
    if type(value) is int and 0 <= value <= _MAX_COUNTER:
        return str(value)
    return "n/a"


def _parse_success(data: Any) -> tuple[Optional[str], dict[str, Any]]:
    """Devuelve `(token_de_falla | None, info)`; `info` trae el texto o el finish."""
    if not isinstance(data, dict):
        return "GEMINI_MALFORMED", {}
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and "blockReason" in feedback:
        return "GEMINI_BLOCKED", {}
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "GEMINI_EMPTY", {}
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return "GEMINI_MALFORMED", {}
    finish = candidate.get("finishReason")
    if finish != "STOP":
        info = {"finish": finish if isinstance(finish, str) else None}
        return ("GEMINI_MAX_TOKENS" if finish == "MAX_TOKENS" else "GEMINI_BLOCKED"), info
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return "GEMINI_MALFORMED", {}
    texts = [
        p["text"]
        for p in parts
        if isinstance(p, dict) and p.get("thought") is not True and isinstance(p.get("text"), str)
    ]
    text = "".join(texts).strip()
    if not text:
        return "GEMINI_EMPTY", {}
    return None, {"text": text}


async def _attempt(
    client: httpx.AsyncClient,
    config: GeminiConfig,
    url: str,
    body: dict[str, Any],
    timeout_seconds: float,
) -> tuple[Optional[str], dict[str, Any]]:
    """Un intento HTTP. Devuelve `(token | None, info)`; no loguea."""
    try:
        response = await client.post(
            url,
            headers={"x-goog-api-key": config.api_key},
            json=body,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        return "GEMINI_TIMEOUT", {"error": type(exc).__name__}
    except Exception as exc:  # no solo httpx.HTTPError (CS-3)
        return "GEMINI_NETWORK", {"error": type(exc).__name__}

    status = response.status_code
    if status == 200:
        try:
            data = response.json()
        except Exception:  # ValueError, RecursionError (CS-19)
            return "GEMINI_MALFORMED", {"status": status}
        token, info = _parse_success(data)
        if token is None:
            info["data"] = data
        return token, {**info, "status": status}
    if status in _RETRYABLE_STATUSES:
        return "GEMINI_SERVER_ERROR", {"status": status}
    return _STATUS_TOKENS.get(status, "GEMINI_UNEXPECTED_STATUS"), {"status": status}


def _log_failure(token: str, feature: str, model: str, info: dict[str, Any]) -> None:
    parts = [token, f"feature={feature}", f"model={model}"]
    if info.get("status") is not None:
        parts.append(f"status={info['status']}")
    if info.get("error"):
        parts.append(f"error={info['error']}")
    finish = info.get("finish")
    if isinstance(finish, str) and _FINISH_REASON_RE.fullmatch(finish):
        parts.append(f"finishReason={finish}")
    level = logging.ERROR if token in _ERROR_LEVEL_TOKENS else logging.WARNING
    logger.log(level, " ".join(parts))


def log_no_client(feature: str) -> None:
    """WARNING `GEMINI_NO_CLIENT`: la feature está habilitada pero el
    llamador no tiene `gemini_http` (error de cableado; no se llama a Google
    ni se crea un cliente)."""
    logger.warning("GEMINI_NO_CLIENT feature=%s", feature)


def _log_success(feature: str, model: str, data: dict[str, Any], started: float) -> Optional[str]:
    usage = data.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}
    served = data.get("modelVersion")
    served_ok = served if isinstance(served, str) and _SERVED_RE.fullmatch(served) else None
    logger.info(
        "Gemini OK feature=%s model=%s served=%s promptTokens=%s outputTokens=%s "
        "thoughtsTokens=%s finishReason=STOP ms=%d",
        feature,
        model,
        served_ok or "n/a",
        _counter(usage.get("promptTokenCount")),
        _counter(usage.get("candidatesTokenCount")),
        _counter(usage.get("thoughtsTokenCount")),
        int((time.monotonic() - started) * 1000),
    )
    return served_ok


async def generate_text(
    client: httpx.AsyncClient,
    config: GeminiConfig,
    *,
    system_prompt: str,
    user_text: str,
    max_output_tokens: int,
    timeout_seconds: float,
    max_retries: int,
    feature: str,
    response_mime_type: Optional[str] = None,
    sleep: Any = None,
) -> GeminiResult:
    """Una llamada a `generateContent`; lanza `GeminiError(kind) from None`.

    `max_retries` reintentos como máximo, solo ante 500/503 y tras
    `sleep(1.0)` (no se duerme después del último intento fallido). No recibe
    identificadores de chat (CS-22).
    """
    sleeper = sleep or _sleep
    url = f"{GEMINI_BASE_URL}/{config.model}:generateContent"
    generation_config: dict[str, Any] = {"maxOutputTokens": max_output_tokens}
    if response_mime_type is not None:
        generation_config["responseMimeType"] = response_mime_type
    generation_config["thinkingConfig"] = {"thinkingLevel": config.thinking_level}
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": generation_config,
    }

    started = time.monotonic()
    attempt = 0
    while True:
        token, info = await _attempt(client, config, url, body, timeout_seconds)
        if token is None:
            break
        if token == "GEMINI_SERVER_ERROR" and attempt < max_retries:
            logger.info(
                "Gemini: reintento tras error de servidor feature=%s status=%s",
                feature,
                info.get("status"),
            )
            attempt += 1
            await sleeper(RETRY_SLEEP_SECONDS)
            continue
        _log_failure(token, feature, config.model, info)
        raise GeminiError(token) from None

    data = info["data"]
    served = _log_success(feature, config.model, data, started)
    usage = data.get("usageMetadata")
    return GeminiResult(
        text=info["text"],
        served_model=served,
        finish_reason="STOP",
        usage=usage if isinstance(usage, dict) else {},
    )
