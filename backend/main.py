"""Standalone offline FastAPI application for inference provenance."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api import (
    assessments,
    audit,
    evidence,
    health,
    inference,
    integration,
    models,
    module_auth,
    producers,
    summary,
)
from backend.core.config import Settings, settings as default_settings
from backend.core.signing import generate_key_pair, load_private_key, load_public_key
from backend.database.db import create_sqlite_engine, initialize, session_factory
from backend.services.provenance_service import ProvenanceService
from backend.services.model_registry_service import ModelRegistryService
from backend.services.audit_service import AuditService
from backend.services.evidence_service import EvidenceService
from backend.services.summary_service import SummaryService
from backend.services.integration_service import IntegrationService
from backend.services.module_auth_service import ModuleAuthService
from backend.services.producer_service import ProducerService
from backend.services.assessment_service import AssessmentService


def create_app(settings: Settings = default_settings) -> FastAPI:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.key_dir.mkdir(parents=True, exist_ok=True)
    private_path = settings.key_dir / "receipt_signing.private.pem"
    public_path = settings.key_dir / "receipt_signing.public.pem"
    if not private_path.exists() and not public_path.exists():
        generate_key_pair(settings.key_dir)
    elif not private_path.exists() or not public_path.exists():
        raise RuntimeError("Incomplete signing key pair; restore or rotate keys explicitly")

    engine = create_sqlite_engine(settings.database_path)
    initialize(engine)
    private_key = load_private_key(private_path)
    public_key = load_public_key(public_path)
    app = FastAPI(
        title="Offline Inference Provenance Backend",
        version="0.1.0",
        description="SHA-256 and Ed25519 signed receipts for computer-vision inference integrity.",
    )
    app.state.engine = engine
    app.state.settings = settings
    app.state.session_factory = session_factory(engine)
    app.state.provenance_service = ProvenanceService(
        private_key,
        public_key,
        settings.max_input_bytes,
        settings.max_receipt_age_seconds,
        settings.max_future_skew_seconds,
    )
    app.state.model_registry_service = ModelRegistryService()
    app.state.audit_service = AuditService()
    app.state.evidence_service = EvidenceService()
    app.state.summary_service = SummaryService()
    app.state.integration_service = IntegrationService()
    app.state.assessment_service = AssessmentService(app.state.summary_service)
    app.state.producer_service = ProducerService()
    app.state.module_auth_service = ModuleAuthService()

    @app.exception_handler(RequestValidationError)
    async def bounded_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        bounded = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Request validation failed"),
                "type": error.get("type", "validation_error"),
            }
            for error in errors[:20]
        ]
        return JSONResponse(
            status_code=422,
            content={
                "detail": bounded,
                "error_count": len(errors),
                "errors_returned": len(bounded),
            },
        )
    app.include_router(health.router)
    app.include_router(inference.router)
    app.include_router(models.router)
    app.include_router(audit.router)
    app.include_router(evidence.router)
    app.include_router(summary.router)
    app.include_router(integration.router)
    app.include_router(module_auth.router)
    app.include_router(producers.router)
    app.include_router(assessments.router)
    return app


app = create_app()
