"""故事页片段校验：stories/<lesson>.html 上线前必须零错误。

故事页是纯声明式 HTML（渲染器 site/story.js 负责全部行为），因此这里检查两类问题：
1. 安全：不允许脚本、样式、外链资源、事件属性、javascript: 链接。
2. 结构：章节 / 场景 / 步骤的引用是否闭合（data-show 指向的消息存在、每个解锁门都有互动、互动配置完整……）。

用法：python -m app.storycheck courses/ai-intro/stories/l4.html [...]
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
FORBIDDEN_TAGS = {"script", "style", "iframe", "object", "embed", "link", "meta", "base", "form", "frame", "frameset"}
FX = {"pause", "bad", "hl-k", "pick", "split", "steps3"}
ACTS = {"choice", "pick"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "line")

    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"], line: int):
        self.tag, self.attrs, self.children, self.parent, self.line = tag, attrs, [], parent, line

    def cls(self) -> set:
        return set((self.attrs.get("class") or "").split())

    def has(self, c: str) -> bool:
        return c in self.cls()

    def walk(self):
        for ch in self.children:
            yield ch
            yield from ch.walk()

    def find(self, pred):
        return [n for n in self.walk() if pred(n)]

    def up(self, pred) -> Optional["Node"]:
        p = self.parent
        while p is not None:
            if pred(p):
                return p
            p = p.parent
        return None


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None, 0)
        self.cur = self.root
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        a = {k: (v or "") for k, v in attrs}
        if tag in FORBIDDEN_TAGS:
            self.errors.append(f"第{line}行：不允许 <{tag}>（故事页只写内容，行为由渲染器提供）")
        for k, v in a.items():
            if k.startswith("on"):
                self.errors.append(f"第{line}行：不允许事件属性 {k}")
            if k in ("href", "src", "action", "formaction") and re.match(r"\s*(javascript|data|vbscript):", v, re.I):
                self.errors.append(f"第{line}行：不允许 {k}={v[:30]}")
            if k == "style" and re.search(r"url\(|expression", v, re.I):
                self.errors.append(f"第{line}行：style 中不允许 url()/expression")
        n = Node(tag, a, self.cur, line)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.cur.tag == tag:
            self.cur = self.cur.parent

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is None or n is self.root:
            self.errors.append(f"第{self.getpos()[0]}行：多余的 </{tag}>")
            return
        self.cur = n.parent


def _ids(v: str) -> list[str]:
    return [x for x in (v or "").split(",") if x]


def check(html: str) -> list[str]:
    b = _Builder()
    b.feed(html)
    b.close()
    errs = list(b.errors)
    root = b.root

    metas = root.find(lambda n: n.has("story-meta"))
    if len(metas) != 1:
        errs.append("需要且只能有一个 <header class=\"story-meta\">（侧栏标题、阶段名）")
    elif not metas[0].find(lambda n: n.has("t")):
        errs.append("story-meta 里缺少 .t 标题")

    chapters = root.find(lambda n: n.has("chapter"))
    if not chapters:
        errs.append("没有任何 .chapter 章节")
    stage_n = 0
    if metas:
        stage_n = len([x for x in metas[0].attrs.get("data-stages", "").split("|") if x])
    for ch in chapters:
        title = ch.attrs.get("data-title")
        if not title:
            errs.append(f"第{ch.line}行：章节缺少 data-title")
        st = ch.attrs.get("data-stage", "0")
        if not st.isdigit() or int(st) > stage_n:
            errs.append(f"第{ch.line}行：章节 data-stage={st} 超出 story-meta 的 data-stages（共 {stage_n} 个阶段）")
        secs = [c for c in ch.children if c.tag == "section"]
        if not secs:
            errs.append(f"第{ch.line}行：章节「{title}」没有内容")
        for s in secs:
            if not (s.has("beat") or s.has("scene")):
                errs.append(f"第{s.line}行：章节下的 section 只能是 .beat 或 .scene")

    steps_all = root.find(lambda n: n.has("st"))
    if not steps_all:
        errs.append("没有任何步骤（.st）")

    # ---- 场景 ----
    unlocks: dict[str, Node] = {}
    for sc in root.find(lambda n: n.has("scene")):
        stage = next((c for c in sc.children if c.has("stage")), None)
        steps_box = next((c for c in sc.children if c.has("steps")), None)
        if stage is None or steps_box is None:
            errs.append(f"第{sc.line}行：.scene 需要 .stage 和 .steps 两个子元素")
            continue
        devs = [c for c in stage.children if c.has("phone") or c.has("board")]
        if len(devs) != 1:
            errs.append(f"第{stage.line}行：.stage 里需要且只能有一个 .phone（对话）或 .board（白板）")
            continue
        dev = devs[0]
        if dev.has("phone"):
            hd = dev.find(lambda n: n.has("phone-hd"))
            if not hd or not hd[0].find(lambda n: n.tag == "b"):
                errs.append(f"第{dev.line}行：.phone 需要 .phone-hd 里的 <b>身份</b>")
            items = dev.find(lambda n: n.has("m") and "data-i" in n.attrs)
        else:
            items = dev.find(lambda n: "data-i" in n.attrs)
        ids = [n.attrs["data-i"] for n in items]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            errs.append(f"第{dev.line}行：data-i 重复：{', '.join(sorted(dup))}")
        idset = set(ids)
        st_list = [c for c in steps_box.children if c.has("step")]
        if not st_list:
            errs.append(f"第{steps_box.line}行：.steps 里没有 .step")
        for stp in st_list:
            if not stp.has("st"):
                errs.append(f"第{stp.line}行：.step 需要同时带 class st")
            cards = [c for c in stp.children if c.has("card")]
            if len(cards) != 1:
                errs.append(f"第{stp.line}行：每个 .step 需要且只能有一个 .card")
            miss = [i for i in _ids(stp.attrs.get("data-show", "")) if i not in idset]
            if miss:
                errs.append(f"第{stp.line}行：data-show 引用了不存在的 data-i：{', '.join(miss)}")
            for fx in (stp.attrs.get("data-fx") or "").split():
                if fx not in FX:
                    errs.append(f"第{stp.line}行：未知 data-fx「{fx}」，可用：{', '.join(sorted(FX))}")
            keep = stp.attrs.get("data-keep")
            if keep and keep not in idset:
                errs.append(f"第{stp.line}行：data-keep 引用了不存在的 data-i：{keep}")
            act = stp.attrs.get("data-act")
            if act is None:
                continue
            if act not in ACTS:
                errs.append(f"第{stp.line}行：未知 data-act「{act}」，可用：{', '.join(sorted(ACTS))}")
                continue
            gate = stp.attrs.get("data-unlock", "")
            if not gate:
                errs.append(f"第{stp.line}行：互动步骤需要 data-unlock（解锁哪个门）")
            elif gate in unlocks:
                errs.append(f"第{stp.line}行：解锁门 {gate} 已被第{unlocks[gate].line}行的互动使用")
            else:
                unlocks[gate] = stp
            card = cards[0] if cards else stp
            if not card.find(lambda n: n.has("fb")):
                errs.append(f"第{stp.line}行：互动卡片需要一个 .fb 反馈区")
            if act == "choice":
                if not dev.has("phone"):
                    errs.append(f"第{stp.line}行：choice 互动只能用在对话场景")
                slot = stp.attrs.get("data-slot", "")
                if slot not in idset:
                    errs.append(f"第{stp.line}行：choice 的 data-slot「{slot}」不是本场景的消息")
                opts = card.find(lambda n: n.has("opt") and "data-c" in n.attrs)
                if len(opts) < 2:
                    errs.append(f"第{stp.line}行：choice 至少需要 2 个 .opt[data-c]")
                keys = set()
                for o in opts:
                    keys.add(o.attrs["data-c"])
                    miss = [i for i in _ids(o.attrs.get("data-show", "")) if i not in idset]
                    if miss:
                        errs.append(f"第{o.line}行：选项 data-show 引用了不存在的 data-i：{', '.join(miss)}")
                    if not o.attrs.get("data-fb"):
                        errs.append(f"第{o.line}行：选项缺少 data-fb（发送后的反馈）")
                # 结果卡：紧随其后的步骤，用 <template data-res="选项key"> 写每个选项的解读
                i = st_list.index(stp)
                nxt = st_list[i + 1] if i + 1 < len(st_list) else None
                res = nxt.find(lambda n: n.tag == "template" and "data-res" in n.attrs) if nxt else []
                if not res:
                    errs.append(f"第{stp.line}行：choice 的下一步需要 <template data-res=…> 结果卡")
                else:
                    if not nxt.find(lambda n: n.has("res")):
                        errs.append(f"第{nxt.line}行：结果卡需要一个 <div class=\"res\"> 容器")
                    rk = {t.attrs["data-res"] for t in res}
                    if rk != keys:
                        errs.append(f"第{nxt.line}行：结果卡 data-res {sorted(rk)} 与选项 {sorted(keys)} 不一致")
            if act == "pick":
                if "pick" not in (stp.attrs.get("data-fx") or "").split():
                    errs.append(f"第{stp.line}行：pick 互动需要 data-fx 含 pick")
                bad = dev.find(lambda n: n.has("s") and n.attrs.get("data-ok") == "1")
                if len(bad) != 1:
                    errs.append(f"第{stp.line}行：pick 场景里需要且只能有一句 .s[data-ok=\"1\"]（错句）")
                tk = {t.attrs.get("data-k") for t in card.find(lambda n: n.tag == "template")}
                for k in ("ok", "no"):
                    if k not in tk:
                        errs.append(f"第{stp.line}行：pick 卡片缺少 <template data-k=\"{k}\">")
                if card.find(lambda n: "data-skip" in n.attrs) and "skip" not in tk:
                    errs.append(f"第{stp.line}行：有跳过按钮时需要 <template data-k=\"skip\">")

    # ---- 解锁门：每个 data-gate 都要有对应互动，且互动本身不能被自己的门锁住 ----
    for n in root.find(lambda n: "data-gate" in n.attrs):
        g = n.attrs["data-gate"]
        if g not in unlocks:
            errs.append(f"第{n.line}行：data-gate={g} 没有任何互动步骤解锁它")
        if not n.has("locked"):
            errs.append(f"第{n.line}行：带 data-gate 的元素需要同时带 class locked")
    for g, stp in unlocks.items():
        if stp.up(lambda p: p.attrs.get("data-gate") == g) or stp.attrs.get("data-gate") == g:
            errs.append(f"第{stp.line}行：互动被它自己要解锁的门 {g} 锁住了")
    if steps_all and steps_all[0].has("locked"):
        errs.append("第一步不能是锁定状态")
    return errs


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for p in argv:
        errs = check(Path(p).read_text(encoding="utf-8"))
        if errs:
            bad += 1
            print(f"✗ {p}（{len(errs)} 处问题）")
            for e in errs:
                print("  - " + e)
        else:
            print(f"✓ {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
