# Integration Contract — Finding Schema v1

All four DRISHTI-TRUST modules may use different internal algorithms, but every module must emit findings through the same frozen outer JSON envelope.

## Frozen schema

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

## Required fields

| Field | Type | Meaning |
|---|---|---|
| `finding_id` | string | Stable unique finding identifier |
| `module` | string | Producing assurance module |
| `asset_type` | string | Type of affected asset |
| `asset_id` | string | Stable identifier of affected asset |
| `category` | string | Finding/anomaly category |
| `severity` | enum | INFO / LOW / MEDIUM / HIGH / CRITICAL |
| `confidence` | number | 0.0 to 1.0 |
| `reason` | string | Human-readable explanation |
| `evidence` | list[string] | Ordered supporting evidence |
| `recommendation` | enum | ACCEPT / REVIEW / QUARANTINE / REJECT |
| `limitations` | list[string] | Known limitations/assumptions |

## Official module values

- `dataset_integrity`
- `model_integrity`
- `inference_integrity`
- `distribution_shift`

## Compatibility rules

1. Do not rename outer fields.
2. `evidence` remains `list[str]` in v1.
3. `limitations` remains `list[str]` in v1.
4. Module-specific detail belongs inside evidence strings or within the module's own internal data structures; it must not break the shared envelope.
5. Do not add a mandatory timestamp to this contract. The backend may maintain ingestion timestamps internally.
6. Unknown outer fields should not be relied upon for cross-module integration.

## Example IDs

```text
sample:img_0042
dataset:DATASET-001
batch:BATCH-017
contributor:CONTRIB-C
model:MODEL-001
inference:INF-0042
distribution:RUN-001
```

## Integration path

```text
Data Integrity ---------\
Model Integrity ---------+--> POST /api/evidence --> Evidence Store --> Summary --> Dashboard
Inference Integrity -----+
Distribution Shift -----/
```

The backend is the integration boundary. A module should remain independently testable and only needs to produce valid Finding Schema v1 output to integrate.
