"""独立 API 边界：进程健康检查与显式来源 CORS 不依赖外部业务服务。"""
import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_health_is_process_liveness_only():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "SHAF"}


@pytest.mark.asyncio
async def test_unconfigured_origin_is_not_granted_access():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/api/chat", headers={
            "Origin": "https://unconfigured.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
