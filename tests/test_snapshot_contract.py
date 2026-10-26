"""`snapshot()` is a published contract: a pane outside this repo reads it.

The pane lives in the editor's own mod directory, written in TSX against a
`claude-code/testing` module that does not exist on this machine -- so its
own tests cannot run, and nothing here would notice if a key were renamed.
The pane would simply render an empty list: no error, no failing test, and
the surface that exists to say "something needs you" would quietly say
nothing.

So the contract is pinned on THIS side, where the suite actually runs. These
four keys are what the pane reads; renaming one is a breaking change and has
to fail here.
"""
import json

import pytest

from agent_mission import missions as M
from agent_mission.__main__ import main


def snapshot(capsys=None):
    """What the pane actually consumes: the CLI's stdout, parsed.

    ⚠️ NOT `board.snapshot()`. `attached` is added by the CLI layer, so
    testing the library function passes while the pane's contract is
    unchecked -- which is how this test was written the first time.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["snapshot"])
    return json.loads(buf.getvalue())
from agent_mission.store import MissionStore

# Exactly the fields the external pane reads. Adding a key is fine; removing
# or renaming one breaks a surface this repo cannot see.
PANE_KEYS = ("id", "title", "needs_you", "attached")


@pytest.fixture()
def one_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("sess", str(tmp_path), "do it", by="human")
    st.set_protected("name", "A goal", by="human")
    return st


def test_every_row_carries_the_keys_the_pane_reads(one_goal):
    rows = snapshot()
    assert rows, "a goal exists, so the snapshot must not be empty"
    for r in rows:
        for k in PANE_KEYS:
            assert k in r, f"{k} missing — the external pane reads it"


def test_needs_you_is_a_bool_not_a_count(one_goal):
    """The pane does `filter(g => g.needsYou)`. A count of 0 is falsy and a
    count of 2 is truthy, so a type change would work by accident until the
    day it did not."""
    for r in snapshot():
        assert isinstance(r["needs_you"], bool), r["needs_you"]


def test_a_claim_awaiting_confirm_means_needs_you(one_goal):
    """The whole point of the pane: a goal with something waiting on the
    human must say so. ⚠️ _session_snapshot computes this separately and
    omits 'review'; snapshot() is the one the pane consumes."""
    ev = one_goal.propose("some work", by="human")
    one_goal.accept(ev["item_id"], by="human")
    one_goal.claim_done(ev["item_id"], "done: x (README.md)", by="agent",
                        session_id="other")
    row = next(r for r in snapshot() if r["id"] == "g")
    assert row["needs_you"] is True, row


def test_the_snapshot_is_json_serialisable(one_goal):
    """The pane shells out to `mission snapshot` and JSON-parses stdout. A
    value json cannot encode turns the pane blank, not noisy."""
    json.dumps(snapshot(), default=str)


def test_queue_rows_carry_the_one_label(one_goal):
    """The pane must not derive labels itself: it did, with a different rule
    from the CLI's, and the two had diverged on the day the second appeared.
    So the label travels in the payload, computed once in Python."""
    from agent_mission.store import label_of, Item
    one_goal.propose("Mode-switch routing — the actual optimisation win",
                     by="human")
    rows = [row for g in snapshot() for lane in g["queue"].values()
            for row in lane]
    assert rows, "a proposal exists, so some lane has a row"
    for row in rows:
        assert "label" in row, row
        assert row["label"] == label_of(Item(row["id"], row["text"])), row
