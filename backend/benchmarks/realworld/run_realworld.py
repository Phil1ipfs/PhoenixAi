"""Real-world coding benchmark for PHOENIX Code: a larger multi-file Python project, real tests as the judge.

Stages (each can be re-run; state lives in ./results):
  analyze  read-only: comprehension questions on the pristine copy, then for every planted bug (a) bug scan on the file,
           (b) bug localisation from the real failing-test output, (c) patch PROPOSAL (nothing applied), + control scans.
  apply    applies ONLY the patches listed as approved in results/approvals.json (a human/reviewer decision), runs the
           full real pytest suite, rolls the patch back, and writes the final report.

Everything happens against a disposable copy in %TEMP%; the original repository is never modified.
PHOENIX itself runs as a normal backend process (own port + own temp database) so the product's real code path is tested.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ORIGINAL, PY, make_copy, run_tests, scrubbed_env  # noqa: E402
from mutants import MUTANTS  # noqa: E402

sys.path.insert(0, str(ORIGINAL))
from app import sysinfo  # noqa: E402

RESULTS = HERE / "results"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

COMPREHENSION = [
    {"id": "c1_needs_verification", "q": "Which function decides whether a question needs live web verification, and what does it return?",
     "files": ["app/research.py"], "expect": [["needs_verification"], ["needed", "reasons"]]},
    {"id": "c2_path_traversal", "q": "How does the Workspace class stop a path from escaping the project folder?",
     "files": ["app/workspace.py"], "expect": [["resolve"], ["..", "relative_to", "is_relative_to", "escape"]]},
    {"id": "c3_sensitive", "q": "Which kinds of files does the workspace refuse to read because they may hold secrets?",
     "files": ["app/workspace.py"], "expect": [[".env", "id_rsa", ".pem", ".key"], ["sensitive", "secret", "credential"]]},
    {"id": "c4_patch_validation", "q": "How does the patch generator validate a proposed Python edit before showing it to the user?",
     "files": ["app/patchgen.py"], "expect": [["syntax", "ast.parse"], ["definition", "changed", "lines", "symbol"]]},
    {"id": "c5_schema_messages", "q": "Which database table stores chat messages, and which column links a message to its conversation?",
     "files": ["app/db.py"], "expect": [["messages"], ["conversation_id"]]},
    {"id": "c6_memory_block", "q": "What does assess_memory decide when available RAM is very low and the model is not loaded?",
     "files": ["app/sysinfo.py"], "expect": [["block"], ["ram", "memory", "free"]]},
    {"id": "c7_max_changed", "q": "What is the maximum number of changed lines MAX_CHANGED_LINES allows in one proposed edit?",
     "files": ["app/patchgen.py"], "expect": [["120"]]},
    {"id": "c8_history", "q": "How does build_messages decide which history messages to keep when the prompt is too long?",
     "files": ["app/chat.py"], "expect": [["newest", "latest", "recent", "last", "reversed", "end"], ["max_chars", "budget", "characters", "limit"]]},
    {"id": "c9_user_agent", "q": "What User-Agent do web requests use, and how can the operator add a contact address?",
     "files": ["app/research.py"], "expect": [["phoenixai"], ["contact"]]},
    {"id": "c10_splice", "q": "Where is Python symbol splicing implemented, and what does it do with new import lines?",
     "files": ["app/patchgen.py"], "expect": [["splice_python"], ["import"]]},
]
CONTROL_FILES = ["app/chat.py", "app/sysinfo.py", "app/patches.py"]


# ------------------------------------------------------------------ measurement
class Sampler(threading.Thread):
    """Samples system free RAM and the Ollama runner's working set every 2 s."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: list[tuple[float, int | None, int]] = []
        self._stop_ev = threading.Event()

    @staticmethod
    def runner_mb() -> int:
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
        except Exception:  # noqa: BLE001
            return 0
        total = 0
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 5 and re.search(r"ollama|llama", parts[0], re.I):
                total += int(re.sub(r"[^\d]", "", parts[4]) or 0) // 1024
        return total

    def run(self):
        while not self._stop_ev.is_set():
            self.samples.append((time.time(), sysinfo.memory()["available_mb"], self.runner_mb()))
            self._stop_ev.wait(2.0)

    def stop(self):
        self._stop_ev.set()

    def window(self, t0: float, t1: float) -> dict:
        w = [s for s in self.samples if t0 - 2 <= s[0] <= t1 + 2]
        avail = [s[1] for s in w if s[1] is not None]
        return {"min_free_mb": min(avail) if avail else None, "peak_runner_mb": max((s[2] for s in w), default=None)}


class Client:
    def __init__(self, sampler: Sampler):
        self.http = httpx.Client(base_url=BASE, timeout=httpx.Timeout(900.0, connect=10.0))
        self.sampler = sampler

    def timed(self, fn):
        t0 = time.time()
        res = fn()
        t1 = time.time()
        return res, {"wall_s": round(t1 - t0, 1), **self.sampler.window(t0, t1)}

    def ask(self, pid: str, mode: str, question: str, file: str | None = None) -> dict:
        def go():
            body = {"mode": mode, "question": question, **({"file": file} if file else {})}
            text, done, err = [], None, None
            with self.http.stream("POST", f"/api/projects/{pid}/ask", json=body) as r:
                if r.status_code != 200:
                    return {"error": f"HTTP {r.status_code}: {r.read().decode()[:300]}", "text": ""}
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev["type"] == "token":
                        text.append(ev["content"])
                    elif ev["type"] == "done":
                        done = ev["message"]
                    elif ev["type"] == "error":
                        err = f"{ev.get('kind')}: {ev.get('message')}"
            return {"text": "".join(text), "metrics": (done or {}).get("metrics") or {}, "error": err}
        res, m = self.timed(go)
        met = res.get("metrics", {})
        ftm = met.get("first_token_ms")
        res["perf"] = {**m, "tokens_per_sec": met.get("tokens_per_sec"), "prompt_tokens": met.get("prompt_tokens"),
                       "completion_tokens": met.get("completion_tokens"),
                       "prompt_tps": round(met["prompt_tokens"] / (ftm / 1000), 1) if ftm and met.get("prompt_tokens") else None,
                       "first_token_s": round(ftm / 1000, 1) if ftm else None}
        return res


# ------------------------------------------------------------------ code helpers
def spans_of(src: str, func: str) -> dict:
    """Line spans of `func` (any nesting) and of the top-level symbol that encloses it."""
    tree = ast.parse(src)
    for top in tree.body:
        for node in ast.walk(top):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
                return {"func": (node.lineno, node.end_lineno), "top": (top.lineno, top.end_lineno), "nested": node is not top}
    raise KeyError(func)


def mutate(root: Path, m: dict) -> tuple[str, int]:
    p = root / m["file"]
    src = p.read_text(encoding="utf-8")
    assert src.count(m["old"]) == 1, f"{m['id']}: anchor not unique"
    bug_line = src[:src.index(m["old"])].count("\n") + 1
    p.write_text(src.replace(m["old"], m["new"]), encoding="utf-8", newline="")
    return src, bug_line


def line_mentions(text: str) -> set[int]:
    nums: set[int] = set()
    for m in re.finditer(r"\blines?\s*#?(\d+)(?:\s*(?:-|–|to|and)\s*(\d+))?", text, re.I):
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        nums.update(range(a, min(b, a + 60) + 1))
    for m in re.finditer(r"\.py:(\d+)", text):
        nums.add(int(m.group(1)))
    return nums


def grade_bug(resp: dict, m: dict, bug_line: int, span: tuple[int, int]) -> dict:
    text = resp.get("text", "")
    srcs = (resp.get("metrics") or {}).get("sources") or []
    in_ctx = any(s["path"] == m["file"] and s["start"] <= bug_line <= s["end"] for s in srcs)
    named = bool(re.search(rf"\b{re.escape(m['func'])}\b", text))
    lines_ok = any(span[0] - 1 <= n <= span[1] + 1 for n in line_mentions(text))
    outcome = ("found" if (named or lines_ok) and in_ctx else
               "named_but_not_in_context" if (named or lines_ok) else
               "missed_in_context" if in_ctx else "not_in_context")
    return {"outcome": outcome, "in_context": in_ctx, "named_function": named, "line_in_span": lines_ok,
            "sources": [f"{s['path']}:{s['start']}-{s['end']}" for s in srcs]}


def pytest_failure_summary(out: str) -> str:
    es = [l.strip() for l in out.splitlines() if l.startswith("E ")][:3]
    fails = re.findall(r"^FAILED (\S+)", out, re.M)[:2]
    return "pytest failed: " + ", ".join(fails) + (" | " + " / ".join(es) if es else "")


# ------------------------------------------------------------------ backend lifecycle
def start_backend(data_dir: Path):
    env = scrubbed_env()
    env["PHOENIX_DATA_DIR"] = str(data_dir)
    proc = subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                            cwd=ORIGINAL, env=env, stdout=open(RESULTS / "backend.log", "ab"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if httpx.get(BASE + "/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("backend did not start")


def stop_backend(proc):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ------------------------------------------------------------------ stage 1
def analyze(args):
    RESULTS.mkdir(exist_ok=True)
    root = make_copy()
    data_dir = Path(tempfile.mkdtemp(prefix="phoenix-realworld-data-"))
    state = {"root": str(root), "data_dir": str(data_dir), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    (RESULTS / "state.json").write_text(json.dumps(state, indent=1))
    print("disposable copy:", root, flush=True)

    base = run_tests(root)
    state["baseline_tests"] = {k: base[k] for k in ("ok", "passed", "failed", "seconds")}
    py_files = [p for p in (root / "app").glob("*.py")]
    state["project"] = {"python_files": len(py_files), "app_lines": sum(len(p.read_text(encoding="utf-8").splitlines()) for p in py_files),
                        "test_files": len(list((root / "tests").glob("test_*.py")))}
    print("baseline:", state["baseline_tests"], state["project"], flush=True)
    assert base["ok"], base["output"]

    proc = start_backend(data_dir)
    sampler = Sampler()
    sampler.start()
    try:
        c = Client(sampler)
        c.http.put("/api/settings", json={"timeout_s": 300, "temperature": 0.1}).raise_for_status()
        settings = c.http.get("/api/settings").json()
        state["settings"] = {k: settings[k] for k in ("model", "num_ctx", "workspace_num_ctx", "workspace_context_chars", "temperature", "timeout_s", "keep_alive")}
        pid = c.http.post("/api/projects", json={"path": str(root), "name": "realworld-copy"}).json()["id"]
        # security spot-checks through the product API: the copy has no secrets and traversal is refused
        state["security_checks"] = {
            "traversal_blocked": c.http.get(f"/api/projects/{pid}/file", params={"path": "../../etc/passwd"}).status_code == 403,
            "env_blocked": c.http.get(f"/api/projects/{pid}/file", params={"path": ".env"}).status_code == 403,
            "listing_has_no_secret_names": not any(re.search(r"\.env|\.pem|\.key|\.db$", e["name"]) for e in c.http.get(f"/api/projects/{pid}/tree").json()),
        }
        results = {"comprehension": [], "controls": [], "bugs": []}

        # ---- A. comprehension on the pristine copy
        for t in COMPREHENSION:
            r = c.ask(pid, "ask", t["q"])
            low = r["text"].lower()
            kw_ok = all(any(k in low for k in g) for g in t["expect"])
            srcs = (r.get("metrics") or {}).get("sources") or []
            row = {"id": t["id"], "question": t["q"], "keyword_pass": kw_ok, "retrieved_right_file": any(s["path"] in t["files"] for s in srcs),
                   "sources": [f"{s['path']}:{s['start']}-{s['end']}" for s in srcs], "answer": r["text"][:1200], "error": r.get("error"), "perf": r["perf"]}
            results["comprehension"].append(row)
            print("comp", t["id"], kw_ok, row["retrieved_right_file"], r["perf"]["wall_s"], "s", flush=True)
            (RESULTS / "stage1.partial.json").write_text(json.dumps(results, indent=1))

        # ---- control scans (no bug planted)
        for f in CONTROL_FILES:
            r = c.ask(pid, "bugs", "", f)
            low = r["text"].lower()
            claims_none = bool(re.search(r"\b(no|not find any|did not find|didn't find)\b.{0,40}\b(bugs?|problems?|issues?)\b", low))
            items = len(re.findall(r"^\s*(?:\d+[.)]|[-*])\s+", r["text"], re.M))
            results["controls"].append({"file": f, "claims_no_bug": claims_none, "listed_items": items, "answer": r["text"][:1500], "perf": r["perf"], "error": r.get("error")})
            print("control", f, "claims_none=", claims_none, "items=", items, r["perf"]["wall_s"], "s", flush=True)

        # ---- B. planted bugs
        for m in MUTANTS:
            if args.only and m["id"].split("_")[0] not in args.only:
                continue
            original, bug_line = mutate(root, m)
            row = {"id": m["id"], "file": m["file"], "func": m["func"], "bug_line": bug_line, "symptom": m["symptom"]}
            try:
                sp = spans_of((root / m["file"]).read_text(encoding="utf-8"), m["func"])
                row["func_span"], row["top_span"], row["nested_in_class"] = sp["func"], sp["top"], sp["nested"]
                t = run_tests(root)
                row["tests_detect_bug"] = not t["ok"]
                row["failed_tests"] = t["failed_ids"][:3]
                failure = pytest_failure_summary(t["output"])
                # (a) scan: "find bugs in this file", no hint
                r = c.ask(pid, "bugs", "", m["file"])
                row["scan"] = {**grade_bug(r, m, bug_line, sp["func"]), "answer": r["text"][:1500], "perf": r["perf"], "error": r.get("error"),
                               "listed_items": len(re.findall(r"^\s*(?:\d+[.)]|[-*])\s+", r["text"], re.M))}
                print(m["id"], "scan:", row["scan"]["outcome"], r["perf"]["wall_s"], "s", flush=True)
                if t["ok"]:   # real tests cannot judge a fix here, so no localisation-from-failure / patch task for this bug
                    row["skipped"] = "real tests do not detect this bug (scan scored against the planted line only)"
                    results["bugs"].append(row)
                    continue
                # (b) localisation from the real failing-test output, no file hint
                r = c.ask(pid, "ask", f"The project's tests fail after a change. {failure}\nWhich function in the project contains the bug, and what is wrong with it?")
                row["locate"] = {**grade_bug(r, m, bug_line, sp["func"]), "answer": r["text"][:1500], "perf": r["perf"], "error": r.get("error"),
                                 "question_used": failure[:300]}
                print(m["id"], "locate:", row["locate"]["outcome"], r["perf"]["wall_s"], "s", flush=True)
                # (c) patch proposal (pending; nothing applied). The user names the failing test + function, as a traceback would.
                instr = f"Fix the bug in `{m['func']}`. {failure}"[:600]
                src_now = (root / m["file"]).read_text(encoding="utf-8")
                body = {"path": m["file"], "instruction": instr}
                resp, perf = c.timed(lambda: c.http.post(f"/api/projects/{pid}/patches/generate", json=body))
                row["patch"] = {"range_used": None}
                if resp.status_code == 413:   # file section too big for the model's context -> select a line range like the UI would
                    top_text = "\n".join(src_now.splitlines()[sp["top"][0] - 1:sp["top"][1]])
                    lo, hi = sp["top"] if len(top_text) <= 5500 else sp["func"]
                    body.update(start=lo, end=hi)
                    resp, perf2 = c.timed(lambda: c.http.post(f"/api/projects/{pid}/patches/generate", json=body))
                    perf = {**perf2, "wall_s": round(perf["wall_s"] + perf2["wall_s"], 1)}
                    row["patch"]["range_used"] = [lo, hi]
                    row["patch"]["first_attempt_status"] = 413
                row["patch"]["http_status"] = resp.status_code
                row["patch"]["perf"] = perf
                data = resp.json()
                if resp.status_code == 201:
                    pm = (data.get("meta") or {})
                    row["patch"].update(patch_id=data["id"], strategy=pm.get("strategy"), attempts=pm.get("attempts"),
                                        checks=[x["name"] for x in pm.get("checks", []) if x.get("ok")], unified=data["unified"],
                                        tokens_per_sec=(pm.get("metrics") or {}).get("tokens_per_sec"),
                                        completion_tokens=(pm.get("metrics") or {}).get("completion_tokens"))
                else:
                    d = data.get("detail", data)
                    row["patch"]["failure"] = (d.get("message") if isinstance(d, dict) else str(d))[:300]
                    row["patch"]["attempt_log"] = d.get("attempts") if isinstance(d, dict) else None
                print(m["id"], "patch:", resp.status_code, row["patch"].get("failure", "proposed"), perf["wall_s"], "s", flush=True)
            finally:
                (root / m["file"]).write_text(original, encoding="utf-8", newline="")
            results["bugs"].append(row)
            (RESULTS / "stage1.partial.json").write_text(json.dumps({**results}, indent=1))
        state["pid"] = pid
        state["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state["ram_overall"] = {"min_free_mb": min((s[1] for s in sampler.samples if s[1] is not None), default=None),
                                "peak_runner_mb": max((s[2] for s in sampler.samples), default=None),
                                "total_ram_mb": sysinfo.memory()["total_mb"]}
        (RESULTS / "state.json").write_text(json.dumps(state, indent=1))
        (RESULTS / "stage1.json").write_text(json.dumps(results, indent=1))
        # human-readable diffs for the approval review
        with open(RESULTS / "review.md", "w", encoding="utf-8") as f:
            for b in results["bugs"]:
                p = b.get("patch") or {}
                f.write(f"## {b['id']} ({b['file']} :: {b['func']}, bug at line {b['bug_line']})\n")
                f.write(f"planted: {next(x for x in MUTANTS if x['id'] == b['id'])['old']!r} -> {next(x for x in MUTANTS if x['id'] == b['id'])['new']!r}\n\n")
                f.write("```diff\n" + (p.get("unified") or f"(no proposal: {p.get('failure') or b.get('skipped')})") + "\n```\n\n")
        print("STAGE1 DONE", flush=True)
    finally:
        sampler.stop()
        stop_backend(proc)


# ------------------------------------------------------------------ stage 2
def apply_stage(args):
    state = json.loads((RESULTS / "state.json").read_text())
    stage1 = json.loads((RESULTS / "stage1.json").read_text())
    approvals = json.loads((RESULTS / "approvals.json").read_text())
    root = Path(state["root"])
    proc = start_backend(Path(state["data_dir"]))
    sampler = Sampler()
    sampler.start()
    out = []
    try:
        c = Client(sampler)
        for b in stage1["bugs"]:
            p = b.get("patch") or {}
            m = next(x for x in MUTANTS if x["id"] == b["id"])
            row = {"id": b["id"], "proposed": bool(p.get("patch_id")), "decision": approvals.get(b["id"], {}).get("decision", "none")}
            if not p.get("patch_id"):
                row["result"] = "no proposal"
                out.append(row)
                continue
            row["reviewer_note"] = approvals.get(b["id"], {}).get("note", "")
            if row["decision"] != "approve":
                c.http.post(f"/api/patches/{p['patch_id']}/reject")
                row["result"] = "rejected by reviewer (not applied)"
                out.append(row)
                continue
            original, _ = mutate(root, m)                       # restore the buggy state the proposal was made against
            try:
                r = c.http.post(f"/api/patches/{p['patch_id']}/apply")
                row["apply_status"] = r.status_code
                if r.status_code != 200:
                    row["result"] = "apply failed: " + r.text[:200]
                else:
                    full = run_tests(root)
                    target = run_tests(root, *b["failed_tests"]) if b.get("failed_tests") else None
                    row["full_suite"] = {k: full[k] for k in ("ok", "passed", "failed", "failed_ids")}
                    row["target_tests_pass"] = target["ok"] if target else None
                    row["result"] = "CORRECT (real tests pass)" if full["ok"] else "INCORRECT (real tests still fail)"
                    if not full["ok"]:
                        row["remaining_failures"] = full["failed_ids"][:5]
                    rb = c.http.post(f"/api/patches/{p['patch_id']}/rollback")
                    row["rollback_ok"] = rb.status_code == 200 and (root / m["file"]).read_text(encoding="utf-8") == original.replace(m["old"], m["new"])
            finally:
                (root / m["file"]).write_text(original, encoding="utf-8", newline="")
            print(row["id"], row["result"], flush=True)
            out.append(row)
        final = run_tests(root)
        (RESULTS / "stage2.json").write_text(json.dumps({"apply": out, "pristine_suite_after_all": {k: final[k] for k in ("ok", "passed", "failed")}}, indent=1))
        print("STAGE2 DONE; pristine suite after everything:", final["passed"], "passed", final["failed"], "failed", flush=True)
    finally:
        sampler.stop()
        stop_backend(proc)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["analyze", "apply"])
    ap.add_argument("--only", nargs="*", help="mutant ids like M2 M3 (default: all valid)")
    a = ap.parse_args()
    analyze(a) if a.stage == "analyze" else apply_stage(a)
