# Fixture Adobe (ADBE) — caso de regresión

**Origen: sintético/reconstruido, NO es una captura real de la API de FMP/FRED.**

Este entorno de implementación no tuvo acceso a una API key real de FMP ni
a red saliente para capturar datos en vivo (sandbox sin acceso a
`financialmodelingprep.com`/`api.stlouisfed.org`). Los valores de estos JSON
fueron **reconstruidos matemáticamente hacia atrás** para que, al pasar por
`rules.py`/`valuation.py` tal como quedaron implementados, reproduzcan
exactamente el caso de referencia documentado en la spec
(`SDD_investbot_mvp.md`, sección "Reglas de validación de empresa"):

> Múltiplos=658, DCF=289, EPS Model=555 → promedio=500 vs precio de mercado 333 → "barata"

Verificado con el código real (no solo a mano) — ver
`tests/test_valuation.py::test_valuation_adobe_regression`. Resultado real al
correr la suite:

| Modelo | Target spec | Valor obtenido | Diff % |
|---|---|---|---|
| Múltiplos | 658 | 658.00 | 0.00% |
| Graham EPS Model | 555 | 555.64 | 0.12% |
| DCF | 289 | 288.82 | -0.06% |
| Promedio (Valor Justo Total) | 500 | 500.82 | 0.16% |

Todos dentro de la tolerancia ±1% exigida por `qa`.

## Cómo se construyeron los números (trazabilidad)

- `EPS_TTM = 20.00` (año fiscal más reciente), historial de 5 años
  `[13.84, 15.17, 16.63, 18.23, 20.00]` → CAGR (`g`) ≈ 9.64%.
- `PER promedio de peers = 32.9` (peers sintéticos con PE 30.0/33.0/35.7).
  → Múltiplos = 20.00 × 32.9 = 658.00.
- `Y = 0.044` (4.4%, plausible para el rendimiento del bono del tesoro EEUU a
  20 años) → Graham = 20.00 × (8.5 + 2×9.64) × 4.4 / 4.4 ≈ 555.64.
- FCF histórico de 5 años terminando en `$8,000,000,000` con CAGR propio del
  8%, descontado a un WACC de ≈10.26% (derivado de `beta=1.1`,
  `total_debt=$4,000,000,000`, `interest_expense=$150,000,000`,
  `income_tax_expense/income_before_tax≈19%`, `market_cap=$153,180,000,000`
  con `price=$333.00` y `shares_outstanding=460,000,000`), con crecimiento
  terminal de 2.5% → DCF ≈ $288.82/acción.
- Precio de mercado fijado en `$333.00` (el mismo valor que usa la spec en
  su caso de referencia) → clasificación "barata".

**Antes de ir a producción real**, este fixture debe reemplazarse por una
captura real de FMP/FRED con una API key válida — queda documentado como
pendiente explícito (ver reporte de cierre de `implementer`).
