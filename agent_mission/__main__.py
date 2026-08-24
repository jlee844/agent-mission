"""mission — the goal, beside the work, that the agent cannot quietly rewrite.

    mission init                 start one for this session (opens your editor)
    mission                      show it, with measured progress
    mission add "..."            add a checklist item
    mission done <id>            tick one off
    mission propose "..."        agent suggests an item (inert until accepted)
    mission accept <id>          you accept a proposal
    mission board                every live session on one page
"""
from __future__ import annotations

import argparse
import json
import os
import time
import subprocess
import sys
import tempfile
from pathlib import Path

from .daemon import ensure as ensure_board, running as board_running, stop as board_stop
from .session import (NoSessionError, activity, current_session_id,
                      find_session, resolve_session, short_id,
                      transcript_for)
from .store import (FIELD_AUTHORITY, Authority, Item, MissionStore,
                    NoMissionError, NoSuchItemError, ProtectedFieldError,
                    children_of, root_for)

TEMPLATE = """\
# Your mission for this session. Lines starting with # are ignored.
# Everything here is YOURS — the agent can propose and record, never rewrite.

# A short title, three to six words. This is what the board shows.
NAME: {name}

OBJECTIVE: {objective}

# What has to be true for this to be finished.
SUCCESS:
- {criterion}

# Hard limits. The agent must not cross these.
CONSTRAINTS:
-

# Explicitly out of scope, so scope creep is visible.
NON-GOALS:
-

# The plan. Indent with two spaces to nest a task under a subgoal.
# Tick items off with `mission done <id>`.
CHECKLIST:
-
"""


def _parse(text: str) -> dict:
    out: dict[str, list[str] | str] = {"name": "", "objective": "", "success_criteria": [],
                                       "constraints": [], "non_goals": [],
                                       "checklist": []}
    key = None
    keys = {"SUCCESS:": "success_criteria", "CONSTRAINTS:": "constraints",
            "NON-GOALS:": "non_goals", "CHECKLIST:": "checklist"}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("NAME:"):
            out["name"] = line.split(":", 1)[1].strip()
            key = None
        elif line.startswith("OBJECTIVE:"):
            out["objective"] = line.split(":", 1)[1].strip()
            key = None
        elif line.strip() in keys:
            key = keys[line.strip()]
        elif line.lstrip().startswith("-") and key:
            v = line.lstrip()[1:].strip()
            if not v:
                continue
            if key == "checklist":
                # indentation is nesting: "  - x" hangs under the previous "- y"
                indent = len(line) - len(line.lstrip())
                out[key].append({"text": v, "indent": indent})
            else:
                out[key].append(v)
    return out


def _edit(seed: str) -> str:
    # EDITOR routinely carries arguments -- "code -w", "subl -w", "vim -f".
    # Treating the whole string as one executable name fails with a confusing
    # FileNotFoundError naming the flags.
    import shlex
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    with tempfile.NamedTemporaryFile("w+", suffix=".mission", delete=False) as fh:
        fh.write(seed)
        path = fh.name
    subprocess.run([*shlex.split(editor), path], check=False)
    return Path(path).read_text(encoding="utf-8")


def _store(sid: str, a=None) -> MissionStore:
    from . import missions as M
    root = (M.missions_root() / sid if (M.missions_root() / sid).exists()
            else root_for(sid))
    st = MissionStore(root)
    if a is not None:
        try:
            st.context_cwd = str(Path(getattr(a, "cwd", ".") or ".").resolve())
        except Exception:
            st.context_cwd = ""
        st.context_via = getattr(a, "_via", "")
    return st


def _mission_target(a) -> tuple[str, str] | None:
    """Which MISSION a command means. Name routes; the session only speaks.

    C11c: cwd is reach, not subject. Jonathan's sessions open at the Mission
    Control root because the work needs the whole tree, while the goal lives
    three folders down -- so cwd says what a session can SEE, never what it is
    FOR. It is not consulted here at all.
    """
    from . import missions as M
    on = getattr(a, "on", None) or getattr(a, "into", None)
    if on:
        try:
            return M.find(on), "explicit"
        except NoSessionError as e:
            if e.candidates:
                raise           # ambiguous in the new layout; never widen it
            # A goal that was never migrated is still a goal. If `--on` only
            # reached the new layout, the one routing flag would have a hole
            # exactly where someone's oldest plans live -- and the fix would be
            # "run migrate first", which is a second instruction to follow.
            return find_session(on), "explicit"
    sid = current_session_id()
    if sid:
        mid = M.attachments().get(sid)
        if mid:
            return mid, "attached"
    return None


def _resolve_with_path(a) -> tuple[str, str]:
    """The session, and HOW it was chosen: explicit or env. Never cwd.

    The path matters as much as the answer. An app-attached terminal exports
    CLAUDE_CODE_SESSION_ID, so a human typing there resolves by `env` while
    believing they resolved by `cwd` -- which is how a career objective landed
    on the Tripnom mission and then renamed it. The cwd branch that made that
    belief plausible is gone; see resolve_session.
    """
    given = getattr(a, "session", None)
    if given:
        if (root_for(given) / "events.jsonl").exists():
            return given, "explicit"
        try:
            return find_session(given), "explicit"
        except NoSessionError:
            return given, "explicit"
    if current_session_id():
        return current_session_id(), "env"
    return resolve_session(None), "named"


def _resolve(a):
    """One resolver, mission-first.

    `--on <name>` always wins; then the mission this session is attached to;
    only then the legacy session-keyed store. The working directory is not
    consulted anywhere in this chain -- see resolve_session for what it cost.

    Nobody types a uuid. --into accepted a name from the day it shipped while
    --session, the flag every human-only command needs, took only the full id.
    """
    hit = _mission_target(a)
    if hit:
        a._via = hit[1]
        return hit[0]
    given = getattr(a, "session", None)
    if given and not (root_for(given) / "events.jsonl").exists():
        try:
            return find_session(given)
        except NoSessionError:
            # No mission matches it -- so treat it as a literal id. `init` is
            # exactly this case: the session it is about to create has no
            # events yet, so name resolution has nothing to match.
            return given
    return resolve_session(given)


HUMAN_ENV = "AGENT_MISSION_I_AM_HUMAN"
_SUBCOMMANDS: set[str] = set()


def _at_a_keyboard() -> bool:
    """Is a person actually typing this?

    The store refuses agent writes, but the CLI could not tell who ran it, so
    it passed by="human" and the guarantee was advisory. In a test, a subagent
    rewrote a protected objective on its first try -- and `why` recorded the
    change as mine. Disclosure in the README did not stop it; nothing did.

    An agent's shell has no controlling terminal. A person typing in one does.
    That is not a security boundary -- an agent can set the override below --
    but it moves impersonation from "what happens by default" to "a deliberate
    lie", which is exactly the threat the design claims to address.
    """
    if os.environ.get(HUMAN_ENV) == "1":
        return True
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _confirm_env_target(a, sid: str) -> bool:
    """A human at a terminal, targeting by env var, must see and confirm it.

    The design assumed a person's terminal has no CLAUDE_CODE_SESSION_ID. An
    app-attached terminal pane has one, so `mission set` typed by hand
    resolved to whichever session owned the pane -- not the mission the person
    was thinking about. It renamed that mission, and the rename made every
    later target line agree with the mistake.
    """
    if getattr(a, "session", None) or getattr(a, "yes", False):
        return True
    try:
        if not sys.stdin.isatty():
            return True            # not an interactive human; other gates apply
    except Exception:
        return True
    m = _store(sid).load()
    if m is None:
        return True
    first = (m.objective or "").strip().split("\n")[0]
    print(f"\n  this will write to:\n"
          f"    {short_id(sid)}  {m.title}\n"
          f"    goal: {first[:72]}\n"
          f"\n  targeted through CLAUDE_CODE_SESSION_ID, which your terminal"
          f"\n  inherited from the session it is attached to — not from where"
          f"\n  you are standing.\n")
    try:
        answer = input("  right mission? [y/N] ").strip().lower()
    except EOFError:
        return False
    if answer in ("y", "yes"):
        return True
    print("\n  nothing written. Name the goal instead:  --on <name>\n")
    return False


def _target(a, what: str):
    """Mission first, session only as the legacy fallback."""
    hit = _mission_target(a)
    if hit:
        mid, via = hit
        a._via = via
        if via == "attached" and not _confirm_env_target(a, mid):
            return None, None
        return mid, _store(mid, a)
    return _legacy_target(a, what)


def _legacy_target(a, what: str):
    """Resolve, record how, and make a tty confirm an env-resolved target.

    Returns (sid, store) or (None, None) if the person declined.
    """
    sid, via = _resolve_with_path(a)
    a._via = via
    if via == "env" and not _confirm_env_target(a, sid):
        return None, None
    return sid, _store(sid, a)


def _human_gate(what: str) -> bool:
    if _at_a_keyboard():
        return True
    print(f"  {what} is yours, and this is not a terminal — refusing.\n"
          f"  If you are an agent: `mission propose \"...\"` instead; it needs\n"
          f"  no permission and stays inert until a person accepts it.\n"
          f"  To compose the command for them: `mission help <command>`.\n"
          f"  If you are a person in a pipeline: {HUMAN_ENV}=1 ahead of the "
          f"command.")
    return False


def cmd_init(a) -> int:
    sid, via = _resolve_with_path(a)
    a._via = via
    if not sid:
        print("  no session id. Run inside Claude Code, or pass --session.")
        return 1
    st = _store(sid, a)
    existing = st.load()
    if existing and not a.force:
        print(f"  a mission already exists for {short_id(sid)}. `mission` to see it, "
              f"`mission init --force` to start over.")
        return 1
    if existing and a.force and existing.checklist and not a.discard_plan:
        # Another session told Jonathan to run `init --force` to fix a bad
        # objective. It would have discarded 11 items and 10 pending proposals:
        # load() folds from the LAST `created` event, so a re-init drops
        # everything before it. Editing a field is what he actually wanted.
        n, p = len(existing.checklist), len(existing.unaccepted)
        print(f"\n  {short_id(sid)} has a plan: {n} items, {p} awaiting accept."
              f"\n  `init --force` starts over and drops all of it.\n"
              f"\n  To change what the mission SAYS, keeping the plan:"
              f"\n    mission set objective \"...\""
              f"\n    mission set name \"...\"\n"
              f"\n  To really start over: add --discard-plan\n")
        return 1

    seed_obj, seed_crit = "", ""
    tp = transcript_for(sid)
    if tp and not a.blank:
        asks = activity(tp).last_asks
        if asks:
            seed_obj = asks[0][:160]
    if a.from_file:
        # An agent that has just interviewed the user writes the answers here.
        # It is transcription, not authorship: every line came from a reply.
        filled = Path(a.from_file).read_text(encoding="utf-8")
    else:
        text = TEMPLATE.format(name="", objective=seed_obj, criterion=seed_crit)
        filled = text if a.no_edit else _edit(text)
    parsed = _parse(filled)
    if not parsed["objective"]:
        print("  no OBJECTIVE given — nothing saved.")
        return 1

    if existing and a.discard_plan:
        # Say it in the log, so the fold has a reason to start over rather
        # than inferring one from a duplicate.
        st.discard(by="human")

    cwd = str(Path(a.cwd).resolve())
    # `init` is deliberately NOT gated: the documented flow is the agent
    # interviewing you and transcribing your answers with --from-file. But
    # recording that as plain "human" made an agent-authored objective
    # indistinguishable from one you typed -- `why objective` said "human"
    # about a goal an agent had written, which is the exact failure this tool
    # exists to make impossible. So the runner is recorded alongside the
    # authority, and `show` says so.
    typed = "human" if _at_a_keyboard() else "agent"
    st.create(sid, cwd, parsed["objective"], by="human", typed_by=typed)
    if parsed["name"]:
        st.set_protected("name", parsed["name"], by="human", typed_by=typed)
    for f in ("success_criteria", "constraints", "non_goals"):
        if parsed[f]:
            st.set_protected(f, parsed[f], by="human", typed_by=typed)
    stack: list[tuple[int, str]] = []          # (indent, item_id)
    for entry in parsed["checklist"]:
        text, indent = entry["text"], entry["indent"]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        ev = st.propose(text, by="human", parent=parent)
        st.accept(ev["item_id"], by="human")
        stack.append((indent, ev["item_id"]))
    print(f"\n  mission set for {short_id(sid)}\n")
    # Start/join the board BEFORE showing: otherwise the mission prints
    # "board: not running" and the next line says it just started one.
    _announce_board(a)
    return cmd_show(a)


def _announce_board(a) -> None:
    """Every session shares one board; joining it is the default."""
    if getattr(a, "no_board", False):
        return
    existing = board_running()
    url = ensure_board(getattr(a, "port", 8976))
    if not url:
        print("  (could not start the board — run `mission board` yourself)\n")
        return
    print(f"  board: {url}"
          f"{'  (joined the one already running)' if existing else '  (started it)'}\n")


def cmd_show(a) -> int:
    from . import missions as M
    if not getattr(a, "on", None) and not getattr(a, "session", None):
        sid = current_session_id()
        if sid and not M.attachments().get(sid) and M.all_missions():
            # No ceremony: the common case for an unattached session is that a
            # goal for it already exists, so say which and how -- rather than
            # "no mission for this session", which reads as "start over".
            print(f"\n  {short_id(sid)} is not attached to a goal yet.\n")
            for mid, st in M.all_missions()[:6]:
                m = st.load()
                if m and not m.archived:
                    print(f"    mission attach {mid}")
                    print(f"      {(m.objective or '')[:70]}")
            print("\n  or `mission init` to write a new one.\n")
            return 1
    sid = _resolve(a)
    m = _store(sid).load() if sid else None
    if not m:
        print("  no mission for this session. `mission init` to write one.")
        return 1
    tp = transcript_for(sid)
    act = activity(tp) if tp else None

    if m.name:
        print(f"\n  {m.name}")
        print(f"  {m.objective}\n")
    else:
        print(f"\n  {m.objective}\n")
    if not m.typed_by_human:
        print("  ⚠ an agent transcribed this goal — `mission why objective` for"
              " the record,\n    `mission set objective \"...\"` to make it "
              "yours.\n")
    if m.success_criteria:
        print("  DONE WHEN")
        for c in m.success_criteria:
            print(f"    · {c}")
    if m.constraints:
        print("\n  CONSTRAINTS")
        for c in m.constraints:
            print(f"    · {c}")
    if m.non_goals:
        print("\n  NOT DOING")
        for c in m.non_goals:
            print(f"    · {c}")
    if m.checklist:
        print(f"\n  PLAN  {m.done_count}/{m.total_count}")
        show_all = getattr(a, "all", False)
        roots = m.tree()
        _print_tree(roots, show_all=show_all, kids=children_of(sid))
        # Every finished leaf is folded away -- itself, or under a finished
        # branch -- so the count is done_count. Counting only the finished
        # ROOTS said "1 finished" while two were off the screen.
        hidden = m.done_count
        if hidden and not show_all:
            print(f"    ({hidden} finished, hidden — `mission show --all`)")
        if m.unaccepted:
            print(f"\n    {len(m.unaccepted)} proposed, awaiting your accept")
    if act:
        print(f"\n  MEASURED SO FAR")
        print(f"    {act.calls} tool calls · {len(act.files)} files changed · "
              f"{act.tests} test runs · {act.failures} failed calls")
        if act.files:
            top = ", ".join(f"{k} {v}x" for k, v in list(act.files.items())[:4])
            print(f"    {top}")
    rec = board_running()
    if rec:
        print(f"  board: http://127.0.0.1:{rec['port']}")
    else:
        print("  board: not running — `mission board` to open it")
    print()
    return 0


def cmd_set(a) -> int:
    """Change a protected field. Goals move; a mission you cannot edit is a
    mission you abandon and rewrite from scratch."""
    from .store import PROTECTED_FIELDS
    if not _human_gate(f"the {a.field}"):
        return 1
    sid, st = _target(a, "set")
    if st is None:
        return 1
    if st.load() is None:
        print("  no mission for this session — `mission init` first")
        return 1
    field = a.field.replace("-", "_")
    if field not in PROTECTED_FIELDS:
        print(f"  {a.field} is not one of: {', '.join(sorted(PROTECTED_FIELDS))}")
        return 1
    value = a.value if field in ("name", "objective") else list(a.value_list or a.value.split("|"))
    st.set_protected(field, value, by="human",
                     typed_by="human" if _at_a_keyboard() else "agent")
    print(f"  {field} updated")
    return cmd_show(a)


def cmd_import(a) -> int:
    """Land a plan somebody else's planner wrote, as proposals.

    Diffs on re-import: what is new goes up, what already exists is left alone,
    and what has vanished from the file is REPORTED, never removed. Removing is
    the human's call, and a tool that silently prunes a plan because a file
    changed is one you stop trusting with the plan.
    """
    from .importer import norm, parse
    sid = _resolve(a)
    st = _store(sid)
    m = st.load()
    if m is None:
        print("  no mission for this session — `mission init` first")
        return 1
    p = Path(a.file)
    if not p.exists():
        print(f"  no such file: {a.file}")
        return 1
    rows = parse(p.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        print(f"  nothing plan-shaped in {a.file} — headings and `- [ ]` bullets")
        return 1
    if len(rows) > a.max:
        print(f"  {len(rows)} items in {a.file}; the cap is {a.max}. "
              f"Import a section of it, or raise --max.")
        return 1

    have = {norm(d["text"]): d["id"] for d in m.checklist}
    stack: list[tuple[int, str | None]] = []      # (depth, item_id)
    added, skipped = [], 0
    for r in rows:
        while stack and stack[-1][0] >= r.depth:
            stack.pop()
        parent = stack[-1][1] if stack else a.under
        key = norm(r.text)
        if key in have:
            skipped += 1
            stack.append((r.depth, have[key]))
            continue
        ev = st.propose(r.text, by="agent", parent=parent)
        have[key] = ev["item_id"]
        added.append((ev["item_id"], r))
        stack.append((r.depth, ev["item_id"]))

    # What the file no longer mentions -- but only within the subtree this
    # import targets. Compared against the WHOLE plan, `--under` reported every
    # unrelated item in the mission as missing from a file that was never
    # supposed to contain them.
    scope = {d["id"] for d in m.checklist}
    if a.under:
        scope, growing = set(), {a.under}
        while growing:
            scope |= growing
            growing = {d["id"] for d in m.checklist
                       if d.get("parent") in scope and d["id"] not in scope}
    seen = {norm(r.text) for r in rows}
    gone = [d["text"] for d in m.checklist
            if d["id"] in scope and not d["done"]
            and norm(d["text"]) not in seen] if a.strict else []

    print(f"\n  {p.name}: {len(added)} proposed, {skipped} already in the plan")
    for iid, r in added:
        print(f"    [?] {iid}  {'  ' * r.depth}{r.text}")
    ticked = [iid for iid, r in added if r.checked]
    if ticked:
        # The file says these are finished. The file is not you.
        was = "was" if len(ticked) == 1 else "were"
        print(f"\n  {len(ticked)} {was} ticked in the source but imported "
              f"unticked — ticking is yours:\n    mission done {' '.join(ticked)}")
    if gone:
        print(f"\n  in the plan but not in {p.name} (not removed):")
        for t in gone[:8]:
            print(f"    · {t}")
    if added:
        print(f"\n  accept them with:\n    mission accept "
              f"{' '.join(i for i, _ in added)}\n")
    return 0


def _slugify(text: str, limit: int = 24) -> str:
    """A short, readable id fragment -- cut at a word boundary.

    Third time in this codebase that a plain slice cut a word in half
    ("generate-a-seeded-two-mo"), after the mission title and the session id.
    A truncated word reads as corruption; a shorter whole word does not.
    """
    import re
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) <= limit:
        return s or "task"
    cut = s[:limit]
    if "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut.strip("-") or "task"


def cmd_delegate(a) -> int:
    """Give one accepted item its own small mission, for a subagent to run.

    A subagent has no session id of its own -- it runs inside its parent's --
    so without this its work is invisible on the board and it has to be handed
    a session id invented by hand. It also does not need the whole mission: it
    needs one item, and the limits that still apply.

    The agent is allowed to run this. It authors nothing: the objective is
    copied verbatim from an item the human already accepted, and constraints
    and non-goals are inherited unchanged. Refusing would only push a subagent
    into working with no recorded goal at all.
    """
    sid = _resolve(a)
    st = _store(sid)
    m = st.load()
    if m is None:
        print("  no mission for this session — `mission init` first")
        return 1
    item = next((Item(**d) for d in m.checklist if d["id"] == a.item_id), None)
    if item is None:
        print(f"  no item {a.item_id!r} in this plan — `mission` to see the ids")
        return 1
    if not item.accepted:
        # Delegating an unaccepted proposal would let the agent turn its own
        # suggestion into a goal, which is the one move the whole design exists
        # to prevent.
        print(f"  {a.item_id} is still a proposal. Accept it first:\n"
              f"    mission accept {a.item_id}")
        return 1

    child = f"{short_id(sid)}.{_slugify(a.to or item.text)}"
    cst = _store(child)
    if cst.load() is not None:
        print(f"  {child} already has a mission — `mission show --session {child}`")
        return 1
    cst.create(child, m.cwd or str(Path(a.cwd).resolve()), item.text,
               by="agent" if not _at_a_keyboard() else "human",
               parent_session=sid, parent_item=item.id)
    # Same typed_by as the create above. These three writes hardcoded
    # by="human" with no typed_by, so `why name` on a delegated child reported
    # a plain human even when an agent ran the whole command -- the exact gap
    # the init fix closed, left open one function over.
    typed = "human" if _at_a_keyboard() else "agent"
    cst.set_protected("name", (a.to or item.text)[:60], by="human", typed_by=typed)
    # Criteria are the PARENT's and stay there: a slice of the work does not
    # get to decide the whole mission is finished. Limits do carry, because a
    # constraint that stops applying to a subagent is not a constraint.
    for f in ("constraints", "non_goals"):
        if getattr(m, f):
            cst.set_protected(f, getattr(m, f), by="human", typed_by=typed)

    print(f"\n  delegated {item.id} → session {child}\n"
          f"  Hand the subagent this, verbatim:\n\n"
          f"    Your goal is recorded. Read it first:\n"
          f"      mission show --session {child}\n"
          f"    Record work you think is missing with:\n"
          f"      mission propose \"...\" --session {child}\n"
          f"    You cannot tick items or change the goal; that is the human's.\n")
    return 0


def cmd_detour(a) -> int:
    """Declare a side quest. The agent may do this freely.

    Drifting into a subgoal is normal work, not a failure. The failure is
    coming back up with nothing that says what the bigger goal was. So this
    records the detour instead of guessing at one -- five attempts to DETECT
    drift all failed (F34), and the honest residue is that going off is
    something you say, not something a detector infers.
    """
    sid = _resolve(a)
    st = _store(sid)
    st.detour(a.label, by="human" if _at_a_keyboard() else "agent")
    depth = len(st.load().detours)
    print(f"  detour ({depth} deep): {a.label}")
    print("  `mission return` when you climb back up")
    return 0


def cmd_return(a) -> int:
    """Close the innermost detour, and replay the goal you left behind."""
    sid = _resolve(a)
    st = _store(sid)
    m = st.load()
    if m is None:
        raise NoMissionError(sid)
    if not m.detours:
        print("  no detour open — nothing to return from")
        return 0
    left = m.detours[-1]
    st.ret(by="human" if _at_a_keyboard() else "agent")
    m = st.load()

    # The point of the whole command: the guards you set at the start, replayed
    # at the moment you come back, rather than trusted to memory.
    print(f"\n  back from: {left}\n")
    print(f"  {m.title}")
    print(f"  {m.objective}\n")
    if m.detours:
        print(f"  still inside: {m.detours[-1]} ({len(m.detours)} deep)\n")
    for label, vals in (("DONE WHEN", m.success_criteria),
                        ("CONSTRAINTS", m.constraints),
                        ("NOT DOING", m.non_goals)):
        if vals:
            print(f"  {label}")
            for v in vals:
                print(f"    · {v}")
            print()
    nxt = [i for i in m.leaves if i.accepted and not i.done]
    if nxt:
        print(f"  NEXT\n    [ ] {nxt[0].id}  {nxt[0].text}\n")
    return 0


def cmd_whereami(a) -> int:
    """One line, for a statusline. Never fails, never blocks.

    The moment you have drifted is exactly the moment you do not think to run
    `mission show`. So the goal has to come to the person rather than wait to
    be asked for, and a statusline is the only surface that is always there.

    Constraints that follow from that: one line, no colour, fast, and exit 0
    on every path including "no mission" and "the log is corrupt". A statusline
    that can fail is a statusline that gets removed.
    """
    if getattr(a, "full", False) and not getattr(a, "on", None):
        # The re-anchor hook runs here. For a session with no goal yet it used
        # to say "mission init", which creates a SECOND goal for work that
        # already has one -- the duplicate-mission failure, recommended by the
        # tool itself. Offer what exists instead.
        from . import missions as M
        sid_now = current_session_id()
        if sid_now and not M.attachments().get(sid_now):
            live = [(mid, st.load()) for mid, st in M.all_missions()]
            live = [(mid, m) for mid, m in live if m and not m.archived]
            if live:
                print("NOT ATTACHED: this session has no goal yet. Ask which one, "
                      "then run `mission attach <name>`. Existing goals:")
                for mid, m in live[:6]:
                    print(f"  {mid} — {(m.objective or '')[:64]}")
                return 0
    try:
        sid = _resolve(a)
    except NoSessionError as e:
        # No mission for this directory is a STATE, not a failure: "no mission
        # — mission init" is the correct statusline for it. Only ambiguity
        # deserves silence, because naming one of several would be a guess and
        # a statusline has no room to ask.
        print("" if e.candidates else "no mission — mission init")
        return 0
    except Exception:
        print("")
        return 0
    try:
        m = _store(sid).load()
    except Exception:
        print("")                       # a corrupt log: saying anything is wrong
        return 0
    if m is None:
        print("no mission — mission init")
        return 0

    if getattr(a, "full", False):
        # Ten lines for a hook to inject: the goal and the guards, not the plan.
        print(f"MISSION: {m.title} — {m.objective}")
        for label, vals in (("DONE WHEN", m.success_criteria),
                            ("CONSTRAINTS", m.constraints),
                            ("NOT DOING", m.non_goals)):
            for v in vals:
                print(f"{label}: {v}")
        nxt = [i for i in m.leaves if i.accepted and not i.done]
        for i in nxt[:3]:
            print(f"NEXT: [{i.id}] {i.text}")
        if m.detours:
            print(f"ON DETOUR: {m.detours[-1]} ({len(m.detours)} deep)")
        if m.unaccepted:
            print(f"AWAITING THE HUMAN: {len(m.unaccepted)} proposal(s)")
        return 0

    bits = [m.title if len(m.title) <= 40 else m.title[:37].rsplit(" ", 1)[0] + "…"]
    if m.total_count:
        bits.append(f"{m.done_count}/{m.total_count}")
    if m.detours:
        top = m.detours[-1]
        deep = f" ({len(m.detours)} deep)" if len(m.detours) > 1 else ""
        bits.append(f"detour: {top}{deep}")
    n = len(m.unaccepted)
    if n:
        bits.append(f"{n} proposal{'' if n == 1 else 's'} waiting")
    # The link, re-read every render. A port hop updates it by construction,
    # which is the whole reason it is not printed once and remembered.
    rec = board_running()
    if rec:
        bits.append(f"http://127.0.0.1:{rec['port']}")
    print(" · ".join(bits))
    return 0


def cmd_observe(a) -> int:
    """Record evidence, a decision, or a note. The one write needing no gate.

    The whole OBSERVABLE level was unreachable from the CLI: `store.observe()`
    existed and was tested, the README and the refusal message both told the
    agent it could observe, and there was no subcommand. So the level the
    design most encourages the agent to use went nowhere.
    """
    sid = _resolve(a)
    st = _store(sid)
    st.observe(a.field, a.text, by="human" if _at_a_keyboard() else "agent")
    print(f"  recorded under {a.field}")
    print(f"  → {_where(sid)}")
    return 0


def cmd_pending(a) -> int:
    """Everything waiting on you, with the command to clear it."""
    sid = _resolve(a)
    m = _store(sid).load()
    if m is None:
        raise NoMissionError(sid)
    waiting = m.unaccepted
    if not waiting:
        print("\n  nothing awaiting you here.\n")
        return 0
    print(f"\n  {len(waiting)} awaiting you in {m.title}\n")
    for i in waiting:
        print(f"    [?] {i.id}  {i.text}")
    print(f"\n    mission accept --pending"
          f"{'' if not a.session else ' --session ' + a.session}\n")
    return 0


def cmd_why(a) -> int:
    """When did this field change, and to what. The log already knew."""
    import datetime as _dt
    sid = _resolve(a)
    st = _store(sid)
    if st.load() is None:
        print("  no mission for this session")
        return 1
    field = a.field.replace("-", "_")
    events = [e for e in st.why(field) if e.get("kind") != "created"
              or field == "objective"]
    if not events:
        print(f"  nothing has ever set {a.field}")
        return 1
    print()
    for e in events:
        when = _dt.datetime.fromtimestamp(e["at"]).strftime("%Y-%m-%d %H:%M")
        v = e.get("value", e.get("objective", ""))
        v = " | ".join(v) if isinstance(v, list) else str(v)
        who = e.get("typed_by", e["by"])
        mark = e["by"] if who == e["by"] else f"{e['by']} (typed by {who})"
        print(f"  {when}  {mark:<24} {v}")
    print()
    return 0


def _print_tree(nodes, depth: int = 0, prefix: str = "",
                show_all: bool = False, kids: dict | None = None) -> None:
    """Indented, with a branch's progress rolled up from its leaves.

    Finished work is folded away by default — what is left is what you act on
    — and `--all` brings it back. Nothing is deleted; it is just not printed.
    """
    if not show_all:
        nodes = [n for n in nodes if not n.complete]
    for n, last in ((n, i == len(nodes) - 1) for i, n in enumerate(nodes)):
        item = n.item
        mark = "x" if (n.complete if n.children else item.done) else (
            " " if item.accepted else "?")
        elbow = "" if depth == 0 else ("└─ " if last else "├─ ")
        roll = f"   {n.done_count}/{n.total}" if n.children else ""
        # A delegated item is being worked on in another session. Without this
        # the parent plan shows it as untouched while a subagent is mid-way
        # through it.
        child = (kids or {}).get(item.id)
        tag = (f"   → {child.session_id} {child.done_count}/{child.total_count}"
               if child else "")
        print(f"    {prefix}{elbow}[{mark}] {item.id}  {item.text}{roll}{tag}")
        if n.children:
            _print_tree(n.children, depth + 1,
                        prefix + ("" if depth == 0 else ("   " if last else "│  ")),
                        show_all=show_all, kids=kids)


def _where(sid: str) -> str:
    """Which mission a write landed on -- name AND objective.

    A name echo is circular after a rename: the mis-targeted write renamed the
    mission it hit, so every later target line displayed the right name over
    the wrong id and the error confirmed itself. The objective cannot be
    renamed by the same accident, so it is what breaks the loop.
    """
    m = _store(sid).load()
    if not m:
        return short_id(sid)
    first = (m.objective or "").strip().split("\n")[0]
    return f"{short_id(sid)}  {m.title}\n     goal: {first[:72]}"


def cmd_add(a) -> int:
    # `add` = propose + accept in one step, both as the human. Ungated, it was
    # the whole authority model in one command: an agent could write an
    # ACCEPTED item that `mission why` then attributed to Jonathan. The tty
    # gate closed set/accept/done/remove and missed the one that does two of
    # them at once.
    if not _human_gate("adding an agreed item"):
        return 1
    sid, st = _target(a, "add")
    if st is None:
        return 1
    ev = st.propose(a.text, by="human", parent=a.under)
    st.accept(ev["item_id"], by="human")
    where = f" under {a.under}" if a.under else ""
    # Name the mission it landed on. Two items meant for Tripnom turned up on
    # the career card and nobody noticed, because the only feedback was the id
    # and the text -- neither of which says WHERE it went.
    print(f"  added {ev['item_id']}{where}  {a.text}")
    print(f"  → {_where(sid)}")
    return 0


def cmd_propose(a) -> int:
    # A REVIEWING session may put an item on a WORKING session's plan. The
    # authority model needs nothing new: a proposal is inert until a person
    # accepts it, so a peer proposing is no more dangerous than the worker
    # proposing into its own mission. Without it, a second session's findings
    # reach the plan only by the human retyping them, which is the friction
    # that stranded 61 of 152 proposals.
    #
    # That used to be `--into`, a second router on the one command that already
    # had three. It is `--on` now; --into still works and is hidden.
    hit = _mission_target(a)
    if hit:
        sid, via = hit
    else:
        sid, via = _resolve_with_path(a)
    a._via = via
    ev = _store(sid, a).propose(a.text, by="agent", parent=a.under,
                             from_session=current_session_id() or "")
    print(f"  proposed {ev['item_id']} — inert until you `mission accept {ev['item_id']}`")
    print(f"  → {_where(sid)}")
    return 0


def _subtree(m, root: str) -> list[str]:
    """Every id under `root`, including it."""
    ids, growing = set(), {root}
    while growing:
        ids |= growing
        growing = {d["id"] for d in m.checklist
                   if d.get("parent") in ids and d["id"] not in ids}
    return [d["id"] for d in m.checklist if d["id"] in ids]


def _select(a, m, want: str) -> list[str] | None:
    """Turn --all / --under into a set of ids, or None if ids were given.

    Measured across five real missions: 152 proposals, 91 accepted, 61 still
    pending -- 40%. Accepting costs a person retyping an 8-character id per
    item, and the worst instance was a 25-id command that failed and had to be
    redone by hand eight minutes later. Naming the set is the fix. Who may run
    it does not change: this is still gated and still denied to the agent.
    """
    # `--all` is shared with `show --all` on the common parser, so `mission
    # done --all` is a plausible typo for "show me everything" -- and it would
    # tick the whole plan. Accepting everything is cheap to undo; declaring all
    # the work finished is a judgement. So --all selects only for accept, and
    # ticking a set requires naming the subtree.
    wide = getattr(a, "all", False) and want == "accept"
    pool = _subtree(m, a.under) if getattr(a, "under", None) else \
        ([i.id for i in m.items] if wide else None)
    if pool is None:
        return None
    by_id = {i.id: i for i in m.items}
    if want == "accept":
        return [i for i in pool if not by_id[i].accepted]
    if want == "done":
        # Only leaves are work, and only agreed work can be ticked.
        leaves = {i.id for i in m.leaves}
        return [i for i in pool
                if i in leaves and by_id[i].accepted and not by_id[i].done]
    return pool          # remove takes whatever is in scope


def _apply(a, verb: str, fn, want: str = "") -> int:
    """Run a per-item action over every id given, and report each one.

    Several ids at once because the alternative is what actually happens: the
    agent proposes six items, ticking them is six commands, and the plan stops
    being maintained. An unknown id is reported and the rest still run -- a
    typo in the fourth id must not silently drop the first three.
    """
    sid, st = _target(a, verb)
    if st is None:
        return 1
    m = st.load()
    if m is None:
        raise NoMissionError("")
    chosen = _select(a, m, want) if want else None
    ids = list(a.item_id) if chosen is None else chosen
    if not ids:
        print(f"  nothing to {verb}" if chosen is not None
              else "  no ids given — pass some, or --all")
        return 0
    bad = 0
    for iid in ids:
        try:
            fn(st, iid)
        except NoSuchItemError:
            print(f"  no item {iid!r} in this plan — `mission` to see the ids")
            bad = 1
        except ProtectedFieldError as e:
            print(f"  {e}")
            bad = 1
        else:
            print(f"  {verb} {iid}")
    return bad


def cmd_accept(a) -> int:
    if not _human_gate("accepting a proposal"):
        return 1
    return _apply(a, "accepted", lambda st, i: st.accept(i, by="human"),
                  want="accept")


def cmd_done(a) -> int:
    if not _human_gate("marking work done"):
        return 1
    rc = _apply(a, "done", lambda st, i: st.complete(i, by="human"),
                want="done")
    return rc or cmd_show(a)


def cmd_attach(a) -> int:
    """Point this session at a mission, by name."""
    from . import missions as M
    sid = a.session or current_session_id()
    if not sid:
        print("  no session id — run this inside Claude Code, or pass --session")
        return 1
    mid = M.find(a.name)
    M.attach(sid, mid, by="human" if _at_a_keyboard() else "agent")
    m = MissionStore(M.missions_root() / mid).load()
    print(f"\n  {short_id(sid)} → {mid}")
    print(f"  {m.title}\n  {(m.objective or '')[:76]}\n")
    return 0


def cmd_archive(a) -> int:
    """Take a finished goal off the board. Nothing is deleted."""
    from . import missions as M
    if not _human_gate("archiving a goal"):
        return 1
    mid = M.find(a.name)
    st = MissionStore(M.missions_root() / mid)
    st.archive(by="human", undo=a.undo)
    print(f"  {mid} {'is back on the board' if a.undo else 'archived'}"
          f" — the log is untouched")
    return 0


def cmd_missions(a) -> int:
    """Every goal, with the sessions that served it."""
    from . import missions as M
    rows = M.all_missions()
    if not rows:
        print("\n  no missions yet — `mission init`, or `mission migrate`\n")
        return 1
    print()
    shown = 0
    for mid, st in rows:
        m = st.load()
        if m is None or (m.archived and not a.all):
            continue
        shown += 1
        sess = M.sessions_of(mid)
        print(f"  {mid:<32} {m.done_count}/{m.total_count}"
              f"{'  ⚑' + str(len(m.unaccepted)) if m.unaccepted else ''}")
        print(f"    {(m.objective or '')[:74]}")
        print(f"    {len(sess)} session(s): "
              f"{', '.join(short_id(x) for x in sess[:4])}\n")
    return 0


def cmd_migrate(a) -> int:
    """Lift session-keyed stores into named missions. Safe to re-run."""
    from . import missions as M
    rows = M.migrate(dry_run=a.dry_run)
    if not rows:
        print("\n  nothing to migrate — everything is already a named mission.\n")
        return 0
    print(f"\n  {'would lift' if a.dry_run else 'lifted'} {len(rows)}:\n")
    for r in rows:
        print(f"    {short_id(r['session']):<12} → {r['mission']:<32} "
              f"{r['events']:>3} events")
    print(f"\n  the old stores are left untouched.\n")
    return 0


def cmd_doctor(a) -> int:
    """What is wrong with the missions, as opposed to the installation."""
    from .doctor import findings
    rows = findings()
    if not rows:
        print("\n  nothing wrong with any mission log.\n")
        return 0
    order = {"serious": 0, "note": 1, "todo": 2}
    mark = {"serious": "⚠", "note": "·", "todo": "→"}
    print()
    for r in sorted(rows, key=lambda r: order[r["level"]]):
        print(f"  {mark[r['level']]} {short_id(r['sid']):<10} {r['what']}")
        print(f"      {r['detail']}")
    bad = sum(1 for r in rows if r["level"] == "serious")
    print(f"\n  {bad} serious, {len(rows) - bad} to read.\n")
    return 1 if bad else 0


def cmd_help(a) -> int:
    """Usage for one command, without running it.

    Claude Code permission patterns are prefix matches with no negation, so
    `Bash(mission accept:*)` also blocks `mission accept --help`. An agent
    composing a paste-ready command for the human therefore cannot read the
    flags of the commands it is most likely to be composing. Carving a hole in
    the deny rule would weaken the one gate that is a real boundary, so this
    is a separate read-only command that writes nothing.
    """
    parser = _build()
    sub = next(x for x in parser._actions
               if isinstance(x, argparse._SubParsersAction))
    if not a.command:
        parser.print_help()
        return 0
    target = sub.choices.get(a.command)
    if target is None:
        print(f"  no command {a.command!r}. Known: "
              f"{', '.join(sorted(sub.choices))}")
        return 1
    target.print_help()
    return 0


def cmd_version(a) -> int:
    """Which build of the tool is this session running?

    Long sessions hold a picture of the tool from whenever they last read it,
    and this one changed under three of them in a day -- one recommended
    `init --force` to fix an objective, which would have dropped the plan,
    because `mission set` did not exist when that session last looked.
    """
    import subprocess
    from . import __version__
    here = Path(__file__).resolve().parent.parent
    rev = date = ""
    try:
        rev = subprocess.run(["git", "-C", str(here), "log", "-1", "--format=%h"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        date = subprocess.run(["git", "-C", str(here), "log", "-1", "--format=%cd",
                               "--date=format:%Y-%m-%d %H:%M"],
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        pass
    print(f"\n  mission {__version__}" + (f"  ({rev}, {date})" if rev else ""))
    print(f"  {here}")
    print(f"\n  commands: {', '.join(sorted(_SUBCOMMANDS))}")
    print("\n  If a command here is missing from what you remember, your picture"
          "\n  of this tool is older than the tool.\n")
    return 0


def _surfaces_shared(a):
    from . import setup_surfaces as S
    return S.status(getattr(a, "settings", None), getattr(a, "dest", None))


def _surfaces(a) -> list[tuple[str, bool, str]]:
    """Each surface, and whether it is actually installed on THIS machine."""
    p, data = _read_settings(a)
    data = data or {}
    dest = (Path(a.dest).expanduser() if getattr(a, "dest", None)
            else Path.home() / ".claude" / "commands")
    deny = data.get("permissions", {}).get("deny", [])
    sl = data.get("statusLine") or {}
    sl_cmd = sl.get("command", "") if isinstance(sl, dict) else str(sl)
    wrapper = _home_dir() / "statusline.sh"
    sl_ok = "mission whereami" in sl_cmd or (
        wrapper.exists() and str(wrapper) in sl_cmd)
    hooks = json.dumps(data.get("hooks", {}).get("SessionStart", []))
    return [
        ("slash command", (dest / "mission.md").exists(), str(dest / "mission.md")),
        ("deny rules", all(r in deny for r in DENY_RULES),
         f"{sum(r in deny for r in DENY_RULES)}/{len(DENY_RULES)} in {p}"),
        ("statusline", bool(sl_ok), sl_cmd[:60] or "not set"),
        ("re-anchor hook", "mission whereami --full" in hooks,
         "SessionStart"),
        ("board bookmark", (_home_dir() / "board.html").exists(),
         str(_home_dir() / "board.html")),
    ]


def cmd_check(a) -> int:
    """Which surfaces are live here. Exit 1 if any is missing, so it scripts.

    The contract this exists to make true: re-running `mission setup` is always
    the complete fix, and there is never a second instruction to follow.
    """
    rows = _surfaces_shared(a)
    print()
    for r in rows:
        # "missing" and "outdated" are different problems with the same fix,
        # and calling an outdated copy "missing" reads as a lie when the file
        # is plainly there.
        state = r.get("state") or ("installed" if r["ok"] else "missing")
        print(f"  {'✓' if r['ok'] else '·'} {r['name']:<16} {state:<10} "
              f"{r['detail']}")
    missing = [r["name"] for r in rows if not r["ok"]]
    if missing:
        print(f"\n  {len(missing)} missing — `mission setup` in a terminal "
              f"installs everything.\n")
        return 1
    print("\n  everything is installed.\n")
    return 0


def cmd_setup(a) -> int:
    """Install the /mission slash command so sessions run it, not read it."""
    if getattr(a, "check", False):
        return cmd_check(a)
    src = Path(__file__).resolve().parent.parent / "commands" / "mission.md"
    if not src.exists():
        print("  commands/mission.md not found in the package")
        return 1
    dest_dir = Path(a.dest).expanduser() if a.dest else Path.home() / ".claude" / "commands"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "mission.md"
    if dest.exists() and not a.force:
        # NOT a return. One surface already being present used to abort the
        # whole run, so `mission setup` printed an instruction and installed
        # nothing else -- which is precisely the failure this command exists
        # to prevent. Every surface is attempted on every run.
        current = dest.read_text(encoding="utf-8")
        same = current == src.read_text(encoding="utf-8")
        print(f"\n  slash command already installed"
              f"{'' if same else ' (older version — --force to update)'}")
    else:
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n  installed {dest}")
    print("  type /mission in any Claude Code session\n")
    # One implementation, two front-ends -- which C10 claimed and this did not
    # honour: the board called setup_surfaces while the CLI kept its own copy.
    # They drifted, and the CLI's copy wrapped its own wrapper because only it
    # was missing the self-reference check.
    from . import setup_surfaces as S
    if not _at_a_keyboard():
        rows = [r for r in S.status(getattr(a, "settings", None),
                                    getattr(a, "dest", None)) if not r["ok"]]
        if rows:
            print("  these are not installed:\n")
            for r in rows:
                print(f"    {r['name']}")
            print("\n  run `mission setup` yourself in a terminal.\n")
        return 0
    for name in S.SURFACES:
        if name == "slash command":
            continue                       # handled above, honours --force
        out = S.install(name, getattr(a, "settings", None),
                        getattr(a, "dest", None))
        if out["applied"]:
            for line in out["applied"]:
                print(f"  → {line}")
            if out["backup"]:
                print(f"    backup: {out['backup']}")
        else:
            print(f"  {name}: {out['why'] or 'nothing to do'}")
    print()
    return 0


def _settings_path(a) -> Path:
    return (Path(a.settings).expanduser() if getattr(a, "settings", None)
            else Path.home() / ".claude" / "settings.json")


def _read_settings(a):
    p = _settings_path(a)
    if not p.exists():
        return p, None
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return p, None


def _write_settings(p: Path, data: dict) -> Path:
    backup = p.with_suffix(f".json.bak-mission-{int(time.time())}")
    backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return backup


def _home_dir() -> Path:
    import os
    h = Path(os.environ.get("AGENT_MISSION_HOME", Path.home() / ".agent-mission"))
    h.mkdir(parents=True, exist_ok=True)
    return h


STATUSLINE = {"type": "command", "command": "mission whereami"}


def _offer_statusline(a) -> None:
    """Put the goal on screen permanently -- the highest-leverage surface.

    The moment you have drifted is exactly the moment you do not think to run
    `mission show`, so the goal has to arrive without being asked for.

    Never overwrites: a statusline someone already configured is theirs, and
    replacing it silently would be the same class of move this whole tool
    exists to prevent.
    """
    if getattr(a, "no_statusline", False):
        return
    p, data = _read_settings(a)
    if data is None:
        return
    current = data.get("statusLine")
    if current == STATUSLINE:
        print("  statusline already runs `mission whereami`\n")
        return
    if current:
        inner = current.get("command") if isinstance(current, dict) else str(current)
        if "mission whereami" in (inner or ""):
            print("  statusline already includes `mission whereami`\n")
            return
        if not _at_a_keyboard():
            print("  you already have a statusline. `mission setup` in a terminal"
                  "\n  offers to chain `mission whereami` onto it.\n")
            return
        # Compose, do not instruct. A surface delivered as "here is what to
        # type" does not get installed -- C2 shipped as printed instructions
        # and the statusline stayed exactly as it was for two days.
        wrapper = _home_dir() / "statusline.sh"
        wrapper.write_text(
            "#!/bin/sh\n"
            "# Written by `mission setup`. Your original statusline command is\n"
            "# preserved verbatim below; `mission whereami` is appended to it.\n"
            "# Statuslines are ONE line, so both halves are flattened.\n"
            "input=$(cat)\n"
            f"mine=$(printf '%s' \"$input\" | {inner} 2>/dev/null | tr '\\n' ' ')\n"
            "goal=$(mission whereami 2>/dev/null)\n"
            "if [ -n \"$mine\" ] && [ -n \"$goal\" ]; then\n"
            "  printf '%s · %s' \"$mine\" \"$goal\"\n"
            "else\n"
            "  printf '%s%s' \"$mine\" \"$goal\"\n"
            "fi\n", encoding="utf-8")
        wrapper.chmod(0o755)
        data["statusLine"] = {"type": "command", "command": str(wrapper)}
        b = _write_settings(p, data)
        print(f"  statusline now runs your command AND `mission whereami`")
        print(f"  wrapper: {wrapper}   (your original is inside it, verbatim)")
        print(f"  WRITTEN TO {p}  (backup: {b.name})\n")
        return
    if not _at_a_keyboard():
        print("  no statusline set. `mission setup` in a terminal offers to add"
              "\n  one that runs `mission whereami`.\n")
        return
    data["statusLine"] = STATUSLINE
    b = _write_settings(p, data)
    print(f"  statusline now runs `mission whereami`  (backup: {b.name})")
    print("  the goal is on screen from here on, at zero keystrokes\n")


def _hook_cmd() -> str:
    return "mission whereami --full 2>/dev/null || true"


def _offer_hooks(a) -> None:
    """Re-anchor after compaction, which is where memory actually fails.

    The mission already SURVIVES compaction on disk. Nothing put it back in
    front of the agent afterwards, so it survived somewhere nobody looked.
    """
    if getattr(a, "no_hooks", False):
        return
    p, data = _read_settings(a)
    if data is None:
        return
    hooks = data.setdefault("hooks", {})
    start = hooks.setdefault("SessionStart", [])
    if any(_hook_cmd() in json.dumps(h) for h in start):
        print("  SessionStart hook already re-anchors the mission\n")
        return
    entry = {"hooks": [{"type": "command", "command": _hook_cmd(),
                        "timeout": 5}]}
    if not _at_a_keyboard():
        print("  a SessionStart hook would re-anchor the mission after a"
              "\n  compaction. `mission setup` in a terminal to add it.\n")
        return
    start.append(entry)                    # appended, never replacing yours
    b = _write_settings(p, data)
    print(f"  SessionStart hook added, alongside your {len(start) - 1} existing"
          f" one(s)  (backup: {b.name})")
    print("  after a compaction the agent gets the goal back, not just the"
          " transcript\n")


# The five commands only a person may run. Blocked at the harness, they never
# reach this code at all -- which is the point: the tty check lives inside the
# thing being protected, and a rule here is enforced by the thing that already
# holds the authority.
DENY_RULES = [f"Bash(mission {c}:*)" for c in
              ("set", "accept", "done", "remove", "add")]


def _install_deny_rules(a) -> int:
    """Ask Claude Code to refuse the human-only commands, agent-side.

    The tty check is a speed bump an agent can step over with one env var. A
    deny rule is not: the harness blocks the call before the CLI runs, so there
    is nothing inside the tool left to talk past.

    Costs the person nothing -- they still type these in their own terminal,
    and the agent keeps propose, delegate, observe, show and import.
    """
    if getattr(a, "no_permissions", False):
        print("  skipped the permission rules (--no-permissions)\n")
        return 0
    path = Path(a.settings).expanduser() if a.settings else \
        Path.home() / ".claude" / "settings.json"
    if not path.exists():
        print(f"  no {path} — add these to your settings yourself:\n")
        for r in DENY_RULES:
            print(f"    {r}")
        print()
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                       # never leave settings broken
        print(f"  could not read {path} ({e}) — add these yourself:")
        for r in DENY_RULES:
            print(f"    {r}")
        return 0
    perms = data.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    missing = [r for r in DENY_RULES if r not in deny]
    if not missing:
        print("  permission rules already in place\n")
        return 0
    # NOT gated on --force. That flag means "overwrite the command file", and
    # letting it also wave through a settings edit is how a narrow escape hatch
    # becomes a wide one: the first run of this as an agent, with --force,
    # wrote the rules it was supposed to refuse to write.
    if not _at_a_keyboard():
        # Editing the human's settings is exactly the kind of change an agent
        # should not make on its own -- and this file governs what agents may
        # do at all.
        print("  these four rules are missing from your settings:\n")
        for r in missing:
            print(f"    {r}")
        print("\n  run `mission setup` yourself in a terminal to add them.\n")
        return 0
    backup = path.with_suffix(f".json.bak-mission-{int(time.time())}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    deny.extend(missing)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  added {len(missing)} deny rule(s) to {path}")
    print(f"  backup: {backup.name}")
    print("  the agent can no longer run set/accept/done/remove at all;")
    print("  you still can, in your own terminal.\n")
    return 0


def cmd_remove(a) -> int:
    if not _human_gate("removing an item"):
        return 1
    rc = _apply(a, "removed", lambda st, i: st.remove(i, by="human"),
                want="remove")
    return rc or cmd_show(a)


def cmd_board(a) -> int:
    from .board import serve
    if a.stop:
        print("  stopped" if board_stop() else "  no board running")
        return 0
    if a.foreground:
        serve(a.port)
        return 0
    rec = board_running()
    url = ensure_board(a.port)
    if not url:
        print("  could not start the board")
        return 1
    print(f"\n  board: {url}"
          f"{'  (already running)' if rec else '  (started, runs in the background)'}")
    print("  `mission board --stop` to shut it down\n")
    if a.open:
        subprocess.run(["open", url], check=False)
    return 0


def _build() -> argparse.ArgumentParser:
    # Shared flags live on a parent so they work AFTER the subcommand too --
    # `mission show --session X` is what anyone actually types, and a
    # top-level-only flag rejects it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--on", metavar="GOAL",
                        help="which goal this is about, by name")
    # Provenance and disambiguation, not routing. `--on` is the one address a
    # person types; four targeting flags on one command was the complexity this
    # pass exists to remove, so these two stay working and stay out of --help.
    common.add_argument("--session", default=None, help=argparse.SUPPRESS)
    common.add_argument("--cwd", default=".", help=argparse.SUPPRESS)
    common.add_argument("--all", action="store_true",
                        help="show finished items too (hidden by default)")

    ap = argparse.ArgumentParser(prog="mission", description=__doc__,
                                 parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    i = sub.add_parser("init", parents=[common]); i.add_argument("--force", action="store_true")
    i.add_argument("--discard-plan", action="store_true",
                   help="with --force: really drop the existing checklist")
    i.add_argument("--blank", action="store_true", help="do not seed from the transcript")
    i.add_argument("--no-edit", action="store_true", help="skip the editor (for scripts)")
    i.add_argument("--from-file", default=None,
                   help="read the filled template from a file instead of an editor")
    i.add_argument("--no-board", action="store_true", help="do not open the board")
    i.add_argument("--port", type=int, default=8976)
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("show", parents=[common]); s.set_defaults(fn=cmd_show)
    ad = sub.add_parser("add", parents=[common])
    ad.add_argument("text")
    ad.add_argument("--under", default=None, metavar="ID",
                    help="nest this under another item")
    ad.set_defaults(fn=cmd_add)

    pr = sub.add_parser("propose", parents=[common])
    pr.add_argument("text")
    pr.add_argument("--under", default=None, metavar="ID")
    pr.add_argument("--into", metavar="GOAL", help=argparse.SUPPRESS)
    pr.set_defaults(fn=cmd_propose)
    ac = sub.add_parser("accept", parents=[common])
    ac.add_argument("item_id", nargs="*", metavar="ID")
    ac.add_argument("--pending", dest="all", action="store_true",
                    help="every proposal awaiting you — no ids needed")
    ac.add_argument("--under", metavar="ID",
                    help="everything in this subtree")
    ac.set_defaults(fn=cmd_accept)
    dn = sub.add_parser("done", parents=[common])
    dn.add_argument("item_id", nargs="*", metavar="ID")
    dn.add_argument("--under", metavar="ID",
                    help="everything in this subtree")
    dn.set_defaults(fn=cmd_done)
    su = sub.add_parser("setup", parents=[common],
                        help="install the /mission slash command")
    su.add_argument("--dest", default=None)
    su.add_argument("--force", action="store_true")
    su.add_argument("--check", action="store_true",
                    help="report which surfaces are installed; change nothing")
    su.add_argument("--no-statusline", action="store_true",
                    help="skip the statusline offer")
    su.add_argument("--no-hooks", action="store_true",
                    help="skip the SessionStart re-anchor hook")
    su.add_argument("--no-permissions", action="store_true",
                    help="skip the Claude Code deny rules")
    su.add_argument("--settings", default=None,
                    help="settings.json to edit (default ~/.claude/settings.json)")
    su.set_defaults(fn=cmd_setup)

    se = sub.add_parser("set", parents=[common],
                        help="change a protected field (name, objective, ...)")
    se.add_argument("field")
    se.add_argument("value", help="for list fields, separate with |")
    se.add_argument("--value-list", nargs="*", default=None)
    se.set_defaults(fn=cmd_set)

    rm = sub.add_parser("remove", parents=[common], help="drop an item (and its subtree)")
    rm.add_argument("item_id", nargs="+", metavar="ID")
    rm.set_defaults(fn=cmd_remove)

    im = sub.add_parser("import", parents=[common],
                        help="land a plan file as proposals (diffs on re-import)")
    im.add_argument("file")
    im.add_argument("--under", default=None, metavar="ID",
                    help="hang the imported tree under an existing item")
    im.add_argument("--max", type=int, default=60,
                    help="refuse a file with more items than this (default 60)")
    im.add_argument("--strict", action="store_true",
                    help="also report plan items missing from the file")
    im.set_defaults(fn=cmd_import)

    dg = sub.add_parser("delegate", parents=[common],
                        help="give one accepted item its own mission for a subagent")
    dg.add_argument("item_id", metavar="ID")
    dg.add_argument("--to", default=None, metavar="NAME",
                    help="short name for the child mission")
    dg.set_defaults(fn=cmd_delegate)

    ob = sub.add_parser("observe", parents=[common],
                        help="record evidence, a decision, or a note")
    ob.add_argument("field", choices=sorted(
        f for f, au in FIELD_AUTHORITY.items() if au is Authority.OBSERVABLE))
    ob.add_argument("text")
    ob.set_defaults(fn=cmd_observe)

    dt = sub.add_parser("detour", parents=[common],
                        help="declare a side quest (the agent may do this)")
    dt.add_argument("label")
    dt.set_defaults(fn=cmd_detour)

    rt = sub.add_parser("return", parents=[common],
                        help="close the detour and replay the goal you left")
    rt.set_defaults(fn=cmd_return)

    wa = sub.add_parser("whereami", parents=[common],
                        help="one line: goal, progress, detour, what is waiting")
    wa.add_argument("--full", action="store_true",
                    help="the goal and its guards, ~10 lines, for a hook")
    wa.set_defaults(fn=cmd_whereami)

    pd = sub.add_parser("pending", parents=[common],
                        help="what is awaiting your accept, and how to clear it")
    pd.set_defaults(fn=cmd_pending)

    wy = sub.add_parser("why", parents=[common],
                        help="when a protected field changed, and to what")
    wy.add_argument("field")
    wy.set_defaults(fn=cmd_why)

    bd = sub.add_parser("board", parents=[common])
    bd.add_argument("--port", type=int, default=8976)
    bd.add_argument("--open", action="store_true", help="open it in your browser")
    bd.add_argument("--stop", action="store_true")
    bd.add_argument("--foreground", action="store_true",
                    help=argparse.SUPPRESS)   # used by the spawner
    bd.set_defaults(fn=cmd_board)

    at = sub.add_parser("attach", parents=[common],
                        help="point this session at a mission, by name")
    at.add_argument("name")
    at.set_defaults(fn=cmd_attach)

    ar = sub.add_parser("archive", parents=[common],
                        help="take a finished goal off the board")
    ar.add_argument("name")
    ar.add_argument("--undo", action="store_true", help="put it back")
    ar.set_defaults(fn=cmd_archive)

    ms = sub.add_parser("missions", parents=[common],
                        help="every goal, with the sessions that served it")
    ms.set_defaults(fn=cmd_missions)

    mg = sub.add_parser("migrate", parents=[common],
                        help="lift session stores into named missions")
    mg.add_argument("--dry-run", action="store_true")
    mg.set_defaults(fn=cmd_migrate)

    dr = sub.add_parser("doctor", parents=[common],
                        help="what is wrong with the missions themselves")
    dr.set_defaults(fn=cmd_doctor)

    hp = sub.add_parser("help", parents=[common],
                        help="usage for one command, without running it")
    hp.add_argument("command", nargs="?")
    hp.set_defaults(fn=cmd_help)

    vs = sub.add_parser("version", parents=[common],
                        help="which build of mission is this, and what can it do")
    vs.set_defaults(fn=cmd_version)

    global _SUBCOMMANDS
    _SUBCOMMANDS = set(sub.choices)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build()
    a = ap.parse_args(argv)
    try:
        return (a.fn if getattr(a, "fn", None) else cmd_show)(a)
    except NoMissionError:
        # Reported from another session: `mission add` on an empty store raised
        # NoSuchItemError on the id it had just written, and `propose` was
        # worse -- it appended an event nothing could ever read back, silently.
        print("\n  no mission for this session yet."
              "\n  `mission init` writes one — it takes a minute, and the agent"
              "\n  cannot change it afterwards.\n")
        return 1
    except NoSessionError as e:
        # A person in their own terminal is the NORMAL caller of the human-only
        # commands, and they used to get a pathlib TypeError.
        #
        # Refusing to guess stays -- tie-breaking on recency would be guessing
        # with extra steps. But the refusal rebuilds the command THEY ran, once
        # per candidate, so resolving it is a paste rather than a retype. A
        # correct refusal that costs three minutes of typing is one you learn
        # to route around.
        import shlex
        ran = list(argv if argv is not None else sys.argv[1:])
        # Drop any addressing the person already tried, VALUE INCLUDED. Only
        # the flag was stripped before, so a failed `--on tripnom` rebuilt as
        # `mission set objective ... tripnom --on career-hub` -- a paste that
        # fails on a stray positional.
        drop, kept, skip = {"--session", "--on", "--into"}, [], False
        for x in ran:
            if skip:
                skip = False
                continue
            if x in drop:
                skip = True
                continue
            if any(x.startswith(d + "=") for d in drop):
                continue
            kept.append(x)
        ran = kept
        print(f"\n  {e}\n")
        for addr, title in e.candidates:
            print(f"    {title}")
            # Quote the address too. A goal named "mission two" rebuilt as
            # `--on mission two`, where the shell hands the command a stray
            # positional and the paste fails on the line that exists to be
            # pasted.
            print(f"      mission {' '.join(shlex.quote(x) for x in ran)} "
                  f"{e.flag} {shlex.quote(addr)}\n")
        if not e.candidates:
            print()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
