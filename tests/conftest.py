"""A fresh clone must be green.

The suite passed for days only because every run exported
AGENT_MISSION_I_AM_HUMAN=1 -- the variable whose whole job is to DISABLE the
human gate. So the tests were green under the one condition that turns off the
thing most of them exist to guard, and `pytest` on a clean checkout was red.

This clears it for every test. A test that needs to act as a person says so.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_ambient_human_override(monkeypatch):
    monkeypatch.delenv("AGENT_MISSION_I_AM_HUMAN", raising=False)


@pytest.fixture
def at_a_keyboard(monkeypatch):
    """Act as a person at a terminal, explicitly."""
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
