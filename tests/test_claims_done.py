"""C17: the agent suggests the tick, the disk corroborates, the human ratifies.

The composition of two things already built: the proposal flow (suggest, never
decide) and the C16 verifier (the disk votes). `done` keeps one writer.
"""
import pytest

from agent_mission import missions as M
from agent_mission.store import MissionStore


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    s = MissionStore(M.missions_root() / "g")
    s.create("g", str(tmp_path), "the goal", by="human", typed_by="human")
    return s


def _accepted_item(st, text="build the widget"):
    iid = st.propose(text, by="agent")["item_id"]
    st.accept(iid, by="human")
    return iid


def test_claims_done_is_refused_on_unaccepted_and_finished_items(st):
    """Claiming completion of a proposal nobody agreed to would smuggle it
    toward the counter; re-claiming a ticked item is noise."""
    prop = st.propose("unagreed", by="agent")["item_id"]
    with pytest.raises(ValueError, match="not accepted"):
        st.claim_done(prop, "done: x (y)", by="agent")

    iid = _accepted_item(st)
    st.complete(iid, by="human")
    with pytest.raises(ValueError, match="already ticked"):
        st.claim_done(iid, "done: x (y)", by="agent")


def test_a_suggestion_is_observable_and_never_moves_the_counter(st):
    iid = _accepted_item(st)
    before = st.load().done_count
    st.claim_done(iid, "done: widget builds (src/widget.py)", by="agent")
    m = st.load()
    assert m.done_count == before, "a suggestion is not a tick"
    assert [i.id for i in m.suggested] == [iid]
    assert "widget.py" in m.suggested[0].claimed_done


def test_a_real_tick_or_removal_clears_the_suggestion(st):
    a = _accepted_item(st, "one")
    b = _accepted_item(st, "two")
    st.claim_done(a, "done: one (x)", by="agent")
    st.claim_done(b, "done: two (y)", by="agent")

    st.complete(a, by="human")
    m = st.load()
    assert [i.id for i in m.suggested] == [b]
    assert next(i for i in m.items if i.id == a).claimed_done == ""

    st.remove(b, by="human")
    assert st.load().suggested == []


def test_verdict_binding_uses_the_item_carrying_claim_format(st, tmp_path):
    """The verdict is computed from the artifacts the claim NAMES, resolved
    against the mission's cwd -- and a claim naming nothing checkable is
    'unchecked', never counted as backed."""
    from agent_mission.claims import verdict_for
    (tmp_path / "real.py").write_text("x\n")

    good = verdict_for("done: shipped (real.py)", cwd=str(tmp_path))
    assert good["ok"] and good["backed"] == 1

    bad = verdict_for("done: shipped (fiction.py)", cwd=str(tmp_path))
    assert not bad["ok"] and bad["unbacked"] == ["fiction.py"]

    empty = verdict_for("finished everything, trust me", cwd=str(tmp_path))
    assert not empty["ok"] and empty["backed"] == 0, \
        "an uncheckable claim must never sweep"


def test_sweep_ticks_only_fully_backed_rows_by_human(st, tmp_path):
    """The client sends no ids: the server re-verifies every suggestion and
    unbacked rows never sweep -- those need the human's eyes."""
    from agent_mission import actions
    (tmp_path / "real.py").write_text("x\n")
    backed = _accepted_item(st, "backed work")
    unbacked = _accepted_item(st, "invented work")
    plain = _accepted_item(st, "no suggestion at all")
    st.claim_done(backed, "done: shipped (real.py)", by="agent")
    st.claim_done(unbacked, "done: shipped (fiction.py)", by="agent")

    sess = actions.Session(enabled=True)
    out = actions.apply(sess, sess.code, "sweep", "g", ids=[])
    assert out["ids"] == [backed] and out["skipped"] == 1

    m = st.load()
    assert next(i for i in m.items if i.id == backed).done
    assert not next(i for i in m.items if i.id == unbacked).done
    assert not next(i for i in m.items if i.id == plain).done
    ev = [e for e in st.events() if e.get("kind") == "completed"]
    assert ev and all(e["by"] == "human" for e in ev)


def test_the_read_only_board_serves_no_confirm_route(st):
    """C10c: the absence of an ENDPOINT, not the absence of a button. The
    refusal happens before the action is even parsed."""
    from agent_mission import actions
    ro = actions.Session(enabled=False)
    with pytest.raises(actions.Unauthorised):
        actions.apply(ro, "anything", "sweep", "g", ids=[])
    with pytest.raises(actions.Unauthorised):
        actions.apply(ro, "anything", "done", "g", ids=["x"])


def test_signal_announces_suggested_ticks_edge_triggered(st, tmp_path):
    from agent_mission import signal as S
    iid = _accepted_item(st)
    assert S.check("conv-a") == [], "baseline consumes the current state"

    st.claim_done(iid, "done: widget (src/widget.py)", by="agent")
    out = S.check("conv-a")
    assert len(out) == 1 and "says an item" in out[0]
    assert "disk agrees" in out[0] or "board" in out[0]
    assert S.check("conv-a") == [], "edge, not level"

    st.complete(iid, by="human")
    assert S.check("conv-a") == [], "confirming is silence, not a signal"


def test_the_board_row_carries_the_verdict_in_grey(st, tmp_path):
    """String-guard on the page: the verdict class exists, is styled with the
    muted colour, and the sweep button excludes unbacked rows by reading
    data-backed -- which only ok verdicts emit."""
    from agent_mission.board import PAGE, _tree
    (tmp_path / "real.py").write_text("x\n")
    iid = _accepted_item(st)
    st.claim_done(iid, "done: shipped (real.py)", by="agent")
    row = next(r for r in _tree(st.load()) if r["id"] == iid)
    assert row["cd"]["ok"] and row["cd"]["backed"] == 1

    assert "agent says done" in PAGE
    assert ".verdict{color:var(--mut)" in PAGE, "grey, never amber"
    assert "confirm all backed" in PAGE
    assert "data-do=sweep" in PAGE


def test_the_contract_and_security_teach_it(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    md = (root / "commands" / "mission.md").read_text()
    assert "claims-done" in md
    assert "claims-done for finished work" in md
    sec = (root / "docs" / "SECURITY.md").read_text()
    for phrase in ("evidence, not proof", "No auto-confirm",
                   "unbacked rows", "no confirm route"):
        assert phrase in sec, f"SECURITY.md lost {phrase!r}"


def test_pending_surfaces_suggestions_with_their_verdicts(st, tmp_path,
                                                          monkeypatch, capsys):
    """The first field session to use claims-done caught `pending` answering
    "nothing awaiting you here" with six suggestions in the log -- while
    claims-done itself said "awaits the human's confirm". Both kinds of
    waiting are one list now, each suggestion with its evidence and the
    disk's verdict."""
    from agent_mission.__main__ import main
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    (tmp_path / "real.py").write_text("x\n")
    iid = _accepted_item(st, "the work")
    st.claim_done(iid, "done: shipped (real.py)", by="agent")

    assert main(["pending", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "nothing awaiting you" not in out
    assert "[◦]" in out and "done: shipped (real.py)" in out
    assert "disk agrees" in out
    assert f"mission done {iid}" in out, "the command that clears it"


def test_show_counts_suggestions_in_its_summary(st, tmp_path, monkeypatch,
                                                capsys):
    from agent_mission.__main__ import main
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    iid = _accepted_item(st)
    st.claim_done(iid, "done: x (y)", by="agent")
    assert main(["show", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "suggested finished" in out
    assert "[◦]" in out
