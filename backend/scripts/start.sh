#!/bin/sh
set -e

# Bring the schema up to head before serving. Fact tables are append-only, so
# migrations here only ever add structure — they never rewrite history.
uv run alembic -c /app/backend/alembic.ini upgrade head

exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
