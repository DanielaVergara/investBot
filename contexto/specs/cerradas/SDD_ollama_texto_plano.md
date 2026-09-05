# Spec: Contrato de comunicación con Ollama — texto plano en vez de JSON estricto (`ai_explain.py`)

**Rol:** `architect` (spec nueva — cambio de fondo sobre el CONTRATO de transporte con Ollama, no
sobre ningún cálculo financiero).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa (foco: que la extracción de texto plano sin ancla JSON no abra
una superficie nueva de inyección/alucinación; que el guard anti-invención y la sanitización de
sector/industry sigan intactos). `qa` agrega criterios de cobertura para los 4 motivos de reintento
rediseñados + la extracción por marcador. `dba`/`frontend`/`backend` no aplican.
**Estado:** spec nueva, lista para `security` → `qa` → `implementer`. **No implementado — solo
diseño.**

---

## Contexto — por qué se pide este cambio

Código real releído completo hoy: `src/investbot/ai_explain.py` (`_fetch_explanation` líneas
2134-2240, `SYSTEM_PROMPT_EXPLAIN` líneas 1888-1911, `SYSTEM_PROMPT_PASO_A_PASO` líneas 1918-1949,
`_respuesta_es_eco_del_payload` líneas 2047-2076, `_respuesta_tiene_portugues` líneas 2118-2126,
`_no_new_protected_tokens` líneas 2016-2027, el loop de reintento líneas 2170-2237) y
`src/investbot/ai_rewrite.py` completo (518 líneas — `rewrite_parts`, `_parse_json_sections`,
`_classify_lines`/`_reconstruct_section`, mismo patrón `"format": "json"` con reintento único).

Con `qwen2.5:3b-instruct` (elegido por restricciones de hardware — Mac 8GB RAM de Daniela),
durante el día de hoy se repitieron fallas de FORMATO al exigirle JSON estricto
(`{"respuesta": "..."}`): JSON mal formado, eco del payload de entrada pegado adentro del JSON,
mezcla de portugués. Cada una se fue parchando con detección + reintento único (evidencia en el
código: comentarios fechados 2026-09-03/2026-09-04 documentando cada incidente), pero Daniela
reporta que sigue pasando seguido, incluso agotando los 2 intentos (evidencia real de hoy: 2
reintentos consecutivos fallidos con `ValueError`, resultando en `EXPLAIN_UNAVAILABLE_MSG` para el
usuario).

Es un patrón conocido de modelos 3B: son notoriamente menos confiables generando JSON
válido/con estructura exacta que generando texto libre en prosa. Hipótesis aprobada por Daniela:
sacar el requisito de JSON en `ai_explain.py` y pedirle a Ollama texto plano directo va a reducir
sustancialmente esta clase de fallas, porque elimina una clase entera de fallo (estructura JSON
rota) DE RAÍZ en vez de seguir agregando detección sobre ella.

---

## Estado actual

- `_fetch_explanation` manda `"format": "json"` en el payload a `/api/generate` (línea 2179) y
  ambos `SYSTEM_PROMPT_*` piden `{"respuesta": "..."}` (regla 4 y regla 6 de cada prompt).
- Tras la respuesta: `json.loads(raw_text)` → si no es un `dict` con clave `"respuesta"` de tipo
  `str`, se lanza `ValueError("estructura inesperada -- falta la clave 'respuesta' string")` (línea
  2194-2196). Esta es la ÚNICA clase de fallo exclusiva de la envoltura JSON — el resto
  (`json.JSONDecodeError`, eco, portugués, guard de integridad) opera sobre el string ya extraído.
- El loop de reintento (líneas 2170-2237) trata 4 motivos como equivalentes — 1 solo reintento total
  por cualquiera de: `(a)` JSON roto o sin clave `"respuesta"`, `(b)` eco del payload, `(c)`
  portugués, `(d)` guard de integridad (`_no_new_protected_tokens`).
- `_respuesta_es_eco_del_payload` (líneas 2047-2076) tiene 2 señales: `(1)` prefijo del texto de
  respuesta coincide con el prefijo del `datos_del_contexto` serializado — no depende de que la
  respuesta sea JSON, compara contra el `payload_json` armado con `json.dumps(datos_del_contexto)`,
  que sigue existiendo igual después de este cambio; `(2)` el separador literal `"} -- "`/`"}-- "`
  cerca del inicio del texto — señal que SÍ asume que el eco viaja envuelto en
  `{"respuesta": "<eco>} -- <explicación>"}`, un patrón específico de la envoltura JSON de salida
  actual.
- `ai_rewrite.py` (`rewrite_parts`) usa el mismo `"format": "json"` pero para transportar **N
  secciones independientes** (`{"0": "...", "1": "...", ...}`), no 1 sola respuesta — ver Decisión
  de diseño #6 más abajo sobre por qué esto es una diferencia estructural real, no cosmética.

## Estado objetivo

1. `_fetch_explanation` deja de exigir JSON de salida a Ollama: se saca `"format": "json"` del
   payload y `SYSTEM_PROMPT_EXPLAIN`/`SYSTEM_PROMPT_PASO_A_PASO` piden texto plano directo, con un
   marcador de inicio fijo para extracción robusta (Decisión #2).
2. `data["response"]` (siempre `str` según la API de Ollama, con o sin `format: json` — confirmado:
   el campo cambia de contenido, no de tipo) se usa directamente como candidato a respuesta, sin
   `json.loads`.
3. Los 4 motivos de reintento se mantienen — cambia el primero (deja de ser "estructura JSON
   inesperada", pasa a ser "respuesta vacía tras extracción", Decisión #3) — mismo mecanismo de loop
   de 2 intentos totales, sin tocar `config.timeout_seconds` ni el rate limiter.
4. `_respuesta_es_eco_del_payload` ajusta su señal 2 (Decisión #4) — la señal 1 no cambia.
5. `ai_rewrite.py` **no se toca en esta spec** — se queda con `"format": "json"` (Decisión #6,
   justificación técnica abajo). Queda documentado como alcance futuro separado si en el futuro
   se junta evidencia de fallas de formato equivalentes en ese flujo.
6. Transparente para todo lo construido encima: `_fetch_explanation` sigue devolviendo `str` (la
   explicación ya lista, pasada por `_enforce_brevity`) — cero cambios en `_dispatch_leaf`, en las
   27 preguntas del menú, en el flujo "paso a paso", ni en ningún call site.

---

## Decisiones de diseño tomadas

### 1. Sacar `"format": "json"` del payload — sin reemplazo por otro parámetro de Ollama

`config` (`ai_rewrite.OllamaConfig`) no cambia de forma — `_fetch_explanation` solo deja de incluir
la clave `"format"` en el `json=` del `POST /api/generate`. Sin `format: "json"`, Ollama no aplica
grammar/sampling constraint alguno — el modelo genera texto libre, terminando por stop-token natural
o por `num_predict` (`MAX_EXPLANATION_OUTPUT_TOKENS=220`, sin cambios). Nada más de la llamada HTTP
cambia (`model`, `system`, `prompt`, `stream: False`, `options.num_predict`).

### 2. Prompts rediseñados — texto plano con marcador de inicio, no envoltura JSON

Los 2 prompts (`SYSTEM_PROMPT_EXPLAIN`, `SYSTEM_PROMPT_PASO_A_PASO`) pierden las reglas 4 y 6
actuales (que exigían `{"respuesta": "..."}` y prohibían ecoar "el JSON de entrada... nunca el
objeto {"respuesta": "..."}"). Se reemplazan por:

- **Regla 4 nueva**: "Empezá tu respuesta completa, sin nada antes, con el texto exacto
  `###RESPUESTA###` seguido de un salto de línea, y a continuación tu explicación en prosa. No
  agregues saludos, aclaraciones, ni texto de ningún tipo antes de `###RESPUESTA###` ni después de
  terminar tu explicación."
- **Regla 6 nueva** (reemplaza la de "nunca copies el JSON de entrada"): "NUNCA repitas ni copies
  el JSON de datos que recibiste en tu respuesta — tu respuesta, después de `###RESPUESTA###`, es
  SOLO la explicación en prosa, nunca el JSON de entrada ni fragmentos de él." (mismo texto de fondo
  que hoy, adaptado a que ya no hay objeto JSON de salida que nombrar).
- El resto de las reglas (1, 2, 3, 5, 7 — brevedad, no inventar datos, no dar recomendación,
  nombrar el modelo, responder en español) **no cambian**, porque son reglas de CONTENIDO, no de
  formato de transporte.

**Por qué un marcador y no simplemente "respondé en texto plano" a secas**: sin ningún ancla, un
modelo 3B puede anteponer preámbulos ("Claro, acá va tu explicación:") que hoy el envoltorio JSON
impedía por construcción (la grammar constraint forzaba que el primer carácter fuera `{`). El
marcador `###RESPUESTA###` reproduce ese mismo efecto de ancla con una exigencia mucho más simple de
cumplir para un modelo chico (emitir un token literal fijo al principio) que reproducir sintaxis
JSON válida completa — consistente con la hipótesis central de esta spec (texto libre > estructura
exacta para modelos 3B). Se probó (criterio, no medido con el modelo real) que 3 símbolos `#`
repetidos son un patrón muy infrecuente en prosa financiera en español, minimizando falsos
positivos de un marcador que aparezca "por casualidad" en medio del texto real.

### 3. Extracción de la respuesta — robusta con fallback, mismo principio que el eco/portugués

Nueva función `_extraer_respuesta_plana(raw_text: str) -> str`:
- Si `raw_text` contiene `"###RESPUESTA###"`, se toma todo el texto DESPUÉS de la primera
  ocurrencia (con `.strip()`).
- Si no aparece (el modelo no siguió la instrucción), se usa `raw_text.strip()` completo como
  fallback — **no se rechaza la respuesta solo por faltar el marcador** (mismo principio de
  degradación con gracia ya usado en `_respuesta_es_eco_del_payload`: 2 señales independientes,
  tolerante a que el modelo no reproduzca el patrón exacto).
- Se loguea a `INFO` (no `WARNING`) cuando el marcador falta, para poder medir en producción qué
  tan seguido el modelo lo omite sin que dispare ruido de alarma — es una señal de calidad, no un
  fallo.

El check que hoy dispara `ValueError("estructura inesperada -- falta la clave 'respuesta' string")`
**desaparece** — no hay más "estructura" que pueda faltar de esa forma. Se reemplaza por el check de
la Decisión #5 (respuesta vacía).

### 4. Ajuste de `_respuesta_es_eco_del_payload` — señal 1 sin cambios, señal 2 generalizada

Confirmado con la relectura del código: la señal 1 (comparación de prefijo contra
`json.dumps(datos_del_contexto)`) **sigue aplicando exactamente igual** — el `prompt` que se le
manda a Ollama (`json.dumps({"pregunta": ..., "datos": ...})`, línea 2144-2146) **no cambia** con
esta spec (solo cambia el formato de SALIDA que se le pide, no el de entrada). Un modelo que
empieza su respuesta ecoando ese mismo JSON de entrada sigue siendo detectable por prefijo, sin
ningún ajuste.

La señal 2 (`_ECO_SEPARADOR_RE = r"\}\s*--\s*"`) asumía que el eco llegaba envuelto DENTRO de un
string JSON de salida (`{"respuesta": "<eco>} -- <explicación>"}`), donde el patrón `"} -- "` era
literalmente lo que quedaba visible al mirar el contenido de esa clave. Sin envoltura de salida, un
eco del `prompt` de entrada terminaría igual en `}` (el prompt es JSON y siempre cierra con `}`),
pero ya no hay ninguna razón para que el modelo agregue el separador `" -- "` específico después —
ese separador era un artefacto de cómo el modelo fusionaba el eco con su propia respuesta DENTRO de
la sintaxis JSON que se le exigía, no algo que el modelo elegiría espontáneamente en texto libre.

**Ajuste**: generalizar la regla 2 para aceptar cualquier separador razonable después del `}` de
cierre del eco, no solo `" -- "` — nueva regex `_ECO_SEPARADOR_RE = re.compile(r"\}\s*(--\s*|\n+)")`
(agrega salto(s) de línea como separador válido, además de conservar `--` por si el modelo lo
reproduce igual). Se mantiene la ventana de búsqueda `texto[:200]` y la condición de que haya
contenido real después (`resto`) — solo se amplía qué cuenta como "el corte entre el eco y la
explicación real". Documentar en el comentario del código que este ajuste es preventivo (no hay
evidencia empírica todavía de este patrón en texto plano, porque el patrón nunca se probó sin JSON
de salida) y que debe revisarse con logs reales tras el primer despliegue.

### 5. Reemplazo del chequeo eliminado — respuesta vacía o solo espacios

El único chequeo que dependía de JSON (clave `"respuesta"` ausente) desaparece, pero deja un hueco
real: sin la grammar constraint de `format: json`, Ollama puede devolver un `response` vacío o solo
espacios en blanco con más facilidad (ej. si genera únicamente el marcador `###RESPUESTA###` y
corta ahí por cualquier motivo, o si no genera nada útil antes de un stop-token temprano). Hoy este
caso ya causaría un mensaje vacío pasado a Telegram si no se atajaba.

**Nuevo chequeo**, mismo lugar en el loop (primer chequeo, mismo trato de reintento que los otros
3): tras `_extraer_respuesta_plana`, si el resultado es `""` (vacío tras `.strip()`), se trata igual
que un fallo de estructura — reintenta una vez, y si el segundo intento también da vacío,
`_ExplainUnavailable()`. Mismo texto de log adaptado ("respuesta vacía tras extracción").

**Total de motivos de reintento: siguen siendo 4** — `(1)` respuesta vacía (nuevo, reemplaza al
antiguo "estructura JSON inesperada"), `(2)` eco del payload (sin cambio de mecanismo, señal 2
ajustada), `(3)` portugués (sin cambio), `(4)` guard de integridad `_no_new_protected_tokens` (sin
cambio). El diseño del loop (2 intentos totales, mismos 4 chequeos secuenciales dentro del mismo
`try`, mismo criterio de "reintenta en `attempt == 0`, se rinde en `attempt == 1`") **no necesita
rediseño** — la estructura ya era genérica sobre "4 motivos posibles", no estaba acoplada a que el
motivo 1 fuera específicamente sobre JSON.

### 6. `ai_rewrite.py` — NO se convierte a texto plano en esta spec

Analizado el mismo patrón en `rewrite_parts` (líneas 379-518) y decidido NO aplicar el mismo cambio,
por una diferencia estructural real, no por inercia:

- `_fetch_explanation` transporta **1 sola respuesta** — el marcador de inicio (Decisión #2) alcanza
  para anclar dónde empieza el contenido útil, porque no hay que decidir dónde TERMINA (todo lo que
  sigue al marcador es la respuesta, sin ambigüedad).
- `rewrite_parts` transporta **N secciones independientes y posicionales** (`len(body_parts)`, que
  varía según cuántas secciones tenga el mensaje de análisis — no es un número fijo). Convertir esto
  a texto plano requeriría que el modelo reproduzca N delimitadores EXACTOS y en el ORDEN correcto
  (ej. `###SECCION_0###`, `###SECCION_1###`, ...) — un problema estrictamente más difícil que 1 solo
  marcador de inicio, con más formas de fallar: delimitador faltante, duplicado, fuera de orden, o
  con un número de secciones que no matchea. El contrato JSON actual (`_parse_json_sections`,
  líneas 338-361) YA exige exactamente esto mismo (conjunto de claves `{"0", ..., "N-1"}` ni de más
  ni de menos) pero se beneficia de que `json.loads` + comparación de sets de claves es una
  validación mecánica barata y exacta — un parser de delimitadores de texto plano tendría que
  reimplementar esa misma validación de forma más frágil (regex sobre texto libre, sin garantía de
  que el delimitador no aparezca también dentro de una sección reescrita).
- El mecanismo de placeholder-y-restitución (`_classify_lines`/`_reconstruct_section`) ya hace la
  parte más pesada del trabajo de integridad ANTES de que el texto llegue a Ollama (líneas con datos
  protegidos nunca viajan como texto editable) — el JSON de transporte en `rewrite_parts` es, a
  diferencia de `ai_explain.py`, solo un contenedor posicional sobre contenido que YA está
  mayormente protegido por placeholders, así que el beneficio esperado de sacar el JSON (reducir
  alucinación/invención de contenido) es menor ahí: el guard de integridad real no depende de la
  envoltura JSON, depende de los placeholders.
- No hay evidencia hoy (a diferencia de `ai_explain.py`, con 3 incidentes de producción fechados
  esta semana) de que `rewrite_parts` esté fallando con la misma frecuencia por estructura JSON rota
  — el pedido de Daniela es específico sobre las explicaciones interactivas, no sobre la redacción
  de tono.

**Conclusión**: `ai_rewrite.py` queda fuera de esta spec. Si en el futuro se junta evidencia de que
`rewrite_parts` también falla seguido por JSON malformado, la solución recomendada NO sería texto
plano con delimitadores (mismo problema de fragilidad que JSON, sin su ventaja de validación
mecánica), sino evaluar alternativas específicas para transporte multi-sección (ej. NDJSON línea por
línea, o simplemente aceptar el `"format": "json"` actual como el mejor trade-off disponible para
ese caso). Alcance futuro, spec separada si se decide encarar.

---

## Decisiones abiertas para Daniela

**Ninguna de arquitectura o de negocio.** El pedido ya trae la decisión de fondo aprobada
explícitamente (sacar JSON, pedir texto plano) y el resto son consecuencias técnicas directas de esa
decisión. Único punto de **redacción**, no de diseño, ajustable sin reabrir esta spec:

- El marcador de extracción se eligió como el texto literal `###RESPUESTA###` — cualquier otro
  string igual de infrecuente en prosa española serviría igual de bien (es un detalle de
  implementación sin impacto visible para el usuario final, el marcador nunca llega al mensaje de
  Telegram).

---

## Presupuesto/impacto

- **Cero cambio de latencia**: se saca un parámetro del payload (`format: "json"`), no se agrega
  ninguna llamada de red nueva. `MAX_EXPLANATION_OUTPUT_TOKENS=220` no cambia.
- **Cero cambio de longitud de mensaje final**: `_enforce_brevity`/`_MAX_EXPLANATION_CHARS=480` se
  aplican igual sobre la respuesta ya extraída — sin cambios.
- **Reducción esperada de fallos por estructura**: se elimina de raíz la clase de fallo "JSON mal
  formado o sin clave `respuesta`" — no hay forma de medir la magnitud sin desplegar y observar
  logs reales (no hay acceso al modelo real desde esta fase de diseño), pero la hipótesis (modelos
  3B son más confiables en texto libre que en JSON estricto) es la premisa ya aprobada por Daniela
  que motiva todo el cambio.
- **Riesgo nuevo, mitigado**: sin `format: "json"` forzando el primer carácter, aumenta la
  probabilidad de preámbulos/texto sobrante alrededor de la respuesta útil — mitigado por el
  marcador de extracción (Decisión #2/#3) con fallback a texto completo si el modelo no lo respeta
  (nunca se descarta una respuesta solo por eso).

---

## Criterios de aceptación

- [ ] `_fetch_explanation` ya no incluye `"format": "json"` en el payload de `POST /api/generate`.
- [ ] `SYSTEM_PROMPT_EXPLAIN` y `SYSTEM_PROMPT_PASO_A_PASO` piden texto plano con el marcador
      `###RESPUESTA###`, sin ninguna mención a `{"respuesta": "..."}` como formato de salida.
- [ ] `_fetch_explanation` ya no llama a `json.loads(raw_text)` sobre la respuesta de Ollama —
      `data["response"]` se usa directamente vía `_extraer_respuesta_plana`.
- [ ] Nueva función `_extraer_respuesta_plana(raw_text: str) -> str`: extrae el texto después de
      `"###RESPUESTA###"` si está presente; si no, devuelve `raw_text.strip()` completo. Con test
      para ambos casos.
- [ ] El chequeo `ValueError("estructura inesperada -- falta la clave 'respuesta' string")` ya no
      existe en el código.
- [ ] Nuevo chequeo de respuesta vacía tras extracción, en el mismo punto del loop (primer chequeo),
      con el mismo trato de reintento (retry en intento 1, `_ExplainUnavailable` en intento 2) que
      los otros 3 motivos.
- [ ] El loop de reintento sigue siendo de 2 intentos totales, con los 4 motivos de fallo
      (respuesta vacía / eco / portugués / guard de integridad) tratados de forma consistente —
      mismo criterio que ya exigía el fix de producción del 2026-09-04 (mencionado en el código
      actual) de unificar el trato de los 4 chequeos.
- [ ] `_respuesta_es_eco_del_payload`: señal 1 (comparación de prefijo) sin cambios de
      comportamiento (mismos tests actuales deben seguir pasando). Señal 2
      (`_ECO_SEPARADOR_RE`) ampliada para aceptar salto(s) de línea además de `"--"` como separador
      — con test nuevo cubriendo el caso de eco terminado en `}\n` sin `--`.
- [ ] `_respuesta_tiene_portugues` y `_no_new_protected_tokens`: cero cambios de código (confirmado
      en esta spec que ya operan sobre el string de respuesta, no sobre estructura JSON).
- [ ] `ai_rewrite.py`: cero cambios de código en esta spec — `rewrite_parts` sigue usando
      `"format": "json"` sin modificación.
- [ ] Todos los call sites existentes de `_fetch_explanation` (preguntas `narrativa`: `mod`/`ben`/
      `ren`; las 22 preguntas `dato_y_paso_a_paso` vía `SYSTEM_PROMPT_PASO_A_PASO`) siguen
      funcionando sin cambios de código propio — `_fetch_explanation` sigue devolviendo `str`.
- [ ] Ningún cambio en ninguna fórmula/cálculo financiero (`valuation.py`, `advanced_scoring.py`,
      `risk_fit.py`, `market_context.py` no se tocan).
- [ ] Suite de tests existente de `ai_explain.py` sigue en verde salvo los tests que testeaban
      explícitamente el contrato JSON de salida (esos se reescriben para el contrato de texto plano,
      no se eliminan sin reemplazo).

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain.py`:
  - `SYSTEM_PROMPT_EXPLAIN` (líneas 1888-1911) → reglas 4 y 6 reescritas para texto plano +
    marcador.
  - `SYSTEM_PROMPT_PASO_A_PASO` (líneas 1918-1949) → mismo ajuste de reglas 4 y 6.
  - `_fetch_explanation` (líneas 2134-2240) → saca `"format": "json"` del payload; reemplaza
    `json.loads(raw_text)` + chequeo de clave `"respuesta"` por `_extraer_respuesta_plana` +
    chequeo de vacío.
  - Nueva función `_extraer_respuesta_plana(raw_text: str) -> str` (ubicar junto a las otras
    funciones de guard, antes de `_fetch_explanation`).
  - `_ECO_SEPARADOR_RE` (línea 2040) → regex ampliada (Decisión #4).
  - Comentario del módulo (líneas 1-43) y comentarios de contexto alrededor de
    `_respuesta_es_eco_del_payload`/`_fetch_explanation` → actualizar referencias a "objeto JSON de
    salida" que ya no aplican.
- `src/investbot/ai_rewrite.py`: sin cambios (Decisión #6).
- Tests de `ai_explain.py` (ubicación real a confirmar por `implementer`/`qa` — no relevada en esta
  spec de diseño): actualizar los que testeaban el contrato `{"respuesta": "..."}` como formato de
  salida esperado; agregar tests nuevos para `_extraer_respuesta_plana` y para el chequeo de
  respuesta vacía.

---

## Restricciones

- No se toca ninguna fórmula/cálculo financiero — cambio 100% sobre el contrato de comunicación con
  Ollama.
- El guard anti-invención (`_no_new_protected_tokens`), la sanitización de sector/industry
  (`_validated_sector`/`_SECTOR_ALLOWLIST`), el rate limiter, `_get_owned_context`/Hallazgo 9 de
  `security`, y todo lo demás ya auditado no cambian.
- `ai_rewrite.py` queda explícitamente fuera de esta spec (Decisión #6) — no confundir con alcance
  pendiente olvidado.
- No se modifica `config.timeout_seconds`, `CONNECT_TIMEOUT_SECONDS`, ni
  `MAX_EXPLANATION_OUTPUT_TOKENS` — el cambio es sobre el contrato de datos, no sobre
  infraestructura de red.
- No se implementa código en esta fase — spec de diseño únicamente, sin commit ni push.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_ollama_texto_plano.md` (esta spec).

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — `security`/`qa` agregan los suyos sin reinterpretar
los ya definidos.

### Decisiones de diseño tomadas [para que `implementer` no las reabra]
1. Sacar `"format": "json"` del payload de `_fetch_explanation`, sin parámetro de reemplazo.
2. Marcador de extracción `###RESPUESTA###` al inicio de la respuesta, con fallback a texto completo
   si el modelo no lo reproduce (nunca se rechaza solo por eso).
3. Nuevo chequeo de "respuesta vacía tras extracción" reemplaza al chequeo de estructura JSON,
   mismo trato de reintento que los otros 3 motivos (eco/portugués/guard de integridad).
4. `_respuesta_es_eco_del_payload`: señal 1 sin cambios; señal 2 ampliada para aceptar salto de
   línea como separador válido, no solo `"--"`.
5. `ai_rewrite.py` NO se convierte a texto plano — justificación técnica: transporta N secciones
   posicionales, no 1 sola respuesta; el contrato JSON con validación exacta de claves es más
   robusto que reproducir N delimitadores en texto plano para ese caso específico.

### Seguridad — puntos a revisar explícitamente
- Confirmar que quitar la grammar constraint de `format: json` no abre una superficie nueva de
  "instruction injection" hacia el modelo (ej. que el usuario, vía datos de FMP que terminan en el
  payload, pueda inducir al modelo a ignorar el marcador o el resto de las reglas del prompt) — el
  guard de integridad (`_no_new_protected_tokens`) sigue siendo la defensa de fondo, independiente
  del formato de salida, pero vale confirmarlo explícitamente dado que este es justamente el tipo de
  cambio que podría aflojar una restricción sin darse cuenta.
- Confirmar que `_extraer_respuesta_plana` no introduce un vector nuevo si el texto del usuario
  (vía `sector` u otros campos ya sanitizados) contiene la secuencia literal `"###RESPUESTA###"` —
  evaluar si hace falta escapar/rechazar esa secuencia en los datos de entrada antes de armar el
  `prompt`, o si el guard de integridad ya cubre ese caso indirectamente.

---

## Revisión de seguridad (`security`)

**Código auditado en vivo** (no solo la spec): `src/investbot/ai_explain.py` completo —
`_fetch_explanation` (líneas 2134-2240), `SYSTEM_PROMPT_EXPLAIN`/`SYSTEM_PROMPT_PASO_A_PASO`
(1888-1949), `_respuesta_es_eco_del_payload` (2047-2076), `_respuesta_tiene_portugues`
(2118-2126), `_no_new_protected_tokens` (2016-2027), `_validated_sector`/`_SECTOR_ALLOWLIST`
(260-284), y el origen real de cada campo que entra en `datos_del_contexto` (`_payload_avanzado`
471-499, `_payload_narrativo` 440-464, `market_context.py` 139-235, `query_handler.py` 544-545).

**Veredicto: nada bloqueante.** El diseño de la spec es sólido — el análisis de `architect` en las
Decisiones #2/#3/#4 ya identificó correctamente el riesgo central (pérdida de la grammar
constraint) y lo mitigó con el marcador + fallback + guard de integridad independiente del
formato de transporte. Se detalla abajo la resolución de los 2 puntos del Handoff y 2 mejoras no
bloqueantes encontradas al auditar el código real.

### Punto 1 del Handoff — ¿quitar `format: json` abre instruction injection hacia el modelo?

**Resuelto: no hay superficie nueva.** Sacar la grammar constraint de salida no cambia el
`prompt` que Ollama recibe como entrada — sigue siendo `json.dumps({"pregunta": ...,
"datos": datos_del_contexto})` sin modificación (confirmado, línea 2144-2146, sin cambios en esta
spec). La pregunta relevante no es "¿cambió el prompt?" sino "¿hay algún campo dentro de
`datos_del_contexto` con texto libre no confiable que un atacante pudiera usar para inyectar
instrucciones al modelo (ej. una frase tipo "ignorá las reglas anteriores...")?" — auditado campo
por campo en `_payload_narrativo`/`_payload_avanzado`:
- `sector`: pasa por `_validated_sector` — allowlist cerrada de 11 strings fijos
  (`_SECTOR_ALLOWLIST`, línea 263-277), nunca texto libre.
- `industry`: excluido directamente del payload (confirmado, no aparece en ningún `_payload_*`).
- `posicion`/`motivo_no_comparable` (pregunta `cmp`): confirmado en `market_context.py`
  líneas 189/202/215/235 que son enums internos generados por nuestro propio código
  (`"eps_no_positivo"`, `"sin_peers_validos"`, `"un_solo_peer_valido"`, o `None`) — nunca texto de
  FMP ni de usuario.
- El resto de los campos (`altman`, `piotroski`, `criterios`, precios, ratios, `modelo`/`modelos`)
  son números o strings fijos definidos en nuestro propio código (`_MODELO_*`, constantes de
  módulo), no texto libre externo.

Conclusión: `datos_del_contexto` no tiene hoy ningún campo de texto libre no sanitizado, con o sin
`format: json`. El riesgo de instruction injection vía payload no aumenta con este cambio porque
el vector (payload de entrada) no cambia — solo cambia el formato de salida exigido. El guard de
integridad (`_no_new_protected_tokens`) sigue siendo, correctamente, la defensa de fondo contra
cualquier contenido inventado, independiente de este análisis.

### Punto 2 del Handoff — ¿`_extraer_respuesta_plana` abre un vector si algún campo contiene la
secuencia literal `"###RESPUESTA###"`?

**Resuelto: no aplica hoy, con una salvedad para el futuro.** Dado el punto 1 (ningún campo de
`datos_del_contexto` es texto libre no sanitizado), la secuencia `"###RESPUESTA###"` no puede
colarse hoy vía `sector`, `industry`, `posicion`/`motivo_no_comparable` ni ningún otro campo — todos
son enums cerrados o numéricos. No hace falta escapar ni rechazar esa secuencia en los datos de
entrada en esta spec.

**Salvedad (no bloqueante, documentar):** esta conclusión depende de que `datos_del_contexto` siga
sin campos de texto libre. Si en el futuro se agrega un campo con texto libre de FMP (ej. una
descripción de empresa, un campo `notes`/`description` sin allowlist) sin volver a auditar este
punto, un atacante que pudiera influir en ese texto (indirectamente, vía datos públicos de FMP)
podría intentar anteponer su propio `"###RESPUESTA###"` para hacer que `_extraer_respuesta_plana`
corte en el punto que el atacante eligió, colando contenido después del marcador falso como si
fuera la respuesta real del modelo. Esto seguiría estando acotado por el guard de integridad
(`_no_new_protected_tokens` seguiría bloqueando números/tickers inventados) y por brevedad
(`_enforce_brevity`), pero es un vector real de manipulación de CONTENIDO textual (no de datos
protegidos) que no existe hoy porque no hay texto libre en el payload. **Recomendación no
bloqueante**: agregar un comentario explícito junto a `_SECTOR_ALLOWLIST`/`datos_del_contexto`
indicando que cualquier campo de texto libre nuevo debe pasar por este mismo análisis antes de
sumarse al payload de `_fetch_explanation`.

### Punto 3 (guard anti-invención, eco, portugués sobre el texto ya recortado)

**Confirmado, correcto.** Los 3 mecanismos (`_no_new_protected_tokens`,
`_respuesta_es_eco_del_payload`, `_respuesta_tiene_portugues`) ya operan sobre el string de
respuesta ya extraído, no sobre la envoltura — esto es así en el código actual (operan sobre
`parsed["respuesta"]`, nunca sobre el JSON crudo) y la spec preserva el mismo orden: extracción
primero (`_extraer_respuesta_plana` reemplaza el `json.loads` + acceso a la clave), después los 3
chequeos sobre `respuesta_candidata` ya extraída, en el mismo `try` del loop. Sin regresión —
mismo patrón, misma superficie de aplicación.

### Punto 4 — mecanismo de "recortar desde la palabra clave": vector de relleno colándose sin que
el guard lo note

**Un hallazgo real, no bloqueante — mejora recomendada.** El caso no cubierto por la spec: si
`num_predict` (truncamiento por límite de tokens, `MAX_EXPLANATION_OUTPUT_TOKENS=220`) o un
stop-token corta la generación a mitad del marcador (ej. el modelo emite `"###RESP"` y se corta
ahí, sin llegar a completar `"###RESPUESTA###"`), `_extraer_respuesta_plana` no encuentra el
marcador completo y cae al fallback `raw_text.strip()` **completo, incluyendo el fragmento parcial
del marcador** (`"###RESP"` quedaría al principio del texto entregado al usuario). Esto no es una
vía de fuga de datos protegidos ni de invención de contenido (los guards de integridad/eco/
portugués no se ven comprometidos por esto — un fragmento como `"###RESP"` no contiene tokens
numéricos protegidos ni coincide con el prefijo del payload), pero sí es un defecto de calidad
visible para el usuario final que el diseño actual de `_extraer_respuesta_plana` no contempla.
**Recomendación no bloqueante**: agregar un fallback intermedio en `_extraer_respuesta_plana` que
detecte y descarte un prefijo parcial del marcador (ej. regex `^#{1,}[A-ZÁ-Úa-zá-ú]*#{0,}\s*`) antes
de aplicar el fallback de texto completo, o loguear (a `INFO`, igual que el caso de marcador
ausente) cuando el texto entregado al usuario empieza con `#` para poder medir en producción si
este caso ocurre en la práctica antes de invertir en el fix.

Segundo sub-hallazgo, informativo: la señal 2 ampliada de `_respuesta_es_eco_del_payload`
(`_ECO_SEPARADOR_RE` con `\n+` agregado) es estrictamente más permisiva que la actual — reduce
falsos negativos (eco no detectado) a costa de un riesgo teórico mínimo de falso positivo (una
respuesta legítima que por casualidad tenga `}` seguido de salto de línea cerca del principio). El
peor caso de un falso positivo es un reintento extra o, en el segundo intento, un
`_ExplainUnavailable()` — degradación de disponibilidad, no de integridad ni de confidencialidad.
Consistente con el resto del diseño (tolerante a falsos negativos de invención, no a falsos
negativos de disponibilidad). Sin objeción.

### Punto 5 — rate limiter, gate de `chat_id`, y demás mecanismos ya auditados

**Confirmado, sin impacto.** `_get_owned_context`/Hallazgo 9 (línea 223, verificación de
`chat_id`) y el rate limiter (`rate_limiter.allow`, línea 2451) están en `_dispatch_leaf`/
`build_explain_handler`, fuera de `_fetch_explanation` — esta spec no toca esas funciones ni las
líneas donde viven estos controles. Tampoco se toca `config.timeout_seconds`,
`CONNECT_TIMEOUT_SECONDS`, ni `MAX_EXPLANATION_OUTPUT_TOKENS`, confirmado en el código
(`_fetch_explanation`, línea 2148-2154, 2180) — coincide con lo declarado en la sección
"Restricciones" de la spec.

### Punto 6 — extracción por PRIMERA ocurrencia del marcador puede filtrar el system prompt

**Hallazgo adicional (segunda pasada de revisión), no bloqueante — mejora recomendada con test.**

**CWE:** CWE-200 — Exposure of Sensitive Information to an Unauthorized Actor (acá lo "sensible" es
el texto de las instrucciones internas del prompt, no un secreto de negocio).
**OWASP:** A04:2025 — Insecure Design.

La Decisión #3 toma todo el texto después de la PRIMERA ocurrencia de `"###RESPUESTA###"` en
`raw_text` (`raw_text.find(...)`, implícito en "se toma todo el texto DESPUÉS de la primera
ocurrencia"). El marcador es un string que el propio `SYSTEM_PROMPT_EXPLAIN`/`SYSTEM_PROMPT_
PASO_A_PASO` contiene literalmente como parte de sus instrucciones (Regla 4 nueva). Los incidentes
de producción ya documentados en el código (líneas 2030-2037, 2079-2097) muestran que
`qwen2.5:3b-instruct` tiene un patrón conocido de "ecoar" contenido que vio en el prompt —hasta
ahora ese eco era del payload de datos (`_respuesta_es_eco_del_payload`, que compara SOLO contra
`datos_del_contexto`, nunca contra el `system_prompt`).

Con el cambio a texto plano, si el modelo repite/parafrasea parte de sus propias instrucciones de
sistema antes de responder (mismo patrón de eco ya observado, ahora aplicado al `system` en vez de
al `prompt` de datos), es plausible que reproduzca el marcador `"###RESPUESTA###"` como parte de
ese eco, y recién más adelante emita su propio marcador real seguido de la explicación genuina.
Tomar la PRIMERA aparición cortaría en la del eco, dejando en el texto extraído: el resto de las
reglas del sistema repetidas + el marcador real + recién ahí la explicación — filtrando al usuario
final fragmentos de las instrucciones internas del bot. Ninguno de los 3 guards existentes lo
cubre: `_respuesta_es_eco_del_payload` no compara contra `system_prompt`, y ni `_no_new_protected_
tokens` ni `_respuesta_tiene_portugues` disparan ante prosa española sin números/tickers.

**Remediación (bajo esfuerzo, no cambia la firma `str -> str`):** usar la ÚLTIMA ocurrencia
(`rfind`) en vez de la primera, ya que un eco de instrucciones ocurre necesariamente ANTES de que
el modelo llegue a responder de verdad:

```python
def _extraer_respuesta_plana(raw_text: str) -> str:
    idx = raw_text.rfind("###RESPUESTA###")  # última ocurrencia, no la primera
    if idx == -1:
        return raw_text.strip()
    return raw_text[idx + len("###RESPUESTA###"):].strip()
```

Alternativa más estricta (preferible si no aumenta la tasa de fallback en la práctica): exigir que
el marcador esté al PRINCIPIO del texto (`raw_text.strip().startswith(...)`), coherente con la
Regla 4 ("empezá tu respuesta completa, sin nada antes"); un eco previo simplemente cae al mismo
fallback de texto completo ya aceptado por la spec, sin filtrar un fragmento parcial de
instrucciones.

**Esfuerzo estimado:** 15-30 minutos + 1 test que simule un eco de instrucciones antes del marcador
real (ej. `raw_text` con el texto de una regla del prompt seguido de `"###RESPUESTA###"` y recién
ahí la explicación).

### Resumen para `qa`/`implementer`

- **Bloqueante**: ninguno.
- **Cambio recomendado antes de implementar (Punto 6)**: usar `rfind` (o `startswith` estricto) en
  `_extraer_respuesta_plana` en vez de tomar la primera ocurrencia de `"###RESPUESTA###"`, con test
  de eco-de-instrucciones-del-sistema.
- **Mejora recomendada, no bloqueante (Punto 4)**: manejar el caso de marcador `"###RESPUESTA###"`
  truncado a la mitad en `_extraer_respuesta_plana`, con test de cobertura para ese caso
  específico (texto de entrada como `"###RESP"` sin completar el marcador).
- **Mejora recomendada, no bloqueante (Punto 2)**: comentario en el código dejando explícito que
  cualquier campo de texto libre nuevo en `datos_del_contexto` requiere re-auditar el riesgo de
  inyección del marcador antes de sumarse al payload.
- El resto de los criterios de aceptación ya definidos por `architect` cubren correctamente la
  superficie de seguridad de este cambio — `qa` puede avanzar a definir criterios de cobertura de
  test sin agregar nuevos requisitos de seguridad.

---

## Criterios QA para Spec: Ollama texto plano en `ai_explain.py` [Iter-1]

**Código de tests auditado en vivo**: `tests/test_ai_explain.py` completo (helper de mock server
líneas 45-72; tests de `_fetch_explanation` líneas 996-1107; tests de eco líneas 1146-1229; tests
de portugués líneas 1259-1332) y `tests/test_ai_rewrite.py` (confirmado: no referencia
`###RESPUESTA###`, `_extraer_respuesta_plana` ni nada de `ai_explain.py` — usa su propio mock
independiente, ver Caso 6 abajo). Base: los 12 criterios de aceptación ya fijados por `architect`
y las 2 mejoras no bloqueantes de `security` (Puntos 2 y 4 de su revisión) están correctos y
completos en su alcance — no se reinterpretan, se les agrega testabilidad concreta abajo.

### Tipo de prueba principal
**Unit testing** (pytest + pytest-httpx, mismo patrón ya usado en `test_ai_explain.py` vía el
helper `httpx.MockTransport`/`respond_with_json` de línea 45-72). No aplica integration/E2E: no
hay Ollama real en CI, y el contrato ya se valida hoy exclusivamente con HTTP mockeado — este
cambio no altera esa estrategia.

### Cobertura mínima requerida
- [ ] Code coverage ≥ 90% en `_extraer_respuesta_plana` (función nueva, lógica de negocio aislada
      y crítica para la calidad del mensaje final al usuario — no es cosmético, corresponde al
      nivel "Alto" de la tabla de riesgo, no "Medio").
- [ ] Branch coverage 100% en `_extraer_respuesta_plana`: las 3 ramas — marcador presente y
      completo, marcador ausente (fallback total), marcador truncado a la mitad (fallback parcial,
      Caso 5 abajo) — deben tener al menos un test cada una.
- [ ] Los 12 criterios de aceptación de `architect` (sección "Criterios de aceptación") cubiertos
      por al menos un test — ninguno queda solo verificado por lectura de código.
- [ ] Las 2 mejoras no bloqueantes de `security` (Puntos 2 y 4) cubiertas: Punto 4 con test
      obligatorio (Caso 5 abajo); Punto 2 con el comentario de código exigido como criterio de
      aceptación (no requiere test, es documentación — ver "Qué NO se prueba y por qué").

### Casos obligatorios

1. **Happy path — marcador presente y completo**: Ollama devuelve
   `"###RESPUESTA###\nExplicación real."` → `_extraer_respuesta_plana` devuelve
   `"Explicación real."` (sin el marcador, `.strip()` aplicado). Test directo a la función más un
   test de integración del loop (`_fetch_explanation` con mock devolviendo ese `response`) que
   confirma que el mensaje final entregado al usuario es exactamente el texto extraído, pasado por
   `_enforce_brevity`.

2. **Relleno antes del marcador**: Ollama antepone preámbulo, ej.
   `"Claro, acá va:\n###RESPUESTA###\nExplicación real."` → debe extraerse solo
   `"Explicación real."`, descartando el preámbulo. Cubre exactamente el riesgo que motiva la
   Decisión #2 del `architect` ("sin ancla, un modelo 3B puede anteponer preámbulos").

3. **Relleno después de la explicación**: Ollama agrega texto extra tras terminar, ej.
   `"###RESPUESTA###\nExplicación real.\n\nEspero que te sirva!"` → la spec, tal como está
   redactada, **no pide recortar el final** (Decisión #3: "todo lo que sigue al marcador es la
   respuesta, sin ambigüedad" — no hay delimitador de cierre). Test que confirma este
   comportamiento tal como fue diseñado (el sobrante de cierre pasa íntegro, y queda sujeto solo a
   `_enforce_brevity`/480 caracteres) — no es un bug, pero debe quedar con test explícito para que
   no se confunda con una regresión el día que alguien note relleno de cierre en producción. Si
   `architect` quisiera acotar esto en el futuro, es backlog (ver más abajo), no bloqueante de esta
   spec.

4. **Marcador ausente por completo**: Ollama devuelve texto sin `"###RESPUESTA###"` en ningún
   lado → fallback a `raw_text.strip()` completo, log a `INFO` (no `WARNING`, confirmar en el test
   con `caplog` filtrando por nivel, mismo patrón que los tests existentes con `caplog` en líneas
   996/1006/1016/1026/1036/1088). **Camino de fallo confirmado**: este caso NO es un motivo de
   reintento (a diferencia de los 4 motivos del loop) — es degradación con gracia, la respuesta se
   acepta igual y sigue el flujo normal hacia los 3 chequeos siguientes (eco/portugués/guard). Solo
   se vuelve motivo de reintento si, además, el resultado del fallback es vacío tras `.strip()`
   (ese es el motivo 1 nuevo, no "marcador ausente" en sí).

5. **Marcador truncado a la mitad (mejora de `security`, Punto 4 — obligatorio, no opcional)**:
   `raw_text = "###RESP"` (u otro corte parcial, ej. `"###"`, `"###RESPUESTA#"`) → el test debe
   confirmar que **ningún fragmento del marcador parcial** (`"###RESP"`, `"###"`, etc.) queda
   presente en el string devuelto por `_extraer_respuesta_plana` ni, en un test de integración del
   loop completo, en el mensaje final enviado a Telegram. La spec actual (Decisión #3) NO
   implementa el fallback intermedio que `security` recomendó (regex de prefijo parcial) — así que
   este test, tal como está la spec HOY, **fallaría** con la implementación literal de la Decisión
   #3 (el fallback devolvería `"###RESP"` íntegro). Esto es una diferencia real detectada por QA:
   **se incorpora como criterio de aceptación obligatorio nuevo** (no opcional como lo dejó
   `security`) porque es exactamente el tipo de defecto visible al usuario final que este pipeline
   existe para prevenir antes de escribir código. `implementer` debe agregar el fallback
   intermedio que `security` esbozó (detectar y descartar un prefijo parcial de `#`/letras antes
   de aplicar el fallback de texto completo) — no es opcional aunque `security` lo etiquetó como
   "no bloqueante" (esa mejora quedó no-bloqueante para el veredicto de *seguridad*, pero acá se
   vuelve bloqueante para el veredicto de *calidad*, que es el criterio de esta sección).
   Casos de test mínimos: marcador cortado en `"###"`, en `"###RESP"`, en `"###RESPUESTA#"`
   (un carácter antes de completarse) — 3 variantes de corte para no dejar un límite fuera de
   partición de equivalencia sin cubrir.

6. **`ai_rewrite.py` no se ve afectado**: `tests/test_ai_rewrite.py` sigue en verde sin
   modificación de ningún test existente (confirmado: no importa nada de `ai_explain.py`, usa su
   propio helper de mock HTTP independiente) — correr la suite completa de `test_ai_rewrite.py`
   antes y después del cambio y diffear resultados (deben ser idénticos). Test explícito nuevo
   (regresión, no solo "no tocarlo"): `rewrite_parts` sigue mandando `"format": "json"` en el
   payload — reusar el patrón de `test_fetch_explanation_num_predict_y_formato_correctos` (línea
   1096-1107) pero apuntando a `rewrite_parts`, capturando el body y confirmando
   `captured["body"]["format"] == "json"` sin cambios.

7. **Guards (eco/portugués/integridad) operan sobre el texto ya recortado**: 3 tests de
   integración del loop, uno por guard, con el marcador presente Y contenido problemático después
   de él — ej. `"###RESPUESTA###\n" + eco_del_payload` debe seguir siendo detectado como eco
   (reusar `_RESPUESTA_PORTUGUES_EVIDENCIA_REAL` y el fixture de eco de línea ~1146 pero
   envueltos ahora en texto plano con marcador en vez de en el JSON `{"respuesta": ...}` actual).
   Esto confirma en código, no solo por lectura, la Decisión #3 del `architect` y el Punto 3 de
   `security`.

8. **Reemplazo del chequeo eliminado — reintento por respuesta vacía**: reescribir
   `test_fetch_explanation_json_sin_clave_respuesta` (línea 1036) — el test actual fuerza
   `{"algo_raro": "x"}` para disparar el `ValueError` de estructura; el nuevo equivalente debe
   forzar `response=""` o `response="   "` (o `"###RESPUESTA###"` solo, sin texto después, que es
   el ejemplo que `architect` da en Decisión #5) y confirmar que dispara el mismo camino de
   reintento (mismo texto de log adaptado, mismo comportamiento en `attempt==0` vs `attempt==1`
   que ya cubren `test_fetch_explanation_reintenta_una_vez_y_se_recupera` línea 1046 y
   `test_fetch_explanation_ambos_intentos_fallan_cae_a_unavailable` línea 1069 — reusar esa misma
   estructura de test, solo cambiando el motivo de fallo inyectado).

9. **`_ECO_SEPARADOR_RE` ampliada**: test nuevo con eco terminado en `"}\n"` sin `"--"` (caso que
   la regex actual NO detectaba, mencionado en el criterio de aceptación de `architect`) —
   confirmar que ahora SÍ dispara reintento por eco, con el mismo patrón que
   `test_fetch_explanation_eco_del_payload_reintenta_y_se_recupera` (línea 1164). Test de no
   regresión: el caso ya cubierto por `test_respuesta_es_eco_detecta_el_patron_exacto_de_la_evidencia_real`
   (línea 1146, separador `"} -- "`) sigue pasando sin cambios.

10. **Caso límite — 220 tokens exactos / corte por `num_predict` sin relación con el marcador**:
    respuesta larga que se corta a mitad de la EXPLICACIÓN (no del marcador) por
    `MAX_EXPLANATION_OUTPUT_TOKENS=220` — confirmar que esto sigue funcionando igual que hoy (no es
    un caso nuevo introducido por esta spec, pero vale un test de regresión explícito dado que se
    toca el mismo código de extracción).

### Testabilidad
- [ ] `_extraer_respuesta_plana` es una función pura (`str -> str`, sin I/O ni estado) — testeable
      de forma aislada sin mocks, tal como ya lo son `_respuesta_es_eco_del_payload`/
      `_respuesta_tiene_portugues`/`_no_new_protected_tokens` hoy. Confirmar que `implementer` la
      deja así (no la acopla a `_fetch_explanation` ni a I/O de red).
- [ ] El mock HTTP existente (`httpx.MockTransport`, helper líneas 45-72) sigue siendo el único
      mecanismo necesario para testear `_fetch_explanation` end-to-end — no hace falta levantar
      Ollama real ni un nuevo framework de mocking.
- [ ] Los tests que hoy arman el `response` mockeado como `json.dumps({"respuesta": texto})`
      (patrón repetido en ~15 tests del archivo) deben migrar a armar el `response` como texto
      plano con marcador (`f"###RESPUESTA###\n{texto}"`) — es un cambio mecánico de fixture, no de
      lógica de test, pero toca casi todos los tests de `_fetch_explanation` del archivo y debe
      hacerse de forma consistente (ver fixture nuevo propuesto abajo).

### Fixtures mínimos que faltan
- [ ] Helper de fixture nuevo, análogo al `respond_with_json` de línea 45-72 pero para texto
      plano — ej. `_response_texto_plano(texto: str, con_marcador: bool = True) -> httpx.Response`
      que arma `{"response": f"###RESPUESTA###\n{texto}" if con_marcador else texto}` — para no
      duplicar la construcción del string marcador en cada test.
- [ ] Constante de fixture para el Caso 5 (marcador truncado): `_MARCADOR_TRUNCADO_VARIANTES = [
      "###", "###RESP", "###RESPUESTA#"]` reusable en el test parametrizado del Caso 5.
- [ ] Reconfirmar/actualizar la constante `_RESPUESTA_PORTUGUES_EVIDENCIA_REAL` y el fixture de
      eco existente (línea ~1146) para que puedan envolverse tanto en el formato viejo (si algún
      test de regresión lo necesita transitoriamente) como en el nuevo formato de marcador — no es
      necesario duplicarlas, alcanza con envolver el mismo texto en el nuevo prefijo al llamar al
      helper nuevo.

### Qué NO se prueba y por qué
- **Contenido semántico de la explicación generada por Ollama real** (si la prosa es correcta,
  bien redactada, o útil) — fuera de alcance de unit testing, no medible sin el modelo real
  corriendo, y explícitamente fuera del alcance de esta spec (que es sobre el contrato de
  transporte, no sobre calidad de contenido).
- **Medición real de cuánto mejora la tasa de fallos de formato en producción** — la spec ya lo
  aclara en "Presupuesto/impacto" ("no hay forma de medir la magnitud sin desplegar y observar
  logs reales") — es responsabilidad de observabilidad post-deploy, no de esta suite de tests.
- **El Punto 2 de `security`** (comentario de código sobre texto libre futuro en el payload) — es
  un criterio de documentación, no de comportamiento; se verifica por revisión de código
  (`implementer` lo agrega, `qa`/`architect` lo confirman leyendo el diff), no con un test
  automatizado. No aplica cobertura de test a un comentario.
- **Caso 3 (relleno después de la explicación) como motivo de rechazo o recorte** — como se explica
  arriba, la spec actual no pide recortar el final; el test de Caso 3 documenta el comportamiento
  tal cual está diseñado, no agrega una exigencia nueva de recorte. Si se decide en el futuro que
  el sobrante de cierre debe recortarse, es una decisión de `architect` (spec nueva o Iter-2), no
  algo que QA pueda decidir unilateralmente vía criterios de test.
- **Ollama real / integración con el servicio en Docker** — confirmado en el modo de operación de
  este agente y en el estado del proyecto: no hay acceso al modelo real desde la fase de diseño;
  la verificación en el bot real de WhatsApp/Telegram con Ollama real queda como paso manual
  post-deploy, igual que otras features recientes del ecosistema (ej. la encuesta de salida de
  trial de FoodMind quedó con el mismo tipo de pendiente).
- **Performance/latencia** — la spec declara "cero cambio de latencia" (se saca un parámetro, no se
  agrega red); no amerita test de performance dedicado, es consecuencia lógica del diseño, no un
  comportamiento a medir.

### Criterio de exit de QA
- Todos los tests de `test_ai_explain.py` y `test_ai_rewrite.py` pasan (BUILD SUCCESS / suite
  verde), incluidos los 10 casos obligatorios de arriba.
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (todos los tests son deterministas — mocks fijos, sin
  dependencia de tiempo real ni de red real).
- El criterio de aceptación nuevo del Caso 5 (marcador truncado) está en verde — es el único
  criterio de esta sección que, tal como está redactada la spec de `architect` hoy, requiere un
  cambio de diseño mínimo (el fallback intermedio) antes de poder pasar; `implementer` debe
  incorporarlo, no se puede marcar como "no aplica".

**Conclusión para el pipeline**: la spec queda lista para `implementer`, con UNA salvedad
puntual — el Caso 5 exige que `implementer` sume el fallback intermedio para marcador truncado que
`security` había dejado como sugerencia opcional; acá se vuelve un criterio de aceptación
obligatorio. Todo lo demás (extracción limpia, extracción con relleno, ausencia total del
marcador, guards sobre texto recortado, `ai_rewrite.py` sin tocar) ya tiene cobertura de test
definida arriba sin necesidad de reabrir ninguna decisión de diseño de `architect` ni de
`security`.
