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
