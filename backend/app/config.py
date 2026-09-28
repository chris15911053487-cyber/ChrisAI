"""集中配置：全部来自环境变量，密钥不写入代码。"""
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ---- 模型 ----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
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

# ---- 限流 / 配额 ----
RATE_IP_PER_MIN = _int("RATE_IP_PER_MIN", 5)             # 每 IP 每分钟对话请求数
VISITOR_DAILY_TURNS = _int("VISITOR_DAILY_TURNS", 10)    # 每访客每天对话轮数
IP_DAILY_TURNS = _int("IP_DAILY_TURNS", 30)              # 每 IP 每天对话轮数（防清 cookie 绕过）
GLOBAL_DAILY_TOKENS = _int("GLOBAL_DAILY_TOKENS", 1_000_000)
RATE_WRITE_PER_MIN = _int("RATE_WRITE_PER_MIN", 20)      # skill 写操作 / 上传

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

COOKIE_NAME = "cl_vid"
