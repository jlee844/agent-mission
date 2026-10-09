"""The board you can get back into.

Two defects, one lockout. A board started from `.zshrc` runs as the CONSOLE
SCRIPT (`.../bin/mission board --rc`), and `_is_board` demanded the literal
string `agent_mission`, which only `python -m agent_mission board` produces --
so `--stop` disowned the person's own board, the port stayed held by a
terminal that had since closed, and the write code was gone with it: freshly
minted per board, in memory, printed to a tty that no longer existed.
"""
import pytest

from agent_mission import actions


# ---- the guard that refused to stop the person's own board ---------------

def _match(cmdline, monkeypatch):
    from agent_mission import daemon
    class R:
        stdout = cmdline
    monkeypatch.setattr(daemon.subprocess, "run", lambda *a, **k: R())
    return daemon._is_board(1234)


def test_a_console_script_board_is_recognised(monkeypatch):
    """The shape that actually runs on this machine, from .zshrc."""
    assert _match("/usr/bin/python3 /Users/x/.local/bin/mission board --rc",
                  monkeypatch)
    assert _match("/Users/x/.local/bin/mission board", monkeypatch)


def test_the_module_form_still_is(monkeypatch):
    assert _match("/usr/bin/python3 -m agent_mission board --port 8976",
                  monkeypatch)


def test_an_unrelated_process_is_not(monkeypatch):
    """A pid is recycled the moment it dies, so this guard is the only thing
    between a stale record and signalling something else entirely."""
    assert not _match("/usr/bin/sleep 30", monkeypatch)
    assert not _match("/sbin/launchd", monkeypatch)
    assert not _match("/usr/bin/python3 /Users/x/bin/missionary board", monkeypatch)


def test_board_must_be_its_own_argument(monkeypatch):
    """`_is_board` inside a one-liner is not a running board -- the first
    version of this fix matched exactly that, on its first test."""
    assert not _match("python3 -c from agent_mission.daemon import _is_board",
                      monkeypatch)


def test_it_returns_a_bool(monkeypatch):
    """It was returning a re.Match, which is truthy and not a bool."""
    assert _match("/x/bin/mission board", monkeypatch) is True


# ---- a passcode you set, so a closed terminal is not a lockout -----------

def test_a_saved_passcode_is_never_stored_in_the_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    pc.save("correct horse battery")
    raw = pc.path().read_text(encoding="utf-8")
    assert "correct horse battery" not in raw
    assert "correct" not in raw and "horse" not in raw
    rec = pc.load()
    assert pc.verifier(rec)("correct horse battery")
    assert not pc.verifier(rec)("correct horse batter")
    assert not pc.verifier(rec)("")


def test_a_passcode_too_short_to_survive_the_file_being_read(tmp_path, monkeypatch):
    """The realistic attack is offline: the agent runs as you and can read
    this file. Six characters would not survive that."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    with pytest.raises(ValueError):
        pc.save("short")
    assert not pc.is_set()


def test_the_board_takes_the_saved_passcode_and_mints_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    pc.save("correct horse battery")
    s = actions.Session(enabled=True, verify=pc.verifier(pc.load()))
    assert s.saved and s.code == "", \
        "a saved passcode must not sit in memory in the clear"
    s.check("correct horse battery")
    with pytest.raises(actions.Unauthorised):
        s.check("wrong one entirely")


def test_the_same_passcode_works_after_a_restart(tmp_path, monkeypatch):
    """The whole point. A new board used to mint a new random code, which
    silently invalidated what the browser had stored."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    pc.save("correct horse battery")
    first = actions.Session(enabled=True, verify=pc.verifier(pc.load()))
    second = actions.Session(enabled=True, verify=pc.verifier(pc.load()))
    first.check("correct horse battery")
    second.check("correct horse battery")


def test_guessing_is_still_locked_out(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    pc.save("correct horse battery")
    s = actions.Session(enabled=True, verify=pc.verifier(pc.load()))
    for _ in range(actions.MAX_WRONG):
        with pytest.raises(actions.Unauthorised):
            s.check("nope nope nope")
    with pytest.raises(actions.Unauthorised) as e:
        s.check("correct horse battery")
    assert "locked" in str(e.value), "the right passcode must not unlock a lockout"


def test_a_read_only_board_is_still_read_only_with_a_passcode(tmp_path, monkeypatch):
    """The tty gate is untouched and this is the load-bearing claim: knowing
    the passcode is NOT proof a person is here, because an agent runs as you
    and could write a passcode file of its own."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    pc.save("correct horse battery")
    s = actions.Session(enabled=False, verify=pc.verifier(pc.load()))
    with pytest.raises(actions.Unauthorised) as e:
        s.check("correct horse battery")
    assert "read-only" in str(e.value)


def test_no_passcode_keeps_the_throwaway_code(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import passcode as pc
    assert pc.load() is None
    s = actions.Session(enabled=True)
    assert not s.saved and len(s.code) == 6
    s.check(s.code)


def test_setting_one_needs_a_person(tmp_path, monkeypatch, capsys):
    """Human-only for the same reason `set` is: an agent that can choose the
    passcode can use the write buttons."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_MISSION_I_AM_HUMAN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["passcode"]) == 1
    assert "only a person" in capsys.readouterr().out
    from agent_mission import passcode as pc
    assert not pc.is_set()


def test_stop_finds_a_board_whose_record_was_erased(monkeypatch, tmp_path):
    """The two halves of the lockout compound: the refusal path DELETES the
    record, so the same bug that disowned the board also erased the note
    saying where it was. Recovery has to work from the port alone."""
    from agent_mission import daemon
    killed = []
    monkeypatch.setattr(daemon, "running", lambda: None)
    monkeypatch.setattr(daemon, "identify",
                        lambda p, **k: {"pid": 4242} if p == 8976 else None)
    monkeypatch.setattr(daemon, "_is_board", lambda pid: pid == 4242)
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(daemon, "_record", lambda: tmp_path / "none.json")
    assert daemon.stop() is True
    assert killed == [4242]


def test_stop_with_no_board_anywhere_is_not_an_error(monkeypatch):
    from agent_mission import daemon
    monkeypatch.setattr(daemon, "running", lambda: None)
    monkeypatch.setattr(daemon, "identify", lambda p, **k: None)
    assert daemon.stop() is False
