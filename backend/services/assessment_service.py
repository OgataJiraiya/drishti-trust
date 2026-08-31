"""Concurrency-safe assessment lifecycle and immutable snapshot operations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_text
from backend.core.hashing import sha256_bytes, sha256_json
from backend.database.models import AssessmentRecord, AssessmentSnapshotRecord
from backend.database.repository import (
    AssessmentMembershipRepository,
    AssessmentRepository,
    AssessmentSnapshotRepository,
    ModuleRunAuthenticationRepository,
)
from backend.schemas.assessments import (
    AssessmentCreate,
    AssessmentDetails,
    AssessmentSnapshot,
    SnapshotVerification,
)
from backend.services.audit_service import AuditService
from backend.services.summary_service import SummaryService
from backend.services.audit_outbox_service import AuditOutboxService


class AssessmentConflict(ValueError):
    pass


class AssessmentMissing(ValueError):
    pass


@dataclass(frozen=True)
class SealResult:
    assessment: AssessmentDetails
    snapshot: AssessmentSnapshot
    created: bool


class AssessmentService:
    def __init__(self, summary_service: SummaryService, outbox: AuditOutboxService | None = None) -> None:
        self.summary_service = summary_service
        self.outbox = outbox

    def create(self, body: AssessmentCreate, session: Session) -> AssessmentDetails:
        metadata_json = canonical_json_text(body.metadata)
        if len(metadata_json.encode("utf-8")) > 16_384:
            raise AssessmentConflict("metadata exceeds the 16384-byte canonical JSON limit")
        session.execute(text("BEGIN IMMEDIATE"))
        repository = AssessmentRepository(session)
        if repository.get(body.assessment_id) is not None:
            session.rollback()
            raise AssessmentConflict("assessment_id already exists")
        record = AssessmentRecord(
            assessment_id=body.assessment_id, name=body.name,
            description=body.description, status="DRAFT", metadata_json=metadata_json,
        )
        repository.add_pending(record)
        if self.outbox:
            self.outbox.stage(session, f"ASSESSMENT_CREATED:{body.assessment_id}",
                "ASSESSMENT_CREATED", "assessment", body.assessment_id,
                {"assessment_id": body.assessment_id, "name": body.name})
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise AssessmentConflict("assessment_id already exists") from exc
        return self.to_details(record)

    def activate(self, assessment_id: str, session: Session) -> tuple[AssessmentDetails, bool]:
        session.execute(text("BEGIN IMMEDIATE"))
        repository = AssessmentRepository(session)
        record = repository.get(assessment_id)
        if record is None:
            session.rollback()
            raise AssessmentMissing("Assessment not found")
        if record.status == "ACTIVE":
            session.rollback()
            return self.to_details(record), False
        if record.status != "DRAFT":
            session.rollback()
            raise AssessmentConflict("SEALED assessment cannot be activated")
        active = repository.active()
        if active is not None:
            session.rollback()
            raise AssessmentConflict(f"Assessment {active.assessment_id!r} is already ACTIVE")
        record.status = "ACTIVE"
        record.activated_at = datetime.now(timezone.utc)
        if self.outbox:
            self.outbox.stage(session, f"ASSESSMENT_ACTIVATED:{assessment_id}",
                "ASSESSMENT_ACTIVATED", "assessment", assessment_id,
                {"assessment_id": assessment_id})
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise AssessmentConflict("Another assessment is already ACTIVE") from exc
        return self.to_details(record), True

    def seal(self, assessment_id: str, session: Session, audit_service: AuditService) -> SealResult:
        session.execute(text("BEGIN IMMEDIATE"))
        assessments = AssessmentRepository(session)
        snapshots = AssessmentSnapshotRepository(session)
        record = assessments.get(assessment_id)
        if record is None:
            session.rollback()
            raise AssessmentMissing("Assessment not found")
        existing = snapshots.get(assessment_id)
        if record.status == "SEALED":
            if existing is None:
                session.rollback()
                raise RuntimeError("SEALED assessment has no immutable snapshot")
            result = SealResult(self.to_details(record), self.to_snapshot(existing), False)
            session.rollback()
            if self.verify(assessment_id, session).status != "VALID":
                raise RuntimeError("SEALED assessment snapshot is internally inconsistent")
            return result
        if record.status != "ACTIVE":
            session.rollback()
            raise AssessmentConflict("Only an ACTIVE assessment can be sealed")
        if existing is not None:
            session.rollback()
            raise RuntimeError("Unsealed assessment unexpectedly has a snapshot")

        sealed_at = datetime.now(timezone.utc)
        summary = self.summary_service.build(
            session, audit_service, "authenticated", assessment_id, "SEALED", "EXPLICIT_ASSESSMENT"
        )
        runs = AssessmentMembershipRepository(session).all_runs(assessment_id)
        auth_repository = ModuleRunAuthenticationRepository(session)
        run_set = []
        for run in runs:
            auth = auth_repository.get(run.run_id)
            if auth is None or auth.request_hash != run.request_hash:
                session.rollback()
                raise RuntimeError("Assessment run authentication provenance is inconsistent")
            run_set.append({
                "run_id": run.run_id,
                "request_hash": run.request_hash,
                "authentication_mode": auth.authentication_mode,
            })
        run_set.sort(key=lambda item: item["run_id"])
        run_set_hash = sha256_json(run_set)
        payload = {
            "assessment_id": assessment_id,
            "schema_version": "1",
            "trust_scope": "authenticated",
            "sealed_at": sealed_at.isoformat().replace("+00:00", "Z"),
            "run_set": run_set,
            "run_set_hash": run_set_hash,
            "summary": summary.model_dump(mode="json"),
        }
        summary_json = canonical_json_text(payload)
        snapshot_record = AssessmentSnapshotRecord(
            assessment_id=assessment_id, schema_version="1", summary_json=summary_json,
            summary_hash=sha256_bytes(summary_json.encode("utf-8")), run_set_hash=run_set_hash,
            run_count=len(runs), trusted_finding_count=summary.trusted_finding_count,
            excluded_untrusted_finding_count=summary.excluded_untrusted_finding_count,
            created_at=sealed_at,
        )
        snapshots.add_pending(snapshot_record)
        record.status = "SEALED"
        record.sealed_at = sealed_at
        if self.outbox:
            overall = payload["summary"]["overall"]
            self.outbox.stage(session, f"ASSESSMENT_SEALED:{assessment_id}",
                "ASSESSMENT_SEALED", "assessment", assessment_id, {
                    "assessment_id": assessment_id, "snapshot_hash": snapshot_record.summary_hash,
                    "run_set_hash": run_set_hash, "run_count": len(runs),
                    "trusted_finding_count": summary.trusted_finding_count,
                    "overall_disposition": overall["disposition"],
                })
        session.commit()
        return SealResult(self.to_details(record), self.to_snapshot(snapshot_record), True)

    def verify(self, assessment_id: str, session: Session) -> SnapshotVerification:
        snapshot = AssessmentSnapshotRepository(session).get(assessment_id)
        if snapshot is None:
            raise AssessmentMissing("Assessment snapshot not found")
        try:
            payload = json.loads(snapshot.summary_json)
            recomputed_summary_hash = sha256_json(payload)
            run_set = payload["run_set"]
            payload_run_hash = sha256_json(run_set)
            current_runs = AssessmentMembershipRepository(session).all_runs(assessment_id)
            auth_repository = ModuleRunAuthenticationRepository(session)
            current_set = []
            for run in current_runs:
                auth = auth_repository.get(run.run_id)
                current_set.append({
                    "run_id": run.run_id, "request_hash": run.request_hash,
                    "authentication_mode": auth.authentication_mode if auth else "MISSING",
                })
            current_set.sort(key=lambda item: item["run_id"])
            recomputed_run_set_hash = sha256_json(current_set)
            valid = (
                recomputed_summary_hash == snapshot.summary_hash
                and payload_run_hash == snapshot.run_set_hash
                and recomputed_run_set_hash == snapshot.run_set_hash
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            recomputed_summary_hash = sha256_bytes(snapshot.summary_json.encode("utf-8"))
            recomputed_run_set_hash = "0" * 64
            valid = False
        return SnapshotVerification(
            assessment_id=assessment_id, status="VALID" if valid else "INVALID",
            stored_summary_hash=snapshot.summary_hash,
            recomputed_summary_hash=recomputed_summary_hash,
            stored_run_set_hash=snapshot.run_set_hash,
            recomputed_run_set_hash=recomputed_run_set_hash,
        )

    @staticmethod
    def to_details(record: AssessmentRecord) -> AssessmentDetails:
        def aware(value: datetime | None) -> datetime | None:
            return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
        return AssessmentDetails(
            assessment_id=record.assessment_id, name=record.name, description=record.description,
            status=record.status, metadata=json.loads(record.metadata_json),
            created_at=aware(record.created_at), activated_at=aware(record.activated_at),
            sealed_at=aware(record.sealed_at),
        )

    @staticmethod
    def to_snapshot(record: AssessmentSnapshotRecord) -> AssessmentSnapshot:
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return AssessmentSnapshot(
            assessment_id=record.assessment_id, schema_version=record.schema_version,
            payload=json.loads(record.summary_json), summary_hash=record.summary_hash,
            run_set_hash=record.run_set_hash, run_count=record.run_count,
            trusted_finding_count=record.trusted_finding_count,
            excluded_untrusted_finding_count=record.excluded_untrusted_finding_count,
            created_at=created_at,
        )
