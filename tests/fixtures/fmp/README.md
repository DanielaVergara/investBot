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
