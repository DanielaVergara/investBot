# Spec: Desglose término por término de la fórmula — Altman Z, Piotroski F-Score, Magic Formula [Iter-1]

**Rol:** `architect` (spec base — construye sobre `SDD_explicacion_paso_a_paso.md`, cerrada e implementada en `main`, más los 3 fixes de producción del 2026-09-03 ya incorporados: labels de botones distinguibles, reintento ante JSON mal formado/eco del payload, y ajuste de `SYSTEM_PROMPT_PASO_A_PASO` para explicar valores clave).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` — esta spec (a) agrega una tabla de texto fijo nueva a `ai_explain_content.py` (mismo patrón de riesgo ya auditado que `FORMULAS_AVANZADO`/`FUENTES_AVANZADO` — sin interpolación de datos de terceros, vale confirmar que se mantiene así), (b) NO cambia ningún contrato de función existente, NO agrega campos nuevos a `ExplanationContext` ni al payload que viaja a Ollama, NO agrega una forma nueva de `callback_data` — superficie de ataque nueva mínima. `qa` agrega criterios de cobertura para los 7 `question_code` nuevos. `dba`/`frontend`/`backend` no aplican (sin persistencia, sin UI web, sin API nueva). No hay scope freeze — `implementer` no toca código hasta que `security` y `qa` agreguen sus criterios.
**Estado:** spec nueva, sin iteraciones previas.

---

## Contexto

Hoy el bloque "🧮 Cuenta" del camino "Explicame paso a paso" muestra la fórmula ya resuelta con los números del ticker, en una sola línea — ejemplo real actual para Altman Z (`_cuenta_alz`, `ai_explain.py:963-974`):

```
Z = 1.2×0.34 + 1.4×0.12 + 3.3×0.18 + 0.6×1.05 + 1.0×0.87 = 0.41 + 0.17 + 0.59 + 0.63 + 1.0 = 2.67
```

Daniela pidió algo más granular: para cada término/letra de la fórmula, un desglose de 3 partes —**de dónde sale el valor** (qué campo/dato lo produjo), **qué es** (nombre real del concepto, ej. "EBITDA"), y **para qué sirve** (qué mide, en lenguaje simple/"dummy", sin jerga). Su ejemplo textual: para "a + b = x", algo como *"a lo saqué de este valor y es EBITDA y sirve para…"*, repetido por cada término.

**Alcance confirmado por Daniela para esta iteración: solo 3 modelos** — Altman Z-Score (+ Z'' si aplica), Piotroski F-Score, y Magic Formula. Las otras 19 preguntas con fórmula (`vf`/`gra`/`dcf`/`mul`/`rat`/`pil`/`rsk`/`mom`/`cmp`/`ver` de texto libre, `aqv`/`aqq`/`aqm`/`aql` de factores AQR en `/avanzado`) quedan fuera — la estructura de datos que propone esta spec es genérica para que extenderlas después sea aditivo (agregar entradas a un dict), no una reescritura.

**Restricción de diseño más importante — por qué esto es determinístico, no generado por Ollama**: el desglose "de dónde sale cada término" es información FACTUAL y ESTRUCTURAL — mapea 1:1 a nombres de campos de FMP/cálculos del bot, ya fijos en el código (`advanced_scoring.py`). Es exactamente el mismo tipo de contenido que el proyecto ya trata como texto fijo (`FORMULAS_AVANZADO`/`FUENTES_AVANZADO`, y la propia "🧮 Cuenta"). Ollama (`qwen2.5:3b-instruct`) ya mostró hoy mismo 3 bugs de formato (JSON mal formado, eco del payload, invención de formato de números) — no es confiable para contenido factual de campos. Ollama sigue aportando la explicación conceptual breve del resultado final (ya cubierta por `SYSTEM_PROMPT_PASO_A_PASO`, sin cambios en esta spec); el desglose término por término NO pasa por Ollama, nunca.

### Código real leído para esta spec

- **`src/investbot/advanced_scoring.py`** (677 líneas, código real, confirmado hoy): `AltmanZResult` (95-109) expone `a, b, c, d, e` (Z'' no usa `e`, queda `None`) — mismos valores usados para sumar `z`, nunca recalculados aparte (comentario de diseño ya en el código, línea 101-104). `calculate_altman_z` (112-157) lee `totalAssets`, `totalLiabilities`, `totalCurrentAssets`, `totalCurrentLiabilities`, `retainedEarnings` del balance; `ebit`, `revenue` del income; `market_cap` del caller. `PiotroskiResult`/`CriterioPiotroski` (213-228) — cada criterio expone `valores: Optional[dict[str, float]]`, las magnitudes reales (`net_income_t`, `cfo_t`, `roa_t`/`roa_t1`, etc.) que determinaron `cumplido`, `None` si no evaluable. `calculate_piotroski_f_score` (324-458) lee `netIncome`, `totalAssets`, `operatingCashFlow`, `longTermDebt`, `totalCurrentAssets`, `totalCurrentLiabilities`, `weightedAverageShsOut`, `grossProfit`, `revenue` (año actual y anterior según el criterio). `MagicFormulaResult` (501-515) expone `ebit`, `capital_invertido`, `ev`, `market_cap`, `total_debt`, `cash` — mismos valores usados para `roic`/`earnings_yield`. `calculate_magic_formula_metrics` (518-566) lee `ebit` del income; `totalCurrentAssets`, `totalCurrentLiabilities`, `propertyPlantEquipmentNet`, `totalDebt`, `cashAndCashEquivalents` del balance; `market_cap` del caller.
- **`src/investbot/ai_explain_content.py`** (536 líneas): `QuestionSpec`/`CategorySpec` (33-44), `FORMULAS_AVANZADO`/`FUENTES_AVANZADO` (412-491, texto fijo, mismo patrón que esta spec extiende), `CATEGORIES_AVANZADO` (396-401): `alt` → `(alz, azp)`, `pio` → `(pig, pir, pia, pie)`, `mag` → `(mgr, mge)`. `category_of` (532-536) ya resuelve la categoría de cualquier `question_code`.
- **`src/investbot/ai_explain.py`** (1803 líneas, código real, confirmado hoy con los 3 fixes de hoy ya incluidos): `_build_leaf_message` (697-724, orden fijo: header → Dato → Cuenta → respuesta Ollama → Fórmula/Fuente → disclaimer) es el único lugar donde se arma el mensaje del camino "paso a paso". `_build_ver_dato_content` (727-745, "Ver dato") arma header + Dato + Fórmula/Fuente — **hoy NUNCA incluye la Cuenta**, es más corto a propósito (costo cero, sin Ollama). `_cuenta_alz`/`_cuenta_azp` (963-988), `_cuenta_pig`/`_fmt_criterio_piotroski`/`_cuenta_piotroski_grupo` (991-1069), `_cuenta_mgr`/`_cuenta_mge` (1072-1092) — las funciones que hoy arman la Cuenta de estos 3 modelos, leen de `datos` (el payload ya armado por `_build_explain_payload`, específico del ticker). `_enforce_cuenta_length`/`_MAX_CUENTA_CHARS=400` (755, 770-784) — guard de longitud existente, trata el exceso como "no calculable" (omite el bloque) en vez de truncar un número a la mitad. `_MAX_EXPLANATION_CHARS=480` (1276) — guard de longitud EXCLUSIVO de la respuesta de Ollama (`_enforce_brevity`, 1279+), no aplica a Cuenta/Fórmula/Fuente (ya documentado en el docstring de `_build_leaf_message`, línea 707-708) ni aplicará al desglose nuevo.

---

## Estado objetivo

1. Las 7 preguntas de `/avanzado` que corresponden a Altman Z, Z'', y los 3 sub-grupos de Piotroski, y Magic Formula (`alz`, `azp`, `pir`, `pia`, `pie`, `mgr`, `mge`) muestran, en el camino "🎓 Explicame paso a paso", una sección nueva **"🔍 Desglose"** además de la "🧮 Cuenta" existente (no la reemplaza — ver Decisión de diseño #2 para el porqué).
2. Cada línea del desglose cubre, para un término/letra/criterio de la fórmula: **de dónde sale** (campo de FMP o cálculo del bot, texto fijo), **qué es** (nombre legible del concepto), y **para qué sirve** (1 línea en lenguaje simple, sin jerga financiera).
3. El desglose es 100% texto fijo por `question_code` — no depende del ticker, no se recalcula por request, nunca pasa por Ollama. Mismo criterio de riesgo que `FORMULAS_AVANZADO`/`FUENTES_AVANZADO`.
4. `pig` ("Puntaje general" de Piotroski) y `alz`/`azp`/`mgr`/`mge`/`pir`/`pia`/`pie` no ganan ningún campo nuevo en `ExplanationContext` ni en el payload hacia Ollama — el desglose no usa `datos` para nada, se arma solo a partir de `question_code`.
5. La estructura de datos (`DesgloseTermino`, `DESGLOSE_AVANZADO`) es genérica: agregar las 19 preguntas restantes en una iteración futura es agregar entradas al dict, sin tocar `_build_leaf_message` ni el resto del mecanismo.
6. Las 20 preguntas sin desglose (todo texto libre + `mod`/`ben`/`aqv`/`aqq`/`aqm`/`aql` de avanzado) no cambian en nada — mismo mensaje que hoy.

---

## Decisiones de diseño tomadas

### 1. Por qué `pig` (Puntaje general) NO tiene desglose propio — se apoya en `pir`/`pia`/`pie`

El F-Score no es una suma ponderada con términos algebraicos (a diferencia de Altman Z) — son 9 criterios binarios independientes. `pig` solo muestra el total ("6 de 9 criterios evaluables cumplidos"), sin términos individuales que desglosar. El desglose de "de dónde sale cada criterio" ya tiene un lugar natural: los 3 sub-grupos `pir`/`pia`/`pie` (categoría "🧮 Piotroski F" en el menú de Nivel 2), que ES el mismo patrón que ya usa la Cuenta hoy (`_cuenta_piotroski_grupo`, reutilizada por los 3). Agregar el desglose ahí, no en `pig`, es consistente con el mecanismo existente y evita un mensaje de `pig` con 9 líneas (excesivo). **No es una decisión de negocio — es continuar el mismo patrón que ya separa Piotroski en 3 sub-preguntas**, por eso no se eleva a Daniela.

### 2. "🔍 Desglose" es una sección NUEVA, no reemplaza "🧮 Cuenta"

Sirven propósitos distintos: la Cuenta responde "¿cuál es el resultado con los números reales de este ticker?" (rápido, cuantitativo). El Desglose responde "¿qué significa cada letra, y de dónde sale?" (educativo, conceptual, igual para cualquier ticker). Reemplazar la Cuenta perdería la sustitución numérica real que Daniela ya pidió y aprobó en la spec anterior — su pedido de hoy es "más granular", no "en vez de". Se muestran las dos, en ese orden (Cuenta primero — el resultado concreto — Desglose después — el porqué de cada pieza), inmediatamente antes de la respuesta de Ollama.

### 3. El desglose NO se muestra en "📊 Ver dato" (camino sin Ollama) — solo en "🎓 Explicame paso a paso"

**Decisión por defecto, pero ver Decisión abierta para Daniela #1** — el pedido de Daniela fue puntualmente sobre lo que hoy muestra la Cuenta, que es exclusiva del camino "paso a paso". Mantener el desglose ahí también mantiene la asimetría ya establecida entre los 2 botones ("Ver dato" = rápido y compacto; "Explicame paso a paso" = completo, con Cuenta + explicación). Técnicamente no hay obstáculo para mostrarlo también en "Ver dato" (es texto fijo, costo cero) — se deja como decisión de UX abierta.

### 4. Estructura de datos — `DesgloseTermino` (dataclass) + `DESGLOSE_AVANZADO` (dict)

Nuevo en `ai_explain_content.py`, junto a `FORMULAS_AVANZADO`/`FUENTES_AVANZADO` (mismo archivo, mismo criterio de separación ya documentado en el docstring del módulo — "estructuras de datos puras, importables directamente en tests"):

```python
@dataclass(frozen=True)
class DesgloseTermino:
    letra: str           # símbolo tal cual aparece en la Fórmula/Cuenta (ej. "A", "EBIT", "Efectivo")
    campo_origen: str     # de dónde sale — campo(s) de FMP o cálculo del bot, texto fijo
    nombre: str           # nombre legible del concepto (ej. "Capital de Trabajo")
    que_mide: str          # 1 línea, lenguaje simple, sin jerga — "para qué sirve"


DESGLOSE_AVANZADO: dict[str, tuple[DesgloseTermino, ...]] = {
    "alz": (...),  # 5 términos: A, B, C, D, E
    "azp": (...),  # 4 términos: A, B, C, D (sin E, igual que la Cuenta hoy)
    "pir": (...),  # 4 criterios: ROA positivo, CFO positivo, ROA creciente, CFO > Utilidad
    "pia": (...),  # 3 criterios: Apalancamiento, Liquidez, Sin dilución
    "pie": (...),  # 2 criterios: Margen bruto, Rotación de activos
    "mgr": (...),  # 3 términos: EBIT, Capital de Trabajo Neto, Activos Fijos Netos
    "mge": (...),  # 4 términos: EBIT, Capitalización de Mercado, Deuda Total, Efectivo
}


def desglose(kind: str, code: str) -> tuple[DesgloseTermino, ...]:
    """`()` para `kind == "texto_libre"` o cualquier `code` sin entrada —
    las 20 preguntas sin desglose no rompen nada, se comportan como hoy."""
    if kind != "avanzado":
        return ()
    return DESGLOSE_AVANZADO.get(code, ())
```

**Contenido de las 7 entradas** (texto fijo, listo para transcribir a código — no se recalcula del payload del ticker, sale directo de `advanced_scoring.py` leído arriba):

**`alz` — Altman Z (fórmula original)**
| Letra | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| A | Activos Corrientes − Pasivos Corrientes, sobre Activos Totales (balance) | Capital de Trabajo | La plata "líquida" que le queda a la empresa para operar el día a día, en proporción a su tamaño |
| B | Utilidades Retenidas, sobre Activos Totales (balance) | Utilidades Retenidas | Cuánta ganancia acumulada a lo largo de los años reinvirtió la empresa en sí misma |
| C | EBIT, sobre Activos Totales (estado de resultados + balance) | EBIT (Ganancia antes de Intereses e Impuestos) | Qué tan rentable es el negocio en sí, sin el efecto de la deuda ni de los impuestos |
| D | Capitalización de Mercado, sobre Pasivos Totales (cotización + balance) | Capitalización de Mercado sobre Deuda | Cuánto "colchón" de valor en bolsa tiene la empresa frente a lo que debe |
| E | Ventas, sobre Activos Totales (estado de resultados + balance) | Rotación de Activos | Cuánto factura la empresa por cada dólar que tiene invertido en el negocio |

**`azp` — Altman Z'' (asset-light)**: mismas A-D que `alz` (texto idéntico), sin E — mismo criterio que la Cuenta hoy (`_cuenta_azp` no usa `e`).

**`pir` — Piotroski, Rentabilidad**
| Criterio | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| ROA positivo | Ganancia Neta (estado de resultados) | Ganancia Neta | Si la empresa ganó plata (no perdió) en el último año |
| CFO positivo | Flujo de Caja Operativo (estado de flujo de efectivo) | Flujo de Caja Operativo | Si entró más efectivo real del que salió por operar el negocio, más allá de la ganancia contable |
| ROA creciente | Ganancia Neta / Activos Totales, año actual vs. anterior (estado de resultados + balance) | ROA (Retorno sobre Activos) | Si la empresa se volvió más eficiente generando ganancia con lo que tiene |
| CFO > Utilidad | Flujo de Caja Operativo vs. Ganancia Neta (flujo de efectivo + resultados) | Calidad de la Ganancia | Si la ganancia reportada está respaldada por efectivo real, no solo "en papel" |

**`pia` — Piotroski, Apalancamiento y liquidez**
| Criterio | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| Apalancamiento decreciente | Deuda de Largo Plazo / Activos Totales, año actual vs. anterior (balance) | Apalancamiento | Si la empresa se está endeudando menos en relación a su tamaño |
| Liquidez creciente | Activos Corrientes / Pasivos Corrientes, año actual vs. anterior (balance) | Liquidez Corriente | Si mejoró su capacidad de pagar deudas de corto plazo con lo que tiene a mano |
| Sin dilución | Acciones en Circulación, año actual vs. anterior (estado de resultados) | Acciones en Circulación | Si la empresa emitió menos acciones nuevas, sin "repartir la torta" entre más dueños |

**`pie` — Piotroski, Eficiencia**
| Criterio | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| Margen bruto creciente | Utilidad Bruta / Ventas, año actual vs. anterior (estado de resultados) | Margen Bruto | Si le queda más ganancia por cada venta después del costo directo de producir/vender |
| Rotación de activos creciente | Ventas / Activos Totales, año actual vs. anterior (estado de resultados + balance) | Rotación de Activos | Si la empresa usa mejor sus activos para generar ventas |

**`mgr` — Magic Formula, ROIC**
| Término | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| EBIT | Estado de resultados | EBIT (Ganancia antes de Intereses e Impuestos) | La ganancia operativa del negocio, antes de intereses e impuestos |
| Capital de Trabajo Neto | Activos Corrientes − Pasivos Corrientes (balance) | Capital de Trabajo Neto | La plata que la empresa necesita tener disponible para el día a día |
| Activos Fijos Netos | Propiedad, Planta y Equipo neto (balance) | Activos Fijos Netos (PP&E) | Cuánto tiene invertido en cosas físicas — plantas, maquinaria, edificios — para operar |

**`mge` — Magic Formula, Earnings Yield**
| Término | De dónde sale | Nombre | Qué mide |
|---|---|---|---|
| EBIT | Estado de resultados | EBIT | (mismo que en `mgr`) |
| Capitalización de Mercado | Cotización en bolsa | Capitalización de Mercado | Cuánto vale la empresa en bolsa hoy |
| Deuda Total | Balance | Deuda Total | Cuánto debe en total la empresa |
| Efectivo | Balance | Efectivo y Equivalentes | Cuánta plata líquida tiene disponible ahora mismo |

### 5. Formato del bloque en el mensaje — compacto, 1 línea por término

```python
def _build_desglose_block(kind: str, question_code: str) -> Optional[str]:
    """100% texto fijo (Decisión de diseño #3) — no recibe `datos`, no hace
    I/O, nunca puede fallar por un campo faltante del ticker (a diferencia
    de `_build_cuenta_line`). `None` si la pregunta no tiene desglose (20 de
    27 preguntas) -- comportamiento hoy sin cambios."""
    terminos = ai_explain_content.desglose(kind, question_code)
    if not terminos:
        return None
    lineas = [
        f"• {t.letra} ({t.nombre}) — sale de {t.campo_origen}. {t.que_mide}."
        for t in terminos
    ]
    bloque = "🔍 Desglose:\n" + "\n".join(lineas)
    return _enforce_desglose_length(bloque)
```

Ejemplo real para `alz` (5 términos, el caso más largo de las 7 entradas):

```
🔍 Desglose:
• A (Capital de Trabajo) — sale de Activos Corrientes − Pasivos Corrientes, sobre Activos Totales (balance). La plata líquida que le queda a la empresa para operar el día a día.
• B (Utilidades Retenidas) — sale de Utilidades Retenidas, sobre Activos Totales (balance). Cuánta ganancia acumulada reinvirtió la empresa en sí misma con los años.
• C (EBIT) — sale de EBIT, sobre Activos Totales (resultados + balance). Qué tan rentable es el negocio, sin el efecto de la deuda ni los impuestos.
• D (Capitalización de Mercado sobre Deuda) — sale de la cotización, sobre Pasivos Totales (balance). Cuánto colchón de valor en bolsa tiene la empresa frente a lo que debe.
• E (Rotación de Activos) — sale de Ventas, sobre Activos Totales (resultados + balance). Cuánto factura la empresa por cada dólar invertido en el negocio.
```
Medido: **916 caracteres** — este es el caso real más largo entre las 7 entradas (5 términos; el resto tiene 2-4).

### 6. `_MAX_DESGLOSE_CHARS` — tope propio, independiente de `_MAX_EXPLANATION_CHARS` (Ollama) y de `_MAX_CUENTA_CHARS` (400, Cuenta)

**Decisión: `_MAX_DESGLOSE_CHARS = 1200`** (~1.3x margen sobre los 916 caracteres medidos del caso más largo real, `alz`). Por qué un margen menor que el 2.5x de `_MAX_CUENTA_CHARS` (400, calibrado sobre un caso mucho más corto): acá el contenido es texto fijo con longitud conocida y auditable en el momento de escribir el código — no varía por ticker ni se arma dinámicamente a partir de números con dígitos variables. El único escenario donde se excede es un error de edición futuro (alguien agrega una entrada con descripciones demasiado largas), no un ticker con números grandes. Mismo tratamiento que `_enforce_cuenta_length` si se excede: se omite el bloque completo (nunca un desglose cortado a mitad de una línea), se loguea un warning — y además, a diferencia de Cuenta, `qa` agrega un test que recorre las 7 entradas de `DESGLOSE_AVANZADO` y falla el build si alguna supera el tope, para que un error de edición se detecte en CI, no en producción.

### 7. Presupuesto/impacto del mensaje final — largo total, sección por sección

Para `alz` con "Explicame paso a paso" (peor caso: Cuenta larga + Desglose largo):

| Sección | Caracteres aprox. |
|---|---|
| Header (`TRANSPARENCY_USED`) | ~40 |
| 📌 Dato | ~60 |
| 🧮 Cuenta | hasta 400 (tope existente) |
| 🔍 Desglose (nuevo) | hasta 1200 (tope nuevo), 916 en el caso real medido |
| Respuesta de Ollama | hasta 480 (tope existente) |
| 📐 Fórmula + 📊 Fuente | ~200-300 |
| Disclaimer | ~150 |
| **Total peor caso** | **~2600** |

Telegram permite hasta 4096 caracteres por mensaje — el peor caso deja margen de ~1500 caracteres, sin necesidad de partir el mensaje en dos. Los otros 6 `question_code` con desglose (`azp`, `pir`, `pia`, `pie`, `mgr`, `mge`) tienen menos términos (2-4) → desglose más corto que el caso medido.

---

## Decisiones abiertas para Daniela

*(genuinamente de negocio/UX — RESUELTAS por Daniela 2026-09-03, ambas con la opción recomendada)*

1. **RESUELTO: el "🔍 Desglose" queda SOLO en "🎓 Explicame paso a paso"** — "Ver dato" se mantiene rápido/corto como hasta ahora, sin cambios.
2. **RESUELTO: orden Cuenta → Desglose** (primero el resultado con números reales, después qué es cada letra) — el orden que ya proponía la spec.

---

## Criterios de aceptación

- [ ] `DesgloseTermino` (dataclass frozen) y `DESGLOSE_AVANZADO` (dict, 7 entradas: `alz`, `azp`, `pir`, `pia`, `pie`, `mgr`, `mge`) existen en `ai_explain_content.py`, con el contenido de la tabla de la Decisión de diseño #4 (o el ajustado tras revisión de `security`/`qa`).
- [ ] `desglose(kind, code) -> tuple[DesgloseTermino, ...]` devuelve `()` para `kind == "texto_libre"` y para cualquier `code` de `avanzado` sin entrada (las 20 preguntas sin desglose no cambian).
- [ ] `_build_desglose_block(kind, question_code)` en `ai_explain.py` devuelve `None` si `desglose()` es vacío, o el bloque formateado (1 línea por término, formato de la Decisión de diseño #5) si no.
- [ ] `_enforce_desglose_length` con `_MAX_DESGLOSE_CHARS = 1200` — si se excede, omite el bloque completo y loguea warning (nunca corta a mitad de línea).
- [ ] `_build_leaf_message` inserta "🔍 Desglose" inmediatamente después de "🧮 Cuenta" (si ambos están presentes) y antes de la respuesta de Ollama — orden confirmado o ajustado según la Decisión abierta #2.
- [ ] Para las 7 preguntas con desglose, un mensaje de "Explicame paso a paso" real (ticker con todos los campos disponibles) muestra Cuenta Y Desglose, ningún término con datos faltantes/`None` visible.
- [ ] Para las 20 preguntas sin desglose, el mensaje de "Explicame paso a paso" es byte-a-byte igual al actual (sin la sección nueva) — cero regresión.
- [ ] El botón "📊 Ver dato" no cambia para ninguna de las 27 preguntas (salvo que Daniela responda la Decisión abierta #1 a favor de incluirlo ahí también).
- [ ] Test que recorre las 7 entradas de `DESGLOSE_AVANZADO`, arma el bloque con `_build_desglose_block`, y falla si alguna supera `_MAX_DESGLOSE_CHARS` (Decisión de diseño #6).
- [ ] Ningún campo nuevo en `ExplanationContext`, en el payload de `_build_explain_payload`, ni en `datos_tokens`/el guard de Ollama — el desglose no es parte de lo que Ollama recibe (no hace falta, no lo genera ni lo repite).
- [ ] Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR.
- [ ] Suite completa de tests existente sigue en verde (0 regresiones) — build actual referenciado en memoria: 1368 tests.

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain_content.py` → agregar `DesgloseTermino`, `DESGLOSE_AVANZADO` (7 entradas), función `desglose(kind, code)`.
- `src/investbot/ai_explain.py` → agregar `_build_desglose_block`, `_enforce_desglose_length`, `_MAX_DESGLOSE_CHARS = 1200`; modificar `_build_leaf_message` para insertar la sección nueva (y, si Daniela lo pide, `_build_ver_dato_content`).
- `tests/test_ai_explain.py` / `tests/test_ai_rewrite.py` (donde ya viven los tests de Cuenta/Fórmula/Fuente) → tests nuevos para `desglose()`, `_build_desglose_block`, el tope de longitud, y el mensaje final de las 7 preguntas afectadas.

---

## Restricciones

- No se toca ninguna fórmula, cálculo, ni campo de `advanced_scoring.py` — esta spec es puramente de presentación/contenido, cero cambios de lógica de negocio.
- No se agregan campos nuevos a `ExplanationContext`, al payload de Ollama, ni a `datos_tokens` del guard — el desglose es independiente del ticker.
- No se toca el mecanismo de `_build_cuenta_line`/`_cuenta_*` existente — el desglose es aditivo, no reemplaza ninguna función de Cuenta.
- No se agrega una forma nueva de `callback_data` ni se cambia el teclado — mismos 2 botones por pregunta que ya existen (`SDD_explicacion_paso_a_paso.md`, no reabierta).
- Fuera de alcance de esta iteración: las 19 preguntas restantes con fórmula (texto libre completo + `mod`/`ben`/`aqv`/`aqq`/`aqm`/`aql` de avanzado) — quedan para una iteración futura si Daniela confirma que el formato funciona bien con estos 3 modelos primero.
- Beneish M-Score (`ben`) sigue sin desglose — D1 (RESUELTO, no reabrir): siempre "no calculable", nada que desglosar.
- No implementar código todavía — esta spec espera `security` y `qa` antes de pasar a `implementer`.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_desglose_terminos_formula.md` (esta spec)

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — `security` agrega los suyos (superficie de ataque nueva mínima: una tabla de texto fijo más, sin datos de terceros, sin cambio de contrato de función, sin campo nuevo hacia Ollama).

### Decisiones de diseño tomadas
Ver "Decisiones de diseño tomadas" arriba (7 puntos) — no reabrir sin spec patch. Las 2 "Decisiones abiertas para Daniela" son las únicas pendientes de negocio; todo lo demás es definitivo.

---

## Revisión de seguridad

**Rol:** `security` · **Fecha:** 2026-09-03 · **Código real leído para esta revisión:** `advanced_scoring.py`, `ai_explain_content.py` (líneas 396-491, `FORMULAS_AVANZADO`/`FUENTES_AVANZADO`/`CATEGORIES_AVANZADO` actuales), `ai_explain.py` (líneas 697-724 `_build_leaf_message`, 755-784 `_enforce_cuenta_length`/`_MAX_CUENTA_CHARS`), `advanced_command.py` (líneas 7, 137, 487) y `query_handler.py`/`onboarding.py`/`summary.py` (uso de `parse_mode`).

**Resultado: sin hallazgos bloqueantes.** Es una feature chica, determinística, sin superficie de ataque nueva real. Los 4 puntos pedidos:

1. **Contenido 100% estático — confirmado.** Las 7 entradas de `DESGLOSE_AVANZADO` (Decisión de diseño #4) son texto fijo transcrito directamente de los nombres de campo de `advanced_scoring.py`, sin ningún placeholder ni f-string que interpole `datos`/FMP. `_build_desglose_block` (Decisión de diseño #5) arma las líneas solo con atributos de `DesgloseTermino` (`letra`, `nombre`, `campo_origen`, `que_mide`), todos literales del dict — no recibe `datos` como parámetro, igual que `FORMULAS_AVANZADO`/`FUENTES_AVANZADO` ya en producción. Coherente con el criterio ya aplicado a esas dos tablas.

2. **Tope de caracteres — mecanismo bien especificado.** `_enforce_desglose_length`/`_MAX_DESGLOSE_CHARS=1200` (Decisión de diseño #6) replica exactamente el patrón ya en producción de `_enforce_cuenta_length`/`_MAX_CUENTA_CHARS=400` (confirmado leyendo el código real, líneas 770-784): si el bloque excede el tope, se descarta el bloque COMPLETO (`return None`) y se loguea un warning — nunca se corta un string a mitad de camino, por lo tanto no hay escenario donde una línea quede truncada mostrando información rota o confusa. Como mejora adicional sobre el patrón de Cuenta, la spec ya pide (criterios de aceptación) un test de CI que recorra las 7 entradas y falle el build si alguna supera el tope — mejor que el caso de Cuenta, que no tiene ese test dedicado.

3. **No pasa por el guard anti-invención — correcto, y con una precisión importante.** Confirmado leyendo el docstring real de `_build_leaf_message` (línea ~704-709): a diferencia de la Cuenta, que SÍ entra a `datos_del_contexto`/`datos_tokens` ANTES de llamar a Ollama (Decisión de diseño #4 de la spec anterior), el Desglose —igual que Fórmula/Fuente— se arma y se inserta en el texto final DESPUÉS de tener la respuesta de Ollama, nunca antes ni mezclado con el prompt. La Decisión de diseño #4 de esta spec ("el desglose no usa `datos` para nada, se arma solo a partir de `question_code`") es consistente con esto. Es el criterio correcto — el guard existe para detectar que Ollama invente o repita datos del payload, y el Desglose nunca pasa por Ollama.

4. **Cero regresión / cero superficie nueva de callback — confirmado por diseño.** `desglose(kind, code)` devuelve `()` para cualquier `code` sin entrada explícita en el dict (default de `dict.get`), así que las 20 preguntas sin desglose no cambian: `_build_desglose_block` devuelve `None` y `_build_leaf_message` no agrega la sección (mismo patrón condicional que ya usa con `cuenta`). No se toca `callback_data`, el teclado, ni el rate limiter — la spec ya lo restringe explícitamente ("Restricciones") y no hay ningún artefacto nuevo en la lista de archivos a modificar que toque esas piezas.

**Verificación adicional hecha en esta revisión, no pedida explícitamente pero relevante:** los mensajes de "Explicame paso a paso" se envían como texto plano, sin `parse_mode="Markdown"` (confirmado en `advanced_command.py` líneas 7/137/487 — hallazgo 4 de una revisión de seguridad anterior, ya corregido en producción). Esto descarta cualquier riesgo de que caracteres del texto fijo nuevo (guiones, paréntesis, "&", etc.) rompan el formato Markdown de Telegram — no aplica el mismo cuidado de escapado que sí se le exige a `company_name`/`peers` (esos SÍ viajan por un camino con `parse_mode="Markdown"`).

**Mejoras recomendadas, no bloqueantes:**

- (a) El comentario del código propuesto en la spec para `_build_desglose_block` ya documenta bien por qué es seguro ("nunca puede fallar por un campo faltante del ticker") — sugiero que el docstring de `_enforce_desglose_length`, cuando se escriba, cite explícitamente el mismo texto que usa `_enforce_cuenta_length` sobre "se omite el bloque completo, nunca se trunca una línea", para que quede auditable en el propio código y no solo en la spec (mismo estándar que ya se sigue en el resto del archivo).
- (b) Si en una futura iteración se extiende `DESGLOSE_AVANZADO` a las 19 preguntas restantes (fuera de alcance ahora), vale re-confirmar en ese momento que ninguna entrada nueva interpole datos dinámicos — el mecanismo genérico lo permite en teoría (`campo_origen`/`que_mide` son `str` simples, no hay nada que impida a futuro escribir un f-string ahí por error). No es un riesgo hoy porque las 7 entradas de esta iteración ya están transcritas como texto literal en la propia spec.

**Conclusión:** nada bloqueante. `qa`/`implementer` pueden continuar el pipeline sin cambios a esta spec.

---

## Criterios de QA

**Rol:** `qa` · **Fecha:** 2026-09-03 · **Momento:** 1 (pre-implementación) · **Código real leído para esta revisión:** los mismos fragmentos citados por `architect` y `security` arriba (`advanced_scoring.py`, `ai_explain_content.py` 396-491, `ai_explain.py` 697-724/755-784/963-1092), más `tests/test_ai_explain.py` (fixtures existentes de Cuenta/Fórmula/Fuente, para confirmar qué se puede reutilizar sin duplicar).

**Nota de alcance:** los 11 checkboxes que dejó `architect` en "Criterios de aceptación" ya cubren correctamente la existencia de las estructuras, el mecanismo de longitud, el orden de inserción, y la regresión a alto nivel. Este bloque no los repite — agrega los ángulos de testabilidad, casos límite exactos, y fixtures concretos que faltan para que esos checkboxes sean verificables con evidencia, no solo declarados.

### Tipo de prueba principal

**Unit testing** (mayoría — `desglose()`, `_build_desglose_block`, `_enforce_desglose_length` son funciones puras, sin I/O, ideales para unit) + **Regression testing dirigida** (las 20 preguntas sin desglose y "Ver dato" para las 27 deben quedar byte-a-byte iguales — es exactamente el escenario que `references/regression.md` llama "selección de casos por impacto de cambio": el cambio toca un único punto de inserción en `_build_leaf_message`, así que el riesgo de regresión se concentra ahí, no en el resto del archivo).

No aplica Integration/E2E como tipo principal: no hay BD, no hay llamada nueva a un servicio externo, y `security` ya confirmó cero superficie de `callback_data`/API nueva.

### Cobertura mínima requerida

- [ ] Code coverage = 100% líneas en `desglose()`, `_build_desglose_block`, `_enforce_desglose_length` (funciones nuevas, puras, sin excusa para dejar una rama sin cubrir).
- [ ] Branch coverage = 100% en las 2 ramas condicionales críticas: `desglose()` con código presente vs. ausente en el dict; `_enforce_desglose_length` bajo el tope vs. sobre el tope.
- [ ] Todos los 11 checkboxes de "Criterios de aceptación" del `architect` cubiertos por al menos un test con evidencia (output de pytest), no solo inspección visual del código.

### Casos obligatorios

- [ ] **Happy path** — para cada una de las 7 entradas (`alz`, `azp`, `pir`, `pia`, `pie`, `mgr`, `mge`), armar el mensaje completo de "Explicame paso a paso" con un fixture de ticker con **todos los campos disponibles**, y confirmar: aparece "🧮 Cuenta" seguido de "🔍 Desglose" (en ese orden, antes de la respuesta de Ollama — `str.index("🧮 Cuenta") < str.index("🔍 Desglose") < índice donde empieza el texto de Ollama`), y **ningún término del bloque tiene `letra`/`campo_origen`/`nombre`/`que_mide` vacío, `None`, o la palabra literal "None" visible en el string final** (este es el caso de mayor riesgo de negocio de la feature — ver más abajo).
- [ ] **Caso límite — tope exacto de `_MAX_DESGLOSE_CHARS`:** un bloque de exactamente 1200 caracteres pasa sin omitirse; un bloque de 1201 caracteres se omite completo (`_build_desglose_block` devuelve `None`, no un string cortado). Ambos casos con un `DesgloseTermino` sintético construido para el test — **no** se fuerza a que las 7 entradas reales lleguen al límite, para no acoplar el test de límite a redacciones futuras.
- [ ] **Caso límite — `desglose()` con código inexistente/typo** (ej. `"xyz"`, cadena vacía, o `kind="texto_libre"` con cualquier `code`): devuelve `()`, no lanza excepción.
- [ ] **Caso de error / regla de negocio explícita — `pig` NO tiene desglose propio** (Decisión de diseño #1): `desglose("avanzado", "pig")` devuelve `()` — test dedicado, porque es la única de las 7+1 preguntas de Piotroski/Altman/Magic donde la ausencia de desglose es intencional y no un olvido; sin este test, un futuro `implementer` podría "completar" `pig` por simetría y violar la decisión de diseño.
- [ ] **Caso de alto riesgo de negocio — longitud real de las 7 entradas bajo el tope** (ya listado por `architect`, aquí se precisa el mecanismo): test parametrizado que recorre `DESGLOSE_AVANZADO.items()`, arma el bloque con `_build_desglose_block` para cada uno, y hace `assert len(bloque) <= 1200` explícito por entrada (no solo un `assert` global) — así, si se agrega texto a una sola entrada en el futuro, el mensaje de fallo del test señala cuál, sin tener que depurar las 7.

### Testabilidad

- [ ] `desglose()`, `_build_desglose_block`, `_enforce_desglose_length` son funciones puras (sin efectos secundarios, sin dependencia de `datos`/ticker) — confirmado por diseño en la spec (Decisión #4), no requiere mocks.
- [ ] `_build_leaf_message` sigue siendo testeable insertando un `desglose` fijo — no hace falta mockear Ollama para probar el ensamblado del bloque nuevo (ya se prueba hoy sin Ollama real para Cuenta/Fórmula/Fuente, mismo patrón).
- [ ] El warning de `_enforce_desglose_length` cuando se omite el bloque es verificable con `caplog`/equivalente (nivel WARNING, sin depender de la redacción exacta del mensaje de log — solo el nivel y que se emita).

### Criterio de exit de QA

- Todos los tests nuevos pasan (BUILD SUCCESS / suite verde) y la suite completa existente (1368 tests, referenciada en memoria) sigue en verde — 0 regresiones.
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la suite nueva (son funciones puras y deterministas — no hay excusa para flakiness aquí; un test flaky en este módulo es señal de un fixture mal construido, no de timing).

### Fixtures mínimos que faltan (a definir antes de `implementer`)

1. **Reutilizar, no duplicar:** los fixtures de ticker "con todos los campos disponibles" que ya existen en `tests/test_ai_explain.py` para probar `_cuenta_alz`/`_cuenta_azp`/`_cuenta_piotroski_grupo`/`_cuenta_mgr`/`_cuenta_mge` (citados por `architect` en "Código real leído") son el punto de partida para el caso "Happy path" de arriba — el Desglose no necesita datos del ticker, pero el mensaje completo sí necesita una Cuenta calculable para armar el escenario realista (Cuenta + Desglose juntos). No crear fixtures de ticker nuevos si los existentes ya cubren estos 7 `question_code`.
2. **Lista explícita de las 20 preguntas sin desglose**, para parametrizar el test de regresión — evita que quede implícito o se infiera mal: los 10 de texto libre (`vf`, `gra`, `dcf`, `mul`, `rat`, `pil`, `rsk`, `mom`, `cmp`, `ver`) + `pig` + `mod` + `ben` + los 4 de AQR (`aqv`, `aqq`, `aqm`, `aql`) + los que falten para completar 20 según `CATEGORIES_AVANZADO`/`category_of` reales (confirmar el conteo exacto contra el código al escribir el test, no contra esta spec, por si `architect` contó distinto).
3. **Snapshot/golden baseline** de los mensajes actuales (antes de que `implementer` toque nada) para las 20 preguntas sin desglose y para "Ver dato" de las 27 — capturado como fixture de texto (ej. `tests/fixtures/mensajes_baseline_pre_desglose/`) para que el test de regresión compare byte-a-byte contra algo concreto, no contra una descripción.
4. **`DesgloseTermino` sintético fuera de `DESGLOSE_AVANZADO`** para los casos límite del tope de 1200 caracteres (ver "Casos obligatorios" arriba) — evita acoplar ese test a la redacción real de las 7 entradas.

### Qué NO se prueba, y por qué

- **Sin E2E real contra Telegram/Ollama/FMP** — mismo criterio que ya usó `security`: el Desglose es texto fijo, sin I/O nuevo, sin llamada nueva a ningún servicio externo (`architect` ya lo deja como checkbox: "cero llamadas HTTP nuevas"). Un E2E no agregaría cobertura real, solo costo de mantenimiento.
- **Sin pruebas de carga/performance** — no hay lógica nueva con costo variable (nada de loops sobre datos externos, nada de tamaño proporcional al ticker); el peor caso son 7 entradas fijas ya medidas en la spec (916 caracteres el más largo). Performance testing no aplica a contenido estático.
- **Sin test de `parse_mode`/escapado de Markdown** — ya descartado por `security` en la "Verificación adicional": estos mensajes se envían como texto plano, sin `parse_mode="Markdown"`, así que caracteres como "&"/paréntesis/guiones en el texto fijo no tienen riesgo de romper formato. Confirmarlo de nuevo en QA sería duplicar el hallazgo de `security`, no agregar cobertura.
- **Sin test de la calidad conceptual/pedagógica del texto** ("¿está bien explicado en lenguaje simple?") — es una revisión de contenido/negocio, no un criterio automatizable; queda a criterio de Daniela al revisar el texto de la Decisión de diseño #4, no de QA.
- **Sin test de la respuesta de Ollama** — el Desglose nunca pasa por Ollama (confirmado por diseño y por `security`), así que no hay escenario donde Ollama afecte o sea afectado por esta feature.

---

**Estado tras esta revisión:** spec lista para pasar a `implementer`. Los 11 checkboxes de `architect` cubren la estructura y el mecanismo; este bloque cierra los ángulos de testabilidad, los 2 casos límite exactos del tope de caracteres, la regla `pig` sin desglose propio, y los fixtures que antes no estaban explícitos (baseline de regresión, lista de las 20 preguntas, término sintético para el límite). No hay bloqueantes de QA — no se requiere una nueva iteración de `architect` antes de implementar.
