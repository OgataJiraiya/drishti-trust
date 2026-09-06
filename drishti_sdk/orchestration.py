"""Minimal four-module coordinator; detectors and backend policy remain authoritative."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.schemas.common import FindingModule
from backend.schemas.evidence import Finding
from .client import DrishtiClient
from .errors import AuthenticationError


@dataclass(frozen=True)
class FullAssessmentResult:
    assessment_id: str
    runs: dict[str, dict]
    summary: dict
    audit: dict
    lifecycle: str
    snapshot: dict
    checkpoint: dict
    security: dict[str, str]


class FullAssessmentOrchestrator:
    """Submit caller-produced findings through the existing authenticated lifecycle."""

    def __init__(self, client: DrishtiClient) -> None:
        self.client = client

    @staticmethod
    def _public_pem(key: Ed25519PrivateKey) -> str:
        return key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")

    def run(self, assessment_id: str, findings_by_module: Mapping[str, Sequence[Finding]]) -> FullAssessmentResult:
        expected = [module.value for module in FindingModule]
        if set(findings_by_module) != set(expected):
            raise ValueError("exactly one result for each frozen assurance module is required")
        self.client.create_assessment(assessment_id, "DRISHTI full-system assessment")
        self.client.activate_assessment(assessment_id)
        runs: dict[str, dict] = {}
        security: dict[str, str] = {}
        for index, module in enumerate(expected, 1):
            findings = list(findings_by_module[module])
            if any(item.module.value != module for item in findings):
                raise ValueError(f"finding module mismatch for {module}")
            producer = f"full-system-{module}"
            key = Ed25519PrivateKey.generate()
            key_digest = sha256(key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()
            key_id = f"full-system-key-{module}-{key_digest[:24]}"
            self.client.register_producer(producer, module.replace("_", " ").title(), module)
            self.client.register_producer_key(producer, key_id, self._public_pem(key))
            run = self.client.build_run(module=module, assessment_id=assessment_id,
                producer=producer, producer_version="1.0.0", findings=findings,
                run_id=f"{assessment_id}-RUN-{index}")
            submitted = self.client.submit_signed_run(run=run, key_id=key_id, private_key=key)
            if index == 1:
                replay = self.client.submit_signed_run(run=run, key_id=key_id, private_key=key)
                security["signed_run_replay"] = replay["result"]
                wrong = Ed25519PrivateKey.generate()
                try:
                    self.client._request("POST", "/api/integration/signed-runs", json={
                        "run": run.model_dump(mode="json"), "key_id": key_id,
                        "signature": self.client.sign_run(run, private_key=wrong),
                    })
                    raise RuntimeError("wrong signature was accepted")
                except AuthenticationError:
                    security["wrong_signature"] = "REJECTED"
                tampered = run.model_dump(mode="json")
                tampered["producer_version"] = "tampered"
                try:
                    self.client._request("POST", "/api/integration/signed-runs", json={
                        "run": tampered, "key_id": key_id,
                        "signature": self.client.sign_run(run, private_key=key),
                    })
                    raise RuntimeError("mutated signed payload was accepted")
                except AuthenticationError:
                    security["payload_tamper"] = "REJECTED"
            stored = self.client.get_run(run.run_id)
            if not stored["authentication"]["authenticated"]:
                raise RuntimeError(f"backend did not authenticate {module}")
            runs[module] = {"submission": submitted, "stored": stored}
        summary = self.client.get_summary(assessment_id)
        audit = self.client.verify_audit()
        self.client.seal_assessment(assessment_id)
        lifecycle = self.client.get_assessment(assessment_id)["status"]
        snapshot = self.client.verify_snapshot(assessment_id)
        self.client.drain_outbox()
        checkpoint_bundle = self.client.create_checkpoint()["bundle"]
        checkpoint = self.client.verify_checkpoint(checkpoint_bundle)
        return FullAssessmentResult(assessment_id, runs, summary, audit,
            lifecycle, snapshot, checkpoint, security)
