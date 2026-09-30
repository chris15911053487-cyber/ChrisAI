"""模型调用：通用 payload / 重试 / 思考内容回传；向量化、混合检索、重排与降级。"""
import asyncio
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import agent, config, db, embeddings, knowledge, llm, main
from app.models import ModelCfg

from .test_knowledge import DOC, make_kb, upload


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


# ---------------- 通用对话模型 ----------------
def cfg(**kw):
    base = dict(id="a" * 32, name="T", base_url="https://llm.example.com/v1", model="m-1", api_key="k")
    return ModelCfg(**{**base, **kw})


def test_payload_merges_extra_body():
    m = cfg(temperature=0.3, max_tokens=500,
            extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high", "stream_options": None,
                        "top_p": 0.9})
    p = llm.build_payload(m, [{"role": "user", "content": "x"}], [{"t": 1}])
    assert p["model"] == "m-1" and p["stream"] is True and p["tools"] == [{"t": 1}]
    assert p["thinking"] == {"type": "enabled"} and p["reasoning_effort"] == "high" and p["top_p"] == 0.9
    assert p["temperature"] == 0.3 and p["max_tokens"] == 500
    assert "stream_options" not in p  # null = 删除默认参数
    p = llm.build_payload(cfg(), [], None)
    assert "temperature" not in p and "tools" not in p and p["stream_options"] == {"include_usage": True}


def _sse(*objs):
    return "".join(f"data: {json.dumps(o)}\n\n" for o in objs) + "data: [DONE]\n\n"


def _mock_llm(monkeypatch, handler):
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    _real_sleep = asyncio.sleep

    async def no_wait(_s):
        await _real_sleep(0)

    monkeypatch.setattr(llm.asyncio, "sleep", no_wait)


def _collect(m=None):
    async def go():
        return [ev async for ev in llm.stream_chat([{"role": "user", "content": "hi"}], model=m or cfg())]
    return asyncio.run(go())


def test_request_goes_to_model_endpoint(monkeypatch):
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"reasoning": "嗯"}}]},
                                             {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]}))

    _mock_llm(monkeypatch, handler)
    evs = _collect(cfg(api_key="sk-1"))
    assert seen == {"url": "https://llm.example.com/v1/chat/completions", "auth": "Bearer sk-1"}
    assert ("reasoning", "嗯") in evs and ("delta", "好") in evs  # 兼容 delta.reasoning

    _collect(cfg(api_key=""))  # 本地模型不需要 Key：不发送 Authorization
    assert seen["auth"] is None


def test_key_from_env(monkeypatch):
    monkeypatch.setenv("FOO_API_KEY", "from-env")
    assert cfg(api_key="", key_env="FOO_API_KEY").key() == "from-env"
    assert cfg(api_key="direct", key_env="FOO_API_KEY").key() == "direct"


def test_retry_on_429_then_success(monkeypatch):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, text="busy")
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"reasoning_content": "想"}}]},
                                             {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]}))

    _mock_llm(monkeypatch, handler)
    evs = _collect()
    assert n["calls"] == 2
    assert ("reasoning", "想") in evs and ("delta", "好") in evs


def test_no_retry_on_401(monkeypatch):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(401, text="bad key")

    _mock_llm(monkeypatch, handler)
    with pytest.raises(llm.LLMError, match="Key"):
        _collect()
    assert n["calls"] == 1


def test_retry_exhausted(monkeypatch):
    monkeypatch.setattr(config, "LLM_RETRIES", 2)
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(503, text="down")

    _mock_llm(monkeypatch, handler)
    with pytest.raises(llm.LLMError):
        _collect()
    assert n["calls"] == 3


def test_history_reasoning_and_flatten(client):
    sid = client.post("/api/sessions").json()["id"]
    db.add_message(sid, "user", "q")
    db.add_message(sid, "assistant", "我先看看", tool_calls=[{"id": "c1", "type": "function",
                   "function": {"name": "list_files", "arguments": "{}"}}], reasoning="先看看文件")
    db.add_message(sid, "tool", "（空）", tool_call_id="c1")
    db.add_message(sid, "assistant", "没有文件")  # 旧消息无 reasoning

    h = agent.build_history(sid, replay_reasoning=True)
    asst = [m for m in h if m["role"] == "assistant"]
    assert asst[0]["reasoning_content"] == "先看看文件" and asst[1]["reasoning_content"] == ""
    assert all("reasoning_content" not in m for m in agent.build_history(sid))

    flat = agent.build_history(sid, replay_reasoning=True, flatten_tools=True)
    assert [m["role"] for m in flat] == ["user", "assistant", "assistant"]
    assert all("tool_calls" not in m and "reasoning_content" not in m for m in flat if m["content"] == "我先看看")


# ---------------- 向量 / 混合检索 ----------------
VOCAB = ["住宿", "酒店", "报销", "期限", "审批", "经理", "发票", "付款"]


def fake_vec(text: str) -> np.ndarray:
    """语义玩具：把"酒店"视为"住宿"的同义词，用于验证向量召回能补足关键词检索。"""
    t = text.replace("酒店", "住宿")
    v = np.array([t.count(w) for w in VOCAB], dtype=np.float32) + 0.01
    return v / np.linalg.norm(v)


@pytest.fixture()
def vec_on(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "k")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-embed")
    calls = {"embed": 0, "rerank": 0}

    async def fake_embed(texts, query=False):
        calls["embed"] += 1
        return np.stack([fake_vec(t) for t in texts])

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    return calls


def _drain():
    while asyncio.run(knowledge.embed_pending_once()):
        pass


def test_rrf_merges_rankings():
    assert knowledge.rrf([[1, 2, 3], [3, 1]])[:2] == [1, 3]


def test_vectorize_and_hybrid_search(client, vec_on):
    kb = make_kb(client)
    doc = upload(client, kb["id"]).json()
    got = client.get(f"/api/kb/{kb['id']}").json()
    assert got["documents"][0]["vec_chunks"] == 0  # 尚未向量化
    _drain()
    got = client.get(f"/api/kb/{kb['id']}").json()
    assert got["documents"][0]["vec_chunks"] == doc["chunks"]

    # "酒店" 在文档中不存在，关键词检索无法命中，向量召回可以
    hits, mode = asyncio.run(knowledge.search([kb["id"]], "酒店一晚多少钱"))
    assert "向量" in mode and hits and "住宿标准" in hits[0]["content"]

    r = client.post(f"/api/kb/{kb['id']}/search", json={"query": "酒店"}).json()
    assert "向量" in r["mode"] and r["hits"]


def test_model_change_triggers_reembed(client, vec_on, monkeypatch):
    kb = make_kb(client)
    upload(client, kb["id"])
    _drain()
    assert db.kb_pending_count(embeddings.model_id()) == 0
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "fake-embed-v2")
    assert db.kb_pending_count(embeddings.model_id()) > 0
    _drain()
    assert db.kb_pending_count(embeddings.model_id()) == 0


def test_degrades_to_keyword_when_embedding_fails(client, vec_on, monkeypatch):
    kb = make_kb(client)
    upload(client, kb["id"])
    _drain()

    async def broken(texts, query=False):
        raise embeddings.EmbeddingError("down")

    monkeypatch.setattr(embeddings, "embed", broken)
    hits, mode = asyncio.run(knowledge.search([kb["id"]], "住宿标准"))
    assert mode == "关键词" and hits and "600 元" in hits[0]["content"]


def test_rerank_reorders_and_degrades(client, vec_on, monkeypatch):
    monkeypatch.setattr(config, "RERANK_MODEL", "fake-rerank")
    monkeypatch.setattr(config, "RERANK_API_KEY", "k")
    kb = make_kb(client)
    # 每条制度一篇文档，保证有多个候选片段
    for i, para in enumerate(p for p in DOC.split("\n\n") if p.startswith("第")):
        upload(client, kb["id"], f"p{i}.md", para.encode())
    _drain()

    async def fake_rerank(query, docs, top_n):
        # 把含"发票"的片段排第一
        order = sorted(range(len(docs)), key=lambda i: "发票" not in docs[i])
        return [(i, 1.0 - k * 0.1) for k, i in enumerate(order)][:top_n]

    monkeypatch.setattr(embeddings, "rerank", fake_rerank)
    hits, mode = asyncio.run(knowledge.search([kb["id"]], "审批 报销 发票", top_k=3))
    assert mode.endswith("重排") and "发票" in hits[0]["content"]

    async def broken(*a, **k):
        raise embeddings.EmbeddingError("down")

    monkeypatch.setattr(embeddings, "rerank", broken)
    hits, mode = asyncio.run(knowledge.search([kb["id"]], "审批 报销 发票", top_k=3))
    assert "重排" not in mode and hits


def test_embedding_client_parses_and_counts_usage(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "k")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        seen["input"] = body["input"]
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}],
                                         "usage": {"total_tokens": 7}})

    monkeypatch.setattr(embeddings, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    before = db.usage_get("embed")["tokens"]
    m = asyncio.run(embeddings.embed(["a", "b"], query=True))
    assert seen["input"][0].startswith("Instruct:")  # Qwen3 查询指令前缀
    assert np.allclose(m[0], [0.6, 0.8]) and np.allclose(m[1], [0, 1])
    assert db.usage_get("embed")["tokens"] == before + 7


def test_embedding_quota(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "k")
    monkeypatch.setattr(config, "EMBEDDING_DAILY_TOKENS", 0)
    with pytest.raises(embeddings.EmbeddingError):
        asyncio.run(embeddings.embed(["a"]))
