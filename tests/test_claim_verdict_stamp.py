"""Measured claims: the disk's verdict is recorded AT the moment of claiming.

Until now a claim's verdict was recomputed whenever something looked at it,
so "how often do agents claim things the disk does not back?" had no answer:
the files kept changing under the old claims. Stamping the verdict into the
claims_done event freezes what the disk said THEN. Old events carry no
stamp; they read 'unrecorded' and are never back-filled, because a verdict
computed today about a claim made weeks ago would be a fabrication.

Stamped inside store.claim_done, so every route that records a claim -- CLI,
board, a test -- is measured the same way, from one definition.
"""
import json
import time

import pytest

from agent_mission import missions as M
from agent_mission.claims import claim_report
from agent_mission.store import MissionStore


@pytest.fixture()
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    goal = tmp_path / "goal"; goal.mkdir()
    (goal / "real.py").write_text("x")
    s = MissionStore(M.missions_root() / "g")
    s.create("s", str(goal), "goal", by="human")
    return s


def _item(st):
    ev = st.propose("work", by="human"); st.accept(ev["item_id"], by="human")
    return ev["item_id"]


def _do_the_work(st):
    """Change the file AFTER the item was agreed -- that is what `ok` means.
    A file already there when it was accepted reads `stale`, correctly."""
    time.sleep(0.02)
    (__import__("pathlib").Path(st.load().cwd) / "real.py").write_text("changed")


def _last_claim(st):
    log = [json.loads(l) for l in (st.root / "events.jsonl").read_text().splitlines()]
    return [e for e in log if e["kind"] == "claims_done"][-1]


@pytest.mark.parametrize("text,want", [
    ("done: wrote it (real.py)", "ok"),
    ("done: wrote it (missing.py)", "unbacked"),
    ("done: just trust me", "unchecked"),
    ("done: everything (./)", "too_broad"),
])
def test_the_verdict_is_stamped_into_the_event(st, text, want):
    i = _item(st)
    _do_the_work(st)
    st.claim_done(i, text, by="agent", session_id="o")
    assert _last_claim(st)["verdict"] == want


def test_an_untouched_file_is_stamped_stale(st):
    """The case the first draft of this test tripped over by accident."""
    i = _item(st)
    st.claim_done(i, "done: wrote it (real.py)", by="agent", session_id="o")
    assert _last_claim(st)["verdict"] == "stale"


def test_a_file_outside_the_folder_is_stamped_uncheckable(st, tmp_path):
    other = tmp_path / "elsewhere.log"; other.write_text("x")
    i = _item(st)
    st.claim_done(i, f"done: x ({other})", by="agent", session_id="o")
    assert _last_claim(st)["verdict"] == "uncheckable"


def test_the_report_counts_and_never_backfills(st):
    a, b, c = _item(st), _item(st), _item(st)
    _do_the_work(st)
    st.claim_done(a, "done: x (real.py)", by="agent", session_id="o")
    st.claim_done(b, "done: x (missing.py)", by="agent", session_id="o")
    st.complete(b, by="human")                 # a human ticked an unbacked claim
    # an OLD-shape claim, with no verdict field, as written before this change
    with open(st.root / "events.jsonl", "a") as fh:
        fh.write(json.dumps({"kind": "claims_done", "by": "agent", "at": 1.0,
                             "item_id": c, "text": "done: old", "session_id": "o"}) + "\n")
    before = (st.root / "events.jsonl").read_bytes()
    r = claim_report([st])
    assert (st.root / "events.jsonl").read_bytes() == before, "read-only"
    assert r["claims"] == 3
    assert r["by_verdict"]["ok"] == 1
    assert r["by_verdict"]["unbacked"] == 1
    assert r["by_verdict"]["unrecorded"] == 1, "old events are not back-filled"
    assert r["unbacked_later_ticked"] == 1
    assert r["recorded"] == 2
    # percentages are over RECORDED claims only -- unrecorded ones say nothing
    assert r["pct_checkable"] == 100.0       # ok + unbacked both named a file
    assert r["pct_unbacked"] == 50.0


def test_the_cli_report_runs_without_waiting_for_stdin(st, capsys, monkeypatch):
    """`claims` is a hook that reads stdin; `--report` must not."""
    import io
    from agent_mission.__main__ import main
    monkeypatch.setattr("sys.stdin", io.StringIO(""))   # would block if read
    i = _item(st); _do_the_work(st)
    st.claim_done(i, "done: x (real.py)", by="agent", session_id="o")
    assert main(["claims", "--report", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "1 recorded when made" in out and "ok 1" in out
