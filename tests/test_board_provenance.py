"""Items 11 and 12: where a board write came from, and what id it may name."""
import json

import pytest

from agent_mission import missions as M
from agent_mission.actions import Session, apply as act
from agent_mission.store import MissionStore


@pytest.fixture
def goal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "the goal", by="human", typed_by="human")
    return st


def _last(st):
    return json.loads((st.root / "events.jsonl").read_text()
                      .strip().split("\n")[-1])


# ── item 11: a board click and a typed command looked identical ───────────

def test_a_board_accept_records_that_it_came_through_the_board(goal):
    iid = goal.propose("work", by="agent")["item_id"]
    sess = Session(enabled=True)
    act(sess, sess.code, "accept", "g", [iid])
    ev = _last(goal)
    assert ev["kind"] == "accepted"
    assert ev["by"] == "human", "the authority is unchanged — a person ruled"
    assert ev["via"] == "board"


def test_a_terminal_accept_carries_no_board_marker(goal):
    """Otherwise the field says nothing."""
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    assert _last(goal).get("via") != "board"


def test_why_prints_the_surface(tmp_path, monkeypatch, capsys):
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "first", by="human", typed_by="human")
    st.context_via = "board"
    st.set_protected("objective", "changed from the board", by="human")

    MM.main(["why", "objective", "--on", "g"])
    out = capsys.readouterr().out
    assert "via board" in out, out


# ── item 12: the id is a path component ───────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../evil", "../../x", "a/b", "./x", "..", ".", "", "A-Upper",
    "with space", "x\x00y", "/absolute", "sub/../../x",
])
def test_a_traversing_or_malformed_id_is_refused(goal, bad):
    sess = Session(enabled=True)
    with pytest.raises(ValueError):
        act(sess, sess.code, "accept", bad, ["someid"])


def test_nothing_is_written_when_the_id_is_refused(goal, tmp_path):
    """A refusal that still touched disk would be worse than the hole."""
    before = sorted(p.name for p in M.missions_root().iterdir())
    sess = Session(enabled=True)
    with pytest.raises(ValueError):
        act(sess, sess.code, "observe", "../evil", [], text="x")
    assert sorted(p.name for p in M.missions_root().iterdir()) == before


def test_a_well_shaped_name_that_is_not_a_mission_is_refused(goal):
    """Shape alone is not enough — it must be a mission this store has."""
    sess = Session(enabled=True)
    with pytest.raises(ValueError, match="no mission"):
        act(sess, sess.code, "accept", "not-a-real-goal", ["x"])


def test_the_real_id_still_works(goal):
    iid = goal.propose("work", by="agent")["item_id"]
    sess = Session(enabled=True)
    out = act(sess, sess.code, "accept", "g", [iid])
    assert out.get("ok") or out, out
    assert goal.load().items[0].accepted is True
