"""Persistent SHA-256 trust anchors for opaque model artifacts."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_text
from backend.database.models import RegisteredModelRecord
from backend.database.repository import ModelRegistryRepository
from backend.schemas.inference import VerificationFinding
from backend.schemas.model import (
    ModelRegistrationResponse,
    ModelRegistryEntry,
    ModelStatus,
    ModelVerificationResponse,
    RegisterModelRequest,
)
from backend.services.audit_outbox_service import AuditOutboxService


class ModelRegistrationConflict(ValueError):
    pass


def safe_filename(filename: str | None) -> str | None:
    """Retain only a display basename; filenames are never used as paths or identity."""
    if filename is None:
        return None
    normalized = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return normalized or None


class ModelRegistryService:
    def __init__(self, outbox: AuditOutboxService | None = None) -> None:
        self.outbox = outbox

    def register_digest(self, request: RegisterModelRequest, session: Session) -> ModelRegistrationResponse:
        return self._register(
            session=session,
            model_id=request.model_id,
            display_name=request.display_name,
            original_filename=request.original_filename,
            expected_sha256=request.expected_sha256,
            artifact_size_bytes=request.artifact_size_bytes,
            declared_format=request.declared_format,
            version=request.version,
            metadata=request.metadata,
            source="PROVIDED_DIGEST",
        )

    def register_artifact(
        self,
        *,
        session: Session,
        model_id: str,
        display_name: str,
        original_filename: str | None,
        observed_sha256: str,
        artifact_size_bytes: int,
        declared_format: str | None,
        version: str | None,
    ) -> ModelRegistrationResponse:
        return self._register(
            session=session,
            model_id=model_id,
            display_name=display_name,
            original_filename=original_filename,
            expected_sha256=observed_sha256,
            artifact_size_bytes=artifact_size_bytes,
            declared_format=declared_format,
            version=version,
            metadata={},
            source="HASHED_ARTIFACT",
        )

    def _register(self, *, session: Session, model_id: str, display_name: str,
                  original_filename: str | None, expected_sha256: str,
                  artifact_size_bytes: int | None, declared_format: str | None,
                  version: str | None, metadata: dict[str, Any], source: str) -> ModelRegistrationResponse:
        repository = ModelRegistryRepository(session)
        session.execute(text("BEGIN IMMEDIATE"))
        existing = repository.get(model_id)
        if existing is not None:
            if existing.expected_sha256 != expected_sha256:
                session.rollback()
                raise ModelRegistrationConflict(
                    "model_id is already bound to a different approved SHA-256 digest; use a new model_id"
                )
            response = ModelRegistrationResponse(entry=self.to_schema(existing), idempotent=True)
            session.rollback()
            return response
        record = RegisteredModelRecord(
            model_id=model_id,
            display_name=display_name,
            original_filename=safe_filename(original_filename),
            expected_sha256=expected_sha256,
            artifact_size_bytes=artifact_size_bytes,
            declared_format=declared_format,
            version=version,
            status=ModelStatus.APPROVED.value,
            metadata_json=canonical_json_text(metadata),
            registration_source=source,
        )
        session.add(record)
        if self.outbox:
            payload = {"model_id": model_id, "expected_sha256": expected_sha256,
                       "registration_source": source, "status": ModelStatus.APPROVED.value}
            if artifact_size_bytes is not None: payload["artifact_size_bytes"] = artifact_size_bytes
            self.outbox.stage(session, f"MODEL_REGISTERED:{model_id}", "MODEL_REGISTERED",
                              "model", model_id, payload)
        session.commit()
        return ModelRegistrationResponse(entry=self.to_schema(record), idempotent=False)

    def verify(self, model_id: str, observed_sha256: str, session: Session) -> ModelVerificationResponse:
        record = ModelRegistryRepository(session).get(model_id)
        if record is None:
            return ModelVerificationResponse(
                model_id=model_id,
                valid=False,
                status="QUARANTINE",
                checks={"registry_entry": "UNAVAILABLE", "model_digest": "UNAVAILABLE", "registry_status": "UNAVAILABLE"},
                expected_sha256=None,
                observed_sha256=observed_sha256,
                findings=[self._finding(
                    "UNREGISTERED_MODEL", "HIGH",
                    "No approved reference digest exists for this model.",
                    {"model_id": model_id, "observed_sha256": observed_sha256},
                )],
            )
        digest_matches = record.expected_sha256 == observed_sha256
        findings: list[VerificationFinding] = []
        if not digest_matches:
            findings.append(self._finding(
                "MODEL_SUBSTITUTION", "CRITICAL",
                "Observed model artifact digest does not match the approved model digest.",
                {"model_id": model_id, "expected_sha256": record.expected_sha256, "observed_sha256": observed_sha256},
            ))
        if record.status != ModelStatus.APPROVED.value:
            attack = "MODEL_REVOKED" if record.status == ModelStatus.REVOKED.value else "MODEL_QUARANTINED"
            findings.append(self._finding(
                attack, "CRITICAL",
                f"The registered model is not authorized because its registry status is {record.status}.",
                {"model_id": model_id, "registry_status": record.status},
            ))
        valid = digest_matches and record.status == ModelStatus.APPROVED.value
        return ModelVerificationResponse(
            model_id=model_id,
            valid=valid,
            status="ACCEPT" if valid else "QUARANTINE",
            checks={
                "registry_entry": "VALID",
                "model_digest": "VALID" if digest_matches else "INVALID",
                "registry_status": record.status,
            },
            expected_sha256=record.expected_sha256,
            observed_sha256=observed_sha256,
            findings=findings,
        )

    def revoke(self, model_id: str, session: Session) -> ModelRegistryEntry | None:
        repository = ModelRegistryRepository(session)
        session.execute(text("BEGIN IMMEDIATE"))
        record = repository.get(model_id)
        if record is None:
            session.rollback()
            return None
        if record.status != ModelStatus.REVOKED.value:
            record.status = ModelStatus.REVOKED.value
            if self.outbox:
                self.outbox.stage(session, f"MODEL_REVOKED:{model_id}", "MODEL_REVOKED",
                    "model", model_id, {"model_id": model_id,
                    "expected_sha256": record.expected_sha256, "status": record.status})
            session.commit()
        else:
            session.rollback()
        return self.to_schema(record)

    @staticmethod
    def to_schema(record: RegisteredModelRecord) -> ModelRegistryEntry:
        return ModelRegistryEntry(
            model_id=record.model_id,
            display_name=record.display_name,
            original_filename=record.original_filename,
            expected_sha256=record.expected_sha256,
            artifact_size_bytes=record.artifact_size_bytes,
            declared_format=record.declared_format,
            version=record.version,
            status=record.status,
            metadata=json.loads(record.metadata_json),
            registration_source=record.registration_source,
            registered_at=record.registered_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _finding(attack_class: str, severity: str, reason: str, evidence: dict[str, Any]) -> VerificationFinding:
        return VerificationFinding(
            attack_class=attack_class,
            severity=severity,
            confidence=1.0,
            reason=reason,
            evidence=evidence,
            recommendation="QUARANTINE",
        )
