"""Shared helpers for the real-world benchmark: disposable copy, planted bugs, real test runs.

Safety rules (enforced here, not just documented):
  - The ORIGINAL repository is only ever read. All edits happen in a fresh temp directory.
  - Secret-looking files, databases, virtualenvs, VCS data and caches are never copied.
  - Tests run in the copy with a scrubbed environment (no inherited tokens/credentials) and no network use by design
    (the project's tests mock all I/O).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ORIGINAL = Path(__file__).resolve().parents[2]            # .../backend
PY = ORIGINAL / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
COPY_DIRS = ("app", "tests", "benchmarks/sample_project")
COPY_FILES = ("requirements.txt", "run.py")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "data", ".git", "node_modules"}
SECRET_NAMES = {".env", "id_rsa", ".npmrc", ".pypirc", ".netrc", "credentials.json", "secrets.json"}
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")


def is_secret(name: str) -> bool:
    low = name.lower()
    return low in SECRET_NAMES or low.endswith(SECRET_SUFFIXES) or low.startswith(".env")


def make_copy(dest_parent: Path | None = None) -> Path:
    """Copy the project's backend into a brand-new temp dir and return the copy root. Never writes to ORIGINAL."""
    root = Path(tempfile.mkdtemp(prefix="phoenix-realworld-", dir=dest_parent)) / "project"
    root.mkdir()
    skipped = []

    def ignore(d, names):
        bad = [n for n in names if n in SKIP_DIRS or is_secret(n) or n.endswith(".pyc")]
        skipped.extend(os.path.join(d, n) for n in bad)
        return bad

    for rel in COPY_DIRS:
        shutil.copytree(ORIGINAL / rel, root / rel, ignore=ignore)
    for f in COPY_FILES:
        shutil.copy2(ORIGINAL / f, root / f)
    return root


def scrubbed_env() -> dict:
    keep = {"SYSTEMROOT", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT", "WINDIR"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PHOENIX_DATA_DIR"] = str(Path(tempfile.gettempdir()) / "phoenix-realworld-data")
    return env


def run_tests(root: Path, *args: str, timeout: int = 300) -> dict:
    """Run the project's REAL pytest suite inside the copy. Returns {ok, passed, failed, output, seconds}."""
    import time
    t0 = time.time()
    r = subprocess.run([str(PY), "-m", "pytest", "-q", "-x" if "-x" in args else "--maxfail=50", "-p", "no:cacheprovider",
                        *[a for a in args if a != "-x"]], cwd=root, capture_output=True, text=True, timeout=timeout,
                       env=scrubbed_env())
    out = r.stdout + r.stderr
    import re
    m = re.search(r"(\d+) passed", out)
    f = re.search(r"(\d+) failed", out)
    failed_ids = re.findall(r"^FAILED (\S+)", out, re.M)
    return {"ok": r.returncode == 0, "passed": int(m.group(1)) if m else 0, "failed": int(f.group(1)) if f else 0,
            "failed_ids": failed_ids, "output": out[-3500:], "seconds": round(time.time() - t0, 1)}
