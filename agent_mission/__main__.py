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
                      resolve_session, short_id, transcript_for)
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


def _store(sid: str) -> MissionStore:
    return MissionStore(root_for(sid))


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


def _human_gate(what: str) -> bool:
    if _at_a_keyboard():
        return True
    print(f"  {what} is yours, and this is not a terminal — refusing.\n"
          f"  If you are an agent: `mission propose \"...\"` instead; it needs\n"
          f"  no permission and stays inert until a person accepts it.\n"
          f"  If you are a person in a pipeline: {HUMAN_ENV}=1 ahead of the "
          f"command.")
    return False


def cmd_init(a) -> int:
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
    if not sid:
        print("  no session id. Run inside Claude Code, or pass --session.")
        return 1
    st = _store(sid)
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
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
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
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
    st = _store(sid)
    if st.load() is None:
        print("  no mission for this session — `mission init` first")
        return 1
    if not _human_gate(f"the {a.field}"):
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
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
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
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
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
    cst.set_protected("name", (a.to or item.text)[:60], by="human")
    # Criteria are the PARENT's and stay there: a slice of the work does not
    # get to decide the whole mission is finished. Limits do carry, because a
    # constraint that stops applying to a subagent is not a constraint.
    for f in ("constraints", "non_goals"):
        if getattr(m, f):
            cst.set_protected(f, getattr(m, f), by="human")

    print(f"\n  delegated {item.id} → session {child}\n"
          f"  Hand the subagent this, verbatim:\n\n"
          f"    Your goal is recorded. Read it first:\n"
          f"      mission show --session {child}\n"
          f"    Record work you think is missing with:\n"
          f"      mission propose \"...\" --session {child}\n"
          f"    You cannot tick items or change the goal; that is the human's.\n")
    return 0


def cmd_why(a) -> int:
    """When did this field change, and to what. The log already knew."""
    import datetime as _dt
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
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
    """Which mission a write just landed on, in one line."""
    m = _store(sid).load()
    return f"{short_id(sid)}  {m.title}" if m else short_id(sid)


def cmd_add(a) -> int:
    # `add` = propose + accept in one step, both as the human. Ungated, it was
    # the whole authority model in one command: an agent could write an
    # ACCEPTED item that `mission why` then attributed to Jonathan. The tty
    # gate closed set/accept/done/remove and missed the one that does two of
    # them at once.
    if not _human_gate("adding an agreed item"):
        return 1
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
    st = _store(sid)
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
    sid = resolve_session(a.session, getattr(a, 'cwd', None))
    ev = _store(sid).propose(a.text, by="agent", parent=a.under)
    print(f"  proposed {ev['item_id']} — inert until you `mission accept {ev['item_id']}`")
    print(f"  → {_where(sid)}")
    return 0


def _apply(a, verb: str, fn) -> int:
    """Run a per-item action over every id given, and report each one.

    Several ids at once because the alternative is what actually happens: the
    agent proposes six items, ticking them is six commands, and the plan stops
    being maintained. An unknown id is reported and the rest still run -- a
    typo in the fourth id must not silently drop the first three.
    """
    st = _store(resolve_session(a.session, getattr(a, 'cwd', None)))
    bad = 0
    for iid in a.item_id:
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
    return _apply(a, "accepted", lambda st, i: st.accept(i, by="human"))


def cmd_done(a) -> int:
    if not _human_gate("marking work done"):
        return 1
    rc = _apply(a, "done", lambda st, i: st.complete(i, by="human"))
    return rc or cmd_show(a)


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


def cmd_setup(a) -> int:
    """Install the /mission slash command so sessions run it, not read it."""
    src = Path(__file__).resolve().parent.parent / "commands" / "mission.md"
    if not src.exists():
        print("  commands/mission.md not found in the package")
        return 1
    dest_dir = Path(a.dest).expanduser() if a.dest else Path.home() / ".claude" / "commands"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "mission.md"
    if dest.exists() and not a.force:
        print(f"  {dest} already exists — `mission setup --force` to overwrite")
        return 1
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n  installed {dest}")
    print("  type /mission in any Claude Code session\n")
    print("  Without this, typing 'mission init' into a session is read as an")
    print("  instruction and the agent goes and does something else entirely.\n")
    return _install_deny_rules(a)


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
    try:
        _store(resolve_session(a.session, getattr(a, 'cwd', None))).remove(a.item_id, by="human")
    except NoSuchItemError:
        print(f"  no item {a.item_id!r} in this plan")
        return 1
    print(f"  removed {a.item_id}")
    return cmd_show(a)


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


def main(argv: list[str] | None = None) -> int:
    # Shared flags live on a parent so they work AFTER the subcommand too --
    # `mission show --session X` is what anyone actually types, and a
    # top-level-only flag rejects it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", default=None,
                        help="session id (default: this one)")
    common.add_argument("--cwd", default=".")
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
    pr.set_defaults(fn=cmd_propose)
    ac = sub.add_parser("accept", parents=[common])
    ac.add_argument("item_id", nargs="+", metavar="ID")
    ac.set_defaults(fn=cmd_accept)
    dn = sub.add_parser("done", parents=[common])
    dn.add_argument("item_id", nargs="+", metavar="ID")
    dn.set_defaults(fn=cmd_done)
    su = sub.add_parser("setup", parents=[common],
                        help="install the /mission slash command")
    su.add_argument("--dest", default=None)
    su.add_argument("--force", action="store_true")
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
    rm.add_argument("item_id")
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

    vs = sub.add_parser("version", parents=[common],
                        help="which build of mission is this, and what can it do")
    vs.set_defaults(fn=cmd_version)

    global _SUBCOMMANDS
    _SUBCOMMANDS = set(sub.choices)

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
        print(f"\n  {e}")
        for sid, title in e.candidates:
            print(f"    --session {sid:<24} {title}")
        print()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
