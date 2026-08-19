"""What is wrong with the missions themselves.

`setup --check` answers "is the tool installed". This answers "is the record
trustworthy" — a different question, and the one that mattered when a stray
event made a live plan invisible for eleven hours.

Every check here reads the event logs and nothing else. No model, no similarity,
no inference: each finding is a fact about the file, which is the only kind of
finding this project has ever managed to make stick.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .store import MissionStore, root_for


def _logs(base=None):
    home = root_for("x", base).parent
    if not home.exists():
        return
    for d in sorted(home.iterdir()):
        log = d / "events.jsonl"
        if d.is_dir() and log.exists():
            yield d, [e for e in MissionStore(d).events()]


# Only findings a person can CLOSE belong in the board's review lane. Nine of
# the sixteen findings in the real corpus are unprovenanced writes -- true
# statements about history that no action changes. A lane containing them
# carries a permanent badge, and a permanent badge is one you stop seeing in a
# day: the same failure as red once meaning three unrelated things.
CLEARABLE = {"duplicate mission start", "written from another directory",
             "damaged log", "agent-transcribed goal", "detour left open"}


def review(base=None) -> list[dict]:
    """What needs a human to re-read it, and can be cleared by one.

    The eligibility rule IS the feature. If an item has no action that removes
    it from this list, it does not belong in this list.
    """
    acked = _acknowledged(base)
    return [f for f in findings(base)
            if f["what"] in CLEARABLE
            and (f["sid"], f["what"]) not in acked]


def _acknowledged(base=None) -> set:
    out = set()
    for d, evs in _logs(base):
        for e in evs:
            if e.get("kind") == "acknowledged":
                out.add((d.name, e.get("finding", "")))
    return out


def findings(base=None) -> list[dict]:
    out = []
    for d, evs in _logs(base):
        sid = d.name
        created = [e for e in evs if e.get("kind") == "created"]
        home_cwd = created[0].get("cwd", "") if created else ""

        # 1. A second `created` shadowed a live plan. Impossible to CAUSE now,
        #    but a log written before the fix still carries the event.
        if len(created) > 1:
            out.append({
                "sid": sid, "level": "serious", "what": "duplicate mission start",
                "detail": f"{len(created)} `created` events — everything before "
                          f"the last one was invisible until the fold was fixed",
            })

        # 2. An event written from a directory that is not this mission's. This
        #    is the fingerprint of a session working elsewhere writing in here,
        #    and it is exactly how the duplicate above arrived.
        for e in evs:
            c = e.get("cwd")
            if c and home_cwd and c != home_cwd:
                out.append({
                    "sid": sid, "level": "serious",
                    "what": "written from another directory",
                    "detail": f"{e.get('kind')} came from {c}, not {home_cwd}",
                })

        # 3. Protected writes with no record of who typed them.
        blind = sum(1 for e in evs
                    if e.get("kind") in ("created", "set")
                    and "typed_by" not in e)
        if blind:
            out.append({
                "sid": sid, "level": "note", "what": "unprovenanced writes",
                "detail": f"{blind} protected write(s) predate typed_by and "
                          f"cannot say whether a person or an agent made them",
            })

        m = MissionStore(d).load()
        if m is None:
            continue

        # A detour nobody returned from. Declared, so this is a fact rather
        # than a guess about whether attention wandered.
        if m.detours:
            opened = [e["at"] for e in evs if e.get("kind") == "detour"]
            age = (time.time() - opened[-1]) / 3600 if opened else 0
            if age >= 8:
                out.append({
                    "sid": sid, "level": "note", "what": "detour left open",
                    "detail": f'"{m.detours[-1]}" — open {age:.0f}h. '
                              f"`mission return` replays the goal",
                })

        # 4. A goal the agent wrote down. Permitted, and worth re-reading.
        if not m.typed_by_human:
            out.append({
                "sid": sid, "level": "note", "what": "agent-transcribed goal",
                "detail": "`mission why objective` shows what it recorded",
            })

        # 5. Proposals nobody has ruled on, with the age of the oldest.
        if m.unaccepted:
            ages = [e["at"] for e in evs if e.get("kind") == "proposed"
                    and e["item_id"] in {i.id for i in m.unaccepted}]
            oldest = (time.time() - min(ages)) / 3600 if ages else 0
            out.append({
                "sid": sid, "level": "todo", "what": "awaiting you",
                "detail": f"{len(m.unaccepted)} proposal(s), oldest "
                          f"{oldest:.0f}h — `mission accept --pending "
                          f"--session {sid[:8]}`",
            })

        # 6. A log with damage. events() skips unparseable lines and counts.
        st = MissionStore(d)
        st.load()
        if st.damaged:
            out.append({
                "sid": sid, "level": "serious", "what": "damaged log",
                "detail": f"{st.damaged} line(s) could not be parsed and were "
                          f"skipped — the events in them are lost",
            })
    return out
