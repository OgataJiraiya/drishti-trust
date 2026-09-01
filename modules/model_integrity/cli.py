"""Bounded local M1 inspection CLI."""
from __future__ import annotations
import argparse, json
from dataclasses import asdict
from pathlib import Path

from backend.core.canonical import canonical_json_text
from .errors import ModelInspectionError
from .service import ModelIntegrityService


def _load_bounded_npy(path: Path, maximum_bytes: int = 256 * 1024 * 1024):
    """Validate the NPY header before NumPy allocates its declared array."""
    import numpy as np
    with path.open("rb") as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, _fortran, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version in {(2, 0), (3, 0)}:
            shape, _fortran, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError("unsupported .npy format version")
    if dtype.hasobject or len(shape) != 5 or any(not isinstance(item, int) or item < 0 for item in shape):
        raise ValueError("input corpus must be a numeric [samples,...model-shape] array")
    count = 1
    for dimension in shape:
        if dimension and count > maximum_bytes // max(1, dtype.itemsize) // dimension:
            raise ValueError("input corpus declaration exceeds the byte limit")
        count *= dimension
    if count * dtype.itemsize > maximum_bytes:
        raise ValueError("input corpus declaration exceeds the byte limit")
    return np.load(path, allow_pickle=False)


def main() -> int:
    from .behavioral_models import ClassificationKind, InputLayout
    parser = argparse.ArgumentParser(prog="model-integrity")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="non-executing M1/M2 static inspection")
    inspect.add_argument("path")
    inspect.add_argument("--strict", action="store_true")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--pretty", action="store_true")
    behavior = sub.add_parser("behavioral", help="explicitly execute bounded behavioral analysis")
    behavior.add_argument("path")
    behavior.add_argument("--input-npy", required=True)
    behavior.add_argument("--input-name", default="input")
    behavior.add_argument("--layout", required=True, choices=[item.value for item in InputLayout])
    behavior.add_argument("--value-min", required=True, type=float)
    behavior.add_argument("--value-max", required=True, type=float)
    behavior.add_argument("--output", required=True)
    behavior.add_argument("--classification-kind", choices=[item.value for item in ClassificationKind])
    behavior.add_argument("--class-axis", type=int, default=-1)
    behavior.add_argument("--json", action="store_true")
    behavior.add_argument("--pretty", action="store_true")
    create = sub.add_parser("baseline-create", help="non-executing deterministic M1/M2 baseline creation")
    create.add_argument("path"); create.add_argument("--pretty", action="store_true")
    verify = sub.add_parser("baseline-verify", help="non-executing bounded baseline JSON verification")
    verify.add_argument("path"); verify.add_argument("--pretty", action="store_true")
    compare = sub.add_parser("compare", help="non-executing static reference-to-candidate comparison")
    compare.add_argument("reference"); compare.add_argument("candidate")
    compare.add_argument("--json", action="store_true"); compare.add_argument("--pretty", action="store_true")
    compare_behavioral = sub.add_parser("compare-behavioral",
        help="explicitly executes both eligible models using bounded M3 analysis")
    compare_behavioral.add_argument("reference"); compare_behavioral.add_argument("candidate")
    compare_behavioral.add_argument("--input-npy", required=True)
    compare_behavioral.add_argument("--input-name", default="input")
    compare_behavioral.add_argument("--layout", required=True, choices=[item.value for item in InputLayout])
    compare_behavioral.add_argument("--value-min", required=True, type=float)
    compare_behavioral.add_argument("--value-max", required=True, type=float)
    compare_behavioral.add_argument("--output", required=True)
    compare_behavioral.add_argument("--classification-kind", choices=[item.value for item in ClassificationKind])
    compare_behavioral.add_argument("--class-axis", type=int, default=-1)
    compare_behavioral.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    if args.command == "baseline-create":
        from .baseline import create_baseline
        try: data = create_baseline(args.path).to_dict()
        except (OSError, ModelInspectionError, ValueError) as exc:
            print(f"BASELINE CREATION FAILED: {exc}"); return 2
        print(json.dumps(data, sort_keys=True, allow_nan=False,
            indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0
    if args.command == "baseline-verify":
        from .baseline import load_baseline
        result = load_baseline(args.path)
        print(json.dumps(result.to_dict(), sort_keys=True, allow_nan=False,
            indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0 if result.valid else 2
    if args.command == "compare":
        from .baseline import BaselineComparisonService
        try: report = BaselineComparisonService().compare(args.reference, args.candidate)
        except (OSError, ModelInspectionError, ValueError) as exc:
            print(f"MODEL COMPARISON FAILED: {exc}"); return 2
        if args.json:
            print(json.dumps(report.to_dict(), sort_keys=True, allow_nan=False,
                indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        else:
            print("MODEL BASELINE COMPARISON\n")
            print(f"Reference: {report.reference_artifact_id}\nCandidate: {report.candidate_artifact_id}")
            print(f"\nArtifact: {report.artifact.state}\nStructure: {report.structure.state}")
            print(f"Parameters:\n  Metadata: {report.parameters.metadata_state}\n  Values: {report.parameters.value_state}")
            print(f"  Changed tensors: {len(report.parameters.tensors_value_changed)}")
            print("\nBehavior: NOT ASSESSED")
            print(f"\nInterpretation: {report.interpretation}")
            print("\nLimitations:")
            print("  None" if not report.limitations else "\n".join(f"  {item}" for item in report.limitations))
        return 0 if report.status.value == "COMPLETE" else 3
    if args.command == "compare-behavioral":
        try:
            import numpy as np
            from .baseline import compare_behavior
            from .behavioral import default_triggers
            from .behavioral_models import InputContract, OutputContract
            input_path = Path(args.input_npy)
            if not input_path.is_file() or input_path.is_symlink() or input_path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("input corpus is not a bounded regular .npy file")
            corpus = _load_bounded_npy(input_path)
            if corpus.shape[0] == 0: raise ValueError("input corpus must contain at least one sample")
            samples = [np.ascontiguousarray(corpus[index]) for index in range(corpus.shape[0])]
            shape = list(samples[0].shape) if samples else []
            channel_axis = 1 if args.layout == InputLayout.NCHW else 3
            contract = InputContract(args.input_name, corpus.dtype.name, shape, InputLayout(args.layout),
                shape[channel_axis] if len(shape) == 4 else 0, args.value_min, args.value_max)
            output = OutputContract(args.output,
                ClassificationKind(args.classification_kind) if args.classification_kind else None, args.class_axis)
            result = compare_behavior(args.reference, args.candidate, samples, contract, output,
                                      default_triggers(contract))
        except (OSError, ModelInspectionError, ValueError) as exc:
            print(f"BEHAVIORAL COMPARISON FAILED: {exc}"); return 2
        print(json.dumps(asdict(result), sort_keys=True, allow_nan=False,
            indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0 if result.status.value == "COMPLETE" else 3
    if args.command == "behavioral":
        try:
            import numpy as np
            from .behavioral import BehavioralIntegrityService
            from .behavioral_models import InputContract, OutputContract
            input_path = Path(args.input_npy)
            if not input_path.is_file() or input_path.is_symlink() or input_path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("input corpus is not a bounded regular .npy file")
            corpus = _load_bounded_npy(input_path)
            if corpus.dtype.hasobject or corpus.ndim != 5:
                raise ValueError("input corpus must be a numeric [samples,...model-shape] array")
            samples = [np.ascontiguousarray(corpus[index]) for index in range(corpus.shape[0])]
            model_shape = list(samples[0].shape) if samples else []
            channel_axis = 1 if args.layout == InputLayout.NCHW else 3
            channels = model_shape[channel_axis] if len(model_shape) == 4 else 0
            contract = InputContract(args.input_name, corpus.dtype.name, model_shape, InputLayout(args.layout),
                channels, args.value_min, args.value_max)
            output = OutputContract(args.output,
                ClassificationKind(args.classification_kind) if args.classification_kind else None, args.class_axis)
            report = BehavioralIntegrityService().analyze(args.path, samples, contract, output)
        except (OSError, ValueError) as exc:
            print(f"BEHAVIORAL ANALYSIS FAILED: {exc}"); return 2
        data = report.to_dict()
        if args.json:
            print(json.dumps(data, sort_keys=True, allow_nan=False,
                indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
            return 0 if report.status.value == "COMPLETE" else 3
        print("MODEL BEHAVIORAL INTEGRITY\n")
        print(f"Execution:\n  Runtime: {report.runtime}\n  Status: {report.status}")
        print(f"\nClean baseline:\n  Samples: {report.coverage.successful_clean_samples}/{report.coverage.requested_samples}")
        print(f"  Repeatability: {'STABLE' if report.repeatability_stable else 'UNSTABLE/UNAVAILABLE'}")
        print("\nTrigger analysis:")
        for item in report.triggers:
            print(f"  {item.trigger.trigger_id}: samples={item.samples_successful}, flip_rate={item.prediction_flip_rate}, dominant_rate={item.dominant_class_rate}")
        print("\nIndicators:")
        print("  None" if not report.issues else "\n".join(f"  [{item.review_level}] {item.code}" for item in report.issues))
        print("\nLimitations:")
        print("  None" if not report.limitations else "\n".join(f"  {item}" for item in report.limitations))
        return 0 if report.status.value == "COMPLETE" else 3
    try: manifest = ModelIntegrityService().inspect(args.path, strict=args.strict)
    except ModelInspectionError as exc:
        print(f"MODEL INSPECTION FAILED: {exc}")
        return 2
    data = manifest.to_dict()
    if args.json:
        print(json.dumps(data, sort_keys=True, allow_nan=False, indent=2 if args.pretty else None,
                         separators=None if args.pretty else (",", ":")))
        return 0
    artifact, structure = manifest.artifact, manifest.structure
    print("MODEL INTEGRITY INSPECTION\n")
    print("Artifact:")
    print(f"  SHA-256: {artifact.sha256}\n  Size: {artifact.byte_size} bytes")
    print(f"  Format: {artifact.claimed_format.upper()}\n  Inspection: {artifact.inspection_level}")
    if structure:
        print("\nStructure:")
        print(f"  Nodes: {structure.node_count}\n  Parameters: {structure.total_parameter_count}")
        print(f"  Inputs: {len(structure.inputs)}\n  Outputs: {len(structure.outputs)}")
        print(f"  Structural fingerprint: {manifest.fingerprints.structural_sha256}")
    analysis = manifest.parameter_analysis
    if analysis:
        print("\nParameters:")
        print(f"  Status: {analysis.status}")
        print(f"  Tensor coverage: {analysis.coverage.tensor_coverage:.1%}")
        print(f"  Element coverage: {analysis.coverage.element_coverage:.1%}")
        print(f"  Parameter fingerprint: {manifest.fingerprints.parameter_value_sha256 or 'Unavailable'}")
        print("\nParameter indicators:")
        print("  None" if not analysis.issues else "\n".join(
            f"  [{i.severity_hint}] {i.code} tensor={i.tensor_name or '-'}" for i in analysis.issues))
    print("\nIssues:")
    print("  None" if not manifest.issues else "\n".join(f"  [{i.severity_hint}] {i.code}" for i in manifest.issues))
    print("\nLimitations:")
    print("  None" if not manifest.limitations else "\n".join(f"  {item}" for item in manifest.limitations))
    if analysis and analysis.limitations:
        print("\n".join(f"  {item}" for item in analysis.limitations))
    return 0


if __name__ == "__main__": raise SystemExit(main())
