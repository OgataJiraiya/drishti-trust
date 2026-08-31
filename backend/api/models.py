"""Approved model registry and opaque artifact identity endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.audit import append_event
from backend.api.dependencies import get_session
from backend.core.hashing import ArtifactTooLarge, sha256_async_chunks
from backend.database.repository import ModelRegistryRepository
from backend.schemas.model import (
    ModelRegistrationResponse,
    ModelListResponse,
    ModelRegistryEntry,
    ModelVerificationResponse,
    RegisterModelRequest,
    VerifyModelRequest,
)
from backend.services.model_registry_service import ModelRegistrationConflict, ModelRegistryService

router = APIRouter(prefix="/api/models", tags=["approved model registry"])


async def service(request: Request) -> ModelRegistryService:
    return request.app.state.model_registry_service


@router.post(
    "/register",
    response_model=ModelRegistrationResponse,
    summary="Register a provided approved SHA-256 digest",
    description=(
        "Registers the caller-provided digest as the trust anchor. This mode does not claim "
        "that model bytes were inspected. Existing model IDs cannot be rebound to another digest."
    ),
)
async def register_digest(
    body: RegisterModelRequest,
    request: Request,
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelRegistrationResponse:
    try:
        result = registry.register_digest(body, session)
        if not result.idempotent:
            try: request.app.state.audit_outbox_service.drain_pending()
            except Exception: pass
        return result
    except ModelRegistrationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/register/artifact",
    response_model=ModelRegistrationResponse,
    summary="Register an opaque streamed model artifact",
    description=(
        "Streams the raw request body, calculates SHA-256 locally, and discards the bytes. "
        "The artifact is never parsed, deserialized, loaded, or executed."
    ),
    openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def register_artifact(
    request: Request,
    model_id: str = Query(min_length=1, max_length=128),
    display_name: str = Query(min_length=1, max_length=256),
    original_filename: str | None = Query(default=None, max_length=512),
    declared_format: str | None = Query(default=None, max_length=64),
    version: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelRegistrationResponse:
    try:
        digest, size = await sha256_async_chunks(request.stream(), request.app.state.settings.max_model_bytes)
        result = registry.register_artifact(
            session=session,
            model_id=model_id,
            display_name=display_name,
            original_filename=original_filename,
            observed_sha256=digest,
            artifact_size_bytes=size,
            declared_format=declared_format,
            version=version,
        )
        if not result.idempotent:
            try: request.app.state.audit_outbox_service.drain_pending()
            except Exception: pass
        return result
    except ArtifactTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ModelRegistrationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=ModelListResponse, summary="List approved model identities", description="Returns paginated registry entries without reading or executing model artifacts.")
async def list_models(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> dict:
    total, records = ModelRegistryRepository(session).list((page - 1) * page_size, page_size)
    return {"total": total, "page": page, "page_size": page_size, "items": [registry.to_schema(item) for item in records]}


@router.get("/{model_id}", response_model=ModelRegistryEntry, summary="Retrieve an approved model identity", description="Returns the immutable SHA-256 trust anchor and current authorization status for one model ID.")
async def get_model(
    model_id: str,
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelRegistryEntry:
    record = ModelRegistryRepository(session).get(model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Registered model not found")
    return registry.to_schema(record)


@router.post(
    "/verify",
    response_model=ModelVerificationResponse,
    summary="Verify a supplied model digest against the approved registry",
    description=(
        "Compares byte-level SHA-256 identity with the approved registry. A match proves exact "
        "artifact identity only; it does not prove behavioural safety or absence of backdoors."
    ),
)
async def verify_digest(
    body: VerifyModelRequest,
    request: Request,
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelVerificationResponse:
    result = registry.verify(body.model_id, body.observed_sha256, session)
    event_type = "MODEL_SUBSTITUTION_DETECTED" if any(
        finding.attack_class == "MODEL_SUBSTITUTION" for finding in result.findings
    ) else "MODEL_VERIFIED"
    append_event(request, event_type, "model", body.model_id, {
        "model_id": body.model_id, "expected_sha256": result.expected_sha256,
        "observed_sha256": result.observed_sha256, "status": result.status,
        "registry_status": result.checks.get("registry_status"),
    })
    return result


@router.post(
    "/verify/artifact",
    response_model=ModelVerificationResponse,
    summary="Stream and verify opaque model bytes",
    description="Hashes the bounded raw request body without model deserialization or execution.",
    openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def verify_artifact(
    request: Request,
    model_id: str = Query(min_length=1, max_length=128),
    filename: str | None = Query(default=None, max_length=512),
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelVerificationResponse:
    try:
        digest, _size = await sha256_async_chunks(request.stream(), request.app.state.settings.max_model_bytes)
    except ArtifactTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    result = registry.verify(model_id, digest, session)
    event_type = "MODEL_SUBSTITUTION_DETECTED" if any(
        finding.attack_class == "MODEL_SUBSTITUTION" for finding in result.findings
    ) else "MODEL_VERIFIED"
    append_event(request, event_type, "model", model_id, {
        "model_id": model_id, "expected_sha256": result.expected_sha256,
        "observed_sha256": result.observed_sha256, "status": result.status,
        "registry_status": result.checks.get("registry_status"),
    })
    return result


@router.post("/{model_id}/revoke", response_model=ModelRegistryEntry, summary="Revoke an approved model", description="Marks the registry entry unauthorized without replacing or deleting its historical SHA-256 trust anchor.")
async def revoke_model(
    model_id: str,
    request: Request,
    session: Session = Depends(get_session),
    registry: ModelRegistryService = Depends(service),
) -> ModelRegistryEntry:
    entry = registry.revoke(model_id, session)
    if entry is None:
        raise HTTPException(status_code=404, detail="Registered model not found")
    try: request.app.state.audit_outbox_service.drain_pending()
    except Exception: pass
    return entry
