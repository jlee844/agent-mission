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
    def branch_of(m):
        return next(n for n in m.tree() if n.item.id == parent)

    branch = branch_of(store.load())
    assert (branch.done_count, branch.total) == (2, 3)
    assert not branch.complete
    store.complete(kids[2], by="human")
    # not tree()[0] any more: a finished branch sinks below unfinished work
    assert branch_of(store.load()).complete


def test_only_leaves_count_as_work(store):
    """A subgoal is a container. Counting it as a task inflates the total and
    can never be ticked honestly."""
    _plan(store)
    m = store.load()
    assert m.total_count == 4, "3 children + 1 flat item, not 5 with the parent"


def test_finished_work_sinks_below_what_is_left(store):
    """The remaining work is what you act on. It should not have to be found
    among ticked boxes -- but inside each group the order you wrote stays."""
    parent, kids, flat = _plan(store)
    for k in kids:
        store.complete(k, by="human")          # the whole branch is now done
    roots = store.load().tree()
    assert [n.item.id for n in roots] == [flat, parent], "done branch sinks"

    store.complete(flat, by="human")
    assert [n.item.id for n in store.load().tree()] == [parent, flat], \
        "everything done -> back to the order it was written in"


def test_a_half_done_branch_does_not_sink(store):
    parent, kids, flat = _plan(store)
    store.complete(kids[0], by="human")
    roots = store.load().tree()
    assert [n.item.id for n in roots] == [parent, flat]
    assert [n.item.id for n in roots[0].children] == [kids[1], kids[2], kids[0]]


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


def test_the_title_falls_back_to_the_objective_at_a_word_boundary(store):
    """A heading cut mid-word reads as a bug. A real card showed
    '...building the whole thin' because the seed was pasted verbatim."""
    long = ("yeah whats important now is to get at least something for every "
            "subpage we have and building the whole thing live locally together")
    store.set_protected("objective", long, by="human")
    t = store.load().title
    assert len(t) <= 72 and t.endswith("…")
    assert not t.rstrip("…").endswith(" ")
    assert long.startswith(t.rstrip("…").rstrip())


def test_a_name_wins_over_the_objective(store):
    store.set_protected("objective", "Ship list sharing to the mobile app", by="human")
    store.set_protected("name", "List sharing on mobile", by="human")
    assert store.load().title == "List sharing on mobile"


def test_a_short_objective_is_used_whole(store):
    store.set_protected("objective", "Ship list sharing", by="human")
    assert store.load().title == "Ship list sharing"


def test_the_agent_cannot_set_the_name(store):
    with pytest.raises(ProtectedFieldError):
        store.set_protected("name", "renamed by the agent", by="agent")


def test_a_protected_field_can_be_changed_later(store):
    """Goals move. A mission you cannot edit is one you abandon and rewrite."""
    store.set_protected("objective", "first", by="human")
    store.set_protected("objective", "second", by="human")
    assert store.load().objective == "second"


def _no_tty(monkeypatch):
    """Exactly what a subagent's shell looks like: no controlling terminal."""
    monkeypatch.delenv("AGENT_MISSION_I_AM_HUMAN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)


def test_an_agents_shell_cannot_rewrite_a_protected_field(tmp_path, monkeypatch, capsys):
    """A subagent rewrote the objective on its first try, and `why` logged the
    change as the human. The store always refused; the CLI could not tell who
    was typing, so the guarantee was advisory."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/tmp", "the real goal", by="human")
    _no_tty(monkeypatch)

    assert main(["set", "objective", "something else", "--session", "s"]) == 1
    assert MissionStore(root_for("s")).load().objective == "the real goal"
    out = capsys.readouterr().out
    assert "not a terminal" in out and "propose" in out


def test_an_agents_shell_cannot_accept_its_own_proposal(tmp_path, monkeypatch):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/tmp", "goal", by="human")
    iid = st.propose("do a thing", by="agent")["item_id"]
    _no_tty(monkeypatch)

    assert main(["accept", iid, "--session", "s"]) == 1
    assert main(["done", iid, "--session", "s"]) == 1
    assert main(["remove", iid, "--session", "s"]) == 1
    m = MissionStore(root_for("s")).load()
    assert not m.items[0].accepted and not m.items[0].done


def test_an_agent_can_still_propose_without_asking(tmp_path, monkeypatch):
    """The refusal must leave the agent a way to be useful, or it routes
    around it."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/tmp", "goal", by="human")
    _no_tty(monkeypatch)
    assert main(["propose", "an idea", "--session", "s"]) == 0
    assert len(MissionStore(root_for("s")).load().checklist) == 1


# ── delegation: one item, its own session ────────────────────────────────────

def _delegated(tmp_path, monkeypatch):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    st = MissionStore(root_for("parent"))
    st.create("parent", "/repo", "Ship it", by="human")
    st.set_protected("constraints", ["no network"], by="human")
    st.set_protected("success_criteria", ["the whole thing ships"], by="human")
    iid = st.propose("Train the model", by="human")["item_id"]
    return main, st, iid


def test_delegating_gives_the_item_its_own_mission(tmp_path, monkeypatch):
    from agent_mission.store import children_of
    main, st, iid = _delegated(tmp_path, monkeypatch)
    st.accept(iid, by="human")
    assert main(["delegate", iid, "--to", "train it", "--session", "parent"]) == 0

    kids = children_of("parent")
    assert list(kids) == [iid]
    child = kids[iid]
    assert child.objective == "Train the model", "copied, never authored"
    assert child.constraints == ["no network"], "limits carry down"
    assert child.success_criteria == [], \
        "a slice of the work does not get to decide the whole mission is done"


def test_an_unaccepted_proposal_cannot_be_delegated(tmp_path, monkeypatch, capsys):
    """Otherwise an agent turns its own suggestion into a goal by proposing it
    and immediately delegating it -- the one move the design exists to stop."""
    from agent_mission.store import children_of
    main, st, iid = _delegated(tmp_path, monkeypatch)
    assert main(["delegate", iid, "--session", "parent"]) == 1
    assert children_of("parent") == {}
    assert "still a proposal" in capsys.readouterr().out


def test_the_parent_plan_shows_what_was_delegated(tmp_path, monkeypatch, capsys):
    """Without it the parent shows the item as untouched while a subagent is
    half way through it."""
    main, st, iid = _delegated(tmp_path, monkeypatch)
    st.accept(iid, by="human")
    main(["delegate", iid, "--to", "train it", "--session", "parent"])
    capsys.readouterr()
    main(["show", "--session", "parent"])
    assert "→ parent.train-it" in capsys.readouterr().out


def test_delegating_twice_does_not_start_over(tmp_path, monkeypatch, capsys):
    main, st, iid = _delegated(tmp_path, monkeypatch)
    st.accept(iid, by="human")
    main(["delegate", iid, "--to", "train it", "--session", "parent"])
    capsys.readouterr()
    assert main(["delegate", iid, "--to", "train it", "--session", "parent"]) == 1
    assert "already has a mission" in capsys.readouterr().out


def test_a_delegated_session_name_is_not_cut_in_half():
    """Third instance of the same slip: 'generate-a-seeded-two-mo'."""
    from agent_mission.__main__ import _slugify
    assert _slugify("Generate a seeded two-moons dataset in numpy") == \
        "generate-a-seeded-two"
    assert _slugify("short one") == "short-one"
    assert _slugify("!!!") == "task"


def test_an_agent_cannot_add_an_already_accepted_item(tmp_path, monkeypatch, capsys):
    """`add` is propose+accept in one step, both as the human. Ungated it was
    the entire authority model in one command: an agent could write an accepted
    item that `mission why` then reported as Jonathan's."""
    from agent_mission.__main__ import DENY_RULES, main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/tmp", "goal", by="human")
    _no_tty(monkeypatch)

    assert main(["add", "a thing I decided myself", "--session", "s"]) == 1
    assert MissionStore(root_for("s")).load().checklist == []
    assert "not a terminal" in capsys.readouterr().out
    assert "Bash(mission add:*)" in DENY_RULES, "and the harness blocks it too"


# ── writing to a session that has no mission ─────────────────────────────────

def test_proposing_without_a_mission_is_refused_not_silently_written(tmp_path):
    """The worse half of a bug reported from another session. `add` crashed
    with NoSuchItemError on the id it had just written; `propose` did NOT
    crash -- it appended an event to disk that load() can never fold, because
    there is no `created` event to start from. No error, no item, and a stray
    directory left behind."""
    from agent_mission.store import NoMissionError
    st = MissionStore(tmp_path / "ghost")
    with pytest.raises(NoMissionError):
        st.propose("an idea", by="agent")
    assert not (tmp_path / "ghost").exists(), "nothing written at all"


def test_observing_and_setting_without_a_mission_are_refused_too(tmp_path):
    from agent_mission.store import NoMissionError
    st = MissionStore(tmp_path / "ghost")
    with pytest.raises(NoMissionError):
        st.observe("notes", "a note")
    with pytest.raises(NoMissionError):
        st.set_protected("objective", "a goal", by="human")


def test_add_on_an_empty_store_explains_instead_of_crashing(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    assert main(["add", "Backend", "--session", "ghost"]) == 1
    out = capsys.readouterr().out
    assert "no mission for this session yet" in out
    assert "Traceback" not in out and "NoSuchItemError" not in out


# ── who actually typed it ────────────────────────────────────────────────────

def test_an_agent_transcribed_objective_is_recorded_as_such(tmp_path, monkeypatch, capsys):
    """The most serious finding in this project, reported by another session:
    `init --from-file` is an agent-runnable path into a protected field, and it
    stamped the result `human`. `mission why objective` then said a person set
    a goal an agent had written -- the exact failure the tool exists to make
    impossible. init stays ungated (the documented flow IS the agent
    transcribing an interview); what changes is that the record says so."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    _no_tty(monkeypatch)                      # an agent's shell
    seed = tmp_path / "m.txt"
    seed.write_text("OBJECTIVE: a goal the agent invented\n")
    assert main(["init", "--from-file", str(seed), "--session", "s",
                 "--cwd", str(tmp_path), "--no-board"]) == 0

    m = MissionStore(root_for("s")).load()
    assert m.objective == "a goal the agent invented"
    assert m.typed_by_human is False, "the record must not claim a person typed it"

    capsys.readouterr()
    main(["show", "--session", "s"])
    assert "an agent transcribed this goal" in capsys.readouterr().out

    capsys.readouterr()
    main(["why", "objective", "--session", "s"])
    assert "typed by agent" in capsys.readouterr().out


def test_a_person_typing_it_is_recorded_plainly(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    seed = tmp_path / "m.txt"
    seed.write_text("OBJECTIVE: a goal I typed myself\n")
    main(["init", "--from-file", str(seed), "--session", "s",
          "--cwd", str(tmp_path), "--no-board"])
    assert MissionStore(root_for("s")).load().typed_by_human is True
    capsys.readouterr()
    main(["show", "--session", "s"])
    assert "an agent transcribed" not in capsys.readouterr().out


def test_authority_is_still_refused_regardless_of_who_typed(store):
    """typed_by records; it never grants. An agent still cannot set a field."""
    with pytest.raises(ProtectedFieldError):
        store.set_protected("objective", "mine now", by="agent", typed_by="human")


def test_a_truncated_log_line_does_not_destroy_the_mission(tmp_path):
    """A log is appended to by processes that can be killed mid-write, so a
    half-written final line is the normal failure. Parsing strictly made one
    bad byte lose the whole mission -- and because the board loads every
    session on its first request, one corrupt log took the board down for
    everyone."""
    st = MissionStore(tmp_path / "m")
    st.create("s", "/repo", "a real goal", by="human")
    i = st.propose("real work", by="human")["item_id"]
    st.accept(i, by="human")
    with st.log.open("a") as fh:
        fh.write('{"kind":"created","by":"hum')      # killed mid-write

    m = st.load()
    assert m is not None and m.objective == "a real goal"
    assert [x.text for x in m.items] == ["real work"]
    assert st.damaged == 1, "the damage is counted, not hidden"


def test_one_unreadable_session_does_not_take_down_the_board(tmp_path, monkeypatch):
    from agent_mission import board
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(board, "live", lambda: [])
    ok = MissionStore(root_for("fine"))
    ok.create("fine", "/repo", "readable", by="human")
    bad = root_for("broken")
    bad.mkdir(parents=True)
    (bad / "events.jsonl").write_text("not json at all\n")

    rows = board.snapshot()
    assert [r["full"] for r in rows] == ["fine"]


def test_delegating_as_an_agent_marks_the_child_fields_too(tmp_path, monkeypatch):
    """create() recorded typed_by correctly; the three set_protected calls
    right after it hardcoded human, so `why name` on a delegated child claimed
    a person had typed it."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("p"))
    st.create("p", "/repo", "parent goal", by="human")
    st.set_protected("constraints", ["no network"], by="human")
    iid = st.propose("a slice", by="human")["item_id"]
    st.accept(iid, by="human")

    _no_tty(monkeypatch)                       # an agent runs delegate
    assert main(["delegate", iid, "--to", "the slice", "--session", "p"]) == 0
    child = MissionStore(root_for("p.the-slice")).load()
    assert child.typed_by_human is False
    assert all(e.get("typed_by") == "agent" for e in
               MissionStore(root_for("p.the-slice")).events()
               if e["kind"] in ("created", "set"))


# ── detours: declared, never inferred ────────────────────────────────────────

def test_a_detour_nests_and_returns(store):
    """Drifting into a subgoal is normal work. The failure is climbing back up
    with nothing that says what the bigger goal was."""
    store.detour("chasing a flaky test")
    store.detour("why is pytest slow")
    assert store.load().detours == ["chasing a flaky test", "why is pytest slow"]
    store.ret()
    assert store.load().detours == ["chasing a flaky test"]
    store.ret()
    assert store.load().detours == []


def test_returning_with_nothing_open_is_not_an_error(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/repo", "goal", by="human")
    assert main(["return", "--session", "s"]) == 0
    assert "nothing to return from" in capsys.readouterr().out


def test_the_agent_may_declare_a_detour_without_a_terminal(tmp_path, monkeypatch):
    """Observable tier: recording is not deciding. This is the honest channel
    for 'I am going on a side quest', which today it takes silently."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/repo", "goal", by="human")
    _no_tty(monkeypatch)
    assert main(["detour", "reading the parser", "--session", "s"]) == 0
    assert MissionStore(root_for("s")).load().detours == ["reading the parser"]


def test_returning_replays_the_guards_you_set_at_the_start(
        tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "Ship list sharing", by="human")
    st.set_protected("constraints", ["no schema migration"], by="human")
    st.set_protected("success_criteria", ["lists sync both ways"], by="human")
    i = st.propose("Wire the sheet", by="human")["item_id"]
    st.accept(i, by="human")
    st.detour("a side quest")

    main(["return", "--session", "s"])
    out = capsys.readouterr().out
    assert "Ship list sharing" in out
    assert "no schema migration" in out, "the guards come back with the goal"
    assert "lists sync both ways" in out
    assert "Wire the sheet" in out, "and what you were on"


# ── whereami: one line, never fails ──────────────────────────────────────────

def test_whereami_is_one_line_in_every_state(tmp_path, monkeypatch, capsys):
    """A statusline that can fail is a statusline that gets removed."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert main(["whereami"]) == 0
    assert capsys.readouterr().out.strip() == "no mission — mission init"

    st = MissionStore(root_for("s"))
    st.create("s", str(tmp_path), "Ship it", by="human")
    st.set_protected("name", "Ship it", by="human")
    i = st.propose("one", by="human")["item_id"]
    st.accept(i, by="human")
    st.propose("an idea", by="agent")
    st.detour("a side quest")

    assert main(["whereami", "--session", "s"]) == 0
    line = capsys.readouterr().out.strip()
    assert "\n" not in line, "exactly one line"
    assert "Ship it" in line and "0/1" in line
    assert "detour: a side quest" in line and "1 proposal waiting" in line


def test_whereami_survives_a_corrupt_log(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    d = root_for("s")
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("not json\n")
    assert main(["whereami", "--session", "s"]) == 0
    assert "Traceback" not in capsys.readouterr().out


def test_a_proposal_does_not_move_your_progress_backwards(store):
    """An agent suggesting work must not change how far along the human is.
    One real board read 8/10 with every agreed task finished and two
    suggestions outstanding."""
    i = store.propose("agreed work", by="human")["item_id"]
    store.accept(i, by="human")
    store.complete(i, by="human")
    assert (store.load().done_count, store.load().total_count) == (1, 1)

    store.propose("an idea the agent had", by="agent")
    m = store.load()
    assert (m.done_count, m.total_count) == (1, 1), "still finished"
    assert len(m.unaccepted) == 1, "and the suggestion is reported separately"


def test_whereami_distinguishes_no_mission_from_ambiguity(tmp_path, monkeypatch,
                                                          capsys):
    """Three states, three answers. 'No mission here' is a STATE and the
    statusline should say so; ambiguity is the one case where silence is right,
    because naming one of several would be a guess and a statusline cannot ask.

    This is the test CI failed on for a month while passing locally: the
    harness exports CLAUDE_CODE_SESSION_ID, so resolution never took the
    empty-directory path a fresh checkout takes."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    assert main(["whereami"]) == 0
    assert capsys.readouterr().out.strip() == "no mission — mission init"

    repo = tmp_path / "repo"
    repo.mkdir()
    for n in ("one", "two"):
        st = MissionStore(root_for(n))
        st.create(n, str(repo), f"goal {n}", by="human")
    monkeypatch.chdir(repo)
    assert main(["whereami"]) == 0
    assert capsys.readouterr().out.strip() == "", "ambiguity says nothing"


def test_the_suite_does_not_inherit_a_session_id():
    """The guard itself. Without it every resolution test answers a question
    the harness set, not the one the test asked -- 168 green locally, red on
    CI. transcript-audit's whole thesis is 'profile the environment before
    trusting a statistic over it', and the suite was caught doing exactly
    that."""
    import os
    assert "CLAUDE_CODE_SESSION_ID" not in os.environ
    assert "AGENT_MISSION_I_AM_HUMAN" not in os.environ


def test_a_second_created_event_cannot_wipe_a_live_plan(store):
    """On 2026-08-19 a session working in transcript-audit appended a `created`
    event to the Tripnom mission with one of Jonathan's chat messages as the
    objective. load() folded from the LAST created, so 52 events -- a 26-item
    plan and 13 pending proposals -- went invisible in one line.

    A second `created` is a bug, not a reset."""
    i = store.propose("real work", by="human")["item_id"]
    store.accept(i, by="human")
    store.create("sess", "/elsewhere", "a stray objective", by="human")

    m = store.load()
    assert m.objective == "Ship the thing", "the original goal stands"
    assert [x.text for x in m.items] == ["real work"], "and the plan"


def test_starting_over_is_something_you_say(store):
    """`init --force --discard-plan` writes it explicitly, so the fold has a
    reason rather than inferring one from a duplicate."""
    i = store.propose("old work", by="human")["item_id"]
    store.accept(i, by="human")
    store.discard(by="human")
    store.create("sess", "/repo", "a new goal", by="human")

    m = store.load()
    assert m.objective == "a new goal" and m.checklist == []


def test_only_a_human_can_discard_a_mission(store):
    with pytest.raises(ProtectedFieldError):
        store.discard(by="agent")
