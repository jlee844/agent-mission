> # ⚠️ RETRACTED 2026-10-11 — DO NOT QUOTE THESE NUMBERS
>
> A second reader checked this document the day it was written and found
> nine passages that do not hold. Both headline figures are mis-stated and
> the ground truth is weaker than the conclusions drawn from it. The text
> below is kept UNEDITED as the record of the error, not as a result.
>
> **Nothing in this file may be cited in an interview, a README, a
> walkthrough pack or a commit message.** It was written to be quoted, which
> is exactly why it has to carry this warning rather than be quietly fixed.
>
> What fails. **Line numbers count from the `# How often is the claim
> verifier right?` heading below, not from the top of the file** — this
> header is prepended, so it shifts every body line by its own length.
>
> | where | what is wrong |
> |---|---|
> | 26, 97, 100-101 | "1 of 16" counts ARTIFACTS, not the 14 accused CLAIMS. The headline mixes the two units, and line 97 then says 16 claims. |
> | 56 | "Recall on reassurance: 11 of 12" is the PRECISION of `ok`, not recall. Recall needs the false negatives — claims that were really done and were called unbacked — and those were never labelled. |
> | 87 | **An omission, not a false claim.** Line 87 correctly says "12 of 58 `ok` verdicts were labelled". What the document never does is total the ~26 labels (12 + 14) against the ~40 the work asked for, or say that **0 of the 57 uncheckable claims** were labelled at all. |
> | 29-30, 89-91 | The ground truth is an existence check — "resolving the named path against the obvious repository roots" (29-30), later admitted as "exists somewhere obvious" (89-91) — not "was the work done", which is what the summary asserts. **And the labelling was not blind: the labeller saw each verdict before labelling it.** |
> | 32 | The units are inconsistent between sections. |
> | 89-91 | No git-log timing was performed, so "changed since the work was agreed" was never independently verified. |
> | 15-20 | The verdict table has no `too_broad` row, so it predates the evidence rule it is measuring. |
>
> A sound version needs a BLIND set of ~40 claims — labelled without seeing
> the verdict — with git-log timing, and the uncheckable claims in the
> denominator. Until that exists this document asserts a precision it did
> not measure.
>
> ⚠️ The first version of THIS header was itself wrong in three places,
> caught by the same reviewer: it accused line 87 of claiming "~40 labelled"
> when that figure came from the proposal's ask and not from the document, it
> quoted lines 29-30 using text from 89-91, and its line numbers were off by
> the header's own length. A retraction that misquotes the thing it retracts
> is the original defect wearing a different hat, so it is recorded here
> rather than silently corrected.
>
> Review: `projects/outputs/agent-mission-review-2026-10-11.md` §7a, §1.

# How often is the claim verifier right?

Measured 2026-10-11 against every `claims_done` event in the maintainer's
store: **129 claims across 4 goals**, 52 days. The verifier has reported a
verdict on each one since C17. Nothing here measures whether the verifier is
*useful*; it measures whether it is *correct*, which had never been checked.

The verdict is deliberately narrow — it answers "does the named file exist,
and has it changed since the work was agreed", never "was the work done".
So "wrong" below means the verdict misleads a reader about the narrow
question, or about the broad one it is read as answering.

## What it says, over all 129

| verdict | n | share |
|---|---|---|
| ok (named, exists, changed since accept) | 58 | 45% |
| uncheckable (claim names no artifact) | 57 | 44% |
| unbacked (named file not on disk) | 13 | 10% |
| stale (exists, unchanged since accept) | 1 | 1% |

**44% of claims are uncheckable.** That is not a verifier error — it is the
author writing prose instead of naming a file — but it bounds everything
else: the verifier has an opinion about 56% of what it is shown.

## Precision on accusations: 1 of 16

An accusation (`unbacked` or `stale`) is the expensive verdict: the board
renders it as evidence against the claim. Every one was checked by resolving
the named path against the obvious repository roots.

**15 of 16 named artifacts EXIST.** Every false accusation has the same
cause: the claim wrote a repo-relative path (`agent_mission/signal.py`,
`README.md`, `mobile/__tests__/fixtures.ts`) while the goal's cwd is the
workspace root two or three levels above. The file is there; the verifier
looked in the wrong place and reported the author's true claim as unbacked.

| claim | said | actually |
|---|---|---|
| `39ed542f` edge-triggered signal shipped | unbacked `agent_mission/signal.py` | exists, under `projects/builds/agent-mission/` |
| `8d6c15eb` claim verifier ported | unbacked `agent_mission/claims.py` | exists, same prefix |
| `5f454b22` board cards carry the claims line | unbacked `agent_mission/board.py` | exists, same prefix |
| `8ef7bbca` adversarial fixture set | unbacked `mobile/__tests__/fixtures.ts` | exists under another repo in the tree |
| …11 more, all the same shape | | |

**The one correct accusation is the `stale` verdict**, and it is correct for
a reason worth keeping. `172700b4` claimed "description and six topics set
(pyproject.toml)". The file was last modified 2026-08-18 21:28; the item was
accepted 2026-08-19 17:45 — **the file did not change after the work was
agreed**. Inspection confirms it: `description` is set, and there is no
`keywords` field at all. GitHub topics are repository metadata, not
`pyproject.toml` keywords, so the claim cited an artifact that could not
carry the thing it claimed. Freshness caught what existence alone would have
passed.

## Recall on reassurance: 11 of 12 correct in the sample

Twelve `ok` verdicts were sampled (seed 7) and labelled by opening the named
artifact and checking for the claimed change.

Eleven were real: the walkthrough packs carry the Say/Code/Why/Probe/Answer
annotation claimed (`Say:` 22, `Probe:` 58 in a second pack); the named test
`test_the_board_opens_on_what_happens_next` exists; the decline button is in
`board.py`; a gap the claim said was removed is genuinely absent from a generated
index (0 of 4 remaining gaps mention it).

⚠️ **One was a false reassurance, and the project already knows it.**
`a64d9eca` claimed the Inbox across all goals; the verdict was `ok`; a
verification session later filed: *"Aggregation half holds; four stated
behaviours are not on disk."* The file existed and had changed, so the
verdict was correct on its narrow question and wrong on the question a
reader asks. **That is the failure mode the design accepts on purpose** —
and it is why `mission checked` exists, and why a second session reading the
artifact is worth more than any verdict.

⚠️ One claim was true when made and is now understated rather than wrong:
`e54de39e` said "12 packs and 91 beats"; the file holds 19 and 201 today.
Growth is not falsification, but it means a verdict recomputed later cannot
distinguish "was true" from "is true".

## What this measures, and what it does not

- Verdicts are **recomputed today**, not read from the event. They were never
  stamped at claim time, so a file edited since changes the answer. Item
  `02809ba2` exists to stamp them; until it lands, this table is the best
  available and not a replay.
- 12 of 58 `ok` verdicts were labelled, not all 58. The false-reassurance
  rate is 1 in 12 **in this sample**, with a wide interval on n=12.
- "Exists somewhere obvious" is weaker than "the work was done". For the 15
  false accusations it is enough — the verdict's own claim is that the file
  is absent, and it is not.

## The one change that would fix most of it

Every false accusation is a path prefix. `claims-done` already warns at
write time, naming the mission cwd — that warning shipped on 2026-10-08,
*after* all 16 of these claims were made. Nothing here measures whether it
helped, because no claim has been written since.

**Honest summary: on the 16 occasions this verifier has accused a claim, it
was right once.** It has never produced a false accusation that was not a
path prefix, and it has produced at least one false reassurance that a human
reader caught. A tool whose entire argument is "measured, not inferred" had
not measured this until today.
