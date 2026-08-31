"""Authenticated producer boundary for atomic module-run ingestion."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.integration import audit_ingestion_result
from backend.schemas.integration import ModuleRunIngestionResponse
from backend.schemas.module_auth import SignedModuleRunSubmission
from backend.services.integration_service import IntegrationConflict, RunAuthentication
from backend.services.module_auth_service import (
    InvalidModuleSignature,
    ModuleAuthorizationDenied,
)

router = APIRouter(prefix="/api/integration", tags=["authenticated module integration"])


@router.post(
    "/signed-runs",
    response_model=ModuleRunIngestionResponse,
    summary="Authenticate and atomically ingest a signed module run",
    description=(
        "Preferred authenticated module provenance path. Verifies the producer approval, "
        "module binding, active Ed25519 public key, and "
        "signature over canonical JSON bytes of the exact inner ModuleRunSubmission. "
        "Authentication completes before the existing all-or-nothing integration transaction."
    ),
)
async def ingest_signed_module_run(
    body: SignedModuleRunSubmission,
    request: Request,
    session: Session = Depends(get_session),
) -> ModuleRunIngestionResponse:
    try:
        authentication = request.app.state.module_auth_service.authenticate(body, session)
    except InvalidModuleSignature as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ModuleAuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # End the read-only registry transaction before IntegrationService reserves
    # SQLite write state with BEGIN IMMEDIATE.
    session.rollback()
    try:
        result = request.app.state.integration_service.ingest_run(
            body.run,
            session,
            RunAuthentication(
                mode="ED25519",
                producer_id=authentication.producer_id,
                key_id=authentication.key_id,
                key_fingerprint=authentication.key_fingerprint,
            ),
        )
    except IntegrationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_ingestion_result(request, body.run, result, authentication)
    return result.response
