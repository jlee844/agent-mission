"""`init --on <new>` must not SILENTLY move the running session.

The move is intended -- "one session, several goals" is the model's own
promise, pinned by test_init_on_a_new_name_creates_a_sibling_goal:127. What
bit on 2026-10-24 was the silence:

Hit live on 2026-10-24: creating a goal for an UNRELATED project moved
this session -- which was doing agent-mission work -- onto the new goal
without a word. Its next claim would have landed on the wrong goal. Creating
a goal for a different project is the common case, so the silent re-route is
the C11 cwd-router inversion again: a silent re-route is worse than a refusal.
"""
import pathlib

from agent_mission import missions as M
from agent_mission.__main__ import main


def _template(name: str) -> str:
    return (f"NAME: {name}\n\nOBJECTIVE: do {name}\n\n"
            "SUCCESS:\n- it works\n\nCHECKLIST:\n- step one\n")


def _init(tmp_path, slug, sid, monkeypatch):
    f = tmp_path / f"{slug}.mission"
    f.write_text(_template(slug))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    main(["init", "--on", slug, "--from-file", str(f),
          "--no-edit", "--no-board"])


def test_the_move_is_announced_with_the_way_back(tmp_path, monkeypatch, capsys):
    """The move itself is the tested promise (one session, several goals);
    the bug was that it was SILENT. So it must name the goal left behind."""
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "first-goal", "sess-A", monkeypatch)
    capsys.readouterr()
    _init(tmp_path, "second-goal", "sess-A", monkeypatch)
    out = capsys.readouterr().out
    assert M.attachments().get("sess-A") == "second-goal"
    assert "MOVED" in out, "a silent re-route is the defect"
    assert "mission attach first-goal" in out, "must name the way back"


def test_an_unattached_session_still_attaches(tmp_path, monkeypatch):
    """The first goal of a fresh session must still attach -- that is the
    documented start-of-session flow and must not regress."""
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "only-goal", "sess-B", monkeypatch)
    assert M.attachments().get("sess-B") == "only-goal"


def test_it_says_the_goal_was_created_but_not_joined(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "goal-one", "sess-C", monkeypatch)
    capsys.readouterr()
    _init(tmp_path, "goal-two", "sess-C", monkeypatch)
    out = capsys.readouterr().out
    assert "goal-two" in out
    assert "attach" in out.lower(), (
        "silence is the bug; it must name how to switch")
