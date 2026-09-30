import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import agent, config, db, knowledge, main, tools

DOC = (
    "# 差旅报销制度\n\n"
    "第一条 员工出差需提前在 SAP B1 中提交出差申请，由部门经理审批。\n\n"
    "第二条 住宿标准：一线城市每晚不超过 600 元，其他城市每晚不超过 400 元。\n\n"
    "第三条 报销须在出差结束后 15 个工作日内提交，附发票原件。\n"
)


def new_client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture()
def client():
    with new_client() as c:
        yield c


def make_kb(c, name="制度库", desc="公司制度"):
    r = c.post("/api/kb", json={"name": name, "description": desc})
    assert r.status_code == 200, r.text
    return r.json()


def upload(c, kid, name="travel.md", data=DOC.encode()):
    return c.post(f"/api/kb/{kid}/documents", files={"file": (name, data)})


# ---------------- 纯函数 ----------------
def test_tokenize_cjk_bigrams_and_words():
    assert knowledge.tokenize("SAP B1 销售订单") == ["sap", "b1", "销售", "售订", "订单"]
    assert knowledge.tokenize("ＡＢＣ") == ["abc"]  # 全角归一化
    assert knowledge.tokenize("审") == ["审"]


def test_build_match_quotes_terms():
    m = knowledge.build_match('住宿" OR x')
    assert '"' not in m.replace('"住宿"', "").replace('"or"', "").replace('"x"', "").replace(" OR ", "")
    assert knowledge.build_match("!!!") == ""


def test_chunk_text_limits_and_keeps_content():
    text = "标题\n\n" + "这是一句很长的制度说明。" * 200
    chunks = knowledge.chunk_text(text, limit=300, overlap=40)
    assert all(len(c) <= 300 for c in chunks)
    assert chunks[0].startswith("标题")  # 短标题并入下一段
    assert "".join(chunks).count("制度说明") >= 200


def test_decode_gbk():
    assert knowledge._decode("中文内容".encode("gbk")) == "中文内容"


def test_html_to_text_strips_script():
    t = knowledge._html_to_text("<p>你好</p><script>alert(1)</script><b>世界</b>")
    assert "alert" not in t and "你好" in t and "世界" in t


# ---------------- 接口 ----------------
def test_kb_crud_upload_search(client):
    kb = make_kb(client)
    assert kb["editable"] and kb["visibility"] == "private"

    r = upload(client, kb["id"])
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["chunks"] >= 1 and doc["filename"] == "travel.md"

    r = client.get(f"/api/kb/{kb['id']}")
    assert r.json()["docs"] == 1 and len(r.json()["documents"]) == 1

    r = client.post(f"/api/kb/{kb['id']}/search", json={"query": "住宿标准是多少"})
    hits = r.json()["hits"]
    assert hits and "600 元" in hits[0]["content"]

    r = client.get(f"/api/kb/{kb['id']}/documents/{doc['id']}/raw")
    assert r.content == DOC.encode()

    r = client.get(f"/api/kb/{kb['id']}/documents/{doc['id']}/chunks")
    assert r.json()["chunks"][0]["seq"] == 0

    r = client.patch(f"/api/kb/{kb['id']}", json={"name": "新名字"})
    assert r.json()["name"] == "新名字"

    assert client.delete(f"/api/kb/{kb['id']}/documents/{doc['id']}").status_code == 200
    assert client.post(f"/api/kb/{kb['id']}/search", json={"query": "住宿"}).json()["hits"] == []
    assert not (config.KB_DIR / kb["id"] / f"{doc['id']}.md").exists()

    assert client.delete(f"/api/kb/{kb['id']}").status_code == 200
    assert client.get(f"/api/kb/{kb['id']}").status_code == 404


def test_rejects_bad_files(client):
    kb = make_kb(client)
    assert upload(client, kb["id"], "a.exe", b"MZ").status_code == 400
    assert upload(client, kb["id"], "empty.txt", b"").status_code == 400
    assert upload(client, kb["id"], "blank.txt", b"   \n\n ").status_code == 422
    # 路径穿越的文件名被清洗
    r = upload(client, kb["id"], "../../etc/passwd.txt", b"hello world")
    assert r.status_code == 200 and r.json()["filename"] == "passwd.txt"


def test_isolation_between_visitors():
    with new_client() as a, new_client() as b:
        kb = make_kb(a)
        upload(a, kb["id"])
        assert b.get(f"/api/kb/{kb['id']}").status_code == 404
        assert b.post(f"/api/kb/{kb['id']}/search", json={"query": "住宿"}).status_code == 404
        assert upload(b, kb["id"]).status_code == 404
        assert b.delete(f"/api/kb/{kb['id']}").status_code == 404
        assert all(k["id"] != kb["id"] for k in b.get("/api/kb").json()["kbs"])

        # 别人的知识库不能被选入自己的会话
        s = b.post("/api/sessions").json()
        r = b.put(f"/api/sessions/{s['id']}/kbs", json={"kb_ids": [kb["id"]]})
        assert r.json()["kb_ids"] == []

        # 管理员公开后：可见、可检索、只读
        h = {"X-Admin-Token": "test-admin"}
        assert b.post(f"/api/admin/kb/{kb['id']}/visibility", json={"visibility": "public"}).status_code == 401
        assert a.post(f"/api/admin/kb/{kb['id']}/visibility", json={"visibility": "public"}, headers=h).status_code == 200
        got = b.get(f"/api/kb/{kb['id']}").json()
        assert got["visibility"] == "public" and got["editable"] is False
        assert b.post(f"/api/kb/{kb['id']}/search", json={"query": "住宿"}).json()["hits"]
        assert upload(b, kb["id"]).status_code == 403
        assert b.delete(f"/api/kb/{kb['id']}").status_code == 403
        r = b.put(f"/api/sessions/{s['id']}/kbs", json={"kb_ids": [kb["id"]]})
        assert r.json()["kb_ids"] == [kb["id"]]
        assert b.get(f"/api/sessions/{s['id']}/messages").json()["session"]["kb_ids"] == [kb["id"]]


def test_kb_count_limit(client, monkeypatch):
    monkeypatch.setattr(config, "KB_MAX_PER_VISITOR", 2)
    make_kb(client, "a")
    make_kb(client, "b")
    assert client.post("/api/kb", json={"name": "c"}).status_code == 403


def test_binary_extract_uses_sandbox(client, monkeypatch):
    seen = {}

    async def fake(data, ext):
        seen["ext"] = ext
        return "[第 1 页]\n合同付款条件：验收后 30 天内付款。"

    monkeypatch.setattr(knowledge, "_extract_in_sandbox", fake)
    kb = make_kb(client)
    r = upload(client, kb["id"], "合同.pdf", b"%PDF-1.4 fake")
    assert r.status_code == 200, r.text
    assert seen["ext"] == ".pdf"
    assert client.post(f"/api/kb/{kb['id']}/search", json={"query": "付款条件"}).json()["hits"]


# ---------------- Agent 工具 ----------------
def test_tools_only_offered_when_selected():
    names = lambda ts: {t["function"]["name"] for t in ts}
    assert "search_knowledge" not in names(tools.tools_for([]))
    assert {"search_knowledge", "read_knowledge"} <= names(tools.tools_for(["x" * 32]))


def test_search_and_read_tools(client):
    kb = make_kb(client)
    upload(client, kb["id"])
    ids = [kb["id"]]

    res = asyncio.run(tools.execute("search_knowledge", json.dumps({"query": "报销期限"}), "v", "s", ids))
    assert res.ok and "15 个工作日" in res.content
    assert res.detail["hits"][0]["file"] == "travel.md"
    doc_id = res.content.split("doc_id=")[1].split("｜")[0]

    res = asyncio.run(tools.execute("read_knowledge", json.dumps({"doc_id": doc_id}), "v", "s", ids))
    assert res.ok and "差旅报销制度" in res.content

    # 不在所选知识库中的文档不可读
    res = asyncio.run(tools.execute("read_knowledge", json.dumps({"doc_id": doc_id}), "v", "s", ["0" * 32]))
    assert not res.ok
    # 未选择知识库
    res = asyncio.run(tools.execute("search_knowledge", json.dumps({"query": "x"}), "v", "s", []))
    assert not res.ok


def test_chat_uses_selected_kb(client, monkeypatch):
    """模拟模型：第一轮调用 search_knowledge，第二轮给出回答。"""
    kb = make_kb(client)
    upload(client, kb["id"])
    calls = []

    async def fake_stream(messages, tool_defs=None, model=None):
        calls.append((messages, tool_defs))
        if len(calls) == 1:
            yield ("tool_calls", [{"id": "c1", "type": "function",
                                   "function": {"name": "search_knowledge", "arguments": '{"query":"住宿标准"}'}}])
        else:
            yield ("delta", "一线城市每晚不超过 600 元【travel.md】")
        yield ("finish", "stop")

    monkeypatch.setattr(agent.llm, "stream_chat", fake_stream)
    r = client.post("/api/chat", json={"message": "住宿标准？", "kb_ids": [kb["id"]]})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "tool_result" in types and types[-1] == "done", events
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["ok"] and tr["detail"]["hits"]

    sys_prompt = calls[0][0][0]["content"]
    assert "制度库" in sys_prompt and "search_knowledge" in sys_prompt
    assert any(t["function"]["name"] == "search_knowledge" for t in calls[0][1])
    tool_msg = next(m for m in calls[1][0] if m["role"] == "tool")
    assert "600 元" in tool_msg["content"]

    sid = next(e for e in events if e["type"] == "session")["id"]
    assert db.get_session(sid)["kb_ids"] == [kb["id"]]
