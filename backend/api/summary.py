"""Read-only dashboard assurance summary API."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.schemas.summary import AssuranceSummary
from backend.services.summary_service import SummaryService

router = APIRouter(tags=["assurance summary"])


async def get_summary_service(request: Request) -> SummaryService:
    return request.app.state.summary_service


@router.get(
    "/api/summary",
    response_model=AssuranceSummary,
    summary="Derive a transparent assurance summary",
    description=(
        "Defaults to Ed25519-authenticated module evidence. trust_scope=all is a diagnostic, "
        "untrusted-inclusive compatibility view. Read-only deterministic aggregation over Finding Schema v1 "
        "and the existing audit-chain verifier. "
        "The transparent heuristic is not a probability of safety or compromise. Asset-level recommendations "
        "remain distinct from module and overall system dispositions. No AI, persistent summary state, or hidden weighting is used."
    ),
)
async def get_summary(
    request: Request,
    trust_scope: Literal["authenticated", "all"] = Query(default="authenticated"),
    session: Session = Depends(get_session),
    service: SummaryService = Depends(get_summary_service),
) -> AssuranceSummary:
    return service.build(session, request.app.state.audit_service, trust_scope)
