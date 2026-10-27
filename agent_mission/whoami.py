"""Was this command run by a person, or by an agent wearing a person's tty?

The tty gate asks one question -- `isatty()` -- and `script(1)` answers it for
anybody. From an agent's non-tty shell, with no override set:

    script -q /dev/null python3 -m agent_mission set objective "..." --on demo

succeeded, and the log recorded `"by": "human", "typed_by": "human"`. The same
trick brings up a WRITABLE board (`script -F -q board.log ... board`) whose
write code is then readable from the log file the agent itself chose. Unlike
the documented `AGENT_MISSION_I_AM_HUMAN` override, nothing in the event log
showed that this had happened, so a forged write was indistinguishable from a
typed one -- which is the single claim this tool makes.

So a tty is necessary and no longer sufficient: we also ask whether this
process is running UNDERNEATH an agent.

WHAT THIS IS NOT: a boundary. The parent chain is walkable out of, and a
second reader walked out of it on 2026-10-08 with no `script`, no override
and no log append: a backgrounded subshell exits, the python inside it
reparents, and `pty.spawn` supplies the tty. The write recorded
`"by":"human"` with no `ancestor_agent` at all.

⚠️ `nohup` and `setsid` do NOT reparent -- `setsid` leaves the ppid unchanged
and `nohup` only ignores SIGHUP. This file used to name both as the way out,
which is wrong about the mechanism while right about the conclusion. Only an
ORPHANING reparents: a double fork, or the parent exiting first, which is
what the demonstrated bypass used.

And the stamp shared the gate's detector, so a bypass of one was a bypass of
the other -- "readable afterwards" was true only on the paths the detector
already caught. Two things changed because of that: the env half now looks
at every CLAUDE_* variable rather than three named ones (which does not stop
`env -u`, but removes the "forgot one" class), and `provenance` records
ORPHANING as its own fact, measured independently of whether we think an
agent is above us. A process with no parent, holding a fresh pty, is not
what a person at a terminal looks like.

What would make this a boundary is a separate process the agent cannot write
as. Nothing in this file is that, and the README and SECURITY.md say so.
"""

from __future__ import annotations

import os
import subprocess

# Env vars Claude Code sets in the processes it spawns. Inherited by every
# descendant, including one re-parented away, so this half survives `setsid`
# even though the parent walk does not.
_AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")

# Prefixes and names that mean the same thing, checked as a FAMILY. The named
# triple above was exhaustive when it was written and is not any more: a live
# session carries about 25 CLAUDE_* variables plus AI_AGENT and CLAUDE_PID, so
# `env -u` of three names was enough to look like a person. Checking the
# family does not stop `env -u` either -- nothing here does -- but it removes
# the class where the list simply fell behind the product.
_AGENT_ENV_PREFIX = ("CLAUDE_", "CLAUDECODE", "ANTHROPIC_")
_AGENT_ENV_EXACT = ("AI_AGENT", "CLAUDECODE",
                    # Codex. Named, not a CODEX_ prefix: a person may set
                    # CODEX_HOME in their own shell, and a false positive
                    # refuses a real person's write. Codex's sandbox blocks
                    # `ps`, so the ancestor walk sees nothing there and these
                    # are the only signal.
                    "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX",
                    "CODEX_CI", "CODEX_SHELL")


def agent_env() -> list[str]:
    """Every environment variable that says a coding agent is above us."""
    return sorted(k for k in os.environ
                  if k in _AGENT_ENV_EXACT or k.startswith(_AGENT_ENV_PREFIX))

# Matched against the ancestor's command. `claude` is the CLI; a node process
# running it shows up as node with claude on the argv, which is why the check
# looks at the whole command string and not just the basename.
_AGENT_HINTS = ("claude", "claude-code", "codex")

_MAX_DEPTH = 24          # launchd is pid 1; this is slack, not a real bound


def _ps(pid: int) -> tuple[int, str]:
    """(ppid, command) for one pid, or (0, '') if it cannot be read."""
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=,command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return 0, ""
    if not out:
        return 0, ""
    head, _, rest = out.partition(" ")
    try:
        return int(head), rest.strip()
    except ValueError:
        return 0, ""


def _looks_like_agent(command: str) -> bool:
    """Does this command line belong to a coding agent?

    Deliberately checks for a path or word boundary rather than a bare
    substring: `/usr/bin/claudette` and a directory called `claude-notes`
    would otherwise read as an agent, and a false positive here refuses a
    real person's write -- the one failure mode worse than missing a forgery.
    """
    if not command:
        return False
    first = command.split()[0] if command.split() else ""
    base = os.path.basename(first)
    if base in _AGENT_HINTS:
        return True
    # `node /opt/.../claude/cli.js` and `claude --resume`: a token that IS the
    # name, or a path component equal to it.
    for token in command.split():
        if os.path.basename(token) in _AGENT_HINTS:
            return True
        if any(part in _AGENT_HINTS for part in token.split("/")):
            return True
    return False


def ancestors(pid: int = None) -> list[str]:
    """Commands of this process's ancestors, nearest first."""
    pid = os.getppid() if pid is None else pid
    chain: list[str] = []
    seen: set[int] = set()
    for _ in range(_MAX_DEPTH):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        ppid, command = _ps(pid)
        if not command:
            break
        chain.append(command)
        pid = ppid
    return chain


def spawned_by_agent() -> bool:
    """True when this process is, or descends from, a coding agent."""
    if agent_env():
        return True
    return any(_looks_like_agent(c) for c in ancestors())


def own_session_leader() -> bool:
    """Is this process its own session leader?

    ⚠️ This replaces the reasoning behind `orphaned`, which MISSED the very
    bypass it was written for. A second reader re-ran round 5 and measured
    what I had not: the writing process is a CHILD of the orphan, so its
    ppid is the orphan's pid, not 1. `getppid() <= 1` was never going to be
    true for it, and the claim "the bypass now leaves a trace" was false in
    four documents.

    Measured here afterwards, on this machine:

      orphan + pty.spawn (round 5)   ppid 2071  sid == pid   tty    STAMPED
      ordinary agent shell           ppid 2171  sid != pid   no tty  no
      under script(1)                ppid 2177  sid != pid   tty     no
      backgrounded, no pty           ppid 2180  sid != pid   no tty  no

    `script` is absent from the catch list on purpose: `script` makes ITSELF
    the session leader and the writer under it is not one, so this says
    nothing about that path -- and it does not need to, because the ancestor
    walk already catches it. Each signal covering a different shape is the
    point; one signal covering both was the mistake.
    """
    try:
        return os.getsid(0) == os.getpid()
    except (AttributeError, OSError):
        return False            # no getsid (Windows): unknown, not suspicious


# The shells a person's terminal session is led by. `login` is what Terminal
# itself starts.
_SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"}


def session_leader_command() -> str:
    """The command line of whoever LEADS this process's session, or ''."""
    try:
        return _ps(os.getsid(0))[1]
    except (AttributeError, OSError):
        return ""


def leader_is_a_persons_shell(command: str) -> bool:
    """Is this session led by what a person's terminal is led by?

    ⚠️ This replaces asking `own_session_leader()` -- "is the WRITER the
    leader?" -- which the attacker controls by where they put the writer. A
    second reader showed it on 2026-10-11: `sh -c "writer"` execs, so the
    writer leads and was stamped, but `sh -c "writer; true"` makes sh fork,
    the writer becomes a child, and nothing was stamped. One character. So
    the question moves from the writer's position to the SESSION itself.

    Measured on this machine, every terminal session was led by one of:
        login -pf <user>                             (Terminal)
        /bin/zsh -l                                    (a login shell)
        login -pfl <user> /bin/bash -c exec ...      (an IDE terminal)
    That third one has a `-c` in it and is honest, because the LEADER is
    `login` -- which is why the executable is looked at before any argument.

    The attack's sessions are led by `sh -c "...; true"` (a shell running a
    command, not waiting for a person) or by the python process that took
    the pty. Unreadable means unknown, which is not suspicious: this only
    ever adds a note to the log, it never refuses a write.
    """
    if not command:
        return True
    toks = command.split()
    exe = toks[0]
    base = os.path.basename(exe).lstrip("-")
    if base == "login":
        return True
    if base in _SHELLS:
        # A login shell is spelled with a leading dash ("-zsh"); otherwise
        # interactive unless it was handed a command to run.
        return exe.startswith("-") or "-c" not in toks[1:]
    return False


def orphaned() -> bool:
    """Has this process been cut loose from whatever started it?

    Measured independently of `spawned_by_agent`, and that independence is
    the point: the stamp and the gate shared one detector, so the bypass that
    defeated the gate also erased the record of itself. A parent of pid 1
    means the process that started this one has exited -- which is what a
    double fork does, and what the demonstrated bypass used to put a pty
    between an agent and a write.

    It is NOT evidence of an agent. A person's `nohup ... &` survives a
    closed terminal the same way, and a launchd-started job legitimately has
    no parent. So this is stamped, never gated on: the log says what was
    measured and the human reads it.
    """
    try:
        return os.getppid() <= 1
    except OSError:
        return False


def tty_name() -> str:
    """The controlling terminal's name, or '' when there is none.

    Recorded on human-authority events so a pty handed out by `script` is
    visible after the fact: a person's write comes from /dev/ttys003, and
    `script` allocates a fresh pty whose name appears nowhere else.
    """
    try:
        return os.ttyname(0)
    except Exception:
        return ""


def provenance() -> dict:
    """The fields stamped on every human-authority event."""
    out: dict = {}
    tty = tty_name()
    if tty:
        out["tty"] = tty
    if spawned_by_agent():
        # Only stamped when true. A field that is False on every honest event
        # is noise; one that is present at all is the thing worth reading.
        out["ancestor_agent"] = True
    if orphaned():
        # Kept, because it is a true and cheap fact, but it is NOT the
        # round-5 signal -- see `own_session_leader` for why it missed it.
        out["orphaned"] = True
    if own_session_leader():
        out["session_leader"] = True
    leader = session_leader_command()
    led_by_a_person = leader_is_a_persons_shell(leader)
    if tty and not led_by_a_person:
        # WHAT led it, so `mission why` can show the reader rather than
        # paraphrase it. Truncated: it is a fact for a person to read.
        out["session_led_by"] = leader[:80]
    if tty and not led_by_a_person and not out.get("ancestor_agent"):
        # A terminal whose SESSION is not led by a person's shell, and nothing
        # admitting to an agent. Covers both measured shapes: round 5 (the
        # writer itself leads, from python) and its one-character variant
        # (`sh -c "writer; true"` leads, the writer is its child). A person's
        # write comes from a session led by `login` or a login shell, so this
        # is absent on the honest path rather than merely rarer.
        #
        # Named rather than scored. It records what was true, `mission why`
        # prints it, and the human decides what it means -- because a
        # deliberately detached job of their own looks the same.
        out["tty_without_shell"] = True
    return out
