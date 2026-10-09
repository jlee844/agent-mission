"""C19-5(b): a session reports it is alive; the board stops guessing.

The ruling (8db99072) came with its own measurement, taken by another session
on 2026-10-08: the board mapped a live process's cwd to ONE project directory
and took the top N transcripts by mtime, N = that directory's process count.
Against 7 processes and 22 transcripts the picks were aged
56/252/266/267/267/267/287 minutes — six ENDED sessions called live, and a
live-but-idle session ranked below N dropped off the board entirely.

Both of those failures get a test here, because they are the two the design
exists to prevent and they pull in opposite directions: freshness alone
over-reports the dead, and a process check alone would be unavailable from
outside. The join (session id -> Claude pid) is written by the hook, which is
the only party that can see both.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_mission import board as B
from agent_mission import heartbeat as HB
from agent_mission import missions as M
from agent_mission.store import MissionStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "h"))
    monkeypatch.setattr(HB, "SOCKS", tmp_path / "socks")
    (tmp_path / "socks").mkdir()
    return tmp_path


def _sock(home, pid):
    (home / "socks" / f"{pid}.sock").write_text("")


def _record(sid, pid, at=None):
    d = HB._dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps(
        {"sid": sid, "pid": pid, "at": at if at is not None else time.time(),
         "cwd": "/tmp"}))


# ── the two failures it exists to fix ────────────────────────────────────

def test_an_idle_live_session_is_still_live(home, monkeypatch):
    """The dropped-session half. A beat 6 hours old, process still running."""
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("idle-but-live", 4242, at=time.time() - 6 * 3600)
    assert HB.live_ids() == {"idle-but-live"}, \
        "freshness must not decide liveness — that is the bug being removed"


def test_an_ended_session_is_not_live_however_fresh_its_beat(home, monkeypatch):
    """The over-reporting half. Beat seconds old, process gone."""
    monkeypatch.setattr(HB, "_ps", lambda pid: (0, ""))
    _record("just-ended", 4242, at=time.time())      # no socket written
    assert HB.live_ids() == set()


def test_a_recycled_pid_is_not_mistaken_for_the_session(home, monkeypatch):
    """Pids are reused. A socket alone is not proof it is the same process."""
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "/usr/bin/vim"))
    _record("gone", 4242)
    assert HB.live_ids() == set(), "the process must still look like an agent"


def test_a_session_that_never_reported_is_unknown_not_dead(home):
    assert HB.live_ids() == set()
    assert HB.known_ids() == set()


# ── the join key ─────────────────────────────────────────────────────────

def test_the_join_is_the_nearest_ancestor_owning_a_socket(home, monkeypatch):
    chain = {10: (11, "python"), 11: (12, "zsh"), 12: (13, "claude"),
             13: (1, "launchd")}
    monkeypatch.setattr(HB, "_ps", lambda pid: chain.get(pid, (0, "")))
    _sock(home, 12)
    assert HB.claude_pid(10) == 12


def test_no_socket_anywhere_in_the_chain_is_zero_not_a_guess(home, monkeypatch):
    chain = {10: (11, "python"), 11: (1, "zsh")}
    monkeypatch.setattr(HB, "_ps", lambda pid: chain.get(pid, (0, "")))
    assert HB.claude_pid(10) == 0


def test_a_cycle_in_the_process_chain_terminates(home, monkeypatch):
    monkeypatch.setattr(HB, "_ps", lambda pid: (10, "zsh"))
    assert HB.claude_pid(10) == 0


def test_beat_outside_a_session_records_nothing(home, monkeypatch):
    """A terminal has no agent ancestor. Silence, not a false record."""
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "zsh"))
    assert HB.beat("sid-x") is None
    assert HB.beats() == []


def test_beat_with_no_session_id_records_nothing(home):
    assert HB.beat("") is None


# ── it must never break the hook that calls it ───────────────────────────

def test_beat_never_raises(home, monkeypatch):
    def boom(_pid):
        raise OSError("ps is gone")
    monkeypatch.setattr(HB, "_ps", boom)
    assert HB.beat("sid-x") is None


def test_an_unreadable_record_does_not_blank_liveness(home, monkeypatch):
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("good", 4242)
    (HB._dir() / "broken.json").write_text("{not json")
    assert HB.live_ids() == {"good"}


def test_the_write_is_atomic(home, monkeypatch):
    """The board polls this directory every 4s; a half-written file is a crash."""
    src = (ROOT / "agent_mission" / "heartbeat.py").read_text()
    assert ".replace(" in src and "tmp" in src


def test_prune_keeps_a_long_idle_live_session(home, monkeypatch):
    """Age is the pruning criterion; liveness still vetoes it."""
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("ancient-but-live", 4242, at=time.time() - 90 * 86400)
    _record("ancient-and-dead", 777, at=time.time() - 90 * 86400)
    assert HB.prune() == 1
    assert HB.known_ids() == {"ancient-but-live"}


# ── the board ────────────────────────────────────────────────────────────

@pytest.fixture
def goal(home, monkeypatch):
    monkeypatch.setattr(B, "live", lambda: [])
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", "/tmp", "Ship the router", by="human")
    # BOTH are attached explicitly: `create` writes the mission, it does not
    # register the creating session in the goal's attachment list, so a
    # fixture that only attached s2 saw one session and the test failed for a
    # reason unrelated to liveness.
    M.attach("s1", "g")
    M.attach("s2", "g")
    assert M.sessions_of("g") == ["s1", "s2"]
    return st


def test_the_board_reports_three_states_never_two(goal, home, monkeypatch):
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    monkeypatch.setattr(B, "transcript_for", lambda sid: None)
    _record("s1", 4242)                      # live
    _record("s2", 777)                       # reported, process gone
    row = next(r for r in B.mission_rows() if r["id"] == "g")
    assert {s["full"]: s["liveness"] for s in row["sessions"]} == {
        "s1": "live", "s2": "ended"}
    assert row["ended"] is False


def test_an_unreported_session_never_makes_a_goal_ended(goal, home, monkeypatch):
    """Dimming a card on an ABSENT heartbeat is the mistake being removed."""
    monkeypatch.setattr(HB, "_ps", lambda pid: (0, ""))
    monkeypatch.setattr(B, "transcript_for", lambda sid: None)
    row = next(r for r in B.mission_rows() if r["id"] == "g")
    assert [s["liveness"] for s in row["sessions"]] == ["unknown", "unknown"]
    assert row["ended"] is False
    assert row["liveness_unknown"] == 2


def test_a_goal_is_ended_only_when_every_session_said_so(goal, home,
                                                         monkeypatch):
    monkeypatch.setattr(HB, "_ps", lambda pid: (0, ""))
    monkeypatch.setattr(B, "transcript_for", lambda sid: None)
    _record("s1", 777)
    _record("s2", 778)
    row = next(r for r in B.mission_rows() if r["id"] == "g")
    assert row["ended"] is True


def test_the_board_no_longer_guesses_from_transcript_mtime(goal):
    """The replaced code took the top N transcripts by mtime as 'live'."""
    import ast
    tree = ast.parse((ROOT / "agent_mission" / "board.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "mission_rows")
    for node in ast.walk(fn):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""
    src = ast.unparse(fn)
    assert "live_ids.add" not in src
    assert "st_mtime" not in src or "SCAN_WINDOW" in src, \
        "mtime may bound the claim SCAN, never decide liveness"


def test_the_page_can_say_unknown(goal):
    assert "liveness unknown — no heartbeat" in B.PAGE
    assert ".dot.unknown" in B.PAGE


# ── the hook wiring ──────────────────────────────────────────────────────

def test_the_signal_hook_beats(home, monkeypatch):
    """No new hook and no settings change: the per-turn command already runs."""
    src = (ROOT / "agent_mission" / "__main__.py").read_text()
    sig = src.split("def cmd_signal", 1)[1].split("\ndef ", 1)[0]
    assert "HB.beat(" in sig
    where = src.split("def cmd_whereami", 1)[1].split("\ndef ", 1)[0]
    assert "HB.beat(" in where, "the statusline covers long turns between prompts"


def test_signal_still_exits_zero_when_beating_fails(home, monkeypatch, tmp_path):
    out = subprocess.run([sys.executable, "-m", "agent_mission", "signal"],
                         capture_output=True, text=True, cwd=ROOT,
                         env={"PATH": "/usr/bin:/bin",
                              "AGENT_MISSION_HOME": str(tmp_path / "nope"),
                              "CLAUDE_CODE_SESSION_ID": "x"})
    assert out.returncode == 0


def test_beating_prunes_itself_once_the_directory_grows(home, monkeypatch):
    """`prune` existed and nothing called it — dead code and a slow leak."""
    monkeypatch.setattr(HB, "PRUNE_ABOVE", 3)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    # The join is stubbed, not exercised: a `_ps` that answers the same for
    # every pid never reaches the socket, so `claude_pid` honestly returns 0
    # and `beat` writes nothing. That is what the first version of this test
    # was really asserting. The join has its own tests above.
    monkeypatch.setattr(HB, "claude_pid", lambda *_a: 4242)
    _sock(home, 4242)
    for n in range(5):
        _record(f"dead-{n}", 9000 + n, at=time.time() - 90 * 86400)
    assert HB.beat("mine", "/tmp")["pid"] == 4242
    left = HB.known_ids()
    assert left == {"mine"}, f"dead records survived: {sorted(left)}"


def test_pruning_never_removes_the_record_just_written(home, monkeypatch):
    monkeypatch.setattr(HB, "PRUNE_ABOVE", 0)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    monkeypatch.setattr(HB, "claude_pid", lambda *_a: 4242)
    _sock(home, 4242)
    HB.beat("mine", "/tmp")
    assert HB.known_ids() == {"mine"}


# ── /clear: one pid, several session ids over its life ──────────────────

def test_only_the_newest_session_on_a_pid_is_live(home, monkeypatch):
    """`/clear` starts a new session id inside the SAME process.

    The old session's record still points at a live pid with a live socket,
    so the board called an ended session live until the process exited. A
    liveness bug that says something reassuring is the hard kind to notice.
    """
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("before-clear", 4242, at=time.time() - 3600)
    _record("after-clear", 4242, at=time.time())
    assert HB.live_ids() == {"after-clear"}
    # Both still REPORTED, so neither is "unknown": the older one is ended.
    assert HB.known_ids() == {"before-clear", "after-clear"}


def test_two_pids_are_both_live(home, monkeypatch):
    """The dedupe is per pid, not global — two real sessions must both show."""
    _sock(home, 4242)
    _sock(home, 4243)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("one", 4242)
    _record("two", 4243)
    assert HB.live_ids() == {"one", "two"}


def test_a_malformed_pid_does_not_break_liveness(home, monkeypatch):
    _sock(home, 4242)
    monkeypatch.setattr(HB, "_ps", lambda pid: (1, "claude"))
    _record("good", 4242)
    d = HB._dir()
    (d / "bad.json").write_text(json.dumps(
        {"sid": "bad", "pid": "not-a-pid", "at": time.time()}))
    assert HB.live_ids() == {"good"}
