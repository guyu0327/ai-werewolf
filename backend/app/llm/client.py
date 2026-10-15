"""OpenAI 兼容接口封装：流式文本 + 结构化输出（json_schema → json_object → 正则提取 降级链）。"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Callable, Type, TypeVar

from openai import APIStatusError, AsyncOpenAI, AuthenticationError, NotFoundError, PermissionDeniedError
from pydantic import BaseModel

from ..models import ModelPreset

T = TypeVar("T", bound=BaseModel)


class LlmError(RuntimeError):
    pass


class LlmFatalError(LlmError):
    """账号/配置级致命错误（欠费、订阅失效、鉴权失败、模型不存在）：应终止对局而非系统代打。"""


_FATAL_MARKERS = (
    "invalidsubscription",
    "subscription",
    "arrearage",
    "欠费",
    "insufficient_quota",
    "insufficient balance",
    "exceeded your current quota",
    "billing",
    "invalid api key",
    "unauthorized",
)


def _raise_if_fatal(e: Exception) -> None:
    """识别账号/配置级错误并转成 LlmFatalError（中文原因，便于直接展示）；其余异常原样放行。"""
    reason: str | None = None
    if isinstance(e, AuthenticationError):
        reason = "API Key 无效或鉴权失败"
    elif isinstance(e, PermissionDeniedError):
        reason = "无该模型的访问权限"
    elif isinstance(e, NotFoundError):
        reason = "模型或接口地址不存在"
    elif isinstance(e, APIStatusError):
        text = str(e).lower()
        if e.status_code == 402:
            reason = "账户欠费"
        elif e.status_code == 400 and any(m in text for m in _FATAL_MARKERS):
            reason = "订阅失效或账户欠费"
        elif e.status_code == 429 and any(m in text for m in _FATAL_MARKERS):
            reason = "配额用尽或账户欠费"
    if reason is not None:
        raise LlmFatalError(reason) from e


def _param_rejected(e: Exception) -> bool:
    """识别「厂商不认识 reasoning_effort 参数」的 400 错误：剥掉该参数重试即可恢复。"""
    if not isinstance(e, APIStatusError) or e.status_code != 400:
        return False
    text = str(e).lower()
    return any(
        m in text
        for m in ("reasoning_effort", "reasoning", "effort", "unrecognized", "unknown parameter", "extra inputs", "unexpected")
    )


def extract_json(text: str) -> dict:
    """从模型输出中提取第一个完整的 JSON 对象（容忍代码块/前后缀噪声）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start = text.find("{")
    if start < 0:
        raise ValueError("输出中没有 JSON 对象")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON 对象未闭合")


class LlmClient:
    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}
        self._effort_unsupported: set[tuple[str, str]] = set()  # 不认 reasoning_effort 参数的厂商（按 base_url+key 记）

    def client_for(self, preset: ModelPreset) -> AsyncOpenAI:
        key = (preset.base_url, preset.api_key)
        client = self._clients.get(key)
        if client is None:
            client = AsyncOpenAI(base_url=preset.base_url, api_key=preset.api_key or "none", timeout=180.0, max_retries=1)
            self._clients[key] = client
        return client

    async def _create(self, preset: ModelPreset, reasoning_effort: str | None, **kwargs):
        """统一请求入口：按需透传 reasoning_effort；厂商不认该参数（400）时剥掉重试一次并记住，之后不再发。"""
        key = (preset.base_url, preset.api_key)
        if reasoning_effort and key not in self._effort_unsupported:
            try:
                return await self.client_for(preset).chat.completions.create(reasoning_effort=reasoning_effort, **kwargs)
            except Exception as e:
                _raise_if_fatal(e)
                if _param_rejected(e):
                    self._effort_unsupported.add(key)
                else:
                    raise
        return await self.client_for(preset).chat.completions.create(**kwargs)

    async def speak(
        self, preset: ModelPreset, messages: list[dict], on_thinking: Callable[[str], None] | None = None
    ) -> AsyncIterator[str]:
        """流式生成发言文本，逐段 yield 增量；思考型模型的 reasoning 增量经 on_thinking 实时回调。

        不传 max_tokens：reasoning 计入 completion 预算，任何预算都可能被长思考吃光，
        流会以 finish=length 正常结束、正文 0 字（不抛异常），表现为发言变「……」。
        """
        try:
            stream = await self._create(
                preset,
                preset.speech_reasoning_effort,
                model=preset.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=preset.speech_temperature,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and on_thinking is not None:
                    on_thinking(reasoning)
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            _raise_if_fatal(e)
            raise

    async def complete(
        self,
        preset: ModelPreset,
        messages: list[dict],
        response_format: dict | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        kwargs: dict = {}
        if response_format:
            kwargs["response_format"] = response_format
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = await self._create(
                preset,
                reasoning_effort if reasoning_effort is not None else preset.decision_reasoning_effort,
                model=preset.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=preset.decision_temperature,
                **kwargs,
            )
        except Exception as e:
            _raise_if_fatal(e)
            raise
        return resp.choices[0].message.content or ""

    async def decide(
        self,
        preset: ModelPreset,
        messages: list[dict],
        schema: Type[T],
        on_thinking: Callable[[str], None] | None = None,
        reasoning_effort: str | None = None,
    ) -> T:
        """结构化决策：先走流式（on_thinking 可实时回调 reasoning），失败再进降级链。

        流式首试对所有调用方都用：思考型模型的 reasoning 会吃掉 completion 预算，
        带小预算的非流式请求会变成空正文，流式 + 不设 max_tokens 才能稳定拿到 JSON。
        降级链（json_schema → json_object → 正则提取）单轮 3 格式；全部失败抛 LlmError，由调用方兜底。
        reasoning_effort 覆盖预设的决策思考力度（如轻量创作类调用传 "low"）。
        """
        try:
            text = await self._complete_stream(preset, messages, on_thinking, reasoning_effort=reasoning_effort)
            return schema.model_validate(extract_json(text))
        except LlmFatalError:
            raise  # 账号级故障无需降级：其余格式同样必败，直接终止对局
        except Exception:
            pass  # 流式首试失败（含不支持流式/reasoning 的服务），静默进入降级链
        name = schema.__name__
        formats: list[dict | None] = [
            {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema.model_json_schema(), "strict": False},
            },
            {"type": "json_object"},
            None,
        ]
        last_err: Exception | None = None
        for fmt in formats:
            try:
                text = await self.complete(preset, messages, response_format=fmt, timeout=90.0, reasoning_effort=reasoning_effort)
                return schema.model_validate(extract_json(text))
            except LlmFatalError:
                raise
            except Exception as e:  # 单个格式失败继续降级
                last_err = e
        raise LlmError(f"结构化输出失败: {last_err}")

    async def _complete_stream(
        self,
        preset: ModelPreset,
        messages: list[dict],
        on_thinking: Callable[[str], None] | None,
        reasoning_effort: str | None = None,
    ) -> str:
        """流式决策：reasoning_content 增量实时回调，content 缓冲为完整文本返回。

        看门狗按「无输出间隔」计：read 超时 45s 收不到任何新 chunk 判定连接卡死，
        思考持续吐字则不限总时长。纯 float 超时避免与 openai 内置 httpx2 的类型耦合。
        """
        try:
            stream = await self._create(
                preset,
                reasoning_effort if reasoning_effort is not None else preset.decision_reasoning_effort,
                model=preset.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=preset.decision_temperature,
                stream=True,
                timeout=45.0,
            )
            parts: list[str] = []
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and on_thinking is not None:
                    on_thinking(reasoning)
                if delta and delta.content:
                    parts.append(delta.content)
            return "".join(parts)
        except Exception as e:
            _raise_if_fatal(e)
            raise

    async def ping(self, preset: ModelPreset) -> str:
        """连通性测试：返回模型回复的首行。"""
        client = self.client_for(preset)
        resp = await client.chat.completions.create(
            model=preset.model,
            messages=[{"role": "user", "content": "请回复：连接成功"}],
            max_tokens=20,
        )
        return (resp.choices[0].message.content or "").strip().splitlines()[0] if resp.choices else ""
