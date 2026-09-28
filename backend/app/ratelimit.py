"""限流与配额：IP 每分钟（内存滑动窗口）+ 访客/IP 每日轮数 + 全站每日 token（SQLite）。"""
import threading
import time
from collections import defaultdict, deque

from . import config, db

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


_turn_lock = threading.Lock()


def begin_turn(visitor_id: str, ip: str) -> None:
    """一次对话开始前调用：检查所有配额并计入一轮。"""
    hit_window(f"chat:{ip}", config.RATE_IP_PER_MIN)
    with _turn_lock:
        if db.usage_get("global")["tokens"] >= config.GLOBAL_DAILY_TOKENS:
            raise QuotaError("今日全站额度已用完，请明天再来")
        if db.usage_get(f"v:{visitor_id}")["turns"] >= config.VISITOR_DAILY_TURNS:
            raise QuotaError(f"今日对话次数已达上限（{config.VISITOR_DAILY_TURNS} 轮），请明天再来")
        if db.usage_get(f"ip:{ip}")["turns"] >= config.IP_DAILY_TURNS:
            raise QuotaError("该网络今日对话次数已达上限，请明天再来")
        db.usage_add(f"v:{visitor_id}", turns=1)
        db.usage_add(f"ip:{ip}", turns=1)
        db.usage_add("global", turns=1)


def global_tokens_exhausted() -> bool:
    return db.usage_get("global")["tokens"] >= config.GLOBAL_DAILY_TOKENS


def add_tokens(n: int) -> None:
    if n > 0:
        db.usage_add("global", tokens=n)


def remaining(visitor_id: str) -> dict:
    return {
        "turns_left": max(0, config.VISITOR_DAILY_TURNS - db.usage_get(f"v:{visitor_id}")["turns"]),
        "turns_per_day": config.VISITOR_DAILY_TURNS,
    }
