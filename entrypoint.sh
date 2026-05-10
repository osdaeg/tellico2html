#!/bin/sh
# entrypoint.sh
set -e

echo "=== Tellico2HTML - Entrypoint ==="
mkdir -p /app/static/images /app/static/books

if [ "${FORCE_REGEN}" = "true" ]; then
    echo "[entrypoint] Modo FORCE_REGEN..."
    python /app/processor.py --force
else
    echo "[entrypoint] Ejecutando procesador..."
    python /app/processor.py
fi

echo "[entrypoint] Procesamiento completado."

PORT="${TELLICO2HTML_INTERNAL_PORT:-8000}"
echo "[entrypoint] Iniciando FastAPI en puerto ${PORT}..."
exec uvicorn api:app --host 0.0.0.0 --port "${PORT}" --log-level info
