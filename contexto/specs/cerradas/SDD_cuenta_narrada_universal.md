# Spec: Cuenta narrada paso a paso — universal (15 preguntas restantes) + dato faltante por paso

**Rol:** `architect` (spec base — extiende `SDD_cuenta_narrada_graham_dcf.md`, cerrada e
implementada hoy, a las 15 preguntas restantes con Cuenta comprimida, y rediseña el mecanismo de
"dato faltante" en toda la familia de Cuentas narradas).
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `security` revisa (mismo guard de integridad — cero cambios de mecanismo de
"nunca antes de Ollama"; el punto nuevo a revisar es que ahora la Cuenta puede exponer strings de
texto fijo como `"no disponible"`/`"no se puede calcular sin el dato faltante"` en vez de omitir el
bloque — confirmar que eso no abre ninguna superficie nueva). `qa` agrega criterios de cobertura
para las 15 preguntas + el retrofit de Graham/DCF. `dba`/`frontend`/`backend` no aplican.
**Estado:** spec nueva, lista para `security` → `qa` → `implementer`.

---

## Contexto

Código real releído línea por línea hoy (no se asumió nada del historial de specs anteriores):

- **`src/investbot/ai_explain.py`** (2432 líneas) — confirmado el estado de las 22 funciones
  `_cuenta_*` (`dato_y_paso_a_paso`), `_CUENTA_TEXTO_LIBRE` (línea 1446-1450),
  `_CUENTA_AVANZADO` (línea 1669-1674), `_build_cuenta_line` (línea 1677-1695),
  `_enforce_cuenta_length` (línea 808-825), `_MAX_CUENTA_CHARS=400` (línea 785),
  `_MAX_CUENTA_NARRADA_CHARS=800` (línea 793, agregada hoy solo para `gra`/`dcf`),
  `_build_leaf_message` (línea 719-754), `_build_desglose_vf` (línea 1175-1209),
  `_VF_SUB_MODELO_CUENTA` (línea 1443).
- **`src/investbot/ai_explain_content.py`** (1018 líneas) — confirmado `DESGLOSE_AVANZADO`/
  `DESGLOSE_TEXTO_LIBRE` ya tienen las 27−15=12 preguntas de `SDD_desglose_universal.md` (cerrada)
  con el texto de "qué mide" de cada término — reutilizado tal cual en las narraciones nuevas de
  esta spec, sin reescribir esas frases.
- **`src/investbot/valuation.py`**, **`advanced_scoring.py`**, **`risk_fit.py`**,
  **`market_context.py`** — releídos para confirmar campos/constantes citadas en cada narración
  (`GRAHAM_G_CAP`, `DCF_PROJECTION_YEARS`, `FACTOR_UMBRALES`, umbrales de Beta). Ninguna fórmula se
  toca (ver Restricciones).

### Alcance confirmado contra el código real — las 15 preguntas y su estado hoy

| Pregunta | `kind` | Cuenta hoy (línea) | Forma actual |
|---|---|---|---|
| `mul` | texto_libre | `_cuenta_mul` (1283) | Fórmula comprimida 1 línea: `EPS × PER = valor` |
| `rat` | texto_libre | `_cuenta_rat` (1338) | 4 piezas independientes unidas con `·`, ya con omisión individual por pieza (pero silenciosa, sin decir "no disponible") |
| `pil` | texto_libre | `_cuenta_pil` (1360) | 1 bloque, `None` si falta cualquiera de los 5 campos (todo o nada) |
| `rsk` | texto_libre | `_cuenta_rsk`/`_cuenta_beta_bucket` (1380/1392) | 1 línea de comparación |
| `mom` | texto_libre | `_cuenta_mom` (1406) | 4 piezas independientes, mismo patrón que `rat` (omisión silenciosa por pieza) |
| `cmp` | texto_libre | `_cuenta_cmp` (1424) | 2 piezas, `None` si falta PER propio |
| `alz` | avanzado | `_cuenta_alz` (1453) | Fórmula comprimida, 5 términos sumados, todo o nada |
| `azp` | avanzado | `_cuenta_azp` (1467) | Fórmula comprimida, 4 términos sumados, todo o nada |
| `pig` | avanzado | `_cuenta_pig` (1481) | 1 línea resumen ("N de M criterios cumplidos") |
| `pir`/`pia`/`pie` | avanzado | `_cuenta_piotroski_grupo` (1556) vía `_fmt_criterio_piotroski` (1502) | **Ya narrado por criterio** (ej. `"Ganancia Neta: $X > 0 → cumplido"`), pero un criterio con datos faltantes se **descarta silenciosamente** de la lista (`if p` en el list comprehension, línea 1558) en vez de decir "no disponible" |
| `mgr` | avanzado | `_cuenta_mgr` (1562) | Fórmula comprimida, todo o nada |
| `mge` | avanzado | `_cuenta_mge` (1571) | Fórmula comprimida, todo o nada |
| `aqv` | avanzado | `_cuenta_aqv` (1595) | 1 línea de comparación, todo o nada |
| `aqq` | avanzado | `_cuenta_aqq` (1614) | 3 sub-métricas, mismo patrón silencioso de `rat`/`mom` (`continue` en el loop, línea 1631) |
| `aqm` | avanzado | `_cuenta_aqm` (1642) | Ya narrada (`SDD_desglose_universal.md`, Grupo F) pero todo o nada |
| `aql` | avanzado | `_cuenta_aql` (1661) | 1 línea de comparación, todo o nada |

**`ver` no está en esta tabla** porque Daniela la incluyó en el pedido (`texto libre: ver, mul, rat,
pil, rsk, mom, cmp` — 7 preguntas) — confirmado: `_cuenta_ver` (línea 1212) también existe hoy como
1 línea de comparación, todo o nada. Se agrega a la tabla de arriba como la 16ª entrada tratada en
esta spec (el pedido dice 15 preguntas pero enumera 16 códigos — `ver` estaba en el texto del pedido
y se incluye).

**Confirmado — ninguna de las 16 necesita "narrar" en el sentido de inventar pasos que no existen
en el cálculo real**: cada narración de abajo sigue exactamente los mismos pasos que ya ejecuta el
código de `valuation.py`/`advanced_scoring.py`/`risk_fit.py`/`market_context.py`, solo cambia cómo
se redactan.

**Hallazgo clave sobre Piotroski (`pir`/`pia`/`pie`)** — confirmado en el código: **ya están
narradas por naturaleza**, tal como intuyó quien encargó esta spec. `_fmt_criterio_piotroski` ya
arma una comparación explícita por criterio (`"{label}: {valor} {op} {referencia} → {etiqueta}"`).
El único cambio real que necesitan es el mecanismo de "dato faltante por paso" (no descartar el
criterio en silencio) — no una redacción nueva.

**`pig` (Piotroski total) no es una fórmula matemática con términos sumados** — es un conteo de
criterios ya evaluados por `pir`/`pia`/`pie`. Se narra como "qué es y qué significa el puntaje", no
como una secuencia de pasos aritméticos (no hay término que pueda faltar de forma independiente:
`puntaje`/`criterios_evaluables` son 1 solo par de números que `advanced_scoring.py` ya calcula
completo o no calcula — confirmado en `advanced_scoring.calculate_piotroski_score`, no hay estado
intermedio parcial). Sigue con guarda todo-o-nada (`None` si falta `puntaje`/`criterios_evaluables`)
porque no hay "pasos" que puedan faltar individualmente dentro de esta pregunta puntual.

**Ninguna de las 16 preguntas de esta spec está anidada dentro de otro mensaje — con 1 excepción:
`mul`.** Confirmado grepeando `_VF_SUB_MODELO_CODE`/`_VF_SUB_MODELO_CUENTA`/`_build_desglose_vf` en
todo el archivo: el único contenedor que reutiliza Cuentas de otras preguntas es "💰 Valor Justo
Total" (`vf`), y sus 3 sub-modelos son `Múltiplos`→`mul`, `Graham`→`gra`, `DCF`→`dcf`. `gra`/`dcf`
ya tienen su versión corta dedicada (`_cuenta_gra_corta`/`_cuenta_dcf_corta`, cerradas hoy). `mul`
**no tiene versión corta** — hoy usa la misma función comprimida para ambos casos porque ya era
corta. Al narrar `mul` en el botón individual, **hace falta una versión corta nueva para el
anidado** (ver Decisión #5) — ninguna de las otras 15 preguntas de esta spec se usa dentro de "vf"
ni de ningún otro contenedor.

---

## Estado actual

Botón individual → "🎓 Explicame paso a paso" → sección `🧮 Cuenta:` de las 16 preguntas de la
tabla: fórmula comprimida en 1 línea densa, o (en el caso de Piotroski) una lista de criterios ya
narrados pero con omisión silenciosa de los que tienen datos faltantes. Si a un ticker le falta
cualquier dato necesario, el bloque `🧮 Cuenta:` **completo desaparece** del mensaje (ninguna pista
de qué faltó ni de qué sí se pudo calcular) — mismo comportamiento que tenían `gra`/`dcf` antes de
la spec de hoy, y el mismo que `_cuenta_gra`/`_cuenta_dcf` (ya narradas) siguen teniendo hoy
también: son todo-o-nada, ver Decisión #2.

## Estado objetivo

1. Las 16 preguntas de la tabla muestran, en `🧮 Cuenta:`, una **narración de los pasos del cálculo
   real con los números del ticker sustituidos** — mismo tono/estilo ya aprobado por Daniela para
   `gra`/`dcf` ("el modelo hace N pasos: 1) ..., 2) ..., ... → resultado").
2. **Ninguna Cuenta con más de 1 paso desaparece completa por 1 solo dato faltante.** Cada paso
   individual que no se puede calcular dice explícitamente que el dato no está disponible; el paso
   final (el resultado numérico) dice explícitamente que no se puede calcular **solo si depende del
   dato faltante** — nunca se inventa ni se aproxima un resultado con un hueco.
3. **Este mecanismo se aplica retroactivamente a `_cuenta_gra`/`_cuenta_dcf`** (Decisión #2,
   justificada abajo) — deja de haber 2 comportamientos distintos (unas Cuentas que desaparecen
   completas, otras que narran "no disponible" por paso) dentro de la misma familia de botones.
4. `_MAX_CUENTA_NARRADA_CHARS=800` deja de ser exclusivo de `gra`/`dcf` — se convierte en el único
   tope de longitud para las 22 preguntas `dato_y_paso_a_paso` (ver Decisión #4), porque después de
   esta spec todas (salvo `vf`, que sigue siendo una fórmula corta) son narraciones en prosa.
5. `mul` gana una versión corta (`_cuenta_mul_corta`) para seguir cumpliendo el presupuesto de
   `_MAX_DESGLOSE_CHARS=1200` dentro de "💰 Valor Justo Total" — mismo patrón que `gra`/`dcf`.
6. Ningún cálculo de `valuation.py`/`advanced_scoring.py`/`risk_fit.py`/`market_context.py` cambia
   — 100% redacción del mismo resultado ya calculado.

---

## Decisiones de diseño tomadas

### 1. Mecanismo genérico de "dato faltante por paso" — 3 reglas, aplicadas a las 17 funciones tocadas (16 nuevas + retrofit de `gra`/`dcf`)

No se crea una abstracción compartida (helper genérico que arme "pasos" a partir de una lista) —
se decidió, con criterio, que **cada función siga escribiendo su propio string** (como ya hacían
`_cuenta_gra`/`_cuenta_dcf`), porque:
- Cada narración tiene su propia cantidad de pasos (1 a 5) y su propio texto conector — una
  abstracción genérica terminaría siendo casi tan larga como el código actual, sin ganancia real de
  mantenibilidad (mismo criterio que ya aplicó `architect` en la spec de Graham/DCF: "el texto es
  redactado a mano, no generado por una plantilla, porque el tono importa más que la brevedad del
  código").
- Mantiene el mismo patrón de testing ya usado (funciones puras `dict -> Optional[str]`, un test
  por escenario).

**Las 3 reglas, aplicadas consistentemente:**

1. **Cada paso se calcula de forma independiente.** Si el dato de ESE paso puntual falta, el paso
   dice `"no disponible"` (o una variante corta con el motivo, ej. `"no disponible o no aplica
   (empresa con pérdidas)"` para PER cuando `per_no_aplicable`) — nunca se omite el paso completo
   de la narración, nunca se salta el número sin decir nada.
2. **El resultado final solo se muestra si TODOS los pasos de los que depende matemáticamente están
   disponibles.** Si depende de un paso faltante, el resultado dice `"no se puede calcular sin el
   dato faltante"` — nunca se aproxima, nunca se omite en silencio, nunca se muestra un número
   parcial o inventado.
3. **Si NINGÚN paso tiene datos disponibles**, la función sigue devolviendo `None` (mismo criterio
   que hoy: no tiene sentido narrar una Cuenta donde no hay un solo número real que mostrar) —
   `_build_leaf_message` sigue omitiendo la línea `🧮 Cuenta:` completa en ese caso extremo, que en
   la práctica no debería ocurrir nunca con datos reales de un ticker (el payload siempre trae algo).

**Regla 3, en código** — la única guarda "todo o nada" que sobrevive en las 17 funciones es esta,
no la guarda "falta 1 campo → `None`" que existía hoy:

```python
if not any((eps_ok, g_ok, y_ok)):
    return None
```

en vez de la guarda actual:

```python
if None in (eps, g, y, valor) or y == 0:
    return None
```

### 2. Retrofit de Graham/DCF — SÍ se aplica, con justificación técnica (más allá del pedido explícito)

El pedido dice explícitamente: *"Aplica a TODAS las preguntas con Cuenta narrada (las que ya
existen: Graham/DCF; y las nuevas de este alcance)"* — no es ambiguo, es una instrucción directa,
no una intuición a confirmar. Aun así, documento el criterio técnico independiente que
**coincidiría con la misma decisión aunque no estuviera explícita**:

- **Consistencia de producto**: si Altman narra "Rotación de Activos: no disponible" pero Graham
  desaparece entero por el mismo tipo de problema, el usuario ve 2 comportamientos distintos para
  el mismo síntoma ("falta un dato") en el mismo menú — inconsistencia visible, no solo interna.
- **Riesgo técnico bajo**: `_cuenta_gra`/`_cuenta_dcf` fueron escritas HOY, con el mismo estilo de
  guardas explícitas (`if None in (...): return None`) que las 15 preguntas nuevas — el patrón de
  reescritura es idéntico, no hay una razón técnica para tratarlas distinto.
- **`security` ya había marcado esto como pendiente de confirmar** en la revisión de
  `SDD_cuenta_narrada_graham_dcf.md` ("se marca como punto a confirmar con Daniela si el criterio
  de aceptación #5 [...] es lo que ella espera") — este pedido de hoy es exactamente esa
  confirmación, y la respuesta es sí.

**No se marca como decisión abierta** — hay instrucción explícita + justificación técnica
independiente que coincide.

### 3. Cada narración — pasos reales, redactados desde el código y con las mismas "qué mide" ya aprobadas en `SDD_desglose_universal.md`

Se listan las 16 (más el retrofit de `gra`/`dcf`) agrupadas por complejidad. **Todo el texto de
abajo es exactamente el que se implementa** (no un resumen) — medido con el script real en la
sección de Presupuesto.

#### Grupo A — 1 paso de comparación (`rsk`, `aql`)

```python
def _cuenta_rsk(datos: dict) -> Optional[str]:
    beta = datos.get("beta")
    bajo, alto = datos.get("beta_umbral_bajo"), datos.get("beta_umbral_alto")
    if beta is None and (bajo is None or alto is None):
        return None
    beta_txt = (
        f"{_ratio2(beta)}, que está {'por debajo de' if bajo is not None and beta < bajo else ('por encima de' if alto is not None and beta > alto else 'entre')} "
        f"{_ratio2(bajo) if bajo is not None else '?'} y {_ratio2(alto) if alto is not None else '?'}"
        if beta is not None and bajo is not None and alto is not None else "no disponible"
    )
    perfil = _perfil_sugerido(beta, bajo, alto) if beta is not None and bajo is not None and alto is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber si el riesgo de esta acción encaja con tu perfil, el modelo hace 2 pasos: "
        f"1) mide qué tan volátil es la acción comparada con el mercado en general (su Beta: {beta_txt}), "
        f"y 2) compara ese número contra los umbrales de tu perfil de riesgo elegido con /start "
        f"→ perfil sugerido: {perfil}."
    )
```

`_cuenta_aql` sigue el mismo patrón de 1 paso (Beta vs. umbrales fijos del factor Low-vol, en vez de
los umbrales de perfil del usuario) — mismo texto, cambia solo la referencia de comparación
("umbrales fijos del factor" en vez de "tu perfil de riesgo").

#### Grupo B — 2 pasos secuenciales (`mul`, `cmp`, `mgr`, `aqv`, `aqm`)

**`mul`** (reemplaza el cuerpo de `_cuenta_mul`; se agrega `_cuenta_mul_corta` para "vf"):

```python
def _cuenta_mul(datos: dict) -> Optional[str]:
    eps, per = datos.get("eps_ttm"), datos.get("per_promedio_peers")
    eps_ok, per_ok = eps is not None, per is not None
    if not eps_ok and not per_ok:
        return None
    valor = _valor_escenario_elegido(datos)
    p1 = _money(eps) if eps_ok else "dato de EPS no disponible"
    p2 = _ratio2(per) if per_ok else "dato de PER promedio de comparables no disponible"
    resultado = _money(valor) if (eps_ok and per_ok and valor is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"Para estimar el valor por este método, el modelo hace 2 pasos: "
        f"1) toma cuánto ganó la empresa por acción en el último año ({p1}), "
        f"y 2) lo multiplica por el PER promedio de empresas parecidas del mismo sector "
        f"({p2}) — así estima cuánto debería valer la acción si cotizara a un múltiplo "
        f"similar al de sus comparables. El resultado: {resultado} por acción."
    )


def _cuenta_mul_corta(datos: dict) -> Optional[str]:
    """Versión corta -- misma fórmula comprimida que `_cuenta_mul` tenía HOY,
    conservada tal cual para el anidado en "vf" (mismo criterio que
    `_cuenta_gra_corta`/`_cuenta_dcf_corta`: la narración completa nueva es
    demasiado larga para el presupuesto de `_MAX_DESGLOSE_CHARS=1200`)."""
    eps, per = datos.get("eps_ttm"), datos.get("per_promedio_peers")
    valor = _valor_escenario_elegido(datos)
    if None in (eps, per, valor):
        return None
    return f"{_money(eps)} × {_ratio2(per)} = {_money(valor)}"
```

**`cmp`**:

```python
def _cuenta_cmp(datos: dict) -> Optional[str]:
    precio, eps = datos.get("precio_actual"), datos.get("eps_ttm")
    per_propio, per_prom = datos.get("per_propio"), datos.get("per_promedio_peers")
    propio_ok = precio is not None and eps and per_propio is not None
    prom_ok = per_prom is not None
    if not propio_ok and not prom_ok:
        return None
    propio_txt = f"{_money(precio)} entre {_money(eps)} = {_ratio2(per_propio)}" if propio_ok else "no disponible (falta precio o EPS)"
    prom_txt = _ratio2(per_prom) if prom_ok else "no disponible (sin comparables con datos suficientes)"
    return (
        f"Para comparar esta acción con su sector, el modelo hace 2 pasos: "
        f"1) calcula el PER propio de la empresa (precio / ganancia por acción): {propio_txt}, "
        f"y 2) lo compara contra el PER promedio de empresas parecidas del mismo sector: {prom_txt}."
    )
```

**`mgr`** (ROIC, Magic Formula):

```python
def _cuenta_mgr(datos: dict) -> Optional[str]:
    if not datos.get("disponible"):
        return None
    ebit, ci = datos.get("ebit"), datos.get("capital_invertido")
    ebit_ok, ci_ok = ebit is not None, ci is not None and ci != 0
    if not ebit_ok and not ci_ok:
        return None
    roic = datos.get("roic")
    ebit_txt = _money(ebit) if ebit_ok else "no disponible"
    ci_txt = _money(ci) if ci_ok else "no disponible"
    resultado = f"{_ratio2(roic)} = {_pct1(roic)}" if (ebit_ok and ci_ok and roic is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"El ranking Magic Formula ordena empresas por 2 factores; el de Retorno usa el ROIC, "
        f"en 2 pasos: 1) toma la ganancia operativa del negocio, antes de intereses e impuestos "
        f"({ebit_txt}), y 2) la divide entre el capital invertido (activos operativos menos "
        f"pasivos corrientes sin deuda) ({ci_txt}) → ROIC = {resultado}. Cuanto más alto, más "
        f"eficiente es la empresa generando ganancias con el capital que usa."
    )
```

**`aqv`** (factor Value AQR):

```python
def _cuenta_aqv(datos: dict) -> Optional[str]:
    ey = datos.get("earnings_yield")
    alto, bajo = datos.get("umbral_alto"), datos.get("umbral_bajo")
    ey_ok = ey is not None
    umb_ok = alto is not None and bajo is not None
    if not ey_ok and not umb_ok:
        return None
    ey_txt = _pct1(ey) if ey_ok else "no disponible"
    umb_txt = _rango_pct(ey, alto, bajo) if ey_ok and umb_ok else "no disponible"
    etiqueta = datos.get("value") if (ey_ok and umb_ok) else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Value (estilo AQR) hace 2 pasos: 1) calcula el Earnings Yield (mismo cálculo "
        f"que en la Magic Formula: EBIT sobre Valor de la Empresa) ({ey_txt}), y 2) lo compara "
        f"contra los umbrales fijos que separan valuaciones baratas/medias/caras dentro de este "
        f"análisis ({umb_txt}) → clasificación: {etiqueta}."
    )
```

**`aqm`** (factor Momentum AQR — ya narrada por `SDD_desglose_universal.md`, ahora con retrofit):

```python
def _cuenta_aqm(datos: dict) -> Optional[str]:
    precio = datos.get("precio_actual")
    avg50, avg200 = datos.get("price_avg_50"), datos.get("price_avg_200")
    p_ok, a50_ok, a200_ok = precio is not None, bool(avg50), bool(avg200)
    if not p_ok and not a50_ok and not a200_ok:
        return None
    avg50_txt = _money(avg50) if a50_ok else "no disponible"
    avg200_txt = _money(avg200) if a200_ok else "no disponible"
    precio_txt = _money(precio) if p_ok else "no disponible"
    etiqueta = datos.get("momentum") if (p_ok and a50_ok and a200_ok) else "no se puede calcular sin el dato faltante"
    return (
        f"El factor Momentum (estilo AQR) hace 2 pasos: 1) compara el precio de hoy contra el "
        f"promedio de los últimos 50 días ({precio_txt} vs. {avg50_txt}), y 2) contra el promedio "
        f"de los últimos 200 días ({precio_txt} vs. {avg200_txt}) — si el precio está por encima "
        f"de ambos → impulso positivo. Clasificación: {etiqueta}."
    )
```

#### Grupo C — 3 pasos secuenciales (`mge`, retrofit `gra`)

**`mge`** (Earnings Yield, Magic Formula):

```python
def _cuenta_mge(datos: dict) -> Optional[str]:
    if not datos.get("disponible"):
        return None
    ebit, mc, td, cash, ey = (datos.get(k) for k in ("ebit", "market_cap", "total_debt", "cash", "earnings_yield"))
    ebit_ok, mc_ok, td_ok, cash_ok = ebit is not None, mc is not None, td is not None, cash is not None
    if not any((ebit_ok, mc_ok, td_ok, cash_ok)):
        return None
    todos_ok = ebit_ok and mc_ok and td_ok and cash_ok
    ebit_txt = _money(ebit) if ebit_ok else "no disponible"
    mc_txt = _money(mc) if mc_ok else "no disponible"
    td_txt = _money(td) if td_ok else "no disponible"
    cash_txt = _money(cash) if cash_ok else "no disponible"
    ev_txt = _money(mc + td - cash) if todos_ok else "no calculable"
    resultado = f"{_ratio2(ey)} = {_pct1(ey)}" if (todos_ok and ey is not None) else "no se puede calcular sin el dato faltante"
    return (
        f"El ranking Magic Formula ordena empresas también por Earnings Yield, en 3 pasos: "
        f"1) toma la ganancia operativa del negocio ({ebit_txt}), "
        f"2) calcula el Valor de la Empresa (capitalización de mercado + deuda total − efectivo: "
        f"{mc_txt} + {td_txt} − {cash_txt} = {ev_txt}), "
        f"y 3) divide la ganancia operativa entre ese valor → Earnings Yield = {resultado}."
    )
```

**`gra` (retrofit, reemplaza el cuerpo cerrado hoy)**:

```python
def _cuenta_gra(datos: dict) -> Optional[str]:
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    eps_ok, g_ok, y_ok = eps is not None, g is not None, (y is not None and y != 0)
    if not any((eps_ok, g_ok, y_ok)):
        return None
    valor = _valor_escenario_elegido(datos) if (eps_ok and g_ok and y_ok) else None
    eps_txt = _money(eps) if eps_ok else "no disponible"
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disponible"
    y_txt = f"{y * 100:.1f}%" if y_ok else "no disponible"
    resultado = _money(valor) if valor is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber cuánto vale la acción según este modelo clásico, el cálculo sigue 3 pasos: "
        f"1) toma cuánto ganó la empresa por acción en el último año ({eps_txt} de EPS), "
        f"2) proyecta cuánto puede crecer esa ganancia a futuro usando el crecimiento histórico "
        f"de sus ganancias, con un techo del 15% para no ser demasiado optimista (en este caso, "
        f"{g_txt}), y 3) multiplica todo por un factor fijo de la fórmula (8.5 + "
        f"2×crecimiento) y lo divide entre la tasa del bono del Tesoro a 10 años ({y_txt}) "
        f"— cuanto más alta esa tasa \"sin riesgo\", menor el valor justo, porque hay una "
        f"alternativa más segura disponible. El resultado: {resultado} por acción."
    )
```

`_cuenta_gra_corta` (usada dentro de "vf") se retrofitea con el mismo criterio de las 3 reglas, pero
solo si Daniela confirma que también quiere "no disponible" dentro de la versión corta anidada —
ver Decisión #6 (se recomienda que SÍ, por consistencia, y se incluye en Artefactos, pero es un
detalle menor de implementación, no una decisión de arquitectura distinta).

#### Grupo D — 4 pasos secuenciales (retrofit `dcf`)

```python
def _cuenta_dcf(datos: dict) -> Optional[str]:
    wacc = datos.get("dcf_wacc")
    g = datos.get("dcf_g_fcf")
    base = datos.get("dcf_fcf_base")
    vp_flujos = datos.get("dcf_valor_presente_flujos")
    vt_desc = datos.get("dcf_valor_terminal_descontado")
    equity = datos.get("dcf_equity_value")
    base_ok, g_ok, wacc_ok = base is not None, g is not None, wacc is not None
    vp_ok, vt_ok, eq_ok = vp_flujos is not None, vt_desc is not None, equity is not None
    if not any((base_ok, g_ok, wacc_ok, vp_ok, vt_ok, eq_ok)):
        return None
    todos_ok = base_ok and g_ok and wacc_ok and vp_ok and vt_ok and eq_ok
    valor_accion = _valor_escenario_elegido(datos) if todos_ok else None
    years = valuation.DCF_PROJECTION_YEARS
    base_txt = _money(base) if base_ok else "no disponible"
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disponible"
    wacc_txt = f"{wacc * 100:.1f}%" if wacc_ok else "no disponible"
    resultado = _money(valor_accion) if valor_accion is not None else "no se puede calcular sin el dato faltante"
    return (
        f"Para saber cuánto vale la empresa hoy, el modelo hace 4 pasos: 1) toma cuánto efectivo "
        f"libre genera HOY el negocio ({base_txt}), 2) asume que va a crecer {g_txt} "
        f"por año durante {years} años, 3) \"trae\" cada uno de esos años futuros a su valor de "
        f"HOY (porque un peso dentro de {years} años vale menos que uno hoy — se descuenta al "
        f"{wacc_txt}, el costo de capital), y 4) le suma un \"valor terminal\" (lo que "
        f"vale seguir generando plata para siempre después del año {years}). Todo eso sumado y "
        f"dividido entre las acciones da {resultado} por acción."
    )
```

**Nota de diseño (pasos 3/4 del DCF)**: `vp_flujos`/`vt_desc`/`equity` son resultados intermedios
del cálculo, no inputs independientes que el usuario pueda visualizar por separado en la narración
(la prosa aprobada por Daniela ya los absorbe implícitamente en "trae cada año a valor de hoy" y
"suma un valor terminal", sin citarlos como número propio) — por eso solo participan de la guarda
`todos_ok` (si falta cualquiera, el resultado final se marca no disponible) pero no tienen su propio
"paso texto" visible, igual que en la versión ya aprobada hoy. Esto es consistente con el criterio
ya usado en la spec cerrada: la narración cuenta el "cómo" del cálculo en el nivel de detalle que
Daniela aprobó, no cada variable intermedia.

#### Grupo E — Múltiples piezas independientes, mismo peso (`rat`, `pil`, `mom`, `aqq`)

Estas 4 ya tenían (`rat`/`mom`) o necesitan (`pil`/`aqq`) un patrón distinto: no son pasos
*secuenciales* de una sola fórmula (el paso 2 no depende del paso 1) — son **piezas independientes
del mismo peso**, cada una evaluable o no evaluable por separado. El retrofit acá es más simple:
cambiar `continue`/omisión silenciosa por texto explícito "no disponible" en la pieza que falta.

**`rat`**:

```python
def _cuenta_rat(datos: dict) -> Optional[str]:
    ca, cl = datos.get("current_assets"), datos.get("current_liabilities")
    liq_ok = ca is not None and cl and datos.get("ratio_liquidez") is not None
    rev, cor = datos.get("revenue"), datos.get("cost_of_revenue")
    mb_ok = rev and cor is not None and datos.get("margen_bruto") is not None
    precio, eps = datos.get("precio_actual"), datos.get("eps_ttm")
    per_ok = precio is not None and eps and datos.get("per") is not None and not datos.get("per_no_aplicable")
    mc = datos.get("market_cap")
    ps_ok = mc is not None and rev and datos.get("ps") is not None
    if not any((liq_ok, mb_ok, per_ok, ps_ok)):
        return None
    liq_txt = f"{_money(ca)} entre {_money(cl)} = {_ratio2(datos['ratio_liquidez'])}" if liq_ok else "no disponible (falta activo o pasivo corriente)"
    mb_txt = f"({_money(rev)} − {_money(cor)}) sobre {_money(rev)} = {_pct1(datos['margen_bruto'])}" if mb_ok else "no disponible (falta ventas o costo de ventas)"
    per_txt = f"{_money(precio)} entre {_money(eps)} = {_ratio2(datos['per'])}" if per_ok else "no disponible o no aplica (empresa con pérdidas)"
    ps_txt = f"{_money(mc)} entre {_money(rev)} = {_ratio2(datos['ps'])}" if ps_ok else "no disponible (falta capitalización o ventas)"
    return (
        f"El modelo calcula hasta 4 ratios clave, cada uno con sus propios datos: "
        f"1) Liquidez corriente (activo corriente / pasivo corriente): {liq_txt}, "
        f"2) Margen bruto ((ventas − costo de ventas) / ventas): {mb_txt}, "
        f"3) PER (precio / ganancia por acción): {per_txt}, "
        f"4) P/S (capitalización / ventas): {ps_txt}."
    )
```

**`pil`**:

```python
def _cuenta_pil(datos: dict) -> Optional[str]:
    pillars = datos.get("pillars") or {}
    rev_r, rev_a = datos.get("revenue_reciente"), datos.get("revenue_antiguo")
    ni_r, ni_a = datos.get("net_income_reciente"), datos.get("net_income_antiguo")
    ratio_liq = datos.get("ratio_liquidez")
    ing_ok = rev_r is not None and rev_a is not None and pillars.get("ingresos_crecientes") is not None
    util_ok = ni_r is not None and ni_a is not None and pillars.get("utilidades_crecientes") is not None
    deuda_ok = ratio_liq is not None and pillars.get("deuda_controlada") is not None
    precio_ok = pillars.get("precio_razonable") is not None
    if not any((ing_ok, util_ok, deuda_ok, precio_ok)):
        return None
    ing_txt = f"{_money(rev_r)} vs. {_money(rev_a)} → {'creciente' if pillars['ingresos_crecientes'] else 'no creciente'}" if ing_ok else "no disponible (falta historial de ventas)"
    util_txt = f"{_money(ni_r)} vs. {_money(ni_a)} → {'creciente' if pillars['utilidades_crecientes'] else 'no creciente'}" if util_ok else "no disponible (falta historial de utilidades)"
    deuda_txt = f"liquidez {_ratio2(ratio_liq)} → {'controlada' if pillars['deuda_controlada'] else 'no controlada'}" if deuda_ok else "no disponible (falta liquidez)"
    precio_txt = ("✅ razonable" if pillars["precio_razonable"] else "❌ no razonable") if precio_ok else "no disponible (falta Valor Justo Total)"
    return (
        f"El modelo evalúa 4 pilares de calidad, cada uno como sí/no: "
        f"1) ¿Ingresos crecientes? compara ventas recientes contra las más antiguas del historial: {ing_txt}. "
        f"2) ¿Utilidades crecientes? compara utilidad reciente contra la más antigua: {util_txt}. "
        f"3) ¿Deuda controlada? compara activo corriente contra pasivo corriente: {deuda_txt}. "
        f"4) ¿Precio razonable? compara el precio de hoy contra el Valor Justo Total: {precio_txt}."
    )
```

**`mom`** — mismo patrón de retrofit sobre las 4 piezas ya existentes (deja de usar `continue` en
silencio, arma texto explícito "no disponible" por referencia faltante). **`aqq`** — mismo retrofit
sobre las 3 sub-métricas (deja de usar `continue`), agregando "no disponible" explícito. Ambas
siguen el mismo patrón textual que `rat`/`pil` de arriba — el código completo se detalla en
Artefactos, no se repite dos veces en esta sección por longitud.

#### Grupo F — Suma ponderada de N términos (`alz`, `azp`)

```python
def _cuenta_alz(datos: dict) -> Optional[str]:
    altman = datos.get("altman") or {}
    if not altman.get("disponible"):
        return None
    a, b, c, d, e = (altman.get(k) for k in ("a", "b", "c", "d", "e"))
    labels_pesos = [
        ("Capital de Trabajo sobre Activos", a, 1.2),
        ("Utilidades Retenidas sobre Activos", b, 1.4),
        ("EBIT sobre Activos", c, 3.3),
        ("Capitalización de Mercado sobre Deuda", d, 0.6),
        ("Rotación de Activos (Ventas sobre Activos)", e, 1.0),
    ]
    if not any(v is not None for _, v, _ in labels_pesos):
        return None
    piezas = []
    for i, (label, val, peso) in enumerate(labels_pesos, start=1):
        piezas.append(f"{i}) {label} = {_ratio2(val)}, ×{peso} = {_ratio2(val * peso)}" if val is not None else f"{i}) {label}: no disponible")
    todos_ok = all(v is not None for _, v, _ in labels_pesos)
    z = altman.get("z")
    resultado = f"Z = {_ratio2(z)}" if todos_ok and z is not None else "Z no se puede calcular sin el dato faltante"
    return (
        f"El Altman Z-Score suma 5 factores financieros, cada uno multiplicado por un peso fijo "
        f"de la fórmula: " + ", ".join(piezas) + f". Sumando los 5 términos → {resultado}."
    )
```

`_cuenta_azp` — mismo patrón con 4 términos (`6.56×A + 3.26×B + 6.72×C + 1.05×D`), mismos labels de
`_DESGLOSE_ALTMAN_A_D` reutilizados.

#### Grupo G — Resumen sin pasos aritméticos propios (`pig`, retrofit `pir`/`pia`/`pie`)

**`pig`** (sin retrofit de "dato faltante por paso" — justificado en el Alcance arriba, no hay
términos independientes que puedan faltar por separado):

```python
def _cuenta_pig(datos: dict) -> Optional[str]:
    piotroski = datos.get("piotroski") or {}
    puntaje, evaluables = piotroski.get("puntaje"), piotroski.get("criterios_evaluables")
    if puntaje is None or not evaluables:
        return None
    return (
        f"El F-Score de Piotroski suma 1 punto por cada criterio de calidad contable que la "
        f"empresa cumple, evaluados en 3 grupos (rentabilidad, apalancamiento y liquidez, "
        f"eficiencia operativa) — de los {evaluables} criterios que se pudieron evaluar con los "
        f"datos disponibles de este ticker, cumplió {puntaje}. Cuantos más cumple, más sólida es "
        f"su calidad contable."
    )
```

**`pir`/`pia`/`pie`** — retrofit de `_fmt_criterio_piotroski` (línea 1502): en vez de devolver
`None` (y que el list comprehension de `_cuenta_piotroski_grupo` lo descarte con `if p`), devuelve
un string explícito "no disponible" por criterio:

```python
def _fmt_criterio_piotroski(criterio: dict) -> Optional[str]:
    nombre = criterio.get("nombre")
    label = _PIOTROSKI_CUENTA_LABEL.get(nombre)
    if label is None:
        return None  # nombre desconocido -- no es "dato faltante", es un bug de datos, se sigue descartando
    cumplido, valores = criterio.get("cumplido"), criterio.get("valores")
    if cumplido is None or not valores:
        return f"{label}: dato no disponible"
    ...  # resto de la función, sin cambios (arma la comparación real por nombre de criterio)
```

`_cuenta_piotroski_grupo` no cambia (sigue uniendo con `" · "` todo lo que no sea `None`) — el
`None` solo ocurre ahora si `nombre` no matchea ningún label conocido (caso de bug de datos, no de
"falta info del ticker"), que sigue siendo correcto descartar en silencio (no es un dato del
ticker, es un valor inesperado del propio código).

---

### 4. Un solo tope de longitud para las 22 preguntas `dato_y_paso_a_paso` — se retira `_MAX_CUENTA_NARRADA_CHARS` como excepción, `_MAX_CUENTA_CHARS` pasa a valer 800

**Medido con Python real (script y resultados completos en Presupuesto)**: el peor caso de las 16
narraciones nuevas + el retrofit de `gra`/`dcf` (incluyendo la versión "falta 1 dato", que en varios
casos es MÁS larga que la versión completa, porque el texto explicativo de "no disponible" a veces
pesa más que el número que reemplaza) da un máximo de **671 caracteres** (`gra`, retrofit, falta
`g`). El siguiente más alto es `pil` con **564** (todo disponible). Ninguna de las 16+2 supera 800.

**Decisión**: en vez de mantener 2 constantes (`_MAX_CUENTA_CHARS=400` para las "viejas",
`_MAX_CUENTA_NARRADA_CHARS=800` solo para `gra`/`dcf`), se simplifica a **una sola constante**:
después de esta spec, **21 de las 22 preguntas `dato_y_paso_a_paso` son narración en prosa** (todas
menos `vf`, que sigue con su fórmula corta de 1 línea `(A + B + C) / 3 = D`, muy por debajo de
cualquiera de los 2 topes). No hay ninguna pregunta que siga necesitando un tope de 400 — mantenerlo
sería una distinción sin ningún caso real que la use.

```python
_MAX_CUENTA_CHARS = 800  # antes 400 -- todas las Cuentas de dato_y_paso_a_paso son narración en
                          # prosa después de SDD_cuenta_narrada_universal.md (peor caso medido: 671,
                          # margen ~19%), salvo "vf" (fórmula corta, muy por debajo). Reemplaza a
                          # `_MAX_CUENTA_NARRADA_CHARS`, que se retira (dejó de ser una excepción).


def _enforce_cuenta_length(cuenta: str, max_chars: int = _MAX_CUENTA_CHARS) -> Optional[str]:
    ...  # cuerpo sin cambios


def _build_cuenta_line(kind: str, question_code: str, datos: dict) -> Optional[str]:
    ...
    cuenta = tabla[question_code](datos)
    if cuenta is None:
        return None
    return _enforce_cuenta_length(cuenta)  # ya no hay 2 ramas -- 1 sola constante para las 22
```

**Margen del tope**: 671/800 ≈ 84% de uso en el peor caso conocido — más ajustado que el margen
2.5x que tenía `_MAX_CUENTA_CHARS=400` originalmente para fórmulas comprimidas, pero consistente
con el margen ~1.25x que ya se aceptó para `_MAX_CUENTA_NARRADA_CHARS=800` en la spec de hoy (la
prosa completa, a diferencia de una fórmula, no tiene forma de acortarse más sin perder claridad —
mismo razonamiento ya validado).

### 5. `mul` necesita versión corta dedicada para el anidado en "vf" — mismo patrón que `gra`/`dcf`

Confirmado en Presupuesto: la narración nueva de `mul` completa mide 361-431 caracteres (vs. los
~30 de la fórmula comprimida que usa hoy dentro de "vf"). Si se reutilizara tal cual dentro del
Desglose de "vf", el bloque completo (narración larga de `mul` + `_cuenta_gra_corta` +
`_cuenta_dcf_corta` + las 3 líneas de "qué mide") se acercaría/excedería `_MAX_DESGLOSE_CHARS=1200`
en el peor caso combinado. Se agrega `_cuenta_mul_corta` (código en Decisión #3, Grupo B) — idéntica
a la fórmula comprimida que `_cuenta_mul` tenía HOY, sin ningún cambio de texto (ya era corta, no
hace falta redactar una versión nueva, solo conservarla bajo otro nombre).

`_VF_SUB_MODELO_CUENTA` (línea 1443) pasa de:
```python
_VF_SUB_MODELO_CUENTA = {"mul": _cuenta_mul, "gra": _cuenta_gra_corta, "dcf": _cuenta_dcf_corta}
```
a:
```python
_VF_SUB_MODELO_CUENTA = {"mul": _cuenta_mul_corta, "gra": _cuenta_gra_corta, "dcf": _cuenta_dcf_corta}
```

El bloque "🔍 Desglose:" completo de "vf" queda **exactamente igual en longitud** que hoy (960
caracteres medidos en la spec cerrada), porque `_cuenta_mul_corta` es carácter por carácter la
misma función que `_cuenta_mul` tenía antes de esta spec — cero impacto de presupuesto en "vf".

### 6. Retrofit de "dato faltante por paso" en `_cuenta_gra_corta`/`_cuenta_dcf_corta`/`_cuenta_mul_corta` (versión anidada en "vf") — SÍ, por consistencia, impacto de presupuesto ya verificado

Las 3 versiones cortas usadas dentro de "vf" también reciben el mismo mecanismo de 3 reglas (si
falta un dato, esa parte de la flecha dice "no disponible" en vez de que toda la sub-cuenta
desaparezca) — mismo criterio de consistencia que motiva el retrofit de las versiones completas
(Decisión #2). El impacto en el presupuesto de "vf" es despreciable: reemplazar un número por el
texto "no disponible" cambia la longitud en ±15 caracteres por campo, muy por debajo del margen de
240 caracteres (20%) que ya tiene el bloque de "vf" medido en la spec cerrada (960 de 1200).

```python
def _cuenta_gra_corta(datos: dict) -> Optional[str]:
    eps, g, y = datos.get("eps_ttm"), datos.get("g_aplicado"), datos.get("y_value")
    eps_ok, g_ok, y_ok = eps is not None, g is not None, (y is not None and y != 0)
    if not any((eps_ok, g_ok, y_ok)):
        return None
    valor = _valor_escenario_elegido(datos) if (eps_ok and g_ok and y_ok) else None
    eps_txt = _money(eps) if eps_ok else "no disp."
    g_txt = f"{g * 100:.1f}%" if g_ok else "no disp."
    y_txt = f"{y * 100:.1f}%" if y_ok else "no disp."
    resultado = _money(valor) if valor is not None else "no calculable"
    return (
        f"EPS {eps_txt} → se proyecta con {g_txt} de crecimiento (techo 15%) → se "
        f"multiplica por 8.5+2×crecimiento y se ajusta por la tasa del bono a 10 años "
        f"({y_txt}, a mayor tasa, menor valor) = {resultado}."
    )
```

`_cuenta_dcf_corta`/`_cuenta_mul_corta` siguen el mismo patrón — el detalle completo va en
Artefactos, no se repite acá por longitud.

---

## Decisiones abiertas para Daniela

**Ninguna de arquitectura.** El pedido ya trae la decisión de retrofit (Decisión #2) explícita, y
el resto son consecuencias técnicas directas de esa decisión (tope único, versión corta de `mul`,
retrofit de las versiones cortas anidadas). Un solo punto de **redacción, no de arquitectura**, que
puede ajustarse sin reabrir esta spec si a Daniela no le convence el texto exacto:

- La frase de resultado no calculable se redactó como `"no se puede calcular sin el dato
  faltante"` en las versiones completas y `"no calculable"` en las versiones cortas anidadas — si
  Daniela prefiere otra redacción (ej. más corta, o distinta para cada pregunta), es un cambio de
  texto sobre esta spec, no una decisión de diseño pendiente.

---

## Presupuesto/impacto — mediciones reales (Python ejecutado, no estimado)

**Script**: `/private/tmp/claude-501/-Users-danielavergara/5baeadd6-73be-4f8a-98d6-e80eb2fe307d/scratchpad/medir_narraciones.py`
(implementa las 16 narraciones nuevas + el retrofit de `gra`/`dcf`, con los mismos valores de peor
caso ya usados en `SDD_cuenta_narrada_graham_dcf.md`: EPS/ratios de 3 cifras, tasas de 2 dígitos,
montos hasta ~$2 billones/trillion). Corrido con `python3`, resultados exactos:

| Pregunta | Todo disponible | Falta 1+ dato | Máximo |
|---|---|---|---|
| `mul` (completa) | 361 | 431 | 431 |
| `mul_corta` (vf) | 33 | — (no medido, cambio ±15 esperado) | 33 |
| `rat` | 483 | 493 | 493 |
| `pil` | 564 | 559 | 564 |
| `rsk` | 341 | 321 | 341 |
| `mom` | 308 | 300 | 308 |
| `cmp` | 256 | 257 | 257 |
| `ver` | 263 | 330 | 330 |
| `alz` | 427 | 455 | 455 |
| `azp` | 407 | 433 | 433 |
| `pig` | 336 | — (sin retrofit, ver Grupo G) | 336 |
| `pir` (grupo, 4 criterios) | 208 | 233 | 233 |
| `mgr` | 425 | 447 | 447 |
| `mge` | 404 | 420 | 420 |
| `aqv` | 309 | 353 | 353 |
| `aqq` | 304 | 342 | 342 |
| `aqm` | 301 | 341 | 341 |
| `aql` | 222 | 248 | 248 |
| `gra` (retrofit) | 637 | **671** | **671** |
| `dcf` (retrofit) | 532 | 566 | 566 |

**Peor caso absoluto: 671 caracteres (`gra`, falta el dato de crecimiento `g`)** — por debajo de
`_MAX_CUENTA_CHARS=800` (nueva constante única, Decisión #4), con margen de 129 caracteres (~16%).

**Confirmado — la versión "falta 1 dato" es más larga que "todo disponible" en 15 de las 19 filas
medidas** (la frase explicativa "no disponible (falta ...)" pesa más que el número que reemplaza),
lo cual valida por qué se midieron ambos casos explícitamente en vez de asumir que "todo disponible"
es siempre el peor caso (lección repetida hoy: nunca estimar).

**Mensaje completo, peor caso combinado (cota superior por diseño, no un ticker real)**: header
transparencia (~75) + `📌 Dato:` (~90) + `🧮 Cuenta:` (tope duro 800) + `🔍 Desglose:` si la
pregunta lo tiene (tope duro 1200, sin cambios) + respuesta de Ollama (tope duro
`_MAX_EXPLANATION_CHARS=480`) + `📐 Fórmula`/`📊 Fuente` (~350) + disclaimer (~150) + separadores
(~14) ≈ **3159 caracteres en el peor caso posible por diseño** (suma de los 3 topes duros, nunca
ocurre a la vez en la práctica porque ninguna pregunta real tiene Cuenta Y Desglose ambos en su
peor caso simultáneamente) — margen de 937 caracteres (23%) contra `TELEGRAM_MESSAGE_LIMIT=4096`.
Sigue habiendo margen suficiente sin necesitar `chunk_for_telegram`.

**Impacto en "💰 Valor Justo Total"**: cero cambio de longitud (`_cuenta_mul_corta` es
byte-a-byte la función `_cuenta_mul` de antes de esta spec) — el bloque sigue midiendo 960
caracteres (bajo 1200), como ya estaba validado.

---

## Criterios de aceptación

**Genéricos — 16 preguntas nuevas + retrofit de `gra`/`dcf`/`gra_corta`/`dcf_corta`:**
- [ ] Cada una de las 16 funciones `_cuenta_*` (`mul`, `rat`, `pil`, `rsk`, `mom`, `cmp`, `ver`,
      `alz`, `azp`, `pir`/`pia`/`pie` vía `_fmt_criterio_piotroski`, `mgr`, `mge`, `aqv`, `aqq`,
      `aqm`, `aql`) devuelve una narración de pasos con los números reales del ticker sustituidos,
      no la fórmula comprimida ni la lista sin contexto que tenían hoy.
- [ ] `pig` narra qué es y qué significa el F-Score (sin pasos aritméticos propios, justificado en
      el Alcance) — sin retrofit de "dato faltante por paso" (no aplica, justificado).
- [ ] Cada narración con 2+ pasos: si falta el dato de 1 paso puntual, ESE paso dice "no
      disponible" (con el motivo cuando aplica, ej. PER no aplicable) — el resto de los pasos con
      datos disponibles se sigue mostrando con sus números reales.
- [ ] El resultado final de cada narración dice "no se puede calcular sin el dato faltante" (o la
      variante corta en las versiones anidadas) si depende de un paso sin dato — nunca se muestra
      un número aproximado, inventado, o a medio calcular.
- [ ] Si NINGÚN dato de la pregunta está disponible para el ticker, la función devuelve `None` y
      `🧮 Cuenta:` se omite completa (mismo comportamiento de siempre para ese caso extremo).
- [ ] `_cuenta_gra`/`_cuenta_dcf` (retrofit): mismo mecanismo de 3 reglas aplicado — para el mismo
      `datos` de entrada que hoy ya usan las funciones cerradas, si TODOS los campos están
      presentes el texto es carácter-por-carácter idéntico al ya aprobado por Daniela (regresión
      cero sobre el caso feliz).
- [ ] `_cuenta_gra_corta`/`_cuenta_dcf_corta` (retrofit) y `_cuenta_mul_corta` (nueva): mismo
      mecanismo de 3 reglas, usadas por `_VF_SUB_MODELO_CUENTA` dentro de "vf" — `mul` deja de
      compartir función con la versión del botón individual.
- [ ] `_MAX_CUENTA_NARRADA_CHARS` se retira; `_MAX_CUENTA_CHARS` pasa a valer 800; `_build_cuenta_line`
      ya no tiene la rama condicional `question_code in ("gra", "dcf")` — 1 sola constante para las
      22 preguntas `dato_y_paso_a_paso`.
- [ ] Test de longitud, peor caso de montos (valores del script real, o equivalente): las 18
      Cuentas narradas (16 nuevas + `gra`/`dcf` retrofit) ≤ 800 caracteres, en ambos escenarios
      (todo disponible, falta 1 dato). El test debe fallar explícitamente si algún cambio futuro de
      redacción rompe el presupuesto.
- [ ] `_VF_SUB_MODELO_CUENTA["mul"]` apunta a `_cuenta_mul_corta`, no a `_cuenta_mul`. El bloque
      "🔍 Desglose:" completo de "vf" sigue midiendo lo mismo que antes de esta spec (≤ 1200,
      sin cambio de longitud respecto al valor ya medido: 960 caracteres).
- [ ] `_CUENTA_TEXTO_LIBRE["mul"]` sigue apuntando a `_cuenta_mul` (la narración completa, para el
      botón individual) — sin cambios de dispatch salvo lo descrito arriba para "vf".
- [ ] `📐 Fórmula:`/`📊 Fuente del dato:` de las 16 preguntas — sin cambios, siguen siendo secciones
      separadas después de la Cuenta.
- [ ] `DESGLOSE_TEXTO_LIBRE`/`DESGLOSE_AVANZADO` (el "🔍 Desglose" término por término, cerrado en
      `SDD_desglose_universal.md`) — sin cambios de contenido; puede coexistir con la narración
      nueva en el mismo mensaje sin duplicar información (la Cuenta narra el cálculo con los pasos
      en orden, el Desglose explica de dónde sale cada término — ya conviven hoy para `gra`/`dcf`).
- [ ] Ningún cálculo de `valuation.py`/`advanced_scoring.py`/`risk_fit.py`/`market_context.py`
      cambia — funciones de cálculo idénticas byte a byte.
- [ ] Suite completa de tests existente sigue en verde, salvo los tests que hoy fijan literalmente
      el string de la fórmula comprimida o de la narración cerrada de `gra`/`dcf` sin el retrofit
      (deben actualizarse para fijar la narración nueva — regresión esperada y deliberada).

---

## Artefactos a crear/modificar

- `src/investbot/ai_explain.py`:
  - Reescribir el cuerpo de las 15 funciones: `_cuenta_mul`, `_cuenta_rat`, `_cuenta_pil`,
    `_cuenta_rsk` (+ `_cuenta_beta_bucket` si aplica), `_cuenta_mom`, `_cuenta_cmp`, `_cuenta_ver`,
    `_cuenta_alz`, `_cuenta_azp`, `_cuenta_pig`, `_cuenta_mgr`, `_cuenta_mge`, `_cuenta_aqv`,
    `_cuenta_aqq`, `_cuenta_aqm`, `_cuenta_aql` (16 preguntas — `pil` y `ver` incluidas).
  - Retrofit de `_fmt_criterio_piotroski` (mecanismo de "dato faltante por paso" para
    `pir`/`pia`/`pie`, sin reescribir el resto de la función).
  - Retrofit de `_cuenta_gra`, `_cuenta_dcf` (mecanismo de 3 reglas, Decisión #2/#3 Grupo C/D).
  - Agregar `_cuenta_mul_corta` (idéntica a la `_cuenta_mul` de hoy); retrofit de
    `_cuenta_gra_corta`, `_cuenta_dcf_corta` (Decisión #6).
  - `_VF_SUB_MODELO_CUENTA`: `"mul"` pasa a `_cuenta_mul_corta`.
  - Retirar `_MAX_CUENTA_NARRADA_CHARS`; `_MAX_CUENTA_CHARS` pasa de 400 a 800; simplificar
    `_build_cuenta_line` (quitar la rama condicional de `gra`/`dcf`).
- `tests/test_ai_explain.py`:
  - Actualizar los tests existentes que fijan el string literal de las 16 Cuentas comprimidas/
    resumidas para fijar la narración nueva.
  - Actualizar los tests de `_cuenta_gra`/`_cuenta_dcf`/`_cuenta_gra_corta`/`_cuenta_dcf_corta`
    (cerrados hoy) para fijar el retrofit (caso feliz idéntico, caso "falta 1 dato" nuevo).
  - Tests nuevos por pregunta: caso "todo disponible" (narración completa), caso "falta 1 dato
    puntual" (paso individual dice "no disponible", resultado dice "no se puede calcular"), caso
    "sin ningún dato" (`None`, se omite el bloque).
  - Test nuevo de `_cuenta_mul_corta`: usada por `_build_desglose_vf` en vez de `_cuenta_mul` para
    el sub-modelo "Múltiplos" de "vf"; longitud del bloque de "vf" sin cambios (960 caracteres,
    mismo valor ya medido en la spec cerrada).
  - Test de longitud, peor caso de montos, para las 18 Cuentas retrofiteadas/nuevas (≤ 800
    caracteres en ambos escenarios de disponibilidad de datos).
  - Test de que `_MAX_CUENTA_NARRADA_CHARS` ya no existe / `_MAX_CUENTA_CHARS == 800`.

## Restricciones

- No se modifica ninguna fórmula de `valuation.py`, `advanced_scoring.py`, `risk_fit.py`,
  `market_context.py` — esta spec es 100% de presentación/redacción del mismo cálculo ya validado
  (mismo criterio que las 4 specs de Desglose/Cuenta anteriores).
- No se tocan `FORMULAS_TEXTO_LIBRE`/`FUENTES_TEXTO_LIBRE`/`FORMULAS_AVANZADO`/`FUENTES_AVANZADO`/
  `DESGLOSE_TEXTO_LIBRE`/`DESGLOSE_AVANZADO` de `ai_explain_content.py` — ya están cerrados por
  `SDD_desglose_universal.md`, sin cambios en esta spec.
- No se agrega botón, callback ni pantalla nueva — mismos 2 botones ya existentes ("📊 Ver dato" /
  "🎓 Explicame paso a paso") para las 16 preguntas.
- No se toca `_build_leaf_message`, `_build_ver_dato_content`, `_build_desglose_block`,
  `_build_desglose_vf` (salvo el único diccionario `_VF_SUB_MODELO_CUENTA`, Decisión #5),
  `_enforce_desglose_length`, `_MAX_DESGLOSE_CHARS`, ni el call site de `handle_explain`.
- `_MAX_DESGLOSE_CHARS=1200` no cambia de valor — el bloque de "vf" queda con la misma longitud que
  antes de esta spec (Decisión #5).
- `vf` (`_cuenta_vf`) no cambia — Daniela no lo incluyó en el pedido y su fórmula corta
  (`(A + B + C) / D = E`) no tiene la queja de "no se entiende" que motivó esta spec.
- Ningún paso de ninguna narración inventa, aproxima o redondea de forma distinta a como ya lo hace
  el cálculo real — "no disponible"/"no se puede calcular sin el dato faltante" son las ÚNICAS 2
  frases nuevas que puede mostrar el sistema para datos faltantes; ningún otro texto sustituye un
  valor real.
- Si en el futuro cambia algún umbral/constante citada como texto fijo en una narración (ej.
  `GRAHAM_G_CAP`, pesos de Altman, umbrales de `FACTOR_UMBRALES`), ese cambio debe venir acompañado
  de actualizar el texto de la narración correspondiente — mismo criterio ya señalado para `gra` en
  la spec cerrada, extendido a las 16 preguntas nuevas.

---

## Handoff → security

### Specs producidas
- `contexto/specs/abiertas/SDD_cuenta_narrada_universal.md` (este documento).

### Criterios de aceptación base
Ver sección "Criterios de aceptación" arriba — cubren: narración de las 16 preguntas restantes,
retrofit del mecanismo de dato faltante en Graham/DCF (completo y corto), unificación del tope de
longitud, versión corta nueva de Múltiplos, y no-regresión sobre cálculos/Fórmula/Fuente/Desglose.

### Decisiones de diseño tomadas [para que `implementer` no las reabra]
1. Mecanismo de 3 reglas (paso individual "no disponible", resultado final "no se puede calcular"
   solo si depende de un paso faltante, `None` solo si NINGÚN dato está disponible) — sin
   abstracción compartida, cada función redacta su propio texto (Decisión #1).
2. Retrofit de Graham/DCF: SÍ, explícito en el pedido + justificación técnica independiente
   (consistencia de producto, mismo patrón de código, ya señalado como pendiente por `security`
   hoy) (Decisión #2).
3. Las 16 narraciones nuevas están agrupadas por complejidad (1 paso, 2 pasos, 3 pasos, 4 pasos,
   piezas independientes, suma ponderada, resumen sin pasos propios) — el texto exacto de cada una
   está en Decisión #3 (código Python final, no un resumen).
4. Tope único `_MAX_CUENTA_CHARS=800` para las 22 preguntas `dato_y_paso_a_paso`, reemplaza los 2
   topes que convivían hoy — peor caso medido 671, margen 16% (Decisión #4).
5. `mul` gana `_cuenta_mul_corta` (idéntica a la fórmula comprimida de hoy) para el anidado en "vf"
   — cero impacto de presupuesto en ese bloque (Decisión #5).
6. Las versiones cortas anidadas (`_cuenta_gra_corta`/`_cuenta_dcf_corta`/`_cuenta_mul_corta`)
   también reciben el mecanismo de 3 reglas, por consistencia — impacto de presupuesto verificado
   despreciable (Decisión #6).

### Seguridad — puntos a revisar explícitamente
- Confirmar que las 2 frases nuevas (`"no disponible"`, `"no se puede calcular sin el dato
  faltante"`) son texto 100% fijo (nunca interpolan nada del usuario ni de terceros sin sanitizar)
  y que su aparición no cambia el mecanismo de "nunca antes de Ollama" — se arman en Python, se
  insertan en el mensaje final antes de la respuesta de Ollama, mismo orden ya auditado.
- Confirmar que ningún dato nuevo se expone en ninguna de las 16 narraciones — todas leen
  exactamente los mismos campos que ya leía la versión comprimida/resumida anterior de cada
  pregunta (ver el código de cada función en Decisión #3, no hay ningún `datos.get(...)` con una
  clave que no existiera ya en el payload).
- Confirmar que el tope nuevo (`_MAX_CUENTA_CHARS=800`, antes 400) no abre una superficie de
  mensajes desproporcionadamente largos — medido: peor caso real 671, margen 16%, sin vector de
  entrada controlable por el usuario que infle estos números más allá de los datos financieros
  reales del ticker (FMP/FRED/Treasury.gov, no input de usuario) — mismo argumento ya validado para
  `_MAX_CUENTA_NARRADA_CHARS=800` hoy, ahora generalizado a las 22 preguntas.

---

## Revisión de seguridad

**Alcance de esta revisión**: no se repite el análisis de las 16 preguntas pregunta por pregunta —
el patrón (texto fijo en Python, campos ya leídos por la versión anterior de cada función, orden de
armado antes/después de Ollama sin cambios) es idéntico al ya auditado hoy para
`SDD_cuenta_narrada_graham_dcf.md` y para `SDD_desglose_universal.md`. El foco de esta revisión es
el punto genuinamente nuevo: **el cambio de mecanismo de "dato faltante"** (punto 2 del pedido).

### 1. Texto fijo vs. Ollama — sin hallazgos
Las 16 narraciones nuevas y el retrofit de `gra`/`dcf` son 100% f-strings de Python armadas con
`datos.get(...)` sobre el payload ya calculado (`valuation.py`/`advanced_scoring.py`/`risk_fit.py`/
`market_context.py`). Ningún texto se pasa por Ollama antes de mostrarse ni se usa como prompt.
Confirmado contra el código completo mostrado en Decisión #3.

### 2. Mecanismo de "dato faltante por paso" — punto central de esta revisión, sin hallazgos bloqueantes
Se revisó cada función con código completo en la spec (`rsk`, `mul`/`mul_corta`, `cmp`, `mgr`,
`aqv`, `aqm`, `mge`, retrofit `gra`/`gra_corta`, retrofit `dcf`, `rat`, `pil`, `alz`, `pig`,
`_fmt_criterio_piotroski`) verificando que **todo valor que puede faltar está envuelto en un
ternario cuyas dos ramas son siempre string** — nunca un valor `None`/`NaN` cae directo en un
f-string (lo cual produciría literalmente `"None"` visible al usuario). En las 14 funciones con
código completo, esto se cumple sin excepción: cada pieza (`p1`, `p2`, `eps_txt`, `g_txt`, `beta_txt`,
`liq_txt`, `ing_txt`, etc.) tiene su propio fallback `"no disponible"` o `"no disponible (motivo)"`,
y cada resultado final (`resultado`, `perfil`, `etiqueta`, `Z`) solo se calcula si su guarda `_ok`
correspondiente es verdadera, con fallback `"no se puede calcular sin el dato faltante"` en el resto
de los casos. La única guarda todo-o-nada que sobrevive (`if not any(...): return None`) está bien
acotada al caso "ningún dato disponible", que sigue devolviendo `None` de forma segura (se omite el
bloque completo, no se muestra un mensaje vacío ni roto).

**Gap de documentación, no de seguridad**: el código completo de `mom`, `aqq`, `azp`, `_cuenta_dcf_corta`
y `_cuenta_mul_corta` (más allá del ya mostrado `mul_corta` corto de 2 líneas) no está en esta spec
— se describe por referencia ("mismo patrón que rat/pil/gra_corta"). Dado que el patrón descrito es
sólido y de bajo riesgo, esto no bloquea el avance a `qa`/`implementer`, pero se deja como punto para
que `implementer` verifique, al escribir el código real de esas 5 funciones, que efectivamente ningún
valor faltante llega sin envolver a un f-string (mismo checklist de 2 ramas-siempre-string aplicado
arriba). Recomendación: que `qa` agregue un test específico de "ningún campo faltante produce el
string literal `'None'` o `'nan'` en la salida" para las 18 Cuentas retrofiteadas/nuevas — barrera
automática independiente de la revisión manual.

### 3. Tope único de 800 caracteres — sin hallazgos
Mecanismo de truncado (`_enforce_cuenta_length`) no cambia de cuerpo, solo de constante — ya
auditado hoy. Peor caso medido (671, `gra` con `g` faltante) deja margen de 129 caracteres (16%)
antes del tope duro. Ninguna de las 16+2 narraciones nuevas mide más que eso. No hay vector de
entrada controlable por el usuario que infle estos strings más allá de los datos financieros reales
del ticker (fuente: FMP/FRED/Treasury.gov) — ni el "no disponible" ni el "no se puede calcular sin
el dato faltante" dependen de longitud variable de datos externos, son strings fijos.

### 4. Guard anti-invención y separación de datos hacia Ollama — sin hallazgos
El armado de `🧮 Cuenta:` sigue ocurriendo en Python, después de la respuesta de Ollama, sin
modificar qué campos viajan hacia el prompt de Ollama (esta spec no toca `ai_explain_content.py` ni
ningún call site que arme el prompt). Las 2 frases nuevas son estáticas y no se originan ni se
reenvían a Ollama en ningún punto del flujo.

### Conclusión
**Sin hallazgos bloqueantes.** El cambio de mecanismo de "dato faltante" es una mejora de seguridad
en sí (elimina el riesgo de mensajes ambiguos u omisiones silenciosas) y no introduce una superficie
nueva: mismo patrón de texto fijo, mismos campos ya leídos, mismo orden respecto a Ollama, mismo
mecanismo de truncado con margen suficiente. Lista para pasar a `qa` (agregar el test de
"nunca 'None'/'nan' literal" sugerido arriba como criterio adicional) → `implementer`.

---

## Criterios de QA

**Rol:** `qa` — agrega cobertura de testing sobre los "Criterios de aceptación" ya definidos por
`architect` y sobre el punto pendiente que dejó `security` (test genérico de "nunca 'None'/'nan'
literal"). No se implementa código todavía — esto define QUÉ tests debe escribir `implementer`, no
los escribe.

### 1. Criterio de aceptación agregado — test genérico anti-"None" (pedido explícito de `security`)

Los "Criterios de aceptación" de `architect` (arriba) no traían un ítem dedicado a esto — se agrega
acá, como el criterio de aceptación #1 de QA:

- [ ] **Test parametrizado único, corre sobre las 18 Cuentas retrofiteadas/nuevas** (16 preguntas
      nuevas + `_cuenta_gra`/`_cuenta_dcf`) **más las 3 versiones cortas anidadas**
      (`_cuenta_gra_corta`/`_cuenta_dcf_corta`/`_cuenta_mul_corta`) — total 21 funciones bajo test.
      Para cada función, y para cada combinación de "qué dato falta" relevante a esa función (todas
      las combinaciones de 1 dato faltante a la vez, más "todos disponibles" y "ninguno
      disponible"), el test corre la función y confirma con `assert "None" not in resultado and
      "nan" not in resultado.lower()` sobre el string devuelto (cuando no es `None`). No basta con
      correr el caso feliz y el caso "falta 1 dato" ya cubiertos en el resto de los tests — este test
      existe específicamente para barrer combinaciones que los tests dirigidos no necesariamente
      instancian una por una (ver \S3, por qué un test separado en vez de ampliar cada test
      dirigido).
- [ ] El mismo test, correspondiente a `pig` y a `_fmt_criterio_piotroski` (que no siguen el
      mecanismo de 3 reglas completo, ver \S4 de este documento) — confirma igual que ninguna
      combinación de datos de Piotroski produce "None"/"nan" literal, aunque su lógica de
      disponibilidad sea distinta (todo-o-nada para `pig`; por-criterio para
      `_fmt_criterio_piotroski`).
- [ ] Este test es el que hace operativa la recomendación de `security` en "Revisión de seguridad,
      \S2" — se referencia explícitamente en el nombre del test o en su docstring (ej.
      `test_ninguna_cuenta_narrada_expone_none_o_nan`) para que quede trazable al pedido de
      seguridad.

### 2. Cobertura confirmada sobre los criterios de aceptación de `architect`

Repasando la sección "Criterios de aceptación" (arriba) ítem por ítem, la cobertura de test que le
corresponde a cada uno ya queda bien definida por los Artefactos de `architect`
("Tests nuevos por pregunta: caso 'todo disponible' [...], caso 'falta 1 dato puntual' [...], caso
'sin ningún dato'") — se confirma acá sin cambios, y se completa lo que faltaba explícito:

- [ ] **Narración con valores reales, por pregunta** (16 nuevas): 1 test por pregunta con "todo
      disponible", comparando el string devuelto contra el texto exacto esperado (mismo patrón que
      los tests ya cerrados de `gra`/`dcf`) — no solo "no está vacío", sino el texto completo
      carácter por carácter, igual que hoy.
- [ ] **Dato faltante por paso, no por bloque** (16 nuevas + retrofit `gra`/`dcf`): al menos 1 test
      por pregunta donde falta exactamente 1 dato de 1 paso — confirma 2 cosas en el mismo assert:
      (a) el paso puntual dice "no disponible" (con motivo cuando aplica: PER no aplicable en `rat`,
      por ejemplo), y (b) los demás pasos de la MISMA narración siguen mostrando sus números reales
      sin cambios — esto es lo que distingue el mecanismo nuevo del viejo (antes desaparecía el
      bloque entero; ahora solo el paso). Para las preguntas con 3+ pasos (`mge`, `gra`, `dcf`), se
      agrega un segundo caso variando CUÁL de los pasos falta (no alcanza con probar 1 solo), porque
      cada paso tiene su propio texto de fallback y su propio `_ok` en la guarda del resultado final.
- [ ] **Resultado final marcado "no calculable" solo cuando corresponde**: test explícito de que el
      resultado NO se marca como no disponible cuando el dato faltante no participa del cálculo del
      resultado (no aplica a ninguna de las 16, todas dependen de todos sus pasos — se deja como
      caso negativo documentado, no como test vacío: confirmar en cada función que efectivamente
      todos los `_ok` entran en la guarda del resultado, ver \S4 "qué no se prueba").
- [ ] **`None` cuando NINGÚN dato está disponible**: 1 test por pregunta (16 nuevas) con el payload
      vacío/todos los campos relevantes en `None` → confirma que la función devuelve `None` (no un
      string con puros "no disponible"), y que `_build_leaf_message`/`_build_cuenta_line` omiten
      `🧮 Cuenta:` completa en ese caso (test de integración liviano, 1 caso, no por pregunta — el
      comportamiento de omisión ya está probado a nivel `_build_cuenta_line`, no hace falta repetirlo
      16 veces).
- [ ] **Regresión de Graham/DCF (retrofit)**: `_cuenta_gra`/`_cuenta_dcf` con el mismo `datos` de
      entrada que ya usaban los tests cerrados hoy (antes del retrofit) → el texto debe seguir siendo
      carácter-por-carácter idéntico al ya aprobado (test ya existente, se re-corre sin
      modificaciones — si falla, es una regresión real, no un test desactualizado). Además, tests
      NUEVOS del caso "falta 1 dato" para `gra`/`dcf` (antes no existía este caso porque eran
      todo-o-nada) — cubre `eps`/`g`/`y` faltando cada uno por separado en `gra`, y cada uno de los 6
      campos (`base`/`g`/`wacc`/`vp_flujos`/`vt_desc`/`equity`) faltando por separado en `dcf`.
      Mismo tratamiento para `_cuenta_gra_corta`/`_cuenta_dcf_corta`/`_cuenta_mul_corta`.
- [ ] **Tope de 800 caracteres, peor caso**: test que corre las 18 Cuentas retrofiteadas/nuevas con
      los valores de peor caso ya medidos en la spec (tabla de "Presupuesto/impacto") — confirma
      `len(resultado) <= 800` para cada una, en los 2 escenarios ("todo disponible" y "falta 1
      dato"). Este test debe usar los MISMOS valores del script real de medición
      (`medir_narraciones.py`, mencionado en Presupuesto) o fixtures equivalentes — no un valor
      arbitrario — para que el test efectivamente ejerza el peor caso conocido (671 caracteres de
      `gra`) y no un caso promedio que pase aunque el presupuesto real esté roto.
- [ ] **`_MAX_CUENTA_NARRADA_CHARS` retirada / `_MAX_CUENTA_CHARS == 800`**: test directo de la
      constante (ya listado en Artefactos de `architect`, se confirma acá sin cambios).
- [ ] **`pig` como resumen sin narración de pasos propios** (decisión explícita de `architect`,
      Grupo G): test dedicado que confirma 2 cosas — (a) `_cuenta_pig` sigue con guarda todo-o-nada
      (`puntaje is None or not evaluables → None`), sin mecanismo de "no disponible por paso" (a
      diferencia de las otras 20 funciones bajo esta spec); y (b) el texto devuelto no contiene
      ningún patrón de "paso N)" ni evalúa términos independientes — es 1 sola oración de qué
      es/qué significa el puntaje. Se agrega como test explícito porque es el único caso de la tabla
      con lógica deliberadamente distinta al resto, y una regresión futura ("alguien le agrega el
      mecanismo de 3 reglas a `pig` sin darse cuenta de que no aplica") pasaría desapercibida sin
      este test.
- [ ] **`_fmt_criterio_piotroski` (retrofit `pir`/`pia`/`pie`)**: test de que un criterio con
      `cumplido is None` o `valores` vacío devuelve `"{label}: dato no disponible"` en vez de `None`
      (cambio de comportamiento del retrofit), Y test de que `_cuenta_piotroski_grupo` sigue
      uniendo con `" · "` — confirmando que el criterio "no disponible" aparece en la lista unida
      (antes desaparecía). Test separado de que un `nombre` desconocido sigue devolviendo `None` y
      se sigue descartando en silencio (caso de bug de datos, comportamiento sin cambios,
      explícito en la spec).
- [ ] **`_VF_SUB_MODELO_CUENTA["mul"]` → `_cuenta_mul_corta`**: test de integración de
      `_build_desglose_vf` confirmando que usa la función corta (no la narración larga) y que el
      bloque de "vf" mide 960 caracteres, sin cambios respecto al valor ya medido antes de esta spec
      (test de no-regresión de longitud, no solo de que la función correcta esté enchufada).

### 3. Estrategia de fixtures — priorización por profundidad, no 16 fixtures completos redundantes

Dado el volumen (21 funciones bajo el mecanismo nuevo), se prioriza así:

**Fixtures completos y profundos (todos los casos: feliz, cada paso faltando por separado, ningún
dato, longitud en peor caso) para 4 preguntas representativas:**
- **`alz` (Altman)** — representa el Grupo F (suma ponderada), es la que tiene más términos
  independientes (5) y el peor caso de longitud del grupo (455) — cubre el patrón de "muchos campos,
  cada uno con su propio fallback, guarda `all(...)` para el resultado".
- **`gra` (Graham, retrofit)** — representa el Grupo C y es el retrofit con el peor caso de longitud
  de TODA la spec (671, el número que valida el tope de 800) — cubre el patrón de "3 pasos
  secuenciales + regresión del caso feliz ya aprobado".
- **`dcf` (retrofit)** — representa el Grupo D (4 pasos, el más largo — 6 campos en la guarda
  `todos_ok` aunque solo 3 tengan texto de paso visible) — cubre el patrón más complejo de guarda
  (campos que participan del resultado sin tener su propio "paso" narrado, ver la Nota de diseño de
  `architect` en Grupo D) — este matiz necesita un test dedicado que confirme que `vp_flujos`/
  `vt_desc`/`equity` faltando también marcan el resultado como no calculable aunque no tengan texto
  propio.
- **`pig` + `_fmt_criterio_piotroski`** — representa el Grupo G (caso especial, sin mecanismo de 3
  reglas en `pig`; mecanismo por-criterio distinto en `_fmt_criterio_piotroski`) — el caso con más
  probabilidad de romperse por una generalización mal aplicada, ver \S2 arriba.

**Test liviano/parametrizado para las 14 preguntas restantes** (`mul`, `rat`, `pil`, `rsk`, `mom`,
`cmp`, `ver`, `azp`, `mgr`, `mge`, `aqv`, `aqq`, `aqm`, `aql`): 1 fixture de "todo disponible" (texto
exacto) + 1 fixture de "falta 1 dato representativo" (no los N posibles, solo 1 elegido por
pregunta, salvo que la pregunta tenga una particularidad como `per_no_aplicable` en `rat` que amerite
un caso extra) + participación en el test genérico anti-"None" de \S1 (que sí barre combinaciones
más exhaustivas para las 21, de forma parametrizada, sin necesitar un fixture dedicado por
combinación). Este nivel es suficiente porque el mecanismo de las 3 reglas es idéntico en su forma
(ternario con fallback) en las 14 — el riesgo real no está en la lógica particular de cada una
(ya cubierta por los 4 fixtures profundos) sino en errores de tipeo/copy-paste al redactar 14 f-strings
distintas, que un test de texto exacto sí atrapa sin necesitar profundidad adicional.

### 4. Qué NO se prueba, y por qué

- **No se prueba cada combinación de "2 datos faltantes a la vez" o más** (ej. en `rat`, que tiene 4
  piezas independientes, no se testean las C(4,2)=6 combinaciones de 2 piezas faltando juntas) —
  cada pieza es independiente de las demás (no hay interacción entre ellas: el texto de la pieza 1
  no cambia según si falta la pieza 2), así que el caso de "falta 1" más el caso de "ninguna" ya
  cubren el espacio de comportamiento relevante; combinaciones intermedias son variaciones lineales
  sin lógica nueva que probar. Excepción ya cubierta aparte: el test genérico anti-"None" de \S1 sí
  barre 1-a-la-vez de forma sistemática para las 21 funciones, que es donde un error de tipeo
  realista podría esconderse.
- **No se re-verifican las fórmulas de negocio** (`GRAHAM_G_CAP`, pesos de Altman, umbrales de
  `FACTOR_UMBRALES`, cálculo de ROIC/Earnings Yield/Beta, etc.) — están fuera de esta spec por
  Restricción explícita de `architect` ("ningún cálculo de valuation.py/advanced_scoring.py/
  risk_fit.py/market_context.py cambia") y ya tienen su propia cobertura de tests en los módulos de
  cálculo, no en `ai_explain.py`. Los tests de esta spec asumen que `datos` ya trae el resultado
  correcto y solo verifican la REDACCIÓN sobre esos valores.
- **No se prueba el mensaje completo de Telegram de punta a punta** (header + Dato + Cuenta +
  Desglose + Ollama + Fórmula/Fuente + disclaimer, los ~3159 caracteres del peor caso combinado) —
  ese ensamblado y su margen contra `TELEGRAM_MESSAGE_LIMIT=4096` ya está cubierto por tests de
  integración existentes de `handle_explain`/`_build_leaf_message`, sin cambios de esta spec (la
  spec no toca esas funciones salvo `_VF_SUB_MODELO_CUENTA`, ya cubierto arriba). Se prueba cada
  Cuenta de forma aislada (función pura `dict -> Optional[str]`), no el mensaje ensamblado completo
  por pregunta — sería redundante repetir 16 veces un ensamblado ya probado genéricamente.
- **No se prueba interacción con Ollama** (contenido de la respuesta narrada, prompts, cache) — esta
  spec es 100% texto fijo en Python ensamblado sin pasar por Ollama (confirmado por `security`,
  \S1 de la Revisión) — no hay superficie nueva que testear ahí; los tests existentes de Ollama no
  se tocan.
- **No se agrega test de carga/performance** — las 21 funciones son f-strings puros sin I/O, mismo
  costo computacional que las versiones actuales que reemplazan; no hay cambio de complejidad que
  amerite medición de performance.
- **No se prueba la redacción exacta de la frase de "no calculable"** como una decisión de negocio
  cerrada — `architect` la dejó explícitamente como "punto de redacción, no de arquitectura" (ver
  "Decisiones abiertas para Daniela") que puede cambiar sin reabrir la spec; los tests fijan el texto
  actual (`"no se puede calcular sin el dato faltante"` / `"no calculable"`) como valor de
  regresión, no como un requisito inmutable — si Daniela pide otra redacción, se espera que el test
  se actualice junto con el código, no que bloquee el cambio.

### Conclusión QA

Cobertura definida: 21 funciones bajo el mecanismo nuevo (16 preguntas + `gra`/`dcf` + 3 versiones
cortas anidadas), con 4 fixtures profundos (Altman, Graham, DCF, Piotroski) que ejercen cada patrón
estructural del Grupo A-G, tests livianos de texto exacto para las 14 preguntas restantes, 1 test
parametrizado nuevo que responde al pedido explícito de `security` (nunca "None"/"nan" literal en
ninguna narración, para ninguna combinación de dato faltante), regresión explícita del caso feliz de
Graham/DCF, medición de peor caso de 800 caracteres con los valores reales ya calculados en
Presupuesto, y un test dedicado que blinda el caso especial de `pig` (sin mecanismo de 3 reglas,
decisión explícita de `architect`) contra una futura generalización incorrecta.

**Spec lista para pasar a `implementer`.** No quedan huecos de cobertura abiertos: el único punto que
`security` había dejado pendiente (test genérico anti-"None") queda incorporado como criterio de
aceptación explícito en \S1, y la estrategia de fixtures (\S3) resuelve el volumen de 16+ preguntas
sin sacrificar profundidad en los casos de mayor riesgo real (Altman por cantidad de términos,
Graham/DCF por ser el retrofit con el peor caso de longitud, Piotroski por su lógica deliberadamente
distinta).
