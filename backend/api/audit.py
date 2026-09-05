"""Read-only API for the append-only audit chain."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.database.repository import AuditRepository
from backend.schemas.audit import AuditChainVerification, AuditListResponse, AuditRecord
from backend.services.audit_service import AuditService

router = APIRouter(prefix="/api/audit", tags=["tamper-evident audit chain"])


async def get_audit_service(request: Request) -> AuditService:
    return request.app.state.audit_service


def append_event(request: Request, event_type: str, asset_type: str,
                 asset_id: str, payload: dict) -> AuditRecord:
    """Append using an isolated transaction after the security action completes."""
    with request.app.state.session_factory() as session:
        return request.app.state.audit_service.append(session, event_type, asset_type, asset_id, payload)


@router.get("", response_model=AuditListResponse, summary="List audit records in chain order", description="Returns paginated append-only records with optional event and asset filters; no audit mutation endpoints are exposed.")
async def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    event_type: str | None = None,
    asset_type: str | None = None,
    asset_id: str | None = None,
    session: Session = Depends(get_session),
    service: AuditService = Depends(get_audit_service),
) -> AuditListResponse:
    total, records = AuditRepository(session).list(
        (page - 1) * page_size, page_size, event_type, asset_type, asset_id
    )
    return AuditListResponse(
        total=total, page=page, page_size=page_size,
        items=[service.to_schema(record) for record in records],
    )


@router.get(
    "/verify",
    response_model=AuditChainVerification,
    summary="Verify the complete canonical audit hash chain",
    description=(
        "Returns the earliest detectable internal corruption. This operation never appends "
        "to the chain, including when verification discovers compromise."
    ),
)
async def verify_audit_chain(
    session: Session = Depends(get_session),
    service: AuditService = Depends(get_audit_service),
) -> AuditChainVerification:
    return service.verify(session)


@router.get("/{audit_id}", response_model=AuditRecord, summary="Retrieve one immutable audit event", description="Returns one stored hash-chain record without editing or rehashing it.")
async def get_audit_record(
    audit_id: str,
    session: Session = Depends(get_session),
    service: AuditService = Depends(get_audit_service),
) -> AuditRecord:
    record = AuditRepository(session).get(audit_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found")
    return service.to_schema(record)
