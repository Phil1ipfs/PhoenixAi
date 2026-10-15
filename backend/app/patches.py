"""Patch proposal parsing, diffing and safe application. Pure logic; no HTTP, no model calls."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from pathlib import Path

BLOCK_RE = re.compile(
    r"<{5,9} ?SEARCH\r?\n(.*?)\r?\n?={5,9}\r?\n(.*?)\r?\n?>{5,9} ?REPLACE", re.DOTALL)
FENCE_RE = re.compile(r"```[\w+-]*\r?\n(.*?)```", re.DOTALL)
MAX_PATCH_BYTES = 300_000


class PatchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in BLOCK_RE.finditer(text)]


def _find(original: str, needle: str) -> tuple[int, int] | None:
    """Locate `needle` in `original`: exact match first (must be unique), then whitespace-tolerant line match."""
    if needle and original.count(needle) == 1:
        i = original.index(needle)
        return i, i + len(needle)
    if needle and original.count(needle) > 1:
        raise PatchError(422, "A SEARCH block matched more than once; it must identify one location")
    nl = [l.rstrip() for l in needle.split("\n")]
    ol = original.split("\n")
    n = len(nl)
    hits = [i for i in range(len(ol) - n + 1) if [l.rstrip() for l in ol[i:i + n]] == nl] if needle.strip() else []
    if len(hits) == 1:
        start = sum(len(l) + 1 for l in ol[:hits[0]])
        return start, start + sum(len(l) + 1 for l in ol[hits[0]:hits[0] + n]) - 1
    return None


def apply_blocks(original: str, blocks: list[tuple[str, str]]) -> str:
    """Apply SEARCH/REPLACE blocks to the text; any block that does not match makes the whole proposal fail."""
    crlf = "\r\n" in original
    text = original.replace("\r\n", "\n")
    for i, (search, replace) in enumerate(blocks, 1):
        search, replace = search.replace("\r\n", "\n"), replace.replace("\r\n", "\n")
        if not search.strip():
            if text.strip():
                raise PatchError(422, f"Block {i}: empty SEARCH is only valid for a new file")
            text = replace
            continue
        loc = _find(text, search)
        if loc is None:
            raise PatchError(422, f"Block {i}: SEARCH text was not found in the file (the model quoted code that doesn't exist)")
        text = text[:loc[0]] + replace + text[loc[1]:]
    return text.replace("\n", "\r\n") if crlf else text


def proposal_from_model_output(original: str | None, output: str, full_file_limit: int = 8000) -> str:
    """Turn raw model output into the proposed full file text, or raise PatchError."""
    blocks = parse_blocks(output)
    if blocks:
        return apply_blocks(original or "", blocks)
    fences = FENCE_RE.findall(output)
    if len(fences) == 1 and (original is None or len(original) <= full_file_limit):
        return fences[0].rstrip("\n") + "\n"  # fallback: model returned the whole file
    raise PatchError(422, "The model did not return a usable change (no SEARCH/REPLACE block or single full-file code block)")


def diff_rows(original: str | None, proposed: str) -> list[dict]:
    """Side-by-side rows: {kind: equal|change|add|del, left:{n,t}|None, right:{n,t}|None}."""
    a = (original or "").splitlines()
    b = proposed.splitlines()
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"kind": "equal", "left": {"n": i1 + k + 1, "t": a[i1 + k]}, "right": {"n": j1 + k + 1, "t": b[j1 + k]}})
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            left = {"n": i1 + k + 1, "t": a[i1 + k]} if k < i2 - i1 else None
            right = {"n": j1 + k + 1, "t": b[j1 + k]} if k < j2 - j1 else None
            rows.append({"kind": "change" if left and right else ("del" if left else "add"), "left": left, "right": right})
    return rows


def unified(original: str | None, proposed: str, path: str) -> str:
    return "".join(difflib.unified_diff((original or "").splitlines(True), proposed.splitlines(True),
                                         f"a/{path}", f"b/{path}"))


def read_strict(p: Path) -> tuple[str, bytes]:
    data = p.read_bytes()
    if len(data) > MAX_PATCH_BYTES:
        raise PatchError(413, "File too large to patch")
    try:
        return data.decode("utf-8"), data
    except UnicodeDecodeError as e:
        raise PatchError(415, "File is not valid UTF-8; refusing to modify it") from e


def atomic_write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".phoenix-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
