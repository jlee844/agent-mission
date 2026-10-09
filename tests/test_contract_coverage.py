"""The agent contract must document the commands an agent is allowed to run.

This is the defect these tests were written for, found on 2026-10-08 by
counting rather than reading: `commands/mission.md` is the protocol injected
into every session, and it documented ZERO of `take`, `release`, `finding`,
`queue`, `verify`, `checked`, `audit` and `dispatch`. The whole point of that
routing loop was to remove the need for a human to tell a session what to do,
and no session could discover any of it — the one path that worked was a
human pasting instructions, which is what it was built to replace.

The README table already had a guard (`test_the_commands_table_lists_every
_live_command`). The contract did not, so the surface a STRANGER reads was
checked and the surface an AGENT reads was not.

The allowed set is DERIVED from the deny rules rather than hand-kept, because
a hand-kept list is a second copy that goes stale exactly as quietly as the
contract did.
"""
from pathlib import Path

import pytest

from agent_mission.setup_surfaces import _DENIED

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "commands" / "mission.md"

# Commands an agent may run that nonetheless do not belong in the contract,
# each with the reason it is exempt. Anything NOT here and not denied must be
# documented, so adding a command forces a choice rather than silence.
# ⚠️ Three entries here were invented for commands that do not exist
# (`troubleshoot`, `acknowledge`, `discard`) and `test_the_exempt_list_holds
# _no_ghosts` caught all three on its first run. An exemption for a
# phantom command is worse than none: it reads as a decision.
EXEMPT = {
    "init": "the contract's own subject — it has three sections of its own",
    "setup": "run once by a person installing; `--check` is doctor's job",
    "doctor": "a person's diagnostic, and it exits 1 by design",
    "version": "trivial",
    "help": "trivial",
    "signal": "a hook calls it; a session never types it",
    "claims": "a hook calls it",
    "whereami": "the statusline calls it",
    "import": "a one-off migration",
    "migrate": "a one-off migration",
    "show": "the default, described throughout",
    "missions": "listing goals; `mission` alone shows this session's",
    "attach": "has its own section, by name",
    "detour": "has its own section, by name",
    "return": "has its own section, by name",
    "propose": "has its own section, by name",
    "pending": "a person's view of what awaits them",
    "why": "reads the log's history for a field",
    "observe": "recording evidence, described in the claims section",
    "sharpen": "documented in the claims section",
    "claims-done": "documented in the claims section",
    "take": "documented in the routing section",
    "release": "documented in the routing section",
    "finding": "documented in the routing section",
    "queue": "documented in the routing section",
    "verify": "documented in the routing section",
    "checked": "documented in the routing section",
    "audit": "documented in the routing section",
    "dispatch": "documented in the routing section",
}


def _subcommands() -> set[str]:
    from agent_mission.__main__ import main
    main(["version"])
    from agent_mission.__main__ import _SUBCOMMANDS
    return set(_SUBCOMMANDS)


def test_every_routing_command_is_in_the_contract():
    """The loop a session discovers for itself, rather than being told."""
    text = CONTRACT.read_text()
    for c in ("take", "release", "finding", "queue", "verify", "checked",
              "audit", "dispatch"):
        assert f"mission {c}" in text, \
            f"`mission {c}` is unreachable from the contract a session reads"


def test_a_new_agent_command_must_be_documented_or_exempted():
    """Derived from the deny rules: a command an agent MAY run and that the
    contract does not mention is a feature no session can find."""
    text = CONTRACT.read_text()
    live = _subcommands()
    undocumented = {c for c in live
                    if c not in _DENIED
                    and f"mission {c}" not in text
                    and c not in EXEMPT}
    assert not undocumented, (
        "agent-runnable commands missing from commands/mission.md: "
        f"{sorted(undocumented)} — document them, or add to EXEMPT with the "
        "reason they do not belong in the contract")


def test_the_exempt_list_holds_no_ghosts():
    """An exemption for a command that no longer exists hides a real gap."""
    live = _subcommands()
    ghosts = {c for c in EXEMPT if c not in live}
    assert not ghosts, f"EXEMPT names commands that do not exist: {sorted(ghosts)}"


def test_nothing_denied_is_taught_as_an_agent_command():
    """The contract must never show an agent a command the rules refuse.

    `done` and `accept` appear in it deliberately — as the human's commands,
    composed FOR them — so the test is about the imperative form, not the
    word.
    """
    text = CONTRACT.read_text()
    for c in _DENIED:
        for bad in (f"```bash\nmission {c} ", f"\nmission {c} --on"):
            assert bad not in text, \
                f"the contract shows `mission {c}` as something to run"


# ── bddee0ed: the contract must not advertise a denied command ───────────
#
# Found by a second reader on 2026-10-08: `argument-hint` offered `board`,
# `add` and `done`, and the body says to run `mission $ARGUMENTS` — so
# `/mission board` instructed the agent to run a command the deny rules then
# blocked. An instruction and a refusal describing the same action read as a
# broken tool, not as a boundary.

def test_the_argument_hint_offers_nothing_the_rules_refuse():
    text = CONTRACT.read_text()
    hint = next(l for l in text.splitlines() if l.startswith("argument-hint:"))
    offered = {w.strip().split()[0] for w in hint.split('"')[1].split("|")}
    clash = offered & set(_DENIED)
    assert not clash, (
        f"the slash command advertises denied command(s): {sorted(clash)} — "
        "either drop them from the hint or stop denying them")


def test_every_command_the_hint_offers_actually_exists():
    """A hint naming a command that was renamed is worse than no hint."""
    text = CONTRACT.read_text()
    hint = next(l for l in text.splitlines() if l.startswith("argument-hint:"))
    offered = {w.strip().split()[0] for w in hint.split('"')[1].split("|")}
    live = _subcommands()
    assert offered <= live, f"hint names non-commands: {sorted(offered - live)}"


def test_the_contract_says_what_to_do_when_a_word_is_the_humans():
    """Not running it is half the answer; the other half is saying so."""
    text = CONTRACT.read_text()
    assert "are the human's, not yours" in text
    assert "do not run it and do not work around it" in text
    assert "Print the command for the person to run themselves" in text
