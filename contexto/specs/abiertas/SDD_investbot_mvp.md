# Spec: InvestBot MVP — Bot de Telegram para análisis de acciones [Iter-1]

**Rol:** `architect` (spec base) + `security` (criterios agregados) + `qa` (criterios de cobertura/testabilidad agregados, ver sección "Criterios QA" al final)
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `architect` — `qa` revisó el 2026-07-24 y agregó criterios de test completos, pero encontró **5 bloqueantes** (B1-B5, ver "Bloqueantes para `architect`" y "Veredicto de `qa`" al final del documento): casos límite financieros sin comportamiento definido (EPS≤0, CAGR con datos insuficientes o EPS negativo en año base, liquidez con denominador 0, ambigüedad de frontera en la tabla de perfil de riesgo). **No hay scope freeze todavía.** `architect` debe emitir un spec patch (Iter-2) acotado a B1-B5 antes de que `implementer` pueda arrancar; el patch no necesita volver a pasar por `security` ni `frontend`.
**Estado:** las 5 preguntas abiertas de la versión anterior fueron resueltas por Daniela el 2026-07-24. **Reapertura el mismo día (2026-07-24):** Daniela corrigió explícitamente la pregunta (d) — presupuesto **$0, sin excepciones, ninguna suscripción paga** — antes de que la spec pasara a `security`. Esto invalida la Decisión de diseño #8 original (que asumía plan pago de FMP) y todo lo que dependía de ella (fuente de "g", fuente de Y, y los 3 modelos de valoración). Esta versión reescribe únicamente la integración con datos financieros bajo la restricción de plan gratuito de FMP (250 req/día); el resto de la spec (cuestionario de riesgo, fórmulas de ratios, stack Python, arquitectura de despliegue) no cambia. Spec lista para revisión de `security` tras este ajuste.

---

## Contexto

Daniela quiere un bot de Telegram **personal** (un único usuario, sin multi-tenant/auth) que analice acciones/empresas de bolsa. Se eligió Telegram Bot API (oficial) en vez de WhatsApp porque WhatsApp no tiene forma oficial de leer chats personales sin librerías no oficiales (riesgo de ban).

El bot debe: (1) fijar el perfil de riesgo de Daniela una sola vez vía un cuestionario de 8 preguntas, (2) recibir un ticker o nombre de empresa, (3) traer datos financieros precalculados de Financial Modeling Prep (FMP), (4) validar la empresa contra reglas de análisis fundamental y calcular un "valor justo" promediando 3 métodos, (5) responder en formato "explícamelo como si fuera tonto" con las analogías propias de Daniela, y (6) decir si la empresa encaja con su perfil de riesgo.

Fuente de las reglas de negocio (material propio de Daniela, no reinterpretar):
- `/Users/danielavergara/Documents/Personal/Inversiones/Bolsa/Cursos/Charla Analisis Fundamental - Charla Nico/Los Ratios Financieros.pdf`
- `/Users/danielavergara/Documents/Personal/Inversiones/Bolsa/Cursos/Charla Analisis Fundamental - Charla Nico/PresentaciónAnalisisFundamental.pdf`
- "Instrucciones para saber tu perfil de riesgo.pdf" (cuestionario de perfil — contenido textual ya transcrito en esta spec, ver sección dedicada)

---

## Estado actual

No existe el proyecto. La carpeta propuesta `/Users/danielavergara/Documents/Personal/InvestBot` no existe (se crea en esta tarea solo `contexto/`, sin código todavía).

Referencia de patrón de infraestructura existente: FoodMindAI (`/Users/danielavergara/Documents/Personal/FoodMind/app`) corre en el mismo VPS con Docker + Traefik + Redis + PostgreSQL, red `n8n-traefik_app_network`, dominio `bot.foodmindchat.com`. InvestBot es un **servicio Docker separado** — no comparte base de datos ni contenedores con FoodMindAI (restricción explícita de Daniela).

---

## Estado objetivo

Un servicio Docker (`investbot-bot`) desplegado en el mismo VPS que:

1. Al recibir `/start` del chat_id autorizado, ejecuta el cuestionario de 8 preguntas de perfil de riesgo (una sola vez; re-ejecutable si Daniela vuelve a mandar `/start`), calcula el puntaje total y lo persiste junto con la categoría de perfil resultante.
2. Ignora cualquier mensaje que no venga del chat_id de Daniela (`TELEGRAM_ALLOWED_CHAT_ID`).
3. Si Daniela envía texto libre (ticker o nombre de empresa) y ya completó el onboarding, resuelve la empresa, consulta FMP, calcula el valor justo (promedio de 3 métodos), clasifica "cara"/"barata", evalúa los pilares de "buena empresa" y el encaje con su perfil de riesgo, y responde con un resumen ejecutivo dummy-friendly usando las analogías de Daniela.
4. Si Daniela envía texto libre sin haber completado el onboarding, el bot le pide correr `/start` primero.
5. Persiste el perfil de riesgo en SQLite (un único registro), sobrevive reinicios del contenedor vía volumen Docker.
6. No expone ningún puerto público ni ruta en Traefik (ver "Decisiones de diseño").

---

## Stack propuesto

**Recomendación: Python 3.12 + `python-telegram-bot` (v21, async) + `httpx` + SQLite (stdlib `sqlite3`).**

Evaluado contra Node.js/TypeScript (Telegraf + better-sqlite3) y Java 21/Spring Boot (status quo de FoodMindAI, para no asumir consistencia de stack sin evaluar).

| Criterio | Peso | Python | Node/TS | Java/Spring |
|---|---|---|---|---|
| Alineación con el problema (bot simple, I/O bound, 2 integraciones REST) | 25% | 9 | 8 | 5 |
| Madurez librería Telegram (`python-telegram-bot` vs `telegraf` vs `TelegramBots`) | 20% | 9 | 8 | 7 |
| Performance/escala (irrelevante a este volumen — 1 usuario) | 10% | 8 | 8 | 9 |
| Curva de arranque / velocidad de entrega para MVP | 20% | 9 | 7 | 5 |
| Ecosistema (parsers JSON, HTTP client, testing) | 10% | 9 | 8 | 7 |
| Costo operativo (RAM del contenedor, tamaño de imagen, boilerplate) | 15% | 9 | 8 | 5 |
| **Score total** | | **8.85** | **7.75** | **6.05** |

**Por qué Python y no Java/Spring (aunque FoodMindAI ya use ese stack):**
- Es un proyecto nuevo e independiente, sin código ni base de datos compartida con FoodMindAI — no hay costo de inconsistencia de stack real.
- El dominio del problema es pequeño: recibir un mensaje, llamar 5-6 endpoints REST, hacer aritmética simple, responder texto. Spring Boot añade boilerplate (controllers, DI, entities JPA) que no aporta valor a esta escala.
- `python-telegram-bot` tiene soporte nativo de `ConversationHandler`, ideal para modelar el cuestionario de 8 preguntas como una máquina de estados sin código extra.
- Imagen Docker más liviana (`python:3.12-slim` + deps ≈ 150-200MB vs JVM ≈ 300-400MB) — relevante en un VPS que ya corre FoodMindAI + n8n + Postgres + Redis + Traefik.
- SQLite vía `sqlite3` (stdlib) no requiere driver ni ORM adicional para un único registro de perfil.

**Por qué no Node/TS:** viable y cercano en score, pero `python-telegram-bot` tiene mejor soporte de `ConversationHandler` para flujos tipo wizard que las alternativas en Node, y Python tiene mejor ergonomía para el cálculo numérico de la valoración (aunque aquí es aritmética simple, no justifica el cambio).

**Condición de revisión de esta decisión:** si en el futuro Daniela decide fusionar este bot con FoodMindAI o compartir infraestructura/equipo de código, reevaluar consistencia de stack.

---

## Decisiones de diseño tomadas

*(para que `implementer` no las reabra — cualquier cambio pasa por spec patch)*

1. **Long polling, no webhook.** La Telegram Bot API soporta ambos modos. Para un bot de un solo usuario se recomienda **long polling**: el contenedor abre la conexión saliente hacia Telegram, sin necesitar dominio, certificado TLS, ruta en Traefik ni webhook secret. Esto reduce la superficie de ataque a cero puertos públicos expuestos — más simple y más seguro que el patrón webhook usado en FoodMindAI (que lo necesita porque WhatsApp Business API exige webhook). Trade-off aceptado: el contenedor debe mantenerse corriendo (`restart: unless-stopped`), igual que cualquier otro contenedor del VPS.
2. **Resolución de nombre de empresa → ticker** vía el endpoint de búsqueda de FMP (`/search` o equivalente — confirmar firma exacta durante implementación, ver pregunta abierta (d) sobre plan de FMP). Si hay una sola coincidencia, se usa directo. Si hay varias, el bot lista hasta 5 con botones inline para que Daniela desambigüe. Si hay cero, responde con error claro sin lanzar excepción sin manejar.
3. **Persistencia:** SQLite de un solo archivo en volumen Docker (`/data/investbot.db`), tabla `risk_profile` con un único registro lógico (id fijo). No se justifica PostgreSQL para un solo usuario y un solo registro — evaluado y descartado por sobre-ingeniería (YAGNI).
4. **Growth rate "g" del modelo Graham — revisado 2026-07-24 (presupuesto $0):** el endpoint de estimados de crecimiento de EPS de analistas pertenece al tier pago/premium de FMP y su disponibilidad gratuita no está verificada — no se diseña como fuente primaria. **Fuente única de diseño:** CAGR histórico de EPS calculado a partir de `/income-statement` (anual, ≥5 años, endpoint gratuito). Fórmula: `g = (EPS_año_más_reciente / EPS_año_más_antiguo)^(1/n_años) - 1`. El bot siempre indica en la respuesta que "g" es CAGR histórico (transparencia con Daniela, mismo principio que la versión anterior). Si durante la implementación se verifica que el endpoint de estimados de analistas sí está disponible gratis (ver criterio de aceptación nuevo en "Integración FMP"), puede añadirse como fuente alternativa opcional indicando cuál se usó — pero el diseño no depende de eso para funcionar.
5. **Regla de encaje beta ↔ perfil de riesgo (default, ajustable):** beta (de `/profile/{ticker}`) < 0.8 → compatible con Muy Conservador/Conservador; 0.8-1.2 → compatible con Moderado; > 1.2 → compatible con Agresivo. Toda acción individual se etiqueta como "renta variable" para el mensaje de encaje. Esta regla es una propuesta razonable del architect, no viene del material fuente de Daniela — queda documentada aquí como asunción explícita; Daniela puede pedir ajustarla sin que eso sea una "regresión" de un criterio verde.
6. **"Ventaja competitiva difícil de copiar"** nunca se calcula numéricamente. El bot siempre la reporta como "revisar manualmente" — es señal cualitativa, no forzar un proxy de datos.
7. **Fuente de Y (rendimiento bono tesoro EEUU 20 años) para el modelo Graham — revisado 2026-07-24 (presupuesto $0):** el endpoint `/treasury-rates` de FMP es un endpoint precalculado del tier pago/premium; su disponibilidad gratuita no está verificada (solo la sugiere la página de marketing de FMP) y no se diseña como dependencia dura. **Fuente primaria de diseño: FRED (Federal Reserve Economic Data)**, serie `DGS20` ("20-Year Treasury Constant Maturity Rate"), vía su API REST pública (`https://api.stlouisfed.org/fred/series/observations?series_id=DGS20&api_key=...&file_type=json`). Requiere una API key gratuita de FRED (registro instantáneo, sin costo, sin tarjeta) — ver artefacto `.env.example` para la nueva variable. **Fallback si FRED no responde o la key no está configurada:** feed público de Treasury.gov/TreasuryDirect (Daily Treasury Par Yield Curve Rates, XML/CSV, sin API key) — se documenta como fallback de segundo nivel porque su formato es menos estable para parsear que la respuesta JSON de FRED. Si ninguna de las dos fuentes responde, el bot reporta el fallo explícitamente a Daniela (nunca usa un valor hardcodeado en silencio). Cualquiera de las dos fuentes usada debe indicarse en la respuesta (mismo principio de transparencia que la decisión #4). **Esta llamada no consume el cupo de 250 req/día de FMP** — es un proveedor distinto.
8. **Plan de FMP: gratuito, $0/mes — revisado 2026-07-24 (corrección explícita de Daniela: presupuesto $0, sin excepciones, ninguna suscripción paga).** El diseño de esta spec **no depende de ningún endpoint premium/precalculado de FMP** (`/dcf`, `/sector-pe-ratio`, `/treasury-rates`) — quedan excluidos como dependencia dura porque su disponibilidad gratuita no está verificada con una API key real, solo sugerida por la página de marketing de FMP. Los 3 modelos de valoración se calculan en un **motor propio** (ver decisiones #4, #7, #9, #10 y la sección "Reglas de validación de empresa") usando exclusivamente endpoints de datos crudos, confiablemente gratuitos: `/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/profile` (sector, beta, market cap de la empresa) y `/search` (resolución nombre→ticker). Límite documentado del plan gratuito: **250 requests/día**. Ver sección dedicada "Presupuesto de requests FMP (plan gratuito)" para el cálculo de cuántas consultas de empresa por día soporta ese límite.
9. **Peer set para el modelo de Múltiplos — nuevo, 2026-07-24 (reemplaza `/sector-pe-ratio`):** el PER promedio del sector se aproxima con un **set fijo de 3 a 5 tickers peer hardcodeados por sector**, mantenido en `src/investbot/peers.py` como diccionario `sector → [tickers]` (ej. `"Technology": ["AAPL", "MSFT", "GOOGL"]`), excluyendo el ticker propio si coincide con algún peer. El sector de la empresa consultada se obtiene de `/profile` (gratuito). Para cada peer se llama `/quote` (gratuito, incluye el campo `pe` — PER trailing) y se promedia. Esta aproximación se documenta explícitamente en la respuesta al usuario como "PER promedio de un set fijo de comparables, no del sector completo" (mismo principio de transparencia). El mantenimiento del set de peers es manual — si un peer deja de cotizar o cambia de sector, es un ajuste de configuración, no un bug, y no es bloqueante para el MVP. Backlog futuro fuera de scope: set de peers dinámico vía un screener de FMP si algún día se paga un plan superior.
10. **Cálculo propio de DCF — nuevo, 2026-07-24 (reemplaza el uso directo de `/dcf`):**
    - **Flujos de caja libres (FCF) históricos:** `Flujo de Caja Operativo - CapEx`, tomados de `/cash-flow-statement` (anual, ≥5 años, gratuito).
    - **Proyección:** FCF proyectado a 5 años usando el mismo CAGR histórico documentado en la decisión #4 (o el CAGR propio de FCF si difiere del de EPS — `implementer` documenta cuál usó por ticker).
    - **WACC simplificado (supuesto explícito, documentado como aproximación, no un cálculo de mesa de trading):**
      - Costo de deuda (Kd) = Gastos por intereses (`/income-statement`) / Deuda total (`/balance-sheet-statement`), ajustado por la tasa impositiva efectiva derivada de `/income-statement`: `Kd × (1 - tasa_impositiva_efectiva)`.
      - Costo de patrimonio (Ke) = CAPM simplificado: `Ke = Y + beta × prima_de_riesgo_de_mercado`, donde Y es la misma tasa libre de riesgo de la decisión #7, beta viene de `/profile`, y la prima de riesgo de mercado es una **constante documentada de 5.5%** (supuesto razonable de largo plazo para EEUU, asunción explícita del `architect` — ajustable por Daniela sin que sea considerado una "regresión" de un criterio verde, mismo tratamiento que la regla beta↔perfil de la decisión #5).
      - Ponderación deuda/patrimonio: deuda total (`/balance-sheet-statement`) vs market cap (`/quote`).
      - `WACC = (E/V × Ke) + (D/V × Kd × (1 - t))`.
    - **Valor terminal:** fórmula de perpetuidad de Gordon Growth ya definida en la spec original — sin cambios.
    - Todo el cálculo vive en `valuation.py`; no hay llamada a `/dcf`. El bot indica en la respuesta que el DCF es una aproximación con supuestos simplificados de WACC (transparencia).

---

## Cuestionario de perfil de riesgo (fuente de verdad textual — no reinterpretar)

Reproducido exactamente como lo entregó Daniela, con las preguntas 5 y 7 ya confirmadas (ver "Decisiones de Daniela" más abajo). `implementer` usa este texto literal para los botones/opciones del `ConversationHandler`, no debe parafrasear.

1. **Edad:** >60 (10) / 50-60 (20) / 40-49 (30) / 30-39 (40) / <30 (50)
2. **Plazo de inversión:** <1 año (10) / 1-2 años (20) / 2-5 años (30) / 5-10 años (40) / >10 años (50)
3. **% de ahorros dispuesto a invertir:** <30% (10) / 30-60% (30) / >60% (50)
4. **Objetivo de inversión:** asegurar y mantener capital (10) / ingresos moderados mensuales (20) / aumentar patrimonio con retornos estables (40) / aumentar patrimonio sin importar riesgos (50)
5. **Fondo de emergencia:** nada (10) / 1 mes de gastos (20) / 3 meses (30) / 6 meses (40) / más de 1 año (50). **Confirmado por Daniela — escala lineal, consistente con las demás preguntas.**
6. **Experiencia en inversiones:** nunca invertido (10) / Fiducuenta o CDT (20) / presta plata a familia/amigos (30) / finca raíz (40) / bolsa (50) / productos alternativos: cripto, crowdfunding (60)
7. **Tolerancia a rendimientos negativos** (3 opciones — la cuarta del PDF fuente, duplicada, queda eliminada por decisión de Daniela): a) Prefiero seguridad y disponibilidad a corto plazo (10) / b) Me interesan inversiones a mediano plazo con rentabilidad baja pero estable (30) / c) Prefiero rentabilidad alta aunque haya años con rendimientos negativos (40).
8. **Reacción si la inversión pierde valor:** vende inmediatamente (10) / consulta a un experto pero mantiene calma (30) / asume pérdidas a corto plazo, espera ganancias a largo plazo (50)

**Tabla de resultado (puntaje total → perfil) — verificada, sin cambios:**

| Rango | Perfil | Señal |
|---|---|---|
| 80-120 | Muy Conservador | renta fija ++, renta variable - |
| 120-180 | Conservador | renta fija +, renta variable - |
| 180-240 | Moderado | renta fija =, renta variable = |
| 240+ | Agresivo | renta fija -, renta variable + |

**Verificación de rango tras el ajuste de la pregunta 7:** la pregunta 7 pasó de 4 opciones (a=10/b=20/c=30/d=40) a 3 opciones (a=10/b=30/c=40) — el **máximo posible de esa pregunta sigue siendo 40** y el **mínimo sigue siendo 10**, así que no cambia el puntaje máximo ni mínimo total del cuestionario. Puntaje mínimo total = 8×10 = 80. Puntaje máximo total = 50+50+50+50+50+60+40+50 = **400**. La tabla de rangos (80-120 / 120-180 / 180-240 / 240+) sigue siendo coherente con ese rango [80, 400] — no requiere ajuste. Confirmar este cálculo con un test unitario (ver Criterios de aceptación).

---

## Reglas de validación de empresa (fuente de verdad textual)

- Pilares de "buena empresa": (1) ingresos que crecen año a año, (2) utilidades positivas y crecientes, (3) ventaja competitiva difícil de copiar (**siempre** "revisar manualmente" — no derivar de datos), (4) deuda controlada, (5) precio razonable (PER/múltiplos).
- **Ratio de Liquidez** = Activos Circulantes / Pasivos Circulantes. Saludable si > 1.
- **Margen Bruto** = (Ventas - Costo de Ventas) / Ventas.
- **EPS** = Ganancia Neta / Número de Acciones.
- **PER (P/E)** = Precio de la Acción / EPS.
- **P/S (Precio-Ventas)** = Capitalización de Mercado / Ventas Totales (útil cuando EPS es negativo).

**Valor Justo = promedio de 3 modelos** (caso real de referencia: Adobe → Múltiplos=658, DCF=289, EPS Model=555 → promedio=500 vs precio de mercado 333 → "barata"):

1. **Múltiplos:** Valor Justo = EPS (TTM) × PER promedio del **set fijo de peers del sector** (ya no usa `/sector-pe-ratio` — ver Decisión de diseño #9).
2. **EPS Model (Graham modificado):** Fair Value = EPS (TTM) × (8.5 + 2×g) × 4.4 / Y — g = % crecimiento anual esperado de EPS, derivado de CAGR histórico (**ver Decisión de diseño #4**), 4.4 = rendimiento histórico bono tesoro 20y época Graham (constante), Y = rendimiento actual bono tesoro EEUU 20 años (**fuente: FRED serie DGS20, fallback Treasury.gov — ver Decisión de diseño #7 revisada**).
3. **DCF:** calculado internamente por el bot — proyección de FCF + WACC simplificado + valor terminal por perpetuidad (**ver Decisión de diseño #10**). Ya no se usa `/dcf` de FMP.

Valor Justo Total = promedio simple de los 3. Precio actual < Valor Justo Total → "barata"; mayor → "cara".

---

## Arquitectura

```mermaid
graph TD
  D[Daniela - Telegram App] -->|mensajes| TG[Telegram Bot API]
  TG <-->|long polling, saliente, sin puerto expuesto| BOT[investbot-bot - contenedor Docker]
  BOT -->|HTTPS REST, API key| FMP[Financial Modeling Prep API]
  BOT -->|lectura/escritura| DB[(SQLite - volumen Docker)]
```

Contraste explícito con FoodMindAI: FoodMindAI expone `bot.foodmindchat.com` vía Traefik porque WhatsApp Business API exige webhook público. InvestBot **no necesita ninguna ruta en Traefik** — el contenedor solo hace conexiones salientes (a Telegram y a FMP). Esto simplifica el despliegue y reduce la superficie de ataque a comparar con FoodMindAI.

### Módulos (dentro del contenedor)

```mermaid
graph TD
  main[bot.py - entrypoint, polling app] --> security[security.py - filtro chat_id]
  main --> onboarding[onboarding.py - ConversationHandler 8 preguntas]
  main --> query[query_handler.py - texto libre = ticker/empresa]
  query --> resolver[fmp_client.py - resolución nombre-ticker via /search]
  query --> fmp[fmp_client.py - quote/income-statement/balance-sheet/cash-flow/key-metrics/profile - solo endpoints gratuitos]
  query --> peers[peers.py - set fijo de peers por sector + PER promedio]
  query --> treasury[treasury_client.py - FRED DGS20, fallback Treasury.gov]
  query --> rules[rules.py - pilares + ratios]
  query --> valuation[valuation.py - 3 metodos calculados internamente + promedio]
  fmp --> valuation
  peers --> valuation
  treasury --> valuation
  query --> risk_fit[risk_fit.py - beta vs perfil guardado]
  query --> summary[summary.py - texto dummy-friendly]
  onboarding --> db[(db.py - SQLite)]
  risk_fit --> db
```

### Presupuesto de requests FMP (plan gratuito, 250/día)

Cada consulta completa de una empresa dispara varias llamadas a distintos endpoints, todas gratuitas:

| Llamada | Cantidad | Endpoint |
|---|---|---|
| Datos propios del ticker | 6 | `/quote`, `/profile`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics` |
| Resolución nombre→ticker (solo si Daniela no mandó el ticker exacto) | 0-1 | `/search` |
| Peers para el modelo de Múltiplos (Decisión #9) | 3-5 | `/quote` por peer |
| **Total por consulta completa** | **9-12** | |

Con el límite de **250 requests/día** del plan gratuito, el bot soporta aproximadamente **entre 20 y 27 consultas completas de empresa por día** (250 / 12 ≈ 20 en el peor caso con 5 peers y resolución por nombre; 250 / 9 ≈ 27 en el mejor caso con 3 peers y ticker exacto). Esto es muy superior al uso esperado de un solo usuario con consultas esporádicas — no se requiere caché ni rate-limit adicional a nivel de aplicación para este MVP (consistente con la restricción existente de no implementar caché).

La llamada a FRED (o su fallback Treasury.gov) para obtener Y **no cuenta contra este cupo** — es un proveedor distinto de FMP.

### Modelo de datos (SQLite)

```sql
CREATE TABLE risk_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),   -- fila única, un solo usuario
  respuesta_1 INTEGER, respuesta_2 INTEGER, respuesta_3 INTEGER, respuesta_4 INTEGER,
  respuesta_5 INTEGER, respuesta_6 INTEGER, respuesta_7 INTEGER, respuesta_8 INTEGER,
  puntaje_total INTEGER NOT NULL,
  perfil TEXT NOT NULL CHECK (perfil IN ('muy_conservador','conservador','moderado','agresivo')),
  completed_at TEXT NOT NULL
);
```

---

## Criterios de aceptación

### Seguridad de acceso (un solo usuario)
- [ ] El bot lee `TELEGRAM_ALLOWED_CHAT_ID` de variable de entorno y descarta (sin responder, o con "no autorizado") cualquier update cuyo `chat_id` no coincida.
- [ ] El token del bot y la API key de FMP se leen de variables de entorno, nunca hardcodeadas; `.env` está en `.gitignore`; `.env.example` documenta las variables sin valores reales.

### Onboarding / perfil de riesgo
- [ ] `/start` dispara el cuestionario de 8 preguntas, en el orden y con las opciones/puntajes exactos de la sección "Cuestionario de perfil de riesgo" de esta spec (excepto preguntas 5 y 7, bloqueadas hasta resolver preguntas abiertas (a)/(b)).
- [ ] Cada pregunta se responde vía botones inline (no texto libre) para evitar puntajes inválidos por typos.
- [ ] Al completar las 8 respuestas, el bot calcula `puntaje_total` (suma), determina `perfil` según la tabla de rangos, persiste todo en SQLite, y confirma a Daniela con el resultado.
- [ ] Volver a correr `/start` sobrescribe el perfil anterior (permite recalibrar).
- [ ] Si Daniela envía un ticker/nombre antes de completar el onboarding, el bot responde indicando que debe correr `/start` primero y no ejecuta ningún análisis ni llamada a FMP.
- [ ] Test unitario que verifica el rango de puntaje total del cuestionario: mínimo = 80 (todas las respuestas en su opción más baja), máximo = 400 (todas en su opción más alta, incluyendo la pregunta 7 con 3 opciones), y que la tabla de mapeo a perfil (80-120/120-180/180-240/240+) cubre ese rango completo sin huecos.

### Resolución de ticker / nombre de empresa
- [ ] Texto que coincide con un ticker válido se usa directo.
- [ ] Texto que es un nombre de empresa con una sola coincidencia en la búsqueda de FMP se resuelve automáticamente.
- [ ] Texto con múltiples coincidencias muestra hasta 5 opciones con botones inline para desambiguar.
- [ ] Texto sin ninguna coincidencia responde con error claro y no lanza excepción sin capturar.

### Integración FMP
- [ ] Para el ticker resuelto, llama únicamente a endpoints de datos crudos del plan gratuito de FMP: `/quote`, `/income-statement` (anual, ≥5 años), `/balance-sheet-statement` (anual, ≥5 años), `/cash-flow-statement` (anual, ≥5 años), `/key-metrics` (multi-año), `/profile` (sector, beta, market cap). El bot **no depende** de `/dcf`, `/sector-pe-ratio` ni `/treasury-rates` para funcionar.
- [ ] Para el modelo de Múltiplos, llama a `/quote` de cada ticker del set fijo de peers del sector correspondiente (Decisión de diseño #9).
- [ ] Timeout configurado en el cliente HTTP; error 4xx/5xx o rate-limit de FMP (incluido el límite de 250 req/día del plan gratuito) se traduce en un mensaje claro a Daniela, nunca en un stack trace crudo ni en un crash del proceso de polling.
- [ ] Los datos de un ticker (propio + peers) se piden en vivo (no hace falta caché para MVP — un solo usuario, uso esporádico, presupuesto de requests con margen amplio, ver "Presupuesto de requests FMP"); si se agrega caché en el futuro, es un backlog item, no parte de esta spec.
- [ ] **Nuevo (verificación de endpoints premium):** durante la implementación, con una API key real de FMP en plan gratuito, verificar explícitamente si `/dcf`, `/sector-pe-ratio` y/o `/treasury-rates` responden sin costo. Si alguno resulta disponible gratis en la práctica, puede usarse como atajo opcional/optimización (ej. comparar contra el cálculo propio, o sustituir una llamada), documentado como tal — nunca como requisito para que el bot funcione. Si ninguno está disponible gratis, el diseño ya funciona sin ellos (comportamiento default de esta spec).

### Cálculo de ratios y valor justo
- [ ] Ratio de Liquidez, Margen Bruto, EPS, PER, P/S calculados con las fórmulas exactas de la sección "Reglas de validación de empresa".
- [ ] Valor Justo por Múltiplos = EPS TTM × PER promedio del set fijo de peers del sector (Decisión de diseño #9).
- [ ] Valor Justo EPS Model = EPS TTM × (8.5 + 2×g) × 4.4 / Y, con g obtenido según la Decisión de diseño #4 (CAGR histórico de EPS) y Y según la Decisión de diseño #7 revisada (FRED serie DGS20, fallback Treasury.gov).
- [ ] Valor Justo DCF = calculado internamente por el bot (proyección de FCF + WACC simplificado + valor terminal por perpetuidad), según la Decisión de diseño #10. Sin llamada a `/dcf` de FMP.
- [ ] Valor Justo Total = promedio simple de los 3.
- [ ] Test de regresión con datos mockeados (no llamada real a FMP) que reproduce el caso Adobe de esta spec: Múltiplos=658, DCF=289, EPS Model=555 → Valor Justo Total=500.
- [ ] Clasificación "barata"/"cara" correcta según precio actual vs Valor Justo Total, con el % de diferencia mostrado en la respuesta.

### Pilares de "buena empresa"
- [ ] Se evalúan y listan individualmente en la respuesta: crecimiento de ingresos (multi-año), utilidades positivas/crecientes, deuda controlada (liquidez > 1), precio razonable (PER/múltiplos).
- [ ] "Ventaja competitiva" se muestra siempre como "revisar manualmente", nunca como un dato calculado.

### Resumen dummy-friendly
- [ ] La respuesta usa literalmente los términos "el boletín" (Estado de Resultados), "la foto" (Balance General), "el extracto" (Flujo de Efectivo).
- [ ] La respuesta referencia la analogía de "Tienda de Limonada" al menos una vez cuando el flujo de explicación lo amerite.
- [ ] La respuesta indica explícitamente si la empresa "encaja" o "no encaja" con el perfil de riesgo guardado de Daniela, aplicando la regla beta↔perfil documentada en "Decisiones de diseño" #5.

### Infraestructura / despliegue
- [ ] `docker compose -f docker-compose.prod.yml up -d` levanta `investbot-bot` en el VPS sin exponer ningún puerto público ni ruta en Traefik.
- [ ] El perfil de riesgo persiste tras `docker compose restart` (volumen Docker para el archivo SQLite).
- [ ] `README.md` documenta arranque local (dev) y despliegue (prod); `.env.example` lista todas las variables sin secretos reales.
- [ ] `README.md` (o documento dedicado) indica explícitamente: **plan gratuito de FMP, $0/mes, límite de 250 requests/día**, listando los endpoints de datos crudos usados (`/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/profile`, `/search`) y el presupuesto de requests calculado (~20-27 consultas de empresa por día, ver sección "Presupuesto de requests FMP (plan gratuito)"). Documenta también que Y (rendimiento del bono del tesoro) se obtiene de FRED (serie DGS20) con fallback a Treasury.gov, **no de FMP** — y que `/dcf`, `/sector-pe-ratio` y `/treasury-rates` son, a lo sumo, atajos opcionales verificados durante la implementación, nunca una dependencia dura.
- [ ] Existe una guía corta de setup manual (`contexto/referencia/SETUP_TELEGRAM_BOT.md`) con los pasos para crear el bot en @BotFather y obtener `TELEGRAM_BOT_TOKEN` — es documentación, no código; Daniela la ejecuta manualmente durante la fase de implementación, antes de que el contenedor pueda arrancar en modo polling.

---

## Artefactos a crear

- `/Users/danielavergara/Documents/Personal/InvestBot/` → nuevo repo (nombre provisional, ver pregunta abierta (e))
- `src/investbot/bot.py` → entrypoint, `Application` en modo polling
- `src/investbot/security.py` → filtro de `chat_id`
- `src/investbot/onboarding.py` → `ConversationHandler` de 8 preguntas
- `src/investbot/query_handler.py` → handler de texto libre (ticker/nombre)
- `src/investbot/fmp_client.py` → wrapper HTTP a FMP (`httpx`), **solo endpoints gratuitos** (`/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/profile`, `/search`); incluye búsqueda nombre→ticker. `/dcf`, `/sector-pe-ratio` y `/treasury-rates` no son dependencia dura (ver criterio de aceptación de verificación en "Integración FMP").
- `src/investbot/peers.py` → **nuevo** — diccionario `sector → [tickers]` de peers hardcodeados (3-5 por sector) y lógica de promedio de PER para el modelo de Múltiplos (Decisión de diseño #9).
- `src/investbot/treasury_client.py` → **nuevo** — cliente para el rendimiento del bono del tesoro EEUU a 20 años (Y): FRED (serie `DGS20`) como fuente primaria, Treasury.gov como fallback (Decisión de diseño #7 revisada).
- `src/investbot/rules.py` → pilares + ratios (liquidez, margen bruto, EPS, PER, P/S)
- `src/investbot/valuation.py` → **motor propio** de los 3 métodos de valor justo (Múltiplos con `peers.py`, EPS Model con `treasury_client.py`, DCF con proyección de FCF + WACC simplificado, ver Decisión de diseño #10) + promedio. Ya no delega a `/dcf` de FMP.
- `src/investbot/risk_fit.py` → comparación beta/tipo de activo vs perfil guardado
- `src/investbot/summary.py` → construcción del texto dummy-friendly con analogías
- `src/investbot/db.py` → schema SQLite + acceso a `risk_profile`
- `Dockerfile`, `docker-compose.yml` (dev), `docker-compose.prod.yml` (prod, sin Traefik)
- `.env.example`, `.gitignore` → `.env.example` agrega `FRED_API_KEY` (API key gratuita de FRED para Y) junto a `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `FMP_API_KEY`
- `tests/` (pytest) → incluye test de regresión del caso Adobe
- `README.md` → incluye la sección obligatoria de "plan mínimo de FMP requerido y por qué"
- `contexto/referencia/SETUP_TELEGRAM_BOT.md` → guía corta (documento, no código) de cómo crear el bot en @BotFather y obtener el token; pasos: abrir chat con @BotFather → `/newbot` → elegir nombre y username → guardar el token devuelto en `TELEGRAM_BOT_TOKEN` del `.env` del VPS → enviar `/start` al bot recién creado desde la cuenta de Daniela para obtener su `chat_id` (ej. vía `getUpdates` o un bot auxiliar tipo @userinfobot) y guardarlo en `TELEGRAM_ALLOWED_CHAT_ID`
- `contexto/README.md`, `contexto/arquitectura/` (a poblar por `implementer`/`architect` en iteraciones siguientes)
- `contexto/specs/abiertas/SDD_investbot_mvp.md` → este archivo (se mueve a `specs/cerradas/` cuando el pipeline completo cierre)

---

## Restricciones

- No compartir base de datos ni contenedores con FoodMindAI. Red Docker separada (no reutilizar `n8n-traefik_app_network` salvo decisión explícita futura).
- No autenticación multi-usuario — el único control de acceso es el `chat_id` fijo por variable de entorno.
- **(Revisado 2026-07-24)** El bot SÍ calcula WACC (simplificado, supuestos documentados) y valor terminal (perpetuidad) de forma manual/interna — ya no depende de `/dcf` de FMP (ver Decisión de diseño #10). Esto invierte la restricción de la versión anterior, que asumía plan pago.
- No forzar un cálculo numérico de "ventaja competitiva" — queda como señal cualitativa fija ("revisar manualmente").
- No implementar caché de datos de FMP, histórico de consultas, alertas de precio, multi-idioma, ni dashboard web — fuera de scope MVP (backlog si Daniela lo pide después).
- **(Revisado 2026-07-24)** El diseño completo funciona sobre el **plan gratuito de FMP** (250 req/día) — no hay "modo premium" ni "modo degradado", solo un único diseño calculado con datos crudos gratuitos (ver Decisión de diseño #8 revisada). Los endpoints premium (`/dcf`, `/sector-pe-ratio`, `/treasury-rates`) no son una dependencia dura; si se confirman gratuitos durante la implementación, pueden usarse como atajo opcional, nunca como requisito de diseño.
- Esta spec es solo la fase de diseño (`architect`). No implementar código todavía — falta pasar por `security` y `qa` antes del scope freeze.

---

## Preguntas abiertas para Daniela (bloquean scope freeze)

Estas NO las resuelve el `architect` por su cuenta — requieren respuesta explícita de Daniela antes de que `implementer` pueda arrancar con las partes afectadas:

**(a) RESUELTA 2026-07-24 — Pregunta 5 del cuestionario (fondo de emergencia):** Daniela confirmó escala lineal 10/20/30/40/50. Ver sección "Cuestionario de perfil de riesgo", pregunta 5. Ya no bloquea scope freeze.

**(b) RESUELTA 2026-07-24 — Pregunta 7 del cuestionario (tolerancia a rendimientos negativos):** Daniela confirmó colapsar a 3 opciones (a=10/b=30/c=40), eliminando la opción duplicada del PDF fuente. Ver sección "Cuestionario de perfil de riesgo", pregunta 7, y la verificación de rango del puntaje total. Ya no bloquea scope freeze.

**(c) RESUELTA 2026-07-24 — Fuente de Y (bono tesoro EEUU 20 años):** no depende de FMP. Fuente primaria: FRED (serie `DGS20`, API gratuita con key). Fallback: Treasury.gov/TreasuryDirect (feed público, sin key). Ver Decisión de diseño #7 revisada. Ya no bloquea scope freeze.

**(d) RESUELTA 2026-07-24 — Cuenta/plan de FMP:** Daniela confirmó explícitamente **presupuesto $0, plan gratuito, sin excepciones, ninguna suscripción paga**. El diseño completo de esta spec se rediseñó para funcionar sobre el plan gratuito (250 req/día) con un motor propio de valoración — ver Decisión de diseño #8 revisada y la sección "Presupuesto de requests FMP (plan gratuito)". Ya no bloquea scope freeze.

**(e) Nombre final del bot y token de BotFather:** ¿ya tiene un nombre decidido y el token generado en @BotFather, o hay que crearlo como parte de la implementación? Afecta el nombre del repo (`InvestBot` es provisional) y la variable `TELEGRAM_BOT_TOKEN`.

---

## Handoff → security

### Specs producidas
- Esta spec (`SDD_investbot_mvp.md`), Iter-1.

### Criterios de aceptación base
Ver sección "Criterios de aceptación" completa arriba — agrupados en: seguridad de acceso, onboarding, resolución de ticker, integración FMP, cálculo de valor justo, pilares de buena empresa, resumen dummy-friendly, infraestructura/despliegue.

### Decisiones de diseño tomadas (no reabrir)
1. Python 3.12 + `python-telegram-bot` + `httpx` + SQLite — justificado en "Stack propuesto".
2. Long polling en vez de webhook — sin Traefik, sin dominio, sin puerto expuesto.
3. Persistencia en SQLite de un solo archivo, sin PostgreSQL.
4. Resolución nombre→ticker vía búsqueda FMP con desambiguación por botones.
5. Regla beta↔perfil de riesgo con umbrales default documentados (ajustable, no bloqueante).
6. "Ventaja competitiva" siempre cualitativa ("revisar manualmente").
7. **(2026-07-24)** Motor propio de valoración sobre plan gratuito de FMP (250 req/día): Múltiplos con peer set hardcodeado (Decisión #9), Graham EPS Model con Y de FRED/Treasury.gov (Decisión #7), DCF con FCF proyectado + WACC simplificado (Decisión #10) — sin dependencia de `/dcf`, `/sector-pe-ratio` ni `/treasury-rates` de FMP.

### Foco esperado para `security`
- Validación del filtro de `chat_id` (¿alcanza con comparar el ID, o hay vector de suplantación a nivel de Telegram Bot API que revisar?).
- Manejo de secretos (`TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, y ahora también **`FRED_API_KEY`**) en el VPS — mismo patrón que ya se auditó en FoodMindAI (`.env` chmod 600, no root).
- Rate-limiting/abuso: aunque es un solo usuario autorizado, ¿qué pasa si el token se filtra? ¿Vale la pena un rate-limit adicional a nivel de aplicación además del filtro de chat_id?
- Manejo de errores de FMP sin filtrar información sensible (API key) en logs o en la respuesta a Telegram.

### Preguntas abiertas que security NO debe resolver por su cuenta
Las (a)-(e) de arriba son de negocio/producto, no de seguridad — quedan pendientes de Daniela independientemente de lo que security agregue.

---

## Criterios de seguridad — agregado por `security` [Iter-1, 2026-07-24]

**Rol:** `security`. Esta sección **agrega** criterios de aceptación a la spec del `architect`; no modifica cuestionario, fórmulas, stack, ni arquitectura de despliegue (todo eso sigue siendo propiedad de `architect`).

**Nivel de verificación (ASVS 5.0):** InvestBot no almacena datos de salud ni datos financieros de cuentas reales — solo un score de perfil de riesgo (8 enteros 10-60) y consultas efímeras a APIs públicas de mercado. Esto es de menor sensibilidad que FoodMind (que sí maneja datos de salud → ASVS L2). Para InvestBot, **ASVS L1 es suficiente como nivel general**, pero el manejo de secretos y logging debe mantener el mismo rigor ya exigido en FoodMindAI (mismo VPS, mismo operador) — no se relaja ese punto por tratarse de un "bot personal".

---

### 1. Filtro de `chat_id` como control de acceso — análisis y vector de suplantación

**Respuesta directa a la pregunta del `architect`:** el `chat_id` **no es falsificable por un tercero a nivel de la Telegram Bot API**. El campo `chat.id`/`from.id` de cada `Update` lo asigna el servidor de Telegram en función de la sesión autenticada de quien envía el mensaje real — un atacante no puede inyectar un `Update` falso con un `chat_id` arbitrario hacia el proceso de long polling de InvestBot sin controlar la cuenta de Telegram de Daniela o el propio token del bot. Es decir: **comparar el ID es una barrera válida contra suplantación remota de identidad**, no es "seguridad ilusoria". Dicho esto, hay tres vectores reales que la spec actual no cubre:

- **(A) Fail-open por configuración ausente/errónea (CWE-1188, CWE-284 / OWASP A01:2025).** Si `TELEGRAM_ALLOWED_CHAT_ID` no está seteada, está vacía, o el código hace un `if chat_id == env_value or not env_value` mal escrito, el filtro puede degradar a "permitir a todos". Esto es plausible como bug de implementación (`.env` faltante tras un despliegue apurado, typo, valor `None` comparado con `int(None)` que no crashea sino que cae en una rama permisiva). Debe ser **fail-closed**: si la variable no está seteada o no es un entero válido, el proceso **debe rechazar arrancar** (crash explícito al boot), nunca arrancar en modo "sin filtro".
- **(B) Cobertura parcial de handlers.** `python-telegram-bot` registra handlers separados para `message`, `callback_query` (los botones inline del cuestionario y de desambiguación de ticker), `edited_message`, etc. Si el filtro de `chat_id` solo se aplica al handler de texto libre y no al `CallbackQueryHandler` de los botones, un usuario no autorizado que descubra el bot (los bots de Telegram son buscables por username) podría interactuar con el cuestionario vía botones sin pasar por el filtro. El filtro debe aplicarse como el **primer handler global** (`group=-1` o equivalente) que intercepta **todos** los tipos de update antes de que lleguen a `onboarding.py`, `query_handler.py` o cualquier `ConversationHandler`.
- **(C) Pérdida de control de la cuenta de Telegram de Daniela.** Si la cuenta de Telegram de Daniela sufre SIM-swap o secuestro de sesión, el atacante hereda el mismo `chat_id` y el filtro lo autoriza legítimamente — esto es un riesgo sistémico de la plataforma Telegram, no algo que el bot pueda mitigar por sí solo con más código. Se documenta como **riesgo residual aceptado** (fuera del control del bot), no como criterio a implementar. Mitigación recomendada a nivel de cuenta (fuera de scope de código): 2FA/verificación en dos pasos activada en la app de Telegram de Daniela.

**Filtrado por tipo de chat (defensa adicional, bajo costo):** dado que el bot solo debe operar en chat privado con Daniela, el filtro debe verificar además `update.effective_chat.type == "private"`, no solo el ID — así, si en el futuro alguien agrega el bot a un grupo (conociendo su username), el bot lo ignora aunque por algún bug el ID coincidiera con un chat distinto.

**Respuesta a "revocación del bot si Daniela pierde el control":** el token se revoca/regenera desde @BotFather (`/revoke`) en cualquier momento sin perder el `chat_id` (que es propiedad de la cuenta de Telegram, no del bot). Esto debe documentarse como procedimiento de respuesta a incidente, no como código.

**Criterios de aceptación nuevos:**
- [ ] Si `TELEGRAM_ALLOWED_CHAT_ID` no está seteada o no es parseable como entero, el proceso falla al arrancar (log claro + exit code ≠ 0) — nunca arranca en modo permisivo.
- [ ] El filtro de `chat_id` se registra como handler global de máxima prioridad (`group=-1` en `python-telegram-bot` o equivalente) y cubre explícitamente: mensajes de texto, `callback_query` (botones inline), y cualquier otro tipo de update que el bot registre. Test que verifica que un `callback_query` con `chat_id` distinto al autorizado es rechazado sin ejecutar el callback del `ConversationHandler`.
- [ ] El filtro valida `chat.type == "private"` además del `chat_id`.
- [ ] `contexto/referencia/SETUP_TELEGRAM_BOT.md` (ya existe como artefacto planeado) incluye una sección "Respuesta a incidente: token comprometido" con el procedimiento de revocar/regenerar el token en @BotFather y actualizar `.env` en el VPS.
- [ ] Riesgo residual de secuestro de cuenta de Telegram documentado explícitamente en `README.md` como limitación conocida (no es un bug del bot).

---

### 2. Gestión de secretos (`TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, `FRED_API_KEY`)

**Patrón base (heredado de la auditoría de FoodMindAI, memoria del 2026-07-23/24):** `.env` con `chmod 600`, propiedad del usuario sin privilegios root (`daniela`, grupo `docker`), nunca en git, `.env.example` sin valores reales. Este patrón ya está como criterio de aceptación del `architect` (sección "Seguridad de acceso") — se confirma y se extiende:

- **Hallazgo concreto de mayor severidad — logging de librerías HTTP en modo debug (CWE-532, OWASP A09:2025).** La Telegram Bot API pasa el token **en el path de la URL** (`https://api.telegram.org/bot<TOKEN>/getUpdates`), no en un header ni en el body. Si en algún momento (debug local, troubleshooting en el VPS) se sube el nivel de log de `httpx` o de `python-telegram-bot` a `DEBUG`, **cada línea de log incluirá el token completo en texto plano**, y esos logs persisten en `docker logs` / el driver de logging del host. Esto es el hallazgo de mayor severidad de esta revisión porque compromete el secreto más crítico (control total del bot) con una sola línea de configuración incorrecta, sin que haga falta ningún bug de lógica.
- **FMP y FRED pasan la API key como query param** (`?apikey=...` y `?api_key=...` respectivamente) — esto es CWE-598 (Use of GET Request Method With Sensitive Query Strings): la key queda en cualquier URL completa que se loguee, en el objeto de excepción de `httpx` (que incluye la URL con query string en su `repr()`/mensaje), y potencialmente en proxies/herramientas de red intermedias si algún día se añade uno.
- El `docker-compose.prod.yml` debe pasar secretos vía `env_file: .env`, nunca como `environment: TOKEN=xxx` hardcodeado dentro del propio compose file (que sí podría terminar committeado por error) ni vía `docker run -e`/línea de comandos (queda en `.bash_history` y en `ps aux` mientras el proceso corre).
- El `Dockerfile` no debe copiar `.env` a la imagen ni usar `ARG`/`ENV` con valores de secretos — los secretos entran solo en runtime vía `env_file`/volumen, nunca en una capa de la imagen (una imagen con secretos embebidos persiste el secreto aunque se borre después, y puede quedar en `docker history`).

**Criterios de aceptación nuevos:**
- [ ] El nivel de logging de los loggers `httpx`, `httpcore` y `telegram` (python-telegram-bot) se fija explícitamente a `WARNING` o superior en producción (nunca `DEBUG`/`INFO` por defecto); si se necesita debug temporal, se documenta como acción manual explícita, no como default del código.
- [ ] Ningún log persistente (`docker logs`, o cualquier logger propio del bot) contiene la URL completa con query string de una llamada a FMP o FRED. El wrapper HTTP (`fmp_client.py`, `treasury_client.py`) loguea únicamente endpoint/ticker/status code, nunca la URL cruda ni el diccionario de params sin sanitizar.
- [ ] Test unitario que verifica que una excepción real de `httpx` (p. ej. `httpx.HTTPStatusError`, que en su mensaje por defecto incluye la URL con query string) nunca se propaga sin sanitizar hacia `logger.*()` ni hacia el mensaje enviado a Telegram — se captura, se reconstruye un mensaje/log sin el query string, y **recién entonces** se loguea/envía.
- [ ] `docker-compose.prod.yml` usa `env_file: .env` para los 3 secretos; no hay ningún secreto en texto plano dentro del propio `docker-compose.prod.yml`, `docker-compose.yml` ni `Dockerfile`.
- [ ] `.env` en el VPS: `chmod 600`, propietario el usuario sin privilegios root ya usado para FoodMindAI (mismo patrón, no usuario nuevo ni root).
- [ ] `.gitignore` cubre `.env` y cualquier archivo `*.env.local`/backup (`.env.bak`, etc.) — ya está como criterio del architect para `.env`, se extiende a variantes de backup.

---

### 3. Manejo de errores de FMP/FRED sin filtrar la API key (a Daniela o a logs)

Extiende el criterio ya existente del `architect` ("error 4xx/5xx o rate-limit de FMP se traduce en un mensaje claro... nunca un stack trace crudo") con el detalle concreto de **qué** puede filtrar la key y **dónde**:

- Mensaje de error hacia Telegram: siempre un texto plantillado y genérico (p. ej. `"No pude obtener los datos de {ticker} ahora mismo, intenta más tarde."`), nunca `str(exception)` ni el body crudo de la respuesta de error de FMP/FRED (que en teoría podría incluir la URL solicitada o parámetros en su payload de error).
- Los códigos HTTP (429 rate-limit, 401/403 key inválida, 5xx) pueden diferenciarse en el mensaje a Daniela ("parece que se acabó el cupo de FMP por hoy" vs "FMP no responde") **sin** incluir la URL ni el header/param de autenticación.
- El manejo de la excepción debe ocurrir **en el wrapper HTTP mismo** (`fmp_client.py`/`treasury_client.py`), no dejarse "burbujear" hasta un `except Exception` genérico en `bot.py` que podría loguear/reenviar el objeto de excepción original tal cual.

**Criterios de aceptación nuevos:**
- [ ] `fmp_client.py` y `treasury_client.py` capturan las excepciones de `httpx` (timeout, `HTTPStatusError`, `RequestError`) en el punto de la llamada, y traducen a una excepción propia (`FMPError`, `TreasuryError`) que **no** incluye la URL ni los params originales en su mensaje.
- [ ] El handler que arma la respuesta a Telegram (`query_handler.py`) solo conoce/usa el mensaje sanitizado de la excepción propia, nunca la excepción de `httpx` original.
- [ ] Test que simula una respuesta 401 de FMP (key inválida/vencida) con `apikey=SECRETO123` en la URL de la request mockeada, y verifica que ni el mensaje enviado a Telegram ni ninguna llamada a `logger.*` en ese test contienen la substring `SECRETO123`.

---

### 4. Validación de input del "ticker" / nombre de empresa

Daniela puede mandar cualquier texto libre. Análisis de superficie:

- **Inyección en la URL de FMP (`/search?query=...`):** si `implementer` usa `httpx.get(url, params={"query": texto, "apikey": key})` (diccionario de params de `httpx`), el propio `httpx` hace el URL-encoding correcto y no hay forma de que el texto "escape" a otro parámetro o path — esto ya mitiga la clase de inyección más obvia. El riesgo real es que alguien construya la URL por concatenación/f-string en vez de usar `params=`; eso sí sería explotable (aunque el "atacante" aquí es la propia Daniela, no hay usuario no autorizado con esta superficie gracias al filtro de `chat_id` — el objetivo de este control es robustez/defensa en profundidad, no defensa contra un atacante externo, ya que un no-autorizado nunca llega a este código).
- **No es SSRF (OWASP A10):** el host de destino (`financialmodelingprep.com`, `api.stlouisfed.org`) está fijo en el código; el input de Daniela solo llega a un query param, nunca determina el host/esquema de la request. Se documenta explícitamente para dejar constancia de que se evaluó y no aplica.
- **Inyección de logs (CWE-117):** si el texto libre de Daniela (que puede incluir saltos de línea, caracteres de control, o secuencias que parezcan otra línea de log) se loguea directo, podría falsear/insertar líneas de log falsas en `docker logs`. Bajo impacto (un solo usuario, no hay SIEM parseando estos logs hoy) pero trivial de mitigar.
- **Presupuesto de requests (CWE-400, disponibilidad):** un texto absurdamente largo o repetido rápido no rompe nada técnicamente (httpx lo encodea igual), pero si por bug de UI Daniela reenvía rápido o pega un texto enorme repetidamente, puede gastar el cupo de 250 req/día sin intención. Cubierto por el rate-limit defensivo de la sección 5.

**Criterios de aceptación nuevos:**
- [ ] Todas las llamadas a FMP y FRED usan el parámetro `params=` de `httpx` (o equivalente que garantice URL-encoding) — **nunca** f-string/concatenación de texto de usuario dentro de la URL. Code review / test que falla si aparece concatenación directa.
- [ ] El texto libre recibido se normaliza antes de usarse: `strip()`, colapsar espacios repetidos, límite de longitud (p. ej. 100 caracteres — suficiente para cualquier ticker o nombre de empresa real) — texto que excede el límite se rechaza con mensaje claro antes de llamar a FMP.
- [ ] El texto libre se sanitiza (remover saltos de línea/caracteres de control) antes de aparecer en cualquier línea de log, incluso en logs de debug/desarrollo.
- [ ] Documentado explícitamente en esta spec (este párrafo cuenta como esa documentación) que el host de FMP/FRED es fijo en código y el input de usuario nunca determina host/esquema — no aplica SSRF, evaluado y descartado.

---

### 5. Otros hallazgos — bot de un solo usuario, 3 API keys, VPS compartido con FoodMindAI en producción

- **Rate-limit defensivo a nivel de aplicación (CWE-400).** Aunque el filtro de `chat_id` hace que solo Daniela pueda gastar el cupo de FMP, un límite adicional simple (p. ej. máximo 10 consultas/minuto desde el chat autorizado, contador en memoria, sin necesidad de Redis) protege contra: bugs propios (doble tap de un botón, loop accidental en el `ConversationHandler`) y contra el escenario "el token de Telegram se filtró" que la spec pregunta explícitamente — si alguien más obtiene el token y logra interactuar (poco probable dado el filtro de `chat_id`, pero es defensa en profundidad barata), el rate-limit acota el daño al presupuesto de 250 req/día. Costo de implementación bajo, se recomienda incluir en el MVP en vez de dejarlo como backlog.
- **Detección de uso concurrente del token (señal gratuita de compromiso).** La Telegram Bot API permite un solo consumidor de `getUpdates` a la vez; si otro proceso usa el mismo token para hacer polling, InvestBot empezará a recibir errores `Conflict` (409) de Telegram. Esto es una señal de que el token se filtró y alguien más lo está usando activamente. Se recomienda loguear (nivel WARNING, sin exponer el token) y, si es viable, notificar a Daniela por el propio bot cuando se detecten `Conflict` repetidos — es una forma barata y concreta de responder a "¿qué pasa si el token se filtra?" con una señal accionable en vez de solo prevención.
- **Usuario no-root dentro del contenedor.** El `Dockerfile` debe definir un `USER` no-root para el proceso del bot (no ejecutar como root dentro del contenedor), consistente con buenas prácticas de hardening de contenedores — reduce el impacto de un eventual escape de contenedor en un VPS que también corre FoodMindAI en producción.
- **Límites de recursos del contenedor.** `docker-compose.prod.yml` debe fijar límites de memoria/CPU para `investbot-bot` (p. ej. `mem_limit`/`deploy.resources.limits`), para que un bug o abuso en este servicio nuevo no consuma recursos que afecten la disponibilidad de FoodMindAI en el mismo VPS (relevante justamente por ser "servicio separado pero VPS compartido").
- **Cadena de suministro (informativo, no bloqueante):** pinnear versiones de `python-telegram-bot`/`httpx` en `requirements.txt`/`pyproject.toml`, y considerar un scan tipo Trivy de la imagen final — mismo pendiente que ya existe abierto para la imagen de FoodMindAI (ver memoria del proyecto); se registra aquí como mismo tipo de deuda para InvestBot, no urgente para el MVP.
- **No se recomienda agregar un endpoint HTTP de administración/observabilidad para este bot.** A diferencia de la preferencia general de Daniela ("endpoints, no paneles"), aquí aplica la restricción explícita del `architect`: cero puertos expuestos, sin Traefik. Cualquier observabilidad debe resolverse con `docker logs`/healthcheck de Docker, no con un servidor HTTP nuevo — esto sería una regresión directa de la Decisión de diseño #1 (superficie de ataque cero). Se deja constancia explícita de que se evaluó y se descarta por conflicto con una decisión de diseño ya tomada.

**Criterios de aceptación nuevos:**
- [ ] Rate-limit en memoria por chat autorizado: máximo N consultas de empresa por minuto (valor sugerido: 10; ajustable, no bloqueante) — al superarse, el bot responde con un mensaje claro en vez de llamar a FMP.
- [ ] Errores `Conflict` (409) de `getUpdates` se loguean explícitamente a nivel WARNING con un mensaje distintivo (p. ej. `"posible uso concurrente del token detectado"`), sin incluir el token en el log.
- [ ] `Dockerfile` define un `USER` no-root para el proceso de la aplicación.
- [ ] `docker-compose.prod.yml` define límites de memoria/CPU para el servicio `investbot-bot`.
- [ ] `requirements.txt`/`pyproject.toml` fija versiones (no rangos abiertos) de `python-telegram-bot` y `httpx`.

---

### Veredicto de `security`

**Ningún hallazgo es bloqueante.** Todos los criterios agregados son implementables dentro de la arquitectura, el stack y las decisiones de diseño ya tomadas por `architect` (long polling, SQLite, sin Traefik, motor propio de valoración) — ninguno requiere reabrir esas decisiones ni volver a `architect` con un spec patch. El hallazgo de mayor severidad (logging en modo DEBUG filtrando el token de Telegram vía la URL) es un riesgo de configuración/implementación, no de diseño.

**La spec queda lista para pasar a `qa`** (siguiente paso del pipeline) con los criterios de esta sección agregados a los del `architect`. No hay necesidad de escalar a `architect` en esta iteración.

Nota: la spec original no describe una interfaz web/HTML — la única "UI" son botones inline nativos de Telegram, cubiertos funcionalmente por los criterios de "Onboarding" y "Resolución de ticker" ya existentes. Si el pipeline considera que `frontend` no aplica a un bot de Telegram sin UI web, el siguiente paso natural tras `security` es `qa` directamente (decisión de proceso, no de este agente).

---

## Handoff → qa

### Specs producidas
- `SDD_investbot_mvp.md`, Iter-1, con criterios de `architect` + `security` (esta revisión, 2026-07-24).

### Criterios de aceptación (base + seguridad)
Ver "Criterios de aceptación" (architect) + "Criterios de seguridad" (security, secciones 1-5 arriba) — ambos conjuntos deben cubrirse; ninguno reemplaza al otro.

### Sin bloqueantes
`security` no encontró ningún hallazgo que requiera spec patch de `architect`. Todos los criterios agregados son verificables por `qa`/`implementer` sin cambiar diseño, stack ni arquitectura.

### Foco esperado para `qa`
- Testabilidad de los criterios de seguridad nuevos: en particular, los tests de "no debe aparecer la API key en logs/mensajes" (sección 3) y de "filtro de chat_id cubre todos los tipos de update" (sección 1) requieren mocks explícitos de `httpx`/`python-telegram-bot` — confirmar que son automatizables, no solo revisión manual.
- Cobertura del test de regresión del caso Adobe (ya pedido por `architect`) junto con el nuevo test de rango de puntaje del cuestionario.
- Confirmar que los criterios de seguridad quedan en el checklist de scope freeze antes de pasar a `implementer`.

---

## Criterios QA — agregado por `qa` [Iter-1, 2026-07-24]

**Rol:** `qa`. Esta sección **agrega** criterios de cobertura y testabilidad a la spec de `architect` + `security`; no reescribe cuestionario, fórmulas, stack, arquitectura ni criterios de seguridad ya definidos.

**Antes de los criterios:** esta revisión encontró **5 huecos de diseño** (4 de ellos causan crash real, no solo "resultado feo") en los casos límite financieros que `architect` no definió. Sin resolverlos, no se puede escribir un test con output esperado determinista — no se documentan como criterio silencioso, se documentan como **bloqueante** en la sección dedicada al final de este bloque, antes del veredicto.

### Tipo de prueba principal
**Unit testing (pytest)** como base — coherente con la pirámide 70/20/10: el riesgo dominante de este proyecto es matemático (4 fórmulas: Múltiplos, Graham EPS Model, DCF, puntaje de perfil de riesgo), no de integración entre muchos componentes. Se complementa con **integration testing acotado** (wrappers HTTP mockeados, sin red real) para `fmp_client.py`/`treasury_client.py`, y un **smoke/E2E mínimo** del flujo conversacional completo (las 8 preguntas de principio a fin) — no se justifica una suite E2E amplia para un bot de un solo usuario sin UI web.

---

### 1. Tests unitarios de fórmulas — inputs conocidos, outputs verificables a mano

**Caso de regresión Adobe (ya exigido por `architect`, se detalla la tolerancia):**
- [ ] `test_valuation_adobe_regression`: con los datos históricos reales de Adobe mockeados (fixture, no llamada real a FMP/FRED), Múltiplos ≈ 658, DCF ≈ 289, EPS Model ≈ 555, promedio (Valor Justo Total) ≈ 500, con **tolerancia ±1%** sobre cada uno de los 4 valores (658/289/555/500) — no exigir igualdad exacta de punto flotante, sí exigir que la diferencia frente al caso documentado en la spec no se deba a un cambio silencioso de fórmula. Si el `implementer` obtiene un valor fuera de esa tolerancia, es fallo de este test, no un "ajuste menor" de la tolerancia.
- [ ] El fixture con los datos crudos de Adobe (income statement, balance sheet, cash flow, quote, profile, peers, Y de FRED) que reproducen ese resultado se guarda en `tests/fixtures/adobe/` como JSON, documentado con la fecha/fuente de captura — es el dato de prueba más importante del proyecto, debe ser trazable.

**Modelo de Múltiplos (`valuation.py`):**
- [ ] Test con EPS TTM y PER promedio de peers conocidos → Valor Justo = EPS × PER_promedio, verificado a mano con 2-3 sets de números simples (ej. EPS=2, PER_promedio=15 → 30).
- [ ] Test de `peers.py`: promedio de PER correcto cuando el ticker propio coincide con un peer del set (debe excluirse de su propio promedio, según Decisión de diseño #9) — verificar con un caso donde el ticker consultado está hardcodeado como peer de su propio sector.

**Graham EPS Model (`valuation.py`):**
- [ ] Test con EPS, g, Y conocidos → Fair Value = EPS × (8.5 + 2×g) × 4.4 / Y, verificado a mano (ej. EPS=3, g=0.10, Y=0.04 → 3 × 10.5 × 4.4 / 0.04 = 3465; usar números que permitan verificación manual exacta, no solo aproximada).
- [ ] Test de cálculo de `g` (CAGR histórico, Decisión #4) aislado del resto del modelo: `g = (EPS_reciente/EPS_antiguo)^(1/n_años) - 1`, con un caso de 5 años de EPS positivos y crecientes, verificado a mano.

**DCF (`valuation.py`):**
- [ ] Test de WACC aislado: Kd, Ke (CAPM con prima de mercado constante 5.5%), ponderación E/V y D/V, con un set de balance/income statement mockeado — verificar el número de WACC a mano antes de usarlo en la proyección.
- [ ] Test de proyección de FCF a 5 años + valor terminal (Gordon Growth) con WACC y g conocidos — verificar el valor presente descontado a mano para al menos un flujo del set.
- [ ] Estos tests deben poder ejecutarse **encadenados o aislados**: el test de WACC no debe requerir correr todo el pipeline de DCF para validarse (facilita diagnóstico cuando algo falla).

**Puntaje de perfil de riesgo (`onboarding.py` o donde viva el cálculo):**
- [ ] `test_puntaje_minimo`: las 8 respuestas en su opción más baja → total = 80 → perfil = Muy Conservador.
- [ ] `test_puntaje_maximo`: las 8 respuestas en su opción más alta (incluida la pregunta 7 con 3 opciones, máximo 40) → total = 400 → perfil = Agresivo.
- [ ] **Casos borde en cada frontera de la tabla** (80, 120, 180, 240) — ver bloqueante B5 más abajo: la spec no define si el límite superior de cada rango es inclusivo o exclusivo (¿120 es "Muy Conservador" o "Conservador"?). Los tests de frontera solo pueden escribirse determinísticamente después de que `architect` fije la convención. Una vez fijada, se requiere un test por cada uno de los 8 valores límite (79/80, 120/121, 180/181, 240/241 o la convención equivalente que se acuerde).

---

### 2. Tests de casos límite financieros

- [ ] **EPS negativo o cero:** test que alimenta EPS TTM = 0 y EPS TTM = -1.5 a `rules.py` (PER) y a `valuation.py` (Múltiplos, Graham). Sin una definición de comportamiento esperado (ver bloqueante B4), este test no puede fijar un assert de resultado — por ahora, el criterio mínimo no bloqueante es: **debe fallar de forma controlada** (excepción propia con mensaje claro) en vez de propagar `ZeroDivisionError` o un valor `nan`/negativo sin explicar a Daniela.
- [ ] **Pasivos circulantes = 0** (empresa sin deuda de corto plazo): test que alimenta ese caso al Ratio de Liquidez. Mismo criterio mínimo: no debe lanzar `ZeroDivisionError` sin capturar (ver bloqueante B3).
- [ ] **Menos de 2 años de income statement anual** (empresa recién salida a bolsa, historial insuficiente para CAGR): test que alimenta 0 o 1 año de datos al cálculo de `g`. Mismo criterio mínimo: no debe lanzar `ZeroDivisionError`/`IndexError` sin capturar (ver bloqueante B2).
- [ ] **g negativo** (EPS decreciente en el período histórico, pero sin cruzar a valores negativos/cero): test que verifica que la fórmula de Graham `(8.5 + 2×g)` con g negativo (ej. g = -0.05) produce un multiplicador menor pero sigue siendo un cálculo válido — este caso **no es bloqueante**, solo requiere cobertura de test, la fórmula funciona matemáticamente con g negativo mientras `(8.5 + 2×g) > 0`.
- [ ] **EPS que cambia de signo dentro de la ventana histórica** (ej. pérdida en el año 2, ganancia en los demás): test que confirma que el cálculo no produce un número complejo silencioso (ver bloqueante B1) — mismo criterio mínimo de fallo controlado hasta que se resuelva el bloqueante.

---

### 3. Testabilidad de llamadas a APIs externas (FMP, FRED, Telegram)

**Requisito de diseño para que esto sea testeable (confirmar con `implementer`, no reabre arquitectura):**
- [ ] `fmp_client.py` y `treasury_client.py` reciben el cliente HTTP (`httpx.AsyncClient` o `httpx.Client`) **inyectado en el constructor/función**, no instanciado como global de módulo — así los tests pueden pasar un cliente con `transport` mockeado (ej. `httpx.MockTransport` o la librería `respx`) sin tocar red real.
- [ ] Ningún test de la suite depende de `FMP_API_KEY`, `FRED_API_KEY` ni `TELEGRAM_BOT_TOKEN` reales — `pytest` debe correr en verde en un entorno sin esas variables seteadas o con valores dummy (`"test-key"`), consistente con el criterio de seguridad de no consumir el cupo real de 250 req/día en CI.
- [ ] Fixtures de respuestas JSON reales (capturadas una vez con una key real, luego usadas offline) para: `/quote`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/profile`, `/search` (caso 0/1/N resultados), y la respuesta de FRED `DGS20`. Viven en `tests/fixtures/fmp/` y `tests/fixtures/fred/`, con nombre descriptivo (`quote_adbe.json`, `search_multiple_matches.json`, `search_no_matches.json`).
- [ ] Test de manejo de error HTTP (429, 401, 5xx, timeout) por cada wrapper, simulado vía el transport mockeado — no depende de que FMP/FRED realmente esté caído para poder correr.
- [ ] Test del criterio de seguridad de la sección 3 (`security`): respuesta 401 con `apikey=SECRETO123` en la URL mockeada → ni el mensaje a Telegram ni ningún log contienen esa substring. Se lista aquí como cruce explícito con `security` para que quede en el checklist de scope freeze, no se duplica el criterio.
- [ ] Para Telegram: los handlers (`security.py`, `onboarding.py`, `query_handler.py`) se testean **invocando la función del handler directamente** con objetos `Update`/`Context` construidos a mano o vía `Update.de_json(...)` sobre un diccionario de fixture, y con `bot.send_message` mockeado (`unittest.mock`/`pytest-mock`) — no hace falta levantar el `Application` en modo polling real para la suite de unit tests. Esto es el patrón estándar de test de `python-telegram-bot`.

---

### 4. Cobertura del flujo conversacional (`ConversationHandler`, 8 preguntas)

- [ ] Test que el estado avanza correctamente pregunta a pregunta: simular las 8 respuestas vía `callback_query` en orden y verificar que el `ConversationHandler` pasa por los 8 estados internos hasta `ConversationHandler.END`, y que el mensaje de confirmación final incluye puntaje y perfil correctos.
- [ ] Test de reinicio: enviar `/start` a mitad del cuestionario (ej. después de responder 3 de 8) → el estado vuelve a la pregunta 1, sin mezclar respuestas de la corrida anterior con la nueva.
- [ ] Test de sobrescritura: completar el cuestionario una vez, correr `/start` de nuevo y completarlo con respuestas distintas → el registro en SQLite (fila única, `id=1`) refleja el segundo resultado, no un duplicado ni el promedio de ambos (cruza con el criterio ya existente del `architect`: "Volver a correr `/start` sobrescribe el perfil anterior").
- [ ] Test de escritura atómica: abandonar el cuestionario a mitad de camino (responder 4 de 8 y no continuar) → no debe quedar ninguna fila parcial/corrupta en `risk_profile` (la spec del `architect` ya implica que la escritura ocurre solo "al completar las 8 respuestas" — este test lo verifica explícitamente).
- [ ] **Test de chat_id no autorizado nunca entra al flujo** (cruce directo con el criterio de `security`, sección 1): un `chat_id` distinto al autorizado que envía `/start` y luego un `callback_query` de respuesta **no** avanza ningún estado del `ConversationHandler` y **no** escribe en SQLite. Este es el mismo test que `security` ya pidió ("Test que verifica que un `callback_query` con `chat_id` distinto al autorizado es rechazado sin ejecutar el callback del `ConversationHandler`") — QA no lo duplica, lo deja listado aquí para que quede explícito en el checklist de scope freeze que es un test automatizado (no solo revisión de código), y agrega el chequeo de que tampoco hay escritura en SQLite como aserción adicional.

---

### 5. Cobertura mínima requerida

- [ ] `valuation.py` (Múltiplos, Graham EPS Model, DCF) y el cálculo de puntaje de perfil de riesgo: **≥ 95% líneas / 100% de las ramas de los casos límite listados en la sección 2** (una vez resueltos los bloqueantes B1-B4). Es el corazón matemático del producto, con fórmulas fijas y pocas dependencias — cubrir al 100% las ramas de error no es sobre-ingeniería aquí, es barato de lograr.
- [ ] Resto del proyecto (`fmp_client.py`, `treasury_client.py`, `security.py`, `onboarding.py`, `query_handler.py`, `db.py`, `summary.py`, `risk_fit.py`, `rules.py`): **≥ 70% líneas** — piso razonable para un MVP personal, no un sistema crítico. No se exige 100% global (falsa seguridad, ver Quick Reference del framework QA).
- [ ] Cobertura total del proyecto reportada: **≥ 75% líneas** al cierre de `implementer`.
- [ ] Todos los criterios de aceptación de `architect` y todos los criterios de `security` (secciones 1-5) están cubiertos por al menos un test automatizado — no se acepta "revisión manual" como evidencia de cierre para ninguno de los dos, excepto los puntos que `security` documentó explícitamente como no-código (procedimiento de incidente en `SETUP_TELEGRAM_BOT.md`, riesgo residual documentado en `README.md`).

---

### 6. Testabilidad — estructura de código requerida

- [ ] Las fórmulas de valoración son funciones puras (input → output, sin I/O ni llamada HTTP dentro de la función de cálculo) en `valuation.py`/`rules.py` — la obtención de datos (FMP, FRED, peers) vive separada en los clientes, y `valuation.py` los recibe ya resueltos como parámetros. Esto es lo que permite los tests de la sección 1 sin mockear HTTP.
- [ ] Los clientes HTTP (`fmp_client.py`, `treasury_client.py`) exponen funciones/métodos inyectables con el cliente HTTP como parámetro (ver sección 3) — no hay un cliente global instanciado a nivel de módulo que impida sustituirlo en tests.
- [ ] El filtro de `chat_id` (`security.py`) es una función/handler testeable de forma aislada, sin depender de que el `Application` completo esté corriendo.
- [ ] No hay lógica de negocio (fórmulas, reglas de validación, cálculo de puntaje) escondida dentro de callbacks del `ConversationHandler` sin poder invocarse por separado — cada cálculo debe poder testearse sin simular la conversación completa de Telegram.

---

### 7. Definición de "listo" medible

- **Comando de test:** `pytest -v --cov=src/investbot --cov-report=term-missing --cov-fail-under=75` corrido localmente (Python 3.12, mismo patrón que FoodMindAI: se verifica local, no depende de que el contenedor esté desplegado en el VPS).
- **Cobertura mínima exigida:** 75% líneas del proyecto total, con el detalle diferenciado de la sección 5 (95%+ en `valuation.py`/scoring, 70%+ en el resto). No se pide más que esto — es un MVP personal, un solo usuario, sin datos sensibles de terceros (ver nivel ASVS L1 ya fijado por `security`).
- **Evidencia que `implementer` debe presentar al cerrar (no basta "pasé los tests"):**
  - Output real y completo del comando `pytest --cov` de la corrida final (no resumido, no editado), incluyendo la tabla de cobertura por archivo.
  - Confirmación explícita de que `test_valuation_adobe_regression` está en el output y pasó, citado por nombre.
  - Lista de los fixtures JSON usados (`tests/fixtures/...`) y de dónde salieron (captura real vs sintético).
  - Confirmación de que la suite corre sin red real: describir cómo se verificó (ej. correr con red deshabilitada, o confirmar que no hay ninguna URL real de `financialmodelingprep.com`/`stlouisfed.org`/`api.telegram.org` alcanzada durante la corrida).
  - 0 tests skippeados/comentados/marcados `xfail` sin justificación documentada.

### Criterio de exit de QA
- Todos los tests pasan (`pytest` sale con código 0).
- Cobertura ≥ 75% total, ≥ 95% en `valuation.py`/scoring de riesgo, según sección 5.
- Sin tests ignorados o comentados para pasar CI.
- Flaky rate = 0 en la nueva suite (correr la suite 2 veces seguidas debe dar el mismo resultado).
- Los 5 bloqueantes de la sección siguiente están resueltos con un spec patch de `architect` **antes** de que `implementer` empiece (no después).

---

### Bloqueantes para `architect` — antes de scope freeze

Estos 5 puntos no se agregan como criterio de test silencioso porque, tal como está escrita la spec hoy, **no hay un resultado esperado definido** para escribirlos como test determinista. Son huecos de diseño de casos límite, no de seguridad ni de stack — se resuelven con un spec patch corto de `architect`, no requieren volver a pasar por `security` ni `frontend`.

**B1 — CAGR de EPS con un año base negativo o con cambio de signo en la ventana histórica.**
`g = (EPS_reciente/EPS_antiguo)^(1/n_años) - 1` (Decisión de diseño #4). Si `EPS_antiguo` es negativo (empresa con pérdidas hace 5 años), Python evalúa una base negativa elevada a un exponente fraccionario y devuelve un **número complejo** silenciosamente (no lanza excepción de inmediato) — el error aparece más adelante, al comparar o sumar ese `g` complejo con el resto de la aritmética, con un traceback que no apunta a la causa real. `architect` debe definir: ¿se excluye el modelo Graham del promedio en este caso (mostrando 2 de 3 valores y aclarándolo a Daniela), se usa un `g` de respaldo (ej. 0%), o se reporta el fallo explícitamente como ya se hace con FRED/Treasury caídos (Decisión #7)? Recomendación de `qa` (no vinculante): mismo patrón que Decisión #7 — nunca inventar un número en silencio, reportar a Daniela que ese modelo no se pudo calcular para esta empresa y promediar solo los otros 2.

**B2 — Menos de 2 años de `/income-statement` anual disponibles.**
Empresa recién salida a bolsa. La Decisión #4 pide "≥5 años" pero no define qué pasa con menos. Con 0 o 1 año de datos, `n_años` en la fórmula de CAGR es 0 o indefinido → `ZeroDivisionError` o `IndexError` si no se maneja. `architect` debe definir el mínimo real de años aceptable (¿2? ¿3?) y el comportamiento por debajo de ese mínimo (mismo patrón sugerido que B1: excluir el modelo Graham/DCF del promedio y avisar a Daniela, en vez de crashear).

**B3 — Pasivos Circulantes = 0 en el Ratio de Liquidez.**
`Ratio de Liquidez = Activos Circulantes / Pasivos Circulantes` (sección "Reglas de validación de empresa"). Una empresa sin deuda de corto plazo (caso real, no hipotético — ocurre) produce `ZeroDivisionError` con la fórmula tal cual está escrita. `architect` debe definir el valor a mostrar en ese caso (ej. "liquidez excelente / no aplica división", o un valor centinela documentado) — no es aceptable que este cálculo crashee el análisis completo de una empresa sin deuda.

**B4 — EPS TTM ≤ 0.**
Rompe PER (`Precio/EPS`, división por cero si EPS=0, resultado negativo sin sentido de negocio si EPS<0) y hace que el modelo de Múltiplos y el modelo Graham produzcan un "valor justo" negativo o indefinido — que luego se promedia con el DCF (que sí puede seguir siendo positivo) sin que la spec diga si eso es válido. La sección "Reglas de validación de empresa" ya reconoce el problema para P/S ("útil cuando EPS es negativo") pero esa alternativa no está conectada a `valuation.py`. `architect` debe definir: ¿se excluyen Múltiplos y Graham del promedio cuando EPS≤0 y se usa solo DCF (aclarándolo a Daniela), se sustituye por P/S como ya insinúa la spec, o se muestra igual con una advertencia explícita de "EPS negativo, valor de Múltiplos/Graham no confiable"?

**B5 — Ambigüedad de frontera en la tabla de perfil de riesgo (menor, no crashea).**
La tabla ("80-120 / 120-180 / 180-240 / 240+") no especifica si el límite superior de cada rango es inclusivo o el inferior del siguiente. Un puntaje de exactamente 120 podría caer en "Muy Conservador" o "Conservador" según cómo se implemente `if/elif`, y hoy dos implementaciones igual de razonables del mismo texto dan resultados distintos para Daniela. Recomendación de `qa`: convención estándar de rango semiabierto — `[80,120) [120,180) [180,240) [240,∞)` (límite inferior inclusivo, superior exclusivo) — pero **`architect` debe confirmarlo explícitamente por escrito en la spec**, no asumirlo implícito, porque determina un resultado real de negocio (el perfil de riesgo de Daniela) en un valor límite plausible.

**Impacto en el pipeline:** siguiendo la Regla 3/Regla 4 de `pipeline.md`, esto es un **bloqueante** (huecos de diseño, no detalle de implementación) → vuelve a `architect` como Iter-2 con un **spec patch** acotado a estos 5 puntos. No toca stack, arquitectura, seguridad ni el resto de las fórmulas — no requiere pasar de nuevo por `security` ni `frontend` salvo que el patch cambie superficie de seguridad (no debería). Una vez emitido el patch, `qa` solo necesita confirmar en una revisión corta que los 5 puntos quedaron con comportamiento definido y testeable — no hace falta rehacer esta sección completa.

---

### Veredicto de `qa`

**La spec NO queda lista para scope freeze todavía.** Las secciones 1 a 7 de este bloque (criterios de test, testabilidad de APIs, cobertura del flujo conversacional, definición de "listo") están completas y no requieren más trabajo de `qa` — pero **5 casos límite financieros (B1-B4 con riesgo real de crash, B5 de ambigüedad de negocio) no tienen comportamiento definido en la spec de `architect`**, y sin eso no se pueden escribir como criterios de aceptación deterministas ni como tests con output esperado.

**Siguiente paso:** `architect` emite un spec patch acotado a B1-B5 (Regla 4 de `pipeline.md` — patch mínimo, no spec nueva, no reinicia el pipeline). El patch no debería tocar seguridad, stack ni el resto de las fórmulas, así que no necesita volver a pasar por `security`. Con el patch resuelto, `qa` confirma en una pasada corta y el pipeline puede ir directo a `implementer` (sigue sin aplicar `frontend` — no hay UI).

**Todo lo demás de la spec (architect + security + esta sección de qa) queda congelado y no se reabre** — el patch de `architect` debe limitar su alcance exactamente a B1-B5.

---

## Spec Patch [Iter-2] para: SDD_investbot_mvp.md

**Rol:** `architect`. **Fecha:** 2026-07-27. **Alcance:** acotado exactamente a los 5 bloqueantes B1-B5 reportados por `qa` en la sección "Bloqueantes para `architect` — antes de scope freeze" de este mismo documento. No reabre stack, arquitectura de despliegue, ni ningún criterio de seguridad de las secciones 1-5 de `security` (ver "Criterios que NO cambian" abajo).

### Criterio que falló

`qa` no pudo convertir en tests deterministas los siguientes 5 puntos porque la spec Iter-1 no define un resultado esperado para casos límite financieros reales:

- **B1** — `g = (EPS_reciente/EPS_antiguo)^(1/n_años) - 1` (Decisión #4) devuelve un número complejo en silencio cuando `EPS_antiguo` (o `EPS_reciente`) es negativo, en vez de lanzar una excepción visible en el punto del error.
- **B2** — la Decisión #4 pide "≥5 años" de `/income-statement` como ideal pero no define el piso mínimo aceptable ni el comportamiento por debajo de ese piso; con 0-1 años, `n_años` es 0/indefinido → `ZeroDivisionError`/`IndexError`.
- **B3** — `Ratio de Liquidez = Activos Circulantes / Pasivos Circulantes` no define qué hacer cuando Pasivos Circulantes = 0 (empresa sin deuda de corto plazo, caso real) → `ZeroDivisionError`.
- **B4** — no hay comportamiento definido para Múltiplos y Graham EPS Model cuando `EPS TTM ≤ 0`; la spec menciona P/S como alternativa pero nunca la conecta a `valuation.py`.
- **B5** — la tabla de perfil de riesgo (80-120/120-180/180-240/240+) no especifica si el límite superior de cada rango es inclusivo o exclusivo; dos implementaciones igual de razonables del mismo texto dan un perfil distinto a Daniela para un puntaje de exactamente 120, 180 o 240.

### Ajuste de diseño

**Principio general que gobierna B1, B2 y B4 (nuevo, no existía en Iter-1): exclusión de modelo + promedio parcial, nunca inventar un número.**

Se adopta y generaliza la recomendación de `qa` para B1 (mismo patrón que la Decisión de diseño #7: nunca inventar un número en silencio). Se aplica como regla única a los 3 modelos de valoración:

- Cada uno de los 3 modelos (Múltiplos, Graham EPS Model, DCF) se calcula de forma independiente. Si un modelo no puede calcularse porque los datos de entrada son inválidos o insuficientes (según las reglas específicas de B1/B2/B4 más abajo), **se excluye del promedio** — nunca se sustituye por `0`, `None` tratado como cero, o un valor de respaldo inventado.
- `Valor Justo Total` = promedio simple **únicamente de los modelos calculables** en esa consulta.
- Si se excluye 1 de 3 modelos: se promedian los 2 restantes, y la respuesta a Daniela indica explícitamente cuál se excluyó y por qué (en el estilo dummy-friendly ya definido, sin jerga).
- Si se excluyen 2 de 3 modelos: se muestra el único valor restante, etiquetado explícitamente como **"valor aproximado, basado en un solo modelo"** — nunca como "Valor Justo Total" promedio, para no sugerir una precisión que no existe.
- Si se excluyen los 3 (caso extremo, ej. empresa sin historial suficiente y con pérdidas simultáneamente): el bot **no muestra ningún Valor Justo**, responde con un mensaje explícito de que no fue posible valorar la empresa con los datos disponibles, y **sigue mostrando el resto del análisis** que sí sea calculable (pilares de "buena empresa" que no dependan de valoración, ej. liquidez, crecimiento de ingresos). El proceso nunca crashea ni deja de responder.

**Estructura de datos requerida para que esto sea testeable (consistente con la sección 6 de `qa`, "funciones puras"):** `valuation.py` retorna una estructura con, como mínimo:
```
{
  "valor_justo_multiplos": float | None,
  "valor_justo_graham": float | None,
  "valor_justo_dcf": float | None,
  "valor_justo_total": float | None,   # None solo si los 3 son None
  "modelos_excluidos": [ {"modelo": "graham", "motivo": "eps_base_negativo"}, ... ],
}
```
`summary.py` (no `valuation.py`) es responsable de convertir `modelos_excluidos` en el texto dummy-friendly que ve Daniela — mantiene la separación ya exigida por `qa` (cálculo puro vs. presentación). Los tests de `qa` sobre B1/B2/B4 assertan contra esta estructura, no contra el texto final renderizado.

---

#### B1 — CAGR con EPS base negativo o cambio de signo

**Decisión:** se introduce una función auxiliar única, reutilizada por **todo** cálculo de CAGR del sistema (no solo el `g` de EPS para Graham — también el CAGR de FCF que la Decisión #10 usa para proyectar el DCF, porque tiene exactamente el mismo riesgo matemático con una base de FCF negativa o cero):

```python
def calculate_cagr(valor_reciente: float, valor_antiguo: float, n_años: int) -> Optional[float]:
    """
    Nunca lanza excepción, nunca devuelve un número complejo. Devuelve None cuando
    el CAGR no es matemáticamente válido o financieramente significativo:
      - valor_antiguo <= 0  → año base en pérdidas o en cero: una base negativa
        elevada a un exponente fraccionario produce un complejo en Python; base 0
        hace la tasa indefinida.
      - valor_reciente <= 0 → año más reciente en pérdidas o en cero: el ratio
        resultante es <= 0, con el mismo riesgo de complejo o de resultado sin
        sentido de negocio.
      - n_años < 2 → ver B2 (piso mínimo de historial).
    El llamador es responsable de excluir el modelo correspondiente del promedio
    y registrar el motivo en `modelos_excluidos` — nunca de sustituir el resultado
    por un valor de respaldo.
    """
```

- Si `calculate_cagr(...)` para el CAGR de EPS retorna `None` → se excluye el **Graham EPS Model** del promedio, motivo `"eps_base_no_positivo"` o `"eps_reciente_no_positivo"` (según cuál de los dos falló).
- Si `calculate_cagr(...)` para el CAGR de FCF retorna `None` (aplicando la misma guarda a los datos de `/cash-flow-statement`) → se excluye el **DCF** del promedio, motivo `"fcf_base_no_positivo"` o `"fcf_reciente_no_positivo"`. Esto cierra un hueco que la Decisión #10 dejaba abierto (reutiliza el mismo CAGR o uno propio de FCF, sin definir qué pasa si tampoco es calculable).
- El modelo de **Múltiplos no depende de CAGR** (Decisión #9) — nunca se excluye por esta razón.

**Criterios de aceptación (testeables):**
- [ ] `test_calculate_cagr_base_negativa`: `calculate_cagr(reciente=5, antiguo=-2, n_años=5)` retorna `None` (no lanza excepción, no retorna un `complex`).
- [ ] `test_calculate_cagr_base_cero`: `calculate_cagr(reciente=5, antiguo=0, n_años=5)` retorna `None`.
- [ ] `test_calculate_cagr_reciente_negativo`: `calculate_cagr(reciente=-1, antiguo=5, n_años=5)` retorna `None`.
- [ ] `test_calculate_cagr_valido`: `calculate_cagr(reciente=8, antiguo=5, n_años=4)` retorna un `float` positivo, verificado a mano.
- [ ] `test_valuation_graham_excluido_por_eps_base_negativo`: con EPS histórico que tiene un año base negativo, `valuation.py` retorna `valor_justo_graham=None`, `"graham"` presente en `modelos_excluidos` con motivo `"eps_base_no_positivo"`, y `valor_justo_total` = promedio de Múltiplos y DCF únicamente (no incluye a Graham ni lo trata como 0).
- [ ] `test_valuation_dcf_excluido_por_fcf_base_negativo`: mismo patrón que el test anterior, aplicado a FCF y al modelo DCF.

---

#### B2 — Menos de 2 años de `/income-statement` (o `/cash-flow-statement`) anual disponibles

**Decisión:** el piso mínimo aceptable para calcular cualquier CAGR (EPS o FCF) es **3 registros anuales** (es decir, `n_años >= 2` entre el dato más antiguo y el más reciente en la fórmula de `calculate_cagr`). Con 0, 1 o 2 registros anuales (`n_años < 2`), `calculate_cagr` retorna `None` por la misma guarda de B1 — no se intenta ni se acerca a una división por cero o índice fuera de rango.

**Justificación del piso (3, no 2):** un CAGR calculado sobre un solo período interanual (`n_años=1`, 2 registros) es demasiado ruidoso para proyectar 5 años hacia adelante (base del DCF) o para ponderar el multiplicador de Graham — se prefiere no calcular el modelo a calcularlo con una señal de baja confianza sin advertirlo. Esto no cambia la Decisión #4 (que sigue pidiendo ≥5 años como caso ideal); B2 solo define el piso por debajo del cual el motor deja de intentarlo.

**Criterios de aceptación (testeables):**
- [ ] `test_calculate_cagr_menos_de_3_años`: `calculate_cagr(reciente=5, antiguo=3, n_años=1)` retorna `None`.
- [ ] `test_calculate_cagr_exactamente_piso`: `calculate_cagr(reciente=5, antiguo=3, n_años=2)` (3 registros) retorna un `float` válido, no `None`.
- [ ] `test_valuation_empresa_reciente_ipo`: con 1 año de `/income-statement` mockeado (empresa recién salida a bolsa), `valuation.py` retorna `valor_justo_graham=None` y `valor_justo_dcf=None` (si el DCF también depende de FCF con el mismo historial insuficiente), ambos en `modelos_excluidos` con motivo `"historial_insuficiente"`, y `valor_justo_total` = valor de Múltiplos únicamente, etiquetado como aproximado según la regla general.
- [ ] `test_valuation_0_de_3_modelos`: con datos que invalidan los 3 modelos simultáneamente (historial insuficiente + EPS TTM ≤ 0, ver B4), `valuation.py` retorna `valor_justo_total=None` sin lanzar excepción; `query_handler.py`/`summary.py` responde con un mensaje explícito de "no fue posible valorar la empresa" y sigue mostrando el resto del análisis no dependiente de valoración (test de integración, no solo unitario).

---

#### B3 — Pasivos Circulantes = 0 en el Ratio de Liquidez

**Decisión:** `rules.py` nunca ejecuta la división si el denominador es 0. Cuando `Pasivos Circulantes == 0`:
- `ratio_liquidez` se retorna como `None` (no `float('inf')`, no un número inventado).
- Se agrega un flag `liquidez_sin_pasivos_circulantes = True` en el resultado de `rules.py`.
- El pilar **"deuda controlada"** (sección "Pilares de buena empresa") se marca `True` cuando `liquidez_sin_pasivos_circulantes` es `True` — una empresa sin deuda de corto plazo satisface trivialmente el criterio de "deuda controlada", no es un caso de error de negocio, solo de aritmética.
- `summary.py` muestra el mensaje "Sin deuda de corto plazo — señal muy positiva" en vez de un número, cuando `liquidez_sin_pasivos_circulantes` es `True`.

**Criterios de aceptación (testeables):**
- [ ] `test_ratio_liquidez_pasivos_circulantes_cero`: con `Activos Circulantes=100`, `Pasivos Circulantes=0`, `rules.py` retorna `ratio_liquidez=None`, `liquidez_sin_pasivos_circulantes=True`, sin lanzar `ZeroDivisionError`.
- [ ] `test_pilar_deuda_controlada_sin_pasivos_circulantes`: con el resultado anterior, el pilar "deuda controlada" se evalúa como cumplido (`True`) en la salida de `rules.py`/`query_handler.py`.
- [ ] `test_ratio_liquidez_caso_normal`: con `Pasivos Circulantes > 0`, el comportamiento no cambia respecto a la fórmula original de Iter-1 (regresión).

---

#### B4 — EPS TTM ≤ 0

**Decisión:** se excluyen **Múltiplos y Graham EPS Model** del promedio cuando `EPS TTM <= 0` — **no** se conecta P/S como sustituto numérico dentro del promedio de Valor Justo. Motivo explícito de por qué no (para que `implementer` no lo reabra): calcular un "Valor Justo por P/S" comparable requiere el P/S de cada peer del set fijo (Decisión #9), lo que implica una llamada adicional (`/key-metrics` o `/income-statement` de cada peer) por consulta — esto reabriría el presupuesto de requests de la Decisión #8/#9 (250 req/día, 9-12 por consulta), que está fuera del alcance de este patch (B1-B5 no tocan infraestructura de despliegue ni las decisiones de presupuesto de FMP). En vez de eso:

- `PER` se retorna como `None` con flag `per_no_aplicable = True` cuando `EPS TTM <= 0` (mismo patrón que B3: nunca una división por cero ni un PER negativo sin sentido).
- `P/S` se sigue calculando y mostrando **siempre** (ya está en el motor de ratios de Iter-1, "Reglas de validación de empresa" — no es un cálculo nuevo) — cuando `EPS TTM <= 0`, se muestra como la métrica de referencia informativa del pilar "precio razonable" en lugar del PER, con una nota aclaratoria de que el PER no aplica por EPS negativo/cero. **No participa del promedio de Valor Justo Total** — sigue siendo solo una métrica del análisis de ratios, igual que en Iter-1.
- Múltiplos y Graham se excluyen del promedio con motivo `"eps_ttm_no_positivo"` cada uno; DCF (que no depende de EPS TTM, solo de FCF) sigue siendo calculable de forma independiente y normalmente participa solo (regla general: "1 de 3" → etiquetado como aproximado).

**Criterios de aceptación (testeables):**
- [ ] `test_per_eps_cero`: con `EPS TTM = 0`, `rules.py` retorna `per=None`, `per_no_aplicable=True`, sin `ZeroDivisionError`.
- [ ] `test_per_eps_negativo`: con `EPS TTM = -1.5`, mismo resultado que el test anterior (`per=None`, `per_no_aplicable=True`).
- [ ] `test_valuation_multiplos_y_graham_excluidos_eps_no_positivo`: con `EPS TTM <= 0`, `valuation.py` retorna `valor_justo_multiplos=None` y `valor_justo_graham=None`, ambos en `modelos_excluidos` con motivo `"eps_ttm_no_positivo"`, y `valor_justo_total` = solo el valor de DCF, etiquetado como aproximado (si DCF sí es calculable).
- [ ] `test_ps_se_muestra_cuando_eps_no_positivo`: con `EPS TTM <= 0`, el resultado de `rules.py` incluye `ps` (Precio/Ventas) calculado normalmente y no `None` (asumiendo Ventas Totales > 0).
- [ ] `test_ps_no_participa_del_promedio`: confirmar explícitamente que `valor_justo_total` nunca incorpora `ps` como cuarto término del promedio — sigue siendo promedio de máximo 3 modelos (Múltiplos, Graham, DCF), nunca 4.

---

#### B5 — Frontera de la tabla de perfil de riesgo

**Decisión:** se confirma por escrito la recomendación de `qa` — **convención de rango semiabierto, límite inferior inclusivo, límite superior exclusivo**, excepto el último rango que es abierto hacia arriba:

```
[80, 120)  → Muy Conservador
[120, 180) → Conservador
[180, 240) → Moderado
[240, ∞)   → Agresivo   (en la práctica, [240, 400] por el máximo ya verificado en Iter-1)
```

Equivalente en código:
```python
def calcular_perfil(puntaje: int) -> str:
    if puntaje < 120:
        return "muy_conservador"
    elif puntaje < 180:
        return "conservador"
    elif puntaje < 240:
        return "moderado"
    else:
        return "agresivo"
```

Esto es consistente con el mínimo=80/máximo=400 ya verificado en Iter-1 (sección "Verificación de rango tras el ajuste de la pregunta 7") — el motor del cuestionario garantiza por construcción (suma de valores fijos de opciones, 8 preguntas) que el puntaje siempre cae en `[80, 400]`, así que no se requiere manejo adicional de rango fuera de esos límites (ni `puntaje < 80` ni `puntaje > 400` son alcanzables con el cuestionario tal como está definido).

**Criterios de aceptación (testeables) — 8 valores límite, uno por cada frontera:**
- [ ] `test_perfil_80` → `"muy_conservador"` (mínimo posible).
- [ ] `test_perfil_119` → `"muy_conservador"`.
- [ ] `test_perfil_120` → `"conservador"` (frontera: inclusivo hacia el rango superior).
- [ ] `test_perfil_179` → `"conservador"`.
- [ ] `test_perfil_180` → `"moderado"`.
- [ ] `test_perfil_239` → `"moderado"`.
- [ ] `test_perfil_240` → `"agresivo"`.
- [ ] `test_perfil_400` → `"agresivo"` (máximo posible).

---

### Criterios adicionales

- [ ] Los 5 conjuntos de criterios de aceptación de B1-B5 arriba (28 tests puntuales en total) se agregan al checklist de scope freeze junto con los ya existentes de `architect`/`security`/`qa` Iter-1.
- [ ] `valuation.py` expone la estructura de retorno documentada en "Ajuste de diseño" (`valor_justo_*`, `modelos_excluidos`) — es un criterio de estructura de código, no solo de comportamiento, y es lo que hace testeables B1/B2/B4 sin parsear texto.
- [ ] `rules.py` expone `per_no_aplicable`/`liquidez_sin_pasivos_circulantes` como flags explícitos en su resultado, no solo como valores `None` sin contexto — así los tests de B3/B4 pueden assertar sobre el motivo, no solo sobre la ausencia de crash.
- [ ] Ningún criterio nuevo de este patch requiere una llamada adicional a FMP/FRED más allá de las ya aprobadas en Iter-1 (confirmado explícitamente en la decisión de B4 al descartar P/S como sustituto activo en el promedio).
- [ ] El caso de regresión Adobe (`test_valuation_adobe_regression`, ya exigido por `architect`+`qa` en Iter-1) sigue pasando sin cambios — ninguna de las 5 decisiones de este patch altera el resultado cuando los 3 modelos son calculables normalmente (que es el caso de Adobe).

### Criterios que NO cambian

Todos los demás criterios de la spec original (Iter-1) siguen vigentes sin cambios, en particular:
- Stack (Python 3.12 + `python-telegram-bot` + `httpx` + SQLite), arquitectura de despliegue (long polling, sin Traefik, sin puertos públicos), y el modelo de datos SQLite — sin cambios.
- Las 5 secciones de `security` (filtro de `chat_id`, gestión de secretos, manejo de errores de FMP/FRED, validación de input, rate-limit/hardening de contenedor) — **vigentes sin cambios, ninguna de las 5 decisiones de este patch toca superficie de seguridad.** B1-B4 son cambios puramente aritméticos dentro de `valuation.py`/`rules.py` (funciones puras, sin I/O, según la sección 6 de `qa`); B5 es una función de mapeo puntaje→string sin entrada externa. Ninguno introduce una llamada HTTP nueva, un secreto nuevo, ni un vector de input no validado.
- Las Decisiones de diseño #1-#10 del `architect` (Iter-1) — sin cambios; este patch **refina** las Decisiones #4 y #10 (define el piso de años y la guarda de `calculate_cagr`) pero no las contradice ni reabre su justificación de fondo (CAGR histórico como fuente de `g`, FRED/Treasury.gov como fuente de Y, motor propio de DCF).
- El caso de regresión Adobe, el cuestionario de 8 preguntas (texto, opciones, puntajes), el presupuesto de requests FMP (9-12 por consulta, ~20-27 consultas/día), y todos los criterios de infraestructura/despliegue — sin cambios.

---

### Handoff → qa

**Specs producidas:** este Spec Patch [Iter-2], agregado al final de `SDD_investbot_mvp.md` (no reemplaza ninguna sección anterior).

**Lo que `qa` debe confirmar (pasada corta, no repetir el análisis completo de Iter-1):**
- [ ] Los 5 bloqueantes (B1-B5) quedaron con comportamiento definido y testeable — en particular, que la estructura de retorno de `valuation.py`/`rules.py` (`modelos_excluidos`, `per_no_aplicable`, `liquidez_sin_pasivos_circulantes`) es suficiente para escribir los 28 tests puntuales listados sin ambigüedad de assert.
- [ ] No quedó ningún caso límite de los 5 originales sin cubrir (ej. confirmar que la combinación "0 de 3 modelos calculables" del test `test_valuation_0_de_3_modelos` está efectivamente cubierta y no es solo una mención).
- [ ] El principio general de "exclusión de modelo + promedio parcial" no introdujo un caso límite nuevo no cubierto (ej. ¿qué pasa si `modelos_excluidos` está vacío pero igual falta un valor? — no debería ocurrir con el diseño propuesto, pero es la clase de chequeo que le corresponde a `qa` confirmar, no a `architect` asumir).

**Si `qa` confirma los 3 puntos anteriores:** el pipeline va **directo a `implementer`** — no vuelve a pasar por `security` ni `frontend`, porque ninguna de las 5 decisiones de este patch toca superficie de seguridad (justificado arriba, en "Criterios que NO cambian") ni hay UI web en este proyecto (ya confirmado por `security` en Iter-1: "no aplica `frontend` a un bot de Telegram sin UI web").

**Si `qa` encuentra que alguno de los 5 sigue sin ser testeable de forma determinista:** vuelve a `architect` como Iter-3, con diagnóstico puntual de cuál de los 5 quedó incompleto — no se reabren los otros 4 que sí queden confirmados (Regla 2 de `pipeline.md`, criterios congelados).

---

## Confirmación de qa — Spec Patch Iter-2 [2026-07-27]

**Rol:** `qa`. Pasada corta de confirmación sobre el Spec Patch [Iter-2] — no repite el análisis de Iter-1 (esa sección sigue congelada).

### B1-B5, uno por uno

- **B1** — Sin ambigüedad. `calculate_cagr` tiene contrato explícito (`None` en base≤0, reciente≤0, n_años<2) y 6 tests con inputs/outputs numéricos concretos, verificables a mano. Testeable tal cual.
- **B2** — Sin ambigüedad. Piso fijado en 3 registros (`n_años>=2`), con test de frontera exacta (`n_años=1`→`None`, `n_años=2`→float válido). Único punto no crítico: `test_valuation_empresa_reciente_ipo` dice que DCF se excluye "si también depende de FCF con el mismo historial insuficiente" — es condicional en la redacción, pero se resuelve construyendo el fixture con income-statement y cash-flow-statement ambos de 1 año; no es una decisión de diseño pendiente, es detalle de fixture. No bloquea.
- **B3** — Sin ambigüedad. Retorno `None` + flag `liquidez_sin_pasivos_circulantes=True` + pilar "deuda controlada" = `True`. Los 3 tests tienen assert determinista directo.
- **B4** — Sin ambigüedad. `per=None` + `per_no_aplicable=True`; P/S se calcula y se muestra pero nunca entra al promedio (test dedicado a confirmar que el promedio nunca tiene 4 términos). Motivo de por qué no se usa P/S como sustituto queda documentado, evitando que `implementer` lo reabra.
- **B5** — Sin ambigüedad. Convención semiabierta confirmada por escrito, con función de referencia y 8 tests de frontera exacta (79/80 no incluido explícitamente pero 119/120 sí, cubre igual el par crítico de cada borde). Totalmente determinista.

**`test_valuation_0_de_3_modelos`** (combinación B2+B4): la parte estructural (`valor_justo_total=None`) es determinista; la verificación del mensaje a Daniela solo exige que el texto contenga la idea de "no fue posible valorar" (no un string exacto) — correcto, porque `summary.py` es responsable del copy y no se le exige a `architect` fijar redacción exacta en este patch. Test escribible como substring/contains, no como igualdad estricta. No es un hallazgo, es la forma correcta de testear texto de UI conversacional.

### Reapertura de lo congelado

Confirmado: el patch no reabre stack, arquitectura de despliegue, modelo de datos, las 5 secciones de `security` (Iter-1), ni los criterios de `qa` Iter-1 fuera de B1-B5. Las Decisiones #4 y #10 se refinan (piso de años, guarda de CAGR) sin contradecir su justificación de fondo. El caso de regresión Adobe y el cuestionario de 8 preguntas quedan intactos. No se detectó ningún criterio nuevo fuera del alcance B1-B5.

### Confirmación de superficie de seguridad/UI

Se confirma la afirmación de `architect`: B1-B4 son cambios puramente aritméticos en funciones puras (`valuation.py`/`rules.py`, sin I/O), B5 es un mapeo puntaje→string sin input externo. Ninguno introduce llamada HTTP nueva, secreto nuevo, ni vector de input no validado — no hay razón para volver a pasar por `security`. No hay UI web en este proyecto (bot de Telegram), por lo que `frontend` tampoco aplica, consistente con lo ya establecido en Iter-1.

### Veredicto de `qa`

**SCOPE FREEZE confirmado.** Los 5 bloqueantes B1-B5 quedaron con comportamiento definido, estructura de retorno concreta (`modelos_excluidos`, `per_no_aplicable`, `liquidez_sin_pasivos_circulantes`) y criterios de aceptación con assert determinista, escribibles como test unitario sin inventar nada. El pipeline pasa **directo a `implementer`**, sin volver a `security` ni a `frontend`.

---

## Spec Patch [Iter-3] para: SDD_investbot_mvp.md — Escenarios Pesimista/Conservador/Optimista en los 3 modelos de valoración

**Rol:** `architect`. **Fecha:** 2026-07-28.

**Origen (distinto a Iter-2):** este patch **no** nace de una escalación de `qa`/`implementer` — el pipeline ya llegó a scope freeze en Iter-2 y el bot está desplegado en producción. Es un **pedido nuevo de negocio de Daniela** (textual): quiere que el bot calcule un rango Pesimista/Conservador/Optimista para los **3 modelos completos de valoración** (Múltiplos, Graham EPS Model, DCF) — hoy el único lugar donde existe algo parecido a un "rango" es el promedio de PER de peers dentro de Múltiplos, y ni siquiera se expone como rango. Se documenta como **spec patch (Iter-3)**, no como spec nueva, porque el cambio es acotado a `valuation.py`, `peers.py` y `summary.py`, sin reabrir stack, arquitectura de despliegue, modelo de datos SQLite, ni ninguna de las 5 secciones de `security` (Iter-1) — mismo criterio de alcance que ya se aplicó en Iter-2.

**Nota de estado del código (para que `implementer` no se confunda con la spec):** `valuation.py` y `peers.py` en el repo real ya incorporan dos fixes post-despliegue no documentados formalmente en esta spec — migración de `/api/v3` (discontinuada) a la API "stable" de FMP, y reemplazo de `/key-metrics-ttm` (resultó ser de pago) por `/key-metrics` anual, derivando el PER de peers como `1 / earningsYield`. Este patch parte del código real tal como está hoy (leído directamente de `src/investbot/valuation.py`, `src/investbot/peers.py`, `src/investbot/rules.py`, `src/investbot/summary.py`), no de la redacción literal de endpoints de Iter-1/Iter-2.

---

### Estado actual

- `valuation.py::compute_valuation(...)` calcula **un único punto** por modelo (Múltiplos, Graham, DCF) y un `valor_justo_total` (promedio simple de los modelos calculables), aplicando las guardas de exclusión de Iter-2 (B1/B2/B4). No existe ningún concepto de escenario.
- `peers.py::get_peer_pe_average(...)` calcula **solo el promedio** de PER de los peers válidos (`PeerAverageResult(per_promedio, peers_usados)`) — no conserva la lista de PERs individuales, así que hoy no hay forma de derivar un mínimo/máximo sin recalcular.
- `summary.py::build_valuation_section(...)` muestra un único número por modelo y un único `valor_justo_total`/"valor aproximado" (según cuántos modelos se excluyeron), sin ningún rango.
- `tests/test_valuation.py::test_valuation_adobe_regression` llama a `compute_valuation(...)` directo y asume implícitamente que ese único resultado es "el" resultado — hoy equivale, sin decirlo explícitamente, al que este patch va a nombrar **escenario conservador**.

### Estado objetivo

Cada consulta de empresa calcula **3 escenarios completos** (pesimista/conservador/optimista) para los 3 modelos, reutilizando exactamente los mismos datos ya obtenidos de FMP/FRED en esa consulta (sin ninguna llamada HTTP adicional — ver punto 5 abajo). La respuesta a Daniela sigue centrada en un solo número accionable (el escenario conservador, sin cambios de comportamiento respecto a hoy) más una línea adicional de rango, sin abrumarla con 9 números.

---

### 1. Qué varía en cada modelo para generar los 3 escenarios

**Principio general de diseño:** el escenario **conservador es, número por número, el cálculo que el bot ya hace hoy** (Iter-1/Iter-2, sin cambios) — nunca se toca su fórmula ni sus guardas. Pesimista y optimista son variaciones **alrededor** de ese mismo cálculo, no un modelo nuevo. Esto es deliberado: minimiza el riesgo de este patch (el comportamiento actual, ya validado con el caso Adobe, queda intacto) y responde directamente al punto 4 del pedido.

**Múltiplos — se propone PER mínimo/promedio/máximo del set de peers, no desviación estándar.**
- Pesimista = EPS TTM × **PER mínimo** de los peers válidos del sector.
- Conservador = EPS TTM × **PER promedio** de los peers válidos (sin cambios respecto a hoy).
- Optimista = EPS TTM × **PER máximo** de los peers válidos.
- **Por qué min/max y no desviación estándar:** el set de peers es de 3 a 5 tickers (Decisión #9, Iter-1) — una desviación estándar sobre una muestra de n=3-5 es estadísticamente ruidosa (un solo peer atípico la distorsiona) y, más importante, es difícil de explicar en el formato "explícamelo como si fuera tonto" (¿cómo le explicás una desviación estándar a Daniela sin jerga?). El mínimo/máximo, en cambio, es literal y verificable: "el peer más barato" y "el peer más caro" del set — consistente con el principio de transparencia ya usado en toda la spec (ej. Decisión #9 ya dice "PER promedio de un set fijo de comparables, no del sector completo").
- **Caso degenerado (menos de 2 peers con PER válido):** si solo hay 1 peer válido (o 0, que ya excluye el modelo entero por Iter-2), mínimo = promedio = máximo — no hay rango real. Se documenta explícitamente en la respuesta ("no hay rango disponible para Múltiplos: solo 1 comparable con datos válidos") en vez de mostrar 3 números iguales sin explicación.

**Graham EPS Model — se propone desplazar `g` (CAGR histórico) en puntos porcentuales absolutos, no multiplicarlo por un factor.**
- Pesimista: `g_pesimista = g_conservador - DELTA_G`
- Conservador: `g` = CAGR histórico tal cual (Decisión #4, sin cambios).
- Optimista: `g_optimista = g_conservador + DELTA_G`
- `DELTA_G` propuesto: **3 puntos porcentuales (0.03 en fracción decimal)** — constante documentada, ajustable por Daniela (mismo tratamiento que la prima de riesgo de mercado de 5.5% en la Decisión #10 — asunción explícita del `architect`, no viene del material fuente).
- **Por qué un desplazamiento absoluto (±3pp) y no un factor multiplicativo (ej. ×0.5 / ×1.5):** un factor multiplicativo invierte la lógica cuando `g` ya es negativo (una empresa con EPS decreciente) — multiplicar un `g` negativo por 1.5 lo hace *más* negativo, lo cual describir como "optimista" sería contradictorio y confuso de explicar. Un desplazamiento absoluto es direccionalmente consistente sin importar el signo de `g`: pesimista siempre resta, optimista siempre suma, sea cual sea el punto de partida.
- **Guarda nueva (cierra un hueco que ya existía en Iter-2, expuesto ahora por este patch):** la fórmula de Graham usa el multiplicador `(8.5 + 2×g_pct)`. Iter-2 (bloqueante B1, sección "casos límite financieros") ya reconocía que "la fórmula funciona matemáticamente con g negativo mientras `(8.5 + 2×g) > 0`" pero **nunca agregó esa guarda al código** — `calculate_graham_fair_value` hoy no valida el signo del multiplicador. Con `g` conservador cercano a cero y `DELTA_G` restándole 3 puntos, el escenario pesimista puede empujar el multiplicador a cero o negativo con más frecuencia que antes (antes solo ocurría con un CAGR histórico ya muy negativo). Este patch **agrega la guarda**: si `(8.5 + 2×g_pct) <= 0` para un escenario dado, ese escenario de Graham se excluye (no se calcula un "valor justo" negativo o cero). Esta guarda aplica a los 3 escenarios por igual (incluido el conservador, cerrando el hueco de Iter-2 de forma retroactiva y consistente).

**DCF — se propone variar tanto el WACC como la tasa de crecimiento del FCF proyectado, con el mismo criterio de desplazamiento absoluto.**
- `wacc_pesimista = wacc_conservador + DELTA_WACC` (mayor tasa de descuento → menor valor presente → más pesimista).
- `wacc_optimista = wacc_conservador - DELTA_WACC` (menor tasa de descuento → mayor valor presente → más optimista).
- `wacc_conservador` = el WACC ya calculado hoy (sin cambios, Decisión #10).
- `g_fcf` (CAGR de FCF usado para proyectar los 5 años) se desplaza con el **mismo `DELTA_G`** que Graham (±3pp) — no se introduce una constante separada por modelo; es la misma idea de "crecimiento futuro más pesimista/optimista" aplicada al mismo tipo de dato (un CAGR histórico), y usar una única constante es más simple de mantener y de explicar que dos constantes con el mismo propósito.
- `DELTA_WACC` propuesto: **1 punto porcentual (0.01 en fracción decimal)** — constante documentada, ajustable.
- La tasa de crecimiento terminal (`TERMINAL_GROWTH_RATE = 2.5%`, perpetuidad de Gordon Growth) **no varía por escenario** — decisión del `architect`, no pregunta abierta: es un supuesto macro de largo plazo (crecimiento nominal de la economía), independiente del optimismo/pesimismo específico de la empresa consultada; ya son 2 palancas (WACC + `g_fcf`) las que generan el rango, agregar una tercera variable compuesta sobre una perpetuidad no aporta señal adicional proporcional a la complejidad que suma.
- **Reutiliza la guarda ya existente** (`wacc <= terminal_growth → None`, `calculate_dcf_fair_value`, Iter-1/Iter-2) sin cambios — si `wacc_optimista` (el más bajo de los 3) cae por debajo de `TERMINAL_GROWTH_RATE`, el escenario optimista del DCF queda excluido por la guarda que ya existe, sin código nuevo para ese caso puntual.

---

### 2. Interacción con la regla de "exclusión de modelo" de Iter-2 — dos niveles de exclusión

Se introduce una distinción explícita que Iter-2 no necesitaba (porque no existían escenarios):

- **Exclusión de base (nivel 1, igual en los 3 escenarios):** cuando el dato crudo de entrada es inválido o insuficiente independientemente del escenario — `EPS TTM <= 0` (B4), historial de EPS/FCF con menos de 3 registros o con año base/reciente no positivo (B1/B2), `Y` no disponible, `market_cap <= 0` (WACC no calculable), o 0 peers con PER válido. Si un modelo falla en este nivel, **está excluido en los 3 escenarios por igual**, y se reporta **una sola vez** (no 3 veces) — la razón no cambia entre pesimista/conservador/optimista, así que repetirla 3 veces sería ruido, no transparencia.
- **Exclusión por escenario (nivel 2, puede diferir entre escenarios):** cuando el dato de base es válido (el modelo es calculable en al menos un escenario) pero el desplazamiento pesimista u optimista empuja un valor intermedio fuera de su rango matemáticamente válido. Dos casos concretos ya identificados arriba:
  - Graham: `(8.5 + 2×g_pct) <= 0` en el escenario pesimista (o, en un caso extremo con `g` conservador ya muy negativo, también en conservador — ver guarda nueva arriba) pero no en optimista.
  - DCF: `wacc_optimista <= TERMINAL_GROWTH_RATE` cuando `wacc_conservador` y `wacc_pesimista` sí lo superan.
  - Un modelo puede entonces **estar presente en 2 de 3 escenarios y ausente en el tercero** — esto es un comportamiento esperado y correcto del diseño, no un bug: cada escenario se evalúa de forma independiente con sus propios parámetros desplazados, igual que Iter-2 ya evalúa cada uno de los 3 modelos de forma independiente dentro de un mismo escenario.
- **Respuesta a la pregunta 2 del pedido, en una frase:** un modelo excluido en el escenario conservador (nivel 1, dato de base inválido) lo está en los 3 escenarios por igual; un modelo válido en conservador puede sin embargo excluirse solo en pesimista u optimista (nivel 2, el desplazamiento de escenario lo saca de rango matemáticamente válido) — nunca al revés (si un modelo no es calculable en conservador con sus parámetros centrales, tampoco lo es en un escenario que lo empeora o lo mejora, porque el problema está en el dato de entrada, no en el desplazamiento).

**Estructura de datos propuesta** (extiende, sin romper, la de Iter-2):

```python
@dataclass
class ScenarioValuationResult:
    valor_justo_multiplos: Optional[float] = None
    valor_justo_graham: Optional[float] = None
    valor_justo_dcf: Optional[float] = None
    valor_justo_total: Optional[float] = None
    modelos_excluidos: list[ModeloExcluido] = field(default_factory=list)  # incluye nivel 1 + nivel 2

@dataclass
class ValuationScenarios:
    pesimista: ScenarioValuationResult
    conservador: ScenarioValuationResult   # idéntico, campo a campo, al resultado de compute_valuation() hoy
    optimista: ScenarioValuationResult
    modelos_excluidos_base: list[ModeloExcluido]  # solo nivel 1 — igual en los 3, se reporta una vez
```

`compute_valuation(...)` (la función que ya existe) **no se toca** — sigue existiendo tal cual, con la misma firma y el mismo comportamiento, y pasa a ser (conceptualmente) el cálculo del escenario conservador. Se agrega una función nueva, `compute_valuation_scenarios(...)`, que:
1. Calcula una vez las exclusiones de nivel 1 (reutilizando la lógica ya existente en `compute_valuation`).
2. Para cada uno de los 3 escenarios, recalcula únicamente los modelos que pasaron el nivel 1, con sus parámetros desplazados (`per_promedio_peers` → min/prom/max; `g` → g∓DELTA_G; `wacc` → wacc±DELTA_WACC), aplicando las guardas de nivel 2 correspondientes.
3. No dispara ninguna llamada a `fmp_client.py`/`treasury_client.py`/`peers.py` con red real — recibe los mismos datos ya resueltos que hoy recibe `compute_valuation` (ver punto 5).

Esto evita triplicar la lógica de negocio (un solo camino de cálculo por modelo, parametrizado, no 3 copias) — consistente con el criterio de `qa` (Iter-1, sección 6) de "no hay lógica de negocio escondida/duplicada".

**Cambio requerido en `peers.py`:** `get_peer_pe_average(...)` debe exponer también la lista de PERs individuales de los peers válidos (no solo el promedio), para poder derivar mínimo/máximo sin volver a golpear `/key-metrics`:
```python
@dataclass
class PeerAverageResult:
    per_promedio: Optional[float]
    per_minimo: Optional[float]   # nuevo
    per_maximo: Optional[float]   # nuevo
    peers_usados: list[str]
```

---

### 3. Presentación dummy-friendly (`summary.py`) — sin abrumar con 9 números

**Actualizado 2026-07-28 — Daniela respondió (b) y (d), reemplazando la propuesta default original:** quiere el desglose por cada uno de los 3 modelos (no solo el total) **y** la clasificación "barata"/"cara" evaluada en los 3 escenarios, no solo en el conservador. Se rediseña el formato de esta sección para incorporar ambas cosas sin volverla una tabla fría de 9 números sueltos.

**Formato definido:**

Una sola sección de rango, con una línea por modelo + la línea de total, alineando los 3 escenarios en el mismo orden siempre (Pesimista | Conservador | Optimista) para que Daniela no tenga que releer una leyenda cada vez:

```
*Rango de Valor Justo (Pesimista | Conservador | Optimista):*
- Múltiplos: $610 | $658 | $705
- Graham EPS Model: $520 | $555 | $590
- DCF: $270 | $289 | $310

*Valor Justo Total: $467 | $500 | $535*
```

- Si un modelo está excluido en **todos** los escenarios (exclusión de nivel 1): no aparece como fila de números — aparece como la frase de exclusión ya definida en Iter-2 ("el modelo Graham no se pudo calcular: ..."), una sola vez, igual que hoy.
- Si un modelo está excluido **solo en un escenario** (exclusión de nivel 2): esa celda puntual se muestra como `N/D` en vez de omitir la fila entera, y se agrega una frase aclaratoria corta al pie (ej. `"DCF no disponible en el escenario Optimista con estos supuestos — se promedia sin él en ese caso."`) — así Daniela ve que el modelo sí existe, solo que ese escenario puntual no lo pudo calcular.
- Caso degenerado de Múltiplos con < 2 peers válidos (ya definido arriba, sección 1): los 3 valores de esa fila son iguales y se agrega la nota "no hay rango disponible para Múltiplos: solo N comparable(s) válido(s)".

**Clasificación "barata"/"cara" — evaluada de forma independiente en los 3 escenarios (decisión nueva, reemplaza la propuesta default anclada solo a conservador):**

Se compara el precio actual contra el `valor_justo_total` de **cada** escenario por separado — no un solo booleano, sino 3 (uno por escenario), cada uno pudiendo ser `None` si ese escenario en particular no tiene `valor_justo_total` (los 3 modelos excluidos en ese escenario puntual — caso extremo, ver criterio de aceptación nuevo abajo):

```python
def classify_scenario(precio_actual: float, valor_justo_total: Optional[float]) -> Optional[bool]:
    """True = barata (precio < valor justo), False = cara, None = no determinable
    (valor_justo_total es None en ese escenario — los 3 modelos quedaron
    excluidos justo en ese escenario)."""
    if valor_justo_total is None:
        return None
    return precio_actual < valor_justo_total
```

**Regla de combinación para el texto final (determinista, sin ambigüedad de redacción):**
- Si los 3 escenarios (pesimista, conservador, optimista) dan el mismo resultado (los 3 `True` o los 3 `False`, ninguno `None`): se muestra **una sola frase consolidada** — `"Barata en los 3 escenarios (Pesimista, Conservador y Optimista) — señal de confianza adicional."` o el equivalente en "cara". Este es el caso más común (Adobe, por ejemplo, con el rango calibrado hoy da `True` en los 3).
- Si los 3 escenarios **no** coinciden (mezcla de `True`/`False`, o alguno es `None`): se muestra el **desglose explícito por escenario**, nunca se "resume" ocultando la discrepancia (mismo principio de transparencia que gobierna toda la spec):
  ```
  Precio actual: $333
  - Pesimista: Cara (valor justo $310)
  - Conservador: Barata (valor justo $500)
  - Optimista: Barata (valor justo $535)
  ```
- Si un escenario da `None` (caso extremo, 0 de 3 modelos calculables en ese escenario puntual): en el desglose se muestra `"no se pudo determinar en este escenario"` para esa fila, en vez de tratarlo como "cara" por default (nunca inventar una clasificación cuando no hay dato — mismo principio de exclusión de Iter-2).

**Por qué esta regla y no promediar los 3 booleanos o mostrar siempre las 3 líneas:** promediar/forzar un solo veredicto ocultaría justamente la señal que Daniela pidió ("barata incluso en el peor escenario" es información nueva, no ruido) — pero mostrar siempre las 3 líneas aunque coincidan agregaría 2 líneas repetitivas al caso más común. La regla "consolidar si coincide, desglosar si no" da la respuesta más corta posible sin nunca esconder una discrepancia real.

---

### 4. Impacto en el caso de regresión Adobe

- **`test_valuation_adobe_regression` no se modifica.** Sigue llamando a `compute_valuation(...)` (sin cambios de firma ni de comportamiento) y sigue verificando exactamente lo mismo que hoy: Múltiplos≈658, DCF≈289, EPS Model≈555, promedio≈500, tolerancia ±1%. Este test pasa a documentarse (en un comentario, no en el código de assert) como "el caso de regresión valida el escenario conservador" — pero no requiere ni un solo cambio de línea.
- **Se agrega un test nuevo**, `test_valuation_adobe_scenarios`, que reutiliza el mismo fixture de `tests/fixtures/adobe/` y llama a `compute_valuation_scenarios(...)`, verificando:
  - El escenario `conservador` es **campo a campo idéntico** al resultado de `compute_valuation(...)` sobre el mismo fixture (no una aproximación — igualdad exacta, ya que es la misma función internamente).
  - `valor_justo_total` sigue la relación `pesimista <= conservador <= optimista` (relación, no un número fijo — Daniela no dio un caso de referencia numérico para pesimista/optimista de Adobe, a diferencia del caso conservador que sí viene documentado en la spec desde Iter-1).
  - Los 3 modelos siguen calculables en los 3 escenarios para el caso Adobe con los fixtures actuales (Adobe con datos "sanos" no debería disparar ninguna exclusión de nivel 2) — si algún escenario excluye un modelo inesperadamente, es señal de que `DELTA_G`/`DELTA_WACC` propuestos son demasiado agresivos para este fixture, y debe ajustarse antes de cerrar el patch (`qa` lo confirma en su revisión, no `architect` en soledad).
- **Respuesta directa a la pregunta 4 del pedido:** sí, el test existente pasa a validar (implícitamente, sin cambiar código) solo el escenario conservador — y se define un test nuevo separado para pesimista/optimista, con un criterio de relación en vez de un número exacto, porque no existe (todavía) un caso de referencia documentado por Daniela para esos dos escenarios.

---

### 5. Impacto en el presupuesto de requests de FMP — confirmado explícitamente: ninguno

Este patch es una **transformación matemática pura sobre datos ya obtenidos** en la misma consulta — no agrega ninguna llamada HTTP nueva a FMP, FRED ni ningún otro proveedor:
- Los 3 escenarios reutilizan exactamente el mismo `eps_historial`, `fcf_historial`, `wacc_inputs`, `y`, y los mismos PERs de peers (mínimo/promedio/máximo se derivan de la misma respuesta de `/key-metrics` por peer que ya se pedía en Iter-1 — antes se descartaba todo menos el promedio, ahora se conservan también el mínimo y el máximo del mismo array de datos ya en memoria).
- No cambia la sección "Presupuesto de requests FMP (plan gratuito)" de Iter-1: sigue siendo 9-12 requests por consulta completa, ~20-27 consultas/día. **Confirmado.**

---

### 6. Contexto de mercado — momentum y comparación con peers (agregado 2026-07-28, mismo Iter-3, antes de pasar a `security`)

**Origen:** pedido nuevo de Daniela (textual): *"aparte de temas de finanzas no hay que ver también el mercado como se comporta? comparar con otras empresas para saber si se está bien y también coger el miedo del mercado y de cómo está"*. Se aterriza en dos piezas concretas — momentum de precio (proxy barato del "cómo está el mercado viendo esta acción", sin depender de un índice de sentimiento real) y comparación explícita contra peers (hoy invisible, solo insumo silencioso del modelo de Múltiplos). Se agrega **a esta misma iteración** (Iter-3 no pasó a `security` todavía) — no reabre las secciones 1-5 ya cerradas con las decisiones de Daniela.

**Regla dura que gobierna todo el diseño de esta sección (restricción de Daniela, aprendida dos veces en esta misma sesión — `/api/v3` legacy discontinuada, `/key-metrics-ttm` resultó de pago pese a parecer gratis en la documentación):** ningún dato nuevo de esta sección depende de un endpoint no verificado. Todo el diseño default se apoya exclusivamente en campos que **ya vienen gratis, en la misma llamada a `/quote` que ya se hace hoy por cada ticker** (cero llamadas nuevas): `priceAvg50`, `priceAvg200`, `yearHigh`, `yearLow` (confirmados en esta sesión con una key real, respuesta de `/stable/quote` para AAPL) — más el set fijo de peers que `peers.py` ya resuelve para el modelo de Múltiplos (Decisión #9, Iter-1). Un índice tipo VIX/Fear & Greed **no se diseña como dependencia dura** — ver pregunta abierta (f) al final.

#### 6.1 Momentum de precio (proxy de "cómo está el mercado viendo esta acción")

**Qué se muestra:**
- Posición dentro del rango de 52 semanas: `% por debajo de su máximo anual` y `% por encima de su mínimo anual`, derivados de `price` (ya obtenido) vs. `yearHigh`/`yearLow`.
- Posición relativa a las medias móviles: `% por encima/debajo de su promedio de 50 días` y `% por encima/debajo de su promedio de 200 días`, derivados de `price` vs. `priceAvg50`/`priceAvg200`.
- Una etiqueta cualitativa única y determinista, no un índice numérico inventado:
  - **"impulso positivo"** si `price > priceAvg50` **y** `price > priceAvg200`.
  - **"impulso negativo"** si `price < priceAvg50` **y** `price < priceAvg200`.
  - **"mixto"** si está por encima de uno pero no del otro (ej. por encima del promedio de 50 días pero todavía por debajo del de 200 días).
  - **"no disponible"** si falta `priceAvg50` o `priceAvg200` en la respuesta de FMP para ese ticker (puede pasar con compañías muy nuevas, con menos de 200 días de historial de cotización) — nunca se sustituye por un valor inventado, mismo principio de exclusión de toda la spec.

**Estructura de datos propuesta** (función pura, sin I/O, mismo estándar que `valuation.py`/`rules.py`):
```python
@dataclass
class MomentumResult:
    pct_vs_year_high: Optional[float]   # negativo o 0 si está en su máximo anual
    pct_vs_year_low: Optional[float]    # positivo típicamente
    pct_vs_avg_50: Optional[float]
    pct_vs_avg_200: Optional[float]
    etiqueta: str  # "impulso_positivo" | "impulso_negativo" | "mixto" | "no_disponible"

def calculate_momentum(
    *, price: float, year_high: Optional[float], year_low: Optional[float],
    price_avg_50: Optional[float], price_avg_200: Optional[float],
) -> MomentumResult:
    """Nunca lanza excepción; campos faltantes producen None puntual y
    etiqueta="no_disponible" si faltan los dos promedios móviles."""
```

**Transparencia obligatoria en el texto (mismo principio que toda la spec):** esta sección se presenta explícitamente como *"un proxy simple de impulso de precio, no un índice de sentimiento de mercado real (como el VIX o el Fear & Greed Index)"* — para que Daniela no confunda esto con la "medida del miedo del mercado" que pidió textualmente; ver pregunta abierta (f) para esa pieza específica.

**Decisión explícita de scope (no pregunta abierta):** `changePercentage` (variación del día, también gratis en `/quote`) **no se muestra** en esta sección por default — es ruido de corto plazo que no aporta a la narrativa de "cómo está posicionada la empresa" que pidió Daniela (a diferencia del rango de 52 semanas y las medias móviles, que sí son señales de mediano/largo plazo). Queda disponible en la estructura de datos por si se pide un modo más detallado en el futuro (backlog, no bloqueante).

#### 6.2 Comparación explícita con peers (sección nueva, visible)

**Qué se muestra:** el PER de la empresa consultada contra el PER mínimo/promedio/máximo de su set fijo de peers (Decisión #9, ya calculado en la sección 1/2 de este mismo patch para el modelo de Múltiplos) — con una posición relativa explícita:
- **"más barata que sus comparables"** si `per_propio < per_minimo_peers` (más barata que el peer más barato).
- **"en línea con sus comparables"** si `per_minimo_peers <= per_propio <= per_maximo_peers`.
- **"más cara que sus comparables"** si `per_propio > per_maximo_peers`.
- **"no comparable"** si `per_propio` es `None` (EPS TTM ≤ 0, `per_no_aplicable=True` — B4, Iter-2) o si no hay ningún peer con PER válido — en ese caso, la sección lo dice explícitamente en vez de omitirse en silencio, y sugiere mirar el P/S como ya hace el resto de la spec en el mismo caso.

**¿Es información nueva o duplica el modelo de Múltiplos?** Es **el mismo dato de peers, presentado con un enfoque distinto y genuinamente nuevo para Daniela** — no se duplica el cálculo, se reutiliza:
- El modelo de Múltiplos (sección 1 de este patch) responde: *"¿cuánto vale mi empresa en dólares, si pagaran por ella lo mismo que pagan por empresas parecidas?"* (un Valor Justo, en la fila de la tabla de escenarios).
- Esta sección nueva responde: *"¿mi empresa está cara o barata **en términos relativos**, comparada con esas mismas empresas, sin pasar por un cálculo de Valor Justo en dólares?"* — sigue siendo útil incluso cuando el modelo de Múltiplos queda **excluido** de la valoración (ej. EPS TTM ≤ 0, B4 de Iter-2): en ese caso la fila de Múltiplos de la tabla de escenarios no existe, pero esta sección de comparación puede seguir mostrando el PER de los peers (aunque marque "no comparable" para la empresa propia, dado que no tiene PER).
- No se pide ningún dato nuevo: `per_minimo`/`per_promedio`/`per_maximo` de `peers.py::PeerAverageResult` ya se agregaron en la sección 2 de este mismo patch — esta sección solo los consume desde un ángulo de presentación distinto.

**Estructura de datos propuesta:**
```python
@dataclass
class PeerComparisonResult:
    per_propio: Optional[float]
    per_minimo_peers: Optional[float]
    per_promedio_peers: Optional[float]
    per_maximo_peers: Optional[float]
    peers_usados: list[str]
    posicion: str  # "mas_barata" | "en_linea" | "mas_cara" | "no_comparable"

def compare_to_peers(
    *, per_propio: Optional[float], per_minimo_peers: Optional[float],
    per_promedio_peers: Optional[float], per_maximo_peers: Optional[float],
    peers_usados: list[str],
) -> PeerComparisonResult:
    """Función pura, sin I/O. `posicion='no_comparable'` si `per_propio` es
    None o no hay ningún peer con PER válido — nunca inventa una posición."""
```

**Dónde vive:** módulo nuevo `src/investbot/market_context.py` (no se mezcla con `valuation.py`, que es específicamente el motor de los 3 modelos de Valor Justo, ni con `rules.py`, que son ratios/pilares de la empresa propia sin comparación externa) — mantiene la separación de responsabilidades ya exigida por `qa` (Iter-1, sección 6).

#### 6.3 Integración en `summary.py`

Se agrega una sección nueva, **"Contexto de mercado"**, ubicada después de "Pilares de buena empresa" y antes de "Encaje con tu perfil de riesgo" (orden de lectura: valor justo → pilares → contexto de mercado → encaje de riesgo → notas de transparencia):

```
*Contexto de mercado:*
- Cotiza a $187, un 4.2% por debajo de su máximo de 52 semanas ($195) y un 18.6% por encima de su mínimo de 52 semanas ($158).
- Por encima de su promedio de 50 días y de 200 días → impulso positivo.
- Comparada con sus comparables del sector (MSFT, ORCL, CRM): tu PER (28.4) está en línea con el rango de tus peers (mínimo 22.1, promedio 27.9, máximo 33.5).

_Nota: el momentum es un proxy simple de precio, no un índice de sentimiento de mercado (VIX/Fear & Greed) — ver limitación conocida en README.md._
```

- Si `MomentumResult.etiqueta == "no_disponible"`: se omite esa línea puntual (no se muestra "impulso: no disponible" como ruido) y se deja constancia solo si Daniela pregunta por qué falta (backlog, no bloqueante para este patch).
- Si `PeerComparisonResult.posicion == "no_comparable"`: se muestra la frase explícita de por qué ("tu PER no aplica por EPS negativo/cero — mirá el P/S como referencia"), igual que ya hace la sección de ratios (B4, Iter-2) — no se omite en silencio.

#### 6.4 Presupuesto de requests de FMP — confirmado explícitamente: ninguna llamada nueva

- El momentum usa exclusivamente campos ya presentes en la respuesta de `/quote` del ticker propio, que **ya se pide hoy** (Decisión #8, Iter-1) — cero llamadas adicionales.
- La comparación con peers reutiliza el mismo array de PERs de peers que la sección 2 de este mismo patch ya calcula (vía `/key-metrics` por peer, ya presupuestado en Iter-1/Decisión #9) — cero llamadas adicionales.
- **Confirmado:** el presupuesto de requests FMP no cambia (sigue 9-12 por consulta completa, ~20-27 consultas/día).

#### 6.5 VIX / Fear & Greed Index — atajo opcional a verificar, NO dependencia dura

No se puede confirmar sin una key real si un índice tipo `^VIX` (o un endpoint equivalente de sentimiento de mercado) está disponible en el plan gratuito de FMP — la documentación pública de FMP ya mostró ser un mal predictor de disponibilidad real dos veces en este mismo proyecto (`/api/v3` legacy discontinuada, `/key-metrics-ttm` de pago pese a parecer gratis). Siguiendo el mismo patrón que Iter-1 ya usó para `/dcf`/`/sector-pe-ratio`/`/treasury-rates` (Decisión #8): **no se diseña como dependencia dura**. El diseño default de esta sección (6.1-6.4) funciona completo sin ningún índice de mercado externo. Ver pregunta abierta (f) para la verificación.

---

### Criterios de aceptación (nuevos, Iter-3)

- [ ] `compute_valuation(...)` no cambia de firma ni de comportamiento — `test_valuation_adobe_regression` sigue pasando sin modificar ni una línea de ese test.
- [ ] `calculate_graham_fair_value` agrega la guarda `(8.5 + 2×g_pct) <= 0 → None` (motivo `"graham_multiplicador_no_positivo"`), aplicada en los 3 escenarios por igual, incluido el conservador.
- [ ] Nueva función `compute_valuation_scenarios(...)` retorna `ValuationScenarios` (pesimista/conservador/optimista + `modelos_excluidos_base` de nivel 1), sin triplicar la lógica de cálculo de cada modelo.
- [ ] `peers.py::PeerAverageResult` expone `per_minimo` y `per_maximo` además de `per_promedio`, derivados del mismo array de PERs de peers válidos ya calculado (sin llamada adicional a `/key-metrics`).
- [ ] `DELTA_G = 0.03` y `DELTA_WACC = 0.01` viven como constantes documentadas en `valuation.py` (mismo patrón que `MARKET_RISK_PREMIUM`/`TERMINAL_GROWTH_RATE` de Iter-1), ajustables sin que sea una "regresión" de un criterio verde.
- [ ] Cuando un modelo se excluye a nivel base (nivel 1): se reporta una sola vez en `modelos_excluidos_base`, no repetido en cada escenario.
- [ ] Cuando un modelo se excluye solo en un escenario (nivel 2): el `ScenarioValuationResult` de ese escenario específico lo incluye en su propio `modelos_excluidos`, y los otros 2 escenarios lo calculan con normalidad si sus parámetros no disparan la misma guarda.
- [ ] Caso degenerado de Múltiplos con < 2 peers válidos: pesimista/conservador/optimista son el mismo número, y `summary.py` lo aclara explícitamente ("no hay rango disponible, solo N comparable(s) válido(s)") en vez de mostrar 3 valores iguales sin contexto.
- [ ] `test_valuation_adobe_scenarios` (nuevo): conservador idéntico a `compute_valuation()`, relación `pesimista <= conservador <= optimista` en `valor_justo_total`, y los 3 modelos calculables en los 3 escenarios para el fixture de Adobe (sin exclusiones de nivel 2 inesperadas).
- [ ] `summary.py` muestra el rango Pesimista | Conservador | Optimista **desglosado por cada uno de los 3 modelos individuales** (Múltiplos, Graham, DCF) además de la línea de `Valor Justo Total` — no solo el total (actualizado 2026-07-28 por respuesta (d) de Daniela).
- [ ] Una celda de modelo excluido solo en un escenario puntual (nivel 2) se muestra como `N/D` en esa posición específica (no se omite la fila completa), con una frase aclaratoria al pie indicando cuál escenario y por qué.
- [ ] Nueva función pura `classify_scenario(precio_actual, valor_justo_total) -> Optional[bool]` (vive en `valuation.py`, sin I/O) retorna `True`/`False`/`None` según el `valor_justo_total` de cada escenario sea mayor/menor/inexistente frente al precio actual.
- [ ] La clasificación "barata"/"cara" se calcula de forma **independiente en los 3 escenarios** (actualizado 2026-07-28 por respuesta (b) de Daniela, reemplaza el criterio anterior que la anclaba solo a conservador): `summary.py` aplica la regla de combinación — si los 3 escenarios coinciden (los 3 `True` o los 3 `False`), se muestra una sola frase consolidada; si no coinciden o alguno es `None`, se muestra el desglose explícito por escenario, nunca un promedio ni un veredicto único que oculte la discrepancia.
- [ ] Test `test_classify_scenario_barata`/`test_classify_scenario_cara`/`test_classify_scenario_none`: casos con `valor_justo_total` mayor, menor e inexistente (`None`) frente al precio actual.
- [ ] Test `test_combinar_clasificacion_consolidada_barata`/`_cara`: los 3 escenarios coinciden → una sola frase, sin desglose.
- [ ] Test `test_combinar_clasificacion_desglosada`: al menos un escenario difiere de los otros dos (ej. pesimista `False`, conservador/optimista `True`) → las 3 líneas de desglose aparecen explícitamente, ninguna se omite.
- [ ] Test `test_combinar_clasificacion_con_none`: un escenario con `valor_justo_total=None` → esa fila del desglose dice "no se pudo determinar en este escenario", nunca se clasifica como cara/barata por default.
- [ ] Ninguna llamada HTTP nueva a FMP/FRED se agrega en este patch — confirmado por revisión de código (`fmp_client.py`/`treasury_client.py`/`peers.py` no ganan ninguna función nueva de red, solo `valuation.py`/`peers.py` ganan cálculo puro adicional sobre datos ya obtenidos).

**Sección 6 — Contexto de mercado (momentum + comparación con peers), agregado 2026-07-28:**

- [ ] Nueva función pura `calculate_momentum(...)` (`market_context.py`) retorna `MomentumResult` con `pct_vs_year_high`, `pct_vs_year_low`, `pct_vs_avg_50`, `pct_vs_avg_200` y `etiqueta` (`"impulso_positivo"`/`"impulso_negativo"`/`"mixto"`/`"no_disponible"`), sin lanzar excepción si falta `priceAvg50`/`priceAvg200`/`yearHigh`/`yearLow`.
- [ ] `test_momentum_impulso_positivo`/`_negativo`/`_mixto`/`_no_disponible`: 4 casos con `price`/`priceAvg50`/`priceAvg200` conocidos, verificados a mano.
- [ ] Nueva función pura `compare_to_peers(...)` (`market_context.py`) retorna `PeerComparisonResult` con `posicion` (`"mas_barata"`/`"en_linea"`/`"mas_cara"`/`"no_comparable"`), usando `per_minimo_peers`/`per_promedio_peers`/`per_maximo_peers` ya expuestos por `peers.py::PeerAverageResult` (sección 2 de este patch) — sin llamada adicional a `/key-metrics`.
- [ ] `test_compare_to_peers_mas_barata`/`_en_linea`/`_mas_cara`/`_no_comparable_eps_negativo`/`_no_comparable_sin_peers_validos`: 5 casos con `per_propio` y el trío mínimo/promedio/máximo de peers conocidos, verificados a mano.
- [ ] `summary.py` agrega la sección "Contexto de mercado" (momentum + comparación con peers) entre "Pilares de buena empresa" y "Encaje con tu perfil de riesgo", con la nota de transparencia explícita de que el momentum es un proxy simple de precio, no un índice de sentimiento de mercado.
- [ ] Cuando `MomentumResult.etiqueta == "no_disponible"`: esa línea se omite de la respuesta (no se muestra como ruido "impulso: no disponible").
- [ ] Cuando `PeerComparisonResult.posicion == "no_comparable"`: se muestra la frase explícita del motivo (EPS no positivo → sugerencia de mirar P/S, o sin peers con PER válido), nunca se omite en silencio.
- [ ] `changePercentage` (variación diaria) no se muestra en la sección de contexto de mercado — decisión explícita de scope (no aporta a la narrativa de mediano/largo plazo pedida por Daniela), disponible en la estructura de datos para un futuro modo detallado (backlog).
- [ ] Confirmado explícitamente: ninguna llamada HTTP nueva para esta sección — momentum usa campos ya presentes en `/quote` del ticker propio (ya solicitado hoy); comparación con peers reutiliza el mismo array de PERs de peers que la sección 2 de este patch ya calcula (vía `/key-metrics` por peer, ya presupuestado en Iter-1). Presupuesto de requests FMP sin cambios (9-12/consulta, ~20-27/día).
- [ ] Ningún endpoint tipo `^VIX`/Fear & Greed se usa como dependencia dura — el diseño default (6.1-6.4) funciona completo sin él (ver pregunta abierta (f)).

### Artefactos a crear/modificar

- `src/investbot/valuation.py` → agrega `compute_valuation_scenarios(...)`, `ScenarioValuationResult`, `ValuationScenarios`, constantes `DELTA_G`/`DELTA_WACC`, y la guarda nueva de multiplicador de Graham en `calculate_graham_fair_value`. `compute_valuation(...)` no cambia.
- `src/investbot/peers.py` → `PeerAverageResult` gana `per_minimo`/`per_maximo`; `get_peer_pe_average(...)` los deriva del mismo array de PERs ya calculado.
- `src/investbot/summary.py` → `build_valuation_section(...)` (o una función nueva que la envuelva) agrega el desglose de rango por modelo (Múltiplos/Graham/DCF) y total, la nota de exclusión por escenario (`N/D` puntual), y la regla de combinación de clasificación cara/barata por escenario (consolidada si coinciden, desglosada si no) — actualizado 2026-07-28 por las respuestas (b) y (d) de Daniela.
- `src/investbot/valuation.py` → agrega también `classify_scenario(...)` (función pura, sin I/O) como parte de este patch (respuesta (b) de Daniela).
- `tests/test_valuation.py` → agrega `test_valuation_adobe_scenarios` y tests unitarios de `compute_valuation_scenarios`/guarda nueva de Graham/min-max de peers; `test_valuation_adobe_regression` no se modifica.
- `tests/test_peers.py` (si existe, o se crea) → tests de `per_minimo`/`per_maximo`.
- `src/investbot/market_context.py` → **nuevo** — `calculate_momentum(...)`/`MomentumResult` y `compare_to_peers(...)`/`PeerComparisonResult` (sección 6, agregado 2026-07-28). Funciones puras, sin I/O, mismo estándar que `valuation.py`/`rules.py`.
- `tests/test_market_context.py` → **nuevo** — tests de `calculate_momentum`/`compare_to_peers` listados en "Criterios de aceptación", sección 6.
- `src/investbot/summary.py` → (además de lo ya listado arriba para escenarios) agrega la sección "Contexto de mercado" (momentum + comparación con peers), ubicada entre "Pilares de buena empresa" y "Encaje con tu perfil de riesgo".
- `contexto/specs/abiertas/SDD_investbot_mvp.md` → este mismo archivo (este patch).

### Restricciones / Criterios que NO cambian

- Stack, arquitectura de despliegue (long polling, sin Traefik), modelo de datos SQLite, cuestionario de perfil de riesgo, presupuesto de requests FMP (9-12/consulta, ~20-27/día) — sin cambios, incluida la sección 6 (confirmado explícitamente en 6.4: cero llamadas HTTP nuevas).
- Las 5 secciones de `security` (Iter-1) — vigentes sin cambios; este patch no agrega ninguna llamada HTTP, ningún secreto nuevo, ni un vector de input no validado nuevo (es cálculo puro sobre datos ya resueltos, mismo criterio que exoneró a Iter-2 de volver a pasar por `security`). Esto incluye la sección 6: `market_context.py` es tan "cálculo puro sobre datos ya resueltos" como `valuation.py`/`rules.py`.
- Las Decisiones de diseño #1-#10 (Iter-1) y B1-B5 (Iter-2) — sin cambios; este patch **refina** la fórmula de Graham (agrega la guarda de multiplicador, un hueco preexistente de Iter-2 que este patch expone y cierra) pero no contradice ninguna decisión previa.
- El texto/opciones/puntajes del cuestionario, la regla beta↔perfil, "ventaja competitiva" siempre cualitativa — sin cambios.
- Ningún endpoint premium/no verificado (`/dcf`, `/sector-pe-ratio`, `/treasury-rates`, y ahora `^VIX`/Fear & Greed) es dependencia dura de ningún diseño de esta spec — mismo tratamiento en Iter-1 y en la sección 6 de este patch.

---

### Preguntas abiertas para Daniela — (a)-(e) RESUELTAS 2026-07-28, (f) NUEVA y pendiente

Las 5 preguntas de la versión anterior de este patch quedaron respondidas por Daniela. Se documentan aquí como decisión cerrada, ya no bloquean scope freeze de este patch (sigue vigente que `security`/`qa` deben confirmar testabilidad antes de `implementer`, como cualquier otro criterio nuevo). La sección 6 (agregada el mismo día) trae una pregunta nueva, (f), que sí sigue abierta — depende de verificar un endpoint que no se puede confirmar sin una key real, mismo tratamiento que ya tuvo el resto de la spec con endpoints premium de FMP.

**(a) RESUELTA — Magnitudes y criterio de Múltiplos:** confirmada tal cual la propuesta del `architect`: PER mínimo/máximo de peers (no desviación estándar) para Múltiplos, `DELTA_G = 0.03` (±3pp) para Graham y para el `g_fcf` del DCF, `DELTA_WACC = 0.01` (±1pp) para el WACC del DCF. Sin cambios respecto a la sección 1 de este patch — no requirió ningún ajuste.

**(b) RESUELTA — Clasificación "barata"/"cara": se evalúa en los 3 escenarios, no solo en conservador.** Cambia la propuesta default original (que la anclaba solo al conservador). Ver sección 3 actualizada arriba ("Presentación dummy-friendly") para el diseño completo: `classify_scenario(...)` clasifica cada escenario de forma independiente, y la regla de combinación consolida la frase si los 3 coinciden o desglosa explícitamente si no — nunca se promedia ni se esconde una discrepancia.

**(c) RESUELTA — Presentación:** confirmada la propuesta default: el rango aparece siempre en cada consulta, sin comando ni palabra clave separada.

**(d) RESUELTA — Nivel de detalle: se desglosa por cada uno de los 3 modelos individuales, no solo el total.** Cambia la propuesta default original (que mostraba solo el total). Ver sección 3 actualizada arriba para el formato exacto (una línea Pesimista | Conservador | Optimista por modelo + la línea de total).

**(e) RESUELTA — Nombres de escenarios:** confirmado literal **"Pesimista / Conservador / Optimista"** en el texto hacia Daniela — no se usa la variante "peor caso/número de referencia/mejor caso".

**(f) NUEVA, pendiente — ¿Está `^VIX` (u otro índice de sentimiento de mercado tipo Fear & Greed) disponible en el plan gratuito de FMP?** El `architect` no puede confirmarlo sin una key real (ya hubo dos sorpresas en este proyecto con endpoints que la documentación pública sugería gratis y no lo eran: `/api/v3` legacy, `/key-metrics-ttm`). Se propone como **verificación durante la implementación** (mismo patrón que Iter-1 ya usó para `/dcf`/`/sector-pe-ratio`/`/treasury-rates`, Decisión #8): si `implementer` confirma que algún endpoint de este tipo responde gratis con la key real de Daniela, puede agregarse como una línea adicional opcional en "Contexto de mercado" (ej. `"VIX actual: 14.2 (miedo bajo)"`), documentado como atajo opcional — **nunca como requisito** para que la sección funcione, porque el diseño default (6.1-6.4) ya cubre "cómo está el mercado viendo esta acción" sin depender de ningún índice externo. Si no está disponible gratis, el diseño ya funciona sin cambios. No bloquea scope freeze de este patch (el resto de la sección 6 es independiente de esta respuesta) — sí queda como pendiente explícito para `implementer`/`qa`, no se decide en soledad.

---

## Handoff → security

### Specs producidas
- Este Spec Patch [Iter-3], agregado al final de `SDD_investbot_mvp.md` (no reemplaza ninguna sección anterior).

### Criterios de aceptación base
Ver "Criterios de aceptación (nuevos, Iter-3)" arriba — se suman a todos los de Iter-1 + Spec Patch Iter-2, que siguen vigentes sin cambios.

### Decisiones de diseño tomadas (no reabrir)
1. Múltiplos: pesimista/conservador/optimista = PER mínimo/promedio/máximo de los peers válidos del set fijo de sector (no desviación estándar). Confirmado por Daniela (pregunta (a)), sin ajustes.
2. Graham: pesimista/conservador/optimista = CAGR histórico ∓/=/± `DELTA_G` (3pp, desplazamiento absoluto, no multiplicativo), con guarda nueva de multiplicador `(8.5+2g_pct)>0` aplicada a los 3 escenarios. Confirmado por Daniela (pregunta (a)), sin ajustes.
3. DCF: pesimista/conservador/optimista = WACC ±/=/∓ `DELTA_WACC` (1pp) y `g_fcf` con el mismo `DELTA_G` de Graham; tasa de crecimiento terminal fija, no varía por escenario. Confirmado por Daniela (pregunta (a)), sin ajustes.
4. Exclusión en 2 niveles: nivel 1 (dato base inválido, igual en los 3 escenarios, se reporta una vez) vs. nivel 2 (desplazamiento de escenario saca un valor intermedio de rango válido, puede diferir por escenario).
5. `compute_valuation(...)` no cambia; escenario conservador es, literalmente, ese mismo cálculo — `test_valuation_adobe_regression` no se modifica.
6. Sin llamadas HTTP nuevas — confirmado explícitamente, es transformación matemática sobre datos ya obtenidos en la misma consulta.
7. **(2026-07-28, respuesta (b) de Daniela — reemplaza la propuesta default original)** Clasificación "barata"/"cara" evaluada de forma independiente en los 3 escenarios vía `classify_scenario(...)` (función pura, `valuation.py`); `summary.py` consolida en una frase si los 3 escenarios coinciden, o desglosa explícitamente si no coinciden o si algún escenario da `None` (0 de 3 modelos calculables en ese escenario puntual) — nunca promedia ni elige un único veredicto que oculte una discrepancia real.
8. **(2026-07-28, respuesta (d) de Daniela — reemplaza la propuesta default original)** `summary.py` muestra el rango Pesimista | Conservador | Optimista desglosado por cada uno de los 3 modelos individuales (Múltiplos, Graham, DCF), además de la línea de total — no solo el total. Un modelo excluido solo en un escenario puntual (nivel 2) se muestra como `N/D` en esa celda específica, no se omite la fila completa.
9. **(2026-07-28, sección 6 — feature nueva, pedido textual de Daniela)** Se agrega "Contexto de mercado": momentum de precio (`calculate_momentum`, basado en `priceAvg50`/`priceAvg200`/`yearHigh`/`yearLow`, ya gratis en `/quote`) + comparación explícita con peers (`compare_to_peers`, reutiliza `per_minimo`/`per_promedio`/`per_maximo` de `peers.py` ya agregados en el punto 1 de este patch). Vive en un módulo nuevo, `market_context.py`, funciones puras sin I/O. Un índice tipo VIX/Fear & Greed **no es dependencia dura** — queda como verificación opcional durante la implementación (pregunta (f), pendiente), sin bloquear el resto del patch.

### Foco esperado para `security`
- Confirmar (como ya hizo en Iter-2 con B1-B5) que las nuevas guardas/constantes/funciones (`DELTA_G`, `DELTA_WACC`, guarda de multiplicador de Graham, `classify_scenario(...)`, la lógica de combinación de clasificación en `summary.py`, y ahora también `calculate_momentum(...)`/`compare_to_peers(...)` de `market_context.py`) son funciones puras sin I/O, sin secreto nuevo, sin vector de input no validado — mismo análisis que ya cerró Iter-2 sin hallazgos bloqueantes, aplicado ahora a este patch completo (incluida la sección 6).
- Confirmar que no hay ninguna superficie nueva de logging (los nuevos campos `per_minimo`/`per_maximo`/`modelos_excluidos_base`, las clasificaciones booleanas por escenario, y los campos de `/quote` reutilizados para momentum — `priceAvg50`, `priceAvg200`, `yearHigh`, `yearLow` — no exponen nada distinto a lo que ya cubren los criterios de la sección 2 de `security`, Iter-1 — no incluyen URLs, keys ni datos sensibles).
- **Foco específico nuevo de la sección 6:** si `implementer` verifica durante la implementación que un endpoint tipo `^VIX` está disponible gratis (pregunta abierta (f)) y lo agrega como atajo opcional, `security` debe revisar ese caso puntual cuando ocurra (nueva URL/parámetros de un proveedor no contemplado hasta ahora) — pero **no bloquea este handoff**: mientras esa verificación no ocurra, no hay ninguna superficie nueva que revisar más allá de lo ya listado (cálculo puro sobre `/quote` y `/key-metrics`, ambos ya auditados en Iter-1).
- Si `security` confirma que no hay hallazgos (escenario esperado, dado que es cálculo puro), el patch pasa directo a `qa` (que confirma testabilidad de los criterios nuevos, mismo patrón que su confirmación corta de Iter-2) y luego a `implementer` — sin reabrir `frontend` (sigue sin haber UI web).

### Preguntas de negocio — (a)-(e) ya resueltas por Daniela, (f) pendiente y explícitamente fuera del alcance de `security`
Las (a)-(e) de la sección "Preguntas abiertas para Daniela" ya quedaron resueltas por escrito (2026-07-28, ver esa sección arriba) — eran de negocio/producto (magnitudes de escenario, presentación al usuario), no de seguridad. La (f) (disponibilidad gratuita de un índice tipo VIX) tampoco es una pregunta de seguridad — es una verificación de plan/presupuesto que le corresponde a `implementer` confirmar con una key real, no a `security` decidir. `security` no necesita resolver ninguna de las 6; solo evalúa la superficie técnica de las decisiones ya tomadas, listada arriba en "Foco esperado".

---

## Criterios de seguridad — agregado por `security` [Iter-3, 2026-07-28]

**Rol:** `security`. Esta sección revisa **solo** el Spec Patch [Iter-3] (escenarios de valoración, secciones 1-5, y "Contexto de mercado", sección 6). Iter-1 y Iter-2 quedan congelados y no se reabren — se referencian únicamente como base de comparación. Verificación hecha contra el **código real del repo** (no solo la prosa del patch): se leyeron `src/investbot/valuation.py`, `src/investbot/peers.py`, `src/investbot/rules.py`, `src/investbot/fmp_client.py` y `src/investbot/query_handler.py` tal como están hoy en `/Users/danielavergara/Documents/Personal/InvestBot/src/investbot/`.

**Nivel de verificación:** sin cambios respecto a Iter-1 — ASVS L1 general, mismo rigor de secretos/logging que FoodMindAI. Este patch no introduce ningún dato de mayor sensibilidad (sigue siendo aritmética sobre cotizaciones públicas).

---

### 1. ¿Son realmente funciones puras, sin I/O? — verificado contra el código, no asumido

Las funciones nuevas de Iter-3 (`compute_valuation_scenarios`, `classify_scenario`, `calculate_momentum`, `compare_to_peers`) todavía no existen en el repo — las escribe `implementer`. No se puede "leer" su pureza directamente. Lo que sí se verificó es el **precedente que deben seguir**, para confirmar que el patrón es real y no solo aspiracional:

- `valuation.py` (Iter-1/Iter-2, código actual) — `calculate_cagr`, `calculate_multiplos_fair_value`, `calculate_graham_fair_value`, `calculate_wacc`, `calculate_dcf_fair_value` y el orquestador `compute_valuation` (líneas 45-332) son, en efecto, funciones que solo reciben `float`/`Optional[float]`/`list[float]`/`dict` ya resueltos y devuelven datos — cero `import httpx`, cero `await`, cero acceso a red o a `db.py` dentro del módulo. El único I/O del proyecto vive en `fmp_client.py`/`treasury_client.py`, inyectado como callable (`get_peer_metrics_fn` en `peers.py:44-50`), nunca instanciado dentro de la función de cálculo.
- `rules.py` (`calculate_eps`, `calculate_liquidity_ratio`, `calculate_per`, `calculate_ps`, `evaluate_pillars`) sigue el mismo patrón: solo aritmética y comparaciones sobre parámetros.
- `compute_valuation_scenarios(...)` (diseño, sección 2 del patch) está especificado para **reutilizar** `calculate_multiplos_fair_value`/`calculate_graham_fair_value`/`calculate_wacc`/`calculate_dcf_fair_value` ya existentes, con parámetros desplazados (`per_promedio_peers`→min/prom/max, `g`→g∓Δ, `wacc`→wacc±Δ) — es composición de las mismas funciones puras ya auditadas, no un camino nuevo de I/O. `classify_scenario(...)` (sección 3 del patch) es una comparación de dos `float`/`None`, sin estado ni dependencia externa — el pseudocódigo mostrado en el patch (`if valor_justo_total is None: return None; return precio_actual < valor_justo_total`) no deja margen para I/O accidental.
- `calculate_momentum(...)`/`compare_to_peers(...)` (`market_context.py`, sección 6) están especificadas con firma `def calculate_momentum(*, price, year_high, price_avg_50, ...) -> MomentumResult` y `def compare_to_peers(*, per_propio, per_minimo_peers, ...) -> PeerComparisonResult` — mismo estilo `kwargs`-only que ya usa `calculate_wacc` (`valuation.py:111-121`) y `evaluate_pillars` (`rules.py:100-106`), consistente con el patrón de pureza ya establecido en el proyecto.

**Criterio de aceptación nuevo (verificación post-implementación, no bloqueante para el diseño):**
- [ ] `compute_valuation_scenarios`, `classify_scenario`, `calculate_momentum`, `compare_to_peers` no importan `httpx`, `sqlite3`, ni ningún módulo de `investbot` que haga I/O (`fmp_client`, `treasury_client`, `db`) — verificable por `grep -n "^import\|^from" src/investbot/market_context.py src/investbot/valuation.py` en la revisión de `qa`/code review del PR de `implementer`, no solo por lectura de la spec.
- [ ] `market_context.py` no recibe el cliente HTTP ni la API key como parámetro en ninguna de sus dos funciones — si `implementer` necesitara pasar el cliente HTTP a `calculate_momentum`/`compare_to_peers` "por si acaso", eso sería una señal de que se coló I/O donde no debería y `qa` debe rechazarlo en revisión.

---

### 2. Vector de input no validado nuevo — confirmado que no hay

Analizado explícitamente, no dado por sentado:

- **Escenarios de valoración (secciones 1-5):** los inputs de `compute_valuation_scenarios` son los mismos que ya recibe `compute_valuation` hoy (`eps_ttm`, `eps_historial`, `per_promedio_peers`, `fcf_historial`, `y`, `wacc_inputs`, `shares_outstanding`) — todos numéricos, ya extraídos y validados en `fmp_client.py`/`query_handler.py` antes de llegar a `valuation.py`. `DELTA_G`/`DELTA_WACC` son constantes de código (`0.03`/`0.01`), no vienen de ningún input de Daniela ni de FMP — no hay superficie de input externo nueva aquí, es una constante estática igual que `MARKET_RISK_PREMIUM`/`TERMINAL_GROWTH_RATE` ya existentes (`valuation.py:38,40`).
- **`peers.py::PeerAverageResult` con `per_minimo`/`per_maximo`:** se confirma en el código actual (`peers.py:68-80`) que `get_peer_pe_average` ya arma la lista completa `pes: list[float]` antes de promediar — agregar `min(pes)`/`max(pes)` es leer el mismo array ya en memoria, cero parsing nuevo de respuesta HTTP, cero endpoint nuevo. No hay vector nuevo.
- **Contexto de mercado (sección 6):** `calculate_momentum` consume `priceAvg50`, `priceAvg200`, `yearHigh`, `yearLow` — campos del mismo objeto `/quote` que `fmp_client.py::get_quote` (línea 96-102) ya trae hoy para el ticker propio, con la misma llamada `params=` que evita inyección en la URL (criterio de `security` Iter-1, sección 4, ya vigente y sin cambios). `compare_to_peers` consume `per_propio`/`per_minimo_peers`/`per_maximo_peers`/`peers_usados` — todos derivados, no texto libre de Daniela ni respuesta cruda de un proveedor nuevo. Ninguna de las dos funciones recibe directamente el texto que Daniela escribió en el chat (eso ya se resuelve y sanitiza antes, en `query_handler.py`, criterio de Iter-1 sección 4, sin cambios).
- **Confirmado explícitamente:** ninguna de las funciones nuevas de Iter-3 introduce un punto de entrada de datos que no haya pasado ya por la validación/tipado de Iter-1 (`fmp_client.py` parsea JSON de FMP con claves conocidas; nunca hay `eval`/deserialización insegura en el proyecto). No hay hallazgo aquí.

---

### 3. ¿El logging de estos módulos podría filtrar algo nuevo?

Revisado contra el código de logging real, no solo contra la ausencia de secretos "obvia":

- `fmp_client.py` (líneas 26-79) ya loguea únicamente `endpoint_label`/`status` en `logger.warning(...)` — nunca la URL completa ni el diccionario `params` (que es donde vive `apikey`). Esto es el patrón que exigió `security` en Iter-1 sección 2-3 y sigue vigente; Iter-3 no toca `fmp_client.py` en absoluto (ni se agrega un endpoint nuevo, ver punto 4 abajo), así que no hay regresión posible aquí.
- `query_handler.py` ya usa `sanitize_for_log(...)` (línea 248) antes de loguear el texto libre de Daniela — criterio de Iter-1 sección 4, vigente sin cambios. Iter-3 no agrega ningún nuevo punto donde texto de usuario llegue a un logger.
- `peers.py` (código actual) **no tiene ningún `logger.*` hoy** — agregar `per_minimo`/`per_maximo` a `PeerAverageResult` no crea una superficie de logging nueva porque no hay logging en ese módulo para empezar. Si `implementer` decide loguear el resultado completo de `PeerAverageResult` en algún punto de `query_handler.py` (no está en la spec, pero es plausible como debug), esos campos son floats de PER — no secretos, no URLs, no datos personales de Daniela. Sin riesgo de severidad comparable a la sección 2/3 de Iter-1 (que trataba específicamente de `apikey`/tokens).
- `market_context.py` (módulo nuevo, no escrito todavía): dado que no recibe cliente HTTP ni API key (punto 1 arriba), no hay forma de que un log de este módulo contenga una URL con query string ni un token — a diferencia del hallazgo de mayor severidad de Iter-1 (sección 2, logging DEBUG de `httpx`/`telegram` filtrando el token en la URL), que era posible precisamente porque esos módulos sí hacen la llamada HTTP. `market_context.py`, al ser cálculo puro downstream de datos ya obtenidos, estructuralmente no puede reproducir ese hallazgo.
- **Conclusión:** ningún campo nuevo de Iter-3 (`per_minimo`, `per_maximo`, `modelos_excluidos_base`, las clasificaciones booleanas por escenario, `pct_vs_year_high`/`pct_vs_avg_50`/etc., `posicion` de `PeerComparisonResult`) es de mayor sensibilidad que un ratio financiero ya expuesto por Iter-1/Iter-2 (`ratio_liquidez`, `per`, `valor_justo_total`) — todos son números derivados de cotizaciones públicas, no secretos ni PII.

**Criterio de aceptación nuevo:**
- [ ] Si `implementer` agrega logging de depuración sobre `ValuationScenarios`/`PeerAverageResult`/`MomentumResult`/`PeerComparisonResult` en algún punto (no exigido por la spec, pero plausible), ese log nunca debe incluir el objeto `wacc_inputs` completo sin filtrar si en el futuro ese diccionario llegara a incorporar algo más que datos financieros públicos — hoy no lo hace (son floats de balance/income statement), se deja como nota preventiva, no como hallazgo activo.

---

### 4. Condición sobre un futuro endpoint tipo `^VIX`/Fear & Greed (pregunta abierta (f), no bloqueante hoy)

La spec ya documenta correctamente que esto es una verificación de `implementer`, no una decisión de `security`. Se agrega aquí el criterio condicional que pide el patch: **si** `implementer` confirma que un endpoint de este tipo responde gratis y lo integra, esa llamada nueva debe cumplir, sin excepción, los mismos criterios ya establecidos en `security` Iter-1 secciones 2-4 antes de mezclarse con el resto del bot:

- [ ] **Secretos (Iter-1 sección 2):** si el endpoint requiere su propia API key o reutiliza `FMP_API_KEY`, esa key se lee de variable de entorno, nunca hardcodeada, y se pasa vía `params=` de `httpx` (nunca f-string/concatenación) — mismo patrón que `search_company`/`get_quote`/`get_profile` ya usan en `fmp_client.py`.
- [ ] **Logging (Iter-1 sección 2-3):** el wrapper de esa llamada nueva loguea únicamente `endpoint_label`/`status`, nunca la URL completa ni el diccionario de `params` — mismo patrón que las funciones existentes de `fmp_client.py` (líneas 54, 60, 73, 79). Si se agrega a un módulo nuevo en vez de a `fmp_client.py`, ese módulo nuevo hereda el mismo estándar, no un logging más permisivo "porque es un módulo distinto".
- [ ] **Manejo de errores (Iter-1 sección 3):** excepción propia que no propaga la URL/params originales hacia el mensaje a Telegram ni hacia ningún `logger.*`, igual que `FMPError`/`TreasuryError` ya exigen.
- [ ] **Presupuesto/disponibilidad (no es criterio de seguridad, se marca aquí solo para que `qa`/`implementer` no lo pierdan de vista):** si el endpoint no es gratuito o consume el cupo de 250 req/día de forma no presupuestada, eso es un problema de diseño (`architect`), no de seguridad — pero si `security` detecta en la revisión del PR que la key usada es de un tier pago sin que Daniela lo haya aprobado, debe señalarlo igual que ya se hizo con `/key-metrics-ttm` en esta misma sesión (hallazgo operativo, no de este documento).

Este criterio queda **condicional, no bloqueante**: mientras esa verificación no ocurra, no hay ninguna superficie nueva que auditar más allá de lo ya cubierto en los puntos 1-3 de esta sección.

---

### Veredicto de `security`

**Ningún hallazgo bloqueante.** Iter-3 completo (escenarios de valoración + contexto de mercado) es, tal como está diseñado y consistente con el código real ya auditado del proyecto, una capa de cálculo puro sobre datos que Iter-1/Iter-2 ya validaron y sanitizaron:

- Las funciones nuevas siguen el mismo patrón de pureza (sin I/O, `kwargs`-only, tipos numéricos) que `calculate_cagr`/`calculate_wacc`/`evaluate_pillars` ya implementan hoy en el repo — verificado leyendo el código, no asumido.
- No hay vector de input no validado nuevo: todos los datos de entrada de las 4 funciones nuevas ya pasaron por la validación/parsing de Iter-1 antes de llegar aquí; las 2 constantes nuevas (`DELTA_G`, `DELTA_WACC`) son literales de código, no input externo.
- No hay superficie de logging nueva de mayor severidad que la ya cubierta en Iter-1 secciones 2-3: `peers.py` no loguea hoy, `market_context.py` no tocará ni el cliente HTTP ni las API keys por diseño, y los campos nuevos son floats derivados de cotizaciones públicas, no secretos.
- La única pieza condicional (un futuro endpoint tipo `^VIX`, pregunta (f)) queda con un criterio explícito de qué debe cumplir si se agrega, sin bloquear el patch actual.

**Este patch puede pasar directo a `qa` sin volver a `architect`.** No se identificó ningún hallazgo que requiera un spec patch adicional ni que reabra las decisiones de diseño de las secciones 1-6 del Iter-3, ni las de Iter-1/Iter-2.

---

## Handoff → qa

### Specs producidas
- Este Spec Patch [Iter-3] completo (secciones 1-6, criterios de aceptación, y esta sección de `security`), agregado al final de `SDD_investbot_mvp.md`.

### Criterios de aceptación (base + Iter-3 + seguridad Iter-3)
Ver "Criterios de aceptación (nuevos, Iter-3)" + los criterios de esta sección de `security` (puntos 1-4 arriba) — se suman a todo lo vigente de Iter-1 + Iter-2, sin reemplazarlo.

### Sin bloqueantes
`security` no encontró ningún hallazgo que requiera volver a `architect`. Todas las funciones nuevas (diseñadas, no implementadas todavía) siguen el patrón de pureza ya verificado en el código existente del proyecto; no hay vector de input nuevo ni superficie de logging de mayor severidad que la ya auditada en Iter-1.

### Foco esperado para `qa`
- Confirmar testabilidad de los ~25 criterios de aceptación nuevos de Iter-3 (sección "Criterios de aceptación (nuevos, Iter-3)") — en particular los tests de exclusión de nivel 2 (`test_valuation_adobe_scenarios`) y los 9 tests de `classify_scenario`/combinación de clasificación, que dependen de fixtures numéricos concretos, no de mocks de red.
- Confirmar el criterio nuevo de `security` punto 1 ("no importan `httpx`/`sqlite3`/módulos de I/O") como parte de la revisión de estructura de código que `qa` ya exige en Iter-1 sección 6 ("no hay lógica de negocio escondida") — mismo tipo de chequeo, ahora aplicado a `market_context.py` y a `compute_valuation_scenarios`.
- Confirmar que `test_valuation_adobe_scenarios` efectivamente verifica que los 3 modelos son calculables en los 3 escenarios para el fixture de Adobe sin exclusiones de nivel 2 inesperadas (criterio de aceptación ya listado por `architect`) — si `DELTA_G`/`DELTA_WACC` resultan demasiado agresivos para ese fixture, es un hallazgo de `qa`/`implementer`, no algo que `security` deba resolver.
- Si `qa` confirma testabilidad, el pipeline va directo a `implementer` (sin volver a `frontend`, sigue sin haber UI web).

---

## Criterios QA — agregado por `qa` [Iter-3, 2026-07-28]

**Rol:** `qa`. Revisión de testabilidad del Spec Patch [Iter-3] completo (secciones 1-6 + `security` Iter-3). No repite el análisis de Iter-1 ni la confirmación de Iter-2 (ambos congelados). Mismo patrón que la confirmación corta de Iter-2: no se rediseña nada, solo se confirma si los criterios ya escritos alcanzan para tests deterministas, y se documentan como bloqueante únicamente los huecos reales.

**Verificación hecha contra código y fixtures reales, no solo contra la prosa del patch** (mismo estándar que ya aplicó `security` en esta iteración): se leyó `src/investbot/valuation.py`, `src/investbot/peers.py`, `src/investbot/rules.py` tal como están hoy, y se ejecutaron a mano (con el intérprete real del proyecto, no una calculadora aparte) los datos de `tests/fixtures/adobe/` con `DELTA_G=0.03`/`DELTA_WACC=0.01` — ver punto 1 abajo. Ninguna de las funciones de escenarios (`compute_valuation_scenarios`, `classify_scenario`, `calculate_momentum`, `compare_to_peers`) existe todavía en el repo, consistente con lo que ya reportó `security`.

---

### 1. Caso Adobe: ¿`DELTA_G=0.03`/`DELTA_WACC=0.01` disparan exclusiones de nivel 2 con los números reales del fixture? — verificado, **no las disparan**

El patch (sección 4) dejó esto explícitamente sin verificar ("si algún escenario excluye un modelo inesperadamente... debe ajustarse antes de cerrar el patch, `qa` lo confirma en su revisión, no `architect` en soledad"). Se corrió el cálculo real con `tests/fixtures/adobe/*.json` y las funciones ya existentes (`calculate_cagr`, `calculate_graham_fair_value`, `calculate_wacc`, `calculate_dcf_fair_value`) aplicando manualmente los desplazamientos propuestos, para emular lo que hará `compute_valuation_scenarios`:

| Escenario | Múltiplos (min/prom/max PER peers) | Graham (`g∓0.03`) | DCF (`WACC±0.01`, `g_fcf∓0.03`) | Total |
|---|---|---|---|---|
| Pesimista | 20.00 × 30.0 = 600.00 | `g=6.64%` → 435.64 | `WACC=11.26%`, `g_fcf=5.0%` → 225.64 | **420.43** |
| Conservador | 20.00 × 32.9 = 658.00 | `g=9.64%` → 555.64 | `WACC=10.26%`, `g_fcf=8.0%` → 288.82 | **500.82** (= regresión ya validada) |
| Optimista | 20.00 × 35.7 = 714.00 | `g=12.64%` → 675.64 | `WACC=9.26%`, `g_fcf=11.0%` → 376.50 | **588.71** |

- Graham: el multiplicador `(8.5+2×g_pct)` da **21.78** en pesimista y **33.78** en optimista — lejos de cruzar 0 (haría falta un `g` conservador menor a aprox. -4.25% para que el pesimista, con `-3pp` adicionales, cruce cero). La guarda nueva de la sección 1 del patch **no se activa** con este fixture.
- DCF: `WACC` optimista (9.26%) queda muy por encima de `TERMINAL_GROWTH_RATE` (2.5%) — la guarda existente (`wacc <= terminal_growth → None`) tampoco se activa.
- Múltiplos: los 3 peers (MSFT/ORCL/CRM) tienen `earningsYield` válido en `peers_metrics.json` → nunca es el caso degenerado de <2 peers.
- **Conclusión:** con el fixture actual, los 3 modelos son calculables en los 3 escenarios, sin ninguna exclusión de nivel 2 — `test_valuation_adobe_scenarios` es escribible tal como lo especifica el patch (relación `pesimista ≤ conservador ≤ optimista` verificada arriba: 420.43 ≤ 500.82 ≤ 588.71) y **pasará** sin necesidad de ajustar `DELTA_G`/`DELTA_WACC` para este caso. Esto no es un hallazgo bloqueante — es la verificación que el patch pidió explícitamente que `qa` hiciera antes de cerrar, y queda cerrada con evidencia numérica trazable (arriba). Recomendación operativa para `implementer`: dejar esta tabla (o su equivalente) como comentario en el test nuevo, igual que ya hace `tests/fixtures/adobe/README.md` con el caso conservador — evita que alguien repita esta verificación a mano en el futuro.

---

### 2. `classify_scenario` / combinación barata-cara — cobertura de casos

Los 4 tests listados (`test_classify_scenario_barata/cara/none`, `test_combinar_clasificacion_consolidada_barata/cara`, `_desglosada`, `_con_none`) tienen assert determinista porque `classify_scenario` es una función pura de 2 argumentos con contrato explícito en pseudocódigo — no hace falta un caso de referencia de negocio para escribirlos, cualquier par `(precio, valor_justo)` inventado sirve. Sin hallazgo en eso.

**Hueco real — interacción no definida con el caso "0 de 3 modelos" ya existente de Iter-2 (`test_valuation_0_de_3_modelos`):**

Si el escenario **conservador** ya tiene los 3 modelos excluidos a **nivel 1** (el caso que Iter-2 ya define: historial insuficiente + EPS TTM≤0 simultáneos, mensaje "no fue posible valorar la empresa"), entonces por la propia regla de la sección 2 de este patch ("un modelo excluido a nivel 1 lo está en los 3 escenarios por igual"), **los 3 escenarios** tienen `valor_justo_total=None` simultáneamente — no es el mismo caso que `test_combinar_clasificacion_con_none` (que cubre **un** escenario en `None`, no los 3).

La spec no dice qué hace `summary.py` en ese caso: ¿omite por completo la sección de clasificación barata/cara (porque ya se mostró el mensaje de Iter-2 de "no fue posible valorar")? ¿O aplica igual la regla de combinación de la sección 3 de este patch y agrega un desglose de 3 líneas "no se pudo determinar en este escenario" — que sería redundante con el mensaje que Iter-2 ya muestra para el mismo caso? Sin definir esto, no hay un assert determinista posible para `test_valuation_0_de_3_modelos` en su forma extendida a Iter-3 (el test de Iter-2 ya existente solo verifica `valor_justo_total=None`, no toca clasificación, así que no cubre este caso nuevo por accidente).

Recomendación de `qa` (no vinculante, consistente con el principio de "nunca mostrar ruido redundante" ya usado en la sección 3 para el caso de 3 escenarios coincidentes): cuando el escenario conservador ya tiene `valor_justo_total=None` por exclusión de nivel 1 de los 3 modelos, `summary.py` omite entera la sección de clasificación barata/cara — el mensaje de Iter-2 ("no fue posible valorar la empresa...") ya comunica que no hay nada que clasificar, y desglosar 3 veces "no se pudo determinar" no aporta información nueva.

---

### 3. `market_context.py` — datos faltantes o insuficientes: 2 huecos reales

**3a. `calculate_momentum` — caso de un solo promedio móvil ausente (no los dos).**

La sección 6.1 solo define `etiqueta="no_disponible"` para el caso "faltan los **dos** promedios móviles" (`price_avg_50` **y** `price_avg_200`). No define qué pasa si falta **solo uno** de los dos — caso real, no hipotético: una empresa con 60-90 días de historial de cotización (ej. IPO reciente) puede tener `priceAvg50` disponible en FMP pero `priceAvg200` todavía en `null` porque no acumuló 200 días de precios. Los 4 tests listados (`test_momentum_impulso_positivo/_negativo/_mixto/_no_disponible`) no incluyen este quinto caso. Sin definirlo, `calculate_momentum(price=X, price_avg_50=Y, price_avg_200=None, ...)` no tiene un assert determinista: ¿`etiqueta="no_disponible"` también en este caso (extendiendo la guarda a "falta cualquiera de los dos", no solo "faltan los dos"), o se intenta una etiqueta parcial (ej. "mixto" o "impulso_positivo" comparando solo contra el promedio disponible)?

Recomendación de `qa` (no vinculante, mismo principio de "nunca inventar en silencio" que ya rige toda la spec): `etiqueta="no_disponible"` si falta **cualquiera** de los dos promedios (no solo si faltan los dos) — las 3 etiquetas cualitativas (positivo/negativo/mixto) están definidas explícitamente sobre la comparación **simultánea** contra ambos promedios; calcular una etiqueta con un solo dato disponible sería una fracción de la definición original, no una aproximación razonable de ella.

**3b. `compare_to_peers` — caso degenerado de exactamente 1 peer válido, no cubierto (mismo tipo de hueco que la sección 1 ya cerró para Múltiplos).**

La sección 6.2 define `posicion="no_comparable"` solo para `per_propio is None` o "0 peers con PER válido". No replica el caso degenerado que la **propia sección 1 de este mismo patch** ya identificó y resolvió explícitamente para el modelo de Múltiplos: "si solo hay 1 peer válido... mínimo=promedio=máximo, no hay rango real — se documenta explícitamente en vez de mostrar 3 números iguales sin explicación". Con exactamente 1 peer válido, `per_minimo_peers == per_promedio_peers == per_maximo_peers` (mismo valor) y la función `compare_to_peers`, tal como está especificada, produciría mecánicamente `"en_linea"` (si `per_propio` coincide con ese único valor) o `"mas_barata"`/`"mas_cara"` — pero calificar eso de **"en línea con tus comparables"** (plural, sugiere un rango real) cuando en realidad hay un solo comparable es información engañosa para Daniela, y contradice el estándar de transparencia que la sección 1 de este mismo patch ya aplicó al mismo tipo de dato.

Los 5 tests listados (`_mas_barata/_en_linea/_mas_cara/_no_comparable_eps_negativo/_no_comparable_sin_peers_validos`) no incluyen un caso "1 peer válido" — falta definir si ese caso es un sexto valor de `posicion` (ej. `"no_comparable"` también aquí, con un motivo distinto, o una posición nueva tipo `"comparacion_limitada"`), o si se acepta mostrar `"en_linea"`/`"mas_barata"`/`"mas_cara"` con un solo dato sin aclaración. Sin definirlo, no hay un assert determinista para ese input específico.

Recomendación de `qa` (no vinculante, consistencia directa con la sección 1 del mismo patch): tratar `<2` peers válidos igual en `compare_to_peers` que en Múltiplos — `posicion="no_comparable"` con un motivo adicional (ej. `"un_solo_peer_valido"`) en vez de una posición que implica un rango que no existe.

---

### 4. Estructura de datos y firmas — ¿alcanza para un assert determinista?

- `classify_scenario`, `calculate_momentum`, `compare_to_peers`: firma completa (parámetros `kwargs`-only, tipos, retorno) especificada en el patch — sin ambigüedad, testeables tal cual (salvo los huecos de comportamiento ya señalados en 2 y 3, que son de **contrato de casos límite**, no de firma).
- `compute_valuation_scenarios(...)`: la firma exacta queda implícita, no escrita como código — la sección 2 dice que "recibe los mismos datos ya resueltos que hoy recibe `compute_valuation`" y que "`per_promedio_peers` → min/prom/max", pero `compute_valuation` hoy solo recibe un único `per_promedio_peers: Optional[float]` (ver `valuation.py:249`), no un trío min/prom/max. La función nueva necesita recibir de alguna forma los 3 valores (¿tres parámetros sueltos `per_minimo_peers`/`per_promedio_peers`/`per_maximo_peers`, o el `PeerAverageResult` completo?) — el patch no lo fija explícitamente como sí hizo Iter-2 con la estructura de `ValuationResult`. **No es bloqueante**: no cambia ningún resultado esperado ni impide escribir el test (el test llama a la función con los datos de peers como sea que `implementer` la nombre, y verifica la tabla de la sección 1 de este mismo documento) — se deja como nota de implementación, no como criterio que requiera volver a `architect`.

---

### 5. Cobertura — bucket de `market_context.py` (nuevo) y de `peers.py`

La spec de Iter-3 no dice en qué bucket de cobertura (95% "corazón matemático" vs 70% "resto") cae `market_context.py`. Decisión de `qa` (dentro de su rol, no requiere `architect`):

- **`market_context.py` → bucket de 70% ("resto"), no el de 95%.** Aunque el patch describe `market_context.py` como "mismo estándar de pureza que `valuation.py`/`rules.py`", el criterio de 95% en Iter-1 se reservó específicamente para el **motor de Valor Justo** (los 3 modelos + el puntaje de perfil de riesgo) — el ejemplo ya establecido es que `rules.py` (igual de puro, igual de crítico para los pilares) **ya está en el bucket de 70%**, no en el de 95%. `market_context.py` (momentum + comparación con peers) es información complementaria/contextual, no el cálculo del Valor Justo en sí — mismo nivel de riesgo de negocio que `rules.py`, no el de `valuation.py`. Se mantiene el criterio ya fijado en Iter-1 sin necesidad de ampliarlo.
- **`peers.py` → recomendación de moverlo explícitamente al bucket de 95%** (hueco preexistente de Iter-1: la lista de "resto" del Iter-1 nunca nombró a `peers.py`, y tampoco quedó en la lista de 95%). Esto ya era una omisión antes de Iter-3, pero se vuelve más relevante ahora: con `per_minimo`/`per_maximo` nuevos, `peers.py` deja de alimentar un solo input (`per_promedio`) a un solo modelo, y pasa a determinar directamente 2 de los 3 valores del escenario de Múltiplos (pesimista y optimista) — es tan "corazón matemático" del Valor Justo como `valuation.py` mismo. Se agrega como criterio de esta sección (no requiere `architect`, es ajuste de umbral de cobertura, potestad de `qa`).
- **Sin cambios** en el umbral total (≥75% líneas del proyecto) ni en la lista ya fijada de módulos del bucket de 70% (Iter-1).

**Criterio de aceptación nuevo (cobertura):**
- [ ] `src/investbot/market_context.py`: ≥ 70% líneas (bucket "resto", mismo criterio que `rules.py`).
- [ ] `src/investbot/peers.py`: ≥ 95% líneas / 100% de las ramas del caso degenerado (<2 peers válidos) — se suma explícitamente al bucket de 95% junto con `valuation.py` y el cálculo de puntaje de riesgo.

---

### Bloqueantes para `architect` — antes de scope freeze de Iter-3

Mismo criterio que Iter-2 (B1-B5): estos son huecos de **comportamiento no definido** para casos límite reales, no de seguridad ni de stack. Se numeran **C1-C3** para no confundirlos con los B1-B5 ya cerrados de Iter-2.

**C1 — Interacción entre "0 de 3 modelos" (Iter-2, exclusión total en el escenario conservador) y la clasificación barata/cara por escenario (Iter-3).**
Cuando el conservador ya tiene los 3 modelos excluidos a nivel 1 (mismo caso que `test_valuation_0_de_3_modelos` de Iter-2), la regla de la sección 2 de este patch implica que **los 3 escenarios** quedan con `valor_justo_total=None` simultáneamente — un caso distinto del ya cubierto `test_combinar_clasificacion_con_none` (un solo escenario en `None`). `architect` debe definir: ¿se omite la sección de clasificación barata/cara por completo en ese caso (recomendación de `qa`, ver punto 2 arriba), o se muestra igual un desglose de 3 líneas "no se pudo determinar"?

**C2 — `calculate_momentum` con exactamente un promedio móvil ausente (no los dos).**
La guarda `etiqueta="no_disponible"` está definida solo para "faltan los dos" (`priceAvg50` y `priceAvg200`). Falta definir el caso real de un solo dato ausente (empresa con 50-199 días de historial de cotización). Recomendación de `qa` (ver punto 3a arriba): extender la guarda a "falta cualquiera de los dos".

**C3 — `compare_to_peers` con exactamente 1 peer válido (caso degenerado no replicado del ya resuelto en la sección 1 para Múltiplos).**
Falta un sexto valor/motivo de `posicion` (o una aclaración explícita) para el caso `per_minimo_peers == per_promedio_peers == per_maximo_peers` con un solo comparable — mostrar `"en_linea"`/`"mas_barata"`/`"mas_cara"` sin aclaración contradice el estándar de transparencia que la sección 1 de este mismo patch ya fijó para el mismo tipo de dato. Recomendación de `qa` (ver punto 3b arriba): tratarlo como `"no_comparable"` con motivo adicional.

**Impacto en el pipeline:** siguiendo el mismo criterio que Iter-2, esto es un bloqueante acotado — vuelve a `architect` como un spec patch corto limitado a C1-C3 (no reabre las secciones 1-5 de este mismo patch, que quedan confirmadas sin ambigüedad — ver puntos 1 y 2 arriba, con evidencia numérica para el punto 1). No requiere volver a pasar por `security` (C1-C3 son puramente de lógica de presentación/clasificación sobre datos ya validados, mismo tipo de cambio que B1-B5 de Iter-2, que tampoco requirió volver a `security`).

---

### Veredicto de `qa`

**La spec NO queda lista para scope freeze todavía — pero el alcance pendiente es pequeño (C1-C3), no una reapertura del patch completo.**

Lo que **sí** queda confirmado, sin necesidad de más trabajo de `architect`:
- El caso de regresión Adobe (`test_valuation_adobe_regression`) no cambia — confirmado, sin tocar código.
- `test_valuation_adobe_scenarios`: verificado con los datos reales del fixture que `DELTA_G=0.03`/`DELTA_WACC=0.01` **no** disparan ninguna exclusión de nivel 2 para Adobe — el test es escribible y pasará tal como lo especifica el patch (ver punto 1, tabla con los 9 valores + evidencia de por qué las guardas no se activan).
- `classify_scenario` y los 6 tests de clasificación/combinación que **sí** tienen contrato completo (barata/cara/none, consolidada/desglosada, un escenario en `None`) están listos para implementarse tal cual.
- Estructura de datos (`ValuationScenarios`, `ScenarioValuationResult`, `MomentumResult`, `PeerComparisonResult`) es suficiente en todos los casos salvo la firma exacta de `compute_valuation_scenarios` (no bloqueante, nota de implementación).
- Cobertura: `market_context.py` → 70%, `peers.py` → 95% (decisión de `qa`, sin volver a `architect`).

Lo que **falta** antes de scope freeze: **C1, C2, C3** — 3 casos límite reales (interacción de clasificación con "0 de 3 modelos", un solo promedio móvil ausente en momentum, 1 peer válido en comparación) sin comportamiento definido, cada uno con una recomendación no vinculante de `qa` ya propuesta para que `architect` solo tenga que confirmar o ajustar por escrito (mismo patrón que B5 en Iter-2).

**Siguiente paso:** `architect` emite un spec patch corto acotado a C1-C3 (mismo patrón que el patch B1-B5 de Iter-2). No necesita volver a pasar por `security` (cambios de lógica de presentación pura, sin I/O, sin secretos, sin input externo nuevo). Con el patch resuelto, `qa` confirma en una pasada corta (mismo patrón que su confirmación de Iter-2) y el pipeline va directo a `implementer`.

**Todo lo demás de este Spec Patch [Iter-3] (secciones 1-6, criterios de `security` Iter-3, y los puntos 1-2-4-5 de esta sección de `qa`) queda congelado y no se reabre** — el patch de `architect` debe limitar su alcance exactamente a C1-C3, igual que Iter-2 se limitó a B1-B5.

---

## Handoff → architect (Iter-4, acotado a C1-C3)

### Specs producidas
- Esta sección de `qa` sobre el Spec Patch [Iter-3], agregada al final de `SDD_investbot_mvp.md`.

### Criterio que falló
`qa` no pudo convertir en tests deterministas 3 casos límite (C1-C3, ver sección "Bloqueantes para `architect`" arriba) porque el Spec Patch Iter-3 no define un resultado esperado para ellos. El resto del patch (secciones 1-6 de `architect`, `security` Iter-3, y los puntos 1/2/4/5 de esta sección de `qa`) queda confirmado y congelado.

### Foco esperado para `architect`
- Definir C1: comportamiento de la sección de clasificación barata/cara cuando el escenario conservador ya tiene `valor_justo_total=None` por exclusión total de nivel 1 (interacción con el caso "0 de 3 modelos" de Iter-2).
- Definir C2: etiqueta de `calculate_momentum` cuando falta exactamente uno de los dos promedios móviles (no los dos).
- Definir C3: valor/motivo de `posicion` en `compare_to_peers` cuando hay exactamente 1 peer válido (caso degenerado análogo al ya resuelto para Múltiplos en la sección 1).
- Las 3 recomendaciones no vinculantes de `qa` (puntos 2, 3a, 3b arriba) están disponibles para adoptar tal cual, ajustar, o reemplazar — `architect` decide, `qa` no fuerza la decisión.

### Si `architect` resuelve C1-C3
El pipeline no necesita volver a `security` (mismo criterio que Iter-2: son cambios de lógica de presentación/clasificación pura, sin I/O nuevo, sin secretos nuevos, sin vector de input externo nuevo). `qa` confirma en una pasada corta y el pipeline va directo a `implementer`.

---

## Spec Patch [Iter-4] para: SDD_investbot_mvp.md — C1-C3 (huecos del Spec Patch Iter-3)

**Rol:** `architect`. **Fecha:** 2026-07-28. **Alcance:** acotado exactamente a los 3 bloqueantes C1-C3 reportados por `qa` en "Bloqueantes para `architect` — antes de scope freeze de Iter-3". No reabre las secciones 1-6 del Spec Patch Iter-3, ni `security` Iter-3, ni los puntos 1/2/4/5 de `qa` Iter-3 (mismo criterio de alcance que Iter-2 aplicó a B1-B5).

### Criterio que falló

`qa` no pudo convertir en tests deterministas 3 casos límite porque el Spec Patch Iter-3 no define un resultado esperado para ellos:

- **C1** — interacción no definida entre "0 de 3 modelos" (Iter-2) y la clasificación barata/cara por escenario (Iter-3): cuando el conservador ya tiene `valor_justo_total=None` por exclusión de nivel 1 de los 3 modelos, no está dicho qué hace `summary.py` con la sección de clasificación.
- **C2** — `calculate_momentum` solo define `"no_disponible"` cuando faltan **los dos** promedios móviles; no define el caso real de que falte solo uno.
- **C3** — `compare_to_peers` no replica el caso degenerado de 1 solo peer válido que la sección 1 de Iter-3 ya resolvió para Múltiplos.

### Ajuste de diseño

#### C1 — Clasificación barata/cara cuando los 3 escenarios quedan sin `valor_justo_total`

**Confirmación de la premisa de `qa` (para que quede escrito, no solo asumido):** por construcción, **`valor_justo_total=None` en el escenario conservador implica lo mismo en pesimista y optimista**. El escenario conservador usa exactamente los mismos parámetros que ya usa `compute_valuation()` hoy (sección 5 del Iter-3: "escenario conservador es, literalmente, ese mismo cálculo") — así que "conservador con 0 de 3 modelos" es, por definición, el caso ya existente `test_valuation_0_de_3_modelos` de Iter-2, que ocurre únicamente por exclusión de **nivel 1** (dato base inválido: historial insuficiente + EPS TTM≤0 simultáneos, u otra combinación que invalide los 3 modelos en la base). La propia regla de la sección 2 de Iter-3 ("un modelo excluido a nivel 1 lo está en los 3 escenarios por igual") garantiza que ese mismo motivo de exclusión aplica sin excepción a pesimista y optimista. No existe un camino en el diseño donde el conservador esté en `None` por los 3 modelos y algún otro escenario no lo esté — la implicación es estructural, no un caso a verificar aparte.

**Decisión (adopta la recomendación de `qa` tal cual, sin ajustes):** cuando los 3 escenarios tienen `valor_justo_total=None` (equivalente extendido de `test_valuation_0_de_3_modelos`), `summary.py` **omite por completo la sección de clasificación barata/cara** — no muestra ni la frase consolidada ni el desglose de 3 líneas. El mensaje ya existente de Iter-2 ("no fue posible valorar la empresa con los datos disponibles... igual te muestro el resto del análisis abajo") ya comunica que no hay nada que clasificar; desglosar 3 veces "no se pudo determinar" sería ruido redundante, no transparencia adicional.

**Regla de combinación de la sección 3 de Iter-3 — orden de evaluación actualizado (única parte de esa sección que este patch toca):**
```
1. Si valor_justo_total es None en el escenario conservador (y, por construcción, también en
   pesimista y optimista — ver justificación arriba):
   → NO se muestra la sección de clasificación barata/cara. Ya se mostró el mensaje de
     Iter-2 de "no fue posible valorar la empresa".
2. En cualquier otro caso (conservador tiene valor_justo_total no-None; a lo sumo 1 o 2 de
   los 3 escenarios son None — nunca los 3, por la implicación de arriba):
   → se aplica la regla ya definida en la sección 3 de Iter-3 sin cambios: consolidar si
     los escenarios no-None coinciden, desglosar si no coinciden o si alguno es None
     ("no se pudo determinar en este escenario" para ese caso puntual).
```
El resto de la sección 3 de Iter-3 (formato del desglose por modelo, celdas `N/D`, notas de transparencia) no cambia.

**Criterios de aceptación (testeables):**
- [ ] `test_combinar_clasificacion_omitida_0_de_3_modelos`: con `pesimista.valor_justo_total=None`, `conservador.valor_justo_total=None`, `optimista.valor_justo_total=None` (mismos datos que `test_valuation_0_de_3_modelos` de Iter-2, extendidos a los 3 escenarios), `summary.py` no incluye ninguna línea de clasificación barata/cara en la respuesta — ni consolidada ni desglosada.
- [ ] Test de regresión explícito: `test_valuation_0_de_3_modelos` (Iter-2, ya existente) sigue pasando sin modificar — este patch no le agrega ninguna aserción nueva a ese test, la aserción nueva vive en el test de `summary.py` de arriba.
- [ ] Test que confirma la implicación estructural: dado cualquier fixture donde `compute_valuation()` (función sin cambios) retorne `valor_justo_total=None`, `compute_valuation_scenarios(...)` retorna `valor_justo_total=None` en los 3 escenarios (no solo en conservador) — cierra la premisa de este patch con evidencia de test, no solo con el argumento de diseño de arriba.

---

#### C2 — `calculate_momentum` con exactamente un promedio móvil ausente

**Decisión (adopta la recomendación de `qa` tal cual, sin ajustes):** la guarda de `etiqueta="no_disponible"` se extiende de "faltan los dos promedios móviles" a **"falta cualquiera de los dos"** (`price_avg_50` es `None` **o** `price_avg_200` es `None`). Las 3 etiquetas cualitativas (`impulso_positivo`/`impulso_negativo`/`mixto`) quedan definidas exclusivamente sobre la comparación simultánea contra ambos promedios — calcular una etiqueta con un solo dato disponible sería una fracción de esa definición, no una aproximación razonable: un precio por encima de su promedio de 50 días sin saber su posición respecto al de 200 días no permite distinguir, por ejemplo, un rebote de corto plazo dentro de una tendencia bajista de largo plazo de un verdadero "impulso positivo" — mostrar cualquiera de las 3 etiquetas igual sería inventar una lectura que los datos disponibles no sostienen.

**Ajuste al contrato de `calculate_momentum` (sección 6.1 de Iter-3, se reemplaza solo esta parte del docstring):**
```python
def calculate_momentum(
    *, price: float, year_high: Optional[float], year_low: Optional[float],
    price_avg_50: Optional[float], price_avg_200: Optional[float],
) -> MomentumResult:
    """Nunca lanza excepción; campos faltantes producen None puntual.

    etiqueta:
      - "no_disponible" si falta price_avg_50 O price_avg_200 (cualquiera de
        los dos, no solo si faltan ambos — Spec Patch Iter-4, C2). Las 3
        etiquetas cualitativas requieren la comparación simultánea contra
        ambos promedios; con un solo dato disponible no hay suficiente
        información para una lectura de tendencia confiable.
      - "impulso_positivo" si price > price_avg_50 y price > price_avg_200.
      - "impulso_negativo" si price < price_avg_50 y price < price_avg_200.
      - "mixto" en cualquier otro caso con ambos datos presentes.
    """
```

**Criterios de aceptación (testeables):**
- [ ] `test_momentum_no_disponible_falta_avg_50`: `price_avg_50=None`, `price_avg_200=150.0` (dato presente) → `etiqueta="no_disponible"`.
- [ ] `test_momentum_no_disponible_falta_avg_200`: `price_avg_50=180.0` (dato presente), `price_avg_200=None` → `etiqueta="no_disponible"`.
- [ ] `test_momentum_no_disponible_faltan_ambos`: caso ya existente en la sección 6.1 de Iter-3 (sin cambios) — sigue retornando `"no_disponible"`.
- [ ] Los 3 tests de etiquetas cualitativas ya listados en Iter-3 (`_impulso_positivo/_negativo/_mixto`) no cambian — siguen requiriendo ambos promedios presentes.

---

#### C3 — `compare_to_peers` con exactamente 1 peer válido

**Decisión (adopta la recomendación de `qa` tal cual, sin ajustes):** se trata igual que el caso ya resuelto en la sección 1 de Iter-3 para Múltiplos — con exactamente 1 peer válido, `per_minimo_peers == per_promedio_peers == per_maximo_peers` (mismo valor), y no hay un rango real contra el cual comparar. `posicion="no_comparable"` en este caso también, con un motivo explícito distinto al de "0 peers" o "EPS no positivo" — nunca `"en_linea"`/`"mas_barata"`/`"mas_cara"`, que sugerirían un rango real inexistente.

**Ajuste a la estructura de datos de `PeerComparisonResult` (sección 6.2 de Iter-3, se agrega un campo, mismo patrón que `ModeloExcluido`/`motivo` de Iter-2 y `per_no_aplicable`/`liquidez_sin_pasivos_circulantes` de B3/B4):**
```python
@dataclass
class PeerComparisonResult:
    per_propio: Optional[float]
    per_minimo_peers: Optional[float]
    per_promedio_peers: Optional[float]
    per_maximo_peers: Optional[float]
    peers_usados: list[str]
    posicion: str  # "mas_barata" | "en_linea" | "mas_cara" | "no_comparable"
    motivo_no_comparable: Optional[str] = None
    # "eps_no_positivo" | "sin_peers_validos" | "un_solo_peer_valido" (nuevo, Spec Patch Iter-4, C3)
    # None cuando posicion != "no_comparable".

def compare_to_peers(
    *, per_propio: Optional[float], per_minimo_peers: Optional[float],
    per_promedio_peers: Optional[float], per_maximo_peers: Optional[float],
    peers_usados: list[str],
) -> PeerComparisonResult:
    """Función pura, sin I/O.

    posicion="no_comparable" con motivo_no_comparable en 3 casos (Spec Patch
    Iter-4 agrega el tercero):
      - "eps_no_positivo" si per_propio es None.
      - "sin_peers_validos" si len(peers_usados) == 0.
      - "un_solo_peer_valido" si len(peers_usados) == 1 (per_minimo_peers ==
        per_promedio_peers == per_maximo_peers, no hay rango real contra el
        cual comparar, aunque los 3 campos numéricos existan).
    """
```

`summary.py` (sección 6.3 de Iter-3) muestra, para el caso nuevo, una frase equivalente a la ya definida para los otros 2 motivos: `"Solo 1 comparable con PER válido en tu set de peers — no hay rango suficiente para comparar."`, en vez de "en línea con tus comparables".

**Criterios de aceptación (testeables):**
- [ ] `test_compare_to_peers_no_comparable_un_solo_peer_valido`: con `peers_usados=["MSFT"]` (1 solo), `per_minimo_peers == per_promedio_peers == per_maximo_peers == 27.9` y `per_propio` = cualquier valor (mayor, menor o igual a 27.9) → `posicion="no_comparable"`, `motivo_no_comparable="un_solo_peer_valido"` — nunca `"en_linea"`/`"mas_barata"`/`"mas_cara"`, independientemente de dónde caiga `per_propio`.
- [ ] Los 2 tests de `_no_comparable` ya listados en Iter-3 se renombran/ajustan para incluir el campo nuevo: `test_compare_to_peers_no_comparable_eps_negativo` → `motivo_no_comparable="eps_no_positivo"`; `test_compare_to_peers_no_comparable_sin_peers_validos` → `motivo_no_comparable="sin_peers_validos"`.
- [ ] Los 3 tests de `_mas_barata/_en_linea/_mas_cara` ya listados en Iter-3 agregan la aserción `motivo_no_comparable is None` (ya que `posicion != "no_comparable"` en esos casos) y usan `len(peers_usados) >= 2` en su fixture, para no solapar accidentalmente con el caso degenerado de este patch.

---

### Confirmación de los números de Adobe (`DELTA_G`/`DELTA_WACC`) — sin ajuste necesario

Se confirma, con la evidencia numérica que `qa` ya trazó en su punto 1 (tabla con Múltiplos 600.00/658.00/714.00, Graham 435.64/555.64/675.64, DCF 225.64/288.82/376.50, Total 420.43/500.82/588.71): `DELTA_G=0.03` y `DELTA_WACC=0.01` **no necesitan ajuste** — con el fixture real de Adobe, ninguna de las guardas nuevas o existentes (multiplicador de Graham, `wacc<=terminal_growth`, <2 peers válidos) se activa en ningún escenario, y la relación `pesimista ≤ conservador ≤ optimista` (420.43 ≤ 500.82 ≤ 588.71) se cumple. `implementer` debe dejar esta tabla (o su equivalente calculado por el propio test) como comentario en `test_valuation_adobe_scenarios`, tal como `qa` recomendó, para que quede trazable sin tener que repetir la verificación a mano.

### Confirmación de buckets de cobertura

Se confirma, sin ajustes, la decisión de `qa` (dentro de su rol, no requería pasar por `architect`, pero se documenta aquí para que quede en un solo lugar junto con el resto de las decisiones de esta iteración): `market_context.py` → bucket de 70% ("resto", mismo nivel que `rules.py` — información contextual, no el motor de Valor Justo en sí); `peers.py` → bucket de 95% (alimenta directamente 2 de los 3 valores del escenario de Múltiplos desde este mismo Iter-3, y ya era una omisión preexistente de Iter-1 que correspondía corregir). Sin cambios en el umbral total del proyecto (≥75%).

### Criterios que NO cambian

Todo lo demás del Spec Patch Iter-3 (secciones 1-6), `security` Iter-3, y los puntos 1, 2 (salvo la regla de combinación actualizada arriba), 4 y 5 de `qa` Iter-3 siguen vigentes sin cambios. En particular:
- `DELTA_G=0.03`, `DELTA_WACC=0.01`, PER mínimo/promedio/máximo de peers para Múltiplos — sin cambios, confirmados arriba.
- La guarda de multiplicador de Graham `(8.5+2×g_pct)>0` y la guarda existente `wacc<=terminal_growth` — sin cambios.
- `classify_scenario(...)` y los 4 tests de clasificación de escenario individual (`_barata/_cara/_none`) — sin cambios.
- El formato de desglose por modelo (Pesimista | Conservador | Optimista) en `summary.py`, las celdas `N/D` de exclusión puntual, y la sección "Contexto de mercado" (momentum + comparación con peers) — sin cambios salvo los 3 ajustes puntuales de C1-C3 arriba.
- Pregunta abierta (f) (VIX/Fear & Greed) — sigue pendiente, sin cambios, no bloquea este patch.
- Ninguna de las 5 secciones de `security` (Iter-1) ni la sección de `security` Iter-3 — sin cambios; C1-C3 son ajustes de lógica de presentación/clasificación pura (sin I/O, sin secretos, sin input externo nuevo), mismo criterio que ya eximió a Iter-2 y a Iter-3 de volver a pasar por `security`.

---

### Handoff → qa

**Specs producidas:** este Spec Patch [Iter-4], agregado al final de `SDD_investbot_mvp.md` (no reemplaza ninguna sección anterior).

**Lo que `qa` debe confirmar (pasada corta, mismo patrón que su confirmación de Iter-2):**
- [ ] C1: la regla de combinación actualizada (omitir la sección de clasificación cuando los 3 escenarios son `None`) es testeable sin ambigüedad, y no introduce un caso nuevo no cubierto (ej. ¿qué pasa si 2 de 3 son `None` pero no los 3? — ya cubierto por la rama 2 de la regla de combinación, sin cambios respecto a Iter-3).
- [ ] C2: la guarda extendida de `calculate_momentum` (falta cualquiera de los dos promedios) no deja un caso intermedio sin definir.
- [ ] C3: el campo nuevo `motivo_no_comparable` en `PeerComparisonResult` es suficiente para un assert determinista en los 3 casos de `"no_comparable"`, y no rompe los tests ya existentes de `_mas_barata/_en_linea/_mas_cara`.

**Si `qa` confirma los 3 puntos:** el pipeline va **directo a `implementer`** — no vuelve a pasar por `security` (C1-C3 no tocan I/O, secretos, ni input externo nuevo, mismo criterio que ya se aplicó a B1-B5 de Iter-2 y al resto de Iter-3).

**Si `qa` encuentra que alguno de los 3 sigue sin ser testeable de forma determinista:** vuelve a `architect` con diagnóstico puntual de cuál de los 3 quedó incompleto — no se reabren los otros 2 que sí queden confirmados (Regla 2 de `pipeline.md`, criterios congelados).

---

## Confirmación de qa — Spec Patch Iter-4 [2026-07-28]

**Rol:** `qa`. Pasada corta de confirmación sobre el Spec Patch [Iter-4] — no repite el análisis de Iter-3 (esa sección sigue congelada, salvo la regla de combinación actualizada que este mismo patch toca).

### C1-C3, uno por uno

- **C1** — Sin ambigüedad. La regla de combinación de 2 pasos no se solapa ni contradice la revisión de Iter-3 para el caso de un solo escenario en `None` (`test_combinar_clasificacion_con_none`): esa rama sigue viviendo, sin cambios, dentro del paso 2 ("desglosar ... o si alguno es None"). El paso 1 solo se activa cuando `conservador.valor_justo_total is None`, y la garantía estructural del propio patch (un modelo excluido a nivel 1 lo está en los 3 escenarios por igual) hace que ese caso sea siempre "los 3 en None" — nunca "conservador en None pero pesimista/optimista no". El caso intermedio real (2 de 3 en `None`, con conservador no-None — posible por exclusiones de nivel 2 en pesimista u optimista, ej. guarda de multiplicador de Graham o `wacc<=terminal_growth` disparándose solo en un escenario) cae en el paso 2 sin ambigüedad: "alguno es None" → desglosar, con "no se pudo determinar en este escenario" para las filas en `None`. No queda ningún caso de los 4 posibles (0, 1, 2 o 3 escenarios en `None`) sin regla asignada. Test de regresión (`test_valuation_0_de_3_modelos` sin nuevas aserciones) y test nuevo de `summary.py` (`test_combinar_clasificacion_omitida_0_de_3_modelos`) están correctamente separados — confirmado, tal como se documentó en el patch.

- **C2** — Sin ambigüedad y sin cuarto caso suelto. Las combinaciones posibles de `(price_avg_50, price_avg_200)` son exactamente 4: ambos presentes, falta solo 50, falta solo 200, faltan ambos. Los 3 tests nuevos/existentes de `"no_disponible"` (`_falta_avg_50`, `_falta_avg_200`, `_faltan_ambos`) cubren las 3 combinaciones con al menos un dato ausente. La cuarta combinación (ambos presentes) no queda huérfana: la cubren los 3 tests cualitativos ya existentes de Iter-3 (`_impulso_positivo/_negativo/_mixto`), que en conjunto son exhaustivos sobre esa rama (`price` por encima de ambos, por debajo de ambos, o "cualquier otro caso" = mixto). Las 4 combinaciones de input tienen test asignado — no hay un quinto caso posible dado que la firma de la función solo tiene esos 2 parámetros opcionales relevantes a la guarda.

- **C3** — Alcanza para assert determinista en los 3 sub-casos y no rompe lo existente. `test_compare_to_peers_no_comparable_un_solo_peer_valido` fija `posicion="no_comparable"` y `motivo_no_comparable="un_solo_peer_valido"` con `per_propio` mayor, menor o igual al único peer — los 3 sub-casos dan el mismo resultado por diseño (no hay rango real, así que la posición de `per_propio` es irrelevante), lo cual es precisamente la propiedad que hay que verificar (que nunca se cuela `"mas_barata"/"en_linea"/"mas_cara"` aunque el valor lo "sugiera"). El patch ya identificó y resolvió el riesgo de ruptura: los tests existentes de `_mas_barata/_en_linea/_mas_cara` posiblemente usaban fixtures de 1 peer por simplicidad, lo cual ahora colisionaría con la regla nueva — el patch lo previene exigiéndoles `len(peers_usados) >= 2` explícitamente y agregándoles `motivo_no_comparable is None`. Los 2 tests de `_no_comparable` ya existentes (`eps_no_positivo`, `sin_peers_validos`) quedan intactos en su comportamiento, solo se les agrega el campo nuevo. Sin solapamiento entre los 3 motivos de `no_comparable` (son mutuamente excluyentes por construcción: `per_propio is None` vs. `len(peers_usados)==0` vs. `len(peers_usados)==1`).

### Números de Adobe — verificados contra el propio transcript de esta sesión

La tabla que cita este patch (Múltiplos 600.00/658.00/714.00, Graham 435.64/555.64/675.64, DCF 225.64/288.82/376.50, Total 420.43/500.82/588.71) coincide **exactamente, cifra por cifra**, con la tabla que `qa` trazó a mano en su propia revisión de Iter-3 (misma sección del documento, líneas con el detalle `Pesimista/Conservador/Optimista` y la relación 420.43 ≤ 500.82 ≤ 588.71). No hace falta re-ejecutar `valuation.py` contra el fixture: es la misma evidencia numérica, citada verbatim por `architect`, no un cálculo nuevo. Sin discrepancias.

### Reapertura de lo congelado

Confirmado: el patch no reabre las secciones 1-6 del Spec Patch Iter-3, `security` Iter-3, ni los puntos 1/2/4/5 de `qa` Iter-3 fuera de la regla de combinación actualizada (que sí estaba explícitamente en alcance de C1). No se detectó ningún criterio nuevo fuera de C1-C3.

### Confirmación de superficie de seguridad

Se confirma la afirmación de `architect`: C1-C3 son ajustes de lógica de presentación/clasificación pura sobre datos ya validados (omitir una sección de texto, extender una guarda de comparación numérica, agregar un campo de motivo a un dataclass) — ninguno introduce I/O, secreto, ni vector de input externo nuevo. No hay razón para volver a pasar por `security` ni por `frontend` (no aplica, bot de Telegram sin UI web, consistente con Iter-1/Iter-2/Iter-3).

### Veredicto de `qa`

**SCOPE FREEZE confirmado para el Spec Patch Iter-4.** Los 3 bloqueantes C1-C3 quedaron con comportamiento definido, estructura de retorno concreta (`motivo_no_comparable`), regla de combinación sin casos huérfanos, y criterios de aceptación con assert determinista, escribibles como test unitario sin inventar nada.

**El pipeline completo (Iter-1 + Iter-2 + Iter-3 + Iter-4) queda listo para `implementer`, sin volver a `security` ni a `frontend`.**

---
