#!/usr/bin/env bash
# Startup script for both Docker and Render. Validates configuration before
# handing off to uvicorn so misconfiguration fails fast with a clear message
# instead of an obscure crash later.
set -euo pipefail

echo "AI Study Assistant — starting up"

python scripts/validate_env.py

exec uvicorn app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
