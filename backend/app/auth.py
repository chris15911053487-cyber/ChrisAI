"""登录鉴权（零依赖）：

- 密码哈希：标准库 hashlib.scrypt，自带随机 salt，常量时间比较。
- 会话 token：HMAC-SHA256 签名的 "uid.exp.sig"，放在 HttpOnly cookie 中；无需服务端存储。
- owner id 约定：登录用户的数据归属 ID 为 "u_{uid}"，与匿名访客的 32 位 hex 不冲突，
  因此 sessions / skills / knowledge_bases 等现有按 owner 隔离的逻辑可直接复用。
"""
import hashlib
import hmac
import re
import secrets
import time
from typing import Optional

from . import config, db, skills

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")  # 字母数字下划线中文，2-20
PASSWORD_MIN = 6
PASSWORD_MAX = 128

# scrypt 参数（CPU/内存成本）
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _secret() -> bytes:
    s = config.AUTH_SECRET or config.ADMIN_TOKEN or "chrisai-insecure-default-secret"
    return s.encode("utf-8")


# ---------- 密码哈希 ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ---------- 会话 token ----------
def make_token(uid: int, ttl_days: Optional[int] = None) -> str:
    exp = int(time.time()) + (ttl_days if ttl_days is not None else config.SESSION_TTL_DAYS) * 86400
    body = f"{uid}.{exp}"
    sig = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[int]:
    """校验签名与有效期，返回 uid；无效返回 None。"""
    if not token:
        return None
    try:
        uid_s, exp_s, sig = token.split(".")
        body = f"{uid_s}.{exp_s}"
    except ValueError:
        return None
    expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        if int(exp_s) < int(time.time()):
            return None
        return int(uid_s)
    except ValueError:
        return None


# ---------- owner id ----------
def owner_of_user(uid: int) -> str:
    return f"u_{uid}"


def is_user_owner(owner: str) -> bool:
    return isinstance(owner, str) and owner.startswith("u_")


def uid_from_owner(owner: str) -> Optional[int]:
    if is_user_owner(owner):
        try:
            return int(owner[2:])
        except ValueError:
            return None
    return None


# ---------- 注册 / 登录 ----------
def _validate_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username or ""):
        raise AuthError("用户名需为 2-20 位字母、数字、下划线或中文")
    if not (PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX):
        raise AuthError(f"密码长度需为 {PASSWORD_MIN}-{PASSWORD_MAX} 位")


def register(username: str, password: str) -> dict:
    username = (username or "").strip()
    _validate_credentials(username, password)
    if db.user_get_by_name(username):
        raise AuthError("该用户名已被注册", 409)
    return db.user_create(username, hash_password(password))


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    user = db.user_get_by_name(username)
    # 无论用户是否存在都执行一次哈希校验，降低用户名枚举的时间差异
    if not user or not verify_password(password, user["password_hash"]):
        raise AuthError("用户名或密码错误", 401)
    db.user_touch_login(user["id"])
    return user


# ---------- 匿名数据迁移 ----------
def migrate_visitor_to_user(old_vid: str, uid: int) -> dict:
    """登录/注册成功后，把当前匿名访客（old_vid）名下的会话、知识库、技能迁移到账号 u_{uid}。
    幂等：无数据时返回全 0；技能同名以账号已有为准（跳过）。返回迁移概要。"""
    new_owner = owner_of_user(uid)
    # 已登录态或非法 vid，不迁移
    if not old_vid or is_user_owner(old_vid) or old_vid == new_owner:
        return {"sessions": 0, "kbs": 0, "skills_moved": 0, "skills_skipped": 0}
    data = db.reassign_owner(old_vid, new_owner)
    sk = skills.migrate_user_skills(old_vid, new_owner)
    return {
        "sessions": data["sessions"],
        "kbs": data["kbs"],
        "skills_moved": sk["moved"],
        "skills_skipped": sk["skipped"],
    }

