---
name: skill-creator
description: 帮用户设计、创建、测试和改进"技能"（Skill）。当用户说"创建/做一个技能""把这个流程保存成技能""修改我的技能"时使用。
---

# skill-creator：创建与改进技能

技能 = 一个目录，包含 `SKILL.md`（说明）和可选的 `scripts/`（Python 脚本）、`references/`（参考文档）、`assets/`（模板等）。
模型平时只看到技能的 name 和 description，需要时才加载全文，所以 **description 决定技能会不会被正确触发**。

## 流程

### 1. 弄清需求（1–2 轮对话即可，不要盘问）
- 这个技能要完成什么任务？输入是什么，输出是什么（文本 / 文件类型）？
- 用户通常会怎么描述这个需求？（用于写 description）
- 有没有固定的格式、模板、业务规则？

### 2. 设计
- name：小写字母、数字、连字符，如 `meeting-minutes`。不能与已有技能重名。
- description：一句话说清"做什么 + 什么时候用"，包含用户可能使用的关键词，≤ 200 字。
- 判断是否需要脚本：输出是文件、需要精确计算、需要固定格式时写脚本；纯写作类技能只要 SKILL.md。

### 3. 编写文件
读取 `references/template.md` 获取 SKILL.md 模板和脚本约定，严格遵守：
- 脚本只能用 Python 标准库和沙箱预装库（python-docx、openpyxl、python-pptx、reportlab、pandas、matplotlib、markdown、jinja2、pypdf、pillow、pyyaml）。
- 沙箱无网络：不要在脚本中访问网络或安装包。
- 输入用命令行参数 / stdin JSON；输出文件写到当前工作目录（文件名不带目录）。
- 技能自带的资源通过 `os.environ["SKILL_DIR"]` 定位。
- 出错时向 stderr 打印清晰的中文原因并以非 0 退出。

### 4. 测试（必须）
先用 `run_python` 把脚本逻辑用一组示例数据跑一遍，确认能正常生成结果；有错误就修正。

### 5. 保存
调用 `create_skill`，files 包含 SKILL.md 和脚本等全部文件。
保存后再用 `run_skill_script` 实际调用一次新技能，确认可用，并把生成的示例文件展示给用户。

### 6. 告知用户
- 技能已创建，**仅你自己可见**；在「技能」页面可以查看、编辑、导出。
- 如果想让所有访客使用，需要站长审核发布。

## 修改已有技能
先 `load_skill` 查看当前内容，再用 `update_skill` 只提交改动的文件。内置/公共技能不能直接改，建议用户在技能页面"复制"一份后再修改。
