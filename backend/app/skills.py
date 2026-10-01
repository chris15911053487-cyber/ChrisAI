"""Skill 管理：扫描、解析 SKILL.md、校验、读写、导入导出、发布。

目录与作用域：
- builtin：镜像内置（只读）            SKILLS_BUILTIN_DIR/<name>/
- public ：管理员发布，所有人可见      DATA_DIR/skills/public/<name>/
- user   ：访客自建，仅本人可见        DATA_DIR/skills/users/<visitor_id>/<name>/
"""
import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

from . import config

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VID_RE = re.compile(r"^[a-f0-9]{32}$")
SEGMENT_RE = re.compile(r"^[\w.\- ()（）]{1,120}$")
TEXT_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv", ".html", ".css", ".js", ".xml", ".sql", ".toml", ".ini", ".cfg"}
ALLOWED_EXTS = TEXT_EXTS | {".docx", ".xlsx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ttf", ".otf"}


class SkillError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Skill:
    name: str
    description: str
    scope: str  # builtin | public | user
    path: Path
    owner: Optional[str] = None
    meta: Optional[dict] = None

    def summary(self, editable: bool = False) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "editable": editable,
            "owner": self.owner,
        }


# ---------- 解析 / 校验 ----------
def parse_skill_md(text: str) -> tuple[dict, str]:
    """返回 (frontmatter, body)。frontmatter 必须包含 name、description。"""
    m = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", text, re.S)
    if not m:
        raise SkillError("SKILL.md 必须以 YAML frontmatter 开头（--- name/description ---）")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"SKILL.md frontmatter 不是合法 YAML：{e}")
    if not isinstance(meta, dict):
        raise SkillError("SKILL.md frontmatter 必须是键值对")
    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    if not NAME_RE.match(name):
        raise SkillError("name 只能包含小写字母、数字和连字符，且以字母或数字开头（最长 64）")
    if not desc:
        raise SkillError("description 不能为空")
    if len(desc) > 1024:
        raise SkillError("description 最长 1024 字符")
    meta["name"], meta["description"] = name, desc
    return meta, m.group(2)


def safe_rel(path: str) -> str:
    """校验技能内/工作区内的相对路径，返回规范化 posix 路径。"""
    if not path or "\x00" in path or "\\" in path:
        raise SkillError(f"非法路径：{path!r}")
    p = PurePosixPath(path)
    if p.is_absolute():
        raise SkillError(f"路径必须是相对路径：{path}")
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts or len(parts) > 6:
        raise SkillError(f"非法路径：{path}")
    for seg in parts:
        if seg == ".." or not SEGMENT_RE.match(seg) or seg.startswith(".") or seg != seg.strip():
            raise SkillError(f"非法路径片段：{seg}")
    return "/".join(parts)


def validate_files(files: dict[str, bytes]) -> dict[str, bytes]:
    """校验一整套技能文件，返回规范化后的 {rel: bytes}。"""
    out: dict[str, bytes] = {}
    total = 0
    for raw, data in files.items():
        rel = safe_rel(raw)
        ext = PurePosixPath(rel).suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise SkillError(f"不支持的文件类型：{rel}")
        if len(data) > config.SKILL_MAX_FILE_KB * 1024:
            raise SkillError(f"文件过大：{rel}（上限 {config.SKILL_MAX_FILE_KB} KB）")
        if ext in TEXT_EXTS:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise SkillError(f"文本文件必须是 UTF-8：{rel}")
        total += len(data)
        out[rel] = data
    if len(out) > config.SKILL_MAX_FILES:
        raise SkillError(f"文件数过多（上限 {config.SKILL_MAX_FILES}）")
    if total > config.SKILL_MAX_TOTAL_KB * 1024:
        raise SkillError(f"技能总大小超限（上限 {config.SKILL_MAX_TOTAL_KB} KB）")
    if "SKILL.md" not in out:
        raise SkillError("缺少 SKILL.md")
    return out


# ---------- 读取 ----------
# owner 目录名：匿名访客 32 位 hex，或登录用户 u_{数字}
OWNER_RE = re.compile(r"^(?:[a-f0-9]{32}|u_[0-9]+)$")


def _user_root(vid: str) -> Path:
    if not OWNER_RE.match(vid or ""):
        raise SkillError("非法访客 ID", 403)
    return config.SKILLS_USERS_DIR / vid


def migrate_user_skills(old_owner: str, new_owner: str) -> dict:
    """把 old_owner 的技能目录迁移到 new_owner。目标已存在同名技能则跳过（不覆盖账号已有）。
    返回 {moved, skipped}。"""
    if not (OWNER_RE.match(old_owner or "") and OWNER_RE.match(new_owner or "")):
        return {"moved": 0, "skipped": 0}
    src_root = config.SKILLS_USERS_DIR / old_owner
    if not src_root.is_dir() or old_owner == new_owner:
        return {"moved": 0, "skipped": 0}
    dst_root = config.SKILLS_USERS_DIR / new_owner
    dst_root.mkdir(parents=True, exist_ok=True)
    moved = skipped = 0
    for d in sorted(src_root.iterdir()):
        if not (d.is_dir() and not d.is_symlink() and NAME_RE.match(d.name)):
            continue
        target = dst_root / d.name
        if target.exists():
            skipped += 1
            continue
        shutil.move(str(d), str(target))
        moved += 1
    # 清理空的旧目录
    try:
        if not any(src_root.iterdir()):
            src_root.rmdir()
    except OSError:
        pass
    return {"moved": moved, "skipped": skipped}


def _load(path: Path, scope: str, owner: Optional[str] = None) -> Optional[Skill]:
    md = path / "SKILL.md"
    if not md.is_file():
        return None
    try:
        meta, _ = parse_skill_md(md.read_text("utf-8"))
    except (SkillError, OSError, UnicodeDecodeError):
        return None
    if meta["name"] != path.name:
        return None
    return Skill(meta["name"], meta["description"], scope, path, owner, meta)


def _scan(root: Path, scope: str, owner: Optional[str] = None) -> list[Skill]:
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.is_symlink() and NAME_RE.match(d.name):
            s = _load(d, scope, owner)
            if s:
                out.append(s)
    return out


def shared_skills() -> list[Skill]:
    """builtin + public（同名时 public 不得覆盖 builtin）。"""
    builtin = _scan(config.SKILLS_BUILTIN_DIR, "builtin")
    names = {s.name for s in builtin}
    return builtin + [s for s in _scan(config.SKILLS_PUBLIC_DIR, "public") if s.name not in names]


def user_skills(vid: str) -> list[Skill]:
    return _scan(_user_root(vid), "user", vid)


def visible_skills(vid: str) -> list[Skill]:
    shared = shared_skills()
    names = {s.name for s in shared}
    return shared + [s for s in user_skills(vid) if s.name not in names]


def get_skill(vid: str, name: str) -> Skill:
    if not NAME_RE.match(name or ""):
        raise SkillError(f"技能名不合法：{name}")
    for s in visible_skills(vid):
        if s.name == name:
            return s
    raise SkillError(f"技能不存在：{name}", 404)


def list_skill_files(skill: Skill) -> list[dict]:
    out = []
    for p in sorted(skill.path.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(skill.path).as_posix()
            if any(seg.startswith(".") or seg == "__pycache__" for seg in rel.split("/")):
                continue
            out.append({"path": rel, "size": p.stat().st_size})
    return out


def read_skill_bytes(skill: Skill, rel: str) -> bytes:
    rel = safe_rel(rel)
    p = (skill.path / rel).resolve()
    if not p.is_relative_to(skill.path.resolve()) or not p.is_file():
        raise SkillError(f"文件不存在：{rel}", 404)
    return p.read_bytes()


def skill_files_bytes(skill: Skill) -> dict[str, bytes]:
    return {f["path"]: read_skill_bytes(skill, f["path"]) for f in list_skill_files(skill)}


def skill_body(skill: Skill) -> str:
    _, body = parse_skill_md((skill.path / "SKILL.md").read_text("utf-8"))
    return body.strip()


# ---------- 写入（仅 user 作用域；admin 可操作 public） ----------
def _check_name_free(name: str) -> None:
    if any(s.name == name for s in shared_skills()):
        raise SkillError(f"技能名 {name} 已被公共技能占用，请换一个名字", 409)


def _write_dir(target: Path, files: dict[str, bytes]) -> None:
    tmp = target.with_name(f".{target.name}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel, data in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    old = target.with_name(f".{target.name}.old")
    if target.exists():
        if old.exists():
            shutil.rmtree(old)
        target.rename(old)
    tmp.rename(target)
    if old.exists():
        shutil.rmtree(old)


def save_user_skill(vid: str, files: dict[str, bytes], expect_name: Optional[str] = None, create: bool = True) -> Skill:
    """写入完整技能（覆盖）。files 需包含 SKILL.md。"""
    files = validate_files(files)
    meta, _ = parse_skill_md(files["SKILL.md"].decode("utf-8"))
    name = meta["name"]
    if expect_name and name != expect_name:
        raise SkillError(f"SKILL.md 中的 name（{name}）与技能名（{expect_name}）不一致")
    root = _user_root(vid)
    target = root / name
    if create:
        _check_name_free(name)
        if target.exists():
            raise SkillError(f"技能 {name} 已存在，请使用更新", 409)
        if len(user_skills(vid)) >= config.MAX_USER_SKILLS:
            raise SkillError(f"每位访客最多创建 {config.MAX_USER_SKILLS} 个技能", 403)
    elif not target.is_dir():
        raise SkillError(f"技能不存在或不可编辑：{name}", 404)
    root.mkdir(parents=True, exist_ok=True)
    _write_dir(target, files)
    return _load(target, "user", vid)


def update_user_skill(vid: str, name: str, changes: dict[str, bytes], delete: list[str] = ()) -> Skill:
    """增量更新：合并 changes、删除 delete 中的文件。"""
    skill = get_skill(vid, name)
    if skill.scope != "user":
        raise SkillError("公共/内置技能不可修改；可以复制后另存为你自己的技能", 403)
    files = skill_files_bytes(skill)
    for d in delete:
        files.pop(safe_rel(d), None)
    for rel, data in changes.items():
        files[safe_rel(rel)] = data
    return save_user_skill(vid, files, expect_name=name, create=False)


def delete_user_skill(vid: str, name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise SkillError("技能名不合法")
    target = _user_root(vid) / name
    if not target.is_dir():
        raise SkillError("技能不存在或不可删除", 404)
    shutil.rmtree(target)


# ---------- 导入导出 ----------
def export_zip(skill: Skill) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in skill_files_bytes(skill).items():
            z.writestr(f"{skill.name}/{rel}", data)
    return buf.getvalue()


def files_from_zip(data: bytes) -> dict[str, bytes]:
    """解析上传的 zip：支持根目录直接是 SKILL.md，或包一层 <name>/ 目录。防 zip bomb / 路径穿越。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillError("不是合法的 zip 文件")
    infos = [i for i in z.infolist() if not i.is_dir()]
    infos = [i for i in infos if "__MACOSX" not in i.filename and not PurePosixPath(i.filename).name.startswith(".")]
    if not infos:
        raise SkillError("zip 为空")
    if len(infos) > config.SKILL_MAX_FILES:
        raise SkillError(f"文件数过多（上限 {config.SKILL_MAX_FILES}）")
    if sum(i.file_size for i in infos) > config.SKILL_MAX_TOTAL_KB * 1024:
        raise SkillError("解压后大小超限")
    names = [i.filename for i in infos]
    prefix = ""
    if "SKILL.md" not in names:
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) == 1 and f"{next(iter(tops))}/SKILL.md" in names:
            prefix = next(iter(tops)) + "/"
        else:
            raise SkillError("zip 中找不到 SKILL.md")
    out: dict[str, bytes] = {}
    limit = config.SKILL_MAX_FILE_KB * 1024
    for i in infos:
        if not i.filename.startswith(prefix):
            continue
        with z.open(i) as f:
            content = f.read(limit + 1)
        if len(content) > limit:
            raise SkillError(f"文件过大：{i.filename}")
        out[i.filename[len(prefix):]] = content
    return out


# ---------- 管理员 ----------
def all_user_skills() -> list[Skill]:
    out = []
    if config.SKILLS_USERS_DIR.is_dir():
        for d in sorted(config.SKILLS_USERS_DIR.iterdir()):
            if d.is_dir() and VID_RE.match(d.name):
                out += _scan(d, "user", d.name)
    return out


def publish(owner: str, name: str) -> Skill:
    src = _user_root(owner) / name
    s = _load(src, "user", owner) if src.is_dir() else None
    if not s:
        raise SkillError("技能不存在", 404)
    if any(b.name == name for b in _scan(config.SKILLS_BUILTIN_DIR, "builtin")):
        raise SkillError("与内置技能重名", 409)
    files = validate_files(skill_files_bytes(s))
    config.SKILLS_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    target = config.SKILLS_PUBLIC_DIR / name
    _write_dir(target, files)
    # 发布后移除作者私有副本，避免同名遮蔽
    shutil.rmtree(src)
    return _load(target, "public")


def unpublish(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise SkillError("技能名不合法")
    target = config.SKILLS_PUBLIC_DIR / name
    if not target.is_dir():
        raise SkillError("公共技能不存在", 404)
    shutil.rmtree(target)
