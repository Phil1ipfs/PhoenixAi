"""Answer verification: five independent, deterministic checks, then a label derived ONLY from the check results.

A valid citation is not a verified answer. Phase F showed the model can cite a real, relevant page and still answer a
different question. So each question below is checked separately:

  1. source_validity   do the cited ids exist, and were the sources really retrieved (not invented)?
  2. source_relevance  do the retrieved sources actually talk about what was asked?
  3. answers_question  does the reply address the question (right kind of answer, not a decline, not a different topic)?
  4. claims            are the checkable facts in the reply (numbers, years, names) present in the cited sources?
  5. time              for time-sensitive questions, is the evidence recent/dated, or could it be stale?

Labels (never "verified"):
  Answer supported   all five checks pass
  Partly supported   cited, relevant and on-topic, but some claims or the freshness could not be confirmed
  Sources retrieved  sources were found, but the reply does not (verifiably) answer from them
  Unable to verify   the reply declines, there is nothing relevant to check against, or access to sources was unavailable

These are lexical/heuristic checks. They catch the failure modes measured so far; they do not prove a claim is true.
"""
from __future__ import annotations

import re
from datetime import date

from .phoenix_files.retrieve import STOP as DOC_STOP, tokens

RECENCY = {"current", "currently", "latest", "newest", "recent", "recently", "today", "now", "right", "still", "new", "last",
           "present", "tonight", "tomorrow", "yesterday", "latest"}
RECENCY_RE = re.compile(r"\b(most recent|latest|current(?:ly)?|newest|right now|today|tonight|this (?:week|month|year)|as of|"
                        r"now|still|last (?:year|month|week|season)|up[- ]to[- ]date)\b", re.I)
WH = {"who", "what", "when", "where", "why", "how", "which", "whom", "whose", "is", "are", "was", "were", "do", "does", "did",
      "can", "could", "would", "should", "will", "much", "many"}
DECLINE = re.compile(
    r"(?:\b(?:could not|couldn'?t|cannot|can'?t|can not|unable to|not able to|am unable|failed to|do(?:es)? not|don'?t|doesn'?t|did not|"
    r"didn'?t|no )\b[^.\n]{0,60}\b(?:verify|verified|find|found|confirm|provide|provides|determine|answer|say|state|states|mention|mentions|"
    r"contain|contains|include|includes|specify|specifies|clearly)\b)|\bnot (?:clearly )?(?:stated|mentioned|specified|provided|found|available)\b|"
    r"\bno (?:relevant |clear |specific )?(?:information|answer|mention|data)\b|\bunverified\b", re.I)
MONTHS = "january february march april may june july august september october november december".split()
MONTH_RE = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+\d{1,4}\b|\b\d{1,2}\s+(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\b", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
VERSION_RE = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
CAP_RE = re.compile(r"\b[A-Z][a-zA-Z'’-]{2,}\b")
SKIP_CAPS = set(MONTHS) | {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "the", "according", "based",
                       "however", "this", "that", "these", "those", "there", "here", "also", "yes", "while", "although"}
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
HEDGE_START = re.compile(r"^\s*(?:note|disclaimer|sources?|i |however|unfortunately)", re.I)


def _cite_re(prefix: str) -> re.Pattern:
    return re.compile(r"\[(" + re.escape(prefix) + r"\d{1,3})\]")


def _terms(text: str) -> list[str]:
    out = []
    for t in tokens(text, drop_stop=True):
        if t in WH or t in RECENCY or t in DOC_STOP:
            continue
        out.append(t)
    return list(dict.fromkeys(out))


def _source_text(s: dict) -> str:
    return " ".join(str(s.get(k) or "") for k in ("title", "excerpt", "snippet", "text", "file_name", "loc"))


# ------------------------------------------------------------------ 1. validity
def check_validity(answer: str, sources: list[dict], prefix: str) -> dict:
    by_id = {str(s["id"]): s for s in sources}
    cited = _cite_re(prefix).findall(answer)
    valid = sorted({c for c in cited if c in by_id})
    invalid = sorted({c for c in cited if c not in by_id})
    ok = bool(valid) and not invalid
    detail = ("every citation refers to a retrieved source" if ok else
              "no citations in the reply" if not cited else f"citation(s) to sources that were never retrieved: {', '.join(invalid)}")
    return {"ok": ok, "valid_ids": valid, "invalid_ids": invalid, "detail": detail}


# ------------------------------------------------------------------ 2. relevance
def check_relevance(question: str, sources: list[dict]) -> dict:
    qt = _terms(question)
    per = []
    for s in sources:
        hay = set(tokens(_source_text(s)))
        matched = [t for t in qt if t in hay]
        cov = len(matched) / len(qt) if qt else 0.0
        need = 1 if len(qt) <= 2 else 2
        per.append({"id": str(s["id"]), "coverage": round(cov, 2), "matched": matched[:8],
                    "relevant": bool(qt) and len(matched) >= need and cov >= 0.4})
    rel = [p["id"] for p in per if p["relevant"]]
    return {"ok": bool(rel), "relevant_ids": rel, "per_source": per, "question_terms": qt[:12],
            "detail": (f"{len(rel)} of {len(per)} source(s) cover the question's key terms" if rel else
                       "none of the retrieved sources covers the question's key terms")}


# ------------------------------------------------------------------ 3. does the reply answer the question?
def expected_form(question: str) -> str | None:
    q = question.lower().strip()
    if re.search(r"\b(version|release)\b", q) and re.search(r"\b(latest|current|newest|which|what)\b", q):
        return "a version number"
    if re.match(r"^(who|whom|whose)\b", q) or re.search(r"\bwho (is|are|was|were|won|wins|leads)\b", q):
        return "a name"
    if re.search(r"\b(how many|how much|how (?:hot|cold|tall|long|far|old|big|large)|what(?:'s| is) the (?:price|cost|temperature|weather|score|rate|population))\b", q) \
            or re.search(r"\b(weather|temperature|forecast|price|cost)\b", q):
        return "a number"
    if re.search(r"^when\b|\bwhat (year|date|day)\b|\bwhen (is|was|did|does|will)\b|\brelease date\b", q):
        return "a date or year"
    return None


def _has_form(answer_body: str, form: str, question: str) -> bool:
    if form == "a number":
        return bool(NUM_RE.search(answer_body))
    if form == "a date or year":
        return bool(YEAR_RE.search(answer_body) or MONTH_RE.search(answer_body))
    if form == "a version number":
        return bool(VERSION_RE.search(answer_body))
    if form == "a name":
        qwords = {w.lower() for w in re.findall(r"[A-Za-z]+", question)}
        caps = [w for w in CAP_RE.findall(answer_body) if w.lower() not in qwords and w.lower() not in {"the", "according", "based"}]
        return bool(caps)
    return True


def check_answers_question(question: str, answer: str, prefix: str) -> dict:
    body = _cite_re(prefix).sub(" ", answer)
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    if DECLINE.search(body) and len(body.split()) < 120:
        return {"ok": None, "declined": True, "expected": expected_form(question),
                "detail": "the reply says it could not find or confirm the answer"}
    problems = []
    form = expected_form(question)
    if form and not _has_form(body, form, question):
        problems.append(f"the question calls for {form}, but the reply contains none")
    if RECENCY_RE.search(question) and not (YEAR_RE.search(body) or MONTH_RE.search(body) or re.search(r"\b(as of|since|currently|current|latest|most recent|today|now)\b", body, re.I)):
        problems.append("the question asks about the current/most recent state, but the reply gives no date, year or 'current/latest' wording, "
                        "so it may describe something else")
    qt = _terms(question)
    if qt and len(body.split()) >= 8:
        at = set(tokens(body))
        if not any(t in at for t in qt):
            problems.append("the reply never mentions the subject of the question")
    return {"ok": not problems, "declined": False, "expected": form, "detail": "; ".join(problems) or "the reply addresses the question"}


# ------------------------------------------------------------------ 4. unsupported claims
def _checkables(sentence: str, question: str) -> list[str]:
    qwords = {w.lower() for w in re.findall(r"[A-Za-z]+", question)}
    out = []
    words = sentence.split()
    first = re.sub(r"\W", "", words[0]).lower() if words else ""
    for m in NUM_RE.finditer(sentence):
        v = m.group(0).strip(",.")
        if len(v.replace(",", "").replace(".", "")) >= 2:
            out.append(v)
    for m in CAP_RE.finditer(sentence):
        w = m.group(0)
        if w.lower() == first and m.start() < 3:
            continue                                 # sentence-initial capital: not evidence of a name
        if w.lower() in qwords or w.lower() in SKIP_CAPS:
            continue
        out.append(w)
    return list(dict.fromkeys(out))


def _present(token: str, hay_raw: str, hay_tokens: set[str]) -> bool:
    t = token.replace(",", "")
    if re.fullmatch(r"[\d.]+", t):
        return re.search(r"(?<![\d.])" + re.escape(t) + r"(?![\d])", hay_raw.replace(",", "")) is not None
    return all(x in hay_tokens for x in tokens(token)) if tokens(token) else True


def check_claims(question: str, answer: str, sources: list[dict], prefix: str) -> dict:
    by_id = {str(s["id"]): s for s in sources}
    all_raw = " ".join(_source_text(s) for s in sources).lower()
    all_tok = set(tokens(all_raw))
    pat = _cite_re(prefix)
    unsupported, checked, uncited_ok = [], 0, 0
    text = re.sub(r"```.*?```", " ", answer, flags=re.S)
    for sent in SENT_SPLIT.split(text):
        s = sent.strip(" -*#>\t")
        if len(s) < 12 or s.endswith("?") or HEDGE_START.match(s) or DECLINE.search(s):
            continue
        ids = [i for i in pat.findall(s) if i in by_id]
        body = pat.sub(" ", s)
        items = _checkables(body, question)
        if not items:
            continue
        checked += 1
        if ids:
            raw = " ".join(_source_text(by_id[i]) for i in ids).lower()
            tok = set(tokens(raw))
            missing = [x for x in items if not _present(x.lower(), raw, tok)]
            if missing:
                unsupported.append({"sentence": s[:200], "ids": ids, "missing": missing[:5],
                                    "reason": f"cites {', '.join('[' + i + ']' for i in ids)} but {', '.join(repr(m) for m in missing[:3])} is not in that source"})
        else:
            missing = [x for x in items if not _present(x.lower(), all_raw, all_tok)]
            if missing:
                unsupported.append({"sentence": s[:200], "ids": [], "missing": missing[:5],
                                    "reason": f"uncited claim; {', '.join(repr(m) for m in missing[:3])} appears in none of the sources"})
            else:
                uncited_ok += 1
    return {"ok": not unsupported, "checked": checked, "unsupported": unsupported[:8], "uncited_but_found": uncited_ok,
            "detail": (f"{checked} factual sentence(s) checked, none unsupported" if not unsupported else
                       f"{len(unsupported)} of {checked} factual sentence(s) contain details not found in the cited sources")}


# ------------------------------------------------------------------ 5. time-sensitivity
def check_time(question: str, time_sensitive: bool, sources: list[dict], cited_ids: list[str], today: date | None = None) -> dict:
    today = today or date.today()
    applicable = bool(time_sensitive or RECENCY_RE.search(question))
    if not applicable:
        return {"applicable": False, "ok": True, "detail": "the question is not time-sensitive"}
    by_id = {str(s["id"]): s for s in sources}
    used = [by_id[i] for i in cited_ids if i in by_id] or sources
    years = [int(y) for s in used for y in YEAR_RE.findall(_source_text(s)) if int(y) <= today.year + 1]
    live = [s for s in used if s.get("fetched")]
    newest = max(years) if years else None
    if not used:
        return {"applicable": True, "ok": False, "latest_year": None, "detail": "time-sensitive, but there is no source to check recency against"}
    if newest is None:
        return {"applicable": True, "ok": False, "latest_year": None, "detail": "time-sensitive, but the sources carry no date, so they could be out of date"}
    if newest < today.year - 1:
        return {"applicable": True, "ok": False, "latest_year": newest, "detail": f"time-sensitive, and the newest date in the sources is {newest}; the information may be out of date"}
    if not live:
        return {"applicable": True, "ok": False, "latest_year": newest,
                "detail": f"time-sensitive; sources mention {newest} but only as search snippets (the full pages were not read), so recency is unconfirmed"}
    return {"applicable": True, "ok": True, "latest_year": newest, "detail": f"time-sensitive; sources were read live and mention {newest}"}


# ------------------------------------------------------------------ verdict
LABELS = {"supported": "Answer supported", "partial": "Partly supported", "sources_only": "Sources retrieved", "unverified": "Unable to verify"}


def check_answer(question: str, answer: str, sources: list[dict], *, prefix: str = "", time_sensitive: bool = False,
                 today: date | None = None, summary: bool = False) -> dict:
    """Run all five checks and derive the label. `sources`: dicts with id, title/excerpt/snippet (web) or text/loc (documents)."""
    v = check_validity(answer, sources, prefix)
    if summary:   # a summary has no key-term question: relevance is by construction, the other checks still apply
        rel = {"ok": True, "relevant_ids": [str(s["id"]) for s in sources], "per_source": [], "question_terms": [],
               "detail": "summary of excerpts sampled from the document"}
        ans = {"ok": not DECLINE.search(answer), "declined": False, "expected": None, "detail": "summary requested"}
        tm = {"applicable": False, "ok": True, "detail": "the question is not time-sensitive"}
    else:
        rel = check_relevance(question, sources)
        ans = check_answers_question(question, answer, prefix)
        tm = check_time(question, time_sensitive, sources, v["valid_ids"], today)
    claims = check_claims(question, answer, sources, prefix)
    checks = {"source_validity": v, "source_relevance": rel, "answers_question": ans, "claims": claims, "time": tm}
    reasons: list[str] = []
    cited_relevant = bool(set(v["valid_ids"]) & set(rel["relevant_ids"]))
    if not sources:
        verdict = "unverified"
        reasons.append("no sources were available to check against")
    elif ans.get("declined"):
        verdict = "unverified"
        reasons.append("the reply says it could not confirm the answer")
    elif not rel["ok"]:
        verdict = "unverified"
        reasons.append(rel["detail"])
    elif not v["valid_ids"] or v["invalid_ids"] or not cited_relevant or ans["ok"] is False:
        verdict = "sources_only"
        if not v["valid_ids"]:
            reasons.append(v["detail"])
        elif v["invalid_ids"]:
            reasons.append(v["detail"])
        elif not cited_relevant:
            reasons.append("the reply cites sources that do not cover the question")
        if ans["ok"] is False:
            reasons.append(ans["detail"])
    elif claims["ok"] and tm["ok"]:
        verdict = "supported"
    else:
        verdict = "partial"
        if not claims["ok"]:
            reasons.append(claims["detail"])
        if not tm["ok"]:
            reasons.append(tm["detail"])
    return {"verdict": verdict, "label": LABELS[verdict], "checks": checks, "reasons": reasons}


def sanitize_answer(answer: str, invalid_ids: list[str], prefix: str = "") -> str:
    for i in invalid_ids:
        answer = answer.replace(f"[{i}]", "[unverified citation removed]")
    return answer
