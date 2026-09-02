# Spec: Menú de explicaciones por capas + fórmula/fuente determinística en ambos flujos [Iter-1]

**Rol:** `architect` (spec base — rediseño de una feature ya implementada y en producción, `SDD_explicaciones_interactivas_ollama.md`, cerrada, más los 2 fixes de producción del 2026-09-02 ya en el working tree).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` — esta spec (a) amplía sustancialmente la superficie de datos que viaja hacia Ollama por consulta (de ~7 sub-dicts posibles a ~27), (b) cambia el `callback_data` de un solo patrón (`xp:{id}:{code}`) a 3 patrones (`xp:{id}:m`, `xp:{id}:c:{cat}`, `xp:{id}:{code}`) que hay que validar sin excepción, y (c) reduce el mensaje base de ambos flujos, moviendo datos que hoy son "gratis" (sin tap) a "requieren tap" — impacto de UX/producto más que de seguridad, pero se señala en el Handoff igual. No aplica `dba` (sin persistencia nueva, `ExplanationContextStore` sigue siendo 100% en memoria). `frontend` no aplica (sin UI web). `backend` queda a discreción de `security`, igual que en las 2 specs anteriores de este proyecto.
**Estado:** spec nueva, sin iteraciones previas. No hay scope freeze — `implementer` no toca código hasta que `security` y `qa` agreguen sus criterios.

---

## Contexto

Daniela probó la feature de botones ya en producción (`SDD_explicaciones_interactivas_ollama.md`, cerrada) y dio este feedback textual:

> "la forma en la que responde no es tan buena... la idea es que seas como profesor, no me traigas como todo un chorrero la primera vez, sino que veme preguntando actúa como profesor, y que hayan muchos botones y más fácil más llamadas por ollama por botón seleccionado"

Aclaró después: quiere botones **"por capas"** (conversación guiada, un botón abre más botones) en vez de todos los botones de un solo nivel — y agregó un pedido nuevo: **cada explicación tiene que mostrar la fórmula exacta que se usó y de qué campo/dato sale el valor**.

**Alcance confirmado por Daniela (no reabrir):**
1. Aplica a AMBOS flujos (texto libre y `/avanzado`) — el primer mensaje de cualquiera de los 2 comandos queda corto, con el detalle detrás de botones.
2. Menú por capas: después del mensaje corto, unos pocos botones generales por categoría temática, cada uno abre sub-botones más específicos. La agrupación temática concreta y si `/avanzado` se agrupa o no queda a criterio del `architect` (ver Decisiones de diseño #2 y #3).

Esta spec construye sobre el código real, no sobre lo documentado en la spec cerrada (que quedó desactualizada por los 2 fixes de producción del 2026-09-02 — falso positivo del guard con números formateados distinto, y nombre del modelo financiero garantizado como dato):

- **`src/investbot/ai_explain.py`** (código real leído completo): `ExplanationContext` (dataclass frozen, líneas 88-108), `ExplanationContextStore` (TTL + tope de tamaño, líneas 117-178), `QUESTIONS_TEXTO_LIBRE`/`QUESTIONS_AVANZADO` (líneas 184-236, 3 y 5 preguntas planas respectivamente), `build_keyboard` (líneas 241-264, un solo nivel — 1 fila para texto libre, 3 filas para `/avanzado`), `_CALLBACK_RE = r"^xp:([0-9a-f]{8}):([a-z]{2,4})$"` (línea 269, un solo patrón), `_build_explain_payload` (líneas 352-422, sub-dict por `question_code` con `modelo`/`total_*` como dato garantizado — Bug 2 de producción), `_normalize_numeric_token`/`_no_new_protected_tokens` (líneas 470-510, Bug 1 de producción — normalización de formato antes de comparar), `build_explain_handler` (líneas 597-694, un solo `CallbackQueryHandler` para el prefijo `xp:`, mensaje "🤔 Pensando…" antes de cada llamada a Ollama, editado in-place con la respuesta o el fallback).
- **`src/investbot/query_handler.py`**: `fetch_and_analyze_parts` (línea 158) puebla `explain_context_sink` (líneas 555-568) con SOLO 5 campos (`company_name`/`escenario_elegido`/`precio_actual`/`scenarios`/`pillars`/`veredicto_barata`) de los muchos dicts que YA calcula localmente (`ratios_dict`, `risk_fit_dict`, `momentum_dict`, `peer_comparison_dict`, `extras_dict`, `vix_dict`, `corporate_events_list`, `treasury_source`, `balance_fuente`, `income_statements_fuente`, `cash_flow_fuente` — líneas 498-553) — estos 11 dicts/valores existen, se calculan, y se descartan apenas se llama a `summary.build_summary_parts(...)` (línea 570). `_run_analysis` (línea 946) siempre llama a `summary.build_summary_parts` (el reporte completo, sin acortar) y arma el `ExplanationContext` solo con esos 5 campos (líneas 1013-1025).
- **`src/investbot/advanced_command.py`**: `_build_message` (línea 125) arma el mensaje completo (Altman, Piotroski, Beneish, Magic Formula, Factores, fuente de datos) y puebla `explain_context_sink` (líneas 274-292) con `company_name`/`sector`/`industry`/`asset_light`/`altman`/`altman_pp`/`piotroski`/`beneish`/`magic`/`factors` — pero **no** con `roe`/`gross_margin`/`beta`, que se calculan localmente (líneas 155, 201-206) y se usan para `factors` pero se descartan después.
- **`src/investbot/summary.py`**: `build_summary_parts` (línea 767) arma 11 secciones (Título, Veredicto, Intro "Tienda de Limonada", Ratios, Extras, Valor Justo con 3 escenarios completos, Pilares, Contexto de mercado, Eventos corporativos, Encaje de riesgo, Notas de transparencia) — TODAS siempre visibles hoy, sin ningún botón. `_MODELO_FORMULAS` (línea 93) ya tiene, como strings fijos, las fórmulas de Múltiplos/Graham/DCF usadas hoy dentro del cuerpo del mensaje — reutilizables literalmente para esta spec (Decisión de diseño #5). Ratios/extras también ya traen su fórmula inline como string fijo (líneas 823-846, 528-575) — mismo criterio de reutilización.
- **`src/investbot/valuation.py`** / **`src/investbot/advanced_scoring.py`** / **`src/investbot/rules.py`**: fórmulas reales confirmadas por lectura directa del código (no de la spec original, que puede haber quedado desactualizada) — ver la tabla completa en Decisión de diseño #6/#7.

---

## Estado objetivo

1. El primer mensaje de **ambos flujos**, cuando Ollama está habilitado, queda corto: título + una síntesis de 2-4 líneas + botones — nunca el reporte de 11 secciones/500+ líneas de hoy.
2. Los botones se organizan en **2 niveles**: Nivel 1 = categorías temáticas (pocas, agrupan por tema) + un puñado de botones sueltos que no necesitan categoría (Veredicto, ¿Qué modelos aplican?, Fuentes y transparencia). Nivel 2 = preguntas puntuales dentro de cada categoría — el mismo mecanismo de hoy (una llamada a Ollama por botón), ahora con más preguntas posibles por análisis (13 en texto libre, 14 en `/avanzado`, contra 3 y 5 de hoy).
3. Cada explicación (Nivel 1 sueltos o Nivel 2 dentro de una categoría) sigue siendo corta (2-4 oraciones de Ollama) pero ahora **siempre** va acompañada de: el dato concreto ya calculado (determinístico), la fórmula exacta del modelo (determinística, nunca redactada por Ollama — Decisión de diseño #5), y de qué campo de FMP/cálculo propio sale cada valor (determinística).
4. Hay un botón **"🔙 Menú"** en toda submenú/explicación que vuelve a mostrar las categorías de Nivel 1 sin necesidad de scrollear — nunca edita ni destruye una explicación ya entregada (Decisión de diseño #1).
5. **Si Ollama está deshabilitado, el comportamiento es idéntico al de hoy** (reporte completo, sin acortar, sin botones) — el acortamiento del mensaje solo aplica cuando hay manera de recuperar el detalle con un botón (Decisión de diseño #4, regla de no-regresión).
6. Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR — como en la spec anterior, todo el contenido nuevo sale de dicts que cada flujo YA calcula y hoy descarta.
7. El guard anti-invención (`_no_new_protected_tokens`), el rate limiter compartido, el mensaje "🤔 Pensando…", y D1 (Beneish siempre no calculable) se mantienen sin aflojar — se extienden a más botones, con el mismo criterio.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch)*

### 1. Navegación en 2 niveles — 3 formas de `callback_data`, "🔙 Menú" siempre manda mensaje nuevo

**Por qué 2 niveles y no más:** el pedido de Daniela es literal — "un botón abre más botones", singular. Un tercer nivel (ej. sub-sub-categorías dentro de Piotroski) agregaría taps sin agregar información nueva, dado que cada pregunta de Nivel 2 ya es una explicación autocontenida y corta. YAGNI — si en el futuro hace falta más profundidad, es aditivo.

**Por qué cada acción manda un mensaje NUEVO, nunca edita uno anterior (salvo el propio "🤔 Pensando…" de cada leaf, que ya funcionaba así):** la spec anterior (Decisión de diseño #7, paso 5, no reabierta) ya estableció que las explicaciones se acumulan como mensajes nuevos para que el usuario pueda volver a leerlas todas sin perder ninguna. Si "🔙 Menú" editara la explicación o el submenú ya mostrado, esa explicación desaparecería del historial visible (Telegram no expone el contenido pre-edición de forma cómoda) — inconsistente con ese principio ya cerrado. La única edición que sigue existiendo es la ya aprobada: el placeholder "🤔 Pensando…" editándose con la respuesta final de ESE mismo botón.

**3 formas de `callback_data`** (regex ampliado, mismo prefijo `xp:`, mismo tope de 64 bytes de Telegram):

| Forma | Regex | Acción | Ejemplo | Bytes |
|---|---|---|---|---|
| Menú (Nivel 1) | `^xp:([0-9a-f]{8}):m$` | Manda mensaje nuevo con los botones de Nivel 1 del `kind` del contexto | `xp:a1b2c3d4:m` | 13 |
| Categoría (Nivel 1 → Nivel 2) | `^xp:([0-9a-f]{8}):c:([a-z]{2,4})$` | Manda mensaje nuevo con los botones de Nivel 2 de esa categoría + "🔙 Menú" | `xp:a1b2c3d4:c:val` | 18 |
| Pregunta puntual (leaf) | `^xp:([0-9a-f]{8}):([a-z]{2,4})$` | Mismo mecanismo de hoy — determinístico o vía Ollama según el código (tabla de metadata nueva, ver abajo) | `xp:a1b2c3d4:gra` | 16 |

Los 3 regex son mutuamente excluyentes por construcción (la forma "categoría" tiene el literal `c:` que ningún `question_code` de 2-4 letras puede producir, porque el regex de `question_code` no admite `:`) — un solo `_CALLBACK_RE` con 3 grupos alternativos (`re.compile(r"^xp:([0-9a-f]{8}):(m|c:[a-z]{2,4}|[a-z]{2,4})$")`) o 3 regex separados evaluados en orden, a discreción de `implementer` — cualquiera de las 2 formas es válida, lo que no cambia es el comportamiento: `callback_data` que no matchea ninguna de las 3 formas sigue el mismo camino que hoy (`EXPLAIN_INVALID_MSG`, `sanitize_for_log`, sin excepción).

**Metadata nueva por `question_code`** — cada leaf necesita declarar si dispara una llamada a Ollama o es contenido 100% determinístico (fórmulas/eventos corporativos ya son texto fijo, no necesitan que un modelo los redacte):

```python
@dataclass(frozen=True)
class QuestionSpec:
    label: str            # texto del botón
    pregunta_fija: str     # solo se usa si requires_ollama=True
    requires_ollama: bool  # False para "evt"/"inf" — ver Decisión #4
```

### 2. Agrupación temática — flujo de texto libre

Propuesta (criterio: separar "cuánto vale" de "qué tan buena es como empresa" de "qué está pasando en el mercado ahora" — 3 preguntas mentales distintas que un inversionista se hace, ninguna es subconjunto de otra):

| Nivel 1 | Tipo | Nivel 2 (`question_code`) | Qué explica |
|---|---|---|---|
| ⚖️ Veredicto | leaf suelto | `ver` | Por qué el veredicto (barata/cara/sin datos) salió así — sin cambios respecto a hoy |
| 💰 Valoración | categoría `val` | `vf` Valor Justo Total | Rango + clasificación barata/cara en los 3 escenarios (síntesis, ya existía como `vf`) |
| | | `gra` Graham (EPS) | Solo el resultado del modelo Graham en los 3 escenarios |
| | | `dcf` DCF | Solo el resultado del modelo DCF en los 3 escenarios |
| | | `mul` Múltiplos | Solo el resultado del modelo Múltiplos en los 3 escenarios |
| | | `rat` Ratios clave | Liquidez, margen bruto, PER, P/S |
| 🏛 Calidad del negocio | categoría `cal` | `pil` Los 4 pilares | Igual que hoy (`pil`) |
| | | `ren` ROE y rentabilidad | ROE, Deuda/Patrimonio, Deuda Neta/EBITDA, Dividend Yield, Payout Ratio (hoy siempre visible sin botón — Extras) |
| 📊 Riesgo y mercado | categoría `rie` | `rsk` Encaje con tu perfil | Beta + encaje con el perfil de riesgo guardado (hoy siempre visible sin botón) |
| | | `mom` Momentum y volatilidad | % vs. máx/mín 52 semanas, promedios móviles, VIX (si disponible) |
| | | `cmp` Comparables del sector | PER propio vs. rango de peers |
| | | `evt` Eventos corporativos | Solo si `corporate_events` no está vacío — **determinístico, no Ollama** (ver Decisión #4) |
| ℹ️ Fuentes y transparencia | leaf suelto | `inf` | Fuente de cada dato, metodología de WACC, disclaimer — **determinístico, no Ollama** |

13 `question_code` nuevos/existentes contra 3 de hoy.

**Por qué "Veredicto" y "Fuentes y transparencia" quedan sueltos, no dentro de una categoría:** Veredicto sintetiza los 3 pilares + valoración + riesgo en una frase — meterlo dentro de "Valoración" lo subordinaría a un tema que no lo contiene completamente. "Fuentes y transparencia" es meta-información sobre TODO el análisis (de dónde salió cada dato, qué fuente respondió esta consulta), no un tema financiero — no encaja en ninguna de las 3 categorías sin forzarlo.

### 3. Agrupación temática — `/avanzado`

Daniela preguntó explícitamente si los 5 modelos ya son categorías naturales de primer nivel. **Sí, con una salvedad:** 2 de los 5 modelos (Piotroski, con 9 criterios independientes, y AQR, con 4 factores) tienen sub-estructura genuina que vale la pena exponer por separado — los otros 3 (Altman, Beneish, Magic Formula) son más simples y solo Altman tiene una variante condicional (Z'').

| Nivel 1 | Tipo | Nivel 2 (`question_code`) | Qué explica |
|---|---|---|---|
| ❓ ¿Qué modelos aplican? | leaf suelto | `mod` | Igual que hoy — síntesis de los 5 |
| 📐 Altman Z | categoría `alt` | `alz` Z (fórmula original) | Solo Z |
| | | `azp` Z'' (asset-light) | Solo si `altman_pp is not None` (ticker asset-light) |
| 🧮 Piotroski F | categoría `pio` | `pig` Puntaje general | F-Score total (igual que el `pio` de hoy) |
| | | `pir` Rentabilidad | 4 criterios: ROA positivo, CFO positivo, ROA creciente, CFO > utilidad |
| | | `pia` Apalancamiento y liquidez | 3 criterios: apalancamiento decreciente, liquidez creciente, sin dilución |
| | | `pie` Eficiencia | 2 criterios: margen bruto creciente, rotación de activos creciente |
| 🚫 Beneish M | leaf suelto | `ben` | Igual que hoy — siempre "no calculable" (D1, no reabrir) |
| 🪄 Magic Formula | categoría `mag` | `mgr` ROIC | Solo ROIC |
| | | `mge` Earnings Yield | Solo Earnings Yield |
| 📊 Factores AQR | categoría `aqr` | `aqv` Value | Earnings Yield vs. umbrales |
| | | `aqq` Quality | ROE + margen bruto + ratio Piotroski vs. umbrales |
| | | `aqm` Momentum | Etiqueta de momentum reutilizada |
| | | `aql` Low-vol | Beta vs. umbrales |

14 `question_code` nuevos/existentes contra 5 de hoy.

**Por qué Beneish sigue suelto y no como categoría vacía:** D1 (no reabrir) — siempre "no calculable con este plan de FMP", una sola explicación fija, ningún sub-tema que abrir. Convertirlo en categoría con 0-1 sub-botones sería una capa de navegación sin contenido detrás.

**Por qué Altman siempre es categoría (nunca leaf directo) aunque `azp` no aplique:** mantener la ESTRUCTURA fija (Altman = categoría con 1 o 2 botones según el ticker) es más simple de implementar y testear que una estructura condicional (a veces categoría, a veces leaf) — mismo criterio que ya usa el proyecto para omitir el botón de VIX/eventos corporativos cuando no hay dato, en vez de cambiar la forma del mensaje.

### 4. Contenido determinístico sin Ollama — `evt` e `inf`

`evt` (eventos corporativos) e `inf` (fuentes y transparencia) **nunca llaman a Ollama** — son bloques de texto ya construidos hoy por `summary.py` (deterministas, ya aprobados por `security` en specs anteriores: eventos corporativos explícitamente NO se resumen con IA por riesgo de alucinación sobre texto legal, ver docstring de `build_corporate_events_section`). Pedirle a Ollama que los redacte de nuevo agregaría latencia y riesgo sin ningún beneficio.

**Consecuencia en el handler:** al recibir un `question_code` con `requires_ollama=False`, `build_explain_handler` responde INMEDIATO (sin "🤔 Pensando…", sin llamada HTTP, sin pasar por el guard) con el bloque ya armado, prefijado con `"📋 Contenido fijo (sin IA)."` (mismo espíritu que `TRANSPARENCY_FIXED_NO_BUTTONS` de `advanced_command.py`, reutilizado como patrón) en vez de `TRANSPARENCY_USED`.

**Consecuencia en el presupuesto:** estos 2 botones no consumen Ollama ni agregan latencia — abaratan el "muchos botones" pedido por Daniela sin costo adicional real.

### 5. Fórmula + fuente del dato — determinístico, agregado después de la respuesta de Ollama (decisión técnica del `architect`, no de Daniela)

Daniela dejó explícitamente esta elección a criterio técnico. Se elige **determinístico** (texto fijo en Python, agregado después de la respuesta de Ollama, nunca redactado por el modelo) sobre la alternativa de pasarlo como "dato garantizado" en `datos_del_contexto` (mismo patrón que `_MODELO_VF`/Bug 2 de producción) por 4 razones:

1. **Las fórmulas nunca cambian por ticker** — a diferencia del nombre del modelo (que sí varía: "Altman Z-Score" vs. "Altman Z-Score + Z''" según `altman_pp`), la fórmula de Graham es literalmente la misma fórmula para AAPL que para cualquier otro ticker. No hay ninguna razón para que un LLM la "redacte" cada vez — es dato estático, no contenido a generar.
2. **Robustez del guard**: una fórmula como `"Z = 1.2A + 1.4B + 3.3C + 0.6D + 1.0E"` está llena de números sueltos (1.2, 1.4, 3.3, 0.6, 1.0) que `ai_rewrite._PROTECTED_TOKEN_RE` matchearía como tokens protegidos. Pasarla como dato garantizado (como se hizo con el nombre del modelo) técnicamente funcionaría, pero exige que Ollama la transcriba carácter por carácter sin alterar un solo dígito o símbolo (×, /, superíndices) — los modelos chicos vía Ollama ya mostraron, en Bug 1 de producción, que ni siquiera el formato de UN número (`$405.63` vs `405.63`) es 100% confiable. Pedirle que transcriba una fórmula completa sin variación multiplica ese riesgo. Determinístico lo elimina por completo: la fórmula nunca pasa por el modelo, no hay nada que pueda alucinar o desformatear.
3. **Menos superficie para el guard** = menos falsos rechazos. Cada símbolo matemático nuevo en el payload es una oportunidad más de que `_no_new_protected_tokens` rechace una respuesta legítima (como ya pasó 2 veces en producción con casos más simples que una fórmula completa).
4. **Más barato**: cero tokens adicionales generados por Ollama, cero riesgo de que la fórmula "coma" presupuesto de las 2-4 oraciones de brevedad exigidas (Regla 1 del `SYSTEM_PROMPT_EXPLAIN`, no reabierta).

**Mecanismo:** 2 diccionarios nuevos de constantes en `ai_explain.py`, indexados por `question_code`:

```python
FORMULAS: dict[str, str] = { ... }   # texto fijo, ej. "gra": "EPS (TTM) × (8.5 + 2×g) × 4.4 / Y, con g = CAGR histórico de EPS"
FUENTES: dict[str, str] = { ... }    # texto fijo, ej. "gra": "EPS TTM y el historial de EPS vienen del estado de resultados (income statement) — Y (tasa libre de riesgo) viene de FRED/Treasury.gov."
```

`mod`/`ver`/`evt`/`inf`/`pig` (síntesis de varios criterios, no una fórmula puntual) pueden no tener una entrada en `FORMULAS` — en ese caso, no se agrega el bloque de fórmula (solo el de fuente, si aplica, o ninguno para `mod`/`ver`, que son síntesis narrativas, no un único cálculo).

**Formato del mensaje final de cada leaf con Ollama** (extiende el formato actual, que hoy es `TRANSPARENCY_USED + respuesta + DISCLAIMER_NO_ASESORAMIENTO`):

```
🤖 Con ayuda de Ollama

📌 Dato: {dato concreto ya calculado, ej. "Graham (Conservador): $182.40"}

{respuesta de Ollama, 2-4 oraciones}

📐 Fórmula: {FORMULAS[question_code]}
📊 Fuente del dato: {FUENTES[question_code]}

{DISCLAIMER_NO_ASESORAMIENTO}
```

El bloque "📌 Dato" también es determinístico (no depende de Ollama) — saca el/los valor(es) concretos del `ExplanationContext` ya guardado (ej. `scenarios["conservador"]["valor_justo_graham"]`), mismo criterio de "nunca recalculado" del resto del proyecto. Esto es lo que reintroduce, DETRÁS del botón correspondiente, los números exactos que hoy se ven sin tocar nada (Decisión de diseño #8 explica de dónde sale cada uno).

### 6. Tabla completa de fórmula + fuente — flujo de texto libre

Fórmulas confirmadas contra el código real de `valuation.py`/`rules.py`/`summary.py` (no inventadas):

| `question_code` | `FORMULAS[...]` | `FUENTES[...]` |
|---|---|---|
| `vf` | *(sin fórmula única — síntesis; ver `mul`/`gra`/`dcf`)* | *(sin bloque de fórmula — solo el bloque de fuente)* Valor Justo Total = promedio simple de los modelos calculables entre Múltiplos, Graham y DCF (`valuation.py`, `compute_valuation_scenarios`). |
| `gra` | `EPS (TTM) × (8.5 + 2×g) × 4.4 / Y, con g = CAGR histórico de EPS` (reutilizado literal de `summary._MODELO_FORMULAS["graham"]`) | EPS (TTM) viene del estado de resultados (o del cálculo TTM propio del bot sobre 4 trimestres); `g` es el CAGR calculado por el bot sobre el historial de EPS (con techo de 15%, ver `GRAHAM_G_CAP`); Y es la tasa del bono del Tesoro a 10 años, de FRED o Treasury.gov. |
| `dcf` | `proyección de Flujo de Caja Libre a 5 años + valor terminal, descontados al WACC` (reutilizado literal de `summary._MODELO_FORMULAS["dcf"]`) | FCF histórico = Flujo de Caja Operativo − Gasto de Capital (CapEx), del estado de flujo de efectivo; WACC es un cálculo propio del bot con beta (dato de FMP), deuda del balance, y tasa impositiva efectiva del estado de resultados. |
| `mul` | `EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector` (reutilizado literal de `summary._MODELO_FORMULAS["multiplos"]`) | EPS TTM del estado de resultados propio; el PER de cada peer es 1 / earningsYield (earningsYield sí es un dato de FMP, el PER individual es un cálculo del bot). |
| `rat` | Liquidez = Activos Circulantes / Pasivos Circulantes · Margen bruto = (Ventas − Costo de Ventas) / Ventas · PER = Precio / EPS · P/S = Capitalización de Mercado / Ventas (4 fórmulas, reutilizadas literales de `summary.py` líneas 823-846) | Activos/Pasivos Circulantes del balance general; Ventas y Costo de Ventas del estado de resultados; Precio y Capitalización de Mercado de la cotización (quote). |
| `pil` | Ingresos crecientes = ingresos del período más reciente > el más antiguo del historial · Utilidades crecientes = ídem + utilidad neta reciente > 0 · Deuda controlada = liquidez > 1 (o sin pasivos de corto plazo) · Precio razonable = clasificación barata/cara del escenario Conservador | Ingresos y Utilidad Neta del estado de resultados; Activos/Pasivos Circulantes del balance; clasificación de precio ya calculada en Valoración. |
| `ren` | ROE = Ganancia Neta / Patrimonio de los Accionistas · Deuda/Patrimonio = Deuda Total / Patrimonio · Deuda Neta/EBITDA · Dividend Yield · Payout Ratio (reutilizadas literales de `summary.py` líneas 528-575) | Los 5 campos vienen ya precalculados de `/key-metrics` de FMP — el bot no los recalcula, los muestra tal cual. |
| `rsk` | Encaje = comparación de la beta del ticker contra el perfil de riesgo guardado (regla propia del bot, `risk_fit.py`) — sin una fórmula matemática única | Beta es un dato de FMP (`profile.beta`); el perfil de riesgo es el que el usuario fijó con `/start`. |
| `mom` | % vs. máximo/mínimo de 52 semanas y vs. promedios móviles de 50/200 días — comparaciones porcentuales directas, sin fórmula compuesta | `yearHigh`/`yearLow`/`priceAvg50`/`priceAvg200` son datos de la cotización (quote) de FMP; VIX es un dato de FMP (símbolo `^VIX`), volatilidad del S&P 500 en general, no del ticker. |
| `cmp` | PER propio = Precio / EPS (TTM) — comparado contra el PER de cada peer (1 / earningsYield) | PER propio: cálculo del bot con datos propios; PER de cada peer: cálculo del bot con `earningsYield` de `/key-metrics` de cada peer (lista fija en `peers.py` o dinámica vía Finnhub, según qué respondió esta consulta). |
| `evt` | *(sin fórmula — hechos, no cálculo)* | SEC EDGAR (oficial, gratis) — formularios 8-K que la empresa está obligada a presentar por ley. |
| `inf` | *(sin fórmula — meta-información)* | Agrega, sin cambios, las notas de transparencia ya construidas por `summary.py` (fuente de peers, fuente de Y, fuente de boletín/foto/extracto, disclaimer de WACC, disclaimer de no-asesoramiento). |

### 7. Tabla completa de fórmula + fuente — `/avanzado`

Fórmulas confirmadas contra `advanced_scoring.py` (no inventadas):

| `question_code` | `FORMULAS[...]` | `FUENTES[...]` |
|---|---|---|
| `mod` | *(sin fórmula — síntesis de qué modelos aplican)* | *(sin bloque)* |
| `alz` | `Z = 1.2A + 1.4B + 3.3C + 0.6D + 1.0E` — A=Capital de Trabajo/Activos Totales, B=Utilidades Retenidas/Activos Totales, C=EBIT/Activos Totales, D=Capitalización de Mercado/Pasivos Totales, E=Ventas/Activos Totales | Capital de Trabajo = Activos Corrientes − Pasivos Corrientes (balance); Utilidades Retenidas, Activos/Pasivos Totales (balance); EBIT y Ventas (estado de resultados); Capitalización de Mercado (cotización). |
| `azp` | `Z'' = 6.56A + 3.26B + 6.72C + 1.05D` — mismas A-D que Z, sin el factor E (ventas/activos), variante para empresas asset-light | Mismos campos que `alz`, sin Ventas. |
| `pig` | `F-Score = suma de los 9 criterios binarios cumplidos (0 a 9)` | Cada criterio sale de balance/estado de resultados/flujo de efectivo del año actual y el anterior — ver `pir`/`pia`/`pie` para el detalle campo por campo. |
| `pir` | 4 criterios: ROA positivo (Ganancia Neta > 0) · CFO positivo (Flujo de Caja Operativo > 0) · ROA creciente (Ganancia Neta/Activos Totales sube vs. año anterior) · CFO > Utilidad Neta | Ganancia Neta y Activos Totales del año actual y anterior (estado de resultados + balance); Flujo de Caja Operativo del estado de flujo de efectivo. |
| `pia` | 3 criterios: apalancamiento decreciente (Deuda Largo Plazo/Activos Totales baja) · liquidez creciente (Activos/Pasivos Corrientes sube) · sin dilución (acciones en circulación no aumentan) | Deuda Largo Plazo, Activos/Pasivos Totales y Corrientes del balance (año actual y anterior); acciones en circulación del estado de resultados. |
| `pie` | 2 criterios: margen bruto creciente (Utilidad Bruta/Ventas sube) · rotación de activos creciente (Ventas/Activos Totales sube) | Utilidad Bruta, Ventas y Activos Totales del estado de resultados y balance (año actual y anterior). |
| `ben` | *(D1, no reabrir: siempre "no calculable" — sin fórmula aplicada)* | FMP en el plan gratuito no separa PP&E bruto ni depreciación pura de amortización, campos que el modelo original exige. |
| `mgr` | `ROIC = EBIT / (Capital de Trabajo Neto + Activos Fijos Netos)` | EBIT del estado de resultados; Capital de Trabajo Neto = Activos Corrientes − Pasivos Corrientes (balance); Activos Fijos Netos (PP&E neto) del balance. |
| `mge` | `Earnings Yield = EBIT / EV`, con `EV = Capitalización de Mercado + Deuda Total − Efectivo` | EBIT del estado de resultados; Deuda Total y Efectivo del balance; Capitalización de Mercado de la cotización. |
| `aqv` | Basado en Earnings Yield (mismo cálculo que `mge`) contra umbrales fijos: >8% alto, 4-8% medio, <4% bajo | Mismo origen que `mge`; umbrales documentados en `advanced_scoring.FACTOR_UMBRALES`. |
| `aqq` | Combina ROE (>15% alto, <5% bajo), Margen Bruto (>40% alto, <20% bajo) y ratio de Piotroski (evaluables cumplidos/evaluables, >75% alto, <40% bajo) — cada sub-métrica disponible aporta ±1/0, suma positiva → alto, negativa → bajo | ROE de `/key-metrics` de FMP; Margen Bruto del estado de resultados; ratio de Piotroski del cálculo ya hecho en `pig`. |
| `aqm` | Reutiliza la etiqueta cualitativa de Momentum (mismo cálculo que `mom` del flujo de texto libre) | `yearHigh`/`yearLow`/`priceAvg50`/`priceAvg200` de la cotización. |
| `aql` | Basado en beta: <0.8 bajo, 0.8-1.2 medio, >1.2 alto | Beta es un dato de FMP (`profile.beta`). |

### 8. Extensión de `ExplanationContext` y de `explain_context_sink` — todo ya calculado, nada recalculado

Mismo principio ya establecido en la spec cerrada (Decisión de diseño #3): el store solo guarda objetos que cada flujo YA calculó para armar su mensaje. Estos campos existen hoy como variables/dicts locales que se descartan — esta spec los conserva.

**`ExplanationContext` — campos nuevos para `kind="texto_libre"`** (todos ya calculados en `query_handler.fetch_and_analyze_parts`, líneas 498-553, hoy no enviados a `explain_context_sink`):

```python
ratios: Optional[dict] = None            # = ratios_dict, línea 498
risk_fit: Optional[dict] = None          # = risk_fit_dict, línea 518
momentum: Optional[dict] = None          # = momentum_dict, línea 524
peer_comparison: Optional[dict] = None   # = peer_comparison_dict, línea 531
extras: Optional[dict] = None            # = extras_dict, línea 545
vix: Optional[dict] = None               # = vix_dict, línea 553
corporate_events: Optional[list] = None  # = corporate_events_list, línea 480-496
treasury_source: Optional[str] = None    # ya calculado, línea 392-400
balance_sheet_fuente: Optional[str] = None      # = balance_fuente
income_statement_fuente: Optional[str] = None   # = income_statements_fuente
cash_flow_fuente: Optional[str] = None          # = cash_flow_fuente
peers_note: Optional[str] = None         # = summary._build_peers_note(peer_comparison_dict["fuente_peers"]) — ya se calcula dentro de build_summary_parts, se saca 1 nivel más arriba para reusar sin recalcular
```

**Campos nuevos para `kind="avanzado"`** (ya calculados en `advanced_command._build_message`, líneas 155/201-206, hoy no enviados al sink):

```python
roe: Optional[float] = None
gross_margin: Optional[float] = None
beta: Optional[float] = None
```

**Cambio mecánico en `query_handler.fetch_and_analyze_parts`**: el `explain_context_sink.update(...)` (líneas 555-568) agrega estos 12 campos nuevos, todos ya existentes como variables locales en ese momento de la función — 0 cálculo nuevo, 0 llamada HTTP nueva.

**Cambio mecánico en `advanced_command._build_message`**: el `explain_context_sink.update(...)` (líneas 274-292) agrega `roe`, `gross_margin`, `beta` (ya son variables locales de la función).

### 9. Acortamiento del primer mensaje — qué queda arriba vs. qué se mueve

**Regla de no-regresión (crítica): el acortamiento SOLO aplica cuando `clients.ollama_config.enabled` es `True`.** Si Ollama está deshabilitado no hay manera de recuperar el detalle con un botón — mostrar la versión corta perdería información sin forma de acceder a ella. Con Ollama deshabilitado, **ambos flujos siguen mostrando exactamente el reporte completo de hoy, sin ningún cambio** (mismo criterio ya usado en toda la historia del proyecto: "sin la feature habilitada, cero botones, cero cambio de comportamiento").

**Texto libre — con Ollama habilitado, `summary.py` gana `build_summary_parts_short(...)` (función nueva, `build_summary_parts` no se toca):**

Queda arriba (sin botón, siempre visible):
1. Línea de transparencia (`TRANSPARENCY_USED`/`TRANSPARENCY_NOT_USED`) — sin cambios (Decisión de diseño #5 de la spec anterior, no reabierta).
2. Título.
3. `build_veredicto_section` — ya es corta (3-4 líneas), sin cambios.
4. Línea nueva: Valor Justo Total del escenario elegido + precio actual (1 línea, ej. `"Valor Justo Total (Conservador): $182.40 — tu precio actual: $150.00."`) — función nueva `build_valor_justo_teaser_line(scenarios, escenario_elegido, precio_actual) -> str`, extrae solo el total del escenario elegido de `scenarios[escenario_elegido]["valor_justo_total"]`, sin recalcular nada.
5. `"👇 Elegí qué querés que te explique."` + teclado de Nivel 1.

Se mueve completamente detrás de botones: Intro "Tienda de Limonada" (pasa a formar parte de `inf`, primera línea), Ratios, Extras, la tabla completa de Valor Justo (3 escenarios × 3 modelos), Pilares, Contexto de mercado completo (momentum + peers + VIX), Eventos corporativos, Encaje de riesgo, Notas de transparencia completas.

**`/avanzado` — con Ollama habilitado, `advanced_command._build_message` gana una rama corta:**

Queda arriba: título + una síntesis de 1-2 líneas (ej. `"Altman Z: zona segura · Piotroski 7/9 · Magic Formula calculable · Beneish: no calculable con este plan."`) + `"👇 Elegí qué modelo querés ver en detalle."` + teclado de Nivel 1.

Se mueve detrás de botones: el desglose completo de cada modelo (Altman con zona, Piotroski con criterios no evaluables, Magic Formula con ROIC/EY, Factores con las 4 etiquetas), y la línea de "Fuente de los datos" (que pasa a formar parte de la explicación de cada leaf, ya cubierta por la tabla de `FUENTES` de la Decisión #7).

### 10. Rate limiting — solo lo que cuesta (Ollama) se limita, navegación y contenido determinístico no

El balde compartido (`security.InMemoryRateLimiter`, misma clave `str(chat_id)`) sigue protegiendo exactamente lo mismo que protegía antes: llamadas caras (FMP, Ollama). Con esta spec:

- **Abrir el menú (`:m`) o una categoría (`:c:{cat}`)**: NO consume el balde — es armar una lista de botones ya conocida en memoria, costo cero, sin llamada de red de ningún tipo.
- **Leaf determinístico (`evt`/`inf`)**: NO consume el balde — mismo motivo, es texto ya armado.
- **Leaf con Ollama**: SÍ consume el balde, igual que hoy — sin cambios en el mecanismo, la clave, ni el orden de chequeo (`query.answer()` primero, después el rate limit, después la llamada a Ollama).

**Por qué esto no afloja la protección real:** el balde existe para topear cuánto FMP/Ollama se consume por minuto — la navegación nunca tocó ninguno de los dos. No limitarla no abre ninguna superficie nueva de abuso (el límite práctico sigue siendo cuántos mensajes por segundo puede mandar un solo `chat_id` autorizado, gobernado por Telegram mismo, no por este bot).

### 11. Guard anti-invención — sin cambios de mecanismo, extendido a más leaves

`_no_new_protected_tokens`/`_normalize_numeric_token` (Bug 1 y guard original, no reabiertos) se aplican EXACTAMENTE igual a los 12 `question_code` nuevos que llaman a Ollama (`gra`/`dcf`/`mul`/`rat`/`ren`/`rsk`/`mom`/`cmp` en texto libre; `alz`/`azp`/`pig`/`pir`/`pia`/`pie`/`mgr`/`mge`/`aqv`/`aqq`/`aqm`/`aql` en `/avanzado`) — mismo criterio de superficie mínima de la Decisión de diseño #4a original: `_build_explain_payload` arma, para cada `question_code`, SOLO el sub-dict que esa pregunta puntual necesita, nunca el `ExplanationContext` completo. La fórmula y la fuente (Decisión #5) NUNCA pasan por este guard porque nunca pasan por Ollama — quedan completamente fuera de su superficie.

**Extensión necesaria del mismo patrón "constante nombrada" del Bug 2 de producción:** los `total_pilares`/`total_modelos`/`total_factores` ya existentes se completan con constantes equivalentes donde aplique (ej. `total_criterios_piotroski_rentabilidad = 4` para `pir`) — mismo criterio, mismo motivo (números del marco conceptual del bot, no datos del ticker, deben viajar como dato garantizado o el guard los rechaza como alucinación).

### 12. `sector`/`industry` — misma sanitización, ahora también en `aqq` (Quality)

`aqq` necesita `gross_margin` (nuevo campo, número puro) pero no necesita `sector`/`industry` — no hay superficie nueva de sanitización de texto libre de terceros más allá de la ya existente (`_validated_sector`, exclusión total de `industry`, hallazgo 1 BLOQUEANTE de `security` de la spec anterior, no reabierto). Ningún `question_code` nuevo introduce un campo de texto libre de FMP que no esté ya cubierto por esas 2 protecciones.

---

## Decisiones abiertas para Daniela

*(genuinamente de producto/UX — RESUELTAS por Daniela 2026-09-02, las 3 con la opción recomendada por `architect`)*

**D1 — RESUELTO: sí lleva el "teaser" de 1 línea** (Valor Justo Total + precio actual) en el mensaje corto inicial.

**D2 — RESUELTO: "Encaje con tu perfil de riesgo" pasa a estar detrás de un botón** (`rsk`, dentro de "Riesgo y mercado"), por consistencia con el resto del rediseño.

**D3 — RESUELTO: cuando Ollama está deshabilitado, el bot vuelve a mostrar el reporte completo de siempre**, sin acortar y sin botones — nunca se pierde acceso a información ya calculada por un problema de infraestructura local.

---

## Presupuesto / impacto

- **FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR: 0 llamadas nuevas, siempre** — igual que la spec anterior, todo el contenido nuevo de `ExplanationContext` es un subconjunto de dicts que cada flujo YA calcula.
- **Ollama:** hasta 27 llamadas posibles por análisis completo (13 en texto libre + 14 en `/avanzado`, si el usuario toca cada botón de Ollama al menos una vez) contra 8 posibles hoy (3+5) — sigue siendo 100% on-demand, nunca automático; el usuario decide cuántas toca. El `num_predict=220`/timeout/tamaño de payload por request no cambian.
- **Memoria del VPS — `ExplanationContextStore` recalculado:** cada `ExplanationContext` pasa de ~1-2 KB (5-10 campos, spec anterior) a un estimado de ~3-4 KB serializados para texto libre (agrega `ratios`/`risk_fit`/`momentum`/`peer_comparison` con su dict de PER por peer/`extras`/`vix`/`corporate_events`/3 strings de fuente/`peers_note`) y ~2-2.5 KB para `/avanzado` (agrega solo 3 floats). Con `max_entries=500` sin cambios, el store completo ronda **1.5-2 MB** (contra ~0.5-1 MB antes) — sigue siendo despreciable frente a `mem_limit: 256m` de `docker-compose.prod.yml`. No hace falta bajar `max_entries` ni `ttl_seconds`.
- **Rate limiter compartido:** sin cambio de mecanismo (Decisión de diseño #10) — con más botones posibles, un usuario que explora mucho en poco tiempo puede agotar el balde de 10 req/60s más rápido que antes; es un trade-off aceptado, no un problema nuevo (el balde existe justamente para eso, y solo cuenta contra llamadas que realmente cuestan).
- **Tamaño de `ai_explain.py`:** el módulo crece sustancialmente (de ~700 a probablemente 1200+ líneas, con las 2 tablas nuevas `FORMULAS`/`FUENTES` de 13+14 entradas cada una). Queda a discreción de `implementer` dividirlo en `ai_explain.py` (mecanismo: store, keyboard, handler) + un archivo de contenido nuevo (ej. `ai_explain_content.py` con las tablas de preguntas/fórmulas/fuentes) si el archivo único se vuelve difícil de mantener — no bloqueante, ninguna de las 2 opciones cambia comportamiento.

---

## Criterios de aceptación

### Navegación (Nivel 1 / Nivel 2 / Menú)
- [ ] `callback_data` de la forma `xp:{context_id}:m` manda un mensaje NUEVO con los botones de Nivel 1 correspondientes al `kind` del contexto guardado.
- [ ] `callback_data` de la forma `xp:{context_id}:c:{cat}` manda un mensaje NUEVO con los botones de Nivel 2 de esa categoría + un botón "🔙 Menú" — para una categoría inexistente/mal formada, mismo camino que `callback_data` inválido (`EXPLAIN_INVALID_MSG`, `sanitize_for_log`, sin excepción).
- [ ] El botón "🔙 Menú" presente en cada submenú y en cada explicación (determinística o vía Ollama) manda SIEMPRE un mensaje nuevo — nunca edita el mensaje donde vive el botón, ni ningún mensaje anterior.
- [ ] El teclado de Nivel 1 del análisis original (texto libre y `/avanzado`) sigue el mismo criterio de adjuntarse solo al último chunk entregado (Decisión de diseño #1 de la spec anterior, no reabierta).
- [ ] Para un ticker sin `altman_pp` (no asset-light), la categoría `alt` muestra solo el botón `alz` — sin `azp`. Para un ticker con `altman_pp is not None`, muestra ambos.
- [ ] Para un análisis sin eventos corporativos, la categoría `rie` no incluye el botón `evt`.

### Contenido determinístico (fórmula + fuente)
- [ ] Cada leaf con entrada en `FORMULAS` incluye el texto exacto de esa entrada en el mensaje final, sin pasar por Ollama, sin variación entre 2 tickers distintos (mismo string literal).
- [ ] Cada leaf con entrada en `FUENTES` incluye el texto exacto de esa entrada en el mensaje final, sin pasar por Ollama.
- [ ] El bloque "📌 Dato" de cada leaf (Decisión de diseño #5) refleja el valor real del `ExplanationContext` guardado para ese análisis — test explícito con 2 tickers distintos confirmando que el dato cambia acorde.
- [ ] `evt`/`inf` responden inmediato (sin mensaje "🤔 Pensando…", sin llamada HTTP a Ollama) — test que confirma cero requests al mock de Ollama para estos 2 `question_code`.
- [ ] Los `question_code` de `evt`/`inf` no consumen el rate limiter compartido — test que agota el balde con otras consultas y confirma que `evt`/`inf` igual responden.
- [ ] Los `question_code` de navegación (`m`, `c:{cat}`) no consumen el rate limiter compartido — mismo test que el punto anterior.

### Guard anti-invención (extendido, no aflojado)
- [ ] Los 12 `question_code` nuevos que llaman a Ollama pasan por `_no_new_protected_tokens` con el mismo criterio (subconjunto, no igualdad) que los existentes — test explícito por cada uno con una respuesta simulada que alucina un número/ticker nuevo, confirmando rechazo.
- [ ] `datos_del_contexto` de cada `question_code` nuevo contiene SOLO el sub-dict que esa pregunta puntual necesita — nunca el `ExplanationContext` completo (test de superficie mínima, mismo patrón que los `question_code` existentes).
- [ ] Constantes de conteo nuevas (ej. `total_criterios_piotroski_rentabilidad`) viajan como dato garantizado en `datos_del_contexto` cuando la pregunta fija las menciona — mismo criterio del Bug 2 de producción, test explícito que confirma que Ollama puede citarlas sin que el guard las rechace.

### Acortamiento del primer mensaje
- [ ] Con `OLLAMA_REWRITE_ENABLED` habilitado, el primer mensaje de texto libre contiene únicamente: línea de transparencia, título, veredicto, teaser de Valor Justo Total, e invitación a usar los botones — nunca las secciones de Ratios/Extras/tabla completa de Valor Justo/Pilares/Contexto de mercado/Eventos corporativos/Encaje de riesgo/Notas de transparencia completas.
- [ ] Con `OLLAMA_REWRITE_ENABLED` habilitado, el primer mensaje de `/avanzado` contiene únicamente: línea de transparencia fija, título, síntesis de 1-2 líneas, e invitación a usar los botones — nunca el desglose completo de los 5 modelos.
- [ ] **Con `OLLAMA_REWRITE_ENABLED` deshabilitado, el comportamiento de AMBOS flujos es byte-idéntico al comportamiento actual (pre-spec)** — reporte completo, sin acortar, sin botones de ningún tipo — test de regresión explícito.
- [ ] Cada sección movida detrás de un botón (Ratios, Extras, tabla de Valor Justo, Pilares, Contexto de mercado, Eventos corporativos, Encaje de riesgo, Notas de transparencia) es alcanzable a través de exactamente un `question_code` — test de cobertura que recorre cada sección del reporte completo de hoy y confirma que existe un botón que la muestra (directa o parcialmente, según la tabla de la Decisión #2/#3).

### Regresión
- [ ] `_build_explain_payload` sigue rechazando `question_code` desconocidos con `ValueError` (red de seguridad, sin cambios de comportamiento para los `question_code` ya existentes: `vf`/`pil`/`ver`/`mod`/`alt`→`alz`/`azp` según corresponda/`pio`→`pig`/`mag`→`mgr`/`mge`/`aqr`→`aqv`/`aqq`/`aqm`/`aql`).
- [ ] La suite completa (`pytest -q`) sigue en verde, incluidos todos los tests existentes de `test_ai_explain.py`/`test_query_handler.py`/`test_advanced_command.py`/`test_summary.py` actualizados al nuevo contrato.
- [ ] D1 (Beneish siempre "no calculable") sigue verificado exactamente igual — `ben` no cambia de comportamiento, solo de contexto de navegación (ahora es un leaf suelto entre otros, antes era uno de 5 botones planos).

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain.py` → cambios mayores: `ExplanationContext` gana 12 campos nuevos (texto libre) + 3 (avanzado); `QuestionSpec` (dataclass nueva, metadata `requires_ollama`); tablas `QUESTIONS_TEXTO_LIBRE`/`QUESTIONS_AVANZADO` migran de tuplas planas a estructura por categoría (`CATEGORIES_TEXTO_LIBRE`/`CATEGORIES_AVANZADO`, cada categoría con label + lista de `question_code`, más los leaves sueltos); `FORMULAS`/`FUENTES` (2 diccionarios nuevos, tablas completas de las Decisiones #6/#7); `_CALLBACK_RE` ampliado a 3 formas (Decisión #1); `build_keyboard` reescrito para Nivel 1 (categorías + leaves sueltos) y Nivel 2 (leaves de una categoría + "🔙 Menú"); `_build_explain_payload` extendido con los 27 `question_code`; `build_explain_handler` reescrito para dispatchear las 3 formas de `callback_data` y bifurcar determinístico/Ollama por `requires_ollama`.
- `src/investbot/query_handler.py` → `fetch_and_analyze_parts`: `explain_context_sink.update(...)` agrega los 12 campos nuevos (todos ya calculados localmente, ver Decisión #8); `_run_analysis`: elige entre `summary.build_summary_parts` (Ollama deshabilitado) y `summary.build_summary_parts_short` (Ollama habilitado) según `ollama_config.enabled`.
- `src/investbot/advanced_command.py` → `_build_message` gana una rama corta (Decisión #9) elegida según si Ollama está habilitado; `explain_context_sink.update(...)` agrega `roe`/`gross_margin`/`beta`.
- `src/investbot/summary.py` → función nueva `build_summary_parts_short(...)` (no reemplaza `build_summary_parts`, que sigue existiendo sin cambios para el camino "Ollama deshabilitado"); función nueva `build_valor_justo_teaser_line(...)`.
- `tests/test_ai_explain.py` → tests de las 3 formas de `callback_data`, las 27 combinaciones formula+fuente, los leaves determinísticos, el guard extendido, el rate limiting selectivo (a completar por `qa`).
- `tests/test_query_handler.py` → tests de `explain_context_sink` con los 12 campos nuevos; tests de `build_summary_parts_short` vs. `build_summary_parts` según `ollama_config.enabled`.
- `tests/test_advanced_command.py` → tests de la rama corta del mensaje; tests de `roe`/`gross_margin`/`beta` en el sink.
- `tests/test_summary.py` → tests de `build_summary_parts_short`/`build_valor_justo_teaser_line`.

---

## Restricciones — no reabrir

- **D1 (Beneish M-Score, `advanced_scoring.py`)**: sigue siempre "no calculable con los datos disponibles". `ben` no cambia de comportamiento.
- **Guard anti-invención (`_no_new_protected_tokens`)**: mismo mecanismo, mismo criterio de subconjunto — no se afloja para acomodar fórmulas ni el bloque "📌 Dato" (ambos quedan FUERA de su superficie por diseño, Decisión #5/#11, no porque el guard se relaje).
- **Rate limiter compartido, misma clave (`str(chat_id)`)**: sigue siendo el mismo balde de `security.InMemoryRateLimiter` para FMP + Ollama + botones de explicación — Decisión #10 solo acota QUÉ acciones lo consumen, no crea un balde nuevo ni cambia la clave.
- **Mensaje "🤔 Pensando…"**: se mantiene, sin cambios de texto, para todo leaf con `requires_ollama=True` — los leaves determinísticos (`evt`/`inf`) simplemente no lo necesitan (no hay espera que comunicar).
- **`sector`/`industry` — sanitización (hallazgo 1 BLOQUEANTE de `security`, spec anterior)**: `_validated_sector`/exclusión total de `industry` siguen aplicando sin cambios; ningún `question_code` nuevo reintroduce texto libre de FMP sin pasar por esa protección.
- **Brevedad de cada explicación (2-4 oraciones + `_MAX_EXPLANATION_CHARS=480`)**: sigue aplicando a la parte generada por Ollama de cada leaf — el bloque de fórmula/fuente/dato es determinístico y se agrega DESPUÉS de aplicar `_enforce_brevity` sobre la respuesta de Ollama, nunca cuenta contra ese límite (igual que `DISCLAIMER_NO_ASESORAMIENTO` hoy).
- **Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR**: sin excepción, en ningún punto de esta spec.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_menu_por_capas_explicaciones.md` (esta spec)

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — cubre navegación, contenido determinístico, guard anti-invención extendido, acortamiento del mensaje con regla de no-regresión, y regresión general.

### Decisiones de diseño tomadas (para que `implementer` no las reabra)
1. Navegación en 2 niveles, 3 formas de `callback_data` (`:m`, `:c:{cat}`, `:{code}`), "🔙 Menú" siempre manda mensaje nuevo, nunca edita contenido ya entregado.
2. Agrupación temática de texto libre: Veredicto (suelto) / Valoración / Calidad del negocio / Riesgo y mercado / Fuentes y transparencia (suelto).
3. Agrupación temática de `/avanzado`: ¿Qué modelos aplican? (suelto) / Altman Z / Piotroski F / Beneish M (suelto) / Magic Formula / Factores AQR.
4. `evt`/`inf` son 100% determinísticos — nunca llaman a Ollama, no consumen el rate limiter, responden inmediato.
5. Fórmula + fuente del dato son SIEMPRE determinísticas (texto fijo en Python), nunca redactadas por Ollama — se agregan después de la respuesta del modelo, fuera de su superficie de guard.
6-7. Tablas completas de `FORMULAS`/`FUENTES` por `question_code`, ambos flujos — ver Decisiones #6/#7 arriba, sacadas del código real (`valuation.py`/`advanced_scoring.py`/`rules.py`/`summary.py`), no inventadas.
8. `ExplanationContext` extendido con 12 campos (texto libre) + 3 (avanzado), todos ya calculados hoy y descartados — cero cálculo nuevo.
9. Acortamiento del primer mensaje SOLO cuando Ollama está habilitado — con Ollama deshabilitado, comportamiento idéntico al actual (regla de no-regresión, no negociable).
10. Rate limiting selectivo: solo leaves con Ollama consumen el balde; navegación y leaves determinísticos no.
11-12. Guard anti-invención sin aflojar, extendido a 12 `question_code` nuevos con el mismo criterio de superficie mínima; sin superficie nueva de texto libre de terceros sin sanitizar.

### Puntos que señalar explícitamente a `security`
- Superficie de datos hacia Ollama ampliada de ~7 a ~27 sub-dicts posibles por análisis — mismo mecanismo de superficie mínima por pregunta, pero vale una revisión de que ningún `question_code` nuevo filtre más de lo necesario (en particular `aqq`, que combina 3 sub-métricas).
- `callback_data` con 3 formas en vez de 1 — la regex nueva y el dispatch deben rechazar sin excepción cualquier combinación fuera de las 3 formas válidas, mismo criterio que hoy.
- Reducción de información visible sin tocar ningún botón (Ratios/Extras/Encaje de riesgo/Pilares pasan de "siempre visibles" a "detrás de un tap") — es una decisión de producto/UX (D1/D2 arriba), no de seguridad de contenido, pero se señala por si `security` identifica algún caso donde ocultar información por defecto tenga implicancia de seguridad del usuario (ej. Encaje de riesgo).

---

## Revisión de seguridad

**Rol:** `security`
**Fecha:** 2026-09-02
**Insumos revisados:** esta spec completa + código real (`src/investbot/ai_explain.py`, `query_handler.py`, `advanced_command.py`, `summary.py`, `security.py`, `treasury_client.py`, `peers.py`, `market_context.py`, `bot.py`) — no solo lo documentado, mismo criterio que usó `architect` para esta spec.
**Veredicto general:** **0 hallazgos bloqueantes.** 1 hallazgo MEDIO no bloqueante (hallazgo 9, nuevo) que se recomienda resolver en esta misma iteración porque es barato y su ventana de exposición la abre justamente una decisión de esta spec (#10). El resto son confirmaciones de los 5 puntos pedidos por `architect` en el Handoff, más 2 mejoras menores no bloqueantes.

### 1. Navegación por 2 niveles — confirmado, sin hallazgos bloqueantes

Leído `_CALLBACK_RE` actual (línea 269, `^xp:([0-9a-f]{8}):([a-z]{2,4})$`) y el nuevo diseño de 3 formas de la Decisión #1. Confirmo por construcción lo que dice la spec: el literal `c:` en la forma "categoría" es sintácticamente imposible de producir con `[a-z]{2,4}` (esa clase no admite `:`), y el token `m` (una sola letra) es sintácticamente imposible de producir con `{2,4}` (mínimo 2 caracteres) — las 3 formas son mutuamente excluyentes sin ambigüedad, sea que `implementer` use un regex con 3 alternativas o 3 regex separados. `callback_data` fuera de las 3 formas cae por el mismo camino que hoy (`EXPLAIN_INVALID_MSG` + `_sanitize_for_log`, sin excepción) — correcto.

`ExplanationContextStore` (líneas 117-178): confirmado que la navegación (`:m`, `:c:{cat}`) **no llama a `store.put()`** — solo `store.get(context_id)` sobre una entrada que ya existe (creada una sola vez por análisis, igual que hoy). El tamaño del store sigue creciendo únicamente con la cantidad de análisis corridos, no con cuántas veces se navega dentro de uno — el cálculo de presupuesto de memoria de `architect` (1.5-2 MB con `max_entries=500`) es correcto y no cambia por la navegación en sí, solo por el tamaño mayor de cada `ExplanationContext` individual (que sí crece por los 12+3 campos nuevos, ya presupuestado). **TTL y tope de tamaño no se debilitan.**

**Mejora recomendada, no bloqueante:** los criterios de aceptación cubren "categoría inexistente/mal formada" para `xp:{id}:c:{cat}`, pero no cubren explícitamente el caso "categoría sintácticamente válida pero de la tabla equivocada para el `kind` del contexto" (ej. mandar `xp:{id}:c:val` — categoría real de texto libre — contra un contexto guardado con `kind="avanzado"`). El código actual ya resuelve el caso análogo a nivel de leaf (líneas 638-643: `question_code` válido pero de la tabla equivocada → `EXPLAIN_EXPIRED_MSG`) — el mismo patrón debe extenderse al dispatch de `:c:{cat}`. Si no se agrega el criterio explícito, el peor caso no es una fuga de datos (el `add_error_handler` global de `bot.py`, línea 121, atrapa cualquier excepción no manejada y solo logea — nunca reenvía traceback al usuario ni crashea el proceso) sino, a lo sumo, un botón que no responde. Recomiendo agregar como criterio de aceptación explícito:
- [ ] `xp:{context_id}:c:{cat}` con una categoría que existe para el OTRO `kind` (no el del contexto guardado) sigue el mismo camino que categoría inexistente — mismo mensaje, mismo log sanitizado.

### 2. Contenido determinístico (fórmula + fuente) — confirmado, sin hallazgos

Verificado contra el código real, no solo contra la spec: el patrón "texto fijo en Python, nunca interpolado con un campo libre de FMP" que las Decisiones #5/#6/#7 proponen para `FORMULAS`/`FUENTES` es exactamente el mismo patrón que el proyecto **ya usa hoy** en 3 lugares distintos que confirmé por lectura directa:
- `summary._MODELO_FORMULAS` (línea 93-101): 3 strings fijos, sin interpolación.
- `summary._build_peers_note` (línea 751-763): retorna uno de 2 strings fijos (`_PEERS_NOTE_FINNHUB`/`_PEERS_NOTE_FIJO`) según una comparación de enum, nunca el texto crudo de ningún campo.
- `treasury_client.py` (línea 158/166): `source` es una de 2 constantes fijas (`SOURCE_FRED`/`SOURCE_TREASURY_GOV`), nunca un string devuelto por la API externa.

Las 27 entradas de las tablas de las Decisiones #6/#7, tal como están escritas en esta misma spec, son 100% texto literal — ninguna interpola `sector`/`industry` ni ningún otro campo de texto libre de FMP. Mismo criterio ya aplicado al hallazgo 1 bloqueante de la spec anterior. **Sin hallazgos.** Confirmo también que `evt`/`inf` (Decisión #4) heredan contenido ya construido por `summary.build_corporate_events_section`/notas de transparencia — código pre-existente, ya revisado en specs anteriores, sin cambios de superficie por esta spec (se mueve detrás de un botón, no se genera contenido nuevo).

### 3. Gate de `chat_id` — confirmado, sin hallazgos

`security.build_chat_id_gate` (líneas 122-148) se registra como `TypeHandler(Update, gate)` en `group=-1` (`bot.py` línea 68-70), y cubre **todo tipo de `Update`** sin importar el `callback_data` — no filtra por prefijo `xp:` ni por forma. Las 3 formas nuevas (`:m`, `:c:{cat}`, `:{code}`) llegan como cualquier otro `callback_query` y pasan por el gate ANTES de llegar a `build_explain_handler`. No requiere ningún cambio para cubrir la navegación nueva — mismo mecanismo confirmado en las 2 revisiones anteriores de esta feature.

### 4. Rate limiter selectivo (Decisión #10) — sin hallazgos bloqueantes, 1 hallazgo MEDIO nuevo relacionado (ver Hallazgo 9)

Confirmo el argumento de `architect`: abrir el menú, abrir una categoría, y responder `evt`/`inf` son operaciones O(1) en memoria (diccionarios ya en memoria del proceso, sin I/O de FMP/Ollama) — no hay manera de saturar CPU/memoria del VPS solo con esto, y el límite práctico real es cuántos `callback_query` por segundo puede generar un solo `chat_id` autorizado, que es bajo para un humano tocando botones en Telegram.

Sin embargo, al auditar la interacción de esta decisión con el resto del mecanismo de acceso encontré el **Hallazgo 9** (abajo) — no es un problema de "cuánto cuesta" sino de "a quién se le muestra", y la Decisión #10 amplía la ventana de explotación de un problema de control de acceso preexistente.

### 5. Manejo de errores / logging — confirmado, sin hallazgos

`bot.py::_on_error` (líneas 39-44) es el `error_handler` global: cualquier excepción no manejada en cualquier handler (incluidos los 3 dispatch nuevos de `xp:`) se logea con `logger.exception` (traceback va al log del proceso, nunca al chat de Telegram) y nunca se re-lanza. Los logs de `ai_explain.py`/`advanced_command.py` usan `_sanitize_for_log`/`sanitize_for_log` de forma consistente antes de interpolar cualquier dato de entrada del usuario o del `callback_data`. Sin cambios de mecanismo necesarios para esta spec — los `question_code`/categorías nuevos deben seguir logueándose sanitizados, mismo patrón que ya usan `question_code`/`raw_data` hoy (líneas 616-630).

---

### Hallazgo 9 [MEDIO — no bloqueante, remediación recomendada en esta iteración] — `ExplanationContext` no está atado al `chat_id` que lo generó (IDOR entre chat_id autorizados)

**CWE**: CWE-639 — Authorization Bypass Through User-Controlled Key
**OWASP**: A01:2025 — Broken Access Control
**ASVS**: V4.1.1 (verificar que las referencias a objetos de otro usuario sean rechazadas)

#### Descripción
`security.py` soporta explícitamente **más de un** `chat_id` autorizado (`TELEGRAM_ALLOWED_CHAT_ID` acepta CSV, docstring líneas 1-19: "hasta N chat_id autorizados"). `ExplanationContext`/`ExplanationContextStore` (líneas 88-178 de `ai_explain.py`) no tienen ningún campo `chat_id` ni ninguna verificación de pertenencia: `store.get(context_id)` devuelve el contexto a **cualquier** `chat_id` que ya pasó el gate global, sin comprobar que sea el mismo `chat_id` para el que se generó ese `context_id`. Esto es preexistente (no lo introduce esta spec), pero la Decisión de diseño #10 de esta spec **amplía la ventana de explotación**: hoy, cualquier `callback_data xp:...` (sin excepción) consume el balde de `InMemoryRateLimiter` (10 req/60s), lo que ya limitaba de forma indirecta cuántos `context_id` por minuto un `chat_id` autorizado podía probar a ciegas. Con esta spec, **la navegación (`:m`, `:c:{cat}`) y los leaves determinísticos (`evt`/`inf`) quedan explícitamente fuera de ese balde** — quien quiera adivinar un `context_id` de otro `chat_id` autorizado ya no está limitado a 10 intentos/minuto para las formas que no cuestan Ollama.

#### Evidencia en el código
```python
# Archivo: src/investbot/ai_explain.py, líneas 633-636 (hoy)
stored = store.get(context_id)
if stored is None:
    await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
    return
# -- nunca se compara `stored` contra el `chat_id` que hizo el request.
```
```python
# Archivo: src/investbot/ai_explain.py, líneas 88-108 (hoy)
@dataclass(frozen=True)
class ExplanationContext:
    kind: str
    ticker: str
    company_name: str
    # ... sin campo chat_id
```

#### Escenario de explotación
Precondición: `TELEGRAM_ALLOWED_CHAT_ID` configurado con 2+ chat_id (feature ya soportada, aunque no sé si está así en producción hoy). El chat_id B (autorizado, pero no el dueño del análisis) manda updates `callback_query` con `data="xp:XXXXXXXX:m"` variando `XXXXXXXX` (espacio de 32 bits, hasta 500 contextos vivos simultáneos con TTL de 12h). Antes de esta spec, cada intento consume el balde compartido (10/60s) — a esa cadencia, cubrir un espacio de miles de millones de combinaciones es impracticable. Con esta spec, `:m` no consume el balde: B puede probar a la velocidad que Telegram le permita mandar `callback_query` (varios por segundo), reduciendo sustancialmente el tiempo esperado para acertar CUALQUIERA de los hasta 500 contextos vivos de otros chat_id. Al acertar, ve el menú de Nivel 1 de un análisis ajeno (ticker, veredicto, teaser de Valor Justo) y puede seguir navegando hacia `evt`/`inf` de ese contexto ajeno (tampoco limitados) sin gastar ni un solo request del balde.

Impacto: expone qué ticker analizó otro usuario autorizado y su valoración — dato sensible en términos de privacidad financiera personal, no una credencial ni un dato de terceros, pero sí "información de otro usuario" que el diseño (fail-closed por `chat_id`, `security.py`) pretende segmentar por persona. No es explotable por nadie fuera del conjunto de chat_id ya autorizados — el gate de `chat_id` sigue siendo la primera barrera y no se salta.

#### Remediación
Agregar `chat_id: int` a `ExplanationContext` (y a `_StoredEntry` o reusar el mismo campo), poblado en `query_handler._run_analysis`/`advanced_command.avanzado` al momento de `store.put(...)`, y verificado en `handle_explain` antes de despachar CUALQUIERA de las 3 formas (`:m`, `:c:{cat}`, `:{code}`):

```python
# ai_explain.py — ExplanationContext gana un campo obligatorio
@dataclass(frozen=True)
class ExplanationContext:
    chat_id: int
    kind: str
    ticker: str
    ...

# handle_explain, después de `stored = store.get(context_id)`
if stored is None:
    await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
    return
if stored.chat_id != chat_id:
    # Mismo mensaje y mismo log que "vencido" -- no revelar que el
    # context_id SÍ existe pero es de otro chat_id (evita side-channel
    # que ayudaría a un atacante a distinguir "no existe" de "existe pero
    # no es mío").
    logger.warning(
        "Intento de acceso a ExplanationContext de otro chat_id: %s", context_id
    )
    await context.bot.send_message(chat_id=chat_id, text=EXPLAIN_EXPIRED_MSG)
    return
```

Criterio de aceptación a agregar (para `qa`/`implementer`):
- [ ] Un `context_id` válido, generado para `chat_id=A`, usado desde `chat_id=B` (ambos autorizados) en cualquiera de las 3 formas (`:m`, `:c:{cat}`, `:{code}`) responde `EXPLAIN_EXPIRED_MSG` — nunca el contenido del análisis de A.
- [ ] El mensaje y el log para "context_id de otro chat_id" son indistinguibles de "context_id vencido/inexistente" (mismo string, mismo nivel de log) — no debe poder inferirse por la respuesta si el `context_id` probado existe.

**Esfuerzo estimado**: 1-2 horas (1 campo nuevo + 1 comparación + 2 tests). Cabe perfectamente dentro del alcance de esta misma iteración, junto con el resto de los campos nuevos que ya se están agregando a `ExplanationContext`.
**Referencia**: OWASP Cheat Sheet — Authorization; CWE-639.

**Por qué no es bloqueante:** requiere (a) que `TELEGRAM_ALLOWED_CHAT_ID` tenga configurado más de un chat_id autorizado — no confirmé que sea el caso en el despliegue actual de producción — y (b) un espacio de búsqueda de 32 bits que, aun sin el balde, sigue tomando horas/días de intentos continuos para acertar un contexto vivo, con una ventana de éxito acotada al TTL de 12h. Con un solo chat_id autorizado en producción, este hallazgo no es explotable en absoluto (no hay "otro chat_id" al que apuntar). Se recomienda resolver ahora porque el costo es bajo y porque `security.py` deja la puerta multi-usuario explícitamente abierta como feature soportada — no como caso hipotético.

---

### Mejoras recomendadas, no bloqueantes (resumen)

1. **Criterio de aceptación faltante** — categoría sintácticamente válida pero de la tabla equivocada para el `kind` del contexto (`xp:{id}:c:{cat}`) debe seguir el mismo camino que categoría inexistente (ver sección 1 arriba).
2. **Hallazgo 9** — atar `ExplanationContext` a `chat_id` y validarlo en `handle_explain`, ver arriba. Recomendado para esta iteración, no bloqueante para el estado mono-usuario actual.

### Checklist de cierre para `qa`/`implementer`
- [ ] Los 2 criterios nuevos de esta revisión (categoría de tabla equivocada, `chat_id` ownership) se agregan a la sección "Criterios de aceptación" antes de que `implementer` cierre el Ralph Loop de esta spec, o se documentan explícitamente como backlog del siguiente run si Daniela decide no priorizarlos ahora.
- [ ] Ningún otro cambio de código requerido antes de pasar a `implementer` — los 5 puntos del Handoff quedan confirmados sin hallazgos bloqueantes.

---

## Criterios QA para Spec: Menú de explicaciones por capas + fórmula/fuente determinística [Iter-1]

**Rol:** `qa`
**Fecha:** 2026-09-02
**Insumos revisados:** spec completa de `architect` (Estado objetivo, 12 Decisiones de diseño, D1-D3 resueltas por Daniela, Criterios de aceptación, Artefactos, Restricciones) + Revisión de seguridad completa de `security` (5 puntos confirmados, Hallazgo 9 MEDIO, 2 mejoras recomendadas, Checklist de cierre).
**Regla aplicada:** no se duplica ningún criterio que `architect` ya dejó concreto — se agregan solo los ángulos de cobertura real que faltaban (huecos de test, no huecos de comportamiento) y se formalizan los 2 puntos que `security` dejó como checklist abierto.

### Tipo de prueba principal
**Unit** para la mayoría de la superficie nueva (dispatch de las 3 formas de `callback_data`, tablas `FORMULAS`/`FUENTES`, `build_keyboard` de 2 niveles, guard extendido, `build_summary_parts_short`/`build_valor_justo_teaser_line`) — es lógica de negocio pura, sin I/O, aislable con `ExplanationContext` fabricado a mano.
**Integration** para el camino completo `handler → store → mock de Ollama` (las 27 combinaciones formula+fuente end-to-end dentro del proceso, sin red) y para `explain_context_sink` poblado por `query_handler`/`advanced_command` real contra fixtures de FMP ya existentes en el proyecto.
Sin **E2E** contra servicios reales — ver sección 4, mismo criterio que la spec cerrada anterior.

### Cobertura mínima requerida
- [ ] Code coverage ≥ 90% en `ai_explain.py` (módulo de mayor riesgo: dispatch de acceso, guard anti-invención, ownership por `chat_id`).
- [ ] Branch coverage = 100% en el dispatch de las 3 formas de `callback_data` (incluida la rama de rechazo y la rama nueva de `chat_id` mismatch) — es lógica de control de acceso, no negociable a menos que 100%.
- [ ] Code coverage ≥ 80% en las funciones nuevas de `query_handler.py`, `advanced_command.py` y `summary.py` (`build_summary_parts_short`, `build_valor_justo_teaser_line`, ramas de `explain_context_sink.update(...)`).
- [ ] Los 34 criterios de aceptación de `architect` (Navegación 6 + Contenido determinístico 6 + Guard 3 + Acortamiento 4 + Regresión 3 = 22, más los agregados en esta sección) están cubiertos por al menos un test con nombre trazable al criterio.

### 1. Revisión de los criterios de aceptación de `architect` — huecos de cobertura real cerrados

Los 5 grupos ya cubren el comportamiento correctamente. Los siguientes son ángulos de **testabilidad** que faltaban — no cambian el comportamiento descrito por `architect`, solo aseguran que quede verificado con evidencia.

#### Navegación
- [ ] `test_menu_and_category_reuse_same_context_id` — un mismo `context_id` sobrevive un recorrido completo Nivel 1 → Nivel 2 → "🔙 Menú" → otra categoría → leaf, sin que `store.put()` se vuelva a llamar (assert sobre el mock de `ExplanationContextStore.put`, contando invocaciones) — formaliza lo que la Decisión #1 y el punto 1 de la revisión de `security` (líneas 405) dan por sentado pero el `architect` no dejó como test explícito.
- [ ] `test_boton_menu_desde_cada_origen` — parametrizado en 3 orígenes: (a) desde un submenú de Nivel 2, (b) desde una explicación determinística (`evt`/`inf`), (c) desde una explicación vía Ollama ya editada in-place — los 3 casos producen un mensaje NUEVO con el teclado de Nivel 1 y el mismo `context_id`. El AC de `architect` dice "presente en cada submenú y en cada explicación" pero no obliga a un test que recorra los 3 orígenes por separado.
- [ ] `test_callback_data_malformado_negativo` — parametrizado: `context_id` con longitud/charset inválido, `:c:` sin categoría, categoría con mayúsculas, categoría con `:` extra, `question_code` de 1 o 5+ caracteres, `context_id` válido pero con el sufijo `:x` no reconocido — los 3 regex (o el regex de 3 alternativas) rechazan todos con el mismo camino que hoy (`EXPLAIN_INVALID_MSG`), no solo "categoría inexistente" como ya cubre el AC de `architect`.
- [ ] `test_menu_o_categoria_con_context_id_vencido` — `xp:{id}:m` y `xp:{id}:c:{cat}` con un `context_id` que no existe en el store (vencido por TTL o nunca existió) responden `EXPLAIN_EXPIRED_MSG` — el AC de `architect` solo prueba esto para el leaf puntual (comportamiento heredado), no explícitamente para las 2 formas nuevas de navegación.
- [ ] `test_teclado_nivel2_incluye_menu_al_final` — el teclado de cualquier categoría termina siempre con el botón "🔙 Menú" con `callback_data == f"xp:{context_id}:m"` — assert de estructura, no solo de comportamiento.

#### Contenido determinístico
- [ ] `test_formato_mensaje_leaf_con_ollama_orden_de_bloques` — para un leaf con `requires_ollama=True` y entrada en ambas tablas, el mensaje final respeta el orden exacto de la Decisión #5 (`🤖 Con ayuda de Ollama` → `📌 Dato` → respuesta de Ollama → `📐 Fórmula` → `📊 Fuente del dato` → disclaimer) — el AC actual verifica presencia de cada bloque por separado, no el orden.
- [ ] `test_bloque_formula_ausente_si_no_hay_entrada` — para `mod`/`ver`/`evt`/`inf`/`pig` (sin entrada en `FORMULAS`), el mensaje final NO contiene la línea `📐 Fórmula:` (omitida, no vacía) — el AC de `architect` no verifica la ausencia explícitamente, solo describe el caso en prosa (línea 149).
- [ ] `test_fuente_ausente_para_mod_ver` — análogo al anterior pero para `FUENTES`, específicamente `mod`/`ver` que la tabla de la Decisión #6/#7 marca como `(sin bloque)`.
- [ ] `test_formula_fuente_fuera_del_limite_de_brevedad` — el bloque determinístico (`📌 Dato`/`📐 Fórmula`/`📊 Fuente`) NO cuenta contra `_MAX_EXPLANATION_CHARS=480` — se trunca/valida solo la respuesta cruda de Ollama antes de anexar los bloques fijos. Este comportamiento está descrito en "Restricciones — no reabrir" (línea 362) pero no tenía un test explícito en el AC de `architect`.

#### Guard anti-invención
- [ ] `test_guard_evaluado_antes_de_anexar_bloques_deterministicos` — confirma que `_no_new_protected_tokens` recibe SOLO la respuesta cruda de Ollama (antes de anexar `📌 Dato`/`📐 Fórmula`/`📊 Fuente`, que están cargados de números que matchearían como tokens protegidos y causarían falsos rechazos si el guard corriera después de anexarlos) — la Decisión #5 punto 2 lo explica en prosa como motivo de diseño, pero no hay un AC que lo verifique como comportamiento del código.
- [ ] `test_guard_superficie_minima_aqq` — caso dedicado (no solo genérico) para `aqq` (Quality), señalado explícitamente por `architect` en el Handoff a `security` (línea 388) como el leaf que combina más sub-métricas (ROE + margen bruto + ratio Piotroski) — confirma que `datos_del_contexto` de `aqq` no incluye ningún campo fuera de esos 3, ni el `ExplanationContext` completo.

#### Acortamiento del primer mensaje
- [ ] `test_teaser_valor_justo_con_escenario_no_calculable` — `build_valor_justo_teaser_line` con `scenarios[escenario_elegido]["valor_justo_total"]` en `None` (modelo no calculable para ese escenario) no rompe ni produce una línea con "None" visible — caso límite no cubierto por el AC de `architect`, que solo prueba el camino feliz.
- [ ] `test_mensaje_corto_longitud_maxima` — con Ollama habilitado, el primer mensaje de cada flujo tiene un techo de longitud razonable (ej. ≤ 600 caracteres, a definir con `implementer` según el texto final) — previene que una sección se "cuele" de vuelta al mensaje corto sin que ningún test de contenido lo detecte por casualidad; complementa (no reemplaza) el test de "solo estas secciones" que ya pide el AC de `architect`.
- [ ] `test_regresion_ollama_deshabilitado_snapshot` — el AC de `architect` pide "byte-idéntico" para D3; se instrumenta como snapshot test contra una captura del output real pre-spec (fixture congelada), no solo una aserción de "contiene las mismas secciones" — un snapshot detecta cualquier diferencia de espaciado/orden que una aserción por sección no vería.

#### Regresión
Sin huecos adicionales — los 3 criterios de `architect` (rechazo de `question_code` desconocido, suite completa verde, D1 sin cambio de comportamiento) ya son suficientes y verificables directamente con la suite existente.

### 2. Checklist de cierre de `security` incorporado como criterios de aceptación formales

#### Categoría de tabla equivocada (mejora #1 de `security`, línea 407-409)
- [ ] **Criterio:** `xp:{context_id}:c:{cat}` con una categoría que existe en la tabla del OTRO `kind` (no el del contexto guardado — ej. `c:val` de texto libre contra un contexto `kind="avanzado"`) sigue el mismo camino que categoría inexistente: `EXPLAIN_EXPIRED_MSG`, log sanitizado, sin excepción.
  **Test asociado:** `test_categoria_de_kind_equivocado_responde_vencido` — parametrizado con las 5 categorías de texto libre contra un contexto `avanzado` y las 4 categorías de `avanzado` contra un contexto `texto_libre` (9 combinaciones), cada una responde `EXPLAIN_EXPIRED_MSG` y nunca lanza excepción no capturada.

#### Hallazgo 9 — `ExplanationContext` atado al `chat_id` que lo generó (líneas 435-503 de `security`)
- [ ] **Criterio:** `store.put(...)` en `query_handler._run_analysis` y en `advanced_command.avanzado` incluye el `chat_id` real de la conversación en el `ExplanationContext` guardado.
  **Test asociado:** `test_chat_id_se_persiste_en_explanation_context` — para ambos flujos, verifica que el objeto guardado en el store tiene `chat_id` igual al `chat_id` del `Update` que disparó el análisis.
- [ ] **Criterio (caso sugerido explícitamente por `security`):** un botón de categoría de un análisis ajeno, usado desde otro `chat_id` autorizado, no filtra el análisis equivocado — "botón de categoría mal dirigido al análisis equivocado".
  **Test asociado:** `test_categoria_ajena_no_filtra_analisis_de_otro_chat_id` — fixture con 2 análisis reales en el store: `chat_id=A` analiza AAPL (`context_id=X`, `kind="texto_libre"`), `chat_id=B` analiza MSFT (`context_id=Y`). Desde `chat_id=B` se manda `xp:X:c:val` (la categoría de Valoración del análisis de A, sintácticamente válida y existente) — el test confirma que la respuesta es `EXPLAIN_EXPIRED_MSG`, que NO aparecen en el mensaje el ticker, veredicto ni ningún botón de Nivel 2 de AAPL, y que el `context_id` de B (`Y`) sigue funcionando con normalidad para B en el mismo test. Se repite el mismo caso para las 3 formas de `callback_data` (`:m`, `:c:{cat}`, `:{code}`), tal como pide security en su remediación.
- [ ] **Criterio:** el mensaje y el nivel/contenido del log para "`context_id` de otro `chat_id`" son indistinguibles de "`context_id` vencido/inexistente" — no debe poder inferirse por la respuesta si el `context_id` probado existe pero pertenece a otro usuario (protección contra side-channel, explícitamente pedida por `security`, líneas 484-493 y 498).
  **Test asociado:** `test_mensaje_vencido_indistinguible_de_ajeno` — compara byte a byte el mensaje enviado al usuario y el string de formato del log en 2 escenarios (`context_id` inexistente vs. `context_id` existente de otro `chat_id`) — deben coincidir exactamente en ambos.

### 3. Fixtures / mocks mínimos requeridos (a crear en `tests/conftest.py` o equivalente si no existen ya)

- `explanation_context_texto_libre_full` — `ExplanationContext(kind="texto_libre", chat_id=..., ...)` con los 12 campos nuevos poblados con datos realistas (`ratios`, `risk_fit`, `momentum`, `peer_comparison`, `extras`, `vix`, `corporate_events` no vacío, `treasury_source`, 3 strings de fuente, `peers_note`) — variante base para las 13 combinaciones de texto libre.
- `explanation_context_texto_libre_sin_eventos` — igual que la anterior pero `corporate_events=[]`, para el AC "sin eventos corporativos, la categoría `rie` no incluye `evt`".
- `explanation_context_avanzado_asset_light` — `kind="avanzado"`, `altman_pp` no `None`, `roe`/`gross_margin`/`beta` poblados — para el AC "ticker asset-light muestra `alz` + `azp`".
- `explanation_context_avanzado_no_asset_light` — igual pero `altman_pp is None` — para el AC "ticker no asset-light muestra solo `alz`".
- `explanation_context_dos_chat_ids` — fixture compuesta con 2 `ExplanationContext` en el mismo store, `chat_id` distintos (A y B), tickers distintos — usada por los tests del Hallazgo 9.
- `ollama_mock_success` — cliente Ollama simulado que devuelve una respuesta breve válida (2-4 oraciones, sin tokens protegidos nuevos) — ya debería existir de la spec cerrada, reutilizar.
- `ollama_mock_hallucinated` — parametrizado, devuelve una respuesta que introduce un número o ticker nuevo no presente en `datos_del_contexto` — usado por los 12 tests nuevos de guard.
- `ollama_config_disabled` — `clients.ollama_config.enabled=False` — dispara el camino D3 (reporte completo de fallback, sin acortar, sin botones) en AMBOS flujos. Distinto de una llamada individual fallida: no es "Ollama caído a mitad de una pregunta puntual" (eso ya lo cubre el mecanismo de fallback existente por leaf, sin cambios en esta spec) — es la configuración apagada desde el arranque, que es el escenario real que D3 resuelve.
- `rate_limiter_agotado` — `InMemoryRateLimiter` con el balde de un `chat_id` ya en 0 — usado para los AC de rate limiting selectivo (`evt`/`inf`/`:m`/`:c:{cat}` deben seguir respondiendo; un leaf con Ollama debe seguir bloqueado).
- `callback_query_factory` — helper que arma un `Update`/`CallbackQuery` falso dado un `callback_data` string y un `chat_id`, para no repetir el boilerplate de `python-telegram-bot` en cada test de dispatch (recorridos de navegación, casos negativos, casos del Hallazgo 9).
- Fixtures para D1/D2/D3 específicamente:
  - **D1** (teaser 1 línea): `scenarios_completos` (3 modelos calculables) y `scenarios_parcial` (1-2 modelos calculables, para el caso límite de `build_valor_justo_teaser_line` con escenario no calculable).
  - **D2** (`rsk` detrás de botón): `risk_fit_dict_poblado` — usado para confirmar que "Encaje con tu perfil" NO aparece en `build_summary_parts_short` y SÍ aparece solo al tocar `rsk`.
  - **D3** (Ollama deshabilitado = reporte completo): snapshot/fixture congelada del output actual de `build_summary_parts`/`_build_message` (pre-spec) para el test de regresión byte-idéntica.

### 4. Fuera de alcance de esta ronda de QA (explícito, mismo criterio ya usado en la spec cerrada anterior)

- **Sin E2E contra Ollama real** — ninguna prueba levanta un servicio Ollama real ni depende de su disponibilidad de red; todo pasa por `ollama_mock_success`/`ollama_mock_hallucinated`/`ollama_config_disabled`.
- **Sin E2E contra FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR reales** — la spec ya garantiza 0 llamadas HTTP nuevas; los tests nuevos consumen los mismos fixtures de datos de FMP que ya existen en el proyecto para `query_handler`/`advanced_command`, sin tocar red.
- **Sin pruebas de carga/performance** — el AC de rate limiting se valida funcionalmente (balde agotado → sigue/no sigue respondiendo), no bajo concurrencia real ni volumen. El argumento de `architect`/`security` de que la navegación es O(1) en memoria se toma como válido por lectura de código, no se mide con throughput real.
- **Sin prueba de la configuración multi-`chat_id` en producción real** — el Hallazgo 9 se valida con fixtures en proceso que simulan 2 `chat_id` autorizados (`explanation_context_dos_chat_ids`), no con 2 cuentas reales de Telegram ni con `TELEGRAM_ALLOWED_CHAT_ID` configurado en un entorno real.
- **Sin pruebas visuales de renderizado en el cliente de Telegram** — se verifica la estructura del teclado (`InlineKeyboardMarkup`, `callback_data` de cada botón) del lado del servidor, nunca cómo se ve en la app de Telegram.
- **Sin fuzzing/mutation testing del regex más allá de los casos negativos ya enumerados** (`test_callback_data_malformado_negativo`) — exploratorio si sobra tiempo, no forma parte de la suite automatizada obligatoria de este run.

### Testabilidad
- [ ] `ExplanationContext`/`ExplanationContextStore` siguen siendo 100% en memoria e inyectables por constructor — sin cambios de este criterio respecto a la spec cerrada.
- [ ] El nuevo campo `chat_id` en `ExplanationContext` es un dato de entrada explícito al fabricar el fixture en tests (no se infiere de un side effect global) — permite construir los 2 análisis de `explanation_context_dos_chat_ids` sin pasar por un `Update` real.
- [ ] Las tablas `FORMULAS`/`FUENTES`/`CATEGORIES_TEXTO_LIBRE`/`CATEGORIES_AVANZADO` son diccionarios/estructuras de datos importables directamente en el test, sin necesidad de instanciar el handler completo para verificarlas.
- [ ] `build_summary_parts_short`/`build_valor_justo_teaser_line` son funciones puras (dict/objeto de entrada → string), sin I/O ni side effects — testeables por unit test directo, sin mocks de red.

### Criterio de exit de QA
- Todos los tests pasan (`pytest -q` en verde), incluida la suite completa existente (`test_ai_explain.py`/`test_query_handler.py`/`test_advanced_command.py`/`test_summary.py`).
- Sin tests ignorados, comentados ni marcados `xfail` para pasar CI.
- Flaky rate = 0 en la nueva suite (los 27 leaves + 3 formas de navegación + Hallazgo 9 se corren 100% mockeados, sin dependencia de red ni de timing real — no debería haber flakiness).
- Cobertura mínima de la sección "Cobertura mínima requerida" arriba, verificada con reporte de `coverage.py` (no estimada).

### ¿Lista para pasar a `implementer`?
**Sí, con una condición de forma, no de fondo:** los 2 criterios del checklist de cierre de `security` (categoría de tabla equivocada, `chat_id` ownership del Hallazgo 9) ya quedaron formalizados arriba como criterios de aceptación con test asociado — `implementer` los recibe junto con el resto, no como una deuda separada. No hay bloqueantes de testabilidad: todo el código nuevo son funciones puras o dispatch sobre objetos ya inyectables, sin acoplamiento duro ni side effects enterrados en estático. Se puede pasar a `implementer` con esta spec tal como queda tras este bloque.
