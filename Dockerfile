# InvestBot — imagen de producción
# Usuario no-root, sin secretos embebidos en la imagen (entran solo en runtime
# vía env_file/volumen — ver docker-compose.prod.yml y criterios de `security`).

FROM python:3.12-slim

# Dependencias del sistema mínimas (certificados TLS para httpx/telegram).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 investbot \
    && useradd --uid 1000 --gid investbot --shell /bin/bash --create-home investbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Volumen de datos — SQLite persiste acá (ver docker-compose*.yml).
RUN mkdir -p /data && chown investbot:investbot /data

USER investbot

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    INVESTBOT_DB_PATH=/data/investbot.db

ENTRYPOINT ["python", "-m", "investbot.bot"]
