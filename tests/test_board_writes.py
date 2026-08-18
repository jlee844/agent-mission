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
    monkeypatch.setattr(board.HTTPServer, "__init__", lambda *a, **k: None)
    monkeypatch.setattr(board.HTTPServer, "serve_forever",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    board.serve(8976)
    assert board.WRITES.enabled is False and board.WRITES.code == ""
