"""C19: which ready items a FRESH session could be handed, from recorded facts.

The ask this answers was "let mission judge which granular jobs are better
given to a newly spawned agent". Judging granularity means reading an item's
text and estimating how big the work is -- which is inference, the class this
project closed five times with pre-registered stopping rules, and which the
stored non-goals name twice ("Drift detection (closed, five failed attempts)",
"Judging whether the work is the RIGHT work"). So this module does not judge.

It BOOKKEEPS. Four signals, every one a fact already in the log, and every
verdict carries the fact that produced it:

  leaf        -- a subgoal is a container; its leaves are the work. Structural.
  ready       -- accepted, not done, not held by a live lease. Already a lane.
  artifact    -- the text names a path-shaped token, so a session with no
                 context can finish it AND claim it checkably. This is C18's
                 finding pointed at delegation: an item naming nothing cannot
                 be claimed in a form `verdict_for` can read, so a fresh
                 instance has no way to show it finished.
  not handed back -- a release-with-a-reason is recorded proof that a session
                 took this and could not finish it. Earned twice on 2026-10-08
                 (a64d9eca, a1708422), both of which needed the human's ruling
                 rather than another agent.

The NEGATIVE verdict is the useful one, which is why the fact is phrased for
it: "names no file, so a fresh session could not prove it finished" is advice
about the item's wording -- the same advice `sharpen` gives, arriving from a
second direction. Nothing here ticks, leases, reorders or refuses: callers
print it.
"""

from __future__ import annotations

from dataclasses import dataclass

# One definition of "path-shaped", shared with the claim verifier through
# `sharpen`. A third copy of this rule is the failure this codebase has hit
# repeatedly -- DENY_RULES in two files, LEASE_TTL on two objects.
from .sharpen import names_an_artifact


@dataclass
class Verdict:
    """Whether a fresh instance could be handed this item, and on what fact."""
    ok: bool
    fact: str            # the recorded fact behind the verdict, for printing
    artifact: str = ""   # the path-shaped token, when the text names one

    @property
    def label(self) -> str:
        return "delegable" if self.ok else "not delegable"


def verdict_for(mission, item, now: float | None = None) -> Verdict:
    """The eligibility verdict for one item. Order of tests IS precedence.

    Structural disqualifications come first: a container or an unaccepted
    proposal is not work anybody should be handed, and saying "names no file"
    about a heading is advice that cannot be taken.
    """
    branches = {i.parent for i in mission.items if i.parent}
    if item.id in branches:
        return Verdict(False, "a subgoal, not work — its leaves are the items")
    lane = mission.lane_of(item, now)
    if lane != "ready":
        why = {
            "waiting": "a proposal awaiting your accept",
            "progress": "held by a live lease",
            "review": "claimed, awaiting a check",
            "done": "already ticked",
        }.get(lane, lane)
        return Verdict(False, f"not in the ready lane — {why}")
    if item.released_at:
        note = (item.release_note or "").strip()
        tail = f" — “{note[:70]}”" if note else ""
        return Verdict(
            False,
            "a session took this and handed it back, so another instance "
            f"would hit the same wall{tail}")
    # strict=True: see `claims.looks_like_a_file`. Measured on the live
    # board, the loose rule called `Item.sessions`, `j/k` and
    # `accepted/unleased/not-done` artifacts.
    art = names_an_artifact(item.text, strict=True)
    if not art:
        return Verdict(
            False,
            "names no file, so a fresh session could not prove it finished")
    return Verdict(True, f"names {art}, so a fresh session can claim it "
                         f"checkably", art)


def eligible(mission, now: float | None = None) -> list:
    """Every ready leaf with its verdict, in the order `take` hands them out.

    Order is borrowed, never recomputed: `next_ready` already states the one
    ordering judgement (fresh rework first), and a second copy of it here
    would let `dispatch` advertise an item `take` would not hand over.
    """
    ready = mission.lanes(now)["ready"]
    fresh = [i for i in ready
             if i.rework and not (i.released_at > i.rework_at)]
    rest = [i for i in ready if i not in fresh]
    return [(i, verdict_for(mission, i, now)) for i in fresh + rest]
