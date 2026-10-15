# Benchmarks

Hardware: i5-8265U (4C/8T), 11.9 GB RAM, Intel UHD 620 (unused; Ollama runs 100% CPU), Windows 11.
Free RAM at test time was usually 1-3 GB (browser, editors open) and dipped to **446 MB** during one run, so numbers reflect a busy machine, not a best case.
Everything below was measured on 2026-10-15 with `qwen2.5-coder:3b` (Q4, ~2.0 GB resident), temperature 0, context 4096 for workspace/benchmark requests.

## Raw speed (manual timing, earlier in development)

| Test | Model | Gen tokens | tok/s | Load | Notes |
|---|---|---|---|---|---|
| Direct Ollama API, cold | qwen2.5-coder:3b | 115 | 5.24 | 12.3 s | |
| Direct Ollama API, warm | qwen2.5-coder:3b | 66 | 6.65 | 0 | |
| Via PHOENIX, cold | qwen2.5-coder:3b | 50 | 6.84 | 10.1 s | first token at 12.6 s |
| Via PHOENIX, cold | llama3.2:3b (comparison) | 12 | 7.19 | 15.4 s | |

## Coding benchmark v1 (built into the app: Benchmark tab, `POST /api/benchmark/run`)

Full run, 15 tasks, single attempt each (no repeats):

| Category | Result | How it is verified |
|---|---|---|
| Code generation | **5/5** | Model code is executed against asserts (palindrome, fizzbuzz, flatten, roman numerals, merge intervals) |
| Bug fixing | **4/4** | Fixed function executed against asserts (off-by-one, empty list, case/whitespace, mutation + distinct) |
| Project comprehension | **3/3** | Retrieval over a bundled 3-file sample project; answer graded by **keyword match** (heuristic, can be fooled/too lenient) |
| Patch/tool use | **1/3** | Model must emit SEARCH/REPLACE edits; applied; result executed against asserts |
| Patch format valid | 3/3 | Edit blocks parsed and applied without error (but not necessarily *correct*) |

Performance in that run: avg **7.35 tok/s**, avg latency **12.0 s** per task, max **31.2 s** (comprehension tasks, longer prompts), model RAM **2068 MB**, lowest free RAM during the run 2493 MB.

### What the failures were (genuine model errors, not harness bugs)
- `tool_fix_rect`: model put the fix *outside* the REPLACE block, so the edit was a no-op.
- `tool_perimeter`: model quoted functions/lines that were not in the file.

### Prompt experiment (inconclusive, reverted)
Adding a worked example to the patch prompt gave tool pass 1/3 again (different tasks failed) and format-valid 2/3. With 3 tasks this is noise; I reverted the change rather than keep an unproven tweak.

### Quick UI run (1 task/category, run through the browser)
codegen 1/1, bugfix 1/1, comprehension 1/1, tool 0/1; avg 7.32 tok/s; avg latency 15.4 s; lowest free RAM 446 MB.

## Real-world checks inside PHOENIX Code (browser test, one run)
On a 2-function demo project with a planted bug (`add` returns `a - b`):
- Explain: coherent summary, cited `src/calc.py`.
- Find bugs: **inconsistent quality** — one run identified the `a - b` bug correctly; another run said "There are no bugs" and in the next sentence described the subtraction as incorrect. Treat bug analysis from a 3B model as a hint, not a verdict.
- Patch proposal ("Fix add so it returns the sum"): produced a valid edit; shown as a diff; applied only after approval; rollback restored the file byte-for-byte.

## Limitations
- 15 tasks, one attempt each: **percentages are indicative, not statistically meaningful**. Variance between runs is expected even at temperature 0 when the prompt changes.
- Tasks are small, self-contained Python functions; results do not predict performance on real multi-file projects, other languages, or long contexts.
- Comprehension grading is keyword-based.
- Only one model (qwen2.5-coder:3b) has the full benchmark; llama3.2:3b was only used for speed/switching checks. Re-run the benchmark after switching to 7B and compare in the Benchmark tab (results are stored per model).
- RAM numbers are system free-memory samples plus Ollama's reported model size, not per-process profiling.
