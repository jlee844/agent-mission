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


def _did_the_work(path):
    """Write the artifact so its mtime lands AFTER the accept.

    `ok` now means "exists AND changed since you accepted it", because mere
    existence made `done: rewrote auth (README.md)` backed in any repo with a
    README. A test that creates the file first is testing the old claim.
    The nudge is because a filesystem's mtime resolution can be coarse enough
    that two writes in the same millisecond compare equal.
    """
    import os
    import time
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")
    future = time.time() + 2
    os.utime(path, (future, future))
    return path


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
    _did_the_work(tmp_path / "real.py")

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
    _did_the_work(tmp_path / "real.py")
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
    # The line leads with the ID now, and names WHO claimed it: a session
    # that dispatched work cannot otherwise tell a peer's reply from its own
    # earlier claim, and without an id it cannot act on either.
    assert len(out) == 1 and "claimed finished" in out[0]
    assert iid in out[0], "the id must be in the line"
    assert "changed since accepted" in out[0] or "board" in out[0]
    assert S.check("conv-a") == [], "edge, not level"

    st.complete(iid, by="human")
    assert S.check("conv-a") == [], "confirming is silence, not a signal"


def test_the_board_row_carries_the_verdict_in_grey(st, tmp_path):
    """String-guard on the page: the verdict class exists, is styled with the
    muted colour, and the sweep button excludes unbacked rows by reading
    data-backed -- which only ok verdicts emit."""
    from agent_mission.board import PAGE, _tree
    _did_the_work(tmp_path / "real.py")
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
    _did_the_work(tmp_path / "real.py")
    iid = _accepted_item(st, "the work")
    st.claim_done(iid, "done: shipped (real.py)", by="agent")

    assert main(["pending", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "nothing awaiting you" not in out
    assert "[◦]" in out and "done: shipped (real.py)" in out
    # Not "disk agrees" any more: that phrase claimed more than the
    # check did. The verdict now names what was established.
    assert "exists and changed since accepted" in out
    assert "disk agrees" not in out
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


# ---- confirm ALL, including what the disk cannot corroborate --------------
# The tier model says a HUMAN confirms and the disk supplies EVIDENCE. Refusing
# the person their own bulk confirm would make the verifier the authority,
# which is not the design -- and the commonest unbacked row is a claim written
# as prose with no artifact to check, a phrasing failure, not a lie. So it is
# allowed, and it is recorded: what the tool rules out is the two arriving by
# the same gesture, or a trust-tick being indistinguishable from an evidenced
# one afterwards.

def test_sweepall_confirms_unbacked_rows_and_records_that_it_did(st, tmp_path):
    from agent_mission import actions
    _did_the_work(tmp_path / "real.py")
    backed = _accepted_item(st, "backed work")
    blind = _accepted_item(st, "unverifiable work")
    st.claim_done(backed, "done: shipped (real.py)", by="agent")
    st.claim_done(blind, "finished it, trust me", by="agent")

    sess = actions.Session(enabled=True)
    out = actions.apply(sess, sess.code, "sweepall", "g", ids=[])
    assert out["backed"] == 1 and out["unevidenced"] == 1

    m = st.load()
    assert all(i.done for i in m.items if i.id in (backed, blind))
    assert all(e["by"] == "human" for e in st.events()
               if e.get("kind") == "completed"), "the human is still the ticker"

    notes = [e for e in st.events() if e.get("kind") == "observed"
             and "without disk evidence" in str(e.get("value", ""))]
    assert notes, "an unevidenced confirm must leave a record saying so"
    assert blind in notes[0]["value"], "and must name which items"
    assert backed not in notes[0]["value"], \
        "an evidenced row is not laundered into the trust note"


def test_the_trust_note_is_written_before_the_ticks(st, tmp_path):
    """If completing raises partway, the log must still say what was about to
    be taken on trust. Order is the whole guarantee."""
    from agent_mission import actions
    blind = _accepted_item(st, "unverifiable work")
    st.claim_done(blind, "all done", by="agent")
    sess = actions.Session(enabled=True)
    actions.apply(sess, sess.code, "sweepall", "g", ids=[])
    kinds = [e.get("kind") for e in st.events()
             if e.get("kind") in ("observed", "completed")]
    assert kinds.index("observed") < kinds.index("completed")


def test_sweepall_is_refused_on_a_read_only_board(st):
    from agent_mission import actions
    ro = actions.Session(enabled=False)
    with pytest.raises(actions.Unauthorised):
        actions.apply(ro, "anything", "sweepall", "g", ids=[])


def test_the_page_keeps_evidenced_and_trusted_confirms_apart():
    """One click must never be able to mean both. Separate buttons, separate
    labels, and the unevidenced one asks first."""
    from agent_mission.board import PAGE
    assert "data-do=sweepall" in PAGE and "data-do=sweep " in PAGE
    assert "unverified" in PAGE, "the button names what it cannot vouch for"
    assert "confirm(" in PAGE[PAGE.index("sweepall'"):][:900], \
        "the unevidenced sweep is two-step"
    assert ".act.warn" in PAGE, "and never wears the confident accent"


# ── C18: the artifact parser was producing FALSE NEGATIVES ───────────────────
# Each case below was observed on a real claims-done batch: 17 of 18 suggestions
# came back "nothing checkable" or "unbacked" while every file named existed on
# disk. A verifier that reports "the disk disagrees" without having asked the
# disk is worse than one that stays silent, because the board renders it as
# evidence against the claim.

def test_parenthesis_inside_the_description_does_not_hijack_the_artifact(tmp_path):
    from agent_mission.claims import verdict_for
    _did_the_work(tmp_path / "wayfinder.md")
    v = verdict_for("done: LLM feature map (five call sites) present (wayfinder.md)",
                    cwd=str(tmp_path))
    # Previously grabbed "five call sites" and reported it UNBACKED.
    assert v["backed"] == 1 and v["ok"], v


def test_artifact_clause_with_trailing_prose_still_verifies(tmp_path):
    from agent_mission.claims import verdict_for
    _did_the_work(tmp_path / "career_index.json")
    v = verdict_for("done: gap gone (career_index.json gaps)", cwd=str(tmp_path))
    assert v["backed"] == 1 and v["ok"], v


def test_several_artifacts_in_one_clause_are_checked_separately(tmp_path):
    from agent_mission.claims import verdict_for
    _did_the_work(tmp_path / "snippets.py")
    _did_the_work(tmp_path / "lock.json")
    v = verdict_for("done: extractor (snippets.py, lock.json)", cwd=str(tmp_path))
    assert v["backed"] == 2 and v["ok"], v


def test_a_missing_file_among_several_still_fails(tmp_path):
    from agent_mission.claims import verdict_for
    _did_the_work(tmp_path / "real.py")
    v = verdict_for("done: two things (real.py, fiction.py)", cwd=str(tmp_path))
    assert v["backed"] == 1 and v["unbacked"] == ["fiction.py"] and not v["ok"], v


def test_prose_only_claim_is_still_unchecked_not_backed(tmp_path):
    from agent_mission.claims import verdict_for
    v = verdict_for("done: finished the thing, trust me", cwd=str(tmp_path))
    assert v["backed"] == 0 and not v["ok"], v


# ── item 6: existence is not evidence that the work happened ──────────────

def test_a_file_that_predates_the_accept_does_not_sweep(st, tmp_path,
                                                        monkeypatch):
    """The spec's own example: `done: rewrote auth (README.md)` came back
    backed because README.md exists, and "confirm all backed" then ticked it
    `by=human`. The file must have CHANGED since the work was agreed."""
    from agent_mission.actions import Session, apply as act

    (tmp_path / "README.md").write_text("existed long before\n")
    iid = _accepted_item(st, "rewrite auth")
    st.claim_done(iid, "done: rewrote auth (README.md)", by="agent")

    sess = Session(enabled=True)
    out = act(sess, sess.code, "sweep", "g", [])
    assert out.get("done") == [] or iid not in out.get("done", []), out
    assert st.load().items[0].done is False, "ticked on a file it never touched"


def test_touching_the_file_afterwards_makes_it_sweep(st, tmp_path):
    """The other half: once the artifact really moves, the sweep takes it."""
    from agent_mission.actions import Session, apply as act

    (tmp_path / "README.md").write_text("existed long before\n")
    iid = _accepted_item(st, "rewrite auth")
    st.claim_done(iid, "done: rewrote auth (README.md)", by="agent")
    _did_the_work(tmp_path / "README.md")

    sess = Session(enabled=True)
    act(sess, sess.code, "sweep", "g", [])
    item = st.load().items[0]
    assert item.done is True
    assert [e for e in st.events()
            if e.get("kind") == "completed" and e.get("by") == "human"], \
        "one writer for done, and the sweep is the human's click"


def test_the_verdict_separates_exists_from_changed(st, tmp_path):
    """Two fields, because the board needs to say which one it has."""
    from agent_mission.claims import verdict_for

    (tmp_path / "old.py").write_text("x\n")
    iid = _accepted_item(st, "work")
    at = st.load().items[0].accepted_at
    assert at > 0, "the accept must be timestamped or freshness is unknowable"

    v = verdict_for("done: shipped (old.py)", cwd=str(tmp_path),
                    accepted_at=at)
    assert v["exists"] is True, "the file is really there"
    assert v["stale"] == ["old.py"]
    assert v["ok"] is False, "and that is not evidence the work happened"

    _did_the_work(tmp_path / "old.py")
    v = verdict_for("done: shipped (old.py)", cwd=str(tmp_path),
                    accepted_at=at)
    assert v["ok"] is True and v["stale"] == []


def test_no_accepted_timestamp_means_unknown_not_stale(st, tmp_path):
    """Older logs carry no `at` on their accept events. Marking every one of
    their claims stale would be a verdict the evidence does not support."""
    from agent_mission.claims import verdict_for

    (tmp_path / "old.py").write_text("x\n")
    v = verdict_for("done: shipped (old.py)", cwd=str(tmp_path),
                    accepted_at=0.0)
    assert v["ok"] is True and v["stale"] == []


def test_a_directory_counts_when_anything_inside_it_changed(st, tmp_path):
    """Naming a package as the artifact of a change inside it is honest, and
    a directory's own mtime does not move on most filesystems.

    The path needs a separator: `_artifacts` does not treat a bare word as a
    citation, which is pre-existing and right -- "done: shipped (pkg)" names
    nothing checkable.
    """
    from agent_mission.claims import verdict_for

    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text("x\n")
    iid = _accepted_item(st, "work")
    at = st.load().items[0].accepted_at

    assert verdict_for("done: shipped (src/pkg)", cwd=str(tmp_path),
                       accepted_at=at)["ok"] is False
    _did_the_work(pkg / "b.py")
    assert verdict_for("done: shipped (src/pkg)", cwd=str(tmp_path),
                       accepted_at=at)["ok"] is True
