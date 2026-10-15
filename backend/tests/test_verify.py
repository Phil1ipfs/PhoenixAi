"""Answer verification (Phase G part 4): five separate checks and labels derived only from them.

Unit tests use fixed sources; integration tests drive the chat endpoint with a scripted model and a mocked web."""
from datetime import date

import pytest

from app import verify
from test_research import PM_PAGE, PM_RESULTS, chat, make_app

TODAY = date(2026, 10, 16)


def src(i, title, excerpt, fetched=True, snippet=""):
    return {"id": i, "title": title, "excerpt": excerpt, "snippet": snippet, "fetched": fetched}


PM = [src(1, "Prime Minister of Japan", "The prime minister of Japan is Jane Example, in office since 12 March 2026.")]


def run(q, a, sources, **kw):
    return verify.check_answer(q, a, sources, today=TODAY, **kw)


# ---------------------------------------------------------------- the five checks are separate
def test_good_answer_is_supported_only_when_every_check_passes():
    r = run("Who is the current prime minister of Japan?", "The current prime minister of Japan is Jane Example [1].", PM, time_sensitive=True)
    assert r["label"] == "Answer supported" and r["verdict"] == "supported" and r["reasons"] == []
    assert {k: v.get("ok") for k, v in r["checks"].items()} == {"source_validity": True, "source_relevance": True, "answers_question": True,
                                                                 "claims": True, "time": True}


def test_valid_citation_but_answer_to_a_different_question_is_not_supported():
    """The Phase F failure: real, relevant source cited, but the reply answers 'who has the most titles', not 'who won last'."""
    s = [src(2, "FIFA World Cup", "Brazil have won the tournament five times, the most of any team. The most recent tournament in 2022 was won by Argentina.")]
    r = run("Who won the most recent FIFA World Cup?", "Brazil has won the most titles [2].", s, time_sensitive=True)
    assert r["checks"]["source_validity"]["ok"] is True          # citation is perfectly valid ...
    assert r["checks"]["source_relevance"]["ok"] is True         # ... and the source is relevant ...
    assert r["checks"]["answers_question"]["ok"] is False        # ... but it does not answer the question
    assert r["label"] == "Sources retrieved" and any("current/most recent" in x for x in r["reasons"])


def test_fabricated_citation_is_flagged_not_trusted():
    r = run("Who is the president of France?", "The president of France is Marie Dupont [1] and a deputy named Zed Zorro [4].",
            [src(1, "President of France", "The president of France is Marie Dupont, elected in 2025.")])
    assert r["checks"]["source_validity"]["invalid_ids"] == ["4"] and r["label"] == "Sources retrieved"


def test_unsupported_numeric_claim_downgrades_to_partly_supported():
    live = [src(1, "Open-Meteo live weather for Manila", "Current conditions in Manila (observed 2026-10-15T20:00): temperature 27.7 C, humidity 72%.")]
    r = run("What is the weather in Manila?", "It is 27.7 C in Manila [1]. Temperatures could reach up to 35 C today.", live, time_sensitive=True)
    assert r["label"] == "Partly supported" and r["checks"]["claims"]["ok"] is False
    assert "35" in r["checks"]["claims"]["unsupported"][0]["missing"]
    clean = run("What is the weather in Manila?", "It is 27.7 C in Manila [1].", live, time_sensitive=True)
    assert clean["label"] == "Answer supported"


def test_claim_cited_to_a_source_that_lacks_the_detail():
    s = [src(1, "Python", "Python 3.15.0 is the latest stable release of Python.", True), src(2, "Python docs", "Python is a language for programming.")]
    r = run("What is the latest Python version?", "The latest stable Python version is 3.15.0 [2].", s, time_sensitive=True)
    assert r["checks"]["claims"]["ok"] is False and "3.15.0" in r["checks"]["claims"]["unsupported"][0]["missing"]


def test_decline_is_unable_to_verify_not_sources_retrieved():
    r = run("Who is the current prime minister of Japan?", "The sources do not clearly say who the current prime minister is.", PM, time_sensitive=True)
    assert r["label"] == "Unable to verify" and r["checks"]["answers_question"]["declined"] is True


def test_irrelevant_sources_cannot_support_anything():
    cooking = [src(1, "Pasta recipes", "Boil water, add salt and cook the pasta for ten minutes.")]
    r = run("Who is the president of France?", "The president of France is Marie Dupont [1].", cooking)
    assert r["checks"]["source_relevance"]["ok"] is False and r["label"] == "Unable to verify"


def test_no_sources_means_unable_to_verify():
    assert run("What is the weather in Manila?", "It is sunny.", [])["label"] == "Unable to verify"


def test_uncited_answer_is_sources_retrieved():
    r = run("Who is the current prime minister of Japan?", "The current prime minister of Japan is Jane Example.", PM, time_sensitive=True)
    assert r["label"] == "Sources retrieved" and r["checks"]["source_validity"]["valid_ids"] == []


# ---------------------------------------------------------------- time sensitivity
def test_snippet_only_evidence_for_time_sensitive_question_is_only_partly_supported():
    s = [src(1, "Prime Minister of Japan", "Jane Example has been the prime minister of Japan since 2026.", fetched=False)]
    r = run("Who is the current prime minister of Japan?", "The current prime minister of Japan is Jane Example [1].", s, time_sensitive=True)
    assert r["checks"]["time"]["ok"] is False and r["label"] == "Partly supported" and "snippet" in r["checks"]["time"]["detail"]


def test_stale_evidence_is_flagged():
    s = [src(1, "Prime Minister of Japan", "Old Name was the prime minister of Japan in 2019.", fetched=True)]
    r = run("Who is the current prime minister of Japan?", "The current prime minister of Japan is Old Name [1].", s, time_sensitive=True)
    assert r["checks"]["time"]["ok"] is False and r["checks"]["time"]["latest_year"] == 2019 and r["label"] == "Partly supported"


def test_undated_evidence_for_time_sensitive_question_is_flagged():
    s = [src(1, "Prime Minister of Japan", "Jane Example is the prime minister of Japan.", fetched=True)]
    r = run("Who is the current prime minister of Japan?", "The current prime minister of Japan is Jane Example [1].", s, time_sensitive=True)
    assert r["checks"]["time"]["ok"] is False and "no date" in r["checks"]["time"]["detail"] and r["label"] == "Partly supported"


def test_timeless_question_needs_no_freshness():
    s = [src(1, "France", "Paris is the capital and largest city of France.")]
    r = run("What is the capital of France?", "The capital of France is Paris [1].", s)
    assert r["checks"]["time"]["applicable"] is False and r["label"] == "Answer supported"


# ---------------------------------------------------------------- answer-form checks
@pytest.mark.parametrize("q,a,ok", [
    ("How many engineers were hired?", "Several engineers were hired recently [1].", False),
    ("How many engineers were hired?", "35 engineers were hired [1].", True),
    ("When was the company founded?", "The company was founded long ago [1].", False),
    ("When was the company founded?", "The company was founded in 1998 [1].", True),
    ("What is the latest version of Python?", "Python is a popular language [1].", False),
    ("What is the latest version of Python?", "The latest version of Python is 3.15.0 [1].", True)])
def test_answer_form_matches_the_question(q, a, ok):
    assert verify.check_answers_question(q, a, "")["ok"] is ok


def test_summary_mode_skips_keyword_relevance_but_still_checks_claims():
    d = [{"id": "D1", "title": "r.pdf p. 1", "text": "The report covers revenue of 4.2 million dollars in the third quarter.", "fetched": True}]
    ok = verify.check_answer("Summarize this document", "- Revenue was 4.2 million dollars [D1].", d, prefix="D", summary=True, today=TODAY)
    bad = verify.check_answer("Summarize this document", "- Revenue was 9.9 million dollars [D1].", d, prefix="D", summary=True, today=TODAY)
    assert ok["label"] == "Answer supported" and bad["label"] == "Partly supported"


def test_sanitize_removes_only_invalid_ids():
    assert verify.sanitize_answer("a [D1] b [D9]", ["D9"]) == "a [D1] b [unverified citation removed]"


# ---------------------------------------------------------------- integration through the chat endpoint
def _now_page():
    return PM_PAGE.replace("2026", str(date.today().year))


def test_chat_labels_a_cited_but_unresponsive_answer_as_sources_retrieved(tmp_path):
    page = ("<html><head><title>FIFA World Cup records</title></head><body><p>Brazil have won the FIFA World Cup five times, the most "
            f"of any team. The most recent final was played in {date.today().year - 1}.</p></body></html>")
    c, _, _ = make_app(tmp_path, reply="Brazil has won the most titles [1].",
                       ddg_results=[("FIFA World Cup records", "https://news.test/wc", "Brazil have won the FIFA World Cup five times.")],
                       pages={"news.test": page})
    c.put("/api/settings", json={"web_mode": "auto"})
    meta = chat(c, "Who won the most recent FIFA World Cup?")[-1]["message"]["metrics"]["web"]
    assert meta["status"] in ("grounded", "grounded_weak")                   # the old citation status still says "cited"
    assert meta["verification"]["label"] == "Sources retrieved"              # the new label refuses to call it supported
    assert meta["verification"]["checks"]["answers_question"]["ok"] is False


def test_chat_supported_answer_requires_all_checks(tmp_path):
    c, _, _ = make_app(tmp_path, reply="The current prime minister of Japan is Jane Example [1].", ddg_results=PM_RESULTS,
                       pages={"news.test": _now_page()})
    c.put("/api/settings", json={"web_mode": "auto"})
    meta = chat(c, "Who is the current prime minister of Japan?")[-1]["message"]["metrics"]["web"]
    assert meta["verification"]["label"] == "Answer supported", meta["verification"]


def test_chat_web_off_time_sensitive_is_unable_to_verify(tmp_path):
    c, _, _ = make_app(tmp_path, reply="It is Fumio Kishida.")
    meta = chat(c, "Who is the current prime minister of Japan?")[-1]["message"]["metrics"]["web"]
    assert meta["verification"]["label"] == "Unable to verify" and "off" in meta["verification"]["reasons"][0]


def test_chat_not_time_sensitive_has_no_web_label(tmp_path):
    c, _, _ = make_app(tmp_path, reply="A closure captures variables from its scope.")
    meta = chat(c, "Explain what a closure is in JavaScript")[-1]["message"]["metrics"]["web"]
    assert "verification" not in meta
