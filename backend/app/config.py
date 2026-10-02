"""集中配置：全部来自环境变量，密钥不写入代码。"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ---- 对话模型 ----
# 对话模型在「设置」页管理（models 表，支持任意 OpenAI 兼容接口）。
# 以下 DEEPSEEK_* 仅用于：首次启动时自动创建默认模型；DEEPSEEK_API_KEY 可被模型的"环境变量 Key"引用。
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
# 思考模式：disabled（默认，更快更省，适合工具调用为主的场景）| enabled
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
if DEEPSEEK_THINKING not in ("enabled", "disabled"):
    DEEPSEEK_THINKING = "disabled"
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")  # low | high | max（仅思考模式）
LLM_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
LLM_RETRIES = _int("LLM_RETRIES", 2)  # 429 / 5xx / 网络错误时的重试次数（仅在尚未输出任何内容时）
PERSONA_PROMPT = os.getenv(
    "AGENT_SYSTEM_PROMPT",
    "你是 Chris Li 的企业 AI 助手，擅长 SAP Business One、企业数据、知识库与 AI Agent 落地。"
    "回答用简体中文，专业、简洁、务实，聚焦企业如何真正用好 AI。",
)

# ---- 沙箱 runner ----
RUNNER_URL = os.getenv("RUNNER_URL", "http://runner:9000").rstrip("/")
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "")
RUNNER_HOST = os.getenv("RUNNER_HOST", "runner")  # 用于拒绝来自沙箱网络的入站请求
RUN_TIMEOUT = _int("RUN_TIMEOUT", 60)

# ---- 管理员 ----
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# ---- 存储 ----
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "agent.db"
WORKSPACES_DIR = DATA_DIR / "workspaces"
SKILLS_PUBLIC_DIR = DATA_DIR / "skills" / "public"
SKILLS_USERS_DIR = DATA_DIR / "skills" / "users"
SKILLS_BUILTIN_DIR = Path(os.getenv("SKILLS_BUILTIN_DIR", "/skills/builtin"))
COURSES_DIR = Path(os.getenv("COURSES_DIR", "/courses"))          # 内置课程内容（随镜像发布，只读）

# ---- 限流 / 配额 ----
RATE_IP_PER_MIN = _int("RATE_IP_PER_MIN", 5)             # 每 IP 每分钟对话请求数
VISITOR_DAILY_TURNS = _int("VISITOR_DAILY_TURNS", 10)    # 匿名访客每天对话轮数
USER_DAILY_TURNS = _int("USER_DAILY_TURNS", 30)          # 登录用户每天对话轮数（用户表 daily_turns 可覆盖）
IP_DAILY_TURNS = _int("IP_DAILY_TURNS", 30)              # 每 IP 每天对话轮数（防清 cookie 绕过）
GLOBAL_DAILY_TOKENS = _int("GLOBAL_DAILY_TOKENS", 1_000_000)
RATE_WRITE_PER_MIN = _int("RATE_WRITE_PER_MIN", 20)      # skill 写操作 / 上传

# ---- 配额日切时区（"每日"按此时区的自然日计算；也用于向用户提示恢复时间） ----
QUOTA_TZ = os.getenv("QUOTA_TZ", "Asia/Shanghai")
try:
    QUOTA_TZINFO = ZoneInfo(QUOTA_TZ)
except (ZoneInfoNotFoundError, ValueError):
    QUOTA_TZ = "Asia/Shanghai"
    QUOTA_TZINFO = ZoneInfo("Asia/Shanghai")
QUOTA_TZ_LABEL = os.getenv("QUOTA_TZ_LABEL", "北京时间")  # 面向用户的时区名

# ---- 登录鉴权 ----
# 会话 cookie 的 HMAC 签名密钥；留空时自动用 ADMIN_TOKEN 兜底，仍建议显式设置
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
SESSION_COOKIE = os.getenv("SESSION_COOKIE", "cl_sess")
SESSION_TTL_DAYS = _int("SESSION_TTL_DAYS", 30)          # 登录态有效期

# ---- Agent ----
MAX_TOOL_ROUNDS = _int("MAX_TOOL_ROUNDS", 10)
MAX_HISTORY_MESSAGES = _int("MAX_HISTORY_MESSAGES", 60)
MAX_USER_MESSAGE_CHARS = _int("MAX_USER_MESSAGE_CHARS", 8000)
TOOL_RESULT_MAX_CHARS = _int("TOOL_RESULT_MAX_CHARS", 12000)

# ---- 文件 / skill 限制 ----
WORKSPACE_QUOTA_MB = _int("WORKSPACE_QUOTA_MB", 100)
UPLOAD_MAX_MB = _int("UPLOAD_MAX_MB", 10)
FILE_TTL_DAYS = _int("FILE_TTL_DAYS", 7)
SKILL_MAX_FILES = _int("SKILL_MAX_FILES", 50)
SKILL_MAX_FILE_KB = _int("SKILL_MAX_FILE_KB", 1024)
SKILL_MAX_TOTAL_KB = _int("SKILL_MAX_TOTAL_KB", 4096)
MAX_USER_SKILLS = _int("MAX_USER_SKILLS", 20)

# ---- 知识库 ----
KB_DIR = DATA_DIR / "kb"                                   # 原始文件：KB_DIR/<kb_id>/<doc_id><ext>
KB_MAX_PER_VISITOR = _int("KB_MAX_PER_VISITOR", 5)         # 每访客知识库数
KB_MAX_DOCS = _int("KB_MAX_DOCS", 100)                     # 每知识库文档数
KB_VISITOR_QUOTA_MB = _int("KB_VISITOR_QUOTA_MB", 50)      # 每访客原始文件总大小
KB_DOC_MAX_CHARS = _int("KB_DOC_MAX_CHARS", 500_000)       # 单文档解析后字符上限
KB_CHUNK_CHARS = _int("KB_CHUNK_CHARS", 600)               # 切片长度
KB_CHUNK_OVERLAP = _int("KB_CHUNK_OVERLAP", 80)            # 长段落切分重叠
KB_MAX_PER_SESSION = _int("KB_MAX_PER_SESSION", 5)         # 单会话可同时选择的知识库数

# ---- 社区帖子 ----
# 固定分类列表（管理员预设）；逗号分隔，可用环境变量覆盖。标签由作者自由填写。
POST_CATEGORIES = tuple(
    c.strip() for c in os.getenv("POST_CATEGORIES", "AI 教程,实践笔记,行业观察,工具推荐,随笔").split(",")
    if c.strip()
)
POST_MAX_PER_USER = _int("POST_MAX_PER_USER", 50)          # 每用户帖子数上限
POST_TITLE_MAX = _int("POST_TITLE_MAX", 120)               # 标题字符上限
POST_BODY_MAX_CHARS = _int("POST_BODY_MAX_CHARS", 100_000) # 正文字符上限
POST_HTML_MAX_CHARS = _int("POST_HTML_MAX_CHARS", 500_000) # 富文本 HTML 字符上限（含标签，故更宽松）
POST_IMAGE_MAX_MB = _int("POST_IMAGE_MAX_MB", 5)           # 帖子内嵌图片单张上限
POST_IMAGES_DIR = DATA_DIR / "post_images"                 # 帖子图片存储目录
POST_MAX_TAGS = _int("POST_MAX_TAGS", 8)                   # 每帖标签数上限
POST_TAG_MAX = _int("POST_TAG_MAX", 20)                    # 单标签字符上限
POST_SEARCH_LIMIT = _int("POST_SEARCH_LIMIT", 8)           # 对话检索返回的帖子片段数

# ---- 向量检索 / 重排（OpenAI 兼容接口，如硅基流动、阿里云百炼、自托管 TEI；不配置则只用关键词检索） ----
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
# 查询前缀：Qwen3-Embedding 需要给查询加指令，文档不加；留空则按模型名自动判断
EMBEDDING_QUERY_INSTRUCT = os.getenv("EMBEDDING_QUERY_INSTRUCT", "")
EMBEDDING_BATCH = _int("EMBEDDING_BATCH", 32)
EMBEDDING_DAILY_TOKENS = _int("EMBEDDING_DAILY_TOKENS", 5_000_000)  # 全站每日 embedding + rerank token 上限
RERANK_BASE_URL = os.getenv("RERANK_BASE_URL", "").rstrip("/")   # 留空复用 EMBEDDING_BASE_URL
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")                  # 留空复用 EMBEDDING_API_KEY
RERANK_MODEL = os.getenv("RERANK_MODEL", "")               # 例如 BAAI/bge-reranker-v2-m3；留空不重排
KB_RECALL = _int("KB_RECALL", 30)                          # 每路召回数（关键词 / 向量），融合后再重排

COOKIE_NAME = "cl_vid"
