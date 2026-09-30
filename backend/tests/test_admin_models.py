"""对话模型：管理员增删改 / 默认 / 测试；访客列表不泄露地址和 Key；对话中切换并按会话保存。"""
import json

import pytest
from fastapi.testclient import TestClient

from app import agent, db, llm, main, models

H = {"X-Admin-Token": "test-admin"}
QWEN = {"name": "通义千问", "description": "阿里云", "base_url": "https://dashscope.example.com/compatible-mode/v1/",
        "model": "qwen-plus", "api_key": "sk-qwen-secret-9876"}


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c
    for m in models.all_models():  # 恢复为种子数据
        db.model_delete(m.id)
    models.seed_if_empty()


def test_seeded_from_env(client):
    ms = client.get("/api/admin/models", headers=H).json()["models"]
    assert [m["name"] for m in ms] == ["DeepSeek", "DeepSeek（思考）"]
    fast, think = ms
    assert fast["is_default"] and fast["key_env"] == "DEEPSEEK_API_KEY"
    assert think["extra_body"]["thinking"] == {"type": "enabled"} and think["replay_reasoning"]


def test_admin_crud_and_secrets(client):
    assert client.get("/api/admin/models").status_code == 401
    r = client.post("/api/admin/models", headers=H, json=QWEN)
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["base_url"] == "https://dashscope.example.com/compatible-mode/v1" and m["key_display"].endswith("9876")
    assert "sk-qwen-secret" not in client.get("/api/admin/models", headers=H).text

    # 公开列表：没有地址 / Key
    pub = client.get("/api/models").json()["models"]
    assert any(x["id"] == m["id"] for x in pub)
    assert not any(k in json.dumps(pub) for k in ("dashscope", "sk-qwen", "base_url", "key_env"))

    # 编辑：Key 留空保留
    r = client.put(f"/api/admin/models/{m['id']}", headers=H, json={**QWEN, "api_key": "", "name": "千问 Plus"})
    assert r.json()["model"]["name"] == "千问 Plus" and models.get(m["id"]).api_key == "sk-qwen-secret-9876"
    # 设为默认，默认不能停用
    client.post(f"/api/admin/models/{m['id']}/default", headers=H)
    assert models.default_model().id == m["id"]
    r = client.put(f"/api/admin/models/{m['id']}", headers=H, json={**QWEN, "enabled": False})
    assert r.status_code == 400
    # 删除默认模型 -> 自动选下一个
    assert client.delete(f"/api/admin/models/{m['id']}", headers=H).status_code == 200
    assert models.default_model() is not None and models.default_model().id != m["id"]


@pytest.mark.parametrize("bad", [
    {"base_url": "ftp://x"}, {"model": ""}, {"model": "a b"}, {"name": ""},
    {"key_env": "ADMIN_TOKEN"}, {"key_env": "RUNNER_TOKEN"}, {"temperature": 3},
    {"extra_body": {"model": "x"}}, {"extra_body": {"messages": []}},
])
def test_validation(client, bad):
    assert client.post("/api/admin/models", headers=H, json={**QWEN, **bad}).status_code in (400, 422)


def test_test_endpoint_uses_unsaved_form(client, monkeypatch):
    seen = {}

    async def fake_stream(messages, tools=None, model=None):
        seen["model"], seen["key"], seen["tools"] = model.model, model.key(), bool(tools)
        yield ("tool_calls", [{"id": "1", "function": {"name": "ping", "arguments": "{}"}}])

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    r = client.post("/api/admin/models/test", headers=H, json=QWEN).json()
    assert r["ok"] and r["tools_ok"] and seen == {"model": "qwen-plus", "key": "sk-qwen-secret-9876", "tools": True}

    # 编辑已有模型、未重新输入 Key：使用已保存的 Key
    mid = client.post("/api/admin/models", headers=H, json=QWEN).json()["model"]["id"]
    r = client.post("/api/admin/models/test", headers=H, json={**QWEN, "api_key": "", "id": mid}).json()
    assert r["ok"] and seen["key"] == "sk-qwen-secret-9876"

    async def fail(messages, tools=None, model=None):
        raise llm.LLMError("模型服务返回 401（API Key 无效或无权限）")
        yield

    monkeypatch.setattr(llm, "stream_chat", fail)
    r = client.post("/api/admin/models/test", headers=H, json=QWEN).json()
    assert not r["ok"] and "401" in r["error"]


def _chat(client, used, **body):
    r = client.post("/api/chat", json={"message": "你好", **body})
    evs = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    return evs


def test_chat_switches_model_per_session(client, monkeypatch):
    used = []

    async def fake_stream(messages, tools=None, model=None):
        used.append((model.id, tools is not None, messages[0]["content"]))
        yield ("delta", "好的")

    monkeypatch.setattr(agent.llm, "stream_chat", fake_stream)
    default = models.default_model()
    qwen = models.create({**QWEN, "supports_tools": False})

    evs = _chat(client, used)
    sid = evs[0]["id"]
    assert evs[0]["model"]["id"] == default.id and used[-1][0] == default.id and used[-1][1]

    evs = _chat(client, used, session_id=sid, model_id=qwen.id)
    assert evs[0]["model"]["name"] == "通义千问"
    assert used[-1][0] == qwen.id and not used[-1][1] and "不支持工具调用" in used[-1][2]
    assert db.get_session(sid)["model_id"] == qwen.id
    assert client.get(f"/api/sessions/{sid}/messages").json()["session"]["model_id"] == qwen.id

    # 不带 model_id：沿用会话的模型
    _chat(client, used, session_id=sid)
    assert used[-1][0] == qwen.id

    # 模型被停用：回退到默认模型
    models.update(qwen.id, {**QWEN, "enabled": False})
    evs = _chat(client, used, session_id=sid, model_id=qwen.id)
    assert evs[0]["model"]["id"] == default.id and used[-1][0] == default.id


def test_no_models_available(client):
    for m in models.all_models():
        db.model_delete(m.id)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 503
    assert client.get("/api/health").json()["key_configured"] is False
