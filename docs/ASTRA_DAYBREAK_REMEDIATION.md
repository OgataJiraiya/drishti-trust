# ASTRA Daybreak remediation

## 1. Starting SHA

`dfa91fe05d4e20eec9c934bafd2c643dbceaa3cd`. The user prepared the remediation branch directly from clean main. Startup independently verified HEAD and main matched this SHA, the branch matched the requested remediation branch, and the working tree was clean. No Daybreak implementation branch was opened or used as a source.

## 2. Final SHA

Validated implementation and regression commit: `af6673838cfb24daba0bacdc534b6e16ce7ace57`.
The JSON `final_sha` identifies this final code/test state. This report is published in a subsequent documentation-only commit; its SHA is in the terminal handoff and Git log. A committed report cannot contain its own commit hash.

## 3. Branch

`fix/astra-daybreak-remediation`. Main stays at the starting SHA. No merge, push, history rewrite, or modification of the Daybreak branch occurred.

## 4. Independently reproduced issues

| ID | Severity | Baseline evidence | Result |
|---|---|---|---|
| DB-001 | MEDIUM | Both two- and five-session receipt tests failed with SQLite sequence uniqueness errors | FIXED |
| DB-002 | MEDIUM | Signed assessment A had 12 persisted findings; summary had 10; frontend rendered 10. A 105-finding frontend fixture also rendered 10 | FIXED |
| DB-003 | LOW | At 390x844, Distribution scrollWidth was 403 and Reports was 524 against clientWidth 375 | FIXED |
| DB-004 | LOW | Immutable-action regression failed on four mutable major-tag references | FIXED |

No reported issue was classified NOT REPRODUCED. No additional supported security defect was established in the affected code. An existing favicon 404 is recorded as a cosmetic limitation.

## 5. Root causes

**DB-001.** `ProvenanceService.create_receipt` called `ReceiptRepository.next_sequence` (MAX + 1) and `latest` before reserving the SQLite writer. Independent sessions could observe the same candidate. The unique sequence constraint prevented duplicate commits but made legitimate requests fail. SQLite uses WAL and foreign keys; the service commits receipt plus audit intent, and the API dependency closes/rolls back failed sessions.

**DB-002.** `SummaryService` intentionally returns `list(reversed(findings))[:10]`. `useWorkspaceData` assigned that preview as its sole live evidence source; App, Findings, Graph, Distribution and Reports consumed it. Reports additionally displayed only five rows. A summary preview cannot establish evidence completeness.

**DB-003.** Earlier mobile one-column rules were overridden by later workstation rules: the distribution summary used `227px 1fr`, and report score/disposition used `245px 245px`. Distribution also inherited desktop padding. The second summary section and report disposition card extended beyond the viewport.

**DB-004.** CI used `actions/checkout@v4`, `actions/setup-python@v5`, and `actions/setup-node@v4`. These major tags can move.

## 6. Reproduction procedures

Regression tests were written and run before the corresponding production fix:

- `.venv/bin/python -m pytest -q backend/tests/test_astra_remediation.py`: baseline **4 failed, 1 passed**. The two concurrency tests rendezvous after allocation with a bounded barrier. Before the fix, both/all sessions read sequence 1; insertion fails the existing unique constraint. The same tests succeed after serialization. The rollback test already passed and remains a neighboring invariant check.
- The API test registered a real Ed25519 producer, submitted 12 signed findings to A and three distinct signed findings to B, and sealed both. A's summary retained ten preview findings, but the scoped endpoint returned 404.
- `npm test -- --run src/test/AstraEvidence.test.tsx`: baseline **2 failed**. Both 12- and 105-finding scenarios rendered only ten explorer rows.
- `python3 scripts/validate_astra_browser.py --baseline`: real temporary Uvicorn/Vite with synthetic signed A/B assessments, all seven application routes at three viewports. CSS remained unchanged for these measurements.
- The CI test found four unpinned references. Read-only upstream `git ls-remote` independently resolved each intended tag.

The first browser setup attempts failed because the system lacks Selenium Manager and the initial readiness selector did not include the Overview component. The script was corrected to use installed Chromium/ChromeDriver and real page selectors; these were harness corrections, not application changes.

## 7. Fixes

**DB-001:** Three lines reserve `BEGIN IMMEDIATE` before either receipt-chain read. Commit releases the reservation; an exception leaves rollback to the existing session owner. No uniqueness, signing, audit or replay constraint was weakened.

**DB-002 backend:** `GET /api/assessments/{assessment_id}/findings?page=1&page_size=100`. Strict query validation bounds pages and page size (1–100), rejects unknown query parameters, and bounds/nonblank-checks the ID. Missing assessments return 404. SQL joins assessment membership, ModuleRun and persisted ED25519 authentication with a matching request hash, expands each run's bounded ID array using SQLite JSON1, deduplicates IDs, and joins only persisted findings. Both count and pagination stay in SQL. Ordering is ingestion time then finding ID. No global Python history scan or unbounded ID parameter list is introduced.

**DB-002 frontend:** Fetch all pages for the selected assessment using the initial total to bound the loop. Reject changing totals, duplicates or incomplete results. Late responses from previous selections cannot publish state. Failed loads retain an explicitly incomplete preview. Reports receive this completeness state and render all loaded findings instead of silently slicing to five. Summary preview and all backend policy calculations remain unchanged.

**DB-003:** Five CSS lines restore single-column summary grids and mobile distribution padding after workstation overrides. No overflow hiding was added. The browser regression script is committed.

**DB-004:** Exact verified commits, with human-readable version comments:

| Action | Pin |
|---|---|
| checkout v4 | `11d5960a326750d5838078e36cf38b85af677262` |
| setup-python v5 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| setup-node v4 | `49933ea5288caeca8642d1e84afbd3f7d6820020` |

Workflow permissions remain `contents: read`. The workflow uses ordinary pull_request, has no secrets expressions or pull_request_target execution, and its local CI demo token is a disposable fixture, not a production credential.

## 8. Regression tests

`backend/tests/test_astra_remediation.py` contains six collected tests:

- two and five concurrent independent sessions: unique committed sequences 1..N, bounded completion, and predecessor hash chain;
- failed commit after flush, rollback, same-session repeated calls, new-session continuation, and no transaction left after success;
- A/B isolation, 12 findings, bounded summary, three pages, deterministic order, empty/out-of-range pages, missing/overlong IDs, unsupported/negative/absurd pagination, and sealed verification;
- deduplication across signed runs, orphan exclusion, unsigned-mode exclusion, and mismatched authentication commitment exclusion;
- exact immutable workflow pins and minimal permissions.

`frontend/src/test/AstraEvidence.test.tsx` contains four tests:

- complete 12-finding explorer/report rendering;
- complete 105-finding rendering across two pages;
- old A response cannot overwrite newly selected empty B;
- page-two failure stays incomplete and visibly marks the report.

`scripts/validate_astra_browser.py` is the actual geometry regression. jsdom cannot measure layout; no CSS-text assertion is substituted for geometry.

## 9. Security implications

The new endpoint preserves the existing local read-access model; it is not a new per-user authorization system. Evidence authentication is enforced by SQL membership and authentication predicates, never frontend filtering. Unsigned, orphan, cross-assessment or mismatched-commitment evidence is excluded regardless of page. Historical approval matches existing summary/seal semantics; later key revocation does not rewrite an earlier authenticated assessment. The endpoint only reads; it cannot mutate sealed evidence.

SQLAlchemy binds all values. Page validation prevents offset overflow, and unknown `offset`/`limit` parameters are rejected rather than silently accepted. The API keeps the existing bounded `EvidenceListResponse`; Finding v1 is unchanged. SQLite JSON1 is required and was verified on SQLite 3.53.4 in both local Python environments. JSON1 is built in on current supported SQLite builds.

The receipt fix serializes the sequence and predecessor as one transaction, including its durable audit intent. Callers must continue to rollback or close a failed session, as they already do.

## 10. Frozen-semantic verification

All existing schema, authentication, lifecycle, summary, replay, module and seal regressions passed. No module detector or policy source was changed.

- Finding v1 retains exactly eleven fields, the four modules, five severities, four recommendations, and list-of-string evidence.
- VERIFY remains distinct from receipt acceptance and system disposition.
- Signatures authenticate bytes; they do not validate detector correctness.
- Filename, digest, and deterministic IDs do not establish safety or authenticity.
- Zero findings do not establish safety or complete the lifecycle; UNKNOWN gains no assurance credit.
- COMPLETE coverage can coexist with QUARANTINE.
- Confidence is not attack probability.
- Finding recommendation is not global system disposition.
- Frontend consumes backend assurance, coverage, status and disposition without computing them.
- No global Distribution Shift score was introduced.
- Lifecycle remains DRAFT -> ACTIVE -> SEALED.
- Private signing keys and administrator tokens stay outside browser data.
- Unsigned production ModuleRuns remain rejected; unsubmitted modules contribute zero coverage.

## 11. Full test results and environment

| Command/gate | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | **823 passed, 1 skipped**, 88.20s; exit 0; no deselections |
| `/tmp/drishti-ci313/bin/python -m pytest -q` | **823 passed, 1 skipped**, 70.42s; exit 0; no deselections |
| focused backend regressions | **6 passed** |
| `.venv/bin/python -m compileall -q backend drishti_sdk modules scripts` | exit 0 |
| `.venv/bin/python -m pip check` | **exit 1**: inherited unrelated Kali packages have missing/conflicting dependencies |
| isolated Python 3.13 pip check | exit 0, no broken requirements |
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npm test -- --run` | **41 passed**, six files; exit 0 |
| `npm run build` | exit 0; Vite JS 292.78 kB, CSS 48.14 kB |
| `git diff --check` | exit 0 |
| PyYAML workflow parse + permissions assertion | exit 0 |

The only Python skip is the unavailable DOTA128 external dataset integration fixture. Existing multiprocessing fork warnings remain (34 on Python 3.14). Python 3.13 also emits two Starlette test-client deprecations. No test was weakened, removed, deselected or adapted to make the final gates pass.

Workspace interpreter: Python 3.14.6, `include-system-site-packages=true`. Supplemental isolated interpreter: Python 3.13.14, matching CI; every installed direct dependency was independently checked against `backend/requirements.txt`: FastAPI 0.141.1, Pydantic 2.13.5, SQLAlchemy 2.0.52, cryptography 47.0.0, Uvicorn 0.52.4, HTTPX 0.28.1, pytest 9.1.1, ONNX 1.22.0, numpy 2.4.6, Pillow 12.3.0. Node 24.19.0, npm 11.16.0, Chromium/ChromeDriver 150.0.7871.181 on Kali Linux.

Full integration tests need local sockets/asyncio thread wakeups and were run with the environment's approved sandbox escalation. They completed with no harness exclusions. An early full-suite run started before the pin edit completed and reported the expected CI pin failure; the final two complete runs above validate the finished changes.

## 12. Browser results

Real Uvicorn and Vite, no mocked network responses. Seven routes: Overview, Findings, Distribution Shift, Reports, New Assessment, System, and Assurance Graph. Model/Dataset/Inference have module cards in Overview and filters in Findings, not separate routed pages in this version.

| Viewport | Before CSS fix | Final |
|---|---|---|
| 390x844 | Distribution 403 > 375; Reports 524 > 375 | 7/7 pass, all scrollWidth <= clientWidth |
| 1366x768 | 7/7 pass | 7/7 pass |
| 1920x1080 | 7/7 pass | 7/7 pass |

Final matrix **21/21 PASS**, repeated after final API validation changes. A's 12 finding rows are rendered in explorer and report. Selecting B replaces them with only B's three rows; EMPTY produces zero rows. Loading completes. Unit tests additionally cover failed pagination and deliberately delayed stale responses.

No failed non-canceled network transport requests. The only severe console entry is the pre-existing `/favicon.ico` 404. No other console error was observed. Temporary servers, synthetic keys, databases and staging directories are cleaned by the script.

## 13. Demo results

Both required Make targets exited 0:

- `make demo`: four authenticated findings, coverage 1.0, assurance 48.3, score status COMPLETE, disposition QUARANTINE.
- `make demo-clean`: four authenticated zero-finding runs, coverage 0.0, assurance null, score status UNAVAILABLE, disposition REVIEW.

Both complete DRAFT -> ACTIVE -> SEALED with VALID audit, snapshot and checkpoint. Payload tampering and wrong signatures are rejected; exact replay is idempotent EXISTS.

## 14. Real ONNX results

All three originals were read only; before/after SHA-256 checks matched.

| Fixture | SHA-256 | Result |
|---|---|---|
| mnist-12.onnx | `5c688690f8bacf667d4c2074af5ad0646ca328d7ab03eccf944a65b320171bdd` | structural and parameter analysis complete |
| mnist-12-modified.onnx | `96a244868e48e2ac873959a5ad5f195d24c5d3fb9dac863dc25fde99ab07555e` | artifact CHANGED, structure SAME, Parameter193 values CHANGED |
| mnist-12-nan.onnx | `be211e7b5be072167b4dff26777d9a87e9412d400ad2fa90c8ebf6a6e69ddb1d` | HIGH PARAMETER_NAN, tensor Parameter193, count 1 |

Existing CLI `inspect --strict --json` and `compare ... --json` exited 0. No model execution capability or behavioral semantics changed.

## 15. Remaining limitations

- Workspace pip check remains failed due to inherited Kali packages. The project dependency set passes in an isolated environment; no unrelated host packages were changed.
- DOTA128 fixture is unavailable; one existing integration test skips.
- Existing fork/test-client deprecation warnings and cosmetic favicon 404 remain.
- Loading all findings is limited to the selected assessment and bounded HTTP pages. Extremely large assessments still require proportional browser memory and SQL ordering work. This task does not redesign evidence storage or virtualize the analyst UI.
- A changing assessment total during pagination produces an explicitly incomplete preview; reselect/refresh retries. A sealed assessment's immutable membership avoids this condition.
- Local OS/database administrators remain trusted. This endpoint does not add tenant authorization to the existing local application.
- Browser regression requires optional Selenium and system Chromium/ChromeDriver; these are validation tools, not runtime dependencies.

## 16. Comparison with suggested directions

The receipt transaction direction was independently confirmed: a writer reservation before both chain reads is the minimal correct repair.

For evidence pagination, this implementation uses a database subquery over authenticated scoped membership and SQLite JSON1, avoiding a global evidence scan or a large Python ID set. The frontend follows every page, including >100 findings, and reports expose completeness rather than silently treating a single page as complete. Reports now render every loaded finding.

The mobile repair is five CSS lines targeted to the measured columns/padding; no application-wide overflow hiding or broad layout refactor. CI pins were resolved directly from the official action repositories.

## 17. Final verdict and commits

**PASS_WITH_LIMITATIONS.** DB-001 through DB-004 were independently reproduced and fixed. No open release blocker remains within this remediation scope. Main and external ONNX bytes are unchanged; nothing was pushed.

Focused implementation commits:

- `8c2e2cf426359e825e122b9fbe5bc8e35eb92650` — serialize receipt allocation.
- `f70dda36997116253b9819cbf0a7f45e6753e1cd` — scoped SQL pagination.
- `6fd14a54432c03e0bbf87507c6787c4a66b03308` — complete frontend/report evidence and tests.
- `027444772ced88feb815b6dcd0b1219c3421153d` — responsive fix and real browser regression.
- `e3932c9f1bed78d111ec89241b9152faaa654313` — immutable action pins.
- `af6673838cfb24daba0bacdc534b6e16ce7ace57` — backend concurrency/isolation/CI regressions.

Re-run commands from the repository root:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q backend drishti_sdk modules scripts
.venv/bin/python -m pip check
(cd frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build)
make demo
make demo-clean
python3 scripts/validate_astra_browser.py
.venv/bin/python -m modules.model_integrity.cli inspect /home/kali/drishti-real-model/mnist-12.onnx --strict --json
.venv/bin/python -m modules.model_integrity.cli inspect /home/kali/drishti-real-model/mnist-12-nan.onnx --strict --json
.venv/bin/python -m modules.model_integrity.cli compare /home/kali/drishti-real-model/mnist-12.onnx /home/kali/drishti-real-model/mnist-12-modified.onnx --json
git diff --check
```
