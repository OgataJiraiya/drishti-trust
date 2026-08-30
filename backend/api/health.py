from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Check local backend health", description="Returns local offline service availability without contacting external systems.")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "inference-provenance", "offline": "true"}
