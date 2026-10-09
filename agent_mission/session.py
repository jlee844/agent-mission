"""Which session am I, and what has actually happened in it.

Claude Code exports CLAUDE_CODE_SESSION_ID into every tool call, so a session
identifies itself with no configuration. That is what makes two agents in one
directory workable: picking "the newest transcript here" silently returns
whichever session typed last.
"""

from __future__ import annotations

import json
import functools
import os
import re
import subprocess
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
_TEST = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|"
                   r"unittest|tox|rspec)\b|test_[\w-]+\.\w+|\btests?[/.]", re.I)


def current_session_id() -> str | None:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip() or None


def transcript_for(session_id: str) -> Path | None:
    """Newest transcript wins, across ALL project directories.

    Claude Code files transcripts per working directory, so a session resumed
    from a different cwd writes into a DIFFERENT project folder. Taking the
    first glob hit read whichever dir sorted first -- a live session showed
    dead on the board (dimmed, numbers frozen) while its real transcript grew
    elsewhere. Seen live: a session written seconds earlier, reported dead."""
    if not PROJECTS.exists():
        return None
    hits = list(PROJECTS.glob(f"*/{session_id}.jsonl"))
    if not hits:
        return None

    def mtime(pth: Path) -> float:
        try:
            return pth.stat().st_mtime
        except OSError:
            return 0.0

    return max(hits, key=mtime)


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


def _fingerprint(path):
    """A transcript is append-only, so (size, mtime) identifies its content."""
    try:
        st = Path(path).stat()
        return (str(path), st.st_size, st.st_mtime)
    except OSError:
        return (str(path), 0, 0.0)


# One entry per transcript, with the oldest dropped past this. Bounded so a
# long-lived board cannot accumulate entries for sessions that ended weeks
# ago, and large enough that a board tracking a dozen goals never thrashes.
MEMO_MAX = 64


def memo_by_file(fn):
    """Cache a whole-file parse against the file's size and mtime.

    The board polls every 4 seconds and re-parsed every transcript each time.
    On a real board that meant ~4 seconds of work per poll over 130 MB of
    JSONL -- so requests overlapped permanently and the page went blank while
    it waited. Transcripts only ever grow, so the fingerprint is exact rather
    than a guess with a TTL.

    ⚠️ AND IT CACHED NOTHING. `cache.clear()` on every miss kept ONE entry
    for the whole process, not one per file -- the comment said "one entry
    per file is enough" and the code did something else. The board polls N
    transcripts in a loop, so each call evicted the previous session's entry
    and the hit rate was exactly ZERO: measured 1 entry for 3 files across
    two full rounds. Every poll re-parsed every byte the board could see --
    1.58 GB of JSONL every 4 seconds on the author's own board, which is
    what took the process to 4.5 GB RSS with an 8.4 GB peak.

    Keyed per PATH now, so a new fingerprint replaces that file's entry and
    leaves the others alone.
    """
    cache: "OrderedDict[tuple, tuple]" = OrderedDict()

    @functools.wraps(fn)
    def wrapper(path, *a, **kw):
        slot = (str(path), a, tuple(sorted(kw.items())))
        fp = _fingerprint(path)
        hit = cache.get(slot)
        if hit is not None and hit[0] == fp:
            cache.move_to_end(slot)
            return hit[1]
        out = fn(path, *a, **kw)
        cache[slot] = (fp, out)
        cache.move_to_end(slot)
        while len(cache) > MEMO_MAX:
            cache.popitem(last=False)          # oldest out, not everything
        return out

    wrapper.cache = cache
    return wrapper


# How many recent user asks a card shows. A deque with this as its maxlen,
# NOT a list trimmed at the end: the list accumulated every qualifying user
# message in the file before throwing all but three away.
ASKS_KEPT = 3

# path -> [offset_of_last_complete_line, size_then, Activity, asks_deque]
# A transcript is append-only, so the parse resumes where it stopped instead
# of starting over. The board re-read 1.58 GB every 4 seconds; with this it
# reads the bytes that were actually added.
_RESUME: "OrderedDict[str, list]" = OrderedDict()


def _fold_record(rec: dict, a: Activity, asks) -> None:
    """One transcript record into the running totals."""
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
            # The old code also did `pending[b["id"]] = True` here and never
            # read `pending` again -- one dict entry per tool call in the
            # file, which on a 1 GB transcript is hundreds of thousands of
            # entries held for the length of the parse, for nothing.
        elif t == "tool_result" and b.get("is_error"):
            a.failures += 1
        elif t == "text" and role == "user":
            txt = " ".join((b.get("text") or "").split())
            if txt and not txt.startswith("<") and 12 < len(txt) < 300:
                asks.append(txt[:200])


def _consume(fh, a: Activity, asks) -> int:
    """Fold complete lines from `fh`, returning the offset to resume at.

    Iterates the file object, so one line is in memory at a time. The old
    `read_text().splitlines()` held the whole file as ONE string AND a list
    of every line -- about 2x the file size before any JSON was parsed, which
    is why a 1.09 GB transcript produced a multi-gigabyte spike.

    A final line with no newline is a record being written RIGHT NOW. It is
    not folded and the offset stops before it, so the next pass sees it whole
    rather than losing it.
    """
    offset = fh.tell()
    for raw in fh:
        if not raw.endswith(b"\n"):
            break                       # partial write; stop before it
        offset += len(raw)
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            _fold_record(rec, a, asks)
    return offset


@memo_by_file
def activity(path: Path, since_ts: float = 0.0) -> Activity:
    key = str(path)
    try:
        size = Path(path).stat().st_size
    except OSError:
        return Activity()

    state = _RESUME.get(key)
    # Restart when the file shrank (rotated, or a different session reusing
    # the name): the accumulated totals would describe bytes that are gone.
    if state is None or size < state[1]:
        state = [0, 0, Activity(), deque(maxlen=ASKS_KEPT)]

    offset, _, acc, asks = state
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            offset = _consume(fh, acc, asks)
    except OSError:
        return Activity()

    _RESUME[key] = [offset, size, acc, asks]
    _RESUME.move_to_end(key)
    while len(_RESUME) > MEMO_MAX:
        _RESUME.popitem(last=False)

    # A snapshot, so a caller holding the result cannot see it change under
    # them on the next poll -- and cannot mutate the accumulator either.
    return Activity(calls=acc.calls, tests=acc.tests, failures=acc.failures,
                    files=dict(sorted(acc.files.items(),
                                      key=lambda kv: -kv[1])),
                    last_asks=list(asks), since=dict(acc.since))


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


class NoSessionError(Exception):
    """Raised when we cannot tell which mission a command means.

    Carries the candidates so the caller can print them instead of a
    traceback: the person is at a keyboard being asked to choose.
    """

    def __init__(self, message: str, candidates: list[tuple[str, str]] | None = None,
                 flag: str = "--on"):
        super().__init__(message)
        self.candidates = candidates or []
        # Which flag addresses these candidates. `--on <name>` is the routing
        # flag a person types; `--session` survives for provenance and appears
        # in no human-facing message.
        self.flag = flag


def resolve_session(explicit: str | None, cwd: str | None = None) -> str:
    """Which mission does this command mean? A name, or nothing.

    This used to fall back to the missions recorded for the current directory,
    deepest match first. It was wrong in the way that costs you a plan: a
    session is opened where the work can REACH what it needs -- the repo root,
    for a coordination tree -- while the goal lives three folders down. So cwd
    answered a question it does not know: what a session can SEE, offered as
    what it is FOR.

    It ran once for real. A career objective was written onto the Wayfinder
    mission and renamed it, because both were recorded under the same root.

    So the router is gone. Resolution is `--on <name>`, then the session id in
    the environment (which says who is SPEAKING, never what is meant), then a
    refusal that lists your goals by name so the fix is a paste. `cwd` stays in
    the signature only so callers need not care; it is not read.
    """
    if explicit:
        return explicit
    env = current_session_id()
    if env:
        return env
    from . import missions as M                     # circular at import time
    cands = M.choices()
    if not cands:
        raise NoSessionError(
            "no goal named, and none exist yet.\n"
            "  `mission init` writes one — it takes a minute.")
    raise NoSessionError("which goal? name it with --on:", cands)


def find_session(needle: str, base=None) -> str:
    """Resolve a session by id prefix or mission name. Ambiguity refuses.

    A reviewing session proposing into a working session's mission needs to
    name it, and nobody types a full uuid. Matching is exact-id, then prefix,
    then case-insensitive substring of the mission's name or objective.
    """
    from .store import MissionStore, root_for

    home = root_for("x", base).parent
    if not home.exists():
        raise NoSessionError(f"no missions at all — nothing named {needle!r}")
    cands = []
    for d in sorted(home.iterdir()):
        if not d.is_dir() or not (d / "events.jsonl").exists():
            continue
        if d.name == needle:
            return d.name
        try:
            m = MissionStore(d).load()
        except Exception:
            continue
        if m is None:
            continue
        hay = f"{m.name} {m.objective}".lower()
        if d.name.startswith(needle) or needle.lower() in hay:
            cands.append((d.name, m.title))
    if not cands:
        raise NoSessionError(f"no session matches {needle!r}")
    if len(cands) > 1:
        raise NoSessionError(f"{needle!r} matches several — say which:", cands,
                             flag="--session")
    return cands[0][0]
