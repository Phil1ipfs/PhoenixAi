"""SQLite persistence. Model-independent: model ids are plain strings stored per message."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SETTINGS = {
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen2.5-coder:3b",
    "system_prompt": (
        "You are PHOENIX, a local programming assistant. Be concise and accurate. "
        "Use fenced code blocks with a language tag. If you are unsure, say so rather than guessing."
    ),
    "num_ctx": 2048,
    "temperature": 0.2,
    "num_predict": 1024,
    "timeout_s": 120,
    "max_prompt_chars": 6000,
    "workspace_num_ctx": 4096,
    "workspace_context_chars": 6000,
    "web_mode": "off",          # off | auto (search only for time-sensitive questions) | always
    "web_max_sources": 3,
    "web_timeout_s": 8,
    "web_contact": "",          # optional email/URL added to the User-Agent (Wikimedia asks bots to identify an operator)
    "wikimedia_pages": False,   # full Wikipedia page reads: opt-in, and only works when web_contact is also set
    "doc_context_chars": 3000,  # characters of retrieved document excerpts sent to the model per question (prompt speed is ~28 tok/s)
    "keep_alive": "5m",         # how long Ollama keeps the model in RAM after a reply ("0" = unload immediately)
    "num_thread": 0,            # 0 = let Ollama decide
    "memory_guard": True,
    "min_free_mb": 600,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system','user','assistant')),
    content TEXT NOT NULL, model TEXT, metrics TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS patches (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
    original TEXT, proposed TEXT NOT NULL, original_hash TEXT, proposed_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, applied_at TEXT, rolled_back_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, results TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
    status TEXT NOT NULL, detail TEXT, pages INTEGER, chars INTEGER NOT NULL DEFAULT 0, chunk_count INTEGER NOT NULL DEFAULT 0,
    warnings TEXT, meta TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS file_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
    loc TEXT NOT NULL, section TEXT, page_start INTEGER, page_end INTEGER, line_start INTEGER, line_end INTEGER, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON file_chunks(file_id, idx);
CREATE TABLE IF NOT EXISTS conversation_files (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, attached_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, file_id)
);
CREATE TABLE IF NOT EXISTS message_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_id TEXT, name TEXT NOT NULL, kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_mf_msg ON message_files(message_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        if "meta" not in {r[1] for r in self.conn.execute("PRAGMA table_info(patches)")}:
            self.conn.execute("ALTER TABLE patches ADD COLUMN meta TEXT")  # added in 1.2: validation info per patch
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- settings
    def get_settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for r in self.conn.execute("SELECT key, value FROM settings"):
            if r["key"] in out:
                out[r["key"]] = json.loads(r["value"])
        return out

    def update_settings(self, changes: dict) -> dict:
        for k, v in changes.items():
            if k in DEFAULT_SETTINGS:
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v)),
                )
        self.conn.commit()
        return self.get_settings()

    # ---- conversations
    def create_conversation(self, model: str, title: str = "New conversation", config: dict | None = None) -> dict:
        cid, ts = uuid.uuid4().hex, now()
        self.conn.execute(
            "INSERT INTO conversations(id,title,model,config,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (cid, title, model, json.dumps(config or {}), ts, ts),
        )
        self.conn.commit()
        return self.get_conversation(cid)

    def get_conversation(self, cid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return _conv(r) if r else None

    def list_conversations(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC, rowid DESC").fetchall()
        return [_conv(r) for r in rows]

    def update_conversation(self, cid: str, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in ("title", "model") and v is not None}
        if allowed:
            sets = ",".join(f"{k}=?" for k in allowed)
            self.conn.execute(f"UPDATE conversations SET {sets}, updated_at=? WHERE id=?", (*allowed.values(), now(), cid))
            self.conn.commit()
        return self.get_conversation(cid)

    def delete_conversation(self, cid: str) -> bool:
        cur = self.conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- messages
    def add_message(self, cid: str, role: str, content: str, model: str | None = None, metrics: dict | None = None) -> dict:
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO messages(conversation_id,role,content,model,metrics,created_at) VALUES(?,?,?,?,?,?)",
            (cid, role, content, model, json.dumps(metrics) if metrics else None, ts),
        )
        self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, cid))
        self.conn.commit()
        return {"id": cur.lastrowid, "role": role, "content": content, "model": model, "metrics": metrics, "created_at": ts}

    def list_messages(self, cid: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        files: dict[int, list[dict]] = {}
        for f in self.conn.execute("SELECT mf.message_id, mf.file_id, mf.name, mf.kind FROM message_files mf "
                                   "JOIN messages m ON m.id=mf.message_id WHERE m.conversation_id=? ORDER BY mf.id", (cid,)):
            files.setdefault(f["message_id"], []).append({"file_id": f["file_id"], "name": f["name"], "kind": f["kind"],
                                                          "removed": f["file_id"] is None})
        return [
            {"id": r["id"], "role": r["role"], "content": r["content"], "model": r["model"],
             "metrics": json.loads(r["metrics"]) if r["metrics"] else None, "created_at": r["created_at"],
             **({"files": files[r["id"]]} if r["id"] in files else {})}
            for r in rows
        ]

    # ---- projects
    def add_project(self, name: str, path: str) -> dict:
        pid = uuid.uuid4().hex
        self.conn.execute("INSERT INTO projects(id,name,path,created_at) VALUES(?,?,?,?)", (pid, name, path, now()))
        self.conn.commit()
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def list_projects(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM projects ORDER BY name")]

    def delete_project(self, pid: str) -> bool:
        cur = self.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- benchmarks
    def add_benchmark(self, model: str, results: dict) -> None:
        self.conn.execute("INSERT INTO benchmarks(model,results,created_at) VALUES(?,?,?)", (model, json.dumps(results), now()))
        self.conn.commit()


def _conv(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["config"] = json.loads(d["config"])
    return d
