"""对话模型配置：任意 OpenAI 兼容的 Chat Completions 接口（DeepSeek、通义千问、Kimi、GLM、豆包、OpenAI、OpenRouter、Ollama…）。

- 管理员在「设置」页增删改模型，设一个默认模型；访客在对话框中可切换已启用的模型，选择按会话保存。
- 厂商特有参数放在 extra_body（合并进请求体，值为 null 表示删除该默认参数），代码中不写死任何厂商。
  例：DeepSeek 思考模式 {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}，并勾选 replay_reasoning。
- Key 可以直接保存（只写不读），也可以填环境变量名（仅允许 *_API_KEY），便于继续把密钥放在 .env。
- 首次启动且模型表为空时，按 .env 中的 DEEPSEEK_* 自动创建默认模型，兼容旧配置。
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from . import config, db
from .settings import URL_RE, mask

MODEL_RE = re.compile(r"^[\w.\-/:@]{1,200}$")
KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}_API_KEY$")
RESERVED = {"model", "messages", "stream", "tools", "tool_choice"}  # extra_body 不能覆盖的字段


@dataclass
class ModelCfg:
    id: str
    name: str
    base_url: str
    model: str
    description: str = ""
    api_key: str = ""
    key_env: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_body: dict = field(default_factory=dict)
    supports_tools: bool = True
    replay_reasoning: bool = False
    enabled: bool = True
    is_default: bool = False
    sort: int = 0

    def key(self) -> str:
        return self.api_key or (os.environ.get(self.key_env, "") if self.key_env else "")

    def public(self) -> dict:
        """给访客看的信息（不含地址和 Key）。"""
        return {"id": self.id, "name": self.name, "description": self.description,
                "supports_tools": self.supports_tools, "is_default": self.is_default}

    def admin(self) -> dict:
        return {
            **self.public(), "base_url": self.base_url, "model": self.model, "host": urlparse(self.base_url).netloc,
            "key_display": mask(self.api_key), "key_env": self.key_env, "key_ready": bool(self.key()),
            "temperature": self.temperature, "max_tokens": self.max_tokens, "extra_body": self.extra_body,
            "replay_reasoning": self.replay_reasoning, "enabled": self.enabled, "sort": self.sort,
        }


def _from_row(r: dict) -> ModelCfg:
    try:
        extra = json.loads(r.get("extra_body") or "{}")
    except ValueError:
        extra = {}
    return ModelCfg(
        id=r["id"], name=r["name"], description=r["description"], base_url=r["base_url"], model=r["model"],
        api_key=r["api_key"], key_env=r["key_env"], temperature=r["temperature"], max_tokens=r["max_tokens"],
        extra_body=extra if isinstance(extra, dict) else {}, supports_tools=bool(r["supports_tools"]),
        replay_reasoning=bool(r["replay_reasoning"]), enabled=bool(r["enabled"]), is_default=bool(r["is_default"]),
        sort=r["sort"],
    )


# ---------------- 查询 ----------------
def all_models() -> list[ModelCfg]:
    return [_from_row(r) for r in db.models_all()]


def get(mid: str) -> Optional[ModelCfg]:
    r = db.model_get(mid) if isinstance(mid, str) and re.fullmatch(r"[a-f0-9]{32}", mid or "") else None
    return _from_row(r) if r else None


def enabled_models() -> list[ModelCfg]:
    return [m for m in all_models() if m.enabled]


def default_model() -> Optional[ModelCfg]:
    ms = enabled_models()
    return next((m for m in ms if m.is_default), ms[0] if ms else None)


def resolve(mid: Optional[str]) -> Optional[ModelCfg]:
    """会话/请求指定的模型（须已启用），否则默认模型。"""
    m = get(mid) if mid else None
    return m if m and m.enabled else default_model()


# ---------------- 校验与修改 ----------------
def validate(data: dict, existing: Optional[ModelCfg] = None) -> dict:
    """把请求数据校验并转为数据库字段。api_key 为空表示保留原值；clear_key=True 表示清空。"""
    out: dict[str, Any] = {}
    name = str(data.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("显示名称不能为空，最长 60 字")
    out["name"] = name
    out["description"] = str(data.get("description") or "").strip()[:200]

    base = str(data.get("base_url") or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not URL_RE.match(base):
        raise ValueError("接口地址必须是 http(s) 地址，例如 https://api.deepseek.com 或 https://xxx/v1")
    out["base_url"] = base

    model = str(data.get("model") or "").strip()
    if not MODEL_RE.match(model):
        raise ValueError("模型 ID 不能为空，且只能包含字母、数字和 . - _ / : @")
    out["model"] = model

    key = str(data.get("api_key") or "").strip()
    if data.get("clear_key"):
        out["api_key"] = ""
    elif key:
        if len(key) > 500 or re.search(r"\s", key):
            raise ValueError("API Key 格式不正确")
        out["api_key"] = key
    elif existing is None:
        out["api_key"] = ""

    key_env = str(data.get("key_env") or "").strip()
    if key_env and not KEY_ENV_RE.match(key_env):
        raise ValueError("环境变量名必须以 _API_KEY 结尾，如 DEEPSEEK_API_KEY")
    out["key_env"] = key_env

    t = data.get("temperature")
    if t in ("", None):
        out["temperature"] = None
    else:
        try:
            t = float(t)
        except (TypeError, ValueError):
            raise ValueError("temperature 必须是数字")
        if not 0 <= t <= 2:
            raise ValueError("temperature 范围 0–2")
        out["temperature"] = t

    mt = data.get("max_tokens")
    if mt in ("", None, 0):
        out["max_tokens"] = None
    else:
        try:
            mt = int(mt)
        except (TypeError, ValueError):
            raise ValueError("max_tokens 必须是整数")
        if not 1 <= mt <= 1_000_000:
            raise ValueError("max_tokens 超出范围")
        out["max_tokens"] = mt

    extra = data.get("extra_body") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra) if extra.strip() else {}
        except ValueError:
            raise ValueError("附加参数必须是合法的 JSON 对象")
    if not isinstance(extra, dict):
        raise ValueError("附加参数必须是 JSON 对象")
    bad = RESERVED & set(extra)
    if bad:
        raise ValueError("附加参数不能包含：" + ", ".join(sorted(bad)))
    raw = json.dumps(extra, ensure_ascii=False)
    if len(raw) > 4000:
        raise ValueError("附加参数过长")
    out["extra_body"] = raw

    out["supports_tools"] = 1 if data.get("supports_tools", True) else 0
    out["replay_reasoning"] = 1 if data.get("replay_reasoning") else 0
    out["enabled"] = 1 if data.get("enabled", True) else 0
    try:
        out["sort"] = int(data.get("sort") or 0)
    except (TypeError, ValueError):
        out["sort"] = 0
    return out


def preview(data: dict, existing: Optional[ModelCfg] = None) -> ModelCfg:
    """用未保存的表单数据构造 ModelCfg（用于"测试"）。"""
    f = validate(data, existing)
    if "api_key" not in f:
        f["api_key"] = existing.api_key if existing else ""
    return _from_row({**f, "id": existing.id if existing else "0" * 32, "is_default": 0})


def create(data: dict) -> ModelCfg:
    f = validate(data)
    first = not all_models()
    if first and not f["enabled"]:
        raise ValueError("第一个模型必须启用")
    return get(db.model_insert(f, is_default=first))


def update(mid: str, data: dict) -> ModelCfg:
    m = get(mid)
    if not m:
        raise LookupError("模型不存在")
    f = validate(data, m)
    if m.is_default and not f["enabled"]:
        raise ValueError("默认模型不能停用，请先把其他模型设为默认")
    db.model_update(mid, f)
    return get(mid)


def set_default(mid: str) -> ModelCfg:
    m = get(mid)
    if not m:
        raise LookupError("模型不存在")
    if not m.enabled:
        raise ValueError("请先启用该模型")
    db.model_set_default(mid)
    return get(mid)


def delete(mid: str) -> None:
    m = get(mid)
    if not m:
        raise LookupError("模型不存在")
    db.model_delete(mid)
    if m.is_default:
        nxt = next(iter(enabled_models()), None)
        if nxt:
            db.model_set_default(nxt.id)


def seed_if_empty() -> None:
    """兼容旧配置：模型表为空时按 .env 的 DEEPSEEK_* 创建"DeepSeek"和"DeepSeek（思考）"两个模型。"""
    if db.models_all():
        return
    base = {"base_url": config.DEEPSEEK_BASE_URL, "model": config.DEEPSEEK_MODEL, "key_env": "DEEPSEEK_API_KEY"}
    fast = validate({**base, "name": "DeepSeek", "description": "速度快，适合日常问答与文档生成",
                     "temperature": 0.5, "extra_body": {"thinking": {"type": "disabled"}}})
    think = validate({**base, "name": "DeepSeek（思考）", "description": "先推理再回答，更适合复杂分析，速度较慢",
                      "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": config.DEEPSEEK_REASONING_EFFORT},
                      "replay_reasoning": True, "sort": 1})
    thinking_default = config.DEEPSEEK_THINKING == "enabled"
    db.model_insert(fast, is_default=not thinking_default)
    db.model_insert(think, is_default=thinking_default)
