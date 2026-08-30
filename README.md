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

## Deadline

Target integrated working demo: **15 September 2026**.
