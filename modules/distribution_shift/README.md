# Distribution Shift Module

Owner branch: `feat/distribution-representation-drift`

Primary scope:

- D1 deterministic, bounded image-window profiling
- D2 pure statistical and image-quality comparison of frozen D1 profiles
- D3 bounded, model-agnostic representation/embedding comparison
- D4 bounded comparison of caller-supplied classification outputs
- D5 deterministic reports-only multi-signal interpretation
- later signed Finding integration

See `docs/DISTRIBUTION_SHIFT.md`. D1 emits profile evidence only; Finding integration
is deferred to D6. Dashboard work belongs under `frontend/`.

D3 accepts caller-supplied numeric embedding matrices through
`RepresentationProfiler`; it provides and downloads no extractor. Public profiles
contain aggregate evidence and cryptographic commitments, while the accompanying
ephemeral evidence bundle retains an immutable bounded matrix for MMD and support
comparison. `RepresentationShiftComparator` emits feature-level states only.

D4 similarly executes no model. `PredictionProfiler` accepts typed output records at
label-only, top-1-confidence, or full-probability evidence tiers and produces bounded
aggregate profiles. `PredictionShiftComparator` reports label, confidence, entropy,
margin, abstention, and caller-declared unknown/OOD feature states when supplied.

D5 consumes only frozen D2, D3, and D4 comparison reports. It preserves coverage and
observed shift as separate axes, emits deterministic non-causal pattern codes and
bounded analyst checks, and never re-thresholds upstream metrics or creates a score,
severity, recommendation, disposition, Finding, or signature.
