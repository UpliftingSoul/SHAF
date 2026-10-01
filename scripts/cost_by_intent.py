"""
ch09 Cost Control：从 Langfuse 按意图汇总近 N 天的 token 消耗，定位最耗资源的业务意图。

意图分类阶段会把结果写成 intent:xxx tag 挂到 trace，
本脚本不自行埋点，而是分页查询 Langfuse Observations v2，关联同一 trace 的意图标签与模型用量，
再按 intent 聚合出请求数、总 token、单均 token 与占比；
未携带 intent tag 的历史 trace 不纳入统计。

运行：make cost-report
可通过 DAYS=N 指定统计窗口，默认近 7 天；要求 Langfuse 已运行且 .env 配好连接所需三变量。

产物：
- data/ch09/reports/cost_by_intent.txt：便于终端 / 人工查看的文本报告；
- data/ch09/reports/cost_by_intent.json：「观测与成本」页面的唯一数据源，
  页面直接读取其中已经算好的统计值，不再自行重复计算，避免报告与页面口径漂移。

兼容口径：面向 Langfuse Cloud / v4，不依赖即将停用的 Metrics v1。
只统计 GENERATION 自身用量，避免父级 span 汇总被再次累加；按 traceId 去重计请求。

成本边界：自定义模型（如 glm-5.2）没有 Langfuse 内置单价，本脚本只统计 token，
不维护或推算金额价格表；如需查看实际费用，应在 Langfuse 中配置对应模型单价。
"""

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone

from app.core import read_notes
from app.core.observability import get_langfuse


_ROOT = pathlib.Path(__file__).resolve().parent.parent
_OUT_DIR = _ROOT / "data/ch09/reports"
_OUT = _OUT_DIR / "cost_by_intent.txt"
_OUT_JSON = _OUT_DIR / "cost_by_intent.json"


def _window(days: int) -> tuple[str, str]:
    """按当前 UTC 时间生成近 days 天的 Langfuse 查询时间窗。"""

    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (now - timedelta(days=days)).strftime(fmt), now.strftime(fmt)


def _as_dict(value) -> dict:
    """SDK 对象按 API 原始字段名展开，也兼容受控测试中的字典。"""
    # Fern SDK 的 dict() 额外处理 FieldMetadata；普通 model_dump 不会展开这些别名。
    return value if isinstance(value, dict) else value.dict()


def _query(client, frm: str, to: str) -> Iterator[dict]:
    """完整游标分页；只取关联与用量字段，不读取对话正文。

    意图可能只在 intent-tagged 事件出现，因此不能只查询 GENERATION。
    游标重复时明确失败，避免死循环或产生看似完整的部分报告。
    """
    cursor, seen_cursors = None, set()
    while True:
        page = _as_dict(client.api.observations.get_many(
            fields="core,basic,usage,trace_context", limit=1000, cursor=cursor,
            from_start_time=datetime.fromisoformat(frm),
            to_start_time=datetime.fromisoformat(to),
        ))
        yield from (_as_dict(row) for row in page["data"])
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            return
        if cursor in seen_cursors:
            raise RuntimeError("Langfuse 分页游标重复，已停止统计；请稍后重试")
        seen_cursors.add(cursor)


def _token_total(row: dict) -> int:
    """total 包含 input/output；缓存细项也是子项，不另行相加。"""
    usage = row.get("usageDetails") or {}
    if usage.get("total") is not None:
        return int(usage["total"])
    return int(usage.get("input") or 0) + int(usage.get("output") or 0)


def _aggregate(rows: Iterable[dict]) -> list[dict]:
    """跨页关联意图与用量；同一事件去重，同一 trace 只计一次请求。"""
    traces = defaultdict(lambda: {"tokens": 0, "intents": set()})
    seen = set()
    for row in rows:
        trace_id, observation_id = row["traceId"], row["id"]
        if not trace_id:                         # 无请求关联的数据无法归因，不拼成虚构请求
            continue
        if (trace_id, observation_id) in seen:
            continue
        seen.add((trace_id, observation_id))
        trace = traces[trace_id]
        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        trace["intents"].update(t.removeprefix("intent:") for t in tags
                                if isinstance(t, str) and t.startswith("intent:")
                                and t.removeprefix("intent:"))
        if row.get("type") == "GENERATION":
            trace["tokens"] += _token_total(row)

    acc = defaultdict(lambda: {"tokens": 0, "count": 0})
    for trace in traces.values():
        if not trace["intents"]:
            continue
        if len(trace["intents"]) != 1:
            raise ValueError("同一 Langfuse trace 存在多个意图标签，无法可靠归因")
        intent = next(iter(trace["intents"]))
        acc[intent]["tokens"] += trace["tokens"]
        acc[intent]["count"] += 1
    return sorted(({"intent": intent, **totals} for intent, totals in acc.items()),
                  key=lambda row: row["tokens"], reverse=True)


def main() -> int:
    """
    执行一次 Cost Control 报告生成：
    查询 Langfuse → 按意图聚合 → 补齐单均 / 占比 → 生成读图小注 → 写出 txt 与 JSON。
    Langfuse 未配置时直接报错退出，不生成无来源的统计结果。
    """

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    client = get_langfuse()
    if client is None:
        print("Langfuse 未配置(.env 三变量),无账可查。")
        return 1

    # -------------------- 1. 查询并按意图聚合 Langfuse 用量 --------------------
    frm, to = _window(args.days)
    rows = _aggregate(_query(client, frm, to))

    # 平均值和占比只在报告生成阶段计算一次；JSON 直接保存结果，
    # 「观测与成本」页面只负责展示，不再复制一套统计公式。
    total = sum(r["tokens"] for r in rows) or 1
    for r in rows:
        r["avg_tokens"] = r["tokens"] // max(r["count"], 1)
        r["share"] = round(r["tokens"] / total, 4)

    # -------------------- 2. 生成便于人工查看的文本报告 --------------------
    lines = [
        f"=== 按意图 token 花销(近 {args.days} 天,数据源 Langfuse)===",
        f"{'意图':6s} {'请求数':>8s} {'总tokens':>12s} "
        f"{'平均tokens':>12s} {'占比':>7s}",
    ]

    for i, r in enumerate(rows):
        mark = "  ← 最烧钱" if i == 0 else ""
        lines.append(
            f"{r['intent']:6s} {r['count']:>8d} "
            f"{r['tokens']:>12,d} {r['avg_tokens']:>12,d} "
            f"{r['share']:>6.0%}{mark}"
        )

    if not rows:
        lines.append("(窗口内没有带 intent tag 的 trace——先聊几句再来)")

    out = "\n".join(lines)
    print(out)

    # -------------------- 3. 生成与本轮统计绑定的读图小注 --------------------
    # 传给小注生成器的字段使用页面中文口径，避免输出 avg_tokens 等代码字段名。
    payload = {
        "窗口天数": args.days,
        "各意图": [
            {
                "意图": r["intent"],
                "请求数": r["count"],
                "总 token": r["tokens"],
                "单均 token": r["avg_tokens"],
                "占比": r["share"],
            }
            for r in rows
        ],
        "总 token": sum(r["tokens"] for r in rows),
        "总请求数": sum(r["count"] for r in rows),
    }

    # 没有有效统计行时不调用模型生成小注，页面使用自己的固定兜底句。
    note = (
        asyncio.run(
            read_notes.generate("cost_by_intent", payload)
        )
        if rows
        else None
    )
    print("\n读图小注:" + (note if note else "本轮没有(页面用兜底句)"))

    # -------------------- 4. 写出文本报告与页面唯一 JSON 数据源 --------------------
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    _OUT.write_text(
        out + "\n",
        encoding="utf-8",
    )

    _OUT_JSON.write_text(
        json.dumps(
            {
                "meta": {
                    "days": args.days,
                    "source": "Langfuse",
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "window_from": frm,
                    "window_to": to,
                },
                "rows": rows,
                "total_tokens": payload["总 token"],
                "total_requests": payload["总请求数"],
                "read_notes": {
                    "cost_by_intent": note
                } if note else {},
            },
            ensure_ascii=False,
            indent=1,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"\n报告已落 {_OUT.relative_to(_ROOT)} "
        f"与 {_OUT_JSON.relative_to(_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
