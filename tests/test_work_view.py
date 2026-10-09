"""The work view and the routing it displays.

The board is grouped by WHO ACTS NEXT, because "which session handles what"
is the question a board with several live sessions has to answer and a
per-goal tree cannot answer it at a glance.
"""
import re
from pathlib import Path

import pytest

from agent_mission import board as B
from agent_mission import missions as M
from agent_mission.store import LEASE_TTL, MissionStore


@pytest.fixture
def goal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "Ship the router", by="human")
    return st


def _four_lanes(st):
    ids = [st.propose(t, by="agent")["item_id"]
           for t in ("a", "b", "c", "unaccepted")]
    for i in ids[:3]:
        st.accept(i, by="human")
    st.lease(ids[0], "workerA")
    st.claim_done(ids[1], "done: shipped (x.py)", by="agent")
    st.finding(ids[2], "not actually finished", session_id="verifier1")
    return ids


# ── the lanes must partition ─────────────────────────────────────────────

def test_every_leaf_is_in_exactly_one_lane(goal):
    _four_lanes(goal)
    m = goal.load()
    lanes = m.lanes()
    seen = [i.id for v in lanes.values() for i in v]
    assert len(seen) == len(set(seen)), "an item in two lanes"
    assert set(seen) == {i.id for i in m.leaves}, "an item in no lane"


def test_each_lane_holds_what_its_name_says(goal):
    ids = _four_lanes(goal)
    lanes = goal.load().lanes()
    assert [i.id for i in lanes["progress"]] == [ids[0]]
    assert [i.id for i in lanes["review"]] == [ids[1]]
    assert [i.id for i in lanes["ready"]] == [ids[2]]
    assert [i.id for i in lanes["waiting"]] == [ids[3]]


def test_an_expired_lease_returns_the_item_to_ready(goal):
    """A session that dies mid-item must not park it forever."""
    ids = _four_lanes(goal)
    m = goal.load()
    later = m.leaves[0].leased_at + LEASE_TTL + 1
    assert m.lane_of(m.leaves[0], now=later) == "ready"


def test_the_board_and_the_cli_cannot_disagree(goal):
    """One lane rule, in `Mission.lane_of`. Two copies is how a row reads
    'in progress' on the board while the store hands it to someone else."""
    _four_lanes(goal)
    m = goal.load()
    rows = B.mission_rows()[0]["queue"]
    for lane, items in m.lanes().items():
        if lane == "done":
            continue
        assert [r["id"] for r in rows[lane]] == [i.id for i in items], lane


# ── the routing loop ─────────────────────────────────────────────────────

def test_a_finding_clears_the_claim_and_requeues_without_a_human(goal):
    """The whole feature. The claim was only a suggestion, so clearing it
    takes nothing from anyone: nothing was un-accepted and nothing ticked."""
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.lease(iid, "workerA")
    goal.claim_done(iid, "done: shipped (x.py)", by="agent")
    assert goal.load().lane_of(goal.load().leaves[0]) == "review"

    goal.finding(iid, "the file is unchanged since you accepted",
                 session_id="verifier1")
    m = goal.load()
    item = m.leaves[0]
    assert m.lane_of(item) == "ready"
    assert item.claimed_done == "", "the claim must be cleared"
    assert item.leased_by == "", "and the lease, or it reads as in progress"
    assert item.accepted is True, "a finding never un-accepts"
    assert item.done is False
    assert "unchanged" in item.rework
    assert item.rework_by == "verifier1"


def test_rework_outranks_fresh_work(goal):
    """The only ordering judgement here, and it is stated not inferred."""
    fresh = goal.propose("fresh work", by="agent")["item_id"]
    old = goal.propose("older work", by="agent")["item_id"]
    for i in (fresh, old):
        goal.accept(i, by="human")
    goal.finding(old, "send it back")
    assert goal.load().next_ready().id == old


def test_a_second_session_cannot_take_a_held_item(goal):
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.lease(iid, "workerA")
    with pytest.raises(ValueError, match="held by"):
        goal.lease(iid, "workerB")


def test_a_session_cannot_take_work_the_human_has_not_accepted(goal):
    """The one move the whole design exists to prevent."""
    iid = goal.propose("unagreed", by="agent")["item_id"]
    with pytest.raises(ValueError, match="not accepted"):
        goal.lease(iid, "workerA")


def test_a_finding_cannot_reopen_the_humans_own_tick(goal):
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.complete(iid, by="human")
    with pytest.raises(ValueError, match="already ticked"):
        goal.finding(iid, "I disagree")


def test_a_human_tick_overrides_a_standing_finding(goal):
    """One writer for `done` means the person gets the last word."""
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.finding(iid, "sent back")
    goal.complete(iid, by="human")
    item = goal.load().leaves[0]
    assert item.done is True and item.rework == ""


def test_a_finding_needs_a_reason(goal):
    """An empty one is a silent reopen, and the reason is what routes it."""
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    with pytest.raises(ValueError, match="needs a reason"):
        goal.finding(iid, "   ")


def test_take_leases_and_prints_the_reason_it_came_back(goal, capsys):
    from agent_mission import __main__ as MM
    iid = goal.propose("wire the lease", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.finding(iid, "no lease event in the fold", session_id="verifier9")

    assert MM.main(["take", "--on", "g", "--as-session", "workerB"]) == 0
    out = capsys.readouterr().out
    assert "wire the lease" in out
    assert "no lease event in the fold" in out, \
        "a worker told only 'do it again' repeats the same work"
    assert goal.load().leaves[0].leased_by == "workerB"


def test_take_says_so_when_nothing_is_agreed(goal, capsys):
    from agent_mission import __main__ as MM
    goal.propose("unagreed", by="agent")
    assert MM.main(["take", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "await" in out.lower() or "proposal" in out.lower()


# ── the display ──────────────────────────────────────────────────────────

def test_the_work_view_is_the_landing_view():
    assert "let FILTER='all', QUERY='', VIEW='work';" in B.PAGE
    for v in ("data-v=work", "data-v=inbox", "data-v=goals"):
        assert v in B.PAGE, v


def test_every_css_variable_used_is_defined():
    """Three were not — `--warn`, `--wrap` and `--fg` — so the Inbox's amber
    heading accent silently never applied and it had no max-width. A missing
    custom property is not an error: the declaration is just dropped."""
    defined = set(re.findall(r"(--[a-z]+):", B.PAGE))
    used = set(re.findall(r"var\((--[a-z]+)\)", B.PAGE))
    assert not (used - defined), sorted(used - defined)


def test_the_amber_accent_means_only_waiting_on_you():
    """This palette has NO red — `--bad` is the amber — so rework carries no
    colour at all rather than borrowing the one signal that means a person
    must act."""
    block = "#work{" + B.PAGE.split("#work{")[1].split("/* The Inbox.")[0]
    # Split on RULES, not lines: these declarations wrap, and a guard that
    # reads one physical line judges a continuation with no selector on it.
    rules = [r for r in block.split("}") if "var(--bad)" in r]
    rules = [r for r in rules if "/*" not in r.split("{")[0]]
    for r in rules:
        selector = r.split("{")[0]
        assert any(w in selector for w in ("waiting", "qheld", "warn")), \
            f"amber used outside the waiting lane: {selector.strip()}"
    assert rules, "the waiting lane should carry the accent"


# ── 83621155: the Inbox keyboard ─────────────────────────────────────────

def test_the_keys_are_gated_on_focus():
    """The page binds Enter on the write-code input and has a search box and
    a note field. A bare `a` fired while someone is typing a filter would
    accept a proposal they never looked at."""
    page = B.PAGE
    assert "function kTyping(" in page
    for tag in ("'input'", "'textarea'", "'select'", "isContentEditable"):
        assert tag in page, tag
    assert "if (kTyping(e.target)) return;" in page


def test_the_keys_only_apply_in_the_inbox():
    assert "if (VIEW !== 'inbox') return;" in B.PAGE


def test_a_modifier_chord_is_left_alone():
    """Cmd-K, Ctrl-R and friends belong to the browser, not to us."""
    assert "if (e.metaKey || e.ctrlKey || e.altKey) return;" in B.PAGE


def test_every_key_is_bound_and_announced():
    page = B.PAGE
    for k in ("'j'", "'k'", "'a'", "'c'", "'x'", "'o'"):
        assert k in page, k
    # An affordance nobody notices is not a feature.
    assert "j/k move · a accept · c confirm · x decline · o open goal" in page


def test_a_key_clicks_the_rows_own_button():
    """The buttons ARE the authority. Calling act() from a key handler would
    let a key reach a write the row does not offer — and a read-only board
    renders no buttons at all, so the keys must then do nothing."""
    page = B.PAGE
    assert "const hit = sel => { const b = row.querySelector(sel); if (b) b.click(); };" \
        in page
    kb = page[page.index("let KROW = -1;"):page.index("const onBoardClick")]
    # Comments stripped: the prose explaining why the handler does not call
    # act() names it, and a guard that matches its own rationale is the
    # FIFTH instance of that failure in this repo. The pattern is now
    # recognisable enough to reach for the strip first.
    code = "\n".join(ln for ln in kb.split("\n")
                      if not ln.strip().startswith("//"))
    assert "act(" not in code, "the keyboard must not write directly"


def test_the_rows_are_addressable():
    """kRows() selects `#inbox li[data-i]`, and data-i was on the BUTTONS
    only — so the first version would have found no rows at all."""
    assert 'data-i="${i.id}">' in B.PAGE, "the li itself needs the id"
    assert "#inbox li[data-i]" in B.PAGE


# ── ddb49e03: decline on the board ───────────────────────────────────────

def test_decline_is_offered_on_proposals_only():
    page = B.PAGE
    assert "data-do=decline" in page
    row = page[page.index("data-do=accept"):page.index("data-do=done")]
    assert "data-do=decline" in row, "it sits beside accept, on the !i.ok branch"


def test_decline_maps_to_the_existing_human_only_remove():
    """Not new authority: `remove` is already in ACTIONS and already refuses
    anything but a human. This is the button that was missing."""
    from agent_mission.actions import ACTIONS
    assert "remove" in ACTIONS
    assert "act('remove', sid, [b.dataset.i])" in B.PAGE
    from agent_mission.store import MissionStore, ProtectedFieldError
    import pytest as _p
    assert ProtectedFieldError is not None


def test_decline_confirms_and_says_the_subtree_goes_too():
    """`remove` drops the item AND its children — that is in the store, not
    something the button chose, so the sentence has to say it."""
    page = B.PAGE
    c = page[page.index("if (b.dataset.do === 'decline')"):]
    c = c[:c.index("return;")]
    assert "confirm(" in c
    assert "nested under it" in c
    assert "log keeps" in c, "it must say nothing is deleted"


def test_declining_refuses_an_agent(tmp_path, monkeypatch):
    from agent_mission.store import MissionStore, ProtectedFieldError
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    iid = st.propose("work", by="agent")["item_id"]
    with pytest.raises(ProtectedFieldError):
        st.remove(iid, by="agent")


def test_decline_is_not_the_accent():
    """The accent means "waiting on you". A decline button IS one of the
    things you are waiting to do, so colouring it says nothing new."""
    css = B.PAGE[B.PAGE.index(".act.warn{"):]
    css = css[:css.index("}") + 1]
    assert "var(--bad)" not in css


# ── the release livelock, found by dispatching for real ──────────────────

def test_rework_already_handed_back_stops_jumping_the_queue(goal):
    """"Rework first" became a LIVELOCK. An item a verifier sent back sorted
    ahead of everything; a session that took it, found it undoable and
    released it left it sorting first AGAIN — so the next session, and the
    next, each burned a turn rediscovering the same wall.

    Seen live on a64d9eca, which a worker correctly refused as needing a
    human ruling. Dispatch would have handed it to all nine idle peers.
    """
    # `other` is proposed FIRST, so tree order prefers it. Otherwise the
    # test cannot tell "stopped jumping the queue" from "was first anyway" --
    # which is what my first version of this asserted, and it failed for
    # exactly that reason.
    other = goal.propose("ordinary work", by="agent")["item_id"]
    sent_back = goal.propose("needs a ruling", by="agent")["item_id"]
    for i in (other, sent_back):
        goal.accept(i, by="human")

    goal.finding(sent_back, "cannot be done as written")
    assert goal.load().next_ready().id == sent_back, "rework goes first once"

    goal.release(sent_back, "it needs a human ruling, not code")
    m = goal.load()
    assert m.next_ready().id == other, "a handed-back item must stop blocking"
    # Still available, just not jumping the queue.
    assert sent_back in [i.id for i in m.lanes()["ready"]]


def test_the_handback_reason_is_kept(goal):
    """A handback with no reason is indistinguishable from a crashed
    session, and the next taker learns nothing."""
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    goal.release(iid, "blocked on a decision only the human can make")
    item = goal.load().leaves[0]
    assert "only the human" in item.release_note
    assert item.released_at > 0


def test_a_new_finding_outranks_an_older_handback(goal):
    """A fresh reason is a fresh reason — it should jump the queue again."""
    other = goal.propose("ordinary", by="agent")["item_id"]
    iid = goal.propose("work", by="agent")["item_id"]
    for i in (other, iid):
        goal.accept(i, by="human")
    goal.finding(iid, "first objection")
    goal.release(iid, "could not do it")
    assert goal.load().next_ready().id == other

    goal.finding(iid, "a different, newer objection")
    m = goal.load()
    assert m.next_ready().id == iid
    item = [i for i in m.leaves if i.id == iid][0]
    assert item.release_note == "", "the old handback no longer applies"


# ── dispatch: it plans, it never delivers ────────────────────────────────

def test_dispatch_delivers_nothing(goal, capsys):
    """The board has no agent socket and no token, and if it could talk on
    that bus it would be reachable FROM it — the write gate undone."""
    from agent_mission import __main__ as MM
    src = (Path(MM.__file__)).read_text()
    fn = src[src.index("def cmd_dispatch("):src.index("def _dispatch_message(")]
    # Strip the DOCSTRING as well as the `#` comments. The docstring
    # explains that the board has no agent socket, so a guard looking for
    # "socket" matched its own rationale -- the sixth time in this repo,
    # and the first where stripping comments alone was not enough.
    body = fn.split('"""')
    code = body[0] + ("".join(body[2:]) if len(body) > 2 else "")
    code = "\n".join(ln for ln in code.split("\n")
                      if not ln.strip().startswith("#"))
    for forbidden in ("SendMessage", "socket", "subprocess", "Popen",
                      "st.lease", "urlopen"):
        assert forbidden not in code, forbidden


def test_dispatch_does_not_lease(goal, capsys):
    """The peer leases under its OWN id when it runs `take`, so the lease
    records who actually has the work, not who was told about it."""
    from agent_mission import __main__ as MM
    iid = goal.propose("work", by="agent")["item_id"]
    goal.accept(iid, by="human")
    assert MM.main(["dispatch", "--on", "g"]) == 0
    assert goal.load().leaves[0].leased_by == "", "dispatch must not lease"


def test_dispatch_is_one_item_per_peer(goal, capsys):
    """Several peers racing for one item is handled by the lease, but each
    would burn a turn to find that out — a stampede with extra steps."""
    from agent_mission import __main__ as MM
    for n in range(5):
        i = goal.propose(f"work {n}", by="agent")["item_id"]
        goal.accept(i, by="human")
    MM.main(["dispatch", "--peers", "2", "--on", "g"])
    out = capsys.readouterr().out
    assert "2 item(s) for 2 peer(s)" in out
    assert "3 more ready" in out


def test_dispatch_hands_out_in_the_same_order_take_would(goal, capsys):
    """Otherwise a dispatched peer is told about one item and handed
    another."""
    from agent_mission import __main__ as MM
    a = goal.propose("first", by="agent")["item_id"]
    b = goal.propose("second", by="agent")["item_id"]
    for i in (a, b):
        goal.accept(i, by="human")
    goal.finding(b, "send the second one back")
    MM.main(["dispatch", "--on", "g"])
    assert f"item: {b}" in capsys.readouterr().out
    assert goal.load().next_ready().id == b


def test_dispatch_is_held_by_the_queue_depth_governor(goal, capsys):
    """Nudging more workers widens the queue that is already the slow step.
    The threshold is measured, not chosen: 56 pending, p90 74h."""
    from agent_mission import __main__ as MM
    for n in range(MM.DISPATCH_MAX + 2):
        goal.propose(f"unaccepted {n}", by="agent")
    work = goal.propose("real work", by="agent")["item_id"]
    goal.accept(work, by="human")
    assert MM.main(["dispatch", "--on", "g"]) == 1
    out = capsys.readouterr().out
    assert "HELD" in out and str(MM.DISPATCH_MAX) in out


def test_the_governor_threshold_is_one_number(goal):
    """The board's page carries it too; two copies is how a banner shows a
    hold the CLI does not apply."""
    from agent_mission.__main__ import DISPATCH_MAX
    assert f"const DISPATCH_MAX = {DISPATCH_MAX};" in B.PAGE


def test_the_dispatch_message_tells_the_peer_to_ask_the_board(goal, capsys):
    """It must not carry instructions for HOW to do the work — the peer asks
    the board, so the reason and the constraints come from one place."""
    from agent_mission import __main__ as MM
    iid = goal.propose("wire it", by="agent")["item_id"]
    goal.accept(iid, by="human")
    MM.main(["dispatch", "--on", "g"])
    out = capsys.readouterr().out
    # The id is named now: a bare `mission take` handed the peer whatever was
    # first in tree order, which on a goal with older open work is not the
    # item it was dispatched. The no-id form is still offered as the fallback.
    assert f"mission take {iid} --on g" in out
    assert "with no id hands you whatever is next" in out, \
        "the fallback must still be offered"
    assert "Ship the router" in out, "the goal"
    assert f"mission claims-done {iid}" in out
    assert f"mission release {iid}" in out
    assert "Do not tick anything" in out


def test_nothing_ready_says_so(goal, capsys):
    from agent_mission import __main__ as MM
    assert MM.main(["dispatch", "--on", "g"]) == 0
    assert "nothing ready" in capsys.readouterr().out


# ── density: 120 rows across 5 goals is the size a real board reaches ─────
#
# the maintainer, looking at the restarted board: "new page layout looks messy". He
# was right, and the measurement said why. 120 ready rows, 95 of them carrying
# an "agreed N days ago..." line and EVERY row carrying a second line about
# naming no file — identical prose repeated a hundred times. 93 of the 120
# belonged to three goals, and each row reprinted its goal's name in grey.
#
# A signal on 79% of the rows is not a signal. These tests pin the three
# fixes: chips instead of per-row lines, one goal heading instead of 120 goal
# labels, and the flagged rows folded behind a summary that says what it hides.

def test_the_advisories_are_chips_not_a_line_on_every_row():
    page = B.PAGE
    assert "d untouched</span>" in page and ">delegable</span>" in page
    for dead in ("qstale", "qnodeleg", "qdeleg"):
        assert dead not in page, f"dead rule {dead} left behind"


def test_the_fact_survives_as_a_tooltip():
    """A bare badge is exactly what the fact exists to prevent."""
    page = B.PAGE
    assert 'title="${esc(r.delegable_fact' in page
    assert "title=\"agreed ${r.untouched_days} days ago" in page


def test_rows_are_grouped_by_goal_and_the_label_stops_repeating():
    page = B.PAGE
    assert "function laneGroups" in page
    assert "laneRow(r,k,false)" in page, "a grouped row must not reprint the goal"
    assert "laneRow(r,k,true)" in page, "an ungrouped lane still needs it"


def test_a_single_goal_board_is_not_grouped():
    """One group is a heading claiming a structure that is not there."""
    page = B.PAGE
    assert "if (order.length < 2) return" in page


def test_what_folds_is_the_flagged_rows_never_the_live_ones():
    """Folding by SIZE would hide live work behind a triangle."""
    page = B.PAGE
    fn = page.split("function laneGroups", 1)[1].split("\nfunction ", 1)[0]
    assert "live.length ? ul(live) : ''" in fn, "live rows must render unfolded"
    assert "stale.length > FOLD_ABOVE" in fn
    assert "may already be built" in fn, "the summary must say what it hides"


def test_the_legend_explains_the_chips_once():
    """Rather than every row explaining itself."""
    page = B.PAGE
    assert "Hover a chip for the fact behind it." in page
    assert "<b>delegable</b>" in page and "<b>Nd untouched</b>" in page


# ── `take <id>`: the dispatched item must be takeable ────────────────────
#
# the maintainer: "just push two tasks ... but the talk did not trigger". Two causes,
# and this is the second. `take` returned the first ready leaf in TREE ORDER,
# so a peer handed item X ran `mission take` and got whatever was oldest — on
# `repo-quality-and-mission-care` that is a 45-day-old item, not the paper-search
# work just added. The dispatch message's own sentence ("it should hand you
# X") was false whenever X was not first.

def test_take_can_be_given_an_id(goal):
    ids = _four_lanes(goal)              # ids[2] is the ready one
    extra = goal.propose("second ready item", by="agent")["item_id"]
    goal.accept(extra, by="human")
    m = goal.load()
    assert m.next_ready().id == ids[2], "precondition: the other one is first"
    from agent_mission.__main__ import main
    assert main(["take", extra, "--on", "g", "--as-session", "w9"]) == 0
    held = next(i for i in goal.load().items if i.id == extra)
    assert held.leased_by == "w9", "the named item was not the one leased"


def test_take_refuses_an_item_it_cannot_hand_over(goal):
    """And says which reason, rather than falling back to the queue's choice."""
    ids = _four_lanes(goal)
    from agent_mission.__main__ import main
    # ids[3] is unaccepted, ids[0] is held, ids[1] is claimed
    for bad in (ids[3], ids[0], ids[1]):
        assert main(["take", bad, "--on", "g", "--as-session", "w9"]) == 1
    leased = [i.id for i in goal.load().items if i.leased_by == "w9"]
    assert not leased, "a refusal must never silently lease something else"


def test_take_with_no_id_still_uses_the_queue(goal):
    ids = _four_lanes(goal)
    from agent_mission.__main__ import main
    assert main(["take", "--on", "g", "--as-session", "w9"]) == 0
    held = next(i for i in goal.load().items if i.leased_by == "w9")
    assert held.id == ids[2]


def test_an_unknown_id_is_an_error_not_a_fallback(goal):
    _four_lanes(goal)
    from agent_mission.__main__ import main
    assert main(["take", "nosuchid", "--on", "g", "--as-session", "w9"]) == 1
    assert not [i for i in goal.load().items if i.leased_by == "w9"]


def test_the_dispatch_message_names_the_command_that_works():
    """It used to print a bare `mission take` beside "it should hand you X"."""
    from agent_mission.__main__ import _dispatch_message
    import agent_mission.store as S
    m = type("M", (), {"objective": "o", "success_criteria": [],
                       "constraints": []})()
    item = S.Item(id="abc123", text="t")
    msg = _dispatch_message("g", m, item)
    assert "mission take abc123 --on g" in msg
    assert "It should hand you" not in msg, "the false promise must be gone"


# ── the lease must name somebody ─────────────────────────────────────────

def test_a_take_with_no_session_id_is_refused_not_leased_to_unknown(goal,
                                                                    capsys):
    """Two sessions both holding "unknown" each read the other's lease as
    their own, so the lease's one guarantee was off without saying so."""
    from agent_mission import __main__ as MM
    i = goal.propose("do it", by="agent")["item_id"]
    goal.accept(i, by="human")
    assert MM.main(["take", "--on", "g"]) == 1
    out = capsys.readouterr().out
    assert "no session id" in out and i in out
    assert "--as-session" in out, "it must say how to proceed"
    assert not [x for x in goal.load().items if x.leased_by]


def test_the_missing_id_is_reported_only_when_something_could_be_taken(goal,
                                                                       capsys):
    """With nothing ready, "no session id" is true about the wrong thing."""
    from agent_mission import __main__ as MM
    goal.propose("unagreed", by="agent")
    assert MM.main(["take", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "no session id" not in out


def test_a_session_renews_its_own_lease(goal):
    """A 90-minute lease that cannot be renewed expires under a long task,
    and the item becomes takeable while the worker is still in it."""
    from agent_mission import __main__ as MM
    i = goal.propose("long job", by="agent")["item_id"]
    goal.accept(i, by="human")
    assert MM.main(["take", i, "--on", "g", "--as-session", "w1"]) == 0
    first = next(x for x in goal.load().items if x.id == i).leased_at
    assert MM.main(["take", i, "--on", "g", "--as-session", "w1"]) == 0
    again = next(x for x in goal.load().items if x.id == i)
    assert again.leased_by == "w1"
    assert again.leased_at >= first, "re-taking must renew, not fail"


def test_another_session_still_cannot_steal_a_live_lease(goal):
    from agent_mission import __main__ as MM
    i = goal.propose("long job", by="agent")["item_id"]
    goal.accept(i, by="human")
    assert MM.main(["take", i, "--on", "g", "--as-session", "w1"]) == 0
    assert MM.main(["take", i, "--on", "g", "--as-session", "w2"]) == 1
    assert next(x for x in goal.load().items if x.id == i).leased_by == "w1"
