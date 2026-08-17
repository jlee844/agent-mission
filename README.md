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

mission setup     # install the /mission slash command (once)
mission init      # write it — opens your editor, then opens the board
mission           # show it, with measured activity
mission board     # the shared board (starts it, or joins the one running)
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

**The plan is a tree.** An objective breaks into subgoals, subgoals into work.
Only leaves count — a subgoal is a container, so counting it as a task both
inflates the total and can never be ticked honestly. A branch shows the progress
of its children and is done when they are.

```bash
mission add "Wire the invite flow" --under e92721c7
```

## Use it from inside a session

```
/mission init
/mission add "port the list types"
/mission done 5005d9f8
```

`/mission init` **interviews you** — one question at a time, each with a
recommended answer so you can say "yes" and move on. Objective, then success
criteria (the one people skip), constraints, non-goals, and a proposed
checklist. It stops when a competent stranger could pick up the session and know
what to do and when to stop. The agent is the scribe; every line traces to
something you said.

**Install the slash command with `mission setup`.** Without it, typing
`mission init` into a session is read as an *instruction* rather than run as a
command — in testing, an agent took it as "go re-initialise the project" and
spent a long turn rewriting unrelated files. A slash command executes.

The command file also tells the agent it may `mission propose "..."` freely —
proposals are inert until you accept them — and that it must not author the
mission itself or route around the store's refusal via the CLI.

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

There are 53 tests and most of them guard exactly this.

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

**One board, not one per session.** `mission init` in a second session joins
the board already running and appears as another card — same URL, no second
server. The port is recorded and *probed*, because a recorded port whose
process died is worse than no record: it sends you to a dead URL.

A mission whose session has ended stays on the board, dimmed and marked
`ended`. The work happened; losing sight of it is what this exists to prevent.

Localhost only. Reads transcripts and the mission log; writes nothing else.

## Session health

Three facts about *how* a session ran, shown on each card. None of them is a
judgement about whether the work was right.

**Which model, and whether it changed.** Behaviour shifting after an update is
not imagination. In one real session the model went
`sonnet-4-6 → opus-4-8 → opus-5` mid-flight, and nothing surfaced it.

**Replies repeated verbatim.** The agent answering a turn it already answered.
Found in a real session at cosine 1.000 — 2,475 characters, byte-identical,
thirteen replies apart. Median similarity in that session was 0.22, so it is far
outside the noise. Numbers count as tokens, because "40/62" and "41/62" are
different answers and a word-only tokenizer calls them a repeat.

**Files two live sessions are both writing.** Nothing inside either session can
see the other, so this is only visible from a board that watches all of them. On
the machine this was built on, three sessions were editing the same file.

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
pip install -e ".[dev]" && python -m pytest tests/ -q     # 53 tests, no network
```

## Status

Not on PyPI yet — install from source as above. Python 3.10+. Session discovery
is Claude Code specific; the store and the board are not.
