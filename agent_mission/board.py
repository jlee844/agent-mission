"""Every live session on one page: its mission, its checklist, what it has done.

Serves on 127.0.0.1. Reads transcripts and the mission log; writes nothing.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .session import PROJECTS, activity, live, transcript_for
from .store import MissionStore, root_for


def _slug(cwd: str) -> str:
    return "-" + cwd.replace("/", "-").lstrip("-")


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
            rows.append({
                "id": sid[:8], "full": sid,
                "cwd": proc["cwd"].replace(str(Path.home()), "~"),
                "procs": proc["procs"],
                "has_mission": m is not None,
                "objective": m.objective if m else "",
                "criteria": m.success_criteria if m else [],
                "constraints": m.constraints if m else [],
                "non_goals": m.non_goals if m else [],
                "items": [{"id": i.id, "t": i.text, "done": i.done,
                           "ok": i.accepted} for i in m.items] if m else [],
                "done": m.done_count if m else 0,
                "total": len(m.checklist) if m else 0,
                "pending_accept": len(m.unaccepted) if m else 0,
                "calls": a.calls, "files": len(a.files), "tests": a.tests,
                "failures": a.failures, "asks": a.last_asks,
                "topfiles": [{"f": k, "n": v} for k, v in list(a.files.items())[:8]],
                "mtime": tp.stat().st_mtime,
            })
    return sorted(rows, key=lambda r: (not r["has_mission"], -r["mtime"]))


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
.sid{font-family:var(--mono);font-size:.68rem;color:var(--mut);display:flex;
justify-content:space-between;margin-bottom:.5rem}
.obj{font-size:1.03rem;line-height:1.4;font-weight:600;margin:0 0 .7rem;text-wrap:pretty}
.none{color:var(--mut);font-size:.85rem;line-height:1.5}
.none code{font-family:var(--mono);font-size:.78rem;background:var(--soft);
padding:.1rem .35rem;border-radius:3px}
h3{font-family:var(--mono);font-size:.6rem;letter-spacing:.13em;text-transform:uppercase;
color:var(--mut);font-weight:500;margin:.9rem 0 .35rem}
ul{margin:0;padding-left:1.05rem}
li{margin:.16rem 0;line-height:1.45;font-size:.86rem}
li.ng{color:var(--mut)}
.chk{list-style:none;padding:0}
.chk li{display:flex;gap:.5rem;align-items:flex-start;font-size:.88rem;padding:.13rem 0}
.box{font-family:var(--mono);color:var(--mut);flex:none}
.chk li.done{color:var(--mut);text-decoration:line-through}
.chk li.done .box{color:var(--ok);text-decoration:none}
.chk li.prop .box{color:var(--bad)}
.bar{height:5px;background:var(--soft);border-radius:3px;overflow:hidden;margin:.55rem 0 .1rem}
.bar i{display:block;height:100%;background:var(--ok)}
.meta{font-family:var(--mono);font-size:.7rem;color:var(--mut);margin-top:.75rem;
padding-top:.6rem;border-top:1px solid var(--soft);line-height:1.7}
.warn{color:var(--bad)}
</style>
<header><h1>Missions</h1><span id=age></span></header>
<div class=grid id=g></div>
<script>
const esc=t=>(t||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
async function tick(){
  let d; try{ d=await (await fetch('/data')).json() }catch(e){ return }
  document.getElementById('age').textContent=new Date().toLocaleTimeString();
  document.getElementById('g').innerHTML = d.length? d.map(s=>`
   <div class=card>
     <div class=sid><span>${s.id} · ${esc(s.cwd)}</span>
       <span>${s.procs} live here</span></div>
     ${s.has_mission? `
       <p class=obj>${esc(s.objective)}</p>
       ${s.total? `<div class=bar><i style="width:${100*s.done/s.total}%"></i></div>
         <div class=sid><span>${s.done} of ${s.total} done</span>
         ${s.pending_accept?`<span class=warn>${s.pending_accept} awaiting accept</span>`:'<span></span>'}</div>
         <ul class=chk>${s.items.map(i=>`
           <li class="${i.done?'done':(i.ok?'':'prop')}">
             <span class=box>[${i.done?'x':(i.ok?' ':'?')}]</span>
             <span>${esc(i.t)}</span></li>`).join('')}</ul>`:''}
       ${s.criteria.length?`<h3>Done when</h3><ul>${s.criteria.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
       ${s.constraints.length?`<h3>Constraints</h3><ul>${s.constraints.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
       ${s.non_goals.length?`<h3>Not doing</h3><ul>${s.non_goals.map(c=>`<li class=ng>${esc(c)}</li>`).join('')}</ul>`:''}
     ` : `<p class=none>No mission yet.<br>Run <code>mission init</code> in this
          session to write one — it takes a minute and the agent cannot change it.</p>
          ${s.asks.length?`<h3>Currently asked</h3><p class=none>${esc(s.asks[s.asks.length-1])}</p>`:''}`}
     <div class=meta>${s.calls.toLocaleString()} calls · ${s.files} files ·
       ${s.tests} test runs · <span class="${s.failures?'warn':''}">${s.failures} failed</span>
       ${s.topfiles.length?`<br>${s.topfiles.slice(0,3).map(f=>esc(f.f)+' '+f.n+'x').join(' · ')}`:''}</div>
   </div>`).join('') : '<p class=none>No live sessions.</p>';
}
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
    print(f"\n  mission board -> http://127.0.0.1:{port}\n  ctrl-c to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  stopped")
