"""reasoning_effort 思考力度透传：决策强制 low、发言用设定值、厂商不支持时剥参数记忆。"""
import asyncio
from types import SimpleNamespace

import httpx
from openai import APIStatusError

from app.llm.client import LlmClient, _param_rejected
from app.models import ModelPreset


class _Msg:
    content = "ok"


class _Resp:
    choices = [SimpleNamespace(message=_Msg())]


class _Delta:
    content = "x"
    reasoning_content = None


class _Chunk:
    choices = [SimpleNamespace(delta=_Delta())]


class _Stream:
    def __init__(self):
        self._done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True
        return _Chunk()


def _preset(**kw):
    kw.setdefault("speech_reasoning_effort", "medium")
    kw.setdefault("decision_reasoning_effort", "low")
    return ModelPreset(id="p", name="n", base_url="http://x", api_key="k", model="m", **kw)


def _wire(captured):
    client = LlmClient()

    class FakeCompletions:
        async def create(self, **kw):
            captured.append(kw)
            return _Stream() if kw.get("stream") else _Resp()

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client.client_for = lambda p: fake
    return client


def test_decision_effort_from_preset():
    """决策请求（complete/流式决策）用预设的 decision_reasoning_effort。"""
    for effort in ("low", "high"):
        captured = []
        client = _wire(captured)
        asyncio.run(client.complete(_preset(decision_reasoning_effort=effort), [{"role": "user", "content": "hi"}]))
        assert captured[0]["reasoning_effort"] == effort


def test_speech_effort_from_preset():
    """发言请求用预设设定的力度；留空则完全不传该参数。"""
    captured = []
    client = _wire(captured)

    async def _eat():
        async for _ in client.speak(_preset(speech_reasoning_effort="high"), [{"role": "user", "content": "hi"}]):
            pass

    asyncio.run(_eat())
    assert captured[0]["reasoning_effort"] == "high"

    captured2 = []
    client2 = _wire(captured2)

    async def _eat2():
        async for _ in client2.speak(_preset(speech_reasoning_effort="low"), [{"role": "user", "content": "hi"}]):
            pass

    asyncio.run(_eat2())
    assert captured2[0]["reasoning_effort"] == "low"


def test_param_rejected_and_memo():
    """400 提到参数名 → 识别为不支持并剥掉重试；记忆后后续请求不再携带。"""
    err = APIStatusError(
        "Unknown parameter: 'reasoning_effort'",
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    assert _param_rejected(err)

    captured = []
    client = LlmClient()

    class FlakyCompletions:
        async def create(self, **kw):
            captured.append(kw)
            if "reasoning_effort" in kw:
                raise err
            return _Resp()

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FlakyCompletions()))
    client.client_for = lambda p: fake

    asyncio.run(client.complete(_preset(), [{"role": "user", "content": "hi"}]))
    assert len(captured) == 2  # 带参失败 → 剥参重试成功
    assert "reasoning_effort" not in captured[1]

    captured.clear()
    asyncio.run(client.complete(_preset(), [{"role": "user", "content": "hi"}]))
    assert "reasoning_effort" not in captured[0]  # 已记忆，不再尝试


def test_system_prompt_pacing_wording():
    """prompt 措辞：去掉不可执行的累计字数约束，改为行为导向。"""
    from app.engine.board import BUILTIN_BOARDS
    from app.engine.game import start_game
    from app.llm.prompts import system_prompt

    import sys
    sys.path.insert(0, "tests")
    from test_engine import make_game

    game = make_game(BUILTIN_BOARDS[0], n=6)
    start_game(game)
    text = system_prompt(game, game.players[0])
    assert "10000" not in text
    assert "几句话定案" in text
