from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health", summary="Check local backend health", description="Returns local offline service availability without contacting external systems.")
async def health(request: Request) -> dict:
    outbox = request.app.state.audit_outbox_service.status()
    chain = request.app.state.audit_service
    with request.app.state.session_factory() as session:
        audit_valid = chain.verify(session).valid
    healthy = bool(outbox["healthy"] and audit_valid)
    return {"status": "ok" if healthy else "degraded", "service": "inference-provenance",
            "offline": "true", "audit_outbox_pending": outbox["pending_count"],
            "audit_outbox_healthy": outbox["healthy"], "audit_chain_valid": audit_valid}
