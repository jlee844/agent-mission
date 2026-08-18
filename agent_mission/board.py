"""Every live session on one page: its mission, its checklist, what it has done.

Serves on 127.0.0.1. Reads transcripts and the mission log; writes nothing.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


def _missions_home() -> Path:
    import os
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


def snapshot() -> list[dict]:
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
            m = MissionStore(root_for(sid)).load()
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
            m = MissionStore(d).load()
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
    return sorted(rows, key=lambda r: (r.get("ended", False),
                                       not r["has_mission"], -r["mtime"]))


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
.grid{display:grid;gap:1.1rem;grid-template-columns:repeat(auto-fill,minmax(23rem,1fr))}
.card{background:var(--card);border:1px solid var(--rule);border-radius:5px;padding:1.1rem 1.2rem}
.card.ended{opacity:.62}
.sid{font-family:var(--mono);font-size:.68rem;color:var(--mut);display:flex;
justify-content:space-between;margin-bottom:.5rem}
.obj{font-size:1.05rem;line-height:1.35;font-weight:600;margin:0 0 .25rem;
text-wrap:pretty;letter-spacing:-.008em}
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
.chk .g{flex:none;width:1.05rem;font-family:var(--mono);color:var(--rule);
white-space:pre;-webkit-user-select:none;user-select:none}
.chk .box{flex:none;font-family:var(--mono);color:var(--mut);padding-right:.42rem}
.chk li.done{color:var(--mut)}
.chk li.done .txt{text-decoration:line-through;text-decoration-color:var(--rule)}
.chk li.done .box{color:var(--ok)}
.chk li.prop .box{color:var(--bad)}
.chk li.branch{font-weight:600;margin-top:.42rem}
.chk li.branch .txt{letter-spacing:-.005em}
.chk .txt{flex:1}
.chk .roll{flex:none;font-family:var(--mono);font-size:.68rem;color:var(--mut);
padding-left:.6rem;display:flex;align-items:center;gap:.4rem}
.chk .mini{width:2.4rem;height:3px;background:var(--soft);border-radius:2px;overflow:hidden}
.chk .mini i{display:block;height:100%;background:var(--ok)}
.box{font-family:var(--mono);color:var(--mut);flex:none}
.chk li.done{color:var(--mut);text-decoration:line-through}
.chk li.done .box{color:var(--ok);text-decoration:none}
.chk li.prop .box{color:var(--bad)}
.doneblock{margin-top:.3rem}
.doneblock summary{font-family:var(--mono);font-size:.66rem;letter-spacing:.06em;
color:var(--mut);cursor:pointer;list-style:none;padding:.2rem 0;text-transform:uppercase}
.doneblock summary::-webkit-details-marker{display:none}
.doneblock summary::before{content:"▸ "}
.doneblock[open] summary::before{content:"▾ "}
.doneblock summary:hover{color:var(--ink)}
.chk.flat li{padding-left:.1rem}
.bar{height:5px;background:var(--soft);border-radius:3px;overflow:hidden;margin:.55rem 0 .1rem}
.bar i{display:block;height:100%;background:var(--ok)}
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
  <span class=spacer></span><span id=age></span>
</header>
<div class=grid id=g></div>
<p class=none id=empty hidden>Nothing matches that.</p>
<script>
const esc=t=>(t||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
// One row of the plan. `flat` drops the connectors: inside the finished fold
// the rows come from different parents, so a guide there would draw a branch
// that isn't on screen.
function row(i,flat){
  // Roots are flush bullets with no connector, so the guide for the root level
  // refers to a line that is never drawn. Drop it, or children render as "│├"
  // against nothing.
  const guides = flat? '' : i.guides.slice(1).map(g=>`<span class=g>${g?'│':' '}</span>`).join('');
  const elbow  = (!flat && i.d)? `<span class=g>${i.last?'└':'├'}</span>` : '';
  return `<li class="${i.branch?'branch':''} ${i.done?'done':(i.ok?'':'prop')}">
    ${guides}${elbow}
    <span class=box>${i.done?'▪':(i.ok?'▫':'?')}</span>
    <span class=txt>${esc(i.t)}</span>
    ${i.roll?`<span class=roll><span class=mini><i style="width:${i.pct}%"></i></span>${i.roll}</span>`:''}
  </li>`;
}
const openFolds=new Set();
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
   || (FILTER === 'todo'  && (s.pending_accept > 0 || !s.has_mission)));

async function tick(){
  let all; try{ all=await (await fetch('/data')).json() }catch(e){ return }
  const d = all.filter(passes);
  document.getElementById('age').textContent =
    (d.length === all.length ? `${all.length} sessions` : `${d.length} of ${all.length}`)
    + ' · ' + new Date().toLocaleTimeString();
  document.getElementById('empty').hidden = d.length > 0 || all.length === 0;
  document.getElementById('g').innerHTML = d.length? d.map(s=>`
   <div class="card ${s.ended?'ended':''}">
     <div class=sid><span>${s.id} · ${esc(s.cwd)}</span>
       <span>${s.ended?'ended':s.procs+' live here'}</span></div>
     ${s.has_mission? `
       <p class=obj>${esc(s.title)}</p>
       ${s.objective && s.objective!==s.title?`<p class=objsub>${esc(s.objective)}</p>`:''}
       ${s.total? `<div class=bar><i style="width:${100*s.done/s.total}%"></i></div>
         <div class=sid><span>${s.done} of ${s.total} done</span>
         ${s.pending_accept?`<span class=warn>${s.pending_accept} awaiting accept</span>`:'<span></span>'}</div>
         <ul class=chk>${s.tree.filter(i=>!i.hid).map(row).join('')}</ul>
         ${s.tree.some(i=>i.hid)?`<details class=doneblock data-sid="${s.id}" ${
             openFolds.has(s.id)?'open':''}><summary>${
             s.tree.filter(i=>i.hid).length} finished</summary>
           <ul class="chk flat">${s.tree.filter(i=>i.hid).map(r=>row(r,true)).join('')}</ul>
         </details>`:''}`:''}
       ${s.criteria.length?`<h3>Done when</h3><ul>${s.criteria.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
       ${s.constraints.length?`<h3>Constraints</h3><ul>${s.constraints.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
       ${s.non_goals.length?`<h3>Not doing</h3><ul>${s.non_goals.map(c=>`<li class=ng>${esc(c)}</li>`).join('')}</ul>`:''}
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
     ${(s.children||[]).length?`<div class=kids><h3>Delegated</h3>${
        s.children.map(k=>`<div class=kid><span class=kn>${esc(k.id)}</span>
          <span class=kt>${esc(k.title)}</span>
          <span class=kp>${k.total?k.done+'/'+k.total:'—'}</span></div>`).join('')}</div>`:''}
     <div class=meta>${s.calls.toLocaleString()} calls · ${s.files} files ·
       ${s.tests} test runs · <span class="${s.failures?'warn':''}">${s.failures} failed</span>
       ${s.topfiles.length?`<br>${s.topfiles.slice(0,3).map(f=>esc(f.f)+' '+f.n+'x').join(' · ')}`:''}</div>
   </div>`).join('') : '<p class=none>No live sessions.</p>';
}
// The page re-renders every 4s, so an opened fold would snap shut under the
// reader. Remember which cards are open. `toggle` does not bubble, hence the
// capture listener -- and it is bound once, to a container render() never
// replaces, so it survives every re-render.
document.getElementById('g').addEventListener('toggle', e=>{
  const sid = e.target.dataset && e.target.dataset.sid;
  if (sid) e.target.open? openFolds.add(sid) : openFolds.delete(sid);
}, true);
document.getElementById('q').addEventListener('input', e=>{
  QUERY = e.target.value.trim().toLowerCase(); tick();
});
document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click', ()=>{
  FILTER = b.dataset.f;
  document.querySelectorAll('.chip').forEach(
    o=>o.setAttribute('aria-pressed', String(o===b)));
  tick();
}));
tick(); setInterval(tick,4000);
</script>"""


class _H(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        body, ctype = ((json.dumps(snapshot()).encode(), "application/json")
                       if self.path.startswith("/data")
                       else (PAGE.encode(), "text/html; charset=utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # noqa: A003
        pass


def serve(port: int = 8976) -> None:
    srv = HTTPServer(("127.0.0.1", port), _H)
    # The server writes its own record, because it is the only thing that knows
    # it is up and which pid it is. Written by the LAUNCHER, a board started any
    # other way -- by hand, or after a restart -- leaves the previous pid in the
    # file, and `mission board --stop` then signals a pid that has since been
    # recycled to something else entirely.
    from .daemon import claim, release
    claim(port)
    print(f"\n  mission board -> http://127.0.0.1:{port}\n  ctrl-c to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  stopped")
    finally:
        release(port)
