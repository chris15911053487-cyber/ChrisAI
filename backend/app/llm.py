"""LLM 客户端：通用 OpenAI 兼容 Chat Completions 协议，具体接哪家由 models.ModelCfg 决定。

stream_chat() 以异步生成器产出事件：
  ("reasoning", str)        思考过程增量（仅思考模式）
  ("delta", str)            文本增量
  ("tool_calls", list)      完整的工具调用列表（流结束时）
  ("usage", int)            本次调用消耗的 total_tokens
  ("finish", str)           finish_reason
后续接入其他模型时，实现同样的事件协议即可。

请求体 = 基础字段（model/messages/stream/stream_options/temperature/max_tokens/tools）+ 模型的 extra_body。
extra_body 中值为 null 的键会从请求中删除（例如某些服务不支持 stream_options）。
思考内容兼容 delta.reasoning_content（DeepSeek、通义等）与 delta.reasoning（OpenRouter 等）。
"""
import asyncio
import json
import logging
import random
from typing import AsyncIterator, Optional

import httpx

from . import config
from .models import ModelCfg, default_model

logger = logging.getLogger("agent.llm")

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(Exception):
    pass


class _Retryable(Exception):
    pass


_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(config.LLM_TIMEOUT, connect=15))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def build_payload(m: ModelCfg, messages: list[dict], tools: Optional[list[dict]]) -> dict:
    payload: dict = {"stream_options": {"include_usage": True}}
    if m.temperature is not None:
        payload["temperature"] = m.temperature
    if m.max_tokens:
        payload["max_tokens"] = m.max_tokens
    for k, v in (m.extra_body or {}).items():
        if v is None:
            payload.pop(k, None)
        else:
            payload[k] = v
    payload.update({"model": m.model, "messages": messages, "stream": True})
    if tools:
        payload["tools"] = tools
    return payload


async def stream_chat(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    model: Optional[ModelCfg] = None,
) -> AsyncIterator[tuple[str, object]]:
    m = model or default_model()
    if m is None:
        raise LLMError("没有可用的对话模型，请管理员在「设置」中添加")
    payload = build_payload(m, messages, tools)
    url = f"{m.base_url}/chat/completions"
    key = m.key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}  # 本地模型（如 Ollama）可以不需要 Key
    attempts = max(0, config.LLM_RETRIES) + 1
    for attempt in range(attempts):
        emitted = False
        try:
            async for ev in _stream_once(url, headers, payload):
                emitted = True
                yield ev
            return
        except _Retryable as e:
            # 已向上游输出过内容就不能重试（否则前端会看到重复文字）
            if emitted or attempt == attempts - 1:
                raise LLMError(str(e))
            delay = min(8.0, 0.8 * 2 ** attempt) + random.uniform(0, 0.4)
            logger.warning("LLM retry %d/%d in %.1fs: %s", attempt + 1, attempts - 1, delay, e)
            await asyncio.sleep(delay)


async def _stream_once(url: str, headers: dict, payload: dict) -> AsyncIterator[tuple[str, object]]:
    calls: dict[int, dict] = {}
    finish = None
    try:
        async with client().stream(
            "POST", url, headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:500]
                logger.error("LLM error %s (%s): %s", resp.status_code, payload.get("model"), body)
                msg = f"模型服务返回 {resp.status_code}"
                if resp.status_code in (401, 403):
                    msg += "（API Key 无效或无权限）"
                elif resp.status_code == 404:
                    msg += "（接口地址或模型 ID 不正确）"
                if resp.status_code in RETRY_STATUS:
                    raise _Retryable(msg + ("（请求过多，请稍后再试）" if resp.status_code == 429 else ""))
                raise LLMError(msg)
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
                    think = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(think, str) and think:
                        yield ("reasoning", think)
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
        raise _Retryable("连接模型服务失败")

    if calls:
        out = []
        for i in sorted(calls):
            c = calls[i]
            if not c["id"]:
                c["id"] = f"call_{i}"
            out.append(c)
        yield ("tool_calls", out)
    yield ("finish", finish or "stop")
