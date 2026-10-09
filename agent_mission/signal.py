"""C14: the board stops being a window you watch and becomes a place you are sent.

The board is a monitoring surface — a two-monitor artifact. On one screen it
sits behind the terminal and gets forgotten; its own author went with the
conversation and stopped looking, and proposals sat 15.9h. Link-always-available
proved necessary but not sufficient.

One attention rule: when something TRANSITIONS to waiting-on-you, exactly one
edge-triggered line reaches a surface you are already looking at, carrying the
link. A standing count repeated every turn is wallpaper — the C12d lesson — so
this fires on the RISE and is silent on repeat, silent on shrink, silent always
otherwise. The statusline keeps the standing count; this is the interrupt form
of the same number.

State is per Claude session, because the signal belongs to a conversation: two
sessions sharing one state file would eat each other's edges.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import missions as M

NOTIFY_RATE_S = 600          # one OS notification per 10 min, at most


def _home() -> Path:
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


def _state_dir() -> Path:
    return _home() / "signal"


def _state_path(sid: str) -> Path:
    safe = "".join(c for c in sid if c.isalnum() or c in "-_")[:64] or "none"
    return _state_dir() / f"{safe}.json"


def _load(sid: str) -> dict:
    try:
        return json.loads(_state_path(sid).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(sid: str, state: dict) -> None:
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        p = _state_path(sid)
        tmp = p.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass                  # state is an optimisation, never a requirement


def notifications_wanted() -> bool:
    """Opt-in, off by default. The pop-never decision stands as the DEFAULT;
    the file exists because the author himself reported forgetting the board."""
    return (_home() / "notify-optin").exists()


def _contract_path() -> Path:
    return Path(os.environ.get("AGENT_MISSION_CONTRACT",
                               Path.home() / ".claude" / "commands" / "mission.md"))


def check(sid: str, base=None) -> list[str]:
    """One line per mission whose pending count ROSE since this session last
    looked. Empty list almost always — silence is the normal output.

    Also the one channel that reaches a LIVE conversation: hooks and the
    contract otherwise land only at session boundaries, so a long session
    runs whatever format was current when it started — five upgrades in four
    days meant every long conversation was stale. First look baselines the
    installed contract's mtime; a later change emits ONE line telling the
    agent to re-read, then goes silent until the next upgrade."""
    state = _load(sid)
    seen = state.get("counts", {})
    now_counts: dict = {}
    lines: list[str] = []

    # ⚠️ Edging on a COUNT silently swallows a reply. Tick one claimed item
    # and have a peer claim another in the same gap and the count is
    # unchanged, so nothing is announced -- and that is the ordinary case
    # while a human works through the review queue. The id SET cannot
    # cancel: what is new is what was not there before. Caught by a test that
    # ticked one handback and released another.
    #
    # A state file written before this key existed is BASELINED rather than
    # replayed: first look records what is there and says nothing, the same
    # rule the contract-mtime check uses. Otherwise one upgrade announces
    # every outstanding item at once, which is the wallpaper this is built to
    # avoid.
    # Only an UPGRADED state file baselines: it has prior state but no `ids`,
    # so replaying would announce everything outstanding at once. A session
    # with NO state is new, and announcing what is already waiting is the
    # point — two existing tests pin that and caught this when the first
    # version suppressed both cases.
    seen_ids = state.get("ids")
    first_look = seen_ids is None and bool(state)
    seen_ids = seen_ids or {}
    now_ids: dict = {}

    def risen(key: str, items: list) -> list:
        """The items under `key` that were not there last time."""
        ids = [i.id for i in items]
        now_ids[key] = ids
        before = set(seen_ids.get(key, []))
        return [] if first_look else [i for i in items if i.id not in before]

    for mid, st in M.all_missions(base):
        m = st.load()
        if m is None or m.archived:
            continue
        pend = m.unaccepted
        now_counts[mid] = len(pend)
        fresh = risen(mid, pend)
        if fresh:
            title = fresh[-1].text[:70]
            more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
            lines.append(
                f"a proposal landed on {m.title}: “{title}”{more} — "
                f"accept on the board or "
                f"`mission accept --pending --on {mid}`")

        # C17: suggested ticks get the same edge treatment as proposals --
        # announce the RISE, then silence. The action offered is the board,
        # where the verdict is already attached to the row.
        sugg = m.suggested
        skey = f"sugg:{mid}"
        now_counts[skey] = len(sugg)
        fresh = risen(skey, sugg)
        if fresh:
            last = fresh[-1]
            title = last.text[:70]
            more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
            # WHO claimed it. Without this a dispatcher cannot tell its own
            # peer's reply from its own earlier claim -- and that is the
            # whole question it is waiting on.
            who = (f" by {last.claimed_by[:8]}"
                   if getattr(last, "claimed_by", "") else "")
            lines.append(
                f"[{last.id}] on {m.title} is claimed finished{who}: "
                f"“{title}”{more} — the board shows whether the disk agrees")

        # A HANDBACK is the reply a dispatcher most needs and the only one
        # nothing announced. A peer that takes an item, finds it cannot be
        # done and releases it with a reason produced silence: the item
        # simply reappeared in the ready lane, and whoever handed it over
        # learned nothing. Measured the same way as the rest -- a COUNT that
        # rose -- so a shrink or a repeat re-floors quietly.
        back = [i for i in m.items if i.released_at and not i.done]
        rkey = f"rel:{mid}"
        now_counts[rkey] = len(back)
        newly = risen(rkey, back)
        if newly:
            last = max(newly, key=lambda i: i.released_at)
            why = (last.release_note or "no reason given")[:90]
            # ⚠️ "+N more" is not decoration here, it is the difference
            # between late and NEVER. The edge records every id it saw, so a
            # second handback in the same gap is marked seen while being left
            # out of the line -- and it can never be announced again. Found
            # live: two items released in one turn, one reported.
            more = f" (+{len(newly) - 1} more)" if len(newly) > 1 else ""
            lines.append(
                f"[{last.id}] on {m.title} was handed BACK{more}: “{why}” — "
                f"it is ready again; the reason is yours to rule on")

        # A verifier's finding: the item is agreed, someone checked it, and
        # it is not finished. Same edge, different meaning from a handback --
        # that one says "I could not", this says "this is wrong".
        found = [i for i in m.items if i.rework and not i.done]
        fkey = f"find:{mid}"
        now_counts[fkey] = len(found)
        newly = risen(fkey, found)
        if newly:
            last = max(newly, key=lambda i: i.rework_at)
            more = f" (+{len(newly) - 1} more)" if len(newly) > 1 else ""
            lines.append(
                f"[{last.id}] on {m.title} was sent back by a check{more}: "
                f"“{last.rework[:90]}”")

    try:
        cm = _contract_path().stat().st_mtime
    except OSError:
        cm = 0.0
    if cm:
        baseline = state.get("contract_mtime")
        if baseline is None:
            state["contract_mtime"] = cm
        elif cm > baseline + 1e-6:
            state["contract_mtime"] = cm
            lines.append(
                "the mission contract was upgraded mid-session — run "
                "`mission whereami --full` and follow the protocol it prints; "
                "it supersedes what you read at session start")

    # Shrink and repeat both update the floor silently, so a decline never
    # re-fires and the next rise is measured from where the person left it.
    state["counts"] = now_counts
    state["ids"] = now_ids
    _save(sid, state)
    return lines


def notify(lines: list[str], board_url: str = "") -> bool:
    """The opt-in OS notification. Edge-triggered like the line, and rate
    limited on top: at most one per NOTIFY_RATE_S, however many edges fire."""
    if not lines or not notifications_wanted():
        return False
    gate = _state_dir() / "notify.ts"
    try:
        last = float(gate.read_text())
    except Exception:
        last = 0.0
    if time.time() - last < NOTIFY_RATE_S:
        return False
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        gate.write_text(str(time.time()), encoding="utf-8")
        body = (lines[0][:120] + (f" — {board_url}" if board_url else ""))
        subprocess.run(
            ["osascript", "-e",
             'display notification "{}" with title "Missions"'.format(
                 body.replace("\\", "").replace('"', "'"))],
            capture_output=True, timeout=5)
        return True
    except Exception:
        return False          # a broken notifier must not break the hook
