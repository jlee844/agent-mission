"""After a write, the cached row must match a fresh rebuild on every field
the write can change.

Measured 2026-10-26: declining from the work view took 3.9s to show. The
write landed, but `_Cache._patch` -- which re-folds one mission instead of
rebuilding the whole board -- refreshed the plan tree and `pending_accept`
and NOT `queue`, `pending_confirm`, `state` or `needs_you`. The goals view
and the inbox draw from the tree, so they updated at once; the work view
draws from `queue`, so it sat on the pre-write board until the background
refresh landed. A button that changes nothing on screen for four seconds is
indistinguishable from a broken one.

`_patch` is a second copy of snapshot()'s row-building, and it drifted: a
field added to snapshot (pending_confirm, the same day) never reached it.
So this compares the patch against snapshot itself, field by field, rather
than listing the fields it happens to know about.
"""
import pytest

from agent_mission import board as B
from agent_mission import missions as M
from agent_mission.store import MissionStore

# Changed by a write to the event log, so a patch must refresh them.
# (Activity counts and liveness come from transcripts and processes; the
# cache's standing contract is that those may be one refresh old.)
LOG_DERIVED = ("tree", "done", "total", "pending_accept", "pending_confirm",
               "queue", "state", "needs_you", "verifier")


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    monkeypatch.setattr(B, "activity", lambda *a, **k: None)
    st = MissionStore(M.missions_root() / "g")
    st.create("s", str(tmp_path), "goal", by="human")
    cache = B._Cache(every=3600)          # never refreshes on its own
    cache.get()                           # first full build
    return st, cache


def _fresh(mid="g"):
    return next(r for r in B.snapshot() if r["id"] == mid)


def _assert_matches(cache):
    got = next(r for r in cache.rows if r["id"] == "g")
    want = _fresh()
    for k in LOG_DERIVED:
        assert got[k] == want[k], f"{k} is stale after a write: {got[k]!r} != {want[k]!r}"


def test_accept_refreshes_the_work_view_lanes(board):
    st, cache = board
    a = st.propose("work", by="agent")
    cache.invalidate("g")
    st.accept(a["item_id"], by="human")
    cache.invalidate("g")
    _assert_matches(cache)


def test_a_claim_refreshes_pending_confirm_and_needs_you(board):
    st, cache = board
    a = st.propose("work", by="human"); st.accept(a["item_id"], by="human")
    cache.invalidate("g")
    st.claim_done(a["item_id"], "done: x (README.md)", by="agent", session_id="o")
    cache.invalidate("g")
    _assert_matches(cache)
    got = next(r for r in cache.rows if r["id"] == "g")
    # On failure, say what the store actually holds: this failed once in a
    # full run while passing alone, so the message has to carry the evidence.
    import json
    log = [json.loads(l) for l in (st.root / "events.jsonl").read_text().splitlines()]
    trail = [(e.get("kind"), e.get("item_id"), e.get("by")) for e in log]
    assert got["pending_confirm"] == 1 and got["needs_you"] is True, \
        f"pending_confirm={got['pending_confirm']} needs_you={got['needs_you']} log={trail}"


def test_decline_refreshes_too(board):
    st, cache = board
    a = st.propose("work", by="agent")
    cache.invalidate("g")
    st.remove(a["item_id"], by="human")
    cache.invalidate("g")
    _assert_matches(cache)


def test_a_slow_background_refresh_cannot_undo_a_later_write(tmp_path, monkeypatch):
    """The race behind an intermittent failure here, forced so it is certain.

    After a write is patched in, `invalidate` also starts a FULL rebuild in a
    background thread. That rebuild reads the store when it STARTS and used
    to assign its rows when it FINISHED, unconditionally -- so a rebuild begun
    before your second click landed after that click's patch and put the
    older board back. On the page: a click works, then briefly un-happens.
    """
    import threading
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    monkeypatch.setattr(B, "activity", lambda *a, **k: None)
    st = MissionStore(M.missions_root() / "g")
    st.create("s", str(tmp_path), "goal", by="human")
    a = st.propose("work", by="human"); st.accept(a["item_id"], by="human")

    real = B.snapshot
    gate, started = threading.Event(), threading.Event()
    def snap():
        rows = real()                               # read NOW...
        if threading.current_thread() is not threading.main_thread():
            started.set(); gate.wait(5)             # ...return LATER
        return rows
    monkeypatch.setattr(B, "snapshot", snap)

    cache = B._Cache(every=3600)
    cache.get()
    cache.invalidate("g")              # patch + a background rebuild begins
    assert started.wait(5), "the background rebuild should be running"
    st.claim_done(a["item_id"], "done: x (README.md)", by="agent", session_id="o")
    cache.invalidate("g")              # the second click's patch
    assert next(r for r in cache.rows if r["id"] == "g")["pending_confirm"] == 1
    gate.set()                         # the older rebuild now finishes
    cache._thread.join(5)
    row = next(r for r in cache.rows if r["id"] == "g")
    assert row["pending_confirm"] == 1, \
        "a rebuild that started before the write overwrote the fresher row"


def test_registering_a_verifier_and_abstaining_show_after_a_patch(board):
    """C20's new fields, held to the same rule: patch == full rebuild."""
    st, cache = board
    st.set_verifier("checker-session")
    cache.invalidate("g")
    a = st.propose("work", by="human"); st.accept(a["item_id"], by="human")
    st.claim_done(a["item_id"], "done: x (README.md)", by="agent", session_id="w")
    st.needs_human(a["item_id"], "cannot tell", session_id="checker-session")
    cache.invalidate("g")
    _assert_matches(cache)
    row = next(r for r in cache.rows if r["id"] == "g")
    assert row["verifier"], "the card must be able to name its verifier"
    q = [r for lane in row["queue"].values() for r in lane]
    assert any(r.get("needs_human_note") == "cannot tell" for r in q)
