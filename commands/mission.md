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

5. **Checklist.** Propose 3–7 concrete items from everything above and the
   session so far. Ask them to cut or reorder rather than to write from blank.

**Stop when a competent stranger could pick up this session and know what to do
and when to stop.** Not before. If a question remains, it is not done.

### Then write it

Write the answers to a temp file in exactly this shape:

```
OBJECTIVE: <their words>
SUCCESS:
- <criterion>
CONSTRAINTS:
- <constraint>
NON-GOALS:
- <non-goal>
CHECKLIST:
- <item>
```

Then run:

```bash
mission init --from-file <that file>
```

Show the output, which includes the board URL. Delete the temp file.

---

## Rules that hold either way

- **The mission is theirs.** Objective, success criteria, constraints and
  non-goals are protected: the store refuses agent writes, and you must not
  route around that by inventing answers and passing them through the CLI.
  Every line traces to something they said.
- **`mission init` without `--from-file` opens an editor.** If it seems to hang,
  it is waiting for them. Say so rather than killing it.
- If `mission: command not found`, tell them to install it. Do not reimplement it.
- You **may** run `mission propose "<text>"` at any time without asking —
  proposals are inert until accepted. Use it when you notice work the mission
  does not list, and say that you did.
