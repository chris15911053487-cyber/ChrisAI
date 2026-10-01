"""帖子富文本 HTML 净化（入库前的服务端防线）。

Quill 等所见即所得编辑器产出的 HTML 是用户可控内容，直接入库再渲染会导致 XSS。
前端渲染虽已过 DOMPurify，但服务端必须独立净化、不信任客户端。

实现基于标准库 html.parser 的白名单过滤器（零新增依赖）：
- 只保留白名单内的标签；非法标签丢弃但保留其文本内容。
- 只保留白名单内的属性；对 href/src 仅允许安全协议（http/https/mailto/相对路径/本站图片）。
- <script>/<style> 等危险元素连同其文本内容一并丢弃。
- 事件处理属性（on*）、style、javascript: 一律剔除。

另提供 html_to_text()：抽取纯文本用于 FTS5 检索建索引。
"""
from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

# 允许的标签（覆盖 Quill snow 工具栏产出：标题/粗斜体/列表/引用/代码/链接/图片/对齐等）
_ALLOWED_TAGS = {
    "p", "br", "span", "div",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "strike", "del", "sub", "sup",
    "blockquote", "pre", "code",
    "ol", "ul", "li",
    "a", "img",
    "hr", "table", "thead", "tbody", "tr", "th", "td",
}

# 自闭合（无需结束标签）
_VOID_TAGS = {"br", "img", "hr"}

# 内容需整体丢弃的危险元素（含其文本）
_DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "noscript", "template"}

# 每个标签允许的属性白名单
_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "span": {"class"},
    "p": {"class"},
    "div": {"class"},
    "ol": {"class"},
    "ul": {"class"},
    "li": {"class"},
    "pre": {"class"},
    "code": {"class"},
    "h1": {"class"}, "h2": {"class"}, "h3": {"class"},
    "h4": {"class"}, "h5": {"class"}, "h6": {"class"},
    "blockquote": {"class"},
    "table": {"class"}, "th": {"class"}, "td": {"class"},
}

# class 值白名单前缀（Quill 用 ql-* 做对齐/缩进/代码块等）
_ALLOWED_CLASS_RE = re.compile(r"^ql-[\w-]+$")

# href/src 允许的协议；相对路径（不含协议）也允许
_SAFE_URL_RE = re.compile(r"^(?:https?:|mailto:|/|\./|\.\./|#)", re.IGNORECASE)
# 显式拒绝的危险协议（即便混入空白/大小写）
_DANGEROUS_SCHEME_RE = re.compile(r"^\s*(?:javascript|data|vbscript|file)\s*:", re.IGNORECASE)


def _clean_url(value: str) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    # 去掉控制字符后再判协议，防 "java\0script:" 之类绕过
    stripped = re.sub(r"[\x00-\x20]", "", v)
    if _DANGEROUS_SCHEME_RE.match(stripped):
        return None
    if _SAFE_URL_RE.match(v) or _SAFE_URL_RE.match(stripped):
        return v
    # 既非安全协议也非相对路径 → 丢弃
    return None


def _clean_class(value: str) -> str | None:
    tokens = [t for t in (value or "").split() if _ALLOWED_CLASS_RE.match(t)]
    return " ".join(tokens) if tokens else None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._drop_depth = 0  # >0 表示当前处于被丢弃内容的元素内

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self._drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return  # 丢弃标签但保留其子文本
        self.out.append(self._render_open(tag, attrs, self_close=False))

    def handle_startendtag(self, tag: str, attrs):
        tag = tag.lower()
        if self._drop_depth:
            return
        if tag in _DROP_CONTENT_TAGS or tag not in _ALLOWED_TAGS:
            return
        self.out.append(self._render_open(tag, attrs, self_close=True))

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self._drop_depth -= 1
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str):
        if self._drop_depth:
            return
        self.out.append(escape(data, quote=False))

    def _render_open(self, tag: str, attrs, self_close: bool) -> str:
        allowed = _ALLOWED_ATTRS.get(tag, set())
        parts = [tag]
        for name, value in attrs:
            name = (name or "").lower()
            if name.startswith("on"):  # 事件处理属性一律剔除
                continue
            if name not in allowed:
                continue
            value = value if value is not None else ""
            if name in ("href", "src"):
                cleaned = _clean_url(value)
                if cleaned is None:
                    continue
                value = cleaned
            elif name == "class":
                cleaned = _clean_class(value)
                if cleaned is None:
                    continue
                value = cleaned
            elif name in ("width", "height"):
                if not re.fullmatch(r"\d{1,5}", value.strip()):
                    continue
                value = value.strip()
            elif name == "target":
                value = "_blank" if value.strip() == "_blank" else ""
                if not value:
                    continue
            parts.append(f'{name}="{escape(value, quote=True)}"')
        # 外链新开页时强制加 rel，防 tabnabbing
        if tag == "a" and 'target="_blank"' in parts:
            parts.append('rel="noopener noreferrer nofollow"')
        inner = " ".join(parts)
        return f"<{inner} />" if (self_close and tag in _VOID_TAGS) else f"<{inner}>"


def sanitize_html(html: str) -> str:
    """净化用户提交的富文本 HTML，返回只含白名单标签/属性的安全 HTML。"""
    if not html:
        return ""
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return "".join(parser.out)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                     "blockquote", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS and self._drop_depth:
            self._drop_depth -= 1

    def handle_data(self, data: str):
        if not self._drop_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """从 HTML 抽取纯文本，用于 FTS5 检索建索引。"""
    if not html:
        return ""
    ex = _TextExtractor()
    ex.feed(html)
    ex.close()
    text = "".join(ex.parts)
    text = unescape(text)
    # 压缩多余空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()
