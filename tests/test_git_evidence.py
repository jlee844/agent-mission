"""Inside a git repo, git -- not the timestamp -- says whether a claimed file
changed since the work was agreed (item: pushback on the git half of the
EVIDENCE RULE, agreed as proposed).

The literal rule, "tracked and modified", would have failed COMMITTED work,
since a committed file is tracked and clean. These pin the agreed version:
commits after acceptance count, uncommitted edits count, a moved timestamp on
unchanged committed content does not, ignored files never do, and outside a
repo the old mtime check is untouched.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agent_mission.claims import _touched_since

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(when)} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q")
    (r / ".gitignore").write_text("*.log\n")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base", when=time.time() - 3600)
    return r


def test_work_committed_after_acceptance_counts(repo):
    since = time.time() - 600
    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "commit", "-qam", "work", when=time.time())
    assert _touched_since(repo / "a.py", since)


def test_committed_before_acceptance_with_a_moved_timestamp_does_not(repo):
    """A formatter or branch switch rewrites the bytes it found: mtime moves,
    nothing changed. mtime alone called this fresh."""
    since = time.time() - 600
    os.utime(repo / "a.py", None)
    assert not _touched_since(repo / "a.py", since)


def test_an_uncommitted_edit_counts(repo):
    since = time.time() - 600
    (repo / "a.py").write_text("x = 3\n")
    assert _touched_since(repo / "a.py", since)


def test_a_new_untracked_file_counts(repo):
    since = time.time() - 600
    (repo / "new.py").write_text("y = 1\n")
    assert _touched_since(repo / "new.py", since)
    assert _touched_since(repo, since), "a directory with new work in it"


def test_an_ignored_file_never_counts(repo):
    since = time.time() - 600
    (repo / "run.log").write_text("written every run\n")
    assert not _touched_since(repo / "run.log", since)


def test_a_directory_with_only_ignored_churn_is_not_changed(repo):
    since = time.time() - 600
    (repo / "run.log").write_text("noise\n")
    os.utime(repo / "a.py", None)
    assert not _touched_since(repo, since)


def test_outside_any_repo_mtime_still_decides(tmp_path):
    since = time.time() - 600
    f = tmp_path / "plain.txt"
    f.write_text("hi\n")
    assert _touched_since(f, since)
    os.utime(f, (since - 100, since - 100))
    assert not _touched_since(f, since)
