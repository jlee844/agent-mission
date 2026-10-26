"""Folders the person said do not belong on the board.

Every new, unattached session is asked whether its folder should become a
goal (see the NOT ATTACHED prompt and CLAUDE.md). Without a memory, a
throwaway folder is asked about by every session that opens in it, which is
the noise the owner said he did not want. So a "no" is recorded once, per
folder, and covers the folder's subfolders.

Append-only, like the event log, so `--undo` is a record rather than an
erasure. Recordable by an agent on purpose: the session asks, the person
answers no, and the SESSION writes it down. The worst a wrong record can do
is suppress a question -- it grants nothing and hides no work.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _file() -> Path:
    # Call time, not import time: tests set AGENT_MISSION_HOME per test.
    return Path(os.environ.get("AGENT_MISSION_HOME")
                or Path.home() / ".agent-mission") / "skip-dirs.jsonl"


def too_wide(folder: Path) -> bool:
    """Skipping these would silence every session on the machine."""
    f = folder.resolve()
    return f == Path(f.anchor) or f == Path.home().resolve()


def record(folder: Path, undo: bool = False, by: str = "agent",
           session: str = "") -> dict:
    row = {"dir": str(folder.resolve()), "undo": undo, "at": time.time(),
           "by": by, "session": session}
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def skipped() -> dict[str, dict]:
    """Folder -> the record that skipped it. The latest record per folder
    wins, so an undo after a skip un-skips it."""
    out: dict[str, dict] = {}
    try:
        lines = _file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue                      # a half-written line is not a record
        if r.get("undo"):
            out.pop(r.get("dir"), None)
        elif r.get("dir"):
            out[r["dir"]] = r
    return out


def covering(folder: Path) -> dict | None:
    """The skip that covers this folder -- itself or any ancestor -- if any."""
    try:
        f = folder.resolve()
    except OSError:
        return None
    s = skipped()
    for cand in (f, *f.parents):
        if str(cand) in s:
            return s[str(cand)]
    return None
