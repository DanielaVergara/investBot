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

## Redacción mejorada por IA local (Ollama + Tailscale) — opcional

Feature opt-in, apagada por defecto (`SDD_redaccion_ia_ollama.md`): reescribe
el TONO del mensaje ya armado por `summary.py` (nunca cambia ningún número,
ticker, porcentaje o veredicto — un guard de código, no una instrucción de
prompt, lo garantiza). Requiere Ollama corriendo en la PC de Daniela y
alcanzable desde el VPS vía Tailscale. Solo funciona cuando la PC está
prendida; con la PC apagada (o sin la feature habilitada) el bot responde
exactamente igual que hoy, sin reescritura, sin error visible.

Pasos de alto nivel (no es un tutorial exhaustivo — cada paso tiene su
propia documentación oficial):

### 1. Conectividad VPS ↔ PC vía Tailscale

Elegida sobre Cloudflare Tunnel/ngrok/SSH reverso (evaluación completa en
`contexto/specs/abiertas/SDD_redaccion_ia_ollama.md`, Decisión de diseño #1 +
revisión de `security`, sección 1) porque ningún extremo necesita publicar
un puerto público — ambos son clientes salientes de una mesh VPN
(WireGuard), coherente con el modelo "sin puertos publicados" que ya usa
`bot.py` para hablarle a Telegram.

1. Instalar Tailscale en el **host** del VPS (no dentro del contenedor
   Docker) y en la PC de Daniela: https://tailscale.com/download
2. Loguear ambos dispositivos en el mismo tailnet (cuenta personal, gratis
   hasta 100 dispositivos).
3. Verificar que el contenedor `investbot-bot` (red bridge por defecto,
   sin `network_mode: host`) alcanza la IP del tailnet de la PC:
   `docker compose exec investbot-bot curl -sS --max-time 5 http://<ip-tailscale-pc>:11434/api/tags`
   — si responde, no hace falta tocar `docker-compose.prod.yml`. Si falla,
   la alternativa mínima es `network_mode: host` para ese servicio (evaluar
   antes qué puertos del host VPS quedan expuestos a loopback, `ss -tlnp`).

### 2. Instalar Ollama + el modelo en la PC de Daniela — vía Docker

**Decisión de Daniela (2026-08-10): Ollama corre dentro de un contenedor
Docker en su PC, no instalado nativo, como capa extra de contención del
propio binario/proceso de Ollama** (defensa en profundidad adicional a las
4 capas de aislamiento de red de la sección 3 — esas 4 capas valen igual
esté Ollama nativo o en contenedor, porque filtran a nivel de red, no de
proceso). Costo aceptado explícitamente: en Docker Desktop para Mac, los
contenedores **no tienen acceso a la GPU (Metal)** — la inferencia corre
por CPU, más lenta que nativo. Daniela decidió priorizar el aislamiento
extra sobre la velocidad.

1. Instalar Docker Desktop si no está: `brew install --cask docker`, o
   https://docker.com/products/docker-desktop
2. Obtener la IP de Tailscale de la PC (con Tailscale ya instalado y
   logueado, paso 1 de arriba): `tailscale ip -4`
3. Levantar el contenedor de Ollama, publicando el puerto **únicamente**
   en la IP de Tailscale (no en `0.0.0.0`, no en todas las interfaces —
   este `-p <ip>:puerto:puerto` es el equivalente en Docker al bind
   address nativo de la sección 3.1):
   ```bash
   docker run -d --name ollama --restart unless-stopped \
     -v ollama:/root/.ollama \
     -p 100.x.y.z:11434:11434 \
     ollama/ollama
   ```
   (reemplazar `100.x.y.z` por la IP real del paso 2)
4. Bajar el modelo dentro del contenedor:
   ```bash
   docker exec ollama ollama pull qwen2.5:7b-instruct
   ```
   (alternativa configurable sin tocar código del bot: `llama3.1:8b`, vía
   `OLLAMA_MODEL`)
5. **Verificación empírica pendiente, no asumir que funciona:** confirmar
   que el firewall `pf` del host (sección 3, capa 4) sigue filtrando el
   tráfico correctamente cuando el puerto lo expone el proxy de Docker
   Desktop y no el proceso de Ollama directamente — mismo criterio de
   "verificar con curl real" que ya usa este proyecto en otros hallazgos.
   Si el filtrado de `pf` no alcanza al tráfico redirigido por Docker
   Desktop, la mitigación cae enteramente en las capas 1-3 (bind a IP de
   Tailscale + ACL + aprobación de dispositivos), que siguen siendo
   independientes de esto y deben verificarse igual.

### 3. Aislar el puerto 11434 — solo el VPS puede alcanzarlo

Bloqueante (pedido explícito de Daniela, revisión completa de `security` en
la spec, sección 2). 4 capas independientes, cada una debe fallar por
separado para romper el aislamiento:

1. **Bind address**: resuelto en la sección 2 vía `-p 100.x.y.z:11434:11434`
   de Docker (equivalente al `OLLAMA_HOST` nativo) — el puerto nunca queda
   publicado en `0.0.0.0` ni accesible por todas las interfaces.
2. **ACL de Tailscale**: política con `tagOwners` restringido a
   `autogroup:admin` para `tag:investbot-vps`/`tag:daniela-pc`, y una única
   regla `accept` de `tag:investbot-vps` hacia `tag:daniela-pc:11434` — sin
   ningún otro `accept` (default-deny implícito para el resto del tailnet).
   Se edita en la consola de administración de Tailscale → Access controls.
3. **Aprobación manual de dispositivos**: activar "require device
   authorization" en la consola de Tailscale (Device management) — un
   dispositivo nuevo no puede rutear tráfico hasta que Daniela lo apruebe.
4. **Firewall del host (macOS, `pf`)**: permitir el puerto 11434 solo desde
   la subred CGNAT de Tailscale (`100.64.0.0/10`), nunca por nombre de
   interfaz (`utunN` cambia entre reinicios):
   ```
   block in proto tcp from any to any port 11434
   pass in proto tcp from 100.64.0.0/10 to any port 11434
   ```

Verificación empírica antes de dar por cerrado (no alcanza con "debería
funcionar"): `curl` exitoso desde el VPS hacia `<ip-tailscale-pc>:11434`;
`curl` fallido desde un dispositivo fuera del tailnet; `curl` fallido desde
un dispositivo del tailnet sin el tag `investbot-vps`; `curl
127.0.0.1:11434` en la PC confirmando que el bind no quedó en loopback.

*(Opcional, no bloqueante: capa 2.5 de defensa en profundidad adicional —
reverse proxy con shared-secret delante de Ollama, ver la spec completa,
sección 2.5, si se quiere ese nivel extra de mitigación ante un fallo
simultáneo de las 4 capas de arriba.)*

**Nota de seguridad (`security`, sección 1):** se evaluó explícitamente
Cloudflare Tunnel como alternativa a Tailscale y se descartó — expone una
superficie orientada a Internet (hostname público + política HTTP-layer)
innecesaria para un enlace privado punto a punto entre 2 dispositivos de
una sola persona. No hace falta re-evaluar esto en el futuro salvo que
cambie el caso de uso (ej. exponer Ollama a más de 1 VPS o a colaboradores
externos).

### 4. Habilitar la feature en el `.env` del VPS

Setear las 2 variables obligatorias (ver `.env.example` para las 4
variables completas, incluidos los defaults):

```
OLLAMA_REWRITE_ENABLED=true
OLLAMA_BASE_URL=http://<ip-tailscale-pc>:11434
```

Reiniciar el contenedor para que tome la configuración nueva:
`docker compose -f docker-compose.prod.yml up -d --force-recreate`.

Con la PC apagada o Ollama no disponible, el bot sigue funcionando
exactamente igual que sin esta feature — fallback silencioso, sin error
visible, logueado a `INFO` (estado esperado, no una anomalía).
