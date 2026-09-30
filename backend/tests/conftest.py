"""测试环境：独立的临时 DATA_DIR，必须在导入 app 之前设置。"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="agent-test-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("SKILLS_BUILTIN_DIR", str(Path(__file__).resolve().parents[2] / "skills" / "builtin"))
os.environ.setdefault("RUNNER_URL", "http://runner.invalid:9000")
os.environ.setdefault("RUNNER_HOST", "runner.invalid")
os.environ["RATE_WRITE_PER_MIN"] = "1000"
os.environ["ADMIN_TOKEN"] = "test-admin"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
