"""PHOENIX AI backend. Binds to 127.0.0.1 by default (see run.py)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import research, sysinfo
from .chat import auto_title, build_messages
from .db import DEFAULT_SETTINGS, Database
from .benchmark_routes import register_benchmark
from .patch_routes import register_patches
from .workspace_routes import register_workspace
from .providers import ModelProvider, OllamaProvider, ProviderError

DATA_DIR = Path(os.environ.get("PHOENIX_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
LOCAL_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"]


class SettingsUpdate(BaseModel):
    ollama_url: str | None = None
    model: str | None = Field(None, min_length=1, max_length=200)
    system_prompt: str | None = Field(None, max_length=8000)
    num_ctx: int | None = Field(None, ge=512, le=32768)
    temperature: float | None = Field(None, ge=0, le=2)
    num_predict: int | None = Field(None, ge=16, le=8192)
    timeout_s: int | None = Field(None, ge=5, le=1800)
    max_prompt_chars: int | None = Field(None, ge=500, le=100000)
    workspace_num_ctx: int | None = Field(None, ge=1024, le=32768)
    workspace_context_chars: int | None = Field(None, ge=1000, le=60000)
    web_mode: str | None = Field(None, pattern="^(off|auto|always)$")
    web_max_sources: int | None = Field(None, ge=1, le=6)
    web_timeout_s: int | None = Field(None, ge=2, le=30)
    web_contact: str | None = Field(None, max_length=200, pattern=r"^[^\r\n]*$")
    keep_alive: str | None = Field(None, pattern=r"^(-1|\d+[smh]?)$")
    num_thread: int | None = Field(None, ge=0, le=32)
    memory_guard: bool | None = None
    min_free_mb: int | None = Field(None, ge=0, le=8000)


class ConversationCreate(BaseModel):
    model: str | None = None
    title: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    model: str | None = None


class SendMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)
    model: str | None = None


def ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def create_app(db_path: Path | str | None = None, provider: ModelProvider | None = None,
               web: research.WebClient | None = None) -> FastAPI:
    db = Database(db_path or DATA_DIR / "phoenix.db")
    state = {"provider": provider or OllamaProvider(db.get_settings()["ollama_url"]), "custom": provider is not None}
    web_holder: dict = {"client": web, "injected": web is not None}
    gen_lock = asyncio.Lock()  # one generation at a time: protects RAM on a 12 GB machine

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await state["provider"].aclose()
        if web_holder["client"]:
            await web_holder["client"].aclose()
        db.close()

    app = FastAPI(title="PHOENIX AI", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=LOCAL_ORIGINS, allow_methods=["*"], allow_headers=["*"])
    app.state.db = db
    app.state.get_provider = lambda: state["provider"]

    # ---------------- health / models
    @app.get("/api/health")
    async def health():
        s = db.get_settings()
        h = await state["provider"].health()
        models, model_installed = [], False
        if h["connected"]:
            try:
                models = [m["name"] for m in await state["provider"].list_models()]
                model_installed = s["model"] in models
            except ProviderError:
                pass
        loaded = await state["provider"].loaded_models() if h["connected"] else []
        return {"backend": "ok", "ollama": h, "active_model": s["model"], "model_installed": model_installed,
                "memory": sysinfo.memory(), "loaded": loaded}

    @app.get("/api/models")
    async def models():
        try:
            installed = await state["provider"].list_models()
        except ProviderError as e:
            raise HTTPException(503, {"kind": e.kind, "message": e.message})
        return {"active": db.get_settings()["model"], "models": installed,
                "loaded": await state["provider"].loaded_models()}

    @app.post("/api/models/unload")
    async def unload_model():
        """Free the model's RAM right now (it reloads on the next request, ~5-15 s)."""
        if gen_lock.locked():
            raise HTTPException(409, "A generation is running; unload after it finishes")
        s = db.get_settings()
        try:
            await state["provider"].unload(s["model"])
        except ProviderError as e:
            raise HTTPException(503, {"kind": e.kind, "message": e.message})
        return {"unloaded": s["model"], "memory": sysinfo.memory()}

    # ---------------- settings
    @app.get("/api/settings")
    async def get_settings():
        return db.get_settings()

    @app.put("/api/settings")
    async def put_settings(body: SettingsUpdate):
        changes = body.model_dump(exclude_none=True)
        if "ollama_url" in changes:
            url = changes["ollama_url"].rstrip("/")
            if not url.startswith(("http://127.0.0.1", "http://localhost")):
                raise HTTPException(400, "ollama_url must be a local address (127.0.0.1 or localhost)")
            changes["ollama_url"] = url
            if not state["custom"]:
                await state["provider"].aclose()
                state["provider"] = OllamaProvider(url)
        return db.update_settings(changes)

    @app.post("/api/settings/reset")
    async def reset_settings():
        db.conn.execute("DELETE FROM settings")
        db.conn.commit()
        return db.get_settings()

    # ---------------- conversations
    @app.get("/api/conversations")
    async def list_conversations():
        return db.list_conversations()

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(body: ConversationCreate | None = None):
        body = body or ConversationCreate()
        return db.create_conversation(body.model or db.get_settings()["model"], body.title or "New conversation")

    @app.get("/api/conversations/{cid}")
    async def get_conversation(cid: str):
        c = db.get_conversation(cid)
        if not c:
            raise HTTPException(404, "Conversation not found")
        return {**c, "messages": db.list_messages(cid)}

    @app.patch("/api/conversations/{cid}")
    async def patch_conversation(cid: str, body: ConversationPatch):
        if not db.get_conversation(cid):
            raise HTTPException(404, "Conversation not found")
        return db.update_conversation(cid, title=body.title, model=body.model)

    @app.delete("/api/conversations/{cid}", status_code=204)
    async def delete_conversation(cid: str):
        if not db.delete_conversation(cid):
            raise HTTPException(404, "Conversation not found")

    # ---------------- chat (streaming NDJSON)
    def get_web(s: dict) -> research.WebClient:
        key = (s["web_timeout_s"], s["web_contact"])
        if web_holder["client"] is None or (web_holder.get("key") not in (None, key) and not web_holder.get("injected")):
            web_holder["client"] = research.WebClient(timeout=s["web_timeout_s"], contact=s["web_contact"])
        web_holder["key"] = web_holder.get("key") or key
        return web_holder["client"]

    @app.post("/api/web/test")
    async def web_test():
        """Connectivity check for the Settings page: one tiny Wikipedia query. Never touches the model."""
        try:
            r = await research.search_wikipedia(get_web(db.get_settings()), "Python programming language", 1)
            return {"ok": bool(r), "provider": "Wikipedia API"}
        except research.WebError as e:
            return {"ok": False, "error": e.message}

    @app.post("/api/conversations/{cid}/messages")
    async def send_message(cid: str, body: SendMessage):
        conv = db.get_conversation(cid)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        if gen_lock.locked():
            raise HTTPException(409, "Another generation is already running")

        s = db.get_settings()
        model = body.model or s["model"]
        first = not db.list_messages(cid)
        user_msg = db.add_message(cid, "user", body.content)
        if first:
            db.update_conversation(cid, title=auto_title(body.content))
        db.update_conversation(cid, model=model)
        messages = build_messages(s["system_prompt"], db.list_messages(cid), s["max_prompt_chars"])

        # ---- evidence workflow: detect -> retrieve -> ground -> (after generation) verify citations
        det = research.needs_verification(body.content)
        mode = s["web_mode"]
        use_web = mode == "always" or (mode == "auto" and det["needed"])
        web_meta: dict = {"mode": mode, "needed": det["needed"], "reasons": det["reasons"], "sources": [],
                          "status": "unverified_disabled" if (det["needed"] and mode == "off") else "not_needed"}
        full_sources: list[dict] = []

        def add_system(msgs: list[dict], extra: str) -> None:
            if msgs and msgs[0]["role"] == "system":
                msgs[0]["content"] += extra
            else:
                msgs.insert(0, {"role": "system", "content": extra.strip()})

        async def prepare():
            msgs = [dict(m) for m in messages]
            if use_web:
                yield {"type": "status", "message": "Searching the web…"}
                try:
                    ev = await research.gather(get_web(s), body.content, s["web_max_sources"])
                except Exception as e:  # noqa: BLE001 - research must never break chat
                    ev = research.Evidence("error", note=f"{type(e).__name__}: {e}")
                if ev.status == "ok" and ev.sources:
                    full_sources.extend(ev.sources)
                    public = [{k: x.get(k) for k in ("id", "title", "url", "fetched", "retrieved_at")} | {"excerpt": x["excerpt"][:400]}
                              for x in ev.sources]
                    web_meta.update(status="sources_found", queries=ev.queries, note=ev.note, sources=public)
                    yield {"type": "sources", "sources": public}
                    add_system(msgs, research.WEB_SYSTEM)
                    msgs[-1]["content"] = research.build_web_prompt(body.content, ev.sources)
                else:
                    web_meta.update(status="unverified_offline" if ev.status in ("offline", "error") else "unverified_no_results",
                                    note=ev.note)
            if web_meta["status"].startswith("unverified"):
                why = {"unverified_disabled": "internet access is turned off in Settings",
                       "unverified_offline": "the web search failed: " + (web_meta.get("note") or "network unavailable"),
                       "unverified_no_results": "the search returned no usable results"}[web_meta["status"]]
                add_system(msgs, research.UNVERIFIED_SYSTEM.format(why=why))
                yield {"type": "warning", "message": "Not verified live: " + why}
            yield {"_result": (msgs, {"web": web_meta})}

        def post(text: str) -> str:
            if full_sources:
                cit = research.verify_citations(text, full_sources)
                text = research.sanitize_answer(text, cit["invalid_ids"])
                web_meta["citations"] = cit
                web_meta["status"] = ("sources_not_cited" if not cit["valid_ids"]
                                      else "grounded_weak" if cit["weak"] or cit["invalid_ids"] else "grounded")
            return text

        return generate(cid, user_msg, messages, model, base_options(s), s, prepare=prepare, post=post)

    def base_options(s: dict, **over) -> dict:
        o = {"num_ctx": s["num_ctx"], "temperature": s["temperature"], "num_predict": s["num_predict"],
             "keep_alive": s["keep_alive"], **over}
        if s["num_thread"]:
            o["num_thread"] = s["num_thread"]
        return o

    async def memory_guard(model: str, options: dict, s: dict) -> dict:
        if not s["memory_guard"]:
            return {"action": "ok", "num_ctx": options["num_ctx"]}
        avail = sysinfo.memory()["available_mb"]
        try:
            loaded = {m["name"] for m in await state["provider"].loaded_models()}
            sizes = {m["name"]: m["size"] / 2**20 for m in await state["provider"].list_models()}
        except ProviderError:
            return {"action": "ok", "num_ctx": options["num_ctx"]}
        return sysinfo.assess_memory(avail, model in loaded, sizes.get(model, 0), s["min_free_mb"], options["num_ctx"])

    def generate(cid: str, user_msg: dict, messages: list[dict], model: str, options: dict, s: dict,
                 extra_metrics: dict | None = None, prepare=None, post=None) -> StreamingResponse:
        """Stream one assistant reply as NDJSON and persist it (partial text too if the client stops)."""

        async def stream() -> AsyncIterator[str]:
            nonlocal messages, options, extra_metrics
            async with gen_lock:
                yield ndjson({"type": "start", "user_message": user_msg, "model": model})
                guard = await memory_guard(model, options, s)
                if guard["action"] == "block":
                    yield ndjson({"type": "error", "kind": "low_memory", "message": guard["message"]})
                    return
                if guard["action"] == "warn":
                    options = {**options, "num_ctx": guard["num_ctx"]}
                    yield ndjson({"type": "warning", "message": guard["message"]})
                if prepare:
                    async for ev in prepare():
                        if "_result" in ev:
                            messages, extra_metrics = ev["_result"][0], {**(extra_metrics or {}), **ev["_result"][1]}
                        else:
                            yield ndjson(ev)
                parts: list[str] = []
                metrics: dict = {}
                status = "complete"
                t0 = time.perf_counter()
                first_token_ms = None
                mem_before = sysinfo.memory()
                try:
                    async for chunk in state["provider"].chat_stream(model, messages, options, s["timeout_s"]):
                        if chunk.content:
                            if first_token_ms is None:
                                first_token_ms = round((time.perf_counter() - t0) * 1000)
                            parts.append(chunk.content)
                            yield ndjson({"type": "token", "content": chunk.content})
                        if chunk.done:
                            metrics = chunk.metrics
                except ProviderError as e:
                    status = "error"
                    yield ndjson({"type": "error", "kind": e.kind, "message": e.message})
                except asyncio.CancelledError:
                    status = "stopped"
                    raise
                finally:
                    text = "".join(parts)
                    if text or status == "complete":
                        if status == "complete" and post:
                            text = post(text)
                        mem_after = sysinfo.memory()
                        metrics = {**metrics, **(extra_metrics or {}), "status": status, "first_token_ms": first_token_ms,
                                   "wall_ms": round((time.perf_counter() - t0) * 1000),
                                   "available_mb_before": mem_before["available_mb"],
                                   "available_mb_after": mem_after["available_mb"]}
                        msg = db.add_message(cid, "assistant", text, model=model, metrics=metrics)
                        if status == "complete":
                            yield ndjson({"type": "done", "message": msg})  # noqa: B012
                        # (when stopped, the client is gone; the partial text is persisted above)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def collect(messages: list[dict], model: str | None = None, **opt_over) -> tuple[str, dict]:
        """Non-streaming generation (patch proposals, benchmarks). Caller must hold gen_lock."""
        s = db.get_settings()
        t0 = time.perf_counter()
        mem_before = sysinfo.memory()
        parts, metrics, first = [], {}, None
        async for chunk in state["provider"].chat_stream(model or s["model"], messages, base_options(s, **opt_over), s["timeout_s"]):
            if chunk.content:
                first = first or round((time.perf_counter() - t0) * 1000)
                parts.append(chunk.content)
            if chunk.done:
                metrics = chunk.metrics
        mem_after = sysinfo.memory()
        metrics = {**metrics, "first_token_ms": first, "wall_ms": round((time.perf_counter() - t0) * 1000),
                   "available_mb_before": mem_before["available_mb"], "available_mb_after": mem_after["available_mb"]}
        return "".join(parts), metrics

    ctx = SimpleNamespace(db=db, state=state, gen_lock=gen_lock, generate=generate, collect=collect,
                          base_options=base_options)
    register_workspace(app, ctx)
    register_patches(app, ctx)
    register_benchmark(app, ctx)

    @app.get("/api/defaults")
    async def defaults():
        return DEFAULT_SETTINGS

    # Serve the built frontend (frontend/dist) so a single process runs the whole app.
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


app = create_app()
