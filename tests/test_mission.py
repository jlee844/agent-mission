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
