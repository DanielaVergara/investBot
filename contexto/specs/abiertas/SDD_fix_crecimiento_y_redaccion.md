# Spec: Fix del criterio de crecimiento + mejora de redacción/formato del mensaje + auditoría de procedencia [Iter-1]

**Rol:** `architect`.
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` (siguiendo el pipeline estándar de este proyecto — ver `SDD_peers_dinamicos_y_eventos_corporativos.md` para el precedente de handoff). **Las 2 preguntas bloqueantes que dejó abierta la Parte 1 (Pregunta 1 y Pregunta 2) ya están resueltas por Daniela (2026-07-30)** — ver "Resolución de las preguntas bloqueantes" al principio de la Parte 1. **Las Partes 2, 3 y 4 nunca tuvieron preguntas bloqueantes de threshold** — son propuestas de redacción/estructura/auditoría que Daniela puede seguir ajustando en la revisión de `security`/`qa` sin que eso reabra la Parte 1. **No queda ninguna pregunta bloqueante pendiente en todo el documento** — el spec está listo para `security` sin gating adicional. Este proyecto no usa un paso de `frontend` separado (bot de solo texto sobre Telegram) — la Parte 3 (formato) hace las veces de esa revisión, hecha acá directamente por el `architect` dado que es simple Markdown de Telegram, no un sistema de diseño.

Motivador: Daniela probó el bot real en producción con NVDA y reportó 3 problemas de una sola conversación. Se agrupan en un solo documento porque nacieron de la misma sesión de feedback, pero — igual que `SDD_peers_dinamicos_y_eventos_corporativos.md` — cada Parte tiene su propio Contexto/Estado actual/Estado objetivo/Decisiones/Preguntas/Criterios/Artefactos/Restricciones. Se ordenan por impacto real: **Parte 1 (bug de cálculo, la más importante) → Parte 2 (redacción) → Parte 3 (formato/espaciado) → Parte 4 (auditoría de procedencia)**.

---

# PARTE 1 — Bug: "Utilidades positivas y crecientes" marca ❌ con crecimiento explosivo pero irregular

## Resolución de las preguntas bloqueantes (2026-07-30)

Daniela resolvió las 2 preguntas bloqueantes que dejó abiertas esta Parte. Quedan **cerradas**, no se reabren:

- **Pregunta 1 — ¿qué opción?:** **Opción A** — comparar solo extremos (`historial[-1] > historial[0]`), sin exigir monotonía año a año y sin ningún threshold nuevo (`K`/`N`) que justificar o mantener. Es la opción recomendada por el `architect` (ver "Recomendación del `architect`" más abajo, que queda confirmada, no solo propuesta) y la que reutiliza el mismo criterio de "extremos" que `valuation.py::calculate_cagr()` ya usa para Graham/DCF — sin heredar la guarda de signo en la base de `calculate_cagr()` (`_es_creciente()` sigue sin esa guarda, ver el trade-off documentado en "Estado actual").
- **Pregunta 2 — ¿mismo criterio para ambos pilares?:** **Sí, el mismo criterio para `ingresos_crecientes` y `utilidades_crecientes`** — no se justifican reglas distintas. `utilidades_crecientes` sigue teniendo, sin cambios, su condición adicional ya existente (`net_income_historial[-1] > 0`), apilada sobre el mismo `_es_creciente()` que usa `ingresos_crecientes`.

Con estas 2 resoluciones, **no queda ninguna pregunta bloqueante pendiente en la Parte 1.** El pseudocódigo final (Opción A, aplicado a ambos pilares) está en "Estado objetivo" y en los Criterios de aceptación, ya no como una de 3 alternativas sino como la única función a implementar.

## Contexto

Daniela confirmó externamente que NVIDIA tiene utilidades positivas y crecientes en cualquier lectura razonable del inversor — la empresa tuvo una explosión de ganancias por IA en los últimos años. El bot igual marca ❌ en el pilar "Utilidades positivas y crecientes". Ya habíamos diagnosticado la causa raíz en esta misma conversación: NVIDIA tuvo un año fiscal con una caída de utilidades (bajón de gaming + ajuste de inventario cripto) en medio de una serie que, mirada de punta a punta, es fuertemente creciente. Un solo año de baja en el medio de la serie histórica rompe el chequeo actual por completo, sin importar cuán fuerte sea la tendencia general.

## Estado actual

- `src/investbot/rules.py::_es_creciente()` (líneas 128-136):

```python
def _es_creciente(historial: list[float]) -> bool:
    """True si la serie (ordenada de más antiguo a más reciente) es no decreciente
    y el valor más reciente supera al más antiguo — crecimiento año a año."""
    if not historial or len(historial) < 2:
        return False
    return all(
        historial[i] <= historial[i + 1] for i in range(len(historial) - 1)
    ) and historial[-1] > historial[0]
```

  Exige DOS condiciones simultáneas: (a) la serie completa es no decreciente **en cada paso consecutivo** (`historial[i] <= historial[i+1]` para TODOS los `i`), Y (b) el último valor supera al primero. La condición (a) es la que rompe el caso NVIDIA — un solo año de baja en cualquier punto intermedio de la serie hace que `all(...)` sea `False`, sin importar que (b) sí se cumpla ampliamente.

- `src/investbot/rules.py::evaluate_pillars()` (líneas 138-169) usa `_es_creciente()` para **ambos** pilares:

```python
ingresos_crecientes = _es_creciente(revenue_historial)
utilidades_crecientes = _es_creciente(net_income_historial) and (
    net_income_historial[-1] > 0 if net_income_historial else False
)
```

  `utilidades_crecientes` ya tiene una condición adicional que no depende de `_es_creciente`: el último valor de la serie debe ser positivo (`net_income_historial[-1] > 0`) — esto es correcto y no cambia con esta spec, cubre el caso de una empresa que "crece" pero sigue en pérdidas (ver `test_pilar_utilidades_crecientes_pero_negativas_al_final_false`, `tests/test_rules.py` línea 132).

- **Origen y forma de los datos** — `src/investbot/query_handler.py::fetch_and_analyze_parts` (líneas 209-217):

```python
revenue_historial = _annual_series(income_statements, "revenue")
net_income_historial = _annual_series(income_statements, "netIncome")
```

  `income_statements` viene de `fmp_client.get_income_statement(..., period="annual", limit=5)` (`src/investbot/fmp_client.py` líneas 170-184, default `limit=5`) — **hasta 5 años de datos anuales**, no más. `_annual_series()` (líneas 95-99 de `query_handler.py`) invierte el orden de FMP (más reciente primero) a cronológico (más antiguo → más reciente). Para una empresa recién salida a bolsa, la serie puede tener **menos de 5 puntos** (mínimo 2 para que `_es_creciente` no retorne `False` de entrada por el guard de la línea 131; el resto del sistema —`valuation.calculate_cagr`, ver abajo— ya trata "menos de 3 puntos" como `historial_insuficiente`).

- **Precedente ya existente en el proyecto para "crecimiento" con guardas explícitas**: `src/investbot/valuation.py::calculate_cagr()` (líneas 52-77) es la función que el resto del bot (Graham, DCF) usa para medir crecimiento — compara **únicamente el valor más reciente contra el más antiguo** (`(valor_reciente / valor_antiguo) ** (1/n_años) - 1`), sin exigir ningún tipo de monotonía intermedia. Es decir: **el bug de `_es_creciente` es una inconsistencia interna del proyecto** — la propia definición de "crecimiento" que usa el motor de valoración (CAGR punta a punta) ya es más permisiva que la que usan los Pilares, y el caso NVIDIA es la prueba de que la versión de Pilares es demasiado estricta para reflejar lo que un inversor razonable llamaría "utilidades crecientes".

  Ojo: `calculate_cagr()` tiene su propia guarda que **no** queremos heredar sin pensarlo — devuelve `None` si `valor_antiguo <= 0` (línea 70). Para `ingresos_crecientes` esto casi nunca importa (los ingresos de una empresa que llega a este análisis son casi siempre positivos todos los años). Para `utilidades_crecientes` sí importa: una empresa en **turnaround** (pérdidas hace unos años, ahora rentable y en franca mejora) tiene `net_income_historial[0] <= 0` — si reutilizáramos `calculate_cagr()` tal cual, esa empresa nunca podría marcar ✅ en "crecientes", aunque su trayectoria sea sólida. El criterio actual (comparación directa `historial[-1] > historial[0]`, sin pasar por la fórmula de CAGR) **sí** puede evaluar ese caso correctamente, porque no tiene guarda de signo en la base. Esto es relevante para las 3 opciones de abajo — ninguna reutiliza `calculate_cagr()` tal cual por este motivo.

## Estado objetivo

`_es_creciente()` deja de exigir monotonía estricta año a año — **confirmado: Opción A** (Pregunta 1, resuelta). El criterio final:
1. Sigue siendo una función pura, sin I/O, con la misma firma (`historial: list[float]) -> bool`).
2. Sigue manejando series de 2 a 5 puntos (según cuántos años devolvió FMP esta consulta) sin crashear.
3. Corrige el caso NVIDIA: una serie con una sola caída intermedia rodeada de crecimiento fuerte evalúa `True`.
4. Acepta explícitamente, con el visto bueno de Daniela, el trade-off de no distinguir "sube-baja-sube mucho" (caso NVIDIA) de una trayectoria "cae varios años y repunta apenas por encima del inicio" — ambas evalúan `True` por igual bajo este criterio. Ver "Recomendación del `architect`" (confirmada) para la justificación de por qué este trade-off es aceptable.

**Aplica exactamente igual a `ingresos_crecientes` y a `utilidades_crecientes`** (Pregunta 2, resuelta) — mismo `_es_creciente()`, sin parámetros distintos por pilar. `utilidades_crecientes` sigue apilando, sin cambios, su condición adicional `net_income_historial[-1] > 0`.

### Pseudocódigo final — `_es_creciente()` (Opción A, confirmada)

```python
def _es_creciente(historial: list[float]) -> bool:
    """True si el valor más reciente de la serie supera al más antiguo —
    crecimiento de punta a punta de la ventana disponible (hasta 5 años
    anuales de FMP), sin exigir que cada paso intermedio sea no decreciente.
    Mismo criterio de "extremos" que valuation.calculate_cagr() usa para
    Graham/DCF (Pregunta 1, RESUELTA por Daniela: Opción A) — sin heredar
    la guarda de signo en la base de calculate_cagr() (acá no importa que
    historial[0] sea <= 0, a diferencia de calculate_cagr, para no excluir
    a empresas en turnaround del pilar de crecimiento).
    """
    if not historial or len(historial) < 2:
        return False
    return historial[-1] > historial[0]
```

### Pseudocódigo final — `evaluate_pillars()` (mismo criterio para ambos pilares, Pregunta 2 confirmada)

```python
def evaluate_pillars(
    *,
    revenue_historial: list[float],
    net_income_historial: list[float],
    liquidity: LiquidityResult,
    barata: Optional[bool],
) -> PillarsResult:
    ingresos_crecientes = _es_creciente(revenue_historial)
    utilidades_crecientes = _es_creciente(net_income_historial) and (
        net_income_historial[-1] > 0 if net_income_historial else False
    )
    # ... resto sin cambios (deuda_controlada, precio_razonable, ventaja_competitiva)
```

Nota: el cuerpo de `evaluate_pillars()` arriba es **idéntico línea por línea** al de hoy (líneas 156-159 de `rules.py`) — el único cambio de esta Parte es adentro de `_es_creciente()`. No hace falta tocar `evaluate_pillars()` en absoluto, solo se muestra acá para dejar explícito que ambos pilares comparten la misma función sin ningún parámetro adicional que los diferencie.

### Opciones descartadas (registro histórico — no se implementan, quedan documentadas para no volver a evaluarlas sin motivo nuevo)

Las Opciones B y C fueron presentadas junto a la A para que Daniela eligiera con información completa. **Quedan descartadas explícitamente** — se documentan igual, sin borrar, siguiendo el mismo criterio de trazabilidad que el resto de este proyecto (una decisión resuelta no se "limpia" del spec, queda como registro de qué se consideró y por qué no se eligió).

### Opción A — Comparar solo el extremo más reciente contra el más antiguo (eliminar la monotonía) — ELEGIDA

Quitar la condición `all(historial[i] <= historial[i+1] ...)` y quedarse únicamente con `historial[-1] > historial[0]` (la condición que **ya existía** como segunda cláusula — no es nueva).

- ✅ Ventajas: cambio mínimo (se borra una condición, no se agrega nada); no introduce ningún threshold/constante nueva que haya que acordar después; corrige el caso NVIDIA de forma directa; consistente con el espíritu de `calculate_cagr()` (comparar extremos) sin heredar su guarda de signo en la base (sigue funcionando para empresas en turnaround).
- ❌ Desventajas (aceptadas explícitamente por Daniela): no distingue una trayectoria "sube-baja-sube mucho" (caso NVIDIA) de una trayectoria "cae 4 años seguidos y repunta apenas el último año por encima del primero" — ambas marcan ✅ por igual, aunque la segunda es mucho menos sólida. No mide volatilidad ni cantidad de años en baja.
- 📌 Por qué se eligió: prioriza simplicidad, evita inventar un threshold nuevo, y confía en que las otras señales del mensaje (Valor Justo, momentum, contexto de mercado) ya dan contexto adicional sobre la calidad de la tendencia — el pilar no pretende ser el único dato que se mira.

### Opción B — Tolerancia de N años de baja permitidos dentro de la ventana completa — DESCARTADA

Mantener el espíritu de "mayormente creciente" pero permitir hasta `K` pasos consecutivos-o-no donde `historial[i] > historial[i+1]` (bajas), en vez de exigir cero. Sigue exigiendo `historial[-1] > historial[0]`.

```python
def _es_creciente(historial: list[float], *, tolerancia_bajas: int = K) -> bool:
    if not historial or len(historial) < 2:
        return False
    bajas = sum(
        1 for i in range(len(historial) - 1) if historial[i] > historial[i + 1]
    )
    return bajas <= tolerancia_bajas and historial[-1] > historial[0]
```

`K` es un **umbral nuevo que Daniela debe fijar explícitamente** (ilustrado acá como `K`, sin valor propuesto — a diferencia de la Opción A, esta opción no se puede especificar sin ese número). Con la ventana actual de máximo 5 puntos (4 pasos posibles), `K=1` sería "como máximo 1 año de baja en toda la ventana"; `K=2` sería más permisivo.

- ✅ Ventajas: preserva más la noción de "tendencia mayormente ascendente" que la Opción A — no acepta una serie con muchas bajas solo porque el último año fue bueno.
- ❌ Desventajas: introduce un número nuevo que hay que justificar y mantener (más superficie de "por qué K y no K+1"); con una ventana de hasta 5 puntos (4 pasos), la diferencia entre `K=1` y `K=2` es grande en términos relativos — hay poco margen para calibrar con precisión; más código que la Opción A para un beneficio incierto dado el tamaño chico de la ventana.
- 📌 Mejor cuando: se quiere una señal más conservadora que la Opción A y se está dispuesto a fijar y documentar un número específico como parte de esta decisión.

### Opción C — Achicar la ventana a los últimos N años en vez de cambiar la regla de comparación — DESCARTADA

En vez de tocar la lógica de comparación, cambiar qué datos entran: evaluar el criterio (cualquiera de los de arriba) solo sobre los últimos `N` años de la serie de hasta 5, ignorando los años más viejos.

```python
def _es_creciente(historial: list[float], *, ventana: int = N) -> bool:
    ventana_datos = historial[-ventana:] if len(historial) >= ventana else historial
    if not ventana_datos or len(ventana_datos) < 2:
        return False
    return ventana_datos[-1] > ventana_datos[0]  # (o la regla de monotonía actual, sobre la ventana recortada)
```

`N` es otro **umbral nuevo que Daniela debe fijar explícitamente**. Nota importante: con `N` chico (ej. 2 o 3), el caso NVIDIA queda corregido *casi por casualidad* — depende de en qué posición exacta de la ventana de 5 años cae el año de baja esta consulta puntual, no de una corrección sistemática del criterio. Si el año de baja cae dentro de la ventana recortada de todos modos, el problema no se resuelve. Esta opción **no ataca la causa raíz** (la exigencia de monotonía) sino que la esquiva reduciendo cuántos años puede "ver" — es la opción menos robusta de las 3 para este caso específico, aunque es intuitiva para un usuario dummy ("¿creció en los últimos N años?").

- ✅ Ventajas: fácil de explicar a un usuario dummy ("mirá solo lo más reciente"); no descarta la idea de monotonía si se combina con la lógica actual sobre la ventana recortada.
- ❌ Desventajas: introduce un número nuevo (`N`); no resuelve el bug de forma confiable/sistemática (depende de dónde cae el año de baja relativo a `N`); reduce la cantidad de historia que el bot efectivamente evalúa, lo cual puede ocultar volatilidad real en vez de tolerarla explícitamente.
- 📌 Mejor cuando: se prioriza una lectura "reciente" del negocio sobre una lectura de ventana completa, y se acepta el riesgo de que la corrección del caso NVIDIA sea incidental, no garantizada, para casos futuros similares.

### Recomendación del `architect` — CONFIRMADA por Daniela (2026-07-30)

Opción A. Es el cambio más chico (una línea menos, cero líneas nuevas de lógica), no introduce ningún threshold nuevo que haya que justificar y mantener después (evita repetir el patrón de "¿por qué este número y no otro?" que ya generó preguntas bloqueantes en specs anteriores), y ataca la causa raíz exacta que diagnosticamos (la exigencia de monotonía, no el tamaño de la ventana). El trade-off que acepta (no distinguir "sube-baja-sube mucho" de "cae mucho y repunta apenas") ya existe hoy de forma análoga en `calculate_cagr()` para Graham/DCF, así que no es un estándar nuevo para el proyecto — es alinear Pilares con el mismo criterio que el resto del motor de valoración ya usa. **Esta recomendación fue confirmada por Daniela como decisión final — ver "Resolución de las preguntas bloqueantes" al principio de esta Parte.**

## Preguntas abiertas — Parte 1

**Ninguna.** Las 2 preguntas bloqueantes que dejó esta Parte (qué opción de criterio; si ambos pilares comparten el mismo criterio) fueron resueltas explícitamente por Daniela el 2026-07-30 — ver "Resolución de las preguntas bloqueantes" al principio de la Parte 1. Quedan fijadas como:

- **Pregunta 1:** Opción A — `_es_creciente()` compara solo `historial[-1] > historial[0]`, sin threshold nuevo.
- **Pregunta 2:** mismo criterio (`_es_creciente()`) para `ingresos_crecientes` y `utilidades_crecientes`, sin diferenciación por pilar.

El spec de la Parte 1 queda completo y listo para `security` sin gating adicional.

## Criterios de aceptación — Parte 1

*(La Pregunta 1 y la Pregunta 2 ya están resueltas — los criterios de abajo usan directamente el valor fijado: Opción A, mismo criterio para ambos pilares. Nada de esto queda condicionado a una elección futura.)*

- [ ] `_es_creciente([100, 90, 80, 70, 200])` → `True` (caída sostenida los primeros 4 años, explosión el último — versión estilizada del caso NVIDIA: baja en medio/temprano, pero el extremo final es ampliamente mayor al inicial).
- [ ] `_es_creciente([100, 200, 150, 300, 500])` → `True` (una sola baja intermedia, tendencia general fuerte — forma más fiel al caso NVIDIA real: años de crecimiento con un bajón en el medio).
- [ ] `_es_creciente([100, 90, 80, 70, 60])` → `False` (caída sostenida sin repunte — sigue marcando mal, no se "arregla" nada que no debía arreglarse).
- [ ] `_es_creciente([])` y `_es_creciente([100])` → `False` (guardas existentes, sin cambios).
- [ ] `_es_creciente([100, 100])` → `False` (el último no supera estrictamente al primero — sin cambios respecto a hoy).
- [ ] Todos los tests ya existentes en `tests/test_rules.py` que dependían de la monotonía estricta (`test_pilar_ingresos_crecientes_true`, `test_pilar_ingresos_decrecientes_false`, `test_pilar_utilidades_crecientes_pero_negativas_al_final_false`) se revisan uno por uno: los que usan series ya monótonas (`[10,20,30,40,50]`, `[50,40,30,20,10]`) siguen pasando sin cambios (la Opción A es un superconjunto de la Opción actual sobre series monótonas). Ninguno de estos tests se borra — si alguno deja de tener sentido conceptual con el nuevo criterio, se marca explícitamente en el PR/commit por qué, no se borra en silencio.
- [ ] Caso NVIDIA (fixture estilizado, ver "Fixture de test" abajo — resuelve el Gap 1 detectado por `qa`) evaluado end-to-end vía `evaluate_pillars()` → `utilidades_crecientes is True` e `ingresos_crecientes is True`.
- [ ] `evaluate_pillars()` no cambia ninguna otra lógica (deuda controlada, precio razonable, ventaja competitiva) — test de regresión.

### Fixture de test — resuelve el Gap 1 detectado por `qa`

`qa` señaló que no existe hoy ningún fixture `tests/fixtures/nvda/` (solo `adobe/`, `fred/`, `fmp/`), y que ni Parte 1 ni Parte 3 pueden escribir un test 100% determinístico sin definir qué datos usar. Decisión del `architect`: **no se fabrican cifras reales de NVIDIA presentadas como si fueran datos verificados** — este proyecto tiene como norma no afirmar datos financieros sin poder confirmarlos (mismo criterio que motivó marcar "no verificado con curl real" en otras partes de este proyecto), y presentar números de memoria como si fueran el 10-K real de NVIDIA violaría ese principio. En su lugar, se formaliza como fixture reutilizable la serie **estilizada** que esta Parte ya venía usando en los ejemplos de arriba — misma forma que el caso real (una caída intermedia rodeada de crecimiento fuerte), sin pretender ser los números exactos:

```python
# tests/fixtures/crecimiento_estilizado.py — NUEVO
"""Series estilizadas para testear el criterio de crecimiento (Parte 1,
SDD_fix_crecimiento_y_redaccion.md) y el mensaje completo de regresión de
formato (Parte 3, mismo spec). Deliberadamente NO son los números reales de
NVIDIA -- son una forma estilizada que reproduce la forma del caso real (una
caída intermedia rodeada de crecimiento fuerte) sin afirmar cifras
financieras no verificadas contra el 10-K real de la empresa. Si Daniela
provee los números reales más adelante, se agrega un fixture aparte con
esos datos exactos -- no reemplaza a este, que sigue siendo válido como
caso estilizado de regresión."""

HISTORIAL_INGRESOS_CASO_ESTILIZADO = [100_000, 200_000, 250_000, 400_000, 700_000]
# monótono creciente -- los ingresos de una empresa que llega a este
# análisis casi nunca caen año a año, a diferencia de las utilidades.

HISTORIAL_UTILIDADES_CASO_ESTILIZADO = [100, 200, 150, 300, 500]
# una caída intermedia (tercer año) rodeada de crecimiento fuerte -- la
# forma exacta del bug reportado por Daniela con NVIDIA (FY2023 con una
# baja de utilidades antes de la explosión de ganancias por IA).
```

Se importa desde `tests/test_rules.py` (Parte 1, test end-to-end de `evaluate_pillars`) y desde `tests/test_summary.py`/`tests/test_query_handler.py` (Parte 3, test de regresión de `chunk_for_telegram` sobre el mensaje completo) — un solo fixture, dos consumidores, sin duplicar valores ad hoc en cada test.

## Artefactos a crear/modificar — Parte 1

- `src/investbot/rules.py` → `_es_creciente()` (líneas 128-136), reescrita según la Opción A confirmada (ver pseudocódigo final arriba). `evaluate_pillars()` (líneas 138-169) no cambia — sigue delegando en `_es_creciente()` sin ningún parámetro nuevo, para ambos pilares por igual.
- `tests/fixtures/crecimiento_estilizado.py` → **nuevo archivo** (ver "Fixture de test" arriba), compartido con Parte 3.
- `tests/test_rules.py` → nuevos casos de la lista de arriba; revisión de los 3 tests existentes citados; test end-to-end usando el fixture nuevo.

## Restricciones — Parte 1

- No se toca `calculate_cagr()` ni ningún otro cálculo de `valuation.py` — el bug es específico de `rules.py::_es_creciente()`, no del motor de valoración.
- **`_es_creciente()` no lleva ningún parámetro de threshold** (Pregunta 1, resuelta: Opción A) — si en el futuro alguien propone agregar uno (tolerancia de bajas, tamaño de ventana), es una spec patch nueva con acuerdo explícito de Daniela, no un ajuste libre de `implementer`.
- `evaluate_pillars()` no gana ningún parámetro para diferenciar el criterio de `ingresos_crecientes` de `utilidades_crecientes` (Pregunta 2, resuelta: mismo criterio para ambos).
- `deuda_controlada`, `precio_razonable` y `ventaja_competitiva` no cambian.

---

# PARTE 2 — Redacción del mensaje (revisión de copywriting)

## Contexto

El mensaje creció mucho desde el MVP: Título → Veredicto → Intro → Ratios clave → Extras (ROE/deuda/dividendos) → Valor Justo (3 escenarios) → Pilares → Contexto de mercado (momentum + peers + VIX) → Eventos corporativos → Encaje de riesgo → Notas de transparencia. Revisión completa de `src/investbot/summary.py` con foco en redacción — **no cambia qué datos se muestran, solo cómo se redactan**. Se listan hallazgos concretos con antes/después; ninguno requiere una decisión de threshold, así que no son bloqueantes de la misma forma que la Parte 1 — son propuestas para que Daniela apruebe o ajuste antes de pasar a `qa`.

## Hallazgo 1 — Redundancia real entre Veredicto y Encaje de riesgo (misma conclusión, dos veces)

`build_veredicto_section()` (líneas 502-539) ya incluye el resultado del encaje de riesgo dentro de la única frase del veredicto:

> *"...y SÍ encaja con tu perfil de riesgo (moderado)."*

Y más abajo, `build_risk_fit_section()` (líneas 414-427) repite la misma conclusión con más detalle:

> *"Encaje con tu perfil de riesgo (moderado): SÍ encaja — es renta variable con beta de 1.66."*

No es un error — cada mención sirve un propósito distinto (resumen de una línea vs. detalle con explicación) — pero al usar casi la misma frase literal ("encaja con tu perfil de riesgo") en dos lugares del mismo mensaje, se lee como que el bot se repite. **Propuesta**: mantener ambas menciones (la información es válida en los dos niveles de lectura), pero variar el fraseo del Veredicto para que quede claro que es un adelanto, no una repetición:

- Antes: `"...con 3/4 pilares sólidos, y SÍ encaja con tu perfil de riesgo (moderado). Mirá con cuidado: utilidades."`
- Después: `"...con 3/4 pilares sólidos. Encaje de riesgo: SÍ (detalle más abajo). Mirá con cuidado: utilidades."`

## Hallazgo 2 — Oración final del Veredicto es un párrafo denso de una sola línea

`build_veredicto_section()` arma una sola oración larga con 3-4 datos distintos separados por comas (precio/pilares/encaje/cuidado). Es información correcta pero difícil de escanear rápido — precisamente el tipo de cosa que Daniela describió como "la llena de un montón de cosas". Ver Parte 3 (Hallazgo de formato) para la propuesta de separarlo en líneas — acá solo se marca que, más allá del formato, el contenido puede reformularse en oraciones más cortas en vez de una sola oración con 3 cláusulas coordinadas.

- Antes: `"*En una frase:* parece *barata* según el valor justo estimado (escenario conservador), con 3/4 pilares sólidos, y SÍ encaja con tu perfil de riesgo (moderado). Mirá con cuidado: utilidades."`
- Después (ver Parte 3 para el formato completo con título separado): `"Parece *barata* según el valor justo estimado (escenario conservador), con 3/4 pilares sólidos.\nEncaje de riesgo: SÍ (detalle más abajo).\nMirá con cuidado: utilidades."`

## Hallazgo 3 — Frase con conector faltante en `_PEERS_NOTE_FINNHUB`

`summary.py` línea 553-560:

> *"Esta consulta, la lista de comparables (peers) se obtuvo dinámicamente de Finnhub (agrupados por sub-industria, no por el sector completo) — no es la lista fija de peers.py."*

"Esta consulta, la lista..." es una construcción sin conector — se lee como si faltara una preposición. Propuesta:

- Antes: `"Esta consulta, la lista de comparables (peers) se obtuvo dinámicamente de Finnhub..."`
- Después: `"En esta consulta, la lista de comparables (peers) vino de Finnhub (agrupados por sub-industria, no por el sector completo) — no es la lista fija de peers.py."`

No toca `_PEERS_NOTE_FIJO` (líneas 542-551) — ese texto no tiene el problema de redacción, y además está protegido por el test `test_peers_note_default_menciona_eleccion_manual_y_no_fmp` (`tests/test_summary.py` línea 608), que exige literalmente las substrings `"elegida a mano"` y `"no la arma FMP"` — cualquier reescritura de `_PEERS_NOTE_FIJO` tiene que preservar esas dos frases exactas o el test se rompe (no es una razón para no tocarlo si hiciera falta, pero no hace falta: no tiene el problema de redacción del Hallazgo 3).

## Hallazgo 4 — Repetición de la construcción "no es lo mismo que" / "no reemplaza"

Se repite la misma estructura retórica de aclaración-por-negación 3 veces en el mensaje:
- VIX: *"NO es lo mismo que un índice compuesto de sentimiento tipo 'Fear & Greed'..."* (línea 358-361).
- WACC: *"...no reemplaza el WACC que armaría un analista con datos de mercado más completos."* (línea 683-685).
- Eventos corporativos / disclaimer final: *"...esos se muestran sin resumir, no reemplazan leer el filing completo."* (línea 692-693).

Es un patrón menor (3 apariciones en un mensaje largo no es alarmante) pero, ya que se está revisando con ojo editorial, se puede variar el verbo en al menos una de las 3 para que no se sienta repetitivo si Daniela lee mensajes seguidos de distintos tickers.

**Texto final cerrado (resuelve el Gap 3 detectado por `qa`):** la primera propuesta de este spec ("...es más simple que el WACC que calcularía un analista...") perdía el matiz cautelar del original — `security` sugirió, sin bloquear, mantener algún verbo de "no sustituye/no reemplaza" para que el disclaimer siga dejando claro que esto NO es un reemplazo válido del WACC de un analista, solo una aproximación más simple. Se incorpora esa sugerencia:

- Antes: `"...no reemplaza el WACC que armaría un analista con datos de mercado más completos."`
- Después (final): `"...es una aproximación más simple, no un sustituto completo del WACC que armaría un analista con datos de mercado más completos."`

Combina ambos objetivos: varía el verbo inicial ("es una aproximación más simple" en vez de repetir "no reemplaza" de nuevo) y preserva la cautela explícita ("no un sustituto completo") que `security` pidió no perder. Este hallazgo sigue siendo el de menor prioridad de los 4 — es un nice-to-have, no una confusión real para quien lee — pero ya no queda condicionado a que Daniela fije el texto: esta es la redacción a implementar.

**Nota para el test de `qa` (`test_wacc_nota_mantiene_matiz_cautelar`, listado más abajo como condicional "solo si Daniela fija el texto final"):** con el texto ya cerrado, el assert debe verificar la substring literal `"no un sustituto completo"` (no `"no sustituye"` ni `"no reemplaza"`, que ya no aparecen en el texto final) — `qa` ajusta el test a este string exacto al entrar a Ralph Loop, tal como su propia sección ya anticipaba ("ajustar al verbo exacto que Daniela elija").

## Preguntas abiertas — Parte 2

Ninguna bloqueante. Los 4 hallazgos son propuestas de texto — Daniela puede aprobar, ajustar el fraseo puntual, o descartar cualquiera de los 4 de forma independiente sin afectar a los demás ni a las Partes 1/3/4.

## Criterios de aceptación — Parte 2

- [ ] Ningún hallazgo de esta parte cambia qué datos numéricos/booleanos se muestran — solo el texto que los rodea (verificable comparando qué variables se interpolan en cada f-string antes/después).
- [ ] `build_veredicto_section()` ya no repite literalmente la frase "encaja con tu perfil de riesgo" en el mismo tono que `build_risk_fit_section()` (Hallazgo 1).
- [ ] `_PEERS_NOTE_FINNHUB` no empieza con la construcción sin conector "Esta consulta, la lista..." (Hallazgo 3).
- [ ] Tests existentes que dependen de substrings exactos (`test_peers_note_default_menciona_eleccion_manual_y_no_fmp`, `test_modelo_formulas_multiplos_aclara_calculo_del_bot`, y cualquier otro listado en `tests/test_summary.py` que haga `assert "..." in text` sobre un texto tocado por esta Parte) se revisan uno por uno antes de tocar el texto correspondiente — ninguno se rompe sin que `implementer` lo señale explícitamente como cambio intencional a validar con `qa`.

## Artefactos a crear/modificar — Parte 2

- `src/investbot/summary.py` → `build_veredicto_section()` (Hallazgo 1 y 2), `_PEERS_NOTE_FINNHUB` (Hallazgo 3), línea del WACC dentro de `transparency_lines` en `build_summary_parts()` (Hallazgo 4).
- `tests/test_summary.py` → ajustar cualquier assert de substring literal que choque con el nuevo texto.

## Restricciones — Parte 2

- No se agrega, quita ni reformula ningún **dato** (número, booleano, fórmula) — solo prosa.
- No se toca `_PEERS_NOTE_FIJO` (protegido por test existente, y no tiene el problema de redacción de esta Parte).
- No se toca ningún string de `rules.py`/`valuation.py`/`market_context.py`/`peers.py`/`corporate_events.py` (esos módulos no arman texto, `summary.py` es el único responsable de redacción — ver docstring de módulo, línea 8-11).

---

# PARTE 3 — Formato y espaciado visual del mensaje

## Contexto

Daniela describió el mensaje como que "la llena de un montón de cosas" sin buena separación visual. Aclaración recibida durante este mismo spec: además de mejorar el espaciado entre bloques, quiere que **cada sección tenga un título claro y bien marcado** que la divida visualmente — de forma consistente en todas las secciones, no solo en algunas como hoy. Telegram con Markdown limitado no tiene headers reales ni tablas — el único recurso disponible es negrita (`*texto*`), itálica (`_texto_`), bullets (`-`) y saltos de línea. No se puede romper `chunk_for_telegram` (límite de 4096 caracteres, `SDD_contenido_financiero_explicado.md` Ampliación #2) — cualquier cambio de formato debe seguir tratando cada elemento de la lista que devuelve `build_summary_parts()` como una unidad atómica no divisible por `chunk_for_telegram` (el chunking parte por sección completa, nunca a mitad de una).

## Hallazgo 1 — Inventario: qué secciones tienen título en negrita hoy y cuáles no

| Sección | ¿Tiene título en negrita? | ¿Título en línea propia (separado del contenido)? | Acción propuesta |
|---|---|---|---|
| Título (empresa) | Sí — `*{company_name} ({ticker})*` | N/A (es el título del mensaje entero) | Sin cambios |
| Veredicto | Parcial — `*En una frase:*` pero pegado al contenido en la misma línea | No | Título propio `*Veredicto:*` en su propia línea (Hallazgo 2) |
| Intro (Tienda de Limonada) | No — párrafo sin título | No | Agregar título (Hallazgo 3) |
| Ratios clave | Sí — `*Ratios clave:*` | Sí | Sin cambios — es el estándar a replicar |
| Extras (Rentabilidad/deuda/dividendos) | Sí — `*Rentabilidad, deuda de largo plazo y dividendos:*` | Sí | Sin cambios |
| Valor Justo | Sí — `*Rango de Valor Justo estimado (...):*` | Sí | Sin cambios en el título; sí se separan sub-bloques internos (Hallazgo 4) |
| Pilares | Sí — `*Pilares de buena empresa:*` | Sí | Sin cambios |
| Contexto de mercado | Sí — `*Contexto de mercado:*` | Sí | Sin cambios en el título; sí se separan sub-bloques internos (Hallazgo 4) |
| Eventos corporativos | Sí — `*Eventos corporativos recientes (SEC EDGAR):*` | Sí | Sin cambios |
| Encaje de riesgo | Parcial — título pegado al veredicto de encaje en la misma línea | No | Separar título de contenido (Hallazgo 5) |
| Notas de transparencia | No — arranca directo con la primera nota en itálica, sin título | No | Agregar título (Hallazgo 6) |

**Estándar propuesto**: todo título de sección es `*Texto del título:*` en su propia línea, seguido del contenido a partir de la línea siguiente — nunca `*Título:* contenido` pegado en la misma línea. Es exactamente el patrón que ya usan 6 de las 10 secciones de contenido (Ratios clave, Extras, Valor Justo, Pilares, Contexto de mercado, Eventos corporativos) — se extiende a las 3 que hoy lo rompen (Veredicto, Encaje de riesgo) y se agrega a las 2 que no tienen título (Intro, Notas de transparencia).

**Aclaración explícita (cierra el Gap 2 detectado por `qa`): el Título de la empresa (`*{company_name} ({ticker})*`, índice 0 de `build_summary_parts()`) queda EXCLUIDO por diseño de este estándar y de cualquier test genérico que lo verifique.** No es una sección de contenido — es el título del mensaje completo, el equivalente al "asunto" de todo el análisis, no una de las 10 secciones que informan un dato o grupo de datos. No sigue el patrón `*Texto:*` (no tiene dos puntos, no tiene contenido debajo en una línea separada — el nombre y ticker de la empresa SON el contenido). El estándar de esta Parte 3 aplica a las **10 secciones de contenido**: Veredicto, Intro, Ratios clave, Extras, Valor Justo, Pilares, Contexto de mercado, Eventos corporativos, Encaje de riesgo, Notas de transparencia. Cualquier test que recorra `build_summary_parts()` verificando el estándar de título debe excluir explícitamente el índice 0 (o iterar sobre `parts[1:]`) — no es un caso límite a resolver "sobre la marcha", es parte de la definición del estándar desde esta versión del spec.

## Hallazgo 2 — Veredicto: separar título de contenido

```python
# build_veredicto_section() — antes
return (
    f"*En una frase:* {precio_txt}, con {solidos}/4 pilares sólidos, "
    f"y {encaje_txt} con tu perfil de riesgo ({risk_fit.get('perfil')})."
    f"{cuidado_txt}"
)

# después
lineas = [
    "*Veredicto:*",
    f"{precio_txt.capitalize()}, con {solidos}/4 pilares sólidos.",
    f"Encaje de riesgo: {'SÍ' if risk_fit.get('encaja') else 'NO'} (detalle más abajo).",
]
if cuidado_txt:
    lineas.append(cuidado_txt.strip())
return "\n".join(lineas)
```

Combina el Hallazgo 1/2 de la Parte 2 (redacción) con el título separado — mismo cambio de código cubre ambos.

## Hallazgo 3 — Intro: agregar título

```python
# antes
intro = (
    "Pensá en una empresa como una Tienda de Limonada: el *boletín* ..."
)

# después
intro = (
    "*Cómo leer este análisis:*\n"
    "Pensá en una empresa como una Tienda de Limonada: el *boletín* ..."
)
```

**Texto cerrado (resuelve el Gap 5 detectado por `qa`): `"*Cómo leer este análisis:*"`.** Es una decisión de redacción, no un threshold financiero, así que la fija el `architect` sin volver a preguntarle a Daniela — mismo criterio que el resto de los títulos de esta Parte, que tampoco pasaron por una pregunta bloqueante aparte. Queda descartadas las 2 alternativas mencionadas en la iteración anterior de este spec ("Antes de los números:", "Para entender lo de abajo:") — ninguna aporta una ventaja clara sobre la elegida, y fijar una sola evita que `implementer` tenga que interpretar cuál usar. Si Daniela prefiere otro texto al ver el mensaje real, es un ajuste de una palabra vía corrección menor (Regla 3 del pipeline), no una reapertura de esta Parte.

## Hallazgo 4 — Contexto de mercado y Valor Justo: separar sub-bloques internos con línea en blanco

Este es el cambio con más impacto en la sensación de "lleno de cosas". Hoy `build_market_context_section()` (líneas 289-370) arma una sola lista `lines` con **3 sub-temas distintos** (momentum/precio, comparación con peers + desglose individual, VIX) y los une con `"\n".join(lines)` (línea 370) — un solo salto de línea entre, por ejemplo, la última línea de momentum y la primera de la comparación con peers. Visualmente no hay ningún respiro entre sub-temas que no tienen relación directa entre sí.

Propuesta: agrupar `lines` en sub-bloques y unir los sub-bloques con línea en blanco (`"\n\n"`), preservando `"\n"` simple dentro de cada sub-bloque (entre una línea y su explicación en itálica, por ejemplo):

```python
def build_market_context_section(...) -> str:
    bloque_momentum = ["*Contexto de mercado:*"]
    # ... (líneas de precio/momentum, igual que hoy)

    bloque_peers = []
    # ... (comparación con peers + desglose individual, igual que hoy)

    bloque_vix = []
    # ... (VIX, igual que hoy)

    nota_final = ["\n_Nota: el momentum de arriba..._"]  # igual que hoy

    bloques = [b for b in (bloque_momentum, bloque_peers, bloque_vix, nota_final) if b]
    return "\n\n".join("\n".join(b) for b in bloques)
```

Mismo tratamiento para `build_valuation_scenarios_section()` (líneas 171-252): ya separa razonablemente el bloque de "Rango de Valor Justo" del bloque de "Valor Justo Total" con una línea en blanco (línea 236, `lines.append(f"\n*Valor Justo Total...")`) — **este patrón ya existe ahí y es el que hay que replicar en Contexto de mercado**, no una idea nueva.

## Hallazgo 5 — Encaje de riesgo: separar título de contenido

```python
# build_risk_fit_section() — antes
return (
    f"*Encaje con tu perfil de riesgo ({risk_fit['perfil']}):* {encaje_txt} — "
    f"es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}.\n"
    "_Renta variable = ..._\n"
    "_Beta mide qué tan volátil..._"
)

# después
return (
    f"*Encaje con tu perfil de riesgo ({risk_fit['perfil']}):*\n"
    f"{encaje_txt} — es {risk_fit['etiqueta_activo']} con beta de {risk_fit['beta']:.2f}.\n"
    "_Renta variable = ..._\n"
    "_Beta mide qué tan volátil..._"
)
```

Cambio mínimo: mover el `:*` de mitad de línea a fin de línea, agregar un `\n`. El contenido no cambia una palabra.

## Hallazgo 6 — Notas de transparencia: agregar título + separar cada nota con línea en blanco

Hoy (`build_summary_parts()`, líneas 669-696 armando `transparency_lines`, unida al final con `"\n".join(transparency_lines)` en la línea 708 del `parts` list): 4-5 notas de transparencia distintas (fuente FMP, peers_note, Y/treasury_source, WACC, disclaimer general) apiladas con un solo `\n` entre cada una — la sección con menos separación visual de todo el mensaje, y una de las 2 secciones de contenido sin título en negrita (junto con Intro, ver Hallazgo 3).

```python
# antes
transparency_lines = [
    "_Datos financieros (...) obtenidos de FMP._",
    f"_Nota de transparencia: {peers_note_final}_",
]
if treasury_source:
    transparency_lines.append(f"_Y (...) obtenida de: {treasury_source}._")
transparency_lines.append("_El DCF es una aproximación...")
transparency_lines.append("_Esto es una síntesis...")
...
"\n".join(transparency_lines)  # en el armado de `parts`

# después
transparency_lines = [
    "*Notas de transparencia:*",
    "_Datos financieros (...) obtenidos de FMP._",
    f"_Nota de transparencia: {peers_note_final}_",
]
if treasury_source:
    transparency_lines.append(f"_Y (...) obtenida de: {treasury_source}._")
transparency_lines.append("_El DCF es una aproximación...")
transparency_lines.append("_Esto es una síntesis...")
...
"\n\n".join(transparency_lines)  # separación real entre cada nota
```

Nota de presupuesto de caracteres: agregar 1 línea de título (~25 caracteres) y pasar de `\n` a `\n\n` entre 4-5 notas (+3-4 saltos de línea extra, ~4 caracteres) es un incremento total de ~30 caracteres sobre una sección que ya hoy puede rondar 600-800 caracteres — no acerca a esta sección al límite de 4096 de forma relevante, y aunque lo hiciera, `chunk_for_telegram`/`_split_oversized_part` (`query_handler.py` líneas 381-410) ya están diseñados para partir una sección que supere el límite sin perder contenido — no hace falta ningún cambio ahí.

## Preguntas abiertas — Parte 3

**Ninguna.** El único punto que quedaba sin cerrar (texto exacto del título de la Intro, Hallazgo 3) fue resuelto por el `architect` como corrección de redacción — no era un threshold financiero ni ameritaba volver a preguntarle a Daniela (ver Hallazgo 3, texto cerrado: `"*Cómo leer este análisis:*"`). El Gap 2 de `qa` (exclusión del Título de empresa del estándar de 10 secciones) también quedó cerrado explícitamente (ver Hallazgo 1). El spec de la Parte 3 queda completo y listo para `implementer`.

## Criterios de aceptación — Parte 3

- [ ] Las **10 secciones de contenido** de `build_summary_parts()` (todas excepto el Título de la empresa, índice 0 — ver aclaración explícita del Hallazgo 1) tienen título en negrita en su propia línea, siguiendo el estándar de la tabla del Hallazgo 1 — verificable con un test que recorra `parts[1:]` (nunca `parts[0]`) y confirme que cada string no vacío empieza con `*` seguido de texto y `:*` antes del primer `\n` (o antes del final del string para secciones de una sola línea, si las hay).
- [ ] El test de arriba **no** se aplica a `parts[0]` (Título de la empresa) — hay un test o assert explícito que confirma que `parts[0]` sigue teniendo el formato `*{company_name} ({ticker})*` sin dos puntos, para dejar constancia de que la exclusión es intencional y no un simple olvido de cobertura.
- [ ] `build_market_context_section()` separa momentum/peers/VIX/nota-final con línea en blanco (`"\n\n"` entre sub-bloques) — test que cuenta ocurrencias de `"\n\n"` dentro del resultado y confirma que es mayor a la versión actual (hoy 0 internas, sin contar la nota final que ya usa `"\n\n_Nota:"` — línea 363).
- [ ] `transparency_lines` se unen con `"\n\n"` en vez de `"\n"`, y el primer elemento es `"*Notas de transparencia:*"`.
- [ ] Ningún test existente que haga `assert "..." in text` sobre contenido de estas secciones se rompe por el cambio de separador (los separadores no forman parte del contenido que esos tests verifican, pero se revisan uno por uno igual — `tests/test_summary.py` tiene varios asserts sobre `build_market_context_section`/`build_risk_fit_section`/`build_veredicto_section`).
- [ ] `chunk_for_telegram` sigue funcionando sin cambios de código — test de regresión que arma un `build_summary_parts(...)` completo con datos sintéticos representativos (reutilizando `tests/fixtures/crecimiento_estilizado.py`, ver Parte 1 — resuelve el Gap 1 de `qa`, no hace falta un fixture HTTP real de NVDA para esto: `build_summary_parts`/`chunk_for_telegram` son funciones puras) confirmando que el número de chunks resultante es razonable (no se dispara por el pequeño incremento de caracteres de esta Parte).
- [ ] Ninguna sección deja de aparecer, ninguna se duplica — test de regresión sobre `build_summary_parts()` comparando el set de "títulos de sección" esperado antes/después.

## Artefactos a crear/modificar — Parte 3

- `src/investbot/summary.py` → `build_veredicto_section()`, intro (dentro de `build_summary_parts()`), `build_market_context_section()`, `build_valuation_scenarios_section()` (solo si Daniela quiere el mismo tratamiento de sub-bloques ahí, ver Hallazgo 4 — ya tiene una línea en blanco parcial), `build_risk_fit_section()`, `transparency_lines` (dentro de `build_summary_parts()`).
- `tests/test_summary.py` → nuevos tests de estructura (títulos en negrita, separación de sub-bloques) + revisión de asserts existentes sobre contenido de las secciones tocadas.

## Restricciones — Parte 3

- No se cambia el límite de 4096 caracteres ni la lógica de `chunk_for_telegram`/`_split_oversized_part`/`_with_continuation_prefixes` (`query_handler.py`) — esta Parte solo toca cómo se arma el texto de cada sección en `summary.py`, no cómo se particiona en mensajes.
- No se agrega ningún separador visual tipo línea horizontal (`⎯⎯⎯⎯⎯⎯`, `---`, etc.) entre secciones — Telegram Markdown no tiene regla horizontal real, y agregar una línea de caracteres decorativos iría en contra del objetivo (menos "lleno de cosas", no más). Si Daniela lo pide explícitamente más adelante, es una spec patch aparte.
- El orden de las secciones (Decisión #5 de `SDD_peers_dinamicos_y_eventos_corporativos.md`, ya vigente) no cambia — esta Parte es puramente de formato interno de cada sección, no de reordenamiento.

---

# PARTE 4 — Auditoría de procedencia de datos (nueva pasada completa)

## Contexto

Hubo 2 auditorías previas de "de dónde sale cada dato": Decisión #13 de `SDD_contenido_financiero_explicado.md`, y `SDD_procedencia_peers_individuales.md`. Desde entonces se agregaron: Extras (ROE/deuda/dividendos), VIX, escenarios de Valor Justo, peers dinámicos vía Finnhub, y eventos corporativos vía SEC EDGAR (`SDD_peers_dinamicos_y_eventos_corporativos.md`, ya implementada). Esta Parte es una auditoría **nueva y completa** de todo lo que el bot muestra hoy en producción, sección por sección de `build_summary_parts()`, confirmando procedencia clara y dummy-friendly (FMP / FRED-Treasury.gov / Finnhub / SEC EDGAR / cálculo propio del bot).

## Metodología

Se recorrió `build_summary_parts()` (líneas 579-711 de `summary.py`) sección por sección, cruzando cada dato mostrado contra su origen real en `query_handler.py`/`fmp_client.py`/`treasury_client.py`/`finnhub_client.py`/`sec_edgar_client.py`/`rules.py`/`valuation.py`/`market_context.py`/`peers.py`.

## Hallazgo A (bien resuelto, sin acción) — Extras, Eventos corporativos, Beta, VIX, Y

Estas 5 áreas ya declaran su fuente de forma explícita e inline, junto al dato:
- **Extras** (`build_extras_section`, líneas 433-499): cada uno de los 5 campos (ROE, Debt-to-Equity, Net Debt/EBITDA, Dividend Yield, Payout Ratio) tiene su propia etiqueta `_(dato de FMP)_` o `_(fórmula: ... — dato ya calculado por FMP)_` pegada a la línea. Ejemplar — es el estándar más claro de todo el mensaje.
- **Eventos corporativos** (`build_corporate_events_section`, líneas 373-392): nota final explícita, "_Fuente: SEC EDGAR (oficial, gratis, sin API key)..._".
- **Beta** (`build_risk_fit_section`, línea 426): "_...Dato de FMP._" explícito.
- **VIX** (`build_market_context_section`, líneas 352-361): "_...Dato de FMP (símbolo ^VIX)._" explícito.
- **Y / tasa del bono** (`build_summary_parts`, líneas 674-677): línea dedicada, `f"_Y (tasa libre de riesgo) obtenida de: {treasury_source}._"`, con `treasury_source` siendo literalmente `"FRED (serie DGS20)"` o `"Treasury.gov (fallback)"` — no hay ambigüedad de cuál de las 2 fuentes respondió esta consulta puntual.

## Hallazgo B (hueco nuevo) — "Ratios clave" no dice explícitamente que son cálculo del bot, no campos de FMP

`build_summary_parts()`, sección `ratios_lines` (líneas 623-649): Liquidez, Margen bruto, PER y P/S muestran su fórmula (`_(fórmula: Activos Circulantes / Pasivos Circulantes)_`, etc.) pero **ninguna línea dice explícitamente que estos 4 valores los calcula el bot a partir de datos crudos de FMP** — a diferencia de Extras, donde cada línea sí distingue "dato ya calculado por FMP" (ROE, Debt-to-Equity, etc. vienen precalculados de `/key-metrics`) de un cálculo propio. Mostrar la fórmula sugiere implícitamente "esto lo calculamos nosotros", pero no lo dice con la misma claridad que el resto del mensaje — es una inconsistencia de nivel de explicitud entre dos secciones que, en los hechos, tienen el mismo tipo de distinción que hacer (cálculo propio vs. dato directo de FMP) y la resuelven con distinto nivel de claridad.

Referencia cruzada: esto es lo mismo que ya distingue el docstring de `rules.py::extract_key_metrics_extras` (líneas 90-102, "*No calcula nada — FMP ya precalcula estos campos*") vs. las funciones `calculate_eps`/`calculate_gross_margin`/`calculate_liquidity_ratio`/`calculate_per`/`calculate_ps` (líneas 14-78, todas fórmulas que el bot ejecuta sobre campos crudos de `/income-statement`/`/balance-sheet-statement`/`/quote`) — el código ya sabe la diferencia, el mensaje no la comunica con la misma claridad en ambos lados.

**Propuesta** (no bloqueante, cruza con la Parte 2 de redacción — mismo criterio, no se duplica el trabajo si se implementa junto): agregar una sola línea consolidada debajo del título de la sección, en vez de repetir la aclaración en cada bullet (evita el patrón "4 paréntesis casi iguales" que sería más ruido, no menos):

```python
ratios_lines = [
    "*Ratios clave:*",
    "_(calculados por el bot a partir de datos de FMP — no son campos directos de la API)_",
]
```

## Hallazgo C (hueco nuevo) — Precio/momentum en "Contexto de mercado" no tienen atribución inline

Las líneas de `build_market_context_section()` (líneas 301-322: "Cotiza a $X, un Y% por debajo de su máximo de 52 semanas...", "Por encima de su promedio de 50 días...") no tienen ninguna nota de fuente inline, a diferencia del VIX que sí la tiene 10 líneas más abajo en la misma sección. **Mitigante ya existente**: la nota general del pie del mensaje ya cubre esto explícitamente — "_Datos financieros (ingresos, deuda, flujo de caja, **cotización**, etc.) obtenidos de Financial Modeling Prep (FMP)._" (línea 670-671) — la palabra "cotización" ya incluye precio/máximos/mínimos/promedios móviles. No es un hueco de **desinformación** (la fuente está declarada en algún lugar del mensaje), pero sí de **cercanía** — la nota está al final del mensaje, potencialmente varios "chunks" de Telegram después de donde aparece el dato (ver Parte 3 sobre estructura). No se propone una línea nueva acá (agregaría ruido a una sección que la Parte 3 ya busca aliviana), se deja documentado como aceptado-por-diseño: la cobertura existe, pero está lejos. Si Daniela quiere atribución más cercana en el futuro, es candidato a spec patch separado, no de esta Parte.

## Hallazgo D (hueco nuevo) — Etiquetas de Eventos corporativos son traducción del bot, no texto literal de SEC EDGAR

`corporate_events.py::RELEVANT_8K_ITEMS` (líneas 20-29) mapea los códigos oficiales de Item de un 8-K ("1.01", "5.02", etc.) a etiquetas en español ("Nuevo contrato importante", "Cambio de directivos o ejecutivos") — son **traducciones/interpretaciones del bot** de una clasificación oficial de la SEC, no texto que la SEC provee en ese idioma ni con esa redacción exacta. La nota de fuente actual (`build_corporate_events_section`, líneas 386-391) dice "Fuente: SEC EDGAR..." sin aclarar que la etiqueta puntual ("Cambio de directivos") es una traducción dummy-friendly del bot del código oficial de Item, no una cita textual del filing. Es un hueco menor — el link al filing original siempre está presente para quien quiera verificar — pero vale la pena una aclaración de una frase.

**Propuesta**:

```python
# antes (línea 386-391)
"  _Fuente: SEC EDGAR (oficial, gratis, sin API key) — formularios "
"8-K que la empresa está obligada a presentar por ley ante eventos "
"materiales. El bot NO resume el contenido legal del filing (fuera "
"de alcance, riesgo de alucinación sobre texto legal) — mostramos "
"fecha + tipo de evento + link para que lo leas vos si te interesa._"

# después — agrega una cláusula sobre las etiquetas
"  _Fuente: SEC EDGAR (oficial, gratis, sin API key) — formularios "
"8-K que la empresa está obligada a presentar por ley ante eventos "
"materiales. El bot NO resume el contenido legal del filing (fuera "
"de alcance, riesgo de alucinación sobre texto legal) — mostramos "
"fecha + tipo de evento (la etiqueta es una traducción del bot del "
"código oficial de Item de la SEC, no una cita textual) + link para "
"que lo leas vos si te interesa._"
```

## Hallazgo E (bien resuelto, sin acción) — Fuente de peers (Finnhub vs. fijo)

`_build_peers_note()` (líneas 563-576) ya distingue explícitamente si esta consulta usó Finnhub o el respaldo fijo, y el desglose individual por peer (`_build_peer_pe_breakdown_line`, líneas 127-155) ya aclara "PER individual calculado por el bot como 1 / earningsYield — earningsYield sí es un dato de FMP, el PER no". Mismo comentario de "cercanía" que el Hallazgo C: la nota de fuente (Finnhub/fijo) vive en el pie del mensaje, lejos de donde aparecen los peers en "Contexto de mercado" — aceptado por diseño, no se propone cambio en esta Parte (la Parte 3 ya reorganiza espaciado, no orden de secciones).

## Preguntas abiertas — Parte 4

**Ninguna.** Los Hallazgos B y D quedan **cerrados como "se implementan"** (resuelve el Gap 4 detectado por `qa`, que señaló que la condicionalidad "o se retira explícitamente" no tenía mecanismo de decisión fijado antes del Ralph Loop). Ninguno de los 2 es un threshold financiero ni una decisión de negocio — son una frase de atribución de procedencia cada uno, coherente con el estándar de claridad que ya usa el resto del mensaje (Extras, Eventos corporativos ya lo hacen así, ver Hallazgo A) — no ameritan volver a preguntarle a Daniela, mismo criterio que el resto de las decisiones de redacción de esta spec. El texto exacto de cada uno (ver Hallazgo B y Hallazgo D arriba) es el que `implementer` debe usar, sin variantes. El Hallazgo C se deja explícitamente documentado como aceptado-por-diseño, no requiere acción de esta spec.

## Criterios de aceptación — Parte 4

- [ ] `ratios_lines` incluye la línea de atribución exacta del Hallazgo B (`"_(calculados por el bot a partir de datos de FMP — no son campos directos de la API)_"`) inmediatamente debajo del título `"*Ratios clave:*"`.
- [ ] La nota de fuente de Eventos corporativos (`build_corporate_events_section`) incluye la cláusula exacta del Hallazgo D (la aclaración de que la etiqueta es "una traducción del bot del código oficial de Item de la SEC, no una cita textual"), sin remover ninguna palabra del texto ya existente sobre el filing.
- [ ] No se descubre, en la revisión, ningún dato mostrado en `build_summary_parts()` que no tenga procedencia identificable en algún punto del mensaje (título de sección, línea inline, o nota general del pie) — si `qa`/`implementer` encuentran uno durante la ejecución que esta auditoría no vio, se documenta como backlog item para spec patch, no se improvisa una atribución sin pasar por este mismo nivel de revisión.

## Artefactos a crear/modificar — Parte 4

- `src/investbot/summary.py` → `ratios_lines` dentro de `build_summary_parts()` (Hallazgo B, texto cerrado); `build_corporate_events_section()` (Hallazgo D, texto cerrado).
- `tests/test_summary.py` → asserts nuevos para las 2 líneas agregadas (`test_ratios_lines_incluye_atribucion_calculo_del_bot`, `test_corporate_events_aclara_etiqueta_es_traduccion_del_bot`, ya listados por `qa` en su matriz de tests nuevos).

## Restricciones — Parte 4

- Esta Parte es puramente de auditoría/documentación + 2 propuestas de una frase cada una — no cambia ningún cálculo, ninguna fuente real de datos, ningún endpoint llamado.
- No se re-audita lo que el Hallazgo A ya confirma como resuelto (Extras, Eventos corporativos ya implementado, Beta, VIX, Y) — evita retrabajo sobre las 2 auditorías previas.

---

## Handoff → implementer

*(Este bloque quedó redactado originalmente como handoff a `security`; `security` y `qa` ya corrieron — ver "Próximo paso" actualizado abajo y "Resolución de los 5 gaps detectados por `qa`" — así que el handoff vigente es a `implementer`.)*

### Specs producidas
- Este documento (`SDD_fix_crecimiento_y_redaccion.md`), 4 partes independientes salvo por orden de prioridad de lectura.

### Criterios de aceptación base
Ver cada Parte — **las 4 partes tienen sus criterios completos y sin ninguna condición pendiente de threshold.** Parte 1 tiene sus criterios redactados directamente sobre la Opción A, ya confirmada por Daniela (no sobre una recomendación abierta) — no hace falta ningún spec patch previo para que `implementer` los use. Partes 2, 3 y 4 nunca estuvieron bloqueadas.

### Decisiones de diseño tomadas
- **Parte 1 — RESUELTA (2026-07-30):** de las 3 opciones presentadas, Daniela confirmó la **Opción A** (comparar solo `historial[-1] > historial[0]`, sin threshold nuevo) para la Pregunta 1, y confirmó **el mismo criterio para `ingresos_crecientes` y `utilidades_crecientes`** para la Pregunta 2 (`utilidades_crecientes` conserva, sin cambios, su condición adicional `net_income_historial[-1] > 0`). Las Opciones B y C quedan documentadas como descartadas, no borradas, para trazabilidad. No queda ninguna pregunta bloqueante en esta Parte.
- Parte 2: 4 hallazgos de redacción con antes/después concreto — ninguno bloqueante, ninguno cambia datos mostrados.
- Parte 3: estándar de título consistente (negrita, línea propia) para las 10 secciones de contenido (el Título de la empresa, índice 0, queda explícitamente excluido del estándar — no es una sección de contenido); separación de sub-bloques con línea en blanco en Contexto de mercado y (ya parcialmente existente) Valor Justo; título nuevo para Intro y Notas de transparencia.
- Parte 4: 2 huecos nuevos de procedencia (Ratios clave sin atribución explícita de "cálculo del bot"; etiquetas de Eventos corporativos sin aclarar que son traducción del bot) con propuesta de una frase cada uno; 1 hueco de "cercanía" (no de corrección) documentado como aceptado por diseño; el resto ya está bien resuelto por auditorías previas + la implementación de Finnhub/SEC EDGAR.

### Próximo paso (actualizado tras la revisión de `qa`)
`security` ya revisó las 4 partes sin hallazgos bloqueantes (su única sugerencia no bloqueante, sobre el matiz cautelar del WACC, ya está incorporada — ver "Resolución de los 5 gaps detectados por `qa`" abajo). `qa` agregó su sección de criterios (ver más abajo) y encontró 5 gaps de precisión, **ya resueltos por el `architect`** directamente en las Partes correspondientes (ninguno era un threshold financiero nuevo, así que no ameritaban volver a preguntarle a Daniela). El spec queda **listo para que `implementer` entre a Ralph Loop** — no queda ningún gap abierto en todo el documento.

## Resolución de los 5 gaps detectados por `qa` (2026-07-30)

`qa` señaló 5 gaps de precisión en su revisión pre-implementación (sección "4. Gaps del spec que impiden escribir un test 100% determinístico hoy", más abajo). Los 5 son decisiones de alcance/redacción, no thresholds financieros — se resolvieron directamente, sin reabrir la Parte 1 (que sigue teniendo solo sus 2 preguntas, ya resueltas por Daniela):

| Gap de `qa` | Resolución | Dónde quedó |
|---|---|---|
| **Gap 1** — no existe fixture NVIDIA | Se formaliza `tests/fixtures/crecimiento_estilizado.py` con series **estilizadas** (no cifras reales de NVIDIA sin verificar) que reproducen la forma del bug — reutilizado por Parte 1 (test end-to-end de `evaluate_pillars`) y Parte 3 (test de regresión de `chunk_for_telegram`) | Parte 1, sección "Fixture de test" |
| **Gap 2** — el estándar de "11 secciones" no excluía el Título de empresa | Aclarado explícitamente: el estándar de títulos aplica a las **10 secciones de contenido**; el Título (índice 0) queda excluido por diseño, con un criterio de aceptación dedicado a confirmar la exclusión | Parte 3, Hallazgo 1 + Criterios de aceptación |
| **Gap 3** — texto final del WACC (Hallazgo 4, Parte 2) sin fijar | Texto cerrado: `"...es una aproximación más simple, no un sustituto completo del WACC que armaría un analista con datos de mercado más completos."` — incorpora la sugerencia de `security` de conservar el matiz cautelar | Parte 2, Hallazgo 4 |
| **Gap 4** — Hallazgos B y D de Parte 4 quedaban condicionales ("o se retira") | Cerrados como **"se implementan"** — ninguno es una decisión de negocio, ambos siguen el mismo estándar de claridad que Extras/Eventos corporativos ya usan | Parte 4, Preguntas abiertas + Criterios de aceptación |
| **Gap 5** — texto del título de la Intro sin fijar | Texto cerrado: `"*Cómo leer este análisis:*"` | Parte 3, Hallazgo 3 |

Con estas 5 resoluciones, **no queda ningún gap abierto en todo el documento** — el spec está listo para que `implementer` entre a Ralph Loop.

---

## Criterios QA para Spec: Fix crecimiento y redacción [Iter-1]

**Rol:** `qa`. **Momento:** 1 (pre-implementación). `security` no dejó hallazgos bloqueantes; su única sugerencia no bloqueante (Parte 2, Hallazgo 4 WACC) se referencia abajo en el Gap 3, sin elevarla a bloqueante.

**Metodología usada para este análisis** (no es inspección de código sobre el papel — se ejecutó):
```
pytest --cov=investbot.rules --cov=investbot.valuation --cov=investbot.summary \
       --cov-report=term-missing --cov-branch -q
```
sobre la suite completa (482 tests, todos verdes hoy), más `grep` de cada substring literal que las Partes 2-4 proponen cambiar contra `tests/test_rules.py`, `tests/test_summary.py`, `tests/test_edge_cases.py` y `tests/test_query_handler.py` — para no heredar la suposición del `architect` de "probablemente varios tests rompen" sin verificarla test por test.

### Tipo de prueba principal

Unit testing (pytest), consistente con el resto del proyecto. Las 4 Partes tocan únicamente funciones puras sin I/O (`rules.py`, `summary.py`) — no se justifica integración/E2E nueva. `_es_creciente` y los builders de `summary.py` ya siguen el patrón AAA del proyecto y son directamente invocables (incluidos los helpers "privados" — el proyecto ya testea `summary._join_con_y`, `summary._build_peer_pe_breakdown_line`, `summary._build_peers_note` directamente; se recomienda el mismo tratamiento para `rules._es_creciente`).

---

### 1. Tests existentes que rompen (verificado, no asumido)

**Parte 1 (`rules.py::_es_creciente`) — ROMPE CERO TESTS.** Corrección a la suposición del `architect`: se revisaron uno por uno los 3 tests que el spec señala como candidatos (`tests/test_rules.py`) más `test_es_creciente_lista_vacia_o_un_elemento` (`tests/test_edge_cases.py:173` — no listado en ningún "Artefactos a modificar" de ninguna Parte, pero ejercita `_es_creciente` vía `evaluate_pillars`) y el fixture end-to-end de Adobe (`tests/fixtures/adobe/income_statement.json`, usado por `tests/test_query_handler.py`, sin ningún assert directo sobre `pillars.*` hoy). **Ningún historial usado en ningún test del proyecto tiene un "dip" intermedio** — son series 100% monótonas crecientes, 100% monótonas decrecientes, o casos de guarda (`[]`, `[5]`). Como la Opción A es un superconjunto estricto de la regla vieja sobre series monótonas, el resultado es idéntico antes/después en los 3:
- `test_pilar_ingresos_crecientes_true` (línea 108): `[10,20,30,40,50]` monótona → sin cambio.
- `test_pilar_ingresos_decrecientes_false` (línea 120): `[50,40,30,20,10]` monótona *decreciente* → `historial[-1]=10 < historial[0]=50` → sigue `False`. **Este test no ejercita el caso "1 dip → pasa a True" que el spec suponía que rompería** — es monótona en toda su extensión, sin repunte final.
- `test_pilar_utilidades_crecientes_pero_negativas_al_final_false` (línea 132): `[-5,-3,-1]` ya era monótona bajo la regla vieja (`-5<=-3<=-1` se cumple) y `-1>-5` — la regla vieja *ya* devolvía `True` acá. Lo que hace `False` a `utilidades_crecientes` es el guard adicional `net_income_historial[-1] > 0` (`-1>0` es `False`), que esta spec no toca. Sin cambio.
- `test_es_creciente_lista_vacia_o_un_elemento` (test_edge_cases.py:173): `revenue_historial=[]`, `net_income_historial=[5]` → ambos guards (`not historial` / `len<2`) intactos en la Opción A. Sin cambio.

**Conclusión Parte 1:** todos los tests nuevos de esta Parte son 100% aditivos — no hay que tocar ni un assert existente. Esto no exime el criterio del `architect` de "revisar uno por uno" (ya se hizo, con este resultado).

**Parte 2 + Parte 3 (`summary.py::build_veredicto_section`) — ROMPEN 8 TESTS, todos en `tests/test_summary.py`, todos por el mismo cambio de código** (Hallazgo 1+2 de Parte 2 y Hallazgo 2 de Parte 3 son, según el propio spec, "el mismo cambio de código" — se analizan juntos):

| Test | Línea | Por qué rompe exactamente |
|---|---|---|
| `test_veredicto_4_de_4_pilares_y_encaja` | 730-744 | `assert "SÍ encaja" in text` — el texto nuevo dice `"Encaje de riesgo: SÍ (detalle más abajo)."`; la frase "SÍ encaja" ya no vive en `build_veredicto_section` (sigue en `build_risk_fit_section`, pero este test llama al Veredicto solo). |
| `test_veredicto_precio_razonable_false_dice_cara` | 746-758 | `assert "parece *cara*" in text` — con `precio_txt.capitalize()` el texto pasa a `"Parece *cara* según..."` (mayúscula inicial), la substring en minúscula deja de matchear. |
| `test_veredicto_precio_razonable_none` | 761-772 | `assert "no pude determinar si está cara o barata" in text` — mismo problema de `.capitalize()`: pasa a `"No pude determinar..."`. |
| `test_veredicto_no_encaja` | 790-800 | `assert "NO encaja" in text` — el texto nuevo dice `"...NO (detalle más abajo)."`, no `"NO encaja"`. |
| `test_veredicto_es_el_segundo_bloque_de_la_respuesta` | 803-808 | `text.index("*En una frase:*")` — el título de la sección cambia literalmente a `"*Veredicto:*"`. |
| `test_veredicto_peor_escenario_no_crashea` | 818-830 | Combina 2 problemas: `"no pude determinar..."` (capitalize) + `"NO encaja"` (frase ya no existe). |
| `test_orden_completo_de_build_summary_con_extras` | 1076-1090 | `text.index("*En una frase:*")` — mismo problema de título, ahora sobre `build_summary()` completo. |
| `test_orden_completo_de_build_summary_sin_extras` | 1093-1107 | `text.index("*En una frase:*")` — ídem. |

Ninguno requiere rediseño, todos son curables cambiando el string esperado — pero **el `architect` no los señaló explícitamente** (mencionó genéricamente "tests que dependen de substrings exactos" para Parte 2 y "se revisan uno por uno" para Parte 3). Son los únicos 8 tests que de hecho rompen en todo el documento.

**Verificado que NO rompen** (no asumido):
- `test_summary_indica_encaja` / `test_summary_indica_no_encaja` — llaman `_build_summary()` completo; "SÍ encaja"/"NO encaja" sigue viviendo intacto en `build_risk_fit_section` (Hallazgo 5 de Parte 3 solo mueve el `:` de lugar, no toca `encaje_txt`).
- `test_peers_note_default_menciona_eleccion_manual_y_no_fmp`, `test_build_peers_note_finnhub_menciona_finnhub_y_sub_industria` — Hallazgo 3 de Parte 2 reescribe solo la apertura de `_PEERS_NOTE_FINNHUB` ("Esta consulta, la lista..." → "En esta consulta..."); ninguna substring que estos tests verifican vive en esa apertura.
- `test_wacc_nota_contiene_costo_promedio_ponderado_y_calculo_propio` — Hallazgo 4 de Parte 2 solo toca la cláusula final ("no reemplaza el WACC..."), no las substrings `"Costo Promedio Ponderado de Capital"` ni `"cálculo propio del bot"` que verifica este test.
- Todos los tests de `build_market_context_section` / `build_valuation_scenarios_section` — Hallazgo 4 de Parte 3 solo agrega `"\n\n"` entre sub-bloques; ningún test hace conteo exacto de saltos de línea hoy, todos son `assert "..." in text`.
- Todos los tests de `build_extras_section` / `build_corporate_events_section` — Hallazgos B y D de Parte 4 solo agregan texto nuevo, no remueven ninguna substring ya verificada.
- `test_pilar_deuda_controlada_*`, `test_pilar_ventaja_competitiva_siempre_revisar_manualmente` — no dependen de `_es_creciente`.

**Gap de proceso detectado:** `tests/test_edge_cases.py` no figura en el "Artefactos a crear/modificar" de ninguna Parte, pero contiene un test relevante a Parte 1 (línea 173). Se agrega como artefacto a *revisar* (no a modificar — sigue pasando) en la lista de abajo, para que `implementer` no lo pase por alto al hacer el commit.

---

### 2. Matriz de tests nuevos

#### Parte 1 — `rules.py`

En `tests/test_rules.py`, llamando **directamente** a `rules._es_creciente(...)` (no solo indirectamente vía `evaluate_pillars` — necesario para cobertura de ramas determinística, ver sección 3):

| Test nuevo | Input | Esperado | Cubre |
|---|---|---|---|
| `test_es_creciente_dip_temprano_explosion_final` | `[100, 90, 80, 70, 200]` | `True` | Criterio bullet 1 |
| `test_es_creciente_caso_nvidia_estilizado` | `[100, 200, 150, 300, 500]` | `True` | Criterio bullet 2 (forma más fiel al caso real) |
| `test_es_creciente_caida_sostenida_sin_repunte_false` | `[100, 90, 80, 70, 60]` | `False` | Criterio bullet 3 — no se "arregla" lo que no debía |
| `test_es_creciente_lista_vacia_false` | `[]` | `False` | Guard, rama `not historial` |
| `test_es_creciente_un_solo_elemento_false` | `[100]` | `False` | Guard, rama `len(historial) < 2` |
| `test_es_creciente_extremos_iguales_false` | `[100, 100]` | `False` | Rama `historial[-1] > historial[0]` → `False` (no estrictamente mayor) |
| `test_es_creciente_dos_elementos_creciente_true` | `[100, 200]` | `True` | Rama `historial[-1] > historial[0]` → `True` (complementa el caso anterior; sin este, la rama `True` del último `return` solo se prueba con listas de 5 elementos) |

End-to-end (reproduce el bug real reportado por Daniela — el test de mayor prioridad de todo el run):

- `test_pilar_utilidades_crecientes_caso_nvidia_dip_intermedio`: `evaluate_pillars(revenue_historial=[...] monótono, net_income_historial=[100,200,150,300,500] o valores reales de NVIDIA si Daniela los provee, liquidity=..., barata=...)` → `pillars.utilidades_crecientes is True` y `pillars.ingresos_crecientes is True`.
- `test_evaluate_pillars_deuda_precio_ventaja_no_cambian_con_dip`: mismo caso, variando `liquidity`/`barata` → `deuda_controlada`, `precio_razonable`, `ventaja_competitiva` responden exactamente igual que antes del cambio (cubre la Restricción "no cambia ninguna otra lógica" + el criterio de aceptación de regresión).

En `tests/test_edge_cases.py`: sin tests nuevos obligatorios (el existente sigue pasando sin tocar), se recomienda un comentario trazando que sigue vigente bajo la Opción A.

#### Parte 2 + Parte 3 — `build_veredicto_section`

Actualizar los 8 tests de la sección 1 con el texto nuevo exacto (no basta con "arreglar hasta que pase" — el texto esperado debe ser literal):

| Test | Assert viejo | Assert nuevo |
|---|---|---|
| `test_veredicto_4_de_4_pilares_y_encaja` | `"SÍ encaja" in text` | `"Encaje de riesgo: SÍ" in text` |
| `test_veredicto_precio_razonable_false_dice_cara` | `"parece *cara*" in text` | `"Parece *cara*" in text` |
| `test_veredicto_precio_razonable_none` | `"no pude determinar..." in text` | `"No pude determinar..." in text` |
| `test_veredicto_no_encaja` | `"NO encaja" in text` | `"Encaje de riesgo: NO" in text` |
| `test_veredicto_es_el_segundo_bloque...`, `test_orden_completo_..._con_extras`, `test_orden_completo_..._sin_extras` | `text.index("*En una frase:*")` | `text.index("*Veredicto:*")` |
| `test_veredicto_peor_escenario_no_crashea` | ambos de arriba | ambos fix combinados |

Tests nuevos:
- `test_veredicto_titulo_en_linea_propia`: `text.split("\n")[0] == "*Veredicto:*"` (Hallazgo 2 Parte 3, literal).
- `test_veredicto_no_repite_frase_encaja_con_tu_perfil_de_riesgo`: sobre el texto de `build_veredicto_section` solo, `assert "encaja con tu perfil de riesgo" not in text.lower()` (criterio de aceptación explícito de Parte 2, Hallazgo 1).
- `test_veredicto_encaje_dice_detalle_mas_abajo`: `assert "(detalle más abajo)" in text`.
- `test_summary_frase_encaja_con_tu_perfil_de_riesgo_aparece_una_sola_vez`: sobre `build_summary()` completo, `text.lower().count("encaja con tu perfil de riesgo") == 1` (debe aparecer solo en `build_risk_fit_section` — verifica que Hallazgo 1 de Parte 2 realmente eliminó la redundancia, no solo que cambió palabras).

#### Parte 2 — Hallazgo 3 (`_PEERS_NOTE_FINNHUB`)

- `test_peers_note_finnhub_no_empieza_con_esta_consulta_la_lista`: `assert not summary._PEERS_NOTE_FINNHUB.startswith("Esta consulta, la lista")`.
- `test_peers_note_finnhub_empieza_con_en_esta_consulta`: `assert summary._PEERS_NOTE_FINNHUB.startswith("En esta consulta, la lista de comparables")`.

(Los tests S1-S3 existentes ya verifican "Finnhub"/"sub-industria"/ausencia de "elegida a mano" pero ninguno fija la apertura exacta — se agrega explícito para que el Hallazgo 3 quede gateado por un test, no solo por inspección visual del PR.)

#### Parte 2 — Hallazgo 4 (WACC) — condicional, ver Gap 3 abajo

- Solo si Daniela fija el texto final (no está fijado hoy): `test_wacc_nota_mantiene_matiz_cautelar`: `assert "no sustituye" in text or "no reemplaza" in text` (ajustar al verbo exacto que Daniela elija) — recoge la sugerencia no bloqueante de `security` sin elevarla a bloqueante.

#### Parte 3 — Formato/espaciado

- `test_las_10_secciones_estandar_tienen_titulo_en_negrita_en_linea_propia`: parametrizado sobre `build_summary_parts(...)`, **excluyendo explícitamente el índice 0 (Título de empresa)** — ver Gap 2. Para cada parte restante: `re.match(r"^\*[^*]+:\*(\n|$)", parte)`.
- `test_market_context_section_separa_subbloques_con_linea_en_blanco`: cuenta `"\n\n"` en `build_market_context_section(...)` y confirma que es mayor a la baseline actual (hoy: solo la nota final la usa, `"\n\n_Nota:"` — línea 363). Fijar el valor esperado exacto una vez implementado (ej. `>= 2`: separador peers/VIX + nota final).
- `test_transparency_lines_titulo_y_doble_salto`: sobre el fragmento de transparencia (vía `_build_summary()`, localizando la sub-sección), `"*Notas de transparencia:*"` es la primera línea del bloque y las notas subsiguientes están separadas por `"\n\n"`, no `"\n"`.
- `test_risk_fit_section_titulo_en_linea_propia`: `build_risk_fit_section(...).split("\n")[0].endswith(":*")` y esa primera línea NO contiene "SÍ encaja"/"NO encaja" (Hallazgo 5 — separación real, no solo mover el símbolo).
- `test_intro_tiene_titulo`: una vez fijado el texto exacto (Gap 5 abajo), `assert "<texto fijado>" in text` apareciendo antes de "Tienda de Limonada".
- `test_ninguna_seccion_duplicada_ni_omitida`: comparar el `set` de primeras líneas de `build_summary_parts(...)` contra el set esperado (11 posibles, algunas condicionales) — criterio de aceptación explícito, cubre "ninguna sección deja de aparecer, ninguna se duplica".
- `test_chunk_for_telegram_mensaje_completo_no_dispara_explosion_de_chunks`: depende del Gap 1 (fixture NVIDIA) — número de chunks resultante acotado a un valor concreto fijado contra la baseline actual con Adobe + el delta de ~30 caracteres documentado en Hallazgo 6.

#### Parte 4 — Auditoría de procedencia (condicionales — ver Gap 4)

- Si Hallazgo B se implementa: `test_ratios_lines_incluye_atribucion_calculo_del_bot`: `assert "calculados por el bot" in text` y `assert "no son campos directos de la API" in text`, y que esa línea es la **segunda** del bloque (inmediatamente debajo de `"*Ratios clave:*"`).
- Si Hallazgo D se implementa: `test_corporate_events_aclara_etiqueta_es_traduccion_del_bot`: sobre `build_corporate_events_section(_un_evento())`, `assert "traducción del bot" in text` y `assert "no una cita textual" in text`. Regresión: no debe romper `test_build_corporate_events_section_un_evento_contenido_completo` (ya verificado que no rompe, sección 1).
- Si cualquiera de los 2 se retira explícitamente (spec lo permite): no se agrega test — se documenta en el PR/commit por qué, mismo criterio de trazabilidad que Parte 1 usa para tests que "dejan de tener sentido conceptual".

---

### 3. Piso de cobertura

Baseline real medido (suite completa, 482 tests, hoy):

| Módulo | Líneas | Ramas | Gaps existentes (pre-spec, no causados por esta spec) |
|---|---|---|---|
| `rules.py` | 100% (61/61) | 100% (14/14) | Ninguno |
| `valuation.py` | 100% (219/219) | 100% (86/86) | Ninguno — no se toca en esta spec |
| `summary.py` | 99% (231/232, falta línea 400) | 98% (88/94, 6 ramas parciales: 201→203, 400, 629→631, 631→636, 641→645, 645→651) | Ver detalle abajo |

**El estándar sigue siendo 100% líneas + 100% ramas para las funciones tocadas** — confirmado, consistente con la sección "Métricas de Calidad" del skill de `qa` ("100% en lógica crítica de negocio") y con que las 2 funciones puras principales de esta spec (`_es_creciente`, `build_veredicto_section`) ya están hoy en 100%/100% y deben seguir así después del cambio.

**Detalle de las funciones tocadas por esta spec y su piso exigido:**

- `rules.py::_es_creciente` — 100%/100% exigido y alcanzable con los 7 tests directos de la sección 2 (cubren las 2 ramas del guard + ambas ramas del `return` final).
- `summary.py::build_veredicto_section` — ya está en 100%/100% hoy (no aparece en "Missing" de la corrida con branch coverage); debe seguir así después de la reescritura. La rama nueva (`if cuidado_txt`) ya tiene cobertura de ambas ramas vía los tests existentes (actualizados) — no hace falta test adicional solo para esa rama.
- `summary.py::_build_peers_note` / `_PEERS_NOTE_FINNHUB` — constante de módulo, sin ramas; 100% trivial.
- `summary.py::build_risk_fit_section` — sin ramas (función lineal), 100% trivial, ya cubierto hoy.
- `summary.py::build_market_context_section` — ya está en 100%/100% hoy; la reestructuración en sub-bloques (Hallazgo 4 Parte 3) no debe introducir ramas nuevas (es solo reagrupación + cambio de separador) — verificar que siga en 100%/100% después, no basta con que los tests existentes sigan pasando.
- `summary.py::build_corporate_events_section` — ya está en 100%/100% hoy; el Hallazgo D de Parte 4 agrega una cláusula de texto incondicional, sin ramas nuevas.
- **`summary.py::build_summary_parts` (bloque `ratios_lines`, líneas 623-649)** — **ADVERTENCIA:** este bloque, tocado por el Hallazgo B de Parte 4, **ya tiene hoy 4 ramas parciales sin cubrir** (629→631, 631→636, 641→645, 645→651 — combinaciones de "ningún campo de ratios presente" que ningún test ejercita hoy, porque `_base_ratios()` en los tests siempre trae los 5 campos o los reemplaza de a uno). Esto es **deuda pre-existente, no introducida por esta spec** — el Hallazgo B en sí (agregar 2 líneas incondicionales después del título) no agrega ramas nuevas y no requiere cerrar este gap para pasar el gate de esta spec. Se documenta como **backlog QA del siguiente run** (no se improvisa el cierre ahora, siguiendo la regla del pipeline de no inyectar casos fuera de scope).
- `summary.py::build_pillars_section` (línea 400, rama `➖`) — **fuera de scope**: la tabla del Hallazgo 1 de Parte 3 dice explícitamente "Pilares: Sin cambios". Gap pre-existente, no se toca ni se exige cerrarlo en este run.
- `summary.py::build_valuation_scenarios_section` (rama parcial 201→203, `if formula`) — fuera de scope salvo que Daniela confirme extender el tratamiento de sub-bloques de Hallazgo 4 a esta función (queda condicional en el propio spec, "solo si Daniela quiere"); si se extiende, agregar el mismo tipo de test de conteo de `"\n\n"` que para `build_market_context_section`.

---

### 4. Gaps del spec que impiden escribir un test 100% determinístico hoy

**Gap 1 — No existe fixture NVIDIA.** `tests/fixtures/` solo tiene `adobe/`, `fred/`, `fmp/` — no hay `nvda/`. Tanto el criterio de Parte 1 ("Caso NVIDIA real, o un fixture equivalente, si no hay uno ya") como el de Parte 3 ("test de regresión con el mensaje completo de NVDA, o un fixture equivalente") asumen algo que no existe hoy. Como `evaluate_pillars`/`build_summary_parts` son funciones puras, no hace falta un fixture HTTP completo (JSON de FMP) — alcanza con listas literales inline en el test (el propio spec ya da los valores estilizados `[100,90,80,70,200]` / `[100,200,150,300,500]`). Recomendación: usar listas inline para Parte 1 (ya cubierto en la matriz de sección 2) y, para el test de chunking de Parte 3, construir un `build_summary_parts(...)` con datos sintéticos representativos (no necesariamente reales de NVIDIA) — a menos que Daniela quiera específicamente un fixture con los números reales de NVIDIA para trazabilidad del bug original, lo cual es una decisión suya, no algo que `qa` pueda inferir.

**Gap 2 — El criterio de "las 11 secciones tienen título estándar" no excluye el Título de empresa, que por diseño NO sigue el patrón.** La fila 1 de la tabla del Hallazgo 1 (Parte 3) dice explícitamente que el Título (`*{company_name} ({ticker})*`) es "N/A" y "Sin cambios" — no tiene `:` ni sigue el patrón `*Texto:*`. Pero el criterio de aceptación de Parte 3 dice literalmente "verificable con un test que recorra `parts` y confirme que cada string no vacío empieza con `*` seguido de texto y `:*` antes del primer `\n`" — aplicado tal cual a las 11 partes, el Título (índice 0) lo rompe por diseño, no por bug. El test genérico debe excluir explícitamente el índice 0 (o reformularse como "las 10 secciones estándar, excluyendo el Título"). Sin esta aclaración, un test escrito literalmente sobre el criterio tal como está redactado fallaría en un caso que el propio spec no quiere que falle.

**Gap 3 — Hallazgo 4 de Parte 2 (WACC) no tiene texto final fijado.** `security` sugirió (no bloqueante) mantener algún verbo de "no sustituye/no reemplaza" si se implementa, para no perder el matiz cautelar. El spec solo tiene el antes/después original ("no reemplaza" → "es más simple que"), sin una versión que combine ambas ideas. Sin un texto exacto fijado por Daniela antes de este Ralph Loop, no se puede escribir un `assert` determinístico para este hallazgo puntual — queda fuera del scope automatizado de este run a menos que Daniela decida el wording final primero (sugerencia concreta, no vinculante: *"...es una aproximación más simple, no un sustituto completo del WACC que armaría un analista con datos de mercado más completos."*).

**Gap 4 — Hallazgos B y D de Parte 4 son condicionales ("o se retira explícitamente") sin mecanismo de decisión fijado.** El criterio de aceptación dice "se agrega... o, si Daniela prefiere no agregarla, este criterio se retira explícitamente en vez de quedar implementado a medias" — es una decisión binaria que Daniela (o `implementer` con su visto bueno) debe tomar *antes* de escribir código, no algo que se resuelva a mitad del Ralph Loop. `qa` no puede fijar de antemano cuál de las 2 ramas de test aplica (sección 2 arriba ya lista ambos casos como condicionales) — este gap no bloquea el resto de la spec, pero si llega al `implementer` sin resolver, el primer paso del Ralph Loop debe ser esa decisión, no la implementación.

**Gap 5 — El título nuevo de la Intro (Hallazgo 3, Parte 3) no tiene texto cerrado.** El propio spec dice "texto exacto del título a confirmar con Daniela — 'Cómo leer este análisis' es una propuesta, no una decisión cerrada". No se puede escribir `test_intro_tiene_titulo` con un `assert` literal hasta que ese texto se fije — mismo patrón que el Gap 3: recomendar fijarlo antes de este Ralph Loop en vez de dejarlo como un criterio que se resuelve "sobre la marcha" durante la implementación.

### Testabilidad

- Todas las funciones tocadas (`_es_creciente`, `build_veredicto_section`, `build_risk_fit_section`, `build_market_context_section`, `build_corporate_events_section`, el bloque `ratios_lines` dentro de `build_summary_parts`) son funciones puras sin I/O — no requieren mocks, ya es el patrón establecido del proyecto.
- `_es_creciente` es técnicamente "privada" (prefijo `_`) pero el proyecto ya tiene precedente de testear helpers privados de `summary.py` directamente (`_join_con_y`, `_agrupar_peers_por_motivo`, `_build_peer_pe_breakdown_line`, `_build_peers_note`) — se recomienda el mismo tratamiento para no depender únicamente de la ruta indirecta vía `evaluate_pillars` para cerrar ramas.
- Ningún efecto secundario que aislar (no hay BD, email, ni API externa en ninguna de las 4 Partes).

### Criterio de exit de QA

- Los 8 tests de la sección 1 quedan actualizados con el texto literal nuevo (no solo "hasta que pase").
- Los tests nuevos de la sección 2 existen y pasan para las Partes 1, 2 y 3 completas; los de Parte 4 (Hallazgos B/D) y el de WACC (Parte 2, Hallazgo 4) existen solo si Daniela resuelve los Gaps 3/4/5 antes de la implementación — si no los resuelve, esos criterios se retiran explícitamente del scope de este run (no quedan "implementados a medias").
- Cobertura 100% líneas + 100% ramas en `_es_creciente` y `build_veredicto_section` (verificable con `--cov-branch`), sin empeorar la cobertura de `build_market_context_section`/`build_corporate_events_section` (ya en 100%/100%, deben seguir así).
- Sin tests ignorados/comentados para pasar CI. Flaky rate = 0 (ninguna de estas funciones tiene no-determinismo — no hay fechas, random, ni red).
- El gap pre-existente de `ratios_lines` (4 ramas parciales) y de `build_pillars_section` (línea 400) quedan documentados como **backlog QA del siguiente run**, no como bloqueantes de este.
