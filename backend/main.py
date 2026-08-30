"""Standalone offline FastAPI application for inference provenance."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from backend.api import audit, evidence, health, inference, integration, models, summary
from backend.core.config import Settings, settings as default_settings
from backend.core.signing import generate_key_pair, load_private_key, load_public_key
from backend.database.db import create_sqlite_engine, initialize, session_factory
from backend.services.provenance_service import ProvenanceService
from backend.services.model_registry_service import ModelRegistryService
from backend.services.audit_service import AuditService
from backend.services.evidence_service import EvidenceService
from backend.services.summary_service import SummaryService
from backend.services.integration_service import IntegrationService


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
    app.include_router(health.router)
    app.include_router(inference.router)
    app.include_router(models.router)
    app.include_router(audit.router)
    app.include_router(evidence.router)
    app.include_router(summary.router)
    app.include_router(integration.router)
    return app


app = create_app()
