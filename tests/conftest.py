"""A fresh clone must be green.

The suite passed for days only because every run exported
AGENT_MISSION_I_AM_HUMAN=1 -- the variable whose whole job is to DISABLE the
human gate. So the tests were green under the one condition that turns off the
thing most of them exist to guard, and `pytest` on a clean checkout was red.

This clears it for every test. A test that needs to act as a person says so.
"""
import os
from pathlib import Path

import pytest


def _real_home() -> Path:
    return Path.home() / ".agent-mission"


# Written by processes that are NOT this suite and legitimately change while
# it runs: other sessions' hooks beat into `heartbeats/`, and a live board
# re-adopts its own record. Including them would make the guard cry wolf,
# and a guard that cries wolf gets deleted.
_CONCURRENT = {"heartbeats", "board.json", "board.log"}


def _snapshot() -> dict:
    home = _real_home()
    if not home.exists():
        return {}
    out = {}
    for p in home.rglob("*"):
        rel = p.relative_to(home)
        if rel.parts and rel.parts[0] in _CONCURRENT:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out[str(rel)] = (st.st_size, int(st.st_mtime))
    return out


@pytest.fixture(scope="session", autouse=True)
def _the_real_store_is_never_touched():
    """Fail the RUN if the suite changed the user's actual store.

    ⚠️ This exists because it happened. One test called `board.serve(8976)`
    with no AGENT_MISSION_HOME, so every run of this suite rewrote the real
    `board.html` and DELETED `board.json` — while a board was serving from
    that store. It was found by a second reader comparing an mtime, not by
    anything here, and the per-test home below would have prevented THAT one
    while proving nothing about the next.

    So the isolation is enforced rather than merely provided: snapshot before,
    compare after, and fail if anything moved. Entries other processes
    legitimately write are excluded by name, and that exclusion is the only
    thing standing between this guard and a false alarm — keep it narrow.
    """
    before = _snapshot()
    # ⚠️ The excluded set contains `board.json` — which is EXACTLY the file
    # the original incident deleted. A review session caught that the guard
    # as first written would not have caught the bug it was written for.
    # Other processes do legitimately REWRITE it, so it cannot be snapshotted
    # like the rest; but nothing in this suite should ever make it VANISH,
    # so disappearance gets its own narrower check below.
    record = _real_home() / "board.json"
    record_before = record.exists()
    yield
    if record_before and not record.exists():
        raise AssertionError(
            "THE SUITE DELETED ~/.agent-mission/board.json — the board's "
            "daemon record, and the exact file the 2026-10-08 incident "
            "removed on every run. One legitimate cause exists: a live "
            "process calling daemon.running() on a STALE record unlinks it. "
            "If no board was running, that is not what happened here.")
    after = _snapshot()
    gone = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(k for k in set(before) & set(after)
                     if before[k] != after[k])
    assert not (gone or added or changed), (
        "THE SUITE TOUCHED THE REAL STORE at ~/.agent-mission — "
        f"deleted={gone[:5]} added={added[:5]} changed={changed[:5]}. "
        "Every test gets its own AGENT_MISSION_HOME; something ignored it.")


@pytest.fixture(autouse=True)
def _never_the_real_store(tmp_path_factory, monkeypatch):
    """No test may touch ~/.agent-mission. Not one.

    ⚠️ This is not hygiene, it is damage repair. `test_writes_are_off_unless
    _stdout_is_a_terminal` called `board.serve(8976)` with no
    AGENT_MISSION_HOME, so every run of the suite rewrote the real
    `board.html` and DELETED `board.json` — the author's own store, while a
    board was serving from it. Found by a second reader on 2026-10-08, who
    could point at the mtime. The mission logs were untouched and `running()`
    re-adopted the live board, so the damage was bounded; it was bounded by
    luck, not by design.
    
    Each test gets its OWN home, so a test that forgets is isolated rather
    than merely quieter. A test wanting a specific home still sets it — this
    runs first and is overridden by a later `monkeypatch.setenv`.
    """
    home = tmp_path_factory.mktemp("agent-mission-home")
    monkeypatch.setenv("AGENT_MISSION_HOME", str(home))


@pytest.fixture(autouse=True)
def _no_ambient_human_override(monkeypatch):
    monkeypatch.delenv("AGENT_MISSION_I_AM_HUMAN", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_session_id(monkeypatch):
    """Claude Code exports CLAUDE_CODE_SESSION_ID into every shell, including
    the one running pytest. So every resolution test silently resolved through
    the harness's own session and never took the path a fresh checkout takes --
    168 green locally, red on CI, for a month.

    A test that needs a session id sets one.
    """
    # Every variable the code reads an id from, by the code's own list:
    # widening session.SESSION_ENV to CODEX_THREAD_ID without this made 14
    # tests fail whenever the suite ran inside a Codex thread.
    from agent_mission.session import SESSION_ENV
    for var in SESSION_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_agent_markers(monkeypatch):
    """whoami.spawned_by_agent() reads the environment, and the environment
    running this suite under Claude Code says "agent" -- so every assertion
    about the honest path would pass on CI and fail locally, which is the same
    split the two fixtures above exist to close.

    A test that needs to look like an agent monkeypatches it.

    ⚠️ Clears the whole FAMILY, not two names. `whoami.agent_env` was widened
    on 2026-10-08 to every CLAUDE_*/ANTHROPIC_* variable plus AI_AGENT,
    because the named triple it replaced had fallen behind a product that now
    exports about 25 of them — and a list that is merely out of date is the
    weakest reason to call a write human. The moment it widened, four tests
    asserting the HONEST path started seeing the runner's own environment.
    Clearing by the same rule the code reads keeps the two in step.
    """
    from agent_mission.whoami import _AGENT_ENV_EXACT, _AGENT_ENV_PREFIX
    for var in list(os.environ):
        if var.startswith(_AGENT_ENV_PREFIX) or var in _AGENT_ENV_EXACT:
            monkeypatch.delenv(var, raising=False)
    # The parent chain is the other half and cannot be unset, only replaced.
    monkeypatch.setattr("agent_mission.whoami.ancestors", lambda pid=None: [],
                        raising=False)


@pytest.fixture
def at_a_keyboard(monkeypatch):
    """Act as a person at a terminal, explicitly."""
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
