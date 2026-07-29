# Spec: ROE/deuda/dividendos/Veredicto + VIX + etiquetas "estimado" + explicaciones dummy (renta variable/beta/WACC) [Iter-1]

**Renombrada desde `SDD_roe_deuda_dividendos_veredicto.md`** — el alcance creció más allá de ROE/deuda/dividendos/Veredicto (título original) para cubrir toda la iniciativa de Daniela de "contenido financiero más completo y explicado": VIX como proxy de volatilidad, etiquetado explícito de datos aproximados, y explicaciones en lenguaje llano + procedencia de conceptos que hoy aparecen sin explicar (renta variable, beta, WACC). Es la misma spec viva, no una spec nueva — sigue en Iter-1 porque `implementer` todavía no empezó a ejecutar nada (no hay scope freeze todavía).

**Rol:** `architect` (spec base, expandida).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa el impacto de **las dos** llamadas HTTP nuevas que agrega esta spec — `/key-metrics` para el ticker propio (ya descripta en la versión original de esta spec) y `/quote?symbol=^VIX` (nueva, sección "Ampliación de alcance" más abajo). Ambas reutilizan wrappers de `fmp_client.py` ya auditados (`get_key_metrics`, `get_quote`), sin código HTTP nuevo. **`security` también revisa la superficie nueva de la Ampliación #2** (manejo de mensajes largos, ver esa sección más abajo): llamadas adicionales a `Message.chat.send_message` de `python-telegram-bot`, mismo canal ya auditado en `SDD_mensaje_cargando.md`, sin dato sensible nuevo ni endpoint nuevo. Luego `qa` agrega criterios de cobertura. **Las 2 preguntas bloqueantes de la versión original de esta spec ya están resueltas** (ver sección siguiente) — no quedan preguntas abiertas bloqueantes conocidas al cierre de esta iteración (ver también la sección final "Preguntas abiertas").

---

## Resolución de las preguntas bloqueantes originales (2026-07-29)

La versión original de esta spec (`SDD_roe_deuda_dividendos_veredicto.md`) dejó 2 preguntas bloqueantes para Daniela. Las resolvió en conversación directa — quedan **cerradas**, no se reabren:

### (a) — RESUELTA: no se verifica con `curl` real antes de implementar

Daniela decidió **no bloquear el diseño con una verificación manual previa**. Se implementa con las guardas defensivas explícitas ya diseñadas en la Decisión #2 original (`extract_key_metrics_extras`: guarda de tipo `isinstance(value, (int, float))`, `None` si el campo falta, viene `None`, o no es numérico) — el mismo criterio B1-B4 que ya usa todo `rules.py`/`valuation.py`. Si en producción algún campo (`roe`, `debtToEquity`, `netDebtToEBITDA`, `dividendYield`, `payoutRatio`) resulta no estar disponible en el plan gratuito, o la convención de unidades (`0.15` vs `15`) resulta distinta de lo asumido, se descubre en uso real — igual que pasó con `/key-metrics-ttm` (README.md, ya documentado como hallazgo post-hoc, no bloqueó el diseño original del MVP).

**Consecuencia de diseño:** el formato `*100` de la tabla de la Decisión #3 original (`roe*100`, `dividend_yield*100`, `payout_ratio*100`) queda como **supuesto de implementación, no como hecho verificado**. Si `implementer` descubre en pruebas manuales (o Daniela reporta un `1500.00%` en vez de `15.00%`) que la convención real es distinta, es una corrección de formato dentro del mismo criterio de aceptación (no una regresión, no requiere spec patch — mismo tratamiento que constantes ajustables como `MARKET_RISK_PREMIUM`).

Esta misma resolución (**"no bloquear con `curl`, implementar con guardas defensivas, descubrir en uso real"**) es también el criterio que se aplica al campo nuevo de esta ampliación, el VIX (ver sección siguiente) — no hay doble estándar entre ambos hallazgos.

### (b) — RESUELTA: 1 llamada HTTP nueva (`/key-metrics` del ticker propio) aceptada explícitamente

Daniela confirmó explícitamente que la llamada nueva a `/key-metrics` para el ticker propio (documentada en la sección "Corrección empírica de la premisa" más abajo, sin cambios) es aceptable, dentro del presupuesto ya documentado. **Nota:** esta ampliación de alcance agrega una **segunda** llamada nueva (VIX, ver abajo) — el presupuesto total se recalcula en la sección "Ampliación de alcance" y en "Artefactos a crear/modificar" (actualización de `README.md`).

---

## Contexto

Daniela pidió agregar 4 piezas de contenido financiero que hoy el bot no muestra, **sin agregar complejidad matemática ni requests nuevos a FMP** (restricción explícita del encargo original):

1. **ROE** (Return on Equity) — eficiencia sobre el patrimonio, hoy ausente.
2. **Debt-to-Equity** (y opcionalmente Net Debt/EBITDA) — apalancamiento de largo plazo; hoy solo existe el Ratio de Liquidez (`rules.calculate_liquidity_ratio`), que mide solvencia de **corto plazo**, no estructura de deuda de largo plazo.
3. **Dividend Yield** (y opcionalmente Payout Ratio) — hoy no aparece en ningún lado de la respuesta.
4. **Veredicto final / resumen ejecutivo** — no es un dato nuevo, es una síntesis textual de datos que `rules.py`/`valuation.py`/`risk_fit.py` **ya calculan hoy**.

Restricción dura confirmada por Daniela: **ninguna fórmula existente cambia** (PER, P/S, liquidez, margen bruto, Múltiplos, Graham, DCF quedan byte a byte iguales). Los 4 puntos son estrictamente aditivos.

La premisa con la que llegó el encargo era: *"el campo `roe` viene pre-calculado en `/key-metrics`, endpoint que el bot YA llama hoy para el ticker propio — no hay que agregar ninguna llamada HTTP nueva"*. **Esta premisa es parcialmente incorrecta** — ver la sección siguiente, verificada por inspección directa del código antes de diseñar nada (mismo estándar de "verificar empíricamente, no asumir" que ya se usó para el hallazgo del 402 símbolo-premium en `SDD_fmp_402_simbolo_premium.md`).

**Ampliación de alcance (2026-07-29):** en la misma conversación donde Daniela resolvió las 2 preguntas bloqueantes de arriba, pidió sumar 3 puntos más a esta misma iniciativa (no specs separadas — todo es "contenido financiero más completo y explicado"):

5. **VIX** (CBOE Volatility Index, símbolo `^VIX`) como proxy de volatilidad de mercado — reemplazo aceptado de lo que originalmente pidió como "Fear & Greed de CNN" (rechazado por no tener API oficial gratuita; cualquier fuente gratis sería scraping no oficial, contra el criterio de este proyecto de solo usar fuentes documentadas).
6. **Etiquetado explícito "estimado"/"aproximado"** en cualquier dato del resumen que sea una aproximación — no solo el VIX.
7. **Explicaciones dummy + procedencia del dato** para "renta variable", "beta" y "WACC" — hoy aparecen sin explicar en lenguaje llano en ningún lado de la respuesta.

Ver la sección "Ampliación de alcance — VIX, etiquetas 'estimado', explicaciones dummy" más abajo para el diseño completo de estos 3 puntos.

**Ampliación de alcance #2 (2026-07-29, hallazgo urgente):** en paralelo, Daniela reportó que en Telegram el texto del análisis "le aparece a la mitad" (cortado). Inspección del código confirma que **no existe ningún manejo del límite de 4096 caracteres de Telegram** en todo el proyecto — ni `reply_text` ni el `.edit_text` que ya implementa `SDD_mensaje_cargando.md` truncan ni parten el mensaje; cuando el texto supera el límite, Telegram rechaza el `sendMessage`/`editMessageText` completo (`BadRequest: Message is too long`), consistente con lo que Daniela ve. Esto se agrega **antes** de pasar a `security` porque el contenido nuevo de los puntos 1-7 de arriba hace el mensaje más largo, agravando un problema que ya existe hoy. Ver la sección "Ampliación de alcance #2 — manejo de mensajes largos (límite 4096 de Telegram)" más abajo para el diseño completo, incluida la medición real de cuánto se excede el límite.

---

## Corrección empírica de la premisa (verificado por inspección de código, no supuesto)

*(sección sin cambios respecto a la versión original de la spec — se mantiene íntegra porque sigue siendo la base del diseño de los puntos 1-4)*

### Hallazgo 1 — `/key-metrics` NO se llama hoy para el ticker propio, solo para los peers

Inspeccioné `src/investbot/query_handler.py::fetch_and_analyze` (líneas 81-160 en la versión pre-esta-spec) completo. La única invocación de `fmp_client.get_key_metrics` en todo el archivo está en la función interna `_get_metrics_for_peer` (línea 128), que se pasa como `get_peer_metrics_fn` a `peers.get_peer_pe_average` (línea 135) — **se llama una vez por cada peer del sector** (`limit=1`), nunca para el ticker que Daniela está consultando. Confirmado con:

```
$ grep -n "get_key_metrics(" src/investbot/query_handler.py
128:            data = await fmp_client.get_key_metrics(
```

Una sola aparición, dentro de `_get_metrics_for_peer`. El único fixture existente para `/key-metrics` (`tests/fixtures/adobe/peers_metrics.json`) confirma esto: solo tiene entradas para `MSFT`/`ORCL`/`CRM` (los peers del sector Technology, ver `peers.py::PEERS_BY_SECTOR`), **no para `ADBE`** (el ticker propio del caso de referencia del proyecto).

**Esto contradice tanto la premisa del encargo como la documentación existente:** tanto `README.md` (línea 39, "`/key-metrics` (anual — usado también para el PER de peers...)", y la tabla de la línea 64, "Datos propios del ticker | 6 | .../`key-metrics`") como `SDD_investbot_mvp.md` (línea 184, misma tabla) **documentan `/key-metrics` como una de las 6 llamadas "propias del ticker"** — pero el código real nunca hace esa llamada para el ticker propio, solo para peers. Es un desalineamiento preexistente entre documentación e implementación, no introducido por esta spec — lo señalo porque cambia el análisis de costo de esta spec.

**Consecuencia para esta spec:** leer ROE/deuda/dividendos del ticker propio **sí requiere una llamada HTTP nueva** (`fmp_client.get_key_metrics` para `ticker`, no solo para peers) — la función ya existe en `fmp_client.py` (no hay que escribir wrapper HTTP nuevo, cero riesgo de seguridad nuevo por endpoint desconocido), pero **es una invocación adicional que hoy no ocurre**, no una reutilización de datos ya en memoria.

**Por qué esto no rompe la restricción de fondo de Daniela, con matiz:** el presupuesto de requests ya documentado en `README.md` (tabla "Presupuesto de requests por consulta") **ya contaba esta llamada** en su total de "9-12 requests por consulta completa" — el texto decía 6 llamadas "propias del ticker" incluyendo `/key-metrics`, aunque el código solo hacía 5. Al agregar la llamada real, el conteo pasa a coincidir por fin con lo que la documentación ya afirmaba (9-12 sin cambios en el techo superior/inferior). No hay impacto neto en el presupuesto de 250 req/día ni en las ~20-27 consultas/día soportadas — pero **es la primera vez que el código hace esa llamada**, así que se documenta como decisión de diseño explícita, no como "cero cambio" literal.

### Hallazgo 2 — presencia real de `roe`/`debtToEquity`/`netDebtToEBITDA`/`dividendYield`/`payoutRatio` en el plan gratuito: NO verificada (y NO se va a verificar, ver resolución (a) arriba)

Revisé los dos únicos artefactos que podrían probar esto:

- `tests/fixtures/adobe/peers_metrics.json` — solo contiene `symbol` y `earningsYield`. No prueba ni descarta la presencia de los 5 campos nuevos.
- `tests/test_fmp_client.py::test_get_profile_y_key_metrics` (línea ~203-216) — usa `httpx.Response(200, json=[{"roe": 0.5}])`, pero es un **mock sintético escrito para probar que el wrapper pasa el JSON tal cual**, no una captura real de FMP. No es evidencia de que el plan gratuito devuelva ese campo.

Ningún fixture en el repo está documentado como "origen: real" para `/key-metrics` (a diferencia de `tests/fixtures/fmp/402_symbol_premium_real.txt`, que sí es una captura `curl` real). **Daniela ya resolvió esto (ver "Resolución (a)" arriba): se implementa con guardas defensivas, no se bloquea con verificación manual.**

---

## Estado actual

- `src/investbot/fmp_client.py::get_key_metrics` (líneas 221-235): wrapper genérico ya existente, devuelve `list[dict]` tal cual FMP responde, sin transformar campos. Se le puede pedir cualquier ticker con `limit=1` sin cambios de firma.
- `src/investbot/fmp_client.py::get_quote` (líneas 142-153): wrapper genérico ya existente y ya auditado por `security` (Iter-1 de `SDD_investbot_mvp.md`) — recibe cualquier `ticker: str` como query param `symbol=`, sin validación de forma (acepta `"ADBE"`, `"MELI"`, o cualquier string), devuelve el primer elemento de la lista de FMP o `None`. **Se puede reutilizar tal cual para pedir `/quote?symbol=^VIX`** — no hace falta escribir ninguna función nueva en `fmp_client.py` (ver "Ampliación de alcance" más abajo).
- `src/investbot/query_handler.py::fetch_and_analyze` (líneas 81-271): trae `quote`/`profile`/`income_statements`/`balance_sheets`/`cash_flows` para el ticker propio (líneas 83-93), aborta con mensaje claro si falta cualquiera de los 5 (línea 95-96). Luego resuelve peers (líneas 124-137) y Y (líneas 140-149), arma ratios/pilares/escenarios/momentum/comparación de peers (líneas 151-255), y llama a `summary.build_summary` (línea 257) pasándole dicts planos ya armados (`ratios_dict`, `pillars_dict`, `risk_fit_dict`, `momentum_dict`, `peer_comparison_dict`).
- `src/investbot/rules.py` (130 líneas): funciones puras con guardas explícitas (B3: liquidez con pasivos circulantes = 0 → `None` + flag; B4: PER con EPS ≤ 0 → `None` + flag). `evaluate_pillars` (líneas 100-130) devuelve `PillarsResult` con `precio_razonable: Optional[bool]` ya igual al flag "barata"/"cara" del escenario Conservador (calculado en `query_handler.py` línea 192-196: `barata=(precio_actual < conservador.valor_justo_total if conservador.valor_justo_total is not None else None)`).
- `src/investbot/risk_fit.py` (39 líneas): `evaluate_risk_fit(beta, perfil)` devuelve `RiskFitResult` con `encaja: bool`, `beta: float`, `etiqueta_activo: str = "renta variable"` (constante `RENTA_VARIABLE_LABEL`, línea 17) — siempre calculable (beta siempre tiene fallback `1.0` en `query_handler.py` línea 100). **Hoy `"renta variable"` es solo una etiqueta de texto sin explicación** — se muestra en `summary.build_risk_fit_section` pero nunca se aclara qué significa. Lo mismo con `beta`: se muestra el número (`beta:.2f}`) pero nunca se explica conceptualmente.
- `src/investbot/market_context.py` (173 líneas): `calculate_momentum` (líneas 41-89) y `compare_to_peers`/`PeerComparisonResult` (líneas 92-173) — funciones puras, sin I/O, reciben datos ya resueltos. Docstring del módulo (líneas 1-16) documenta explícitamente "Presupuesto de requests FMP: cero llamadas HTTP nuevas" — **esta afirmación deja de ser válida para el módulo en conjunto una vez agregada la función de VIX** (ver más abajo; las funciones de momentum/peers siguen sin requests nuevos, la función de VIX sí depende de un dato que requiere un fetch nuevo, aunque la función en sí sigue siendo pura).
- `src/investbot/valuation.py::calculate_wacc` (líneas 128-181): WACC simplificado (CAPM + costo de deuda ponderado), documentado internamente con fórmula y guardas, pero **el valor de WACC nunca se muestra al usuario** — solo se usa internamente para el DCF. La única mención textual de "WACC" en toda la respuesta al usuario es la nota de transparencia final de `summary.build_summary` (línea 379): `"_El DCF es una aproximación con supuestos simplificados de WACC._"` — sin explicar qué es el WACC.
- `src/investbot/summary.py` (391 líneas): `build_summary` (línea 301) arma la respuesta en el orden: título+intro Tienda de Limonada → Ratios clave → Valor Justo (3 escenarios) → Pilares de buena empresa → Contexto de mercado → Encaje de riesgo → notas de transparencia. Cada sección tiene su propia función `build_*_section`, pura, testeada en aislamiento (`tests/test_summary.py`). `build_market_context_section` (líneas 215-271) termina con una nota (líneas 267-270) que dice explícitamente: `"_Nota: el momentum es un proxy simple de precio, no un índice de sentimiento de mercado (VIX/Fear & Greed)._"` — **esta nota queda desactualizada una vez que el VIX sí se muestra** (ver "Ampliación de alcance").
- `tests/fixtures/adobe/peers_metrics.json`: fixture sintético de `/key-metrics`, solo con `earningsYield` para 3 peers.
- `tests/fixtures/adobe/quote.json`: fixture sintético de `/quote` para `ADBE` — campos `symbol`, `name`, `price`, `marketCap`, `volume`, `exchange` (no incluye `yearHigh`/`priceAvg50`/etc., que otros tests deben mockear aparte para probar momentum).
- `tests/test_query_handler.py::_adobe_router` (líneas 20-40): el router de test mapea `/stable/key-metrics` por el query param `symbol`, buscando en `adobe_fixtures["peers_metrics"]` — si el `symbol` no está en ese dict (como pasaría hoy con `ADBE`, el ticker propio), devuelve `[]`. También mapea `/stable/quote` por `symbol`, hoy solo conoce el `symbol=ADBE` del ticker propio.

---

## Estado objetivo

1. `fetch_and_analyze` obtiene, además de lo que ya trae, el `/key-metrics` (anual, `limit=1`) del **ticker propio** — de forma *best-effort*: si falla o viene vacío, el resto del análisis sigue funcionando exactamente igual que hoy (nunca aborta el análisis completo por esto).
2. `rules.py` gana una función pura de solo-lectura con guardas (`extract_key_metrics_extras`) que lee `roe`, `debtToEquity`, `netDebtToEBITDA`, `dividendYield`, `payoutRatio` del dict de `/key-metrics` del ticker propio, sin calcular nada, nunca crashea con datos ausentes o de tipo inesperado.
3. `summary.py` gana una nueva sección ("Rentabilidad, deuda de largo plazo y dividendos") ubicada entre "Ratios clave" y "Valor Justo", que se omite por completo si ninguno de los 5 campos está disponible.
4. `summary.py` gana un "Veredicto" — un párrafo corto ubicado al **inicio** de la respuesta (después del título, antes de la intro pedagógica de Tienda de Limonada), armado exclusivamente a partir de `pillars` y `risk_fit` (dicts que `build_summary` ya recibe hoy) — cero datos nuevos, cero requests nuevos, cero fórmula nueva.
5. Ninguna fórmula existente (PER, P/S, liquidez, margen bruto, Múltiplos, Graham, DCF) cambia una sola línea de lógica.
6. **(Ampliación)** `fetch_and_analyze` obtiene, de forma *best-effort*, la cotización del VIX (`/quote?symbol=^VIX`, reutilizando `fmp_client.get_quote` tal cual). `market_context.py` gana una función pura `extract_vix_context` que lee el valor del VIX ya resuelto, sin fórmula, sin umbral cualitativo. `summary.build_market_context_section` gana un bullet de VIX (omitido si no hay dato) etiquetado explícitamente como "estimado/aproximado", con una aclaración explícita de que no es un índice compuesto de sentimiento.
7. **(Ampliación)** Todo dato mostrado en el resumen que sea una aproximación (no un valor reportado/calculado exacto) dice explícitamente "estimado" o "aproximado" en el texto — auditoría completa en la sección "Ampliación de alcance" más abajo.
8. **(Ampliación)** `summary.build_risk_fit_section` gana 2 líneas de explicación dummy fijas (qué es "renta variable", qué es "beta") con procedencia del dato. La nota de transparencia de `build_summary` sobre el DCF/WACC se expande para explicar qué es el WACC en criollo y aclarar que es un cálculo propio del bot, no un dato de FMP.
9. **(Ampliación #2)** `summary.py` gana `build_summary_parts` (devuelve la lista de secciones sin unir); `query_handler.py` gana `chunk_for_telegram` y la lógica de entrega multi-mensaje en `_run_analysis`, de forma que ningún análisis se pierde o queda truncado por el límite de 4096 caracteres de Telegram — se reparte en varios mensajes, cortando siempre en un límite de sección, nunca a mitad de línea/oración.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch)*

### Decisiones 1-6 — ROE/deuda/dividendos/Veredicto (sin cambios respecto a la versión original de esta spec)

1. **Nueva llamada a `fmp_client.get_key_metrics` para el ticker propio, *best-effort*, no bloqueante.** En `fetch_and_analyze`, después del bloque de abort-check existente (línea 95-96) que ya cubre `quote`/`profile`/`income_statements`/`balance_sheets`/`cash_flows`, se agrega:

   ```python
   try:
       own_metrics_list = await fmp_client.get_key_metrics(
           clients.fmp_http, clients.fmp_api_key, ticker, limit=1
       )
       own_metrics = own_metrics_list[0] if own_metrics_list else None
   except fmp_client.FMPError:
       own_metrics = None
   ```

   **Deliberadamente no participa del abort-check existente.** Si esta llamada falla (402, 429, timeout, lo que sea) o devuelve `[]`, el análisis completo sigue — el usuario ya recibe hoy un análisis útil sin ROE/deuda/dividendos, y esta spec es aditiva, no debe degradar la disponibilidad actual del bot por un dato secundario. Mismo principio de "nunca inventar, nunca crashear, degradar con gracia" que ya usa el resto del proyecto (B3/B4 de `rules.py`).

2. **`rules.py` — nueva función pura `extract_key_metrics_extras`, sin fórmula, solo lectura con guarda de tipo:**

   ```python
   @dataclass
   class KeyMetricsExtras:
       roe: Optional[float]
       debt_to_equity: Optional[float]
       net_debt_to_ebitda: Optional[float]
       dividend_yield: Optional[float]
       payout_ratio: Optional[float]

   def extract_key_metrics_extras(metrics: Optional[dict]) -> KeyMetricsExtras:
       """Lee ROE, Debt-to-Equity, Net Debt/EBITDA, Dividend Yield y Payout
       Ratio del dict más reciente de /key-metrics (anual) del ticker propio.
       No calcula nada — FMP ya precalcula estos campos. Guarda de tipo: si
       el campo está ausente, es None, o no es int/float, se descarta como
       None (nunca crashea, nunca inventa un valor, nunca intenta parsear
       strings). No filtra por signo ni rango — un ROE negativo, un
       Debt-to-Equity fuera de rango típico, o un Payout Ratio > 100% son
       señales financieras reales (patrimonio negativo, sobre-endeudamiento,
       reparto de dividendos pese a pérdidas) y se muestran tal cual, sin
       interpretación numérica adicional (eso sería agregar complejidad
       matemática nueva, fuera de alcance de esta spec).
       """
       def _num(key: str) -> Optional[float]:
           if not metrics:
               return None
           value = metrics.get(key)
           return value if isinstance(value, (int, float)) else None

       return KeyMetricsExtras(
           roe=_num("roe"),
           debt_to_equity=_num("debtToEquity"),
           net_debt_to_ebitda=_num("netDebtToEBITDA"),
           dividend_yield=_num("dividendYield"),
           payout_ratio=_num("payoutRatio"),
       )
   ```

   **Caso especial: `dividend_yield == 0` NO es lo mismo que `None`.** `0` es una señal legítima y es justamente la pregunta que Daniela quiere que el bot conteste ("¿reparte plata o no?") — el guard `isinstance(value, (int, float))` acepta `0` y `0.0` (Python: `isinstance(0, (int, float))` es `True`), así que `0` se propaga como dato válido, no como ausente. Mismo tratamiento para `payout_ratio == 0`.

3. **`summary.py` — nueva sección `build_extras_section`, ubicada entre "Ratios clave" y "Valor Justo".** Se omite la sección completa (retorna `None`, no un string vacío ni un bloque de puros "N/D") si los 5 campos vienen `None` — mismo criterio ya usado en `build_valuation_scenarios_section` cuando los 3 escenarios quedan sin valor justo (Spec Patch Iter-4, C1: "se omite la sección completa" en vez de mostrar ruido). Si al menos un campo está disponible, se muestran solo las líneas de los campos presentes (mismo patrón que `ratios_lines` en `build_summary`: cada bullet es condicional a `is not None`, sin placeholder "N/D" para los ausentes — consistencia con el resto de la sección "Ratios clave", que tampoco usa "N/D" para bullets individuales).

   Textos dummy-friendly propuestos (citando siempre qué representa el campo, aclarando que es un dato ya calculado por FMP y no una fórmula del bot — mismo principio de transparencia que el resto de `summary.py`):

   | Campo | Bullet (fórmula/origen) | Texto dummy |
   |---|---|---|
   | `roe` | `ROE (Rentabilidad sobre el Patrimonio): {roe*100:.1f}%` _(fórmula: Ganancia Neta / Patrimonio de los Accionistas — dato ya calculado por FMP, el bot no lo recalcula)_ | "Por cada $100 que pusieron los dueños, la empresa ganó ${roe*100:.0f} este año." |
   | `debt_to_equity` | `Deuda/Patrimonio (Debt-to-Equity): {debt_to_equity:.2f}` _(fórmula: Deuda Total / Patrimonio de los Accionistas — dato de FMP)_ | "Cuánta deuda tiene la empresa comparada con lo que es de los dueños." |
   | `net_debt_to_ebitda` (opcional) | `Deuda Neta / EBITDA: {net_debt_to_ebitda:.2f}x` _(dato de FMP)_ | "Cuántos años de ganancia operativa le tomaría pagar toda su deuda." |
   | `dividend_yield` | `Dividend Yield: {dividend_yield*100:.2f}%` _(dato de FMP)_ | Si `== 0`: "Esta empresa no reparte dividendos actualmente — reinvierte todo." Si `> 0`: "Por cada $100 invertidos, la empresa te devuelve aproximadamente ${dividend_yield*100:.2f} al año en dividendos, antes de impuestos." |
   | `payout_ratio` (opcional) | `Payout Ratio: {payout_ratio*100:.1f}%` _(dato de FMP)_ | "% de la ganancia que reparte como dividendo — el resto lo reinvierte en el negocio." |

   **El multiplicador `*100` de la tabla es un supuesto a confirmar en uso real** (ver "Resolución (a)" al principio de esta spec — no se verifica con `curl` antes de implementar, se descubre en producción si la convención de unidades real es distinta).

4. **`summary.py` — nuevo "Veredicto", ubicado al inicio, construido exclusivamente a partir de `pillars` y `risk_fit` ya recibidos por `build_summary` — cero parámetros nuevos de datos financieros, cero fórmula nueva:**

   ```python
   def build_veredicto_section(*, pillars: dict, risk_fit: dict) -> str:
       """Resumen ejecutivo de una frase. Se arma puramente a partir de
       `pillars` (ya calculado por rules.evaluate_pillars) y `risk_fit` (ya
       calculado por risk_fit.evaluate_risk_fit) — los mismos dicts que
       build_summary ya recibe hoy. No lee ratios, no lee ROE/deuda/
       dividendos, no aplica ningún umbral nuevo sobre datos financieros:
       "qué mirar con cuidado" es una enumeración de qué pilares booleanos
       ya vienen en False, no una interpretación numérica nueva.
       """
       precio_razonable = pillars.get("precio_razonable")
       if precio_razonable is True:
           precio_txt = "parece *barata* según el valor justo estimado (escenario conservador)"
       elif precio_razonable is False:
           precio_txt = "parece *cara* según el valor justo estimado (escenario conservador)"
       else:
           precio_txt = "no pude determinar si está cara o barata con los datos disponibles"

       claves = ["ingresos_crecientes", "utilidades_crecientes", "deuda_controlada", "precio_razonable"]
       solidos = sum(1 for k in claves if pillars.get(k) is True)
       debiles = [k for k in claves if pillars.get(k) is False]

       encaje_txt = "SÍ encaja" if risk_fit.get("encaja") else "NO encaja"

       cuidado_txt = ""
       if debiles:
           etiquetas = {
               "ingresos_crecientes": "ingresos",
               "utilidades_crecientes": "utilidades",
               "deuda_controlada": "deuda",
               "precio_razonable": "precio",
           }
           cuidado_txt = f" Mirá con cuidado: {', '.join(etiquetas[k] for k in debiles)}."

       return (
           f"*En una frase:* {precio_txt}, con {solidos}/4 pilares sólidos, "
           f"y {encaje_txt} con tu perfil de riesgo ({risk_fit.get('perfil')})."
           f"{cuidado_txt}"
       )
   ```

   **Nunca crashea:** `pillars` siempre tiene las 4 claves (garantizado por `PillarsResult`, incluso cuando `precio_razonable` es `None`); `risk_fit` siempre tiene `encaja`/`perfil` (garantizado por `RiskFitResult`, `beta` siempre tiene fallback en `query_handler.py`).

   **Deliberadamente NO usa ROE/deuda/dividendos en el veredicto** — mantenerlos fuera evita que el veredicto necesite un umbral nuevo (ej. "¿qué ROE es bueno?") que sería math nueva no acordada con Daniela. Si en el futuro Daniela quiere que el veredicto los incluya, es una spec patch separada con su propio umbral acordado explícitamente, no algo que `implementer` deba inventar.

5. **Orden final de `build_summary` (reemplaza el orden documentado en el docstring actual, línea 320-321):**

   ```
   Título → Veredicto → Intro Tienda de Limonada → Ratios clave →
   Rentabilidad/deuda/dividendos (nueva, se omite si no hay datos) →
   Valor Justo (3 escenarios) → Pilares de buena empresa →
   Contexto de mercado (incluye VIX, ver Ampliación de alcance) →
   Encaje de riesgo (incluye explicaciones dummy, ver Ampliación de alcance) →
   Notas de transparencia (incluye explicación de WACC, ver Ampliación de alcance)
   ```

   `build_summary` gana un parámetro nuevo `extras: Optional[dict] = None` (default para no romper firma de llamadores existentes en tests) con las 5 claves de `KeyMetricsExtras`, y un parámetro nuevo `vix: Optional[dict] = None` (ver Ampliación de alcance). La lista `parts` filtra `None` antes de unir con `"\n\n"` (ya existe ese patrón implícito — hay que hacerlo explícito porque ahora dos elementos pueden ser `None`: `build_extras_section` cuando no hay datos).

6. **Ningún dato de ROE/deuda/dividendos participa de ningún pilar, escenario de valoración, ni clasificación barata/cara.** Es puramente informativo — la restricción de "ninguna fórmula existente cambia" se cumple porque estos campos nunca entran a `rules.evaluate_pillars`, `valuation.py`, ni `risk_fit.py`.

### Decisiones 7-13 — Ampliación de alcance: VIX, etiquetas "estimado", explicaciones dummy (NUEVAS)

7. **VIX — se reutiliza `fmp_client.get_quote` tal cual, sin wrapper HTTP nuevo.** `get_quote(client, api_key, ticker)` ya acepta cualquier string como `ticker` (se pasa tal cual como query param `symbol=`, vía `params=` de `httpx` — nunca f-string, ya auditado). Se le pasa la constante `"^VIX"`:

   ```python
   # market_context.py
   VIX_SYMBOL = "^VIX"
   ```

   En `query_handler.py::fetch_and_analyze`, después del bloque *best-effort* de `own_metrics` (Decisión #1), se agrega otro bloque *best-effort* idéntico en estructura:

   ```python
   try:
       vix_quote = await fmp_client.get_quote(
           clients.fmp_http, clients.fmp_api_key, market_context.VIX_SYMBOL
       )
   except fmp_client.FMPError:
       vix_quote = None
   ```

   **Deliberadamente no participa del abort-check existente**, mismo principio que la Decisión #1: si el plan gratuito no expone `^VIX` en `/quote` (no verificado — mismo criterio de "Resolución (a)": no se bloquea con `curl`, se descubre en uso real), o la llamada falla por cualquier motivo, el resto del análisis sigue exactamente igual, solo se omite el bullet de VIX.

8. **`market_context.py` — nueva función pura `extract_vix_context`, sin fórmula, sin umbral cualitativo:**

   ```python
   @dataclass
   class VixResult:
       valor: Optional[float]
       disponible: bool

   def extract_vix_context(quote: Optional[dict]) -> VixResult:
       """Lee el nivel del VIX (CBOE Volatility Index) desde un /quote ya
       resuelto para VIX_SYMBOL ("^VIX"). Función pura, sin I/O — el fetch
       ocurre en query_handler.py, best-effort (Decisión #7), mismo patrón
       que rules.extract_key_metrics_extras (guarda de tipo, nunca crashea).

       Deliberadamente NO clasifica el valor en "alta"/"baja"/"moderada"
       volatilidad — eso requeriría un umbral numérico nuevo no acordado
       con Daniela (mismo criterio que la Restricción de "no agregar
       interpretación numérica nueva" ya vigente para ROE/deuda/dividendos,
       Decisión #2/#6). El VIX se muestra como dato crudo con su
       explicación textual en summary.py, nunca como semáforo. Si Daniela
       quiere umbrales de "alta/baja volatilidad" más adelante, es una spec
       patch separada con el umbral acordado explícitamente.
       """
       if not quote:
           return VixResult(valor=None, disponible=False)
       valor = quote.get("price")
       valor = valor if isinstance(valor, (int, float)) else None
       return VixResult(valor=valor, disponible=valor is not None)
   ```

   Se reutiliza el campo `price` de `/quote` (mismo campo que ya usa `fetch_and_analyze` para el ticker propio, línea 103: `precio_actual = quote.get("price") or profile.get("price")`) — no se introduce ningún campo nuevo sin precedente en el código. **No se lee/muestra la variación % diaria del VIX** (campo tipo `changePercentage`, no verificado en ningún fixture ni test del repo) — Daniela pidió el VIX como proxy de volatilidad, no un timing diario; agregar la variación % sería complejidad no pedida y un campo de FMP sin precedente de uso en este proyecto. Si Daniela lo quiere después, es alcance nuevo.

   **Actualización de docstring de módulo requerida:** `market_context.py` (líneas 1-16) declara hoy "Presupuesto de requests FMP: cero llamadas HTTP nuevas" — afirmación que sigue siendo cierta para `calculate_momentum`/`compare_to_peers` (sin cambios), pero ya no es cierta para el módulo en conjunto: `extract_vix_context` en sí sigue siendo pura/sin I/O, pero el dato que recibe (`quote` de `^VIX`) sí requiere un fetch nuevo (Decisión #7). El docstring se actualiza para aclarar esta distinción, no se borra la afirmación original sobre momentum/peers.

9. **`summary.py` — nuevo bullet de VIX dentro de `build_market_context_section`, con parámetro nuevo `vix: Optional[dict] = None`:**

   ```python
   def build_market_context_section(
       *, precio_actual: float, momentum: dict, peer_comparison: dict,
       vix: Optional[dict] = None,
   ) -> str:
       ...  # bullets existentes de momentum/peer comparison sin cambios
       if vix and vix.get("disponible"):
           lines.append(
               f"- VIX (CBOE Volatility Index): {vix['valor']:.2f}"
           )
           lines.append(
               "  _Estimado/aproximado — mide la volatilidad implícita "
               "esperada de opciones sobre el S&P 500 (el mercado en "
               "general), no del ticker que consultaste. NO es lo mismo "
               "que un índice compuesto de sentimiento tipo \"Fear & "
               "Greed\" (ese combina varias señales distintas; el VIX "
               "solo mide volatilidad). Dato de FMP (símbolo ^VIX)._"
           )
       lines.append(
           "\n_Nota: el momentum de arriba es un proxy simple de precio "
           "del ticker consultado, no del mercado en general. El VIX (si "
           "aparece más arriba) es una aproximación de la volatilidad "
           "esperada del mercado en general, no del ticker — tampoco es "
           "un índice de sentimiento compuesto._"
       )
       return "\n".join(lines)
   ```

   **Reemplaza la nota final actual** (líneas 267-270 de `summary.py`: `"_Nota: el momentum es un proxy simple de precio, no un índice de sentimiento de mercado (VIX/Fear & Greed)._"`), que queda desactualizada apenas el VIX se muestra de verdad — la nota nueva sigue siendo válida tanto si el VIX está disponible (aclara qué es y qué no es) como si no lo está (la mención "si aparece más arriba" cubre ambos casos sin necesidad de dos textos distintos).

   El bullet de VIX se omite por completo (no aparece, ni como "N/D") si `vix` es `None` o `vix.get("disponible")` es `False` — mismo criterio de "degradar con gracia sin ruido" que el resto del proyecto (Decisión #3, `build_extras_section`; Spec Patch Iter-4 C1, `build_valuation_scenarios_section`).

10. **Etiquetado "estimado"/"aproximado" — auditoría completa de la respuesta actual + lo agregado por esta spec.** Revisé cada dato mostrado en `summary.py` (actual + lo que agregan los puntos 1-4 de esta spec) para decidir si amerita la etiqueta:

    | Dato | ¿Es una aproximación? | Acción |
    |---|---|---|
    | VIX | Sí — volatilidad implícita de opciones, no un índice de sentimiento compuesto | **Nuevo:** bullet dice "Estimado/aproximado" explícitamente (Decisión #9) |
    | Valor Justo (Múltiplos/Graham/DCF, y su total) | Sí — por definición, los 3 modelos son estimaciones de valor intrínseco, no un hecho reportado | **Cambio:** headers de `build_valuation_scenarios_section` pasan de `"*Rango de Valor Justo (Pesimista \| Conservador \| Optimista):*"` a `"*Rango de Valor Justo estimado (Pesimista \| Conservador \| Optimista):*"`, y de `"*Valor Justo Total: ..."` a `"*Valor Justo Total (estimado): ..."` |
    | DCF (nota de transparencia) | Sí — ya decía "aproximación" | Sin cambio en la palabra, se expande la explicación de WACC (Decisión #12) |
    | WACC (mencionado en la nota del DCF) | Sí — cálculo propio simplificado del bot | Ya cubierto por "el DCF es una aproximación..."; se agrega explicación conceptual (Decisión #12) |
    | ROE, Debt-to-Equity, Net Debt/EBITDA, Dividend Yield, Payout Ratio | **No** — son campos ya calculados/reportados por FMP a partir de datos reales de la empresa, no aproximaciones del bot | Sin cambio — ya están etiquetados con "(dato de FMP)" (Decisión #3), que es la etiqueta correcta para este caso (procedencia, no aproximación) |
    | Y (tasa libre de riesgo, bono del tesoro) | **No** — es una tasa de mercado real reportada por FRED/Treasury.gov, no una aproximación del bot | Sin cambio |
    | Liquidez, Margen Bruto, PER, P/S (Ratios clave) | **No** — son cálculos exactos con fórmula fija sobre datos reales reportados, no estimaciones | Sin cambio |
    | Momentum (% vs máximo/mínimo/promedios de 52 semanas) | **No** — son porcentajes calculados directamente sobre datos de `/quote`, no una aproximación | Sin cambio |
    | Comparación con peers (PER propio vs rango de peers) | **No** es una aproximación numérica en sí — pero el conjunto de comparables **es** un recorte (set fijo de 3-5 peers, no el sector completo) | Ya cubierto por `peers_note` (footer existente: "PER promedio de un set fijo de comparables, no del sector completo") — no requiere la palabra "estimado" nueva, ya es transparente sobre la limitación |
    | Beta | **No** — es un dato reportado por FMP (`/profile`), no una aproximación del bot | Sin cambio en el valor; sí gana explicación conceptual (Decisión #11, distinto problema: falta de explicación, no falta de etiqueta de aproximación) |

    **Conclusión de la auditoría:** los únicos cambios de etiquetado "estimado/aproximado" son el VIX (nuevo) y los headers de Valor Justo (cambio de texto, sin cambio de cálculo). El resto de la respuesta ya está correctamente etiquetado (con "(dato de FMP)"/fórmula) o no amerita la etiqueta porque no es una aproximación sino un valor exacto reportado o calculado con fórmula fija.

11. **`summary.py::build_risk_fit_section` — 2 líneas nuevas de explicación dummy fijas, siempre presentes (no condicionales, porque `beta`/`perfil`/`etiqueta_activo` siempre están disponibles):**

    ```python
    def build_risk_fit_section(risk_fit: dict) -> str:
        encaje_txt = "SÍ encaja" if risk_fit["encaja"] else "NO encaja"
        return (
            f"*Encaje con tu perfil de riesgo ({risk_fit['perfil']}):* {encaje_txt} — "
            f"es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}.\n"
            "_Renta variable = sos dueño de una parte de la empresa (una "
            "acción). A diferencia de la renta fija (bonos, plazo fijo), acá "
            "no hay un pago garantizado: ganás o perdés según cómo le va al "
            "negocio._\n"
            "_Beta mide qué tan volátil es esta acción comparada con el "
            "mercado en general: 1.0 = se mueve igual que el mercado; más de "
            "1.0 = se mueve más fuerte (para arriba y para abajo); menos de "
            "1.0 = se mueve menos. Dato de FMP._"
        )
    ```

    **Texto dummy exacto y ubicación fijados** — `implementer` no debe reformular el contenido, solo integrar el snippet (puede ajustar formato Markdown menor si Telegram lo requiere, pero no el contenido conceptual). Se ubica en `build_risk_fit_section` (no en `build_summary` directamente) porque es la función que ya arma esa sección — mantiene la separación cálculo puro vs. presentación exigida por `qa` (docstring de módulo, línea 8-11).

12. **`summary.py::build_summary` — nota de transparencia del WACC, expandida con explicación conceptual + procedencia explícita:**

    Reemplaza la línea actual (línea 379): `"_El DCF es una aproximación con supuestos simplificados de WACC._"` por:

    ```python
    transparency_lines.append(
        "_El DCF es una aproximación con supuestos simplificados de WACC "
        "(Costo Promedio Ponderado de Capital): combina cuánto le cuesta a "
        "la empresa financiarse con capital propio (accionistas) y con "
        "deuda (bancos/bonistas), ponderado por cuánto usa de cada uno. Es "
        "un cálculo propio del bot (no viene de FMP), simplificado — no "
        "reemplaza el WACC que armaría un analista con datos de mercado "
        "más completos._"
    )
    ```

    **Ubicación sin cambios** (sigue siendo la última línea de `transparency_lines`, dentro de `build_summary`) — solo cambia el contenido del texto, no la posición ni la lógica condicional (siempre se muestra, igual que hoy, porque el DCF siempre se intenta calcular).

12b. **`summary.py::build_summary` — nueva línea de transparencia general, agregada al final de `transparency_lines` (después de la nota de WACC de la Decisión #12), sugerida por `security` en su revisión de esta spec** (hallazgo informativo, no bloqueante: el nuevo "Veredicto" de la Decisión #4 es más directivo que el resto del mensaje — "parece *barata*", "SÍ encaja" — y ninguna parte de la respuesta aclaraba hasta ahora que se trata solo de datos históricos, sin noticias ni eventos recientes):

    ```python
    transparency_lines.append(
        "_Esto es una síntesis de datos financieros históricos, no "
        "asesoramiento financiero profesional ni una recomendación de "
        "inversión. No tiene en cuenta noticias, eventos recientes, "
        "cambios de gestión ni el contexto cualitativo del negocio — "
        "revisá eso vos antes de decidir._"
    )
    ```

    **Siempre se muestra**, sin condición (a diferencia de la nota de Y/treasury_source, que depende de si se pudo obtener) — aplica a toda respuesta del bot, tenga o no Veredicto/datos de valoración calculables.

13. **Procedencia del dato — auditoría de cobertura, sin cambios de código más allá de lo ya descripto en las Decisiones #3, #9, #11, #12, #12b.** Revisé si cada dato mostrado indica de dónde sale (FMP / FRED-Treasury.gov / cálculo propio del bot):

    - **Ya cubierto hoy, sin cambios:** nota general "Datos financieros ... obtenidos de FMP" (footer, siempre presente); "Y (tasa libre de riesgo) obtenida de: {treasury_source}" (footer, condicional a que Y se haya podido obtener); cada bullet de "Ratios clave" cita su fórmula (`_fórmula: ..._`), lo que implícitamente indica "cálculo propio del bot sobre datos de FMP".
    - **Cubierto por esta spec (Decisión #3):** ROE/Debt-to-Equity/Net Debt-EBITDA/Dividend Yield/Payout Ratio, cada uno con "(dato de FMP)" en su bullet.
    - **Cubierto por esta spec (Decisión #9):** VIX, con "Dato de FMP (símbolo ^VIX)" en su bullet.
    - **Cubierto por esta spec (Decisión #12):** WACC, con "Es un cálculo propio del bot (no viene de FMP)" explícito.
    - **Cubierto por esta spec (Decisión #11):** Beta, con "Dato de FMP" explícito en la nueva línea de explicación.
    - **Decisión explícita de NO agregar procedencia por-bullet en Momentum/Comparación con peers:** ambos ya están cubiertos por la nota general de FMP del footer (son cálculos directos sobre `/quote`, sin fórmula propia distinta de "resta y divide", ya documentado en el docstring de `market_context.py`) — agregar "(dato de FMP)" a cada bullet individual de esas 2 secciones sería ruido repetitivo sin agregar información nueva (la nota general del footer ya lo cubre), inconsistente con que tampoco lo hacen hoy los bullets de Pilares de buena empresa (que citan "según el boletín"/"según la foto" en vez de repetir "de FMP"). **No es un hueco, es una decisión de diseño ya cubierta por el patrón existente.**
    - **Veredicto:** no necesita procedencia propia — es una síntesis de `pillars`/`risk_fit`, ambos ya con su procedencia cubierta en sus propias secciones.

---

## Ampliación de alcance #2 (2026-07-29, hallazgo urgente de Daniela) — manejo de mensajes largos (límite 4096 de Telegram)

**Origen del hallazgo:** Daniela reportó que en Telegram el texto del análisis "le aparece a la mitad" (cortado). Inspección de todo `src/investbot/*.py` confirma que **no existe ningún manejo del límite de 4096 caracteres de Telegram** — ni `reply_text`, ni `edit_message_text`, ni el `.edit_text` que ya implementa `SDD_mensaje_cargando.md` (`query_handler.py`, función `_run_analysis`, líneas 356-390 actuales, ya implementada y en el working tree sin commitear) manejan el caso de texto que supera el límite. Cuando eso pasa, la API de Telegram **rechaza el `sendMessage`/`editMessageText` completo** (`BadRequest: Message is too long`) — no lo trunca del lado del servidor. El único truncado que existe hoy en el repo es uno no relacionado en `fmp_client.py` (logs de error 402); confirmado por grep de "4096"/"split"/"truncat" en `src/investbot/*.py` sin resultados relevantes a mensajes salientes de Telegram.

Se agrega esta sección **antes** de que la spec pase a `security` porque el contenido que las Decisiones 1-13 de esta misma spec ya diseñaron (ROE/deuda/dividendos, VIX, Veredicto, explicaciones dummy de renta variable/beta/WACC) hace el mensaje considerablemente más largo — sin resolver esto primero, lo que implemente `implementer` después de las Decisiones 1-13 rompe el bot con más frecuencia, no con menos.

### Decisión 14 — Medido, no asumido: con el contenido de esta misma spec, el mensaje típico ya supera 4096 caracteres

**Metodología:** se ejecutó `fetch_and_analyze("ADBE", ...)` real (mismo código y mismos fixtures que usa `tests/test_query_handler.py::test_fetch_and_analyze_adobe_end_to_end`) contra el código **actual** (antes de implementar las Decisiones 1-13) para obtener una longitud verificada, y luego se reconstruyó el texto **post-implementación** insertando en ese mismo texto real los fragmentos literales ya fijados palabra por palabra en las Decisiones #3 (extras), #4 (veredicto), #9 (VIX), #10 (etiquetas "estimado"), #11 (explicación renta variable/beta) y #12 (WACC expandido) de esta spec — no se inventó texto nuevo para la proyección, se usó el mismo texto que `implementer` va a copiar.

| | Caracteres | vs. límite de 4096 |
|---|---|---|
| **Hoy** (Adobe, código actual, sin Decisiones 1-13) | **2227** | 1869 de margen |
| **Post-implementación** (Adobe, mismo caso, + Decisiones 1-13 completas) | **4506** | **410 caracteres por encima del límite** |

El caso Adobe usado es, además, el escenario **más favorable posible**: clasificación "Barata en los 3 escenarios" consolidada (sin desglose por escenario), cero modelos excluidos, cero notas de "no disponible" a nivel 2, 4/4 pilares sólidos, comparación con peers exitosa (sin fallback "no comparable"), y el fixture `quote.json` de Adobe ni siquiera trae `yearHigh`/`yearLow`, así que el bullet de momentum se omite por completo (`ETIQUETA_MOMENTUM_LABELS`, `summary.py` líneas 242-246) — en producción ese bullet normalmente sí aparece. Condiciones más "ruidosas" que sí ocurren con tickers reales alargan el mensaje todavía más (**análisis estructural por inspección de código, no medido con un segundo fixture real** — mismo estándar de honestidad de esta spec sobre no confundir "verificado" con "asumido", ver "Resolución (a)" al principio del documento):

- **Clasificación no unánime o con algún `None`** (`_build_classification_lines`, `summary.py` líneas 181-212): agrega un encabezado "Precio actual: ..." + hasta 4 líneas de desglose, en vez de la única línea consolidada del caso Adobe.
- **Modelos excluidos a nivel base** (`excluidos_base`, `summary.py` líneas 142-145): una línea por modelo excluido.
- **Notas de "no disponible" a nivel 2** (`modelos_nivel2_nd`, `summary.py` líneas 168-172): hasta 9 líneas posibles (3 modelos × 3 escenarios), cada una de ~100+ caracteres.
- **Bullet de momentum presente** (`summary.py` líneas 223-240): ~150-250 caracteres adicionales cuando el `/quote` real trae `yearHigh`/`yearLow`/promedios móviles (lo normal en producción, a diferencia del fixture sintético de Adobe).

**Conclusión:** se confirma el reporte de Daniela. Con certeza medida, el bot va a superar el límite de forma sistemática apenas se implementen las Decisiones 1-13 de esta misma spec, incluso en el caso más limpio posible (410 caracteres de exceso, caso Adobe). No hacía falta medir el peor caso para tomar la decisión de diseño — el caso favorable ya es evidencia suficiente y concreta de que el problema es real y bloqueante ahora.

### Decisión 15 — Opción elegida: partir en varios mensajes de Telegram, nunca truncar/resumir contenido

**Opción A — truncar o resumir el contenido cuando se pasa del límite.**
- ✅ Ventajas: un solo mensaje, más simple de implementar (`text[:4096]`).
- ❌ Desventajas: InvestBot es un bot de análisis financiero — su razón de ser es que Daniela reciba el análisis **completo** (fórmulas, los 3 escenarios de valor justo, procedencia de cada dato) para poder confiar en lo que ve. Truncar pierde contenido sin que Daniela lo note a simple vista (podría perder justo la clasificación final barata/cara, o una nota de transparencia que cambia cómo debe leerse un dato). Resumir en vez de truncar requeriría lógica nueva de priorización de contenido ("¿qué sección es prescindible?") no definida por nadie, y reintroduce el mismo tipo de "interpretación numérica nueva sin acordar con Daniela" que las Decisiones #2/#6/#8 de esta spec ya prohíben para ROE/VIX/etc.
- **Por qué se descarta:** el costo (perder información financiera en un bot cuyo valor es justamente no perderla) supera el beneficio (un solo mensaje), y nada en el proyecto sugiere que a Daniela le sirva un resumen recortado — al contrario, toda la iniciativa "contenido financiero más completo y explicado" que motiva esta spec va en la dirección opuesta.

**Opción B (elegida) — partir en varios mensajes, cortando siempre en un límite de sección, nunca a mitad de línea/oración.**
- ✅ Ventajas: cero pérdida de información. Los límites de corte (secciones que `build_summary` ya arma por separado) son los mismos que la Decisión #5 ya fijó como orden de lectura — no hace falta inventar ningún criterio nuevo de "dónde es seguro cortar".
- ❌ Desventajas: 2-3 mensajes en vez de 1 (impacto menor para un solo usuario, mismo criterio de proporcionalidad que `SDD_mensaje_cargando.md` ya usó para "2 llamadas a Telegram en vez de 1"); más superficie de fallo por más llamadas a la API (cubierto por las guardas de las Decisiones 18-19).
- **Por qué se elige:** en un bot cuyo propósito es dar información financiera completa y confiable, partir preserva el contrato implícito ("lo que ves es el análisis completo") mejor que cualquier recorte. Mismo criterio ya aplicado en Decisiones #2/#6/#8 de esta spec: "no perder ni inventar, degradar con gracia" — acá degradar con gracia significa más mensajes, no menos contenido.

### Decisión 16 — Diseño del split: reutiliza los límites de sección que `build_summary` ya arma, nunca corta dentro de una sección salvo el caso raro de la Decisión 18

`build_summary` (`summary.py`, líneas 382-391) ya arma internamente una lista ordenada `parts` y hace `"\n\n".join(parts)` al final. Esa lista **ya es** la partición segura que hace falta: cada elemento de `parts` es, por construcción, una sección completa, sin mezclar contenido de otra.

**16.1.** `summary.py` gana `build_summary_parts(...) -> list[str]`, mismos parámetros que `build_summary`, que hace todo el armado y devuelve `parts` ya filtrada de `None` (sin `"\n\n".join`). `build_summary` pasa a ser un wrapper de una línea:

```python
def build_summary(*, ...) -> str:
    return "\n\n".join(build_summary_parts(...))
```

Puramente aditivo: todo lo que hoy llama `build_summary(...)` sigue recibiendo el mismo `str`, incluidos todos los tests existentes de `tests/test_summary.py`. Solo `query_handler.py` necesita `build_summary_parts` para poder cortar antes de entregar a Telegram.

**16.2.** `query_handler.py` gana la constante `TELEGRAM_MESSAGE_LIMIT = 4096` y una función pura `chunk_for_telegram`, junto a `LOADING_MSG`/`_run_analysis` — es lógica de *entrega* por Telegram, no de *contenido* financiero (mismo criterio de separación que el docstring de `summary.py`, líneas 8-11, ya exige: "mantiene la separación cálculo puro vs. presentación"; el límite de 4096 es una restricción del canal de entrega, no una decisión de presentación de contenido).

```python
TELEGRAM_MESSAGE_LIMIT = 4096
# Margen reservado en cada chunk para el prefijo de continuación
# "_(cont. parte N/M)_\n\n" (Decisión 17.1) — conservador a propósito.
_CONTINUATION_PREFIX_RESERVE = 40

def chunk_for_telegram(
    parts: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """Empaqueta `parts` (las secciones de summary.build_summary_parts) en
    la menor cantidad de mensajes de Telegram posible, sin superar `limit`
    caracteres por mensaje. Nunca corta el contenido de una sección salvo
    que una sección sola ya supere `limit` (Decisión 18). Determinístico,
    sin I/O, sin conocimiento de python-telegram-bot."""
    budget = limit - _CONTINUATION_PREFIX_RESERVE
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        if current:
            chunks.append("\n\n".join(current))

    for part in parts:
        if len(part) > budget:
            flush()
            current, current_len = [], 0
            chunks.extend(_split_oversized_part(part, budget))
            continue
        added_len = len(part) + (2 if current else 0)  # "\n\n" entre partes
        if current_len + added_len > budget:
            flush()
            current, current_len = [part], len(part)
        else:
            current.append(part)
            current_len += added_len
    flush()
    return chunks or [""]
```

Bin packing voraz estándar: agrupa secciones consecutivas mientras entren en el presupuesto, corta a un mensaje nuevo apenas la próxima no entra. Nunca reordena `parts` — el orden de lectura de la Decisión #5 (Veredicto → Tienda de Limonada → Ratios → Extras → Valor Justo → Pilares → Contexto → Riesgo → Transparencia) se preserva igual, solo repartido en más de un mensaje cuando no entra en uno.

### Decisión 17 — Integración con el mensaje de carga (`SDD_mensaje_cargando.md`): el primer chunk reemplaza el mensaje de carga, los siguientes se envían como mensajes nuevos vía `Message.chat.send_message`

Verificado contra el `.venv` del proyecto (`python-telegram-bot==21.6`, mismo estándar de verificación que ya usó `SDD_mensaje_cargando.md` sección 2): `telegram.Chat.send_message(text, parse_mode=..., ...) -> Message`, y `Message.chat` es un atributo siempre presente tanto en el `Message` que devuelve `reply_text` como en el que devuelve `edit_message_text` (ya confirmado sin el caso `Union[..., bool]` relevante para este proyecto por la misma spec de carga, que descarta el modo inline). Esto resuelve "¿de dónde sale la capacidad de enviar mensajes nuevos para los chunks 2+?" **sin cambiar la firma de `_run_analysis` ni la de los call sites** — mismo principio no negociable que ya fijó `SDD_mensaje_cargando.md` ("`_run_analysis` no cambia de firma"): el objeto `Message` que ya se captura hoy (`loading_msg`, o el que devuelve `reply_fn` en el camino de fallback) trae `.chat`, que alcanza para los mensajes adicionales.

`_run_analysis` (`query_handler.py`, líneas 356-390 actuales) reemplaza su cuerpo por:

```python
async def _run_analysis(reply_fn, ticker: str, perfil: str) -> None:
    loading_msg = None
    try:
        loading_msg = await reply_fn(LOADING_MSG.format(ticker=ticker))
    except TelegramError as exc:
        logger.warning("No se pudo enviar el mensaje de carga para %s — %s", ticker, exc)

    try:
        parts = await fetch_and_analyze_parts(ticker, clients, perfil)  # ver nota abajo
    except (fmp_client.FMPError, treasury_client.TreasuryError) as exc:
        final_parts, kwargs = [str(exc)], {}
    except Exception:
        logger.exception("Error inesperado analizando %s", ticker)
        final_parts, kwargs = [GENERIC_ERROR_MSG], {}
    else:
        final_parts, kwargs = parts, {"parse_mode": "Markdown"}

    try:
        chunks = chunk_for_telegram(final_parts)
    except Exception:
        logger.exception("Fallo inesperado partiendo el mensaje para %s", ticker)
        chunks = [_hard_truncate_with_marker(final_parts)]  # Decisión 19

    chunks = _with_continuation_prefixes(chunks)  # Decisión 17.1, no-op si len==1

    if loading_msg is None:
        await _deliver_all(reply_fn, None, chunks, ticker, **kwargs)
        return

    try:
        await loading_msg.edit_text(chunks[0], **kwargs)
    except TelegramError as exc:
        logger.warning("No se pudo editar el mensaje final para %s — %s", ticker, exc)
        await _deliver_all(reply_fn, None, chunks, ticker, **kwargs)
        return

    await _deliver_all(reply_fn, loading_msg, chunks[1:], ticker, **kwargs)


async def _deliver_all(reply_fn, first_msg, remaining_or_all, ticker, **kwargs) -> None:
    """Entrega los chunks restantes. Si `first_msg` es None, `remaining_or_all`
    incluye el chunk 0 y se manda por `reply_fn` (mismo comportamiento que hoy
    cuando no hay `loading_msg`); el resto se manda con `.chat.send_message`
    sobre el `Message` que devuelve esa primera llamada."""
    chunks = remaining_or_all
    if first_msg is None:
        first_msg = await reply_fn(chunks[0], **kwargs)
        chunks = chunks[1:]
    for i, chunk in enumerate(chunks, start=2):
        try:
            await first_msg.chat.send_message(chunk, **kwargs)
        except TelegramError as exc:
            logger.error(
                "No se pudo enviar la parte %d del análisis de %s — esa parte no "
                "llegó a Telegram: %s", i, ticker, exc,
            )
```

**Nota sobre `fetch_and_analyze_parts`:** el nombre es ilustrativo — cómo exponer `parts` (en vez de un `str`) desde `fetch_and_analyze` hasta `_run_analysis` (¿función nueva `fetch_and_analyze_parts`? ¿`fetch_and_analyze` pasa a devolver `list[str]` con un wrapper de compatibilidad que hace `"\n\n".join(...)` para no romper `test_fetch_and_analyze_adobe_end_to_end`, que hoy hace `assert "Adobe" in text` sobre un `str`?) es un detalle de implementación que `implementer` resuelve con el mismo patrón de wrapper-de-compatibilidad que la Decisión 16.1 ya fijó para `build_summary`/`build_summary_parts` — no cambia esta decisión de diseño, solo dónde vive la línea que hace el join.

**17.1. Prefijo de continuación**, para que Daniela no vea un mensaje nuevo sin contexto de que es continuación del análisis:

```python
_CONTINUATION_PREFIX = "_(cont. parte {i}/{n})_\n\n"

def _with_continuation_prefixes(chunks: list[str]) -> list[str]:
    if len(chunks) <= 1:
        return chunks
    n = len(chunks)
    return [
        chunk if i == 1 else _CONTINUATION_PREFIX.format(i=i, n=n) + chunk
        for i, chunk in enumerate(chunks, start=1)
    ]
```

El primer chunk **no** lleva prefijo — reemplaza al mensaje de carga o llega por `reply_fn`, ya tiene contexto (Daniela ve "Analizando ADBE..." convertirse en el resultado, igual que hoy). Texto en itálica Markdown (`_..._`), mismo estilo que el resto de las notas de transparencia.

**El camino de un solo chunk (el caso de hoy) queda exactamente igual que el comportamiento ya validado por `SDD_mensaje_cargando.md`** — `chunk_for_telegram` devuelve `[texto_completo]`, `_with_continuation_prefixes` es no-op, y el flujo es idéntico al actual (un `edit_text`, cero `send_message` adicionales). Esto es intencional: las Decisiones 15-17 no reabren ni modifican ningún comportamiento de `SDD_mensaje_cargando.md` para el caso de mensaje corto, solo lo extienden para el caso largo.

### Decisión 18 — Guarda: una sección individual que por sí sola supera el presupuesto (`budget`)

**Corrección de consistencia (post-QA):** el boundary real es `budget = TELEGRAM_MESSAGE_LIMIT - _CONTINUATION_PREFIX_RESERVE = 4056`, no `4096` — así lo usa el pseudocódigo de la Decisión 16.2 tanto para decidir si una `part` es "demasiado grande" (`if len(part) > budget`) como para invocar `_split_oversized_part(part, budget)`. Es la opción correcta y no cambia: reservar el margen del prefijo de continuación también en las secciones partidas evita que un fragmento resultante, si termina en un chunk que no es el primero, se pase de 4096 al sumarle el prefijo. Toda referencia a "4096" más abajo en esta sección y en "Criterios de aceptación" debe leerse como `budget` (4056 con la reserva por defecto).

Caso raro — ninguna sección individual se acerca a este tamaño ni en el caso medido ni en el análisis estructural de la Decisión 14 (la más larga proyectada, "Rentabilidad, deuda de largo plazo y dividendos" con los 5 campos, mide ~700 caracteres) — pero se decide qué pasa para no dejarlo indefinido:

```python
def _split_oversized_part(part: str, limit: int) -> list[str]:
    """Parte una sección que por sí sola supera `limit`, probando cortes
    cada vez más finos: párrafo ("\\n\\n") -> línea ("\\n") -> oración
    (". ") -> corte duro con marcador visible + log. Nunca corta a mitad de
    palabra salvo el último recurso absoluto."""
    for separator in ("\n\n", "\n", ". "):
        pieces = part.split(separator)
        if len(pieces) > 1:
            chunks, current = [], ""
            for piece in pieces:
                candidate = current + (separator if current else "") + piece
                if len(candidate) > limit and current:
                    chunks.append(current)
                    current = piece
                else:
                    current = candidate
            if current:
                chunks.append(current)
            if all(len(c) <= limit for c in chunks):
                return chunks
    # Último recurso: no debería llegar acá con el contenido actual del
    # proyecto. Corte duro + marcador visible + log explícito (Decisión 19:
    # nunca perder contenido en silencio).
    logger.error(
        "Sección individual de %d caracteres no se pudo partir por párrafo/"
        "línea/oración dentro del límite de Telegram — corte duro aplicado. "
        "Texto completo:\n%s", len(part), part,
    )
    marker = "\n\n⚠️ _[recortado por límite de Telegram — ver logs]_"
    return [part[: limit - len(marker)] + marker]
```

Si esto se activa en producción, es señal de que alguna sección de `summary.py` creció más de lo previsto por esta spec — el `logger.error` con el texto completo asegura que el contenido nunca se pierde de verdad, solo no llega por Telegram en ese envío puntual.

### Decisión 19 — Guarda: el split en sí falla de forma inesperada (bug, caso no previsto)

Cubierto en el pseudocódigo de la Decisión 17 con un `try/except Exception` alrededor de `chunk_for_telegram(...)` dentro de `_run_analysis` — es la **única** excepción genérica (`except Exception`, no `except TelegramError`) que agrega esta ampliación, y es deliberado: acá no se enmascara un fallo de la API de Telegram (ya cubierto de forma acotada en toda esta spec y en `SDD_mensaje_cargando.md`), se pone una red de seguridad de último recurso contra un bug propio en la lógica de partición que, sin esto, tumbaría `_run_analysis` completo y Daniela no recibiría absolutamente nada — peor que cualquier otro caso ya cubierto.

```python
def _hard_truncate_with_marker(parts: list[str]) -> str:
    full = "\n\n".join(parts)
    logger.error(
        "chunk_for_telegram falló de forma inesperada (%d caracteres, %d "
        "secciones) — se entrega un único mensaje truncado como último "
        "recurso. Texto completo:\n%s", len(full), len(parts), full,
    )
    marker = "\n\n⚠️ _[mensaje recortado por un error interno — el análisis completo quedó en los logs]_"
    return full[: TELEGRAM_MESSAGE_LIMIT - len(marker)] + marker
```

Igual que en la Decisión 18: el contenido completo se loguea antes de truncar — "perder contenido" acá significa "no llega por Telegram en esa entrega puntual", nunca "desaparece sin dejar rastro". Este camino entrega como mínimo un mensaje visible a Daniela (nunca silencio total, nunca un `BadRequest` sin manejar que tumbe `_run_analysis`).

---

## Criterios de aceptación

### `fmp_client.py` / `query_handler.py` — nueva llamada a `/key-metrics` del ticker propio
- [ ] `fetch_and_analyze` llama `fmp_client.get_key_metrics(clients.fmp_http, clients.fmp_api_key, ticker, limit=1)` para el **ticker propio** (no solo para peers), en un `try/except fmp_client.FMPError` que nunca propaga la excepción.
- [ ] Si esa llamada falla o devuelve `[]`, el resto del análisis (ratios, valor justo, pilares, contexto de mercado, encaje de riesgo) se calcula y responde exactamente igual que hoy — test de regresión que verifica que la respuesta completa (menos la sección nueva) es idéntica a la actual cuando `/key-metrics` del ticker propio devuelve error o vacío.
- [ ] El fallo de esta llamada nunca dispara el mensaje `"No pude obtener suficientes datos de {ticker} para analizarlo ahora mismo."` (ese abort-check sigue dependiendo únicamente de `quote`/`profile`/`income_statements`/`balance_sheets`/`cash_flows`, sin modificación).
- [ ] `tests/test_query_handler.py::_adobe_router`: la ruta `/stable/key-metrics` debe distinguir la llamada del ticker propio (`symbol=ADBE`) de la de cada peer (`symbol=MSFT/ORCL/CRM`) — hoy ambas comparten el mismo path, diferenciables solo por el query param `symbol`. Se agrega un fixture nuevo (ver "Artefactos") para el caso `symbol=ADBE` distinto del dict `peers_metrics`.

### `rules.py` — `extract_key_metrics_extras`
- [ ] Con los 5 campos presentes y numéricos → los 5 valores de `KeyMetricsExtras` no son `None`.
- [ ] `metrics=None` (llamada falló) → los 5 campos son `None`, sin excepción.
- [ ] `metrics={}` (lista vacía de FMP) → los 5 campos son `None`, sin excepción.
- [ ] Un campo ausente del dict (ej. `metrics` tiene `roe` pero no `dividendYield`) → ese campo específico es `None`, los demás se leen normalmente.
- [ ] Un campo presente pero no numérico (ej. `"roe": "N/A"`, string) → `None` para ese campo, sin excepción, sin intento de `float("N/A")`.
- [ ] `dividendYield=0` → `dividend_yield == 0` (no `None`) — test explícito que distingue "no reparte dividendos" (0) de "dato ausente" (`None`).
- [ ] `payoutRatio` negativo o mayor a 1.0 (ej. `1.5` = 150%, empresa reparte más de lo que gana) → se propaga tal cual, sin normalizar ni clamear.
- [ ] `debtToEquity` negativo (patrimonio negativo) → se propaga tal cual, sin filtrar por signo.
- [ ] Test unitario aislado, sin HTTP (función pura, mismo estándar que el resto de `rules.py`).

### `summary.py` — sección nueva de rentabilidad/deuda/dividendos
- [ ] Los 5 campos presentes → los 5 bullets aparecen, con el texto de fórmula/origen y el texto dummy de la tabla de la Decisión #3.
- [ ] Los 5 campos ausentes (`extras=None` o dict con los 5 en `None`) → `build_extras_section` retorna `None` y la sección **no aparece en absoluto** en la respuesta final (ni un header vacío, ni bullets "N/D").
- [ ] Con 2 de 5 campos disponibles (ej. solo `roe` y `dividend_yield`) → solo esos 2 bullets aparecen, sin bullets "N/D" para los 3 ausentes.
- [ ] `dividend_yield=0` → bullet muestra "no reparte dividendos actualmente", no un `0.00%` sin contexto.
- [ ] La sección se ubica exactamente entre "Ratios clave" y "Valor Justo" en `build_summary` — test que verifica el orden de aparición de los headers en el string final (`"Ratios clave"` antes de `"Rentabilidad"` antes de `"Valor Justo"` / `"Rango de Valor Justo"`).

### `summary.py` — Veredicto
- [ ] Con los 4 pilares `True` y `risk_fit.encaja=True` → veredicto dice "barata", "4/4 pilares sólidos", "SÍ encaja", y **no** incluye la frase "Mirá con cuidado".
- [ ] Con `precio_razonable=None` (0 de 3 escenarios de valor justo calculables) → veredicto dice explícitamente "no pude determinar si está cara o barata", sin lanzar excepción ni mostrar `None` crudo.
- [ ] Con al menos un pilar en `False` → la frase "Mirá con cuidado: {lista}" aparece, listando exactamente los pilares en `False` (usando las etiquetas "ingresos"/"utilidades"/"deuda"/"precio", no las claves internas `ingresos_crecientes`/etc.).
- [ ] Con `risk_fit.encaja=False` → veredicto dice "NO encaja".
- [ ] El veredicto es el **segundo** bloque de la respuesta final (después del título, antes de la intro de Tienda de Limonada) — test que verifica el orden: `"*{company_name} ({ticker})*"` aparece, seguido de `"*En una frase:*"`, seguido de `"Tienda de Limonada"`.
- [ ] Test que confirma que `build_veredicto_section` **no** recibe ni usa `extras`/ROE/deuda/dividendos como parámetro — verificación de firma (solo `pillars` y `risk_fit`), para que quede explícito que el veredicto no depende de los datos nuevos de esta spec.

### `market_context.py` — `extract_vix_context` (NUEVO)
- [ ] `quote={"price": 18.42, ...}` → `VixResult(valor=18.42, disponible=True)`.
- [ ] `quote=None` (llamada falló) → `VixResult(valor=None, disponible=False)`, sin excepción.
- [ ] `quote={}` (dict vacío) → `VixResult(valor=None, disponible=False)`, sin excepción.
- [ ] `quote={"price": "N/A"}` (campo presente pero no numérico) → `VixResult(valor=None, disponible=False)`, sin excepción, sin intento de `float("N/A")`.
- [ ] `quote={"price": 0}` → `VixResult(valor=0, disponible=True)` — `0` es un dato válido (guarda de tipo `isinstance`, no de rango; el VIX en `0` sería anómalo financieramente pero no es responsabilidad de esta función juzgarlo, mismo criterio que `extract_key_metrics_extras`).
- [ ] Test unitario aislado, sin HTTP (función pura).
- [ ] Test que confirma que `extract_vix_context` **no** calcula ni expone ninguna clasificación cualitativa ("alta"/"baja"/"moderada" volatilidad) — verificación de que `VixResult` solo tiene los 2 campos `valor`/`disponible`, ningún campo de etiqueta.

### `fmp_client.py` / `query_handler.py` — nueva llamada a `/quote?symbol=^VIX` (NUEVO)
- [ ] `fetch_and_analyze` llama `fmp_client.get_quote(clients.fmp_http, clients.fmp_api_key, market_context.VIX_SYMBOL)` (con `VIX_SYMBOL == "^VIX"`), en un `try/except fmp_client.FMPError` que nunca propaga la excepción — mismo patrón que la Decisión #1 (ROE/deuda/dividendos).
- [ ] Si esa llamada falla o devuelve `None`, el resto del análisis se calcula y responde exactamente igual que hoy — test de regresión análogo al de `/key-metrics` del ticker propio.
- [ ] El fallo de esta llamada nunca dispara el mensaje de abort (`"No pude obtener suficientes datos..."`) — mismo criterio que la llamada de ROE/deuda/dividendos.
- [ ] `fmp_client.py` **no gana ninguna función nueva** — se reutiliza `get_quote` tal cual (test que confirma que no se agregó ningún wrapper HTTP nuevo, ej. `get_vix_quote`, que sería redundante).
- [ ] `tests/test_query_handler.py::_adobe_router`: la ruta `/stable/quote` debe distinguir la llamada del ticker propio (`symbol=ADBE`) de la del VIX (`symbol=^VIX`) — hoy solo conoce `symbol=ADBE`. Se agrega un fixture nuevo (ver "Artefactos").

### `summary.py` — bullet de VIX en Contexto de mercado (NUEVO)
- [ ] `vix={"valor": 18.42, "disponible": True}` → el bullet de VIX aparece con el valor formateado a 2 decimales, la etiqueta "Estimado/aproximado" y la aclaración de que no es un índice de sentimiento compuesto.
- [ ] `vix={"valor": None, "disponible": False}` o `vix=None` → el bullet de VIX **no aparece en absoluto** (ni "N/D", ni header vacío) — mismo criterio de degradación con gracia del resto del proyecto.
- [ ] La nota final de `build_market_context_section` (reemplazo de la nota actual sobre momentum/VIX) aparece siempre, tanto si el VIX está disponible como si no — texto exacto verificado en test.
- [ ] `build_summary` acepta `vix: Optional[dict] = None` con default, sin romper ningún caller/test existente que no lo pase.

### `summary.py` — etiquetas "estimado"/"aproximado" (NUEVO)
- [ ] El header de `build_valuation_scenarios_section` dice literalmente `"Rango de Valor Justo estimado"` (no solo `"Rango de Valor Justo"`).
- [ ] La línea de `"Valor Justo Total"` dice literalmente `"Valor Justo Total (estimado)"`.
- [ ] El bullet de VIX (cuando está disponible) contiene literalmente la palabra "Estimado" o "aproximado" (case-insensitive).
- [ ] Test de regresión: los bullets de Ratios clave (Liquidez, Margen Bruto, PER, P/S), los bullets de ROE/Debt-to-Equity/Net Debt-EBITDA/Dividend Yield/Payout Ratio, y los bullets de Momentum/Comparación con peers **no** contienen la palabra "estimado"/"aproximado" (verificación negativa explícita de que la auditoría de la Decisión #10 no sobre-etiquetó datos que no lo ameritan).

### `summary.py` — explicaciones dummy + procedencia (NUEVO)
- [ ] `build_risk_fit_section` incluye, siempre (no condicional), las 2 líneas exactas de explicación de "renta variable" y "beta" de la Decisión #11 (verificación de substring exacto o razonablemente exacto, no solo presencia de la palabra "beta").
- [ ] La explicación de "renta variable" no aparece duplicada en ningún otro lugar de la respuesta (test de conteo: aparece exactamente 1 vez).
- [ ] La nota de transparencia del DCF/WACC (última línea de `transparency_lines`) contiene la expansión conceptual de la Decisión #12 (verificación de que incluye "Costo Promedio Ponderado de Capital" y "cálculo propio del bot").
- [ ] Test de regresión: el orden de `parts` en `build_summary` sigue siendo válido con los 2 parámetros nuevos (`extras`, `vix`) en `None`/default — la respuesta se arma sin excepción para un caso sin ninguno de los datos nuevos de esta spec.

### `summary.py` — `build_summary_parts` (NUEVO — Ampliación #2)
- [ ] `build_summary_parts(...)` devuelve la misma lista `parts` que hoy arma `build_summary` internamente, ya filtrada de `None`, sin unir con `"\n\n".join`.
- [ ] `build_summary(...) == "\n\n".join(build_summary_parts(...))` para cualquier combinación de parámetros — test de equivalencia explícito.
- [ ] Todos los tests existentes de `tests/test_summary.py` que llaman a `build_summary(...)` y assertan sobre el `str` resultante siguen pasando sin modificar sus asserts.

### `query_handler.py` — `chunk_for_telegram` (NUEVO — Ampliación #2)
- [ ] Con `parts` cuya suma total (con separadores) es `<= 4096` → devuelve un único elemento, idéntico a `"\n\n".join(parts)` (comportamiento de hoy, sin cambios).
- [ ] Con `parts` cuya suma total supera 4096 → devuelve más de un elemento; cada elemento, incluyendo el prefijo de continuación de la Decisión 17.1, mide `<= 4096` caracteres.
- [ ] Ninguna sección de `parts` queda partida entre dos chunks, salvo el caso cubierto por `_split_oversized_part` (sección individual > 4096).
- [ ] El orden de las secciones en la concatenación de todos los chunks (sin los prefijos de continuación) es idéntico al orden de `parts` recibido — no se reordena nada.
- [ ] Caso `_split_oversized_part`: una sección sintética de más de 4096 caracteres con párrafos internos (`\n\n`) se parte en el separador más grueso posible sin exceder el límite en ningún pedazo resultante; se verifica también el fallback de corte duro + marcador cuando ni siquiera hay separadores (`\n\n`/`\n`/`. `) disponibles.
- [ ] Casos límite del límite: secciones sintéticas de exactamente `budget-1`, `budget` y `budget+1` caracteres (4055, 4056 y 4057 con `_CONTINUATION_PREFIX_RESERVE = 40` por defecto, no 4095/4096/4097 — ver corrección de consistencia en Decisión 18) — verificar que el corte ocurre exactamente donde corresponde.
- [ ] Test unitario aislado, sin HTTP, sin mocks de Telegram (función pura).

### `query_handler.py` — integración multi-mensaje con `_run_analysis` / mensaje de carga (NUEVO — Ampliación #2)
- [ ] Caso feliz de un solo chunk (contenido corto, como hoy): comportamiento **idéntico** al ya cubierto por los criterios de aceptación de `SDD_mensaje_cargando.md` — cero regresión, cero `send_message` adicional, cero prefijo de continuación.
- [ ] Caso feliz multi-chunk (2-3 chunks): `loading_msg.edit_text` recibe el chunk 1 (sin prefijo); cada chunk siguiente llega vía `loading_msg.chat.send_message`, en orden, cada uno con el prefijo `_(cont. parte i/n)_` correcto.
- [ ] Caso `loading_msg is None` (falló el envío del mensaje de carga) + contenido multi-chunk: el chunk 1 llega por `reply_fn` (como hoy en el caso de un solo chunk), y los chunks siguientes por `.chat.send_message` sobre el `Message` que devuelve esa llamada.
- [ ] Caso falla el `edit_text` del chunk 1 sobre `loading_msg` + contenido multi-chunk: mismo fallback de un intento único ya definido en `SDD_mensaje_cargando.md` (punto 4b) — el chunk 1 se reintenta por `reply_fn`, y los chunks siguientes por `.chat.send_message` sobre ese nuevo retorno.
- [ ] Caso falla el envío de un chunk intermedio (`.chat.send_message` de la parte 2 de 3, por ejemplo): se loguea con `logger.error` (no se traga en silencio) y se sigue intentando enviar la parte 3 (no se aborta el resto por el fallo de una parte); ninguna excepción se propaga fuera de `handle_text`/`handle_disambiguation`.
- [ ] Caso `chunk_for_telegram` lanza una excepción inesperada (mock con `side_effect`): se entrega un único mensaje con el marcador de recorte de la Decisión 19, con el texto completo logueado vía `logger.error`; ninguna excepción se propaga.
- [ ] Cero llamadas nuevas a FMP/FRED/Treasury.gov — esta ampliación es 100% interacción con la API de Telegram, mismo criterio que el resto de `SDD_mensaje_cargando.md`.

### Regresión — nada existente cambia
- [ ] Los términos literales "el boletín"/"la foto"/"el extracto"/"Tienda de Limonada" siguen presentes en la respuesta (criterio ya existente de `SDD_investbot_mvp.md`, sigue vigente).
- [ ] Las fórmulas de PER, P/S, Ratio de Liquidez, Margen Bruto, Múltiplos, Graham EPS Model y DCF no cambian ni una línea — verificable por `git diff` mostrando cero cambios en `valuation.py` fuera de, como mucho, el nuevo parámetro de `build_summary` en su llamador; `rules.py` solo gana la función nueva `extract_key_metrics_extras` + dataclass `KeyMetricsExtras`, sin tocar `calculate_eps`/`calculate_gross_margin`/`calculate_liquidity_ratio`/`calculate_per`/`calculate_ps`/`evaluate_pillars`; `market_context.py` solo gana `VIX_SYMBOL`/`VixResult`/`extract_vix_context`, sin tocar `calculate_momentum`/`compare_to_peers`.
- [ ] Todos los tests existentes de `tests/test_summary.py`, `tests/test_rules.py`, `tests/test_query_handler.py`, `tests/test_valuation.py`, `tests/test_peers.py`, `tests/test_risk_fit.py`, `tests/test_market_context.py` siguen pasando sin modificar sus asserts existentes (solo se agregan tests nuevos).
- [ ] `build_summary` sigue aceptando llamadas sin los parámetros `extras`/`vix` (nuevos, con default) sin romper ningún test/caller existente.
- [ ] `build_market_context_section` sigue aceptando llamadas sin el parámetro `vix` (nuevo, con default) sin romper ningún test/caller existente.

---

## Artefactos a crear/modificar

- `src/investbot/fmp_client.py` → **sin cambios** (`get_key_metrics` y `get_quote` ya existen, se reutilizan tal cual — incluso para VIX).
- `src/investbot/query_handler.py` → en `fetch_and_analyze`: agregar el bloque *best-effort* de `own_metrics` (Decisión #1); agregar el bloque *best-effort* de `vix_quote` (Decisión #7); agregar la llamada a `rules.extract_key_metrics_extras` y a `market_context.extract_vix_context`; pasar `extras` y `vix` (convertidos a dict plano, mismo patrón que `ratios_dict`/`pillars_dict`) a `summary.build_summary`.
- `src/investbot/rules.py` → agregar `KeyMetricsExtras` (dataclass) y `extract_key_metrics_extras` (función pura). No modificar ninguna función existente.
- `src/investbot/market_context.py` → agregar `VIX_SYMBOL` (constante), `VixResult` (dataclass), `extract_vix_context` (función pura). Actualizar el docstring de módulo (líneas 1-16) para aclarar que `extract_vix_context` es pura pero el dato de entrada requiere un fetch nuevo (a diferencia de `calculate_momentum`/`compare_to_peers`, que siguen sin requests nuevos). No modificar `calculate_momentum`/`compare_to_peers`.
- `src/investbot/summary.py` → agregar `build_extras_section` y `build_veredicto_section`; modificar `build_summary` para: (a) aceptar los nuevos parámetros `extras: Optional[dict] = None` y `vix: Optional[dict] = None`, (b) reordenar `parts` según la Decisión #5, (c) filtrar `None` de `parts` antes de unir; modificar `build_market_context_section` para aceptar `vix: Optional[dict] = None` y agregar el bullet + nota nueva (Decisión #9); modificar `build_risk_fit_section` para agregar las 2 líneas de explicación dummy (Decisión #11); modificar la nota de transparencia del WACC dentro de `build_summary` (Decisión #12); modificar los headers de `build_valuation_scenarios_section` para agregar "estimado" (Decisión #10).
- `tests/test_rules.py` → tests de `extract_key_metrics_extras` (todos los casos de "Criterios de aceptación").
- `tests/test_market_context.py` (nuevo archivo, o sección nueva si ya existe) → tests de `extract_vix_context` (todos los casos de "Criterios de aceptación").
- `tests/test_summary.py` → tests de `build_extras_section`, `build_veredicto_section`, bullet de VIX en `build_market_context_section`, etiquetas "estimado" en headers de Valor Justo, explicaciones dummy en `build_risk_fit_section`, nota de WACC expandida, más el test de orden de secciones en `build_summary`.
- `tests/test_query_handler.py` → actualizar `_adobe_router` para distinguir `symbol=ADBE` (ticker propio) de los peers en la ruta `/stable/key-metrics`, y para distinguir `symbol=ADBE` de `symbol=^VIX` en la ruta `/stable/quote`; agregar tests de los 2 casos *best-effort* (fallo/vacío de `/key-metrics` propio y de `/quote` de VIX no rompen el análisis).
- `tests/fixtures/adobe/` → nuevo fixture sintético para la respuesta de `/key-metrics` del ticker propio con los 5 campos poblados (ej. `key_metrics_own.json`); nuevo fixture sintético para la respuesta de `/quote?symbol=^VIX` (ej. `quote_vix.json`, forma `[{"symbol": "^VIX", "price": 18.42}]`) — ambos documentados como **origen: sintético** hasta que se confirme en producción (mismo criterio que el resto de fixtures del proyecto, ver `tests/fixtures/fmp/README.md`).
- `tests/fixtures/fmp/README.md` → si se agregan los fixtures nuevos fuera de `tests/fixtures/adobe/`, documentarlos con el mismo formato que las entradas existentes (una línea + origen).
- `README.md` → actualizar la fila "Datos propios del ticker" de la tabla de presupuesto de requests para reflejar que `/key-metrics` del ticker propio **ahora sí** se llama en el código; agregar una fila nueva "Contexto de mercado (VIX, no depende del ticker consultado)" con cantidad `1` y endpoint `/quote (symbol=^VIX)`; actualizar el total de "9-12" a **"10-13"**; actualizar el rango de consultas/día soportadas de "~20 y ~27" a **"~19 y ~25"** (250/13 ≈ 19, 250/10 = 25) — sigue muy por encima del uso esperado de un solo usuario, no es un hallazgo de riesgo, solo actualización numérica.
- `src/investbot/summary.py` (Ampliación #2) → agregar `build_summary_parts`; `build_summary` pasa a ser un wrapper de una línea sobre `build_summary_parts` (Decisión 16.1).
- `src/investbot/query_handler.py` (Ampliación #2) → agregar `TELEGRAM_MESSAGE_LIMIT`, `_CONTINUATION_PREFIX_RESERVE`, `_CONTINUATION_PREFIX`, `chunk_for_telegram`, `_split_oversized_part`, `_with_continuation_prefixes`, `_deliver_all`, `_hard_truncate_with_marker`; modificar `_run_analysis` (líneas 356-390 actuales) según la Decisión 17; exponer `parts` desde `fetch_and_analyze` (nombre exacto a definir por `implementer`, ver nota de la Decisión 17, mismo patrón de wrapper-de-compatibilidad que `build_summary`/`build_summary_parts`).
- `tests/test_summary.py` (Ampliación #2) → tests de `build_summary_parts` y de su equivalencia con `build_summary`.
- `tests/test_query_handler.py` (Ampliación #2) → tests de `chunk_for_telegram` (aislados, sin mocks de Telegram) y de la integración multi-chunk con `_run_analysis` (mocks de `Message`/`Chat` con `.chat.send_message` configurado, mismo patrón que ya usa `_fake_text_update`).

---

## Restricciones

- **Ninguna fórmula existente cambia.** PER, P/S, Ratio de Liquidez, Margen Bruto, Múltiplos, Graham EPS Model y DCF quedan intactos, byte a byte de lógica — esta spec es 100% aditiva sobre `rules.py`/`summary.py`/`query_handler.py`/`market_context.py`.
- **No se agrega ninguna interpretación numérica nueva sobre ROE/Debt-to-Equity/Dividend Yield/Payout Ratio ni sobre el VIX** (ej. "ROE > 15% es bueno", "Debt-to-Equity > 2 es riesgoso", "VIX > 30 es alta volatilidad"). Se muestran como datos informativos con su fórmula/origen citado, sin semáforo ni umbral — si Daniela quiere eso después, es una spec patch separada con el umbral acordado explícitamente.
- **El Veredicto no depende de ROE/deuda/dividendos ni del VIX.** Se arma exclusivamente de `pillars` y `risk_fit`, datos que el bot ya calcula hoy — cero riesgo de que el veredicto necesite un umbral nuevo no acordado.
- **No se agrega ninguna llamada HTTP adicional más allá de las 2 ya decididas** (`/key-metrics` del ticker propio, `limit=1`; `/quote?symbol=^VIX`). No se llama `/key-metrics-ttm` (confirmado de pago) ni ningún otro endpoint para obtener estos datos. No se agrega ninguna llamada para "Fear & Greed" de CNN (rechazado explícitamente por Daniela, sin API oficial gratuita).
- **`net_debt_to_ebitda` y `payout_ratio` son opcionales** (Daniela los pidió como "y opcionalmente") — si en uso real resulta que no existen en el plan gratuito, se implementa igual el resto (roe, debt_to_equity, dividend_yield) sin bloquear toda la spec por esos dos campos.
- **No se toca `peers.py`** — la llamada a `/key-metrics` para peers (para `earningsYield`/PER) sigue exactamente igual.
- **No se muestra la variación % diaria del VIX** ni ningún otro campo de `/quote` de `^VIX` más allá de `price` (Decisión #8) — fuera de alcance, agregar complejidad no pedida por Daniela.
- **No se cachea el VIX entre consultas** aunque no dependa del ticker consultado (podría ahorrarse presupuesto de requests pidiéndolo una sola vez por período corto en vez de una vez por consulta) — optimización de presupuesto fuera de alcance de esta spec, el presupuesto actualizado (10-13 requests/consulta, ~19-25 consultas/día) ya es ampliamente suficiente para el uso de un único usuario; si en el futuro el volumen de uso lo justifica, es una spec de optimización separada.
- **(Ampliación #2) No se trunca ni se resume contenido financiero**, salvo los 2 casos de último recurso explícitamente logueados (Decisiones 18 y 19) — y en esos casos el contenido completo queda en logs, nunca se pierde de verdad.
- **(Ampliación #2) No se agrega reintento con backoff** para el envío de chunks adicionales — mismo criterio de "un intento, sin reintentos" que ya fijó `SDD_mensaje_cargando.md` para el mensaje final.
- **(Ampliación #2) No se cambia la firma de `_run_analysis`** ni la de los call sites de `handle_text`/`handle_disambiguation` — mismo principio no negociable heredado de `SDD_mensaje_cargando.md`.
- **(Ampliación #2) No se agrega ninguna dependencia nueva** — `Chat.send_message` ya viene con `python-telegram-bot==21.6`, ya presente en `requirements.txt`.
- Esta spec es solo la fase de diseño (`architect`). No implementar código todavía — falta pasar por `security` y `qa`.

---

## Preguntas abiertas

**Ninguna pregunta bloqueante nueva identificada.** Las 2 preguntas bloqueantes de la versión original de esta spec ya están resueltas (ver sección al inicio del documento). El diseño de VIX/etiquetas "estimado"/explicaciones dummy no generó ninguna decisión que requiera confirmación adicional de Daniela antes de scope freeze — todas las decisiones de diseño (formato del bullet de VIX, ausencia de umbral cualitativo, textos dummy exactos, ubicación de cada texto) quedaron fijadas explícitamente en la sección "Decisiones de diseño tomadas" arriba, siguiendo el mismo criterio ya validado por Daniela para ROE/deuda/dividendos (no bloquear con verificación previa, degradar con gracia, sin interpretación numérica nueva sin umbral acordado).

**Ampliación #2 (manejo de mensajes largos): tampoco genera preguntas bloqueantes.** Daniela pidió explícitamente que el diseño (partir vs. truncar/resumir, cómo combina con el mensaje de carga, qué pasa en los casos raros) se decidiera "con criterio de architect", sin devolverlo como pregunta abierta. Las Decisiones 14-19 dejan fijado: partir en múltiples mensajes (Decisión 15, elegida explícitamente sobre truncar/resumir), reutilizar los límites de sección ya existentes (Decisión 16), integración exacta con `SDD_mensaje_cargando.md` sin cambiar su firma (Decisión 17), y las 2 guardas de último recurso con logging explícito para nunca perder contenido en silencio (Decisiones 18-19).

---

## Handoff → security

### Specs producidas
- Esta spec (`SDD_contenido_financiero_explicado.md`, antes `SDD_roe_deuda_dividendos_veredicto.md`), Iter-1.

### Criterios de aceptación base
Ver sección "Criterios de aceptación" completa arriba, agrupada en: nueva llamada FMP (`/key-metrics` propio), `rules.py`, `summary.py` (sección nueva + veredicto), nueva llamada FMP (VIX), `market_context.py`, `summary.py` (VIX + etiquetas + explicaciones dummy), regresión.

### Decisiones de diseño tomadas (no reabrir)
1. Llamada nueva a `/key-metrics` para el ticker propio, *best-effort*, no bloqueante — reutiliza `fmp_client.get_key_metrics` ya existente y ya auditado.
2. `rules.extract_key_metrics_extras` — función pura, guarda de tipo, sin fórmula, sin filtrar por signo/rango.
3. `summary.build_extras_section` — sección omitible, ubicada entre "Ratios clave" y "Valor Justo".
4. `summary.build_veredicto_section` — ubicado al inicio, construido solo de `pillars`/`risk_fit`, sin ROE/deuda/dividendos.
5. Nuevo orden de `build_summary` (Decisión #5).
6. Ninguna interpretación numérica nueva sobre los datos nuevos — solo se muestran, no se juzgan (aplica también al VIX).
7. Llamada nueva a `/quote?symbol=^VIX`, *best-effort*, no bloqueante — reutiliza `fmp_client.get_quote` ya existente y ya auditado, sin wrapper HTTP nuevo.
8. `market_context.extract_vix_context` — función pura, guarda de tipo, sin fórmula, sin clasificación cualitativa.
9. `summary.build_market_context_section` gana bullet de VIX, omitible, etiquetado "estimado/aproximado".
10. Etiquetas "estimado"/"aproximado" agregadas solo donde corresponde (VIX, headers de Valor Justo) — auditoría completa en Decisión #10, no se sobre-etiquetó nada.
11. Explicaciones dummy fijas de "renta variable"/"beta" en `build_risk_fit_section`, siempre presentes.
12. Explicación conceptual de WACC expandida en la nota de transparencia del DCF, con procedencia explícita ("cálculo propio del bot").
13. **(Ampliación #2)** Partir el mensaje en varios envíos de Telegram cuando supera 4096 caracteres, cortando siempre en un límite de sección — nunca truncar ni resumir contenido financiero (Decisión 15).
14. **(Ampliación #2)** `summary.build_summary_parts` expone la lista de secciones sin unir; `query_handler.chunk_for_telegram` empaqueta esas secciones en el menor número de mensajes posible sin superar el límite (Decisión 16).
15. **(Ampliación #2)** El primer chunk reemplaza al mensaje de carga (`.edit_text`), los chunks siguientes se envían con `Message.chat.send_message` — sin cambiar la firma de `_run_analysis` ni de los call sites (Decisión 17).
16. **(Ampliación #2)** Guardas de último recurso con `logger.error` explícito (nunca silencioso) para: una sección individual que por sí sola supera el límite (Decisión 18), y un fallo inesperado del propio mecanismo de partición (Decisión 19).

### Foco esperado para `security`
- La nueva llamada a `/key-metrics` para el ticker propio usa exactamente el mismo wrapper (`fmp_client.get_key_metrics`) ya auditado en Iter-1 de `SDD_investbot_mvp.md` (secretos vía `env_file`, sin logging de URL/API key) — ¿hay algo nuevo que auditar por ser el *ticker propio* en vez de un peer, o el análisis de Iter-1 ya cubre este caso por igual?
- La nueva llamada a `/quote?symbol=^VIX` usa exactamente el mismo wrapper (`fmp_client.get_quote`) ya auditado, con un **símbolo constante hardcodeado** (`"^VIX"`, no input de usuario) — riesgo de inyección aún menor que el caso de `ticker` (que sí viene de input de usuario resuelto vía `/search-symbol`). ¿Confirma `security` que esto no requiere análisis adicional más allá de lo ya cubierto para `get_quote` en Iter-1?
- ¿El manejo `except fmp_client.FMPError: own_metrics = None` / `except fmp_client.FMPError: vix_quote = None` (silenciar el error sin loguear detalle adicional más allá de lo que ya loguea `_get` internamente) es suficiente para ambas llamadas nuevas, o `security` quiere un log explícito adicional en `query_handler.py` para poder diagnosticar si alguna de estas 2 llamadas falla sistemáticamente?
- Ninguna API key ni dato sensible nuevo se agrega — los 5 campos nuevos (ROE, deuda, dividendos) son datos financieros públicos de la empresa consultada, y el VIX es un índice de mercado público — mismo nivel de sensibilidad que el resto de los datos ya mostrados (ASVS L1 ya definido como suficiente en Iter-1).
- **(Ampliación #2)** La única superficie nueva de "manejo de mensajes largos" son llamadas adicionales a `Message.chat.send_message` — mismo canal (Telegram, mismo `chat_id` ya autorizado por el `ALLOWED_CHAT_ID`/rate limiter existentes, ver `SDD_investbot_mvp.md`) y mismo tipo de dato (el propio texto del análisis, ya considerado no sensible) que las llamadas ya auditadas de `SDD_mensaje_cargando.md`. No hay `chat_id` nuevo, no hay input de usuario nuevo entrando a esta ruta (el contenido de cada chunk sale de `build_summary_parts`, no de texto crudo del usuario). ¿Confirma `security` que esto no abre superficie nueva más allá de "más llamadas al mismo canal ya auditado"?
- **(Ampliación #2)** El único `except Exception` genérico de todo este documento vive en la Decisión 19 (red de seguridad alrededor de `chunk_for_telegram`, no alrededor de la API de Telegram) — ¿confirma `security` que está suficientemente acotado (solo protege contra un bug propio de partición, siempre loguea con `logger.error` antes de degradar, nunca enmascara un fallo de Telegram ni se usa para nada más en el archivo)?

### Preguntas abiertas que `security` NO debe resolver por su cuenta
Ninguna — las 2 preguntas de negocio/producto de la versión original ya fueron resueltas por Daniela (ver sección al inicio de este documento).

---

## Criterios QA para Spec: Contenido financiero explicado — ROE/deuda/dividendos/Veredicto/VIX/mensajes largos [Iter-1]

**Rol:** `qa` (pre-implementación, Momento 1 del pipeline BMAD). Amplía la spec de `architect` (revisada sin hallazgos bloqueantes por `security`, incluida la Decisión #12b) — no la reescribe. Leída completa (846 líneas) + código real de `summary.py`, `rules.py`, `market_context.py`, `valuation.py`, `risk_fit.py`, `fmp_client.py`, `query_handler.py` + tests existentes de `test_summary.py`, `test_market_context.py`, `test_query_handler.py`, `test_fmp_client.py`. Corrida real de la suite actual antes de escribir este bloque: **262 tests, 98.10% de cobertura total** (`rules.py`/`market_context.py`/`valuation.py`/`peers.py`/`db.py`/`security.py` al 100%, `summary.py` al 99%, `query_handler.py` al 96%) — cifra citada en la sección 3 más abajo.

Cubre las 6 piezas del encargo: (1) `KeyMetricsExtras` + fetch propio, (2) VIX, (3) Veredicto, (4) explicaciones dummy + WACC expandido + disclaimer 12b, (5) límite 4096/`chunk_for_telegram`, (6) reordenamiento de `build_summary`.

### Tipo de prueba principal
**Unit testing** para las funciones puras nuevas (`rules.extract_key_metrics_extras`, `market_context.extract_vix_context`, `summary.build_extras_section`, `summary.build_veredicto_section`, `query_handler.chunk_for_telegram`, `query_handler._split_oversized_part`, `query_handler._with_continuation_prefixes`, `query_handler._hard_truncate_with_marker`) + **Integration testing** para la orquestación (`fetch_and_analyze` con `httpx.MockTransport`, `_run_analysis` con mocks de `telegram.Message`/`Chat`, mismo patrón que ya usa el 100% de `test_query_handler.py`). Mismo mix que ya usa el proyecto: lógica pura al 100%, capa de integración con matriz de escenarios explícita en vez de %.

---

### 1. Tests existentes — qué rompe, qué no, y qué queda "verde por casualidad"

#### 1.1 No rompen (verificado leyendo el código, no supuesto)
- Todos los tests de `test_summary.py` que llaman `build_summary(**kwargs)` sin `extras`/`vix`: ambos son keyword-only con default `None` (Decisiones #5, #9) — ningún caller existente rompe.
- Los tests de `build_valuation_scenarios_section` (`test_build_valuation_scenarios_section_desglosa_los_3_modelos` y el resto): el cambio de header a `"Rango de Valor Justo estimado"` / `"Valor Justo Total (estimado)"` (Decisión #10) no rompe ningún assert porque todos usan `in text` (substring), y `"Valor Justo Total"` sigue siendo substring de `"Valor Justo Total (estimado)"`.
- `test_fmp_client.py` completo: `fmp_client.py` queda **sin cambios** según "Artefactos a crear/modificar" — cero riesgo de regresión ahí.
- Los 7 tests de la sección "Mensaje de carga" (`test_handle_text_resuelve_ticker_exacto_y_responde`, `test_handle_text_falla_envio_mensaje_carga_no_bloquea_analisis`, `test_handle_text_falla_edit_final_hace_fallback_a_reply_fn`, `test_handle_text_excepcion_no_telegram_en_envio_carga_se_propaga`, `test_run_analysis_error_generico_no_crashea`, `test_run_analysis_fmp_error_dentro_de_fetch_and_analyze`, `test_handle_disambiguation_resuelve_y_responde`): siguen en verde **si y solo si** la Decisión 17 preserva el camino de 1-chunk exactamente como la propia spec promete (línea 602: "el flujo es idéntico al actual"). Esto no se da por sentado — es el primer criterio de la matriz de la Pieza 5b más abajo, con evidencia (los mismos 7 tests corriendo sin tocar sus asserts).

#### 1.2 Rompen con certeza si `implementer` no fija un detalle que la spec dejó abierto
- **`test_fetch_and_analyze_adobe_end_to_end`** (`assert "Adobe" in text`, `"barata" in text`, `"el boletín" in text`) y **`test_fetch_and_analyze_datos_incompletos_mensaje_claro`** (`assert "No pude obtener" in text`): ambos asumen que `fetch_and_analyze(...)` devuelve un `str`. La spec deja esto explícitamente sin resolver (nota debajo de la Decisión 17, línea 583: "el nombre es ilustrativo... es un detalle de implementación que `implementer` resuelve"). Si `implementer` cambia `fetch_and_analyze` para devolver `list[str]`, ambos tests rompen (`in` sobre una lista no hace substring match). **QA fija esto como criterio, no lo deja abierto** — ver Gap #1.
- **`_adobe_router`** (línea 27-28 de `test_query_handler.py`): hoy devuelve el fixture de cotización de Adobe para **cualquier** `symbol` pedido a `/stable/quote`, sin filtrar. No es una ruptura de assert (nada lo verifica hoy), pero bloquea escribir cualquier test correcto de VIX — ver Gap #2, tratado aparte porque es un riesgo de falso-verde, no de fallo visible.

#### 1.3 Verdes "por casualidad" — deben reforzarse, no solo dejarse pasar
- **`test_market_context_section_incluye_nota_de_transparencia`**: pasa tanto con la nota actual como con la nota nueva de la Decisión #9 (ambas contienen los substrings `"proxy simple de precio"` y `"VIX"`), así que no prueba realmente el reemplazo de texto que pide la Decisión #9. Debe complementarse con un test que verifique el texto exacto nuevo en sus 2 variantes (VIX disponible / no disponible) — incluido en la matriz de la Pieza 2.

---

### 2. Matriz de tests nuevos por pieza

**Pieza 1 — `KeyMetricsExtras` (`rules.extract_key_metrics_extras` + `summary.build_extras_section` + fetch en `query_handler`)**
- [ ] Happy path: 5 campos numéricos presentes → 5 valores no-`None` + 5 bullets con fórmula/origen y texto dummy exactos de la tabla de la Decisión #3.
- [ ] `metrics=None` → 5 campos `None`; `build_extras_section` retorna `None`; la sección no aparece (ni header, ni "N/D").
- [ ] `metrics={}` → mismo resultado que `None`.
- [ ] 1 campo ausente del dict → ese campo `None`, el resto se lee normal (caso parcial, 4 de 5 → 4 bullets).
- [ ] 1 campo string no numérico (`"roe": "N/A"`) → `None` para ese campo, sin `ValueError`/`TypeError`.
- [ ] `dividendYield=0` → `dividend_yield == 0` (no `None`) + bullet dice "no reparte dividendos actualmente", nunca `"0.00%"` sin contexto.
- [ ] `payoutRatio=1.5` (150%) y `debtToEquity` negativo → se propagan tal cual, sin clamp/normalización — test negativo explícito de "sin interpretación numérica nueva" (Restricción de la spec).
- [ ] Fetch best-effort: `fmp_client.get_key_metrics` lanza `FMPError` para el ticker propio → `own_metrics=None`; el resto de la respuesta (ratios/valor justo/pilares/contexto/riesgo) es **byte a byte idéntico** al texto que produce el mismo fixture sin esta llamada — diff de texto, no solo "no crashea".
- [ ] Orden: header "Rentabilidad, deuda de largo plazo y dividendos" aparece después de "Ratios clave" y antes de "Rango de Valor Justo" — test de índice de substring.

**Pieza 2 — VIX (`market_context.extract_vix_context` + bullet en `build_market_context_section` + fetch)**
- [ ] `quote={"price": 18.42}` → `VixResult(valor=18.42, disponible=True)` + bullet visible con "Estimado"/"aproximado" (case-insensitive) + aclaración de que no es un índice compuesto tipo Fear & Greed.
- [ ] `quote=None` / `quote={}` / `quote={"price": "N/A"}` → `VixResult(valor=None, disponible=False)`, bullet ausente por completo.
- [ ] `quote={"price": 0}` → `valor=0, disponible=True` (guarda de tipo, no de rango).
- [ ] `VixResult` expone únicamente `valor`/`disponible` — test de `dataclasses.fields` que confirma que no hay campo de clasificación cualitativa.
- [ ] Nota final de `build_market_context_section` (reemplazo, Decisión #9): texto exacto nuevo verificado en 2 tests separados (VIX disponible / no disponible) — **reemplaza** el test "verde por casualidad" de 1.3, no lo complementa nada más.
- [ ] Fetch best-effort: `get_quote(..., "^VIX")` lanza `FMPError` → `vix_quote=None`; resto de la respuesta idéntico — mismo patrón de diff que Pieza 1.
- [ ] Prerequisito (no un test de negocio en sí, ver Gap #2): `_adobe_router` distingue `symbol=ADBE` de `symbol=^VIX` en `/stable/quote`, con fixture nuevo `quote_vix.json` con un valor claramente distinto del precio de Adobe (ej. `18.42` vs `333.00`) — el test end-to-end debe verificar explícitamente que el VIX mostrado **no** coincide con el precio de ADBE.
- [ ] `fmp_client.py` no gana ninguna función nueva — verificación de que `get_vix_quote` (o similar) no existe.

**Pieza 3 — Veredicto (`summary.build_veredicto_section`)**
- [ ] 4/4 pilares `True` + `encaja=True` → "barata", "4/4 pilares sólidos", "SÍ encaja", sin "Mirá con cuidado".
- [ ] `precio_razonable=None` → "no pude determinar si está cara o barata", sin excepción, sin `None` crudo en el texto.
- [ ] ≥1 pilar `False` → "Mirá con cuidado: {lista}" con etiquetas humanas (ingresos/utilidades/deuda/precio), no las claves internas.
- [ ] `encaja=False` → "NO encaja".
- [ ] Orden: Veredicto es el 2do bloque (después del título, antes de "Tienda de Limonada") — índice de substring.
- [ ] Firma: `build_veredicto_section` solo acepta `pillars`/`risk_fit` — `inspect.signature` confirma ausencia de parámetro de `extras`/ROE/deuda/dividendos/VIX.
- [ ] Caso peor-escenario (alto riesgo de negocio, no listado explícitamente en el spec): 0/4 pilares + `precio_razonable=None` + `encaja=False` simultáneos → texto sigue siendo válido, sin excepción.

**Pieza 4 — Explicaciones dummy + WACC + disclaimer 12b**
- [ ] `build_risk_fit_section` incluye siempre (sin condición) las 2 líneas exactas de "renta variable"/"beta" de la Decisión #11 — substring exacto o casi-exacto, no solo la palabra "beta" suelta.
- [ ] La explicación de "renta variable" aparece **exactamente 1 vez** en toda la respuesta (`text.count(...) == 1`), no solo "está presente".
- [ ] Nota de WACC (Decisión #12) contiene "Costo Promedio Ponderado de Capital" y "cálculo propio del bot".
- [ ] Disclaimer general 12b está **siempre** presente, sin condición — a diferencia de la nota de `treasury_source` (condicional): test explícito con y sin `treasury_source` que confirma que 12b aparece en ambos casos.
- [ ] Orden dentro de `transparency_lines`: nota de WACC (#12) antes que el disclaimer general (#12b).
- [ ] Regresión: con `extras=None`/`vix=None` (default) `build_summary` arma la respuesta completa sin excepción — caso "cero datos nuevos".

**Pieza 5 — Límite 4096 / `build_summary_parts` / `chunk_for_telegram` (funciones puras)**
- [ ] `build_summary(...) == "\n\n".join(build_summary_parts(...))` — equivalencia explícita, con y sin `extras`/`vix`.
- [ ] `chunk_for_telegram(parts)` con suma total ≤ presupuesto → 1 solo chunk, idéntico a `"\n\n".join(parts)`.
- [ ] `chunk_for_telegram(parts)` con suma total > presupuesto → ≥2 chunks, cada uno ≤ 4096 (incluido el prefijo de continuación que se agrega después).
- [ ] Ninguna sección se parte entre 2 chunks salvo el caso `_split_oversized_part`.
- [ ] Orden de las secciones en la concatenación de todos los chunks (sin prefijos) idéntico al de `parts` recibido.
- [ ] `_split_oversized_part`: sección sintética > presupuesto con párrafos internos (`\n\n`) → se parte en el separador más grueso posible sin exceder el límite en ningún pedazo.
- [ ] `_split_oversized_part`: sección sintética sin ningún separador (`\n\n`/`\n`/`. ` ausentes) → corte duro con marcador visible + `logger.error` con el texto completo (test con `caplog`).
- [ ] Casos límite de tamaño — **ver Gap #1**: los valores a testear son `budget-1`, `budget`, `budget+1` con `budget = TELEGRAM_MESSAGE_LIMIT - _CONTINUATION_PREFIX_RESERVE = 4056`, **no** `4095/4096/4097` como sugiere la redacción literal de "Criterios de aceptación" (línea 742) — justificación completa en Gap #1.
- [ ] `chunk_for_telegram([])` → `[""]` (cubre la rama `chunks or [""]`).
- [ ] `_with_continuation_prefixes`: 1 chunk → no-op. ≥2 chunks → chunk 1 sin prefijo, chunks 2..N con `_(cont. parte i/n)_` correcto.
- [ ] 100% aislado: sin HTTP, sin mocks de Telegram.

**Pieza 5b — Integración `_run_analysis` multi-mensaje**
- [ ] Caso 1-chunk: idéntico byte a byte al comportamiento ya cubierto por los 7 tests existentes de "Mensaje de carga" (sección 1.1) — **estos 7 tests deben seguir pasando sin modificar sus asserts**, es el criterio de regresión más importante de toda esta pieza.
- [ ] Caso multi-chunk feliz (2-3 chunks): `loading_msg.edit_text` recibe chunk 1 sin prefijo; cada chunk siguiente vía `loading_msg.chat.send_message`, en orden, con prefijo correcto — mock de `Message`/`Chat` con `.chat.send_message` configurado (extensión de `_fake_text_update`).
- [ ] Caso `loading_msg is None` + multi-chunk: chunk 1 por `reply_fn`, resto por `.chat.send_message` sobre el `Message` devuelto.
- [ ] Caso falla `edit_text` del chunk 1 + multi-chunk: 1 reintento por `reply_fn` (mismo criterio ya validado por `SDD_mensaje_cargando.md`), resto por `.chat.send_message` sobre el nuevo `Message`.
- [ ] Caso falla el envío de un chunk intermedio → `logger.error` (test con `caplog`), se sigue intentando el siguiente chunk, ninguna excepción se propaga hacia `handle_text`.
- [ ] Caso `chunk_for_telegram` lanza excepción inesperada (mock con `side_effect=RuntimeError`) → 1 único mensaje con el marcador de la Decisión 19, `logger.error` con el texto completo (`caplog`), ninguna excepción se propaga. **Es el único `except Exception` genérico de todo el proyecto** — exige además un test negativo: una excepción producida en otro punto de `_deliver_all` (no dentro de `chunk_for_telegram`) NO debe caer en este mismo `except`.
- [ ] Cero llamadas nuevas a FMP/FRED/Treasury.gov en toda esta pieza — contar requests capturadas por el `MockTransport` antes/después.

**Pieza 6 — Reordenamiento de `build_summary`**
- [ ] Orden completo en un solo test, por índice de substring: Título → "*En una frase:*" (Veredicto) → "Tienda de Limonada" → "Ratios clave" → "Rentabilidad..." (si hay extras) → "Rango de Valor Justo" → "Pilares de buena empresa" → "Contexto de mercado" → "Encaje con tu perfil de riesgo" → nota de transparencia FMP.
- [ ] Mismo test repetido con `extras=None` (sección Extras ausente) — confirma que el resto del orden no se corre cuando un elemento intermedio de `parts` es `None`.

---

### 3. Cobertura mínima requerida

- El piso global `--cov-fail-under=75` del README **se mantiene sin cambios** como red de regresión general, pero **no alcanza por sí solo para esta spec**, por 3 motivos concretos:
  1. **Dilución**: la suite actual (262 tests) ya está al **98.10%** de cobertura total, verificado corriendo `pytest -v --cov=src/investbot --cov-report=term-missing --cov-fail-under=75` antes de escribir este bloque. Con esa base, se podrían agregar cientos de líneas nuevas mal testeadas (Piezas 1-6) y el `TOTAL` seguiría muy por encima de 75% sin que el gate lo note — mide el repo entero, no las líneas nuevas de esta spec.
  2. **El estándar ya vigente en el repo es más alto que 75%**: `rules.py`, `market_context.py`, `valuation.py`, `risk_fit.py`, `peers.py`, `db.py`, `security.py` están hoy al **100%**. Aceptar menos para las funciones nuevas de esta spec (`extract_key_metrics_extras`, `extract_vix_context`, `build_extras_section`, `build_veredicto_section`) sería una regresión de facto de la práctica ya vigente en el mismo repo, no un estándar nuevo inventado por `qa`.
  3. **Las 2 rutas de I/O nuevas señaladas en el encargo** (fetch de VIX/key-metrics propio, manejo de 4096) son la superficie de mayor riesgo real de esta spec: son las únicas 2 con ramas de fallo que, sin test, pueden hacer que Daniela reciba un mensaje incompleto o corrupto sin que nada lo detecte — el mismo escenario que motivó todo el hallazgo de la Ampliación #2 ("el texto me aparece a la mitad").
- **Piso explícito por pieza (adicional al 75% global, no en su reemplazo):**
  - `rules.extract_key_metrics_extras`, `market_context.extract_vix_context`, `summary.build_extras_section`, `summary.build_veredicto_section`, `query_handler.chunk_for_telegram`, `query_handler._split_oversized_part`, `query_handler._with_continuation_prefixes`, `query_handler._hard_truncate_with_marker`: **100% líneas y 100% ramas** — mismo nivel que ya tienen `rules.py`/`market_context.py`/`valuation.py`, no un estándar nuevo.
  - `query_handler._run_analysis`/`_deliver_all` (integración con mocks de Telegram): **100% de las 6 ramas de la matriz "Pieza 5b"** de arriba — medido como checklist de escenarios, no como % de líneas (el anidamiento de `try/except` hace que el % de líneas sea engañoso), mismo criterio que ya exige `SDD_mensaje_cargando.md` para esta misma función.
  - `summary.py` (modificado, no nuevo): mantener el ~99% actual sin bajar. Si tras los cambios queda alguna línea nueva sin cubrir, debe justificarse explícitamente en Momento 2 (mismo estándar que ya aplica a la única línea hoy sin cubrir, `summary.py:279`).

---

### 4. Gaps del spec — bloqueantes para escribir un test determinístico ahora

**Gap #1 — El boundary real de "sección demasiado grande" es `budget = 4056`, no 4096, pero "Criterios de aceptación" (línea 742) pide testear 4095/4096/4097.**
En la Decisión 16.2, `chunk_for_telegram` calcula `budget = limit - _CONTINUATION_PREFIX_RESERVE` (4096 − 40 = 4056) y usa ese `budget` — no `TELEGRAM_MESSAGE_LIMIT` (4096) — tanto para decidir si una `part` es "demasiado grande para un chunk" (`if len(part) > budget`) como para invocar `_split_oversized_part(part, budget)` (el parámetro formal de esa función se llama `limit`, pero el valor real que recibe en la llamada es `budget`). La Decisión 18 describe el mismo mecanismo en prosa como "una sección individual que por sí sola supera **4096** caracteres", y "Criterios de aceptación" (línea 742) pide testear específicamente "4095, 4096 y 4097". Con el pseudocódigo tal como está escrito, una sección de exactamente 4096 caracteres **ya es oversized** (4096 > 4056) — un test que asuma que 4096 es el punto de corte fallaría contra el pseudocódigo literal de la Decisión 16.2. Esto no es interpretable por `qa`/`implementer` sin una decisión de `architect`: ¿el `budget` reducido (4056) es efectivamente el límite real (y entonces la Decisión 18 y "Criterios de aceptación" deberían decir "supera budget", no "supera 4096"), o `_split_oversized_part` debería recibir `TELEGRAM_MESSAGE_LIMIT` en vez de `budget`? La matriz de la Pieza 5 arriba usa `budget` como supuesto de trabajo (es lo que dice el pseudocódigo literal), pero es una asunción de `qa`, no un hecho confirmado por `architect` — señalado explícito para no dejarlo a interpretación de `implementer`.

**Gap #2 — `_adobe_router` de test no distingue `symbol` en `/stable/quote`; sin arreglarlo primero, un test de VIX puede pasar en falso.**
El router actual (línea 27-28 de `test_query_handler.py`) devuelve el fixture de cotización de Adobe para **cualquier** `symbol` pedido a `/stable/quote`, sin filtrar por el query param. Si `implementer` agrega el fetch de VIX (Decisión #7) sin que el router se actualice primero, el `quote` que recibe `extract_vix_context` para `^VIX` sería en realidad el de ADBE (`price: 333.00`) — un dato numérico válido, `VixResult(valor=333.0, disponible=True)`, que no lanza ninguna excepción ni falla ningún assert existente (no hay ningún assert sobre VIX hoy). El bug pasaría inadvertido en CI. No es un "detalle a definir por `implementer`", es una precondición de testabilidad: el router y el fixture nuevo (`quote_vix.json`, ya previsto en "Artefactos a crear/modificar") deben existir **antes** de escribir cualquier assert sobre el valor del VIX, y el test end-to-end debe verificar explícitamente que el valor de VIX mostrado **≠** precio de ADBE — no alcanza con verificar que el bullet aparece.

**Gap #3 — El criterio de aceptación de la línea 723 ("los bullets de Ratios/Extras/Momentum/Peers no contienen 'estimado'/'aproximado'") es imposible de testear como negación sobre el texto completo.**
Con el VIX (Decisión #9) y los headers de Valor Justo (Decisión #10) mostrando literalmente "estimado"/"aproximado" en el **mismo** `build_summary` final, un test `assert "estimado" not in text` sobre el texto completo fallaría siempre por diseño — se contradice con los propios criterios de aceptación de VIX/Valor Justo de la misma spec (líneas 714-722). El test de regresión negativa de la línea 723 debe escribirse aislando las líneas/bullets específicos (`ratios_lines`, el output de `build_extras_section` en aislamiento, las líneas de momentum/peer comparison), no sobre `build_summary(...)` completo. Ya resuelto en la matriz de las Piezas 1/2 arriba (tests aislados por función), pero se señala porque la redacción literal del `architect` induce a un test mal diseñado si se sigue al pie de la letra.

**Gap #4 — La convención `*100` para roe/dividend_yield/payout_ratio es un supuesto de implementación, no un hecho verificado (ya reconocido por la propia spec, "Resolución (a)").**
Para `qa` esto significa: los tests que fijan `"roe": 0.18` → `"18.0%"` están testeando el **supuesto**, no un contrato confirmado con FMP real. Deben llevar un comentario explícito señalándolo (mismo estándar que ya usa `test_fmp_client.py` distinguiendo fixtures "sintético" vs "real" en su README), para que si en producción la convención resulta ser `roe: 18` (no `0.18`), el ajuste del test se entienda como "corrección de un supuesto ya documentado", no como una regresión de un criterio verde.

---

### Testabilidad
- [ ] Todas las funciones puras nuevas (`extract_key_metrics_extras`, `extract_vix_context`, `build_extras_section`, `build_veredicto_section`, `chunk_for_telegram`, `_split_oversized_part`, `_with_continuation_prefixes`) son funciones de módulo sin estado, sin I/O — mismo patrón que el 100% del resto de `rules.py`/`market_context.py`/`valuation.py`.
- [ ] Los 2 fetches best-effort nuevos (`own_metrics`, `vix_quote`) están aislados en bloques `try/except fmp_client.FMPError` dentro de `fetch_and_analyze`, mockeables vía `httpx.MockTransport` sin tocar lógica de negocio.
- [ ] El único `except Exception` genérico del proyecto (Decisión 19) está acotado a una única llamada, no envuelve ningún otro código — testeable con `side_effect` sin afectar el resto de `_run_analysis`.
- [ ] Ningún dato nuevo (ROE/deuda/dividendos/VIX) entra a `rules.evaluate_pillars`/`valuation.py`/`risk_fit.py` — verificable con los tests de regresión ya listados por `architect` (línea 756).

### Criterio de exit de QA
- Todos los tests de la matriz de la sección 2 pasan (suite verde), incluidos los 7 tests existentes de "Mensaje de carga" **sin modificar sus asserts**.
- Cobertura 100% líneas+ramas en las 8 funciones puras nuevas de la sección 3; 100% de las 6 ramas de integración de `_run_analysis`/`_deliver_all`; `--cov-fail-under=75` global sigue pasando (piso de regresión, no piso de esta spec).
- Sin tests ignorados o comentados para pasar CI. Flaky rate = 0 en la nueva suite.
- **Gaps #1-#4 resueltos explícitamente (por `architect` o por decisión documentada de Daniela) antes de que `implementer` toque `chunk_for_telegram`/`_adobe_router`/los tests de etiquetado negativo** — no se resuelven "sobre la marcha" durante la implementación.
