"""The five surfaces, installed by one implementation with two front-ends.

`mission setup` in a terminal and the board's Setup panel must do the same
thing, or the board quietly drifts from what the CLI would have done and you
have two answers to "is this installed". So the doing lives here; the CLI and
the board only ask.

Everything in this module is:

    idempotent      running it twice changes nothing the second time
    diff-shown      it reports what it would change before it changes it
    backed up       settings.json is copied before any write

Those three properties are the tier rule -- see the SETUP TIER note in
__main__ -- and they are why this work can reach a button at all, while
`set`/`accept`/`done`/`remove` cannot.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DENY_RULES = [f"Bash(mission {c}:*)" for c in
              ("set", "accept", "done", "remove", "add")]
STATUSLINE = {"type": "command", "command": "mission whereami"}
HOOK_CMD = "mission whereami --full 2>/dev/null || true"
SURFACES = ("slash command", "deny rules", "statusline",
            "re-anchor hook")


def home() -> Path:
    h = Path(os.environ.get("AGENT_MISSION_HOME",
                            Path.home() / ".agent-mission"))
    h.mkdir(parents=True, exist_ok=True)
    return h


def settings_path(explicit: str | None = None) -> Path:
    return (Path(explicit).expanduser() if explicit
            else Path.home() / ".claude" / "settings.json")


def commands_dir(explicit: str | None = None) -> Path:
    return (Path(explicit).expanduser() if explicit
            else Path.home() / ".claude" / "commands")


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, data: dict) -> str:
    backup = path.with_suffix(f".json.bak-mission-{int(time.time())}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return backup.name


def _template() -> Path:
    return Path(__file__).resolve().parent.parent / "commands" / "mission.md"


def status(settings: str | None = None, dest: str | None = None) -> list[dict]:
    """Each surface: installed or not, and what is there now."""
    sp, cd = settings_path(settings), commands_dir(dest)
    data = _read(sp) or {}
    deny = data.get("permissions", {}).get("deny", [])
    sl = data.get("statusLine") or {}
    sl_cmd = sl.get("command", "") if isinstance(sl, dict) else str(sl)
    wrapper = home() / "statusline.sh"
    hooks = json.dumps(data.get("hooks", {}).get("SessionStart", []))
    cmd_file = cd / "mission.md"
    # Existence is not currency. The command file is a COPY, so it goes stale
    # every time the repo's version changes -- and it was reported "installed"
    # while five sections behind, which is how other sessions kept working from
    # a picture of the tool that no longer matched it.
    try:
        fresh = cmd_file.read_text(encoding="utf-8") == \
            _template().read_text(encoding="utf-8")
    except OSError:
        fresh = False
    return [
        {"name": "slash command", "ok": cmd_file.exists() and fresh,
         "state": "current" if fresh else
                  ("outdated" if cmd_file.exists() else "missing"),
         "detail": str(cmd_file) if fresh else
                   (f"{cmd_file} — `mission setup --force` updates it"
                    if cmd_file.exists() else str(cmd_file))},
        {"name": "deny rules", "ok": all(r in deny for r in DENY_RULES),
         "detail": f"{sum(r in deny for r in DENY_RULES)}/{len(DENY_RULES)}"},
        {"name": "statusline",
         "ok": "mission whereami" in sl_cmd or (wrapper.exists()
                                                and str(wrapper) in sl_cmd),
         "detail": sl_cmd[:70] or "not set"},
        {"name": "re-anchor hook", "ok": "mission whereami --full" in hooks,
         "detail": "SessionStart"},
    ]


def plan(name: str, settings: str | None = None,
         dest: str | None = None) -> dict:
    """What installing this surface WOULD change. Writes nothing."""
    sp, cd = settings_path(settings), commands_dir(dest)
    data = _read(sp)
    if name == "slash command":
        f = cd / "mission.md"
        if f.exists() and f.read_text(encoding="utf-8") == \
                _template().read_text(encoding="utf-8"):
            return {"changes": [], "why": "already current"}
        return {"changes": [f"write {f}"], "why": ""}
    if data is None:
        return {"changes": [], "why": f"cannot read {sp}"}
    if name == "deny rules":
        have = data.get("permissions", {}).get("deny", [])
        missing = [r for r in DENY_RULES if r not in have]
        return {"changes": [f'permissions.deny += "{r}"' for r in missing],
                "why": "" if missing else "already current"}
    if name == "statusline":
        cur = data.get("statusLine") or {}
        inner = cur.get("command") if isinstance(cur, dict) else str(cur)
        if inner and "mission whereami" in inner:
            return {"changes": [], "why": "already current"}
        if inner:
            return {"changes": [
                f"write {home() / 'statusline.sh'} (wraps: {inner[:50]})",
                f'statusLine.command = "{home() / "statusline.sh"}"'], "why": ""}
        return {"changes": ['statusLine = {"command": "mission whereami"}'],
                "why": ""}
    if name == "re-anchor hook":
        start = data.get("hooks", {}).get("SessionStart", [])
        if any(HOOK_CMD in json.dumps(h) for h in start):
            return {"changes": [], "why": "already current"}
        return {"changes": [f"hooks.SessionStart += {HOOK_CMD} "
                            f"(keeping your {len(start)})"], "why": ""}
    if name == "board bookmark":
        f = home() / "board.html"
        return ({"changes": [], "why": "already current"} if f.exists()
                else {"changes": [f"write {f}"], "why": ""})
    raise ValueError(f"unknown surface: {name}")


def install(name: str, settings: str | None = None,
            dest: str | None = None) -> dict:
    """Apply one surface. Idempotent; returns what changed and the backup."""
    p = plan(name, settings, dest)
    if not p["changes"]:
        return {"applied": [], "why": p["why"], "backup": None}
    sp, cd = settings_path(settings), commands_dir(dest)

    if name == "slash command":
        cd.mkdir(parents=True, exist_ok=True)
        (cd / "mission.md").write_text(_template().read_text(encoding="utf-8"),
                                       encoding="utf-8")
        return {"applied": p["changes"], "why": "", "backup": None}
    if name == "board bookmark":
        from .daemon import running, write_bookmark
        rec = running()
        write_bookmark(rec["port"] if rec else 8976)
        return {"applied": p["changes"], "why": "", "backup": None}

    data = _read(sp)
    if data is None:
        return {"applied": [], "why": f"cannot read {sp}", "backup": None}

    if name == "deny rules":
        data.setdefault("permissions", {}).setdefault("deny", []).extend(
            r for r in DENY_RULES
            if r not in data["permissions"]["deny"])
    elif name == "statusline":
        cur = data.get("statusLine") or {}
        inner = cur.get("command") if isinstance(cur, dict) else str(cur)
        if inner:
            w = home() / "statusline.sh"
            w.write_text(
                "#!/bin/sh\n"
                "# Written by `mission setup`. Your original statusline command\n"
                "# is preserved verbatim below; `mission whereami` is appended.\n"
                "# Statuslines are ONE line, so both halves are flattened.\n"
                "input=$(cat)\n"
                f"mine=$(printf '%s' \"$input\" | {inner} 2>/dev/null | tr '\\n' ' ')\n"
                "goal=$(mission whereami 2>/dev/null)\n"
                "if [ -n \"$mine\" ] && [ -n \"$goal\" ]; then\n"
                "  printf '%s · %s' \"$mine\" \"$goal\"\n"
                "else\n"
                "  printf '%s%s' \"$mine\" \"$goal\"\n"
                "fi\n", encoding="utf-8")
            w.chmod(0o755)
            data["statusLine"] = {"type": "command", "command": str(w)}
        else:
            data["statusLine"] = dict(STATUSLINE)
    elif name == "re-anchor hook":
        data.setdefault("hooks", {}).setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 5}]})

    backup = _write(sp, data)
    return {"applied": p["changes"], "why": "", "backup": backup}
