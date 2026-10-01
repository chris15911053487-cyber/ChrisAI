"""Agent 可调用的工具：定义（OpenAI function schema）与执行。"""
import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Optional

import httpx

from . import config, db, knowledge, posts, skills, workspace
from .skills import SkillError

logger = logging.getLogger("agent.tools")

MAX_RUN_OUTPUT = 6000  # 回传给模型的 stdout/stderr 上限


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


_FILES_PARAM = {
    "type": "array",
    "description": "技能文件列表。路径相对技能根目录，如 SKILL.md、scripts/main.py、references/guide.md",
    "items": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string", "description": "UTF-8 文本内容"}},
        "required": ["path", "content"],
    },
}

TOOLS: list[dict] = [
    _fn("load_skill", "加载技能的完整说明（SKILL.md）和文件清单。使用任何技能前必须先调用。",
        {"name": {"type": "string", "description": "技能名"}}, ["name"]),
    _fn("read_skill_file", "读取技能目录中的参考文件或脚本源码（文本）。",
        {"name": {"type": "string"}, "path": {"type": "string", "description": "相对技能根目录的路径"}}, ["name", "path"]),
    _fn("run_skill_script",
        "在隔离沙箱中执行技能里的 Python 脚本。工作目录为会话工作区（可读写，脚本生成的文件会自动提供给用户下载）；"
        "技能目录以只读方式位于环境变量 SKILL_DIR。沙箱无网络，超时 60 秒。",
        {"name": {"type": "string", "description": "技能名"},
         "script": {"type": "string", "description": "脚本路径，如 scripts/build.py"},
         "args": {"type": "array", "items": {"type": "string"}, "description": "命令行参数"},
         "stdin": {"type": "string", "description": "可选，传给脚本的标准输入"}},
        ["name", "script"]),
    _fn("run_python",
        "在隔离沙箱中执行一段 Python 代码（无网络，超时 60 秒）。工作目录为会话工作区，生成的文件会提供给用户下载。"
        "可用库：python-docx、openpyxl、python-pptx、reportlab、pandas、matplotlib、markdown、jinja2、pypdf、pillow、pyyaml。",
        {"code": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, ["code"]),
    _fn("write_file", "在会话工作区写入文本文件（例如 JSON 数据、Markdown、CSV），用户可下载。",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _fn("read_file", "读取会话工作区中的文本文件（包括用户上传的文件）。二进制文件请用 run_python 读取。",
        {"path": {"type": "string"}}, ["path"]),
    _fn("list_files", "列出会话工作区中的文件。", {}, []),
    _fn("create_skill",
        "为当前用户创建一个新技能（仅本人可见）。files 必须包含 SKILL.md，其开头为 YAML frontmatter（name、description）。",
        {"files": _FILES_PARAM}, ["files"]),
    _fn("update_skill", "修改当前用户自己的技能：写入/覆盖 files 中的文件，删除 delete 中列出的文件。",
        {"name": {"type": "string"}, "files": _FILES_PARAM,
         "delete": {"type": "array", "items": {"type": "string"}}}, ["name"]),
]

# 仅在当前会话选择了知识库时提供给模型
KB_TOOLS: list[dict] = [
    _fn("search_knowledge",
        "在用户为本会话选择的知识库中检索（关键词 + 语义混合），返回最相关的若干片段（含文档 ID、文件名、片段序号）。"
        "query 可以是完整问题，也可以是关键词；结果不理想时换个说法再检索。",
        {"query": {"type": "string", "description": "检索关键词或问题"},
         "top_k": {"type": "integer", "description": "返回片段数，默认 6，最多 12"}},
        ["query"]),
    _fn("read_knowledge",
        "按顺序读取知识库中某篇文档的连续片段，用于查看检索命中片段的上下文。",
        {"doc_id": {"type": "string", "description": "search_knowledge 返回的文档 ID"},
         "start": {"type": "integer", "description": "起始片段序号（从 0 开始）"},
         "count": {"type": "integer", "description": "读取片段数，默认 3，最多 8"}},
        ["doc_id"]),
]


# 仅在当前会话启用了"社区帖子"来源时提供给模型
POST_TOOLS: list[dict] = [
    _fn("search_posts",
        "在社区帖子中检索（关键词），返回最相关的若干片段（含帖子 ID、标题、分类、作者）。"
        "帖子是站内用户发布的文章/笔记。query 可以是完整问题或关键词；结果不理想时换个说法再检索。",
        {"query": {"type": "string", "description": "检索关键词或问题"},
         "top_k": {"type": "integer", "description": "返回片段数，默认 8，最多 12"}},
        ["query"]),
    _fn("read_post",
        "按顺序读取某篇帖子的连续片段，用于查看检索命中片段的上下文。",
        {"post_id": {"type": "string", "description": "search_posts 返回的帖子 ID"},
         "start": {"type": "integer", "description": "起始片段序号（从 0 开始）"},
         "count": {"type": "integer", "description": "读取片段数，默认 3，最多 8"}},
        ["post_id"]),
]


def tools_for(kb_ids: list[str], post_on: bool = False) -> list[dict]:
    """按会话启用的来源拼装工具集。post_on=True 时追加帖子检索工具。"""
    out = list(TOOLS)
    if kb_ids:
        out += KB_TOOLS
    if post_on:
        out += POST_TOOLS
    return out

@dataclass
class ToolResult:
    content: str                      # 回填给模型
    ok: bool = True
    summary: str = ""                 # 给前端的简短描述
    files: list[dict] = field(default_factory=list)  # 新生成/更新的文件
    detail: Optional[dict] = None     # 给前端展开的详情（stdout 等）

    def meta(self) -> dict:
        return {"ok": self.ok, "summary": self.summary, "files": self.files, "detail": self.detail}


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n...（省略 {len(s) - n} 字符）...\n" + s[-n // 2:]


def _text_or_note(data: bytes, path: str) -> str:
    try:
        return _clip(data.decode("utf-8"), config.TOOL_RESULT_MAX_CHARS)
    except UnicodeDecodeError:
        return f"[二进制文件 {path}，{len(data)} 字节，请用 run_python 处理]"


def _files_arg(files: Any) -> dict[str, bytes]:
    if not isinstance(files, list):
        raise SkillError("files 必须是数组")
    out = {}
    for f in files:
        if not isinstance(f, dict) or "path" not in f or "content" not in f:
            raise SkillError("files 中每项需要 path 与 content")
        out[str(f["path"])] = str(f["content"]).encode("utf-8")
    return out


async def _run_in_sandbox(sid: str, entry: dict, args: list, stdin: str, skill_files: Optional[dict[str, bytes]]) -> ToolResult:
    work = workspace.all_bytes(sid)
    if sum(len(v) for v in work.values()) > 50 * 1024 * 1024:
        return ToolResult("工作区文件过大（>50MB），无法送入沙箱", ok=False, summary="工作区过大")
    b64 = lambda d: {k: base64.b64encode(v).decode() for k, v in d.items()}
    payload = {
        "entry": entry,
        "args": [str(a) for a in (args or [])][:50],
        "stdin": stdin or "",
        "timeout": config.RUN_TIMEOUT,
        "work": b64(work),
        "skill": b64(skill_files or {}),
    }
    try:
        async with httpx.AsyncClient(timeout=config.RUN_TIMEOUT + 30) as c:
            r = await c.post(f"{config.RUNNER_URL}/run", json=payload, headers={"X-Runner-Token": config.RUNNER_TOKEN})
    except httpx.HTTPError as e:
        logger.error("runner unreachable: %r", e)
        return ToolResult("沙箱服务不可用", ok=False, summary="沙箱不可用")
    if r.status_code != 200:
        logger.error("runner %s: %s", r.status_code, r.text[:300])
        msg = "沙箱繁忙，请稍后重试" if r.status_code == 503 else f"沙箱返回错误 {r.status_code}"
        return ToolResult(msg, ok=False, summary=msg)
    res = r.json()

    produced = []
    errors = []
    for rel, data in (res.get("files") or {}).items():
        try:
            produced.append(workspace.write(sid, rel, base64.b64decode(data)))
        except SkillError as e:
            errors.append(f"{rel}: {e}")
    for rel in res.get("deleted") or []:
        try:
            workspace.delete(sid, rel)
        except SkillError:
            pass

    code = res.get("exit_code")
    timed_out = res.get("timed_out")
    ok = code == 0 and not timed_out
    stdout, stderr = res.get("stdout", ""), res.get("stderr", "")
    lines = [f"exit_code: {code}" + ("（超时被终止）" if timed_out else ""), f"耗时: {res.get('duration', 0):.1f}s"]
    if stdout:
        lines.append("stdout:\n" + _clip(stdout, MAX_RUN_OUTPUT))
    if stderr:
        lines.append("stderr:\n" + _clip(stderr, MAX_RUN_OUTPUT))
    if produced:
        lines.append("生成/更新的文件（已提供给用户下载，无需再次说明路径细节）：" + ", ".join(f["path"] for f in produced))
    if errors:
        lines.append("以下文件未能保存：" + "; ".join(errors))
    summary = ("执行成功" if ok else ("执行超时" if timed_out else f"执行失败（exit {code}）"))
    if produced:
        summary += f"，生成 {len(produced)} 个文件"
    return ToolResult(
        "\n".join(lines), ok=ok, summary=summary, files=produced,
        detail={"stdout": _clip(stdout, 4000), "stderr": _clip(stderr, 4000), "exit_code": code, "timed_out": timed_out},
    )


async def execute(name: str, raw_args: str, vid: str, sid: str,
                  kb_ids: Optional[list[str]] = None,
                  post_cats: Optional[list[str]] = None) -> ToolResult:
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return ToolResult("参数不是合法的 JSON 对象，请修正后重试", ok=False, summary="参数错误")
    try:
        if name in ("search_knowledge", "read_knowledge"):
            return await _dispatch_kb(name, args, kb_ids or [])
        if name in ("search_posts", "read_post"):
            return await _dispatch_posts(name, args, post_cats)
        return await _dispatch(name, args, vid, sid)
    except SkillError as e:
        return ToolResult(f"错误：{e}", ok=False, summary=str(e))
    except Exception:
        logger.exception("tool %s failed", name)
        return ToolResult("工具执行出现内部错误", ok=False, summary="内部错误")


def _int_arg(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


async def _dispatch_kb(name: str, a: dict, kb_ids: list[str]) -> ToolResult:
    if not kb_ids:
        return ToolResult("当前会话没有选择知识库。请提示用户在输入框上方选择知识库。", ok=False, summary="未选择知识库")

    if name == "search_knowledge":
        query = str(a.get("query", "")).strip()[:500]
        if not query:
            raise SkillError("query 不能为空")
        hits, mode = await knowledge.search(kb_ids, query, _int_arg(a.get("top_k"), 6))
        if not hits:
            return ToolResult(f"没有检索到与「{query}」相关的内容。可以换个关键词再试；仍无结果则如实告诉用户知识库中没有相关信息。",
                              summary=f"检索「{query}」：无结果", detail={"query": query, "mode": mode, "hits": []})
        blocks = [
            f"[{i}] 知识库：{h['kb_name']}｜文件：{h['filename']}｜doc_id={h['doc_id']}｜片段 #{h['seq']}\n{h['content']}"
            for i, h in enumerate(hits, 1)
        ]
        text = _clip("\n\n".join(blocks), config.TOOL_RESULT_MAX_CHARS)
        return ToolResult(
            text,
            summary=f"检索「{query}」：{len(hits)} 条结果（{mode}）",
            detail={"query": query, "mode": mode, "hits": [
                {"kb": h["kb_name"], "file": h["filename"], "seq": h["seq"], "snippet": h["content"][:300]} for h in hits
            ]},
        )

    doc, rows = knowledge.read_chunks(kb_ids, str(a.get("doc_id", "")),
                                      _int_arg(a.get("start"), 0), _int_arg(a.get("count"), 3))
    if not rows:
        return ToolResult(f"{doc['filename']} 没有更多片段（共 {doc['chunks']} 个）", summary=f"读取 {doc['filename']}：无更多内容")
    text = f"文件：{doc['filename']}（共 {doc['chunks']} 个片段）\n\n" + "\n\n".join(f"[片段 #{r['seq']}]\n{r['content']}" for r in rows)
    first, last = rows[0]["seq"], rows[-1]["seq"]
    return ToolResult(
        _clip(text, config.TOOL_RESULT_MAX_CHARS),
        summary=f"读取 {doc['filename']} 片段 #{first}–#{last}",
        detail={"hits": [{"file": doc["filename"], "seq": r["seq"], "snippet": r["content"][:300]} for r in rows]},
    )


async def _dispatch_posts(name: str, a: dict, post_cats: Optional[list[str]]) -> ToolResult:
    """帖子检索/阅读。post_cats 为 None 表示全部分类；列表则限定这些分类。"""
    scope = "全部分类" if not post_cats else "、".join(post_cats)
    if name == "search_posts":
        query = str(a.get("query", "")).strip()[:500]
        if not query:
            raise SkillError("query 不能为空")
        top_k = max(1, min(_int_arg(a.get("top_k"), config.POST_SEARCH_LIMIT), 12))
        hits = posts.search(query, categories=post_cats, limit=top_k)
        if not hits:
            return ToolResult(
                f"没有检索到与「{query}」相关的帖子（范围：{scope}）。可换个关键词再试；仍无结果则如实告诉用户社区帖子中没有相关内容。",
                summary=f"检索帖子「{query}」：无结果", detail={"query": query, "hits": []})
        blocks = [
            f"[{i}] 帖子：{h['title']}｜分类：{h['category']}｜作者：{h['author_name']}｜post_id={h['post_id']}｜片段 #{h['seq']}\n{h['content']}"
            for i, h in enumerate(hits, 1)
        ]
        text = _clip("\n\n".join(blocks), config.TOOL_RESULT_MAX_CHARS)
        return ToolResult(
            text,
            summary=f"检索帖子「{query}」：{len(hits)} 条结果",
            detail={"query": query, "hits": [
                {"title": h["title"], "category": h["category"], "seq": h["seq"], "snippet": h["content"][:300]}
                for h in hits
            ]},
        )

    # read_post
    pid = str(a.get("post_id", "")).strip()
    try:
        post = posts.get_readable(pid, owner=None)  # 已发布帖子任何人可读
    except posts.PostError:
        return ToolResult("找不到该帖子（可能未发布或已删除）。请改用 search_posts 重新检索。",
                          ok=False, summary="帖子不存在")
    seq = max(0, _int_arg(a.get("start"), 0))
    count = max(1, min(_int_arg(a.get("count"), 3), 8))
    rows = db.post_chunks_range(pid, seq, count)
    if not rows:
        return ToolResult(f"帖子《{post['title']}》没有更多片段。", summary=f"读取帖子《{post['title']}》：无更多内容")
    text = f"帖子：{post['title']}（分类 {post['category']}｜作者 {post['author_name']}）\n\n" + \
           "\n\n".join(f"[片段 #{r['seq']}]\n{r['content']}" for r in rows)
    first, last = rows[0]["seq"], rows[-1]["seq"]
    return ToolResult(
        _clip(text, config.TOOL_RESULT_MAX_CHARS),
        summary=f"读取帖子《{post['title']}》片段 #{first}–#{last}",
        detail={"hits": [{"title": post["title"], "seq": r["seq"], "snippet": r["content"][:300]} for r in rows]},
    )


async def _dispatch(name: str, a: dict, vid: str, sid: str) -> ToolResult:
    if name == "load_skill":
        s = skills.get_skill(vid, str(a.get("name", "")))
        files = skills.list_skill_files(s)
        listing = "\n".join(f"- {f['path']} ({f['size']} B)" for f in files)
        body = _clip(skills.skill_body(s), config.TOOL_RESULT_MAX_CHARS)
        return ToolResult(
            f"# 技能 {s.name}（{s.scope}）\n{s.description}\n\n## 说明\n{body}\n\n## 文件\n{listing}",
            summary=f"已加载技能 {s.name}",
        )

    if name == "read_skill_file":
        s = skills.get_skill(vid, str(a.get("name", "")))
        path = str(a.get("path", ""))
        return ToolResult(_text_or_note(skills.read_skill_bytes(s, path), path), summary=f"读取 {s.name}/{path}")

    if name == "run_skill_script":
        s = skills.get_skill(vid, str(a.get("name", "")))
        script = skills.safe_rel(str(a.get("script", "")))
        if PurePosixPath(script).suffix != ".py":
            raise SkillError("只支持执行 .py 脚本")
        files = skills.skill_files_bytes(s)
        if script not in files:
            raise SkillError(f"技能 {s.name} 中没有脚本 {script}", 404)
        r = await _run_in_sandbox(sid, {"type": "script", "path": script}, a.get("args") or [], str(a.get("stdin") or ""), files)
        r.summary = f"{s.name}/{script}：{r.summary}"
        return r

    if name == "run_python":
        code = str(a.get("code", ""))
        if not code.strip():
            raise SkillError("code 不能为空")
        if len(code) > 100_000:
            raise SkillError("代码过长")
        r = await _run_in_sandbox(sid, {"type": "code", "code": code}, a.get("args") or [], "", None)
        r.summary = f"运行 Python：{r.summary}"
        r.detail = {**(r.detail or {}), "code": _clip(code, 4000)}
        return r

    if name == "write_file":
        f = workspace.write(sid, str(a.get("path", "")), str(a.get("content", "")).encode("utf-8"))
        return ToolResult(f"已写入 {f['path']}（{f['size']} 字节）", summary=f"写入 {f['path']}", files=[f])

    if name == "read_file":
        path = str(a.get("path", ""))
        return ToolResult(_text_or_note(workspace.read(sid, path), path), summary=f"读取 {path}")

    if name == "list_files":
        files = workspace.listing(sid)
        return ToolResult(
            "\n".join(f"- {f['path']} ({f['size']} B)" for f in files) or "（工作区为空）",
            summary=f"{len(files)} 个文件",
        )

    if name == "create_skill":
        s = skills.save_user_skill(vid, _files_arg(a.get("files")), create=True)
        return ToolResult(
            f"技能 {s.name} 创建成功（仅你可见）。文件：" + ", ".join(f["path"] for f in skills.list_skill_files(s)),
            summary=f"创建技能 {s.name}", detail={"skill": s.name},
        )

    if name == "update_skill":
        s = skills.update_user_skill(
            vid, str(a.get("name", "")), _files_arg(a.get("files") or []), [str(x) for x in (a.get("delete") or [])]
        )
        return ToolResult(f"技能 {s.name} 已更新", summary=f"更新技能 {s.name}", detail={"skill": s.name})

    return ToolResult(f"未知工具：{name}", ok=False, summary="未知工具")


def describe_call(name: str, raw_args: str) -> str:
    """给前端显示的调用标题。"""
    try:
        a = json.loads(raw_args or "{}")
    except ValueError:
        a = {}
    return {
        "load_skill": lambda: f"加载技能 {a.get('name', '')}",
        "read_skill_file": lambda: f"读取 {a.get('name', '')}/{a.get('path', '')}",
        "run_skill_script": lambda: f"执行 {a.get('name', '')}/{a.get('script', '')}",
        "run_python": lambda: "运行 Python 代码",
        "write_file": lambda: f"写入 {a.get('path', '')}",
        "read_file": lambda: f"读取 {a.get('path', '')}",
        "list_files": lambda: "列出工作区文件",
        "create_skill": lambda: "创建技能",
        "update_skill": lambda: f"更新技能 {a.get('name', '')}",
        "search_knowledge": lambda: f"检索知识库：{str(a.get('query', ''))[:60]}",
        "read_knowledge": lambda: "阅读知识库文档",
        "search_posts": lambda: f"检索社区帖子：{str(a.get('query', ''))[:60]}",
        "read_post": lambda: "阅读社区帖子",
    }.get(name, lambda: name)()
