"""社区帖子业务层。

设计要点（与知识库解耦，共用检索基建）：
- 登录用户发帖；状态机 draft → pending → published / rejected，先审核后公开。
- 分类为管理员预设的固定列表（config.POST_CATEGORIES），标签由作者自由填写。
- 检索：发布时把正文切片并建 FTS5 关键词索引（复用 knowledge.chunk_text / tokenize），
  独立于知识库的 kb_fts。对话侧用 search() 按会话选中的分类过滤检索（空=全部分类）。
- 权限：作者可管理自己的帖子；published 的帖子所有人可读；审核与下架由管理员执行。
- 编辑已发布的帖子会退回 pending 并清除线上索引，避免对话检索到未复审的新内容。
"""
import logging
import time
from typing import Optional
from . import config, db
from .htmlsanitize import html_to_text, sanitize_html
from .knowledge import build_match, chunk_text, tokenize

logger = logging.getLogger("agent.posts")

# 状态常量
DRAFT, PENDING, PUBLISHED, REJECTED = "draft", "pending", "published", "rejected"
_STATUSES = {DRAFT, PENDING, PUBLISHED, REJECTED}


class PostError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------- 校验 ----------------
def categories() -> list[str]:
    """管理员预设的固定分类列表。"""
    return list(config.POST_CATEGORIES)


def _validate_category(category: str) -> str:
    category = (category or "").strip()
    if category not in config.POST_CATEGORIES:
        raise PostError("分类无效，请从预设分类中选择")
    return category


def _validate_tags(tags: Optional[list]) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise PostError("标签格式不正确")
    out, seen = [], set()
    for t in tags:
        if not isinstance(t, str):
            raise PostError("标签格式不正确")
        t = t.strip()
        if not t:
            continue
        if len(t) > config.POST_TAG_MAX:
            raise PostError(f"单个标签长度不能超过 {config.POST_TAG_MAX} 字")
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= config.POST_MAX_TAGS:
            break
    return out


def _validate_content(title: str, body_md: str) -> tuple[str, str]:
    title = (title or "").strip()
    if not title:
        raise PostError("标题不能为空")
    if len(title) > config.POST_TITLE_MAX:
        raise PostError(f"标题长度不能超过 {config.POST_TITLE_MAX} 字")
    body_md = body_md or ""
    if len(body_md) > config.POST_BODY_MAX_CHARS:
        raise PostError(f"正文长度不能超过 {config.POST_BODY_MAX_CHARS} 字")
    return title, body_md


def _clean_body_html(body_html: Optional[str]) -> str:
    """服务端净化富文本正文（白名单），并校验长度上限。"""
    raw = body_html or ""
    if len(raw) > config.POST_HTML_MAX_CHARS:
        raise PostError(f"正文内容过长（上限 {config.POST_HTML_MAX_CHARS} 字）")
    return sanitize_html(raw)


# ---------------- 读取 / 权限 ----------------
def get_readable(pid: str, owner: Optional[str], is_admin: bool = False) -> dict:
    """读取帖子：published 任何人可读；其他状态仅作者或管理员可读。"""
    post = db.post_get(pid)
    if not post:
        raise PostError("帖子不存在", 404)
    if post["status"] == PUBLISHED or is_admin or (owner and post["owner"] == owner):
        return post
    raise PostError("帖子不存在", 404)


def _get_owned(pid: str, owner: str) -> dict:
    post = db.post_get(pid)
    if not post:
        raise PostError("帖子不存在", 404)
    if post["owner"] != owner:
        raise PostError("无权操作该帖子", 403)
    return post


def list_published(category: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
    cat = None
    if category:
        cat = _validate_category(category)
    return db.post_list_published(category=cat, limit=limit, offset=offset)


def list_mine(owner: str) -> list[dict]:
    return db.post_list_by_owner(owner)


def published_categories() -> list[dict]:
    return db.post_published_categories()


# ---------------- 创建 / 编辑 / 删除 ----------------
def create(owner: str, author_name: str, title: str, body_md: str,
           category: str, tags: Optional[list] = None, body_html: Optional[str] = None) -> dict:
    if db.post_count_owned(owner) >= config.POST_MAX_PER_USER:
        raise PostError(f"帖子数量已达上限（{config.POST_MAX_PER_USER}）", 429)
    title, body_md = _validate_content(title, body_md)
    cat = _validate_category(category)
    tag_list = _validate_tags(tags)
    clean_html = _clean_body_html(body_html)
    return db.post_create(owner, author_name or "", title, body_md, cat, tag_list, body_html=clean_html)


def update(pid: str, owner: str, title: str, body_md: str,
           category: str, tags: Optional[list] = None, body_html: Optional[str] = None) -> dict:
    """编辑帖子。若原为 published，编辑后退回 pending 并清除线上索引（需重新审核）。"""
    post = _get_owned(pid, owner)
    title, body_md = _validate_content(title, body_md)
    cat = _validate_category(category)
    tag_list = _validate_tags(tags)
    clean_html = _clean_body_html(body_html)
    fields = {"title": title, "body_md": body_md, "body_html": clean_html,
              "category": cat, "tags": tag_list}
    if post["status"] in (PUBLISHED, REJECTED):
        # 已上线或曾被驳回的，编辑后重新进入待审；撤下线上内容
        fields["status"] = PENDING
        fields["reject_reason"] = ""
        db.post_clear_index(pid)
    return db.post_update_fields(pid, fields)


def delete(pid: str, owner: Optional[str], is_admin: bool = False) -> None:
    post = db.post_get(pid)
    if not post:
        raise PostError("帖子不存在", 404)
    if not is_admin and post["owner"] != owner:
        raise PostError("无权删除该帖子", 403)
    db.post_delete(pid)  # post_chunks 外键级联，post_fts 触发器清理


# ---------------- 状态机 ----------------
def submit(pid: str, owner: str) -> dict:
    """提交审核：draft / rejected → pending。"""
    post = _get_owned(pid, owner)
    if post["status"] == PENDING:
        return post
    if post["status"] == PUBLISHED:
        raise PostError("帖子已发布，无需再次提交", 409)
    if not (post["title"] or "").strip():
        raise PostError("标题不能为空")
    return db.post_update_fields(pid, {"status": PENDING, "reject_reason": ""})


def review(pid: str, approve: bool, reason: str = "") -> dict:
    """管理员审核。approve=True → published 并建索引；False → rejected 带原因。"""
    post = db.post_get(pid)
    if not post:
        raise PostError("帖子不存在", 404)
    if post["status"] != PENDING:
        raise PostError("只能审核待审状态的帖子", 409)
    if approve:
        _build_index(pid, post["title"], post.get("body_md") or "", post.get("body_html") or "")
        return db.post_update_fields(pid, {"status": PUBLISHED, "reject_reason": "",
                                           "published_at": time.time()})
    return db.post_update_fields(pid, {"status": REJECTED, "reject_reason": (reason or "").strip()[:500]})


def unpublish(pid: str, owner: Optional[str], is_admin: bool = False) -> dict:
    """下架已发布帖子：published → draft，并清除线上索引。作者或管理员可操作。"""
    post = db.post_get(pid)
    if not post:
        raise PostError("帖子不存在", 404)
    if not is_admin and post["owner"] != owner:
        raise PostError("无权操作该帖子", 403)
    if post["status"] != PUBLISHED:
        raise PostError("只能下架已发布的帖子", 409)
    db.post_clear_index(pid)
    return db.post_update_fields(pid, {"status": DRAFT})


# ---------------- 索引 ----------------
def _build_index(pid: str, title: str, body_md: str, body_html: str = "") -> None:
    """把标题 + 正文切片并建 FTS5 关键词索引（复用知识库的切片与分词）。
    优先用 body_html 抽取的纯文本（新帖），否则回退 body_md（旧帖）。"""
    body = html_to_text(body_html) if body_html else (body_md or "")
    text = (title or "").strip()
    if body:
        text = f"{text}\n\n{body}" if text else body
    chunks = chunk_text(text)
    rows = [(c, " ".join(tokenize(c))) for c in chunks]
    db.post_index(pid, rows)


# ---------------- 对话检索 ----------------
def search(query: str, categories: Optional[list[str]] = None,
           limit: Optional[int] = None) -> list[dict]:
    """供 agent 工具调用：在已发布帖子中按关键词检索。
    categories 为空/None 表示全部分类；只保留预设分类内的取值。"""
    match = build_match(query)
    if not match:
        return []
    cats = None
    if categories:
        cats = [c for c in categories if c in config.POST_CATEGORIES]
        if not cats:  # 传了分类但全不合法 → 无结果，避免退化成全量
            return []
    return db.post_search(match, cats, limit or config.POST_SEARCH_LIMIT)


def read_context(pid: str, seq: int, span: int = 2) -> list[dict]:
    """读取某帖指定片段附近的上下文（供对话补充上下文）。"""
    start = max(0, seq - span)
    return db.post_chunks_range(pid, start, span * 2 + 1)
