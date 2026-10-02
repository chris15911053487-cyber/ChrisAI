# 故事页写法（编剧规范）

每节课的故事页是一份**纯内容的 HTML 片段**：`stories/<课id>.html`。动画、对话流式输出、侧栏、手机模式、进度保存、续读、末尾的「完成」页全部由 `site/story.js` 提供。片段里**不写脚本、不写样式**。

上线三步：

1. 由原始文档（`lessons/<课id>.md`）按本规范改写成 `stories/<课id>.html`
2. 校验：`cd backend && python -m app.storycheck ../courses/ai-intro/stories/<课id>.html`，必须是 ✓
3. 在 `course.yaml` 里给这节课设 `story: true`，填上 `minutes.story`，目录就会自动从「制作中」变成可学习

参考样例：`l4.html`（第4课，覆盖了全部组件）。

## 骨架

```html
<header class="story-meta" data-stages="看清问题|掌握方法|落地成长">
  <div class="t">AI用得好不好，<br>差在<em>第二句话</em></div>
  <div class="s">二次对话 · 3 个阶段 · 8 章</div>
</header>

<div class="chapter" data-title="开场" data-stage="0"> … </div>
<div class="chapter" data-no="01" data-title="困境" data-stage="1" data-desc="一句话说明"> … </div>
```

- `story-meta`：侧栏标题（`<em>` 会标黄）。`data-stages` 用 `|` 分隔阶段名。
- `.chapter`：`data-stage` 是从 1 开始的阶段序号，0 表示不属于任何阶段（比如开场）。`data-no` 是章号，有它才会弹出章节提示。`data-badge="选一句"` 会在侧栏显示「互动」徽标。
- 章节下面只能放两种 `<section>`：`.beat`（金句页）和 `.scene`（舞台）。

## 组件

**金句页**：一屏一句话。

```html
<section class="beat st">
  <div class="small reveal">小字引导</div>
  <h2 class="reveal">第一次回答，<br>不是答案，是<em>反馈</em>。</h2>
  <p class="reveal">补充说明（可选）</p>
</section>
```

`h1` 用于课题，`h2` 用于金句，`.dim` 是灰字。还有几个可选块：`.trio`（三栏要点）、`.recap`（两栏清单）、`.opts[data-quick]`（快问快答，每个 `.opt` 用 `data-r` 写回应，回应显示在同一页的 `.reply` 里）。

**对话舞台**：左边是旁白，右边是聊天窗。

```html
<section class="scene">
  <div class="stage"><div class="phone">
    <div class="phone-hd"><b>小林 · 机械专业大四</b></div>   <!-- 身份 -->
    <div class="body">
      <div class="m u" data-i="0">用户说的话（读者点「发送」）</div>
      <div class="m a" data-i="1">AI 的回复（逐字输出）</div>
      <div class="m sys" data-i="2">系统提示</div>
    </div>
  </div></div>
  <div class="steps">
    <div class="step st" data-show="0"><div class="card">旁白…</div></div>
    <div class="step st" data-show="0,1" data-fx="pause"><div class="card">旁白…</div></div>
  </div>
</section>
```

- `data-show`：这一步聊天窗里显示哪些消息，值是 `data-i` 列表。新出现的消息会按顺序播放。
- 消息附加类：`.in`（蓝色，向内）、`.out`（橙色，向外）、`.dull`（灰色斜体，表示无效回复）、`.sys.end`（红色，对话结束）。在 `.phone` 上加 `data-judge`，再给消息加 `.j`，计数会改成「带判断 N 句」。
- `data-fx` 舞台效果，可以多个用空格分隔：
  - `pause`：其余消息变暗，配合 `data-keep="1"` 保留某一条
  - `bad`：给 `.bad-s` 加红色波浪线
  - `hl-k`：高亮 `.k`
  - `pick`：句子可点，用于找错句
  - `split`：白板上的 `data-dir` 着色
  - `steps3`：显示 `.p` 分段编号
- 旁白卡片里：`.who` 是小标题，`.tag-in` / `.tag-out` 是方向色，`.q` 是大问题。

**白板舞台**：没有对话的讲解，比如原因列表、对比、公式。把 `.phone` 换成 `.board`，每个元素带 `data-i`，按 `data-show` 逐个点亮。可用的块有 `.b-hd`、`.tile`（`<b>` 加 `<span>`）、`.dirs`/`.dir.in/.out`、`.same`、`.amp`。

## 互动与解锁

互动步骤写 `data-act` 和 `data-unlock="门号"`。互动后面被锁住的内容写 `class="… locked" data-gate="门号"`。门号在一节课里不能重复。`data-wait` 是互动完成前「下一步」按钮上的提示文字。

**选一句**（`choice`）：读者选一句话，这句话填进聊天窗由读者发送，不同选项走向不同结局。

```html
<div class="step st" data-show="0,1" data-fx="pause" data-act="choice" data-unlock="1" data-slot="c1" data-wait="先选一句你会怎么回 ↑">
  <div class="card"><div class="q">你会怎么回？</div>
    <div class="opts">
      <button class="opt" type="button" data-c="end"  data-show="0,1,c1,c1end" data-fb="发送后的反馈">好的，谢谢！</button>
      <button class="opt" type="button" data-c="good" data-show="0,1,c1" data-cls="in" data-fb="…">好问题</button>
    </div>
    <div class="fb" aria-live="polite"></div>
  </div>
</div>
<div class="step st locked" data-gate="1"><div class="card"><div class="res"></div>
  <template data-res="end">选了 end 之后的解读</template>
  <template data-res="good">选了 good 之后的解读</template>
</div></div>
```

`data-slot` 指向聊天窗里一条空的 `.m u`。每个选项需要写：

- `data-show`：选这个选项后要显示的消息
- `data-cls`：可选，消息颜色
- `data-fb`：发送后的反馈

紧跟着的下一步必须为每个选项写一个 `<template data-res>`。

**找错句**（`pick`）：AI 回复里有一句是错的，读者把它点出来。

```html
<div class="m a" data-i="1">
  <span class="s" tabindex="0" role="button" data-ok="0">对的句子</span>
  <span class="s" tabindex="0" role="button" data-ok="1">错的句子（只能有一句）</span>
</div>
…
<div class="step st" data-show="0,1" data-fx="pick" data-act="pick" data-unlock="2" data-wait="先找出错的那句 ↑">
  <div class="card"><div class="q">有一句不对，你能找到吗？</div>
    <div class="fb" aria-live="polite"></div>
    <button class="opt" type="button" data-skip>我看不出来，直接告诉我</button>
    <template data-k="ok">找对时的反馈</template>
    <template data-k="no">点错时的提示</template>
    <template data-k="skip">直接揭晓时的说明</template>
  </div>
</div>
```

## 改写原则（从原文到故事）

- **先拆章，再写步。** 一章回答一个问题。全课 6–10 章，按「看清问题 → 掌握方法 → 落地成长」这类三段式分阶段。
- **一屏一件事。** 金句页只放一句话，旁白卡片控制在 1–3 句。一个场景 4–7 步，超过就拆成两个场景。
- **案例用舞台演，道理用金句收。** 原文里的人物案例做成对话场景，开头写「现在，你是某某」，结尾接一个金句页。
- **一章最多一个互动。** 互动只放在最关键的判断点，比如「你会怎么回？」「哪句不对？」。互动之后的内容一律加锁。
- **前后呼应。** 开场的困境在后面要被点破，比如「和第 01 章是同一件事」。最后一章给出带走的练习。
- **完成页不用写。** 渲染器会自动追加，里面有工具卡、回目录和下一课的入口。
- **文案只用纯文本和行内标签。** 可以用 `<strong>`、`<em>`、`<br>`、`<span class="…">`，禁止 `<script>`、`<style>`、事件属性和外链资源，校验器会拦下来。
