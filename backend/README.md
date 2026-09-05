# SIH 26228 — Offline Inference Provenance Backend

This workspace contains the completed cryptographic provenance and replay-protection
vertical slices: canonical SHA-256 digests, Ed25519-signed task-agnostic inference
receipts, durable SQLite storage, evidence-based integrity verification, and atomic
freshness/replay enforcement.

## Run locally

From the repository root, install dependencies once and invoke Uvicorn as a Python
module. Administrative credentials are runtime-only and have no source-code default:

```bash
cd drishti-trust
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
export DRISHTI_ADMIN_BEARER_TOKEN="replace-with-runtime-secret"
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The API is `http://127.0.0.1:8000`; Swagger is at `/docs` and health at `/health`.
Runtime uses only local files:
`backend/data/provenance.sqlite3` and the Ed25519 development keys in `backend/keys/`.
Private key files are ignored. Production must provision keys through an appropriate
secure offline key-management process or HSM.

Unsigned compatibility ingestion and direct evidence mutation are disabled by default.
Only a deliberate trusted-internal deployment may enable both settings:

```bash
export DRISHTI_ALLOW_UNSIGNED_INGESTION="true"
export DRISHTI_INTERNAL_INGEST_BEARER_TOKEN="replace-with-separate-runtime-secret"
```

The preferred module integration endpoint is `/api/integration/signed-runs`, which uses
Ed25519 producer authentication and requires no bearer credential. See the root README
section “Trust Boundary and Access Control” for the complete boundary and limitations.

## Inference API

- `GET /health`
- `POST /api/inference/receipt`
- `POST /api/inference/verify`
- `POST /api/inference/accept`
- `GET /api/inference/{receipt_id}`
- `GET /api/inference?page=1&page_size=50`

Creation accepts base64 image bytes, a precomputed model SHA-256 (the backend never
deserializes a model), JSON preprocessing/configuration, and arbitrary JSON inference
output. Verification accepts the signed receipt plus independently supplied artifacts.
An omitted artifact is explicitly `UNAVAILABLE` and cannot yield `ACCEPT`.

### Verification is not acceptance

`POST /api/inference/verify` is read-only. It verifies the signature and independently
supplied input/model/preprocessing/config/output digests. It never consumes a nonce or
changes replay state, so repeatedly verifying a valid receipt does not turn it into a
replay.

`POST /api/inference/accept` processes the receipt as a new inference event. In addition
to the integrity checks, it enforces a fresh UTC timestamp, unique nonce, unique exact
receipt hash, and a globally increasing sequence. Successful state is stored atomically
in `accepted_inferences`; receipt archival in `inference_receipts` does not imply prior
acceptance. SQLite uniqueness constraints provide a final concurrent-replay guard.

The single-node sequence scope is the global signing/inference stream. Sequences must be
strictly greater than the highest accepted sequence. Gaps are allowed because they can
result from receipts being generated but never accepted; gaps alone are not classified
as attacks.

Default freshness limits are 300 seconds maximum age and 30 seconds future clock skew.
They are configurable through the application `Settings` object. A genuine signature
proves authenticity, not freshness: accepting the same signed receipt twice produces a
valid signature check but `NONCE_REUSE`/`REPLAY_DETECTED` findings and `REJECT`.

## Approved Model Registry

The registry answers one deliberately narrow question: is the supplied artifact exactly
the approved sequence of bytes for this `model_id`? SHA-256 is the identity anchor.
Filename, path, extension, declared format, display metadata, and architecture name are
not identity evidence.

Two offline registration modes make the source of trust explicit:

- `POST /api/models/register` records a caller-provided digest and labels the entry
  `PROVIDED_DIGEST`; it does not claim the backend inspected model bytes.
- `POST /api/models/register/artifact` streams a bounded raw request body, calculates
  SHA-256 locally, discards the bytes, and labels it `HASHED_ARTIFACT`.

Verification has matching `POST /api/models/verify` digest and
`POST /api/models/verify/artifact` opaque-byte modes. Uploaded bytes are never parsed,
deserialized, imported into a model framework, or executed. Artifact endpoints accept
the model bytes as the raw request body, with model metadata supplied as query fields.
The default size limit is 1 GiB and is configurable with `Settings.max_model_bytes`.

Registration is immutable by default: the same `model_id` and digest is idempotent, but
attempting to bind that ID to another digest returns HTTP 409. A new model version should
use a new explicit model ID. `POST /api/models/{model_id}/revoke` removes authorization
without changing the historical trust anchor.

Unknown models are quarantined with `UNREGISTERED_MODEL`. Revoked models are quarantined
with `MODEL_REVOKED` even when their bytes still match. This preserves the principles
that unknown does not mean trusted and authenticity does not imply current authorization.

Example substitution demo:

1. Stream `genuine_model.onnx` to `/api/models/register/artifact` for `MODEL-001`.
2. Verify those bytes and receive `model_digest=VALID`, `ACCEPT`.
3. Replace the file contents while retaining the name `genuine_model.onnx`.
4. Verify again and receive `MODEL_SUBSTITUTION`, `CRITICAL`, `QUARANTINE`.

Model digest verification proves artifact identity. It does not prove that the approved
model is free of backdoors or unsafe behavior. Semantic and behavioural assessment is
the responsibility of the Model Integrity module.

## Tamper-Evident Audit Chain

Security actions append immutable application records to the local `audit_log` table.
Every record contains a contiguous sequence, deterministic `AUD-########` identifier,
UTC timestamp, event/asset fields, canonical JSON payload, the preceding record hash,
and its own SHA-256 hash. There are no update or delete audit APIs.

The genesis convention is explicit: the first record's `previous_hash` is 64 lowercase
zero characters. Each `current_hash` is SHA-256 over canonical JSON containing exactly:

```text
audit_id, sequence, event_type, asset_type, asset_id,
payload, normalized UTC timestamp, previous_hash
```

`current_hash` is not included in its own hash input. Appends take a SQLite
`BEGIN IMMEDIATE` write reservation before reading the current head, so concurrent
writers receive unique contiguous sequences and link to the correct predecessor.
Audit state persists in the same offline SQLite database.

Integrated events include `MODEL_REGISTERED`, `MODEL_VERIFIED`, `MODEL_REVOKED`,
`MODEL_SUBSTITUTION_DETECTED`, `INFERENCE_RECEIPT_CREATED`, `INFERENCE_VERIFIED`,
`INFERENCE_ACCEPTED`, `OUTPUT_TAMPERING_DETECTED`, `SIGNATURE_INVALID`, and
`REPLAY_DETECTED`. Payloads contain hashes, identifiers, dispositions, and check states;
they never contain signing private keys or complete input/model binary content.

Receipt `/verify` calls append forensic verification events, but remain read-only with
respect to accepted nonce and sequence state: verification is still not acceptance.
`GET /api/audit/verify` is itself read-only and deliberately does not append a failure
event if the chain is corrupt, avoiding recursion into an already untrusted chain.

Audit endpoints:

- `GET /api/audit` lists records in sequence order with pagination and optional event or
  asset filters.
- `GET /api/audit/{audit_id}` retrieves one record.
- `GET /api/audit/verify` recomputes the complete chain and reports the earliest
  detectable corruption with stored and recomputed evidence.

Internal payload changes, hash/link changes, inserted or reordered rows, sequence
changes, and deletion of interior records are detectable. To demonstrate, directly
change an old `payload_json` value in a controlled test database without recomputing its
hash, then call `/api/audit/verify`; the result is `COMPROMISED` with
`AUDIT_RECORD_TAMPERING`.

A hash chain is tamper-evident, not magically tamper-proof. This MVP intentionally does
not implement a signed external checkpoint. If an attacker can directly delete a clean
suffix of the database, the remaining valid prefix cannot prove that later tail records
once existed. Detecting silent tail truncation requires an external or signed trusted
head checkpoint in a future milestone.

Existing services commit their security-state change and immediately append the audit
event in a separate serialized transaction. This avoids destabilizing proven receipt,
replay, and registry transactions, but leaves a small prototype crash window between
the state commit and audit append. Audit append operations themselves are atomic.

## Common Team Finding Contract

All four analysis modules may use completely different internal algorithms, but they
must emit findings using the frozen Finding Schema v1 envelope. The exact finalized
example is:

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

The fields identify the immutable evidence record, producing module, affected asset,
general finding category, severity and confidence, human-readable reason, supporting
evidence, recommended disposition, and known limitations. Official modules are
`dataset_integrity`, `model_integrity`, `inference_integrity`, and
`distribution_shift`. Severity is one of `INFO`, `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`;
recommendation is `ACCEPT`, `REVIEW`, `QUARANTINE`, or `REJECT`.

Evidence is intentionally a list of human-readable strings in Finding Schema v1. It is
never interpreted, evaluated, or converted into an object. Evidence requires at least
one bounded supporting string for a substantive finding. Limitations are also an ordered
string list and may be empty when no additional limitation applies. No timestamp or
database metadata is required or returned as part of the shared contract.

Evidence endpoints (`POST` is a disabled-by-default trusted-internal compatibility API):

- `POST /api/evidence` ingests one Finding document.
- `GET /api/evidence` lists findings with pagination and optional module, asset type,
  asset ID, category, severity, and recommendation filters.
- `GET /api/evidence/{finding_id}` returns the exact shared Finding fields.

Finding IDs are immutable evidence identities. Submitting the same `finding_id` and
identical canonical body returns `EXISTS` without creating another row or audit event.
Submitting that ID with any changed Finding field returns HTTP 409 and never overwrites
the original. Every newly created finding appends a compact `EVIDENCE_CREATED` audit
event, and the complete audit chain remains independently verifiable.

Person-3 replay, model-substitution, and output-tampering adapters convert existing
detailed verification findings into this same envelope with deterministic IDs. They are
not automatically persisted in Milestone 10: explicit `/api/evidence` ingestion remains
the integration boundary. This preserves existing security-response and audit timelines
and prevents evidence-store behavior from changing a security decision. A later trusted
orchestration layer can call the adapters and evidence service without changing the
frozen contract.

## Assurance Scoring

`GET /api/summary` derives a read-only, dashboard-ready assurance view from current
Finding Schema v1 records and the existing audit-chain verifier. The score is a
transparent prioritization heuristic; it is not a probability of compromise and does
not replace expert investigation.

Severity penalties are centralized and fixed:

| Severity | Penalty |
|---|---:|
| INFO | 2 |
| LOW | 8 |
| MEDIUM | 20 |
| HIGH | 45 |
| CRITICAL | 75 |

Each finding has `individual_penalty = severity_penalty × confidence`. Findings are
grouped by category within their module. Only the maximum individual penalty in each
category contributes:

```text
category_penalty = max(individual_penalty in category)
module_total_penalty = min(100, sum(category_penalty per category))
module_assurance_score = 100 - module_total_penalty
```

Worked example: `NEAR_DUPLICATE`, severity `HIGH`, confidence `0.91` has penalty
`45 × 0.91 = 40.95`; therefore its single-category dataset score is
`100 - 40.95 = 59.05`, displayed as `59.1`. The response exposes every contributing
constant, source finding, category penalty, total penalty, and formula.

Score bands are `GOOD` for 90–100, `WATCH` for 70–<90, `REVIEW` for 40–<70, and
`HIGH_RISK` below 40. Their base dispositions are `ACCEPT`, `REVIEW`, `REVIEW`, and
`QUARANTINE`. Disposition precedence is explicit:
`ACCEPT < REVIEW < QUARANTINE < REJECT`.

Module weights are:

| Module | Weight |
|---|---:|
| dataset_integrity | 0.25 |
| model_integrity | 0.35 |
| inference_integrity | 0.25 |
| distribution_shift | 0.15 |

Coverage is the sum of weights for modules with evidence. Assessed-module scores are
normalized while coverage remains explicit:

```text
weighted_sum = sum(module_score × module_weight for assessed modules)
available_weight = sum(module_weight for assessed modules)
overall_score = weighted_sum / available_weight
```

Zero coverage yields `UNAVAILABLE` with a null score. Partial coverage yields
`PROVISIONAL` and can never produce `ACCEPT`. Complete coverage yields `COMPLETE`.
No findings is not successful assessment: every empty module is `UNKNOWN`, has a null
score, and requires `REVIEW`.

### Asset versus system disposition

Finding Schema v1 recommendations primarily describe the affected asset or event. They
are not copied blindly into the overall pipeline disposition. In particular, `REJECT`
on one inference rejects that inference but does not globally reject the whole system.
The module and system effects are controlled by explicit centralized policy.

Critical `OUTPUT_TAMPERING` and `REPLAY_DETECTED` produce asset disposition `REJECT`,
module disposition `QUARANTINE`, and system disposition `QUARANTINE`. Critical
`MODEL_SUBSTITUTION` and `BACKDOOR_BEHAVIOR` quarantine the affected model, module, and
system. A compromised audit chain also requires at least system quarantine. No current
policy condition globally rejects the entire pipeline; global `REJECT` is reserved for
a future explicitly defined system-wide condition.

A standalone asset recommendation of `REJECT` creates review pressure at module/system
level but cannot itself force global rejection. `QUARANTINE` recommendations escalate
globally only for pipeline-critical assets or explicit category rules. A high average
cannot override critical evidence. Audit infrastructure errors are `UNAVAILABLE`, never
silently valid. The summary exposes critical override details containing category,
asset ID, asset disposition, and system effect.

The summary includes stable global and per-module severity/recommendation counts,
category counts, five deterministically ordered top findings per module, ten latest
findings, deduplicated reported limitations, score explanations, coverage, critical
overrides, and audit-integrity status. It writes no evidence, audit records, replay
state, model state, or persistent summary table.

## Cross-Module Integration Gate

All four detector modules should submit signed batches through the authenticated module
boundary. The unsigned route remains only for explicitly enabled compatibility use:

- `POST /api/integration/signed-runs` authenticates and atomically ingests a module run.
- `POST /api/integration/runs` is the disabled-by-default trusted-internal route.
- `GET /api/integration/runs/{run_id}` retrieves immutable run metadata.
- `GET /api/integration/runs` lists runs with pagination and optional module filtering.

Run metadata (`run_id`, module, producer, and optional producer version) remains outside
Finding Schema v1. The public Finding envelope is unchanged. A run contains 1–100
findings, every finding must use the outer run's official module, and finding IDs must
be unique within the request. Unknown fields, blank identifiers, invalid findings, and
module mismatches fail validation before database mutation.

The complete submission is canonically serialized and identified by a SHA-256 request
hash. Finding order is part of that request identity. Ingestion acquires a SQLite
`BEGIN IMMEDIATE` write reservation, checks the run identity and every finding identity,
then stages all new findings and the run record before one commit. Consequently, a
conflict at any position—including finding 100—persists none of that run's changes.

Idempotency and immutability rules are:

- same `run_id` and exact canonical request: `EXISTS`, with no new state or audit event;
- same `run_id` with changed request content: HTTP 409;
- globally existing identical Finding JSON: safely reused and counted as existing;
- existing `finding_id` with any changed Finding field: HTTP 409 for the whole batch.

After a newly created run commits, each newly stored finding receives the existing
`EVIDENCE_CREATED` audit event and the run receives one `MODULE_RUN_INGESTED` event with
bounded metadata and counts. Exact reruns do not duplicate either event. As documented
for the existing audit architecture, these audit appends occur immediately after the
integration transaction in separate serialized transactions. A process failure in that
small post-commit window can therefore leave committed security state without its audit
event; the gate does not claim cross-transaction atomicity that SQLite does not provide
in the current architecture.

## Authenticated Module Provenance

The authenticated integration boundary closes the trust gap in a self-declared
`producer` string. Administrators register an approved producer with exactly one module,
then register one or more Ed25519 **public** keys for it. The backend never requests or
stores producer private keys.

Producer administration endpoints are:

- `POST /api/producers`, `GET /api/producers`, and `GET /api/producers/{producer_id}`;
- `POST /api/producers/{producer_id}/keys` and `GET /api/producers/{producer_id}/keys`;
- `POST /api/producers/{producer_id}/revoke`;
- `POST /api/producers/{producer_id}/keys/{key_id}/revoke`.

Public-key registration parses the PEM as an Ed25519 public key, rewrites it to canonical
SubjectPublicKeyInfo PEM, and fingerprints the 32 raw public-key bytes with SHA-256. It
rejects private keys, malformed PEM, other algorithms, changed immutable key IDs, and
cross-producer key reuse. Producers have `APPROVED`/`REVOKED` status; keys have
`ACTIVE`/`REVOKED` status. Revoking one key leaves other active rotation keys usable,
while revoking a producer disables every future submission without deleting history.

Modules submit the strict wrapper `{run, key_id, signature}` to
`POST /api/integration/signed-runs`. Signing bytes are exactly:

```text
canonical_json_bytes(run payload, omitting assessment_id only when it is absent)
```

The existing request hash is SHA-256 over those same bytes. Finding order therefore
remains signed and semantically significant. The service checks producer existence and
approval, exact producer/module binding, key ownership and active status, raw-key
fingerprint consistency, base64 encoding, and the Ed25519 signature before invoking the
existing atomic integration transaction. Authentication failures create no findings,
module run, or success audit events.

Module-side Python signing uses the shared helper, avoiding divergent serialization:

```python
from backend.integrations.signing import sign_module_run
from backend.schemas.integration import ModuleRunSubmission

run = ModuleRunSubmission.model_validate(run_document)
signature = sign_module_run(run, private_key)  # private_key stays with the module
signed_document = {"run": run.model_dump(mode="json"), "key_id": key_id,
                   "signature": signature}
```

New authenticated runs emit bounded `MODULE_RUN_AUTHENTICATED` evidence and an enriched
`MODULE_RUN_INGESTED` event containing the exact stored `request_hash`, key ID, raw-key
fingerprint, and `authenticated=true`. Neither signatures nor public/private key material
is copied into audit payloads. Exact reruns remain idempotent and do not duplicate these
success events.

`POST /api/integration/runs` remains available for backward-compatible internal/trusted
ingestion but does not establish producer authenticity. External module handoff should
use the signed endpoint. Finding Schema v1 is unchanged; all authentication metadata is
outside its frozen envelope.

Operational limitations: administrator bearer auth is MVP access control, offline key
provisioning is a trust ceremony, possession of an approved private key authenticates
identity but not detector correctness, and key compromise requires explicit revocation.
Durable state events use the transactional audit outbox described below.

## Assessment Lifecycle

Administrative assessment create, activate, and seal mutations reuse the existing
fail-closed administrator bearer. Read routes provide filtering and pagination, current
assessment discovery, scoped runs, immutable snapshots, and snapshot verification.

The lifecycle is `DRAFT -> ACTIVE -> SEALED`; sealed is terminal. SQLite write
reservations and a partial unique index enforce at most one active assessment. A scoped
run may join only an active assessment, and membership commits atomically with findings,
run identity, and authentication provenance. Legacy runs without `assessment_id` remain
valid and visibly return `null` membership.

Summary responses identify `EXPLICIT_ASSESSMENT`, `ACTIVE_ASSESSMENT`, or
`GLOBAL_LEGACY` scope. Within an assessment, `authenticated` selects only Ed25519 runs;
`all` also selects trusted-internal runs. Direct evidence, unscoped runs, and other
assessments are excluded. Scoring and disposition policy are unchanged.

Seal persists canonical summary JSON and SHA-256 plus a deterministic run-set commitment
over sorted run ID, request hash, and authentication mode entries. Snapshot GET never
recomputes it; snapshot verification checks payload and current membership integrity.
Create, activate, and seal transactionally stage bounded audit intents, and scoped run
events carry the assessment ID.

`create_all` is not a migration framework, so M14 adds tables without automatic schema
migration; use a clean/new MVP database. Admin bearer auth is not RBAC, key provisioning
is an operational ceremony, identity does not prove detector correctness, scoring is
heuristic, checkpoints protect only through their retained head, and snapshots lack
external timestamping/notarization.

## Audit Durability and Signed Checkpoints

`audit_outbox` stores canonical, SHA-256 committed `PENDING`/`DELIVERED` intent. Security
state and intent share a transaction. Ordered recovery appends the legacy-compatible
audit row and marks delivery atomically; startup, normal post-commit delivery, and the
admin drain endpoint can recover pending work without duplicates. Integrity failures
remain pending and make operational health degraded.

Dedicated Ed25519 checkpoint keys use
`audit_checkpoint_signing.private.pem`/`.public.pem` (ignored runtime files). The signing
bytes are exactly `DRISHTI-TRUST:AUDIT-CHECKPOINT:v1\n` followed by canonical checkpoint
JSON. `checkpoint_hash` is SHA-256 of that JSON, and each stored checkpoint names the
previous payload hash. Missing/inconsistent keys fail closed; keys are generated only
for a fresh database with no checkpoints.

Routes include outbox status/admin drain; checkpoint create/list/latest/get; stored and
external verification; and public-key export. Checkpoint creation rejects an empty audit
chain and will not knowingly omit pending intents. External verification needs no
checkpoint database row.

An externally retained checkpoint detects deletion below its sequence, but cannot prove
that records created after it survive. Retain the newest bundle and separately pin its
public key/fingerprint outside SQLite. Total database destruction, checkpoint-private-key
compromise, absent trusted timestamps/HSMs, bearer-only admin access, and `create_all`
schema management remain known limitations.

## Security Principles and Limitations

- Authenticity != trustworthiness. An authentic receipt can originate from an unsafe or
  backdoored model.
- Valid signature != freshness. Replay protection is enforced only at `/accept`.
- Filename equality != model identity. SHA-256 of opaque bytes establishes identity.
- Digest match != behavioural safety. Semantic assessment belongs to Model Integrity.
- No findings != successful assessment. Missing modules remain `UNKNOWN`.
- Numeric assurance score != probability of compromise. It is a transparent heuristic.
- Hash chain = tamper-evident, not tamper-proof. A separately retained signed checkpoint
  detects clean truncation only through its referenced sequence.

The backend operates without Internet, cloud APIs, external databases, model services,
blockchain networks, or arbitrary deserialization. Runtime signing keys and SQLite data
remain local and are excluded from version control. Outbox intent durability does not
survive total database destruction, and cryptographic identity cannot establish model
behavioural safety or detector correctness.

## Test

```bash
pytest -q backend/tests
```

This implementation deliberately stops before frontend/dashboard implementation,
packaged demo scripts, Docker, and deployment.
