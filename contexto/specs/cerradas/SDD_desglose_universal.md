# Spec: Desglose término por término — extensión universal a las 12 preguntas restantes

**Rol:** `architect` (spec base — extiende el mecanismo genérico ya cerrado e implementado en
`SDD_desglose_terminos_formula.md` + `SDD_desglose_con_valores_reales.md` +
`SDD_desglose_valor_justo_total.md`, hoy en producción para 8 de 27 preguntas: `alz`, `azp`, `pir`,
`pia`, `pie`, `mgr`, `mge` en `/avanzado`, y `vf` en texto libre).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa (mismo guard de integridad, mismo criterio de "nunca antes de
Ollama" — sin cambios de mecanismo, pero 1 pregunta expone un campo de FMP nuevo al desglose:
ver Grupo F). `qa` agrega criterios de cobertura para las 12 preguntas nuevas. `dba`/`frontend`
no aplican.
**Estado:** spec nueva, lista para `security` → `qa` → `implementer`.

---

## Contexto

Daniela pidió que **todas** las preguntas con fórmula propia del bot (no solo las 8 que ya tienen
"🔍 Desglose" hoy) muestren el mismo nivel de detalle: valores reales del ticker sustituidos
término por término, de dónde sale cada uno, y qué mide — igual que ya se ve en Altman/Piotroski/
Magic Formula/Valor Justo Total.

### Alcance confirmado contra el código real (hoy, línea por línea)

**Ya tienen Desglose (no se tocan en esta spec):**
`DESGLOSE_AVANZADO` (`ai_explain_content.py:546-667`) → `alz`, `azp`, `pir`, `pia`, `pie`, `mgr`,
`mge`. `DESGLOSE_TEXTO_LIBRE` (`ai_explain_content.py:670-700`) → `vf` (único).

**Faltan — 12 preguntas, todas con `variant="dato_y_paso_a_paso"` (`ai_explain_content.py`,
confirmado línea por línea contra `QUESTIONS_TEXTO_LIBRE`/`QUESTIONS_AVANZADO`):**

| Grupo | Preguntas | `kind` |
|---|---|---|
| A — Valoración con cuenta ya resuelta | `gra`, `dcf`, `mul` | texto_libre |
| B — Ratios y pilares | `rat`, `pil` | texto_libre |
| C — Riesgo y mercado | `rsk`, `mom`, `cmp` | texto_libre |
| D — Veredicto (caso especial, decisión con criterio) | `ver` | texto_libre |
| E — Factores AQR con dato ya expuesto | `aqv`, `aqq`, `aql` | avanzado |
| F — Factor Momentum AQR (dato NUEVO a exponer) | `aqm` | avanzado |

**Siguen sin Desglose, por diseño, sin cambios en esta spec:** `mod`, `ben`, `ren` (variant
`narrativa` — sin fórmula propia del bot detrás, confirmado en `QUESTIONS_TEXTO_LIBRE`/
`QUESTIONS_AVANZADO`), `evt`/`inf` (variant `deterministico`, contenido fijo sin fórmula), `pig`
(se apoya en `pir`/`pia`/`pie`, decisión ya cerrada en la spec original, sin cambios).

### Hallazgo central: 11 de las 12 preguntas ya tienen su función de Cuenta completa y correcta

Confirmado línea por línea en `ai_explain.py`: **todas** las 12 preguntas nuevas ya están
registradas en `_CUENTA_TEXTO_LIBRE` (línea 1172-1176: `gra`, `dcf`, `mul`, `rat`, `pil`, `rsk`,
`mom`, `cmp`) o `_CUENTA_AVANZADO` (línea 1378-1383: `aqv`, `aqq`, `aqm`, `aql`) — sus funciones
`_cuenta_gra`/`_cuenta_dcf`/`_cuenta_mul`/`_cuenta_rat`/`_cuenta_pil`/`_cuenta_rsk`/`_cuenta_mom`/
`_cuenta_cmp`/`_cuenta_aqv`/`_cuenta_aqq`/`_cuenta_aql` (líneas 1034-1370) **ya sustituyen valores
reales del ticker en la fórmula** — el botón "🎓 Explicame paso a paso" de estas 11 preguntas ya
muestra la cuenta resuelta hoy. Lo único que falta es la sección "🔍 Desglose" (texto fijo de qué
es/qué mide cada término, agregada DESPUÉS de la Cuenta).

**Única excepción: `_cuenta_aqm` (línea 1363-1367) existe pero es trivial — `f"Factor Momentum:
{momentum}"`, sin ningún valor real sustituido** (solo repite la etiqueta cualitativa que ya
muestra "📌 Dato"). Esta es la única de las 12 preguntas que necesita, además del Desglose, una
Cuenta nueva de verdad — ver Grupo F.

---

## Mecanismo reutilizado (sin cambios de diseño — ya cerrado y en producción)

- `DesgloseTermino` (dataclass, `ai_explain_content.py:501-506`) — `letra`, `campo_origen`,
  `nombre`, `que_mide`. Reutilizada tal cual para las 12 preguntas nuevas.
- `desglose(kind, code)` (`ai_explain_content.py:703-713`) — ya genérica (`dict.get(code, ())`),
  ya soporta ambas ramas (`avanzado`/`texto_libre`) desde `SDD_desglose_valor_justo_total.md`. No
  cambia.
- `_build_desglose_block(kind, question_code, datos, context=None)` (`ai_explain.py:912-946`) — ya
  genérica: itera `ai_explain_content.desglose(kind, code)`, llama al extractor de
  `_DESGLOSE_VALOR_EXTRACTORS.get(code)` por término, arma `"• {letra} ({nombre}) = {valor} — sale
  de {campo_origen}. {que_mide}."`. No cambia — las 12 preguntas nuevas entran agregando entradas a
  `DESGLOSE_TEXTO_LIBRE`/`DESGLOSE_AVANZADO` y a `_DESGLOSE_VALOR_EXTRACTORS`, sin tocar la función.
- `_enforce_desglose_length` (`_MAX_DESGLOSE_CHARS=1200`) y `_enforce_cuenta_length`
  (`_MAX_CUENTA_CHARS=400`) — sin cambios de valor (ver "Presupuesto de longitud" abajo).
- Guard de integridad / orden Cuenta→Desglose→respuesta de Ollama → Fórmula/Fuente → disclaimer —
  sin cambios; el Desglose se sigue armando DESPUÉS de `_fetch_explanation`, nunca es input suyo.
- `"vf"` es el único caso "especial" (sub-cuentas anidadas, `_build_desglose_vf`) — ninguna de las
  12 preguntas nuevas lo necesita: todas usan el patrón genérico de 1-valor-por-término (mismo que
  Altman/Piotroski/Magic Formula), porque su Cuenta ya es 1 sola línea/bloque autocontenido, no un
  promedio de 3 sub-modelos con cuenta propia cada uno.

---

## Grupo A — Valoración con cuenta ya resuelta: `gra`, `dcf`, `mul`

Todos los términos ya están en `_payload_texto_libre` (`ai_explain.py:346-380`) — cero campos
nuevos.

### `gra` (Graham EPS)

`_cuenta_gra` (línea 1034-1043) ya usa `eps_ttm`, `g_aplicado`, `y_value`, escenario elegido.

```python
DESGLOSE_TEXTO_LIBRE["gra"] = (
    DesgloseTermino("EPS", "eps_ttm — estado de resultados (TTM, calculado por el bot)",
                     "EPS (Ganancia por Acción)", "Cuánto ganó la empresa por cada acción en los últimos 12 meses"),
    DesgloseTermino("g", "g_aplicado del escenario elegido — CAGR histórico de EPS, con techo de 15% (valuation.py)",
                     "Crecimiento aplicado", "Qué tan rápido se espera que crezcan las ganancias por acción, según el historial"),
    DesgloseTermino("Y", "y_value — tasa del bono del Tesoro a 10 años (FRED/Treasury.gov)",
                     "Tasa del bono a 10 años", "El retorno \"sin riesgo\" contra el que se compara la inversión en la acción"),
)
```

Extractor (`_valor_desglose_gra`): `EPS`→`_money(eps_ttm)`, `g`→`_pct1(g_aplicado)`,
`Y`→`_pct1(y_value)`. `None` si el campo falta (mismo criterio que `mgr`/`mge`).

### `dcf` (Flujo de Caja Descontado)

`_cuenta_dcf` (línea 1054-1071) ya usa 6 campos: `dcf_fcf_base`, `dcf_g_fcf`, `dcf_wacc`,
`dcf_valor_presente_flujos`, `dcf_valor_terminal_descontado`, `dcf_equity_value`.

```python
DESGLOSE_TEXTO_LIBRE["dcf"] = (
    DesgloseTermino("FCF base", "dcf_fcf_base — Flujo de Caja Operativo − CapEx (estado de flujo de efectivo)",
                     "Flujo de Caja Libre base", "El efectivo real que le queda a la empresa después de operar e invertir en sí misma, hoy"),
    DesgloseTermino("WACC", "dcf_wacc — costo de capital propio del bot (beta, deuda, tasa impositiva efectiva)",
                     "WACC (costo de capital)", "La tasa a la que se \"descuentan\" los flujos futuros para traerlos a valor de hoy"),
    DesgloseTermino("g", "dcf_g_fcf — crecimiento proyectado del FCF a 5 años",
                     "Crecimiento proyectado del FCF", "Qué tan rápido se espera que crezca ese flujo de caja libre en los próximos años"),
    DesgloseTermino("Valor presente de los flujos", "dcf_valor_presente_flujos — suma de los 5 años proyectados, descontados al WACC",
                     "Valor presente de los flujos", "Cuánto valen hoy los flujos de caja que se esperan durante los próximos 5 años"),
    DesgloseTermino("Valor terminal descontado", "dcf_valor_terminal_descontado — valor de la empresa más allá del año 5, descontado al WACC",
                     "Valor terminal descontado", "Cuánto vale hoy todo lo que la empresa va a generar después del año 5"),
    DesgloseTermino("Valor de la empresa", "dcf_equity_value — suma de los dos anteriores",
                     "Valor de la empresa (equity)", "El valor total estimado de la empresa hoy, sumando el corto y el largo plazo"),
)
```

Extractor por letra → campo (`_money` en los 4 montos, `_pct1` en WACC/g). 6 términos — ver
presupuesto de longitud abajo (el más largo de las 12 preguntas nuevas).

### `mul` (Múltiplos)

`_cuenta_mul` (línea 1046-1051) ya usa `eps_ttm`, `per_promedio_peers`.

```python
DESGLOSE_TEXTO_LIBRE["mul"] = (
    DesgloseTermino("EPS", "eps_ttm — estado de resultados (TTM, calculado por el bot)",
                     "EPS (Ganancia por Acción)", "Cuánto ganó la empresa por cada acción en los últimos 12 meses"),
    DesgloseTermino("PER promedio peers", "per_promedio_peers — 1/earningsYield de cada peer del sector (/key-metrics de FMP)",
                     "PER promedio de los comparables", "A cuántas veces sus ganancias cotizan, en promedio, empresas parecidas del mismo sector"),
)
```

---

## Grupo B — Ratios y pilares: `rat`, `pil`

### `rat` (Ratios clave)

`_cuenta_rat` (línea 1074-1093) ya arma hasta 4 sub-cuentas independientes (liquidez, margen
bruto, PER, P/S), cada una solo si sus campos están disponibles. El Desglose espeja esos mismos 4
términos, ya expuestos en el payload (`current_assets`, `current_liabilities`, `revenue`,
`cost_of_revenue`, `precio_actual`, `eps_ttm`, `market_cap`) — cero campos nuevos.

```python
DESGLOSE_TEXTO_LIBRE["rat"] = (
    DesgloseTermino("Liquidez", "current_assets / current_liabilities — balance general",
                     "Liquidez corriente", "Si la empresa puede pagar sus deudas de corto plazo con lo que tiene a mano"),
    DesgloseTermino("Margen bruto", "(revenue − cost_of_revenue) / revenue — estado de resultados",
                     "Margen bruto", "Cuánto le queda de cada venta después del costo directo de producir/vender"),
    DesgloseTermino("PER", "precio_actual / eps_ttm — cotización + estado de resultados",
                     "PER (Precio/Ganancia)", "A cuántas veces sus ganancias anuales cotiza la acción hoy"),
    DesgloseTermino("P/S", "market_cap / revenue — cotización + estado de resultados",
                     "P/S (Precio/Ventas)", "A cuántas veces sus ventas anuales está valuada la empresa en bolsa"),
)
```

Extractor: reusa exactamente las mismas condiciones de `_cuenta_rat` (PER omitido si
`per_no_aplicable`, liquidez omitida si `current_liabilities` es 0/`None`) — un término sin dato
disponible se omite de la línea (mismo criterio que el resto: nunca `None` visible).

### `pil` (4 Pilares)

`_cuenta_pil` (línea 1096-1113) ya usa `revenue_reciente/antiguo`, `net_income_reciente/antiguo`,
`ratio_liquidez`, y las 4 banderas booleanas de `pillars`. El pilar "Precio razonable" no tiene un
número propio — es la clasificación barata/cara del escenario Conservador ya calculada en
Valoración (documentado así en `FORMULAS_TEXTO_LIBRE["pil"]`) — su línea de Desglose usa el mismo
formato ✅/❌ que ya usa Piotroski para criterios sin magnitud numérica propia.

```python
DESGLOSE_TEXTO_LIBRE["pil"] = (
    DesgloseTermino("Ingresos crecientes", "revenue_reciente vs. revenue_antiguo — estado de resultados",
                     "Ingresos crecientes", "Si la empresa factura más ahora que al principio de su historial"),
    DesgloseTermino("Utilidades crecientes", "net_income_reciente vs. net_income_antiguo — estado de resultados",
                     "Utilidades crecientes", "Si la empresa gana más plata ahora que al principio de su historial, y no está perdiendo"),
    DesgloseTermino("Deuda controlada", "ratio_liquidez > 1 — balance general",
                     "Deuda controlada", "Si la empresa puede cubrir sus deudas de corto plazo con lo que tiene a mano"),
    DesgloseTermino("Precio razonable", "clasificación barata/cara del escenario Conservador — botón «⚖️ Veredicto»",
                     "Precio razonable", "Si, según el modelo del bot, la acción cotiza por debajo de lo que vale hoy"),
)
```

Extractor: los 3 primeros formatean valores reales (`_money`/`_ratio2`) igual que `_cuenta_pil`;
"Precio razonable" usa `✅ Cumple`/`❌ No cumple` desde `pillars.get("precio_razonable")` (mismo
patrón que `_valor_desglose_piotroski`).

---

## Grupo C — Riesgo y mercado: `rsk`, `mom`, `cmp`

### `rsk` (Encaje con tu perfil)

`_cuenta_rsk`/`_cuenta_beta_bucket` (línea 1116-1139) ya usan `beta`, `beta_umbral_bajo`,
`beta_umbral_alto`.

```python
DESGLOSE_TEXTO_LIBRE["rsk"] = (
    DesgloseTermino("Beta", "beta — dato de FMP (profile.beta)",
                     "Beta", "Qué tan volátil es la acción comparada con el mercado en general (1.0 = igual de volátil)"),
    DesgloseTermino("Perfil de riesgo", "perfil guardado con /start",
                     "Perfil de riesgo", "El nivel de riesgo que el usuario eligió tolerar al configurar el bot"),
)
```

Extractor: `Beta`→`_ratio2(beta)`; `Perfil de riesgo`→`datos.get("perfil")` tal cual (texto fijo
de FMP no interviene — es un dato propio del usuario, sin riesgo de inyección).

### `mom` (Momentum y volatilidad)

`_cuenta_mom` (línea 1142-1157) ya usa `precio_actual`, `year_high/low`, `price_avg_50/200`,
`pct_vs_*` — los 4 términos ya están en el payload (`ai_explain.py:432-449`), cero campos nuevos.

```python
DESGLOSE_TEXTO_LIBRE["mom"] = (
    DesgloseTermino("vs. máx. 52 semanas", "precio_actual vs. year_high — cotización (quote) de FMP",
                     "Precio vs. máximo anual", "Qué tan lejos está el precio de hoy de su punto más alto en el último año"),
    DesgloseTermino("vs. mín. 52 semanas", "precio_actual vs. year_low — cotización (quote) de FMP",
                     "Precio vs. mínimo anual", "Qué tan lejos está el precio de hoy de su punto más bajo en el último año"),
    DesgloseTermino("vs. promedio 50 días", "precio_actual vs. price_avg_50 — cotización (quote) de FMP",
                     "Precio vs. promedio de 50 días", "Cómo está el precio de hoy respecto a su tendencia de corto plazo"),
    DesgloseTermino("vs. promedio 200 días", "precio_actual vs. price_avg_200 — cotización (quote) de FMP",
                     "Precio vs. promedio de 200 días", "Cómo está el precio de hoy respecto a su tendencia de largo plazo"),
)
```

Extractor: cada letra → `_pct1(pct_vs_*)` ya calculado en el payload (no se recalcula).

### `cmp` (Comparables del sector)

`_cuenta_cmp` (línea 1160-1169) ya usa `precio_actual`, `eps_ttm`, `per_propio`,
`per_promedio_peers`. **Decisión de diseño**: el Desglose NO enumera `peers_usados` individualmente
(puede ser una lista larga y de longitud variable, dato de terceros de FMP) — usa 2 términos fijos
(PER propio, PER promedio de peers), igual que la Cuenta ya hace hoy. Esto mantiene el bloque de
longitud acotada sin importar cuántos peers tenga el sector del ticker.

```python
DESGLOSE_TEXTO_LIBRE["cmp"] = (
    DesgloseTermino("PER propio", "precio_actual / eps_ttm — cotización + estado de resultados",
                     "PER propio", "A cuántas veces sus ganancias anuales cotiza esta acción hoy"),
    DesgloseTermino("PER promedio peers", "per_promedio_peers — 1/earningsYield de cada peer (/key-metrics de FMP)",
                     "PER promedio de los comparables", "A cuántas veces sus ganancias cotizan, en promedio, empresas parecidas del mismo sector"),
)
```

---

## Grupo D — `ver` (⚖️ Veredicto): desglose liviano, con criterio documentado

**Decisión de diseño (a discreción de `architect`, no requiere a Daniela)**: `ver` recibe un
Desglose de 2 términos (Precio actual, Valor Justo Total), con el mismo formato "puntero corto"
que ya usan los 3 sub-términos de `vf` (`campo_origen` = referencia al botón que da el detalle
completo) — **no** el mecanismo especial de sub-cuentas anidadas de `_build_desglose_vf`.

**Por qué no el mecanismo de `vf`**: `ver` compara 2 números ya explicados en profundidad en otro
botón (`Precio actual` es autoevidente — una cotización; `Valor Justo Total` ya tiene su propio
Desglose completo de 3 sub-modelos a 1 toque de distancia, botón "💰 Valor Justo Total"). Repetir
las 3 sub-cuentas de Múltiplos/Graham/DCF dentro del Desglose de `ver` sería duplicar contenido que
ya existe, en vez de agregar información nueva — el mismo criterio que ya usa `vf` internamente
para sus propios sub-términos (`campo_origen="fórmula y fuente completas: botón «Múltiplos»"`, no
la fórmula completa repetida).

```python
DESGLOSE_TEXTO_LIBRE["ver"] = (
    DesgloseTermino("Precio actual", "precio_actual — cotización (quote) de FMP",
                     "Precio actual", "Lo que cuesta hoy 1 acción de la empresa en el mercado"),
    DesgloseTermino("Valor Justo Total", "cálculo completo: botón «💰 Valor Justo Total»",
                     "Valor Justo Total", "Cuánto debería valer la acción según el promedio de los modelos calculables del bot"),
)
```

Extractor genérico (patrón Altman/Magic Formula, no el especial de `vf`): `Precio actual`→
`_money(precio_actual)`, `Valor Justo Total`→`_money(valor_justo_total)` (mismos campos que ya lee
`_cuenta_ver`, línea 1003-1011).

---

## Grupo E — Factores AQR con dato ya expuesto: `aqv`, `aqq`, `aql`

### `aqv` (Value)

`_cuenta_aqv` (línea 1311-1322) ya usa `earnings_yield`, `umbral_alto`, `umbral_bajo`.

```python
DESGLOSE_AVANZADO["aqv"] = (
    DesgloseTermino("Earnings Yield", "magic.earnings_yield — mismo cálculo que «Earnings Yield» de la Magic Formula (EBIT/EV)",
                     "Earnings Yield", "Cuánta ganancia operativa genera la empresa por cada dólar de su valor total (deuda incluida)"),
    DesgloseTermino("Umbrales", "advanced_scoring.FACTOR_UMBRALES[\"value_earnings_yield\"]",
                     "Umbrales de clasificación", "Los cortes fijos que definen si el Earnings Yield es alto, medio o bajo"),
)
```

Extractor: `Earnings Yield`→`_pct1(earnings_yield)`; `Umbrales`→string ya armado por
`_cuenta_aqv`'s lógica de rango (`"> {alto}"`/`"< {bajo}"`/`"entre {bajo} y {alto}"`) — reusa la
misma función interna, no la reescribe.

### `aqq` (Quality)

`_cuenta_aqq` (línea 1335-1360) ya usa 3 sub-métricas con sus 3 pares de umbrales — el más
detallado de los 3.

```python
DESGLOSE_AVANZADO["aqq"] = (
    DesgloseTermino("ROE", "roe — /key-metrics de FMP",
                     "ROE (Retorno sobre el Patrimonio)", "Cuánta ganancia genera la empresa por cada dólar que pusieron sus dueños"),
    DesgloseTermino("Margen bruto", "gross_margin — estado de resultados",
                     "Margen bruto", "Cuánto le queda de cada venta después del costo directo de producir/vender"),
    DesgloseTermino("Ratio de Piotroski", "piotroski_ratio — criterios cumplidos / evaluables del F-Score",
                     "Ratio de Piotroski", "Qué proporción de los criterios de calidad del F-Score cumplió la empresa"),
)
```

Extractor: cada letra → `_pct1(valor)` ya presente en el payload (`roe`, `gross_margin`,
`piotroski_ratio`, calculado en `_payload_avanzado` línea 552-556).

### `aql` (Low-vol)

`_cuenta_aql`/`_cuenta_beta_bucket` (línea 1370-1375) ya usan `beta`, `beta_umbral_bajo/alto`.

```python
DESGLOSE_AVANZADO["aql"] = (
    DesgloseTermino("Beta", "beta — dato de FMP (profile.beta)",
                     "Beta", "Qué tan volátil es la acción comparada con el mercado en general (1.0 = igual de volátil)"),
)
```

1 solo término — Low-vol es puramente función de beta, sin sub-métricas combinadas (a diferencia
de Quality). Extractor: `_ratio2(beta)`.

---

## Grupo F — `aqm` (Momentum AQR): único caso que expone un dato nuevo

### El hallazgo (mismo patrón que Magic Formula en la spec original)

`_payload_avanzado["aqm"]` (`ai_explain.py:572-574`) hoy expone **solo** la etiqueta cualitativa:

```python
if question_code == "aqm":
    factors = context.factors or {}
    return {"modelo": "Factor Momentum (AQR)", "momentum": factors.get("momentum")}
```

`_cuenta_aqm` (línea 1363-1367) por lo tanto no puede mostrar ningún valor real — es la única de
las 12 preguntas nuevas sin datos numéricos ya disponibles en su `datos`.

**El dato SÍ se calcula hoy**, solo que se descarta antes de llegar a `explain_context_sink`:
`advanced_command.py:197-203` calcula `momentum_result = market_context.calculate_momentum(...)`
completo (con `pct_vs_year_high/low`, `pct_vs_avg_50/200`, `etiqueta`), pero
`calculate_factor_score` (línea 218-226) solo consume `momentum_result.etiqueta` — el resto se
pierde. **Confirmado por `advanced_scoring.calculate_factor_score` (línea 685)**: la etiqueta
`alto`/`medio`/`bajo` de Momentum depende ÚNICAMENTE de si el precio está por encima/debajo de
`price_avg_50` **y** `price_avg_200` simultáneamente (`market_context.calculate_momentum`, línea
113-120) — `year_high`/`year_low` NO participan de esta clasificación (a diferencia de `mom` en
texto libre, que sí los muestra como contexto adicional). El Desglose de `aqm` se limita entonces a
los 3 valores que SÍ determinan la etiqueta: precio actual, promedio 50 días, promedio 200 días —
mostrar `year_high`/`year_low` acá sería ruido no relacionado con esta clasificación puntual.

### Solución: reutilizar campos genéricos que `ExplanationContext` YA tiene (cero campos nuevos en el dataclass)

`ExplanationContext` ya declara `momentum`, `precio_actual`, `year_high`, `year_low`,
`price_avg_50`, `price_avg_200` (línea 113-147) — **campos genéricos, no exclusivos de
`kind="texto_libre"`**, simplemente nunca poblados por `advanced_command.py` hoy (confirmado:
la construcción de `ExplanationContext(kind="avanzado", ...)` en `advanced_command.py:464-481`
no los pasa). A diferencia de la extensión de Magic Formula (que sí necesitó 2 campos nuevos en
`MagicFormulaResult`/`advanced_scoring.py`), acá **no se agrega ningún campo nuevo a ningún
dataclass** — solo se pasan 2 argumentos más, ya calculados, a una llamada que ya existe:

```python
# advanced_command.py:197-203 — ya calculado, sin cambios
momentum_result = market_context.calculate_momentum(
    price=price or 0.0, year_high=quote.get("yearHigh"), year_low=quote.get("yearLow"),
    price_avg_50=quote.get("priceAvg50"), price_avg_200=quote.get("priceAvg200"),
)
```

**Cambio 1 — `explain_context_sink.update(...)` (línea 241-256), agregar:**

```python
momentum={
    "pct_vs_year_high": momentum_result.pct_vs_year_high,
    "pct_vs_year_low": momentum_result.pct_vs_year_low,
    "pct_vs_avg_50": momentum_result.pct_vs_avg_50,
    "pct_vs_avg_200": momentum_result.pct_vs_avg_200,
    "etiqueta": momentum_result.etiqueta,
},  # mismo dict que ya arma query_handler.py:531-536 para texto libre — mismo shape, sin duplicar lógica nueva
precio_actual=price,
year_high=quote.get("yearHigh"),
year_low=quote.get("yearLow"),
price_avg_50=quote.get("priceAvg50"),
price_avg_200=quote.get("priceAvg200"),
```

**Cambio 2 — `ExplanationContext(kind="avanzado", ...)` (línea 464-481), agregar:**

```python
momentum=explain_context_sink["momentum"],
precio_actual=explain_context_sink["precio_actual"],
year_high=explain_context_sink["year_high"],
year_low=explain_context_sink["year_low"],
price_avg_50=explain_context_sink["price_avg_50"],
price_avg_200=explain_context_sink["price_avg_200"],
```

**Cambio 3 — `_payload_avanzado["aqm"]` (`ai_explain.py:572-574`), reemplazar:**

```python
if question_code == "aqm":
    factors = context.factors or {}
    return {
        "modelo": "Factor Momentum (AQR)",
        "momentum": factors.get("momentum"),
        "precio_actual": context.precio_actual,
        "price_avg_50": context.price_avg_50,
        "price_avg_200": context.price_avg_200,
    }
```

Superficie mínima (Decisión de diseño #11 original, sin aflojar): solo los 3 campos que la
clasificación usa — no se agregan `year_high`/`year_low`/`pct_vs_*` al payload que ve Ollama, esos
solo viven en `context.momentum`/`context.year_high`/etc. para uso interno de la Cuenta/Desglose
si hiciera falta (no hace falta acá, ver extractor abajo).

**Cambio 4 — `_cuenta_aqm` (línea 1363-1367), reemplazar la versión trivial:**

```python
def _cuenta_aqm(datos: dict) -> Optional[str]:
    precio = datos.get("precio_actual")
    avg50, avg200 = datos.get("price_avg_50"), datos.get("price_avg_200")
    etiqueta = datos.get("momentum")
    if None in (precio, avg50, avg200):
        return None
    cmp50 = ">" if precio > avg50 else "<"
    cmp200 = ">" if precio > avg200 else "<"
    return (
        f"Precio {_money(precio)} {cmp50} promedio 50d {_money(avg50)} y "
        f"{cmp200} promedio 200d {_money(avg200)} → {etiqueta}"
    )
```

Mismo criterio de "no calculable" que el resto (`None` si falta cualquiera de los 3 campos —
coincide exactamente con `market_context.calculate_momentum`, que da `etiqueta="no_disponible"` en
ese mismo caso).

**Cambio 5 — `DESGLOSE_AVANZADO["aqm"]` (nueva entrada):**

```python
DESGLOSE_AVANZADO["aqm"] = (
    DesgloseTermino("vs. promedio 50 días", "precio_actual vs. price_avg_50 — cotización (quote) de FMP",
                     "Precio vs. promedio de 50 días", "Cómo está el precio de hoy respecto a su tendencia de corto plazo"),
    DesgloseTermino("vs. promedio 200 días", "precio_actual vs. price_avg_200 — cotización (quote) de FMP",
                     "Precio vs. promedio de 200 días", "Cómo está el precio de hoy respecto a su tendencia de largo plazo"),
)
```

Extractor (`_valor_desglose_aqm`): calcula el mismo `_pct1` que ya usa `mom` de texto libre, a
partir de `precio_actual`/`price_avg_50`/`price_avg_200` — no reutiliza `context.momentum` (ese
dict trae `pct_vs_year_high/low` que no aplican acá, ver justificación arriba) — calcula
directamente `(precio - avg) / avg` con los 3 campos ya en `datos`, mismo patrón que
`market_context._pct_vs` pero sin importar ese módulo (evita acoplar `ai_explain.py` a
`market_context.py` por 1 fórmula de 1 línea ya replicada en `_cuenta_mom`).

---

## Presupuesto de longitud — peor caso por pregunta

Mismo mecanismo de enforcement ya validado en producción (`_MAX_CUENTA_CHARS=400`,
`_MAX_DESGLOSE_CHARS=1200`, `_MAX_EXPLANATION_CHARS=480` para la respuesta de Ollama) — estos 3
topes **no cambian de valor** en esta spec, y por diseño acotan el mensaje completo sin importar el
contenido de las tablas nuevas, siempre que cada bloque individual quede debajo de su tope (lo que
se confirma abajo).

| Pregunta | # términos Desglose | Estimado Desglose (peor caso) | Bajo `_MAX_DESGLOSE_CHARS=1200` |
|---|---|---|---|
| `gra` | 3 | ~330 car. | Sí, amplio margen |
| `dcf` | 6 | ~650 car. (la más larga — 6 líneas, `que_mide` de hasta 90 car. c/u) | Sí, margen ~45% |
| `mul` | 2 | ~230 car. | Sí |
| `rat` | 4 | ~420 car. | Sí |
| `pil` | 4 | ~430 car. | Sí |
| `rsk` | 2 | ~230 car. | Sí |
| `mom` | 4 | ~440 car. | Sí |
| `cmp` | 2 | ~230 car. (peers NO enumerados — ver Grupo C) | Sí |
| `ver` | 2 | ~220 car. | Sí |
| `aqv` | 2 | ~250 car. | Sí |
| `aqq` | 3 | ~340 car. | Sí |
| `aqm` | 2 | ~240 car. | Sí |
| `aql` | 1 | ~150 car. | Sí |

Estimado a partir del mismo patrón de línea que ya mide `mge` en producción (`_DESGLOSE_ALTMAN_A_D`
+ `mge`, 4 términos, ~600 car. medido en la spec original) — ninguna tabla nueva supera los 6
términos de `dcf`, y ninguna `que_mide` individual supera las ~100 car. ya usadas en las tablas
existentes.

**Mensaje completo, peor caso (`dcf`, el más largo):** header transparencia (~75) + Dato (~90) +
Cuenta de `dcf` (tope duro 400, ya validado — es la Cuenta más larga existente, sin cambios en esta
spec) + Desglose (~650, peor caso) + respuesta de Ollama (tope duro 480) + Fórmula/Fuente de `dcf`
(~330, `FUENTES_TEXTO_LIBRE["dcf"]` ya existe) + disclaimer (~150) + separadores (~7×2=14) ≈ **~2189
caracteres** — muy por debajo del límite de Telegram (4096), con margen mayor al 45%, en línea con
el margen ya confirmado para `vf` (~2100 car. en su peor caso, spec cerrada). Ninguna de las 12
preguntas nuevas se acerca al límite: `dcf` es el peor caso por ser la única con 6 términos y una
Cuenta ya larga de por sí.

**`rat`/`cmp` en particular (atención pedida por Daniela)**: `rat` tiene 4 términos fijos (no
crece con datos externos) y `cmp` tiene solo 2 (los peers individuales NO se enumeran en el
Desglose por diseño — Grupo C) — ninguno de los dos escala con la cantidad de peers/ratios
disponibles del ticker, a diferencia de la Cuenta de `cmp` que sí podría (pero no lo hace hoy:
`_cuenta_cmp` también se limita a PER propio + PER promedio, sin listar peers individuales).

---

## Decisiones abiertas para Daniela

Ninguna genuina de negocio. Los 2 puntos con criterio propio (`ver` sin sub-cuentas anidadas,
`cmp` sin enumerar peers individuales) están documentados y justificados arriba (Grupo D y Grupo
C) con el mismo criterio ya validado en specs anteriores — no requieren su decisión porque son
consistentes con patrones que ella ya aprobó (el puntero corto de `vf`, la Cuenta ya acotada de
`cmp`).

---

## Criterios de aceptación

**Genéricos, aplican a las 12 preguntas:**
- [ ] Cada pregunta tiene una entrada en `DESGLOSE_TEXTO_LIBRE` o `DESGLOSE_AVANZADO`
      (`ai_explain_content.py`) con `DesgloseTermino` según el diseño de su grupo arriba.
- [ ] Cada pregunta tiene su extractor en `_DESGLOSE_VALOR_EXTRACTORS` (`ai_explain.py`) — patrón
      genérico (1 valor por término), salvo que se documente lo contrario.
- [ ] El Desglose se arma DESPUÉS de `_fetch_explanation` (nunca antes, nunca es input de Ollama) —
      mismo mecanismo ya en producción, sin cambios de orden.
- [ ] Un campo faltante para un ticker puntual omite el valor de esa línea (nunca `None` visible,
      nunca inventado) — mismo `try/except` amplio que `_build_cuenta_line`/`_build_desglose_block`
      ya tienen.
- [ ] `_MAX_DESGLOSE_CHARS=1200` y `_MAX_CUENTA_CHARS=400` no cambian de valor; ningún bloque nuevo
      los excede en el peor caso realista (ver tabla de presupuesto).
- [ ] "📊 Ver dato" de las 12 preguntas sigue sin mostrar Cuenta ni Desglose (comportamiento ya
      establecido, sin cambios — el Desglose es exclusivo de "🎓 Explicame paso a paso").
- [ ] Las 15 preguntas sin Desglose (`mod`, `ben`, `ren`, `evt`, `inf`, `pig`, y las 8 ya cerradas
      antes de esta spec) quedan byte-a-byte iguales — cero regresión.
- [ ] Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR.
- [ ] Suite completa de tests existente sigue en verde.

**Específicos de `aqm` (Grupo F, único con dato nuevo):**
- [ ] `explain_context_sink` en `advanced_command.py` incluye `momentum`, `precio_actual`,
      `year_high`, `year_low`, `price_avg_50`, `price_avg_200` con los mismos valores que
      `momentum_result`/`quote`/`price` ya calculados en esa función — cero llamadas HTTP nuevas
      (mismo `quote` ya obtenido, mismo `momentum_result` ya calculado).
- [ ] `ExplanationContext(kind="avanzado", ...)` en `advanced_command.py` pasa esos 6 campos —
      ningún campo nuevo agregado al dataclass `ExplanationContext` (reutiliza los ya existentes).
- [ ] `_cuenta_aqm` deja de ser trivial: para un ticker con `price`/`price_avg_50`/`price_avg_200`
      disponibles, muestra la comparación real (`"Precio $X > promedio 50d $Y y > promedio 200d $Z
      → alto"`, con los símbolos `>`/`<` correctos según el caso real) — sigue devolviendo `None`
      si falta cualquiera de los 3 campos, igual que `market_context.calculate_momentum` da
      `"no_disponible"` en ese mismo caso.
- [ ] El Desglose de `aqm` (2 términos: promedio 50 días, promedio 200 días) usa `precio_actual`/
      `price_avg_50`/`price_avg_200` ya expuestos — no usa `context.momentum["pct_vs_year_high"]`
      ni `"pct_vs_year_low"` (esos no participan de la clasificación de Momentum AQR, ver
      justificación en Grupo F).
- [ ] `_payload_avanzado["aqm"]` expone `precio_actual`/`price_avg_50`/`price_avg_200` además de
      `modelo`/`momentum` — sin agregar `year_high`/`year_low`/`pct_vs_*` al payload que ve Ollama
      (superficie mínima, Decisión de diseño #11 original).
- [ ] `mom` (texto libre) queda byte-a-byte igual — no se modifica `query_handler.py` ni el
      `momentum_dict` que ya arma para ese flujo.

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain_content.py` → agregar 8 entradas a `DESGLOSE_TEXTO_LIBRE` (`gra`,
  `dcf`, `mul`, `rat`, `pil`, `rsk`, `mom`, `cmp`, `ver` — 9 en total) y 4 entradas a
  `DESGLOSE_AVANZADO` (`aqv`, `aqq`, `aqm`, `aql`), con el contenido exacto de cada Grupo arriba.
- `src/investbot/ai_explain.py` →
  - 13 extractores nuevos en `_DESGLOSE_VALOR_EXTRACTORS` (uno por pregunta, patrón genérico salvo
    lo documentado).
  - Reemplazar `_cuenta_aqm` (única Cuenta que cambia de comportamiento — Grupo F, Cambio 4).
  - Actualizar `_payload_avanzado["aqm"]` (Grupo F, Cambio 3).
- `src/investbot/advanced_command.py` → `explain_context_sink.update(...)` y
  `ExplanationContext(kind="avanzado", ...)` agregan los 6 campos del Grupo F, Cambios 1-2. Ningún
  otro cambio en este archivo.
- `tests/test_ai_explain.py` (o equivalente) → tests nuevos por pregunta: Desglose con todos los
  campos disponibles, Desglose con 1+ campos faltantes (omite esa línea, nunca `None`), longitud en
  el peor caso para `dcf` (la más larga), y para `aqm` específicamente: `_cuenta_aqm` con datos
  reales vs. con algún campo faltante, y que el payload de Ollama para `aqm` no cambia de shape
  salvo los 3 campos nuevos documentados.
- `tests/test_advanced_command.py` (o equivalente) → test de que `explain_context_sink` y
  `ExplanationContext(kind="avanzado")` incluyen los 6 campos nuevos del Grupo F con los valores
  correctos, sin llamadas HTTP nuevas.

## Restricciones

- No se modifica ninguna fórmula de `valuation.py`, `advanced_scoring.py`, `risk_fit.py`,
  `market_context.py` — esta spec es 100% de presentación/contenido, igual que las 3 specs de
  Desglose anteriores.
- No se modifica `market_context.calculate_momentum` ni su `MomentumResult` — se reutiliza tal
  cual, ya se calculaba, solo se deja de descartar 2 de sus campos (`pct_vs_avg_50/200` quedan
  disponibles vía el nuevo dict `momentum` de `explain_context_sink`, aunque el extractor de `aqm`
  termine recalculando el mismo cociente directamente desde `precio_actual`/`price_avg_*` — ver
  justificación en Grupo F).
- No se agrega ningún campo nuevo a ningún dataclass (`ExplanationContext`, `MagicFormulaResult`,
  `FactorScoreResult`, `ValuationResult`, etc.) — el único cambio de "dato nuevo expuesto" (`aqm`)
  se resuelve reutilizando campos que `ExplanationContext` ya tenía declarados y sin poblar para
  `kind="avanzado"`.
- No se agrega botón, callback ni pantalla nueva — mismos 2 botones ya existentes ("📊 Ver dato" /
  "🎓 Explicame paso a paso") para las 12 preguntas.
- `_MAX_DESGLOSE_CHARS`, `_MAX_CUENTA_CHARS`, `_MAX_EXPLANATION_CHARS` no cambian de valor.
- `cmp` no enumera `peers_usados` en el Desglose (Grupo C) — si Daniela pidiera eso más adelante,
  es una spec patch separada con el criterio de acotamiento (ej. top-3 peers) explícitamente
  acordado, no algo a decidir en esta spec.
- Sin cambios a `_build_desglose_block`, `_build_leaf_message`, `_build_ver_dato_content`, el orden
  de secciones del mensaje, ni el guard de integridad — todo el mecanismo común queda exactamente
  como en las specs cerradas anteriores.

---

## Revisión de seguridad

**Rol:** `security` — auditoría contra el código real, sin cambios de diseño (extensión aditiva del
patrón ya validado 4 veces en `SDD_desglose_terminos_formula.md`,
`SDD_desglose_con_valores_reales.md` y `SDD_desglose_valor_justo_total.md`).

**Verificado línea por línea contra el código actual (pre-implementación):**

1. **Nunca input de Ollama.** Confirmado en `ai_explain.py`: `_fetch_explanation` se llama en la
   línea 2002 y `_build_desglose_block` recién en la línea 2020, después — mismo orden ya auditado.
   `_build_desglose_block` (línea 912-946) no hace I/O; itera texto fijo de
   `ai_explain_content.desglose(kind, code)` y le pega un valor puntual con un extractor de
   `_DESGLOSE_VALOR_EXTRACTORS`. Las 12 preguntas nuevas entran por el mismo mecanismo genérico, sin
   tocar la función. Sin hallazgos.

2. **`aqm` — los 6 campos reutilizados son 100% numéricos.** Confirmado en `market_context.py`:
   `MomentumResult` (línea 64-69) son 5 campos `float`/`Optional[float]` + una `etiqueta` de tipo
   enum cerrado (`"impulso_positivo" | "impulso_negativo" | "mixto" | "no_disponible"`, nunca texto
   libre de FMP). Confirmado en `advanced_command.py` (línea 197-203): `momentum_result` sale de
   `quote.get("yearHigh"/"yearLow"/"priceAvg50"/"priceAvg200")` — campos numéricos de `/quote`, ya
   viajan hoy sin problema para `kind="texto_libre"` (mismo dataclass `ExplanationContext`, líneas
   145-148, ya declara `year_high`/`year_low`/`price_avg_50`/`price_avg_200` con ese propósito).
   Ningún campo de texto libre de FMP (`sector`/`industry`, que sí requieren la allow-list GICS
   documentada en el comentario de línea 318-322 de `advanced_command.py`) entra al payload de
   `aqm` — el Cambio 3 de la spec (`_payload_avanzado["aqm"]`) expone solo `momentum`/
   `precio_actual`/`price_avg_50`/`price_avg_200`, los 4 numéricos/enum. Sin hallazgos.

3. **Dato faltante.** `_build_desglose_block` envuelve cada extractor en `try/except Exception` amplio
   (línea 938-941, mismo patrón que `_build_cuenta_line`) y usa `prefijo_valor = f" = {valor}" if
   valor else ""` — un campo faltante omite el número de esa línea sin caerse, nunca imprime `None`
   ni inventa un valor. El diseño de `_cuenta_aqm` (Grupo F, Cambio 4) sigue el mismo criterio:
   devuelve `None` completo si falta cualquiera de los 3 campos, en vez de armar una línea con huecos.
   Consistente con las 11 preguntas restantes (Grupos A-E), que documentan el mismo criterio
   explícitamente. Sin hallazgos.

4. **Presupuesto de longitud.** `_MAX_DESGLOSE_CHARS=1200` y `_MAX_CUENTA_CHARS=400` no cambian de
   valor (confirmado, sin diffs a esas constantes en la spec). El razonamiento de `architect` es
   sólido: `dcf` (6 términos, el peor caso) estima ~650 caracteres de Desglose, muy por debajo del
   tope de 1200, y el mensaje completo peor caso (~2189 caracteres) deja >45% de margen contra el
   límite de Telegram (4096) — mismo margen porcentual ya validado para `vf`. La decisión de no
   enumerar `peers_usados` individualmente en `cmp` (Grupo C) es correcta desde el ángulo de
   seguridad además del de longitud: evita pegar una lista de terceros (FMP) de longitud variable
   directamente en el mensaje sin pasar por sanitización — mantiene la superficie de datos de
   terceros expuestos igual de acotada que hoy. Sin hallazgos.

5. **Texto libre de terceros sin sanitizar.** Ninguna de las 12 preguntas nuevas introduce un campo
   de texto libre de FMP nuevo al payload de Ollama ni al Desglose — todos los `campo_origen`/
   `nombre`/`que_mide` de las tablas `DESGLOSE_TEXTO_LIBRE`/`DESGLOSE_AVANZADO` son texto fijo escrito
   por `architect`, y todos los valores sustituidos vienen de extractores que formatean con `_money`/
   `_pct1`/`_ratio2` (línea 779-790, solo aritmética + formato numérico) o comparan flags booleanos ya
   calculados (`pillars.get(...)`, `per_no_aplicable`). La única excepción documentada — `rsk`, línea
   "Perfil de riesgo" → `datos.get("perfil")` tal cual — no es dato de FMP: es la elección de perfil
   que el propio usuario configuró con `/start` dentro del bot, mismo dato ya mostrado hoy sin
   sanitización adicional en otras partes de la UI del bot. Sin hallazgos.

**Conclusión:** sin hallazgos bloqueantes ni menores. Extensión de bajo riesgo, consistente en
diseño y en código real con el patrón ya auditado 4 veces. Aprobado para `qa` → `implementer`.

---

## Handoff → security

### Specs producidas
- Esta spec (`SDD_desglose_universal.md`) — extensión aditiva del mecanismo ya auditado en
  `SDD_desglose_terminos_formula.md` (seguridad sin hallazgos sobre el diseño genérico) y
  `SDD_desglose_valor_justo_total.md`.

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — `qa` agrega cobertura específica por pregunta.

### Decisiones de diseño tomadas (para que `implementer` no las reabra)
1. Las 11 preguntas de los Grupos A-E usan el patrón genérico ya existente (1 valor por término) —
   ninguna necesita el mecanismo especial de sub-cuentas anidadas de `vf`.
2. `ver` recibe un Desglose liviano de 2 términos con formato "puntero corto" (no repite el
   Desglose completo de `vf`) — justificado en Grupo D.
3. `cmp` no enumera peers individuales en el Desglose — justificado en Grupo C.
4. `aqm` es el único caso que expone datos nuevos, resuelto reutilizando campos ya declarados en
   `ExplanationContext` (sin agregar ningún campo a ningún dataclass) — justificado en Grupo F.
5. Puntos de atención para `security`: `aqm` ahora pasa `momentum`/`year_high`/`year_low`/
   `price_avg_50`/`price_avg_200`/`precio_actual` a un `ExplanationContext(kind="avanzado")` — son
   los mismos campos numéricos de `/quote` que ya viajan sin problema para `kind="texto_libre"`
   (ningún dato de FMP de texto libre nuevo, como `sector`/`industry`, entra al payload de `aqm` —
   ver el Cambio 3 del Grupo F, que expone solo 3 de los 6 campos al payload que ve Ollama).

---

## Criterios QA para Spec: Desglose universal — extensión a 12 preguntas [Iter-1]

**Rol:** `qa` — revisión de testabilidad pre-implementación. Verificado contra el código real de
tests (`tests/test_ai_explain.py`, fixtures `_texto_libre_context`/`_avanzado_context`, constantes
`_CODES_CON_DESGLOSE`/`_TODAS_LAS_PREGUNTAS` ya usadas por la suite de las 8 preguntas cerradas) —
no contra la spec en abstracto.

### Tipo de prueba principal

**Unit** (con un tramo de **Integration** acotado). El mecanismo (`_build_desglose_block`,
`_DESGLOSE_VALOR_EXTRACTORS`, `_cuenta_aqm`) es lógica pura de formato sin I/O — unit test es
suficiente y es el mismo nivel ya usado para las 8 preguntas cerradas (`test_ai_explain.py`,
sección "🔍 Desglose"). El único tramo de Integration es Grupo F (`aqm`): que
`explain_context_sink.update(...)` y `ExplanationContext(kind="avanzado", ...)` en
`advanced_command.py` efectivamente propaguen los 6 campos hasta `ai_explain.py` sin llamada HTTP
nueva — eso cruza 2 módulos y ya tiene su propio archivo (`tests/test_advanced_command.py`), como
indica la spec.

### Revisión de "Criterios de aceptación" del architect — 2 huecos, completados abajo

Los 8 criterios genéricos y los 5 específicos de `aqm` ya definidos por `architect` son correctos y
verificables tal cual. Faltaban 2 ángulos que `security` no cubre (no es su rol) y que sí son de
`qa`:

- [ ] **Consistencia Cuenta↔Desglose** (no estaba explícito como criterio, solo como intención en
      "Contexto"): para las 12 preguntas nuevas, el valor que muestra cada línea del Desglose debe
      coincidir exactamente con el valor ya sustituido en la Cuenta de la misma pregunta — mismo
      criterio que ya prueban `test_consistencia_cuenta_y_desglose_*` para `alz`/`azp`/`mgr`/`mge`.
      Sin este criterio, un extractor podría redondear distinto o leer un campo distinto al de
      `_cuenta_*` y generar una contradicción silenciosa dentro del mismo mensaje.
- [ ] **`ver` no debe activar el mecanismo especial de `vf`** (`_build_desglose_vf`): dado que `ver`
      y `vf` conviven en `texto_libre` y `_build_desglose_block` ya tiene una rama de delegación
      específica para `code == "vf"` con `context` (ver `test_build_desglose_block_texto_libre_vf_*`
      en la suite actual), hace falta un criterio explícito de que agregar `"ver"` a
      `DESGLOSE_TEXTO_LIBRE` NO dispara esa rama — sin este criterio, un `if code in (...)` mal
      escrito en la delegación existente podría capturar `ver` por accidente.

### Cobertura mínima requerida

- [ ] Code coverage ≥ 90% en las líneas nuevas de `ai_explain_content.py` (las 13 tablas) y
      `ai_explain.py` (los 13 extractores + `_cuenta_aqm` + `_payload_avanzado["aqm"]`) — riesgo
      Alto (afecta directamente lo que el usuario final lee en cada una de las 12 preguntas, no es
      código cosmético).
- [ ] Branch coverage 100% en `_cuenta_aqm` (las 2 ramas `>`/`<` para 50d y para 200d — 4
      combinaciones posibles) y en cada extractor con lógica condicional propia (`rat`: PER omitido
      si `per_no_aplicable`, liquidez omitida si `current_liabilities` es 0/`None`; `pil`: bandera
      booleana de "Precio razonable").
- [ ] Los 13 criterios de aceptación del `architect` (8 genéricos + 5 de `aqm`) cubiertos por al
      menos un test — ver mapeo test↔criterio en "Casos obligatorios" abajo.

### Cobertura confirmada de las 12 preguntas nuevas

Para cada una de `gra`, `dcf`, `mul`, `rat`, `pil`, `rsk`, `mom`, `cmp`, `ver`, `aqv`, `aqq`, `aql`,
`aqm` (12 codes, agregados a la lista ya parametrizada `_CODES_DATO_Y_PASO_A_PASO` de la suite
actual — no una lista nueva paralela):

- [ ] **Desglose completo con valores reales, parametrizado por code** — extiende
      `test_valor_desglose_alz_termino_a_termino_ejemplo_de_daniela` (patrón ya usado) a las 12
      preguntas: 1 test por pregunta (o 1 parametrizado con `pytest.mark.parametrize("code", [...])`
      cuando el patrón de extracción es genérico) que arma un `_texto_libre_context`/
      `_avanzado_context` con todos los campos presentes, llama `_build_desglose_block(kind, code,
      datos)`, y hace `assert` letra por letra contra el valor formateado esperado (`_money`/`_pct1`/
      `_ratio2` según el término) — no solo `assert terminos` genérico, para no dejar pasar un
      extractor que devuelve el campo equivocado con el formato correcto por casualidad.
- [ ] **Caso de dato faltante, sin "None"** — extiende el barrido ya existente
      `test_build_desglose_block_ningun_none_visible_en_ningun_caso` (actualmente parametrizado sobre
      `_CODES_CON_DESGLOSE`) para incluir las 12 preguntas nuevas, con `_NONE_VISIBLE_RE` ya definido
      en la suite. Además, 1 test específico por pregunta con **exactamente 1 campo faltante** (no
      todos) que confirme que solo esa línea se omite y las demás siguen mostrando su valor real —
      el barrido genérico solo prueba el caso "todo faltante", que es más fácil de pasar por
      accidente (un `try/except` que traga la excepción entera oculta un extractor roto en el caso
      parcial).
- [ ] **Consistencia Cuenta↔Desglose** — 1 test por pregunta (patrón
      `test_consistencia_cuenta_y_desglose_alz_mismo_valor_termino_a_termino`): arma `_cuenta_*` y
      `_build_desglose_block` con el mismo `datos`, confirma que cada valor que aparece en la Cuenta
      aparece igual (mismo formato, mismo redondeo) en la línea correspondiente del Desglose. Crítico
      en `dcf` (6 valores, mayor superficie de desalineación posible) y en `aqm` (Cuenta y Desglose
      comparten literalmente los mismos 3 campos crudos — si divergen es un bug de copy-paste, no de
      lógica).
- [ ] **Caso especial `aqm` — los 6 campos reutilizados**:
  - [ ] `_cuenta_aqm` con `precio_actual`/`price_avg_50`/`price_avg_200` completos → cadena real con
        el símbolo `>`/`<` correcto en ambas comparaciones (test con 4 combinaciones: `>`/`>`,
        `>`/`<`, `<`/`>`, `<`/`<` — branch coverage 100% pedido arriba).
  - [ ] `_cuenta_aqm` con 1 de los 3 campos en `None` (los 3 casos por separado, no solo "todos
        ausentes") → `None` completo, nunca una línea con hueco.
  - [ ] Test de integración (`tests/test_advanced_command.py`) que confirme que
        `explain_context_sink` y `ExplanationContext(kind="avanzado", ...)` llevan los 6 campos con
        el mismo valor que `momentum_result`/`quote`/`price` ya calculados — con `monkeypatch`/mock
        del cliente FMP contando invocaciones, para probar "cero llamadas HTTP nuevas" con evidencia
        y no solo lectura de código.
  - [ ] Test de que `_payload_avanzado["aqm"]` NO incluye `year_high`/`year_low`/`pct_vs_year_high`/
        `pct_vs_year_low` (assert de ausencia de esas 4 keys, no solo presencia de las 3 nuevas) —
        confirma la superficie mínima expuesta a Ollama, mismo criterio que ya prueba
        `test_payload_mgr_mge_solo_contienen_su_metrica` para el caso análogo de Magic Formula.
  - [ ] Regresión: `mom` (texto libre) produce exactamente el mismo `_build_desglose_block` y la
        misma Cuenta antes y después del cambio — mismo `context.momentum` dict, pero `mom` no debe
        verse afectado por que `aqm` ahora también popule campos que `ExplanationContext` ya tenía
        declarados.
- [ ] **`ver` con su desglose liviano de 2 términos**:
  - [ ] Test que confirma que `DESGLOSE_TEXTO_LIBRE["ver"]` tiene exactamente 2
        `DesgloseTermino` (no 3, no un objeto anidado) y que ninguno de los 2 `campo_origen`
        contiene las palabras "escenario"/"conservador"/"agresivo"/"optimista" (que sí aparecen en
        el desglose interno de `vf`) — evita que una futura edición copie por error el patrón de
        sub-cuentas de `vf` dentro de `ver`.
  - [ ] Test que confirma que `_build_desglose_block("texto_libre", "ver", datos, context=...)` NO
        llama `_build_desglose_vf` (mismo patrón que
        `test_build_desglose_block_texto_libre_otro_code_con_context_no_delega`, extendiendo su lista
        de codes probados a incluir `"ver"` explícitamente en vez de confiar en que ya está cubierto
        por "otro code").
- [ ] **"Comparables del sector" (`cmp`) sin listar cada peer individual**:
  - [ ] Test parametrizado con 3 fixtures de `peer_comparison` con distinta cantidad de peers (2, 5,
        20 — usar los mismos peers de ejemplo que ya arma el fixture de `_cuenta_cmp` existente,
        variando solo el largo de la lista) que confirme que `len(_build_desglose_block("texto_libre",
        "cmp", datos))` (o la longitud en caracteres del bloque armado) es **idéntica** en los 3
        casos — el mensaje no crece con la cantidad de comparables. Este es el test explícitamente
        pedido por Daniela ("atención pedida por Daniela" en la spec) y hoy no existe ninguno que lo
        verifique con datos reales, solo la afirmación en prosa de `architect`.
  - [ ] Test que confirma que ningún ticker individual de `peers_usados` (ni su nombre ni su símbolo)
        aparece como substring en el texto del Desglose de `cmp` — no solo que la longitud no cambie,
        sino que efectivamente no se filtra ningún peer por otro extractor mal escrito.
- [ ] **Presupuesto de longitud, peor caso para `dcf`**:
  - [ ] Test que arma el `datos` de `dcf` con los 6 valores en su magnitud más extrema realista
        (montos grandes con muchos dígitos, WACC/g con decimales), llama
        `_build_desglose_block("texto_libre", "dcf", datos)`, y hace `assert len(bloque) <
        ai_explain._MAX_DESGLOSE_CHARS` — no una estimación en comentario como hace hoy la spec
        (~650 caracteres), sino el número real medido, siguiendo el mismo patrón que
        `test_build_desglose_vf_ticker_e_peor_caso_montos_extremos_bajo_el_tope` ya usa para `vf`.
  - [ ] Test de mensaje completo peor caso para `dcf` (header + Dato + Cuenta + Desglose + respuesta
        de Ollama con `_MAX_EXPLANATION_CHARS` al tope + Fórmula/Fuente + disclaimer) con
        `assert len(mensaje) < 4096` (límite duro de Telegram) — el ~2189 caracteres estimado por
        `architect` es una proyección, no un número medido contra código; este test lo convierte en
        evidencia verificable y detecta regresión si alguna constante de longitud cambia en el
        futuro sin que nadie actualice el comentario de la spec.

### Casos obligatorios

- [ ] Happy path: las 12 preguntas nuevas, con "🎓 Explicame paso a paso", muestran Cuenta y
      Desglose en el orden correcto y con valores reales — extensión directa de
      `test_mensaje_paso_a_paso_muestra_cuenta_y_desglose_en_orden` (ya parametrizado por code) a
      las 12 preguntas.
- [ ] Caso límite: valor exactamente en 0 no se confunde con "faltante" — mismo patrón que
      `test_build_desglose_block_caso_limite_cero_real_no_ausente`, extendido a los términos nuevos
      donde 0 es un valor real posible (ej. `pct_vs_avg_50 = 0.0%` en `mom`/`aqm` cuando el precio
      coincide exactamente con el promedio; `g_aplicado = 0%` en `gra` si el CAGR histórico dio
      negativo y se truncó a 0).
- [ ] Caso de error: extractor con dato malformado (tipo incorrecto, string en vez de número) no
      propaga excepción y omite la línea — extensión de
      `test_build_desglose_block_extractor_con_dato_malformado_no_propaga_excepcion` a al menos 1
      extractor nuevo por Grupo (A-F), no los 13 uno por uno (el mecanismo de `try/except` es común
      y ya está probado a nivel de función, no por extractor).
- [ ] Caso de alto riesgo de negocio: **"📊 Ver dato" nunca incluye Desglose para las 12 preguntas
      nuevas** — extensión de `test_ver_dato_nunca_incluye_desglose_ni_para_las_7_preguntas_con_desglose`
      (ya parametrizado) a las 12 preguntas nuevas. Alto riesgo porque es el botón que el usuario
      toca con más frecuencia (respuesta corta), y una regresión acá filtraría contenido pensado
      solo para el botón de detalle.
- [ ] Caso de alto riesgo de negocio: **regresión byte-a-byte de las 15 preguntas sin Desglose**
      (`mod`, `ben`, `ren`, `evt`, `inf`, `pig` + las 8 ya cerradas antes de esta spec) — extensión
      de `test_mensaje_paso_a_paso_sin_desglose_para_las_20_preguntas_regresion` (el nombre del test
      existente dice "20" porque contaba antes de esta spec; al agregar las 12 preguntas nuevas la
      lista de "sin Desglose" baja a 15 — actualizar el nombre/constante, no solo el cuerpo, para que
      no quede un test mal nombrado prometiendo un número que ya no es cierto).

### Testabilidad

- [ ] Los 13 extractores nuevos son funciones puras (`dict -> str | None`), sin I/O ni estado
      compartido — mismo criterio que ya cumplen los extractores existentes, confirmado por
      `test_build_desglose_block_es_funcion_pura_sin_io` (extender su alcance a incluir los codes
      nuevos, no solo los 7 actuales).
- [ ] `_cuenta_aqm` sigue siendo una función pura de `dict -> str | None` — el cambio de Grupo F no
      introduce ningún acceso a `context`/I/O dentro de la función, solo lee del `datos` que ya
      recibe como parámetro (mismo contrato que las demás `_cuenta_*`).
- [ ] Los 6 campos nuevos de `ExplanationContext` (Grupo F) se pueden inyectar directamente vía
      `_avanzado_context(momentum=..., precio_actual=..., ...)` en tests, sin pasar por
      `advanced_command.py` real — confirma que el dataclass sigue siendo mockeable/inyectable para
      los tests unit de `ai_explain.py`, y que solo el test de integración específico
      (`tests/test_advanced_command.py`) necesita el flujo completo con FMP mockeado.

### Fixtures mínimos que faltan (priorizados por riesgo, sin 1 fixture completo redundante por pregunta)

Dado el patrón ya validado y reutilizado (mismo `_texto_libre_context`/`_avanzado_context` con
overrides), **no** hace falta un fixture nuevo completo por cada una de las 12 preguntas — la
mayoría reusa los campos que esos 2 helpers ya exponen (confirmado: `scenarios`, `ratios`,
`risk_fit`, `momentum`, `peer_comparison`, `pillars` ya existen como parámetros de
`_texto_libre_context` según el grep de la suite actual). Los 3 fixtures que sí hacen falta,
priorizados por riesgo real de bug silencioso:

1. **`dcf` — fixture de magnitudes extremas** (Prioridad 1, la más grande de las 12): valores de
   `dcf_fcf_base`/`dcf_valor_presente_flujos`/`dcf_valor_terminal_descontado`/`dcf_equity_value` con
   6+ dígitos y `dcf_wacc`/`dcf_g_fcf` con decimales largos (ej. `0.091234`) — necesario para el
   test de presupuesto de longitud peor caso (ya no alcanza con los valores "típicos" del fixture
   por defecto, que dan ~650 car. estimados pero no fueron elegidos para maximizar longitud).
2. **`aqm` — fixture de las 4 combinaciones `>`/`<`** (Prioridad 2, el dato reexpuesto — mayor
   riesgo de que el Cambio 3/4 del Grupo F quede mal cableado entre `advanced_command.py` y
   `ai_explain.py`): 4 variantes de `_avanzado_context(momentum=..., precio_actual=..., price_avg_50=...,
   price_avg_200=...)` cubriendo precio por encima/debajo de ambos promedios — necesario para el
   branch coverage 100% pedido arriba y para el test de integración con `advanced_command.py`.
3. **`cmp` — fixture de `peer_comparison` con lista de peers variable** (Prioridad 3, la lista
   variable de peers): 3 variantes (2, 5, 20 peers) del mismo `peer_comparison` que ya usa
   `test_cuenta_cmp_per_propio_y_peers`, solo variando el largo de `peers_usados` — necesario para
   el test de longitud constante pedido por Daniela.

Para las 10 preguntas restantes (`gra`, `mul`, `rat`, `pil`, `rsk`, `mom`, `ver`, `aqv`, `aqq`,
`aql`) los fixtures por defecto de `_texto_libre_context()`/`_avanzado_context()` (sin overrides) ya
tienen valores no triviales para todos sus campos (confirmado: `test_cuenta_cmp_per_propio_y_peers`
y `test_cuenta_dcf_wacc_y_g_sustituidos_proyeccion_resumida` ya arman su Cuenta con el fixture por
defecto sin overrides) — alcanza con 1 override puntual por test para el caso "1 campo faltante" de
cada una, sin fixture nuevo dedicado.

### Criterio de exit de QA

- Todos los tests pasan (BUILD SUCCESS / suite verde), incluida la suite completa existente
  (regresión de las 15 preguntas sin Desglose y de las 8 ya cerradas).
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (todos los tests son unit puros sin I/O real, salvo el 1 test de
  integración de `aqm` con FMP mockeado — cero motivo de flakiness ahí tampoco).
- Los 2 tests de longitud (Desglose de `dcf` bajo `_MAX_DESGLOSE_CHARS`, mensaje completo bajo 4096)
  usan `assert len(...) <` contra el número real medido, no una estimación en comentario.

### Qué NO se prueba, y por qué (mismo criterio ya usado en las specs cerradas)

- **No se prueba el contenido exacto de la respuesta de Ollama** para las 12 preguntas nuevas — ya
  cubierto de forma genérica por el guard de integridad existente (Desglose nunca es input de
  Ollama); repetirlo por pregunta sería redundante sin agregar señal.
- **No se prueban las 15 preguntas sin Desglose con un fixture nuevo por pregunta** — alcanza con el
  barrido paramétrico de regresión ya existente (mismo patrón, no se reinventa).
- **No se prueba el valor absoluto de `momentum_result.pct_vs_year_high`/`pct_vs_year_low`** para
  `aqm` — la spec documenta explícitamente que esos 2 campos NO participan del Desglose ni de la
  Cuenta de `aqm` (Grupo F); alcanza con el test de ausencia de esas keys en el payload, no hace
  falta probar su valor.
- **No se prueba con reintentos/timeouts de FMP** para el flujo de `aqm` — la spec es explícita en
  que no hay llamadas HTTP nuevas, solo reutilización de datos ya obtenidos; el único test de
  integración necesario es de propagación de campos, no de resiliencia de red (eso ya está cubierto
  en la suite de `advanced_command.py` para el `quote`/`momentum_result` original, sin cambios acá).
- **No se prueba carga/performance** — el volumen de esta feature es texto fijo + formato numérico
  en memoria, sin nuevas queries ni fan-out; no aplica performance testing (tabla de riesgo del
  skill: "UI cosmético/preferencias → Exploratorio, no automatizar" — acá es aún más liviano, es
  contenido estático).
- **No se prueba accesibilidad/UI** — no hay UI nueva, es texto plano de Telegram; `frontend` no
  aplica (ya lo dice el header de la spec).
- **No se re-verifica la fórmula matemática de Graham/DCF/Múltiplos/etc.** — eso ya está cubierto
  por la suite de `valuation.py`/`advanced_scoring.py`/`risk_fit.py`/`market_context.py`, que esta
  spec no modifica (restricción explícita del `architect`); QA de esta spec cubre presentación, no
  recalcula la lógica de negocio.

### Cobertura del criterio "consistente con su propia Cuenta" — resumen de trazabilidad

| Pregunta | Test de valores reales | Test de dato faltante | Test de consistencia Cuenta↔Desglose | Fixture dedicado |
|---|---|---|---|---|
| `gra` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No (fixture por defecto) |
| `dcf` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | **Sí — magnitudes extremas (P1)** |
| `mul` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `rat` | Sí (parametrizado) | Sí + branch PER/liquidez | Sí | No |
| `pil` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `rsk` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `mom` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `cmp` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | **Sí — lista de peers variable (P3)** |
| `ver` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí + test anti-delegación a `vf` | No |
| `aqv` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `aqq` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `aql` | Sí (parametrizado) | Sí (barrido + 1 campo) | Sí | No |
| `aqm` | Sí (4 combinaciones) | Sí (3 campos por separado) | Sí | **Sí — 4 combinaciones `>`/`<` (P2)** |

**Conclusión QA**: la spec del `architect` es implementable y ya cerró 13/13 criterios de aceptación
verificables (8 genéricos + 5 de `aqm`). Con los 2 criterios agregados arriba (consistencia
Cuenta↔Desglose, no-delegación de `ver` a `vf`) y los 3 fixtures priorizados (`dcf`, `aqm`, `cmp`),
la cobertura queda completa para pasar a `implementer` — sin bloqueantes de testabilidad.
