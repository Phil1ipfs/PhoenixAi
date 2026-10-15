"""Benchmark validity: each patch task's tests must FAIL on the untouched file and PASS on a known-good solution."""
import pytest

from app import benchmark as bm
from app import patchgen

# reference solutions expressed exactly like the model should answer in "symbol" mode
REFERENCE = {
    "tool_fix_rect": "def area_rect(w, h):\n    return w * h",
    "tool_pi": "import math\n\ndef area_circle(r):\n    return math.pi * r * r",
    "tool_perimeter": "def perimeter_rect(w, h):\n    return 2 * (w + h)",
    "h_shout": "def shout(s):\n    return s.upper() + '!'",
    "h_peek": "class Stack:\n    def __init__(self):\n        self.items = []\n\n    def push(self, x):\n        self.items.append(x)\n\n"
              "    def pop(self):\n        return self.items.pop()\n\n    def peek(self):\n        return self.items[-1] if self.items else None",
    "h_const": "TIMEOUT = 30",
    "h_even": "def is_even(n):\n    return n % 2 == 0",
    "h_clamp": "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))",
    "h_none": "def full_name(first, last):\n    return ((first or '') + ' ' + (last or '')).strip()",
    "h_mutable": "def add_item(item, items=None):\n    if items is None:\n        items = []\n    items.append(item)\n    return items",
    "h_temp": "def celsius_to_f(c):\n    return c * 9 / 5 + 32",
}
ALL = bm.TOOL_TASKS + bm.TOOL_HELDOUT


def test_every_task_has_a_reference():
    assert {t["id"] for t in ALL} == set(REFERENCE)
    assert len({t["id"] for t in ALL}) == len(ALL) == 11


@pytest.mark.parametrize("task", ALL, ids=lambda t: t["id"])
def test_tests_fail_before_and_pass_after(task):
    ok_before, _ = bm.run_python_tests(task["file"], task["tests"])
    assert not ok_before, "task would pass without any edit"
    fixed = patchgen.splice_python(task["file"], REFERENCE[task["id"]])
    assert patchgen.validate(task["name"], task["file"], fixed, task["instruction"])
    ok_after, detail = bm.run_python_tests(fixed, task["tests"])
    assert ok_after, detail


def test_codegen_and_bugfix_tests_are_not_trivially_true():
    for t in bm.BUGFIX:
        assert not bm.run_python_tests(t["code"], t["tests"])[0], t["id"]
