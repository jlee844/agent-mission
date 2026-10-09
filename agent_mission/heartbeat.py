"""C19-5(b): a session reports that it is alive, instead of the board guessing.

The ruling this implements, and the measurement behind it (2026-10-08, filed
as 8db99072 by a session that went and counted): the board mapped a live
process's cwd to ONE project directory and took the top N transcripts by
mtime, N = that directory's process count. Against 7 processes in one
directory there were 22 transcripts, and the picks were aged
56/252/266/267/267/267/287 minutes -- six ENDED sessions reported live, while
a live-but-idle session ranked below N was dropped entirely. That dropping is
the dimming the board was first patched for, and the patch's stated cause was
disproved by the same pass: 0 of 5303 session ids appear in more than one
project directory.

The honest problem is that there is no pid-to-session key ON DISK -- `lsof`
over all ten live pids shows no open `.jsonl` descriptor, because a transcript
is opened, appended and closed. So nothing outside a session can name it.

What makes (b) work is that the hook runs INSIDE the session's own process
tree, so it can write the key nobody else can read. Measured on this machine:
walking the hook's ancestors reaches
`~/Library/Application Support/Claude/claude` at pid 69139, and
`/tmp/cc-socks/69139.sock` exists -- so (session id, Claude pid) is a real
join, recorded by the one party that knows both.

Why the pid and not the beat's timestamp: a session idle between turns writes
nothing, so freshness alone would call it dead -- exactly the failure this
replaces. The pid is still alive while it sits idle, so liveness is a fact
about a process, and the beat is only how that fact got recorded. Nothing
here infers: a session that has never beaten is reported UNKNOWN, never dead.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .whoami import _looks_like_agent, _ps

# Where a Claude Code session's agent socket lives. Owned by `session.live()`
# as a concept; named here because the join test needs it per pid rather than
# a directory scan.
SOCKS = Path("/tmp/cc-socks")

# How far up to walk looking for the process that owns a socket. The measured
# chain is 3 deep (hook -> zsh -> claude); 12 leaves room for a wrapper or
# two without ever walking to init.
_MAX_DEPTH = 12


# Prune only once the directory is bigger than any plausible number of live
# sessions. Ten sockets is a busy machine; 64 is well past that, so pruning
# never races a session that is simply idle.
PRUNE_ABOVE = 64


def _home() -> Path:
    # Honours AGENT_MISSION_HOME at CALL time, not import time: every test in
    # this suite sets it per-test, and a module-level constant would read one
    # tmpdir for the whole session.
    return Path(os.environ.get("AGENT_MISSION_HOME")
                or Path.home() / ".agent-mission")


def _dir() -> Path:
    return _home() / "heartbeats"


def claude_pid(start: int | None = None) -> int:
    """The nearest ancestor process that owns an agent socket, or 0.

    0 is "could not establish it", which is not the same as "not live" --
    callers must not turn it into a death. A plain terminal invocation has no
    such ancestor and legitimately returns 0.
    """
    pid = os.getppid() if start is None else start
    seen: set[int] = set()
    for _ in range(_MAX_DEPTH):
        if pid <= 1 or pid in seen:
            return 0
        seen.add(pid)
        ppid, _cmd = _ps(pid)
        if not _cmd:
            return 0
        if (SOCKS / f"{pid}.sock").exists():
            return pid
        pid = ppid
    return 0


def alive(pid: int) -> bool:
    """Is that pid still a running coding agent?

    Two tests, because a pid alone is not safe: pids are RECYCLED, so a
    recorded 69139 can come back as something unrelated and a bare `ps -p`
    would call the dead session live. The socket must still exist AND the
    process must still look like an agent.
    """
    if not pid or not (SOCKS / f"{pid}.sock").exists():
        return False
    _ppid, cmd = _ps(pid)
    return bool(cmd) and _looks_like_agent(cmd)


def beat(sid: str, cwd: str = "") -> dict | None:
    """Record that this session is alive, with the pid that proves it.

    Returns the record, or None when it could not be established -- which is
    the honest outcome outside a session (a plain terminal, a test runner)
    and must stay distinguishable from a beat saying "dead". Never raises:
    this runs from a hook, and a hook that can break a prompt gets
    uninstalled.
    """
    try:
        if not sid:
            return None
        pid = claude_pid()
        if not pid:
            return None
        # ABSOLUTE, always. The caller passes argparse's `--cwd`, which
        # defaults to ".", so every record said "." and the field could not
        # identify anything -- found while trying to work out which session
        # had filed a review.
        try:
            where = str(Path(cwd or os.getcwd()).resolve())
        except OSError:
            where = os.getcwd()
        rec = {"sid": sid, "pid": pid, "at": time.time(), "cwd": where}
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{sid}.tmp"
        tmp.write_text(json.dumps(rec))
        tmp.replace(d / f"{sid}.json")       # atomic: the board polls this
        # Self-limiting. `prune` existed and NOTHING called it, so the
        # directory would have grown one record per session forever and the
        # board reads all of them every poll -- dead code that is also a slow
        # leak. Globbing a small directory once per turn is cheaper than the
        # unbounded read it prevents, and the bound is generous enough that a
        # busy day never triggers it.
        if len(list(d.glob("*.json"))) > PRUNE_ABOVE:
            prune()
        return rec
    except Exception:
        return None


def beats() -> list[dict]:
    """Every recorded heartbeat, readable ones only.

    A damaged file is SKIPPED rather than raised: one unparsable record must
    not blank the board's liveness, which is the failure `_safe_load` exists
    for one directory over.
    """
    d = _dir()
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("sid") and rec.get("pid"):
            out.append(rec)
    return out


def live_ids() -> set[str]:
    """Session ids whose own recorded process is still running.

    ⚠️ One beat per PID, newest wins. `/clear` and `/resume` start a new
    session id inside the SAME Claude process, so the old session's record
    still points at a live pid holding a live socket — and the board reported
    an ended session as live until the process exited. A pid can host several
    session ids over its life and only one of them is current, so the pid is
    the key and the newest beat on it is the answer.

    Found by a second reader reading the code, not by the board failing
    visibly, which is how a liveness bug usually arrives: it says something
    reassuring.
    """
    newest: dict[int, dict] = {}
    for r in beats():
        try:
            pid = int(r.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        prev = newest.get(pid)
        if prev is None or float(r.get("at") or 0) > float(prev.get("at") or 0):
            newest[pid] = r
    return {r["sid"] for pid, r in newest.items() if alive(pid)}


def known_ids() -> set[str]:
    """Session ids that have EVER beaten.

    The difference between this and `live_ids` is the whole honesty of the
    feature: outside it, liveness is unknown and must be rendered as unknown.
    """
    return {r["sid"] for r in beats()}


def prune(keep_days: float = 30.0) -> int:
    """Drop heartbeats for processes long gone. Returns how many were removed.

    Not a liveness decision -- an ended session's record is harmless, it just
    accumulates. Age, not liveness, is the criterion, so a long-idle live
    session is never pruned out of existence.
    """
    cut = time.time() - keep_days * 86400
    n = 0
    for p in _dir().glob("*.json") if _dir().exists() else []:
        try:
            rec = json.loads(p.read_text())
            if float(rec.get("at") or 0) < cut and not alive(int(rec.get("pid") or 0)):
                p.unlink()
                n += 1
        except (OSError, ValueError):
            continue
    return n
