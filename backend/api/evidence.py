"""Cross-team evidence ingestion and retrieval API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.auth import require_trusted_internal_ingest
from backend.database.repository import EvidenceRepository
from backend.schemas.common import FindingModule, Recommendation, Severity
from backend.schemas.evidence import EvidenceIngestionResponse, EvidenceListResponse, Finding
from backend.services.evidence_service import EvidenceConflict, EvidenceService

router = APIRouter(prefix="/api/evidence", tags=["common team evidence"])


async def get_service(request: Request) -> EvidenceService:
    return request.app.state.evidence_service


@router.post(
    "",
    response_model=EvidenceIngestionResponse,
    summary="Ingest one immutable Finding Schema v1 document",
    description=(
        "Disabled by default trusted-internal compatibility path requiring explicit enablement "
        "and the internal bearer credential; normal modules must use signed-runs. The eleven-field "
        "Finding body is the frozen team contract. Identical resubmission is "
        "idempotent; changing an existing finding ID returns HTTP 409."
    ),
)
async def ingest_finding(
    body: Finding,
    request: Request,
    _authorization: None = Depends(require_trusted_internal_ingest),
    session: Session = Depends(get_session),
    service: EvidenceService = Depends(get_service),
) -> EvidenceIngestionResponse:
    try:
        result = service.ingest(body, session)
    except EvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.result == "CREATED":
        try: request.app.state.audit_outbox_service.drain_pending()
        except Exception: pass
    return result


@router.get("", response_model=EvidenceListResponse, summary="List and filter team findings", description="Returns paginated frozen Finding Schema v1 documents with optional module, asset, category, severity, and recommendation filters.")
async def list_findings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    module: FindingModule | None = None,
    asset_type: str | None = Query(default=None, min_length=1, max_length=64),
    asset_id: str | None = Query(default=None, min_length=1, max_length=256),
    category: str | None = Query(default=None, min_length=1, max_length=128),
    severity: Severity | None = None,
    recommendation: Recommendation | None = None,
    session: Session = Depends(get_session),
    service: EvidenceService = Depends(get_service),
) -> EvidenceListResponse:
    total, records = EvidenceRepository(session).list(
        (page - 1) * page_size, page_size,
        module.value if module else None, asset_type, asset_id, category,
        severity.value if severity else None,
        recommendation.value if recommendation else None,
    )
    return EvidenceListResponse(
        total=total, page=page, page_size=page_size,
        items=[service.to_schema(record) for record in records],
    )


@router.get("/{finding_id}", response_model=Finding, summary="Retrieve one exact Finding JSON", description="Returns exactly the eleven public Finding Schema v1 fields with no internal ingestion metadata.")
async def get_finding(
    finding_id: str,
    session: Session = Depends(get_session),
    service: EvidenceService = Depends(get_service),
) -> Finding:
    record = EvidenceRepository(session).get(finding_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return service.to_schema(record)
