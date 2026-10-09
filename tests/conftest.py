"""A fresh clone must be green.

The suite passed for days only because every run exported
AGENT_MISSION_I_AM_HUMAN=1 -- the variable whose whole job is to DISABLE the
human gate. So the tests were green under the one condition that turns off the
thing most of them exist to guard, and `pytest` on a clean checkout was red.

This clears it for every test. A test that needs to act as a person says so.
"""
import os

import pytest


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
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


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
    for var in list(os.environ):
        if (var.startswith(("CLAUDE_", "CLAUDECODE", "ANTHROPIC_"))
                or var == "AI_AGENT"):
            monkeypatch.delenv(var, raising=False)
    # The parent chain is the other half and cannot be unset, only replaced.
    monkeypatch.setattr("agent_mission.whoami.ancestors", lambda pid=None: [],
                        raising=False)


@pytest.fixture
def at_a_keyboard(monkeypatch):
    """Act as a person at a terminal, explicitly."""
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
