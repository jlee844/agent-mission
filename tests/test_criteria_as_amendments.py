"""An acceptance criterion is an AMENDMENT to an item, not a sibling row.

C19-4, fixing C18: `sharpen --propose` filed one new top-level checklist row
per finding, so a pass over ten loose items added ten rows of noise to the
plan -- the C12d wallpaper failure with a new name. The criterion belongs ON
the item it sharpens.

Same tier (a proposal, inert until accepted), same accept flow (the human
accepts by id), NO new authority: only a human may accept, exactly as before.
"""
import pytest

from agent_mission.store import MissionStore, ProtectedFieldError


@pytest.fixture()
def st(tmp_path):
    s = MissionStore(tmp_path / "m")
    s.create("sess", str(tmp_path), "do the thing", by="human")
    return s


def _item(st, text="write the parser"):
    ev = st.propose(text, by="human")
    st.accept(ev["item_id"], by="human")
    return ev["item_id"]


def test_an_accepted_criterion_lands_on_the_item_not_the_plan(st):
    iid = _item(st)
    before = len(st.load().checklist)
    ev = st.propose_criterion(iid, "cite tests/test_parser.py", by="agent")
    st.accept(ev["item_id"], by="human")
    m = st.load()
    assert len(m.checklist) == before, "a criterion must not add a plan row"
    row = next(d for d in m.checklist if d["id"] == iid)
    assert row["criteria"] == ["cite tests/test_parser.py"], row


def test_it_is_inert_until_accepted(st):
    iid = _item(st)
    st.propose_criterion(iid, "cite tests/test_parser.py", by="agent")
    row = next(d for d in st.load().checklist if d["id"] == iid)
    assert row["criteria"] == [], "an unaccepted criterion must not apply"


def test_a_pending_criterion_is_visible_so_it_can_be_accepted(st):
    iid = _item(st)
    ev = st.propose_criterion(iid, "cite README.md", by="agent")
    pend = st.load().pending_criteria
    assert len(pend) == 1
    assert pend[0]["id"] == ev["item_id"]
    assert pend[0]["target"] == iid
    assert pend[0]["text"] == "cite README.md"


def test_only_a_human_may_accept_one(st):
    iid = _item(st)
    ev = st.propose_criterion(iid, "cite README.md", by="agent")
    with pytest.raises(ProtectedFieldError):
        st.accept(ev["item_id"], by="agent")


def test_accepting_clears_it_from_pending(st):
    iid = _item(st)
    ev = st.propose_criterion(iid, "cite README.md", by="agent")
    st.accept(ev["item_id"], by="human")
    assert st.load().pending_criteria == []


def test_several_criteria_accumulate_in_order(st):
    iid = _item(st)
    for t in ("cite a.py", "cite b.py"):
        ev = st.propose_criterion(iid, t, by="agent")
        st.accept(ev["item_id"], by="human")
    row = next(d for d in st.load().checklist if d["id"] == iid)
    assert row["criteria"] == ["cite a.py", "cite b.py"], row


def test_a_removed_pending_criterion_never_applies(st):
    iid = _item(st)
    ev = st.propose_criterion(iid, "cite nope.py", by="agent")
    st.remove(ev["item_id"], by="human")
    m = st.load()
    assert m.pending_criteria == []
    row = next(d for d in m.checklist if d["id"] == iid)
    assert row["criteria"] == []


def test_a_criterion_on_an_unknown_item_is_refused(st):
    with pytest.raises(Exception):
        st.propose_criterion("deadbeef", "cite a.py", by="agent")


def test_a_pending_criterion_shows_up_where_you_accept_things(
        tmp_path, monkeypatch, capsys):
    """⚠️ Found by probing, not by the unit tests above: the amendment landed
    in `pending_criteria` and `mission pending` listed only checklist rows, so
    the criterion was unacceptable -- a proposal nobody can see is worse than
    no proposal, because the agent reports it filed one."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "probe")
    f = tmp_path / "m.mission"
    f.write_text("NAME: Probe\n\nOBJECTIVE: probe it\n\n"
                 "CHECKLIST:\n- make the thing work\n")
    main(["init", "--on", "probe-goal", "--from-file", str(f),
          "--no-edit", "--no-board"])
    iid = next(d["id"] for d in _load(tmp_path).checklist)
    capsys.readouterr()

    main(["sharpen", "--propose"])
    capsys.readouterr()
    main(["pending"])
    out = capsys.readouterr().out
    cid = next(c["id"] for c in _load(tmp_path).pending_criteria)
    assert cid in out, "the criterion must be acceptable, so it must be shown"
    assert iid in out, "and it must say which item it amends"


def test_accept_pending_applies_a_criterion_too(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "probe")
    f = tmp_path / "m.mission"
    f.write_text("NAME: Probe\n\nOBJECTIVE: probe it\n\n"
                 "CHECKLIST:\n- make the thing work\n")
    main(["init", "--on", "probe-goal", "--from-file", str(f),
          "--no-edit", "--no-board"])
    main(["sharpen", "--propose"])
    main(["accept", "--pending"])
    m = _load(tmp_path)
    assert m.pending_criteria == [], "accept --pending must clear it"
    assert m.checklist[0]["criteria"], "and apply it to the item"


def _load(tmp_path):
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    return MissionStore(M.missions_root() / "probe-goal").load()
