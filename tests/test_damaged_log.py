"""Item 10: a line that PARSES but cannot be folded.

`{"kind":"accepted","by":"human"}` -- valid JSON, no `item_id` -- gave a
KeyError traceback from `mission show` AND from `mission doctor`, the tool
whose whole job is to diagnose a damaged log. `whereami` went silent, and the
board dropped the card with no message at all.

SECURITY.md said damage is "surfaced, not swallowed". That was true only for
lines that fail to parse as JSON.
"""
import json

import pytest

from agent_mission import board as B
from agent_mission import missions as M
from agent_mission.store import MissionStore

BAD = {"kind": "accepted", "by": "human"}          # no item_id


@pytest.fixture
def mission_with_a_bad_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    root = M.missions_root() / "demo"
    st = MissionStore(root)
    st.create("s1", str(tmp_path), "a goal that must not vanish", by="human")
    st.propose("real work", by="agent")
    with (root / "events.jsonl").open("a") as fh:
        fh.write(json.dumps(BAD) + "\n")
    return root


def test_load_returns_the_mission_and_counts_the_damage(
        mission_with_a_bad_event):
    st = MissionStore(mission_with_a_bad_event)
    m = st.load()
    assert m is not None, "the goal is still on disk; losing it is the bug"
    assert m.objective == "a goal that must not vanish"
    assert st.damaged == 1
    assert "item_id" in st.damage[0], st.damage


def test_a_torn_line_and_a_bad_event_share_one_counter(
        mission_with_a_bad_event):
    """One number means 'lines this fold could not use', not two kinds of
    damage reported under one name."""
    with (mission_with_a_bad_event / "events.jsonl").open("a") as fh:
        fh.write("{not json at all\n")
    st = MissionStore(mission_with_a_bad_event)
    st.load()
    assert st.damaged == 2


def test_doctor_reports_it_instead_of_crashing(mission_with_a_bad_event,
                                               capsys):
    """It crashed here, which is the worst place for it to crash.

    The spec's Prove line said "doctor exits 0". That was MY expectation and
    it was wrong: `doctor` exits 1 when it finds something serious, which is
    its designed contract and what makes it usable in a script. The property
    worth asserting is that it RUNS and NAMES the damage -- a traceback is
    neither, and a traceback was what it did.
    """
    from agent_mission import __main__ as MM
    rc = MM.main(["doctor"])                   # must not raise
    out = capsys.readouterr().out
    assert rc == 1, "a serious finding is a non-zero exit, by design"
    assert "damaged log" in out
    assert "item_id" in out, "it should say WHAT was wrong, not just count"


def test_show_does_not_traceback(mission_with_a_bad_event, capsys):
    from agent_mission import __main__ as MM
    rc = MM.main(["show", "--on", "demo"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "a goal that must not vanish" in out


def test_whereami_still_speaks(mission_with_a_bad_event, capsys):
    """It went silent, which reads as 'no mission' rather than 'damaged'."""
    from agent_mission import __main__ as MM
    MM.main(["whereami", "--on", "demo"])
    assert capsys.readouterr().out.strip() != ""


def test_the_board_still_shows_the_card(mission_with_a_bad_event,
                                        monkeypatch):
    monkeypatch.setattr(B, "live", lambda: [])
    rows = B.mission_rows()
    assert len(rows) == 1, "the card disappeared with no message — the bug"
    assert rows[0]["damaged"] == 1
    assert rows[0]["damage"], "the count without the reason is not a report"


def test_a_log_that_cannot_be_folded_at_all_renders_a_placeholder(
        tmp_path, monkeypatch):
    """_safe_load returned None, and None means 'no mission here' — so an
    unreadable log and an empty directory rendered identically."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    root = M.missions_root() / "wrecked"
    st = MissionStore(root)
    st.create("s1", str(tmp_path), "goal", by="human")

    def boom(self):
        raise RuntimeError("log is unreadable")

    monkeypatch.setattr(MissionStore, "load", boom)
    rows = B.mission_rows()
    assert len(rows) == 1
    assert rows[0]["damaged"]
    assert "unreadable" in " ".join(rows[0]["damage"])
    # It must carry every key a healthy row has, or the card throws instead
    # of reporting.
    for key in ("tree", "done", "total", "sessions", "claims_bad", "title"):
        assert key in rows[0], key


def test_a_healthy_log_reports_no_damage(tmp_path, monkeypatch):
    """A field that is non-zero on every row tells you nothing."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    st = MissionStore(M.missions_root() / "clean")
    st.create("s1", str(tmp_path), "goal", by="human")
    rows = B.mission_rows()
    assert rows[0]["damaged"] == 0
    assert rows[0]["damage"] == []
