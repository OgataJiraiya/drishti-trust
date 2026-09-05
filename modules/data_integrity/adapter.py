"""Dataset Integrity mapping and signed ModuleRun integration via the frozen SDK."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from drishti_sdk.adapters import DatasetIntegrityAdapter
from drishti_sdk.client import DrishtiClient


@dataclass(frozen=True)
class DatasetIntegrityIntegrationResult:
    finding_count: int
    run_id: str
    assessment_id: str
    backend_result: str


class DatasetIntegrityRunBuilder:
    """Validate detector output and reuse the system signing/submission path."""

    def __init__(self, client: DrishtiClient) -> None:
        if not isinstance(client, DrishtiClient):
            raise TypeError("client must be DrishtiClient")
        self.client = client
        self.adapter = DatasetIntegrityAdapter(client)

    def map_findings(self, findings: list[dict[str, Any]]) -> list[Finding]:
        return [Finding.model_validate(item) for item in findings]

    def build_run(self, *, assessment_id: str, producer: str,
                  producer_version: str | None, findings: list[Finding],
                  run_id: str | None = None) -> ModuleRunSubmission:
        if any(str(item.module) != "dataset_integrity" for item in findings):
            raise ValueError("every Finding must belong to dataset_integrity")
        return self.client.build_run(module="dataset_integrity", assessment_id=assessment_id,
            producer=producer, producer_version=producer_version, findings=findings, run_id=run_id)

    def sign_run(self, run: ModuleRunSubmission, *,
                 private_key: Ed25519PrivateKey | None = None,
                 private_key_path: str | Path | None = None) -> str:
        return self.client.sign_run(run, private_key=private_key,
                                    private_key_path=private_key_path)

    def submit_signed_run(self, *, run: ModuleRunSubmission, key_id: str,
                          private_key: Ed25519PrivateKey | None = None,
                          private_key_path: str | Path | None = None
                          ) -> DatasetIntegrityIntegrationResult:
        response = self.client.submit_signed_run(run=run, key_id=key_id,
            private_key=private_key, private_key_path=private_key_path)
        return DatasetIntegrityIntegrationResult(len(run.findings), run.run_id,
            run.assessment_id or "", str(response.get("result", "UNKNOWN")))
