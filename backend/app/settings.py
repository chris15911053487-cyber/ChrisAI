"""运行时配置（向量 / 重排模型）：管理员在「设置」页修改，立即生效。对话模型见 models.py。

优先级：页面保存的值（SQLite settings 表） > 环境变量（backend/.env） > 代码默认值。
生效方式：直接更新 config 模块属性（llm / embeddings 每次调用时读取 config；单进程部署）。
密钥只写不读：接口只返回掩码（…末 4 位），留空表示不修改。
"""
import os
import re
from typing import Any

from . import config, db

URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[^\s]*)?$")
MODEL_RE = re.compile(r"^[\w.\-/:@]{1,200}$")

FIELDS: dict[str, dict] = {
    # ---- 向量模型 ----
    "EMBEDDING_MODEL": {"group": "embed", "label": "模型", "type": "model", "required": True,
                        "suggest": ["BAAI/bge-m3", "Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Embedding-4B",
                                    "Qwen/Qwen3-Embedding-8B"],
                        "hint": "更换模型后，已有文档会在后台自动重新向量化"},
    "EMBEDDING_BASE_URL": {"group": "embed", "label": "接口地址", "type": "url", "required": True,
                           "hint": "OpenAI 兼容的 /embeddings 接口，如硅基流动 https://api.siliconflow.cn/v1"},
    "EMBEDDING_API_KEY": {"group": "embed", "label": "API Key", "type": "secret",
                          "hint": "留空则关闭向量检索，只用关键词检索"},
    "EMBEDDING_DAILY_TOKENS": {"group": "embed", "label": "每日 token 上限", "type": "int",
                               "hint": "向量 + 重排合计，超过后当天自动退回关键词检索"},
    # ---- 重排模型 ----
    "RERANK_MODEL": {"group": "rerank", "label": "模型", "type": "model",
                     "suggest": ["BAAI/bge-reranker-v2-m3", "Qwen/Qwen3-Reranker-0.6B", "Qwen/Qwen3-Reranker-4B",
                                 "Qwen/Qwen3-Reranker-8B"],
                     "placeholder": "留空不重排", "hint": "留空则不重排"},
    "RERANK_BASE_URL": {"group": "rerank", "label": "接口地址", "type": "url", "placeholder": "留空复用向量模型地址"},
    "RERANK_API_KEY": {"group": "rerank", "label": "API Key", "type": "secret", "hint": "未设置时复用向量模型的 Key"},
}

# 启动时（应用覆盖前）的取值 = .env 或代码默认值，用于"恢复默认"
_DEFAULTS: dict[str, Any] = {k: getattr(config, k) for k in FIELDS}


def _validate(name: str, raw: Any) -> Any:
    f = FIELDS[name]
    t = f["type"]
    if t == "int":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{f['label']}必须是整数")
        if not 0 <= v <= 10 ** 11:
            raise ValueError(f"{f['label']}超出范围")
        return v
    v = str(raw if raw is not None else "").strip()
    if t == "enum":
        if v not in f["choices"]:
            raise ValueError(f"{f['label']}只能是 {' / '.join(f['choices'])}")
        return v
    if not v:
        if f.get("required"):
            raise ValueError(f"{f['label']}（{name}）不能为空")
        return ""
    if t == "url":
        v = v.rstrip("/")
        if not URL_RE.match(v):
            raise ValueError(f"{name} 不是合法的 http(s) 地址")
    elif t == "model":
        if not MODEL_RE.match(v):
            raise ValueError(f"{name} 含非法字符")
    elif t == "secret":
        if len(v) > 500 or re.search(r"\s", v):
            raise ValueError(f"{name} 格式不正确")
    return v


def _apply(name: str, value: Any) -> None:
    setattr(config, name, value)


def load() -> None:
    """启动时把页面保存的值覆盖到 config。无效值忽略（保留 .env）。"""
    for k, raw in db.settings_all().items():
        if k in FIELDS:
            try:
                _apply(k, _validate(k, raw) if FIELDS[k]["type"] != "secret" else raw)
            except ValueError:
                pass


def mask(v: Any) -> str:
    s = str(v or "")
    if not s:
        return ""
    return f"已设置 · …{s[-4:]}" if len(s) >= 12 else "已设置"


def view() -> list[dict]:
    saved = db.settings_all()
    out = []
    for name, f in FIELDS.items():
        secret = f["type"] == "secret"
        cur, dflt = getattr(config, name), _DEFAULTS[name]
        out.append({
            "name": name, **{k: v for k, v in f.items()},
            "value": mask(cur) if secret else cur,
            "default_display": (mask(dflt) if secret else str(dflt)) or "（空）",
            "source": "page" if name in saved else ("env" if os.environ.get(name) else "default"),
        })
    return out


def update(values: dict[str, Any], reset: list[str], clear: list[str]) -> set[str]:
    """先整体校验，全部通过才写入。返回实际变化的配置名。"""
    unknown = [k for k in [*values, *reset, *clear] if k not in FIELDS]
    if unknown:
        raise ValueError("未知配置项：" + ", ".join(unknown))
    staged: dict[str, Any] = {}
    for k, raw in values.items():
        if FIELDS[k]["type"] == "secret" and (raw is None or str(raw).strip() == ""):
            continue  # 密钥留空 = 不修改
        staged[k] = _validate(k, raw)
    for k in clear:
        if FIELDS[k]["type"] != "secret":
            raise ValueError(f"{k} 不能清除")
        staged[k] = ""

    changed = set()
    for k in reset:
        if k in staged:
            continue
        db.settings_delete(k)
        if getattr(config, k) != _DEFAULTS[k]:
            changed.add(k)
        _apply(k, _DEFAULTS[k])
    for k, v in staged.items():
        db.settings_set(k, str(v))
        if getattr(config, k) != v:
            changed.add(k)
        _apply(k, v)
    return changed
