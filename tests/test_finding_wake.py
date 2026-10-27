"""Who hears that a claim was sent back.

A finding re-queues the work, but the session that claimed it only reads the
board when someone types in it, so the fix sat untaken until a person noticed.
The checker is told to wake the claimant by the title the person sees. A
`checked` leaves nothing to change, so it prompts nobody.
"""
import json

import pytest

from agent_mission import __main__ as MM
from agent_mission import missions as M
from agent_mission.store import MissionStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    apps = tmp_path / "apps" / "org" / "acct"
    apps.mkdir(parents=True)
    monkeypatch.setenv("AGENT_MISSION_CLAUDE_SESSIONS", str(tmp_path / "apps"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    (apps / "local_doer.json").write_text(
        json.dumps({"cliSessionId": "doer", "title": "Career worker"}))
    st = MissionStore(M.missions_root() / "career")
    st.create("h", str(tmp_path), "goal career", by="human")
    (tmp_path / "pack.md").write_text("x")
    iid = st.propose("fix the pack", by="agent")["item_id"]
    st.accept(iid, by="human")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "doer")
    assert MM.main(["claims-done", iid, "done: fixed (pack.md)",
                    "--on", "career"]) == 0
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    return st, iid


def test_a_finding_tells_the_checker_to_wake_the_claimant(setup, capsys):
    st, iid = setup
    capsys.readouterr()
    assert MM.main(["finding", iid, "line 416 still contradicts beat 12",
                    "--on", "career", "--as-session", "checker"]) == 0
    out = capsys.readouterr().out
    assert "has not been told" in out
    assert "“Career worker”" in out, "named by the title the person sees"
    assert "line 416 still contradicts beat 12" in out, "the reason travels"
    assert f"mission take {iid} --on career" in out


def test_checked_prompts_nobody(setup, capsys):
    st, iid = setup
    capsys.readouterr()
    assert MM.main(["checked", iid, "re-read beat 12, agrees",
                    "--on", "career", "--as-session", "checker"]) == 0
    out = capsys.readouterr().out
    assert "has not been told" not in out and "Wake" not in out


def test_no_claimant_means_no_wake_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    st = MissionStore(M.missions_root() / "g")
    st.create("h", str(tmp_path), "goal g", by="human")
    iid = st.propose("work", by="agent")["item_id"]
    st.accept(iid, by="human")
    assert MM.main(["finding", iid, "found by reading", "--on", "g",
                    "--as-session", "checker"]) == 0
    assert "has not been told" not in capsys.readouterr().out
