# STATUS (updated 2026-10-15)

## Verification summary
| Layer | Result |
|---|---|
| Backend automated tests (`cd backend && .venv\Scripts\python -m pytest`) | **55 passed, 1 skipped** (symlink-escape test skipped: this Windows account cannot create symlinks) |
| Browser test, chat (`e2e/ui.test.mjs`, real Edge via Playwright, real model) | 18/18 |
| Browser test, PHOENIX Code (`e2e/code.test.mjs`) | 20/20 |
| Browser test, benchmark tab (`e2e/bench.test.mjs`) | 3/3 |
| Live benchmark, real model | 15 tasks, see BENCHMARKS.md |

Browser tests ran in **headless** Edge, started via `run.py` (the command inside `start-phoenix.bat`); the .bat itself was not double-clicked. Run them yourself: start the backend, then `cd e2e && node ui.test.mjs` (also `code.test.mjs`, `bench.test.mjs`; they use the real model and take a few minutes).

## Features
### Implemented and verified (browser + API)
Chat with streaming, Stop, history persistence, new/switch/delete conversations, markdown + syntax highlighting + Copy code, model selection/switching (verified with qwen2.5-coder:3b and llama3.2:3b), settings, connection status, error/loading states, performance metrics per reply.
PHOENIX Code: authorize project folder, lazy file tree, file viewer, plain-text code search, file filter (glob), explain file, bug analysis, project Q&A with bounded context retrieval, source citations.
Controlled editing: AI patch proposal (SEARCH/REPLACE), side-by-side diff, explicit approve/reject, atomic apply, conflict detection (file changed since proposal), change history, rollback (refuses if file changed after apply), new-file creation.
Benchmark: 15 tasks in 4 categories, explicit execution consent, results saved per model.

### Implemented but **not** verified
- "Choose folder..." native dialog (tkinter subprocess): not exercised (headless test). Pasting a path is tested.
- Symlink-escape protection: code exists, automated test skipped on this machine (it also never lists/traverses symlinks).
- Behaviour under real out-of-memory: errors from Ollama are classified (`out_of_memory`) and shown, but I did not trigger a real OOM.
- 7B models: not tested (not installed). Architecture has no 3B-specific code; only the model id changes.

### Not implemented
- Terminal/command execution (deliberately excluded), file deletion/rename, multi-file atomic patches.
- Semantic/embedding search (search is substring + keyword-scored retrieval, computed on demand; no persistent index).
- Authentication (backend is localhost-only; anyone with access to this PC's localhost can use it).
- Non-Windows testing.

## Security posture (as built)
Localhost bind; CORS limited to localhost; Ollama URL must be local; workspace paths are relative, resolved and confined to the authorized root; `.env`, keys, `*.db`, `.git`, `node_modules` etc. are blocked from reading/searching/patching; retrieved code is wrapped as untrusted data in prompts; no file changes without approval; AI output is never executed except in the benchmark, which needs explicit consent and runs an import-allowlisted `python -I` subprocess with a timeout (best-effort, **not** a true sandbox). Path-traversal, secret-file, conflict and rollback behaviours have automated tests.

## Known limitations / issues
- Quality: 3B model. Bug analysis was inconsistent (see BENCHMARKS.md); patch generation succeeded on the simple demo but only 1/3 on the benchmark patch tasks.
- Speed: ~5-8 tok/s; first request after idle +10-15 s; benchmark/patch/explain runs take 10-30 s each.
- RAM: free memory dropped as low as 446 MB while the model and a browser were running. Close heavy apps for long runs.
- Context: workspace requests use `workspace_num_ctx` 4096 and a 6000-char excerpt budget; large files need a line range for patch proposals.
- Frontend bundle ~560 KB (all highlight.js languages).
- One generation at a time (chat, ask, patch and benchmark share a lock); in-memory, assumes a single backend process.
- Rollback restores only the file a patch changed; it is not a general version-control replacement. Use Git for real safety.

## Compatibility with future models
Model ids are plain strings in settings/messages; switching needs no migration. A v1.0 database (made before workspace/patches existed) opens unchanged, with new tables and settings defaults added automatically (tested).
