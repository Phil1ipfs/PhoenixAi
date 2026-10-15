import pytest

from app import patchgen
from app.patches import PatchError

SHAPES = "def area_rect(w, h):\n    return w + h\n\n\ndef area_circle(r):\n    return 3 * r * r\n"


def fenced(code):
    return f"Here you go:\n```python\n{code}\n```"


# ------------------------------------------------------------- splicing
def test_replace_existing_function_only():
    out = patchgen.splice_python(SHAPES, "def area_rect(w, h):\n    return w * h")
    assert out == "def area_rect(w, h):\n    return w * h\n\n\ndef area_circle(r):\n    return 3 * r * r\n"


def test_append_new_function():
    out = patchgen.splice_python(SHAPES, "def perimeter_rect(w, h):\n    return 2 * (w + h)")
    assert out.startswith(SHAPES.rstrip("\n")) and out.rstrip().endswith("return 2 * (w + h)")
    ns = {}
    exec(compile(out, "x", "exec"), ns)  # test-only: our own literal
    assert ns["perimeter_rect"](2, 3) == 10 and ns["area_rect"](2, 3) == 5


def test_import_hoisted_and_function_replaced():
    out = patchgen.splice_python(SHAPES, "import math\n\ndef area_circle(r):\n    return math.pi * r * r")
    assert out.startswith("import math\n") and "return 3 * r * r" not in out and "w + h" in out


def test_existing_import_not_duplicated_and_docstring_respected():
    src = '"""Module doc."""\nimport math\n\n\ndef f():\n    return 1\n'
    assert patchgen.splice_python(src, "import math\ndef f():\n    return 2").count("import math") == 1
    out = patchgen.splice_python('"""Doc."""\n\n\ndef f():\n    return 1\n', "import re\ndef f():\n    return re.compile('a')")
    assert out.startswith('"""Doc."""\nimport re')


def test_decorated_function_and_class_methods():
    src = "import functools\n\n\n@functools.cache\ndef f(x):\n    return x\n\n\nclass A:\n    def m(self):\n        return 1\n"
    out = patchgen.splice_python(src, "@functools.cache\ndef f(x):\n    return x + 1")
    assert out.count("@functools.cache") == 1 and "return x + 1" in out
    out = patchgen.splice_python(src, "class A:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 2")
    assert "def n(self)" in out and out.count("class A") == 1


def test_rejects_bad_blocks():
    for bad, msg in [("def f(:\n  pass", "syntax error"), ("print('hi')", "Only complete"),
                     ("def a():\n    pass\ndef a():\n    pass", "twice")]:
        with pytest.raises(PatchError) as e:
            patchgen.splice_python(SHAPES, bad)
        assert msg in e.value.message


def test_crlf_original_handled_by_caller_not_corrupted():
    out = patchgen.splice_python(SHAPES, "def area_rect(w, h):\n    return w * h")
    assert "\r" not in out


# ------------------------------------------------------------- validation
def test_validate_catches_noop_syntax_lost_and_unmodified_target():
    with pytest.raises(PatchError, match="did not change"):
        patchgen.validate("a.py", SHAPES, SHAPES, "fix area_rect")
    with pytest.raises(PatchError, match="syntax error"):
        patchgen.validate("a.py", SHAPES, "def x(:\n", "do it")
    with pytest.raises(PatchError, match="removed existing"):
        patchgen.validate("a.py", SHAPES, "def area_rect(w, h):\n    return w * h\n", "fix area_rect")
    changed_other = SHAPES.replace("3 * r * r", "4 * r * r")
    with pytest.raises(PatchError, match="about `area_rect`, but your edit did not change it"):
        patchgen.validate("a.py", SHAPES, changed_other, "Fix area_rect so it multiplies")
    ok = patchgen.validate("a.py", SHAPES, SHAPES.replace("w + h", "w * h"), "Fix area_rect so it multiplies")
    assert {c["name"] for c in ok} >= {"python_syntax", "requested_symbol_changed", "diff_roundtrip", "focused_change"}


def test_validate_limits_edit_size_and_json():
    big = "x = 1\n" + "\n".join(f"y{i} = {i}" for i in range(300))
    with pytest.raises(PatchError, match="keep it focused"):
        patchgen.validate("a.py", "x = 1\n", big, "add stuff")
    with pytest.raises(PatchError, match="not valid JSON"):
        patchgen.validate("a.json", "{}", "{oops", "edit")
    assert patchgen.validate("notes.md", "a", "b", "edit")[0]["ok"] is None   # no checker: honestly reported


# ------------------------------------------------------------- orchestration with scripted model
class Script:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    async def __call__(self, messages, **opts):
        self.prompts.append(messages)
        return self.replies.pop(0), {"wall_ms": 100, "tokens_per_sec": 5.0}


@pytest.mark.asyncio
async def test_symbol_strategy_success_first_try():
    c = Script(fenced("def area_rect(w, h):\n    return w * h"))
    p = await patchgen.propose_edit(c, "s.py", SHAPES, "Fix area_rect to return the area")
    assert p.strategy == "symbol" and p.metrics["attempts"] == 1 and "w * h" in p.text
    assert "Existing top-level names: area_rect, area_circle" in c.prompts[0][1]["content"]


@pytest.mark.asyncio
async def test_retry_feeds_back_exact_error_then_succeeds():
    c = Script(fenced("def area_rect(w, h):\n    return w + h"),                 # no change -> rejected
               fenced("def area_rect(w, h):\n    return w * h"))
    p = await patchgen.propose_edit(c, "s.py", SHAPES, "Fix area_rect to return the area")
    assert p.metrics["attempts"] == 2 and p.attempts[0]["ok"] is False
    feedback = c.prompts[1][-1]["content"]
    assert "rejected" in feedback and "did not change" in feedback


@pytest.mark.asyncio
async def test_retries_are_bounded():
    c = Script(*["no code here"] * 3, "never reached")
    with pytest.raises(patchgen.PatchGenError) as e:
        await patchgen.propose_edit(c, "s.py", SHAPES, "fix area_rect", max_retries=2)
    assert len(e.value.attempts) == 3 and len(c.replies) == 1 and "fenced" in e.value.message


@pytest.mark.asyncio
async def test_search_replace_error_feedback_names_real_line():
    bad = "<<<<<<< SEARCH\n    return w - h\n=======\n    return w * h\n>>>>>>> REPLACE"
    good = "<<<<<<< SEARCH\n    return w + h\n=======\n    return w * h\n>>>>>>> REPLACE"
    c = Script(bad, good)
    p = await patchgen.propose_edit(c, "s.py", SHAPES, "Fix area_rect", strategy="search_replace")
    assert p.metrics["attempts"] == 2 and "closest line that actually exists" in c.prompts[1][-1]["content"]
    assert "w * h" in p.text


@pytest.mark.asyncio
async def test_legacy_strategy_is_single_attempt_without_validation():
    noop = "<<<<<<< SEARCH\n    return w + h\n=======\n    return w + h\n>>>>>>> REPLACE"
    c = Script(noop, "unused")
    p = await patchgen.propose_edit(c, "s.py", SHAPES, "Fix area_rect", strategy="legacy")
    assert p.checks == [] and p.text == SHAPES and len(c.replies) == 1


@pytest.mark.asyncio
async def test_non_python_uses_search_replace_with_honest_checks():
    c = Script("<<<<<<< SEARCH\nhello\n=======\nbye\n>>>>>>> REPLACE")
    p = await patchgen.propose_edit(c, "notes.txt", "hello\n", "say bye")
    assert p.strategy == "search_replace" and p.text == "bye\n"
    assert any(ch["name"] == "syntax" and ch["ok"] is None for ch in p.checks)
