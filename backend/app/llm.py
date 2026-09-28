"""LLM 客户端（OpenAI 兼容协议，当前对接 DeepSeek）。

stream_chat() 以异步生成器产出事件：
  ("delta", str)            文本增量
  ("tool_calls", list)      完整的工具调用列表（流结束时）
  ("usage", int)            本次调用消耗的 total_tokens
  ("finish", str)           finish_reason
后续接入其他模型时，实现同样的事件协议即可。
"""
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from . import config

logger = logging.getLogger("agent.llm")


class LLMError(Exception):
    pass


_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(config.LLM_TIMEOUT, connect=15))
    return _client


async def aclose() -> None:
    if _client is not None:
        await _client.aclose()


async def stream_chat(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.5,
) -> AsyncIterator[tuple[str, object]]:
    if not config.DEEPSEEK_API_KEY:
        raise LLMError("服务端未配置 DEEPSEEK_API_KEY")
    payload: dict = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}

    calls: dict[int, dict] = {}
    finish = None
    try:
        async with client().stream(
            "POST", f"{config.DEEPSEEK_BASE_URL}/chat/completions", headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:500]
                logger.error("LLM error %s: %s", resp.status_code, body)
                raise LLMError(f"模型服务返回 {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                usage = obj.get("usage")
                if usage and usage.get("total_tokens"):
                    yield ("usage", int(usage["total_tokens"]))
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        yield ("delta", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
    except httpx.HTTPError as e:
        logger.warning("LLM request failed: %r", e)
        raise LLMError("连接模型服务失败")

    if calls:
        out = []
        for i in sorted(calls):
            c = calls[i]
            if not c["id"]:
                c["id"] = f"call_{i}"
            out.append(c)
        yield ("tool_calls", out)
    yield ("finish", finish or "stop")
