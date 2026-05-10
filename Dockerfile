# ─── Tellico2HTML ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="Tellico2HTML"
LABEL description="Convierte colecciones Tellico a páginas HTML estáticas"

# Dependencias del sistema (mínimas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY processor.py .
COPY enricher.py .
COPY api.py .
COPY server.py .
COPY entrypoint.sh .
RUN chmod +x /app/entrypoint.sh

# El volumen /app/static es compartido con Nginx
VOLUME ["/app/static"]

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
