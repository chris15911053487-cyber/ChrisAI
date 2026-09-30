"""管理员「设置」页：鉴权、校验、密钥掩码、即时生效、恢复默认、持久化加载、连通性测试。"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, db, embeddings, llm, main, settings

H = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c
    # 清理，避免影响其他测试
    settings.update({}, list(settings.FIELDS), [])


def field(c, name):
    return next(f for f in c.get("/api/admin/settings", headers=H).json()["fields"] if f["name"] == name)


def test_requires_admin(client):
    assert client.get("/api/admin/settings").status_code == 401
    assert client.put("/api/admin/settings", json={"values": {}}, headers={"X-Admin-Token": "x"}).status_code == 401
    assert client.post("/api/admin/settings/test", json={"target": "llm"}).status_code == 401


def test_update_applies_immediately_and_masks_secret(client):
    r = client.put("/api/admin/settings", headers=H, json={"values": {
        "EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-4B",
        "EMBEDDING_API_KEY": "sk-secret-abcd1234",
        "RERANK_BASE_URL": "https://rerank.example.com/v1/",
    }})
    assert r.status_code == 200, r.text
    assert set(r.json()["changed"]) >= {"EMBEDDING_MODEL", "EMBEDDING_API_KEY"}
    assert config.EMBEDDING_MODEL == "Qwen/Qwen3-Embedding-4B" and config.EMBEDDING_API_KEY == "sk-secret-abcd1234"
    assert config.RERANK_BASE_URL == "https://rerank.example.com/v1"  # 去掉末尾斜杠
    assert embeddings.enabled()

    f = field(client, "EMBEDDING_API_KEY")
    assert f["source"] == "page" and "sk-secret" not in f["value"] and f["value"].endswith("1234")
    assert "sk-secret" not in client.get("/api/admin/settings", headers=H).text

    # 密钥留空 = 不修改
    client.put("/api/admin/settings", headers=H, json={"values": {"EMBEDDING_API_KEY": ""}})
    assert config.EMBEDDING_API_KEY == "sk-secret-abcd1234"
    # 清除
    client.put("/api/admin/settings", headers=H, json={"clear": ["EMBEDDING_API_KEY"]})
    assert config.EMBEDDING_API_KEY == "" and not embeddings.enabled()


def test_validation_is_all_or_nothing(client):
    before = config.EMBEDDING_MODEL
    for bad in ({"EMBEDDING_BASE_URL": "ftp://x"}, {"EMBEDDING_MODEL": ""},
                {"EMBEDDING_MODEL": "a b"}, {"EMBEDDING_DAILY_TOKENS": "-1"}, {"NOT_A_FIELD": "1"},
                {"EMBEDDING_API_KEY": "has space"}):
        r = client.put("/api/admin/settings", headers=H, json={"values": {"EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B", **bad}})
        assert r.status_code == 400, bad
    assert config.EMBEDDING_MODEL == before


def test_reset_restores_default_and_reload(client):
    default = settings._DEFAULTS["EMBEDDING_MODEL"]
    client.put("/api/admin/settings", headers=H, json={"values": {"EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B"}})
    assert db.settings_all()["EMBEDDING_MODEL"] == "Qwen/Qwen3-Embedding-8B"

    # 模拟重启：config 回到 .env 值，再从数据库加载
    config.EMBEDDING_MODEL = default
    settings.load()
    assert config.EMBEDDING_MODEL == "Qwen/Qwen3-Embedding-8B"

    client.put("/api/admin/settings", headers=H, json={"reset": ["EMBEDDING_MODEL"]})
    assert config.EMBEDDING_MODEL == default and "EMBEDDING_MODEL" not in db.settings_all()


def test_rerank_falls_back_to_embedding_credentials(client, monkeypatch):
    monkeypatch.setattr(config, "RERANK_API_KEY", "")
    monkeypatch.setattr(config, "RERANK_BASE_URL", "")
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "ek")
    monkeypatch.setattr(config, "RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    assert embeddings.rerank_enabled()
    assert embeddings._rerank_url() == config.EMBEDDING_BASE_URL and embeddings._rerank_key() == "ek"


def test_connectivity_endpoint(client, monkeypatch):
    async def fake_embed(texts, query=False):
        return np.ones((len(texts), 1024), np.float32)

    async def fake_rerank(q, docs, n):
        return [(0, 0.98), (1, 0.01)]

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    monkeypatch.setattr(embeddings, "rerank", fake_rerank)
    r = client.post("/api/admin/settings/test", headers=H, json={"target": "embed"}).json()
    assert r["ok"] and "1024" in r["detail"]
    r = client.post("/api/admin/settings/test", headers=H, json={"target": "rerank"}).json()
    assert r["ok"] and "第 1" in r["detail"]

    async def broken(*a, **k):
        raise embeddings.EmbeddingError("HTTP 401: invalid key")

    monkeypatch.setattr(embeddings, "embed", broken)
    r = client.post("/api/admin/settings/test", headers=H, json={"target": "embed"}).json()
    assert not r["ok"] and "401" in r["error"]
    assert client.post("/api/admin/settings/test", headers=H, json={"target": "llm"}).status_code == 422
