"""Safe, read-mostly access to an authorized project directory.

Every path the API accepts is a *relative* path that is resolved (following symlinks) and verified to stay
inside the project root. Secrets, VCS metadata and dependency/build folders are filtered out. File contents
are treated as untrusted data.
"""
from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", ".next", ".idea", ".vscode", "target", "coverage", ".tox", ".cache", ".phoenix",
}
SENSITIVE_NAMES = {".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc",
                   "credentials", "credentials.json", "secrets.json", ".htpasswd"}
SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".kdbx", ".jks", ".keystore", ".db", ".sqlite", ".sqlite3")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z", ".exe",
                   ".dll", ".so", ".dylib", ".pyc", ".class", ".jar", ".woff", ".woff2", ".ttf", ".mp3", ".mp4",
                   ".mov", ".bin", ".lock", ".map"}
MAX_FILE_BYTES = 300_000
MAX_WALK_FILES = 5000
STOPWORDS = set("""the a an and or of to in is it this that for with on as by be are was how what why does do where which
from can you me my i code file function class method explain bug bugs find show tell about work works""".split())


class WorkspaceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def is_sensitive(name: str) -> bool:
    low = name.lower()
    return low in SENSITIVE_NAMES or low.endswith(SENSITIVE_SUFFIXES) or low.startswith(".env.")


class Workspace:
    def __init__(self, root: str | Path):
        try:
            self.root = Path(root).resolve(strict=True)
        except (OSError, RuntimeError) as e:
            raise WorkspaceError(400, f"Folder does not exist: {root}") from e
        if not self.root.is_dir():
            raise WorkspaceError(400, "Workspace must be a folder")
        if self.root.parent == self.root:
            raise WorkspaceError(400, "Refusing to authorize a drive root; pick a project folder")

    # ---- boundary enforcement
    def resolve(self, rel: str, *, must_exist: bool = True, allow_root: bool = True) -> Path:
        if rel is None or "\x00" in rel:
            raise WorkspaceError(400, "Invalid path")
        rel = rel.replace("\\", "/").strip()
        if rel in ("", ".", "/"):
            if not allow_root:
                raise WorkspaceError(400, "A file path is required")
            return self.root
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ":" in rel:
            raise WorkspaceError(403, "Absolute paths and drive/stream syntax are not allowed")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise WorkspaceError(403, "Path escapes the workspace")
        for part in parts:
            if part.lower() in IGNORED_DIRS:
                raise WorkspaceError(403, f"'{part}' is excluded from the workspace")
        if is_sensitive(parts[-1]):
            raise WorkspaceError(403, "Access to secret/credential files is blocked")
        target = (self.root / Path(*parts)).resolve(strict=False)
        if not target.is_relative_to(self.root):
            raise WorkspaceError(403, "Path escapes the workspace (symlink or traversal)")
        if must_exist and not target.exists():
            raise WorkspaceError(404, f"Not found: {rel}")
        return target

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    # ---- exploration
    def list_dir(self, rel: str = "") -> list[dict]:
        d = self.resolve(rel)
        if not d.is_dir():
            raise WorkspaceError(400, "Not a directory")
        out = []
        for e in sorted(os.scandir(d), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            if e.name.lower() in IGNORED_DIRS and e.is_dir(follow_symlinks=False) or is_sensitive(e.name):
                continue
            if e.is_symlink():
                continue  # never traverse symlinks
            is_dir = e.is_dir(follow_symlinks=False)
            out.append({"name": e.name, "path": self.rel(Path(e.path)), "type": "dir" if is_dir else "file",
                        "size": None if is_dir else e.stat().st_size})
        return out

    def tree_text(self, max_depth: int = 3, max_chars: int = 900) -> str:
        lines: list[str] = []

        def walk(rel: str, depth: int):
            for e in self.list_dir(rel):
                lines.append("  " * depth + e["name"] + ("/" if e["type"] == "dir" else ""))
                if e["type"] == "dir" and depth + 1 < max_depth:
                    walk(e["path"], depth + 1)
                if sum(len(l) + 1 for l in lines) > max_chars:
                    return

        walk("", 0)
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n…"

    def iter_files(self, glob: str | None = None):
        """Yield (relpath, Path) for readable text-ish files, pruning ignored dirs. `glob` is comma-separated patterns."""
        pats = [g.strip() for g in (glob or "").split(",") if g.strip()]
        n = 0
        for dirpath, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d.lower() not in IGNORED_DIRS and not os.path.islink(os.path.join(dirpath, d)))
            for f in sorted(files):
                p = Path(dirpath) / f
                if is_sensitive(f) or p.suffix.lower() in BINARY_SUFFIXES or p.is_symlink():
                    continue
                rel = self.rel(p)
                if pats and not any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(f, g) for g in pats):
                    continue
                try:
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                n += 1
                if n > MAX_WALK_FILES:
                    return
                yield rel, p

    # ---- reading
    def read_text(self, p: Path) -> str:
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                raise WorkspaceError(413, f"File larger than {MAX_FILE_BYTES // 1000} KB; read a line range instead")
            raw = p.read_bytes()
        except OSError as e:
            raise WorkspaceError(400, f"Cannot read file: {e.strerror}") from e
        if b"\x00" in raw[:4096]:
            raise WorkspaceError(415, "Binary file")
        return raw.decode("utf-8", errors="replace")

    def read_file(self, rel: str, start: int = 1, end: int | None = None, max_chars: int = 24000) -> dict:
        p = self.resolve(rel, allow_root=False)
        if not p.is_file():
            raise WorkspaceError(400, "Not a file")
        text = self.read_text(p)
        lines = text.splitlines()
        total = len(lines)
        start = max(1, start)
        end = min(end or total, total)
        chunk, used = [], 0
        for i in range(start - 1, end):
            if used + len(lines[i]) + 1 > max_chars:
                end = i
                break
            chunk.append(lines[i])
            used += len(lines[i]) + 1
        return {"path": self.rel(p), "content": "\n".join(chunk), "start": start, "end": start + len(chunk) - 1,
                "total_lines": total, "truncated": start + len(chunk) - 1 < total}

    # ---- search (plain substring; no regex => no ReDoS)
    def search(self, query: str, glob: str | None = None, case: bool = False, max_results: int = 60,
               time_budget: float = 5.0) -> list[dict]:
        q = query if case else query.lower()
        if not q.strip():
            return []
        t0, out = time.monotonic(), []
        for rel, p in self.iter_files(glob):
            if time.monotonic() - t0 > time_budget or len(out) >= max_results:
                break
            try:
                text = self.read_text(p)
            except WorkspaceError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if q in (line if case else line.lower()):
                    out.append({"path": rel, "line": i, "text": line.strip()[:200]})
                    if len(out) >= max_results:
                        break
        return out

    # ---- context retrieval (on-demand, no persistent index)
    def retrieve_context(self, question: str, budget: int = 6000, focus: str | None = None) -> dict:
        sources: list[dict] = []
        blocks: list[str] = []
        used = 0

        def add(rel: str, text: str, start: int, end: int):
            nonlocal used
            numbered = "\n".join(f"{start + i:>4}| {l}" for i, l in enumerate(text.splitlines()))
            block = f"### {rel} (lines {start}-{end})\n{numbered}"
            blocks.append(block)
            used += len(block)
            sources.append({"path": rel, "start": start, "end": end})

        tree = self.tree_text(max_depth=2, max_chars=600)
        used += len(tree)

        if focus:
            f = self.read_file(focus, max_chars=int(budget * 0.7))
            add(f["path"], f["content"], f["start"], f["end"])

        words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)]
        keys = []
        for w in words:
            for part in {w, *re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", w)}:
                if len(part) >= 3 and part.lower() not in STOPWORDS and part.lower() not in keys:
                    keys.append(part.lower())
        keys = keys[:12]
        if keys:
            scored: list[tuple[float, str, Path, list[str]]] = []
            t0 = time.monotonic()
            for rel, p in self.iter_files():
                if rel == focus:
                    continue
                if time.monotonic() - t0 > 5:
                    break
                try:
                    text = self.read_text(p)
                except WorkspaceError:
                    continue
                low, name = text.lower(), rel.lower()
                score = sum(5 for k in keys if k in name) + sum(min(low.count(k), 5) for k in keys)
                if score:
                    scored.append((score, rel, p, text.splitlines()))
            scored.sort(key=lambda t: -t[0])
            for score, rel, p, lines in scored[:4]:
                if used > budget:
                    break
                hits = [i for i, l in enumerate(lines) if any(k in l.lower() for k in keys)]
                # window around the densest hit cluster
                best = max(hits, key=lambda i: sum(1 for j in hits if abs(j - i) <= 12), default=0)
                lo, hi = max(0, best - 10), min(len(lines), best + 25)
                snippet = "\n".join(lines[lo:hi])
                room = budget - used
                if room < 300:
                    break
                snippet = snippet[:room]
                add(rel, snippet, lo + 1, lo + len(snippet.splitlines()))
        return {"tree": tree, "blocks": blocks, "sources": sources, "keywords": keys, "chars": used}
