"""Transactional receipt repository."""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.models import (
    AcceptedInferenceRecord,
    AssessmentRecord,
    AssessmentRunMembershipRecord,
    AssessmentSnapshotRecord,
    AuditLogRecord,
    FindingRecord,
    InferenceReceiptRecord,
    ModuleProducerRecord,
    ModuleRunAuthenticationRecord,
    ModuleRunRecord,
    ProducerKeyRecord,
    RegisteredModelRecord,
)


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, assessment_id: str) -> AssessmentRecord | None:
        return self.session.get(AssessmentRecord, assessment_id)

    def active(self) -> AssessmentRecord | None:
        return self.session.scalar(select(AssessmentRecord).where(AssessmentRecord.status == "ACTIVE").limit(1))

    def add_pending(self, record: AssessmentRecord) -> None:
        self.session.add(record)

    def list(self, offset: int, limit: int, status: str | None = None) -> tuple[int, list[AssessmentRecord]]:
        query = select(AssessmentRecord)
        if status is not None:
            query = query.where(AssessmentRecord.status == status)
        total = int(self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        records = list(self.session.scalars(
            query.order_by(AssessmentRecord.created_at, AssessmentRecord.assessment_id)
            .offset(offset).limit(limit)
        ))
        return total, records


class AssessmentMembershipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> AssessmentRunMembershipRecord | None:
        return self.session.get(AssessmentRunMembershipRecord, run_id)

    def add_pending(self, record: AssessmentRunMembershipRecord) -> None:
        self.session.add(record)

    def runs(self, assessment_id: str, offset: int, limit: int) -> tuple[int, list[ModuleRunRecord]]:
        query = (select(ModuleRunRecord)
                 .join(AssessmentRunMembershipRecord,
                       AssessmentRunMembershipRecord.run_id == ModuleRunRecord.run_id)
                 .where(AssessmentRunMembershipRecord.assessment_id == assessment_id))
        total = int(self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        records = list(self.session.scalars(
            query.order_by(ModuleRunRecord.created_at, ModuleRunRecord.run_id).offset(offset).limit(limit)
        ))
        return total, records

    def all_runs(self, assessment_id: str) -> list[ModuleRunRecord]:
        return self.runs(assessment_id, 0, 1_000_000)[1]

    def finding_ids(self, assessment_id: str, authentication_mode: str | None = None) -> set[str]:
        query = (select(ModuleRunRecord.finding_ids_json)
                 .join(AssessmentRunMembershipRecord,
                       AssessmentRunMembershipRecord.run_id == ModuleRunRecord.run_id)
                 .join(ModuleRunAuthenticationRecord,
                       ModuleRunAuthenticationRecord.run_id == ModuleRunRecord.run_id)
                 .where(
                     AssessmentRunMembershipRecord.assessment_id == assessment_id,
                     ModuleRunAuthenticationRecord.request_hash == ModuleRunRecord.request_hash,
                 ))
        if authentication_mode is not None:
            query = query.where(ModuleRunAuthenticationRecord.authentication_mode == authentication_mode)
        return {finding_id for encoded in self.session.scalars(query) for finding_id in json.loads(encoded)}


class AssessmentSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, assessment_id: str) -> AssessmentSnapshotRecord | None:
        return self.session.get(AssessmentSnapshotRecord, assessment_id)

    def add_pending(self, record: AssessmentSnapshotRecord) -> None:
        self.session.add(record)


class ReceiptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def next_sequence(self) -> int:
        return int(self.session.scalar(select(func.coalesce(func.max(InferenceReceiptRecord.sequence), 0))) or 0) + 1

    def latest(self) -> InferenceReceiptRecord | None:
        return self.session.scalar(
            select(InferenceReceiptRecord).order_by(InferenceReceiptRecord.sequence.desc()).limit(1)
        )

    def add(self, record: InferenceReceiptRecord) -> None:
        self.session.add(record)
        self.session.commit()

    def get(self, receipt_id: str) -> InferenceReceiptRecord | None:
        return self.session.get(InferenceReceiptRecord, receipt_id)

    def list(self, offset: int, limit: int) -> tuple[int, list[InferenceReceiptRecord]]:
        total = int(self.session.scalar(select(func.count()).select_from(InferenceReceiptRecord)) or 0)
        items = list(self.session.scalars(
            select(InferenceReceiptRecord)
            .order_by(InferenceReceiptRecord.sequence.desc())
            .offset(offset).limit(limit)
        ))
        return total, items


class AcceptanceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_nonce(self, nonce: str) -> AcceptedInferenceRecord | None:
        return self.session.scalar(select(AcceptedInferenceRecord).where(AcceptedInferenceRecord.nonce == nonce))

    def by_receipt_hash(self, receipt_hash: str) -> AcceptedInferenceRecord | None:
        return self.session.scalar(
            select(AcceptedInferenceRecord).where(AcceptedInferenceRecord.receipt_hash == receipt_hash)
        )

    def by_sequence(self, sequence: int) -> AcceptedInferenceRecord | None:
        return self.session.scalar(
            select(AcceptedInferenceRecord).where(AcceptedInferenceRecord.sequence == sequence)
        )

    def highest_sequence(self) -> int | None:
        value = self.session.scalar(select(func.max(AcceptedInferenceRecord.sequence)))
        return int(value) if value is not None else None

    def add(self, record: AcceptedInferenceRecord) -> None:
        self.session.add(record)


class ModelRegistryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, model_id: str) -> RegisteredModelRecord | None:
        return self.session.get(RegisteredModelRecord, model_id)

    def add(self, record: RegisteredModelRecord) -> None:
        self.session.add(record)
        self.session.commit()

    def list(self, offset: int, limit: int) -> tuple[int, list[RegisteredModelRecord]]:
        total = int(self.session.scalar(select(func.count()).select_from(RegisteredModelRecord)) or 0)
        items = list(self.session.scalars(
            select(RegisteredModelRecord)
            .order_by(RegisteredModelRecord.registered_at.desc(), RegisteredModelRecord.model_id)
            .offset(offset).limit(limit)
        ))
        return total, items

    def revoke(self, record: RegisteredModelRecord) -> None:
        record.status = "REVOKED"
        self.session.commit()


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self) -> AuditLogRecord | None:
        return self.session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.sequence.desc()).limit(1))

    def add(self, record: AuditLogRecord) -> None:
        self.session.add(record)
        self.session.commit()

    def get(self, audit_id: str) -> AuditLogRecord | None:
        return self.session.get(AuditLogRecord, audit_id)

    def all_ordered(self) -> list[AuditLogRecord]:
        return list(self.session.scalars(select(AuditLogRecord).order_by(AuditLogRecord.sequence)))

    def list(self, offset: int, limit: int, event_type: str | None = None,
             asset_type: str | None = None, asset_id: str | None = None) -> tuple[int, list[AuditLogRecord]]:
        query = select(AuditLogRecord)
        if event_type:
            query = query.where(AuditLogRecord.event_type == event_type)
        if asset_type:
            query = query.where(AuditLogRecord.asset_type == asset_type)
        if asset_id:
            query = query.where(AuditLogRecord.asset_id == asset_id)
        total = int(self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        records = list(self.session.scalars(query.order_by(AuditLogRecord.sequence).offset(offset).limit(limit)))
        return total, records


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, finding_id: str) -> FindingRecord | None:
        return self.session.get(FindingRecord, finding_id)

    def add(self, record: FindingRecord) -> None:
        self.session.add(record)
        self.session.commit()

    def add_pending(self, record: FindingRecord) -> None:
        """Stage a finding inside a caller-owned transaction without committing."""
        self.session.add(record)

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(FindingRecord)) or 0)

    def all_ingestion_order(self) -> list[FindingRecord]:
        return list(self.session.scalars(
            select(FindingRecord).order_by(FindingRecord.ingested_at, FindingRecord.finding_id)
        ))

    def list(self, offset: int, limit: int, module: str | None = None,
             asset_type: str | None = None, asset_id: str | None = None,
             category: str | None = None, severity: str | None = None,
             recommendation: str | None = None) -> tuple[int, list[FindingRecord]]:
        query = select(FindingRecord)
        filters = {
            FindingRecord.module: module,
            FindingRecord.asset_type: asset_type,
            FindingRecord.asset_id: asset_id,
            FindingRecord.category: category,
            FindingRecord.severity: severity,
            FindingRecord.recommendation: recommendation,
        }
        for column, value in filters.items():
            if value is not None:
                query = query.where(column == value)
        total = int(self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        records = list(self.session.scalars(
            query.order_by(FindingRecord.ingested_at, FindingRecord.finding_id).offset(offset).limit(limit)
        ))
        return total, records


class ModuleRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> ModuleRunRecord | None:
        return self.session.get(ModuleRunRecord, run_id)

    def add_pending(self, record: ModuleRunRecord) -> None:
        self.session.add(record)

    def list(self, offset: int, limit: int, module: str | None = None) -> tuple[int, list[ModuleRunRecord]]:
        query = select(ModuleRunRecord)
        if module is not None:
            query = query.where(ModuleRunRecord.module == module)
        total = int(self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        records = list(self.session.scalars(
            query.order_by(ModuleRunRecord.created_at, ModuleRunRecord.run_id).offset(offset).limit(limit)
        ))
        return total, records


class ModuleRunAuthenticationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> ModuleRunAuthenticationRecord | None:
        return self.session.get(ModuleRunAuthenticationRecord, run_id)

    def add_pending(self, record: ModuleRunAuthenticationRecord) -> None:
        self.session.add(record)

    def authenticated_finding_ids(self) -> set[str]:
        runs = self.session.execute(
            select(ModuleRunRecord.finding_ids_json)
            .join(
                ModuleRunAuthenticationRecord,
                ModuleRunAuthenticationRecord.run_id == ModuleRunRecord.run_id,
            )
            .where(
                ModuleRunAuthenticationRecord.authentication_mode == "ED25519",
                ModuleRunAuthenticationRecord.request_hash == ModuleRunRecord.request_hash,
            )
        ).scalars()
        return {finding_id for encoded in runs for finding_id in json.loads(encoded)}


class ProducerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, producer_id: str) -> ModuleProducerRecord | None:
        return self.session.get(ModuleProducerRecord, producer_id)

    def add_pending(self, record: ModuleProducerRecord) -> None:
        self.session.add(record)

    def list(self, offset: int, limit: int) -> tuple[int, list[ModuleProducerRecord]]:
        total = int(self.session.scalar(select(func.count()).select_from(ModuleProducerRecord)) or 0)
        records = list(self.session.scalars(
            select(ModuleProducerRecord)
            .order_by(ModuleProducerRecord.created_at, ModuleProducerRecord.producer_id)
            .offset(offset).limit(limit)
        ))
        return total, records


class ProducerKeyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key_id: str) -> ProducerKeyRecord | None:
        return self.session.get(ProducerKeyRecord, key_id)

    def by_fingerprint(self, fingerprint: str) -> ProducerKeyRecord | None:
        return self.session.scalar(
            select(ProducerKeyRecord).where(
                ProducerKeyRecord.public_key_fingerprint == fingerprint
            ).limit(1)
        )

    def add_pending(self, record: ProducerKeyRecord) -> None:
        self.session.add(record)

    def list_for_producer(self, producer_id: str) -> list[ProducerKeyRecord]:
        return list(self.session.scalars(
            select(ProducerKeyRecord)
            .where(ProducerKeyRecord.producer_id == producer_id)
            .order_by(ProducerKeyRecord.created_at, ProducerKeyRecord.key_id)
        ))
