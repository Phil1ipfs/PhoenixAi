"""Coding benchmark for small local models. Python tasks are verified by executing real tests.

SECURITY NOTE: model output is executed only when the user explicitly starts a benchmark (confirm flag).
The runner applies an AST allowlist and runs in a separate `python -I` process with a timeout and a scratch
directory, but this is best-effort hardening, NOT a security sandbox.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOWED_IMPORTS = {"math", "re", "itertools", "functools", "collections", "string", "typing", "heapq", "bisect",
                   "json", "operator", "dataclasses", "decimal", "fractions", "statistics"}
BANNED_NAMES = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "exit", "quit", "memoryview"}
SAFE_DUNDERS = {"__init__", "__name__", "__eq__", "__lt__", "__repr__", "__str__", "__len__", "__hash__", "__iter__",
                "__next__", "__contains__", "__getitem__", "__add__", "__post_init__"}
FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
SAMPLE_PROJECT = Path(__file__).resolve().parent.parent / "benchmarks" / "sample_project"


def extract_code(output: str) -> str:
    blocks = FENCE.findall(output)
    return (max(blocks, key=len) if blocks else output).strip()


def check_safe(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return f"blocked import: {a.name}"
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                return f"blocked import: {n.module}"
        elif isinstance(n, ast.Name) and n.id in BANNED_NAMES:
            return f"blocked name: {n.id}"
        elif isinstance(n, ast.Attribute) and n.attr.startswith("__") and n.attr not in SAFE_DUNDERS:
            return f"blocked attribute: {n.attr}"
    return None


def run_python_tests(code: str, tests: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Execute `code` followed by `tests` (plain asserts) in an isolated subprocess."""
    problem = check_safe(code)
    if problem:
        return False, problem
    with tempfile.TemporaryDirectory(prefix="phoenix-bench-") as d:
        script = Path(d) / "run.py"
        script.write_text(code + "\n\n" + tests + "\nprint('ALL_PASSED')\n", encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(script)], cwd=d, capture_output=True, text=True, timeout=timeout,
                               env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PYTHONIOENCODING": "utf-8"})
        except subprocess.TimeoutExpired:
            return False, "timeout (possible infinite loop)"
    if r.returncode == 0 and "ALL_PASSED" in r.stdout:
        return True, "ok"
    last = (r.stderr.strip().splitlines() or ["failed"])[-1]
    return False, last[:200]


# ---------------------------------------------------------------- tasks
CODEGEN_RULES = "Reply with only one ```python code block containing the function. No tests, no input(), no explanation."

CODEGEN = [
    {"id": "gen_palindrome", "prompt": "Write a Python function `is_palindrome(s)` that returns True if the string reads the same "
     "forwards and backwards, ignoring case and any non-alphanumeric characters.",
     "tests": "assert is_palindrome('A man, a plan, a canal: Panama')\nassert not is_palindrome('hello')\nassert is_palindrome('')\nassert is_palindrome('No lemon, no melon')"},
    {"id": "gen_fizzbuzz", "prompt": "Write a Python function `fizzbuzz(n)` returning a list of strings for 1..n: 'Fizz' for multiples of 3, "
     "'Buzz' for multiples of 5, 'FizzBuzz' for both, otherwise the number as a string.",
     "tests": "assert fizzbuzz(15)[-1]=='FizzBuzz'\nassert fizzbuzz(5)==['1','2','Fizz','4','Buzz']\nassert fizzbuzz(0)==[]"},
    {"id": "gen_flatten", "prompt": "Write a Python function `flatten(items)` that flattens arbitrarily nested lists of integers into one flat list, preserving order.",
     "tests": "assert flatten([1,[2,[3,4]],5])==[1,2,3,4,5]\nassert flatten([])==[]\nassert flatten([[[]],[1]])==[1]"},
    {"id": "gen_roman", "prompt": "Write a Python function `roman_to_int(s)` converting a Roman numeral string (I,V,X,L,C,D,M, with subtractive forms) to an integer.",
     "tests": "assert roman_to_int('III')==3\nassert roman_to_int('IV')==4\nassert roman_to_int('MCMXCIV')==1994\nassert roman_to_int('LVIII')==58"},
    {"id": "gen_intervals", "prompt": "Write a Python function `merge_intervals(intervals)` that takes a list of [start, end] pairs and returns "
     "a sorted list of merged overlapping intervals as lists.",
     "tests": "assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]\nassert merge_intervals([[1,4],[4,5]])==[[1,5]]\nassert merge_intervals([])==[]"},
]

BUGFIX_RULES = "Reply with only one ```python code block containing the corrected function(s). No explanation."
BUGFIX = [
    {"id": "fix_sum_to", "code": "def sum_to(n):\n    total = 0\n    for i in range(n):\n        total += i\n    return total\n",
     "desc": "`sum_to(n)` should return the sum of integers from 1 to n inclusive, but returns the wrong value.",
     "tests": "assert sum_to(5)==15\nassert sum_to(1)==1\nassert sum_to(0)==0"},
    {"id": "fix_average", "code": "def average(nums):\n    return sum(nums) / len(nums)\n",
     "desc": "`average(nums)` crashes on an empty list; it should return 0 for an empty list.",
     "tests": "assert average([2,4])==3\nassert average([])==0"},
    {"id": "fix_count_words", "code": "def count_words(text):\n    counts = {}\n    for w in text.split(' '):\n        counts[w] = counts.get(w, 0) + 1\n    return counts\n",
     "desc": "`count_words(text)` should count words case-insensitively and ignore extra whitespace, "
             "e.g. 'The  the cat' -> {'the': 2, 'cat': 1}.",
     "tests": "assert count_words('The  the cat')=={'the':2,'cat':1}\nassert count_words('')=={}"},
    {"id": "fix_second_largest", "code": "def second_largest(nums):\n    nums.sort()\n    return nums[-2]\n",
     "desc": "`second_largest(nums)` should return the second largest DISTINCT value, must not mutate the input list, "
             "and return None if there are fewer than two distinct values.",
     "tests": "a=[3,1,3,2]\nassert second_largest(a)==2\nassert a==[3,1,3,2]\nassert second_largest([5,5]) is None"},
]

COMPREHENSION = [
    {"id": "comp_discount", "question": "At what quantity does the bulk discount start, and how large is the discount?",
     "expect": [["10", "ten"], ["15%", "0.15", "15 percent", "15 %"]]},
    {"id": "comp_stock", "question": "What happens when remove_stock is asked to remove more than the available quantity?",
     "expect": [["valueerror", "raises", "raise"], ["insufficient"]]},
    {"id": "comp_ordertotal", "question": "Which functions does Inventory.order_total use to compute the final price?",
     "expect": [["apply_discount"], ["add_tax"]]},
]

SHAPES = "def area_rect(w, h):\n    return w + h\n\n\ndef area_circle(r):\n    return 3 * r * r\n"
# "tool" = the original 3 patch tasks (the ones the product was tuned against).
TOOL_TASKS = [
    {"id": "tool_fix_rect", "name": "shapes.py", "file": SHAPES, "instruction": "Fix area_rect so it returns the area (width times height).",
     "tests": "assert area_rect(3,4)==12\nassert area_circle(1)==3"},
    {"id": "tool_pi", "name": "shapes.py", "file": SHAPES, "instruction": "Change area_circle to use math.pi (add `import math` at the top of the file).",
     "tests": "assert abs(area_circle(1)-3.14159)<1e-3\nassert area_rect(2,2)==4 or area_rect(2,2)==2+2"},
    {"id": "tool_perimeter", "name": "shapes.py", "file": SHAPES, "instruction": "Add a new function perimeter_rect(w, h) that returns the perimeter of a rectangle.",
     "tests": "assert perimeter_rect(2,3)==10"},
]
# "tool_heldout" = 8 further tasks written BEFORE the new strategy was ever run on them (never used for tuning).
TOOL_HELDOUT = [
    {"id": "h_shout", "name": "textutils.py", "file": "def shout(s):\n    return s.lower()\n\n\ndef whisper(s):\n    return s.lower()\n",
     "instruction": "Change shout so it returns the text in upper case followed by an exclamation mark.",
     "tests": "assert shout('hi')=='HI!'\nassert whisper('HI')=='hi'"},
    {"id": "h_peek", "name": "stack.py", "file": "class Stack:\n    def __init__(self):\n        self.items = []\n\n    def push(self, x):\n        self.items.append(x)\n\n    def pop(self):\n        return self.items.pop()\n",
     "instruction": "Add a peek method to Stack that returns the top item without removing it, or None if the stack is empty.",
     "tests": "s=Stack()\nassert s.peek() is None\ns.push(1)\ns.push(2)\nassert s.peek()==2\nassert s.pop()==2\nassert s.peek()==1"},
    {"id": "h_const", "name": "settings.py", "file": "TIMEOUT = 5\nRETRIES = 3\n\n\ndef describe():\n    return f'timeout={TIMEOUT}, retries={RETRIES}'\n",
     "instruction": "Change TIMEOUT to 30.",
     "tests": "assert TIMEOUT==30\nassert RETRIES==3\nassert describe()=='timeout=30, retries=3'"},
    {"id": "h_even", "name": "parity.py", "file": "def is_even(n):\n    return n % 2 == 1\n\n\ndef is_odd(n):\n    return n % 2 == 1\n",
     "instruction": "Fix is_even; it returns the wrong answer.",
     "tests": "assert is_even(2) and not is_even(3)\nassert is_odd(3) and not is_odd(2)"},
    {"id": "h_clamp", "name": "geometry.py", "file": "import math\n\n\ndef hyp(a, b):\n    return math.sqrt(a * a + b * b)\n",
     "instruction": "Add a function clamp(x, lo, hi) that limits x to the range lo..hi.",
     "tests": "assert clamp(5,0,3)==3\nassert clamp(-1,0,3)==0\nassert clamp(2,0,3)==2\nassert hyp(3,4)==5"},
    {"id": "h_none", "name": "names.py", "file": "def full_name(first, last):\n    return first + ' ' + last\n",
     "instruction": "Make full_name treat a None argument as an empty string, and make sure the result has no leading or trailing spaces.",
     "tests": "assert full_name('A','B')=='A B'\nassert full_name(None,'B')=='B'\nassert full_name('A',None)=='A'\nassert full_name(None,None)==''"},
    {"id": "h_mutable", "name": "items.py", "file": "def add_item(item, items=[]):\n    items.append(item)\n    return items\n",
     "instruction": "Fix the mutable default argument bug in add_item.",
     "tests": "a=add_item(1)\nb=add_item(2)\nassert a==[1] and b==[2]\nassert add_item(3,[9])==[9,3]"},
    {"id": "h_temp", "name": "temps.py", "file": "def celsius_to_f(c):\n    return c * 9 / 5\n\n\ndef f_to_celsius(f):\n    return (f - 32) * 5 / 9\n",
     "instruction": "Fix celsius_to_f; it forgets to add 32.",
     "tests": "assert celsius_to_f(100)==212\nassert f_to_celsius(212)==100"},
]


def grade_keywords(answer: str, groups: list[list[str]]) -> bool:
    low = answer.lower()
    return all(any(k in low for k in g) for g in groups)


def catalog() -> list[dict]:
    return ([{"id": t["id"], "category": "codegen"} for t in CODEGEN] + [{"id": t["id"], "category": "bugfix"} for t in BUGFIX]
            + [{"id": t["id"], "category": "comprehension"} for t in COMPREHENSION]
            + [{"id": t["id"], "category": "tool"} for t in TOOL_TASKS]
            + [{"id": t["id"], "category": "tool_heldout"} for t in TOOL_HELDOUT])
