# PHOENIX AI task board (Lead audit, 2026-10-16)

Audit basis: README/ARCHITECTURE/STATUS/BENCHMARKS/CHANGELOG, `backend/app/main.py` read in full, file/commit state of the working tree.
Constraint: ~12 GB RAM, CPU-only, free/local only. **Several other Claude sessions are editing this tree right now** (UI redesign in `frontend/src`, document Q&A in `backend/app/phoenix_files/`, `backend/app/verify.py`, a real-world benchmark in `backend/benchmarks/realworld/` that holds the single model lock). Rule: one owner per file; the Lead does not edit files another session is actively writing.

## Done by Lead (verified)
| ID | Area | What | Evidence |
|---|---|---|---|
| L1 | Security | `ollama_url` locality check used `startswith`, so `http://127.0.0.1.evil.com` and `http://localhost@evil.com` were accepted (breaks the "no cloud, localhost only" guarantee). Now parsed with `urlsplit`; host must be exactly 127.0.0.1 / localhost / ::1. | `main.py` `is_local_url`, new test `test_ollama_url_rejects_lookalike_hosts`; backend suite **131 passed, 1 skipped** |

| L2 | Security | S1: Host-header and cross-site Origin checks (pure ASGI middleware, so streaming/Stop is untouched). Upload review (S2) found: cross-site text/plain upload now blocked; DOCX DOCTYPE/ENTITY scan only covers the first 2 KB/20 KB; PDF child has a time limit but no memory limit. | `test_rejects_foreign_host_and_cross_site_writes` |
| L3 | Backend | B1: a send refused by the memory guard no longer leaves an unanswered user message (`db.delete_message`; error event carries `discarded: true`). Concurrent sends: a short-lived claim makes the second send return 409 instead of queueing and storing a duplicate user turn. | `test_blocked_send_leaves_no_orphan_user_message`, `test_concurrent_sends_one_accepted_one_409`; backend 255 passed, 5 failed (pre-existing in the document tests, fail without these changes too), 1 skipped |

## Backlog (priority = impact vs effort/risk)
| P | ID | Role | Objective | Files | Acceptance | Depends on |
|---|---|---|---|---|---|---|
| 1 | S1 | Security | Reject requests whose `Host` header is not localhost/127.0.0.1 (DNS-rebinding) and require `application/json` on mutating routes (cross-site form/text POST). CORS alone does not stop a web page from *sending* requests. | `backend/app/main.py` | Test: foreign `Host` -> 400/403; tests using `testserver` host still pass; UI + e2e still work | Coordinate with owner of `main.py` edits |
| 1 | S2 | Security | Review the new document upload/extract path (PDF worker, size/page limits, filename and path handling, prompt-injection wrapping of extracted text) before it ships. | `backend/app/phoenix_files/*`, `verify.py` | Written review; tests for oversized/corrupt/zip-bomb-like PDFs, path traversal in names | Document-Q&A session finishes |
| 1 | Q1 | QA | Re-run the real-model suites (`ui`, `code`, `bench`) after the Ember UI redesign; changelog says they were not re-run. | `e2e/*.test.mjs` | 18/18, 20/20, 3/3 (or documented changes); screenshots refreshed | UI session done; **model free** (realworld benchmark must finish first, one generation at a time) |
| 2 | B1 | Backend | `send_message` stores the user message before the memory guard can block, so a blocked send leaves an orphan user turn; lock check and add are not atomic. | `main.py` | Blocked send leaves history unchanged; concurrent sends -> exactly one 200, one 409 | none |
| 2 | D1 | DevOps | `start-phoenix.bat` has never been double-clicked (STATUS.md). Verify cold start, port-in-use message, Ollama-not-running message; pin `requirements.txt` upper bounds for `pypdf` etc. and add a lockfile. | `start-phoenix.bat`, `backend/requirements.txt` | Manual cold-start log; clear errors in both failure cases | none |
| 2 | F1 | Frontend | Regenerate button (needs backend endpoint that replaces the last assistant turn instead of duplicating the user turn). | `frontend/src/MessageView.tsx`, new route in `main.py` | Regenerate keeps one user turn; Stop still works | B1 (shared route code) |
| 3 | A1 | AI/LLM | Context management: summarise old turns instead of truncating by chars; measure prompt-processing cost (~28 tok/s is the bottleneck). | `chat.py` | Benchmark shows no regression; long chats keep early facts | measurable on a free model |
| 3 | A2 | AI/LLM | Try a 7B model once (never tested). Keep only if speed/RAM acceptable. Needs user approval to download several GB. | settings only | Benchmark table in BENCHMARKS.md | **user approval** |
| 3 | C1 | Coding agent | "symbol" patch strategy is Python-only and cannot delete code; extend to JS/TS or add deletion. | `patchgen.py` | Held-out tasks pass; no regression on 11/11 | none |

## Status of other sessions (observed, not controlled by Lead)
- UI redesign ("Ember"): modified/added `App.tsx`, `Sidebar.tsx`, `Composer.tsx`, `MessageView.tsx`, `ui.tsx`, `theme.ts`, `Icons.tsx`, `index.css`. Last write 01:26. CHANGELOG says `tsc`/`vite build` clean and `fake.test.mjs` 26/26. Lead has **not** re-run these.
- Document Q&A + answer verification: `backend/app/phoenix_files/`, `verify.py`, `db.py`/`main.py` changes. Last write 01:34. Unreviewed.
- Real-world benchmark: `backend/benchmarks/realworld/`, stage 1 partial results written 01:32; occupying the model.
- Uncommitted: all of the above is local only; GitHub has just the initial commit.

## Known limits (unchanged)
3B model quality, 5-8 tok/s, tight RAM, one generation at a time, no auth, Windows-only testing. See STATUS.md.
