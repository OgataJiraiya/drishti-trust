"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request


async def get_session(request: Request):  # type: ignore[no-untyped-def]
    with request.app.state.session_factory() as session:
        yield session
