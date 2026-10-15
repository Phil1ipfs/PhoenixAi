"""Deterministic retrieval: BM25 over chunks, plus exact page / section / phrase handling. No embeddings, no randomness.

Same query + same documents => same ranked list, always (ties break on file id then chunk index).
"""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = set("""a an and are as at be been but by can could did do does for from had has have how i if in into is it its me my of on or
our please so than that the their them then there these they this to was we were what when where which who whom why will with would
you your about tell show give explain describe say says said document documents file files pdf attached upload uploaded text page
pages section sections according mention mentioned""".split())
def _stem(w: str) -> str:
    """Light, deterministic stemmer: plurals and -ing/-ed. (messages -> message, prices -> price, boxes -> box, queries -> query)."""
    if len(w) > 4:
        if w.endswith("ies"):
            return w[:-3] + "y"
        if w.endswith(("sses", "xes", "ches", "shes", "zes")):
            return w[:-2]
        if w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
    for suf in ("ing", "edly", "ed"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str, drop_stop: bool = False) -> list[str]:
    """Words, with snake_case / camelCase / digits split so `build_messages` matches 'build messages'."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    out = []
    for w in re.findall(r"[A-Za-z0-9]+", spaced):
        w = w.lower()
        if drop_stop and (w in STOP or len(w) < 2):
            continue
        out.append(_stem(w))
    return out


def query_terms(question: str) -> list[str]:
    return list(dict.fromkeys(tokens(question, drop_stop=True)))


PAGE_REF = re.compile(r"\b(?:page|pages|pp?\.?)\s*(\d{1,4})(?:\s*(?:-|–|to)\s*(\d{1,4}))?", re.I)
LINE_REF = re.compile(r"\b(?:line|lines|rows?)\s*(\d{1,6})(?:\s*(?:-|–|to)\s*(\d{1,6}))?", re.I)


def explicit_refs(question: str) -> dict:
    pages: set[int] = set()
    for m in PAGE_REF.finditer(question):
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        pages.update(range(a, min(b, a + 20) + 1))
    lines: set[int] = set()
    for m in LINE_REF.finditer(question):
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        lines.update(range(a, min(b, a + 200) + 1))
    phrases = [p.lower() for p in re.findall(r'"([^"]{3,80})"', question)]
    return {"pages": pages, "lines": lines, "phrases": phrases}


class Index:
    """BM25 index over a list of chunk dicts (keys: file_id, idx, text, section, page_start, page_end, line_start, line_end)."""

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.docs = []
        for c in chunks:
            toks = tokens(c["text"]) + tokens(c.get("section") or "") * 2     # section titles count extra: they name the topic
            self.docs.append(Counter(toks))
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, question: str, limit: int = 8, k1: float = 1.5, b: float = 0.75) -> list[dict]:
        terms = query_terms(question)
        refs = explicit_refs(question)
        hits = []
        for i, c in enumerate(self.chunks):
            tf = self.docs[i]
            score, matched = 0.0, set()
            norm = k1 * (1 - b + b * self.lens[i] / self.avg) if self.avg else k1
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    score += self.idf.get(t, 0.0) * (f * (k1 + 1)) / (f + norm)
                    matched.add(t)
            low = c["text"].lower()
            for ph in refs["phrases"]:
                if ph in low:
                    score += 5.0
                    matched.add(ph)
            ps, pe = c.get("page_start"), c.get("page_end")
            if refs["pages"] and ps is not None and any(ps <= p <= (pe or ps) for p in refs["pages"]):
                score += 20.0
                matched.add("page")
            ls, le = c.get("line_start"), c.get("line_end")
            if refs["lines"] and ls is not None and any(ls <= n <= (le or ls) for n in refs["lines"]):
                score += 20.0
                matched.add("lines")
            if score > 0:
                hits.append({**c, "score": round(score, 4), "matched": sorted(matched),
                             "coverage": round(len(matched & set(terms)) / len(terms), 3) if terms else 0.0})
        hits.sort(key=lambda h: (-h["score"], h["file_id"], h["idx"]))
        return hits[:limit]


def pack_budget(hits: list[dict], budget: int, per_chunk_cap: int = 1400) -> list[dict]:
    """Take the best hits that fit the character budget, then restore document order (better for reading)."""
    chosen, used = [], 0
    for h in hits:
        size = min(len(h["text"]), per_chunk_cap)
        if chosen and used + size > budget:
            continue
        chosen.append({**h, "text": h["text"][:per_chunk_cap]})
        used += size
    return sorted(chosen, key=lambda h: (h["file_id"], h["idx"]))


def summary_sample(chunks: list[dict], budget: int) -> list[dict]:
    """Representative chunks for a summary: the opening, the first chunk of each section, then even spacing. Deterministic."""
    if not chunks:
        return []
    picked: dict[int, dict] = {0: chunks[0]}
    seen_sections = {chunks[0].get("section")}
    for c in chunks:
        sec = c.get("section")
        if sec and sec.split(" > ")[0] not in {(s or "").split(" > ")[0] for s in seen_sections}:
            picked[c["idx"]] = c
            seen_sections.add(sec)
    out, used = [], 0
    order = sorted(picked)
    # fill the rest of the budget with evenly spaced chunks
    per = max(300, min(900, budget // max(4, min(len(chunks), 8))))
    remaining = max(0, (budget - sum(min(len(picked[i]["text"]), per) for i in order)) // per)
    if remaining and len(chunks) > len(order):
        step = len(chunks) / (remaining + 1)
        for j in range(1, remaining + 1):
            order.append(chunks[min(len(chunks) - 1, int(j * step))]["idx"])
    for i in sorted(set(order)):
        c = next(c for c in chunks if c["idx"] == i)
        size = min(len(c["text"]), per)
        if out and used + size > budget:
            continue
        out.append({**c, "text": c["text"][:per]})
        used += size
    return out
