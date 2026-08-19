# Design notes

Why `mission` is shaped the way it is. The README says what it does; this says
what was tried, what failed, and which decisions are load-bearing.

## Measured or marked, never inferred

This project is the surviving half of one that failed. Five detectors were built
to spot an agent drifting off task — execution ratio, vocabulary shift, an LLM
judge over a 40-turn window, the same judge over a 195-turn arc with the real
success criteria, and structural segmentation. Each was scored against nine real
moments where a human had to step in and redirect. Each had its prediction and
its kill condition written down **before** the money was spent. Total: $1.10.

All five failed. The reason is the useful part: **the drift was on-topic the
whole time.** The contradicting pair in the corpus sat at the **91st percentile
of similarity** within its own session, so clustering, PCA and anomaly detection
are structurally wrong for it — they rank the contradiction as the most normal
thing there.

So nothing here guesses. Progress is either **measured** (read from the
transcript: calls, files, test runs, failures) or **marked by a human**. A
detour is **declared**, not detected. There is no similarity score anywhere in
this tool, and adding one would be a regression.

## The reminder, not the enforcement

Drifting into a subgoal is normal work. The failure is climbing back up with
nothing that says what the bigger goal was — and that failure belongs to the
person as much as the agent.

Which is why the surfaces matter more than the storage. The mission survived
compaction on disk for a week before anything put it back in front of anyone.
`whereami` on a statusline, and a `SessionStart` hook after a compaction, fire
at the two moments memory actually fails.

`mission return` replays the objective, the constraints and the item you left,
because the guards you set at the start are exactly what you have forgotten by
the time you come back.

## Friction is a design problem, not a user problem

Measured across five real missions: **152 proposals, 91 accepted, 61 left
pending — 40%.** Accepting cost a person retyping an eight-character id per
item. The worst single case was a 25-id command that failed and had to be
redone by hand eight minutes later.

The answer was never to loosen the gate. It was `--pending`, `--under`, and
`mission pending` printing the command that clears it.

The same lesson, twice more: proposals arrived in **8 catch-up bursts** rather
than when the work was asked for, and `mission add` printed an id and the text
but never *which mission* it landed on — so two items meant for another project
sat on the wrong card unnoticed.

## Importing, and why it diffs

Every planner in this space ends at a markdown file: `writing-plans` writes to
`docs/superpowers/plans/`, GSD to `.planning/ROADMAP.md`, Kiro to
`.kiro/specs/<feature>/tasks.md`. They are good at decomposition and they all
stop at a file nothing reads back.

`mission import` lands that file as proposals, and **diffs on re-import** — new
rows go up, existing rows are left alone, vanished rows are reported and never
removed. A tool that silently prunes your plan because a file changed is one you
stop trusting with the plan.

A `[x]` in the source imports **unticked**. The file says the work is finished;
the file is not you.

## Board decisions

**One accent.** Colour means "this is waiting on you" and nothing else. It
previously also meant a failed tool call and a health warning — three unrelated
facts competing for one alarm, so none of them read as urgent.

**Finished work sinks, then folds.** What is left is what you act on; it should
not have to be found among ticked boxes.

**Delegated missions attach to their parent.** One session under test produced
five cards, four of them its own children.

**Unaccepted proposals do not count toward progress.** Counting them let an
agent move your progress backwards by making suggestions — one real board read
`8/10` with every agreed task finished and two suggestions outstanding.

## Things deliberately not built

- Drift detection, in any form. Closed with pre-registered stopping rules; see above.
- Auto-ticking or auto-accepting. Marking work done is a judgement.
- An MCP interface. Tool schemas load into context every session; a CLI costs
  nothing until called.
