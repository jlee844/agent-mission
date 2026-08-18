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


def _mission_with_a_finished_branch(tmp_path):
    from agent_mission.store import MissionStore
    st = MissionStore(tmp_path / "s")
    st.create("s", "/tmp", "obj", by="human")
    top = st.propose("Store", by="human")["item_id"]
    kid = st.propose("events", by="human", parent=top)["item_id"]
    left = st.propose("Ship", by="human")["item_id"]
    for i in (top, kid, left):
        st.accept(i, by="human")
    st.complete(kid, by="human")          # the whole branch is now finished
    return st.load(), top, kid, left


def test_finished_rows_are_marked_hidden_and_unfinished_ones_are_not(tmp_path):
    """A card is only useful at a glance if what is LEFT fits on it."""
    import agent_mission.board as B
    m, top, kid, left = _mission_with_a_finished_branch(tmp_path)
    rows = {r["id"]: r for r in B._tree(m)}
    assert rows[top]["hid"] and rows[kid]["hid"], "a done branch and its subtree fold"
    assert not rows[left]["hid"], "unfinished work is never folded"


def test_the_fold_is_a_toggle_not_a_deletion(tmp_path):
    """'What did we already do' is a real question — finished work is one
    click away, not gone."""
    import agent_mission.board as B
    assert "details class=doneblock" in B.PAGE
    assert "finished</summary>" in B.PAGE


def test_an_open_fold_survives_the_four_second_re_render():
    """The page re-renders on a timer; a fold that snaps shut under the reader
    is the same failure as the story editor that lost its listeners."""
    import agent_mission.board as B
    assert "openFolds" in B.PAGE
    # `toggle` does not bubble, so the delegated listener must capture
    assert "'toggle'" in B.PAGE and "}, true)" in B.PAGE


def test_the_cli_hides_finished_work_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    _mission_with_a_finished_branch(tmp_path)
    main(["show", "--session", "s"])
    out = capsys.readouterr().out
    assert "Ship" in out and "Store" not in out
    assert "1 finished, hidden" in out
    main(["show", "--session", "s", "--all"])
    assert "Store" in capsys.readouterr().out


def test_the_board_records_its_own_pid_not_the_launchers(tmp_path, monkeypatch):
    """A board started any way but through ensure() -- by hand, or after a
    restart -- used to leave the PREVIOUS pid in the record. `--stop` then
    signals a pid that has since been recycled to something else."""
    import os
    import json as J
    from agent_mission import daemon as D
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    D.claim(8976)
    rec = J.loads((tmp_path / "board.json").read_text())
    assert rec["pid"] == os.getpid() and rec["port"] == 8976
    D.release(8976)
    assert not (tmp_path / "board.json").exists()


def test_release_leaves_someone_elses_record_alone(tmp_path, monkeypatch):
    import json as J
    from agent_mission import daemon as D
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    (tmp_path / "board.json").write_text(J.dumps({"port": 8976, "pid": 1}))
    D.release(8976)
    assert (tmp_path / "board.json").exists(), "not ours; not ours to delete"


def test_stop_refuses_to_signal_a_pid_that_is_not_a_board(tmp_path, monkeypatch):
    """pid 1 is launchd. Signalling it because a stale file named it is the
    failure this guard exists for."""
    import json as J
    from agent_mission import daemon as D
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    (tmp_path / "board.json").write_text(J.dumps({"port": 8976, "pid": 1}))
    monkeypatch.setattr(D, "_responding", lambda *a, **k: True)
    killed = []
    monkeypatch.setattr(D.os, "kill", lambda *a: killed.append(a))
    assert D.stop() is False
    assert killed == [], "never signalled"


def test_the_always_on_context_cost_stays_small():
    """The package's cost in every session is its slash-command FRONTMATTER --
    the body loads only when invoked, and the CLI costs nothing until called.

    This is the differentiator against putting the plan in the prompt: there,
    planning depth and context cost rise together. Here they do not, and this
    test is what keeps that true as the command file grows.
    """
    md = (Path(__file__).resolve().parents[1] / "commands" / "mission.md")
    text = md.read_text(encoding="utf-8")
    assert text.startswith("---"), "frontmatter is what gets preloaded"
    front = text.split("---", 2)[1]
    assert len(front) < 400, (
        f"frontmatter is {len(front)} chars — every session pays this. "
        "Put the detail in the body, which loads only on invoke.")


def test_a_chosen_session_name_is_not_cut_in_half():
    """uuids shorten to 8 hex fine. An id a person chose does not: the board
    labelled the session "mltest-subagent" as "mltest-s"."""
    from agent_mission.session import short_id
    assert short_id("be17144b-d3be-41dd-a02a-c6ef71292e3f") == "be17144b"
    assert short_id("mltest-subagent") == "mltest-subagent"
    assert short_id("short") == "short"
    assert short_id("a" * 40).endswith("…")
