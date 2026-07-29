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
- `/income-statement` (anual, ≥5 años ideal, mínimo 3 años para CAGR — ver B2)
- `/balance-sheet-statement` (anual)
- `/cash-flow-statement` (anual)
- `/key-metrics` (anual — usado también para el PER de peers, ver más abajo)
- `/search-symbol` (resolución nombre → ticker)

**Nunca se depende de** `/dcf`, `/sector-pe-ratio` ni `/treasury-rates`
(endpoints precalculados del tier pago/premium, disponibilidad gratuita no
verificada). Tampoco de `/key-metrics-ttm`: verificado con una key real que
es un endpoint **de pago** en el plan gratuito actual de FMP (`402 Payment
Required`), a pesar de que la documentación pública no lo deja claro. En su
lugar:
- El PER promedio "de sector" se aproxima con un **set fijo de 3-5 peers
  hardcodeados por sector** (`src/investbot/peers.py`), documentado en cada
  respuesta como "PER promedio de un set fijo de comparables, no del sector
  completo". La API stable ya no expone un campo `pe` directo en `/quote`
  (deprecado junto con la API legacy) — el PER de cada peer se deriva como
  `1 / earningsYield` desde `/key-metrics` **anual** (la variante TTM da un
  PER más "en vivo" pero es de pago — ver arriba). Mismo presupuesto de
  requests que la versión anterior (1 llamada por peer).
- El DCF se calcula internamente (proyección de FCF + WACC simplificado +
  valor terminal por perpetuidad de Gordon Growth), sin llamar a `/dcf`.
- La tasa libre de riesgo (Y) **no viene de FMP** — ver siguiente sección.

### Presupuesto de requests por consulta

| Llamada | Cantidad | Endpoint |
|---|---|---|
| Datos propios del ticker | 6 | `/quote`, `/profile`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics` |
| Resolución nombre→ticker (solo si no mandaste el ticker exacto) | 0-1 | `/search` |
| Peers para el modelo de Múltiplos | 3-5 | `/key-metrics` (anual) por peer |
| Contexto de mercado (VIX, no depende del ticker consultado) | 1 | `/quote (symbol=^VIX)` |
| **Total por consulta completa** | **10-13** | |

Con 250 requests/día, el bot soporta **entre ~19 y ~25 consultas completas de
empresa por día** — muy por encima del uso esperado de un solo usuario con
consultas esporádicas. No hay caché ni rate-limit adicional a nivel de
aplicación más allá de un límite defensivo de 10 consultas/minuto (protección
contra bugs propios, no un límite de negocio).

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
  hace conexiones salientes (a Telegram, FMP, FRED/Treasury.gov).
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
- **Secretos:** `TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, `FRED_API_KEY` se leen
  solo de variables de entorno (`.env`, `chmod 600`, nunca en git). Los
  loggers de `httpx`/`httpcore`/`telegram` se fijan a `WARNING` en
  producción — nunca `DEBUG`, porque el token de Telegram viaja en el path
  de la URL y las API keys de FMP/FRED como query param; un log en `DEBUG`
  los filtraría en texto plano.
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
  peers.py             set fijo de peers por sector + promedio de PER (vía key-metrics anual)
  treasury_client.py   FRED (DGS20) + fallback Treasury.gov
  rules.py              ratios financieros + pilares de buena empresa
  valuation.py           motor propio de valoración (3 modelos + promedio parcial)
  risk_fit.py            encaje beta vs perfil de riesgo
  summary.py              texto dummy-friendly con las analogías de Daniela
  db.py                    SQLite (tabla risk_profile, fila única)
tests/
  fixtures/adobe/     caso de regresión (ver tests/fixtures/adobe/README.md)
  fixtures/fmp/       fixtures genéricos de FMP (quote, search)
  fixtures/fred/      fixture de FRED DGS20
```
