PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup demo demo-clean backend frontend test

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r backend/requirements.txt
	cd frontend && npm ci

demo:
	$(PY) scripts/demo_full_system.py --scenario concern

demo-clean:
	$(PY) scripts/demo_full_system.py --scenario clean

backend:
	@test -n "$(DRISHTI_ADMIN_BEARER_TOKEN)" || (echo "DRISHTI_ADMIN_BEARER_TOKEN is required" >&2; exit 2)
	$(PY) -m uvicorn backend.main:app --host "$${DRISHTI_HOST:-127.0.0.1}" --port "$${DRISHTI_PORT:-8000}"

frontend:
	cd frontend && VITE_DRISHTI_DEMO_MODE=false npm run dev -- --host "$${DRISHTI_FRONTEND_HOST:-127.0.0.1}"

test:
	$(PY) -m pytest -q
	$(PY) -m compileall -q backend drishti_sdk modules scripts
	cd frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
