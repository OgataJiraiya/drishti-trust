import httpx
import pytest
from backend.core.config import Settings
from backend.main import create_app


@pytest.mark.anyio
async def test_chunked_json_is_bounded_before_parsing(tmp_path):
    app = create_app(Settings(data_dir=tmp_path/'data', key_dir=tmp_path/'keys', max_json_request_bytes=64))
    async def chunks():
        yield b' ' * 40
        yield b' ' * 40
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/inference/verify', content=chunks())
        assert response.status_code == 413
        assert response.json() == {'detail': 'Request body exceeds byte limit'}
        assert (await client.get('/health')).status_code == 200


@pytest.mark.anyio
async def test_bounded_json_still_reaches_schema_validation(client):
    response = await client.post('/api/inference/verify', json={})
    assert response.status_code == 422
