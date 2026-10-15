"""Check every planted bug against the REAL test suite in a fresh disposable copy (does the suite detect it?)."""
import json, sys
from common import make_copy, run_tests
from mutants import MUTANTS

root = make_copy()
print("copy:", root)
base = run_tests(root) if not sys.argv[1:] else {"ok": True, "passed": 0, "failed": 0, "seconds": 0}
print("baseline:", base["passed"], "passed,", base["failed"], "failed", base["seconds"], "s")
assert base["ok"], base["output"]
rows = []
ONLY = set(sys.argv[1:])
for m in MUTANTS:
    if ONLY and m['id'].split('_')[0] not in ONLY: continue
    p = root / m["file"]
    src = p.read_text(encoding="utf-8")
    if src.count(m["old"]) != 1:
        rows.append({"id": m["id"], "detected": None, "note": f"edit anchor found {src.count(m['old'])}x"}); continue
    p.write_text(src.replace(m["old"], m["new"]), encoding="utf-8")
    r = run_tests(root)
    p.write_text(src, encoding="utf-8")
    rows.append({"id": m["id"], "detected": not r["ok"], "failed": r["failed"], "failed_ids": r["failed_ids"][:4]})
    print(rows[-1])
print(json.dumps({"root": str(root)}))
