# mission

**The goal, beside the work, that the agent cannot quietly rewrite.**

You start a session with something in mind. Two hours later there are 800 tool
calls, a summary written by the thing being summarised, and no easy answer to
*what was this for, and is it done?*

`mission` puts the goal next to the work: one command at the start, a checklist
you own, and one page showing every running session — what each is for, what it
has ticked off, and what it has actually done.

```bash
git clone https://github.com/jlee844/agent-mission && cd agent-mission
pip install -e .

mission init      # write it (opens your editor, takes a minute)
mission           # show it, with measured activity
mission board     # every live session on one page
```

## What it looks like

```
  Ship list sharing to mobile

  DONE WHEN
    · lists sync web↔mobile
    · PARITY.md regenerated

  CONSTRAINTS
    · no schema migration

  NOT DOING
    · redesigning the map

  CHECKLIST  1/4
    [x] 37f1f159  Port SpotList types
    [ ] f12da176  Wire AddToListSheet
    [ ] 464ef06c  Regenerate PARITY.md
    [?] d9e2e249  Add a caching layer
    1 proposed, awaiting your accept

  MEASURED SO FAR
    527 tool calls · 42 files changed · 141 test runs · 19 failed calls
```

`[?]` is an agent proposal. It is inert until you accept it.

## Three levels of authority

This is the whole design.

| level | fields | who writes |
|---|---|---|
| 🔒 **protected** | objective, success criteria, constraints, non-goals | **you only** |
| 🟡 **proposed** | checklist, strategy | agent suggests, you accept |
| 🟢 **observable** | decisions, evidence, notes | agent records freely |

**The agent has no path to a protected field.** Not a permission check it might
route around — there is no method that writes one on its behalf, and the same
holds for accepting a proposal and for marking an item done. Marking work
complete is a judgement, so it stays with you; the agent may record evidence.

There are 24 tests and most of them guard exactly this.

**The honest scope of that guarantee.** The store refuses agent-authored
missions. The *CLI* cannot tell who typed the command — it passes `by="human"`
because a person is assumed to be at the keyboard. So:

> An agent calling the library **cannot** set your objective or tick your
> checklist. An agent with **shell access** can run `mission init` and pretend
> to be you.

This defends against an agent quietly redefining the goal in the course of its
work, not against one deliberately impersonating you with full local
privileges. Closing that needs the CLI to prove a human is present — a
confirmation the agent cannot supply — which is not built.

## Several sessions at once

Two agents in one directory is normal — one on the sub-project, one that needs
the parent repo. Each session identifies itself from the id Claude Code exports
into every tool call, so missions never cross. `mission board` shows them
together, refreshing:

- what each session is for, and its checklist
- criteria, constraints and non-goals
- measured activity: calls, files changed, test runs, failed calls
- sessions with no mission say so, and tell you the command

Localhost only. Reads transcripts and the mission log; writes nothing else.

## What it will not do

**It does not judge whether the work is the right work.** That needs your
intent, and five separate attempts to answer it mechanically all failed — drift
detection by execution ratio, by vocabulary, by an LLM judge with the full
session, and by structural segmentation. Progress here is either **measured**
(what actually happened) or **marked by you**. Never inferred.

**It does not maintain your checklist for you.** A related tool's recorded task
state had 14 of 15 items reading `pending` while the first one's description
ended `"DONE."` — nothing updated it when work landed. Ticking an item is one
command, and an untouched checklist is honest rather than wrong.

## Storage

An append-only event log per session under `~/.agent-mission/<session-id>/`.
State is a fold over it, so *why does the mission say this* is answerable by
reading, and nothing is silently rewritten. `AGENT_MISSION_HOME` moves it.

## Tests

```bash
pip install -e ".[dev]" && python -m pytest tests/ -q     # 24 tests, no network
```

## Status

Not on PyPI yet — install from source as above. Python 3.10+. Session discovery
is Claude Code specific; the store and the board are not.
