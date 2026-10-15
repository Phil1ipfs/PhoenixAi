"""Web Off / Auto / Always behaviour, verified by recording the HTTP requests PHOENIX really makes (never by trusting
what the model says). Deterministic: the internet is mocked. Live provider checks are at the end and skip when offline."""
import asyncio
import json
import socket
import time
from urllib.parse import quote, unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from app import research
from app.main import create_app
from app.providers import ChatChunk, ModelProvider

PAGES = {
    "news.test": ("<html><head><title>President of the Philippines</title>"
                  "<meta property='article:published_time' content='2026-10-10T08:00:00Z'></head><body>"
                  "<p>The current president of the Philippines is Juan Example, whose term runs until June 2028.</p></body></html>"),
    "usa.test": ("<html><head><title>President of the United States</title><script type='application/ld+json'>"
                 "{\"datePublished\": \"2026-09-01\"}</script></head><body><p>The current president of the United States is "
                 "Jane Sample, serving a term that runs until January 2029.</p></body></html>"),
    "eiffel.test": "<html><title>Eiffel Tower</title><p>The Eiffel Tower is a wrought-iron lattice tower in Paris, France, completed in 1889.</p></html>",
    "junk.test": "<html><title>Buy cheap shoes</title><p>Best discount sneakers and boots for every season and every budget online.</p></html>",
}


class Net:
    """Mock internet. `log` = every URL requested, so tests assert on real traffic."""

    def __init__(self, ddg="ok", wiki="ok", pages=None, slow_pages=0.0, results=None):
        self.log, self.ddg, self.wiki, self.pages, self.slow, self.results = [], ddg, wiki, pages or PAGES, slow_pages, results

    async def handler(self, req: httpx.Request) -> httpx.Response:
        self.log.append(str(req.url))
        host = req.url.host
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if host == "html.duckduckgo.com":
            if self.ddg == "down":
                raise httpx.ConnectError("offline")
            if self.ddg == "timeout":
                raise httpx.ReadTimeout("slow")
            if self.ddg == "500":
                return httpx.Response(500, text="err")
            q = unquote(req.url.params.get("q", "")).lower()
            rows = self.results or (
                [("President of the United States", "https://usa.test/p", "The current president of the United States.")] if "usa" in q else
                [("Eiffel Tower", "https://eiffel.test/e", "Wrought-iron tower in Paris.")] if "eiffel" in q else
                [("President of the Philippines", "https://news.test/p", "The current president of the Philippines."),
                 ("Cheap shoes", "https://junk.test/s", "Discount sneakers.")])
            return httpx.Response(200, text="".join(f'<a class="result__a" href="//duckduckgo.com/l/?uddg={quote(u, safe="")}">{t}</a>'
                                                    f'<a class="result__snippet" href="x">{s}</a>' for t, u, s in rows))
        if host == "en.wikipedia.org":
            if self.wiki == "down":
                raise httpx.ConnectError("offline")
            return httpx.Response(200, json={"query": {"search": [{"title": "Eiffel Tower", "snippet": "The Eiffel Tower is a tower in Paris."}]}})
        if host in self.pages:
            if self.slow:
                await asyncio.sleep(self.slow)
            return httpx.Response(200, text=self.pages[host], headers={"content-type": "text/html"})
        return httpx.Response(404)

    def searches(self):
        return [u for u in self.log if httpx.URL(u).host in ("html.duckduckgo.com", "en.wikipedia.org")]


class Model(ModelProvider):
    def __init__(self, reply="Answer [1]."):
        self.reply, self.calls = reply, []

    async def health(self): return {"connected": True, "version": "t", "url": "t"}
    async def list_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30}]
    async def loaded_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30}]
    async def aclose(self): pass

    async def chat_stream(self, model, messages, options, timeout):
        self.calls.append(messages)
        yield ChatChunk(content=self.reply)
        yield ChatChunk(done=True, metrics={})


def make(tmp_path, mode, net=None, reply="Answer [1]."):
    net, prov = net or Net(), Model(reply)
    web = research.WebClient(transport=httpx.MockTransport(net.handler), resolver=lambda h: True, timeout=2)
    c = TestClient(create_app(tmp_path / "t.db", prov, web))
    c.put("/api/settings", json={"web_mode": mode})
    return c, c.post("/api/conversations").json()["id"], net, prov


def send(c, cid, text, **kw):
    r = c.post(f"/api/conversations/{cid}/messages", json={"content": text, **kw})
    ev = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    return ev, ev[-1].get("message", {})


def web(m):
    return (m.get("metrics") or {}).get("web") or {}


# ---------------------------------------------------------------- Web Off
@pytest.mark.parametrize("q", ["Who is the current president of the Philippines?", "What is the price of bitcoin today?", "latest news"])
def test_web_off_makes_no_external_requests_and_says_so(tmp_path, q):
    c, cid, net, prov = make(tmp_path, "off")
    ev, m = send(c, cid, q)
    assert net.log == []                                                 # nothing left this computer
    assert web(m)["status"] == "unverified_disabled" and any(e["type"] == "warning" for e in ev)


# ---------------------------------------------------------------- Web Auto
@pytest.mark.parametrize("q", ["Who is the current president of the Philippines?", "What is the price of bitcoin?",
                               "latest version of Python", "What's the news today?", "Who won the 2026 election?"])
def test_web_auto_searches_for_current_information(tmp_path, q):
    c, cid, net, _ = make(tmp_path, "auto")
    send(c, cid, q)
    assert net.searches(), q


@pytest.mark.parametrize("q", ["Explain what a closure is in JavaScript", "Write a Python function to reverse a string", "hello"])
def test_web_auto_does_not_search_for_ordinary_coding_or_chat(tmp_path, q):
    c, cid, net, _ = make(tmp_path, "auto")
    send(c, cid, q)
    assert net.log == [], q


# ---------------------------------------------------------------- Web Always
def test_web_always_really_searches_even_for_timeless_questions(tmp_path):
    c, cid, net, prov = make(tmp_path, "always")
    ev, m = send(c, cid, "Tell me about the Eiffel Tower")
    assert any("html.duckduckgo.com" in u for u in net.log) and any("eiffel.test" in u for u in net.log)   # real traffic
    w = web(m)
    log = w["search"]["log"]
    s = next(e for e in log if e["step"] == "search")
    assert s["provider"] == "DuckDuckGo" and s["query"] == "Tell me about the Eiffel Tower" and s["status"] == "ok"
    assert s["urls"] == ["https://eiffel.test/e"] and s["requested_at"] and isinstance(s["duration_ms"], int)
    f = next(e for e in log if e["step"] == "fetch")
    assert f["status"] == "fetched" and f["used_in_answer"] is True
    assert "UNTRUSTED WEB SOURCES" in prov.calls[0][-1]["content"]     # the evidence really reached the model
    statuses = [e["message"] for e in ev if e["type"] == "status"]
    assert statuses == ["Searching the web…", "Retrieving 1 source…", "Checking information…", "Generating answer…"]


@pytest.mark.parametrize("q,why", [("hi", "nothing to look up"), ("my password: hunter2 is it safe?", "password"),
                                   ("```python\nprint(1)\n```\nwhat does this print?", "code"),
                                   ("email me at someone@example.com", "e-mail")])
def test_web_always_skips_unsafe_or_pointless_searches_and_explains(tmp_path, q, why):
    c, cid, net, _ = make(tmp_path, "always")
    _, m = send(c, cid, q)
    assert net.log == [] and web(m)["status"] == "search_skipped" and why in web(m)["note"]


def test_provider_failure_falls_back_to_wikipedia_and_logs_both(tmp_path):
    c, cid, net, _ = make(tmp_path, "always", Net(ddg="500"))
    _, m = send(c, cid, "Tell me about the Eiffel Tower")
    log = web(m)["search"]["log"]
    assert [(e["provider"], e["status"]) for e in log if e["step"] == "search"] == [("DuckDuckGo", "error"), ("Wikipedia", "ok")]
    assert web(m)["sources"][0]["url"].startswith("https://en.wikipedia.org/wiki/")


def test_duckduckgo_human_check_pauses_it_and_goes_straight_to_wikipedia(tmp_path):
    net = Net()
    orig = net.handler

    async def blocked(req):
        if req.url.host == "html.duckduckgo.com":
            net.log.append(str(req.url))
            return httpx.Response(200, text='<div class="anomaly-modal">are you human?</div>')
        return await orig(req)

    c = TestClient(create_app(tmp_path / "t.db", Model(), research.WebClient(transport=httpx.MockTransport(blocked), resolver=lambda h: True)))
    c.put("/api/settings", json={"web_mode": "always"})
    cid = c.post("/api/conversations").json()["id"]
    send(c, cid, "Tell me about the Eiffel Tower")
    n = len([u for u in net.log if "duckduckgo" in u])
    _, m = send(c, cid, "Tell me about the Eiffel Tower", fresh=True)
    assert len([u for u in net.log if "duckduckgo" in u]) == n            # not contacted again while paused
    e = next(x for x in web(m)["search"]["log"] if x["provider"] == "DuckDuckGo")
    assert "paused" in e["error"] and web(m)["sources"][0]["url"].startswith("https://en.wikipedia.org/")


def test_offline_fallback_is_labelled_and_model_is_cautioned(tmp_path):
    c, cid, net, prov = make(tmp_path, "always", Net(ddg="down", wiki="down"))
    ev, m = send(c, cid, "Who is the current president of the Philippines?")
    assert web(m)["status"] == "unverified_offline" and web(m)["sources"] == []
    assert any(e["type"] == "warning" and "Not verified live" in e["message"] for e in ev)
    assert "could not verify it live" in prov.calls[0][0]["content"]    # the model must say it is not live


def test_search_timeout_is_reported_not_hidden(tmp_path):
    c, cid, net, _ = make(tmp_path, "always", Net(ddg="timeout", wiki="down"))
    _, m = send(c, cid, "Tell me about the Eiffel Tower")
    e = next(x for x in web(m)["search"]["log"] if x["provider"] == "DuckDuckGo")
    assert e["status"] == "error" and "Timed out" in e["error"] and web(m)["status"] == "unverified_offline"


def test_slow_pages_are_cut_off_at_the_budget_and_marked_snippet_only():
    net = Net(slow_pages=1.0)
    wc = research.WebClient(transport=httpx.MockTransport(net.handler), resolver=lambda h: True, timeout=2)
    t0 = time.perf_counter()
    ev = asyncio.run(research.gather(wc, "Tell me about the Eiffel Tower", page_budget_s=0.3))
    assert time.perf_counter() - t0 < 0.9                                 # did not wait for the slow page
    assert ev.sources[0]["fetched"] is False and ev.sources[0]["fetch_error"] == "deadline"


def test_cancelling_a_search_leaves_no_background_requests():
    net = Net(slow_pages=2.0)
    wc = research.WebClient(transport=httpx.MockTransport(net.handler), resolver=lambda h: True, timeout=5)

    async def go():
        task = asyncio.create_task(research.gather(wc, "Tell me about the Eiffel Tower"))
        await asyncio.sleep(0.3)                                          # search done, page fetch in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(go()) == []


# ---------------------------------------------------------------- relevance, citations, "used" flags, dates
def test_irrelevant_results_are_dropped_before_the_model_sees_them(tmp_path):
    c, cid, net, prov = make(tmp_path, "auto")
    _, m = send(c, cid, "Who is the current president of the Philippines?")
    assert [s["url"] for s in web(m)["sources"]] == ["https://news.test/p"]
    assert any(e["step"] == "filter" and e["url"] == "https://junk.test/s" for e in web(m)["search"]["log"])
    assert "shoes" not in prov.calls[0][-1]["content"].lower()


def test_published_date_comes_from_the_page_or_is_left_empty(tmp_path):
    c, cid, *_ = make(tmp_path, "auto")
    _, m = send(c, cid, "Who is the current president of the Philippines?")
    assert web(m)["sources"][0]["published"] == "2026-10-10"
    assert research.extract_published(PAGES["usa.test"]) == "2026-09-01"
    assert research.extract_published(PAGES["eiffel.test"]) is None    # never invented


def test_uncited_sources_are_marked_unused(tmp_path):
    two = [("President of the Philippines", "https://news.test/p", "x"), ("President of the United States", "https://usa.test/p", "president")]
    c, cid, *_ = make(tmp_path, "auto", Net(results=two), reply="Juan Example is president [1].")
    _, m = send(c, cid, "Who is the current president of the Philippines?")
    assert [s["used"] for s in web(m)["sources"]] == [True, False]


def test_fabricated_citation_is_removed_and_not_counted_as_used(tmp_path):
    c, cid, *_ = make(tmp_path, "auto", reply="Juan Example is president [1], see also [7].")
    _, m = send(c, cid, "Who is the current president of the Philippines?")
    assert "[7]" not in m["content"] and web(m)["citations"]["invalid_ids"] == [7]


# ---------------------------------------------------------------- conversation follow-ups
def test_followups_build_the_search_query_from_the_previous_question(tmp_path):
    c, cid, net, _ = make(tmp_path, "auto")
    send(c, cid, "Who is the current president of the Philippines?")
    n = len(net.log)
    _, m = send(c, cid, "Until when?")
    q = web(m)["search"]["query"]
    assert q.startswith("Who is the current president of the Philippines?") and q.endswith("Until when?")
    assert any("president" in unquote(u) for u in net.log[n:] if "duckduckgo" in u)
    _, m = send(c, cid, "How about the USA?")
    assert web(m)["sources"][0]["url"] == "https://usa.test/p"           # the right topic, not a weather question
    assert "weather" not in (m.get("metrics") or {})


# ---------------------------------------------------------------- cache
def test_second_identical_question_uses_cache_and_says_when_it_was_retrieved(tmp_path):
    c, cid, net, _ = make(tmp_path, "always")
    _, m1 = send(c, cid, "Tell me about the Eiffel Tower")
    n = len(net.log)
    ev, m2 = send(c, cid, "Tell me about the Eiffel Tower")
    assert len(net.log) == n                                             # no new request
    s = web(m2)["search"]
    assert s["cached"] is True and s["log"][0]["provider"] == "cache"
    assert web(m2)["sources"][0]["cached"] is True
    assert web(m2)["sources"][0]["retrieved_at"] == web(m1)["sources"][0]["retrieved_at"]   # original fetch time, not "now"
    assert any(e["type"] == "status" and e["message"].startswith("Using cached search results") for e in ev)


def test_fresh_search_bypasses_the_cache(tmp_path):
    c, cid, net, _ = make(tmp_path, "always")
    send(c, cid, "Tell me about the Eiffel Tower")
    n = len(net.log)
    _, m = send(c, cid, "Tell me about the Eiffel Tower", fresh=True)
    assert len(net.log) > n and web(m)["search"]["cached"] is False


def test_cache_expires(monkeypatch):
    cache = research.SearchCache()
    cache.put("q", research.Evidence("ok"))
    assert cache.get("q", 600)
    real = time.time
    monkeypatch.setattr(research.time, "time", lambda: real() + 601)
    assert cache.get("q", 600) is None


def test_freshness_limits_by_topic():
    assert research.cache_ttl(["weather"]) == 600 and research.cache_ttl(["price"]) == 600
    assert research.cache_ttl(["officeholder / role question"]) == 1800 and research.cache_ttl([]) == 3600


def test_failed_searches_are_never_cached(tmp_path):
    net = Net(ddg="down", wiki="down")
    c, cid, *_ = make(tmp_path, "always", net)
    send(c, cid, "Tell me about the Eiffel Tower")
    net.ddg = net.wiki = "ok"
    _, m = send(c, cid, "Tell me about the Eiffel Tower")
    assert web(m)["search"]["cached"] is False and web(m)["sources"]


# ---------------------------------------------------------------- live providers (skipped when offline)
def _online(host):
    try:
        socket.create_connection((host, 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _online("html.duckduckgo.com"), reason="no internet")
def test_live_search_really_reaches_the_internet():
    async def go():
        wc = research.WebClient(timeout=10)
        try:
            return await research.gather(wc, "Python programming language latest release", 3)
        finally:
            await wc.aclose()

    ev = asyncio.run(go())
    searched = [e for e in ev.log if e["step"] == "search"]
    assert searched and searched[-1]["status"] == "ok" and searched[-1]["urls"]   # a real provider returned real URLs
    assert ev.status == "ok" and all(s["url"].startswith("http") for s in ev.sources)
