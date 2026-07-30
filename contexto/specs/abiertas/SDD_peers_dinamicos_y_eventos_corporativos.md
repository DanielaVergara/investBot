# Spec: Peers dinámicos por sector (Finnhub) + Eventos corporativos relevantes (SEC EDGAR) [Iter-1]

**Rol:** `architect`.
**Pipeline:** BMAD + SDD + Ralph Loop (ver `/Users/danielavergara/Documents/Programming/claude/contextos/pipeline.md`)
**Siguiente paso:** `qa` agrega criterios de cobertura/testabilidad. Son **2 features independientes, agrupadas en un solo documento** porque nacieron de la misma conversación con Daniela (misma iniciativa: "análisis más completo sin romper la premisa de 100% gratis"), pero tocan superficies distintas — cada una con su propia sección de Contexto/Estado actual/Estado objetivo/Decisiones/Preguntas abiertas/Criterios/Artefactos/Restricciones. **Ambas agregan un proveedor HTTP nuevo** (`finnhub_client.py`, `sec_edgar_client.py`), ninguno de los dos consume el presupuesto de 250 req/día de FMP (proveedores distintos, mismo principio que FRED/Treasury.gov). **Las 5 preguntas bloqueantes que dejó abiertas la versión anterior de esta spec ya están resueltas por Daniela** (ver "Resolución de las preguntas bloqueantes" al principio de cada Parte, y el consolidado al final). **`security` ya revisó esta spec y encontró 1 hallazgo bloqueante puntual** (email real de Daniela en `.env.example` de un repo público — Decisión #4 de la Parte 2), **ya resuelto y aplicado** (ver "Resolución de hallazgos de `security`", justo antes del Handoff). No queda ninguna pregunta bloqueante de Daniela ni ningún hallazgo de `security` sin resolver — el spec está listo para `qa` sin gating adicional. Este proyecto no usa un paso de `frontend` separado (bot de solo texto sobre Telegram).

---

## Contexto general (compartido por ambas partes)

Daniela probó el bot con NVIDIA y notó que el rango Pesimista/Conservador/Optimista de "Múltiplos" quedaba plano (2 de los 3 peers fijos de `peers.py::PEERS_BY_SECTOR` no devolvieron PER válido esta consulta, dejando 1 solo comparable — `min == promedio == máximo` por construcción). De ahí salió el pedido de peers dinámicos (Parte 1). En paralelo pidió que el bot muestre algo sobre eventos corporativos recientes — cambios de gerencia, contratos, litigios (Parte 2).

Restricción dura de todo el proyecto, repetida para esta spec: **100% gratis, sin excepciones** — cualquier fuente nueva se investiga primero para confirmar que es gratuita (o se acepta explícitamente el riesgo de que deje de serlo, con guarda defensiva de fallback), nunca se asume sin evidencia razonable. Ninguna fórmula de valoración existente (PER, P/S, Múltiplos, Graham, DCF, pilares, encaje de riesgo) cambia una sola línea de lógica en esta spec — ambas features son aditivas/informativas.

---

# PARTE 1 — Peers dinámicos por sector (Finnhub)

## Resolución de las preguntas bloqueantes (2026-07-29)

Daniela resolvió las 3 preguntas bloqueantes que dejó abiertas la versión anterior de esta spec. Quedan **cerradas**, no se reabren:

- **1.1 — grouping de Finnhub:** `subIndustry` — la recomendación del `architect`, confirmada.
- **1.2 — mínimo de peers dinámicos antes de aceptar la lista de Finnhub:** **3**, igual al tamaño de la lista fija actual. Es más exigente que la Opción A originalmente esbozada (que aceptaba cualquier lista no vacía) — Daniela aceptó explícitamente que esto hace caer al respaldo fijo más seguido cuando Finnhub responde con una lista corta (1 o 2 tickers), a cambio de nunca aceptar un set de comparables más pobre que el que el bot ya usa hoy.
- **1.3 — tope de peers dinámicos consultados contra FMP:** **5** — la recomendación del `architect` (reutiliza el límite superior ya documentado en README), confirmada.

Con estas 3 resoluciones, **no queda ninguna pregunta bloqueante pendiente en la Parte 1.**

## Contexto

Hoy `peers.py::PEERS_BY_SECTOR` es un diccionario hardcodeado (exactamente 3 tickers por sector, ej. `"Technology": ["MSFT", "ORCL", "CRM"]`), mantenido a mano. El README (línea 48-49) y el docstring de `peers.py` (líneas 1-6) ya documentan esto como una aproximación aceptada — pero el caso NVIDIA mostró su punto débil: si 2 de los 3 peers fallan, el rango de Múltiplos queda plano.

Investigué `/stock-peers` de FMP (haría exactamente esto) pero es **confirmado de pago** (no está en el free tier documentado ni observado). En cambio, **Finnhub** — proveedor gratis distinto, sin tarjeta — tiene `GET /stock/peers?symbol=X&grouping=sector|industry|subIndustry` (default `subIndustry`), documentado en múltiples fuentes públicas como parte del free tier (60 llamadas/minuto). **No verificado con `curl` real** — mismo criterio que "Resolución (a)" de `SDD_contenido_financiero_explicado.md` (Daniela ya aceptó ese patrón para el VIX: no bloquear el diseño con verificación manual previa, implementar con guardas defensivas, descubrir en uso real). Aplico el mismo criterio acá explícitamente, sin doble estándar.

Finnhub tiene antecedente documentado de mover endpoints de gratis a pago sin aviso (GitHub issue `finnhubio/Finnhub-API#271`) — ese issue puntual nombra "Dividends" y "Major Developments", **no** "Company Peers". No es evidencia de que `/stock/peers` esté en riesgo inminente, pero tampoco es garantía de que nunca lo esté — la guarda defensiva de esta spec (fallback automático a la lista fija) trata **cualquier** fallo de Finnhub (402/403/timeout/red/JSON inesperado) exactamente igual, sin distinguir "todavía gratis" de "ya no es gratis": mismo criterio de "no confiar en que un endpoint gratis lo siga siendo para siempre" que ya rige `fmp_client.py` (ver su docstring, líneas 1-22) y que motivó toda esta feature.

## Estado actual

- `src/investbot/peers.py::PEERS_BY_SECTOR` (líneas 30-42): único origen de peers candidatos, vía `get_peers_for_sector(sector, own_ticker)` (líneas 62-65) — filtra el propio ticker, nada más.
- `src/investbot/peers.py::get_peer_pe_average` (líneas 68-130): recibe `get_peer_metrics_fn` (inyectado, hoy siempre `fmp_client.get_key_metrics` parcialmente aplicado por peer) + `sector` + `own_ticker`; llama a `get_peers_for_sector` internamente (línea 91) — no hay ningún punto de inyección para una fuente de candidatos alternativa. `PeerAverageResult` (líneas 45-59) ya expone `peers_pe: dict[str, float]` y `peers_no_usados: dict[str, str]` (spec anterior, ya implementada) — no tiene ningún campo que indique de dónde salió la lista de candidatos.
- `src/investbot/market_context.py::PeerComparisonResult`/`compare_to_peers` (líneas 130-227): propaga `peers_pe`/`peers_no_usados` sin cambios de lógica de clasificación — mismo hueco, no hay campo de "fuente de peers".
- `src/investbot/query_handler.py::fetch_and_analyze_parts` (líneas 153-166): arma `_get_metrics_for_peer` (closure que llama a `fmp_client.get_key_metrics` y traduce `FMPError` a `None`) y llama a `peers.get_peer_pe_average(get_peer_metrics_fn=..., sector=sector, own_ticker=ticker)` — sin ningún otro parámetro.
- `src/investbot/query_handler.py::Clients` (líneas 71-79): bundle de clientes HTTP + API keys — `fmp_http`, `fred_http`, `treasury_gov_http`, `fmp_api_key`, `fred_api_key`. No hay cliente ni key para Finnhub.
- `src/investbot/bot.py::build_application` (líneas 47-85): construye `Clients` con 3 `httpx.AsyncClient()` y 2 API keys leídas de entorno en `main()` (líneas 88-117: `TELEGRAM_BOT_TOKEN`, `FMP_API_KEY` obligatorias — fail con `sys.exit(1)` si faltan —, `FRED_API_KEY` opcional).
- `src/investbot/summary.py::build_summary_parts`/`build_summary` (líneas 519-696): parámetro `peers_note` con default hardcodeado (líneas 532-539 / 665-672) que dice explícitamente **"esta lista de comparables por sector es fija y fue elegida a mano... no la arma FMP, ni la elige ningún algoritmo dinámico, ni se actualiza sola"** — este texto se vuelve **falso** en cuanto Finnhub aporte la lista dinámicamente esta consulta; hoy no hay ningún mecanismo para que el texto varíe según qué fuente se usó.
- `tests/test_peers.py` (línea 311 y alrededores): asume el orden fijo `PEERS_BY_SECTOR["Technology"]` (MSFT, ORCL, CRM) — ningún test cubre una fuente de candidatos alternativa.
- `.env.example`: no tiene ninguna variable para Finnhub.
- `README.md` (tabla "Presupuesto de requests por consulta", línea 66): fila "Peers para el modelo de Múltiplos | 3-5 | `/key-metrics` (anual) por peer" — el "3-5" ya está documentado hoy aunque el código actual siempre da exactamente 3 (ningún sector tiene más de 3 en `PEERS_BY_SECTOR`); es un desalineamiento preexistente entre docs y código, no introducido por esta spec, que esta spec **resuelve** (el 3-5 pasa a ser real una vez Finnhub puede devolver hasta 5 candidatos).

## Estado objetivo

1. Nuevo módulo `src/investbot/finnhub_client.py` — wrapper HTTP fino a Finnhub, mismo patrón de seguridad que `fmp_client.py`/`treasury_client.py`: cliente `httpx.AsyncClient` inyectado, `params=` siempre (nunca f-string), excepciones traducidas a `FinnhubError` sanitizado (nunca incluye la URL ni la API key), solo loguea endpoint/status.
2. Nueva función `finnhub_client.get_peers(client, api_key, symbol, *, grouping="subIndustry") -> list[str]` — 1 llamada HTTP por consulta, a `GET https://finnhub.io/api/v1/stock/peers`.
3. Nueva env var opcional `FINNHUB_API_KEY`. Si no está seteada, o la llamada a Finnhub falla por cualquier motivo (402/403/429/timeout/red/JSON inesperado/lista vacía), **fallback automático y silencioso** a `PEERS_BY_SECTOR` (la lista fija pasa de ser la única fuente a ser el respaldo — no se elimina ni se borra ninguna entrada existente).
4. `peers.py::PeerAverageResult` gana un campo `fuente_peers: str` (`"finnhub"` | `"fijo_respaldo"`) para que el resto del pipeline (y finalmente `summary.py`) sepa, por consulta, qué fuente se usó — necesario para no mostrar el texto de "lista fija elegida a mano" cuando en realidad se usó Finnhub esta vez (ver Decisión #5).
5. El PER de cada peer devuelto por Finnhub lo sigue calculando FMP exactamente igual que hoy (`get_peer_pe_average` reutiliza el mismo loop, `get_peer_metrics_fn` sin cambios) — Finnhub solo aporta la lista de QUIÉNES son peers candidatos, nunca sus métricas. El manejo de `peers_no_usados`/`peers_pe` (motivo de exclusión por peer) es exactamente el mismo ya implementado, sin ningún cambio — no se reinventa esa parte, tal como pidió Daniela.
6. Cero llamadas HTTP nuevas contra FMP por el hecho de agregar Finnhub en sí (1 llamada nueva, a un proveedor distinto, fuera del presupuesto de 250/día) — pero **si Finnhub devuelve más candidatos que los 3 fijos de hoy, cada candidato adicional SÍ agrega una llamada `/key-metrics` a FMP** (ver Decisión #2 sobre el tope de candidatos, fijado en 5 — Pregunta 1.3, resuelta).

## Decisiones de diseño tomadas

### 1. `finnhub_client.py` — mismo patrón de `fmp_client.py`/`treasury_client.py`

```python
"""Wrapper HTTP a Finnhub — solo el endpoint de peers dinámicos (Feature 1).

Fuente PRIMARIA para la lista de peers candidatos por ticker. Fallback si
falla o no está configurada: PEERS_BY_SECTOR hardcodeado en peers.py (la
lista fija pasa de "única fuente" a "respaldo", no se elimina).

Finnhub tiene antecedente documentado (GitHub issue finnhubio/Finnhub-API#271)
de mover endpoints de gratis a pago sin aviso -- ese issue puntual nombra
"Dividends"/"Major Developments", no "Company Peers", pero este cliente trata
CUALQUIER fallo (402/403/429/timeout/red/JSON inesperado) exactamente igual:
fallback silencioso a la lista fija, nunca un error visible al usuario, nunca
una excepción cruda propagada.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 10.0


class FinnhubError(Exception):
    """Error sanitizado de Finnhub -- nunca incluye la URL ni la API key."""


async def get_peers(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    *,
    grouping: str = "subIndustry",
) -> list[str]:
    """Peers dinámicos de Finnhub para `symbol`. `grouping`: "sector" |
    "industry" | "subIndustry" (default de Finnhub y de este wrapper --
    Pregunta 1.1 RESUELTA por Daniela: subIndustry, para atacar la causa
    raíz del caso NVIDIA -- comparables angostos por sub-industria en vez
    del sector GICS completo). `query_handler.py` no pasa un `grouping`
    distinto -- no hay override configurable, es el valor fijo a usar.

    Nunca propaga excepciones crudas de httpx -- las traduce a FinnhubError.
    El llamador (query_handler.py, vía closure inyectada a peers.py) es
    responsable de capturarla y devolver [] para activar el fallback
    (mismo contrato que _get_metrics_for_peer ya usa con FMPError hoy).
    """
    url = f"{BASE_URL}/stock/peers"
    try:
        response = await client.get(
            url,
            params={"symbol": symbol, "grouping": grouping, "token": api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("Finnhub timeout — endpoint=/stock/peers")
        raise FinnhubError("Finnhub no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("Finnhub error HTTP — endpoint=/stock/peers status=%s", status)
        raise FinnhubError(f"Finnhub respondió con un error ({status}).")
    except httpx.RequestError:
        logger.warning("Finnhub error de red — endpoint=/stock/peers")
        raise FinnhubError("No pude conectarme a Finnhub ahora mismo.")

    try:
        data: Any = response.json()
    except ValueError:
        logger.warning("Finnhub respuesta no-JSON — endpoint=/stock/peers")
        raise FinnhubError("Finnhub devolvió una respuesta inesperada.")

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]
```

**Nunca se loguea la API key** — va como query param `token=`, mismo riesgo que `apikey=` de FMP o `api_key=` de FRED, mismo criterio ya auditado por `security` en esos 2 clientes (solo se loguea `endpoint_label`/status, nunca la URL completa ni `params`).

### 2. `peers.py` — nuevas constantes de fuente + parámetro de inyección opcional en `get_peer_pe_average`

```python
PEERS_FUENTE_FINNHUB = "finnhub"
PEERS_FUENTE_FIJO = "fijo_respaldo"
# Tope de candidatos dinámicos a consultar contra FMP por consulta —
# Pregunta 1.3, RESUELTA por Daniela: 5 (reutiliza el límite superior ya
# documentado hoy en README.md, "3-5 peers", no un número nuevo).
MAX_PEERS_DINAMICOS = 5
# Mínimo de peers dinámicos que Finnhub debe devolver (tras filtrar el
# propio ticker) para aceptar la lista dinámica esta consulta — Pregunta
# 1.2, RESUELTA por Daniela: 3, igual al tamaño de la lista fija actual.
# Más exigente que "cualquier lista no vacía": si Finnhub devuelve 1 o 2
# tickers, se descarta esa lista completa y se cae al respaldo fijo
# (nunca se combinan ambas fuentes en la misma consulta — Restricción ya
# vigente). Aceptado explícitamente por Daniela: prioriza no aceptar un
# set de comparables más pobre que el que el bot ya usa hoy, a costa de
# caer al respaldo más seguido cuando Finnhub responde con una lista corta.
MIN_PEERS_DINAMICOS_PARA_USAR = 3


@dataclass
class PeerAverageResult:
    per_promedio: Optional[float]
    per_minimo: Optional[float]
    per_maximo: Optional[float]
    peers_usados: list[str]
    peers_pe: dict[str, float] = field(default_factory=dict)
    peers_no_usados: dict[str, str] = field(default_factory=dict)
    fuente_peers: str = PEERS_FUENTE_FIJO  # NUEVO — default preserva el
    # comportamiento de cualquier test/código que instancie el dataclass
    # sin pasar este campo.


async def get_peer_pe_average(
    *,
    get_peer_metrics_fn: Callable[[str], Awaitable[Optional[dict]]],
    sector: str,
    own_ticker: str,
    get_dynamic_peers_fn: Optional[Callable[[str], Awaitable[list[str]]]] = None,  # NUEVO
) -> PeerAverageResult:
    fuente_peers = PEERS_FUENTE_FIJO
    peers_candidatos: list[str] = []

    if get_dynamic_peers_fn is not None:
        dynamic = await get_dynamic_peers_fn(own_ticker)
        # Filtra el propio ticker (Finnhub a veces lo incluye en su propia
        # respuesta) y aplica el tope — mismo principio de "propio ticker
        # excluido" que get_peers_for_sector ya aplica hoy.
        dynamic = [p for p in dynamic if p.upper() != own_ticker.upper()][:MAX_PEERS_DINAMICOS]
        if len(dynamic) >= MIN_PEERS_DINAMICOS_PARA_USAR:  # Pregunta 1.2,
            # RESUELTA: 3 como mínimo (igual al tamaño de la lista fija) —
            # una lista de Finnhub más corta que esto se descarta entera,
            # nunca se usa parcialmente ni se completa con la lista fija.
            peers_candidatos = dynamic
            fuente_peers = PEERS_FUENTE_FINNHUB

    if not peers_candidatos:
        peers_candidatos = get_peers_for_sector(sector, own_ticker)
        fuente_peers = PEERS_FUENTE_FIJO

    # ... resto del loop EXACTAMENTE igual a como está hoy (líneas 96-113),
    # reemplazando `peers = get_peers_for_sector(sector, own_ticker)` por
    # `peers = peers_candidatos` ...

    if not pes:
        return PeerAverageResult(
            per_promedio=None, per_minimo=None, per_maximo=None,
            peers_usados=[], peers_pe={}, peers_no_usados=peers_no_usados,
            fuente_peers=fuente_peers,
        )
    return PeerAverageResult(
        per_promedio=sum(pes) / len(pes), per_minimo=min(pes), per_maximo=max(pes),
        peers_usados=usados, peers_pe=peers_pe, peers_no_usados=peers_no_usados,
        fuente_peers=fuente_peers,
    )
```

**`get_dynamic_peers_fn` nunca debe lanzar** — es responsabilidad del closure que arma `query_handler.py` (igual que `_get_metrics_for_peer` ya hace con `FMPError` hoy) capturar `finnhub_client.FinnhubError` y devolver `[]`. `peers.py` sigue sin conocer `finnhub_client.py` ni `fmp_client.py` directamente — mismo principio de inyección de dependencias ya usado en todo el módulo.

**El orden de `peers_candidatos` cuando viene de Finnhub no está garantizado por relevancia** — Finnhub no documenta el criterio de orden del array que devuelve. El tope `[:MAX_PEERS_DINAMICOS]` es "primeros N que Finnhub devolvió", no "los N más relevantes" — misma clase de aproximación aceptada que ya usa el proyecto para PER de peers vía `/key-metrics` anual (no TTM).

### 3. `market_context.py` — `PeerComparisonResult`/`compare_to_peers` propagan `fuente_peers`

Mismo patrón que `peers_pe`/`peers_no_usados` (spec anterior): nuevo campo en el dataclass, nuevo parámetro opcional en `compare_to_peers` (default `PEERS_FUENTE_FIJO`), propagado sin cambios en las 4 ramas de retorno (`eps_no_positivo`/`sin_peers_validos`/`un_solo_peer_valido`/comparable) — no condiciona ninguna lógica de clasificación existente.

### 4. `query_handler.py` — `Clients` gana cliente + API key de Finnhub; wiring del closure

```python
@dataclass
class Clients:
    fmp_http: httpx.AsyncClient
    fred_http: httpx.AsyncClient
    treasury_gov_http: httpx.AsyncClient
    fmp_api_key: str
    fred_api_key: Optional[str]
    finnhub_http: httpx.AsyncClient        # NUEVO
    finnhub_api_key: Optional[str]         # NUEVO — opcional, gate de la Decisión #1
```

```python
async def _get_finnhub_peers(peer_ticker_query: str) -> list[str]:
    try:
        return await finnhub_client.get_peers(
            clients.finnhub_http, clients.finnhub_api_key, peer_ticker_query,
            grouping="subIndustry",
        )
    except finnhub_client.FinnhubError:
        return []

peer_result = await peers.get_peer_pe_average(
    get_peer_metrics_fn=_get_metrics_for_peer,
    sector=sector,
    own_ticker=ticker,
    get_dynamic_peers_fn=_get_finnhub_peers if clients.finnhub_api_key else None,
)
```

**Si `clients.finnhub_api_key` es `None`/vacío, ni siquiera se intenta la llamada** (`get_dynamic_peers_fn=None` directo) — mismo patrón que `treasury_client.get_treasury_yield` con `fred_api_key` (si no está seteada, ni lo intenta, pasa directo al fallback).

`peer_comparison_dict` (línea 278-288) gana la clave `"fuente_peers": peer_comparison_result.fuente_peers`.

### 5. `bot.py`/`.env.example` — nueva env var opcional `FINNHUB_API_KEY`

```python
# bot.py::main()
finnhub_api_key = os.environ.get("FINNHUB_API_KEY")  # Optional[str], no aborta si falta
...
clients = query_handler.Clients(
    ...,
    finnhub_http=httpx.AsyncClient(),
    finnhub_api_key=finnhub_api_key,
)
```

`.env.example` gana:

```
# API key OPCIONAL de Finnhub (https://finnhub.io) para peers dinámicos por
# sector/sub-industria. Si se deja vacía, el bot usa automáticamente la lista
# fija de respaldo (peers.py::PEERS_BY_SECTOR) — nunca falla por esto.
# Free tier: 60 llamadas/minuto, sin tarjeta. Ver README.md.
FINNHUB_API_KEY=
```

### 6. `summary.py` — `peers_note` deja de ser un texto estático único, depende de `fuente_peers`

**Decisión clave para no romper el test existente** (`test_peers_note_default_menciona_eleccion_manual_y_no_fmp`, `tests/test_summary.py` línea 608): el texto actual ("...fija... elegida a mano... no la arma FMP...") sigue siendo **literalmente cierto** cuando `fuente_peers` es `PEERS_FUENTE_FIJO` (o no viene informado, para compatibilidad con llamadores viejos) — la lista fija SIGUE siendo elegida a mano, solo que ahora es el respaldo en vez de la única fuente. Solo cuando `fuente_peers == PEERS_FUENTE_FINNHUB` el texto cambia.

```python
def _build_peers_note(fuente_peers: Optional[str]) -> str:
    if fuente_peers == peers.PEERS_FUENTE_FINNHUB:
        return (
            "Esta consulta, la lista de comparables (peers) se obtuvo "
            "dinámicamente de Finnhub (agrupados por sub-industria, no por "
            "el sector completo) — no es la lista fija de peers.py. Si "
            "Finnhub no responde o no está configurado, el bot usa "
            "automáticamente un respaldo fijo elegido a mano por quien "
            "construyó el bot."
        )
    return (
        "PER promedio de un set fijo de comparables, no del sector completo. "
        "Esta lista de comparables (peers) por sector es fija y fue elegida a "
        "mano por quien construyó el bot (ver peers.py, diccionario "
        "PEERS_BY_SECTOR) — no la arma FMP, ni la elige ningún algoritmo "
        "dinámico, ni se actualiza sola; si una empresa deja de ser un buen "
        "comparable, hay que cambiarla manualmente en el código. (Si Finnhub "
        "está configurado y respondió, esta consulta usa sus peers dinámicos "
        "en su lugar — ver arriba si corresponde.)"
    )
```

`build_summary_parts`/`build_summary` cambian el parámetro `peers_note: str = "<texto largo>"` a `peers_note: Optional[str] = None` (sigue siendo *keyword-only*, no rompe ningún llamador posicional). Si se pasa explícito, se usa tal cual (compat total con tests/llamadores existentes que sí lo pasan). Si es `None`, se computa con `_build_peers_note(peer_comparison.get("fuente_peers"))`. **`query_handler.py` no pasa `peers_note` explícito** — deja que se compute solo a partir de `peer_comparison_dict["fuente_peers"]`, que sí propaga correctamente.

**Nota sobre el texto "fijo" actualizado**: se agregó la frase final entre paréntesis para que el texto por-defecto siga siendo preciso incluso cuando Finnhub SÍ está configurado pero falló *esta* consulta puntual — evita que el texto suene a "Finnhub nunca se usa" cuando en realidad es la fuente primaria activa.

### 7. `peers.py` — docstring de módulo actualizado (ya no es "la única fuente")

El docstring de `peers.py` (líneas 1-6) se actualiza para reflejar que `PEERS_BY_SECTOR` es ahora el **respaldo**, no la fuente primaria — sin borrar la explicación de por qué existe la lista fija (sigue siendo relevante para el caso de fallback).

## Preguntas abiertas — Parte 1

**Ninguna.** Las 3 preguntas bloqueantes que dejó esta parte (grouping de Finnhub; mínimo de peers dinámicos antes de aceptarlos; tope de candidatos consultados contra FMP) fueron resueltas explícitamente por Daniela — ver "Resolución de las preguntas bloqueantes" al principio de esta Parte 1. Quedan fijadas como:

- **1.1:** `grouping="subIndustry"` (constante, sin override configurable).
- **1.2:** `MIN_PEERS_DINAMICOS_PARA_USAR = 3`.
- **1.3:** `MAX_PEERS_DINAMICOS = 5`.

El spec de la Parte 1 queda completo y listo para `security` sin gating adicional.

## Criterios de aceptación — Parte 1

*(las Preguntas 1.1/1.2/1.3 ya están resueltas — los criterios de abajo usan directamente los valores fijados: `grouping="subIndustry"`, `MIN_PEERS_DINAMICOS_PARA_USAR=3`, `MAX_PEERS_DINAMICOS=5`)*

### `finnhub_client.py`
- [ ] `get_peers` nunca propaga `httpx.HTTPStatusError`/`TimeoutException`/`RequestError` crudos — siempre `FinnhubError` sanitizado (sin URL, sin API key en el mensaje).
- [ ] `get_peers` usa `params=` de `httpx`, nunca f-string/concatenación con el símbolo o la key.
- [ ] Respuesta no-lista, o lista con elementos no-string, no lanza `TypeError`/`AttributeError` — se descartan/filtran.
- [ ] Ningún log de `get_peers` incluye la API key (`token=...`) ni la URL completa con params.

### `peers.py`
- [ ] `PeerAverageResult` tiene el campo nuevo `fuente_peers: str`, default `PEERS_FUENTE_FIJO` — no rompe ninguna instanciación existente en tests.
- [ ] `get_peer_pe_average` sin `get_dynamic_peers_fn` (o `None`) se comporta byte-a-byte igual que hoy (test de regresión).
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn` que devuelve **exactamente 3, 4 o 5** tickers (después de filtrar el propio ticker) → usa esa lista, `fuente_peers == PEERS_FUENTE_FINNHUB`.
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn` que devuelve **1 o 2** tickers (después de filtrar el propio ticker) → **no** usa esa lista (por debajo de `MIN_PEERS_DINAMICOS_PARA_USAR=3`) — cae a `get_peers_for_sector`, `fuente_peers == PEERS_FUENTE_FIJO`. Test explícito para 1 y para 2, distinto del caso de `[]`.
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn` que devuelve `[]` → cae a `get_peers_for_sector`, `fuente_peers == PEERS_FUENTE_FIJO`.
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn` que devuelve **más de 5** tickers → se recorta a los primeros 5 (`MAX_PEERS_DINAMICOS`), sin excepción.
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn` que incluye el propio ticker en su respuesta → se filtra antes de contar contra el mínimo de 3 (test explícito: Finnhub devuelve 4 tickers incluyendo el propio → quedan 3 después del filtro → SÍ se acepta, límite exacto).
- [ ] `get_peer_pe_average` con `get_dynamic_peers_fn=None` (Finnhub no configurado) → mismo comportamiento que hoy, `fuente_peers == PEERS_FUENTE_FIJO`.
- [ ] El resto de la lógica (`peers_pe`/`peers_no_usados` con motivo) no cambia sea cual sea la fuente de candidatos — test de regresión reutilizando los casos ya existentes de la spec anterior, ahora también con `fuente_peers=FINNHUB`.

### `market_context.py` / `query_handler.py`
- [ ] `compare_to_peers` propaga `fuente_peers` en las 4 ramas de retorno.
- [ ] `peer_comparison_dict` incluye la clave `"fuente_peers"`.
- [ ] `Clients` acepta `finnhub_http`/`finnhub_api_key` sin romper la construcción existente en `bot.py` (test de regresión).
- [ ] Con `finnhub_api_key=None`, no se construye ningún closure que llame a Finnhub (test que confirma 0 llamadas a un mock de Finnhub cuando la key falta).

### `summary.py`
- [ ] `_build_peers_note(None)` y `_build_peers_note(peers.PEERS_FUENTE_FIJO)` devuelven el mismo texto de siempre (contiene "elegida a mano" y "no la arma FMP") — no rompe `test_peers_note_default_menciona_eleccion_manual_y_no_fmp`.
- [ ] `_build_peers_note(peers.PEERS_FUENTE_FINNHUB)` menciona explícitamente "Finnhub" y "sub-industria" (grouping confirmado, Pregunta 1.1), y NO contiene "elegida a mano" (para no sonar contradictorio con la fuente real usada esta consulta).
- [ ] `peers_note` explícito (pasado por el llamador) sigue teniendo prioridad total sobre el cálculo automático — test de regresión.

## Artefactos a crear/modificar — Parte 1

- `src/investbot/finnhub_client.py` → **nuevo archivo**, wrapper HTTP (`FinnhubError`, `get_peers`).
- `src/investbot/peers.py` → `PEERS_FUENTE_FINNHUB`/`PEERS_FUENTE_FIJO`/`MAX_PEERS_DINAMICOS`; `PeerAverageResult` (campo `fuente_peers`); `get_peer_pe_average` (parámetro `get_dynamic_peers_fn`); docstring de módulo actualizado.
- `src/investbot/market_context.py` → `PeerComparisonResult`/`compare_to_peers` (campo/parámetro `fuente_peers`).
- `src/investbot/query_handler.py` → `Clients` (2 campos nuevos); closure `_get_finnhub_peers`; wiring a `get_peer_pe_average`; `peer_comparison_dict` (clave nueva).
- `src/investbot/bot.py` → lectura de `FINNHUB_API_KEY` (opcional, sin `sys.exit` si falta), construcción de `Clients` con los 2 campos nuevos.
- `src/investbot/summary.py` → `_build_peers_note`; `peers_note: Optional[str] = None` en `build_summary_parts`/`build_summary`.
- `.env.example` → `FINNHUB_API_KEY` (opcional, comentada).
- `README.md` → nueva sección "Fuente de peers dinámicos (Finnhub)" (mismo nivel de transparencia que FRED/Treasury.gov); actualización de la fila "Peers para el modelo de Múltiplos" en la tabla de presupuesto (confirmar que "3-5" ahora es real, no aspiracional); nota en la sección de `peers.py`/`PEERS_BY_SECTOR` aclarando que pasó a ser el respaldo.
- `tests/test_finnhub_client.py` → **nuevo archivo**, tests aislados del wrapper (mock de `httpx.MockTransport`, sin red real, mismo patrón que `tests/test_fmp_client.py`/`tests/test_treasury_client.py`).
- `tests/test_peers.py` → casos nuevos con `get_dynamic_peers_fn` (lista no vacía, lista vacía, `None`).
- `tests/test_market_context.py` → propagación de `fuente_peers`.
- `tests/test_query_handler.py` → `Clients` con Finnhub, closure gateado por `finnhub_api_key`.
- `tests/test_summary.py` → `_build_peers_note` para ambas fuentes.

## Restricciones — Parte 1

- **Ninguna fórmula de PER/Múltiplos cambia.** El cálculo de PER por peer (`1/earningsYield`) y el promedio/mínimo/máximo son idénticos sea cual sea la fuente de candidatos.
- **La lista fija de `peers.py` NO se elimina** — pasa a ser el respaldo, sigue existiendo tal cual, mantenimiento manual sin cambios.
- **Ningún fallo de Finnhub es visible para Daniela como error** — siempre fallback silencioso, nunca un mensaje de "Finnhub falló" en el chat de Telegram (la única señal es la nota de transparencia, que ya de por sí distingue la fuente).
- **No se combina la lista de Finnhub con la lista fija en la misma consulta** (es una u otra, nunca una unión) — mantiene la semántica simple de "una sola fuente por consulta", evita mezclar 2 metodologías de clasificación de comparables (GICS de Finnhub vs. curación manual) en un mismo promedio.
- **`grouping` fijo en `"subIndustry"` y `MIN_PEERS_DINAMICOS_PARA_USAR`/`MAX_PEERS_DINAMICOS` fijos en `3`/`5`** (Preguntas 1.1/1.2/1.3, resueltas) — no son parámetros que `implementer` deba dejar configurables ni ajustar por conveniencia; cambiarlos requiere una spec patch con acuerdo explícito de Daniela.

---

# PARTE 2 — Eventos corporativos relevantes (SEC EDGAR)

## Resolución de las preguntas bloqueantes (2026-07-29)

Daniela resolvió 2 de las 3 preguntas bloqueantes que dejó abiertas la versión anterior de esta spec (la tercera, 2.2, ya había quedado marcada como no bloqueante — ver abajo). Quedan **cerradas**, no se reabren:

- **2.1 — cantidad/ventana de eventos corporativos:** **5 eventos / últimos 180 días** — la recomendación del `architect`, confirmada.
- **2.3 — `SEC_EDGAR_USER_AGENT`:** **`"InvestBot ydva1998@gmail.com"`**, provisto por Daniela — pero **ese valor real NO va en `.env.example`** (corrección post-`security`, ver nota abajo). Solo va en el `.env` local del VPS.
- **2.2 — redacción de la etiqueta de Item 8.01:** no bloqueante desde el planteo original — queda la redacción ya propuesta por el `architect` (con la aclaración de incertidumbre), sin necesidad de confirmación aparte de Daniela.

**Corrección post-resolución (hallazgo bloqueante de `security`, 2026-07-29):** el repo de GitHub de este proyecto es **público** y `.env.example` está trackeado en git. `security` marcó como bloqueante dejar el email real de Daniela en `.env.example` (exposición permanente e indexable en un repo público). Daniela aceptó la corrección: `SEC_EDGAR_USER_AGENT` queda **vacío** en `.env.example` (con el formato requerido documentado en un comentario), igual que el resto de las variables del archivo — el valor real se completa únicamente en el `.env` local (gitignored, `chmod 600`). Ver Decisión #4 para el detalle. Esto no cambia nada del comportamiento en producción ni de ninguna otra decisión de la Parte 2.

Con estas resoluciones, **no queda ninguna pregunta bloqueante pendiente en la Parte 2**, y el hallazgo de `security` sobre esta pregunta ya está aplicado.

## Contexto

Daniela quiere ver algo sobre eventos corporativos recientes: cambios de gerencia, contratos importantes, litigios. La primera fuente evaluada (Finnhub "Major Developments") es casi seguro de pago — nombrada explícitamente en el mismo GitHub issue `#271` como movida a premium. Se descarta sin más investigación (evidencia directa, no un antecedente genérico).

En su lugar, **SEC EDGAR** — la fuente oficial y legal de EEUU para toda empresa pública listada ahí. Cuando ocurre un evento material (cambio de directivos/ejecutivos, contrato material, litigio relevante, bancarrota, etc.), la empresa está **obligada por ley** a presentar un formulario **8-K** ("Current Report") dentro de los días siguientes al evento. Es la fuente más confiable posible para este tipo de contenido — no depende de que un tercero decida seguir ofreciéndola gratis, es una obligación regulatoria pública.

**Endpoints usados, ambos gratis, sin API key, sin login:**
- `https://www.sec.gov/files/company_tickers.json` — mapeo completo ticker → CIK (identificador de la SEC), archivo estático que se puede cachear.
- `https://data.sec.gov/submissions/CIK{cik10digitos}.json` — historial de filings de una empresa, incluye 8-K con fecha y (en filings recientes) los códigos de "Item" del evento.

**Verifiqué en `tests/fixtures/adobe/profile.json` y en `fmp_client.py::get_profile` que FMP NO devuelve un campo `cik`** — no hay atajo, hace falta el mapeo propio vía `company_tickers.json`.

**Rate limit: 10 req/seg por IP, sin límite diario** — muy por encima de cualquier uso de un bot personal de un solo usuario. **Requiere header `User-Agent` con contacto identificable** — no es opcional, es un requisito de acceso justo de la SEC (bloquea/limita IPs que mandan un User-Agent genérico o vacío); necesitaba un valor real de Daniela (Pregunta 2.3, resuelta: `"InvestBot ydva1998@gmail.com"`).

**Decisión explícita y justificada (pedida por Daniela, no dada por sentada): NO se resume el contenido del 8-K con NLP/LLM.** El contenido completo de un 8-K es un documento legal — a veces un párrafo, a veces varias páginas con anexos — y resumirlo automáticamente agregaría: (a) complejidad nueva fuera del alcance actual del bot (no hay ningún componente de NLP/LLM en todo el proyecto hoy), (b) riesgo real de alucinación sobre texto legal (un resumen incorrecto de un litigio o un cambio de gerencia es peor que no mostrar nada), (c) un costo de mantenimiento (parsers de HTML/XBRL de filings reales, que varían mucho de forma entre empresas). **Lo realista y lo que se implementa:** fecha + tipo de evento (Item) + link directo al filing para que Daniela lo lea ella misma si le interesa. Trade-off aceptado explícitamente: el bot informa "pasó algo, andá a mirarlo" en vez de "esto es lo que pasó" — es menos cómodo, pero no arriesga inventar contenido sobre un documento legal real.

## Estado actual

- No existe ningún cliente ni módulo de SEC EDGAR en el proyecto. `fmp_client.py::get_profile` no expone `cik` (confirmado por inspección de `tests/fixtures/adobe/profile.json` y del código real de `get_profile`, líneas 156-167 de `fmp_client.py`).
- `src/investbot/summary.py::build_summary_parts` (líneas 519-649): el orden actual de secciones es Título → Veredicto → Intro → Ratios clave → Extras (ROE/deuda/dividendos) → Valor Justo → Pilares → Contexto de mercado (momentum + peers + VIX) → Encaje de riesgo → Notas de transparencia. No hay ningún lugar para "eventos corporativos".
- La última línea de `transparency_lines` (líneas 629-635) dice explícitamente hoy: *"Esto es una síntesis de datos financieros históricos... No tiene en cuenta noticias, eventos recientes, cambios de gestión ni el contexto cualitativo del negocio — revisá eso vos antes de decidir."* — **este texto se vuelve parcialmente desactualizado** en cuanto el bot sí muestre eventos corporativos oficiales (aunque sea solo fecha+tipo+link, sin resumen) — necesita un ajuste de una frase (Decisión #7).
- `src/investbot/query_handler.py::Clients` (líneas 71-79) no tiene cliente HTTP ni configuración para SEC EDGAR.
- `.env.example` no tiene ninguna variable relacionada.

## Estado objetivo

1. Nuevo módulo `src/investbot/sec_edgar_client.py` — wrapper HTTP fino (mismo patrón de seguridad que los demás clientes): `get_cik_for_ticker` (con caché en memoria del mapeo completo, TTL 24h) y `get_submissions` (historial de filings de un CIK). Siempre envía el header `User-Agent` configurado.
2. Nuevo módulo puro `src/investbot/corporate_events.py` — sin I/O, mismo patrón que `market_context.py`: `RELEVANT_8K_ITEMS` (mapeo Item→etiqueta dummy), `CorporateEvent` (dataclass), `extract_relevant_8k_events(submissions, cik10, *, max_events, since_days)` que filtra 8-Ks recientes con al menos 1 Item relevante y arma la URL pública del filing.
3. Nueva env var **obligatoria para activar esta feature** `SEC_EDGAR_USER_AGENT` (formato `"<nombre/app> <email de contacto>"`, requisito de la SEC). Si está vacía, la feature completa se omite (sin llamar nunca a SEC, sin crashear) — mismo patrón de "si no está configurado, no se intenta" que Finnhub.
4. `summary.py` gana `build_corporate_events_section` — nueva sección, ubicada **después de "Contexto de mercado" y antes de "Encaje de riesgo"** (ver Decisión #6 para la justificación de ubicación), omitida por completo si no hay eventos relevantes (sin ruido tipo "no hay eventos").
5. La nota final de transparencia se ajusta una frase para dejar de decir que el bot "no tiene en cuenta... eventos recientes... cambios de gestión" de forma absoluta — ahora aclara que sí lista eventos oficiales de SEC EDGAR (sin resumir su contenido).
6. Cero llamadas HTTP nuevas contra FMP — SEC EDGAR es un proveedor completamente aparte (igual que FRED/Treasury.gov/Finnhub), no consume el presupuesto de 250 req/día.

## Decisiones de diseño tomadas

### 1. `sec_edgar_client.py` — caché en memoria del mapeo ticker→CIK + fetch de submissions

```python
"""Wrapper HTTP a SEC EDGAR (data.sec.gov / www.sec.gov) — fuente oficial y
gratuita de filings públicos (Feature 2: eventos corporativos vía 8-K).

Requiere SIEMPRE un header User-Agent con contacto identificable (requisito
de acceso justo de la SEC, no opcional) — ver SEC_EDGAR_USER_AGENT en
.env.example. Sin esta variable, la feature completa se omite (nunca se
manda una request sin el header configurado).

Rate limit documentado: 10 req/seg por IP, SIN límite diario (a diferencia
de FMP). No consume el presupuesto de 250 req/día de FMP — proveedor
distinto, mismo criterio que FRED/Treasury.gov/Finnhub.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
DEFAULT_TIMEOUT = 10.0
_CACHE_TTL_HOURS = 24  # mapeo ticker->CIK cambia muy poco; evita re-descargar
# un archivo de cientos de KB en cada consulta (mismo espíritu que el estado
# en memoria ya usado por security.InMemoryRateLimiter — proceso único, sin
# necesidad de un lock: en el peor caso 2 consultas simultáneas refrescan el
# caché 2 veces, sin efecto negativo).

_ticker_cik_cache: dict[str, str] = {}
_cache_loaded_at: Optional[datetime] = None


class SecEdgarError(Exception):
    """Error sanitizado de SEC EDGAR — nunca incluye la URL cruda."""


async def _get_json(client: httpx.AsyncClient, url: str, user_agent: str) -> Any:
    try:
        response = await client.get(
            url, headers={"User-Agent": user_agent}, timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("SEC EDGAR timeout — url=%s", url)
        raise SecEdgarError("SEC EDGAR no respondió a tiempo.")
    except httpx.HTTPStatusError as exc:
        logger.warning("SEC EDGAR error HTTP — url=%s status=%s", url, exc.response.status_code)
        raise SecEdgarError(f"SEC EDGAR respondió con un error ({exc.response.status_code}).")
    except httpx.RequestError:
        logger.warning("SEC EDGAR error de red — url=%s", url)
        raise SecEdgarError("No pude conectarme a SEC EDGAR ahora mismo.")
    try:
        return response.json()
    except ValueError:
        logger.warning("SEC EDGAR respuesta no-JSON — url=%s", url)
        raise SecEdgarError("SEC EDGAR devolvió una respuesta inesperada.")


async def _ensure_ticker_cache(client: httpx.AsyncClient, user_agent: str) -> None:
    global _cache_loaded_at
    now = datetime.now(timezone.utc)
    if _cache_loaded_at and (now - _cache_loaded_at) < timedelta(hours=_CACHE_TTL_HOURS):
        return
    data = await _get_json(client, TICKERS_URL, user_agent)
    if not isinstance(data, dict):
        raise SecEdgarError("SEC EDGAR devolvió un formato inesperado para el mapeo de tickers.")
    nuevo_cache: dict[str, str] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if isinstance(ticker, str) and cik is not None:
            nuevo_cache[ticker.upper()] = str(cik).zfill(10)
    _ticker_cik_cache.clear()
    _ticker_cik_cache.update(nuevo_cache)
    _cache_loaded_at = now


async def get_cik_for_ticker(
    client: httpx.AsyncClient, user_agent: str, ticker: str
) -> Optional[str]:
    """CIK de 10 dígitos (con ceros a la izquierda) para `ticker`, o `None`
    si no se encuentra o si SEC EDGAR falla (nunca lanza SecEdgarError hacia
    afuera — best-effort, mismo criterio que own_metrics/VIX en query_handler.py)."""
    try:
        await _ensure_ticker_cache(client, user_agent)
    except SecEdgarError:
        return None
    return _ticker_cik_cache.get(ticker.upper())


async def get_submissions(
    client: httpx.AsyncClient, user_agent: str, cik10: str
) -> Optional[dict]:
    """Historial de filings de `cik10`. `None` si SEC EDGAR falla (best-effort,
    no lanza hacia afuera — mismo criterio que get_cik_for_ticker)."""
    try:
        data = await _get_json(client, SUBMISSIONS_URL.format(cik10=cik10), user_agent)
    except SecEdgarError:
        return None
    return data if isinstance(data, dict) else None
```

**Nota de diseño:** a diferencia de `fmp_client.py`/`finnhub_client.py` (que lanzan su error sanitizado y dejan que el llamador decida qué hacer), `get_cik_for_ticker`/`get_submissions` devuelven directamente `None` en vez de lanzar — mismo patrón *best-effort* ya usado en `query_handler.py` para `own_metrics`/`vix_quote` (try/except alrededor de la llamada a `fmp_client`), pero movido adentro del cliente esta vez porque hay 2 llamadas encadenadas (CIK → submissions) y el punto de "no bloquear el resto del análisis" es más simple de garantizar acá que repetido 2 veces en `query_handler.py`.

**El caché en memoria de módulo (`_ticker_cik_cache`/`_cache_loaded_at`) es estado mutable global** — mismo patrón ya aceptado en este proyecto (`security.InMemoryRateLimiter`), justificado por ser un proceso único de un solo usuario, sin necesidad de invalidación entre workers.

### 2. `corporate_events.py` — módulo puro, sin I/O

```python
"""Extracción de eventos corporativos relevantes desde un historial de
filings de SEC EDGAR ya resuelto (Feature 2). Función pura, sin I/O — mismo
estándar que market_context.py: recibe datos ya obtenidos por
sec_edgar_client.py como parámetros.

NO resume el contenido del filing (decisión explícita, ver spec) — solo
extrae fecha + Item + URL pública del documento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# Items de un 8-K relevantes al pedido de Daniela (cambios de gerencia,
# contratos importantes, litigios) — clasificación estándar de la SEC
# (Regulation S-K / Form 8-K, estructura pública y estable desde la reforma
# de 2004), confirmada por investigación, no improvisada:
RELEVANT_8K_ITEMS: dict[str, str] = {
    "1.01": "Nuevo contrato importante",
    "1.02": "Terminación de un contrato importante",
    "1.03": "Posible bancarrota o reorganización",
    "5.02": "Cambio de directivos o ejecutivos",
    "8.01": "Otro evento relevante (puede incluir litigios/demandas — la SEC "
            "no exige reportar litigios en un Item propio salvo casos "
            "extremos, así que a veces caen acá y a veces no se reportan "
            "vía 8-K en absoluto)",
}
# NO se agrega ningún Item fuera de estos 5 sin acuerdo explícito de
# Daniela (ej. 2.01 "Acquisition/Disposition of Assets" queda afuera —
# no lo pidió, sería agregar alcance no acordado).


@dataclass
class CorporateEvent:
    filing_date: str  # "YYYY-MM-DD", tal como lo devuelve SEC EDGAR
    items: list[str]
    labels: list[str]
    filing_url: str


def _build_filing_url(cik10: str, accession_number: str, primary_document: str) -> Optional[str]:
    if not cik10 or not accession_number or not primary_document:
        return None
    try:
        cik_sin_ceros = str(int(cik10))
    except (TypeError, ValueError):
        return None
    accession_sin_guiones = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_sin_ceros}/"
        f"{accession_sin_guiones}/{primary_document}"
    )


def extract_relevant_8k_events(
    submissions: Optional[dict],
    cik10: Optional[str],
    *,
    max_events: int = 5,     # Pregunta 2.1, RESUELTA por Daniela: 5
    since_days: int = 180,   # Pregunta 2.1, RESUELTA por Daniela: 180 días
) -> list[CorporateEvent]:
    """Nunca lanza excepción — cualquier forma inesperada del JSON de SEC
    EDGAR (campo ausente, arrays de distinto largo, fecha no parseable)
    hace que esa entrada puntual se descarte, nunca que la función falle
    (mismo criterio B1-B4 del resto del proyecto: guardas de tipo, nunca
    asumir la forma exacta del payload sin verificarlo con curl real —
    mismo patrón que Resolución (a) de SDD_contenido_financiero_explicado).
    """
    if not submissions or not cik10:
        return []
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    items_raw = recent.get("items") or []

    n = min(len(forms), len(dates), len(accessions), len(docs), len(items_raw))
    cutoff = date.today() - timedelta(days=since_days)
    events: list[CorporateEvent] = []

    for i in range(n):
        if forms[i] != "8-K":
            continue
        try:
            filing_date = date.fromisoformat(dates[i])
        except (ValueError, TypeError):
            continue
        if filing_date < cutoff:
            continue

        codigos_raw = items_raw[i] if isinstance(items_raw[i], str) else ""
        codigos = [c.strip() for c in codigos_raw.split(",") if c.strip()]
        matched = [c for c in codigos if c in RELEVANT_8K_ITEMS]
        if not matched:
            continue

        url = _build_filing_url(cik10, accessions[i], docs[i])
        if url is None:
            continue

        events.append(
            CorporateEvent(
                filing_date=dates[i],
                items=matched,
                labels=[RELEVANT_8K_ITEMS[c] for c in matched],
                filing_url=url,
            )
        )
        if len(events) >= max_events:
            break

    return events
```

**No verificado con `curl` real que el campo `"items"` exista literalmente con ese nombre en `filings.recent`** — Daniela ya describió esta estructura en su investigación previa ("en filings recientes, los códigos de Item del evento"), y el diseño de arriba asume ese nombre de campo pero se degrada con gracia (lista vacía, nunca crashea) si el campo no existe, viene vacío, o tiene una forma distinta — mismo criterio de "Resolución (a)" ya aceptado por Daniela para otros campos de FMP: no se bloquea el diseño con verificación manual previa.

### 3. `query_handler.py` — wiring *best-effort*, no bloquea el resto del análisis

```python
# Clients gana:
sec_edgar_http: httpx.AsyncClient
sec_edgar_user_agent: Optional[str]
```

```python
corporate_events_list: list[dict] = []
if clients.sec_edgar_user_agent:
    cik10 = await sec_edgar_client.get_cik_for_ticker(
        clients.sec_edgar_http, clients.sec_edgar_user_agent, ticker
    )
    submissions = (
        await sec_edgar_client.get_submissions(
            clients.sec_edgar_http, clients.sec_edgar_user_agent, cik10
        )
        if cik10 else None
    )
    events = corporate_events.extract_relevant_8k_events(submissions, cik10)
    corporate_events_list = [
        {"filing_date": e.filing_date, "labels": e.labels, "filing_url": e.filing_url}
        for e in events
    ]
# Si sec_edgar_user_agent no está configurado, corporate_events_list queda
# [] sin ninguna llamada de red intentada — mismo patrón que Finnhub sin key.
```

**Deliberadamente no participa del abort-check existente** (mismo principio que `own_metrics`/VIX): si SEC EDGAR falla, no responde, o el ticker no tiene CIK (ej. algunos ADRs/emisores extranjeros que no presentan 8-K, presentan 6-K en su lugar, formato distinto no cubierto por esta spec), el resto del análisis sigue exactamente igual, la sección se omite sin ruido.

### 4. `.env.example` — nueva env var, **requerida para activar esta feature específica** (el bot entero sigue arrancando sin ella)

Pregunta 2.3, **RESUELTA**: Daniela proveyó el contacto real a usar (`"InvestBot ydva1998@gmail.com"`) — **corregido tras hallazgo bloqueante de `security`** (ver nota abajo): ese valor real **NO** va en `.env.example`.

```
# User-Agent OBLIGATORIO para llamar a SEC EDGAR (Feature 2: eventos
# corporativos vía 8-K). La SEC exige identificar el bot/app + un contacto
# real (email) en cada request -- si se deja vacía, el bot funciona igual
# pero esta sección se omite siempre (nunca se llama a SEC EDGAR sin este
# header). Formato: "<nombre/app> <email-de-contacto>", ej.
# "InvestBot contacto@ejemplo.com"
SEC_EDGAR_USER_AGENT=
```

**Corrección de `security` (hallazgo bloqueante, aplicado):** el repo de GitHub de este proyecto es **público** y `.env.example` está trackeado en git — dejar el email real de Daniela ahí lo expondría de forma permanente en un repo público (indexable, imposible de despublicar retroactivamente), sin importar que el propio valor no sea "secreto" desde el punto de vista de la política de acceso de la SEC. `SEC_EDGAR_USER_AGENT` queda **vacío** en `.env.example`, con solo el formato requerido documentado en el comentario — exactamente el mismo patrón que ya siguen **todas** las demás variables del archivo (`TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, `FRED_API_KEY`, `FINNHUB_API_KEY`), sin excepción. El valor real (`"InvestBot ydva1998@gmail.com"`) lo completa Daniela **solo en su `.env` local del VPS** (gitignored, `chmod 600`, mismo patrón ya establecido para el resto de las variables). La SEC sigue recibiendo el header igual una vez que Daniela complete su `.env` real — este cambio es puramente sobre qué queda commiteado en git, no sobre el comportamiento en producción.

### 5. `summary.py` — nueva sección, omitida si no hay eventos

```python
def build_corporate_events_section(events: list[dict]) -> Optional[str]:
    """Sección "Eventos corporativos recientes (SEC EDGAR)". Se omite por
    completo (retorna None) si `events` está vacía -- mismo criterio de
    "degradar con gracia sin ruido" que build_extras_section/bullet de VIX."""
    if not events:
        return None
    lines = ["*Eventos corporativos recientes (SEC EDGAR):*"]
    for ev in events:
        etiquetas = " + ".join(ev["labels"])
        lines.append(f"- {ev['filing_date']}: {etiquetas} — [ver el filing]({ev['filing_url']})")
    lines.append(
        "  _Fuente: SEC EDGAR (oficial, gratis, sin API key) — formularios "
        "8-K que la empresa está obligada a presentar por ley ante eventos "
        "materiales. El bot NO resume el contenido legal del filing (fuera "
        "de alcance, riesgo de alucinación sobre texto legal) — mostramos "
        "fecha + tipo de evento + link para que lo leas vos si te interesa._"
    )
    return "\n".join(lines)
```

### 6. Ubicación en `build_summary_parts` — después de "Contexto de mercado", antes de "Encaje de riesgo"

Justificación (pedida explícitamente por Daniela, no una elección arbitraria): "Contexto de mercado" ya agrupa todo lo que rodea al ticker más allá de sus propios estados financieros — momentum de precio, comparación con peers, VIX (volatilidad de mercado). "Eventos corporativos" extiende exactamente ese mismo eje ("qué está pasando alrededor de esta empresa, más allá del balance") pero desde el ángulo regulatorio/cualitativo en vez del ángulo de precio de mercado — agruparlos consecutivos mantiene juntas las 2 secciones de "contexto externo" antes de pasar a "Encaje de riesgo" (que es sobre el perfil personal de Daniela, un eje distinto: ya no es sobre la empresa, es sobre cómo encaja con ella). Alternativa considerada y descartada: pegarlo justo antes de "Notas de transparencia" al final — se descarta porque enterraría contenido potencialmente importante (un cambio de CEO, una demanda) al final del mensaje, después de toda la data cuantitativa, cuando Daniela pidió que fuera visible, no un anexo.

```python
market_context_section = build_market_context_section(...)
corporate_events_section = build_corporate_events_section(corporate_events)  # NUEVO
risk_section = build_risk_fit_section(risk_fit)
...
parts = [
    titulo, veredicto_section, intro, "\n".join(ratios_lines),
    extras_section, valuation_section, pillars_section,
    market_context_section,
    corporate_events_section,  # NUEVO — None si no hay eventos, se filtra solo
    risk_section,
    "\n".join(transparency_lines),
]
```

`build_summary_parts`/`build_summary` ganan el parámetro `corporate_events: Optional[list[dict]] = None` (default para no romper firma de llamadores/tests existentes).

### 7. Ajuste de la nota final de transparencia (ya no puede decir "no tiene en cuenta... eventos recientes" de forma absoluta)

Reemplaza (texto actual, `summary.py` líneas 629-635):

> *"Esto es una síntesis de datos financieros históricos, no asesoramiento financiero profesional ni una recomendación de inversión. No tiene en cuenta noticias, eventos recientes, cambios de gestión ni el contexto cualitativo del negocio — revisá eso vos antes de decidir."*

por:

```python
transparency_lines.append(
    "_Esto es una síntesis de datos financieros históricos, no "
    "asesoramiento financiero profesional ni una recomendación de "
    "inversión. No incluye análisis de noticias ni del contexto "
    "cualitativo del negocio más allá de los eventos corporativos "
    "oficiales de SEC EDGAR listados arriba (si los hay) — y esos se "
    "muestran sin resumir, no reemplazan leer el filing completo. "
    "Revisá vos el resto del contexto cualitativo antes de decidir._"
)
```

**Ubicación sin cambios** (sigue siendo la última línea de `transparency_lines`) — solo cambia el contenido del texto.

## Preguntas abiertas — Parte 2

**Ninguna bloqueante.** Las 2 preguntas bloqueantes que dejó esta parte fueron resueltas explícitamente por Daniela — ver "Resolución de las preguntas bloqueantes" al principio de esta Parte 2:

- **2.1 (cantidad/ventana de eventos):** `max_events=5`, `since_days=180` — fijados, ver Decisión #2.
- **2.3 (`SEC_EDGAR_USER_AGENT`):** valor real `"InvestBot ydva1998@gmail.com"` fijado, pero **solo para el `.env` local de Daniela** — `.env.example` queda vacío (corrección de `security`, hallazgo bloqueante ya aplicado, ver Decisión #4).

**2.2 (redacción de la etiqueta de Item 8.01) queda como no bloqueante**, tal como se dejó planteada desde el origen: la redacción propuesta por el `architect` (con la aclaración explícita de incertidumbre — "puede incluir litigios/demandas... no siempre") se usa tal cual, sin necesidad de una confirmación aparte de Daniela. Si más adelante, al ver el resultado real, Daniela prefiere ajustar el texto, es una corrección menor post-implementación (no requiere spec patch).

El spec de la Parte 2 queda completo, con el hallazgo bloqueante de `security` sobre `.env.example` ya aplicado, y listo para `qa` sin gating adicional.

## Criterios de aceptación — Parte 2

### `sec_edgar_client.py`
- [ ] `get_cik_for_ticker`/`get_submissions` nunca propagan excepciones crudas de `httpx` — devuelven `None` en cualquier fallo (timeout/HTTP error/red/JSON inesperado).
- [ ] Ningún log incluye el contenido completo de la respuesta ni información sensible (no aplica API key acá, pero sí se verifica que no se loguee de más).
- [ ] El header `User-Agent` se envía en **ambas** llamadas (tickers y submissions) — test que verifica el header en la request mockeada.
- [ ] El caché de ticker→CIK no vuelve a pedir `company_tickers.json` dentro del TTL (test con 2 llamadas seguidas, 1 sola request HTTP real al mock).
- [ ] Caché expirado (mock de tiempo > TTL) → vuelve a pedir el archivo.
- [ ] Ticker no encontrado en el mapeo → `None`, no excepción.

### `corporate_events.py`
- [ ] `extract_relevant_8k_events(None, None)` / `extract_relevant_8k_events(None, "0000320193")` / `extract_relevant_8k_events({...}, None)` → `[]`, nunca excepción.
- [ ] Filing con `form != "8-K"` se descarta (ej. 10-Q, 10-K, 4).
- [ ] Filing 8-K con `items` que no matchea ningún código de `RELEVANT_8K_ITEMS` (ej. solo "2.02", "9.01") → se descarta, no aparece como evento.
- [ ] Filing 8-K con `items="5.02,9.01"` (mixto, 1 relevante + 1 no relevante) → aparece como evento, con `items=["5.02"]` únicamente (9.01 no se lista, no es relevante).
- [ ] Filing 8-K con `items="1.01,5.02"` (2 relevantes en el mismo filing) → aparece como 1 solo evento con ambas etiquetas.
- [ ] Filing fuera de la ventana `since_days` → se descarta.
- [ ] Más de `max_events` filings relevantes → se recorta a `max_events`, sin excepción.
- [ ] Arrays de distinto largo en `filings.recent` (ej. `items` más corto que `form`) → no lanza `IndexError`, usa el mínimo común.
- [ ] `filingDate` no parseable como fecha ISO → esa entrada se descarta, no crashea el resto.
- [ ] `_build_filing_url` con cualquier argumento vacío/`None` → devuelve `None` (esa entrada se descarta en `extract_relevant_8k_events`, no genera un link roto).
- [ ] URL generada tiene el formato `https://www.sec.gov/Archives/edgar/data/{cik_sin_ceros}/{accession_sin_guiones}/{documento}` — test con un CIK con ceros a la izquierda y un accession number con guiones, verificando que ambos se normalizan correctamente.

### `query_handler.py`
- [ ] Con `sec_edgar_user_agent=None`/vacío → cero llamadas HTTP a un mock de SEC EDGAR (test explícito).
- [ ] Con `sec_edgar_user_agent` configurado pero `get_cik_for_ticker` devuelve `None` → `corporate_events_list == []`, resto del análisis sin cambios.
- [ ] Con `sec_edgar_user_agent` configurado y CIK+submissions exitosos → `corporate_events_list` poblada correctamente desde `extract_relevant_8k_events`.
- [ ] `fetch_and_analyze_parts` no cambia de firma pública — test de regresión de `test_fetch_and_analyze_adobe_end_to_end`.

### `summary.py`
- [ ] `build_corporate_events_section([])` → `None`.
- [ ] `build_corporate_events_section([{... 1 evento ...}])` → contiene la fecha, la(s) etiqueta(s), el link en formato Markdown, y la nota de fuente ("SEC EDGAR", "NO resume").
- [ ] `build_summary_parts` con `corporate_events=None` (default, llamador viejo) → no incluye la sección, ningún test existente se rompe.
- [ ] La sección de eventos corporativos aparece, en `build_summary_parts`, después de la sección de Contexto de mercado y antes de la de Encaje de riesgo — test de orden.
- [ ] La nota final de transparencia ya no contiene la frase absoluta "No tiene en cuenta... eventos recientes" — contiene en cambio la mención a SEC EDGAR sin resumir.

## Artefactos a crear/modificar — Parte 2

- `src/investbot/sec_edgar_client.py` → **nuevo archivo**.
- `src/investbot/corporate_events.py` → **nuevo archivo**.
- `src/investbot/query_handler.py` → `Clients` (2 campos nuevos); bloque *best-effort* de CIK+submissions+extracción; wiring a `summary.build_summary_parts`.
- `src/investbot/bot.py` → lectura de `SEC_EDGAR_USER_AGENT` (opcional, sin `sys.exit` si falta), construcción de `Clients`.
- `src/investbot/summary.py` → `build_corporate_events_section`; inserción en `build_summary_parts`/`build_summary` (parámetro + ubicación); ajuste de la nota final de transparencia.
- `.env.example` → `SEC_EDGAR_USER_AGENT`.
- `README.md` → nueva sección "Fuente de eventos corporativos (SEC EDGAR)" (mismo nivel de transparencia que FRED/Treasury.gov/Finnhub): endpoints usados, requisito de User-Agent, rate limit, aclaración de que no resume contenido legal.
- `tests/test_sec_edgar_client.py` → **nuevo archivo**.
- `tests/test_corporate_events.py` → **nuevo archivo**.
- `tests/test_query_handler.py` → casos nuevos de wiring *best-effort*.
- `tests/test_summary.py` → casos nuevos de `build_corporate_events_section` + ubicación + nota de transparencia ajustada.

## Restricciones — Parte 2

- **No se resume el contenido del 8-K con NLP/LLM** — decisión explícita con trade-off documentado (ver Contexto), no se reabre sin una spec nueva.
- **No se agregan Items más allá de los 5 confirmados** (`1.01`/`1.02`/`1.03`/`5.02`/`8.01`) sin acuerdo explícito de Daniela.
- **La feature completa se omite (sin crash, sin llamadas de red) si `SEC_EDGAR_USER_AGENT` no está configurada** — nunca un comportamiento "a medias" (ej. mandar la request sin header, arriesgando que la SEC bloquee la IP del VPS).
- **`max_events=5`/`since_days=180` (Pregunta 2.1, resuelta) son valores fijos**, no configurables por `implementer` — cambiarlos requiere una spec patch con acuerdo explícito de Daniela.
- **Ninguna fórmula de valoración ni pilar cambia** — esta feature es puramente informativa, no participa de ningún cálculo.

---

## Consolidado — Preguntas abiertas bloqueantes (ambas partes)

**Ninguna.** Las 5 preguntas bloqueantes de esta spec (1.1, 1.2, 1.3, 2.1, 2.3) fueron resueltas explícitamente por Daniela el 2026-07-29 — ver "Resolución de las preguntas bloqueantes" al principio de cada Parte. La única pregunta restante (2.2, redacción de la etiqueta del Item 8.01) fue clasificada desde el origen como no bloqueante, con la recomendación del `architect` ya aplicada directamente en el diseño.

| # | Pregunta | Resolución | Valor fijado |
|---|---|---|---|
| 1.1 | `grouping` de Finnhub | Resuelta por Daniela | `subIndustry` |
| 1.2 | Mínimo de peers dinámicos antes de aceptar la lista de Finnhub | Resuelta por Daniela | `MIN_PEERS_DINAMICOS_PARA_USAR = 3` |
| 1.3 | Tope de peers dinámicos a consultar contra FMP | Resuelta por Daniela | `MAX_PEERS_DINAMICOS = 5` |
| 2.1 | Cantidad máxima y ventana de tiempo de eventos corporativos | Resuelta por Daniela | `max_events=5`, `since_days=180` |
| 2.2 | Redacción de la etiqueta de Item 8.01 | No bloqueante desde el origen | Redacción ya fijada en la Decisión #2 de la Parte 2, sin cambios |
| 2.3 | Valor real de `SEC_EDGAR_USER_AGENT` | Resuelta por Daniela | `"InvestBot ydva1998@gmail.com"` — **solo en el `.env` local**, `.env.example` queda vacío (corrección de `security`, ver abajo) |

**El spec queda completo — `implementer` puede entrar a Ralph Loop en cuanto `qa` complete su paso del pipeline (Scope Freeze), sin ninguna pregunta pendiente de Daniela y sin ningún hallazgo de `security` sin resolver.**

---

## Resolución de hallazgos de `security` (2026-07-29)

`security` revisó esta spec y encontró **1 hallazgo bloqueante puntual**, ya resuelto:

- **Hallazgo:** el repo de GitHub de este proyecto es **público** (confirmado vía API de GitHub) y `.env.example` está trackeado en git. La Decisión #4 original de la Parte 2 dejaba el valor real de `SEC_EDGAR_USER_AGENT` (`"InvestBot ydva1998@gmail.com"`, el email personal de Daniela) escrito directamente en `.env.example` — eso lo hubiera expuesto de forma permanente en un repo público, indexable, imposible de despublicar retroactivamente aunque se corrigiera después.
- **Resolución:** Daniela aceptó la recomendación de `security`. `SEC_EDGAR_USER_AGENT` queda **vacío** en `.env.example`, con el formato requerido documentado en un comentario — mismo patrón que ya siguen, sin excepción, todas las demás variables del archivo (`TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`, `FRED_API_KEY`, `FINNHUB_API_KEY`). El valor real lo completa Daniela únicamente en su `.env` local del VPS (gitignored, `chmod 600`). Ver Decisión #4 de la Parte 2 (ya actualizada) para el detalle completo.
- **Sin impacto en ningún otro punto de la spec** — la SEC sigue recibiendo el header `User-Agent` exactamente igual una vez que Daniela complete su `.env` real; el cambio es puramente sobre qué queda commiteado en git, no sobre el comportamiento del bot en producción. No se reabre ninguna otra decisión de la Parte 1 ni de la Parte 2.

**No queda ningún hallazgo bloqueante de `security` sin resolver.** El spec está listo para pasar a `qa`.

---

## Handoff → qa

`security` ya revisó esta spec y encontró 1 hallazgo bloqueante puntual (`.env.example`/repo público, ver "Resolución de hallazgos de `security`" arriba) — ya resuelto y aplicado. El siguiente paso del pipeline es `qa` (agrega criterios de cobertura/testabilidad); este proyecto no tiene un paso de `frontend` separado (bot de solo texto sobre Telegram, sin UI propia — mismo patrón ya seguido por las specs anteriores de este proyecto).

### Specs producidas
- `contexto/specs/abiertas/SDD_peers_dinamicos_y_eventos_corporativos.md` (esta spec — 2 partes independientes, ambas con sus preguntas bloqueantes resueltas por Daniela, y el hallazgo bloqueante de `security` ya aplicado).

### Criterios de aceptación base
Ver "Criterios de aceptación — Parte 1" y "Criterios de aceptación — Parte 2" arriba.

### Decisiones de diseño tomadas (para que `qa`/`implementer` no las reabran)
- **2 proveedores HTTP nuevos, ninguno consume el presupuesto de 250 req/día de FMP** (Finnhub, SEC EDGAR) — mismo principio que FRED/Treasury.gov.
- **Ambos siguen el mismo patrón de seguridad ya auditado en `fmp_client.py`/`treasury_client.py`**: cliente inyectado, `params=`/headers explícitos (nunca f-string con datos de usuario en la URL), excepciones traducidas a un error propio sanitizado (nunca URL ni credenciales en el mensaje/log), solo se loguea endpoint/status.
- **Ambas features tienen fallback/omisión automática y silenciosa si la fuente no está configurada o falla** — Finnhub cae a la lista fija ya existente; SEC EDGAR omite la sección completa. Ninguna de las 2 puede crashear el análisis ni bloquear el resto de la respuesta.
- **Finnhub**: `finnhub_api_key` es un secreto (va como query param `token=`) — mismo tratamiento que `FMP_API_KEY`/`FRED_API_KEY` (solo en `.env`, `chmod 600`, nunca en git, nunca en logs).
- **SEC EDGAR**: `SEC_EDGAR_USER_AGENT` **queda vacío en `.env.example`** (corrección de `security`, hallazgo bloqueante ya resuelto — el repo es público, `.env.example` está trackeado en git). El comentario documenta solo el formato requerido por la SEC; el valor real (`"InvestBot ydva1998@gmail.com"`) va únicamente en el `.env` local del VPS de Daniela, mismo tratamiento que el resto de las variables del archivo — sin excepción, aunque el valor en sí no sea confidencial para la SEC.
- **Ninguna fórmula de valoración existente cambia** en ninguna de las 2 partes — ambas son aditivas/informativas, mismo principio que toda spec anterior de este proyecto.
- **Umbrales/topes fijados por Daniela** (`grouping="subIndustry"`, mínimo 3, tope 5 peers dinámicos, 5 eventos/180 días) — no son configurables ni ajustables por `implementer` por conveniencia; un cambio futuro requiere spec patch.
- **No queda ninguna pregunta bloqueante ni ningún hallazgo de `security` sin resolver** — `qa` puede agregar criterios de cobertura de punta a punta, e `implementer` puede arrancar Ralph Loop en cuanto el pipeline llegue a Scope Freeze.

---

## Criterios QA para Spec: Peers dinámicos (Finnhub) + Eventos corporativos (SEC EDGAR) [Iter-1]

**Rol:** `qa` (pre-implementación — Momento 1 del pipeline BMAD). Amplío la spec de `architect` (revisada sin hallazgos bloqueantes por `security`) — no la reescribo. Leída completa (912 líneas, ambas Partes) + código real de `src/investbot/peers.py`, `market_context.py`, `query_handler.py`, `summary.py`, `fmp_client.py`, `treasury_client.py`, `bot.py`, y las 5 suites de test relevantes completas: `tests/test_peers.py` (339 líneas), `tests/test_market_context.py` (312 líneas), `tests/test_query_handler.py` (1036 líneas), `tests/test_summary.py` (1107 líneas), `tests/test_treasury_client.py` (198 líneas) + `tests/test_bot.py` (76 líneas, relevante por `build_application`). Todas las citas de línea de este bloque están verificadas contra el código real, no contra el pseudocódigo del `architect` (que en algunos casos difiere levemente del archivo actual — sin impacto de fondo, solo drift normal de líneas).

**Corrida de baseline real antes de esta spec** (`pytest --cov=investbot --cov-branch --cov-report=term-missing` desde `src/`, `pythonpath=src` vía `pytest.ini`):

```
378 passed
Name                               Stmts   Miss Branch BrPart  Cover
------------------------------------------------------------------------------
src/investbot/peers.py                41      0      10      0   100%
src/investbot/market_context.py       64      0      20      0   100%
src/investbot/fmp_client.py           78      0      16      0   100%
src/investbot/treasury_client.py     101      3      20      2    96%   106->104, 111, 131-132
src/investbot/query_handler.py       255      6      46      2    97%   146, 160-161, 177-178, 483
src/investbot/summary.py             214      1      88      6    98%   200->202, 377, 575->577, 577->582, 587->591, 591->597
src/investbot/bot.py                  57     10      12      2    83%   58, 104-120, 124
TOTAL                                1266     20     350     12    98%
```

Esta cifra es la vara de comparación para el criterio de exit de `qa` (Momento 2): el `TOTAL` no debe bajar de 98%, y ningún módulo tocado por esta spec debe bajar de su cifra actual.

### Tipo de prueba principal

**Unit testing** con `httpx.MockTransport` (sin red real) para los 2 clientes HTTP nuevos — mismo patrón exacto que `tests/test_fmp_client.py`/`tests/test_treasury_client.py` — y para la función pura nueva `corporate_events.py` (mismo estándar que `market_context.py`/`rules.py`/`valuation.py`, sin mocks, solo datos de entrada). **Integration testing** para el wiring en `query_handler.py` (reutiliza `_adobe_router`/`_make_clients` ya existentes en `test_query_handler.py`, agregando rutas nuevas al mismo estilo). No aplica E2E/smoke — mismo criterio ya usado en toda spec anterior de este proyecto (bot de un solo usuario, sin infraestructura de staging).

---

### 1. Tests existentes que rompen — impacto de firma/comportamiento

#### 1.1 — CRÍTICO, no cubierto por los criterios de aceptación del `architect`: `query_handler.Clients` y `bot.build_application`

El pseudocódigo de la Decisión #4 (Parte 1) y Decisión #3 (Parte 2) agrega 4 campos nuevos a `Clients` **sin default** (`finnhub_http: httpx.AsyncClient`, `finnhub_api_key: Optional[str]`, `sec_edgar_http: httpx.AsyncClient`, `sec_edgar_user_agent: Optional[str]`). En un `@dataclass`, un campo sin `= valor` es **posicional/keyword obligatorio** aunque su tipo sea `Optional[...]` — el tipo describe qué valor puede tomar, no si hay que pasarlo.

Conté **13 sitios** en `tests/test_query_handler.py` que instancian `query_handler.Clients(...)` pasando exactamente los 5 campos de hoy (`fmp_http`, `fred_http`, `treasury_gov_http`, `fmp_api_key`, `fred_api_key`), sin ningún campo nuevo: 2 helpers reutilizados por la mayoría de los tests (`_make_clients`, línea 68-80; `_empty_clients`, línea 726-734) + 11 construcciones inline independientes (líneas 123, 243, 270, 292, 350, 392, 512, 544, 943, 977, 1016 — cada una arma su propio `httpx.MockTransport` para un escenario puntual, ej. `test_fetch_and_analyze_datos_incompletos_mensaje_claro`, `test_fetch_and_analyze_cero_llamadas_http_nuevas`). Además, `tests/test_bot.py::test_build_application_registra_handlers` (línea 46-54) llama a `bot.build_application(telegram_token=..., allowed_chat_id=..., db_path=..., fmp_api_key=..., fred_api_key=...)` — si `build_application` gana parámetros nuevos sin default (`finnhub_api_key`, `sec_edgar_user_agent`), ese test también rompe.

**Si el `implementer` sigue el pseudocódigo literal del `architect` (sin default), las 13 construcciones de `Clients` + la construcción de `build_application` fallan con `TypeError: missing N required keyword-only argument(s)` antes de que corra un solo assert** — no es un fallo de lógica, es un fallo de arranque de la suite completa de `test_query_handler.py` (1036 líneas, ~90 tests) más `test_bot.py`. El criterio de aceptación que el `architect` ya escribió ("`Clients` acepta `finnhub_http`/`finnhub_api_key` sin romper la construcción existente en `bot.py` — test de regresión") **solo cubre el único call site de `bot.py`**, no los 14 call sites de test que este spec no nombra. Esto es un gap real, no una interpretación — ver sección 5.

**Criterio QA obligatorio (no negociable, bloquea el resto de esta sección si no se cumple):**
- [ ] `Clients` define los 4 campos nuevos **con default** `= None`:
  ```python
  finnhub_http: Optional[httpx.AsyncClient] = None
  finnhub_api_key: Optional[str] = None
  sec_edgar_http: Optional[httpx.AsyncClient] = None
  sec_edgar_user_agent: Optional[str] = None
  ```
  (los defaults van al final del dataclass sin violar la regla de Python de "campo sin default no puede seguir a uno con default" — los 5 campos actuales quedan primero, sin tocar).
- [ ] `build_application` define `finnhub_api_key: str | None = None` y `sec_edgar_user_agent: str | None = None` (con default, a diferencia de `fred_api_key` que hoy es obligatorio-pero-nullable — acá si no hay default, `test_build_application_registra_handlers` rompe igual).
- [ ] Con esos defaults: los 13 sitios de `Clients(...)` en `test_query_handler.py` y el sitio de `build_application(...)` en `test_bot.py` **no requieren ningún cambio** — test de regresión: correr la suite completa tal cual está hoy contra el código nuevo, 0 modificaciones a esos 14 call sites, 0 fallos nuevos.
- [ ] Si por algún motivo `implementer`/`architect` deciden NO dar default (ej. por consistencia estricta con `fred_api_key`), la alternativa es una spec patch que enumere explícitamente los 14 call sites a actualizar — no es aceptable descubrir esto recién en Ralph Loop.

#### 1.2 — `peers.py`: no rompe nada (confirmado por inspección, no por suposición)

`PeerAverageResult.fuente_peers: str = PEERS_FUENTE_FIJO` tiene default → las 15 instanciaciones indirectas vía `get_peer_pe_average(...)` en `tests/test_peers.py` (ninguna pasa `get_dynamic_peers_fn`, todas caen al mismo branch de hoy) siguen produciendo objetos con `fuente_peers == PEERS_FUENTE_FIJO`. Verifiqué que **ningún test de `test_peers.py` hace `assert result == PeerAverageResult(...)` por igualdad de dataclass completa** (grep confirmado, 0 coincidencias) — todos comparan campo por campo (`result.per_promedio`, `result.peers_usados`, etc.), por lo que un campo nuevo con default no puede romperlos aunque comparen por igualdad parcial. `test_get_peer_pe_average_preserva_orden_de_peers_by_sector` (línea 309) sigue pasando: no usa `get_dynamic_peers_fn`, el orden de `PEERS_BY_SECTOR["Technology"]` no cambia.

- [ ] Test de regresión explícito: los 15 tests actuales de `test_peers.py` pasan sin modificar una línea contra el `peers.py` nuevo.

#### 1.3 — `market_context.py`: no rompe nada (mismo análisis)

`PeerComparisonResult.fuente_peers` con default `PEERS_FUENTE_FIJO` (Decisión #3, Parte 1) — 0 instanciaciones de `PeerComparisonResult(...)` por igualdad completa en `test_market_context.py` (grep confirmado; solo `VixResult(...)` se compara por igualdad, en 4 tests — `VixResult` no lo toca esta spec). Los 17 tests de `compare_to_peers` en `test_market_context.py` (líneas 86-278) siguen pasando sin cambios.

- [ ] Test de regresión explícito: los 17 tests de `compare_to_peers`/`calculate_momentum` pasan sin modificar.

#### 1.4 — `summary.py`: `peers_note`, orden, disclaimer final — no rompe, con una condición

`test_peers_note_default_menciona_eleccion_manual_y_no_fmp` (línea 608) hace `assert "elegida a mano" in text` y `assert "no la arma FMP" in text` sobre el resultado de `_build_summary()` (sin argumentos, o sea `peers_note` no explícito y sin `fuente_peers` en `peer_comparison`). El texto fijo propuesto en la Decisión #6 de la Parte 1 (`_build_peers_note` rama `else`) contiene ambas frases **verbatim** — confirmado por lectura literal del pseudocódigo del `architect`. Igual con el disclaimer final: `test_disclaimer_12b_siempre_presente_con_treasury_source`/`sin_treasury_source` (líneas 982-995) buscan la substring `"no asesoramiento financiero profesional ni una recomendación de inversión"` — la Decisión #7 de la Parte 2 preserva esa frase exacta al reescribir el disclaimer (solo cambia lo que viene después). `test_orden_wacc_antes_que_disclaimer_12b` (línea 998) también sobrevive, no depende de la sección de eventos corporativos.

`test_orden_completo_de_build_summary_con_extras`/`sin_extras` (líneas 1076-1107) verifican orden de secciones vía `text.index(...)` — como `corporate_events` es `Optional[list[dict]] = None` por default y `_build_summary()` (helper de test, revisar su firma) no pasa ese parámetro, `build_corporate_events_section(None)` devuelve `None` y la sección se omite (`if not events: return None` — `None` es falsy). El orden de las secciones existentes no cambia de posición relativa entre sí, solo se inserta un elemento adicional (que en este caso es `None` y se filtra) entre `market_context_section` y `risk_section`. **Condición**: esto solo es cierto si `build_corporate_events_section` recibe literalmente `None` (o `[]`) cuando el llamador no pasa nada — confirmar que `_build_summary()` (el helper interno de `test_summary.py`, no confundir con `summary.build_summary`) no empieza a pasar `corporate_events=[...]` por accidente al hacerse el merge.

- [ ] Test de regresión explícito: `test_peers_note_default_menciona_eleccion_manual_y_no_fmp`, `test_disclaimer_12b_siempre_presente_con_treasury_source`, `test_disclaimer_12b_siempre_presente_sin_treasury_source`, `test_orden_wacc_antes_que_disclaimer_12b`, `test_orden_completo_de_build_summary_con_extras`, `test_orden_completo_de_build_summary_sin_extras` — los 6 pasan sin modificar una línea.
- [ ] Nuevo test de orden explícito (no existe hoy, hace falta agregarlo): con `corporate_events` no vacío, `text.index("Contexto de mercado") < text.index("Eventos corporativos") < text.index("Encaje con tu perfil de riesgo")` — el criterio de aceptación del `architect` ("test de orden") lo pide en prosa pero no da el nombre/forma del test; se deja fijado acá.

#### 1.5 — Resumen

| Módulo | Tests existentes afectados | Rompe? | Condición |
|---|---|---|---|
| `query_handler.Clients` / `bot.build_application` | 13 + 1 = 14 call sites | **SÍ, si no hay default** | Campos nuevos deben tener `= None` (criterio 1.1, obligatorio) |
| `peers.py` | 15 tests (`test_peers.py`) | No | `fuente_peers` con default ya en el pseudocódigo del `architect` |
| `market_context.py` | 17 tests (`test_market_context.py`) | No | ídem |
| `summary.py` | 6 tests puntuales (ver 1.4) | No | Texto fijo preserva las substrings exactas que testean hoy |
| `fetch_and_analyze`/`fetch_and_analyze_parts` (firma pública) | `test_fetch_and_analyze_firma_publica_sin_cambios` (línea 992) | No | Ningún parámetro nuevo en esas 2 funciones — todo el wiring nuevo vive dentro del cuerpo, vía `clients.finnhub_*`/`clients.sec_edgar_*` |
| `test_fetch_and_analyze_cero_llamadas_http_nuevas` (línea 1004) | Cuenta rutas contra `fmp_http` únicamente | No, con la condición 1.1 | Finnhub/SEC EDGAR usan clientes HTTP separados (`finnhub_http`/`sec_edgar_http`), nunca tocan `fmp_http` — el conteo de rutas de FMP no cambia mientras `finnhub_api_key`/`sec_edgar_user_agent` no se pasen en ese test (no se pasan hoy, y no hace falta que empiecen a pasarse) |

---

### 2. Matriz de tests nuevos — Parte 1 (Finnhub)

#### `tests/test_finnhub_client.py` (nuevo archivo, mismo patrón que `test_treasury_client.py`)

| # | Caso | Tipo | Detalle |
|---|---|---|---|
| F1 | Happy path | Feliz | `MockTransport` devuelve `["MSFT", "ORCL", "CRM", "IBM", "SAP"]` (200) → `get_peers` devuelve la lista tal cual, en el mismo orden |
| F2 | Timeout | Guarda | `httpx.TimeoutException` → `FinnhubError`, mensaje sin URL ni key |
| F3 | HTTP error (402/403/429) | Guarda | `httpx.HTTPStatusError` → `FinnhubError` con el status en el mensaje, nunca la key |
| F4 | Error de red | Guarda | `httpx.RequestError` → `FinnhubError` genérico |
| F5 | Respuesta no-JSON | Guarda | body no parseable → `FinnhubError` |
| F6 | Respuesta JSON pero no-lista (ej. `{"error": "..."}`) | Guarda | `data` no es `list` → `[]` (no excepción — spec dice explícitamente "se descartan/filtran", no que se lance error) |
| F7 | Lista con elementos no-string (ej. `["MSFT", 123, None, "ORCL"]`) | Límite | se filtran los no-string, quedan solo `["MSFT", "ORCL"]` |
| F8 | Lista vacía `[]` (200 OK) | Límite | `get_peers` devuelve `[]` — no es un error, es un dato válido (el fallback lo decide `peers.py`, no `finnhub_client.py`) |
| F9 | `params=` usa `httpx`, nunca f-string | Seguridad | inspección de la llamada real a `client.get(url, params={...})` — verificar con un handler que capture `request.url.params` que `symbol`/`grouping`/`token` llegan como query params URL-encoded, nunca concatenados en `url` |
| F10 | La API key no aparece en el mensaje de ningún `FinnhubError` | Seguridad | mismo patrón que `test_error_401_no_filtra_api_key` de `test_fmp_client.py` — construir con una key "canario" (ej. `"CANARY-KEY-12345"`) y assertar que no está en `str(exc)` para F2/F3/F4/F5 |
| F11 | Ningún log incluye la API key ni la URL completa con params | Seguridad | mismo patrón que `test_error_401_no_filtra_api_key_en_logs(caplog)` — `caplog.at_level(logging.WARNING)`, assert key canario no está en `caplog.text` |
| F12 | `grouping` se envía tal cual se pasa (default `"subIndustry"`) | Límite | verificar el query param real, no solo que la función no crashee |

#### `tests/test_peers.py` — casos nuevos (agregar a la suite existente, no reemplazar)

| # | Caso | Tipo | Resultado esperado |
|---|---|---|---|
| P1 | `get_dynamic_peers_fn` devuelve 3 tickers (mínimo exacto) | Límite (aceptación) | se usa la lista dinámica, `fuente_peers == PEERS_FUENTE_FINNHUB` |
| P2 | `get_dynamic_peers_fn` devuelve 4 tickers | Feliz | ídem, con 4 |
| P3 | `get_dynamic_peers_fn` devuelve 5 tickers (tope exacto) | Límite | ídem, con 5, sin recorte |
| P4 | `get_dynamic_peers_fn` devuelve 6 tickers | Límite (tope) | se recorta a los primeros 5 (`MAX_PEERS_DINAMICOS`), sin excepción |
| P5 | `get_dynamic_peers_fn` devuelve 1 ticker | Límite (rechazo) | **no** se usa — cae a `get_peers_for_sector`, `fuente_peers == PEERS_FUENTE_FIJO` |
| P6 | `get_dynamic_peers_fn` devuelve 2 tickers | Límite (rechazo) | ídem P5, caso distinto (no colapsar P5/P6 en un solo test parametrizado sin verificar cada uno) |
| P7 | `get_dynamic_peers_fn` devuelve `[]` | Guarda | cae a `get_peers_for_sector`, `fuente_peers == PEERS_FUENTE_FIJO` — caso distinto de P5/P6 (lista vacía vs. lista corta) |
| P8 | `get_dynamic_peers_fn` devuelve 4 tickers **incluyendo el propio ticker** | Límite exacto | se filtra el propio antes de contar el mínimo → quedan 3 → SÍ se acepta (`fuente_peers == FINNHUB`) — el criterio del `architect` ya lo pide explícito |
| P9 | `get_dynamic_peers_fn` devuelve 7 tickers, con el propio ticker en una posición intermedia (ej. índice 2 de 7) | Límite (orden filtro→tope) | se filtra el propio primero (quedan 6), **luego** se recorta a 5 — verificar que el resultado son los primeros 5 de la lista ya filtrada, no los primeros 5 de la lista cruda. Este caso **no está en la lista de criterios del `architect`** — el pseudocódigo lo resuelve sin ambigüedad (`filter` antes de `[:MAX]`) pero ningún criterio existente lo ejercita con el propio ticker en el medio de una lista >5; se agrega para no dejar ese orden de operaciones sin cobertura |
| P10 | `get_dynamic_peers_fn=None` | Regresión | comportamiento idéntico a hoy, `fuente_peers == PEERS_FUENTE_FIJO` |
| P11 | `get_dynamic_peers_fn` acepta 3-5 candidatos válidos, pero **ninguno** devuelve un `earningsYield` utilizable desde `get_peer_metrics_fn` (los 3 caen en `peers_no_usados`) | Combinación (no está en la lista del `architect`) | `per_promedio is None`, `peers_usados == []`, **pero `fuente_peers` sigue siendo `PEERS_FUENTE_FINNHUB`** (no se degrada a `FIJO` solo porque el resultado final quedó vacío — la fuente de candidatos y la validez del PER son ejes independientes). Este caso combina el branch degenerado ya existente ("0 de N peers válidos") con la fuente dinámica nueva; el pseudocódigo del `architect` (bloque `if not pes: return PeerAverageResult(..., fuente_peers=fuente_peers)`) ya lo resuelve bien, pero no hay ningún criterio de aceptación ni test que lo ejercite — riesgo real de que `implementer` "simplifique" y resetee `fuente_peers` a `FIJO` en ese branch por accidente |
| P12 | Regresión: `peers_pe`/`peers_no_usados` con motivo, usando `get_dynamic_peers_fn` en vez de la lista fija | Regresión | reutilizar los casos ya existentes (`PEER_MOTIVO_SIN_DATO`, `PEER_MOTIVO_EARNINGS_YIELD_NO_POSITIVO`) pero con `fuente_peers=FINNHUB` — confirma que la Parte 1 no reinventa esa lógica |

#### `tests/test_market_context.py` — casos nuevos

| # | Caso |
|---|---|
| M1 | `compare_to_peers(..., fuente_peers=PEERS_FUENTE_FINNHUB)` propaga el campo en las 4 ramas (`eps_no_positivo`, `sin_peers_validos`, `un_solo_peer_valido`, comparable) — parametrizar sobre las 4 ramas existentes agregando el campo nuevo |
| M2 | `compare_to_peers(...)` sin pasar `fuente_peers` → default `PEERS_FUENTE_FIJO` (regresión de compatibilidad hacia atrás, mismo patrón que ya usa `test_compare_to_peers_backward_compat_sin_pasar_peers_pe_ni_no_usados`, línea 228) |

#### `tests/test_query_handler.py` — casos nuevos

| # | Caso |
|---|---|
| Q1 | `clients.finnhub_api_key=None` → 0 llamadas a un `MockTransport` de Finnhub (agregar un router que cuenta llamadas y fallar el test si recibe alguna) |
| Q2 | `clients.finnhub_api_key` configurado, Finnhub responde 3+ peers válidos → `peer_comparison_dict["fuente_peers"] == "finnhub"`, y el/los peer(s) devueltos por Finnhub (no los de `PEERS_BY_SECTOR`) son los que se consultan contra `/key-metrics` (verificar con el router de FMP qué símbolos golpeó) |
| Q3 | `clients.finnhub_api_key` configurado pero Finnhub responde con `FinnhubError` (mock que lanza en el transport) → el closure `_get_finnhub_peers` la captura y devuelve `[]` → cae al fallback fijo, **sin que la excepción llegue a `fetch_and_analyze_parts`** (test negativo: la consulta entera no debe fallar) |
| Q4 | Regresión: `test_fetch_and_analyze_cero_llamadas_http_nuevas` sigue verde sin modificar (ver 1.5) |
| Q5 | Regresión: `test_fetch_and_analyze_firma_publica_sin_cambios` sigue verde sin modificar (ver 1.5) |

#### `tests/test_summary.py` — casos nuevos

| # | Caso |
|---|---|
| S1 | `_build_peers_note(None)` y `_build_peers_note(peers.PEERS_FUENTE_FIJO)` → mismo texto, ambos contienen "elegida a mano" y "no la arma FMP" |
| S2 | `_build_peers_note(peers.PEERS_FUENTE_FINNHUB)` → contiene "Finnhub" y "sub-industria" (o "subindustria"/"sub industria" — **confirmar la grafía exacta que use el `implementer`, ver gap en sección 5**), NO contiene "elegida a mano" |
| S3 | `peers_note` explícito pasado por el llamador tiene prioridad total sobre `fuente_peers` — pasar `peers_note="texto custom"` + `peer_comparison={"fuente_peers": "finnhub"}` y verificar que sale "texto custom", no el texto calculado |

---

### 3. Matriz de tests nuevos — Parte 2 (SEC EDGAR)

#### `tests/test_sec_edgar_client.py` (nuevo archivo)

| # | Caso | Tipo | Detalle |
|---|---|---|---|
| E1 | Happy path — `get_cik_for_ticker` | Feliz | mock de `company_tickers.json` con `{"0": {"ticker": "ADBE", "cik_str": 796343}}` → devuelve `"0000796343"` (10 dígitos, ceros a la izquierda) |
| E2 | Happy path — `get_submissions` | Feliz | mock de `data.sec.gov/submissions/CIK...` → devuelve el dict tal cual |
| E3 | `get_cik_for_ticker`: timeout/HTTP error/error de red/no-JSON en `company_tickers.json` | Guarda | los 4 casos → `None`, nunca excepción hacia afuera (el `SecEdgarError` interno de `_get_json` se captura dentro de `get_cik_for_ticker`) |
| E4 | `get_submissions`: mismos 4 casos de fallo | Guarda | → `None`, nunca excepción |
| E5 | Ticker no encontrado en el mapeo (ej. `"ZZZZ"` no está en el dict cacheado) | Guarda | `None`, no excepción, no reintenta la descarga (el caché ya está cargado) |
| E6 | Header `User-Agent` presente en la llamada a `company_tickers.json` | Seguridad/requisito SEC | capturar `request.headers["User-Agent"]` en el `MockTransport` y verificar que es exactamente el valor pasado |
| E7 | Header `User-Agent` presente en la llamada a `submissions` | ídem | mismo chequeo, segunda URL |
| E8 | Caché dentro del TTL: 2 llamadas seguidas a `get_cik_for_ticker` → 1 sola request HTTP real | Cache | contar invocaciones del `MockTransport` con un handler que incrementa un contador |
| E9 | Caché expirado: forzar `_cache_loaded_at` a >24h atrás (ver técnica en sección 5.2, no requiere mockear el reloj) → la siguiente llamada vuelve a pedir `company_tickers.json` | Cache | contador de requests sube a 2 |
| E10 | `company_tickers.json` con formato inesperado (no es un `dict`, ej. una lista) | Guarda de tipo | `SecEdgarError` interno → `get_cik_for_ticker` devuelve `None`, no crashea |
| E11 | Entradas del mapeo con forma inesperada (`entry` no es dict, `ticker`/`cik_str` ausentes o de tipo raro) | Guarda de tipo | esas entradas puntuales se descartan del caché, no rompen el resto del mapeo |
| E12 | Fallo de red al construir el caché → `get_cik_for_ticker` devuelve `None` y **la siguiente llamada reintenta** (no hay negative-caching de fallos — `_cache_loaded_at` no se actualiza si `_ensure_ticker_cache` lanza) | Comportamiento no obvio del pseudocódigo, agregar explícito | 2 llamadas seguidas con la primera fallando y la segunda con éxito → la segunda sí construye el caché |
| E13 | Ningún log de `_get_json`/`get_cik_for_ticker`/`get_submissions` incluye el body completo de la respuesta ni datos que no correspondan (criterio del `architect`, "no aplica API key acá pero sí se verifica que no se loguee de más") | Seguridad | `caplog` sobre un escenario de error con un body grande/con contenido sensible simulado, verificar que solo aparece `url=`/`status=` en el log, nunca el contenido del body |

#### `tests/test_corporate_events.py` (nuevo archivo, función pura — sin `httpx`/mocks de red)

| # | Caso | Tipo |
|---|---|---|
| C1 | `extract_relevant_8k_events(None, None)` | Guarda | `[]` |
| C2 | `extract_relevant_8k_events(None, "0000320193")` | Guarda | `[]` |
| C3 | `extract_relevant_8k_events({...}, None)` | Guarda | `[]` |
| C4 | `submissions` sin la clave `"filings"` o sin `"recent"` (el `.get(...) or {}` del pseudocódigo debe sostener esto) | Guarda de forma | `[]`, no `KeyError` |
| C5 | Filing con `form == "10-Q"`/`"10-K"`/`"4"` | Filtro | se descarta |
| C6 | Filing `8-K` con `items` que no matchea ningún código relevante (ej. solo `"2.02"`, `"9.01"`) | Filtro | se descarta |
| C7 | Filing `8-K` con `items="5.02,9.01"` (1 relevante + 1 no relevante) | Feliz (parcial) | aparece con `items=["5.02"]` únicamente |
| C8 | Filing `8-K` con `items="1.01,5.02"` (2 relevantes) | Feliz | 1 solo evento, ambas etiquetas |
| C9 | Filing dentro de la ventana, exactamente en el borde: `filing_date == date.today() - timedelta(days=180)` | Límite exacto — **no está en la lista del `architect`, agregar explícito** | se **incluye** (`filing_date < cutoff` es estrictamente menor, el día 180 exacto no queda excluido) |
| C10 | Filing en el borde+1: `filing_date == date.today() - timedelta(days=181)` | Límite exacto | se **excluye** |
| C11 | Más de `max_events=5` filings relevantes | Tope | se recorta a 5, en el orden en que aparecen en `filings.recent` (más reciente primero, según cómo la SEC ordena el array) |
| C12 | Arrays de distinto largo en `filings.recent` (ej. `items` con 3 elementos, `form` con 5) | Guarda de forma | usa `min(...)`, no `IndexError` |
| C13 | `filingDate` no parseable (`"fecha-invalida"`, `None`, `123`) | Guarda de tipo | esa entrada se descarta, el resto de la lista se sigue procesando |
| C14 | `_build_filing_url` con cualquier argumento vacío o `None` (`cik10=""`, `accession_number=None`, `primary_document=""`) | Guarda | `None` — parametrizar los 3 argumentos por separado, no solo un caso combinado |
| C15 | `_build_filing_url` con `cik10="0000796343"` (con ceros) y `accession_number="0000796343-24-000123"` (con guiones) | Feliz | URL exacta `https://www.sec.gov/Archives/edgar/data/796343/000079634324000123/{documento}` — verificar la normalización de ambos por separado, no solo el resultado final |
| C16 | `_build_filing_url` con `cik10` no convertible a `int` (ej. `"ABC"`) | Guarda de tipo | `None` |
| C17 | Filing `8-K/A` (enmienda, forma literal distinta a `"8-K"`) | Comportamiento del código tal como está escrito, no un bug — **agregar explícito para que quede documentado, ver nota en sección 5.3** | se descarta (`forms[i] != "8-K"` es comparación exacta de string) |
| C18 | 0 eventos en la ventana (todos los filings del fixture son 10-K/10-Q, o todos fuera de ventana, o todos con Items no relevantes) | Feliz (caso "sin eventos") | `[]`, sin excepción — es el caso que hace que `build_corporate_events_section` devuelva `None` río abajo |

#### `tests/test_query_handler.py` — casos nuevos (Parte 2)

| # | Caso |
|---|---|
| Q6 | `sec_edgar_user_agent=None`/vacío → 0 llamadas HTTP a un `MockTransport` de SEC EDGAR |
| Q7 | `sec_edgar_user_agent` configurado, `get_cik_for_ticker` devuelve `None` (ticker no encontrado, ej. probar con un ADR) → `corporate_events_list == []`, el resto del análisis (ratios, valor justo, etc.) sale idéntico a un caso sin SEC EDGAR — comparar contra el mismo fixture con `sec_edgar_user_agent=None` |
| Q8 | `sec_edgar_user_agent` configurado, CIK + submissions exitosos, con al menos 1 evento relevante en el fixture → `corporate_events_list` poblada, y el texto final del análisis contiene la sección "Eventos corporativos" |
| Q9 | `get_submissions` falla (mock que devuelve 500) tras un CIK exitoso → `corporate_events_list == []`, resto del análisis sin cambios, sin excepción propagada |
| Q10 | Regresión: `fetch_and_analyze_parts` no cambia de firma pública (extender `test_fetch_and_analyze_firma_publica_sin_cambios`, ya cubierto en 1.5) |

#### `tests/test_summary.py` — casos nuevos (Parte 2)

| # | Caso |
|---|---|
| S4 | `build_corporate_events_section([])` → `None` |
| S5 | `build_corporate_events_section(None)` → `None` (el tipo declarado es `list[dict]` pero el llamador real, `build_summary_parts`, puede pasar `None` por default — confirmar que `if not events` cubre ambos, no solo `[]`) |
| S6 | `build_corporate_events_section([{...1 evento...}])` → contiene la fecha exacta, la(s) etiqueta(s) unidas con `" + "`, el link en formato Markdown `[ver el filing](url)`, y la nota de fuente con las substrings "SEC EDGAR" y "NO resume" |
| S7 | `build_corporate_events_section([{...2 eventos...}])` → 2 líneas de bullet, cada una con su fecha/etiquetas/link propios |
| S8 | `build_summary_parts(..., corporate_events=None)` (default, llamador viejo) → texto idéntico al que produce hoy sin ese parámetro — test de regresión byte a byte contra un caso ya existente |
| S9 | `build_summary_parts(..., corporate_events=[{...}])` → orden: `text.index("Contexto de mercado") < text.index("Eventos corporativos") < text.index("Encaje con tu perfil de riesgo")` (ver 1.4, este es el test que faltaba) |
| S10 | Disclaimer final ya no contiene la frase literal "No tiene en cuenta noticias, eventos recientes, cambios de gestión" (la vieja) — sí contiene la mención a SEC EDGAR sin resumir (buscar substring "sin resumir" o equivalente, según texto final del `implementer`) |
| S11 | Con `corporate_events=None` (sin eventos), el disclaimer sigue apareciendo con la frase de apertura sin cambios ("no asesoramiento financiero profesional...") — regresión de S8 pero enfocada específicamente en el disclaimer, no en toda la sección |

---

### 4. Piso de cobertura

**No aplica un único piso para las 3 piezas nuevas** — mismo criterio de diferenciación ya usado en `SDD_investbot_mvp.md` Iter-1 (bucket 95%/100% para "corazón matemático" vs. bucket más bajo para "resto") y en `SDD_contenido_financiero_explicado.md` (100% para funciones puras nuevas vs. matriz de escenarios para integración):

| Archivo | Piso | Justificación |
|---|---|---|
| `corporate_events.py` | **100% líneas + 100% ramas** | Función pura, sin I/O — mismo estándar ya vigente hoy en `peers.py`/`market_context.py`/`rules.py`/`valuation.py` (los 4 al 100%/100% en el baseline de esta spec). La matriz C1-C18 de la sección 3 cubre cada rama del pseudocódigo del `architect` sin necesitar ninguna rama defensiva irreductible — no hay motivo para aceptar menos acá que en `market_context.py`. |
| `finnhub_client.py` | **100% líneas + 100% ramas** | No es el estándar "resto ≥70%" de `SDD_investbot_mvp.md` Iter-1 (ese piso es de 2026-07-24, para el MVP inicial, y el propio `qa` de iteraciones posteriores ya lo subió para módulos con lógica real). El comparable directo correcto es **`fmp_client.py`**, no `treasury_client.py`: mismo patrón textual ("wrapper HTTP fino", 4 excepciones traducidas — Timeout/HTTPStatusError/RequestError/no-JSON), y `fmp_client.py` ya está al 100%/100% en el baseline de arriba pese a tener *más* sub-branching que `finnhub_client.py` (sub-casos de 402/429/401/403). Si `fmp_client.py` llegó a 100% con más complejidad, `finnhub_client.py` no tiene excusa estructural para quedar más abajo — la matriz F1-F12 ya lo cubre. |
| `sec_edgar_client.py` | **100% líneas + 100% ramas** | Mismo argumento que `finnhub_client.py`, con el agregado de las ramas de caché (hit/miss/expirado/fallo-sin-negative-caching) — la matriz E1-E13 las cubre todas explícitamente. |
| `peers.py` / `market_context.py` (código nuevo de esta spec: `fuente_peers`, `get_dynamic_peers_fn`) | **100% líneas + 100% ramas** | Ya es el estándar vigente hoy para estos 2 módulos (100%/100% en el baseline) — el código nuevo no introduce ninguna rama defensiva no alcanzable, la matriz P1-P12/M1-M2 lo cubre entero. |
| `query_handler.py` / `summary.py` (código nuevo de esta spec) | **El % total del archivo no baja del baseline** (97%/98% respectivamente) — no se exige subir los huecos preexistentes ya identificados (líneas 146/160-161/177-178/483 de `query_handler.py`; 200→202/377/575→577/577→582/587→591/591→597 de `summary.py`), que no están relacionados con esta spec. El código nuevo específico de esta spec (closures `_get_finnhub_peers`, bloque *best-effort* de SEC EDGAR, `build_corporate_events_section`, `_build_peers_note`) sí debe llegar a 100%/100% propio — mismo criterio ya usado en `SDD_procedencia_peers_individuales.md` sección 3 ("el piso de 100% aplica únicamente al código nuevo/modificado de esta spec"). | Evita forzar a `implementer` a resolver deuda preexistente fuera de alcance (scope creep), pero tampoco permite que código nuevo se esconda detrás del % ya alto del archivo. |

**Nota explícita sobre por qué NO se usa el piso "resto ≥70%"**: ese piso viene de `SDD_investbot_mvp.md` Iter-1 (2026-07-24), pensado para el MVP inicial completo. El propio proyecto ya lo abandonó de facto — el baseline real de hoy tiene `fmp_client.py`/`peers.py`/`market_context.py`/`db.py`/`security.py`/`rules.py`/`valuation.py`/`onboarding.py`/`risk_fit.py` los 9 al 100%, y `treasury_client.py`/`query_handler.py`/`summary.py` entre 96-98%. Aceptar 70% para 2 clientes HTTP nuevos sería una regresión de facto de la práctica ya vigente en este mismo repo, no un estándar nuevo — mismo argumento que ya usó `qa` en `SDD_contenido_financiero_explicado.md` sección 3, punto 2.

**Regla de verificación para Momento 2**: correr `pytest --cov=investbot --cov-branch --cov-report=term-missing` (agregar `--cov-branch` a mano, no está en el comando documentado del README) y confirmar 100%/100% en `finnhub_client.py`, `sec_edgar_client.py`, `corporate_events.py`, y que `peers.py`/`market_context.py` se mantienen en 100%/100%; para `query_handler.py`/`summary.py`, confirmar que el `Missing` no incluye ninguna línea nueva de esta spec (las líneas preexistentes del baseline pueden seguir apareciendo).

---

### 5. Gaps de la spec — bloquean un test 100% determinístico si no se resuelven antes de implementar

#### 5.1 — CRÍTICO: `Clients`/`build_application` sin defaults rompe ~15 sitios de test (ya desarrollado en sección 1.1)

No se repite acá el detalle — es el gap #1 en severidad. Bloquea la suite completa de `test_query_handler.py`/`test_bot.py` si no se resuelve exactamente como se especifica en 1.1 antes de que `implementer` toque el primer archivo.

#### 5.2 — `sec_edgar_client.py`: estado global mutable sin mecanismo de reset entre tests

`_ticker_cik_cache`/`_cache_loaded_at` son variables de módulo (no de instancia, no inyectadas) — mismo patrón ya aceptado que `security.InMemoryRateLimiter`, pero con una diferencia importante: `InMemoryRateLimiter` se instancia por test (`security.InMemoryRateLimiter(...)`), mientras que el caché de `sec_edgar_client.py` es **compartido por todo el proceso de pytest**, incluida toda la suite completa (378+ tests hoy). Si un test escribe en `_ticker_cik_cache` (ej. E1, E8, E9) y no lo limpia, **cualquier test posterior que llame a `get_cik_for_ticker` con el mismo ticker recibe el CIK cacheado del test anterior**, no el que su propio `MockTransport` sirve — esto es no-determinismo dependiente del orden de ejecución, exactamente lo que el skill de QA marca como flaky test inaceptable ("Flaky Test Rate objetivo < 2%, un flaky test es deuda con interés compuesto").

**No es una pregunta abierta de negocio (no requiere a Daniela)** — es una decisión de testabilidad, potestad de `qa`/`implementer`. Se fija acá:

- [ ] `tests/test_sec_edgar_client.py` (y cualquier otro archivo que llegue a ejercitar `sec_edgar_client.get_cik_for_ticker`, incluido `test_query_handler.py`) debe tener un **fixture `autouse`** que resetea `sec_edgar_client._ticker_cik_cache = {}` y `sec_edgar_client._cache_loaded_at = None` antes de cada test:
  ```python
  @pytest.fixture(autouse=True)
  def _reset_sec_edgar_cache():
      sec_edgar_client._ticker_cik_cache.clear()
      sec_edgar_client._cache_loaded_at = None
      yield
  ```
- [ ] Este fixture es un criterio de exit de QA (Momento 2) — sin él, la suite puede pasar en aislamiento (`pytest tests/test_sec_edgar_client.py`) y fallar de forma intermitente al correr la suite completa (`pytest`), dependiendo del orden de recolección de tests. Si `implementer` no lo agrega, es un defecto de severidad ALTA (no crítico porque no afecta producción, pero rompe la confiabilidad de CI).

#### 5.3 — `sec_edgar_client.py`: cómo controlar el TTL de 24h en un test, sin mockear el reloj

El criterio de aceptación del `architect` dice literalmente "Caché expirado (mock de tiempo > TTL) → vuelve a pedir el archivo" pero no dice **cómo** — `_ensure_ticker_cache` llama a `datetime.now(timezone.utc)` inline, no inyectado, así que no hay un parámetro para pasarle un reloj falso. Mockear `datetime.now` en un módulo que hace `from datetime import datetime` requiere `monkeypatch.setattr(sec_edgar_client, "datetime", FakeDatetimeClass)` (patchear la clase completa, no solo el método) — es más frágil de lo necesario.

**Técnica recomendada, fijada acá para no dejarla a interpretación**: como `_cache_loaded_at` es una variable de módulo directamente asignable, el test de "caché expirado" no necesita mockear el reloj en absoluto — alcanza con:
```python
sec_edgar_client._cache_loaded_at = datetime.now(timezone.utc) - timedelta(hours=25)
```
antes de la segunda llamada, y verificar que el `MockTransport` recibe una segunda request a `company_tickers.json`. Es más simple, no depende de mockear una clase built-in, y ejercita la misma condición (`(now - _cache_loaded_at) < timedelta(hours=24)` evaluada en `False`) sin fragilidad adicional.

- [ ] `implementer` usa esta técnica (asignación directa a `_cache_loaded_at`), no un mock de `datetime.now`, para el test E9.

#### 5.4 — `corporate_events.py`: `date.today()` no inyectable — riesgo de tests con fecha de vencimiento silenciosa

`extract_relevant_8k_events` calcula `cutoff = date.today() - timedelta(days=since_days)` usando la fecha real del sistema en el momento de la ejecución, sin ningún parámetro para inyectar "hoy". Esto es correcto para producción, pero para los tests de ventana (C9, C10, y cualquier test que arme un fixture con fechas relativas a "hace X días") significa que **las fechas del fixture de test no pueden ser literales hardcodeadas** (ej. `"2026-01-15"`) — si se hardcodean, el test empieza a fallar solo silenciosamente en el futuro, cuando esa fecha ya no esté dentro de los 180 días de `date.today()` en el momento en que corra CI. Esto es exactamente la clase de bug que las specs anteriores de este proyecto ya identificaron y evitaron en otros contextos (aunque no se nombró explícitamente para fechas hasta ahora).

**No es una pregunta bloqueante de negocio — es una regla de construcción de fixtures, fijada acá:**

- [ ] Todo test de `test_corporate_events.py` que necesite una fecha relativa a la ventana de 180 días debe calcularla en el momento de ejecución del test, nunca como literal:
  ```python
  from datetime import date, timedelta
  fecha_dentro_de_ventana = (date.today() - timedelta(days=30)).isoformat()
  fecha_en_el_borde = (date.today() - timedelta(days=180)).isoformat()   # C9
  fecha_fuera_de_ventana = (date.today() - timedelta(days=181)).isoformat()  # C10
  ```
- [ ] `implementer` no hardcodea ninguna fecha de filing en los fixtures de `test_corporate_events.py` — es un criterio de exit de QA revisable por inspección del código de test (no por corrida, ya que un test con fecha hardcodeada puede pasar hoy y fallar recién en el futuro, sin que Ralph Loop lo detecte en el momento).
- [ ] **No se pide** agregar un parámetro `today: Optional[date] = None` a `extract_relevant_8k_events` para inyección de dependencias (que sería la alternativa "más limpia" y es el patrón que sí usa el resto del proyecto para I/O) — sería scope creep no pedido por `architect`; la regla de fixtures de arriba alcanza para tests deterministas sin tocar la firma de la función pura.

#### 5.5 — Redacción exacta de "sub-industria"/"subIndustry" en el texto de `_build_peers_note`

El criterio del `architect` para `_build_peers_note(peers.PEERS_FUENTE_FINNHUB)` pide que el texto "mencione explícitamente... sub-industria" pero el pseudocódigo de la Decisión #6 usa la grafía "sub-industria" (con guion) en el texto de cara al usuario, mientras que el parámetro técnico es `grouping="subIndustry"` (sin guion, camelCase). No es ambiguo para escribir el código (el pseudocódigo ya trae el texto completo, literal), pero si `implementer` cambia la redacción del texto en algún ajuste posterior, el test S2 debe buscar una substring que sobreviva a variaciones menores de redacción sin volverse frágil.

- [ ] Test S2 busca la substring `"sub-industria"` (con guion, tal como aparece en el texto literal de la Decisión #6) — si `implementer` cambia la redacción, es una corrección menor al test, no un hallazgo de `qa`.

#### 5.6 — Item 8.01 y filings `8-K/A`: comportamiento no explícitamente decidido, pero determinista tal como está escrito

Ya cubierto como caso C17 en la sección 3: el pseudocódigo compara `forms[i] != "8-K"` con igualdad exacta de string, por lo que una enmienda `"8-K/A"` **queda excluida** aunque reporte los mismos Items relevantes que el 8-K original que enmienda. La spec no dice si esto es intencional o un descuido — no bloquea escribir un test determinista (el código, tal como está escrito, tiene un comportamiento único y verificable), pero si Daniela esperaba ver también enmiendas, es un hallazgo a validar con ella **después** de ver el resultado real (no bloquea este pipeline, mismo criterio que la Pregunta 2.2 ya resuelta como no bloqueante). Se deja documentado para no perderlo.

---

### Testabilidad

- [x] Los 2 clientes HTTP nuevos reciben el cliente `httpx.AsyncClient` inyectado (nunca instanciado como global de módulo) — mismo patrón ya auditado en `fmp_client.py`/`treasury_client.py`, permite `httpx.MockTransport` sin red real.
- [x] `corporate_events.py` es función pura sin I/O — recibe `submissions`/`cik10` ya resueltos, sin tocar `sec_edgar_client.py` directamente (mismo desacople que `market_context.py` respecto de `fmp_client.py`).
- [ ] **Pendiente de que `implementer` resuelva antes de empezar** (no es un problema de diseño, es una omisión de la spec): dar defaults `= None` a los 4 campos nuevos de `Clients` y a los 2 parámetros nuevos de `build_application` (gap 5.1/criterio 1.1).
- [ ] **Pendiente**: agregar el fixture `autouse` de reset de caché en `sec_edgar_client.py` (gap 5.2) antes de que exista más de un test que toque ese módulo.
- [x] El estado mutable de módulo en `sec_edgar_client.py` (`_ticker_cik_cache`/`_cache_loaded_at`) es directamente asignable desde tests sin necesitar mocks de reloj — ver técnica en 5.3.

### Criterio de exit de QA

- Todos los tests pasan (`pytest -q` en verde) — suite completa, no solo los archivos nuevos.
- `pytest -v --cov=src/investbot --cov-report=term-missing --cov-fail-under=75` (comando documentado en README) sigue en verde; adicionalmente, corrida manual con `--cov-branch` confirma los pisos de la sección 4 (100%/100% en `finnhub_client.py`/`sec_edgar_client.py`/`corporate_events.py`/`peers.py`/`market_context.py`; `query_handler.py`/`summary.py` sin bajar de 97%/98% y sin líneas nuevas en `Missing`).
- Los 14 call sites de `Clients(...)`/`build_application(...)` identificados en la sección 1.1 pasan **sin modificación** — si alguno necesitó tocarse, es señal de que el criterio de defaults no se cumplió y hay que volver a 1.1, no parchear cada sitio.
- El fixture `autouse` de reset de caché de `sec_edgar_client.py` (5.2) existe y la suite completa (no solo `test_sec_edgar_client.py` en aislamiento) pasa en 2 corridas consecutivas sin flakiness.
- Ningún test de `test_corporate_events.py` usa una fecha de filing hardcodeada como literal — verificado por inspección (5.4).
- Sin tests `skip`/`xfail`. Flaky rate = 0 en la suite nueva (verificar corriendo la suite completa 2 veces seguidas, no solo los archivos nuevos en aislamiento — el riesgo de flakiness de esta spec específicamente viene del caché global de 5.2, no de timing real).
