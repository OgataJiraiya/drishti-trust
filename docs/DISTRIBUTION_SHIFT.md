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
