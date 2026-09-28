"""Chris Li · 技能沙箱 runner

只做一件事：接收一段 Python（技能脚本或临时代码）+ 工作区文件，在隔离环境中执行，返回输出和变更的文件。

隔离措施（容器层 + 进程层）：
- 容器：只接入 internal 网络（无外网）、只读根文件系统、无任何密钥、cap 最小化、限制内存/CPU/进程数
- 进程：每个任务使用独立的临时 UID（互相不可读），0700 的任务目录，清空环境变量，
        rlimit 限制 CPU 时间/内存/文件大小/进程数/打开文件数，墙钟超时后杀掉该 UID 的所有进程
- 输出收集：先杀干净任务进程再收集，只读取普通文件（不跟随符号链接）
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import pwd  # noqa: F401  (确保 nss 可用)
import resource
import shutil
import signal
import stat
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s runner: %(message)s")
log = logging.getLogger()

TOKEN = os.environ.get("RUNNER_TOKEN", "")
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/jobs"))
PYTHON = os.environ.get("JOB_PYTHON", "/usr/local/bin/python3")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "3"))
MAX_TIMEOUT = int(os.environ.get("MAX_TIMEOUT", "60"))
MEM_BYTES = int(os.environ.get("JOB_MEM_MB", "1024")) * 1024 * 1024
FSIZE_BYTES = int(os.environ.get("JOB_FSIZE_MB", "50")) * 1024 * 1024
OUTPUT_TOTAL = 60 * 1024 * 1024
OUTPUT_FILES = 200
STREAM_CAP = 64 * 1024
MAX_BODY = 90 * 1024 * 1024
UID_BASE, UID_POOL = 20000, 1000
MPL_TEMPLATE = Path("/opt/mpl")

_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_uid_lock = threading.Lock()
_uids_in_use: set[int] = set()
_next_uid = 0


def _alloc_uid() -> int:
    global _next_uid
    with _uid_lock:
        for _ in range(UID_POOL):
            uid = UID_BASE + _next_uid
            _next_uid = (_next_uid + 1) % UID_POOL
            if uid not in _uids_in_use:
                _uids_in_use.add(uid)
                return uid
    raise RuntimeError("no free uid")


def _free_uid(uid: int) -> None:
    with _uid_lock:
        _uids_in_use.discard(uid)


def _safe_rel(p: str) -> str | None:
    if not p or "\x00" in p or "\\" in p:
        return None
    pp = PurePosixPath(p)
    if pp.is_absolute():
        return None
    parts = [x for x in pp.parts if x not in ("", ".")]
    if not parts or len(parts) > 8 or any(x == ".." or x.startswith(".") for x in parts):
        return None
    return "/".join(parts)


def _materialize(root: Path, files: dict, uid: int | None, mode_file: int, mode_dir: int) -> dict[str, str]:
    """写入文件并返回 {rel: sha256}。"""
    hashes = {}
    for raw, b64 in (files or {}).items():
        rel = _safe_rel(raw)
        if not rel:
            continue
        data = base64.b64decode(b64)
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True, mode=mode_dir)
        p.write_bytes(data)
        os.chmod(p, mode_file)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    # 统一属主/权限
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, mode_dir)
        if uid is not None:
            os.chown(dirpath, uid, uid)
            for f in filenames:
                os.chown(os.path.join(dirpath, f), uid, uid)
    return hashes


def _kill_uid(uid: int) -> None:
    """杀掉属于该 UID 的所有进程（包括 setsid/双 fork 逃逸出的）。"""
    for _ in range(3):
        found = False
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                if os.stat(f"/proc/{pid}").st_uid == uid:
                    os.kill(int(pid), signal.SIGKILL)
                    found = True
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        if not found:
            return
        time.sleep(0.05)


def _read_cap(path: Path) -> str:
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size <= STREAM_CAP:
            data = f.read()
        else:
            head = f.read(STREAM_CAP // 2)
            f.seek(size - STREAM_CAP // 2)
            data = head + f"\n...（省略 {size - STREAM_CAP} 字节）...\n".encode() + f.read()
    return data.decode("utf-8", "replace")


def _collect(work: Path, before: dict[str, str]) -> tuple[dict, list, list]:
    out, notes, total = {}, [], 0
    seen = set()
    for dirpath, dirnames, filenames in os.walk(work, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, work).replace(os.sep, "/")
            if fn.startswith(".") or fn.endswith(".pyc"):
                continue
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                continue  # 跳过符号链接、设备等
            seen.add(rel)
            if len(out) >= OUTPUT_FILES or total + st.st_size > OUTPUT_TOTAL:
                notes.append(f"{rel}: 超出输出上限，未返回")
                continue
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                data = f.read()
            if before.get(rel) == hashlib.sha256(data).hexdigest():
                continue
            out[rel] = base64.b64encode(data).decode()
            total += len(data)
    deleted = [r for r in before if r not in seen]
    return out, deleted, notes


def run_job(req: dict) -> dict:
    entry = req.get("entry") or {}
    timeout = max(1, min(int(req.get("timeout") or MAX_TIMEOUT), MAX_TIMEOUT))
    args = [str(a) for a in (req.get("args") or [])][:50]
    stdin_data = str(req.get("stdin") or "").encode()[: 1024 * 1024]

    uid = _alloc_uid()
    job = JOBS_DIR / uuid.uuid4().hex
    try:
        job.mkdir(mode=0o700)
        os.chown(job, uid, uid)
        os.chmod(job, 0o700)
        work, skill, tmp = job / "work", job / "skill", job / "tmp"
        work.mkdir()
        skill.mkdir()
        tmp.mkdir()
        before = _materialize(work, req.get("work"), uid, 0o600, 0o700)
        _materialize(skill, req.get("skill"), None, 0o444, 0o555)  # 属主 root，只读
        for f in MPL_TEMPLATE.glob("*"):  # 预构建的 matplotlib 字体缓存与中文配置
            if f.is_file():
                shutil.copy(f, tmp / f.name)
                os.chown(tmp / f.name, uid, uid)
        os.chown(tmp, uid, uid)
        os.chmod(tmp, 0o700)
        os.chmod(skill, 0o555)

        if entry.get("type") == "script":
            rel = _safe_rel(str(entry.get("path", "")))
            if not rel or not (skill / rel).is_file():
                return {"exit_code": -1, "stdout": "", "stderr": "脚本不存在", "duration": 0, "timed_out": False, "files": {}, "deleted": []}
            cmd = [PYTHON, "-I", "-B", str(skill / rel), *args]
        elif entry.get("type") == "code":
            code_file = job / "main.py"
            code_file.write_text(str(entry.get("code", "")), "utf-8")
            os.chmod(code_file, 0o444)
            cmd = [PYTHON, "-I", "-B", str(code_file), *args]
        else:
            return {"exit_code": -1, "stdout": "", "stderr": "未知 entry 类型", "duration": 0, "timed_out": False, "files": {}, "deleted": []}

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(work),
            "TMPDIR": str(tmp),
            "SKILL_DIR": str(skill),
            "WORK_DIR": str(work),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(tmp),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }

        def preexec():
            os.setsid()
            os.umask(0o077)
            resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
            resource.setrlimit(resource.RLIMIT_AS, (MEM_BYTES, MEM_BYTES))
            resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_BYTES, FSIZE_BYTES))
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)

        out_f, err_f = job / "stdout", job / "stderr"
        t0 = time.monotonic()
        timed_out = False
        with open(out_f, "wb") as so, open(err_f, "wb") as se:
            p = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.PIPE, stdout=so, stderr=se,
                                 preexec_fn=preexec, close_fds=True)
            try:
                p.communicate(stdin_data, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _kill_uid(uid)
                p.wait()
        duration = time.monotonic() - t0

        files, deleted, notes = _collect(work, before)
        stderr = _read_cap(err_f)
        if notes:
            stderr += "\n[runner] " + "; ".join(notes)
        code = p.returncode
        if code is not None and code < 0 and not timed_out:
            sig = -code
            stderr += f"\n[runner] 进程被信号 {signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else sig} 终止（可能超出 CPU/内存限制）"
        return {
            "exit_code": code, "stdout": _read_cap(out_f), "stderr": stderr, "duration": round(duration, 2),
            "timed_out": timed_out, "files": files, "deleted": deleted,
        }
    finally:
        _kill_uid(uid)
        shutil.rmtree(job, ignore_errors=True)
        _purge_tmp(uid)
        _free_uid(uid)


def _purge_tmp(uid: int) -> None:
    """删除任务写到共享 /tmp、/dev/shm 的残留文件。"""
    for base in ("/tmp", "/dev/shm"):
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for e in entries:
            try:
                if e.stat(follow_symlinks=False).st_uid != uid:
                    continue
                if e.is_dir(follow_symlinks=False):
                    shutil.rmtree(e.path, ignore_errors=True)
                else:
                    os.unlink(e.path)
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "runner"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/run":
            return self._json(404, {"detail": "not found"})
        if not TOKEN or not hmac.compare_digest(self.headers.get("X-Runner-Token", ""), TOKEN):
            return self._json(401, {"detail": "unauthorized"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"detail": "body too large"})
        try:
            req = json.loads(self.rfile.read(n))
        except ValueError:
            return self._json(400, {"detail": "bad json"})
        if not _sem.acquire(timeout=20):
            return self._json(503, {"detail": "busy"})
        try:
            t = time.monotonic()
            res = run_job(req)
            log.info("job %s exit=%s timeout=%s %.1fs files=%d", (req.get("entry") or {}).get("type"),
                     res["exit_code"], res["timed_out"], time.monotonic() - t, len(res["files"]))
            self._json(200, res)
        except Exception:
            log.exception("job failed")
            self._json(500, {"detail": "runner error"})
        finally:
            _sem.release()


def main():
    if os.geteuid() != 0:
        raise SystemExit("runner 需要以 root 启动（用于切换到每个任务的临时 UID）")
    if not TOKEN:
        raise SystemExit("缺少 RUNNER_TOKEN")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(JOBS_DIR, 0o711)
    for d in JOBS_DIR.iterdir():
        shutil.rmtree(d, ignore_errors=True)
    srv = ThreadingHTTPServer(("0.0.0.0", 9000), Handler)
    srv.daemon_threads = True
    log.info("runner listening on :9000, concurrency=%d", MAX_CONCURRENT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
