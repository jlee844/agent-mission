# mission

**The goal that outlives the detours — the agent cannot quietly rewrite it, and
you cannot quietly forget it.**

[![tests](https://github.com/jlee844/agent-mission/actions/workflows/tests.yml/badge.svg)](https://github.com/jlee844/agent-mission/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.9%20–%203.14-0E6E68)](pyproject.toml)
[![deps](https://img.shields.io/badge/dependencies-none-0E6E68)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-666)](LICENSE)

You start a session with something in mind. Two hours later there are 800 tool
calls, a summary written by the thing being summarised, and no easy answer to
*what was this for, and is it done?*

```bash
git clone https://github.com/jlee844/agent-mission && cd agent-mission
pip install -e .
mission setup      # slash command, deny rules, statusline, re-anchor hook, attention hook
```

## You touch this in three places

**1. Answer the interview, once.** In Claude Code, `/mission init` asks what done
looks like, how you will know, and what you must not do on the way — one question
at a time, each with a suggested answer you can just say yes to. Your agent
transcribes; it does not author. (Outside Claude Code, `mission init` opens the
same fields in your editor.)

**2. Glance at your statusline.** The goal, the count, the detour you are on —
without leaving what you are doing.

```
Ship list sharing · 2/5 · detour: chasing a flaky test · 3 proposals waiting
```

And when something *newly* needs you — a proposal just landed — one line
arrives in the conversation itself, with the link. Edge-triggered: it fires on
the change, never repeats, and is silent otherwise.

**3. Click on the board.** `mission board` puts every session you have running
on one page: its goal, its plan as a tree, and what it has **actually done** —
calls, files, test runs, failures, read from the transcript rather than reported
by the agent. A strip at the top answers the question you arrive with: *is
anything waiting on me.* Run it yourself and it prints a write code, which turns
on the accept, tick and note buttons; the agent never sees that code.

![The mission board](docs/board.png)

Everything else here is for the agent, or for scripting.
**Humans use the board; agents use the CLI.**

### Three levels of authority

| level | fields | who writes |
|---|---|---|
| 🔒 **protected** | objective, success criteria, constraints, non-goals | **you only** |
| 🟡 **proposed** | checklist, strategy | agent suggests, you accept |
| 🟢 **observable** | decisions, evidence, notes, detours | agent records freely |

The agent cannot author a protected field, accept its own proposal, or tick an
item — enforced in the store, at the terminal, and by Claude Code's own deny
rules. See [SECURITY.md](docs/SECURITY.md) for how, and for what that does
*not* cover.

---

# Agent & scripting reference

## Setup

`setup` **finishes the job** rather than printing instructions — if you already
have a statusline it writes a wrapper that runs yours *and* appends the goal,
keeping your original verbatim. It backs up your settings, appends to hooks
instead of replacing them, and refuses to touch anything unless a person is at
the keyboard.

```bash
mission setup --check     # which surfaces are live here; exits 1 if any is missing
```

Or install them from the board: enter the write code and a **Setup panel** lists
the same surfaces with an Install button each — showing the exact settings change
before it applies, and backing the file up first. Both front ends call the same
functions, so the board can never drift from what the terminal would have done.

Re-running `mission setup` is always the complete fix. There is never a second
instruction to follow.

Two surfaces are opt-in, never installed by default: `mission setup
--auto-board` appends one guarded line to your shell rc so the board starts
**writable** at login (one code paste per day, buttons thereafter), and
`--notify` turns on an OS notification when a proposal lands — edge-triggered,
at most one per 10 minutes, off unless you know you ignore terminals.

## Commands

| command | what it does |
|---|---|
| `init` | interview and write the mission (`--from-file`, `--force --discard-plan`) |
| `show` | the goal, the plan, measured activity (`--all` includes finished) |
| `whereami` | one line for a statusline (`--full` for the ~10-line re-anchor) |
| `pending` | what awaits your accept, and the command that clears it |
| `add` | add an agreed item (`--under <id>`) |
| `propose` | suggest an item; inert until accepted (`--on <goal>` for another's plan) |
| `accept` | accept proposals (`--pending`, `--under <id>`, or ids) |
| `done` | tick agreed work (`--under <id>`, or ids) |
| `remove` | drop items and their subtrees |
| `detour` / `return` | declare a side quest; return replays the goal |
| `set` | change a protected field, keeping the plan and the history |
| `help <command>` | usage for one command, without running it |
| `why <field>` | when it changed, to what, and who typed it |
| `import <file>` | land an external plan as proposals; diffs on re-import |
| `delegate <id>` | give one accepted item its own session for a subagent |
| `observe <field> <text>` | record evidence, a decision, or a note |
| `doctor` | what is wrong with the missions themselves, not the install |
| `signal` | one line if something newly awaits you; silent otherwise (for hooks) |
| `attach` | point this session at a goal |
| `missions` | every goal, and the sessions that served it |
| `archive` | take a finished goal off the board (`--undo`) |
| `migrate` | lift older session-keyed stores; safe to re-run |
| `board` | the shared board (`--stop`; run it yourself for write buttons) |
| `setup` / `setup --check` | install the surfaces; check which are live |
| `version` | the build, and every command that exists right now |

## Goals move

```bash
mission set objective "Ship list sharing AND the invite flow" --on list-sharing
mission set name "List sharing + invites"
```

Every edit is a new event, so the old value stays and `mission why objective`
still answers. The agent is refused on all of these — you type them in **your
own terminal**, which is why the goal is named: nothing there knows which
session you meant.

## Missions are goals; sessions attach to them

A goal outlives the session that started it, and one session often serves
several goals in a day. So a **mission** is the thing with a name, and a
**session attaches** to it:

```bash
mission attach career          # this session is working on that goal
mission set objective "..." --on career    # from anywhere, any terminal
mission missions               # every goal, and the sessions that served it
```

**Names route, sessions speak, directories inform.** `--on <name>` is the only
address — it reaches goals in either storage layout, so nothing has to be
migrated first. The session id in your environment says *who is writing*, never
*what is meant*. The working directory says neither, and is not consulted: a
session is opened where the work can *reach* what it needs, which for a
coordination repo is the root, while the goal lives three folders down. That
router existed once and cost a plan — a career objective landed on an unrelated
mission and renamed it, because both were opened at the same root. When nothing
names a goal, `mission` refuses and lists yours, each with the command that
addresses it.

The board shows **one card per goal**, with the sessions that served it listed
inside and their measured activity summed. One goal spanning four sessions used
to read as four cards, each showing a slice.

```bash
mission migrate               # lift older session-keyed stores; safe to re-run
mission archive <name>        # take a finished goal off the board (--undo)
```

A finished goal keeps its log forever and stops competing for your attention.
Archiving is a statement about attention, not about history — one throwaway
experiment with four unaccepted proposals was outranking three live sessions.
On the board it is a hover control on the card header.

## One board, and where it lives

**One board per store, and only one.** Starting a second finds the first and
points you at it rather than splitting the truth in two — two boards for the
same missions is two answers to "what is the state", and the one you happen to
be looking at is whichever won the port.

It lives at `127.0.0.1:8976` and **stays there**: the port is the lock and the
record is only a cache, so a restart returns to 8976, a board whose record was
lost is adopted rather than duplicated, and only a foreign process on that port
can push it elsewhere. `~/.agent-mission/board.html` is a bookmark that always
points at the live one — and says the board is down rather than sending you to a
dead URL.

## Limitations

- **Outside Claude Code you have to name the goal.** Inside it, the session id
  is in the environment and its attachment answers everything. In your own
  terminal nothing does, so `--on <name>` is required — and it autocompletes
  nothing, which is why `init` asks for a short name you will recognise. The
  refusal lists your goals with the command for each, so it costs a paste.
- **Progress is measured or marked, never inferred.** There is no drift
  detection here, on purpose — [DESIGN.md](docs/DESIGN.md) says what was tried.
- **The event log is a plain file.** An agent with shell access can append a
  forged event. The guarantee is no *silent* rewrite through this tool's
  interfaces, not tamper-proofness.
- **Not on PyPI.** Install from source.

## Docs

- [DESIGN.md](docs/DESIGN.md) — why it is shaped this way, and the five dead drift detectors
- [SECURITY.md](docs/SECURITY.md) — what is enforced, and the honest limits

## Part of a set

Four small tools that read what an AI coding session actually did, rather than
what it said it did.

| | |
|---|---|
| **mission** *(you are here)* | the goal, beside the work |
| [**receipt**](https://github.com/jlee844/receipt) | what a session did, what it cost, which claims are backed |
| [**blindspot**](https://github.com/jlee844/blindspot) | which changed lines a test would actually catch a bug in |
| [**transcript-audit**](https://github.com/jlee844/transcript-audit) | profile a transcript corpus before computing anything over it |

MIT.
