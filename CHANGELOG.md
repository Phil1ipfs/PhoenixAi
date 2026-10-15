# Changelog

## Unreleased - "Ember" UI redesign (frontend only; backend and APIs untouched)
- Design system: tokens in `index.css` (charcoal/midnight dark, warm light), accents Ember/Electric/Violet, glass surfaces, shared `ui.tsx` primitives (IconButton, Banner, Sheet, button/input classes), `Icons.tsx`, persisted appearance store in `theme.ts`.
- Components split out of `App.tsx`: `Sidebar` (search, date groups, inline delete confirm, collapsible on desktop), `Composer`, `MessageView`, `EmptyState`.
- Motion (CSS only, transform/opacity, all off under `prefers-reduced-motion`): flickering logo (faster while generating), glowing composer while busy, model-chip pop on switch, sheet/backdrop enter+exit, staggered suggestion cards, press/lift feedback, skeleton loaders, optional ambient background (toggle in Settings > Appearance).
- Settings: new Appearance section (theme, accent, ambient); local-model indicator; skeleton loading.
- Fixed: build break (`useTheme` vs `useAppearance`), duplicate sidebar toggle on mobile.
- Verified: `tsc`/`vite build` clean; `e2e/fake.test.mjs` 26/26; manual screenshots dark/light/mobile, no horizontal overflow, Esc closes sheet.
- Not done / limits: HyperFrames' `compose`/`render_video` are disabled from Claude Code and produce video, not UI, so motion is plain CSS/SVG. No regenerate button (needs a backend endpoint; resending would duplicate the user message). Real-model suites (`ui/code/bench`) not re-run. No cloud-model indicator since only local Ollama is supported.

## 1.2.0 (2026-10-16) - Research, reliable edits, memory management, UI polish
**Internet research (optional, default off)**
- `web_mode` off/auto/always; deterministic time-sensitivity detector; DuckDuckGo + Wikipedia + Open-Meteo (free, no keys); robots.txt, SSRF and size guards.
- Numbered sources with clickable links, per-source "page read / snippet only", citation verification (fabricated `[n]` removed, unsupported claims flagged), deterministic unverified/offline/disabled banners.
- Found with the real network (mocks could not): DuckDuckGo rate-limits User-Agents containing "robots"; gzip bodies were decoded twice; Wikipedia requires contact info (new optional `web_contact` setting; without it Wikipedia pages fall back to snippets).

**Coding-agent reliability**
- New default strategy for Python: model returns complete functions/classes, applied by AST splicing; deterministic validation (syntax, nothing lost, requested symbol changed, focused size, diff round-trip); bounded retries with exact error feedback; SEARCH/REPLACE kept for other file types with a "closest real line" hint.
- Benchmark: +8 held-out patch tasks, `strategy` option, benchmark-validity tests. Same 11 patch tasks, same model: legacy 7/11 (twice) -> symbol 11/11 (three runs).
- Patch records store strategy/attempts/validation checks; UI shows them.

**Memory / CPU**
- Pre-flight memory guard (warn + smaller context, or block with `low_memory`), `keep_alive` and `num_thread` settings, "Unload model now", health shows which model is in RAM, health polling pauses in hidden tabs, lazy-loaded Code/Benchmark chunks.
- Measured and documented (BENCHMARKS.md): per-component RAM, context-size cost, prompt-processing speed, thread count (no reliable effect, left on auto).

**UI**
- Header with title, Web mode, free-RAM chip; message labels, typing indicator with live status ("Searching the web..."), source cards, sticky settings actions, grouped settings, chat-only sidebar controls, responsive menu.
- New deterministic UI test suite on a fake backend (26 checks) and hardened real-model suites; tests now clean up their own conversations.

**Tests:** backend 55 -> 126 passed (+1 skipped).

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
