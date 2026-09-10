"""Assessment lifecycle, membership, and sealed snapshot API."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.auth import require_admin
from backend.api.dependencies import get_session
from backend.database.repository import (
    AssessmentMembershipRepository, AssessmentRepository, AssessmentSnapshotRepository,
    EvidenceRepository,
)
from backend.schemas.assessments import (
    AssessmentCreate, AssessmentDetails, AssessmentListResponse, AssessmentRunListResponse,
    AssessmentSnapshot, SnapshotVerification,
)
from backend.schemas.evidence import EvidenceListResponse
from backend.services.evidence_service import EvidenceService
from backend.services.assessment_service import AssessmentConflict, AssessmentMissing, AssessmentService

router = APIRouter(prefix="/api/assessments", tags=["assessment lifecycle"])


def _errors(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, AssessmentMissing) else 409, detail=str(exc))


@router.post("", response_model=AssessmentDetails, status_code=201,
             dependencies=[Depends(require_admin)], summary="Create a DRAFT assessment")
async def create_assessment(body: AssessmentCreate, request: Request,
                            session: Session = Depends(get_session)) -> AssessmentDetails:
    try:
        result = request.app.state.assessment_service.create(body, session)
    except (AssessmentConflict, AssessmentMissing) as exc:
        raise _errors(exc) from exc
    try: request.app.state.audit_outbox_service.drain_pending()
    except Exception: pass
    return result


@router.get("", response_model=AssessmentListResponse, summary="List assessments")
async def list_assessments(status: Literal["DRAFT", "ACTIVE", "SEALED"] | None = None,
                           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                           session: Session = Depends(get_session)) -> AssessmentListResponse:
    total, records = AssessmentRepository(session).list(offset, limit, status)
    return AssessmentListResponse(total=total, limit=limit, offset=offset,
                                  items=[AssessmentService.to_details(record) for record in records])


@router.get("/current", response_model=AssessmentDetails, summary="Get the current ACTIVE assessment")
async def current_assessment(session: Session = Depends(get_session)) -> AssessmentDetails:
    record = AssessmentRepository(session).active()
    if record is None:
        raise HTTPException(status_code=404, detail="No ACTIVE assessment")
    return AssessmentService.to_details(record)


@router.get("/{assessment_id}", response_model=AssessmentDetails, summary="Get an assessment")
async def get_assessment(assessment_id: str, session: Session = Depends(get_session)) -> AssessmentDetails:
    record = AssessmentRepository(session).get(assessment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return AssessmentService.to_details(record)


@router.post("/{assessment_id}/activate", response_model=AssessmentDetails,
             dependencies=[Depends(require_admin)], summary="Activate a DRAFT assessment")
async def activate_assessment(assessment_id: str, request: Request,
                              session: Session = Depends(get_session)) -> AssessmentDetails:
    try:
        result, changed = request.app.state.assessment_service.activate(assessment_id, session)
    except (AssessmentConflict, AssessmentMissing) as exc:
        raise _errors(exc) from exc
    if changed:
        try: request.app.state.audit_outbox_service.drain_pending()
        except Exception: pass
    return result


@router.post("/{assessment_id}/seal", response_model=AssessmentSnapshot,
             dependencies=[Depends(require_admin)], summary="Seal and snapshot an ACTIVE assessment")
async def seal_assessment(assessment_id: str, request: Request,
                          session: Session = Depends(get_session)) -> AssessmentSnapshot:
    try:
        result = request.app.state.assessment_service.seal(
            assessment_id, session, request.app.state.audit_service
        )
    except (AssessmentConflict, AssessmentMissing) as exc:
        raise _errors(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result.created:
        try: request.app.state.audit_outbox_service.drain_pending()
        except Exception: pass
    return result.snapshot


@router.get("/{assessment_id}/runs", response_model=AssessmentRunListResponse,
            summary="List only runs attached to an assessment")
async def assessment_runs(assessment_id: str, request: Request,
                          limit: int = Query(50, ge=1, le=100),
                          offset: int = Query(0, ge=0),
                          session: Session = Depends(get_session)) -> AssessmentRunListResponse:
    if AssessmentRepository(session).get(assessment_id) is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    total, records = AssessmentMembershipRepository(session).runs(assessment_id, offset, limit)
    return AssessmentRunListResponse(
        total=total, limit=limit, offset=offset,
        items=[request.app.state.integration_service.to_details(record, session) for record in records],
    )


@router.get("/{assessment_id}/findings", response_model=EvidenceListResponse,
            summary="List authenticated findings attached to an assessment")
async def assessment_findings(
    assessment_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    session: Session = Depends(get_session),
) -> EvidenceListResponse:
    if AssessmentRepository(session).get(assessment_id) is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    finding_ids = AssessmentMembershipRepository(session).finding_ids(assessment_id, "ED25519")
    total, records = EvidenceRepository(session).list_ids(
        finding_ids, (page - 1) * page_size, page_size
    )
    return EvidenceListResponse(
        total=total, page=page, page_size=page_size,
        items=[EvidenceService.to_schema(record) for record in records],
    )


@router.get("/{assessment_id}/snapshot", response_model=AssessmentSnapshot,
            summary="Retrieve the persisted immutable sealed snapshot")
async def get_snapshot(assessment_id: str, session: Session = Depends(get_session)) -> AssessmentSnapshot:
    record = AssessmentSnapshotRepository(session).get(assessment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assessment snapshot not found")
    return AssessmentService.to_snapshot(record)


@router.get("/{assessment_id}/snapshot/verify", response_model=SnapshotVerification,
            summary="Verify snapshot and current run-set commitments")
async def verify_snapshot(assessment_id: str, request: Request,
                          session: Session = Depends(get_session)) -> SnapshotVerification:
    try:
        return request.app.state.assessment_service.verify(assessment_id, session)
    except AssessmentMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
