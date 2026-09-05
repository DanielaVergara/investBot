# Spec: Cuenta narrada paso a paso — Graham (`gra`) y DCF (`dcf`)

**Rol:** `architect` (spec base).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa (superficie de longitud de mensaje y guard de integridad, igual
que las specs de Desglose anteriores). `qa` agrega criterios de cobertura. `dba`/`frontend`/`backend`
no aplican. Después, `implementer` ejecuta.
**Estado:** spec nueva, lista para `security`.

---

## Contexto

Daniela mostró un ejemplo real del mensaje de hoy para "🧮 Cuenta" de Graham y DCF:

```
Graham: $16.70 × (8.5 + 2×13.4) × 4.4 / 5.3 = $493.44
DCF: FCF base $9,852,000,000.00, crece a g=9.3% anual (WACC=12.4%) → FCF proyectado
año 5 ≈ $15,396,413,238.50. Flujos descontados a valor presente ≈ $45,360,690,094.48
+ valor terminal descontado ≈ $88,645,529,833.61 = valor de la empresa ≈
$134,006,219,928.09 → $313.83 por acción
```

Y dijo explícitamente: "no entiendo la fórmula". Confirmó que quiere una **narración de los
pasos del proceso**, no la fórmula comprimida con números sustituidos. Aprobó explícitamente este
formato para DCF:

```
"Para saber cuánto vale la empresa hoy, el modelo hace 4 pasos: 1) toma cuánto efectivo libre
genera HOY el negocio ($9,852M), 2) asume que va a crecer 9.3% por año durante 5 años, 3) 'trae'
cada uno de esos años futuros a su valor de HOY (porque un peso dentro de 5 años vale menos que
uno hoy — se descuenta al 12.4%, el costo de capital), y 4) le suma un 'valor terminal' (lo que
vale seguir generando plata para siempre después del año 5). Todo eso sumado y dividido entre las
acciones da $313.83 por acción."
```

Y pidió que se diseñe también la narración de Graham (3 pasos: EPS actual → ajuste por
crecimiento con techo conservador → multiplicador × ajuste por tasa libre de riesgo), con el
mismo criterio de redacción.

### Código real leído para esta spec (confirmado hoy, línea por línea)

- **`src/investbot/ai_explain.py`** (2355 líneas):
  - `_cuenta_gra(datos)` (línea 1232-1241) — arma hoy la fórmula comprimida de Graham:
    `f"{_money(eps)} × (8.5 + 2×{g_pct:.1f}) × {GRAHAM_HISTORICAL_YIELD:.1f} / {y_pct:.1f} = {_money(valor)}"`.
    Lee `datos["eps_ttm"]`, `datos["g_aplicado"]`, `datos["y_value"]` y
    `_valor_escenario_elegido(datos)`. Devuelve `None` si falta cualquiera de los 4 o si `y == 0`.
  - `_cuenta_dcf(datos)` (línea 1252-1269) — arma hoy la fórmula comprimida de DCF (FCF base →
    FCF proyectado año 5 → flujos descontados + valor terminal descontado → equity → valor por
    acción). Lee `datos["dcf_wacc"]`, `datos["dcf_g_fcf"]`, `datos["dcf_fcf_base"]`,
    `datos["dcf_valor_presente_flujos"]`, `datos["dcf_valor_terminal_descontado"]`,
    `datos["dcf_equity_value"]` y `_valor_escenario_elegido(datos)`. Devuelve `None` si falta
    cualquiera de los 7.
  - `_CUENTA_TEXTO_LIBRE` (línea 1370-1374) — dict `question_code -> función de Cuenta`, mapea
    `"gra" -> _cuenta_gra`, `"dcf" -> _cuenta_dcf`. Se usa en 2 lugares:
    1. `_build_cuenta_line(kind, question_code, datos)` (línea 1601-1618) — call site del botón
       individual (💰 Valoración → Graham/DCF → "🎓 Explicame paso a paso"). Aplica
       `_enforce_cuenta_length(cuenta)` (línea 800-814) con `_MAX_CUENTA_CHARS = 400` (línea 785)
       **al resultado completo** — si excede, **omite el bloque completo** (nunca trunca a mitad
       de un número).
    2. `_build_desglose_vf(context, datos_vf)` (línea 1164-1198) — call site anidado dentro de
       "💰 Valor Justo Total". Para cada sub-modelo (Múltiplos/Graham/DCF), arma un `datos_sub`
       propio con `_payload_texto_libre(context, code)` y llama a
       `_CUENTA_TEXTO_LIBRE[code](datos_sub)` — **sin pasar por `_enforce_cuenta_length`
       individualmente**; el único guard de longitud que corre sobre este bloque es
       `_enforce_desglose_length` (línea 1094-1107), con `_MAX_DESGLOSE_CHARS = 1200` (línea 823),
       aplicado **al bloque "🔍 Desglose:" completo** (las 3 sub-cuentas + las 3 líneas de
       "qué mide" + el separador), no a cada sub-cuenta por separado.
  - `_build_leaf_message` (línea 719-754) — ensambla el mensaje final del botón individual:
    header → Dato → `🧮 Cuenta: {cuenta}` (si existe) → Desglose (si existe) → respuesta de
    Ollama → Fórmula/Fuente → disclaimer. **No cambia en esta spec** — la narración sigue entrando
    por el mismo `if cuenta: partes.append(f"🧮 Cuenta: {cuenta}")` (línea 741-742), sin tocar el
    label ni el orden.

- **`src/investbot/ai_explain_content.py`** (1018 líneas):
  - `FORMULAS_TEXTO_LIBRE["gra"]`/`["dcf"]` (línea 175-176) y `FUENTES_TEXTO_LIBRE["gra"]`/`["dcf"]`
    (línea 219-230) — **no se tocan**: siguen siendo la fórmula abstracta y la fuente de cada dato,
    secciones separadas (`📐 Fórmula`/`📊 Fuente del dato`) que se muestran DESPUÉS de la Cuenta y
    de la respuesta de Ollama. La narración no las reemplaza, conviven (la narración cuenta el
    "cómo se llega al número con los datos de este ticker"; Fórmula/Fuente siguen siendo la
    referencia técnica abstracta + de dónde sale cada insumo).
  - `DESGLOSE_TEXTO_LIBRE["gra"]`/`["dcf"]` (línea 760-821) — los 3/6 términos con
    `campo_origen`/`nombre`/`que_mide` de cada componente, usados por `_valor_desglose_gra`/
    `_valor_desglose_dcf` cuando el usuario toca el botón individual "🔎 Ver desglose" de Graham/DCF
    (`/avanzado`-style, 20 preguntas que ya lo tienen). **No se tocan** — ese Desglose (letra por
    letra: EPS/g/Y para Graham; FCF base/WACC/g/VP flujos/VT descontado/Valor de la empresa para
    DCF) es un mecanismo distinto y ya resuelto, complementario a la Cuenta, no competidor.

- **`src/investbot/valuation.py`** (813 líneas):
  - `GRAHAM_HISTORICAL_YIELD = 4.4` (línea 39), `GRAHAM_G_CAP = 0.15` (línea 81) — el techo del
    15% sobre `g` (CAGR de EPS) que Daniela pidió mencionar en la narración de Graham. Ya aplicado
    ANTES de que `g` llegue a `datos["g_aplicado"]` (`_cap_graham_g`, línea 84-100) — la narración
    solo lo redacta, no lo recalcula.
  - `DCF_PROJECTION_YEARS = 5` (línea 41), `TERMINAL_GROWTH_RATE = 0.025` (línea 40) — constantes
    ya usadas por `_cuenta_dcf` hoy (vía `valuation.DCF_PROJECTION_YEARS`), reutilizadas tal cual
    por la narración.
  - Ninguna fórmula de este archivo se toca — confirmado, ver Restricciones.

---

## Estado actual

- Botón individual "Graham"/"DCF" → "🎓 Explicame paso a paso" → sección "🧮 Cuenta": fórmula
  comprimida con números sustituidos, en 1 línea densa (ejemplo real arriba). Daniela confirmó que
  no se entiende.
- "💰 Valor Justo Total" → "🎓 Explicame paso a paso" → sección "🔍 Desglose" → sub-secciones
  Graham/DCF: reutilizan la MISMA fórmula comprimida (`_cuenta_gra`/`_cuenta_dcf` sin cambios,
  vía `_build_desglose_vf`).

## Estado objetivo

1. El botón individual de **Graham** y de **DCF** muestra, en el lugar donde hoy se muestra la
   fórmula comprimida (`🧮 Cuenta:`), una **narración de los pasos del cálculo con los números
   reales del ticker sustituidos** — nunca la fórmula en una sola línea densa.
2. La versión anidada dentro de **"💰 Valor Justo Total"** también narra Graham y DCF (no vuelve
   a la fórmula comprimida) — pero con una **redacción más corta** que la del botón individual,
   por presupuesto de caracteres (ver Decisión de diseño #3). Sigue siendo una narración legible
   de los pasos, no solo números — nunca se resigna a la fórmula comprimida como atajo para
   ahorrar caracteres.
3. Múltiplos (`mul`) **no cambia** — Daniela no reportó queja sobre esa Cuenta (`$9.50 × 15.20 =
   $144.40`, ya es 1 sola operación, fácil de leer) y no está en el alcance de este pedido.
4. Ningún cálculo de `valuation.py` cambia — es 100% redacción del mismo resultado ya calculado.
5. Si algún componente no está disponible para el ticker, la narración completa se omite (mismo
   comportamiento ya existente: `_cuenta_gra`/`_cuenta_dcf` devuelven `None` si falta cualquiera
   de sus campos) — nunca se arma una narración con un paso en blanco o inventado.

---

## Decisiones de diseño tomadas

### 1. Reemplaza, no agrega — mismo campo `datos`, mismo label `🧮 Cuenta:`, cero cambio de mecanismo

Se reescribe el **contenido** que devuelven `_cuenta_gra`/`_cuenta_dcf`, no se agrega una sección
nueva. Razones:

- Daniela no pidió "además de la fórmula, la narración" — pidió reemplazar la fórmula porque no
  la entiende. Agregar ambas duplicaría la explicación del mismo cálculo dos veces en el mismo
  mensaje, sin beneficio.
- `_build_leaf_message`, `_build_desglose_vf`, `_build_cuenta_line`, `_CUENTA_TEXTO_LIBRE`,
  `_enforce_cuenta_length`/`_enforce_desglose_length` — **cero cambios de firma o de mecanismo**.
  El único cambio real es el cuerpo de 2 funciones (`_cuenta_gra`/`_cuenta_dcf`) + 2 funciones
  nuevas para la versión corta anidada (ver Decisión #3) + 1 ajuste de presupuesto de longitud
  (ver Decisión #4). Coherente con la restricción de Daniela ("esto es solo presentación/
  redacción del mismo cálculo que ya se hace").

### 2. Narración de Graham — 3 pasos, redactados desde el código real de `valuation.py`

Daniela pidió que el arquitecto redacte el contenido (no ella). Basado en
`calculate_graham_fair_value` (línea 146-176 de `valuation.py`) y `_cap_graham_g` (línea 84-100):

**Los 3 pasos reales del cálculo** (ya se ejecutan hoy, la narración solo los cuenta en orden):
1. Toma el EPS TTM (`eps_ttm`) — cuánto ganó la empresa por acción en los últimos 12 meses.
2. Proyecta ese EPS con una tasa de crecimiento (`g_aplicado` = CAGR histórico de EPS, con techo
   de `GRAHAM_G_CAP = 0.15` aplicado ANTES por `_cap_graham_g` — la narración menciona el techo
   explícitamente, tal como pidió Daniela: "un techo conservador").
3. Multiplica por el factor `(8.5 + 2×g_pct) × GRAHAM_HISTORICAL_YIELD` y divide entre `y_value`
   (la tasa del bono del Tesoro a 10 años) — la narración explica la dirección del efecto (mayor
   tasa libre de riesgo → menor valor justo, porque compite con una alternativa "sin riesgo" más
   atractiva), no solo el número.

**Texto (Python, `ai_explain.py`, reemplaza el cuerpo de `_cuenta_gra`)**:

```python
def _cuenta_gra(datos: dict) -> Optional[str]:
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    valor = _valor_escenario_elegido(datos)
    if None in (eps, g, y, valor) or y == 0:
        return None
    g_pct, y_pct = g * 100, y * 100
    return (
        f"Para saber cuánto vale la acción según este modelo clásico, el cálculo sigue 3 pasos: "
        f"1) toma cuánto ganó la empresa por acción en el último año ({_money(eps)} de EPS), "
        f"2) proyecta cuánto puede crecer esa ganancia a futuro usando el crecimiento histórico "
        f"de sus ganancias, con un techo del 15% para no ser demasiado optimista (en este caso, "
        f"{g_pct:.1f}%), y 3) multiplica todo por un factor fijo de la fórmula (8.5 + "
        f"2×crecimiento) y lo divide entre la tasa del bono del Tesoro a 10 años ({y_pct:.1f}%) "
        f"— cuanto más alta esa tasa \"sin riesgo\", menor el valor justo, porque hay una "
        f"alternativa más segura disponible. El resultado: {_money(valor)} por acción."
    )
```

**Guardas — sin cambios respecto a hoy**: mismos 4 campos (`eps_ttm`, `g_aplicado`, `y_value`,
`_valor_escenario_elegido(datos)`), mismo `None`/`y == 0` → `None`. El `15%` del techo está
escrito como texto fijo (no `valuation.GRAHAM_G_CAP * 100`) porque es una constante estable y
citada por su nombre en el texto explicativo ("un techo del 15%"); si `GRAHAM_G_CAP` cambiara de
valor en el futuro, ese cambio de `valuation.py` debe venir acompañado de actualizar este texto —
señalado en Restricciones.

**Ejemplo con los números reales de Daniela** (`EPS=$16.70`, `g=13.4%`, `Y=4.4%`,
`valor=$493.44`):

```
Para saber cuánto vale la acción según este modelo clásico, el cálculo sigue 3 pasos: 1) toma
cuánto ganó la empresa por acción en el último año ($16.70 de EPS), 2) proyecta cuánto puede
crecer esa ganancia a futuro usando el crecimiento histórico de sus ganancias, con un techo del
15% para no ser demasiado optimista (en este caso, 13.4%), y 3) multiplica todo por un factor
fijo de la fórmula (8.5 + 2×crecimiento) y lo divide entre la tasa del bono del Tesoro a 10 años
(4.4%) — cuanto más alta esa tasa "sin riesgo", menor el valor justo, porque hay una alternativa
más segura disponible. El resultado: $493.44 por acción.
```

627 caracteres (medido con Python real, no estimado).

### 3. Narración de DCF — 4 pasos, formato ya aprobado explícitamente por Daniela

**Texto (Python, `ai_explain.py`, reemplaza el cuerpo de `_cuenta_dcf`)**:

```python
def _cuenta_dcf(datos: dict) -> Optional[str]:
    wacc = datos.get("dcf_wacc")
    g = datos.get("dcf_g_fcf")
    base = datos.get("dcf_fcf_base")
    vp_flujos = datos.get("dcf_valor_presente_flujos")
    vt_desc = datos.get("dcf_valor_terminal_descontado")
    equity = datos.get("dcf_equity_value")
    valor_accion = _valor_escenario_elegido(datos)
    if None in (wacc, g, base, vp_flujos, vt_desc, equity, valor_accion):
        return None
    years = valuation.DCF_PROJECTION_YEARS
    return (
        f"Para saber cuánto vale la empresa hoy, el modelo hace 4 pasos: 1) toma cuánto efectivo "
        f"libre genera HOY el negocio ({_money(base)}), 2) asume que va a crecer {g * 100:.1f}% "
        f"por año durante {years} años, 3) \"trae\" cada uno de esos años futuros a su valor de "
        f"HOY (porque un peso dentro de {years} años vale menos que uno hoy — se descuenta al "
        f"{wacc * 100:.1f}%, el costo de capital), y 4) le suma un \"valor terminal\" (lo que "
        f"vale seguir generando plata para siempre después del año {years}). Todo eso sumado y "
        f"dividido entre las acciones da {_money(valor_accion)} por acción."
    )
```

**Guardas — sin cambios respecto a hoy**: mismos 7 campos, mismo `None` si falta cualquiera.
**Diferencia respecto a la versión de hoy**: ya no se calcula `fcf_year5 = base * (1 + g) **
years` (era el único cálculo que hacía la función además de formatear — no aportaba a la
narración aprobada por Daniela, que no menciona el FCF proyectado del año 5 como paso propio,
sino que lo absorbe implícitamente en "asume que va a crecer X% por año durante 5 años"). Esto
es consistente con la restricción "no toques ninguna fórmula ya validada": `fcf_year5` no es un
resultado que `valuation.py` expone (`DCFBreakdown` no lo tiene bajo ese nombre en el payload de
`_cuenta_dcf`; el campo real es `dcf_fcf_base`, `dcf_valor_presente_flujos`, etc., todos leídos
tal cual) — era una re-derivación cosmética solo para mostrarla en la fórmula comprimida, que la
narración aprobada por Daniela ya no necesita.

**Ejemplo con los números reales de Daniela** (`FCF base=$9,852,000,000.00`, `g=9.3%`,
`WACC=12.4%`, `valor_accion=$313.83`):

```
Para saber cuánto vale la empresa hoy, el modelo hace 4 pasos: 1) toma cuánto efectivo libre
genera HOY el negocio ($9,852,000,000.00), 2) asume que va a crecer 9.3% por año durante 5 años,
3) "trae" cada uno de esos años futuros a su valor de HOY (porque un peso dentro de 5 años vale
menos que uno hoy — se descuenta al 12.4%, el costo de capital), y 4) le suma un "valor terminal"
(lo que vale seguir generando plata para siempre después del año 5). Todo eso sumado y dividido
entre las acciones da $313.83 por acción.
```

521 caracteres (medido con Python real) — coincide, salvo formato de números, con el texto que
Daniela aprobó explícitamente.

### 4. Versión anidada en "💰 Valor Justo Total" — narración MÁS CORTA, no la fórmula comprimida

**El punto más importante de esta spec, medido con Python real (no estimado) — ver script y
resultados completos más abajo.**

**Hallazgo bloqueante si se reutiliza la narración completa tal cual dentro de "vf":** el bloque
"🔍 Desglose:" de "vf" (que junta Múltiplos + Graham + DCF, cada uno con su propia sub-cuenta)
mide, en el **peor caso de montos** (FCF base a escala de ~$1 billón/trillion, tasas de 2 dígitos,
EPS de 3 cifras): **1701 caracteres con las narraciones completas — EXCEDE
`_MAX_DESGLOSE_CHARS = 1200`**. Por el criterio ya establecido (`_enforce_desglose_length`,
Decisión de diseño #6 de `SDD_desglose_terminos_formula.md`: si excede el tope, se omite el
**bloque completo**, nunca se trunca a mitad de un número), esto significaría que para un ticker
de esa magnitud el usuario **pierde el Desglose entero** de "vf" — mucho peor que mostrar una
narración más corta. Por eso la versión anidada usa una redacción corta dedicada, nunca la
narración completa.

**Diseño: 2 funciones nuevas, dedicadas a la versión anidada — `_cuenta_gra_corta`/
`_cuenta_dcf_corta`, con los mismos 4/7 campos y las mismas guardas que
`_cuenta_gra`/`_cuenta_dcf`, pero redactadas como pasos encadenados con flechas (`→`) en vez de
prosa completa — sigue siendo una narración de pasos, no la fórmula comprimida de hoy:**

```python
def _cuenta_gra_corta(datos: dict) -> Optional[str]:
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    valor = _valor_escenario_elegido(datos)
    if None in (eps, g, y, valor) or y == 0:
        return None
    g_pct, y_pct = g * 100, y * 100
    return (
        f"EPS {_money(eps)} → se proyecta con {g_pct:.1f}% de crecimiento (techo 15%) → se "
        f"multiplica por 8.5+2×crecimiento y se ajusta por la tasa del bono a 10 años "
        f"({y_pct:.1f}%, a mayor tasa, menor valor) = {_money(valor)}."
    )


def _cuenta_dcf_corta(datos: dict) -> Optional[str]:
    wacc = datos.get("dcf_wacc")
    g = datos.get("dcf_g_fcf")
    base = datos.get("dcf_fcf_base")
    vp_flujos = datos.get("dcf_valor_presente_flujos")
    vt_desc = datos.get("dcf_valor_terminal_descontado")
    equity = datos.get("dcf_equity_value")
    valor_accion = _valor_escenario_elegido(datos)
    if None in (wacc, g, base, vp_flujos, vt_desc, equity, valor_accion):
        return None
    years = valuation.DCF_PROJECTION_YEARS
    return (
        f"FCF hoy {_money(base)} → crece {g * 100:.1f}%/año durante {years} años → se descuentan "
        f"esos flujos futuros a valor de hoy al {wacc * 100:.1f}% (costo de capital) → se suma el "
        f"valor de seguir generando caja después del año {years} = {_money(valor_accion)} por "
        f"acción."
    )
```

**`_build_desglose_vf` (línea 1164-1198) — 1 cambio acotado**: hoy llama a
`_CUENTA_TEXTO_LIBRE[code](datos_sub)` (línea 1186) para las 3 sub-cuentas. Se agrega un dict
nuevo, dedicado a la versión anidada, que reusa `_cuenta_mul` tal cual (no cambia, Decisión #1)
pero usa las versiones cortas para Graham/DCF:

```python
_VF_SUB_MODELO_CUENTA = {
    "mul": _cuenta_mul,
    "gra": _cuenta_gra_corta,
    "dcf": _cuenta_dcf_corta,
}
```

Y la línea `cuenta_sub = _CUENTA_TEXTO_LIBRE[code](datos_sub)` pasa a
`cuenta_sub = _VF_SUB_MODELO_CUENTA[code](datos_sub)`. `_CUENTA_TEXTO_LIBRE` (usado por el botón
individual vía `_build_cuenta_line`) sigue apuntando a `_cuenta_gra`/`_cuenta_dcf` (las versiones
narradas completas) — **sin cambios**.

**Ejemplo con los números reales de Daniela, versión corta**:

```
EPS $16.70 → se proyecta con 13.4% de crecimiento (techo 15%) → se multiplica por
8.5+2×crecimiento y se ajusta por la tasa del bono a 10 años (4.4%, a mayor tasa, menor valor) =
$493.44.
```
```
FCF hoy $9,852,000,000.00 → crece 9.3%/año durante 5 años → se descuentan esos flujos futuros a
valor de hoy al 12.4% (costo de capital) → se suma el valor de seguir generando caja después del
año 5 = $313.83 por acción.
```

### 5. `_MAX_CUENTA_CHARS = 400` no alcanza para la narración completa del botón individual —
   nuevo tope dedicado `_MAX_CUENTA_NARRADA_CHARS = 800`

**Hallazgo bloqueante, medido con Python real**: la narración completa de Graham mide 627
caracteres en el caso real de Daniela y hasta **637 en el peor caso de montos** (EPS de 3 cifras,
tasas de 2 dígitos); la de DCF mide 521 en el caso real y hasta **532 en el peor caso** (FCF base
a escala de ~$1 billón). **Ambas superan `_MAX_CUENTA_CHARS = 400`** — si se dejara ese tope sin
cambios, `_enforce_cuenta_length` omitiría la Cuenta narrada completa en el botón individual
**siempre**, para cualquier ticker, incluido el caso real que motivó este pedido. Esto rompería
el objetivo #1 de esta spec.

**Decisión**: nuevo tope dedicado, aplicado ÚNICAMENTE a `gra`/`dcf` en `_build_cuenta_line`
(línea 1601-1618) — las 8 preguntas restantes de `_CUENTA_TEXTO_LIBRE` (`ver`, `vf`, `mul`, `rat`,
`pil`, `rsk`, `mom`, `cmp`) siguen con `_MAX_CUENTA_CHARS = 400` sin cambios, porque sus Cuentas
no cambian de formato en esta spec:

```python
_MAX_CUENTA_NARRADA_CHARS = 800  # Graham/DCF narrados — peor caso medido: 637/532 caracteres


def _enforce_cuenta_length(cuenta: str, max_chars: int = _MAX_CUENTA_CHARS) -> Optional[str]:
    """Mismo criterio de siempre (nunca trunca a mitad de un número, omite el
    bloque completo si excede) -- `max_chars` parametrizado para que Graham/
    DCF narrados usen `_MAX_CUENTA_NARRADA_CHARS` sin afectar el tope de las
    8 preguntas restantes."""
    if len(cuenta) > max_chars:
        logger.warning(
            "Cuenta de %d caracteres excede max_chars=%d -- bloque omitido",
            len(cuenta), max_chars,
        )
        return None
    return cuenta


def _build_cuenta_line(kind: str, question_code: str, datos: dict) -> Optional[str]:
    ...  # resto sin cambios
    cuenta = _CUENTA_TEXTO_LIBRE[question_code](datos)
    if cuenta is None:
        return None
    max_chars = _MAX_CUENTA_NARRADA_CHARS if question_code in ("gra", "dcf") else _MAX_CUENTA_CHARS
    return _enforce_cuenta_length(cuenta, max_chars)
```

**800 se eligió con margen ~25% sobre el peor caso medido (637)** — mismo criterio de margen que
ya usó `_MAX_CUENTA_CHARS = 400` originalmente (documentado como "2.5x sobre el caso más largo
conocido" en el código actual) y que usó `_MAX_DESGLOSE_CHARS = 1200` — acá el margen es menor
(1.25x) porque la narración en prosa completa, a diferencia de una fórmula, no tiene forma de
acortarse más sin perder claridad; un margen de 2.5x (2000+ caracteres) sería un tope
artificialmente laxo sin beneficio, dado que el mensaje completo del botón individual (ver
Decisión #6) tiene margen amplio de todas formas.

**La versión anidada (`_cuenta_gra_corta`/`_cuenta_dcf_corta`) NO usa este tope nuevo** — no pasa
individualmente por `_enforce_cuenta_length` (igual que hoy, `_build_desglose_vf` no la aplica por
sub-cuenta); el único guard que corre sobre esas 2 sub-cuentas es `_enforce_desglose_length`
(1200), aplicado al bloque "🔍 Desglose:" completo (ver Decisión #4 y #6).

### 6. Presupuesto de caracteres — medido con Python real sobre el formato exacto, peor caso de
   montos (lección de hoy: nunca estimar)

**Script y metodología**: se armaron las 4 funciones de narración (completa ×2, corta ×2) con el
código exacto propuesto arriba, se corrieron con:
- **Caso real** (los números que pegó Daniela): Graham 627 caracteres, DCF 521 caracteres.
- **Peor caso de montos** (EPS $999.99, g al techo de 15%, Y al 99.9% como cota superior
  defensiva, FCF base $999,999,999,999.99 ≈ 1 billón/trillion, WACC/g al 99.9%, valor por acción
  $999,999,999.99): Graham narrada completa **637 caracteres**, DCF narrada completa **532
  caracteres**; Graham corta **197 caracteres**, DCF corta **231 caracteres**.

**Botón individual (Graham o DCF solos)** — mensaje completo en el peor caso:
`header (~75) + Dato (~90) + Cuenta narrada (637 o 532, peor caso) + Desglose si el usuario lo
pidió por separado (no aplica al mismo mensaje) + respuesta de Ollama (tope duro
_MAX_EXPLANATION_CHARS=480) + Fórmula/Fuente (~250-350) + disclaimer (~150) + separadores (~7×2)`
≈ **~1750-1850 caracteres en el peor caso** — muy por debajo de `TELEGRAM_MESSAGE_LIMIT = 4096`,
margen amplio (más de la mitad libre).

**"💰 Valor Justo Total" con las 2 narraciones cortas anidadas + Múltiplos + Cuenta propia de
"vf" + Desglose + Ollama + Fórmula/Fuente + disclaimer** — medido con Python real sobre el
formato exacto de `_build_desglose_vf`, peor caso de montos en las 3 sub-cuentas:

- Bloque "🔍 Desglose:" completo (Múltiplos + Graham corta + DCF corta, con sus 3 líneas de
  "qué mide"): **960 caracteres** — por debajo de `_MAX_DESGLOSE_CHARS = 1200`, con margen de
  ~240 caracteres (20%). **Si se hubiera usado la narración completa en vez de la corta, el mismo
  bloque mide 1701 caracteres — excede el tope y el Desglose completo se omitiría** (ver
  Decisión #4). Este es el resultado que justifica la versión corta.
- Mensaje completo de "vf" (header + Dato + Cuenta de "vf" + Desglose de 960 + respuesta de
  Ollama al tope duro de 480 + Fórmula/Fuente + disclaimer): **2065 caracteres en el peor caso**
  — muy por debajo de 4096, margen de más de 2000 caracteres (50%).

**Conclusión**: ambos casos entran cómodos en 1 solo mensaje de Telegram, sin necesidad de
`chunk_for_telegram`. La versión corta anidada no es un ajuste al límite — tiene margen holgado
— pero es la única opción viable dado que la narración completa sí rompe el tope interno de
`_MAX_DESGLOSE_CHARS = 1200` (no el de Telegram, un tope propio ya existente y no negociable en
esta spec, ver Restricciones).

**No se toca `_MAX_DESGLOSE_CHARS = 1200` ni `_MAX_CUENTA_CHARS = 400`** — el primero porque la
versión corta ya entra con margen; el segundo porque solo Graham/DCF necesitan más espacio en el
botón individual, y para eso se creó el tope dedicado `_MAX_CUENTA_NARRADA_CHARS` (Decisión #5),
sin tocar el tope general que protege a las 8 preguntas restantes.

---

## Decisiones abiertas para Daniela

Ninguna — el contenido de las narraciones (texto libre pedido explícitamente a este arquitecto),
el criterio de reemplazo (no agregar), y la versión corta anidada con su presupuesto medido son
decisiones de diseño ya resueltas arriba con justificación. Si Daniela quiere ajustar la redacción
exacta de algún paso (ej. otro verbo, otra analogía), es un ajuste de texto sobre esta spec, no
una decisión de arquitectura pendiente.

---

## Criterios de aceptación

- [ ] `_cuenta_gra(datos)` devuelve la narración completa de 3 pasos (Decisión #2) en vez de la
      fórmula comprimida — mismas 4 guardas de entrada (`eps_ttm`, `g_aplicado`, `y_value`,
      `_valor_escenario_elegido(datos)`, `y == 0`), mismo `None` cuando falta cualquiera.
- [ ] `_cuenta_dcf(datos)` devuelve la narración completa de 4 pasos (Decisión #3, formato
      aprobado por Daniela) en vez de la fórmula comprimida — mismas 7 guardas de entrada, mismo
      `None` cuando falta cualquiera. `fcf_year5` deja de calcularse (no se usa en la narración
      aprobada).
- [ ] `_cuenta_gra_corta(datos)` y `_cuenta_dcf_corta(datos)` existen, con las mismas guardas que
      sus versiones completas, y devuelven la narración corta con flechas (Decisión #4).
- [ ] `_VF_SUB_MODELO_CUENTA = {"mul": _cuenta_mul, "gra": _cuenta_gra_corta, "dcf":
      _cuenta_dcf_corta}` existe; `_build_desglose_vf` usa este dict (no `_CUENTA_TEXTO_LIBRE`)
      para armar las 3 sub-cuentas.
- [ ] `_CUENTA_TEXTO_LIBRE["gra"]`/`["dcf"]` siguen apuntando a `_cuenta_gra`/`_cuenta_dcf`
      (las versiones narradas completas) — usadas por `_build_cuenta_line` para el botón
      individual, sin cambios de dispatch.
- [ ] `_enforce_cuenta_length` acepta `max_chars` (default `_MAX_CUENTA_CHARS = 400`, sin cambiar
      el comportamiento para las 8 preguntas que no son `gra`/`dcf`); `_build_cuenta_line` pasa
      `_MAX_CUENTA_NARRADA_CHARS = 800` cuando `question_code in ("gra", "dcf")`.
- [ ] Botón individual "Graham" → "🎓 Explicame paso a paso": la sección `🧮 Cuenta:` muestra la
      narración completa de 3 pasos con los números reales del ticker, no la fórmula comprimida.
- [ ] Botón individual "DCF" → "🎓 Explicame paso a paso": la sección `🧮 Cuenta:` muestra la
      narración completa de 4 pasos con los números reales del ticker, no la fórmula comprimida.
- [ ] "💰 Valor Justo Total" → "🎓 Explicame paso a paso" → "🔍 Desglose": las sub-secciones
      Graham y DCF muestran la narración CORTA (con flechas), nunca la fórmula comprimida de hoy
      ni la narración completa del botón individual.
- [ ] "💰 Valor Justo Total" → sub-sección Múltiplos: sin cambios (`_cuenta_mul`, fórmula
      `EPS × PER = valor`).
- [ ] Para un ticker donde Graham o DCF no son calculables (cualquiera de los campos de entrada en
      `None`), la Cuenta narrada (completa o corta) se omite exactamente igual que hoy se omite
      la fórmula comprimida — nunca un paso en blanco, nunca "None" visible.
- [ ] Test de longitud, peor caso de montos (valores del script de la Decisión #6, o equivalente):
      Graham narrada completa ≤ 800 caracteres, DCF narrada completa ≤ 800 caracteres, bloque
      "🔍 Desglose:" completo de "vf" con las 2 narraciones cortas ≤ 1200 caracteres. Los 3 tests
      deben fallar explícitamente (no solo advertir) si algún futuro cambio de redacción rompe el
      presupuesto.
- [ ] `📐 Fórmula:`/`📊 Fuente del dato:` de `gra`/`dcf` (en `ai_explain_content.py`) — sin
      cambios, siguen mostrando la fórmula abstracta y la fuente de los datos, en su sección
      propia después de la Cuenta.
- [ ] `DESGLOSE_TEXTO_LIBRE["gra"]`/`["dcf"]` (el "🔍 Desglose" del botón individual, letra por
      letra EPS/g/Y y FCF base/WACC/g/VP/VT/Valor de la empresa) — sin cambios.
- [ ] Ningún cálculo de `valuation.py` cambia — `calculate_graham_fair_value`,
      `calculate_dcf_fair_value`, `_cap_graham_g`, `compute_valuation`,
      `compute_valuation_scenarios` idénticos byte a byte.
- [ ] Suite completa de tests existente sigue en verde, salvo los tests que hoy fijan literalmente
      el string de la fórmula comprimida de `_cuenta_gra`/`_cuenta_dcf` (deben actualizarse para
      fijar la narración nueva — regresión esperada y deliberada, no accidental).
- [ ] **[agregado por `qa`]** Para el mismo `datos` de entrada, el valor final citado por
      `_cuenta_gra_corta`/`_cuenta_dcf_corta` (`{_money(valor)}`/`{_money(valor_accion)}`) es
      idéntico, carácter a carácter, al valor final citado por `_cuenta_gra`/`_cuenta_dcf` — la
      versión corta resume los pasos pero nunca diverge en el resultado numérico mostrado al
      usuario entre el botón individual y "💰 Valor Justo Total".

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain.py`:
  - Reescribir el cuerpo de `_cuenta_gra` (línea ~1232) y `_cuenta_dcf` (línea ~1252) con las
    narraciones completas (Decisiones #2/#3).
  - Agregar `_cuenta_gra_corta` y `_cuenta_dcf_corta`, junto a `_cuenta_gra`/`_cuenta_dcf`
    (Decisión #4).
  - Agregar `_VF_SUB_MODELO_CUENTA` (dict, junto a `_VF_SUB_MODELO_CODE`, línea ~1147) y usarlo en
    `_build_desglose_vf` (línea ~1186) en vez de `_CUENTA_TEXTO_LIBRE`.
  - Agregar `_MAX_CUENTA_NARRADA_CHARS = 800` (junto a `_MAX_CUENTA_CHARS`, línea ~785).
  - `_enforce_cuenta_length` (línea ~800): agregar parámetro `max_chars` con default
    `_MAX_CUENTA_CHARS` (retrocompatible).
  - `_build_cuenta_line` (línea ~1601): pasar `_MAX_CUENTA_NARRADA_CHARS` para `gra`/`dcf`,
    `_MAX_CUENTA_CHARS` para el resto.
- `tests/test_ai_explain.py`:
  - Actualizar los tests existentes que fijan el string literal de la fórmula comprimida de
    `_cuenta_gra`/`_cuenta_dcf` para fijar la narración nueva.
  - Tests nuevos para `_cuenta_gra_corta`/`_cuenta_dcf_corta` (caso calculable, caso no
    calculable).
  - Test nuevo: `_build_desglose_vf` usa las versiones cortas para Graham/DCF (no las completas) —
    verificar que la substring de la narración completa NO aparece en el bloque de "vf" y que sí
    aparece la substring distintiva de la versión corta (ej. `"→"`, o el patrón `"EPS ... → se
    proyecta"`).
  - Test de longitud, peor caso de montos (ver criterio de aceptación de longitud arriba).
  - Test de que el botón individual de Graham/DCF sigue omitiendo la Cuenta cuando falta algún
    campo (mismo comportamiento, verificado sobre la narración nueva).

## Restricciones

- No se toca `valuation.py` — ninguna fórmula, ninguna constante de cálculo
  (`GRAHAM_HISTORICAL_YIELD`, `GRAHAM_G_CAP`, `DCF_PROJECTION_YEARS`, `TERMINAL_GROWTH_RATE`,
  `calculate_graham_fair_value`, `calculate_dcf_fair_value`, `_cap_graham_g`, `compute_valuation`,
  `compute_valuation_scenarios`). Esta spec es puramente de presentación/redacción del mismo
  cálculo ya validado.
- No se tocan `FORMULAS_TEXTO_LIBRE`/`FUENTES_TEXTO_LIBRE`/`DESGLOSE_TEXTO_LIBRE` de
  `ai_explain_content.py` para `gra`/`dcf` — siguen siendo secciones separadas (`📐 Fórmula`,
  `📊 Fuente del dato`, `🔍 Desglose` del botón individual), sin cambios.
- No se toca `_cuenta_mul` ni ninguna otra función de `_CUENTA_TEXTO_LIBRE` (`_cuenta_ver`,
  `_cuenta_vf`, `_cuenta_rat`, `_cuenta_pil`, `_cuenta_rsk`, `_cuenta_mom`, `_cuenta_cmp`) — fuera
  de alcance, sin queja reportada.
- No se toca `_build_leaf_message`, `_build_ver_dato_content`, `_build_cuenta_line` (salvo el
  único `if` de tope de longitud descrito en la Decisión #5), `_build_desglose_block`,
  `_enforce_desglose_length`, `_MAX_DESGLOSE_CHARS`, ni el call site de `handle_explain`.
- No se agrega botón, callback ni pantalla nueva — mismos botones ya existentes para Graham/DCF y
  para "💰 Valor Justo Total".
- Si en el futuro `GRAHAM_G_CAP` cambia de valor en `valuation.py`, el texto fijo "un techo del
  15%" en `_cuenta_gra`/`_cuenta_gra_corta` debe actualizarse junto con ese cambio — señalado
  explícitamente porque el texto no lo referencia dinámicamente (ver justificación en Decisión
  #2).

---

## Handoff → security

### Specs producidas

- `contexto/specs/abiertas/SDD_cuenta_narrada_graham_dcf.md` (este documento).

### Criterios de aceptación base

Ver sección "Criterios de aceptación" arriba — cubren: reemplazo de contenido sin cambio de
mecanismo, guardas de entrada sin cambios, presupuesto de longitud medido en 2 lugares distintos
(botón individual con tope nuevo dedicado, versión anidada con el tope ya existente), y
no-regresión sobre `valuation.py`/Fórmula/Fuente/Desglose del botón individual.

### Decisiones de diseño tomadas [para que `implementer` no las reabra]

1. Se reemplaza la Cuenta, no se agrega una sección nueva (Decisión #1).
2. Narración de Graham: 3 pasos (EPS → crecimiento con techo del 15% → multiplicador ajustado por
   tasa libre de riesgo), redactada por este arquitecto según pidió Daniela (Decisión #2).
3. Narración de DCF: 4 pasos, texto ya aprobado explícitamente por Daniela (Decisión #3).
4. La versión anidada en "vf" usa una redacción CORTA dedicada (con flechas `→`), nunca la
   narración completa ni la fórmula comprimida — necesario porque la narración completa hace que
   el bloque "🔍 Desglose:" de "vf" exceda `_MAX_DESGLOSE_CHARS = 1200` en el peor caso de montos
   (medido: 1701 vs. 1200) (Decisión #4).
5. Nuevo tope `_MAX_CUENTA_NARRADA_CHARS = 800`, aplicado solo a `gra`/`dcf` en el botón
   individual — necesario porque la narración completa (hasta 637/532 caracteres en el peor caso)
   excede el tope general de 400 (Decisión #5).
6. Presupuesto de longitud medido con Python real sobre el formato exacto, peor caso de montos,
   en 2 mensajes distintos (botón individual, "vf" completo) — ambos con margen amplio respecto a
   `TELEGRAM_MESSAGE_LIMIT = 4096` (Decisión #6). El punto de riesgo real no era Telegram, era el
   tope interno `_MAX_DESGLOSE_CHARS`, ya resuelto con la versión corta.

### Seguridad — puntos a revisar explícitamente

- Confirmar que las narraciones (completa y corta) siguen sin pasar nunca por Ollama ni por el
  guard de integridad (`ai_rewrite.protected_tokens`) como input — se arman 100% en Python, se
  insertan en el mensaje final antes de la respuesta de Ollama (mismo mecanismo ya validado en
  specs anteriores, sin cambios de orden).
- Confirmar que ningún dato nuevo se expone — las narraciones leen exactamente los mismos campos
  que ya leía la fórmula comprimida (`eps_ttm`, `g_aplicado`, `y_value`, `dcf_wacc`, `dcf_g_fcf`,
  `dcf_fcf_base`, `dcf_valor_presente_flujos`, `dcf_valor_terminal_descontado`,
  `dcf_equity_value`, `escenario_elegido`/valor del escenario) — cero campos nuevos en
  `ExplanationContext`, `_payload_texto_libre`, o `datos_del_contexto` que ve Ollama.
- Confirmar el tope de longitud nuevo (`_MAX_CUENTA_NARRADA_CHARS = 800`) no abre una superficie
  de mensajes desproporcionadamente largos — medido: peor caso real 637/532, margen de solo
  ~25%, no hay vector de entrada controlable por el usuario que infle estos números más allá de
  los datos financieros reales del ticker (vienen de FMP/FRED/Treasury.gov, no de input de
  usuario).

---

## Revisión de seguridad

**Modo:** Secure Code Review (bajo riesgo, feature chica — mismo patrón ya revisado varias veces
hoy: texto fijo + valores ya calculados, nunca generado por Ollama). Código real releído línea
por línea en `ai_explain.py` (no solo la spec) antes de concluir. **Sin hallazgos.**

1. **Texto fijo + valores ya calculados, nunca Ollama** — confirmado en el código actual: `_cuenta_gra`
   (línea 1232-1241) y `_cuenta_dcf` (línea 1252-1269) son funciones puras `dict -> Optional[str]`,
   sin I/O, que solo leen campos numéricos de `datos` y arman el string con `_money()`/f-strings
   (formateo numérico puro, sin interpolación de texto libre de terceros). El call site del botón
   individual (`_build_cuenta_line`, línea 1601-1618) y el de "vf" (`_build_desglose_vf`, línea
   1164-1198) insertan el resultado en el mensaje final ANTES de la respuesta de Ollama en el
   armado del texto, pero — confirmado leyendo `_build_leaf_message` (línea 719-754) — la Cuenta
   nunca se agrega al payload que `_fetch_explanation`/Ollama reciben como input; es texto que se
   concatena al mensaje de salida, mismo orden y mecanismo ya auditado para Altman/Piotroski/Magic
   Formula/Desglose de "vf". Las 2 funciones nuevas propuestas (`_cuenta_gra_corta`/
   `_cuenta_dcf_corta`) siguen exactamente el mismo patrón: mismas guardas de entrada, mismo tipo
   de retorno, cero llamada a red o a Ollama.

2. **Tope nuevo de 800 caracteres (Graham/DCF) — mecanismo de "omitir bloque completo" intacto** —
   confirmado en el código actual: `_enforce_cuenta_length` (línea 800-814) no trunca nunca — solo
   compara `len(cuenta) > _MAX_CUENTA_CHARS` y devuelve `None` (bloque completo omitido) o el
   string íntegro sin modificar. La spec propone parametrizar `max_chars` con default
   `_MAX_CUENTA_CHARS = 400` (retrocompatible) y que `_build_cuenta_line` pase
   `_MAX_CUENTA_NARRADA_CHARS = 800` solo cuando `question_code in ("gra", "dcf")` — la lógica de
   comparación y corte no cambia, solo el número contra el que se compara para 2 de 10 preguntas.
   No hay ruta nueva de truncamiento a mitad de frase ni a mitad de número.

3. **Cero regresión sobre el tope de 1200 del Desglose ni sobre las demás preguntas** — confirmado
   por lectura directa: `_build_desglose_vf` (línea 1164-1198), tal como está hoy, ya NO llama a
   `_enforce_cuenta_length` sobre cada sub-cuenta individualmente — la spec preserva ese
   comportamiento (solo cambia de qué dict saca la función: `_VF_SUB_MODELO_CUENTA` en vez de
   `_CUENTA_TEXTO_LIBRE`, únicamente para `gra`/`dcf`, reutilizando `_cuenta_mul` sin tocar). El
   único guard sobre ese bloque sigue siendo `_enforce_desglose_length`/`_MAX_DESGLOSE_CHARS = 1200`
   (línea 823 y siguientes), que la spec no modifica. Las 8 preguntas restantes de
   `_CUENTA_TEXTO_LIBRE` y las de `_CUENTA_AVANZADO` (línea ~1596-1600) siguen pasando por
   `_enforce_cuenta_length` con el default `_MAX_CUENTA_CHARS = 400` sin cambios — el `if` nuevo en
   `_build_cuenta_line` es estrictamente aditivo para 2 códigos, no altera la rama que ejecutan los
   otros 20.

4. **Dato faltante — nunca se inventa ni se finge un paso** — confirmado que el diseño sigue el
   patrón "todo o nada" ya establecido en el proyecto para la Cuenta del botón individual:
   `_cuenta_gra`/`_cuenta_dcf` (y sus versiones cortas/narradas) devuelven `None` si falta
   cualquiera de sus campos de entrada, y `_build_leaf_message` (línea 741-742) omite la línea
   `🧮 Cuenta:` por completo cuando es `None` — nunca arma una narración con un paso en blanco,
   con `"None"` visible, o con un dato fabricado. Esto cumple el objetivo de seguridad (nunca
   inventar/fingir un valor), pero es importante precisar frente al punto 4 pedido para esta
   auditoría: el mecanismo NO redacta la frase "no disponible" dentro de un paso de la narración
   completa del botón individual — omite el bloque `🧮 Cuenta:` entero, igual que hace hoy con la
   fórmula comprimida. La única ruta del código que sí usa una frase explícita de "no calculable"
   por componente es la nested de "vf" (`_build_desglose_vf`, línea ~1188: `f"• {t.nombre} — no
   calculable con los datos disponibles."`), que ya existe y la spec no cambia. **No es una
   vulnerabilidad** — omitir el bloque completo es al menos tan seguro como mostrar "no
   disponible" (ninguna ruta arma un paso con dato ausente o inventado) — pero se marca como
   punto a confirmar con Daniela si el criterio de aceptación #5 de "Estado objetivo" (omitir todo
   el bloque) es lo que ella espera, o si prefiere que la narración del botón individual muestre
   los pasos disponibles con un paso explícito en "no disponible" en vez de desaparecer el bloque
   entero. Es una decisión de producto/UX, no de seguridad — no bloquea el pipeline.

**Conclusión:** cambio puramente de redacción (contenido de 2 funciones existentes + 2 nuevas con
el mismo contrato), sin superficie de ataque nueva, sin dato nuevo expuesto a Ollama, sin cambio
en el mecanismo de truncamiento, sin regresión sobre los topes ya existentes. Aprobado para pasar
a `qa`/`implementer` sin cambios a los criterios de aceptación. Único punto NO bloqueante:
aclarar con Daniela si "omitir el bloque completo" (ya documentado en la spec) es el
comportamiento deseado frente a "decir 'no disponible' paso por paso", dado que la fase de
seguridad de este pipeline lo recibió como criterio a confirmar.

---

## Criterios QA para Spec: Cuenta narrada Graham/DCF [Iter-1]

### Tipo de prueba principal

**Unit testing.** Las 4 funciones tocadas/creadas (`_cuenta_gra`, `_cuenta_dcf`,
`_cuenta_gra_corta`, `_cuenta_dcf_corta`) son puras `dict -> Optional[str]`, sin I/O, sin
dependencias externas — el patrón exacto para el que unit testing da la señal más rápida y estable
(confirmado por `security`: "sin llamada a red o a Ollama"). Se complementa con un puñado de casos
de **integration testing acotado** (no un tipo de prueba distinto, sino el mismo archivo
`tests/test_ai_explain.py`) para 2 puntos que no son observables solo con unit tests:
1. Que `_build_cuenta_line` aplique el tope correcto (400 vs. 800) según `question_code` — depende
   de la función que orquesta, no solo de `_enforce_cuenta_length` aislada.
2. Que `_build_desglose_vf` arme el bloque completo de "vf" (Múltiplos + Graham corta + DCF corta)
   por debajo de 1200 — depende del ensamblado conjunto de 3 sub-cuentas + las 3 líneas de "qué
   mide" + el separador, no de cada sub-cuenta por separado.

No se justifica E2E dedicado para esta spec — no hay flujo de usuario nuevo (mismos botones, mismo
mecanismo de mensaje), y el pipeline ya tiene cobertura E2E de "Graham"/"DCF"/"Valor Justo Total"
de specs anteriores que no cambian de forma.

### Cobertura mínima requerida

- [ ] Code coverage ≥ 90% en las 4 funciones de narración (`_cuenta_gra`, `_cuenta_dcf`,
      `_cuenta_gra_corta`, `_cuenta_dcf_corta`) — lógica de negocio de cara al usuario, no
      cosmética (riesgo Alto según la tabla de riesgo del skill: "flujos principales de usuario").
- [ ] Branch coverage 100% en las guardas de entrada de las 4 funciones (cada campo en `None` por
      separado, más `y == 0` para Graham) — es lógica condicional que decide "mostrar vs. omitir
      un bloque completo al usuario", clasificable como crítica de negocio.
- [ ] Branch coverage 100% en `_enforce_cuenta_length` con `max_chars` parametrizado — las 2 ramas
      (excede / no excede) para ambos valores de tope (400 y 800).
- [ ] Todos los criterios de aceptación del `architect` (incluida la línea agregada por `qa` sobre
      consistencia corta/completa) cubiertos por al menos un test.

### Casos obligatorios

- [ ] **Happy path Graham (caso real Daniela)**: `eps_ttm=16.70, g_aplicado=0.134, y_value=0.044`,
      escenario elegido con valor `493.44` → `_cuenta_gra` devuelve exactamente el texto de 3 pasos
      de la Decisión #2, con `$16.70`, `13.4%`, `4.4%`, `$493.44` interpolados; `len(resultado) ==
      627`.
- [ ] **Happy path DCF (caso real Daniela)**: `dcf_wacc=0.124, dcf_g_fcf=0.093,
      dcf_fcf_base=9852000000.00, dcf_valor_presente_flujos=45360690094.48,
      dcf_valor_terminal_descontado=88645529833.61, dcf_equity_value=134006219928.09`, valor por
      acción `313.83` → `_cuenta_dcf` devuelve exactamente el texto de 4 pasos de la Decisión #3;
      `len(resultado) == 521`. Verificar explícitamente que el string NO contiene ninguna mención a
      un "FCF proyectado año 5" (regresión del cálculo retirado `fcf_year5`).
- [ ] **Caso límite — peor caso de montos**: `eps_ttm=999.99, g_aplicado=0.15 (techo),
      y_value=0.999` para Graham y `dcf_wacc=0.999, dcf_g_fcf=0.999,
      dcf_fcf_base=999999999999.99` para DCF (valores de la Decisión #6) → `len(_cuenta_gra(...)) ==
      637`, `len(_cuenta_dcf(...)) == 532`, ambos `<= _MAX_CUENTA_NARRADA_CHARS = 800` **después**
      de pasar por `_enforce_cuenta_length` (no solo antes) — el test debe llamar al pipeline
      completo (`_build_cuenta_line`), no solo a `_cuenta_gra`/`_cuenta_dcf` en aislamiento, para
      no dar falso verde si alguien conecta el tope equivocado.
- [ ] **Caso límite — bloque "vf" completo, peor caso de montos**: `_build_desglose_vf` con los 3
      sub-modelos en peor caso → `len(bloque "🔍 Desglose:")` ≤ 1200 usando las versiones cortas;
      test explícito adicional que arme el mismo bloque forzando (vía monkeypatch o llamada
      directa) las versiones **completas** en el dict de "vf" y confirme que en ese escenario
      **excede** 1200 — no como criterio de aceptación del producto, sino como test de regresión
      que documenta *por qué* existe la versión corta (si en el futuro alguien revierte
      `_VF_SUB_MODELO_CUENTA` a `_CUENTA_TEXTO_LIBRE`, este test debe fallar).
- [ ] **Caso de error — dato faltante, Graham**: 5 sub-casos, uno por cada guarda
      (`eps_ttm=None`, `g_aplicado=None`, `y_value=None`, valor de escenario `None`, `y_value=0`) →
      `_cuenta_gra` y `_cuenta_gra_corta` devuelven `None` en los 5; `_build_cuenta_line` no agrega
      la línea `🧮 Cuenta:` al mensaje final (verificado con `_build_leaf_message` o el mock
      equivalente, no solo con la función aislada).
- [ ] **Caso de error — dato faltante, DCF**: 7 sub-casos, uno por cada guarda (`dcf_wacc`,
      `dcf_g_fcf`, `dcf_fcf_base`, `dcf_valor_presente_flujos`, `dcf_valor_terminal_descontado`,
      `dcf_equity_value`, valor de escenario, cada uno `None` por separado) → mismo comportamiento
      que Graham.
- [ ] **Caso de error — dato faltante dentro de "vf"**: un sub-modelo (ej. DCF) sin datos
      calculables mientras Múltiplos y Graham sí lo son → `_build_desglose_vf` sigue mostrando la
      línea existente `"• {nombre} — no calculable con los datos disponibles."` para DCF (mecanismo
      ya existente, sin cambios, según señaló `security` en el punto 4) y las narraciones cortas de
      los otros 2 sub-modelos se muestran con normalidad — este caso no está en los criterios de
      aceptación del `architect` de forma explícita y se agrega aquí porque es el único punto donde
      "omitir todo" (botón individual) y "decir no calculable" (vf) conviven en la misma spec; si
      no se prueba, un bug ahí pasaría desapercibido.
- [ ] **Caso de alto riesgo de negocio — consistencia corta/completa**: con el mismo `datos` de
      Graham y de DCF (caso real Daniela), extraer el valor final formateado de
      `_cuenta_gra(datos)` y de `_cuenta_gra_corta(datos)` (idem DCF) y assertar que el string
      `_money(valor)` que aparece al final de ambos es idéntico — cubre el criterio de aceptación
      agregado por `qa` arriba. Este es el caso de mayor riesgo de negocio de toda la spec: si
      corta y completa mostraran números distintos para el mismo ticker, el usuario vería
      inconsistencia entre el botón individual y "Valor Justo Total" sin ningún error técnico que
      lo delate.
- [ ] **Regresión — 8 preguntas restantes de `_CUENTA_TEXTO_LIBRE`**: para al menos 2 de las 8
      preguntas no tocadas (`mul` y una más, ej. `rat`), confirmar que `_build_cuenta_line` sigue
      usando `_MAX_CUENTA_CHARS = 400` (no 800) — un test dedicado con un caso sintético que exceda
      400 pero no 800 caracteres para esas preguntas debe seguir devolviendo `None` (bloque
      omitido). Si este test no existiera, un error de `question_code in ("gra", "dcf")` mal escrito
      (ej. `not in`) pasaría silencioso.
- [ ] **Regresión — Fórmula/Fuente/Desglose del botón individual sin cambios**: snapshot o
      comparación directa de `FORMULAS_TEXTO_LIBRE["gra"]`/`["dcf"]`,
      `FUENTES_TEXTO_LIBRE["gra"]`/`["dcf"]` y `DESGLOSE_TEXTO_LIBRE["gra"]`/`["dcf"]` contra su
      valor actual (antes de esta spec) — bytes idénticos.
- [ ] **Regresión — `valuation.py` sin cambios**: test de que `calculate_graham_fair_value`,
      `calculate_dcf_fair_value`, `_cap_graham_g`, `compute_valuation`,
      `compute_valuation_scenarios` no fueron tocados — puede ser tan simple como un test existente
      que ya cubre estos cálculos y que debe seguir en verde sin modificación de sus asserts
      numéricos (si algún assert numérico de `valuation.py` necesitó cambiar para que la suite
      pase, es señal de que se tocó lo prohibido — debe escalarse, no ajustarse el test).

### Testabilidad

- [ ] Las 4 funciones de narración son funciones puras (`dict -> Optional[str]`), sin necesidad de
      mocks — confirmado por `security` (punto 1 de la Revisión de seguridad).
- [ ] `_enforce_cuenta_length(cuenta, max_chars=...)` es invocable de forma aislada con cualquier
      `max_chars`, sin depender de `_build_cuenta_line` — permite testear las 2 ramas sin construir
      un mensaje completo.
- [ ] `_build_desglose_vf` es invocable con un `datos_vf` de test controlado (mismo mecanismo que
      ya usan las specs de Desglose anteriores) — no requiere levantar Telegram ni Ollama.
- [ ] No hay lógica nueva en constructores ni en métodos estáticos no testeables — las 2 funciones
      nuevas (`_cuenta_gra_corta`/`_cuenta_dcf_corta`) siguen el mismo patrón de función de módulo
      que las existentes.

### Criterio de exit de QA

- Todos los tests pasan (BUILD SUCCESS / suite verde), incluida la suite completa preexistente de
  `tests/test_ai_explain.py` (salvo los tests que fijaban literalmente el string de la fórmula
  comprimida, actualizados deliberadamente — ver criterio de aceptación del `architect`).
- Sin tests ignorados, comentados o marcados `xfail` para pasar CI.
- Flaky rate = 0 en la nueva suite — las funciones son deterministas (sin fechas, sin aleatoriedad,
  sin orden de diccionario relevante), no debería haber flakiness posible; si aparece, es señal de
  un bug real (ej. dependencia de orden de ejecución entre tests que comparten estado mutable), no
  de un test "naturalmente inestable".
- Los 2 valores de longitud medidos por el `architect` (637/532 para narración completa, 960 para
  el bloque "vf" corto) están fijados como asserts exactos en al menos un test cada uno — no solo
  como "≤ 800"/"≤ 1200" — para que cualquier cambio futuro de redacción que mueva esos números sea
  visible en el diff del test, no solo silenciosamente absorbido por el margen.

### Fixtures mínimos que faltan (a crear en `tests/test_ai_explain.py` o `conftest.py`)

- `datos_graham_caso_real` — el caso real de Daniela (Decisión #2), reutilizable entre el test de
  contenido exacto y el test de consistencia corta/completa.
- `datos_graham_peor_caso` — `eps_ttm=999.99, g_aplicado=0.15, y_value=0.999`, escenario con el
  valor resultante que da `calculate_graham_fair_value` para esos insumos (no inventar el valor —
  calcularlo con la función real de `valuation.py`, igual que hizo el `architect` para medir 637
  caracteres).
- `datos_dcf_caso_real` — el caso real de Daniela (Decisión #3).
- `datos_dcf_peor_caso` — `dcf_wacc=0.999, dcf_g_fcf=0.999, dcf_fcf_base=999999999999.99` y el
  resto de campos derivados con la función real de `valuation.py`.
- `datos_graham_incompleto(campo_faltante)` / `datos_dcf_incompleto(campo_faltante)` — fixtures
  parametrizados (uno por cada uno de los 4/7 campos) para los casos de error, evitando 12 fixtures
  sueltos casi idénticos.
- `datos_vf_peor_caso_3_submodelos` — combina los 3 peores casos (Múltiplos + Graham + DCF) en el
  formato que espera `_build_desglose_vf`, para el test del bloque "vf" completo ≤ 1200.
- `datos_vf_un_submodelo_faltante` — igual al anterior pero con DCF forzado a incalculable (un
  campo en `None`), para el caso de error dentro de "vf".

### Qué NO se prueba en este ciclo QA, y por qué

- **No se prueba en Telegram real / bot corriendo** — verificación manual del renderizado final
  (emoji, saltos de línea, cómo se ve en el cliente de Telegram) queda fuera del alcance de la
  suite automatizada; es exploratorio, de bajo riesgo (texto plano, sin Markdown nuevo, sin
  parsing especial) y no repetible de forma barata — según la Regla 80/20 del skill, no se
  automatiza. Se recomienda una verificación manual puntual post-deploy (1 ticker con montos
  normales, no peor caso) antes de cerrar el pipeline, pero no es un criterio QA bloqueante.
- **No se prueba la calidad subjetiva de la redacción** (si la analogía "explicación de la tasa
  libre de riesgo" es la más clara posible, si otro verbo comunicaría mejor) — es una decisión de
  producto/UX del `architect`, ya aprobada por Daniela para DCF y propuesta para Graham; QA fija el
  string exacto acordado como test de regresión, no evalúa su claridad.
- **No se prueba `valuation.py`** más allá del test de no-regresión de que sigue intacto — sus
  cálculos ya están validados por specs anteriores y la Restricción de esta spec prohíbe tocarlos;
  re-testear su lógica interna sería trabajo duplicado.
- **No se prueba rendimiento/carga** — las 4 funciones son O(1), sin I/O; no hay escenario de carga
  que aplique (riesgo Bajo, no automatizable con beneficio real).
- **No se prueba el mecanismo de Ollama, el guard de integridad (`ai_rewrite.protected_tokens`) ni
  la respuesta del LLM** — confirmado por `security` que la Cuenta nunca entra al payload de
  Ollama; verificar eso es responsabilidad de la Revisión de seguridad ya hecha, no de QA funcional
  de esta spec.
- **No se prueba el punto abierto que dejó `security`** (si "omitir el bloque completo" es el
  comportamiento deseado por Daniela para el botón individual, frente a "decir 'no disponible' paso
  a paso") — es una decisión de producto pendiente de confirmación con Daniela, no un defecto; QA
  prueba el comportamiento tal como está especificado hoy (omitir el bloque completo), y si Daniela
  pide el otro comportamiento, es un cambio de spec (nuevo Iter), no un fix de QA.
