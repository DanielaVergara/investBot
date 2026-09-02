# Evaluación: ¿conviene agregar web scraping como fuente de datos para InvestBot?

**Tipo de documento:** evaluación de factibilidad (no es una spec de implementación — no define criterios de aceptación ni artefactos a crear). Responde a la pregunta de Daniela: "¿conviene usar scraping para llenar los huecos de datos del bot?"

**Fecha:** 2026-09-02
**Evaluador:** architect

---

## Contexto

InvestBot corre 100% sobre el plan gratuito de FMP — decisión de diseño explícita del proyecto ("un solo diseño, 100% plan gratuito, sin excepciones", `SDD_investbot_mvp.md`). Hoy hay 2 huecos de datos causados por esa restricción:

1. **Beneish M-Score siempre "no calculable"** (`advanced_scoring.py`, decisión D1 ya tomada por Daniela, no reabierta por esta evaluación) — FMP gratuito no separa PP&E bruto de PP&E neto, ni depreciación pura de amortización combinada.
2. **Detección de ETF/fondo sin verificar** (`advanced_command.py`, `_is_etf_or_fund`, decisión D6 no bloqueante) — los campos `isEtf`/`isFund`/`isActivelyTrading` de `/profile` nunca se confirmaron con un `curl` real contra el plan pago de FMP.

Daniela preguntó si scrapear una fuente pública (`stockanalysis.com`, ya usada con éxito para investigación manual en `analisis_fundamental_avanzado.md`) resolvería esto y le daría mejor información para entender los modelos.

Hice pruebas reales (`curl` directo, no solo inferencia) contra `stockanalysis.com` y contra la documentación pública de FMP. Los hallazgos abajo son de esas pruebas, no supuestos.

---

## Hallazgo 1 — ¿Llenaría el scraping los huecos de datos?

### Beneish M-Score (gap principal)

Hice `curl` real contra `https://stockanalysis.com/stocks/aapl/financials/balance-sheet/` (240 KB de HTML, fetch exitoso) y grepeé el JSON embebido que la página envía al navegador (SvelteKit hidrata la tabla desde ahí, no es solo HTML de tabla).

**Resultado real:**
- Los campos `grossPPE` (PP&E bruto) y `accumulatedDepreciation` (depreciación acumulada) **sí existen** en ese JSON embebido — encontrados textualmente: `grossPPE:124311000000`, `accumulatedDepreciation:-75803000000` (AAPL, período TTM más reciente).
- Pero cada uno **aparece exactamente una sola vez** en toda la página — es decir, son parte de un objeto de métricas "punto en el tiempo" (probablemente usado internamente por el sitio para calcular alguna razón como "antigüedad promedio del PP&E"), **no una serie histórica por año**. No hay un segundo valor para el año anterior en la misma carga de página.
- La tabla **visible** que un humano ve en esa página (la que Daniela consultó manualmente) **no muestra estas dos líneas** — solo muestra "Property, Plant & Equipment" neto. Confirmé esto con una segunda pasada de lectura de la página además del grep del JSON.
- Beneish M-Score necesita estas 2 variables (para AQI y DEPI) en **dos períodos** (año actual y año anterior) para calcular una tasa de cambio. Con un solo punto en el tiempo, expuesto además por una vía no documentada y no destinada a consumo externo, **no alcanza para calcular el modelo** — haría falta otra fuente para el segundo período, o esperar un año y cachear el valor, lo cual no es viable para un bot que responde en el momento a cualquier ticker.

**Conclusión de este punto:** scrapear stockanalysis.com **no resuelve** el gap de Beneish M-Score de forma confiable hoy. Los campos existen, pero no en la forma (serie de 2+ años) que el modelo requiere, y dependen de una estructura interna no documentada que puede no repetirse igual para otros tickers (no probé con un ticker distinto de AAPL para no generalizar de más — este hallazgo es específico de AAPL y debería reverificarse por ticker si se quisiera avanzar).

### Detección de ETF (gap secundario)

Probé con `curl` si `stockanalysis.com` distingue ETFs de acciones por estructura de URL: `stockanalysis.com/stocks/spy/` devuelve **HTTP 200 con redirect real a** `stockanalysis.com/etf/spy/`. El sitio clasifica a SPY como ETF y lo enruta a una sección distinta.

**Este es un dato mucho más simple y confiable de extraer que el de Beneish** — no requiere parsear una tabla ni un JSON interno, solo observar a qué URL redirige el sitio. Si se quisiera un scraping mínimo, esta sería la única parte que de verdad vale la pena considerar (ver "Alternativa" al final, aunque la alternativa recomendada ahí sigue siendo no-scraping).

---

## Hallazgo 2 — Confiabilidad / riesgo de rotura silenciosa

El dato de Beneish que sirve depende de un **JSON interno no documentado, embebido en el bundle de la aplicación web** (no una API pública ni un contrato versionado). Esto es un riesgo distinto y peor al de una API:

- Un cambio de FMP da un **error HTTP explícito** (402, 404, cambio de esquema con campo ausente que el proyecto ya sabe detectar con `_get_num` devolviendo `None`).
- Un cambio de `stockanalysis.com` (rediseño de página, cambio de framework, renombrar `grossPPE` a otra clave, o simplemente dejar de exponer ese campo en el bundle) **no da ningún error** — el scraper puede seguir corriendo, devolver 200, y silenciosamente empezar a parsear texto o estructura que ya no significa lo mismo, o directamente no encontrar la clave y quedarse en blanco sin que nada lo señale como una falla del sistema.
- Cómo se detectaría: la única forma real es un **test de forma de datos corriendo periódicamente** (ej. un smoke test diario que haga el fetch real y valide que las claves esperadas existen, son numéricas, y están en un rango plausible) — no un test que corre en CI contra un fixture estático, porque un fixture estático nunca detecta que la fuente real cambió. Esto es infraestructura adicional (un job periódico, alertas si falla) que el proyecto no tiene hoy para ninguna otra dependencia externa — FMP no la necesita porque sus errores son explícitos.

**Conclusión de este punto:** el costo de mantenimiento no es solo "escribir el scraper" — es mantener para siempre un detector de rotura silenciosa que hoy no existe en el proyecto.

---

## Hallazgo 3 — Legal / términos de servicio

Hice `curl` real contra:

- **`robots.txt`** (`https://stockanalysis.com/robots.txt`, verificado con `curl -v`, HTTP 200): permite crawling genérico (`User-agent: *`) salvo `/e/` y `/p/`; bloquea 3 bots específicos por nombre (`dotbot`, `BLEXBot`, `mj12bot` — crawlers de análisis de enlaces/SEO, no relevantes para este caso). **No bloquea `/stocks/` ni `/etf/`**, que son las rutas que se necesitarían.
- **Terms of Use** (`https://stockanalysis.com/terms-of-use/`, página real obtenida y leída completa, no la página 404 de `/terms-of-service/` que probé primero por error): **no contiene ninguna cláusula que prohíba explícitamente scraping, bots, o acceso automatizado** (busqué las palabras "scrape", "crawl", "bot", "automated", "API" — ninguna aparece). La única restricción de uso de contenido que sí existe textualmente es: *"It is not allowed to republish our content in full without our explicit permission. However, you can use snippets of the content as long as you do not modify the content and clearly state where you got it from."* — esto habla de **republicar** el contenido (mostrárselo a terceros como si fuera propio), no de leerlo programáticamente para uso interno de un bot personal.

**Conclusión de este punto:** no encontré una prohibición explícita. Esto **no es lo mismo que "es legal sin restricción"** — el `robots.txt` es una señal técnica de cortesía, no un contrato, y los ToS no cubren expresamente el caso de un bot que lee datos para procesarlos (no para republicarlos). Es una zona gris razonablemente favorable, no una autorización clara. Si el uso creciera (muchos usuarios, alto volumen), valdría la pena una revisión legal más allá de lo que un análisis técnico puede confirmar.

---

## Hallazgo 4 — Costo operativo

- **Latencia medida real:** un solo fetch a la página de balance de AAPL tardó **0.25s** desde este entorno (`curl -w time_total`). No es el escenario típico de "el scraping es mucho más lento" — al menos para esta fuente, la respuesta HTTP es rápida (usa Cloudflare + CDN). El costo real no está en la latencia de red sino en: (a) parsear HTML/JSON no estructurado en cada llamada (trabajo de CPU y de mantenimiento que una respuesta JSON de API no requiere), y (b) el riesgo de bloqueo si el volumen crece.
- **Sin cupo diario tipo FMP (250 req/día)** — a favor del scraping en apariencia — pero **con riesgo real de bloqueo por Cloudflare/rate-limit/CAPTCHA** si el patrón de tráfico se ve automatizado (peticiones muy regulares, mismo User-Agent, sin cookies de sesión reales). FMP gratuito, en cambio, da un 429/402 explícito y documentado; un bloqueo de Cloudflare puede ser silencioso (un CAPTCHA en el HTML en vez de datos) y se confunde con el problema del Hallazgo 2.
- **Frecuencia de uso esperada:** si esto se implementara, la única forma sensata es **cachear agresivamente** (una vez por ticker por día, no una vez por cada `/avanzado` que corre un usuario) — nunca "cada vez que alguien pide un análisis", porque eso multiplica el riesgo de bloqueo sin ninguna ganancia (el balance general de una empresa no cambia intra-día).

---

## Tensión con la filosofía del proyecto (punto 5 — no es mi decisión, la dejo planteada)

El proyecto se vendió explícitamente a Daniela como **"100% FMP gratuito, sin excepciones"** — es una decisión de diseño documentada, no un detalle incidental. Agregar `stockanalysis.com` como fuente de datos, aunque sea gratuita y aunque no cueste dinero, **rompe esa premisa de diseño de raíz**: ya no es "un solo proveedor de datos gratuito" sino "FMP + una fuente externa scrapeada sin contrato ni SLA".

Esto no es solo una cuestión estética de arquitectura — tiene consecuencias concretas que ya se vieron en los Hallazgos 2-4: una segunda fuente de datos duplica la superficie de cosas que pueden romperse silenciosamente, y lo hace con una fuente que es estructuralmente menos confiable que FMP (sin contrato de API, sin versión, sin canal de errores explícito).

La pregunta que le queda a Daniela no es técnica sino de producto: ¿el valor de tener Beneish M-Score calculado (aunque sea con datos de una sola época, sin la serie de 2 años que el modelo realmente necesita — ver Hallazgo 1) justifica renunciar a la garantía de "un solo proveedor, sin sorpresas" que hoy el proyecto puede ofrecer con confianza?

---

## Alternativa más chica (punto 6): endpoint pago de FMP

Antes de considerar una fuente externa, vale la pena preguntar si el **plan pago de FMP** (no scraping, sigue siendo el mismo proveedor, con contrato y soporte) ya trae estos 2 campos separados. FMP tiene un endpoint de "estados financieros completos / as-reported" que en otros proyectos suele incluir el detalle completo de PP&E (bruto, depreciación acumulada, neto) tal como aparece en el 10-K — pero es un endpoint típicamente marcado como Premium en la documentación pública de FMP.

**No pude confirmarlo con una prueba real en este entorno** — intenté acceder a `site.financialmodelingprep.com` (la documentación de FMP) y obtuve HTTP 403, el mismo bloqueo de red ya documentado en el proyecto (`_is_etf_or_fund`, "sin acceso de red a site.financialmodelingprep.com"). Esto es consistente con el patrón que el proyecto ya conoce: la documentación pública de FMP no es confiable para decidir qué es gratis y qué no (`/api/v3` resultó discontinuada, `/key-metrics-ttm` resultó de pago pese a parecer gratis en la documentación) — solo un `curl` real con la key paga de Daniela puede confirmarlo.

**Por qué esta alternativa es estructuralmente mejor que scraping, si el dato existe:** sigue siendo FMP (un solo proveedor, un solo contrato, errores HTTP explícitos, sin riesgo de bloqueo por Cloudflare, sin riesgo de rotura silenciosa por rediseño de página). El único costo es monetario (el plan pago), no arquitectónico. Si Daniela algún día tiene o considera tener el plan pago por otra razón, verificar este endpoint específico es una prueba de 5 minutos con `curl` y la key real — mucho más barata que evaluar o mantener un scraper.

---

## Recomendación final

**No, no conviene implementar scraping de `stockanalysis.com` (ni de otra fuente pública similar) para llenar estos 2 huecos, con las condiciones descritas abajo.**

Razones, en orden de peso:

1. **No resuelve el problema que se buscaba resolver.** El Hallazgo 1 muestra que ni siquiera obtendría lo necesario para calcular Beneish M-Score de forma confiable (falta la serie de 2 años) — se estaría asumiendo un riesgo arquitectónico y legal a cambio de nada, no a cambio de una feature completa.
2. **Rompe la garantía central del proyecto** ("100% FMP, sin excepciones") a cambio de una fuente estructuralmente menos confiable — sin contrato, sin canal de error explícito, con riesgo de rotura silenciosa que hoy el proyecto no tiene que gestionar para ninguna otra dependencia.
3. **El costo de mantenimiento no es el scraper — es el detector de que el scraper se rompió.** Eso es infraestructura nueva (monitoreo periódico) que el proyecto no tiene precedente de operar.

**Condición bajo la cual reconsideraría:** si en el futuro se encuentra una fuente que sí expone la serie histórica completa (2+ años) de PP&E bruto y depreciación de forma estable y con alguna garantía de contrato (no un JSON interno no documentado), y Daniela decide explícitamente que quiere relajar la regla de "100% FMP" a cambio de esa feature — ahí sí valdría la pena una spec real, con el detector de rotura del Hallazgo 2 como criterio de aceptación obligatorio, no opcional.

**Lo que sí recomiendo en su lugar, en orden de prioridad:**
1. Para el gap de ETF (D6): usar la señal de redirect de `stockanalysis.com` (`/stocks/<ticker>/` → `/etf/<ticker>/`) es técnicamente viable y mucho más simple/confiable que el caso de Beneish (no depende de parsear datos financieros, solo de una redirección HTTP) — pero sigue siendo una fuente externa nueva para el mismo problema filosófico del punto 5. Antes de eso, lo más barato y alineado con la filosofía del proyecto es **cerrar D6 con un `curl` real contra `/profile` con la key paga de Daniela** (la verificación que el proyecto ya tiene pendiente y nunca costó nada nuevo) — sigue siendo FMP.
2. Para Beneish (D1): dejarlo como está (ya es una decisión resuelta y aceptada — "no calculable con este plan, sin excepción"). Si algún día se quiere reabrir, la vía más barata es confirmar con `curl` real si el plan pago de FMP separa estos campos (ver sección de Alternativa) antes de mirar cualquier fuente externa.

La decisión final, como siempre, es de Daniela — este documento deja los hechos verificados y el trade-off sobre la mesa, no la resuelve por ella.
