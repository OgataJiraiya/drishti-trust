"""Atomic cross-module batch ingestion into the immutable evidence store."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_text
from backend.core.hashing import sha256_bytes
from backend.database.models import ModuleRunAuthenticationRecord, ModuleRunRecord
from backend.database.repository import (
    EvidenceRepository,
    ModuleRunAuthenticationRepository,
    ModuleRunRepository,
)
from backend.schemas.evidence import Finding
from backend.schemas.integration import (
    ModuleRunDetails,
    ModuleRunAuthentication,
    ModuleRunIngestionResponse,
    ModuleRunSubmission,
)
from backend.services.evidence_service import EvidenceService


class IntegrationConflict(ValueError):
    """An immutable run or finding identity was submitted with changed content."""


@dataclass(frozen=True)
class IntegrationIngestResult:
    response: ModuleRunIngestionResponse
    created_findings: list[Finding]
    request_hash: str


@dataclass(frozen=True)
class RunAuthentication:
    mode: str
    producer_id: str
    key_id: str | None = None
    key_fingerprint: str | None = None


class IntegrationService:
    def ingest_run(
        self,
        submission: ModuleRunSubmission,
        session: Session,
        authentication: RunAuthentication,
    ) -> IntegrationIngestResult:
        request_json = canonical_json_text(submission.model_dump(mode="json"))
        request_hash = sha256_bytes(request_json.encode("utf-8"))
        finding_ids = [finding.finding_id for finding in submission.findings]

        # Reserve SQLite write state before checking any immutable identity.
        session.execute(text("BEGIN IMMEDIATE"))
        run_repository = ModuleRunRepository(session)
        evidence_repository = EvidenceRepository(session)
        existing_run = run_repository.get(submission.run_id)
        if existing_run is not None:
            if existing_run.request_hash != request_hash or existing_run.request_json != request_json:
                session.rollback()
                raise IntegrationConflict("run_id already exists with different immutable submission content")
            response = ModuleRunIngestionResponse(
                run_id=submission.run_id,
                module=submission.module,
                producer=submission.producer,
                producer_version=submission.producer_version,
                result="EXISTS",
                total_findings=len(submission.findings),
                created_findings=0,
                existing_findings=len(submission.findings),
                finding_ids=finding_ids,
            )
            session.rollback()
            return IntegrationIngestResult(
                response=response, created_findings=[], request_hash=request_hash
            )

        pending: list[tuple[Finding, str]] = []
        existing_count = 0
        # Complete preflight happens before staging a single insert.
        for finding in submission.findings:
            canonical_finding = canonical_json_text(finding.model_dump(mode="json"))
            existing_finding = evidence_repository.get(finding.finding_id)
            if existing_finding is None:
                pending.append((finding, canonical_finding))
            elif existing_finding.finding_json == canonical_finding:
                existing_count += 1
            else:
                session.rollback()
                raise IntegrationConflict(
                    f"finding_id {finding.finding_id!r} already exists with different immutable Finding JSON"
                )

        for finding, canonical_finding in pending:
            evidence_repository.add_pending(
                EvidenceService.record_from_finding(finding, canonical_finding)
            )
        run_repository.add_pending(ModuleRunRecord(
            run_id=submission.run_id,
            module=submission.module.value,
            producer=submission.producer,
            producer_version=submission.producer_version,
            request_hash=request_hash,
            request_json=request_json,
            finding_count=len(submission.findings),
            created_count=len(pending),
            existing_count=existing_count,
            finding_ids_json=canonical_json_text(finding_ids),
        ))
        # These models intentionally have no ORM relationship; flush the parent
        # explicitly so SQLite's foreign-key check cannot observe child-first order.
        session.flush()
        ModuleRunAuthenticationRepository(session).add_pending(ModuleRunAuthenticationRecord(
            run_id=submission.run_id,
            authentication_mode=authentication.mode,
            producer_id=authentication.producer_id,
            key_id=authentication.key_id,
            key_fingerprint=authentication.key_fingerprint,
            request_hash=request_hash,
        ))
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise IntegrationConflict("module run conflicts with an existing immutable identity") from exc

        response = ModuleRunIngestionResponse(
            run_id=submission.run_id,
            module=submission.module,
            producer=submission.producer,
            producer_version=submission.producer_version,
            result="CREATED",
            total_findings=len(submission.findings),
            created_findings=len(pending),
            existing_findings=existing_count,
            finding_ids=finding_ids,
        )
        return IntegrationIngestResult(
            response=response,
            created_findings=[finding for finding, _canonical in pending],
            request_hash=request_hash,
        )

    @staticmethod
    def to_details(record: ModuleRunRecord, session: Session) -> ModuleRunDetails:
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        auth_record = ModuleRunAuthenticationRepository(session).get(record.run_id)
        if auth_record is None:
            raise RuntimeError(f"Module run {record.run_id!r} has no authentication provenance")
        authenticated_at = auth_record.authenticated_at
        if authenticated_at.tzinfo is None:
            authenticated_at = authenticated_at.replace(tzinfo=timezone.utc)
        return ModuleRunDetails(
            run_id=record.run_id,
            module=record.module,
            producer=record.producer,
            producer_version=record.producer_version,
            request_hash=record.request_hash,
            total_findings=record.finding_count,
            created_findings=record.created_count,
            existing_findings=record.existing_count,
            finding_ids=json.loads(record.finding_ids_json),
            created_at=created_at,
            authentication=ModuleRunAuthentication(
                authenticated=auth_record.authentication_mode == "ED25519",
                mode=auth_record.authentication_mode,
                producer_id=auth_record.producer_id,
                key_id=auth_record.key_id,
                key_fingerprint=auth_record.key_fingerprint,
                request_hash=auth_record.request_hash,
                authenticated_at=authenticated_at,
            ),
        )
