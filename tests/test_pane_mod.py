"""The pane's own tests, run as part of THIS suite.

⚠️ They lived beside the mod and nothing ran them. For weeks the file
imported 'claude-code/testing' and was run -- when it was run at all -- with
`vitest`, which cannot resolve that module; when it finally went through the
real runner it failed 2 of 2, written blind. A test nothing executes is a
claim, so it is wired in here.

⚠️ The runner is `claude plugin test`, and it must be a claude NEW ENOUGH to
have it. The `claude` on PATH can be older than the engine a desktop session
runs on -- on the machine this was written on it was 2.1.170 while the
session ran 2.1.308, and the old one has no `test` command and an outdated
validator that REJECTS the current hooks.json format. So this finds a claude
that supports `plugin test`, and skips (saying why) rather than failing on a
machine that has none: a missing tool is not a broken pane.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_mission.setup_surfaces import pane_dir


def _candidates() -> list[str]:
    out = []
    if os.environ.get("AGENT_MISSION_CLAUDE"):
        out.append(os.environ["AGENT_MISSION_CLAUDE"])
    # Newest first: the desktop app keeps every engine it has run.
    app = sorted(glob.glob(str(Path.home() / "Library/Application Support"
                               "/Claude/claude-code/*/*/claude.app/Contents"
                               "/MacOS/claude")), reverse=True)
    out += app
    if shutil.which("claude"):
        out.append(shutil.which("claude"))
    return out


def _runner() -> str | None:
    for c in _candidates():
        try:
            r = subprocess.run([c, "plugin", "test", "--help"],
                               capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and "unknown command" not in (r.stdout + r.stderr):
            return c
    return None


def test_the_pane_mod_passes_its_own_tests():
    runner = _runner()
    if runner is None:
        pytest.skip("no claude with `plugin test` found (set "
                    "AGENT_MISSION_CLAUDE); the pane's tests were NOT run")
    r = subprocess.run([runner, "plugin", "test", str(pane_dir())],
                       capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and " 0 fail" in out, out[-2000:]
