"""C19: spawn eligibility is BOOKKEEPING, and the guard that it never judges.

The request was "let mission be the judge of which granular jobs are better
handed to a freshly spawned agent". Judging means reading the text to estimate
size, which collides with two stored non-goals. What is built instead reports
recorded facts, and these tests pin that distinction: every verdict must be
derivable from fields in the log, and the module must contain no second copy
of the path rule the claim verifier already owns.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from agent_mission import delegable as D
from agent_mission import missions as M
from agent_mission.store import LEASE_TTL, MissionStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    s = MissionStore(M.missions_root() / "g")
    s.create("s1", str(tmp_path), "Ship the router", by="human")
    return s


def _accepted(s, text):
    ev = s.propose(text, by="agent")
    s.accept(ev["item_id"], by="human")
    return ev["item_id"]


def _code(path: Path) -> str:
    """Source with comments and docstrings removed."""
    import ast
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""
    return ast.unparse(tree)


def _v(s, item_id):
    m = s.load()
    return D.verdict_for(m, next(i for i in m.items if i.id == item_id))


# ── the four signals, one test each ──────────────────────────────────────

def test_an_item_naming_a_file_is_delegable(st):
    i = _accepted(st, "fix the fold in agent_mission/store.py")
    v = _v(st, i)
    assert v.ok and v.artifact == "agent_mission/store.py"
    assert "claim it checkably" in v.fact


def test_an_item_naming_no_file_is_not(st):
    v = _v(st, _accepted(st, "make the board easier to read"))
    assert not v.ok
    assert "names no file" in v.fact
    assert "could not prove it finished" in v.fact, "the negative case must say WHY"


def test_a_subgoal_is_not_work_anybody_is_handed(st):
    parent = _accepted(st, "Routing")
    child = st.propose("fix store.py", by="agent", parent=parent)["item_id"]
    st.accept(child, by="human")
    v = _v(st, parent)
    assert not v.ok and "subgoal" in v.fact
    assert _v(st, child).ok, "the leaf under it is still delegable"


def test_an_unaccepted_proposal_is_never_delegable(st):
    i = st.propose("fix store.py", by="agent")["item_id"]
    v = _v(st, i)
    assert not v.ok and "awaiting your accept" in v.fact


def test_a_held_item_is_not_delegable_until_the_lease_lapses(st):
    i = _accepted(st, "fix store.py")
    st.lease(i, "workerA")
    assert not _v(st, i).ok
    m = st.load()
    it = next(x for x in m.items if x.id == i)
    it.leased_at -= LEASE_TTL + 1          # the lease has expired
    assert D.verdict_for(m, it).ok, "an expired lease must free the item"


def test_a_claimed_item_is_not_delegable(st):
    i = _accepted(st, "fix store.py")
    st.claim_done(i, "done: fixed (store.py)", by="agent")
    v = _v(st, i)
    assert not v.ok and "awaiting a check" in v.fact


def test_a_handed_back_item_is_not_delegable_and_the_reason_travels(st):
    """A release-with-a-reason is recorded proof an agent could not do it."""
    i = _accepted(st, "fix the fold in store.py")
    st.release(i, "needs a ruling on which text wins")
    v = _v(st, i)
    assert not v.ok
    assert "handed it back" in v.fact
    assert "needs a ruling" in v.fact, "the human's useful fact is the note"


def test_rework_alone_does_not_disqualify(st):
    """A verifier's finding is the work continuing, not proof of a wall."""
    i = _accepted(st, "fix the fold in store.py")
    st.finding(i, "the fold still swallows a KeyError", session_id="verifier1")
    assert _v(st, i).ok


# ── it must not judge, and must not own a second path rule ───────────────

def test_the_verdict_never_reads_the_text_for_size(st):
    """Two items of wildly different size, same verdict: the fact is the file."""
    small = _accepted(st, "fix one typo in README.md")
    huge = _accepted(st, "rewrite the entire storage engine in README.md")
    assert _v(st, small).ok and _v(st, huge).ok
    assert _v(st, small).fact == _v(st, huge).fact


def test_there_is_exactly_one_path_rule(st):
    """The failure this codebase keeps hitting is two copies of one rule."""
    src = (ROOT / "agent_mission" / "delegable.py").read_text()
    assert "names_an_artifact" in src
    assert "_PATHY" not in src, "a second copy of the path rule"
    assert "re.compile" not in src, "it is deriving path-shaped itself"


def test_no_model_and_no_network(st):
    src = (ROOT / "agent_mission" / "delegable.py").read_text()
    for banned in ("import requests", "urllib", "subprocess", "anthropic"):
        assert banned not in src


def test_the_verdict_writes_nothing(st):
    """Eligibility is a read. Nothing about asking may change the log."""
    i = _accepted(st, "fix store.py")
    log = st.root / "events.jsonl"
    before = log.read_bytes()
    D.eligible(st.load())
    _v(st, i)
    assert log.read_bytes() == before


# ── order is borrowed from next_ready, never recomputed ──────────────────

def test_eligible_lists_in_the_order_take_hands_items_out(st):
    a = _accepted(st, "first, in store.py")
    b = _accepted(st, "second, in board.py")
    st.finding(b, "sent back", session_id="verifier1")   # fresh rework sorts up
    m = st.load()
    assert [i.id for i, _ in D.eligible(m)] == [b, a]
    assert m.next_ready().id == b, "dispatch and take must agree"


# ── the surfaces print it ────────────────────────────────────────────────

def _cli(*args, home):
    return subprocess.run([sys.executable, "-m", "agent_mission", *args],
                          capture_output=True, text=True, cwd=ROOT,
                          env={"PATH": "/usr/bin:/bin",
                               "AGENT_MISSION_HOME": str(home)})


def test_queue_prints_the_verdict_on_every_ready_row(st, tmp_path):
    _accepted(st, "fix the fold in store.py")
    _accepted(st, "make the board easier to read")
    out = _cli("queue", "--on", "g", home=tmp_path).stdout
    assert "→ delegable" in out
    assert "· not delegable" in out
    assert "names no file" in out


def test_dispatch_prints_the_verdict_for_each_pick(st, tmp_path):
    _accepted(st, "fix the fold in store.py")
    out = _cli("dispatch", "--on", "g", home=tmp_path).stdout
    assert "delegable:" in out


def test_dispatch_delegable_says_what_it_skipped(st, tmp_path):
    _accepted(st, "make the board easier to read")      # not delegable, first
    _accepted(st, "fix the fold in store.py")           # delegable
    out = _cli("dispatch", "--on", "g", "--delegable", home=tmp_path).stdout
    assert "skipping 1 ready item" in out
    assert "may hand a peer one of them instead" in out, \
        "filtering breaks the plan's own promise and must say so"


def test_dispatch_delegable_with_nothing_eligible_points_at_sharpen(st,
                                                                   tmp_path):
    _accepted(st, "make the board easier to read")
    out = _cli("dispatch", "--on", "g", "--delegable", home=tmp_path).stdout
    assert "nothing delegable among 1 ready item" in out
    assert "mission sharpen" in out


def test_the_board_carries_the_fact_not_just_the_flag(st, monkeypatch):
    from agent_mission import board as B
    monkeypatch.setattr(B, "live", lambda: [])
    rows = B._queue_rows(st.load(), "g")
    _accepted(st, "make the board easier to read")
    rows = B._queue_rows(st.load(), "g")
    r = rows["ready"][0]
    assert r["delegable"] is False
    assert "names no file" in r["delegable_fact"]


def test_the_page_carries_the_reason_even_as_a_chip(st):
    """The verdict was a LINE on every row and is a chip with a tooltip now.

    The line was correct and unreadable: 120 ready rows each repeating the
    same sentence about naming no file. What this still guards is that the
    FACT travels with the flag — a bare badge is the thing the fact exists to
    prevent — and that the chip did not quietly acquire the accent.
    """
    js = (ROOT / "agent_mission" / "board.py").read_text()
    assert "delegable_fact" in js, "the page shows the flag without the reason"
    assert 'title="${esc(r.delegable_fact' in js, \
        "the reason must still reach the reader somewhere"
    # One accent, one meaning: amber is "waiting on you" and this is not it.
    rule = js.split("#work .qchip{", 1)[1].split("}", 1)[0]
    assert "--bad" not in rule


# ── the narrowing, pinned by the tokens that forced it ───────────────────
#
# These three came off the LIVE board on 2026-10-08, where the loose
# path-shaped rule called every one of them an artifact. They are kept as a
# fixture because each is a different way for prose to look like a path: a
# dotted attribute, two keyboard keys, and a slash-separated list.

@pytest.mark.parametrize("prose,token", [
    ("C19-2 working_on <item>: a new event on Item.sessions", "Item.sessions"),
    ("C19-1 Inbox view with j/k navigation across goals", "j/k"),
    ("mark an item delegable when accepted/unleased/not-done", "accepted/unleased/not-done"),
])
def test_prose_that_looks_like_a_path_is_not_an_artifact(st, prose, token):
    from agent_mission.sharpen import names_an_artifact
    assert names_an_artifact(prose) == token, "the loose rule used to match"
    assert names_an_artifact(prose, strict=True) == "", "strict must not"
    v = _v(st, _accepted(st, prose))
    assert not v.ok and "names no file" in v.fact


@pytest.mark.parametrize("tok", [
    "agent_mission/board.py", "board.py", "tests/", "events.jsonl",
    "docs/DESIGN.md", "cowork-projects/",
])
def test_real_artifacts_still_pass(tok):
    from agent_mission.claims import looks_like_a_file
    assert looks_like_a_file(tok)


def test_a_file_yet_to_exist_is_still_delegable(st):
    """Eligibility must not require the artifact to exist — most items make it."""
    v = _v(st, _accepted(st, "add tests/test_nothing_here_yet.py"))
    assert v.ok and v.artifact == "tests/test_nothing_here_yet.py"


def test_the_strict_rule_lives_in_one_place(st):
    """Two tolerances, one definition. A second copy is the recurring bug."""
    # Comments and docstrings stripped first: a guard that matches its own
    # explanation is the failure this suite has hit six times.
    src = _code(ROOT / "agent_mission" / "delegable.py")
    assert "looks_like_a_file" not in src, \
        "delegable must ask sharpen, which asks claims"
    sh = (ROOT / "agent_mission" / "sharpen.py").read_text()
    assert sh.count("def names_an_artifact") == 1


def test_c18_itself_stays_loose(st):
    """Narrowing C19 must not silently change which criteria C18 sharpens."""
    from agent_mission import sharpen as S
    st.set_protected("success_criteria", ["the plan lives on Item.sessions"],
                     by="human", typed_by="human")
    assert S.sharpenings(st.load()) == [], \
        "C18's tolerance changed; a template is advice, not an assertion"
