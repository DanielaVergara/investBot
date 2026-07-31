# Fixtures genéricos de FMP

**Origen: sintético**, escrito a mano imitando la forma real de las
respuestas de `/quote` y `/search` de Financial Modeling Prep (documentada en
https://site.financialmodelingprep.com/developer/docs). No son una captura
real (sin acceso a red/API key real en este entorno de implementación).

- `quote_adbe.json` — forma de `/quote/ADBE`, valores consistentes con
  `tests/fixtures/adobe/quote.json`.
- `search_single_match.json` — `/search` con una sola coincidencia exacta.
- `search_multiple_matches.json` — `/search` con 6 coincidencias (para
  probar el truncado a 5 botones inline).
- `search_no_matches.json` — `/search` sin resultados (`[]`).

Antes de producción real, reemplazar por una captura real con API key válida
si se quiere verificar la forma exacta de la respuesta de FMP (pendiente
documentado en el reporte de cierre de `implementer`).

- `402_symbol_premium_real.txt` — **Origen: real**, capturado con `curl`
  contra `/quote` para los tickers MELI y DRAM (respuesta 402 idéntica en
  ambos). Body en texto plano (no JSON) — usado para el sub-caso "símbolo
  premium" de la spec `SDD_fmp_402_simbolo_premium.md`.
- `402_generic_payment_required.json` — **Origen: sintético**, `{"error":
  "Payment Required"}` — representa un 402 genérico no reconocido (regresión
  explícita: no debe activar el mensaje de "símbolo premium").
- `income_statement_quarterly_nvda_real.json` — **Origen: real**, capturado
  con `curl` contra `/income-statement?symbol=NVDA&period=quarter&limit=4`
  el 2026-07-31. Confirma que `period="quarter"` **sí está disponible en el
  plan gratuito** (sin 402) y que los nombres de campo (`netIncome`, `eps`,
  `epsDiluted`, `weightedAverageShsOut(Dil)`, `period`, `fiscalYear`) son
  idénticos a los de la respuesta anual — usado para verificar los supuestos
  de `SDD_eps_ttm_real.md`.
- `balance_sheet_quarterly_nvda_real.json` — **Origen: real**, capturado con
  `curl` contra `/balance-sheet-statement?symbol=NVDA&period=quarter&limit=4`
  el 2026-07-31. Confirma `period="quarter"` disponible en el plan gratuito
  para este endpoint también (sin 402); campos (`totalCurrentAssets`,
  `totalCurrentLiabilities`, `shortTermDebt`, `longTermDebt`, etc.)
  idénticos a la respuesta anual.
- `cash_flow_quarterly_nvda_real.json` — **Origen: real**, capturado con
  `curl` contra `/cash-flow-statement?symbol=NVDA&period=quarter&limit=4`
  el 2026-07-31. Confirma `period="quarter"` disponible en el plan gratuito
  para este endpoint también (sin 402); campos (`operatingCashFlow`,
  `capitalExpenditure`, `freeCashFlow`, `netIncome`) idénticos a la
  respuesta anual.
