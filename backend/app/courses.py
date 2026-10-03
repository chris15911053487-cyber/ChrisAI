"""内置课程：从 COURSES_DIR/<course_id>/course.yaml 读取清单，故事页 / 正文 / 视频为同目录下的文件。

访问规则：
- 公开：课程列表、课程目录所需的结构信息（阶段、课程元信息、工具卡名称）。
- 需登录：故事页、正文、视频、工具卡要点、学习进度。
故事页：stories/<lesson>.html 为纯声明式 HTML 片段（无脚本），由 site/story.html 的固定渲染器播放；
        上线前用 `python -m app.storycheck <文件>` 校验。
课程内容随镜像发布（只读），进度按账号存 SQLite（course_progress）。
"""
import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from . import config, db

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MODES = ("video", "text", "story")
STATE_MAX_BYTES = 64 * 1024          # 故事页阅读状态 JSON 上限
VIDEO_EXTS = {".mp4": "video/mp4", ".webm": "video/webm"}


class CourseError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


# ---------------- 清单加载（按 mtime 缓存，改内容无需重启） ----------------
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _root() -> Path:
    return config.COURSES_DIR


def _load(cid: str) -> Optional[dict]:
    if not ID_RE.match(cid or ""):
        return None
    f = _root() / cid / "course.yaml"
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return None
    with _lock:
        hit = _cache.get(cid)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or data.get("id") != cid:
        return None
    data.setdefault("lessons", [])
    data.setdefault("stages", [])
    data.setdefault("tools", [])
    with _lock:
        _cache[cid] = (mtime, data)
    return data


def _all() -> list[dict]:
    root = _root()
    if not root.is_dir():
        return []
    out = [c for d in sorted(root.iterdir()) if d.is_dir() and (c := _load(d.name))]
    return sorted(out, key=lambda c: (c.get("sort", 0), c["id"]))


def _course(cid: str) -> dict:
    c = _load(cid)
    if not c:
        raise CourseError("课程不存在", 404)
    return c


def _published(cid: str) -> dict:
    c = _course(cid)
    if c.get("status") != "published":
        raise CourseError("课程筹备中，暂未开放", 404)
    return c


def _lesson(c: dict, lid: str) -> dict:
    for l in c["lessons"]:
        if l.get("id") == lid:
            return l
    raise CourseError("课程不存在", 404)


def _file(cid: str, *parts: str) -> Optional[Path]:
    """课程目录内的文件；防目录穿越。"""
    base = (_root() / cid).resolve()
    p = base.joinpath(*parts).resolve()
    if base not in p.parents or not p.is_file():
        return None
    return p


def _video_file(c: dict, l: dict) -> Optional[Path]:
    name = (l.get("video") or "").strip()
    if not name or "/" in name or "\\" in name or Path(name).suffix.lower() not in VIDEO_EXTS:
        return None
    return _file(c["id"], "videos", name)


# ---------------- 公开视图 ----------------
def _lesson_public(c: dict, l: dict) -> dict:
    cid = c["id"]
    mins = l.get("minutes") or {}
    text_f = _file(cid, "lessons", f"{l['id']}.md")
    has_text = text_f is not None
    has_story = bool(l.get("story")) and _file(cid, "stories", f"{l['id']}.html") is not None
    has_video = _video_file(c, l) is not None
    # 按约 500 字/分钟估算阅读时长
    text_min = max(1, round(len(text_f.read_text(encoding="utf-8")) / 500)) if text_f else 0
    return {
        "id": l["id"], "no": l.get("seq"), "stage": l.get("stage"),
        "title": l.get("title", ""), "tagline": l.get("tagline", ""),
        "change": list(l.get("change") or []), "solves": l.get("solves", ""),
        "practice": list(l.get("practice") or []), "cases": list(l.get("cases") or []),
        "takeaways": list(l.get("takeaways") or []), "knowledge": list(l.get("knowledge") or []),
        "modes": {
            "video": {"available": has_video, "minutes": mins.get("video") or 0},
            "text": {"available": has_text, "minutes": text_min},
            "story": {"available": has_story, "minutes": mins.get("story") or 0},
        },
    }


def _summary(c: dict) -> dict:
    return {
        "id": c["id"], "title": c.get("title", ""), "subtitle": c.get("subtitle", ""),
        "summary": c.get("summary", ""), "status": c.get("status", "coming"),
        "audience": c.get("audience", ""), "lesson_count": len(c["lessons"]),
        "stage_count": len(c["stages"]), "topics": list(c.get("topics") or []),
    }


def list_public() -> list[dict]:
    return [_summary(c) for c in _all()]


def detail(cid: str, owner: Optional[str]) -> dict:
    """课程目录数据。owner 为空（未登录）时不含工具卡要点与进度。"""
    c = _course(cid)
    out = _summary(c)
    out["stages"] = [{"id": s.get("id"), "name": s.get("name", ""), "question": s.get("question", "")} for s in c["stages"]]
    out["finale"] = c.get("finale") or None
    out["next"] = c.get("next") or None
    if c.get("status") != "published":
        out.update(lessons=[], tools=[], progress=None, logged_in=bool(owner))
        return out
    out["lessons"] = [_lesson_public(c, l) for l in c["lessons"]]
    out["tools"] = [{"id": t.get("id"), "name": t.get("name", ""), "lesson": t.get("lesson"),
                     **({"points": list(t.get("points") or [])} if owner else {})} for t in c["tools"]]
    out["logged_in"] = bool(owner)
    out["progress"] = progress_map(owner, cid) if owner else None
    return out


# ---------------- 需登录的内容 ----------------
def lesson_text(cid: str, lid: str) -> str:
    c = _published(cid)
    _lesson(c, lid)
    f = _file(cid, "lessons", f"{lid}.md")
    if not f:
        raise CourseError("正文制作中", 404)
    return f.read_text(encoding="utf-8")


def lesson_story(cid: str, lid: str) -> str:
    c = _published(cid)
    l = _lesson(c, lid)
    f = _file(cid, "stories", f"{lid}.html") if l.get("story") else None
    if not f:
        raise CourseError("这节课制作中", 404)
    return f.read_text(encoding="utf-8")


def video_file(cid: str, lid: str) -> tuple[Path, str]:
    c = _published(cid)
    f = _video_file(c, _lesson(c, lid))
    if not f:
        raise CourseError("视频制作中", 404)
    return f, VIDEO_EXTS[f.suffix.lower()]


# ---------------- 进度 ----------------
def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise CourseError("学习方式无效")


def progress_map(owner: str, cid: str) -> dict:
    """{lesson_id: {mode: {done, updated_at}}}（不含故事页阅读状态，避免目录数据过大）"""
    out: dict[str, dict] = {}
    for r in db.course_progress_list(owner, cid):
        out.setdefault(r["lesson_id"], {})[r["mode"]] = {"done": bool(r["done"]), "updated_at": r["updated_at"]}
    return out


def get_state(owner: str, cid: str, lid: str, mode: str) -> dict:
    _check_mode(mode)
    _lesson(_published(cid), lid)
    r = db.course_progress_get(owner, cid, lid, mode)
    if not r:
        return {"done": False, "state": None, "updated_at": 0}
    try:
        state = json.loads(r["state"]) if r["state"] else None
    except ValueError:
        state = None
    return {"done": bool(r["done"]), "state": state, "updated_at": r["updated_at"]}


def save(owner: str, cid: str, lid: str, mode: str, done: Optional[bool], state: Any) -> dict:
    _check_mode(mode)
    _lesson(_published(cid), lid)
    state_json = None
    if state is not None:
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(state_json.encode("utf-8")) > STATE_MAX_BYTES:
            raise CourseError("学习记录过大", 413)
    prev = db.course_progress_get(owner, cid, lid, mode)
    # 完成状态只升不降：重新学习不会把"已完成"抹掉
    new_done = bool(prev and prev["done"]) or bool(done)
    db.course_progress_upsert(owner, cid, lid, mode, new_done,
                              state_json if state is not None else (prev["state"] if prev else None))
    return get_state(owner, cid, lid, mode)


def reset_state(owner: str, cid: str, lid: str, mode: str, full: bool = False) -> dict:
    """清空故事页阅读状态。full=False 保留已完成标记；full=True 整行删除（含完成标记，工具卡随之取消收集）。"""
    _check_mode(mode)
    _lesson(_published(cid), lid)
    prev = db.course_progress_get(owner, cid, lid, mode)
    if prev and full:
        db.course_progress_delete(owner, cid, lid, mode)
    elif prev:
        db.course_progress_upsert(owner, cid, lid, mode, bool(prev["done"]), None)
    return get_state(owner, cid, lid, mode)
