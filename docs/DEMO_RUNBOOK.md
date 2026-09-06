# Evaluator Demo Runbook

## Prerequisites and setup

Use Linux with Python 3.11–3.14, Python `venv`, Node.js 22+, and npm 10+. From a fresh
clone of `main` run `make setup`.

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

Start a persistent local backend:

```bash
export DRISHTI_ADMIN_BEARER_TOKEN='choose-a-local-runtime-secret'
export DRISHTI_DATA_DIR="$PWD/runtime/data"
export DRISHTI_KEY_DIR="$PWD/runtime/keys"
export DRISHTI_PORT=8000
make backend
```

In another terminal, point the evaluator demo at that already-running backend and seed a
real sealed concern assessment into its persistent database:

```bash
export DRISHTI_ADMIN_BEARER_TOKEN='choose-a-local-runtime-secret'
export DRISHTI_API_URL=http://127.0.0.1:8000
make demo-live
```

`make demo-live` runs the same four real detectors and signed ModuleRuns as the canonical
concern demo, but it does not start or delete a temporary backend. The command prints the
new assessment ID and ends with `Live UI: select historical assessment <ID>`.

Then start the real frontend in another terminal:

```bash
export VITE_DRISHTI_API_URL=http://127.0.0.1:8000
make frontend
```

Visit `http://127.0.0.1:5173`, choose the newly printed sealed assessment from the
assessment selector, and inspect `/`, `/findings`, `/distribution`, `/graph`, `/reports`,
and `/system`. Live mode explicitly disables fixtures; there must be no `DEMO DATA`
marker. A sealed assessment is historical, so `/api/assessments/current` may correctly
return 404 until another assessment is ACTIVE.

For another local port, set both `DRISHTI_API_URL` and `VITE_DRISHTI_API_URL` to the same
loopback URL. Health is at `/health`; OpenAPI is at `/openapi.json`. A new data path
initializes automatically. Backend receipt/checkpoint keys are generated locally; demo
module keys are ephemeral and only public identities are registered.

## Validation and troubleshooting

Run `make test` for the complete matrix.

- Empty live UI / `No active assessment`: run `make demo-live`, refresh, then select the
  printed historical assessment ID. The canonical `make demo` intentionally deletes its
  temporary backend state and therefore does not populate the separately started UI.
- Missing admin token: export a non-default local value for backend startup and
  `make demo-live`.
- UI reports offline: verify `/health`, the local port, and a `127.0.0.1`/`localhost` origin.
- OOD unavailable: optional CLIP resources are local-only and never downloaded.
- ONNX worker fails on another OS: bounded workers rely on Linux process/`RLIMIT` semantics.

The demo prints detector, backend-interaction, and total seconds. Exact time varies by
evaluator hardware.

## Organization-provided evidence is a separate workflow

`make demo`, `make demo-clean`, and `make demo-live` use controlled evaluator fixtures. They do not test an organization’s model. For real evidence, start the live workstation with intake enabled and use **New Assessment**. See [Organization Assessment](ORGANIZATION_ASSESSMENT.md) for capability generation, ONNX intake, reference/behavioral options, signed evidence and cleanup. Do not expose the admin bearer through frontend configuration.

## Repeated live demonstrations

Each orchestration uses fresh ephemeral module keys with public-key-derived identifiers,
so concern and clean assessments can run consecutively on the same backend without a
key-identifier collision. Zero-Finding runs still produce no scored coverage.
Model registry registration and revocation require the server-side administrative
bearer; never place that bearer in browser JavaScript.
