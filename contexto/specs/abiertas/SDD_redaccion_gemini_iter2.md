## Spec Patch [Iter-2] — Redacción de las explicaciones de Gemini (template, sin tope de 480, números, gráficas)

Rol: `architect`. Fecha: 2026-11-12. Destino sugerido: `contexto/specs/abiertas/SDD_redaccion_gemini_iter2.md`. Siguiente: `security` (CS-23 a CS-30), `qa`, luego `implementer`. `dba`, `backend` y `frontend` no participan.

> **PRECEDENCIA:** este patch extiende `SDD_migracion_gemini.md`. Orden: este patch (Iter-2 de redacción) > sec. 15 > 14 > 13 > 12 > 1–11 de la spec base. Donde este patch cambia un criterio de la spec base (CA-11, CA-67, CS-5 y los tests del tope de 480), el texto de acá PREVALECE. Todo lo demás de la spec base (guards, IDOR, allowlist, EXPLAIN_UNAVAILABLE_MSG, rewrite) NO cambia.

Qué leí yo mismo:
- `SDD_migracion_gemini.md`: encabezado, índice de secciones y los CA/CS vigentes.
- `ai_explain.py`: l.114-154, 371-441, 519-535, 644-700, 767-866, 1275-1285, 1934-2126, 2335-2480.
- `ai_rewrite.py`: l.60-135 y `SYSTEM_PROMPT` l.233-263.
- `gemini_client.py`: filtro de salida.
- `scripts/gemini_smoke.py`: cabecera y l.395-425.
- `requirements.txt` y `Dockerfile`.
- Tests: l.830-850, 975-1000, 1640-1660, 4020-4045, 4630-4655, 5140-5165, 5325-5345 y 5555-5605 de `tests/test_ai_explain.py`; `tests/test_traceability.py` l.285-345.

No ejecuté nada contra Gemini. No hay ejemplo real de salida mala.

---

## 0. Diagnóstico: hipótesis ordenadas por evidencia en el código

No tengo un ejemplo pegado. Lo siguiente es lo que SÍ está en el código y explica "mala redacción" aun sin ver la salida. Etiqueté cada punto como VERIFICADO EN CÓDIGO o NO VERIFICADO.

| # | Hipótesis | Estado | Evidencia |
|---|---|---|---|
| H1 | **Contradicción de formato de números.** Los bloques `Dato`/`Cuenta`/`Desglose` usan `f"${x:,.2f}"` (`$1,234.56`, con coma de miles). Pero la regla 4 de ambos prompts manda "sin separador de miles". El modelo recibe `cuenta` con miles y una instrucción opuesta. | VERIFICADO EN CÓDIGO | `ai_explain.py:839` `_money`; l.1947 y 1980 (regla 4) |
| H2 | **Floats crudos en el JSON.** `json.dumps({"datos": ...})` serializa floats a precisión completa (`496.33333333333337`, `394328000000.0`, `1.5e-05`). El modelo ve "formato máquina" y lo mete en la prosa o lo redondea (y el guard lo rechaza). | VERIFICADO el mecanismo (l.2094); NO VERIFICADO que ocurra con contextos reales | `_fetch_explanation` no redondea nada |
| H3 | **`_enforce_brevity` corta en el último "." antes de 480, y el "." de un decimal cuenta.** Un corte puede dejar "…vale $285." (número mutilado). Además, 480 con 2-4 oraciones deja la explicación a medias. | VERIFICADO EN CÓDIGO | l.1992-1996: `texto.rfind(".", 0, 480)` |
| H4 | **Markdown filtrado.** Gemini suele emitir `**negrita**` y viñetas `* `. Las explicaciones se envían SIN `parse_mode` (CA-46), así que el usuario ve los asteriscos literales. Los prompts no dicen nada del formato de salida. | NO VERIFICADO (muy probable, no observado) | los prompts no prohíben Markdown |
| H5 | **El guard empuja a texto evasivo.** `protected_tokens` incluye `\b[A-ZÁÉÍÓÚ]{2,10}\b` (siglas y palabras en mayúsculas) y cualquier cifra. Cualquier sigla (USD, EPS, WACC, FCF) o cifra (un "1." de lista numerada, un año, "18 centavos") que NO esté en el payload rechaza TODA la respuesta (`EXPLAIN_UNAVAILABLE_MSG`). El modelo no sabe esto porque el prompt no se lo dice. | VERIFICADO EN CÓDIGO (`ai_rewrite.py:94-135`, `_no_new_protected_tokens`) | sección 3 |
| H6 | **Sobrecarga de obligaciones en el prompt de paso a paso.** La regla 1 pide en 2 a 4 oraciones cortas: no recalcular, no repetir números, tono criollo, analogía, explicar CADA término de la cuenta y el significado, más la regla 5 (nombrar el modelo). Con 5 a 9 términos en la cuenta es imposible de cumplir. | VERIFICADO EN CÓDIGO (l.1960-1986) | prompt |
| H7 | **Claves técnicas filtradas.** Las claves del payload son `snake_case` (`dcf_valor_terminal_descontado`, `escenario_elegido`) y el modelo las puede copiar a la prosa. | NO VERIFICADO | payloads l.371-441 |
| H8 | **Thinking `minimal` con muchas restricciones simultáneas.** `maxOutputTokens=1024` cuenta los tokens de pensamiento. Hay que descartar `MAX_TOKENS` (que hoy cae a fallback). | NO VERIFICADO | `MAX_EXPLANATION_OUTPUT_TOKENS = 1024` |

Conclusión: H1, H3, H5 y H6 son defectos de diseño reales y verificados, no especulación. Se arreglan sí o sí en este patch, independientemente de qué muestre el ejemplo real. H4, H7 y H8 se miden con el smoke (sección 9) antes y después.

---

## 1. Spec: Redacción de explicaciones — template, sin tope de 480, números presentados, gráficas

### Estado actual

- `SYSTEM_PROMPT_EXPLAIN` (`ai_explain.py:1936`) y `SYSTEM_PROMPT_PASO_A_PASO` (l.1960): 6 reglas rígidas. "2 a 4 oraciones cortas", "sin introducción ni cierre", "copiá los números sin separador de miles y con punto decimal".
- `_MAX_EXPLANATION_CHARS = 480` y `_enforce_brevity` (l.1989-1996), aplicados al final de `_fetch_explanation` (l.2126).
- `MAX_EXPLANATION_OUTPUT_TOKENS = 1024` (l.1988).
- `datos_del_contexto` va al modelo con floats crudos; `cuenta` (string con `$1,234.56`) se agrega en l.2380.
- `datos_tokens` se calcula sobre ese mismo JSON (l.2364).
- `_build_leaf_message` (l.767) une con `"\n\n"`: transparencia → Dato → Cuenta → Desglose → respuesta → Fórmula/Fuente → disclaimer. Sin tope total; confía en que la respuesta mide ≤ 480.
- Las explicaciones se envían sin `parse_mode`.
- `ai_rewrite.SYSTEM_PROMPT` regla 7 "condensar" aplica al primer mensaje, que SÍ usa `parse_mode="Markdown"`.
- No hay librería de gráficos ni `send_photo` (`requirements.txt`: solo `python-telegram-bot==21.6` y `httpx==0.27.2`).

### Estado objetivo

1. La respuesta del modelo no tiene tope de 480 ni de "2 a 4 oraciones". Tiene un tope alto de seguridad (`_MAX_RESPUESTA_CHARS = 1800`, ajustable por medición) más un ajuste dinámico contra el límite de Telegram (4096) hecho en `_build_leaf_message`. El corte, si ocurre, es en límite de párrafo u oración y NUNCA dentro de un número.
2. Los prompts piden una estructura fija de 3 bloques con encabezado emoji, párrafos separados por línea en blanco, sin Markdown. Mantienen todas las reglas de integridad y las explican al modelo (que antes las ignoraba): siglas, cifras nuevas, símbolos `$`/`%`.
3. El modelo recibe los números ya presentados y legibles (`$1,234.56`, `15.3%`, `$2.5 mil millones`), con un formato único y coherente con `Dato`/`Cuenta`/`Desglose`. El guard NO se relaja: acepta exactamente lo que acepta hoy más los tokens de la versión presentada.
4. `_normalize_layout` limpia Markdown y espaciado ANTES de los guards, de modo que lo que se chequea es lo que se envía.
5. `maxOutputTokens` de explicaciones sube a 2048.
6. Gráficas (Fase 2): barras Unicode generadas 100% en Python a partir del contexto almacenado, insertadas en el mensaje (no pasan por el modelo ni por el guard). Imágenes (matplotlib o Pillow) y QuickChart: decisión documentada en la sección 6; imágenes quedan como spec aparte condicional.

### Criterios de aceptación

Ver sección 8 (CA-70 a CA-88, CA-S7/S8, CS-23 a CS-30).

### Artefactos a crear/modificar

Ver sección 10.

### Restricciones

Ver sección 11.

---

## 2. Template de respuesta (decisión (a))

### 2.1 Decisiones

- **Quién arma qué.** Python sigue armando el marco determinístico (transparencia, Dato, Cuenta, Desglose, Fórmula/Fuente, disclaimer). El modelo escribe SOLO el bloque de explicación, con exactamente 3 secciones separadas por UNA línea en blanco.
- **Encabezados de sección:** una línea con emoji + etiqueta fija, definidos en una constante compartida `SECTION_LABELS`. Los usan el prompt, el normalizador y los tests, y así no hay desvío entre ellos. Están elegidos para pasar el guard: sin dígitos, sin palabras en MAYÚSCULAS, sin `✅`/`❌` (esos dos son tokens protegidos), sin emojis con ZWJ (U+200D lo rechaza `violates_output_filter`).

| Variante | Sección 1 | Sección 2 | Sección 3 |
|---|---|---|---|
| Paso a paso (22 preguntas `dato_y_paso_a_paso`) | `💡 En simple` | `🔎 Qué es cada pieza` | `🧭 Qué significa el resultado` |
| Narrativa (`mod`, `ben`, `ren`, `ver`, etc.) | `💡 En simple` | `🔎 Qué dicen los datos` | `🧭 Cómo leerlo` |

- **Espaciado:** una línea en blanco entre secciones. Dentro de "Qué es cada pieza", cada ítem va en una línea que empieza con `• ` (sin línea en blanco entre ítems). Nunca más de una línea en blanco consecutiva. `_normalize_layout` garantiza esto aunque el modelo no lo haga.
- **Negrita:** ninguna. Sin `parse_mode` los `**` se verían literales, y activar `parse_mode` contradice CA-46 y reintroduce el riesgo del incidente 2026-09-04 (`Can't parse entities`). Énfasis solo con el emoji de sección y la estructura.
- **Emojis:** solo los de los encabezados (3 por mensaje) más los que ya tiene el marco determinístico (🤖 📌 🧮 🔍 📐 📊). Cero emojis en la prosa.
- **Numeración:** PROHIBIDO numerar pasos o ítems con dígitos. Un "1." es un token protegido que no está en el payload, y el guard rechaza TODA la respuesta. Se usa `• ` y las palabras "primero", "después", "por último".
- **Sin saludo ni cierre.** Sin repetir la pregunta.
- **Largo:** guía orientativa de ~600 a 1200 caracteres, sin tope por oraciones. Tope duro: sección 4.

### 2.2 Ejemplo A — paso a paso, "valor justo" (`vf`), mensaje completo

Cifras ilustrativas, las mismas del fixture de tests (precio $430.00; Múltiplos $500.00; Graham $480.00; DCF $510.00). Las líneas entre corchetes son los bloques determinísticos existentes, sin cambios.

```
🤖 Con ayuda de Gemini

📌 Dato: Valor Justo Total (Conservador): $496.00 — precio actual: $430.00

🧮 Cuenta: ($500.00 + $480.00 + $510.00) / 3 = $496.00

[📊 Gráfico — solo Fase 2, ver sección 6.3]

[🔍 Desglose — bloque determinístico existente]

💡 En simple
Este análisis usa el Valor Justo Total, que promedia tres formas distintas de estimar cuánto debería valer la acción: Múltiplos, Graham y flujo de caja descontado. La idea es tener un precio de referencia que no dependa de un solo método.

🔎 Qué es cada pieza
• Múltiplos ($500.00): compara a la empresa con otras parecidas del mismo sector.
• Graham ($480.00): una fórmula clásica que parte de lo que la empresa gana por acción y de cuánto se espera que crezca.
• Flujo de caja descontado ($510.00): suma el dinero que la empresa podría generar a futuro, traído a valor de hoy.
• Valor Justo Total ($496.00): el promedio de los tres métodos.
• Precio actual ($430.00): lo que cuesta hoy la acción en el mercado.

🧭 Qué significa el resultado
El valor justo estimado queda 15.3% por encima del precio actual. Dicho simple: según estos tres métodos, la acción hoy se negocia por debajo de lo que estaría valiendo. Es una estimación que depende de supuestos, no una predicción ni una recomendación.

📐 Fórmula: [existente]
📊 Fuente del dato: [existente]

[disclaimer existente]
```

Nota sobre el ejemplo: "flujo de caja descontado" en palabras evita depender de que la sigla DCF esté en algún texto del payload. Sección 3.4 y riesgo R-3: el implementer verifica contra los payloads reales qué siglas están disponibles.

### 2.3 Ejemplo B — narrativa (`ren`)

Payload presentado: `{"modelo": "...", "roe": "18.4%"}` (cifra ilustrativa).

```
🤖 Con ayuda de Gemini

📌 Dato: ROE: 18.4%

💡 En simple
El retorno sobre el patrimonio mide cuánta ganancia genera la empresa con el dinero que aportan sus accionistas.

🔎 Qué dicen los datos
El retorno sobre el patrimonio de la empresa es 18.4%. Por cada dólar que ponen los accionistas, el negocio produce una ganancia del 18.4%.

🧭 Cómo leerlo
Un valor alto suele indicar que la empresa aprovecha bien el capital que tiene. Conviene mirarlo junto con el nivel de deuda, porque endeudarse también puede inflar este número. Es un dato para entender el negocio, no una señal de compra o de venta.

[disclaimer existente]
```

Nota: el ejemplo evita deliberadamente "18 centavos". "18" no está como token en el payload, y `18.4` sin `%` tampoco coincide con `18.4%`. Ese caso lo rechazaría el guard. Esta restricción va al prompt (regla 1, sección 3.3).

### 2.4 `_normalize_layout(texto) -> str` (nueva, pura, idempotente)

Se aplica ANTES de los guards. En orden:
1. `\r\n`/`\r` → `\n`; quitar espacios al final de cada línea.
2. Quitar `**`, `__`, `` ` ``, y los `#` iniciales de línea (`^#{1,6}\s*`).
3. Viñetas de línea: `^\s*[*\-–]\s+` → `• `.
4. Si una línea de sección conocida (`SECTION_LABELS`) no tiene línea en blanco antes, insertarla.
5. Colapsar 3 o más `\n` seguidos en `\n\n`; `strip()` final.

No rechaza por estructura: si el modelo no usa las etiquetas, el texto igual se entrega, normalizado. El rechazo por estructura aumentaría el fallback y empeoraría lo que la usuaria ve. La conformidad con la estructura se MIDE en el smoke (CA-S7), no se impone con un reintento (costo y rate limit: CS-30).

Orden en `_fetch_explanation`: `generate_text` → `_normalize_layout` → `_no_new_protected_tokens` → `violates_output_filter` → devolver el texto normalizado SIN cortar. El recorte pasa a `_build_leaf_message` (sección 4).

---

## 3. Prompts nuevos (decisión (b))

Reglas de forma que los tests de la spec base fijan y que se conservan:
- Numeración `(?m)^(\d+)\. ` contigua; ahora 1 a 7. Cuidado: ninguna otra línea del prompt puede empezar con `dígito + ". "`.
- Contienen `modelo` y `español rioplatense`.
- Paso a paso contiene `cuenta` y `YA ESTÁ CALCULADA`.
- No contienen `{"respuesta"`, `###RESPUESTA###` ni `portugués`.
- CA-41: `systemInstruction == constante del módulo`, sin interpolación en runtime. El prompt se arma UNA vez al importar, a partir de `SECTION_LABELS`.

### 3.1 `SYSTEM_PROMPT_EXPLAIN` (narrativa)

```
Sos un profesor de finanzas que explica con claridad y calidez, en español
rioplatense, por un chat de Telegram. Vas a recibir un JSON con una pregunta
puntual y los datos ya calculados para responderla. Escribí como lo haría una
persona que explica bien: frases completas y naturales, sin telegrafismo.

Reglas, sin excepción:
1. Integridad de los datos: usá ÚNICAMENTE las cifras y datos del JSON.
   Nunca inventes, estimes ni calcules cifras nuevas (diferencias,
   porcentajes, promedios, proyecciones, años, fechas). Para comparar usá
   palabras ("bastante por encima", "casi igual") o citá cifras que ya
   estén en el JSON. Los valores del JSON ya vienen escritos para leerse
   (por ejemplo "$1,234.56", "15.3%", "$2.5 mil millones"): copialos
   exactamente como están, con su símbolo y sus decimales, sin cambiar los
   separadores ni expresarlos de otra forma.
2. Nunca des una recomendación de compra o venta ni asesoramiento
   financiero personalizado: solo explicá qué significa el dato.
3. No escribas palabras enteras en MAYÚSCULAS ni siglas, salvo que
   aparezcan tal cual en el JSON; si no, escribí el nombre completo en
   palabras. No uses números ni listas numeradas para ordenar ideas:
   usá viñetas con "• " y palabras como "primero" o "después".
4. Formato: texto plano. Nada de Markdown (ni asteriscos, ni guiones bajos,
   ni numerales, ni tablas), ni enlaces, ni menciones con arroba. Nunca
   escribas nombres de claves del JSON (los que llevan guion bajo): traducilos
   a palabras normales.
5. Nombrá el modelo financiero ("modelo" o "modelos" en el JSON) y decí en
   general qué mide, con tono de análisis de inversionista, pero accesible
   para quien no estudió finanzas. Una comparación cotidiana ayuda si es
   corta.
6. Estructura: tres bloques separados por una línea en blanco, cada uno
   con su encabezado exacto en una línea:
   {S1}
   (qué mide el modelo y qué responde la pregunta)
   {S2}
   (las cifras clave del JSON y qué significa cada una; podés usar viñetas)
   {S3}
   (cómo interpretarlo, con matices y límites del modelo)
   El largo es el que haga falta para que se entienda, sin relleno: no
   repitas la pregunta, ni saludes, ni te despidas.
7. Respondé siempre en español rioplatense, sin mezclar otros idiomas.
```

Sustitución de `{S1..S3}` por `SECTION_LABELS["narrativa"]` al importar. Verificar que ninguna línea sustituida empiece con dígito.

### 3.2 `SYSTEM_PROMPT_PASO_A_PASO`

Igual que el anterior, salvo la regla 1 (agrega la cuenta), la regla 5 y la regla 6:

```
Sos un profesor de finanzas que explica con claridad y calidez, en español
rioplatense, por un chat de Telegram. Vas a recibir un JSON con una pregunta
puntual, los datos ya calculados y una clave "cuenta" con la fórmula YA
RESUELTA paso a paso (números reales, cada término calculado, resultado
final). El usuario ya ve esa cuenta arriba de tu respuesta.

Reglas, sin excepción:
1. La cuenta en "cuenta" YA ESTÁ CALCULADA Y ES CORRECTA: no la recalcules
   ni la repitas entera. Usá ÚNICAMENTE las cifras y datos del JSON; nunca
   inventes, estimes ni calcules cifras nuevas (diferencias, porcentajes,
   promedios, proyecciones, años, fechas). Para comparar usá palabras o
   citá cifras que ya estén en el JSON. Los valores vienen escritos para
   leerse (por ejemplo "$1,234.56", "15.3%", "$2.5 mil millones"): copialos
   exactamente como están, con su símbolo y sus decimales.
2. Nunca des una recomendación de compra o venta ni asesoramiento
   financiero personalizado: solo explicá qué significa el resultado.
3. [idéntica a la regla 3 de EXPLAIN]
4. [idéntica a la regla 4 de EXPLAIN]
5. Nombrá el modelo financiero ("modelo" o "modelos" en el JSON) y decí
   qué mide. Explicá QUÉ ES cada valor clave de la cuenta, uno por uno, y
   no dejes ningún término sin explicar. Usá un lenguaje simple, con una
   comparación cotidiana si ayuda (por ejemplo, pensar en la empresa como
   una tienda de limonada).
6. Estructura: tres bloques separados por una línea en blanco, cada uno
   con su encabezado exacto en una línea:
   {S1}
   (qué mide el modelo, en lenguaje simple)
   {S2}
   (una viñeta "• " por cada término de la cuenta: "Término (cifra): qué es")
   {S3}
   (qué significa el resultado para quien lee, con sus límites)
   El largo es el que haga falta; sin saludo, cierre ni repetir la pregunta.
7. Respondé siempre en español rioplatense, sin mezclar otros idiomas.
```

### 3.3 Por qué cada cambio (para que el implementer no lo reabra)

- Se elimina "2 a 4 oraciones cortas", "sin introducción ni cierre" y "punto decimal, sin separador de miles" (H1, H6).
- Se agrega lo que el guard impone, para que el modelo escriba pensando en él (H5): no cifras nuevas, no siglas ni palabras en mayúsculas fuera del JSON, no numerar, copiar con el símbolo (`$`, `%`). Un `18.4` no coincide con `18.4%` en el guard actual.
- Se prohíbe Markdown (H4) y las claves técnicas (H7).
- La estructura la fijan `SECTION_LABELS` y los tests, no la memoria del prompt.

### 3.4 Siglas disponibles: verificar con payloads reales (R-3)

El guard sólo deja pasar mayúsculas que aparecen en el payload. El implementer debe, con un test parametrizado por cada `question_code`, listar qué siglas contiene cada payload presentado (`modelo`, `modelos`, `cuenta`). Si `DCF`/`WACC`/`ROE`/`EPS` no están en el payload de una pregunta que los necesita, la solución es agregar al payload un campo `"glosario"` de texto fijo (Python) con las siglas permitidas para esa pregunta, o instruir "escribilo en palabras". Decisión por defecto: palabras (prompt regla 3). No se toca el guard.

---

## 4. Reemplazo de `_enforce_brevity` y tope de longitud (decisión (b))

### 4.1 Diseño

- Se eliminan `_enforce_brevity` y `_MAX_EXPLANATION_CHARS` (grep a 0).
- Se agrega `_MAX_RESPUESTA_CHARS = 1800` (tope duro del texto del modelo; valor provisional, se calibra con el peor caso medido) y `_MIN_RESPUESTA_CHARS = 400`.
- Se agrega `_fit_respuesta(texto, max_chars) -> str`, pura:
  1. Si `len(texto) <= max_chars`, devolver tal cual.
  2. Si no, cortar en el último `\n\n` antes de `max_chars`.
  3. Si no hay, cortar en el último fin de oración `[.!?]` seguido de espacio o fin de texto. Esto NO considera el "." de un decimal, porque ahí el "." va seguido de un dígito.
  4. Si no hay, cortar en el último espacio y agregar `…`.
  5. Si el último bloque que queda es solo un encabezado de sección (línea de `SECTION_LABELS` sin cuerpo), quitarlo.
  6. Se loguea `GEMINI_RESPUESTA_RECORTADA chars=N max=M` (INFO, sin contenido) para poder medir con qué frecuencia pasa.
- **Ajuste contra el límite de Telegram, en `_build_leaf_message`:** `restante = TELEGRAM_MESSAGE_LIMIT - 100 - len(mensaje_sin_respuesta)`. La respuesta se ajusta a `min(_MAX_RESPUESTA_CHARS, restante)`. El margen de 100 cubre diferencias de conteo (emojis fuera del BMP): la regla exacta de Telegram para el conteo NO está verificada (R-5).
  - Si `restante < _MIN_RESPUESTA_CHARS`, se omiten bloques opcionales completos (nunca se truncan), en este orden: primero el `📊 Gráfico`, luego el `🔍 Desglose`. Nunca se omiten `Dato`, `Cuenta`, la respuesta ni el disclaimer.
  - Es el mismo criterio que `_enforce_cuenta_length` (omitir un bloque aritmético antes que mutilar un número).
- `_enforce_cuenta_length` y sus 800 chars NO cambian: la `Cuenta` es determinística y el pedido de quitar el límite es sobre el texto del modelo.
- `MAX_EXPLANATION_OUTPUT_TOKENS`: 1024 → **2048**. Razón: 1800 chars de español son ~450-600 tokens, pero los tokens de pensamiento cuentan contra `maxOutputTokens` y un `MAX_TOKENS` cae a fallback (CA-43). Es el mismo valor que ya usa el rewrite. Costo: ninguno si el modelo no lo usa. NO VERIFICADO contra Gemini real cuántos tokens de pensamiento consume `minimal`: el smoke lo mide (`thoughts` ya está en sus contadores).

### 4.2 Impacto en tests (nodeids/archivos) — a modificar

| Archivo | Test | Cambio |
|---|---|---|
| `tests/test_ai_explain.py` ~l.981 | `test_enforce_brevity_texto_corto_se_devuelve_sin_modificar` | renombrar a `test_fit_respuesta_texto_corto_se_devuelve_sin_modificar`, llamar a `_fit_respuesta(t, _MAX_RESPUESTA_CHARS)` |
| ~l.986 | `test_enforce_brevity_texto_largo_con_punto_corta_en_ultimo_punto` | reescribir: corte en `\n\n` u oración; assert `len <= max` y no termina en "…" si había oración |
| ~l.994 | `test_enforce_brevity_texto_largo_sin_punto_corte_duro_con_puntos_suspensivos` | reescribir: corte duro en espacio + "…" (el comportamiento exacto cambió) |
| `tests/test_traceability.py` ~l.294-340 | manifiesto `M_BREV` y `test_enforce_brevity_conservado_sin_perder_aserciones` | actualizar nombres de los 3 tests y mantener la suma de aserciones ≥ 5 (CA-64: el verificador no admite perder aserciones); si el nuevo test de `_fit_respuesta` tiene más aserciones, subir el manifiesto, no bajarlo |
| `tests/test_ai_explain.py` ~l.4020-4035 | `test_build_desglose_vf_ticker_e_peor_caso_montos_extremos_bajo_el_tope` | `"x" * _MAX_EXPLANATION_CHARS` → `"x" * _MAX_RESPUESTA_CHARS`; el assert de `<= TELEGRAM_MESSAGE_LIMIT` ahora lo garantiza el ajuste dinámico |
| ~l.4630-4650 | `test_build_desglose_block_dcf_peor_caso_montos_extremos_bajo_el_tope` | ídem |
| ~l.5145 | `test_cs5_el_filtro_corre_antes_de_enforce_brevity_url_pasada_la_posicion_480` | reescribir: URL pasada la posición `_MAX_RESPUESTA_CHARS` sigue rechazada (el filtro corre sobre el texto completo normalizado, antes de cualquier recorte). El "control positivo" que cita `_enforce_brevity` se reemplaza por `_fit_respuesta` |
| ~l.1646 | `test_system_prompt_explain_pide_texto_plano_con_marcador_y_brevedad` | quitar el assert de `"2 a 4 oraciones"`; reemplazar por los asserts de CA-76 |
| ~l.1652 | `test_system_prompt_explain_regla_5_pide_nombrar_el_modelo` | sin cambios (sigue pasando) |
| ~l.5561 | `test_ca67_prompts_reglas_1_a_6_contiguas_y_contenido[SYSTEM_PROMPT_EXPLAIN / SYSTEM_PROMPT_PASO_A_PASO]` | reglas 1..7; quitar las dos frases de "sin separador de miles / punto decimal"; ver CA-76 |
| ~l.5581 | `test_ca11_tope_de_tokens_de_explicaciones_es_1024` | → `== 2048` (CA-80) |
| ~l.5585 | `test_ca11_fetch_explanation_usa_1024_tokens_timeout_configurado_y_un_reintento` | `maxOutputTokens == 2048` |
| `scripts/gemini_smoke.py` ~l.410 | `row["truncated"]` usa `_MAX_EXPLANATION_CHARS` | → `_MAX_RESPUESTA_CHARS` (contar recortes por tope) |

Sin cambios esperados (verificar que sigan verdes): `test_ca41_system_instruction_es_la_constante_del_modulo[ren-SYSTEM_PROMPT_EXPLAIN / vf-SYSTEM_PROMPT_PASO_A_PASO]` (~l.5328), `test_payload_texto_libre_pregunta_gemini_incluye_modelo` (~l.834) y `test_payload_avanzado_pregunta_gemini_incluye_modelo` (~l.846): la clave `modelo` se conserva.

Hay que correr la suite completa. Puede haber tests que asuman una respuesta ≤ 480 que no encontré con grep.

---

## 5. Números en español sin romper el guard (decisión (c))

### 5.1 Qué hace el guard hoy (verificado en código)

- Tokens: `[+-]?\$?\d[\d.,]*%?`, mayúsculas de 2 a 10 letras, `✅ ❌`, `SÍ`, `NO`.
- `_normalize_numeric_token`: quita `$` y signo; con EXACTAMENTE una coma seguida de 1-2 dígitos la toma como decimal; con 3 dígitos o 2+ comas la toma como miles.
- `_percent_token_a_decimal`: acepta `12%` si el payload trae `0.12`.
- Es un guard de subconjunto sobre cadenas normalizadas, no sobre valores.

### 5.2 Decisión: formato único `$1,234.56` / `15.3%` (convención del resto del bot), presentado ANTES de llamar al modelo

**NO** se cambia el formato a es-AR/es-CO (`1.234,56`) y **NO** se cambia el guard para aceptarlo. Razones:
1. Todo el contenido determinístico (`Dato`, `Cuenta`, `Desglose`, ~cientos de aserciones `"$480.00"`) ya usa `$1,234.56`. Mezclar formatos en el mismo mensaje es peor que cualquiera de los dos.
2. `1.234` es ambiguo sin contexto (1234 en es, 1.234 en en). El caso `1.234,56` ya figura como riesgo conocido (CA-S2). Aceptar ambas representaciones en el guard abre falsos aceptados que ningún heurístico puede distinguir; es relajar la integridad.
3. El problema real de H1/H2 no es el locale, es que el modelo ve floats crudos y reglas contradictorias. Eso se arregla presentando el número.

### 5.3 `_present_payload(datos) -> dict` (nueva, pura)

Se aplica a `datos_del_contexto` después de agregar `cuenta` y antes de `json.dumps` para el modelo. Reglas por hoja numérica (ni `bool` ni `str` ni `None` se tocan):

1. **Clasificación por clave, NO por heurística.** Se construye una tabla `_PAYLOAD_KIND: dict[str, "money"|"pct"|"ratio"]` extrayendo qué claves ya pasan por `_fmt_money_or_nd` / `_fmt_pct_or_nd` / `_fmt_ratio_or_nd` en `_dato_texto_libre`/`_dato_avanzado`. Así la unidad queda probada por el código que ya la muestra al usuario. El implementer la construye leyendo esos dos dispatchers (yo no pude leer los ~27 casos completos).
2. `money` → `_money_compact`: `≥ 1e9` → `"$X.X mil millones"`; `≥ 1e6` → `"$X.X millones"`; si no, `_money(x)`. Negativos con el signo antes del `$` (`-$5.00`). El token `5.00` normaliza igual en el payload y en la respuesta.
3. `pct` (proporciones crudas) → `_pct1(x)` = `"15.3%"`.
4. `ratio` → `f"{x:.2f}"` como string.
5. Hoja numérica sin clasificar → se deja numérica, pero `round(x, 4)` (nunca `496.33333333333337`). Esto mantiene la semántica del guard de hoy.
6. `NaN`/`inf` → `None`. Floats muy chicos (que `json.dumps` emite como `1e-05`) se redondean a 4 decimales para que no aparezcan tokens como `05`.
7. Enteros (`total_modelos`, conteos) no se tocan.

### 5.4 `datos_tokens` — monótono (CS-25)

```
datos_tokens = tokens(json.dumps(payload_presentado)) ∪ tokens(json.dumps(payload_crudo_redondeado_como_hoy))
```

Garantiza que nada que hoy pase deje de pasar (no hay regresión de falsos rechazos) y que el modelo también pueda escribir `15.3%` cuando el payload crudo traía `0.153`. NO agrega equivalencias nuevas al guard. Los tokens vienen únicamente de datos que el bot mismo calculó.

### 5.5 Campo derivado calculado en Python: `diferencia_vs_precio`

Para `vf` y `ver`: `(valor_justo_total / precio_actual - 1)` presentado como `"+15.3%"` / `"-8.2%"`. Es lo que el modelo naturalmente quiere decir ("está 15.3% por encima") y hoy lo obliga a calcular, lo que el guard rechaza. `None` si `precio_actual` es `None` o `≤ 0`, o el valor justo es `None`. Es un campo solo numérico: no agrega superficie de PII (CS-26). Se extiende a otras preguntas solo con un caso de uso concreto (YAGNI).

### 5.6 Casos de borde

| Caso | Comportamiento esperado |
|---|---|
| Respuesta con `$1,234.56`, payload con `$1,234.56` | aceptada |
| Respuesta con `1234.56` (crudo), payload presentado `$1,234.56` y crudo `1234.56` | aceptada (unión) |
| Respuesta con `$1.234,56` | **rechazada** → `EXPLAIN_UNAVAILABLE_MSG`. Limitación documentada y fijada por test (CA-79), no se “arregla” |
| Respuesta con `0,125` (decimal es de 3 cifras) | rechazada (se interpreta como miles); aceptable, cae a fallback |
| Respuesta con `15.3` sin `%`, payload `15.3%` | rechazada; el prompt pide copiar el símbolo |
| Respuesta con cifra nueva `18` | rechazada (se mantiene) |
| Monto `394328000000.0` | presentado `"$394.3 mil millones"`; el token `394.3` queda en `datos_tokens` |
| Monto negativo | `-$5.00`; el signo normaliza igual |
| EPS ≈ 0 o precio ≤ 0 | `diferencia_vs_precio = None`; el modelo no recibe el campo |
| `1.5e-05` | redondeado a 4 decimales |
| Año ("2025") u ordinal ("1.") escrito por el modelo | rechazado (se mantiene); el prompt lo prohíbe |
| "mil millones" | español (1e9). **Nunca** usar "billón" (ambiguo: 1e12 en español, 1e9 en inglés) |

NO VERIFICADO contra Gemini real: la tasa con que el modelo copia strings formateados sin alterarlos. Se mide en el smoke (`guard_numeros` en las razones de rechazo).

---

## 6. Gráficas (decisión (e))

### 6.1 Comparación

| Opción | Dependencias | Peso | Seguridad/Privacidad | Tests offline | Calidad visual |
|---|---|---|---|---|---|
| **1. Barras Unicode en el texto** (`█░`) | ninguna | 0 | Todo local. Sin `send_photo`, sin FS, sin red. Sin `parse_mode` (no hay monoespaciado) | Triviales (string golden) | Aceptable: barras proporcionales por línea. Sin alineación perfecta de columnas (fuente proporcional en Telegram) |
| **2a. matplotlib** (+ numpy, pillow, kiwisolver, fonttools, contourpy, cycler, pyparsing, dateutil) | ~8 paquetes | Estimado +80-120 MB de imagen (no medido) | Renderizado local. Requiere `MPLCONFIGDIR` escribible por usuario no-root (hoy solo `/data`); backend `Agg`; `pyplot` no es thread-safe → usar `Figure` OO en `asyncio.to_thread`. Primera ejecución genera caché de fuentes (lento) | Posibles (bytes PNG, firma PNG, tamaño), pero más frágiles (versiones de fuentes/render) | Mejor |
| **2b. Pillow solo** (dibujar rectángulos y texto) | 1 paquete (+ wheels con freetype) | Estimado +10-15 MB (no medido) | Local. Sin FS (`BytesIO`). Superficie de CVE conocida de decoders de imagen (se usa solo para ESCRIBIR PNG, no para decodificar entrada de terceros) | Buenos (PNG determinístico para entradas dadas, comparación de píxeles/tamaño) | Bueno: barras y marcas, fuente por defecto |
| **3. QuickChart (servicio externo)** | `httpx` ya presente | 0 | **Envía a un tercero** ticker, cifras y la IP del VPS más el momento de la consulta (qué ticker consulta el usuario). Dependencia de disponibilidad y de rate limits de un servicio gratuito. Contradice el principio de mínimo dato a terceros de la spec base 3.12 (hoy sólo Google). La URL con los datos viaja en GET y queda en logs de terceros | Mockeables, pero no prueban nada del render real | Bueno |

Los pesos de imagen son estimaciones mías de orden de magnitud, NO medidas. El implementer mide `docker image ls` antes y después si la Fase 3 se aprueba.

### 6.2 Recomendación

- **Fase 2 (en este patch, después de que la Fase 1 esté medida): opción 1.** Barras Unicode generadas por Python. Razones: cero dependencias (cumple "no agregues dependencias sin justificar"), cero superficie nueva, las explicaciones siguen sin `parse_mode` y sin nuevo tipo de mensaje, testeable con strings. Es lo que mejor se adapta a datos que son pocas cifras comparables (3 a 5 barras).
- **Fase 3 (spec aparte, SOLO si la usuaria, tras ver las barras, sigue queriendo imágenes): Pillow (2b)**, no matplotlib. Justificación escrita: para barras horizontales simples, matplotlib agrega ~10x el peso y varias dependencias transitivas (numpy incluido) sin ganar nada que se vea en un chat de Telegram. Se enviaría con `bot.send_photo(photo=BytesIO)` como mensaje separado (no se puede convertir `edit_message_text` en foto), con `caption` en texto plano ≤ 1024 chars. Requisitos mínimos de esa spec, ya listados para no perder la decisión: versión pinneada y verificada con `pip-audit`, sin escribir al FS, tope de tamaño del PNG, mismo balde del rate limiter, y que el `Dockerfile` no cambie salvo un `apt-get` si hiciera falta (no esperado con wheels de `slim`).
- **QuickChart: descartado** por privacidad. Self-host de QuickChart queda descartado por costo operativo (otro servicio Node en el VPS). Esa decisión se reabre solo si se acepta enviar datos a un tercero con consentimiento explícito de la usuaria.

### 6.3 Qué graficar con datos ya almacenados (sin llamar a FMP)

Fuente: sólo `ExplanationContext` obtenido por `_get_owned_context` (IDOR por `chat_id` intacto). Los gráficos se generan en Python desde los mismos valores que arma `_build_explain_payload`, no desde el modelo. Reutilizan `_money`, así que el número impreso es el mismo que ya ve el usuario.

| Pregunta | Datos | Gráfico (ejemplo) |
|---|---|---|
| `vf` | `valor_justo_multiplos`, `_graham`, `_dcf`, `_total`, `precio_actual` | barras de 4 modelos más precio |
| `gra`, `dcf`, `mul` | `pesimista`, `conservador`, `optimista` del modelo vs `precio_actual` | barras de 3 escenarios más precio |
| `ver` | `valor_justo_total` vs `precio_actual` | 2 barras |
| `pil` | pilares cumplidos (n de total) | `████░░ 3 de 4` |
| `pig` (Piotroski) | puntaje sobre 9 | barra n/9 |
| `alz` (Altman) | puntaje vs zonas (umbrales ya presentes en el código; el implementer los toma de ahí) | barra con zonas |
| Peers (`mul`, `rat`) | `per` vs `per_promedio_peers` | 2 barras. Usar SOLO los campos que ya pasan por la validación existente de `peer_comparison`; **no verifiqué `validated_peers`**, el implementer lo confirma antes de tocar cualquier dato de peers |
| Riesgo | `beta` vs `beta_umbral_bajo`/`beta_umbral_alto` y `perfil` validado | barra con dos marcas |

Mínimo viable de Fase 2: `vf`, `gra/dcf/mul` y `ver` (los más usados y con datos ya probados); el resto en una segunda pasada.

Ejemplo (Fase 2, `vf`; ancho de barra 12; valores con `_money`):

```
📊 Valor justo vs precio
Múltiplos      ████████████  $500.00
Graham         ███████████░  $480.00
Flujo (DCF)    ████████████  $510.00
Valor Justo    ███████████░  $496.00
Precio actual  ██████████░░  $430.00
```

Reglas de la barra (`_bar(valor, maximo, ancho=12)`):
- longitud = `round(valor / maximo * ancho)`, mínimo 1 bloque si `valor > 0`.
- `valor ≤ 0` o `None` → línea `n/d` sin barra.
- Todos iguales → barras llenas.
- Escala común al máximo del conjunto.
- Tope del bloque: `_MAX_GRAFICO_CHARS = 400`.
- Si faltan datos para dibujar al menos 2 barras → `None` (no se inserta el bloque).
- Inserción: después de `Cuenta`, antes de `Desglose`. NUNCA entra al `datos_del_contexto` ni a `datos_tokens`: se arma después de la respuesta del modelo, igual que el `Desglose`.

Limitación declarada: la fuente de Telegram es proporcional, así que las barras no se alinean en columnas. Se acepta; el bloque monoespaciado exigiría `parse_mode` (HTML `<pre>`) y se descarta porque rompe la invariante CA-46.

---

## 7. Rewrite (`ai_rewrite.py`) y thinking / maxOutputTokens (decisión (d))

### 7.1 Rewrite del primer mensaje: NO cambia en este patch

- El síntoma reportado es en los botones "paso a paso"/explicaciones, no en el primer mensaje.
- El rewrite usa JSON mode, placeholders `⟦PHn⟧` y un guard distinto (`_is_safe_rewrite` más balance de Markdown); no ve los números crudos. Por eso H1/H2/H5 no lo afectan.
- La regla 7 "condensar" va en dirección contraria a "bien espaciada", pero el primer mensaje no es lo que se objetó. Tocarlo reabre `CA-36` (tests de la regla 7) y el riesgo de parse del Markdown.
- **Pregunta de producto Q5** (abajo): si la usuaria también encuentra mal redactado el primer mensaje, se abre un patch aparte con medición propia. Default: no tocar.
- CA-82: `SYSTEM_PROMPT` de `ai_rewrite.py` sin cambios (el diff de ese bloque debe ser vacío).

### 7.2 `GEMINI_THINKING_LEVEL` / `maxOutputTokens`

- `maxOutputTokens` de explicaciones: 2048 (sección 4.1).
- `GEMINI_THINKING_LEVEL` es una variable ÚNICA, que aplica a explicaciones Y a rewrite (`gemini_client.load_config`). Subir `minimal` → `low` mejora (hipótesis, NO VERIFICADO) la adherencia a las restricciones del prompt, pero suma latencia al rewrite, que tiene timeout propio de 8 s sin reintento.
- **Decisión:** no se cambia el default en el código. El smoke se corre dos veces con `GEMINI_LIVE_*` (una con `minimal`, otra con `low`) sobre las mismas muestras, con el mismo protocolo de la sección 9. Se cambia el valor en el `.env` del VPS SOLO si `low` mejora el cumplimiento medible Y la latencia p95 del rewrite sigue < timeout. Si ayuda solo a las explicaciones y perjudica al rewrite, se abre una variable separada en otro patch (YAGNI hoy).
- Esto NO verifica que `thinkingLevel: low` sea aceptado por el modelo exacto de producción: el smoke lo confirma o no.

---

## 8. Criterios de aceptación

Numeración: CA-70 en adelante; CS-23 en adelante; smoke CA-S7 en adelante (la spec base llega a CA-S6). "(AUTO)" = test automático offline; "(MANUAL-LOCAL)" = smoke con key de pruebas.

### Longitud y recorte
- [ ] CA-70 (AUTO) `grep` en `src/`, `scripts/` y `tests/`: 0 apariciones de `_enforce_brevity` y `_MAX_EXPLANATION_CHARS`; existen `_MAX_RESPUESTA_CHARS` (valor 1800), `_MIN_RESPUESTA_CHARS` (400) y `_fit_respuesta`.
- [ ] CA-71 (AUTO) Una respuesta de 1200 caracteres con puntos y decimales (p.ej. `$285.75`) que pasa los guards sale de `_fetch_explanation` idéntica (tras normalizar); ya no se corta a 480.
- [ ] CA-72 (AUTO) Propiedad sobre `_fit_respuesta`: para un texto con decimales y para TODO `max_chars` entre 50 y `len(texto)`, el resultado cumple: `len ≤ max_chars` (+1 si termina en "…"), y los `protected_tokens` del resultado son un prefijo (multiconjunto) de los del original. Es decir, jamás hay un número cortado. Con control positivo: el `_enforce_brevity` anterior incumple esta propiedad sobre un texto de prueba (documentado en el test).
- [ ] CA-73 (AUTO) `_build_leaf_message` con la respuesta de `_MAX_RESPUESTA_CHARS` más `Cuenta` máxima, `Desglose` máximo y `Gráfico` máximo mide ≤ 4096. Cuando `restante < 400` se omiten primero el `Gráfico` y luego el `Desglose`; `Dato`, `Cuenta`, la respuesta y el disclaimer nunca se omiten.
- [ ] CA-80 (AUTO) `MAX_EXPLANATION_OUTPUT_TOKENS == 2048` y `generationConfig.maxOutputTokens == 2048` en la request de explicación (reemplaza a CA-11 en los dos tests de 1024); el rewrite sigue en 2048.

### Template y normalizador
- [ ] CA-74 (AUTO) `_normalize_layout`: tabla de casos (`**a**`, `__b__`, `# T`, `* x`, `- y`, `` `c` ``, 4 saltos seguidos, `\r\n`, etiqueta sin línea en blanco previa) con salida esperada exacta. Idempotente: `f(f(x)) == f(x)`. `protected_tokens(salida) == protected_tokens(entrada)` (nunca agrega ni quita números).
- [ ] CA-75 (AUTO) El texto que se chequea con ambos guards es el texto normalizado, y es EXACTAMENTE el que llega a `_build_leaf_message` (test con spy).
- [ ] CA-76 (AUTO) Ambos prompts: reglas `1..7` contiguas (regex `(?m)^(\d+)\. `); contienen `modelo`, `español rioplatense`, `Markdown`, `MAYÚSCULAS` y las 3 etiquetas de `SECTION_LABELS` correspondientes; el de paso a paso contiene `cuenta` y `YA ESTÁ CALCULADA`; NO contienen `2 a 4 oraciones`, `cortas`, `separador de miles`, `punto decimal`, `{"respuesta"`, `###RESPUESTA###` ni `portugués`. `systemInstruction == constante` (CA-41, sin interpolación en runtime).
- [ ] CA-85 (AUTO) Golden: con una respuesta del modelo de ejemplo (la del Ejemplo A) y un contexto sintético fijo, el mensaje de `vf` paso a paso es igual al texto golden del test (orden: transparencia → Dato → Cuenta → [Gráfico] → Desglose → respuesta → Fórmula/Fuente → disclaimer; una línea en blanco entre bloques). Ídem para `ren`.

### Números y payload
- [ ] CA-77 (AUTO) Para cada `question_code` de ambas tablas con contexto sintético: ninguna hoja float del payload presentado tiene más de 2 decimales en su forma de string ni aparece en notación científica; `datos_tokens ⊇ tokens(payload crudo como hoy)` (monotonía: nada que hoy pase deja de pasar). Cada clave de `_PAYLOAD_KIND` aparece en un dispatcher de `Dato` (la unidad sale del código existente).
- [ ] CA-78 (AUTO) `diferencia_vs_precio`: `(496.00, 430.00) → "+15.3%"`; precio `None`, `0` o negativo → `None`; valor justo `None` → `None`; el campo no existe en preguntas que no son `vf`/`ver`.
- [ ] CA-79 (AUTO) Regresión de formato: respuesta con `$1,234.56` aceptada; con `1234.56` aceptada; con `$1.234,56` **rechazada** (`_ExplainUnavailable`, limitación documentada); con `15.3` cuando el payload trae `15.3%` rechazada; con una cifra nueva rechazada.
- [ ] CA-81 (AUTO) El campo `cuenta` y los bloques `Dato`/`Cuenta`/`Desglose` no cambian de formato (los tests con `"$480.00"` pasan sin modificar).

### Rewrite y configuración
- [ ] CA-82 (AUTO) `ai_rewrite.SYSTEM_PROMPT` sin cambios (los tests de CA-36 pasan sin modificar; diff vacío en ese bloque).
- [ ] CA-83 (AUTO) La matriz de fallas CA-43 (400, 401, 402, 403, 404, 429, 500, 503, timeout, ConnectError, JSON inválido, `candidates` vacío, `blockReason`, `MAX_TOKENS`, rechazo del guard) pasa sin cambios. `EXPLAIN_UNAVAILABLE_MSG` sigue siendo el único mensaje de fallo.
- [ ] CA-84 (AUTO) Ninguna llamada a `send_message`/`edit_message_text` del flujo de explicación lleva `parse_mode` (CA-46), incluido el bloque `Gráfico`.

### Gráfica Unicode (Fase 2)
- [ ] CA-86 (AUTO) Golden de `_build_grafico_vf` con valores sintéticos. Solo contiene `█`, `░`, texto fijo y montos de `_money`; mide ≤ `_MAX_GRAFICO_CHARS`; longitudes de barra proporcionales (`500` vs `430` con ancho 12 → 12 y 10).
- [ ] CA-87 (AUTO) Datos insuficientes (menos de 2 valores válidos) → `None`; el mensaje final es idéntico al de una versión sin gráfico.
- [ ] CA-88 (AUTO) Bordes: valor negativo o cero → `n/d`; todos iguales; relación 1000:1 (mínimo 1 bloque); un solo valor; `None` mezclado; sin llamadas HTTP (spy: 0) y sin acceso a `ExplanationContext` ajeno.

### Smoke (MANUAL-LOCAL)
- [ ] CA-S7 Muestra de N ≥ 30 salidas (6 `question_code`: `vf`, `gra`, `dcf`, `ren`, `ver`, `alz` × 5 repeticiones), con la config de producción (`minimal`) y de nuevo con `low`. Reportar por código: aceptación (1 menos tasa de rechazo del guard), rechazos por `guard_numeros`/`filtro_salida`/`MAX_TOKENS`, longitud p50/p95, cantidad de recortes por tope, conformidad con las 3 etiquetas, restos de Markdown ANTES del normalizador (para medir H4), fugas de claves `snake_case` (para medir H7), tokens de pensamiento. **Umbrales:** aceptación ≥ 90% y no peor que la línea base medida con los prompts actuales en la misma corrida; 0 `MAX_TOKENS`; conformidad con las etiquetas ≥ 90%; longitud p50 entre 500 y 1500; 0 restos de Markdown tras el normalizador.
- [ ] CA-S8 Rúbrica humana ciega: la usuaria califica 10 salidas (5 con los prompts actuales, 5 con los nuevos, orden aleatorio, sin saber cuál es cuál) en claridad, naturalidad, espaciado y utilidad (escala 1 a 5). Aceptación: promedio de los prompts nuevos ≥ 4 en cada criterio y ≥ 1 punto sobre los actuales. Si falla: Iter-3 con las muestras como evidencia. Si falla de nuevo: STOP y escalar a decisión humana.

### Seguridad (CS-23 a CS-30, a ampliar por `security`)
- [ ] CS-23 (AUTO) El tope es duro sobre el texto del modelo: ningún camino devuelve más de `_MAX_RESPUESTA_CHARS` ni deja el mensaje total sobre 4096. El log de recorte no contiene el texto del modelo (solo contadores).
- [ ] CS-24 (AUTO) Normalizar antes de los guards: una respuesta con `](`, una URL, una `@mención` o un carácter invisible/bidi sigue rechazada aun si el normalizador la "limpia" (el normalizador NO elimina esos caracteres; solo Markdown inofensivo). Una URL pasada la posición `_MAX_RESPUESTA_CHARS` también se rechaza (reemplaza a CS-5 en su prueba de 480).
- [ ] CS-25 (AUTO) Los guards no se debilitan: `_no_new_protected_tokens`, `_normalize_numeric_token` y `_percent_token_a_decimal` sin cambios (diff vacío salvo el cálculo de `datos_tokens`, que es la unión de la sección 5.4). Para cada `question_code`, una respuesta con una cifra inventada es rechazada. Mutación de control (CQ): quitar la llamada al guard debe romper un test.
- [ ] CS-26 (AUTO) Superficie al modelo: los canarios de CA-40 (`chat_id`, `company_name`, `ticker`, `industry`, `sector`) siguen ausentes de la request para TODOS los `question_code` con los payloads presentados; los campos nuevos (`diferencia_vs_precio`) son solo numéricos.
- [ ] CS-27 (AUTO) El gráfico y las cifras derivadas se calculan 100% en Python, sin red y sin pasar por el modelo; `requirements.txt` y `Dockerfile` con diff vacío en las Fases 1 y 2.
- [ ] CS-28 (AUTO + revisión) Si se agrega `--show-text` al smoke: apagado por defecto; solo con contextos sintéticos con canarios; imprime el texto del modelo con control y bidi eliminados, con tope de líneas; no escribe archivos; las guardas existentes (key distinta de la de producción, no CI, no contenedor, etc.) no se aflojan.
- [ ] CS-29 (AUTO) IDOR y validaciones intactas: los tests de `_get_owned_context`, `_validated_perfil`, `_validated_sector` y la allowlist (CA-42) pasan sin cambios. El gráfico lee solo del contexto ya validado; cualquier dato de peers pasa por la misma validación que el payload.
- [ ] CS-30 (AUTO) Sin llamadas extra a Gemini: 1 request por botón; sin reintento por mala estructura o mala calidad (el único reintento sigue siendo el de 500/503 del cliente). Test: contador de llamadas == 1 con una respuesta de estructura incorrecta pero válida.

---

## 9. Cómo medir la calidad de redacción (sin ejemplo real todavía)

**Fase 0 (antes de implementar nada).**
1. La usuaria pega 2 o 3 respuestas reales "malas" (con el botón y el ticker). Es la forma de confirmar H1-H7; si muestran algo que no está en la tabla, el implementer vuelve a mí antes de codificar.
2. Línea base con el código actual: correr `scripts/gemini_smoke.py --explain-reps 5` con los 6 códigos de CA-S7. Hoy el script **no imprime el texto del modelo** (decisión de seguridad de la spec base). Para ver el texto hace falta el flag opcional `--show-text` de CS-28, que `security` debe aprobar. Sin él, el baseline sólo da métricas (aceptación, longitud, `finish`), no calidad percibida.
3. Dejar guardado el resultado del baseline para la comparación de CA-S7/S8.

**Después de la Fase 1.** Mismo protocolo, y luego la rúbrica ciega CA-S8 de la usuaria. Las métricas automáticas detectan regresiones (rechazos, Markdown, longitud); la rúbrica decide si "se lee bien", que es lo único que importa de verdad.

**Qué NO está verificado contra Gemini real (marcado explícitamente):**
- Que el modelo cumpla de forma estable la estructura de 3 bloques con `minimal` (por eso se mide, y por eso no se rechaza por estructura).
- Que el modelo copie fielmente los strings formateados (`$1,234.56`, `15.3%`) sin cambiar separadores.
- Cuántos tokens de pensamiento consume `minimal` y si `low` es aceptado y mejora.
- Que Gemini emita Markdown (H4) y claves `snake_case` (H7): son suposiciones sobre su comportamiento típico.
- El conteo exacto de caracteres de Telegram con emojis (por eso el margen de 100).
- Cuántas siglas permite hoy cada payload (R-3): depende del código de los ~27 payloads que no leí completos.

---

## 10. Artefactos a crear/modificar

| Archivo | Cambio |
|---|---|
| `src/investbot/ai_explain.py` | Reemplazar `SYSTEM_PROMPT_EXPLAIN`/`_PASO_A_PASO` (3.1, 3.2); agregar `SECTION_LABELS`; borrar `_enforce_brevity`/`_MAX_EXPLANATION_CHARS`; agregar `_MAX_RESPUESTA_CHARS`, `_MIN_RESPUESTA_CHARS`, `_fit_respuesta`, `_normalize_layout`; `MAX_EXPLANATION_OUTPUT_TOKENS = 2048`; agregar `_PAYLOAD_KIND`, `_money_compact`, `_present_payload`; campo `diferencia_vs_precio` en payloads `vf`/`ver`; cambiar el cálculo de `datos_tokens` a la unión (5.4); orden en `_fetch_explanation` (2.4); `_build_leaf_message` con ajuste dinámico (4.1) y parámetro opcional `grafico` (Fase 2); `_build_grafico_*` y `_bar` (Fase 2) |
| `scripts/gemini_smoke.py` | usar `_MAX_RESPUESTA_CHARS`; nuevas métricas (CA-S7: etiquetas, Markdown previo al normalizador, claves `snake_case`, recortes); flag opcional `--show-text` (CS-28); `thinking` seleccionable por argumento para la comparación |
| `tests/test_ai_explain.py` | modificar los tests de la sección 4.2; crear tests de CA-71 a CA-88 y CS-23 a CS-30 |
| `tests/test_traceability.py` | actualizar `M_BREV` y su test (sección 4.2) |
| `.env.example`, `README` | solo si cambia algo de operación (no se esperan variables nuevas) |
| `requirements.txt`, `Dockerfile` | **sin cambios** en las Fases 1 y 2 (CS-27) |
| `src/investbot/ai_rewrite.py` | **sin cambios** |

Delta de tests esperado: ~35 nuevos, ~12 modificados. El implementer reporta N0 (suite verde actual) y N1 con números reales.

---

## 11. Restricciones / fuera de alcance

- No se toca `ai_rewrite.py`, `query_handler.py`, el guard de balance de Markdown del primer mensaje ni el flujo del rewrite.
- No se activa `parse_mode` en ninguna explicación.
- No se agregan dependencias en las Fases 1 y 2. Pillow (Fase 3) solo con spec aparte y aprobación explícita.
- No se envían datos a terceros distintos de Google.
- No se cambia la convención numérica del bot (`$1,234.56`) ni el guard para es-AR/es-CO.
- Sin reintentos por calidad; sin segunda llamada a Gemini para "arreglar" la redacción.
- Sin commits ni push sin autorización explícita de la usuaria.

---

## 12. Plan de fases

| Fase | Contenido | Puerta para avanzar |
|---|---|---|
| **0** Diagnóstico | Ejemplos reales de la usuaria; `--show-text` (si `security` lo aprueba); línea base con los prompts actuales | Hipótesis confirmadas o corregidas |
| **1** Núcleo de redacción | Prompts, `SECTION_LABELS`, `_normalize_layout`, `_fit_respuesta` + ajuste dinámico, `_present_payload`, `diferencia_vs_precio`, tokens a 2048, tests | Suite verde (N1 reportado) y CA-S7 |
| **1b** Medición | CA-S7 con `minimal` y con `low`; rúbrica CA-S8 | Aceptación de la usuaria |
| **2** Barras Unicode | `_build_grafico_vf`, `gra/dcf/mul`, `ver` | CA-86 a CA-88; revisión visual de la usuaria |
| **3** (spec aparte, condicional) | Imágenes con Pillow | Decisión de la usuaria tras ver la Fase 2 |

Despliegue recomendado: Fase 1 sola primero (para aislar qué cambió la calidad), después Fase 2.

---

## 13. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R-1 | El modelo (`minimal`) no sigue la estructura | No se rechaza por estructura; el normalizador garantiza espaciado; se mide en CA-S7 |
| R-2 | Más largo ⇒ más cifras ⇒ más chances de que el guard rechace una cifra inventada ⇒ más fallback | `diferencia_vs_precio` y el prompt que explica el guard; la tasa se mide; el fallback sigue siendo el mismo mensaje |
| R-3 | Siglas no disponibles en algunos payloads | Test que lista siglas por pregunta; default "escribilo en palabras" (3.4) |
| R-4 | `_PAYLOAD_KIND` con unidad equivocada ⇒ el modelo recibe `15.3%` donde era una razón | La tabla sale de los dispatchers de `Dato` (ya muestran al usuario esa unidad); CA-77; clave sin clasificar queda numérica y redondeada |
| R-5 | Conteo de Telegram distinto al supuesto | Margen de 100; ajuste dinámico; el `edit_message_text` ya tiene fallback `EXPLAIN_DELIVERY_FAILED_MSG` |
| R-6 | Respuesta más larga ⇒ más tokens ⇒ más costo/latencia | Tope 1800 chars y 2048 tokens; se mide la latencia p95 en el smoke |
| R-7 | Pasar de `minimal` a `low` perjudica al rewrite (timeout 8 s) | Decisión basada en medición; variable separada si hace falta (otro patch) |
| R-8 | El ejemplo del prompt (si se agregara) contamina las respuestas | No se incluyen ejemplos con cifras en el prompt; los ejemplos de esta spec son solo para los tests golden |

---

## 14. Pasos manuales en el VPS (para la usuaria, tras la autorización explícita de despliegue)

1. Verificar que el working tree esté commiteado (lo decide la usuaria; yo no commitea ni pushea).
2. En el VPS: reconstruir la imagen y reiniciar el servicio con el compose de producción (`docker-compose.prod.yml`, según el `Dockerfile` existente; nombres exactos a confirmar en el VPS).
3. Variables de entorno: **ninguna nueva**. `GEMINI_THINKING_LEVEL` solo se cambia en el `.env` si CA-S7 lo justifica.
4. Sin migración de datos. Fase 3 (si ocurre) sí cambia `requirements.txt` e imagen.
5. Rollback: volver a la imagen anterior (conservar el tag previo antes de reconstruir).
6. Prueba manual post-despliegue: pedir `vf` en un ticker real y verificar estructura, espaciado, ausencia de asteriscos y mensaje < 4096.

---

## 15. Preguntas de producto para la usuaria (con default recomendado)

| # | Pregunta | Default recomendado |
|---|---|---|
| Q1 | Formato de números: ¿`$1,234.56` (el del resto del bot) o `$1.234,56`? | Mantener `$1,234.56`. Cambiarlo toca todo el bot y el guard (sección 5.2) |
| Q2 | Largo deseado de la explicación | ~600 a 1200 caracteres, tope 1800 |
| Q3 | ¿OK con 3 secciones con emoji de encabezado, sin negrita? | Sí |
| Q4 | Gráficas: ¿barras de texto primero, imágenes solo si las barras no alcanzan? | Sí (Fase 2 ahora, Fase 3 condicional) |
| Q5 | ¿También te parece mal redactado el primer mensaje del análisis, o solo los botones? | Solo los botones; no tocar el rewrite |
| Q6 | ¿Podés pegar 2 o 3 respuestas reales "malas" antes de implementar? | Sí (Fase 0) |
| Q7 | ¿Se aprueba `--show-text` en el smoke (solo datos sintéticos) para ver el texto del modelo? | Sí, apagado por defecto (CS-28) |

---

## Handoff → security / implementer

### Specs producidas
- Este Spec Patch [Iter-2] de redacción (`SDD_redaccion_gemini_iter2.md`).

### Criterios de aceptación base
- CA-70 a CA-88 (sin CA-85 de Fase 2: CA-86 a CA-88 son de Fase 2; CA-85 es golden de Fase 1), CA-S7 y CA-S8, CS-23 a CS-30 (secciones 8).
- `security` debe revisar en particular: CS-24 (normalizador antes del guard), CS-25 (unión de `datos_tokens` sin debilitar), CS-28 (`--show-text`) y la superficie nueva `diferencia_vs_precio`.

### Decisiones de diseño tomadas
1. Estructura fija de 3 bloques con encabezado emoji, texto plano, sin Markdown, sin numeración con dígitos (el guard rechazaría los dígitos). Python arma el marco; el modelo sólo escribe el bloque central.
2. Sin rechazo por estructura; el normalizador arregla el espaciado y la limpieza de Markdown ANTES de los guards.
3. Se elimina `_enforce_brevity`; tope alto `_MAX_RESPUESTA_CHARS = 1800` más ajuste dinámico a 4096 en `_build_leaf_message`, con corte en párrafo u oración y jamás dentro de un número.
4. Los números se presentan al modelo en el formato del resto del bot (`$1,234.56`, `15.3%`, `$X mil millones`) mediante `_present_payload`, con unidades tomadas de los dispatchers de `Dato`. El guard no cambia; `datos_tokens` es la unión del payload presentado y el crudo. Es-AR/es-CO queda rechazado y fijado por test.
5. Campo derivado `diferencia_vs_precio` calculado en Python (`vf`, `ver`).
6. `maxOutputTokens` 2048; `GEMINI_THINKING_LEVEL` sin cambiar por defecto, decidido por medición.
7. El rewrite no se toca.
8. Gráficas: barras Unicode en Python en la Fase 2 (sin dependencias, sin `parse_mode`). Imágenes con Pillow, no matplotlib, solo con spec aparte. QuickChart descartado por privacidad.
9. No hay reintentos ni segunda llamada a Gemini por calidad.

Archivos de contexto relevantes (rutas absolutas):
- `/Users/danielavergara/Documents/Personal/InvestBot/src/investbot/ai_explain.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/src/investbot/ai_rewrite.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/src/investbot/gemini_client.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/scripts/gemini_smoke.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/tests/test_ai_explain.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/tests/test_traceability.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/contexto/specs/abiertas/SDD_migracion_gemini.md`
---

# Sección 16 — Security (Iter-2)  [PREVALECE sobre las secciones 1–15 de este patch donde contradiga]

Rol `security`, 2026-11-12. Veredicto: APROBADO CON 2 BLOQUEANTES (B-1, B-2) corregidos en esta sección. `--show-text` APROBADO con condiciones C1–C9. `gemini_client.violates_output_filter`, `ai_rewrite.py`, `requirements.txt` y `Dockerfile`: diff VACÍO en Iter-2.

## 16.1 Evidencia medida (offline)
- E1: el normalizador CREA coincidencias del filtro (``ht`tps://evil.com`` pasa el filtro crudo; tras normalizar se rechaza) → los guards corren SIEMPRE sobre el texto normalizado que se entrega.
- E2: `protected_tokens(salida) == protected_tokens(entrada)` es FALSO por construcción (`AB__CD` → `ABCD`): CA-74 en esa parte es insatisfacible.
- E3: el algoritmo de 2.4 NO es idempotente (186/20 000 entradas aleatorias).
- E4: `^\s*[*\-–]\s+` con `(?m)` es cuadrático (1.55 s con 20 000 `\n`, 6.37 s con 40 000) y se come líneas en blanco → DoS del event loop y pérdida del espaciado.
- E5: con `[ \t]*`/`[ \t]+` y bucle a punto fijo (máx. 3 pasadas): 0 no idempotentes en 60 000 casos, 40 000 `\n` en 0.004 s.
- E6/E8: `_normalize_numeric_token` descarta el signo (`+15.3%` ≡ `-15.3%`); el guard no verifica dirección.
- E7: con payload `"$394.3 mil millones"`, `$394.3 millones` PASA el guard (escala no verificada).
- E9: pasan el filtro de salida U+2028, U+0085, U+061C, tag chars U+E00xx, `evil.com`, `bit.ly/xx` (brecha preexistente → BL-1).
- E10: el smoke fija `investbot` en WARNING sin handler → `logging.lastResort` imprime a stderr el `GEMINI_GUARD_REJECT ... respuesta=<500 chars>`.
- E11: los strings que llegan al modelo salen de enums/constantes o están validados; `company_name`, `ticker`, `industry` no están en ningún `_payload_*`; el usuario no manda texto libre (callbacks solo llevan `question_code`).

## 16.2 Criterios de seguridad finales (reemplazan CS-23..CS-30; "AUTO" = test offline)
- [ ] **CS-23 (AUTO) Longitud y entrega.** (a) `len(result.text) > _MAX_RAW_CHARS` (6000) → `_ExplainUnavailable` ANTES de cualquier regex; log `GEMINI_GUARD_REJECT feature=explain guard=largo chars=N` sin contenido. (b) Respuesta entregada ≤ `_MAX_RESPUESTA_CHARS` y mensaje total ≤ 4096 en UNIDADES UTF-16 (`len(t.encode("utf-16-le"))//2`), probado con respuestas de 0 a 6000 caracteres y Cuenta/Desglose/Gráfico máximos. (c) Si tras omitir Gráfico y Desglose `restante < 0` → `EXPLAIN_UNAVAILABLE_MSG`; nunca se envía >4096 ni se trunca un bloque determinístico. (d) `GEMINI_RESPUESTA_RECORTADA` solo lleva enteros (test con caplog y canario). (e) Si hubo recorte, el mensaje incluye la línea fija `(explicación acortada por el largo del mensaje)` justo después de la respuesta, agregada DESPUÉS de los guards.
- [ ] **CS-24 (AUTO) Orden y equivalencia.** (a) Pipeline fijo: `crudo → tope CS-23a → _normalize_layout → _no_new_protected_tokens → _escalas_consistentes → violates_output_filter → _fit_respuesta → _build_leaf_message`. Un spy verifica que el string que ven ambos guards es idéntico al que recibe `_build_leaf_message` salvo el recorte (prefijo + a lo sumo la línea de CS-23e). (b) Tabla de bypass, todos rechazados: ``ht`tps://evil.com``, ``www`.evil.com``, ``[x]`(http://a)``, ``mira ](`x``, `@@ana`, `@<ZWSP>ana`, URL en la posición 1700 de un texto de 1800. Mutación de control: mover el filtro ANTES de normalizar debe romper un test. (c) Con 20 000 entradas aleatorias y semilla fija: idempotente; `set(salida) ⊆ set(entrada) ∪ {"•","\n"," "}`; sin `**`, `__` ni backtick. (d) La igualdad de `protected_tokens` entre entrada y salida se ELIMINA; los tokens se calculan SIEMPRE sobre el texto normalizado.
- [ ] **CS-25 (AUTO) Guards intactos y unión sin lavado.** (a) Diff vacío en `_no_new_protected_tokens`, `_normalize_numeric_token`, `_percent_token_a_decimal`, `ai_rewrite.protected_tokens`/`_PROTECTED_TOKEN_RE`. (b) Una única función `_compute_datos_tokens(datos_crudos, datos_presentados)` = tokens(json.dumps(crudo SIN redondear, igual que hoy)) ∪ tokens(json.dumps(presentado)); la usan el handler Y `scripts/gemini_smoke.py` (test por grep: el smoke no repite `json.dumps` + `protected_tokens`). (c) Por cada `question_code`: una cifra fuera del conjunto (`987654.31`) se rechaza; cada token monetario presentado con el último dígito cambiado se rechaza si no está en el conjunto. Mutación de control: quitar la llamada al guard rompe un test.
- [ ] **CS-26 (AUTO) Superficie al modelo.** (a) Los canarios de CA-40 (`chat_id`, `company_name`, `ticker`, `industry`) y canarios inválidos en `sector`, `perfil`, `peers_usados` ausentes del payload presentado, de `user_text` y de `systemInstruction`. (b) Snapshot de las claves `str` por `question_code`; una clave `str` nueva rompe el test y exige revisión de security. (c) `_present_payload` no agrega hojas `str` fuera de money/pct/ratio y `posicion_vs_precio`.
- [ ] **CS-27 (AUTO) Gráfico.** (a) `_build_grafico_*(datos: dict) -> Optional[str]`; `inspect.signature` sin parámetros `store`, `chat_id` ni `context_id`. (b) Valores admitidos: `type(v) in (int, float)`, `math.isfinite`, `v > 0` y `maximo > 0`; otro valor → `n/d` o `None`. (c) Envuelto en `try/except Exception → None` con log del tipo de excepción; probado con `nan`, `inf`, `True`, `"x"`, `1e300`, `maximo=0`. (d) Si supera `_MAX_GRAFICO_CHARS` → `None` (no se trunca). (e) Caracteres permitidos: `█`, `░`, etiquetas constantes y salidas de `_money`. (f) Spy de red y FS en 0; sin `parse_mode`; nunca entra a `datos_del_contexto` ni a `datos_tokens`. (g) `requirements.txt` y `Dockerfile` con diff vacío en Fases 1 y 2.
- [ ] **CS-28 (AUTO + revisión) `--show-text`:** cumple C1–C9 de 16.4.
- [ ] **CS-29 (AUTO)** Los tests de IDOR (`_get_owned_context`), `_validated_perfil`, `_validated_sector`, `_validated_peers` y la allowlist (CA-42) pasan SIN modificar.
- [ ] **CS-30 (AUTO)** Contador de llamadas a Gemini == 1 por clic, también con estructura incorrecta pero válida y con recorte. Sin reintento por calidad.
- [ ] **CS-31 (AUTO) Escala de montos (cierra B-1).** Tercer chequeo en `_fetch_explanation` DESPUÉS de `_no_new_protected_tokens` y SIN modificarla: función pura `_escalas_consistentes(payload_presentado, respuesta) -> bool`. Regex `(\$?\d[\d.,]*)\s*(mil millones|miles de millones|millones|billones|trillones)\b` con `re.IGNORECASE`. `escalas_payload` = conjunto `{(token_normalizado, sufijo)}` del JSON presentado. Regla 1: toda coincidencia de la respuesta debe estar en `escalas_payload`. Regla 2: si un token de la respuesta aparece en el payload SOLO con sufijo, no puede aparecer sin ese sufijo inmediatamente después. Con payload `$394.3 mil millones`: `$394.3 mil millones` OK; `$394.3 millones`, `$394.3` solo y `394.3 billones` rechazados. Razón `guard=escala` (smoke: `guard_escala`). Sin cambios en `_normalize_numeric_token`.
- [ ] **CS-32 (AUTO) Fidelidad de `_present_payload` (cierra M-3).** Cada hoja money/pct/ratio del payload presentado es igual al string de `_fmt_money_or_nd`/`_fmt_pct_or_nd`/`_fmt_ratio_or_nd` para el mismo valor crudo o, si la clave no se muestra en `Dato`, el valor parseado de vuelta coincide con el crudo dentro del redondeo. Test de colisión: ninguna clave de `_PAYLOAD_KIND` con dos unidades distintas entre preguntas (si ocurre, la clave pasa a `(question_code, clave)`). `bool`, `str`, `None` no se tocan; `NaN`/`inf` → `None`; se usa `type(v) in (int, float)`.
- [ ] **CS-33 (AUTO) Dirección del campo derivado (cierra M-1).** `diferencia_vs_precio` se reemplaza por dos campos: `"posicion_vs_precio"` (`"por encima"` | `"por debajo"` | `"igual"`) y `"diferencia_vs_precio_abs"` (porcentaje SIN signo, ej. `"15.3%"`). Tabla: `(496.00, 430.00) → ("por encima","15.3%")`; `(400.00, 430.00) → ("por debajo","7.0%")`; diferencia que redondea a `0.0%` → `"igual"`. Si precio es `None`, ≤0, `bool`, `NaN`/`inf` o no numérico, o el valor justo es `None`/no numérico: no se emite ninguno. Solo en `vf` y `ver`. CA-S7: revisión manual con `--show-text` sobre `vf` y `ver`: 0 respuestas con dirección contradiciendo `posicion_vs_precio`.
- [ ] **CS-34 (AUTO) Robustez del normalizador (cierra B-2).** Entradas de 6000 caracteres de `\n`, de `" \n"` y de espacios se normalizan en < 50 ms. Se prohíbe `\s` en las regex de líneas del normalizador; solo `[ \t]`. Propiedades de CS-24c con semilla fija.
- [ ] **CS-35 (AUTO) Prompts sin fuga de mecanismo.** Ambos prompts NO contienen (insensible a mayúsculas): `rechaz`, `guard`, `filtro`, `regex`, `token`, `bidi`, `http`, `límite de`, `tasa`, `rate`, `key`, `gemini`. Contienen la cláusula de cantidades con letras (16.5-F). Mantienen los asserts de CA-76; `systemInstruction` sigue siendo la constante del módulo.
- [ ] **CS-36 (AUTO) Emojis del marco (menor).** `_normalize_layout` quita de la respuesta del modelo los emojis del marco determinístico (🤖 📌 🧮 🔍 📐 📊 📋) para que no imite bloques verificados; conserva los de `SECTION_LABELS` (💡 🔎 🧭). Test: una línea `📐 Fórmula: …` del modelo queda sin el emoji.

## 16.4 `--show-text`: APROBADO con condiciones C1–C9
- **C1** Apagado por defecto; flag exacto `--show-text`; opcional `--show-text-max N` (defecto 6, máx. 30).
- **C2** Solo textos de explicaciones (no del rewrite), de contextos sintéticos con canarios, con `finishReason == STOP`. Nunca cuerpos de error, headers, request, key ni payload.
- **C3** Todo texto pasa por un único `_safe_display(texto, max_chars=2500)` con lista BLANCA: se conserva `\n`; se conserva cada carácter cuya `unicodedata.category` empiece con L, M, N, P, S, o sea Zs (convertido a `" "`); todo lo demás (Cc incl. ESC y C1, Cf incl. bidi/ZWJ/tag chars/U+FE0F aislado, Cs, Co, Cn, Zl, Zp) se escribe `<U+XXXX>`. Máx. 40 líneas por texto.
- **C4** Un texto rechazado por `filtro_salida` NO se imprime (solo razón y longitud). Los rechazados por `guard_numeros`, `guard_escala` o `largo` sí, saneados.
- **C5** Etiqueta `[codigo rep N ACEPTADO|RECHAZADO:motivo]`.
- **C6** No escribe archivos (test que reemplaza `builtins.open` por una función que lanza); sin `print` de variables de entorno.
- **C7** `evaluate_guards` sin diff; los tests de `test_gemini_smoke_guards.py` pasan sin modificar; guardas no se aflojan (key distinta de producción, no CI, no contenedor, sin token Telegram/key FMP, `GEMINI_LIVE_CONFIRM=yes`, tope 150 llamadas).
- **C8** Corrige E10: dentro de `_run` se instala un `NullHandler` en el logger `investbot` con `propagate=False` (restaurado al salir); con `--show-text`, los rechazos se imprimen solo por `_safe_display`.
- **C9** Tests de `_safe_display` con ESC+CSI, OSC 52, `\x9b`, U+202E, U+2028, U+061C, tag chars, ZWJ, NUL y 10 000 caracteres: la salida solo contiene categorías permitidas y respeta el tope.
- Aplicable también a producción (M-6): el log `GEMINI_GUARD_REJECT` pasa a usar `_safe_display(..., max_chars=500)`.

## 16.5 Correcciones puntuales al patch (texto exacto; PREVALECEN)
- **A. Sección 2.4, algoritmo de `_normalize_layout` (reemplazo completo):** pura, idempotente, lineal. Hasta 3 pasadas, deteniéndose cuando el resultado no cambia: (1) `\r\n`/`\r` → `\n`; (2) quitar `**`, `__`, `` ` ``; (3) quitar prefijo de encabezado `(?m)^[ \t]*#{1,6}[ \t]*`; (4) viñetas `(?m)^[ \t]*[*\-–][ \t]+` → `• ` (NUNCA `\s`); (5) quitar espacios/tabs al final de línea; (6) quitar los emojis del marco determinístico (🤖 📌 🧮 🔍 📐 📊 📋); (7) si una línea de `SECTION_LABELS` no tiene línea en blanco antes, insertarla; (8) colapsar 3+ `\n` en `\n\n`; `strip()` final. Solo borra, une o agrega `•`, ` ` y `\n`. Puede crear coincidencias del filtro, por eso los guards corren SIEMPRE sobre su salida.
- **B. Sección 2.4, orden en `_fetch_explanation`:** `generate_text → tope de entrada (_MAX_RAW_CHARS=6000, CS-23a) → _normalize_layout → _no_new_protected_tokens → _escalas_consistentes (CS-31) → violates_output_filter → devolver el texto normalizado SIN cortar`. El recorte pasa a `_build_leaf_message` (sección 4).
- **C. CA-74, última oración:** "Idempotente: `f(f(x)) == f(x)` con 20 000 entradas aleatorias y semilla fija. `set(salida) ⊆ set(entrada) ∪ {'•','\n',' '}`. Sin `**`, `__` ni backtick. (La igualdad de `protected_tokens` se elimina: es falsa por construcción, 16.1-E2.) Los tokens se calculan SIEMPRE sobre el texto normalizado."
- **D. Sección 4.1, `_fit_respuesta`, pasos 2–6 (reemplazo):** (2) cortar en el último `\n\n` antes de `max_chars`; (3) si no hay, en el último fin de oración `[.!?]` seguido de espacio o fin de texto; (4) si no hay ningún corte en la mitad final del rango (posición ≥ 0.4·max_chars) devolver `None` y el llamador envía `EXPLAIN_UNAVAILABLE_MSG` — NO se corta en un espacio ni se agrega `…` (podría dejar `$394.3 mil…`); (5) si el último bloque que queda es solo un encabezado de `SECTION_LABELS`, quitarlo; (6) loguear `GEMINI_RESPUESTA_RECORTADA chars=N max=M` (INFO, solo enteros). Longitud medida en UTF-16. **CA-72 corregido:** para TODO `max_chars` entre 50 y `len(texto)` el resultado es `None` o cumple `len ≤ max_chars` y es PREFIJO del original; `Counter(protected_tokens(resultado)) ≤ Counter(protected_tokens(original))`; y el último carácter del resultado no es dígito, `$` ni `%`.
- **E. Sección 5.4:** `datos_tokens = _compute_datos_tokens(datos_crudos, datos_presentados) = tokens(json.dumps(crudo SIN redondear, igual que hoy)) ∪ tokens(json.dumps(presentado))`, una única función usada por el handler y por `scripts/gemini_smoke.py`. El guard no verifica escala ni dirección: la escala se verifica con CS-31; la dirección se reduce con CS-33.
- **F. Sección 3.1 (y 3.2 por referencia):** al final de la regla 1 agregar `No escribas cantidades con letras ("el doble", "la mitad", "diez por ciento"): comparar con palabras vagas sí, expresar una cantidad que no está en el JSON no.`; al final de la regla 4 agregar `Tampoco uses emojis en el texto, salvo los encabezados.` Ninguna línea nueva del prompt empieza con dígito + ". ".
- **G. Sección 5.5:** reemplazar `diferencia_vs_precio` con signo por el par `posicion_vs_precio` / `diferencia_vs_precio_abs` de CS-33; CA-78 usa la tabla de verdad de CS-33.
- **H. CA-77:** agregar fidelidad CS-32 y 0 colisiones de unidad por clave.
- **I. CA-88:** reemplazar "sin acceso a `ExplanationContext` ajeno" por "las funciones `_build_grafico_*` no tienen parámetros `store`, `chat_id` ni `context_id` (`inspect.signature`)"; agregar casos `nan`, `inf`, `True`, `"x"`, `1e300`, `maximo=0`.
- **J. Sección 9, punto 2:** `--show-text` aprobado por `security` con las condiciones C1–C9.
- **K. Riesgos nuevos:** R-9 (el guard no verifica escala ni dirección → CS-31/CS-33); R-10 (`_present_payload` transformación de confianza → CS-32); R-11 (CPU del normalizador ante respuestas degeneradas → CS-23a, CS-34).
- **L. CA-S7:** agregar métricas de rechazos por `guard_escala` y `largo`, y revisión manual de dirección de `vf`/`ver` con `--show-text`.

## 16.6 Triage
- **Bloqueantes (resueltos en esta sección):** B-1 escala de montos (CS-31); B-2 `_normalize_layout` cuadrático/no idempotente (16.5-A/B/C, CS-23a, CS-34).
- **Correcciones menores a incorporar por el implementer (máx. 2 vueltas):** M-1 dirección (CS-33); M-2 `_fit_respuesta` (16.5-D, CS-23); M-3 fidelidad `_PAYLOAD_KIND` (CS-32); M-4 `_compute_datos_tokens` único (CS-25b); M-5 CA-72; M-6 `_safe_display` y logs (C8, C9); M-7 emojis del marco (CS-36); M-8 asserts negativos de prompts y cláusula de cantidades (CS-35, 16.5-F); M-9 robustez de barras (CS-27).
- **Backlog (siguiente run, NO tocar ahora):** BL-1 endurecer `violates_output_filter` (U+061C, U+2028/2029, U+0085, C1, U+00AD, U+034F, U+2061-2064, U+180E, U+FFF9-FFFB, tag chars, dominios sin esquema `evil.com`/`bit.ly/x`); BL-2 guard sensible al signo; BL-3 cantidades con letras ("quinientos"); BL-4 el guard es de pertenencia a un conjunto, no liga cifra a concepto; BL-5 retención del log `GEMINI_GUARD_REJECT`; BL-6 la spec de Pillow (Fase 3) debe incluir tope de tamaño del PNG, `pip-audit`, versión fijada y mismo balde del rate limiter.

## 16.7 Checklist de security tras la implementación
1. `git diff` vacío en `ai_rewrite.py`, `gemini_client.py` (incl. `_OUTPUT_FILTER_RE`), `requirements.txt`, `Dockerfile`; diff de `ai_explain.py` limitado a lo de la sección 10 más `_compute_datos_tokens`, `_escalas_consistentes`, `_safe_display`.
2. CS-23..CS-36 verdes, incluidas las mutaciones de control de CS-24b y CS-25c.
3. Grep: 0 de `_enforce_brevity` y `_MAX_EXPLANATION_CHARS`; 0 de `\s` en las regex del normalizador.
4. Prueba manual del normalizador con 6000 `\n` (< 50 ms).
5. Suite completa verde con N0 y N1 reportados.
6. `--show-text` con datos sintéticos: sin secuencias de escape en la consola.

---

# Sección 17 — QA (Iter-2)

Rol: `qa`, Momento 1, 2026-11-12. Base: `.claude/contextos/qa/skill.md`. Solo lectura sobre el repo. Los archivos de apoyo están en el scratchpad (rutas en 17.10).

**Veredicto: NO CONGELAR todavía.** Hay 9 bloqueantes (QB-1 a QB-9), 3 de ellos insatisfacibles tal cual están escritos. Los reemplazos de texto van abajo. Ningún bloqueante cambia el diseño de fondo del architect o de security. Con los reemplazos de 17.1 y las decisiones de 17.9 queda congelable.

## 17.0 Evidencia medida
Todo se corrió con `.venv/bin/python -m pytest -p no:cacheprovider`.

| Medición | Valor |
|---|---|
| HEAD real | `502d5df` ("Improve gemini"), working tree limpio salvo la spec (untracked). `957f11d` es su padre, y el contenido que describe el pedido ya está commiteado en `502d5df`. Registrar `git rev-parse HEAD` al arrancar cada fase y usar ese SHA como base de todos los diffs. |
| Suite base | 2529 passed, 4 skipped, N0 = 2533 nodos, 3.7 a 5.0 s de reloj |
| Test más lento | 0.14 s (`test_cs13_main_no_crea_archivos_en_el_repo`) |
| Skips | 4, todos `evt`/`inf` (`test_ai_explain.py:3315` y `:4181`) |
| Estabilidad base | `PYTHONHASHSEED` 0, 3 y 9 verdes. Orden inverso verde (plugin en el scratchpad). `-W error` verde en `test_ai_explain.py` (917 passed), `test_traceability.py` y `test_gemini_smoke_guards.py` (55 passed). |
| Cobertura base `ai_explain.py` | 96.9% líneas (1163/1200), 90.7% ramas (412/454). `gemini_client.py` 100%/100%. |
| Herramientas | `pytest-cov` sí. No hay `xdist`, `random-order`, `hypothesis` ni `mutmut`. No agregar dependencias de dev (`requirements-dev.txt` con diff vacío). |

Verificado con código propio de QA, sin tocar el repo:
- **Normalizador:** reconstruí 16.5-A y lo fuzzé. Reproduje dos familias de no idempotencia (QB-1).
- **Prompts:** extraje los prompts de 3.1 y 3.2 más las cláusulas de 16.5-F. Cumplen CA-76 y CS-35: reglas 1 a 7 contiguas y 0 palabras prohibidas.
- **Filtro de salida real:** probé las entradas de CS-24b (ver 17.1).
- **Regex de CS-31 y redondeo:** probé la regex y el redondeo de `_bar` (ver 17.1).
- **Reemplazo propuesto:** implementé el `_normalize_layout` de QB-1. Pasa 597 mil casos exhaustivos más 300 mil aleatorios, idempotente y lineal (6000 `\n` en 0.3 ms).

## 17.1 Revisión de criterios: bloqueantes, con texto exacto de reemplazo

### QB-1 (BLOQUEANTE) `_normalize_layout` de 16.5-A no cumple CA-74 ni CS-24c
"Hasta 3 pasadas" no garantiza idempotencia ni ausencia de `**`, `__` y backtick. Contraejemplos reproducidos, aplicando en el paso (2) `**`, luego `__`, luego backtick:
- `*_*_`_*_*` (9 caracteres) termina en `**`. Una segunda aplicación da `''`.
- `*_*_*_`_*_*_*` termina en `*_**_*`.
- `# # # # - x` termina en `# - x`, no idempotente. Con 10 `# ` queda `# # # # # # # - y`. La regex `#{1,6}[ \t]*` quita un solo `# ` por pasada.
- El generador aleatorio de security (E5) no los ve porque necesita profundidad de anidamiento.

Reemplazo de 16.5-A, orden obligatorio. Con este orden ninguna operación posterior crea coincidencias de una anterior, y es una pasada, lineal:
```
_normalize_layout(texto), pura, idempotente en UNA pasada y lineal. Orden fijo:
(1) \r\n y \r -> \n.
(2) borrar TODOS los caracteres '*', '_' y '`' (str.translate; no se borran pares, se borran caracteres).
(3) borrar los emojis del marco determinístico U+1F916 U+1F4CC U+1F9EE U+1F50D U+1F4D0 U+1F4CA U+1F4CB
    (OJO: el marco usa U+1F50D; la etiqueta '🔎' de SECTION_LABELS es U+1F50E y NO se borra).
(4) encabezados: (?m)^[ \t]*(?:#+[ \t]*)+  -> '' (quita TODOS los '#' iniciales de la línea).
(5) viñetas: (?m)^[ \t]*[-–][ \t]+  -> '• '   (el '*' ya no existe; nunca \s).
(6) espacios/tabs finales de línea: (?m)[ \t]+$ -> ''.
(7) línea en blanco antes de cada línea de SECTION_LABELS: re.sub(r"(?<=[^\n])\n(?=" + "|".join(map(re.escape, etiquetas)) + ")", "\n\n", t)
    (lookbehind/lookahead; con grupos consumidores falla con etiquetas contiguas, también medido).
(8) \n{3,} -> \n\n; strip() final.
Solo borra caracteres, une líneas, o agrega '•', ' ' y '\n'.
```
Oráculo para el implementer y para el test: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/norm2.py`.

Reemplazo de la parte de propiedades de CA-74 y CS-24c, que es la que hoy no pasa:
```
(c') Propiedades de _normalize_layout, sin excepciones:
  P1 idempotencia f(f(x)) == f(x); P2 sin '**', '__' ni '`' en la salida;
  P3 set(salida) ⊆ set(entrada) ∪ {'•', '\n', ' '};
  P4 sin '\r' ni 3 '\n' seguidos; P5 sin '#' ni '-'/'–' seguido de espacio al inicio de línea.
  Se verifican con (i) enumeración EXHAUSTIVA de todas las cadenas de largo ≤ 6 sobre el alfabeto
  ['*','_','`','#','-',' ','\n','📌','x'] (597 mil casos, < 1 s), (ii) el contraejemplo fijo
  '*_*_`_*_*' y '# # # # - x', y (iii) 20 000 cadenas aleatorias con random.Random(1234) sobre
  átomos que incluyen: * _ ` # - – • \t \r \n espacio, los 7 emojis del marco, las 5 etiquetas
  de SECTION_LABELS, '​', '@', '](', 'http://', '1', '$', '.', '%'. Un fuzz sin esos átomos no cuenta.
```

### QB-2 (BLOQUEANTE) CS-32 es insatisfacible
Pide que cada hoja money sea igual al string de `_fmt_money_or_nd`. Pero 5.3 manda `"$394.3 mil millones"` para montos ≥ 1e9, y `_fmt_money_or_nd` da `$394,328,000,000.00`. Reemplazo de la primera frase de CS-32:
```
Cada hoja money del payload presentado es EXACTAMENTE `_money_compact(valor_crudo)`. Para |valor| < 1e6 eso
es igual a `_fmt_money_or_nd(valor_crudo)`. Para |valor| >= 1e6, el token numérico parseado de vuelta
(x 1e6 o x 1e9 según el sufijo) difiere del crudo en menos de 0.05 de la unidad mostrada. Cada hoja pct es
`_pct1(crudo)` y cada hoja ratio es `f"{crudo:.2f}"`.
```

### QB-3 (BLOQUEANTE) CA-77 contradice 5.3(5)
CA-77 dice "ninguna hoja float con más de 2 decimales". 5.3(5) deja hojas sin clasificar como `round(x, 4)`. Reemplazo:
```
CA-77. Para cada question_code con llamada al modelo (los 25 de _ALL_CODES menos "evt" e "inf"; no
parametrizar con evt/inf para no sumar skips): (a) toda hoja money/pct/ratio del payload presentado es
str; (b) ninguna hoja float (sin clasificar) tiene más de 4 decimales ni notación científica
(re.search(r"[eE][+-]?\d", json.dumps(p)) is None); (c) monotonía: _compute_datos_tokens(crudo, presentado) ⊇ tokens calculados con la
expresión de HEAD copiada literal en el test (set comprehension con json.dumps(crudo) + protected_tokens
+ _normalize_numeric_token); (d) CS-32.
```

### QB-4 (BLOQUEANTE) CA-72 pide un "control positivo con `_enforce_brevity`" que CA-70 prohíbe
La función se borra. Reemplazo de la frase de control positivo de CA-72:
```
Control positivo: el test define localmente `_legacy_cut(t, n)` = `t[:t.rfind(".", 0, n) + 1] if t.rfind(".", 0, n) > 0 else t[:n] + "…"`
(sin usar el nombre prohibido por CA-70) y afirma que existe al menos un `max_chars` del barrido para el cual `_legacy_cut` VIOLA la propiedad
(texto de prueba: "A"*(k) + " vale $285.75 hoy. " + ... con el "." decimal en la posición 479).
```

### QB-5 (BLOQUEANTE) CA-72 corregido por 16.5-D sigue siendo insatisfacible en un borde
"Último carácter no es dígito, `$` ni `%`" falla cuando `max_chars == len(texto)`. `_fit_respuesta` devuelve el texto entero y puede terminar en dígito. Reemplazo:
```
CA-72. Barrido max_chars in range(50, len(texto) + 1) sobre textos con decimales, porcentajes, emojis fuera del BMP
(longitud medida en UTF-16: len(s.encode("utf-16-le")) // 2) y encabezados de SECTION_LABELS. Para cada max_chars:
resultado is None, o (len16(resultado) <= max_chars y texto.startswith(resultado) y
Counter(protected_tokens(resultado)) <= Counter(protected_tokens(texto)) y
(max_chars >= len16(texto) o resultado[-1] no es dígito, '$' ni '%')).
El texto de prueba termina en "." para que el caso max_chars == len no dependa del dato.
```

### QB-6 (BLOQUEANTE) Contrato de `_fit_respuesta -> None` dentro de `_build_leaf_message`
4.1 pone el ajuste en `_build_leaf_message`, que devuelve `str`. 16.5-D y CS-23(c) exigen `EXPLAIN_UNAVAILABLE_MSG` cuando no hay corte válido. Consecuencias:
- Los dos tests peor-caso (`test_build_desglose_vf_ticker_e_peor_caso_montos_extremos_bajo_el_tope`, l.3993, y `test_build_desglose_block_dcf_peor_caso_montos_extremos_bajo_el_tope`, l.4600) usan `"x" * MAX`. Esa cadena no tiene corte válido y daría `None`.
- `_build_leaf_message` tiene 17 llamadas en tests.

Propuesta, a ratificar por el architect (17.9-A4):
```
`_build_leaf_message` conserva su firma y comportamiento (concatenación pura), más `grafico: Optional[str] = None` (default None).
Se agrega `_fit_leaf_message(dato_line, respuesta, formula, fuente, *, cuenta, desglose, grafico) -> Optional[str]`: calcula el presupuesto
en UTF-16, omite Gráfico y luego Desglose si restante < _MIN_RESPUESTA_CHARS, llama a `_fit_respuesta`, agrega la línea de CS-23(e) si
hubo recorte y delega en `_build_leaf_message`. Devuelve None si restante < 0 o `_fit_respuesta` da None. El handler hace
`edit_message_text(EXPLAIN_UNAVAILABLE_MSG)` en ese caso. Los dos tests peor-caso pasan a llamar `_fit_leaf_message` con una respuesta
de oraciones (p. ej. "Dato. " * 300) y afirman len16 <= 4096; sus demás aserciones no cambian.
```

### QB-7 (BLOQUEANTE de trazabilidad) Choque de ids CA-S7
La spec base SÍ define `CA-S7` (AUTO, smoke guards sin ejecución al importar). Está en `contexto/specs/abiertas/SDD_migracion_gemini.md` l.702, 731 y 842. Está implementado como `test_ca_s7_*` en `tests/test_gemini_smoke_guards.py:79` y `:95`. Está cableado en `tests/test_traceability.py` (`S7_PREFIX`, `("ca","s7")` en `_faltantes`). La frase de la spec "la spec base llega a CA-S6" es falsa. Reemplazo:
```
Renumerar en TODA la spec (sec. 8, 9, 12, 16.5-L y handoff): CA-S7 -> CA-S8 (medición del smoke) y CA-S8 -> CA-S9 (rúbrica humana).
Ambos son MANUAL-LOCAL y NO entran en CA_AUTO. Los tests `test_ca_s7_*` existentes no se tocan.
```

### QB-8 (BLOQUEANTE para medir) El smoke no cubre los códigos de CA-S8
`scripts/gemini_smoke.py:249` define `_EXPLAIN_QUESTIONS = ("mod","ben","ren","ver","gra","dcf","rsk","pig")`. Faltan `vf` y `alz`, y el contexto sintético del script ya los admite. Reemplazo:
```
F0 amplía _EXPLAIN_QUESTIONS a ("mod","ben","ren","ver","gra","dcf","rsk","pig","vf","alz") (10 códigos). CA-S1 pasa a decir "10 preguntas".
`test_cs13_main_exito_con_mock_...` y `test_cs13_main_tope_de_150_...` deben seguir pasando sin modificar (150 llamadas se alcanzan con
--explain-reps 30). Costo por corrida real con defaults: 10x5 + 10x3 = 80 llamadas (< 150).
```

### QB-9 (BLOQUEANTE de diseño) Dónde se presenta el payload y dónde se agregan los campos derivados
Tests existentes que fijan el comportamiento:
- `test_payload_texto_libre_vf_sin_cambios_antes_y_despues_de_construir_el_desglose` (l.4071) afirma `set(datos_vf) == {7 claves}`. Agregar `posicion_vs_precio` o `diferencia_vs_precio_abs` dentro de `_payload_texto_libre` lo rompe.
- `_build_dato_line`, `_build_desglose_*` y las cuentas deben seguir recibiendo el payload CRUDO. Eso es CA-81.
- `_fetch_kwargs` (l.287) y 19 llamadas usan la firma `datos_del_contexto` y `datos_tokens`.
- `test_cs4_datos_tokens...` (l.5058) lee `seen["datos_del_contexto"]["peers_usados"]` y `seen["datos_tokens"]`.

Reemplazo, a ratificar (17.9-A3):
```
El handler (`_dispatch_leaf`) conserva `datos_del_contexto` CRUDO para Dato/Cuenta/Desglose. Construye `datos_modelo = _present_payload(datos_crudos)`,
agrega a la COPIA presentada `posicion_vs_precio` y `diferencia_vs_precio_abs` (solo vf/ver), y calcula
`datos_tokens = _compute_datos_tokens(datos_crudos, datos_modelo)`. Llama `_fetch_explanation(datos_del_contexto=datos_modelo, datos_tokens=datos_tokens, ...)`
con la firma ACTUAL sin parámetros nuevos. `_payload_texto_libre`/`_payload_avanzado` no cambian (sus tests de claves siguen verdes).
`_compute_datos_tokens(crudo, presentado=None)`: con `None` equivale a HEAD (sirve a la F0 y al baseline).
```

## 17.2 Revisión de criterios: menores (texto de reemplazo)

| Id | Problema | Reemplazo o aclaración |
|---|---|---|
| CA-70 | El grep sobre `tests/` se auto-matchea. La función `test_enforce_brevity_conservado_sin_perder_aserciones` de `test_traceability.py` contiene `_enforce_brevity` como substring. El comentario de `ai_explain.py:2112` también. | "(AUTO) un test recorre con `re` los `*.py` de `src/`, `scripts/` y `tests/` (NO `contexto/`) buscando las dos cadenas armadas por concatenación dentro del propio test: 0 coincidencias. Control positivo con fuente sintética. Se renombra `test_enforce_brevity_conservado...` a `test_fit_respuesta_conservado...` y se actualizan los comentarios. Además: `_MAX_RESPUESTA_CHARS == 1800`, `_MIN_RESPUESTA_CHARS == 400`, `_MAX_RAW_CHARS == 6000`." |
| CA-71 | Los tokens por defecto de `_fetch_kwargs` son `set()`. | "Texto de 1200 caracteres (>480, <1800), oraciones terminadas en '.', con `$285.75` y `15.3%`, y `tokens` que los contienen (usar `_compute_datos_tokens`). Resultado == `_normalize_layout(texto)`. Variante de 5000 caracteres: igual (la función NO corta; el corte es del paso de entrega)." |
| CA-73 | Las cifras máximas reales son: Cuenta 800 (`_MAX_CUENTA_CHARS`), Desglose 1200 (`_MAX_DESGLOSE_CHARS`), Gráfico 400, fórmula+fuente 427 como máximo (medido en `ai_explain_content`), disclaimer 427, header 22. Las partes fijas suman ~3400 y `restante` ronda 600. | "Cinco escenarios con `_fit_leaf_message`, todos en UTF-16 ≤ 4096: (A) restante ≥ 1800, sin recorte. (B) 400 ≤ restante < 1800, recorte sin omitir bloques. (C) restante < 400, se omite SOLO el Gráfico. (D) tras omitir el Gráfico sigue < 400, se omite también el Desglose. (E) tras omitir ambos restante < 0, devuelve `None`. Los escenarios C a E se arman con `formula` sintética larga (p. ej. 2800 caracteres), porque con los máximos reales no se llega a C." |
| CA-75 / CS-24a | Dicen cosas distintas ("exactamente" vs "salvo recorte"). | "Un spy registra el argumento de `_no_new_protected_tokens`, `_escalas_consistentes` y `violates_output_filter`, y lo que devuelve `_fetch_explanation`. Los cuatro son el MISMO string (`==`). Lo que recibe `_fit_leaf_message` como `respuesta` es ese string. El texto entregado es un prefijo del mismo, más a lo sumo la línea de CS-23(e)." |
| CA-76 / CS-35 | Faltan chequeos de las etiquetas. | Agregar: "(i) cada etiqueta de `SECTION_LABELS` cumple `protected_tokens(e) == []`, `not violates_output_filter(e)`, sin dígitos y sin '✅/❌'; (ii) cada etiqueta aparece en el prompt como línea completa; (iii) ninguna línea del prompt sustituido empieza con dígito + '. ' salvo las reglas 1 a 7." |
| CA-79 | Faltan filas. Se necesita datos de prueba controlados. | Agregar: "(f) Payload crudo `394328000000.0` y presentado `"$394.3 mil millones"`: la respuesta `$394.3 mil millones` es ACEPTADA. (g) Para el caso `15.3` vs `15.3%`: crudo `0.153` y presentado `"15.3%"`. El set de tokens no debe contener `15.3`; el test lo afirma antes de llamar al guard." |
| CA-82 | "diff vacío" no es un test pytest. | Es una puerta de fase: `git diff --quiet <SHA_base> -- src/investbot/ai_rewrite.py src/investbot/gemini_client.py requirements.txt Dockerfile requirements-dev.txt` debe dar exit 0. |
| CA-83 | `FAILURE_TABLE` es compartida con CA-17 a CA-21, CA-31 y CA-43. | No se editan filas de la tabla compartida. Los rechazos nuevos (`largo`, `escala`) van en tests propios `test_ca83_*`, cada uno con exactamente 1 registro `GEMINI_GUARD_REJECT` (regla CA-69). |
| CA-84 | Hay que agregar el caso con Gráfico. | Test nuevo `test_ca84_*` (no editar la lista de `test_ca46`): éxito con gráfico (Fase 2), recorte y "acortada" sin `parse_mode`. |
| CA-85 | El golden debe salir de ejecutar el pipeline, no de pegar el Ejemplo A. | El golden de Fase 1 NO tiene `Gráfico`; en la Fase 2 se agrega un segundo golden con gráfico. Fijar contexto, respuesta y comparar con un literal `"""..."""` en el test, generado una vez y revisado a ojo. |
| CA-86 | **Error numérico en 6.3.** Con máximo 510 y ancho 12, el Valor Justo (496) da `496/510*12 = 11.67`, o sea 12 bloques. El ejemplo dibuja 11. Además `round()` de Python redondea half-even (`round(2.5) == 2`). | Corregir la línea del ejemplo a `Valor Justo    ████████████  $496.00`. Agregar: "la longitud es `max(1, int(valor/maximo*ancho + 0.5))` (half-up, sin `round`). Caso fijo: `(5, 24, 12)` da 3 bloques." Los 12 y 10 de "500 vs 430" son correctos con máximo 500 o 510. |
| CA-88 | Falta el resultado de varios bordes. | "`1e300` hace que `_money` mida 405 caracteres, el bloque supera 400 y el builder devuelve `None` (no `n/d`). `maximo=0` se prueba directo sobre `_bar(valor, 0)`: sin excepción, línea `n/d`. Un solo valor válido da `None`." |
| CS-23 | Detalles medibles. | "(a) Se prueba con spy: con 6001 caracteres no se invoca `_normalize_layout` ni `protected_tokens`; con 6000 exactos sí. (d) `caplog` con respuesta canario: el registro `GEMINI_RESPUESTA_RECORTADA` cumple `re.fullmatch(r"GEMINI_RESPUESTA_RECORTADA chars=\d+ max=\d+", msg)`. (e) La línea se define como la constante `_NOTA_ACORTADA = "(explicación acortada por el largo del mensaje)"`, entra en el cómputo de `restante` y se cuenta dentro de 4096, pero no dentro de `_MAX_RESPUESTA_CHARS`. (b) Se agrega un caso con respuesta de oraciones de 40 emojis fuera del BMP (cada una ocupa 2 unidades UTF-16): la versión con `len()` en vez de UTF-16 debe fallar." |
| CS-24b | De la tabla de bypass solo dos filas discriminan el orden. | Filas discriminantes (el filtro crudo da False y el normalizado True): ``ht`tps://evil.com`` y ``www`.evil.com``. Las demás (``[x]`(http://a)``, ``mira ](`x``, `@@ana`, `@<ZWSP>ana`) ya las rechaza el filtro sin normalizar: quedan como control. Agregar la fila "URL después de la posición 1800 en un texto de 2500" (control de que el filtro corre sobre el texto completo). |
| CS-25a | "diff vacío" de funciones. | Medición: `ast.dump` de `_no_new_protected_tokens`, `_normalize_numeric_token`, `_percent_token_a_decimal` y `ai_rewrite._PROTECTED_TOKEN_RE`/`protected_tokens` es igual al de `git show <SHA_base>:src/investbot/<archivo>`. Puerta de fase, script de 10 líneas. |
| CS-26b | "Snapshot" sin definir. | Un diccionario literal `{code: frozenset(claves str del payload)}` capturado de HEAD más las claves nuevas permitidas (`posicion_vs_precio`, y las hojas pct/money/ratio). |
| CS-31 | Regex ya probada. `"$394.3 mil  millones"` (dos espacios) no matchea. | Tabla de tests con payload `"$394.3 mil millones"`: `$394.3 mil millones` OK; `$394.3 millones` NO; `$394.3` solo NO; `394.3 billones` NO; `$394.3 mil  millones` NO (fail-closed); `394.3\nmillones` NO (el payload no lo trae). Cada fila verifica el `guard=escala` y que `_no_new_protected_tokens` la aceptaba (si no, no se prueba nada). |
| CS-33 | Falta cómputo sobre el valor redondeado. | "`posicion_vs_precio` se calcula sobre el valor ya redondeado a 1 decimal. Filas: (496.00, 430.00) → por encima / 15.3%; (400.00, 430.00) → por debajo / 7.0%; (430.01, 430.00) → igual / 0.0%; precio `True` → ninguno; precio `float('nan')` → ninguno; valor justo `'x'` → ninguno." |
| CS-34 | 50 ms de reloj puede ser frágil en CI. | "`min` de 3 mediciones < 50 ms con 6000 `\n`, con `' \n'*3000` y con `' '*6000`; más un test de escala: con 24 000 caracteres la medición es < 5x la de 6000 (la versión cuadrática da ~16x)." |
| CS-36 | Los emojis `🔎` (U+1F50E, etiqueta) y `🔍` (U+1F50D, marco Desglose) se confunden a simple vista. | El test usa `"\U0001F50D"` y `"\U0001F50E"` explícitos: el primero se borra y el segundo se conserva. |
| M-6 | `_safe_display` conserva `\n`, pero el test existente `test_cs5_log_del_rechazo_sanitiza_y_trunca_a_500_caracteres` (l.5156) exige que el mensaje de log no tenga `\n` y que `respuesta=` mida 500. | El log usa `_safe_display(_sanitize_for_log(r), max_chars=500)` (o un parámetro `keep_newlines=False`). El test (`startswith("@alguien ")`, `"\n" not in mensaje`, `len == 500`) queda sin modificar. |
| 0.4 vs mitad | 16.5-D dice "mitad final del rango (≥ 0.4·max_chars)". 0.4 no es la mitad. | Usar `>= 0.4 * max_chars` y quitar "mitad final". |

## 17.3 Tests existentes que fijan 480, prompts viejos, 1024 y `datos_tokens`
Verificados con grep en `tests/test_ai_explain.py`, `tests/test_traceability.py`, `scripts/gemini_smoke.py` y `tests/test_gemini_smoke_guards.py`. Los números de línea son de `502d5df`.

| Nodeid | Línea | Qué fija | Acción |
|---|---|---|---|
| `test_ai_explain.py::test_enforce_brevity_texto_corto_se_devuelve_sin_modificar` | 981 | 1 assert | renombrar a `test_fit_respuesta_texto_corto_se_devuelve_sin_modificar` |
| `…::test_enforce_brevity_texto_largo_con_punto_corta_en_ultimo_punto` | 986 | 3 asserts (len ≤ máx, endswith ".", sin "…") | renombrar a `test_fit_respuesta_texto_largo_corta_en_ultimo_fin_de_oracion`. Texto con oraciones. Mantiene 3 asserts. |
| `…::test_enforce_brevity_texto_largo_sin_punto_corte_duro_con_puntos_suspensivos` | 994 | 1 assert de corte duro con "…" | rescribir a `test_fit_respuesta_sin_corte_valido_devuelve_none` (1 assert: `is None`). Es un cambio de comportamiento deliberado de 16.5-D, no un debilitamiento. |
| `…::test_system_prompt_explain_pide_texto_plano_con_marcador_y_brevedad` | 1646 | `"2 a 4 oraciones" in …` | cambiar solo ese assert por `not in`. Mantiene `{"respuesta"` ausente. |
| `…::test_system_prompt_explain_regla_5_pide_nombrar_el_modelo` | 1652 | `"5."` y `"modelo"` | sin cambios |
| `…::test_system_prompt_paso_a_paso_pide_texto_plano_con_marcador_y_no_recalcular` | 3039 | `cuenta`, `YA ESTÁ CALCULADA` | sin cambios (los prompts nuevos los conservan) |
| `…::test_build_desglose_vf_ticker_e_peor_caso_montos_extremos_bajo_el_tope` | 3993 (usa `_MAX_EXPLANATION_CHARS` en 4029) | `"x" * 480` | ver QB-6 |
| `…::test_build_desglose_block_dcf_peor_caso_montos_extremos_bajo_el_tope` | 4600 (en 4639) | ídem | ver QB-6 |
| `…::test_payload_texto_libre_vf_sin_cambios_antes_y_despues_de_construir_el_desglose` | 4071 | set de 7 claves de `vf` | **no está en la lista del architect.** Debe pasar sin modificar (QB-9). |
| `…::test_cs4_datos_tokens_se_calcula_sobre_el_payload_ya_filtrado` | 5058 | `seen["datos_tokens"]` y `seen["datos_del_contexto"]` | **tampoco está en la lista.** Debe pasar sin modificar (QB-9). |
| `…::_tokens_de` (helper) | 1080 | repite el cálculo de `datos_tokens` de producción | cambiarlo a `_compute_datos_tokens(datos, None)`. Es helper, no test; sus 2 usos siguen igual. |
| `…::test_fetch_explanation_respuesta_limpia_sin_eco_pasa_normal` | 1089 | resultado == texto | sin cambios; el normalizador no toca ese texto |
| `…::test_cs5_el_filtro_corre_antes_de_enforce_brevity_url_pasada_la_posicion_480` | 5145 | usa `_enforce_brevity` y 480 | renombrar a `test_cs5_el_filtro_corre_sobre_el_texto_completo_url_pasada_la_posicion_1800`. Relleno > 1800 y < 6000. Control: `"http://" not in _fit_respuesta(texto, 1800)`. |
| `…::test_cs5_log_del_rechazo_sanitiza_y_trunca_a_500_caracteres` | 5156 | log, 500 caracteres | **tampoco está en la lista.** Ver M-6; sin modificar. |
| `…::test_cs5_rechazo_por_guard_de_numeros_tambien_usa_el_token` | 5169 | `guard=numeros` | sin cambios; los nombres `numeros` y `salida` se conservan, se agregan `largo` y `escala` |
| `…::test_ca67_prompts_reglas_1_a_6_contiguas_y_contenido[SYSTEM_PROMPT_EXPLAIN]` / `[…PASO_A_PASO]` | 5562 | `[1,2,3,4,5,6]`, "Copiá los números exactamente como aparecen en el JSON", "mismo formato, punto decimal, sin separador de miles" | los dos strings y la lista cambian a 1..7 con otros textos (cambio deliberado). Renombrar a `…reglas_1_a_7…`. Se mantienen `5.`, `modelo`, `español rioplatense` y las 3 prohibiciones. |
| `…::test_ca67_paso_a_paso_conserva_cuenta_y_ya_esta_calculada` | 5576 | cuenta, YA ESTÁ CALCULADA | sin cambios |
| `…::test_ca11_tope_de_tokens_de_explicaciones_es_1024` | 5581 | `== 1024` | renombrar a `…es_2048`, `== 2048` |
| `…::test_ca11_fetch_explanation_usa_1024_tokens_timeout_configurado_y_un_reintento` | 5585 | `maxOutputTokens == 1024` | renombrar a `…usa_2048_tokens…`. Cambiar solo ese valor; las otras 4 aserciones quedan. |
| `…::test_ca41_system_instruction_es_la_constante_del_modulo[ren-…]` / `[vf-…]` | 5328 | `systemInstruction == constante` | sin cambios |
| `test_ai_rewrite.py` (l.1296 y 1308), `test_gemini_client.py` (l.547 a 583) | — | 2048 del rewrite y 1024 genérico del cliente | sin cambios (no son del flujo de explicaciones) |
| `tests/test_traceability.py::M_BREV` | 294 | 3 nombres, 5 aserciones | renombrar las 3 claves. Suma ≥ 5. |
| `tests/test_traceability.py::test_enforce_brevity_conservado_sin_perder_aserciones` | 336 | len==3, sum==5 | renombrar a `test_fit_respuesta_conservado_sin_perder_aserciones` (CA-70) |
| `tests/test_traceability.py::test_ca64_los_reemplazados_quedan_fuera_de_la_lista_auto` | 155 | `len(CA_AUTO) == 6+14+26+5`, `len(CS_AUTO) == 13` | actualizar por fase (17.4) |
| `scripts/gemini_smoke.py` | 410 | `ai_explain._MAX_EXPLANATION_CHARS` | → `_MAX_RESPUESTA_CHARS` |
| `scripts/gemini_smoke.py` | 380-383 | repite `json.dumps` + `protected_tokens` | usar `_compute_datos_tokens` (CS-25b) |
| `scripts/gemini_smoke.py` | 417-427 | clasificación de razones | debe replicar el orden del pipeline: largo → normalizar → numeros → escala → salida (si no, el baseline y el nuevo no son comparables) |
| `tests/test_gemini_smoke_guards.py` | — | sin referencias a 480/1024/prompts | solo se agregan tests. Los existentes no se modifican. |

Más allá de la lista: ningún otro test de `test_ai_explain.py` usa "480", "1024" ni "2 a 4 oraciones" (grep completo).

## 17.4 Preservación: qué NO puede debilitarse y cómo medirlo
Aserciones intocables, con su medición:
1. **Guards anti-invención.** Los 15 tests de M_CA25 y los casos de `test_ca26_guard_de_numeros_casos_portados_de_is_safe_rewrite`. Medición: multiset de aserciones y `ast.dump` igual al de la base (CS-25a).
2. **IDOR y validaciones.** `_get_owned_context`, `_validated_perfil`, `_validated_sector`, `_validated_peers`, `test_cs22_*` y M_CA42_CTX/STORE/SEC/BOT. Sin modificar.
3. **Filtro de salida y `_OUTPUT_FILTER_RE`.** Diff vacío (CA-82).
4. **Matriz CA-43 / CA-31 / CA-69 / A11.** `test_ca43_*` y `FAILURE_TABLE`: sin tocar la tabla ni los asserts de canario de key, cuerpo e `Illegal header value`.
5. **CA-46 / CA-84.** Ninguna llamada con `parse_mode`.
6. **CA-81.** Los asserts con `"$480.00"`, `"$496.00"` y similares quedan intactos.
7. **Manifiestos de `test_traceability.py`:** M_CA25, M_CA33_*, M_CA34, M_CA36, M_CA42_* y sus contadores (`== 15/16`, `10/12`, `14/48`, `12/17`, `3/7`, `13`). Solo se renombra `M_BREV`.

Medición reproducible, al estilo CQ-7: el script compara por test el multiset de aserciones contra `git show <SHA_base>`. Ruta: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/assert_multiset_diff.py`. Validado: contra HEAD da `TOTAL_NO_PERMITIDOS 0`.
```
.venv/bin/python -I "$S/assert_multiset_diff.py" "$PWD" <SHA_base>          # debe terminar con "TOTAL_NO_PERMITIDOS 0"
ALLOW_JSON='{"<test>": ["<substring de aserción permitida a perder>"], "<test_renombrado>": ["RENAMED_OR_DELETED"]}' ...  # solo para los renombrados de 17.3
.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider | grep :: | sort > nodeids_nuevo.txt
git stash-free comparison: git show <SHA_base>:tests/... no hace falta; basta comparar nodeids_base (colectado en la fase 0) contra nodeids_nuevo
comm -23 nodeids_base.txt nodeids_nuevo.txt            # = SOLO los 9 renombrados de 17.3 (y los ids [..] de ca67/ca11 si cambian de nombre)
```
Reglas:
- Toda pérdida de aserción fuera de la allowlist de 17.3 es bloqueante (defecto ALTO).
- También se compara el **conjunto de nodeids**, para detectar parametrizaciones recortadas.

**Reglas de CA-64 para los ids nuevos:**
- El regex es `^test_(ca|cs)(\d+)(?=_|$)`. Los sub-criterios se escriben `test_cs23_a_…`, no `test_cs23a_…` (esto último NO matchea).
- Nombres válidos: `test_ca70_…` a `test_ca88_…` y `test_cs23_…` a `test_cs36_…`.
- CA-S8 y CA-S9 son manuales y no llevan test.
- `CA_AUTO` y `CS_AUTO` se extienden por fase, porque se despliega Fase 1 sola (spec sec. 12). Si no, la F1 queda roja por exigir CA-86 a CA-88.

| Fase | `CA_AUTO` agrega | `len(CA_AUTO)` | `CS_AUTO` agrega | `len(CS_AUTO)` |
|---|---|---|---|---|
| F0 | — | 51 | 25, 28 | 15 |
| F1 | 70 a 85 (16 ids) | 67 | 23, 24, 26, 29, 30, 31, 32, 33, 34, 35, 36 | 26 |
| F2 | 86 a 88 | 70 | 27 | 27 |

Actualizar en cada fase la fórmula de `test_ca64_los_reemplazados_quedan_fuera_de_la_lista_auto`.

**Impacto en el manifiesto de `test_traceability.py`:**
- `M_BREV` es el único afectado (rename).
- M_CA25, M_CA33_*, M_CA34, M_CA36 y M_CA42_* **no se tocan**. Ninguna de sus pruebas vive en las zonas modificadas, salvo las de CA-25/26 (guards), que son de funciones con diff vacío.

## 17.5 Plan de fases con puertas verdes
Convenciones (todas desde la raíz del repo, `S` = scratchpad):
- `P=".venv/bin/python -m pytest -q -p no:cacheprovider"`.
- Registrar `BASE=$(git rev-parse HEAD)` al arrancar cada fase.

Puertas comunes (G1 a G9), repetidas en cada fase:
- **G1** Suite completa: `$P` → N pasados esperados (ver 17.7), 0 fallos, skips ≤ 4.
- **G2** Estabilidad en archivos tocados: `for s in 0 1 2 3 4 5 6 7 8 9; do PYTHONHASHSEED=$s $P tests/test_ai_explain.py tests/test_traceability.py tests/test_gemini_smoke_guards.py || break; done`. Más suite completa con semillas 0, 3 y 9.
- **G3** Orden inverso: `PYTHONPATH=$S $P -p reverse_order`.
- **G4** `-W error` en los archivos tocados y los nuevos: `$P -W error tests/test_ai_explain.py tests/test_traceability.py tests/test_gemini_smoke_guards.py [archivo nuevo]`.
- **G5** Preservación: `assert_multiset_diff.py` con `TOTAL_NO_PERMITIDOS 0` y `comm -23` de nodeids (17.4).
- **G6** Diff limitado: `git diff --stat $BASE` solo toca los archivos de 17.10. `git diff --quiet $BASE -- requirements.txt requirements-dev.txt Dockerfile src/investbot/ai_rewrite.py src/investbot/gemini_client.py` exit 0.
- **G7** Cobertura: `$P --cov=investbot --cov-branch --cov-report=json:$S/cov.json && .venv/bin/python -I $S/cov_new_lines.py $S/cov.json $BASE src/investbot/ai_explain.py` (umbrales en 17.6). Borrar `.coverage` al terminar.
- **G8** `$P --durations=10`: suite ≤ 8 s y ningún test > 0.5 s.
- **G9** Mutaciones obligatorias de la fase (17.7).

**F0 — smoke y utilitarios compartidos** (sin cambiar prompts ni comportamiento del modelo)
- Contenido:
  - `scripts/gemini_smoke.py`: `--show-text` y `--show-text-max`, `--thinking`, `_EXPLAIN_QUESTIONS` ampliado (QB-8), NullHandler (C8), métricas que no dependen del código nuevo (ver 17.8).
  - `ai_explain.py`: `_safe_display`, `_compute_datos_tokens(crudo, presentado=None)` usada por el handler y el smoke, y el log de rechazo con `_safe_display(_sanitize_for_log(...), 500)`.
  - Tests de CS-28 / C1 a C9, CS-25(b), M-6.
- Puerta específica: **ningún test existente se modifica**, salvo `test_ca64_los_reemplazados…` (constantes 51 y 15). Prueba: `git diff $BASE -- tests | grep '^-[^-]'` solo muestra esa línea.
- Comando de aceptación F0: G1 a G8 más `grep -n "json.dumps" scripts/gemini_smoke.py` sin ninguna línea que combine `protected_tokens` (CS-25b).
- **Salida de F0:** baseline en vivo, MANUAL-LOCAL, a cargo de la usuaria. `GEMINI_LIVE_KEY=… GEMINI_LIVE_CONFIRM=yes python scripts/gemini_smoke.py --explain-reps 5 --show-text | tee baseline_minimal.txt`, y repetir con `--thinking low`. Con prompts viejos.

**F1 — núcleo de redacción**
- Contenido: `SECTION_LABELS`, prompts (3.1 y 3.2 más 16.5-F), `_normalize_layout` (QB-1), `_MAX_RAW_CHARS`, `_escalas_consistentes`, `_fit_respuesta`, `_fit_leaf_message` (QB-6), `_present_payload` y `_PAYLOAD_KIND`, `_money_compact`, `posicion_vs_precio` / `diferencia_vs_precio_abs`, tokens 2048, `ai_explain.py` ya sin `_enforce_brevity`.
- Smoke: clasificación de razones con el orden nuevo, `guard_escala`, `largo`.
- Tests: todos los CA-70 a CA-85 y CS-23 a CS-26, CS-29 a CS-36, más las modificaciones de 17.3.
- Orden interno sugerido (cada paso con suite verde):
  1. Borrado de `_enforce_brevity` y `_fit_respuesta` con sus tests renombrados.
  2. `_normalize_layout`.
  3. Tope de entrada y pipeline CS-24a.
  4. Prompts.
  5. `_present_payload` y `_compute_datos_tokens`.
  6. `_escalas_consistentes`.
  7. Campos derivados.
  8. Presupuesto de entrega y línea CS-23(e).
  9. Tokens 2048.
- G1 a G9, más: `grep -c '\\s' ` sobre el cuerpo de `_normalize_layout` = 0 (el patrón `\s` está prohibido por CS-34; el implementer lo verifica con `inspect.getsource` en un test) y la prueba manual 6000 `\n` < 50 ms (CS-34).
- **Salida de F1:** CA-S8 con `minimal` y con `low`, y rúbrica CA-S9. Si CA-S8 recomienda recalibrar `_MAX_RESPUESTA_CHARS`, es un cambio de constante y del test que la fija, sin nueva spec.

**F2 — barras Unicode** (`vf`, `gra`, `dcf`, `mul`, `ver`)
- Contenido: `_bar`, `_build_grafico_vf`, `_build_grafico_escenarios` (gra, dcf, mul) y `_build_grafico_ver`, con `try/except`, y la integración (parámetro `grafico` y orden de omisión).
- Tests: CA-86 a CA-88, CS-27, golden con gráfico, más CA-84 con gráfico.
- G1 a G9, más `git diff --quiet $BASE -- requirements.txt Dockerfile` (CS-27g).
- Revisión visual de la usuaria sobre el mensaje de `vf` real.

**F3 — cierre técnico y documentación**
- Contenido: actualizar comentarios y docstrings que citan 480 o `_enforce_brevity`, README o `.env.example` solo si aplica, mover la spec a `cerradas/` solo si la usuaria lo autoriza, y el QA Handoff.
- Puerta: G1 a G9 completas desde cero sobre el working tree final, `grep` de CA-70 = 0, `git diff --stat` coincide con la tabla de 17.10, y un nuevo N1 reportado.

## 17.6 Cobertura, estabilidad, tiempo
- **Líneas nuevas o cambiadas en `ai_explain.py`:** ≥ 98% de líneas y toda rama de decisión de las funciones puras cubierta. Medido con `cov_new_lines.py` (`COBERTURA_LINEAS_NUEVAS ≥ 98.0%` y `ramas_sin_cubrir` vacío para las funciones listadas). Cualquier línea no cubierta se justifica por escrito (defensiva imposible de alcanzar).
- **100% de líneas y ramas** en estas funciones puras: `_normalize_layout`, `_fit_respuesta`, `_fit_leaf_message`, `_escalas_consistentes`, `_present_payload`, `_money_compact`, `_compute_datos_tokens`, `_safe_display`, `_bar`, `_build_grafico_*`.
- **Módulo completo:** no puede bajar de la base: ≥ 96.9% de líneas (37 sin cubrir hoy) y ≥ 90.7% de ramas. Las 37 líneas sin cubrir de hoy no cuentan como "nuevas".
- **`gemini_client.py`:** 100%/100% se mantiene (sin cambios de código).
- **Smoke:** `scripts/` no entra en cobertura. Sus funciones nuevas (`_safe_display` vive en `ai_explain`) se prueban desde `test_gemini_smoke_guards.py`.
- **Estabilidad:** `PYTHONHASHSEED` 0 a 9 en los archivos tocados y semillas 0, 3 y 9 en la suite; orden inverso; `-W error`. Flaky = 0. Cualquier fallo intermitente se cuarentena y no se ignora.
- **Tiempo:** suite ≤ 8 s (base 3.7 a 5.0 s); ningún test > 0.5 s (base 0.14 s). La enumeración exhaustiva del normalizador debe correr en < 1 s y el fuzz de 20 000 casos en < 1 s. Si se pasa, reducir el largo de la enumeración, no el fuzz.

## 17.7 Mutaciones obligatorias (manual: no hay `mutmut` ni `hypothesis`)
Protocolo, sin tocar el árbol de trabajo:
1. `mkdir "$S/mut" && rsync -a --exclude .git --exclude .venv --exclude __pycache__ ./ "$S/mut/"` y reutilizar `.venv` por ruta absoluta.
2. Cada mutante se aplica con `python - <<` haciendo `str.replace` y `assert count == 1`.
3. Se corre `$P -x <nodeids esperados>` dentro de `"$S/mut"`.
4. Aceptación: exit ≠ 0 y el test que falla ∈ columna "test que debe fallar". Si falla otro test y no el esperado, la mutación sigue "viva" para ese criterio.
5. Todo mutante equivalente se documenta por escrito.

Meta: 100% de las mutaciones M-01 a M-34 muertas por el test indicado (F1 y F0 según la columna "Fase"); los equivalentes se documentan.

| # | Fase | Mutación | Test que debe fallar |
|---|---|---|---|
| M-01 | F1 | quitar la llamada a `_escalas_consistentes` | `test_cs31_*` (fila `$394.3 millones`) |
| M-02 | F1 | `_escalas_consistentes` sin regla 2 | `test_cs31_*` (fila `$394.3` solo) |
| M-03 | F1 | `_escalas_consistentes` sin regla 1 | `test_cs31_*` (fila `394.3 billones`) |
| M-04 | F1 | mover `violates_output_filter` antes de `_normalize_layout` | `test_cs24_b_*` (filas ``ht`tps`` y ``www`.``) |
| M-05 | F1 | pasar el texto crudo a los guards y el normalizado a la entrega | `test_ca75_*` (spy `==`) |
| M-06 | F1 | restaurar `^\s*[*\-–]\s+` | `test_cs34_*` (tiempo y ratio 6000/24000) y el caso `"a\n\n\n- b"` que conserva la línea en blanco |
| M-07 | F1 | `_normalize_layout` en 3 pasadas de la versión 16.5-A | `test_cs24_c_*` (contraejemplos fijos y enumeración exhaustiva) |
| M-08 | F1 | `_normalize_layout` sin borrar emojis del marco | `test_cs36_*` |
| M-09 | F1 | `_normalize_layout` borra también U+1F50E | `test_cs36_*` (caso "se conserva") |
| M-10 | F1 | `_normalize_layout` devuelve el texto sin cambios | `test_ca74_*` |
| M-11 | F1 | `_fit_respuesta` corta en el último espacio y agrega "…" | `test_ca72_*` (barrido) |
| M-12 | F1 | `_fit_respuesta` corta en el último "." con `rfind(".")` | `test_ca72_*` (caso decimal) |
| M-13 | F1 | `_fit_respuesta` mide `len()` en vez de UTF-16 | `test_ca72_*` y `test_cs23_b_*` (emojis fuera del BMP) |
| M-14 | F1 | `_fit_respuesta` sin quitar el encabezado huérfano | `test_ca72_*` (caso encabezado) |
| M-15 | F1 | `_fit_respuesta` sin umbral 0.4 (siempre devuelve algo) | `test_ca72_*` (espera `None`) |
| M-16 | F1 | borrar la unión en `_compute_datos_tokens` (solo presentado) | `test_ca77_*` (monotonía) |
| M-17 | F1 | `_compute_datos_tokens` solo crudo | `test_ca79_*` fila (f) `$394.3 mil millones` aceptada |
| M-18 | F1 | `_present_payload` devuelve el crudo (sin redondear) | `test_ca77_*` |
| M-19 | F1 | `_present_payload` toca `bool`/`str` | `test_cs32_*` |
| M-20 | F1 | `_PAYLOAD_KIND` con una unidad cambiada (pct↔ratio) | `test_cs32_*` (fidelidad y colisión) |
| M-21 | F1 | `posicion_vs_precio` invertida | `test_cs33_*` y `test_ca78_*` |
| M-22 | F1 | `posicion_vs_precio` calculada sobre el valor crudo | `test_cs33_*` (fila 430.01 → igual) |
| M-23 | F1 | `diferencia_vs_precio_abs` emitido en otros códigos | `test_cs26_*` (snapshot) |
| M-24 | F1 | quitar el tope `_MAX_RAW_CHARS` | `test_cs23_a_*` (spy sobre 6001) |
| M-25 | F1 | tope `>=` en vez de `>` | `test_cs23_a_*` (borde 6000 aceptado) |
| M-26 | F1 | sin ajuste dinámico (solo 1800) | `test_ca73_*` escenario B y `test_cs23_b_*` |
| M-27 | F1 | omisión de Desglose antes que Gráfico | `test_ca73_*` escenarios C y D |
| M-28 | F1 | `restante < 0` no devuelve `None` | `test_ca73_*` escenario E y `test_cs23_c_*` |
| M-29 | F1 | línea "acortada" agregada antes de los guards | `test_cs24_a_*` / `test_cs23_e_*` |
| M-30 | F1 | log `GEMINI_RESPUESTA_RECORTADA` con el texto | `test_cs23_d_*` |
| M-31 | F1 | quitar la llamada a `_no_new_protected_tokens` | `test_cs25_c_*` y los 15 de M_CA25 |
| M-32 | F1 | segunda llamada a Gemini ante mala estructura | `test_cs30_*` |
| M-33 | F1 | `MAX_EXPLANATION_OUTPUT_TOKENS = 1024` | `test_ca80_*` (los dos) |
| M-34 | F1 | etiqueta de `SECTION_LABELS` con un dígito | `test_ca76_*` (inocuas ante guard) |
| M-35 | F0 | `_safe_display` deja pasar `Cf` o ESC | `test_cs28_*` (C9: ESC+CSI, OSC 52, `\x9b`, U+202E, U+2028, U+061C, tag chars, ZWJ, NUL) |
| M-36 | F0 | `--show-text` activo por defecto | `test_cs28_*` (C1) |
| M-37 | F0 | imprimir un texto rechazado por `filtro_salida` | `test_cs28_*` (C4) |
| M-38 | F0 | el smoke repite `json.dumps` + `protected_tokens` | `test_cs25_b_*` (grep) |
| M-39 | F2 | `_bar` sin mínimo de 1 bloque | `test_ca88_*` (1000:1) |
| M-40 | F2 | `_bar` con `round()` en vez de half-up | `test_ca86_*` caso `(5, 24, 12)` |
| M-41 | F2 | `_bar` dibuja barra con `valor <= 0` | `test_ca88_*` |
| M-42 | F2 | `_build_grafico_*` acepta `bool` o no filtra `nan`/`inf` | `test_cs27_*` (c) |
| M-43 | F2 | gráfico dentro de `datos_del_contexto` | `test_cs27_*` (f) |
| M-44 | F2 | `parse_mode` en el envío con gráfico | `test_ca84_*` |
| M-45 | F2 | truncar el gráfico en vez de devolver `None` | `test_cs27_*` (d) y caso `1e300` |

## 17.8 Medición de calidad en el smoke (CA-S8 y CA-S9)
**Lo que sí se automatiza (CA-S8, MANUAL-LOCAL con key de pruebas).**
- El script se versiona una sola vez en F0 y mide con **regex propias sobre el texto crudo** (sin importar `_normalize_layout` ni `SECTION_LABELS`). Así el MISMO script mide el baseline (prompts viejos) y el nuevo.
- Métricas por código y agregadas: aceptación; razones de rechazo (`guard_numeros`, `guard_escala`, `largo`, `filtro_salida`, `MAX_TOKENS`); longitud p50/p95 del texto del modelo; conformidad con las 3 etiquetas (solo aplica al nuevo, el baseline da 0 por definición); restos de Markdown previos al normalizador; fugas de `snake_case`; tokens de pensamiento; recortes.
- La contradicción de dirección en `vf` y `ver` tiene un chequeo heurístico automático (el texto dice "por debajo" con `posicion_vs_precio == "por encima"` y no menciona la otra). Es una alarma, no un veredicto: la revisión manual de CS-33 sigue.
- Salida solo a stdout. El script no escribe archivos (CS-14); el registro lo hace la usuaria con `| tee`. Guardar `modelo`, `served`, fecha, `thinkingLevel` y el SHA de cada corrida.

Reemplazo de los umbrales de CA-S7 (ahora CA-S8):
```
CA-S8 (MANUAL-LOCAL). Dos corridas con la misma muestra: minimal y low. Por corrida: 10 códigos de _EXPLAIN_QUESTIONS x 5 repeticiones = 50 salidas
(no 30). Umbrales sobre el TOTAL de las 50 (con 5 repeticiones por código el 90% no se puede cumplir por código: 4/5 = 80%):
 - aceptación >= 45/50 y no menor que el baseline de F0 con la misma configuración;
 - por código, aceptación >= 4/5 (si algún código queda en 3/5, se informa y decide el architect);
 - 0 MAX_TOKENS;
 - conformidad con las 3 etiquetas >= 45/50;
 - longitud p50 entre 500 y 1500 caracteres del texto normalizado, y p95 <= 1800;
 - se INFORMAN (sin umbral): restos de Markdown previos al normalizador, fugas de snake_case, rechazos por guard_escala y largo, p95 de latencia del rewrite, tokens de pensamiento;
 - "0 restos de Markdown" se verifica en un test AUTO (CA-74), no en el smoke (después del normalizador es siempre 0: tautología).
Con n=50 los umbrales son una alarma de regresión, no una prueba estadística (el intervalo de Wilson de 45/50 es ~[0.79, 0.96]).
```

**Lo que NO se puede automatizar.**
- Claridad, naturalidad, espaciado y utilidad (CA-S9).
- La dirección correcta de la comparación con el precio (la revisión de CS-33 sobre `vf` y `ver`).
- Si `low` mejora la adherencia, y el juicio final de "se lee bien".
- Que Gemini real cumpla estructura y copie los números formateados (lo mide el smoke, pero cada rechazo necesita lectura).

Protocolo de la rúbrica ciega (CA-S9, reemplaza el de la spec):
```
CA-S9 (MANUAL-LOCAL, ciega parcial).
1. Muestra: para 5 códigos fijos (vf, gra, dcf, ren, alz), la PRIMERA salida ACEPTADA por el guard de la repetición 1 en orden de ejecución, una con prompts
   viejos (baseline F0) y una con prompts nuevos, mismo contexto sintético. Regla fija: nunca se elige a mano. Si un código no tiene salida aceptada
   en alguno de los dos lados, se reemplaza por el siguiente código de la lista (ver, rsk) y se anota.
2. Quién arma el orden: QA (este rol), no el implementer ni el architect, para que quien escribió los prompts no conozca la clave. QA genera con
   random.Random(<semilla de 8 dígitos>) la permutación de los 10 textos, los numera 1 a 10 y escribe la clave (qué número es viejo o nuevo) en un archivo
   del scratchpad, fuera del repo. Antes de mostrar los textos publica SOLO el sha256 de ese archivo de clave. QA no abre la clave hasta que la usuaria entregó las notas.
3. Presentación: los 10 textos en el MISMO marco determinístico (header, Dato, Cuenta, Desglose, disclaimer iguales), de uno en uno, sin etiqueta de versión ni
   metadatos (ni modelo, ni longitud).
4. Calificación: la usuaria da 1 a 5 en claridad, naturalidad, espaciado y utilidad para cada uno, y las anota antes de saber la clave.
5. Desenmascarar: QA entrega la clave, verifica el sha256 y calcula los promedios por criterio.
6. Aceptación: promedio de los nuevos >= 4 en cada criterio y >= 1 punto sobre los viejos. Con 5 vs 5 y una sola evaluadora es una decisión de producto,
   no una prueba estadística; se informa el rango de cada criterio.
Limitación declarada: los encabezados con emoji delatan el prompt nuevo; la ceguera es parcial.
```

## 17.9 Preguntas y ambigüedades para el architect
- **A1** `_MAX_RESPUESTA_CHARS`: 1800 está fijo en CA-70, pero 4.1 dice "se calibra por medición". Propongo un solo test que fija 1800 y el resto de los tests lo referencian por nombre, para recalibrar tocando una constante y un test. Además, ¿la guía de 600 a 1200 (Q2) es un objetivo medible o solo orientativo? Hoy CA-S8 pide p50 entre 500 y 1500.
- **A2** `SECTION_LABELS`: ¿en `ai_explain.py` o en `ai_explain_content.py`? Recomiendo `ai_explain.py` (prompts, normalizador y `_fit_respuesta` están ahí y no se crea un import cruzado). Tipo: `dict[str, tuple[str, str, str]]` de claves `"narrativa"` y `"paso_a_paso"` (la spec solo nombra la primera). El prompt se arma una sola vez al importar (CA-41), con `str.replace`, no con `.format` (el prompt tiene llaves en `{S1}`).
- **A3** `_present_payload`: ¿en el handler (propuesta de QB-9) o en `_fetch_explanation`? La segunda obliga a cambiar la firma, el helper `_fetch_kwargs` y 19 llamadas.
- **A4** Contrato de `_fit_respuesta -> None` y del presupuesto (QB-6): `_fit_leaf_message` nuevo vs cambiar el retorno de `_build_leaf_message`.
- **A5** Entre `0 ≤ restante < 400` tras omitir Gráfico y Desglose: ¿se entrega una respuesta cortísima, o conviene `EXPLAIN_UNAVAILABLE_MSG` también ahí? CS-23(c) solo cubre `< 0`. Con las partes fijas máximas reales (~3400 caracteres) no se llega, pero un `Dato` más largo sí podría.
- **A6** La línea "(explicación acortada…)": ¿entra en el presupuesto de `restante`? Propongo que sí (QA la cuenta dentro de 4096, no dentro de 1800).
- **A7** Redondeo de `_bar`: half-up explícito (propuesta) o `round()` de Python.
- **A8** Nombres de tests parametrizados: `test_ca77_*[codigo]` sobre los 25 códigos con modelo (no 27, para no sumar skips). Los sub-criterios con guion bajo (`test_cs23_a_…`).
- **A9** `_safe_display`: ¿en `ai_explain.py` (el log de producción también lo usa, M-6) y el smoke lo importa? Es lo que asume el checklist de security 16.7-1.
- **A10** `_compute_datos_tokens(crudo, presentado=None)` con `None` equivalente a HEAD: necesario para que F0 y el baseline usen la misma función. Si el architect prefiere firma de dos argumentos obligatorios, F0 debe pasar `presentado=crudo`.
- **A11** Smoke `--thinking`: ¿argumento (propuesta; `test_cs12_construye_la_config…` exige solo 2 apariciones de `environ`) o variable del mapping de entorno armado a mano?
- **A12** Header de la spec: la línea de HEAD (`957f11d`) está desactualizada; hoy es `502d5df`. Conviene fijar el SHA base de cada fase en el handoff.

## 17.10 Tamaño esperado del delta de tests (N0 → N1)
Los "~35 nuevos, ~12 modificados" de la spec son funciones, no nodos de pytest. Hay varios criterios por código (CA-77, CS-25c, CS-26, CS-32) y tablas parametrizadas.

| Fase | Nodos nuevos esperados | Detalle |
|---|---|---|
| F0 | +22 a +35 | `_safe_display` (~14), `--show-text` C1 a C8 (~9), `_compute_datos_tokens` y grep (~4), traceability (0, solo constantes) |
| F1 | +120 a +290 | prompts + CS-35 (~8 si se parametriza por palabra, hasta ~26), normalizador (~20), fit y entrega (~24), payload por código (25 × 4 criterios = 100 si se parametriza; ~8 si no), CA-78/CS-33 (~10), CA-79 (~6), CS-31 (~10), resto (~45) |
| F2 | +30 a +45 | CA-86 a CA-88, CS-27, golden y CA-84 con gráfico |
| Total | **+170 a +370 → N1 entre 2703 y 2903** | passed = N1 − 4 skips |

Conteos sospechosos:
- **N1 − N0 < +120:** casi seguro faltan los tests por código (CA-77, CS-25c, CS-26, CS-32) o están en un solo bucle. Un bucle oculta cuál código falla y no mide cobertura por código.
- **N1 − N0 > +450:** producto cartesiano de parámetros (códigos × palabras × prompts) o duplicación.
- **N1 < N0:** hay tests borrados, bloqueante. Los únicos ids que pueden desaparecer son los renombrados de 17.3 (9 más los de `ca67` y `ca11` si cambia el id), y deben reaparecer con nombre nuevo.
- **Skips > 4:** solo se aceptan skips nuevos de `evt` e `inf` justificados por escrito. Los nuevos tests por código usan los 25 códigos con llamada al modelo.

## 17.11 Criterio de exit de QA (Momento 2)
- Suite verde (N1 reportado), G1 a G9 completas en F0, F1 y F2, 45 mutaciones muertas (M-01 a M-45) o equivalentes documentados.
- Sin tests ignorados o comentados. Flaky = 0.
- CA-S8 y CA-S9 documentados (manuales) con los archivos de la usuaria; no hay "APROBADO" de redacción sin ellos.
- Defectos abiertos: ninguno de severidad ALTA o superior.

## Archivos de apoyo (scratchpad, fuera del repo)
- Oráculo del normalizador corregido: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/norm2.py`
- Contraejemplos del normalizador de 16.5-A (reconstrucción de QA): `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/norm.py`
- Comparación de multiconjuntos de aserciones: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/assert_multiset_diff.py`
- Cobertura de líneas nuevas: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/cov_new_lines.py`
- Plugin de orden inverso: `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/reverse_order.py`

Archivos del repo relevantes:
- `/Users/danielavergara/Documents/Personal/InvestBot/contexto/specs/abiertas/SDD_redaccion_gemini_iter2.md`
- `/Users/danielavergara/Documents/Personal/InvestBot/src/investbot/ai_explain.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/scripts/gemini_smoke.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/tests/test_ai_explain.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/tests/test_traceability.py`
- `/Users/danielavergara/Documents/Personal/InvestBot/tests/test_gemini_smoke_guards.py`

Nota operativa: en el repo solo borré `.coverage`, que generó mi propia corrida de cobertura. No edité nada más ni hice commits.

---

# Sección 18 — Spec Patch Iter-2.1 (architect)  [PREVALECE: 18 > 17 > 16 > 1–15 de este patch > spec base]

Rol `architect`, 2026-11-12. Cuenta como Iter-2.1 (no Iter-3). Base: HEAD `502d5df`, working tree limpio salvo esta spec. Los 9 bloqueantes de QA (QB-1..QB-9) se ACEPTAN; ninguno cambia el diseño de fondo ni las decisiones de producto (números `$1,234.56`; template de 3 secciones con emoji, sin negrita; barras Unicode primero, Pillow solo con spec aparte; el rewrite del primer mensaje NO cambia). **Con esta sección la spec queda CONGELADA (SCOPE FREEZE).**

## 18.0 Precedencia y lectura
1. Orden: 18 > 17 > 16 > 1–15 de este patch > spec base (`SDD_migracion_gemini.md`: sec. 15 > 14 > … > 1–11).
2. Un "reemplazo" de la sección 17 vale tal cual, salvo donde 18 lo corrige.
3. Renombre de ids (QB-7): en las secciones 1–16 léase **CA-S7 = CA-S8** (medición del smoke) y **CA-S8 = CA-S9** (rúbrica humana). El **CA-S7 de la spec base** (guardas del smoke, AUTO, `test_ca_s7_*`) NO se toca.
4. Quedan DEROGADOS donde contradigan a 17/18: 2.4 (algoritmo del normalizador), 4.1 pasos 2–6 de `_fit_respuesta`, 5.3 punto 2 (negativos) y punto 5 (hojas sin clasificar), 8 (CA-72, CA-74, CA-77, CA-82, CA-S7/S8) y la tabla de tests de 4.2 (vale 17.3).
5. Criterios de la spec base que cambian: CA-11 → CA-80; CA-67 → CA-76; la prueba de 480 de CS-5 → CS-24(b); CA-S1 → "10 preguntas"; CQ-7 recibe la excepción de 18.8.

## 18.1 Decisión por bloqueante
| QB | Decisión |
|---|---|
| QB-1 | Problema y ORDEN de QA aceptados; su implementación con regex RECHAZADA. Vale 18.2 (línea a línea, sin `\s`). Defectos del oráculo de QA reproducidos: `(?m)[ \t]+$` es cuadrático (110 ms con 6000 espacios, 1.75 s con 24 000) y `.strip()` quita NBSP/U+2028 dejando al descubierto un `#` o `-` (no idempotente). |
| QB-2 | Aceptado con ajustes (18.3): `_money(-5.0)` da `$-5.00`, no `-$5.00`. |
| QB-3 | Aceptado con ajuste (18.3: tope 1e15; `round(1e300,4)` imprime `1e+300`). |
| QB-4 | Aceptado tal cual (control positivo con `_legacy_cut` local, sin usar el nombre prohibido por CA-70). |
| QB-5 | Aceptado con dos precisiones (18.4: resultado `""` → `None`; párrafos del texto de prueba terminan en `[.!?]`). |
| QB-6 | Aceptado y ratificado (A4/A5, 18.4): `_build_leaf_message` queda concatenación pura; `_fit_leaf_message` hace presupuesto, omisiones, recorte y nota. |
| QB-7 | Aceptado (18.0 regla 3). |
| QB-8 | Aceptado: el smoke pasa a 10 códigos (faltan `vf` y `alz`); con defaults 10×5 + 10×3 = 80 llamadas por corrida; el tope duro de 150 se mantiene. |
| QB-9 | Aceptado y ratificado (A3): `_present_payload`, campos derivados y `_compute_datos_tokens` viven en `_dispatch_leaf`; `_fetch_explanation` mantiene firma; `_payload_texto_libre`/`_payload_avanzado` intactos. |

## 18.2 `_normalize_layout` FINAL (reemplaza 2.4, 16.5-A y el reemplazo de QA)
Pura, idempotente en UNA pasada, lineal, sin `\s`; único regex: `\n{3,}`. **Oráculo ejecutable (fuente de verdad de los fixtures): `/private/tmp/claude-501/-Users-danielavergara/31cd9213-5907-4d6b-a8ac-b976155114f9/scratchpad/arq_norm_final.py`** (el implementer copia de ahí los casos fijos con caracteres especiales).
```
_normalize_layout(texto) -> str. Orden fijo:
(1) "\r\n" y "\r" -> "\n".
(2) borrar los CARACTERES '*', '_' y '`' y los emojis del marco determinístico
    U+1F916 U+1F4CC U+1F9EE U+1F50D U+1F4D0 U+1F4CA U+1F4CB (str.translate). NO se borra U+1F50E (etiqueta '🔎').
(3) por cada línea (split("\n")):
    (3a) s = línea.lstrip(" \t"). Si s empieza con '#': s = s.lstrip("# \t") y línea = s.
    (3b) EN SECUENCIA (no elif): si s[:1] in ("-","–") y len(s)>1 y s[1] in " \t": línea = "• " + s[1:].lstrip(" \t").
    (3c) línea = línea.rstrip(" \t").
    (3d) si ya hay líneas, la anterior no es "" y línea.startswith(tuple de TODAS las etiquetas de SECTION_LABELS): insertar una línea "" antes.
(4) unir con "\n"; re.sub(r"\n{3,}", "\n\n", ...); .strip(" \t\n")  (NUNCA .strip() a secas).
```
Solo borra caracteres, une líneas o agrega '•', ' ' y '\n'. Puede crear coincidencias del filtro; los guards corren SIEMPRE sobre su salida (CS-24a).
**Propiedades:** P1 idempotencia; P2 sin `**`, `__` ni backtick; P3 `set(salida) ⊆ set(entrada) ∪ {'•','\n',' '}`; P4 sin `\r` ni 3 `\n` seguidos; P5 ninguna línea cumple `re.match(r"[ \t]*(#|[-–][ \t])", línea)`.
**Entradas del test:** (i) enumeración exhaustiva sobre `['*','_','`','#','-',' ','\n','📌','x',' ']` con largo ≤ 5 (≈111 mil casos); (ii) contraejemplos fijos: `'*_*_`_*_*'`→`''`; `'# # # # - x'`→`'• x'`; `'#- #'`→`'• #'`; `' - x'` sin cambios; `'- \n'`→`'•'`; `'a\n💡 En simple\nb'`→`'a\n\n💡 En simple\nb'` (y los demás del oráculo); (iii) 20 000 cadenas con `random.Random(1234)` sobre átomos que incluyan `* _ ` # - – • \t \r \n`, espacio, los 7 emojis del marco, las 5 etiquetas, ZWSP, `@`, `](`, `http://`, `1`, `$`, `.`, `%` y OBLIGATORIAMENTE los espacios/saltos Unicode `\x0b \x0c \x1c \x85 U+2028 U+3000 NBSP`. Un fuzz sin esos átomos no cuenta.
**CS-34 (reemplaza a QA):** `min` de 3 mediciones < 50 ms con `"\n"*6000`, `" \n"*3000`, `" "*6000`, `"a"+" "*6000+"x"`, `"a"+" \t"*3000+"x"` y `"# "*3000+"x"`; test de escala: la medición con 24 000 caracteres es < 8× la de 6 000 (lineal ≈ 4×, cuadrático ≈ 16×); test por `inspect.getsource`: el cuerpo de `_normalize_layout` no contiene `\s`, `(?m)` ni `$` dentro de patrones, y el único `re` es el de `\n{3,}`.

## 18.3 Payload presentado
`_money_compact(x)` FINAL (reemplaza 5.3 punto 2):
```
a = abs(x)
si a >= 1e9, o round(a/1e6, 1) >= 1000.0:  f"${x/1e9:,.1f} mil millones"   # 394328000000.0 -> $394.3 mil millones
si no, si a >= 1e6:                         f"${x/1e6:,.1f} millones"
si no:                                      _money(x)                        # = f"${x:,.2f}"
```
El signo va como ya lo pone el bot (`_money(-5.0) == "$-5.00"`), NO `-$5.00`. La promoción evita `$1,000.0 millones` (999 960 000 → `$1.0 mil millones`). `1.5e12` → `$1,500.0 mil millones` (`_normalize_numeric_token("1,500.0")` = `1500.0`, verificado). Nunca "billón".
`_present_payload` (enmienda de 5.3 puntos 1, 5, 6): solo se transforman hojas `type(v) in (int, float)`; `bool`, `str`, `None` no se tocan; se recorren dict y list anidados (la clave de la hoja es la del dict padre más cercano). **Regla numérica de seguridad:** toda hoja int/float con `not math.isfinite(v)` o `abs(v) >= 1e15` pasa a `None` (clasificada o no); un int NO clasificado < 1e15 queda tal cual. Float sin clasificar: `round(v, 4)`; ninguna hoja queda en notación científica. `_PAYLOAD_KIND` por clave; si hay colisión de unidad entre preguntas, esa clave pasa a `(question_code, clave)`. Los campos derivados NO los agrega `_present_payload`: los agrega el handler con el helper puro `_derivar_posicion_vs_precio(valor_justo, precio) -> dict` (devuelve `{}` o las dos claves de CS-33), desde valores CRUDOS, sobre la COPIA presentada, solo en `vf`/`ver`; ambos campos entran a `datos_modelo` antes de `_compute_datos_tokens`.
**CS-32 vigente:** cada hoja money es EXACTAMENTE `_money_compact(valor_crudo)` (para |v|<1e6 eso es `_money(v)`, igual a `_fmt_money_or_nd` en positivos y negativos); para |v|≥1e6 el token parseado de vuelta (×1e6 o ×1e9 según el sufijo) difiere del crudo en ≤ 0.05 de la unidad mostrada (+1e-9 relativo); cada hoja pct es `_pct1(crudo)`; cada hoja ratio es `f"{crudo:.2f}"`; resto de CS-32 (colisión de unidades, `bool`/`str`/`None` intactos, NaN/inf → `None`) sin cambio.
**CA-77 vigente:** (a) toda hoja money/pct/ratio del payload presentado es `str`; (b) ninguna hoja float sin clasificar tiene más de 4 decimales ni notación científica: `re.search(r"[eE][+-]?\d", json.dumps(p)) is None`, incluyendo entradas `1e300` y `1.5e-05`; (c) monotonía: `_compute_datos_tokens(crudo, presentado)` contiene los tokens calculados con la expresión de HEAD copiada literal en el test (`json.dumps(crudo, ensure_ascii=False, default=str)` + `protected_tokens` + `_normalize_numeric_token`); (d) CS-32. Parametrización: `_PRESENTABLE_CODES = [c for c in _ALL_CODES if c not in ("evt","inf")]` (25 ids).
**`_compute_datos_tokens(crudo, presentado=None)`** (A10): con `None` equivale exactamente a HEAD (`ensure_ascii=False, default=str`, sin redondear); con `presentado`, la unión de 5.4. Un único cuerpo, usado por el handler y por el smoke.

## 18.4 Recorte y entrega
Constantes en `ai_explain.py`: `_MAX_RESPUESTA_CHARS=1800`, `_MIN_RESPUESTA_CHARS=400`, `_MAX_RAW_CHARS=6000` (en `len(str)`), `_TELEGRAM_LIMIT=4096` (propia; un test afirma que es igual a `query_handler.TELEGRAM_MESSAGE_LIMIT` sin importarla en el módulo), `_MARGEN_TELEGRAM=100`, `_NOTA_ACORTADA="(explicación acortada por el largo del mensaje)"`. `len16(s) = len(s.encode("utf-16-le")) // 2`.
`_fit_respuesta(texto, max_chars) -> Optional[str]` FINAL:
```
1. Si len16(texto) <= max_chars: devolver texto.
2. piso = 0.4 * max_chars. Candidatos con len16(candidato) en [piso, max_chars]:
   (a) texto[:i] con i = el ÚLTIMO índice de "\n\n" que cumpla; si no hay,
   (b) texto[:j+1] con j = el ÚLTIMO índice de [.!?] seguido de espacio o fin de texto que cumpla
       (el "." de un decimal va seguido de dígito y no cuenta); si no hay,
   (c) devolver None. NUNCA cortar en un espacio ni agregar "…".
3. res = candidato.rstrip(). Mientras la ÚLTIMA LÍNEA de res (strip) sea exactamente una etiqueta de SECTION_LABELS: quitarla y rstrip.
4. Si res == "": devolver None.
5. logger.info("GEMINI_RESPUESTA_RECORTADA chars=%d max=%d", len16(texto), max_chars)  (solo enteros).
6. Devolver res.
```
Prioridad: párrafo completo antes que oración; la condición de 0.4 aplica a ambos.
`_fit_leaf_message(dato_line, respuesta, formula, fuente, *, cuenta=None, desglose=None, grafico=None) -> Optional[str]` FINAL:
```
restante(g, d) = _TELEGRAM_LIMIT - _MARGEN_TELEGRAM - len16(_build_leaf_message(dato_line, "", formula, fuente, cuenta=cuenta, desglose=d, grafico=g))
g, d = grafico, desglose; r = restante(g, d)
si r < _MIN_RESPUESTA_CHARS y g: g = None; r = restante(g, d)
si r < _MIN_RESPUESTA_CHARS y d: d = None; r = restante(g, d)
si r < 0: devolver None                                        # CS-23(c)
si len16(respuesta) <= min(_MAX_RESPUESTA_CHARS, r): cuerpo = respuesta
sino:
    si r < _MIN_RESPUESTA_CHARS: devolver None                 # A5
    res = _fit_respuesta(respuesta, min(_MAX_RESPUESTA_CHARS, r - len16("\n\n" + _NOTA_ACORTADA)))
    si res is None: devolver None
    cuerpo = res + "\n\n" + _NOTA_ACORTADA                     # DESPUÉS de los guards (CS-23e); no cuenta contra 1800, sí contra 4096
msg = _build_leaf_message(..., cuerpo, ..., desglose=d, grafico=g)
si len16(msg) > _TELEGRAM_LIMIT: devolver None                 # defensa final
devolver msg
```
`_build_leaf_message` NO cambia de comportamiento: solo suma `grafico: Optional[str] = None` (entre `Cuenta` y `Desglose`). El handler llama `_fit_leaf_message`; si devuelve `None`, hace `edit_message_text(EXPLAIN_UNAVAILABLE_MSG)` igual que el camino `_ExplainUnavailable`, y registra `logger.info("GEMINI_ENTREGA_OMITIDA motivo=%s chars=%d", motivo, n)` con `motivo` ∈ {`presupuesto`, `sin_corte`} y solo enteros. Si la respuesta normalizada queda vacía, `_fetch_explanation` lanza `_ExplainUnavailable` con `guard=vacio` (log solo de contadores).
**CA-72 vigente:** texto de prueba donde todo párrafo y toda oración termina en `[.!?]`; para cada `max_chars` en `range(50, len16(texto)+1)`: `resultado is None` o (`len16(resultado) <= max_chars`, `texto.startswith(resultado)`, `resultado != ""`, `Counter(protected_tokens(resultado)) <= Counter(protected_tokens(texto))`, y `max_chars >= len16(texto)` o `resultado[-1]` no es dígito, `$` ni `%`). Control positivo `_legacy_cut` y emojis fuera del BMP.
**CA-73 vigente (escenarios):** (A) restante ≥ 1800, sin recorte; (B) 400 ≤ restante < 1800, con recorte; (C) restante < 400, se omite SOLO el Gráfico; (D) tras omitir el Gráfico sigue < 400, se omite también el Desglose; (E) tras omitir ambos restante < 0 → `None`; **(F)** tras omitir ambos, 0 ≤ restante < 400: una respuesta que entra completa SÍ se entrega; una que exige recorte devuelve `None`. En TODOS: `len16(msg) <= 4096` y `Dato`, `Cuenta`, respuesta y disclaimer presentes (salvo `None`). C a F se arman con `formula` sintética larga.
**CS-23:** (b) en UTF-16; (d) el log de recorte cumple `re.fullmatch(r"GEMINI_RESPUESTA_RECORTADA chars=\d+ max=\d+", msg)`; (e) usa `_NOTA_ACORTADA`, cuenta dentro de 4096 y fuera de 1800.

## 18.5 `_escalas_consistentes` (CS-31) FINAL
Se agrega `mil` como último sufijo (cierra `$5.00 mil` cuando el payload trae `$5.00`).
```
_ESCALA_RE = re.compile(r"(\$?\d[\d.,]*)(?:\s*(mil millones|miles de millones|millones|billones|trillones|mil)\b)?", re.IGNORECASE)
pares(s) = { (_normalize_numeric_token(num), sufijo.lower() | None) por cada coincidencia }
P = pares(json del payload presentado); R = pares(respuesta)
Regla 1: todo (n, s) de R con s != None debe estar en P.
Regla 2: todo (n, None) de R tal que P contenga (n, s) con s != None y NO contenga (n, None) -> rechazo.
Rechazo = guard=escala. _normalize_numeric_token y _no_new_protected_tokens sin cambios.
```
Con payload `$394.3 mil millones` y `15.3%`: aceptada `$394.3 mil millones`; rechazadas `$394.3 millones`, `$394.3` solo, `394.3 billones`, `$394.3 mil  millones` (dos espacios, fail-closed), `394.3\nmillones`, `$394.3 mil`, `$394.3 miles de millones`. Con payload `$5.00`: `$5.00 millones` y `$5.00 mil` rechazadas.

## 18.6 Decisiones finales a 17.9
| # | Decisión |
|---|---|
| A1 | `_MAX_RESPUESTA_CHARS=1800`; un solo test lo fija (`test_ca70_*`), el resto referencia el nombre. "600 a 1200" es orientativo y no va en el prompt; el proxy medible es CA-S8: p50 entre 500 y 1500 y p95 ≤ 1800 sobre el largo del texto crudo. |
| A2 | `SECTION_LABELS` en `ai_explain.py`, `dict[str, tuple[str,str,str]]` con claves `"narrativa"` y `"paso_a_paso"`; el prompt se arma UNA vez al importar con `str.replace` de `{S1}`,`{S2}`,`{S3}` (no `.format`); constante plana `_ALL_LABELS` para el normalizador. |
| A3 | `_present_payload`, campos derivados y `_compute_datos_tokens` en `_dispatch_leaf`; `_fetch_explanation` mantiene su firma. |
| A4 | `_fit_leaf_message` nuevo (18.4). |
| A5 | Con presupuesto < 400 solo se entrega la respuesta si entra completa; si necesita recorte, `EXPLAIN_UNAVAILABLE_MSG`. |
| A6 | La nota entra en el cómputo de 4096 y no en el de 1800. |
| A7 | Half-up explícito: `int(valor / maximo * ancho + 0.5)`, mínimo 1 si `valor > 0`; caso fijo `(5,24,12)` = 3; `_bar` devuelve `str`: la barra o el literal `"n/d"` si `valor` o `maximo` no cumplen CS-27(b). |
| A8 | `_PRESENTABLE_CODES` (25). Ids: `test_ca77_payload_presentado[<code>]`, `test_cs25_c_cifra_inventada_rechazada[<code>]`, `test_cs26_a_canarios_ausentes[<code>]`, `test_cs26_b_snapshot_claves_str[<code>]`, `test_cs32_fidelidad_presentado[<code>]`; CS-35 parametrizado por `[prompt-palabra]`; sub-criterios con guion bajo (`test_cs23_a_*`). |
| A9 | `_safe_display(texto, max_chars=2500, keep_newlines=True)` en `ai_explain.py`; el smoke lo importa; el log de rechazo usa `_safe_display(_sanitize_for_log(r), max_chars=500, keep_newlines=False)` (el test `test_cs5_log_del_rechazo_sanitiza_y_trunca_a_500_caracteres` queda sin modificar). |
| A10 | Ver 18.3. |
| A11 | `--thinking {minimal,low}` como argumento, default `None` = no tocar el entorno; se aplica a una COPIA del mapping de entorno que recibe `load_config` (sin nuevas apariciones de `environ`). |
| A12 | SHA base `502d5df`; cada fase registra `git rev-parse HEAD` al arrancar. |

## 18.7 Menores de 17.2: se aceptan TODOS los reemplazos, con estas correcciones
- **CA-82** pasa a AUTO: `test_ca82_*` con `sha256(ai_rewrite.SYSTEM_PROMPT)` igual a un literal capturado de HEAD; la puerta `git diff --quiet $BASE -- …` se conserva además como G6.
- **CA-81** gana `test_ca81_*`: con contexto sintético de `vf` y un spy, `_build_dato_line`, `_build_cuenta_line` y `_build_desglose_block` reciben el payload CRUDO y `_fetch_explanation` recibe el presentado (fija QB-9).
- **CS-25(a):** en lugar de `ast.dump` contra `git show`, el test fija `sha256(inspect.getsource(f))` de `_no_new_protected_tokens`, `_normalize_numeric_token`, `_percent_token_a_decimal`, `ai_rewrite.protected_tokens` y `ai_rewrite._PROTECTED_TOKEN_RE.pattern`; tocar cualquiera exige que `security` actualice el literal.
- Test de URL pasada la posición 1800: el control positivo contempla `None` (`res is None or "http://" not in res`), con un texto con oraciones.
- `_tokens_de` (helper de `test_ai_explain.py` ~l.1080) se cambia en F1, no en F0, para que F0 modifique cero tests existentes.
- **CS-26(c):** `_present_payload` solo convierte hojas numéricas a `str`; `posicion_vs_precio` y `diferencia_vs_precio_abs` las agrega el handler; el snapshot de CS-26(b) las admite solo para `vf` y `ver`.
- El umbral de `_fit_respuesta` se escribe `>= 0.4 * max_chars` (no "mitad").

## 18.8 Conjunto FINAL de criterios
Vigentes de la spec base sin cambio: todos los CA/CS/CQ de `SDD_migracion_gemini.md` salvo los redefinidos. CA-S1 → "10 preguntas". Excepción a CQ-7: los 3 tests de `_enforce_brevity` (5 aserciones) se renombran a los 3 `test_fit_respuesta_*` de 17.3 y conservan 5 aserciones en total; el manifiesto `M_BREV` se renombra y mantiene suma ≥ 5.
| Id | Fase | Texto vigente |
|---|---|---|
| CA-70 | F1 | 17.2 (recorrido con `re` armado por concatenación sobre `*.py` de `src/`, `scripts/`, `tests/`, no `contexto/`; control positivo; constantes 1800/400/6000) |
| CA-71 | F1 | 17.2 |
| CA-72 | F1 | 18.4 |
| CA-73 | F1 | 18.4 (A–F) |
| CA-74 | F1 | 18.2 (P1–P5, exhaustiva, fijos, fuzz con átomos Unicode) |
| CA-75 | F1 | 17.2 (el spy ve el MISMO string en los cuatro puntos) |
| CA-76 | F1 | 8 + 17.2 (i)–(iii) |
| CA-77 | F1 | 18.3 |
| CA-78 | F1 | tabla de CS-33 + 17.2 (sobre valor redondeado) |
| CA-79 | F1 | 8 + 17.2 (f), (g) |
| CA-80 | F1 | 8 |
| CA-81 | F1 | 8 + 18.7 |
| CA-82 | F1 | 18.7 |
| CA-83 | F1 | 17.2 (tests propios `test_ca83_*` para `largo`, `escala`, `vacio`) |
| CA-84 | F1/F2 | 17.2 (variante con Gráfico en F2) |
| CA-85 | F1 | 17.2 (golden sin Gráfico; con Gráfico en F2) |
| CA-86 | F2 | 8 + 17.2 (línea `Valor Justo ████████████ $496.00`; half-up; `(5,24,12)` = 3) |
| CA-87 | F2 | 8 |
| CA-88 | F2 | 8 + 16.5-I + 17.2 (`1e300` = `None`; `maximo=0` sobre `_bar` = `"n/d"`; un solo valor = `None`) |
| CA-S8 | manual | 17.8 (50 salidas por corrida) |
| CA-S9 | manual | 17.8 (rúbrica ciega) |
| CS-23 | F1 | 16.2 + 17.2 + 18.4 |
| CS-24 | F1 | 16.2 + 17.2 (el pipeline incluye `_escalas_consistentes`) |
| CS-25 | F0 (b), F1 (a, c) | 16.2 + 18.7 |
| CS-26 | F1 | 16.2 + 18.7 |
| CS-27 | F2 | 16.2 |
| CS-28 | F0 | 16.4 (C1–C9) |
| CS-29 / CS-30 | F1 | 16.2 |
| CS-31 | F1 | 16.2 + 18.5 |
| CS-32 | F1 | 18.3 |
| CS-33 | F1 | 16.2 + 17.2 |
| CS-34 | F1 | 18.2 |
| CS-35 / CS-36 | F1 | 16.2 + 17.2 (CS-36 con `\U0001F50D` explícito vs `\U0001F50E`) |
**CQ nuevos:** CQ-21 puertas G1–G9 de 17.5 en cada fase. CQ-22 las 45 mutaciones de 17.7 con estas sustituciones y adiciones: M-06 = reemplazar el recorrido por líneas con `re.sub(r'(?m)[ \t]+$','',t)` (lo mata `test_cs34_*`); M-07 = `.strip()` final en lugar de `.strip(' \t\n')` (lo matan los fijos `' - x'` y el del oráculo); M-46 `elif` entre encabezado y viñeta (fijo `#- #`); M-47 borrado de `**`/`__` por pares con `replace` (fijo `*_*_`_*_*`); M-48 `_fit_leaf_message` recorta con presupuesto < 400 (escenario F de CA-73); M-49 `_money_compact` sin promoción (`test_cs32_*` con 999 960 000); M-50 `_present_payload` deja pasar floats ≥ 1e15 o infinitos (`test_ca77_*` b); M-51 `_escalas_consistentes` sin el sufijo `mil` (`test_cs31_*`). Meta: 51 mutaciones muertas o equivalentes documentadas. CQ-23 preservación (17.4) con `TOTAL_NO_PERMITIDOS 0` y allowlist = los renombrados de 17.3. CQ-24 cobertura (17.6). CQ-25 delta de tests: N1 − N0 entre +120 y +450 (N0 = 2533), skips ≤ 4.

## 18.9 Plan de fases (17.5 confirmado, con 3 ajustes)
F0 (smoke `--show-text`, `_safe_display`, `_compute_datos_tokens` compartido, smoke a 10 códigos, `--thinking`) → F1 (núcleo: prompts, `SECTION_LABELS`, `_normalize_layout`, `_fit_respuesta`/`_fit_leaf_message`, `_present_payload`, campos derivados, `_escalas_consistentes`, tokens 2048, tests) → F2 (barras Unicode `vf`/`gra`/`dcf`/`mul`/`ver`) → F3 (cierre técnico/docs). Puertas G1–G9 de 17.5. Ajustes: (1) la métrica comparable baseline/nuevo es "aceptación por los DOS guards de HEAD" (`guard_numeros` + `filtro_salida`); `guard_escala`, `largo` y `vacio` se informan APARTE en F1; (2) el baseline vivo (F0, MANUAL-LOCAL, a cargo de la usuaria con `--show-text`, con `minimal` y `--thinking low`) se corre sobre el árbol de F0 (prompts viejos) ANTES de que F1 cambie los prompts; (3) orden interno de F1: `_normalize_layout` antes de las puertas del pipeline; `_present_payload`/`_compute_datos_tokens` y los campos derivados antes de `_escalas_consistentes`.
**Criterio de exit:** suite verde con N0=2533 y N1 reportados; G1–G9 completas en F0, F1 y F2; 51 mutaciones muertas o documentadas; 0 tests ignorados o flaky; sin defectos ALTA abiertos; CA-S8 y CA-S9 documentados por la usuaria (sin ellos no hay "aprobado de redacción"); diff vacío en `ai_rewrite.py`, `gemini_client.py`, `requirements.txt`, `requirements-dev.txt` y `Dockerfile` (G6).

## 18.10 Decisión humana pendiente (lista cerrada, con default)
1. Rúbrica ciega CA-S9 (califica la usuaria, 1–5 en claridad, naturalidad, espaciado, utilidad; orden armado por QA). Si falla: Iter-3 con las muestras como evidencia.
2. Despliegue: lo autoriza la usuaria. Default: F1 sola primero, F2 después.
3. Corridas en vivo: 4 corridas × 80 llamadas con su key de pruebas (baseline y nuevo, `minimal` y `low`).
4. `GEMINI_THINKING_LEVEL`: cambiar a `low` solo si CA-S8 lo justifica y la latencia p95 del rewrite queda bajo 8 s.
5. Fase 3 (Pillow): spec aparte, solo si sigue queriendo imágenes tras ver las barras.
6. Commit y push: nadie los hace sin su autorización.
7. Q5/Q6: si el primer mensaje también le parece mal redactado (default: no) y 2–3 respuestas reales "malas" (recomendado antes del baseline; no bloquea el código de F0).
8. Texto visible nuevo: la línea fija "(explicación acortada por el largo del mensaje)"; ante respuestas sin corte válido la usuaria verá el mensaje de "no disponible" en lugar de un texto a medias (raro).

## Handoff → implementer
Decisiones de diseño (no reabrir): `_normalize_layout` línea a línea sin `\s` y con `strip(" \t\n")`; pipeline: tope crudo (6000) → normalizar → `_no_new_protected_tokens` → `_escalas_consistentes` → filtro de salida → `_fit_leaf_message`; `_money_compact` con signo como el bot, promoción de unidad y tope 1e15; `_present_payload` y campos derivados en el handler; `_fit_respuesta` devuelve `None` sin corte válido y `_fit_leaf_message` decide entrega o fallback; `_bar` half-up y `"n/d"`; CA-S7→CA-S8, CA-S8→CA-S9; smoke a 10 códigos; hashes de `inspect.getsource` en vez de `ast.dump`.
