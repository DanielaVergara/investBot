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

## `SDD_analisis_fundamental_avanzado.md` — fixtures de `/avanzado`

**Origen: sintético** para todos los fixtures de esta sección, escritos a
mano imitando la forma real ya confirmada de `balance_/income_/cash_flow_
quarterly_nvda_real.json` de arriba (mismos nombres de campo, `period=
"annual"` en vez de `"quarter"`, 2 elementos por array — año más reciente
primero, mismo orden que devuelve FMP). Usados por
`tests/test_advanced_command.py` (integration, `httpx.MockTransport`).

- `profile_empresa_completa.json` + `quote_empresa_completa.json` +
  `balance_/income_/cash_flow_annual_empresa_completa.json` — caso "todo
  disponible", empresa manufacturera clásica (`sector="Industrials"`, fuera
  de la lista asset-light de D4) — happy path de los 5 modelos con Altman Z
  **original** (sin Z''), Piotroski 9/9, Magic Formula y los 4 factores AQR
  todos calculables.
- `profile_empresa_asset_light.json` + `quote_empresa_asset_light.json` +
  `balance_/income_/cash_flow_annual_empresa_asset_light.json` — empresa de
  software (`sector="Technology"`, matchea D4) — mismo happy path, pero
  dispara el cálculo adicional de Z'' (Altman Z original **y** Z'', ambos
  mostrados).
- `profile_etf_spy.json` — **ticker SPY**, con `isEtf=true` (campo propuesto
  por `architect` para D6). **Pendiente de verificar con `curl` real** contra
  `/profile?symbol=SPY` — no se pudo confirmar en este entorno de
  implementación (mismo bloqueo de red ya documentado en la spec: `site.
  financialmodelingprep.com` devolvió HTTP 403 a fetch/búsqueda automatizada
  durante la investigación de la spec). Si el campo real de FMP no es
  `isEtf`, `advanced_command._is_etf_or_fund` deja de detectar este caso por
  el campo de `/profile`, pero la red de seguridad del paso 4 (estados
  financieros vacíos) sigue rechazando el ticker igual — el comando sigue
  siendo correcto, solo más costoso en requests para este caso puntual (ver
  Decisión de diseño #2 de la spec).

Los demás casos de la spec ("ticker inexistente" = `/profile` vacío, "sin
estados financieros propios" = balance/income/cash-flow vacíos para un
ticker sin flag de ETF/fondo) se simulan inline en
`tests/test_advanced_command.py` con un router que devuelve `[]`/`None` —
no requieren un fixture propio (mismo criterio que
`test_fetch_and_analyze_datos_incompletos_mensaje_claro` en
`tests/test_query_handler.py`). Las variantes con un campo faltante para
cada modelo (Altman/Piotroski/Magic Formula/factores AQR) tampoco requieren
fixture: son funciones puras de `advanced_scoring.py` testeadas con dicts
armados a mano en `tests/test_advanced_scoring.py` (mismo criterio que
`tests/test_rules.py`/`tests/test_valuation.py`, sin fixtures de archivo
para tests unitarios de funciones puras).
