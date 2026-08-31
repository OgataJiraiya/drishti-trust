"""Thin module-labelled adapters; no detector or scoring logic lives here."""
from __future__ import annotations
from typing import Any

from backend.schemas.evidence import Finding
from .client import DrishtiClient


class ModuleAdapter:
    module: str
    def __init__(self, client: DrishtiClient) -> None: self.client = client

    def finding(self, *, asset_type: str, asset_id: str, category: str,
                severity: str, confidence: float, reason: str,
                evidence: list[str], recommendation: str,
                stable_evidence_key: object, limitations: list[str] | None = None,
                finding_id: str | None = None) -> Finding:
        return self.client.build_finding(module=self.module, asset_type=asset_type,
            asset_id=asset_id, category=category, severity=severity,
            confidence=confidence, reason=reason, evidence=evidence,
            recommendation=recommendation, limitations=limitations,
            stable_evidence_key=stable_evidence_key, finding_id=finding_id)

    def from_detector_result(self, result: dict[str, Any], **finding_fields: Any) -> Finding:
        """Bind a caller-selected stable key; detector semantics remain caller-owned."""
        stable_key = finding_fields.pop("stable_evidence_key", result)
        return self.finding(stable_evidence_key=stable_key, **finding_fields)


class DatasetIntegrityAdapter(ModuleAdapter): module = "dataset_integrity"
class ModelIntegrityAdapter(ModuleAdapter): module = "model_integrity"
class InferenceIntegrityAdapter(ModuleAdapter): module = "inference_integrity"
class DistributionShiftAdapter(ModuleAdapter): module = "distribution_shift"
