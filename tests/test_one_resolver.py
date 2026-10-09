"""Item 7: one relative path, three directories, three verdicts.

`done: X (src/a.py)` could read "disk agrees" where the agent wrote it and
"NOT on disk" on the board — because the Stop hook resolved against the
SESSION's cwd, `claims-done` against the AGENT PROCESS's cwd, and the board
against the MISSION's. Both verifiers also fell back to the server process's
own cwd when handed "".

The pack's story ("my own ten claims came back NOT backed… against the
mission's cwd") is precisely this class of bug.
"""
import json
import os
from pathlib import Path

import pytest

from agent_mission import claims as C


def _claim(artifact: str) -> C.Claim:
    """A contract-format claim naming one artifact."""
    return C.Claim(session_id="s1", block_index=0,
                   sentence=f"done: shipped ({artifact})",
                   mentions_tests=False, artifact=artifact)


def test_absolute_paths_are_never_rebased():
    p = Path("/tmp/x/a.py")
    assert C.resolve(str(p), mission_cwd="/other", session_cwd="/third") == p


def test_the_mission_cwd_wins_over_the_session_cwd():
    """Same answer from every surface is the whole point."""
    got = C.resolve("src/a.py", mission_cwd="/repo", session_cwd="/elsewhere")
    assert got == Path("/repo/src/a.py")


def test_the_session_cwd_is_the_fallback_not_the_default():
    """A claim made before any mission names a directory still resolves."""
    assert C.resolve("src/a.py", session_cwd="/elsewhere") \
        == Path("/elsewhere/src/a.py")


def test_the_process_cwd_is_never_used(tmp_path, monkeypatch):
    """The one that made the verdict depend on who was asking."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x\n")
    got = C.resolve("a.py")
    assert got == Path("a.py"), "unresolved, not resolved against os.getcwd()"
    assert not got.is_absolute()


@pytest.fixture
def three_cwds(tmp_path, monkeypatch):
    """A mission whose cwd holds the artifact, and two other directories."""
    from agent_mission import missions as M
    from agent_mission.store import MissionStore

    home = tmp_path / "store"
    monkeypatch.setenv("AGENT_MISSION_HOME", str(home))
    mission_cwd = tmp_path / "repo"
    (mission_cwd / "src").mkdir(parents=True)
    (mission_cwd / "src" / "a.py").write_text("x\n")
    session_cwd = tmp_path / "somewhere-else"
    session_cwd.mkdir()
    process_cwd = tmp_path / "third"
    process_cwd.mkdir()

    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(mission_cwd), "the goal", by="human")
    M.attach("s1", "g")
    return st, mission_cwd, session_cwd, process_cwd


def test_all_three_surfaces_agree_on_one_claim(three_cwds, monkeypatch,
                                               capsys):
    """The parametrised check the spec's Prove line names: identical text,
    three different process cwds, one verdict."""
    st, mission_cwd, session_cwd, process_cwd = three_cwds
    from agent_mission import __main__ as MM

    text = "done: shipped (src/a.py)"
    iid = st.propose("the work", by="agent")["item_id"]
    st.accept(iid, by="human")

    verdicts = []
    for where in (mission_cwd, session_cwd, process_cwd):
        monkeypatch.chdir(where)

        # 1. `claims-done`, run by the agent from `where`.
        rc = MM.main(["claims-done", iid, text, "--on", "g"])
        assert rc == 0
        out = capsys.readouterr().out
        verdicts.append(("claims-done", "NOT on disk" not in out))

        # 2. the board / sweep path.
        m = st.load()
        v = C.verdict_for(text, cwd=m.cwd or "")
        verdicts.append(("board", v["exists"]))

        # 3. the Stop hook, whose payload cwd is the SESSION's.
        claim = _claim("src/a.py")
        got = C.verify(claim, cwd=str(session_cwd),
                       mission_cwd=str(mission_cwd))
        verdicts.append(("hook", got.status == "backed"))

    kinds = {k for k, _ in verdicts}
    assert kinds == {"claims-done", "board", "hook"}
    assert all(ok for _, ok in verdicts), (
        f"the same claim got different answers: {verdicts}")


def test_a_claim_outside_the_mission_cwd_is_unbacked_everywhere(three_cwds):
    """The fix must not make everything pass: a path that is really absent
    stays absent, from every surface."""
    st, mission_cwd, session_cwd, _ = three_cwds
    text = "done: shipped (src/ghost.py)"
    m = st.load()
    assert C.verdict_for(text, cwd=m.cwd or "")["exists"] is False
    claim = _claim("src/ghost.py")
    assert C.verify(claim, cwd=str(session_cwd),
                    mission_cwd=str(mission_cwd)).status == "unbacked"


def test_a_file_only_in_the_session_cwd_does_not_count_as_backed(three_cwds):
    """Mission cwd FIRST means a stray file beside the session is not
    evidence for a claim about the goal."""
    st, mission_cwd, session_cwd, _ = three_cwds
    (session_cwd / "src").mkdir()
    (session_cwd / "src" / "decoy.py").write_text("x\n")
    claim = _claim("src/decoy.py")
    got = C.verify(claim, cwd=str(session_cwd), mission_cwd=str(mission_cwd))
    assert got.status == "unbacked"
