# Organization assessment on a local workstation

There are two separate workflows. `make demo` uses controlled fixtures. Live **New Assessment** accepts organization-provided evidence, runs existing detectors locally, authenticates signed ModuleRuns, and seals a backend assessment. Runtime analysis requires no outbound Internet. Install dependencies before entering an air gap.

## Start the live workstation

Run one backend process, bound to loopback. Keep the administrative credential in the terminal environment only. Use a private data directory; do not share it between backend processes.

```sh
export DRISHTI_DATA_DIR=/tmp/drishti-organization/data
export DRISHTI_KEY_DIR=/tmp/drishti-organization/keys
export DRISHTI_INTAKE_ORIGIN=http://127.0.0.1:5173
# Set DRISHTI_ADMIN_BEARER_TOKEN securely in this backend terminal.
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
VITE_DRISHTI_DEMO_MODE=false VITE_DRISHTI_API_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Mint an intake capability locally, with the backend's administrative credential available only in this CLI terminal:

```sh
DRISHTI_API_URL=http://127.0.0.1:8000 .venv/bin/python scripts/intake_capability.py
```

Paste the printed **intake capability** into New Assessment. Never paste the administrative bearer. The capability expires after 30 minutes, owns one intake job, and cannot administer assessments, registry entries, producers or keys. The browser retains it only in component memory. Reloading the page clears it. Eight outstanding capabilities are allowed per process; restart invalidates all capabilities.

Open `/new-assessment`, supply a name and evidence, then choose **STAGE EVIDENCE FOR REVIEW**. Selection and staging do not execute a model. Review filename, byte size, declared format, SHA-256 and separately reported matching registry states before **RUN INTEGRITY ASSESSMENT**. The backend verifies staged hashes again before detector use. A filename is not artifact identity; a digest match does not establish behavioral safety. ONNX format parsing occurs during static analysis, not selection.

Progress reflects completed operations and detector execution. At completion the UI shows the backend score, status, coverage, disposition and snapshot audit state. **VIEW ASSESSMENT** selects the sealed historical assessment. It does not activate it.

## Evidence and limits

- Candidate: one `.onnx`, at most 32 MiB. Existing M1/M2 static analysis and M5 mapping run. No PyTorch, pickle, joblib, archives, arbitrary Python, or format fallback.
- Reference: one optional `.onnx`. M4 compares reference and candidate. Reference designation does not itself prove trust. M4 may be PARTIAL where the existing engine cannot compare a layer, including parameterless models.
- Behavioral: optional numeric `.npy`, 1–32 samples in `[samples, ...4D model shape]`, at most 32 MiB. Explicit opt-in, input/output names, NCHW/NHWC, finite minimum/maximum, classification kind when applicable, and class axis are required. No normalization or semantics are inferred. NPY headers are bounded before allocation and `allow_pickle=False` is retained. The existing bounded M3 worker is reused; unavailable/partial execution remains visible.
- Dataset: explicitly selected PNG/JPEG/BMP/WebP files. Existing image duplicate, perceptual and OOD checks run. This initial intake does not accept annotation manifests, labels or contributor metadata, so label and contributor-risk checks remain NOT ASSESSED. This is image-only dataset evidence.
- Distribution: reference/current selected image windows for D1/D2, optional explicit representation JSON for D3, and prediction JSON for D4. D5 interpretation and D6 mapping reuse existing implementations. No model upload alone triggers Distribution Shift. A missing image reference/current pair is NOT PROVIDED. If every supplied comparison is unusable (including insufficient sample support), Distribution Shift is UNAVAILABLE and no Distribution Shift ModuleRun is submitted. There is no global drift score.
- Inference: one JSON object in the existing `VerifyReceiptRequest` shape: `{"receipt": <signed InferenceReceipt>, "artifacts": <VerificationArtifacts>}`. Maximum 1 MiB. The local backend receipt key must authenticate the signature. Existing verification and output-tampering mapping are reused. Other unmapped verification failures stop intake honestly. This pathway verifies archived evidence; it does not accept a new inference event, consume replay state or claim replay acceptance was assessed. VERIFY != ACCEPT.

Across a job: at most 128 selected files and 128 MiB total; JSON pairs are limited to 1 MiB each. Models, corpus and JSON roles allow one file each. Unsafe filenames, traversal, symlinks in staging and unsupported extensions are rejected. No folder recursion or archive extraction occurs. Names must use ASCII letters/digits, spaces, dots, underscores or hyphens, start with a letter/digit, contain no `..`, and be at most 128 characters.

### Representation pair JSON

```json
{
  "descriptor": {
    "extractor_name": "organization-encoder",
    "extractor_version": "1",
    "output_dimension": 2,
    "normalization": "NONE"
  },
  "reference": [[0.1, 0.2], [0.2, 0.3]],
  "current": [[0.4, 0.5], [0.5, 0.6]]
}
```

This abbreviated example illustrates the format, not sufficient statistical support. Use real observations and the existing detector's minimum support. At most 128 samples per window and 64 dimensions. Normalization must be explicit (`NONE` or `L2_PER_SAMPLE`); optional `extractor_digest` follows the existing `sha256:<digest>` contract. Caller-designated association does not authenticate physical-world observation correspondence.

### Prediction pair JSON

```json
{
  "descriptor": {
    "evidence_tier": "FULL_PROBABILITIES",
    "class_labels": ["A", "B"],
    "output_family": "CLASSIFICATION",
    "probability_semantics": "CLASS_PROBABILITIES",
    "abstention_semantics": "caller-designated abstention",
    "unknown_semantics": "caller-designated unknown"
  },
  "reference": [{"predicted_label": "A", "confidence": 0.9, "probabilities": [0.9, 0.1], "abstained": false, "unknown_or_ood": false}],
  "current": [{"predicted_label": "B", "confidence": 0.9, "probabilities": [0.1, 0.9], "abstained": false, "unknown_or_ood": false}]
}
```

Again, supply real observations with sufficient support. Maximum 128 records/window and 64 classes. Existing `LABEL_ONLY` and `TOP1_CONFIDENCE` tiers also work when declared; their missing evidence remains unavailable. Class order and probability semantics are explicit. No probabilities or chart values are synthesized.

## Trust and lifecycle

Only executed modules create runs. A model-only assessment has one authenticated Model Integrity run; the other three domains are NOT SUBMITTED. A legitimate zero-Finding detector run is allowed, but the frozen backend scoring policy still treats a module without Findings as UNKNOWN. Execution completion and scoring coverage are different. Do not interpret no findings, a valid signature, complete coverage, or a sealed snapshot as safety.

Detectors run before creating the backend assessment. After detector analysis, intake prepares and authenticates every signed envelope with fresh in-memory Ed25519 keys before creating DRAFT and activating it. Approval and key binding are rechecked inside the persistence write reservation after signature authentication. Module progress is AWAITING SUBMISSION after analysis and COMPLETE only after authenticated persistence. Backend `AssessmentService` owns sealing; `SummaryService` owns scores and coverage. Private execution keys never leave the server. Public producer registrations remain auditable. The model registry upload/verify APIs remain identity-only and are not used for execution.

An existing unrelated ACTIVE assessment is not modified automatically; activation conflicts fail honestly. Intake finalization recovery reads its own persisted lifecycle in a fresh session, including when an operation committed before raising. After activation, recovery seals exactly the successfully persisted subset, including zero runs (coverage 0, assurance UNAVAILABLE, disposition REVIEW). Unsubmitted modules never become COMPLETE or gain coverage. A partial intake remains FAILED at the job level but exposes its truthful SEALED snapshot and View Assessment action; assessment lifecycle states are unchanged. If all requested runs persisted and sealing recovery succeeds, the intake is COMPLETE.

Recovery makes at most three attempts, rolling back failed transaction state before each fresh session. The local execution lock remains held through recovery. Persistent database/storage unavailability or an integrity failure that prevents valid sealing cannot be repaired by pretending to seal or rewriting history: the job reports finalization_recovery=UNAVAILABLE, retains the last known lifecycle and bounded error type, and local backend logs preserve the exception. Administrative recovery is then required. A process crash before recovery is likewise outside the returned-execution guarantee.

Immediate audit-outbox draining is separate from sealing success. A drain failure retains durable intents, reports audit_durability=DEGRADED visibly in intake, and does not turn a valid complete SEALED assessment into a failed detector run. The snapshot remains immutable and records audit state at sealing rather than claiming later delivery succeeded. Consult the existing System/audit durability endpoints for current delivery state.

## Local security and cleanup

Intake requires both loopback peer and loopback Host plus an exact configured Origin and a random intake-only capability. Proxy-forwarded headers do not supply authorization. Minting requires the separate admin bearer and rejects browser Origin requests. CORS changes apply only to `/api/intake/`, with explicit methods and headers; administrative CORS remains read-only. Use a single trusted local workstation process, not a shared public service. The local OS account and administrator-controlled data directory are trusted.

Staging is under `DRISHTI_DATA_DIR/intake/ORG-<random bounded ID>/`, with directory mode 0700 and files 0600. Server-generated logical filenames prevent user-controlled paths. Hashes, sizes and logical IDs are retained; workstation paths are not returned. Detector Findings are not rewritten: unexpected staging path leakage fails closed. All staged bytes are removed after sealing or execution failure. Cancel removes staged bytes before execution. Running jobs finish bounded workers before cleanup; forced cancellation during execution is not offered. Expired idle jobs are cleaned by a 30-second janitor, and orphaned job directories are removed when the single backend restarts. Process crashes require restart cleanup. Do not commit staging or keys.

## CLI fallback

The static CLI remains available without the browser:

```sh
python -m modules.model_integrity.cli inspect candidate.onnx --json
python -m modules.model_integrity.cli compare reference.onnx candidate.onnx --json
python -m modules.model_integrity.cli behavioral candidate.onnx \
  --input-npy corpus.npy --input-name input --output output \
  --layout NCHW --value-min 0 --value-max 1 --class-axis -1 --json
```

Supply your actual contract; these names/ranges are examples. Use `--classification-kind LOGITS` or `PROBABILITIES` only when applicable. CLI detector reports alone do not create a signed backend assessment; use the existing SDK/module builders and authenticated lifecycle for submission.

## Validation

`backend/tests/test_intake.py` creates bounded local ONNX, NPY, images and receipt fixtures, tests candidate-only, reference, behavioral and full D1–D6 evidence, authenticates runs, verifies sealing, and checks authorization, expiry, traversal, size and cleanup. These tests need no Internet. Browser validation artifacts belong under `/tmp/drishti-intake-validation/`, outside the repository.

## Execution and assurance in Overview

Execution comes from authenticated ModuleRun membership in the selected assessment.
A completed zero-Finding model run displays authenticated completion alongside UNKNOWN
assurance, zero coverage contribution and UNAVAILABLE overall assurance. Absence of
Findings does not establish safety. No run displays NOT SUBMITTED only when the run
listing is complete; unavailable or truncated evidence is identified as unavailable.
The backend remains the sole score, disposition and lifecycle authority.

Non-streaming mutation API bodies have a default 40 MiB pre-JSON limit. Raw artifact
and intake upload handlers retain their separate incremental limits. These are
per-request bounds, not a multi-user process memory guarantee.
