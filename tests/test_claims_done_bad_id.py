"""`claims-done` with an unknown id must report it, not crash.

Measured 2026-10-25: `claims-done <goal-id> "done: ..."` raised
NoSuchItemError out of `store.claim_done` through `main`, printing a stack
trace. Every other per-item verb goes through `_apply`, which catches it and
prints "no item <id> in this plan".

Confusing a goal id for an item id is the ORDINARY case -- both are short hex
strings -- so a traceback is the worst available answer: it reads as a broken
tool rather than a wrong argument.
"""
import pytest

from agent_mission.__main__ import main


@pytest.fixture()
def goal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s")
    f = tmp_path / "m.mission"
    f.write_text("NAME: P\n\nOBJECTIVE: p\n\nCHECKLIST:\n- real work\n")
    main(["init", "--on", "g", "--from-file", str(f), "--no-edit", "--no-board"])
    return "g"


def test_an_unknown_id_is_reported_not_raised(goal, capsys):
    rc = main(["claims-done", "deadbeef", "done: x (README.md)", "--on", goal])
    out = capsys.readouterr().out
    assert rc == 1, "a wrong id is a failure, so it must not exit 0"
    assert "deadbeef" in out
    assert "no item" in out.lower()


def test_passing_the_GOAL_id_says_so(goal, capsys):
    """The specific confusion that produced this: the goal id looks like an
    item id, so the message must name what was actually passed."""
    rc = main(["claims-done", goal, "done: x (README.md)", "--on", goal])
    out = capsys.readouterr().out
    assert rc == 1
    assert goal in out and "no item" in out.lower()


def test_a_real_id_still_works(goal, capsys):
    import re
    main(["show", "--on", goal])
    iid = re.search(r"\[ \] (\w{8})", capsys.readouterr().out).group(1)
    rc = main(["claims-done", iid, "done: x (README.md)", "--on", goal])
    assert rc == 0
    assert "awaits the human's confirm" in capsys.readouterr().out
