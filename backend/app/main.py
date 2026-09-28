"""Chris Li · AI Agent 后端

- /api/chat           Agent 对话（SSE），支持技能加载、沙箱执行脚本、生成文件、创建技能
- /api/sessions       会话持久化（按匿名访客 cookie 隔离）
- /api/files          会话工作区文件下载 / 上传
- /api/skills         技能管理（列表、查看、创建、编辑、删除、导入导出、复制）
- /api/admin          管理员：审核并发布访客技能（需 X-Admin-Token）
"""
import asyncio
import hmac
import json
import logging
import re
import secrets
import socket
import time
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import quote

import yaml
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import agent, config, db, llm, ratelimit, skills, workspace
from .ratelimit import QuotaError
from .skills import SkillError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent.api")

VID_RE = re.compile(r"^[a-f0-9]{32}$")


# ---------------- 生命周期 ----------------
async def _cleanup_loop():
    while True:
        try:
            n = await asyncio.to_thread(workspace.cleanup_expired)
            if n:
                logger.info("cleanup: removed %d expired files", n)
        except Exception:
            logger.exception("cleanup failed")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    for d in (config.WORKSPACES_DIR, config.SKILLS_PUBLIC_DIR, config.SKILLS_USERS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    await llm.aclose()


app = FastAPI(title="Chris Li AI Agent API", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------------- 访客身份 & 沙箱入站隔离（纯 ASGI 中间件，不影响 SSE） ----------------
_runner_ips: tuple[float, set[str]] = (0.0, set())


def _runner_addrs() -> set[str]:
    global _runner_ips
    ts, ips = _runner_ips
    if time.time() - ts > 30:
        try:
            ips = {ai[4][0] for ai in socket.getaddrinfo(config.RUNNER_HOST, None)}
        except OSError:
            ips = set()
        _runner_ips = (time.time(), ips)
    return ips


class VisitorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = (scope.get("client") or ("", 0))[0]
        if client and client in _runner_addrs():
            # 沙箱中的代码不允许回连 API
            resp = JSONResponse({"detail": "forbidden"}, status_code=403)
            return await resp(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        vid = None
        for part in headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == config.COOKIE_NAME and VID_RE.match(v):
                vid = v
        is_new = vid is None
        if is_new:
            vid = secrets.token_hex(16)
        scope.setdefault("state", {})["vid"] = vid
        secure = headers.get("x-forwarded-proto") == "https"

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and is_new:
                cookie = (f"{config.COOKIE_NAME}={vid}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
                          + ("; Secure" if secure else ""))
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(VisitorMiddleware)


@app.exception_handler(SkillError)
async def _skill_err(_: Request, e: SkillError):
    return JSONResponse({"detail": str(e)}, status_code=e.status)


@app.exception_handler(QuotaError)
async def _quota_err(_: Request, e: QuotaError):
    return JSONResponse({"detail": str(e)}, status_code=e.status)


def visitor(request: Request) -> str:
    vid = request.state.vid
    db.touch_visitor(vid)
    return vid


def client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def owned_session(sid: str, vid: str) -> dict:
    s = db.get_owned_session(sid, vid) if workspace.SID_RE.match(sid or "") else None
    if not s:
        raise HTTPException(404, "会话不存在")
    return s


def write_limit(request: Request) -> None:
    ratelimit.hit_window(f"write:{client_ip(request)}", config.RATE_WRITE_PER_MIN)


def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    if not config.ADMIN_TOKEN or not x_admin_token or not hmac.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise HTTPException(401, "需要管理员令牌")


# ---------------- 健康 ----------------
@app.get("/api/health")
async def health(request: Request, vid: str = Depends(visitor)):
    return {
        "status": "ok",
        "model": config.DEEPSEEK_MODEL,
        "key_configured": bool(config.DEEPSEEK_API_KEY),
        "quota": ratelimit.remaining(vid),
    }


# ---------------- 会话 ----------------
@app.get("/api/sessions")
async def list_sessions(vid: str = Depends(visitor)):
    return {"sessions": db.list_sessions(vid)}


@app.post("/api/sessions")
async def create_session(request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    return db.create_session(vid)


class RenameReq(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


@app.patch("/api/sessions/{sid}")
async def rename_session(sid: str, req: RenameReq, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    db.rename_session(sid, req.title.strip())
    return {"ok": True}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    db.delete_session(sid)
    workspace.remove_all(sid)
    return {"ok": True}


def _file_view(sid: str, f: dict) -> dict:
    return {"path": f["path"], "size": f["size"], "url": f"/api/files/{sid}/{quote(f['path'])}"}


@app.get("/api/sessions/{sid}/messages")
async def session_messages(sid: str, vid: str = Depends(visitor)):
    s = owned_session(sid, vid)
    out = []
    for m in db.get_messages(sid):
        item = {"id": m["id"], "role": m["role"], "content": m["content"]}
        if m["tool_calls"]:
            from .tools import describe_call
            item["tool_calls"] = [
                {"id": c["id"], "name": c["function"]["name"],
                 "title": describe_call(c["function"]["name"], c["function"]["arguments"]),
                 "args": c["function"]["arguments"][:4000]}
                for c in m["tool_calls"]
            ]
        if m["role"] == "tool":
            item["tool_call_id"] = m["tool_call_id"]
            meta = m["meta"] or {}
            item["content"] = None  # 原始结果仅供模型使用
            item.update({k: meta.get(k) for k in ("ok", "summary", "detail")})
            item["files"] = [_file_view(sid, f) for f in meta.get("files") or []]
        out.append(item)
    existing = {f["path"] for f in workspace.listing(sid)}
    return {"session": s, "messages": out, "existing_files": sorted(existing)}


# ---------------- 文件 ----------------
INLINE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


@app.get("/api/sessions/{sid}/files")
async def session_files(sid: str, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    return {"files": [_file_view(sid, f) for f in workspace.listing(sid)]}


@app.post("/api/sessions/{sid}/files")
async def upload_file(sid: str, request: Request, file: UploadFile = File(...), vid: str = Depends(visitor)):
    write_limit(request)
    owned_session(sid, vid)
    data = await file.read(config.UPLOAD_MAX_MB * 1024 * 1024 + 1)
    if len(data) > config.UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"文件过大（上限 {config.UPLOAD_MAX_MB} MB）")
    name = PurePosixPath((file.filename or "upload").replace("\\", "/")).name
    name = re.sub(r"[^\w.\-()]", "_", name).lstrip(".") or "upload"
    f = workspace.write(sid, f"uploads/{name[:100]}", data)
    return _file_view(sid, f)


def _download(data: bytes, filename: str) -> Response:
    ext = PurePosixPath(filename).suffix.lower()
    inline = ext in INLINE_TYPES
    headers = {
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-store",
    }
    return Response(data, media_type=INLINE_TYPES.get(ext, "application/octet-stream"), headers=headers)


@app.get("/api/files/{sid}/{path:path}")
async def download_file(sid: str, path: str, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    data = workspace.read(sid, path)
    return _download(data, PurePosixPath(path).name)


@app.delete("/api/files/{sid}/{path:path}")
async def delete_file(sid: str, path: str, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    workspace.delete(sid, path)
    return {"ok": True}


# ---------------- 对话 ----------------
class ChatReq(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)


_active: dict[str, float] = {}  # sid -> 开始时间（防同一会话并发）
ACTIVE_STALE = 900


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatReq, request: Request, vid: str = Depends(visitor)):
    if not config.DEEPSEEK_API_KEY:
        raise HTTPException(500, "服务端未配置 DEEPSEEK_API_KEY")
    text = req.message.strip()
    if not text:
        raise HTTPException(400, "消息不能为空")
    if len(text) > config.MAX_USER_MESSAGE_CHARS:
        raise HTTPException(400, f"消息过长（上限 {config.MAX_USER_MESSAGE_CHARS} 字）")

    if req.session_id:
        session = owned_session(req.session_id, vid)
    else:
        session = None
    sid = session["id"] if session else None
    if sid:
        if time.time() - _active.get(sid, 0) < ACTIVE_STALE:
            raise HTTPException(409, "该会话正在处理中，请稍候")
        _active[sid] = time.time()
    try:
        ratelimit.begin_turn(vid, client_ip(request))
    except QuotaError:
        if sid:
            _active.pop(sid, None)
        raise

    if not session:
        session = db.create_session(vid, text[:30])
        sid = session["id"]
        _active[sid] = time.time()
    elif session["title"] == "新对话":
        db.rename_session(sid, text[:30])
        session["title"] = text[:30]

    async def stream():
        try:
            yield sse({"type": "session", "id": sid, "title": session["title"]})
            async for ev in agent.run_turn(vid, sid, text):
                if ev["type"] == "tool_result":
                    ev = {**ev, "files": [_file_view(sid, f) for f in ev.get("files") or []]}
                yield sse(ev)
            yield sse({"type": "done", "quota": ratelimit.remaining(vid)})
        except Exception:
            logger.exception("chat turn failed")
            yield sse({"type": "error", "message": "服务内部错误"})
        finally:
            _active.pop(sid, None)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- 技能 ----------------
class SkillFile(BaseModel):
    path: str
    content: str


class SkillCreateReq(BaseModel):
    files: list[SkillFile]


class SkillUpdateReq(BaseModel):
    files: list[SkillFile] = []
    delete: list[str] = []


class ForkReq(BaseModel):
    new_name: str


def _files(req_files: list[SkillFile]) -> dict[str, bytes]:
    return {f.path: f.content.encode("utf-8") for f in req_files}


@app.get("/api/skills")
async def list_skills(vid: str = Depends(visitor)):
    return {"skills": [s.summary(editable=s.scope == "user") for s in skills.visible_skills(vid)]}


@app.get("/api/skills/{name}")
async def get_skill(name: str, vid: str = Depends(visitor)):
    s = skills.get_skill(vid, name)
    return {
        **s.summary(editable=s.scope == "user"),
        "skill_md": (s.path / "SKILL.md").read_text("utf-8"),
        "files": skills.list_skill_files(s),
    }


@app.get("/api/skills/{name}/files/{path:path}")
async def get_skill_file(name: str, path: str, raw: bool = False, vid: str = Depends(visitor)):
    s = skills.get_skill(vid, name)
    data = skills.read_skill_bytes(s, path)
    if not raw:
        try:
            return {"path": path, "content": data.decode("utf-8"), "text": True}
        except UnicodeDecodeError:
            return {"path": path, "content": None, "text": False, "size": len(data)}
    return _download(data, PurePosixPath(path).name)


@app.post("/api/skills")
async def create_skill(req: SkillCreateReq, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    s = skills.save_user_skill(vid, _files(req.files), create=True)
    return s.summary(editable=True)


@app.put("/api/skills/{name}")
async def update_skill(name: str, req: SkillUpdateReq, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    s = skills.update_user_skill(vid, name, _files(req.files), req.delete)
    return s.summary(editable=True)


@app.delete("/api/skills/{name}")
async def delete_skill(name: str, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    skills.delete_user_skill(vid, name)
    return {"ok": True}


@app.post("/api/skills/import")
async def import_skill(request: Request, file: UploadFile = File(...), vid: str = Depends(visitor)):
    write_limit(request)
    data = await file.read(config.UPLOAD_MAX_MB * 1024 * 1024 + 1)
    if len(data) > config.UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(413, "文件过大")
    s = skills.save_user_skill(vid, skills.files_from_zip(data), create=True)
    return s.summary(editable=True)


@app.get("/api/skills/{name}/export")
async def export_skill(name: str, vid: str = Depends(visitor)):
    s = skills.get_skill(vid, name)
    return Response(skills.export_zip(s), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{s.name}.zip"'})


@app.post("/api/skills/{name}/fork")
async def fork_skill(name: str, req: ForkReq, request: Request, vid: str = Depends(visitor)):
    """复制任意可见技能为自己的技能（可编辑）。"""
    write_limit(request)
    s = skills.get_skill(vid, name)
    if not skills.NAME_RE.match(req.new_name):
        raise SkillError("新名称只能包含小写字母、数字和连字符")
    files = skills.skill_files_bytes(s)
    meta, body = skills.parse_skill_md(files["SKILL.md"].decode("utf-8"))
    meta["name"] = req.new_name
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    files["SKILL.md"] = f"---\n{fm}\n---\n{body}".encode("utf-8")
    new = skills.save_user_skill(vid, files, create=True)
    return new.summary(editable=True)


# ---------------- 管理员 ----------------
@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
async def admin_stats():
    return db.stats()


@app.get("/api/admin/skills", dependencies=[Depends(require_admin)])
async def admin_skills():
    return {
        "shared": [s.summary() for s in skills.shared_skills()],
        "users": [s.summary() for s in skills.all_user_skills()],
    }


def _admin_find(owner: str, name: str) -> skills.Skill:
    for s in (skills.shared_skills() if owner in ("public", "builtin") else skills.user_skills(owner)):
        if s.name == name and (owner not in ("public", "builtin") or s.scope == owner):
            return s
    raise SkillError("技能不存在", 404)


@app.get("/api/admin/skills/{owner}/{name}", dependencies=[Depends(require_admin)])
async def admin_skill_detail(owner: str, name: str):
    s = _admin_find(owner, name)
    return {**s.summary(), "files": {rel: (d.decode("utf-8", "replace") if len(d) < 200_000 else f"[{len(d)} B]")
                                      for rel, d in skills.skill_files_bytes(s).items()}}


@app.post("/api/admin/skills/{owner}/{name}/publish", dependencies=[Depends(require_admin)])
async def admin_publish(owner: str, name: str):
    return skills.publish(owner, name).summary()


@app.delete("/api/admin/skills/public/{name}", dependencies=[Depends(require_admin)])
async def admin_unpublish(name: str):
    skills.unpublish(name)
    return {"ok": True}
