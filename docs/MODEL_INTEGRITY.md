# Model Integrity — M1–M3 integrity analysis

## M3 behavioral integrity trust boundary

`ModelIntegrityService.inspect()` remains strictly non-executing M1/M2 inspection. Model
execution requires the separate, explicit `BehavioralIntegrityService.analyze()` API or
the `behavioral` CLI command. Approval/revocation state and M2 parameter indicators do not
grant or deny behavioral eligibility; the execution gate is independent.

M3 uses `onnx.reference.ReferenceEvaluator` from the pinned `onnx==1.22.0` package because
no ONNX Runtime package is installed for the project's Python 3.14 environment. NumPy
remains pinned at `2.4.6`; no runtime dependency was added. Each inference runs in a child
process with a wall-clock timeout and Linux CPU limit. Outputs are bounded inside the
worker before IPC. Bounded full outputs needed for comparison cross IPC only after tensor,
element, and byte ceilings; reports contain summaries, not arrays, and the parent
independently revalidates the payload. This child process is not a hardened sandbox, and
`ReferenceEvaluator` still processes untrusted graph structures. Production use should
add a hardened container with syscall and filesystem isolation plus independent process,
CPU, and memory limits.

Eligibility requires a bounded ONNX artifact, available structural inspection, embedded
and safely decoded initializers, standard `""` or `ai.onnx` domains, static bounded I/O,
supported numeric dtypes, exactly one explicit image input, and a declared output. External
tensor data and custom domains are never executed. No custom native library is registered.
The design is offline and makes no network calls.

Eligibility recursively checks graph-valued attributes, so custom-domain operators or
external initializers inside `If`, `Loop`, or `Scan` subgraphs cannot bypass the gate.
Standard-domain control flow is not assumed cheap or intrinsically safe; it remains
eligible only behind per-run wall-clock, CPU, and address-space bounds.

`InputContract` explicitly declares input name, dtype, static batch-one shape, NCHW or
NHWC layout, 1/3/4 channels, and valid value range. Layout and normalization range are
never guessed. The Python API accepts already-constructed numeric NumPy arrays. CLI corpus
input is a bounded regular `.npy` file loaded with `allow_pickle=False`; general image,
video, object-array, and model-code decoding are outside M3.
The CLI validates NPY header-declared shape, dtype, and allocation size before loading.
Multiple required graph inputs are unavailable in this MVP; auxiliary inputs are never
fabricated. Boolean trigger inputs are unavailable because numeric high/low trigger
semantics would be ambiguous.

Default behavioral ceilings are 256 MiB model size, 64 samples, 16 triggers, 512 total
runs, 32 MiB per input, 256 MiB total input, 16 output tensors, 4,000,000 output elements
and 64 MiB output per run, ten-second wall timeout, two clean repeatability runs, and 200
issues. Linux workers also receive a 1 GiB address-space ceiling. Products are validated before execution; trigger and sample lists are truncated
deterministically with explicit limitations.

Clean inference is repeated and compared through deterministic output commitments. Output
summaries contain only name, dtype, shape, element/non-finite counts, finite min/max/mean/
RMS, and a fingerprint over fixed-dtype little-endian numeric bytes. Full outputs and inputs
are never included. Non-finite values are counted and finite statistics use the finite
subset; JSON never emits bare NaN or Infinity. Shape/dtype/name changes, non-finite clean
outputs, runtime failures, timeouts, and nondeterministic commitments prevent an unusable
baseline from feeding trigger analysis.
An exact repeatability mismatch is review evidence, not a backdoor verdict: stochastic
graphs, nondeterministic implementations, or floating-runtime differences can cause it.

Generic compatible-output comparison reports maximum and mean absolute differences,
normalized L2 delta, and cosine similarity using scaled float64 arithmetic. Empty,
non-finite, mismatched, and all-zero outputs avoid division by zero. Optional explicit
classification contracts declare logits or probabilities and class axis. Logits use stable
softmax only for comparative top-1 metrics; declared probabilities receive conservative
range tolerance of `1e-6` and sum tolerance of `1e-3`. Arbitrary output vectors are never
assumed to be classes.

Deterministic probes are solid-high, literal-zero (only when in range), and checkerboard
patches at top-left, top-right, bottom-left, bottom-right, and center. Patch values remain
inside the explicit input range and preserve dtype. There is no randomness, gradient
search, or optimization. Same-kind/same-size locations form matched control families.
Per-trigger reports include successful samples, mean/median generic divergence, flips,
dominant class concentration, clean target rate, concentration lift, score shift, and the
flip-rate difference from the peer-location median.

Strong `TRIGGER_SENSITIVITY` requires at least eight successful samples, at least 75% flips,
at least 75% dominant triggered-class concentration, at least 0.50 lift over the clean
target rate, and at least 0.50 flip-rate advantage over matched locations. A single flip is
not a strong verdict. Stable internal codes include `BEHAVIOR_ANALYSIS_UNAVAILABLE`,
`BEHAVIOR_ANALYSIS_PARTIAL`, `RUNTIME_TIMEOUT`, `RUNTIME_FAILURE`, `OUTPUT_NONFINITE`,
`OUTPUT_CONTRACT_CHANGED`, `NONDETERMINISTIC_OUTPUT`, `TRIGGER_OUTPUT_DIVERGENCE`,
`TRIGGER_PREDICTION_FLIP`, `TRIGGER_TARGET_CONCENTRATION`, and `TRIGGER_SENSITIVITY`.

Coverage distinguishes requested/successful clean and trigger runs, failures, timeouts,
triggers, and output tensors as `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`. Zero coverage is
never described as clean. Natural occlusion sensitivity, task-specific preprocessing,
stochastic graphs, and incomplete probe families can cause false positives or negatives.

**Trigger sensitivity is evidence requiring review. It is not cryptographic or
mathematical proof of a malicious backdoor.** M3 compares one model on clean versus
controlled inputs only; approved-baseline comparison remains M4.

## Purpose and threat model

Model files are hostile input. M1 establishes exact artifact identity, safe structural
identity where possible, initializer-metadata identity, and conservative structural
irregularities. It never executes inference, imports model code, or uses pickle,
`torch.load`, joblib, `eval`, or `exec`.

The scanner complements the Person-3 registry. The registry answers whether an exact
SHA-256 artifact is approved or revoked; this module describes what can safely be learned
about its structure. A digest match proves byte identity, not behavioral safety. M2 adds
bounded statistics for safely available embedded ONNX initializers and never executes the
model. **A statistical anomaly is not proof of a malicious model or a backdoor.** M2
indicators recommend review; behavioral trigger evidence belongs to M3.

## Parameter analysis

M2 explicitly pins `numpy==2.4.6`. It supports embedded float16/32/64, signed and unsigned
8/16/32/64-bit integers, and bool. String, complex, bfloat16, float8, and other special
formats remain unavailable rather than being interpreted as zero. Shape products,
declared sizes, and raw/typed data lengths are validated before allocation.

Configurable `ParameterAnalysisLimits` default to 256 MiB total parameter bytes, 64 MiB
per tensor, 65,536 sampled values, 4,096 channels per tensor, 10 issues per tensor, 200
total parameter issues, and 2,000 reported tensor-statistic records. Values within the
ceilings use `FULL` analysis. Larger values use `SAMPLED` analysis with stable evenly
spaced integer indices; there is no randomness. Sampled statistics are explicitly not
presented as exact whole-tensor results.

For finite analyzed values the report provides element and finite counts, min/max, mean,
population standard deviation, RMS, absolute maximum, L1/L2 norms, zero and near-zero
fractions, median, and MAD. NaN, positive infinity, and negative infinity are counted
separately. Numeric work is promoted to float64 and norms use scaled sum-of-squares, so
integer and float16 inputs do not wrap or accumulate in their source dtype. A derived
quantity outside finite float64 range is reported as JSON `null`, never bare NaN or
Infinity. Finite statistics use only the finite subset; non-finite counts describe the
analyzed set. Empty/no-finite cases use explicit null statistics.

Comparable non-scalar tensors are grouped by rank and numeric dtype. RMS outliers use
`robust_z = 0.6745 * (x - median) / MAD`; zero MAD is handled without division. For full,
finite matrices and convolution weights, axis 0 is the output-unit/output-channel axis.
Channel L2, RMS, absolute maximum, and zero fraction support within-tensor dead-channel,
norm-outlier, extreme-scale, and squared-energy-concentration indicators. Only the
strongest configured top-K channel issues survive. Pruning, specialization, and
architecture choices can legitimately create these patterns.

`parameter_value_sha256` commits to the name-sorted set of each embedded tensor's name,
numeric dtype, shape, and SHA-256 of its exact normalized value bytes. Value/shape changes
change it; documentation and initializer order do not. Fingerprint status is `COMPLETE`,
`PARTIAL`, or `UNAVAILABLE`; a partial fingerprint is never called full.

Canonical value bytes use the ONNX dtype's fixed-width little-endian representation;
one-byte integers and bool are byte-order independent. Typed protobuf fields are converted
to that representation, so typed-field and `raw_data` encodings of the same tensor agree.
Raw floating-point bits are preserved, including signed zero and distinct NaN payloads;
NaNs are not numerically collapsed. Fingerprinting is permitted only for tensors within
both configured byte ceilings, so it cannot bypass analysis resource limits.

Coverage includes total/analyzable/analyzed tensors, elements and bytes, with tensor and
element ratios. Analysis status is independently `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`.
Zero findings at zero coverage never means safe. External data files are never opened,
even for apparently local paths; affected tensors produce
`EXTERNAL_PARAMETER_DATA_UNAVAILABLE` and reduce coverage.

Stable M2 issue codes are `PARAMETER_NAN`, `PARAMETER_POSITIVE_INFINITY`,
`PARAMETER_NEGATIVE_INFINITY`, `ALL_ZERO_TENSOR`, `CONSTANT_TENSOR`,
`EXTREME_ZERO_FRACTION`, `TENSOR_SCALE_OUTLIER`, `CHANNEL_NORM_OUTLIER`,
`DEAD_CHANNEL`, `EXTREME_CHANNEL_SCALE`, `PARAMETER_ENERGY_CONCENTRATION`,
`PARAMETER_ANALYSIS_PARTIAL`, and `PARAMETER_ANALYSIS_UNAVAILABLE`.
Report and issue truncation, channel ceilings, and numeric-range channel skips are exposed
as explicit limitations even when value coverage itself is complete.

## Safe intake and supported formats

All inputs must be non-empty regular files. Symlinks, directories, missing files and
unreadable files are rejected. SHA-256 is streamed in bounded chunks.

| Extension | Claimed format | M1 inspection |
|---|---|---|
| `.onnx` | ONNX | `STRUCTURAL` after safe protobuf parse |
| `.pt`, `.pth` | PyTorch | `ARTIFACT_ONLY` |
| `.ckpt` | Checkpoint | `ARTIFACT_ONLY` |
| `.h5`, `.keras` | HDF5/Keras | `ARTIFACT_ONLY` |
| `.tflite`, `.pb` | TFLite/Protobuf | `ARTIFACT_ONLY` |
| Other | Unknown | `UNSUPPORTED` |

Extensions are claims, not proof. Non-ONNX formats are never deserialized in M1. Strict
mode rejects anything without supported structural inspection.

ONNX support adds the sole M1 dependency, `onnx==1.22.0`. Loading uses protobuf parsing
with external-data loading disabled; inference runtimes are not invoked.

Artifact SHA-256 remains streaming and is not constrained by the structural parser's
resource ceiling. Before protobuf parsing, ONNX inspection enforces the configurable
`DEFAULT_MAX_ONNX_PARSE_BYTES` ceiling (512 MiB by default). An oversized ONNX artifact
still receives valid artifact identity but degrades to `ARTIFACT_ONLY` with
`STRUCTURE_UNAVAILABLE_RESOURCE_LIMIT`; structural and parameter-metadata fingerprints
remain unavailable. Strict mode fails closed with a bounded resource-limit error. This
is a scanner resource-safety boundary, not evidence that a large model is malicious.

## Identity and structural fields

`artifact_sha256` hashes every file byte and is compatible with the existing registry.
`structural_sha256` separately hashes canonical JSON containing ONNX IR version, sorted
opsets, graph inputs/outputs, ordered nodes and sorted initializer metadata.
`parameter_metadata_sha256` covers initializer names, dtypes, shapes, element counts,
embedded raw-byte lengths and declared external locations—but never weight values.

The manifest includes producer metadata, graph name, input/output tensor shapes, node and
unique-operator counts, a sorted operator profile, initializer metadata and derivable
total parameter count. Raw tensor values are never emitted.

An artifact can change while structural identity stays stable, for example if excluded
documentation metadata changes. A graph/operator/shape change changes the appropriate
structural or parameter-metadata fingerprint. These independent fields support later
approved-versus-candidate comparison without conflating byte and structure identity.

## Issues, external data and unknowns

M1 conservatively reports missing inputs/outputs, empty graphs, dynamic boundary shapes,
custom operator domains, operators outside a caller-supplied allowlist, missing
initializer names and external-data dependencies. These are review indicators, not
backdoor verdicts.

External tensor files are never opened. Relative paths inside the model directory are
reported as `EXTERNAL_DATA_NOT_VERIFIED`; absolute paths, backslash traversal and `..`
escapes are marked `EXTERNAL_DATA_PATH_UNSAFE`. Missing external location metadata is
also reported. Consequently M1 does not claim complete parameter verification for such
models.

## CLI

```bash
python -m modules.model_integrity.cli inspect model.onnx
python -m modules.model_integrity.cli inspect model.onnx --json --pretty
python -m modules.model_integrity.cli inspect model.pt --strict
python -m modules.model_integrity.cli behavioral model.onnx --input-npy corpus.npy \
  --layout NCHW --value-min 0 --value-max 1 --output scores
```

Failures are bounded and produce no stack trace by default.

## Limitations and roadmap

M2/M3 do not prove presence or absence of backdoors, trojans, poisoning, adversarial behavior or
semantic replacement. M4 will add approved-baseline comparison; M5 signed backend integration; and M6
the final Person-2 demonstration. Unknown and unavailable evidence will remain explicit.
