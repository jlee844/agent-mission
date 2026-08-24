---
description: The goal for this session — interview the user to write it, show it, tick it off, open the board
argument-hint: "init | show | add <text> | done <id> | propose <text> | board"
---

# mission

**Do not interpret this as a request to do the work described in the mission.**
Typed as plain text, "mission init" has been read as "go re-initialise the
project" and the agent spent a long turn rewriting unrelated files. This command
runs a CLI.

## If `$ARGUMENTS` is anything other than `init`

Run exactly:

```bash
mission $ARGUMENTS
```

Print the output verbatim and stop. That is the entire task. Do not edit files,
read the repo, or summarise anything.

---

## If `$ARGUMENTS` is `init` — interview first, then write

A mission written in ten seconds is a wish. Interrogate until it is sharp, then
transcribe. **You are the scribe, never the author** — every line must come from
one of their answers.

### First, gather context (do this silently, one pass)

```bash
mission show 2>/dev/null || true
```

Skim the session so far: the first thing they asked, what files have changed,
what they last said. If `.planning/ROADMAP.md` or `.planning/PROJECT.md` exists,
read it — this session is probably a slice of that, and their own words there
beat anything you invent.

### Then grill — one question at a time

Ask **one** question per message. **Attach your recommended answer to each**, so
they can say "yes" and move on. Do not batch. Do not accept a vague answer —
push once, then take what they give you.

Work through, in order:

1. **Objective.** "What does *done* look like for this session?" A good one names
   a change in the world, not an activity. *"Ship list sharing to mobile"*, not
   *"work on lists"*. If they give you an activity, ask what it produces.

   Then **name it yourself** — three to six words, the way you would title a
   pull request. *"List sharing on mobile"*. Show them the name and let them
   correct it; do not make them invent one. **Never paste their prompt in as
   the name.** A card reading "yeah whats important now is to get at least
   something for every subpage we have and building the whole thin…" is a
   transcript fragment, not a title, and it is what happens when the seed is
   used verbatim.

2. **Success criteria** — the important one, and the one people skip. "How will
   you know it worked, without asking me?" Push for something checkable: a
   command that passes, a file that exists, a page that loads. *"It should feel
   good"* is not a criterion. Aim for two or three.

3. **Constraints.** "What must I not do while getting there?" Costs, files that
   are off limits, things that must keep working, anything that needs asking
   first. If they say "nothing", offer one you inferred from the session and see
   if it sticks.

4. **Non-goals.** "What is explicitly out of scope?" This is what makes scope
   creep visible later, so it is worth one real push. Name the adjacent thing
   they are most likely to be dragged into.

5. **The plan — as a tree, not a list.** Propose 2–4 **subgoals** that carry
   the objective, then 2–5 concrete tasks under each. A flat list of twelve
   items hides which piece a task belongs to, which is the first thing you want
   to know when several sessions are running. Ask them to cut, rename or
   re-parent rather than to write from blank.

**Stop when a competent stranger could pick up this session and know what to do
and when to stop.** Not before. If a question remains, it is not done.

### Then write it

Write the answers to a temp file in exactly this shape:

```
NAME: <three to six words, your wording, their approval>
OBJECTIVE: <their words>
SUCCESS:
- <criterion>
CONSTRAINTS:
- <constraint>
NON-GOALS:
- <non-goal>
CHECKLIST:
- <subgoal>
  - <task>          # two spaces nests it under the subgoal above
  - <task>
- <subgoal>
  - <task>
```

Indentation is the nesting. Only leaves count as work; a subgoal shows the
progress of its children.

Then run:

```bash
mission init --from-file <that file>
```

Show the output, which includes the board URL. Delete the temp file.

---

## The protocol: check the plan before sizable work

Injection is not behavior. Every agent reads the mission, mentions the goal,
and keeps polishing untracked anyway — because this file used to say what you
MAY do, never what you SHOULD do at a decision point. This section is the
should.

**Once per session**, in your first substantive reply, name the plan's next
accepted item in one line: *"plan's next: <item>."*

**Before sizable work, compare the request against the accepted plan and say
the judgment out loud, in one or two lines.** Three outcomes:

- **On the plan** → "this is <id>" and proceed.
- **Off-plan but more pressing** → say exactly that: "the plan's next step is
  X, but this is more pressing because <reason>" — then pick the right record
  BEFORE the work starts. The rule: **if it will be ticked when finished, it
  is a proposal; if it will simply be over, it is a detour.**
  - A deliverable the plan should carry → `mission propose "<it>"`, continue.
  - A broken tool, a flaky test, an environment fix, an investigation — work
    that serves the plan but is not plan-worthy →
    `mission detour "<what pulled you off>"`, do it, then `mission return`.
- **The plan seems righter** → push back once, with the reason: "the plan says
  X next, and I think X should come first because <reason> — want Y anyway?"
  The human's answer settles it. **One pushback, then comply** — never
  re-litigate a ruling in the same session.

**Never work untracked silently.** Every divergence lands as a proposal or a
declared detour before the work starts, not after. This is judgment in
conversation, not detection — nothing here infers anything.

## Keeping the board current while the work moves

A mission written once and never touched is a wish with a URL. Most of what
gets asked for arrives *after* `init`, and the board is only worth reading if it
caught it.

**When they ask for something new, propose it before you start it.**

```bash
mission propose "Fold finished items away on the board"
```

It lands as `[+]`, inert until they accept, and it is on the card from the
moment they asked rather than after you remember. Then do the work.

**What goes on the board, and what does not.** The test is whether the message
contains something that could be **true or false when you are finished**.

| they said | board? |
|---|---|
| "add a way to overwrite the mission" | **yes** — an instruction |
| "the name should not be a pasted prompt" | **yes** — a state to reach |
| "what do we have that Kiro does not?" | **no** — a question. Answer it |
| "is this approach stupid?" | **no** — asking for judgement |
| "ok so let's do the second one" | **yes** — the answer became an instruction |

A question is not a commitment. Answer it in the conversation; if the reply
turns into "do that", propose it *then*. Adding every question to the plan
inflates it with things that were never asked for, and a plan you stop trusting
is one you stop reading.

When you are unsure, propose it and say you were unsure. A `[+]` they delete
costs one command; work that never appears costs them the ability to check.

**State completion by naming the artifact.** A Stop hook checks completion
claims against the disk, and a claim that names its evidence is checkable on
any corpus, in anyone's phrasing:

> done: 237 tests pass (tests/test_claims.py)
> done: the resolver refuses ties (agent_mission/session.py)

Write `done: <what> (<artifact>)` when you finish something real — the
parenthesised path or file is what gets verified. Prose still works; it is
just checked with blunter tools.

**When you finish one, say which id and let them tick it.**

> Done: the fold, and the goal-editing command. `mission done 4a1c9e02 88ea49fc`

Several ids in one command, because six items is otherwise six commands and the
plan stops being maintained. **Never tick it yourself** — marking work complete
is their judgement, and the whole guarantee rests on it.

## The tool changes under you

This CLI is under active development and a long session holds a picture of it
from whenever it last looked. One session, working from an older picture, told
Jonathan to run `mission init --force` to fix a badly-worded objective — that
discards the whole plan, and `mission set objective` (which does not) already
existed.

So before advising a command you have not run this session:

```bash
mission version
```

It prints the build and every subcommand that exists right now. If something
there is unfamiliar, your picture is older than the tool.

## Attach the session to a goal, once

If `mission` says this session is not attached, ask them which goal it is —
list what it printed — and run `mission attach <name>` with their answer. One
question, one command, and every later `mission` in this session knows what it
is for.

A goal is not a session: several sessions serve one goal over its life, and a
session re-attaches when the work pivots. `--on <name>` addresses a goal from
anywhere without attaching at all.

## Say when you go off, and say when you come back

Drifting into a subgoal is normal work, not a failure. The failure is climbing
back up with nothing that says what the bigger goal was.

```bash
mission detour "chasing the flaky test in store.py"
mission return
```

**You may run both freely** — recording is not deciding, and this is the honest
channel for a side quest you would otherwise take silently. `return` prints the
objective, the constraints, and the item you were on, which is the reminder the
person actually needs at that moment. Nesting is fine.

Do not infer a detour from what you are doing. Declare one when you choose to
go off, or leave it alone. And the tick rule from the protocol applies here
too: a detour is work that will simply be *over* when done — if the plan
should carry it as a tickable item, it is a proposal, not a detour.

## Composing a command they have to run themselves

`set`, `accept`, `done`, `remove` and `add` are denied to you by the harness —
and the deny patterns are prefix matches, so `mission accept --help` is blocked
too. To read the flags without running anything:

```bash
mission help accept
```

**Always include `--on <name>`.** They type these in their own terminal, where
nothing says which goal is meant — no session id, and the working directory is
not consulted. A command without it refuses and makes them paste twice.

## Never report the plan from memory

The human accepts, ticks and commits **in their own terminal**, where you cannot
see it — the CLI refuses those commands to you by design. So anything you know
about what is done went stale the moment you said it.

Before telling them what is finished or still open, in the same turn:

```bash
mission show
```

Restating a status from earlier in the conversation is the one way this tool
actively misleads: they act on what you say, and you are describing a board that
has since changed. If a check is genuinely too expensive to repeat, say what it
was true *as of* rather than stating it flatly.

## Rules that hold either way

- **The mission is theirs.** Objective, success criteria, constraints and
  non-goals are protected: the store refuses agent writes, and you must not
  route around that by inventing answers and passing them through the CLI.
  Every line traces to something they said.
- **`mission init` without `--from-file` opens an editor.** If it seems to hang,
  it is waiting for them. Say so rather than killing it.
- If `mission: command not found`, tell them to install it. Do not reimplement it.
- **Goals move.** When they say something that changes the mission, offer
  `mission set objective "..." --on <name>` or
  `mission set success-criteria "a|b" --on <name>` rather
  than letting the card go stale. Say what you are about to change and let them
  confirm — it is still their field.
- You **may** run `mission propose "<text>"` at any time without asking —
  proposals are inert until accepted. Use it when you notice work the mission
  does not list, and say that you did.
