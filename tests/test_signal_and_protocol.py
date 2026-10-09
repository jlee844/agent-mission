"""C14: the edge-triggered attention signal. C15: the protocol and auto-board.

The finding both answer: injection is not behavior, and a board behind the
terminal is a board nobody watches. Proposals sat 15.9h with the accept
buttons off and no signal anywhere.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    d = M.missions_root() / "goal-a"
    st = MissionStore(d)
    st.create("goal-a", "/repo", "the goal", by="human", typed_by="human")
    return st


def _lines(sid="conv-1"):
    from agent_mission import signal as S
    return S.check(sid)


def test_the_signal_fires_only_on_a_rise(corpus):
    assert _lines() == [], "a quiet corpus says nothing"

    corpus.propose("add invite tokens", by="agent")
    out = _lines()
    assert len(out) == 1 and "invite tokens" in out[0]
    assert "accept" in out[0] and "--on goal-a" in out[0], \
        "the line carries the way to act on it"

    assert _lines() == [], "a standing count repeated is wallpaper (C12d)"
    assert _lines() == [], "still standing, still silent"


def test_a_decline_does_not_refire_and_the_next_rise_does(corpus):
    ev = corpus.propose("one", by="agent")
    _lines()                                        # seen
    corpus.remove(ev["item_id"], by="human")        # declined
    assert _lines() == [], "shrink is silence, not a signal"
    corpus.propose("two", by="agent")
    out = _lines()
    assert len(out) == 1 and "two" in out[0], \
        "the floor moved down with the decline, so the next rise fires"


def test_each_conversation_gets_its_own_edge(corpus):
    corpus.propose("shared", by="agent")
    assert len(_lines("conv-1")) == 1
    assert len(_lines("conv-2")) == 1, \
        "a second session has not seen it yet — state is per conversation"
    assert _lines("conv-1") == []


def test_several_new_proposals_are_one_line_not_a_scroll(corpus):
    for n in range(4):
        corpus.propose(f"item {n}", by="agent")
    out = _lines()
    assert len(out) == 1 and "+3 more" in out[0]


def test_an_archived_mission_never_signals(corpus, tmp_path):
    corpus.propose("late idea", by="agent")
    corpus.archive(by="human")
    assert _lines() == [], "archiving is a statement about attention"


def test_notification_path_is_absent_unless_opted_in(corpus, monkeypatch):
    from agent_mission import signal as S
    ran = []
    monkeypatch.setattr(S.subprocess, "run",
                        lambda *a, **k: ran.append(a) or None)
    assert S.notify(["a line"], "http://x") is False
    assert ran == [], "off by default means nothing runs, not a silent run"

    (S._home() / "notify-optin").write_text("on")
    assert S.notify(["a line"], "http://x") is True
    assert len(ran) == 1
    assert S.notify(["another"], "http://x") is False, \
        "rate limited: one per 10 minutes however many edges fire"


def test_signal_command_never_fails(tmp_path, monkeypatch, capsys):
    """A hook that can break a prompt is a hook that gets uninstalled."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "nowhere"))
    assert main(["signal"]) == 0
    assert capsys.readouterr().out == "", "no missions, no output, no error"


def test_the_protocol_is_in_the_command_file_and_the_hook_payload(
        tmp_path, monkeypatch, capsys):
    """C15a. String-guarded like the README tests: the protocol exists only if
    an agent actually receives it, and both delivery paths must carry it."""
    md = (ROOT / "commands" / "mission.md").read_text()
    for phrase in ("Never work untracked silently", "push back once",
                   "plan's next", "One pushback, then comply"):
        assert phrase in md, f"mission.md lost the protocol phrase {phrase!r}"

    from agent_mission import missions as M
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("g", "/repo", "the goal", by="human", typed_by="human")
    main(["whereami", "--full", "--on", "g"])
    out = capsys.readouterr().out
    assert "PROTOCOL:" in out and "never work untracked" in out
    assert "push back once" in out


def test_auto_board_appends_once_and_backs_up(tmp_path, monkeypatch):
    """C15b. Idempotent, diff-shown, backed up — the setup tier rule, applied
    to a file this tool does not own."""
    from agent_mission import setup_surfaces as S
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    rc = tmp_path / "zshrc"
    rc.write_text("export EDITOR=vim\n")
    monkeypatch.setenv("AGENT_MISSION_RC", str(rc))

    # A machine with no ~/.claude/settings.json -- CI, and any fresh install.
    # This surface never touches settings, so their absence must not block it;
    # it did, because the plan() branch sat below the cannot-read guard, and
    # the developer machine could not reproduce it.
    out = S.install("auto-board", settings=str(tmp_path / "no-such.json"))
    assert out["applied"] and out["backup"], out["why"]
    text = rc.read_text()
    assert text.startswith("export EDITOR=vim\n"), "the original is intact"
    assert "mission board --rc &" in text

    again = S.install("auto-board", settings=str(tmp_path / "no-such.json"))
    assert again["applied"] == [] and again["why"] == "already current"
    assert rc.read_text() == text, "running it twice changes nothing"


def test_auto_board_is_never_installed_by_default(tmp_path, monkeypatch,
                                                  capsys):
    """A default `mission setup` must not start servers from the shell rc or
    turn on notifications. Both arrive only by their flag."""
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    rc = tmp_path / "zshrc"
    rc.write_text("# mine\n")
    monkeypatch.setenv("AGENT_MISSION_RC", str(rc))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))

    main(["setup", "--settings", str(settings), "--dest", str(tmp_path / "c")])
    capsys.readouterr()
    assert rc.read_text() == "# mine\n"
    assert not (tmp_path / "home" / "notify-optin").exists()

    main(["setup", "--auto-board", "--notify",
          "--settings", str(settings), "--dest", str(tmp_path / "c")])
    assert "mission board --rc &" in rc.read_text()
    assert (tmp_path / "home" / "notify-optin").exists()


def test_the_attention_hook_is_a_default_surface(tmp_path, monkeypatch,
                                                 capsys):
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}))

    main(["setup", "--settings", str(settings), "--dest", str(tmp_path / "c")])
    capsys.readouterr()
    data = J.loads(settings.read_text())
    ups = J.dumps(data["hooks"]["UserPromptSubmit"])
    assert "mission signal" in ups
    assert "echo mine" in ups, "appended, never replaced"


def test_board_at_a_tty_serves_writable_not_detached(monkeypatch, tmp_path):
    """Plain `mission board` handed a person to ensure(), which detaches the
    server with stdout in a log file -- isatty False, read-only, no code. So
    every path a human was TOLD to use produced a board with no buttons, and
    the write code existed only behind --foreground, which no doc mentioned.
    Followed twice, betrayed twice, on the real machine."""
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(MM, "board_running", lambda: None)
    monkeypatch.setattr(MM, "ensure_board",
                        lambda port: calls.append(("ensure", port)) or "url")
    import agent_mission.board as B
    monkeypatch.setattr(B, "serve",
                        lambda port, writable=None: calls.append(("serve", port)))
    monkeypatch.setattr(MM.sys.stdout, "isatty", lambda: True)

    MM.main(["board"])
    assert calls == [("serve", 8976)], \
        "a person at a keyboard gets the in-terminal, writable board"


def test_board_at_a_tty_replaces_a_read_only_board(monkeypatch, tmp_path,
                                                   capsys):
    """The board that is up cannot be upgraded in place: its code must print
    to the terminal the person is sitting at. So a read-only (or too-old-to-
    say) board is stopped and replaced, and a writable one is pointed at."""
    from agent_mission import __main__ as MM
    import agent_mission.board as B
    import agent_mission.daemon as D
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(MM, "board_running", lambda: {"port": 8976, "pid": 1})
    monkeypatch.setattr(MM, "board_stop", lambda: calls.append("stop") or True)
    monkeypatch.setattr(B, "serve",
                        lambda port, writable=None: calls.append("serve"))
    monkeypatch.setattr(MM.sys.stdout, "isatty", lambda: True)

    freed = []
    monkeypatch.setattr(D, "_free",
                        lambda port: bool(freed) or freed.append(1) or False)
    monkeypatch.setattr(D, "identify", lambda port: {"writes": False})
    MM.main(["board"])
    assert calls == ["stop", "serve"], "read-only is replaced, not reused"
    assert freed, ("the bind waits for the old board to actually die -- "
                   "signalling it and binding in the same instant lost the "
                   "race on the first real run (Errno 48)")

    calls.clear()
    monkeypatch.setattr(D, "identify", lambda port: {"writes": True})
    MM.main(["board"])
    assert calls == [], "a writable board is pointed at, never restarted"
    assert "writable" in capsys.readouterr().out


def test_board_not_at_a_tty_still_detaches_read_only(monkeypatch, tmp_path,
                                                     capsys):
    """The path `mission init` and agents use is unchanged -- and its output
    now says the board it started has no buttons, instead of implying done."""
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(MM, "board_running", lambda: None)
    monkeypatch.setattr(MM, "ensure_board", lambda port: "http://127.0.0.1:8976")
    monkeypatch.setattr(MM.sys.stdout, "isatty", lambda: False)

    MM.main(["board"])
    out = capsys.readouterr().out
    assert "read-only from here" in out and "your own terminal" in out


def test_board_actions_reach_a_migrated_mission(tmp_path, monkeypatch):
    """The board addresses cards by mission id (missions/<id>/), and act()
    resolved only the pre-inversion session layout -- so with the write code
    finally working, the first Accept ever clicked answered "no mission".
    Third member of one family: doctor audited the abandoned twins, choices()
    forgot the legacy stores, and apply() forgot the migrated ones."""
    from agent_mission import actions, missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "docs-site")
    st.create("docs-site", "/repo", "the goal", by="human", typed_by="human")
    pid = st.propose("an idea", by="agent")["item_id"]

    sess = actions.Session(enabled=True)
    out = actions.apply(sess, sess.code, "accept", "docs-site", ids=[pid])
    assert out["ok"] and pid in out["ids"], out
    assert st.load().leaves[0].accepted

    out = actions.apply(sess, sess.code, "done", "docs-site", ids=[pid])
    assert out["ok"]
    assert st.load().leaves[0].done


def test_group_hues_are_deterministic_and_inherited():
    """Colour is the GROUP: a child carries its top-level subgoal's hue, the
    keyword table gives the usual domains the same hue on every mission, and
    the fallback is a hash -- stable across processes, honest about being
    arbitrary. A substring table is not a classifier: no scores, nothing
    learned (the F34 law)."""
    from agent_mission.board import DOMAIN_HUES, _hue
    assert _hue("Backend API for lists") == 155
    assert _hue("Frontend board display") == 210
    assert _hue("Ship to the App Store") == 30
    assert _hue("xyzzy plugh") == _hue("xyzzy plugh"), "hash path is stable"
    hues = [h for h, _ in DOMAIN_HUES]
    assert len(hues) == len(set(hues)), "two domains sharing a hue is a bug"


def test_tree_rows_carry_the_parents_hue(tmp_path, monkeypatch):
    from agent_mission.board import _tree
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "goal", by="human", typed_by="human")
    top = st.propose("Backend work", by="human")["item_id"]
    st.accept(top, by="human")
    kid = st.propose("polish the docs for it", parent=top, by="human")["item_id"]
    st.accept(kid, by="human")

    rows = _tree(st.load())
    parent = next(r for r in rows if r["t"] == "Backend work")
    child = next(r for r in rows if "polish" in r["t"])
    assert parent["hue"] == 155
    assert child["hue"] == 155, ("the child says 'docs' but the colour is the "
                                 "group, so it inherits the subgoal's hue")


def test_the_page_recovers_from_a_board_restart_by_itself():
    """The board restarts under the page routinely -- every upgrade, every
    switch to writable. The old catch silently returned, leaving a page that
    looked alive and was three restarts stale. String-guard the recovery the
    same way the JS syntax test works: the page must show the state and keep
    polling, and remove the banner when the board answers again."""
    from agent_mission.board import PAGE
    assert "reconn" in PAGE and "retrying" in PAGE
    assert "gone.remove()" in PAGE


def test_a_branch_proposal_can_be_accepted_from_the_page():
    """The docs-site dead end: one proposal with six children. accept-all
    accepted the children; the PARENT was a branch row, branches had no
    accept button, and the waiting list filtered branches out -- so the card
    said "1 awaiting accept" forever above a button that collected zero
    targets and silently returned. String-guarded on the page source."""
    from agent_mission.board import PAGE
    # The accept button is gated on !i.done only; tick stays leaf-only.
    assert "WRITABLE && CODE() && !i.done)" in PAGE
    assert "ask=v.filter(i=>!i.ok" in PAGE, "branches show in waiting-on-you"
    assert "found nothing to accept" in PAGE, "and the empty case says so"


def test_the_page_carries_the_report_loop():
    """A click that does nothing leaves the person with no thread to pull:
    the evidence is in a console they never open. The page keeps its last 20
    failures, toasts the newest, and the report button packages the lot for
    pasting to Claude -- clipboard, not an endpoint, so it stays a person
    handing evidence to their own agent."""
    from agent_mission.board import PAGE
    for needle in ("note_err", "unhandledrejection", "build_report",
                   "PROBLEM REPORT", "paste this whole block"):
        assert needle in PAGE, f"report loop lost {needle!r}"
    assert "ERRS.length>20" in PAGE, "the ring is bounded"


def test_the_page_does_not_yank_scroll_on_every_poll():
    """The 4s tick rebuilt the cards' HTML unconditionally, and rebuilding
    destroys the scroll position of every scrollable plan block -- a person
    reading a long tree was pulled back to the top mid-scroll, every 4
    seconds. The render is skipped when nothing changed, and when something
    did, each scrolled block is put back where it was."""
    from agent_mission.board import PAGE
    assert "html === LAST_HTML" in PAGE, "unchanged polls must not re-render"
    assert "scrollTop" in PAGE, "changed polls restore the scroll"


def test_the_page_does_not_rebuild_under_an_active_scroll():
    """The unchanged-skip almost never fires on a LIVE session -- the call
    counters move nearly every poll -- so the block was still replaced under
    the reader's finger, killing the gesture mid-scroll (field report, twice).
    The render must yield to recent interaction: any scroll/touch on the grid
    defers the rebuild to a later, quiet tick."""
    from agent_mission.board import PAGE
    assert "LAST_TOUCH" in PAGE, "interaction guard missing"
    # The guard must run BEFORE the rebuild commits (LAST_HTML assignment),
    # otherwise a deferred render is skipped forever as "unchanged".
    guard = PAGE.index("Date.now() - LAST_TOUCH")
    commit = PAGE.index("LAST_HTML = html")
    assert guard < commit, "guard must defer before LAST_HTML commits"
    # `scroll` does not bubble; without capture the guard never hears it.
    assert "'scroll'" in PAGE and "capture:true" in PAGE.replace(" ", ""), \
        "scroll must be captured on the grid"


def test_your_own_click_repaints_immediately():
    """The scroll guard deferred renders for a few seconds after touching the
    grid -- and `pointerdown` was in its event list, so every button press
    armed the quiet window a microsecond before the write it triggered. The
    tick fired inside its own deferral and the row sat there: the board
    looked broken on exactly the interaction being watched."""
    from agent_mission.board import PAGE
    assert "tick(true)" in PAGE, "a write forces its own repaint"
    assert "!force && Date.now() - LAST_TOUCH" in PAGE, \
        "and the deferral yields to force"
    assert "html === LAST_HTML && !force" in PAGE, \
        "the unchanged-skip must yield to force too, or the forced paint is eaten"
    # Click precursors are not scrolling. Only real scroll events may defer.
    guard = PAGE[PAGE.index("for (const ev of ["):][:120]
    assert "pointerdown" not in guard and "touchstart" not in guard, \
        "click precursors must not arm the scroll deferral"


def test_a_write_does_not_rescan_every_transcript(corpus, monkeypatch):
    """invalidate() ran a full snapshot() INSIDE the POST handler, moving the
    ~4s corpus scan this cache exists to avoid onto the one request a person
    waits on. A write changes the event log, never a transcript's tool-call
    count, so only the written mission is re-folded."""
    from agent_mission import board as B

    calls = []
    monkeypatch.setattr(B, "snapshot", lambda: calls.append(1) or [])
    # The background refresh is stubbed so this asserts what the CALLER did,
    # not who won a race with it.
    monkeypatch.setattr(B._Cache, "_refresh_async", lambda self: None)
    c = B._Cache()
    stale = {"id": "goal-a", "title": "old", "calls": 999, "done": 0, "total": 0}

    c.rows = [dict(stale)]
    c.invalidate("nope")
    assert calls, "an unknown mission still rebuilds fully — never serve wrong"

    ev = corpus.propose("ship it", by="agent")
    corpus.accept(ev["item_id"], by="human")
    calls.clear()
    c.rows = [dict(stale)]
    c.invalidate("goal-a")
    assert calls == [], "a write must not rescan transcripts on the request path"
    assert c.rows[0]["total"] == 1, "the written mission is re-folded from disk"
    assert c.rows[0]["calls"] == 999, "transcript activity is reused, not rescanned"


# ---- C19b.1: the Inbox -----------------------------------------------------
# The rows are NOT new -- every card already rendered its own proposals,
# claims-done suggestions and findings, with these buttons. What did not exist
# is the AGGREGATION, so "is anything waiting on me" cost one glance per goal
# and a proposal on a goal you were not looking at sat 15.9h.

def test_the_board_opens_on_what_happens_next():
    """Superseded: the Inbox was the landing view until the work lanes
    existed. The Inbox answers "what is waiting on me"; `work` answers
    "what happens next and by whom", which is the larger question once
    sessions route work between themselves — and the waiting lane inside it
    is the Inbox's list, so neither claim was lost.
    """
    from agent_mission.board import PAGE
    assert "let FILTER='all', QUERY='', VIEW='work'" in PAGE, \
        "the board opens on the work queue, not on a status page"
    for v in ("data-v=work", "data-v=inbox", "data-v=goals"):
        assert v in PAGE, v


def test_the_inbox_sorts_by_what_the_data_actually_supports():
    """The Inbox sorts by GOAL recency and its heading says so.

    It was written that way because `Item` carried no timestamp, and this
    test's first version asserted that absence so the decision would be
    revisited if it changed. **It changed**: `accepted_at` was added for the
    claim-freshness check, and this guard fired on the commit that added it,
    which is exactly what it was for.

    The sort is deliberately NOT changed here. `accepted_at` is when a thing
    was AGREED, not when it started waiting for a reply, and the Inbox's
    stated question is "what is waiting on me" -- for an unaccepted proposal,
    the field is 0.0 by definition, i.e. absent for the rows the Inbox is
    mostly made of. So per-item age is now possible for ACCEPTED rows only,
    which is a different list; the pre-registered p90 measurement decides
    whether it is worth having, not this test.
    """
    from agent_mission.board import PAGE
    from agent_mission.store import Item
    assert "accepted_at" in Item.__dataclass_fields__, \
        "the field this note is about"
    assert Item.__dataclass_fields__["accepted_at"].default == 0.0, \
        "0.0 on an unaccepted row is why it cannot order the Inbox today"
    assert "(b.s.mtime || 0) - (a.s.mtime || 0)" in PAGE, "sorted by goal recency"
    assert "most recently active first" in PAGE, "and the heading says which"
    # Comments are stripped before this check: the prose explaining WHY not to
    # claim "oldest first" contains the phrase, and a guard that matches its
    # own rationale is the self-matching guard this repo keeps re-inventing.
    shown = "\n".join(l for l in PAGE.splitlines()
                      if not l.strip().startswith("//"))
    assert "oldest first" not in shown, "never claim an order the data cannot give"


def test_the_inbox_has_no_filter_that_does_nothing():
    """A clause referencing a field the payload does not carry READS like a
    filter and is a no-op — this codebase has shipped that twice."""
    from agent_mission.board import PAGE
    assert "ended_archived" not in PAGE, "that field never existed"
    assert "mission_rows() already drops an" in PAGE, \
        "say WHY there is no archived filter here, or someone re-adds a fake one"


def test_one_click_handler_serves_both_surfaces():
    """The Inbox renders the same rows with the same buttons; a second copy is
    a second thing to keep correct."""
    from agent_mission.board import PAGE
    assert PAGE.count("const onBoardClick") == 1
    assert "getElementById('g').addEventListener('click', onBoardClick)" in PAGE
    assert "getElementById('inbox').addEventListener('click', onBoardClick)" in PAGE


def test_bulk_accept_survives_into_the_inbox():
    """One `sharpen --propose` pass can file thirty rows. Thirty keystrokes to
    clear them is the C12d wallpaper failure in a new shape."""
    from agent_mission.board import PAGE
    assert "accept all ${nprop}" in PAGE
    assert "b.closest('.card') || b.closest('.gl').nextElementSibling" in PAGE, \
        "in the Inbox rows sit under a heading, not inside a .card"


def test_the_accent_appears_once_not_per_row():
    """Everything in this view is waiting on you, so painting every row amber
    would make the accent mean 'a row' instead of 'waiting on you'.

    ⚠️ This test PASSED for a version that did not work. It asserted
    `var(--warn)` appeared exactly once — and `--warn` was never defined
    anywhere in the stylesheet, so the declaration was dropped and the
    heading was never amber at all. An undefined custom property is not a
    CSS error; it is silence. Counting a token is not the same as checking a
    colour renders, and the name is now one the palette actually declares
    (`--bad`, which IS this palette's amber).

    Fixing the name then showed the count was wrong too: the block also
    carries `#inbox li.prop .box{color:var(--bad)}`, which the old assertion
    never saw because it was counting the WRONG TOKEN. So the accent was on
    rows all along.

    That one is fine — a proposal glyph IS "waiting on you", the same
    meaning, not a second one. Which is the real law: not "appears once" but
    "only ever means one thing". A count was the wrong guard, and it is what
    let a broken variable name ship.
    """
    from agent_mission.board import PAGE
    css = PAGE[PAGE.index("#inbox{"):PAGE.index(".chips{")]
    rules = [r for r in css.split("}") if "var(--bad)" in r]
    assert rules, "the accent should be here somewhere"
    for r in rules:
        selector = r.split("{")[0]
        assert any(w in selector for w in ("h2 b", "prop")), \
            f"amber on something that is not waiting on you: {selector.strip()}"
    assert "--bad:" in PAGE, "the accent has to be a variable that exists"


# ── talking back: a dispatched session's result must reach the dispatcher ──
#
# the maintainer: "can you make sure they talk back after done? notify the session
# so that they will look at the result". The claim path already crossed goals
# — `check` iterates every mission, so a peer's CLAIM reached every session at
# its next prompt. What produced silence was the opposite outcome: a peer that
# took an item, found it could not be done and released it with a reason just
# put the row back in the ready lane, and whoever handed it over learned
# nothing. That is the reply you most need, because it is the one that needs a
# ruling.

def _goal(tmp_path, monkeypatch, name="g"):
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / name)
    st.create("s1", str(tmp_path), "Ship the router", by="human")
    return st


def _accepted(st, text="do the thing"):
    i = st.propose(text, by="agent")["item_id"]
    st.accept(i, by="human")
    return i


def test_a_handback_reaches_the_dispatcher(tmp_path, monkeypatch):
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    i = _accepted(st)
    S.check("watcher")                              # baseline: silence after
    assert S.check("watcher") == []
    st.lease(i, "peer9")
    st.release(i, "needs a ruling on which text wins")
    lines = S.check("watcher")
    assert any("handed BACK" in l for l in lines), lines
    assert any("needs a ruling" in l for l in lines), "the REASON must travel"
    assert any(i in l for l in lines), "and the id, or you cannot act on it"


def test_a_handback_announces_once(tmp_path, monkeypatch):
    """Edge-triggered like everything else here, or it becomes wallpaper."""
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    i = _accepted(st)
    S.check("watcher")
    st.release(i, "cannot")
    assert S.check("watcher"), "the first look must report it"
    assert S.check("watcher") == [], "the second must be silent"


def test_a_finding_reaches_the_dispatcher_too(tmp_path, monkeypatch):
    """'I could not' and 'this is wrong' are different replies."""
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    i = _accepted(st)
    S.check("watcher")
    st.finding(i, "the fold still swallows a KeyError", session_id="verifier1")
    lines = S.check("watcher")
    assert any("sent back by a check" in l for l in lines), lines
    assert any("swallows a KeyError" in l for l in lines)


def test_a_claim_now_says_who_made_it(tmp_path, monkeypatch):
    """A dispatcher cannot tell its peer's reply from its own old claim."""
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    i = _accepted(st)
    S.check("watcher")
    st.claim_done(i, "done: built it (x.py)", by="agent", session_id="peer9")
    lines = S.check("watcher")
    assert any("claimed finished by peer9" in l for l in lines), lines


def test_a_ticked_item_stops_announcing_its_handback(tmp_path, monkeypatch):
    """Once the human rules on it, it is no longer a reply awaiting anyone."""
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    i = _accepted(st)
    st.release(i, "cannot")
    S.check("watcher")
    st.complete(i, by="human")
    i2 = _accepted(st, "another")
    st.release(i2, "also cannot")
    lines = S.check("watcher")
    assert any(i2 in l for l in lines)
    assert not any(i in l and "handed BACK" in l for l in lines)


def test_the_dispatch_message_can_ask_for_a_direct_reply(tmp_path, monkeypatch):
    """Two channels, because they fail differently: late vs unreliable."""
    from agent_mission.__main__ import _dispatch_message
    from agent_mission.store import Item
    m = type("M", (), {"objective": "o", "success_criteria": [],
                       "constraints": []})()
    item = Item(id="abc123", text="t")
    assert "also message" not in _dispatch_message("g", m, item)
    msg = _dispatch_message("g", m, item, reply_to="Mission layer")
    assert "also message Mission layer" in msg
    assert "which id and what you concluded" in msg


def test_two_handbacks_in_one_gap_are_both_reported(tmp_path, monkeypatch):
    """"+N more" is late-vs-NEVER here, not decoration.

    The edge records every id it saw, so an item left out of the line is
    marked seen and can never be announced again. Found live: two items
    released in one turn, one reported, the other lost.
    """
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    a, b = _accepted(st, "first"), _accepted(st, "second")
    S.check("watcher")
    st.release(a, "reason A")
    st.release(b, "reason B")
    out = S.check("watcher")
    assert len(out) == 1, "one line per goal, not a scroll"
    assert "+1 more" in out[0], f"the second handback vanished: {out}"
    assert S.check("watcher") == []


def test_two_findings_in_one_gap_are_both_reported(tmp_path, monkeypatch):
    from agent_mission import signal as S
    st = _goal(tmp_path, monkeypatch)
    a, b = _accepted(st, "first"), _accepted(st, "second")
    S.check("watcher")
    st.finding(a, "wrong A", session_id="v1")
    st.finding(b, "wrong B", session_id="v1")
    out = S.check("watcher")
    assert len(out) == 1 and "+1 more" in out[0], out
