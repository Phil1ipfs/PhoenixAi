# Architecture

```
React/TS UI  --/api (NDJSON stream)-->  FastAPI  -->  ModelProvider (interface)  -->  OllamaProvider --> Ollama :11434
                                           |
                                        SQLite (conversations, messages, settings, projects, benchmarks)
```

- **Model independence:** `backend/app/providers/base.py` defines `ModelProvider` (health, list_models, chat_stream, loaded_models). Only `OllamaProvider` knows Ollama. The model id is a runtime setting stored in SQLite, never hardcoded in logic. Adding another runtime = one new provider class.
- **Memory:** SQLite stores model ids as plain strings per message, so changing models never touches stored data.
- **Streaming:** `POST /api/conversations/{id}/messages` returns NDJSON events: `start`, `token`*, `done` | `error`. Client abort (Stop button) cancels the upstream Ollama request; the partial reply is saved with `status: stopped`.
- **Resource limits:** one generation at a time (409 otherwise); history trimmed to `max_prompt_chars`; `num_ctx` default 2048; per-reply latency, tok/s and free-RAM before/after are recorded in `messages.metrics`.
- **Security:** backend binds 127.0.0.1; CORS limited to localhost; `ollama_url` must be a localhost address; no code execution; no filesystem tools yet.
- **Workspace (`workspace.py`):** every path is relative, resolved (symlinks followed) and checked to stay inside the authorized root; secrets/VCS/dependency folders are filtered; search is plain substring (no regex, so no ReDoS); `retrieve_context` scores files by keyword on demand under a character budget (no persistent index).
- **Patches (`patches.py`, `patch_routes.py`):** the model emits SEARCH/REPLACE blocks, which are applied in memory and stored as a `pending` patch with original/proposed text and hashes. Apply checks the file hash (conflict => 409) and writes atomically. Rollback restores the stored original only if the file still matches the applied hash.
- **Benchmark (`benchmark*.py`):** fixed tasks; Python answers run in an AST-allowlisted `python -I` subprocess with a timeout, only after explicit consent.
- **Single process:** the backend also serves `frontend/dist`, so there is no separate web server.
