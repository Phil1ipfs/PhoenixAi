"""HTTP API for PHOENIX Files. Uploads are raw request bodies (no multipart dependency): POST /api/files?name=report.pdf."""
from __future__ import annotations

import asyncio
import hashlib
import re

from fastapi import FastAPI, HTTPException, Query, Request

from . import limits
from .chunk import pack
from .extract import SUPPORTED, UploadError, classify, check_name_sensitive, extract, max_bytes, safe_filename
from .retrieve import query_terms
from .store import FileStore


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def snippet(text: str, terms: list[str], width: int = 320) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if t and low.find(t) >= 0), default=0)
    a = max(0, pos - width // 4)
    s = text[a:a + width].strip()
    return ("…" if a else "") + s + ("…" if a + width < len(text) else "")


def register_files(app: FastAPI, ctx, store: FileStore) -> FileStore:
    db = ctx.db
    ctx.files = store

    def need_conv(cid: str):
        if not db.get_conversation(cid):
            raise HTTPException(404, "Conversation not found")

    @app.get("/api/files/limits")
    async def file_limits():
        return {"supported_extensions": SUPPORTED, "max_bytes": limits.MAX_BYTES, "max_pdf_pages": limits.MAX_PDF_PAGES,
                "max_attached": limits.MAX_ATTACHED, "max_files": limits.MAX_FILES}

    @app.post("/api/files", status_code=201)
    async def upload(request: Request, name: str = Query(..., max_length=400), conversation_id: str | None = None):
        try:
            safe = safe_filename(name)
            kind = classify(safe)
            check_name_sensitive(safe)
        except UploadError as e:
            raise err(e.status, e.code, e.message)
        if conversation_id:
            need_conv(conversation_id)
        limit = max_bytes(kind)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise err(413, "too_large", f"The file is {int(declared) / 2**20:.1f} MB; the limit for this type is {limit // 2**20} MB.")
        buf = bytearray()
        async for part in request.stream():
            buf.extend(part)
            if len(buf) > limit:
                raise err(413, "too_large", f"The file is larger than the {limit // 2**20} MB limit for this type.")
        data = bytes(buf)
        sha = hashlib.sha256(data).hexdigest()
        existing = store.find_by_sha(sha)
        if conversation_id and not (existing and store.is_attached(conversation_id, existing["id"])) \
                and store.attachment_count(conversation_id) >= limits.MAX_ATTACHED:
            raise err(409, "too_many_files", f"A conversation can have at most {limits.MAX_ATTACHED} attached files; remove one first.")
        if existing:
            if conversation_id:
                store.attach(conversation_id, existing["id"])
            return {**existing, "duplicate": True}
        if store.count() >= limits.MAX_FILES:
            raise err(409, "library_full", f"PHOENIX keeps at most {limits.MAX_FILES} uploaded files; delete some first.")
        try:
            ex = await asyncio.to_thread(extract, safe, data)
        except UploadError as e:
            raise err(e.status, e.code, e.message)
        chunks, cw = await asyncio.to_thread(pack, ex)
        warnings = [*ex.warnings, *cw]
        pages = ex.meta.get("pages") if ex.kind == "pdf" else None
        row = store.add_file(name=safe, kind=ex.kind, size=len(data), sha=sha, status=ex.status, detail=ex.detail, pages=pages,
                             chars=sum(len(p.text) for p in ex.paras), warnings=warnings, meta=ex.meta, chunks=chunks)
        if conversation_id:
            store.attach(conversation_id, row["id"])
        return {**row, "duplicate": False}

    @app.get("/api/files")
    async def list_files(conversation_id: str | None = None):
        if conversation_id:
            need_conv(conversation_id)
        return store.list(conversation_id)

    @app.get("/api/files/search")
    async def search_files(q: str = Query(..., min_length=1, max_length=300), conversation_id: str | None = None,
                           file_id: str | None = None, limit: int = Query(8, ge=1, le=25)):
        """Deterministic keyword search (BM25) in uploaded documents. No model involved."""
        if file_id:
            if not store.get(file_id):
                raise HTTPException(404, "File not found")
            ids = [file_id]
        elif conversation_id:
            need_conv(conversation_id)
            ids = [f["id"] for f in store.list(conversation_id)]
        else:
            ids = [f["id"] for f in store.list()]
        hits = store.index(ids).search(q, limit=limit) if ids else []
        terms = query_terms(q)
        return {"query": q, "terms": terms, "count": len(hits),
                "results": [{"file_id": h["file_id"], "file_name": h["file_name"], "idx": h["idx"], "loc": h["loc"],
                             "section": h["section"], "page_start": h["page_start"], "page_end": h["page_end"],
                             "line_start": h["line_start"], "line_end": h["line_end"], "score": h["score"],
                             "snippet": snippet(h["text"], [t for t in re.findall(r"[a-z0-9]+", q.lower())] or terms)} for h in hits]}

    @app.get("/api/files/{fid}")
    async def get_file(fid: str):
        f = store.get(fid)
        if not f:
            raise HTTPException(404, "File not found")
        return f

    @app.get("/api/files/{fid}/outline")
    async def outline(fid: str):
        if not store.get(fid):
            raise HTTPException(404, "File not found")
        return store.outline(fid)

    @app.get("/api/files/{fid}/chunks/{idx}")
    async def get_chunk(fid: str, idx: int):
        c = store.chunk(fid, idx)
        if not c:
            raise HTTPException(404, "Section not found")
        return {k: c[k] for k in ("file_id", "idx", "loc", "section", "page_start", "page_end", "line_start", "line_end", "text")}

    @app.delete("/api/files/{fid}", status_code=204)
    async def delete_file(fid: str):
        if not store.delete(fid):
            raise HTTPException(404, "File not found")

    # ---- conversation attachments
    @app.get("/api/conversations/{cid}/files")
    async def conversation_files(cid: str):
        need_conv(cid)
        return store.list(cid)

    @app.post("/api/conversations/{cid}/files/{fid}", status_code=201)
    async def attach(cid: str, fid: str):
        need_conv(cid)
        if not store.get(fid):
            raise HTTPException(404, "File not found")
        if not store.is_attached(cid, fid) and store.attachment_count(cid) >= limits.MAX_ATTACHED:
            raise err(409, "too_many_files", f"A conversation can have at most {limits.MAX_ATTACHED} attached files; remove one first.")
        store.attach(cid, fid)
        return store.get(fid)

    @app.delete("/api/conversations/{cid}/files/{fid}")
    async def remove_from_conversation(cid: str, fid: str):
        """Remove a document from this conversation. If no other conversation uses it, the file and its text are erased."""
        need_conv(cid)
        if not store.detach(cid, fid):
            raise HTTPException(404, "File is not attached to this conversation")
        deleted = False
        if store.other_conversations(fid, cid) == 0:
            deleted = store.delete(fid)
        return {"detached": True, "deleted": deleted}

    return store
