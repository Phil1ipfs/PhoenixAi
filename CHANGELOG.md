# Changelog

## 1.1.0 (2026-10-15) - PHOENIX Code
- Real-browser (Edge/Playwright) test suites for chat, Code view, benchmark.
- Fixes found by those tests: Stop-then-reload race (partial reply now appears reliably), settings dropdown flashing "(not installed)", untagged code blocks not highlighted (auto-detect), missing favicon, stale benchmark results shown mid-run.
- PHOENIX Code: authorized project folders, safe file tree/read/search/filter, explain, bug analysis, bounded context retrieval.
- Controlled editing: AI patch proposals, side-by-side diff, approve/reject, atomic apply, conflict detection, history, rollback.
- Benchmark suite (codegen, bugfix, comprehension, patch/tool use) with executable tests, speed/latency/RAM metrics, per-model history.
- New settings: `workspace_num_ctx`, `workspace_context_chars` (existing databases upgrade automatically).
- Backend tests: 8 -> 55 (+1 skipped).

## 1.0.0-alpha (2026-10-15)
- Environment verified; Python 3.12 and qwen2.5-coder:3b installed.
- FastAPI backend, provider abstraction (Ollama), SQLite persistence.
- React/Tailwind chat UI: streaming, markdown, highlighting, copy code, settings, model select, status, stop.
