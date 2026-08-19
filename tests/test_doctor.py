"""Diagnostics over the missions themselves, not the installation.

Every finding here is a fact about a file. No model, no similarity, no
inference -- which is the only kind of finding this project has managed to
make stick.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.doctor import findings                    # noqa: E402
from agent_mission.store import MissionStore, root_for       # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))


def _kinds(base=None):
    return {f["what"] for f in findings()}


def test_a_clean_mission_reports_nothing():
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    assert findings() == []


def test_a_duplicate_mission_start_is_reported():
    """The Tripnom incident: a second `created` made 52 events invisible."""
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "the real goal", by="human", typed_by="human")
    st.create("s", "/repo", "a stray sentence", by="human", typed_by="human")
    assert "duplicate mission start" in _kinds()
    assert any(f["level"] == "serious" for f in findings())


def test_an_event_written_from_another_directory_is_reported():
    """The fingerprint of a session working elsewhere writing in here -- which
    is exactly how that duplicate arrived."""
    st = MissionStore(root_for("s"))
    st.create("s", "/repo/tripnom", "the goal", by="human", typed_by="human")
    st.create("s", "/repo/transcript-audit", "oops", by="human", typed_by="human")
    hit = [f for f in findings() if f["what"] == "written from another directory"]
    assert hit and "transcript-audit" in hit[0]["detail"]


def test_writes_with_no_provenance_are_counted():
    """A legacy check by construction: create() now always stamps typed_by, so
    a new event cannot lack it. What this counts is history -- 39 events in the
    real corpus that cannot say whether a person or an agent made them, and
    the number can only shrink."""
    import json
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    with st.log.open("a") as fh:                 # a line from before the fix
        fh.write(json.dumps({"kind": "set", "by": "human", "field": "name",
                             "value": "old", "at": 1.0}) + "\n")
    hit = [f for f in findings() if f["what"] == "unprovenanced writes"]
    assert hit and hit[0]["level"] == "note", "a note, not an alarm"


def test_a_new_write_can_never_be_unprovenanced():
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    st.set_protected("name", "x", by="human")    # typed_by not passed
    assert all("typed_by" in e for e in st.events()
               if e["kind"] in ("created", "set"))


def test_an_agent_transcribed_goal_is_surfaced_not_alarmed():
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="agent")
    hit = [f for f in findings() if f["what"] == "agent-transcribed goal"]
    assert hit and hit[0]["level"] == "note", "permitted, and worth re-reading"


def test_the_backlog_names_the_command_that_clears_it():
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    st.propose("an idea", by="agent")
    hit = [f for f in findings() if f["what"] == "awaiting you"]
    assert hit and "mission accept --pending" in hit[0]["detail"]


def test_a_damaged_log_is_reported_rather_than_hidden(tmp_path):
    """events() skips what it cannot parse. Skipping silently would make the
    log look complete when events are missing from it."""
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    with st.log.open("a") as fh:
        fh.write('{"kind":"set","by":"hum\n')
    hit = [f for f in findings() if f["what"] == "damaged log"]
    assert hit and hit[0]["level"] == "serious"


# ── the review lane: only what a person can close ───────────────────────────

def test_the_lane_excludes_findings_nobody_can_clear():
    """Nine of sixteen findings in the real corpus are unprovenanced writes --
    true statements about history that no action changes. A lane containing
    them carries a permanent badge, and a permanent badge is one you stop
    seeing in a day."""
    import json
    from agent_mission.doctor import findings, review
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    with st.log.open("a") as fh:
        fh.write(json.dumps({"kind": "set", "by": "human", "field": "name",
                             "value": "old", "at": 1.0}) + "\n")
    assert any(f["what"] == "unprovenanced writes" for f in findings())
    assert review() == [], "reported on demand, never in the lane"


def test_every_lane_item_has_something_that_removes_it():
    """The eligibility rule IS the feature. An item with no closing action is
    a permanent accusation."""
    from agent_mission.doctor import CLEARABLE, review
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "the real goal", by="human", typed_by="human")
    st.create("s", "/elsewhere", "a stray sentence", by="human", typed_by="human")
    lane = review()
    assert lane and all(f["what"] in CLEARABLE for f in lane)


def test_acknowledging_clears_it_without_touching_the_record():
    from agent_mission.doctor import findings, review
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "the real goal", by="human", typed_by="human")
    st.create("s", "/repo", "a stray sentence", by="human", typed_by="human")
    before = len(list(st.events()))
    assert any(f["what"] == "duplicate mission start" for f in review())

    st.acknowledge("duplicate mission start", by="human")
    assert not any(f["what"] == "duplicate mission start" for f in review())
    assert any(f["what"] == "duplicate mission start" for f in findings()), \
        "still true, still reported by doctor -- reading it changed nothing"
    assert len(list(st.events())) == before + 1, "one event added, none altered"
    assert st.load().objective == "the real goal", "and the mission is intact"


def test_only_a_human_can_say_they_read_it():
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    with pytest.raises(Exception):
        st.acknowledge("duplicate mission start", by="agent")


def test_a_detour_left_open_all_day_asks_to_be_closed():
    import time
    from agent_mission.doctor import review
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "a goal", by="human", typed_by="human")
    st.detour("chasing a flaky test")
    assert not any(f["what"] == "detour left open" for f in review()), \
        "a fresh detour is ordinary work"

    ev = st.log.read_text().splitlines()
    import json
    last = json.loads(ev[-1]); last["at"] = time.time() - 9 * 3600
    st.log.write_text("\n".join(ev[:-1] + [json.dumps(last)]) + "\n")
    hit = [f for f in review() if f["what"] == "detour left open"]
    assert hit and "mission return" in hit[0]["detail"]
