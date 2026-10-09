"""Every live session on one page: its mission, its checklist, what it has done.

Serves on 127.0.0.1. Reads transcripts and the mission log; writes nothing.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .actions import Session, Unauthorised, apply as apply_action
from .health import collisions, inspect as inspect_health
from .session import PROJECTS, activity, live, short_id, transcript_for
from .store import LEASE_TTL, MissionStore, root_for


def _slug(cwd: str) -> str:
    return "-" + cwd.replace("/", "-").lstrip("-")


# Group colour, chosen HERE so both renderers agree and nothing infers.
# A subgoal names its domain more often than not; a small keyword table maps
# the usual ones to a stable hue, so "backend" is the same green in every
# mission on the board. Anything the table does not name hashes its own text
# to a hue -- deterministic, stable across reorderings, and honest about
# being arbitrary. First match wins; matching is a substring test, not a
# classifier: no scores, no thresholds, nothing learned (the F34 law).
DOMAIN_HUES = (
    (155, ("backend", "back end", "server", "api", "db", "database",
           "pipeline", "ingest")),
    (210, ("frontend", "front end", "ui", "page", "board", "display",
           "render", "css", "design", "layout")),
    (265, ("test", "verify", "check", "review", "audit", "eval")),
    (30,  ("ship", "release", "publish", "deploy", "submit", "upload",
           "build and", "app store")),
    (330, ("doc", "readme", "write", "spec", "plan", "polish", "word")),
    (95,  ("feature", "adjust", "fix", "refactor", "clean")),
)


def _hue(text: str) -> int:
    t = (text or "").lower()
    for hue, words in DOMAIN_HUES:
        if any(w in t for w in words):
            return hue
    # No table entry: hash the text itself. zlib.crc32 is stable across
    # processes and Python versions, which hash() deliberately is not.
    import zlib
    return zlib.crc32(t.encode("utf-8")) % 360


def _tree(m) -> list[dict]:
    """The plan as nested rows the page can render without re-deriving it.

    Rows carry `hid` for finished work — itself done, or under something done.
    The page folds those away, because a card is only useful at a glance if
    what is left fits on it; the finished rows stay one click away rather than
    disappearing, since "what did we already do" is a real question.
    """
    from .claims import verdict_for as _verdict

    def walk(nodes, depth=0, guides=(), hidden=False, hue=None):
        out = []
        for idx, n in enumerate(nodes):
            last = idx == len(nodes) - 1
            done = n.complete if n.children else n.item.done
            # C17: the agent's claims-done suggestion, with the DISK's verdict
            # attached at render time -- so the row the human confirms shows
            # the evidence, not the agent's confidence.
            cd = None
            if n.item.claimed_done and not done:
                v = _verdict(n.item.claimed_done, cwd=m.cwd or "",
                             accepted_at=getattr(n.item, "accepted_at", 0.0))
                cd = {"text": n.item.claimed_done[:160], **v}
            # Colour is the GROUP, so a child carries its top-level subgoal's
            # hue: the point is telling the backend block from the frontend
            # block at a glance, not painting every row its own shade.
            h = hue if hue is not None else _hue(n.item.text)
            out.append({
                "id": n.item.id, "t": n.item.text, "d": depth,
                "done": done,
                "ok": n.item.accepted,
                "branch": bool(n.children),
                "roll": f"{n.done_count}/{n.total}" if n.children else "",
                "pct": round(100 * n.done_count / n.total) if n.children and n.total else 0,
                "guides": list(guides), "last": last,
                "hid": hidden or done,
                "hue": h,
                "cd": cd,
            })
            out.extend(walk(n.children, depth + 1, (*guides, not last),
                            hidden or done, h))
        return out
    return walk(m.tree())


def _safe_load(path):
    """Load a mission, or None if that session's log is unreadable.

    snapshot() reads every session on the board, and the first request is not
    inside the background refresh's try/except. So an exception here took the
    whole page down for everyone rather than losing one card.
    """
    try:
        st = MissionStore(path)
        m = st.load()
    except Exception as exc:
        # A card that VANISHES is the worst outcome: the goal is still on
        # disk, the board shows nothing, and nothing says so. Return a
        # sentinel the renderer turns into a visible "log damaged" row.
        return _Damaged(path.name, f"{type(exc).__name__}: {exc}")
    if m is None:
        return None
    if st.damaged:
        # Loaded, but part of the log was unusable. The card renders
        # normally and carries the count, because the plan it shows is now
        # known to be incomplete.
        m.damaged = st.damaged
        m.damage = list(st.damage)
    return m


class _Damaged:
    """A mission whose log could not be folded at all.

    Not None: None means "no mission here", and the two must not render the
    same. `mission doctor` is the only thing that can act on it, so the row
    says that and nothing else.
    """

    archived = False
    checklist: list = []
    detours: list = []

    def __init__(self, mid: str, why: str):
        self.id = mid
        self.why = why
        self.title = mid
        self.objective = ""
        self.damaged = 1
        self.damage = [why]


def _missions_home() -> Path:
    import os
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


def _mtime(path) -> float:
    """mtime, or 0.0 when the file cannot be stat'd. 0.0 reads as very old."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _queue_rows(m, mid: str) -> dict:
    """Each lane as plain rows the page can render without re-deriving.

    The lane assignment happens ONCE, in `Mission.lane_of`, so the board and
    `mission queue` cannot disagree about where an item is -- which they
    would the moment two copies of the rule existed.
    """
    import time as _t
    from .delegable import verdict_for as _delegable
    now = _t.time()
    _stale = {i.id for i in m.untouched(now=now)}
    out: dict = {}
    for lane, items in m.lanes(now).items():
        if lane == "done":
            continue
        out[lane] = [{
            "id": i.id, "text": i.text[:160], "goal": mid,
            "goal_title": m.title,
            "rework": (i.rework or "")[:200],
            "rework_by": short_id(i.rework_by) if i.rework_by else "",
            "held_by": short_id(i.leased_by) if i.leased_by else "",
            "held_min": int((now - i.leased_at) // 60) if i.leased_at else 0,
            "ttl_min": max(0, int((LEASE_TTL - (now - i.leased_at)) // 60))
            if i.leased_at else 0,
            "claim": (i.claimed_done or "")[:160],
            # WHICH session said it was done. Populated in the store by
            # `claims_done` and never carried here, so the review lane showed
            # that something awaited a check but not who had claimed it --
            # the one fact you need to know whether the checker and the
            # claimant are the same session.
            "claimed_by": short_id(i.claimed_by) if i.claimed_by else "",
            # A second session's OPINION, carried separately from the disk's
            # verdict so the page can never print one as the other.
            "checked_note": (i.checked_note or "")[:200],
            "checked_by": short_id(i.checked_by) if i.checked_by else "",
            # C19: could a FRESH instance be handed this one? Mechanical,
            # from the fields above -- never a reading of the text. The
            # negative case is the useful one, so the fact travels with the
            # flag and the page prints it rather than a bare badge.
            "delegable": _delegable(m, i, now).ok,
            "delegable_fact": _delegable(m, i, now).fact[:140],
            # Agreed long ago and never touched. A measured pair -- the
            # accept time, and the absence of every lease/claim/release --
            # not a reading of the code. -1 means the log never recorded an
            # accept time, which is unknown rather than old.
            "untouched_days": (round(m.days_since_accepted(i, now), 0)
                               if i.id in _stale else 0),
        } for i in items]
    return out


def mission_rows() -> list[dict]:
    """One card per GOAL, with the sessions that served it inside it.

    Activity sums across every attached session, because "how much work has
    gone into this goal" is the question -- and one goal spanning four sessions
    used to read as four cards each showing a slice.
    """
    # transcript_for is already imported at module level. Re-importing it here
    # shadowed it, so a test could not substitute it -- and neither could
    # anything else that needed to.
    from . import missions as M
    from . import heartbeat as HB
    out = []
    # C19-5(b): liveness is now REPORTED by each session from the hook that
    # runs every turn, keyed on the Claude pid the hook can see and nothing
    # outside can. The guess it replaces mapped a cwd to one project dir and
    # took the top N transcripts by mtime -- measured on 2026-10-08 that
    # reported six ~4-hour-dead sessions as live and DROPPED a live idle one,
    # which was the dimming the board had already been patched for once.
    #
    # `known` is the honest half: a session that has never beaten is UNKNOWN,
    # not dead. Collapsing those two was the whole defect, and the same rule
    # the watcher learned elsewhere -- unreachable is not closed.
    live_ids = HB.live_ids()
    known_ids = HB.known_ids()

    for mid, st in M.all_missions():
        m = _safe_load(st.root)
        if m is None or m.archived:
            continue
        if isinstance(m, _Damaged):
            # Say so in the one place a person is looking. The alternative --
            # what this did before -- was to return 0 rows and leave the goal
            # looking deleted.
            # Same key set as a healthy row, because the renderer reads
            # these by name and a row missing one is a card that throws
            # instead of a card that reports. (The first version of this
            # invented `checklist`/`note`, which no template reads.)
            out.append({
                "id": mid, "full": mid, "mission": True, "cwd": "",
                "title": mid, "objective": "", "named": False,
                "criteria": [], "constraints": [], "non_goals": [],
                "tree": [], "done": 0, "total": 0, "pending_accept": 0,
                "sessions": [], "procs": 0, "ended": True,
                "has_mission": True,
                "calls": 0, "files": 0, "tests": 0, "failures": 0,
                "claims_checked": 0, "claims_bad": [],
                "asks": [], "topfiles": [], "models": [],
                "model_changed": False, "repeats": 0, "exact_repeats": 0,
                "worst_repeat": None, "collisions": [], "children": [],
                "mtime": 0,
                "damaged": 1, "damage": list(m.damage),
            })
            continue
        calls = files = tests = fails = 0
        sess = []
        claims_bad, claims_checked = [], 0
        for sid in M.sessions_of(mid):
            tp = transcript_for(sid)
            a = activity(tp) if tp else None
            if a:
                calls += a.calls; files += len(a.files)
                tests += a.tests; fails += a.failures
            # C16: the tail of each LIVE session's transcript, checked against
            # disk. Live only -- an ended session's claims were either caught
            # at the time or are history, and scanning every transcript ever
            # would make the 4s refresh pay for the archive.
            # Live, or not yet reporting and touched within SCAN_WINDOW.
            # That mtime test is a COST bound, not a liveness claim: scanning
            # every transcript ever recorded would make the 4-second refresh
            # pay for the archive. A session with no heartbeat still gets
            # scanned while it is recent, so installing the hook is an
            # improvement rather than a precondition.
            # Guarded: a transcript can be rotated or deleted between the
            # glob and the stat, and an OSError here would blank the whole
            # board rather than one row. (Caught by an existing test that
            # stubs the path -- the first version stat'd it bare.)
            recent = tp is not None and (time.time() - _mtime(tp)) < SCAN_WINDOW
            if tp and (sid in live_ids or (sid not in known_ids and recent)):
                from .claims import scan as _claims_scan
                r = _claims_scan(tp, sid, mission_cwd=m.cwd or "")
                claims_checked += r["checked"]
                claims_bad.extend(r["reportable"])
            sess.append({"id": short_id(sid), "full": sid,
                         "live": sid in live_ids,
                         # Three states, never two. The page must be able to
                         # say "unknown" rather than print a death it cannot
                         # support.
                         "liveness": ("live" if sid in live_ids
                                      else "ended" if sid in known_ids
                                      else "unknown"),
                         "calls": a.calls if a else 0})
        out.append({
            "id": mid, "full": mid, "mission": True,
            "cwd": (m.cwd or "").replace(str(Path.home()), "~"),
            "title": m.title, "objective": m.objective, "named": bool(m.name),
            "criteria": m.success_criteria, "constraints": m.constraints,
            "non_goals": m.non_goals, "tree": _tree(m),
            "done": m.done_count, "total": m.total_count,
            "pending_accept": len(m.unaccepted),
            "sessions": sess, "procs": sum(1 for x in sess if x["live"]),
            # Ended only when every session REPORTED and none is live. One
            # unknown and the goal is not called ended -- dimming a card on
            # an absent heartbeat is the exact mistake being removed.
            "ended": (bool(sess)
                      and all(x["liveness"] == "ended" for x in sess)),
            "liveness_unknown": sum(1 for x in sess
                                    if x["liveness"] == "unknown"),
            "has_mission": True,
            "calls": calls, "files": files, "tests": tests, "failures": fails,
            "claims_checked": claims_checked,
            "claims_bad": claims_bad[:4],
            "asks": [], "topfiles": [], "models": [], "model_changed": False,
            "repeats": 0, "exact_repeats": 0, "worst_repeat": None,
            "collisions": [], "children": [],
            "mtime": st.log.stat().st_mtime,
            # The four lanes, flat, for the work view. Grouped by WHO ACTS
            # NEXT rather than by goal: "which session handles what" is the
            # question a board with several live sessions has to answer, and
            # a per-goal tree cannot answer it at a glance. The tree is still
            # there, per card, for "which piece does this belong to".
            "queue": _queue_rows(m, mid),
            # Loaded, but some events were unusable, so the plan shown is
            # known to be incomplete. Reported on the card rather than only
            # in `doctor`, because the board is where the plan is read.
            "damaged": getattr(m, "damaged", 0),
            "damage": list(getattr(m, "damage", []))[:3],
        })
    return out


def snapshot() -> list[dict]:
    from . import missions as M
    if M.all_missions():
        rows = mission_rows()
        from .doctor import review as _review
        try:
            lane = _review()
        except Exception:
            lane = []
        per = {}
        for f in lane:
            per.setdefault(f["sid"], []).append({"what": f["what"],
                                                 "detail": f["detail"]})
        now = time.time()
        for r in rows:
            r["review"] = per.get(r["full"], [])
            r["state"] = _state(r, now)
            r["needs_you"] = r["state"] in ("waiting", "nomission", "review")
        order = {"review": 0, "waiting": 1, "nomission": 2, "working": 3,
                 "idle": 4, "ended": 5}
        # Live work outranks a finished goal, whatever is pending on it. A
        # one-off test mission with four unaccepted proposals was sorting above
        # three sessions that were actually running.
        return sorted(rows, key=lambda r: (r["ended"], order[r["state"]],
                                           -r["mtime"]))
    return _session_snapshot()


def _session_snapshot() -> list[dict]:
    rows, seen = [], set()
    for proc in live():
        d = PROJECTS / _slug(proc["cwd"])
        if not d.exists():
            continue
        tps = sorted((p for p in d.glob("*.jsonl") if p.stat().st_size > 2000),
                     key=lambda p: -p.stat().st_mtime)[:proc["procs"]]
        for tp in tps:
            sid = tp.stem
            if sid in seen:
                continue
            seen.add(sid)
            m = _safe_load(root_for(sid))
            a = activity(tp)
            h = inspect_health(tp)
            rows.append({
                "id": short_id(sid), "full": sid,
                "parent": m.parent_session if m else "",
                "parent_item": m.parent_item if m else "",
                "cwd": proc["cwd"].replace(str(Path.home()), "~"),
                "procs": proc["procs"],
                "has_mission": m is not None, "ended": False,
                "title": m.title if m else "", "objective": m.objective if m else "",
                "named": bool(m.name) if m else False,
                "criteria": m.success_criteria if m else [],
                "constraints": m.constraints if m else [],
                "non_goals": m.non_goals if m else [],
                "tree": _tree(m) if m else [],
                "done": m.done_count if m else 0,
                "total": m.total_count if m else 0,
                "pending_accept": len(m.unaccepted) if m else 0,
                "calls": a.calls, "files": len(a.files), "tests": a.tests,
                "failures": a.failures, "asks": a.last_asks,
                "topfiles": [{"f": k, "n": v} for k, v in list(a.files.items())[:8]],
                "models": h.model_order, "model_changed": h.model_changed,
                "repeats": len(h.repeats), "exact_repeats": h.exact_repeats,
                "worst_repeat": h.repeats[0] if h.repeats else None,
                "_files": a.files,
                "mtime": tp.stat().st_mtime,
            })
    # Files two live sessions have both written. Nothing inside either session
    # can see the other, so this is only visible from here.
    clash = collisions({r["id"]: r.pop("_files", {}) for r in rows})
    for r in rows:
        r["collisions"] = [c["file"] for c in clash
                           if r["id"] in c["sessions"]][:6]

    # A mission whose session has ended must not vanish from the board -- the
    # work happened, and losing sight of it is exactly what this exists to
    # prevent. Ended sessions are shown, marked, and sorted last.
    home = _missions_home()
    if home.exists():
        for d in home.iterdir():
            if not d.is_dir() or d.name in seen or not (d / "events.jsonl").exists():
                continue
            m = _safe_load(d)
            if m is None:
                continue
            tp = transcript_for(d.name)
            a = activity(tp) if tp else None
            rows.append({
                "id": short_id(d.name), "full": d.name,
                "parent": m.parent_session, "parent_item": m.parent_item,
                "cwd": (m.cwd or "").replace(str(Path.home()), "~"),
                "procs": 0, "ended": True,
                "has_mission": True, "title": m.title, "objective": m.objective,
                "named": bool(m.name),
                "criteria": m.success_criteria, "constraints": m.constraints,
                "non_goals": m.non_goals,
                "tree": _tree(m),
                "done": m.done_count, "total": m.total_count,
                "pending_accept": len(m.unaccepted),
                "calls": a.calls if a else 0, "files": len(a.files) if a else 0,
                "tests": a.tests if a else 0, "failures": a.failures if a else 0,
                "asks": a.last_asks if a else [],
                "topfiles": [{"f": k, "n": v} for k, v in
                             list(a.files.items())[:8]] if a else [],
                "models": [], "model_changed": False, "repeats": 0,
                "exact_repeats": 0, "worst_repeat": None, "collisions": [],
                "mtime": (d / "events.jsonl").stat().st_mtime,
            })
    # A delegated mission is a slice of its parent's work, not a peer. Left as
    # its own card it triples the board: one real session produced five cards
    # in testing, four of them children. Attach it to the parent instead, and
    # only promote it if the parent is not on the board at all.
    by_full = {r["full"]: r for r in rows}
    kids: list[dict] = []
    for r in rows:
        parent = by_full.get(r.get("parent") or "")
        if parent is not None and parent is not r:
            parent.setdefault("children", []).append({
                "id": r["id"], "title": r["title"], "item": r["parent_item"],
                "done": r["done"], "total": r["total"], "calls": r["calls"],
            })
            kids.append(r)
    rows = [r for r in rows if r not in kids]
    for r in rows:
        r.setdefault("children", [])
    # One state per session, so the page never re-derives it and the sort,
    # the icon and the filters cannot disagree about what a card is.
    from .doctor import review as _review
    try:
        lane = _review()
    except Exception:
        lane = []
    per = {}
    for f in lane:
        per.setdefault(f["sid"], []).append({"what": f["what"],
                                             "detail": f["detail"]})
    now = time.time()
    for r in rows:
        r["review"] = per.get(r["full"], [])
        r["state"] = _state(r, now)
        r["needs_you"] = r["state"] in ("waiting", "nomission")
    # Sorted by who needs you, not by what you touched last: a session with
    # proposals waiting outranks one that is merely more recent.
    order = {"review": 0, "waiting": 1, "nomission": 2, "working": 3,
             "idle": 4, "ended": 5}
    # Ended sessions still sort last even when they are waiting on you: the
    # ask is worth surfacing, not worth putting above live work.
    return sorted(rows, key=lambda r: (r.get("ended", False),
                                       order[r["state"]], -r["mtime"]))


IDLE_AFTER = 15 * 60          # no transcript write for this long = parked
# How far back to claim-scan a transcript whose session has never reported a
# heartbeat. A COST bound, not a liveness claim: without it the 4-second
# refresh would re-scan every transcript ever written, which is why the scan
# was live-only before liveness became reportable.
SCAN_WINDOW = 24 * 3600


def _state(r: dict, now: float) -> str:
    """What this card IS, in one word.

    Deliberately not "how far along": that is the progress bar's job. This
    answers the only question you ask while scanning six cards at once --
    which of these is waiting on me.
    """
    # "waiting" outranks "ended": a finished session whose proposals you never
    # ruled on is precisely the thing that gets lost, and hiding it from the
    # count was the first thing the strip got wrong -- it read "nothing is
    # waiting on you" above two cards saying "awaiting accept".
    # A finding a person must re-read outranks a proposal waiting: one is
    # something possibly wrong with the record, the other is ordinary work.
    if r.get("review"):
        return "review"
    if r["has_mission"] and r["pending_accept"]:
        return "waiting"
    if r.get("ended"):
        return "ended"
    if not r["has_mission"]:
        return "nomission"
    return "working" if now - r["mtime"] < IDLE_AFTER else "idle"


PAGE = """<!doctype html><meta charset=utf-8><title>Missions</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#F7F7F8;--card:#fff;--ink:#151518;--mut:#66666E;--rule:#DEDEE3;--soft:#EEEEF2;
--ok:#5A5AD8;--bad:#92661C;--okw:#EBEBFB;--badw:#F7EDD8;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
--mono:ui-monospace,"SF Mono",Menlo,monospace;
/* The reading column for the single-column views (work, inbox). The grid
   sizes itself from its cards; these do not, and both referenced an
   undefined --wrap, so they ran the full width of the window. */
--wrap:58rem}
@media(prefers-color-scheme:dark){:root{--bg:#0B0B0D;--card:#131316;--ink:#EDEDF0;
--mut:#84848D;--rule:#222228;--soft:#1A1A20;--ok:#8B8BF5;--bad:#F5B942;
--okw:#191930;--badw:#221A0A}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;
margin:0;padding:1.3rem 1.5rem 4rem;-webkit-font-smoothing:antialiased}
header{display:flex;gap:1rem;align-items:baseline;margin-bottom:1rem}
h1{font-family:var(--mono);font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;
color:var(--mut);font-weight:500;margin:0}
#age{font-family:var(--mono);font-size:.66rem;color:var(--mut)}
/* align-items:start, or every card stretches to the tallest in its row and
   short missions render as a title floating above a field of empty card. */
.grid{display:grid;gap:1.1rem;align-items:start;
/* min(23rem,100%): a bare 23rem minimum is wider than a 375px phone, so the
   whole page scrolled sideways. */
grid-template-columns:repeat(auto-fill,minmax(min(23rem,100%),1fr))}
.card{background:var(--card);border:1px solid var(--rule);border-radius:7px;
padding:1.1rem 1.2rem;transition:border-color .15s ease,transform .15s ease}
.card:hover{border-color:var(--mut)}
/* The archive control appears on hover: it is rare, irreversible-feeling, and
   should not sit in the reading path of a card you are just looking at. */
.card .sid .act{opacity:0}
.card:hover .sid .act,.card .sid .act:focus{opacity:1}
.card.ended{opacity:.62}
/* A long cwd used to run straight into "1 live here" at narrow widths --
   they are separate facts and read as one string. Gap, and the path is the
   half that truncates. */
.sid{font-family:var(--mono);font-size:.68rem;color:var(--mut);display:flex;
justify-content:space-between;gap:.75rem;margin-bottom:.5rem}
.sid>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sid>span:last-child{flex:none}
/* Two lines maximum. A session named from a pasted prompt ran to four and
   pushed the plan under the fold. */
.obj{font-size:1.05rem;line-height:1.35;font-weight:600;margin:0 0 .25rem;
text-wrap:pretty;letter-spacing:-.008em;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden}
.objsub{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
overflow:hidden}
.objsub{font-size:.83rem;line-height:1.45;color:var(--mut);margin:0 0 .7rem;text-wrap:pretty}
.none{color:var(--mut);font-size:.85rem;line-height:1.5}
.none code{font-family:var(--mono);font-size:.78rem;background:var(--soft);
padding:.1rem .35rem;border-radius:3px}
h3{font-family:var(--mono);font-size:.6rem;letter-spacing:.13em;text-transform:uppercase;
color:var(--mut);font-weight:500;margin:.9rem 0 .35rem}
ul{margin:0;padding-left:1.05rem}
li{margin:.16rem 0;line-height:1.45;font-size:.86rem}
li.ng{color:var(--mut)}
.chk{list-style:none;padding:0}
.chk li{display:flex;gap:0;align-items:flex-start;font-size:.87rem;padding:.12rem 0;
line-height:1.45}
/* Tree connectors were drawn in --rule, which on the dark theme is within a
   few points of the card background: the nesting the tree exists to show was
   invisible. Muted, not hidden. */
.chk .g{flex:none;width:1.05rem;font-family:var(--mono);color:var(--mut);
opacity:.55;white-space:pre;-webkit-user-select:none;user-select:none}
.chk .box{flex:none;font-family:var(--mono);color:var(--mut);padding-right:.42rem}
.chk li.done{color:var(--mut)}
.chk li.done .txt{text-decoration:line-through;
text-decoration-color:var(--rule);text-decoration-thickness:1px}
.chk li.done .box{color:var(--ok)}
.chk li.prop .box{color:var(--bad)}
.chk li.branch{font-weight:600;margin-top:.42rem}
.chk li.branch .txt{letter-spacing:-.005em}
/* A proposal can be a paragraph -- an agent explaining a blocker writes one.
   Unclamped, one item owns the card and the plan under it is pushed off the
   screen. Three lines, full text on hover. */
.chk .txt{flex:1;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
overflow:hidden}
.chk .roll{flex:none;font-family:var(--mono);font-size:.68rem;color:var(--mut);
padding-left:.6rem;display:flex;align-items:center;gap:.4rem}
.chk .mini{width:2.4rem;height:3px;background:var(--soft);border-radius:2px;overflow:hidden}
.chk .mini i{display:block;height:100%;background:var(--ok)}
/* Group colour: hue = which subgoal a row belongs to, deterministic, set
   server-side. Structure only -- guides, box, header, bar -- and muted, so
   the single warm accent keeps its monopoly on "waiting on you". A proposal's
   `?` stays accent-coloured even inside a hued group for exactly that
   reason. */
.chk li.hued .g{color:hsl(var(--grp) 22% 52% / .75)}
.chk li.hued .box{color:hsl(var(--grp) 38% 48%)}
.chk li.hued.branch .txt{color:hsl(var(--grp) 30% 42%)}
.chk li.hued.branch .box{color:hsl(var(--grp) 45% 45%)}
.chk li.hued .mini i{background:hsl(var(--grp) 40% 48%)}
@media(prefers-color-scheme:dark){
  .chk li.hued .g{color:hsl(var(--grp) 22% 48% / .8)}
  .chk li.hued .box{color:hsl(var(--grp) 40% 58%)}
  .chk li.hued.branch .txt{color:hsl(var(--grp) 42% 68%)}
  .chk li.hued.branch .box{color:hsl(var(--grp) 48% 60%)}
  .chk li.hued .mini i{background:hsl(var(--grp) 45% 55%)}
}
.claims{color:var(--mut);font-size:.72rem;margin-top:.3rem;line-height:1.5}
.verdict{color:var(--mut);font-size:.7rem;font-family:var(--mono)}
.chk li.sugg .box{color:var(--ink)}
.sweep button{margin-right:.4rem}
.chk li.hued.done .box{color:var(--ok)}
.chk li.hued.prop .box{color:var(--bad)}
.box{font-family:var(--mono);color:var(--mut);flex:none}
/* Strike the TEXT, never the row: a row-level rule also struck the progress
   badge, so a finished branch read as "1/1" with a line through the number. */
.chk li.done{color:var(--mut)}
.chk li.done .box{color:var(--ok)}
.chk li.prop .box{color:var(--bad)}
/* A proposal is the one row asking something of the reader, so it gets the
   only accent in the list. */
.chk li.prop{color:var(--ink)}
.chk li:not(.branch):hover{background:var(--soft);border-radius:3px}
.doneblock{margin-top:.3rem}
.doneblock summary{font-family:var(--mono);font-size:.66rem;letter-spacing:.06em;
color:var(--mut);cursor:pointer;list-style:none;padding:.2rem 0;text-transform:uppercase}
.doneblock summary::-webkit-details-marker{display:none}
.doneblock summary::before{content:"▸ "}
.doneblock[open] summary::before{content:"▾ "}
.doneblock summary:hover{color:var(--ink)}
.chk.flat li{padding-left:.1rem}
.bar{height:5px;background:var(--soft);border-radius:3px;overflow:hidden;margin:.55rem 0 .1rem}
.bar i{display:block;height:100%;background:var(--ok);border-radius:3px;
transition:width .4s cubic-bezier(.4,0,.2,1)}
.meta{font-family:var(--mono);font-size:.7rem;color:var(--mut);margin-top:.75rem;
padding-top:.6rem;border-top:1px solid var(--soft);line-height:1.7}
.warn{color:var(--bad)}
.flags{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.7rem}
.flag{font-family:var(--mono);font-size:.66rem;padding:.16rem .45rem;border-radius:3px;
background:var(--badw);color:var(--bad)}
.flag.calm{background:var(--soft);color:var(--mut)}
.flagdet{font-family:var(--mono);font-size:.68rem;color:var(--mut);margin-top:.4rem;line-height:1.6}
.doneblock{margin-top:.5rem}
.doneblock summary{font-family:var(--mono);font-size:.66rem;letter-spacing:.08em;
text-transform:uppercase;color:var(--mut);cursor:pointer;list-style:none;
padding:.2rem 0;-webkit-user-select:none;user-select:none}
.doneblock summary::before{content:"▸ ";font-size:.7rem}
.doneblock[open] summary::before{content:"▾ "}
.doneblock summary:hover{color:var(--ink)}
.chk.flat{padding-left:.1rem;opacity:.85}
/* Both lists scroll inside their own block rather than growing the card.
   One session here finished 11 items and proposed 4 more; unbounded, the
   card ran past the fold and the goal at the top scrolled out of sight —
   which is the one thing the board must never do. ~10 rows, then scroll. */
.chk{max-height:15.5rem;overflow-y:auto;overscroll-behavior:contain}
.doneblock .chk{max-height:14rem}
.chk::-webkit-scrollbar{width:7px}
.chk::-webkit-scrollbar-thumb{background:var(--rule);border-radius:4px}
.chk::-webkit-scrollbar-thumb:hover{background:var(--mut)}
.chk::-webkit-scrollbar-track{background:transparent}
.chk{scrollbar-width:thin;scrollbar-color:var(--rule) transparent}
/* Header stays put; only the board scrolls, so search and the counts are
   reachable however far down you are. */
header{position:sticky;top:0;z-index:5;background:var(--bg);padding-bottom:.7rem;
flex-wrap:wrap;row-gap:.55rem}
#q{font-family:var(--sans);font-size:.8rem;color:var(--ink);background:var(--card);
border:1px solid var(--rule);border-radius:4px;padding:.32rem .6rem;width:15rem;
outline:none}
#q:focus{border-color:var(--ok)}
/* The work view: four lanes, grouped by who acts next.

   THE ACCENT RULE: amber (--warn) is used on the `waiting on you` lane
   heading and its count, and NOWHERE else here. Rework is red, because it is
   urgent for a WORKER and not for the person -- amber on both would put them
   in competition and the accent would stop meaning "nothing moves until you
   click". `held` and `fresh` chips are deliberately mute. */
#work{max-width:var(--wrap);margin:0 auto;padding:0 1.1rem 2rem}
#work .qstats{display:flex;flex-wrap:wrap;gap:1.1rem;align-items:baseline;
font-size:.78rem;color:var(--mut);margin:.4rem 0 1rem}
#work .qstats b{color:var(--ink);font-size:1.15rem;font-weight:600;
margin-right:.25rem}
#work .qstats .warn b{color:var(--bad)}
#work .qstats .qsess{margin-left:auto}
#work .qheld{border:1px dashed var(--bad);border-radius:6px;
padding:.45rem .7rem;font-size:.78rem;color:var(--bad);margin:0 0 1.1rem}
#work h2.qh{font-size:.95rem;margin:1.3rem 0 .1rem;font-weight:600}
#work h2.qh b{font-weight:600}
#work .qlist.waiting + *, #work h2.qh b{color:inherit}
#work .qsub{color:var(--mut);font-size:.76rem;margin:0 0 .6rem}
#work .qgroup{margin:0 0 .7rem}
#work .qgh{font-size:.8rem;color:var(--mut);margin:0 0 .25rem;
font-weight:500;cursor:default}
#work .qfold > summary{cursor:pointer;color:var(--mut);font-size:.76rem;
padding:.25rem 0}
#work .qfold[open] > summary{margin-bottom:.3rem}
#work .qfold code{font-size:.72rem}
#work .qgh b{color:var(--ink);font-weight:600}
#work .qgstale{color:var(--mut)}
#work .qsub code{font-size:.72rem}
#work .qlist{list-style:none;margin:0;padding:0;border:1px solid var(--rule);
border-radius:8px;overflow:hidden}
#work .qrow{display:flex;gap:.6rem;align-items:flex-start;
padding:.5rem .7rem;border-bottom:1px solid var(--rule)}
#work .qrow:last-child{border-bottom:none}
#work .qid{font-family:var(--mono);font-size:.72rem;color:var(--mut);
min-width:4.6rem}
#work .qbody{flex:1;min-width:0}
#work .qtext{font-size:.84rem}
#work .qgoal{color:var(--mut);font-size:.72rem;margin-left:.4rem}
#work .qmeta{color:var(--mut);font-size:.73rem;margin-top:.1rem}
/* Deliberately NOT coloured. The mockup for this drew rework in red; this
   palette has no red -- `--bad` IS the amber -- and adding one so a sketch
   could be right would break "one accent, one meaning" to decorate a lane
   that is urgent for a WORKER, not for the person reading the board. The
   word and the reason carry it. */
#work .qrework{color:var(--ink);font-size:.73rem;margin-top:.1rem}
/* Mute, and italic, so a model's opinion never reads with the weight of the
   disk verdict printed above it. */
#work .qopinion{color:var(--mut);font-size:.73rem;margin-top:.1rem;
font-style:italic}
/* C19 eligibility. Both muted, because neither is "waiting on you" -- the
   amber in this view has one meaning and this is not it. The two differ only
   in weight: an item a fresh session can take is ordinary, an item needing
   context is the one worth reading, so it gets the ink. */
/* Hollow, because it asserts nothing. A filled dot in either colour would
   claim a state the board does not know. */
.dot.unknown{background:transparent;border:1px solid var(--mut)}
/* The eligibility and untouched advisories were a line each here and are
   chips now -- see laneRow. The rules they used are gone rather than left
   unreferenced: dead CSS reads as a style someone might still be using. The
   accent law they were written under still holds (amber means waiting on
   you, nothing else), which is why the chips are the row's existing mute
   border treatment and the only amber left in this view is the strip. */
/* Decline sits beside accept and must not compete with it: muted, never the
   accent. The accent means "waiting on you"; a decline button IS one of the
   things you are waiting to do, so colouring it would say nothing new. */
.act.warn{color:var(--mut)}
.act.warn:hover{color:var(--ink);border-color:var(--ink)}
#work .qchip{font-size:.68rem;color:var(--mut);border:1px solid var(--rule);
border-radius:4px;padding:.05rem .35rem;white-space:nowrap}
#work .qchip.rework{color:var(--ink);border-color:var(--ink)}
/* The only amber in this view: the waiting-on-you lane, its count and its
   held-dispatch banner -- one meaning, three places that say it. */
#work .qlist.waiting{border-color:var(--bad)}
/* The Inbox. Everything here is waiting on you, so the amber accent appears
   ONCE, on the heading -- painting it per row would make the accent mean
   "a row" instead of "waiting on you", which is the C12d wallpaper failure at
   a smaller scale. Rows keep the glyph conventions the cards already use. */
#inbox{max-width:var(--wrap);margin:0 auto;padding:0 1.1rem 2rem}
#inbox h2{font-size:.95rem;margin:.2rem 0 .1rem}
#inbox h2 b{color:var(--bad)}
#inbox .sub{color:var(--mut);font-size:.76rem;margin:0 0 1rem}
#inbox .gl{display:flex;align-items:baseline;gap:.5rem;margin:1.1rem 0 .3rem;
border-bottom:1px solid var(--rule);padding-bottom:.25rem}
#inbox .gl h3{font-size:.8rem;margin:0;font-weight:600}
#inbox .gl .when{font-family:var(--mono);font-size:.66rem;color:var(--mut)}
#inbox .gl .act{opacity:1;margin-left:auto}
#inbox ul{list-style:none;padding:0;margin:0}
#inbox li{display:flex;gap:.45rem;align-items:flex-start;padding:.3rem 0;
font-size:.87rem;border-bottom:1px solid var(--soft)}
#inbox li .box{flex:none;font-family:var(--mono);color:var(--mut)}
#inbox li.prop .box{color:var(--bad)}
#inbox li .txt{flex:1}
#inbox li .act{opacity:1}
#inbox .verdict{color:var(--mut);font-size:.74rem;margin-top:.12rem}
#inbox .none{color:var(--mut);padding:2rem 0}
/* The keyboard cursor. A left rule rather than a background, because a filled
   row would compete with the proposal glyph for the same attention -- and a
   single-sided border takes no radius (the hub learned that one too). */
#inbox li.kcur{border-left:2px solid var(--ink);border-radius:0;
padding-left:.45rem;margin-left:-.57rem}
#inbox .keys{color:var(--mut);font-size:.72rem;margin:.1rem 0 1rem}
.chips{display:flex;gap:.3rem}
.chip{font-family:var(--mono);font-size:.66rem;letter-spacing:.04em;padding:.26rem .55rem;
border-radius:3px;border:1px solid var(--rule);background:var(--card);color:var(--mut);
cursor:pointer}
.chip[aria-pressed=true]{background:var(--ok);border-color:var(--ok);color:var(--bg)}
.kids{margin-top:.75rem;padding-top:.6rem;border-top:1px solid var(--soft)}
.kid{display:flex;gap:.5rem;align-items:baseline;font-size:.8rem;padding:.14rem 0}
.kid .kn{font-family:var(--mono);font-size:.66rem;color:var(--mut);flex:none}
.kid .kt{flex:1;text-wrap:pretty}
.kid .kp{font-family:var(--mono);font-size:.66rem;color:var(--mut);flex:none}
.spacer{flex:1}
#setup{border:1px solid var(--rule);background:var(--card);border-radius:7px;
padding:.75rem .95rem;margin-bottom:1.1rem;font-size:.85rem}
#setup h4{margin:0 0 .5rem;font-family:var(--mono);font-size:.66rem;
letter-spacing:.1em;text-transform:uppercase;color:var(--mut);font-weight:500}
#setup .srow{display:flex;align-items:baseline;gap:.6rem;padding:.22rem 0}
#setup .sname{width:9rem;flex:none}
#setup .sdet{flex:1;color:var(--mut);font-family:var(--mono);font-size:.7rem;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#setup .diff{font-family:var(--mono);font-size:.7rem;color:var(--mut);
padding:.2rem 0 .4rem 9.6rem;line-height:1.6}
.act{font-family:var(--mono);font-size:.64rem;padding:.1rem .35rem;border-radius:3px;
border:1px solid var(--rule);background:none;color:var(--mut);cursor:pointer;
flex:none;margin-left:.4rem;opacity:0;transition:opacity .12s}
.chk li:hover .act,.act:focus{opacity:1}
.act:hover{color:var(--ink);border-color:var(--mut)}
.act.ok{color:var(--ok);border-color:var(--ok)}
/* Confirming without evidence is a legitimate human call, so it gets a real
   button -- but it must never look like the backed sweep sitting beside it.
   Muted and outlined, not the confident accent. */
.act.warn{color:var(--mut);border-color:var(--mut);border-style:dashed}
.act.warn:hover{color:var(--bad);border-color:var(--bad)}
#code{position:fixed;inset:auto 1.2rem 1.2rem auto;background:var(--card);
border:1px solid var(--bad);border-radius:7px;padding:.8rem .95rem;max-width:22rem;
font-size:.82rem;line-height:1.5;z-index:20}
#code input{font-family:var(--mono);font-size:.85rem;letter-spacing:.15em;width:6.5rem;
padding:.25rem .4rem;margin-right:.4rem;background:var(--bg);color:var(--ink);
border:1px solid var(--rule);border-radius:3px}
#code button{font-family:var(--mono);font-size:.7rem;padding:.28rem .55rem;
border-radius:3px;border:1px solid var(--rule);background:none;color:var(--ink);cursor:pointer}
#code .hint{color:var(--mut);font-size:.74rem;margin-top:.45rem}
.notebox{display:flex;gap:.4rem;margin-top:.6rem}
.notebox input{flex:1;font-family:var(--sans);font-size:.8rem;padding:.28rem .5rem;
background:var(--bg);color:var(--ink);border:1px solid var(--rule);border-radius:3px}
.notebox button{font-family:var(--mono);font-size:.68rem;padding:.28rem .5rem;
border:1px solid var(--rule);border-radius:3px;background:none;color:var(--mut);cursor:pointer}

/* ONE accent means "this is waiting on you". Red previously meant a pending
   proposal, a failed tool call AND a health warning -- three unrelated facts
   competing for the same alarm, so none of them read as urgent. Failures and
   health notes are grey now; only the ask is coloured. */
.flag{background:var(--soft);color:var(--mut)}
.flag.ask{background:var(--badw);color:var(--bad)}
.warn{color:var(--mut)}
.warn.ask{color:var(--bad)}

/* The strip answers "is anything waiting on me" without reading a single
   card, which is the question you actually arrive with. */
#errtoast{position:fixed;bottom:1rem;left:50%;transform:translate(-50%,150%);
  transition:transform .25s;z-index:98;max-width:34rem;font-size:.8rem;
  background:var(--card);border:1px solid var(--bad);color:var(--ink);
  border-radius:7px;padding:.55rem .9rem;box-shadow:0 4px 14px #0004}
#errtoast.up{transform:translate(-50%,0)}
#repout{position:fixed;inset:10% 15%;z-index:97;overflow:auto;background:var(--card);
  border:1px solid var(--rule);border-radius:9px;padding:1rem 1.2rem;font-size:.8rem}
#repout pre{background:var(--soft);padding:.7rem;border-radius:5px;overflow:auto;
  font-size:.68rem;line-height:1.5;user-select:all}
#repout button{font-family:var(--mono);cursor:pointer}
#reconn{position:fixed;top:0;left:0;right:0;z-index:99;text-align:center;
  font:600 .78rem/2.2 var(--mono);background:var(--bad);color:#fff;opacity:.92}
#strip{border:1px solid var(--rule);background:var(--card);border-radius:7px;
padding:.6rem .9rem;margin-bottom:1.1rem;font-size:.85rem;display:flex;
gap:1.1rem;align-items:center;flex-wrap:wrap}
#strip.clear{color:var(--mut);border-style:dashed;background:transparent}
#strip b{font-weight:600}
#strip .go{font-family:var(--mono);font-size:.7rem;color:var(--mut);cursor:pointer;
border:1px solid var(--rule);border-radius:3px;padding:.16rem .45rem;background:none}
#strip .go:hover{color:var(--ink);border-color:var(--mut)}

/* A status is a shape you can read peripherally, not a word you must parse. */
.dot{width:.5rem;height:.5rem;border-radius:50%;flex:none;display:inline-block}
.dot.working{background:var(--ok)}
.dot.waiting{background:var(--bad)}
.dot.nomission{background:var(--bad);opacity:.45}
.dot.idle{background:var(--mut);opacity:.5}
.dot.ended{background:var(--rule)}
/* Review keeps the SAME accent as "waiting on you" -- one colour, one meaning
   -- and a flag says which kind. A serious finding is rare (2 of 16 in the
   real corpus), so unlike the earlier border experiment this lights up almost
   nothing. If it ever lights up most of the board, the eligibility rule is
   wrong, not the styling. */
.dot.review{background:var(--bad);box-shadow:0 0 0 3px var(--badw)}
.card.review{border-color:var(--bad)}
.rev{border:1px solid var(--bad);background:var(--badw);border-radius:5px;
padding:.5rem .7rem;margin:.6rem 0 .2rem;font-size:.82rem;line-height:1.5}
.rev b{font-weight:600}
.rev .d{color:var(--mut);font-size:.76rem;margin:.2rem 0 .4rem}
.rev .acts{display:flex;gap:.4rem;flex-wrap:wrap}
.rev .acts button{font-family:var(--mono);font-size:.66rem;padding:.16rem .45rem;
border-radius:3px;border:1px solid var(--bad);background:none;color:var(--bad);
cursor:pointer}
.rev .acts button:hover{background:var(--bad);color:var(--bg)}
.rev code{font-family:var(--mono);font-size:.72rem;background:var(--card);
padding:.1rem .3rem;border-radius:3px;-webkit-user-select:all;user-select:all}
/* Every state that says "something needs doing" must show HOW. A read-only
   board has no buttons, so it prints the command instead -- click to select
   the whole thing, because the alternative is retyping an id by hand. */
.howto{font-family:var(--mono);font-size:.72rem;background:var(--soft);
color:var(--ink);border:1px solid var(--rule);border-radius:4px;
padding:.35rem .5rem;margin:.4rem 0 .1rem;-webkit-user-select:all;user-select:all;
cursor:copy;overflow-x:auto;white-space:nowrap}
.howto:hover{border-color:var(--mut)}
.why{color:var(--mut);font-size:.74rem;margin:.3rem 0 .1rem;line-height:1.5}
.card.flash{border-color:var(--ok)}
.asks{border:1px solid var(--bad);background:var(--badw);border-radius:5px;
padding:.3rem .55rem .45rem;margin:.5rem 0 .6rem}
.asks h3{margin:.15rem 0 .1rem;color:var(--bad)}
.asks .chk{max-height:11rem}
.st{display:flex;align-items:center;gap:.4rem}
/* No border tint for "waiting". On a real board five of six sessions had
   proposals outstanding, so every card lit up and the accent meant nothing
   again -- the same mistake as red meaning three things. The dot and the
   count carry it; the strip carries the total. */

/* Compact mode: cards are right for three sessions and wrong for eight. */
.grid.compact{grid-template-columns:1fr;gap:.35rem}
.grid.compact .card{display:flex;align-items:baseline;gap:.7rem;padding:.5rem .8rem}
.grid.compact .card>*:not(.rowline){display:none}
.rowline{display:none}
.grid.compact .rowline{display:flex;align-items:center;gap:.7rem;width:100%;
font-size:.85rem}
.grid.compact .rowline .rid{font-family:var(--mono);font-size:.68rem;color:var(--mut);
flex:none;width:9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grid.compact .rowline .rt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grid.compact .rowline .rp{font-family:var(--mono);font-size:.7rem;color:var(--mut);flex:none}
</style>
<header>
  <h1>Missions</h1>
  <input id=q type=search placeholder="filter by goal, task, folder…" autocomplete=off>
  <div class=chips>
    <button class=chip data-v=work aria-pressed=true>work</button>
    <button class=chip data-v=inbox aria-pressed=false>inbox</button>
    <button class=chip data-v=goals aria-pressed=false>goals</button>
  </div>
  <div class=chips id=fchips hidden>
    <button class=chip data-f=all aria-pressed=true>all</button>
    <button class=chip data-f=live aria-pressed=false>live</button>
    <button class=chip data-f=todo aria-pressed=false>needs you</button>
    <button class=chip data-f=ended aria-pressed=false>ended</button>
  </div>
  <span class=spacer></span>
  <button class=chip id=dense aria-pressed=false>compact</button>
  <button class=chip id=report aria-pressed=false title="collect what just went wrong, for pasting to Claude">report</button>
  <span id=age></span>
</header>
<div id=strip></div>
<div id=setup hidden></div>
<div id=work hidden></div>
<div id=inbox hidden></div>
<div class=grid id=g></div>
<div id=code hidden></div>
<div id=repout hidden></div>
<p class=none id=empty hidden>Nothing matches that.</p>
<script>
const esc=t=>(t||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
// Which rows survive the fold, with `last` recomputed over the SURVIVORS.
// `last` decides between "├" and "└", and it was computed over every sibling
// including the finished ones now hidden -- so the last visible child drew a
// "├" with nothing beneath it, a branch line into empty space.
function visible(tree){
  const rows = tree.filter(i=>!i.hid).map(i=>({...i}));
  rows.forEach((r,n)=>{
    const next = rows.slice(n+1).find(o=>o.d <= r.d);
    r.last = !next || next.d < r.d;
  });
  return rows;
}
// One row of the plan. `flat` drops the connectors: inside the finished fold
// the rows come from different parents, so a guide there would draw a branch
// that isn't on screen.
function row(i,flat,sid){
  // Roots are flush bullets with no connector, so the guide for the root level
  // refers to a line that is never drawn. Drop it, or children render as "│├"
  // against nothing.
  const guides = flat? '' : i.guides.slice(1).map(g=>`<span class=g>${g?'│':' '}</span>`).join('');
  const elbow  = (!flat && i.d)? `<span class=g>${i.last?'└':'├'}</span>` : '';
  // The hue is the GROUP -- every row under one top-level subgoal shares it,
  // so the backend block reads as one thing without reading a word. It is
  // structure, never attention: desaturated, and only on the skeleton
  // (guides, box, header, bar). The one warm accent still means exactly
  // "waiting on you" and nothing else.
  const hu = (typeof i.hue==='number')? `--grp:${i.hue}` : '';
  // C17: a claims-done row. The verdict is GREY -- a fact to read, never the
  // alert accent -- and the button says "confirm": same done event, arriving
  // pre-evidenced. The suggestion glyph is ◦, distinct from + proposals.
  const cd = i.cd;
  // Three verdicts, not two. "disk agrees" used to mean only that a file of
  // that name exists -- true of README.md in every repo -- so the middle case
  // below is the one this wording exists to stop being invisible.
  const cdline = cd? `<div class=verdict>agent says done — ${
      cd.ok? `named file${cd.backed>1?'s':''} exist${cd.backed>1?'':'s'}, changed since accepted (${cd.backed})`
       : cd.unbacked.length? `${cd.unbacked.length} claim${cd.unbacked.length>1?'s':''} NOT backed: ${esc(cd.unbacked[0].split('/').slice(-2).join('/'))}`
       : (cd.stale&&cd.stale.length)? `exists, UNTOUCHED since you accepted: ${esc(cd.stale[0].split('/').slice(-2).join('/'))}`
       : `nothing checkable in the claim`}</div>` : '';
  return `<li style="${hu}" class="${i.branch?'branch':''} ${
      i.done?'done':(i.ok?'':'prop')} ${typeof i.hue==='number'?'hued':''} ${cd?'sugg':''}"
      ${cd&&cd.ok?`data-backed="${i.id}"`:''} data-i="${i.id}">
    ${guides}${elbow}
    <span class=box>${i.done?'▪':(cd?'◦':(i.ok?'▫':'+'))}</span>
    <span class=txt title="${esc(i.t)}">${esc(i.t)}${cdline}</span>
    ${i.roll?`<span class=roll><span class=mini><i style="width:${i.pct}%"></i></span>${i.roll}</span>`:''}
    ${(WRITABLE && CODE() && !i.done)?
       (!i.ok? `<button class="act ok" data-do=accept data-s="${sid}" data-i="${i.id}">accept</button>`
             + `<button class="act warn" data-do=decline data-s="${sid}" data-i="${i.id}">decline</button>`
             : (!i.branch? `<button class=act data-do=done data-s="${sid}" data-i="${i.id}">${cd?'confirm':'tick'}</button>`:''))
      :''}
  </li>`;
}
const openFolds=new Set();
let WRITABLE=false;
// The code lives only in the browser that was told it. It is never fetched
// from the server, which is the entire point: the board can verify it and
// nothing else on this machine can obtain it.
const CODE = () => localStorage.getItem('mission-code') || '';

// ---- the customer-service loop ----------------------------------------
// When a click does nothing, the person has no thread to pull: the evidence
// is in a console they never open, on a page that cannot file its own bug.
// So the page keeps the last 20 things that went wrong, shows the newest as
// a toast, and the report button packages the lot for PASTING TO CLAUDE --
// the agent then reads the code, not the person. Clipboard, not an endpoint:
// a report the page could POST is a report an agent could fabricate or
// harvest, and this must stay a person handing evidence to their own agent.
const ERRS = [];
function note_err(msg){
  ERRS.push({at:new Date().toISOString().slice(11,19), msg:String(msg).slice(0,300)});
  if (ERRS.length>20) ERRS.shift();
  let t = document.getElementById('errtoast');
  if(!t){ t=document.createElement('div'); t.id='errtoast'; document.body.append(t); }
  t.textContent = ERRS[ERRS.length-1].msg + ' — the report button (top right) packages this for Claude';
  t.classList.add('up');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('up'), 6000);
}
window.addEventListener('error', e=>note_err('js error: '+(e.message||e.type)));
window.addEventListener('unhandledrejection', e=>note_err('unhandled: '+(e.reason&&e.reason.message||e.reason)));
async function build_report(){
  let ident={}; try{ ident=await (await fetch('/api/identity')).json() }catch(e){ ident={unreachable:true} }
  const cards=[...document.querySelectorAll('.card')].map(c=>{
    const t=c.querySelector('.obj'); const w=c.querySelector('.warn.ask');
    return `${t?t.textContent.slice(0,40):'?'} | ${w?w.textContent.trim().slice(0,40):'no pending'}`});
  return [
    'MISSION BOARD PROBLEM REPORT (paste this whole block to Claude)',
    `when: ${new Date().toISOString()}`,
    `board: pid ${ident.pid||'?'} v${ident.version||'?'} writes=${ident.writes} `+
      `${ident.unreachable?'UNREACHABLE':''}`,
    `page: writable=${WRITABLE} code_entered=${!!CODE()}`,
    `cards:`, ...cards.map(x=>'  '+x),
    ERRS.length? 'recent problems (newest last):' : 'recent problems: none recorded',
    ...ERRS.map(e=>`  ${e.at} ${e.msg}`),
    'ask: inspect the board code for why the above happened',
  ].join('\\n');
}
document.getElementById('report').addEventListener('click', async ()=>{
  const txt = await build_report();
  let ok=false; try{ await navigator.clipboard.writeText(txt); ok=true }catch(e){}
  const o=document.getElementById('repout'); o.hidden=false;
  o.innerHTML = `<b>${ok?'copied to clipboard':'clipboard blocked — copy the block below'}</b> — `+
    `paste it into your Claude session and it will investigate.`+
    `<pre>${esc(txt)}</pre><button onclick="this.parentElement.hidden=true">close</button>`;
});
// -----------------------------------------------------------------------

async function act(action, session, ids, text){
  const r = await fetch('/', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({code: CODE(), action, session, ids, text: text||''})});
  if (!r.ok){
    const e = await r.json().catch(()=>({error:'failed'}));
    if (r.status === 403) localStorage.removeItem('mission-code');
    note_err(`${action} failed: ${e.error||r.status}`);
  }
  await tick(true);          // your own click paints now, never on the poll
  return r.ok;
}

// The Setup panel: same five surfaces as `mission setup --check`, same
// functions behind them. It only exists on a board a person started, because
// the endpoint itself does not exist on a read-only one.
async function renderSetup(){
  const box = document.getElementById('setup');
  if (!WRITABLE || !CODE()){ box.hidden = true; return; }
  let d; try{ d = await (await fetch('/api/setup')).json() }catch(e){ box.hidden = true; return }
  const missing = d.surfaces.filter(s=>!s.ok);
  if (!missing.length){ box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<h4>Setup — ${missing.length} of ${d.surfaces.length} not installed</h4>` +
    d.surfaces.map(s=>`
      <div class=srow>
        <span class=sname>${s.ok?'✓':'·'} ${esc(s.name)}</span>
        <span class=sdet>${esc(s.detail)}</span>
        ${s.ok?'':`<button class="act ok" data-setup="${esc(s.name)}">install</button>`}
      </div>
      ${s.ok?'':`<div class=diff>${s.plan.map(c=>'→ '+esc(c)).join('<br>') ||
                                    'nothing to change'}</div>`}`).join('');
}

function askForCode(){
  const box = document.getElementById('code');
  // Only ask when the board can actually accept writes AND we have no code.
  if (!WRITABLE || CODE()){ box.hidden = true; return; }
  if (box.dataset.up) return;
  box.dataset.up = '1'; box.hidden = false;
  box.innerHTML = `<b>Write code</b><br>Shown in the terminal running
    <code>mission board</code>.
    <div style="margin-top:.5rem"><input id=codein maxlength=6 placeholder="······"
      autocomplete=off spellcheck=false><button id=codego>unlock</button></div>
    <div class=hint>The agent never sees this, which is why these buttons are
    yours. Skip it and the board stays read-only.</div>`;
  document.getElementById('codego').onclick = ()=>{
    const v = document.getElementById('codein').value.trim();
    if (v){ localStorage.setItem('mission-code', v); box.hidden = true; box.dataset.up=''; tick(); }
  };
  document.getElementById('codein').onkeydown = e=>{
    if (e.key === 'Enter') document.getElementById('codego').click();
  };
}
// Above this many unanswered proposals, dispatch refuses. Measured, not
// chosen: the live log showed 56 pending with a p90 accept time of 74h,
// so spawning more workers widens the one place that is already slowest.
const DISPATCH_MAX = 20;
let FILTER='all', QUERY='', VIEW='work';

// C19b.1. Everything waiting on you, across every goal, in one place.
//
// The rows themselves are NOT new -- each card already renders its own
// proposals, its claims-done suggestions and its findings, with the same
// buttons. What did not exist is the AGGREGATION: they were scoped one card
// each, so "is anything waiting on me" cost as many glances as there are
// goals, and a proposal on a goal you were not looking at sat for 15.9 hours.
//
// Ordered by MISSION RECENCY, not by per-item age. `Item` carries no
// timestamp -- per-item age would need a new folded field or a re-scan of
// every mission's log on a loop that already re-reads every transcript every
// four seconds. Saying "oldest first" while sorting by something else would
// be the worse option; this sorts by what the data actually supports and the
// heading says so.
// The work view: four lanes, grouped by WHO ACTS NEXT rather than by goal.
// A board with several live sessions has to answer "which session handles
// what", and a per-goal tree cannot answer that at a glance -- the tree is
// still one click away per card, for "which piece does this belong to".
//
// THE ACCENT RULE HOLDS: amber appears on the `waiting on you` lane and
// nowhere else. Rework is urgent for a WORKER, not for the human, so it is
// red -- painting it amber would put it in competition with the only signal
// that means "nothing moves until you click".
function laneRow(r, kind, withGoal){
  // Inside a goal group the heading already said it; printing it again on
  // every row is what made 120 rows read as a wall.
  const goal = withGoal
    ? `<span class=qgoal>${esc(r.goal_title || r.goal)}</span>` : '';
  let note = '';
  if (kind === 'ready' && r.rework){
    note = `<div class=qrework>sent back${r.rework_by? ' by '+esc(r.rework_by):''}
            — ${esc(r.rework)}</div>`;
  } else if (kind === 'progress'){
    note = `<div class=qmeta>${esc(r.held_by)} · ${r.held_min}m
            · lease expires in ${r.ttl_min}m</div>`;
  } else if (kind === 'review'){
    const who = r.claimed_by? ` <span class=qgoal>${esc(r.claimed_by)}</span>` : '';
    note = r.claim? `<div class=qmeta>${esc(r.claim)}${who}</div>` : '';
    // Two lines, never merged. The disk's verdict is evidence; a second
    // session's read is an opinion, and the word "opinion" is on screen
    // because the verifier is the same model reading the same disk.
    if (r.checked_by){
      note += `<div class=qopinion>a second session (${esc(r.checked_by)})
               agrees — opinion, not evidence: ${esc(r.checked_note)}</div>`;
    }
  }
  // ⚠️ These two were a LINE each, on every row, and the board became
  // unreadable the moment it was looked at: 120 ready rows across all goals,
  // 95 of them carrying "agreed N days ago...", and every row repeating the
  // same sentence about naming no file. A signal on 79% of the rows is not a
  // signal, and identical prose stacked 100 times is wallpaper. Both are
  // chips now, and the sentence each one used to print is its tooltip, so
  // nothing was lost -- only repeated.
  //
  // Which case gets the chip is decided by which is RARE. The negative
  // eligibility verdict is the common one here, so the exception worth
  // marking is "a fresh session could take this"; the advice the negative
  // carries belongs in `mission sharpen`, which exists for exactly that and
  // says it once per item instead of once per render.
  const chips = [];
  if (kind === 'ready'){
    if (r.rework) chips.push(`<span class="qchip rework">rework</span>`);
    if (r.untouched_days) chips.push(`<span class=qchip
      title="agreed ${r.untouched_days} days ago and never taken, claimed or
             handed back — on this board that has usually meant it is already
             built. mission audit briefs a session to check.">${
      r.untouched_days}d untouched</span>`);
    if (r.delegable) chips.push(`<span class=qchip
      title="${esc(r.delegable_fact || '')}">delegable</span>`);
    if (!chips.length) chips.push(`<span class=qchip>fresh</span>`);
  } else if (kind === 'progress'){
    chips.push('<span class=qchip>held</span>');
  }
  const chip = chips.join('');
  return `<li class=qrow><code class=qid>${esc(r.id)}</code>
    <div class=qbody><div class=qtext>${esc(r.text)} ${goal}</div>${note}</div>
    ${chip}</li>`;
}

function renderWork(all){
  const box = document.getElementById('work');
  const goals = all.filter(s => s.has_mission && !s.damaged);
  const lanes = {ready:[], progress:[], review:[], waiting:[]};
  for (const g of goals){
    const q = g.queue || {};
    for (const k of Object.keys(lanes)) (q[k] || []).forEach(r => lanes[k].push(r));
  }
  const n = k => lanes[k].length;
  const sessions = goals.reduce((t,g) => t + (g.procs||0), 0);

  // The governor, stated as a number rather than a feeling. Dispatching more
  // workers while the human's accept queue is deep makes the measured
  // bottleneck worse, so the board says so instead of offering the button.
  // Counted from the rows, like every other number here, so no summary can
  // disagree with the lane it summarises.
  const stale = lanes.ready.filter(r => r.untouched_days).length;
  const held = n('waiting') > DISPATCH_MAX;
  const bar = held
    ? `<div class=qheld>dispatch is held: ${n('waiting')} waiting on you,
       threshold ${DISPATCH_MAX}</div>` : '';

// Grouped by goal, because a flat cross-goal queue stops being readable at
// the size a real board reaches. Measured 2026-10-08: 120 ready rows, of
// which 61 were one goal's and 93 belonged to three — every one of them
// repeating its goal's name in grey beside the text. The group heading says
// it once.
//
// What folds is the FLAGGED ROWS, not the group. The first rule here folded
// a group only when every row in it was stale, which left the biggest goal
// (61 rows, 48 stale) fully expanded and changed nothing — and folding by
// size alone would have hidden live work behind a disclosure triangle, which
// is the thing this view exists to stop. Folding exactly the flagged set
// keeps every actionable row on screen, and what is hidden is described by
// the summary that hides it.
const FOLD_ABOVE = 3;

function laneGroups(k, rows){
  const order = [];                       // first appearance, so the upstream
  const by = new Map();                   // ordering survives grouping
  for (const r of rows){
    const g = r.goal || '';
    if (!by.has(g)){ by.set(g, []); order.push(g); }
    by.get(g).push(r);
  }
  if (order.length < 2) return `<ul class="qlist ${k}">${
    rows.map(r => laneRow(r,k,true)).join('')}</ul>`;
  return order.map(g => {
    const rs = by.get(g);
    const live = rs.filter(r => !r.untouched_days);
    const stale = rs.filter(r => r.untouched_days);
    const head = `${esc(rs[0].goal_title || g)} <b>${rs.length}</b>${
      stale.length? ` · <span class=qgstale>${stale.length} may be done already</span>`:''}`;
    const ul = xs => `<ul class="qlist ${k}">${
      xs.map(r => laneRow(r,k,false)).join('')}</ul>`;
    // Few enough to read at a glance: show them, a triangle would be worse.
    const folded = stale.length > FOLD_ABOVE
      ? `<details class=qfold><summary>${stale.length} agreed long ago and
           never taken — may already be built
           (<code>mission audit</code>)</summary>${ul(stale)}</details>`
      : (stale.length ? ul(stale) : '');
    return `<div class=qgroup><p class=qgh>${head}</p>${
      live.length ? ul(live) : ''}${folded}</div>`;
  }).join('');
}

  const lane = (k, title, sub) => !n(k) ? '' : `
    <h2 class=qh>${title} <b>${n(k)}</b></h2>
    ${sub? `<p class=qsub>${sub}</p>`:''}
    ${laneGroups(k, lanes[k])}`;

  box.innerHTML = `
    <div class=qstats>
      <span><b>${n('ready')}</b> ready to pick up</span>
      <span><b>${n('progress')}</b> in progress</span>
      <span><b>${n('review')}</b> in review</span>
      <span class=warn><b>${n('waiting')}</b> waiting on you</span>
      ${stale? `<span class=warn><b>${stale}</b> may be done already</span>`:''}
      <span class=qsess>${sessions} session(s) live</span>
    </div>
    ${bar}
    ${lane('ready','ready to pick up',
           'accepted work that is not done — a session takes these with '
           + '<code>mission take</code>, no acceptance needed. '
           + '<b>Nd untouched</b> = agreed that long ago and never taken, '
           + 'claimed or handed back, so it may already be built '
           + '(<code>mission audit</code>). <b>delegable</b> = names a file, '
           + 'so a session with no context could finish it and prove it. '
           + 'Hover a chip for the fact behind it.')}
    ${lane('progress','in progress')}
    ${lane('review','in review','a claim awaiting a check')}
    ${lane('waiting','waiting on you','nothing here moves without a click')}
    ${(n('ready')||n('progress')||n('review')||n('waiting'))? ''
      : '<p class=qsub>Nothing queued, nothing waiting.</p>'}`;
}

function renderInbox(all){
  const box = document.getElementById('inbox');
  // No archived filter here on purpose: mission_rows() already drops an
  // archived goal before it reaches the page. A second clause referencing a
  // field the payload does not carry would READ like a filter and do nothing
  // -- the silent no-op this codebase has shipped twice before.
  const goals = all.filter(s => s.has_mission)
                   .map(s => {
    const v = visible(s.tree || []);
    return {s, ask: v.filter(i => !i.ok || i.cd), rev: s.review || []};
  }).filter(g => g.ask.length || g.rev.length)
    .sort((a, b) => (b.s.mtime || 0) - (a.s.mtime || 0));

  const n = goals.reduce((t, g) => t + g.ask.length + g.rev.length, 0);
  if (!n){
    box.innerHTML = '<p class=none>Nothing is waiting on you.</p>';
    return;
  }
  const html = goals.map(g => {
    const s = g.s, can = WRITABLE && CODE();
    // The per-goal batch action survives into the Inbox on purpose: one
    // `sharpen --propose` pass can file thirty rows, and thirty keystrokes to
    // clear them is the same wallpaper failure in a new shape.
    const nprop = g.ask.filter(i => !i.ok).length;
    const bulk = (can && nprop > 1)
      ? `<button class="act ok" data-do=acceptall data-s="${s.full}">accept all ${nprop}</button>` : '';
    return `<div class=gl><h3>${esc(s.title || s.id)}</h3>`
      + `<span class=when>${ago(s.mtime)}</span>${bulk}</div><ul>`
      + g.rev.map(f => `<li><span class=box>⚑</span><span class=txt>${esc(f.what)}`
          + `<div class=verdict>${esc(f.detail || '')}</div></span>`
          + (can ? `<button class=act data-ack="${esc(f.what)}" data-s="${s.full}">mark read</button>` : '')
          + `</li>`).join('')
      + g.ask.map(i => row(i, true, s.full)).join('')
      + `</ul>`;
  }).join('');
  box.innerHTML = `<h2><b>${n}</b> waiting on you</h2>`
    + `<p class=keys>j/k move · a accept · c confirm · x decline · o open goal</p>`
    + `<p class=sub>across ${goals.length} goal${goals.length > 1 ? 's' : ''},`
    + ` most recently active first</p>` + html;
}

function ago(t){
  if (!t) return '';
  const m = (Date.now() / 1000 - t) / 60;
  if (m < 60) return `${Math.max(0, Math.round(m))}m ago`;
  if (m < 60 * 48) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
}

// Everything a card is searchable BY: the goal, every task, the folder, and
// the ids -- searching a board of goals for a task you half remember is the
// case that matters, and matching only the title fails it.
const haystack = s => [s.title, s.objective, s.cwd, s.id,
  ...(s.tree||[]).map(i=>i.t), ...(s.criteria||[]),
  ...(s.children||[]).map(k=>k.title)].join(' ').toLowerCase();

const passes = s =>
  (QUERY === '' || haystack(s).includes(QUERY)) &&
  (FILTER === 'all'
   // `ended` is now only true when every session REPORTED that it ended, so
   // an unknown session shows under "live" rather than being hidden. A row
   // the board cannot speak for belongs where you will see it.
   || (FILTER === 'live'  && !s.ended)
   || (FILTER === 'ended' && s.ended)
   // "needs you" is the only filter that is about YOUR attention rather than
   // the session's state: proposals waiting, or no mission written at all.
   || (FILTER === 'todo'  && (s.pending_accept > 0 || !s.has_mission
                              || (s.review||[]).length)));

async function tick(force){
  // The board restarts under this page routinely -- every upgrade, and every
  // switch from read-only to writable. The silent-return here left a page
  // that looked alive and was three restarts stale, with nothing but console
  // errors to say so. Say it on the page, keep polling, recover on our own.
  let payload; try{ payload=await (await fetch('/data')).json() }
  catch(e){
    let b = document.getElementById('reconn');
    if(!b){ b = document.createElement('div'); b.id='reconn';
      b.textContent = 'board unreachable — it may be restarting; retrying…';
      document.body.prepend(b); }
    return;
  }
  const gone = document.getElementById('reconn'); if(gone) gone.remove();
  const all = payload.rows; WRITABLE = payload.writable;
  if (payload.home){
    const w = document.getElementById('strip');
    w.className = ''; w.innerHTML =
      `<span>⚠ reading <b>${esc(payload.home)}</b>, not your usual store —` +
      ` this board was started with AGENT_MISSION_HOME set.</span>`;
    document.getElementById('g').innerHTML =
      all.map(s=>`<div class=card><p class=obj>${esc(s.title||s.id)}</p></div>`).join('');
    return;
  }
  askForCode();
  const d = all.filter(passes);
  document.getElementById('age').textContent =
    (d.length === all.length ? `${all.length} sessions` : `${d.length} of ${all.length}`)
    + ' · ' + new Date().toLocaleTimeString();
  // Exactly one empty state. "No live sessions" and "Nothing matches that"
  // are different facts and were rendering on top of each other.
  document.getElementById('empty').hidden = d.length > 0 || all.length === 0;

  // What is waiting on you, across every session -- so the answer to "is
  // anything waiting on me" costs one glance, not six cards.
  const asks   = all.filter(s=>s.state === 'waiting');
  const blank  = all.filter(s=>s.state === 'nomission');
  const strip  = document.getElementById('strip');
  const n = asks.reduce((t,s)=>t + s.pending_accept, 0);
  const bits = [];
  const rev = all.reduce((t,s)=>t + (s.review||[]).length, 0);
  if (rev) bits.push(`<span>⚑ <b>${rev}</b> need${rev>1?'':'s'} re-reading</span>`
    + `<button class=go data-jump=todo>show</button>`);
  if (n) bits.push(`<span><b>${n}</b> proposal${n>1?'s':''} awaiting you`
    + ` in ${asks.length} session${asks.length>1?'s':''}</span>`
    + `<button class=go data-jump=todo>show</button>`);
  if (blank.length) bits.push(`<span><b>${blank.length}</b> session${
    blank.length>1?'s':''} with no mission</span>`
    + `<button class=go data-jump=todo>show</button>`);
  if (!WRITABLE) bits.push('<span class=why>This board is read-only. Run'
    + ' <code>mission board</code> yourself in a terminal for a write code'
    + ' and buttons.</span>');
  strip.className = bits.length? '' : 'clear';
  strip.innerHTML = bits.length? bits.join('')
    : '<span>Nothing is waiting on you.</span>';
  renderSetup();
  // The Inbox and the goal grid are two renderings of the same payload, so
  // the poll, the reconnect banner and the write path are shared and only the
  // surface differs.
  document.getElementById('work').hidden = VIEW !== 'work';
  document.getElementById('inbox').hidden = VIEW !== 'inbox';
  document.getElementById('g').hidden = VIEW !== 'goals';
  document.getElementById('fchips').hidden = VIEW !== 'goals';
  if (VIEW === 'work'){
    document.getElementById('empty').hidden = true;
    renderWork(all);
    return;
  }
  if (VIEW === 'inbox'){
    document.getElementById('empty').hidden = true;
    renderInbox(all);
    return;
  }
  // Re-rendering destroys the scroll position of every scrollable block
  // inside a card, so a person reading a long plan was yanked back to the
  // top every 4 seconds -- mid-scroll. Two defences: skip the render
  // entirely when the data has not changed (the overwhelmingly common
  // poll), and when it has, put every scrolled block back where it was.
  const g = document.getElementById('g');
  const html = d.length? d.map(s=>`
   <div class="card ${s.ended?'ended':''} ${s.state}">
     <div class=rowline><span class="dot ${s.state}" title="${s.state}"></span>
       <span class=rid>${esc(s.id)}</span>
       <span class=rt>${esc(s.title||'no mission yet')}</span>
       <span class=rp>${s.total?s.done+'/'+s.total:'—'}</span>
       <span class=rp>${s.pending_accept?s.pending_accept+' waiting':''}</span></div>
     <div class=sid><span class=st><span class="dot ${s.state}" title="${s.state}"></span>
       ${s.id} · ${esc(s.cwd)}</span>
       <span>${s.ended?'ended':s.procs+' live here'}${
         (WRITABLE&&CODE()&&s.mission)?
           ` <button class=act data-arch="${s.full}">archive</button>`:''}</span></div>
     ${s.has_mission? `
       <p class=obj>${esc(s.title)}</p>
       ${s.named && s.objective?`<p class=objsub>${esc(s.objective)}</p>`:''}
       ${(s.review||[]).map(f=>`<div class=rev>
          <b>⚑ ${esc(f.what)}</b>
          <div class=d>${esc(f.detail)}</div>
          <div class=acts>${(WRITABLE&&CODE())?
            `<button data-ack="${esc(f.what)}" data-s="${s.full}">mark read</button>`:''}
            <span class=d>reading it is all this asks — the record does not change</span>
          </div></div>`).join('')}<!-- only when the mission has a real NAME: otherwise the title is
     the objective trimmed, and printing both says it twice -->
       ${s.total? `<div class=bar><i style="width:${100*s.done/s.total}%"></i></div>
         <div class=sid><span>${s.done} of ${s.total} done</span>
         ${(()=>{const cds=(s.tree||[]).filter(r=>r.cd&&!r.hid);
            const nb=cds.filter(r=>r.cd.ok).length, nu=cds.length-nb;
            if(!(WRITABLE&&CODE()&&cds.length)) return '';
            // Two buttons, never one. Evidence-backed rows sweep on a single
            // click because the disk already did the reading. The rest are a
            // SEPARATE, two-step action that names what it is doing: the tool
            // must never let "the disk agrees" and "I decided to trust it"
            // arrive by the same gesture.
            return `<span class=sweep>`
              + (nb?`<button class="act ok" data-do=sweep data-s="${s.full}">confirm all backed (${nb})</button>`:'')
              + (nu?`<button class="act warn" data-do=sweepall data-s="${s.full}"
                       data-n="${nu}">confirm all ${cds.length} — ${nu} unverified</button>`:'')
              + `</span>`;})()}
         ${s.pending_accept?`<span class="warn ask">${s.pending_accept} awaiting accept${
   (WRITABLE&&CODE())?` <button class="act ok" data-do=acceptall data-s="${s.full}">accept all</button>`:''
 }</span>`:'<span></span>'}</div>
         ${(s.pending_accept && !(WRITABLE&&CODE()))?`
           <div class=why>To accept them, in your own terminal:</div>
           <div class=howto>mission accept --pending --on ${esc(s.id)}</div>`:''}
         ${(()=>{const v=visible(s.tree),
                  ask=v.filter(i=>!i.ok||i.cd), agreed=v.filter(i=>i.ok&&!i.cd);
            // Waiting-on-you sits ABOVE the agreed work. Mixed into the plan a
            // proposal reads as a task you have already signed up for, and the
            // one thing on a card that asks something of you should not have to
            // be hunted for among the things that do not.
            //
            // A claims-done row belongs here too, and did not for a week: it
            // is an ACCEPTED item, so it failed the `!i.ok` test and sat in
            // the tree where the plan is read, not where decisions are made.
            // Both kinds are "this needs your eyes", and they leave the block
            // by being decided -- accepted rows drop into the plan in their
            // real position, confirmed ones into the finished fold.
            const prop=ask.filter(i=>!i.ok).length, conf=ask.length-prop;
            const head=[prop?`${prop} to accept`:'', conf?`${conf} to confirm`:'']
                        .filter(Boolean).join(' · ');
            return (ask.length? `<div class=asks><h3>${head}</h3>
                     <ul class="chk flat">${ask.map(i=>row(i,true,s.full)).join('')}</ul></div>`:'')
                 + `<ul class=chk>${agreed.map(i=>row(i,false,s.full)).join('')}</ul>`;})()}<!-- map(row) passes the INDEX as row's second argument, so every row
     after the first rendered in flat mode and the tree lost every
     connector. The nesting was in the data the whole time. -->
         ${s.tree.some(i=>i.hid)?`<details class=doneblock data-sid="${s.id}" ${
             openFolds.has(s.id)?'open':''}><summary>${
             s.tree.filter(i=>i.hid).length} finished</summary>
           <ul class="chk flat">${s.tree.filter(i=>i.hid).map(r=>row(r,true,s.full)).join('')}</ul>
         </details>`:''}`:''}
       ${(s.criteria.length+s.constraints.length+s.non_goals.length)?`
         <details class=doneblock data-sid="terms-${s.id}" ${openFolds.has('terms-'+s.id)?'open':''}>
           <summary>the deal — ${s.criteria.length} done-when · ${
             s.constraints.length+s.non_goals.length} limits</summary>
           ${s.criteria.length?`<h3>Done when</h3><ul>${s.criteria.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
           ${s.constraints.length?`<h3>Constraints</h3><ul>${s.constraints.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
           ${s.non_goals.length?`<h3>Not doing</h3><ul>${s.non_goals.map(c=>`<li class=ng>${esc(c)}</li>`).join('')}</ul>`:''}
         </details>`:''}
     ` : `<p class=none>No mission yet.<br>Run <code>mission init</code> in this
          session to write one — it takes a minute and the agent cannot change it.</p>
          ${s.asks.length?`<h3>Currently asked</h3><p class=none>${esc(s.asks[s.asks.length-1])}</p>`:''}`}
     <div class=flags>
       ${s.models.length?`<span class="flag ${s.model_changed?'':'calm'}">${
          s.models.map(m=>m.replace('claude-','')).join(' → ')}</span>`:''}
       ${s.exact_repeats?`<span class=flag>${s.exact_repeats} identical reply${s.exact_repeats>1?'s':''}</span>`:''}
       ${!s.exact_repeats&&s.repeats?`<span class="flag calm">${s.repeats} near-repeat${s.repeats>1?'s':''}</span>`:''}
       ${s.collisions.length?`<span class=flag>shared file${s.collisions.length>1?'s':''}</span>`:''}
     </div>
     ${s.collisions.length?`<div class=flagdet>also being written by another session:<br>${
        s.collisions.map(f=>esc(f)).join(' · ')}</div>`:''}
     ${s.worst_repeat&&s.worst_repeat.sim>=0.999?`<div class=flagdet>a reply was repeated
        verbatim ${s.worst_repeat.gap} replies later</div>`:''}
     ${(s.sessions||[]).length?`<div class=kids><h3>Sessions on this goal</h3>${
        s.sessions.map(x=>`<div class=kid>
          <span class="dot ${x.liveness==='live'?'working':
                             x.liveness==='unknown'?'unknown':'ended'}"></span>
          <span class=kn>${esc(x.id)}</span>
          <span class=kt>${x.liveness==='live'?'live':
            x.liveness==='unknown'?'liveness unknown — no heartbeat':'ended'}</span>
          <span class=kp>${x.calls.toLocaleString()} calls</span></div>`).join('')}</div>`:''}
     ${(s.children||[]).length?`<div class=kids><h3>Delegated</h3>${
        s.children.map(k=>`<div class=kid><span class=kn>${esc(k.id)}</span>
          <span class=kt>${esc(k.title)}</span>
          <span class=kp>${k.total?k.done+'/'+k.total:'—'}</span></div>`).join('')}</div>`:''}
     ${(WRITABLE && CODE() && s.has_mission)?`<div class=notebox>
        <input placeholder="note to self — recorded, never judged" data-note="${s.full}">
        <button data-do=note data-s="${s.full}">save</button></div>`:''}
     <div class=meta>${s.calls.toLocaleString()} calls · ${s.files} files ·
       ${s.tests} test runs · <span class=warn>${s.failures} failed</span>
       ${(s.claims_bad&&s.claims_bad.length)?`<div class=claims>${
           s.claims_bad.length} claim${s.claims_bad.length>1?'s':''} nothing backs${
           s.claims_bad.map(c=>`<br>· “${esc(c.sentence.slice(0,90))}” — ${esc(c.detail.slice(0,80))}`).join('')
         }</div>`
        :(s.claims_checked?`<div class=claims>${s.claims_checked} claims checked against disk — all backed</div>`:'')}
       ${s.topfiles.length?`<br>${s.topfiles.slice(0,3).map(f=>esc(f.f)+' '+f.n+'x').join(' · ')}`:''}</div>
   </div>`).join('') : (all.length? '' : '<p class=none>No live sessions.</p>');
  if (html === LAST_HTML && !force) return;
  // The skip above almost never fires on a LIVE session -- the call/file
  // counters move nearly every poll, so the block was still rebuilt under an
  // active scroll. Restoring scrollTop is not enough: the gesture is attached
  // to the destroyed element, so momentum dies and the block can snap to top
  // before the restore paints. So: never rebuild while the person is
  // scrolling the grid. The data is 4s stale at worst; a lost scroll is a
  // lost reader.
  //
  // `force` is the other half, and it is not optional: a click YOU made must
  // paint now. Without it the deferral swallowed the person's own tick --
  // press confirm, watch the row sit there for seconds -- because the write's
  // own refresh landed inside the quiet window it had just started. Deferring
  // someone's scroll is politeness; deferring their click is a broken button.
  if (!force && Date.now() - LAST_TOUCH < 2500) return;
  LAST_HTML = html;
  const scrolled = [...g.querySelectorAll('.chk')].map((el,n)=>[n, el.scrollTop])
                     .filter(([,t])=>t>0);
  g.innerHTML = html;
  const chks = g.querySelectorAll('.chk');
  for (const [n, t] of scrolled) if (chks[n]) chks[n].scrollTop = t;
}
let LAST_HTML = '';
let LAST_TOUCH = 0;
// SCROLL events only. `pointerdown`/`touchstart` were in this list and they
// are click PRECURSORS, not scrolling: every button press armed the quiet
// window a microsecond before the write it triggered, so the board appeared
// to lag on exactly the interaction the person was watching.
// `scroll` does not bubble, hence capture; passive so scrolling never janks.
for (const ev of ['scroll','wheel','touchmove'])
  document.getElementById('g').addEventListener(
    ev, ()=>{ LAST_TOUCH = Date.now(); }, {capture:true, passive:true});
// The page re-renders every 4s, so an opened fold would snap shut under the
// reader. Remember which cards are open. `toggle` does not bubble, hence the
// capture listener -- and it is bound once, to a container render() never
// replaces, so it survives every re-render.
document.getElementById('g').addEventListener('toggle', e=>{
  const sid = e.target.dataset && e.target.dataset.sid;
  if (sid) e.target.open? openFolds.add(sid) : openFolds.delete(sid);
}, true);
// The strip's buttons are rewritten on every tick, so the listener lives on
// the container, which is not.
document.getElementById('setup').addEventListener('click', async e=>{
  const b = e.target.closest('[data-setup]'); if(!b) return;
  const name = b.dataset.setup;
  const diff = b.closest('.srow').nextElementSibling.textContent.trim();
  // The diff is on screen already; confirming means you read it.
  if (!confirm(`Install "${name}"?\n\n${diff}\n\nsettings.json is backed up first.`)) return;
  b.disabled = true; b.textContent = '…';
  const r = await fetch('/', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({code: CODE(), action:'setup', session:'', ids:[], text:name})});
  const out = await r.json().catch(()=>({}));
  if (!r.ok) alert(out.error || 'failed');
  else if (out.backup) console.log('backup:', out.backup);
  renderSetup();
});
// ONE handler, bound to both surfaces. The Inbox renders the same rows with
// the same buttons, so a second copy would be a second thing to keep correct
// -- the failure that put four divergent stage-label maps in the docs site.
// ── Inbox keyboard ───────────────────────────────────────────────────────
//
// j/k move, a accepts, c confirms, o opens the goal, x declines.
//
// ⚠️ GATED ON FOCUS, and that is the whole difficulty. The page already binds
// Enter on the write-code input, and the board has a search box and a note
// field: a bare `a` fired while someone is typing a filter would accept a
// proposal they never looked at. The vault's review app learned this the same
// way -- bare arrows had to keep moving the caret inside a textarea -- so the
// rule here is the same: if focus is in anything you can type into, the key
// is not ours.
let KROW = -1;

function kRows(){
  return [...document.querySelectorAll('#inbox li[data-i]')];
}

function kFocus(n){
  const rows = kRows();
  if (!rows.length){ KROW = -1; return; }
  KROW = Math.max(0, Math.min(n, rows.length - 1));
  rows.forEach((r,i) => r.classList.toggle('kcur', i === KROW));
  rows[KROW].scrollIntoView({block:'nearest'});
}

function kTyping(el){
  if (!el) return false;
  const tag = (el.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select'
      || el.isContentEditable;
}

document.addEventListener('keydown', e => {
  if (VIEW !== 'inbox') return;
  if (kTyping(e.target)) return;            // they are writing, not steering
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const rows = kRows();
  if (!rows.length) return;
  const k = e.key;
  if (k === 'j' || k === 'k'){
    kFocus(KROW < 0 ? 0 : KROW + (k === 'j' ? 1 : -1));
    e.preventDefault();
    return;
  }
  if (KROW < 0) return;                     // nothing selected: nothing to act on
  const row = rows[KROW];
  // The buttons the row already renders ARE the authority. Pressing a key
  // clicks one of them rather than calling act() directly, so a key can
  // never reach a write the row itself would not offer -- a read-only board
  // renders no buttons, and the keys then do nothing.
  const hit = sel => { const b = row.querySelector(sel); if (b) b.click(); };
  if (k === 'a'){ hit('[data-do=accept]'); e.preventDefault(); }
  else if (k === 'c'){ hit('[data-do=done]'); e.preventDefault(); }
  else if (k === 'x'){ hit('[data-do=decline]'); e.preventDefault(); }
  else if (k === 'o'){ hit('a.goal, [data-open]'); e.preventDefault(); }
});

const onBoardClick = e=>{
  const ar = e.target.closest('[data-arch]');
  if (ar){
    if (confirm('Archive this goal? It leaves the board. Nothing is deleted — '
              + 'the log stays, and `mission archive --undo` brings it back.'))
      act('archive', ar.dataset.arch, []);
    return;
  }
  const a = e.target.closest('[data-ack]');
  if (a){
    act('ack', a.dataset.s, [], a.dataset.ack);
    return;
  }
  const b = e.target.closest('[data-do]'); if(!b) return;
  const sid = b.dataset.s;
  if (b.dataset.do === 'decline'){
    // `remove` drops the item AND its subtree -- that is in the store, not
    // something this button chose -- so the confirm says so. Declining was
    // CLI-only until now; the authority is unchanged (remove is already
    // human-only), this is the button that was missing.
    if (confirm('Decline this proposal? It leaves the plan, with anything '
              + 'nested under it. Nothing is deleted — the event log keeps '
              + 'it, and `mission why` still shows it was proposed.'))
      act('remove', sid, [b.dataset.i]);
    return;
  }
  if (b.dataset.do === 'note'){
    const inp = document.querySelector(`[data-note="${sid}"]`);
    if (inp && inp.value.trim()){ act('note', sid, [], inp.value); inp.value=''; }
    return;
  }
  if (b.dataset.do === 'sweep'){
    act('sweep', sid, []);
    return;
  }
  if (b.dataset.do === 'sweepall'){
    // The only destructive-ish button on the board that cannot be undone by
    // another click, so it asks -- and the question names the number it
    // cannot vouch for rather than saying "are you sure".
    const n = b.dataset.n;
    if (confirm(`Confirm every suggested item on this card?\n\n`
              + `${n} of them have no disk evidence — nothing on disk backs `
              + `what the agent said. Confirming them records YOUR judgement, `
              + `and the log will show they were taken on trust.`))
      act('sweepall', sid, []);
    return;
  }
  if (b.dataset.do === 'acceptall'){
    // In the Inbox the rows sit under a goal heading, not inside a .card, so
    // the scope is "this button's own group" rather than a fixed selector.
    const grp = b.closest('.card') || b.closest('.gl').nextElementSibling;
    const ids = [...grp.querySelectorAll('[data-do=accept]')].map(x=>x.dataset.i);
    if (ids.length) act('accept', sid, ids);
    else note_err('accept all found nothing to accept on this card — likely a bug worth reporting');
    return;
  }
  act(b.dataset.do, sid, [b.dataset.i]);
};
document.getElementById('g').addEventListener('click', onBoardClick);
document.getElementById('inbox').addEventListener('click', onBoardClick);
document.getElementById('strip').addEventListener('click', e=>{
  const b = e.target.closest('[data-jump]'); if(!b) return;
  // Filtering alone looked like nothing happened: when every card is already
  // waiting on you, "show" filtered three cards down to the same three. Take
  // the reader to the first one and mark it instead.
  document.querySelector('.chip[data-f=todo]').click();
  setTimeout(()=>{
    const first = document.querySelector('#g .card');
    if (!first) return;
    first.scrollIntoView({behavior:'smooth', block:'center'});
    first.classList.add('flash');
    setTimeout(()=>first.classList.remove('flash'), 1400);
  }, 120);
});
document.getElementById('dense').addEventListener('click', ()=>{
  const on = document.getElementById('g').classList.toggle('compact');
  document.getElementById('dense').setAttribute('aria-pressed', String(on));
});
document.getElementById('q').addEventListener('input', e=>{
  QUERY = e.target.value.trim().toLowerCase(); tick();
});
// [data-f] and not .chip: the density button borrows .chip for its styling,
// and binding on the class alone set FILTER to undefined and emptied the
// board -- while also clearing every filter's pressed state.
document.querySelectorAll('.chip[data-v]').forEach(b=>b.addEventListener('click', ()=>{
  VIEW = b.dataset.v;
  document.querySelectorAll('.chip[data-v]').forEach(
    o=>o.setAttribute('aria-pressed', String(o===b)));
  LAST_HTML = '';          // the grid was hidden; force it to rebuild on return
  tick(true);
}));
document.querySelectorAll('.chip[data-f]').forEach(b=>b.addEventListener('click', ()=>{
  FILTER = b.dataset.f;
  document.querySelectorAll('.chip[data-f]').forEach(
    o=>o.setAttribute('aria-pressed', String(o===b)));
  tick();
}));
tick(); setInterval(tick,4000);
</script>"""


class _Cache:
    """Serve the last snapshot immediately; refresh it off the request path.

    Reading every live transcript takes ~4s on a real board -- 130 MB of JSONL,
    and the files being written are exactly the ones whose cache key changes
    every poll, so memoising by mtime bought nothing. The browser polls every
    4s, so requests overlapped permanently and the page sat blank waiting.

    Nothing here needs to be to-the-second: it is a board you glance at. So a
    reader never blocks, and the data is at most one refresh old.
    """

    def __init__(self, every: float = 6.0):
        self.every = every
        self.rows: list[dict] = []
        self.at = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def get(self) -> list[dict]:
        if not self.rows and not self.at:
            self.rows, self.at = snapshot(), time.time()   # first call only
        if time.time() - self.at > self.every:
            self._refresh_async()
        return self.rows

    def _refresh_async(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.at = time.time()        # claim the slot before starting
            self._thread = threading.Thread(target=self._refresh, daemon=True)
            self._thread.start()

    def invalidate(self, mid: str = "") -> None:
        """After a write, the next read must not serve the pre-write board.

        This used to run a full `snapshot()` INSIDE the POST handler, so every
        Accept and every tick blocked on a whole-corpus transcript rescan --
        the same ~4s this cache exists to keep off the request path, moved onto
        the one request a person is actually watching. The button felt broken.

        A write changes the mission's EVENT LOG and nothing else: the tree, the
        counts, the title. It cannot change how many tool calls a transcript
        contains. So re-fold just that mission (a small file) and keep the
        activity numbers already in the row -- they are at most one refresh
        old, which is this cache's standing contract, not a new compromise.
        A full refresh still runs, in the background, off the click.
        """
        patched = self._patch(mid) if mid else False
        if not patched:
            # New mission, archived away, or an unknown id: fall back to the
            # honest full rebuild rather than serving something wrong.
            self.rows, self.at = snapshot(), time.time()
            return
        self.at = 0.0 if not self.rows else self.at - self.every  # force refresh
        self._refresh_async()

    def _patch(self, mid: str) -> bool:
        """Re-fold one mission into its existing row. True if it was applied."""
        try:
            from . import missions as M
            for i, row in enumerate(self.rows):
                if row.get("id") != mid:
                    continue
                st = dict(M.all_missions()).get(mid)
                if st is None:
                    return False
                m = _safe_load(st.root)
                if m is None or m.archived:
                    return False            # the card is leaving; rebuild fully
                self.rows[i] = {**row, **{
                    "title": m.title, "objective": m.objective,
                    "named": bool(m.name), "criteria": m.success_criteria,
                    "constraints": m.constraints, "non_goals": m.non_goals,
                    "tree": _tree(m), "done": m.done_count,
                    "total": m.total_count, "pending_accept": len(m.unaccepted),
                    "cwd": (m.cwd or "").replace(str(Path.home()), "~"),
                    "mtime": st.log.stat().st_mtime,
                }}
                return True
        except Exception:
            return False                    # never fail a write over a redraw
        return False

    def _refresh(self) -> None:
        try:
            rows = snapshot()
        except Exception:
            return                        # keep the last good board
        self.rows, self.at = rows, time.time()


CACHE = _Cache()


WRITES = Session(enabled=False)      # replaced by serve() when a person runs it


class _H(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── who is allowed to talk to this board at all ──────────────────────
    #
    # The board bound 127.0.0.1 and treated that as the whole answer. It is
    # not: a browser on ANY web page can reach a loopback server, and a DNS
    # name that resolves to 127.0.0.1 makes the page same-origin with it.
    # Demonstrated against a live board:
    #   * `GET /data` with `Host: attacker.example` returned the full row
    #     JSON -- objectives, cwd paths, claim text. That is everything a
    #     DNS-rebinding page needs to READ the board.
    #   * a POST with `Origin: https://evil.example` and
    #     `Content-Type: text/plain` -- which a browser sends cross-site with
    #     NO preflight -- was processed and counted as a wrong code. Five of
    #     those from any open tab LOCKED THE BOARD until restart.
    #
    # So: the Host header must name this board, and a cross-origin POST is
    # refused BEFORE the code is looked at, so it cannot consume an attempt.
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}",
                        f"[::1]:{port}")

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True                 # not a browser request
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}",
                          f"http://localhost:{port}",
                          f"http://[::1]:{port}")

    def _reject_foreign(self) -> bool:
        """True when the request was refused and nothing more should happen."""
        if not self._host_ok():
            # 421 Misdirected Request: the honest code. The request reached
            # the right socket for the wrong name.
            self._send(421, json.dumps(
                {"error": "this board answers only to 127.0.0.1"}).encode(),
                "application/json")
            return True
        return False

    def do_GET(self):                                    # noqa: N802
        if self._reject_foreign():
            return
        if self.path.startswith("/api/identity"):
            # Proof that the thing on this port is OUR board, serving THIS
            # store. A bare TCP connect could not tell a live board from a
            # foreign service, so a lost record spawned a second board and a
            # stranger on 8976 was reported as the board's URL.
            import os
            from . import __version__
            return self._send(200, json.dumps({
                "mission_board": True, "pid": os.getpid(),
                "home": str(_missions_home()), "version": __version__,
                # Whether this board can take writes at all -- NOT the code.
                # `mission board` at a tty reads this to decide between "use
                # the one that is up" and "replace it with a writable one".
                "writes": bool(WRITES and WRITES.enabled),
            }).encode(), "application/json")
        if self.path.startswith("/api/setup"):
            # C10c: a read-only board serves NO setup route at all. Hiding the
            # buttons while leaving the endpoint live is the classic version of
            # this bug -- the background board that `mission init` spawns writes
            # its output to a log the agent can read, so it must not be able to
            # reach settings.json by any path, not merely be discouraged from
            # showing the option.
            if not WRITES.enabled:
                return self._send(404, b'{"error":"read-only board"}',
                                  "application/json")
            from . import setup_surfaces as S
            return self._send(200, json.dumps(
                {"surfaces": [dict(r, plan=S.plan(r["name"])["changes"])
                              for r in S.status()]}).encode(),
                "application/json")
        if self.path.startswith("/data"):
            # `writable` says a code will be ACCEPTED, never what it is.
            home = str(_missions_home())
            payload = {"rows": CACHE.get(), "writable": WRITES.enabled,
                       # Only sent when it is NOT the usual store: an empty
                       # board is otherwise indistinguishable from a board
                       # pointed somewhere else.
                       "home": None if home == str(Path.home() / ".agent-mission")
                       else home}
            return self._send(200, json.dumps(payload).encode(),
                              "application/json")
        self._send(200, PAGE.encode(), "text/html; charset=utf-8")

    # A client that sends a big Content-Length and then stalls used to block
    # rfile.read() forever on a single-threaded server -- denying the board to
    # everyone, including the person, with no symptom but a page that stopped
    # updating. Any client dying mid-request did it by accident.
    timeout = 10                       # BaseHTTPRequestHandler honours this
    MAX_BODY = 256 * 1024

    def do_POST(self):                                   # noqa: N802
        try:
            if self._reject_foreign():
                return
            # Refused WITHOUT touching the attempt counter -- a cross-site
            # POST must not be able to spend the person's five tries. The
            # content-type requirement is the other half: `text/plain` is one
            # of the three a form can send with no preflight, so demanding
            # JSON means a cross-site POST has to ask permission first.
            ctype = (self.headers.get("Content-Type") or "").split(";")[0]
            if not self._origin_ok() or ctype.strip() != "application/json":
                return self._send(403, json.dumps(
                    {"error": "this board takes same-origin JSON only"}
                ).encode(), "application/json")
            if not WRITES.enabled:
                # Same rule as the GET: no route, not a hidden button.
                return self._send(403, json.dumps(
                    {"error": "this board is read-only"}).encode(),
                    "application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n > self.MAX_BODY:
                return self._send(413, json.dumps(
                    {"error": "body too large"}).encode(), "application/json")
            req = json.loads(self.rfile.read(n) or b"{}")
            out = apply_action(WRITES, req.get("code", ""), req.get("action", ""),
                               req.get("session", ""), req.get("ids", []),
                               req.get("text", ""))
            # Patch just the mission that changed; the full refresh happens
            # off the request path so the click is not paying for a rescan.
            CACHE.invalidate(req.get("session", ""))
            self._send(200, json.dumps(out).encode(), "application/json")
        except Unauthorised as e:
            self._send(403, json.dumps({"error": str(e)}).encode(),
                       "application/json")
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}).encode(),
                       "application/json")

    def log_message(self, *a):                           # noqa: A003
        pass


def serve(port: int = 8976, writable: bool | None = None) -> None:
    # A person running `mission board` has a terminal; the background board that
    # `mission init` spawns does not, and its stdout goes to a log file the
    # agent could read. So the tty IS the test, and the code only ever reaches
    # a real terminal.
    # A board serving a TEST store must not sit on the port every real session
    # looks at. One left running with AGENT_MISSION_HOME=/tmp/... made every
    # card on the real board read "No mission yet" -- the data was fine, the
    # board was reading an empty directory, and nothing on the page said so.
    default_home = Path.home() / ".agent-mission"
    if _missions_home() != default_home and port == 8976:
        port = 8996
        print(f"\n  serving {_missions_home()} (not the default store),"
              f"\n  so taking port {port} instead of 8976.\n")

    # ONE board per store, always. Two boards for the same missions is two
    # answers to "what is the state", and the one you are looking at is
    # whichever won the port -- exactly how a test store came to be serving
    # 8976 while five real sessions read "No mission yet".
    from .daemon import identify
    for probe in range(8976, 8976 + 12):
        if probe == port:
            continue
        found = identify(probe)
        if found:
            print(f"\n  a board for this store is already running:"
                  f"\n    http://127.0.0.1:{probe}   (pid {found.get('pid')})"
                  f"\n\n  that one is the single source. Use it, or stop it first"
                  f"\n  with `mission board --stop`.\n")
            return

    global WRITES
    if writable is None:
        try:
            writable = sys.stdout.isatty()
        except Exception:
            writable = False
        # A tty is not proof of a person: `script -F -q board.log ... board`
        # gave an agent a WRITABLE board whose code it then read out of its
        # own log file. The override stays decisive so a person in a pipeline
        # can still get one. See whoami.py for why this is a speed bump.
        if writable:
            import os as _os
            from . import whoami as _w
            if _os.environ.get("AGENT_MISSION_I_AM_HUMAN") != "1" \
                    and _w.spawned_by_agent():
                print("\n  this board is running under a coding agent "
                      "(see `mission why`), so it is READ-ONLY."
                      "\n  start it from your own terminal to get the "
                      "write buttons.\n")
                writable = False
    # The passcode record is read ONCE, here, and held for the life of this
    # board -- see passcode.load() for why re-reading per request would be a
    # way to change the lock on a door that is already open.
    from . import passcode as _pc
    _rec = _pc.load() if writable else None
    # A record on disk is not evidence a PERSON set it -- the agent runs as
    # you and can write one. So a writable board asks for it once, here, in
    # the terminal that already proved somebody is sitting at it. Failing
    # that, the board still comes up: read-only, saying why.
    if _rec is not None and not _pc.confirm_at_terminal(_rec):
        _rec = None
        writable = False
    WRITES = Session(enabled=bool(writable),
                     verify=_pc.verifier(_rec) if _rec else None)
    class _Quiet(ThreadingHTTPServer):
        # The writable board runs in the PERSON'S terminal now, and every
        # browser reload that drops a connection mid-response printed a
        # 30-line BrokenPipeError traceback into it. A closed pipe is the
        # client's business; a real error still prints.
        def handle_error(self, request, client_address):
            import sys as _s
            exc = _s.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                return
            super().handle_error(request, client_address)

    srv = _Quiet(("127.0.0.1", port), _H)
    srv.daemon_threads = True
    # The server writes its own record, because it is the only thing that knows
    # it is up and which pid it is. Written by the LAUNCHER, a board started any
    # other way -- by hand, or after a restart -- leaves the previous pid in the
    # file, and `mission board --stop` then signals a pid that has since been
    # recycled to something else entirely.
    from .daemon import claim, release
    claim(port)
    print(f"\n  mission board -> http://127.0.0.1:{port}")
    if WRITES.enabled and WRITES.saved:
        from . import passcode as _pc2
        _when = _pc2.set_on()
        print("\n  writable — confirmed with YOUR passcode"
              + (f" (set {_when})" if _when else "")
              + ".\n  It survives restarts, so the browser may already have it."
              "\n  Forgotten it? `mission passcode` again to set a new one."
              "\n  Did not set it on that date? An agent may have:"
              " `mission passcode --clear`.")
    elif WRITES.enabled:
        print(f"\n  write code: {WRITES.code}"
              f"\n  type it into the board once to accept and tick from there."
              f"\n  it is not on disk and no page returns it — only this terminal"
              f"\n  has it, which is why the agent cannot use these buttons."
              f"\n\n  tired of re-typing a new one after every restart?"
              f"  `mission passcode`")
    else:
        print("\n  read-only (started in the background). Run `mission board`"
              "\n  yourself in a terminal to get a write code.")
    print("\n  ctrl-c to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  stopped")
    finally:
        release(port)
