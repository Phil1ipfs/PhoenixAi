"""Benchmark endpoints: explicit, user-started, streamed progress, results persisted."""
from __future__ import annotations

import asyncio
import json
import statistics
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import benchmark as bm
from . import sysinfo
from .db import now
from .patchgen import PatchGenError, propose_edit
from .providers import ProviderError
from .workspace import Workspace
from .workspace_routes import WORKSPACE_RULES


class RunBody(BaseModel):
    categories: list[str] = ["codegen", "bugfix", "comprehension", "tool", "tool_heldout"]
    strategy: str = "auto"         # patch strategy for tool tasks: legacy | search_replace | symbol | auto
    max_retries: int = 2           # bounded validation-feedback retries (ignored by "legacy")
    confirm_execute: bool = False  # must be true: tests EXECUTE model-written Python in a subprocess
    limit: int | None = None       # max tasks per category (quick runs)


def nd(o: dict) -> str:
    return json.dumps(o, ensure_ascii=False) + "\n"


def register_benchmark(app: FastAPI, ctx) -> None:
    db = ctx.db

    @app.get("/api/benchmark/tasks")
    async def tasks():
        return bm.catalog()

    @app.get("/api/benchmark/history")
    async def history():
        rows = db.conn.execute("SELECT * FROM benchmarks ORDER BY id DESC LIMIT 20").fetchall()
        return [{"id": r["id"], "model": r["model"], "created_at": r["created_at"], "results": json.loads(r["results"])} for r in rows]

    @app.post("/api/benchmark/run")
    async def run(body: RunBody):
        if not body.confirm_execute:
            raise HTTPException(400, "Benchmarks execute model-written Python in a subprocess; set confirm_execute=true to proceed")
        if ctx.gen_lock.locked():
            raise HTTPException(409, "Another generation is already running")
        s = db.get_settings()
        model = s["model"]

        async def stream():
            async with ctx.gen_lock:
                results: list[dict] = []
                provider = ctx.state["provider"]

                async def gen(messages, **o):
                    return await ctx.collect(messages, model, temperature=0, num_predict=o.pop("num_predict", 600),
                                             num_ctx=max(s["num_ctx"], s["workspace_num_ctx"]), **o)

                def pick(items, cat):
                    return items[: body.limit] if body.limit else items

                plan: list[tuple[str, dict]] = []
                for cat, items in (("codegen", bm.CODEGEN), ("bugfix", bm.BUGFIX), ("comprehension", bm.COMPREHENSION), ("tool", bm.TOOL_TASKS), ("tool_heldout", bm.TOOL_HELDOUT)):
                    if cat in body.categories:
                        plan += [(cat, t) for t in pick(items, cat)]
                yield nd({"type": "start", "model": model, "total": len(plan)})
                for cat, t in plan:
                    yield nd({"type": "task_start", "id": t["id"], "category": cat})
                    r = {"id": t["id"], "category": cat, "passed": False, "detail": "", "tool_ok": None}
                    try:
                        if cat == "codegen":
                            out, m = await gen([{"role": "system", "content": bm.CODEGEN_RULES}, {"role": "user", "content": t["prompt"]}])
                            r["passed"], r["detail"] = await asyncio.to_thread(bm.run_python_tests, bm.extract_code(out), t["tests"])
                        elif cat == "bugfix":
                            out, m = await gen([{"role": "system", "content": bm.BUGFIX_RULES},
                                                {"role": "user", "content": f"{t['desc']}\n\n```python\n{t['code']}```"}])
                            r["passed"], r["detail"] = await asyncio.to_thread(bm.run_python_tests, bm.extract_code(out), t["tests"])
                        elif cat == "comprehension":
                            ws = Workspace(bm.SAMPLE_PROJECT)
                            c = ws.retrieve_context(t["question"], s["workspace_context_chars"])
                            prompt = (f"Answer using only the project context below.\n\nQuestion: {t['question']}\n\nProject layout:\n{c['tree']}\n\n"
                                      "<<<UNTRUSTED PROJECT EXCERPTS\n" + "\n\n".join(c["blocks"]) + "\nUNTRUSTED PROJECT EXCERPTS>>>")
                            out, m = await gen([{"role": "system", "content": s["system_prompt"] + WORKSPACE_RULES}, {"role": "user", "content": prompt}], num_predict=300)
                            r["passed"] = bm.grade_keywords(out, t["expect"])
                            r["detail"] = "keyword match (heuristic): " + ("all expected facts present" if r["passed"] else "missing expected facts")
                            r["sources"] = [x["path"] for x in c["sources"]]
                        else:  # patch/tool reliability: the real product pipeline, then execute tests on the result
                            r["strategy"] = body.strategy
                            t_start = time.perf_counter()

                            async def gen_c(messages, **o):
                                return await gen(messages)

                            try:
                                prop = await propose_edit(gen_c, t["name"], t["file"], t["instruction"], strategy=body.strategy,
                                                          max_retries=body.max_retries)
                                r["tool_ok"], r["attempts"] = True, prop.metrics["attempts"]
                                r["passed"], r["detail"] = await asyncio.to_thread(bm.run_python_tests, prop.text, t["tests"])
                                out, m = prop.text, {**prop.metrics, "wall_ms": round((time.perf_counter() - t_start) * 1000),
                                                     "first_token_ms": prop.metrics.get("first_token_ms")}
                            except PatchGenError as e:
                                r["tool_ok"], r["attempts"], r["detail"] = False, len(e.attempts), e.message
                                out, m = e.last_output, {"wall_ms": round((time.perf_counter() - t_start) * 1000), "first_token_ms": None,
                                                         "available_mb_after": sysinfo.memory()["available_mb"]}
                        r.update(latency_ms=m["wall_ms"], first_token_ms=m["first_token_ms"], tokens_per_sec=m.get("tokens_per_sec"),
                                 completion_tokens=m.get("completion_tokens"), available_mb_after=m["available_mb_after"],
                                 output_excerpt=out[:400])
                    except ProviderError as e:
                        r["detail"] = f"{e.kind}: {e.message}"
                        results.append(r)
                        yield nd({"type": "task_done", "result": r})
                        yield nd({"type": "error", "kind": e.kind, "message": e.message})
                        break
                    except Exception as e:  # noqa: BLE001 - keep the run alive, record the failure honestly
                        r["detail"] = f"harness error: {type(e).__name__}: {e}"
                    results.append(r)
                    yield nd({"type": "task_done", "result": r})

                summary = summarize(model, s, results, await provider.loaded_models())
                if results:
                    db.add_benchmark(model, summary)
                yield nd({"type": "summary", "summary": summary})

        from fastapi.responses import StreamingResponse
        return StreamingResponse(stream(), media_type="application/x-ndjson")


def summarize(model: str, s: dict, results: list[dict], loaded: list[dict]) -> dict:
    cats: dict[str, dict] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += 1 if r["passed"] else 0
    tool = [r["tool_ok"] for r in results if r["tool_ok"] is not None]
    tps = [r["tokens_per_sec"] for r in results if r.get("tokens_per_sec")]
    lat = [r["latency_ms"] for r in results if r.get("latency_ms")]
    mem = [r["available_mb_after"] for r in results if r.get("available_mb_after") is not None]
    mine = next((m for m in loaded if m["name"] == model), None)
    tool_rows = [r for r in results if r["category"] in ("tool", "tool_heldout")]
    strategy = tool_rows[0].get("strategy") if tool_rows else None
    return {"model": model, "tool_strategy": strategy, "finished_at": now(), "num_ctx": max(s["num_ctx"], s["workspace_num_ctx"]), "categories": cats,
            "tool_format_success": {"ok": sum(1 for t in tool if t), "total": len(tool)},
            "avg_tokens_per_sec": round(statistics.mean(tps), 2) if tps else None,
            "avg_latency_s": round(statistics.mean(lat) / 1000, 1) if lat else None,
            "max_latency_s": round(max(lat) / 1000, 1) if lat else None,
            "min_available_ram_mb": min(mem) if mem else None, "model_ram_mb": round(mine["size"] / 2**20) if mine else None,
            "total_ram_mb": sysinfo.memory()["total_mb"], "results": results}
