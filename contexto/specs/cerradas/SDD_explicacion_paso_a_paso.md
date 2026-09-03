# Spec: "Explicame paso a paso" — botón determinístico + botón con cuenta resuelta, menú siempre presente [Iter-1]

**Rol:** `architect` (spec base — segunda iteración sobre `SDD_menu_por_capas_explicaciones.md`, cerrada e implementada en `main`, más los 3 fixes de producción del 2026-09-02 ya incorporados: falso positivo del guard, nombre del modelo, escape de Markdown).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` — esta spec (a) cambia el contrato público de `calculate_dcf_fair_value` (única función del proyecto cuyo contrato cambia en esta spec — ver Decisión de diseño #7), (b) agrega una 4ª forma de `callback_data` (`xp:{id}:p:{code}`) que hay que validar con el mismo rigor que las 3 existentes, (c) amplía otra vez la superficie de datos hacia Ollama (cada leaf con paso a paso suma entre 2 y 6 campos numéricos nuevos al payload — nunca texto libre de terceros nuevo), y (d) introduce una superficie nueva a auditar: el bloque "🧮 Cuenta" es determinístico (nunca pasa por Ollama, mismo principio que "📐 Fórmula"/"📊 Fuente"), pero es el primer bloque determinístico que **incluye el resultado final ya resuelto con los números reales del ticker** — vale una revisión de que ningún caso "no calculable" arme una cuenta con `None` visible como texto ("Z = 1.2×None + ..."). No aplica `dba` (sin persistencia nueva). `frontend` no aplica (sin UI web). `backend` queda a discreción de `security`, igual que en las 2 specs anteriores de este proyecto.
**Estado:** spec nueva, sin iteraciones previas. No hay scope freeze — `implementer` no toca código hasta que `security` y `qa` agreguen sus criterios.

---

## Contexto

Daniela probó el menú por capas ya en producción y pidió dos cambios, ambos textuales y ya aclarados en conversación previa:

1. **Cada pregunta del menú necesita dos caminos, no uno solo**: un botón "normal" que muestra el dato tal cual (rápido, determinístico, costo cero — mismo criterio que ya usan `evt`/`inf` hoy) y un botón nuevo **"🎓 Explicame paso a paso"** que sí llama a Ollama, y que tiene que mostrar la fórmula con los NÚMEROS REALES del ticker reemplazados y la cuenta resuelta — su ejemplo textual: *"Z = 1.2×0.34 + 1.4×0.12 + 3.3×0.18 + 0.6×1.05 + 1.0×0.87 = 0.41 + 0.17 + 0.59 + 0.63 + 0.87 = 2.67"* — no la fórmula en abstracto (eso ya existe como `FORMULAS_TEXTO_LIBRE`/`FORMULAS_AVANZADO`, sigue siendo útil pero no alcanza para lo que pide acá).
2. **Después de CUALQUIER respuesta** (resultado normal o paso a paso), **el menú de navegación tiene que reaparecer en el mismo mensaje de respuesta** — sin un tap aparte. Queda a criterio del `architect` decidir si "el menú" son los botones de Nivel 2 de la categoría, o Nivel 1 — ver Decisión de diseño #2.

Esta spec construye sobre código real, leído completo, no sobre lo documentado en la spec cerrada anterior:

- **`src/investbot/ai_explain.py`** (694 líneas, código real): `ExplanationContext` (27 campos, líneas 89-129), `ExplanationContextStore` (117-187), `_get_owned_context` (190-205, Hallazgo 9 de `security` ya remediado — chat_id ownership), `_CALLBACK_MENU_RE`/`_CALLBACK_CATEGORY_RE`/`_CALLBACK_LEAF_RE` (210-212, 3 formas), `_payload_texto_libre`/`_payload_avanzado` (282-467, sub-dict por pregunta, superficie mínima), `_build_dato_line`/`_dato_texto_libre`/`_dato_avanzado` (495-591, el bloque "📌 Dato" ya determinístico), `_build_leaf_message` (593-606, orden fijo: header→Dato→respuesta Ollama→Fórmula/Fuente→disclaimer), `_build_deterministic_content` (609-630, `evt`/`inf`, nunca llaman a Ollama), `SYSTEM_PROMPT_EXPLAIN`/`_enforce_brevity`/`_no_new_protected_tokens` (633-682, guard sin cambios), `build_keyboard`/`build_category_keyboard` (757-805, Nivel 1/Nivel 2, **hoy sin `reply_markup` en ninguna respuesta de leaf** — confirmado leyendo `_dispatch_leaf`, líneas 848-919: tanto la rama determinística como la rama Ollama mandan texto plano, sin teclado — este es exactamente el gap que Daniela señala en el punto 2), `_dispatch_menu`/`_dispatch_category`/`_dispatch_leaf` (811-919, dispatch de las 3 formas actuales).
- **`src/investbot/ai_explain_content.py`** (437 líneas): `QuestionSpec` (21-24, hoy solo `label`/`pregunta_fija`/`requires_ollama` — **todas las preguntas salvo `evt`/`inf` tienen `requires_ollama=True`**, es decir hoy el botón "normal" de `gra`/`alz`/`pig`/etc. YA llama a Ollama; no existe la separación que pide Daniela), `CategorySpec`, `QUESTIONS_TEXTO_LIBRE`/`QUESTIONS_AVANZADO` (13+14 entradas), `CATEGORIES_TEXTO_LIBRE`/`CATEGORIES_AVANZADO`, `LEVEL1_TEXTO_LIBRE`/`LEVEL1_AVANZADO`, `FORMULAS_TEXTO_LIBRE`/`FORMULAS_AVANZADO`/`FUENTES_TEXTO_LIBRE`/`FUENTES_AVANZADO` (las 4 tablas de texto fijo, reutilizadas sin cambios de contenido por esta spec).
- **`src/investbot/advanced_scoring.py`** (577 líneas, código real): `calculate_altman_z`/`calculate_altman_z_prime_prime` (líneas 103-194) **YA calculan A, B, C, D, E como variables locales** (`working_capital`, `a`, `b`, `c`, `d`, `e`, líneas 136-142/182-187) pero `AltmanZResult` (95-100) solo expone `z`/`zona`/`campos_faltantes` — los 5 componentes se descartan apenas se suman. `calculate_piotroski_f_score` (309-376) recibe TODOS los números crudos (`net_income_t`/`t1`, `total_assets_t`/`t1`, `cfo_t`, `lt_debt_t`/`t1`, `current_assets/liabilities_t`/`t1`, `shares_t`/`t1`, `gross_profit_t`/`t1`, `revenue_t`/`t1` — líneas 322-338) como variables locales de la función, se los pasa a los 9 helpers `_criterio_*` (216-306, cada uno hace la comparación y devuelve solo `bool`/`None`), y `CriterioPiotroski` (202-205) solo guarda `nombre`/`cumplido` — los números que motivaron cada `bool` se descartan. `calculate_magic_formula_metrics` (427-465) calcula `capital_invertido`/`ev` como locales (456-457) pero `MagicFormulaResult` (419-424) solo expone `roic`/`earnings_yield`. `calculate_factor_score` (526-576) no necesita cambios — ya recibe `earnings_yield`/`roe`/`gross_margin`/`piotroski_score`/`piotroski_evaluables`/`beta` como parámetros (los mismos números vuelven a estar disponibles en el caller), y `FACTOR_UMBRALES`/`LOW_VOL_BETA_UMBRAL_BAJO`/`_ALTO` (479-488) ya son constantes nombradas (mismo patrón "Bug 2" que exige el guard).
- **`src/investbot/valuation.py`** (722 líneas, código real): `calculate_dcf_fair_value` (235-310) calcula internamente `fcf_proyectado` (lista de 5 valores, línea 294-298), `valor_presente`/`valor_terminal`/`valor_presente_terminal`/`equity_value` (300-309) — **todos se descartan, la función devuelve solo `equity_value / shares_outstanding` como `Optional[float]` (línea 310)**. `compute_valuation`/`compute_valuation_scenarios` (345-474, 543-708) SÍ calculan `wacc`/`wacc_escenario` y `g_fcf`/`g_fcf_escenario` como variables locales (líneas 447, 442-443, 621, 674-675) inmediatamente antes de llamar a `calculate_dcf_fair_value` — esos 2 números (a diferencia de la proyección año a año) están disponibles en el caller sin ningún cambio de contrato. `ScenarioValuationResult`/`ValuationResult` (313-342, 487-510) ya exponen `graham_g_original`/`graham_g_aplicado`/`graham_g_capped` (patrón Iter-4 ya establecido, precedente directo para esta spec) pero nada de DCF.
- **`src/investbot/risk_fit.py`** (40 líneas): `evaluate_risk_fit` (30-39) usa **literales sin nombre** `0.8`/`1.2` (líneas 32/34) — a diferencia de `advanced_scoring.LOW_VOL_BETA_UMBRAL_BAJO`/`_ALTO` (mismo concepto, ya nombrados ahí) — inconsistencia menor que esta spec corrige (Decisión de diseño #5).
- **`src/investbot/query_handler.py`**: `fetch_and_analyze_parts` calcula `eps_ttm`/`revenue`/`cost_of_revenue`/`market_cap`/`current_assets`/`current_liabilities`/`y_value`/`revenue_historial`/`net_income_historial`/`quote.get("yearHigh"/"yearLow"/"priceAvg50"/"priceAvg200")` como variables locales (líneas 296-419) — **ninguno de estos 13 valores viaja hoy a `explain_context_sink`** (confirmado leyendo el `.update(...)` completo, líneas 569-591: hoy solo van los 5 dicts/objetos ya agregados por la spec anterior). `advanced_command.py` ya tiene `balance_reciente`/`income_reciente`/`market_cap` como locales (usados para llamar a `calculate_altman_z`/`calculate_piotroski_f_score`/`calculate_magic_formula_metrics`) — no hace falta agregar NADA a `ExplanationContext` para `/avanzado`: basta con que los resultados tipados (`AltmanZResult`/`PiotroskiResult`/`MagicFormulaResult`) vengan más ricos, porque `context.altman`/`context.piotroski`/`context.magic` ya viajan completos como dicts.

---

## Estado objetivo

1. Cada pregunta del menú que tiene una fórmula/cuenta resoluble por el bot (22 de las 27 preguntas — ver tabla de la Decisión de diseño #3) muestra **2 botones hermanos** en el teclado de Nivel 2 (o de Nivel 1 si es suelta): **"📊 Ver dato"** (determinístico, sin Ollama, costo cero — comportamiento nuevo, hoy este botón único llama a Ollama) y **"🎓 Explicame paso a paso"** (llama a Ollama, muestra la fórmula con los números reales del ticker sustituidos y la cuenta resuelta, generada 100% en Python — nunca por Ollama — más 2-4 oraciones de Ollama explicando qué significa esa cuenta).
2. Las 5 preguntas sin fórmula resoluble (`mod`/`ben`/`ren` — síntesis narrativa o dato precalculado por FMP sin cómputo propio del bot — y `evt`/`inf`, ya determinísticas) mantienen exactamente **1 botón**, sin cambio de comportamiento salvo el punto 3.
3. **Toda respuesta de cualquier leaf** (determinística, paso a paso, o narrativa) llega con un teclado adjunto: el de Nivel 2 de su categoría (con "🔙 Menú" al final, igual que hoy) si la pregunta vive dentro de una categoría, o el de Nivel 1 si es una pregunta suelta — nunca sin botones (Decisión de diseño #2).
4. El bloque "🧮 Cuenta" (fórmula con números reales sustituidos y resultado de cada término) es determinístico, construido en Python a partir de datos que el bot ya calculó — nunca generado ni transcrito por Ollama, mismo principio ya establecido para "📐 Fórmula"/"📊 Fuente" (Decisión de diseño #5 de la spec cerrada, no reabierta).
5. Altman Z/Z'', Piotroski (los 3 sub-grupos + el general), Magic Formula (ROIC + Earnings Yield), Graham y Múltiplos tienen cuenta resuelta completa con TODOS los términos intermedios visibles — los modelos que Daniela priorizó explícitamente. DCF tiene cuenta PARCIAL (WACC y g proyectado sustituidos, proyección año a año resumida en una oración, no listada término a término) — ver Decisión de diseño #7, limitación documentada y justificada, no silenciosa.
6. Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR — igual que las 2 specs anteriores, todo dato nuevo es un subconjunto de variables que cada flujo YA calcula y hoy descarta.
7. El guard anti-invención, el rate limiter selectivo, el mensaje "🤔 Pensando…", D1 (Beneish siempre no calculable) y la sanitización de `sector`/`industry` se mantienen sin aflojar.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch)*

### 1. `QuestionSpec` gana un campo `variant` — 3 categorías, no una sola bandera booleana

`requires_ollama: bool` (hoy) no alcanza para expresar 3 comportamientos distintos. Se reemplaza por:

```python
@dataclass(frozen=True)
class QuestionSpec:
    label: str
    variant: str  # "dato_y_paso_a_paso" | "narrativa" | "deterministico"
    pregunta_narrativa: Optional[str] = None    # usada solo si variant == "narrativa"
    pregunta_paso_a_paso: Optional[str] = None  # usada solo si variant == "dato_y_paso_a_paso"
```

- **`"dato_y_paso_a_paso"`** (22 de 27 preguntas): 2 botones. "Ver dato" nunca llama a Ollama (nuevo). "Explicame paso a paso" llama a Ollama con la cuenta ya resuelta como dato garantizado (Decisión de diseño #4).
- **`"narrativa"`** (`mod`, `ben`, `ren` — 3 preguntas): 1 botón, comportamiento **idéntico al de hoy** (llama a Ollama, sin cuenta/sustitución porque no hay una fórmula propia del bot detrás — `mod` sintetiza 5 modelos distintos, `ben` siempre "no calculable" D1, `ren` son 5 campos que FMP ya entrega precalculados, el bot no los deriva de nada — ver Decisión de diseño #6 para por qué `ren` NO se degrada a determinístico puro).
- **`"deterministico"`** (`evt`, `inf` — 2 preguntas): 1 botón, comportamiento idéntico al de hoy (nunca llama a Ollama).

**Por qué 3 categorías y no 2**: colapsar `narrativa` dentro de `deterministico` perdería la explicación de Ollama que hoy YA funciona bien para `mod`/`ben`/`ren` (síntesis de 5 modelos, motivo de no-calculable, contexto de 5 métricas FMP) — sería una regresión de calidad que Daniela no pidió. Colapsar `narrativa` dentro de `dato_y_paso_a_paso` obligaría a inventar una "cuenta" donde no existe una fórmula real del bot — violaría el principio de "nunca inventar/aproximar un cálculo que el bot no hace" (mismo espíritu de D1 de Beneish).

### 2. El menú reaparece SIEMPRE — Nivel 2 de la categoría si la pregunta vive en una, Nivel 1 si es suelta

**Decisión: cada respuesta de leaf lleva adjunto el teclado de Nivel 2 de SU categoría (con "🔙 Menú" al final, reutilizando `build_category_keyboard` sin cambios) si la pregunta pertenece a una categoría; el teclado de Nivel 1 (reutilizando `build_keyboard` sin cambios) si es una pregunta suelta (`ver`/`inf` en texto libre; `mod`/`ben` en `/avanzado`).**

**Por qué esto y no siempre Nivel 1** (la alternativa que Daniela dejó abierta): el patrón de uso más probable después de preguntar "¿cuánto vale por Graham?" es seguir explorando LA MISMA categoría — "¿y por DCF? ¿y por Múltiplos?" — antes de saltar a otro tema. Reaparecer directo en Nivel 2 ahorra un tap por cada pregunta de seguimiento dentro del mismo tema, que es el caso de uso dominante (comparar sub-modelos entre sí, o los 9 criterios de Piotroski entre sí). "🔙 Menú" sigue siendo un solo tap de distancia si el usuario quiere cambiar de tema — no se pierde nada, solo se optimiza el camino más común. Para las preguntas sueltas (sin categoría) no hay "Nivel 2 propio" al que volver — el Nivel 1 completo ES su lista de hermanos naturales (mismo criterio).

**Consecuencia mecánica**: se agrega `category_of(kind: str, code: str) -> Optional[str]` a `ai_explain_content.py` (búsqueda inversa sobre `CATEGORIES_*`, función pura, O(1) con un dict precomputado a nivel de módulo) y `build_response_keyboard(kind, context_id, question_code, context) -> InlineKeyboardMarkup` a `ai_explain.py` (dispatchea a `build_category_keyboard` o `build_keyboard` según `category_of`). Los 3 puntos de envío de `_dispatch_leaf` (rama determinística, rama narrativa, rama paso a paso) pasan `reply_markup=build_response_keyboard(...)` — hoy ninguno lo hace.

### 3. Qué preguntas tienen cuenta resuelta — tabla completa, invasividad por pregunta

**Texto libre (13 preguntas):**

| `question_code` | `variant` | Cuenta resuelta (paso a paso) | Campos nuevos necesarios | Invasividad |
|---|---|---|---|---|
| `ver` | dato_y_paso_a_paso | `Precio actual $150.00 < Valor Justo Total $182.40 → Barata` | ninguno nuevo (ya en payload) | ninguna |
| `vf` | dato_y_paso_a_paso | `($175.20 + $182.40 + $189.10) / 3 = $182.23` (solo los modelos calculables, mismo criterio que `valor_justo_total`) | ninguno nuevo (ya en `scenarios`) | ninguna |
| `gra` | dato_y_paso_a_paso | `$8.20 × (8.5 + 2×9.4) × 4.4 / 4.2 = $182.40` | `eps_ttm`, `y_value` (2 nuevos, `g_aplicado` ya existe en `scenarios[esc]["graham_g_aplicado"]`) | baja |
| `dcf` | dato_y_paso_a_paso **parcial** | Ver Decisión de diseño #7 | `dcf_wacc`, `dcf_g_fcf`, `dcf_fcf_base` (3 nuevos, campos gratis en `compute_valuation_scenarios`) | media (contrato de `calculate_dcf_fair_value`) |
| `mul` | dato_y_paso_a_paso | `$8.20 × 22.3 = $182.86` | `eps_ttm` (compartido con `gra`; `per_promedio_peers` ya en `peer_comparison`) | baja |
| `rat` | dato_y_paso_a_paso | 4 sub-cuentas (liquidez/margen/PER/P·S), solo las que tengan dato | `current_assets`, `current_liabilities`, `revenue`, `cost_of_revenue`, `market_cap` (5 nuevos; `eps_ttm` compartido) | baja |
| `pil` | dato_y_paso_a_paso | 4 criterios con números: `Ingresos: $391B (2024) > $274B (2021) → creciente`, etc. | `revenue_reciente`, `revenue_antiguo`, `net_income_reciente`, `net_income_antiguo` (4 nuevos; liquidez ya en `ratios`) | baja |
| `ren` | **narrativa** (sin cuenta — ver Decisión de diseño #6) | — | ninguno | — |
| `rsk` | dato_y_paso_a_paso | `Beta 1.05 está entre 0.8 y 1.2 → perfil Moderado` | 2 constantes nombradas nuevas en `risk_fit.py` (Decisión de diseño #5; `beta` ya en `risk_fit`) | baja |
| `mom` | dato_y_paso_a_paso | `($150.00 − $198.23) / $198.23 × 100 = -24.3% vs. máx. 52 sem.` (y análogo para los otros 3 puntos de referencia disponibles) | `year_high`, `year_low`, `price_avg_50`, `price_avg_200` (4 nuevos; `precio_actual` ya existe) | baja |
| `cmp` | dato_y_paso_a_paso | `PER propio = $150.00 / $8.20 = 18.3 — PER promedio peers = 22.3` | `eps_ttm` (compartido; PER peers ya en `peer_comparison`) | baja |
| `evt` | deterministico | — (sin cambios) | ninguno | — |
| `inf` | deterministico | — (sin cambios) | ninguno | — |

**`/avanzado` (14 preguntas):**

| `question_code` | `variant` | Cuenta resuelta (paso a paso) | Campos nuevos necesarios | Invasividad |
|---|---|---|---|---|
| `mod` | **narrativa** (sin cambios) | — | ninguno | — |
| `alz` | dato_y_paso_a_paso | `Z = 1.2×0.34 + 1.4×0.12 + 3.3×0.18 + 0.6×1.05 + 1.0×0.87 = 0.41 + 0.17 + 0.59 + 0.63 + 0.87 = 2.67` (ejemplo textual de Daniela) | `a`,`b`,`c`,`d`,`e` en `AltmanZResult` (5 nuevos) | baja — prioridad Daniela |
| `azp` | dato_y_paso_a_paso | `Z'' = 6.56×0.34 + 3.26×0.12 + 6.72×0.18 + 1.05×1.05 = 2.23 + 0.39 + 1.21 + 1.10 = 4.93` | mismos 5 campos que `alz` (reutilizados, `e` no aplica) | baja — prioridad Daniela |
| `pig` | dato_y_paso_a_paso | `7 de 9 criterios evaluables cumplidos` (conteo, no requiere desglosar cada uno — eso lo hacen `pir`/`pia`/`pie`) | ninguno nuevo (`puntaje`/`criterios_evaluables` ya existen) | ninguna |
| `pir` | dato_y_paso_a_paso | `ROA: 0.18 > 0 → cumplido · CFO: $118B > 0 → cumplido · ROA creciente: 0.18 > 0.15 → cumplido · CFO > Utilidad: $118B > $97B → cumplido` (ejemplo con los 4 criterios del grupo) | `valores: Optional[dict]` en `CriterioPiotroski` (1 campo nuevo, reutilizado por `pir`/`pia`/`pie`) | baja — prioridad Daniela |
| `pia` | dato_y_paso_a_paso | análogo a `pir`, 3 criterios del grupo | mismo campo `valores` | baja — prioridad Daniela |
| `pie` | dato_y_paso_a_paso | análogo a `pir`, 2 criterios del grupo | mismo campo `valores` | baja — prioridad Daniela |
| `ben` | **narrativa** (D1, no reabrir — sin cambios) | — | ninguno | — |
| `mgr` | dato_y_paso_a_paso | `ROIC = $114B / ($42B + $45B) = $114B / $87B = 1.31 = 131%` | `ebit`, `capital_invertido` en `MagicFormulaResult` (2 nuevos) | baja — prioridad Daniela |
| `mge` | dato_y_paso_a_paso | `EY = $114B / ($2.8T + $108B − $61B) = $114B / $2.85T = 0.040 = 4.0%` | `ebit` (compartido con `mgr`), `ev` en `MagicFormulaResult` (1 nuevo; `market_cap`/`total_debt`/`cash` también se agregan para que la cuenta muestre el armado de EV, 3 nuevos más) | baja — prioridad Daniela |
| `aqv` | dato_y_paso_a_paso | `Earnings Yield 4.0% está entre 4% y 8% → medio` (reutiliza `mge`) | ninguno nuevo (umbral ya en `FACTOR_UMBRALES`, se agrega al payload) | baja |
| `aqq` | dato_y_paso_a_paso | `ROE 22% > 15% (+1) · Margen bruto 43% > 40% (+1) · Piotroski 7/9=78% > 75% (+1) → suma +3 → alto` | ninguno nuevo (`roe`/`gross_margin`/`piotroski_ratio` ya en el payload de `aqq`, umbrales se agregan al payload) | baja |
| `aqm` | dato_y_paso_a_paso | `Etiqueta de momentum: impulso_positivo → factor alto` (reutiliza la etiqueta ya calculada, sin números adicionales) | ninguno nuevo | ninguna |
| `aql` | dato_y_paso_a_paso | `Beta 1.05 está entre 0.8 y 1.2 → medio` (reutiliza el mismo patrón que `rsk`, umbrales ya nombrados en `advanced_scoring.py`) | ninguno nuevo (umbrales ya existen como constantes) | ninguna |

**Resumen**: 22 preguntas con cuenta resuelta (20 completas + DCF parcial + `pig` que es un conteo simple), 3 narrativas sin cambio de fondo (`mod`/`ben`/`ren`), 2 determinísticas sin cambio (`evt`/`inf`). De los modelos que Daniela priorizó explícitamente (Altman Z/Z'', Piotroski, Magic Formula), los 4 quedan con cuenta COMPLETA y son, en efecto, los de menor invasividad — confirma la intuición de Daniela en el pedido original.

### 4. Contrato Ollama para "paso a paso" — la cuenta viaja como dato garantizado, Ollama solo la interpreta

**Extiende, no reemplaza, el mecanismo ya aprobado de `_build_explain_payload` (superficie mínima por pregunta).** Para una pregunta `dato_y_paso_a_paso`:

```python
datos_del_contexto = _build_explain_payload(context, question_code)  # ya existe, ahora más rico
cuenta = _build_cuenta_line(context.kind, question_code, datos_del_contexto)  # NUEVO, 100% Python
datos_del_contexto["cuenta"] = cuenta  # la cuenta entra al payload ANTES de mandarlo a Ollama
```

`_build_cuenta_line` es una función pura nueva (dispatch por `question_code`, mismo patrón que `_build_dato_line`/`_dato_texto_libre`/`_dato_avanzado` ya existentes) que arma el string de la tabla de la Decisión de diseño #3 leyendo ÚNICAMENTE del mismo `datos_del_contexto` ya armado — nunca recalcula nada, nunca llama a `advanced_scoring.py`/`valuation.py` de nuevo. Si algún campo necesario es `None` (modelo no calculable para ese ticker — ej. Altman con `campos_faltantes`), `_build_cuenta_line` devuelve `None` (nunca arma un string con "None" visible) y el mensaje final omite el bloque "🧮 Cuenta", cayendo al mismo texto de "no calculable" que ya usa `_dato_avanzado`/`_dato_texto_libre` para el bloque "📌 Dato".

**Por qué la cuenta entra en `datos_del_contexto` (y por lo tanto en `datos_tokens` del guard) en vez de viajar por un canal aparte**: el guard `_no_new_protected_tokens` (sin cambios de mecanismo) calcula `datos_tokens` a partir de TODO `datos_del_contexto` serializado (línea 887-893 de `ai_explain.py`, sin cambios) — al incluir `cuenta` ahí, cualquier número que Ollama cite de la cuenta en su explicación (ej. mencionar "el 2.67 final" o "el término de 0.87 de ventas") ya es, por construcción, un subconjunto de `datos_tokens`. No hace falta ningún caso especial en el guard.

**`SYSTEM_PROMPT_PASO_A_PASO`** (constante nueva, NO reemplaza `SYSTEM_PROMPT_EXPLAIN` — ese sigue usándose sin cambios para las preguntas `narrativa`):

```
Sos un profesor de finanzas que explica en español rioplatense, en un
mensaje de chat. Vas a recibir un JSON con una pregunta puntual, los datos
ya calculados, y una clave "cuenta" con la fórmula YA RESUELTA paso a paso
(números reales, cada término calculado, resultado final).

Reglas estrictas:
1. La cuenta en "cuenta" YA ESTÁ CALCULADA Y ES CORRECTA — tu trabajo es
   explicar en 2 a 4 oraciones cortas QUÉ SIGNIFICA ese resultado o alguno
   de sus términos, nunca recalcularla ni repetirla palabra por palabra
   (el usuario ya la ve arriba de tu respuesta, repetirla desperdicia tu
   espacio de respuesta).
2. Usá ÚNICAMENTE los números/datos del JSON que te paso — nunca inventes,
   estimes ni completes un dato que no esté ahí.
3. Nunca dés una recomendación de compra/venta ni asesoramiento financiero
   personalizado — solo explicá qué significa el resultado.
4. Respondé ÚNICAMENTE con un objeto JSON de la forma
   {"respuesta": "..."}, sin texto antes ni después.
5. Con tono de análisis de inversionista: nombrá el modelo financiero
   ("modelo"/"modelos" en el JSON) al principio de tu respuesta y decí en
   general qué mide -- sin salirte de las 2 a 4 oraciones de la regla 1.
```

Mismo `format: "json"`, mismo `num_predict=220`, mismo timeout (`ai_rewrite.CONNECT_TIMEOUT_SECONDS`/`config.timeout_seconds`) que hoy — sin cambios de infraestructura de red.

### 5. `risk_fit.py` — umbrales nombrados (mismo patrón que `advanced_scoring.py`)

```python
# risk_fit.py — antes: literales 0.8/1.2 dentro de evaluate_risk_fit
BETA_UMBRAL_BAJO = 0.8   # < esto: compatible con Muy Conservador/Conservador
BETA_UMBRAL_ALTO = 1.2   # > esto: compatible con Agresivo
```

`evaluate_risk_fit` pasa a usar estas 2 constantes en vez de los literales — **cero cambio de comportamiento** (mismos valores, ahora nombrados). Necesario para que `rsk` pueda mandarlas a Ollama como dato garantizado (mismo criterio "Bug 2" ya aplicado a `total_pilares`/`total_modelos`/etc. — un número del marco conceptual del bot, no del ticker, tiene que viajar nombrado o el guard lo rechaza al no estar en ningún dato de origen).

### 6. `ren` se queda como `narrativa`, no se degrada a `deterministico` — decisión de continuidad, no de negocio

Los 5 campos de `ren` (ROE, Deuda/Patrimonio, Deuda Neta/EBITDA, Dividend Yield, Payout Ratio) vienen precalculados de `/key-metrics` de FMP — el bot los muestra tal cual, no los deriva de una fórmula propia (confirmado en la spec cerrada, Decisión #6, tabla `FUENTES_TEXTO_LIBRE["ren"]`, no reabierta). Por eso `ren` no tiene ni puede tener una "cuenta resuelta" en el sentido que pide Daniela — no hay términos que sustituir, es un valor de tercero.

La alternativa real era degradar `ren` a `deterministico` puro (perder la explicación de Ollama que hoy contextualiza esos 5 números — ej. "un ROE de 22% es alto para el sector X, pero la Deuda/Patrimonio de 1.8 indica..."). Se descarta: sería una regresión de calidad en un botón que hoy funciona bien, sin que Daniela lo haya pedido — la generalización de "botón normal = determinístico" aplica a preguntas que TIENEN una fórmula del bot detrás (esas SÍ pierden su narración por defecto porque ahora "Ver dato" es una alternativa más barata y la profundidad se mueve a "paso a paso"), no a las que nunca la tuvieron.

### 7. DCF — paso a paso PARCIAL, contrato de `calculate_dcf_fair_value` cambia (única función del proyecto que cambia de contrato en esta spec)

**El problema**: a diferencia de Altman/Piotroski/Magic Formula (donde todos los términos intermedios son variables locales de UNA función que YA recibe los datos crudos), el DCF tiene una proyección de 5 años + valor terminal + 2 descuentos — esos 4-6 números intermedios (`fcf_proyectado` año a año, `valor_presente`, `valor_terminal`, `valor_presente_terminal`, `equity_value`) están enterrados DENTRO de `calculate_dcf_fair_value`, que hoy devuelve solo `Optional[float]` (el resultado final por acción). Exponerlos exige cambiar su contrato público — 2 call sites (`compute_valuation`, `compute_valuation_scenarios`) y los tests directos de la función.

**Decisión: cambio de contrato ACOTADO, no la lista completa de 5 años.**

```python
@dataclass(frozen=True)
class DCFBreakdown:
    valor_por_accion: Optional[float]       # mismo valor que antes devolvía la función
    fcf_proyectado_final: Optional[float]   # último año de la proyección (año 5), no los 5
    valor_presente_flujos: Optional[float]  # suma de los 5 flujos descontados
    valor_terminal_descontado: Optional[float]  # valor terminal ya traído a presente
    equity_value: Optional[float]

def calculate_dcf_fair_value(...) -> DCFBreakdown:
    ...  # misma lógica de cálculo, sin cambios en las fórmulas
```

Los 2 call sites cambian de `result.valor_justo_dcf = calculate_dcf_fair_value(...)` a `dcf = calculate_dcf_fair_value(...); result.valor_justo_dcf = dcf.valor_por_accion` (mecánico) y `ScenarioValuationResult`/`ValuationResult` ganan `dcf_wacc`/`dcf_g_fcf`/`dcf_fcf_base` (ya locales gratis en el caller, sin cambio de contrato) + `dcf_valor_presente_flujos`/`dcf_valor_terminal_descontado`/`dcf_equity_value` (nuevos, del `DCFBreakdown`).

**Por qué NO se expone la lista completa de 5 años** (`fcf_proyectado`, los 5 valores): la cuenta de DCF con 5 años desglosados term a término (`FCF₁=$X, FCF₂=$Y, ..., FCF₅=$Z, cada uno descontado a (1+WACC)^t...`) es sustancialmente más larga que cualquier otra cuenta de esta spec (Altman tiene 5 términos que se suman UNA vez; DCF tendría 5 términos que se descuentan y sí o sí exigen mostrar el exponente `t` de cada uno para que la cuenta tenga sentido) — rompería el tope de caracteres razonable (Decisión de diseño #8) o forzaría un mensaje desproporcionadamente más largo que el resto del menú para un solo botón. La cuenta PARCIAL sigue cumpliendo el pedido de Daniela ("números reales, no fórmula abstracta"):

```
🧮 Cuenta: FCF base $109B, crece a g=8.3% anual (WACC=9.1%) → FCF proyectado
año 5 ≈ $161B. Flujos descontados a valor presente ≈ $612B + valor terminal
descontado ≈ $2.1T = valor de la empresa ≈ $2.71T → $182.10 por acción.
```

Esto SÍ son números reales sustituidos (no la fórmula en abstracto "proyección de FCF + valor terminal, descontados al WACC" que ya existe hoy) — solo que agrupa la proyección de 5 años en su resultado final (año 5) en vez de listar los 5 años individualmente. Se documenta explícitamente como limitación de esta iteración, no como omisión silenciosa — si Daniela pide después el desglose año a año completo, es aditivo (agregar `fcf_proyectado: list[float]` al `DCFBreakdown` ya existente, sin volver a tocar el contrato).

### 8. Presupuesto de caracteres — por qué 400 para "🧮 Cuenta" y ~1100 para el mensaje completo de paso a paso

Hoy no existe un tope sobre el mensaje COMPLETO de un leaf con Ollama — solo `_MAX_EXPLANATION_CHARS=480` acota la respuesta CRUDA de Ollama antes de anexarle los bloques fijos (📌 Dato/📐 Fórmula/📊 Fuente/disclaimer, sin tope propio hasta ahora). Esta spec agrega:

```python
_MAX_CUENTA_CHARS = 400  # nuevo — tope duro sobre el bloque "🧮 Cuenta"
```

**Por qué 400 y no menos**: la cuenta más larga de la tabla de la Decisión de diseño #3 es Altman Z (5 términos × 2 formas de mostrarlos — con coeficiente y resuelto — más el resultado final), que en la práctica ronda 140-160 caracteres con tickers reales. 400 deja margen 2.5x sobre el caso más largo conocido (incluyendo Piotroski con 4 criterios en `pir`, que ronda 200-250 caracteres) sin abrir la puerta a que una cuenta se vuelva un párrafo — si `_build_cuenta_line` alguna vez produce más de 400 caracteres para un ticker con números inusualmente largos (ej. montos en notación completa sin abreviar), se trunca con el mismo criterio ya usado por `_enforce_brevity` (corte en el último punto/coma dentro del límite, o corte duro + "…").

**Por qué NO se define un único tope sobre el mensaje completo**: el mensaje de paso a paso ya es la suma de 4 bloques independientes, cada uno con su propio tope ya establecido o nuevo — 📌 Dato (una línea corta, sin tope explícito porque `_dato_texto_libre`/`_dato_avanzado` ya producen strings acotados por construcción), 🧮 Cuenta (400, nuevo), respuesta de Ollama (480, sin cambios), disclaimer (constante fija, ~150 caracteres). La suma en el peor caso ronda 1100-1150 caracteres — muy por debajo del límite de 4096 de un mensaje de Telegram, y consistente con "un poco más largo que las explicaciones de 2-4 oraciones actuales" que pide Daniela (hoy el mensaje completo ronda 700-750 caracteres en el peor caso) sin volverse un reporte. No se define una constante de "tope total" separada porque sumar 4 topes ya acotados de forma independiente es, en sí mismo, el tope total — agregar una 5ª constante sería redundante.

### 9. "Ver dato" (botón determinístico nuevo, generalizado a las 22 preguntas `dato_y_paso_a_paso`)

Mismo formato que hoy usan `evt`/`inf` (Decisión de diseño #4 de la spec cerrada, generalizada): `DETERMINISTIC_PREFIX` ("📋 Contenido fijo (sin IA).") + 📌 Dato + 📐 Fórmula (si existe entrada en `FORMULAS_*`) + 📊 Fuente (si existe entrada en `FUENTES_*`) — **sin cuenta resuelta** (esa es exclusiva de "paso a paso") y **sin disclaimer** (no es contenido generado por IA, mismo criterio que `evt`/`inf` hoy, que tampoco lo llevan). No consume el rate limiter compartido, no manda "🤔 Pensando…" (no hay espera que comunicar), responde inmediato.

**Consecuencia de comportamiento explícita, ya implícita en el pedido de Daniela**: las 22 preguntas `dato_y_paso_a_paso` PIERDEN, como comportamiento por defecto de su primer botón, la narración de 2-4 oraciones de Ollama que tienen hoy — se mueve a ser opt-in detrás de "🎓 Explicame paso a paso". Esto es exactamente lo que Daniela pidió ("un botón normal... sin llamar a Ollama") — se documenta acá para que quede explícito en la spec, no como hallazgo nuevo a discutir.

---

## Decisiones abiertas para Daniela

*(genuinamente de producto/alcance — el resto de las decisiones de esta spec ya fueron delegadas explícitamente a criterio técnico por Daniela en el pedido original)*

**D1 — RESUELTO por Daniela 2026-09-02: versión PARCIAL para esta iteración** (WACC y crecimiento con números reales, proyección de 5 años resumida en el resultado final, no listada año a año). Ampliar a desglose año a año queda como pedido aditivo para una iteración futura si hace falta, sin rehacer nada de lo que se implemente ahora.

Detalle de la decisión original (para contexto, ya no bloquea): La Decisión de diseño #7 arriba propone una cuenta PARCIAL (WACC y crecimiento sustituidos, proyección resumida en el resultado del año 5, sin listar los 5 años uno por uno) para mantener el cambio de contrato de `calculate_dcf_fair_value` acotado (2 call sites, tests existentes) y el mensaje dentro del presupuesto de caracteres de esta spec. La alternativa es la cuenta COMPLETA (año a año, con el exponente de descuento de cada uno) — más fiel al pedido literal de Daniela pero con mayor invasividad de código (mismo `DCFBreakdown`, pero con `fcf_proyectado: list[float]` de 5 valores, y una cuenta de texto más larga que exige revisar de nuevo el tope de caracteres para ese caso puntual) y mayor superficie de regresión (más terreno para que el guard rechace por un formato de número inesperado en 5 términos en vez de en 1).

**Recomendación del `architect`: empezar con la versión PARCIAL en esta iteración.** Es aditivo — agregar el desglose año a año después no repite trabajo, solo extiende `DCFBreakdown` con un campo más y ajusta `_build_cuenta_line("dcf", ...)`, sin tocar nada de lo que ya haya quedado cerrado. Si Daniela prueba la versión parcial y le sigue faltando detalle, es un pedido puntual y barato de resolver en una iteración siguiente, no una re-arquitectura.

---

## Presupuesto / impacto

- **FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR: 0 llamadas nuevas, siempre.** Los ~24 campos nuevos entre `ExplanationContext` (texto libre) y los resultados tipados de `advanced_scoring.py`/`valuation.py` (avanzado) son variables que cada flujo YA calcula localmente y hoy descarta — confirmado archivo por archivo en "Estado actual" arriba.
- **Ollama:** el número de llamadas POSIBLES por análisis no cambia sustancialmente (sigue siendo ≤27, ahora con hasta 22 preguntas ofreciendo paso a paso en vez de 25 preguntas siempre-Ollama) — lo que cambia es que "Ver dato" (nuevo, costo cero) se vuelve la vía de entrada por defecto, así que en la práctica el consumo REAL de Ollama debería BAJAR respecto a hoy para usuarios que solo quieren el número, y solo sube para quienes explícitamente piden el paso a paso. `num_predict=220`/timeout sin cambios.
- **Memoria del VPS — `ExplanationContext`:** crece con ~24 campos nuevos (todos `float`/`Optional[float]` cortos, 8 bytes cada uno más overhead de Python) — estimado +0.3-0.5 KB por contexto, sobre los ~3-4 KB actuales (texto libre) / ~2-2.5 KB (`/avanzado`) de la spec anterior. Con `max_entries=500` sin cambios, el store completo pasa de ~1.5-2 MB a un estimado de ~1.8-2.5 MB — sigue despreciable frente a `mem_limit: 256m` de `docker-compose.prod.yml` (confirmado, línea 19). No hace falta bajar `max_entries`/`ttl_seconds`.
- **Rate limiter compartido:** sin cambio de mecanismo. "Ver dato" (22 preguntas nuevas con este botón) se suma a `evt`/`inf`/`:m`/`:c:{cat}` como tráfico que NO consume el balde — el balde protege exactamente lo mismo que protegía antes (llamadas a Ollama), ahora con una vía de entrada más barata disponible por defecto.
- **Tamaño de archivos:** `advanced_scoring.py` crece con ~15 líneas (campos nuevos en 3 dataclasses, sin tocar ninguna fórmula). `valuation.py` crece con `DCFBreakdown` (~10 líneas) + el cambio mecánico en 2 call sites (~6 líneas). `ai_explain_content.py` crece con la migración de `QuestionSpec` (nuevo campo `variant`, 27 entradas actualizadas) — sin cambios en `FORMULAS_*`/`FUENTES_*` (contenido idéntico). `ai_explain.py` gana `_build_cuenta_line` (dispatch de ~22 casos, similar en tamaño a `_build_dato_line` ya existente), `SYSTEM_PROMPT_PASO_A_PASO`, `_MAX_CUENTA_CHARS`, `category_of`/`build_response_keyboard`, y el dispatch de la 4ª forma de `callback_data`.

---

## Criterios de aceptación

### Botones y `callback_data`
- [ ] Cada pregunta con `variant="dato_y_paso_a_paso"` muestra 2 botones en su teclado de Nivel 2 (o Nivel 1 si es suelta): "📊 Ver dato" (`callback_data=xp:{id}:{code}`) y "🎓 Explicame paso a paso" (`callback_data=xp:{id}:p:{code}`) — test explícito por cada una de las 22 preguntas.
- [ ] Cada pregunta con `variant="narrativa"` o `variant="deterministico"` muestra exactamente 1 botón, `callback_data=xp:{id}:{code}` — sin botón `:p:` — test explícito para las 5 preguntas (`mod`/`ben`/`ren`/`evt`/`inf`).
- [ ] `callback_data` de la forma `xp:{id}:p:{code}` con un `code` de `variant` distinto a `dato_y_paso_a_paso` (ej. `xp:{id}:p:mod`) responde `EXPLAIN_INVALID_MSG`, sin excepción — mismo camino que un `question_code` desconocido.
- [ ] `callback_data` de la forma `xp:{id}:p:{code}` con un `code` inexistente en ninguna tabla responde `EXPLAIN_INVALID_MSG` — mismo camino que hoy.
- [ ] El regex `_CALLBACK_PASO_A_PASO_RE` (`^xp:([0-9a-f]{8}):p:([a-z]{2,4})$`) es mutuamente excluyente con las 3 formas existentes — test que confirma que ningún `callback_data` válido matchea 2 regex a la vez (mismo criterio que la revisión de `security` de la spec cerrada, sección 1).

### Menú siempre presente
- [ ] La respuesta de "Ver dato" de cualquier pregunta con categoría lleva `reply_markup` con el teclado de Nivel 2 de esa categoría (con "🔙 Menú" al final) — test por cada una de las 4 categorías de texto libre + 4 de `/avanzado`.
- [ ] La respuesta de "Explicame paso a paso" de cualquier pregunta con categoría lleva el mismo teclado de Nivel 2.
- [ ] La respuesta de una pregunta suelta (`ver`/`inf`/`mod`/`ben`/`ren`) lleva `reply_markup` con el teclado de Nivel 1 (categorías + sueltos) de su `kind`.
- [ ] `category_of(kind, code)` devuelve `None` para las 5 preguntas sueltas y el código de categoría correcto para las 22 restantes — test exhaustivo de las 27 preguntas.
- [ ] Ningún envío de `_dispatch_leaf` (ninguna de las 3 ramas: determinístico, narrativa, paso a paso) queda sin `reply_markup` — test de regresión negativo (assert que TODAS las llamadas a `send_message`/`edit_message_text` de `_dispatch_leaf` incluyen `reply_markup` no-`None`).

### Cuenta resuelta (paso a paso)
- [ ] Para las 20 preguntas con cuenta COMPLETA (todas las `dato_y_paso_a_paso` salvo `dcf`), el mensaje final incluye un bloque "🧮 Cuenta" con el resultado de CADA término individual antes de la suma/operación final y el resultado total — test por pregunta con un fixture de números conocidos, comparando el string exacto esperado (mismo criterio que Daniela pidió: "Z = 1.2×0.34 + ... = 0.41 + ... = 2.67", no solo el resultado final).
- [ ] Para `dcf`, el mensaje final incluye WACC y `g_fcf` sustituidos con sus valores reales, más el resultado agregado de la proyección (año 5) y el valor por acción final — sin listar los 5 años individualmente (Decisión de diseño #7, comportamiento esperado, no un bug).
- [ ] Para un ticker donde el modelo NO es calculable (ej. Altman con `campos_faltantes` no vacío, o Magic Formula con `disponible=False`), el bloque "🧮 Cuenta" está AUSENTE (nunca un string con "None"/valores faltantes visibles) — el mensaje cae al mismo texto de "no calculable" que ya usa `_dato_avanzado`/`_dato_texto_libre` para "📌 Dato" — test explícito por cada modelo "todo o nada" (Altman, Z'', Magic Formula) con datos incompletos.
- [ ] `_build_cuenta_line` es una función pura (dict de entrada → `Optional[str]`), sin I/O, testeable sin mockear Ollama — mismo criterio de testabilidad que `_build_dato_line`.
- [ ] La clave `"cuenta"` se agrega a `datos_del_contexto` ANTES de calcular `datos_tokens` para el guard — test que confirma que un número presente SOLO en la cuenta (no en ningún otro campo del payload) es aceptado por `_no_new_protected_tokens` si Ollama lo cita.
- [ ] `_MAX_CUENTA_CHARS=400` se aplica con el mismo criterio de corte que `_enforce_brevity` (último punto/coma dentro del límite, corte duro + "…" si no hay ninguno) — test con una cuenta simulada que excede el límite.

### "Ver dato" (botón determinístico nuevo)
- [ ] Para las 22 preguntas `dato_y_paso_a_paso`, el botón "Ver dato" responde sin llamar a Ollama (test con mock que assert-ea cero requests HTTP a Ollama), sin mensaje "🤔 Pensando…", sin consumir el rate limiter compartido (mismo test cruzado que ya existe para `evt`/`inf`, extendido a las 22 preguntas).
- [ ] El mensaje de "Ver dato" incluye 📌 Dato + 📐 Fórmula (si existe en `FORMULAS_*`) + 📊 Fuente (si existe en `FUENTES_*`) — NUNCA el bloque "🧮 Cuenta" (exclusivo de paso a paso) — test explícito de ausencia.
- [ ] El mensaje de "Ver dato" NO incluye `DISCLAIMER_NO_ASESORAMIENTO` (mismo criterio que `evt`/`inf` hoy, contenido no generado por IA) — test de regresión explícito.

### Preguntas `narrativa` (`mod`/`ben`/`ren`) y `deterministico` (`evt`/`inf`)
- [ ] El comportamiento de `mod`/`ben`/`ren` es byte-idéntico al de antes de esta spec, salvo el `reply_markup` nuevo (mismo `_build_leaf_message`, mismo `SYSTEM_PROMPT_EXPLAIN`, mismo guard) — test de regresión snapshot.
- [ ] El comportamiento de `evt`/`inf` es byte-idéntico al de antes de esta spec, salvo el `reply_markup` nuevo — test de regresión snapshot.
- [ ] Ninguna de estas 5 preguntas acepta la forma `xp:{id}:p:{code}` (cubierto también arriba en "Botones y `callback_data`").

### Campos nuevos — sin cálculo nuevo, solo exposición
- [ ] `AltmanZResult`/`calculate_altman_z`/`calculate_altman_z_prime_prime`: los campos `a`/`b`/`c`/`d`/`e` devueltos son EXACTAMENTE los mismos valores que ya se usan para sumar `z` (test que reconstruye `z` desde los campos expuestos con los coeficientes documentados y confirma igualdad con `result.z`).
- [ ] `MagicFormulaResult`: `ebit`/`capital_invertido`/`ev`/`market_cap`/`total_debt`/`cash` reconstruyen `roic`/`earnings_yield` con la misma fórmula documentada — mismo criterio de test que Altman.
- [ ] `CriterioPiotroski.valores`: para cada uno de los 9 criterios, el dict contiene las magnitudes reales que determinaron `cumplido` (ej. `roa_actual`/`roa_anterior` para `roa_creciente`) — test por criterio con un fixture donde se conoce el resultado esperado.
- [ ] `DCFBreakdown`: `valor_por_accion` es idéntico al `Optional[float]` que la función devolvía antes del cambio de contrato — test de regresión que corre el mismo caso con la firma vieja (guardada como fixture) y la nueva, comparando el resultado final.
- [ ] `ValuationResult`/`ScenarioValuationResult`: `dcf_wacc`/`dcf_g_fcf`/`dcf_fcf_base`/`dcf_valor_presente_flujos`/`dcf_valor_terminal_descontado`/`dcf_equity_value` son `None` en cualquier escenario donde `valor_justo_dcf` también es `None` (mismo criterio "todo o nada" que el resto del proyecto) — test explícito.
- [ ] `risk_fit.evaluate_risk_fit` con las constantes nombradas `BETA_UMBRAL_BAJO`/`BETA_UMBRAL_ALTO` produce EXACTAMENTE los mismos resultados que la versión con literales, para el mismo conjunto de casos de test ya existente — test de regresión byte a byte.

### Guard anti-invención (extendido, no aflojado)
- [ ] Las 22 preguntas `dato_y_paso_a_paso`, en su camino "paso a paso", pasan por `_no_new_protected_tokens` con el mismo criterio de subconjunto — test explícito por cada una con una respuesta simulada que alucina un número/ticker no presente ni en `datos_del_contexto` ni en `cuenta`.
- [ ] `_build_explain_payload` de cada una de las 22 preguntas sigue conteniendo SOLO el sub-dict necesario para esa pregunta puntual (superficie mínima, sin cambios de criterio) — los campos nuevos (`a`/`b`/`c`/`d`/`e`, `eps_ttm`, etc.) se agregan ÚNICAMENTE al sub-dict de la pregunta que los necesita, nunca a todas.
- [ ] `SYSTEM_PROMPT_PASO_A_PASO` se usa exclusivamente en el camino de las 22 preguntas `dato_y_paso_a_paso`; `SYSTEM_PROMPT_EXPLAIN` (sin cambios) se usa exclusivamente en `mod`/`ben`/`ren` — test que confirma el prompt exacto enviado en el mock de Ollama para 1 pregunta de cada categoría.

### Regresión
- [ ] La suite completa (`pytest -q`) sigue en verde, incluidos todos los tests existentes de `test_ai_explain.py`/`test_advanced_scoring.py`/`test_valuation.py`/`test_risk_fit.py`/`test_query_handler.py`/`test_advanced_command.py` actualizados al nuevo contrato de `calculate_dcf_fair_value` y a la migración de `QuestionSpec`.
- [ ] D1 (Beneish siempre no calculable) sigue verificado exactamente igual — `ben` no cambia de comportamiento salvo el `reply_markup`.
- [ ] El guard, el rate limiter selectivo, el mensaje "🤔 Pensando…", y la sanitización de `sector`/`industry` (hallazgo 1 BLOQUEANTE de `security`, spec original, no reabierto) siguen aplicando sin cambios de criterio.

---

## Artefactos a crear/modificar

- `src/investbot/advanced_scoring.py` → `AltmanZResult` gana `a`/`b`/`c`/`d`/`e` (`Optional[float]`); `calculate_altman_z`/`calculate_altman_z_prime_prime` los populan desde variables ya locales (sin tocar la fórmula de `z`); `CriterioPiotroski` gana `valores: Optional[dict[str, float]] = None`; `calculate_piotroski_f_score` los popula desde las variables ya locales de la función (sin tocar los 9 helpers `_criterio_*`); `MagicFormulaResult` gana `ebit`/`capital_invertido`/`ev`/`market_cap`/`total_debt`/`cash` (`Optional[float]`); `calculate_magic_formula_metrics` los popula desde variables ya locales.
- `src/investbot/valuation.py` → `DCFBreakdown` (dataclass nueva); `calculate_dcf_fair_value` cambia de `-> Optional[float]` a `-> DCFBreakdown`; `compute_valuation`/`compute_valuation_scenarios` actualizan sus 2 call sites (mecánico) y agregan `dcf_wacc`/`dcf_g_fcf`/`dcf_fcf_base`/`dcf_valor_presente_flujos`/`dcf_valor_terminal_descontado`/`dcf_equity_value` a `ValuationResult`/`ScenarioValuationResult` (+ `as_dict()` de ambas).
- `src/investbot/risk_fit.py` → `BETA_UMBRAL_BAJO`/`BETA_UMBRAL_ALTO` (constantes nuevas, nombran los literales `0.8`/`1.2` ya existentes); `evaluate_risk_fit` los usa en vez de los literales (cero cambio de comportamiento).
- `src/investbot/ai_explain_content.py` → `QuestionSpec` migra de `pregunta_fija`/`requires_ollama` a `variant`/`pregunta_narrativa`/`pregunta_paso_a_paso` (27 entradas actualizadas en `QUESTIONS_TEXTO_LIBRE`/`QUESTIONS_AVANZADO`, contenido de las preguntas fijas reescrito para la variante paso a paso donde aplica); `category_of(kind, code) -> Optional[str]` (función nueva, búsqueda inversa precomputada). `FORMULAS_*`/`FUENTES_*` sin cambios de contenido.
- `src/investbot/ai_explain.py` → `_payload_texto_libre`/`_payload_avanzado` extendidos con los campos nuevos por pregunta (Decisión de diseño #3); `_build_cuenta_line(kind, question_code, datos) -> Optional[str]` (función nueva, dispatch de 22 casos); `SYSTEM_PROMPT_PASO_A_PASO`/`_MAX_CUENTA_CHARS` (constantes nuevas); `build_response_keyboard(kind, context_id, question_code, context)` (función nueva, usa `category_of`); `_CALLBACK_PASO_A_PASO_RE` (regex nuevo); `_dispatch_leaf` reescrito para dispatchear 3 variantes × la forma `:p:` nueva, y para adjuntar `reply_markup` en las 3 ramas de envío; `_build_leaf_message` gana un parámetro opcional `cuenta: Optional[str]` (se inserta entre "📌 Dato" y la respuesta de Ollama cuando está presente).
- `src/investbot/query_handler.py` → `fetch_and_analyze_parts`: `explain_context_sink.update(...)` agrega ~15 campos nuevos (todos ya calculados localmente, ver tabla de la Decisión de diseño #3) — cero cálculo nuevo, cero llamada HTTP nueva.
- `src/investbot/ai_explain.py` (`ExplanationContext`) → gana ~15 campos nuevos para `kind="texto_libre"` (`eps_ttm`, `y_value`, `current_assets`, `current_liabilities`, `revenue`, `cost_of_revenue`, `market_cap`, `revenue_reciente`, `revenue_antiguo`, `net_income_reciente`, `net_income_antiguo`, `year_high`, `year_low`, `price_avg_50`, `price_avg_200`) — `kind="avanzado"` no gana campos nuevos en `ExplanationContext` (los dicts `altman`/`piotroski`/`magic` ya viajan completos y ahora son más ricos por los cambios en `advanced_scoring.py`).
- `tests/test_advanced_scoring.py` → tests de los campos nuevos de `AltmanZResult`/`CriterioPiotroski`/`MagicFormulaResult` (a completar por `qa`).
- `tests/test_valuation.py` → tests del nuevo contrato de `calculate_dcf_fair_value`/`DCFBreakdown`, tests de regresión del valor final idéntico al de antes del cambio de contrato.
- `tests/test_risk_fit.py` → test de regresión de `BETA_UMBRAL_BAJO`/`BETA_UMBRAL_ALTO`.
- `tests/test_ai_explain.py` → tests de las 22 preguntas con 2 botones, la 4ª forma de `callback_data`, `_build_cuenta_line` por pregunta, `build_response_keyboard`/`category_of`, "Ver dato" sin Ollama para las 22, regresión de `mod`/`ben`/`ren`/`evt`/`inf` (a completar por `qa`).
- `tests/test_query_handler.py` → tests de `explain_context_sink` con los ~15 campos nuevos.

---

## Restricciones — no reabrir

- **D1 (Beneish M-Score, `advanced_scoring.py`)**: sigue siempre "no calculable con los datos disponibles". `ben` sigue `narrativa`, sin paso a paso posible por definición.
- **Guard anti-invención (`_no_new_protected_tokens`)**: mismo mecanismo, mismo criterio de subconjunto — la cuenta entra AL payload antes del cálculo de `datos_tokens` (Decisión de diseño #4), no hay caso especial nuevo en el guard en sí.
- **Rate limiter compartido, misma clave (`str(chat_id)`)**: sin cambios de mecanismo — "Ver dato" (22 preguntas nuevas con este botón) se suma a la lista de acciones que NO lo consumen, mismo criterio ya establecido para `evt`/`inf`/`:m`/`:c:{cat}`.
- **Mensaje "🤔 Pensando…"**: sin cambios de texto, se mantiene para `narrativa` y para el camino "paso a paso" de `dato_y_paso_a_paso` — "Ver dato" nunca lo necesita (sin espera que comunicar).
- **`sector`/`industry` — sanitización (hallazgo 1 BLOQUEANTE de `security`, spec original)**: sin cambios — ningún campo nuevo de esta spec es texto libre de FMP, todos son numéricos (`float`) o ya sanitizados (`sector` en `mod` ya pasa por `_validated_sector`).
- **Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR**: sin excepción, en ningún punto de esta spec.
- **Fórmulas de `advanced_scoring.py`/`valuation.py`/`risk_fit.py`**: ninguna cambia — esta spec es exclusivamente de EXPOSICIÓN de valores intermedios ya calculados internamente, salvo el cambio de contrato de `calculate_dcf_fair_value` (Decisión de diseño #7), que tampoco toca la lógica de cálculo, solo qué devuelve.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_explicacion_paso_a_paso.md` (esta spec)

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — cubre botones/callback_data (4ª forma), menú siempre presente, cuenta resuelta, "Ver dato" determinístico, preguntas narrativa/deterministico sin cambios, campos nuevos sin cálculo nuevo, guard extendido, y regresión.

### Decisiones de diseño tomadas (para que `implementer` no las reabra)
1. `QuestionSpec.variant` — 3 categorías (`dato_y_paso_a_paso`/`narrativa`/`deterministico`) reemplazan el booleano `requires_ollama`.
2. El menú reaparece SIEMPRE tras cualquier respuesta — Nivel 2 de la categoría si la pregunta pertenece a una, Nivel 1 si es suelta.
3. Tabla completa de qué preguntas tienen cuenta resuelta (22 de 27), con el campo nuevo exacto que necesita cada una y su nivel de invasividad.
4. La cuenta se agrega a `datos_del_contexto` ANTES del cálculo del guard — Ollama la recibe como dato garantizado, nunca la genera.
5. `risk_fit.py` gana constantes nombradas (paridad con `advanced_scoring.py`).
6. `ren` se queda como `narrativa` (no se degrada a determinístico puro) — preserva la explicación de Ollama que hoy ya funciona bien para un dato sin fórmula propia del bot.
7. DCF: cuenta PARCIAL, único cambio de contrato de función de toda la spec (`calculate_dcf_fair_value` → `DCFBreakdown`) — decisión D1 abierta a Daniela sobre si ampliar a desglose año a año.
8. `_MAX_CUENTA_CHARS=400`, presupuesto total de mensaje ~1100 caracteres (suma de 4 bloques ya acotados independientemente).
9. "Ver dato" generalizado a las 22 preguntas — nunca llama a Ollama, mismo criterio de costo cero que `evt`/`inf`.

### Puntos que señalar explícitamente a `security`
- El bloque "🧮 Cuenta" es el primer contenido determinístico de esta feature que muestra el RESULTADO FINAL ya resuelto (no solo la fórmula en abstracto o un dato puntual) — vale confirmar que ningún caso "no calculable" arme una cuenta con `None` visible como texto (ya cubierto por un criterio de aceptación explícito, pero vale una revisión de código directa en `_build_cuenta_line`).
- 4ª forma de `callback_data` (`xp:{id}:p:{code}`) — la regex nueva y el dispatch deben rechazar sin excepción cualquier combinación fuera de las 4 formas válidas ahora vigentes, mismo criterio que las 3 anteriores (incluida la combinación `:p:` con un `code` de `variant` incorrecta).
- El cambio de contrato de `calculate_dcf_fair_value` es el único cambio de firma de función de esta spec — vale confirmar que los 2 call sites y cualquier test directo de la función quedan actualizados sin que el valor final (`valor_por_accion`) cambie de resultado para ningún caso ya cubierto por tests existentes.
- Superficie de datos hacia Ollama ampliada otra vez (cada pregunta `dato_y_paso_a_paso` suma 0-6 campos numéricos nuevos, nunca texto libre de terceros) — mismo mecanismo de superficie mínima por pregunta ya confirmado 2 veces por `security` en las specs anteriores.

---

## Revisión de seguridad

**Rol:** `security` — revisión de la spec (sin implementar código), sobre el código real (`ai_explain.py` 694 líneas, `ai_explain_content.py`, `advanced_scoring.py`, `valuation.py`, `risk_fit.py`, `bot.py`) leído completo para los 5 puntos de auditoría solicitados, no solo sobre lo que la spec describe.

**Veredicto general: 1 hallazgo BLOQUEANTE (contrato de `calculate_dcf_fair_value` incompleto en los puntos de retorno temprano), 3 mejoras recomendadas no bloqueantes. Los 4 mecanismos de seguridad ya existentes (guard anti-invención, sanitización sector/industry, rate limiter selectivo, superficie mínima por pregunta) se extienden correctamente, sin aflojar ningún criterio.**

### [BLOQUEANTE] — Contrato de `calculate_dcf_fair_value` sin especificar en sus 4 puntos de retorno temprano

**CWE**: CWE-476 — NULL Pointer Dereference (equivalente Python: `AttributeError` sobre `None`)
**OWASP**: A04:2025 — Insecure Design (cambio de contrato sin cubrir todos los caminos de retorno)
**ASVS**: V1.1 (verificación de que el diseño cubre todos los flujos de error)

#### Descripción
La Decisión de diseño #7 (línea 184) muestra el nuevo contrato:
```python
def calculate_dcf_fair_value(...) -> DCFBreakdown:
    ...  # misma lógica de cálculo, sin cambios en las fórmulas
```
pero no dice explícitamente qué debe devolver la función en sus 4 puntos de retorno temprano — hoy (código real, `valuation.py`) son 4 `return None` distintos:

```python
# Archivo: src/investbot/valuation.py, líneas 269-277 y 287-288
if not fcf_historial or len(fcf_historial) < (CAGR_MIN_N_AÑOS * periodos_por_anio) + 1:
    return None
if shares_outstanding is None or shares_outstanding <= 0:
    return None
if wacc is None or wacc <= terminal_growth:
    return None
...
    g_fcf = calculate_cagr(fcf_reciente, fcf_antiguo, n_años)
    if g_fcf is None:
        return None
```

Si el `implementer` convierte solo el `return equity_value / shares_outstanding` final (línea 310) a `return DCFBreakdown(...)` y deja estos 4 `return None` tal cual — algo perfectamente compatible con la instrucción literal "misma lógica de cálculo, sin cambios en las fórmulas" —, la función deja de cumplir su propio contrato de tipo (`-> DCFBreakdown`) exactamente en el caso "DCF no calculable", que es el caso donde HOY el código ya depende de recibir `None` limpio:

```python
# Archivo: src/investbot/valuation.py, líneas 451 y 676 (2 call sites)
result.valor_justo_dcf = calculate_dcf_fair_value(...)
if result.valor_justo_dcf is None:               # línea 458 / 684
    result.modelos_excluidos.append(ModeloExcluido("dcf", "dcf_no_calculable"))
```

Con el contrato nuevo esto se reescribe (mecánico, según la propia spec) a `dcf = calculate_dcf_fair_value(...); result.valor_justo_dcf = dcf.valor_por_accion`. Si en alguno de los 4 casos de arriba `calculate_dcf_fair_value` sigue devolviendo `None` en lugar de un `DCFBreakdown` con los 5 campos en `None`, `dcf.valor_por_accion` lanza `AttributeError: 'NoneType' object has no attribute 'valor_por_accion'` — una excepción no capturada por ningún `try/except` de `compute_valuation`/`compute_valuation_scenarios`.

**Esto no es un caso raro**: `wacc <= terminal_growth` y CAGR de FCF no calculable son los motivos de exclusión de DCF más comunes del proyecto (tickers jóvenes, FCF negativo o errático, `y` no disponible) — el criterio de aceptación existente ("`DCFBreakdown`: `valor_por_accion` es idéntico al `Optional[float]` que la función devolvía antes... test de regresión que corre el mismo caso con la firma vieja") solo compara el caso feliz (DCF calculable), no ejercita los 4 caminos de exclusión con el tipo de retorno nuevo — un test que solo cubre el camino feliz puede pasar en verde con este bug presente.

#### Escenario de explotación (disponibilidad, no confidencialidad)
No es un vector de ataque externo — es un bug de contrato que un usuario legítimo dispara sin intención: cualquier ticker donde el DCF no sea calculable (ejemplo: empresa sin historial de FCF suficiente, o `wacc` no supera el `terminal_growth`) hace que `/valor` o `/avanzado` completo lance una excepción no manejada. `bot.py` línea 121 registra `application.add_error_handler(_on_error)`, y `_on_error` (líneas 39-44) solo hace `logger.exception(...)` — **nunca responde nada al usuario ni al chat**. El resultado observable es: el bot se queda callado (o, si la excepción ocurre después de mandar "🤔 Pensando…" en algún flujo relacionado, ese mensaje queda pegado para siempre) — no se filtra un traceback crudo al usuario (el `_on_error` global ya lo evita, correcto), pero SÍ se rompe la disponibilidad de una función central del bot (no solo del botón nuevo de "paso a paso") para un conjunto de tickers nada marginal.

#### Remediación
Agregar a la Decisión de diseño #7 (o como criterio de aceptación nuevo, no reabre nada ya cerrado) una instrucción explícita:

```python
_DCF_NO_CALCULABLE = DCFBreakdown(
    valor_por_accion=None,
    fcf_proyectado_final=None,
    valor_presente_flujos=None,
    valor_terminal_descontado=None,
    equity_value=None,
)

def calculate_dcf_fair_value(...) -> DCFBreakdown:
    if not fcf_historial or len(fcf_historial) < (CAGR_MIN_N_AÑOS * periodos_por_anio) + 1:
        return _DCF_NO_CALCULABLE
    if shares_outstanding is None or shares_outstanding <= 0:
        return _DCF_NO_CALCULABLE
    if wacc is None or wacc <= terminal_growth:
        return _DCF_NO_CALCULABLE
    ...
        if g_fcf is None:
            return _DCF_NO_CALCULABLE
    ...
    return DCFBreakdown(
        valor_por_accion=equity_value / shares_outstanding,
        fcf_proyectado_final=fcf_proyectado[-1],
        valor_presente_flujos=valor_presente,
        valor_terminal_descontado=valor_presente_terminal,
        equity_value=equity_value,
    )
```

Y agregar un criterio de aceptación explícito (falta hoy en la lista de "Campos nuevos"): *"`calculate_dcf_fair_value` devuelve una instancia de `DCFBreakdown` (nunca `None` desnudo) en los 4 caminos de exclusión (`fcf_historial` insuficiente, `shares_outstanding` inválido, `wacc` no calculable o `<= terminal_growth`, `g_fcf`/CAGR no calculable) además del camino feliz — test explícito por cada uno de los 4, no solo el de regresión del valor final."*

**Esfuerzo estimado**: 15-20 minutos (constante `_DCF_NO_CALCULABLE` + 4 sustituciones + 1 test parametrizado con los 4 casos) — bloquea el paso a `implementer` hasta que la spec lo especifique explícitamente, pero no requiere ningún rediseño.
**Referencia**: CWE-476, OWASP A04:2025

---

### Confirmaciones — los 5 puntos de auditoría solicitados

1. **Campos nuevos siempre numéricos, nunca texto libre de FMP sin sanitizar** — CONFIRMADO en código real. `advanced_scoring._get_num` (línea 46-53) descarta cualquier valor que no pase `_is_valid_number` antes de que `a`/`b`/`c`/`d`/`e` (Altman, líneas 136-142), los 9 `_criterio_*` (Piotroski, líneas 216-306) o `ebit`/`capital_invertido`/`ev` (Magic Formula) se calculen — los campos nuevos que expondrán `AltmanZResult`/`CriterioPiotroski.valores`/`MagicFormulaResult` son sub-productos aritméticos de floats ya validados, mismo patrón que `sector` (allow-list, `ai_explain.py` línea 240-243) e `industry` (excluido siempre). Ningún campo nuevo de esta spec es un string de FMP sin pasar por `_get_num` o una allow-list.

2. **Guard anti-invención — la cuenta la resuelve Python, no es solo una instrucción de prompt** — CONFIRMADO en código real, no es solo `SYSTEM_PROMPT_PASO_A_PASO`. `_no_new_protected_tokens` (`ai_explain.py` línea 677-681) opera sobre `ai_rewrite.protected_tokens()` (`ai_rewrite.py` línea 158-164, regex `_PROTECTED_TOKEN_RE`), que extrae con una expresión regular real cualquier número/✅/❌/SÍ/NO/ticker de la respuesta CRUDA de Ollama — no depende de que el modelo "obedezca" la regla 1 del prompt ("la cuenta ya está calculada, no la recalcules"). Si Ollama hiciera mal una cuenta y mencionara en su prosa un resultado que no coincide con ningún token de `datos_tokens` (que incluirá `cuenta` antes del cálculo del guard, línea 887-893, confirmado por la Decisión de diseño #4), ese token falla el subset-check y la respuesta se descarta completa (`raise _ExplainUnavailable()`, línea 744-749) — cae a `EXPLAIN_UNAVAILABLE_MSG`, nunca se muestra al usuario. Mecanismo genuino de código, no una instrucción que Ollama pudiera ignorar impunemente.

3. **4ª forma de `callback_data` — sin ambigüedad con las 3 existentes** — CONFIRMADO por análisis de los 4 regex. `_CALLBACK_LEAF_RE` (`^xp:([0-9a-f]{8}):([a-z]{2,4})$`) exige que el segmento final sea SOLO letras minúsculas anclado con `$`; la forma nueva propuesta (`^xp:([0-9a-f]{8}):p:([a-z]{2,4})$`) siempre contiene un `:` en ese segmento (p. ej. `p:alz`), que rompe el patrón `[a-z]{2,4}$` de `_CALLBACK_LEAF_RE` (los `:` no son `[a-z]`) — mismo principio de exclusión mutua por el que `_CALLBACK_CATEGORY_RE` (`c:`) y `_CALLBACK_MENU_RE` (`m`) ya conviven sin colisión hoy. No hay ningún `question_code` de 1 letra en las tablas actuales (todos son 2-4 letras) que pudiera confundirse con el literal `p:` seguido de más código. Diseño sano, consistente con el rigor ya aplicado a las 3 formas existentes.

4. **Cambio de contrato de `calculate_dcf_fair_value`** — PARCIALMENTE CONFIRMADO, ver hallazgo BLOQUEANTE arriba. El valor final (`valor_por_accion`) sí queda protegido por el criterio de regresión ya escrito; lo que falta es la instrucción explícita de que los 4 caminos de exclusión temprana también deben construir un `DCFBreakdown` completo (todos los campos en `None`, nunca un `None` desnudo).

5. **Rate limiter compartido y manejo de errores sin traceback crudo** — CONFIRMADO para el mecanismo general: `_dispatch_leaf` actual (línea 848-919) ya separa claramente la rama determinística (sin balde, sin "🤔 Pensando…", líneas 875-880) de la rama Ollama (`rate_limiter.allow(str(chat_id))`, línea 883), y la spec generaliza este mismo patrón a "Ver dato" (sin balde) vs. "Explicame paso a paso" (con balde, mismo criterio). El manejo global de errores (`bot.py` línea 39-44, `application.add_error_handler(_on_error)`) confirma que ningún traceback crudo llega al chat — solo se loguea. Ver mejora recomendada (b) abajo sobre un punto específico no cubierto por ningún `try/except` local.

### Mejoras recomendadas — no bloqueantes

**(a) `dcf_wacc`/`dcf_g_fcf`/`dcf_fcf_base` deben asignarse junto con el resto del desglose DCF, no apenas se calculan como variables locales.** En el código real (`valuation.py` líneas 442-451), `g_fcf` y `wacc` se calculan como locales ANTES de saber si el modelo completo termina siendo calculable (pueden existir con valor numérico válido aunque el DCF termine excluido por otro motivo más adelante). El criterio de aceptación ya existente ("`dcf_wacc`/`dcf_g_fcf`/... son `None` en cualquier escenario donde `valor_justo_dcf` también es `None`") es correcto y lo detectaría en `qa`, pero la Decisión de diseño #7 se beneficiaría de decirlo explícito en el código de ejemplo: asignar estos 3 campos a `ValuationResult`/`ScenarioValuationResult` SOLO dentro del mismo bloque `else` donde se llama a `calculate_dcf_fair_value` y solo si `dcf.valor_por_accion is not None` (gateo único, no 3 asignaciones sueltas) — reduce el riesgo de que `implementer` lo interprete distinto y `qa` tenga que devolverlo como corrección menor.

**(b) `_build_cuenta_line` no queda cubierto por el mismo `try/except` que protege `_fetch_explanation`.** En el código real (`ai_explain.py` líneas 900-913), solo la llamada a `_fetch_explanation` está envuelta en `try: ... except _ExplainUnavailable:`. Según la Decisión de diseño #4, `cuenta = _build_cuenta_line(...)` se ejecuta ANTES de esa llamada, para poder inyectar `"cuenta"` en `datos_del_contexto`. Un bug de programación en el dispatch nuevo de ~22 casos (p. ej. un `KeyError` por un campo mal nombrado en un caso puntual) no está cubierto por ningún `except` local — sube hasta el `_on_error` global (`bot.py` línea 39-44), que solo loguea y no responde nada al chat. No es una fuga de traceback (el `_on_error` ya lo evita), pero si ocurre DESPUÉS de mandar "🤔 Pensando…" dentro del mismo flujo, ese mensaje queda pegado sin actualizarse nunca. Recomendación: envolver también la construcción de `cuenta` en el mismo bloque `try/except _ExplainUnavailable` (o uno equivalente) para que cualquier fallo de programación en `_build_cuenta_line` caiga al mismo `EXPLAIN_UNAVAILABLE_MSG` en vez de silencio — dado que esta spec agrega ~22 casos nuevos de dispatch, la superficie donde un bug puntual puede ocurrir crece proporcionalmente.

**(c) Truncar la "cuenta" al llegar a `_MAX_CUENTA_CHARS=400` es más riesgoso que truncar la prosa de Ollama.** La Decisión de diseño #8 propone reusar el criterio de corte de `_enforce_brevity` (corte en el último punto/coma, o corte duro + "…") si `_build_cuenta_line` llegara a producir más de 400 caracteres. A diferencia de la prosa de Ollama (donde cortar a mitad de oración es tolerable), la "cuenta" es aritmética — cortarla a mitad de un número (p. ej. `"...= $182.4"` cortado a `"...= $18…"`) mostraría un resultado numérico incompleto y potencialmente engañoso, exactamente lo que esta spec entera busca evitar. Dado que el propio análisis de la spec estima 2.5x de margen sobre el caso más largo conocido, la probabilidad es baja — pero recomendado: si `_build_cuenta_line` alguna vez produce más de 400 caracteres, tratarlo como el mismo caso "no calculable" (omitir el bloque completo y loguear un WARNING) en vez de truncar un número a la mitad — consistente con el principio ya establecido en el resto de la spec de "nunca mostrar un número parcial o inconsistente".

---

## Criterios QA para Spec: "Explicame paso a paso" [Iter-1]

**Rol:** `qa` — criterios de testabilidad y cobertura sobre la spec ya revisada por `architect` y `security` (1 hallazgo bloqueante con remediación exacta ya escrita, convertido abajo en criterio de aceptación con test dedicado, más 3 mejoras recomendadas no bloqueantes). No se reescribe nada de `architect` ni de `security` — se complementa con los ángulos que faltaban, tal como pide el contrato de esta skill.

### Tipo de prueba principal
**Unit testing**, ~95% de la superficie de esta spec. Toda la lógica nueva es pura y aislable: funciones puras (`_build_cuenta_line`, `category_of`, `calculate_dcf_fair_value`, los getters nuevos de `advanced_scoring.py`) o dispatch determinístico con dependencias ya mockeables hoy (Ollama vía `_CountingClient`, Telegram vía los objetos `send_message`/`edit_message_text` capturables — ambos patrones ya existen en `tests/test_ai_explain.py`). No hay BD, colas ni containers nuevos, así que no aplica "integration testing" como categoría separada — los 2 call sites de `calculate_dcf_fair_value` (`compute_valuation`/`compute_valuation_scenarios`) se cubren igual con unit tests parametrizados, porque ambas funciones ya son puras sobre sus parámetros (confirmado leyendo el código real). E2E queda fuera de scope — ver "Fuera de alcance" al final, mismo criterio ya usado en las 2 specs anteriores de este proyecto.

### Cobertura mínima requerida
- [ ] Code coverage ≥ 90% líneas en los 5 archivos modificados (`ai_explain.py`, `ai_explain_content.py`, `advanced_scoring.py`, `valuation.py`, `risk_fit.py`) — por encima del baseline de 80% porque es lógica financiera mostrada directamente al usuario.
- [ ] Branch coverage = 100% en los 4 caminos de retorno temprano de `calculate_dcf_fair_value` (crítico — hallazgo bloqueante de `security`, criterio dedicado más abajo) y en las ramas "no calculable → bloque ausente" de `_build_cuenta_line` para Altman, Z'' y Magic Formula.
- [ ] Todos los criterios de aceptación ya escritos por `architect` (sección "Criterios de aceptación") cubiertos por al menos un test — confirmado sección por sección en "Cobertura confirmada" más abajo.
- [ ] Las 3 mejoras recomendadas no bloqueantes de `security` quedan con seguimiento explícito: (a) y (c) son observables por test unitario y se agregan abajo; (b) depende de una decisión de `implementer` (envolver o no `_build_cuenta_line` en el mismo `try/except` que `_fetch_explanation`) — si lo hace, 1 test de regresión confirma que un `KeyError` simulado en `_build_cuenta_line` cae a `EXPLAIN_UNAVAILABLE_MSG` en vez de subir sin capturar; si no, queda anotado en el backlog QA al final de esta sección, no se inventa un criterio bloqueante que `security` no pidió como tal.

### Casos obligatorios
- [ ] **Happy path**: pregunta `dato_y_paso_a_paso` con ticker donde todos los modelos son calculables — "Ver dato" responde sin Ollama y con teclado adjunto; "Explicame paso a paso" responde con la cuenta resuelta término por término, la explicación de Ollama, y el mismo teclado.
- [ ] **Caso límite**: ticker donde CADA uno de los 4 modelos "todo o nada" (Altman, Z'', Piotroski, Magic Formula) es individualmente no calculable — bloque "🧮 Cuenta" ausente en los 4, sin "None"/"null" visible en ningún mensaje.
- [ ] **Caso límite**: DCF con `wacc == terminal_growth` exactamente en el borde — debe excluirse (el criterio real es `<=`, no `<`; un test que solo prueba `wacc < terminal_growth` no lo distingue).
- [ ] **Caso de error**: los 4 caminos de exclusión de `calculate_dcf_fair_value` — ver criterio dedicado abajo (conversión directa del hallazgo bloqueante).
- [ ] **Caso de alto riesgo de negocio**: barrido general — ningún bloque "🧮 Cuenta" ni "📌 Dato" generado por TODA la suite de tests de leafs (no solo los casos "no calculable" explícitos) contiene el literal `None`/`null` como si fuera un valor calculado — 1 test que corre un `assert not re.search(r"\bNone\b|\bnull\b", mensaje)` sobre cada mensaje producido en la suite parametrizada de las 27 preguntas, como red de seguridad adicional a los tests puntuales.

### Testabilidad
- [ ] `_build_cuenta_line` es función pura (`dict` → `Optional[str]`), sin I/O — ya exigido por `architect` (criterio existente), QA lo hereda sin cambios.
- [ ] `calculate_dcf_fair_value` sigue siendo función pura tras el cambio de contrato — mismos inputs, mismo determinismo, sin mocks necesarios para ejercitar los 4 caminos de exclusión.
- [ ] Los 2 call sites (`compute_valuation`/`compute_valuation_scenarios`) permiten forzar cada camino de exclusión de DCF variando solo parámetros de entrada — confirmado por inspección de la firma real, no requiere ningún refactor de testabilidad.
- [ ] El envío de Telegram ya es mockeable vía el objeto de aplicación inyectado en los tests existentes (mismo patrón que `_CountingClient`) — extensible sin cambios de arquitectura para capturar `reply_markup` en las 3 ramas de `_dispatch_leaf`.

### Criterio de exit de QA
- Suite completa (`pytest -q`) en verde — no solo los tests nuevos, toda la suite existente actualizada al nuevo contrato.
- Sin tests ignorados, comentados ni marcados `xfail`/`skip` para maquillar CI.
- Flaky rate = 0 en la suite nueva — nada de la superficie nueva depende de tiempo real, red real ni orden de ejecución (todo mockeado o puro por diseño).
- `DCFBreakdown`/`_DCF_NO_CALCULABLE` verificados con `==` de dataclass, nunca con comparación campo por campo escrita a mano — evita que un test quede verde por accidente si se agrega un campo nuevo al dataclass y alguien olvida poblarlo en algún camino.

---

### Criterio de aceptación nuevo — hallazgo BLOQUEANTE de `security` convertido en test

Agregar a la sección "Criterios de aceptación" → "Campos nuevos — sin cálculo nuevo, solo exposición" (no reemplaza nada ahí, se suma):

- [ ] `test_dcf_breakdown_fcf_historial_insuficiente_devuelve_campos_none` — `calculate_dcf_fair_value` con `fcf_historial` de longitud insuficiente devuelve una instancia de `DCFBreakdown` con los 5 campos en `None` (comparar por `==` contra `_DCF_NO_CALCULABLE`), nunca `None` desnudo.
- [ ] `test_dcf_breakdown_shares_outstanding_invalido_devuelve_campos_none` — mismo assert con `shares_outstanding=None` y con `shares_outstanding<=0` (2 sub-casos).
- [ ] `test_dcf_breakdown_wacc_no_supera_terminal_growth_devuelve_campos_none` — mismo assert con `wacc=None` y con `wacc <= terminal_growth` (incluye el caso borde `wacc == terminal_growth`, 2 sub-casos).
- [ ] `test_dcf_breakdown_cagr_no_calculable_devuelve_campos_none` — mismo assert con un `fcf_historial` que hace que `calculate_cagr` devuelva `None` (sin `g_fcf_override`).
- [ ] `test_compute_valuation_dcf_no_calculable_sin_attribute_error` — `compute_valuation` con `shares_outstanding` inválido (el único de los 4 caminos NO pre-filtrado ya por el caller antes de invocar `calculate_dcf_fair_value`, confirmado leyendo `valuation.py`) completa sin excepción, `result.valor_justo_dcf is None`, y `ModeloExcluido("dcf", "dcf_no_calculable")` aparece en `result.modelos_excluidos` — mismo assert que ya usa el test de regresión existente `test_valuation_dcf_excluido_por_fcf_base_negativo` (línea 327 de `tests/test_valuation.py`), extendido al nuevo contrato `DCFBreakdown`.
- [ ] `test_compute_valuation_scenarios_dcf_no_calculable_sin_attribute_error` — mismo test contra `compute_valuation_scenarios`, ejercitando el segundo call site para los 3 escenarios (pesimista/conservador/optimista) — el punto exacto que `security` marca como el que revienta con `AttributeError` si el fix no se aplica.

**Nota de testabilidad**: los 4 caminos se alcanzan con inputs directos a `calculate_dcf_fair_value` (función pura, sin mocks). Los 2 tests de call sites tampoco requieren mocks — `compute_valuation`/`compute_valuation_scenarios` son puras sobre sus parámetros.

---

### Cobertura confirmada

**Los 3 modelos priorizados por Daniela (Altman, Piotroski, Magic Formula), verificados término por término — no solo el resultado final:**
- [ ] `test_cuenta_alz_verificada_termino_a_termino` — reutilizando como datos conocidos el fixture ya existente `test_calculate_altman_z_caso_completo_zona_segura` (`tests/test_advanced_scoring.py`, línea 73), `_build_cuenta_line("avanzado", "alz", ...)` produce el string exacto con cada coeficiente, cada valor A-E y cada término ya multiplicado antes de la suma — assert de igualdad de string completo, no `in`/substring.
- [ ] `test_cuenta_pir_verificada_termino_a_termino` (y análogo para `pia`/`pie`) — cada criterio del grupo aparece con su magnitud real (`valores[criterio]`) y el operador resuelto, no solo el `bool` de `cumplido` — assert de igualdad de string completo por grupo.
- [ ] `test_cuenta_mgr_verificada_termino_a_termino` — `ROIC = ebit / capital_invertido` con ambos sustituidos y el resultado en decimal y porcentaje, como pide el ejemplo de la Decisión de diseño #3.
- [ ] `test_cuenta_mge_verificada_termino_a_termino` — `EY = ebit / ev` mostrando el armado de `ev` (`market_cap + total_debt − cash`) explícito, no solo el `ev` ya sumado — la spec lo exige textualmente (línea 104).
- [ ] Los 16 modelos restantes con cuenta completa quedan cubiertos por al menos 1 test cada uno, mismo criterio (string completo contra el valor esperado calculado a mano) — la tabla de la Decisión de diseño #3 ya documenta la fórmula exacta por pregunta y sirve de oráculo de test, no hace falta repetirla acá.

**DCF parcial:**
- [ ] `test_cuenta_dcf_wacc_y_g_sustituidos_proyeccion_resumida` — el bloque "🧮 Cuenta" de `dcf` incluye `dcf_wacc`/`dcf_g_fcf` reales y el resultado agregado del año 5, pero NO lista los 5 años individualmente (assert negativo explícito) — confirma la Decisión de diseño #7 como comportamiento esperado, no un bug a corregir en QA momento 2.
- [ ] `test_cuenta_dcf_usa_dcfbreakdown_no_recalcula` — `_build_cuenta_line("texto_libre", "dcf", ...)` no vuelve a llamar `calculate_dcf_fair_value` (spy con 0 llamadas) — confirma que lee únicamente del payload ya armado, el caso más tentador de recalcular por ser el único parcial.

**3 preguntas narrativas sin cambio (`mod`/`ben`/`ren`):**
- [ ] Ya exigido por `architect` como "comportamiento byte-idéntico... test de regresión snapshot". QA precisa el alcance de "byte-idéntico": mismo `SYSTEM_PROMPT_EXPLAIN`, mismo payload (sin clave `"cuenta"` — estas 3 preguntas nunca la generan), mismo texto de salida para el mismo mock de Ollama, y el ÚNICO diff observable entre el `call_args` de antes y después de esta spec es la presencia de `reply_markup` no-`None`.

**Menú de navegación reaparece tras CUALQUIER respuesta, en el nivel correcto:**
- [ ] `test_category_of_exhaustivo_27_preguntas` — ya pedido por `architect`. Es la fuente de verdad única para "qué nivel le corresponde a cada pregunta" (Nivel 2 de su categoría si `category_of` devuelve algo, Nivel 1 si devuelve `None`) — combinado con el criterio de regresión negativo ya existente de `architect` ("ningún envío de `_dispatch_leaf` queda sin `reply_markup`"), cubre la matriz completa de 27 preguntas × camino(s) sin necesitar un test dedicado por celda.
- [ ] Caso explícito a no dar por sentado: para las preguntas sueltas (`ver`, `inf`, `mod`, `ben`, `ren`) el teclado de Nivel 1 que se adjunta tiene que ser el de su `kind` correcto (`texto_libre` para `ver`/`inf`/`ren`, `avanzado` para `mod`/`ben`) — test que confirma que no se cruza el teclado de un `kind` con leafs del otro.

---

### Fixtures / mocks mínimos que faltan

- **Fixture de test para `_DCF_NO_CALCULABLE`**: importar la misma instancia de producción en `test_valuation.py` para comparar por `==`, en vez de reconstruir el dataclass a mano en cada test — evita desincronización si `DCFBreakdown` gana un campo futuro.
- **Fixture "ticker con DCF no calculable pero todo lo demás calculable"**: no existe hoy con ese alcance (los fixtures actuales de `compute_valuation` prueban DCF excluido en aislamiento del cálculo, no el mensaje de Telegram completo). Extender `_texto_libre_context`/`_avanzado_context` (`tests/test_ai_explain.py`, líneas 79/146) con una variante nombrada (ej. `_texto_libre_context_dcf_no_calculable`) que fije `scenarios[esc]["valor_justo_dcf"] = None`.
- **Fixture "los 4 modelos todo-o-nada simultáneamente no calculables"**: extensión de `_avanzado_context` con `altman`/`altman_pp`/`magic` en `disponible=False` y `piotroski` con `criterios_evaluables=0` — usada por el test de barrido de ausencia de `None` visible.
- **Mock de Ollama que cita un número presente SOLO en `"cuenta"`**: extensión de `_CountingClient` (línea 35) con una respuesta fija que mencione textualmente el resultado final de la cuenta — necesario para el criterio ya escrito por `architect` sobre que ese número es aceptado por `_no_new_protected_tokens` (línea 263).
- **Mock de Ollama que alucina un número ausente de `datos_del_contexto` Y de `cuenta`**: variante del anterior, parametrizable por las 22 preguntas `dato_y_paso_a_paso` — 1 fixture reusado, no 22 fixtures distintos.
- **Spy sobre el envío de Telegram que captura `reply_markup` de las 3 ramas de `_dispatch_leaf` en una sola corrida**: envolviendo el objeto ya usado (`_CountingClient` o equivalente) con una lista que acumule cada `call_args` completo — evita instrumentar cada test de leaf por separado para el test de regresión negativo.
- **Fixture de "cuenta simulada que excede 400 caracteres"**: no existe ninguna cuenta real que lo haga por diseño (Decisión de diseño #8) — se necesita `_build_cuenta_line` monkeypatcheado o un caso sintético (montos de 15+ dígitos sin abreviar) para ejercitar `_MAX_CUENTA_CHARS` de verdad, no solo confirmarlo por inspección.

---

### Fuera de alcance — qué NO se prueba y por qué

Mismo criterio ya aplicado en las 2 specs anteriores de este proyecto, confirmado sin cambios para esta spec:

- **E2E contra Ollama real**: ningún test levanta un servidor Ollama real ni depende de la respuesta real de `qwen2.5:3b-instruct` — todo mockeado, porque la salida de un LLM real no es determinística y rompería la suite en CI. La verificación del comportamiento real del prompt en producción queda para verificación manual de Daniela en el bot real.
- **E2E contra FMP real**: cero llamadas HTTP nuevas en esta spec — las ya existentes siguen mockeadas con los fixtures ya usados por `test_query_handler.py`/`test_advanced_command.py`, sin agregar ningún test de integración contra la API real.
- **Pruebas de carga/performance**: no aplica — esta spec no cambia el mecanismo del rate limiter ni agrega ninguna ruta de alto volumen; "Ver dato" es estrictamente MÁS barato (sin Ollama) que el comportamiento actual, nunca más caro. Sin presupuesto nuevo de Apdex/P95 que verificar.
- **Las 4 tablas de texto fijo (`FORMULAS_*`/`FUENTES_*`)**: contenido idéntico, sin cambios de esta spec — no se re-testean, ya cubiertas por la suite existente y cerradas en la spec anterior.
- **El desglose año a año completo del DCF (5 términos)**: explícitamente fuera de scope de esta iteración (D1/Decisión de diseño #7) — sin criterio de aceptación que lo pida, por lo tanto sin test; backlog QA condicionado a que Daniela lo pida en una iteración futura.
- **Fuzzing adversarial del regex `_CALLBACK_PASO_A_PASO_RE`** más allá de los casos ya descritos (inyección, longitud extrema, unicode): cubierto en general por el hallazgo ya remediado de `security` en la spec cerrada anterior (ownership) y por el criterio de mutua exclusión de esta spec — no se agrega fuzzing dedicado porque ningún regex existente del proyecto lo tiene; sería inconsistente introducirlo solo acá.

### Backlog QA (siguiente run)
- Mejora (b) de `security` (envolver `_build_cuenta_line` en el mismo `try/except` que `_fetch_explanation`): si `implementer` no la aplica en esta iteración, queda como test pendiente de agregar cuando se decida — no bloquea el paso a `implementer` porque `security` la marcó explícitamente como no bloqueante.
- Verificación en el bot real de WhatsApp/Telegram del mensaje completo de "paso a paso" (longitud percibida, legibilidad de la cuenta en el cliente móvil) — exploratorio, no automatizable, mismo criterio que otras verificaciones manuales ya registradas en memoria del proyecto.

---
