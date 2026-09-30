"""
ch09 证据置信度阈值校准脚本：用 ch04 评估集为 evidence_confidence_threshold 找数据分离点。

校准过程：
1. 读取 tests/data/eval_ch04.jsonl，将 A_policy / B_model / C_colloquial 视为“应可答”，
   D_absent 视为“应拒答”。
2. 每道题统一走 hybrid_rerank 检索，再调用 compute_evidence_confidence()，
   得到与线上置信度闸完全同口径的 0~1 分数。
3. 分别统计“应可答”和“应拒答”两组分数分布，观察两类样本的可分程度。
4. 从 0.05 到 0.95 逐点扫描阈值：
      可答通过率 = 应可答样本中 confidence >= threshold 的比例
      应拒放行率 = 应拒答样本中 confidence >= threshold 的比例
      Youden J   = 可答通过率 - 应拒放行率
   取 Youden J 最大的点作为推荐阈值，同时兼顾“别误拦能答的”和“别误放该拒的”。
5. 推荐值只写入报告，不自动修改 settings；最终由人工查看分布和误放 / 误拦情况后回填配置。

脚本输出 confidence_calibration.txt / .json，供人工校准与“观测与成本”页面读取。
其中 read_notes 只负责基于结果生成读图小注，不参与置信度计算和推荐阈值选择。

运行：make calibrate-confidence
前提：Milvus、检索上游可用，且知识库已经完成构建。
"""

import asyncio
import json
import pathlib
import sys
from datetime import datetime

from app.core import read_notes, retrieval
from app.core.confidence import compute_evidence_confidence


_ROOT = pathlib.Path(__file__).resolve().parent.parent
_OUT_DIR = _ROOT / "data/ch09/reports"
_OUT = _OUT_DIR / "confidence_calibration.txt"
_OUT_JSON = _OUT_DIR / "confidence_calibration.json"

# A/B/C 属于知识库应能覆盖的样本；D_absent 单独作为“应拒答”组。
ANSWERABLE = {"A_policy", "B_model", "C_colloquial"}

_SEM = asyncio.Semaphore(8)
_LINES: list[str] = []


def _log(msg=""):
    """同时输出到终端并缓存文本，最终原样写入 txt 校准报告。"""

    print(msg, flush=True)
    _LINES.append(msg)


async def _conf(sample) -> tuple[str, float]:
    """对单条评估样本执行正式检索，并返回其桶类别和证据置信度。"""

    async with _SEM:
        hits = await retrieval.search_knowledge(
            sample["query"], strategy="hybrid_rerank"
        )
    return sample["bucket"], compute_evidence_confidence(hits).score


def _dist(name, xs) -> dict | None:
    """打印一行分布，并把同一份统计值返回给 JSON 产物，避免两处重复计算。"""

    xs = sorted(xs)
    if not xs:
        return None

    def p(q):
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    stat = {
        "n": len(xs),
        "min": round(xs[0], 3),
        "p25": round(p(.25), 3),
        "p50": round(p(.5), 3),
        "p75": round(p(.75), 3),
        "max": round(xs[-1], 3),
    }

    _log(
        f"{name:12s} n={stat['n']:3d} min={stat['min']:.3f} "
        f"p25={stat['p25']:.3f} p50={stat['p50']:.3f} "
        f"p75={stat['p75']:.3f} max={stat['max']:.3f}"
    )
    return stat


async def main():
    # -------------------- 1. 跑评估集：得到两组 evidence_confidence --------------------
    samples = [json.loads(ln) for ln in open("tests/data/eval_ch04.jsonl")]
    results = await asyncio.gather(*(_conf(s) for s in samples))

    answerable = [c for b, c in results if b in ANSWERABLE]
    absent = [c for b, c in results if b == "D_absent"]

    # -------------------- 2. 统计可答 / 应拒两组置信度分布 --------------------
    _log("=== evidence_confidence 分布(hybrid_rerank)===")
    dist = {
        "answerable": _dist("可答(ABC)", answerable),
        "absent": _dist("应拒(D)", absent),
    }

    # -------------------- 3. 扫描阈值，以 Youden J 选择最佳分离点 --------------------
    _log("\n=== 阈值扫描(通过率=conf>=t 的可答占比;放行率=conf>=t 的应拒占比)===")
    _log(f"{'t':>6s} {'可答通过率':>10s} {'应拒放行率':>10s} {'YoudenJ':>8s}")

    scan, best_t, best_j = [], 0.0, -1.0

    for i in range(5, 96):
        t = i / 100

        # 同一阈值下同时衡量两类结果：
        # 希望可答通过率尽量高，同时应拒放行率尽量低。
        tpr = sum(1 for c in answerable if c >= t) / len(answerable)
        fpr = sum(1 for c in absent if c >= t) / len(absent)
        j = tpr - fpr

        scan.append({
            "t": round(t, 2),
            "pass_rate": round(tpr, 3),
            "leak_rate": round(fpr, 3),
            "youden_j": round(j, 3),
        })

        if i % 5 == 0 or j > best_j:
            _log(f"{t:6.2f} {tpr:10.3f} {fpr:10.3f} {j:8.3f}")

        if j > best_j:
            best_t, best_j = t, j

    _log(
        f"\n推荐阈值 evidence_confidence_threshold = "
        f"{best_t:.2f}(Youden J={best_j:.3f})"
    )
    _log("请回填 app/config.py 默认值,并在 dev-notes/ch09.md 记录本次校准。")

    pick = next((r for r in scan if r["t"] == round(best_t, 2)), None)
    recommended = {
        "threshold": round(best_t, 2),
        "youden_j": round(best_j, 3),
        "pass_rate": pick["pass_rate"] if pick else None,
        "leak_rate": pick["leak_rate"] if pick else None,
    }

    # -------------------- 4. 生成页面小注并写出校准产物 --------------------
    # 读图小注只提供分布、推荐点和每 0.1 一条的代表性扫描线；
    # 完整 91 个扫描点仍保存在 JSON 中，但不全部塞给小注生成逻辑。
    def _zh(stat):
        return None if not stat else {
            "题数": stat["n"],
            "最低": stat["min"],
            "p25": stat["p25"],
            "中位": stat["p50"],
            "p75": stat["p75"],
            "最高": stat["max"],
        }

    note = await read_notes.generate(
        "confidence_calibration",
        {
            "证据置信度分布": {
                "可答(政策/型号/口语)": _zh(dist["answerable"]),
                "应拒(库外)": _zh(dist["absent"]),
            },
            "选定的线": {
                "阈值": recommended["threshold"],
                "可答通过率": recommended["pass_rate"],
                "应拒放行率": recommended["leak_rate"],
                "YoudenJ": recommended["youden_j"],
            },
            "阈值扫描(每 0.1 一条)": [
                {
                    "阈值": r["t"],
                    "可答通过率": r["pass_rate"],
                    "应拒放行率": r["leak_rate"],
                }
                for r in scan
                if round(r["t"] * 10, 3) == int(r["t"] * 10)
            ],
        },
    )

    _log("\n读图小注:" + (note if note else "本轮没有(页面用兜底句)"))

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    _OUT_JSON.write_text(
        json.dumps(
            {
                "meta": {
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "strategy": "hybrid_rerank",
                    "dataset": "tests/data/eval_ch04.jsonl",
                    "n_answerable": len(answerable),
                    "n_absent": len(absent),
                },
                "distribution": dist,
                "scan": scan,
                "recommended": recommended,
                "read_notes": {"confidence_calibration": note} if note else {},
            },
            ensure_ascii=False,
            indent=1,
        ) + "\n",
        encoding="utf-8",
    )

    _log(
        f"报告已落 {_OUT.relative_to(_ROOT)} "
        f"与 {_OUT_JSON.relative_to(_ROOT)}"
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
