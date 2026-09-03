#!/usr/bin/env python3
"""Offline, bounded DRISHTI four-module demonstration against a temporary backend."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.schemas.inference import VerificationFinding
from backend.services.finding_adapters import replay_to_common_finding
from drishti_sdk import DrishtiClient, FullAssessmentOrchestrator
from modules.data_integrity.adapter import DatasetIntegrityRunBuilder
from modules.data_integrity.analyzer import analyze_dataset
from modules.distribution_shift.demo import build_interpretation_for_scenario
from modules.distribution_shift.findings import DistributionShiftFindingMapper
from modules.distribution_shift.integration import DistributionShiftRunBuilder
from modules.model_integrity.demo import ScenarioKind, create_static_specimen
from modules.model_integrity.findings import ModelIntegrityEvidenceBundle
from modules.model_integrity.integration import ModelIntegrityRunBuilder
from modules.model_integrity.service import ModelIntegrityService


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(client: DrishtiClient) -> None:
    for _ in range(100):
        try:
            if client.health()["status"] == "ok":
                return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("temporary backend did not start")


def _module_findings(client: DrishtiClient, root: Path, concern: bool):
    dataset = root / "dataset"
    dataset.mkdir()
    Image.new("RGB", (8, 8), (20, 30, 40)).save(dataset / "one.png")
    if concern:
        (dataset / "two.png").write_bytes((dataset / "one.png").read_bytes())
    data_result = analyze_dataset(str(dataset), run_phash=False, run_labels=False, run_ood=False)
    data = DatasetIntegrityRunBuilder(client).map_findings(data_result["findings"])

    model_path = create_static_specimen(
        root / "model.onnx", ScenarioKind.PARAMETER_NAN if concern else ScenarioKind.CLEAN
    )
    model = ModelIntegrityRunBuilder(client).map_findings(
        ModelIntegrityEvidenceBundle(manifest=ModelIntegrityService().inspect(model_path))
    )

    payload = {"filename": "sample.bin", "input_base64": base64.b64encode(b"sample").decode(),
        "model_id": "demo-model", "model_sha256": "a" * 64, "preprocessing": {"v": 1},
        "config": {"threshold": 0.5}, "output": {"label": "plane"}}
    receipt = client._request("POST", "/api/inference/receipt", json=payload)
    artifacts = {key: payload[key] for key in (
        "input_base64", "model_sha256", "preprocessing", "config", "output")}
    accepted = client._request("POST", "/api/inference/accept",
                               json={"receipt": receipt, "artifacts": artifacts})
    if accepted["status"] != "ACCEPT":
        raise RuntimeError("initial inference receipt was not accepted")
    inference = []
    if concern:
        replay = client._request("POST", "/api/inference/accept",
                                 json={"receipt": receipt, "artifacts": artifacts})
        internal = [VerificationFinding.model_validate(item) for item in replay["findings"]]
        inference = [replay_to_common_finding(receipt["receipt_id"], replay["checks"], internal)]

    interpretation = build_interpretation_for_scenario("broad-shift" if concern else "clean")[3]
    distribution = DistributionShiftRunBuilder(client).map_findings(interpretation)
    return {"dataset_integrity": data, "model_integrity": model,
        "inference_integrity": inference, "distribution_shift": distribution}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic offline full-system demo")
    parser.add_argument("--scenario", choices=("clean", "concern"), default="concern")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="drishti-full-system-") as directory:
        root = Path(directory)
        port = _free_port()
        token = "DRISHTI-LOCAL-DEMO"
        env = dict(os.environ, DRISHTI_DATA_DIR=str(root / "data"),
                   DRISHTI_KEY_DIR=str(root / "keys"), DRISHTI_ADMIN_BEARER_TOKEN=token,
                   DRISHTI_INTERNAL_INGEST_BEARER_TOKEN=token)
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=Path(__file__).resolve().parents[1], env=env)
        try:
            with DrishtiClient(f"http://127.0.0.1:{port}", admin_token=token) as client:
                _wait(client)
                assessment_id = f"FULL-{args.scenario.upper()}"
                result = FullAssessmentOrchestrator(client).run(
                    assessment_id, _module_findings(client, root, args.scenario == "concern"))
                print(f"Assessment: {assessment_id}")
                for module, details in result.runs.items():
                    stored = details["stored"]
                    print(f"  {module}: findings={stored['total_findings']} authenticated="
                          f"{stored['authentication']['authenticated']}")
                overall = result.summary["overall"]
                print(f"Coverage: {overall['assessment_coverage']}")
                print(f"System assurance: {overall['assurance_score']} ({overall['score_status']})")
                print(f"System disposition: {overall['disposition']}")
                print(f"Audit: {result.audit['status']}")
                print(f"Lifecycle: {result.lifecycle}")
                print(f"Snapshot: {result.snapshot['status']}")
                print(f"Checkpoint: {result.checkpoint['status']}")
                print("Security: " + ", ".join(
                    f"{name}={status}" for name, status in sorted(result.security.items())))
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
