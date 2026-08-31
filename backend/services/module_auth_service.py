"""Fail-closed authorization and Ed25519 verification for module runs."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.core.signing import verify_signature
from backend.database.repository import ProducerKeyRepository, ProducerRepository
from backend.integrations.signing import module_run_signing_bytes
from backend.schemas.module_auth import SignedModuleRunSubmission
from backend.services.producer_service import ProducerService


class ModuleAuthenticationFailed(ValueError):
    """Base class for authentication failures that must precede ingestion."""


class InvalidModuleSignature(ModuleAuthenticationFailed):
    pass


class ModuleAuthorizationDenied(ModuleAuthenticationFailed):
    pass


@dataclass(frozen=True)
class AuthenticatedModule:
    producer_id: str
    key_id: str
    key_fingerprint: str


class ModuleAuthService:
    def authenticate(
        self, submission: SignedModuleRunSubmission, session: Session
    ) -> AuthenticatedModule:
        producer = ProducerRepository(session).get(submission.run.producer)
        if producer is None:
            raise ModuleAuthorizationDenied("Module producer is not authorized")
        if producer.status != "APPROVED":
            raise ModuleAuthorizationDenied("Module producer is not authorized")
        if producer.module != submission.run.module.value:
            raise ModuleAuthorizationDenied("Producer is not authorized for the claimed module")

        key_record = ProducerKeyRepository(session).get(submission.key_id)
        if key_record is None:
            raise ModuleAuthorizationDenied("Producer key is not authorized")
        if key_record.producer_id != producer.producer_id:
            raise ModuleAuthorizationDenied("Producer key is not authorized")
        if key_record.status != "ACTIVE":
            raise ModuleAuthorizationDenied("Producer key is not authorized")

        public_key, _canonical_pem, fingerprint = ProducerService.parse_public_key(
            key_record.public_key_pem
        )
        if fingerprint != key_record.public_key_fingerprint:
            raise ModuleAuthorizationDenied("Stored producer key identity is inconsistent")
        if not verify_signature(
            public_key, module_run_signing_bytes(submission.run), submission.signature
        ):
            raise InvalidModuleSignature("Module run signature is invalid")
        return AuthenticatedModule(
            producer_id=producer.producer_id,
            key_id=key_record.key_id,
            key_fingerprint=fingerprint,
        )
