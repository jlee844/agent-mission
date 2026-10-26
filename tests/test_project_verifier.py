"""C20, as ruled 2026-10-26: ONE standing verifier per project.

The owner chose a per-goal verifier over a fresh session per claim. Most of
the plumbing existed: `verify --spawn --on <goal>` starts one session for all
of a goal's claims, and an attached session already hears of each new claim.
What this adds:

  * registration -- a session declares itself the goal's verifier, so the
    board can show who checks this goal and the signal can address it;
  * routing -- the registered verifier is told "this needs your check",
    rather than the generic notice every attached session sees;
  * a third verdict, needs-human, for a verifier that cannot decide. Beside
    `checked` (agrees) and `finding` (sends it back), abstaining had no word,
    so it was either a forced guess or silence.
"""
import pytest

from agent_mission import missions as M
from agent_mission.store import MissionStore


@pytest.fixture()
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    (tmp_path / "f.py").write_text("x")
    s = MissionStore(M.missions_root() / "g")
    s.create("s", str(tmp_path), "goal", by="human")
    return s


def _claimed(st, by="worker"):
    ev = st.propose("work", by="human"); st.accept(ev["item_id"], by="human")
    st.claim_done(ev["item_id"], "done: x (f.py)", by="agent", session_id=by)
    return ev["item_id"]


def _row(st, i):
    return next(d for d in st.load().checklist if d["id"] == i)


# ── registration ─────────────────────────────────────────────────────────

def test_a_session_registers_as_the_goals_verifier(st):
    st.set_verifier("checker")
    assert st.load().verifier == "checker"


def test_one_per_goal_the_latest_registration_wins(st):
    st.set_verifier("first"); st.set_verifier("second")
    assert st.load().verifier == "second"


def test_clearing_it(st):
    st.set_verifier("checker"); st.set_verifier("", clear=True)
    assert st.load().verifier == ""


# ── the third verdict ────────────────────────────────────────────────────

def test_needs_human_is_recorded_and_changes_no_lane(st):
    i = _claimed(st)
    st.needs_human(i, "the change is there but I cannot tell if it is right",
                   session_id="checker")
    r = _row(st, i)
    assert r["needs_human_note"].startswith("the change is there")
    assert r["needs_human_by"] == "checker"
    m = st.load()
    assert i in [x.id for x in m.lanes()["review"]], "it stays awaiting the human"


def test_needs_human_requires_a_reason(st):
    i = _claimed(st)
    with pytest.raises(ValueError):
        st.needs_human(i, "  ", session_id="checker")


def test_needs_human_requires_a_claim(st):
    ev = st.propose("work", by="human"); st.accept(ev["item_id"], by="human")
    with pytest.raises(ValueError):
        st.needs_human(ev["item_id"], "why", session_id="checker")


def test_the_claimant_cannot_abstain_on_its_own_claim(st):
    """Self-grading, refused exactly as `checked` refuses it."""
    i = _claimed(st, by="worker")
    with pytest.raises(ValueError):
        st.needs_human(i, "why", session_id="worker")


def test_a_later_opinion_replaces_the_earlier_one(st):
    i = _claimed(st)
    st.needs_human(i, "unsure", session_id="checker")
    st.checked(i, "re-read it: holds", session_id="checker")
    r = _row(st, i)
    assert r["checked_note"] and not r["needs_human_note"]
    st.needs_human(i, "unsure again", session_id="checker")
    r = _row(st, i)
    assert r["needs_human_note"] and not r["checked_note"]


def test_a_new_claim_clears_it(st):
    i = _claimed(st)
    st.needs_human(i, "unsure", session_id="checker")
    st.claim_done(i, "done: x again (f.py)", by="agent", session_id="worker")
    assert not _row(st, i)["needs_human_note"], "it was about the OLD claim"


def test_ticking_clears_it(st):
    i = _claimed(st)
    st.needs_human(i, "unsure", session_id="checker")
    st.complete(i, by="human")
    assert not _row(st, i)["needs_human_note"]


# ── routing ──────────────────────────────────────────────────────────────

def test_the_registered_verifier_is_told_to_check(st):
    from agent_mission import signal as S
    S.check("checker"); S.check("bystander")          # baseline both
    st.set_verifier("checker")
    _claimed(st)
    mine = " ".join(S.check("checker"))
    theirs = " ".join(S.check("bystander"))
    assert "needs your check" in mine and "mission verify --on g" in mine
    assert "needs your check" not in theirs, "only the goal's verifier is asked"
