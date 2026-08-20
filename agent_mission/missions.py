"""A mission is a goal. A session is something that worked on it for a while.

The store's primary key was the session, so the board answered "what is each
session doing" when the question people actually ask is "how is each GOAL
going". Sessions hop between goals; goals outlive sessions; and a session id is
a thing nobody chose and nobody can remember.

So: missions live under `missions/<id>/`, are addressed by NAME, and sessions
ATTACH to them. Attachment is an event in the mission's own log, which makes
"who worked on this, and when" a fold like everything else.

The old layout still reads -- `mission migrate` lifts it -- because a tool that
loses your plan during an upgrade has failed at the one thing it promises.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .store import MissionStore


def home(base=None) -> Path:
    return Path(base or os.environ.get(
        "AGENT_MISSION_HOME", Path.home() / ".agent-mission"))


def missions_root(base=None) -> Path:
    return home(base) / "missions"


def slug(name: str, limit: int = 32) -> str:
    """A readable id. Cut at a word boundary -- four times in this codebase a
    plain slice halved a word and read as corruption."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if len(s) > limit:
        s = s[:limit]
        s = s.rsplit("-", 1)[0] if "-" in s else s
    return s.strip("-") or "mission"


def all_missions(base=None) -> list[tuple[str, MissionStore]]:
    root = missions_root(base)
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "events.jsonl").exists():
            out.append((d.name, MissionStore(d)))
    return out


def legacy_stores(base=None) -> list[tuple[str, MissionStore]]:
    """Session-keyed stores from before the inversion."""
    h = home(base)
    if not h.exists():
        return []
    return [(d.name, MissionStore(d)) for d in sorted(h.iterdir())
            if d.is_dir() and d.name != "missions"
            and (d / "events.jsonl").exists()]


def attachments(base=None) -> dict[str, str]:
    """session id -> mission id, latest attachment wins.

    A session may re-attach when the human's work pivots, which is why this is
    a fold over events rather than a file anyone rewrites.
    """
    out: dict[str, tuple[float, str]] = {}
    for mid, st in all_missions(base):
        for e in st.events():
            if e.get("kind") == "attached" and e.get("session_id"):
                sid, at = e["session_id"], e.get("at", 0.0)
                if sid not in out or at >= out[sid][0]:
                    out[sid] = (at, mid)
    return {sid: mid for sid, (_, mid) in out.items()}


def sessions_of(mission_id: str, base=None) -> list[str]:
    """Every session that ever served this mission, in order."""
    st = MissionStore(missions_root(base) / mission_id)
    seen, out = set(), []
    for e in st.events():
        if e.get("kind") == "attached":
            sid = e.get("session_id")
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out


def find(name: str, base=None) -> str:
    """A mission by name or id fragment. Ambiguity refuses -- never guesses."""
    from .session import NoSessionError
    cands = []
    for mid, st in all_missions(base):
        if mid == name:
            return mid
        m = st.load()
        hay = f"{mid} {m.name if m else ''} {m.objective if m else ''}".lower()
        if mid.startswith(name.lower()) or name.lower() in hay:
            cands.append((mid, (m.title if m else mid)))
    if not cands:
        raise NoSessionError(f"no mission matches {name!r}")
    if len(cands) > 1:
        raise NoSessionError(f"{name!r} matches several — say which:", cands)
    return cands[0][0]


def choices(base=None, limit: int = 8) -> list[tuple[str, str]]:
    """Every goal you could mean, as (name, label), most recently touched first.

    A refusal is only as good as what it hands you next. This is what turns
    "say which goal" into a paste: the names are the addresses.
    """
    rows = []
    seen = attachments(base)
    for mid, st in all_missions(base):
        m = st.load()
        if m is None or m.archived:
            continue
        try:
            when = st.log.stat().st_mtime
        except OSError:
            when = 0.0
        rows.append((when, mid, m.title))
    # Stores from before the inversion are still addressable -- `--on` falls
    # back to find_session for them. Leaving them out would mean a person with
    # nine un-migrated goals is told they have none, which is the one lie a
    # refusal cannot afford.
    for sid, st in legacy_stores(base):
        m = st.load()
        if m is None or m.archived or sid in seen:
            continue
        try:
            when = st.log.stat().st_mtime
        except OSError:
            when = 0.0
        rows.append((when, m.name or sid, m.title))
    rows.sort(reverse=True)
    out = []
    for when, mid, title in rows[:limit]:
        mins = int((time.time() - when) / 60)
        ago = ("just now" if mins < 1 else f"{mins}m ago" if mins < 60
               else f"{mins // 60}h ago")
        out.append((mid, f"{title}  ·  active {ago}"))
    return out


def attach(session_id: str, mission_id: str, by: str = "human",
           base=None) -> dict:
    st = MissionStore(missions_root(base) / mission_id)
    return st._append("attached", by, session_id=session_id)


def migrate(base=None, dry_run: bool = False) -> list[dict]:
    """Lift each session-keyed store into a named mission, session attached.

    Mechanical, because the logs are append-only: copy the events verbatim,
    then append one `attached` event. Idempotent -- a mission already lifted is
    skipped, so running it twice is safe and running it after new work is too.
    """
    out = []
    root = missions_root(base)
    existing = {mid for mid, _ in all_missions(base)}
    # A session already attached somewhere has already been lifted. Keying the
    # skip on the slug instead let a second run mint "<name>-2" for every
    # mission -- 9 became 18, in the one command whose whole promise is that
    # re-running it is safe.
    already = attachments(base)
    for sid, st in legacy_stores(base):
        m = st.load()
        if m is None or sid in already:
            continue
        mid = slug(m.name or m.objective or sid)
        n, base_mid = 2, mid
        while mid in existing and mid not in {r["mission"] for r in out}:
            mid = f"{base_mid}-{n}"
            n += 1
        row = {"session": sid, "mission": mid, "title": m.title,
               "events": len(list(st.events()))}
        out.append(row)
        if dry_run:
            continue
        d = root / mid
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").write_text(
            st.log.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8")
        attach(sid, mid, by="human", base=base)
        existing.add(mid)
    return out
