# Spec: Control de acceso multi-usuario (hasta 3 chat_id autorizados) [Iter-1]

**Rol:** `architect` (spec base)
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` — revisar si el modelo fail-closed sigue siendo sólido con múltiples IDs, y si el rate-limit agregado (compartido, no per-usuario) sigue siendo aceptable con hasta 3 usuarios reales.
**Estado:** spec nueva, sin iteraciones previas. No hay scope freeze todavía.

---

## Contexto

Hoy `TELEGRAM_ALLOWED_CHAT_ID` autoriza a exactamente **un** chat_id (Daniela). Daniela quiere extender el control de acceso a **hasta 3 usuarios autorizados** (ella + 2 personas más), manteniendo el mismo modelo de seguridad: **fail-closed** — si la configuración está vacía, ausente o inválida, el proceso no arranca en modo permisivo. Nunca "todos permitidos".

Este cambio es puramente de control de acceso. No introduce roles, no introduce datos por-usuario nuevos más allá de lo que ya exista, y no toca el resto de la lógica de negocio del bot (onboarding, resolución de ticker, valoración, etc.).

---

## Estado actual

- `src/investbot/security.py`:
  - `get_allowed_chat_id(env)` (líneas ~35-55): lee `TELEGRAM_ALLOWED_CHAT_ID`, hace `int(raw.strip())`, devuelve **`int`**. Lanza `ConfigurationError` (fail-closed) si la variable no está seteada, está vacía, o no es parseable como entero.
  - `is_authorized_chat(update, allowed_chat_id)` (líneas ~58-67): recibe `allowed_chat_id: int`, compara `chat.id != allowed_chat_id` (igualdad 1 a 1) y exige `chat.type == "private"`.
  - `build_chat_id_gate(allowed_chat_id)` (líneas ~70-94): handler global `group=-1` que construye la coroutine de gate y corta con `ApplicationHandlerStop` cualquier update no autorizado. Recibe `allowed_chat_id` tal cual se lo pasen y lo reenvía a `is_authorized_chat`.
  - `InMemoryRateLimiter` (líneas ~97-119): rate-limit agregado en memoria, `key: str` arbitraria, no tiene ninguna dependencia de `allowed_chat_id` — es un componente separado.
- `src/investbot/bot.py`:
  - Línea 100: `allowed_chat_id = security.get_allowed_chat_id()` en `main()`, antes de cualquier otra validación de entorno — si lanza, el proceso termina con traceback y exit code ≠ 0 (fail-closed).
  - Línea 50: parámetro `allowed_chat_id: int` de `build_application(...)`.
  - Línea 66: `TypeHandler(Update, security.build_chat_id_gate(allowed_chat_id))` registrado en `group=-1`, cubriendo todos los tipos de update antes que `onboarding` y `query_handler`.
  - Línea 87: `rate_limiter = security.InMemoryRateLimiter(...)` — instancia única, agregada, pasada a `query_handler.build_query_handlers`. No depende de `allowed_chat_id`.
  - Línea 135: log de arranque incluye `allowed_chat_id=%s` en el mensaje de info.
- `.env.example`: documenta `TELEGRAM_ALLOWED_CHAT_ID` como "chat_id de Daniela (único usuario autorizado)".
- `tests/test_security.py`: 5 tests sobre `get_allowed_chat_id` que asumen retorno `int` (líneas 19-39), 4 tests sobre `is_authorized_chat` que pasan un `int` como segundo argumento (líneas 53-67), y tests de `build_chat_id_gate` que lo instancian con un `int` (líneas 76-100).
- `tests/test_bot.py`: usa `monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")` (líneas 24, 30, 38, 106, 130) y pasa `allowed_chat_id=12345` directamente a `build_application` en al menos 2 puntos (líneas 50, 92).
- `tests/test_onboarding.py` y `tests/test_query_handler.py`: **verificado, no referencian `security.`, `allowed_chat_id` ni `build_application`** — no requieren cambios por este SDD.

---

## Estado objetivo

1. `TELEGRAM_ALLOWED_CHAT_ID` acepta **uno o varios** chat_id, separados por coma (CSV), sin romper el valor actual de un solo entero ya desplegado en el VPS.
2. `get_allowed_chat_id(env)` devuelve un **`frozenset[int]`** (nunca vacío) con todos los chat_id autorizados, en vez de un `int` único.
3. `is_authorized_chat(update, allowed_chat_ids)` valida **membership** (`chat.id in allowed_chat_ids`) en vez de igualdad 1 a 1, manteniendo la validación de `chat.type == "private"`.
4. `build_chat_id_gate(allowed_chat_ids)` recibe el `frozenset[int]` y lo reenvía a `is_authorized_chat` sin cambiar su contrato de `ApplicationHandlerStop` / logging.
5. El modelo fail-closed se preserva exactamente para el caso de un solo ID, y se extiende explícitamente a cada caso edge de CSV (ver "Casos edge — comportamiento exacto").
6. `bot.py` no cambia su flujo de arranque (`main()` sigue leyendo `allowed_chat_id` antes que el resto de variables de entorno) — solo cambia el tipo que fluye desde `get_allowed_chat_id()` hasta `build_application(...)` y de ahí a `build_chat_id_gate(...)`.
7. El rate limiter (`InMemoryRateLimiter`) **no cambia** — sigue siendo agregado (cupo compartido entre todos los chat_id autorizados), no per-usuario. Ver "Decisión de diseño — rate limiter" para la justificación explícita de por qué no se separa por usuario en este SDD.
8. `.env.example` documenta el nuevo formato CSV, mantiene backward compatibility explícita para el valor de un solo entero.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch)*

### 1. Nombre de variable de entorno: mantener `TELEGRAM_ALLOWED_CHAT_ID`, aceptar CSV. No crear variable nueva.

**Decisión: reutilizar `TELEGRAM_ALLOWED_CHAT_ID`, con formato CSV de enteros (`"111,222,333"`), en vez de introducir `TELEGRAM_ALLOWED_CHAT_IDS` (plural) o una variable separada.**

Justificación:
- **Backward compatibility real y verificada**: el valor hoy desplegado en el VPS es un único entero (ej. `"12345"`). Un string con un solo entero **es** un CSV válido de un elemento — `"12345".split(",")` produce `["12345"]`. No hace falta ninguna migración de `.env` en el VPS para que el bot siga arrancando exactamente igual que hoy. Si se creara una variable nueva, habría que mantener las dos (la vieja como fallback, o forzar la migración manual del `.env` de producción antes de desplegar) — complejidad innecesaria para este cambio.
- **Una sola fuente de verdad**: mantener dos variables (`TELEGRAM_ALLOWED_CHAT_ID` singular legado + `TELEGRAM_ALLOWED_CHAT_IDS` plural nuevo) obliga a decidir qué pasa si ambas están seteadas (¿se combinan? ¿una gana? ¿es un error?) — una superficie de ambigüedad que no aporta valor de seguridad, solo complejidad de parseo y de documentación.
- **Costo de la decisión**: el nombre de la variable ya no describe exactamente "un chat_id", sino "uno o más". Se acepta ese costo cosmético (el nombre no cambia) a cambio de compatibilidad total sin migración. `implementer` actualiza el comentario en `.env.example` para reflejar el nuevo formato, no el nombre de la variable.
- **Alternativa descartada**: `TELEGRAM_ALLOWED_CHAT_IDS` (variable nueva, plural) — se descarta porque exige decidir un período de transición o doble-lectura (`IDS` con fallback a `ID`), lo cual es más superficie de bug fail-open que el approach elegido, para un beneficio (nombre gramaticalmente más preciso) puramente estético.

### 2. Tipo de retorno de `get_allowed_chat_id`: `frozenset[int]`, no `list[int]` ni `set[int]`.

- `frozenset` en vez de `set` mutable: una vez validado al arranque, el conjunto de IDs autorizados no debe ser mutable en runtime — evita que un bug en otro módulo (o un test descuidado) modifique el conjunto después de la validación fail-closed inicial. Es un valor de configuración inmutable, igual que el `int` que devolvía antes.
- `frozenset` en vez de `list`: la operación central es membership check (`in`) en cada update entrante — con como mucho 3 elementos la diferencia de performance es irrelevante, pero `frozenset` comunica la intención (conjunto sin orden, sin duplicados) más claramente que una lista, y previene un bug futuro de comparar posición/índice.

### 3. `is_authorized_chat`: cambia de comparación de igualdad a membership.

`chat.id != allowed_chat_id` (línea 63 actual) pasa a `chat.id not in allowed_chat_ids`. El resto de la función (chequeo de `chat is None`, chequeo de `chat.type == "private"`) no cambia. La firma del segundo parámetro cambia de `allowed_chat_id: int` a `allowed_chat_ids: frozenset[int]` — se renombra el parámetro (singular → plural) para que el código sea autoexplicativo; esto es un cambio de nombre de parámetro, no solo de tipo, y `implementer` debe propagarlo consistentemente en `security.py` (incluyendo `build_chat_id_gate`).

### 4. `build_chat_id_gate`: mismo contrato, nuevo tipo de parámetro.

Sin cambios de comportamiento — solo el parámetro pasa de `allowed_chat_id: int` a `allowed_chat_ids: frozenset[int]`, reenviado tal cual a `is_authorized_chat`. El logging de rechazo (línea 84 actual, `"Update rechazado — chat_id no autorizado: %s"`) no cambia — sigue logueando el `chat_id` individual del update rechazado, no la lista completa de autorizados (no hay razón de seguridad para loguear el conjunto autorizado en cada rechazo, y hacerlo sería ruido).

### 5. Rate limiter: NO se toca. Se documenta como decisión explícita, no como implementación.

Daniela pidió explícitamente no tocar `InMemoryRateLimiter` ni la lógica de negocio del resto del bot en este SDD, y que cualquier riesgo de compartir cupo se anote como decisión, no se implemente. Análisis:

- **Riesgo de cupo compartido**: con 1 usuario, el límite de `max_requests=10` por `window_seconds=60.0` (línea 87 de `bot.py`) protegía a Daniela de sus propios loops/dobles-tap accidentales, y en menor medida de un token filtrado. Con hasta 3 usuarios reales compartiendo el mismo cupo agregado (misma `key` en `InMemoryRateLimiter.allow`, según cómo `query_handler` la construya hoy), un uso intensivo simultáneo de 2 de los 3 usuarios autorizados podría agotar el cupo de FMP (250 req/día, ver `SDD_investbot_mvp.md`) o el rate-limit local más rápido de lo que ocurriría con un solo usuario, afectando a los otros 2 usuarios legítimos (denegación de servicio "amistosa", no maliciosa).
- **Por qué no se resuelve acá**: separar el rate limiter por chat_id (`key = f"{chat_id}"` en vez de una key global) es un cambio de comportamiento de negocio/UX (cuotas por usuario) fuera del alcance de "control de acceso multi-usuario" que pidió Daniela para este SDD. Requiere decidir: ¿cupo igual para los 3? ¿prioridad de Daniela sobre los otros 2? ¿qué pasa si un usuario agota su cupo pero el de FMP global sigue disponible? Son preguntas de producto, no de seguridad de acceso.
- **Decisión explícita**: el rate limiter sigue siendo agregado (cupo compartido entre los hasta 3 chat_id autorizados) en este SDD. Si Daniela confirma que quiere cupo per-usuario, es un **SDD separado** con su propio análisis de UX/negocio — no se implementa como parte de este cambio.
- **No es un riesgo de seguridad de autenticación/autorización** (el foco de este SDD) — es un riesgo de disponibilidad/fairness entre usuarios ya autorizados, de menor severidad que un fallo fail-open del gate.

### 6. Backward compatibility de `.env.example`

`.env.example` se actualiza para documentar el formato CSV y aclarar que un solo entero sigue siendo válido, sin requerir ninguna acción en el `.env` real del VPS a menos que Daniela decida agregar los otros 2 usuarios.

---

## Formato del CSV y casos edge — comportamiento exacto

Todos los casos deben seguir lanzando `ConfigurationError` (fail-closed) salvo los explícitamente marcados como válidos. Ninguno debe resultar en "modo permisivo" (conjunto vacío interpretado como "todos permitidos", o excepción silenciada).

| Caso | Valor de ejemplo | Comportamiento |
|---|---|---|
| Un solo entero (compatibilidad con el desplegado hoy) | `"12345"` | Válido. `frozenset({12345})`. |
| Múltiples enteros separados por coma | `"111,222,333"` | Válido. `frozenset({111, 222, 333})`. |
| Espacios alrededor de cada elemento | `" 111 , 222 ,333 "` | Válido tras `strip()` por elemento (mismo trato que el `raw.strip()` que ya existe hoy para el caso de un solo entero). `frozenset({111, 222, 333})`. |
| Variable ausente | no seteada | `ConfigurationError` — mismo mensaje/comportamiento que hoy. |
| Variable vacía o solo espacios | `""`, `"   "` | `ConfigurationError` — mismo mensaje/comportamiento que hoy. |
| Un elemento no numérico | `"111,abc,333"` | `ConfigurationError`. Mensaje debe identificar el elemento inválido y su valor crudo (ej. incluir `abc` en el mensaje), igual que hoy identifica el valor completo cuando falla el `int()`. |
| Elemento vacío por coma duplicada o trailing comma | `"111,,222"`, `"111,222,"`, `",111,222"` | `ConfigurationError`. Un elemento vacío tras el split no es "se ignora silenciosamente" — es una configuración ambigua (¿typo? ¿coma de más?) y debe fallar explícito, consistente con el principio fail-closed de esta spec. |
| Duplicados | `"111,111,222"` | Válido — `frozenset` deduplica naturalmente (`frozenset({111, 222})`). No es un error; loguear a nivel `INFO` (no `WARNING`, no bloqueante) que se detectaron duplicados es opcional y queda a criterio de `implementer`, no es criterio de aceptación bloqueante. |
| Más de 3 IDs (ej. 5) | `"111,222,333,444,555"` | Válido — **no hay límite duro de cantidad en este SDD**. Daniela pidió "hasta 3" como caso de uso esperado, no como tope técnico que deba enforcearse con una excepción. Si en el futuro se quiere un tope duro por razones de seguridad (ej. evitar que alguien con acceso al `.env` amplíe el círculo de autorizados sin revisión), es una decisión de producto separada — no se implementa en este SDD. `implementer` no debe agregar un `len(...) > 3` que lance error: eso sería un criterio inventado, no pedido. |
| Un solo elemento vacío tras strip pero coma presente (ej. `","`) | `","` | `ConfigurationError` — dos elementos vacíos, mismo trato que "elemento vacío" arriba. |
| Enteros negativos o cero | `"0"`, `"-100"` | Válido en cuanto a parseo — `chat_id` de Telegram puede ser negativo (grupos/canales) aunque este bot solo opera en chat privado; no hay razón de negocio para rechazar un entero negativo a nivel de parseo. La validación de `chat.type == "private"` en `is_authorized_chat` sigue siendo la barrera funcional para chats no privados, no el parseo de la variable de entorno. |

**Regla general de implementación sugerida** (no vincula la implementación exacta, `implementer` puede ajustar mientras cumpla la tabla anterior): split por `,`, `strip()` por elemento, rechazar si algún elemento resulta en string vacío tras el strip, intentar `int()` por elemento y agregar el elemento crudo al mensaje de error si falla, construir `frozenset` al final.

---

## Criterios de aceptación

### Parseo y fail-closed (`get_allowed_chat_id`)
- [ ] `get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "12345"})` devuelve `frozenset({12345})` (backward compatibility exacta con el caso de un solo entero).
- [ ] `get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "111,222,333"})` devuelve `frozenset({111, 222, 333})`.
- [ ] `get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": " 111 , 222 ,333 "})` devuelve `frozenset({111, 222, 333})` (espacios tolerados por elemento).
- [ ] Variable ausente o vacía (`""`, `"   "`) → `ConfigurationError`, mismo comportamiento fail-closed que la versión actual.
- [ ] Un elemento no numérico (ej. `"111,abc,333"`) → `ConfigurationError` con el valor inválido identificable en el mensaje.
- [ ] Elemento vacío por coma duplicada/trailing/leading (`"111,,222"`, `"111,222,"`, `",111,222"`, `","`) → `ConfigurationError` en los 4 casos.
- [ ] Duplicados (`"111,111,222"`) → válido, `frozenset({111, 222})`, sin excepción.
- [ ] Más de 3 IDs (ej. 5 elementos) → válido, sin excepción, sin tope duro implementado.
- [ ] Ningún caso de entrada inválida devuelve un `frozenset` vacío ni `None` — todo caso inválido lanza `ConfigurationError`, nunca hay un "modo permisivo" implícito.

### Autorización (`is_authorized_chat`)
- [ ] `is_authorized_chat(update_de(111, "private"), frozenset({111, 222, 333}))` → `True`.
- [ ] `is_authorized_chat(update_de(999, "private"), frozenset({111, 222, 333}))` → `False` (chat_id fuera del conjunto).
- [ ] `is_authorized_chat(update_de(111, "group"), frozenset({111, 222, 333}))` → `False` (chat_type no privado, aunque el ID esté autorizado).
- [ ] `is_authorized_chat(update_sin_chat(), frozenset({111}))` → `False` (chat `None`).
- [ ] Cada uno de los 3 chat_id autorizados por separado (111, 222, 333) es aceptado individualmente en chat privado — no alcanza con probar solo el primero del conjunto.

### Gate global (`build_chat_id_gate`)
- [ ] `build_chat_id_gate(frozenset({111, 222, 333}))` construye un gate que deja pasar updates de cualquiera de los 3 IDs en chat privado, y corta con `ApplicationHandlerStop` a cualquier otro.
- [ ] El comportamiento de `callback_query.answer()` best-effort y el logging de rechazo (sin exponer el conjunto completo de autorizados) se mantiene igual que hoy.

### Integración en `bot.py`
- [ ] `main()` sigue leyendo `allowed_chat_id = security.get_allowed_chat_id()` como primera validación de entorno (fail-closed antes que `TELEGRAM_BOT_TOKEN`/`FMP_API_KEY`), ahora recibiendo un `frozenset[int]`.
- [ ] `build_application(...)` acepta el `frozenset[int]` en el parámetro (renombrado a `allowed_chat_ids` para consistencia con `security.py`) y lo pasa a `security.build_chat_id_gate(...)` sin transformación adicional.
- [ ] El log de arranque (línea 135 actual) sigue emitiendo el conjunto autorizado de forma legible (ej. `chat_id=frozenset({111, 222, 333})` o una representación ordenada tipo `"111,222,333"` — a criterio de `implementer`, no crítico) sin que esto cambie el nivel de log ni exponga secretos.
- [ ] `docker compose` no requiere ningún cambio de configuración adicional más allá del valor de `TELEGRAM_ALLOWED_CHAT_ID` en `.env` para que el bot siga arrancando exactamente igual con el `.env` de producción actual (un solo entero), sin tocar `docker-compose.prod.yml`.

### Regresión — no tocar rate limiter ni lógica de negocio
- [ ] `InMemoryRateLimiter` no cambia de firma, comportamiento ni tests existentes.
- [ ] `onboarding.py`, `query_handler.py`, `valuation.py`, `rules.py`, y el resto de módulos de negocio no requieren ningún cambio — se verifica que `tests/test_onboarding.py` y `tests/test_query_handler.py` pasan sin modificación.
- [ ] Ningún test existente de `test_bot.py` que no dependa directamente del tipo de `allowed_chat_id` cambia de comportamiento (ej. tests de `TELEGRAM_BOT_TOKEN`/`FMP_API_KEY` ausentes siguen fallando igual).

### Documentación
- [ ] `.env.example` documenta `TELEGRAM_ALLOWED_CHAT_ID` con el nuevo formato CSV, ejemplo de múltiples IDs, y aclara explícitamente que un solo entero (formato actual) sigue siendo válido sin cambios.
- [ ] El comentario ya no dice "único usuario autorizado" — se actualiza a algo como "uno o más chat_id autorizados (CSV), ej. Daniela + hasta 2 personas más".

---

## Artefactos a crear/modificar

- `src/investbot/security.py` → `get_allowed_chat_id` cambia de retorno `int` a `frozenset[int]`, con parseo CSV y validación fail-closed por elemento (ver "Formato del CSV y casos edge"); `is_authorized_chat` cambia el segundo parámetro de `allowed_chat_id: int` a `allowed_chat_ids: frozenset[int]` y la comparación de igualdad a membership; `build_chat_id_gate` cambia el parámetro al mismo tipo, reenviado sin transformación. `InMemoryRateLimiter` y `log_conflict_error` **no se tocan**.
- `src/investbot/bot.py` → `main()` línea 100 (sin cambio de lógica, solo el tipo que fluye desde `get_allowed_chat_id()`); `build_application(...)` línea 50 (parámetro `allowed_chat_id: int` → `allowed_chat_ids: frozenset[int]`, propagar el rename a la línea 66 donde se pasa a `build_chat_id_gate`); línea 127 (call-site del rename); línea 135 (log de arranque, ajustar formato de impresión del conjunto si se considera necesario, no bloqueante).
- `.env.example` → actualizar comentario de `TELEGRAM_ALLOWED_CHAT_ID` (formato CSV, backward compatibility, ejemplo con 3 IDs).
- `tests/test_security.py` → actualizar los 5 tests de `get_allowed_chat_id` (líneas 19-39) para esperar `frozenset[int]` en vez de `int`; agregar tests nuevos para CSV multi-elemento, espacios, elemento no numérico, comas vacías/duplicadas/trailing, duplicados, y más de 3 IDs (ver tabla de casos edge — cada fila de la tabla debe tener al menos un test correspondiente); actualizar los 4 tests de `is_authorized_chat` (líneas 53-67) para pasar un `frozenset[int]` como segundo argumento en vez de un `int`, y agregar al menos un test con conjunto de 3 elementos verificando cada uno; actualizar los tests de `build_chat_id_gate` (líneas 76-100) para instanciarlo con `frozenset[int]`.
- `tests/test_bot.py` → los call-sites que pasan `allowed_chat_id=12345` directo a `build_application` (líneas 50, 92) deben actualizarse al nuevo nombre de parámetro `allowed_chat_ids` y tipo `frozenset[int]` (ej. `frozenset({12345})`); los tests que usan `monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")` (líneas 24, 30, 38, 106, 130) no requieren cambio de valor (siguen usando un solo entero, backward compatibility), pero sí deben seguir pasando con el nuevo tipo de retorno internamente — verificar que ningún assert compara `allowed_chat_id == 12345` esperando un `int` puro.
- `tests/test_onboarding.py`, `tests/test_query_handler.py` → **verificado, sin referencias a `security.`/`allowed_chat_id`/`build_application`; no requieren cambios.**

---

## Restricciones

- No se toca `InMemoryRateLimiter`: sigue siendo agregado (cupo compartido entre todos los chat_id autorizados), no per-usuario. Ver Decisión de diseño #5 — cualquier cambio a cupo per-usuario es un SDD separado.
- No se toca ninguna lógica de negocio fuera de `security.py` y los call-sites de tipo en `bot.py` — onboarding, resolución de ticker, valoración, persistencia SQLite quedan exactamente igual.
- No se introduce ninguna variable de entorno nueva — se reutiliza `TELEGRAM_ALLOWED_CHAT_ID` con formato CSV (Decisión de diseño #1).
- No se implementa ningún tope duro de cantidad de IDs autorizados (ej. rechazar si son más de 3) — "hasta 3" es el caso de uso esperado de Daniela, no un límite técnico a enforcear.
- No se implementa persistencia de usuarios autorizados en base de datos ni gestión dinámica (agregar/quitar usuarios sin reiniciar el proceso) — el conjunto de autorizados sigue siendo estático, leído una sola vez al arranque desde la variable de entorno, igual que el modelo actual de un solo ID. Cualquier gestión dinámica es fuera de scope de este SDD.
- No se agregan roles ni permisos diferenciados entre los hasta 3 usuarios (ej. "admin" vs "usuario") — los 3 tienen exactamente el mismo nivel de acceso que Daniela tiene hoy.
- Esta spec es solo la fase de diseño (`architect`). No implementar código todavía — falta pasar por `security` antes del scope freeze, dado que este es un cambio de superficie de autenticación/autorización.

---

## Handoff → security

### Specs producidas
- Esta spec (`SDD_multiusuario_chat_id.md`), Iter-1.

### Criterios de aceptación base
Ver sección "Criterios de aceptación" completa arriba, agrupados en: parseo y fail-closed, autorización, gate global, integración en `bot.py`, regresión (no tocar rate limiter ni lógica de negocio), documentación.

### Decisiones de diseño tomadas (no reabrir)
1. Reutilizar `TELEGRAM_ALLOWED_CHAT_ID` con formato CSV, sin variable nueva — backward compatibility total con el `.env` actual del VPS (Decisión #1).
2. `get_allowed_chat_id` devuelve `frozenset[int]`, nunca `list`/`set` mutable (Decisión #2).
3. `is_authorized_chat` y `build_chat_id_gate` cambian de comparación de igualdad a membership, mismo contrato de `ApplicationHandlerStop` (Decisiones #3-4).
4. Rate limiter permanece agregado/compartido — no se implementa cupo per-usuario en este SDD (Decisión #5, riesgo de fairness documentado explícitamente, no es un riesgo de autenticación).
5. Sin tope duro de cantidad de IDs autorizados, sin gestión dinámica, sin roles — ver "Restricciones".

### Foco esperado para `security`
- Confirmar que el modelo fail-closed cubre todos los casos edge de CSV listados en "Formato del CSV y casos edge" sin dejar una rama permisiva (ej. revisar si algún parseo con `try/except` demasiado amplio podría silenciar un error y caer en un conjunto vacío interpretado como "permitir todo" — riesgo ya mitigado en el diseño pero a confirmar en la implementación real).
- Evaluar si compartir el mismo `.env`/`TELEGRAM_ALLOWED_CHAT_ID` entre 3 personas reales (en vez de 1) cambia el nivel de ASVS o el análisis de "pérdida de control de cuenta de Telegram" ya documentado en `SDD_investbot_mvp.md` sección "Criterios de seguridad" — con 3 cuentas de Telegram distintas, la superficie de riesgo de SIM-swap/secuestro de sesión se triplica (3 cuentas en vez de 1), aunque el control de acceso en sí (comparación de ID) siga siendo válido.
- Confirmar o refinar la decisión de no separar el rate limiter por usuario (Decisión #5) — si `security` considera que el riesgo de fairness/DoS amistoso es mayor de lo estimado acá, debe anotarlo como criterio nuevo para un SDD separado, no implementarlo dentro de este.
- Revisar el mensaje de log de rechazo del gate (no debe cambiar para exponer el conjunto completo de autorizados a quien tenga acceso a los logs, más allá de lo que ya se loguea hoy del chat_id individual rechazado).

---

## Criterios de seguridad — agregado por `security` [Iter-1, 2026-07-30]

**Rol:** `security`. Esta sección **agrega** criterios de aceptación a la spec del `architect`; no reescribe las decisiones de diseño, la tabla de casos edge, los criterios de aceptación base ni las restricciones ya definidas — todo eso sigue siendo propiedad de `architect` y ninguna de sus 6 decisiones se reabre acá.

**Nivel de verificación (ASVS 5.0):** se mantiene el mismo criterio fijado en `SDD_investbot_mvp.md` — InvestBot no maneja datos de salud, **ASVS L1 general es suficiente**, con el mismo rigor de FoodMindAI en secretos/logging (no se relaja por ser "bot personal"). Este cambio no introduce datos nuevos ni cambia esa clasificación: sigue siendo control de acceso puro sobre un bot sin datos sensibles de terceros.

---

### 1. Cobertura del parseo CSV frente a casos adversariales

La tabla de casos edge de `architect` (sección "Formato del CSV y casos edge") ya cubre correctamente: ausente, vacío, no numérico, comas vacías/duplicadas/trailing, duplicados, y "más de 3 IDs sin tope". Se confirma que **ninguno de esos casos degrada a modo permisivo** — todos lanzan `ConfigurationError` salvo los explícitamente marcados como válidos. Se agregan 4 casos adversariales adicionales que la tabla no menciona explícitamente:

- **(A) chat_id negativo o cero — ya cubierto correctamente, se confirma el razonamiento.** Telegram usa IDs negativos para grupos/canales y nunca asigna `0` como chat_id real de ningún tipo de chat. Aceptar `"0"` o `"-100"` a nivel de parseo no abre ninguna vía de autorización real: `is_authorized_chat` sigue exigiendo `chat.type == "private"`, y ningún chat privado real tendrá jamás `chat.id == 0`. Una entrada `"0"` en el CSV es, en la práctica, una entrada "muerta" (nunca puede matchear), no un riesgo. **No se requiere ningún criterio nuevo acá**, solo se deja documentado el porqué.
- **(B) Dígitos Unicode no-ASCII — caso no cubierto, riesgo bajo pero real de confusión de configuración (no de bypass).** Verificado empíricamente: `int()` en Python acepta dígitos Unicode decimales de cualquier categoría `Nd` — `int("١١١")` (dígitos arábigo-índicos) devuelve `111`, e `int("１２３")` (dígitos fullwidth) devuelve `123`. Esto significa que un elemento del CSV escrito con dígitos no-ASCII (ej. copiado/pegado desde una fuente que normalizó números a otro script) **parsea silenciosamente a un entero válido**, sin lanzar `ConfigurationError`, aunque el operador probablemente no tenía intención de escribir ese script. No es un vector de bypass de autorización (el entero resultante sigue siendo un chat_id concreto, no "todos" ni un comodín) — es un riesgo de **integridad de configuración**: el operador podría creer que un chat_id no es válido/no fue aceptado y en realidad sí lo fue, o viceversa, generando confusión al depurar quién está autorizado. Igual de relevante: `str.strip()` de Python ya normaliza espacios Unicode no-ASCII como `\xa0` (NBSP) correctamente (verificado), así que ese sub-caso ya está cubierto sin cambios.
  - **Criterio nuevo:** cada elemento del CSV se valida con `elemento.isascii()` (o equivalente) antes de intentar `int()`; si contiene caracteres no-ASCII, se rechaza con `ConfigurationError` mencionando el elemento crudo — mismo tratamiento que "no numérico". Esto elimina la ambigüedad en vez de depender de que `int()` "adivine" una intención. **No bloqueante** para el diseño (no hay bypass de autorización), pero de costo trivial y se recomienda incluir en esta iteración para no dejar un comportamiento silencioso y no testeado.
  - Test nuevo: `get_allowed_chat_id(env={"TELEGRAM_ALLOWED_CHAT_ID": "١١١"})` y con `"１１１"` → `ConfigurationError` (una vez aplicado el criterio anterior). Si `implementer` decide no aplicar el guard de `isascii()`, como mínimo debe existir un test que documente el comportamiento actual (que sí parsea) para que no sea un caso no cubierto por tests.
- **(C) CSV extremadamente largo como vector de DoS al arrancar — analizado, no es un vector de ataque explotable en este modelo de amenaza.** `TELEGRAM_ALLOWED_CHAT_ID` se lee de `os.environ` (o del `env` inyectado en tests), es decir, **requiere acceso previo de escritura al `.env`/entorno del proceso en el VPS** — quien pueda inyectar un CSV de millones de elementos ya tiene control suficiente sobre el despliegue como para hacer daño mucho mayor (cambiar `TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, o el propio código). No existe una vía remota (Telegram, HTTP) para que un tercero no autorizado influya en el valor de esta variable de entorno. El parseo es lineal (`O(n)` en cantidad de elementos) y ocurre **una sola vez, al arranque**, no en el hot path de cada update — un CSV de 100k elementos añade como mucho unos milisegundos al arranque, no un DoS sostenido. **Veredicto: no bloqueante, no se requiere ningún límite de longitud/cantidad** — sería redundante con la Restricción ya explícita de `architect` de no imponer tope duro de cantidad de IDs, y agregar un límite "por las dudas" solo para este caso reintroduciría exactamente la ambigüedad que `architect` decidió evitar (Decisión #1, "más de 3 IDs... válido, no hay límite duro"). Se documenta el análisis para que quede constancia de que se evaluó explícitamente, no que se pasó por alto.
- **(D) Invariante de "nunca devolver conjunto vacío" — reforzar con un chequeo defensivo explícito, no solo con la tabla de casos.** La tabla ya garantiza que todo caso inválido lanza excepción antes de llegar a un `return`. Se agrega un criterio de **defensa en profundidad** independiente de qué rama del parseo se ejecute: justo antes del `return frozenset(...)`, si el conjunto resultante está vacío, lanzar `ConfigurationError` de todos modos. Esto protege contra un bug futuro de refactor (alguien reordena el `try/except` y abre una ruta nueva que no estaba en la tabla original) sin depender de que cada desarrollador futuro recuerde replicar la tabla completa de casos edge.

**Criterios de aceptación nuevos (sección 1):**
- [ ] Cada elemento del CSV se valida como ASCII antes de `int()`; un elemento con dígitos Unicode no-ASCII (ej. `"١١١"`, `"１１１"`) lanza `ConfigurationError` igual que un elemento no numérico — con test explícito para ambos casos de ejemplo.
- [ ] `get_allowed_chat_id` tiene un chequeo defensivo final: si por cualquier razón el conjunto construido está vacío justo antes de retornar, lanza `ConfigurationError` en vez de devolver `frozenset()` — este chequeo es independiente de la validación por elemento y actúa como red de seguridad ante bugs de refactor futuros.
- [ ] Documentado en esta spec (este párrafo cuenta como esa documentación) que un CSV arbitrariamente largo no es un vector de DoS explotable en este modelo de amenaza (requiere acceso previo al entorno de despliegue, parseo es O(n) y ocurre una sola vez al arranque) — evaluado y descartado explícitamente, sin criterio de límite de longitud.
- [ ] `"0"` y `"-100"` como chat_id individuales siguen siendo válidos a nivel de parseo (ya en la tabla de `architect`) — se confirma sin cambios, documentado el porqué no representan riesgo (Telegram nunca asigna esos valores a un chat real, `chat.type == "private"` sigue siendo la barrera funcional).

---

### 2. Veredicto — pregunta abierta 1: superficie de cuentas de Telegram triplicada

**No bloqueante para este SDD. Se documenta como riesgo residual aceptado**, con el mismo tratamiento que `SDD_investbot_mvp.md` sección "Criterios de seguridad" punto 1(C) le dio al caso de 1 usuario — se extiende, no se reinventa:

- El control de acceso en sí (membership sobre `frozenset[int]`) sigue siendo tan válido como la igualdad 1 a 1 anterior: `chat.id`/`from.id` lo asigna el servidor de Telegram según la sesión autenticada real, no es falsificable por un tercero sin controlar la cuenta o el token del bot. El cambio de diseño no debilita esa propiedad.
- Lo que sí cambia con matemática simple: con 3 cuentas de Telegram autorizadas en vez de 1, la probabilidad de que **al menos una** sufra SIM-swap/secuestro de sesión/phishing de código de acceso es estrictamente mayor que con 1 sola cuenta (probabilidad de unión de 3 eventos independientes ≥ probabilidad de 1 solo evento). El impacto de que ocurra es el mismo que ya está documentado (el atacante hereda acceso legítimo al bot) — lo que se triplica es la superficie de entrada, no la severidad por evento.
- **Por qué no es bloqueante:** es un riesgo de la plataforma Telegram (gestión de sesión/2FA de cada cuenta individual), no del código de InvestBot — igual que en el caso de 1 usuario, el bot no puede mitigar esto con más lógica de comparación de IDs. Convertirlo en bloqueante para este SDD significaría bloquear cualquier extensión de control de acceso multi-usuario hasta resolver un problema que está fuera del límite de confianza del propio bot.
- **Diferencia real que sí importa y que la spec de `architect` no menciona:** con 1 usuario, revocar acceso ante sospecha de compromiso = revocar el token en @BotFather (mata el bot para todos, pero solo hay un "todos"). Con 3 usuarios, la Restricción ya vigente de `architect` ("no se implementa gestión dinámica... el conjunto de autorizados es estático, leído una sola vez al arranque") significa que **revocar a un solo chat_id comprometido sin afectar a los otros 2 requiere editar `TELEGRAM_ALLOWED_CHAT_ID` en el `.env` del VPS y reiniciar el proceso** — no hay revocación en caliente. Esto es consistente con el alcance que `architect` ya fijó (no se reabre), pero debe quedar documentado como **procedimiento de respuesta a incidente**, no asumido implícitamente.

**Criterios de aceptación nuevos (sección 2) — documentación, no código:**
- [ ] `README.md` (o el documento de runbook que ya exista, ej. `SETUP_TELEGRAM_BOT.md`) agrega una sección "Respuesta a incidente: una de las cuentas autorizadas comprometida" con el procedimiento explícito: editar `TELEGRAM_ALLOWED_CHAT_ID` quitando el chat_id afectado, reiniciar el proceso/contenedor, y (opcional pero recomendado) rotar `TELEGRAM_BOT_TOKEN` en @BotFather si se sospecha que el token también pudo filtrarse por la misma vía.
- [ ] Se documenta explícitamente (este párrafo cuenta como esa documentación) que la superficie de riesgo de secuestro de cuenta de Telegram se triplica con 3 usuarios respecto al análisis de 1 usuario en `SDD_investbot_mvp.md`, como riesgo residual aceptado — no requiere cambio de código.
- [ ] Se recomienda (no bloqueante, fuera del control del código) que los 3 usuarios autorizados tengan verificación en dos pasos activada en su cuenta de Telegram — mismo tipo de recomendación ya hecha para Daniela en la spec anterior, extendida a los otros 2.

---

### 3. Veredicto — pregunta abierta 2: `InMemoryRateLimiter` con cupo agregado compartido

**No bloqueante para este SDD. Se concuerda con la Decisión de diseño #5 de `architect`: no se toca el rate limiter en esta iteración.** Análisis de riesgo:

```
AMENAZA: agotamiento del cupo diario de FMP (250 req/día) o del rate-limit local (10 req/60s)
         por uno de los 3 usuarios autorizados, dejando sin servicio a los otros 2.
VECTOR: autenticado (requiere ser uno de los 3 chat_id ya autorizados) — no hay vector
        no autenticado, el gate de chat_id sigue cortando antes de llegar al rate limiter.
IMPACTO: disponibilidad únicamente (los otros 2 usuarios legítimos no pueden usar el bot
         hasta que se resetee la ventana/día) — no hay impacto de confidencialidad ni
         integridad, no hay escalación de privilegios ni acceso a datos de otro usuario.
PROBABILIDAD: media — con 3 personas reales y uso esporádico (no es una app con miles de
         usuarios), el escenario más probable es "amistoso" (2 consultas seguidas de
         2 personas distintas en la misma ventana de 60s), no malicioso. El escenario
         malicioso (uno de los 3 agotando el cupo a propósito) requiere que uno de los
         3 ya autorizados decida activamente sabotear a los otros — es un riesgo de
         confianza entre las 3 personas que Daniela invitó, no un atacante externo.
RIESGO NETO: Medio en el peor caso amistoso, Bajo como vector malicioso deliberado
         (el "atacante" ya es una de las 3 personas de confianza explícita de Daniela).
MITIGACIÓN RECOMENDADA: no implementar en este SDD (per Decisión #5). Backlog: cupo
         per-chat_id como SDD separado si Daniela reporta el problema en uso real.
REFERENCIA: CWE-400 (Uncontrolled Resource Consumption), OWASP A04:2025 (Insecure Design)
         como categoría de "decisión de diseño documentada", no como hallazgo activo.
```

- **Por qué no es bloqueante:** el rate limiter agregado no introduce ningún camino nuevo para que un usuario no autorizado consuma cupo — sigue siendo estrictamente peor "fairness entre 3 amigos" que "brecha de seguridad". Bloquear el SDD de control de acceso multi-usuario por este motivo sería sobre-alcance: el problema de fairness existiría igual si Daniela simplemente le prestara su chat_id/cuenta a las otras 2 personas hoy mismo sin este cambio.
- **Lo único que sí se agrega como criterio nuevo** (no requiere tocar `InMemoryRateLimiter`, no cambia su firma/comportamiento/tests — cumple la Restricción vigente) es **observabilidad**: sin saber qué chat_id generó cada request, si el cupo se agota no hay forma de diagnosticar cuál de los 3 lo causó ni de decidir si fue error o mal uso — ver sección 4 abajo, que cubre esto como parte del mismo hallazgo.

**Criterios de aceptación nuevos (sección 3):**
- [ ] Documentado en esta spec (cumplido con el análisis de riesgo arriba) que la decisión de no separar el rate limiter se evaluó explícitamente desde seguridad, no solo desde arquitectura — veredicto: riesgo de disponibilidad Medio/Bajo, no bloqueante, backlog si se materializa en uso real.
- [ ] Si en el futuro se decide implementar cupo per-chat_id (SDD separado), ese SDD debe definir explícitamente qué pasa cuando el cupo de FMP global (250/día) se acerca al límite aunque ningún usuario individual haya agotado su propio cupo — para no repetir la ambigüedad ya señalada por `architect` en la Decisión #5.

---

### 4. Logging por identidad — diagnóstico y revocación selectiva con 3 usuarios

Hoy `build_chat_id_gate` (línea 84 de `security.py`) solo loguea en el camino de **rechazo** (`"Update rechazado — chat_id no autorizado: %s"`). No existe ningún log en el camino de **aceptación**. Con 1 usuario autorizado esto era razonable (no había nada que distinguir). Con hasta 3 identidades distintas, la ausencia de logging en el camino aceptado tiene un costo operativo concreto: si el rate-limit se agota, o si se sospecha que una de las 3 cuentas fue comprometida (sección 2), **hoy no hay forma de correlacionar en los logs qué chat_id autorizado generó qué actividad** — solo se ve qué se rechazó, nunca quién usó el bot legítimamente.

- Esto es un hallazgo de **observabilidad (OWASP A09:2025 — Logging & Monitoring Failures)**, no de control de acceso — el gate ya decide correctamente quién pasa, el problema es que no queda rastro de quién pasó.
- El cambio es de bajo costo y **vive enteramente dentro de `security.py`** (ya en el alcance de artefactos a modificar de esta spec) — no requiere tocar `bot.py`, `query_handler.py`, ni el `InMemoryRateLimiter` (su firma/comportamiento no cambian, solo se agrega una línea de log en `build_chat_id_gate`, función distinta).
- Igual que ya aclara la Decisión #4 de `architect` para el log de rechazo, este log de aceptación **no debe incluir el conjunto completo de autorizados** — solo el `chat.id` individual del update aceptado, simétrico a lo que ya se hace para el rechazado. No se expone nada que no se exponga ya hoy.

**Criterio de aceptación nuevo (sección 4) — recomendado, bajo costo, no bloqueante para scope freeze pero se sugiere incluir en esta misma iteración dado el costo trivial:**
- [ ] `build_chat_id_gate` loguea a nivel `INFO` (no `WARNING`, no es una anomalía) el `chat_id` de todo update **aceptado**, con un mensaje simétrico al de rechazo (ej. `"Update aceptado — chat_id autorizado: %s"`), sin incluir el conjunto completo de `allowed_chat_ids` en ese log.
- [ ] Test que verifica que, al aceptar un update de cada uno de los 3 chat_id de un `frozenset({111, 222, 333})`, el log de aceptación identifica el chat_id individual correcto en cada caso (no un valor fijo ni el conjunto completo).
- [ ] Si `implementer` o `architect` deciden no incluir este criterio en esta iteración (por priorización), queda registrado explícitamente como **backlog** para el próximo SDD que toque `security.py` — no bloquea el cierre de este.

---

### 5. Chequeo de no-regresión del fail-closed: "conjunto vacío" vs. "todos autorizados"

Distinción importante que debe quedar explícita como criterio de test, no solo como intención de diseño:

- **Propiedad estructural ya correcta por diseño:** `is_authorized_chat` usa `chat.id not in allowed_chat_ids` (membership). Si `allowed_chat_ids` fuera, por algún bug, un `frozenset()` vacío, el resultado de `in` sobre un conjunto vacío es **siempre `False`** para cualquier `chat.id` — es decir, la propiedad estructural del propio operador `in` garantiza que "conjunto vacío" degrada a **"nadie autorizado" (fail-closed correcto, aunque sea un bug de disponibilidad)**, nunca a "todos autorizados". Esto es fundamentalmente distinto y de menor severidad que el bug que sí sería crítico: un código que interprete "conjunto vacío o `None`" como señal de "sin restricción, dejar pasar todo" (ej. un patrón defensivo mal pensado tipo `if not allowed_chat_ids: return True`).
- **Ese patrón peligroso no existe en el diseño actual** (ni en el código de `architect` ni en el código vigente de `security.py`) — no hay ningún `if not allowed_chat_ids` en `is_authorized_chat` ni en `build_chat_id_gate`. Se confirma que el diseño no lo introduce. Aun así, se agrega como **test de regresión explícito** para que quede blindado contra un futuro refactor que introduzca ese patrón por error (ej. alguien "simplificando" el código y agregando un atajo para el caso "sin autorizados configurados, dejar pasar en dev/testing" — un anti-patrón común que aparece en código real).
- Esto también cubre el ítem ya existente en los criterios base de `architect` ("Ningún caso de entrada inválida devuelve un `frozenset` vacío ni `None`") — ese criterio protege `get_allowed_chat_id`; el criterio nuevo de acá protege la capa de abajo (`is_authorized_chat`/`build_chat_id_gate`) de forma independiente, como defensa en profundidad — si algún día `get_allowed_chat_id` cambiara y ese invariante se rompiera, esta capa no debe convertir "vacío" en "todos".

**Criterios de aceptación nuevos (sección 5):**
- [ ] Test explícito: `is_authorized_chat(update_de(cualquier_chat_id, "private"), frozenset())` → `False`, para al menos 3 valores distintos de `chat_id` (incluyendo `0` y un negativo) — confirma que un conjunto vacío nunca autoriza a nadie, sea cual sea el chat_id.
- [ ] Test explícito equivalente sobre `build_chat_id_gate(frozenset())`: cualquier update entrante es rechazado con `ApplicationHandlerStop`, ninguno pasa — mismo caso, a nivel del gate completo en vez de la función interna.
- [ ] Code review / grep explícito (criterio de revisión, no solo de test automatizado): confirmar que en ningún punto de `security.py` aparece un patrón `if not allowed_chat_ids` (o equivalente) que retorne/permita autorización — si aparece, es un hallazgo bloqueante que debe corregirse antes de scope freeze, no un detalle menor.
- [ ] Confirmar que `get_allowed_chat_id` (criterio de la sección 1(D) de acá) y `is_authorized_chat`/`build_chat_id_gate` (esta sección) son **dos capas independientes** de la misma garantía fail-closed — un test que solo cubra una de las dos no es suficiente para cerrar este punto.

---

### Sin bloqueantes

`security` no encontró ningún hallazgo que obligue a un spec patch de `architect` ni que impida pasar a `implementer`. Los 2 puntos que `architect` dejó explícitamente abiertos (superficie de cuentas triplicada, rate limiter compartido) se resuelven acá con veredicto **no bloqueante / backlog documentado**, consistente con el precedente ya establecido en `SDD_investbot_mvp.md` y en `.env.example` de dejar constancia explícita en vez de expandir el alcance del SDD actual.

### Foco esperado para `implementer`
- Los criterios nuevos de la sección 1 (guard `isascii()`, chequeo defensivo de conjunto vacío) y la sección 4 (log de aceptación) viven enteramente en `security.py` — no requieren tocar `bot.py` más allá de lo que ya pedía `architect`.
- Los tests de regresión de la sección 5 (`frozenset()` nunca autoriza) deben agregarse a `tests/test_security.py` junto con los tests ya pedidos por `architect` para la tabla de casos edge — mismo archivo, misma iteración.
- La documentación de incident response (sección 2) y la nota de riesgo residual pueden ir en `README.md` o `SETUP_TELEGRAM_BOT.md` según dónde ya viva el runbook existente — no crear un documento nuevo si ya hay uno.

---

## Criterios QA para Spec: Control de acceso multi-usuario (chat_id) [Iter-1, 2026-07-30]

**Rol:** `qa` (Momento 1 — pre-implementación). Esta sección **agrega** criterios de cobertura y testabilidad a lo ya definido por `architect` (Criterios de aceptación, Artefactos a crear/modificar) y `security` (secciones 1-5); no reescribe ni reabre ninguna decisión de diseño ni ningún criterio ya aprobado. Donde `architect`/`security` ya listaron un caso de test concreto, este bloque lo referencia en vez de duplicarlo, y solo agrega el ángulo de cobertura/testabilidad que faltaba (mapeo explícito caso→test, fixtures de `test_bot.py` a tocar, baseline de cobertura medido, y el formato de evidencia que exigirá QA en el Momento 2).

### Tipo de prueba principal

**Unit testing (pytest)** sobre `tests/test_security.py` — es el tipo correcto porque `get_allowed_chat_id`, `is_authorized_chat` y `build_chat_id_gate` son lógica pura o casi pura (la única dependencia externa es `os.environ`/`env` inyectado, ya mockeable por diseño) sin necesidad de red, BD ni Telegram real. **Regression testing** sobre `tests/test_bot.py`, `tests/test_onboarding.py` y `tests/test_query_handler.py` como red de seguridad del cambio de tipo `int → frozenset[int]` — no se justifica integration ni E2E para este SDD: no hay una integración nueva entre componentes (el gate ya existe, solo cambia el tipo del parámetro) ni un flujo de usuario nuevo que recorrer.

### Cobertura mínima requerida

- **Baseline medido hoy (2026-07-30, antes de la implementación)** — corrido localmente con `.venv` del proyecto:
  ```
  pytest --cov=investbot.security --cov-report=term-missing tests/test_security.py
  → src/investbot/security.py: 57 stmts, 0 miss, 100% cobertura de líneas, 17 tests
  ```
  ```
  pytest -q (suite completa)
  → 482 passed
  ```
  **Nota de corrección de dato**: el historial del proyecto registraba "211 tests" como referencia — ese número está desactualizado (el proyecto creció desde esa nota). El baseline vinculante para el criterio de no-regresión de este SDD es el medido hoy: **482 tests pasando, 0 fallando, 0 skipped**. Cualquier verificación de "no regresión" en el Momento 2 debe compararse contra 482, no contra 211.
- [ ] Code coverage de `src/investbot/security.py` **no debe bajar de 100% líneas** tras el cambio — es lógica crítica de control de acceso (tabla de riesgo: "Crítico — 100%, todos los escenarios"), y ya está en 100% hoy, por lo que "no bajar" es el criterio correcto (no "alcanzar" un umbral).
- [ ] Branch coverage 100% en las dos ramas nuevas de alto riesgo que agrega `security` en su sección 1: el guard `isascii()` (rama ASCII válido vs. rechazo) y el chequeo defensivo de conjunto vacío antes del `return` (ver "Testabilidad" abajo — esta rama requiere una decisión de diseño de testabilidad para ser alcanzable).
- [ ] Los 8 criterios de aceptación base de `architect` (sección "Parseo y fail-closed") + los 5 criterios de `architect` (sección "Autorización") + los 2 de `architect` (sección "Gate global") + los 4 nuevos de `security` (sección 1) + los 3 de `security` (sección 2, documentación) + los 2 de `security` (sección 3, documentación) + los 3 de `security` (sección 4, logging) + los 4 de `security` (sección 5, regresión fail-closed) están cada uno cubiertos por **al menos un test identificable por nombre** en la lista de abajo — no basta con "la suite pasa", cada checkbox debe poder señalarse a un test concreto.
- Comando de verificación exacto para el Momento 2: `pytest --cov=investbot.security --cov-report=term-missing tests/test_security.py -v` — el reporte debe adjuntarse como evidencia (no solo "pasó"), ver "QA Handoff — formato de evidencia exigido" al final.

### Casos obligatorios — mapeo test→criterio para `tests/test_security.py`

Los tests marcados **(existente)** ya están en el archivo (líneas 19-100 de la versión actual) y solo requieren actualizar el assert de tipo (`int` → `frozenset[int]`) y el nombre del segundo parámetro donde aplique. Los marcados **(nuevo)** no existen y deben agregarse.

**A. `get_allowed_chat_id` — parseo CSV y fail-closed**
1. (existente) Un solo entero `"12345"` → `frozenset({12345})` — backward compat exacta. *Cubre: criterio base #1 de architect.*
2. (nuevo) Dos elementos `"111,222"` → `frozenset({111, 222})`.
3. (nuevo) Tres elementos `"111,222,333"` → `frozenset({111, 222, 333})` — el caso de uso explícito "hasta 3" de Daniela. *Cubre: criterio base #2.*
4. (existente, actualizar assert) Espacios `" 111 , 222 ,333 "` → `frozenset({111, 222, 333})`. *Cubre: criterio base #3.*
5. (existente) Variable ausente → `ConfigurationError`. *Cubre: criterio base #4.*
6. (existente) Variable vacía `""` → `ConfigurationError`.
7. (nuevo) Variable solo espacios `"   "` → `ConfigurationError` (caso distinto de vacío puro, la tabla de `architect` lo lista aparte).
8. (existente, actualizar assert) Elemento no numérico `"111,abc,333"` → `ConfigurationError`, y **el test debe assertar que `"abc"` aparece en el mensaje de la excepción** (`str(exc_info.value)`), no solo que se lanzó. *Cubre: criterio base #5.*
9. (nuevo) Coma vacía interna `"111,,222"` → `ConfigurationError`.
10. (nuevo) Trailing comma `"111,222,"` → `ConfigurationError`.
11. (nuevo) Leading comma `",111,222"` → `ConfigurationError`.
12. (nuevo) Un solo elemento vacío, coma sola `","` → `ConfigurationError`. *9-12 cubren: criterio base #6 (los 4 casos deben ser tests separados, no uno solo parametrizado que oculte cuál falla — si se usa `@pytest.mark.parametrize`, cada caso debe tener un `id` legible en el reporte).*
13. (nuevo) Duplicados `"111,111,222"` → `frozenset({111, 222})`, **sin excepción** (assert explícito de que no se lanza nada, no solo el valor de retorno). *Cubre: criterio base #7.*
14. (nuevo) Más de 3 IDs `"111,222,333,444,555"` → válido, `frozenset` de 5 elementos, sin excepción. *Cubre: criterio base #8.*
15. (nuevo) Unicode no-ASCII, dígitos arábigo-índicos `"١١١"` → `ConfigurationError`. *Cubre: security sección 1, criterio (B).*
16. (nuevo) Unicode no-ASCII, dígitos fullwidth `"１２３"` → `ConfigurationError`. *Cubre: security sección 1, criterio (B), segundo ejemplo explícito de la spec.*
17. (nuevo) `"0"` como único elemento → válido, `frozenset({0})`, sin excepción. *Cubre: security sección 1, criterio (A)/(D) confirmación de la tabla de architect.*
18. (nuevo) `"-100"` como único elemento → válido, `frozenset({-100})`, sin excepción.
19. (nuevo) **Chequeo defensivo de conjunto vacío** — ver "Testabilidad" abajo: test directo (no vía CSV, porque no hay input de CSV normal que llegue vacío tras los guards anteriores) que confirme que, si el conjunto resultante estuviera vacío antes del `return`, se lanza `ConfigurationError`. *Cubre: security sección 1, criterio (D).*

**B. `is_authorized_chat` — membership**
20. (existente, actualizar tipo) `is_authorized_chat(update_de(111, "private"), frozenset({111, 222, 333}))` → `True`.
21. (nuevo) Repetir el caso 20 para `222` y para `333` individualmente — **los 3 IDs del conjunto deben probarse por separado**, no alcanza con probar solo el primero. *Cubre: criterio base autorización #4 explícito de architect ("no alcanza con probar solo el primero del conjunto").*
22. (existente, actualizar tipo) `is_authorized_chat(update_de(999, "private"), frozenset({111, 222, 333}))` → `False` (fuera del conjunto).
23. (existente, actualizar tipo) `is_authorized_chat(update_de(111, "group"), frozenset({111, 222, 333}))` → `False` (no privado, aunque el ID esté autorizado).
24. (existente, actualizar tipo) `is_authorized_chat(update_sin_chat(), frozenset({111}))` → `False` (chat `None`).
25. (nuevo) `is_authorized_chat(update_de(chat_id, "private"), frozenset())` → `False`, repetido para **al menos 3 valores de `chat_id` distintos, incluyendo `0` y un negativo**. *Cubre: security sección 5, primer criterio — conjunto vacío nunca autoriza a nadie.*

**C. `build_chat_id_gate` — gate global**
26. (existente, actualizar tipo) Gate con `frozenset({111, 222, 333})` deja pasar `111` en chat privado sin lanzar.
27. (nuevo) Repetir el caso 26 para `222` y `333` individualmente. *Cubre: criterio de gate global "deja pasar a los 3", igual razonamiento que el caso 21.*
28. (existente, actualizar tipo) Gate corta con `ApplicationHandlerStop` para ID fuera del conjunto.
29. (existente, actualizar tipo) Gate corta con `ApplicationHandlerStop` para chat no privado.
30. (existente, actualizar tipo) `callback_query.answer()` best-effort se invoca en el camino de rechazo (`assert_awaited_once`) — sin cambios de comportamiento, solo tipo del gate.
31. (nuevo) **Logging de aceptación en INFO**: capturar con `caplog` al nivel `INFO`, invocar el gate con cada uno de los 3 IDs autorizados por separado, y assertar que el registro de log identifica el `chat_id` individual correcto en cada invocación — **y que el mensaje NO contiene la representación del `frozenset` completo** (ej. `assert "222" not in record.message` cuando se acepta `111`, o un assert más robusto tipo `assert str(allowed_chat_ids) not in record.message`). *Cubre: security sección 4, ambos criterios.*
32. (nuevo) `build_chat_id_gate(frozenset())` → cualquier update entrante (probar con al menos 2 valores de `chat_id` distintos) es rechazado con `ApplicationHandlerStop`, ninguno pasa. *Cubre: security sección 5, segundo criterio.*

**D. Revisión de código (no automatizable como test, criterio de checklist)**
33. Grep/code review explícito: confirmar que no existe ningún patrón `if not allowed_chat_ids` (o equivalente semántico) en `security.py` que retorne/permita autorización. *Cubre: security sección 5, tercer criterio — QA debe correr `grep -n "not allowed_chat_id" src/investbot/security.py` en el Momento 2 y adjuntar el output (vacío = pasa) como evidencia, no solo declarar "revisado".*

### Testabilidad

- [ ] Los métodos de negocio (`get_allowed_chat_id`, `is_authorized_chat`, `build_chat_id_gate`) siguen siendo funciones puras o casi puras, invocables directamente en tests sin mocks de infraestructura — el cambio de tipo no debe introducir ningún acoplamiento nuevo a `os.environ` global (debe seguir aceptando `env` inyectado como hoy).
- [ ] **Hallazgo de testabilidad sobre el chequeo defensivo de conjunto vacío (security sección 1, criterio D):** tal como está descrito ("justo antes del `return frozenset(...)`, si el conjunto resultante está vacío, lanzar `ConfigurationError`"), esta rama **no es alcanzable desde la API pública `get_allowed_chat_id(env)` con ningún input de CSV válido** — todos los inputs que producirían un conjunto vacío (variable ausente, vacía, elemento inválido) ya son interceptados por los guards anteriores y lanzan antes de llegar a esa línea. Si `implementer` escribe el guard como código muerto inline (`if not result: raise ...` después de un `return` normal de la función), **la herramienta de coverage lo marcará como línea no cubierta** y bajará el 100% de baseline, o forzará a `implementer` a agregar un `# pragma: no cover` que oculta el problema real: la rama no es testeable en aislamiento.
  - **Recomendación de testabilidad (no bloqueante para scope freeze, pero QA la exigirá en el Momento 2 si el coverage baja de 100%):** extraer el chequeo final a una función privada pequeña y pura, ej. `_ensure_non_empty(ids: frozenset[int]) -> frozenset[int]`, que reciba el conjunto ya construido y sea invocable directamente desde un test con un `frozenset()` armado a mano — sin pasar por el parseo de CSV. Esto convierte el caso 19 de la lista anterior en un test directo y determinístico en vez de un intento de forzar un input imposible.
  - Si `implementer` decide no extraer la función, el criterio de exit igual aplica: el caso 19 debe existir de alguna forma verificable (aunque sea invocando una función interna con `_` o usando `unittest.mock.patch` sobre el paso intermedio) — "no se puede testear" no es una respuesta aceptable para lógica de fail-closed crítica.
- [ ] No hay lógica crítica nueva en constructores ni métodos estáticos no testeables — `build_chat_id_gate` sigue devolviendo una closure/coroutine testeable directamente, como hoy.
- [ ] Los efectos secundarios (logging) están aislados y son verificables vía `caplog` sin necesidad de mockear un logger real — ya es el patrón usado en `test_log_conflict_error_no_incluye_token`, se reutiliza el mismo patrón para el log de aceptación (caso 31).

### Fixtures/mocks de `tests/test_bot.py` a actualizar

Verificado por grep directo sobre el archivo actual (no solo las líneas que cita `architect`):

- **Línea 50 y línea 92** (`allowed_chat_id=12345` como kwarg de `build_application(...)`): renombrar a `allowed_chat_ids=frozenset({12345})` — estos son los únicos 2 call-sites directos que pasan el valor como argumento de tipo, confirmado por `grep -n "allowed_chat_id" tests/test_bot.py`.
- **Líneas 24, 30, 38, 106, 130** (`monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")` / `monkeypatch.delenv(...)`): **no requieren cambio de valor** — siguen usando la variable de entorno con un solo entero como string, que sigue siendo válido por backward compatibility. Verificado por grep: **ningún test en el archivo compara `allowed_chat_id` contra el entero `12345` directamente** (no hay `== 12345` ni `assert ... is 12345`) — el único uso es pasar el string a `setenv` y el kwarg ya cubierto arriba. No hay fixture oculta que asuma `int`.
- **Ningún mock de `application.handlers`** necesita cambiar de estructura — el test de `test_build_application_registra_handlers` (línea ~44) solo verifica `-1 in application.handlers` y la cantidad de handlers en ese grupo, no el tipo interno de `allowed_chat_id` que capturó la closure.
- **Criterio nuevo:** agregar al menos un test en `test_bot.py` que pase `allowed_chat_ids=frozenset({111, 222, 333})` (3 elementos, no solo 1) a `build_application(...)` y confirme que igual construye la `Application` sin error — el test existente de la línea 50 usa un frozenset de 1 elemento, que no ejercita el caso multi-usuario a nivel de integración `bot.py` ↔ `security.py`.

### Criterio de no-regresión — suite completa

- [ ] Correr `pytest -q` (suite completa) antes y después del cambio. Baseline pre-cambio medido hoy: **482 passed, 0 failed, 0 skipped, 0 warnings nuevos** (10 warnings existentes de `PTBUserWarning`/deprecation ajenos a este cambio, no deben aumentar).
- [ ] Tras el cambio: **0 tests rotos fuera de los que se actualizan intencionalmente** (la lista de tests "(existente, actualizar tipo/assert)" de la sección anterior, todos en `tests/test_security.py` y `tests/test_bot.py`). Cualquier test que falle en `tests/test_onboarding.py`, `tests/test_query_handler.py`, `tests/test_edge_cases.py` o cualquier otro archivo no listado es una **regresión no prevista** y bloquea el cierre del Momento 2 — se trata como criterio bloqueante, no como corrección menor.
- [ ] El número final de tests pasando debe ser **482 + N**, donde N = cantidad de tests nuevos agregados según la lista de casos obligatorios arriba (aproximadamente 15-18 nuevos en `test_security.py` + al menos 1 nuevo en `test_bot.py`) — si el número final es menor a 482, hay tests que se perdieron o quedaron deshabilitados, no solo "renombrados".
- [ ] Flaky rate = 0: correr la suite nueva/modificada al menos 2 veces seguidas y confirmar mismo resultado — los tests de `caplog` (casos 31 y el log de rechazo existente) son el punto de mayor riesgo de flakiness si no se limpia el logger entre tests.

### Cómo se verifica "cobertura" del cambio

1. `pytest --cov=investbot.security --cov-report=term-missing tests/test_security.py -v` — el reporte de `--cov-report=term-missing` debe mostrar `100%` en la fila de `security.py` y la columna `Missing` vacía. Si aparece alguna línea en `Missing`, identificar si es la rama defensiva de conjunto vacío (ver "Testabilidad" — requiere el fix de extracción de función) u otra línea no anticipada.
2. Comparar contra el baseline documentado arriba (57 stmts, 0 miss, 100%, medido 2026-07-30) — el criterio es **no bajar**, no es necesario que suban los stmts totales de forma desproporcionada (un aumento razonable de líneas por el parseo CSV nuevo es esperable y no es un problema en sí mismo).
3. Adjuntar el output completo del comando (no un resumen) como evidencia en el Momento 2 — ver formato de QA Handoff abajo.

### Criterio de exit de QA (Momento 1 → scope freeze)

- Esta sección de criterios está completa y no requiere más iteración de `qa` antes del scope freeze — no hay hallazgos bloqueantes de testabilidad que impidan pasar a `implementer` (el punto de la rama defensiva de conjunto vacío es una recomendación de diseño testeable, no un bloqueante: si `implementer` no la aplica, el caso 19 simplemente debe resolverse de otra forma, documentado arriba).
- Sin tests ignorados, comentados o marcados `xfail`/`skip` para hacer pasar la suite — si algún caso de la tabla de `architect` resulta genuinamente imposible de testear tal como está diseñado, es una escalación a `architect` (bloqueante de testabilidad), no un test silenciado.

---

## QA Handoff — formato exigido para la validación POST-implementación (Momento 2)

Cuando `implementer` entregue el cambio, `qa` no acepta "pasó" como evidencia. Se exige exactamente:

```markdown
## QA Handoff [Iter-1]

### Resultado de validación
[APROBADO / RECHAZADO — con justificación de una línea]

### Evidencia exigida (adjuntar output real, no resumen)
1. Output completo de `pytest -q` (suite completa) — para confirmar el conteo total
   final vs. baseline de 482 + N tests nuevos, 0 failed, 0 skipped.
2. Output completo de `pytest --cov=investbot.security --cov-report=term-missing
   tests/test_security.py -v` — para confirmar 100% líneas, sin líneas en "Missing"
   (o diagnóstico de por qué alguna línea quedó sin cubrir, ej. la rama defensiva).
3. Output de `pytest tests/test_security.py -v` (verbose, no solo `-q`) — para
   verificar por nombre que cada uno de los 33 casos de la lista "Casos obligatorios"
   de esta spec tiene un test correspondiente identificable (nombre de función visible).
4. Output de `grep -n "not allowed_chat_id" src/investbot/security.py` — debe estar
   vacío (criterio 33, code review de fail-closed).
5. Diff de `tests/test_bot.py` — para confirmar que las líneas 50 y 92 cambiaron a
   `allowed_chat_ids=frozenset(...)` y que se agregó el test nuevo de 3 elementos.
6. Si se corrió la suite 2 veces para descartar flakiness (criterio de flaky rate),
   adjuntar ambos outputs o confirmar explícitamente que se corrió 2 veces.

### Criterios QA verificados
- [ ] Cada uno de los 33 casos de "Casos obligatorios" — evidencia: nombre de test +
  línea del output verbose de pytest.
- [ ] Coverage de security.py = 100%, sin bajar del baseline — evidencia: reporte punto 2.
- [ ] 0 regresiones fuera de los tests intencionalmente actualizados — evidencia: punto 1,
  diff de archivos de test tocados vs. lista esperada (solo test_security.py, test_bot.py).
- [ ] Code review de patrón `if not allowed_chat_ids` — evidencia: punto 4.

### Métricas de calidad
- Cobertura: X% líneas (debe ser 100% en security.py), Y% branches en las 2 ramas nuevas.
- Tests: N nuevos, 482 existentes — todos pasan (total final = 482 + N).
- Flaky tests: 0 (verificado con 2 corridas).
- Tiempo de suite: Xs (referencia: suite completa hoy corre en ~0.6s, no debería
  degradarse de forma notoria por este cambio).

### Defectos abiertos
[Lista de defectos pendientes con severidad — o "Ninguno"]

### Backlog QA (siguiente run)
[Casos identificados fuera de scope — ej. cupo per-usuario del rate limiter (ya
documentado como backlog explícito por `security`, no es hallazgo nuevo de QA)]
```

**Regla de rechazo automático:** si `implementer` entrega solo un mensaje tipo "todos los tests pasan" sin los 6 puntos de evidencia de arriba, `qa` rechaza la entrega y la devuelve pidiendo la evidencia — no se re-ejecuta la suite "de confianza" sin verla correr con sus propios comandos.

---

## Cierre del pipeline — Iter-1 [`architect`, 2026-07-30]

**Rol:** `architect`. Cierre formal del pipeline BMAD + SDD + Ralph Loop para este SDD. Iter-1, sin escalaciones, sin spec patch requerido.

### Confirmación de criterios de aceptación

Todos los criterios de aceptación de esta spec están cumplidos:

- **Criterios base de `architect`** (secciones "Parseo y fail-closed", "Autorización", "Gate global", "Integración en `bot.py`", "Regresión", "Documentación") — cumplidos. `implementer` no reabrió ninguna de las 6 decisiones de diseño de la sección "Decisiones de diseño tomadas".
- **Criterios agregados por `security`** (secciones 1-5: guard `isascii()`, chequeo defensivo de conjunto vacío vía `_ensure_non_empty`, veredicto no bloqueante sobre superficie de cuentas triplicada, veredicto no bloqueante sobre rate limiter compartido, logging de aceptación en INFO, tests de regresión de fail-closed) — cumplidos, verificados independientemente por `security` en la validación post-implementación.
- **33 casos de test de `qa`** (sección "Casos obligatorios") — los 33/33 verificados por `qa` con nombre de test concreto en la corrida verbose, ninguno pendiente.

### Sign-offs recibidos

- **`qa` (validación post-implementación): APROBADO.** 33/33 casos verificados por nombre de test, suite completa corrida 2 veces sin flaky (537 passed ambas corridas — 482 baseline + 55 nuevos/actualizados), coverage 100% en `security.py` (71/71 statements), diff de `tests/test_onboarding.py` revisado y confirmado como desviación mecánica correcta (2 líneas, sin lógica nueva). Cero hallazgos.
- **`security` (validación post-implementación): APROBADO.** Verificó el orden `isascii()` → `int()` en el parseo, `_ensure_non_empty` aislable y testeable de forma independiente, logging de aceptación sin exponer el conjunto completo de autorizados, `chat.type == "private"` intacto sin regresión, grep sin patrones de bypass (`if not allowed_chat_ids` ausente), y la sección de respuesta a incidente en `SETUP_TELEGRAM_BOT.md` adecuada para revocación de un chat_id comprometido. Cero hallazgos.
- No aplica sign-off de `frontend` — este SDD no tiene componente de UI.

### Estado final

**Código listo, no commiteado.** `implementer` ejecutó el cambio completo en el working tree (`src/investbot/security.py`, `bot.py`, `.env.example`, `contexto/referencia/SETUP_TELEGRAM_BOT.md`, `tests/test_security.py`, `tests/test_bot.py`, `tests/test_onboarding.py`) sin commitear ni pushear nada — verificado independientemente por el orquestador (`HEAD == origin/main`). Queda pendiente de que Daniela decida cuándo commitear, pushear y desplegar al VPS.

**Pipeline: CERRADO. Iter-1. Sin escalaciones a humano.**

---
