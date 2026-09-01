"""Thin reuse of the frozen SDK signed module-run pipeline for Model Integrity."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from drishti_sdk.adapters import ModelIntegrityAdapter
from drishti_sdk.client import DrishtiClient
from .findings import (
    FindingMappingContext, FindingMappingPolicy, ModelIntegrityEvidenceBundle,
    ModelIntegrityFindingMapper,
)

_MODEL_ARTIFACT_ID = re.compile(r"^model:sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ModelIntegrityIntegrationResult:
    finding_count: int
    signed_run_id: str
    assessment_id: str
    candidate_artifact_id: str
    backend_result: str


class ModelIntegrityRunBuilder:
    """Build, sign, and optionally submit through existing SDK methods only."""
    def __init__(self, client: DrishtiClient, *, policy: FindingMappingPolicy | None = None) -> None:
        self.client = client
        self.adapter = ModelIntegrityAdapter(client)
        self.mapper = ModelIntegrityFindingMapper(self.adapter, policy=policy)

    def map_findings(self, bundle: ModelIntegrityEvidenceBundle,
                     context: FindingMappingContext | None = None) -> list[Finding]:
        return self.mapper.map(bundle, context)

    def build_run(self, *, assessment_id: str, producer: str,
                  producer_version: str | None, findings: list[Finding],
                  run_id: str | None = None) -> ModuleRunSubmission:
        if not findings:
            raise ValueError("the frozen ModuleRunSubmission requires at least one Finding")
        return self.client.build_run(module="model_integrity", assessment_id=assessment_id,
            producer=producer, producer_version=producer_version, findings=findings, run_id=run_id)

    def sign_run(self, run: ModuleRunSubmission, *, private_key: Ed25519PrivateKey | None = None,
                 private_key_path: str | Path | None = None) -> str:
        return self.client.sign_run(run, private_key=private_key, private_key_path=private_key_path)

    def submit_signed_run(self, *, run: ModuleRunSubmission, key_id: str,
                          candidate_artifact_id: str,
                          private_key: Ed25519PrivateKey | None = None,
                          private_key_path: str | Path | None = None
                          ) -> ModelIntegrityIntegrationResult:
        if not _MODEL_ARTIFACT_ID.fullmatch(candidate_artifact_id) \
                or any(item.asset_id != candidate_artifact_id for item in run.findings):
            raise ValueError("candidate artifact identity must match every mapped Finding")
        response = self.client.submit_signed_run(run=run, key_id=key_id, private_key=private_key,
            private_key_path=private_key_path)
        return ModelIntegrityIntegrationResult(len(run.findings), run.run_id,
            run.assessment_id or "", candidate_artifact_id, str(response.get("result", "UNKNOWN")))
