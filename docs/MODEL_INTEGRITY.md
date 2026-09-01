# Model Integrity — M1

## Purpose and threat model

Model files are hostile input. M1 establishes exact artifact identity, safe structural
identity where possible, initializer-metadata identity, and conservative structural
irregularities. It never executes inference, imports model code, or uses pickle,
`torch.load`, joblib, `eval`, or `exec`.

The scanner complements the Person-3 registry. The registry answers whether an exact
SHA-256 artifact is approved or revoked; this module describes what can safely be learned
about its structure. A digest match proves byte identity, not behavioral safety.

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
```

Failures are bounded and produce no stack trace by default.

## Limitations and roadmap

M1 does not prove absence of backdoors, trojans, poisoning, adversarial behavior or
semantic replacement. M2 will add bounded parameter anomaly forensics; M3 behavioral and
trigger analysis; M4 approved-baseline comparison; M5 signed backend integration; and M6
the final Person-2 demonstration. Unknown and unavailable evidence will remain explicit.
