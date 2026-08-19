"""Missions are goals; sessions attach to them.

The store's primary key was the session, so the board answered "what is each
session doing" when the question is "how is each GOAL going". These guard the
inversion and, above all, the migration -- a tool that loses your plan during
an upgrade has failed at the one thing it promises.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission import missions as M                      # noqa: E402
from agent_mission.store import MissionStore, root_for       # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    return tmp_path


def _legacy(sid, name, objective, items=()):
    st = MissionStore(root_for(sid))
    st.create(sid, "/repo", objective, by="human", typed_by="human")
    st.set_protected("name", name, by="human", typed_by="human")
    for t in items:
        i = st.propose(t, by="human")["item_id"]
        st.accept(i, by="human")
    return st


def test_migration_is_lossless():
    st = _legacy("s1", "Career hub", "Ship the career pages", ("one", "two"))
    before = st.load()
    rows = M.migrate()
    after = MissionStore(M.missions_root() / rows[0]["mission"]).load()
    assert after.objective == before.objective
    assert after.name == before.name
    assert [i.text for i in after.items] == [i.text for i in before.items]
    assert (after.done_count, after.total_count) == (before.done_count,
                                                     before.total_count)


def test_migration_is_idempotent():
    """Keying the skip on the slug minted "<name>-2" on every run: 9 missions
    became 18, in the one command whose whole promise is that re-running it is
    safe."""
    _legacy("s1", "Career hub", "a goal")
    assert len(M.migrate()) == 1
    assert M.migrate() == [] and M.migrate() == []
    assert len(M.all_missions()) == 1


def test_a_mission_is_addressed_by_name_not_by_session():
    _legacy("5fd98e2e-long-uuid", "Ship Tripnom", "Get it to the App Store")
    M.migrate()
    assert M.find("tripnom") == "ship-tripnom"
    assert M.find("Ship Tripnom") == "ship-tripnom"


def test_an_ambiguous_name_refuses():
    from agent_mission.session import NoSessionError
    _legacy("a", "Mission board", "one")
    _legacy("b", "Mission layer", "two")
    M.migrate()
    with pytest.raises(NoSessionError):
        M.find("mission")


def test_many_sessions_serve_one_goal_and_activity_sums(monkeypatch):
    """One goal spanning four sessions used to read as four cards, each
    showing a slice of the work."""
    from agent_mission import board
    _legacy("s1", "Career hub", "Ship the career pages")
    M.migrate()
    mid = M.find("career")
    M.attach("s2", mid)
    M.attach("s3", mid)
    assert M.sessions_of(mid) == ["s1", "s2", "s3"]

    monkeypatch.setattr(board, "live", lambda: [])
    monkeypatch.setattr(board, "activity",
                        lambda tp: type("A", (), {"calls": 10, "files": {},
                                                  "tests": 1, "failures": 0})())
    monkeypatch.setattr(board, "transcript_for", lambda sid: Path("/x"))
    rows = board.snapshot()
    assert len(rows) == 1, "one card for the goal, not three for the sessions"
    assert rows[0]["calls"] == 30, "activity sums across attached sessions"
    assert len(rows[0]["sessions"]) == 3


def test_re_attaching_moves_a_session_to_the_new_goal():
    """Sessions pivot. The latest attachment wins, and the history stays."""
    _legacy("a", "Career hub", "one")
    _legacy("b", "Tripnom", "two")
    M.migrate()
    M.attach("s9", M.find("career"))
    assert M.attachments()["s9"] == "career-hub"
    M.attach("s9", M.find("tripnom"))
    assert M.attachments()["s9"] == "tripnom"


def test_cwd_never_routes_a_write(monkeypatch, tmp_path):
    """C11c: sessions open at the Mission Control root because the work needs
    the whole tree, while the goal lives three folders down. cwd says what a
    session can SEE, never what it is FOR."""
    from agent_mission.__main__ import _mission_target
    _legacy("s1", "Career hub", "a goal")
    M.migrate()
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    a = type("A", (), {"on": None, "cwd": str(tmp_path), "session": None})()
    assert _mission_target(a) is None, "no name, no attachment -> no target"

    a.on = "career"
    assert _mission_target(a) == ("career-hub", "explicit")
