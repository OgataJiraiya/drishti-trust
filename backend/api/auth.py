"""Fail-closed bearer authentication for administrative and compatibility APIs."""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer_scheme = HTTPBearer(auto_error=False)
_BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}


def _require_configured_bearer(
    credentials: HTTPAuthorizationCredentials | None,
    configured_token: str | None,
    unconfigured_detail: str,
) -> None:
    if not configured_token:
        raise HTTPException(status_code=503, detail=unconfigured_detail)
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, configured_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication failed",
            headers=_BEARER_HEADERS,
        )


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    _require_configured_bearer(
        credentials,
        request.app.state.settings.admin_bearer_token,
        "Administrative authentication is not configured",
    )


async def require_trusted_internal_ingest(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    settings = request.app.state.settings
    if not settings.allow_unsigned_ingestion:
        raise HTTPException(status_code=403, detail="Unsigned integration is disabled")
    _require_configured_bearer(
        credentials,
        settings.internal_ingest_bearer_token,
        "Trusted-internal ingestion authentication is not configured",
    )
