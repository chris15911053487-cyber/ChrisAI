"""会话工作区：DATA_DIR/workspaces/<session_id>/，存放生成/上传的文件。"""
import re
import shutil
import time
from pathlib import Path

from . import config, db
from .skills import SkillError, safe_rel

SID_RE = re.compile(r"^[a-f0-9]{32}$")


def root(sid: str) -> Path:
    if not SID_RE.match(sid):
        raise SkillError("非法会话 ID", 400)
    return config.WORKSPACES_DIR / sid


def resolve(sid: str, rel: str) -> tuple[str, Path]:
    rel = safe_rel(rel)
    base = root(sid)
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        raise SkillError("路径越界", 400)
    return rel, p


def usage_bytes(sid: str) -> int:
    base = root(sid)
    if not base.is_dir():
        return 0
    return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())


def write(sid: str, rel: str, data: bytes) -> dict:
    rel, p = resolve(sid, rel)
    old = p.stat().st_size if p.is_file() else 0
    if usage_bytes(sid) - old + len(data) > config.WORKSPACE_QUOTA_MB * 1024 * 1024:
        raise SkillError(f"工作区空间不足（上限 {config.WORKSPACE_QUOTA_MB} MB）", 413)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    db.upsert_file(sid, rel, len(data))
    return {"path": rel, "size": len(data)}


def read(sid: str, rel: str) -> bytes:
    rel, p = resolve(sid, rel)
    if not p.is_file():
        raise SkillError(f"文件不存在：{rel}", 404)
    return p.read_bytes()


def listing(sid: str) -> list[dict]:
    base = root(sid)
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out.append({"path": p.relative_to(base).as_posix(), "size": p.stat().st_size})
    return out


def all_bytes(sid: str) -> dict[str, bytes]:
    base = root(sid)
    return {f["path"]: (base / f["path"]).read_bytes() for f in listing(sid)}


def delete(sid: str, rel: str) -> None:
    rel, p = resolve(sid, rel)
    if p.is_file():
        p.unlink()
    db.delete_file_record(sid, rel)


def remove_all(sid: str) -> None:
    base = root(sid)
    if base.is_dir():
        shutil.rmtree(base, ignore_errors=True)


def cleanup_expired() -> int:
    """删除超过 FILE_TTL_DAYS 的文件。"""
    cutoff = time.time() - config.FILE_TTL_DAYS * 86400
    n = 0
    for rec in db.expired_files(cutoff):
        try:
            delete(rec["session_id"], rec["path"])
            n += 1
        except SkillError:
            db.delete_file_record(rec["session_id"], rec["path"])
    # 兜底：清理无记录的过期文件
    if config.WORKSPACES_DIR.is_dir():
        for p in config.WORKSPACES_DIR.rglob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                n += 1
    return n
