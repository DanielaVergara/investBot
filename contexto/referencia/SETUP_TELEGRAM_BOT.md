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

El bot solo responde al `chat_id` autorizado (`TELEGRAM_ALLOWED_CHAT_ID`).
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

Guardar el valor en `TELEGRAM_ALLOWED_CHAT_ID` del `.env` del VPS.

## Verificar antes de arrancar el contenedor

- `.env` tiene `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID` seteados
  (el proceso falla al arrancar si `TELEGRAM_ALLOWED_CHAT_ID` falta o no es
  un entero — comportamiento intencional, fail-closed).
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
