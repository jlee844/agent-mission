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
    live session (seen live: written seconds earlier, shown dead).

    ⚠️ THIS ASSERTS BOTH DIRECTIONS ON PURPOSE, AND THE FIRST VERSION DID NOT.
    `Path.glob` yields directory order, not sorted order, and on the author's
    filesystem it happened to yield the NEWER folder first -- so `hits[0]`
    coincidentally equalled the right answer and the test passed against the
    very bug it was written for. Verified by mutation: restoring
    `return hits[0]` left this file green at 5 passed.

    Whichever order glob returns is therefore fixed for a given tree, so one
    ordering cannot prove the rule. Making each copy the newest in turn does:
    under first-glob-hit exactly one of the two rounds must fail, whatever
    order the filesystem chose.
    """
    import os
    projects = tmp_path / "projects"
    a = projects / "-Users-x-repo"
    b = projects / "-Users-x-repo-sub"
    for d in (a, b):
        d.mkdir(parents=True)
        (d / "abc123.jsonl").write_text("{}\n")
    monkeypatch.setattr(S, "PROJECTS", projects)

    for newest, stale in ((b, a), (a, b)):
        now = time.time()
        os.utime(newest / "abc123.jsonl", (now, now))
        os.utime(stale / "abc123.jsonl", (now - 3600, now - 3600))
        assert S.transcript_for("abc123") == newest / "abc123.jsonl", (
            f"newest transcript must win, but the stale copy in "
            f"{stale.name} was returned")


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
