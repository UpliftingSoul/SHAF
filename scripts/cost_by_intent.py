"""
ch09 Cost Control：从 Langfuse 按意图汇总近 N 天的 token 消耗，定位最耗资源的业务意图。

意图分类阶段会把结果写成 intent:xxx tag 挂到 trace，
本脚本不自行埋点，而是直接查询 Langfuse Metrics API，以 observations 视图按 tags + traceId 拉取 token 明细，
再在客户端按 intent 聚合出请求数、总 token、单均 token 与占比；
未携带 intent tag 的历史 trace 不纳入统计。

运行：make cost-report
可通过 DAYS=N 指定统计窗口，默认近 7 天；要求 Langfuse 已运行且 .env 配好连接所需三变量。

产物：
- data/ch09/reports/cost_by_intent.txt：便于终端 / 人工查看的文本报告；
- data/ch09/reports/cost_by_intent.json：「观测与成本」页面的唯一数据源，
  页面直接读取其中已经算好的统计值，不再自行重复计算，避免报告与页面口径漂移。

兼容口径：当前自部署 Langfuse v3.218 使用 SDK 的 api.legacy.metrics_v1；
v1 不提供 traces 视图，因此从 observations 视图拉取明细，再按 traceId 去重统计请求数。

成本边界：自定义模型（如 glm-5.2）没有 Langfuse 内置单价，本脚本只统计 token，
不维护或推算金额价格表；如需查看实际费用，应在 Langfuse 中配置对应模型单价。
"""

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict
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


def _rows_of(resp) -> list[dict]:
    """兼容 Langfuse Metrics API 的对象 / dict 响应，将 data 统一转换为字典列表。"""

    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    return [row if isinstance(row, dict) else row.dict() for row in (data or [])]


def _query(client, frm: str, to: str) -> list[dict]:
    """
    查询指定时间窗内的 token 明细。
    v1 Metrics API 从 observations 视图按 tags + traceId 分组，
    为后续“按意图统计 token、按 trace 去重计请求数”提供原始数据。
    """

    resp = client.api.legacy.metrics_v1.metrics(query=json.dumps({
        "view": "observations",
        "metrics": [{"measure": "totalTokens", "aggregation": "sum"}],
        "dimensions": [{"field": "tags"}, {"field": "traceId"}],
        "filters": [],
        "fromTimestamp": frm,
        "toTimestamp": to,
    }))
    return _rows_of(resp)


def _aggregate(rows: list[dict]) -> list[dict]:
    """
    将 Metrics 明细按 intent 聚合。
    token 直接累加，request count 按 traceId 去重；没有 intent:xxx tag 的旧 trace 忽略。
    最终按总 token 降序返回，最耗 token 的意图自然排在首位。
    """

    acc: dict[str, dict] = defaultdict(
        lambda: {"tokens": 0, "traces": set()}
    )

    for row in rows:
        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]

        # intent 来源只认 classify_intent 写入的 intent:xxx tag；
        # 没有该 tag 的历史数据无法可靠归类，因此不强行计入某个兜底桶。
        intents = [
            t.removeprefix("intent:")
            for t in tags
            if str(t).startswith("intent:")
        ]
        if not intents:
            continue

        intent = intents[0]
        acc[intent]["tokens"] += int(
            float(row.get("sum_totalTokens") or 0)
        )
        acc[intent]["traces"].add(row.get("traceId"))

    return sorted(
        (
            {
                "intent": k,
                "tokens": v["tokens"],
                "count": len(v["traces"]),
            }
            for k, v in acc.items()
        ),
        key=lambda r: r["tokens"],
        reverse=True,
    )


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
