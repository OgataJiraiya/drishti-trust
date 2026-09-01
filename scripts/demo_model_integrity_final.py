#!/usr/bin/env python3
"""Disposable offline M6 product demonstration."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.model_integrity.demo import (ModelIntegrityFinalOrchestrator, SCENARIOS,
    ScenarioKind, create_behavioral_specimen, create_static_specimen)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="DRISHTI-TRUST Model Integrity M6 final demo (offline by default)")
    value.add_argument("--scenario", choices=[item.value for item in ScenarioKind], action="append")
    value.add_argument("--all", action="store_true", help="run all static scenarios; behavioral execution is still excluded")
    value.add_argument("--behavioral", action="store_true", help="explicitly permit the trigger-sensitive scenario")
    value.add_argument("--offline", action="store_true", help="document the default local mapping-only mode")
    value.add_argument("--json", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    selected = [ScenarioKind(item) for item in (args.scenario or [])]
    if not selected: selected = [ScenarioKind.CLEAN, ScenarioKind.WEIGHT_CHANGE,
        ScenarioKind.STRUCTURE_CHANGE, ScenarioKind.PARAMETER_NAN]
    if args.all: selected = [item for item in ScenarioKind if item != ScenarioKind.TRIGGER_SENSITIVE]
    if ScenarioKind.TRIGGER_SENSITIVE in selected and not args.behavioral:
        parser().error("trigger-sensitive execution requires --behavioral")
    orchestrator = ModelIntegrityFinalOrchestrator(); results = []
    with tempfile.TemporaryDirectory(prefix="drishti-model-m6-") as directory:
        root = Path(directory)
        for kind in selected:
            scenario = SCENARIOS[kind]
            if kind == ScenarioKind.TRIGGER_SENSITIVE:
                reference = create_behavioral_specimen(root / f"{kind}-reference.onnx", False)
                candidate = create_behavioral_specimen(root / f"{kind}-candidate.onnx", True)
                results.append(orchestrator.run_behavioral_scenario(scenario, reference, candidate))
            else:
                reference = create_static_specimen(root / f"{kind}-reference.onnx", ScenarioKind.CLEAN)
                if kind == ScenarioKind.CLEAN:
                    candidate = root / f"{kind}-candidate.onnx"; candidate.write_bytes(reference.read_bytes())
                else: candidate = create_static_specimen(root / f"{kind}-candidate.onnx", kind)
                results.append(orchestrator.run_static_scenario(scenario, reference, candidate))
        report = orchestrator.report(results)
        print(report.to_json() if args.json else report.to_text())
    return 0


if __name__ == "__main__": raise SystemExit(main())
