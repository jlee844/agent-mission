# Working on agent-mission

Read `commands/mission.md` for the agent contract (which verbs are yours).
This file is the shorter, uglier document: **mistakes already made here, so
the next session does not pay for them again.** Every entry below cost real
time once.

## Filing a claim

The format is strict and it fails SILENTLY in three different ways.

- **The literal token is `done:`.** `claims-done <id> "done NOT as proposed:
  …"` parses nothing, because `_CLAUSE` looks for `done:`. Write `done: not
  as proposed — …` instead.
- **Only the last parenthesised group within 400 characters of `done:` is
  read.** A 462-character claim with its path at the end came back "nothing
  checkable". Put the path early, or keep the claim short.
- **Paths resolve from the GOAL's cwd, not your shell's.** Working inside a
  repo that sits below the goal's root, a repo-relative path comes back
  unbacked. Prefix it.
- **Claim only when EVERY clause of the item is met.** An item asked for
  short labels on "every surface"; it was claimed after `show` alone, the
  human correctly confirmed exactly what was claimed, and the remaining
  surface landed after the item was closed with nothing to attach to. If you
  find a gap after a tick, the fix is a **new proposal** naming the gap and
  the ticked item — not a note, which nobody is asked to rule on.
- **A `path:line` citation used to be dropped** and is now stripped to the
  path. If you add another citation shape, test it: the failure mode is a
  claim that reads as "you cited nothing" when you cited something.

## Verifying, before you report

- **Do not truncate tool output you are about to reason about.** A claim
  failure printed the exact cause — "this claim is 462 characters; only the
  first 400 after `done:` are parsed" — and a `| head -3` cut it off. The
  next five minutes went into blaming the parser. The guard was working.
- **Check your probe before you doubt the code.** `verdict_for("x (a.py)")`
  returns False because the text has no `done:`; that is the probe being
  wrong, not a bug. Reproduce through the CLI first.
- **A grep hit is a candidate, not a finding.** Three items looked built
  because a symbol existed; each had several clauses and needed reading.
- **Unit tests on a new store field do not prove a human can reach it.**
  Eight passing tests covered a criterion amendment that was invisible in
  `pending` and in `accept --pending`, so it could never be accepted. A
  proposal nobody can see is worse than none: the agent reports filing it.
  Probe the end-to-end path through the CLI.
- **A flag that suppresses failure hides it from you too.** `git mv -k`
  skips errors silently: the pane folder was untracked, git refused the
  move, `-k` returned success, and the `|| mv` fallback never ran. The
  resulting wheel held zero pane files, and only listing the wheel showed it.
- **Prove packaging by building and installing, never by reading the
  config.** A `pip install` carries only the package; the first move of the
  pane left it beside the package, where it would not exist after install.
  Build the wheel, install it in a clean venv, and check the path exists.
- **The `claude` on PATH can be older than the engine you run on.** One was
  2.1.170 under a 2.1.308 session: it had no `plugin test` and its validator
  rejected the current `hooks.json` format, which reads exactly like a broken
  mod. `tests/test_pane_mod.py` finds a claude that has the command.
- **A test nothing runs is a claim.** The pane's tests were written, never
  run, and run once with the wrong runner (`vitest` cannot resolve
  `claude-code/testing`). Through the real runner they failed 2 of 2. If a
  test needs a special runner, wire it into the suite.
- **The suite runs on Python 3.9.** A runtime annotation such as
  `str | None` fails to import there; use `from __future__ import annotations`.
- **Validate a command name against what EXISTS, not against what something
  else references.** A doc was written telling sessions to run a verb that
  had never been implemented.

## Changing behaviour

- **A test that pins the opposite of your change is a prior ruling.** Read
  why before overriding it. Two cases here: the re-anchor message once
  advised creating a goal and caused duplicate goals, and `init --on <new>`
  re-attaching the session is a deliberate promise ("one session, several
  goals"), not the bug it was reported as. In the second case the real defect
  was that it happened in silence.
- **If you relax a guard, keep its protection as structure.** The duplicate
  goal guard became ordering plus a human-asks-first condition, with both
  failure modes recorded in the test's docstring, rather than a deletion.
- **Two copies of one rule diverge.** `needs_you` was computed in two
  functions with different sets, so a goal with a claim in review needed
  attention in one surface and not the other. When you find a second copy,
  make it one function.
- **One copy of the pane, shipped with the tool.** It lives in
  `agent_mission/pane/` and `mission setup` loads it in every session. Never
  write a second in a session's mod folder: the first pane was exactly that,
  loaded only where it was written, and it carried its own copy of the label
  rule that had already diverged from the board's.
- **Another session is reached through the board, not directly.** Read its
  state from `snapshot`/`queue`; ask it for work by putting an item on its
  goal; report results on the board. See `commands/mission.md`.
- **A new agent-runnable command must be documented** in
  `commands/mission.md` or exempted with a reason, and listed in the README
  table. Two contract tests enforce it.
- **The privacy test is the one that fires most.** It reads docstrings and
  comments, not just code. Do not put a person's name, a private project
  name, or a real session id in a comment you are writing about why a bug
  happened.

## The store

- **Tests must never touch the real store.** A smoke test once wrote two
  items into a live goal, recorded `by=human`, and they sat there for weeks.
  `tests/conftest.py` now points `AGENT_MISSION_HOME` at a tmpdir per test
  and asserts the real store is untouched; do not work around it.
- **Never leave test writes on a live store.** If a demonstration ticks,
  claims or proposes something real, undo it in the same session and re-read
  the file to confirm.
- **An id-shaped field is not always the id you want.** Resolving a session
  returns the SESSION, not the goal it is attached to; use the attachment
  map.
