# InvestBot

Bot de Telegram **personal** (un único usuario) para análisis fundamental de
acciones. Recibe un ticker o nombre de empresa, trae datos financieros crudos
de Financial Modeling Prep (plan gratuito), calcula un "valor justo" con un
motor propio de valoración (Múltiplos, Graham EPS Model, DCF), evalúa los
pilares de "buena empresa" y dice si la acción encaja con tu perfil de
riesgo — todo en formato "explícamelo como si fuera tonto".

`InvestBot` es un nombre de repo/paquete provisional — el nombre final del
bot en Telegram (y su `@username`) se define al crearlo en @BotFather (ver
`contexto/referencia/SETUP_TELEGRAM_BOT.md`); no bloquea el funcionamiento
del código.

---

## Plan de FMP requerido y por qué

**Plan gratuito de Financial Modeling Prep, $0/mes.** El diseño completo de
este bot funciona sobre el plan gratuito — no hay "modo premium" ni "modo
degradado", un único diseño calculado con datos crudos gratuitos.

**Límite del plan gratuito: 250 requests/día.**

**Base URL: la API "stable" de FMP (`https://financialmodelingprep.com/stable/...`,
ticker vía query param `symbol=`).** La API legacy (`/api/v3/...`, ticker en
el path) fue discontinuada por FMP para cuentas creadas después del
31/08/2025 — verificado en el despliegue real con una key nueva (cualquier
llamada a `/api/v3/...` devuelve `"Legacy Endpoint"`, no es un problema de key
inválida). El migrado a `stable` no cambia el diseño ni el presupuesto de
requests, solo la forma de la URL.

Endpoints de datos crudos usados (todos gratuitos):
- `/quote`
- `/profile` (sector, beta, market cap)
- `/income-statement` — **trimestral primero** (`period=quarter`), con
  fallback anual condicional si la trimestral falla o resulta insuficiente
  (ver "EPS TTM real y datos trimestrales" más abajo).
- `/balance-sheet-statement` — **trimestral primero** (snapshot del trimestre
  más reciente, `limit=1`), con el mismo fallback anual condicional.
- `/cash-flow-statement` — **trimestral primero**, con el mismo fallback
  anual condicional.
- `/key-metrics` (anual — usado también para el PER de peers, ver más abajo)
- `/search-symbol` (resolución nombre → ticker)

Los 3 endpoints trimestrales están **confirmados con `curl` real** contra el
plan gratuito (`period=quarter`, sin 402) — ver
`tests/fixtures/fmp/README.md`. El fallback anual (idéntico al
comportamiento del bot antes de esta migración) sigue existiendo como red de
seguridad ante un fallo puntual (rate limit, timeout, cambio de política de
FMP), no porque haya quedado ninguna duda de disponibilidad.

**Nunca se depende de** `/dcf`, `/sector-pe-ratio` ni `/treasury-rates`
(endpoints precalculados del tier pago/premium, disponibilidad gratuita no
verificada). Tampoco de `/key-metrics-ttm`: verificado con una key real que
es un endpoint **de pago** en el plan gratuito actual de FMP (`402 Payment
Required`), a pesar de que la documentación pública no lo deja claro. En su
lugar:
- El PER promedio "de sector" se aproxima con un set de **3-5 peers**
  (`src/investbot/peers.py`), documentado en cada respuesta según de dónde
  salió la lista de candidatos esta consulta — ver "Fuente de peers
  dinámicos (Finnhub)" más abajo. La API stable ya no expone un campo `pe`
  directo en `/quote` (deprecado junto con la API legacy) — el PER de cada
  peer se deriva como `1 / earningsYield` desde `/key-metrics` **anual** (la
  variante TTM da un PER más "en vivo" pero es de pago — ver arriba). Mismo
  presupuesto de requests que la versión anterior (1 llamada por peer).
- El DCF se calcula internamente (proyección de FCF + WACC simplificado +
  valor terminal por perpetuidad de Gordon Growth), sin llamar a `/dcf`.
- La tasa libre de riesgo (Y) **no viene de FMP** — ver siguiente sección.

### Presupuesto de requests por consulta

`/income-statement`, `/balance-sheet-statement` y `/cash-flow-statement`
pasaron de "1 llamada anual fija" a "1 llamada trimestral primaria, con
fallback condicional a la llamada anual de siempre solo si la trimestral
falla o resulta insuficiente" — el presupuesto **no aumenta en el camino
feliz** (las 3 fuentes trimestrales responden con datos suficientes), y sube
hasta 3 llamadas extra solo en el peor caso (las 3 fallan a la vez):

| Llamada | Cantidad (camino típico) | Cantidad (peor caso — las 3 fuentes trimestrales fallan) | Endpoint |
|---|---|---|---|
| Datos propios del ticker | **6** | **9** | `/quote`, `/profile`, `/income-statement` (trimestral, TTM + historial), `/balance-sheet-statement` (trimestral), `/cash-flow-statement` (trimestral, TTM + historial), `/key-metrics` |
| Resolución nombre→ticker (solo si no mandaste el ticker exacto) | 0-1 | 0-1 | `/search` |
| Peers para el modelo de Múltiplos | 3-5 | 3-5 | `/key-metrics` (anual) por peer — el "3-5" ahora es real: con Finnhub configurado y respondiendo, puede haber hasta 5 candidatos dinámicos (`MAX_PEERS_DINAMICOS`); sin Finnhub (o si no llega al mínimo de 3), son exactamente 3, los del respaldo fijo por sector |
| Contexto de mercado (VIX, no depende del ticker consultado) | 1 | 1 | `/quote (symbol=^VIX)` |
| **Total por consulta completa** | **10-13** | **13-16** | |

En el caso típico (las 3 fuentes trimestrales responden), el presupuesto de
requests no cambia respecto a la versión anterior del bot: 10-13 por
consulta, **~19 a ~25 consultas/día** con 250 requests/día. En el peor caso
(FMP deja de servir `period=quarter` para alguno de los 3 endpoints propios
del ticker de forma simultánea, algo no observado en producción hasta la
fecha), sube a 13-16 por consulta, **~15 a ~19 consultas/día** — sigue muy
por encima del uso esperado de un único usuario.

No hay caché ni rate-limit adicional a nivel de aplicación más allá de un
límite defensivo de 10 consultas/minuto (protección contra bugs propios, no
un límite de negocio) — este límite ahora se chequea en el único
choke-point real que dispara un análisis completo (`_run_analysis`,
independientemente de si la consulta llegó por texto libre o por el botón
final del flujo interactivo, ver más abajo).

### EPS TTM real y datos trimestrales (income-statement/balance-sheet/cash-flow)

El bot deriva `EPS (TTM)`, el ratio P/S, el margen bruto y los inputs del
WACC de una suma de los **últimos 4 trimestres reales** (`period=quarter`)
en vez de un solo reporte anual — un EPS/PER "desactualizado" según el año
fiscal más reciente ya no es posible mientras la fuente trimestral esté
disponible. Los pilares de crecimiento (ingresos/utilidades) y el CAGR de
Graham/DCF también usan la serie trimestral cruda (no un TTM móvil) cuando
está disponible, con la ventana de historial (12 o 20 trimestres) elegida
por vos en cada consulta — ver el flujo de botones más abajo.

Detalles técnicos (para quien audite el código, no necesarios para usar el
bot):
- **P/S y el costo de la deuda (Kd) del WACC siempre usan cifras TTM**, nunca
  un solo trimestre suelto — de lo contrario saldrían ~4x distorsionados
  (un stock de balance combinado con el flujo de un solo trimestre no
  produce una tasa/ratio anual coherente).
- El **FCF que ancla la proyección del DCF a 5 años es el FCF TTM** (suma de
  los últimos 4 trimestres) — el CAGR que mide la *tendencia* del FCF sigue
  midiéndose sobre el historial crudo (nivel y tendencia son 2 preguntas
  distintas).
- **Diseño atómico por endpoint**: si algún campo de los 4 trimestres más
  recientes de un endpoint no es válido, ese endpoint completo cae al
  fallback anual de siempre — nunca se mezclan campos trimestrales y
  anuales dentro del mismo cálculo.
- **Riesgo de estacionalidad aceptado**: comparar 2 trimestres crudos (en
  vez de una serie de TTM móvil) puede heredar ruido estacional en los
  pilares de crecimiento/CAGR — riesgo conocido y aceptado, no resuelto en
  esta versión.

### Escenario de Valor Justo y ventana de historial — elección por consulta

Cada consulta de ticker pregunta, con botones inline, **qué escenario de
Valor Justo mostrar resaltado** (Pesimista / Conservador / Optimista — los 3
siempre se calculan y se muestran, la elección solo resalta uno) y **cuánto
historial trimestral usar** (Corto plazo = 12 trimestres/3 años, Largo plazo
= 20 trimestres/5 años) antes de correr el análisis completo. Es un flujo
sin estado de servidor (todo viaja codificado en los botones) — elegir un
escenario no cuesta ningún request adicional; elegir la ventana sí determina
el `limit=` real de las 3 llamadas trimestrales de esa consulta puntual. El
pilar "Precio razonable" del checklist de buena empresa siempre se evalúa
contra el escenario Conservador, sin importar qué botón hayas apretado —
es una vara estable entre consultas, no cambia según tu elección de esa vez.

**Peers dinámicos (Finnhub) y eventos corporativos (SEC EDGAR) son
proveedores completamente aparte** — ninguna de las 2 features nuevas
consume el presupuesto de 250 req/día de FMP, mismo principio que FRED/
Treasury.gov (ver secciones dedicadas más abajo).

## Fuente de Y (tasa libre de riesgo, bono del tesoro EEUU 20 años)

**No viene de FMP.** Fuente primaria: **FRED** (Federal Reserve Economic
Data), serie `DGS20`, vía su API REST pública. Requiere una API key gratuita
de FRED (registro instantáneo, sin costo, sin tarjeta:
https://fred.stlouisfed.org/docs/api/api_key.html).

**Fallback si FRED no responde o la key no está configurada:** feed público
de Treasury.gov (Daily Treasury Par Yield Curve Rates, CSV, sin API key).

Si ninguna de las dos fuentes responde, el bot lo reporta explícitamente —
nunca usa un valor hardcodeado en silencio. Esta llamada **no consume el
cupo de 250 req/día de FMP** (proveedor distinto).

## Fuente de peers dinámicos (Finnhub)

**Opcional — el bot funciona igual sin configurarla.** Fuente primaria de la
lista de peers candidatos por ticker: **Finnhub** (`GET /stock/peers`,
`grouping=subIndustry`), proveedor gratuito distinto de FMP, sin tarjeta
(free tier: 60 llamadas/minuto). Requiere `FINNHUB_API_KEY` (registro
gratuito en https://finnhub.io).

**Fallback automático y silencioso** — nunca un error visible en el chat —
a la lista fija por sector ya existente (`src/investbot/peers.py::
PEERS_BY_SECTOR`, mantenimiento manual, sigue existiendo tal cual) en
cualquiera de estos casos: `FINNHUB_API_KEY` no configurada, la llamada
falla (402/403/429/timeout/red/JSON inesperado), o Finnhub devuelve menos
de 3 peers válidos tras excluir el propio ticker (`MIN_PEERS_DINAMICOS_PARA_USAR`).
Nunca se combinan ambas fuentes en la misma consulta — es una u otra. Tope
de candidatos dinámicos consultados contra FMP: 5 (`MAX_PEERS_DINAMICOS`).

Cada respuesta indica explícitamente, en la nota de transparencia, cuál de
las 2 fuentes se usó esta consulta — el PER individual de cada peer lo sigue
calculando FMP exactamente igual sea cual sea la fuente de la lista
(`1 / earningsYield` vía `/key-metrics` anual, ver arriba).

Finnhub tiene antecedente documentado (GitHub issue
`finnhubio/Finnhub-API#271`) de mover endpoints de gratis a pago sin
aviso — ese issue nombra "Dividends"/"Major Developments", no "Company
Peers", pero el bot trata cualquier fallo de Finnhub exactamente igual
(fallback silencioso), sin distinguir "todavía gratis" de "ya no lo es".

## Fuente de eventos corporativos (SEC EDGAR)

**Opcional — el bot funciona igual sin configurarla.** Cuando una empresa
tiene un evento material (cambio de directivos/ejecutivos, contrato
importante, litigio relevante, posible bancarrota), está obligada por ley a
presentar un formulario **8-K** ante la SEC. El bot lista, por consulta,
hasta 5 eventos de 8-K de los últimos 180 días con al menos un Item
relevante (`1.01`, `1.02`, `1.03`, `5.02`, `8.01`).

**Endpoints usados, ambos gratis, sin API key, sin login:**
- `https://www.sec.gov/files/company_tickers.json` — mapeo ticker → CIK,
  cacheado en memoria 24h (archivo estático de cientos de KB).
- `https://data.sec.gov/submissions/CIK{cik10}.json` — historial de filings
  de una empresa, incluye 8-K con fecha y códigos de Item.

**Rate limit: 10 req/seg por IP, sin límite diario.** Requiere el header
`User-Agent` con contacto identificable (`SEC_EDGAR_USER_AGENT`, formato
`"<nombre/app> <email de contacto>"`) — no es opcional, es un requisito de
acceso justo de la SEC. Si `SEC_EDGAR_USER_AGENT` no está configurada, la
feature completa se omite (nunca se manda una request sin el header).

**El bot NO resume el contenido legal del 8-K con NLP/LLM** — decisión
explícita: un resumen incorrecto de un litigio o un cambio de gerencia es
peor que no mostrar nada, y este proyecto no tiene ningún componente de
NLP/LLM hoy. Se muestra fecha + tipo de evento (Item) + link directo al
filing público en `sec.gov` para leerlo vos misma si te interesa.

Ninguna de las 2 features nuevas de esta sección consume el cupo de 250
req/día de FMP — proveedores distintos, mismo principio que FRED/
Treasury.gov.

---

## Arquitectura (resumen)

- Python 3.12 + `python-telegram-bot` v21 (async) + `httpx` + SQLite (stdlib).
- **Long polling**, no webhook: sin dominio, sin TLS, sin ruta en Traefik,
  cero puertos públicos expuestos.
- SQLite de un solo archivo (`/data/investbot.db` en el contenedor), una
  única fila lógica de perfil de riesgo.
- Servicio Docker separado de FoodMindAI — sin red ni base de datos
  compartida.

```
Daniela (Telegram) <--long polling--> investbot-bot (Docker) --HTTPS--> FMP
                                                          `----HTTPS--> FRED / Treasury.gov
                                                          `----HTTPS--> Finnhub (opcional, peers dinámicos)
                                                          `----HTTPS--> SEC EDGAR (opcional, eventos corporativos)
                                                          `----lectura/escritura--> SQLite (volumen)
```

---

## Arranque local (dev)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# completar TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_ID, FMP_API_KEY, FRED_API_KEY
# FINNHUB_API_KEY y SEC_EDGAR_USER_AGENT son opcionales — sin ellas el bot
# arranca igual, solo se omiten peers dinámicos / eventos corporativos.

python -m investbot.bot
```

O con Docker Compose (dev):

```bash
cp .env.example .env   # completar valores reales
docker compose up --build
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest -v --cov=src/investbot --cov-report=term-missing --cov-fail-under=75
```

La suite no requiere `FMP_API_KEY`/`FRED_API_KEY`/`TELEGRAM_BOT_TOKEN` reales
ni acceso a red — todas las llamadas HTTP se mockean con
`httpx.MockTransport` sobre fixtures en `tests/fixtures/`.

---

## Despliegue (producción, VPS)

InvestBot corre como un contenedor Docker **separado** de FoodMindAI, sin
compartir red, base de datos ni contenedores.

```bash
# En el VPS, dentro del directorio del proyecto:
cp .env.example .env
chmod 600 .env   # mismo patrón que FoodMindAI — propietario sin privilegios root
# completar .env con los valores reales

docker compose -f docker-compose.prod.yml up -d --build
```

- **No expone ningún puerto público ni ruta en Traefik** — el contenedor solo
  hace conexiones salientes (a Telegram, FMP, FRED/Treasury.gov, y
  opcionalmente Finnhub/SEC EDGAR).
- El perfil de riesgo persiste tras `docker compose restart` (volumen Docker
  `investbot_data`).
- Límites de memoria (`mem_limit: 256m`) y CPU (`cpus: 0.5`) fijados en
  `docker-compose.prod.yml` para no afectar la disponibilidad de FoodMindAI
  en el mismo VPS.
- Antes de arrancar el contenedor, hay que crear el bot en @BotFather y
  obtener el token — ver `contexto/referencia/SETUP_TELEGRAM_BOT.md`.

---

## Seguridad — puntos relevantes

- **Control de acceso:** un único `chat_id` autorizado
  (`TELEGRAM_ALLOWED_CHAT_ID`), validado como handler global de máxima
  prioridad que cubre todos los tipos de update (mensajes y botones inline),
  y que además valida que el chat sea privado. Si la variable no está
  seteada o no es un entero válido, **el proceso falla al arrancar**
  (fail-closed) — nunca arranca en modo permisivo.
- **Secretos:** `TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, `FRED_API_KEY`,
  `FINNHUB_API_KEY` se leen solo de variables de entorno (`.env`, `chmod
  600`, nunca en git). `SEC_EDGAR_USER_AGENT` no es un secreto para la SEC
  en sí (es un identificador de contacto, no una credencial), pero sigue el
  mismo tratamiento que el resto de las variables — vacía en `.env.example`
  (repo público), solo el valor real en el `.env` local. Los loggers de
  `httpx`/`httpcore`/`telegram` se fijan a `WARNING` en producción — nunca
  `DEBUG`, porque el token de Telegram viaja en el path de la URL y las API
  keys de FMP/FRED/Finnhub como query param; un log en `DEBUG` los filtraría
  en texto plano.
- **Manejo de errores:** ninguna excepción cruda de `httpx` (que puede
  incluir la URL completa con la API key) se propaga hacia los logs ni hacia
  Telegram — se traduce siempre a un mensaje sanitizado.

### Riesgo residual aceptado (no es un bug del bot)

Si la cuenta de Telegram de Daniela sufre un secuestro de sesión (SIM-swap,
robo de sesión), el atacante hereda el mismo `chat_id` autorizado y el
filtro del bot lo deja pasar legítimamente — esto es un riesgo sistémico de
la plataforma Telegram, fuera del control del código del bot. Mitigación
recomendada (fuera de scope de código): activar verificación en dos pasos
en la app de Telegram de Daniela. Ver también
`contexto/referencia/SETUP_TELEGRAM_BOT.md` para el procedimiento de
respuesta a incidente si el **token** (no la cuenta) se compromete.

---

## Fuera de scope del MVP

Sin caché de datos de FMP, sin histórico de consultas, sin alertas de
precio, sin multi-idioma, sin dashboard web, sin autenticación
multi-usuario (el único control de acceso es el `chat_id` fijo).

---

## Estructura del proyecto

```
src/investbot/
  bot.py             entrypoint, Application en modo long polling
  security.py        filtro global de chat_id, rate-limit en memoria
  onboarding.py       ConversationHandler de 8 preguntas + scoring de perfil
  query_handler.py    handler de texto libre, orquesta todo el análisis
  fmp_client.py        wrapper HTTP a FMP stable (solo endpoints gratuitos)
  peers.py             peers por sector (dinámicos vía Finnhub, respaldo fijo) + promedio de PER (vía key-metrics anual)
  finnhub_client.py     wrapper HTTP a Finnhub (peers dinámicos, opcional)
  treasury_client.py   FRED (DGS20) + fallback Treasury.gov
  sec_edgar_client.py   wrapper HTTP a SEC EDGAR (mapeo ticker→CIK cacheado + submissions, opcional)
  corporate_events.py   extracción pura de eventos 8-K relevantes desde submissions
  rules.py              ratios financieros + pilares de buena empresa
  valuation.py           motor propio de valoración (3 modelos + promedio parcial)
  risk_fit.py            encaje beta vs perfil de riesgo
  market_context.py       momentum de precio + comparación con peers + VIX
  summary.py               texto dummy-friendly con las analogías de Daniela
  db.py                     SQLite (tabla risk_profile, fila única)
tests/
  fixtures/adobe/     caso de regresión (ver tests/fixtures/adobe/README.md)
  fixtures/fmp/       fixtures genéricos de FMP (quote, search)
  fixtures/fred/      fixture de FRED DGS20
```
