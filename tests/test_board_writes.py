"""The board can accept and tick — without handing the agent a way in.

A naive Accept button is a POST any local process can make, including the
agent's own shell via curl, and the Claude Code deny rules never see it: they
match shell commands, not HTTP. These guard the thing that makes the buttons
safe — a code that only ever reached the human's terminal.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.actions import Session, Unauthorised, apply  # noqa: E402
from agent_mission.store import MissionStore, root_for          # noqa: E402


@pytest.fixture
def mission(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "goal", by="human")
    st.propose("an agent idea", by="agent")
    return st


def _first(st):
    return st.load().checklist[0]["id"]


def test_a_background_board_cannot_write_at_all(mission):
    """`mission init` spawns the board with its output going to a log file the
    agent can read. That board must never accept a write, whatever code is
    presented."""
    ro = Session(enabled=False)
    for code in ("", "000000", "guess"):
        with pytest.raises(Unauthorised, match="read-only"):
            apply(ro, code, "accept", "s", [_first(mission)])
    assert not mission.load().items[0].accepted


def test_the_wrong_code_is_refused(mission):
    s = Session(enabled=True)
    with pytest.raises(Unauthorised, match="wrong code"):
        apply(s, "000000", "accept", "s", [_first(mission)])
    assert not mission.load().items[0].accepted


def test_the_right_code_accepts_and_ticks(mission):
    s = Session(enabled=True)
    iid = _first(mission)
    assert apply(s, s.code, "accept", "s", [iid])["ok"]
    assert mission.load().items[0].accepted
    assert apply(s, s.code, "done", "s", [iid])["ok"]
    assert mission.load().items[0].done


def test_a_note_is_recorded_never_judged(mission):
    s = Session(enabled=True)
    apply(s, s.code, "note", "s", [], "the eval set is the bottleneck")
    assert mission.load().notes == ["the eval set is the bottleneck"]
    with pytest.raises(ValueError):
        apply(s, s.code, "note", "s", [], "   ")


def test_the_code_never_leaves_the_process(tmp_path, monkeypatch):
    """Not on disk, and not in any response. A code the agent can read is not
    a code."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    s = Session(enabled=True)
    on_disk = [p for p in Path(tmp_path).rglob("*") if p.is_file()
               and s.code in p.read_text(errors="replace")]
    assert on_disk == []

    from agent_mission import board
    monkeypatch.setattr(board, "WRITES", s)
    monkeypatch.setattr(board, "snapshot", lambda: [{"full": "s"}])
    board.CACHE = board._Cache()
    payload = json.dumps({"rows": board.CACHE.get(), "writable": board.WRITES.enabled})
    assert s.code not in payload
    assert '"writable": true' in payload, "the page is told writes work, not the code"


def test_an_unknown_action_is_refused(mission):
    s = Session(enabled=True)
    with pytest.raises(ValueError, match="unknown action"):
        apply(s, s.code, "set_objective", "s", ["x"])


def test_a_write_to_a_missing_mission_is_refused(mission):
    s = Session(enabled=True)
    with pytest.raises(ValueError, match="no mission"):
        apply(s, s.code, "accept", "no-such-session", ["x"])


def test_writes_are_off_unless_stdout_is_a_terminal(monkeypatch):
    """The tty is the test: a person ran the board themselves. The board that
    `mission init` starts in the background has no terminal."""
    from agent_mission import board
    monkeypatch.setattr(board.ThreadingHTTPServer, "__init__", lambda *a, **k: None)
    monkeypatch.setattr(board.ThreadingHTTPServer, "serve_forever",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    board.serve(8976)
    assert board.WRITES.enabled is False and board.WRITES.code == ""


def test_guessing_the_code_locks_writes(mission):
    """24 bits is ~16.7M values and the deny rules do not help: they match
    shell commands, not an HTTP POST from a python one-liner. On loopback a
    full sweep is hours, not years."""
    from agent_mission.actions import MAX_WRONG
    s = Session(enabled=True)
    for _ in range(MAX_WRONG):
        with pytest.raises(Unauthorised, match="wrong code"):
            apply(s, "000000", "accept", "s", [_first(mission)])
    with pytest.raises(Unauthorised, match="locked"):
        apply(s, s.code, "accept", "s", [_first(mission)])   # even the RIGHT one
    assert not mission.load().items[0].accepted


def test_a_correct_code_clears_the_counter(mission):
    from agent_mission.actions import MAX_WRONG
    s = Session(enabled=True)
    for _ in range(MAX_WRONG - 1):
        with pytest.raises(Unauthorised):
            apply(s, "000000", "accept", "s", [_first(mission)])
    apply(s, s.code, "accept", "s", [_first(mission)])
    assert s.wrong == 0, "a person who fat-fingered it once is not locked out"


def test_a_bad_id_in_a_batch_does_not_hide_the_good_ones(mission):
    """Each call writes its own event immediately, so raising partway through
    left a prefix applied and told the caller only '400'."""
    s = Session(enabled=True)
    good = _first(mission)
    out = apply(s, s.code, "accept", "s", [good, "not-an-id"])
    assert out["ids"] == [good] and out["failed"][0]["id"] == "not-an-id"
    assert out["ok"] is False
    assert mission.load().items[0].accepted, "the good one still landed"


def test_a_test_store_never_takes_the_real_boards_port(monkeypatch, tmp_path):
    """A board left running with AGENT_MISSION_HOME=/tmp/... kept port 8976, so
    every real session's card read "No mission yet". The data was fine; the
    board was reading an empty directory and the page did not say so."""
    from agent_mission import board
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    got = {}
    monkeypatch.setattr(board.ThreadingHTTPServer, "__init__",
                        lambda self, addr, h: got.update(port=addr[1]))
    monkeypatch.setattr(board.ThreadingHTTPServer, "serve_forever",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(board.ThreadingHTTPServer, "daemon_threads", True,
                        raising=False)
    board.serve(8976, writable=False)
    assert got["port"] != 8976, "a temp store must not hold the shared port"


def test_a_second_board_for_the_same_store_refuses_to_start(monkeypatch, tmp_path):
    """Two boards for one set of missions is two answers to 'what is the
    state', and the one you are looking at is whichever won the port. That is
    how a test store came to be serving 8976 while five real sessions read
    'No mission yet'."""
    from agent_mission import board, daemon
    monkeypatch.setenv("AGENT_MISSION_HOME", str(Path.home() / ".agent-mission"))
    monkeypatch.setattr(daemon, "identify",
                        lambda p, timeout=0.6: {"mission_board": True, "pid": 999}
                        if p == 8976 else None)
    started = []
    monkeypatch.setattr(board.ThreadingHTTPServer, "__init__",
                        lambda self, addr, h: started.append(addr[1]))
    board.serve(8977, writable=False)
    assert started == [], "it pointed at the running one instead of binding"
