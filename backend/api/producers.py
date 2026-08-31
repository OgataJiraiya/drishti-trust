"""Administrative public-key registry for authorized analysis producers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.audit import append_event
from backend.api.dependencies import get_session
from backend.api.auth import require_admin
from backend.database.repository import ProducerKeyRepository, ProducerRepository
from backend.schemas.producers import (
    ProducerDetails,
    ProducerKeyListResponse,
    ProducerKeyMutationResponse,
    ProducerKeyRegistration,
    ProducerListResponse,
    ProducerMutationResponse,
    ProducerRegistration,
)
from backend.services.producer_service import (
    InvalidProducerKey,
    ProducerConflict,
    ProducerNotFound,
    ProducerService,
)

router = APIRouter(prefix="/api/producers", tags=["authenticated module producers"])


async def get_service(request: Request) -> ProducerService:
    return request.app.state.producer_service


def _not_found(exc: ProducerNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post("", response_model=ProducerMutationResponse, summary="Register an approved module producer", description="Creates an immutable producer-to-module binding. Exact registration is idempotent; changed identity content returns HTTP 409. Administrative RBAC is an acknowledged deployment responsibility.")
async def register_producer(
    body: ProducerRegistration,
    request: Request,
    _authorization: None = Depends(require_admin),
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerMutationResponse:
    try:
        result, producer = service.register_producer(body, session)
    except ProducerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result == "CREATED":
        append_event(request, "PRODUCER_REGISTERED", "module_producer", producer.producer_id, {
            "producer_id": producer.producer_id,
            "module": producer.module,
            "status": producer.status,
        })
    return ProducerMutationResponse(result=result, producer=producer)


@router.get("", response_model=ProducerListResponse, summary="List registered module producers", description="Returns public producer authorization metadata with deterministic pagination.")
async def list_producers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerListResponse:
    total, records = ProducerRepository(session).list((page - 1) * page_size, page_size)
    return ProducerListResponse(
        total=total, page=page, page_size=page_size,
        items=[service.to_producer(record) for record in records],
    )


@router.get("/{producer_id}", response_model=ProducerDetails, summary="Retrieve one module producer", description="Returns public producer metadata and durable approval or revocation status.")
async def get_producer(
    producer_id: str,
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerDetails:
    record = ProducerRepository(session).get(producer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Producer not found")
    return service.to_producer(record)


@router.post("/{producer_id}/keys", response_model=ProducerKeyMutationResponse, summary="Register an Ed25519 producer public key", description="Accepts only an Ed25519 public key. The backend canonicalizes it and fingerprints raw key bytes; private key PEM and other algorithms are rejected.")
async def register_producer_key(
    producer_id: str,
    body: ProducerKeyRegistration,
    request: Request,
    _authorization: None = Depends(require_admin),
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerKeyMutationResponse:
    try:
        result, key = service.register_key(producer_id, body, session)
    except ProducerNotFound as exc:
        raise _not_found(exc) from exc
    except InvalidProducerKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProducerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result == "CREATED":
        append_event(request, "PRODUCER_KEY_REGISTERED", "producer_key", key.key_id, {
            "key_id": key.key_id,
            "producer_id": key.producer_id,
            "key_fingerprint": key.public_key_fingerprint,
            "status": key.status,
        })
    return ProducerKeyMutationResponse(result=result, key=key)


@router.get("/{producer_id}/keys", response_model=ProducerKeyListResponse, summary="List producer public keys", description="Returns public verification keys and durable active/revoked status; no private material is stored or returned.")
async def list_producer_keys(
    producer_id: str,
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerKeyListResponse:
    if ProducerRepository(session).get(producer_id) is None:
        raise HTTPException(status_code=404, detail="Producer not found")
    records = ProducerKeyRepository(session).list_for_producer(producer_id)
    return ProducerKeyListResponse(
        producer_id=producer_id, items=[service.to_key(record) for record in records]
    )


@router.post("/{producer_id}/revoke", response_model=ProducerMutationResponse, summary="Revoke a module producer", description="Durably revokes the producer. Historical runs remain readable; all future signed submissions fail closed.")
async def revoke_producer(
    producer_id: str,
    request: Request,
    _authorization: None = Depends(require_admin),
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerMutationResponse:
    try:
        changed, producer = service.revoke_producer(producer_id, session)
    except ProducerNotFound as exc:
        raise _not_found(exc) from exc
    if changed:
        append_event(request, "PRODUCER_REVOKED", "module_producer", producer_id, {
            "producer_id": producer_id, "module": producer.module, "status": producer.status,
        })
    return ProducerMutationResponse(result="REVOKED", producer=producer)


@router.post("/{producer_id}/keys/{key_id}/revoke", response_model=ProducerKeyMutationResponse, summary="Revoke one producer key", description="Durably disables this key without affecting other active keys registered to the producer.")
async def revoke_producer_key(
    producer_id: str,
    key_id: str,
    request: Request,
    _authorization: None = Depends(require_admin),
    session: Session = Depends(get_session),
    service: ProducerService = Depends(get_service),
) -> ProducerKeyMutationResponse:
    try:
        changed, key = service.revoke_key(producer_id, key_id, session)
    except ProducerNotFound as exc:
        raise _not_found(exc) from exc
    if changed:
        append_event(request, "PRODUCER_KEY_REVOKED", "producer_key", key_id, {
            "key_id": key_id,
            "producer_id": producer_id,
            "key_fingerprint": key.public_key_fingerprint,
            "status": key.status,
        })
    return ProducerKeyMutationResponse(result="REVOKED", key=key)
