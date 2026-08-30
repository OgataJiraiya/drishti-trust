"""Receipt creation and evidence-based verification."""
from __future__ import annotations

import base64
import binascii
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_bytes, canonical_json_text
from backend.core.hashing import sha256_bytes, sha256_json
from backend.core.signing import public_key_id, sign_bytes, verify_signature
from backend.database.models import AcceptedInferenceRecord, InferenceReceiptRecord
from backend.database.repository import AcceptanceRepository, ReceiptRepository
from backend.schemas.inference import (
    AcceptReceiptRequest, CreateReceiptRequest, InferenceEvidence, InferenceReceipt, InputEvidence,
    ModelEvidence, ProcessingEvidence, SecurityEvidence, SignerEvidence,
    VerificationArtifacts, VerificationFinding, VerificationResponse,
)


class InvalidInputEncoding(ValueError):
    pass


def decode_input(value: str, max_bytes: int) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputEncoding("input_base64 must be valid base64") from exc
    if len(decoded) > max_bytes:
        raise InvalidInputEncoding(f"decoded input exceeds {max_bytes} bytes")
    return decoded


class ProvenanceService:
    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        public_key: Ed25519PublicKey,
        max_input_bytes: int,
        max_receipt_age_seconds: int = 300,
        max_future_skew_seconds: int = 30,
    ) -> None:
        self.private_key = private_key
        self.public_key = public_key
        self.max_input_bytes = max_input_bytes
        self.max_receipt_age_seconds = max_receipt_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds

    def create_receipt(self, request: CreateReceiptRequest, session: Session) -> InferenceReceipt:
        image_bytes = decode_input(request.input_base64, self.max_input_bytes)
        repository = ReceiptRepository(session)
        sequence = repository.next_sequence()
        previous = repository.latest()
        unsigned = {
            "receipt_id": f"INF-{sequence:08d}",
            "input": InputEvidence(filename=request.filename, sha256=sha256_bytes(image_bytes)),
            "model": ModelEvidence(model_id=request.model_id, sha256=request.model_sha256, behavior_id=request.behavior_id),
            "processing": ProcessingEvidence(
                preprocessing_sha256=sha256_json(request.preprocessing),
                config_sha256=sha256_json(request.config),
            ),
            "inference": InferenceEvidence(output=request.output, output_sha256=sha256_json(request.output)),
            "security": SecurityEvidence(
                sequence=sequence,
                nonce=secrets.token_hex(32),
                timestamp=datetime.now(timezone.utc),
                previous_receipt_hash=previous.receipt_hash if previous else None,
            ),
            "signer": SignerEvidence(key_id=public_key_id(self.public_key)),
        }
        payload = {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value for key, value in unsigned.items()}
        receipt = InferenceReceipt(**unsigned, signature=sign_bytes(self.private_key, canonical_json_bytes(payload)))
        receipt_json = canonical_json_text(receipt.model_dump(mode="json"))
        repository.add(InferenceReceiptRecord(
            receipt_id=receipt.receipt_id,
            receipt_json=receipt_json,
            receipt_hash=sha256_bytes(receipt_json.encode("utf-8")),
            sequence=receipt.security.sequence,
            nonce=receipt.security.nonce,
            timestamp=receipt.security.timestamp,
            signature=receipt.signature,
        ))
        return receipt

    def verify(self, receipt: InferenceReceipt, artifacts: VerificationArtifacts) -> VerificationResponse:
        checks: dict[str, str] = {}
        findings: list[VerificationFinding] = []
        key_matches = receipt.signer.key_id == public_key_id(self.public_key)
        signature_valid = key_matches and verify_signature(
            self.public_key, canonical_json_bytes(receipt.signed_payload()), receipt.signature
        )
        checks["signature"] = "VALID" if signature_valid else "INVALID"
        if not signature_valid:
            findings.append(self._finding("SIGNATURE_INVALID", "Receipt signature or signer key identifier is invalid", {}, "REJECT"))

        self._check_bytes("input_digest", artifacts.input_base64, receipt.input.sha256, "INPUT_TAMPERING", findings, checks)
        self._check_hash("model_digest", artifacts.model_sha256, receipt.model.sha256, "MODEL_SUBSTITUTION", findings, checks)
        self._check_json("preprocessing_digest", artifacts.preprocessing, receipt.processing.preprocessing_sha256, "CONFIG_TAMPERING", findings, checks)
        self._check_json("config_digest", artifacts.config, receipt.processing.config_sha256, "CONFIG_TAMPERING", findings, checks)
        self._check_json("output_digest", artifacts.output, receipt.inference.output_sha256, "OUTPUT_TAMPERING", findings, checks)
        has_critical = any(item.severity == "CRITICAL" for item in findings)
        complete = all(value == "VALID" for value in checks.values())
        valid = signature_valid and complete and not has_critical
        status = "ACCEPT" if valid else ("REJECT" if has_critical else "REVIEW")
        return VerificationResponse(valid=valid, status=status, checks=checks, findings=findings)

    def accept(self, request: AcceptReceiptRequest, session: Session) -> VerificationResponse:
        """Atomically validate and accept a receipt as a new inference event."""
        result = self.verify(request.receipt, request.artifacts)
        checks = dict(result.checks)
        findings = list(result.findings)
        receipt = request.receipt
        receipt_hash = sha256_json(receipt.model_dump(mode="json"))

        now = datetime.now(timezone.utc)
        timestamp = receipt.security.timestamp.astimezone(timezone.utc)
        if timestamp < now - timedelta(seconds=self.max_receipt_age_seconds):
            checks["timestamp"] = "INVALID"
            findings.append(self._finding(
                "STALE_RECEIPT",
                "The receipt timestamp is outside the permitted freshness window.",
                {
                    "receipt_timestamp": timestamp.isoformat(),
                    "maximum_age_seconds": self.max_receipt_age_seconds,
                },
                "REJECT",
            ))
        elif timestamp > now + timedelta(seconds=self.max_future_skew_seconds):
            checks["timestamp"] = "INVALID"
            findings.append(self._finding(
                "FUTURE_TIMESTAMP",
                "The receipt timestamp is beyond the permitted future clock skew.",
                {
                    "receipt_timestamp": timestamp.isoformat(),
                    "maximum_future_skew_seconds": self.max_future_skew_seconds,
                },
                "REJECT",
            ))
        else:
            checks["timestamp"] = "VALID"

        # BEGIN IMMEDIATE obtains SQLite's write reservation before replay reads.
        # This makes the check-and-insert operation atomic across sessions/processes.
        session.execute(text("BEGIN IMMEDIATE"))
        repository = AcceptanceRepository(session)
        nonce_record = repository.by_nonce(receipt.security.nonce)
        duplicate_record = repository.by_receipt_hash(receipt_hash)
        sequence_record = repository.by_sequence(receipt.security.sequence)
        highest_sequence = repository.highest_sequence()

        if nonce_record is not None:
            checks["nonce"] = "INVALID"
            findings.append(self._finding(
                "NONCE_REUSE",
                "The inference nonce has already been accepted.",
                {"previous_receipt_id": nonce_record.receipt_id},
                "REJECT",
            ))
        else:
            checks["nonce"] = "VALID"

        if duplicate_record is not None:
            checks["receipt"] = "INVALID"
            findings.append(self._finding(
                "REPLAY_DETECTED",
                "This exact signed receipt has already been accepted.",
                {"previous_receipt_id": duplicate_record.receipt_id},
                "REJECT",
            ))
        else:
            checks["receipt"] = "VALID"

        if sequence_record is not None:
            checks["sequence"] = "INVALID"
            findings.append(self._finding(
                "DUPLICATE_SEQUENCE",
                "The inference sequence has already been accepted.",
                {"sequence": receipt.security.sequence},
                "REJECT",
            ))
        elif highest_sequence is not None and receipt.security.sequence < highest_sequence:
            checks["sequence"] = "INVALID"
            findings.append(self._finding(
                "SEQUENCE_REGRESSION",
                "The inference sequence regresses below the accepted stream position.",
                {"observed_sequence": receipt.security.sequence, "highest_accepted_sequence": highest_sequence},
                "REJECT",
            ))
        else:
            checks["sequence"] = "VALID"

        has_critical = any(finding.severity == "CRITICAL" for finding in findings)
        complete = all(value == "VALID" for value in checks.values())
        if has_critical or not complete:
            session.rollback()
            return VerificationResponse(valid=False, status="REJECT" if has_critical else "REVIEW", checks=checks, findings=findings)

        repository.add(AcceptedInferenceRecord(
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt_hash,
            nonce=receipt.security.nonce,
            sequence=receipt.security.sequence,
            receipt_timestamp=timestamp,
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # A database uniqueness constraint is the final fail-closed guard.
            checks["nonce"] = "INVALID"
            findings.append(self._finding(
                "REPLAY_DETECTED",
                "The inference event conflicted with already accepted replay state.",
                {},
                "REJECT",
            ))
            return VerificationResponse(valid=False, status="REJECT", checks=checks, findings=findings)
        return VerificationResponse(valid=True, status="ACCEPT", checks=checks, findings=[])

    def _check_bytes(self, name: str, supplied: str | None, expected: str, attack: str, findings: list, checks: dict) -> None:
        if supplied is None:
            checks[name] = "UNAVAILABLE"; return
        try:
            observed = sha256_bytes(decode_input(supplied, self.max_input_bytes))
        except InvalidInputEncoding:
            observed = "INVALID_ENCODING"
        self._compare(name, expected, observed, attack, findings, checks)

    def _check_json(self, name: str, supplied: object | None, expected: str, attack: str, findings: list, checks: dict) -> None:
        if supplied is None:
            checks[name] = "UNAVAILABLE"; return
        self._compare(name, expected, sha256_json(supplied), attack, findings, checks)

    def _check_hash(self, name: str, observed: str | None, expected: str, attack: str, findings: list, checks: dict) -> None:
        if observed is None:
            checks[name] = "UNAVAILABLE"; return
        self._compare(name, expected, observed, attack, findings, checks)

    def _compare(self, name: str, expected: str, observed: str, attack: str, findings: list, checks: dict) -> None:
        if observed == expected:
            checks[name] = "VALID"; return
        checks[name] = "INVALID"
        findings.append(self._finding(attack, f"{name} does not match the signed receipt", {"expected_sha256": expected, "observed_sha256": observed}, "REJECT"))

    @staticmethod
    def _finding(attack: str, reason: str, evidence: dict, recommendation: str) -> VerificationFinding:
        return VerificationFinding(attack_class=attack, severity="CRITICAL", confidence=1.0, reason=reason, evidence=evidence, recommendation=recommendation)
