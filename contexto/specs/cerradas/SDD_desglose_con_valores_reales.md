# Spec: Desglose con valores reales del ticker — Altman Z, Piotroski F-Score, Magic Formula [Iter-1]

## Contexto

`SDD_desglose_terminos_formula.md` (cerrada) agregó el bloque "🔍 Desglose"
al flujo "🎓 Explicame paso a paso" de `/avanzado`: para 7 de las 27
preguntas (`alz`, `azp`, `pir`, `pia`, `pie`, `mgr`, `mge`), muestra qué es
cada letra de la fórmula y de qué campo de FMP sale — pero 100% texto fijo,
sin el valor puntual que esa letra tomó para el ticker que se está
consultando.

Daniela pidió agregar ese valor real a cada línea (mismo espíritu que
"🧮 Cuenta", pero término por término, al lado de la descripción fija).

### Código real leído para esta spec

- `src/investbot/ai_explain.py`: `_build_desglose_block` (línea 819),
  `_cuenta_alz`/`_cuenta_azp`/`_cuenta_pig`/`_cuenta_piotroski_grupo`/
  `_fmt_criterio_piotroski`/`_cuenta_mgr`/`_cuenta_mge`, `_build_explain_payload`
  → `_payload_avanzado`, call site en `handle_explain` (línea ~1733-1795).
- `src/investbot/ai_explain_content.py`: `DesgloseTermino`, `DESGLOSE_AVANZADO`,
  `desglose()`.
- `src/investbot/advanced_scoring.py`: `AltmanZResult` (a-e ya expuestos),
  `PiotroskiResult`/`CriterioPiotroski` (`valores` ya expuesto),
  `MagicFormulaResult` (solo `ebit`/`capital_invertido`/`ev`/`market_cap`/
  `total_debt`/`cash` — sin `current_assets`/`current_liabilities`/`ppe_net`
  por separado).
- `src/investbot/advanced_command.py`: confirma que `magic=dataclasses.asdict(magic)`
  puebla `ExplanationContext.magic` — cualquier campo nuevo en
  `MagicFormulaResult` llega solo con agregar el campo al dataclass, sin tocar
  `advanced_command.py`.

## Estado objetivo

Cada línea de "🔍 Desglose" muestra, cuando el dato está disponible para ese
ticker:

```
🔍 Desglose:
• A (Capital de Trabajo) = 0.34 — sale de Activos Corrientes − Pasivos Corrientes, sobre Activos Totales (balance). La plata "líquida" que le queda a la empresa para operar el día a día, en proporción a su tamaño.
```

Si el valor puntual no está disponible para ese ticker, la línea se muestra
igual, sin el segmento `= valor` (nunca "None", nunca inventado) — idéntica
al comportamiento de hoy.

## Confirmación por pregunta (punto 1 del pedido)

| Pregunta | ¿Tiene todo lo necesario hoy? | Detalle |
|---|---|---|
| `alz` | Sí | `datos["altman"]["a"/"b"/"c"/"d"/"e"]` ya están en `AltmanZResult` |
| `azp` | Sí | `datos["altman_pp"]["a"/"b"/"c"/"d"]` ya están |
| `pir` | Sí | `datos["criterios"]` trae `cumplido` (bool/None) por criterio |
| `pia` | Sí | ídem |
| `pie` | Sí | ídem |
| `mge` | Sí (confirmado) | `datos.get("ebit"/"market_cap"/"total_debt"/"cash")` ya están en el payload de `mgr`... de `mge` — los 4 términos de la fórmula ya se exponen por separado hoy |
| `mgr` | **No** | `magic.get("ebit")`/`magic.get("capital_invertido")` — este último YA COMBINADO (`(current_assets - current_liabilities) + ppe_net`). Los 2 términos "Capital de Trabajo Neto" y "Activos Fijos Netos" no están expuestos por separado en ningún lado accesible |

Confirmado: la sospecha de Daniela es correcta — **solo `mgr` necesita un
campo nuevo expuesto**. Las otras 6 preguntas se resuelven leyendo el
`datos` que `_build_explain_payload` ya arma hoy, sin tocar
`advanced_scoring.py` para ellas.

Para Piotroski (punto 3 del pedido): `CriterioPiotroski.valores` SÍ existe
con ese nombre exacto en `advanced_scoring.py` (línea 220) y ya viaja hasta
`datos["criterios"][i]["valores"]` vía `dataclasses.asdict`. Ver Decisión de
diseño #3 sobre por qué esta spec NO lo usa para el Desglose.

## Decisiones de diseño tomadas

### 1. `mgr`: exponer `current_assets`, `current_liabilities`, `ppe_net` como campos nuevos de `MagicFormulaResult` — sin tocar la fórmula

`advanced_scoring.MagicFormulaResult` gana 3 campos nuevos, mismo patrón
exacto que ya se usó para exponer A-E de Altman en la iteración anterior
(campos de solo lectura, poblados con los MISMOS valores locales que ya se
usan para calcular `capital_invertido`, nunca recalculados aparte):

```python
@dataclass
class MagicFormulaResult:
    disponible: bool
    roic: Optional[float] = None
    earnings_yield: Optional[float] = None
    campos_faltantes: list[str] = field(default_factory=list)
    ebit: Optional[float] = None
    capital_invertido: Optional[float] = None
    ev: Optional[float] = None
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    # --- campos nuevos (esta spec) — exposición pura, NUNCA cambian el
    # cálculo de `capital_invertido` (validado contra la skill, no se toca) ---
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    ppe_net: Optional[float] = None
```

En `calculate_magic_formula_metrics`, en el `return` de éxito, se agregan los
3 campos con las variables locales que YA existen (`current_assets`,
`current_liabilities`, `ppe_net`) — cero líneas de cálculo nuevas, la
fórmula `capital_invertido = (current_assets - current_liabilities) + ppe_net`
queda exactamente igual.

`dataclasses.asdict(magic)` en `advanced_command.py` (líneas 250, 331) ya
serializa el dataclass completo — no requiere ningún cambio ahí. Los 3
campos nuevos llegan solos a `context.magic`.

En `_payload_avanzado`, el bloque de `mgr` (línea 501-509) agrega 3 líneas:

```python
if question_code == "mgr":
    magic = context.magic or {}
    return {
        "modelo": "ROIC (Magic Formula)",
        "roic": magic.get("roic"),
        "disponible": magic.get("disponible"),
        "ebit": magic.get("ebit"),
        "capital_invertido": magic.get("capital_invertido"),
        "current_assets": magic.get("current_assets"),
        "current_liabilities": magic.get("current_liabilities"),
        "ppe_net": magic.get("ppe_net"),
    }
```

**Nota de superficie mínima** (Decisión de diseño #11 de la spec original):
estos 3 campos nuevos SÍ entran a `datos_del_contexto` y por lo tanto a
`datos_tokens` (el guard de integridad) — mismo tratamiento que `ebit`/
`capital_invertido` ya reciben hoy. No cambia el guard, solo agrega 3
números protegidos más al mismo mecanismo existente.

### 2. `mge`: confirmado, no necesita cambios

Los 4 términos de su desglose (EBIT, Capitalización de Mercado, Deuda Total,
Efectivo) ya están en `datos["ebit"/"market_cap"/"total_debt"/"cash"]` desde
la iteración anterior. Cero cambios en `_payload_avanzado`/`advanced_scoring.py`
para esta pregunta.

### 3. Piotroski (`pir`/`pia`/`pie`): valor = ✅/❌/➖, SIN la magnitud subyacente

Cada línea muestra el estado del criterio, no un número:

```
• ROA positivo (Ganancia Neta) ✅ Cumple — sale de Ganancia Neta (estado de resultados). Si la empresa ganó plata...
```

- `cumplido is True` → `✅ Cumple`
- `cumplido is False` → `❌ No cumple`
- `cumplido is None` (criterio no evaluable para este ticker) → `➖ No evaluable`

**Por qué NO se usa `CriterioPiotroski.valores` acá** (aunque existe y está
confirmado, punto 3 del pedido): esos valores son pares comparativos (ej.
`roa_t` vs `roa_t1`, dos números para UN criterio) — mostrarlos en la misma
línea compacta del Desglose (que ya tiene letra + nombre + origen + qué mide)
satura la línea y duplica lo que "🧮 Cuenta" YA muestra con más espacio y
mejor formato (`_fmt_criterio_piotroski`, ya arma exactamente ese detalle
`"ROA: 0.08 > 0.05 → cumplido"`). El Desglose complementa la Cuenta, no la
repite — la Cuenta es donde vive la magnitud, el Desglose es donde vive el
✅/❌/➖ + la explicación de qué es cada criterio.

Mapeo texto-de-letra → clave interna del criterio (nuevo diccionario local
en `ai_explain.py`, reutiliza las claves canónicas que
`_PIOTROSKI_CUENTA_LABEL` ya usa):

```python
_DESGLOSE_LETRA_A_NOMBRE_CRITERIO = {
    "ROA positivo": "roa_positivo",
    "CFO positivo": "cfo_positivo",
    "ROA creciente": "roa_creciente",
    "CFO > Utilidad": "cfo_mayor_utilidad",
    "Apalancamiento decreciente": "apalancamiento_decreciente",
    "Liquidez creciente": "liquidez_creciente",
    "Sin dilución": "sin_dilucion",
    "Margen bruto creciente": "margen_bruto_creciente",
    "Rotación de activos creciente": "rotacion_activos_creciente",
}
```

Este diccionario vive en `ai_explain.py` (no en `ai_explain_content.py`) —
`ai_explain_content.py` sigue siendo 100% texto fijo, sin ninguna lógica que
dependa de `datos` (no se reabre esa decisión de diseño de la spec anterior).

### 4. `_build_desglose_block` pasa a recibir `datos` — mismo criterio de robustez que `_build_cuenta_line`

Firma nueva: `_build_desglose_block(kind: str, question_code: str, datos: dict) -> Optional[str]`.

Por cada `DesgloseTermino`, se intenta resolver un valor puntual con una
tabla de extractores por `question_code` (vive en `ai_explain.py`, junto a
`_cuenta_*` — no en `ai_explain_content.py`):

```python
def _valor_desglose_alz(letra: str, datos: dict) -> Optional[str]:
    altman = datos.get("altman") or {}
    if not altman.get("disponible"):
        return None
    valor = altman.get({"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}.get(letra))
    return _ratio2(valor) if valor is not None else None

def _valor_desglose_azp(letra: str, datos: dict) -> Optional[str]:
    altman_pp = datos.get("altman_pp") or {}
    if not altman_pp.get("disponible"):
        return None
    valor = altman_pp.get({"A": "a", "B": "b", "C": "c", "D": "d"}.get(letra))
    return _ratio2(valor) if valor is not None else None

def _valor_desglose_piotroski(letra: str, datos: dict) -> Optional[str]:
    nombre = _DESGLOSE_LETRA_A_NOMBRE_CRITERIO.get(letra)
    criterio = next(
        (c for c in (datos.get("criterios") or []) if c.get("nombre") == nombre), None
    )
    if criterio is None or criterio.get("cumplido") is None:
        return "➖ No evaluable" if criterio is not None else None
    return "✅ Cumple" if criterio["cumplido"] else "❌ No cumple"

def _valor_desglose_mgr(letra: str, datos: dict) -> Optional[str]:
    if letra == "EBIT":
        v = datos.get("ebit")
        return _money(v) if v is not None else None
    if letra == "Capital de Trabajo Neto":
        ca, cl = datos.get("current_assets"), datos.get("current_liabilities")
        return _money(ca - cl) if ca is not None and cl is not None else None
    if letra == "Activos Fijos Netos":
        v = datos.get("ppe_net")
        return _money(v) if v is not None else None
    return None

def _valor_desglose_mge(letra: str, datos: dict) -> Optional[str]:
    campo = {
        "EBIT": "ebit", "Capitalización de Mercado": "market_cap",
        "Deuda Total": "total_debt", "Efectivo": "cash",
    }.get(letra)
    v = datos.get(campo) if campo else None
    return _money(v) if v is not None else None

_DESGLOSE_VALOR_EXTRACTORS = {
    "alz": _valor_desglose_alz, "azp": _valor_desglose_azp,
    "pir": _valor_desglose_piotroski, "pia": _valor_desglose_piotroski,
    "pie": _valor_desglose_piotroski,
    "mgr": _valor_desglose_mgr, "mge": _valor_desglose_mge,
}
```

Ensamblado de la línea:

```python
def _build_desglose_block(kind: str, question_code: str, datos: dict) -> Optional[str]:
    terminos = ai_explain_content.desglose(kind, question_code)
    if not terminos:
        return None
    extractor = _DESGLOSE_VALOR_EXTRACTORS.get(question_code)
    lineas = []
    for t in terminos:
        valor = None
        if extractor is not None:
            try:
                valor = extractor(t.letra, datos)
            except Exception:  # noqa: BLE001 -- misma red de seguridad que _build_cuenta_line
                valor = None
        prefijo_valor = f" = {valor}" if valor else ""
        lineas.append(f"• {t.letra} ({t.nombre}){prefijo_valor} — sale de {t.campo_origen}. {t.que_mide}.")
    bloque = "🔍 Desglose:\n" + "\n".join(lineas)
    return _enforce_desglose_length(bloque)
```

**Comportamiento ante dato faltante** (punto 4 del pedido, decisión
explícita): la línea se muestra IGUAL, sin el segmento `= valor` — nunca se
omite la letra completa, nunca se muestra "None". Esto preserva la garantía
que ya tenía `_build_desglose_block` de "nunca puede romper el bloque
completo por un campo faltante del ticker" — ahora extendida a "un valor
puntual faltante no le quita la línea a las demás letras, solo le quita el
número a esa letra". El `try/except` amplio por extractor es la misma red de
seguridad que ya usa `_build_cuenta_line` (línea 1229-1232).

Call site (línea ~1794): pasa a `_build_desglose_block(stored.kind, question_code, datos_del_contexto)`
— `datos_del_contexto` ya existe en ese scope (línea 1733), ya incluye
`"cuenta"` si se calculó, sin problema (los extractores nuevos no leen esa
clave).

### 5. Guard de integridad — confirmado, sin cambios de mecanismo

El guard de integridad (`ai_rewrite.protected_tokens`, comparación contra
`datos_tokens`) sigue actuando ÚNICAMENTE sobre lo que Ollama devuelve, y
`datos_tokens` sigue derivándose ÚNICAMENTE de `datos_del_contexto` — que ya
es 100% numérico/de fuente confiable (FMP + cálculos propios del bot), el
MISMO dato que "🧮 Cuenta" ya usa hoy sin objeciones de seguridad.
`_build_desglose_block` sigue sin recibir ni tocar la respuesta de Ollama —
se arma DESPUÉS de `_fetch_explanation` (línea 1776-1784) y se inserta en el
texto final en `_build_leaf_message`, exactamente igual que hoy (Decisión de
diseño #4 de la spec anterior, no se reabre). El único cambio es que ahora
lee 2-4 claves adicionales de un dict que YA estaba disponible en ese scope
— no hay ninguna llamada nueva a Ollama, ni una superficie nueva de datos
que llegue al prompt.

### 6. `_MAX_DESGLOSE_CHARS=1200` — el guard de longitud ahora sí depende (levemente) del ticker

Antes de esta spec, el guard de 1200 caracteres solo podía dispararse por un
error de edición futuro (texto fijo demasiado largo). Con valores reales
agregados, el bloque varía por ticker (~15-20 caracteres extra por línea, 3
a 4 líneas por pregunta) — el margen sigue siendo amplio (caso más largo
conocido hoy más el agregado de valores no se acerca al tope), pero se deja
documentado como diferencia de comportamiento, no una regresión: mismo
tratamiento de "se omite el bloque completo" si algún día se excede.

## Decisiones abiertas para Daniela

Ninguna decisión de negocio genuina quedó abierta — el diseño resuelve los
4 puntos técnicos del pedido con el mismo criterio de robustez ya validado
en la spec anterior. La única elección con algo de criterio de producto
(mostrar ✅/❌/➖ sin magnitud en Piotroski, Decisión de diseño #3) tiene una
recomendación con justificación técnica clara (evitar duplicar lo que
"🧮 Cuenta" ya muestra) — si Daniela prefiere ver también la magnitud ahí,
es un cambio de una función (`_valor_desglose_piotroski`), sin impacto en el
resto del diseño.

## Criterios de aceptación

- [ ] `alz`/`azp`: cada línea del Desglose muestra el valor real de A-E (o
      A-D) para el ticker consultado, formateado a 2 decimales, cuando
      `altman`/`altman_pp` están disponibles.
- [ ] `pir`/`pia`/`pie`: cada línea muestra ✅ Cumple / ❌ No cumple / ➖ No
      evaluable, según el `cumplido` real de ese criterio para el ticker.
- [ ] `mgr`: `MagicFormulaResult` expone `current_assets`, `current_liabilities`,
      `ppe_net`; el Desglose muestra EBIT, Capital de Trabajo Neto (calculado
      como `current_assets - current_liabilities`) y Activos Fijos Netos con
      sus valores reales, en formato moneda.
- [ ] `mge`: cada línea muestra EBIT, Capitalización de Mercado, Deuda Total
      y Efectivo reales, en formato moneda — sin cambios en `advanced_scoring.py`.
- [ ] Ningún ticker con un modelo no calculable (`disponible=False`) rompe el
      bloque — las líneas se muestran sin el segmento `= valor`.
- [ ] Ningún ticker con un criterio de Piotroski no evaluable rompe el
      bloque — esa línea muestra `➖ No evaluable`.
- [ ] `capital_invertido` en `MagicFormulaResult`/`_cuenta_mgr` sigue
      devolviendo exactamente el mismo valor que hoy (sin cambios de
      fórmula) — test de regresión sobre `calculate_magic_formula_metrics`.
- [ ] El guard de integridad (`ai_rewrite.protected_tokens`) sigue sin ver
      nunca el contenido de `_build_desglose_block` como entrada — solo como
      texto insertado después de la respuesta de Ollama.
- [ ] `_MAX_DESGLOSE_CHARS=1200` sigue aplicando: si se excede, se omite el
      bloque completo (test con un caso sintético que fuerce el límite).
- [ ] Ningún string "None" visible en ningún caso de dato faltante.

## Artefactos a crear/modificar

- `src/investbot/advanced_scoring.py` → `MagicFormulaResult` gana 3 campos
  (`current_assets`, `current_liabilities`, `ppe_net`); `calculate_magic_formula_metrics`
  los puebla en el `return` de éxito con las variables locales ya existentes.
  Fórmula de `capital_invertido`/`roic`/`earnings_yield` sin cambios.
- `src/investbot/ai_explain.py` →
  - `_payload_avanzado`, bloque `mgr`: agrega `current_assets`,
    `current_liabilities`, `ppe_net` al dict devuelto.
  - Nuevo: `_DESGLOSE_LETRA_A_NOMBRE_CRITERIO`, `_valor_desglose_alz`,
    `_valor_desglose_azp`, `_valor_desglose_piotroski`, `_valor_desglose_mgr`,
    `_valor_desglose_mge`, `_DESGLOSE_VALOR_EXTRACTORS`.
  - `_build_desglose_block`: firma pasa a `(kind, question_code, datos)`,
    arma el segmento `= valor` por línea cuando el extractor resuelve algo.
  - Call site en `handle_explain` (línea ~1794): pasa `datos_del_contexto`.
- `src/investbot/ai_explain_content.py` → **sin cambios**. `DesgloseTermino`/
  `DESGLOSE_AVANZADO` siguen siendo 100% texto fijo, no dependen de `datos`.

## Restricciones

- No se toca `capital_invertido = (current_assets - current_liabilities) + ppe_net`
  ni ninguna otra fórmula ya validada contra la skill de análisis fundamental.
- No se toca el guard de integridad (`ai_rewrite.protected_tokens`) ni el
  orden fijo de `_build_leaf_message` (header → Dato → Cuenta → Desglose →
  respuesta de Ollama → Fórmula/Fuente → disclaimer).
- No se agrega ningún botón, callback ni pantalla nueva — el bloque sigue
  apareciendo solo en "🎓 Explicame paso a paso" de las 7 preguntas que ya
  tenían Desglose, exactamente en los mismos casos que hoy (Decisión de
  diseño #3 de la spec anterior, no reabierta: sigue sin mostrarse en "Ver
  dato").
- `ai_explain_content.py` sigue sin conocer `datos` ni Telegram/Ollama —
  la lógica de extracción de valores vive enteramente en `ai_explain.py`.
- No se implementa código en esta fase — esto es solo la spec de arquitectura,
  a la espera de que `security`/`qa` la amplíen y luego `implementer` la
  ejecute.

## Revisión de seguridad

Código real releído para esta revisión: `_build_desglose_block`/
`_build_cuenta_line`/`_enforce_desglose_length`/`_money`/`_ratio2` en
`ai_explain.py` (líneas 697-832, 766-792, 1218-1236), el call site de
`handle_explain` (líneas 1733-1798), y `MagicFormulaResult`/
`calculate_magic_formula_metrics` en `advanced_scoring.py` (líneas 502-566),
más los dos consumidores de `dataclasses.asdict(magic)` en
`advanced_command.py`.

**Sin hallazgos bloqueantes.** Es una extensión chica y bien acotada; el
diseño reutiliza correctamente los mismos mecanismos ya validados en
`SDD_desglose_terminos_formula.md`.

Confirmado punto por punto:

1. **Orden con Ollama, intacto.** En el código actual, `_build_desglose_block`
   se sigue llamando en la línea 1794, DESPUÉS de `_fetch_explanation`
   (líneas 1776-1784). El diseño nuevo no mueve esa línea, solo le agrega el
   parámetro `datos`. El bloque nunca entra al prompt ni a `datos_tokens`
   como entrada del guard — sigue siendo texto que se pega al mensaje final
   en `_build_leaf_message`, igual que hoy. Los 3 campos nuevos de
   `MagicFormulaResult` sí entran a `datos_del_contexto` (y por lo tanto al
   JSON que ve Ollama y a `datos_tokens`), pero son números de FMP/cálculo
   propio, mismo tratamiento que `ebit`/`capital_invertido` ya reciben sin
   objeciones — no es una superficie nueva de tipo distinto.

2. **Dato faltante, sin hueco de "None" visible.** Los 5 extractores
   propuestos (`_valor_desglose_alz/azp/piotroski/mgr/mge`) devuelven
   explícitamente `None` en cada rama sin dato, y solo llaman a `_money`/
   `_ratio2` cuando ya confirmaron que el valor no es `None` — mismo patrón
   que usa hoy `_build_cuenta_line`. El ensamblado
   (`prefijo_valor = f" = {valor}" if valor else ""`) funciona porque
   `valor` en ese punto ya es un string formateado (`"0.34"`, `"➖ No
   evaluable"`) o `None`/`""`, nunca el float crudo — así que un valor real
   igual a cero no se pierde por falsy-check (el string `"0.00"` es truthy).
   El `try/except Exception` amplio alrededor de cada extractor (línea
   256-257 del diseño) es la misma red de seguridad que ya usa
   `_build_cuenta_line` (línea 1229-1232 hoy) — un error de tipos en un
   ticker puntual degrada a "sin valor", nunca rompe el bloque completo ni
   filtra un traceback al usuario.

3. **`MagicFormulaResult`: fórmula intacta, campos nuevos no viajan a otro
   lado inesperado.** `capital_invertido = (current_assets -
   current_liabilities) + ppe_net` (línea 547 hoy) no se toca; los 3 campos
   nuevos solo agregan exposición de datos que YA se calculan con esas
   mismas variables locales. Único consumidor real de
   `dataclasses.asdict(magic)` es `advanced_command.py` (líneas 246-251 y
   327-332), que puebla `context.magic`/`explain_context_sink["magic"]` sin
   ninguna lista explícita de campos — por eso los campos nuevos "entran
   solos" sin tocar ese archivo, y por la misma razón no hay ningún otro
   punto del código (fuera de `ai_explain.py`) que lea `MagicFormulaResult`
   campo por campo y pueda romperse por campos adicionales: es un dataclass
   con default `None`, retrocompatible por diseño con cualquier consumidor
   existente.

4. **Nada de texto libre interpolado sin sanitizar.** Todos los valores que
   entran a la línea del Desglose pasan por `_money`/`_ratio2` (números) o
   por las 3 cadenas fijas `"✅ Cumple"/"❌ No cumple"/"➖ No evaluable"`
   (constantes del código, no datos externos). No hay ningún punto donde un
   campo de texto de FMP (nombre de empresa, descripción, etc.) llegue a
   interpolarse en esta feature.

### Mejora recomendada (no bloqueante)

- El mapeo `_DESGLOSE_LETRA_A_NOMBRE_CRITERIO` es un diccionario nuevo y
  paralelo a `_PIOTROSKI_CUENTA_LABEL`; si algún día una letra de
  `DESGLOSE_AVANZADO` (en `ai_explain_content.py`) cambia de texto y no se
  actualiza este diccionario en paralelo, el criterio correspondiente
  simplemente pierde su valor (degrada a línea sin `= valor`, nunca rompe
  nada) — no es un riesgo de seguridad, pero conviene que `qa` agregue un
  test que recorra las 9 letras de Piotroski contra las claves de este
  diccionario para detectar ese desfase en CI en vez de en producción.

**Conclusión**: spec aprobada desde seguridad tal cual está diseñada, sin
cambios de mecanismo requeridos. Puede pasar a `qa`/`implementer`.

## Criterios QA para Spec: Desglose con valores reales del ticker [Iter-1]

Código real releído para esta ampliación: `tests/test_ai_explain.py` (fixture
`_avanzado_context` línea 176-242; suite existente de "🧮 Cuenta" línea
2112-2358; suite existente de "🔍 Desglose" línea 2584-2773) y
`src/investbot/advanced_scoring.py` (`MagicFormulaResult`/
`calculate_magic_formula_metrics`, línea 500-566) — confirmado que la fórmula
`capital_invertido = (current_assets - current_liabilities) + ppe_net` usa
exactamente esas 3 variables locales, ya extraídas de `balance` en línea
527-529.

### Tipo de prueba principal

**Unit** — toda la extensión es lógica pura en 2 archivos (`ai_explain.py`,
`advanced_scoring.py`), sin I/O nuevo, con `dict`/`dataclass` de entrada y
`Optional[str]`/`dataclass` de salida en cada punto tocado. Ya existe una
suite unit consolidada para "🧮 Cuenta" y "🔍 Desglose" en el mismo módulo con
el mismo patrón (Arrange: `datos`/`ctx` fijo → Act: llamar la función pura →
Assert: string exacto) — esta ampliación se integra ahí, no abre un tipo de
prueba nuevo. No aplica integration/E2E: no hay llamada nueva a FMP, Ollama ni
Telegram (confirmado en Revisión de seguridad, punto 1).

### Cobertura mínima requerida

- [ ] Code coverage ≥ 90% en las funciones nuevas de `ai_explain.py`
      (`_valor_desglose_alz/azp/piotroski/mgr/mge`, `_DESGLOSE_VALOR_EXTRACTORS`,
      `_build_desglose_block` con la firma nueva) — es lógica de negocio
      aislada, no UI ni glue code.
- [ ] 100% branch coverage en `_valor_desglose_mgr` y `_valor_desglose_piotroski`
      (tienen ramas por letra/criterio con comportamiento distinto cada una —
      alto riesgo de que una rama quede sin ejercitar y falle silenciosamente
      en producción, dado que el `try/except` amplio la esconde).
- [ ] Todos los criterios de aceptación del `architect` (sección "Criterios
      de aceptación" arriba) cubiertos por al menos un test — ver tabla de
      trazabilidad más abajo.

### Trazabilidad: criterio de aceptación → caso de prueba

| Criterio del architect | Caso de prueba obligatorio |
|---|---|
| `alz`/`azp` valor real A-E/A-D | `test_valor_desglose_alz_termino_a_termino_ejemplo_de_daniela`, `test_valor_desglose_azp_termino_a_termino` (ver fixtures) |
| `pir`/`pia`/`pie` ✅/❌/➖ | `test_valor_desglose_piotroski_las_9_letras_contra_fixture_avanzado` (parametrizado, ver más abajo) |
| `mgr`: 2 campos nuevos + Desglose con valores reales | `test_magic_formula_result_expone_current_assets_liabilities_ppe_net`, `test_valor_desglose_mgr_termino_a_termino` |
| `mge`: 4 valores reales, sin cambios en `advanced_scoring.py` | `test_valor_desglose_mge_termino_a_termino` (ya cubierto en espíritu por `test_cuenta_mge_verificada_termino_a_termino`, mismos datos de origen) |
| Dato faltante → línea sin `= valor`, nunca "None" | `test_build_desglose_block_altman_no_disponible_lineas_sin_valor`, `test_build_desglose_block_ningun_none_visible_en_ningun_caso` (parametrizado sobre las 7 preguntas) |
| Criterio Piotroski no evaluable → `➖ No evaluable` | `test_valor_desglose_piotroski_no_evaluable_cumplido_none` |
| `capital_invertido` sin cambio de fórmula | `test_calculate_magic_formula_metrics_capital_invertido_identico_post_cambio` (regresión, en `test_advanced_scoring.py`) |
| Guard de integridad no ve `_build_desglose_block` | `test_build_desglose_block_datos_no_es_input_de_ai_rewrite` (ver Testabilidad) |
| `_MAX_DESGLOSE_CHARS=1200` sigue aplicando | ya cubierto por la suite existente (`test_max_desglose_chars_tope_exacto_1200_no_se_omite`, `test_max_desglose_chars_1201_se_omite_completo_no_trunca`) — **no se toca esa lógica**, solo se re-corre para confirmar que sigue verde con la firma nueva de `_build_desglose_block` |
| Ningún "None" visible | cubierto transversalmente por el caso parametrizado de dato faltante arriba |

### Casos obligatorios

- [ ] **Happy path por pregunta (7 casos)**: para cada una de `alz`, `azp`,
      `pir`, `pia`, `pie`, `mgr`, `mge`, con la fixture `_avanzado_context()`
      ya existente (ampliada — ver Fixtures faltantes), el bloque de Desglose
      de esa pregunta contiene el valor exacto esperado en cada línea,
      formateado igual que en "🧮 Cuenta" para el mismo término (ver
      "Consistencia Cuenta vs. Desglose" más abajo).
- [ ] **Caso límite — cero real, no ausente**: un ticker con `roa_positivo`
      con `valores.roa_t == 0.0` (o un término de Altman en `0.0`) debe seguir
      mostrando `= 0.00` en el Desglose, nunca omitir el segmento por
      falsy-check. Cubre explícitamente el punto de seguridad #2 de la
      Revisión (`"0.00"` es truthy como string, pero el test debe existir
      para que un futuro refactor no vuelva a comparar el float crudo).
- [ ] **Caso de error — modelo no disponible**: `altman.disponible=False` /
      `altman_pp.disponible=False` / `magic.disponible=False` → todas las
      líneas de esa pregunta se muestran sin `= valor`, el bloque no se omite
      completo (solo el valor puntual falta, no el desglose entero — distinto
      del comportamiento de "🧮 Cuenta", que si omite la línea completa).
- [ ] **Caso de error — criterio Piotroski ausente del payload**: si
      `datos["criterios"]` no trae ninguna entrada con el `nombre` mapeado
      (desalineación entre `DESGLOSE_AVANZADO` y `_DESGLOSE_LETRA_A_NOMBRE_CRITERIO`,
      o ticker sin ese criterio evaluado), la línea se muestra sin `= valor`
      (no revienta con `KeyError`/`AttributeError`).
- [ ] **Caso de error — extractor lanza excepción**: forzar un `datos`
      malformado (ej. `datos["altman"]["a"]` como string no numérico) y
      confirmar que `_build_desglose_block` no propaga la excepción — la
      línea sale sin `= valor`, el resto del bloque se arma normal.
- [ ] **Alto riesgo de negocio — `mgr` con capital de trabajo negativo**:
      `current_assets < current_liabilities` (empresa con problemas de
      liquidez, caso real de negocio) → "Capital de Trabajo Neto" debe
      mostrar el valor negativo formateado en moneda (ej. `-$50,000.00`), no
      omitirse ni mostrarse en valor absoluto — es información real que un
      inversionista necesita ver tal cual.
- [ ] **Regresión — `MagicFormulaResult` no rompe consumidores existentes**:
      test que instancia `MagicFormulaResult(disponible=True, roic=0.2, ...)`
      **sin pasar** `current_assets`/`current_liabilities`/`ppe_net` y
      confirma que no lanza error (valen `None` por default) — cubre
      cualquier lugar del código (tests incluidos) que construya el
      dataclass con los campos viejos únicamente. Complementar con un test
      que llame `dataclasses.asdict(magic)` (mismo patrón que
      `advanced_command.py`) y confirme que el dict resultante trae las 3
      claves nuevas con `None` cuando no se pasaron, y con el valor real
      cuando sí.
- [ ] **Mejora recomendada de `security` (incorporada como criterio de
      aceptación)**: test `test_piotroski_letras_desglose_alineadas_con_mapeo_interno`
      que recorre las 9 letras que `ai_explain_content.desglose("avanzado", "pir"/"pia"/"pie")`
      expone y confirma, para cada una, que existe una entrada en
      `_DESGLOSE_LETRA_A_NOMBRE_CRITERIO` cuyo valor coincide con uno de los
      9 nombres canónicos de criterio que usa `PiotroskiResult`/
      `_PIOTROSKI_CUENTA_LABEL` (incluida la ya usada por "🧮 Cuenta", para
      partir de una lista de nombres canónicos existente y no inventar una
      nueva). Este test debe fallar en CI si algún día se agrega/renombra una
      letra en `DESGLOSE_AVANZADO` sin actualizar el diccionario en paralelo
      — es exactamente el desfase silencioso que `security` señaló como no
      bloqueante pero recomendado.

### Consistencia Cuenta vs. Desglose (punto explícito del pedido de Daniela)

- [ ] Para cada una de las 7 preguntas, el valor que aparece en la línea del
      Desglose para un término dado es **el mismo string formateado** (mismo
      número de decimales, mismo signo de moneda/porcentaje) que el que
      aparece para ese mismo término dentro de "🧮 Cuenta", usando la MISMA
      fixture de contexto en el mismo test — no basta con testear cada bloque
      por separado con fixtures distintas. Ejemplo concreto: en
      `test_cuenta_alz_verificada_termino_a_termino_ejemplo_de_daniela` el
      término A vale `0.34`; el test equivalente de Desglose debe usar el
      mismo `datos` y confirmar `"A (Capital de Trabajo) = 0.34"` en la
      línea. Evita que "🧮 Cuenta" y "🔍 Desglose" —que aparecen en el MISMO
      mensaje al usuario— muestren números distintos para el mismo término
      por un `_ratio2`/`_money` aplicado con precisión distinta en cada lado.
- [ ] Para `mgr`: el Desglose muestra "Capital de Trabajo Neto" (término
      separado) y "Activos Fijos Netos" (término separado); "🧮 Cuenta"
      sigue mostrando el ROIC ya combinado (`capital_invertido`). No son
      literalmente el mismo string en este caso puntual — el criterio de
      consistencia acá es aritmético, no textual: `current_assets -
      current_liabilities` (Desglose) + `ppe_net` (Desglose) debe sumar
      exactamente el `capital_invertido` que usa "🧮 Cuenta", con la fixture
      compartida.

### Testabilidad

- [ ] `_valor_desglose_alz/azp/piotroski/mgr/mge` son funciones puras
      (`str, dict -> Optional[str]`), invocables directo en el test sin
      mockear nada — mismo criterio ya validado para `_cuenta_*` y
      `_build_dato_line`.
- [ ] `_build_desglose_block(kind, question_code, datos)` sigue sin I/O ni
      `await` — test que confirma `not inspect.iscoroutinefunction(...)`
      (mismo patrón que `test_build_cuenta_line_es_funcion_pura_sin_io`).
- [ ] El guard de integridad (`ai_rewrite.protected_tokens`) es verificable
      sin llamar a Ollama: test que arma `datos_del_contexto` con los 3
      campos nuevos de `mgr`, deriva `datos_tokens`, y confirma que
      `_build_desglose_block` nunca se pasa como argumento a la función que
      arma el prompt de Ollama (inspección de la firma real de
      `_fetch_explanation`/`_build_explain_payload` en el call site, no un
      mock — es una aserción estructural sobre el orden de llamadas ya
      existente en `handle_explain`).
- [ ] Los 3 campos nuevos de `MagicFormulaResult` se prueban en
      `test_advanced_scoring.py` (no en `test_ai_explain.py`) — mismo
      criterio de separación de módulos que ya existe en el repo.

### Fixtures mínimos que faltan

1. **`_avanzado_context` ampliada** (`tests/test_ai_explain.py`, línea
   176-242): el `magic=` del fixture hoy no incluye `current_assets`/
   `current_liabilities`/`ppe_net`. Agregar valores consistentes con
   `capital_invertido=570_000.0` ya fijado ahí, por ejemplo
   `current_assets=650_000.0, current_liabilities=150_000.0, ppe_net=70_000.0`
   (→ `(650_000 - 150_000) + 70_000 = 570_000`, no rompe
   `test_cuenta_mgr_verificada_termino_a_termino` ni ningún otro test que ya
   dependa de ese fixture).
2. **Fixture Piotroski "criterio ausente del payload"**: variante de
   `_avanzado_context(piotroski=...)` con la lista `criterios` recortada
   (falta una de las 9 entradas) — para el caso de error de criterio
   ausente.
3. **Fixture `MagicFormulaResult` con capital de trabajo negativo**: balance
   sintético en `test_advanced_scoring.py` con `totalCurrentAssets <
   totalCurrentLiabilities` — no existe hoy ningún caso de prueba con capital
   de trabajo negativo para Magic Formula (los existentes asumen negocio
   sano).
4. **Fixture "extractor con dato malformado"**: `datos["altman"] = {"disponible":
   True, "a": "no-es-numero", ...}` — no existe hoy un caso de tipo inválido
   para los extractores de Desglose (la suite actual solo cubre
   disponible/no disponible).
5. **Lista canónica de nombres de criterios Piotroski para el test de
   alineación**: reutilizar la lista ya usada por
   `_PIOTROSKI_CUENTA_LABEL`/`_avanzado_context` (los 9 nombres: `roa_positivo`,
   `cfo_positivo`, `roa_creciente`, `cfo_mayor_utilidad`,
   `apalancamiento_decreciente`, `liquidez_creciente`, `sin_dilucion`,
   `margen_bruto_creciente`, `rotacion_activos_creciente`) — no crear una
   lista nueva en el test, importar/derivar de la fuente existente para que
   el test detecte también un desfase en esa fuente.

### Qué NO se prueba en este run (y por qué)

- **Formato de fórmula A-E de Altman/Piotroski/Magic Formula en sí** (los
  coeficientes `1.2×A + 1.4×B + ...`, los umbrales de cada criterio, la
  fórmula de ROIC/EV): ya cubierto exhaustivamente por
  `test_advanced_scoring.py` y por la suite de "🧮 Cuenta"
  (`SDD_explicacion_paso_a_paso.md`) — esta spec no cambia ninguna fórmula,
  solo expone/lee valores ya calculados.
- **Contenido textual fijo de `DesgloseTermino`/`DESGLOSE_AVANZADO`** (nombre
  de cada letra, `campo_origen`, `que_mide`): ya cubierto por
  `test_desglose_avanzado_7_entradas_exactas` y
  `test_desglose_entrada_sin_terminos_vacios_ni_none` — sin cambios en
  `ai_explain_content.py` en esta spec (confirmado en "Artefactos a
  crear/modificar").
- **`_MAX_DESGLOSE_CHARS=1200` como mecanismo** (por qué 1200, cómo se
  trunca): ya cubierto por la suite existente citada arriba — esta spec solo
  documenta que el margen sigue siendo amplio con los valores agregados, no
  cambia el mecanismo. Sí se re-corre la suite existente para confirmar que
  sigue verde (ver tabla de trazabilidad), pero no se diseñan casos nuevos
  de longitud.
- **Orden `_build_leaf_message` (header → Dato → Cuenta → Desglose →
  respuesta → Fórmula/Fuente → disclaimer)**: ya cubierto por
  `test_build_leaf_message_inserta_desglose_entre_cuenta_y_respuesta` y
  `test_mensaje_paso_a_paso_muestra_cuenta_y_desglose_en_orden` — esta spec
  no toca el orden, solo el contenido de la línea de Desglose.
- **"📊 Ver dato" sigue sin mostrar Desglose**: ya cubierto por
  `test_ver_dato_nunca_incluye_desglose_ni_para_las_7_preguntas_con_desglose`
  — sin cambios de superficie en esta spec (confirmado en Restricciones).
- **Rendimiento/carga**: feature de formateo de texto puro, sin llamada
  nueva a red ni a BD — no aplica performance testing (Riesgo bajo según la
  tabla de priorización del skill de QA).

### Criterio de exit de QA

- Todos los tests pasan (BUILD SUCCESS / suite verde), incluida la suite
  completa existente de "🧮 Cuenta"/"🔍 Desglose" (sin regresiones).
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (funciones puras sin I/O — no debería
  haber ninguna fuente de flakiness; si aparece una, es señal de que algo
  quedó no-determinístico y se trata como bloqueante, no como flaky
  tolerable).
- Los 3 campos nuevos de `MagicFormulaResult` no requieren ningún cambio en
  tests de `advanced_command.py` para seguir pasando (confirma en código el
  punto 3 de la Revisión de seguridad).

**Spec lista para `implementer`**: sí — los 4 puntos del pedido de Daniela
tienen cobertura de test definida punto por punto, la mejora recomendada de
`security` quedó incorporada como criterio de aceptación obligatorio (no
opcional), y los fixtures faltantes están identificados con valores
concretos que no rompen ningún test existente.
