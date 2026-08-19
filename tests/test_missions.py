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


# ── Phase 3: less ceremony ───────────────────────────────────────────────────

def test_archiving_takes_a_goal_off_the_board_without_deleting_it():
    """A one-off test mission with four unaccepted proposals outranked three
    live sessions. Archiving is a statement about attention, not history."""
    from agent_mission import board
    _legacy("s1", "A finished experiment", "prove something once")
    M.migrate()
    st = MissionStore(M.missions_root() / M.find("finished"))
    before = len(list(st.events()))

    st.archive(by="human")
    assert st.load().archived
    assert len(list(st.events())) == before + 1, "one event, nothing removed"
    assert st.load().objective == "prove something once", "the log is intact"

    import types
    board.live = lambda: []
    assert board.snapshot() == [], "off the board"

    st.archive(by="human", undo=True)
    assert not st.load().archived
    assert len(board.snapshot()) == 1, "and back"


def test_only_a_human_can_archive():
    from agent_mission.store import ProtectedFieldError
    _legacy("s1", "A goal", "a thing")
    M.migrate()
    st = MissionStore(M.missions_root() / M.find("goal"))
    with pytest.raises(ProtectedFieldError):
        st.archive(by="agent")


def test_live_work_outranks_a_finished_goal_whatever_is_pending(monkeypatch):
    from agent_mission import board
    _legacy("dead", "Old experiment", "done long ago")
    _legacy("alive", "Current work", "in progress")
    M.migrate()
    MissionStore(M.missions_root() / M.find("old")).propose("stray", by="agent")

    monkeypatch.setattr(board, "live", lambda: [])
    monkeypatch.setattr(board, "mission_rows", lambda: [
        {"full": "old-experiment", "ended": True, "state": "waiting",
         "mtime": 99, "review": [], "has_mission": True, "pending_accept": 1},
        {"full": "current-work", "ended": False, "state": "waiting",
         "mtime": 1, "review": [], "has_mission": True, "pending_accept": 0},
    ])
    assert [r["full"] for r in board.snapshot()] == ["current-work",
                                                     "old-experiment"]


def test_an_unattached_session_is_offered_the_goals_that_exist(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """"No mission for this session" reads as "start over" when the goal
    already exists and this session simply has not said so."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "brand-new")
    _legacy("s1", "Career hub", "Ship the career pages")
    M.migrate()

    assert main(["show"]) == 1
    out = capsys.readouterr().out
    assert "not attached" in out and "mission attach career-hub" in out
    assert "Ship the career pages" in out


def test_the_reanchor_hook_offers_attach_not_init(monkeypatch, capsys):
    """The hook told a session with no goal to run `mission init` -- creating a
    SECOND goal for work that already has one. The duplicate-mission failure,
    recommended by the tool itself."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fresh")
    _legacy("s1", "Career hub", "Ship the career pages")
    M.migrate()

    assert main(["whereami", "--full"]) == 0
    out = capsys.readouterr().out
    assert "NOT ATTACHED" in out and "mission attach" in out
    assert "career-hub" in out and "Ship the career pages" in out
    assert "mission init" not in out, "never advise a second goal"
