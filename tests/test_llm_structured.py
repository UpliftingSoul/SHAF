"""llm.structured 的通路选择:只走 function calling + 流式,失败重试一次就抛给调用点。

为什么要有这组测试:结构化输出是否可用取决于「模型 × 通道」,不是模型本身的能力。
中转的非流式 OpenAI 兼容层会把 arguments 拼坏(MiniMax 与 Claude 全中),换成流式就
干净,所以这两个参数是绕坑的关键、不能被谁顺手改回去。机制也必须写死 function_calling:
吃默认值会落到 json_schema,那条路解析正文,而模型有时在 tool_calls 旁边多吐一段
思考正文,解析正文就返回 None。

这里也锁住「不许再加 json_mode 降级路」。曾经有过那条路,实测证明它是个陷阱:
MiniMax-M3 走 json_mode 10 次全败(把 JSON Schema 原样吐回来),同一模型同一通道走
function calling 流式非流式各 12 次全过。降级到必炸的通路,不如当场失败——异常抛到
调用点,那里降级成安全默认值,日志里还留得下真正的出错位置。用假模型跑,不调上游。
"""
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from app.core import llm


class _Check(BaseModel):
    useful: bool = Field(description="证据是否足够")
    reason: str = Field(description="一句话理由")


# function calling 那一路的三种结局。**没有 tool_calls 时 LangChain 返回 None、不抛异常**:
# PydanticToolsParser(first_tool_only=True) 拿不到工具调用就给 None,所以只测「抛异常」
# 等于测了一个真实不会发生的形态,重试逻辑漏掉 None 也照样绿。
FC_OK = "ok"
FC_NONE = None                                        # 通道吃掉了 tool_calls
FC_PARSE_ERR = ValueError("Invalid JSON: arguments 被拼坏")   # 中转把 arguments 拼坏
FC_TRANSIENT_ERR = TimeoutError("上游超时")            # 瞬时故障,跟通道能力无关


class _FakeModel:
    """function calling 那路可以配置成返回 None 或抛指定异常。

    method 一律记进 calls["methods"],好断言没人偷偷再挂一条 json_mode 通路上来。
    """

    def __init__(self, fc, calls: dict):
        self.fc, self.calls = fc, calls

    def with_structured_output(self, schema, method=None):
        self.calls["methods"].append(method)

        def fc(_x):
            self.calls["function_calling"] += 1
            if isinstance(self.fc, BaseException):
                raise self.fc
            if self.fc is FC_NONE:
                return None
            return schema(useful=True, reason="function_calling")
        return RunnableLambda(fc)


@pytest.fixture
def fake(monkeypatch):
    def make(fc=FC_OK):
        calls = {"function_calling": 0, "kwargs": [], "methods": []}
        def factory(**kw):
            calls["kwargs"].append(kw)
            return _FakeModel(fc, calls)
        monkeypatch.setattr(llm, "get_chat_model", factory)
        return calls
    return make


async def test_能拿到_tool_calls_的模型一次就成(fake):
    calls = fake(FC_OK)
    r = await llm.structured(_Check, model="gpt-5.5").ainvoke("证据够不够")
    assert r.reason == "function_calling"
    assert calls["function_calling"] == 1       # 成了就别多打一次上游


async def test_arguments_被拼坏时重试一次再抛给调用点(fake):
    # 以前这里会退到 json_mode。现在不退了:重试一次,还不成就把异常抛出去,
    # 由调用点降级成安全默认值(见 query_understanding / selfcheck 的 except 分支)
    calls = fake(FC_PARSE_ERR)
    with pytest.raises(ValueError):
        await llm.structured(_Check, model="claude-sonnet-5").ainvoke("证据够不够")
    assert calls["function_calling"] == 2
    assert "json_mode" not in calls["methods"]


async def test_通道吃掉_tool_calls_要转成异常_不能把_None_放出去(fake):
    # 这条是「拿不到 tool_calls」的真实形态:LangChain 不抛异常,而是返回 None。
    # 放任 None 传给调用点,会在 intent.py 的 r.intent、selfcheck.py 的 r.useful 上
    # 当场 AttributeError,报错位置指不到真正的出处。所以在 structured 里就地转成异常
    calls = fake(FC_NONE)
    with pytest.raises(OutputParserException):
        await llm.structured(_Check, model="claude-sonnet-5").ainvoke("证据够不够")
    assert calls["function_calling"] == 2      # 偶发不返回,原样重试一次


def test_通道吃掉_tool_calls_同步路径也一样(fake):
    calls = fake(FC_NONE)
    with pytest.raises(OutputParserException):
        llm.structured(_Check, model="claude-sonnet-5").invoke("证据够不够")
    assert calls["function_calling"] == 2


async def test_瞬时故障也重试一次_不影响下一次调用(fake):
    # 429 / 超时是瞬时的,重试一次值得。但失败不许留下任何跨调用的状态:
    # eval-ch04 跑几百次裁判,一次限流不该影响后半程的行为,不然这轮分数跟上轮不可比
    calls = fake(FC_TRANSIENT_ERR)
    with pytest.raises(TimeoutError):
        await llm.structured(_Check, model="gpt-5.5").ainvoke("证据够不够")
    assert calls["function_calling"] == 2
    with pytest.raises(TimeoutError):
        await llm.structured(_Check, model="gpt-5.5").ainvoke("再来一次")
    assert calls["function_calling"] == 4      # 下一次照样从 function calling 试起


def test_机制必须写死_function_calling_且只有这一条通路(fake):
    # 两件事一起锁:ChatOpenAI 的默认是 json_schema(解析正文),模型多吐一段思考正文
    # 就会拿到自然语言、返回 None,所以必须显式传 function_calling;而 json_mode 那条
    # 降级路已经证明是陷阱(MiniMax 会把 Schema 原样吐回来),不许再挂回来
    calls = fake(FC_OK)
    llm.structured(_Check, model="gpt-5.5")
    assert calls["methods"] == ["function_calling"]


def test_默认开流式(fake):
    # 中转的非流式 OpenAI 兼容层会把 arguments 拼坏,流式才干净。调用点不传就该是流式
    calls = fake(FC_OK)
    llm.structured(_Check, model="gpt-5.5")
    assert calls["kwargs"][-1]["streaming"] is True
    llm.structured(_Check, model="gpt-5.5", streaming=False)
    assert calls["kwargs"][-1]["streaming"] is False


def test_裁判要的_temperature_透传到底层模型(fake):
    # 当评委的链路必须能把温度按到 0:同一份输入重放,0.3 下判断会来回跳,
    # 那点抖动会直接写进评估分数里
    calls = fake(FC_OK)
    llm.structured(_Check, model="gpt-5.5", temperature=0)
    assert calls["kwargs"][-1]["temperature"] == 0
    llm.structured(_Check, model="gpt-5.5")
    assert calls["kwargs"][-1]["temperature"] is None    # 不传就别替调用点做决定


def test_默认温度不变_显式传0才是0():
    # 真的建模型,只看参数落没落下去(不发请求)
    assert llm.get_chat_model().temperature == 0.3
    assert llm.get_chat_model(temperature=0).temperature == 0


def test_minimax_structured_forces_non_streaming(monkeypatch):
    """MiniMax 流式下不吐 tool_calls,structured 必须给它关掉流式。

    两家上游毛病相反:中转的非流式兼容层会把 arguments 拼坏(所以默认开流式),
    MiniMax 反过来。这条防止有人把默认值改回去时静默退化——退化的表现是
    function calling 连着两次拿不到 tool_calls,整条链当场抛异常。
    """
    seen = {}

    def fake_get_chat_model(streaming=False, model=None, temperature=None, slot="chat"):
        seen["streaming"] = streaming
        raise RuntimeError("stop-here")      # 拿到参数就够,不真建模型

    monkeypatch.setattr(llm, "get_chat_model", fake_get_chat_model)
    for name, expect in (("MiniMax-M3", False), ("deepseek-ai/DeepSeek-V4-Flash", True)):
        seen.clear()
        with pytest.raises(RuntimeError):
            llm.structured(_Check, model=name)
        assert seen["streaming"] is expect, f"{name} 的 streaming 应为 {expect}"


class _FlakyModel(_FakeModel):
    """前 n 次 function calling 不给 tool_calls,之后正常——模拟 MiniMax 的偶发行为。"""

    def __init__(self, misses: int, calls: dict):
        super().__init__(FC_OK, calls)
        self.misses = misses

    def with_structured_output(self, schema, method=None):
        self.calls["methods"].append(method)

        def fc(_x):
            self.calls["function_calling"] += 1
            if self.calls["function_calling"] <= self.misses:
                return None
            return schema(useful=True, reason="function_calling")
        return RunnableLambda(fc)


@pytest.fixture
def flaky(monkeypatch):
    def make(misses: int):
        calls = {"function_calling": 0, "kwargs": [], "methods": []}
        monkeypatch.setattr(llm, "get_chat_model", lambda **kw: _FlakyModel(misses, calls))
        return calls
    return make


async def test_偶发拿不到_tool_calls_重试一次就该成(flaky):
    """一次抖动不许把整条链打崩。

    MiniMax 实测就是偶发不返回:同一 prompt 连着调有时给有时不给。既然没有降级路了,
    这一层重试就是唯一的缓冲,不能省。
    """
    calls = flaky(misses=1)
    r = await llm.structured(_Check, model="flaky-model").ainvoke("证据够不够")
    assert r.reason == "function_calling"
    assert calls["function_calling"] == 2          # 重试了一次,拿到了


async def test_失败不留跨调用状态_下一次照样从头试(flaky):
    """曾经的 sticky 机制会把连续失败的模型永久钉在降级路上,而且只加不减、进程重启才恢复。

    MiniMax 偶发不返回 + json_mode 必炸,两件事凑一起,三次抖动就能让整个进程再也答不出
    结构化结果。现在不留这种记忆:每次调用都是干净的两次机会。
    """
    calls = flaky(misses=2)                         # 前两次不给,第三次开始给
    with pytest.raises(OutputParserException):
        await llm.structured(_Check, model="dead-model").ainvoke("x")
    assert calls["function_calling"] == 2

    r = await llm.structured(_Check, model="dead-model").ainvoke("x")
    assert r.reason == "function_calling"           # 上一次全败,这一次照样试、照样能成


# 使用真实 LangChain 请求构造和解析器，模拟不接受 json_schema 的上游；不访问网络。
@pytest.mark.parametrize("entry", ["prelabel", "simulate", "mining", "mining_injected", "extract"])
async def test_business_entries_use_compatible_tool_calls(monkeypatch, entry):
    from app.api import extract
    from app.kb import mining
    from scripts.ch10 import build_corpus, prelabel

    outputs = {
        "_Labeled": {"labels": ["尺码", "退换货"]},
        "SimulatedCorpusBatch": {"items": [{"text": "买大了想退", "labels": ["尺码", "退换货"]}]},
        "QaExtraction": {"questions": ["如何退货"], "answers": ["联系客服申请"]},
        "AfterSalesTicket": {"order_id": "1001", "request_type": "退款",
                             "expected_solution": "申请退款"},
    }
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {
                "message": "This response_format type is unavailable now",
                "type": "invalid_request_error", "code": "invalid_request_error",
            }})
        name = body["tools"][0]["function"]["name"]
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0,
                "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{
                        "id": "call-test", "type": "function", "function": {
                            "name": name, "arguments": json.dumps(outputs[name]),
                        },
                    }],
                },
            }],
        })

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            model = ChatOpenAI(model="deepseek-flash", api_key="test-only",
                               base_url="https://deepseek.invalid/v1", max_retries=0,
                               http_client=sync_client, http_async_client=async_client)
            monkeypatch.setattr(llm, "get_chat_model", lambda **kwargs: model)
            if entry == "mining_injected":
                def unexpected_factory(**kwargs):
                    raise AssertionError("注入模型时不能重新创建默认模型")
                monkeypatch.setattr(llm, "get_chat_model", unexpected_factory)
            if entry == "prelabel":
                assert await prelabel.prelabel_one("买大了想退") == ["尺码", "退换货"]
            elif entry == "simulate":
                result = await build_corpus.simulate("尺码", 1)
                assert result == [{"text": "买大了想退", "labels": ["尺码", "退换货"],
                                   "origin": "simulated"}]
            elif entry.startswith("mining"):
                client = model if entry == "mining_injected" else None
                result = await mining.extract_qa(["用户:如何退货"], model=client)
                assert result == [mining.QaPair(question="如何退货", answer="联系客服申请")]
            else:
                result = await extract.get_extractor().ainvoke({"text": "订单1001申请退款"})
                assert result.model_dump() == outputs["AfterSalesTicket"]
    assert len(requests) == 1
    assert "response_format" not in requests[0]
    assert requests[0]["tools"][0]["type"] == "function"
    if entry == "simulate":
        function = requests[0]["tools"][0]["function"]
        assert function["name"] == "SimulatedCorpusBatch"
        assert "模拟" in function["description"]


async def test_simulation_logs_parser_cause_without_full_payload(monkeypatch, capsys):
    from scripts.ch10 import build_corpus

    def fail(_):
        raise OutputParserException("Unknown tool type: 'get_weather'\nfull-batch-payload")

    monkeypatch.setattr(llm, "structured", lambda schema: RunnableLambda(fail))
    assert await build_corpus.simulate("订单修改", 1) == []
    log = capsys.readouterr().out
    assert log.count("Unknown tool type: 'get_weather'") == 5
    assert "full-batch-payload" not in log
