# Model Integrity Module

Owner branch: `feat/model-integrity`

Primary scope:

- behavioural fingerprinting
- reference challenge battery
- backdoor-like behaviour assessment
- trigger/activation/parameter analyses where access permits
- black-box/white-box graceful degradation
- Finding Schema v1 output using module `model_integrity`

M1 safe intake, artifact identity and ONNX structural inspection are implemented here.
See `docs/MODEL_INTEGRITY.md` for the supported-format and safety contract. Later detector
milestones will integrate with Finding Schema v1 without changing that frozen contract.
