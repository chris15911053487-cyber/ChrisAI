from fastapi.testclient import TestClient
from app import main, config

ADMIN = {"X-Admin-Token": "test-admin"}


def test_posts_end_to_end():
    with TestClient(main.app) as c:
        cat = config.POST_CATEGORIES[0]

        # 匿名不能发帖
        r = c.post("/api/posts", json={"title": "x", "body_md": "y", "category": cat})
        assert r.status_code == 401, r.text

        # 公开分类端点（无需登录）
        r = c.get("/api/posts/categories")
        assert r.status_code == 200
        assert cat in r.json()["categories"]

        # 注册并登录（设置 cookie）
        r = c.post("/api/auth/register", json={"username": "writer01", "password": "secret123"})
        assert r.status_code == 200, r.text

        # 发草稿
        r = c.post("/api/posts", json={
            "title": "SAP B1 批次管理", "body_md": "介绍 SAP Business One 批次与盘点流程。",
            "category": cat, "tags": ["SAP", "库存", "SAP"]})
        assert r.status_code == 200, r.text
        p = r.json()
        pid = p["id"]
        assert p["status"] == "draft"
        assert p["tags"] == ["SAP", "库存"]  # 去重

        # 非法分类
        r = c.post("/api/posts", json={"title": "a", "body_md": "b", "category": "不存在"})
        assert r.status_code == 400, r.text

        # 未发布不在公开列表
        assert not any(x["id"] == pid for x in c.get("/api/posts").json()["posts"])

        # 提交审核
        r = c.post(f"/api/posts/{pid}/submit")
        assert r.status_code == 200 and r.json()["status"] == "pending"

        # 管理员待审列表
        r = c.get("/api/admin/posts?status=pending", headers=ADMIN)
        assert r.status_code == 200 and any(x["id"] == pid for x in r.json()["posts"])

        # 无 admin token 拒绝
        assert c.get("/api/admin/posts").status_code == 401

        # 审核通过
        r = c.post(f"/api/admin/posts/{pid}/review", headers=ADMIN, json={"approve": True})
        assert r.status_code == 200 and r.json()["status"] == "published"

        # 公开列表与分类过滤（针对本帖，不假设全局数量）
        assert any(x["id"] == pid for x in c.get("/api/posts").json()["posts"])
        assert any(x["id"] == pid for x in c.get(f"/api/posts?category={cat}").json()["posts"])

        # 详情（含正文）
        r = c.get(f"/api/posts/{pid}")
        assert r.status_code == 200 and "批次" in r.json()["body_md"]
        assert r.json().get("mine") is True  # 作者本人

        # 编辑已发布 → 退回 pending
        r = c.put(f"/api/posts/{pid}", json={"title": "改标题", "body_md": "新正文", "category": cat, "tags": []})
        assert r.status_code == 200 and r.json()["status"] == "pending"
        assert not any(x["id"] == pid for x in c.get("/api/posts").json()["posts"])  # 撤下线上

        # 会话帖子分类选择
        sid = c.post("/api/sessions").json()["id"]
        r = c.put(f"/api/sessions/{sid}/post-cats", json={"categories": [cat, "无效分类"]})
        assert r.status_code == 200 and r.json()["post_cats"] == [cat] and r.json()["enabled"] is True
        # 启用全部分类
        r = c.put(f"/api/sessions/{sid}/post-cats", json={"categories": ["*"]})
        assert r.json()["post_cats"] == ["*"] and r.json()["enabled"] is True
        # 清空 = 未启用
        r = c.put(f"/api/sessions/{sid}/post-cats", json={"categories": []})
        assert r.json()["post_cats"] == [] and r.json()["enabled"] is False
        # 重新设回单分类用于回显校验
        c.put(f"/api/sessions/{sid}/post-cats", json={"categories": [cat]})
        # 会话详情回显 post_cats
        r = c.get(f"/api/sessions/{sid}/messages")
        assert r.json()["session"]["post_cats"] == [cat]

        # 删除
        assert c.delete(f"/api/posts/{pid}").status_code == 200
        assert c.get(f"/api/posts/{pid}").status_code == 404

    print("POSTS API SMOKE OK")


def test_posts_agent_integration():
    import asyncio
    from app import agent, tools, config

    cat = config.POST_CATEGORIES[0]

    # resolve_post_source 三态
    assert agent.resolve_post_source([]) == (False, None)
    assert agent.resolve_post_source(None) == (False, None)
    assert agent.resolve_post_source(["*"]) == (True, None)
    assert agent.resolve_post_source([cat]) == (True, [cat])
    assert agent.resolve_post_source(["不存在的分类"]) == (False, None)  # 过滤后为空 → 未启用

    # tools_for：未启用不挂帖子工具；启用则挂
    names = lambda defs: {d["function"]["name"] for d in defs}
    assert "search_posts" not in names(tools.tools_for([], post_on=False))
    assert "search_posts" in names(tools.tools_for([], post_on=True))
    assert "read_post" in names(tools.tools_for([], post_on=True))

    # post_prompt 文案随范围变化
    assert agent.post_prompt([]) == ""
    assert "全部分类" in agent.post_prompt(["*"])
    assert cat in agent.post_prompt([cat])

    # 准备一篇已发布帖子（直接走业务层，避免再走 HTTP）
    from app import posts
    owner = "u_900"
    p = posts.create(owner, "作者", "向量检索入门", "本文讲解向量检索与关键词检索的区别与融合。", cat, ["检索"])
    posts.submit(p["id"], owner)
    posts.review(p["id"], approve=True)

    async def run():
        # 启用全部分类：search_posts 命中
        r = await tools.execute("search_posts", '{"query":"向量检索"}', "u_900", "sid-x",
                                kb_ids=[], post_cats=None)
        assert r.ok and "向量检索入门" in r.content, r.content
        # read_post 读取片段
        pid = r.detail["hits"] and p["id"]
        r2 = await tools.execute("read_post", f'{{"post_id":"{p["id"]}"}}', "u_900", "sid-x", post_cats=None)
        assert r2.ok and "向量检索入门" in r2.content
        # 限定到不含该帖的分类 → 无结果
        other = config.POST_CATEGORIES[1]
        r3 = await tools.execute("search_posts", '{"query":"向量检索"}', "u_900", "sid-x",
                                 kb_ids=[], post_cats=[other])
        assert "无结果" in r3.summary, r3.summary
        # read_post 不存在的帖子 → 友好失败
        r4 = await tools.execute("read_post", '{"post_id":"' + "f" * 32 + '"}', "u_900", "sid-x", post_cats=None)
        assert not r4.ok and "帖子" in r4.summary

    asyncio.run(run())
    print("POSTS AGENT INTEGRATION OK")
