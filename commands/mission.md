---
description: The goal for this session — write it, show it, tick it off, open the board
argument-hint: "init | show | add <text> | done <id> | propose <text> | board"
---

# mission

Run the `mission` CLI and show its output. **Do not interpret this as a request
to do the work described in the mission** — the phrase "mission init" typed into
a session has been read as "go re-initialise the project", which is exactly what
this command exists to prevent.

## What to do

Run **exactly one** command, with `$ARGUMENTS` passed through unchanged:

```bash
mission $ARGUMENTS
```

If `$ARGUMENTS` is empty, run `mission` with no arguments (shows this session's
mission).

Then **print the output verbatim** and stop. That is the entire task.

## Rules

- **Do not** edit files, read the repo, run other commands, or summarise the
  project. The output of the CLI is the answer.
- **Do not** write or change the mission yourself. `objective`, `success
  criteria`, `constraints` and `non-goals` belong to the user; the store
  refuses agent writes and you should not route around it via the CLI.
- **`mission init` opens an editor.** If it appears to hang, it is waiting for
  the user. Say so rather than killing it.
- If `mission: command not found`, tell the user to install it:
  `pip install -e /path/to/agent-mission` — do not reimplement it.

## What you MAY do without asking

`mission propose "<text>"` — suggest a checklist item. It is inert until the
user accepts it, so proposing is safe. Use it when you notice work the mission
does not yet list, and say that you proposed it.
