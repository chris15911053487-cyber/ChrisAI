"""posts 业务层单元测试：权限、状态机、边界、索引清理、检索分类过滤。

直接测 app.posts + app.db，不走 HTTP（更快更精确）。
conftest 为整个测试会话用同一个临时 DATA_DIR，故这里用独立 owner / 唯一标题避免相互干扰。
"""
import pytest

from app import config, db, posts

CAT0 = config.POST_CATEGORIES[0]
CAT1 = config.POST_CATEGORIES[1]


def _mk(owner, title="标题", body="正文内容", cat=CAT0, tags=None, author="作者"):
    return posts.create(owner, author, title, body, cat, tags or [])


# ---------------- 创建 / 校验 ----------------
def test_create_defaults_to_draft():
    p = _mk("u_t1")
    assert p["status"] == "draft"
    assert p["published_at"] == 0


def test_create_rejects_empty_title():
    with pytest.raises(posts.PostError):
        posts.create("u_t1", "a", "   ", "body", CAT0, [])


def test_create_rejects_invalid_category():
    with pytest.raises(posts.PostError):
        posts.create("u_t1", "a", "标题", "body", "不存在的分类", [])


def test_tags_dedup_and_limit():
    tags = ["a", "a", "A", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    p = _mk("u_t1", title="标签测试", tags=tags)
    # 去重（大小写不敏感）且不超过上限
    assert len(p["tags"]) <= config.POST_MAX_TAGS
    assert p["tags"][0] == "a"
    assert "A" not in p["tags"][1:]  # 'A' 作为 'a' 的重复被过滤


def test_title_too_long_rejected():
    with pytest.raises(posts.PostError):
        posts.create("u_t1", "a", "x" * (config.POST_TITLE_MAX + 1), "b", CAT0, [])


# ---------------- 权限 ----------------
def test_non_owner_cannot_update():
    p = _mk("u_owner")
    with pytest.raises(posts.PostError) as ei:
        posts.update(p["id"], "u_other", "改", "改", CAT0, [])
    assert ei.value.status == 403


def test_non_owner_cannot_delete():
    p = _mk("u_owner")
    with pytest.raises(posts.PostError) as ei:
        posts.delete(p["id"], "u_other")
    assert ei.value.status == 403


def test_admin_can_delete_any():
    p = _mk("u_owner")
    posts.delete(p["id"], owner=None, is_admin=True)
    assert db.post_get(p["id"]) is None


def test_unpublished_not_readable_by_others():
    p = _mk("u_owner")  # draft
    with pytest.raises(posts.PostError) as ei:
        posts.get_readable(p["id"], owner="u_other")
    assert ei.value.status == 404
    # 作者本人可读
    assert posts.get_readable(p["id"], owner="u_owner")["id"] == p["id"]
    # 管理员可读
    assert posts.get_readable(p["id"], owner=None, is_admin=True)["id"] == p["id"]


def test_published_readable_by_anyone():
    p = _mk("u_owner")
    posts.submit(p["id"], "u_owner")
    posts.review(p["id"], approve=True)
    assert posts.get_readable(p["id"], owner=None)["status"] == "published"


# ---------------- 状态机 ----------------
def test_submit_from_draft():
    p = _mk("u_sm")
    r = posts.submit(p["id"], "u_sm")
    assert r["status"] == "pending"


def test_cannot_submit_published():
    p = _mk("u_sm")
    posts.submit(p["id"], "u_sm")
    posts.review(p["id"], approve=True)
    with pytest.raises(posts.PostError) as ei:
        posts.submit(p["id"], "u_sm")
    assert ei.value.status == 409


def test_review_requires_pending():
    p = _mk("u_sm")  # draft，未提交
    with pytest.raises(posts.PostError) as ei:
        posts.review(p["id"], approve=True)
    assert ei.value.status == 409


def test_reject_sets_reason():
    p = _mk("u_sm")
    posts.submit(p["id"], "u_sm")
    r = posts.review(p["id"], approve=False, reason="需补充示例")
    assert r["status"] == "rejected"
    assert r["reject_reason"] == "需补充示例"


def test_edit_published_returns_to_pending_and_clears_index():
    p = _mk("u_edit", title="索引测试", body="独特关键词 鲟鱼检索标记")
    posts.submit(p["id"], "u_edit")
    posts.review(p["id"], approve=True)
    # 已发布：可检索到
    assert any(h["post_id"] == p["id"] for h in posts.search("鲟鱼检索标记"))
    # 编辑 → 退回 pending 且索引清除
    r = posts.update(p["id"], "u_edit", "新标题", "全新内容无旧词", CAT0, [])
    assert r["status"] == "pending"
    assert not any(h["post_id"] == p["id"] for h in posts.search("鲟鱼检索标记"))


def test_unpublish_clears_index():
    p = _mk("u_unp", title="下架测试", body="犰狳独有词")
    posts.submit(p["id"], "u_unp")
    posts.review(p["id"], approve=True)
    assert any(h["post_id"] == p["id"] for h in posts.search("犰狳独有词"))
    r = posts.unpublish(p["id"], "u_unp")
    assert r["status"] == "draft"
    assert not any(h["post_id"] == p["id"] for h in posts.search("犰狳独有词"))


def test_unpublish_requires_published():
    p = _mk("u_unp")  # draft
    with pytest.raises(posts.PostError) as ei:
        posts.unpublish(p["id"], "u_unp")
    assert ei.value.status == 409


# ---------------- 检索 / 分类过滤 ----------------
def _publish(owner, title, body, cat):
    p = _mk(owner, title=title, body=body, cat=cat)
    posts.submit(p["id"], owner)
    posts.review(p["id"], approve=True)
    return p


def test_search_only_hits_published():
    p = _mk("u_se", title="仅草稿", body="犀牛角质层词汇")
    assert posts.search("犀牛角质层词汇") == []  # draft 不命中
    posts.submit(p["id"], "u_se")
    assert posts.search("犀牛角质层词汇") == []  # pending 仍不命中
    posts.review(p["id"], approve=True)
    assert any(h["post_id"] == p["id"] for h in posts.search("犀牛角质层词汇"))


def test_search_category_filter():
    a = _publish("u_cf", "分类A文", "蜜獾专属词", CAT0)
    b = _publish("u_cf", "分类B文", "蜜獾专属词", CAT1)
    all_hits = {h["post_id"] for h in posts.search("蜜獾专属词")}
    assert {a["id"], b["id"]} <= all_hits
    only0 = {h["post_id"] for h in posts.search("蜜獾专属词", categories=[CAT0])}
    assert a["id"] in only0 and b["id"] not in only0


def test_search_invalid_category_returns_empty():
    _publish("u_cf2", "有效文", "穿山甲标记词", CAT0)
    # 传了分类但全不合法 → 返回空（不退化成全量）
    assert posts.search("穿山甲标记词", categories=["不存在的分类"]) == []


def test_search_empty_query():
    assert posts.search("") == []
    assert posts.search("   ") == []


# ---------------- 列表预览（摘要 / 首图） ----------------
def test_preview_from_html_and_md():
    p = posts.create("u_pv", "作者", "预览", "", CAT0, [],
                     body_html='<p>第一段<b>加粗</b></p><img src="javascript:alert(1)">'
                               '<img src="/api/posts/images/u_pv/' + "a" * 32 + '.png"><p>' + "长" * 300 + "</p>")
    assert p["excerpt"].startswith("第一段加粗")
    assert p["excerpt"].endswith("…") and len(p["excerpt"]) <= 141
    assert p["cover"] == "/api/posts/images/u_pv/" + "a" * 32 + ".png"   # 跳过危险协议，取第一张合法图

    r = posts.update(p["id"], "u_pv", "预览", "# 标题\n\n![图](https://example.com/c.png)\n\n**正文**[链接](http://x)", CAT0, [])
    assert r["excerpt"] == "标题 正文链接"
    assert r["cover"] == "https://example.com/c.png"


def test_preview_no_image():
    p = _mk("u_pv2", body="只有文字")
    assert p["excerpt"] == "只有文字" and p["cover"] == ""


def test_migrate_backfills_preview():
    """旧库没有 excerpt/cover/featured_at 列：_migrate 补列并为已有帖子补算摘要和首图。"""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    c.execute("DROP TABLE posts")
    c.execute("CREATE TABLE posts (id TEXT PRIMARY KEY, owner TEXT, author_name TEXT, title TEXT, body_md TEXT, "
              "body_html TEXT NOT NULL DEFAULT '', category TEXT, tags TEXT, status TEXT, reject_reason TEXT, "
              "created_at REAL, updated_at REAL, published_at REAL)")
    c.execute("INSERT INTO posts(id, body_md, body_html) VALUES('old', '旧帖 ![x](https://e.com/i.png)', '')")
    db._migrate(c)
    r = c.execute("SELECT excerpt, cover, featured_at FROM posts WHERE id='old'").fetchone()
    assert (r["excerpt"], r["cover"], r["featured_at"]) == ("旧帖", "https://e.com/i.png", 0)
    db._migrate(c)  # 幂等


# ---------------- 精选 ----------------
def test_feature_requires_published():
    p = _mk("u_ft")
    with pytest.raises(posts.PostError) as ei:
        posts.set_featured(p["id"], True)
    assert ei.value.status == 409
    with pytest.raises(posts.PostError) as ei:
        posts.set_featured("f" * 32, True)
    assert ei.value.status == 404


def test_feature_list_and_cleared_on_unpublish_or_edit():
    p = _publish("u_ft2", "精选测试", "精选正文", CAT0)
    posts.set_featured(p["id"], True)
    assert posts.list_featured()[0]["id"] == p["id"]          # 最近设为精选的排第一
    posts.set_featured(p["id"], False)
    assert all(x["id"] != p["id"] for x in posts.list_featured())

    posts.set_featured(p["id"], True)
    posts.unpublish(p["id"], "u_ft2")                          # 下架 → 取消精选
    assert db.post_get(p["id"])["featured_at"] == 0

    posts.submit(p["id"], "u_ft2")
    posts.review(p["id"], approve=True)
    posts.set_featured(p["id"], True)
    posts.update(p["id"], "u_ft2", "精选测试", "改过的正文", CAT0, [])   # 编辑已发布 → 待审，取消精选
    assert db.post_get(p["id"])["featured_at"] == 0
    assert all(x["id"] != p["id"] for x in posts.list_featured())


def test_featured_limit():
    ids = [_publish("u_ft3", f"精选上限{i}", "正文", CAT0)["id"] for i in range(config.POST_FEATURED_MAX + 1)]
    for pid in ids:
        posts.set_featured(pid, True)
    got = [x["id"] for x in posts.list_featured()]
    assert len(got) == config.POST_FEATURED_MAX
    assert got == list(reversed(ids))[:config.POST_FEATURED_MAX]
    for pid in ids:
        posts.set_featured(pid, False)   # 还原，避免影响其他测试
