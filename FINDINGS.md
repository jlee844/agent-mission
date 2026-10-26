# What 56 days of real use measured

This project was built to stop a coding agent marking its own work done. After
eight weeks I measured my own store instead of describing the design. The
headline is that the authority gate's value is **conditional on whether the
success criterion is falsifiable**, and where it is not, the human's ruling
carries almost no information.

Nothing here is a projection. It is a fold over `~/.agent-mission`:
**1,124 events, 11 goals, 2026-08-17 to 2026-10-12.**

## Method

Every event in every `missions/*/events.jsonl`, counted by `kind`, `by`,
`typed_by` and `context_via`. Rates are computed over **distinct item ids**,
not event counts, and **agent proposals are separated from items the human
added**. Both corrections mattered — see Caveats.

## 1. The gate is close to a rubber stamp on real work

⚠️ **This section replaces an earlier version of itself, and the correction
is the finding.** The first draft reported a bimodal result — 24% acceptance
on one goal against 100% on others — and concluded that the gate's
selectivity is inherited from whether the success criterion is falsifiable.
That was an artifact. **Six of the eleven goals in this store are TEST
FIXTURES**, identifiable by session ids that are not session ids
(`mltest-s`, `hardtest`), and the two "selective" goals were both fixtures.
Their declines were test behaviour, not a human ruling.

On the five real goals:

| | |
|---|---|
| Agent proposals | 224 |
| Accepted by the human | **209 (93%)** |
| Never accepted | 15 |
| Explicitly declined | 1 |

Per goal: 87%, 90%, 100%, 100%, 100%. No goal filters hard.

**So the authority gate did not demonstrate behavioural control.** One
decline in 224 proposals is not a filter; it is a record of agreement. That
does not make the record worthless — see §4 — but any claim that this tool
*changes what gets built* fails on its own data.

⚠️ The number was computed three times before it was right: 98% (counting
items the human added via `add`, plus duplicate accept events), then 85%
(correct arithmetic, contaminated population), then 93% (real goals only).
Every error was in the POPULATION, not the arithmetic. The repository next
door — `transcript-audit` — exists to profile a corpus before any statistic
is computed over it, and it was never pointed at this store.

## 2. Acceptance is bimodal: fast or never

Propose → accept latency over 292 matched pairs: **median 1.6 h**, p90
**143 h (6 days)**, max **676 h (28 days)**. 36 proposals were never accepted
at all.

So the queue does not drain steadily. Either I ruled within the session, or the
item sat indefinitely. This is the behaviour the signal hook and the board were
built to fix, and the tail says they only partly did.

## 3. A headline feature was never used once

`detour` and `return` — declaring a side quest and coming back, a documented
feature and a clause of the agent protocol — fired **0 times in 1,124 events**.

The work diverged constantly over eight weeks. Nobody ever declared it. That is
the strongest single piece of evidence against building drift detection here:
**a detector needs a maintained plan to compare against, and the plan was
accepted wholesale and then overtaken.** Pointed at this store, a drift
detector would alarm on everything.

## 4. The claim loop did run, and it is the part that worked

| | |
|---|---|
| Completion claims by agents | 131 |
| Human rulings on those claims | 131 |
| Findings (claim rejected with a reason) | 2 |
| Second-session agreements (`checked`) | 3 |
| Leases taken | 14 · released 5 |
| Writes through the board | 84 |
| Protected-field changes (`set`) | 50 |

Every claim got a human verdict — none aged out, none auto-promoted. The
verifier caught real errors repeatedly, including two of mine in October (a
repo-relative artifact path, and a claim whose named directory proved nothing).

But session-to-session routing is **barely exercised**: 14 leases, 5 releases,
2 findings. It is built and tested; it is not validated by use.

## 5. Usage collapsed after the build month

Human-authored events: **515 in August, 33 in September, 84 in October.**

I built the instrument and then largely stopped using it. Any claim about this
tool changing behaviour has to survive that number, and it does not.

## Caveats

- **Two corrections to my own first pass, both of which inflated the result.**
  I first reported 98% acceptance. That counted the 84 items *I* added via
  `add` (which writes a propose **and** an accept, both as the human) as if
  they were agent proposals, and counted 31 duplicate `accepted` events across
  7 items. On distinct agent proposals the figure is 85%.
- `typed_by` is absent on 338 events and `context_via` on 626 — both fields
  postdate the earliest goals, so the surface breakdown covers roughly the
  later half of the history only. Kind, `by`, and the item-level rates are
  complete.
- n = 1 user, one machine, one model family. Every number is about my own
  habits, not about agents in general.
- "Never accepted" is not "rejected". A pending item may be waiting, stale, or
  forgotten; the store cannot tell these apart, and that ambiguity is itself a
  finding.

## Conclusion

The authority model did **not** demonstrate behavioural control, and the
pending-tail and zero-detour numbers show why: the plan was not maintained as
a live document, so there was little for a gate to gate.

What the eight weeks did produce is an **attributed, replayable record** —
1,124 events, 50 goal revisions, every write carrying who authored it, who
typed it and which surface it came through — and a **filesystem-backed claim
check** that caught real errors including the author's.

That is the project, stated at the size the evidence supports. The log's most
defensible job is not prevention: it is to brief an independent verifier
cheaply, because a 1,124-event history folds into a page.
