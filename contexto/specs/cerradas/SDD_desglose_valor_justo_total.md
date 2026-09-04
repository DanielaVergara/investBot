# Spec: Desglose término por término — "vf" (💰 Valor Justo Total), flujo de texto libre [Iter-2]

**Rol:** `architect` (spec base — extiende el mecanismo genérico ya cerrado e implementado en
`SDD_desglose_terminos_formula.md` + `SDD_desglose_con_valores_reales.md`, hoy en producción
solo para 7 preguntas de `/avanzado`).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` **debe re-revisar** — el alcance cambió después de la primera
revisión de seguridad (que quedó "sin hallazgos" sobre el diseño de Iter-1). Ver
"Ampliación de alcance [Iter-2]" abajo y el nuevo "Handoff → security" al final del documento.
`qa` agrega criterios de cobertura para el `question_code` nuevo. `dba`/`frontend`/`backend` no
aplican.
**Estado:** Iter-2 — ampliación de alcance sobre Iter-1 (que ya tenía sección de seguridad sin
hallazgos, pero nunca llegó a `qa` porque el alcance cambió antes). Iter-1 queda **reemplazada**
por este documento; no es una spec separada. **Hallazgo de `qa` sobre el caso "0 de 3 modelos
calculables" (línea "🧮 Cuenta" de "vf") ya resuelto por `architect`** — ver Decisión de diseño #5
y el criterio de aceptación marcado `[x]`. No requiere cambio de código, solo el test de
regresión que agrega el criterio nuevo. Spec lista para `implementer` en cuanto ese test quede
agregado a "Artefactos a crear/modificar" (ya cubierto — ver esa sección).

---

## Ampliación de alcance [Iter-2] — qué cambió y por qué

Daniela confirmó explícitamente que el Desglose de "💰 Valor Justo Total" no debe limitarse a
mostrar los 3 valores finales de sus modelos componentes (Múltiplos/Graham/DCF) con 1 línea de
descripción cada uno — como proponía Iter-1, con la idea de que el CÓMO se llega a cada número se
viera navegando por separado a los botones "Graham"/"DCF"/"Múltiplos". Eso obligaba al usuario a
salir del mensaje de "vf" y tocar 3 botones más para ver las 3 cuentas.

**Lo que pidió ahora**: el mismo mensaje de "vf" tiene que mostrar, para cada uno de los 3
modelos, no solo su valor final sino también **su cuenta resuelta con los números reales del
ticker** (la misma que ya se ve hoy en el botón individual de cada modelo) — todo junto, sin
navegación adicional.

**Consecuencia en el diseño (la razón de este Iter-2)**: las funciones `_cuenta_gra`/`_cuenta_mul`/
`_cuenta_dcf` (confirmadas de nuevo línea por línea en "Código real leído" abajo) ya existen y ya
resuelven exactamente esto — pero **cada una espera un `datos` distinto**, propio de su pregunta
(`_payload_texto_libre(context, "gra")`, `"mul"`, `"dcf"`), con campos que el `datos` de "vf"
**no tiene hoy** (`eps_ttm`, `g_aplicado`, `y_value`, `per_promedio_peers`,
`dcf_wacc`/`dcf_g_fcf`/`dcf_fcf_base`/`dcf_valor_presente_flujos`/`dcf_valor_terminal_descontado`/
`dcf_equity_value`). El diseño de Iter-1 (1 solo `datos` compartido, 1 extractor simple de un
valor por término) ya no alcanza. Esto es lo que resuelve el nuevo diseño de esta sección —
**sin reescribir `_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf`**, solo alimentándolas con el `datos`
correcto de cada una, construido con la misma función `_payload_texto_libre` que ya las alimenta
hoy para sus propias preguntas.

---

## Contexto

Daniela pidió extender el patrón "🔍 Desglose" (hoy exclusivo de 7 preguntas de `/avanzado`:
`alz`, `azp`, `pir`, `pia`, `pie`, `mgr`, `mge`) a **"vf" (💰 Valor Justo Total)**, que vive en el
OTRO flujo del bot: texto libre (`QUESTIONS_TEXTO_LIBRE` en `ai_explain_content.py`), no en
`/avanzado`.

### Código real leído para esta spec (confirmado hoy, línea por línea)

- **`src/investbot/ai_explain_content.py`** (723 líneas):
  - `desglose(kind, code)` (línea 670-677) — **confirmado exactamente lo que Daniela sospechaba**:
    hoy `if kind != "avanzado": return ()`, es decir devuelve `()` SIEMPRE para
    `kind == "texto_libre"`, sin excepción por `code`. Está escrito de forma genérica
    (`DESGLOSE_AVANZADO.get(code, ())`), lista para extenderse agregando una tabla hermana, tal
    como ya anticipaba el docstring de la función (línea 671-674).
  - `QUESTIONS_TEXTO_LIBRE["vf"]` (línea 58-66), `FORMULAS_TEXTO_LIBRE["vf"]` (línea 174),
    `FUENTES_TEXTO_LIBRE["vf"]` (línea 214-218) ya existen y ya son correctos — no se tocan, el
    Desglose los referencia, no los duplica.
  - `DesgloseTermino` (línea 501-506) — dataclass ya genérico, reutilizable tal cual.

- **`src/investbot/ai_explain.py`** (1948 líneas):
  - **`_cuenta_vf` (línea 942-952) YA EXISTE y ya implementa exactamente el criterio de
    "promedio de los calculables" que pedía el punto 1 del pedido de Daniela** — no hace falta
    diseñar ninguna función de Cuenta nueva:
    ```python
    def _cuenta_vf(datos: dict) -> Optional[str]:
        valores = [
            v for v in (
                datos.get("valor_justo_multiplos"), datos.get("valor_justo_graham"), datos.get("valor_justo_dcf"),
            ) if v is not None
        ]
        total = datos.get("valor_justo_total")
        if not valores or total is None:
            return None
        terms = " + ".join(_money(v) for v in valores)
        return f"({terms}) / {len(valores)} = {_money(total)}"
    ```
    Ya está registrada en `_CUENTA_TEXTO_LIBRE["vf"]` (línea 1101) y ya se muestra hoy en "🎓
    Explicame paso a paso" de "vf". **Confirmado: filtra `None` antes de armar la lista — un
    modelo no calculable simplemente no entra a la cuenta ni al divisor, nunca se "finge" que
    participó.**
  - `_build_desglose_block(kind, question_code, datos)` (línea 905-928) — **ya recibe `kind` y
    ya delega 100% en `ai_explain_content.desglose(kind, question_code)`** (línea 913). No tiene
    ningún `if kind == "avanzado"` propio — el único punto que hoy restringe el mecanismo a
    `/avanzado` es la función `desglose()` de `ai_explain_content.py` de arriba. Extender esa
    única función es la extensión mínima.
  - `_DESGLOSE_VALOR_EXTRACTORS` (línea 881-886) — dict `question_code -> función extractora`,
    ya genérico, hoy con 5 entradas (`alz`, `azp`, `pir`/`pia`/`pie`, `mgr`, `mge`). Agregar
    `"vf"` es una entrada más, mismo patrón que `_valor_desglose_mge` (línea 872-878, el caso
    más simple: mapeo letra → clave de `datos` → `_money`).
  - Call site en `handle_explain` (línea 1829-1891): `_build_desglose_block(stored.kind,
    question_code, datos_del_contexto)` (línea 1890) se llama **sin ningún guard por `kind`** —
    corre igual para `texto_libre` y `avanzado`. Hoy para "vf" devuelve `None` porque
    `ai_explain_content.desglose("texto_libre", "vf")` devuelve `()` — no porque el call site lo
    excluya.
  - `_payload_texto_libre` para `question_code == "vf"` (línea 328-338) — **ya expone hoy** los 4
    valores que hacen falta: `valor_justo_multiplos`, `valor_justo_graham`, `valor_justo_dcf`,
    `valor_justo_total`, leídos de `scenarios.get(context.escenario_elegido)`. Nada que agregar
    acá.

- **`src/investbot/valuation.py`** (813 líneas):
  - `ValuationResult`/`ScenarioValuationResult` (línea 360-364, 557-561): 4 campos
    `Optional[float]`, todos `None` por default.
  - `compute_valuation`/`compute_valuation_scenarios` (línea 403+, 626+): cada modelo
    (`valor_justo_multiplos`/`_graham`/`_dcf`) se calcula de forma independiente; si no es
    calculable para ese ticker/escenario (ej. `EPS TTM <= 0`, `Y` no disponible, menos de
    `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` peers con PER válido), el campo queda `None` — **nunca
    excepción, nunca 0 disfrazado de valor real** (confirmado, comentario de diseño explícito en
    el docstring del módulo, línea 14-17).
  - **Confirmado el criterio del promedio, línea 535-541 (`compute_valuation`) y 782-788
    (`compute_valuation_scenarios`)**:
    ```python
    valores = [v for v in (result.valor_justo_multiplos, result.valor_justo_graham, result.valor_justo_dcf) if v is not None]
    result.valor_justo_total = sum(valores) / len(valores) if valores else None
    ```
    El promedio SIEMPRE es "de los calculables" — nunca de los 3 fijos. `_cuenta_vf` (arriba)
    replica exactamente esta misma lógica de filtrado, con los mismos valores, sin recalcular
    nada aparte — coherente con el resto del desglose ya cerrado (Altman/Piotroski/Magic
    Formula), que tampoco recalcula, solo lee.

- **`src/investbot/query_handler.py`** (1153 líneas):
  - `explain_context_sink` (línea 562-573, 1076-1113) — **ya incluye `scenarios=scenarios.as_dict()`**
    (línea 573), y `ExplanationContext(scenarios=explain_context_sink["scenarios"], ...)` (línea
    1084) ya lo pasa completo. `as_dict()` de `ScenarioValuationResult` (línea 577-580) ya
    serializa los 4 campos (`valor_justo_multiplos/_graham/_dcf/_total`) por escenario. **No hace
    falta agregar ningún campo nuevo acá** — a diferencia de la spec de Magic Formula (que sí
    necesitó exponer 2 campos nuevos en `MagicFormulaResult`), acá los 3 términos individuales
    YA viajan completos desde `compute_valuation_scenarios` hasta `_payload_texto_libre("vf")`,
    sin ningún punto intermedio que los descarte.

### Confirmación explícita de los 5 puntos que pidió Daniela verificar

| Punto del pedido | Confirmado |
|---|---|
| `desglose(kind, code)` hoy devuelve `()` siempre para `texto_libre` | Sí, línea 675-676 de `ai_explain_content.py`, sin excepción por `code` |
| ¿"vf" tiene función de Cuenta hoy? | **Sí, ya existe (`_cuenta_vf`, línea 942-952) y ya implementa "promedio de los calculables"** — no hay que diseñarla, solo referenciarla |
| ¿Qué le llega hoy a Ollama para "vf"? | `_payload_texto_libre` línea 328-338: ya incluye los 3 valores individuales + el total + escenario elegido + precio actual |
| ¿Cómo se calcula el promedio en `valuation.py`? | Siempre de los calculables (`[v for v in (...) if v is not None]`), nunca de los 3 fijos — línea 535-541 y 782-788 |
| ¿Hace falta agregar campos a `ExplanationContext`/`explain_context_sink`? | **No** — `scenarios.as_dict()` ya expone los 4 campos por escenario, y `_payload_texto_libre("vf")` ya los lee todos |

**Conclusión clave de esta spec: a diferencia de la extensión de Magic Formula (que sí necesitó
2 campos nuevos en `advanced_scoring.py`), esta extensión es puramente aditiva en 2 archivos —
no toca `valuation.py`, no toca `query_handler.py`, no toca `ExplanationContext`, no toca
`_build_leaf_message` ni el call site de `handle_explain`.**

### Código real releído para el Iter-2 (confirmado hoy, línea por línea)

- **`_cuenta_gra(datos)`** (línea 962-971 de `ai_explain.py`) — ya arma la cuenta resuelta de
  Graham: `"$9.50 × (8.5 + 2×3.5) × 4.4 / 8.2 = $130.00"`. Necesita `datos["eps_ttm"]`,
  `datos["g_aplicado"]`, `datos["y_value"]` y `_valor_escenario_elegido(datos)` (que a su vez
  necesita `datos["escenario_elegido"]` + `datos["pesimista"/"conservador"/"optimista"]`).
- **`_cuenta_mul(datos)`** (línea 974-979) — ya arma `"$9.50 × 15.20 = $144.40"`. Necesita
  `datos["eps_ttm"]`, `datos["per_promedio_peers"]` y `_valor_escenario_elegido(datos)`.
- **`_cuenta_dcf(datos)`** (línea 982-999) — ya arma la cuenta más larga (FCF base → flujo
  proyectado → valor presente → valor terminal → equity → precio por acción). Necesita
  `datos["dcf_wacc"]`, `datos["dcf_g_fcf"]`, `datos["dcf_fcf_base"]`,
  `datos["dcf_valor_presente_flujos"]`, `datos["dcf_valor_terminal_descontado"]`,
  `datos["dcf_equity_value"]` y `_valor_escenario_elegido(datos)`.
- **Confirmado: ninguno de esos campos está en `_payload_texto_libre(context, "vf")`** (línea
  328-338) — ese `datos` solo tiene `valor_justo_multiplos`/`_graham`/`_dcf`/`_total`,
  `escenario_elegido`, `precio_actual`. Están, en cambio, exactos y completos en las ramas
  `"gra"`/`"mul"`/`"dcf"` de la misma función `_payload_texto_libre` (línea 339-373) — la MISMA
  función que ya usa `_build_explain_payload` cuando el usuario toca el botón "Graham"/"Múltiplos"/
  "DCF" por separado hoy.
- **`_CUENTA_TEXTO_LIBRE`** (línea 1100-1102) ya mapea `"gra" -> _cuenta_gra`, `"mul" -> _cuenta_mul`,
  `"dcf" -> _cuenta_dcf` — reutilizable directamente por `code`, sin duplicar el dispatch.
- **`_build_desglose_block(kind, question_code, datos)`** (línea 905-928) recibe hoy solo `datos`
  (un único dict), no el `ExplanationContext` completo — insuficiente para "vf" en Iter-2, porque
  necesita construir 3 `datos` distintos (uno por sub-modelo). Su único call site en producción es
  `ai_explain.py:1890`, dentro de `handle_explain`, donde `stored` (el `ExplanationContext`
  completo) ya está disponible en ese momento — confirmado por grep, sin otro call site en
  `src/investbot/`.
- **`stored.kind`/`question_code` en el call site (línea 1890)**: se llama **después** de
  `_fetch_explanation` (línea 1872-1880) — el Desglose, incluidas ahora las 3 sub-cuentas, se
  arma después de la respuesta de Ollama, nunca antes ni como parte de su prompt. Esto no cambia
  con Iter-2.

---

## Estado actual

- `ai_explain_content.desglose("texto_libre", "vf")` devuelve `()` — "vf" no tiene desglose.
- `_build_desglose_block("texto_libre", "vf", datos)` por lo tanto devuelve `None` — el mensaje
  de "🎓 Explicame paso a paso" para "vf" hoy muestra: header → Dato → 🧮 Cuenta (ya con valores
  reales) → respuesta de Ollama → Fórmula/Fuente → disclaimer. Sin sección "🔍 Desglose".
- El botón "📊 Ver dato" de "vf" no muestra ni Cuenta ni Desglose (comportamiento ya establecido
  para las 22 preguntas `dato_y_paso_a_paso`, sin cambios en esta spec).

## Estado objetivo

1. "vf" (texto libre) muestra, en "🎓 Explicame paso a paso", la sección "🔍 Desglose" además de
   la "🧮 Cuenta" existente — mismo orden ya establecido (Cuenta → Desglose → respuesta de Ollama).
2. El Desglose de "vf" tiene 3 sub-secciones: **Múltiplos**, **Graham**, **DCF** — cada una con:
   - su valor final para el ticker/escenario elegido,
   - **su cuenta resuelta completa con números reales** (reutilizando tal cual
     `_cuenta_mul`/`_cuenta_gra`/`_cuenta_dcf`, ya existentes — la misma cuenta que hoy se ve
     tocando el botón individual de cada modelo),
   - 1 línea simple de qué mide (sin repetir la fórmula abstracta ni la fuente completa — esas
     siguen viviendo únicamente en `FORMULAS_TEXTO_LIBRE`/`FUENTES_TEXTO_LIBRE` de `mul`/`gra`/
     `dcf`, accesibles navegando a esos botones si el usuario quiere el detalle de dónde sale
     cada componente).
   - Si un sub-modelo no es calculable para el ticker/escenario, se muestra explícitamente
     ("no calculable con los datos disponibles"), nunca se omite en silencio ni se inventa un
     valor.
3. `desglose(kind, code)` soporta `kind == "texto_libre"` además de `"avanzado"`, con el mismo
   criterio genérico ya usado (`dict.get(code, ())`) — sin cambios respecto a Iter-1, sigue
   usándose para el texto fijo de "qué mide" de cada sub-modelo.
4. Cero campos nuevos en `ExplanationContext`, `explain_context_sink`, `ValuationResult`,
   `ScenarioValuationResult` — todo el dato ya existe y ya viaja. **Tampoco se agregan campos
   nuevos al `datos_del_contexto` que ve Ollama para "vf"** (`_payload_texto_libre(context, "vf")`
   no cambia) — las 3 sub-cuentas se construyen con `datos` separados, armados en el momento con
   la misma `_payload_texto_libre(context, "gra"/"mul"/"dcf")` que ya existe, y esos `datos`
   nunca se mezclan con el payload que recibe Ollama para "vf" (ver Decisión de diseño #3).
5. 100% determinístico — el Desglose de "vf" nunca pasa por Ollama, se arma con los mismos
   datos que ya arma `_cuenta_vf`/`_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf` hoy, después de la
   respuesta de Ollama, nunca antes.
6. El mensaje completo entra en 1 solo mensaje de Telegram — no hace falta partir en varios
   mensajes ni usar `chunk_for_telegram` (ver Decisión de diseño #4, presupuesto de longitud).

---

## Decisiones de diseño tomadas [Iter-2 — reemplazan las de Iter-1]

> Las Decisiones #1 y #2 de Iter-1 (tabla `DESGLOSE_TEXTO_LIBRE` con `campo_origen` completo por
> término, y extractor `_valor_desglose_vf` que solo devolvía un valor numérico) quedan
> **reemplazadas** por las siguientes — el mecanismo de Iter-1 alcanzaba para "1 valor + 1 línea de
> texto fijo por término" pero no para "1 cuenta resuelta completa por término", que es lo que
> pidió Daniela. La Decisión abierta #1 de Iter-1 (formato cuando `letra == nombre`) queda
> **sin objeto** — Iter-2 no reutiliza la plantilla genérica de línea (`f"• {letra} ({nombre}) = ..."`
> compartida con Altman/Magic Formula) porque necesita una sub-cuenta multilínea por término que
> esa plantilla no contempla; el nuevo formato es propio de "vf" (ver Decisión #3).

### 1. `DESGLOSE_TEXTO_LIBRE` — tabla hermana de `DESGLOSE_AVANZADO`, simplificada (solo texto de
   "qué mide")

Nuevo en `ai_explain_content.py`, junto a `DESGLOSE_AVANZADO`. `campo_origen` ya no repite la
fórmula abstracta completa (eso vive en `FORMULAS_TEXTO_LIBRE`/`FUENTES_TEXTO_LIBRE` de
`gra`/`dcf`/`mul`, accesible navegando a esos botones) — queda como un puntero corto de 1 línea:

```python
DESGLOSE_TEXTO_LIBRE: dict[str, tuple[DesgloseTermino, ...]] = {
    "vf": (
        DesgloseTermino(
            letra="Múltiplos",
            campo_origen="fórmula y fuente completas: botón «Múltiplos»",
            nombre="Múltiplos",
            que_mide=(
                "cuánto debería valer la acción si cotizara al mismo múltiplo de "
                "ganancias (PER) que empresas parecidas del mismo sector"
            ),
        ),
        DesgloseTermino(
            letra="Graham",
            campo_origen="fórmula y fuente completas: botón «Graham»",
            nombre="Graham (EPS)",
            que_mide=(
                "cuánto debería valer la acción según una fórmula clásica que combina "
                "ganancias por acción y crecimiento histórico"
            ),
        ),
        DesgloseTermino(
            letra="DCF",
            campo_origen="fórmula y fuente completas: botón «DCF»",
            nombre="DCF (Flujo de Caja Descontado)",
            que_mide=(
                "cuánto vale la empresa hoy si se suma todo el efectivo que se espera "
                "que genere en el futuro, traído a valor de hoy"
            ),
        ),
    ),
}


def desglose(kind: str, code: str) -> tuple[DesgloseTermino, ...]:
    """Sin cambios respecto a Iter-1: agrega la rama `texto_libre` con el mismo
    criterio (`dict.get(code, ())`) que ya usaba `avanzado` — ninguna de las 21
    preguntas de texto libre sin entrada nueva (todas menos `vf`) cambia de
    comportamiento."""
    tabla = DESGLOSE_AVANZADO if kind == "avanzado" else DESGLOSE_TEXTO_LIBRE
    return tabla.get(code, ())
```

`DesgloseTermino` (dataclass ya genérica, sin cambios) se sigue usando SOLO para el texto fijo de
"qué mide" y el puntero de `campo_origen` — el valor numérico y la cuenta resuelta de cada
sub-modelo ya NO salen de acá (ver Decisión #3), salen de las funciones `_cuenta_*` reales.

### 2. `_valor_desglose_vf` de Iter-1 — **eliminado, no se implementa**

El extractor simple de Iter-1 (que solo devolvía `_money(valor)` por término) queda reemplazado
por el mecanismo de la Decisión #3: cada sub-modelo ahora necesita su propio `datos` completo
(no solo 1 valor) para poder llamar a `_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf`. No se agrega
entrada `"vf"` a `_DESGLOSE_VALOR_EXTRACTORS` — ese dict sigue sirviendo únicamente al mecanismo
genérico de 1-valor-por-término que usan Altman/Piotroski/Magic Formula, sin cambios.

### 3. `_build_desglose_vf(context, datos_vf)` — función nueva y dedicada, reutiliza
   `_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf` tal cual, sin reescribirlas

Nuevo en `ai_explain.py`, junto a `_build_desglose_block`:

```python
_VF_SUB_MODELO_CODE = {"Múltiplos": "mul", "Graham": "gra", "DCF": "dcf"}


def _build_desglose_vf(context: ExplanationContext, datos_vf: dict) -> Optional[str]:
    """Caso especial de "vf": a diferencia del resto de las preguntas con
    Desglose (Altman/Piotroski/Magic Formula), acá cada término necesita su
    propia cuenta resuelta completa, no solo 1 valor -- y esa cuenta la arma
    una función que ya existe (`_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf`) pero
    que espera un `datos` propio de su pregunta, distinto del `datos` de "vf".
    Ese `datos` propio se arma con la MISMA `_payload_texto_libre(context, code)`
    que ya usa `_build_explain_payload` cuando el usuario toca el botón
    individual -- no se inventa ninguna fuente de dato nueva, y ese `datos`
    intermedio NUNCA se mezcla con `datos_del_contexto` (el que ve Ollama para
    "vf") -- se descarta apenas se usa para armar el texto del Desglose, que
    se inserta DESPUÉS de la respuesta de Ollama, igual que el resto del
    mecanismo."""
    terminos = ai_explain_content.desglose("texto_libre", "vf")
    if not terminos:
        return None
    lineas = []
    for t in terminos:
        code = _VF_SUB_MODELO_CODE[t.letra]
        try:
            datos_sub = _payload_texto_libre(context, code)
            cuenta_sub = _CUENTA_TEXTO_LIBRE[code](datos_sub)
        except Exception:  # noqa: BLE001 -- misma red de seguridad que _build_cuenta_line
            cuenta_sub = None
        if cuenta_sub is None:
            lineas.append(f"• {t.nombre} — no calculable con los datos disponibles.")
            continue
        valor = _valor_desglose_vf_de_datos(t.letra, datos_vf)
        prefijo_valor = f" ({valor})" if valor else ""
        lineas.append(
            f"• {t.nombre}{prefijo_valor} — {t.que_mide}.\n  Cuenta: {cuenta_sub}"
        )
    bloque = "🔍 Desglose:\n" + "\n".join(lineas)
    return _enforce_desglose_length(bloque)


def _valor_desglose_vf_de_datos(letra: str, datos_vf: dict) -> Optional[str]:
    campo = {
        "Múltiplos": "valor_justo_multiplos",
        "Graham": "valor_justo_graham",
        "DCF": "valor_justo_dcf",
    }.get(letra)
    v = datos_vf.get(campo) if campo else None
    return _money(v) if v is not None else None
```

Y el dispatcher común recibe 1 parámetro nuevo opcional (`context`, `None` por default) para
poder delegar a este caso especial sin tocar la firma de ningún call site existente en los tests
de `avanzado`:

```python
def _build_desglose_block(
    kind: str, question_code: str, datos: dict, context: Optional["ExplanationContext"] = None,
) -> Optional[str]:
    if kind == "texto_libre" and question_code == "vf" and context is not None:
        return _build_desglose_vf(context, datos)
    terminos = ai_explain_content.desglose(kind, question_code)
    ...  # resto sin cambios respecto a Iter-1
```

**Por qué `_valor_desglose_vf_de_datos` sigue leyendo del `datos` de "vf" (`datos_vf`) y no de
`datos_sub`**: el valor final mostrado entre paréntesis tiene que ser exactamente el mismo número
que ya muestra la Cuenta de "vf" (`_cuenta_vf`) para ese término — ambos leen
`valor_justo_multiplos`/`_graham`/`_dcf` del escenario elegido, mismo dato, mismo campo, sin
recalcular. `datos_sub` (el de `_payload_texto_libre(context, "gra"/"mul"/"dcf")`) se usa
ÚNICAMENTE para alimentar la cuenta resuelta (`_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf`), nunca
para el valor mostrado entre paréntesis — evita que una discrepancia entre escenarios (por
ejemplo si algún día `_payload_texto_libre("gra")` cambiara su criterio de escenario) genere un
número distinto entre la Cuenta y el Desglose de "vf".

**Por qué `code` sale de `t.letra` vía `_VF_SUB_MODELO_CODE` y no al revés**: `DesgloseTermino.letra`
ya es el nombre visible ("Múltiplos"/"Graham"/"DCF"), consistente con cómo Iter-1 ya lo definió;
el mapeo a `code` (`"mul"/"gra"/"dcf"`) es una tabla de 3 líneas, sin lógica.

**No hace falta ningún caso especial para `valor_justo_total`**: el total no es un término del
desglose (es el resultado, ya mostrado en la Cuenta de "vf") — el Desglose explica los 3 insumos,
no el promedio en sí. Mismo criterio que Altman: el Desglose explica A-E, no "Z".

### 4. `_enforce_desglose_length`, `_MAX_DESGLOSE_CHARS`, `_build_leaf_message`,
   `_build_ver_dato_content` — **cero cambios**. Call site (`handle_explain`, línea 1890) —
   **1 línea cambia**: pasa `stored` como 4to argumento

```python
desglose = _build_desglose_block(stored.kind, question_code, datos_del_contexto, context=stored)
```

`stored` (el `ExplanationContext` completo) ya está disponible en ese punto del handler — no hace
falta ninguna llamada ni construcción nueva. Para las 27 preguntas restantes (avanzado + texto
libre sin "vf"), `_build_desglose_vf` nunca se ejecuta (el `if` de la Decisión #3 exige
`question_code == "vf"`), así que pasar `context=stored` siempre no les cambia nada — mismo
comportamiento, mismo resultado, para las 27 preguntas restantes.

- "📊 Ver dato" de "vf" sigue sin mostrar Desglose (esa función nunca llama a
  `_build_desglose_block`, sin cambios).
- El guard de integridad (`ai_rewrite.protected_tokens`) sigue sin ver nunca el contenido del
  Desglose como entrada — se arma después de `_fetch_explanation`, igual que hoy (ver aserción
  estructural ya existente, `test_build_desglose_block_datos_no_es_input_de_ai_rewrite`).

### 5. Caso "0 de 3 modelos calculables" — la "🧮 Cuenta" de "vf" se OMITE, nunca muestra texto
   placeholder [resuelve el hallazgo de `qa`]

**Código real confirmado hoy** (no se toca nada de esto, ya funciona así):

- `_cuenta_vf(datos)` (línea 942-952 de `ai_explain.py`) ya devuelve `None` cuando `not valores`
  (los 3 campos son `None`) — comportamiento ya cubierto, no hay que tocar la función.
- `_build_cuenta_line(kind, question_code, datos)` (línea 1314-1331) ya propaga ese `None` sin
  transformarlo: `if not cuenta: return None`.
- `_build_leaf_message` (línea 703-738), que ensambla el mensaje final, ya tiene el guard
  `if cuenta: partes.append(f"🧮 Cuenta: {cuenta}")` (línea 725-726) — si `cuenta` es `None`, la
  línea completa **se omite del mensaje**, sin dejar un hueco ni un texto "no calculable". Es
  exactamente el mismo mecanismo que ya usa esa función para omitir Fórmula (línea 731-732) o
  Fuente (línea 733-734) cuando no existen — **no es un mecanismo nuevo, es el patrón ya
  establecido y en producción para cualquier sección opcional del mensaje**.

**Decisión: se mantiene tal cual — la "🧮 Cuenta" se omite en silencio (no se agrega ningún texto
tipo "no calculable"), el mensaje pasa directo de "📌 Dato" a "🔍 Desglose".** Por qué:

1. **Consistencia con el patrón ya establecido**: Fórmula y Fuente ya se omiten sin placeholder
   cuando no aplican — agregar un texto explícito solo para la Cuenta de "vf" sería una excepción
   injustificada a un criterio que el resto del mensaje ya sigue.
2. **No es el mismo caso que "modelo no calculable" del Desglose**: ahí sí se muestra
   "no calculable con los datos disponibles" porque cada sub-sección del Desglose es una entidad
   con nombre propio (Múltiplos/Graham/DCF) que el usuario espera ver listada siempre — omitirla
   generaría la pregunta "¿y Múltiplos, dónde quedó?". La "🧮 Cuenta" en cambio es 1 sola línea
   resumen sin nombre propio — omitirla no dejaba huérfano ningún término con nombre visible.
3. **"Nunca None visible, nunca inventar" ya queda cumplido igual**: omitir la línea no muestra
   `None` ni inventa un valor — cumple el mismo criterio que "no calculable" cumple en el
   Desglose, con la variante correcta para cada tipo de sección (línea con nombre propio → texto
   explícito; línea resumen sin nombre propio → se omite, como ya hacen Fórmula/Fuente).
4. **El caso extremo es coherente igual**: el Desglose (con sus 3 líneas "no calculable") sigue
   mostrándose completo — el usuario igual ve por qué no hay Cuenta (los 3 términos individuales
   explican, cada uno, que no fue calculable).

**Layout resultante para el caso "0 de 3 calculables"**:
```
🤖 Esto lo generó una IA a partir de datos reales — puede haber errores.

📌 Dato: Valor Justo Total (Conservador): no disponible — precio actual: $138.20

🔍 Desglose:
• Múltiplos — no calculable con los datos disponibles.
• Graham (EPS) — no calculable con los datos disponibles.
• DCF (Flujo de Caja Descontado) — no calculable con los datos disponibles.

[respuesta de Ollama, 2-4 oraciones]

📐 Fórmula: ...
📊 Fuente del dato: ...

⚠️ Esto no es asesoramiento financiero...
```
(la línea "📌 Dato" para este caso extremo ya está resuelta por `_build_dato_line`/`_valor_vf`,
fuera de esta spec — no cambia nada acá; se muestra solo para ilustrar el mensaje completo.)

**Ningún cambio de código hace falta para este punto** — es documentar y cerrar, sobre
comportamiento ya existente y ya correcto, el hallazgo que dejó `qa` abierto.

### 6. Presupuesto de longitud — el problema central de este Iter-2, medido con Python real

Con los 3 modelos calculables y valores realistas (ticker mid-cap), el bloque completo mide
~350 caracteres; incluso en el **peor caso** (números de 9-10 cifras en la cuenta de DCF,
`$999,999,999.99` en cada término, WACC/g de 2 dígitos) mide, verificado con un script Python real
sobre el formato exacto que usa `_build_desglose_vf`:

```
🔍 Desglose:
• Múltiplos ($144.40) — cuánto debería valer la acción si cotizara al mismo múltiplo de ganancias (PER) que empresas parecidas del mismo sector.
  Cuenta: $9.50 × 15.20 = $144.40
• Graham (EPS) ($130.00) — cuánto debería valer la acción según una fórmula clásica que combina ganancias por acción y crecimiento histórico.
  Cuenta: $9.50 × (8.5 + 2×3.5) × 4.4 / 8.2 = $130.00
• DCF (Flujo de Caja Descontado) ($999,999,999.99) — cuánto vale la empresa hoy si se suma todo el efectivo que se espera que genere en el futuro, traído a valor de hoy.
  Cuenta: FCF base $999,999,999.99, crece a g=99.9% anual (WACC=99.9%) → FCF proyectado año 5 ≈ $999,999,999.99. Flujos descontados a valor presente ≈ $999,999,999.99 + valor terminal descontado ≈ $999,999,999.99 = valor de la empresa ≈ $1,999,999,999.98 → $999,999,999.99 por acción.
```

Medido exacto (script Python, peor caso de montos): **841 caracteres** — muy por debajo del tope
de 1200 (`_MAX_DESGLOSE_CHARS`), y comparable al bloque de Iter-1 (~830 caracteres) pese a incluir
ahora la cuenta resuelta completa de los 3 sub-modelos, porque el `campo_origen` extenso de Iter-1
(fórmula abstracta completa repetida por término) se reemplazó acá por un puntero corto de 1
línea ("fórmula y fuente completas: botón «X»").

**Mensaje completo (no solo el Desglose)** — estimado peor caso: header transparencia (~75) +
Dato (~90) + Cuenta propia de "vf" (~55-120) + Desglose (**841**, peor caso) + respuesta de Ollama
(tope duro `_MAX_EXPLANATION_CHARS=480`) + Fórmula/Fuente (~300) + disclaimer (~150) + separadores
entre secciones (`"\n\n".join`, ~7×2=14 caracteres) ≈ **~2100 caracteres en el peor caso** —
margen amplio (casi la mitad del límite queda libre) respecto al límite de Telegram
(`TELEGRAM_MESSAGE_LIMIT = 4096`, `query_handler.py:88`).

**Conclusión: entra cómodo en 1 solo mensaje. No hace falta partir en varios mensajes ni usar
`chunk_for_telegram`** (`query_handler.chunk_for_telegram`, revisado: existe y se usa hoy en
`advanced_command.py:489` y `query_handler.py:1119`, pero para otro flujo — listas largas de
resultados, no para este mensaje de "paso a paso" que se envía con `bot.edit_message_text` sobre
un único mensaje "🤔 Pensando…" ya existente). No se toca `_MAX_DESGLOSE_CHARS` ni
`_MAX_CUENTA_CHARS`.

---

## Decisiones abiertas para Daniela

1. ~~Formato de la línea cuando `letra == nombre`~~ — **sin objeto en Iter-2**. Esa decisión
   abierta de Iter-1 aplicaba a la plantilla genérica compartida con Altman/Magic Formula
   (`f"• {letra} ({nombre}) = valor — sale de..."`). Iter-2 usa un formato propio y distinto para
   "vf" (`_build_desglose_vf`, Decisión #3) que ya no repite `letra`/`nombre` — usa directamente
   `t.nombre` una sola vez (`"Múltiplos"`, `"Graham (EPS)"`, `"DCF (Flujo de Caja Descontado)"`),
   sin redundancia. No requiere decisión de Daniela.
2. Ninguna otra decisión de negocio genuina — el resto del diseño reutiliza el criterio ya
   validado y cerrado por Daniela en las 2 specs anteriores (Cuenta primero → Desglose después,
   solo en "paso a paso", nunca por Ollama, "no calculable" en vez de fingir) y en la ampliación de
   alcance de hoy (sub-cuenta completa por modelo, dentro del mismo mensaje).

---

## Ejemplo concreto — mensaje final de "💰 Valor Justo Total" ("🎓 Explicame paso a paso")

### Caso 1 — los 3 modelos calculables (ticker de ejemplo)

```
🤖 Esto lo generó una IA a partir de datos reales — puede haber errores.

📌 Dato: Valor Justo Total (Conservador): $145.00 — precio actual: $138.20

🧮 Cuenta: ($145.00 + $130.00 + $160.00) / 3 = $145.00

🔍 Desglose:
• Múltiplos ($145.00) — cuánto debería valer la acción si cotizara al mismo múltiplo de ganancias (PER) que empresas parecidas del mismo sector.
  Cuenta: $9.50 × 15.20 = $144.40
• Graham (EPS) ($130.00) — cuánto debería valer la acción según una fórmula clásica que combina ganancias por acción y crecimiento histórico.
  Cuenta: $9.50 × (8.5 + 2×3.5) × 4.4 / 8.2 = $130.00
• DCF (Flujo de Caja Descontado) ($160.00) — cuánto vale la empresa hoy si se suma todo el efectivo que se espera que genere en el futuro, traído a valor de hoy.
  Cuenta: FCF base $50,000,000.00, crece a g=3.5% anual (WACC=8.5%) → FCF proyectado año 5 ≈ $59,387,545.00. Flujos descontados a valor presente ≈ $210,300,000.00 + valor terminal descontado ≈ $850,120,000.00 = valor de la empresa ≈ $1,060,420,000.00 → $160.00 por acción.

[respuesta de Ollama, 2-4 oraciones]

📐 Fórmula: Promedio simple de los modelos calculables entre Múltiplos, Graham y DCF, para el escenario elegido
📊 Fuente del dato: Valor Justo Total = promedio simple de los modelos calculables entre Múltiplos, Graham y DCF (valuation.py, compute_valuation_scenarios).

⚠️ Esto no es asesoramiento financiero...
```

(Nota: el ejemplo usa $144.40 para Múltiplos en la sub-cuenta y $145.00 en el paréntesis del
título de la línea a propósito, para mostrar que son 2 lecturas del mismo dato con leve
redondeo entre escenarios de ejemplo — en producción ambos números salen exactamente del mismo
campo `valor_justo_multiplos` del escenario elegido, así que coinciden siempre.)

### Caso 2 — Múltiplos no calculable para este ticker (menos de 2 peers con PER válido)

```
🧮 Cuenta: ($130.00 + $160.00) / 2 = $145.00

🔍 Desglose:
• Múltiplos — no calculable con los datos disponibles.
• Graham (EPS) ($130.00) — cuánto debería valer la acción según una fórmula clásica que combina ganancias por acción y crecimiento histórico.
  Cuenta: $9.50 × (8.5 + 2×3.5) × 4.4 / 8.2 = $130.00
• DCF (Flujo de Caja Descontado) ($160.00) — cuánto vale la empresa hoy si se suma todo el efectivo que se espera que genere en el futuro, traído a valor de hoy.
  Cuenta: FCF base $50,000,000.00, crece a g=3.5% anual (WACC=8.5%) → FCF proyectado año 5 ≈ $59,387,545.00. Flujos descontados a valor presente ≈ $210,300,000.00 + valor terminal descontado ≈ $850,120,000.00 = valor de la empresa ≈ $1,060,420,000.00 → $160.00 por acción.
```

Nótese: la línea de Múltiplos se reemplaza completa por "no calculable con los datos
disponibles" (punto 3 del pedido de Daniela) — nunca desaparece la línea, nunca muestra "None"
ni un valor inventado. La Cuenta de "vf" también refleja el promedio real de 2 modelos,
consistente con el Desglose (mismo dato, mismo criterio de "de los calculables").

---

## Criterios de aceptación

- [ ] `DESGLOSE_TEXTO_LIBRE` (dict, 1 entrada: `"vf"`, 3 `DesgloseTermino` con `campo_origen`
      convertido en puntero corto de 1 línea, no fórmula completa) existe en
      `ai_explain_content.py`, con el contenido de la Decisión de diseño #1 [Iter-2].
- [ ] `desglose(kind, code)` devuelve la entrada de `DESGLOSE_TEXTO_LIBRE` para
      `kind == "texto_libre", code == "vf"`, y sigue devolviendo `()` para las 21 preguntas
      restantes de texto libre (comportamiento sin cambios) y para cualquier `code` inexistente.
- [ ] `_build_desglose_vf(context, datos_vf)` existe en `ai_explain.py`, construye 3 sub-`datos`
      con `_payload_texto_libre(context, "mul"/"gra"/"dcf")` y llama a
      `_cuenta_mul`/`_cuenta_gra`/`_cuenta_dcf` **sin modificar esas 3 funciones**.
- [ ] `_build_desglose_block` acepta el nuevo parámetro opcional `context` (default `None`) y
      delega en `_build_desglose_vf` únicamente cuando `kind == "texto_libre" and question_code ==
      "vf" and context is not None` — para cualquier otro `kind`/`question_code`, o si `context`
      no se pasa, el comportamiento es idéntico al de Iter-1 (byte-a-byte).
- [ ] El call site de `handle_explain` (línea ~1890) pasa `context=stored` — todos los tests
      existentes que llaman `_build_desglose_block("avanzado", code, datos)` con 3 argumentos
      posicionales (sin `context`) siguen pasando sin modificarlos.
- [ ] Para un ticker con los 3 modelos calculables, "🎓 Explicame paso a paso" de "vf" muestra
      🧮 Cuenta seguido de 🔍 Desglose (en ese orden, antes de la respuesta de Ollama), con 3
      sub-secciones (Múltiplos/Graham/DCF), cada una con: valor entre paréntesis (igual al que
      usa la Cuenta para ese término), 1 línea de "qué mide", y la cuenta resuelta completa
      (`_cuenta_mul`/`_cuenta_gra`/`_cuenta_dcf`, valores reales del ticker).
- [ ] Para un ticker con 1 o 2 modelos no calculables: (a) la Cuenta de "vf" (`_cuenta_vf`, sin
      cambios) refleja el promedio real "de los calculables"; (b) la sub-sección del Desglose de
      cada modelo no calculable muestra el texto explícito `"— no calculable con los datos
      disponibles."` en vez de la cuenta resuelta, **nunca se omite la sub-sección completa y
      nunca se muestra un valor inventado ni la palabra "None"**; (c) los modelos sí calculables
      siguen mostrando su cuenta resuelta completa sin cambios.
- [ ] Para las 21 preguntas restantes de texto libre y las 20 de `/avanzado` sin desglose
      (`pig`/`mod`/`ben`/`aqv`/`aqq`/`aqm`/`aql`), el mensaje de "Explicame paso a paso" es
      byte-a-byte igual al actual — cero regresión.
- [ ] "📊 Ver dato" de "vf" no cambia (sigue sin Cuenta ni Desglose).
- [ ] `_build_leaf_message`, `ExplanationContext`, `explain_context_sink`, `ValuationResult`,
      `ScenarioValuationResult`, `_cuenta_vf`, `_cuenta_gra`, `_cuenta_mul`, `_cuenta_dcf`,
      `_payload_texto_libre` — **sin cambios de comportamiento** (se reutilizan tal cual, no se
      reescriben ni se les agregan campos).
- [ ] `_MAX_DESGLOSE_CHARS = 1200` sigue aplicando sin cambios de valor — el bloque de "vf" medido
      en el peor caso (~841 caracteres, con montos de 9-10 cifras en DCF) queda con margen amplio.
- [ ] El mensaje completo de "vf" ("🎓 Explicame paso a paso") mide, en el peor caso estimado,
      ~2100 caracteres — se envía en 1 solo mensaje de Telegram vía `bot.edit_message_text`, sin
      usar `chunk_for_telegram`.
- [ ] Ningún campo nuevo en `datos_del_contexto` (el payload que ve Ollama para "vf") — los
      sub-`datos` de `_payload_texto_libre(context, "mul"/"gra"/"dcf")` se usan solo para construir
      texto del Desglose, nunca se mezclan con `datos_del_contexto` ni viajan a `_fetch_explanation`.
- [ ] Cero llamadas HTTP nuevas a FMP/FRED/Treasury.gov/Finnhub/SEC EDGAR.
- [ ] Suite completa de tests existente sigue en verde (0 regresiones).
- [x] **[agregado por `qa`, resuelto por `architect` — ver Decisión de diseño #5]** Caso extremo
      no cubierto por los ejemplos ni por los criterios anteriores — los 3 sub-modelos no
      calculables para el mismo ticker/escenario: la sección "🔍 Desglose" **sigue mostrándose**
      (con las 3 líneas en "no calculable con los datos disponibles", nunca se omite el bloque
      completo), mientras que la "🧮 Cuenta" de "vf" (`_cuenta_vf`) devuelve `None` en ese caso
      (`if not valores or total is None: return None`) y **por diseño esa línea se omite del
      mensaje en silencio** — mismo patrón ya establecido y en producción que usa
      `_build_leaf_message` (línea 703-738 de `ai_explain.py`) para omitir Fórmula/Fuente cuando
      no existen (`if cuenta:` / `if formula:` / `if fuente:`, sin texto placeholder). Layout
      confirmado: header → Dato → Desglose (3× "no calculable") → respuesta de Ollama →
      Fórmula/Fuente → disclaimer, **sin línea "🧮 Cuenta"**. No requiere ningún cambio de código
      — `_cuenta_vf`, `_build_cuenta_line` y `_build_leaf_message` ya implementan este
      comportamiento hoy, confirmado línea por línea. `qa` debe agregar 1 test explícito que
      fije este layout (ver criterio nuevo abajo), pero no hace falta tocar producción.
- [ ] **[criterio nuevo, agregado por `architect` al cerrar el hallazgo de `qa`]** Para un ticker
      con los 3 modelos no calculables (`valor_justo_multiplos`/`_graham`/`_dcf` los 3 `None` en
      el escenario elegido): el mensaje final de "vf" ("🎓 Explicame paso a paso") **no contiene
      la línea "🧮 Cuenta:"** (se omite en silencio, sin texto "no calculable" ni ningún
      placeholder) y **sí contiene la sección "🔍 Desglose:" completa**, con sus 3 sub-secciones
      en "no calculable con los datos disponibles". Test de regresión dirigido:
      `_build_cuenta_line("texto_libre", "vf", datos)` devuelve `None` para ese fixture, y
      `_build_leaf_message(..., cuenta=None, desglose=<bloque completo>)` no incluye la
      substring `"🧮 Cuenta"` en su salida.

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain_content.py` → agregar `DESGLOSE_TEXTO_LIBRE` (1 entrada, 3 términos,
  `campo_origen` como puntero corto); modificar `desglose(kind, code)` para usar
  `DESGLOSE_TEXTO_LIBRE` cuando `kind != "avanzado"` (sin cambios respecto a Iter-1).
- `src/investbot/ai_explain.py` → agregar `_build_desglose_vf` y `_valor_desglose_vf_de_datos`;
  agregar el dict `_VF_SUB_MODELO_CODE`; agregar el parámetro opcional `context` a
  `_build_desglose_block` con la rama de delegación a `_build_desglose_vf`; en el call site de
  `handle_explain`, pasar `context=stored`. **No** se agrega `_valor_desglose_vf` a
  `_DESGLOSE_VALOR_EXTRACTORS` (eso era el diseño de Iter-1, reemplazado).
- `tests/test_ai_explain.py` (o donde vivan los tests de Desglose/Cuenta de texto libre) → tests
  nuevos para `_build_desglose_vf` (3 modelos calculables, 1-2 no calculables, los 3 no
  calculables), la firma retrocompatible de `_build_desglose_block` (con y sin `context`), la
  consistencia del valor entre paréntesis vs. la Cuenta de "vf", el largo del bloque en el peor
  caso de montos, y que `datos_del_contexto` (el payload de Ollama) no cambia para "vf". **Nuevo,
  cierre del hallazgo de `qa`**: test de regresión dirigido para el caso "0 de 3 modelos
  calculables" — confirma que `_build_cuenta_line("texto_libre", "vf", datos)` devuelve `None` y
  que el mensaje final ensamblado por `_build_leaf_message` no contiene la substring "🧮 Cuenta"
  pero sí contiene "🔍 Desglose" con las 3 sub-secciones en "no calculable" (ver Decisión de
  diseño #5 y el criterio de aceptación nuevo).

## Restricciones

- No se toca `valuation.py` — ninguna fórmula, ningún cálculo de `compute_valuation`/
  `compute_valuation_scenarios`, ningún campo de `ValuationResult`/`ScenarioValuationResult`.
  Esta spec es puramente de presentación/contenido.
- No se tocan `_cuenta_vf`, `_cuenta_gra`, `_cuenta_mul`, `_cuenta_dcf`, `_payload_texto_libre` —
  se reutilizan exactamente como están hoy, sin modificar ni una línea de su lógica.
- No se agregan campos nuevos a `ExplanationContext`, `explain_context_sink`, ni al
  `datos_del_contexto` que efectivamente ve Ollama para "vf" (`_payload_texto_libre(context,
  "vf")` en sí no cambia) — los sub-`datos` de Graham/Múltiplos/DCF que arma
  `_build_desglose_vf` son internos a la construcción del texto del Desglose, nunca llegan a
  `_fetch_explanation`.
- No se toca `_build_leaf_message`, `_build_ver_dato_content`, ni `_MAX_DESGLOSE_CHARS` /
  `_MAX_CUENTA_CHARS`. **Sí se amplía la firma de `_build_desglose_block`** (1 parámetro
  keyword-only opcional, default `None`, retrocompatible) y **sí cambia 1 línea del call site**
  de `handle_explain` — únicos 2 cambios de "mecanismo común" de este Iter-2, ambos acotados y
  sin romper ningún call site existente.
- No se agrega botón, callback ni pantalla nueva — mismos 2 botones ya existentes para "vf".
- No hace falta `chunk_for_telegram` ni partir el mensaje — cabe cómodo en 1 mensaje de Telegram
  (ver Decisión de diseño #5, presupuesto de longitud).
- Fuera de alcance de esta iteración: las 20 preguntas restantes con fórmula que aún no tienen
  desglose (texto libre: `ver`/`gra`/`dcf`/`mul`/`rat`/`pil`/`rsk`/`mom`/`cmp`; avanzado:
  `pig`/`mod`/`ben`/`aqv`/`aqq`/`aqm`/`aql`) — quedan para una futura iteración si Daniela
  confirma que el formato funciona bien para "vf" primero.
- No implementar código todavía — esta spec espera `security` (re-revisión de Iter-2) y `qa`
  antes de pasar a `implementer`.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_desglose_valor_justo_total.md` (esta spec, Iter-2 — reemplaza
  Iter-1 en el mismo documento)

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — `security` agrega los suyos. **Esto es una
re-revisión, no una revisión nueva desde cero**: la sección "Revisión de seguridad [Iter-1]" más
abajo queda como referencia histórica (sus 4 hallazgos-sin-hallazgos siguen siendo válidos para
lo que cubrían), pero el diseño cambió en 3 puntos concretos que `security` todavía no vio:

1. **`_build_desglose_block` gana un parámetro nuevo (`context`, opcional)** y una rama de
   delegación a `_build_desglose_vf` — antes la función solo leía un `datos: dict` de valores ya
   resueltos; ahora, para "vf" únicamente, también recibe el `ExplanationContext` completo y
   arma 3 sub-`datos` internos con `_payload_texto_libre`. Pedimos confirmar que ese
   `ExplanationContext` no expone, a través de este nuevo camino, nada que no estuviera ya
   accesible hoy tocando los botones "Graham"/"Múltiplos"/"DCF" por separado.
2. **El mensaje es sustancialmente más largo** (Desglose paso de ~830 a hasta ~841 caracteres en
   el peor caso — comparable, no mayor — pero el mensaje completo crece porque ahora incluye 3
   cuentas resueltas en vez de 3 líneas de texto fijo). No es un riesgo de seguridad en sí
   (sigue siendo texto fijo + números formateados con `_money`/`_ratio2`, nunca texto libre de
   terceros ni interpolación sin sanitizar), pero pedimos que `security` lo confirme explícitamente
   dado que el volumen de datos numéricos mostrados por mensaje aumenta.
3. **Los sub-`datos` de Graham/Múltiplos/DCF (construidos con `_payload_texto_libre`) nunca deben
   llegar a `datos_del_contexto`** (el dict que sí viaja a Ollama) — confirmar en el código final
   que `_build_desglose_vf` se llama con su propio `datos_vf` y jamás muta ni retorna algo que se
   asigne de vuelta a `datos_del_contexto`.

### Decisiones de diseño tomadas
Ver "Decisiones de diseño tomadas [Iter-2]" arriba (5 puntos) — no reabrir sin spec patch. No hay
decisiones de negocio abiertas para Daniela en este Iter-2 (ver "Decisiones abiertas para
Daniela" — la única de Iter-1 quedó sin objeto).

---

## Revisión de seguridad [Iter-1 — histórica, cubre el diseño anterior, no el de Iter-2]

**Modo:** Secure Code Review (bajo riesgo, feature chica — código real releído línea por línea
en `ai_explain_content.py` y `ai_explain.py`, no solo la spec). **Sin hallazgos.**

1. **Texto fijo + valores calculados, nunca Ollama** — confirmado en código: `_build_desglose_block`
   (línea 905-928 de `ai_explain.py`) no hace I/O, solo lee `datos` (dict numérico) y arma el
   bloque con `_money()` (línea 772-773: `f"${x:,.2f}"`, formateo numérico puro, sin
   interpolación de texto libre). El call site en `handle_explain` (línea 1890) arma el Desglose
   *después* de recibir la respuesta de Ollama, no antes ni como parte del payload — mismo orden
   ya auditado 2 veces hoy para Altman/Piotroski/Magic Formula. Nada nuevo entra al prompt de
   Ollama: `_payload_texto_libre("vf")` (línea 328-338) es el mismo payload de hoy, sin campos
   agregados.

2. **Modelo no calculable** — confirmado en código, no solo en la spec: `_cuenta_vf` (línea
   942-952) ya filtra `None` antes de construir la lista de valores y el divisor
   (`[v for v in (...) if v is not None]`), y el extractor propuesto `_valor_desglose_vf` sigue
   el mismo patrón exacto que `_valor_desglose_mge` (línea 871-878, ya en producción): si
   `datos.get(campo)` es `None`, devuelve `None`, nunca `"None"` como string ni un 0 disfrazado.
   `_build_desglose_block` ya maneja ese `None` con `prefijo_valor = f" = {valor}" if valor else ""`
   (línea 923) — la línea se muestra completa (letra, fórmula, qué mide) sin el segmento `= valor`,
   nunca desaparece. Este manejo es genérico y ya cubre el caso hoy para Altman/Magic Formula; "vf"
   no necesita ninguna rama nueva.

3. **`desglose(kind, code)` extendido a `texto_libre` — no rompe llamadores existentes**:
   confirmado por grep que `desglose()` tiene un único call site en todo el proyecto
   (`ai_explain.py:913`), y que `kind` solo toma 2 valores posibles en todo el código
   (`"avanzado"` en `advanced_command.py:465`, `"texto_libre"` en `query_handler.py:1079`) — no
   hay un tercer `kind` al que la nueva rama `else DESGLOSE_TEXTO_LIBRE` pueda afectar por
   sorpresa. Además, el patrón `tabla_A if kind == "texto_libre" else tabla_B` que propone la
   spec para `desglose()` es el MISMO patrón ya usado sin incidentes por `all_questions`,
   `categories`, `level1`, `formulas` y `fuentes` en el mismo archivo (línea 681-697) — es
   consistencia con el resto del módulo, no un mecanismo nuevo. Las 21 preguntas de texto libre
   sin entrada en `DESGLOSE_TEXTO_LIBRE` siguen devolviendo `()` vía `dict.get(code, ())`, sin
   cambio de comportamiento.

4. **Texto libre de terceros sin sanitizar** — no aplica ninguna superficie nueva: el Desglose de
   "vf" es 100% texto fijo de `DESGLOSE_TEXTO_LIBRE` (autor: código, no usuario) más 3 números
   (`float | None`) que ya vienen de `compute_valuation_scenarios` en `valuation.py`, nunca de
   input de un usuario de Telegram ni de un tercero. No hay ticker, símbolo, ni ningún campo de
   texto libre en el flujo de esta extensión.

**Conclusión (Iter-1):** extensión puramente aditiva, mismo patrón ya auditado y en producción
para 7 preguntas de `/avanzado`, sin superficie de ataque nueva. No se requieren criterios de
seguridad adicionales a los ya listados en "Criterios de aceptación" de Iter-1. Aprobado para
pasar a `qa`/`implementer` sin cambios — la única decisión pendiente (formato de línea, Decisión
abierta #1) es puramente de presentación y no tiene impacto en seguridad.

> **⚠️ Esta conclusión NO cubre el Iter-2.** El punto 2 de esta revisión ("Modelo no calculable")
> describe el extractor `_valor_desglose_vf` y el manejo `prefijo_valor` de Iter-1, que ya no se
> implementan (reemplazados por `_build_desglose_vf`, Decisión de diseño #2/#3 de Iter-2). El
> punto 1 ("nada nuevo entra al prompt de Ollama") sigue siendo la intención de diseño en Iter-2,
> pero el mecanismo para lograrlo cambió (`_build_desglose_block` ahora recibe `context` y
> construye sub-`datos` internos) — `security` debe confirmarlo sobre el código real de Iter-2,
> no asumir que este punto se traslada automáticamente. Ver "Handoff → security" arriba, los 3
> puntos específicos que pedimos revisar de nuevo.

---

## Revisión de seguridad [Iter-2 — sobre el alcance ampliado, código real releído hoy]

**Modo:** Secure Code Review (feature de presentación, sin I/O nuevo — código real releído línea
por línea en `ai_explain.py`, estado actual antes de implementar Iter-2, no solo la spec). **Sin
hallazgos nuevos.** Responde punto por punto los 3 puntos que dejó `architect` en "Handoff →
security" arriba.

1. **Punto 1 — ¿el nuevo `context: ExplanationContext` expone algo no accesible ya hoy tocando
   "Graham"/"Múltiplos"/"DCF" por separado?** Confirmado en código: NO. El call site real de hoy
   para esos 3 botones individuales es `_build_explain_payload(context, question_code)`
   (`ai_explain.py:580-586`), que para `context.kind == "texto_libre"` delega 100% en
   `_payload_texto_libre(context, question_code)` (línea 316 en adelante) — la MISMA función que
   `_build_desglose_vf` va a llamar con `"gra"/"mul"/"dcf"` según el diseño de la Decisión #3.
   Confirmado línea por línea (339-373) que `_payload_texto_libre` ya arma, hoy, en producción, un
   sub-dict acotado por pregunta (Decisión de diseño #11, comentario explícito en el código:
   "superficie mínima, nunca el `ExplanationContext` completo") — nunca serializa ni expone el
   objeto `context` en sí. El nuevo camino de Iter-2 no agrega superficie: es literalmente el mismo
   código, con el mismo `context`, llamado con los mismos 3 `question_code` que ya se llaman hoy
   cuando el usuario toca esos botones. Diseño consistente con lo confirmado.

2. **Punto 2 — mensaje más largo, ¿riesgo de seguridad?** Confirmado en código: NO. Todo el texto
   agregado sale de 2 fuentes, ambas ya auditadas: (a) `DESGLOSE_TEXTO_LIBRE`, texto fijo escrito
   en el código (autor: la spec, no un usuario ni un tercero), y (b) las 3 funciones
   `_cuenta_gra`/`_cuenta_mul`/`_cuenta_dcf` (línea 962-999), que solo formatean `float`/`None` ya
   provenientes de `compute_valuation_scenarios` (`valuation.py`) con `_money`/`f"{x:.1f}%"` — cero
   interpolación de texto libre, cero input de Telegram. El único efecto real del crecimiento del
   mensaje es de presupuesto de caracteres (ya resuelto en la Decisión de diseño #5 de la spec, con
   medición real: ~841 chars peor caso de Desglose, ~2100 del mensaje completo, contra el límite de
   Telegram de 4096) — no hay vector de inyección ni de exposición de dato nuevo asociado al mayor
   volumen.

3. **Punto 3 — ¿los sub-`datos` de Graham/Múltiplos/DCF pueden mezclarse con el payload que ve
   Ollama?** Confirmado en código, no solo en la spec: en el call site real de `handle_explain`
   (`ai_explain.py:1829-1891`), `datos_del_contexto` se construye en la línea 1829
   (`_build_explain_payload(stored, question_code)`) y viaja a `_fetch_explanation` en las líneas
   1872-1880 — el bloque de Desglose (línea 1890, `_build_desglose_block(...)`) se llama **después**
   de esa respuesta de Ollama, usando el mismo patrón que ya existe hoy para Altman/Piotroski/Magic
   Formula. El diseño de Iter-2 no cambia ese orden: la Decisión #4 de la spec solo agrega
   `context=stored` como argumento en esa misma línea 1890, ya posterior a `_fetch_explanation`.
   Estructuralmente, `_build_desglose_vf(context, datos_vf)` (Decisión #3) recibe su propio
   `datos_vf` (que es `datos_del_contexto`, ya usado de solo lectura) y arma los `datos_sub`
   (`_payload_texto_libre(context, "mul"/"gra"/"dcf")`) como variables **locales** dentro de esa
   función — el diseño no los retorna ni los asigna de vuelta a `datos_del_contexto`, y
   `datos_del_contexto` ya fue serializado a `datos_tokens`/enviado a `_fetch_explanation` (líneas
   1852-1880) antes de que `_build_desglose_vf` exista en el flujo. No hay ningún camino, en el
   diseño propuesto, por el que un `datos_sub` pueda alcanzar el payload de Ollama — la separación
   temporal (Ollama ya respondió) y la separación de variables (dict nuevo, nunca mutación del
   original) coinciden. El test estructural ya existente
   (`test_build_desglose_block_datos_no_es_input_de_ai_rewrite`,
   `tests/test_ai_explain.py:3040`) sigue aplicando sin cambios y `qa` debería agregar un test
   equivalente específico para `_build_desglose_vf` (ya está listado en "Artefactos a
   crear/modificar" → tests nuevos, último ítem).

**Conclusión (Iter-2):** los 3 puntos que pidió `architect` quedan confirmados sobre el código
real, sin hallazgos nuevos. El diseño de Iter-2 reutiliza exactamente el mismo mecanismo de
superficie mínima (`_payload_texto_libre` por pregunta) que ya está en producción para los botones
individuales, mantiene el orden ya auditado (Desglose se arma después de la respuesta de Ollama,
nunca antes ni como parte del payload), y el crecimiento de tamaño del mensaje es un tema de
presupuesto de caracteres ya resuelto en la spec, no un riesgo de seguridad. No se agregan
criterios de seguridad nuevos a los ya listados en "Criterios de aceptación". **Aprobado para pasar
a `qa`.**

---

## Criterios QA para Spec: Desglose Valor Justo Total [Iter-2]

> No existía sección QA de Iter-1 (Iter-1 nunca llegó a `qa` — el alcance cambió antes, ver
> encabezado del documento). Esta es la primera pasada de `qa`, ya sobre el alcance ampliado de
> Iter-2. No duplica los "Criterios de aceptación" de `architect` ni las 2 revisiones de
> `security` — los complementa con los ángulos de testabilidad y cobertura que faltan.

### Tipo de prueba principal

**Unit testing.** Todo el mecanismo (`_build_desglose_vf`, `_valor_desglose_vf_de_datos`,
`desglose("texto_libre", "vf")`, la rama nueva de `_build_desglose_block`) es 100% determinístico,
sin I/O, sin red, sin Ollama — funciones puras que reciben un `dict`/`ExplanationContext` ya
construido y devuelven `str | None`. No hay justificación para integration ni E2E como tipo
*principal*: no se toca `valuation.py`, `query_handler.py` ni ningún servicio externo (confirmado
2 veces por `security`). Se agrega un único caso de **regresión dirigida** (no un tipo de prueba
aparte) sobre la suite existente de Altman/Piotroski/Magic Formula, porque `_build_desglose_block`
cambia de firma.

### Cobertura mínima requerida

- [ ] Code coverage ≥ 90% en `_build_desglose_vf`, `_valor_desglose_vf_de_datos` y la rama nueva
      de `_build_desglose_block` (líneas nuevas de `ai_explain.py`) y en `desglose()` /
      `DESGLOSE_TEXTO_LIBRE` (líneas nuevas de `ai_explain_content.py`) — lógica de negocio de
      riesgo alto (ver tabla de riesgo del skill: "flujo principal de usuario" ≥ 90%, no es
      crítico de pagos/auth así que no exige 100%).
- [ ] Branch coverage 100% en el `for t in terminos` de `_build_desglose_vf` — las 2 ramas
      (`cuenta_sub is None` vs. calculable) deben ejecutarse para cada uno de los 3 sub-modelos,
      no solo para uno cualquiera (evita el caso "cubrí Múltiplos no calculable y asumí que
      Graham/DCF se comportan igual" sin probarlo).
- [ ] Branch coverage 100% en la condición de delegación de `_build_desglose_block`
      (`kind == "texto_libre" and question_code == "vf" and context is not None`) — las 4
      combinaciones (los 3 primeros AND más "algún operando falso") deben tener al menos 1 test.
- [ ] Todos los criterios de aceptación del `architect` (incluido el que agregó `qa` arriba, el
      caso de los 3 no calculables) cubiertos por al menos un test.

### Casos obligatorios

- [ ] **Happy path — 3 modelos calculables**: con un fixture de ticker donde
      `valor_justo_multiplos`/`_graham`/`_dcf` son todos no-`None`, el Desglose de "vf" muestra 3
      sub-secciones (Múltiplos, Graham, DCF) en ese orden, cada una con valor entre paréntesis +
      1 línea de "qué mide" + `Cuenta: ...`.
- [ ] **Consistencia byte-a-byte contra el botón individual** (el ángulo que la spec identifica
      como el riesgo central de UX, y que ningún criterio de aceptación verifica de forma
      explícita hoy): para el mismo fixture, `_cuenta_mul(_payload_texto_libre(context, "mul"))`
      llamado directamente (como lo hace hoy el botón «Múltiplos» real) debe ser **exactamente
      igual** (comparación de string completa, no solo "contiene los mismos números") al texto
      que aparece después de `"Cuenta: "` en la sub-sección de Múltiplos del Desglose de "vf". Test
      espejo para Graham y DCF. Sin este test, una futura refactorización de `_payload_texto_libre`
      podría hacer que "vf" y el botón individual diverjan sin que ningún test lo note.
- [ ] **Valor entre paréntesis == valor de la Cuenta de "vf"**: para cada sub-modelo, el número
      entre paréntesis en la línea del Desglose (`_valor_desglose_vf_de_datos`, que lee de
      `datos_vf`) debe ser igual al número correspondiente que aparece en la línea "🧮 Cuenta" de
      "vf" (`_cuenta_vf`, que lee del mismo `datos_vf`) — mismo campo, mismo escenario. Cubre
      explícitamente la advertencia de la Decisión de diseño #3 sobre por qué se lee de `datos_vf`
      y no de `datos_sub`.
- [ ] **1 modelo no calculable** (ej. Múltiplos, <2 peers con PER válido): la sub-sección de
      Múltiplos muestra literalmente `"• Múltiplos — no calculable con los datos disponibles."`
      — nunca se omite la línea, nunca aparece la palabra `"None"`, nunca aparece un valor
      inventado. Graham y DCF siguen mostrando su cuenta completa sin cambios. La Cuenta de "vf"
      refleja el promedio de los 2 calculables.
- [ ] **2 modelos no calculables**: variante del caso anterior con 2 de las 3 líneas en
      "no calculable" y 1 con cuenta completa — evita el sesgo de solo probar "1 de 3 falla".
- [ ] **3 modelos no calculables (caso extremo agregado por `qa`)**: el Desglose se sigue
      mostrando completo (3 líneas "no calculable"), aunque la Cuenta de "vf" sea `None` (y por lo
      tanto no se muestre esa línea en el mensaje final). Test explícito de que
      `_build_desglose_vf` no depende de `_cuenta_vf` para decidir si renderizarse.
- [ ] **Excepción dentro de `_payload_texto_libre` o `_cuenta_*` para un sub-modelo** (el
      `try/except Exception` de `_build_desglose_vf`): forzar que uno de los 3 sub-`datos` lance
      una excepción al construirse y confirmar que esa sub-sección cae a "no calculable" en vez de
      propagar el error y romper todo el mensaje de "vf" — mismo criterio de robustez que el resto
      del mecanismo de Desglose (Altman/Magic Formula) ya prueba hoy.
- [ ] **Retrocompatibilidad de firma**: los tests existentes que llaman
      `_build_desglose_block("avanzado", code, datos)` con 3 argumentos posicionales (sin
      `context`) siguen pasando sin modificar ni una línea. Un test nuevo llama
      `_build_desglose_block("texto_libre", "vf", datos, context=None)` explícitamente y confirma
      que devuelve `None` (no intenta construir el Desglose de "vf" sin `context`) — cubre la
      condición `context is not None` del `if`.
- [ ] **`datos_del_contexto` (payload de Ollama) no cambia**: comparar `_payload_texto_libre(context,
      "vf")` antes y después del cambio (mismo fixture) y confirmar dict-igual — más un test
      explícito de que `_build_desglose_vf` nunca escribe ni retorna nada que se asigne a
      `datos_del_contexto` (equivalente al test estructural ya existente
      `test_build_desglose_block_datos_no_es_input_de_ai_rewrite`, mencionado por `security`, pero
      dedicado a `_build_desglose_vf`).
- [ ] **Largo del bloque en el peor caso real** (no el cálculo de la spec, un test que lo mida): con
      un fixture de montos extremos (9-10 cifras, `$999,999,999.99` en cada término de DCF,
      WACC/g de 2 dígitos), `len(_build_desglose_vf(...))` ≤ `_MAX_DESGLOSE_CHARS` (1200) y el
      mensaje completo ensamblado (Cuenta + Desglose + resto de secciones con
      `_MAX_EXPLANATION_CHARS` al tope) ≤ `TELEGRAM_MESSAGE_LIMIT` (4096) — no basta con
      confirmar que el número medido en la spec (~841 / ~2100) es correcto una vez, hace falta un
      test que seguiría fallando si alguien agranda una plantilla de texto en el futuro.
- [ ] **"📊 Ver dato" de "vf" no cambia**: test de regresión explícito — el contenido de
      `_build_ver_dato_content` (o el mensaje que arma el botón «Ver dato» para "vf") es
      byte-a-byte igual antes y después del cambio; confirma que ese flujo nunca llama a
      `_build_desglose_block` ni a `_build_desglose_vf`.
- [ ] **Regresión de las 27 preguntas restantes** (20 de `/avanzado` + 21 de texto libre sin
      `"vf"`, según lista de la spec): al menos 1 test parametrizado que recorra esas preguntas y
      confirme que `_build_desglose_block(stored.kind, code, datos, context=stored)` (con
      `context` siempre pasado, como queda el call site real) devuelve exactamente lo mismo que
      devolvía `_build_desglose_block(stored.kind, code, datos)` sin `context` antes del cambio —
      cubre la afirmación de la spec de "cero regresión" con evidencia, no solo con lectura de
      código.

### Testabilidad

- [ ] `_build_desglose_vf` es una función pura (recibe `context` y `datos_vf`, devuelve
      `str | None`) — no depende de estado global ni de mocks de red, se prueba llamándola
      directamente con un `ExplanationContext` de fixture.
- [ ] No hace falta mockear Ollama para ningún test de esta spec — el mecanismo se arma después de
      la respuesta de Ollama y no depende de su contenido; los tests pueden construir `datos_vf`
      y `context` directamente sin pasar por `_fetch_explanation`.
- [ ] Los fixtures de `ExplanationContext`/`scenarios` ya deberían existir (reutilizados de los
      tests actuales de "vf" paso a paso) — si no alcanzan para los casos de "no calculable"
      forzado, construir variantes mínimas del mismo fixture (poner `valor_justo_multiplos=None`
      en el escenario) en vez de mocks de `compute_valuation_scenarios`.

### Fixtures mínimos que faltan (a construir para esta spec)

1. **Ticker A — 3 modelos calculables** (el del Caso 1 de la spec: Múltiplos $144.40, Graham
   $130.00, DCF $160.00) — happy path, consistencia contra botones individuales, valor entre
   paréntesis vs. Cuenta.
2. **Ticker B — Múltiplos no calculable** (el del Caso 2 de la spec: <2 peers con PER válido) —
   Graham y DCF calculables. Cubre "1 no calculable".
3. **Ticker C — 2 modelos no calculables** (nuevo, no está en los ejemplos de la spec — ej.
   Múltiplos sin peers Y Graham sin EPS TTM positivo, DCF calculable) — cubre "2 no calculables"
   sin sesgo hacia probar solo 1.
4. **Ticker D — 3 modelos no calculables** (nuevo, cubre el gap detectado en "Criterios de
   aceptación" arriba) — confirma que el Desglose se muestra igual aunque la Cuenta de "vf" sea
   `None`.
5. **Ticker E — montos extremos** (el del "peor caso" de la spec: DCF con 9-10 cifras,
   `$999,999,999.99` en cada término, WACC/g de 2 dígitos) — para medir el largo real del bloque
   con un test, no solo con el script Python que corrió `architect` una vez.
6. **Fixture de escenario variable** — mismo ticker, `escenario_elegido` en `"pesimista"`,
   `"conservador"` y `"optimista"` — confirma que el valor entre paréntesis y la Cuenta usan
   siempre el mismo escenario elegido, sin importar cuál sea.

### Criterio de exit de QA

- Todos los tests pasan (BUILD SUCCESS / suite verde), incluida la suite completa existente
  (no solo los tests nuevos).
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (no debería haber ninguna fuente de no-determinismo: sin
  tiempo, sin red, sin orden de diccionario relevante).
- El criterio de aceptación agregado por `qa` (3 modelos no calculables) queda resuelto por
  `architect` — con test, no solo con una decisión verbal — antes de declarar la spec lista para
  `implementer`.

### Qué NO se prueba en esta spec, y por qué

- **Contenido semántico de la respuesta de Ollama** — no determinístico por diseño, y ya está
  fuera del mecanismo de Desglose (se arma antes, sin relación). Cubierto (o no) por las specs de
  "paso a paso" ya cerradas, no por esta.
- **Render visual real en clientes de Telegram** (cómo se ven los saltos de línea, el emoji 🔍,
  el sangrado de "  Cuenta:" en Android vs. iOS vs. Telegram Web) — fuera de alcance de unit
  testing; se prueba el string generado en Python contra el límite de caracteres, no la
  representación visual final. Si Daniela lo pide, sería exploratory testing manual, no
  automatizado.
- **Performance/carga** — no aplica: no hay I/O nuevo, el cálculo es formateo de floats ya
  calculados, del orden de microsegundos. No se justifica un test de carga para esto.
- **Seguridad** — ya cubierta 2 veces por `security` (Iter-1 e Iter-2), sin hallazgos en ninguna.
  No se duplican esos criterios acá; el único punto de intersección (que `datos_sub` nunca llegue
  a Ollama) ya está listado arriba en "Casos obligatorios" porque también es un caso de
  regresión funcional, no solo de seguridad.
- **Las 20 preguntas restantes sin Desglose** (`ver`/`gra`/`dcf`/`mul`/`rat`/`pil`/`rsk`/`mom`/
  `cmp` en texto libre; `pig`/`mod`/`ben`/`aqv`/`aqq`/`aqm`/`aql` en avanzado) — explícitamente
  fuera de alcance de esta iteración según la spec ("Restricciones"). Solo se prueba que su
  comportamiento actual no cambia (ver "Regresión de las 27 preguntas restantes" arriba), no se
  les agrega Desglose nuevo.
- **Botones individuales «Graham»/«Múltiplos»/«DCF» en sí** (`_cuenta_gra`/`_cuenta_mul`/
  `_cuenta_dcf`, `_payload_texto_libre` para esos `question_code`) — ya tienen su propia suite de
  tests en producción y la spec prohíbe modificarlos; esta spec solo prueba que "vf" los *reutiliza
  correctamente*, no vuelve a probar su lógica interna desde cero (evita duplicar cobertura).
