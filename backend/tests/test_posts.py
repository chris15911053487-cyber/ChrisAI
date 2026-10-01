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
