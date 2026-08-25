"""Parsing, session identity, and the board's rendering guarantees."""
import pytest
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
    """Was `assert '"127.0.0.1"' in inspect.getsource(board.serve)` -- which
    passes if the string appears in a comment or a dead branch, and never
    verifies the socket. Bind it and look."""
    import socket
    import threading
    from agent_mission import board

    srv = board.ThreadingHTTPServer(("127.0.0.1", 0), board._H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass                      # loopback reaches it

        host = socket.gethostbyname(socket.gethostname())
        if host.startswith("127."):
            pytest.skip("this machine has no non-loopback address to try")
        with pytest.raises(OSError):
            socket.create_connection((host, port), timeout=2)
    finally:
        srv.shutdown()


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


def test_an_ended_session_keeps_its_mission_on_the_board(tmp_path, monkeypatch):
    """The work happened; losing sight of it is what this exists to prevent.

    Was an assertion on inspect.getsource() -- which passes if the string
    appears in a comment, and broke the moment the function was split. Assert
    the card, not the source."""
    from agent_mission import board
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(board, "live", lambda: [])      # nothing running
    st = MissionStore(root_for("gone"))
    st.create("gone", "/repo", "work that finished", by="human",
              typed_by="human")

    rows = board.snapshot()
    assert [r["full"] for r in rows] == ["gone"]
    assert rows[0]["ended"] is True and rows[0]["has_mission"]


def test_setup_installs_the_slash_command(tmp_path):
    from agent_mission.__main__ import main
    assert main(["setup", "--dest", str(tmp_path)]) == 0
    assert (tmp_path / "mission.md").exists()


def test_setup_refuses_to_clobber_without_force(tmp_path, monkeypatch):
    """It must not overwrite an edited command file -- but it must also not
    STOP, which it used to: one present surface aborted the other four."""
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    dest = tmp_path / "c"
    dest.mkdir()
    (dest / "mission.md").write_text("mine", encoding="utf-8")

    assert main(["setup", "--dest", str(dest), "--settings", str(settings)]) == 0
    assert (dest / "mission.md").read_text() == "mine", "not clobbered"
    assert main(["setup", "--dest", str(dest), "--settings", str(settings),
                 "--force"]) == 0
    assert (dest / "mission.md").read_text() != "mine", "--force updates it"


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
    # "aaaa..." is all hex characters, so it takes the uuid path -- which is
    # correct, and was my test being wrong rather than the code.
    assert short_id("a" * 40) == "aaaaaaaa"
    assert short_id("training-run-" + "x" * 40).endswith("…")


def test_an_agent_cannot_write_permission_rules_even_with_force(tmp_path, monkeypatch, capsys):
    """--force means 'overwrite the command file'. It used to wave through the
    settings edit too, so the first agent run wrote the very rules meant to
    constrain it."""
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.delenv("AGENT_MISSION_I_AM_HUMAN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({"permissions": {"deny": []}}))

    main(["setup", "--force", "--dest", str(tmp_path / "cmds"),
          "--settings", str(settings)])
    assert J.loads(settings.read_text())["permissions"]["deny"] == []
    assert "run `mission setup` yourself in a terminal" in capsys.readouterr().out


def test_a_person_at_a_terminal_gets_the_rules_written(tmp_path, monkeypatch, capsys):
    import json as J
    from agent_mission.__main__ import DENY_RULES, main
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({"permissions": {"deny": ["Bash(rm:*)"]}}))

    main(["setup", "--dest", str(tmp_path / "cmds"), "--settings", str(settings)])
    deny = J.loads(settings.read_text())["permissions"]["deny"]
    assert deny[0] == "Bash(rm:*)", "existing rules kept"
    assert all(r in deny for r in DENY_RULES)
    assert list(tmp_path.glob("settings.json.bak-mission-*")), "backed up first"


def _mission_at(tmp_path, sid, cwd, name):
    from agent_mission.store import MissionStore, root_for
    st = MissionStore(root_for(sid))
    st.create(sid, cwd, f"objective for {name}", by="human")
    st.set_protected("name", name, by="human")
    return st


def test_a_person_in_their_own_terminal_gets_a_message_not_a_traceback(
        tmp_path, monkeypatch, capsys, at_a_keyboard):
    """Outside Claude Code there is no CLAUDE_CODE_SESSION_ID -- and the design
    sends the person to their own terminal for every human-only command, so
    this is the NORMAL path. It used to raise a pathlib TypeError."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_path)

    assert main(["accept", "abc12345"]) == 1
    out = capsys.readouterr().out
    assert "mission init" in out, "no goals at all is a state, and it says so"
    assert "Traceback" not in out


def test_no_code_path_resolves_a_target_from_the_working_directory(
        tmp_path, monkeypatch):
    """C11c, finished. cwd used to route writes: the missions recorded for this
    directory, deepest match first. It ran once for real -- a career objective
    was written onto the Tripnom mission and renamed it, because both were
    opened at the Mission Control root.

    A session is opened where the work can REACH what it needs. That says what
    it can SEE, never what it is FOR."""
    import inspect
    from agent_mission import session as S
    from agent_mission.session import NoSessionError, resolve_session
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo = tmp_path / "repo"
    (repo / "deep").mkdir(parents=True)
    _mission_at(tmp_path, "sess-a", str(repo), "The repo mission")

    # Standing exactly on the mission's own directory is the friendliest case
    # the old router had, and it must still refuse.
    for where in (repo, repo / "deep"):
        with pytest.raises(NoSessionError):
            resolve_session(None, str(where))

    src = inspect.getsource(S.resolve_session)
    assert "m.cwd" not in src and "startswith" not in src, \
        "the directory matcher is back"


def test_the_refusal_names_your_goals_so_the_fix_is_a_paste(
        tmp_path, monkeypatch):
    """A refusal is only as good as what it hands you next."""
    from agent_mission.session import NoSessionError, resolve_session
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    _mission_at(tmp_path, "sess-a", str(tmp_path), "First")
    _mission_at(tmp_path, "sess-b", str(tmp_path), "Second")

    with pytest.raises(NoSessionError) as e:
        resolve_session(None, str(tmp_path))
    assert e.value.flag == "--on", "one routing flag, and it is the name one"
    labels = " ".join(c[1] for c in e.value.candidates)
    assert "First" in labels and "Second" in labels
    assert "active" in labels, "and when each was last touched"


def test_on_addresses_a_goal_that_was_never_migrated(tmp_path, monkeypatch,
                                                     capsys):
    """One routing flag means one, including into the old layout. Otherwise the
    fix for an un-migrated goal is `run migrate first` -- a second instruction
    to follow, which setup exists to abolish."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    _mission_at(tmp_path, "sess-old", str(tmp_path), "Legacy goal")

    assert main(["show", "--on", "Legacy"]) == 0
    assert "Legacy goal" in capsys.readouterr().out


def test_a_delegated_mission_attaches_to_its_parent(tmp_path, monkeypatch):
    """One real session produced five cards in testing, four of them its own
    delegated children. A slice of the work is not a peer of the whole."""
    from agent_mission import board
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(board, "live", lambda: [])
    parent = MissionStore(root_for("p"))
    parent.create("p", "/repo", "the whole thing", by="human")
    child = MissionStore(root_for("p.slice"))
    child.create("p.slice", "/repo", "one slice", by="human",
                 parent_session="p", parent_item="item1")

    rows = board.snapshot()
    assert [r["full"] for r in rows] == ["p"], "the child is not a top-level card"
    assert [k["title"] for k in rows[0]["children"]] == ["one slice"]


def test_an_orphaned_child_is_still_shown(tmp_path, monkeypatch):
    """If the parent is not on the board, hiding the child loses the work
    entirely -- which is the failure this whole tool exists to prevent."""
    from agent_mission import board
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(board, "live", lambda: [])
    MissionStore(root_for("c")).create("c", "/repo", "orphan slice", by="human",
                                       parent_session="gone", parent_item="i")
    assert [r["full"] for r in board.snapshot()] == ["c"]


def _row(**kw):
    base = dict(ended=False, has_mission=True, pending_accept=0, mtime=0.0)
    base.update(kw)
    return base


def test_a_state_says_what_a_card_is_in_one_word():
    import time
    from agent_mission.board import IDLE_AFTER, _state
    now = time.time()
    assert _state(_row(mtime=now), now) == "working"
    assert _state(_row(mtime=now - IDLE_AFTER - 1), now) == "idle"
    assert _state(_row(has_mission=False, mtime=now), now) == "nomission"
    assert _state(_row(pending_accept=2, mtime=now), now) == "waiting"
    assert _state(_row(ended=True, mtime=now), now) == "ended"


def test_a_finished_session_still_reports_what_it_is_waiting_on():
    """'ended' used to shadow 'waiting', so the strip read 'nothing is waiting
    on you' directly above two cards saying 'awaiting accept'. Proposals in a
    session that has stopped are exactly the ones that get lost."""
    import time
    from agent_mission.board import _state
    now = time.time()
    assert _state(_row(ended=True, pending_accept=3, mtime=now), now) == "waiting"


def test_sessions_needing_you_sort_first_but_finished_ones_stay_last(
        tmp_path, monkeypatch):
    import time
    from agent_mission import board
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(board, "live", lambda: [])
    for name in ("quiet", "asking"):
        st = MissionStore(root_for(name))
        st.create(name, "/repo", f"goal {name}", by="human")
    MissionStore(root_for("asking")).propose("an idea", by="agent")

    order = [r["full"] for r in board.snapshot()]
    assert order.index("asking") < order.index("quiet"), "the ask comes first"
    assert all(r["ended"] for r in board.snapshot()), "both are ended sessions"


def test_the_board_serves_the_last_snapshot_instead_of_blocking(monkeypatch):
    """Reading every live transcript takes ~4s on a real board, and the browser
    polls every 4s -- requests overlapped permanently and the page sat blank.
    A reader must never wait for the parse."""
    import time
    from agent_mission import board
    calls = []

    def slow():
        calls.append(1)
        return [{"full": f"snap-{len(calls)}"}]

    monkeypatch.setattr(board, "snapshot", slow)
    c = board._Cache(every=60)
    assert c.get()[0]["full"] == "snap-1"
    for _ in range(20):
        c.get()
    assert len(calls) == 1, "20 reads, one parse"


def test_a_failed_refresh_keeps_the_last_good_board(monkeypatch):
    """A board that empties itself because one transcript was mid-write is
    worse than one that is six seconds stale."""
    from agent_mission import board
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] > 1:
            raise OSError("transcript vanished")
        return [{"full": "good"}]

    monkeypatch.setattr(board, "snapshot", flaky)
    c = board._Cache(every=0)
    assert c.get()[0]["full"] == "good"
    c._refresh()
    assert c.get()[0]["full"] == "good", "the last good board survives"


def test_force_will_not_silently_discard_a_plan(tmp_path, monkeypatch, capsys):
    """Another session told Jonathan to run `mission init --force` to fix a bad
    objective. It would have dropped 11 items and 10 pending proposals: load()
    folds from the LAST `created` event, so a re-init discards everything
    before it."""
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    seed = tmp_path / "m.txt"
    seed.write_text("OBJECTIVE: original\nCHECKLIST:\n- one\n- two\n")
    main(["init", "--from-file", str(seed), "--session", "s", "--cwd",
          str(tmp_path), "--no-board"])
    capsys.readouterr()

    again = tmp_path / "m2.txt"
    again.write_text("OBJECTIVE: replacement\n")
    assert main(["init", "--force", "--from-file", str(again), "--session", "s",
                 "--cwd", str(tmp_path), "--no-board"]) == 1
    out = capsys.readouterr().out
    assert "2 items" in out and "mission set objective" in out
    m = MissionStore(root_for("s")).load()
    assert m.objective == "original" and len(m.checklist) == 2, "nothing lost"

    assert main(["init", "--force", "--discard-plan", "--from-file", str(again),
                 "--session", "s", "--cwd", str(tmp_path), "--no-board"]) == 0
    assert MissionStore(root_for("s")).load().objective == "replacement"


def test_a_write_says_which_mission_it_landed_on(tmp_path, monkeypatch, capsys):
    """Two items meant for another project turned up on the career card and
    nobody noticed: the only feedback was an id and the text, neither of which
    says where it went."""
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "the career hub", by="human")
    st.set_protected("name", "Career hub", by="human")

    main(["add", "Backend", "--session", "s"])
    assert "Career hub" in capsys.readouterr().out
    main(["propose", "invite tokens", "--session", "s"])
    assert "Career hub" in capsys.readouterr().out


def test_the_readme_does_not_hardcode_a_test_count():
    """It went stale twice and was caught by a reviewer both times. CI says the
    number now; prose does not get to."""
    import re
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    stale = re.findall(r"\b\d+\s*tests\b", readme)
    assert not stale, f"hardcoded test counts in the README: {stale}"
    assert "actions/workflows/tests.yml/badge.svg" in readme, "CI badge instead"


def test_the_deny_rule_count_in_the_docs_matches_the_list():
    """A comment said 'the four commands only a person may run' directly above
    a list of five, added in the same commit that added the fifth."""
    import re
    from agent_mission.__main__ import DENY_RULES
    root = Path(__file__).resolve().parents[1]
    src = (root / "agent_mission" / "__main__.py").read_text()
    readme = (root / "README.md").read_text()
    words = {"four": 4, "five": 5, "six": 6, "three": 3}
    for text, where in ((src, "__main__.py"), (readme, "README.md")):
        for m in re.finditer(r"\b(three|four|five|six)\b[^.\n]{0,40}"
                             r"(deny rule|commands only a person)", text, re.I):
            assert words[m.group(1).lower()] == len(DENY_RULES), (
                f"{where} says {m.group(1)}; DENY_RULES has {len(DENY_RULES)}")


def _plan_for(tmp_path, monkeypatch):
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "goal", by="human")
    parent = st.propose("Subgoal", by="human")["item_id"]
    st.accept(parent, by="human")
    kids = []
    for t in ("one", "two"):
        e = st.propose(t, by="human", parent=parent)["item_id"]
        st.accept(e, by="human")
        kids.append(e)
    loose = st.propose("an agent idea", by="agent")["item_id"]
    return st, parent, kids, loose


def test_accept_pending_takes_every_proposal_without_ids(
        tmp_path, monkeypatch, capsys, at_a_keyboard):
    """152 proposals across five real missions, 61 never accepted. The cost was
    retyping an 8-character id per item; the worst case was 25 of them in one
    command that failed and had to be redone by hand."""
    from agent_mission.__main__ import main
    st, parent, kids, loose = _plan_for(tmp_path, monkeypatch)
    assert main(["accept", "--pending", "--session", "s"]) == 0
    assert st.load().unaccepted == []
    assert loose in capsys.readouterr().out


def test_done_under_ticks_a_subtree_and_only_its_leaves(
        tmp_path, monkeypatch, at_a_keyboard):
    from agent_mission.__main__ import main
    st, parent, kids, _ = _plan_for(tmp_path, monkeypatch)
    main(["done", "--under", parent, "--session", "s"])
    m = st.load()
    assert {i.id for i in m.leaves if i.done} == set(kids)
    assert m.done_count == 2, "the container is not work and is not ticked"


def test_all_will_not_tick_a_whole_plan(tmp_path, monkeypatch, at_a_keyboard):
    """`--all` is shared with `show --all`, so `mission done --all` is a
    plausible typo for 'show me everything' — and it would declare every task
    finished."""
    from agent_mission.__main__ import main
    st, parent, kids, _ = _plan_for(tmp_path, monkeypatch)
    main(["done", "--all", "--session", "s"])
    assert st.load().done_count == 0


def test_remove_takes_several_ids(tmp_path, monkeypatch, at_a_keyboard):
    from agent_mission.__main__ import main
    st, parent, kids, loose = _plan_for(tmp_path, monkeypatch)
    main(["remove", *kids, "--session", "s"])
    assert {d["id"] for d in st.load().checklist} == {parent, loose}


def test_observe_exists_and_needs_no_gate(tmp_path, monkeypatch, capsys):
    """The whole OBSERVABLE level was unreachable from the CLI: store.observe()
    existed and was tested, the README and the refusal message both told the
    agent it could observe, and there was no subcommand."""
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/repo", "goal", by="human")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    assert main(["observe", "evidence", "127 tests pass", "--session", "s"]) == 0
    assert MissionStore(root_for("s")).load().evidence == ["127 tests pass"]


def test_observe_cannot_reach_a_protected_field(tmp_path, monkeypatch):
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    MissionStore(root_for("s")).create("s", "/repo", "goal", by="human")
    with pytest.raises(SystemExit):        # argparse rejects the choice
        main(["observe", "objective", "mine now", "--session", "s"])


def test_pending_prints_the_command_that_clears_it(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/repo", "goal", by="human")
    st.propose("an idea", by="agent")
    main(["pending", "--session", "s"])
    out = capsys.readouterr().out
    assert "1 awaiting you" in out and "mission accept --pending" in out


def test_an_existing_statusline_is_composed_not_discarded(
        tmp_path, monkeypatch, capsys, at_a_keyboard):
    """C2 shipped as printed instructions -- "here is what to chain" -- and the
    statusline stayed exactly as it was for two days. A surface delivered as
    advice does not get installed, so setup composes it instead. The original
    command survives verbatim inside the wrapper."""
    import json as J
    import subprocess
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({"statusLine": {
        "type": "command", "command": "printf 'branch: main\\nline two'"}}))

    main(["setup", "--dest", str(tmp_path / "c"), "--settings", str(settings)])
    wrapper = J.loads(settings.read_text())["statusLine"]["command"]
    assert wrapper.endswith("statusline.sh")
    body = Path(wrapper).read_text()
    assert "printf 'branch: main" in body, "the original, verbatim"

    out = subprocess.run(["sh", wrapper], input="{}", capture_output=True,
                         text=True).stdout
    assert "branch: main" in out, "their statusline still shows"
    assert "\n" not in out.strip(), "one line — statuslines truncate"


def test_the_statusline_is_offered_when_there_is_none(tmp_path, monkeypatch,
                                                      capsys, at_a_keyboard):
    import json as J
    from agent_mission.__main__ import STATUSLINE, main
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    main(["setup", "--dest", str(tmp_path / "c"), "--settings", str(settings)])
    assert J.loads(settings.read_text())["statusLine"] == STATUSLINE
    assert list(tmp_path.glob("settings.json.bak-mission-*"))


def test_the_reanchor_hook_is_appended_not_substituted(tmp_path, monkeypatch,
                                                       capsys, at_a_keyboard):
    """The machine this was built on already had four hooks. Replacing the
    SessionStart array would have removed two of them."""
    import json as J
    from agent_mission.__main__ import main
    settings = tmp_path / "settings.json"
    theirs = [{"hooks": [{"type": "command", "command": "their-thing"}]},
              {"hooks": [{"type": "command", "command": "another"}]}]
    settings.write_text(J.dumps({"hooks": {"SessionStart": list(theirs)}}))

    main(["setup", "--dest", str(tmp_path / "c"), "--settings", str(settings)])
    got = J.loads(settings.read_text())["hooks"]["SessionStart"]
    assert got[:2] == theirs, "both of theirs survive, in order"
    assert len(got) == 3 and "mission whereami --full" in J.dumps(got[2])


def test_an_agent_cannot_edit_your_settings(tmp_path, monkeypatch, capsys):
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    main(["setup", "--force", "--dest", str(tmp_path / "c"),
          "--settings", str(settings)])
    assert J.loads(settings.read_text()) == {}, "nothing written"


def test_proposing_into_another_session_records_where_it_came_from(
        tmp_path, monkeypatch, capsys):
    """A reviewing session's findings reach the plan without the human
    retyping them -- the friction that stranded 61 of 152 proposals."""
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "reviewer")
    for name in ("alpha", "beta"):
        st = MissionStore(root_for(name))
        st.create(name, "/repo", f"goal of {name}", by="human")
        st.set_protected("name", f"{name} mission", by="human")

    assert main(["propose", "found a bug", "--into", "beta mission"]) == 0
    beta = MissionStore(root_for("beta")).load()
    assert [i.text for i in beta.items] == ["found a bug"]
    assert not beta.items[0].accepted, "still inert — a peer cannot accept"
    ev = [e for e in MissionStore(root_for("beta")).events()
          if e["kind"] == "proposed"][0]
    assert ev["from_session"] == "reviewer"
    assert MissionStore(root_for("alpha")).load().checklist == []


def test_an_ambiguous_target_refuses_rather_than_guessing(tmp_path, monkeypatch,
                                                          capsys):
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    for name in ("alpha", "beta"):
        st = MissionStore(root_for(name))
        st.create(name, "/repo", f"goal of {name}", by="human")
        st.set_protected("name", f"{name} mission", by="human")

    assert main(["propose", "x", "--into", "mission"]) == 1
    out = capsys.readouterr().out
    assert "matches several" in out and "alpha" in out and "beta" in out
    assert MissionStore(root_for("beta")).load().checklist == []


def test_check_reports_each_surface_and_exits_nonzero_when_missing(
        tmp_path, monkeypatch, capsys):
    """The contract: re-running `mission setup` is always the complete fix, and
    there is never a second instruction to follow. --check is how you know."""
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))

    assert main(["setup", "--check", "--settings", str(settings),
                 "--dest", str(tmp_path / "c")]) == 1
    out = capsys.readouterr().out
    # Four surfaces, not five: the board bookmark is written by the board
    # itself when it binds, so offering it in setup was ceremony -- a row that
    # asked you to install something that installs itself.
    for surface in ("slash command", "deny rules", "statusline",
                    "re-anchor hook"):
        assert surface in out
    assert "board bookmark" not in out
    assert "missing" in out and "mission setup" in out


def test_check_passes_once_setup_has_run(tmp_path, monkeypatch, capsys,
                                         at_a_keyboard):
    import json as J
    from agent_mission.__main__ import main
    from agent_mission.daemon import write_bookmark
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_MISSION_HOME", str(home))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))

    main(["setup", "--settings", str(settings), "--dest", str(tmp_path / "c")])
    write_bookmark(8976)                 # the board writes this when it binds
    capsys.readouterr()  # noqa
    assert main(["setup", "--check", "--settings", str(settings),
                 "--dest", str(tmp_path / "c")]) == 0
    assert "everything is installed" in capsys.readouterr().out


def test_setup_run_twice_changes_nothing_the_second_time(
        tmp_path, monkeypatch, capsys, at_a_keyboard):
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    args = ["setup", "--force", "--settings", str(settings),
            "--dest", str(tmp_path / "c")]

    main(args)
    once = settings.read_text()
    capsys.readouterr()
    main(args)
    assert settings.read_text() == once, "idempotent"
    out = capsys.readouterr().out
    assert "already" in out


def test_the_bookmark_points_at_the_live_port_and_says_so_when_down(tmp_path,
                                                                    monkeypatch):
    """Bookmark it once and it is correct forever, including across a port
    change -- and it must not send you to a dead URL."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission.daemon import write_bookmark
    page = write_bookmark(8979).read_text()
    assert "http://127.0.0.1:8979/" in page
    assert 'mode: "no-cors"' in page, "file:// origin is null; a plain fetch is refused"
    assert "not running" in page and "mission board" in page


def test_one_installed_surface_does_not_abort_the_rest(tmp_path, monkeypatch,
                                                       capsys, at_a_keyboard):
    """`mission setup` on a machine that already had the slash command printed
    'use --force' and stopped -- installing none of the other four surfaces.
    The one command whose entire contract is 'this is always the complete fix'
    was the one that told you to type something else."""
    import json as J
    from agent_mission.__main__ import DENY_RULES, main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    dest = tmp_path / "c"
    dest.mkdir()
    (dest / "mission.md").write_text("an older version")

    assert main(["setup", "--settings", str(settings), "--dest", str(dest)]) == 0
    data = J.loads(settings.read_text())
    assert all(r in data["permissions"]["deny"] for r in DENY_RULES)
    assert data["statusLine"], "and the statusline"
    assert data["hooks"]["SessionStart"], "and the hook"
    out = capsys.readouterr().out
    assert "already installed" in out and "older version" in out


def test_the_readme_tells_one_routing_story():
    """It told three at once. A "cwd never routes a write" line sat forty lines
    above a section documenting the cwd router, whose worked example rebuilt an
    `accept` -- a write -- from directory candidates. The prose was right about
    the design and wrong about the code, so the code was the thing to fix."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "Names route, sessions speak, directories inform." in readme
    for gone in ("Which mission does a command mean?", "counting ancestors",
                 "Claude Code specific", "matched from the working directory"):
        assert gone not in readme, f"the old routing story survives: {gone!r}"

    # No human-facing example addresses a goal by session id.
    for line in readme.splitlines():
        if line.strip().startswith("mission ") or line.startswith("| `"):
            assert "--session" not in line, f"--session in an example: {line}"


def test_the_commands_table_lists_every_live_command():
    """A stranger scanning the table has to see the current shape of the
    product. attach/missions/archive/migrate shipped with the inversion and the
    table still described the version before it."""
    import re
    from agent_mission.__main__ import main
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    table = readme[readme.index("| command | what it does |"):]
    # [a-z-]: `claims-done` is one command, and a hyphen-blind extraction
    # counted its first half and reported its second missing.
    listed = {w for row in table.splitlines() if row.startswith("| `")
              for w in re.findall(r"`([a-z][a-z-]*)", row.split("|")[1])}

    main(["version"])
    from agent_mission.__main__ import _SUBCOMMANDS
    missing = {c for c in _SUBCOMMANDS if c not in listed} - {"help", "version"}
    assert not missing, f"live commands missing from the README table: {missing}"


def test_help_reads_usage_for_a_command_the_agent_cannot_run(capsys):
    """Permission patterns are prefix matches with no negation, so
    `Bash(mission accept:*)` also blocks `mission accept --help`. An agent
    composing a command for the human cannot read the flags of the commands it
    is most likely to be composing. A hole in the deny rule would weaken the
    one gate that is a real boundary; a read-only command does not."""
    from agent_mission.__main__ import DENY_RULES, main
    assert main(["help", "accept"]) == 0
    out = capsys.readouterr().out
    assert "--pending" in out and "--under" in out
    assert not any("mission help" in r for r in DENY_RULES), "never denied"

    assert main(["help", "nonsense"]) == 1
    assert "no command" in capsys.readouterr().out


def test_the_refusal_rebuilds_the_command_you_ran(tmp_path, monkeypatch, capsys):
    """A correct refusal that costs three minutes of retyping is one you learn
    to route around."""
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for n in ("one", "two"):
        st = MissionStore(root_for(n))
        st.create(n, str(repo), f"goal {n}", by="human")
        st.set_protected("name", f"mission {n}", by="human")
    monkeypatch.chdir(repo)

    assert main(["pending"]) == 1
    out = capsys.readouterr().out
    assert "mission pending --on 'mission one'" in out
    assert "mission pending --on 'mission two'" in out
    assert "active" in out, "and when each was last touched"
    assert "--session" not in out, "one routing flag in anything a person reads"


def test_session_accepts_a_name_not_only_a_uuid(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("abc123def"))
    st.create("abc123def", "/repo", "the goal", by="human")
    st.set_protected("name", "Career hub", by="human")
    st.propose("an idea", by="agent")

    assert main(["pending", "--session", "Career hub"]) == 0
    assert "1 awaiting you" in capsys.readouterr().out
    assert main(["pending", "--session", "abc123"]) == 0, "prefix works too"


def test_the_tier_rule_is_stated_as_a_rule_not_a_list():
    """A list needs a per-command argument every time something new is added.
    A rule -- idempotent, diff-shown, backed up -- gives the next command an
    obvious home."""
    sec = (Path(__file__).resolve().parents[1] / "docs" / "SECURITY.md").read_text()
    assert "Which tier a command belongs in" in sec
    for prop in ("idempotent", "diff before it applies", "backed up"):
        assert prop in sec
    assert "no setup route at all" in sec, "and the read-only guarantee"


def test_an_outdated_slash_command_is_not_reported_as_installed(tmp_path,
                                                                monkeypatch,
                                                                capsys):
    """The command file is a COPY. It went five sections stale -- detours,
    `mission help`, "never report the plan from memory" -- while --check said
    "installed", which is how other sessions kept working from a picture of
    the tool that no longer matched it."""
    import json as J
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))
    dest = tmp_path / "c"
    dest.mkdir()
    (dest / "mission.md").write_text("an older version of the command file")

    assert main(["setup", "--check", "--settings", str(settings),
                 "--dest", str(dest)]) == 1
    out = capsys.readouterr().out
    assert "outdated" in out and "--force" in out
    assert "missing" not in out.split("slash command")[1].split("\n")[0], \
        "the file is plainly there — calling it missing reads as a lie"


def test_the_boards_javascript_parses():
    """A stray newline inside a confirm() string killed the whole script, so
    the board rendered a header and nothing else -- 203 tests green, page
    blank. Nothing in the suite had ever parsed the JavaScript it ships."""
    import shutil
    import subprocess
    from agent_mission.board import PAGE
    js = PAGE.split("<script>")[1].split("</script>")[0]

    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--check", "-"], input=js,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return

    # No node: catch the specific class anyway -- an unterminated string
    # literal on a single line.
    for n, line in enumerate(js.splitlines(), 1):
        for q in ("'", '"'):
            if line.count(q) % 2 and "\\" + q not in line and "`" not in line:
                raise AssertionError(f"line {n}: odd number of {q}: {line[:70]}")


def test_every_actionable_state_shows_how_to_act_on_it():
    """The reported dead end: the strip said "31 proposals awaiting you",
    clicking `show` appeared to do nothing, and no card said how to accept
    anything. A board that reports a problem and offers no exit is a board that
    teaches you to ignore it."""
    from agent_mission.board import PAGE
    assert "mission accept --pending --on" in PAGE, \
        "a read-only card prints the command that clears it"
    assert "This board is read-only" in PAGE and "mission board" in PAGE, \
        "and says why there are no buttons, once, at the top"
    assert "scrollIntoView" in PAGE and "flash" in PAGE, \
        "`show` takes you to the card instead of only re-filtering"
    assert "user-select:all" in PAGE, "the command is one click to select"


def test_proposals_render_above_the_agreed_plan():
    """Mixed into the plan, a proposal reads as a task you already signed up
    for. The one thing on a card that asks something of you should not have to
    be hunted for among the things that do not."""
    from agent_mission.board import PAGE
    assert "waiting on you</h3>" in PAGE
    assert PAGE.index("class=asks") < PAGE.index("<ul class=chk>${agreed"), \
        "the asking block is emitted before the agreed list"


def test_setup_never_wraps_its_own_wrapper(tmp_path, monkeypatch, at_a_keyboard):
    """One `mission setup --force` on an already-wrapped statusline produced a
    script that called itself -- infinite recursion -- and lost the original
    command, because only settings.json was backed up and the original lived
    inside the file being overwritten."""
    import json as J
    import subprocess
    from agent_mission import setup_surfaces as S
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({"statusLine": {
        "type": "command", "command": "printf 'branch: main'"}}))
    args = ["setup", "--force", "--settings", str(settings),
            "--dest", str(tmp_path / "c")]

    main(args)
    main(args)          # the second run is the one that broke it
    main(args)

    body = [l for l in S.wrapper_path().read_text().splitlines()
            if l.startswith("mine=")][0]
    assert str(S.wrapper_path()) not in body, "it must not call itself"
    assert "printf 'branch: main'" in body, "the original survives re-runs"
    assert S.wrapped_original() == "printf 'branch: main'"

    out = subprocess.run(["sh", str(S.wrapper_path())], input="{}",
                         capture_output=True, text=True, timeout=5).stdout
    assert "branch: main" in out and "\n" not in out.strip()


def test_the_cli_and_the_board_share_one_setup_implementation(monkeypatch,
                                                              tmp_path):
    """C10 claimed this and it was not true: the board called setup_surfaces
    while cmd_setup kept its own copy. They drifted -- only one had the
    self-reference check -- and the CLI's copy wrapped its own wrapper."""
    import json as J
    from agent_mission import setup_surfaces as S
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    settings = tmp_path / "settings.json"
    settings.write_text(J.dumps({}))

    called = []
    real = S.install
    monkeypatch.setattr(S, "install",
                        lambda *a, **k: (called.append(a[0]), real(*a, **k))[1])
    main(["setup", "--settings", str(settings), "--dest", str(tmp_path / "c")])
    assert "statusline" in called and "deny rules" in called, \
        "the CLI goes through setup_surfaces, not a private copy"


def test_the_readme_puts_the_three_touchpoints_on_the_first_screen():
    """C12e. The command table used to be the fourth thing a stranger read, so
    the answer to "what is this for a person" was twenty rows of CLI flags. The
    rule is one screen: the interview, the statusline, the board -- then a fold,
    and everything an agent needs below it."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    fold = readme.index("# Agent & scripting reference")
    human = readme[:fold]

    assert len(human.splitlines()) <= 70, "the human half outgrew one screen"
    for word in ("interview", "statusline", "board"):
        assert word in human.lower(), f"{word} is not on the first screen"

    # The reference half is below the fold, not above it.
    assert "| command | what it does |" in readme[fold:]
    assert "| command | what it does |" not in human
    assert "Humans use the board; agents use the CLI." in human


def test_the_readme_lists_the_surfaces_setup_actually_installs():
    """It said five for as long as there were four -- the Stop hook was removed
    from setup and the install line kept advertising it."""
    from agent_mission.setup_surfaces import SURFACES
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    line = next(l for l in readme.splitlines() if l.startswith("mission setup "))
    for name in SURFACES:
        assert name in line, f"{name!r} missing from the install line"
    # "Stop hook" was on this list while no such surface existed (C12d cut
    # it). C16 shipped a REAL one -- the claim verifier -- so the stale claim
    # to guard against is now the count, not the phrase.
    for stale in ("five surfaces", "four surfaces"):
        assert stale not in readme, f"README still advertises {stale!r}"


def test_one_addressing_flag_reaches_the_human(capsys):
    """`propose` carried --session, --cwd, --on and --into at once. Four
    targeting flags on one command is the thing this pass removes; the other
    three still work, and none of them is offered."""
    from agent_mission.__main__ import main
    for cmd in ("propose", "accept", "set", "show"):
        assert main(["help", cmd]) == 0
        out = capsys.readouterr().out
        assert "--session" not in out, f"{cmd} --help offers --session"
        assert "--into" not in out, f"{cmd} --help offers --into"
        assert "--cwd" not in out, f"{cmd} --help offers --cwd"


def test_into_still_works_and_means_on(tmp_path, monkeypatch, capsys):
    """Hidden is not removed. Anything scripted against --into keeps running."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    _mission_at(tmp_path, "sess-a", str(tmp_path), "The goal")

    assert main(["propose", "an idea", "--into", "The goal"]) == 0
    capsys.readouterr()
    assert main(["propose", "another", "--on", "The goal"]) == 0
    out = capsys.readouterr().out
    assert "proposed" in out


def test_a_write_standing_in_the_missions_own_directory_still_refuses(
        tmp_path, monkeypatch, capsys):
    """The end-to-end form of the same rule. Standing exactly on the directory
    a mission was opened in is the case the old router served best, and it is a
    write, so it must ask which goal rather than answer from the floor."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _mission_at(tmp_path, "sess-a", str(repo), "The repo mission")
    monkeypatch.chdir(repo)

    assert main(["add", "a task"]) == 1
    out = capsys.readouterr().out
    assert "which goal?" in out
    assert "--on" in out and "The repo mission" in out

    # And the store is untouched: nothing was written on the way to refusing.
    from agent_mission.store import MissionStore, root_for
    assert not MissionStore(root_for("sess-a")).load().checklist


def test_no_remediation_command_the_user_reads_uses_session(tmp_path,
                                                            monkeypatch, capsys):
    """The Phase 4 acceptance said no --session in human-facing examples, and I
    checked the docs but not the CLI's own output. `mission doctor` was printing
    `--on`'s job as `--session {sid[:8]}` -- which truncated a mission NAME to
    eight characters, so the line that exists to be pasted could not be."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    mid = "a-long-mission-name"
    _mission_at(tmp_path, mid, str(tmp_path), "Long name")
    from agent_mission.store import MissionStore, root_for
    MissionStore(root_for(mid)).propose("something", by="agent")

    main(["doctor"])
    out = capsys.readouterr().out
    assert "--session" not in out, "doctor still hands out --session"
    assert f"--on {mid}" in out, "and the name is not truncated"


def test_doctor_reads_the_live_missions_not_their_migrated_twins(
        tmp_path, monkeypatch):
    """`migrate` copies events into missions/<name>/ and leaves the old
    session-keyed directory behind, frozen. doctor walked the old layout, so it
    audited the twins and saw no live mission at all -- career-hub had 64 events
    and doctor read the 27 in its abandoned copy, reporting proposals that were
    accepted two days earlier. The board's review lane reads this."""
    from agent_mission import doctor, missions as M
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    old = MissionStore(root_for("sess-a"))
    old.create("sess-a", str(tmp_path), "the goal", by="human")
    old.propose("stale, accepted since", by="agent")
    M.migrate()

    live = MissionStore(M.missions_root() / M.slug("the goal"))
    live.accept(live.load().unaccepted[0].id, by="human")

    seen = [d.name for d, _ in doctor._logs()]
    assert "sess-a" not in seen, "the migrated twin is still being audited"
    assert any(s != "sess-a" for s in seen), "and the live mission is not"
    assert not [f for f in doctor.findings() if f["what"] == "awaiting you"], \
        "doctor reported a proposal that was accepted on the live mission"


def test_the_review_lane_holds_its_pre_registered_bound(tmp_path, monkeypatch):
    """The lane shipped with a rule written before the data: if it shows what
    `doctor` shows, the eligibility rule is wrong and it should not ship.

    Fixing doctor's scan sent it from 2 items to 42 -- 40 of them one row per
    event of "written from another directory" on a single mission. That check
    was written when cwd ROUTED writes, so a foreign directory meant a misroute.
    Names route now and `--on` from any terminal is the documented normal case,
    so the detector had started measuring the feature."""
    from agent_mission import doctor
    from agent_mission.store import MissionStore, root_for
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/home/repo", "the goal", by="human")
    for i in range(30):
        st.context_cwd = "/somewhere/else"
        st.observe("notes", f"n{i}", by="agent")

    rows = [f for f in doctor.findings()
            if f["what"] == "written from another directory"]
    assert len(rows) <= 1, "one row per mission, with a count -- not per event"
    assert not [r for r in doctor.review()
                if r["what"] == "written from another directory"], \
        "nothing a person cannot close belongs in the lane"
