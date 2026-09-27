#!/bin/sh
set -e

# If arguments are passed, execute them (e.g. celery worker, alembic)
if [ $# -gt 0 ]; then
    exec "$@"
else
    # Default to uvicorn API gateway
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
fi