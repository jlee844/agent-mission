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
