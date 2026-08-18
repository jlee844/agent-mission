"""Which session am I, and what has actually happened in it.

Claude Code exports CLAUDE_CODE_SESSION_ID into every tool call, so a session
identifies itself with no configuration. That is what makes two agents in one
directory workable: picking "the newest transcript here" silently returns
whichever session typed last.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
_TEST = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|"
                   r"unittest|tox|rspec)\b|test_[\w-]+\.\w+|\btests?[/.]", re.I)


def current_session_id() -> str | None:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip() or None


def transcript_for(session_id: str) -> Path | None:
    if not PROJECTS.exists():
        return None
    hits = list(PROJECTS.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def live() -> list[dict]:
    """Running Claude Code sessions, by working directory.

    A pid cannot be mapped to a session id from outside — several processes
    share a directory and nothing on disk links one to a transcript — so this
    reports directories and how many processes are in each, never a pid per
    session.
    """
    socks = Path("/tmp/cc-socks")
    counts: dict[str, int] = {}
    if socks.exists():
        for s in socks.glob("*.sock"):
            if not s.stem.isdigit():
                continue
            if subprocess.run(["ps", "-p", s.stem], capture_output=True).returncode:
                continue
            r = subprocess.run(["lsof", "-a", "-p", s.stem, "-d", "cwd", "-Fn"],
                               capture_output=True, text=True)
            cwd = next((l[1:] for l in r.stdout.splitlines() if l.startswith("n")), "")
            if cwd:
                counts[cwd] = counts.get(cwd, 0) + 1
    return [{"cwd": k, "procs": v} for k, v in counts.items()]


@dataclass
class Activity:
    """What measurably happened — never an opinion about whether it was right."""
    calls: int = 0
    files: dict[str, int] = field(default_factory=dict)
    tests: int = 0
    failures: int = 0
    last_asks: list[str] = field(default_factory=list)
    since: dict[str, int] = field(default_factory=dict)


def activity(path: Path, since_ts: float = 0.0) -> Activity:
    a = Activity()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return a
    pending: dict[str, bool] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        role, c = msg.get("role"), msg.get("content")
        blocks = ([{"type": "text", "text": c}] if isinstance(c, str)
                  else c if isinstance(c, list) else [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "tool_use":
                a.calls += 1
                inp = b.get("input") or {}
                name = b.get("name") or ""
                target = inp.get("file_path") or inp.get("command") or ""
                if name in WRITE_TOOLS and inp.get("file_path"):
                    k = Path(inp["file_path"]).name
                    a.files[k] = a.files.get(k, 0) + 1
                if _TEST.search(str(target)):
                    a.tests += 1
                pending[b.get("id")] = True
            elif t == "tool_result" and b.get("is_error"):
                a.failures += 1
            elif t == "text" and role == "user":
                s = " ".join((b.get("text") or "").split())
                if s and not s.startswith("<") and 12 < len(s) < 300:
                    a.last_asks.append(s[:200])
    a.last_asks = a.last_asks[-3:]
    a.files = dict(sorted(a.files.items(), key=lambda kv: -kv[1]))
    return a


def short_id(sid: str) -> str:
    """A session id short enough to read, without cutting a word in half.

    Claude Code ids are uuids, where the first 8 hex characters identify a
    session fine. Any other id is something a person chose, and "mltest-s" is
    a worse label than the name they picked.
    """
    head = sid[:8]
    if len(sid) > 8 and all(c in "0123456789abcdefABCDEF" for c in head):
        return head
    return sid if len(sid) <= 24 else sid[:23] + "…"
