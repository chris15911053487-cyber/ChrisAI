---
name: docx-report
description: 生成排版规范的 Word（.docx）文档，如报告、方案、会议纪要、说明书。支持标题层级、段落、列表、表格、引用、分页和封面信息。用户要求"写一份 Word/文档/报告并导出"时使用。
---

# docx-report：生成 Word 文档

## 流程
1. 先和用户确认（或自行合理推断）文档主题、结构、篇幅。
2. 把文档内容组织成下面的 JSON 规格。
3. 调用 `run_skill_script`：
   - name: `docx-report`
   - script: `scripts/build_docx.py`
   - args: `["输出文件名.docx"]`（文件名用中文或英文均可，不要带目录）
   - stdin: JSON 规格字符串
4. 执行成功后，用一两句话告诉用户文档包含哪些内容。文件会自动出现在下载卡片里。

如果 JSON 很长，也可以先用 `write_file` 写到 `spec.json`，再用 args `["输出.docx", "--spec", "spec.json"]`。

## JSON 规格
```json
{
  "title": "文档标题",
  "subtitle": "可选副标题",
  "author": "可选作者",
  "date": "可选日期，缺省为今天",
  "toc": false,
  "blocks": [
    {"type": "heading", "text": "一、背景", "level": 1},
    {"type": "paragraph", "text": "普通段落，支持 **加粗** 和 *斜体*。"},
    {"type": "bullets", "items": ["要点一", "要点二"]},
    {"type": "numbered", "items": ["步骤一", "步骤二"]},
    {"type": "table", "headers": ["列1", "列2"], "rows": [["a", "b"], ["c", "d"]], "caption": "可选表题"},
    {"type": "quote", "text": "引用或提示框"},
    {"type": "image", "path": "chart.png", "width_cm": 14, "caption": "可选图题"},
    {"type": "pagebreak"}
  ]
}
```

说明：
- heading 的 level 取 1–3。
- image 的 path 是会话工作区中的文件（例如之前用 run_python + matplotlib 生成的图表）。
- 每个 paragraph 是一段；不要把整篇文章塞进一个 paragraph。
- 中文排版默认使用微软雅黑 / 宋体回退，正文 11pt，1.5 倍行距。
