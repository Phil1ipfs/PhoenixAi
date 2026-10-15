# PHOENIX AI

A local, private coding assistant. Runs `qwen2.5-coder:3b` through Ollama on your own CPU. No cloud AI, no API keys. Internet research is optional and off by default.

## Run
1. Make sure Ollama is running (`ollama list` shows `qwen2.5-coder:3b`).
2. Double-click `start-phoenix.bat` (or `cd backend && .venv\Scripts\python run.py`) and open http://127.0.0.1:8000
3. Dev mode with hot reload: backend as above, then `cd frontend && npm run dev` -> http://127.0.0.1:5173

## First-time setup
- Backend: `python -m venv backend/.venv`, then `backend\.venv\Scripts\pip install -r backend/requirements.txt`
- Frontend: `cd frontend && npm install && npm run build` (the backend serves `frontend/dist`)
- Tests: `cd backend && .venv\Scripts\python -m pytest` (126 tests, no network or model needed)
- Browser tests (optional, `cd e2e && npm install`):
  - `node fake.test.mjs` - deterministic UI tests (scripted model + mocked web; starts its own server on :8003)
  - `node ui.test.mjs`, `node code.test.mjs`, `node bench.test.mjs` - real model against the app on :8000 (start the backend first; a few minutes each)

## Using it
- **Chat tab:** conversations are saved in `backend/data/phoenix.db`. Stop halts generation and keeps the partial reply. The header shows the title, Web mode and free RAM.
- **Internet research:** header **Web** selector (or Settings).
  - *off* (default): nothing leaves your PC. Time-sensitive questions (who is the current..., weather, prices, latest version...) get a visible "not verified, may be outdated" banner.
  - *auto*: searches only for time-sensitive questions. Answers cite numbered sources `[1]` linking to the real pages; you see which pages were fully read vs. snippet-only, and a banner if the search failed or citations don't match.
  - *always*: searches every message (slower).
  - "Cited" means the citations are real and match the retrieved text; it does **not** guarantee the answer is right.
  - Optional **Web contact** in Settings lets PHOENIX read Wikipedia pages (Wikimedia requires bots to identify an operator). Leave blank to stay anonymous (snippets only for those pages).
- **Code tab:** authorize a project folder (button or paste a path). Browse/search files, then **Explain**, **Find bugs**, or ask about the project. Only small relevant excerpts go to the model.
- **Changes panel:** describe an edit; PHOENIX proposes a validated patch (strategy, attempts and checks are shown) with a side-by-side diff. **Approve & apply** writes it; nothing is written without approval. Applied changes can be rolled back if the file hasn't changed since. Still use Git for anything important.
- **Web modes (composer):** *Off* never contacts the internet and marks time-sensitive answers as not checked. *Auto* searches only for current-information questions (leaders, news, prices, releases, weather...). *Always* really searches before every answer (DuckDuckGo, Wikipedia fallback), except for messages it won't send to a search engine (passwords/keys/e-mails, code, long private text, greetings), which it says. Under each web answer: "Live search completed" or "Cached results used ... Search again", the sources with publication dates and whether they were cited, and **Search details** (every request made). If searching fails you see "Live search unavailable" and the answer is labelled as the model's offline knowledge.
- **Weather:** answered from live Open-Meteo data without the AI model. Name a city ("weather in Manila tomorrow at 4pm"); for a country or state PHOENIX asks which city, because one temperature can't describe a whole country. Follow-ups like "How about Los Angeles?", "What about tomorrow?" and "until when?" keep the context. Needs Web set to Auto or Always.
- **Benchmark tab:** 23 small tasks (+ patch-strategy selector). Python answers are checked by running tests in a restricted subprocess (asks for your consent first). Takes ~10 minutes on CPU; results are stored per model so you can compare 3B vs 7B.

## Memory and speed tips (measured on an i5-8265U / 12 GB)
- The model holds ~2.1 GB while loaded. Settings -> **Keep model in RAM** (or **Unload model now**) frees it; the next reply reloads it (~5-15 s).
- Context size is cheap in RAM (4096 costs ~70 MB more than 2048) but prompt processing is slow (~28 tokens/s), so large contexts/sources mean 25-60 s replies. Lower **Max sources** / context chars for speed.
- Your other apps matter more than PHOENIX: in our measurements Edge alone used ~1.9 GB. Close heavy apps before long runs; the header turns amber/red when free RAM is low.
- See BENCHMARKS.md for the measurements.

## Switching models
`ollama pull <model>`, then Settings -> Model. History is kept; each reply records which model wrote it. For 7B on 12 GB RAM expect slower and tighter memory; run the Benchmark tab to compare.

## Safety
Localhost only. PHOENIX reads only inside folders you authorize, never `.env`/key files, never modifies files without approval, and never runs terminal commands. Web pages and project files are treated as untrusted data.

## Honest expectations
A 3B model is good for explanations, small functions and focused single-function edits; unreliable for subtle bugs, multi-file changes and reading long sources. See STATUS.md (what is verified) and BENCHMARKS.md (measured results and limits).
