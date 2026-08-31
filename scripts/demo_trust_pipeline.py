#!/usr/bin/env python3
"""Deterministic four-module Person-3 demo against a running loopback backend."""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from drishti_sdk import (
    AssessmentStateError, DatasetIntegrityAdapter, DistributionShiftAdapter,
    DrishtiClient, InferenceIntegrityAdapter, ModelIntegrityAdapter,
)

MODULES = [
    ("dataset_integrity", DatasetIntegrityAdapter, "dataset", "training-set-v1", "ANNOTATION_ANOMALY", "near-duplicate and poisoned-label screening completed"),
    ("model_integrity", ModelIntegrityAdapter, "model", "vision-model-v1", "PARAMETER_OUTLIER", "parameter-outlier screening completed"),
    ("inference_integrity", InferenceIntegrityAdapter, "inference", "demo-stream", "OUTPUT_CONSISTENCY", "replay and output-integrity screening completed"),
    ("distribution_shift", DistributionShiftAdapter, "dataset", "deployment-window-1", "DISTRIBUTION_SHIFT", "deployment distribution drift screening completed"),
]


def public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def run_demo(client: DrishtiClient, assessment_id: str = "DEMO-A", emit=print) -> dict:
    timings: dict[str, float] = {}
    emit("DRISHTI-TRUST END-TO-END DEMO\n")
    assert client.health()["status"] == "ok"; emit("[PASS] Backend health")
    client.create_assessment(assessment_id, "SIH Person-3 trust pipeline")
    emit("[PASS] Assessment created")
    client.activate_assessment(assessment_id); emit("[PASS] Assessment active")

    submitted = []
    for index, (module, adapter_type, asset_type, asset_id, category, reason) in enumerate(MODULES, 1):
        producer, key_id = f"demo-{module}", f"demo-key-{module}"
        key = Ed25519PrivateKey.generate()
        client.register_producer(producer, f"Demo {module}", module)
        client.register_producer_key(producer, key_id, public_pem(key))
        adapter = adapter_type(client)
        finding = adapter.finding(asset_type=asset_type, asset_id=asset_id,
            category=category, severity="LOW", confidence=0.9, reason=reason,
            evidence=[f"deterministic demo signal {index}"], recommendation="REVIEW",
            limitations=["Representative integration evidence; not detector implementation."],
            stable_evidence_key={"demo_case": index, "version": 1})
        run = client.build_run(module=module, assessment_id=assessment_id, producer=producer,
            producer_version="1.0.0", findings=[finding], run_id=f"DEMO-RUN-{index}")
        started = perf_counter()
        response = client.submit_signed_run(run=run, key_id=key_id, private_key=key)
        timings[f"submit_{module}_ms"] = (perf_counter() - started) * 1000
        assert response["result"] == "CREATED"
        submitted.append((run, key_id, key))
        emit(f"[PASS] {module.replace('_', ' ').title()} run authenticated")

    started = perf_counter(); summary = client.get_summary(assessment_id)
    timings["summary_ms"] = (perf_counter() - started) * 1000
    assert summary["overall"]["assessment_coverage"] == 1.0
    assert summary["overall"]["score_status"] == "COMPLETE"
    emit("[PASS] Coverage: 100%")
    emit("[PASS] Assurance status: COMPLETE")
    for module, details in summary["modules"].items():
        emit(f"  {module}: {details['assurance_score']} ({details['disposition']})")
    emit(f"\nOverall score: {summary['overall']['assurance_score']}")
    emit(f"Disposition: {summary['overall']['disposition']}")
    assert client.verify_audit()["status"] == "VALID"; emit("\n[PASS] Audit chain VALID")

    started = perf_counter(); snapshot = client.seal_assessment(assessment_id)
    timings["seal_ms"] = (perf_counter() - started) * 1000
    emit("[PASS] Assessment SEALED")
    assert client.verify_snapshot(assessment_id)["status"] == "VALID"; emit("[PASS] Snapshot VALID")
    client.drain_outbox()
    started = perf_counter(); checkpoint = client.create_checkpoint()["bundle"]
    timings["checkpoint_ms"] = (perf_counter() - started) * 1000
    assert client.verify_checkpoint(checkpoint)["status"] == "VALID"; emit("[PASS] Audit checkpoint VALID")
    outbox = client.outbox_status()
    assert outbox["healthy"] and outbox["pending_count"] == 0; emit("[PASS] Audit outbox healthy")

    run, key_id, key = submitted[0]
    try:
        client.submit_signed_run(run=run, key_id=key_id, private_key=key)
        raise AssertionError("SEALED assessment unexpectedly accepted a run")
    except AssessmentStateError:
        emit("[PASS] SEALED assessment rejected further signed run")
    emit("\nLatency sanity (ms): " + ", ".join(
        f"{name}={value:.1f}" for name, value in timings.items()))
    emit("\nTRUST PIPELINE COMPLETE")
    return {"summary": summary, "snapshot": snapshot, "checkpoint": checkpoint,
            "outbox": outbox, "timings_ms": timings}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete Person-3 loopback demo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--assessment-id", default="DEMO-A")
    parser.add_argument("--admin-token", default=os.getenv("DRISHTI_ADMIN_BEARER_TOKEN"))
    args = parser.parse_args()
    if not args.admin_token:
        parser.error("set DRISHTI_ADMIN_BEARER_TOKEN or pass --admin-token")
    with DrishtiClient(args.base_url, admin_token=args.admin_token) as client:
        run_demo(client, args.assessment_id)
    return 0


if __name__ == "__main__": raise SystemExit(main())
