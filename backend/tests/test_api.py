import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.chat import build_messages
from app.db import Database
from app.main import create_app
from app.providers import ChatChunk, ModelProvider, ProviderError


class FakeProvider(ModelProvider):
    """Test double for the transport only; never used by the running app."""

    def __init__(self, tokens=("Hel", "lo"), error=None):
        self.tokens, self.error, self.calls = tokens, error, []

    async def health(self):
        return {"connected": True, "version": "fake", "url": "fake"}

    async def list_models(self):
        return [{"name": "qwen2.5-coder:3b", "size": 1}, {"name": "qwen2.5-coder:7b", "size": 2}]

    async def loaded_models(self):
        return []

    async def chat_stream(self, model, messages, options, timeout):
        self.calls.append((model, messages, options))
        for t in self.tokens:
            yield ChatChunk(content=t)
        if self.error:
            raise self.error
        yield ChatChunk(done=True, metrics={"completion_tokens": len(self.tokens), "tokens_per_sec": 9.9})

    async def aclose(self):
        pass


def make(tmp_path, **kw):
    p = FakeProvider(**kw)
    return TestClient(create_app(tmp_path / "t.db", p)), p


def events(resp):
    return [json.loads(l) for l in resp.text.splitlines() if l.strip()]


def test_health_and_models(tmp_path):
    c, _ = make(tmp_path)
    h = c.get("/api/health").json()
    assert h["ollama"]["connected"] and h["model_installed"]
    assert [m["name"] for m in c.get("/api/models").json()["models"]] == ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]


def test_chat_streams_and_persists(tmp_path):
    c, _ = make(tmp_path)
    cid = c.post("/api/conversations").json()["id"]
    ev = events(c.post(f"/api/conversations/{cid}/messages", json={"content": "hi there"}))
    assert [e["type"] for e in ev] == ["start", "token", "token", "done"]
    conv = c.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["content"] == "Hello"
    assert conv["messages"][1]["metrics"]["tokens_per_sec"] == 9.9
    assert conv["title"] == "hi there"


def test_error_is_reported(tmp_path):
    c, _ = make(tmp_path, tokens=(), error=ProviderError("timeout", "slow"))
    cid = c.post("/api/conversations").json()["id"]
    ev = events(c.post(f"/api/conversations/{cid}/messages", json={"content": "x"}))
    assert ev[-1] == {"type": "error", "kind": "timeout", "message": "slow"}


def test_model_switch_keeps_history(tmp_path):
    c, p = make(tmp_path)
    cid = c.post("/api/conversations").json()["id"]
    c.post(f"/api/conversations/{cid}/messages", json={"content": "one"})
    c.put("/api/settings", json={"model": "qwen2.5-coder:7b"})
    c.post(f"/api/conversations/{cid}/messages", json={"content": "two"})
    assert p.calls[0][0] == "qwen2.5-coder:3b" and p.calls[1][0] == "qwen2.5-coder:7b"
    # second call saw the earlier turn
    assert any(m["content"] == "one" for m in p.calls[1][1])
    msgs = c.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["model"] for m in msgs if m["role"] == "assistant"] == ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]


def test_delete_and_404(tmp_path):
    c, _ = make(tmp_path)
    cid = c.post("/api/conversations").json()["id"]
    assert c.delete(f"/api/conversations/{cid}").status_code == 204
    assert c.get(f"/api/conversations/{cid}").status_code == 404
    assert c.post(f"/api/conversations/{cid}/messages", json={"content": "x"}).status_code == 404


def test_settings_validation_and_local_only(tmp_path):
    c, _ = make(tmp_path)
    assert c.put("/api/settings", json={"num_ctx": 10}).status_code == 422
    assert c.put("/api/settings", json={"ollama_url": "http://evil.example.com"}).status_code == 400
    assert c.put("/api/settings", json={"temperature": 0.5}).json()["temperature"] == 0.5


def test_ollama_url_rejects_lookalike_hosts(tmp_path):
    c, _ = make(tmp_path)
    for bad in ("http://127.0.0.1.evil.com", "http://localhost.evil.com:11434", "http://localhost@evil.com",
                "http://127.0.0.1:11434@evil.com", "ftp://127.0.0.1", "http://[::2]:11434"):
        assert c.put("/api/settings", json={"ollama_url": bad}).status_code == 400, bad
    for good in ("http://127.0.0.1:11434/", "http://localhost:11434", "http://[::1]:11434"):
        assert c.put("/api/settings", json={"ollama_url": good}).status_code == 200, good


def test_rejects_foreign_host_and_cross_site_writes(tmp_path):
    c, _ = make(tmp_path)
    # DNS rebinding: attacker's domain resolves to 127.0.0.1, so the browser sends that domain as Host
    assert c.get("/api/settings", headers={"Host": "evil.example.com:8000"}).status_code == 403
    assert c.get("/api/settings", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert c.get("/api/settings", headers={"Host": "[::1]:8000"}).status_code == 200
    # cross-site writes, incl. a text/plain body that needs no CORS preflight
    for hdr in ({"Origin": "https://evil.example.com"}, {"Origin": "null"}, {"Origin": "http://127.0.0.1.evil.com"}):
        assert c.post("/api/conversations", content=b"{}", headers={**hdr, "Content-Type": "text/plain"}).status_code == 403, hdr
        assert c.put("/api/settings", json={"temperature": 1}, headers=hdr).status_code == 403, hdr
    assert c.get("/api/conversations").status_code == 200
    # the real UI (same origin and Vite dev server) and non-browser clients still work
    for hdr in ({"Origin": "http://127.0.0.1:8000"}, {"Origin": "http://localhost:5173"}, {}):
        assert c.post("/api/conversations", headers=hdr).status_code == 201, hdr


def test_persistence_across_reopen(tmp_path):
    db = Database(tmp_path / "p.db")
    cid = db.create_conversation("m")["id"]
    db.add_message(cid, "user", "hello")
    db.close()
    db2 = Database(tmp_path / "p.db")
    assert db2.list_messages(cid)[0]["content"] == "hello"


def test_prompt_trimming():
    hist = [{"role": "user", "content": "a" * 100} for _ in range(10)]
    msgs = build_messages("sys", hist, 350)
    assert msgs[0]["role"] == "system" and len(msgs) == 4
    giant = build_messages("sys", [{"role": "user", "content": "z" * 10000}], 1000)
    assert len(giant[-1]["content"]) <= 1000


def test_upgrade_from_v1_database_keeps_data(tmp_path):
    """A DB created by PHOENIX v1.0 (no patches table, no workspace settings) must open unchanged."""
    import sqlite3
    path = tmp_path / "v1.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL,
        config TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL, content TEXT NOT NULL, model TEXT, metrics TEXT, created_at TEXT NOT NULL);
    CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
    CREATE TABLE benchmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, results TEXT NOT NULL, created_at TEXT NOT NULL);
    INSERT INTO settings VALUES ('model', '"qwen2.5-coder:7b"'), ('temperature', '0.7');
    INSERT INTO conversations VALUES ('c1','Old chat','qwen2.5-coder:3b','{}','2026-01-01T00:00:00','2026-01-01T00:00:00');
    INSERT INTO messages(conversation_id,role,content,model,created_at) VALUES ('c1','user','hello','qwen2.5-coder:3b','2026-01-01T00:00:00');
    """)
    con.commit(); con.close()
    c = TestClient(create_app(path, FakeProvider()))
    s = c.get("/api/settings").json()
    assert s["model"] == "qwen2.5-coder:7b" and s["temperature"] == 0.7      # saved settings preserved
    assert s["workspace_num_ctx"] == 4096                                      # new settings get defaults
    assert c.get("/api/conversations/c1").json()["messages"][0]["content"] == "hello"
    assert c.get("/api/projects/none/patches").json() == []                    # new table exists
