# Final source review

## Scope and method

Starting main: `0d4be235a0f1751a19848f22d86fd1c6626f5634`; feature branch:
`fix/final-integrity-hardening`. No intervening main changes were present at checkout.

Review covered first-party backend routes, schemas, core cryptography, database and
services; SDK and orchestration; model, dataset, inference and distribution modules;
frontend routes, data hooks, evidence presentation and tests; scripts, relevant docs,
Makefile, dependency configuration and GitHub Actions. Generated dependencies, runtime
state and external model contents were excluded. No Docker configuration was present.

Control/data-flow review followed artifact ingestion through bounded parsing, detector
reports, Finding mapping, signature verification, approval, transactional persistence,
assessment membership, scoring, sealing, snapshot verification and audit outbox. It
also followed browser selection through asynchronous requests and rendered context.
Failure analysis covered malformed/oversized input, missing resources, key loss,
revocation, duplicate requests, partial persistence, transaction ownership and restart.
Targeted regressions preceded broader suites. Existing failure-injection tests were
retained. Static searches were manually interpreted, not used as a substitute for
control-flow review. Browser screenshots were visually reviewed outside the repository.
This is a source review with regression evidence, not a formal proof or penetration-test
claim; external dependencies were not independently audited.

## Issues and resolutions

Counts: CRITICAL 0; HIGH 7; MEDIUM 9; LOW 1; INFO 0. Seventeen issues total,
including the two supplied issues. Each row is a separate issue; related fixes may
share a commit. No Finding, scoring or lifecycle design change was made.

| ID | Severity / category | Affected files | Root cause and impact | Fix | Regression / residual limitation |
|---|---|---|---|---|---|
| F01 | MEDIUM / correctness | `modules/model_integrity/onnx_inspector.py` | Structural payload included initializer storage lengths and external metadata, causing serialization changes to look structural. | Restrict structural initializer fields to name, dtype, shape, element count. Parameter metadata is unchanged. | `tests/model_integrity/test_m1.py`: typed/raw representation, finite/NaN value changes and nine genuine structural mutations. Existing structural hashes require regeneration from trusted originals. |
| F02 | MEDIUM / UX, trust-semantics | `frontend/src/pages/OverviewPage.tsx`, `App.tsx`, `hooks/useWorkspaceData.ts` | UNKNOWN assurance hid the difference between authenticated zero-Finding execution and no submission. | Render execution from authenticated selected-assessment run membership, separately from backend assurance. | `IntakeUX.test.tsx`: zero run, absent run, wrong assessment and unauthenticated run. Truncated/unavailable run listings cannot establish non-submission. |
| F03 | HIGH / security | `backend/services/integration_service.py`, `api/module_auth.py` | Approval could change between authentication and persistence. | Recheck producer, module, key status/ownership/fingerprint under the ingestion write reservation; return 403. | `test_module_auth.py`: producer and key revocation after authentication persist no evidence. Trusted local database administration remains outside this boundary. |
| F04 | HIGH / security | `backend/api/models.py` | Registry writes/revocation lacked the existing administrator gate. | Require admin authorization on both registration modes and revoke. | `test_models.py`: all three mutations denied without credentials; authorized streaming ceiling still tested. Local bearer administration is not production RBAC. |
| F05 | HIGH / data-integrity | `backend/services/assessment_service.py` | Snapshot verification checked authentication presence without matching its request commitment. | Require authentication request hash to equal persisted run hash. | `test_assessment_lifecycle.py`: changed authentication hash invalidates snapshot. Full privileged database/key compromise is outside the trust guarantee. |
| F06 | HIGH / lifecycle | `backend/services/summary_service.py` | Audit-read fallback rolled back a caller-owned seal reservation. | Keep transaction ownership with the caller while honestly returning audit UNAVAILABLE. | `test_assessment_lifecycle.py`: audit failure retains SQLite transaction and seals truthful snapshot. Persistent storage failure can still require admin recovery. |
| F07 | HIGH / security | `backend/core/signing.py` | Private key permissions were narrowed only after writing; existence checks were not exclusive. | Create private files with mode 0600 and O_EXCL before the first byte. | `test_signatures.py`: creation flags and resulting permissions. Key-pair creation is not a two-file atomic transaction; an interrupted partial pair fails closed and needs recovery. |
| F08 | HIGH / data-integrity | `backend/main.py`, `services/provenance_service.py` | Losing both receipt keys silently generated a new trust root despite stored receipts; mismatched pairs were not rejected on initialization. | Check receipt history before key generation and verify private/public pairing. | `test_signatures.py`: missing historical keys and mismatched pair fail closed. No automatic rotation, HSM or external trust-root escrow. |
| F09 | MEDIUM / resource-safety | `backend/api/body_limit.py`, `core/config.py`, `main.py` | JSON bodies reached framework parsing without a global byte ceiling. | Bound non-streaming mutation bodies before parsing, default 40 MiB; retain raw upload streaming limits. | `test_body_limit.py`: chunked overflow gives 413; ordinary malformed body retains 422. Per-request limit is not a global concurrent-memory limit. |
| F10 | MEDIUM / UX, data-integrity | `frontend/src/hooks/useDashboard.ts`, `useWorkspaceData.ts`, `pages/SystemPage.tsx`, `App.tsx` | Out-of-order selection responses and old workspace state could publish mismatched context. | Only latest request generation publishes; scope workspace results to summary identity and require authenticated matching membership in System. | `SelectionRace.test.tsx`, evaluator/workspace tests. Requests are invalidated, not transport-cancelled. |
| F11 | MEDIUM / correctness | `modules/model_integrity/parameter_analysis.py` | Channel statistics reduced a valid zero-element initializer with max(empty). | Skip channel statistics for an empty tensor while preserving complete analysis. | `test_m1.py`: zero-element initializer. Empty tensors are not evidence of safety. |
| F12 | MEDIUM / resource-safety | `modules/data_integrity/analyzer.py` | Exact-duplicate suppression materialized every pair in a group. | Compare path-to-digest membership using linear auxiliary storage. | `test_analyzer.py`: only same-group near duplicates are suppressed. Perceptual comparison retains its existing limits and cost. |
| F13 | MEDIUM / resource-safety, correctness | `modules/distribution_shift/image_features.py` | Promoted Pillow decompression warning escaped the unavailable-result boundary. | Catch the promoted warning and preserve failed-image accounting. | `test_d1.py`: warning yields UNAVAILABLE, zero profiled and one failed image. Decoder heuristics do not replace process isolation. |
| F14 | MEDIUM / lifecycle | `drishti_sdk/orchestration.py` | Repeated orchestration reused a fixed key ID with newly generated key bytes, failing after activation. | Include the generated public-key digest in each key identifier. | `backend/tests/test_sdk.py`: two consecutive clean assessments seal validly with distinct keys. This is not generic SDK failure-atomic orchestration recovery. |
| F15 | HIGH / resource-safety | `modules/model_integrity/baseline.py` | Tensor-detail comparison reparsed ONNX even when inspection had refused structural parsing; configured parameter limits were not propagated. | Skip detail parsing for artifact-only manifests and pass analysis limits. | `test_m4.py`: spy proves no parser call after byte-ceiling refusal. Static parsing is still subject to the documented ONNX parser limits. |
| F16 | MEDIUM / correctness | `modules/model_integrity/baseline.py` | A failed detail extraction became an empty tensor list and could imply tensors were added/removed. | Use explicit unavailable details and PARTIAL comparison with no invented deltas. | `test_m4.py`: one-sided detail failure emits no added/removed tensors. Aggregate commitments remain independently reported where available. |
| F17 | LOW / UX, documentation | `frontend/src/styles/globals.css`, README, implementation docs, `backend/api/evidence.py` | Fixed narrow report columns overlapped long IDs; docs called the schema ten-field and overclaimed execution proof/read-only UI. | Flexible wrapping report rows; correct eleven-field wording, authenticated completion-report semantics, admin and fingerprint documentation. | Browser geometry and visual review; documentation checked against implementation. Top-five report remains a summary, not a complete evidence export. |

## Frozen semantic checks

Finding Schema v1 retains exactly eleven fields, including `category`, `evidence: list[str]`
and `limitations`; authentication and timestamps remain outside it. The four module
names, severity/recommendation enums and DRAFT → ACTIVE → SEALED lifecycle are unchanged.
No score formula, weight, cap, coverage policy, critical override or disposition policy
was edited. Frontend score arithmetic was not introduced. There is no global drift score.

VERIFY remains distinct from receipt ACCEPT and replay-state mutation. Signatures
establish authenticated provenance, not detector correctness. Filename is not identity;
digest identity is not behavioral safety. UNKNOWN, zero Findings and complete scored
coverage do not prove safety. Zero Findings do not establish lifecycle completion.
Finding recommendations remain separate from backend system disposition. No PASS
Findings or fabricated evidence were added. Production signed ingestion rejects unsigned
runs; the existing disabled-by-default compatibility path remains explicitly untrusted
and excluded from assurance.

## Security and failure boundaries

Administrative bearer and private module keys remain server-side. Intake capabilities
remain random, expiring, one-job scoped, bounded in count and unavailable after process
restart. Existing tests cover cross-job use, Host/Origin/loopback checks, CORS, upload
ceilings, filename traversal, symlinks, staged-byte hash checks and cleanup. Non-intake
CORS is read-only; registry mutations now also require the admin dependency. Receipt
APIs retain their documented local-workstation assumptions and attest caller-supplied
input/output records, not witnessed inference execution.

Authentication retains canonical JSON, exact request hashes, Ed25519 tamper rejection,
producer/module/key binding and duplicate identity checks. Approval revalidation now
shares persistence serialization. Intake recovery retains fresh-session bounded retries,
zero/partial-run sealing, no coverage for missing modules and post-seal outbox truthfulness.
The audit chain/checkpoints remain tamper-evident, without trusted timestamping or HSM.
History is never rewritten to conceal failure.

Static scan covered PRIVATE KEY, bearer/password/secret/token/API-key terms, local paths,
TODO/FIXME/HACK/XXX, eval/exec, pickle, torch.load/joblib, shell execution, subprocess
and wildcard CORS. Matches were manually reviewed: test/demo credentials, environment
variable names, security documentation, deliberate CI temporary paths, model.eval,
regex.exec and trusted worker IPC were not embedded production secrets. NPY loading
retains allow_pickle=False. No new arbitrary pickle loader, shell execution or runtime
Internet requirement was introduced. Optional local ML resources remain trusted local
inputs; absent resources remain unavailable. No model, array, screenshot, database,
log, environment file or runtime key is included in the change.

## Validation and E2E

Final local matrix: M1 + M6 **62 passed**; Model Integrity **334 passed**; backend
**228 passed**; Distribution Shift **182 passed**; Data Integrity **73 passed, 1 skipped**;
full Python **817 passed, 1 skipped**. Compileall, frontend typecheck, lint, build and
**37 frontend tests** passed. Both local demo scenarios passed; live concern and repeated
clean orchestration passed. Screen and print report geometry assertions passed.
Compared with the supplied baseline, Python increased by 30 and frontend by 2 tests;
no tests were deleted. Hosted CI is a separate required check on the final PR SHA.

Existing suites were retained;
new regression counts are additive. Python 3.14 emits existing multithreaded fork
DeprecationWarnings in bounded M3 paths. Optional local-resource tests may skip.
No configured/installed Ruff, mypy or Bandit was available; no dependency was added.

Candidate-only browser intake used a real, locally generated safe ONNX fixture through
upload, staging, execution, signing, persistence and seal. It produced one authenticated
model run, zero Findings, UNKNOWN model assurance, zero coverage, null overall score,
SEALED lifecycle and VALID snapshot. The other three modules displayed NOT SUBMITTED.

Full-evidence intake used controlled, actual detector fixtures: candidate/reference ONNX,
image dataset and reference/current images, numeric representation/prediction evidence,
and a locally signed archived receipt with mismatched supplied output. All four modules
submitted authenticated runs; backend coverage was 100%, disposition QUARANTINE,
lifecycle SEALED, snapshot VALID and audit VALID. These fixtures are controlled tests,
not private production evidence. Archived receipt verification was not called live
inference acceptance. Concern and clean demos, including consecutive live runs, passed.

All seven live browser routes were exercised with historical selection retained. Screenshots
were kept outside version control. Reports were rechecked after the long-ID layout fix.
Distribution displays unavailable detailed charts instead of invented chart values; graph
links reflect assessment/run/Finding membership and omit unsupported relationships.

### Exact real-model blocker

The three requested MNIST files were absent from the supplied location. Therefore
Parameter193 metadata, storage representation and the exact baseline/+0.001/NaN CLI
results could not be measured. No substitute was labeled MNIST and no private model was
modified. Equivalent controlled ONNX regressions pass, including HIGH PARAMETER_NAN,
complete parameter coverage and genuine structure changes, but do not satisfy these
exact-file release gates. Release remains blocked pending the requested artifacts.

## Residual limitations and design boundary

Browser intake remains ONNX-only for models and image-only for datasets; no browser
label/contributor ingestion. Heuristic detectors and bounded behavioral corpora do not
prove safety. Structural hashing is a conservative graph projection, not complete
semantic equivalence (for example, full attribute-value/nested-graph semantics are not
newly introduced here). Expanding that definition requires an explicit design decision.
Parameter storage metadata has not been redefined.

The trusted local OS/account, bearer-based administration, single-process intake and
create_all database initialization remain. Process crashes before returned recovery and
persistent storage failures may require administrative recovery. No trusted timestamp,
HSM, full production RBAC or general concurrent-service resource guarantee is claimed.
Large evidence listings may be truncated and are labeled accordingly. External dependency
internals were not independently audited. Missing real MNIST artifacts are an explicit
release blocker, independent of automated CI success.
