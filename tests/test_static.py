"""前后端分离边界回归：FastAPI 只提供 API，不再托管旧页面或静态资源。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_page_routes_are_owned_by_frontend():
    for path in (
        "/", "/admin", "/kb", "/rag-eval", "/review", "/observability",
        "/topics", "/topics/questions", "/acceptance", "/acceptance/eval",
        "/acceptance/data", "/acceptance/errors", "/static/admin.js",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"].startswith("application/json"), path


def test_api_documentation_and_health_remain_available():
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok", "service": "SHAF"}
