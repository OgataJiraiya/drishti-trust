# Distribution Shift Module

Owner branch: `feat/distribution-representation-drift`

Primary scope:

- D1 deterministic, bounded image-window profiling
- D2 pure statistical and image-quality comparison of frozen D1 profiles
- D3 bounded, model-agnostic representation/embedding comparison
- D4 bounded comparison of caller-supplied classification outputs
- D5 deterministic reports-only multi-signal interpretation
- D6 frozen Finding v1 mapping and existing Person-3 signed integration

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

The final product builds designated image-window profiles, compares statistical,
representation, and supplied-output distributions, interprets cross-layer patterns,
and maps one interpretation to zero-or-one frozen Finding. D6 reuses the existing
`DistributionShiftAdapter`, `DrishtiClient`, `ModuleRunSubmission`, and Ed25519
protocol. Person-3 remains authoritative for assurance scoring, disposition,
lifecycle, audit, snapshot, outbox, and checkpoint behavior.

Run `python scripts/demo_distribution_shift_final.py --scenario broad-shift` for the
offline D1–D6 chain. Add `--signed` and a local admin token only when an explicitly
running loopback backend should receive a non-clean Finding.
