"""SQLite 持久化：访客、会话、消息、文件、用量。"""
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
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        _conn = c
    return _conn


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


def get_session(sid: str) -> Optional[dict]:
    return _one("SELECT * FROM sessions WHERE id=?", (sid,))


def get_owned_session(sid: str, vid: str) -> Optional[dict]:
    return _one("SELECT * FROM sessions WHERE id=? AND visitor_id=?", (sid, vid))


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
) -> int:
    cur = _exec(
        "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id, meta, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            sid,
            role,
            content,
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
            tool_call_id,
            json.dumps(meta, ensure_ascii=False) if meta else None,
            time.time(),
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
    }
