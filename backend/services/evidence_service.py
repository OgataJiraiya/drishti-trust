"""Immutable, idempotent storage for the frozen team Finding contract."""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_text
from backend.database.models import FindingRecord
from backend.database.repository import EvidenceRepository
from backend.schemas.evidence import EvidenceIngestionResponse, Finding


class EvidenceConflict(ValueError):
    pass


class EvidenceService:
    def ingest(self, finding: Finding, session: Session) -> EvidenceIngestionResponse:
        canonical = canonical_json_text(finding.model_dump(mode="json"))
        session.execute(text("BEGIN IMMEDIATE"))
        repository = EvidenceRepository(session)
        existing = repository.get(finding.finding_id)
        if existing is not None:
            if existing.finding_json != canonical:
                session.rollback()
                raise EvidenceConflict("finding_id already exists with different immutable Finding JSON")
            response = EvidenceIngestionResponse(finding=self.to_schema(existing), result="EXISTS")
            session.rollback()
            return response
        record = self.record_from_finding(finding, canonical)
        repository.add(record)
        return EvidenceIngestionResponse(finding=self.to_schema(record), result="CREATED")

    @staticmethod
    def record_from_finding(finding: Finding, canonical: str | None = None) -> FindingRecord:
        """Build a persistence record without adding or committing it."""
        data = finding.model_dump(mode="json")
        return FindingRecord(
            finding_id=finding.finding_id,
            module=finding.module.value,
            asset_type=finding.asset_type,
            asset_id=finding.asset_id,
            category=finding.category,
            severity=finding.severity.value,
            confidence=finding.confidence,
            reason=finding.reason,
            evidence_json=canonical_json_text(data["evidence"]),
            recommendation=finding.recommendation.value,
            limitations_json=canonical_json_text(data["limitations"]),
            finding_json=canonical or canonical_json_text(data),
        )

    @staticmethod
    def to_schema(record: FindingRecord) -> Finding:
        return Finding.model_validate(json.loads(record.finding_json))
