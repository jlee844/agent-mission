"""A board running old code must say so, and `mission board` must fix it.

Measured 2026-10-25: the board on 8976 had run since 2026-10-08 -- 17 days --
so none of the board work since was visible, and reloading the page showed
nothing new. Typing `mission board` did not help: at a terminal it replaced a
board that was READ-ONLY, but a writable one it simply reported as "already
running" and left in place. The login hook joined it on every login. Nothing
anywhere said the code had moved on.
"""
import pytest

from agent_mission import board as B


def test_the_fingerprint_follows_the_code(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    one = B._code_fingerprint(tmp_path)
    assert one == B._code_fingerprint(tmp_path), "stable when nothing changed"
    (tmp_path / "a.py").write_text("x = 2\n")
    assert B._code_fingerprint(tmp_path) != one, "changes when the code does"


def test_touching_a_file_is_not_a_change(tmp_path):
    """A banner that fires on a `touch` teaches you to ignore it."""
    import os
    f = tmp_path / "a.py"; f.write_text("x = 1\n")
    one = B._code_fingerprint(tmp_path)
    os.utime(f, (1, 1))
    assert B._code_fingerprint(tmp_path) == one


def test_stale_when_the_disk_moved_on(monkeypatch):
    monkeypatch.setitem(B._STARTED, "fp", "started-with")
    monkeypatch.setattr(B, "_current_fingerprint", lambda: "on-disk-now")
    assert B._code_stale() is True
    monkeypatch.setattr(B, "_current_fingerprint", lambda: "started-with")
    assert B._code_stale() is False


def test_not_stale_before_serve_recorded_anything(monkeypatch):
    """The CLI imports this module too; only a SERVING board can be stale."""
    monkeypatch.setitem(B._STARTED, "fp", None)
    assert B._code_stale() is False


@pytest.mark.parametrize("ident,restart,expect", [
    ({"writes": True, "code": "a", "stale": False}, False, None),
    ({"writes": True, "code": "a", "stale": True}, False, "stale"),
    ({"writes": True}, False, "stale"),          # predates the fingerprint
    ({"writes": False, "code": "a", "stale": False}, False, "read-only"),
    ({"writes": True, "code": "a", "stale": False}, True, "restart"),
])
def test_when_mission_board_replaces_the_running_one(ident, restart, expect):
    assert B._replace_reason(ident, restart) == expect


def test_the_page_says_so():
    i = B.PAGE.index("payload.stale_code")
    assert "mission board" in B.PAGE[i:i + 600], "the notice names the fix"
