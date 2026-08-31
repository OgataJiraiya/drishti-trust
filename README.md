# DRISHTI-TRUST

**SIH 2026 – Problem Statement 26228**  
Trustworthy Computer Vision Integrity Assurance for Data, Models and Inference Outputs in Multi-Contributor Pipelines.

Organization: **Ministry of Defence (MoD) / Indian Army (DGIS)**

DRISHTI-TRUST is an offline, air-gapped assurance framework for computer-vision pipelines. The project combines training-data integrity, model integrity, inference provenance, distribution-shift analysis, analyst-facing evidence, and a tamper-evident audit trail.

## Team modules

| Branch | Area | Primary responsibility |
|---|---|---|
| `feat/data-integrity` | Data Integrity | label issues, duplicates, OOD, trigger/anomaly evidence, contributor risk |
| `feat/model-integrity` | Model Integrity | behavioural fingerprinting, backdoor/anomaly assessment, model evidence |
| `feat/security-backend` | Security / Provenance | signed inference receipts, replay/tamper/substitution detection, evidence API, audit chain |
| `feat/drift-ui` | Distribution Shift + UI | drift assessment, analyst dashboard, final visualization/integration |

`main` is the stable integration branch. Do not develop directly on `main`.

## Shared Finding Schema v1

Every module must emit findings using this frozen outer contract:

```json
{
  "finding_id": "F-DATA-001",
  "module": "dataset_integrity",
  "asset_type": "sample",
  "asset_id": "sample:img_0042",
  "category": "NEAR_DUPLICATE",
  "severity": "HIGH",
  "confidence": 0.91,
  "reason": "Sample is near-identical to 37 other samples.",
  "evidence": [
    "phash_distance=3",
    "cluster_id=17"
  ],
  "recommendation": "REVIEW",
  "limitations": [
    "Similarity heuristic; visually similar legitimate images may be flagged."
  ]
}
```

Official module values:

- `dataset_integrity`
- `model_integrity`
- `inference_integrity`
- `distribution_shift`

Severity: `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`  
Recommendation: `ACCEPT`, `REVIEW`, `QUARANTINE`, `REJECT`

The contract is documented in `docs/INTEGRATION_CONTRACT.md` and `shared/schemas/`.

## Repository layout

```text
drishti-trust/
├── backend/                     # shared backend / API integration
├── modules/
│   ├── data_integrity/
│   ├── model_integrity/
│   ├── inference_integrity/
│   └── distribution_shift/
├── frontend/                    # analyst-facing dashboard
├── shared/
│   └── schemas/                 # frozen cross-module contracts
├── docs/                        # architecture and integration notes
├── scripts/                     # reproducible local/demo scripts
├── tests/                       # cross-module/integration tests
└── .github/                     # PR and issue templates
```

## Team workflow

1. Clone the repository.
2. Checkout your assigned feature branch.
3. Pull before starting work.
4. Commit small, understandable changes.
5. Push only to your feature branch.
6. Open a pull request into `main` when a coherent feature is ready.
7. Another teammate reviews the PR before merge.
8. Never commit keys, datasets, generated databases, large model artifacts, `.env` files, or secrets.

See `CONTRIBUTING.md` and `docs/TEAM_WORKFLOW.md` before starting.

## Core design principles

- Offline / air-gapped runtime
- No cloud or external API dependency
- Unknown is never silently treated as safe
- Authenticity is not the same as trustworthiness
- Explain every flag with evidence, confidence, limitation, and recommended action
- Use standard cryptography; never invent cryptographic primitives
- Preserve model/data formats without unsafe deserialization where verification only requires hashing
- Keep the audit trail tamper-evident and limitations explicit

## Local API startup

Run the backend from the repository root. On first use, install its dependencies and
start Uvicorn through the active Python interpreter so the command does not depend on a
standalone `uvicorn` executable being on `PATH`:

```bash
cd /home/kali/drishti-trust
python -m pip install -r backend/requirements.txt
export DRISHTI_ADMIN_BEARER_TOKEN="replace-with-runtime-secret"
python -m uvicorn backend.main:app \
  --host 127.0.0.1 \
  --port 8000
```

The API is at `http://127.0.0.1:8000`, Swagger documentation at `/docs`, and the health
check at `/health`. On first startup the application creates its local SQLite database
under `backend/data/` and a receipt-signing Ed25519 key pair under `backend/keys/`.
These runtime artifacts are ignored and must not be committed. Production/offline
deployments must provision and protect signing keys through their own trust ceremony.

## Trust Boundary and Access Control

The normal module path is `module -> Ed25519 -> POST /api/integration/signed-runs`.
Producer and active-key authorization, the signature, and the complete canonical run
are verified before atomic ingestion. Signed submissions do not use either bearer token.

Producer/key registration and revocation require a runtime administrator bearer set in
`DRISHTI_ADMIN_BEARER_TOKEN`. No default credential exists; if it is unconfigured,
administrative mutations fail closed with HTTP 503. The token is neither stored in
SQLite nor placed in responses or audit payloads.

`POST /api/integration/runs` and `POST /api/evidence` are trusted-internal compatibility
APIs, not the normal provenance path. They are disabled by default. To enable them,
both settings below are required, and callers must supply the internal bearer:

```bash
export DRISHTI_ALLOW_UNSIGNED_INGESTION="true"
export DRISHTI_INTERNAL_INGEST_BEARER_TOKEN="replace-with-separate-runtime-secret"
```

`GET /api/summary` defaults to the active assessment and trusted view: only its
Ed25519-attested findings contribute. An explicit `assessment_id` retrieves historical
scope. With no active assessment, compatibility is preserved and clearly labelled
`GLOBAL_LEGACY`. `trust_scope=all` includes trusted-internal runs within the selected
assessment; direct and unscoped evidence never enters an assessment summary.

This bearer control is MVP access control, not multi-user RBAC. Runtime bearer
provisioning remains an operational trust ceremony. Possession of an approved producer
private key authenticates producer identity and exact bytes, not detector correctness.
Audit appends remain post-state transactions, and clean audit-tail truncation needs a
future external/signed checkpoint. SQLAlchemy `create_all` is not a migration system.
Assurance scoring remains heuristic. Snapshots are locally hashed but are not externally
timestamped or notarized.

## Assessment lifecycle

An administrator creates a `DRAFT` assessment and explicitly activates it. At most one
is `ACTIVE`; activation never silently replaces another. Modules put the optional
`assessment_id` inside `ModuleRunSubmission`, so canonical Ed25519 signing binds the
assessment, run identity, producer, findings, and finding order. Active-state validation,
immutable findings, run provenance, and membership commit in one SQLite transaction.

```bash
curl -H "Authorization: Bearer $DRISHTI_ADMIN_BEARER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"assessment_id":"ASSESS-2026-001","name":"Friday remediation check","metadata":{}}' \
  http://127.0.0.1:8000/api/assessments
curl -X POST -H "Authorization: Bearer $DRISHTI_ADMIN_BEARER_TOKEN" \
  http://127.0.0.1:8000/api/assessments/ASSESS-2026-001/activate
```

Modules sign a run containing `"assessment_id":"ASSESS-2026-001"` and submit it to
`/api/integration/signed-runs`. `/api/summary` then describes that active assessment.
Sealing is terminal and idempotent:

```bash
curl -X POST -H "Authorization: Bearer $DRISHTI_ADMIN_BEARER_TOKEN" \
  http://127.0.0.1:8000/api/assessments/ASSESS-2026-001/seal
curl http://127.0.0.1:8000/api/assessments/ASSESS-2026-001/snapshot/verify
```

Seal writes one canonical authenticated summary, its SHA-256 hash, and a deterministic
commitment to sorted `{run_id, request_hash, authentication_mode}` entries before making
the assessment `SEALED`. No later run can attach and there is no reopen/delete API.
Exact immutable findings may be reused in different assessments through distinct runs;
changed evidence still requires a new finding ID.

M14 uses additive tables and SQLAlchemy `create_all`, not migrations. A clean/new MVP
database is recommended; dangerous automatic schema rewriting is intentionally absent.

## Deadline

Target integrated working demo: **15 September 2026**.
