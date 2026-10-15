import os
import sys

import pytest

from app.workspace import Workspace, WorkspaceError


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "app.py").write_text("def hello_world():\n    return 'hi'\n\nclass Cart:\n    def total(self):\n        return 0\n", encoding="utf-8")
    (root / "src" / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "node_modules" / "lib" / "x.js").write_text("secret junk", encoding="utf-8")
    (root / ".env").write_text("API_KEY=hunter2", encoding="utf-8")
    (root / "server.pem").write_text("-----BEGIN", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (root / "data.txt").write_bytes(b"abc\x00def")
    (tmp_path / "outside.txt").write_text("TOP SECRET OUTSIDE", encoding="utf-8")
    return Workspace(root)


@pytest.mark.parametrize("bad", ["../outside.txt", "src/../../outside.txt", "..\\outside.txt", "/etc/passwd",
                                 "C:\\Windows\\win.ini", "C:/Windows/win.ini", "src/app.py:stream", ".env", "server.pem",
                                 "node_modules/lib/x.js", ".git/config", "src\x00/app.py"])
def test_blocked_paths(proj, bad):
    with pytest.raises(WorkspaceError) as e:
        proj.read_file(bad)
    assert e.value.status in (403, 400)


def test_symlink_escape_blocked(proj, tmp_path):
    link = proj.root / "src" / "link.txt"
    try:
        os.symlink(tmp_path / "outside.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(WorkspaceError) as e:
        proj.read_file("src/link.txt")
    assert e.value.status == 403
    assert all(x["name"] != "link.txt" for x in proj.list_dir("src"))


def test_drive_root_rejected():
    with pytest.raises(WorkspaceError):
        Workspace(os.path.abspath(os.sep))


def test_listing_filters(proj):
    names = [e["name"] for e in proj.list_dir("")]
    assert names == ["src", "data.txt", "logo.png"] or set(names) == {"src", "data.txt", "logo.png"}
    assert ".env" not in names and "node_modules" not in names and ".git" not in names and "server.pem" not in names


def test_binary_rejected(proj):
    with pytest.raises(WorkspaceError) as e:
        proj.read_file("data.txt")
    assert e.value.status == 415


def test_read_range(proj):
    r = proj.read_file("src/app.py", start=4, end=6)
    assert r["content"].startswith("class Cart") and r["start"] == 4 and r["total_lines"] == 6


def test_search_and_filter(proj):
    hits = proj.search("helper")
    assert [h["path"] for h in hits] == ["src/util.py"]
    assert proj.search("hunter2") == [] and proj.search("secret junk") == []  # filtered files never searched
    assert proj.search("def", glob="util*") and all(h["path"].endswith("util.py") for h in proj.search("def", glob="util*"))
    assert proj.search("HELLO", case=False) and not proj.search("HELLO", case=True)


def test_context_retrieval_is_bounded_and_relevant(proj):
    big = proj.root / "src" / "big.py"
    big.write_text("\n".join(f"def f{i}(): return {i}" for i in range(3000)), encoding="utf-8")
    ctx = proj.retrieve_context("how does the Cart total work?", budget=1500)
    assert any(s["path"] == "src/app.py" for s in ctx["sources"])
    assert ctx["chars"] <= 1500 + 700  # budget + tree outline allowance
    focus = proj.retrieve_context("explain", budget=1500, focus="src/big.py")
    assert focus["sources"][0]["path"] == "src/big.py" and sum(len(b) for b in focus["blocks"]) <= 1500 + 200
    assert "hunter2" not in "".join(ctx["blocks"])
