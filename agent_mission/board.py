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
from .store import MissionStore, root_for


def _slug(cwd: str) -> str:
    return "-" + cwd.replace("/", "-").lstrip("-")


def _tree(m) -> list[dict]:
    """The plan as nested rows the page can render without re-deriving it.

    Rows carry `hid` for finished work — itself done, or under something done.
    The page folds those away, because a card is only useful at a glance if
    what is left fits on it; the finished rows stay one click away rather than
    disappearing, since "what did we already do" is a real question.
    """
    def walk(nodes, depth=0, guides=(), hidden=False):
        out = []
        for idx, n in enumerate(nodes):
            last = idx == len(nodes) - 1
            done = n.complete if n.children else n.item.done
            out.append({
                "id": n.item.id, "t": n.item.text, "d": depth,
                "done": done,
                "ok": n.item.accepted,
                "branch": bool(n.children),
                "roll": f"{n.done_count}/{n.total}" if n.children else "",
                "pct": round(100 * n.done_count / n.total) if n.children and n.total else 0,
                "guides": list(guides), "last": last,
                "hid": hidden or done,
            })
            out.extend(walk(n.children, depth + 1, (*guides, not last),
                            hidden or done))
        return out
    return walk(m.tree())


def _safe_load(path):
    """Load a mission, or None if that session's log is unreadable.

    snapshot() reads every session on the board, and the first request is not
    inside the background refresh's try/except. So an exception here took the
    whole page down for everyone rather than losing one card.
    """
    try:
        return MissionStore(path).load()
    except Exception:
        return None


def _missions_home() -> Path:
    import os
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


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
    out = []
    live_ids = set()
    for proc in live():
        d = PROJECTS / _slug(proc["cwd"])
        if d.exists():
            for tp in sorted((p for p in d.glob("*.jsonl")
                              if p.stat().st_size > 2000),
                             key=lambda p: -p.stat().st_mtime)[:proc["procs"]]:
                live_ids.add(tp.stem)

    for mid, st in M.all_missions():
        m = _safe_load(st.root)
        if m is None or m.archived:
            continue
        calls = files = tests = fails = 0
        sess = []
        for sid in M.sessions_of(mid):
            tp = transcript_for(sid)
            a = activity(tp) if tp else None
            if a:
                calls += a.calls; files += len(a.files)
                tests += a.tests; fails += a.failures
            sess.append({"id": short_id(sid), "full": sid,
                         "live": sid in live_ids,
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
            "ended": not any(x["live"] for x in sess),
            "has_mission": True,
            "calls": calls, "files": files, "tests": tests, "failures": fails,
            "asks": [], "topfiles": [], "models": [], "model_changed": False,
            "repeats": 0, "exact_repeats": 0, "worst_repeat": None,
            "collisions": [], "children": [],
            "mtime": st.log.stat().st_mtime,
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
:root{--bg:#F6F7F6;--card:#fff;--ink:#16191B;--mut:#626B6E;--rule:#DCE1DF;--soft:#EDF0EE;
--ok:#0E6E68;--bad:#8E3B2F;--okw:#E4EFEE;--badw:#F5E7E4;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
--mono:ui-monospace,"SF Mono",Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#0F1313;--card:#151A19;--ink:#E7ECE9;
--mut:#8B9591;--rule:#242C2A;--soft:#1B2220;--ok:#54BCB3;--bad:#D8836F;
--okw:#142A28;--badw:#2B1B18}}
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
    <button class=chip data-f=all aria-pressed=true>all</button>
    <button class=chip data-f=live aria-pressed=false>live</button>
    <button class=chip data-f=todo aria-pressed=false>needs you</button>
    <button class=chip data-f=ended aria-pressed=false>ended</button>
  </div>
  <span class=spacer></span>
  <button class=chip id=dense aria-pressed=false>compact</button>
  <span id=age></span>
</header>
<div id=strip></div>
<div id=setup hidden></div>
<div class=grid id=g></div>
<div id=code hidden></div>
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
  return `<li class="${i.branch?'branch':''} ${i.done?'done':(i.ok?'':'prop')}">
    ${guides}${elbow}
    <span class=box>${i.done?'▪':(i.ok?'▫':'?')}</span>
    <span class=txt title="${esc(i.t)}">${esc(i.t)}</span>
    ${i.roll?`<span class=roll><span class=mini><i style="width:${i.pct}%"></i></span>${i.roll}</span>`:''}
    ${(WRITABLE && CODE() && !i.done && !i.branch)?
       (!i.ok? `<button class="act ok" data-do=accept data-s="${sid}" data-i="${i.id}">accept</button>`
             : `<button class=act data-do=done data-s="${sid}" data-i="${i.id}">tick</button>`)
      :''}
  </li>`;
}
const openFolds=new Set();
let WRITABLE=false;
// The code lives only in the browser that was told it. It is never fetched
// from the server, which is the entire point: the board can verify it and
// nothing else on this machine can obtain it.
const CODE = () => localStorage.getItem('mission-code') || '';

async function act(action, session, ids, text){
  const r = await fetch('/', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({code: CODE(), action, session, ids, text: text||''})});
  if (!r.ok){
    const e = await r.json().catch(()=>({error:'failed'}));
    if (r.status === 403) localStorage.removeItem('mission-code');
    alert(e.error || 'failed');
  }
  tick();
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
let FILTER='all', QUERY='';

// Everything a card is searchable BY: the goal, every task, the folder, and
// the ids -- searching a board of goals for a task you half remember is the
// case that matters, and matching only the title fails it.
const haystack = s => [s.title, s.objective, s.cwd, s.id,
  ...(s.tree||[]).map(i=>i.t), ...(s.criteria||[]),
  ...(s.children||[]).map(k=>k.title)].join(' ').toLowerCase();

const passes = s =>
  (QUERY === '' || haystack(s).includes(QUERY)) &&
  (FILTER === 'all'
   || (FILTER === 'live'  && !s.ended)
   || (FILTER === 'ended' && s.ended)
   // "needs you" is the only filter that is about YOUR attention rather than
   // the session's state: proposals waiting, or no mission written at all.
   || (FILTER === 'todo'  && (s.pending_accept > 0 || !s.has_mission
                              || (s.review||[]).length)));

async function tick(){
  let payload; try{ payload=await (await fetch('/data')).json() }catch(e){ return }
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
  document.getElementById('g').innerHTML = d.length? d.map(s=>`
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
         ${s.pending_accept?`<span class="warn ask">${s.pending_accept} awaiting accept${
   (WRITABLE&&CODE())?` <button class="act ok" data-do=acceptall data-s="${s.full}">accept all</button>`:''
 }</span>`:'<span></span>'}</div>
         ${(s.pending_accept && !(WRITABLE&&CODE()))?`
           <div class=why>To accept them, in your own terminal:</div>
           <div class=howto>mission accept --pending --on ${esc(s.id)}</div>`:''}
         <ul class=chk>${visible(s.tree).map(i=>row(i,false,s.full)).join('')}</ul><!-- map(row) passes the INDEX as row's second argument, so every row
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
          <span class="dot ${x.live?'working':'ended'}"></span>
          <span class=kn>${esc(x.id)}</span>
          <span class=kt>${x.live?'live':'ended'}</span>
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
       ${s.topfiles.length?`<br>${s.topfiles.slice(0,3).map(f=>esc(f.f)+' '+f.n+'x').join(' · ')}`:''}</div>
   </div>`).join('') : (all.length? '' : '<p class=none>No live sessions.</p>');
}
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
document.getElementById('g').addEventListener('click', e=>{
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
  if (b.dataset.do === 'note'){
    const inp = document.querySelector(`[data-note="${sid}"]`);
    if (inp && inp.value.trim()){ act('note', sid, [], inp.value); inp.value=''; }
    return;
  }
  if (b.dataset.do === 'acceptall'){
    const card = b.closest('.card');
    const ids = [...card.querySelectorAll('[data-do=accept]')].map(x=>x.dataset.i);
    if (ids.length) act('accept', sid, ids);
    return;
  }
  act(b.dataset.do, sid, [b.dataset.i]);
});
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

    def invalidate(self) -> None:
        """After a write, the next read must not serve the pre-write board."""
        self.rows, self.at = snapshot(), time.time()

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

    def do_GET(self):                                    # noqa: N802
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
            CACHE.invalidate()          # the plan just changed; do not show stale
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
    WRITES = Session(enabled=bool(writable))
    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    srv.daemon_threads = True
    # The server writes its own record, because it is the only thing that knows
    # it is up and which pid it is. Written by the LAUNCHER, a board started any
    # other way -- by hand, or after a restart -- leaves the previous pid in the
    # file, and `mission board --stop` then signals a pid that has since been
    # recycled to something else entirely.
    from .daemon import claim, release
    claim(port)
    print(f"\n  mission board -> http://127.0.0.1:{port}")
    if WRITES.enabled:
        print(f"\n  write code: {WRITES.code}"
              f"\n  type it into the board once to accept and tick from there."
              f"\n  it is not on disk and no page returns it — only this terminal"
              f"\n  has it, which is why the agent cannot use these buttons.")
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
