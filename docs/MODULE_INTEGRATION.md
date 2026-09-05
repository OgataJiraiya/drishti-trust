# Module Developer — 5 Minute Integration

This is the handoff contract for Persons 1, 2 and 4. The backend remains the authority;
the SDK only handles validation, canonical signing and HTTP mechanics.

## 1. Obtain integration identity

Ask the operator for the current `assessment_id`, your approved `producer_id`, active
`key_id`, and corresponding local Ed25519 private-key file. Never send the private key
to the backend. Person 1 uses `dataset_integrity`, Person 2 uses `model_integrity`, and
Person 4 uses `distribution_shift`. The backend also accepts `inference_integrity`.

## 2. Submit detector results

```python
from drishti_sdk import DrishtiClient, ModelIntegrityAdapter

with DrishtiClient("http://127.0.0.1:8000") as client:
    adapter = ModelIntegrityAdapter(client)
    finding = adapter.finding(
        asset_type="model", asset_id="MODEL-001", category="PARAMETER_OUTLIER",
        severity="HIGH", confidence=0.94,
        reason="A stable detector rule identified an anomalous layer.",
        evidence=["layer=layer4.2.conv3", "score=0.94"], recommendation="REVIEW",
        limitations=["Detector threshold requires task-specific calibration."],
        stable_evidence_key={"check": "parameter_outlier", "layer": "layer4.2.conv3"},
    )
    run = client.build_run(module="model_integrity", assessment_id="ASSESS-001",
        producer="model-integrity", producer_version="1.0.0", findings=[finding])
    result = client.submit_signed_run(run=run, key_id="MODEL-KEY-1",
        private_key_path="./local-keys/model.private.pem")
```

The detector owns evidence meaning, thresholds, severity, confidence, recommendation and
limitations. Person 3 owns schema validation, producer/key authorization, signature
verification, assessment isolation, immutable storage, summaries and durable audit.

## Frozen Finding Schema v1

Exactly these fields are allowed, in this contract: `finding_id`, `module`, `asset_type`,
`asset_id`, `category`, `severity`, `confidence`, `reason`, `evidence`, `recommendation`,
`limitations`. Modules are `dataset_integrity`, `model_integrity`,
`inference_integrity`, `distribution_shift`. Severity is `INFO`, `LOW`, `MEDIUM`,
`HIGH`, `CRITICAL`; recommendation is `ACCEPT`, `REVIEW`, `QUARANTINE`, `REJECT`.

## Signed-run contract

`POST /api/integration/signed-runs` receives `{run, key_id, signature}`. The signature is
Ed25519 over the backend's canonical JSON representation of the exact run. It binds the
assessment ID, module, producer, version, finding content and order. The SDK reuses the
backend signing helper; do not hand-roll JSON or Base64.

The producer must be approved for exactly the run module, the public key must be active,
and the assessment must be `ACTIVE`. A `DRAFT`, `SEALED`, missing or different assessment
is rejected. Exact run retries return `EXISTS` without duplicate evidence/audit records;
the same run or finding identity with changed bytes returns conflict.

Retrieve a run with `get_run(run_id)`, current scope with `get_current_assessment()`, and
trusted assurance with `get_summary(assessment_id, "authenticated")`. SDK exceptions
retain bounded HTTP status/detail: authentication failures, immutable conflicts, local
validation failures and assessment-state conflicts are distinct types.

Start locally with `./scripts/run_backend.sh`. Preferred signed runs do **not** require
enabling trusted-internal compatibility ingestion.
