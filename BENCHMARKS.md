# Benchmarks

Hardware: i5-8265U (4C/8T), 11.9 GB RAM, Intel UHD 620 (unused; Ollama runs 100% CPU), Windows 11.
All measurements 2026-10-15/16 with `qwen2.5-coder:3b` (Q4, ~2.1 GB resident), temperature 0 for benchmarks.
**The machine was never idle:** Edge, VS Code, Claude, Spotify, Discord etc. were running, free RAM was 0.4-3.7 GB, and run-to-run speed varied by about +/-10-15%. Treat every number as "on this busy laptop".

## 1. Coding benchmark (Benchmark tab; 23 tasks, one attempt each)

Final run at product defaults (patch strategy `auto`), 2026-10-16:

| Category | Result | Verification |
|---|---|---|
| Code generation | 5/5 | model code executed against asserts |
| Bug fixing | 4/4 | fixed function executed against asserts |
| Project comprehension | 3/3 | keyword match on a 3-file sample project (heuristic, lenient) |
| Patch tasks, original 3 | 3/3 | proposal -> validation -> applied -> asserts executed |
| Patch tasks, held-out 8 | 8/8 | same, tasks written before the new strategy was ever run on them |

Speed in that run: avg 6.59 tok/s, avg latency 11.6 s/task, max 39.1 s (comprehension: long prompts), lowest free RAM 750 MB, model RAM 2068 MB.

### Patch-reliability experiment (Priority 3)
Same 11 patch tasks, same model, same settings (temperature 0, ctx 4096), one run each unless noted:

| Strategy | Original 3 | Held-out 8 | Total | Avg latency |
|---|---|---|---|---|
| legacy: SEARCH/REPLACE, single try (the pre-1.2 behaviour) | 2/3 | 5/8 | **7/11** (repeated: 7/11) | ~8-12 s |
| SEARCH/REPLACE + validation + 2 retries | 2/3 | 5/8 | **7/11** | ~11 s |
| **symbol** (model writes complete functions; AST splice; validation; retries) | 3/3 | 8/8 | **11/11** (three runs: 11/11, 11/11, 11/11) | ~7-8 s |

Plus the full 23-task run above (all 11 patch tasks passed again).
- Earlier, on the original 3 tasks only, legacy scored 1/3 twice and 2/3 here: **run-to-run variance exists even at temperature 0**, so single runs of 3 tasks are not reliable.
- Why the old format failed (examined outputs): the model put new code outside the REPLACE block, quoted lines that are not in the file, or "fixed" only half (e.g. a `None` default without creating the list). Retries on the old format did not rescue these (the model repeated the mistake), so retries alone did not help in this sample.
- Why the new one works: the model only has to write a complete function; the code does the splicing and checks it.
- **Caveats:** 11 small single-file Python tasks; "symbol" cannot delete code; the retry path never triggered in the real runs (every symbol attempt passed first time), so retries are verified by scripted tests only; other languages still use SEARCH/REPLACE (no improvement measured there). A prompt tweak (worked example) was also tried and reverted as inconclusive.
- `tests/test_bench_tasks.py` proves each patch task fails on the untouched file and passes with a reference solution (the benchmark can't pass by doing nothing).

## 2. Internet research (Priorities 1-2): real-network integration run
Separate from the mocked tests. Real DuckDuckGo/Open-Meteo/pages + real Qwen, `web_mode` off vs auto, 2026-10-15 (Philippines evening):

| Question | Web OFF (model memory) | Web AUTO |
|---|---|---|
| Current prime minister of Japan? | Banner "not verified". Answer began "I could not verify it live" but then asserted **Fumio Kishida** (stale) | Cited sources [2][3] (Wikipedia snippet, Britannica page) -> **Sanae Takaichi**, matching the retrieved text. 22.5 s |
| Weather in Manila now? | banner; invented "sunny, 29C/21C" from memory | Open-Meteo live reading (27.7 C, 72 %, ...) reproduced correctly [1][2]; one extra uncited claim ("up to 31 C"). 61.9 s |
| Latest stable Python? | answered "3.11.1" (stale) | python.org page -> **3.15.0** [1]. 39.5 s |
| Most recent FIFA World Cup winner? | "France 2022" (stale) | Citations were real but the answer **did not address the question** ("Brazil has won the most titles") and was still labelled "cited". 23.3 s |

Reading: with the web on, 3/4 answers matched the retrieved sources and were cited; 1/4 was cited but unresponsive. I judged correctness against the retrieved text, not against independent ground truth. Offline, the 3B model produced confident stale answers even when told it could not verify; the deterministic banner is what protects the user, not the model's hedging.
- Latency cost: 22-62 s vs 5-10 s (reading 3-4 pages and ~28 tok/s prompt processing).
- Wikipedia pages came back snippet-only because Wikimedia requires an operator contact in the User-Agent (optional setting `web_contact`); other sites' pages were read in full.
- First real attempt found 3 bugs mocks had hidden (DDG rate-limiting a UA containing "robots", double gzip decoding, Wikipedia 403); all fixed and regression-tested.
- Not measured: accuracy on a large question set, other languages/regions, rate-limit behaviour under heavy use.

## 3. RAM and CPU (Priority 4)
**Who uses the memory** (idle snapshot, Working Set): Ollama model runner `llama-server` 2,141 MB | **Edge (46 processes) 1,863 MB** | svchost 1,065 MB | Claude 646 MB | VS Code 603 MB | Spotify 350 MB | Discord 318 MB | Defender 293 MB. Free RAM with the model loaded: ~1.5 GB.
PHOENIX components: backend (Python) **~58-60 MB**; UI in headless Edge ~490 MB working set / ~230 MB private across 9 browser processes with a 7 MB JS heap (the browser, not the app, is the cost); no Node process is needed in production. So the model is ~95 % of PHOENIX's footprint, and your other apps matter more than PHOENIX does. PHOENIX does not (and I did not) stop other apps or Windows services.

**Context size** (cold load each time, same 688-token prompt):

| num_ctx | model RAM (Ollama) | runner working set | uncached prompt speed | gen speed |
|---|---|---|---|---|
| 2048 | 1994 MB | 2055 MB | 27.6 tok/s | 6.28 |
| 4096 | 2068 MB | 2128 MB | 27.8 tok/s | 6.62 |
| 8192 | 2308 MB | 2300 MB | 28.3 tok/s | 6.82 |

-> Context is cheap in RAM (+74 MB for 4096, +314 MB for 8192) but **prompt processing (~28 tok/s) is the real cost**: a 1,700-token source/code context costs ~60 s before the first word. (Repeated identical prefixes are cached by Ollama and become near-instant - that is why chat history is cheap.) Gen speed differences across ctx are noise.

**Threads** (interleaved runs, 3 each, plus an earlier sequential experiment): auto ~6.0-6.7 tok/s, 4 threads ~7.0-7.1, 8 threads ~6.7-6.8, 2 threads ~6.4. Pooled over both experiments (n=6 each): auto ~6.35, 4 ~6.95, 8 ~6.8. The earlier experiment suggested 8 was best and the later one said 4; with +/-10 % noise **no setting is reliably better** (4 might be ~9 % faster). Default left on auto; the setting is exposed.

**keep_alive = 0:** model unloaded right after the reply: free RAM 1.5 GB -> 3.7 GB; next request pays a 5-10 s reload (measured 5.1-10.2 s load; first load of the day was 12-15 s).

**Memory guard:** thresholds were chosen from these measurements and the observation that a 2 GB model loaded with only 1.6 GB "available" (Windows reclaims cache), so it blocks conservatively (<~40 % of needed RAM, or <150 MB when the model is already loaded) and otherwise warns + shrinks context. Verified with mocks and a simulated-RAM UI test; **not** triggered by a genuine out-of-memory event on this machine.

**Frontend:** main bundle 569 KB (176 KB gzip) + lazy Code 14 KB + Bench 6 KB; an attempt to trim highlight.js languages made the bundle larger and was reverted.

## 4. Speed history (manual, early development)
Direct API cold qwen2.5-coder:3b 5.24 tok/s (12.3 s load); warm 6.65; via PHOENIX cold 6.84. llama3.2:3b (comparison only): 7.19 cold.

## Limitations
- Small task counts (23 benchmark tasks, 4 web questions): percentages are indicative, not statistically meaningful.
- Single machine, single model (qwen2.5-coder:3b; llama3.2:3b only for switching checks). No 7B measurements.
- Benchmark tasks are small self-contained Python snippets; they do not predict performance on real multi-file projects or other languages.
- Comprehension grading is keyword-based; web "correctness" is judged against retrieved text, not ground truth.
- RAM numbers are system free-memory samples and process working sets, not exhaustive profiling.
