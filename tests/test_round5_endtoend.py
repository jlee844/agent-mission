"""The round-5 bypass, run for real against a throwaway store.

Why this file exists, and it is the most important sentence in it: the unit
test for this stamp MOCKED `getppid` and passed, while the actual bypass went
through unstamped. The mock encoded my model of the attack, so it proved the
model. A second reader re-ran the attack and measured that the WRITING process
is a child of the orphan — ppid 2071, not 1 — which `getppid() <= 1` can never
catch.

So this test performs the attack: a backgrounded subshell exits so its child
reparents, `pty.spawn` supplies a terminal, and the three named environment
variables are stripped. Then it reads the event log and asserts the write is
marked. It is slow and it shells out, and that is the price of a test that can
fail when the claim is false.

Everything happens inside a tmp AGENT_MISSION_HOME. ⚠️ The suite used to
write to the author's REAL store from one forgotten test; conftest now gives
every test its own home, and this file double-checks its own.
"""
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="no pty/getsid on Windows")


def _events(home: Path, goal: str) -> list[dict]:
    log = home / "missions" / goal / "events.jsonl"
    if not log.exists():
        return []
    out = []
    for line in log.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_MISSION_HOME", str(home))
    assert str(home) != str(Path.home() / ".agent-mission")
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    st = MissionStore(M.missions_root() / "demo")
    st.create("s1", str(tmp_path), "the real objective", by="human",
              typed_by="human")
    return home


def _run_bypass(home: Path, text: str) -> None:
    """Orphan, take a pty, and write — the attack exactly as reported."""
    script = home.parent / "bypass.py"
    script.write_text(textwrap.dedent(f"""
        import pty, sys, time
        time.sleep(1.5)                       # let the subshell exit first
        pty.spawn([{sys.executable!r}, "-m", "agent_mission", "set",
                   "objective", {text!r}, "--on", "demo"])
    """))
    cmd = (f"( env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID "
           f"-u CLAUDE_CODE_ENTRYPOINT "
           f"{sys.executable} {script} </dev/null >/dev/null 2>&1 & )")
    env = dict(os.environ, AGENT_MISSION_HOME=str(home),
               PYTHONPATH=str(ROOT))
    subprocess.run(["/bin/sh", "-c", cmd], env=env, cwd=str(ROOT),
                   timeout=30, check=False)
    for _ in range(80):                       # it sleeps 1.5s before writing
        time.sleep(0.25)
        if any(e.get("kind") == "set" for e in _events(home, "demo")):
            return


def test_the_bypass_still_works_and_is_now_marked(store):
    """Two assertions, and the first one matters as much as the second.

    The gate does not hold — this is a speed bump, and a test asserting it
    held would be the overclaim all over again. What must be true is that the
    write cannot be mistaken for a person's afterwards.
    """
    # ⚠️ The condition this runs under, which took two wrong conclusions to
    # pin down: conftest clears every CLAUDE_*/ANTHROPIC_*/AI_AGENT variable
    # for every test, so the subprocess inherits an environment with NO agent
    # markers — i.e. an attacker who stripped them all, not just the three
    # the original report named. That is the honest scenario, because the env
    # half is the weak one (`env -i` defeats it) and the session-leader stamp
    # is the only thing left. Run by hand with those variables present, the
    # same command is REFUSED, which is worth knowing and is not what this
    # test is for.
    assert not [k for k in os.environ
                if k.startswith(("CLAUDE_", "ANTHROPIC_")) or k == "AI_AGENT"], \
        "this test needs the stripped-environment case; conftest provides it"
    _run_bypass(store, "rewritten by the bypass")
    sets = [e for e in _events(store, "demo") if e.get("kind") == "set"]
    # NOT a skip. A skip here is how a test that proves nothing stays green —
    # which is the exact failure mode this file was written to end.
    assert sets, (
        "the bypass did not complete, so this test proved nothing. Do not "
        "convert this back into a skip: find out why it did not run.")

    ev = sets[-1]
    assert ev.get("by") == "human", (
        "the gate is a speed bump and this test does not pretend otherwise; "
        "if this now fails, the gate got stronger and the claim can be raised")
    assert ev.get("tty"), "it took a pty, so the event should record one"
    assert ev.get("tty_without_shell") is True, (
        "THE REGRESSION THIS FILE EXISTS FOR: the write went through "
        "unmarked, exactly as it did on 2026-10-08. `mission why` would "
        "show it as indistinguishable from a person at a terminal.")


def test_an_ordinary_agent_write_is_refused_not_merely_marked(store):
    """The speed bump still bumps: no pty, no write."""
    out = subprocess.run(
        [sys.executable, "-m", "agent_mission", "set", "objective",
         "rewritten plainly", "--on", "demo"],
        capture_output=True, text=True, cwd=str(ROOT),
        env=dict(os.environ, AGENT_MISSION_HOME=str(store),
                 PYTHONPATH=str(ROOT)))
    assert out.returncode == 1
    assert "refusing" in out.stdout
    assert not [e for e in _events(store, "demo") if e.get("kind") == "set"]
