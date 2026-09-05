# Model Integrity M5 — Frozen Finding v1 integration

M5 maps existing M1–M4 observations into the existing Person-3 trust pipeline. It does not
change Frozen Finding v1, score evidence, decide a system disposition, register models, or
implement signing. `ModelIntegrityFindingMapper.map(...)` is offline and consumes reports;
it never runs inspection or behavioral execution. Behavioral evidence must already have
been produced through explicit M3/M4 execution.

Every Finding has exactly the frozen fields `finding_id`, `module`, `asset_type`,
`asset_id`, `category`, `severity`, `confidence`, `reason`, `evidence`, `recommendation`,
and `limitations`. `module` is always `model_integrity`, `asset_type` is `model`, and
`asset_id` is the exact `model:sha256:<candidate digest>` identity—not a filename or path.
Evidence and limitations remain bounded `list[str]`; raw weights, samples, outputs, keys,
tokens, and local paths are not emitted.

The mapper uses the existing `ModelIntegrityAdapter`, which delegates stable Finding IDs
to the SDK's canonical deterministic ID helper. IDs commit to module, candidate artifact,
category, and stable observation identity such as tensor/channel, trigger, or comparison
ID. Equivalent reports therefore map to identical Findings and ordering without timestamps
or randomness.

Confidence means confidence that the stated observation was measured—not probability that
a model is malicious. Exact digest/non-finite observations receive high fixed observation
confidence; statistical and heuristic observations receive lower conservative confidence.
M5 contains no learned confidence model and clamps all values to finite `[0,1]`.

Severity and recommendation are centralized in `MODEL_INTEGRITY_MAPPING_POLICY_V1`.
Statistical irregularities generally produce `REVIEW`; non-finite parameters/outputs and
strong trigger sensitivity may produce `QUARANTINE` pending human review. M5 does not use
`REJECT`, copy Person-3 severity penalties, calculate assurance, or override disposition.
Person-3 remains authoritative for scoring and system policy.

## Mapping inventory

| Source | Mapped internal codes | Finding treatment |
|---|---|---|
| M1 | `CUSTOM_OPERATOR_DOMAIN`, `OPERATOR_NOT_APPROVED`, `EXTERNAL_DATA_LOCATION_MISSING`, `EXTERNAL_DATA_PATH_UNSAFE`, `EXTERNAL_DATA_NOT_VERIFIED`, `NO_GRAPH_INPUTS`, `NO_GRAPH_OUTPUTS`, `EMPTY_GRAPH`, `DYNAMIC_OR_UNKNOWN_SHAPE`, `INITIALIZER_NAME_MISSING` | Structural observation, malformed-boundary observation, or incomplete-evidence review |
| M2 | `PARAMETER_NAN`, `PARAMETER_POSITIVE_INFINITY`, `PARAMETER_NEGATIVE_INFINITY`, `ALL_ZERO_TENSOR`, `CONSTANT_TENSOR`, `EXTREME_ZERO_FRACTION`, `TENSOR_SCALE_OUTLIER`, `CHANNEL_NORM_OUTLIER`, `DEAD_CHANNEL`, `EXTREME_CHANNEL_SCALE`, `PARAMETER_ENERGY_CONCENTRATION`, analysis partial/unavailable | Exact non-finite or bounded statistical observation |
| M3 | runtime timeout/failure, output non-finite/contract change, nondeterminism, trigger divergence/flip/concentration/sensitivity, analysis partial/unavailable | Behavioral observation under the declared bounded protocol |
| M4 static | artifact mismatch, structural fingerprint change, parameter metadata/value change, new parameter anomaly | Candidate/reference difference with comparison ID and reference status |
| M4 behavior | new trigger sensitivity, nondeterminism, output non-finite, protocol incomparable, comparison partial/unavailable | Regression only for COMPLETE matched protocols; otherwise coverage-oriented review |

Informational manifests and structure/profile fields, equality states, persisting/resolved
comparison observations, ordinary clean coverage, and unknown future codes without an
explicit reviewed policy entry are intentionally not mapped: they are redundant, do not
state an anomaly, or are unsafe to interpret automatically. All currently emitted M1 issue
codes and the documented M2/M3 security issue inventories have explicit entries. No
`MODEL_SAFE` or backdoor-confirmation Finding exists. Direct
M2 observations and M4 `NEW_PARAMETER_ANOMALY` may both remain only when the latter adds a
specific reference/comparison context; exact duplicate IDs are coalesced.

Findings are sorted by conservative local retention priority (severity, category, asset,
then deterministic ID) before bounding. This ordering is only a report-amplification
control; it is not an assurance score. When more than 100 candidates exist, the mapper
keeps the strongest 99 and uses the final slot for `FINDING_MAPPING_TRUNCATED`, which is a
LOW/REVIEW coverage limitation rather than an anomaly claim.

All heuristic Findings explain legitimate alternatives. Trigger sensitivity explicitly
does not prove a malicious backdoor. A candidate/reference difference does not establish
unauthorized or malicious substitution. `DESIGNATED_ONLY` is never described as approved;
`VERIFIED_REFERENCE` records only the M4 caller-verified reference digest context. Registry
state strings are metadata unless supplied through a separately trusted Person-3 path.

## Signing and backend ownership

`ModelIntegrityRunBuilder` delegates run identity, assessment binding, canonical bytes,
Ed25519 signing, and signed submission to `DrishtiClient`. Only public keys enter the
Person-3 producer registry. Private keys remain operation-local and are neither logged nor
placed in Findings or submissions. The backend authenticates producer/module/key binding,
rejects altered payloads and revoked/unknown identities, stores assessment membership,
handles exact replay idempotently, scores existing categories with its frozen policy, and
records the existing durable audit events.

The frozen `ModuleRunSubmission` requires at least one Finding. Consequently an
authenticated zero-Finding Model Integrity run cannot represent “clean completed coverage”
under the current contract. M5 preserves this behavior, emits no fake INFO Finding, and
leaves the module `UNKNOWN` until genuine mapped evidence exists. Changing this contract is
outside Person-2 ownership.

Offline mapping requires no backend or network. Signed submission is a separate explicit
operation and can target the existing local Person-3 service. M5 adds no cloud dependency,
second signing protocol, model registry, score, disposition engine, or M6 orchestration.
