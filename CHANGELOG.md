# Changelog

## Unreleased - Real-time search transparency (2026-10-16)
**What was wrong:** Web Always searched every message, including code, passwords and greetings (they were sent to DuckDuckGo); progress showed one "Searching the web…" for the whole pipeline; nothing recorded which requests were really made, so "live" could not be checked; irrelevant results (e.g. shopping pages) went to the model; slow pages had no overall time limit and a Stop could leave fetches running; follow-ups like "Until when?" were searched as the literal text; every repeat question re-searched (no cache), and there was no way to tell live from earlier results.
- `gather_steps()`: real progress events (Searching / Retrieving N sources / Checking information / Generating answer), a per-request log (question, query, provider, time, status, duration, URLs, publication date, cited or not), relevance filter, page-read time budget with cancellation, publication dates from page metadata only.
- Web Always: privacy/sense skip rules with a visible reason ("Live search skipped: ..."); failures show "Live search unavailable ... offline fallback".
- Cache: in memory, 10 min / 30 min / 1 h by topic, failures never cached, original retrieval time kept, "Cached results used ... Search again" (`fresh: true`).
- Follow-ups anchored on the last real question ("Until when?" then "How about the USA?" both refer to the president question).
- UI: search line, verification label ("Answer supported by the sources" only when the automatic checks pass, else "not independently verified"), published dates, "not cited" marks, Search details panel.
- Tests: +32 web-mode tests (`test_web_modes.py`, incl. 1 live DuckDuckGo test), +5 UI checks.

## Unreleased - Weather accuracy, location reasoning, source conflicts (2026-10-16)
**The bug** (reproduced before fixing, with mocks shaped like real Open-Meteo/DuckDuckGo responses): "What's the weather tomorrow?" searched the web with no location; "I mean USA" was not linked to the previous question (Web Auto: no search at all; Web Always: searched the literal text "I mean USA"); two pages reporting 12 °C and 31 °C went to the model with nothing checking that they conflict, so "both sources agree" could pass; and the old Open-Meteo helper took the first geocoder hit, which for "USA" is **Uşak, Türkiye**.
- New `weather.py`: deterministic place/day/hour parsing with conversation context; countries, states/provinces and continents get a clarifying question instead of a number; validated geocoding (exact-name preference, ambiguity -> list real candidates, country/region hints, "City, State" qualifiers, coordinates checked); validated forecast (coordinates, units, time zone, plausible values); answer written from the numbers with place, coordinates, time zone, current-vs-forecast labels, °F-first for US places. No model call: ~0.2-1.8 s instead of 20-100 s. Errors say "I won't guess".
- `research.py`: removed the unsafe `weather_source`; `contextual_query` for follow-ups; `apply_conflict_guard` removes "sources agree" claims and appends each source's value when temperatures disagree; `move_leading_citations` fixes "1. [1]".
- UI: statuses `weather_live`, `weather_unavailable`, `sources_conflict`; inline source chips after the text, cleaned source titles, per-reply speed line hidden.
- Security/backend (same day): `ollama_url` parsed (look-alike hosts refused); Host/Origin middleware against DNS rebinding and cross-site writes; a send refused by the memory guard leaves no orphan user message; concurrent sends get 409 instead of queueing.
- Tests: +49 weather/location/citation regression tests (incl. 1 live), +5 UI checks, +4 security/concurrency tests.

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
