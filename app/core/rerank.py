import asyncio
import re

import httpx

from app.config import settings

_VERSION_SEG = re.compile(r"/v\d+$")


def _rerank_url() -> str:
    """
    【痛点】
    配置项名字叫 [上游地址]，正常人理解就是填域名，比如`https://xxx-rerank.com`，不会手动加上`/v1`。
    如果只填域名，代码拼接出来就变成 `https://xxx‑rerank.com/rerank`，缺少版本前缀，上游直接返回 404。
    ⚠️ 坑点：该错误启动无异常，仅检索走到重排阶段才会404，极易因回答质量异常间接暴露。

    【本函数解决办法】
    拼接rerank重排接口完整地址，每次实时计算，修改配置无需重启服务。
    自动补版本路径：不同厂商版本段不一致；若配置仅填域名缺少/v1/v2，默认追加/v1。
    """
    base = settings.rerank_base_url.rstrip("/")
    if not _VERSION_SEG.search(base):
        base += "/v1"
    return base + "/rerank"


_RETRY_STATUS = {429, 500, 502, 503, 504}
_RETRIES = 3          # 限流退避重试次数
_BACKOFF = 1.5        # 秒,每次翻倍


# 封装重排服务`/rerank`接口的 HTTP POST 请求，**带指数退避重试逻辑**，
# 专门应对调用重排上游时的瞬时故障，服务于 RAG 检索‑重排链路与自动化评估脚本。
async def _post(url, json, headers, timeout):
    """
    带指数退避重试的POST请求，用于调用rerank重排上游接口。

    处理两类瞬时故障：
    1. HTTP状态码429限流（`429 Too Many Requests`，属于**瞬时限流，不是请求参数错误**）、5xx服务异常；
    2. 传输层抖动：连接断开、网络超时等TransportError。

    仅对瞬时可恢复错误重试；参数错误等4xx业务异常不重试，直接返回响应。
    ★ 重试耗尽后不静默吞错：抛出异常/返回失败响应，供评估脚本标记任务失败，
      避免评估分数被隐性压低（代码悄悄吞掉返回个空列表，这道题就会被记成召回失败）。
    """
    last: httpx.Response | None = None  # 缓存最后一次HTTP响应
    last_exc: Exception | None = None  # 缓存最后一次网络异常

    # 循环：1次原始请求 + _RETRIES次重试
    for i in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json=json, headers=headers, timeout=timeout)

        except httpx.TransportError as e:
            # 网络层瞬时故障；旧版未处理该异常，会直接打断评估
            last_exc = e
            if i < _RETRIES:
                await asyncio.sleep(_BACKOFF * (2 ** i))  # 指数退避等待
                continue
            raise

        # 不属于可重试错误，直接返回
        if resp.status_code not in _RETRY_STATUS:
            return resp

        # 命中429/5xx，保存响应，准备下一次重试
        last = resp
        if i < _RETRIES:
            await asyncio.sleep(_BACKOFF * (2 ** i))

    # 全部重试用尽后的收尾分支
    if last is None and last_exc is not None:
        raise last_exc  # 从未拿到HTTP响应，抛出网络异常
    return last  # 返回最后一次失败响应交给上层


async def rerank(query: str, docs: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """ 直连上游 /rerank 调 bge-reranker-v2-m3。返回 [(原始索引, 相关分)] 按分降序,截断 top_n。"""

    if not docs:
        return []
    payload = {"model": settings.rerank_model, "query": query, "documents": docs,
               "top_n": top_n or len(docs)}

    # rerank 不是 OpenAI 协议里的东西,这个接口走的是 Jina / Cohere 那套形状
    # (query + documents, 回 results 里的 index 与 relevance_score),
    # 所以这里手写请求，不用 openai 客户端。
    resp = await _post(_rerank_url(), payload,
                       {"Authorization": f"Bearer {settings.rerank_api_key}"}, 60)
    resp.raise_for_status()
    results = resp.json()["results"]

    # 返回值设计成 [(原始索引, 相关分)] 的列表， index 指的是文档在入参 documents 里的下标。
    # 调用方拿这个下标回自己的列表里取元数据，重排这一层压根不需要知道一个知识块长什么样。
    ranked = sorted(((r["index"], float(r["relevance_score"])) for r in results),
                    key=lambda x: x[1], reverse=True)
    return ranked[:top_n] if top_n else ranked
