"""Capa de post-procesamiento de redacción con LLM local (Ollama).

Reescribe el TONO de las secciones que `summary.py` ya terminó de armar —
nunca participa de ningún cálculo, nunca ve datos crudos de FMP, nunca
decide nada financiero. Opt-in, apagado por defecto
(`OLLAMA_REWRITE_ENABLED`): sin la variable seteada, `rewrite_parts` es un
no-op inmediato, cero latencia agregada, cero llamadas HTTP.

Garantía de integridad — placeholder-y-restitución (Spec Patch [Iter-2] de
`SDD_redaccion_ia_ollama.md`, reemplaza la Decisión de diseño #3(c)
original de comparar multiset por sección, que `security` demostró que no
detecta un intercambio de tokens protegidos entre 2 líneas de la misma
sección): antes de enviar cada sección a Ollama, toda línea con al menos un
"protected token" (número, %, ticker, ✅/❌, SÍ/NO) se reemplaza por un
placeholder opaco (`⟦PHn⟧`); el LLM nunca ve ni puede alterar el contenido
real de esas líneas. Tras la respuesta, se restituye cada placeholder por
su línea original verbatim — no hay "swap" posible porque no hay nada que
el modelo pueda mover. `_is_safe_rewrite`/`_protected_tokens` no
desaparecen: cambian de rol, de "comparación primaria" a "criterio de
clasificación" + "red de seguridad final" post-restitución.

Cuando Ollama no responde a tiempo (PC apagada, tailnet caída, timeout,
lo que sea) el fallback es silencioso: se devuelve el texto original de
`summary.py` sin cambios, sin excepción, logueado a INFO — es un estado
esperado del sistema, no una anomalía (Decisión de diseño #2).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

# --- Configuración (Decisión de diseño #4) ---------------------------------

ENV_ENABLED = "OLLAMA_REWRITE_ENABLED"
ENV_BASE_URL = "OLLAMA_BASE_URL"
ENV_MODEL = "OLLAMA_MODEL"
ENV_TIMEOUT = "OLLAMA_TIMEOUT_SECONDS"

DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_TIMEOUT_SECONDS = 8.0
# Timeout de conexión fijo (Decisión de diseño #2) — no configurable por env,
# a propósito: el caso más común (PC apagada) debe fallar rápido siempre,
# independientemente de qué tan generoso sea OLLAMA_TIMEOUT_SECONDS (que
# solo controla el read timeout, el caso "PC prendida pero generando lento").
CONNECT_TIMEOUT_SECONDS = 3.0

# Tope duro de tokens de salida (`num_predict` de Ollama) — no configurable
# por env, a propósito: es una red de seguridad de tiempo, no una perilla de
# tuning de usuario. Sin esto, un modelo puede generar indefinidamente sin
# converger a un stop token (observado en producción: >1100 tokens en una
# sola reescritura), haciendo que CUALQUIER timeout, por generoso que sea,
# eventualmente se agote sin producir ninguna respuesta utilizable. Si la
# reescritura se corta a mitad de camino por este tope, los marcadores
# <<<SECTION_N>>> quedan incompletos y el guard de reconstrucción cae a
# fallback igual que cualquier otra respuesta malformada — nunca llega un
# mensaje cortado al usuario final.
MAX_OUTPUT_TOKENS = 600

_TRUTHY_VALUES = {"true", "1", "yes"}

# Indicador visible en el mensaje final (Pregunta abierta 1, resuelta por
# Daniela: sí lo quiere). Criterio de `security`, sección 7: no debe revelar
# ningún detalle de infraestructura (nombre de proveedor, IP, modelo,
# timeout, estado de la PC) — solo comunica que hubo un paso adicional de
# IA local, nada más específico.
AI_REWRITE_INDICATOR = "_(redacción asistida por IA local)_"


@dataclass(frozen=True)
class OllamaConfig:
    """Configuración resuelta de la feature. `enabled=False` es el estado
    seguro por defecto — `rewrite_parts` lo trata como no-op inmediato."""

    enabled: bool
    base_url: str
    model: str
    timeout_seconds: float


def load_config(env: Optional[Mapping[str, str]] = None) -> OllamaConfig:
    """Lee las 4 variables de entorno de Ollama. Nunca lanza — a diferencia
    de `security.get_allowed_chat_id` (fail-closed), esta feature es
    opcional/best-effort (Decisión de diseño #5): cualquier configuración
    ausente o incompleta resuelve a `enabled=False`, nunca aborta el
    arranque del bot.

    `OLLAMA_REWRITE_ENABLED` sin setear (o cualquier valor que no sea
    "true"/"1"/"yes" case-insensitive) -> deshabilitado, sin siquiera mirar
    `OLLAMA_BASE_URL`. Con el flag en `true` pero `OLLAMA_BASE_URL` vacía o
    ausente -> también deshabilitado (defensivo, evita un `ConnectError`
    inmediato contra una URL vacía en cada consulta).
    """
    source = env if env is not None else os.environ

    raw_enabled = (source.get(ENV_ENABLED) or "").strip().lower()
    flag_on = raw_enabled in _TRUTHY_VALUES

    # El feature flag es la puerta de entrada explícita (Decisión de diseño
    # #4): con el flag apagado, `OLLAMA_BASE_URL` ni siquiera se lee — no
    # solo "se ignora su valor".
    base_url = (source.get(ENV_BASE_URL) or "").strip() if flag_on else ""
    enabled = flag_on and bool(base_url)

    model = source.get(ENV_MODEL) or DEFAULT_MODEL

    raw_timeout = source.get(ENV_TIMEOUT)
    try:
        timeout_seconds = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    return OllamaConfig(
        enabled=enabled, base_url=base_url, model=model, timeout_seconds=timeout_seconds
    )


# --- Guard de integridad numérica (Decisión de diseño #3(c) original —
# `_protected_tokens`/`_is_safe_rewrite` sobreviven el Spec Patch [Iter-2]
# sin cambio de firma, solo cambian de rol: ver docstring del módulo) -------

_PROTECTED_TOKEN_RE = re.compile(
    r"[+-]?\$?\d[\d.,]*%?|✅|❌|\bSÍ\b|\bNO\b|\b[A-ZÁÉÍÓÚ]{2,10}\b"
)


def _protected_tokens(text: str) -> list[str]:
    return sorted(_PROTECTED_TOKEN_RE.findall(text))


def _is_safe_rewrite(original: str, rewritten: str) -> bool:
    return _protected_tokens(original) == _protected_tokens(rewritten)


# --- Placeholder-y-restitución (Spec Patch [Iter-2]) ------------------------

_PLACEHOLDER_RE = re.compile(r"⟦PH\d+⟧")


def _classify_lines(section: str) -> tuple[str, dict[str, str]]:
    """Reemplaza cada línea con >=1 protected token por un placeholder
    opaco. Devuelve (texto_con_placeholders, mapa_placeholder_a_linea_original).
    Líneas sin ningún protected token (prosa pura) quedan intactas y
    completamente libres para que el LLM las reescriba sin restricción.

    El índice de cada placeholder es la posición de línea *dentro de la
    sección* (se reinicia en cada llamada) — mismo criterio ya usado para
    los marcadores `<<<SECTION_i>>>` del prompt completo.
    """
    lines = section.split("\n")
    line_map: dict[str, str] = {}
    result_lines = []
    for idx, line in enumerate(lines):
        if _protected_tokens(line):
            placeholder = f"⟦PH{idx}⟧"
            line_map[placeholder] = line
            result_lines.append(placeholder)
        else:
            result_lines.append(line)
    return "\n".join(result_lines), line_map


def _reconstruct_section(
    rewritten: str, line_map: dict[str, str], original: str
) -> Optional[str]:
    """Capa 1: valida que el conjunto de placeholders en `rewritten` sea
    exactamente el que se envió (mismo conjunto, sin duplicados, sin
    faltantes — el orden NO importa). Si pasa, restituye cada placeholder
    por su línea original verbatim.

    Capa 2 (defensa en profundidad, cierra huecos de clasificación no
    detectados por `_protected_tokens`): el resultado final debe seguir
    pasando `_is_safe_rewrite` contra la sección original completa — por
    construcción siempre debería pasar (todo el contenido protegido se
    restituyó verbatim, el resto nunca tenía protected tokens), así que un
    fallo acá indica un bug de clasificación o una alucinación del LLM en
    una línea de prosa libre (sin placeholder), no un "swap" de
    placeholders — se trata igual: fallback a la sección original completa.
    """
    expected = set(line_map.keys())
    found = _PLACEHOLDER_RE.findall(rewritten)
    if set(found) != expected or len(found) != len(expected):
        return None  # placeholder faltante, duplicado o desconocido

    reconstructed = rewritten
    for placeholder, original_line in line_map.items():
        reconstructed = reconstructed.replace(placeholder, original_line, 1)

    if not _is_safe_rewrite(original, reconstructed):
        return None  # red de seguridad final

    return reconstructed


# --- Prompt (Decisión de diseño #3(a)/(b), regla 6 agregada por el patch) --

_SECTION_DELIM = "\n<<<SECTION_{i}>>>\n"
_SECTION_MARKER_RE = re.compile(r"<<<SECTION_\d+>>>\n?")

SYSTEM_PROMPT = (
    "Sos un editor de redacción financiera en español rioplatense. Tu única tarea\n"
    "es mejorar la CLARIDAD y NATURALIDAD del texto que te paso, sección por\n"
    "sección, delimitada por marcadores <<<SECTION_N>>>.\n\n"
    "Reglas estrictas, sin excepción:\n"
    "1. NUNCA cambies, agregues, quites ni \"corrijas\" ningún número, porcentaje,\n"
    "   ticker, símbolo (✅/❌), o palabra de veredicto (SÍ/NO) — copialos\n"
    "   exactamente como aparecen en el texto original.\n"
    "2. NUNCA agregues información, opinión, consejo financiero, ni datos que no\n"
    "   estén ya en el texto.\n"
    "3. Mantené el formato Markdown de Telegram (*negrita*, _itálica_) y los\n"
    "   marcadores <<<SECTION_N>>> exactamente en las mismas posiciones.\n"
    "4. Si una sección ya está clara, devolvela sin cambios.\n"
    "5. Respondé ÚNICAMENTE con el texto reescrito completo, sin comentarios\n"
    "   tuyos, sin explicaciones adicionales.\n"
    "6. Vas a ver tokens de la forma ⟦PHn⟧ en el texto (n es un número). Son\n"
    "   marcadores opacos — no sabés ni necesitás saber qué representan. Copialos\n"
    "   EXACTAMENTE tal cual aparecen, una sola vez cada uno, en cualquier lugar\n"
    "   del texto que tenga sentido para la fluidez de tu redacción. Nunca los\n"
    "   modifiques, fusiones con palabras vecinas, dupliques, traduzcas, ni\n"
    "   interpretes su contenido.\n"
)


def _split_by_markers(text: str, expected_count: int) -> Optional[list[str]]:
    """Parte la respuesta cruda de Ollama por los marcadores
    `<<<SECTION_i>>>`. `None` si la cantidad de marcadores encontrados no
    coincide exactamente con `expected_count` — estructura rota, fallback
    completo (todas las secciones vuelven al original)."""
    matches = list(_SECTION_MARKER_RE.finditer(text))
    if len(matches) != expected_count:
        return None
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[start:end].strip("\n"))
    return sections


# --- Llamada de red + orquestación ------------------------------------------


async def rewrite_parts(
    parts: list[str],
    config: OllamaConfig,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> list[str]:
    """Recibe la misma forma de datos que fluye entre
    `fetch_and_analyze_parts` y `chunk_for_telegram` (`list[str]`, índice 0
    = título) y devuelve una lista de la misma longitud: cada sección es o
    bien la reescritura de Ollama (si pasó el guard de 2 capas) o el texto
    original sin cambios.

    El título (`parts[0]`) nunca se envía a Ollama ni se reescribe, bajo
    ningún escenario (Restricción de `architect`). Con la feature
    deshabilitada, o con `parts` de longitud <=1 (solo título), es un no-op
    inmediato — ni siquiera se resuelve `config.base_url` en una conexión.
    """
    if not config.enabled:
        return parts

    body_parts = parts[1:]
    if not body_parts:
        return parts

    placeholder_sections: list[str] = []
    line_maps: list[dict[str, str]] = []
    for section in body_parts:
        placeholder_text, line_map = _classify_lines(section)
        placeholder_sections.append(placeholder_text)
        line_maps.append(line_map)

    prompt = "".join(
        _SECTION_DELIM.format(i=i) + text for i, text in enumerate(placeholder_sections)
    )

    client = http_client if http_client is not None else httpx.AsyncClient()
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=config.timeout_seconds,
        write=config.timeout_seconds,
        pool=config.timeout_seconds,
    )

    try:
        response = await client.post(
            f"{config.base_url}/api/generate",
            json={
                "model": config.model,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": MAX_OUTPUT_TOKENS},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        # `response.json()` lanza `json.JSONDecodeError` sobre cuerpo
        # no-JSON — es subclase de `ValueError` en la librería estándar de
        # Python, por eso queda cubierta acá aunque no se nombre explícita
        # (criterio de `security`, sección 4 — documentado para que un
        # refactor futuro no la excluya pensando que `ValueError` sobra).
        raw_text = data["response"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        # `asyncio.CancelledError` (y cualquier otro `BaseException`) NO
        # queda atrapado acá — hereda de `BaseException`, no de `Exception`,
        # así que se propaga normalmente (criterio de `security`, sección 4).
        logger.info(
            "Ollama no disponible o timeout — fallback a redacción original (%s)",
            type(exc).__name__,
        )
        return parts

    try:
        sections = _split_by_markers(raw_text, len(body_parts))
        if sections is None:
            logger.warning(
                "Respuesta de Ollama con estructura de marcadores inesperada "
                "(%d secciones esperadas) — fallback completo a redacción original",
                len(body_parts),
            )
            return parts

        result_body: list[str] = []
        any_rewritten = False
        for original_section, rewritten_section, line_map in zip(
            body_parts, sections, line_maps
        ):
            reconstructed = _reconstruct_section(rewritten_section, line_map, original_section)
            if reconstructed is None:
                logger.warning(
                    "Una sección no pasó el guard de integridad — esa sección "
                    "vuelve a su redacción original, el resto del mensaje sigue "
                    "su curso normal"
                )
                result_body.append(original_section)
            else:
                result_body.append(reconstructed)
                if reconstructed != original_section:
                    any_rewritten = True
    except Exception:
        # Guard/parseo de marcadores lanzando una excepción propia (bug de
        # programación, no un fallo de red) — mismo fallback que un fallo de
        # red, nunca propaga hacia `_run_analysis` (criterio de `security`,
        # sección 4, última fila).
        logger.warning(
            "Fallo inesperado reconstruyendo la respuesta de Ollama — "
            "fallback completo a redacción original", exc_info=True,
        )
        return parts

    result = [parts[0], *result_body]
    if any_rewritten:
        result[-1] = result[-1] + "\n\n" + AI_REWRITE_INDICATOR
    return result
