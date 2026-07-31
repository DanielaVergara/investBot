# Spec: Calidad mínima de peers para el modelo de Múltiplos (caso ADBE/PLTR) [Iter-1]

**Rol:** `architect`.
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `qa` agrega criterios de cobertura/testabilidad — `security` ya revisó (sección "Revisión de `security`" más abajo: sin hallazgos, lista para `qa`). **Actualización 2026-07-31 — las 3 preguntas abiertas quedaron CONFIRMADAS por Daniela, aceptando las 3 recomendaciones del `architect` tal cual** (ver detalle y justificación en la sección "Preguntas abiertas", ahora marcadas como resueltas): `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2`; no se implementa el reintento con respaldo fijo (Pregunta 2); no se implementa el filtro de outlier (Pregunta 3). Scope Freeze — `implementer` puede fijar el valor literal `2` sin ambigüedad, no queda ninguna pregunta abierta bloqueante.

---

## Contexto

Daniela consultó ADBE (Adobe) en producción. La sección de peers mostró:

```
Comparada con sus comparables del sector: Solo 1 comparable con PER válido
en tu set de peers — no hay rango suficiente para comparar.
PER de tus comparables: PLTR 259.2 — CRM, APP, DDOG y CDNS no devolvieron
un dato de FMP esta consulta.
```

Y el modelo de Múltiplos del Valor Justo dio **$4,327.99** en los 3 escenarios (Pesimista = Conservador = Optimista, porque con 1 solo peer válido `per_minimo == per_promedio == per_maximo` por construcción matemática — el mismo fenómeno ya documentado y resuelto parcialmente por `SDD_peers_dinamicos_y_eventos_corporativos.md` para el caso NVIDIA, pero con una consecuencia distinta y no resuelta: acá el número no solo queda "plano", queda **disparatado**). Ese $4,327.99, promediado con Graham ($414-583) y DCF ($251-392) en `valor_justo_total`, infló el rango final a ~$1,664-1,768 — muy lejos del precio real ($247.90) y muy lejos de lo que dan Graham/DCF por sí solos.

**PLTR (Palantir) no es un comparable de negocio razonable de ADBE (Adobe)** — analítica de datos/defensa con hype de IA vs. software creativo/diseño — cotiza a un PER de 259.2x que no tiene relación con el negocio de Adobe. PLTR vino de la fuente dinámica de Finnhub (`grouping="subIndustry"`), no de la lista fija (`PEERS_BY_SECTOR["Technology"] = ["MSFT", "ORCL", "CRM"]`, que nunca incluyó a PLTR).

### Causa raíz confirmada leyendo el código (no hipotética)

1. **`peers.py::MIN_PEERS_DINAMICOS_PARA_USAR = 3`** (línea 71) exige al menos 3 **candidatos** de Finnhub antes de aceptar su lista — Finnhub devolvió 5 (PLTR, CRM, APP, DDOG, CDNS), pasó el chequeo. Este mínimo se aplica **antes** de saber cuántos de esos 5 van a devolver un `earningsYield` utilizable de FMP.
2. **`peers.py::get_peer_pe_average`** (líneas 100-194): de los 5 candidatos, solo PLTR devolvió `earningsYield` válido esta consulta — CRM/APP/DDOG/CDNS cayeron en `peers_no_usados` con motivo `PEER_MOTIVO_SIN_DATO` (línea 161/167). El resultado: `peers_usados=["PLTR"]`, `per_promedio=per_minimo=per_maximo=259.2`. **El código no distingue este resultado de tener 3, 4 o 5 peers válidos** — sigue siendo un `PeerAverageResult` con `per_promedio` no-`None`, indistinguible aguas abajo de un caso saludable.
3. **`valuation.py::compute_valuation_scenarios`** (líneas 501-508): el único chequeo de nivel 1 sobre los peers es `elif peer_average.per_promedio is None` (0 peers válidos → excluido con motivo `"per_peers_no_disponible"`). **No hay ningún chequeo sobre `len(peer_average.peers_usados)`** — 1 peer válido cuenta exactamente igual que 5 para el propósito de incluir Múltiplos en el promedio de `valor_justo_total` (línea 594-603, `_escenario()`, que promedia `valor_justo_multiplos`/`valor_justo_graham`/`valor_justo_dcf` sin ponderar por calidad/cantidad de muestra de ninguno de los 3).
4. **`summary.py::build_valuation_scenarios_section`** (líneas 243-247) ya tiene una guarda **parcial**: si `n_peers_validos < 2`, agrega una nota de texto ("no hay rango disponible para Múltiplos: solo N comparable(s) válido(s))") — pero es **puramente informativa**, la fila de Múltiplos se sigue mostrando con su valor numérico (acá, $4,327.99) y ese valor **sigue promediándose** en `Valor Justo Total`. La nota advierte del síntoma (rango plano) pero no de la causa real del daño (un solo dato, potencialmente un outlier temático, con el mismo peso que Graham/DCF).
5. **Precedente ya existente en el propio código que la solución de esta spec debe alinear, no inventar de cero**: `market_context.py::compare_to_peers` (líneas 194-215) **ya trata `len(peers_usados) < 2` como "no comparable"** (`motivo_no_comparable = "sin_peers_validos"` con 0, `"un_solo_peer_valido"` con 1) para la sección de "Contexto de mercado". Es decir: **el propio proyecto ya decidió, en otro lugar, que 1 peer válido no es una base suficiente** — solo que esa decisión nunca se propagó al modelo de Múltiplos de Valor Justo, que sigue tratando 1 peer como si fuera una muestra completa. Esta spec cierra esa inconsistencia interna, no inventa un criterio nuevo.

### Lo que Daniela quiere resolver

Que un solo peer con PER extremo/no representativo (por mala clasificación de sub-industria de Finnhub, o por ser un outlier temático real como PLTR vs. ADBE) no pueda inflar el Valor Justo Total con el mismo peso que Graham/DCF.

**No se distingue "1 peer de mala calidad temática" de "1 peer razonable con poca cobertura de datos esta consulta"** — ver Decisión #6: técnicamente son indistinguibles con los datos que el bot tiene hoy (Finnhub no expone ningún score de "qué tan buen comparable es X para Y", solo la sub-industria), y agregar esa distinción requeriría lógica de clasificación temática nueva (fuera de alcance, ver Restricciones). El criterio de esta spec es **puramente cuantitativo**: cuántos peers con PER válido hay, no cuán "parecidos" son cualitativamente.

---

## Estado actual

- `src/investbot/peers.py::MIN_PEERS_DINAMICOS_PARA_USAR = 3` (línea 71): mínimo de **candidatos** de Finnhub (antes de consultar FMP), no de peers con dato válido. Ya resuelto y fijado por Daniela en `SDD_peers_dinamicos_y_eventos_corporativos.md` — **esta spec no lo reabre, no lo toca**.
- `src/investbot/peers.py::get_peer_pe_average` (líneas 100-194): calcula `peers_pe`/`peers_no_usados`/`per_promedio`/`per_minimo`/`per_maximo` sin ningún concepto de "mínimo de peers válidos" — cualquier cantidad ≥1 de peers válidos produce un `PeerAverageResult` con `per_promedio` numérico, sin distinción de calidad de muestra.
- `src/investbot/valuation.py::compute_valuation_scenarios` (líneas 501-508, nivel 1 de Múltiplos): único chequeo hoy es `peer_average.per_promedio is None` (0 peers válidos). No existe ningún chequeo sobre `len(peer_average.peers_usados)`.
- `src/investbot/valuation.py::compute_valuation` (líneas 324-336, versión no-escenario, ya no se usa en producción pero se mantiene y testea por paridad — ver `tests/test_valuation.py::test_valuation_adobe_scenarios`, líneas 771-785, "conservador idéntico campo a campo a `compute_valuation()`"): recibe `per_promedio_peers: Optional[float]` (un float suelto, no el `PeerAverageResult` completo) — no tiene ninguna forma de saber cuántos peers aportaron ese promedio.
- `src/investbot/summary.py::build_valuation_scenarios_section` (líneas 190-283): parámetro `n_peers_validos: int` ya existe y ya se propaga desde `query_handler.py` (línea 535: `n_peers_validos=len(peer_result.peers_usados)`) — **el dato que hace falta para el fix ya llega hasta `summary.py` hoy**, solo que hoy solo se usa para una nota de texto (líneas 243-247), no para excluir el modelo.
- `src/investbot/summary.py::MOTIVO_LABELS` (líneas 37-53): no tiene ninguna entrada para "peers válidos insuficientes" — ese concepto no existe todavía como motivo de exclusión de nivel 1.
- `src/investbot/market_context.py::compare_to_peers` (líneas 194-215): **ya implementa el mismo umbral conceptual (`< 2` peers válidos → no comparable)**, pero de forma completamente independiente de `valuation.py` — dos literales (`== 0`, `== 1`) en un archivo distinto, sin constante compartida. Esta spec no unifica ambos en una sola constante (ver Restricciones) para no tocar la lógica de clasificación de `market_context.py`, ya cerrada en una spec anterior.
- `README.md` (tabla "Presupuesto de requests por consulta", línea 84): documenta el "3-5" de peers consultados contra FMP — no dice nada hoy sobre qué pasa si, de esos 3-5, muy pocos devuelven un dato válido.

---

## Estado objetivo

1. `valuation.py` gana una nueva constante `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` y un nuevo motivo de exclusión de nivel 1, `"peers_validos_insuficientes"`, aplicado en `compute_valuation_scenarios` (y, por paridad, opcionalmente en `compute_valuation`): si `len(peer_average.peers_usados) < MIN_PEERS_VALIDOS_PARA_MULTIPLOS`, Múltiplos se excluye completamente del promedio de `valor_justo_total` en los 3 escenarios — **exactamente el mismo tratamiento que ya reciben `eps_ttm_no_positivo`/`per_peers_no_disponible`**, no un mecanismo nuevo.
2. `summary.py` muestra ese nuevo motivo con un mensaje que preserva el detalle cuantitativo que ya se mostraba hoy ("solo N comparable(s) válido(s)"), ahora en el formato ya usado por el resto de las exclusiones de nivel 1 ("Múltiplos no se pudo calcular: ...").
3. **Cero llamadas HTTP nuevas.** El dato que dispara la exclusión (`len(peer_average.peers_usados)`) ya se calcula hoy, sin ningún request adicional — es un chequeo en memoria sobre un resultado ya obtenido, igual que el resto de las guardas de nivel 1 (`eps_ttm <= 0`, `per_promedio is None`).
4. Se deja explícitamente como **preguntas abiertas para Daniela** (no decididas en esta spec): (a) el valor exacto de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS`, (b) si además de excluir del promedio conviene reintentar con el respaldo fijo dentro de la misma consulta cuando la fuente dinámica da pocos peers válidos (Alcance 1 del pedido original), y (c) si hace falta un filtro de outlier de PER dentro del propio set de peers (Alcance 3). Ver "Preguntas abiertas".

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch; las 3 preguntas de la sección "Preguntas abiertas" SÍ siguen abiertas, no están en esta lista)*

### 1. Mecanismo: reutilizar `ModeloExcluido` de nivel 1, no inventar uno nuevo

`valuation.py` ya tiene, desde el Spec Patch Iter-3, exactamente el concepto que hace falta: una exclusión "de nivel 1" (dato de entrada inválido, igual en los 3 escenarios, reportada una sola vez en `modelos_excluidos_base`) versus "de nivel 2" (el desplazamiento pesimista/optimista rompe un cálculo que en el conservador sí era válido). "Muy pocos peers válidos" es, por definición, un problema de nivel 1: no depende de qué escenario se está calculando (`per_minimo`/`per_promedio`/`per_maximo` se derivan todos del mismo `pes` list, con la misma cantidad de elementos). No hace falta ninguna estructura de datos nueva — se agrega una rama más al `if/elif` que ya arma `nivel1_multiplos`:

```python
# valuation.py — nueva constante, ubicada junto a las demás constantes de módulo
MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2  # Pregunta 1 — CONFIRMADO por Daniela
# 2026-07-31, recomendación del architect aceptada tal cual. Ya no es TBD.

# compute_valuation_scenarios() — nivel 1 de Múltiplos, único bloque tocado:
if eps_no_positivo:
    nivel1_multiplos = ModeloExcluido("multiplos", "eps_ttm_no_positivo")
elif peer_average.per_promedio is None:
    nivel1_multiplos = ModeloExcluido("multiplos", "per_peers_no_disponible")
elif len(peer_average.peers_usados) < MIN_PEERS_VALIDOS_PARA_MULTIPLOS:  # NUEVO
    nivel1_multiplos = ModeloExcluido("multiplos", "peers_validos_insuficientes")
else:
    nivel1_multiplos = None
multiplos_valido = nivel1_multiplos is None
```

**El orden del `if/elif` importa y se preserva**: el caso de 0 peers válidos (`per_promedio is None`) sigue devolviendo `"per_peers_no_disponible"` (no se reclasifica como `"peers_validos_insuficientes"`) — son 2 motivos distintos que ya se comunican distinto hoy ("no pude obtener el PER de los comparables" vs. "no hay suficientes"), y no hay pedido de Daniela para fusionarlos. Con `MIN_PEERS_VALIDOS_PARA_MULTIPLOS >= 1` (cualquier valor razonable), el nuevo `elif` solo puede dispararse cuando `per_promedio` ya es no-`None` (1 o más peers válidos) pero por debajo del mínimo — nunca se solapa con el caso de 0.

Resto del bloque de `compute_valuation_scenarios` (la función interna `_escenario()`, líneas 547-604) **no cambia una sola línea** — ya usa `multiplos_valido`/`nivel1_multiplos` de forma genérica, sin conocer los motivos posibles.

### 2. `compute_valuation` (versión no-escenario): parámetro nuevo opcional, default preserva el comportamiento exacto de hoy

`compute_valuation` (líneas 288-398) no recibe el `PeerAverageResult` completo, solo `per_promedio_peers: Optional[float]` — no tiene forma de saber cuántos peers aportaron ese número. Gana un parámetro nuevo, **opcional, con default `None`**:

```python
def compute_valuation(
    *,
    eps_ttm: float,
    eps_historial: list[float],
    per_promedio_peers: Optional[float],
    fcf_historial: list[float],
    y: Optional[float],
    wacc_inputs: dict,
    shares_outstanding: float,
    periodos_por_anio_eps: int = 1,
    periodos_por_anio_fcf: int = 1,
    fcf_base: Optional[float] = None,
    n_peers_validos: Optional[int] = None,  # NUEVO
) -> ValuationResult:
    ...
    # --- Múltiplos ---
    if eps_no_positivo:
        result.modelos_excluidos.append(ModeloExcluido("multiplos", "eps_ttm_no_positivo"))
    elif n_peers_validos is not None and n_peers_validos < MIN_PEERS_VALIDOS_PARA_MULTIPLOS:  # NUEVO
        result.modelos_excluidos.append(ModeloExcluido("multiplos", "peers_validos_insuficientes"))
    else:
        result.valor_justo_multiplos = calculate_multiplos_fair_value(eps_ttm, per_promedio_peers)
        if result.valor_justo_multiplos is None:
            result.modelos_excluidos.append(ModeloExcluido("multiplos", "per_peers_no_disponible"))
```

Con `n_peers_validos=None` (default, ningún llamador existente lo pasa), el comportamiento es **byte a byte idéntico** al de antes de esta spec — ningún test existente de `compute_valuation` se ve afectado (confirmado: ninguno pasa hoy `per_promedio_peers` con la intención de simular "1 peer válido", ver Restricciones).

**`compute_valuation` no se llama desde `query_handler.py` en producción** (solo `compute_valuation_scenarios`, que sí recibe siempre el `PeerAverageResult` completo con `peers_usados`) — este cambio es puramente para no dejar cojo el invariante ya testeado "conservador idéntico campo a campo a `compute_valuation()`" (`tests/test_valuation.py::test_valuation_adobe_scenarios`, líneas 771-785): quien quiera esa paridad exacta en un caso con pocos peers válidos debe pasar `n_peers_validos=len(peer_average.peers_usados)` explícitamente a `compute_valuation`. El fixture de Adobe usado en ese test tiene 3 peers válidos (MSFT/ORCL/CRM) — no cruza el nuevo umbral bajo ningún valor razonable de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` (2 o 3), así que ese test específico sigue pasando sin tocarlo.

### 3. `query_handler.py`: cero cambios

`query_handler.py` ya llama a `compute_valuation_scenarios(peer_average=peer_result, ...)` pasando el `PeerAverageResult` completo (línea 401) — el nuevo chequeo vive enteramente dentro de `valuation.py`, sin ningún wiring nuevo. `n_peers_validos=len(peer_result.peers_usados)` (línea 535, usado para `summary.py`) tampoco cambia — sigue siendo el mismo cálculo, ahora también usado consistentemente por `valuation.py` (antes solo llegaba hasta el texto de `summary.py`).

### 4. `summary.py`: nuevo label + mensaje dinámico, y eliminación de la guarda parcial que queda obsoleta

Nueva entrada estática de fallback en `MOTIVO_LABELS` (usada si por algún motivo el mensaje dinámico de abajo no aplica — mismo patrón defensivo que el resto del diccionario, `.get(motivo, motivo)`):

```python
MOTIVO_LABELS = {
    ...
    "peers_validos_insuficientes": (
        "no hay suficientes comparables (peers) con PER válido esta consulta "
        "para un promedio confiable"
    ),
}
```

En el loop que imprime `excluidos_base` (líneas 249-252), se agrega un caso especial que reconstruye el detalle cuantitativo que ya se mostraba hoy ("solo N comparable(s) válido(s)"), reutilizando el `n_peers_validos` que la función ya recibe como parámetro:

```python
for item in excluidos_base:
    modelo_label = MODELO_LABELS.get(item["modelo"], item["modelo"])
    if item["motivo"] == "peers_validos_insuficientes":
        motivo_label = (
            "no hay suficientes comparables con PER válido esta consulta "
            f"(mínimo {valuation.MIN_PEERS_VALIDOS_PARA_MULTIPLOS}, hubo {n_peers_validos})"
        )
    else:
        motivo_label = MOTIVO_LABELS.get(item["motivo"], item["motivo"])
    lines.append(f"- {modelo_label} no se pudo calcular: {motivo_label}.")
```

Requiere importar la constante: `from investbot.valuation import MIN_PEERS_VALIDOS_PARA_MULTIPLOS, classify_scenario` (línea 23, hoy solo importa `classify_scenario`).

**Se elimina el bloque que queda obsoleto** (líneas 243-247 actuales):

```python
if modelo_key == "multiplos" and n_peers_validos < 2:
    lines.append(
        f"  _(no hay rango disponible para Múltiplos: solo {n_peers_validos} "
        "comparable(s) válido(s))_"
    )
```

Este bloque queda **muerto por construcción** una vez aplicada la Decisión #1: para cualquier valor de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS >= 2` (el rango de valores en discusión en la Pregunta 1), cualquier `n_peers_validos < 2` ya dispara la exclusión de nivel 1 en `valuation.py`, lo que hace que `"multiplos"` esté en `excluidos_base_modelos` y el loop de la línea 225-226 (`if modelo_key in excluidos_base_modelos: continue`) salte la fila **antes** de llegar a esta línea — nunca se ejecuta con el código nuevo. Dejarlo sería código muerto, no una guarda redundante inofensiva.

**Cambio de comportamiento explícito, marcado como tal (no una regresión no anunciada):** antes de esta spec, con 1 peer válido, la fila de Múltiplos se mostraba con su valor numérico + una nota al pie. Después de esta spec, la fila de Múltiplos **no se muestra en absoluto** (se reemplaza por la línea "Múltiplos no se pudo calcular: ..." del bloque de exclusiones), igual que ya pasa hoy con `eps_ttm_no_positivo`. **Esto rompe intencionalmente** `tests/test_summary.py::test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` (líneas 190-197) — no se ajusta cosméticamente, se **reemplaza** por un test nuevo que verifica el comportamiento correcto (Múltiplos ausente de la fila con valores, presente en el bloque de exclusiones con el detalle "mínimo X, hubo N") — ver Criterios de aceptación.

### 5. `README.md`: nota de documentación, sin cambio de presupuesto de requests para lo decidido en esta spec

La tabla de presupuesto (línea 84) no cambia sus números — esta spec no agrega ninguna llamada HTTP. Se agrega una nota breve junto a la fila de "Peers para el modelo de Múltiplos" aclarando que, si tras consultar FMP quedan menos de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` peers con `earningsYield` válido, el modelo de Múltiplos se excluye del promedio de Valor Justo Total (no se muestra un número calculado con una muestra insuficiente).

### 6. No se distingue "outlier temático" de "poca cobertura de datos" — criterio puramente cuantitativo, por diseño

Daniela señaló, en el caso real, que PLTR es un mal comparable de negocio para ADBE (un juicio *cualitativo* sobre el tipo de negocio). Esta spec **no** intenta detectar eso — solo cuenta *cuántos* peers con PER válido hay. Motivo: distinguir "outlier temático real" (PLTR vs. ADBE) de "peer razonable con mala cobertura de datos esta consulta" (ej. CRM, que sí es un comparable razonable de ADBE, simplemente no devolvió dato esta vez) requeriría una fuente de datos o heurística que hoy el bot no tiene — Finnhub no expone ningún score de similitud de negocio, solo la sub-industria usada para *generar* la lista de candidatos (que ya se demostró insuficiente en el caso PLTR: PLTR y ADBE cayeron en la misma sub-industria de Finnhub y aun así no son comparables razonables). Agregar esa distinción sería un sistema de clasificación temática nuevo, fuera de alcance de esta spec y con riesgo real de sobre-ingeniería (YAGNI) para un caso que, con 1 solo peer válido, ya se resuelve completamente con el umbral cuantitativo (Decisión #1) — si el único peer válido es un outlier, ya no participa del promedio, sea cual sea la razón cualitativa de fondo. El caso donde SÍ importaría la distinción cualitativa (2+ peers válidos, uno de ellos un outlier extremo) es exactamente el terreno de la Pregunta 3 (filtro de outlier) — ver "Preguntas abiertas".

---

## Preguntas abiertas — CONFIRMADAS 2026-07-31, las 3 con la recomendación del `architect`

### Pregunta 1 — ¿Cuál es el mínimo de peers con PER válido para incluir Múltiplos en el promedio? — **CONFIRMADO: 2**

**Recomendación del `architect`: 2.** Justificación, no una elección arbitraria:
- **Ya es el umbral que el propio proyecto usa en otro lugar** para la misma pregunta de fondo ("¿alcanza esta cantidad de peers para decir algo confiable?") — `market_context.py::compare_to_peers` (líneas 194-215) ya clasifica `len(peers_usados) == 1` como `"un_solo_peer_valido"`, no comparable, exactamente el caso PLTR/ADBE. Con `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2`, el modelo de Múltiplos y la sección de "Contexto de mercado" quedan **consistentes entre sí** por primera vez: hoy es posible (y pasó, con ADBE) que "Contexto de mercado" diga "no hay rango suficiente para comparar" mientras "Valor Justo" sí usa ese mismo peer solitario con peso pleno — una contradicción visible para Daniela en el mismo mensaje.
- Con `MIN = 2`, el caso ADBE/PLTR queda resuelto (1 peer válido → excluido).
- **Alternativa a considerar: 3** (mismo tamaño que la lista fija de respaldo, `PEERS_BY_SECTOR`, y que `MIN_PEERS_DINAMICOS_PARA_USAR`). Es más conservador matemáticamente, pero dado que **de los 5 candidatos de Finnhub para ADBE, solo 1 devolvió dato válido** (una tasa de fallo de FMP del 80% observada en producción, no hipotética), un umbral de 3 excluiría Múltiplos con bastante frecuencia — potencialmente más seguido de lo que Daniela quiere, dado que Múltiplos es uno de los 3 modelos que el bot ofrece como valor agregado. No recomiendo 3 sin que Daniela vea ese trade-off explícitamente.
- **No recomiendo dejarlo en 1** (el equivalente a "sin cambio") — sería no resolver el problema reportado.

**Confirmado por Daniela 2026-07-31: 2.** `implementer` fija `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2` sin ambigüedad — ya no es TBD.

### Pregunta 2 — ¿Vale la pena reintentar con el respaldo fijo cuando la fuente dinámica da pocos peers válidos? (Alcance 1 del pedido original) — **CONFIRMADO: no**

**Recomendación del `architect`: no, al menos no en esta iteración.** Esto es un mecanismo distinto y más invasivo que la Decisión #1 de esta spec (que solo *excluye* Múltiplos cuando hay pocos datos, sin intentar conseguir más). La opción evaluada sería: si tras consultar FMP para los candidatos dinámicos de Finnhub quedan menos de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` peers válidos, consultar *además* los peers del respaldo fijo (`PEERS_BY_SECTOR`) que todavía no se hayan probado, dentro de la misma consulta, para intentar llegar al mínimo.

Trade-offs concretos, no genéricos:
- **Costo de requests real**: hoy el presupuesto documentado para peers es "3-5" llamadas a `/key-metrics` (README línea 84). Un reintento agregaría hasta 3 llamadas más (el tamaño de la lista fija de respaldo) en el peor caso, llevando el peor caso de esa fila de la tabla a "hasta 8", y el total de la consulta completa de "13-16" a **hasta ~19** en el peor caso combinado (fuentes trimestrales fallando + reintento de peers). Sigue muy por debajo del límite de 250/día, pero es un salto real de presupuesto que merece decisión explícita, no un ajuste silencioso.
- **No hay garantía de que el reintento sirva**: los peers del respaldo fijo pueden fallar el mismo `earningsYield` esta consulta por el mismo motivo que fallaron los dinámicos (ej. FMP con datos incompletos ese día, no algo específico de qué ticker se pregunta) — se pagaría el costo de requests sin certeza de resolver el problema.
- **Complejidad no trivial**: haría falta deduplicar contra los tickers ya intentados (evitar pedir de nuevo un ticker que ya falló, aunque aparezca en ambas listas) y decidir qué pasa si el respaldo fijo tampoco alcanza el mínimo (¿se excluye igual, con qué motivo?) — no es un cambio de una línea como la Decisión #1.
- **La Decisión #1 de esta spec ya resuelve el daño concreto reportado** (el número disparatado en Valor Justo Total) sin este costo — el reintento solo agregaría la posibilidad de *mostrar* Múltiplos más seguido, no de evitar el daño, que ya está evitado.

**Confirmado por Daniela 2026-07-31: no se implementa el reintento.** Si más adelante lo quiere (para que Múltiplos se excluya con menos frecuencia, a costa del presupuesto y la complejidad descritos), es una spec patch separada — no reabre esta spec.

### Pregunta 3 — ¿Hace falta un filtro de outlier de PER dentro del propio set de peers? (Alcance 3 del pedido original) — **CONFIRMADO: no**

**Recomendación del `architect`: no, todavía.** Esto atacaría un caso que la Decisión #1 (con `MIN_PEERS_VALIDOS_PARA_MULTIPLOS >= 2`) no cubre: 2 o más peers válidos donde uno es un outlier extremo (ej. un PLTR con PER 259 promediado junto a un CRM con PER 30 — con `MIN=2` esto pasaría el umbral y se promediaría igual, sin filtrar el outlier). No es el caso reportado por Daniela (que tuvo exactamente 1 peer válido, ya cubierto), sino un caso hipotético relacionado.

No lo recomiendo ahora porque:
- Agregar un filtro de outlier (ej. descartar un PER que esté a más de Nx del resto) introduce **otra constante a decidir sin datos reales que la justifiquen** (¿qué múltiplo de distancia? ¿respecto a la mediana o al promedio del resto?) — más superficie de decisión no pedida explícitamente, con el mismo riesgo de "inventar un umbral sin acuerdo" que este proyecto evita por principio (ver `SDD_procedencia_peers_individuales.md`, restricción explícita de Daniela).
- No hay evidencia todavía de que este caso (2+ peers válidos con un outlier) haya ocurrido en producción — el caso real reportado fue de 1 peer, no de 2+ con un outlier.
- Es razonable resolver primero el caso confirmado (Decisión #1) y observar en uso real si el caso "2+ peers con outlier" aparece antes de agregar una segunda capa de defensa.

**Confirmado por Daniela 2026-07-31: no se implementa el filtro de outlier.** Si en el futuro aparece un caso real de 2+ peers válidos con un outlier evidente, es candidato a una spec patch separada con el umbral concreto acordado explícitamente — no se decide en silencio en esta iteración.

**Las 3 preguntas quedaron confirmadas por Daniela el 2026-07-31, aceptando la recomendación del `architect` tal cual en los 3 casos** — no queda ninguna pregunta abierta bloqueante. `implementer` puede fijar `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2` y proceder sin implementar el reintento (Pregunta 2) ni el filtro de outlier (Pregunta 3).

---

## Criterios de aceptación

*(la Pregunta 1 ya está resuelta: `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2`. Los criterios siguen redactados contra el nombre de la constante, no contra el literal "2", por prolijidad y para que sigan siendo válidos si un spec patch futuro cambia el valor — pero `implementer` ya puede escribir tests con el número real, no hace falta parametrizar contra un TBD.)*

### `valuation.py`
- [ ] `compute_valuation_scenarios` con `peer_average.peers_usados` de longitud `MIN_PEERS_VALIDOS_PARA_MULTIPLOS - 1` (ej. 1 peer válido si el mínimo es 2) y `eps_ttm > 0` → `nivel1_multiplos.motivo == "peers_validos_insuficientes"` en `modelos_excluidos_base`, y `valor_justo_multiplos is None` en los 3 escenarios (pesimista/conservador/optimista).
- [ ] `compute_valuation_scenarios` con `peer_average.peers_usados` de longitud exactamente `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` → Múltiplos se calcula normalmente (test de límite exacto, no solo "menos que" y "más que").
- [ ] `compute_valuation_scenarios` con `peer_average.per_promedio is None` (0 peers válidos) → sigue devolviendo motivo `"per_peers_no_disponible"`, **no** `"peers_validos_insuficientes"` (test explícito que distingue ambos motivos — regresión sobre `test_valuation_scenarios_multiplos_nivel1_sin_peers_validos`, que debe seguir pasando sin modificación).
- [ ] `compute_valuation_scenarios` con `eps_ttm <= 0` y pocos peers válidos simultáneamente → motivo sigue siendo `"eps_ttm_no_positivo"` (el chequeo de EPS tiene prioridad en el orden del `if/elif`, test explícito de precedencia).
- [ ] `compute_valuation` con `n_peers_validos=None` (default, no pasado) → comportamiento **byte a byte idéntico** al de antes de esta spec (test de regresión explícito sobre los casos ya existentes en `tests/test_edge_cases.py`/`tests/test_valuation.py` que llaman a `compute_valuation` sin ese parámetro).
- [ ] `compute_valuation` con `n_peers_validos` explícito por debajo del mínimo → mismo motivo `"peers_validos_insuficientes"`, mismo comportamiento que la versión de escenarios.
- [ ] `tests/test_valuation.py::test_valuation_adobe_scenarios` (fixture real de Adobe, 3 peers válidos MSFT/ORCL/CRM) sigue pasando sin modificación — 3 ≥ cualquier valor razonable de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` (2 o 3), no cruza el nuevo umbral.

### `summary.py`
- [ ] `MOTIVO_LABELS["peers_validos_insuficientes"]` existe como fallback estático.
- [ ] `build_valuation_scenarios_section` con `modelos_excluidos_base` conteniendo `{"modelo": "multiplos", "motivo": "peers_validos_insuficientes"}` y `n_peers_validos=1` → el texto contiene `"Múltiplos no se pudo calcular"` y el detalle dinámico `"mínimo {MIN_PEERS_VALIDOS_PARA_MULTIPLOS}, hubo 1"` (usando el valor real de la constante, no hardcodeado en el test) — **reemplaza** `test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` (comportamiento intencionalmente distinto, ver Decisión #4).
- [ ] La fila de Múltiplos con valores por escenario (Pesimista | Conservador | Optimista) **no aparece** cuando el motivo es `"peers_validos_insuficientes"` — mismo comportamiento que ya existe hoy para `"eps_ttm_no_positivo"` (test de regresión sobre el patrón ya cubierto).
- [ ] El texto viejo `"no hay rango disponible para Múltiplos"` ya no aparece en ningún caso (test negativo explícito, confirma que el bloque obsoleto de la Decisión #4 fue eliminado, no solo que quedó inalcanzable).
- [ ] Ningún otro motivo de `modelos_excluidos_base`/`modelos_excluidos` (nivel 1 o nivel 2) cambia su texto — test de regresión general sobre el resto de `tests/test_summary.py` sin modificar sus aserciones.

### `query_handler.py`
- [ ] Cero llamadas HTTP nuevas — test/inspección que confirma que no se agregó ningún `await fmp_client.*`/`await finnhub_client.*` nuevo.
- [ ] `n_peers_validos=len(peer_result.peers_usados)` (línea 535) no cambia — test de regresión.

### `README.md`
- [ ] Tabla de presupuesto (línea 84) no cambia sus números.
- [ ] Nota nueva junto a la fila de peers, explicando la exclusión cuando hay menos de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` peers con PER válido.

---

## Artefactos a crear/modificar

- `src/investbot/valuation.py` → constante `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2` (Pregunta 1 confirmada); `compute_valuation_scenarios` (nueva rama `elif` en el nivel 1 de Múltiplos); `compute_valuation` (parámetro nuevo opcional `n_peers_validos`, misma rama nueva).
- `src/investbot/summary.py` → import de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS`; `MOTIVO_LABELS` (entrada nueva); loop de `excluidos_base` (caso especial con el detalle cuantitativo); eliminación del bloque obsoleto (líneas 243-247 actuales).
- `README.md` → nota junto a la tabla de presupuesto (fila de peers para Múltiplos).
- `tests/test_valuation.py` → casos nuevos descritos en Criterios de aceptación; ningún test existente se reescribe salvo que se demuestre que cruza el nuevo umbral (ninguno lo hace, confirmado arriba).
- `tests/test_summary.py` → reemplazo explícito de `test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` por el test nuevo descrito arriba; casos nuevos para el mensaje dinámico.
- `tests/test_edge_cases.py` → test de regresión confirmando que `compute_valuation` sin `n_peers_validos` no cambia (`test_valuation_multiplos_excluido_por_peers_no_disponibles`, línea 62, debe seguir pasando sin modificación).

**No se toca:** `src/investbot/fmp_client.py` (sin endpoints nuevos ni cambiados), `src/investbot/peers.py` (la lógica de `get_peer_pe_average`/`PeerAverageResult` no cambia — el dato que hace falta, `peers_usados`, ya existe), `src/investbot/market_context.py` (su propio umbral de `< 2` para "Contexto de mercado" ya existe y esta spec lo deja intacto — ver Restricciones), `src/investbot/rules.py`.

---

## Restricciones

- **`fmp_client.py` no cambia** — ningún endpoint nuevo, ninguna llamada nueva. Cero impacto en el presupuesto de 250 requests/día para lo decidido en esta spec (Decisiones #1-#6). Si Daniela responde afirmativamente la Pregunta 2 (reintento), **eso sí** cambiaría el presupuesto — cuantificado explícitamente arriba, pero no forma parte de esta iteración.
- **No se reabre `MIN_PEERS_DINAMICOS_PARA_USAR = 3`** (mínimo de *candidatos* de Finnhub antes de consultar FMP, ya fijado por Daniela en `SDD_peers_dinamicos_y_eventos_corporativos.md`) — es una pregunta distinta de `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` (mínimo de peers *con dato válido*, después de consultar FMP). Ambos mínimos pueden coexistir con valores distintos sin contradicción.
- **No se toca la lógica de clasificación de `market_context.py::compare_to_peers`** (`posicion`/`motivo_no_comparable`, ya cerrada en `SDD_procedencia_peers_individuales.md`) — su propio umbral de `< 2` para "no comparable" queda como está, implementado con literales propios (`== 0`, `== 1`), sin refactor a una constante compartida con `valuation.py` en esta spec (evita tocar código ya cerrado y probado; si más adelante se quiere una única fuente de verdad para "qué es una muestra mínima de peers", es un refactor aparte, no parte de este fix).
- **No se implementa el reintento con respaldo fijo (Pregunta 2) ni el filtro de outlier (Pregunta 3) en esta iteración** — confirmado por Daniela 2026-07-31, ambos quedan descartados para esta spec, no como trabajo pendiente.
- **No se distingue "outlier temático" de "poca cobertura de datos"** (Decisión #6) — el criterio es puramente cuantitativo (`len(peers_usados)`), nunca cualitativo sobre qué tan bueno es el comparable.
- **Ninguna fórmula de Graham/DCF cambia** — esta spec toca exclusivamente la condición de inclusión/exclusión de Múltiplos en el promedio, nunca el cálculo interno de ningún modelo.
- **`MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2` — confirmado por Daniela 2026-07-31, ya no es TBD.** `implementer` fija ese valor literal directamente.

---

## Impacto en presupuesto de requests

**Cero, para el alcance decidido (Decisiones #1-#6).** El chequeo nuevo (`len(peer_average.peers_usados) < MIN_PEERS_VALIDOS_PARA_MULTIPLOS`) opera sobre un dato que `peers.get_peer_pe_average` ya calcula hoy, sin ninguna llamada HTTP adicional — mismo principio que las guardas de nivel 1 ya existentes (`eps_ttm <= 0`, `per_promedio is None`), que tampoco disparan requests.

**La Pregunta 2 quedó confirmada como "no"** — el reintento con respaldo fijo no se implementa, así que el impacto contingente descrito arriba (hasta +3 llamadas, presupuesto "10-13 / 16-19" en el peor caso combinado) no aplica a esta iteración. Si en el futuro se revierte esa decisión, es una spec patch aparte con su propio presupuesto a actualizar en `README.md`.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_calidad_peers_multiplos.md` (esta spec).

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — cubre `valuation.py`, `summary.py`, `query_handler.py` (regresión), `README.md`.

### Decisiones de diseño tomadas (para que `security`/`qa`/`implementer` no las reabran)
- El mecanismo reutiliza la infraestructura de exclusión de nivel 1 ya existente en `valuation.py` (`ModeloExcluido`, `modelos_excluidos_base`) — no se agrega ninguna estructura de datos nueva, solo una rama de condición más, igual patrón que `eps_ttm_no_positivo`/`per_peers_no_disponible`.
- El umbral se aplica sobre `len(peer_average.peers_usados)`, dato ya calculado hoy sin I/O adicional — cero impacto en el presupuesto de requests para el alcance decidido.
- `compute_valuation` (versión no-escenario) gana el mismo chequeo vía un parámetro opcional (`n_peers_validos`, default `None` = comportamiento idéntico a hoy) — preserva el invariante ya testeado de paridad con `compute_valuation_scenarios.conservador`.
- El bloque de nota parcial existente en `summary.py` (líneas 243-247 actuales) se elimina por quedar muerto — no se deja como código inalcanzable.
- **No se implementan** el reintento con respaldo fijo ni el filtro de outlier en esta iteración — decisión explícita del `architect`, con recomendación negativa justificada (costo de requests + falta de garantía, en el primer caso; falta de un caso real observado + riesgo de inventar un umbral sin acuerdo, en el segundo). No es trabajo pendiente, es alcance evaluado y descartado por ahora.
- **No se distingue "outlier temático" de "poca cobertura de datos"** — criterio puramente cuantitativo, decisión explícita (Decisión #6), no una limitación no reconocida.

### Preguntas abiertas — actualización 2026-07-31: las 3 CONFIRMADAS por Daniela
- **Pregunta 1: `MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2`, confirmado.** Ya no bloquea a `implementer`.
- **Preguntas 2 y 3: confirmado que no se implementan** (reintento con respaldo fijo y filtro de outlier, respectivamente) — quedan fuera del alcance de esta iteración, con recomendación del `architect` aceptada tal cual por Daniela.

**No queda ninguna pregunta bloqueante.** Scope Freeze — la spec está lista para `qa` y, después, para `implementer` con el valor `2` ya fijado.

---

## Revisión de `security` — completada

**Alcance revisado:** esta spec completa (Decisiones #1-#6, las 3 Preguntas abiertas, Criterios de aceptación), contra el código real: `src/investbot/peers.py` (195 líneas, completo), `src/investbot/valuation.py` (líneas 260-620, `ModeloExcluido`/`compute_valuation`/`compute_valuation_scenarios`/`_escenario`), `src/investbot/summary.py` (líneas 1-60 y 180-280, `MOTIVO_LABELS`/`build_valuation_scenarios_section`), `src/investbot/market_context.py` (líneas 180-230, `compare_to_peers`), `src/investbot/query_handler.py` (grep dirigido a `compute_valuation_scenarios`/`n_peers_validos`/`peer_result`), y `README.md` línea 84. También releí `SDD_procedencia_peers_individuales.md` y `SDD_peers_dinamicos_y_eventos_corporativos.md` para el estándar de convenciones ya aplicado (`params=` nunca f-string, `PeerAverageResult` ya sanitizado antes de llegar a `valuation.py`), y `SDD_eps_ttm_real.md` como referencia de formato y de vara de comparación para qué tipo de hallazgo amerita bloquear (ver Hallazgo 1/2 de esa spec, que sí involucraban un endpoint HTTP nuevo vía callback y logging sin sanear — ninguno de los 2 patrones está presente acá).

**Confirmado sin código nuevo de superficie HTTP.** Coincide con lo que dice el handoff del `architect`: `fmp_client.py`/`finnhub_client.py` no aparecen en "Artefactos a crear/modificar" ni en `peers.py` (confirmado leyendo el archivo completo — `get_peer_pe_average`, líneas 100-194, no cambia una sola línea bajo esta spec). El único dato que dispara el mecanismo nuevo, `len(peer_average.peers_usados)`, es un entero derivado de una lista que `peers.py` ya construye validada: cada ticker en `usados` llegó ahí solo después de pasar `isinstance(earnings_yield, (int, float))` (línea 164) y `earnings_yield > 0` (línea 169) — no hay ningún camino por el que un valor no numérico, `None`, o negativo participe del conteo. El nuevo `elif` de `valuation.py` (Decisión #1) es una comparación aritmética (`int < int`) sobre un dato ya saneado río arriba, no un punto de entrada de datos externos nuevo.

### Confirmaciones sin hallazgo (verificado explícitamente, no asumido)

- **Cero endpoints/params nuevos**: `peers.py::get_peer_pe_average` no cambia; ninguna llamada nueva a `fmp_client`/`finnhub_client`; el `README.md` (línea 84, tabla de presupuesto) no cambia sus números, consistente con "Impacto en presupuesto de requests: Cero" declarado por `architect`.
- **Manejo de errores sin cambios de patrón**: el mecanismo nuevo no agrega ningún `try/except` — reutiliza `ModeloExcluido`/`modelos_excluidos_base`, exactamente la misma estructura ya usada por `eps_ttm_no_positivo`/`per_peers_no_disponible` (confirmado en `valuation.py` líneas 501-508 actuales). Ninguna excepción de proveedor externo se toca ni se re-propaga distinto.
- **Sin superficie de log/message injection (CWE-117) en el mensaje nuevo**: el texto agregado en `summary.py` (Decisión #4) interpola únicamente un `int` (`n_peers_validos`) y la constante `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` — ningún ticker ni ninguna cadena proveniente de FMP/Finnhub se inserta en el mensaje nuevo. No aplica el mismo riesgo que motivó el Hallazgo 2 de `SDD_eps_ttm_real.md` (ahí sí había un ticker crudo llegando a `logger.exception`).
- **Wiring de `query_handler.py` confirmado sin cambios**: `compute_valuation_scenarios(peer_average=peer_result, ...)` (línea 401) y `n_peers_validos=len(peer_result.peers_usados)` (línea 535) — ambos ya existen tal cual, ninguna modificación requerida por esta spec, consistente con la Decisión #3.
- **`compute_valuation()` (versión no-escenario) confirmado sin llamador en producción**: único match de `compute_valuation(` en `query_handler.py` es un comentario (línea 389), no una invocación real — consistente con la afirmación de `architect` de que el parámetro nuevo `n_peers_validos` en esa función es solo para no romper el invariante de paridad ya testeado, sin impacto en el camino de producción real.
- **Tests referenciados por la spec existen y respaldan las claims de regresión**: `test_valuation_scenarios_multiplos_nivel1_sin_peers_validos` (`tests/test_valuation.py:481`), `test_valuation_adobe_scenarios` (`tests/test_valuation.py:705`), `test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` (`tests/test_summary.py:190`), `test_valuation_multiplos_excluido_por_peers_no_disponibles` (`tests/test_edge_cases.py:62`) — los 4 existen con esos nombres en esas líneas, no son referencias a tests inexistentes.
- **`market_context.py::compare_to_peers`** (líneas 180-230): confirmado que ya trata `len(peers_usados) == 1` como `"un_solo_peer_valido"` (líneas 207-219) y `== 0` como `"sin_peers_validos"` (líneas 194-206) — el precedente que cita la spec es real, no una paráfrasis. Esta spec no lo toca, confirmado.

### Análisis de seguridad de las 3 preguntas abiertas

**Pregunta 1 (umbral 2 vs. 3):** sin implicancia de seguridad — es una decisión estadística sobre tamaño mínimo de muestra, no una superficie de ataque. Única nota de higiene, no bloqueante: cualquiera sea el valor que Daniela confirme, debe seguir siendo un literal fijado en código (como ya está diseñado en la Decisión #1), no una variable de entorno ni un valor derivado de datos externos — de lo contrario la afirmación "cero llamadas HTTP nuevas" y el análisis de esta revisión dejarían de ser válidos sin que nadie lo note. La spec ya lo trata como constante literal; esto es solo una confirmación de que debe seguir siendo así si `implementer` la instrumenta.

**Pregunta 2 (reintento con respaldo fijo, no implementado en esta iteración):** el diseño *evaluado y descartado* por `architect` ya está acotado por naturaleza — `PEERS_BY_SECTOR` es una lista fija hardcodeada de 3 a 5 tickers por sector (`peers.py` líneas 39-51), así que el "hasta +3 llamadas" que cuantifica `architect` es un techo real de una sola pasada adicional, no un bucle. Esto es distinto en naturaleza del Hallazgo 1 (CWE-770) de `SDD_eps_ttm_real.md`, donde el problema era un usuario pudiendo re-disparar una consulta completa un número **ilimitado** de veces vía un callback persistente sin rate-limit — acá se evalúa una única llamada extra, acotada, dentro de una consulta que ya pasa por `rate_limiter.allow` aguas arriba en `handle_text`. Si en el futuro Daniela revierte esta decisión y se escribe una spec patch para el reintento, `security` deja 2 notas para esa spec futura (no bloquean nada hoy, la Pregunta 2 no se implementa acá):
  - Debe deduplicar contra los tickers dinámicos ya intentados (el propio `architect` ya lo anota como requisito de corrección, no de seguridad) — si no deduplica, el techo de "+3" deja de ser preciso.
  - El diseño no debe evolucionar hacia "seguir intentando fuentes hasta juntar N peers válidos" sin un techo fijo de intentos — ahí sí correspondería reabrir CWE-770, porque un bucle condicionado a alcanzar un mínimo (en vez de "una lista fija, una vez") ya no tiene la misma garantía de acotación que el diseño descartado hoy.

**Pregunta 3 (filtro de outlier, no implementado en esta iteración):** el dato de entrada para un futuro filtro (los PER individuales en `peer_average.peers_pe`) ya llega saneado — `peers.py` garantiza que todo valor en `pes`/`peers_pe` es un `float` derivado de `1.0 / earnings_yield` con `earnings_yield > 0` ya verificado (líneas 169-173), así que no hay ningún camino por el que un valor no numérico, `None`, negativo o cero llegue a una lógica de outlier futura sin sanitizar — la premisa de la pregunta 3 del enunciado ("¿podría introducir lógica que dependa de datos no confiables sin sanitizar?") no aplica, porque el saneo ya ocurre antes en `peers.py`, no sería responsabilidad del filtro de outlier. La única observación técnica (no de seguridad — no hay dato de atacante ni de usuario involucrado) es que un futuro filtro basado en mediana/promedio debería contemplar el caso de quedar con una lista de un solo elemento tras excluir el outlier bajo evaluación, para no dividir por una muestra vacía — bug de lógica a validar en su propia spec/`qa`, no un hallazgo de seguridad.

### Veredicto

**Sin hallazgos bloqueantes ni no bloqueantes.** La spec queda **lista para pasar a `qa` tal como está escrita** — no hace falta agregar ningún criterio de aceptación de seguridad nuevo a la sección "Criterios de aceptación" ya redactada por `architect`. La superficie de esta spec (una comparación aritmética sobre un dato ya validado, sin I/O nuevo, sin texto de usuario/tercero nuevo en los mensajes) es sustancialmente más chica que la de `SDD_eps_ttm_real.md` (que sí tuvo 2 hallazgos bloqueantes por introducir callbacks de Telegram nuevos con datos embebidos) — es coherente que esta revisión no encuentre nada que bloquear.

**Actualización 2026-07-31, posterior a esta revisión:** Daniela confirmó las 3 preguntas abiertas aceptando la recomendación del `architect` tal cual (`MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2`; no reintento; no filtro de outlier) — no cambia nada de este análisis de seguridad, el mecanismo revisado ya era válido para cualquier valor ≥2.

**Siguiente paso:** `qa` agrega criterios de cobertura/testabilidad sobre la spec, ya con Scope Freeze (sin preguntas abiertas pendientes).

---

## Criterios de QA

**Rol:** `qa` (pre-implementación — Momento 1 del pipeline BMAD). Amplío la spec de `architect`, ya revisada por `security` sin hallazgos — no reescribo "Criterios de aceptación" ni "Decisiones de diseño tomadas", los complemento con los ángulos de testabilidad/cobertura que un `implementer` apurado podría saltearse. Leída la spec completa (Decisiones #1-#6, las 3 Preguntas abiertas ya confirmadas, Criterios de aceptación, revisión de `security`) + código real de `src/investbot/valuation.py` (líneas 260-620: `ModeloExcluido`, `compute_valuation`, `compute_valuation_scenarios`, `_escenario`), `src/investbot/summary.py` (líneas 37-53 `MOTIVO_LABELS`, 190-285 `build_valuation_scenarios_section`), `src/investbot/query_handler.py` (grep dirigido a `compute_valuation_scenarios`/`n_peers_validos`, líneas 398 y 535) + las suites `tests/test_valuation.py`, `tests/test_summary.py`, `tests/test_edge_cases.py`, `tests/test_query_handler.py`, `tests/test_peers.py`, `tests/test_market_context.py` (grep dirigido a `compute_valuation(`, `compute_valuation_scenarios(`, `peers_usados`).

**Corrida de baseline real antes de esta spec** (`.venv/bin/python -m pytest -q --cov=investbot --cov-report=term-missing --cov-branch`, `pythonpath=src` vía `pytest.ini`):

```
663 passed, 12 warnings in 1.82s

Name                                Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------------------
src/investbot/bot.py                   62      2     12      1    96%   60, 143
src/investbot/corporate_events.py      53      0     16      0   100%
src/investbot/db.py                    30      0      8      0   100%
src/investbot/finnhub_client.py        31      0      2      0   100%
src/investbot/fmp_client.py            78      0     16      0   100%
src/investbot/market_context.py        66      0     20      0   100%
src/investbot/onboarding.py            71      0     10      0   100%
src/investbot/peers.py                 56      0     16      0   100%
src/investbot/query_handler.py        387      6     76      2    98%   303, 340-341, 373-374, 752
src/investbot/risk_fit.py              17      0      4      0   100%
src/investbot/rules.py                 95      0     22      0   100%
src/investbot/sec_edgar_client.py      61      0     10      0   100%
src/investbot/security.py              71      0     24      0   100%
src/investbot/summary.py              254      0    104      5    99%   234->236, 707->709, 709->714, 719->723, 723->729
src/investbot/treasury_client.py      101      3     20      2    96%   106->104, 111, 131-132
src/investbot/valuation.py            219      0     86      0   100%
-------------------------------------------------------------------------------
TOTAL                                1653     11    446     10    99%
```

**Confirmado por inspección**: los huecos existentes en `summary.py` (234→236: rama de `_MODELO_FORMULAS.get(modelo_key)` vacía; 707→729: ramas defensivas de la sección de ratios — liquidez/PER/PS con dato ausente) y en `query_handler.py` (303, 340-341, 373-374, 752) son **deuda preexistente sin relación con esta spec** — ninguno cae dentro de `build_valuation_scenarios_section` (líneas 190-285) ni del bloque de nivel 1 de Múltiplos en `valuation.py` (líneas 501-508). Esta cifra (`663 passed`, `TOTAL 99%`, `valuation.py 100%/100%`, `summary.py 99%`) es la vara de comparación para el criterio de exit (sección "Criterio de exit de QA" más abajo): el `TOTAL` no debe bajar de 99%, y ningún módulo de la tabla debe bajar de su cifra actual salvo donde esta sección fija explícitamente un piso distinto (ver punto 3).

### Tipo de prueba principal

**Unit testing** sobre funciones puras — mismo criterio ya vigente en `valuation.py` (100%/100% hoy) y consistente con el resto del proyecto (`rules.py`, `peers.py`, `market_context.py`, todos al 100% sin mocks). El mecanismo nuevo es una comparación aritmética (`len(list) < int`) sobre datos ya en memoria — no hay I/O que mockear, no aplica `httpx.MockTransport`. **No aplica integration testing sobre `query_handler.py`**: la propia spec (Decisión #3, confirmada por `security`) establece que ese módulo tiene **cero cambios de wiring** — los 2 únicos usos de `n_peers_validos`/`compute_valuation_scenarios` en `query_handler.py` (líneas 398 y 535) ya existen tal cual hoy. La única prueba "de integración" exigida es negativa: confirmar por grep/inspección que esos 2 puntos no cambiaron (ver punto 2). No aplica E2E/smoke — mismo criterio ya usado en toda spec anterior de este proyecto.

---

### 1. Matriz de casos límite obligatorios en `valuation.py`

Los "Criterios de aceptación" del `architect` (sección `valuation.py`) ya cubren: umbral−1 excluye, umbral exacto incluye, 0 peers sigue en `"per_peers_no_disponible"`, precedencia de `eps_ttm<=0` sobre pocos peers, `compute_valuation` con default `None` sin regresión, y la paridad con `test_valuation_adobe_scenarios`. Esto agrega los bordes que ese checklist no explicita:

- [ ] **`len(peers_usados) == MIN_PEERS_VALIDOS_PARA_MULTIPLOS + 1` (3 peers)** — no solo "exactamente el mínimo" y "mínimo−1": confirmar que 1 peer por encima del umbral también incluye Múltiplos normalmente (ya lo cubre implícitamente `test_valuation_adobe_scenarios` con 3 peers, pero ese test no aísla el umbral — parametrizar un test propio con `MIN_PEERS_VALIDOS_PARA_MULTIPLOS + 1` exactos, no reusar el fixture de Adobe como única evidencia de este borde).
- [ ] **0 peers válidos NO debe confundirse con "insuficientes" — verificado además a nivel de invariante de entrada, no solo de motivo devuelto**: `peers.py::get_peer_pe_average` garantiza que `per_promedio is None` si y solo si `peers_usados == []` (confirmado leyendo `peers.py` completo — `per_promedio`/`per_minimo`/`per_maximo` se calculan sobre la misma lista `peers_pe` cuya longitud es `len(peers_usados)`). Esto implica que, en la práctica, el nuevo `elif` de la Decisión #1 **nunca es alcanzable con `len(peers_usados) == 0`** — el `if eps_no_positivo` / `elif per_promedio is None` ya lo atrapa antes. Test explícito que documente esta garantía: `PeerAverageResult(per_promedio=None, per_minimo=None, per_maximo=None, peers_usados=[])` con `eps_ttm > 0` → motivo `"per_peers_no_disponible"`, **nunca** `"peers_validos_insuficientes"` — ya existe como `test_valuation_scenarios_multiplos_nivel1_sin_peers_validos` (ver punto 2), pero acá se pide un comentario/docstring en el test nuevo que deje constancia explícita de por qué el caso "0 peers pero `per_promedio` no-`None`" no se testea: es un estado inconsistente que `peers.py` no puede producir, no un caso ignorado por descuido.
- [ ] **Precedencia con `eps_ttm<=0` Y `len(peers_usados) == 0` simultáneos** (no solo "pocos peers", que ya pide el `architect`) — motivo debe seguir siendo `"eps_ttm_no_positivo"`, nunca `"per_peers_no_disponible"` ni `"peers_validos_insuficientes"`. Es un caso distinto del que ya pide el `architect` (`eps_ttm<=0` + pocos-pero-no-cero peers) porque ejercita que el primer `if` corta antes de siquiera evaluar `peer_average.per_promedio is None` — con 0 peers en vez de 1-a-(umbral−1), es la combinación de mayor riesgo de que un `implementer` reordene el `if/elif` "para simplificar" sin darse cuenta de que cambia cuál motivo gana.
- [ ] **El test de precedencia de EPS que ya existe en el repo no alcanza como evidencia suficiente**: `tests/test_valuation.py::test_valuation_scenarios_0_de_3_modelos_los_3_escenarios_en_none` (línea 453) usa `peer_average.peers_usados=["MSFT"]` (exactamente 1 peer, por debajo del nuevo `MIN=2`) combinado con `eps_ttm=-1.0`, y hoy pasa porque `eps_no_positivo` corta antes. Este test **seguirá pasando sin modificación** después de esta spec (confirmado: el orden del `if/elif` no cambia) y de hecho ya ejercita la precedencia por coincidencia de fixture — pero es un test escrito para otro propósito (Iter-4, C1: "0 de 3 modelos → los 3 escenarios en `None`"), no un test de precedencia intencional. **No debe contarse como cobertura del criterio de precedencia** del `architect` ni de este documento — se necesita un test nuevo, nombrado explícitamente por la intención (ej. `test_valuation_scenarios_precedencia_eps_no_positivo_sobre_peers_insuficientes`), que documente la precedencia como comportamiento buscado, no como efecto colateral de otro fixture.
- [ ] **`compute_valuation` (versión no-escenario) — mismo boundary que la versión de escenarios, en su propio test**: `n_peers_validos=MIN_PEERS_VALIDOS_PARA_MULTIPLOS` exacto (no solo `MIN-1` ya pedido por el `architect`) → Múltiplos se calcula normalmente. Sin este test, la rama `else` de la Decisión #2 (`calculate_multiplos_fair_value(...)`) queda sin ejercitar específicamente en el borde superior para esta función, aunque sí lo esté para `compute_valuation_scenarios`.
- [ ] **Tipo de `n_peers_validos` en `compute_valuation`**: `n_peers_validos=0` explícito (no `None`) con `per_promedio_peers` no-`None` — caso sintético que no puede darse en producción (ver invariante de `peers.py` arriba) pero que la firma de la función permite construir en un test (nadie impide pasar `n_peers_validos=0, per_promedio_peers=15.0` a mano) → debe devolver `"peers_validos_insuficientes"` igual que cualquier valor `< MIN`, sin excepción ni comportamiento especial para `0`. Cierra la superficie de la firma nueva, no solo el camino que produce `query_handler.py`.

---

### 2. Tests de regresión identificados por nombre exacto

**No deben cambiar una línea (verificados por grep sobre `compute_valuation`/`compute_valuation_scenarios`/`peers_usados` en todo el repo, no solo los 2 que cita el enunciado):**

- `tests/test_valuation.py::test_valuation_adobe_scenarios` (línea 705) — fixture real de Adobe, 3 peers (MSFT/ORCL/CRM), no cruza el umbral con `MIN=2`. Incluye la aserción de paridad `conservador_directo = valuation.compute_valuation(...)` **sin pasar `n_peers_validos`** (línea 772) — confirma en el propio test existente que el default `None` preserva la paridad ya testeada.
- `tests/test_valuation.py::test_valuation_scenarios_multiplos_nivel1_sin_peers_validos` (línea 481) — 0 peers, debe seguir devolviendo `"per_peers_no_disponible"`.
- `tests/test_valuation.py::test_valuation_scenarios_graham_nivel1_historial_insuficiente` (línea 502), `test_valuation_scenarios_graham_nivel1_y_no_disponible` (línea 521), `test_valuation_scenarios_dcf_nivel1_wacc_no_calculable` (línea 539), `test_valuation_scenarios_graham_nivel2_excluido_solo_en_pesimista` (línea 566), `test_valuation_scenarios_dcf_nivel2_excluido_solo_en_optimista` (línea 591) — los 5 usan `peer_average.peers_usados=["MSFT", "ORCL"]` (exactamente 2 peers, el nuevo `MIN`) y no evalúan Múltiplos en sus aserciones — **regresión directa sobre el borde exacto del umbral**: si `implementer` comete un error de signo (`<=` en vez de `<`), estos 5 tests seguirían en verde porque no assertan sobre `motivos_base.get("multiplos")`, así que **no alcanzan solos como evidencia del borde** — complementan, no reemplazan, el test explícito de "exactamente `MIN`" del punto 1.
- `tests/test_valuation.py::test_valuation_scenarios_0_de_3_modelos_los_3_escenarios_en_none` (línea 453) — usa 1 peer pero motivo esperado es `"eps_ttm_no_positivo"` (ver punto 1, no confundir con test de precedencia intencional).
- `tests/test_edge_cases.py::test_valuation_multiplos_excluido_por_peers_no_disponibles` (línea 62) — `per_promedio_peers=None`, no pasa `n_peers_validos` → debe seguir devolviendo `"per_peers_no_disponible"`.
- `tests/test_edge_cases.py::test_valuation_graham_excluido_por_y_no_disponible`, y el resto de `compute_valuation(...)` en ese archivo (líneas 84-150) — ninguno pasa `n_peers_validos`, regresión por default.
- `tests/test_valuation.py::test_compute_valuation_defaults_regresion_byte_a_byte` (línea 939) y `test_compute_valuation_scenarios_defaults_regresion_byte_a_byte` (línea 1045) — **patrón ya establecido en este mismo archivo** por la spec anterior (`SDD_eps_ttm_real.md`, Decisión #13) para `periodos_por_anio_eps`/`periodos_por_anio_fcf`/`fcf_base`: comparan `compute_valuation(**kwargs)` sin params nuevos vs. con los defaults explícitos, `.as_dict()` idéntico. **Esta spec debe agregar un test hermano con el mismo patrón para `n_peers_validos`** (ej. `test_compute_valuation_n_peers_validos_default_regresion_byte_a_byte`) — no basta con que los tests viejos de `periodos_por_anio_*` sigan pasando, hace falta el mismo tipo de test para el parámetro nuevo de esta spec específicamente.
- `tests/test_query_handler.py` — único uso de `compute_valuation_scenarios` es un monkeypatch-spy (`fake_compute_valuation_scenarios`, línea 1546, dentro de `test_fetch_and_analyze_nvda_historiales_crudos_trimestrales_cronologicos`) que llama a la función real con los kwargs que ya arma `query_handler.py` — no construye su propio `peer_average`. Confirmar (no asumir) que los fixtures de NVDA usados ahí tienen ≥2 peers válidos, para que este test no empiece a fallar por acoplamiento accidental con el nuevo umbral; si tiene menos, no es una regresión de esta spec sino una fixture que hoy vive en una zona gris que el nuevo umbral vuelve visible — reportarlo como hallazgo, no parchear el test en silencio.
- `tests/test_market_context.py` (todo el archivo, ~20 usos de `peers_usados`) — **no debe tocarse**: `compare_to_peers` no se modifica (Restricciones de la spec), su propio umbral `< 2` es independiente y ya existía antes de esta spec.
- `tests/test_summary.py` — todos los usos de `n_peers_validos=3` (≥15 call sites, ej. líneas 97, 155, 174, 185, 236, 249, 262, 283, 980, 987, 1101...) no cambian de comportamiento (3 ≥ 2, nunca dispara ni la nota vieja ni la exclusión nueva). Los usos con `n_peers_validos=0` (líneas 223, 306) tampoco cambian — ya pasan por `modelos_excluidos_base` con motivos ajenos a Múltiplos en esos fixtures, no ejercitan la Decisión #4. `MOTIVO_LABELS["eps_ttm_no_positivo"]`/`["y_no_disponible"]`/etc. (líneas 1577-1592) no cambian — la entrada nueva (`"peers_validos_insuficientes"`) se agrega al diccionario, no reemplaza ninguna existente.
- `tests/test_summary.py::test_valuation_scenarios_section_no_agrega_desglose_por_peer` (línea 640) — itera `n_peers_validos` en `(0, 1, 3)` contra `_base_scenarios()` (fixture **sin** `"multiplos"` en `modelos_excluidos_base`). Sigue pasando sin modificar porque solo assert-ea ausencia de substrings de tickers/PER, no el texto de la nota vieja que se elimina. **Nota de testabilidad, no de regresión**: después de esta spec, la combinación `n_peers_validos=1` + Múltiplos NO excluido en `modelos_excluidos_base` es **imposible en producción** (`query_handler.py` siempre deriva `n_peers_validos` del mismo `peer_result` que arma `peer_average`, y `valuation.py` ya lo habría excluido) — este test sigue siendo válido como test unitario de `summary.py` en aislamiento (la función no valida esa consistencia, por diseño, ver Testabilidad), pero no debe leerse como evidencia de comportamiento end-to-end real con 1 peer.

**Cambia a propósito, no es regresión:**

- `tests/test_summary.py::test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` (línea 190) — **se reemplaza**, no se ajusta cosméticamente (Decisión #4 del `architect`, ya marcado explícitamente en la spec). El reemplazo debe verificar 2 cosas que el test viejo no separaba: (a) el texto `"no hay rango disponible para Múltiplos"` **ya no aparece en ningún caso** (test negativo, ya pedido por `architect` en Criterios de aceptación de `summary.py`); (b) la fila de Múltiplos con valores numéricos está ausente y en su lugar aparece la línea de `excluidos_base` con el detalle `"mínimo {N}, hubo {n_peers_validos}"`. Nombre sugerido para el reemplazo, siguiendo la convención ya usada en este archivo (`test_build_valuation_scenarios_section_modelo_excluido_nivel1_no_muestra_fila`, línea 164, que ya prueba el patrón general para otro motivo): `test_build_valuation_scenarios_section_peers_validos_insuficientes_excluye_multiplos`.

---

### 3. Piso de cobertura por módulo tocado

| Archivo | Baseline real (arriba) | Piso exigido | Justificación |
|---|---|---|---|
| `valuation.py` | 100%/100% | **100% líneas + 100% ramas** (código nuevo y viejo) | Ya es el estándar vigente en este módulo hoy — el nuevo `elif` de la Decisión #1 y el nuevo parámetro `n_peers_validos` de la Decisión #2 son ramas puras sobre datos ya en memoria, del mismo tipo exacto que el resto del archivo, ya al 100%. La matriz del punto 1 (umbral exacto, umbral+1, 0 peers vs. insuficientes, precedencia con EPS en sus 2 variantes, `compute_valuation` en su propio borde) cubre cada rama nueva sin dejar ninguna defensiva fuera de alcance. No hay motivo estructural para aceptar menos que el 100% que el módulo ya tiene. |
| `summary.py` | 99% (0 líneas sin cubrir, 5 branch parciales — `234->236, 707->709, 709->714, 719->723, 723->729`, ninguno relacionado con esta spec, confirmado por inspección arriba) | **El % total del archivo no baja de 99%** sobre esa deuda preexistente; **el código nuevo específico de esta spec** (la entrada nueva en `MOTIVO_LABELS`, el caso especial `if item["motivo"] == "peers_validos_insuficientes"` dentro del loop de `excluidos_base`, y la eliminación del bloque obsoleto líneas 243-247) debe llegar a **100%/100% propio** — mismo criterio ya aplicado en `SDD_eps_ttm_real.md` sección 5 ("el piso de 100% aplica únicamente al código nuevo/modificado de esta spec", no retroactivo a deuda no relacionada). La eliminación del bloque viejo también debe reflejarse en cobertura: si el bloque muerto se deja pero deja de ejecutarse, el % del archivo *subiría* artificialmente por líneas ya no alcanzables en vez de por código nuevo cubierto — el test negativo del punto 2 (`"no hay rango disponible para Múltiplos"` ya no aparece en ningún caso) es la forma de detectar ese escenario, no la cifra de cobertura sola. |
| `query_handler.py` | 98% (6 miss, 2 branch parciales, líneas 303, 340-341, 373-374, 752 — ninguna relacionada con esta spec) | **No baja de 98%** — cero cambios de código esperados en este módulo (Decisión #3, confirmado por `security`). Si algún test nuevo de esta spec fuerza cualquier cambio en `query_handler.py` más allá de lo ya existente en las líneas 398/535, es señal de que la restricción "cero cambios de wiring" se violó — escalar a `architect`, no ajustar el piso. |

**TOTAL del repo**: no debe bajar de **99%** (baseline real de arriba). El código nuevo de esta spec es pequeño (una rama `elif` + un parámetro opcional + una entrada de diccionario + un caso especial en un loop existente) — no debería mover el TOTAL de forma perceptible, y no hay justificación para que baje.

**Nota sobre el piso genérico de CI**: el README documenta `--cov-fail-under=75` como red de seguridad mínima para todo el repo — no aplica como objetivo de esta spec, mismo argumento ya usado en specs anteriores de este proyecto (`SDD_eps_ttm_real.md`, `SDD_peers_dinamicos_y_eventos_corporativos.md`): aceptar 75% en los 2 módulos que esta spec toca sería una regresión de facto frente a la práctica ya vigente (`valuation.py` al 100%, `summary.py` al 99% hoy).

---

### 4. Testabilidad — verificado, sin hallazgos

- [x] El mecanismo nuevo (`len(peer_average.peers_usados) < MIN_PEERS_VALIDOS_PARA_MULTIPLOS`) opera enteramente sobre datos ya en memoria, sin I/O — no requiere ningún mock/fixture nuevo más allá de construir un `peers.PeerAverageResult` a mano, patrón ya usado en ~15 tests existentes de `tests/test_valuation.py`.
- [x] `compute_valuation`/`compute_valuation_scenarios` siguen siendo funciones puras (sin estado global, sin efectos secundarios) — el parámetro nuevo (`n_peers_validos`) es un `Optional[int]` inyectable directamente, sin necesidad de builder ni fixture especial.
- [x] `build_valuation_scenarios_section` (`summary.py`) ya recibe `n_peers_validos`/`modelos_excluidos_base` como parámetros explícitos, no acoplados a `valuation.py` en tiempo de test — permite (y ya lo hace hoy, ver punto 2) construir combinaciones sintéticas imposibles en producción para testear la función en aislamiento, sin necesidad de pasar por `compute_valuation_scenarios` real.
- [x] `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` es una constante de módulo importable (`from investbot.valuation import MIN_PEERS_VALIDOS_PARA_MULTIPLOS`) — los tests pueden (y deben, según el propio criterio del `architect`) parametrizar contra la constante en vez de hardcodear `2`, para que sigan siendo válidos si un spec patch futuro cambia el valor. Único punto a vigilar en Momento 2: que `implementer` efectivamente la use así en los tests nuevos, no como literal duplicado.

### Criterio de exit de QA

- Todos los tests pasan (`663 passed` hoy + los nuevos de esta spec, suite verde).
- Sin tests ignorados (`@pytest.mark.skip`) ni comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (correr 2 veces seguidas antes de cerrar Momento 2 — no debería haber ninguna fuente de no-determinismo, son funciones puras, pero se verifica igual, mismo estándar que specs anteriores).
- Cobertura: `TOTAL` ≥ 99%, `valuation.py` en 100%/100%, `summary.py` no por debajo de 99% total con el código nuevo específico al 100%/100%, `query_handler.py` no por debajo de 98% (idealmente sin cambios).
- El test de reemplazo (`test_build_valuation_scenarios_section_degenerado_menos_de_2_peers` → su sucesor) existe y el texto viejo `"no hay rango disponible para Múltiplos"` no aparece en ningún test de la suite tras el cambio — verificado con `grep -rn "no hay rango disponible" tests/` devolviendo 0 resultados.
- Los 2 tests de precedencia explícitos del punto 1 (EPS no positivo sobre peers insuficientes, y sobre 0 peers) existen con nombre propio, no delegados a un test escrito para otro propósito.

---

## Checklist final — Scope Freeze

- [ ] Las 3 preguntas abiertas — **confirmadas por Daniela 2026-07-31**, no bloqueantes (ver sección "Preguntas abiertas").
- [ ] Revisión de `security` — **sin hallazgos**, spec ya lista para `qa` antes de este documento.
- [ ] Matriz de casos límite de `valuation.py` (umbral exacto, umbral+1, 0 peers vs. insuficientes con su invariante de `peers.py` documentado, precedencia de EPS en sus 2 variantes — con 0 peers y con pocos-pero-no-cero, borde superior de `compute_valuation`) — cubierta en la sección 1, con los huecos del checklist del `architect` señalados explícitamente (no delegados en silencio a `implementer`).
- [ ] Tests de regresión identificados por archivo/nombre exacto (`test_valuation.py`, `test_edge_cases.py`, `test_query_handler.py`, `test_market_context.py`, `test_summary.py`) — cubiertos en la sección 2, con el caso de "cambio de comportamiento esperado, no regresión" (`test_build_valuation_scenarios_section_degenerado_menos_de_2_peers`) marcado aparte, y con la advertencia explícita de que 5 tests existentes en el borde exacto (`peers_usados=["MSFT","ORCL"]`) no alcanzan solos como evidencia del umbral por no assertar sobre Múltiplos.
- [ ] Piso de cobertura por módulo fijado con números concretos del baseline real corrido hoy (663 passed, TOTAL 99%), no "cobertura alta" — sección 3.
- [ ] Testabilidad verificada sin hallazgos — sección 4, sin puntos pendientes de confirmar en Momento 2 (a diferencia de `SDD_eps_ttm_real.md`, que sí dejaba un punto abierto de testabilidad para `_run_analysis`/`chat_id` — acá no aplica, no hay superficie nueva de wiring).
- [ ] Ningún criterio de esta sección requiere volver a `architect` ni a `security` — todos son criterios de testeo sobre decisiones de diseño ya cerradas y ya auditadas.

**Veredicto de `qa`:** la spec queda con **Scope Freeze lista para pasar a `implementer`**. No se implementa código ni tests en este documento — es exclusivamente la ampliación de criterios de cobertura/testabilidad sobre la spec ya diseñada y ya revisada por `security`.
