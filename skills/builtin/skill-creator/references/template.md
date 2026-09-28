# SKILL.md 模板

```markdown
---
name: my-skill
description: 做什么 + 什么时候用。包含用户常用说法作为关键词。
---

# my-skill：一句话标题

## 流程
1. 需要从用户那里获得哪些信息
2. 调用 `run_skill_script`：
   - name: `my-skill`
   - script: `scripts/main.py`
   - args: `["输出文件名.xxx"]`
   - stdin: JSON 规格（见下）
3. 成功后如何向用户说明结果

## 输入规格
（给出 JSON 示例和每个字段的含义）

## 注意事项
（业务规则、格式要求、常见错误）
```

# 脚本模板（scripts/main.py）

```python
"""一句话说明。用法：python main.py 输出文件名 [--spec spec.json]；默认从 stdin 读 JSON。"""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output")
    ap.add_argument("--spec")
    a = ap.parse_args()
    out = os.path.basename(a.output)          # 只写到当前工作目录
    raw = open(a.spec, encoding="utf-8").read() if a.spec else sys.stdin.read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"输入不是合法 JSON：{e}")   # 非 0 退出 + 中文原因

    # 需要技能自带资源时：
    # tpl = os.path.join(os.environ["SKILL_DIR"], "assets", "template.docx")

    # ... 生成 out ...

    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
```

# 约束清单
- 文件路径：只允许相对路径，片段不能以 `.` 开头，不能包含 `..`
- 允许的文件类型：.md .txt .py .json .yaml .yml .csv .html .css .js .xml .sql .toml .ini .cfg（文本）；模板图片等二进制文件请用户在技能页面上传 zip
- 单文件 ≤ 1 MB，整个技能 ≤ 4 MB，最多 50 个文件
- 每位访客最多 20 个技能
- 执行超时 60 秒，内存约 1 GB，无网络
