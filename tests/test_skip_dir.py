"""A 'no' to putting a folder on the board is remembered.

CLAUDE.md asks every new, unattached session whether its folder belongs on
the board. Without a memory, every session in a throwaway folder asks again,
which is the noise the owner said he does not want ("I don't want everything
to go up on there"). So the answer is recorded once, per folder, and the
NOT ATTACHED prompt then says one line instead of asking.

Recordable by an agent on purpose: the flow is that the session asks, the
human answers no, and the SESSION records it. The worst a wrong record can
do is suppress a question -- it grants nothing and hides no work.
"""
import os
from pathlib import Path

import pytest

from agent_mission.__main__ import main


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fresh-unattached")
    # a goal must exist, or NOT ATTACHED has nothing to list
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    MissionStore(M.missions_root() / "real-goal").create("s", str(tmp_path), "x", by="human")
    work = tmp_path / "throwaway"; (work / "sub").mkdir(parents=True)
    return tmp_path, work


def _whereami(cwd, monkeypatch, capsys):
    monkeypatch.chdir(cwd)
    capsys.readouterr()
    main(["whereami", "--full"])
    return capsys.readouterr().out


def test_without_a_no_the_prompt_offers_both(env, monkeypatch, capsys):
    _, work = env
    out = _whereami(work, monkeypatch, capsys)
    assert "mission init --on" in out and "mission attach" in out


def test_a_recorded_no_silences_the_offer(env, monkeypatch, capsys):
    _, work = env
    monkeypatch.chdir(work)
    assert main(["skip-dir"]) == 0
    out = _whereami(work, monkeypatch, capsys)
    assert "mission init --on" not in out, "it must not ask again"
    assert "not for the board" in out, "one line saying why it is quiet"


def test_a_subfolder_is_covered(env, monkeypatch, capsys):
    _, work = env
    monkeypatch.chdir(work); main(["skip-dir"])
    out = _whereami(work / "sub", monkeypatch, capsys)
    assert "mission init --on" not in out


def test_undo_brings_the_question_back(env, monkeypatch, capsys):
    _, work = env
    monkeypatch.chdir(work); main(["skip-dir"]); main(["skip-dir", "--undo"])
    out = _whereami(work, monkeypatch, capsys)
    assert "mission init --on" in out


@pytest.mark.parametrize("too_wide", [str(Path.home()), "/"])
def test_refuses_to_silence_everything(env, monkeypatch, capsys, too_wide):
    """Skipping home or / would silence every session on the machine."""
    assert main(["skip-dir", too_wide]) == 1
    assert "every" in capsys.readouterr().out


def test_an_attached_session_is_unaffected(env, monkeypatch, capsys):
    """It is about the QUESTION, not about hiding a goal you joined."""
    tmp, work = env
    monkeypatch.chdir(work); main(["skip-dir"])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "joined")
    from agent_mission import missions as M
    M.attach("joined", "real-goal")
    out = _whereami(work, monkeypatch, capsys)
    assert "NOT ATTACHED" not in out
