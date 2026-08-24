"""Three failures found in real use on 2026-08-24, one test file.

A live session read as dead because its transcript moved directories; five
upgrades in four days that no running conversation ever heard about; and a
session that could not open a second goal without a three-command workaround.
Each was filed as a proposal, accepted, and is held here.
"""
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission import missions as M                      # noqa: E402
from agent_mission import session as S                       # noqa: E402
from agent_mission import signal as SIG                      # noqa: E402
from agent_mission.__main__ import main                      # noqa: E402
from agent_mission.store import MissionStore                 # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    return tmp_path


def test_transcript_for_prefers_the_newest_across_project_dirs(tmp_path, monkeypatch):
    """A session resumed from a different cwd writes its transcript into a
    different project folder. First-glob-hit read the stale copy and dimmed a
    live session (seen live: 5fd98e2e, written seconds earlier, shown dead)."""
    projects = tmp_path / "projects"
    old = projects / "-Users-x-repo"
    new = projects / "-Users-x-repo-sub"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "abc123.jsonl").write_text("{}\n")
    (new / "abc123.jsonl").write_text("{}\n{}\n")
    stale = time.time() - 3600
    import os
    os.utime(old / "abc123.jsonl", (stale, stale))
    monkeypatch.setattr(S, "PROJECTS", projects)

    got = S.transcript_for("abc123")
    assert got == new / "abc123.jsonl", "newest transcript must win"


def test_signal_announces_a_mid_session_contract_upgrade_once(tmp_path, monkeypatch):
    """Hooks and the contract land at session boundaries; the signal hook is
    the one channel that reaches a LIVE conversation. Baseline on first look,
    one line on change, silence after."""
    contract = tmp_path / "mission.md"
    contract.write_text("v1")
    monkeypatch.setenv("AGENT_MISSION_CONTRACT", str(contract))

    assert SIG.check("sess-a") == []            # first look: baseline, no line

    past = time.time() - 60
    import os
    os.utime(contract, (past, past))
    assert SIG.check("sess-a") == []            # older/equal: silent

    contract.write_text("v2")                   # upgrade lands mid-session
    lines = SIG.check("sess-a")
    assert len(lines) == 1 and "upgraded mid-session" in lines[0]
    assert "whereami --full" in lines[0], "must tell the agent how to re-read"

    assert SIG.check("sess-a") == []            # once, then silence


def test_signal_upgrade_state_is_per_session(tmp_path, monkeypatch):
    """Two sessions must each hear about the upgrade once — shared state would
    eat the second session's edge."""
    contract = tmp_path / "mission.md"
    contract.write_text("v1")
    monkeypatch.setenv("AGENT_MISSION_CONTRACT", str(contract))
    SIG.check("sess-a")
    SIG.check("sess-b")
    contract.write_text("v2")
    assert len(SIG.check("sess-a")) == 1
    assert len(SIG.check("sess-b")) == 1


def _mission_file(tmp_path, name, objective):
    f = tmp_path / f"{M.slug(name)}.md"
    f.write_text(f"NAME: {name}\nOBJECTIVE: {objective}\nCHECKLIST:\n- one thing\n")
    return f


def test_init_on_a_new_name_creates_a_sibling_goal(tmp_path, monkeypatch, capsys):
    """One session, several goals — the model's own promise. init --on
    <new-name> used to refuse because the session already served a goal; the
    workaround was a seed session id + migrate + attach, three commands where
    the promise says one."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")

    first = _mission_file(tmp_path, "First goal", "the original mission")
    assert main(["init", "--from-file", str(first), "--no-edit",
                 "--no-board"]) == 0
    M.migrate()
    assert main(["init", "--from-file", str(first), "--no-edit",
                 "--no-board"]) == 1            # same session, no --on: refuses

    sib = _mission_file(tmp_path, "Second goal", "the sibling mission")
    assert main(["init", "--on", "second-goal", "--from-file", str(sib),
                 "--no-edit", "--no-board"]) == 0

    mids = {mid for mid, _ in M.all_missions()}
    assert "second-goal" in mids, "the sibling goal must exist"
    assert M.attachments().get("sess-1") == "second-goal", \
        "the session re-attaches to the goal it just opened"
    got = MissionStore(M.missions_root() / "second-goal").load()
    assert got.objective == "the sibling mission"
    first_mid = next(m for m in mids if m != "second-goal")
    kept = MissionStore(M.missions_root() / first_mid).load()
    assert kept.objective == "the original mission", \
        "creating a sibling must not touch the first goal"


def test_init_on_an_existing_goal_still_refuses_to_clobber(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    first = _mission_file(tmp_path, "First goal", "the original mission")
    assert main(["init", "--from-file", str(first), "--no-edit",
                 "--no-board"]) == 0
    M.migrate()
    again = _mission_file(tmp_path, "First goal again", "an overwrite attempt")
    assert main(["init", "--on", "first-goal", "--from-file", str(again),
                 "--no-edit", "--no-board"]) == 1, \
        "--on naming an EXISTING goal keeps the old refuse-to-clobber path"
