# Spec: Comando `/avanzado` — 5 modelos cuantitativos de análisis fundamental [Iter-1]

**Rol:** `architect` (spec base — diseño de una feature nueva de Telegram, toca superficie de entrada de usuario y consumo del cupo compartido de FMP).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa el nuevo comando (parseo de argumento de usuario, rate-limit, manejo de errores de FMP) → `qa` agrega criterios de cobertura/testabilidad → `implementer` ejecuta con Ralph Loop. No aplica `dba` (sin cambios de esquema/datos persistidos) ni `frontend` (sin UI más allá de texto de Telegram, mismo criterio que specs anteriores del proyecto). `backend` queda a discreción: esta spec no expone superficie de API nueva ni cambia arquitectura de servicio — reutiliza el mismo patrón cliente-HTTP-inyectado que ya audita `security`; se marca como no-aplicable salvo que `security` diga lo contrario.

**Estado (2026-09-01): D1, D2 y D3 resueltas por Daniela — ver cada una abajo. D4/D6/D7 quedan con propuesta del `architect` sin bloquear (no son decisiones de negocio, son detalles técnicos a verificar en `implementer`). D5 ya estaba confirmada. Spec lista para pasar a `security`.**

---

## Contexto

La skill `analisis-fundamental-avanzado` (`/Users/danielavergara/.claude/skills/analisis-fundamental-avanzado/SKILL.md`, copia también en `contexto/referencia/analisis_fundamental_avanzado.md`) documenta 5 modelos cuantitativos — Altman Z-Score (+ variante Z''), Piotroski F-Score, Beneish M-Score, Magic Formula de Greenblatt, y un scoring de 4 factores estilo AQR — pensados para aplicarse "a mano" sobre datos de fuentes públicas (stockanalysis.com, SEC EDGAR, macrotrends.net).

Daniela pidió convertir esto en una **feature real del bot**: un comando de Telegram que calcule los 5 modelos automáticamente para un ticker, usando los datos que InvestBot ya trae de FMP (plan gratuito, $0/mes, 250 requests/día — cupo **compartido** con el flujo de análisis existente).

InvestBot ya tiene:
- Un motor propio de valoración (`valuation.py`, `rules.py`) que sigue la misma regla de negocio que pide esta spec: **nunca estimar un dato financiero que falta** — se reporta `None`/"no disponible", nunca se sustituye por un supuesto (ver `rules.sum_ttm_field`, `rules.calculate_income_statement_ttm`: "todo o nada", nunca suma parcial).
- Un wrapper HTTP a FMP (`fmp_client.py`) que solo usa endpoints confirmados como gratuitos: `/quote`, `/profile`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/search-symbol` — y excluye explícitamente `/dcf`, `/sector-pe-ratio`, `/treasury-rates` (Decisión de diseño #8 de `SDD_investbot_mvp.md`) porque su disponibilidad gratuita nunca se verificó con una key real.
- Un presupuesto de requests ya calculado y documentado: el flujo de análisis actual (`query_handler.fetch_and_analyze_parts`) cuesta **9-12 requests por consulta completa**, soportando **~20-27 consultas/día** dentro del cupo de 250/día. No hay caché ni contador de uso diario — decisión de diseño explícita del MVP ("un solo usuario, uso esporádico, presupuesto con margen amplio").
- Sin comandos de Telegram propios más allá de `/start` (onboarding, `ConversationHandler`). El flujo principal de análisis es un `MessageHandler` de texto libre (`query_handler.py`), no un `CommandHandler`.

---

## Estado actual

- `src/investbot/fmp_client.py` expone funciones async ya listas para reusar sin cambios: `get_profile`, `get_quote`, `get_income_statement`, `get_balance_sheet_statement`, `get_cash_flow_statement` (todas aceptan `period=`/`limit=`).
- `src/investbot/query_handler.py` (971 líneas) contiene el único flujo de análisis hoy: `handle_text` → resuelve ticker → `_ask_escenario`/`_ask_ventana` (botones inline) → `_run_analysis` → `fetch_and_analyze_parts` (hasta 9-12 requests a FMP) → `summary.build_summary_parts`. No hay ningún `CommandHandler` para comandos de análisis — solo `bot.py` registra `CommandHandler("start", ...)` dentro de `onboarding.py`.
- `src/investbot/bot.py`, `build_application()`: registra el gate de `chat_id` (grupo -1), el handler de onboarding, y los handlers de `query_handler.build_query_handlers(...)`. Cualquier comando nuevo se registra acá.
- `src/investbot/security.py`: `InMemoryRateLimiter(max_requests=10, window_seconds=60.0)` ya instanciado una vez en `build_application` y compartido (inyectado) entre todos los handlers — protege el cupo de FMP contra loops/bugs, no es un contador de cupo diario.
- **Campos reales confirmados con `curl` contra el plan gratuito** (fixtures `tests/fixtures/fmp/{balance,income,cash_flow}_sheet_quarterly_nvda_real.json`, capturados 2026-07-31 — origen: real, documentado en `tests/fixtures/fmp/README.md`):
  - `balance-sheet-statement`: incluye `totalAssets`, `totalLiabilities`, `totalCurrentAssets`, `totalCurrentLiabilities`, `retainedEarnings`, `totalEquity`/`totalStockholdersEquity`, `longTermDebt`, `shortTermDebt`, `totalDebt`, `accountsReceivables`/`netReceivables`, `cashAndCashEquivalents`, **`propertyPlantEquipmentNet`** (neto — **no** hay campo de PP&E bruto).
  - `income-statement`: incluye `revenue`, `costOfRevenue`, `grossProfit`, `ebit`, `netIncome`, `sellingGeneralAndAdministrativeExpenses`, **`depreciationAndAmortization`** (combinado — **no** hay depreciación separada de amortización), `weightedAverageShsOut`/`weightedAverageShsOutDil`, `interestExpense`, `incomeTaxExpense`, `incomeBeforeTax`.
  - `cash-flow-statement`: incluye `operatingCashFlow`, `netIncome`, `capitalExpenditure`, `freeCashFlow`.
  - Confirmado también (Decisión #10 de `SDD_investbot_mvp.md`): `period="quarter"` y `period="annual"` devuelven los mismos nombres de campo, y `limit=N` en una sola llamada trae los `N` períodos más recientes — **2 años fiscales caben en 1 sola llamada por endpoint**, no 1 llamada por año.
- **No confirmado con `curl` real (el sitio `site.financialmodelingprep.com` bloqueó el acceso automatizado — HTTP 403 — durante la investigación de esta spec, tanto vía fetch directo como vía búsqueda web, que devolvió resultados inconsistentes/no verificables):**
  - Si `/profile` incluye algún campo booleano tipo `isEtf`/`isFund`/`isActivelyTrading` para distinguir una empresa individual de un ETF/fondo. El código actual nunca lee ni depende de estos campos.
  - Si existe un endpoint de precios históricos (`/historical-price-eod-*` u otro) disponible en el plan gratuito, y qué campos devuelve.
  - Forma exacta y completa de la respuesta real de `/quote`/`/profile` más allá de los campos que el código ya usa hoy (`price`, `marketCap`, `beta`, `sector`, `industry`, `yearHigh`, `yearLow`, `priceAvg50`, `priceAvg200` — estos sí están confirmados porque ya están en producción).

---

## Estado objetivo

Un comando nuevo de Telegram, `/avanzado <TICKER>`, que:

1. Exige un ticker exacto como argumento (sin resolución difusa por nombre — ver Decisión de diseño #1).
2. Valida que el ticker corresponde a una **empresa individual con estados financieros propios** antes de calcular nada — si no lo es (ETF, fondo, cripto, o cualquier símbolo sin balance/income/cash-flow), responde con un mensaje claro y **no intenta calcular ningún modelo** (ver Decisión de diseño #2).
3. Trae de FMP los datos de balance general, estado de resultados, flujo de caja operativo (2 años fiscales: reciente + anterior) y datos de mercado (precio, capitalización) — reusando exclusivamente los endpoints ya confirmados como gratuitos, sin agregar ninguno nuevo no verificado (salvo que Daniela apruebe D2 más abajo).
4. Calcula los 5 modelos con las fórmulas **exactas** de la skill (Altman Z + Z'', Piotroski F-Score, Beneish M-Score, Magic Formula, factores estilo AQR) — nunca estima un campo faltante; ver sección "Regla 'nunca inventar' aplicada modelo por modelo".
5. Responde en el formato sugerido por la propia skill ("Formato de reporte recomendado"), indicando explícitamente qué modelos no se pudieron calcular y por qué.
6. Respeta el mismo rate-limit compartido (`InMemoryRateLimiter`) y el mismo patrón de manejo de errores (`fmp_client.FMPError` → mensaje sanitizado, nunca traceback crudo) que el resto del bot.

---

## Decisiones de diseño

### 1. Comando dedicado con ticker exacto, no flujo de texto libre

`/avanzado <TICKER>` se implementa como `CommandHandler("avanzado", ...)`, separado del `MessageHandler` de texto libre existente. Requiere el ticker como argumento exacto (ej. `/avanzado AAPL`) — **no** reusa `fmp_client.search_company` para resolución por nombre.

**Por qué:** (a) mantiene el costo de requests predecible y bajo (ver sección de presupuesto) — una resolución por nombre ambigua costaría 1 request adicional y, si hay múltiples coincidencias, reabriría el flujo de botones `tk:` que ya existe para el otro comando, complejidad que este comando no necesita; (b) es coherente con que este es un comando "de experto" (5 modelos cuantitativos), no el flujo casual de texto libre — se asume que quien lo usa ya sabe el ticker exacto; (c) si Daniela prefiere resolución por nombre, es un cambio acotado a agregar después, no bloquea esta spec.

Formato de validación del argumento: mismo regex conservador que ya usa `query_handler._TICKER_CALLBACK_RE` (`^[A-Za-z0-9.\-]{1,10}$`), duplicado localmente en el módulo nuevo (mismo patrón de bajo acoplamiento que `peers.py`/`market_context.py`, que no importan de `query_handler.py`). Sin argumento o argumento inválido → mensaje de uso, cero requests a FMP.

### 2. Validación "empresa individual con estados financieros propios" — sin costo extra en el caso de rechazo

Orden de las llamadas, diseñado para gastar lo mínimo posible cuando el ticker **no** aplica:

1. `GET /profile` (1 request). Si la respuesta es vacía/`None` → rechazar inmediatamente ("no encontré esa empresa" / ticker inexistente). **0 requests adicionales.**
2. Si `/profile` trae los campos `isEtf`/`isFund`/`isActivelyTrading` (pendiente de confirmar — ver Decisión abierta D6) y alguno indica ETF/fondo/no-activo → rechazar con el mensaje de "esto no es una empresa individual" de la skill. **0 requests adicionales** (mismo request ya gastado en el paso 1 sirve para la validación y, si pasa, para los datos de sector/beta).
3. Si `/profile` no trae esos campos o no los tiene poblados, se sigue igual a los datos financieros (paso siguiente) — la validación definitiva de "tiene estados financieros propios" es de todas formas necesaria como red de seguridad (una cripto o un símbolo raro puede no tener flag pero sí devolver balance/income/cash-flow vacíos).
4. `GET /quote`, `GET /income-statement` (`period="annual", limit=2`), `GET /balance-sheet-statement` (`period="annual", limit=2`), `GET /cash-flow-statement` (`period="annual", limit=2`) — 4 requests. Si **cualquiera** de los 4 viene vacío/`None` → rechazar con "no tiene estados financieros propios disponibles, no se puede aplicar este análisis" (mismo estilo de abort-check que ya usa `fetch_and_analyze_parts` línea 253: `if not quote or not profile or not income_statements or not balance_sheets or not cash_flows`).

Costo: **1 request** si se rechaza en el paso 1-2, **5 requests** si pasa la validación completa y se calculan los 5 modelos.

**Pendiente antes de implementar (no bloquea el diseño, sí bloquea el código):** confirmar con un `curl` real contra `/profile` para un ETF conocido (ej. `SPY`) y, si es posible, un par cripto, qué campos/valores trae realmente FMP en el plan gratuito — mismo criterio que el proyecto ya exige para cualquier supuesto sobre la forma de una respuesta de FMP (ver precedente: fixtures "real" vs "sintético" de `tests/fixtures/fmp/README.md`, y el propio fix de 402 que se basó en un `curl` real, no en documentación). Si los campos no existen o no son confiables, el comando sigue funcionando correctamente solo con el fallback del paso 4 (más costoso en el caso de rechazo — 5 requests en vez de 1 — pero sigue siendo correcto).

### 3. Módulos nuevos, separados de `query_handler.py`

- `src/investbot/advanced_scoring.py` — **funciones puras, sin I/O** (mismo principio que `rules.py`/`valuation.py`, criterio de `qa` ya vigente en el proyecto: "no hay lógica de negocio escondida", testeable sin mockear HTTP). Implementa las fórmulas exactas de la skill: `calculate_altman_z`, `calculate_altman_z_prime_prime`, `calculate_piotroski_f_score`, `calculate_beneish_m_score`, `calculate_magic_formula_metrics`, `calculate_factor_score`. Cada función recibe los campos crudos ya resueltos (dicts de balance/income/cash-flow de los 2 años, más precio/market cap/beta) y devuelve un resultado tipado con `disponible: bool` (o flags por criterio, según el modelo — ver sección siguiente) siguiendo el mismo patrón de `rules.PerResult`/`rules.LiquidityResult`.
- `src/investbot/advanced_command.py` — **orquestación + Telegram**, mismo rol que `query_handler.py` pero acotado a este comando: parsea el argumento, llama a `fmp_client`, aplica la validación de la Decisión #2, llama a `advanced_scoring`, arma el mensaje final con el formato de la skill, maneja errores/rate-limit. Expone `build_advanced_command_handler(clients, rate_limiter) -> CommandHandler`.
- `src/investbot/bot.py` → se agrega el registro del nuevo handler en `build_application()`, reusando los mismos `clients`/`rate_limiter` ya construidos ahí (sin nuevas variables de entorno, sin nuevos clientes HTTP).

**Por qué separado de `query_handler.py`:** el archivo ya tiene 971 líneas y una responsabilidad clara (el flujo de texto libre con escenarios/ventanas). Esta feature no comparte ese flujo — comparte solo `Clients`/`fmp_client`/rate limiter — separarlo evita acoplar dos features independientes en el mismo archivo, mismo criterio de cohesión que ya separa `peers.py`, `market_context.py`, `risk_fit.py`, `corporate_events.py` como módulos independientes importados por `query_handler.py`.

### 4. Regla "nunca inventar" aplicada modelo por modelo

No todos los 5 modelos fallan igual ante un dato faltante — se definen **dos patrones**, según cómo está construido cada modelo en la propia skill:

**Patrón "todo o nada"** (Altman Z/Z'', Beneish M-Score, Magic Formula): son una única fórmula/ratio ponderada — no se puede sumar parcialmente. Si **cualquier** campo de entrada de la fórmula es `None`/no numérico, el modelo completo se reporta como `"no calculable con los datos disponibles"`, indicando qué campo específico faltó (mismo patrón ya usado por `rules.sum_ttm_field`/`calculate_income_statement_ttm`: "descarta el paquete COMPLETO en vez de mezclar fuentes").

**Patrón "criterios independientes"** (Piotroski F-Score, y las 4 etiquetas del scoring estilo AQR): cada criterio/factor es una evaluación separada. Si falta el dato de un criterio puntual, **ese criterio se excluye** (no participa ni suma ni resta) y se reporta explícitamente cuántos criterios fueron evaluables — ej. `"F-Score: 6/7 evaluables (2 no disponibles: dilución, margen bruto)"` en vez de fingir que se evaluaron los 9. Mismo espíritu que `rules.evaluate_pillars`, que ya evalúa cada pilar de forma independiente con su propio `None`.

Esta distinción es una decisión de diseño explícita (no está en la skill, que asume implícitamente datos completos) — se deja documentada acá para que `implementer` no la reabra ni la mezcle entre modelos.

### 5. Formato de respuesta

Un único mensaje de Telegram (no usa el flujo de botones/escenarios del otro comando), con el formato de la skill como base:

```
TICKER — Nombre de la empresa
Altman Z-Score: X.XX (zona: segura/gris/riesgo) [Z'': X.XX, zona — solo si sector asset-light]
Piotroski F-Score: X/9 (o "X/Y evaluables" si faltaron criterios)
Beneish M-Score: -X.XX (probabilidad de manipulación: baja/ambigua/alta) [o "no calculable", ver D1]
Magic Formula: ROIC XX% | Earnings Yield XX% (sin ranking — un solo ticker, ver Decisión #6)
Factores: Value [alto/medio/bajo] · Quality [alto/medio/bajo] · Momentum [alto/medio/bajo] · Low-vol [alto/medio/bajo]
Fuente de los datos: FMP (plan gratuito), balance/income/cash-flow más recientes disponibles, fecha del período fiscal
```

Reusa `query_handler.chunk_for_telegram` si el mensaje superara 4096 caracteres (defensivo — en la práctica un solo ticker no debería acercarse a ese límite, a diferencia del análisis completo que sí lo alcanza con múltiples secciones).

### 6. Magic Formula sin ranking — un solo ticker por consulta

La skill es explícita: el ranking de Magic Formula necesita comparar varias empresas ("con menos de ~5-6 empresas el ranking pierde utilidad estadística"). Este comando analiza **un ticker a la vez** — no hay portafolio. Se reportan ROIC y Earnings Yield como valores crudos (%), sin posición de ranking, con la misma advertencia de la skill si Daniela pide en el futuro un comando de comparación multi-ticker. **Un comando `/comparar TICKER1 TICKER2 ...` que sí arme el ranking queda fuera de alcance de esta spec** — backlog item explícito.

---

## Decisiones abiertas para Daniela

Estas 7 decisiones tocan la regla dura "nunca inventar" o el presupuesto de FMP — no las resuelve `architect` unilateralmente, quedan para que Daniela las confirme antes de scope freeze (`security`).

**D1 — Beneish M-Score: ¿proxy documentado o "no calculable" siempre? → RESUELTO: Opción A (estricta).**
Daniela eligió priorizar la regla dura del proyecto sobre la utilidad práctica del modelo: **Beneish M-Score se reporta siempre como "no calculable con los datos disponibles"** para cualquier ticker, sin excepción — FMP gratuito no separa PP&E bruto ni depreciación pura de amortización, y no se sustituyen esos campos por proxies aunque estén documentados como tales. El modelo queda visible en el formato de respuesta (para que quede claro que se intentó) pero nunca mostrará un número con datos de este plan.

**D2 — Momentum del factor 4 (AQR-style): → RESUELTO: proxy de costo cero, no el endpoint nuevo.**
Corrección importante: la pregunta original a Daniela sugería mal que había que "agregar una llamada extra" — no es así. Se usa `market_context.calculate_momentum` (ya existe en el proyecto), con datos de `/quote` que el comando **ya** pide para otra cosa (`priceAvg50`, `priceAvg200`, `yearHigh`, `yearLow`) — **cero requests adicionales**. Es una lectura de momentum distinta a "retorno de precio 6-12 meses" literal (posición vs. medias móviles y rango de 52 semanas), documentada como tal en el mensaje. El endpoint de precios históricos que daría el retorno % exacto **no se implementa** — su disponibilidad en el plan gratuito nunca se confirmó y agregarlo sin verificar rompería el criterio del proyecto de no asumir forma de respuesta de FMP sin `curl` real. Si en el futuro Daniela quiere el retorno % exacto, es una spec aparte que empieza por verificar ese endpoint.

**D3 — Umbrales absolutos para las etiquetas alto/medio/bajo. → RESUELTO: umbrales fijos predefinidos.**
Se usan valores de referencia estándar de la industria (ej. beta < 0.8 = low-vol "bajo"/defensivo, 0.8–1.2 = "medio", > 1.2 = "alto"; ROE > 15% = "alto", 5–15% = "medio", < 5% = "bajo") para poder etiquetar un ticker aislado sin universo de comparación. `implementer` deja la tabla de umbrales exacta en una constante documentada en `advanced_scoring.py` (no dispersa en el código), para que sea fácil de ajustar después si algún umbral resulta poco útil en la práctica.

**D4 — Clasificación "asset-light" para disparar el cálculo de Z''. (no bloquea, detalle técnico)**
Propuesta de `architect`, a verificar por `implementer` contra los valores reales que devuelve `profile.sector`/`profile.industry` de FMP (no genéricos inventados): dispara Z'' cuando el sector es `Technology`, `Communication Services`, o la industria indica software/plataforma digital/servicios de internet (ej. "Software", "Internet Content & Information", "Internet Retail"). Cualquier sector fuera de esa lista usa solo el Z original. No es una decisión de negocio — si `implementer` encuentra que los valores reales de FMP no calzan con esta lista, la ajusta sin volver a preguntar.

**D5 — Confirmado, no bloqueante:** Magic Formula sin ranking en este comando (Decisión de diseño #6) — se documenta acá solo para que quede junto al resto de decisiones abiertas relacionadas.

**D6 — Validación de "empresa individual" — campos de `/profile` sin verificar con curl real. (no bloqueante)**
Ver Decisión de diseño #2. No bloquea el diseño (hay fallback funcional en el paso 4 que igual detecta ETF/cripto/fondo aunque falten los flags de `/profile`). `implementer` verifica con `curl` real contra un ETF conocido (ej. SPY) al construir el comando, sin necesidad de volver a preguntarle a Daniela.

**D7 — Presupuesto compartido: ACEPTADO implícitamente al pedir esta feature.**
El comando cuesta 5 requests (o 1 si se rechaza temprano), sumado al cupo ya usado por el flujo existente (9-12/consulta) — costo combinado ~14-17 requests si se usan ambos comandos sobre el mismo ticker el mismo día, ~16-17 tickers/día de margen. Daniela pidió explícitamente esta feature con este alcance, así que se toma como aceptado — no se agrega caché ni contador diario en esta spec (sigue la decisión ya vigente del MVP).

---

## Presupuesto de requests FMP — impacto de este comando

Cálculo, siguiendo el mismo método ya usado en `SDD_investbot_mvp.md` sección "Presupuesto de requests FMP":

| Escenario | Requests FMP | Notas |
|---|---|---|
| Ticker rechazado en validación (no es empresa individual) | **1** | Solo `/profile` |
| Análisis completo de los 5 modelos | **5** | `/profile` + `/quote` + `/income-statement` (limit=2) + `/balance-sheet-statement` (limit=2) + `/cash-flow-statement` (limit=2) |

Con el cupo de 250 requests/día ya compartido con el flujo existente (9-12/consulta, ~20-27 consultas/día):

- Si el bot **solo** usara este comando nuevo: 250 / 5 ≈ **50 análisis avanzados/día**.
- Uso combinado (mismo ticker con ambos comandos en el mismo día): ~14-17 requests por ticker → 250 / ~15 ≈ **16-17 tickers/día** con ambos análisis.
- Sigue siendo generoso para el patrón de uso real del bot (un solo usuario, consultas esporádicas, ya documentado en el MVP) — no cambia la decisión de no agregar caché, pero reduce el margen disponible. Ver Decisión abierta D7.

---

## Criterios de aceptación

- [ ] `/avanzado` sin argumento o con un argumento que no matchea el regex de ticker (`^[A-Za-z0-9.\-]{1,10}$`) responde con un mensaje de uso claro (ej. `"Usá /avanzado TICKER, por ejemplo /avanzado AAPL"`) — **cero** llamadas a FMP.
- [ ] `/avanzado TICKER` para un ticker inexistente (`/profile` vacío) responde con un mensaje de "no encontré esa empresa" — **1** llamada a FMP, no más.
- [ ] `/avanzado TICKER` para un ETF/fondo conocido (una vez confirmado el campo real de `/profile`, D6) responde con un mensaje que dice explícitamente que este análisis no aplica a ETFs/fondos/cripto — sin intentar calcular ningún modelo.
- [ ] `/avanzado TICKER` para un ticker sin estados financieros propios (balance/income/cash-flow vacíos) responde con el mismo tipo de mensaje que el punto anterior, aunque `/profile` no tuviera el flag de ETF/fondo (red de seguridad del paso 4 de la Decisión #2).
- [ ] Para una empresa individual con datos completos, el mensaje final incluye los 5 modelos (o su reporte de "no calculable"/"no disponible" según corresponda) en el formato de la sección "Formato de respuesta".
- [ ] Cada modelo "todo o nada" (Altman Z/Z'', Beneish M-Score, Magic Formula) se reporta como "no calculable con los datos disponibles" si **cualquier** campo de entrada de su fórmula es `None`/no numérico — nunca se calcula con un valor sustituido ni parcial. Un test por modelo cubre al menos un campo faltante de cada fórmula.
- [ ] Piotroski F-Score excluye criterios individuales cuando falta el dato de alguno de los 2 años necesarios, reportando explícitamente cuántos de los 9 fueron evaluables — nunca asume 0 ni 1 para el criterio faltante.
- [ ] Z'' solo se calcula (y se muestra) cuando el sector/industria del ticker matchea la clasificación "asset-light" acordada en D4; para el resto, solo se muestra Z original.
- [ ] Ningún test de `advanced_scoring.py` requiere red/HTTP — son funciones puras (criterio ya vigente de `qa` para `rules.py`/`valuation.py`).
- [ ] `fmp_client.FMPError` (429/402/401/403/timeout/genérico) capturado en `advanced_command.py` se traduce al mismo tipo de mensaje sanitizado ya usado en `_run_analysis` — nunca un traceback crudo llega a Telegram.
- [ ] El comando respeta el `rate_limiter` compartido — igual que `_run_analysis`, si `rate_limiter.allow(chat_id)` es `False`, responde `RATE_LIMITED_MSG` sin llamar a FMP.
- [ ] El ticker se sanea antes de loguearse (`sanitize_for_log`, mismo criterio que `query_handler.py`).
- [ ] `bot.py` registra el nuevo `CommandHandler` reusando los mismos `clients`/`rate_limiter` ya construidos en `build_application` — sin nuevas variables de entorno.
- [ ] La suite completa (`pytest -q`) sigue en verde, incluidos todos los tests existentes sin modificar.

---

## Artefactos a crear/modificar

- `src/investbot/advanced_scoring.py` (NUEVO) → funciones puras de los 5 modelos + dataclasses de resultado con flags de disponibilidad por modelo/criterio.
- `src/investbot/advanced_command.py` (NUEVO) → `CommandHandler` de `/avanzado`: parseo de argumento, orquestación de llamadas a `fmp_client`, validación "empresa individual", armado del mensaje final.
- `src/investbot/bot.py` → agregar el registro del nuevo handler en `build_application()`.
- `tests/test_advanced_scoring.py` (NUEVO) → tests de las funciones puras, incluyendo los casos de campo faltante por modelo.
- `tests/test_advanced_command.py` (NUEVO) → tests del comando con `httpx.MockTransport`, incluyendo rechazo temprano (ETF/inexistente), rate-limit, y errores de FMP.
- `tests/fixtures/fmp/` → agregar fixtures nuevos necesarios (perfil sintético de ETF una vez confirmado D6, balance/income/cash-flow de 2 años de una empresa "asset-light" para probar Z'', y de una empresa manufacturera para Z original) — documentados en el `README.md` existente con su origen (real/sintético), mismo formato ya usado.

---

## Restricciones

- **No se modifica el flujo existente de texto libre** (`query_handler.py`, `handle_text`/`_run_analysis`/botones `tk:`/`esc:`/`vent:`) — el comando nuevo es aditivo, no toca ningún archivo del flujo actual salvo `bot.py` para el registro del handler.
- **No se agrega ningún endpoint de FMP no confirmado como gratuito** (`/dcf`, `/sector-pe-ratio`, `/treasury-rates`, cualquier endpoint de precios históricos) sin que Daniela apruebe explícitamente D2 y sin verificación por `curl` real primero — mismo criterio ya vigente del proyecto (Decisión de diseño #8 de `SDD_investbot_mvp.md`).
- **No se agrega caché ni contador de cupo diario** en esta spec — sigue la decisión ya vigente del MVP; D7 deja documentado el impacto, no lo resuelve con código nuevo.
- **No se construye todavía un comando de comparación multi-ticker** (`/comparar`) para el ranking real de Magic Formula — fuera de alcance, backlog item (Decisión de diseño #6).
- **La regla "nunca inventar" no se relaja para ningún modelo** salvo la excepción explícita y visible de D1 (proxy documentado de PP&E neto/D&A combinado para Beneish, si Daniela aprueba Opción B) — cualquier otro campo faltante siempre resulta en "no calculable"/"no disponible", nunca en un valor sustituido en silencio.
- **No se toca la lógica de validación de empresa existente** (`rules.py`, pilares de "buena empresa" del flujo de texto libre) — es un concepto distinto (pilares de calidad vs. los 5 modelos cuantitativos de esta spec), sin superposición de código.
- Esta spec no reabre ninguna decisión de diseño de `SDD_investbot_mvp.md` (stack, persistencia, arquitectura de despliegue, modelos de valoración existentes) — es una feature aditiva sobre la base ya aprobada.

---

## Revisión de seguridad

**Rol:** `security` (auditoría de diseño — sin código escrito todavía, ver criterio del pipeline).
**Alcance:** parseo del argumento de `/avanzado`, rate-limit compartido, manejo de errores de FMP, gate de `chat_id`, riesgo de agotar el cupo diario de FMP, y logging de datos innecesarios. Comparado contra el estándar ya vigente en `security.py`, `bot.py` y `query_handler.py` (que ya tiene hallazgos previos marcados `BLOQUEANTE`/`no bloqueante` — se sigue la misma convención acá).
**Veredicto general: sin hallazgos BLOQUEANTES. La spec puede pasar a `qa`.** Hay 6 hallazgos recomendados (no bloqueantes) que conviene incorporar como criterios de aceptación adicionales antes de `implementer`, porque son baratos de fijar ahora y caros de detectar después con un test que no los cubre explícitamente.

### 1. [INFORMATIVO] Gate de `chat_id` — confirmado, sin cambios necesarios

**CWE**: N/A (confirmación, no vulnerabilidad)
**OWASP**: A01:2025 — Broken Access Control

`security.build_chat_id_gate` se registra en `bot.py` como `TypeHandler(Update, ...)` en `group=-1` (`bot.py:67-69`), cubre **todos** los tipos de update, y corta la cadena con `ApplicationHandlerStop` antes de que cualquier otro handler se ejecute si el chat no está autorizado. Cualquier `CommandHandler` nuevo agregado con `application.add_handler(handler)` sin especificar `group` (default `0`, que corre *después* de `-1`) queda protegido automáticamente — no hace falta que `advanced_command.py` ni `bot.py` implementen ninguna lógica de autorización propia.

**Recomendado (no bloqueante):** agregar un criterio de aceptación explícito — `tests/test_advanced_command.py` debe incluir un test de `/avanzado` desde un `chat_id` no autorizado que confirme `ApplicationHandlerStop` (o, más simple, que el handler de `/avanzado` nunca se invoque) — hoy la spec confía en que el comportamiento se hereda del gate global, pero no hay ningún test que lo pruebe *para este comando en particular*. Barato de agregar, cierra la duda para siempre.

### 2. [MEDIO] Rate limiter compartido — el diseño no especifica la clave exacta, riesgo de bucket duplicado en vez de compartido

**CWE**: CWE-770 (Allocation of Resources Without Limits) — aplicado al cupo de FMP, no a memoria/CPU
**OWASP**: A04:2025 — Insecure Design

El objetivo #6 y la Decisión de diseño dicen "respeta el mismo rate-limit compartido (`InMemoryRateLimiter`)", y Decisión #3 dice que `bot.py` reusa "los mismos `clients`/`rate_limiter` ya construidos". Eso garantiza que se pase la **misma instancia** de `InMemoryRateLimiter`, pero `InMemoryRateLimiter._events` es un `dict` keyed por el string que cada llamador le pase a `.allow(key)` — la instancia compartida solo produce un balde *realmente* compartido si `advanced_command.py` usa **exactamente** la misma clave que `query_handler.py`: `chat_key = str(update.effective_chat.id)` (`query_handler.py:747`, repetido en `:799` y `:826`).

Si `implementer` improvisa una clave distinta (ej. `chat_id` como `int` en vez de `str`, o algo como `f"avanzado:{chat_id}"` para "namespacear" el comando — una tentación razonable si no se documenta lo contrario), el resultado sería **dos baldes independientes de 10 requests/60s en vez de uno compartido** — duplicando de hecho el techo de abuso combinado que D7 ya calculó asumiendo un balde único. Ningún criterio de aceptación actual lo detectaría: "el comando respeta el rate-limit" (criterio ya listado) pasa igual con un balde separado, porque ese test typicamente solo llama `/avanzado` repetidamente, nunca mezcla ambos comandos en la misma ventana.

**Escenario concreto:** con baldes separados, el mismo chat autorizado podría hacer 10 llamadas a `/avanzado` (50 requests FMP) **más** 10 consultas de texto libre (90-120 requests FMP) dentro de la misma ventana de 60 segundos — hasta ~170 requests/minuto, agotando el cupo diario de 250 en menos de 1 minuto de uso descuidado (doble tap, loop de un script, bug de un cliente de Telegram alternativo), en vez del comportamiento ya asumido de un único balde de 10 llamadas/60s combinadas.

**Mitigación recomendada:**
- Fijar explícitamente en la spec (y como criterio de aceptación) que la clave debe ser `str(update.effective_chat.id)`, sin prefijo ni transformación — idéntica a `query_handler.py`.
- Agregar un test específico en `tests/test_advanced_command.py` (o uno cruzado en un archivo de integración) que confirme que llamar `/avanzado` N veces y luego una consulta de texto libre en la misma ventana comparten el mismo contador — no que cada uno individualmente respete el límite, sino que **el total combinado** dispara `RATE_LIMITED_MSG`.

**No bloqueante** porque el riesgo de explotación real es bajo (usuario ya autorizado, sin incentivo económico para agotar su propio cupo) — pero es exactamente el tipo de gap que el pipeline pide confirmar explícitamente para este comando, y es gratis de cerrar con una frase en la spec.

### 3. [BAJO] Falta un `except Exception` genérico en `advanced_command.py` — no hay fuga, pero el bot queda mudo ante un bug de fórmula

**CWE**: CWE-755 (Improper Handling of Exceptional Conditions)
**OWASP**: A09:2025 — Logging & Monitoring Failures (parcial — también toca disponibilidad)

`_run_analysis` en `query_handler.py` tiene dos `except` separados (línea 919 y 921): `fmp_client.FMPError`/`TreasuryError` → mensaje sanitizado ya armado, y un `except Exception` genérico ("Hallazgo 2 de `security`, BLOQUEANTE" original) que loguea con `sanitize_for_log(ticker)` y responde `GENERIC_ERROR_MSG` — nunca deja que una excepción no prevista llegue sin respuesta al usuario.

El criterio de aceptación actual de esta spec solo cubre el primer caso: *"`fmp_client.FMPError` ... capturado en `advanced_command.py` se traduce al mismo tipo de mensaje sanitizado"*. No hay ningún criterio equivalente al segundo `except`. Esto importa particularmente acá porque `advanced_scoring.py` son 6 funciones nuevas con fórmulas que dividen por `totalAssets`, `totalEquity`, `totalLiabilities`, etc. — campos que la regla "nunca inventar" protege solo contra `None`, no contra un valor presente pero igual a `0` (ej. un caso real de deuda total `0` en una empresa sin apalancamiento, o `totalCurrentLiabilities: 0`), lo que puede producir `ZeroDivisionError` en al menos Altman Z, Piotroski (ratios de liquidez) y Magic Formula (ROIC).

**No hay riesgo de fuga**: confirmé que `bot.py:_on_error` (el `add_error_handler` global, línea 39-44) solo loguea con `logger.exception(...)` — nunca responde nada al chat. Entonces una excepción no capturada en `advanced_command.py` no filtra traceback/API key/URL a Telegram (mismo estándar que el resto del bot cumple), pero sí deja al usuario sin ninguna respuesta ("el bot no contestó"), rompiendo la paridad de UX/observabilidad que el resto del bot sí tiene.

**Mitigación recomendada:** agregar a `advanced_command.py` el mismo `except Exception` que `_run_analysis` alrededor de la orquestación completa (fetch + validación + `advanced_scoring` + armado del mensaje), logueando `sanitize_for_log(ticker)` y respondiendo `GENERIC_ERROR_MSG` (reusar la constante de `query_handler.py`, no duplicarla). Agregar como criterio de aceptación explícito, con al menos un test que fuerce una excepción no-`FMPError` (ej. monkeypatchear `advanced_scoring.calculate_altman_z` para que lance `ZeroDivisionError`) y confirme que el usuario recibe `GENERIC_ERROR_MSG`, no silencio.

**No bloqueante** (no hay fuga de datos ni bypass de auth), pero se recomienda incorporar antes de `implementer` porque es el mismo patrón ya validado en este archivo — no hay ambigüedad de diseño que resolver.

### 4. [BAJO] `parse_mode` no especificado — texto de terceros (nombre de empresa vía FMP) podría romper el parseo de Markdown

**CWE**: CWE-20 (Improper Input Validation) — aplicado al formato de salida, no a una fuga
**OWASP**: A05:2025 — Security Misconfiguration (config de formato, no de secretos)

La Decisión de diseño #5 muestra el formato del mensaje final pero no dice si se envía con `parse_mode="Markdown"` (como hace `_run_analysis` en el camino exitoso, línea 939) o como texto plano. El template de la Decisión #5 no usa ninguna sintaxis Markdown (`*`, `_`, `` ` ``, `[`), así que texto plano alcanza y es más seguro. Si `implementer` copia `parse_mode="Markdown"` por costumbre (mismo patrón que `_run_analysis`), el nombre de la empresa (`profile.get("companyName")`, dato de terceros vía FMP, no controlado por el proyecto ni sanitizado hoy) podría contener caracteres que rompen el parser de Telegram (ej. un `_` o `*` sin cerrar en el nombre real de una empresa) y causar un `TelegramError: Can't parse entities` al enviar — no es una fuga de datos ni una vulnerabilidad de inyección de código (Telegram no ejecuta nada, solo formatea texto), pero sí un fallo de entrega evitable.

**Mitigación recomendada:** enviar el mensaje de `/avanzado` sin `parse_mode` (texto plano) dado que el formato no lo necesita — más simple que escapar Markdown, y elimina la superficie por completo. Documentarlo como decisión explícita en Decisión #5 para que `implementer` no copie el patrón de `_run_analysis` por inercia.

**No bloqueante.**

### 5. [INFORMATIVO] Log del argumento inválido — falta un tope de longitud explícito antes de sanitizar

**CWE**: CWE-117 (Improper Output Neutralization for Logs) — parcialmente ya mitigado
**OWASP**: A09:2025 — Logging & Monitoring Failures

El criterio de aceptación ya exige `sanitize_for_log` sobre el ticker antes de loguearlo (correcto, cubre CWE-117 por caracteres de control). Pero a diferencia del `callback_data` de los botones `tk:`/`esc:`/`vent:` (acotado por el límite de 64 bytes de Telegram para `callback_data`), el argumento de un `CommandHandler` (`context.args`) viene del texto libre del mensaje, sin ese límite — un usuario autorizado (o un cliente de Telegram alternativo con un bug) podría enviar `/avanzado ` seguido de miles de caracteres. `sanitize_for_log` quita saltos de línea y caracteres de control, pero no acota longitud, así que el intento de log del argumento inválido (`"ticker inválido: %s"`, mismo patrón que `query_handler.py:808-811`) podría producir una línea de log de varios KB.

**Mitigación recomendada:** truncar el valor logueado a una longitud fija corta (ej. 40 caracteres, más que suficiente ya que un ticker válido tiene como máximo 10) antes de pasarlo a `sanitize_for_log`, mismo espíritu que `_MAX_BODY_CHARS_402` en `fmp_client.py`.

**No bloqueante** — el gate de `chat_id` ya exige que quien llega hasta acá esté autorizado, así que el impacto es acotado a ruido de logs, no a un vector de ataque externo.

### 6. [INFORMATIVO] D7 asume un solo usuario — `security.py` ya soporta múltiples `chat_id` autorizados (CSV)

**CWE**: N/A (nota de contexto, no vulnerabilidad de este diseño)
**OWASP**: A04:2025 — Insecure Design (supuesto de negocio a re-verificar, no del código de esta spec)

D7 acepta el impacto en el cupo compartido de FMP con la premisa "un solo usuario, uso esporádico". Pero `security.get_allowed_chat_id()` ya soporta múltiples `chat_id` autorizados vía CSV (`TELEGRAM_ALLOWED_CHAT_ID` — ver docstring de `security.py:1-19`, "control de acceso multi-usuario (hasta N chat_id autorizados)"). El `InMemoryRateLimiter` limita **por clave** (por `chat_id`, ver hallazgo 2) — así que si Daniela alguna vez agrega un segundo `chat_id` autorizado, cada usuario autorizado tendría su **propio** balde de 10 llamadas/60s, multiplicando por N el techo combinado de abuso involuntario sobre el mismo cupo diario de FMP (que es uno solo, por API key, no por usuario). El cálculo de D7 ("~16-17 tickers/día") deja de ser válido con más de un `chat_id` activo.

**No es un hallazgo de esta spec** (la capacidad multi-usuario ya existe independientemente de `/avanzado`) — se deja documentado acá solo para que quede registrado junto a D7, por si Daniela agrega un segundo `chat_id` en el futuro: en ese momento sí conviene revisar si hace falta un contador de cupo diario compartido entre usuarios (explícitamente fuera de alcance de esta spec, ver sección "Restricciones").

### 7. [INFORMATIVO] Confirmaciones — sin hallazgos

- **Inyección en la URL/params de FMP:** no aplica. El comando reusa sin cambios las funciones de `fmp_client.py` (`get_profile`, `get_quote`, etc.), que ya usan `params=` de `httpx` exclusivamente (nunca f-string/concatenación) — el ticker, una vez pasado el regex `^[A-Za-z0-9.\-]{1,10}$`, llega a FMP correctamente encodeado sin riesgo de injection.
- **Fuga de API key/traceback/URL en errores de FMP (429/402/401/403/timeout):** ya completamente cubierto en `fmp_client._get` (líneas 63-126), sin cambios necesarios — `FMPError` nunca incluye URL ni params. La spec reusa este mecanismo sin modificarlo, correcto.
- **PII:** no aplica — los datos mostrados (balance, income, cash-flow, precio) son de terceros (empresas públicas), no datos personales del usuario. No hay campo nuevo que loguee algo del usuario más allá del ticker (ya cubierto en hallazgo 5) y el `chat_id` (ya logueado hoy por el gate global, sin cambios).
- **Regex del ticker:** duplicar `^[A-Za-z0-9.\-]{1,10}$` localmente en `advanced_command.py` en vez de importarlo de `query_handler.py` es consistente con el patrón de bajo acoplamiento que ya usan `peers.py`/`market_context.py` — sin objeción, aunque como nota menor de mantenibilidad (no de seguridad) valdría la pena que `qa` verifique que ambas copias del regex no diverjan con el tiempo.

### Resumen para `qa`

| # | Hallazgo | Severidad | Bloqueante |
|---|---|---|---|
| 1 | Test explícito de `/avanzado` + gate de `chat_id` no autorizado | Informativo | No |
| 2 | Clave exacta del rate limiter (`str(chat_id)`) + test de balde compartido entre comandos | Medio | No |
| 3 | `except Exception` genérico en `advanced_command.py` (paridad con `_run_analysis`) | Bajo | No |
| 4 | Sin `parse_mode="Markdown"` en el mensaje de `/avanzado` (texto plano) | Bajo | No |
| 5 | Truncar longitud del argumento inválido antes de loguear | Informativo | No |
| 6 | D7 asume un solo `chat_id` — revisar si se agrega otro en el futuro | Informativo | No |

Ningún hallazgo bloquea el pase a `qa`/`implementer`. Se recomienda que `qa` incorpore los hallazgos 1-5 como criterios de aceptación/tests adicionales explícitos (barato ahora, más caro de detectar después sin un test que los cubra), y que `implementer` los resuelva en la misma pasada que el resto de la spec.

---

## Criterios QA para Spec: Comando `/avanzado` — 5 modelos cuantitativos [Iter-1]

**Rol:** `qa` (Momento 1 — pre-implementación). Recibida la spec ya enriquecida por `architect` y `security` (sin bloqueantes, 6 hallazgos no bloqueantes). Esta sección complementa los 17 criterios de aceptación existentes y los hallazgos 1-5 de `security` con los ángulos de cobertura/testabilidad que faltaban — no duplica lo que ya está bien definido arriba.

### Tipo de prueba principal

**Unit + Integration** (sin E2E real contra FMP). Justificación: mismo criterio ya vigente en el proyecto para `rules.py`/`valuation.py` (unit, funciones puras) y `query_handler.py` (integration, `httpx.MockTransport`) — ver `tests/test_rules.py`, `tests/test_query_handler.py`. Esta spec reproduce exactamente ese patrón en dos módulos nuevos:
- `advanced_scoring.py` → **Unit**: funciones puras, sin I/O, testeadas con dicts de entrada armados a mano — sin red/HTTP/mocks.
- `advanced_command.py` → **Integration**: orquestación + Telegram, testeada con `httpx.MockTransport` (mismo patrón que `tests/test_query_handler.py`) y objetos `Update`/`Context` simulados (mismo patrón que `tests/test_bot.py`).

No aplica E2E porque no hay flujo de UI más allá de texto de Telegram (ya lo descarta `architect`), y el proyecto nunca ejecuta contra la API real de FMP en CI (ver sección "Qué NO se prueba").

### Cobertura mínima requerida

- [ ] Code coverage ≥ 90% en `advanced_scoring.py` (lógica crítica de negocio: fórmulas financieras, ver tabla de riesgo — 100% en la rama "campo faltante" de cada una de las 6 funciones)
- [ ] Code coverage ≥ 80% en `advanced_command.py` (orquestación — ≥ 90% en las ramas de rechazo temprano y manejo de errores, que son las de mayor riesgo de negocio: gasto de cupo FMP y UX ante fallo)
- [ ] Branch coverage 100% en la distinción "todo o nada" vs. "criterios independientes" (Decisión de diseño #4) — cada una de las 6 funciones de `advanced_scoring.py` tiene al menos un test que ejercita su rama de dato faltante
- [ ] Los 17 criterios de aceptación del `architect` cubiertos por al menos un test explícito (mapeo 1:1 abajo)
- [ ] Los 5 hallazgos no bloqueantes de `security` (1-5) cubiertos por al menos un test explícito (mapeo abajo) — el hallazgo 6 es informativo, sin código de esta spec, no requiere test

### Mapeo criterios de aceptación → test (verifica que ninguno quede sin cobertura)

| # | Criterio de aceptación (`architect`) | Test que lo cubre |
|---|---|---|
| 1 | Sin argumento / regex inválido → mensaje de uso, 0 requests | `test_advanced_command.py::test_sin_argumento_responde_uso_sin_llamar_fmp`, `::test_argumento_invalido_responde_uso_sin_llamar_fmp` |
| 2 | Ticker inexistente (`/profile` vacío) → "no encontré esa empresa", 1 request | `::test_ticker_inexistente_responde_no_encontrado_una_sola_llamada` |
| 3 | ETF/fondo conocido → mensaje "no aplica a ETF/fondo/cripto", sin calcular modelos | `::test_etf_responde_no_aplica_sin_calcular_modelos` |
| 4 | Ticker sin estados financieros (balance/income/cash-flow vacíos, sin flag de `/profile`) → mismo mensaje que #3 | `::test_sin_estados_financieros_responde_no_aplica_red_de_seguridad` |
| 5 | Empresa individual completa → mensaje con los 5 modelos en el formato de la spec | `::test_empresa_completa_incluye_los_5_modelos_en_formato` |
| 6 | Cada modelo "todo o nada" → "no calculable" si falta cualquier campo (1 test por modelo) | `test_advanced_scoring.py::test_altman_z_campo_faltante_no_calculable`, `::test_altman_z_prime_prime_campo_faltante_no_calculable`, `::test_beneish_m_score_siempre_no_calculable` (D1), `::test_magic_formula_campo_faltante_no_calculable` |
| 7 | Piotroski excluye criterios individuales, reporta "X/9 evaluables" | `::test_piotroski_f_score_excluye_criterio_con_dato_faltante`, `::test_piotroski_f_score_reporta_evaluables_correctos` |
| 8 | Z'' solo se muestra si sector/industria matchea D4 | `::test_z_prime_prime_se_calcula_solo_para_asset_light`, `::test_z_original_para_empresa_manufacturera` |
| 9 | `advanced_scoring.py` sin red/HTTP | Verificación estática (ver "Testabilidad" abajo) + ausencia de `httpx`/`fmp_client` importado en el módulo |
| 10 | `FMPError` → mensaje sanitizado, nunca traceback crudo | `test_advanced_command.py::test_fmp_error_429/402/401/403/timeout_responde_mensaje_sanitizado` (parametrizado) |
| 11 | Rate limiter compartido, `RATE_LIMITED_MSG` sin llamar a FMP | `::test_rate_limiter_bloqueado_no_llama_fmp` |
| 12 | Ticker saneado antes de loguearse | `::test_ticker_invalido_se_loguea_saneado` |
| 13 | `bot.py` registra el handler con `clients`/`rate_limiter` compartidos | `test_bot.py::test_build_application_registra_handler_avanzado` |
| 14 | Suite completa en verde | Ejecución de `pytest -q` en Momento 2 (Ralph Loop) — no es un test nuevo, es el gate final |

*(Los 3 criterios de aceptación restantes del `architect` — formato exacto del mensaje, presupuesto de requests por escenario, y no-modificación de `query_handler.py` — están cubiertos transversalmente por los tests de arriba más una revisión de diff en Momento 2, no requieren un test dedicado adicional.)*

### Mapeo hallazgos de `security` → test

| # | Hallazgo | Test que lo cubre |
|---|---|---|
| 1 | Gate de `chat_id` no autorizado corta antes de `/avanzado` | `test_advanced_command.py::test_avanzado_desde_chat_id_no_autorizado_no_se_invoca` (mismo patrón que `test_bot.py::test_nuevos_handlers_esc_vent_no_interceptan_onboarding`) |
| 2 | Rate limiter usa la MISMA key (`str(chat_id)`) y el MISMO balde que `query_handler.py` — **crítico, ver detalle abajo** | `test_advanced_command.py::test_rate_limiter_key_identica_a_query_handler` + `tests/test_rate_limit_compartido.py::test_avanzado_y_texto_libre_comparten_el_mismo_balde` (integration cruzada) |
| 3 | `except Exception` genérico → `GENERIC_ERROR_MSG`, nunca silencio | `test_advanced_command.py::test_excepcion_no_fmp_error_responde_generic_error_msg` (monkeypatch de `advanced_scoring.calculate_altman_z` para forzar `ZeroDivisionError`) |
| 4 | Sin `parse_mode="Markdown"` en el mensaje final | `test_advanced_command.py::test_mensaje_final_se_envia_sin_parse_mode_markdown` (assert sobre el kwarg de la llamada mockeada a `send_message`/`reply_text`) |
| 5 | Argumento inválido truncado antes de loguear (ej. 40 caracteres) | `test_advanced_command.py::test_argumento_larguisimo_se_trunca_antes_de_loguear` (input de varios KB, assert sobre longitud del log capturado) |

**Detalle del hallazgo 2 (el más importante de cerrar con test, no solo con código):** un test que solo llama `/avanzado` repetidas veces y confirma que el rate-limit dispara **no** detecta un balde duplicado (ej. `f"avanzado:{chat_id}"` en vez de `str(chat_id)`) — pasaría igual con baldes separados. El test obligatorio es una prueba **cruzada**: instanciar un único `InMemoryRateLimiter(max_requests=10, window_seconds=60.0)`, inyectarlo en ambos handlers (`/avanzado` y el `MessageHandler` de texto libre), disparar 6 llamadas a `/avanzado` + 5 consultas de texto libre desde el mismo `chat_id` dentro de la ventana, y confirmar que la **11ª** interacción combinada (sin importar cuál de los dos comandos sea) recibe `RATE_LIMITED_MSG` — nunca 10+10 exitosas. Si el test solo mide "10 llamadas a `/avanzado` bloquean la 11ª de `/avanzado`", el hallazgo 2 de `security` queda sin verificar y puede pasar desapercibido.

### Casos obligatorios

- [ ] Happy path: `/avanzado AAPL` (empresa manufacturera/tradicional, sin asset-light) con balance/income/cash-flow/quote completos de 2 años → mensaje con Z original (no Z''), F-Score X/9, Beneish "no calculable" (D1), Magic Formula con ROIC/Earnings Yield, 4 factores AQR etiquetados
- [ ] Happy path asset-light: mismo flujo con `sector="Technology"` → mensaje incluye Z **y** Z'' (D4)
- [ ] Caso límite — valor `0` presente (no `None`) en un divisor: `totalLiabilities=0`, `totalCurrentLiabilities=0`, `totalAssets=0` → no debe lanzar `ZeroDivisionError` sin capturar; cubierto tanto a nivel de `advanced_scoring.py` (la función debe manejarlo o propagar de forma controlada) como a nivel de `advanced_command.py` (hallazgo 3 de `security`, `except Exception` genérico como red de seguridad)
- [ ] Caso límite — ticker de exactamente 10 caracteres (límite superior del regex) y de 11 (debe rechazar)
- [ ] Caso límite — ticker con minúsculas (`/avanzado aapl`) — confirmar si se normaliza a mayúsculas antes de llamar a FMP o si FMP lo acepta indistinto (dejar explícito en el test qué comportamiento se espera, evita que `implementer` lo decida sin registro)
- [ ] Caso de error: cada código de `FMPError` (429/402/401/403/timeout/genérico) → mensaje sanitizado correspondiente, ninguno filtra traceback/URL/API key
- [ ] Caso de error: `advanced_scoring` lanza una excepción no-`FMPError` (ej. `ZeroDivisionError`, `KeyError` si el orquestador arma mal el dict de entrada) → `GENERIC_ERROR_MSG`, nunca silencio (hallazgo 3)
- [ ] Caso de alto riesgo de negocio: rate limiter compartido con clave idéntica entre `/avanzado` y el flujo de texto libre (hallazgo 2, detalle arriba) — el de mayor severidad de los hallazgos de `security` porque afecta directamente el presupuesto de FMP que sostiene todo el MVP

### Testabilidad

- [ ] `advanced_scoring.py` no importa `httpx`, `fmp_client`, ni ningún símbolo de I/O — verificable con un test estático simple (`import ast` sobre el archivo, o simplemente confirmar que ningún test de este módulo necesita `httpx.MockTransport`) — mismo criterio ya vigente para `rules.py`/`valuation.py`
- [ ] Las 6 funciones de `advanced_scoring.py` son puras: mismo input → mismo output, sin mutar los dicts de entrada (test de idempotencia opcional pero recomendado dado que son 6 funciones nuevas)
- [ ] `advanced_command.py` recibe `clients`/`rate_limiter` por inyección en `build_advanced_command_handler(clients, rate_limiter)` (ya lo dice la Decisión de diseño #3) — ningún test necesita monkeypatchear un singleton global para simular rate-limit o fallo de FMP
- [ ] El efecto secundario de Telegram (`update.message.reply_text` / `context.bot.send_message`) está aislado detrás de un mock/objeto simulado en todos los tests de `advanced_command.py` — mismo patrón que `tests/test_query_handler.py`

### Fixtures de datos mínimas requeridas (completa la lista parcial de "Artefactos a crear/modificar")

Ubicación: `tests/fixtures/fmp/`, documentadas en el `README.md` existente con origen (real/sintético), mismo formato ya usado.

| Fixture | Propósito | Origen esperado |
|---|---|---|
| `profile_empresa_completa.json` + `balance_/income_/cash_flow_annual_empresa_completa.json` (2 años, `limit=2`) | Caso "todo disponible" — happy path de los 5 modelos | Sintético (imitando forma real ya confirmada) |
| Variante con `totalDebt`/`retainedEarnings` faltante o `null` | Campo faltante para Altman Z/Z'' → "no calculable" | Sintético |
| Variante con dato faltante en 1 de los 2 años necesarios para un criterio de Piotroski (ej. `grossProfit` año anterior) | Piotroski excluye ese criterio puntual, reporta "X/9 evaluables" | Sintético |
| N/A — Beneish siempre "no calculable" por D1, no necesita fixture de campo faltante específico | Confirma que ni con datos completos se calcula (ver criterio 6) | Reusa la fixture "completa" |
| Variante con `netIncome` o `weightedAverageShsOut` faltante | Magic Formula → "no calculable" | Sintético |
| Variante con `beta`/`priceAvg50`/`priceAvg200`/`yearHigh`/`yearLow` faltante | Factor Momentum (AQR) → criterio excluido, no todo el scoring | Sintético |
| `profile_etf_spy.json` (o el ticker que `implementer` confirme por `curl`, D6) | ETF/fondo rechazado — con el/los campo(s) real(es) que FMP usa para distinguirlo, una vez confirmado D6 | **Real si D6 se confirma antes de escribir el test**; sintético + nota explícita de "pendiente verificar con curl" si no |
| `profile_ticker_inexistente.json` (o simplemente respuesta vacía/`None`) | Ticker inexistente | Sintético (`/profile` vacío es un caso trivial de simular) |
| `balance_/income_/cash_flow_annual_empresa_asset_light.json` | Empresa "asset-light" (`sector="Technology"` u otro de D4) — dispara Z'' | Sintético, sector/industria coherente con la lista de D4 |
| `balance_/income_/cash_flow_annual_empresa_manufacturera.json` | Empresa manufacturera clásica — Z original, no Z'' | Sintético, sector fuera de la lista de D4 (ej. "Industrials") |
| Body vacío / símbolo raro sin balance/income/cash-flow (ej. cripto) | Red de seguridad del paso 4 (Decisión #2) — rechazo sin flag de `/profile` | Sintético |

**Nota:** ninguna fixture nueva requiere origen "real" obligatoriamente salvo la de ETF (D6, ya lo marca `architect` como pendiente de `curl`) — el resto son variantes sintéticas de la forma ya confirmada como real en `balance_/income_/cash_flow_quarterly_nvda_real.json`, mismo criterio que el proyecto ya aplica (fixture sintética que imita una forma ya verificada, no una fixture sintética que inventa forma).

### Qué NO se prueba (y por qué)

- **No hay test E2E contra la API real de FMP.** Mismo criterio ya vigente en todo el proyecto (`tests/test_fmp_client.py`, `test_query_handler.py`, etc. — todos usan `httpx.MockTransport` o fixtures, nunca una key real en CI). Las únicas verificaciones contra FMP real de esta spec son manuales, fuera de la suite automatizada: el `curl` de D6 (campos de `/profile` para ETF) y el `curl` ya hecho para confirmar la forma de balance/income/cash-flow (ya documentado como "real" en el README de fixtures).
- **No hay test de carga/performance.** El comando hace como máximo 5 requests secuenciales a una API externa ya lenta por naturaleza (red) — no hay lógica de negocio propia que justifique medir P95/P99; mismo criterio que el resto del bot (sin comandos de Telegram con requisitos de performance documentados).
- **No hay test de la resolución por nombre / flujo de botones `tk:`** — no aplica, la Decisión de diseño #1 excluye explícitamente la resolución difusa por nombre para este comando.
- **No hay test del comando `/comparar` multi-ticker ni del ranking real de Magic Formula** — explícitamente fuera de alcance (Decisión de diseño #6, backlog item), no corresponde crear tests de algo que no se va a implementar en esta spec.
- **No hay test de que el proxy de PP&E/D&A para Beneish produzca un número** — D1 resolvió la Opción A estricta, Beneish nunca calcula un valor con este plan de FMP; el único test relevante es confirmar que siempre devuelve "no calculable", no que un proxy funcione.
- **No hay test de contador de cupo diario ni de caché** — explícitamente fuera de alcance de esta spec (Restricciones, D7) — no se testea algo que no se construye.
- **No hay test de multi-`chat_id` real con más de un usuario autorizado simultáneo** (hallazgo 6 de `security`) — es informativo y no corresponde al código de esta spec (la capacidad multi-usuario ya existe en `security.py` independientemente); si Daniela agrega un segundo `chat_id`, ese es un caso de regresión para un pipeline run futuro, no de este.

### Criterio de exit de QA

- Todos los tests pasan (BUILD SUCCESS / suite verde), incluida la suite completa existente sin modificar (`pytest -q`)
- Sin tests ignorados, comentados, ni marcados `xfail`/`skip` para pasar CI
- Flaky rate = 0 en la nueva suite (`test_advanced_scoring.py`, `test_advanced_command.py`, y el test cruzado de rate-limit compartido)
- Los 17 criterios de aceptación del `architect` y los 5 hallazgos no bloqueantes de `security` (1-5) tienen al menos un test verde que los cubre explícitamente (ver mapeos arriba) — el hallazgo 6 queda documentado como no aplicable a esta spec

### Veredicto de `qa` — Momento 1

**Spec lista para pasar a `implementer`.** No se identificaron gaps de testabilidad que requieran devolver la spec a `architect`: ambos módulos nuevos siguen el mismo patrón ya probado en el proyecto (funciones puras / orquestación con HTTP mockeado), no hay lógica crítica enterrada en constructores ni estáticos no testeables, y los efectos secundarios (FMP, Telegram) ya están aislados por diseño (inyección de `clients`/`rate_limiter`, mismo patrón que `query_handler.py`). El único punto que merece atención especial durante Ralph Loop (Momento 2) es el hallazgo 2 de `security` — verificar con el test cruzado específico, no solo con lectura de código, que la key del rate limiter es realmente `str(chat_id)` sin prefijo.
