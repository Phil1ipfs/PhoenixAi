# PHOENIX AI

A local, private coding assistant. Runs `qwen2.5-coder:3b` through Ollama on your own CPU. No cloud AI, no API keys.

## Run
1. Make sure Ollama is running (`ollama list` shows `qwen2.5-coder:3b`).
2. Double-click `start-phoenix.bat` (or `cd backend && .venv\Scripts\python run.py`) and open http://127.0.0.1:8000
3. Dev mode with hot reload: backend as above, then `cd frontend && npm run dev` -> http://127.0.0.1:5173

## First-time setup
- Backend: `python -m venv backend/.venv`, then `backend\.venv\Scripts\pip install -r backend/requirements.txt`
- Frontend: `cd frontend && npm install && npm run build` (the backend serves `frontend/dist`)
- Tests: `cd backend && .venv\Scripts\python -m pytest`
- Browser tests (optional): `cd e2e && npm install && node ui.test.mjs` (uses installed Microsoft Edge + the real model)

## Using it
- **Chat tab:** conversations are saved in `backend/data/phoenix.db`. Stop button halts generation and keeps the partial reply.
- **Code tab:** authorize a project folder (button or paste a path). Browse/search files, then **Explain**, **Find bugs**, or ask about the project. PHOENIX only sends small relevant excerpts to the model.
- **Changes panel:** describe an edit, review the side-by-side diff, then **Approve & apply**. Nothing is written without approval. Applied changes can be rolled back (if the file hasn't changed since). Still use Git for anything important.
- **Benchmark tab:** runs 15 small tasks against the selected model, executing model-written Python in a restricted subprocess (asks for your consent first). Takes several minutes; results are stored per model so you can compare 3B vs 7B.

## Switching models
`ollama pull <model>`, then Settings -> Model. History is kept; each reply records which model wrote it. For 7B on 12 GB RAM, expect it to be slower and tighter on memory; check the Benchmark tab.

## Safety
Localhost only. PHOENIX can read only inside folders you authorize, never `.env`/key files, and never modifies files without your approval. It never runs terminal commands.

## Honest expectations
A 3B model is useful for explanations, small functions and simple fixes, and unreliable for subtle bugs and multi-step edits. See STATUS.md (what is verified) and BENCHMARKS.md (measured results).
