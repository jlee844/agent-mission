"""A shared flag must mean the same thing on either side of the subcommand.

Found by another session on 2026-10-11, filing proposals:

    mission --on <goal> propose "..."

silently ignored `--on` and wrote to the goal that session happened to be
ATTACHED to. Four proposals landed on the wrong board, and nothing said so —
the command printed success, naming the wrong goal in small type.

The cause is argparse, not the router. `common` is a parent of the top-level
parser AND of every subparser; argparse applies the subparser's defaults
AFTER the main parse, so a value typed before the subcommand was overwritten
by the subcommand's own empty default. Every flag in `common` had it, not
just `--on`.

⚠️ A misroute is worse than an error. An error costs a retype; this wrote
real events to a real goal and reported success, which is the one outcome
this project's whole design exists to prevent — "names route" stops meaning
anything if the name is dropped on the floor.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from agent_mission import missions as M
from agent_mission.store import MissionStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def two_goals(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    for name in ("goal-a", "goal-b"):
        MissionStore(M.missions_root() / name).create(
            "s1", str(tmp_path), f"objective {name}", by="human",
            typed_by="human")
    return tmp_path


def _items(goal: str) -> list[str]:
    return [i.text for i in MissionStore(M.missions_root() / goal).load().items]


@pytest.mark.parametrize("argv", [
    ["--on", "goal-b", "propose", "x"],       # before the subcommand
    ["propose", "x", "--on", "goal-b"],       # after it
])
def test_on_routes_from_either_side_of_the_subcommand(two_goals, argv):
    from agent_mission.__main__ import main
    assert main(argv) == 0
    assert _items("goal-b") == ["x"], "the named goal did not get it"
    assert _items("goal-a") == [], "it landed on the wrong goal"


def test_the_flag_before_the_subcommand_is_not_silently_dropped(two_goals):
    """The exact shape of the bug: dropped, then fell back to an attachment.

    With a session attached to goal-a, dropping `--on goal-b` sent the write
    to goal-a and PRINTED SUCCESS. The test asserts the write landed where it
    was addressed, not merely that something was written.
    """
    from agent_mission.__main__ import main
    M.attach("s1", "goal-a")
    assert main(["--on", "goal-b", "propose", "addressed to b"]) == 0
    assert _items("goal-a") == [], "it fell back to the attached goal"
    assert _items("goal-b") == ["addressed to b"]


def test_every_shared_flag_survives_the_subcommand(two_goals):
    """`--on` was the one that hurt; the cause covered all of them."""
    from agent_mission.__main__ import _build
    ap = _build()
    a = ap.parse_args(["--on", "goal-b", "--all", "queue"])
    assert a.on == "goal-b"
    assert a.all is True


def test_the_defaults_still_exist_when_nothing_is_passed(two_goals):
    """SUPPRESS removes the attribute; `main` puts the default back once."""
    from agent_mission.__main__ import _build
    ap = _build()
    a = ap.parse_args(["queue"])
    assert not hasattr(a, "on"), "SUPPRESS should leave it absent here"
    # …and main fills it, so every command still sees the same shape.
    from agent_mission.__main__ import main
    main(["queue", "--on", "goal-b"])        # exercises the fill path


def test_the_defaults_are_declared_in_exactly_one_place():
    """Two places saying what "not given" means IS the bug."""
    src = (ROOT / "agent_mission" / "__main__.py").read_text()
    assert src.count('("on", None), ("session", None)') == 1
    # No shared flag may carry a real default on the parser any more.
    common = src.split("common = argparse.ArgumentParser")[1].split("ap = ")[0]
    for flag in ("--on", "--session", "--cwd", "--all"):
        seg = common.split(f'"{flag}"')[1].split(")")[0]
        assert "argparse.SUPPRESS" in seg, f"{flag} can still be overwritten"


def test_the_cli_routes_it_too(two_goals):
    """Through the real entry point, not just main(): a person types this."""
    out = subprocess.run(
        [sys.executable, "-m", "agent_mission", "--on", "goal-b",
         "propose", "via the CLI"],
        capture_output=True, text=True, cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT),
             "AGENT_MISSION_HOME": str(M.missions_root().parent)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "goal-b" in out.stdout
    assert _items("goal-b") == ["via the CLI"]
