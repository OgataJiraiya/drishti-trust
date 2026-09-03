# Evaluator Demo Runbook

## Prerequisites and setup

Use Linux with Python 3.11–3.14, Python `venv`, Node.js 22+, and npm 10+. From a fresh
clone of `feat/full-system-integration` run `make setup`.

This creates a repository-local `.venv`, installs `backend/requirements.txt`, and runs
`npm ci`. Package installation may require an index; after installation the runtime and
demo require no outbound network.

## Canonical full-system demo

Run `make demo`. The concern scenario creates bounded local image and ONNX fixtures. It
produces one real finding from each assurance domain, submits four authenticated
ModuleRuns, and shows backend-computed complete coverage, assurance, and QUARANTINE.
It also proves replay idempotency, wrong-signature rejection, payload-tamper rejection,
audit validity, sealing, snapshot verification, and checkpoint verification.

Expected labels include:

```text
Assessment lifecycle: DRAFT -> ACTIVE -> SEALED
dataset_integrity: findings=1 authenticated=True
model_integrity: findings=1 authenticated=True
inference_integrity: findings=1 authenticated=True
distribution_shift: findings=1 authenticated=True
Backend coverage: 1.0
Backend disposition: QUARANTINE
Audit: VALID
Snapshot: VALID
Checkpoint: VALID
```

`make demo-clean` submits four authenticated empty ModuleRuns. Expected semantics are
coverage `0.0`, assurance `None (UNAVAILABLE)`, and disposition `REVIEW`. This is not a
safety claim.

Both commands create their SQLite database and Ed25519 keys in a private temporary
directory and remove it at exit. Rerunning is the safe demo reset. No private key,
bearer credential, or large JSON document is printed.

## Live backend and frontend

```bash
export DRISHTI_ADMIN_BEARER_TOKEN='choose-a-local-runtime-secret'
export DRISHTI_DATA_DIR="$PWD/runtime/data"
export DRISHTI_KEY_DIR="$PWD/runtime/keys"
make backend
```

In another terminal run `make frontend`, then visit `/`, `/findings`, `/distribution`,
`/graph`, `/reports`, and `/system` at `http://127.0.0.1:5173`. The live command
explicitly disables fixtures. For another local backend port, set
`VITE_DRISHTI_API_URL=http://127.0.0.1:PORT`.

Health is at `/health`; OpenAPI is at `/openapi.json`. A new data path initializes
automatically. Backend receipt/checkpoint keys are generated locally; demo module keys
are ephemeral and only public identities are registered.

## Validation and troubleshooting

Run `make test` for the complete matrix.

- Missing admin token: export a non-default local value for backend startup.
- UI reports offline: verify `/health`, the local port, and a `127.0.0.1`/`localhost` origin.
- OOD unavailable: optional CLIP resources are local-only and never downloaded.
- ONNX worker fails on another OS: bounded workers rely on Linux process/`RLIMIT` semantics.

The demo prints detector, backend-interaction, and total seconds. Exact time varies by
evaluator hardware.
