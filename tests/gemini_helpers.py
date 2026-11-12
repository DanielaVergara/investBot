"""Helpers compartidos para los tests de Gemini (SDD_migracion_gemini.md).

Sin imports de `src`: solo stdlib y httpx, así se usa desde cualquier fase.
Contiene:

- constantes de test (key dummy, canarios),
- el constructor del sobre de respuesta de Gemini,
- accesores del cuerpo de request,
- la TABLA ÚNICA de fallas (CA-17 a CA-21, CA-31, CA-43; 15.3 y A13),
  compartida por el cliente, el rewrite y las explicaciones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

# --- Constantes de test -------------------------------------------------------

# Key dummy válida contra CS-2 (sin forma de key real; nunca `AIza...`).
DUMMY_KEY = "TEST-DUMMY-KEY-0123456789ABCDEF"
# Canario de key para pruebas de fuga: válido como key, reconocible, sin forma real.
CANARY_KEY = "CANARY-KEY-DO-NOT-LEAK-0000"
# Canario para el cuerpo de una respuesta HTTP de error.
CANARY_BODY = "CANARY-BODY-DO-NOT-LEAK-1111"

MODEL_DEFAULT = "gemini-3.5-flash-lite"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


# --- Sobre de respuesta -------------------------------------------------------


def gemini_response(
    text: Optional[str] = "respuesta de prueba",
    *,
    finish_reason: Optional[str] = "STOP",
    thought_parts: tuple[str, ...] = (),
    extra_parts: Optional[list[dict[str, Any]]] = None,
    usage: Optional[dict[str, Any]] = None,
    model_version: Optional[str] = None,
    prompt_feedback: Optional[dict[str, Any]] = None,
    candidates: Optional[Any] = None,
) -> dict[str, Any]:
    """Sobre `generateContent`: `candidates[0].content.parts[0].text`.

    `thought_parts` agrega partes `{"thought": true, "text": ...}` ANTES de la
    parte de texto. `usage=None` / `model_version=None` omiten el campo (sobre
    mínimo). `candidates` (si se pasa) reemplaza la lista completa.
    """
    parts: list[dict[str, Any]] = [{"text": t, "thought": True} for t in thought_parts]
    if text is not None:
        parts.append({"text": text})
    if extra_parts:
        parts.extend(extra_parts)
    cand: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
    if finish_reason is not None:
        cand["finishReason"] = finish_reason
    body: dict[str, Any] = {
        "candidates": candidates if candidates is not None else [cand]
    }
    if usage is not None:
        body["usageMetadata"] = usage
    if model_version is not None:
        body["modelVersion"] = model_version
    if prompt_feedback is not None:
        body["promptFeedback"] = prompt_feedback
    return body


def usage_metadata(prompt: int = 100, output: int = 20, thoughts: int = 0) -> dict[str, int]:
    return {
        "promptTokenCount": prompt,
        "candidatesTokenCount": output,
        "thoughtsTokenCount": thoughts,
        "totalTokenCount": prompt + output + thoughts,
    }


def ok_response(text: str = "respuesta de prueba", **kwargs: Any) -> httpx.Response:
    return httpx.Response(200, json=gemini_response(text, **kwargs))


# --- Accesores del cuerpo de request -------------------------------------------


def request_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def system_text(body: dict[str, Any]) -> str:
    return body["systemInstruction"]["parts"][0]["text"]


def user_text(body: dict[str, Any]) -> str:
    return body["contents"][0]["parts"][0]["text"]


def generation_config(body: dict[str, Any]) -> dict[str, Any]:
    return body["generationConfig"]


# --- Handlers de MockTransport ---------------------------------------------------

# Un paso de una secuencia de respuestas:
#   ("status", 503)                  -> respuesta HTTP con ese status y cuerpo con canario
#   ("body", {...})                  -> 200 con ese JSON
#   ("raw", b"...")                  -> 200 con esos bytes crudos
#   ("raise", "timeout"|"connect"|"read"|"protocol"|"generic")
Step = tuple[str, Any]


def _raise_for(kind: str, request: httpx.Request) -> None:
    if kind == "timeout":
        raise httpx.ReadTimeout("timeout simulado", request=request)
    if kind == "connect":
        raise httpx.ConnectError("conexion simulada", request=request)
    if kind == "read":
        raise httpx.ReadError("lectura simulada", request=request)
    if kind == "protocol":
        raise httpx.LocalProtocolError(f"Illegal header value {CANARY_KEY!r}")
    if kind == "generic":
        raise RuntimeError(f"fallo generico {CANARY_KEY!r}")
    raise AssertionError(f"kind de excepcion desconocido: {kind}")  # pragma: no cover


def respond_step(step: Step, request: httpx.Request) -> httpx.Response:
    what, value = step
    if what == "status":
        headers = {"location": "https://evil.example/"} if 300 <= value < 400 else None
        return httpx.Response(
            value, json={"error": {"message": CANARY_BODY}}, headers=headers
        )
    if what == "body":
        return httpx.Response(200, json=value)
    if what == "raw":
        return httpx.Response(200, content=value)
    if what == "raise":
        _raise_for(value, request)
    raise AssertionError(f"step desconocido: {what}")  # pragma: no cover


def sequence_handler(
    steps: tuple[Step, ...] | list[Step],
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    """Handler que responde la secuencia en orden (repite el último paso).

    Devuelve `(handler, requests)`; `requests` registra cada request recibida,
    así la cantidad de llamadas HTTP se afirma con `len(requests)`.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        idx = min(len(seen) - 1, len(steps) - 1)
        return respond_step(steps[idx], request)

    return handler, seen


class SleepRecorder:
    """`sleep` inyectable que nunca duerme y registra los valores recibidos."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --- Tabla única de fallas -----------------------------------------------------


@dataclass(frozen=True)
class FailureCase:
    """Una fila de la tabla única de fallas (CA-17 a 21, CA-31, CA-43; A13).

    `explain_*` aplica a las explicaciones (`max_retries=1`), `rewrite_*` al
    rewrite (`max_retries=0`). `*_token=None` significa éxito (la operación
    devuelve texto). El token es el del ÚLTIMO intento realizado.
    `*_level` es el nivel del único registro con token (ERROR o WARNING).
    """

    id: str
    steps: tuple[Step, ...]
    explain_token: Optional[str]
    rewrite_token: Optional[str]
    explain_calls: int
    rewrite_calls: int
    explain_sleeps: tuple[float, ...]
    rewrite_sleeps: tuple[float, ...]
    explain_level: Optional[str] = None
    rewrite_level: Optional[str] = None
    ca: tuple[str, ...] = field(default=())


def _single(
    id_: str,
    step: Step,
    token: str,
    level: str,
    ca: tuple[str, ...],
) -> FailureCase:
    """Falla de un solo intento: igual en ambas features, sin `sleep`."""
    return FailureCase(
        id_, (step,), token, token, 1, 1, (), (), level, level, ca
    )


_OK_AFTER_RETRY = ("body", gemini_response("segundo intento ok"))

FAILURE_TABLE: tuple[FailureCase, ...] = (
    # --- CA-17 / CA-18: status deterministicos o de cuenta, sin reintento
    _single("402", ("status", 402), "GEMINI_BILLING_402", "ERROR", ("CA-17",)),
    _single("400", ("status", 400), "GEMINI_BAD_REQUEST", "ERROR", ("CA-18",)),
    _single("401", ("status", 401), "GEMINI_AUTH", "ERROR", ("CA-18",)),
    _single("403", ("status", 403), "GEMINI_AUTH", "ERROR", ("CA-18",)),
    _single("404", ("status", 404), "GEMINI_MODEL_NOT_FOUND", "ERROR", ("CA-18",)),
    _single("429", ("status", 429), "GEMINI_RATE_LIMIT", "WARNING", ("CA-18",)),
    # --- CA-19: 500/503 con un reintento solo en explicaciones
    FailureCase(
        "500_luego_200", (("status", 500), _OK_AFTER_RETRY),
        None, "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (), None, "WARNING", ("CA-19",),
    ),
    FailureCase(
        "503_luego_200", (("status", 503), _OK_AFTER_RETRY),
        None, "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (), None, "WARNING", ("CA-19",),
    ),
    FailureCase(
        "500_luego_500", (("status", 500), ("status", 500)),
        "GEMINI_SERVER_ERROR", "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (),
        "WARNING", "WARNING", ("CA-19",),
    ),
    FailureCase(
        "503_luego_503", (("status", 503), ("status", 503)),
        "GEMINI_SERVER_ERROR", "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (),
        "WARNING", "WARNING", ("CA-19",),
    ),
    FailureCase(
        "500_luego_503", (("status", 500), ("status", 503)),
        "GEMINI_SERVER_ERROR", "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (),
        "WARNING", "WARNING", ("CA-19",),
    ),
    FailureCase(
        "500_luego_400", (("status", 500), ("status", 400)),
        "GEMINI_BAD_REQUEST", "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (),
        "ERROR", "WARNING", ("CA-19",),
    ),
    FailureCase(
        "500_luego_timeout", (("status", 500), ("raise", "timeout")),
        "GEMINI_TIMEOUT", "GEMINI_SERVER_ERROR", 2, 1, (1.0,), (),
        "WARNING", "WARNING", ("CA-19",),
    ),
    # --- CA-20: excepciones de la llamada, sin reintento
    _single("timeout", ("raise", "timeout"), "GEMINI_TIMEOUT", "WARNING", ("CA-20",)),
    _single("connect_error", ("raise", "connect"), "GEMINI_NETWORK", "WARNING", ("CA-20",)),
    _single("read_error", ("raise", "read"), "GEMINI_NETWORK", "WARNING", ("CA-20",)),
    _single(
        "local_protocol_error", ("raise", "protocol"), "GEMINI_NETWORK", "WARNING", ("CA-20",)
    ),
    _single("excepcion_generica", ("raise", "generic"), "GEMINI_NETWORK", "WARNING", ("CA-20",)),
    # --- CA-20 / CS-18: status no contemplado
    _single("302", ("status", 302), "GEMINI_UNEXPECTED_STATUS", "WARNING", ("CA-20", "CS-18")),
    _single("418", ("status", 418), "GEMINI_UNEXPECTED_STATUS", "WARNING", ("CA-20",)),
    # --- CA-21: HTTP 200 con contenido inutilizable
    _single(
        "200_blockreason",
        ("body", gemini_response(None, finish_reason=None, candidates=[],
                                 prompt_feedback={"blockReason": "SAFETY"})),
        "GEMINI_BLOCKED", "WARNING", ("CA-21",),
    ),
    _single(
        "200_candidates_vacio",
        ("body", {"candidates": []}),
        "GEMINI_EMPTY", "WARNING", ("CA-21",),
    ),
    _single(
        "200_candidates_no_lista",
        ("body", {"candidates": "no soy una lista"}),
        "GEMINI_EMPTY", "WARNING", ("CA-21",),
    ),
    _single(
        "200_max_tokens",
        ("body", gemini_response("cortado a med", finish_reason="MAX_TOKENS")),
        "GEMINI_MAX_TOKENS", "WARNING", ("CA-21",),
    ),
    _single(
        "200_finish_safety",
        ("body", gemini_response("x", finish_reason="SAFETY")),
        "GEMINI_BLOCKED", "WARNING", ("CA-21",),
    ),
    _single(
        "200_finish_stop_con_salto",
        ("body", gemini_response("x", finish_reason="STOP\n")),
        "GEMINI_BLOCKED", "WARNING", ("CA-21",),
    ),
    _single(
        "200_texto_vacio",
        ("body", gemini_response("   ")),
        "GEMINI_EMPTY", "WARNING", ("CA-21",),
    ),
    _single(
        "200_solo_partes_thought",
        ("body", gemini_response(None, thought_parts=("pensando",))),
        "GEMINI_EMPTY", "WARNING", ("CA-21",),
    ),
    _single("200_cuerpo_no_json", ("raw", b"<html>no json</html>"), "GEMINI_MALFORMED",
            "WARNING", ("CA-21",)),
    _single("200_json_forma_inesperada", ("body", ["no", "es", "dict"]), "GEMINI_MALFORMED",
            "WARNING", ("CA-21",)),
    _single(
        "200_json_anidado_100000",
        ("raw", ("[" * 100000 + "]" * 100000).encode()),
        "GEMINI_MALFORMED", "WARNING", ("CA-21", "CS-19"),
    ),
)

FAILURE_IDS = [c.id for c in FAILURE_TABLE]
