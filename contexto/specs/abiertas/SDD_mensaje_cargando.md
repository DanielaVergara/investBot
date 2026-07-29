# Spec: Mensaje de "cargando" durante el análisis [Iter-1]

**Rol:** `architect` (spec base).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` — revisión liviana, opcional pero recomendada (ver "Nota para `security`" al final; el único código nuevo que toca una superficie no cubierta antes es el manejo de excepciones de la API de Telegram, no hay dato sensible nuevo). No pasa por `frontend` — InvestBot no tiene superficie web/HTML, es un bot de Telegram de un solo usuario; no aplica WCAG ni ningún criterio que ese rol agregue en este proyecto (mismo criterio ya usado en `SDD_fmp_402_simbolo_premium.md`). Luego `qa` agrega criterios de cobertura sobre la recomendación de testing que este documento ya deja resuelta (ver sección "Recomendación de testing — mocks vs. verificación manual"). Después `implementer` ejecuta con Ralph Loop.

---

## Contexto

Daniela pidió una señal de que el bot está trabajando mientras arma el análisis. Hoy, entre que el usuario manda un ticker y recibe la respuesta, hay silencio total del lado de Telegram.

La causa es `fetch_and_analyze` (`src/investbot/query_handler.py:81`), que encadena entre 6 y 12 llamadas HTTP (6 fijas al ticker propio + 0-1 de `/search` + 3-5 por peer, ver README.md sección "Presupuesto de requests por consulta") contra FMP, y una llamada adicional a FRED/Treasury.gov para `Y`. Esto puede tardar varios segundos, sobre todo si algún peer o el fallback de Treasury.gov tarda en responder.

**Hallazgo clave que cambia el planteo original de la tarea:** `fetch_and_analyze` no se dispara desde 3 lugares — se dispara desde **uno solo**: `_run_analysis` (línea 356). `handle_text` (línea 279) y `handle_disambiguation` (línea 339) no llaman a `fetch_and_analyze` directamente; ambos llaman a `_run_analysis(reply_fn, ticker, perfil)`, pasándole una función de respuesta distinta según el origen:

- `handle_text` → `_run_analysis(update.message.reply_text, resolved, perfil)` — `reply_fn` **envía un mensaje nuevo** cada vez que se invoca.
- `handle_disambiguation` → `_run_analysis(query.edit_message_text, ticker, perfil)` — `reply_fn` **edita** el mensaje de los botones de desambiguación ("Encontré varias coincidencias, elegí una:") cada vez que se invoca.

Esta diferencia es la base de la decisión de diseño de esta spec: como `reply_fn` ya es polimórfico (a veces "enviar", a veces "editar"), el patrón de carga se puede centralizar **una sola vez en `_run_analysis`**, sin tocar `handle_text` ni `handle_disambiguation`, y sin necesidad de pasarles `context.bot`/`chat_id` a ninguno de los dos. Esto cubre los dos flujos de entrada reales con una sola modificación.

---

## Estado actual

`src/investbot/query_handler.py`, función `_run_analysis` (líneas 354-364):

```python
async def _run_analysis(reply_fn, ticker: str, perfil: str) -> None:
    try:
        text = await fetch_and_analyze(ticker, clients, perfil)
    except (fmp_client.FMPError, treasury_client.TreasuryError) as exc:
        await reply_fn(str(exc))
        return
    except Exception:
        logger.exception("Error inesperado analizando %s", ticker)
        await reply_fn(GENERIC_ERROR_MSG)
        return
    await reply_fn(text, parse_mode="Markdown")
```

`reply_fn` se invoca **una sola vez**, siempre al final, con el resultado ya calculado. No hay ninguna interacción con Telegram entre el momento en que arranca `fetch_and_analyze` y el momento en que termina.

---

## Estado objetivo

`_run_analysis` invoca a `reply_fn` **dos veces** en el camino feliz:

1. **Antes** de llamar a `fetch_and_analyze`: envía/edita (según el `reply_fn` recibido) un mensaje de carga que menciona el ticker, y guarda el objeto `Message` que devuelve esa llamada.
2. **Después**, en vez de volver a llamar a `reply_fn`, edita ese mismo `Message` (`.edit_text(...)`) con el resultado final (éxito o error) — el usuario ve **un solo mensaje que cambia de contenido**, nunca dos mensajes separados.

Si cualquiera de los dos pasos de Telegram falla, el análisis en sí (`fetch_and_analyze`) no se ve afectado — nunca se bloquea ni se cancela por un fallo de la API de Telegram — y el resultado final siempre se intenta entregar por al menos una vía (ver "Decisión de diseño", punto 4).

---

## Decisión de diseño

### 1. Opción elegida: enviar + editar en el mismo mensaje (Opción 3 del planteo original, con un refinamiento)

Se descartan las otras dos opciones evaluadas:

**Opción 1 — `send_chat_action(TYPING)` en loop.**
- ✅ Ventajas: nativo de Telegram ("escribiendo…"), no deja ningún mensaje en el historial.
- ❌ Desventajas: se apaga solo a los ~5s, así que requiere una tarea `asyncio` en paralelo que lo repita mientras dure el análisis, con su propio manejo de cancelación (incluyendo el camino de excepción de `fetch_and_analyze`) para no dejar una tarea colgada. Requiere además pasar `context.bot` y `chat_id` a `_run_analysis`, que hoy no los recibe — cambia la firma y a ambos call sites (`handle_text` y `handle_disambiguation`). No muestra qué ticker se está analizando.
- 📌 Mejor cuando: hay múltiples pasos largos y no importa dar detalle de qué se está haciendo, o cuando ya existe infraestructura de tareas en paralelo en el bot (no es el caso hoy — el proyecto no tiene ninguna tarea de fondo).
- **Por qué se descarta acá:** la complejidad de la tarea repetitiva + cancelación no se justifica frente a la Opción 3, que resuelve lo mismo con menos superficie de fallo, sin cambiar ninguna firma, y mostrando el ticker.

**Opción 2 — mensaje de texto explícito, nunca editado.**
- ✅ Ventajas: la más simple de implementar (una sola llamada extra a `reply_fn`, sin guardar el `Message` devuelto).
- ❌ Desventajas: dado que Daniela es la única usuaria y consulta el bot de forma recurrente, cada consulta deja un mensaje "Analizando…" huérfano acumulándose en el historial del chat — ruido permanente que crece con el uso, sin nunca limpiarse.
- 📌 Mejor cuando: el historial del chat no importa (bots efímeros, canales de un solo uso) o cuando editar no es técnicamente viable.
- **Por qué se descarta acá:** el propio historial de chat de Daniela es donde vive el registro de consultas pasadas — ensuciarlo con mensajes muertos degrada la experiencia que se está tratando de mejorar.

**Opción 3 (elegida) — enviar el mensaje de carga y editarlo con el resultado.**
- ✅ Ventajas: un solo mensaje visible por consulta (igual que hoy), aparece de inmediato, muestra el ticker, no requiere tarea de fondo ni cambia la firma de `_run_analysis` ni de los call sites.
- ❌ Desventajas: dos llamadas a la API de Telegram en vez de una (impacto despreciable — Telegram no factura ni limita esto de forma relevante para un solo usuario); si el `edit` falla, hay un mensaje de fallback (ver punto 4) que sí puede duplicar el mensaje en el caso límite de que el edit falle pero el fallback funcione.
- 📌 Mejor cuando (y es el caso de este proyecto): un solo usuario, sin necesidad de UI "en vivo" tipo typing indicator, y donde mostrar el ticker que se está procesando aporta valor real.

### 2. Refinamiento sobre el planteo original de la Opción 3: reusar el `Message` devuelto, no una segunda llamada a `reply_fn`

El planteo original de la tarea sugería enviar el mensaje y "editarlo después" sin especificar el mecanismo. La forma correcta de hacerlo sin duplicar lógica entre `handle_text` y `handle_disambiguation` es:

```python
loading_msg = await reply_fn(LOADING_MSG.format(ticker=ticker))
# ... más adelante ...
await loading_msg.edit_text(final_text, **kwargs)
```

Esto funciona igual para los dos orígenes sin ninguna rama condicional en `_run_analysis`:

- Si `reply_fn` es `update.message.reply_text` (caso `handle_text`): la primera llamada **envía un mensaje nuevo** ("Analizando `TICKER`…") y devuelve ese `Message`; `loading_msg.edit_text(...)` edita ese mismo mensaje nuevo con el resultado final. Un solo mensaje visible, igual que hoy.
- Si `reply_fn` es `query.edit_message_text` (caso `handle_disambiguation`): la primera llamada **edita** el mensaje de los botones de desambiguación, reemplazándolo por "Analizando `TICKER`…", y devuelve ese `Message`; `loading_msg.edit_text(...)` lo vuelve a editar con el resultado final. El mensaje de botones se convierte, en dos pasos, en el resultado — cero mensajes nuevos.

Verificado contra el `.venv` del proyecto (`python-telegram-bot==21.6` instalado): `Message.reply_text(...) -> 'Message'` (siempre `Message`, sin unión de tipos). `CallbackQuery.edit_message_text(...) -> Union[Message, bool]` — el caso `bool` (`True`) solo ocurre cuando el callback viene de un **mensaje inline** (`inline_message_id` seteado en vez de `message`), algo que este bot nunca usa (todas las interacciones son sobre chats normales vía `ApplicationBuilder` + `CallbackQueryHandler` con botones adjuntos a un mensaje real, no resultados de `@bot` en modo inline). En este proyecto, entonces, ambas llamadas devuelven siempre un `telegram.Message` con método `.edit_text(...)`, así que el mismo código sirve para los dos orígenes sin `isinstance` ni ramas. Si en el futuro se agregara soporte a modo inline, este supuesto dejaría de valer y habría que revisar este punto — no es el caso hoy.

### 3. Texto del mensaje de carga

Constante `LOADING_MSG`, junto a las demás constantes de mensaje ya existentes al principio del archivo (`NO_ONBOARDING_MSG`, `RATE_LIMITED_MSG`, etc.):

```python
LOADING_MSG = "🔍 Analizando {ticker}, dame un toque..."
```

**Sin `parse_mode`** en esta llamada (a diferencia del mensaje final, que sí usa `parse_mode="Markdown"`). Se decide texto plano a propósito: evita depender de que el ticker nunca contenga un carácter especial de Markdown V1 (`_`, `*`, `` ` ``, `[`) — hoy no ocurre con tickers reales, pero no vale la pena la superficie de fallo (un `BadRequest` de Telegram por Markdown mal formado) para una ganancia visual mínima en un mensaje que va a desaparecer en segundos.

### 4. Manejo de fallos — best-effort explícito, sin excepciones genéricas

Dos puntos de fallo nuevos, cada uno con su propia guarda:

**a) Falla el envío/edit del mensaje de carga (primera llamada a `reply_fn`).**
Se envuelve en `try/except telegram.error.TelegramError` (no `except Exception` genérico — mismo criterio que ya aplicó `security` en `SDD_fmp_402_simbolo_premium.md` sobre no usar catch-all que enmascare bugs propios). Si falla, se loguea con `logger.warning` y `loading_msg` queda en `None`. El análisis sigue exactamente igual (`fetch_and_analyze` se llama de todos modos). Esto es **exactamente** el requisito explícito de Daniela: "si el envío del mensaje de carga falla... no debe tumbar ni bloquear el análisis real".

**b) Falla el edit final sobre el mensaje de carga (`loading_msg.edit_text(...)`).**
No estaba pedido explícitamente, pero se cubre porque dejar "Analizando…" congelado para siempre en el chat — sin que el resultado llegue nunca — es, desde la perspectiva de Daniela, indistinguible de un fallo silencioso (el mismo principio que motiva toda esta spec). Se envuelve también en `try/except telegram.error.TelegramError`; si falla, se hace **un único** intento de fallback llamando a `reply_fn(final_text, **kwargs)` directamente (sin loop de reintentos, sin backoff — fuera de alcance). Ese intento de fallback **no** se envuelve en un try/except adicional: si también falla, se comporta igual que el riesgo ya preexistente en el código actual (si `reply_fn(text, ...)` fallara hoy, tampoco estaría cubierto) — no se amplía la garantía más allá de lo que ya existía antes de esta spec.

Si `loading_msg` es `None` (falló el paso "a"), el flujo final es **idéntico al comportamiento actual**: se llama a `reply_fn(final_text, **kwargs)` una sola vez, sin ningún intento de edit.

### 5. `_run_analysis` no cambia de firma

`handle_text` (línea 337: `await _run_analysis(update.message.reply_text, resolved, perfil)`) y `handle_disambiguation` (línea 352: `await _run_analysis(query.edit_message_text, ticker, perfil)`) **no se modifican**. Toda la lógica nueva vive dentro de `_run_analysis`.

---

## Recomendación de testing — mocks vs. verificación manual

**Recomendación: tests automatizados con mocks de Telegram, mismo patrón que `test_query_handler.py` ya usa (`AsyncMock`, `SimpleNamespace`, `httpx.MockTransport`) — no alcanza con verificación manual.**

Justificación:

1. **Este cambio no es solo "agregar una interacción con la API de Telegram" — modifica un flujo de control ya cubierto por tests de regresión existentes.** `_run_analysis` pasa de invocar `reply_fn` una vez a invocarlo (o invocar `.edit_text` sobre su resultado) en un patrón de dos pasos con ramas de fallback. Los tests actuales que ya ejercitan ese código (`test_handle_text_resuelve_ticker_exacto_y_responde`, `test_handle_disambiguation_resuelve_y_responde`, `test_run_analysis_error_generico_no_crashea`) verifican el contenido del mensaje final vía `reply_text.call_args`/`edit_message_text.call_args` — con el cambio, ese contenido se mueve a `.edit_text.call_args` sobre el objeto devuelto. **Estos tres tests van a fallar tal como están** en cuanto se implemente esta spec — no es opcional actualizarlos, es un requisito de no-regresión.
2. **La lógica de fallback (punto 4) es exactamente el tipo de rama que la verificación manual no cubre de forma confiable** — no se puede provocar a demanda un fallo real de red hacia Telegram desde un chat real para confirmar que el análisis sigue corriendo igual. Con `AsyncMock(side_effect=telegram.error.TelegramError(...))` se simula en una línea.
3. **Encaja 100% con el patrón ya establecido en el proyecto**: cero interacción real con Telegram en ningún test existente — todo pasa por `AsyncMock`/`SimpleNamespace` (ver `_fake_text_update`, líneas 136-141 de `test_query_handler.py`) y `httpx.MockTransport` para FMP/FRED. No hace falta ningún mecanismo nuevo, solo reconfigurar el `return_value` de los mocks ya usados.

Verificación manual complementaria (no sustituta): **una sola corrida real contra el bot de Telegram de Daniela** para confirmar la percepción visual (que el mensaje efectivamente "parpadea" de carga a resultado sin verse raro, timing real incluido) — esto sí es genuinamente difícil de capturar en un mock, pero es un chequeo de UX puntual, no el mecanismo de verificación de corrección del código.

### Mocks que requieren reconfigurarse (no solo agregarse)

Los `AsyncMock()` usados hoy como `reply_text`/`edit_message_text` no tienen configurado un `return_value` con `.edit_text` awaitable — por default, el valor de retorno de un `AsyncMock()` es un `MagicMock` síncrono, y `await` sobre el resultado de llamar a un atributo de un `MagicMock` lanza `TypeError`. Hace falta:

```python
# antes (test_query_handler.py:138)
update.message = SimpleNamespace(text=text, reply_text=AsyncMock())

# después
update.message = SimpleNamespace(
    text=text,
    reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
)
```

Mismo patrón para `query.edit_message_text` en `test_handle_disambiguation_resuelve_y_responde` (línea 287).

---

## Criterios de aceptación

- [ ] En un caso de éxito (fixtures de Adobe, mismo patrón que `test_fetch_and_analyze_adobe_end_to_end`), al llamar a `handle_text` con un ticker exacto: `update.message.reply_text` es invocado **exactamente una vez**, con `LOADING_MSG.format(ticker="ADBE")` (o equivalente), sin `parse_mode`.
- [ ] En ese mismo caso, el resultado final (contiene "Adobe", "barata", etc.) llega a través de `.edit_text` sobre el objeto devuelto por esa primera llamada, **con** `parse_mode="Markdown"` — no a través de una segunda invocación de `reply_text`.
- [ ] Mismo par de criterios para `handle_disambiguation`: `query.edit_message_text` invocado una vez con el mensaje de carga (mencionando el ticker resuelto del callback), y el resultado final vía `.edit_text` sobre ese retorno.
- [ ] Caso error `FMPError`/`TreasuryError` (mismo patrón que `test_handle_text_fmp_error_en_search`, pero disparado dentro de `fetch_and_analyze`, no en `search_company`): el texto de la excepción llega vía `.edit_text`, no vía una segunda llamada a `reply_fn`.
- [ ] Caso error genérico (mismo patrón que `test_run_analysis_error_generico_no_crashea`): `GENERIC_ERROR_MSG` llega vía `.edit_text`, y el test sigue confirmando que la excepción original nunca se propaga sin capturar.
- [ ] **Resiliencia — falla el mensaje de carga:** con `reply_text` configurado con `side_effect=telegram.error.TelegramError("boom")`, `handle_text` completa igual: `fetch_and_analyze` es invocado con el mismo ticker (verificable con las fixtures de Adobe, confirmando que el resultado contiene "Adobe"), y el resultado final llega por una llamada directa a `reply_fn` (sin `.edit_text`, porque no hay `Message` que editar). Ninguna excepción se propaga fuera de `handle_text`.
- [ ] **Resiliencia — falla el edit final:** con `reply_text` devolviendo un mock cuyo `edit_text` tiene `side_effect=telegram.error.TelegramError("boom")`, se verifica que ocurre exactamente un intento de fallback: `reply_text` es invocado una **segunda** vez con el contenido final (mensaje de carga en la primera llamada, resultado en la segunda). Ninguna excepción se propaga fuera de `handle_text`.
- [ ] **Cero llamadas nuevas a FMP/FRED/Treasury.gov**: verificado por inspección — `fmp_client.py`, `treasury_client.py`, `peers.py`, `valuation.py`, `rules.py`, `summary.py`, `market_context.py`, `Clients` (dataclass) quedan sin ninguna modificación; el `httpx.MockTransport` de los tests de éxito recibe exactamente las mismas rutas que hoy (sin agregar ninguna ruta nueva al router de fixtures).
- [ ] Los tests que hoy retornan antes de llegar a `_run_analysis` (`test_handle_text_sin_onboarding_pide_start`, `test_handle_text_texto_muy_largo`, `test_handle_text_rate_limited`, `test_handle_text_sin_coincidencias`, `test_handle_text_multiples_coincidencias_muestra_botones`, `test_handle_text_fmp_error_en_search`) siguen pasando **sin modificar una sola línea** — no tocan el mensaje de carga porque nunca llegan a `_run_analysis`.
- [ ] `pytest -q` completo en verde, sin tests nuevos marcados `skip`/`xfail`.

---

## Artefactos a crear/modificar

- `src/investbot/query_handler.py` → agregar constante `LOADING_MSG`; import de `telegram.error.TelegramError`; modificar únicamente el cuerpo de `_run_analysis` (líneas 354-364) según la sección "Decisión de diseño". `handle_text` y `handle_disambiguation` quedan sin cambios (mismos call sites, misma firma).
- `tests/test_query_handler.py` → reconfigurar el `return_value` de los mocks en `_fake_text_update` (línea 138) y en `test_handle_disambiguation_resuelve_y_responde` (línea 287, `edit_message_text`); actualizar las aserciones de contenido final en `test_handle_text_resuelve_ticker_exacto_y_responde`, `test_handle_disambiguation_resuelve_y_responde` y `test_run_analysis_error_generico_no_crashea` para leer del `.edit_text` devuelto en vez de la llamada directa; agregar los tests nuevos listados en "Criterios de aceptación" (resiliencia de carga, resiliencia de edit, verificación del mensaje de carga en sí).

---

## Restricciones

- **No se implementa la Opción 1** (`send_chat_action`/typing loop) ni ninguna tarea `asyncio` de fondo — descartada explícitamente en "Decisión de diseño".
- **No se cambia la firma de `_run_analysis`** ni la de los call sites en `handle_text`/`handle_disambiguation` — no se les pasa `context.bot` ni `chat_id`.
- **No se toca `fetch_and_analyze`, `Clients`, `fmp_client.py`, `treasury_client.py`, `peers.py`, `valuation.py`, `rules.py`, `summary.py` ni `market_context.py`** — cero impacto en el presupuesto de requests de FMP/FRED/Treasury.gov (esta spec es 100% interacción con la API de Telegram).
- **No se agrega reintento con backoff** más allá del único intento de fallback descrito en el punto 4 de "Decisión de diseño" — si ese fallback también falla, el comportamiento es el mismo riesgo (no cubierto) que ya existía antes de esta spec para la entrega del mensaje final.
- **No se modifica el comportamiento de `reply_markup`** en la edición del mensaje de desambiguación: si hoy los botones inline sobreviven a un `edit_message_text` que no los vuelve a pasar explícitamente, ese comportamiento (heredado, no introducido por esta spec) queda igual. Se documenta como limitación conocida, no como algo a resolver acá — si se decide atenderlo, es un backlog item para un pipeline run separado (Regla 5 de `pipeline.md`, Scope Freeze).
- **No requiere dependencias nuevas** — `telegram.error.TelegramError` ya viene con `python-telegram-bot==21.6`, ya presente en `requirements.txt`.
- **No se toca el rate limiter, el onboarding ni la validación de input** (`normalize_query`, `MAX_INPUT_LENGTH`, etc.).
- Esta spec no reabre ninguna decisión de `SDD_investbot_mvp.md` (stack, modelos de valoración, presupuesto de requests) — es un cambio aislado sobre un único bloque de un archivo de producción y su archivo de tests.

---

## Nota para `security`

Superficie nueva: manejo de excepciones alrededor de dos llamadas a la API de Telegram que hoy no existían. No hay dato sensible nuevo en juego (el ticker ya está validado/resuelto antes de llegar a `_run_analysis`, no es input crudo del usuario). El único punto que vale la pena confirmar es el ya incorporado en el diseño: que el `try/except` use `telegram.error.TelegramError` (acotado) y no `except Exception` (catch-all) en los dos puntos nuevos, para no enmascarar bugs propios de la lógica de `_run_analysis` bajo la etiqueta de "falló Telegram, es best-effort". Si `security` no encuentra nada adicional, puede pasar directo a `qa` sin agregar una sección propia — no es obligatorio bloquear el pipeline en este paso para un cambio de este tamaño (mismo criterio de proporcionalidad que ya aplicó `architect` en specs anteriores de este proyecto).

---

## Criterios QA para Spec: Mensaje de "cargando" durante el análisis [Iter-1]

**Rol:** `qa` (pre-implementación — Momento 1). Verificado contra el código real (`src/investbot/query_handler.py`, líneas 279, 339, 354-364) y `tests/test_query_handler.py` completo. Todas las referencias a líneas que da `architect` en este documento fueron confirmadas exactas contra el archivo actual (no hay drift). El siguiente paso es `implementer`; no se toca `src/` ni `tests/` en este momento.

### Tipo de prueba principal

**Unit testing con mocks** (`AsyncMock` / `SimpleNamespace` / `httpx.MockTransport`), mismo patrón ya establecido en `test_query_handler.py`. Justificación: `_run_analysis` es lógica de negocio aislable, y su única dependencia externa (la API de Telegram) es inestable/lenta desde la perspectiva de un test — exactamente el caso donde el principio "Mock vs Real" del skill indica mockear. No aplica integration/E2E real porque no hay infraestructura de bot de prueba en CI y el propio `architect` ya dejó esto resuelto en "Recomendación de testing" — este bloque no lo reabre, lo formaliza en criterios verificables.

### Cobertura mínima requerida

- [ ] `--cov-fail-under=75` a nivel de proyecto (ya exigido por el README) se mantiene en verde — **alcanza como piso**, no requiere subirlo.
- [ ] Adicional específico para este feature, por ser flujo principal de usuario (tabla de riesgo del skill: "Alto → ≥90%"): `_run_analysis` (líneas 354-364 actuales, va a crecer) queda con **cobertura de líneas ≥ 90%** medida solo sobre esa función — verificable con `--cov-report=term-missing` y mirando el rango de líneas en el output, no hace falta un flag nuevo.
- [ ] **Branch coverage por checklist manual, no por tooling**: el proyecto no tiene `--cov-branch` habilitado (ni en `pytest.ini` ni en el comando del README) y no corresponde agregarlo ahora — sería una decisión de tooling nueva fuera del alcance de esta spec (Regla 5, Scope Freeze). En su lugar, las 4 combinaciones de la matriz de la sección "Casos obligatorios" de abajo deben tener **cada una** al menos un test dedicado; QA verifica esto por inspección de la suite, no por reporte de cobertura.
- [ ] Los 6 criterios de aceptación ya listados por `architect` (sección "Criterios de aceptación" arriba) están cada uno cubiertos por al menos un test — mapeo test→criterio incluido más abajo.

### Casos obligatorios

Matriz de las 4 combinaciones posibles del patrón enviar-y-editar (las 4 deben existir como test):

| # | Envío del mensaje de carga | Edit final | Comportamiento esperado | Test |
|---|---|---|---|---|
| 1 | OK | OK | camino feliz: 1 llamada a `reply_fn` (carga) + `.edit_text` (resultado) | ajustar `test_handle_text_resuelve_ticker_exacto_y_responde` |
| 2 | OK | OK | mismo patrón, call site de desambiguación | ajustar `test_handle_disambiguation_resuelve_y_responde` |
| 3 | **falla** (`TelegramError`) | n/a (`loading_msg=None`) | análisis sigue igual; entrega final por `reply_fn` directo, sin `.edit_text` | **nuevo**: `test_handle_text_falla_envio_mensaje_carga_no_bloquea_analisis` |
| 4 | OK | **falla** (`TelegramError`) | 1 intento de fallback: 2da llamada a `reply_fn` con el contenido final | **nuevo**: `test_handle_text_falla_edit_final_hace_fallback_a_reply_fn` |

- [ ] **Happy path (fila 1 y 2 de la matriz)**: cubre los primeros dos criterios de aceptación de `architect` para ambos call sites — `reply_fn` invocado **exactamente una vez** con `LOADING_MSG.format(ticker=...)` sin `parse_mode`, y el resultado final llega vía `.edit_text` sobre el retorno de esa llamada, con `parse_mode="Markdown"` en el caso de éxito.
- [ ] **Caso límite — falla el envío de carga (fila 3)**: `reply_text` con `side_effect=telegram.error.TelegramError("boom")`. Asserts: `fetch_and_analyze` corre igual (usar fixtures Adobe reales, no mockear `fetch_and_analyze`, para probar la integración real del bypass) → el resultado final contiene "Adobe"; `reply_text` fue **awaited exactamente 2 veces** (1: intento de carga que lanza excepción pero sí se registra como llamada awaited; 2: entrega directa del resultado final, sin pasar por `.edit_text` porque no hay `Message`); ninguna excepción se propaga fuera de `handle_text`.
- [ ] **Caso de error — falla el edit final (fila 4)**: `reply_text` devuelve un mock cuyo `edit_text` tiene `side_effect=telegram.error.TelegramError("boom")`. Assert: `reply_text.await_count == 2` (carga + fallback), la 2da llamada trae el contenido final; `edit_text` fue invocado (y falló) exactamente una vez, sin reintentos adicionales; ninguna excepción se propaga.
- [ ] **Caso de alto riesgo de negocio**: cero llamadas nuevas a FMP/FRED/Treasury.gov — ya cubierto por criterio de `architect`, se verifica reusando el mismo `httpx.MockTransport` de `_adobe_router` sin agregar rutas nuevas (si se agregara una ruta no contemplada, el router devuelve 404 y el test de happy path fallaría con datos incompletos — es una guarda implícita, no hace falta un assert explícito de "rutas exactas").
- [ ] **Regresión — los 6 tests que retornan antes de `_run_analysis`** (`test_handle_text_sin_onboarding_pide_start`, `test_handle_text_texto_muy_largo`, `test_handle_text_rate_limited`, `test_handle_text_sin_coincidencias`, `test_handle_text_multiples_coincidencias_muestra_botones`, `test_handle_text_fmp_error_en_search`) pasan sin tocar una línea — confirmado por inspección: ninguno llega a `_run_analysis`, y el cambio propuesto en `_fake_text_update` (agregar `return_value` con `.edit_text`) es inocuo para ellos porque ninguno inspecciona el valor de retorno de `reply_text`, solo `call_args` sobre la llamada en sí.

### Tests existentes a ajustar — mock shape exacto

1. **`_fake_text_update` (línea 138)** — cambiar el default:
   ```python
   update.message = SimpleNamespace(
       text=text,
       reply_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
   )
   ```
   Esto afecta a **todos** los tests que usan el helper, pero es seguro para los 6 que no llegan a `_run_analysis` (ver arriba).

2. **`test_handle_text_resuelve_ticker_exacto_y_responde` (líneas 195-206)** — reescribir el bloque de asserts final:
   ```python
   update.message.reply_text.assert_awaited_once_with(
       query_handler.LOADING_MSG.format(ticker="ADBE")
   )
   loading_msg = update.message.reply_text.return_value
   loading_msg.edit_text.assert_awaited_once()
   args, kwargs = loading_msg.edit_text.call_args
   assert "Adobe" in args[0]
   assert kwargs.get("parse_mode") == "Markdown"
   ```

3. **`test_handle_disambiguation_resuelve_y_responde` (líneas 279-296)** — reconfigurar el `query` de la misma forma:
   ```python
   query = SimpleNamespace(
       data="tk:ADBE",
       answer=AsyncMock(),
       edit_message_text=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
   )
   ...
   query.edit_message_text.assert_awaited_once()
   loading_msg = query.edit_message_text.return_value
   loading_msg.edit_text.assert_awaited_once()
   args, kwargs = loading_msg.edit_text.call_args
   assert "Adobe" in args[0]
   ```

4. **`test_run_analysis_error_generico_no_crashea` (líneas 298-329)** — el bloque final cambia de:
   ```python
   args, kwargs = update.message.reply_text.call_args
   assert args[0] == query_handler.GENERIC_ERROR_MSG
   ```
   a:
   ```python
   update.message.reply_text.assert_awaited_once_with(
       query_handler.LOADING_MSG.format(ticker="ADBE")
   )
   loading_msg = update.message.reply_text.return_value
   args, kwargs = loading_msg.edit_text.call_args
   assert args[0] == query_handler.GENERIC_ERROR_MSG
   assert "parse_mode" not in kwargs
   ```

### Testabilidad

- [x] `reply_fn` ya es inyectable (parámetro de `_run_analysis`) — no requiere cambios de diseño para ser mockeable.
- [x] El efecto secundario nuevo (2 llamadas a la API de Telegram en vez de 1) está completamente aislado detrás de `reply_fn`/`.edit_text` — sin necesidad de tocar `fetch_and_analyze` ni ningún cliente HTTP real.
- [x] No hay lógica nueva en constructores ni en métodos estáticos — todo vive en `_run_analysis`, una función ya testeada hoy.
- [ ] Pendiente de confirmar en implementación: que `telegram.error.TelegramError` se importe de forma que los tests puedan construir instancias para `side_effect` (`from telegram.error import TelegramError` o `import telegram.error`) — no es un cambio de diseño, es un detalle de import que hay que verificar no quede atado a un alias interno no exportado.

### Criterio de exit de QA

- Todos los tests pasan (`pytest -q` en verde), incluidos los 4 nuevos/ajustados de la matriz.
- `--cov-fail-under=75` en verde a nivel de proyecto; `_run_analysis` con ≥90% de líneas cubiertas (ver sección de cobertura).
- Sin tests `skip`/`xfail`.
- Flaky rate = 0 — como es 100% mocks sin `sleep`/timing real, no debería haber no-determinismo; si aparece, es señal de un `AsyncMock` mal configurado, no de flakiness genuina.

### Gaps detectados en la spec — bloquean un test 100% determinístico si no se resuelven antes de implementar

1. **`**kwargs` por rama no está unificado en ningún lugar del documento.** El pseudocódigo de la sección "Refinamiento" (`loading_msg.edit_text(final_text, **kwargs)`) da a entender que `kwargs` varía según qué texto se está enviando, pero nunca se enumera la tabla completa. Del código actual se infiere, pero no queda escrito en ningún lado como contrato. Se deja fijado acá para que `implementer` y `qa` (Momento 2) trabajen contra el mismo criterio, sin inferencias:

   | Rama | Texto | `kwargs` |
   |---|---|---|
   | Mensaje de carga (ambas llamadas iniciales) | `LOADING_MSG.format(ticker=ticker)` | `{}` (sin `parse_mode`, punto 3 de la spec) |
   | Éxito | `text` (resultado de `fetch_and_analyze`) | `{"parse_mode": "Markdown"}` |
   | `FMPError`/`TreasuryError` | `str(exc)` | `{}` |
   | Error genérico | `GENERIC_ERROR_MSG` | `{}` |

   Sin esta tabla explícita, un test que asserteara `parse_mode` en la rama de error (o que no lo asserteara en absoluto) no tendría con qué compararse — este gap queda **cerrado** con la tabla de arriba; los tests de error deben incluir `assert "parse_mode" not in kwargs` (ya reflejado en el ajuste de `test_run_analysis_error_generico_no_crashea` arriba).

2. **El conteo exacto de llamadas a `reply_fn` en el escenario "falla el envío de carga" no estaba numerado.** La spec dice "el resultado final llega por una llamada directa a `reply_fn`" pero no aclara si eso es la 1ª llamada real (porque la fallida "no cuenta") o la 2ª (porque `AsyncMock` sí registra la llamada aunque el `await` lance excepción). Es la 2ª — un `AsyncMock(side_effect=...)` registra la invocación en `call_args_list`/`await_count` en el momento en que se llama, independientemente de que el `await` posterior lance. **Cerrado** con el criterio explícito arriba: `reply_text.await_count == 2`.

3. **No hay test que confirme que el `except` nuevo NO es catch-all** (es decir, que una excepción que no sea `telegram.error.TelegramError` durante el envío del mensaje de carga sí se propaga). No estaba en los criterios de aceptación originales de `architect`, pero es exactamente el punto que `security` marcó como el único a vigilar en su nota. Se agrega como caso nuevo de este Momento 1 (no es un caso inventado en Momento 2, que sí estaría prohibido por el skill):
   - [ ] **Nuevo — `test_handle_text_excepcion_no_telegram_en_envio_carga_se_propaga`**: con `reply_text` con `side_effect=ValueError("no es un TelegramError")`, verificar que `handle_text` deja propagar esa excepción (no la traga silenciosamente como best-effort). Esto es una guarda de regresión: si en el futuro alguien amplía el `except TelegramError` a `except Exception`, este test lo detecta.

4. **Orden de guardado de `loading_msg` cuando `reply_fn` es awaited pero devuelve `None` explícitamente (no lanza, pero retorna `None`)** — caso no contemplado ni por `architect` ni acá arriba. No es el comportamiento de `Message.reply_text`/`CallbackQuery.edit_message_text` documentado (siempre devuelven `Message` o, en el caso `bool` ya descartado, `True`), así que **no se considera un gap real** — se menciona solo para dejar constancia de que se evaluó y se descarta explícitamente (no requiere test: sería testear un contrato de la librería que la spec ya verificó no aplica a este proyecto).

Con los gaps 1-3 resueltos arriba (tabla de `kwargs`, conteo de llamadas, test de no-catch-all), la spec queda con superficie suficiente para que `implementer` escriba código determinístico y `qa` (Momento 2) valide sin ambigüedad.
