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
- `PER promedio de peers = 32.9` (peers sintéticos con PE 30.0/33.0/35.7,
  expresado en `peers_metrics_ttm.json` como `earningsYieldTTM` = 1/PE — la
  API stable de FMP ya no expone `pe` directo en `/quote`, ver
  `fmp_client.get_key_metrics_ttm`/`peers.get_peer_pe_average`).
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

**Actualización 2026-07-28:** al desplegar en el VPS con una key real se
confirmó que la API legacy (`/api/v3/...`) fue discontinuada por FMP para
cuentas nuevas — el bot migró a la API "stable" (`/stable/...`, ticker vía
`symbol=`). Los **nombres de campo** de este fixture (`totalCurrentAssets`,
`operatingCashFlow`, `capitalExpenditure`, `eps`, `netIncome`,
`earningsYieldTTM`, etc.) se verificaron contra respuestas reales de
`/stable/quote`, `/profile`, `/income-statement`, `/balance-sheet-statement`,
`/cash-flow-statement` y `/key-metrics-ttm` para AAPL — siguen siendo
sintéticos en sus **valores** (siguen reproduciendo el caso Adobe de la spec),
pero la **forma** del JSON ya no es hipotética.

## Fixtures agregados por `SDD_contenido_financiero_explicado.md` (2026-07-29)

**Origen: sintético**, no capturas reales — mismo criterio que el resto de este archivo.

- `key_metrics_own.json` — respuesta de `/key-metrics` (anual, `limit=1`)
  para el **ticker propio** (`ADBE`), a diferencia de `peers_metrics.json`
  (que solo cubre los peers `MSFT`/`ORCL`/`CRM`). Usado para probar
  `rules.extract_key_metrics_extras` end-to-end. Los valores de
  `roe`/`debtToEquity`/`netDebtToEBITDA`/`dividendYield`/`payoutRatio` son
  inventados para poblar los 5 campos (Adobe real no reparte dividendos;
  acá se usa un `dividendYield` positivo a propósito para cubrir la rama
  "> 0" del bullet, la rama "== 0" se cubre con un test unitario aparte en
  `test_rules.py`/`test_summary.py`).
- `quote_vix.json` — respuesta de `/quote?symbol=^VIX`, con un `price`
  (`18.42`) deliberadamente distinto del precio de ADBE (`333.00`) para que
  el test end-to-end pueda verificar que el VIX mostrado no es, por error
  de ruteo del fixture, el precio de Adobe (Gap #2 de la sección QA de la
  spec).
