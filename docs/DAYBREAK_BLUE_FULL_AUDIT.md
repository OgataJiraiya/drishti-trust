# DAYBREAK BLUE Full Audit

## 1. Executive verdict

**PASS WITH LIMITATIONS.** No CRITICAL or HIGH defect and no open release blocker was supported after adversarial source review, execution, and remediation. Two MEDIUM correctness/reliability defects and two LOW readiness/supply-chain defects were confirmed and fixed. “Pass” means the documented invariants resisted the tests below; it does not certify detector correctness or eliminate the stated local-operator and environment limitations.

## 2. Starting SHA

`dfa91fe05d4e20eec9c934bafd2c643dbceaa3cd` on clean `main`.

## 3. Final audit branch SHA

Audited implementation: `6058b455f17a00f5ad97cab1bb1ab9340fc432d5` on `audit/daybreak-blue-full-review`. The report is committed afterward, so its publication commit is necessarily identified by the final `git log`, not self-referentially embedded here.

## 4. Environment

- Kali Linux, kernel `7.1.5+kali-amd64`, x86_64; timezone Asia/Kolkata.
- Isolated CI-shaped Python 3.13.14 environment from `backend/requirements.txt`.
- Node 24.19.0, npm 11.16.0; project declares Node 22 in CI.
- Chromium/ChromeDriver 150.0.7871.181.
- SQLite local persistence; temporary audit state and screenshots stayed under `/tmp`.
- Real ONNX inputs were read from `/home/kali/drishti-real-model`; originals were not modified.

## 5. Architecture summary

Data flow reconstructed from source:

`local input -> capability/origin/size/name validation -> private staging -> bounded detector -> Finding v1 -> signed ModuleRun -> authentication + transactional persistence -> assessment aggregation -> immutable seal/snapshot + audit checkpoint -> API -> React UI`

The backend owns lifecycle, evidence authentication, aggregation, coverage, assurance, disposition, snapshots, and audit verification. The browser selects and renders assessments, starts the loopback-only intake workflow using a narrow capability, and displays backend values.

## 6. Trust-boundary review

- **Browser to API:** browser input, filenames, IDs, JSON, and Origin are untrusted. Administrative mutations require a configured bearer. Local intake additionally requires loopback peer/Host, exact configured Origin, a short-lived scoped capability, and bounded formats.
- **Module producer to integration:** producer output is untrusted until strict Finding/ModuleRun validation, Ed25519 verification, producer/module/key binding, revocation recheck, active-assessment binding, and transactional ingestion succeed.
- **Artifact to detector:** filenames are labels, not identities. Staged bytes receive SHA-256 identities and are rechecked before use; hashes do not imply behavioral safety.
- **Persistence to seal:** an SQLite write reservation serializes lifecycle/run-set changes. A seal commits backend summary, authenticated run commitments, audit position, and snapshot hash. Verification detects later run-set/authentication changes.
- **Backend to browser/report:** assurance, coverage, lifecycle, and disposition are authoritative backend data. React interpolation escapes Finding text; browser print reports use the same scoped state.
- **Outside the threat model:** a trusted OS/database/key-directory administrator can alter local state. There is no HSM, trusted timestamp, remote attestation, or multi-user RBAC.

No reviewed frontend request could increase coverage/assurance, improve disposition, skip lifecycle states, admit unsigned production runs, or mutate a sealed assessment.

## 7. Frozen semantic-contract verification

Verified without changing the frozen semantics:

- `VERIFY != ACCEPT`; signature authentication is reported separately from analytical conclusions.
- filename, digest, deterministic ID, and signature each have deliberately narrower meanings than identity, behavioral safety, authenticity, and detector correctness.
- unsubmitted modules contribute zero coverage; zero findings remains `UNKNOWN` and earns no positive assurance.
- `COMPLETE` coverage does not imply safety; severe authenticated findings can retain COMPLETE coverage while forcing QUARANTINE.
- confidence only scales evidence penalty and is not described as attack probability.
- Finding recommendation does not directly become global disposition; backend policy aggregates it.
- distribution shift has evidence/interpretation but no forbidden global shift score.
- the only lifecycle is DRAFT -> ACTIVE -> SEALED.
- no private signing key or administrator token is present in frontend source/bundle/API/report data.

## 8. Finding schema verification

Backend `Finding`, SDK construction, persistence conversion, ModuleRun DTOs, fixtures, API schemas, report rendering, and the TypeScript `Finding` interface were traced. The public schema has exactly the eleven required fields, `extra="forbid"`, the four module values, five severity values, four recommendations, confidence in `[0,1]`, and `evidence: list[str]`.

New regressions reject missing category, extra timestamp, `attack_class` substitution, string evidence, non-string evidence members, and overlong reason text. Existing tests cover invalid modules/enums/confidence. Markup and Unicode remain inert data and survive round-trip without schema drift.

## 9. Module-by-module review

- **Dataset integrity:** byte-derived sample identity is distinct from filename; exact and bounded near-duplicate work, image decode failures, inconsistent dimensions/channels, limits, and detector error reporting were reviewed. Intake rejects archives, traversal, absolute/Windows paths, unsafe suffixes, symlinks, empty files, excessive counts/bytes, and changed staged bytes. Labels/contributor-risk remain explicitly unassessed in image-only intake.
- **Model integrity:** ONNX parsing is static and size bounded. No pickle, joblib, unsafe `torch.load`, dynamic import from artifact, shell, or subprocess execution path was found. Behavioral ONNX execution is explicit opt-in and isolated with CPU/address-space/wall/output bounds. Real valid, modified, and NaN models produced distinct deterministic digests and appropriate structural/parameter evidence; a signed suspicious model can still yield findings.
- **Inference integrity:** receipt input/model/preprocessing/config/output are canonicalized and signed. NaN/Inf and malformed/non-finite payloads are schema-rejected, receipt IDs/nonces/sequences bind replay state, and model/input identity mismatches produce explicit failure/findings. Receipt confidence is not treated as attack probability.
- **Distribution shift:** reference/current identity and metric evidence are preserved. Empty/tiny/incompatible/non-finite/constant cases fail unavailable or explicit detector status, never zero-risk. Image, representation, and prediction signals are interpreted without a global distribution-shift score.

## 10. Lifecycle review

DRAFT -> ACTIVE -> SEALED and all forbidden reverse/skip transitions were exercised. Duplicate activation/seal is conflict or idempotent only where the immutable result is identical. Submission requires ACTIVE and is rejected for DRAFT/SEALED. Concurrent activation, concurrent seal behavior, seal-versus-ingest, stale revision, duplicate submission, cross-assessment binding, wrong module/producer, revocation, and post-seal mutation tests passed. Snapshot verification binds the authenticated run set and audit commitment.

## 11. Cryptographic/signature review

- Algorithm: Ed25519; PEM private keys are mode-restricted local files. Only public producer keys enter persistence/API.
- Signed bytes: canonical JSON of the exact validated ModuleRun payload, including assessment/module/producer/findings. Request hash anchors the same canonical bytes in authentication and audit data.
- Domain binding is provided by the full typed ModuleRun fields; key IDs are producer-bound and fingerprints/revocation are checked again inside the write transaction.
- Valid, altered, reordered, truncated/malformed signature, wrong key, unknown key, revoked key, wrong assessment, copied signature, and unsigned-production cases were tested. Reordering is a mutation because Finding order is part of the signed request.
- Receipt signing and audit checkpoint signing use separate key files/purposes. No private material was found in Git history, frontend, API responses, logs inspected, or reports.

Cryptographic validity authenticates submitted bytes; it does not certify detector correctness.

## 12. API review

OpenAPI/source enumeration found 55 routes. Method/schema/ID/enum/body-limit/authentication behavior, scoped listing, CORS, host/origin controls, error handling, and mutation authorization were reviewed. Non-streaming JSON is bounded before framework parsing; streaming artifact endpoints enforce incremental limits. Defaults bind Uvicorn and Vite to `127.0.0.1`; `0.0.0.0` is not the default. Errors did not expose stack traces or local artifact paths.

DB-002 added `GET /api/assessments/{assessment_id}/findings`, which returns only authenticated evidence attached to that assessment, with bounded pagination.

## 13. Frontend review

The React API client, selection state, workspace data hook, score/coverage/disposition cards, findings/graph/distribution/reports pages, download/print controls, and CSS were manually reviewed. The UI does not recompute backend score, coverage, or disposition; percentage formatting is display-only. Request effects use an active flag so late responses cannot overwrite a newer assessment. Text is React-escaped; no `innerHTML`/`dangerouslySetInnerHTML` exists. UNKNOWN, empty, partial, and sealed states were walked.

DB-002 fixed silent ten-Finding truncation. DB-003 fixed mobile overflow caused by late desktop rules. An inline data favicon removed an otherwise harmless 404.

## 14. File/upload parser review

Intake uses private `0700` roots, `0600` files, generated logical names, `O_EXCL|O_NOFOLLOW`, role/suffix allowlists, nonblocking job locks, per-file/total/count bounds, incremental streaming, digest/size recheck, bounded parsers, and cleanup. Staging root/role symlinks and post-stage mutation are rejected. Archives/nested archives are not accepted, eliminating zip-slip exposure. MIME and original names are not trusted for identity.

## 15. Persistence/concurrency review

SQLite foreign keys, uniqueness, idempotency/conflict behavior, rollback boundaries, audit outbox durability, replay state, assessment revisions, and seal/run reservations were reviewed. DB-001 fixed receipt sequence allocation by acquiring `BEGIN IMMEDIATE` before reading `MAX(sequence)`, preventing two workers from allocating the same chain identity. The forced race regression commits sequences 1 and 2 without lock-up or collision.

The project initializes schema directly and has no migration framework; release upgrades that change schema require an explicit migration/backup procedure.

## 16. Reporting review

Reports are React-rendered browser views/print output and JSON API data; no server-side HTML/PDF URL fetcher, template injection, `file://` loader, or SSRF path was found. Finding strings, filenames, limitations, and evidence are interpolated as text. Reports consume the backend summary and scoped findings; they do not improve disposition or recompute scores. Very large histories are page bounded, although only the first 100 scoped findings are currently loaded by the UI and are explicitly marked incomplete when more exist.

## 17. Dependency/CI review

`npm audit --json` reported zero known vulnerabilities across 305 dependencies. The isolated declared Python environment passed `pip check`. Python dependencies use bounded compatible ranges and exact pins for ONNX/numpy; npm has a lockfile. Broad Python ranges mean release builds are not byte-for-byte reproducible and should be resolved into a reviewed lock/constraints file.

DB-004 pinned GitHub Actions to verified immutable commit SHAs. Workflow permissions are `contents: read`; no untrusted-PR secret execution path was found.

## 18. Test matrix

| Gate | Result |
|---|---|
| Existing module suite before fixes | 589 passed, 1 skipped |
| Supported full suite excluding sandbox-only portal/intake harness | 799 passed, 1 skipped, 1 deselected in 53.67s |
| Intake fault matrix under temporary async diagnostic adaptation | 26 passed in 7.76s; adaptation reverted because production Uvicorn does not reproduce the harness deadlock |
| New backend regressions | 9 passed in 1.33s |
| Frontend Vitest | 39 passed |
| TypeScript / ESLint / Vite build | pass / pass / pass |
| Python compileall / pip check / diff check | pass / pass / pass |
| Clean and concern full-system demos | pass; signed runs, valid seal/checkpoint/audit, tamper rejection |
| Real ONNX valid/modified/NaN | pass; NaN produced HIGH parameter finding |

The one deselected test uses Starlette `TestClient`; the sandbox blocked its AnyIO portal before `/health`. A fresh minimal TestClient request reproduced the same harness hang. Real Uvicorn tests covered its orchestration/lifecycle purpose.

## 19. Browser matrix

Selenium drove real Chromium against local Uvicorn/Vite for three assessments (ACTIVE/unsubmitted, SEALED concern, SEALED clean), seven major pages, and three viewports: 1366x768, 1920x1080, and 390x844. All 63 combinations completed with no severe console error, failed request, or horizontal overflow after DB-003. The concern assessment rendered backend `48.3 / COMPLETE / QUARANTINE` and four findings; clean/unsubmitted stayed `UNAVAILABLE / REVIEW`; sealed views were read-only and snapshots valid.

## 20. Performance/resource-abuse findings

Artifact, JSON, findings-per-run (100), upload byte/count, behavioral samples (32), ONNX parse size, pair comparison, worker resource, and API page limits were confirmed. No attacker-controlled unbounded archive expansion or model code execution exists. Evidence membership listing avoids SQLite parameter ceilings, but currently scans the local evidence table; very large long-lived histories need benchmark-driven indexing. Dataset pair comparisons are explicitly capped. Measurements are local and are not universal throughput claims.

## 21. Confirmed vulnerabilities

| ID | Severity | Title | Proof |
|---|---|---|---|
| DB-001 | MEDIUM | Receipt sequence allocation race | Two sessions could read the same `MAX(sequence)+1` before either owned a SQLite write reservation, causing collision/lock-up. |
| DB-002 | MEDIUM | Assessment UI silently omitted findings after ten | Backend summary intentionally caps `latest_findings`; the live hook treated that preview as the complete assessment evidence set. |
| DB-003 | LOW | Mobile report/distribution overflow | Chromium at 390x844 measured horizontal overflow because later desktop CSS overrode mobile rules. |
| DB-004 | LOW | Mutable third-party CI action references | Workflow used `actions/*@v4/v5` movable tags. |

## 22. Fixed vulnerabilities

- DB-001: reserve the SQLite writer before sequence allocation; concurrency regression added.
- DB-002: add authenticated assessment-scoped evidence pagination and load it in the UI; backend 12-Finding and frontend scoped-loading regressions added.
- DB-003: final mobile-width/grid overrides plus static regression; real browser matrix rerun.
- DB-004: pin checkout/setup-python/setup-node to verified tag commit SHAs.

## 23. Unresolved issues

No release blocker remains. Non-blocking residuals:

- OS/database/key-directory administrators remain trusted; SQLite files and PEM keys are not HSM-backed.
- No multi-user authorization/RBAC or trusted timestamp/remote attestation exists.
- Schema upgrades lack migrations.
- The UI fetches one bounded page (100) and honestly marks larger evidence histories incomplete.
- Inference receipt creation is unauthenticated on the local API; its security value is integrity/replay evidence, not caller identity. Deployment must retain loopback/network isolation or add caller authentication.

## 24. Limitations

- No external penetration target, cloud deployment, HSM, multi-host database, or hostile reverse proxy was in scope.
- Network vulnerability metadata was available for npm; no independent Python advisory scanner database was installed. `pip check` validates consistency, not CVEs.
- The sandbox prevents Starlette/AnyIO portal/thread harness completion; real Uvicorn, browser, demos, and all non-portal tests were used as compensating evidence.
- Performance was bounded/adversarial locally, not load-tested across production hardware.
- Detector science and training-data representativeness cannot be proven solely by source review.

## 25. Exact commands executed

Principal commands (failed sandbox attempts and diagnostic variants are retained in the audit narrative above):

```text
git status --short
git branch --show-current
git rev-parse HEAD
git remote -v
git log --oneline -10
git switch -c audit/daybreak-blue-full-review
git ls-files
git status --ignored
git grep -nE 'TODO|FIXME|HACK|XXX|placeholder|temporary|mock|fake|pass($|[[:space:]]*#)'
git grep -nE 'eval\(|exec\(|shell=True|os\.system|subprocess|pickle|joblib|yaml\.load|innerHTML|dangerouslySetInnerHTML'
git grep -niE 'password|passwd|secret|token|apikey|api_key|bearer|private.key|private_key'
git grep -nE 'localhost|127\.0\.0\.1|0\.0\.0\.0'
git log --all --name-only --pretty=format:
git rev-list --all | xargs git grep -I -n -E 'BEGIN .*PRIVATE KEY|api[_-]?key|bearer'
python -m pytest -q
python -m pytest -q --ignore=backend/tests/test_intake.py -k 'not test_full_orchestrator_repeats_with_fresh_keys_on_same_backend'
python -m pytest -q backend/tests/test_assessment_lifecycle.py::test_assessment_findings_endpoint_is_scoped_and_not_summary_truncated backend/tests/test_receipts.py::test_concurrent_receipt_creation_allocates_distinct_sequences backend/tests/test_evidence.py::test_finding_shape_and_bounds_reject_contract_drift backend/tests/test_evidence.py::test_unicode_and_markup_are_data_not_schema_extensions
python -m compileall -q backend drishti_sdk modules scripts
python -m pip check
python scripts/demo_full_system.py --scenario clean
python scripts/demo_full_system.py --scenario concern
npm audit --json
npm run typecheck
npm run lint
npm test -- --run
npm run build
git ls-remote https://github.com/actions/checkout.git refs/tags/v4 refs/tags/v4^{}
git ls-remote https://github.com/actions/setup-python.git refs/tags/v5 refs/tags/v5^{}
git ls-remote https://github.com/actions/setup-node.git refs/tags/v4 refs/tags/v4^{}
git diff --check
git diff main...HEAD
git status
git log --oneline --decorate -20
```

Additional temporary scripts started Uvicorn/Vite, seeded signed clean/concern/unsubmitted assessments, drove Selenium across the 63 browser cases, inspected the three external ONNX files, and were removed with their `/tmp` state.

## 26. Final release verdict

**PASS_WITH_LIMITATIONS.** The audit actively attempted signature bypass, lifecycle/state races, cross-assessment contamination, unsafe parser behavior, semantic inflation, frontend authority drift, stored markup injection, stale UI races, and resource abuse. The supported defects are fixed with regressions, and no release-blocking defect remains. Deployment should remain loopback/air-gap capable and must not expand the trusted boundary without authentication, migration, and key-management work.
