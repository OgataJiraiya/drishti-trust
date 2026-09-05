"""Trusted atomic integration boundary for all four analysis modules."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.auth import require_trusted_internal_ingest
from backend.database.repository import ModuleRunRepository
from backend.schemas.common import FindingModule
from backend.schemas.integration import (
    ModuleRunDetails,
    ModuleRunIngestionResponse,
    ModuleRunListResponse,
    ModuleRunSubmission,
)
from backend.services.integration_service import (
    AssessmentNotActive,
    AssessmentNotFound,
    IntegrationConflict,
    IntegrationIngestResult,
    IntegrationService,
    RunAuthentication,
)
from backend.services.module_auth_service import AuthenticatedModule

router = APIRouter(prefix="/api/integration/runs", tags=["cross-module integration"])


async def get_integration_service(request: Request) -> IntegrationService:
    return request.app.state.integration_service


def audit_ingestion_result(
    request: Request,
    body: ModuleRunSubmission,
    result: IntegrationIngestResult,
    authentication: AuthenticatedModule | None = None,
) -> None:
    """Best-effort post-commit delivery; durable intents were staged by the service."""
    if result.response.result != "CREATED":
        return
    try:
        request.app.state.audit_outbox_service.drain_pending()
    except Exception:
        # State and durable intent are already committed; recovery/status exposes failure.
        pass


@router.post(
    "",
    response_model=ModuleRunIngestionResponse,
    summary="Atomically ingest one analysis module run",
    description=(
        "Disabled by default trusted-internal compatibility path; it requires explicit enablement "
        "and the configured internal bearer credential. It is not the preferred module provenance "
        "boundary and is not Ed25519 module authentication. Validates a strict outer "
        "run envelope containing the unchanged frozen Finding Schema v1. "
        "The batch commits all findings and immutable run metadata exactly once or rolls back entirely. "
        "Exact reruns are idempotent; changed run or finding identities return HTTP 409. External "
        "module producers should use /api/integration/signed-runs."
    ),
)
async def ingest_module_run(
    body: ModuleRunSubmission,
    request: Request,
    _authorization: None = Depends(require_trusted_internal_ingest),
    session: Session = Depends(get_session),
    service: IntegrationService = Depends(get_integration_service),
) -> ModuleRunIngestionResponse:
    try:
        result = service.ingest_run(
            body,
            session,
            RunAuthentication(mode="TRUSTED_INTERNAL", producer_id=body.producer),
        )
    except IntegrationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssessmentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssessmentNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_ingestion_result(request, body, result)
    return result.response


@router.get(
    "",
    response_model=ModuleRunListResponse,
    summary="List immutable module runs",
    description="Returns module-run metadata in deterministic ingestion order with pagination and optional module filtering.",
)
async def list_module_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    module: FindingModule | None = None,
    session: Session = Depends(get_session),
    service: IntegrationService = Depends(get_integration_service),
) -> ModuleRunListResponse:
    total, records = ModuleRunRepository(session).list(
        (page - 1) * page_size, page_size, module.value if module else None
    )
    return ModuleRunListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[service.to_details(record, session) for record in records],
    )


@router.get(
    "/{run_id}",
    response_model=ModuleRunDetails,
    summary="Retrieve immutable module-run metadata",
    description=(
        "Returns integration metadata stored outside Finding Schema v1, including canonical request identity and ingestion counts."
    ),
)
async def get_module_run(
    run_id: str,
    session: Session = Depends(get_session),
    service: IntegrationService = Depends(get_integration_service),
) -> ModuleRunDetails:
    record = ModuleRunRepository(session).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Module run not found")
    return service.to_details(record, session)
