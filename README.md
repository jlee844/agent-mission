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

  PLAN  2/5
    [ ] 627d52c7  Mobile port   1/2
    └─ [ ] a91922a1  Wire AddToListSheet
    [ ] f87a7497  Regenerate PARITY.md
    [?] 660974c9  Add a caching layer
    (2 finished, hidden — `mission show --all`)

    1 proposed, awaiting your accept

  MEASURED SO FAR
    527 tool calls · 42 files changed · 141 test runs · 19 failed calls
```

`[?]` is an agent proposal. It is inert until you accept it.

**Finished work sinks and then folds away.** What is left is what you act on, so
it should not have to be found among ticked boxes. Order is unfinished-first at
every level, and the order you wrote survives inside each group. `--all` brings
the finished items back; on the board they are one click away under `▸ FINISHED`.

**The plan is a tree.** An objective breaks into subgoals, subgoals into work.
Only leaves count — a subgoal is a container, so counting it as a task both
inflates the total and can never be ticked honestly. A branch shows the progress
of its children and is done when they are.

```bash
mission add "Wire the invite flow" --under e92721c7
mission remove e92721c7          # drops it and its subtree
```

Removal is soft: the event log keeps it. Append-only is about not losing
history, not about being unable to change your mind — a plan you cannot prune
stops being a plan.

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

## Bring a plan you already have

Every planner in this space ends at a markdown file — `writing-plans` writes to
`docs/superpowers/plans/`, GSD to `.planning/ROADMAP.md`, Kiro to
`.kiro/specs/<feature>/tasks.md`. They are good at decomposition and they all
stop at a file nothing reads back.

```bash
mission import docs/superpowers/plans/2026-08-17-list-sharing.md
mission import .kiro/specs/lists/tasks.md --under 56fa8644
```

Headings and nested checkboxes become the tree, and it lands as **proposals**.
Fenced code is skipped, links flatten to their text, and the numbering the
source carries (`2.1`, `Task 3:`) is dropped — renumber a plan and every item
would otherwise look new.

**Re-importing diffs.** New rows go up, rows already in the plan are left alone,
and rows that vanished from the file are reported with `--strict` and **never
removed** — removing is your call, and a tool that silently prunes the plan
because a file changed is one you stop trusting with the plan. That is what
makes a replan cheap: change the file, import again, read the difference.

**A `[x]` in the source is imported unticked.** The file says it is finished;
the file is not you. The count is reported so you can tick them in one command
if the source was right.

## Hand one item to a subagent

A subagent has no session id of its own — it runs inside its parent's — so its
work is invisible on the board and it has to be handed an id invented by hand.
It also does not need the whole mission. It needs one item, and the limits that
still apply.

```bash
mission delegate 5a182a09 --to "logreg baseline"
```

That makes a child mission whose objective is **copied verbatim** from an item
you already accepted. Constraints and non-goals carry down, because a limit that
stops applying to a subagent is not a limit. Success criteria stay with the
parent: a slice of the work does not get to decide the whole mission is
finished. The parent's plan then shows `→ <child> 2/4` beside that item.

**The agent may run this**, because it authors nothing. It may not delegate an
*unaccepted* proposal — otherwise it could propose an item and immediately
delegate it, turning its own suggestion into a goal.

## Goals move

```bash
mission set objective "Ship list sharing AND the invite flow"
mission set name "List sharing + invites"
mission set success-criteria "lists sync both ways|invites accepted end to end"
```

A mission you cannot edit is one you abandon and rewrite from scratch. Every
edit is a new event, so the old value stays in the log:

```bash
mission why objective        # when it changed, and to what
```

The agent is refused on all of these exactly as before.

## It survives compaction

The mission lives in `~/.agent-mission/<session-id>/`, not in the model's
context. Compact the conversation and the agent forgets the discussion; the
objective, the criteria and the tree are still on the board, and `mission` still
prints them. That is the point of writing it down.

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

There are 97 tests and most of them guard exactly this.

**The honest scope of that guarantee.** The store has always refused agent
writes. The *CLI* could not tell who typed the command, so it passed
`by="human"` unconditionally — and in a test, a subagent handed the CLI rewrote
a protected objective on its first try. `mission why` recorded the change as
mine. It also accepted its own proposal. The README disclosed this as a known
limit at the time; disclosure stopped nothing.

So human-only commands now require a **controlling terminal**:

```
$ mission set objective "something else"      # from an agent's shell
  the objective is yours, and this is not a terminal — refusing.
  If you are an agent: `mission propose "..."` instead.
```

An agent's shell has no tty; a person typing in one does. `set`, `accept`,
`done` and `remove` are gated on it, with `AGENT_MISSION_I_AM_HUMAN=1` for
pipelines and CI.

**The rule that actually holds.** `mission setup` also offers to add four deny
rules to `~/.claude/settings.json`:

```
Bash(mission set:*)  ·  Bash(mission accept:*)
Bash(mission done:*) ·  Bash(mission remove:*)
```

Now the harness refuses the call before this code runs, so there is nothing
left inside the tool to talk past — and it costs the person nothing, because
they type those in their own terminal anyway. The agent keeps `propose`,
`delegate`, `observe`, `show` and `import`.

Writing those rules is itself gated on a terminal, and **not** on `--force`:
that flag means "overwrite the command file", and letting it also wave through
a settings edit is how a narrow escape hatch becomes a wide one. It did, once.

> **The tty check alone is not a security boundary.** An agent can set that variable. What it
> changes is the *default*: impersonation went from what happens on the first
> try to something that takes a deliberate lie — which is the threat this
> design actually claims to address. Proving a human is present needs a
> confirmation the agent cannot supply, and that is not built.

The refusal always names `propose`, so the agent keeps a way to be useful. A
guardrail with no path around it gets routed around.

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
pip install -e ".[dev]" && python -m pytest tests/ -q     # 97 tests, no network
```

## Status

Not on PyPI yet — install from source as above. Session discovery is Claude
Code specific; the store and the board are not.

**Python 3.9 through 3.14**, all 97 tests passing on each. The floor is 3.9
because that is the Python macOS ships: the package originally declared 3.10+,
which would have told a user on stock macOS Python that it was unsupported
while it ran fine.
