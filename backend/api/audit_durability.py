"""Operational outbox and signed audit checkpoint APIs."""
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.auth import require_admin
from backend.schemas.audit import (
    AuditCheckpointBundle, CheckpointCreationResponse, CheckpointListResponse,
    CheckpointVerification,
)
from backend.services.audit_checkpoint_service import CheckpointConflict

router = APIRouter(prefix="/api/audit", tags=["audit durability"])


@router.get("/outbox/status")
async def outbox_status(request: Request) -> dict:
    return request.app.state.audit_outbox_service.status()


@router.post("/outbox/drain", dependencies=[Depends(require_admin)])
async def drain_outbox(request: Request) -> dict:
    try:
        count = request.app.state.audit_outbox_service.drain_pending()
    except Exception as exc:
        raise HTTPException(409, detail="Audit outbox could not be drained safely") from exc
    return {"delivered": count, **request.app.state.audit_outbox_service.status()}


@router.post("/checkpoints", response_model=CheckpointCreationResponse,
             dependencies=[Depends(require_admin)])
async def create_checkpoint(request: Request) -> CheckpointCreationResponse:
    try:
        return request.app.state.audit_checkpoint_service.create()
    except CheckpointConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(request: Request) -> CheckpointListResponse:
    return CheckpointListResponse(items=request.app.state.audit_checkpoint_service.list())


@router.get("/checkpoints/latest", response_model=AuditCheckpointBundle)
async def latest_checkpoint(request: Request) -> AuditCheckpointBundle:
    bundle = request.app.state.audit_checkpoint_service.latest()
    if bundle is None: raise HTTPException(404, detail="Audit checkpoint not found")
    return bundle


@router.get("/checkpoints/public-key")
async def checkpoint_public_key(request: Request) -> dict:
    return request.app.state.audit_checkpoint_service.public_key_info()


@router.post("/checkpoints/verify", response_model=CheckpointVerification)
async def verify_external(bundle: AuditCheckpointBundle, request: Request) -> CheckpointVerification:
    return request.app.state.audit_checkpoint_service.verify(bundle)


@router.get("/checkpoints/{checkpoint_id}", response_model=AuditCheckpointBundle)
async def get_checkpoint(checkpoint_id: str, request: Request) -> AuditCheckpointBundle:
    bundle = request.app.state.audit_checkpoint_service.get(checkpoint_id)
    if bundle is None: raise HTTPException(404, detail="Audit checkpoint not found")
    return bundle


@router.get("/checkpoints/{checkpoint_id}/verify", response_model=CheckpointVerification)
async def verify_stored(checkpoint_id: str, request: Request) -> CheckpointVerification:
    try:
        return request.app.state.audit_checkpoint_service.verify_stored(checkpoint_id)
    except KeyError as exc:
        raise HTTPException(404, detail="Audit checkpoint not found") from exc
