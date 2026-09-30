"""Embedding 与 Rerank 客户端（OpenAI 兼容 /embeddings，Cohere/Jina 风格 /rerank）。

- 未配置 EMBEDDING_API_KEY 时 enabled() 为 False，知识库只用关键词检索。
- 所有调用失败都抛 EmbeddingError，由上层降级处理，绝不影响对话。
- 用量计入 usage 表的 "embed" 键，超过 EMBEDDING_DAILY_TOKENS 后当天不再调用。
"""
import asyncio
import logging
import random
from typing import Optional
from urllib.parse import urlparse

import httpx
import numpy as np

from . import config, db

logger = logging.getLogger("agent.embed")

USAGE_KEY = "embed"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
QWEN3_INSTRUCT = "Instruct: 根据用户的问题，检索能回答该问题的文档片段\nQuery: "


class EmbeddingError(Exception):
    pass


_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def enabled() -> bool:
    return bool(config.EMBEDDING_API_KEY and config.EMBEDDING_MODEL)


def _rerank_url() -> str:
    return config.RERANK_BASE_URL or config.EMBEDDING_BASE_URL


def _rerank_key() -> str:
    return config.RERANK_API_KEY or config.EMBEDDING_API_KEY


def rerank_enabled() -> bool:
    return bool(_rerank_key() and config.RERANK_MODEL)


def model_id() -> str:
    """存进 kb_chunks.embed_model，模型或服务地址变化时触发重算。"""
    return f"{config.EMBEDDING_MODEL}@{urlparse(config.EMBEDDING_BASE_URL).netloc}"


def info() -> dict:
    return {
        "enabled": enabled(),
        "model": config.EMBEDDING_MODEL if enabled() else None,
        "provider": urlparse(config.EMBEDDING_BASE_URL).netloc if enabled() else None,
        "rerank": config.RERANK_MODEL if rerank_enabled() else None,
    }


def query_prefix() -> str:
    if config.EMBEDDING_QUERY_INSTRUCT:
        return config.EMBEDDING_QUERY_INSTRUCT.replace("\\n", "\n")
    return QWEN3_INSTRUCT if "qwen3-embedding" in config.EMBEDDING_MODEL.lower() else ""


def quota_exhausted() -> bool:
    return db.usage_get(USAGE_KEY)["tokens"] >= config.EMBEDDING_DAILY_TOKENS


def _usage_tokens(body: dict) -> int:
    u = body.get("usage") or {}
    n = u.get("total_tokens") or u.get("prompt_tokens")
    if not n:
        t = (body.get("meta") or {}).get("tokens") or {}
        n = (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0)
    return int(n or 0)


async def _post(url: str, key: str, payload: dict, retries: int = 2) -> dict:
    if quota_exhausted():
        raise EmbeddingError("今日向量服务额度已用完")
    for attempt in range(retries + 1):
        try:
            r = await client().post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            err = f"连接失败：{e!r}"
        else:
            if r.status_code == 200:
                body = r.json()
                n = _usage_tokens(body)
                if n:
                    db.usage_add(USAGE_KEY, tokens=n)
                return body
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in RETRY_STATUS:
                raise EmbeddingError(err)
        if attempt < retries:
            await asyncio.sleep(min(8.0, 1.0 * 2 ** attempt) + random.uniform(0, 0.5))
    raise EmbeddingError(err)


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


async def embed(texts: list[str], query: bool = False) -> np.ndarray:
    """返回 (n, dim) 的 L2 归一化 float32 矩阵。"""
    if not enabled():
        raise EmbeddingError("未配置向量模型")
    if query:
        texts = [query_prefix() + t for t in texts]
    body = await _post(f"{config.EMBEDDING_BASE_URL}/embeddings", config.EMBEDDING_API_KEY,
                       {"model": config.EMBEDDING_MODEL, "input": texts, "encoding_format": "float"})
    data = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
    if len(data) != len(texts):
        raise EmbeddingError(f"返回向量数量不符：{len(data)} != {len(texts)}")
    return _normalize(np.asarray([d["embedding"] for d in data], dtype=np.float32))


async def rerank(query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
    """返回 [(原下标, 相关性分数)]，按分数降序。"""
    if not rerank_enabled():
        raise EmbeddingError("未配置重排模型")
    body = await _post(f"{_rerank_url()}/rerank", _rerank_key(),
                       {"model": config.RERANK_MODEL, "query": query, "documents": docs,
                        "top_n": top_n, "return_documents": False}, retries=1)
    out = [(int(r["index"]), float(r.get("relevance_score", r.get("score", 0.0)))) for r in body.get("results") or []]
    out = [(i, s) for i, s in out if 0 <= i < len(docs)]
    out.sort(key=lambda x: -x[1])
    return out


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blobs(blobs: list[bytes]) -> np.ndarray:
    return np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), -1)
