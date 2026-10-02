# ChrisAI

Chris Li 的个人网站 + 企业级 AI Agent 平台。前端是一个静态展示站点，后端是一个基于 DeepSeek 的 AI Agent 服务，支持多轮工具调用、技能（Skill）系统，以及一个高度隔离的脚本执行沙箱。

整套系统通过 Docker Compose 编排，分为三个相互隔离的服务：静态站点、Agent API、脚本沙箱 Runner。

---

## 功能特性

- **个人展示站点**：介绍、能力、AI Agent 专区、联系方式（`site/`，nginx 提供）。
- **多模型**：对话模型在「设置」页管理，支持任意 OpenAI 兼容接口（DeepSeek、通义千问、Kimi、GLM、豆包、OpenAI、OpenRouter 等），可设默认模型，访客在对话框中按会话切换。
- **AI Agent 对话**：`/api/chat` 基于 SSE 流式返回，支持多轮工具调用（最多 `MAX_TOOL_ROUNDS` 轮）。
- **技能（Skill）系统**：内置技能（`skill-creator`、`xlsx-builder`、`docx-report`），并支持访客创建、编辑、导入导出、复制自己的技能，管理员可审核发布为公共技能。
- **安全沙箱执行**：技能脚本在独立的 Runner 容器中运行——无外网、无密钥、只读根文件系统、每任务独立 UID、内存/CPU/进程数受限。
- **知识库**：上传 PDF / Word / Excel / PPT / Markdown 等文档，对话中选择知识库后 AI 先检索再回答并标注出处。检索为关键词（SQLite FTS5）+ 语义向量混合，RRF 融合后可选 reranker 重排；向量服务不可用时自动退回关键词检索。
- **社区帖子**：登录用户发布文章 / 笔记（Markdown），先经管理员审核后公开；分类为管理员预设固定列表，标签自由填写。帖子可在对话中作为参考来源（关键词检索，与知识库并列、可按分类勾选）。正文渲染经 DOMPurify 净化，防止 XSS。
- **AI 课程**：内置两门课程。「AI入门」6 节课分四个阶段（关系 → 认知 → 方法 → 能力）。在课程列表点「AI入门」会在新标签页打开深色课程目录（`course.html`），每节课用滚动叙事的**故事页**（`story.html`）播放，包括全屏金句、对话舞台、白板、互动解锁和手机端点击推进，学完收集工具卡。还没做好故事页的课在目录里显示「制作中」。「AI知识普及」筹备中。未登录可以浏览目录；进入课程、查看工具卡要点和学习进度需要登录，进度按账号保存，可续读。故事页由固定渲染器（`site/story.js`）播放纯内容片段（`courses/<课程>/stories/<课>.html`）；做新课只需写片段、跑校验器、在清单里打开开关，不用改代码。写法见 `courses/ai-intro/stories/README.md`。
- **会话持久化**：按匿名访客 cookie 隔离会话，数据存于 SQLite。
- **文件工作区**：每会话独立工作区，支持文件上传/下载，带配额与 TTL 自动清理。
- **多层限流与配额**：nginx 层 + 应用层（每 IP 每分钟、每访客每日轮数、全局每日 token 上限等）。

## 架构

```
┌─────────────┐      ┌──────────────┐       ┌──────────────┐
│  website    │      │  agent-api   │       │   runner     │
│  (nginx)    │─────▶│  (FastAPI)   │──────▶│  (sandbox)   │
│  :8015→:80  │ /api │   :8000      │  HTTP │   :9000      │
└─────────────┘      └──────────────┘       └──────────────┘
       静态站点          Agent / 技能 / 会话        脚本执行沙箱
                              │
                              ▼
                        DeepSeek API
```

- `website` 与 `agent-api` 在 `web` 网络；`agent-api` 与 `runner` 在 `sandbox` 网络。
- `sandbox` 网络 `internal: true`，Runner **没有到外网的路由**，也拿不到任何密钥。
- nginx 将 `/api/chat`（SSE，单独限流）与 `/api/*`（会话/文件/技能）反向代理到 `agent-api`。

## 目录结构

```
ChrisAI/
├── docker-compose.yml       # 三服务编排
├── Dockerfile               # 站点镜像（nginx）
├── nginx.conf               # 站点与 API 反向代理、限流、安全头
├── site/                    # 静态前端（index.html / agent.html / skills.html 等；course.html 课程目录、story.html + story.js/css 故事页渲染器）
├── backend/                 # Agent API（FastAPI）
│   ├── app/
│   │   ├── main.py          # 路由：/api/chat /api/sessions /api/files /api/skills /api/admin
│   │   ├── agent.py         # Agent 主循环与工具调用
│   │   ├── llm.py           # DeepSeek 客户端
│   │   ├── skills.py        # 技能管理
│   │   ├── knowledge.py     # 知识库：解析/切片/检索
│   │   ├── posts.py         # 社区帖子：状态机/审核/检索
│   │   ├── courses.py       # 内置课程：清单加载、登录可见范围、学习进度
│   ├── storycheck.py    # 故事页片段校验（安全 + 结构），python -m app.storycheck <文件>
│   │   ├── tools.py         # 工具定义
│   │   ├── workspace.py     # 会话工作区与文件
│   │   ├── ratelimit.py     # 限流与配额
│   │   ├── db.py            # SQLite
│   │   └── config.py        # 全部配置来自环境变量
│   ├── requirements.txt
│   └── .env.example
├── runner/                  # 脚本执行沙箱
│   ├── runner.py
│   ├── Dockerfile
│   └── .env.example
├── courses/                 # 内置课程内容（随 agent-api 镜像发布）
│   ├── ai-intro/            # AI入门：course.yaml 清单 + stories/*.html 故事页（+ README 编剧规范）+ lessons/*.md 原始文档 + videos/
│   └── ai-knowledge/        # AI知识普及（筹备中）：course.yaml + drafts/ 素材
├── skills/builtin/          # 内置技能
│   ├── skill-creator/
│   ├── xlsx-builder/
│   └── docx-report/
└── docs/                    # 说明文档与素材
```

## 快速开始

前置要求：Docker 与 Docker Compose。

1. 克隆仓库

   ```bash
   git clone git@github.com:chris15911053487-cyber/ChrisAI.git
   cd ChrisAI
   ```

2. 配置环境变量

   ```bash
   cp backend/.env.example backend/.env
   cp runner/.env.example runner/.env
   ```

   编辑 `backend/.env`，至少填入：

   - `DEEPSEEK_API_KEY`：你的 DeepSeek API 密钥
   - `RUNNER_TOKEN`：Runner 通信令牌，`openssl rand -hex 32` 生成
   - `ADMIN_TOKEN`：管理员令牌（发布公共技能时使用）

   将同一个 `RUNNER_TOKEN` 写入 `runner/.env`：

   ```bash
   token=$(openssl rand -hex 32)
   echo "RUNNER_TOKEN=$token" >> backend/.env
   echo "RUNNER_TOKEN=$token"  > runner/.env
   ```

3. 启动

   ```bash
   docker compose up -d --build
   ```

4. 访问 `http://localhost:8015`（可在 `docker-compose.yml` 中修改左侧端口）。

## 配置项

所有后端配置均来自环境变量（见 `backend/app/config.py`），密钥不写入代码。常用项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥；首次启动时据此创建默认模型，也可被模型的"从 .env 读取"引用。其他模型在「设置」页配置 | — |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名（`deepseek-chat` 已于 2026-07 下线） | `deepseek-flash` |
| `DEEPSEEK_THINKING` | 思考模式 `enabled` / `disabled` | `disabled` |
| `DEEPSEEK_REASONING_EFFORT` | 思考强度 `low` / `high` / `max`（仅思考模式） | `high` |
| `LLM_RETRIES` | 429 / 5xx / 网络错误重试次数 | `2` |
| `EMBEDDING_API_KEY` | 向量服务密钥；不填则知识库只用关键词检索 | — |
| `EMBEDDING_BASE_URL` | OpenAI 兼容 `/embeddings` 地址 | `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | 向量模型 | `BAAI/bge-m3` |
| `RERANK_MODEL` | 重排模型（留空不重排），如 `BAAI/bge-reranker-v2-m3` | — |
| `EMBEDDING_DAILY_TOKENS` | 全站每日向量 + 重排 token 上限 | `5000000` |
| `AGENT_SYSTEM_PROMPT` | 自定义系统提示词 | 内置企业 AI 助手人设 |
| `RUNNER_TOKEN` | Runner 通信令牌（必填） | — |
| `ADMIN_TOKEN` | 管理员令牌 | — |
| `RATE_IP_PER_MIN` | 每 IP 每分钟对话请求数 | `5` |
| `VISITOR_DAILY_TURNS` | 每访客每日对话轮数 | `10` |
| `IP_DAILY_TURNS` | 每 IP 每日对话轮数 | `30` |
| `GLOBAL_DAILY_TOKENS` | 全局每日 token 上限 | `1000000` |
| `MAX_TOOL_ROUNDS` | 单次对话最大工具调用轮数 | `10` |
| `WORKSPACE_QUOTA_MB` | 每会话工作区配额 | `100` |
| `UPLOAD_MAX_MB` | 单文件上传上限 | `10` |
| `FILE_TTL_DAYS` | 工作区文件保留天数 | `7` |
| `POST_CATEGORIES` | 社区帖子固定分类（逗号分隔，管理员预设） | `AI 教程,实践笔记,行业观察,工具推荐,随笔` |
| `POST_MAX_PER_USER` | 每用户帖子数上限 | `50` |
| `POST_BODY_MAX_CHARS` | 帖子正文字符上限 | `100000` |

## API 概览

| 路由 | 说明 |
|------|------|
| `POST /api/chat` | Agent 对话（SSE 流式），支持技能加载、沙箱执行、生成文件、创建技能 |
| `/api/sessions` | 会话持久化（按访客 cookie 隔离） |
| `/api/files` | 会话工作区文件下载 / 上传 |
| `/api/skills` | 技能列表、查看、创建、编辑、删除、导入导出、复制 |
| `/api/kb` | 知识库：创建、上传文档、查看片段、检索测试；`PUT /api/sessions/{id}/kbs` 为会话选择知识库 |
| `/api/posts` | 社区帖子：列表、详情、创建、编辑、提交审核、我的帖子；`PUT /api/sessions/{id}/post-cats` 选择帖子来源分类 |
| `/api/courses` | 内置课程：列表与课程目录（公开）；`/lessons/{id}/story`（故事页片段 + 阅读进度）、`/text`、`/video` 与 `/progress` 需登录 |
| `/api/models` | 对话框可选的模型列表（不含地址与 Key） |
| `/api/admin` | 管理员：模型配置、发布访客技能、公开知识库、审核帖子（需 `X-Admin-Token`） |

## 安全设计

- **沙箱隔离**：Runner 容器 `read_only`、`cap_drop: ALL`、`no-new-privileges`、`internal` 网络无外网、每任务独立 UID，内存/CPU/PID 受限。
- **密钥隔离**：密钥仅存在于 `agent-api`，Runner 完全拿不到。
- **入站隔离**：`agent-api` 拒绝来自沙箱网络的入站请求（防止脚本回连）。
- **限流**：nginx（`10r/s` API、`5r/m` 对话）+ 应用层多维配额。
- **上传限制**：文件大小、技能文件数量与总大小均有上限。
- **UGC 净化**：社区帖子正文为用户提交的 Markdown，渲染时经 DOMPurify 净化（净化库不可用时退回转义纯文本），防止 XSS。
- `.env` 文件已在 `.gitignore` 中，不会被提交。

## 技术栈

- **前端**：原生 HTML / CSS / JS，nginx 提供静态资源
- **后端**：Python 3 · FastAPI · Uvicorn · httpx · Pydantic · PyYAML · NumPy
- **模型**：DeepSeek（对话）；可选任意 OpenAI 兼容的 embedding / rerank 服务（知识库语义检索）
- **存储**：SQLite
- **编排**：Docker Compose

## 许可

私有项目，未经授权请勿分发。
