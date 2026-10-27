"""The name a person sees for a session, read from the app that runs it.

The board knows sessions by id. A person knows them by the title in the app's
sidebar -- and the two did not meet: the session serving the career goal was
titled "Next project evaluation framework", and from the board's 8-character id
nobody could find it, so a request sat untaken while that session looked
broken.

Read-only, local, and best effort: an unknown id gets no title, never a
guess. Two sources:
  * the Claude desktop app keeps one JSON record per session, holding the
    CLI's session id (`cliSessionId`) and the sidebar `title`;
  * Codex keeps `session_index.jsonl`, one thread per line with
    `thread_name`.
A Claude Code session started in a terminal has neither, and says so.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _claude_dir() -> Path:
    return Path(os.environ.get("AGENT_MISSION_CLAUDE_SESSIONS") or
                Path.home() / "Library" / "Application Support" / "Claude"
                / "claude-code-sessions")


def _codex_index() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") \
        / "session_index.jsonl"


def titles() -> dict:
    """{session id: (title, kind)}; kind is "claude-desktop" or "codex"."""
    out: dict = {}
    d = _claude_dir()
    if d.is_dir():
        for p in d.glob("*/*/local_*.json"):
            try:
                rec = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            sid, title = rec.get("cliSessionId"), rec.get("title")
            if sid and title and not rec.get("isArchived"):
                out[sid] = (title, "claude-desktop")
    try:
        lines = _codex_index().read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:                    # later lines are newer renames
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("id") and rec.get("thread_name"):
            out[rec["id"]] = (rec["thread_name"], "codex")
    return out


def title_for(sid: str, table: dict | None = None) -> tuple[str, str]:
    """(title, kind) for one session; ("", "terminal") when nothing knows it."""
    table = titles() if table is None else table
    return table.get(sid, ("", "terminal"))
