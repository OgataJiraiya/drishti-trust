"""Inference receipt API."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.api.audit import append_event
from backend.api.dependencies import get_session
from backend.database.repository import ReceiptRepository
from backend.schemas.inference import (
    AcceptReceiptRequest,
    CreateReceiptRequest,
    InferenceReceipt,
    InferenceListResponse,
    VerificationResponse,
    VerifyReceiptRequest,
)
from backend.services.provenance_service import InvalidInputEncoding, ProvenanceService

router = APIRouter(prefix="/api/inference", tags=["inference provenance"])


async def get_service(request: Request) -> ProvenanceService:
    return request.app.state.provenance_service


@router.post(
    "/receipt", response_model=InferenceReceipt, status_code=status.HTTP_201_CREATED,
    summary="Create and store a signed inference receipt",
    description="Hashes the supplied artifacts, signs every security-relevant receipt field with Ed25519, and stores the receipt locally.",
)
async def create_receipt(
    body: CreateReceiptRequest,
    request: Request,
    session: Session = Depends(get_session),
    service: ProvenanceService = Depends(get_service),
) -> InferenceReceipt:
    try:
        receipt = service.create_receipt(body, session)
        append_event(
            request=request,
            event_type="INFERENCE_RECEIPT_CREATED", asset_type="inference",
            asset_id=receipt.receipt_id,
            payload={"receipt_id": receipt.receipt_id, "input_sha256": receipt.input.sha256,
                     "model_id": receipt.model.model_id, "model_sha256": receipt.model.sha256,
                     "output_sha256": receipt.inference.output_sha256},
        )
        return receipt
    except InvalidInputEncoding as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/verify",
    response_model=VerificationResponse,
    summary="Verify receipt integrity (read-only)",
    description=(
        "Verifies the signature and supplied artifact digests without consuming a nonce, "
        "advancing replay state, or accepting a new inference event."
    ),
)
async def verify_receipt(
    body: VerifyReceiptRequest,
    request: Request,
    service: ProvenanceService = Depends(get_service),
) -> VerificationResponse:
    result = service.verify(body.receipt, body.artifacts)
    attacks = {finding.attack_class: finding for finding in result.findings}
    if "SIGNATURE_INVALID" in attacks:
        event_type = "SIGNATURE_INVALID"
        payload = {"receipt_id": body.receipt.receipt_id, "signature_status": "INVALID"}
    elif "OUTPUT_TAMPERING" in attacks:
        event_type = "OUTPUT_TAMPERING_DETECTED"
        payload = {"receipt_id": body.receipt.receipt_id, **attacks["OUTPUT_TAMPERING"].evidence}
    else:
        event_type = "INFERENCE_VERIFIED"
        payload = {"receipt_id": body.receipt.receipt_id, "status": result.status, "checks": result.checks}
    append_event(request, event_type, "inference", body.receipt.receipt_id, payload)
    return result


@router.post(
    "/accept",
    response_model=VerificationResponse,
    summary="Accept a receipt as a new inference event",
    description=(
        "Performs integrity verification plus timestamp, nonce, exact-receipt, and global "
        "monotonic-sequence replay checks. Successful acceptance atomically persists replay state."
    ),
)
async def accept_receipt(
    body: AcceptReceiptRequest,
    request: Request,
    session: Session = Depends(get_session),
    service: ProvenanceService = Depends(get_service),
) -> VerificationResponse:
    result = service.accept(body, session)
    attacks = {finding.attack_class: finding for finding in result.findings}
    if "REPLAY_DETECTED" in attacks or "NONCE_REUSE" in attacks or "DUPLICATE_SEQUENCE" in attacks:
        event_type = "REPLAY_DETECTED"
        payload = {
            "receipt_id": body.receipt.receipt_id,
            "signature_status": result.checks.get("signature"),
            "nonce_status": result.checks.get("nonce"),
            "sequence_status": result.checks.get("sequence"),
            "signals": sorted(attacks),
        }
    elif result.status == "ACCEPT":
        event_type = "INFERENCE_ACCEPTED"
        payload = {"receipt_id": body.receipt.receipt_id, "sequence": body.receipt.security.sequence}
    elif "SIGNATURE_INVALID" in attacks:
        event_type = "SIGNATURE_INVALID"
        payload = {"receipt_id": body.receipt.receipt_id, "signature_status": "INVALID"}
    elif "OUTPUT_TAMPERING" in attacks:
        event_type = "OUTPUT_TAMPERING_DETECTED"
        payload = {"receipt_id": body.receipt.receipt_id, **attacks["OUTPUT_TAMPERING"].evidence}
    else:
        event_type = "INFERENCE_VERIFICATION_FAILED"
        payload = {"receipt_id": body.receipt.receipt_id, "status": result.status, "checks": result.checks}
    append_event(request, event_type, "inference", body.receipt.receipt_id, payload)
    return result


@router.get("/{receipt_id}", response_model=InferenceReceipt, summary="Retrieve a stored inference receipt", description="Returns one previously created receipt without changing replay acceptance state.")
async def get_receipt(receipt_id: str, session: Session = Depends(get_session)) -> InferenceReceipt:
    record = ReceiptRepository(session).get(receipt_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return InferenceReceipt.model_validate(json.loads(record.receipt_json))


@router.get("", response_model=InferenceListResponse, summary="List stored inference receipts", description="Returns paginated archived receipts; archival does not imply replay acceptance.")
async def list_receipts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    total, records = ReceiptRepository(session).list((page - 1) * page_size, page_size)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [json.loads(record.receipt_json) for record in records],
    }
