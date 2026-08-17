"""Parsing, session identity, and the board's rendering guarantees."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.__main__ import _parse, main          # noqa: E402
from agent_mission.session import current_session_id     # noqa: E402


def test_the_editor_template_round_trips():
    text = """
# a comment, ignored
OBJECTIVE: Ship list sharing
SUCCESS:
- lists sync
- parity regenerated
CONSTRAINTS:
- no schema migration
NON-GOALS:
- redesigning the map
CHECKLIST:
- port the types
"""
    p = _parse(text)
    assert p["objective"] == "Ship list sharing"
    assert p["success_criteria"] == ["lists sync", "parity regenerated"]
    assert p["constraints"] == ["no schema migration"]
    assert p["non_goals"] == ["redesigning the map"]
    assert p["checklist"] == ["port the types"]


def test_empty_bullets_are_dropped_not_stored_blank():
    assert _parse("OBJECTIVE: x\nSUCCESS:\n-\n-  \n")["success_criteria"] == []


def test_comments_never_become_content():
    assert _parse("# OBJECTIVE: fake\nOBJECTIVE: real")["objective"] == "real"


def test_shared_flags_work_after_the_subcommand(tmp_path, monkeypatch, capsys):
    """`mission show --session X` is what people type; a top-level-only flag
    rejects it."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    assert main(["show", "--session", "nope"]) == 1
    assert "no mission" in capsys.readouterr().out


def test_session_identity_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert current_session_id() == "abc-123"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "  ")
    assert current_session_id() is None


def test_board_escapes_session_text():
    """Objectives and prompts are rendered into a page you open in a browser."""
    from agent_mission.board import PAGE
    assert "esc(" in PAGE and "&lt;" in PAGE


def test_board_binds_localhost_only():
    import inspect
    from agent_mission import board
    assert '"127.0.0.1"' in inspect.getsource(board.serve)


def test_board_tells_you_how_to_start_a_mission_when_there_is_none():
    from agent_mission.board import PAGE
    assert "mission init" in PAGE
