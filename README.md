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

`mission` writes the goal down once, keeps it somewhere the agent cannot edit,
and puts it back in front of you — on your statusline, after a compaction, and
on one page showing every session you have running.

![The mission board](docs/board.png)

## Install

```bash
git clone https://github.com/jlee844/agent-mission && cd agent-mission
pip install -e .
mission setup      # slash command, deny rules, statusline, re-anchor hook
```

`setup` **finishes the job** rather than printing instructions — if you already
have a statusline it writes a wrapper that runs yours *and* appends the goal,
keeping your original verbatim. It backs up your settings, appends to hooks
instead of replacing them, and refuses to touch anything unless a person is at
the keyboard.

```bash
mission setup --check     # which surfaces are live here; exits 1 if any is missing
```

Or install them from the board: run `mission board` yourself, enter the write
code, and a **Setup panel** lists the same five surfaces with an Install button
each — showing the exact settings change before it applies, and backing the file
up first. Both front ends call the same functions, so the board can never drift
from what the terminal would have done.

Re-running `mission setup` is always the complete fix. There is never a second
instruction to follow.

## Usage

```bash
mission init                 # interviews you, then writes it
mission                      # the goal, the plan, measured activity
mission whereami             # one line, for a statusline
mission detour "chasing a flaky test"
mission return               # replays the goal and the guards you set
mission board                # every live session on one page
```

```
Ship list sharing · 2/5 · detour: chasing a flaky test · 3 proposals waiting
```

**Three levels of authority.** This is the whole design.

| level | fields | who writes |
|---|---|---|
| 🔒 **protected** | objective, success criteria, constraints, non-goals | **you only** |
| 🟡 **proposed** | checklist, strategy | agent suggests, you accept |
| 🟢 **observable** | decisions, evidence, notes, detours | agent records freely |

The agent cannot author a protected field, accept its own proposal, or tick an
item — enforced in the store, at the terminal, and by Claude Code's own deny
rules. See [SECURITY.md](docs/SECURITY.md) for how, and for what that does
*not* cover.

## Commands

| command | what it does |
|---|---|
| `init` | interview and write the mission (`--from-file`, `--force --discard-plan`) |
| `show` | the goal, the plan, measured activity (`--all` includes finished) |
| `whereami` | one line for a statusline (`--full` for the ~10-line re-anchor) |
| `pending` | what awaits your accept, and the command that clears it |
| `add` | add an agreed item (`--under <id>`) |
| `propose` | suggest an item; inert until accepted (`--into <session>`) |
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
| `board` | the shared board (`--stop`; run it yourself for write buttons) |
| `setup --check` | which surfaces are installed, missing, or outdated |
| — | the board's Setup panel installs the same five, behind the write code |
| `setup` | slash command, deny rules, statusline, hooks |
| `version` | the build, and every command that exists right now |

## Goals move

```bash
mission set objective "Ship list sharing AND the invite flow" --session 69426e5a
mission set name "List sharing + invites"
```

Every edit is a new event, so the old value stays and `mission why objective`
still answers. The agent is refused on all of these. `--session` is in the first
example because you type these in **your own terminal**, where it is often
needed — see below.

## Missions are goals; sessions attach to them

A goal outlives the session that started it, and one session often serves
several goals in a day. So a **mission** is the thing with a name, and a
**session attaches** to it:

```bash
mission attach career          # this session is working on that goal
mission set objective "..." --on career    # from anywhere, any terminal
mission missions               # every goal, and the sessions that served it
```

**The name routes; the session only speaks.** `--on <name>` is the address. The
session id says *who is writing*, never *what is meant* — and the working
directory says neither.

That last part is deliberate. A session is opened where the work can *reach*
what it needs, which for a coordination repo is the root — while the goal lives
three folders down. **cwd tells you what a session can see, not what it is
for**, so it never routes a write.

The board now shows **one card per goal**, with the sessions that served it
listed inside and their measured activity summed. One goal spanning four
sessions used to read as four cards, each showing a slice.

```bash
mission migrate               # lift older session-keyed stores; safe to re-run
mission archive <name>        # take a finished goal off the board (--undo)
```

A finished goal keeps its log forever and stops competing for your attention.
Archiving is a statement about attention, not about history — one throwaway
experiment with four unaccepted proposals was outranking three live sessions.
On the board it is a hover control on the card header.

## Which mission does a command mean?

Inside Claude Code the session id is in the environment, so nothing needs
saying. In **your own terminal** — where every human-only command has to be
typed — it is not, so `mission` resolves it from the directory you are standing
in:

1. `--session` if you passed one. It takes a **mission name or an id prefix**,
   not only the full uuid.
2. The `CLAUDE_CODE_SESSION_ID` of the session you are inside.
3. The missions recorded for this directory, **counting ancestors** — you are
   usually in a subfolder of the session's root. Deepest match wins, so a
   mission opened in a subproject beats one opened at the repo root.
4. A tie **refuses**, and prints your own command back once per candidate:

```
several missions cover this directory — say which:

  Career hub, live locally  ·  active 11h ago
    mission accept --pending --session 69426e5a-a75f-448b-aa45-c3f80eabd2b1

  Mission board for live sessions  ·  active 18h ago
    mission accept --pending --session be17144b-d3be-41dd-a02a-c6ef71292e3f
```

It refuses rather than picking the most recent, because two sessions on one
directory is the normal case here and guessing would tick the wrong plan.
Breaking the tie on recency is guessing with extra steps.

## The board

`mission board` shows every running session: its goal, its plan as a tree, and
what it has **actually done** — calls, files, test runs, failures, read from the
transcript rather than reported by the agent. A strip at the top answers the
question you arrive with: *is anything waiting on me.*

Run it yourself in a terminal and it prints a write code, which turns on accept,
tick and note buttons. The agent never sees that code — that is the point, and
[SECURITY.md](docs/SECURITY.md) explains the mechanism.

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

- **Session discovery works two ways, and the second is fuzzy.** Inside Claude
  Code the id comes from the environment and is exact. In your own terminal it
  is matched from the working directory, and when several missions share a
  directory tree the command refuses rather than guessing — see above.
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
