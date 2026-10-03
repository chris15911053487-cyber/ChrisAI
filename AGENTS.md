# AGENTS.md

项目概览、目录结构、配置项、API 列表见 `README.md`。这里只记录从代码里不容易看出来的约定。

## 协作约定

- 用中文回复。
- 没让提交就不提交；没让推送就不推送。
- 界面文案改动要小步、保持原有版面，不要顺手重排布局。
- 不要读取或回显 `backend/.env`、`runner/.env`（含密钥，已在 `.gitignore`）。要看配置项看 `backend/.env.example` 和 `backend/app/config.py`。
- `*.mp4` 被 `.gitignore` 忽略，视频不入库；`.pytest_cache/` 目前没有被忽略，提交时别带上。

## 命令与部署

- 本机只有 `python3`，没有 `python`，也没装 pytest 和后端依赖。
- 跑测试要先建临时虚拟环境：
  ```bash
  python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -r backend/requirements.txt pytest
  cd backend && /tmp/venv/bin/python -m pytest tests -q
  ```
- `conftest.py` 让整个测试会话共用一个临时 `DATA_DIR`，测试之间共享数据库：用独立的 owner / 唯一标题，避免互相干扰；改了全局数据（如模型）要在 fixture 里还原。
- 前端没有构建步骤、没有 lint、没有前端测试；纯静态文件，改完直接在浏览器看。
- 部署靠 Docker Compose，三个服务分开构建：
  - 改 `site/` → `docker compose up -d --build website`
  - 改 `backend/` 或 `courses/` 或 `skills/builtin/`（构建时 `COPY` 进镜像）→ `docker compose up -d --build agent-api`
  - 改 `runner/` → 重建 `runner`
- `docs/` 不进任何镜像（`.dockerignore`）；`site/docs/` 才是站点自己的素材。

## 后端约定（`backend/app/`）

- 所有配置来自环境变量，统一在 `config.py` 读取；新增配置项同时补 `backend/.env.example` 和 README 的配置表。密钥只能留在 `agent-api`，绝不能传给 `runner`。
- 数据按 **owner** 隔离：登录用户的 owner 是 `u_{uid}`，匿名访客是 32 位 hex 的 cookie id（`auth.py` 顶部有说明）。新的按用户存储的功能沿用 owner，不要另造身份。
- 需要登录用 `require_user`，管理员接口用 `require_admin`（请求头 `X-Admin-Token`，`hmac.compare_digest` 比较）。写接口加 `write_limit`。
- 业务层抛各自的 `XxxError`（带 `status`），在 `main.py` 里用 `exception_handler` 统一转成 JSON；路由里不要散落 `HTTPException` 来表达业务错误。
- 数据库是单个 SQLite（WAL），`db.py` 里 `_lock` 串行化。**新增列要写进 `_migrate()`**（幂等的 `ALTER TABLE`），只改 `SCHEMA` 不会更新已有的库。
- 新增 Agent 工具要同时改 `tools.py` 的 `TOOLS` 定义和 `_dispatch*`，并在 `describe_call` 里补展示文案；工具是否对本次对话开放由 `tools_for` 决定（例如知识库/帖子工具只在选了来源时才提供）。
- `agent-api` 会拒绝来自 `runner` 的入站请求（`VisitorMiddleware`），沙箱里的脚本不能回连 API，不要绕开。
- 社区帖子正文是用户提交的内容：后端用 `htmlsanitize.py` 净化，前端再过 DOMPurify。两层都要保留。
- 向量/重排服务不可用时必须退回关键词检索，别让检索整体失败。
- 测试：业务层直接测模块（如 `test_posts.py`），接口层用 `TestClient(main.app)`；调 LLM 的地方用 mock，不要在测试里发真实请求。

## 网络与沙箱（`nginx.conf` / `docker-compose.yml` / `runner/`）

- `/api/chat` 是 SSE：nginx 里单独一段，`proxy_buffering off`、读超时 900s、请求体 64k；其余 `/api/` 读超时 120s、请求体 12m（应用层上传上限 10MB，要保持 nginx ≥ 应用层）。改 SSE 相关代码别让中间层缓冲。
- nginx 限流（`/api/` 10r/s、`/api/chat` 5r/m）与应用层配额（`ratelimit.py`）是两层，改其中一层时想一下另一层。
- `sandbox` 网络是 `internal: true`，`runner` 无外网、无密钥；`runner` 用 `read_only` + `cap_drop: ALL`，给它加能力或挂载前要有充分理由。
- `agent-api` 容器以 uid 1000 运行，数据在卷 `agent-data` 的 `/data`（SQLite、工作区、用户技能、知识库文件）。

## 前端约定（`site/`）

- 原生 HTML / CSS / JS，不引入框架和打包工具。第三方库放 `site/vendor/` 并带版本号（marked、DOMPurify、Quill）。
- 设计变量在 `tokens.css`；各页公共的 `auth.js`、`sidebar.js`、`icons.js` 被多页共用，改之前先 grep 都有谁在用。
- **缓存版本号**：每个页面引用自己的 js/css 都带 `?v=YYYYMMDDx`，并且是**每个 HTML 各写一份**。改了某个 js/css，要把所有引用它的 HTML 里的 `?v=` 一起改（`grep -rn "文件名?v=" site/`）。nginx 只对 HTML 设了 no-cache。
- 用户生成内容渲染一律先净化，不要直接 `innerHTML` 拼接用户输入。
- 侧栏宽度固定，文案很容易挤爆；加字前先估算宽度。

## 做新课（故事页）

1. 由 `courses/<课程>/lessons/<课>.md` 改写成 `courses/<课程>/stories/<课>.html`（纯内容片段，不写脚本和样式）。
2. 校验：`cd backend && python3 -m app.storycheck ../courses/ai-intro/stories/<课>.html`，必须输出 ✓。`storycheck` 只需要标准库，可直接运行。
3. 在 `course.yaml` 里给该课设 `story: true` 并填 `minutes.story`。
4. 同步改 `backend/tests/test_courses.py`：里面硬编码了哪些课有故事页（目前只有 `l1`、`l4`，其余断言为「制作中」）。
5. 写法见 `courses/ai-intro/stories/README.md`。参考样例：`l4.html`（覆盖全部组件）、`l1.html`。

## 容易踩的坑

- `course.yaml` 里课程序号用 `seq`，不要用 `no` 当键名（YAML 会解析成布尔 `False`）。
- 改 `site/story.js` 或 `site/story.css` 后，同步改 `site/story.html` 里的 `?v=`；`course.html` 也引用了 `story.css`，要一起改。
- 课程「已完成」状态只升不降（`courses.save`）。要清掉只能 `DELETE .../progress?mode=story&full=true`，这也是前端「重置本课进度」用的接口；不带 `full` 只清阅读状态。
- 故事页的 `story-meta` 里只有 `.t` 是必需的，`.s` 副标题可省略（l1、l4 已去掉）。
- 侧栏只有 264px 宽，底部的快捷键行和「互动 / 重置本课进度」行都按单行设计。
- README 里目录树的个别缩进和实际不符（如 `storycheck.py`），以实际文件为准。
