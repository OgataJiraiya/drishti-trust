# Distribution Shift — D1 image-window profiling

D1 answers only: **what exactly are the reference and current distributions that
later milestones will compare?** It creates deterministic image-window profile
evidence. It makes no drift, risk, severity, disposition, trust, model-performance,
or deployment-safety judgment and emits no Frozen Finding.

An `ImageWindowProfiler` profiles a directory as `REFERENCE` or `CURRENT` using
identical processing. A reference has `DESIGNATED_REFERENCE` semantics: designation
does not establish authenticity, approval, trust, or safety. Role metadata is not
part of content profile identity, so identical reference/current content has the
same `drift-profile:sha256:...` identity.

## Safe intake and bounds

Traversal is lexicographically sorted, confined to the supplied directory, does not
follow symlinks, ignores hidden entries and unsupported suffixes, accepts regular
files only, resolves paths beneath the resolved root, and supports JPEG, PNG, BMP,
TIFF, and WEBP decoding through Pillow. Extension is only an intake filter; decoded
format is authoritative. Hashing uses streaming SHA-256 in 1 MiB chunks and sample
identity is content-based, never filename-based.

Defaults bound files to 64 MiB, decoded images to 50 million pixels, windows to
10,000 selected samples, failure examples to 20, relative paths to 256 characters,
format categories to 16, and histograms to 256 bins. Size is checked before decode;
dimensions are checked before RGB conversion. Multiframe images are rejected.
Selection is lexicographic first-N and truncation produces `PARTIAL`. Each supplied
file is an observation, including byte-identical duplicates. Duplicate canonical
commitment entries preserve multiplicity without absolute paths.

`COMPLETE` means every eligible sample was profiled. `PARTIAL` means useful evidence
exists but failures or truncation occurred. `UNAVAILABLE` means no usable sample was
available. These describe coverage, not safety.

## Feature definitions and aggregation

Images are converted deterministically to RGB; alpha is ignored rather than
composited. Original mode is grouped as `GRAYSCALE`, `RGB`, or `RGBA`. Unknown modes
are rejected. EXIF is neither read into evidence nor serialized.

- Luminance: `0.2126 R + 0.7152 G + 0.0722 B`, with RGB normalized to `[0,1]`.
- Brightness: population mean of normalized luminance.
- Contrast: population standard deviation of normalized luminance.
- Sharpness: population variance of concatenated horizontal and vertical first
  differences. It is a relative sharpness proxy, not ground-truth blur quality;
  a 1×1 image has explicitly defined zero support.
- Entropy: base-2 Shannon entropy of the fixed luminance histogram; empty bins are
  excluded.
- Saturation: mean `(max(R,G,B)-min(R,G,B))/max(R,G,B)`, with black pixels and
  grayscale deterministically zero.
- Dimensions: width, height, pixel count, and width/height aspect ratio.

Each continuous feature stores count, min, max, mean, population standard deviation,
median, and q05/q25/q75/q95 using NumPy's `method="linear"`. Brightness and saturation
also store fixed `[0,1]` histograms for D2 readiness. Other unbounded features retain
summaries rather than incomparable window-relative histograms.

The sample-set commitment hashes sorted canonical entries containing content SHA-256,
byte size, decoded format, width, and height. The profile ID commits to schema version,
material limits, counts, commitment, aggregate summaries/histograms, status, failure
counts, and limitations. It excludes role, timestamps, UUIDs, host/user identity,
temporary paths, and filenames. Serialization reuses the existing read-only canonical
JSON helper and every public profile passes `json.dumps(..., allow_nan=False)`.

Failures are mapped to bounded codes rather than decoder exception text. Only bounded,
normalized relative paths may appear in examples. Profiles never contain raw bytes,
pixel/luminance arrays, embeddings, EXIF/GPS, absolute paths, or decoder tracebacks.

## Offline boundary and limitations

D1 uses only local Pillow and NumPy processing. It performs no model inference,
download, network request, external command, or cloud operation.

- A filesystem scan is not an atomic snapshot; obvious size/mtime changes are detected.
- Image-decoder vulnerabilities cannot be completely eliminated.
- Summary statistics discard distribution detail.
- Sample truncation produces partial evidence.
- Handcrafted quality features do not capture semantic shift.
- Reference designation does not establish authenticity.
- D1 makes no drift or model-performance conclusion.
- Profile equality does not prove deployment safety.

## D2 statistical and image-quality comparison

D2 is implemented on branch `feat/distribution-statistical-drift` above frozen D1
base `44ae21f516bbf240ba481e68b163e8355c7cd764`. It consumes already-created D1
profiles only. `DistributionShiftComparator.compare(reference, current)` performs no
directory scan, file read, decoder call, network operation, or model execution. The
API requires `REFERENCE` then `CURRENT` roles and never silently swaps them.

D2 compares width, height, pixel count, aspect ratio, brightness, contrast,
sharpness, entropy, saturation, image-mode distribution, and decoded-format
distribution. Every feature reports support counts, typed metrics, raw aggregate
mean/median/std evidence where applicable, direction, state, reason, and limitations.
States are `STABLE`, `SHIFTED`, `PARTIAL`, `UNAVAILABLE`, or `INCOMPARABLE`; they are
feature evidence, not severity or system disposition. There is deliberately no
global score or multi-signal interpretation.

The default minimum support is 20 profiled observations in each window. Below that,
D2 emits `PARTIAL` feature evidence and makes neither a stable nor shifted conclusion.
An unavailable D1 profile makes the comparison unavailable. A partial D1 profile may
still yield feature metrics, but the report remains partial. Schema incompatibility
makes the report incomparable. Histogram length, nonnegative counts, positive mass,
and consistency with summary counts are checked. Incompatible brightness/saturation
histograms make those features incomparable while compatible summary-only features
can still be compared.

### Metrics and decision policy

Continuous summaries use absolute standardized mean difference:
`|mean_cur-mean_ref| / sqrt((std_ref²+std_cur²)/2)`. Equal zero-scale distributions
produce zero; different constant distributions produce `None` plus explicit constant
shift evidence, never infinity. Robust quantile shift is the largest corresponding
q05/q25/median/q75/q95 displacement divided by the larger q05–q95 range, with the
same constant-distribution handling. Symmetric median change is
`2|median_cur-median_ref|/(|median_cur|+|median_ref|+epsilon)`.

Brightness and saturation additionally use fixed-support base-2 Jensen–Shannon
divergence, total-variation distance, and approximate one-dimensional Wasserstein
distance `sum(|CDF_ref-CDF_cur|)/bin_count`. Mode and format counts are aligned by
sorted category name and compared with Jensen–Shannon and total variation. A format
change is operational evidence and does not establish visual or semantic change.

The default configurable thresholds are SMD 0.8, robust quantile shift 0.5,
symmetric median change 0.25, JS 0.10, TV 0.25, and histogram Wasserstein 0.10.
These are conservative detector heuristics, not probabilities or universal scientific
constants. `metric >= threshold` is an exceedance. Summary features require two
normalized exceedances, except exact constant-distribution changes. Histogram-backed
features use the same explicit two-exceedance rule across all available metrics.
Categorical features require both JS and TV. No hidden weighting or aggregation exists.

The immutable policy has `drift-policy:sha256:...` identity. The ordered reference and
current profile IDs, policy, final feature evidence, report status, and limitations
produce `drift-comparison:sha256:...`. IDs contain no timestamp, UUID, process/machine
identity, or path. Reports are deterministic and pass strict JSON with no NaN or
infinity. D2 uses descriptive effect sizes and distances only; aggregate D1 summaries
cannot support fabricated KS, Mann–Whitney, t-test, permutation, bootstrap, or p-value
claims.

D2 limitations include:

- It operates on D1 aggregates rather than every raw feature observation.
- Summary-only effects cannot reproduce full empirical hypothesis tests.
- Histogram Wasserstein is an approximation over fixed bins.
- Generic thresholds are configurable heuristics, not scientific constants.
- Small windows cannot support strong conclusions; partial D1 coverage weakens them.
- Statistical/image-quality shift does not prove model-performance degradation.
- Absence of observed D2 shift does not prove deployment safety.
- D2 neither attributes malicious behavior nor authenticates a designated reference.
- D3 will address representation shift; D4 will address prediction/output shift.

D2 emits no Finding v1, ModuleRun, recommendation, severity, disposition, or Person-3
assurance value. D3 will add separately bounded representation evidence without
changing these D2 semantics.

## D3 representation / embedding drift

D3 answers whether the learned or supplied feature representation of current data
has moved from the designated reference representation. It accepts explicit numeric
`[samples, dimensions]` NumPy arrays; it does not read images, run a model, provide a
default extractor, download weights, use a cloud service, or access the network. A
caller may run any suitable local extractor outside D3 and supply its embeddings.

`RepresentationSpaceDescriptor` commits extractor name and version, output dimension,
normalization mode, and an optional caller-provided SHA-256 extractor digest into a
`representation-space:sha256:...` identity. Without a digest the identity basis is
`CALLER_DECLARED`; with one it is `DIGEST_DECLARED`. Neither authenticates the
extractor or makes the space trusted. Different dimensions or space identities are
incomparable and are never silently padded, truncated, normalized, or swapped.

`RepresentationProfiler.build_reference` and `build_current` validate a numeric,
finite, exactly two-dimensional array with positive axes. Object/string arrays,
NaN/infinity, excessive dimension, and excessive total values fail closed. Defaults
bound samples to 10,000, dimensions to 4,096, total values to 10,000,000, pairwise
samples to 2,000, covariance dimensions to 512, and nearest-reference points to
2,000. These finite limits are part of profile identity.

When samples exceed the bound, rows are selected deterministically. With stable,
unique bounded sample IDs, SHA-256-ranked IDs select the bounded subset; without IDs,
evenly spaced row positions select it. The selected rows are then canonically ordered
by ID and row digest. Each row is normalized to contiguous little-endian float64
before SHA-256. Sorted `(sample_id, row_digest)` entries preserve multiplicity and
make identities invariant to C/F memory layout and, when no selection boundary is
crossed, row ordering. Without IDs, row order can affect which rows survive
truncation. Duplicate rows remain repeated observations.

Normalization is explicit in the space descriptor. `NONE` preserves supplied scale.
`L2_PER_SAMPLE` divides each selected row by its L2 norm; a zero row fails closed.
Normalization is never inferred from observed norms. Caller arrays are never mutated.

The public `RepresentationProfile` records status (`COMPLETE`, `PARTIAL`, or
`UNAVAILABLE`), counts, dimension, sample-set commitment, centroid, centroid norm,
sample norm summary, radial-distance summary, population variance diagonal and its
bounded summary, optional population-covariance commitment, and limitations.
Truncation produces `PARTIAL`. Full covariance is computed only through 512
dimensions and only its canonical-byte digest is public; above the bound it is
explicitly unavailable. The current D3 detector compares diagonal variance, so it
may miss covariance rotations. The profile ID commits all public evidence and
material limits but excludes role, time, UUID, process, host, and paths.

The `RepresentationWindowEvidence` bundle separates this JSON-safe public profile
from the copied, read-only selected matrix needed by pairwise analysis. Raw embeddings
are not returned by `profile.to_dict()`. Centroids and variance diagonals may still
leak aggregate feature information, so profiles should be handled as potentially
sensitive evidence.

### Comparison signals and policy

The immutable comparison policy has deterministic
`representation-policy:sha256:...` identity. Default minimum support is 20 samples
per side. Thresholds are configurable detector heuristics—not probabilities,
scientific constants, p-values, confidence, or a global score.

- `REPRESENTATION_CENTROID` computes cosine distance `1-cosine` and centroid movement
  divided by the RMS radial scale. It shifts when both thresholds are exceeded or an
  exact degenerate/constant-collapse change occurs. Two zero centroids yield zero;
  one zero centroid yields `None` plus explicit degenerate evidence. Equal collapsed
  centroids yield zero; different collapsed centroids yield no infinity.
- `REPRESENTATION_DISPERSION` compares distances from each row to its window centroid
  using standardized mean difference, robust quantile displacement, and symmetric
  median change. Two exceedances or exact constant-dispersion change shift the feature.
- `REPRESENTATION_VARIANCE` compares population variance diagonals using relative L2,
  cosine distance, and relative total-variance change. Two exceedances or an explicit
  collapsed-versus-dispersed change shift it.
- `REPRESENTATION_MMD` uses the nonnegative biased RBF estimate
  `mean(Kxx)+mean(Kyy)-2 mean(Kxy)`. Pairwise inputs are deterministically capped.
  Default bandwidth is the median non-diagonal squared distance on the bounded union;
  a constant union uses the recorded fixed fallback. Tiny floating negatives are
  clamped only within tolerance; material negatives raise an error.
- `REPRESENTATION_SUPPORT_DISTANCE` compares the median current-to-nearest-reference
  distance with median reference-to-nearest-other-reference distance. Self diagonals
  are excluded internally. Fewer than two references is unavailable. Zero duplicate
  baseline support plus positive current separation is explicit shift evidence,
  rather than an epsilon-inflated ratio.

Pairwise MMD and nearest-support work are independently bounded and deterministic;
subsampling makes the report `PARTIAL` and is recorded. Energy distance is omitted in
D3 to avoid redundant pairwise cost. Each signal is independently `STABLE`, `SHIFTED`,
`PARTIAL`, `UNAVAILABLE`, or `INCOMPARABLE`. Insufficient support produces only
`PARTIAL` signals. There is no weighted or overall drift, risk, trust, health,
representation, severity, or disposition score.

The ordered profile IDs, representation space, resolved policy and bandwidth,
feature evidence, status, and sorted limitations produce a deterministic
`representation-comparison:sha256:...` ID; reversing unequal windows changes the
identity. All public values are rounded to 12 decimals and serialize with strict JSON
(`allow_nan=False`).

### Claim boundary and limitations

D3 representation movement does not establish model failure, poisoning, compromise,
an adversarial attack, semantic failure, unsafe deployment, or malicious intent.
Stable D3 metrics do not prove safety, correctness, or future reliability. Reference
designation is not authenticity. In particular:

- evidence quality depends on the caller-supplied feature space;
- caller-declared names and digests are not automatically authenticated;
- representation shift does not establish model-performance loss;
- MMD sensitivity depends on kernel bandwidth;
- bounded deterministic pairwise subsampling can lose localized evidence;
- covariance is unavailable above its dimension bound, and diagonal variance misses
  some rotations;
- aggregate metrics can miss slice-specific or localized shifts;
- D3 assesses no labels, classes, predictions, or semantic correctness;
- D3 provides and downloads no extractor.

D3 emits no Finding v1, ModuleRun, signing material, recommendation, severity, or
Person-3 lifecycle/scoring/disposition data. D4 will separately address bounded
prediction/output drift; multi-signal interpretation and Finding integration remain
D5 and D6 work.

## D4 prediction / supplied-output drift

D4 is implemented on `feat/distribution-prediction-drift` above frozen D3 base
`56f61a1ff0513913ed7bc6b669a600fee023ca6d`.

D4 answers whether the observable distribution of caller-supplied classification
outputs changed between a designated reference window and a current window. It
accepts already-produced `PredictionRecord` values. It does not open images, invoke
D1 or D3 profiling, execute inference, read weights, provide or download a model,
call a cloud service, or access the network.

`PredictionOutputSpaceDescriptor` commits the ordered class vocabulary, classification
family, evidence tier, probability semantics, optional declared model/output-head
digest/name, and explicit abstention and unknown/OOD semantics into a deterministic
`prediction-space:sha256:...` ID. Class order is probability-index order and is never
sorted: changing order, vocabulary, tier, declared semantics, or digest changes the
space. Exact output-space identity is required for comparison. Without a digest its
identity basis is `CALLER_DECLARED`; a supplied digest gives `DIGEST_DECLARED` but is
still not automatically authenticated or trusted.

### Evidence tiers and validation

- `LABEL_ONLY` requires a declared predicted label and supports only predicted-label
  distribution evidence.
- `TOP1_CONFIDENCE` additionally requires a finite confidence in `[0,1]`; entropy and
  margin remain unavailable.
- `FULL_PROBABILITIES` requires a numeric finite rank-one vector matching the ordered
  vocabulary, values in `[0,1]`, and sum within the configured `1e-6` tolerance of
  one. It derives top-1 confidence, normalized Shannon entropy, and top-1 margin.

Full-output labels must equal deterministic NumPy argmax; ties select the lowest class
index. If confidence is also supplied it must agree with maximum probability within
the same tolerance. D4 does not silently normalize, clamp, overwrite, or reinterpret
malformed records. Invalid label, confidence, probability shape/value/sum,
label/argmax disagreement, confidence disagreement, sample ID, or flag semantics is
excluded under a bounded failure code. Useful remaining evidence is `PARTIAL`; no
usable observation is `UNAVAILABLE`.

Entropy is `-sum(p log2 p)` over positive terms only and is divided by `log2(K)` for
`K>1`, yielding `[0,1]`; a one-class space has normalized entropy zero. Margin is
`p_top1-p_top2` and is unavailable for one class. Abstention is never inferred from
confidence, and unknown/OOD status is never inferred by D4. Each rate exists only
when its descriptor semantics are declared and every usable record supplies the
corresponding Boolean flag. “Unknown/OOD” always means caller-declared output state,
not verified OOD correctness.

Default profile bounds are 100,000 samples, 10,000 classes, 10,000,000 probability
values, 256-character sample IDs, 50 fixed `[0,1]` histogram bins, and 20 bounded
failure examples. Over-limit sample windows use SHA-256-ranked stable IDs when all
are present and unique, otherwise evenly spaced row positions. Truncation is
`PARTIAL`. Complete profiles are order invariant because normalized observation
commitments are sorted; truncation without IDs can depend on record positions.
Duplicates remain repeated observations.

The public `PredictionProfile` contains ordered label counts including zero-count
classes, summaries and fixed histograms for available confidence/entropy/margin,
declared flag counts/rates, bounded failures, and a sample-set commitment. It contains
no raw per-sample probability matrix, image, path, embedding, weight, timestamp,
host/user data, or traceback. Its `prediction-profile:sha256:...` identity commits
space, material limits, aggregate evidence, status, and sorted limitations, while
excluding role so identical reference/current content has identical identity.

### Feature comparison

The immutable policy and ordered comparison have deterministic
`prediction-policy:sha256:...` and `prediction-comparison:sha256:...` IDs. Default
minimum support is 20 observations on each side; insufficient support produces only
`PARTIAL` features. Partial input evidence may still be compared, but the report
remains partial. Unavailable input makes the report unavailable, and any output-space
mismatch makes every feature incomparable.

- `PREDICTED_LABEL_DISTRIBUTION` uses base-2 Jensen–Shannon divergence and total
  variation over descriptor class order, shifting only when both thresholds exceed.
- `TOP1_CONFIDENCE`, `PREDICTION_ENTROPY`, and `TOP1_MARGIN` use absolute standardized
  mean difference, robust quantile displacement, symmetric median change, histogram
  Jensen–Shannon, total variation, and fixed-bin Wasserstein distance. Two metric
  exceedances or an exact constant-distribution change shift a feature. Median
  direction is explicitly increased, decreased, or same.
- `ABSTENTION_RATE` and `DECLARED_UNKNOWN_RATE` use direct absolute rate change and a
  configurable threshold. Rates of zero and one require no division.

All thresholds are configurable detector heuristics—not probabilities, universal
constants, p-values, or significance tests. Feature states are independently
`STABLE`, `SHIFTED`, `PARTIAL`, `UNAVAILABLE`, or `INCOMPARABLE`. D4 has no global or
weighted score, severity, recommendation, disposition, Finding, signing, or backend
lifecycle integration. Public values are finite and rounded to 12 decimals; all
descriptors, profiles, and reports support strict JSON with `allow_nan=False`.

### Scientific boundary and limitations

Output drift is not model-performance drift. Without ground truth D4 cannot measure
accuracy, precision, recall, F1, correctness, or error rate. Confidence drift is not
calibration drift without outcomes, and D4 computes no ECE or Brier score. A
predicted-label frequency change may reflect genuine input-population change,
operating conditions, model behavior, pipeline configuration, or a combination; D4
does not assign cause.

- Caller declarations and digests are not externally authenticated.
- Generic thresholds are heuristics; small or partial windows weaken conclusions.
- Label-only evidence cannot support confidence, entropy, or margin.
- Top-1 confidence cannot reconstruct a full predictive distribution.
- Aggregate evidence can miss slice-specific output behavior.
- Detection and segmentation adapters are not implemented in D4.
- D4 does not establish compromise, poisoning, unsafe deployment, or malicious intent.
- No detected output shift proves safety, calibration, performance, or future
  reliability; reference designation is not authenticity.
- D4 does not combine D2, D3, and D4 evidence. D5 will perform separately bounded
  multi-signal interpretation; D6 remains responsible for Finding integration.

## D5 reports-only multi-signal interpretation

D5 is implemented on `feat/distribution-multisignal-interpretation` above frozen D4
base `0a74d740f0724e4a4bb7ff81bc9edf4d04b9a5ef`. It answers what changed, remained
stable, or could not be assessed across frozen D2 image/statistical, D3
representation, and D4 prediction-output reports. `MultiSignalDriftInterpreter`
accepts those concrete report objects or explicit `None`; it never consumes D1
profiles, files, images, embeddings, prediction records, arrays, models, or paths.
It neither inspects numerical metrics nor recomputes detector thresholds or states.

Layers always appear as `IMAGE_STATISTICAL`, `REPRESENTATION`, then
`PREDICTION_OUTPUT`. Source presence is `PROVIDED` or `NOT_PROVIDED`. Coverage is
independently `COMPLETE`, `PARTIAL`, `UNAVAILABLE`, `INCOMPARABLE`, or
`NOT_PROVIDED`; observation is independently `SHIFT_EVIDENCE_PRESENT`,
`NO_SHIFT_OBSERVED`, or `NOT_ASSESSABLE`. Thus a partial D3 report with shifted MMD
remains partial while preserving representation shift evidence. Partial stable-only
features mean no shift was observed in assessed features, not that the whole layer
is stable. Unavailable, incomparable, and absent layers are never converted to
stable.

D5 defensively requires the exact frozen report types and schema version 1, validates
the actual comparison-ID prefixes, strict JSON safety, bounded unique feature names,
disjoint feature partitions, and agreement between partitions and frozen feature
states. Arbitrary dictionaries and duck-typed objects fail closed. It copies only
bounded feature identifiers and comparison IDs—not upstream metrics, histograms,
centroids, vectors, probability data, reasons, or free-form limitations.

### Identities, source binding, and policy

`MultiSignalBundle` commits the exact D2/D3/D4 comparison IDs, including explicit
nulls, into `multisignal-bundle:sha256:...`. Bundling is caller-designated source
association: D5 cannot prove that the reports describe the same physical sample
windows. Deterministic comparison IDs are content identities, not signatures or
authenticated provenance. External orchestration and D6/Person-3 integration are
required for stronger run and audit binding.

The semantic policy contains only a bounded rule version and output-size limits. It
has deterministic `multisignal-policy:sha256:...` identity and contains no weights,
shift/risk thresholds, probabilities, rankings, or scores. The interpretation ID
commits schema, bundle and policy identities, ordered layer summaries, pattern,
coverage status, deterministic text, checks, scientific boundaries, and limitations
as `multisignal-interpretation:sha256:...`.

### Pattern matrix and coverage rules

For three complete layers, the eight binary combinations map exactly to:

| D2 | D3 | D4 | Pattern |
|---:|---:|---:|---|
| 0 | 0 | 0 | `NO_OBSERVED_SHIFT_ALL_LAYERS` |
| 1 | 0 | 0 | `IMAGE_STATISTICAL_SHIFT_ONLY` |
| 0 | 1 | 0 | `REPRESENTATION_SHIFT_ONLY` |
| 0 | 0 | 1 | `PREDICTION_OUTPUT_SHIFT_ONLY` |
| 1 | 1 | 0 | `IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT` |
| 1 | 0 | 1 | `IMAGE_STATISTICAL_AND_OUTPUT_SHIFT` |
| 0 | 1 | 1 | `REPRESENTATION_AND_OUTPUT_SHIFT` |
| 1 | 1 | 1 | `BROAD_MULTILAYER_SHIFT` |

Here `1` means at least one frozen upstream feature is shifted; `0` means complete
coverage with no shifted feature and at least one stable feature. An `_ONLY` pattern
is impossible unless both other layers are complete and show no shift. With
incomplete coverage, observed change normally becomes
`SHIFT_WITH_INCOMPLETE_COVERAGE`; no observed change becomes
`NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE`; no usable stable/shifted evidence becomes
`NO_USABLE_EVIDENCE`. Shift in all three layers remains `BROAD_MULTILAYER_SHIFT` even
if coverage is partial, while report status retains partial coverage.

Interpretation status describes coverage only: `COMPLETE` requires all three complete
reports, `PARTIAL` requires some usable evidence with at least one incomplete layer,
and `UNAVAILABLE` means no usable stable/shifted feature conclusion. Complete never
means safe, trusted, or accepted. `changed_layers` includes complete or partial shift
evidence; `stable_layers` requires complete coverage; `incomplete_layers` and
`not_assessable_layers` remain explicit.

### Analyst output and scientific boundary

Fixed templates produce bounded summaries, per-layer observations, and analyst
checks in deterministic order. Checks identify changed feature IDs, identity/runtime
configuration to verify, incomplete evidence to acquire, source-context binding, and
slice-specific behavior. They are checks—not recommendations or dispositions. Fixed
unsupported-conclusion codes state that cause, malicious intent, model performance,
calibration, deployment safety, reference authenticity, and common cause are not
established.

Pattern codes describe which observable layers changed, never why. Image/statistical
change does not prove sensor or environmental cause. Representation change does not
prove a hidden attack or degradation. Output change does not prove wrong predictions
or reduced accuracy. Non-adjacent D2+D4 change with stable measured D3 evidence is not
a contradiction; the supplied representation space may be insensitive to changed
factors. Broad multi-layer shift does not establish a common cause, compromise,
poisoning, malicious activity, model failure, accuracy/calibration degradation, or
unsafe deployment. Fully stable evidence does not establish correctness, safety, or
future reliability. A designated reference is not authenticated, trusted, or safe.

D5 is deterministic in-memory rule logic. It uses no filesystem, NumPy, network,
model framework, LLM, learned classifier, cryptography, backend scoring, signing,
audit, Finding, severity, recommendation, or disposition integration. Public reports
contain no time, UUID, machine identity, or raw evidence and pass strict JSON with
`allow_nan=False`.

Remaining limitations include dependence on correct upstream reports, unauthenticated
source IDs and caller-designated association, inability to prove shared physical
windows, aggregate evidence missing slices, incomplete layers preventing full
stability conclusions, and no re-evaluation of detector thresholds. D5 estimates
neither performance nor calibration and establishes neither reference authenticity
nor common cause. D6 will perform signed Person-3/Finding integration; D5 emits no
Finding v1 or backend disposition.

## D6 final frozen-Finding and signed integration

D6 is implemented on `feat/distribution-final-integration` above frozen D5 base
`a1677c22dd8d3ead5e927b575f5ea096187b99e9`. D1–D5 remain frozen. D6 is a thin mapper
and integration layer: exact D5 reports map through the existing SDK
`DistributionShiftAdapter`, and `DistributionShiftRunBuilder` delegates run identity,
`ModuleRunSubmission`, Ed25519 signing, and authenticated submission to the existing
`DrishtiClient`. It creates no backend, schema, signer, score, disposition, audit
chain, lifecycle, producer registry, key registry, or checkpoint format.

Frozen Finding v1 remains exactly `finding_id`, `module`, `asset_type`, `asset_id`,
`category`, `severity`, `confidence`, `reason`, `evidence`, `recommendation`, and
`limitations`. D6 always uses module `distribution_shift`, asset type
`distribution_context`, and the D5 bundle ID as asset ID. The bundle commits the exact
source comparison identities but does not prove that reports describe the same
physical windows. `DistributionShiftAdapter.finding()` and its stable evidence key
derive deterministic `F-SHIFT-...` IDs; no timestamp or randomness enters Finding
identity.

### Mapping policy v1

`DISTRIBUTION_SHIFT_MAPPING_POLICY_V1` emits at most one Finding per interpretation,
preventing correlated D2/D3/D4 observations from accumulating multiple backend
penalties for one interpreted context. Detailed changed layers, bounded shifted
feature identifiers, interpretation/bundle/pattern/status, layer coverage, and source
comparison IDs remain ordered `list[str]` evidence. Evidence is capped at 20 strings
and limitations at 10; truncation is explicit.

- Complete `NO_OBSERVED_SHIFT_ALL_LAYERS` maps to zero Findings—never a fake safe,
  pass, completion, INFO, or ACCEPT Finding.
- Complete single-layer patterns retain their category with `MEDIUM`, confidence
  `0.90`, and `REVIEW`.
- Complete two-layer patterns and `BROAD_MULTILAYER_SHIFT` retain their category with
  `HIGH`, confidence `0.90`, and `REVIEW`.
- `SHIFT_WITH_INCOMPLETE_COVERAGE` becomes
  `DISTRIBUTION_SHIFT_WITH_INCOMPLETE_COVERAGE`; one changed layer is `MEDIUM`, two or
  three are `HIGH`, with confidence `0.90` and `REVIEW`.
- `NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE` becomes the `LOW`, confidence `1.00`,
  `REVIEW` coverage Finding `DISTRIBUTION_SHIFT_EVIDENCE_INCOMPLETE`.
- `NO_USABLE_EVIDENCE` becomes the `MEDIUM`, confidence `1.00`, `REVIEW`
  assessability Finding `DISTRIBUTION_SHIFT_EVIDENCE_UNAVAILABLE`.

Confidence `0.90` is fixed observation-mapping confidence, not attack, error,
maliciousness, or safety probability. Confidence `1.00` states that the structural
coverage gap exists. Severity describes changed-layer scope or assessability attention,
not maliciousness. Every emitted D6 Finding recommends `REVIEW`; none recommends
`ACCEPT`, `QUARANTINE`, or `REJECT`, and none uses `CRITICAL`. Finding recommendation
is not Person-3 backend disposition.

Every Finding states that distribution shift does not establish cause, malicious
intent, performance/calibration degradation, or deployment unsafety; deterministic
source IDs are not authenticated provenance; the designated reference is not thereby
authentic, approved, or safe; and caller-designated D5 association is not physical
window proof. Signing authenticates producer, key, and submitted payload—not sensor,
embedding, prediction, reference, or physical-world truth.

### Frozen run limitation and existing backend ownership

Frozen `ModuleRunSubmission` requires at least one Finding. Therefore a complete clean
interpretation produces `[]` and cannot represent a signed clean module completion.
`DistributionShiftRunBuilder.build_run()` rejects empty findings with that exact
limitation. D6 does not weaken the schema, manufacture a clean Finding, call a private
endpoint, or introduce a second completion protocol. Backend Distribution Shift
coverage consequently remains unknown for a purely clean zero-Finding result under
v1.

For non-clean evidence the builder enforces module and bundle asset binding, then
delegates to the SDK. The existing backend owns producer/key authentication, DRAFT /
ACTIVE / SEALED lifecycle, replay (`CREATED` then `EXISTS`), stored authentication,
assurance scoring, module/system disposition, coverage, audit verification, snapshot,
outbox, and checkpoint. A Distribution Shift-only assessment normally has partial
whole-system coverage; D6 reports backend-returned values and never recomputes them.

### Final runtime-generated demo

`DistributionShiftFinalOrchestrator` creates temporary deterministic image windows,
caller-supplied synthetic embeddings, and caller-supplied prediction records, then
runs the real frozen D1–D5 APIs and D6 mapper. No specimen, image, embedding,
prediction matrix, key, token, or benchmark artifact is retained. Scenarios are
`clean`, `image-shift`, `representation-shift`, `output-shift`, `broad-shift`, and
`incomplete`. Offline is the default and performs no network operation:

```text
python scripts/demo_distribution_shift_final.py --scenario clean
python scripts/demo_distribution_shift_final.py --scenario broad-shift
```

Signed mode uses only an explicitly running loopback backend and the existing SDK:

```text
DRISHTI_ADMIN_BEARER_TOKEN=... python scripts/demo_distribution_shift_final.py \
  --scenario broad-shift --signed --assessment-id DRIFT-D6-DEMO
```

Clean `--signed` returns `NO_FINDINGS_TO_SUBMIT` without requiring a token or making a
backend call. Shifted signed mode generates an Ed25519 private key only in memory,
registers its public key, activates the assessment, submits and replays the signed
run, fetches persisted authentication and backend summary, verifies audit, seals and
verifies snapshot, drains outbox, and creates/verifies a checkpoint. No backend is
started automatically and no cloud or internet service is contacted.

Remaining limitations include the zero-Finding run gap, unauthenticated underlying
observations before external binding, caller-designated source association, signatures
not proving physical truth, detector heuristics, aggregate slice blindness, supplied
D3 representation quality, D4 classification-only scope, partial whole-system
coverage in a Distribution Shift-only run, and production checkpoints benefiting from
external anchoring, HSM protection, or trusted timestamps. D6 changes none of
Person-3 scoring or disposition policy. Distribution Shift ends at D6; there is no D7.
