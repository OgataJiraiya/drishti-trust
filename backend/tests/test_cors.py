from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_local_analyst_ui_can_read_backend(client):
    response = await client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


@pytest.mark.anyio
async def test_non_loopback_web_origin_is_not_authorized(client):
    response = await client.get("/health", headers={"Origin": "https://example.invalid"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
