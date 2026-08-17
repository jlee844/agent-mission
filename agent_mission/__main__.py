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
from .store import (FIELD_AUTHORITY, Authority, MissionStore,
                    ProtectedFieldError, root_for)

TEMPLATE = """\
# Your mission for this session. Lines starting with # are ignored.
# Everything here is YOURS — the agent can propose and record, never rewrite.

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

# Things to do. Tick them off with `mission done <id>`.
CHECKLIST:
-
"""


def _parse(text: str) -> dict:
    out: dict[str, list[str] | str] = {"objective": "", "success_criteria": [],
                                       "constraints": [], "non_goals": [],
                                       "checklist": []}
    key = None
    keys = {"SUCCESS:": "success_criteria", "CONSTRAINTS:": "constraints",
            "NON-GOALS:": "non_goals", "CHECKLIST:": "checklist"}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("OBJECTIVE:"):
            out["objective"] = line.split(":", 1)[1].strip()
            key = None
        elif line.strip() in keys:
            key = keys[line.strip()]
        elif line.lstrip().startswith("-") and key:
            v = line.lstrip()[1:].strip()
            if v:
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
    text = TEMPLATE.format(objective=seed_obj, criterion=seed_crit)
    filled = text if a.no_edit else _edit(text)
    parsed = _parse(filled)
    if not parsed["objective"]:
        print("  no OBJECTIVE given — nothing saved.")
        return 1

    cwd = str(Path(a.cwd).resolve())
    st.create(sid, cwd, parsed["objective"], by="human")
    for f in ("success_criteria", "constraints", "non_goals"):
        if parsed[f]:
            st.set_protected(f, parsed[f], by="human")
    for item in parsed["checklist"]:
        ev = st.propose(item, by="human")
        st.accept(ev["item_id"], by="human")
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
        print(f"\n  CHECKLIST  {m.done_count}/{len(m.checklist)}")
        for i in m.items:
            mark = "x" if i.done else (" " if i.accepted else "?")
            print(f"    [{mark}] {i.id}  {i.text}")
        if m.unaccepted:
            print(f"    {len(m.unaccepted)} proposed, awaiting your accept")
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


def cmd_add(a) -> int:
    sid = a.session or current_session_id()
    st = _store(sid)
    ev = st.propose(a.text, by="human")
    st.accept(ev["item_id"], by="human")
    print(f"  added {ev['item_id']}  {a.text}")
    return 0


def cmd_propose(a) -> int:
    ev = _store(a.session or current_session_id()).propose(a.text, by="agent")
    print(f"  proposed {ev['item_id']} — inert until you `mission accept {ev['item_id']}`")
    return 0


def cmd_accept(a) -> int:
    _store(a.session or current_session_id()).accept(a.item_id, by="human")
    print(f"  accepted {a.item_id}")
    return 0


def cmd_done(a) -> int:
    try:
        _store(a.session or current_session_id()).complete(a.item_id, by="human")
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

    ap = argparse.ArgumentParser(prog="mission", description=__doc__,
                                 parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    i = sub.add_parser("init", parents=[common]); i.add_argument("--force", action="store_true")
    i.add_argument("--blank", action="store_true", help="do not seed from the transcript")
    i.add_argument("--no-edit", action="store_true", help="skip the editor (for scripts)")
    i.add_argument("--no-board", action="store_true", help="do not open the board")
    i.add_argument("--port", type=int, default=8976)
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("show", parents=[common]); s.set_defaults(fn=cmd_show)
    ad = sub.add_parser("add", parents=[common]); ad.add_argument("text"); ad.set_defaults(fn=cmd_add)
    pr = sub.add_parser("propose", parents=[common]); pr.add_argument("text"); pr.set_defaults(fn=cmd_propose)
    ac = sub.add_parser("accept", parents=[common]); ac.add_argument("item_id"); ac.set_defaults(fn=cmd_accept)
    dn = sub.add_parser("done", parents=[common]); dn.add_argument("item_id"); dn.set_defaults(fn=cmd_done)
    su = sub.add_parser("setup", parents=[common],
                        help="install the /mission slash command")
    su.add_argument("--dest", default=None)
    su.add_argument("--force", action="store_true")
    su.set_defaults(fn=cmd_setup)

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
