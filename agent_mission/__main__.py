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
import subprocess
import sys
import tempfile
from pathlib import Path

from .daemon import ensure as ensure_board, running as board_running, stop as board_stop
from .session import activity, current_session_id, transcript_for
from .store import (FIELD_AUTHORITY, Authority, MissionStore, NoSuchItemError,
                    ProtectedFieldError, root_for)

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


def cmd_init(a) -> int:
    sid = a.session or current_session_id()
    if not sid:
        print("  no session id. Run inside Claude Code, or pass --session.")
        return 1
    st = _store(sid)
    if st.load() and not a.force:
        print(f"  a mission already exists for {sid[:8]}. `mission` to see it, "
              f"`mission init --force` to start over.")
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
    st.create(sid, cwd, parsed["objective"], by="human")
    if parsed["name"]:
        st.set_protected("name", parsed["name"], by="human")
    for f in ("success_criteria", "constraints", "non_goals"):
        if parsed[f]:
            st.set_protected(f, parsed[f], by="human")
    stack: list[tuple[int, str]] = []          # (indent, item_id)
    for entry in parsed["checklist"]:
        text, indent = entry["text"], entry["indent"]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        ev = st.propose(text, by="human", parent=parent)
        st.accept(ev["item_id"], by="human")
        stack.append((indent, ev["item_id"]))
    print(f"\n  mission set for {sid[:8]}\n")
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
    sid = a.session or current_session_id()
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
        _print_tree(roots, show_all=show_all)
        hidden = sum(1 for n in roots if n.complete)
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
    sid = a.session or current_session_id()
    st = _store(sid)
    if st.load() is None:
        print("  no mission for this session — `mission init` first")
        return 1
    field = a.field.replace("-", "_")
    if field not in PROTECTED_FIELDS:
        print(f"  {a.field} is not one of: {', '.join(sorted(PROTECTED_FIELDS))}")
        return 1
    value = a.value if field in ("name", "objective") else list(a.value_list or a.value.split("|"))
    st.set_protected(field, value, by="human")
    print(f"  {field} updated")
    return cmd_show(a)


def _print_tree(nodes, depth: int = 0, prefix: str = "",
                show_all: bool = False) -> None:
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
        print(f"    {prefix}{elbow}[{mark}] {item.id}  {item.text}{roll}")
        if n.children:
            _print_tree(n.children, depth + 1,
                        prefix + ("" if depth == 0 else ("   " if last else "│  ")),
                        show_all=show_all)


def cmd_add(a) -> int:
    sid = a.session or current_session_id()
    st = _store(sid)
    ev = st.propose(a.text, by="human", parent=a.under)
    st.accept(ev["item_id"], by="human")
    where = f" under {a.under}" if a.under else ""
    print(f"  added {ev['item_id']}{where}  {a.text}")
    return 0


def cmd_propose(a) -> int:
    ev = _store(a.session or current_session_id()).propose(
        a.text, by="agent", parent=a.under)
    print(f"  proposed {ev['item_id']} — inert until you `mission accept {ev['item_id']}`")
    return 0


def cmd_accept(a) -> int:
    try:
        _store(a.session or current_session_id()).accept(a.item_id, by="human")
    except NoSuchItemError:
        print(f"  no item {a.item_id!r} in this plan — `mission` to see the ids")
        return 1
    print(f"  accepted {a.item_id}")
    return 0


def cmd_done(a) -> int:
    try:
        _store(a.session or current_session_id()).complete(a.item_id, by="human")
    except NoSuchItemError:
        print(f"  no item {a.item_id!r} in this plan — `mission` to see the ids")
        return 1
    except ProtectedFieldError as e:
        print(f"  {e}")
        return 1
    print(f"  done {a.item_id}")
    return cmd_show(a)


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
    return 0


def cmd_remove(a) -> int:
    try:
        _store(a.session or current_session_id()).remove(a.item_id, by="human")
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
    ac = sub.add_parser("accept", parents=[common]); ac.add_argument("item_id"); ac.set_defaults(fn=cmd_accept)
    dn = sub.add_parser("done", parents=[common]); dn.add_argument("item_id"); dn.set_defaults(fn=cmd_done)
    su = sub.add_parser("setup", parents=[common],
                        help="install the /mission slash command")
    su.add_argument("--dest", default=None)
    su.add_argument("--force", action="store_true")
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

    bd = sub.add_parser("board", parents=[common])
    bd.add_argument("--port", type=int, default=8976)
    bd.add_argument("--open", action="store_true", help="open it in your browser")
    bd.add_argument("--stop", action="store_true")
    bd.add_argument("--foreground", action="store_true",
                    help=argparse.SUPPRESS)   # used by the spawner
    bd.set_defaults(fn=cmd_board)

    a = ap.parse_args(argv)
    return (a.fn if getattr(a, "fn", None) else cmd_show)(a)


if __name__ == "__main__":
    raise SystemExit(main())
