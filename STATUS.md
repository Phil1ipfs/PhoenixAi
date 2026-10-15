# STATUS (updated 2026-10-16)

## Verification summary
| Layer | Result |
|---|---|
| Backend automated tests (`cd backend && .venv\Scripts\python -m pytest`) | **126 passed, 1 skipped** (symlink-escape test skipped: this Windows account can't create symlinks). No network or model needed; RAM is pinned in a fixture so tests don't depend on the machine. |
| Deterministic UI tests (`e2e/fake.test.mjs`, scripted model + mocked web, real Edge) | **26/26** |
| Real-model browser tests (`e2e/ui.test.mjs`, `code.test.mjs`, `bench.test.mjs`) | **18/18, 20/20, 3/3** (the chat suite failed 2 checks once while the laptop was heavily loaded, then passed 18/18; those two checks were made load-tolerant) |
| Real-model benchmark, product defaults | 23/23 (see BENCHMARKS.md for caveats) |
| Real-network research run | 4 questions; 3 cited and consistent with sources, 1 cited but unresponsive (BENCHMARKS.md) |

Browser tests run in **headless** Edge via Playwright, with the backend started by `run.py` (what `start-phoenix.bat` runs); the .bat itself wasn't double-clicked. Reproduce: see README.

## Phase F deliverables
| Priority | State |
|---|---|
| 1 Internet research | **Implemented, optional, default off.** Free sources (DuckDuckGo HTML, Wikipedia API, Open-Meteo), robots/SSRF/size guards, clickable numbered sources, live-vs-memory distinction, graceful failure, on/off/auto setting. |
| 2 Factual accuracy workflow | **Implemented**: detect -> retrieve -> ground -> verify citations -> deterministic unverified/offline banners. Tested for outdated-info, fabricated citations, unsupported claims, offline, no results. Limits below. |
| 3 Coding-agent reliability | **Implemented and measured**: same 11 patch tasks 7/11 -> 11/11 (3 repeat runs), incl. 8 held-out. Python only; see caveats. |
| 4 RAM/CPU | **Measured and optimized lightly** (keep_alive, unload, memory guard, lazy chunks, hidden-tab polling). Biggest finding: model ~2.1 GB; other apps dominate; prompt processing ~28 tok/s is the real context cost. |
| 5 Interface | **Done**: header, message labels, source cards, status/loading text, grouped settings, responsive menu, polish from screenshot review. |
| 6 Evaluation | **Done** as listed above. |

## Features
### Implemented and verified
Everything from v1.0/1.1 (chat, streaming, Stop, persistence, model switching, PHOENIX Code, approvals/rollback, benchmark) plus: web research modes, citation verification, source links, symbol-based patch strategy with deterministic validation and retries, memory guard, keep_alive/unload, new settings, lazy-loaded views, deterministic UI suite.

### Implemented but not verified in real life
- Wikipedia page fetching with a contact set (needs your email/URL; mocked test only; without contact it correctly degrades to snippets).
- Memory guard on a genuine out-of-memory event (only simulated); thresholds are heuristics.
- "Choose folder..." native dialog (headless tests); symlink-escape protection (test skipped here).
- Retry loop with the real model (it never needed a retry in the measured runs; verified by scripted tests).
- 7B models (not installed).

### Not implemented
- Terminal/command execution (excluded by design), file deletion/rename, multi-file patches, deletion inside the symbol strategy.
- Better-than-SEARCH/REPLACE strategy for non-Python files (no improvement measured/claimed).
- Semantic search, search for languages other than English, news-specific providers, result caching.
- Authentication; non-Windows testing; Ollama-side tuning that needs system/env changes (flash attention, KV-cache quantization) - documented, not changed.

## Known limitations (read these)
- **"Cited" is not "true".** Citation checks are lexical; a model can cite a real source and still answer a different question (observed once in 4). Time-sensitive answers without web access come from the 3B model's memory and were stale in every test, even when it said it couldn't verify; the banner, not the model, is the safeguard.
- DuckDuckGo's HTML endpoint is a free, unofficial interface; it rate-limits or changes without notice (handled: Wikipedia fallback, then an honest "unverified"). Wikipedia needs `web_contact` to read pages.
- Web answers are slow: 22-62 s (page reading + ~28 tok/s prompt processing).
- Patch strategy gains are measured on 11 small Python tasks; real code will be harder. Always review the diff.
- Free RAM often 1-3 GB with your normal apps open; the browser and other apps use more than PHOENIX. Close heavy apps for long runs.
- One generation at a time; in-memory lock assumes a single backend process.
- Frontend main bundle ~569 KB (176 KB gzip).

## Security posture
Localhost bind; local-only CORS/Ollama URL; workspace confinement; secrets blocked; web/file content treated as untrusted and delimited in prompts; web fetches restricted to public hosts (re-checked on redirects), robots.txt respected, size/time capped; no terminal execution; edits need explicit approval; benchmark execution needs consent and is restricted (best effort, not a sandbox). Internet is used only when you enable it, and only for search queries and page requests.

## Compatibility with future models
Model ids are plain strings in settings/messages; switching needs no migration. A v1.0 database opens unchanged (tested), including the new `patches.meta` column and new setting defaults.
