# Spec: PER individual por peer + procedencia explícita del cálculo + auditoría completa de procedencia de datos [Iter-1]

**Rol:** `architect`.
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa esta spec. Adelanto del análisis: **cero llamadas HTTP nuevas, cero endpoints nuevos** — todo el contenido de esta spec expone datos que `peers.py::get_peer_pe_average` ya calcula hoy (el PER de cada peer individual se calcula en el loop interno de esa función desde hace tiempo, línea 78 actual: `pes.append(1.0 / float(earnings_yield))`) pero descarta antes de devolver el resultado agregado. Es una spec de **exposición de datos ya calculados + una clasificación de motivo ya derivable de la misma lógica existente**, no de cálculo financiero nuevo ni de I/O nuevo. Luego `qa` agrega criterios de cobertura. **Las 2 preguntas bloqueantes que dejó abiertas la versión anterior de esta spec ya están resueltas por Daniela** (ver sección siguiente) — no quedan preguntas abiertas bloqueantes conocidas al cierre de esta iteración. El spec queda completo y listo para pasar a `security` sin gating adicional.

---

## Resolución de las 2 preguntas bloqueantes (2026-07-29)

La versión anterior de esta spec dejó 2 preguntas abiertas para Daniela. Las resolvió en conversación directa — quedan **cerradas**, no se reabren:

### Pregunta 1 — RESUELTA: el detalle de PER individual va solo en Contexto de mercado, no se duplica en Valor Justo

Daniela eligió la **Opción B** de las 2 presentadas: el detalle de PER individual por peer (nombres + valores + quiénes no respondieron) aparece **únicamente** en la sección "Contexto de mercado" (bullet de comparación con peers). La fila de "Múltiplos" en la sección de Valor Justo **no cambia más allá de lo que ya estaba decidido en la Decisión #8** (la aclaración de una línea de que el PER de peers usado ahí es "cálculo del bot, no campo directo de FMP") — sin agregar el desglose completo por peer, sin línea de referencia cruzada tampoco (Daniela no pidió ese puntero, así que no se inventa). **Consecuencia de diseño:** `build_valuation_scenarios_section` no gana ningún parámetro ni línea nueva en esta spec, más allá del texto ya fijado en la Decisión #8 original (que no dependía de esta pregunta).

### Pregunta 2 — RESUELTA: se distingue el motivo exacto por peer, no un balde genérico único

Daniela pidió explícitamente distinguir **2 motivos** por los que un peer no aporta un PER válido esta consulta:
1. **Sin dato** — la llamada a FMP para ese peer falló o no vino con un `earningsYield` utilizable (incluye la variante "el campo no es numérico", mismo criterio de "no es un dato usable" ya establecido en `rules.extract_key_metrics_extras` de la spec anterior, que tampoco distingue "ausente" de "no numérico" — un solo balde "no se pudo leer").
2. **`earningsYield` negativo o cero** — el peer tiene pérdidas, motivo financiero real y distinto del anterior (no es que la consulta falló, es que el número existe y dice que la empresa perdió plata).

**Consecuencia de diseño:** `peers_no_usados` deja de ser `list[str]` (solo nombres) y pasa a ser `dict[str, str]` (ticker → motivo, con 2 valores posibles: `"sin_dato"` / `"earnings_yield_no_positivo"`) — ver Decisión #1/#2 actualizadas abajo. El texto que arma `summary.py` agrupa por motivo y usa una frase distinta para cada uno (ver Decisión #5 actualizada).

**No se agrega ninguna categoría de motivo más allá de estas 2** — ej. no se distingue "timeout" de "402 Payment Required" de "429 rate limit" dentro de "sin_dato" (todos caen en el mismo balde, porque desde la perspectiva del peer analizado el resultado es el mismo: "no llegó un dato utilizable"), y tampoco se agrega un tercer motivo para "earningsYield exactamente 0" vs "negativo" (ambos son "no positivo", misma frase). Ampliar esa granularidad más allá de lo que Daniela pidió sería inventar categorías nuevas sin acuerdo — si lo quiere después, es una spec patch separada.

Con estas 2 resoluciones, **no queda ninguna pregunta bloqueante pendiente en esta spec.**

---

## Contexto

Daniela probó el bot real con NVIDIA. La fila de "Múltiplos" en el rango de Valor Justo (Pesimista | Conservador | Optimista) mostró el mismo valor ($177.83) en los 3 escenarios. Se le explicó — y lo aceptó — que es matemáticamente forzoso: `calculate_multiplos_fair_value` usa `per_minimo`/`per_promedio`/`per_maximo` de `peers.PeerAverageResult`, y esos 3 valores solo difieren si hay ≥2 peers con PER válido; con 1 solo peer válido (de los 3 configurados para el sector Technology: `MSFT`/`ORCL`/`CRM`), `min == promedio == máximo` por definición.

De esa conversación salieron 4 pedidos nuevos, todos aceptados explícitamente por Daniela:

1. **Mostrar el PER individual de cada peer usado, no solo el agregado.** Hoy el bot nunca dice cuál peer respondió ni con qué PER — Daniela no puede verificar nada, solo "confiar" en el número agregado. Pedido, con texto de ejemplo dado por Daniela: *"PER de tus comparables: ORCL 24.3 — MSFT y CRM no devolvieron un dato válido esta consulta"*.
2. **Aclarar que el PER de cada peer NO es un campo directo de FMP, sino calculado por el bot** (`1 / earningsYield`) — mismo criterio de transparencia que el resto del proyecto (ej. la nota de WACC, Decisión #12 de `SDD_contenido_financiero_explicado.md`).
3. **Auditoría de procedencia completa y definitiva** de todo lo que `summary.build_summary` muestra hoy (post-implementación de esa spec anterior) — sin asumir que ya está todo cubierto.
4. **Aclarar que la lista de peers por sector es fija y elegida manualmente por el desarrollador** al construir el bot, no por FMP ni por ningún algoritmo dinámico — la nota existente (`peers_note`) ya dice "fijo" pero no aclara que es una decisión manual humana.

Restricción explícita y permanente de este proyecto, repetida por Daniela para esta spec puntual: **nunca inventar un dato ni un umbral nuevo sin acuerdo explícito** — cualquier decisión de formato/threshold no dictada literalmente por su pedido queda como pregunta abierta, no se decide unilateralmente. Las 2 preguntas que esta restricción generó ya están resueltas (ver sección anterior).

---

## Auditoría de procedencia — cobertura completa de `build_summary_parts` (Ask #3)

Revisé cada dato que el bot muestra hoy en producción (`src/investbot/summary.py::build_summary_parts`, línea 441, orden real de `parts` línea 552-563), sección por sección, confirmando si su procedencia (FMP directo / FRED-Treasury.gov / cálculo propio del bot con qué fórmula) queda clara en el texto que Daniela recibe. **No asumo que la auditoría de la Decisión #13 de `SDD_contenido_financiero_explicado.md` siguió siendo válida** — la rehice completa sobre el código actual.

| Sección | Dato mostrado | Procedencia indicada hoy | ¿Hueco? |
|---|---|---|---|
| Título | `company_name`, `ticker` | Ninguna explícita, pero es identidad, no una cifra a verificar | No — no aplica (no es "un número") |
| Veredicto | Síntesis de `pillars`/`risk_fit` (booleanos, ya citados en sus propias secciones) | No necesita procedencia propia | No — ya resuelto (Decisión #13 previa) |
| Ratios clave | Liquidez, Margen bruto, PER, P/S | Cada bullet cita su fórmula (`_fórmula: ..._`); nota general de footer "obtenidos de FMP" cubre los insumos | No |
| Rentabilidad/deuda/dividendos | ROE, Debt/Equity, Net Debt/EBITDA, Dividend Yield, Payout Ratio | Cada bullet dice explícitamente "(dato de FMP)" | No |
| **Valor Justo — Múltiplos** | `per_minimo`/`per_promedio`/`per_maximo` de los peers | La fórmula mostrada (`summary.py` línea 80, `_MODELO_FORMULAS["multiplos"]`) dice *"EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector"* — **no aclara que ese PER de peers es un cálculo propio del bot (`1/earningsYield`), no un campo de FMP** | **Sí — Gap B, cerrado por la Decisión #8** (una línea de aclaración; el desglose completo por peer **no** se agrega acá — Pregunta 1 resuelta) |
| Valor Justo — Graham | `g` (CAGR de EPS) | Fórmula muestra "g = CAGR histórico de EPS" — cálculo propio implícito, ya aceptado como patrón (Decisión #13 previa) | No |
| Valor Justo — DCF | WACC (mencionado en la fórmula, línea 82) | Explicado y marcado "cálculo propio del bot" en la nota de transparencia final (Decisión #12 previa) — la explicación está lejos (al final del mensaje) de donde se menciona la palabra "WACC" (en la fila del DCF), pero cubierto | No (mejora de ubicación posible, no un hueco de contenido — no se toca en esta spec para no reabrir una decisión ya cerrada) |
| Pilares de buena empresa | Ingresos/Utilidades crecientes, Deuda controlada, Precio razonable | "(según el boletín)"/"(según la foto)" — patrón ya aceptado como suficiente (Decisión #13 previa: no repetir "de FMP" en cada bullet cuando ya lo dice el footer) | No |
| Contexto de mercado — Momentum | % vs máximo/mínimo/promedios de 52 semanas | Cubierto por nota general de footer (cálculo directo sobre `/quote`, ya documentado) | No |
| **Contexto de mercado — Comparación con peers** | Nombres de peers usados + PER propio + PER mínimo/promedio/máximo agregado de peers | El bullet dice de quién es el PER propio (ya explicado en Ratios clave) pero **no aclara que el PER agregado de los peers es un cálculo del bot, no un dato de FMP** | **Sí — Gap B, cerrado por la Decisión #5/#6** |
| **Contexto de mercado — Comparación con peers, PER individual** | — (no existe hoy) | No se muestra en absoluto qué peer individual respondió qué valor, ni cuáles no respondieron ni por qué | **Sí — Gap A, cerrado por la Decisión #5/#6** (el pedido principal, Ask #1, ahora con motivo distinguido por la Resolución de la Pregunta 2) |
| **Contexto de mercado — caso "no comparable" (`posicion == "no_comparable"`)** | — | `MOTIVO_NO_COMPARABLE_LABELS` (summary.py líneas 50-57) solo muestra un texto genérico ("Solo 1 comparable con PER válido..."); **en este branch no se muestra NINGÚN número ni nombre de peer**, ni siquiera cuando sí hay un peer válido con datos disponibles (`un_solo_peer_valido`) | **Sí — Gap D (hallazgo nuevo de esta auditoría, no reportado antes por Daniela), cerrado por la Decisión #6.** Es exactamente el caso NVIDIA: `compare_to_peers` sí calcula `per_minimo_peers == per_promedio_peers == per_maximo_peers` (todos iguales al único peer válido) y sabe su nombre en `peers_usados`, pero `build_market_context_section` (líneas 254-258) nunca los imprime en esta rama — Daniela vio *cero* cifras de peers en Contexto de mercado, solo la frase genérica |
| Contexto de mercado — VIX | Nivel del VIX | "Dato de FMP (símbolo ^VIX)" explícito | No |
| Encaje de riesgo | Beta, "renta variable" | "Dato de FMP" explícito para beta; explicación conceptual para "renta variable" | No |
| Notas de transparencia — FMP general | (paraguas de todo lo no citado individualmente) | Presente, sin cambios | No |
| **Notas de transparencia — `peers_note`** | "PER promedio de un set fijo de comparables, no del sector completo" | Dice "fijo" pero **no aclara que la lista la eligió a mano el desarrollador, no FMP ni un algoritmo** | **Sí — Gap C (Ask #4), cerrado por la Decisión #7** |
| Notas de transparencia — Y/treasury_source | Tasa del bono | "obtenida de: {treasury_source}" | No |
| Notas de transparencia — WACC | — | Cálculo propio explícito (Decisión #12 previa) | No |
| Notas de transparencia — disclaimer general | — | Presente, no es procedencia de dato | No |

**Conclusión de la auditoría:** 4 huecos reales, no 3 — el pedido original de Daniela (Asks #1/#2/#4) cubre 3, y esta auditoría encontró un cuarto (Gap D) que es, de hecho, la causa técnica exacta por la que Daniela vio "nada" sobre peers en Contexto de mercado para NVIDIA (no vio ni siquiera la cifra que sí estaba calculada). Esta spec cierra los 4.

---

## Estado actual

- `src/investbot/peers.py::PeerAverageResult` (líneas 32-37): solo guarda `per_promedio`/`per_minimo`/`per_maximo`/`peers_usados` (nombres, sin valor individual, sin motivo de exclusión).
- `src/investbot/peers.py::get_peer_pe_average` (líneas 46-89): el loop (líneas 72-79) calcula `per_peer = 1.0 / float(earnings_yield)` **por cada peer individualmente** y lo agrega a la lista `pes` para promediar — **el valor individual se descarta apenas se usa para el agregado**, nunca se guarda asociado a su ticker. Tampoco se guarda qué peers de `PEERS_BY_SECTOR` fueron candidatos pero no llegaron a `usados`, ni por qué motivo (`metrics` vacío/ausente vs. `earningsYield` inválido/≤0 son hoy indistinguibles porque ambos caen en el mismo `continue` sin dejar rastro) — es derivable en conjunto (`get_peers_for_sector(sector, own_ticker)` menos `peers_usados`) pero hoy no se calcula ni se expone en ningún dataclass, y el motivo específico ni siquiera se computa internamente hoy.
- `src/investbot/market_context.py::PeerComparisonResult` (líneas 130-140) y `compare_to_peers` (líneas 143-211): mismo problema, un nivel más arriba — recibe y reexpone `peers_usados` (nombres) pero nunca el PER individual ni la lista de fallidos con motivo. Las 3 ramas de `posicion == "no_comparable"` (líneas 165-194) devuelven un `PeerComparisonResult` sin ningún dato numérico de peers, aunque el llamador (`query_handler.py`) sí tiene esos datos disponibles en `peer_result`.
- `src/investbot/query_handler.py` (líneas 164-166, 241-247, 276-284): arma `peer_result` (vía `peers.get_peer_pe_average`), lo pasa a `market_context.compare_to_peers` y arma `peer_comparison_dict` — ninguno de los 3 puntos propaga PER individual ni lista de fallidos con motivo porque las capas de abajo no los exponen todavía.
- `src/investbot/summary.py::build_market_context_section` (líneas 217-291): arma el bullet de "Comparada con sus comparables del sector" con nombres + agregado (líneas 259-271) o el texto genérico de `MOTIVO_NO_COMPARABLE_LABELS` (líneas 254-258) — ninguna rama muestra PER individual ni motivo por peer.
- `src/investbot/summary.py::_MODELO_FORMULAS["multiplos"]` (línea 80): texto de fórmula de la fila de Múltiplos en Valor Justo, sin aclarar procedencia del PER de peers.
- `src/investbot/summary.py::build_summary_parts`/`build_summary` (líneas 441-604): parámetro `peers_note` con default `"PER promedio de un set fijo de comparables, no del sector completo."` (líneas 454, 580) — sin mención de que la lista es manual.
- `tests/test_peers.py`, `tests/test_market_context.py`, `tests/test_summary.py`: cubren el comportamiento actual (agregado únicamente) — ningún test hoy verifica PER individual ni motivo de exclusión por peer, porque el dato no existe todavía en ningún dataclass.

---

## Estado objetivo

1. `peers.PeerAverageResult` expone, además de lo que ya expone hoy, el PER individual de cada peer que sí respondió (`peers_pe: dict[str, float]`) y, para cada peer candidato que no aportó un PER válido esta consulta, el **motivo específico** (`peers_no_usados: dict[str, str]`, valores `"sin_dato"` / `"earnings_yield_no_positivo"`) — sin llamada HTTP nueva: son datos ya calculados o ya derivables de la misma lógica existente del loop, solo que hoy se descartan sin guardarse.
2. `market_context.PeerComparisonResult`/`compare_to_peers` propagan esos 2 campos nuevos sin cambiar ninguna de las 3 reglas de clasificación existentes (`mas_barata`/`en_linea`/`mas_cara`/`no_comparable` y sus 3 motivos de "no comparable").
3. `query_handler.py` propaga esos 2 campos nuevos hasta `peer_comparison_dict` — cero llamadas HTTP nuevas, cero cambio de firma pública de `fetch_and_analyze`/`fetch_and_analyze_parts`.
4. `summary.build_market_context_section` muestra, en **todas** las variantes del bullet de comparación con peers (incluida la rama `no_comparable`, que hoy no muestra nada — Gap D), el PER individual de cada peer que respondió + el nombre y el motivo específico de los que no, con la aclaración explícita de que ese PER es un cálculo del bot (`1 / earningsYield`), no un dato directo de FMP.
5. `summary.py::_MODELO_FORMULAS["multiplos"]` gana la aclaración de una línea de que el PER de peers usado en Múltiplos es el mismo cálculo del bot. **El desglose completo por peer NO se agrega en Valor Justo** (Pregunta 1 resuelta: vive solo en Contexto de mercado) — `build_valuation_scenarios_section` no gana ningún parámetro nuevo en esta spec.
6. `peers_note` (default de `build_summary_parts`/`build_summary`) se expande para decir explícitamente que la lista de peers por sector es fija y fue elegida a mano por el desarrollador del bot, no por FMP ni por ningún algoritmo dinámico.
7. Ninguna fórmula existente cambia una sola línea de lógica — es estrictamente exposición de un dato ya calculado + una clasificación de motivo derivable de la misma lógica existente + texto nuevo.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch; no quedan preguntas bloqueantes, ver "Resolución de las 2 preguntas bloqueantes" arriba)*

### 1. `peers.py` — `PeerAverageResult` gana 2 campos nuevos, con default para no romper firma

```python
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# Motivos posibles por los que un peer candidato no aporta un PER válido
# esta consulta (Resolución de la Pregunta 2 — 2 motivos, ninguno más):
PEER_MOTIVO_SIN_DATO = "sin_dato"
# La llamada a /key-metrics para ese peer falló, vino vacía, o el campo
# earningsYield no vino en forma numérica utilizable. No se distingue el
# motivo técnico exacto (timeout/402/429/campo ausente/campo no numérico)
# — todos son "no llegó un dato utilizable", mismo criterio ya usado en
# rules.extract_key_metrics_extras (spec anterior) para "ausente" vs "no
# numérico".
PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO = "earnings_yield_no_positivo"
# earningsYield sí vino como número, pero es <= 0 — utilidades negativas o
# nulas del peer. Señal financiera real, distinta de "sin dato".


@dataclass
class PeerAverageResult:
    per_promedio: Optional[float]
    per_minimo: Optional[float]
    per_maximo: Optional[float]
    peers_usados: list[str]
    peers_pe: dict[str, float] = field(default_factory=dict)
    # NUEVO: PER individual de cada peer que sí devolvió un dato válido esta
    # consulta, ya calculado como 1/earningsYield (mismo valor que hoy se
    # promedia y se descarta — no es un cálculo nuevo, es guardar lo que ya
    # se calculaba). Preserva el orden de PEERS_BY_SECTOR (dict insertion
    # order en Python 3.7+).
    peers_no_usados: dict[str, str] = field(default_factory=dict)
    # NUEVO (Resolución Pregunta 2): ticker -> motivo, para cada peer
    # candidato del sector (excluyendo el ticker propio) que esta consulta
    # NO devolvió un PER válido. Valores: PEER_MOTIVO_SIN_DATO |
    # PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO. Reemplaza el diseño anterior
    # (list[str], solo nombres sin motivo) — ya resuelto, no es un cambio en
    # discusión.
```

### 2. `peers.py::get_peer_pe_average` — puebla los 2 campos nuevos dentro del mismo loop existente, clasificando el motivo, sin llamada adicional

```python
async def get_peer_pe_average(
    *, get_peer_metrics_fn, sector: str, own_ticker: str,
) -> PeerAverageResult:
    peers_candidatos = get_peers_for_sector(sector, own_ticker)
    pes: list[float] = []
    usados: list[str] = []
    peers_pe: dict[str, float] = {}
    peers_no_usados: dict[str, str] = {}

    for peer in peers_candidatos:
        metrics = await get_peer_metrics_fn(peer)
        if not metrics:
            peers_no_usados[peer] = PEER_MOTIVO_SIN_DATO
            continue

        earnings_yield = metrics.get("earningsYield")
        if not isinstance(earnings_yield, (int, float)):
            # Campo ausente o no numérico -> mismo balde que "sin dato
            # utilizable" (Resolución Pregunta 2, no se agrega un 3er motivo).
            peers_no_usados[peer] = PEER_MOTIVO_SIN_DATO
            continue

        if earnings_yield > 0:
            per_peer = 1.0 / float(earnings_yield)
            pes.append(per_peer)
            usados.append(peer)
            peers_pe[peer] = per_peer
        else:
            peers_no_usados[peer] = PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO

    if not pes:
        return PeerAverageResult(
            per_promedio=None, per_minimo=None, per_maximo=None,
            peers_usados=[], peers_pe={}, peers_no_usados=peers_no_usados,
        )
    return PeerAverageResult(
        per_promedio=sum(pes) / len(pes),
        per_minimo=min(pes),
        per_maximo=max(pes),
        peers_usados=usados,
        peers_pe=peers_pe,
        peers_no_usados=peers_no_usados,
    )
```

**Cero cambio de comportamiento numérico** — `pes`, `usados`, `per_promedio/minimo/maximo` se calculan exactamente igual que hoy; lo único nuevo es que `per_peer` (ya calculado) se guarda en `peers_pe[peer]`, y cada peer descartado ahora se clasifica en uno de los 2 motivos ya existentes implícitamente en las condiciones `if` de siempre (el `if not metrics` y el `if earnings_yield > 0` ya estaban ahí — esta spec solo etiqueta cuál rama se tomó, no agrega ninguna condición nueva).

### 3. `market_context.py` — `PeerComparisonResult`/`compare_to_peers` propagan los 2 campos nuevos, sin tocar la lógica de clasificación

```python
@dataclass
class PeerComparisonResult:
    per_propio: Optional[float]
    per_minimo_peers: Optional[float]
    per_promedio_peers: Optional[float]
    per_maximo_peers: Optional[float]
    peers_usados: list[str]
    posicion: str
    motivo_no_comparable: Optional[str] = None
    peers_pe: dict[str, float] = field(default_factory=dict)      # NUEVO
    peers_no_usados: dict[str, str] = field(default_factory=dict) # NUEVO (ticker -> motivo)


def compare_to_peers(
    *,
    per_propio: Optional[float],
    per_minimo_peers: Optional[float],
    per_promedio_peers: Optional[float],
    per_maximo_peers: Optional[float],
    peers_usados: list[str],
    peers_pe: dict[str, float] | None = None,        # NUEVO, default {} si no se pasa
    peers_no_usados: dict[str, str] | None = None,   # NUEVO, default {} si no se pasa
) -> PeerComparisonResult:
    peers_pe = peers_pe or {}
    peers_no_usados = peers_no_usados or {}
    # ... misma lógica de clasificación existente sin cambios ...
    # cada uno de los 4 `return PeerComparisonResult(...)` (las 3 ramas
    # "no_comparable" + la rama comparable) agrega
    # `peers_pe=peers_pe, peers_no_usados=peers_no_usados` — el mismo valor
    # en las 4, porque estos 2 campos no dependen de qué rama de
    # clasificación se tomó, solo de qué devolvió `peers.py`.
```

**Los 3 motivos de `no_comparable` (`eps_no_positivo`/`sin_peers_validos`/`un_solo_peer_valido`) no cambian su lógica de decisión** — solo dejan de "perder" los datos de `peers_pe`/`peers_no_usados` en el camino, que es exactamente el Gap D encontrado en la auditoría. **Nota de nomenclatura:** el motivo `"eps_no_positivo"` de `motivo_no_comparable` (por qué el ticker propio no es comparable) y el motivo `"earnings_yield_no_positivo"` de `peers_no_usados` (por qué un peer individual no aporta PER) son conceptos distintos que suenan parecido — no se unifican ni se renombran, cada uno vive en su propio campo con su propio significado (uno es sobre el ticker propio, el otro sobre cada peer).

### 4. `query_handler.py` — wiring, sin cambio de firma pública

```python
peer_comparison_result = market_context.compare_to_peers(
    per_propio=per_result.per,
    per_minimo_peers=peer_result.per_minimo,
    per_promedio_peers=peer_result.per_promedio,
    per_maximo_peers=peer_result.per_maximo,
    peers_usados=peer_result.peers_usados,
    peers_pe=peer_result.peers_pe,               # NUEVO
    peers_no_usados=peer_result.peers_no_usados, # NUEVO (dict ticker -> motivo)
)
...
peer_comparison_dict = {
    "per_propio": peer_comparison_result.per_propio,
    "per_minimo_peers": peer_comparison_result.per_minimo_peers,
    "per_promedio_peers": peer_comparison_result.per_promedio_peers,
    "per_maximo_peers": peer_comparison_result.per_maximo_peers,
    "peers_usados": peer_comparison_result.peers_usados,
    "posicion": peer_comparison_result.posicion,
    "motivo_no_comparable": peer_comparison_result.motivo_no_comparable,
    "peers_pe": peer_comparison_result.peers_pe,               # NUEVO
    "peers_no_usados": peer_comparison_result.peers_no_usados, # NUEVO
}
```

`fetch_and_analyze`/`fetch_and_analyze_parts` no cambian de firma — el cambio es interno a cómo se arma `peer_comparison_dict`.

### 5. `summary.py` — nueva línea de detalle de PER individual, agrupada por motivo, texto y gramática fijados

Texto base tomado literalmente del ejemplo de Daniela ("PER de tus comparables: ORCL 24.3 — MSFT y CRM no devolvieron un dato válido esta consulta"), ahora extendido para agrupar los peers fallidos por motivo (Resolución Pregunta 2) con concordancia singular/plural por grupo:

```python
_MOTIVO_PEER_LABELS = {
    "sin_dato": {
        "singular": "no devolvió un dato de FMP esta consulta",
        "plural": "no devolvieron un dato de FMP esta consulta",
    },
    "earnings_yield_no_positivo": {
        "singular": (
            "tiene pérdidas esta consulta (earningsYield negativo o cero) "
            "— no se puede calcular su PER"
        ),
        "plural": (
            "tienen pérdidas esta consulta (earningsYield negativo o cero) "
            "— no se puede calcular su PER"
        ),
    },
}


def _join_con_y(items: list[str]) -> str:
    """'A' | 'A y B' | 'A, B y C' — lista en castellano con conjunción final."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} y {items[-1]}"


def _agrupar_peers_por_motivo(peers_no_usados: dict[str, str]) -> dict[str, list[str]]:
    """Agrupa ticker->motivo en motivo->[tickers], preservando el orden de
    aparición (mismo orden que PEERS_BY_SECTOR, porque peers_no_usados se
    construye en ese orden en peers.py)."""
    grupos: dict[str, list[str]] = {}
    for peer, motivo in peers_no_usados.items():
        grupos.setdefault(motivo, []).append(peer)
    return grupos


def _build_peer_pe_breakdown_line(
    peers_pe: dict[str, float], peers_no_usados: dict[str, str]
) -> Optional[str]:
    """Detalle de PER individual por peer (Ask #1) + motivo específico por
    peer que no aportó dato (Resolución Pregunta 2) + procedencia explícita
    del cálculo (Ask #2). `None` si no hay absolutamente ningún dato de
    peers que mostrar (sector sin peers configurados en PEERS_BY_SECTOR)."""
    if not peers_pe and not peers_no_usados:
        return None

    clausulas: list[str] = []
    if peers_pe:
        listado = ", ".join(
            f"{nombre} {_fmt_ratio(valor)}" for nombre, valor in peers_pe.items()
        )
        clausulas.append(f"PER de tus comparables: {listado}")

    for motivo, nombres in _agrupar_peers_por_motivo(peers_no_usados).items():
        forma = "singular" if len(nombres) == 1 else "plural"
        texto_motivo = _MOTIVO_PEER_LABELS.get(motivo, {}).get(
            forma, "no tiene un PER válido esta consulta"
        )
        clausulas.append(f"{_join_con_y(nombres)} {texto_motivo}")

    texto = " — ".join(clausulas) + "."
    return (
        f"  _{texto} (PER individual calculado por el bot como "
        "1 / earningsYield — earningsYield sí es un dato de FMP, el PER no)._"
    )
```

**Ejemplo con motivo mixto** (ORCL válido, MSFT sin dato, CRM con pérdidas):

```
PER de tus comparables: ORCL 24.3 — MSFT no devolvió un dato de FMP esta
consulta — CRM tiene pérdidas esta consulta (earningsYield negativo o cero)
— no se puede calcular su PER. (PER individual calculado por el bot como
1 / earningsYield — earningsYield sí es un dato de FMP, el PER no).
```

**Texto y gramática fijados** (mismo criterio que la explicación de renta variable/beta en `SDD_contenido_financiero_explicado.md`, Decisión #11: `implementer` no reformula el contenido conceptual — puede ajustar puntuación/Markdown menor si Telegram lo requiere, pero no debe fusionar los 2 motivos en un texto genérico ni agregar un 3er motivo).

### 6. Ubicación: se agrega en `build_market_context_section`, en TODAS las ramas del bullet de peers — cierra el Gap D

```python
def build_market_context_section(
    *, precio_actual: float, momentum: dict, peer_comparison: dict,
    vix: Optional[dict] = None,
) -> str:
    ...  # bullets de momentum sin cambios
    posicion = peer_comparison.get("posicion")
    peers_pe = peer_comparison.get("peers_pe") or {}
    peers_no_usados = peer_comparison.get("peers_no_usados") or {}

    if posicion == "no_comparable":
        motivo = peer_comparison.get("motivo_no_comparable")
        texto = MOTIVO_NO_COMPARABLE_LABELS.get(motivo, "no se pudo comparar con tus peers.")
        lines.append(f"- Comparada con sus comparables del sector: {texto}")
    else:
        # ... bullet existente de min/promedio/máximo, sin cambios ...

    peer_breakdown_line = _build_peer_pe_breakdown_line(peers_pe, peers_no_usados)
    if peer_breakdown_line:
        lines.append(peer_breakdown_line)

    ...  # bullet de VIX y nota final sin cambios
```

**Se agrega DESPUÉS del bullet existente (sea cual sea su rama), ANTES del bullet de VIX** — no reordena nada más de la sección. Esta única inserción, fuera del `if/else` de `posicion`, es justamente lo que cierra el Gap D: antes, la rama `no_comparable` nunca imprimía nada de `peers_pe`; ahora sí, con motivo distinguido, sin tocar el texto genérico existente de `MOTIVO_NO_COMPARABLE_LABELS` (que sigue intacto, ver Criterios de aceptación — no se rompe `test_market_context_section_peer_comparison_un_solo_peer_valido`).

### 7. `peers_note` — texto expandido (Ask #4)

Nuevo default para el parámetro `peers_note` de `build_summary_parts`/`build_summary`:

```python
peers_note: str = (
    "PER promedio de un set fijo de comparables, no del sector completo. "
    "Esta lista de comparables (peers) por sector es fija y fue elegida a "
    "mano por quien construyó el bot (ver peers.py, diccionario "
    "PEERS_BY_SECTOR) — no la arma FMP, ni la elige ningún algoritmo "
    "dinámico, ni se actualiza sola; si una empresa deja de ser un buen "
    "comparable, hay que cambiarla manualmente en el código."
)
```

Ningún test existente verifica el string exacto de `peers_note` (confirmado por búsqueda en `tests/test_summary.py`) — el cambio de default es seguro.

### 8. `_MODELO_FORMULAS["multiplos"]` — aclaración mínima de procedencia (parte de Ask #2, sin el desglose completo — Pregunta 1 resuelta como "no duplicar")

```python
_MODELO_FORMULAS = {
    "multiplos": (
        "EPS (TTM) × PER promedio/mínimo/máximo de los peers del sector "
        "(el PER de cada peer es un cálculo del bot: 1 / earningsYield, no "
        "un campo directo de FMP)"
    ),
    ...  # graham, dcf sin cambios
}
```

Esta es la única modificación de la sección de Valor Justo en toda esta spec — **no se agrega el desglose por peer ni una línea de referencia cruzada acá** (Resolución de la Pregunta 1: el desglose vive solo en Contexto de mercado).

---

## Criterios de aceptación

### `peers.py`
- [ ] `PeerAverageResult` tiene los campos nuevos `peers_pe: dict[str, float]` y `peers_no_usados: dict[str, str]`, ambos con default vacío — no rompe ninguna instanciación existente en tests.
- [ ] Con 3/3 peers válidos → `peers_pe` tiene las 3 entradas con el PER correcto (`1/earningsYield`), `peers_no_usados == {}`.
- [ ] Con 1/3 peers válidos, 1 fallido por `metrics=None` y 1 por `earningsYield<=0` (caso mixto) → `peers_pe` tiene 1 entrada, `peers_no_usados` tiene exactamente `{"<ticker-sin-dato>": "sin_dato", "<ticker-perdidas>": "earnings_yield_no_positivo"}`.
- [ ] `earningsYield` presente pero no numérico (ej. string) → se clasifica como `"sin_dato"`, no como `"earnings_yield_no_positivo"` (test explícito que distingue ambos casos).
- [ ] `earningsYield == 0` exacto (no negativo) → se clasifica como `"earnings_yield_no_positivo"` (mismo balde que negativo, sin un 3er motivo para "cero" vs "negativo").
- [ ] Con 0/3 peers válidos → `peers_pe == {}`, `peers_no_usados` tiene los 3 candidatos, cada uno con su motivo correspondiente.
- [ ] `peers_no_usados` nunca incluye el ticker propio (ya excluido por `get_peers_for_sector`).
- [ ] Test de regresión: `per_promedio`/`per_minimo`/`per_maximo`/`peers_usados` calculan exactamente los mismos valores que antes de esta spec (ningún cambio numérico).

### `market_context.py`
- [ ] `compare_to_peers` acepta `peers_pe`/`peers_no_usados` como parámetros opcionales (default `{}`/`{}`) — no rompe ningún call site existente que no los pase.
- [ ] Las 4 ramas de retorno (`eps_no_positivo`, `sin_peers_validos`, `un_solo_peer_valido`, comparable) propagan `peers_pe`/`peers_no_usados` idénticos a los recibidos (incluyendo el motivo por peer) — test explícito para cada rama.
- [ ] Ningún cambio en la lógica de `posicion`/`motivo_no_comparable` (test de regresión sobre los criterios ya existentes en `tests/test_market_context.py`).

### `query_handler.py`
- [ ] `peer_comparison_dict` incluye las 2 claves nuevas, pobladas desde `peer_result.peers_pe`/`peer_result.peers_no_usados` (este último con motivo por peer, no solo nombres).
- [ ] `fetch_and_analyze`/`fetch_and_analyze_parts` no cambian de firma pública — test de regresión de `test_fetch_and_analyze_adobe_end_to_end` sigue pasando sin modificación.
- [ ] Cero llamadas HTTP nuevas — test/inspección que confirma que no se agregó ningún `await fmp_client.*` nuevo.

### `summary.py`
- [ ] `_build_peer_pe_breakdown_line({}, {})` → `None` (sin dato de peers en absoluto, ej. sector no configurado).
- [ ] `_build_peer_pe_breakdown_line({"ORCL": 24.3}, {"MSFT": "sin_dato", "CRM": "sin_dato"})` → contiene `"PER de tus comparables: ORCL 24.3"`, `"MSFT y CRM"`, `"no devolvieron un dato de FMP esta consulta"` (plural, mismo motivo agrupado), y la aclaración `"1 / earningsYield"`.
- [ ] `_build_peer_pe_breakdown_line({"ORCL": 24.3}, {"MSFT": "sin_dato", "CRM": "earnings_yield_no_positivo"})` (motivo mixto) → contiene **2 cláusulas separadas**: `"MSFT no devolvió un dato de FMP esta consulta"` (singular, un solo nombre en ese motivo) Y `"CRM tiene pérdidas esta consulta"` — nunca fusiona ambos motivos en una sola frase ni usa un texto genérico para los 2.
- [ ] `_build_peer_pe_breakdown_line({"ORCL": 24.3}, {"MSFT": "sin_dato"})` (1 solo fallido) → usa singular `"MSFT no devolvió un dato de FMP esta consulta"` (no plural).
- [ ] `_build_peer_pe_breakdown_line({"ORCL": 24.3, "MSFT": 22.1, "CRM": 20.5}, {})` → sin ninguna cláusula de fallidos, termina en punto simple.
- [ ] `_build_peer_pe_breakdown_line({}, {"MSFT": "sin_dato", "ORCL": "earnings_yield_no_positivo", "CRM": "sin_dato"})` (0 válidos, motivo mixto) → sin el prefijo "PER de tus comparables:", con 2 cláusulas de motivo (`"MSFT y CRM"` agrupados en "sin_dato" plural, `"ORCL"` solo en "earnings_yield_no_positivo" singular).
- [ ] `build_market_context_section` con `posicion="no_comparable"` y `motivo_no_comparable="un_solo_peer_valido"` (caso NVIDIA) → el texto sigue conteniendo `"Solo 1 comparable con PER válido en tu set de peers"` (no se rompe `test_market_context_section_peer_comparison_un_solo_peer_valido`) **y además** ahora contiene el detalle de PER individual + motivo de los otros 2 si `peer_comparison["peers_pe"]`/`["peers_no_usados"]` vienen poblados en el test.
- [ ] `build_market_context_section` con `posicion="en_linea"` (caso ya cubierto hoy) → sigue mostrando el bullet agregado existente sin cambios, más la nueva línea de detalle a continuación.
- [ ] La nueva línea aparece siempre después del bullet de peers existente (sea cual sea su rama) y antes del bullet de VIX — test de orden de aparición en el string.
- [ ] `peers_note` (default nuevo) menciona explícitamente "elegida a mano"/"manual" y "no la arma FMP" — test que verifica substrings, no el string completo (para no acoplar el test a la redacción exacta más de lo necesario).
- [ ] `_MODELO_FORMULAS["multiplos"]` incluye la aclaración "cálculo del bot" / "no un campo directo de FMP".
- [ ] `build_valuation_scenarios_section` **no** cambia de firma ni de comportamiento más allá del texto de `_MODELO_FORMULAS["multiplos"]` — test de regresión que confirma que no aparece ningún desglose por peer en la sección de Valor Justo (Resolución Pregunta 1).

---

## Artefactos a crear/modificar

- `src/investbot/peers.py` → constantes `PEER_MOTIVO_SIN_DATO`/`PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO`; `PeerAverageResult` (2 campos nuevos, `peers_no_usados` es `dict[str, str]`); `get_peer_pe_average` (clasifica motivo dentro del loop existente).
- `src/investbot/market_context.py` → `PeerComparisonResult` (2 campos nuevos), `compare_to_peers` (2 parámetros nuevos opcionales, propagados en las 4 ramas de retorno).
- `src/investbot/query_handler.py` → llamada a `compare_to_peers` (2 kwargs nuevos), `peer_comparison_dict` (2 claves nuevas). Sin cambio de firma pública.
- `src/investbot/summary.py` → `_MOTIVO_PEER_LABELS`, `_join_con_y`, `_agrupar_peers_por_motivo`, `_build_peer_pe_breakdown_line` (funciones/constantes nuevas); `build_market_context_section` (inserción de la nueva línea); `_MODELO_FORMULAS["multiplos"]` (texto expandido); default de `peers_note` en `build_summary_parts`/`build_summary` (texto expandido). `build_valuation_scenarios_section` **no se toca** más allá de lo ya cubierto por `_MODELO_FORMULAS`.
- `tests/test_peers.py` → casos nuevos para `peers_pe`/`peers_no_usados` con motivo (3/3, 1/3 con motivo mixto, 0/3 con motivo mixto, caso "no numérico" vs "ausente" vs "earningsYield<=0").
- `tests/test_market_context.py` → casos nuevos para propagación de `peers_pe`/`peers_no_usados` (con motivo) en las 4 ramas de `compare_to_peers`.
- `tests/test_summary.py` → casos nuevos para `_build_peer_pe_breakdown_line` (aislado, incluyendo motivo mixto) y para `build_market_context_section` con los campos nuevos poblados en cada rama, incluida `un_solo_peer_valido`.
- `tests/test_query_handler.py` → test de regresión confirmando que `peer_comparison_dict` incluye las 2 claves nuevas y que no se agregó ninguna llamada HTTP nueva.
- `README.md` → no requiere cambios obligatorios para esta spec (la Pregunta 1 se resolvió como "no duplicar en Valor Justo", así que no hay una decisión nueva de UI que documentar ahí más allá de lo que ya dice sobre el cálculo `1/earningsYield`).

---

## Restricciones

- **Ninguna fórmula existente cambia una sola línea de lógica.** PER de cada peer sigue siendo `1/earningsYield`; PER promedio/mínimo/máximo se calculan exactamente igual; Múltiplos/Graham/DCF/clasificación barata-cara no cambian.
- **Cero llamadas HTTP nuevas, cero endpoints nuevos.** Todo el dato ya se obtiene hoy vía `fmp_client.get_key_metrics` por peer (ya llamado hoy, una vez por peer, sin cambios) — esta spec solo cambia qué se guarda del resultado ya recibido.
- **La distinción de motivo se limita a las 2 categorías que Daniela pidió explícitamente** (`sin_dato` / `earnings_yield_no_positivo`) — no se agrega granularidad adicional no pedida (ej. no se distingue 402/429/timeout dentro de "sin dato", no se distingue "cero" de "negativo" dentro de "earnings_yield_no_positivo"). Si Daniela quiere más granularidad después, es una spec patch separada con la nueva categoría acordada explícitamente.
- **No se toca la explicación de WACC ni su ubicación** (Decisión #12 de la spec anterior, ya cerrada) — la mención de "cerca vs. lejos" en la auditoría es solo observación, no una acción de esta spec.
- **No se toca ningún criterio de clasificación `mas_barata`/`en_linea`/`mas_cara`/`no_comparable`** — los campos nuevos son puramente informativos, agregados sin condicionar ninguna rama existente.
- **`peers_pe`/`peers_no_usados` no participan de ningún pilar, escenario de valoración, ni clasificación barata/cara** — mismo principio que ROE/deuda/dividendos en la spec anterior (Decisión #6 de esa spec): puramente informativo.
- **`build_valuation_scenarios_section` (Valor Justo) no gana el desglose por peer** — Resolución de la Pregunta 1: ese detalle vive únicamente en `build_market_context_section`.

---

## Preguntas abiertas

**Ninguna.** Las 2 preguntas bloqueantes que dejó la versión anterior de esta spec (duplicar o no el desglose en Valor Justo; motivo genérico o distinguido por peer) fueron resueltas explícitamente por Daniela — ver "Resolución de las 2 preguntas bloqueantes" al principio de este documento. El spec queda completo y listo para pasar a `security` sin gating adicional.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_procedencia_peers_individuales.md` (esta spec, ya con las 2 preguntas bloqueantes resueltas).

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — cubre `peers.py`, `market_context.py`, `query_handler.py`, `summary.py`.

### Decisiones de diseño tomadas
- Cero I/O nuevo, cero endpoint nuevo — exposición de un valor ya calculado (`1/earningsYield` por peer) que hoy se descarta tras el promedio, más una clasificación de motivo derivable de condiciones `if` que ya existían en el loop (no se agrega ninguna condición nueva, solo se etiqueta cuál rama se tomó).
- 2 campos nuevos en 2 dataclasses (`PeerAverageResult`, `PeerComparisonResult`) — `peers_no_usados` es `dict[str, str]` (ticker → motivo), no `list[str]` — propagados sin condicionar ninguna lógica de clasificación existente.
- Motivo limitado a exactamente 2 categorías (`sin_dato` / `earnings_yield_no_positivo`), resueltas explícitamente por Daniela — no ampliar sin spec patch.
- Texto y gramática de la nueva línea de detalle fijados (Decisión #5), incluyendo agrupación por motivo — no reformular ni fusionar motivos.
- `peers_note` expandido (Decisión #7) — no rompe tests existentes (verificado, ninguno asocia el string exacto).
- Gap D (rama `no_comparable` sin ningún dato de peers, causa exacta del caso NVIDIA) cerrado insertando la nueva línea fuera del `if/else` de `posicion` (Decisión #6), sin tocar el texto genérico existente de `MOTIVO_NO_COMPARABLE_LABELS`.
- El desglose por peer **no** se duplica en Valor Justo (Decisión #8 + Resolución Pregunta 1) — `build_valuation_scenarios_section` solo gana una línea de aclaración de procedencia, nada más.
- **No quedan preguntas abiertas bloqueantes.** `security` puede revisar de punta a punta sin esperar ninguna resolución adicional de Daniela.

---

## Criterios QA para Spec: PER individual por peer + procedencia [Iter-1]

**Rol:** `qa`. Leído: spec completa (arriba), `src/investbot/peers.py`, `src/investbot/market_context.py`, `src/investbot/summary.py`, `src/investbot/query_handler.py`, y sus 4 suites de test correspondientes. Baseline de cobertura verificado corriendo `pytest --cov=investbot --cov-branch` hoy mismo (antes de esta spec): **`peers.py` 100% líneas/100% ramas, `market_context.py` 100%/100%, `summary.py` 96%/96% (huecos preexistentes no relacionados con peers), `query_handler.py` 97%/~96% (ídem)** — 138 tests pasan en los 4 archivos de test relevantes.

### Tipo de prueba principal

**Unit Testing** para `peers.py`, `market_context.py`, `summary.py` (funciones puras, sin I/O, mismo patrón ya establecido en el proyecto). **Un test de integración acotado** en `test_query_handler.py` para el wiring end-to-end (`peer_comparison_dict` poblado desde `peer_result` a través de `compare_to_peers`, sin llamadas HTTP nuevas) — no se justifica E2E porque no hay ningún flujo de usuario nuevo, solo texto adicional en una respuesta que ya se arma hoy.

---

### 1. Impacto en tests existentes

**Conclusión: ningún test existente se rompe.** Verificado por inspección + evidencia, no solo por lectura de la spec:

- `PeerAverageResult`/`PeerComparisonResult` agregan los 2 campos nuevos **al final**, ambos con `field(default_factory=dict)`. Grep confirma que las 8 instanciaciones directas de `peers.PeerAverageResult(...)` en `tests/test_valuation.py` (líneas 457, 482, 503, 522, 540, 571, 595, 743) y las 4 instanciaciones de `PeerComparisonResult(...)` dentro de `market_context.py` usan exclusivamente kwargs — ninguna posicional que pudiera desalinearse con los campos nuevos.
- `compare_to_peers` ya es keyword-only (`*` en la firma); los 2 parámetros nuevos son opcionales con default `None → {}`. Las 7 llamadas existentes en `tests/test_market_context.py` no pasan `peers_pe`/`peers_no_usados` — siguen siendo válidas, reciben `{}`.
- `build_market_context_section` no cambia de firma (`peer_comparison: dict`); lee los campos nuevos con `.get(...) or {}`. `_base_peer_comparison()` (definida en `test_summary.py` línea 64 y replicada en `test_query_handler.py`) no tiene esas claves — sigue funcionando idéntico a hoy.
- Ningún test hace `dataclasses.fields(...)` == un set cerrado sobre `PeerAverageResult` o `PeerComparisonResult` (sí existe ese patrón para `VixResult` en `test_market_context.py:198`, pero no para estas dos clases) — confirmado por grep, cero riesgo de rotura por ese lado.
- `peers_note`: grep en `tests/test_summary.py` confirma que ningún test asocia el string default completo (solo hay asserts de contenido puntual en otras secciones) — el cambio de default es seguro, ya lo dice la spec y lo confirmo independientemente.
- `test_fetch_and_analyze_adobe_end_to_end` (único test que ejercita `peer_comparison_dict` de punta a punta) solo hace `assert "Adobe" in text` / `"barata" in text` / `"el boletín" in text` — no hay assert de igualdad de dict ni de conteo de claves que se rompa.

**Nota para el `implementer`:** aunque nada se rompe, `test_get_peer_pe_average_peer_sin_metrics_se_descarta`, `test_get_peer_pe_average_un_solo_peer_valido_minimo_igual_a_maximo` y `test_get_peer_pe_average_earnings_yield_no_positivo_se_descarta` (ya existentes en `tests/test_peers.py`) son candidatos naturales para **extender** (no reemplazar) con asserts adicionales de `peers_pe`/`peers_no_usados`, ya que construyen exactamente los fixtures que la matriz de abajo necesita — evita duplicar fixtures desde cero.

---

### 2. Matriz de tests nuevos

#### `peers.py` — `get_peer_pe_average` / `PeerAverageResult`

| # | Caso | Fixture (earningsYield por peer) | Assert clave |
|---|---|---|---|
| P1 | Feliz: 3/3 válidos | MSFT=1/30, ORCL=1/34, CRM=1/32 | `peers_pe == {"MSFT":30.0,"ORCL":34.0(approx),"CRM":32.0}` (valores exactos vía `pytest.approx`), `peers_no_usados == {}` |
| P2 | Mixto: 1 válido + 1 `sin_dato` (metrics=None) + 1 `earnings_yield_no_positivo` | MSFT=1/30, ORCL=None, CRM=-0.01 | `peers_pe == {"MSFT": approx(30.0)}`, `peers_no_usados == {"ORCL":"sin_dato","CRM":"earnings_yield_no_positivo"}` |
| P3 | `earningsYield` ausente (key no existe en el dict de metrics) | MSFT sin key `earningsYield` | clasificado `"sin_dato"` |
| P4 | `earningsYield` presente pero no numérico (string) | MSFT=`"N/A"` | clasificado `"sin_dato"`, **no** `"earnings_yield_no_positivo"` (test que distingue explícitamente ambos motivos con el mismo peer en 2 sub-casos) |
| P5 | `earningsYield == 0` exacto | MSFT=0 | clasificado `"earnings_yield_no_positivo"` (mismo balde que negativo, sin 3er motivo) |
| P6 | 0/3 válidos, motivo mixto | MSFT=None, ORCL=0, CRM="bad" | `peers_pe == {}`, `peers_no_usados` con los 3, cada uno con su motivo correcto (`sin_dato`/`earnings_yield_no_positivo`/`sin_dato`) |
| P7 | `peers_no_usados` nunca incluye el ticker propio | `own_ticker="ORCL"` en sector Technology | `"ORCL" not in peers_no_usados` y `"ORCL" not in peers_pe` |
| P8 | Regresión numérica | mismos fixtures que P1/P2 | `per_promedio`/`per_minimo`/`per_maximo`/`peers_usados` calculan idéntico a antes de la spec (mismos valores que los tests ya existentes) |
| P9 | Orden preservado | sector con 3 peers, ninguno alfabético con el orden de fallo/éxito | `list(peers_pe.keys())` y `list(peers_no_usados.keys())` respetan el orden de `PEERS_BY_SECTOR[sector]` (excluyendo propio ticker), no orden alfabético ni de inserción azaroso |

#### `market_context.py` — `compare_to_peers` / `PeerComparisonResult`

| # | Caso | Assert clave |
|---|---|---|
| M1 | Propagación en las 4 ramas (parametrizado: `eps_no_positivo`, `sin_peers_validos`, `un_solo_peer_valido`, comparable/`en_linea`) | `result.peers_pe == peers_pe` y `result.peers_no_usados == peers_no_usados` pasados, **idénticos** en las 4 ramas — incluyendo motivo por peer, no solo nombres |
| M2 | Backward-compat: no se pasan `peers_pe`/`peers_no_usados` | `result.peers_pe == {}`, `result.peers_no_usados == {}` |
| M3 | Se pasa `peers_pe=None`/`peers_no_usados=None` explícito (no solo omitido) | coerciona a `{}` igual que M2 (cubre la rama `peers_pe or {}` con `None` explícito, distinto de "parámetro omitido" en términos de qué expresión ejecuta Python aunque el resultado sea el mismo) |
| M4 | Regresión de clasificación: pasar `peers_pe`/`peers_no_usados` poblados en la rama `sin_peers_validos` no cambia `posicion` | `posicion` sigue siendo `"no_comparable"`/`"sin_peers_validos"` — los campos nuevos son puramente informativos, no condicionan la rama |

#### `summary.py` — funciones nuevas aisladas

| # | Función | Caso | Assert clave |
|---|---|---|---|
| S1 | `_join_con_y` | `[]` | `""` |
| S2 | `_join_con_y` | `["A"]` | `"A"` |
| S3 | `_join_con_y` | `["A","B"]` | `"A y B"` |
| S4 | `_join_con_y` | `["A","B","C"]` | `"A, B y C"` |
| S5 | `_agrupar_peers_por_motivo` | `{}` | `{}` |
| S6 | `_agrupar_peers_por_motivo` | `{"MSFT":"sin_dato","CRM":"earnings_yield_no_positivo","ORCL":"sin_dato"}` | `{"sin_dato":["MSFT","ORCL"], "earnings_yield_no_positivo":["CRM"]}` — agrupa preservando el orden de **primera aparición** de cada motivo, no orden alfabético |
| S7 | `_build_peer_pe_breakdown_line` | `({}, {})` | `None` |
| S8 | `_build_peer_pe_breakdown_line` | `({"ORCL":24.3}, {"MSFT":"sin_dato","CRM":"sin_dato"})` | contiene `"PER de tus comparables: ORCL 24.3"`, `"MSFT y CRM"`, `"no devolvieron un dato de FMP esta consulta"` (plural), `"1 / earningsYield"` |
| S9 | `_build_peer_pe_breakdown_line` | `({"ORCL":24.3}, {"MSFT":"sin_dato","CRM":"earnings_yield_no_positivo"})` (motivo mixto) | 2 cláusulas separadas: `"MSFT no devolvió un dato de FMP esta consulta"` (singular) Y `"CRM tiene pérdidas esta consulta"` — **y** assert de orden: la cláusula de `"sin_dato"` aparece antes que la de `"earnings_yield_no_positivo"` en el string resultante (determinístico por orden de primera aparición en el dict de entrada — ver S6) |
| S10 | `_build_peer_pe_breakdown_line` | `({"ORCL":24.3}, {"MSFT":"sin_dato"})` (1 solo fallido) | singular, no plural |
| S11 | `_build_peer_pe_breakdown_line` | `({"ORCL":24.3,"MSFT":22.1,"CRM":20.5}, {})` (feliz, 3/3) | sin cláusula de fallidos, termina en punto simple, **las 3 PER individuales aparecen** (`"ORCL 24.3"`, `"MSFT 22.1"`, `"CRM 20.5"`) |
| S12 | `_build_peer_pe_breakdown_line` | `({}, {"MSFT":"sin_dato","ORCL":"earnings_yield_no_positivo","CRM":"sin_dato"})` (0 válidos, motivo mixto) | sin prefijo `"PER de tus comparables:"`, 2 cláusulas (`"MSFT y CRM"` agrupados plural, `"ORCL"` solo singular) |

#### `summary.py` — `build_market_context_section` (integración de las piezas de arriba)

| # | Caso | Assert clave |
|---|---|---|
| B1 | **`posicion="no_comparable"`, `motivo="un_solo_peer_valido"` — caso NVIDIA, escenario que vio Daniela** | contiene `"Solo 1 comparable con PER válido en tu set de peers"` (no se rompe el test ya existente `test_market_context_section_peer_comparison_un_solo_peer_valido`) **y además** contiene el PER del único peer válido + motivo de los otros 2, cuando `peer_comparison["peers_pe"]`/`["peers_no_usados"]` vienen poblados en el input del test — **este es el caso de aceptación más importante de toda la spec**, es el bug real reportado |
| B2 | `posicion="no_comparable"`, `motivo="eps_no_positivo"`, con `peers_pe`/`peers_no_usados` poblados | breakdown line aparece igual (Gap D no es exclusivo de `un_solo_peer_valido` — las 3 ramas de `no_comparable` estaban ciegas a estos datos, las 3 deben quedar cubiertas) |
| B3 | `posicion="no_comparable"`, `motivo="sin_peers_validos"`, con `peers_no_usados` poblado (los 3 candidatos fallaron) y `peers_pe={}` | breakdown line aparece mostrando solo cláusulas de fallo, sin prefijo `"PER de tus comparables:"` |
| B4 | `posicion="en_linea"` (rama comparable, ya cubierta hoy sin el detalle) | bullet agregado existente sin cambios + nueva línea de detalle a continuación |
| B5 | Feliz completo: 3 peers válidos, `peers_no_usados={}` | breakdown line con los 3 PER individuales, sin cláusula de motivo |
| B6 | `peers_pe={}` y `peers_no_usados={}` juntos (sector no configurado en `PEERS_BY_SECTOR`, ej. `get_peers_for_sector` devolvió `[]`) | **no se agrega ninguna línea de breakdown** — cubre la rama `if peer_breakdown_line:` en `False` |
| B7 | Orden de aparición | con VIX presente y sin VIX: la línea de breakdown aparece siempre después del bullet de peers existente (cualquier rama) y antes del bullet de VIX — assert por índice/posición del substring en el string final, en ambas variantes (con/sin VIX) |

#### `summary.py` — resto de la sección

| # | Caso | Assert clave |
|---|---|---|
| N1 | `peers_note` default nuevo | contiene `"elegida a mano"` o `"manual"` (substring, no string completo) y `"no la arma FMP"` |
| N2 | `_MODELO_FORMULAS["multiplos"]` | contiene `"cálculo del bot"` y `"no un campo directo de FMP"` (o equivalente literal fijado en Decisión #8) |
| N3 | `build_valuation_scenarios_section` no cambia de comportamiento | con `n_peers_validos` variable (0,1,3) y escenarios ya existentes, el texto de la sección de Valor Justo **no** contiene ningún PER individual por peer ni ticker de peer suelto fuera de la fórmula — test de regresión explícito de "Pregunta 1 resuelta" |

#### `query_handler.py`

| # | Caso | Assert clave |
|---|---|---|
| Q1 | `peer_comparison_dict` incluye `peers_pe`/`peers_no_usados` poblados end-to-end | extender el fixture de Adobe (`adobe_fixtures["peers_metrics"]`) para que al menos 1 peer devuelva `earningsYield<=0` y opcionalmente 1 sin dato — verificar en el texto final de `fetch_and_analyze` (o inspeccionando `peer_comparison_dict` si se expone en un test más unitario) que aparecen el motivo y el PER correctos |
| Q2 | `fetch_and_analyze`/`fetch_and_analyze_parts` no cambian de firma pública | `inspect.signature(query_handler.fetch_and_analyze_parts)` (y la wrapper) idéntica a antes — test de regresión explícito, no solo "el test end-to-end sigue pasando" |
| Q3 | Cero llamadas HTTP nuevas | contar requests servidas por `_adobe_router` (wrap del handler con un contador) antes/después — mismo número de llamadas a `/stable/key-metrics` que hoy (1 por peer + 1 propia), ninguna ruta nueva golpeada |

---

### 3. Piso de cobertura

**Aplica el mismo estándar de 100% líneas + 100% ramas ya vigente en el proyecto para `peers.py` y `market_context.py`** — confirmado por evidencia propia (no solo por la afirmación del `architect`): corrí `pytest --cov=investbot --cov-branch` sobre la suite actual antes de esta spec y ambos módulos ya están en 100%/100%. El código nuevo de esta spec (constantes de motivo, el loop reclasificado en `get_peer_pe_average`, los 4 `return` de `compare_to_peers`) es 100% alcanzable sin ramas defensivas irreductibles — toda la matriz de arriba (P1-P9, M1-M4) lo cubre.

Para `summary.py`, **el piso de 100% aplica únicamente al código nuevo/modificado de esta spec** (`_MOTIVO_PEER_LABELS`, `_join_con_y`, `_agrupar_peers_por_motivo`, `_build_peer_pe_breakdown_line`, la inserción nueva en `build_market_context_section`, el texto nuevo de `_MODELO_FORMULAS`/`peers_note`) — cubierto por S1-S12, B1-B7, N1-N2. **No se exige** (ni es objetivo de este run, sería scope creep) llevar a 100% los huecos preexistentes ya identificados por `pytest --cov-branch` hoy y no tocados por esta spec: `summary.py` líneas 129→131 (rama `if formula:` siempre verdadera, las 3 claves de `_MODELOS_ORDEN` tienen fórmula configurada — rama falsa no alcanzable con la config actual), 298-300 (helper `check()` de `build_pillars_section`, no relacionado a peers), 490→492/492→497/502→506/506→512 (combinatoria de `ratios_lines`, no relacionado a peers); tampoco `query_handler.py` líneas 146/160-161/177-178/479. Esos huecos quedan igual que hoy — si el `implementer` los toca de pasada, mejor, pero no es criterio de exit de este run.

**Regla de verificación para Momento 2:** correr `pytest --cov=investbot.peers --cov=investbot.market_context --cov-branch --cov-report=term-missing` y confirmar 100%/100% en ambos; para `summary.py`/`query_handler.py`, confirmar que el % total **no baja** del baseline (96%/97% respectivamente) y que ninguna línea nueva de esta spec aparece en `Missing`.

---

### 4. Gaps de la spec que impiden un test determinístico

**Ninguno bloqueante.** La spec es inusualmente exhaustiva (motivos fijados a 2 categorías, texto y gramática literales, pseudocódigo completo de cada función nueva) — no encontré una decisión de formato/threshold sin resolver que impida escribir un assert exacto. Dos observaciones menores, ninguna bloqueante, ambas derivables del pseudocódigo dado (no requieren volver a preguntarle a Daniela):

1. **Orden de los grupos de motivo cuando se interleavan en `peers_no_usados`** (ej. sin_dato, earnings_yield_no_positivo, sin_dato en ese orden de inserción): el pseudocódigo de `_agrupar_peers_por_motivo` (usa `dict.setdefault` iterando en orden de inserción) implica que el grupo aparece en el texto final en el orden de **primera aparición** del motivo, no en un orden fijo predefinido (ej. no siempre "sin_dato primero"). La spec no lo dice en prosa explícita, pero el código sí lo fija — cubierto por el test S9/S6 con assert de orden explícito para que quede como comportamiento contractual, no implícito.
2. **Branch defensivo de motivo desconocido** en `_MOTIVO_PEER_LABELS.get(motivo, {}).get(forma, "no tiene un PER válido esta consulta")`: no es alcanzable en producción (solo existen 2 constantes de motivo), y `dict.get(k, default)` no es un punto de rama para `coverage.py` (no es un `if`/`for`) — no cuenta contra el piso de 100% ramas. No hace falta un test dedicado para cobertura, aunque es sano agregar uno como documentación de comportamiento futuro-proof (no obligatorio para el exit de QA).

---

### Testabilidad

- [x] Todas las funciones nuevas (`_join_con_y`, `_agrupar_peers_por_motivo`, `_build_peer_pe_breakdown_line`) son funciones puras de módulo, sin estado ni I/O — directamente unit-testeables sin mocks.
- [x] `get_peer_pe_average` ya recibe `get_peer_metrics_fn` inyectado (patrón existente) — el motivo se puebla dentro del mismo loop, sin nueva dependencia externa que mockear.
- [x] `compare_to_peers` sigue siendo función pura sin I/O — los 2 parámetros nuevos son datos, no dependencias.
- [x] Sin lógica crítica en constructores ni en métodos estáticos no testeables — todo son `dataclass` con defaults simples.

### Criterio de exit de QA

- Todos los tests pasan (suite completa verde, no solo los 4 archivos tocados — correr la suite entera por si hay acoplamiento no anticipado con `test_valuation.py`, que instancia `PeerAverageResult` directamente).
- Sin tests ignorados/comentados para pasar CI.
- Flaky rate = 0 en la suite nueva (todas las funciones son puras y determinísticas — no debería haber flakiness posible salvo error de implementación).
- `peers.py`/`market_context.py`: 100% líneas + 100% ramas (mismo estándar que hoy).
- `summary.py`/`query_handler.py`: 0 líneas nuevas de esta spec en `Missing`; % total no regresiona por debajo del baseline documentado arriba.
- Caso B1 (NVIDIA / `un_solo_peer_valido` con datos de peers poblados) verificado explícitamente con evidencia de output — es el criterio de aceptación de mayor prioridad de negocio de todo este run, por ser el bug real que reportó Daniela.
