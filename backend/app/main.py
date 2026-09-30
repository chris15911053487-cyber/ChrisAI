"""Chris Li · AI Agent 后端

- /api/chat           Agent 对话（SSE），支持技能加载、沙箱执行脚本、生成文件、创建技能
- /api/sessions       会话持久化（按匿名访客 cookie 隔离）
- /api/files          会话工作区文件下载 / 上传
- /api/skills         技能管理（列表、查看、创建、编辑、删除、导入导出、复制）
- /api/kb             知识库管理（创建、上传文档、检索测试）；对话时按会话选择知识库
- /api/admin          管理员：审核并发布访客技能、公开知识库（需 X-Admin-Token）
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
from typing import Any, Optional
from urllib.parse import quote

import yaml
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import agent, config, db, embeddings, knowledge, llm, models, ratelimit, settings, skills, workspace
from .ratelimit import QuotaError
from .skills import SkillError
from .tools import describe_call

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
    settings.load()  # 管理员在「设置」页保存的向量 / 重排配置覆盖 .env
    models.seed_if_empty()  # 首次启动：按 .env 的 DEEPSEEK_* 创建默认对话模型
    for d in (config.WORKSPACES_DIR, config.SKILLS_PUBLIC_DIR, config.SKILLS_USERS_DIR, config.KB_DIR):
        d.mkdir(parents=True, exist_ok=True)
    tasks = [asyncio.create_task(_cleanup_loop()), asyncio.create_task(knowledge.vector_loop())]
    knowledge.kick()  # 启动时补算未向量化 / 换模型后需要重算的片段
    yield
    for t in tasks:
        t.cancel()
    await llm.aclose()
    await embeddings.aclose()


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
    m = models.default_model()
    return {
        "status": "ok",
        "model": m.name if m else None,
        "key_configured": bool(m),
        "vector": embeddings.info(),
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
        if m["role"] == "assistant" and m.get("reasoning"):
            item["reasoning"] = m["reasoning"][:20000]
        if m["tool_calls"]:
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
    m = models.resolve(s.get("model_id"))
    s = {**s, "kb_ids": knowledge.resolve_selection(vid, s.get("kb_ids") or []), "model_id": m.id if m else None}
    return {"session": s, "messages": out, "existing_files": sorted(existing)}


class SessionKbReq(BaseModel):
    kb_ids: list[str] = Field(default_factory=list, max_length=20)


@app.put("/api/sessions/{sid}/kbs")
async def set_session_kbs(sid: str, req: SessionKbReq, vid: str = Depends(visitor)):
    owned_session(sid, vid)
    ids = knowledge.resolve_selection(vid, req.kb_ids)
    db.set_session_kbs(sid, ids)
    return {"kb_ids": ids}


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
@app.get("/api/models")
async def list_models():
    """对话框可选的模型（仅名称和说明，不含地址与 Key）。"""
    return {"models": [m.public() for m in models.enabled_models()]}


class ChatReq(BaseModel):
    session_id: Optional[str] = None
    model_id: Optional[str] = Field(default=None, max_length=32)  # 提供时切换会话使用的模型
    message: str = Field(..., min_length=1)
    kb_ids: Optional[list[str]] = Field(default=None, max_length=20)  # 提供时覆盖会话的知识库选择


_active: dict[str, float] = {}  # sid -> 开始时间（防同一会话并发）
ACTIVE_STALE = 900


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatReq, request: Request, vid: str = Depends(visitor)):
    text = req.message.strip()
    if not text:
        raise HTTPException(400, "消息不能为空")
    if len(text) > config.MAX_USER_MESSAGE_CHARS:
        raise HTTPException(400, f"消息过长（上限 {config.MAX_USER_MESSAGE_CHARS} 字）")

    if req.session_id:
        session = owned_session(req.session_id, vid)
    else:
        session = None
    model = models.resolve(req.model_id or (session or {}).get("model_id"))
    if model is None:
        raise HTTPException(503, "暂无可用的对话模型，请联系管理员配置")
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
    if req.kb_ids is not None:
        db.set_session_kbs(sid, knowledge.resolve_selection(vid, req.kb_ids))
    if session.get("model_id") != model.id:
        db.set_session_model(sid, model.id)

    async def stream():
        try:
            yield sse({"type": "session", "id": sid, "title": session["title"],
                       "model": {"id": model.id, "name": model.name}})
            async for ev in agent.run_turn(vid, sid, text, model):
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


# ---------------- 知识库 ----------------
class KbCreateReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)


class KbPatchReq(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    description: Optional[str] = Field(default=None, max_length=500)


class KbSearchReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=12)


def _doc_view(d: dict) -> dict:
    out = {k: d[k] for k in ("id", "filename", "ext", "size", "chars", "chunks", "created_at")}
    if "vec_chunks" in d:
        out["vec_chunks"] = d["vec_chunks"]
    return out


def _embed_model() -> Optional[str]:
    return embeddings.model_id() if embeddings.enabled() else None


@app.get("/api/kb")
async def list_kbs(vid: str = Depends(visitor)):
    return {
        "kbs": [knowledge.view(k, vid) for k in db.kb_list_visible(vid)],
        "limits": {
            "max_kbs": config.KB_MAX_PER_VISITOR, "max_docs": config.KB_MAX_DOCS,
            "quota_mb": config.KB_VISITOR_QUOTA_MB, "used_bytes": db.kb_owner_bytes(vid),
            "upload_max_mb": config.UPLOAD_MAX_MB, "max_per_session": config.KB_MAX_PER_SESSION,
            "exts": sorted(knowledge.ALLOWED_EXTS),
        },
        "vector": knowledge.vector_status(),
    }


@app.post("/api/kb")
async def create_kb(req: KbCreateReq, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    return knowledge.view(knowledge.create(vid, req.name, req.description), vid)


@app.get("/api/kb/{kid}")
async def get_kb(kid: str, vid: str = Depends(visitor)):
    kb = knowledge.get_visible(vid, kid)
    return {**knowledge.view(kb, vid), "documents": [_doc_view(d) for d in db.kb_docs(kid, _embed_model())],
            "vector": embeddings.info()}


@app.patch("/api/kb/{kid}")
async def update_kb(kid: str, req: KbPatchReq, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    knowledge.get_owned(vid, kid)
    fields = {k: v.strip() for k, v in req.model_dump(exclude_none=True).items()}
    if "name" in fields and not fields["name"]:
        raise HTTPException(400, "名称不能为空")
    return knowledge.view(db.kb_update(kid, fields), vid)


@app.delete("/api/kb/{kid}")
async def delete_kb(kid: str, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    knowledge.get_owned(vid, kid)
    knowledge.remove(kid)
    return {"ok": True}


@app.post("/api/kb/{kid}/documents")
async def upload_kb_doc(kid: str, request: Request, file: UploadFile = File(...), vid: str = Depends(visitor)):
    write_limit(request)
    kb = knowledge.get_owned(vid, kid)
    data = await file.read(config.UPLOAD_MAX_MB * 1024 * 1024 + 1)
    if len(data) > config.UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"文件过大（上限 {config.UPLOAD_MAX_MB} MB）")
    doc = await knowledge.add_document(vid, kb, file.filename or "document.txt", data)
    return {**_doc_view(doc), "truncated": doc["truncated"]}


@app.delete("/api/kb/{kid}/documents/{doc_id}")
async def delete_kb_doc(kid: str, doc_id: str, request: Request, vid: str = Depends(visitor)):
    write_limit(request)
    knowledge.delete_document(knowledge.get_owned(vid, kid), doc_id)
    return {"ok": True}


@app.get("/api/kb/{kid}/documents/{doc_id}/raw")
async def download_kb_doc(kid: str, doc_id: str, vid: str = Depends(visitor)):
    doc, data = knowledge.document_file(knowledge.get_visible(vid, kid), doc_id)
    return _download(data, doc["filename"])


@app.get("/api/kb/{kid}/documents/{doc_id}/chunks")
async def kb_doc_chunks(kid: str, doc_id: str, start: int = 0, count: int = 20, vid: str = Depends(visitor)):
    knowledge.get_visible(vid, kid)
    doc = db.kb_doc_get(kid, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    rows = db.kb_chunks_range(doc_id, max(0, start), max(1, min(count, 50)))
    return {"document": _doc_view(doc), "chunks": rows}


@app.post("/api/kb/{kid}/search")
async def search_kb(kid: str, req: KbSearchReq, request: Request, vid: str = Depends(visitor)):
    ratelimit.hit_window(f"kbsearch:{client_ip(request)}", 30)
    knowledge.get_visible(vid, kid)
    hits, mode = await knowledge.search([kid], req.query, req.top_k)
    return {"mode": mode, "hits": [{k: h[k] for k in ("doc_id", "filename", "seq", "content")} for h in hits]}


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


# ---------------- 管理员：模型设置 ----------------
class SettingsReq(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)   # 恢复为 .env / 默认值
    clear: list[str] = Field(default_factory=list)   # 把密钥设为空


class SettingsTestReq(BaseModel):
    target: str = Field(..., pattern="^(embed|rerank)$")


VECTOR_KEYS = {"EMBEDDING_MODEL", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"}


@app.get("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_get_settings():
    return {"fields": settings.view(), "status": knowledge.vector_status()}


@app.put("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_put_settings(req: SettingsReq):
    try:
        changed = settings.update(req.values, req.reset, req.clear)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if changed:
        logger.info("settings changed by admin: %s", ", ".join(sorted(changed)))  # 只记录名称，不记录值
    if changed & VECTOR_KEYS:
        knowledge.kick()  # 换模型 / 新配置 Key 后补算向量
    return {"changed": sorted(changed), "fields": settings.view(), "status": knowledge.vector_status()}


@app.post("/api/admin/settings/test", dependencies=[Depends(require_admin)])
async def admin_test_settings(req: SettingsTestReq):
    """用当前已保存的配置发一次最小请求，验证连通性。"""
    t0 = time.monotonic()
    try:
        if req.target == "embed":
            vec = await embeddings.embed(["连接测试"])
            detail = f"{config.EMBEDDING_MODEL} · 向量维度 {vec.shape[1]}"
        else:
            ranked = await embeddings.rerank("报销期限是多久", ["报销须在出差结束后 15 个工作日内提交", "公司年会安排在十二月"], 2)
            if not ranked:
                raise embeddings.EmbeddingError("重排接口没有返回结果")
            detail = f"{config.RERANK_MODEL} · 相关文档排第 {[i for i, _ in ranked].index(0) + 1}（得分 {ranked[0][1]:.3f}）"
    except embeddings.EmbeddingError as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "detail": detail, "ms": int((time.monotonic() - t0) * 1000)}


# ---------------- 管理员：对话模型 ----------------
class ModelIn(BaseModel):
    name: str = Field(..., max_length=60)
    description: str = Field(default="", max_length=200)
    base_url: str = Field(..., max_length=500)
    model: str = Field(..., max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=500)  # 留空 = 不修改
    clear_key: bool = False
    key_env: str = Field(default="", max_length=64)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
    supports_tools: bool = True
    replay_reasoning: bool = False
    enabled: bool = True
    sort: int = 0


class ModelTestReq(ModelIn):
    id: Optional[str] = Field(default=None, max_length=32)  # 编辑已有模型时，未填 Key 则用已保存的 Key


def _model_call(fn, *a):
    try:
        return fn(*a)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _models_view() -> dict:
    return {"models": [m.admin() for m in models.all_models()]}


@app.get("/api/admin/models", dependencies=[Depends(require_admin)])
async def admin_list_models():
    return _models_view()


@app.post("/api/admin/models", dependencies=[Depends(require_admin)])
async def admin_create_model(req: ModelIn):
    m = _model_call(models.create, req.model_dump())
    logger.info("model created: %s (%s)", m.name, m.model)
    return {"model": m.admin(), **_models_view()}


@app.put("/api/admin/models/{mid}", dependencies=[Depends(require_admin)])
async def admin_update_model(mid: str, req: ModelIn):
    m = _model_call(models.update, mid, req.model_dump())
    logger.info("model updated: %s (%s)", m.name, m.model)
    return {"model": m.admin(), **_models_view()}


@app.post("/api/admin/models/{mid}/default", dependencies=[Depends(require_admin)])
async def admin_default_model(mid: str):
    _model_call(models.set_default, mid)
    return _models_view()


@app.delete("/api/admin/models/{mid}", dependencies=[Depends(require_admin)])
async def admin_delete_model(mid: str):
    _model_call(models.delete, mid)
    return _models_view()


PING_TOOL = [{"type": "function", "function": {"name": "ping", "description": "连通性测试，收到请求时必须调用",
                                               "parameters": {"type": "object", "properties": {}}}}]


@app.post("/api/admin/models/test", dependencies=[Depends(require_admin)])
async def admin_test_model(req: ModelTestReq):
    """用表单中的配置（可未保存）发一次最小请求；支持工具时顺带验证工具调用。"""
    existing = models.get(req.id) if req.id else None
    cfg = _model_call(models.preview, req.model_dump(exclude={"id"}), existing)
    t0 = time.monotonic()
    text, called, think = "", False, False
    prompt = "请调用 ping 工具。" if cfg.supports_tools else "只回复两个字：正常"
    try:
        async for kind, val in llm.stream_chat([{"role": "user", "content": prompt}],
                                              PING_TOOL if cfg.supports_tools else None, model=cfg):
            if kind == "delta":
                text += val
            elif kind == "reasoning":
                think = True
            elif kind == "tool_calls":
                called = any(c["function"]["name"] == "ping" for c in val)
            elif kind == "usage":
                ratelimit.add_tokens(val)
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}
    parts = [f"{cfg.model} 连接正常"]
    if cfg.supports_tools:
        parts.append("工具调用 ✓" if called else "未触发工具调用（该模型可能不支持工具，建议取消勾选）")
    elif text:
        parts.append(f"回复「{text.strip()[:20]}」")
    if think:
        parts.append("返回了思考内容")
    return {"ok": True, "tools_ok": called, "detail": " · ".join(parts), "ms": int((time.monotonic() - t0) * 1000)}


class KbVisibilityReq(BaseModel):
    visibility: str = Field(..., pattern="^(private|public)$")


@app.get("/api/admin/kb", dependencies=[Depends(require_admin)])
async def admin_list_kbs():
    return {"kbs": [{**knowledge.view(k), "owner": k["owner"]} for k in db.kb_list_all()]}


@app.post("/api/admin/kb/{kid}/visibility", dependencies=[Depends(require_admin)])
async def admin_kb_visibility(kid: str, req: KbVisibilityReq):
    if not knowledge.KID_RE.match(kid) or not db.kb_get(kid):
        raise HTTPException(404, "知识库不存在")
    return knowledge.view(db.kb_update(kid, {"visibility": req.visibility}))


@app.delete("/api/admin/kb/{kid}", dependencies=[Depends(require_admin)])
async def admin_delete_kb(kid: str):
    if not knowledge.KID_RE.match(kid) or not db.kb_get(kid):
        raise HTTPException(404, "知识库不存在")
    knowledge.remove(kid)
    return {"ok": True}


# ---------------- 首页卡片（AI工具 / AI作品 / AI课程） ----------------
def _valid_section(section: str) -> str:
    if section not in db.CARD_SECTIONS:
        raise HTTPException(400, f"section 必须是 {', '.join(db.CARD_SECTIONS)} 之一")
    return section


class CardIn(BaseModel):
    section: str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=1000)
    icon: str = Field(default="", max_length=16)
    tag: str = Field(default="", max_length=40)
    category: str = Field(default="", max_length=40)
    sort: int = 0
    enabled: bool = True


class CardPatch(BaseModel):
    section: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=1000)
    icon: Optional[str] = Field(default=None, max_length=16)
    tag: Optional[str] = Field(default=None, max_length=40)
    category: Optional[str] = Field(default=None, max_length=40)
    sort: Optional[int] = None
    enabled: Optional[bool] = None


# 公开只读：展示页拉取启用中的卡片
@app.get("/api/cards")
async def public_cards(section: Optional[str] = None):
    if section is not None:
        _valid_section(section)
    return {"cards": db.list_cards(section=section, enabled_only=True)}


# 管理端：全部卡片（含未启用）
@app.get("/api/admin/cards", dependencies=[Depends(require_admin)])
async def admin_list_cards(section: Optional[str] = None):
    if section is not None:
        _valid_section(section)
    return {"cards": db.list_cards(section=section, enabled_only=False)}


@app.post("/api/admin/cards", dependencies=[Depends(require_admin)])
async def admin_create_card(card: CardIn):
    _valid_section(card.section)
    return db.create_card(
        section=card.section, title=card.title.strip(), description=card.description,
        url=card.url.strip(), icon=card.icon.strip(), tag=card.tag.strip(),
        category=card.category.strip(), sort=card.sort, enabled=card.enabled,
    )


@app.put("/api/admin/cards/{card_id}", dependencies=[Depends(require_admin)])
async def admin_update_card(card_id: int, patch: CardPatch):
    if db.get_card(card_id) is None:
        raise HTTPException(404, "卡片不存在")
    fields = patch.model_dump(exclude_none=True)
    if "section" in fields:
        _valid_section(fields["section"])
    for k in ("title", "url", "icon", "tag", "category"):
        if k in fields and isinstance(fields[k], str):
            fields[k] = fields[k].strip()
    return db.update_card(card_id, fields)


@app.delete("/api/admin/cards/{card_id}", dependencies=[Depends(require_admin)])
async def admin_delete_card(card_id: int):
    if db.get_card(card_id) is None:
        raise HTTPException(404, "卡片不存在")
    db.delete_card(card_id)
    return {"ok": True}
