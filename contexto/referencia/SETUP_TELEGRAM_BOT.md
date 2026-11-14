# Setup manual del bot de Telegram (@BotFather)

Guía corta — es documentación, no código. Daniela la ejecuta manualmente
antes de que el contenedor pueda arrancar en modo polling (el bot necesita
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID` seteados en `.env` para
arrancar; sin ellos falla al arrancar de forma intencional — ver
`security.py`).

## Crear el bot

1. Abrir un chat con [@BotFather](https://t.me/BotFather) en Telegram.
2. Enviar `/newbot`.
3. Elegir un nombre visible para el bot (puede cambiarse después).
4. Elegir un `@username` único que termine en `bot` (ej. `mi_investbot_bot`)
   — esto no puede cambiarse después sin crear un bot nuevo.
5. @BotFather devuelve un **token** con el formato `123456789:AAExxxxxxxx...`.
   Copiarlo y guardarlo en la variable `TELEGRAM_BOT_TOKEN` del `.env` del
   VPS (nunca en el repo, nunca en un commit — `.env` está en `.gitignore`).

## Obtener tu `chat_id`

El bot solo responde a los `chat_id` autorizados (`TELEGRAM_ALLOWED_CHAT_ID`,
uno o varios separados por coma — ej. Daniela + hasta 2 personas más).
Para obtenerlo:

**Opción A — bot auxiliar (más simple):**
1. Abrir un chat con [@userinfobot](https://t.me/userinfobot) en Telegram.
2. Enviarle cualquier mensaje.
3. Responde con tu `id` — ese es tu `chat_id` (un chat privado 1:1 tiene el
   mismo `id` que el usuario).

**Opción B — `getUpdates` del propio bot:**
1. Enviar `/start` (o cualquier mensaje) al bot recién creado desde tu
   cuenta de Telegram.
2. Abrir en el navegador (o `curl`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Buscar el campo `"chat":{"id": ...}` en la respuesta JSON — ese número es
   tu `chat_id`.

Guardar el valor en `TELEGRAM_ALLOWED_CHAT_ID` del `.env` del VPS. Si hay más
de una persona autorizada, separar los `chat_id` con coma (ej.
`TELEGRAM_ALLOWED_CHAT_ID=12345,67890,11223`) — un solo entero (formato
actual) sigue siendo válido sin cambios.

## Verificar antes de arrancar el contenedor

- `.env` tiene `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID` seteados
  (el proceso falla al arrancar si `TELEGRAM_ALLOWED_CHAT_ID` falta o contiene
  algún elemento inválido — comportamiento intencional, fail-closed).
- `.env` tiene `chmod 600`, propietario el usuario sin privilegios root
  (mismo patrón ya usado para FoodMindAI).
- `FMP_API_KEY` (plan gratuito, https://financialmodelingprep.com) y
  `FRED_API_KEY` (gratuita, https://fred.stlouisfed.org/docs/api/api_key.html)
  también están seteadas.

---

## Respuesta a incidente: token comprometido

Si el token de Telegram se filtra (commit accidental, log en modo DEBUG,
captura de pantalla, etc.), tratarlo como un secreto comprometido:

1. **Revocar el token de inmediato** desde @BotFather:
   - Abrir un chat con @BotFather.
   - Enviar `/mybots`.
   - Elegir el bot afectado.
   - `API Token` → `Revoke current token`.
   - @BotFather genera un token nuevo inmediatamente; el anterior deja de
     funcionar en el acto (cualquier proceso que lo use empieza a recibir
     `401 Unauthorized`).
2. **Actualizar `.env` en el VPS** con el token nuevo
   (`TELEGRAM_BOT_TOKEN=...`).
3. **Reiniciar el contenedor** para que tome el token nuevo:
   `docker compose -f docker-compose.prod.yml up -d --force-recreate`.
4. El `chat_id` autorizado **no cambia** — es propiedad de la cuenta de
   Telegram de Daniela, no del bot, así que no hace falta tocar
   `TELEGRAM_ALLOWED_CHAT_ID`.
5. Señal de alerta a vigilar mientras tanto: errores `Conflict` (409) en los
   logs del contenedor (`docker logs investbot-bot`) indican que otro
   proceso está usando el mismo token para hacer polling — es decir, que
   alguien más lo tiene y lo está usando activamente. El bot loguea esto
   como `WARNING` con el mensaje "posible uso concurrente del token
   detectado" (nunca imprime el token en el log).
6. Revisar cómo se filtró el token (¿quedó en un commit? ¿en un log en modo
   DEBUG? ¿en una captura de pantalla compartida?) para no repetir la causa.

**Nota:** esto cubre el compromiso del **token del bot**. El riesgo de que
se comprometa la **cuenta de Telegram de Daniela** (que heredaría el mismo
`chat_id` autorizado) es un riesgo distinto y sistémico de la plataforma,
documentado como limitación conocida en `README.md` — la mitigación ahí es
activar verificación en dos pasos en la cuenta de Telegram, no algo que se
resuelva revocando el token del bot.

---

## Respuesta a incidente: una de las cuentas autorizadas comprometida

Con hasta 3 usuarios autorizados (Daniela + hasta 2 personas más), la
superficie de riesgo de secuestro de cuenta de Telegram (SIM-swap, phishing
de código de acceso, sesión robada) se triplica respecto al caso de 1 solo
usuario — no porque el control de acceso del bot sea más débil (sigue siendo
membership sobre un conjunto de `chat_id`, tan válido como la comparación 1 a
1 anterior), sino porque hay 3 cuentas reales en vez de 1, cada una gestionada
por su propio dueño fuera del control del bot. Es un riesgo aceptado de la
plataforma Telegram, no del código de InvestBot.

El conjunto de `chat_id` autorizados es **estático**: se lee una sola vez al
arrancar el proceso desde `TELEGRAM_ALLOWED_CHAT_ID`. No hay revocación en
caliente — revocar a una sola persona sin afectar a las demás requiere editar
el `.env` y reiniciar el contenedor:

1. **Identificar el `chat_id` afectado** (el de la persona cuya cuenta de
   Telegram se sospecha comprometida).
2. **Editar `TELEGRAM_ALLOWED_CHAT_ID` en el `.env` del VPS**, quitando ese
   `chat_id` del CSV y dejando los demás intactos (ej. de
   `12345,67890,11223` a `12345,11223` si `67890` es el afectado).
3. **Reiniciar el contenedor** para que tome el conjunto nuevo:
   `docker compose -f docker-compose.prod.yml up -d --force-recreate`.
4. **(Opcional pero recomendado)** rotar `TELEGRAM_BOT_TOKEN` en @BotFather
   (ver sección anterior) si existe sospecha de que el token también pudo
   filtrarse por la misma vía (ej. capturas de pantalla compartidas desde la
   cuenta comprometida).
5. Una vez que la persona afectada recupere el control de su cuenta de
   Telegram (y, idealmente, active verificación en dos pasos), su `chat_id`
   puede volver a agregarse al CSV y reiniciar de nuevo.

**Recomendación general:** los 3 usuarios autorizados deberían tener
verificación en dos pasos activada en su cuenta de Telegram — misma
recomendación ya hecha para Daniela en la documentación de seguridad del
proyecto, extendida a cualquier persona adicional que se autorice.

---

## Gemini API key — opcional

Feature opt-in (`SDD_migracion_gemini.md`): con una `GEMINI_API_KEY` válida el
bot muestra mensaje corto + botones "paso a paso" con explicaciones de IA, y
(con `GEMINI_REWRITE_ENABLED=true`) reescribe el tono del primer mensaje.
Sin key, el bot funciona igual con todo calculado por Python.

**Seguridad de la key:** nunca pegar la key en chats, tickets ni commits; no
pegar la salida de `docker compose config` ni de `docker inspect`, que
imprimen el entorno (incluida la key).

### 1. Crear el proyecto y la key en Google AI Studio

Antes de provisionar nada en el VPS (gate de privacidad y de gasto):

1. En Google AI Studio crear un **proyecto nuevo solo para InvestBot** y la
   key de ese proyecto, **vinculando la facturación activa** (tier de pago),
   la misma cuenta de facturación que FoodMind. Si el panel lo ofrece,
   restringir la key a la API "Generative Language".
2. **Confirmar facturación activa:** el proyecto de la key debe mostrar
   facturación activa y la key debe pertenecer a ese proyecto. Anotar la
   confirmación (captura o nota). Si no se puede confirmar el tier de pago,
   **no** poner la key en el `.env` de producción: en el tier gratuito los
   prompts pueden usarse para mejorar productos de Google y ser leídos por
   personas (términos vigentes: https://aistudio.google.com/docs/terms).
3. **Registrar el control de gasto:** una alerta de presupuesto del proyecto
   o un tope de cuota, si el panel lo permite (si no, anotar "no disponible"
   y revisar el saldo cada semana); anotar quién se entera si ocurre un
   error 402 (saldo agotado: frena a todos los proyectos de la misma cuenta
   de facturación; InvestBot degrada solo al contenido determinístico).
4. Ensayar una vez "revocar primero, rotar después" con una key descartable.

### 2. Provisionar la key en el `.env` del VPS

En el VPS, en el directorio donde está `docker-compose.prod.yml`, en este
orden exacto (sin dejar la key en el historial del shell ni imprimirla):

```
# 1) Permisos: debe dar 600. Si no, corregir ANTES de agregar la key.
stat -c %a .env
chmod 600 .env

# 2) Asegurar newline final (si no, la key quedaría pegada a la última línea).
[ -n "$(tail -c1 .env)" ] && echo >> .env
tail -c1 .env | od -An -c

# 3) Agregar la key sin que quede en el historial.
read -rs K; printf 'GEMINI_API_KEY=%s\n' "$K" >> .env; unset K

# 4) Debe dar 1 (una sola línea), sin imprimir el valor.
grep -c '^GEMINI_API_KEY=' .env

# 5) Activar la reescritura de tono (opcional; sin esta línea el bot
#    funciona con mensaje corto y botones, pero sin reescritura).
echo 'GEMINI_REWRITE_ENABLED=true' >> .env
```

Verificar `ls -l .env` → `-rw-------`, con dueño el usuario sin privilegios
de root. Opcionales solo si se quiere cambiar el default: `GEMINI_MODEL`,
`GEMINI_THINKING_LEVEL`, `GEMINI_TIMEOUT_SECONDS`,
`GEMINI_REWRITE_TIMEOUT_SECONDS` (ver `.env.example`).

### 3. Deploy y qué log esperar

```
docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d
docker logs investbot-bot
```

El log de arranque debe decir `Gemini habilitado model=... rewrite=on` (o
`rewrite=off` si no se agregó `GEMINI_REWRITE_ENABLED=true`). Si dice
`GEMINI_KEY_INVALID`, la key tiene un formato inválido (espacios, comillas,
saltos de línea): corregirla antes de seguir. Después, un análisis real y un
botón "paso a paso" deben mostrar una línea `Gemini OK feature=... finishReason=STOP`.
El bot nunca imprime la key en los logs.

Rollback rápido si algo falla: poner `GEMINI_REWRITE_ENABLED=false` y
`docker compose -f docker-compose.prod.yml up -d --force-recreate` si el
problema es solo la reescritura; vaciar `GEMINI_API_KEY` para volver al modo
100% determinístico.

### Capas de datos opcionales (EDGAR y Finnhub)

`SEC_EDGAR_USER_AGENT` (formato `<app> <email>`, alias dedicado) activa SEC EDGAR
para estados financieros y 8-K; `FINNHUB_API_KEY` activa Finnhub para peers y
cotización/perfil. Ambas vacías = todo vía FMP. Rollback y validación previa
(`scripts/compare_edgar_fmp.py`, imagen `investbot-bot:prev-8b74b58`,
`docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate`):
ver la sección "Despliegue" del `README.md`.

### 4. Rotación de la key

1. Crear la key nueva en AI Studio (mismo proyecto).
2. Reemplazar la línea en `.env` (mismo procedimiento de la sección 2: sin
   historial del shell, sin imprimir) y reiniciar:
   `docker compose -f docker-compose.prod.yml up -d --force-recreate`.
3. **Revocar la key anterior** en AI Studio.
4. Comprobar con `grep -c '^GEMINI_API_KEY=' .env` que sigue habiendo una
   sola línea, y que el log de arranque dice `Gemini habilitado`.

---

## Respuesta a incidente: key de Gemini comprometida

Si la key de Gemini se filtra (commit accidental, pegada en un chat o ticket,
salida de `docker compose config` / `docker inspect` compartida, captura de
pantalla), tratarla como un secreto comprometido. **Orden: revocar primero,
rotar después.**

1. **Revocar la key de inmediato** en Google AI Studio (proyecto de InvestBot
   → API keys → eliminar la key afectada). Deja de funcionar en el acto y
   el bot cae solo al contenido determinístico (fallback sin error visible
   para el usuario; el log registrará `GEMINI_AUTH` o `GEMINI_BAD_REQUEST`; el status real de una key revocada se anota en CA-S4).
2. **Crear una key nueva** en el mismo proyecto y actualizar el `.env` del
   VPS con el procedimiento de la sección 2 (sin historial del shell).
3. **Reiniciar el contenedor** para que tome la key nueva:
   `docker compose -f docker-compose.prod.yml up -d --force-recreate`.
4. Comprobar con `docker logs investbot-bot` que dice `Gemini habilitado` y
   con `grep -c '^GEMINI_API_KEY=' .env` que hay una sola línea.
5. Revisar en el panel de AI Studio el uso y el gasto del período expuesto
   (llamadas que no sean del bot) y, si hay consumo anómalo, avisar a quien
   administra la facturación compartida.
6. Revisar cómo se filtró la key para no repetir la causa. Si quedó en un
   commit, revocarla igual (el historial de git la conserva) y no reescribir
   el historial como sustituto de la revocación.
