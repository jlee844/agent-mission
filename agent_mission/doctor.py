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
    """Every mission worth checking, exactly once.

    This walked `~/.agent-mission/*/` -- the pre-inversion layout. After the
    inversion that set is the OLD copies: `migrate` writes the events into
    `missions/<name>/` and leaves the session-keyed directory behind, frozen at
    the moment it was lifted. So doctor was auditing ghosts and could not see a
    single live mission. `career-hub` had 64 events; doctor read the 27 in its
    abandoned twin and reported proposals that were accepted two days ago.

    The board's review lane reads this, which is the part that mattered: the
    one surface built to say "something needs you" was sourced from stale data.

    Now: the missions, plus any legacy store nothing has lifted yet -- the same
    already-migrated test `missions.choices()` uses, so the two agree.
    """
    from . import missions as M

    for mid, st in M.all_missions(base):
        yield st.root, [e for e in st.events()]

    lifted = set(M.attachments(base))
    for sid, st in M.legacy_stores(base):
        if sid in lifted:
            continue
        yield st.root, [e for e in st.events()]


# Only findings a person can CLOSE belong in the board's review lane. Nine of
# the sixteen findings in the real corpus are unprovenanced writes -- true
# statements about history that no action changes. A lane containing them
# carries a permanent badge, and a permanent badge is one you stop seeing in a
# day: the same failure as red once meaning three unrelated things.
CLEARABLE = {"duplicate mission start", "damaged log",
             "agent-transcribed goal", "detour left open", "stale goal"}


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

        # 2. Events written from somewhere other than this mission's directory.
        #
        #    This was `serious`, one finding PER EVENT, and clearable. All three
        #    are now wrong, and the first real run proved it: career-hub alone
        #    produced 40 identical rows and the review lane went from 2 items to
        #    42 -- past the number the lane was pre-registered to refuse to ship
        #    at ("if it shows 16, the eligibility rule is wrong").
        #
        #    The rule is wrong because C11c changed what cwd MEANS. It used to
        #    route writes, so a foreign directory was the fingerprint of a
        #    misroute. Now names route and `mission set ... --on career` from any
        #    terminal is the documented normal case -- so this detector measures
        #    the feature. One note, with a count, and nothing to close.
        elsewhere = sum(1 for e in evs
                        if e.get("cwd") and home_cwd
                        and e.get("cwd") != home_cwd)
        if elsewhere:
            out.append({
                "sid": sid, "level": "note",
                "what": "written from another directory",
                "detail": f"{elsewhere} write(s) came from outside {home_cwd} "
                          f"— normal since goals are addressed by name",
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
        if m is None or getattr(m, "archived", False):
            continue

        # A goal nobody has touched in a fortnight is not "in progress", and
        # leaving it on the board teaches you to read past the board.
        last = max((e.get("at", 0) for e in evs), default=0)
        idle_days = (time.time() - last) / 86400
        if idle_days >= 14 and m.done_count < m.total_count:
            out.append({
                "sid": sid, "level": "note", "what": "stale goal",
                "detail": f"untouched {idle_days:.0f} days — "
                          f"`mission archive {sid}` if it is over",
            })

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
                # `--on <name>`, in full. It was `--session {sid[:8]}`, which
                # truncated a mission NAME to eight characters -- "mltest-s" --
                # so the one line here that exists to be pasted could not be.
                "detail": f"{len(m.unaccepted)} proposal(s), oldest "
                          f"{oldest:.0f}h — `mission accept --pending "
                          f"--on {sid}`",
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
