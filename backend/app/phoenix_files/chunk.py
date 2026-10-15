"""Turn extracted paragraphs into small overlapping chunks that remember where they came from (page / section / lines)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import limits
from .extract import Extracted, Para


@dataclass
class Chunk:
    idx: int
    text: str
    page_start: int | None
    page_end: int | None
    line_start: int | None
    line_end: int | None
    section: str | None
    loc: str                  # human-readable reference, e.g. "pp. 3-4 · Results" or "lines 10-40 · parse_args"


def _split_long(p: Para, target: int) -> list[Para]:
    """Split an over-long paragraph at line, then sentence, then word boundaries (never mid-word unless a word is huge)."""
    if len(p.text) <= target:
        return [p]
    pieces: list[str] = []
    cur = ""
    units = re.split(r"(?<=\n)|(?<=[.!?;])\s+", p.text)
    for u in units:
        if not u:
            continue
        while len(u) > target:                     # a single huge unit: hard split on whitespace
            cut = u.rfind(" ", 0, target)
            cut = cut if cut > target // 2 else target
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(u[:cut])
            u = u[cut:].lstrip() if cut != target else u[cut:]
        if cur and len(cur) + len(u) + 1 > target:
            pieces.append(cur)
            cur = u
        else:
            cur = (cur + " " + u) if cur and not cur.endswith("\n") else (cur + u)
    if cur.strip():
        pieces.append(cur)
    # distribute line numbers proportionally so references stay approximately right
    out, total = [], max(len(p.text), 1)
    pos = 0
    for piece in pieces:
        a = p.line + round((p.line_end - p.line) * pos / total) if p.line is not None and p.line_end is not None else p.line
        pos += len(piece)
        b = p.line + round((p.line_end - p.line) * min(pos, total) / total) if p.line is not None and p.line_end is not None else p.line_end
        out.append(Para(piece.strip(), p.page, p.section, a, b))
    return [x for x in out if x.text]


def make_loc(kind: str, c: dict) -> str:
    sec = f" · {c['section']}" if c.get("section") else ""
    ps, pe, ls, le = c["page_start"], c["page_end"], c["line_start"], c["line_end"]
    if ps is not None:
        return (f"p. {ps}" if ps == pe else f"pp. {ps}-{pe}") + sec
    if ls is not None:
        unit = {"csv": "rows", "docx": "¶"}.get(kind, "lines")
        rng = f"{ls}" if ls == le else f"{ls}-{le}"
        if kind == "docx":
            return (c["section"] + " · " if c.get("section") else "") + f"¶{rng}"
        return f"{unit} {rng}" + sec
    return (c.get("section") or "document")


def pack(ex: Extracted, target: int = limits.CHUNK_TARGET, overlap: int = limits.CHUNK_OVERLAP) -> tuple[list[Chunk], list[str]]:
    pieces: list[Para] = []
    for p in ex.paras:
        pieces.extend(_split_long(p, target))
    code_like = ex.kind in ("code", "json", "csv")
    joiner = "\n" if code_like else "\n\n"
    chunks: list[Chunk] = []
    warnings: list[str] = []
    cur: list[Para] = []
    size = 0

    def emit():
        if not cur:
            return
        pages = [x.page for x in cur if x.page is not None]
        lines = [x.line for x in cur if x.line is not None]
        lends = [x.line_end for x in cur if x.line_end is not None]
        sec = next((x.section for x in cur if x.section), None)
        body = joiner.join(x.text for x in cur)
        d = {"page_start": min(pages) if pages else None, "page_end": max(pages) if pages else None,
             "line_start": min(lines) if lines else None, "line_end": max(lends) if lends else None, "section": sec}
        text = (ex.prefix + "\n" if ex.prefix and cur[0].section != "Profile" else "") + body
        chunks.append(Chunk(len(chunks), text, **d, loc=make_loc(ex.kind, d)))

    boundary_sections = ex.kind in ("markdown", "docx", "code", "csv")
    hard = {"Profile", "Structure"}          # synthetic overview chunks (CSV profile, JSON structure) stay on their own

    def carry_over(prev: list[Para]) -> list[Para]:
        carry: list[Para] = []
        used = 0
        for x in reversed(prev):             # overlap: trailing pieces totalling <= overlap characters
            if used + len(x.text) > overlap:
                break
            carry.insert(0, x)
            used += len(x.text)
        if not carry and prev and not code_like:   # one long paragraph: carry its last words so context is not cut mid-thought
            last = prev[-1]
            tail = last.text[-overlap:]
            cut = tail.find(" ")
            tail = tail[cut + 1:] if 0 <= cut < len(tail) - 20 else tail
            carry = [Para(tail, last.page, last.section, last.line_end, last.line_end)]
        return carry if len(carry) < len(prev) or len(prev) == 1 else []

    for piece in pieces:
        new_block = False
        if cur:
            last = cur[-1]
            if ex.kind == "pdf" and piece.page != last.page:
                new_block = True             # a chunk never spans pages, so a page reference is always exact
            elif boundary_sections and piece.section != last.section:
                new_block = True             # ... and never spans sections / functions
            elif last.section in hard and piece.section != last.section:
                new_block = True
        if cur and (new_block or size + len(piece.text) + len(joiner) > target):
            emit()
            cur = [] if new_block else carry_over(cur)
            size = sum(len(x.text) + len(joiner) for x in cur)
        cur.append(piece)
        size += len(piece.text) + len(joiner)
        if len(chunks) >= limits.MAX_CHUNKS:
            warnings.append(f"The file is very long; only the first {limits.MAX_CHUNKS} sections are searchable.")
            cur = []
            break
    emit()
    return chunks[: limits.MAX_CHUNKS], warnings
