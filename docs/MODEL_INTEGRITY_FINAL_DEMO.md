# DRISHTI-TRUST Model Integrity final demonstration (M6)

M6 freezes Person-2 on branch `feat/model-final-integration`, on top of M5 commit
`8c3229a`. It is a thin product orchestration layer over M1 safe intake, M2 bounded
parameter forensics, M3 explicit behavioral analysis, M4 baseline comparison, and
M5 Frozen Finding v1 mapping/signing. It adds no detector, registry, baseline
format, scoring policy, disposition policy, or signing implementation.

## Product story and execution boundary

1. Establish exact model identity.
2. Inspect structure without executing the model.
3. Analyze parameter integrity.
4. Compare candidate against reference.
5. Optionally execute bounded behavioral probes.
6. Translate evidence into Frozen Finding v1.
7. Cryptographically sign the module run.
8. Backend verifies producer/key/assessment binding.
9. Existing assurance policy interprets evidence.
10. Audit chain preserves tamper-evident history.

`run_static_scenario` performs steps 1--4 and 6 only. It never creates the M3
runtime. `run_behavioral_scenario` is the conspicuous execution boundary and is
the only M6 API that runs model inference. The CLI also requires `--behavioral`
for `trigger-sensitive`.

The normal reference is `DESIGNATED_ONLY`. Supplying and matching an expected
SHA-256 yields `VERIFIED_REFERENCE`; this verifies identity in caller context but
does not make the reference approved or safe. A designated baseline is not an
authenticity claim, and equality is not proof of safety.

## Scenarios and reports

All ONNX models and NumPy corpora are generated inside temporary storage and
deleted. No binary fixture is committed. Clean, weight-changed, structurally
changed, NaN-parameter, and trigger-sensitive candidates exercise existing APIs.
The trigger graph is the controlled M3 synthetic specimen design.

The typed scenario result is outside Finding v1. Deterministic JSON uses
`allow_nan=False`; it contains artifact IDs, evidence IDs, bounded tensor names,
states, categories, recommendations, and limitations, but no paths, raw values,
arrays, keys, tokens, or model bytes. `model-demo:sha256:<digest>` commits to this
deterministic evidence and contains no timestamp, UUID, process ID, or temp path.

A clean comparison normally maps zero Findings. No `MODEL_SAFE` or completion
Finding is invented. Frozen ModuleRun v1 requires at least one Finding, so an
authenticated clean completion cannot be submitted and backend module coverage
remains unknown/unrepresented. Schema validation rejects an empty run.

Offline mode performs M1--M5 mapping locally without a service or signing key.
Signed integration uses `ModelIntegrityRunBuilder`, `DrishtiClient`, and the
existing Person-3 producer/key/ACTIVE-assessment APIs. It fetches the persisted run
and existing assurance; M6 displays those values and never computes a score.
Frozen lifecycle remains DRAFT (reject evidence), ACTIVE (accept authenticated
evidence), and SEALED (reject later evidence). Existing APIs own sealed snapshots,
outbox drain, chain verification, signed checkpoints, replay (`CREATED` then
`EXISTS`), tamper rejection, and key revocation. Production checkpoint trust
benefits from external anchoring, an HSM, and trusted timestamps.

## Quick demo

```bash
source .venv/bin/activate
python scripts/demo_model_integrity_final.py --scenario weight-change
python scripts/demo_model_integrity_final.py --scenario structure-change
python scripts/demo_model_integrity_final.py --scenario parameter-nan
python scripts/demo_model_integrity_final.py --scenario trigger-sensitive --behavioral
python scripts/demo_model_integrity_final.py --all --offline
```

Default invocation is static and offline. The subsystem is designed for local,
air-gapped use and downloads no models or datasets.

## Threat model and limitations

- Unsupported M1 formats remain artifact-only; external tensor files are not opened.
- M2 sampled statistics can miss localized abnormalities.
- M3 child-process isolation is bounded fault containment, not a hardened sandbox;
  trigger probes cannot detect every backdoor mechanism.
- M4 designated baseline identity is not authenticity; equality does not prove safety.
- M5 confidence is observation confidence, not probability of malicious intent.
- Caller registry context is not independently authenticated by core M5.
- Frozen ModuleRun v1 cannot represent zero-Finding clean completion.
- Model Integrity-only evidence is partial coverage, not whole-system trust.
- Trigger sensitivity is observational evidence, never a confirmed backdoor claim.

M6 preserves hostile-input bounds inherited from M1--M5: symlinks and empty files,
parse size, external data, custom domains, malformed/duplicate graphs, NaN/Inf,
dynamic shapes, unsupported formats, runtime timeouts, partial coverage, protocol
mismatch, bounded strings, redaction, identity conflicts, and canonical signatures.
