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
    assert p["checklist"] == [{"text": "port the types", "indent": 0}]


def test_indentation_in_the_checklist_is_nesting():
    """A plan is a tree. Two spaces hangs a task under the subgoal above it."""
    p = _parse("OBJECTIVE: x\nCHECKLIST:\n- Mobile port\n  - types\n  - sheet\n- Backend\n")
    assert [(c["text"], c["indent"]) for c in p["checklist"]] == [
        ("Mobile port", 0), ("types", 2), ("sheet", 2), ("Backend", 0)]


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


# ── the shared board ─────────────────────────────────────────────────────────

def test_editor_arguments_are_honoured(tmp_path, monkeypatch):
    """EDITOR routinely carries flags — "code -w", "subl -w". Treating the whole
    string as one executable fails with a FileNotFoundError naming the flags."""
    import agent_mission.__main__ as M
    seen = {}
    monkeypatch.setattr(M.subprocess, "run", lambda cmd, **k: seen.setdefault("cmd", cmd))
    monkeypatch.setenv("EDITOR", "code -w")
    M._edit("hello")
    assert seen["cmd"][:2] == ["code", "-w"]


def test_a_dead_board_record_is_not_trusted(tmp_path, monkeypatch):
    """A recorded port whose process died is worse than no record — it sends
    you to a dead URL."""
    import json as J
    from agent_mission import daemon
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "board.json").write_text(J.dumps({"port": 9, "pid": 1}))
    assert daemon.running() is None
    assert not (tmp_path / "board.json").exists(), "stale record is cleared"


def test_no_board_record_means_no_board(tmp_path, monkeypatch):
    from agent_mission import daemon
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    assert daemon.running() is None


def test_reading_a_missionless_session_leaves_no_directory(tmp_path):
    """An empty directory reads as 'a mission exists' to anything scanning."""
    from agent_mission.store import MissionStore
    root = tmp_path / "sess"
    assert MissionStore(root).load() is None
    assert not root.exists()


def test_writing_creates_the_directory(tmp_path):
    from agent_mission.store import MissionStore
    root = tmp_path / "sess"
    MissionStore(root).create("s", "/r", "goal", by="human")
    assert (root / "events.jsonl").exists()


def test_an_ended_session_keeps_its_mission_on_the_board():
    """A mission whose session closed must not vanish — the work happened."""
    import inspect
    from agent_mission import board
    src = inspect.getsource(board.snapshot)
    assert '"ended": True' in src and "_missions_home" in src


# ── the slash command ────────────────────────────────────────────────────────

def test_setup_installs_the_slash_command(tmp_path):
    from agent_mission.__main__ import main
    assert main(["setup", "--dest", str(tmp_path)]) == 0
    assert (tmp_path / "mission.md").exists()


def test_setup_refuses_to_clobber_without_force(tmp_path):
    from agent_mission.__main__ import main
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "mission.md").write_text("mine", encoding="utf-8")
    assert main(["setup", "--dest", str(tmp_path)]) == 1
    assert (tmp_path / "mission.md").read_text() == "mine"
    assert main(["setup", "--dest", str(tmp_path), "--force"]) == 0
    assert (tmp_path / "mission.md").read_text() != "mine"


def test_init_can_be_written_from_a_file(tmp_path, monkeypatch):
    """After interviewing the user, the agent transcribes the answers rather
    than driving an interactive editor."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    src = tmp_path / "m.txt"
    src.write_text("OBJECTIVE: Ship it\nSUCCESS:\n- it ships\nCHECKLIST:\n- do the thing\n")
    from agent_mission.__main__ import main
    assert main(["init", "--session", "s1", "--blank", "--no-board",
                 "--from-file", str(src)]) == 0
    from agent_mission.store import MissionStore, root_for
    m = MissionStore(root_for("s1")).load()
    assert m.objective == "Ship it" and m.success_criteria == ["it ships"]


def test_the_command_interviews_before_writing():
    """A mission written in ten seconds is a wish."""
    src = (Path(__file__).resolve().parents[1] / "commands" / "mission.md").read_text()
    assert "one question at a time" in src.lower()
    assert "recommended answer" in src
    assert "scribe, never the author" in src
    low = src.lower()
    for topic in ("objective", "success criteria", "constraints", "non-goals"):
        assert topic in low, f"the interview must cover {topic}"
    assert "subgoal" in low, "the plan is elicited as a tree, not a flat list"


def test_the_slash_command_tells_the_agent_to_execute_not_interpret():
    """'mission init' typed into a session was read as 'go re-initialise the
    project' and the agent did something else entirely. The command file has to
    close that off explicitly."""
    src = (Path(__file__).resolve().parents[1] / "commands" / "mission.md").read_text()
    low = src.lower()
    assert "do not interpret" in low
    assert "mission $ARGUMENTS" in src
    assert "verbatim" in low, "output must be shown as-is, not summarised"


def test_the_slash_command_does_not_let_the_agent_author_the_mission():
    """The store refuses agent writes; the command file must refuse to route
    around that by inventing answers and passing them through the CLI."""
    src = (Path(__file__).resolve().parents[1] / "commands" / "mission.md").read_text()
    low = src.lower()
    assert "protected" in low
    assert "route around" in low
    assert "traces to something they said" in low


def test_child_guides_skip_the_root_level():
    """Roots are flush bullets with no connector, so a guide for the root level
    points at a line that is never drawn — children rendered as "│├"."""
    import agent_mission.board as B
    assert "i.guides.slice(1)" in B.PAGE


def test_a_branch_carries_its_rolled_up_progress():
    import agent_mission.board as B
    assert "class=mini" in B.PAGE and "i.roll" in B.PAGE
