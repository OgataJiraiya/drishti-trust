"""Adapters from detailed Person-3 findings to frozen Finding Schema v1."""
from __future__ import annotations

from typing import Any

from backend.core.hashing import sha256_json
from backend.schemas.evidence import Finding
from backend.schemas.inference import VerificationFinding


def _stable_id(prefix: str, material: dict[str, Any]) -> str:
    return f"{prefix}-{sha256_json(material)[:16].upper()}"


def replay_to_common_finding(
    receipt_id: str, checks: dict[str, str], internal_findings: list[VerificationFinding]
) -> Finding:
    signals = sorted(finding.attack_class for finding in internal_findings)
    material = {"receipt_id": receipt_id, "category": "REPLAY_DETECTED", "signals": signals}
    return Finding(
        finding_id=_stable_id("F-INF-REPLAY", material),
        module="inference_integrity",
        asset_type="inference",
        asset_id=f"inference:{receipt_id}",
        category="REPLAY_DETECTED",
        severity="CRITICAL",
        confidence=1.0,
        reason="A previously accepted inference receipt was submitted again.",
        evidence=[
            f"signature={checks.get('signature', 'UNAVAILABLE')}",
            f"nonce={checks.get('nonce', 'UNAVAILABLE')}",
            f"sequence={checks.get('sequence', 'UNAVAILABLE')}",
        ],
        recommendation="REJECT",
        limitations=["Replay detection depends on retained acceptance state and configured freshness controls."],
    )


def model_substitution_to_common_finding(model_id: str, finding: VerificationFinding) -> Finding:
    expected = str(finding.evidence.get("expected_sha256", "UNAVAILABLE"))
    observed = str(finding.evidence.get("observed_sha256", "UNAVAILABLE"))
    material = {"model_id": model_id, "category": "MODEL_SUBSTITUTION", "expected": expected, "observed": observed}
    return Finding(
        finding_id=_stable_id("F-INF-MODEL", material),
        module="inference_integrity",
        asset_type="model",
        asset_id=f"model:{model_id}",
        category="MODEL_SUBSTITUTION",
        severity="CRITICAL",
        confidence=finding.confidence,
        reason="Observed model artifact does not match the approved model digest.",
        evidence=[f"expected_sha256={expected}", f"observed_sha256={observed}"],
        recommendation="QUARANTINE",
        limitations=["Digest verification proves exact artifact identity, not behavioral safety."],
    )


def output_tampering_to_common_finding(receipt_id: str, finding: VerificationFinding) -> Finding:
    expected = str(finding.evidence.get("expected_sha256", "UNAVAILABLE"))
    observed = str(finding.evidence.get("observed_sha256", "UNAVAILABLE"))
    material = {"receipt_id": receipt_id, "category": "OUTPUT_TAMPERING", "expected": expected, "observed": observed}
    return Finding(
        finding_id=_stable_id("F-INF-OUTPUT", material),
        module="inference_integrity",
        asset_type="inference",
        asset_id=f"inference:{receipt_id}",
        category="OUTPUT_TAMPERING",
        severity="CRITICAL",
        confidence=finding.confidence,
        reason="Inference output digest does not match the signed receipt.",
        evidence=[f"expected_sha256={expected}", f"observed_sha256={observed}"],
        recommendation="REJECT",
        limitations=[],
    )
