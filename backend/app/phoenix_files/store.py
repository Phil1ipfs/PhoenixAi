"""SQLite persistence for uploaded documents. Only extracted text + metadata are stored (never the original bytes)."""
from __future__ import annotations

import json
import uuid

from ..db import Database, now
from .chunk import Chunk
from .retrieve import Index


class FileStore:
    def __init__(self, db: Database):
        self.db = db
        self.conn = db.conn
        self._index_cache: dict[tuple, Index] = {}

    # ---- helpers
    @staticmethod
    def public(r) -> dict:
        d = dict(r)
        d["warnings"] = json.loads(d["warnings"]) if d.get("warnings") else []
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else {}
        d.pop("sha256", None)
        return d

    # ---- files
    def add_file(self, *, name: str, kind: str, size: int, sha: str, status: str, detail: str, pages: int | None,
                 chars: int, warnings: list[str], meta: dict, chunks: list[Chunk]) -> dict:
        fid = uuid.uuid4().hex
        with self.conn:
            self.conn.execute(
                "INSERT INTO files(id,name,kind,size_bytes,sha256,status,detail,pages,chars,chunk_count,warnings,meta,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, name, kind, size, sha, status, detail, pages, chars, len(chunks), json.dumps(warnings), json.dumps(meta), now()))
            self.conn.executemany(
                "INSERT INTO file_chunks(file_id,idx,loc,section,page_start,page_end,line_start,line_end,text) VALUES(?,?,?,?,?,?,?,?,?)",
                [(fid, c.idx, c.loc, c.section, c.page_start, c.page_end, c.line_start, c.line_end, c.text) for c in chunks])
        return self.get(fid)

    def get(self, fid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        return self.public(r) if r else None

    def find_by_sha(self, sha: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM files WHERE sha256=? ORDER BY created_at LIMIT 1", (sha,)).fetchone()
        return self.public(r) if r else None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def list(self, conversation_id: str | None = None) -> list[dict]:
        if conversation_id:
            rows = self.conn.execute("SELECT f.* FROM files f JOIN conversation_files cf ON cf.file_id=f.id "
                                     "WHERE cf.conversation_id=? ORDER BY cf.attached_at, f.rowid", (conversation_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM files ORDER BY created_at DESC, rowid DESC").fetchall()
        out = [self.public(r) for r in rows]
        if not conversation_id:
            att: dict[str, list[str]] = {}
            for r in self.conn.execute("SELECT file_id, conversation_id FROM conversation_files"):
                att.setdefault(r["file_id"], []).append(r["conversation_id"])
            for f in out:
                f["conversations"] = att.get(f["id"], [])
        return out

    def delete(self, fid: str) -> bool:
        with self.conn:
            self.conn.execute("UPDATE message_files SET file_id=NULL WHERE file_id=?", (fid,))   # history keeps the name, marked removed
            cur = self.conn.execute("DELETE FROM files WHERE id=?", (fid,))                       # cascades chunks + attachments
        self._index_cache.clear()
        return cur.rowcount > 0

    # ---- attachments
    def attach(self, cid: str, fid: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO conversation_files(conversation_id,file_id,attached_at) VALUES(?,?,?)", (cid, fid, now()))
        self.conn.commit()

    def detach(self, cid: str, fid: str) -> bool:
        cur = self.conn.execute("DELETE FROM conversation_files WHERE conversation_id=? AND file_id=?", (cid, fid))
        self.conn.commit()
        return cur.rowcount > 0

    def is_attached(self, cid: str, fid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM conversation_files WHERE conversation_id=? AND file_id=?", (cid, fid)).fetchone() is not None

    def attachment_count(self, cid: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM conversation_files WHERE conversation_id=?", (cid,)).fetchone()[0]

    def other_conversations(self, fid: str, cid: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM conversation_files WHERE file_id=? AND conversation_id<>?", (fid, cid)).fetchone()[0]

    def exclusive_files(self, cid: str) -> list[str]:
        """Files attached to this conversation and to no other (they go away with the conversation)."""
        rows = self.conn.execute(
            "SELECT file_id FROM conversation_files WHERE conversation_id=? AND file_id NOT IN "
            "(SELECT file_id FROM conversation_files WHERE conversation_id<>?)", (cid, cid)).fetchall()
        return [r[0] for r in rows]

    def link_message(self, message_id: int, files: list[dict]) -> None:
        self.conn.executemany("INSERT INTO message_files(message_id,file_id,name,kind) VALUES(?,?,?,?)",
                              [(message_id, f["id"], f["name"], f["kind"]) for f in files])
        self.conn.commit()

    # ---- chunks / retrieval
    def chunks(self, file_ids: list[str]) -> list[dict]:
        if not file_ids:
            return []
        q = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT c.file_id, f.name AS file_name, f.kind, c.idx, c.loc, c.section, c.page_start, c.page_end, c.line_start, c.line_end, c.text "
            f"FROM file_chunks c JOIN files f ON f.id=c.file_id WHERE c.file_id IN ({q}) AND f.status='ready' ORDER BY c.file_id, c.idx", file_ids).fetchall()
        return [dict(r) for r in rows]

    def chunk(self, fid: str, idx: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM file_chunks WHERE file_id=? AND idx=?", (fid, idx)).fetchone()
        return dict(r) if r else None

    def index(self, file_ids: list[str]) -> Index:
        key = tuple(sorted(file_ids))
        idx = self._index_cache.get(key)
        if idx is None:
            if len(self._index_cache) >= 4:
                self._index_cache.pop(next(iter(self._index_cache)))
            idx = self._index_cache[key] = Index(self.chunks(list(key)))
        return idx

    def outline(self, fid: str) -> list[dict]:
        rows = self.conn.execute("SELECT idx, loc, section, page_start FROM file_chunks WHERE file_id=? ORDER BY idx", (fid,)).fetchall()
        seen, out = set(), []
        for r in rows:
            key = r["section"] or (f"p{r['page_start']}" if r["page_start"] else None)
            if key and key not in seen:
                seen.add(key)
                out.append({"idx": r["idx"], "title": r["section"] or f"Page {r['page_start']}", "loc": r["loc"]})
        return out[:300]
