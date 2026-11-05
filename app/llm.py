"""OpenAI SDK 封装：统一温度/超时/思考开关，并暴露流式与一次性两种调用。

为什么需要“思考开关”
--------------------
本项目使用的 deepseek-v4-pro / deepseek-flash 都是**推理模型**：它们会先产出
`reasoning_content`（思维链），再产出正文。实测：

    plain                3.8s  reasoning=232  content=34
    extra_body={"thinking": {"type": "disabled"}}  1.3s  reasoning=0  content=30

也就是说：审题、提纲、评分这些“要想清楚”的阶段应当保留思考；而正文写作如果保留
思考，首字要等 30~60 秒，交互上不可接受，但质量又确实更好。因此做成**分阶段开关**
（见 pipeline.py 的 _THINKING 表），并且把思考过程以“思考中 N 字”的形式流给前端，
避免页面长时间空白。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Iterable, Literal

from openai import AsyncOpenAI

from .config import settings

Stage = Literal["reasoning", "content"]


class LLMError(RuntimeError):
    """模型调用失败的统一异常，上层据此给前端可读提示。"""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.configured:
            raise LLMError("未配置 OPENAI_API_KEY，请在 .env 中填写后重启服务")
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.request_timeout,
            max_retries=1,
        )
    return _client


def _normalize(messages: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        out.append({"role": role, "content": content if isinstance(content, str) else str(content)})
    return out


def _extra(thinking: bool) -> dict:
    return {} if thinking else {"thinking": {"type": "disabled"}}


async def stream_chat_rich(
    messages: Iterable[dict],
    model: str | None = None,
    temperature: float = 0.85,
    max_tokens: int = 4000,
    thinking: bool = True,
) -> AsyncIterator[tuple[str, str]]:
    """逐块产出 (阶段, 文本)，阶段为 "reasoning" 或 "content"。"""
    client = _get_client()
    try:
        stream = await client.chat.completions.create(
            model=model or settings.model,
            messages=_normalize(messages),  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            extra_body=_extra(thinking),
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield "reasoning", reasoning
            text = getattr(delta, "content", None)
            if text:
                yield "content", text
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一收敛成 LLMError
        raise LLMError(f"模型调用失败：{exc}") from exc


async def stream_chat(
    messages: Iterable[dict],
    model: str | None = None,
    temperature: float = 0.85,
    max_tokens: int = 4000,
    thinking: bool = True,
) -> AsyncIterator[str]:
    """只要正文的流式版本（便于单独测试）。"""
    async for kind, text in stream_chat_rich(
        messages, model=model, temperature=temperature, max_tokens=max_tokens, thinking=thinking
    ):
        if kind == "content":
            yield text


async def complete_chat(
    messages: Iterable[dict],
    model: str | None = None,
    temperature: float = 0.5,
    max_tokens: int = 2000,
    thinking: bool = True,
) -> str:
    """一次性拿到完整正文，用于审题/评分这类需要结构化结果的阶段。"""
    client = _get_client()
    try:
        resp = await client.chat.completions.create(
            model=model or settings.fast_model,
            messages=_normalize(messages),  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=_extra(thinking),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"模型调用失败：{exc}") from exc
    if not resp.choices:
        raise LLMError("模型返回为空")
    return resp.choices[0].message.content or ""


__all__ = ["LLMError", "stream_chat", "stream_chat_rich", "complete_chat"]
