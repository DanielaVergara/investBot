# Spec: Mensaje específico para 402 "símbolo premium" de FMP [Iter-1]

**Rol:** `architect` (spec base — fix acotado, no pasa por `frontend` por no haber UI; ver "Restricciones" sobre alcance de `security`/`qa`).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa el criterio de no-logueo de body crudo (sección "Gestión de secretos" ya vigente en `SDD_investbot_mvp.md` aplica por herencia); luego `implementer` ejecuta con Ralph Loop. Alcance ya acotado y aprobado por Daniela — no requiere `frontend` ni nuevas preguntas abiertas de negocio.

> **Revisión de `security` (Iter-1) — completada.** Criterios de seguridad agregados en la sección "Criterios de seguridad (agregado por `security`)" más abajo, sin modificar ninguna sección preexistente. Lista para `implementer`.

---

## Contexto

FMP devuelve HTTP 402 en dos situaciones distintas que hoy colapsan en el mismo mensaje genérico:

1. **402 a nivel de endpoint completo** (ya conocido y evitado, no "manejado con mensaje"): `/key-metrics-ttm` es premium para toda cuenta gratuita, cualquier ticker. Se resolvió en `peers.py` **evitando llamar ese endpoint** (usa `/key-metrics` anual + `earningsYield` en su lugar) — no hay lógica de mensaje para este caso porque el bot nunca lo dispara.
2. **402 a nivel de símbolo específico dentro de un endpoint mayormente gratuito** (caso nuevo, confirmado con `curl` real contra `/quote` para `MELI` y `DRAM`; `AMZN` en el mismo endpoint responde 200 sin problema). El body real de FMP para este caso:

   ```
   Premium Query Parameter: 'Special Endpoint : This value set for 'symbol' is not available under your current subscription please visit our subscription page to upgrade your plan at https://financialmodelingprep.com/
   ```

Hoy este segundo caso cae en la rama genérica de `_get` y el usuario de Telegram recibe: *"FMP respondió con un error (402). Intenta más tarde."* — mensaje engañoso, porque sugiere un problema transitorio (como el 429 de cupo) cuando en realidad ese ticker puntual nunca va a funcionar en el plan gratuito, sin importar cuánto se reintente.

**Decisión de alcance ya tomada con Daniela:** este fix es solo de **mensaje de error**. No se investiga qué otros símbolos están afectados, no se cambia el manejo de ningún otro status code, y no se toca el flujo de resolución de tickers (`query_handler.py`, `fmp_client.search_company`).

---

## Estado actual

`src/investbot/fmp_client.py`, función `_get` (líneas 41-80):

- Captura `httpx.HTTPStatusError` y distingue por `status_code`: 429 → mensaje de cupo diario; 401/403 → mensaje de key inválida/vencida; cualquier otro status (incluido 402 en cualquiera de sus dos variantes) → mensaje genérico `f"FMP respondió con un error ({status}). Intenta más tarde."`.
- La rama de excepción **nunca lee `exc.response.text`/`.json()`** — solo usa `status_code`. No hay precedente de inspección de body para 402 en el código actual (el único lugar donde se distingue por body es, según el contexto del proyecto, la lógica de `peers.py`, que evita el endpoint en vez de inspeccionar su respuesta).
- El body de error de FMP nunca se loguea (`logger.warning` solo incluye `endpoint_label` y `status`), consistente con el criterio de `security` de `SDD_investbot_mvp.md` sección 2 ("Ningún log persistente contiene la URL completa con query string... el wrapper HTTP loguea únicamente endpoint/ticker/status code").
- `query_handler.py` (líneas ~254-255, ~302-303) captura `fmp_client.FMPError` y hace `reply_text(str(exc))` — es decir, el texto del mensaje de `FMPError` se muestra **literal** al usuario de Telegram. Esto no cambia.

---

## Estado objetivo

`_get` distingue, **solo para status 402**, entre dos sub-casos usando el texto del body de la respuesta (no solo el status code, siguiendo el mismo estilo ya usado en el proyecto para diferenciar sub-casos de un mismo status):

1. **Sub-caso "símbolo premium"** (nuevo): el body contiene el patrón textual confirmado (`"Special Endpoint"` + `"symbol"`, case-insensitive — ver "Decisión de diseño" abajo). El bot responde con un mensaje que:
   - Deja explícito que es **ese ticker puntual** el que no está disponible en el plan gratuito de FMP (usando el valor de `params.get("symbol")` si está presente — nunca el diccionario completo de params).
   - Aclara que **no** es un problema de cupo diario ni de la API key (para no confundirlo con los mensajes de 429/401/403 ya existentes).
   - Sugiere probar con otro ticker.
2. **Sub-caso genérico** (comportamiento sin cambios): cualquier 402 cuyo body no matchee el patrón anterior (incluye el caso hipotético de que `/key-metrics-ttm` u otro endpoint devuelva 402 con un body distinto) cae en el mensaje genérico actual, sin modificación.

La lectura del body para hacer el match ocurre **en memoria**, nunca se loguea el body crudo (ver "Restricciones").

---

## Decisión de diseño: cómo distinguir el sub-caso

- Se agrega una función pura `_is_symbol_premium_402(body_text: str) -> bool` en `fmp_client.py` que evalúa, case-insensitive, si `body_text` contiene tanto `"special endpoint"` como `"symbol"`. Ambos substrings están presentes literalmente en el body real confirmado con `curl` para MELI/DRAM.
- Se elige un match **conservador** (dos substrings específicos, no una regex amplia tipo `"premium"` sola) a propósito: evita falsos positivos sobre otros 402 no confirmados (p. ej. si algún endpoint completo devolviera un body con la palabra "premium" en otro contexto), consistente con la restricción de no investigar el alcance completo — **solo se trata el caso confirmado**, cualquier otro 402 no confirmado sigue cayendo en el mensaje genérico (comportamiento seguro por omisión).
- La lectura del body ocurre con `exc.response.text`, envuelta en `try/except` — si por algún motivo no se puede leer (body vacío, encoding raro), se trata como "no matchea" y cae al mensaje genérico. Nunca debe poder lanzar una excepción no capturada.
- **Sobre la posible presencia de la API key en el body de error de FMP:** no se puede garantizar, con la evidencia disponible, que ningún body de error de FMP refleje la key en ningún escenario. Por diseño conservador, el body **nunca se loguea crudo**, sin importar el resultado del match — solo se loguea un indicador booleano/label (`subcaso=simbolo_premium` o `subcaso=generico`) junto al `endpoint_label` y `status` ya existentes.

---

## Criterios de aceptación

- [ ] Un test con `httpx.MockTransport` que responde `402` con el body real confirmado (el texto exacto de MELI/DRAM del contexto de esta spec) sobre `/quote` produce un `FMPError` cuyo mensaje: (a) menciona explícitamente que el ticker consultado no está disponible en el **plan gratuito** de FMP, (b) no es el string genérico `"FMP respondió con un error (402). Intenta más tarde."`, (c) no menciona cupo/límite diario ni la API key, (d) sugiere probar con otro ticker.
- [ ] Un test con `httpx.MockTransport` que responde `402` con un body distinto (p. ej. `{"error": "Payment Required"}` o vacío) produce el mensaje genérico actual sin cambios — regresión explícita contra el comportamiento existente para 402 no confirmados.
- [ ] Los tests existentes de `tests/test_fmp_client.py` (429, 401/403, 5xx genérico, timeout, connect error, respuesta no-JSON, y los de `search_company`/`get_quote`/etc.) siguen pasando sin modificación.
- [ ] Un test (mismo patrón que `test_error_401_no_filtra_api_key_en_logs`) que verifica que, ante el 402 "símbolo premium", **ningún log** (`caplog`) contiene el body crudo de la respuesta ni la API key — solo `endpoint_label`, `status` y el label del sub-caso.
- [ ] Un test unitario aislado de `_is_symbol_premium_402` (función pura) cubre: body real confirmado → `True`; body genérico sin esos substrings → `False`; body vacío/`None`-safe → `False`; variación de mayúsculas/minúsculas → `True`.
- [ ] El mensaje al usuario, si incluye el ticker, lo obtiene únicamente de `params.get("symbol")` — un test verifica que ningún otro campo de `params` (en particular `apikey`) aparece en el mensaje resultante.
- [ ] `peers.py` y el comportamiento de evitar `/key-metrics-ttm` quedan sin ninguna modificación (verificado por inspección — no se toca el archivo).
- [ ] `query_handler.py` queda sin ninguna modificación (verificado por inspección — no se toca el archivo; el mensaje nuevo llega al usuario exactamente igual que hoy, vía `str(exc)` de `FMPError`).

---

## Criterios de seguridad (agregado por `security`, Iter-1)

Contexto de la evaluación: este es el primer punto del código donde se lee el
**body** de una respuesta de error de FMP (hasta ahora `_get` solo usaba
`status_code`). El riesgo neto es **Bajo** (no hay indicio de que FMP refleje
la API key en el body de un 402 — no está en `params` de query string sino en
`apikey`, y el body confirmado con MELI/DRAM no la contiene), pero se agregan
los siguientes criterios para cerrar dos superficies que la spec base no
cubre explícitamente: (a) el logueo del body ocurre potencialmente en **toda**
rama de 402, no solo en la de "símbolo premium"; (b) un fallo de lectura del
body o un cambio de wording de FMP no debe degradar en un "genérico" que se
vea idéntico en logs a un 402 realmente no reconocido — eso sería un fallo
silencioso que dificulta el diagnóstico futuro (CWE-778, insufficient logging).

- [ ] **Body nunca logueado en ninguna rama de 402, no solo en la de "símbolo premium".**
  El test ya listado (`caplog` para el sub-caso símbolo premium) se extiende
  con un test equivalente para el sub-caso **genérico** (body `{"error": "Payment Required"}`
  y body vacío): en ningún caso `caplog.text` contiene el body crudo ni la API key.
  Justificación: el body se lee (para poder decidir si matchea) en ambas
  ramas por igual, así que ambas deben probarse, no solo la que matchea.
- [ ] **Lectura del body con excepción acotada, no `except Exception` genérico.**
  El `try/except` alrededor de `exc.response.text` captura explícitamente los
  tipos esperables (p. ej. `UnicodeDecodeError`/`RuntimeError` si `httpx`
  no puede decodificar) — no un `except Exception` de catch-all que
  enmascare bugs propios de `_is_symbol_premium_402` u otro error inesperado.
- [ ] **Label de log distingue "body no matchea" de "body no se pudo leer".**
  Cuando falla la lectura/decodificación del body, el log usa un label
  distinto (p. ej. `subcaso=cuerpo_no_legible`) del que usa un 402 genérico
  cuyo body sí se leyó pero no matcheó (`subcaso=generico`). Ninguno de los
  dos labels incluye contenido del body — solo el label. Esto da visibilidad
  operativa si FMP cambia el formato de error o empieza a devolver bodies
  no-UTF8, sin necesitar loguear contenido sensible. Un test cubre este caso
  simulando que `exc.response.text` lanza, y verifica: (a) no propaga la
  excepción sin capturar, (b) el mensaje al usuario cae en el genérico
  existente, (c) el log usa el label de "no legible", no el de "genérico".
- [ ] **Cota de tamaño antes de matchear.** `_is_symbol_premium_402` opera
  sobre como máximo los primeros ~2000 caracteres del body (truncado antes
  de `.lower()`/`in`), para no depender de que un body anómalamente grande
  (proxy mal configurado, respuesta no esperada) genere trabajo o memoria
  desproporcionados en cada 402. No cambia el resultado para el body real
  confirmado (mucho más corto que el límite).
- [ ] **Ningún dato de `params` distinto de `symbol` llega al mensaje de usuario ni a un log**,
  incluyendo si en el futuro se agregara otro campo a `params` — el test ya
  listado en "Criterios de aceptación" para `apikey` se interpreta como
  representativo de "cualquier clave de `params` que no sea `symbol`", no
  solo `apikey` literal.

---

## Criterios QA para Spec: Mensaje específico para 402 "símbolo premium" de FMP [Iter-1]

> Revisión de `qa` (Iter-1) — completada. Se agregan criterios de cobertura y
> testabilidad sin modificar "Criterios de aceptación" ni "Criterios de
> seguridad" ya escritos. No se revisita ninguna decisión de diseño. Lista
> para `implementer` — ver "Confirmación de scope freeze" al final de esta
> sección.

### Tipo de prueba principal

**Unit / API contract testing**, mismo estilo que el resto de
`tests/test_fmp_client.py`: `httpx.MockTransport` inyectado (sin red real),
más un test unitario aislado para la función pura `_is_symbol_premium_402`.
No aplica Integration/E2E — el cambio no toca ningún límite de proceso, BD
ni flujo de Telegram (`query_handler.py` queda intacto por restricción
explícita de la spec).

### Cobertura mínima requerida

El proyecto no tiene `pytest-cov`/umbral de cobertura configurado
(`pytest.ini` no lo referencia) — no se agrega tooling nuevo para este fix
acotado. Cobertura se verifica **por inspección de branches cubiertos**, no
por reporte de herramienta:

- [ ] 100% de las líneas nuevas en `_get` (rama `status == 402`, ambas
  sub-ramas) ejercitadas por al menos un test — riesgo "Alto" (determina el
  mensaje que ve el usuario final, mismo nivel que las ramas 429/401/403 ya
  existentes).
- [ ] 100% de líneas y branches de `_is_symbol_premium_402` — función nueva,
  pura, de bajo costo de cobertura total y alto impacto en la clasificación.
- [ ] Todos los criterios de aceptación del `architect` y todos los de
  `security` cubiertos por al menos un test (ya se cumple con lo listado en
  esas secciones — este bloque solo lo declara como gate explícito de QA).

### Casos obligatorios (adicionales a los ya listados por `architect`/`security`)

- [ ] **Match parcial no debe disparar el sub-caso.** Dos tests separados:
  body que contiene `"special endpoint"` pero no `"symbol"`, y body que
  contiene `"symbol"` pero no `"special endpoint"` → ambos deben caer en el
  mensaje genérico. Cubre el riesgo de que un cambio futuro relaje el AND de
  los dos substrings a un OR por error.
- [ ] **Mayúsculas/minúsculas mixtas entre los dos substrings**, no solo
  "todo mayúsculas" u "todo minúsculas" (p. ej. `"Special ENDPOINT"` +
  `"SymBol"` en el mismo body) → `True`. El criterio de `architect` ya pide
  "variación de mayúsculas/minúsculas"; este caso fuerza que ambos substrings
  se normalicen de forma independiente, no que el test pase por casualidad
  con un solo `.upper()`/`.lower()` aplicado a todo el body de una vez.
- [ ] **Boundary del truncado a ~2000 caracteres (criterio de `security`).**
  Dos tests: (a) body de >2000 caracteres donde ambos substrings aparecen
  **después** de la posición 2000 → debe evaluar `False` (cae en genérico) —
  este es el único test que prueba que el truncado realmente se aplica, no
  solo que se documentó; (b) body de >2000 caracteres donde los substrings
  aparecen **antes** de la posición 2000 → debe seguir evaluando `True`.
  Sin este par, el criterio de `security` sobre el límite de tamaño queda
  sin verificación observable.
- [ ] **El sub-caso "símbolo premium" se prueba en los tres endpoints
  confirmados en la spec** (`/quote`, `/profile`, `/income-statement`), no
  solo en `/quote`. La lógica vive en `_get` (compartida), pero el `Estado
  objetivo` de la spec cita los tres endpoints como confirmados con
  MELI/DRAM — un test que solo cubre `/quote` no verifica que
  `get_profile`/`get_income_statement` efectivamente pasan por la misma
  rama (p. ej. por un wrapper que capture `FMPError` distinto en el futuro).
  Usar `pytest.mark.parametrize` sobre las tres funciones públicas para
  evitar tres tests casi idénticos.
- [ ] **`params` sin clave `"symbol"` presente.** Caso defensivo: si en el
  futuro este 402 se disparara desde un endpoint sin `symbol` en `params`
  (hoy no ocurre, pero la función que arma el mensaje no debe asumir la
  clave), un test verifica que `_get`/el mensaje no lanzan `KeyError` — el
  mensaje puede omitir el ticker o usar un valor genérico, pero no debe
  crashear.
- [ ] **Ticker con caracteres no alfanuméricos comunes en tickers reales**
  (p. ej. `"BRK.B"`) en `params["symbol"]` → aparece igual en el mensaje sin
  alterar el resto del texto (confirma que el mensaje se arma con
  interpolación simple, no con una regex que trate el ticker como patrón).

### Testabilidad

- [ ] `_is_symbol_premium_402` se define como función de módulo (no closure
  anidada dentro de `_get`) para poder importarla y testearla en aislamiento,
  igual que ya lo requiere el criterio de `architect`.
- [ ] La lectura de `exc.response.text` no depende de red: `httpx.Response`
  construido en memoria por `MockTransport` ya expone `.text` de forma
  síncrona — no se necesita ningún mock adicional más allá de los que ya usa
  el resto del archivo de tests.
- [ ] El truncado a ~2000 caracteres y el `try/except` acotado (criterios de
  `security`) deben quedar como pasos separados dentro de
  `_is_symbol_premium_402` o inmediatamente antes de invocarla — si quedan
  entrelazados con el resto de `_get` de forma que no se puedan ejercitar
  sin pasar por todo el flujo HTTP, es un hallazgo de testabilidad a
  reportar en el Momento 2 (post-implementación), no algo para resolver
  ahora.

### Fixtures nuevas necesarias (`tests/fixtures/fmp/`)

Siguiendo la convención ya existente en `tests/fixtures/fmp/README.md`
(fixtures documentadas como "sintético" o "real", con origen explícito):

- [ ] Un fixture con el **body real** confirmado con MELI/DRAM (texto plano,
  no JSON — el body de este 402 no es JSON): p. ej.
  `tests/fixtures/fmp/402_symbol_premium_real.txt` con el texto exacto citado
  en "Contexto" de esta spec, documentado en el README como **origen: real**
  (capturado con `curl`, a diferencia de los demás fixtures del directorio
  que son sintéticos).
- [ ] Un fixture sintético para el 402 genérico de regresión, p. ej.
  `tests/fixtures/fmp/402_generic_payment_required.json` (`{"error": "Payment Required"}`,
  ya sugerido por `architect`) — documentado como **origen: sintético**.
- [ ] Actualizar `tests/fixtures/fmp/README.md` agregando ambas entradas con
  el mismo formato que las existentes (una línea por archivo + origen).

Ningún fixture existente (`quote_adbe.json`, `search_*.json`) se modifica.

### Qué debe quedar parametrizado

- [ ] Los tests de `_is_symbol_premium_402` (casos límite de mayúsculas,
  match parcial, truncado) vía `pytest.mark.parametrize`, evitando
  duplicar `async def test_...` casi idénticos.
- [ ] El test de "símbolo premium por endpoint" (`/quote`, `/profile`,
  `/income-statement`) parametrizado sobre `(fn, endpoint_label)` en vez de
  tres funciones de test separadas.

### Qué no debe romperse (regresión explícita)

- [ ] Los 15 tests ya existentes en `tests/test_fmp_client.py` (los listados
  arriba en este archivo) siguen pasando **sin modificar una sola línea**
  de los tests actuales — verificable con `git diff` sobre
  `tests/test_fmp_client.py` mostrando solo líneas agregadas, cero líneas
  removidas/cambiadas en los tests preexistentes.
- [ ] `pytest` completo (`pytest -q` desde la raíz del repo, usa
  `pytest.ini` ya configurado) corre en verde, incluyendo los tests de
  otros módulos (no solo `test_fmp_client.py`) — confirma que no hay
  import circular ni efecto de import-time introducido por la nueva función.
- [ ] Ninguna de las ramas 429 / 401-403 / 5xx genérico / timeout / connect
  error / no-JSON cambia de texto ni de orden dentro de `_get` (mismo
  criterio que ya listó `architect`, aquí como gate ejecutable: un test
  existente por rama sigue en verde y su `assert` sobre el string del
  mensaje no requirió tocarse).

### Criterio de exit de QA

- Todos los tests (nuevos + existentes) pasan: `pytest -q` → `0 failed`.
- Sin tests ignorados/comentados (`@pytest.mark.skip`, `xfail`) para lograr
  la suite verde.
- Flaky rate = 0 — ningún test nuevo depende de tiempo real, orden de
  ejecución entre tests, ni de red (todo vía `MockTransport`).
- `tests/fixtures/fmp/README.md` actualizado y consistente con los archivos
  presentes en el directorio (ningún fixture huérfano sin documentar).

---

### Confirmación de scope freeze

Con este bloque, la spec queda congelada para `implementer`: no hay
criterios de aceptación, seguridad o QA pendientes de definir, no hay
preguntas abiertas de negocio, y los tres bloques de criterios
(`architect`/`security`/`qa`) son consistentes entre sí — ninguno contradice
ni reabre al anterior. `implementer` puede ejecutar Ralph Loop directamente
sobre "Criterios de aceptación" + "Criterios de seguridad" + los casos
obligatorios de esta sección, sin necesidad de volver a preguntar alcance.

---

## Artefactos a crear/modificar

- `src/investbot/fmp_client.py` → en `_get`, dentro del bloque `except httpx.HTTPStatusError`, agregar la rama `status == 402` con la función auxiliar `_is_symbol_premium_402` y el nuevo mensaje; el resto de ramas (429, 401/403, genérico final) no cambian de posición ni de texto.
- `tests/test_fmp_client.py` → agregar los tests listados en "Criterios de aceptación" (no modificar los tests existentes).

---

## Restricciones

- **No se investiga el alcance completo** de qué otros símbolos o endpoints están afectados por este tipo de 402 — el fix solo cubre el patrón textual confirmado con MELI/DRAM.
- **No se modifica el manejo de ningún otro status code** (429, 401/403, timeout, `RequestError`, respuesta no-JSON, ni el 402 genérico para bodies no reconocidos).
- **No se toca `query_handler.py`** ni el flujo de resolución de tickers/desambiguación (`search_company`, botones inline). El nuevo mensaje llega al usuario por el mismo mecanismo ya existente (`str(exc)` de `FMPError`).
- **No se toca `peers.py`** ni la lógica ya existente de evitar `/key-metrics-ttm` (eso resuelve el 402 a nivel de endpoint completo por un mecanismo distinto — evitación, no mensaje — y queda fuera de este fix).
- **No se agrega reintento automático, fallback a otro ticker, ni caché** — coherente con las restricciones ya vigentes de `SDD_investbot_mvp.md`.
- **El body crudo de la respuesta de FMP nunca se loguea**, se use o no para el match del sub-caso — solo se loguea un label del sub-caso detectado, igual que hoy solo se loguea `status`.
- El mensaje al usuario sigue sin incluir URL cruda, params completos ni la API key — mismo principio ya vigente en el resto de `fmp_client.py`.
- Esta spec no reabre ni reinterpreta ninguna decisión de diseño de `SDD_investbot_mvp.md` (stack, persistencia, presupuesto de requests, modelos de valoración, etc.) — es un fix aislado sobre un único archivo de producción y su archivo de tests.
