"""A file OUTSIDE the goal's folder is not evidence for the goal.

Measured 2026-10-11, the third hole in one evening: freshness was checked by
mtime, so `done: rewrote the auth flow (/var/log/system.log)` came back
`ok=True` -- any constantly-written file anywhere satisfies "changed since
you accepted". /etc/hosts reads stale, so the attack needs a file that
changes, and the next one is ~/.zsh_history, which a home-prefix rule would
pass. Path heuristics cannot close it; the true statement is that nothing
can attribute a change in another tree to THIS goal, so such an artifact is
reported `uncheckable` -- never backed, never swept by "confirm all backed".
"""
import os
import time

from agent_mission.claims import verdict_for


def _setup(tmp_path):
    root = tmp_path / "goal"; root.mkdir()
    inside = root / "src.py"; inside.write_text("x")
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    noisy = elsewhere / "system.log"; noisy.write_text("constantly written")
    return root, inside, noisy


def test_a_fresh_file_outside_the_folder_is_not_backed(tmp_path):
    root, _, noisy = _setup(tmp_path)
    accepted = time.time() - 60          # the noisy file changed after this
    v = verdict_for(f"done: rewrote auth ({noisy})", str(root), accepted)
    assert v["ok"] is False, "a changing file elsewhere must not back a claim"
    assert v["uncheckable"] == [str(noisy)]
    assert v["backed"] == 0


def test_a_home_dotfile_is_outside_too(tmp_path):
    """The next attack the measurement named. A home-prefix rule passes it."""
    root, _, _ = _setup(tmp_path)
    hist = tmp_path / ".zsh_history"; hist.write_text("ls")
    v = verdict_for(f"done: x ({hist})", str(root), time.time() - 60)
    assert v["ok"] is False and v["uncheckable"]


def test_inside_the_folder_is_unchanged(tmp_path):
    root, inside, _ = _setup(tmp_path)
    v = verdict_for("done: wrote it (src.py)", str(root), time.time() - 60)
    assert v["ok"] is True and v["uncheckable"] == []


def test_a_symlink_into_the_folder_counts_as_inside(tmp_path):
    root, inside, _ = _setup(tmp_path)
    link = tmp_path / "shortcut.py"
    os.symlink(inside, link)
    v = verdict_for(f"done: x ({link})", str(root), time.time() - 60)
    assert v["uncheckable"] == [], "it resolves into the goal's folder"


def test_no_goal_folder_means_no_judgement(tmp_path):
    """With no folder to compare against, saying 'outside' would be invented."""
    _, _, noisy = _setup(tmp_path)
    v = verdict_for(f"done: x ({noisy})", "", 0.0)
    assert v["uncheckable"] == []


def test_mixed_claims_are_not_backed(tmp_path):
    """One real artifact must not launder an outside one."""
    root, inside, noisy = _setup(tmp_path)
    v = verdict_for(f"done: x (src.py, {noisy})", str(root), time.time() - 60)
    assert v["ok"] is False and v["uncheckable"] == [str(noisy)]
