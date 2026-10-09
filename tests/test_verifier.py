"""The verification session: a second session, not a second reader.

Its whole value is structural — a fresh context, no access to the doer's
reasoning, forced to start from the artifact — and that is WEAKER than
independence, because it is the same model reading the same disk. Every
surface that prints its verdict has to say so, or a second guess gets
laundered as a check. This repo has the precedent: probe-lab's error-gate
review needed "a second reader" because Claude wrote both the detectors and
the transcripts they ran on.
"""
import pytest

from agent_mission import board as B
from agent_mission import missions as M
from agent_mission.store import MissionStore


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    """An accepted item with a claim whose artifact really moved."""
    import os
    import time
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setattr(B, "live", lambda: [])
    repo = tmp_path / "repo"
    repo.mkdir()
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(repo), "Ship the router", by="human")
    iid = st.propose("wire the lease", by="agent")["item_id"]
    st.accept(iid, by="human")
    art = repo / "store.py"
    art.write_text("x\n")
    future = time.time() + 2
    os.utime(art, (future, future))
    st.claim_done(iid, "done: wired it (store.py)", by="agent",
                  session_id="workerA")
    return st, iid


# ── it cannot do the things the role forbids ─────────────────────────────

def test_a_verifier_cannot_tick(claimed):
    """`done` has one writer, and it is not a session."""
    st, iid = claimed
    from agent_mission.store import ProtectedFieldError
    with pytest.raises(ProtectedFieldError):
        st.complete(iid, by="agent")


def test_the_session_that_claimed_it_cannot_check_it(claimed):
    """Self-grading is the failure this role exists to separate, so it is
    refused rather than labelled."""
    st, iid = claimed
    with pytest.raises(ValueError, match="cannot be the one that checks"):
        st.checked(iid, "looks right to me", session_id="workerA")


def test_a_different_session_can(claimed):
    st, iid = claimed
    st.checked(iid, "read store.py: lease/release/finding are all there",
               session_id="verifier9")
    item = st.load().leaves[0]
    assert item.checked_by == "verifier9"
    assert "lease/release/finding" in item.checked_note


def test_an_unexplained_agreement_is_refused(claimed):
    """A bare 'agrees' is the rubber stamp this exists to avoid."""
    st, iid = claimed
    with pytest.raises(ValueError, match="say what you checked"):
        st.checked(iid, "   ", session_id="verifier9")


def test_there_must_be_a_claim_to_check(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    iid = st.propose("work", by="agent")["item_id"]
    st.accept(iid, by="human")
    with pytest.raises(ValueError, match="nothing claimed"):
        st.checked(iid, "fine", session_id="verifier9")


# ── a check changes nothing; a finding changes the lane ──────────────────

def test_agreeing_moves_no_lane_and_ticks_nothing(claimed):
    st, iid = claimed
    before = st.load()
    st.checked(iid, "read it, it holds", session_id="verifier9")
    after = st.load()
    assert before.lane_of(before.leaves[0]) == "review"
    assert after.lane_of(after.leaves[0]) == "review", \
        "an opinion must not advance anything"
    assert after.leaves[0].done is False


def test_disagreeing_requeues_and_drops_the_opinion(claimed):
    """A finding supersedes a check: they are about the same claim, and the
    claim is gone."""
    st, iid = claimed
    st.checked(iid, "looks fine", session_id="verifier9")
    st.finding(iid, "the lease has no TTL", session_id="verifier9")
    item = st.load().leaves[0]
    assert st.load().lane_of(item) == "ready"
    assert item.checked_note == "" and item.checked_by == ""
    assert "no TTL" in item.rework


def test_a_new_claim_drops_an_old_check(claimed):
    """The check was of a different claim."""
    st, iid = claimed
    st.checked(iid, "fine", session_id="verifier9")
    st.finding(iid, "send it back", session_id="verifier9")
    st.claim_done(iid, "done: redone it (store.py)", by="agent",
                  session_id="workerB")
    item = st.load().leaves[0]
    assert item.checked_note == ""
    assert item.claimed_by == "workerB"


def test_a_human_tick_clears_the_opinion_too(claimed):
    st, iid = claimed
    st.checked(iid, "fine", session_id="verifier9")
    st.complete(iid, by="human")
    assert st.load().leaves[0].checked_note == ""


# ── the brief ────────────────────────────────────────────────────────────

def test_the_brief_carries_no_transcript(claimed, capsys, monkeypatch):
    """The fresh context is most of what this role has going for it. A
    verifier handed the doer's reasoning tends to re-run it."""
    from agent_mission import __main__ as MM
    st, iid = claimed
    assert MM.main(["verify", "--on", "g"]) == 0
    out = capsys.readouterr().out
    assert "wire the lease" in out and "done: wired it" in out
    assert "DISK:" in out, "the mechanical verdict must be handed over"
    for leak in ("transcript", "assistant", "tool_use", "thinking"):
        assert leak not in out.lower(), leak


def test_the_brief_states_where_paths_resolve(claimed, capsys):
    """Claim artifacts resolve against the MISSION's cwd, and a verifier
    opening the wrong directory reports a real file as missing."""
    from agent_mission import __main__ as MM
    st, iid = claimed
    MM.main(["verify", "--on", "g"])
    out = capsys.readouterr().out
    assert "ARTIFACT PATHS RESOLVE AGAINST" in out
    assert st.load().cwd in out


def test_the_brief_separates_the_disk_from_the_opinion(claimed, capsys):
    from agent_mission import __main__ as MM
    st, iid = claimed
    st.checked(iid, "read it", session_id="verifier9")
    MM.main(["verify", "--on", "g"])
    out = capsys.readouterr().out
    assert "present and changed since accepted" in out
    assert "an opinion" in out, "or a guess reads as a check"


def test_the_role_text_forbids_fixing(capsys):
    from agent_mission.__main__ import VERIFIER_ROLE
    low = VERIFIER_ROLE.lower()
    assert "do not fix" in low or "not fix anything" in low
    assert "opinion" in low, "it must say what its verdict is worth"
    assert "mission finding" in VERIFIER_ROLE
    assert "mission checked" in VERIFIER_ROLE


def test_verify_is_quiet_with_nothing_claimed(tmp_path, monkeypatch, capsys):
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    assert MM.main(["verify", "--on", "g"]) == 0
    assert "nothing to check" in capsys.readouterr().out


# ── spawning ─────────────────────────────────────────────────────────────

def test_an_agent_cannot_spawn_a_verifier(claimed, capsys, monkeypatch):
    """A verifier writes findings, which re-queue work — so spawning one is
    not a read-only act and is not reachable from an agent's shell."""
    from agent_mission import __main__ as MM
    monkeypatch.setattr(MM, "_at_a_keyboard", lambda: False)
    assert MM.main(["verify", "--spawn", "--on", "g"]) == 1
    out = capsys.readouterr().out
    assert "yours to do" in out
    assert "--role" in out, "it should hand over the manual path"


def test_a_person_spawns_one_with_the_role_in_the_prompt(claimed, monkeypatch,
                                                         capsys):
    from agent_mission import __main__ as MM
    seen = {}

    class _P:
        pid = 4242

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        seen["cwd"] = kw.get("cwd")
        return _P()

    monkeypatch.setattr(MM, "_at_a_keyboard", lambda: True)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/local/bin/claude")
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    assert MM.main(["verify", "--spawn", "--on", "g"]) == 0

    assert seen["argv"][:2] == ["/usr/local/bin/claude", "-p"]
    prompt = seen["argv"][2]
    assert "VERIFICATION session" in prompt
    assert "done: wired it" in prompt, "the brief has to be in the prompt"
    assert seen["cwd"] == claimed[0].load().cwd, \
        "it must start where the artifacts resolve"
    assert "cannot tick" in capsys.readouterr().out


def test_the_spawned_prompt_has_no_spawn_step(claimed):
    """An agent that can spawn agents that spawn agents is not a feature."""
    from agent_mission.__main__ import VERIFIER_ROLE
    assert "--spawn" not in VERIFIER_ROLE


def test_a_missing_claude_binary_says_what_to_do(claimed, monkeypatch, capsys):
    from agent_mission import __main__ as MM
    monkeypatch.setattr(MM, "_at_a_keyboard", lambda: True)
    monkeypatch.setattr("shutil.which", lambda n: None)
    assert MM.main(["verify", "--spawn", "--on", "g"]) == 1
    assert "mission verify --role" in capsys.readouterr().out


# ── the display ──────────────────────────────────────────────────────────

def test_the_board_carries_the_opinion_apart_from_the_verdict(claimed):
    st, iid = claimed
    st.checked(iid, "read store.py end to end", session_id="verifier9")
    row = B.mission_rows()[0]["queue"]["review"][0]
    assert row["checked_by"] == "verifier9"
    assert "read store.py" in row["checked_note"]
    assert row["claim"] and row["claim"] != row["checked_note"]


def test_the_page_calls_it_an_opinion(claimed):
    assert "opinion, not evidence" in B.PAGE
    assert "qopinion" in B.PAGE


def test_the_review_lane_names_the_session_that_claimed_it(claimed):
    """Found by the first real verification session, on work finished
    twenty minutes earlier: `claimed_by` was populated in the store and
    appeared nowhere in board.py, while `checked_by` was carried three
    times. So the card showed that something awaited a check but not who
    had claimed it -- which is the one fact you need to see whether the
    checker and the claimant are the same session."""
    st, iid = claimed
    row = B.mission_rows()[0]["queue"]["review"][0]
    assert row["claimed_by"] == "workerA"
    assert "claimed_by" in B.PAGE
