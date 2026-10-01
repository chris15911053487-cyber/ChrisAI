"""限流与配额：IP 每分钟（内存滑动窗口）+ 访客/用户/IP 每日轮数 + 全站每日 token（SQLite）。

日切时区由 config.QUOTA_TZ 决定（默认 Asia/Shanghai），配额错误会提示按该时区的恢复时间。
匿名访客与登录用户采用分级配额；登录用户上限取用户表 daily_turns（-2=全局登录额度，-1=不限制，>=0=自定义）。
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from . import auth, config, db

_lock = threading.Lock()
_windows: dict[str, deque] = defaultdict(deque)


class QuotaError(Exception):
    def __init__(self, message: str, status: int = 429):
        super().__init__(message)
        self.status = status


def hit_window(key: str, limit: int, window: float = 60.0) -> None:
    """滑动窗口计数，超过则抛 QuotaError。"""
    now = time.monotonic()
    with _lock:
        q = _windows[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = int(window - (now - q[0])) + 1
            raise QuotaError(f"请求过于频繁，请 {retry} 秒后再试")
        q.append(now)
        # 防止字典无限增长
        if len(_windows) > 50_000:
            for k in [k for k, v in _windows.items() if not v][:10_000]:
                _windows.pop(k, None)


def _reset_hint() -> str:
    """下一个日切时间的友好提示，如：将于北京时间 00:00 恢复。"""
    now = datetime.now(config.QUOTA_TZINFO)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return f"将于{config.QUOTA_TZ_LABEL} {nxt:%H:%M} 恢复"


def _user_limit(owner: str) -> int:
    """登录用户的每日轮数上限；-1 表示无限制。匿名返回 VISITOR_DAILY_TURNS。"""
    uid = auth.uid_from_owner(owner)
    if uid is None:
        return config.VISITOR_DAILY_TURNS
    u = db.user_get(uid)
    dt = u["daily_turns"] if u else -2
    if dt == -1:
        return -1
    if dt is None or dt < 0:          # -2 或异常：用全局登录额度
        return config.USER_DAILY_TURNS
    return dt


_turn_lock = threading.Lock()


def begin_turn(owner: str, ip: str) -> None:
    """一次对话开始前调用：检查所有配额并计入一轮。owner 为匿名 vid 或 "u_{uid}"。"""
    hit_window(f"chat:{ip}", config.RATE_IP_PER_MIN)
    limit = _user_limit(owner)
    logged_in = auth.is_user_owner(owner)
    with _turn_lock:
        if db.usage_get("global")["tokens"] >= config.GLOBAL_DAILY_TOKENS:
            raise QuotaError("今日全站额度已用完，" + _reset_hint())
        if limit >= 0 and db.usage_get(f"v:{owner}")["turns"] >= limit:
            if logged_in:
                raise QuotaError(f"今日对话次数已达上限（{limit} 轮），{_reset_hint()}")
            raise QuotaError(
                f"今日对话次数已达上限（{limit} 轮），{_reset_hint()}。登录后可获得更多额度。"
            )
        if db.usage_get(f"ip:{ip}")["turns"] >= config.IP_DAILY_TURNS:
            raise QuotaError("该网络今日对话次数已达上限，" + _reset_hint())
        db.usage_add(f"v:{owner}", turns=1)
        db.usage_add(f"ip:{ip}", turns=1)
        db.usage_add("global", turns=1)


def global_tokens_exhausted() -> bool:
    return db.usage_get("global")["tokens"] >= config.GLOBAL_DAILY_TOKENS


def add_tokens(n: int) -> None:
    if n > 0:
        db.usage_add("global", tokens=n)


def remaining(owner: str) -> dict:
    """剩余轮数与登录态，供前端 /api/health 与对话结束时展示。"""
    limit = _user_limit(owner)
    used = db.usage_get(f"v:{owner}")["turns"]
    unlimited = limit < 0
    return {
        "logged_in": auth.is_user_owner(owner),
        "unlimited": unlimited,
        "turns_per_day": None if unlimited else limit,
        "turns_left": None if unlimited else max(0, limit - used),
        "reset_hint": _reset_hint(),
        "tz_label": config.QUOTA_TZ_LABEL,
    }
