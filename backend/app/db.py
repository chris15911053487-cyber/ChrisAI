"""SQLite 持久化：访客、会话、消息、文件、用量、卡片、知识库。"""
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import config

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    visitor_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_visitor ON sessions(visitor_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    meta TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS files (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, path)
);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,          -- tools | works | courses
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    tag TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_section ON cards(section, sort, id);
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,             -- 创建者访客 ID
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private',   -- private | public（仅管理员可设为 public）
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS models (      -- 对话模型（任意 OpenAI 兼容 Chat Completions 接口）
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,              -- 显示名，访客在对话框中看到
    description TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,             -- 请求里的 model 字段
    api_key TEXT NOT NULL DEFAULT '',
    key_env TEXT NOT NULL DEFAULT '',  -- 或从环境变量读取 Key（如 DEEPSEEK_API_KEY）
    temperature REAL,
    max_tokens INTEGER,
    extra_body TEXT NOT NULL DEFAULT '{}',   -- 合并进请求体的厂商参数（如 DeepSeek thinking）
    supports_tools INTEGER NOT NULL DEFAULT 1,
    replay_reasoning INTEGER NOT NULL DEFAULT 0,  -- 历史消息回传 reasoning_content（DeepSeek 思考模式 + 工具）
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (   -- 管理员在「设置」页修改的运行时配置，覆盖 .env
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_owner ON knowledge_bases(owner, updated_at DESC);
CREATE TABLE IF NOT EXISTS kb_documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,           -- 原始文件字节数
    chars INTEGER NOT NULL,          -- 解析后字符数
    chunks INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kbdoc_kb ON kb_documents(kb_id, created_at);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kbchunk_doc ON kb_chunks(doc_id, seq);
-- 全文索引：tokens 为预分词结果（中文二元组 + 英文单词），rowid = kb_chunks.id
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(tokens);
CREATE TRIGGER IF NOT EXISTS kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN
    DELETE FROM kb_fts WHERE rowid = old.id;
END;
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        _migrate(c)
        _conn = c
    return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """为旧库补齐新增列（幂等）。"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(cards)").fetchall()}
    if "category" not in cols:
        c.execute("ALTER TABLE cards ADD COLUMN category TEXT NOT NULL DEFAULT ''")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
    if "kb_ids" not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN kb_ids TEXT NOT NULL DEFAULT '[]'")
    if "model_id" not in cols:  # 会话使用的对话模型（空 = 默认模型）
        c.execute("ALTER TABLE sessions ADD COLUMN model_id TEXT NOT NULL DEFAULT ''")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(kb_chunks)").fetchall()}
    if "embedding" not in cols:  # float32 L2 归一化向量；embed_model 记录生成它的模型，换模型后自动重算
        c.execute("ALTER TABLE kb_chunks ADD COLUMN embedding BLOB")
        c.execute("ALTER TABLE kb_chunks ADD COLUMN embed_model TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kbchunk_embed ON kb_chunks(embed_model)")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    if "reasoning" not in cols:  # 思考模式的 reasoning_content（带工具调用时必须回传给模型）
        c.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")


def _exec(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, args)


def _all(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def _one(sql: str, args: tuple = ()) -> Optional[dict]:
    with _lock:
        r = conn().execute(sql, args).fetchone()
        return dict(r) if r else None


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- visitors ----------
def touch_visitor(vid: str) -> None:
    now = time.time()
    _exec(
        "INSERT INTO visitors(id, created_at, last_seen) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
        (vid, now, now),
    )


# ---------- sessions ----------
def create_session(vid: str, title: str = "新对话") -> dict:
    sid = uuid.uuid4().hex
    now = time.time()
    _exec(
        "INSERT INTO sessions(id, visitor_id, title, created_at, updated_at) VALUES(?,?,?,?,?)",
        (sid, vid, title, now, now),
    )
    return get_session(sid)


def _session_row(r: Optional[dict]) -> Optional[dict]:
    if r is not None:
        try:
            ids = json.loads(r.get("kb_ids") or "[]")
        except ValueError:
            ids = []
        r["kb_ids"] = [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []
    return r


def get_session(sid: str) -> Optional[dict]:
    return _session_row(_one("SELECT * FROM sessions WHERE id=?", (sid,)))


def get_owned_session(sid: str, vid: str) -> Optional[dict]:
    return _session_row(_one("SELECT * FROM sessions WHERE id=? AND visitor_id=?", (sid, vid)))


def set_session_model(sid: str, model_id: str) -> None:
    _exec("UPDATE sessions SET model_id=? WHERE id=?", (model_id, sid))


def set_session_kbs(sid: str, kb_ids: list[str]) -> None:
    _exec("UPDATE sessions SET kb_ids=? WHERE id=?", (json.dumps(kb_ids), sid))


def list_sessions(vid: str, limit: int = 100) -> list[dict]:
    return _all(
        "SELECT id, title, created_at, updated_at FROM sessions WHERE visitor_id=? "
        "ORDER BY updated_at DESC LIMIT ?",
        (vid, limit),
    )


def rename_session(sid: str, title: str) -> None:
    _exec("UPDATE sessions SET title=? WHERE id=?", (title[:80], sid))


def touch_session(sid: str) -> None:
    _exec("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))


def delete_session(sid: str) -> None:
    _exec("DELETE FROM sessions WHERE id=?", (sid,))


# ---------- messages ----------
def add_message(
    sid: str,
    role: str,
    content: Optional[str] = None,
    tool_calls: Optional[list] = None,
    tool_call_id: Optional[str] = None,
    meta: Optional[dict] = None,
    reasoning: Optional[str] = None,
) -> int:
    cur = _exec(
        "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id, meta, created_at, reasoning) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            sid,
            role,
            content,
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
            tool_call_id,
            json.dumps(meta, ensure_ascii=False) if meta else None,
            time.time(),
            reasoning or None,
        ),
    )
    return cur.lastrowid


def get_messages(sid: str) -> list[dict]:
    rows = _all("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))
    for r in rows:
        r["tool_calls"] = json.loads(r["tool_calls"]) if r["tool_calls"] else None
        r["meta"] = json.loads(r["meta"]) if r["meta"] else None
    return rows


# ---------- files ----------
def upsert_file(sid: str, path: str, size: int) -> None:
    _exec(
        "INSERT INTO files(session_id, path, size, created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(session_id, path) DO UPDATE SET size=excluded.size, created_at=excluded.created_at",
        (sid, path, size, time.time()),
    )


def list_files(sid: str) -> list[dict]:
    return _all("SELECT path, size, created_at FROM files WHERE session_id=? ORDER BY created_at", (sid,))


def delete_file_record(sid: str, path: str) -> None:
    _exec("DELETE FROM files WHERE session_id=? AND path=?", (sid, path))


def expired_files(before_ts: float) -> list[dict]:
    return _all("SELECT session_id, path FROM files WHERE created_at < ?", (before_ts,))


# ---------- usage ----------
def usage_get(key: str, day: Optional[str] = None) -> dict:
    r = _one("SELECT turns, tokens FROM usage WHERE day=? AND key=?", (day or today(), key))
    return r or {"turns": 0, "tokens": 0}


def usage_add(key: str, turns: int = 0, tokens: int = 0) -> None:
    _exec(
        "INSERT INTO usage(day, key, turns, tokens) VALUES(?,?,?,?) "
        "ON CONFLICT(day, key) DO UPDATE SET turns=turns+excluded.turns, tokens=tokens+excluded.tokens",
        (today(), key, turns, tokens),
    )


def stats() -> dict[str, Any]:
    return {
        "visitors": _one("SELECT COUNT(*) n FROM visitors")["n"],
        "sessions": _one("SELECT COUNT(*) n FROM sessions")["n"],
        "today": usage_get("global"),
        "embed_tokens_today": usage_get("embed")["tokens"],
    }


# ---------- cards（首页卡片：AI工具 / AI作品 / AI课程） ----------
CARD_SECTIONS = ("tools", "works", "courses")


def list_cards(section: Optional[str] = None, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM cards"
    conds, args = [], []
    if section:
        conds.append("section=?")
        args.append(section)
    if enabled_only:
        conds.append("enabled=1")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY section, sort, id"
    rows = _all(sql, tuple(args))
    for r in rows:
        r["enabled"] = bool(r["enabled"])
    return rows


def get_card(card_id: int) -> Optional[dict]:
    r = _one("SELECT * FROM cards WHERE id=?", (card_id,))
    if r:
        r["enabled"] = bool(r["enabled"])
    return r


def create_card(
    section: str,
    title: str,
    description: str = "",
    url: str = "",
    icon: str = "",
    tag: str = "",
    sort: int = 0,
    enabled: bool = True,
    category: str = "",
) -> dict:
    now = time.time()
    cur = _exec(
        "INSERT INTO cards(section, title, description, url, icon, tag, category, sort, enabled, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (section, title, description, url, icon, tag, category, sort, 1 if enabled else 0, now, now),
    )
    return get_card(cur.lastrowid)


def update_card(card_id: int, fields: dict) -> Optional[dict]:
    allowed = ("section", "title", "description", "url", "icon", "tag", "category", "sort", "enabled")
    sets, args = [], []
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return get_card(card_id)
    sets.append("updated_at=?")
    args.append(time.time())
    args.append(card_id)
    _exec(f"UPDATE cards SET {', '.join(sets)} WHERE id=?", tuple(args))
    return get_card(card_id)


def delete_card(card_id: int) -> None:
    _exec("DELETE FROM cards WHERE id=?", (card_id,))


# ---------- 知识库 ----------
_KB_COLS = ("k.id, k.owner, k.name, k.description, k.visibility, k.created_at, k.updated_at, "
            "(SELECT COUNT(*) FROM kb_documents d WHERE d.kb_id=k.id) AS docs, "
            "(SELECT COALESCE(SUM(d.chars),0) FROM kb_documents d WHERE d.kb_id=k.id) AS chars")


def kb_create(owner: str, name: str, description: str) -> dict:
    kid = uuid.uuid4().hex
    now = time.time()
    _exec(
        "INSERT INTO knowledge_bases(id, owner, name, description, visibility, created_at, updated_at) "
        "VALUES(?,?,?,?, 'private', ?, ?)",
        (kid, owner, name, description, now, now),
    )
    return kb_get(kid)


def kb_get(kid: str) -> Optional[dict]:
    return _one(f"SELECT {_KB_COLS} FROM knowledge_bases k WHERE k.id=?", (kid,))


def kb_list_visible(vid: str) -> list[dict]:
    """自己的（任意可见性）+ 所有公开的。"""
    return _all(
        f"SELECT {_KB_COLS} FROM knowledge_bases k WHERE k.owner=? OR k.visibility='public' "
        "ORDER BY (k.owner=?) DESC, k.updated_at DESC",
        (vid, vid),
    )


def kb_list_all() -> list[dict]:
    return _all(f"SELECT {_KB_COLS} FROM knowledge_bases k ORDER BY k.updated_at DESC")


def kb_count_owned(vid: str) -> int:
    return _one("SELECT COUNT(*) n FROM knowledge_bases WHERE owner=?", (vid,))["n"]


def kb_owner_bytes(vid: str) -> int:
    return _one(
        "SELECT COALESCE(SUM(d.size),0) n FROM kb_documents d JOIN knowledge_bases k ON k.id=d.kb_id WHERE k.owner=?",
        (vid,),
    )["n"]


def kb_update(kid: str, fields: dict) -> Optional[dict]:
    sets, args = [], []
    for k in ("name", "description", "visibility"):
        if k in fields:
            sets.append(f"{k}=?")
            args.append(fields[k])
    sets.append("updated_at=?")
    args += [time.time(), kid]
    _exec(f"UPDATE knowledge_bases SET {', '.join(sets)} WHERE id=?", tuple(args))
    return kb_get(kid)


def kb_delete(kid: str) -> None:
    _exec("DELETE FROM knowledge_bases WHERE id=?", (kid,))


def kb_docs(kid: str, embed_model: Optional[str] = None) -> list[dict]:
    """embed_model 给定时附带 vec_chunks：已用该模型向量化的片段数。"""
    return _all(
        "SELECT d.*, (SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id=d.id AND c.embed_model=?) AS vec_chunks "
        "FROM kb_documents d WHERE d.kb_id=? ORDER BY d.created_at DESC",
        (embed_model or "", kid),
    )


def kb_doc_get(kid: str, doc_id: str) -> Optional[dict]:
    return _one("SELECT * FROM kb_documents WHERE id=? AND kb_id=?", (doc_id, kid))


def kb_doc_find(doc_id: str, kb_ids: list[str]) -> Optional[dict]:
    if not kb_ids:
        return None
    q = ",".join("?" * len(kb_ids))
    return _one(f"SELECT * FROM kb_documents WHERE id=? AND kb_id IN ({q})", (doc_id, *kb_ids))


def kb_doc_add(kid: str, filename: str, ext: str, size: int, chars: int,
               chunks: list[tuple[str, str]], doc_id: Optional[str] = None) -> dict:
    """在一个事务里写入文档、切片和全文索引。chunks: [(content, tokens)]。"""
    doc_id = doc_id or uuid.uuid4().hex
    now = time.time()
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            c.execute(
                "INSERT INTO kb_documents(id, kb_id, filename, ext, size, chars, chunks, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (doc_id, kid, filename, ext, size, chars, len(chunks), now),
            )
            for seq, (content, tokens) in enumerate(chunks):
                cur = c.execute("INSERT INTO kb_chunks(kb_id, doc_id, seq, content) VALUES(?,?,?,?)",
                                (kid, doc_id, seq, content))
                c.execute("INSERT INTO kb_fts(rowid, tokens) VALUES(?,?)", (cur.lastrowid, tokens))
            c.execute("UPDATE knowledge_bases SET updated_at=? WHERE id=?", (now, kid))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    return kb_doc_get(kid, doc_id)


def kb_doc_delete(kid: str, doc_id: str) -> None:
    _exec("DELETE FROM kb_documents WHERE id=? AND kb_id=?", (doc_id, kid))
    _exec("UPDATE knowledge_bases SET updated_at=? WHERE id=?", (time.time(), kid))


def kb_chunks_range(doc_id: str, start: int, count: int) -> list[dict]:
    return _all(
        "SELECT seq, content FROM kb_chunks WHERE doc_id=? AND seq>=? ORDER BY seq LIMIT ?",
        (doc_id, start, count),
    )


def kb_search(kb_ids: list[str], match: str, limit: int) -> list[dict]:
    """FTS5 BM25 检索。match 为已构造好的 FTS 查询表达式。"""
    if not kb_ids or not match:
        return []
    q = ",".join("?" * len(kb_ids))
    return _all(
        "SELECT c.id, c.kb_id, c.doc_id, c.seq, c.content, d.filename, bm25(kb_fts) AS score "
        "FROM kb_fts JOIN kb_chunks c ON c.id = kb_fts.rowid JOIN kb_documents d ON d.id = c.doc_id "
        f"WHERE kb_fts MATCH ? AND c.kb_id IN ({q}) ORDER BY score LIMIT ?",
        (match, *kb_ids, limit),
    )


# ---------- 知识库向量 ----------
def kb_pending_chunks(embed_model: str, limit: int) -> list[dict]:
    """尚未用当前模型向量化的片段（含换模型后的旧向量）。"""
    return _all(
        "SELECT id, content FROM kb_chunks WHERE embed_model IS NULL OR embed_model != ? ORDER BY id LIMIT ?",
        (embed_model, limit),
    )


def kb_pending_count(embed_model: str) -> int:
    return _one("SELECT COUNT(*) n FROM kb_chunks WHERE embed_model IS NULL OR embed_model != ?", (embed_model,))["n"]


def kb_set_embeddings(rows: list[tuple[int, bytes]], embed_model: str) -> None:
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            c.executemany("UPDATE kb_chunks SET embedding=?, embed_model=? WHERE id=?",
                          [(blob, embed_model, cid) for cid, blob in rows])
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def kb_vector_sig(kid: str, embed_model: str) -> tuple[int, int]:
    """(已向量化片段数, 最大片段 ID)——用作内存向量缓存的版本号。"""
    r = _one("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM kb_chunks WHERE kb_id=? AND embed_model=?", (kid, embed_model))
    return r["n"], r["m"]


def kb_vectors(kid: str, embed_model: str) -> list[dict]:
    return _all("SELECT id, embedding FROM kb_chunks WHERE kb_id=? AND embed_model=? ORDER BY id", (kid, embed_model))


def kb_chunks_by_ids(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    rows = _all(
        "SELECT c.id, c.kb_id, c.doc_id, c.seq, c.content, d.filename FROM kb_chunks c "
        f"JOIN kb_documents d ON d.id = c.doc_id WHERE c.id IN ({q})",
        tuple(ids),
    )
    return {r["id"]: r for r in rows}


# ---------- 运行时设置 ----------
def settings_all() -> dict[str, str]:
    return {r["key"]: r["value"] for r in _all("SELECT key, value FROM settings")}


def settings_set(key: str, value: str) -> None:
    _exec("INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
          "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
          (key, value, time.time()))


def settings_delete(key: str) -> None:
    _exec("DELETE FROM settings WHERE key=?", (key,))


# ---------- 对话模型 ----------
MODEL_COLS = ("name", "description", "base_url", "model", "api_key", "key_env", "temperature", "max_tokens",
              "extra_body", "supports_tools", "replay_reasoning", "enabled", "sort")


def models_all() -> list[dict]:
    return _all("SELECT * FROM models ORDER BY sort, created_at")


def model_get(mid: str) -> Optional[dict]:
    return _one("SELECT * FROM models WHERE id=?", (mid,))


def model_insert(fields: dict, is_default: bool = False) -> str:
    mid = uuid.uuid4().hex
    now = time.time()
    cols = [k for k in MODEL_COLS if k in fields]
    _exec(
        f"INSERT INTO models(id, {', '.join(cols)}, is_default, created_at, updated_at) "
        f"VALUES(?, {', '.join('?' * len(cols))}, ?, ?, ?)",
        (mid, *[fields[k] for k in cols], 1 if is_default else 0, now, now),
    )
    return mid


def model_update(mid: str, fields: dict) -> None:
    cols = [k for k in MODEL_COLS if k in fields]
    if not cols:
        return
    _exec(f"UPDATE models SET {', '.join(f'{k}=?' for k in cols)}, updated_at=? WHERE id=?",
          (*[fields[k] for k in cols], time.time(), mid))


def model_delete(mid: str) -> None:
    _exec("DELETE FROM models WHERE id=?", (mid,))


def model_set_default(mid: str) -> None:
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            c.execute("UPDATE models SET is_default = (id = ?)", (mid,))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
