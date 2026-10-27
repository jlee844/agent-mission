"""Codex (GPT) as a board session.

Codex reads hooks from `~/.codex/hooks.json`, with events nested below a
top-level `hooks` object, and adds a hook's stdout to the model's context. So
the same two lines reach a Codex thread: the re-anchor at session start and
the edge-triggered signal at each prompt.

What this module will NOT do is edit `~/.codex/config.toml`. Codex's sandbox
refuses writes outside the workspace, and the store lives in
`~/.agent-mission`; letting Codex write there is a change to Codex's security
settings, so it is printed for the person to add, never applied.

Codex also asks the person to TRUST each new or changed hook (by hash) before
it runs -- `/hooks` in the CLI. Installing here does not make them run.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

MARK_BEGIN = "<!-- agent-mission:begin -->"
MARK_END = "<!-- agent-mission:end -->"
# Every event Codex accepts (read from codex-cli 0.162's config schema). The
# migration of an old flat file must know all of them: with only our two, a
# flat file that also held another tool's PreToolUse hook was refused outright.
HOOK_EVENTS = ("PreModelRequest", "PostModelRequest", "PreToolUse",
               "PermissionRequest", "PostToolUse", "PreCompact",
               "PostCompact", "SessionStart", "SessionEnd",
               "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop",
               "Interrupt")

AGENTS_BLOCK = f"""{MARK_BEGIN}
## The mission board (shared with Claude Code sessions)

Your person runs one board of goals at http://127.0.0.1:8976, read and written
with the `mission` CLI. Claude Code sessions and Codex threads share it: it is
the only channel between sessions. Your thread id is `$CODEX_THREAD_ID`.

- At the start of a task, if the hook line says NOT ATTACHED and this work
  belongs to a goal, `mission attach <goal>`. Ask before creating a goal.
- Routing is yours, ruling is the person's. You may `propose`, `observe`, `take`,
  `release`, `claims-done`, `finding`, `checked`. Only the person accepts or ticks.
  Never describe a proposal as agreed or a claim as finished.
- `mission queue --on <goal>` shows ready work; `mission take <id> --on
  <goal>` leases it. Claim with `mission claims-done <id> "done: <what>
  (<path from the goal's folder>)" --on <goal>`. Claim only when every clause
  is met; a claim must name a file.
- To ask another session for work, put an item on ITS goal. To report, write
  to the board, not only to the chat.
- If a `mission` write is refused by your sandbox, the board is not broken:
  tell the person, and give them the `writable_roots` line the error prints.
  Never ask for full access and never work around it. If Codex offers the
  person a one-time approval to run that command outside the sandbox, that
  is their call and fine. Reading the board works in every sandbox mode.
- Full contract: `mission help` and `mission whereami --full`.
{MARK_END}
"""

SANDBOX_LINE = ('[sandbox_workspace_write]\n'
                'writable_roots = ["{store}"]')


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _mission_cmd() -> str:
    found = shutil.which("mission")
    return found or "mission"


def wanted_hooks(cmd: str | None = None) -> dict:
    cmd = cmd or _mission_cmd()
    return {
        "SessionStart": [{
            "matcher": "startup|resume|clear|compact",
            "hooks": [{"type": "command",
                       "command": f"{cmd} whereami --full",
                       "statusMessage": "Mission board"}]}],
        "UserPromptSubmit": [{
            "hooks": [{"type": "command", "command": f"{cmd} signal"}]}],
    }


def _ours(handler: dict) -> bool:
    c = handler.get("command", "")
    return ("whereami --full" in c or c.rstrip().endswith(" signal")
            or c.strip() == "mission signal") and "mission" in c


def _has(groups: list, command: str) -> bool:
    return any(h.get("command") == command
               for g in groups for h in g.get("hooks", []))


def sandbox_allows(store: str, config_text: str) -> bool:
    """Does config.toml list the board folder as a writable root?

    A text check, not a TOML parse: the suite runs on Python 3.9, which has
    no tomllib, and a regex over one key is enough to answer yes or no.
    Both spellings count -- the absolute path and the `~/` form."""
    import re
    m = re.search(r"writable_roots\s*=\s*\[([^\]]*)\]", config_text)
    if not m:
        return False
    roots = re.findall(r"[\"']([^\"']+)[\"']", m.group(1))
    want = {str(Path(store).expanduser()).rstrip("/")}
    for r in roots:
        if str(Path(r).expanduser()).rstrip("/") in want:
            return True
    return False


def status(cmd: str | None = None) -> dict:
    home = codex_home()
    hooks_p, agents_p = home / "hooks.json", home / "AGENTS.md"
    hooks_error = ""
    if not hooks_p.exists():
        data = {"hooks": {}}
    else:
        try:
            data = json.loads(hooks_p.read_text())
        except OSError as exc:
            data = {}
            hooks_error = f"cannot read hooks config: {exc}"
        except ValueError as exc:
            data = {}
            hooks_error = f"invalid JSON: {exc}"
    if not hooks_error:
        if (not isinstance(data, dict)
                or set(data) - {"hooks", "description"}
                or not isinstance(data.get("hooks"), dict)):
            hooks_error = "invalid Codex schema; expected top-level hooks object"
            hooks = {}
        else:
            hooks = data["hooks"]
    want = wanted_hooks(cmd)
    hooks_ok = not hooks_error and all(
        isinstance(hooks.get(ev, []), list)
        and _has(hooks.get(ev, []), g["hooks"][0]["command"])
        for ev, groups in want.items() for g in groups)
    try:
        agents_ok = MARK_BEGIN in agents_p.read_text()
    except OSError:
        agents_ok = False
    store = os.environ.get("AGENT_MISSION_HOME") or str(
        Path.home() / ".agent-mission")
    try:
        sandbox_ok = sandbox_allows(store, (home / "config.toml").read_text())
    except OSError:
        sandbox_ok = False
    return {"home": str(home), "codex_installed": home.exists(),
            "hooks": hooks_ok, "hooks_error": hooks_error,
            "agents_md": agents_ok, "sandbox": sandbox_ok, "store": store}


def _backup(p: Path) -> str:
    if not p.exists():
        return ""
    b = p.with_name(p.name + f".bak-mission-{int(time.time())}")
    shutil.copy2(p, b)
    return str(b)


def install(cmd: str | None = None) -> list[str]:
    """Add our hooks and AGENTS.md block. Idempotent; never removes another
    tool's hooks; backs up anything it changes. Returns what it did."""
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    done: list[str] = []

    hooks_p = home / "hooks.json"
    try:
        data = json.loads(hooks_p.read_text()) if hooks_p.exists() else {}
    except (OSError, ValueError):
        raise SystemExit(f"{hooks_p} is not valid JSON; fix it first, "
                         f"nothing was changed")
    if not isinstance(data, dict):
        raise SystemExit(f"{hooks_p} must contain a JSON object; nothing changed")
    # Codex requires event names under the top-level `hooks` object. Migrate
    # the earlier agent-mission flat format in place, preserving description
    # and all event groups, then back up the original before writing.
    changed = False
    if "hooks" not in data:
        if set(data) - set(HOOK_EVENTS) - {"description"}:
            raise SystemExit(f"{hooks_p} has unsupported top-level fields; "
                             f"nothing was changed")
        old = data
        data = {"hooks": {k: v for k, v in old.items()
                          if k in HOOK_EVENTS}}
        if "description" in old:
            data["description"] = old["description"]
        changed = True
    elif (set(data) - {"hooks", "description"}
          or not isinstance(data.get("hooks"), dict)):
        raise SystemExit(f"{hooks_p} has an invalid Codex hooks schema; "
                         f"expected top-level hooks object, nothing changed")
    hooks = data["hooks"]
    for ev, groups in wanted_hooks(cmd).items():
        have = hooks.setdefault(ev, [])
        for g in groups:
            c = g["hooks"][0]["command"]
            if _has(have, c):
                continue
            # A stale copy of ours (old path to `mission`) is replaced, not
            # stacked: two signal hooks print every line twice.
            for hg in have:
                hg["hooks"] = [h for h in hg.get("hooks", []) if not _ours(h)]
            have[:] = [hg for hg in have if hg.get("hooks")]
            have.append(g)
            changed = True
    if changed:
        b = _backup(hooks_p)
        hooks_p.write_text(json.dumps(data, indent=2) + "\n")
        done.append(f"wrote {hooks_p}" + (f" (backup {b})" if b else ""))

    agents_p = home / "AGENTS.md"
    text = agents_p.read_text() if agents_p.exists() else ""
    if MARK_BEGIN in text and MARK_END in text:
        head, _, rest = text.partition(MARK_BEGIN)
        _, _, tail = rest.partition(MARK_END)
        new = head + AGENTS_BLOCK.rstrip("\n") + tail
    else:
        new = (text.rstrip("\n") + "\n\n" if text else "") + AGENTS_BLOCK
    if new != text:
        b = _backup(agents_p)
        agents_p.write_text(new)
        done.append(f"wrote {agents_p}" + (f" (backup {b})" if b else ""))
    return done


def next_steps(store: str) -> list[str]:
    return [
        "Codex asks you to trust new hooks before they run: open `/hooks` "
        "in Codex and trust the two mission hooks.",
        "Codex's sandbox blocks writes outside the workspace, and the board "
        "lives in " + store + ". Add this to ~/.codex/config.toml yourself "
        "(it is a sandbox setting, so this tool does not edit it):",
        "    " + SANDBOX_LINE.format(store=store).replace("\n", "\n    "),
    ]
