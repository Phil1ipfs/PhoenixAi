# Architecture

```
React/TS UI --/api (NDJSON stream)--> FastAPI --> ModelProvider (interface) --> OllamaProvider --> Ollama :11434
 (lazy Code/Bench chunks)                |
                                         +--> research.py (OPTIONAL) --> DuckDuckGo HTML / Wikipedia API / Open-Meteo / pages
                                         +--> patchgen.py (edit proposals, AST splicing, validation, retries)
                                         +--> SQLite (conversations, messages, settings, projects, patches, benchmarks)
```

## Core
- **Model independence:** `providers/base.py` defines `ModelProvider` (health, list_models, chat_stream, loaded_models, unload). Only `OllamaProvider` knows Ollama. The model id is a runtime setting in SQLite, never hardcoded in logic. Adding another runtime = one new provider class.
- **Memory (data):** SQLite stores model ids as plain strings per message, so changing models never touches stored data. Schema upgrades are additive (`CREATE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`, new settings get defaults) and tested against a v1 database.
- **Streaming:** `POST /api/conversations/{id}/messages` returns NDJSON events: `start`, `warning`*, `status`*, `sources`?, `token`*, `done` | `error`. Client abort (Stop) cancels the upstream request; the partial reply is saved with `status: stopped`.
- **One generation at a time** (409 otherwise): protects RAM. History is trimmed to `max_prompt_chars`.

## Evidence workflow (internet research, optional, default OFF)
1. `needs_verification(question)` - deterministic regex rules (officeholders, weather, prices, news, recent years, "latest version", weak time words outside code questions). Tested with positive and negative tables.
2. Policy by `web_mode`: `off` (never touch the network; time-sensitive questions get the *unverified* banner + a caution in the system prompt), `auto` (search only when step 1 fires), `always`.
3. `gather()`: weather via Open-Meteo for "weather in X", otherwise DuckDuckGo HTML search, Wikipedia API as fallback; top pages are fetched in parallel, boilerplate removed (stdlib `HTMLParser`, infobox rows kept), best-matching blocks selected under a character budget. Each source = `{id, title, url, fetched, retrieved_at, excerpt}`; `fetched=false` means only the search snippet was available and is labelled so.
4. Prompt: numbered sources inside `<<<UNTRUSTED WEB SOURCES ... >>>`; the model is told to answer only from them, cite `[n]`, and say it could not verify otherwise.
5. After generation `verify_citations()`: every `[n]` must be a retrieved source (invalid ones are replaced by "[unverified citation removed]" in the stored/displayed text) and each cited sentence must lexically match its source (numbers must appear in it). Result status: `grounded` / `grounded_weak` / `sources_not_cited`. **This verifies citations, not truth.**
6. The UI banner comes from the status (`unverified_disabled|offline|no_results`), not from anything the model says.
- **Web safety:** public http(s) hosts only, standard ports, no credentials in URLs; DNS result checked for private/loopback/link-local ranges on every redirect hop; robots.txt honoured (fail closed on network errors); 600 KB body cap; 3 redirects max; ~1 search/second; plain descriptive User-Agent (+ optional contact for Wikimedia). Failure of any of this degrades to "unverified", never to made-up sources.

## Coding workspace
- **Workspace (`workspace.py`):** every path is relative, resolved (symlinks followed) and confined to the authorized root; secrets/VCS/dependency folders filtered; plain substring search (no regex/ReDoS); `retrieve_context` scores files on demand under a character budget (no persistent index).
- **Patch proposals (`patchgen.py`):** for Python the model returns *complete new definitions* (it is good at that, poor at copying exact text); we splice them in with the AST (replace by name, append new, hoist imports). Other files use SEARCH/REPLACE. Deterministic validation: syntax (`ast`/`json`), no definitions lost, the symbol named in the request actually changed, focused size (<=120 changed lines), diff round-trip. On failure the exact error (plus the closest real line for a missing SEARCH) is sent back, max 2 retries.
- **Patch lifecycle (`patches.py`, `patch_routes.py`):** proposals are stored `pending` with original/proposed text, hashes and validation info. Apply requires explicit approval, checks the file hash (conflict => 409) and writes atomically. Rollback restores the stored original only if the file still matches the applied hash.
- **Benchmark (`benchmark*.py`):** 26 fixed tasks; Python answers run in an AST-allowlisted `python -I` subprocess with a timeout, only after explicit consent. Patch tasks run the same pipeline the product uses; a `strategy` option allows A/B comparison. A test proves every task fails untouched and passes with a reference solution.

## Resource management
- **Pre-flight memory guard** (`sysinfo.assess_memory`): before a reply, free RAM vs. model size (+ context estimate). `warn` shrinks the context to 2048 and shows a notice; `block` (conservative) returns a `low_memory` error without touching the model. Thresholds are heuristics calibrated on this 12 GB machine (see BENCHMARKS.md) and can be disabled/tuned in Settings.
- **`keep_alive`** (passed to Ollama) and an explicit **Unload model now** action release the ~2 GB model on demand. `num_thread` is exposed but left on auto (no reliable benefit measured).
- **UI:** Code and Benchmark views are lazy chunks; health polling pauses while the tab is hidden; no animation libraries.

## Security summary
Localhost bind, local-only CORS and Ollama URL; workspace confinement; secrets blocked; untrusted-data delimiters for files and web pages; no terminal execution; file edits only after explicit approval; benchmark execution needs consent and is restricted (best effort, not a sandbox); the internet is used only when the user enables it, and only for search queries and page requests.
