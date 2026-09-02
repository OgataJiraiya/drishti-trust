# Distribution Shift Module

Owner branch: `feat/distribution-representation-drift`

Primary scope:

- D1 deterministic, bounded image-window profiling
- D2 pure statistical and image-quality comparison of frozen D1 profiles
- D3 bounded, model-agnostic representation/embedding comparison
- later prediction and multi-signal drift milestones

See `docs/DISTRIBUTION_SHIFT.md`. D1 emits profile evidence only; Finding integration
is deferred to D6. Dashboard work belongs under `frontend/`.

D3 accepts caller-supplied numeric embedding matrices through
`RepresentationProfiler`; it provides and downloads no extractor. Public profiles
contain aggregate evidence and cryptographic commitments, while the accompanying
ephemeral evidence bundle retains an immutable bounded matrix for MMD and support
comparison. `RepresentationShiftComparator` emits feature-level states only.
