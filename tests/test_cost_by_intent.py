"""云端用量归因回归：真实 SDK 响应结构、完整分页、跨页标签和 Token 去重。"""
from types import SimpleNamespace
import json
import sys

import pytest
from langfuse.api.observations.types.observations_v2response import ObservationsV2Response
from langfuse.api.core.pydantic_utilities import parse_obj_as

from scripts.cost_by_intent import _aggregate, _query
from scripts import cost_by_intent


def observation(id, trace="t1", type="GENERATION", tags=None, usage=None):
    return {"id": id, "traceId": trace, "type": type, "tags": tags or [],
            "startTime": "2026-10-01T00:00:00Z", "projectId": "test-project",
            "usageDetails": usage or {}}


def test_pagination_joins_late_intent_and_deduplicates_sdk_rows():
    """意图只在后一页的事件中出现；总量不重复加缓存/输入细项或父 span。"""
    generation = observation("g1", usage={"total": 30, "input": 20, "output": 10,
                                          "input_cache_read": 15})
    pages = [
        {"data": [generation, observation("root", type="SPAN", usage={"total": 99})],
         "meta": {"cursor": "next"}},
        {"data": [generation, observation("g2", usage={"input": 3, "output": 2}),
                  observation("tag", type="EVENT", tags=["intent:物流"]),
                  observation("g3", trace="t2", tags=["intent:物流"], usage={"total": 5}),
                  observation("untagged", trace="t3", usage={"total": 100})],
         "meta": {}},
    ]
    calls = []

    def get_many(**kwargs):
        calls.append(kwargs)
        # 与 SDK HTTP 响应使用同一反序列化入口，覆盖自定义字段别名。
        return parse_obj_as(ObservationsV2Response, pages[len(calls) - 1])

    client = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=get_many)))
    rows = _aggregate(_query(client, "2026-09-30T00:00:00Z", "2026-10-02T00:00:00Z"))
    assert rows == [{"intent": "物流", "tokens": 40, "count": 2}]
    assert [c["cursor"] for c in calls] == [None, "next"]
    assert calls[0]["fields"] == "core,basic,usage,trace_context"
    assert calls[0]["from_start_time"].isoformat() == "2026-09-30T00:00:00+00:00"


def test_repeating_cursor_fails_instead_of_publishing_partial_report():
    get_many = lambda **kwargs: {"data": [], "meta": {"cursor": "stuck"}}
    client = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=get_many)))
    with pytest.raises(RuntimeError, match="游标重复"):
        list(_query(client, "2026-09-30T00:00:00Z", "2026-10-02T00:00:00Z"))


def test_conflicting_intents_fail_without_arbitrary_attribution():
    with pytest.raises(ValueError, match="多个意图"):
        _aggregate([observation("g1", tags=["intent:物流", "intent:退款"])])


def test_empty_zero_usage_and_unlinked_events():
    assert _aggregate([]) == []
    assert _aggregate([observation("tag", type="EVENT", tags=["intent:闲聊"]),
                       observation("unlinked", trace=None, tags=["intent:退款"])]) == [
        {"intent": "闲聊", "tokens": 0, "count": 1}]


def test_cloud_report_preserves_page_contract(tmp_path, monkeypatch):
    """完整执行报告入口并读取页面 API；不调用真实云端、数据库或模型。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import observability

    async def no_note(*args):
        return None

    async def no_runs(*args, **kwargs):
        return []

    monkeypatch.setattr(cost_by_intent, "get_langfuse", lambda: object())
    monkeypatch.setattr(cost_by_intent, "_query", lambda *args: [
        observation("g1", usage={"total": 30}),
        observation("tag", type="EVENT", tags=["intent:物流"]),
        observation("g2", trace="t2", tags=["intent:物流"], usage={"total": 10}),
    ])
    monkeypatch.setattr(cost_by_intent.read_notes, "generate", no_note)
    monkeypatch.setattr(cost_by_intent, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(cost_by_intent, "_ROOT", tmp_path)
    monkeypatch.setattr(cost_by_intent, "_OUT", tmp_path / "cost.txt")
    monkeypatch.setattr(cost_by_intent, "_OUT_JSON", tmp_path / "cost.json")
    monkeypatch.setattr(sys, "argv", ["cost_by_intent", "--days", "2"])
    assert cost_by_intent.main() == 0
    report = json.loads((tmp_path / "cost.json").read_text(encoding="utf-8"))
    assert report["rows"] == [{"intent": "物流", "tokens": 40, "count": 2,
                               "avg_tokens": 20, "share": 1.0}]
    assert report["total_requests"] == 2 and report["total_tokens"] == 40
    monkeypatch.setattr(observability, "COST", tmp_path / "cost.json")
    monkeypatch.setattr(observability, "CALIB", tmp_path / "absent.json")
    monkeypatch.setattr(observability, "TREND_NOTE", tmp_path / "absent-note.json")
    monkeypatch.setattr("app.db.repository.list_eval_runs", no_runs)
    app = FastAPI()
    app.include_router(observability.router)
    with TestClient(app) as client:
        response = client.get("/api/observability/overview")
    assert response.status_code == 200
    assert response.json()["cost"]["rows"] == report["rows"]
