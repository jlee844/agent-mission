"""Agreed long ago and never touched: the hole the four lanes left.

The four lanes only ever ask the human to act on something an AGENT started --
a proposal to accept, a claim to confirm, a finding to read. Work that was
FINISHED and never claimed asks nothing of anybody: it stays in the ready
lane, which is also the dispatch queue, so it is offered to peers forever.

Measured on the live board on 2026-10-08: five items had been agreed 43-45
days with no lease, no claim, no release. Every one was already built, and
`dispatch` was about to hand a peer `Build C17: claims-done`, which shipped
weeks earlier. These tests pin the signal and keep it measured.
"""
import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_mission import board as B
from agent_mission import missions as M
from agent_mission.store import LEASE_TTL, UNTOUCHED_DAYS, MissionStore

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400.0


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    s = MissionStore(M.missions_root() / "g")
    s.create("s1", str(tmp_path), "Ship the router", by="human")
    return s


def _old(s, text, days=45.0):
    """An accepted item whose accept timestamp is `days` in the past.

    The back-dating goes into `m.checklist`, NOT onto an `Item` from
    `m.items` -- that property rebuilds `Item(**d)` on every access, so a
    mutated Item is a throwaway and anything re-deriving (`untouched`,
    `lanes`) never sees it. The first version of this fixture did exactly
    that and the test failed for a reason that had nothing to do with the
    code under test.
    """
    i = s.propose(text, by="agent")["item_id"]
    s.accept(i, by="human")
    m = s.load()
    d = next(x for x in m.checklist if x["id"] == i)
    d["accepted_at"] -= days * DAY
    return i, m, d


# ── the signal ───────────────────────────────────────────────────────────

def test_an_item_agreed_long_ago_and_never_touched_is_flagged(st):
    i, m, _ = _old(st, "Build the thing")
    assert [x.id for x in m.untouched()] == [i]
    it = next(x for x in m.items if x.id == i)
    assert 44 < m.days_since_accepted(it) < 46


def test_an_item_agreed_today_is_not(st):
    _old(st, "Build the thing", days=0.0)
    assert st.load().untouched() == []


@pytest.mark.parametrize("touch", ["lease", "claim", "release", "finding"])
def test_any_single_touch_clears_the_flag(st, touch):
    """The claim is "nobody has touched this", so one touch ends it."""
    i, _, _ = _old(st, "Build the thing")
    {"lease": lambda: st.lease(i, "workerA"),
     "claim": lambda: st.claim_done(i, "done: built (x.py)", by="agent"),
     "release": lambda: st.release(i, "cannot do it"),
     "finding": lambda: st.finding(i, "not finished", session_id="v1")}[touch]()
    m = st.load()
    next(x for x in m.checklist if x["id"] == i)["accepted_at"] -= 45 * DAY
    assert i not in [x.id for x in m.untouched()], \
        f"a {touch} is a touch; the row must stop asking"


def test_an_expired_lease_is_still_a_touch(st):
    """Back in `ready`, but a session DID take it — that is not untouched."""
    i, _, _ = _old(st, "Build the thing")
    st.lease(i, "workerA")
    m = st.load()
    d = next(x for x in m.checklist if x["id"] == i)
    d["accepted_at"] -= 45 * DAY
    d["leased_at"] -= LEASE_TTL + 1
    assert m.lane_of(next(x for x in m.items if x.id == i)) == "ready"
    assert i not in [x.id for x in m.untouched()]


def test_an_unaccepted_proposal_is_never_flagged(st):
    """It is waiting on YOU already; one row must not ask twice."""
    i = st.propose("Build the thing", by="agent")["item_id"]
    m = st.load()
    next(x for x in m.checklist
         if x["id"] == i)["accepted_at"] = time.time() - 45 * DAY
    assert m.untouched() == []


def test_a_missing_accept_time_is_unknown_not_old(st):
    """An older log carries no timestamp. Absence of evidence is not age."""
    i, m, d = _old(st, "Build the thing")
    d["accepted_at"] = 0.0
    assert m.untouched() == []
    assert m.days_since_accepted(next(x for x in m.items
                                      if x.id == i)) == -1.0


def test_a_done_item_is_not_flagged(st):
    i, _, _ = _old(st, "Build the thing")
    st.complete(i, by="human")
    m = st.load()
    next(x for x in m.checklist if x["id"] == i)["accepted_at"] -= 45 * DAY
    assert m.untouched() == []


# ── it must stay measured ────────────────────────────────────────────────

def test_the_flag_never_reads_the_repo(st):
    """Deciding the work is DONE is the closed non-goal. This only times it."""
    # Docstrings stripped: the guard matched the word "mtime" inside the
    # paragraph explaining why no mtime is consulted. Sixth time a guard in
    # this suite has matched its own explanation.
    tree = ast.parse((ROOT / "agent_mission" / "store.py").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""
    fn = ast.unparse(tree).split("def untouched", 1)[1].split("\n    def ", 1)[0]
    for banned in ("exists", "open(", "read_text", "mtime", "glob", "verdict"):
        assert banned not in fn, f"untouched() is inspecting the repo: {banned}"


def test_the_threshold_is_one_constant(st):
    """A second copy of a threshold is this codebase's recurring bug."""
    assert UNTOUCHED_DAYS == 14
    hits = subprocess.run(["grep", "-rn", "UNTOUCHED_DAYS",
                           "agent_mission"], cwd=ROOT, capture_output=True,
                          text=True).stdout
    assert "UNTOUCHED_DAYS = 14" in hits
    assert hits.count("UNTOUCHED_DAYS = ") == 1


def test_the_reason_is_recorded_beside_the_number(st):
    """Pre-registered, with the measurement that chose it."""
    src = (ROOT / "agent_mission" / "store.py").read_text()
    block = src.split("UNTOUCHED_DAYS = 14")[0][-900:]
    assert "43" in block and "0.1" in block, \
        "the number must carry the distribution that justified it"


def test_untouched_writes_nothing(st):
    i, _, _ = _old(st, "Build the thing")
    log = st.root / "events.jsonl"
    before = log.read_bytes()
    st.load().untouched()
    assert log.read_bytes() == before


# ── the surfaces ─────────────────────────────────────────────────────────

def _cli(*args, home):
    return subprocess.run([sys.executable, "-m", "agent_mission", *args],
                          capture_output=True, text=True, cwd=ROOT,
                          env={"PATH": "/usr/bin:/bin",
                               "AGENT_MISSION_HOME": str(home)})


def _persist_old(st, text, days=45.0):
    """Write an accept event back-dated on disk, so the CLI sees it too."""
    i = st.propose(text, by="agent")["item_id"]
    st.accept(i, by="human")
    log = st.root / "events.jsonl"
    lines = log.read_text().splitlines()
    out = []
    for ln in lines:
        if '"accepted"' in ln and i in ln:
            import json
            ev = json.loads(ln)
            ev["at"] = ev["at"] - days * DAY
            ln = json.dumps(ev)
        out.append(ln)
    log.write_text("\n".join(out) + "\n")
    return i


def test_queue_flags_it_and_points_at_audit(st, tmp_path):
    _persist_old(st, "Build the thing")
    out = _cli("queue", "--on", "g", home=tmp_path).stdout
    assert "never touched" in out
    assert "mission audit" in out
    assert "agreed 45 days ago" in out


def test_audit_briefs_a_session_and_refuses_to_judge(st, tmp_path):
    _persist_old(st, "Build the thing")
    out = _cli("audit", "--on", "g", home=tmp_path).stdout
    assert "You do not build anything" in out
    assert "It does not know whether the work is" in out
    assert "claims-done" in out and "the human's tick" in out
    assert str(st.load().cwd) in out, "a claim resolves from the mission cwd"


def test_audit_says_so_when_there_is_nothing_to_ask(st, tmp_path):
    out = _cli("audit", "--on", "g", home=tmp_path).stdout
    assert "nothing agreed more than 14 days ago is untouched" in out


def test_audit_writes_nothing(st, tmp_path):
    _persist_old(st, "Build the thing")
    log = st.root / "events.jsonl"
    before = log.read_bytes()
    _cli("audit", "--on", "g", home=tmp_path)
    assert log.read_bytes() == before


def test_the_dispatch_message_warns_the_peer_itself(st, tmp_path):
    """The plan is read by a dispatcher; the WARNING must reach the worker."""
    _persist_old(st, "Build the thing in store.py")
    out = _cli("dispatch", "--on", "g", home=tmp_path).stdout
    assert "ALREADY BUILT" in out
    assert "do not" in out and "rebuild it" in out
    assert "it may already be built; the message says so" in out


def test_a_fresh_item_gets_no_warning(st, tmp_path):
    i = st.propose("Build the thing in store.py", by="agent")["item_id"]
    st.accept(i, by="human")
    out = _cli("dispatch", "--on", "g", home=tmp_path).stdout
    assert "ALREADY BUILT" not in out


# ── the board ────────────────────────────────────────────────────────────

def test_the_board_carries_the_days_not_a_bare_flag(st):
    _old(st, "Build the thing")      # in-memory is enough: rows re-derive
    i = _persist_old(st, "Build the other thing")
    rows = B._queue_rows(st.load(), "g")
    row = next(r for r in rows["ready"] if r["id"] == i)
    assert row["untouched_days"] == 45
    assert all(r["untouched_days"] == 0 for r in rows["ready"]
               if r["id"] != i)


def test_the_page_asks_rather_than_asserts(st):
    """Still asks — in a tooltip and the lane legend, not on 95 of 120 rows.

    The per-row line was accurate and useless at that density: a signal on
    79% of a board is wallpaper. The wording that matters is unchanged.
    """
    page = B.PAGE
    assert "untouched_days" in page
    assert "may already be built" in page
    assert "d untouched</span>" in page, "the chip must carry the age"
    assert "mission audit" in page


def test_the_strip_counts_from_the_rows(st):
    """No summary may disagree with the lane it summarises."""
    page = B.PAGE
    assert "lanes.ready.filter(r => r.untouched_days).length" in page


# ── 507f72ed: a claim that cannot be checked must say WHY at write time ───

def test_a_repo_relative_claim_is_told_where_it_looked(st, tmp_path):
    """"not on disk" reads as "your work is missing" when the fault is the path."""
    i = st.propose("fix it", by="agent")["item_id"]
    st.accept(i, by="human")
    out = _cli("claims-done", i, "done: fixed (agent_mission/store.py)",
               "--on", "g", home=tmp_path).stdout
    assert "named but NOT on disk" in out
    assert "paths resolve from the MISSION's cwd" in out
    assert str(st.load().cwd) in out
    assert "projects/builds/<repo>" in out


def test_an_overlong_claim_is_told_the_parser_window(st, tmp_path):
    """The other way a well-formed claim comes back empty. Hit twice today."""
    i = st.propose("fix it", by="agent")["item_id"]
    st.accept(i, by="human")
    long = "done: " + ("x" * 420) + " (agent_mission/store.py)"
    out = _cli("claims-done", i, long, "--on", "g", home=tmp_path).stdout
    assert "nothing checkable" in out
    assert "only the first 400" in out
    assert "shorten it" in out


def test_a_short_prose_claim_is_not_told_about_the_window(st, tmp_path):
    """The length hint must not fire on a claim whose problem is prose."""
    i = st.propose("fix it", by="agent")["item_id"]
    st.accept(i, by="human")
    out = _cli("claims-done", i, "done: I fixed the thing", "--on", "g",
               home=tmp_path).stdout
    assert "nothing checkable" in out
    assert "only the first 400" not in out
