"""Items 1 and 2: the KDF fallback, and a tty that is not a person.

Both exist because a claim in the README was false on the platform the tool
targets: "3.9-3.14" did not hold on macOS's own interpreter, and "writes are
enabled only when a person ran `mission board`" did not hold against
`script(1)`.
"""
import json
import os
from pathlib import Path

import pytest

from agent_mission import passcode as P
from agent_mission import whoami


# ── item 1: the KDF must not depend on how Python was linked ──────────────

def test_a_passcode_round_trips_on_this_interpreter(tmp_path, monkeypatch):
    """The failure was AttributeError at import-of-use, not a wrong hash."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("correct-horse")
    rec = P.load()
    assert rec["algo"] in (P.SCRYPT, P.PBKDF2)
    assert P.verifier(rec)("correct-horse") is True
    assert P.verifier(rec)("wrong-horse-xx") is False


def test_the_record_names_its_own_algorithm_and_is_verified_with_that(
        tmp_path, monkeypatch):
    """A record made where scrypt exists must verify where it does not, and
    the other way round -- otherwise a passcode set on Homebrew python stops
    working under /usr/bin/python3, silently, as a wrong passcode."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))

    # Force the weaker branch even on an OpenSSL build, so this test asserts
    # the same thing on every interpreter instead of only on macOS's.
    monkeypatch.setattr(P, "_HAS_SCRYPT", False)
    P.save("pbkdf2-passcode")
    rec = P.load()
    assert rec["algo"] == P.PBKDF2
    assert "rounds" in rec and rec["rounds"] >= 600_000

    # Now "move" that record to an interpreter that HAS scrypt. The record,
    # not the interpreter, must decide.
    monkeypatch.setattr(P, "_HAS_SCRYPT", hasattr(
        __import__("hashlib"), "scrypt"))
    assert P.verifier(P.load())("pbkdf2-passcode") is True


def test_a_record_written_before_the_algo_field_existed_still_loads(
        tmp_path, monkeypatch):
    """Back-compat: the first records carry no `algo` and were scrypt."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("legacy-passcode")
    rec = json.loads(P.path().read_text())
    if rec["algo"] != P.SCRYPT:
        pytest.skip("no scrypt here, so no legacy record could have been made")
    del rec["algo"]
    P.path().write_text(json.dumps(rec))
    assert P.load()["algo"] == P.SCRYPT
    assert P.verifier(P.load())("legacy-passcode") is True


def test_the_passcode_is_never_in_the_file_under_either_algorithm(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    for has in (False, True):
        if has and not hasattr(__import__("hashlib"), "scrypt"):
            continue
        monkeypatch.setattr(P, "_HAS_SCRYPT", has)
        P.save("never-stored-plain")
        assert "never-stored-plain" not in P.path().read_text()


# ── item 2: a pty is not a keyboard ───────────────────────────────────────

def _as_agent_child(monkeypatch):
    """Exactly what `script -q /dev/null python3 -m agent_mission ...` looks
    like from in here: a real tty, and claude in the parent chain."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(whoami, "ancestors",
                        lambda pid=None: ["script -q /dev/null python3 -m "
                                          "agent_mission set objective x",
                                          "/bin/zsh -c ...",
                                          "node /opt/claude/cli.js --resume"])


def test_looks_like_agent_does_not_fire_on_lookalike_names():
    """A false positive refuses a real person's write, which is worse than
    missing a forgery -- so the match is on a path/token boundary."""
    assert whoami._looks_like_agent("claude --resume") is True
    assert whoami._looks_like_agent("node /opt/x/claude/cli.js") is True
    assert whoami._looks_like_agent("/usr/bin/claudette --go") is False
    assert whoami._looks_like_agent("vim claude-notes/today.md") is False
    assert whoami._looks_like_agent("-zsh") is False
    assert whoami._looks_like_agent("") is False


def test_a_tty_under_claude_code_is_not_a_keyboard(monkeypatch):
    from agent_mission.__main__ import _at_a_keyboard
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr(whoami, "ancestors", lambda pid=None: ["-zsh"])
    assert _at_a_keyboard() is True          # a real terminal still works

    _as_agent_child(monkeypatch)
    assert _at_a_keyboard() is False


def test_the_env_markers_alone_are_enough(monkeypatch):
    from agent_mission.__main__ import _at_a_keyboard
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr(whoami, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _at_a_keyboard() is False


def test_the_documented_override_still_wins(monkeypatch):
    """Its whole purpose is to be the deliberate, recorded lie."""
    from agent_mission.__main__ import _at_a_keyboard
    _as_agent_child(monkeypatch)
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    assert _at_a_keyboard() is True


def test_set_refuses_a_protected_write_from_a_pty_under_an_agent(
        tmp_path, monkeypatch, capsys):
    """The demonstrated attack, as a test: `script` + `mission set`."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission import __main__ as M
    from agent_mission.store import MissionStore

    root = tmp_path / "missions" / "demo"
    st = MissionStore(root)
    st.create("s1", str(tmp_path), "the real objective", by="human")

    _as_agent_child(monkeypatch)
    rc = M.main(["set", "objective", "agent rewrote via script", "--on", "demo"])
    assert rc != 0
    assert st.load().objective == "the real objective"


def test_a_board_started_under_an_agent_is_read_only(monkeypatch):
    """`script -F -q board.log ... board` came up with writes:true and the
    code readable from the log the agent chose."""
    from agent_mission import board as B
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(whoami, "ancestors",
                        lambda pid=None: ["node /opt/claude/cli.js"])

    captured = {}

    class _Srv:
        def __init__(self, *a, **k):
            self.daemon_threads = False

        def serve_forever(self):
            captured["writes"] = B.WRITES.enabled
            raise KeyboardInterrupt

        def server_close(self):
            pass

    import agent_mission.daemon as D
    monkeypatch.setattr(B, "ThreadingHTTPServer", _Srv, raising=False)
    monkeypatch.setattr(D, "identify", lambda port, timeout=0.6: None)
    monkeypatch.setattr(D, "claim", lambda port: None)
    monkeypatch.setattr(D, "release", lambda port: None)
    try:
        B.serve(port=8997)
    except (KeyboardInterrupt, SystemExit, TypeError):
        pass
    assert captured.get("writes") in (False, None)


def test_a_human_event_is_stamped_so_a_pty_is_visible_afterwards(
        tmp_path, monkeypatch):
    """The decisive half: unlike the env override, `script` left NOTHING in
    the log. `mission why` can only report what was recorded."""
    from agent_mission.store import MissionStore
    monkeypatch.setattr(whoami, "ancestors",
                        lambda pid=None: ["node /opt/claude/cli.js"])
    monkeypatch.setattr(whoami, "tty_name", lambda: "/dev/ttys099")

    st = MissionStore(tmp_path / "m")
    st.create("s1", str(tmp_path), "goal", by="human")
    ev = json.loads((tmp_path / "m" / "events.jsonl").read_text()
                    .strip().split("\n")[0])
    assert ev["ancestor_agent"] is True
    assert ev["tty"] == "/dev/ttys099"


def test_an_honest_human_event_carries_no_agent_stamp(tmp_path, monkeypatch):
    """A field that is present on every event tells you nothing."""
    from agent_mission.store import MissionStore
    monkeypatch.setattr(whoami, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setattr(whoami, "tty_name", lambda: "/dev/ttys003")
    st = MissionStore(tmp_path / "m")
    st.create("s1", str(tmp_path), "goal", by="human")
    ev = json.loads((tmp_path / "m" / "events.jsonl").read_text()
                    .strip().split("\n")[0])
    assert "ancestor_agent" not in ev
    assert ev["tty"] == "/dev/ttys003"


def test_an_agent_authority_event_is_not_stamped_at_all(tmp_path, monkeypatch):
    """Only human authority is worth forging, so only it is stamped."""
    from agent_mission.store import MissionStore
    monkeypatch.setattr(whoami, "ancestors",
                        lambda pid=None: ["node /opt/claude/cli.js"])
    st = MissionStore(tmp_path / "m")
    st.create("s1", str(tmp_path), "goal", by="human")
    st.propose("some work", by="agent")
    last = json.loads((tmp_path / "m" / "events.jsonl").read_text()
                      .strip().split("\n")[-1])
    assert last["by"] == "agent"
    assert "ancestor_agent" not in last


# ── item 4: a passcode record is not evidence a person set it ─────────────

def _writable_board(monkeypatch, tmp_path, answers):
    """Start serve() as a person at a tty, feeding `answers` to the prompt."""
    from agent_mission import board as B
    import agent_mission.daemon as D
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(whoami, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setattr(D, "identify", lambda port, timeout=0.6: None)
    monkeypatch.setattr(D, "claim", lambda port: None)
    monkeypatch.setattr(D, "release", lambda port: None)

    it = iter(answers)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(it))
    seen = {}

    class _Srv:
        def __init__(self, *a, **k):
            self.daemon_threads = False

        def serve_forever(self):
            seen["enabled"] = B.WRITES.enabled
            seen["verify"] = B.WRITES.verify is not None
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(B, "ThreadingHTTPServer", _Srv, raising=False)
    try:
        B.serve(port=8993)
    except (KeyboardInterrupt, SystemExit):
        pass
    return seen


def test_the_right_passcode_at_the_terminal_opens_the_board(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("the-real-one")
    seen = _writable_board(monkeypatch, tmp_path, ["the-real-one"])
    assert seen["enabled"] is True
    assert seen["verify"] is True


def test_a_passcode_the_person_does_not_know_leaves_the_board_read_only(
        tmp_path, monkeypatch):
    """The hole: an agent writes a record for a passcode IT chose, then waits
    for the person's own `mission board` to load it."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("agent-chose-this")
    seen = _writable_board(monkeypatch, tmp_path,
                           ["guess1xx", "guess2xx", "guess3xx"])
    assert seen["enabled"] is False
    assert seen["verify"] is False


def test_the_prompt_stops_after_three_tries(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("never-guessed")
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "wrong-one"

    assert P.confirm_at_terminal(P.load(), ask=ask) is False
    assert len(asked) == P.MAX_TRIES


def test_an_eof_at_the_prompt_is_read_only_not_a_crash(tmp_path, monkeypatch):
    """A board started where stdin is closed must come up, saying why."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("some-passcode")

    def ask(prompt):
        raise EOFError

    assert P.confirm_at_terminal(P.load(), ask=ask) is False


def test_no_passcode_record_means_no_prompt_and_a_minted_code(
        tmp_path, monkeypatch):
    """The unchanged path: no record, so the board mints a per-run code and
    must NOT ask for anything."""
    def boom(*a, **k):
        raise AssertionError("asked for a passcode when none is set")

    monkeypatch.setattr("getpass.getpass", boom)
    seen = _writable_board(monkeypatch, tmp_path, [])
    assert seen["enabled"] is True
    assert seen["verify"] is False


def test_the_banner_does_not_promise_the_browser_already_has_it(tmp_path):
    """It said the browser 'should already have it and you may not need to
    type anything' -- which was the invitation this item removes."""
    src = (Path(__file__).resolve().parents[1]
           / "agent_mission" / "board.py").read_text()
    assert "may not need to type anything" not in src


# ── a40a67ad: the record's cost parameters are agent-writable ────────────
#
# Found by a second reader on 2026-10-08. `passcode.json` lives in the
# mission home, which the agent can write, and `verifier` took its word on
# n/r/p/rounds. A planted `{"rounds": 10_000_000_000}` makes the board hang
# inside its own startup check — nobody steals anything, the board just stops,
# which is a denial of service against the one surface the human writes from.

import time

import pytest

from agent_mission import passcode as P


def _rec(**kw):
    base = {"algo": P.PBKDF2, "salt": "00" * 16, "hash": "11" * 32,
            "n": 16384, "r": 8, "p": 1, "rounds": P._PBKDF2_ROUNDS}
    base.update(kw)
    return base


@pytest.mark.parametrize("field,value", [
    ("rounds", 10_000_000_000),
    ("n", 1 << 40),
    ("r", 1 << 20),
    ("p", 1 << 20),
])
def test_an_absurd_cost_is_refused_without_computing_it(field, value):
    algo = P.SCRYPT if field in ("n", "r", "p") else P.PBKDF2
    started = time.time()
    assert P.verifier(_rec(algo=algo, **{field: value}))("anything") is False
    assert time.time() - started < 1.0, \
        f"{field}={value} was computed instead of refused"


@pytest.mark.parametrize("field", ["n", "r", "p", "rounds"])
def test_a_zero_or_negative_cost_is_refused(field):
    assert P.verifier(_rec(**{field: 0}))("anything") is False
    assert P.verifier(_rec(**{field: -1}))("anything") is False


def test_a_missing_or_unparsable_cost_is_refused_not_defaulted():
    rec = _rec()
    del rec["n"]
    assert P.verifier(rec)("anything") is False
    assert P.verifier(_rec(n="lots"))("anything") is False


def test_a_normal_record_still_verifies(tmp_path, monkeypatch):
    """The cap must not break the passcode it exists to protect."""
    # `set_on()` is the DATE the record was written, not the setter — my
    # first version called it as one. `save` writes the record; `load` reads
    # it back, which is also what the board does.
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    P.save("correct horse")
    rec = P.load()
    assert P.verifier(rec)("correct horse") is True
    assert P.verifier(rec)("wrong") is False


def test_the_ceilings_are_above_the_defaults():
    """Or a future cost increase is silently capped back down."""
    assert P._MAX_ROUNDS > P._PBKDF2_ROUNDS * 10
    assert P._MAX_N >= 1 << 20


def test_why_does_not_claim_a_pty_it_never_measured():
    """The stamp records the ancestor, never how the terminal was obtained.

    Comments stripped first: the guard matched the comment recording the old
    wording. Seventh time in this suite that a guard has matched its own
    explanation.
    """
    import ast
    import pathlib as _pl
    src = (_pl.Path(__file__).resolve().parents[1]
           / "agent_mission" / "__main__.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""
    code = ast.unparse(tree)
    assert "in a pty spawned by a coding agent" not in code
    assert "under a coding agent" in code


# ── d3c4c8b9: the stamp must not share the gate's detector ───────────────
#
# A second reader walked out of the parent check on 2026-10-08 with no
# `script`, no override and no log append: a backgrounded subshell exits, the
# python inside it reparents to init, and `pty.spawn` supplies the tty. The
# write recorded `"by":"human"` with NO `ancestor_agent` — because the stamp
# asked the same detector the gate had just failed. A record that disappears
# exactly when it would have mattered is not a record.

def test_orphaning_is_measured_without_asking_the_detector(monkeypatch):
    from agent_mission import whoami as W
    monkeypatch.setattr(W.os, "getppid", lambda: 1)
    assert W.orphaned() is True
    monkeypatch.setattr(W.os, "getppid", lambda: 4242)
    assert W.orphaned() is False


def test_the_bypass_shape_is_stamped_even_with_the_detector_blind(monkeypatch):
    """No agent env, no agent ancestor, a tty, leading its own session.

    ⚠️ This test used to mock `getppid` and assert `tty_without_parent`, and
    it PASSED while the real bypass went unstamped — the writer is a child of
    the orphan, so its ppid is never 1. A mock that encodes the author's wrong
    model proves the model, not the code. The end-to-end test below is the one
    that can fail.
    """
    from agent_mission import whoami as W
    monkeypatch.setattr(W, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setattr(W, "tty_name", lambda: "/dev/ttys015")
    monkeypatch.setattr(W.os, "getsid", lambda _pid: os.getpid())
    prov = W.provenance()
    assert prov.get("ancestor_agent") is None, "the detector is blind here"
    assert prov["session_leader"] is True
    assert prov["tty_without_shell"] is True


def test_an_ordinary_human_write_carries_neither_flag(monkeypatch):
    from agent_mission import whoami as W
    monkeypatch.setattr(W, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setattr(W, "tty_name", lambda: "/dev/ttys003")
    monkeypatch.setattr(W.os, "getppid", lambda: 4242)
    monkeypatch.setattr(W.os, "getsid", lambda _pid: 4000)
    prov = W.provenance()
    for flag in ("orphaned", "session_leader", "tty_without_shell"):
        assert flag not in prov


def test_the_env_half_reads_the_family_not_three_names(monkeypatch):
    """The named triple had fallen behind a product exporting ~25 of them."""
    from agent_mission import whoami as W
    monkeypatch.setattr(W, "ancestors", lambda pid=None: ["-zsh"])
    monkeypatch.setenv("CLAUDE_SOMETHING_NEW", "1")
    assert W.spawned_by_agent() is True
    assert "CLAUDE_SOMETHING_NEW" in W.agent_env()


def test_the_docs_no_longer_say_setsid_reparents():
    """`setsid` leaves the ppid unchanged; only an orphaning reparents."""
    import ast
    import pathlib as _pl
    root = _pl.Path(__file__).resolve().parents[1]
    src = (root / "agent_mission" / "whoami.py").read_text()
    # The module docstring is where the wrong mechanism was named, so this
    # one reads the PROSE deliberately rather than stripping it.
    doc = ast.get_docstring(ast.parse(src)) or ""
    assert "do NOT reparent" in doc
    assert "double fork" in doc
