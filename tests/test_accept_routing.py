"""Who hears that work was accepted.

It went to every session on every goal, so a session attached to one goal was
told to "take" another goal's item, and the person read it as filed on the
wrong goal. Now: the goal's own sessions are told to take it; the session
that ASKED for it from elsewhere is told how to wake the goal's session, by
the title the person sees; everyone else hears nothing.
"""
import json

import pytest

from agent_mission import missions as M
from agent_mission import signal as S
from agent_mission.store import MissionStore


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    apps = tmp_path / "apps" / "org" / "acct"
    apps.mkdir(parents=True)
    monkeypatch.setenv("AGENT_MISSION_CLAUDE_SESSIONS", str(tmp_path / "apps"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()

    def app(sid, title):
        (apps / f"local_{sid}.json").write_text(
            json.dumps({"cliSessionId": sid, "title": title}))

    def goal(name):
        st = MissionStore(M.missions_root() / name)
        st.create("h", "/w", f"goal {name}", by="human")
        return st
    return type("B", (), {"app": staticmethod(app), "goal": staticmethod(goal),
                          "codex": tmp_path / "codex"})


def _settle(*sids):
    for s in sids:
        S.check(s)


def test_only_the_goals_own_sessions_are_told_to_take_it(board):
    career = board.goal("career")
    board.goal("tools")
    M.attach("career-worker", "career")
    M.attach("tools-worker", "tools")
    _settle("career-worker", "tools-worker")
    i = career.propose("review the book", by="agent")["item_id"]
    _settle("career-worker", "tools-worker")
    career.accept(i, by="human")
    assert any(f"mission take {i}" in l for l in S.check("career-worker"))
    assert not any(i in l for l in S.check("tools-worker")), \
        "a session on another goal must not be told to take it"


def test_the_asker_on_another_goal_is_told_to_wake_it_by_title(board):
    career = board.goal("career")
    board.goal("tools")
    M.attach("asker", "tools")
    M.attach("career-worker", "career")
    board.app("career-worker", "Next project evaluation framework")
    _settle("asker", "career-worker")
    i = career.propose("Review the toolkit book: suggest edits",
                       by="agent", from_session="asker")["item_id"]
    _settle("asker")
    career.accept(i, by="human")
    lines = S.check("asker")
    assert len(lines) == 1, lines
    line = lines[0]
    assert "your request" in line and "ACCEPTED" in line
    assert "“Next project evaluation framework”" in line
    assert f"mission take {i} --on career" in line, "a ready message"
    assert S.check("asker") == [], "edge-triggered, once"


def test_a_codex_target_is_named_but_not_promised_a_message(board):
    career = board.goal("career")
    board.goal("tools")
    M.attach("asker", "tools")
    M.attach("01a1-codex", "career")
    (board.codex / "session_index.jsonl").write_text(
        json.dumps({"id": "01a1-codex", "thread_name": "Border thread"}) + "\n")
    _settle("asker")
    i = career.propose("x", by="agent", from_session="asker")["item_id"]
    _settle("asker")
    career.accept(i, by="human")
    line = S.check("asker")[0]
    assert "Codex thread “Border thread”" in line
    assert "cannot be messaged" in line
    assert "cross-session message to" not in line, \
        "no promise to wake what cannot be messaged"


def test_a_goal_nobody_serves_says_so(board):
    career = board.goal("career")
    board.goal("tools")
    M.attach("asker", "tools")
    _settle("asker")
    i = career.propose("x", by="agent", from_session="asker")["item_id"]
    _settle("asker")
    career.accept(i, by="human")
    assert "no session serves that goal" in S.check("asker")[0]


def test_an_asker_attached_to_the_same_goal_just_takes_it(board):
    g = board.goal("career")
    M.attach("me", "career")
    _settle("me")
    i = g.propose("x", by="agent", from_session="me")["item_id"]
    _settle("me")
    g.accept(i, by="human")
    lines = S.check("me")
    assert len(lines) == 1 and f"mission take {i}" in lines[0], lines
    assert "your request" not in lines[0]


def test_the_item_remembers_who_asked(board):
    g = board.goal("career")
    i = g.propose("x", by="agent", from_session="asker")["item_id"]
    item = next(x for x in g.load().items if x.id == i)
    assert item.from_session == "asker"


def test_the_board_shows_each_sessions_app_title(board, monkeypatch):
    from agent_mission import board as B
    board.goal("career")
    M.attach("career-worker", "career")
    board.app("career-worker", "Next project evaluation framework")
    monkeypatch.setattr(B, "transcript_for", lambda sid: None)
    rows = [r for r in B.mission_rows() if r["id"] == "career"]
    s = rows[0]["sessions"][0]
    assert s["app"] == "Next project evaluation framework"
    assert s["app_kind"] == "claude-desktop"
    assert "x.app" in B.PAGE, "the page renders it"
