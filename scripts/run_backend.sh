#!/usr/bin/env sh
set -eu
exec python -m uvicorn backend.main:app --host "${DRISHTI_HOST:-127.0.0.1}" --port "${DRISHTI_PORT:-8000}"
