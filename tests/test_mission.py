"""The authority model is the product. Most of these guard it."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.store import (Authority, FIELD_AUTHORITY, MissionStore,   # noqa: E402
                                 ProtectedFieldError, root_for)


@pytest.fixture
def store(tmp_path):
    s = MissionStore(tmp_path / "m")
    s.create("sess", "/repo", "Ship the thing", by="human")
    return s


# ── protected: the agent has no path to these ────────────────────────────────

@pytest.mark.parametrize("fieldname", sorted(
    f for f, a in FIELD_AUTHORITY.items() if a is Authority.PROTECTED))
def test_the_agent_cannot_write_any_protected_field(store, fieldname):
    with pytest.raises(ProtectedFieldError):
        store.set_protected(fieldname, ["hijacked"], by="agent")


def test_the_human_can(store):
    store.set_protected("success_criteria", ["it syncs"], by="human")
    assert store.load().success_criteria == ["it syncs"]


def test_the_agent_cannot_create_a_mission(tmp_path):
    s = MissionStore(tmp_path / "m")
    with pytest.raises(ProtectedFieldError):
        s.create("sess", "/repo", "a goal I invented", by="agent")


def test_the_agent_cannot_declare_an_item_done(store):
    ev = store.propose("do the thing")
    store.accept(ev["item_id"], by="human")
    with pytest.raises(ProtectedFieldError):
        store.complete(ev["item_id"], by="agent")


def test_the_agent_cannot_accept_its_own_proposal(store):
    ev = store.propose("add a caching layer")
    with pytest.raises(ProtectedFieldError):
        store.accept(ev["item_id"], by="agent")


def test_an_unaccepted_proposal_is_inert(store):
    store.propose("add a caching layer")
    m = store.load()
    assert len(m.unaccepted) == 1
    assert m.done_count == 0


# ── the agent CAN record ─────────────────────────────────────────────────────

def test_the_agent_may_observe_because_recording_is_not_deciding(store):
    store.observe("evidence", "tests ran: 33 passed")
    assert store.load().evidence == ["tests ran: 33 passed"]


def test_observing_a_protected_field_is_refused(store):
    with pytest.raises(ValueError):
        store.observe("objective", "something else")


# ── the log is the truth ─────────────────────────────────────────────────────

def test_history_is_kept_not_overwritten(store):
    store.set_protected("objective", "first", by="human")
    store.set_protected("objective", "second", by="human")
    assert store.load().objective == "second"
    assert len(store.why("objective")) >= 3, "both edits plus creation remain"


def test_a_detail_key_cannot_shadow_the_event_envelope(store):
    """A payload field called 'kind' used to overwrite the event's own kind and
    make it unfindable."""
    ev = store._append("observed", "agent", kind="not-this", field="notes",
                       value="x")
    assert ev["kind"] == "observed"


def test_state_is_a_fold_so_order_is_preserved(store):
    a = store.propose("first"); b = store.propose("second")
    store.accept(a["item_id"], by="human")
    store.accept(b["item_id"], by="human")
    store.complete(b["item_id"], by="human")
    items = store.load().items
    assert [i.text for i in items] == ["first", "second"]
    assert [i.done for i in items] == [False, True]


def test_an_empty_store_has_no_mission(tmp_path):
    assert MissionStore(tmp_path / "empty").load() is None


def test_root_is_scoped_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    assert root_for("aaa") != root_for("bbb")


# ── the plan is a tree ───────────────────────────────────────────────────────

def _plan(store):
    a = store.propose("Mobile port", by="human")
    store.accept(a["item_id"], by="human")
    kids = []
    for t in ("types", "selector", "sheet"):
        e = store.propose(t, by="human", parent=a["item_id"])
        store.accept(e["item_id"], by="human")
        kids.append(e["item_id"])
    flat = store.propose("Regenerate PARITY.md", by="human")
    store.accept(flat["item_id"], by="human")
    return a["item_id"], kids, flat["item_id"]


def test_children_nest_under_their_parent(store):
    parent, kids, flat = _plan(store)
    roots = store.load().tree()
    assert [n.item.id for n in roots] == [parent, flat]
    assert [n.item.id for n in roots[0].children] == kids


def test_a_branch_rolls_progress_up_from_its_leaves(store):
    parent, kids, _ = _plan(store)
    store.complete(kids[0], by="human")
    store.complete(kids[1], by="human")
    branch = store.load().tree()[0]
    assert (branch.done_count, branch.total) == (2, 3)
    assert not branch.complete
    store.complete(kids[2], by="human")
    assert store.load().tree()[0].complete


def test_only_leaves_count_as_work(store):
    """A subgoal is a container. Counting it as a task inflates the total and
    can never be ticked honestly."""
    _plan(store)
    m = store.load()
    assert m.total_count == 4, "3 children + 1 flat item, not 5 with the parent"


def test_an_orphan_is_shown_as_a_root_not_dropped(store):
    """Losing a task because its parent vanished is worse than showing it
    slightly out of place."""
    ev = store.propose("stray", by="human", parent="nonexistent")
    store.accept(ev["item_id"], by="human")
    assert [n.item.text for n in store.load().tree()] == ["stray"]


def test_an_unknown_item_id_is_refused(store):
    """Appending an event for an id that is not in the plan used to succeed
    silently, so a typo reported 'done' and changed nothing."""
    from agent_mission.store import NoSuchItemError
    _plan(store)
    with pytest.raises(NoSuchItemError):
        store.complete("not-an-id", by="human")
    with pytest.raises(NoSuchItemError):
        store.accept("not-an-id", by="human")


def test_an_item_can_be_removed(store):
    """Append-only is about not losing history, not about being unable to
    change your mind. A plan you cannot prune stops being a plan."""
    parent, kids, flat = _plan(store)
    store.remove(flat, by="human")
    assert [n.item.id for n in store.load().tree()] == [parent]


def test_removing_a_subgoal_takes_its_subtree(store):
    parent, kids, flat = _plan(store)
    store.remove(parent, by="human")
    m = store.load()
    assert [n.item.id for n in m.tree()] == [flat]
    assert all(k not in [d["id"] for d in m.checklist] for k in kids)


def test_the_agent_cannot_remove_an_item(store):
    _, _, flat = _plan(store)
    with pytest.raises(ProtectedFieldError):
        store.remove(flat, by="agent")


def test_removal_is_soft_the_log_still_has_it(store):
    _, _, flat = _plan(store)
    store.remove(flat, by="human")
    assert any(e.get("kind") == "removed" for e in store.events())
    assert any(e.get("item_id") == flat for e in store.events())
