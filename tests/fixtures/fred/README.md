# Fixture de FRED (serie DGS20)

**Origen: sintético**, escrito a mano imitando la forma real de la respuesta
de `https://api.stlouisfed.org/fred/series/observations` (documentada en
https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
incluyendo el caso de un día sin dato (`"value": "."`, feriado/fin de
semana) que FRED reporta así. No es una captura real (sin acceso a red/API
key real en este entorno de implementación).

- `dgs20.json` — 3 observaciones recientes de la serie `DGS20`, la más
  reciente con valor `"4.40"` (4.40%).
