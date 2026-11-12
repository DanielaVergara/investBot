"""Reescritura de tono con Gemini (`SDD_migracion_gemini.md`).

Reescribe el TONO de las secciones que `summary.py` ya terminó de armar —
nunca participa de ningún cálculo, nunca ve datos crudos de FMP, nunca
decide nada financiero. Opt-in, apagado por defecto
(`GEMINI_REWRITE_ENABLED`, que además exige una `GEMINI_API_KEY` válida): con
`config.rewrite_enabled=False`, `rewrite_parts` es un no-op inmediato, cero
latencia agregada, cero llamadas HTTP. El transporte (request, errores,
parseo del sobre) vive en `gemini_client.generate_text`; este módulo no crea
clientes HTTP.

Garantía de integridad — placeholder-y-restitución (Spec Patch [Iter-2] de
`SDD_redaccion_ia_ollama.md`, sigue vigente): antes de enviar cada sección al
modelo, toda línea con al menos un "protected token" (número, %, ticker,
✅/❌, SÍ/NO) o que sea un disclaimer protegido se reemplaza por un
placeholder opaco (`⟦PHn⟧`); el modelo nunca ve ni puede alterar el contenido
real de esas líneas. Tras la respuesta, se restituye cada placeholder por su
línea original verbatim — no hay "swap" posible porque no hay nada que el
modelo pueda mover. `_is_safe_rewrite`/`_protected_tokens` son la "red de
seguridad final" post-restitución.

Orden por sección (3.14 de la spec): parseo del JSON -> filtro de salida
(URLs/menciones/invisibles) sobre el texto del modelo, ANTES de restituir ->
restitución con `_reconstruct_section` (placeholders, `_is_safe_rewrite`,
balance de Markdown) -> tope de longitud sobre la sección ya reconstruida.
Cualquier falla vuelve a esa sección a su texto original.

Ante cualquier falla de Gemini (error HTTP, timeout, bloqueo, estructura
inesperada) el fallback es el texto original de `summary.py` sin cambios, sin
excepción: el rewrite no reintenta (es cosmético y bloquea el primer mensaje).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from investbot import gemini_client
from investbot.gemini_client import GeminiConfig, GeminiError, violates_output_filter
from investbot.summary import DISCLAIMER_NO_ASESORAMIENTO, DISCLAIMER_WACC_DCF

logger = logging.getLogger(__name__)

# Tope de tokens de salida del rewrite (3.2 de la spec): los tokens de
# pensamiento cuentan contra este tope, así que es un techo de seguridad
# contra una generación fuera de control, no un objetivo. `MAX_TOKENS` es
# falla con fallback al texto original.
REWRITE_MAX_OUTPUT_TOKENS = 2048
_REWRITE_MIME_TYPE = "application/json"

# Tope de longitud por sección ya reconstruida (3.14/P-7):
# `max(int(1.5 * len(original)), 200)`. El prompt pide condensar, así que una
# salida más larga indica desvío.
_LENGTH_FACTOR = 1.5
_LENGTH_FLOOR = 200

# --- Transparencia (3.7 de la spec): primera línea de todo análisis exitoso.
# Ninguna constante contiene `*`, `_`, `` ` `` ni `[`: se anteponen a un
# mensaje que se envía con `parse_mode="Markdown"`.
TRANSPARENCY_USED = "🤖 Con ayuda de Gemini"
TRANSPARENCY_NOT_USED = "📋 Gemini no disponible esta vez"
TRANSPARENCY_FIXED_NO_BUTTONS = "📋 Análisis con formato fijo (sin IA)."
TRANSPARENCY_FIXED_WITH_BUTTONS = (
    "📋 Análisis con formato fijo — pedí una explicación con los botones de abajo."
)


def transparency_line(config: Optional[GeminiConfig], used: bool) -> str:
    """Primera línea del mensaje de análisis, 4 estados (3.6/3.7): sin
    Gemini habilitado -> formato fijo sin botones; habilitado y sin rewrite
    -> formato fijo con botones; rewrite habilitado -> `USED` si al menos una
    sección cambió, `NOT_USED` si no (falla o sin cambios)."""
    if config is None or not config.enabled:
        return TRANSPARENCY_FIXED_NO_BUTTONS
    if not config.rewrite_enabled:
        return TRANSPARENCY_FIXED_WITH_BUTTONS
    return TRANSPARENCY_USED if used else TRANSPARENCY_NOT_USED


# --- Guard de integridad numérica (Decisión de diseño #3(c) original —
# `_protected_tokens`/`_is_safe_rewrite` sobreviven el Spec Patch [Iter-2]
# sin cambio de firma, solo cambian de rol: ver docstring del módulo) -------

_PROTECTED_TOKEN_RE = re.compile(
    r"[+-]?\$?\d[\d.,]*%?|✅|❌|\bSÍ\b|\bNO\b|\b[A-ZÁÉÍÓÚ]{2,10}\b"
)


def _protected_tokens(text: str) -> list[str]:
    return sorted(_PROTECTED_TOKEN_RE.findall(text))


# Alias público (SDD_explicaciones_interactivas_ollama.md, Artefactos): mismo
# símbolo, misma implementación — `ai_explain.py` lo reusa para su guard de
# subconjunto (`_no_new_protected_tokens`) sin importar un nombre privado de
# otro módulo.
protected_tokens = _protected_tokens


def _is_safe_rewrite(original: str, rewritten: str) -> bool:
    return _protected_tokens(original) == _protected_tokens(rewritten)


# --- Guard de balance de Markdown (incidente de producción 2026-09-04,
# ADBE: `telegram.error.BadRequest: Can't parse entities`) -----------------
#
# `_is_safe_rewrite`/`_protected_tokens` de arriba comparan números/%/
# tickers/✅/❌/SÍ/NO -- nunca compararon el conteo de los caracteres que
# Telegram interpreta como delimitadores de entidad Markdown (`*`, `_`,
# `` ` ``, `[`). Una línea de prosa 100% libre (sin ningún protected token,
# ej. "*Veredicto:*" de `summary.build_veredicto_section`) puede llegar
# intacta al modelo para reescritura -- si el modelo parafrasea y dropea o
# duplica UNO de esos caracteres (ej. "*Veredicto:*" -> "*Veredicto:"), el
# guard de arriba no lo detecta porque ningún protected token cambió, pero
# el mensaje final queda con un delimitador de negrita/itálica desparejado
# y Telegram rechaza el `edit_text`/`send_message` completo con
# `BadRequest: Can't parse entities` -- el usuario se queda sin respuesta.
#
# Se compara PARIDAD (conteo par/impar), no igualdad de conteo contra el
# original: la regla 7 del prompt permite condensar libremente prosa libre,
# incluso eliminando por completo una línea con Markdown propio (ej. un
# título en negrita que se funde en una frase de resumen sin formato) — eso
# es una reescritura legítima que cambia el conteo total sin dejar ningún
# delimitador desparejado. Lo que nunca es legítimo es que la sección
# reconstruida quede con un conteo IMPAR de `*`/`_`/`` ` `` (delimitador
# que abre sin cerrar, o viceversa) — eso es lo que Telegram no puede
# parsear. `[` se verifica igual por consistencia con el resto de
# caracteres especiales de Markdown de Telegram, aunque en la práctica no
# aparece en ninguna sección de este bot.
_MARKDOWN_DELIMITER_CHARS = ("*", "_", "`", "[")


def _markdown_balance_preserved(original: str, rewritten: str) -> bool:
    """`original` no se usa en la comparación (se mantiene en la firma por
    simetría con `_is_safe_rewrite` y porque documenta la intención: el
    invariante que debe seguir cumpliendo `rewritten` es el mismo que ya
    cumplía `original` antes de pasar por el modelo). Devuelve `False` si
    algún delimitador de Markdown queda con conteo impar en `rewritten`."""
    return all(rewritten.count(ch) % 2 == 0 for ch in _MARKDOWN_DELIMITER_CHARS)


# --- Placeholder-y-restitución (Spec Patch [Iter-2]) ------------------------

_PLACEHOLDER_RE = re.compile(r"⟦PH\d+⟧")

# Disclaimers de transparencia/legales protegidos (Spec Patch [Iter-3]): se
# importan como el MISMO objeto que usa `summary.py` para armar el mensaje
# real (Opción C del patch) — no una copia hardcodeada que pueda driftear. Se
# comparan por igualdad exacta de línea completa en `_classify_lines`, así
# que nunca llegan al modelo como texto libre editable — no hay nada que
# "condensar" porque no hay nada que el modelo pueda leer.
_PROTECTED_DISCLAIMERS = frozenset({DISCLAIMER_WACC_DCF, DISCLAIMER_NO_ASESORAMIENTO})


def _classify_lines(section: str) -> tuple[str, dict[str, str]]:
    """Reemplaza cada línea con >=1 protected token, o que sea exactamente
    uno de los disclaimers protegidos (`_PROTECTED_DISCLAIMERS`, Spec Patch
    [Iter-3]), por un placeholder opaco. Devuelve
    (texto_con_placeholders, mapa_placeholder_a_linea_original). Líneas sin
    ningún protected token y que no sean un disclaimer (prosa pura) quedan
    intactas y completamente libres para que el LLM las reescriba/condense
    sin restricción.

    El índice de cada placeholder es la posición de línea *dentro de la
    sección* (se reinicia en cada llamada) — mismo criterio ya usado para
    los marcadores `<<<SECTION_i>>>` del prompt completo.
    """
    lines = section.split("\n")
    line_map: dict[str, str] = {}
    result_lines = []
    for idx, line in enumerate(lines):
        if line in _PROTECTED_DISCLAIMERS or _protected_tokens(line):
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
    pasando `_is_safe_rewrite` Y `_markdown_balance_preserved` contra la
    sección original completa — por construcción ambos siempre deberían
    pasar (todo el contenido protegido se restituyó verbatim, el resto
    nunca tenía protected tokens), así que un fallo acá indica un bug de
    clasificación o una alucinación del LLM en una línea de prosa libre
    (sin placeholder) — ya sea un dato inventado (`_is_safe_rewrite`) o un
    delimitador de Markdown de Telegram agregado/dropeado al parafrasear
    (`_markdown_balance_preserved`, incidente de producción 2026-09-04) —,
    no un "swap" de placeholders — se trata igual: fallback a la sección
    original completa.
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

    if not _markdown_balance_preserved(original, reconstructed):
        return None  # delimitador de Markdown de Telegram desparejado

    return reconstructed


# --- Prompt (Decisión de diseño #3(a)/(b), regla 6 agregada por el patch) ----
# Contrato de transporte: objeto JSON con claves de índice numérico en texto.
# El servidor garantiza JSON sintácticamente válido (`responseMimeType:
# "application/json"`, sin schema); las claves exactas y los tipos de los
# valores los valida `_parse_json_sections`. El guard de integridad real
# (`_is_safe_rewrite`/placeholders) es independiente del modelo y no depende
# de este contenedor. Contenido del prompt constante, sin interpolación.

SYSTEM_PROMPT = (
    "Sos un editor de redacción financiera en español rioplatense. Tu única tarea\n"
    "es mejorar la CLARIDAD y NATURALIDAD del texto que te paso.\n\n"
    "Contrato de entrada/salida: vas a recibir un objeto JSON cuyas claves son\n"
    "índices numéricos en formato texto (\"0\", \"1\", \"2\", ...) y cuyos valores\n"
    "son las secciones de texto a reescribir. Respondé ÚNICAMENTE con un objeto\n"
    "JSON válido que tenga EXACTAMENTE las mismas claves que recibiste, ni de\n"
    "más ni de menos, donde cada valor sea la reescritura de la sección\n"
    "correspondiente a esa clave.\n\n"
    "Reglas estrictas, sin excepción:\n"
    "1. NUNCA cambies, agregues, quites ni \"corrijas\" ningún número, porcentaje,\n"
    "   ticker, símbolo (✅/❌), o palabra de veredicto (SÍ/NO) — copialos\n"
    "   exactamente como aparecen en el texto original.\n"
    "2. NUNCA agregues información, opinión, consejo financiero, ni datos que no\n"
    "   estén ya en el texto.\n"
    "3. Mantené el formato Markdown de Telegram (*negrita*, _itálica_) dentro de\n"
    "   cada valor de texto, y las claves del objeto JSON exactamente como las\n"
    "   recibiste.\n"
    "4. Si una sección ya está clara Y breve, devolvela sin cambios.\n"
    "5. Respondé ÚNICAMENTE con el objeto JSON reescrito, sin comentarios tuyos,\n"
    "   sin explicaciones adicionales, sin texto antes ni después del JSON.\n"
    "6. Vas a ver tokens de la forma ⟦PHn⟧ dentro de los valores de texto del\n"
    "   JSON (n es un número). Son marcadores opacos — no sabés ni necesitás\n"
    "   saber qué representan. Copialos EXACTAMENTE tal cual aparecen, una sola\n"
    "   vez cada uno, en cualquier lugar del texto que tenga sentido para la\n"
    "   fluidez de tu redacción. Nunca los modifiques, fusiones con palabras\n"
    "   vecinas, dupliques, traduzcas, ni interpretes su contenido.\n"
    "7. Además de mejorar la claridad, podés CONDENSAR el texto: acortalo\n"
    "   eliminando redundancia, rodeos o repeticiones, siempre que no se\n"
    "   pierda ningún dato, número, ticker, veredicto, matiz de significado\n"
    "   ni idea completa. El objetivo es más claro y más corto, nunca menos\n"
    "   información. No resumas de forma agresiva — condensar no es resumir.\n"
)


def _parse_json_sections(raw_text: str, expected_count: int) -> Optional[list[str]]:
    """Parsea el texto del modelo como un objeto JSON con claves de índice
    string (`"0"`..`"{expected_count-1}"`). `None` si:
    - `raw_text` no es JSON válido (`json.JSONDecodeError`) o está anidado
      tan hondo que lanza `RecursionError` (CS-19), o
    - el resultado no es un `dict`, o
    - el conjunto de claves no es exactamente `{"0", ..., str(expected_count-1)}`
      (ni de más ni de menos), o
    - algún valor no es `str`.

    Estructura rota en cualquiera de esos casos -> fallback completo (todas
    las secciones vuelven al original)."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    expected_keys = {str(i) for i in range(expected_count)}
    if set(data.keys()) != expected_keys:
        return None
    if not all(isinstance(value, str) for value in data.values()):
        return None
    return [data[str(i)] for i in range(expected_count)]


def _length_limit(original: str) -> int:
    return max(int(_LENGTH_FACTOR * len(original)), _LENGTH_FLOOR)


# --- Orquestación -----------------------------------------------------------


@dataclass(frozen=True)
class RewriteOutcome:
    """Resultado de `rewrite_parts`. `used_gemini` es `True` solo si al menos
    una sección resultó efectivamente distinta del original tras los guards;
    `_run_analysis` lo usa para elegir la línea de transparencia."""

    parts: list[str]
    used_gemini: bool


def _fallback(parts: list[str]) -> RewriteOutcome:
    return RewriteOutcome(parts=parts, used_gemini=False)


async def rewrite_parts(
    parts: list[str],
    config: Optional[GeminiConfig],
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> RewriteOutcome:
    """Recibe la misma forma de datos que fluye entre
    `fetch_and_analyze_parts` y `chunk_for_telegram` (`list[str]`, índice 0
    = título) y devuelve un `RewriteOutcome` cuyo `.parts` es una lista de la
    misma longitud: cada sección es o bien la reescritura de Gemini (si pasó
    todos los guards) o el texto original sin cambios.

    No-op inmediato (0 llamadas HTTP, `used_gemini=False`) si la config es
    `None`, si `config.rewrite_enabled` es falso, o si `parts` solo trae el
    título. Con `rewrite_enabled=True` y `http_client=None` también es no-op
    (sin crear ningún cliente) y deja un WARNING `GEMINI_NO_CLIENT`: en
    producción `main()` crea el cliente siempre que Gemini esté habilitado,
    así que ese estado es un error de cableado y debe ser visible.

    El título (`parts[0]`) nunca se envía al modelo ni se reescribe. Sin
    reintentos: la primera falla de Gemini es definitiva y se devuelve el
    texto original (el cliente ya dejó su propio registro con token)."""
    if config is None or not (config.enabled and config.rewrite_enabled):
        return _fallback(parts)

    body_parts = parts[1:]
    if not body_parts:
        return _fallback(parts)

    if http_client is None:
        gemini_client.log_no_client("rewrite")
        return _fallback(parts)

    placeholder_sections: list[str] = []
    line_maps: list[dict[str, str]] = []
    for section in body_parts:
        placeholder_text, line_map = _classify_lines(section)
        placeholder_sections.append(placeholder_text)
        line_maps.append(line_map)

    user_text = json.dumps(
        {str(i): text for i, text in enumerate(placeholder_sections)}, ensure_ascii=False
    )

    try:
        result = await gemini_client.generate_text(
            http_client,
            config,
            system_prompt=SYSTEM_PROMPT,
            user_text=user_text,
            max_output_tokens=REWRITE_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.rewrite_timeout_seconds,
            max_retries=0,
            feature="rewrite",
            response_mime_type=_REWRITE_MIME_TYPE,
        )
    except GeminiError:
        # `asyncio.CancelledError` (BaseException) no queda atrapado y se
        # propaga. El cliente ya registró la falla con su token: no se duplica.
        return _fallback(parts)

    try:
        sections = _parse_json_sections(result.text, len(body_parts))
        if sections is None:
            logger.warning(
                "GEMINI_MALFORMED feature=rewrite estructura JSON inesperada "
                "(%d secciones esperadas) — fallback completo a redacción original",
                len(body_parts),
            )
            return _fallback(parts)

        result_body: list[str] = []
        any_rewritten = False
        for original_section, rewritten_section, line_map in zip(
            body_parts, sections, line_maps
        ):
            # 1) filtro de salida sobre el texto del modelo, ANTES de restituir
            # (las líneas restituidas nunca son salida del modelo).
            if violates_output_filter(rewritten_section):
                logger.warning(
                    "GEMINI_GUARD_REJECT feature=rewrite guard=salida — esa sección "
                    "vuelve a su redacción original, el resto del mensaje sigue su "
                    "curso normal"
                )
                result_body.append(original_section)
                continue
            # 2) restitución + `_is_safe_rewrite` + balance de Markdown
            reconstructed = _reconstruct_section(rewritten_section, line_map, original_section)
            if reconstructed is None:
                logger.warning(
                    "GEMINI_GUARD_REJECT feature=rewrite guard=integridad — una sección "
                    "no pasó el guard de integridad — esa sección vuelve a su "
                    "redacción original, el resto del mensaje sigue su curso normal"
                )
                result_body.append(original_section)
                continue
            # 3) tope de longitud sobre la sección YA reconstruida
            if len(reconstructed) > _length_limit(original_section):
                logger.warning(
                    "GEMINI_GUARD_REJECT feature=rewrite guard=longitud — esa sección "
                    "vuelve a su redacción original, el resto del mensaje sigue su "
                    "curso normal"
                )
                result_body.append(original_section)
                continue
            result_body.append(reconstructed)
            if reconstructed != original_section:
                any_rewritten = True
    except Exception:
        # Parseo/guards lanzando una excepción propia (bug de programación, no
        # un fallo de red): mismo fallback, nunca propaga hacia `_run_analysis`.
        # El traceback es la única pista de un bug; la excepción original de
        # httpx nunca llega acá (`GeminiError` se lanza `from None`).
        logger.warning(
            "Fallo inesperado reconstruyendo la respuesta de Gemini — "
            "fallback completo a redacción original", exc_info=True,
        )
        return _fallback(parts)

    return RewriteOutcome(parts=[parts[0], *result_body], used_gemini=any_rewritten)
