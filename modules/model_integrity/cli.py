"""Bounded local M1 inspection CLI."""
from __future__ import annotations
import argparse, json

from backend.core.canonical import canonical_json_text
from .errors import ModelInspectionError
from .service import ModelIntegrityService


def main() -> int:
    parser = argparse.ArgumentParser(prog="model-integrity")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("path")
    inspect.add_argument("--strict", action="store_true")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try: manifest = ModelIntegrityService().inspect(args.path, strict=args.strict)
    except ModelInspectionError as exc:
        print(f"MODEL INSPECTION FAILED: {exc}")
        return 2
    data = manifest.to_dict()
    if args.json:
        print(json.dumps(data, sort_keys=True, indent=2 if args.pretty else None,
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
    print("\nIssues:")
    print("  None" if not manifest.issues else "\n".join(f"  [{i.severity_hint}] {i.code}" for i in manifest.issues))
    print("\nLimitations:")
    print("  None" if not manifest.limitations else "\n".join(f"  {item}" for item in manifest.limitations))
    return 0


if __name__ == "__main__": raise SystemExit(main())
