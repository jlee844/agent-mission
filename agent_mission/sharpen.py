"""C18: sharpen the CRITERIA, never grade the work.

Source: Raghavendra, Gunjal, Liu & He, *Agentic Rubrics as Contextual Verifiers
for SWE Agents*, ACL 2026. An expert agent explores a repository, writes a
context-grounded rubric, and patches are scored against it without running
tests. Their ablation is the part that transfers: an agent that has actually
read the repo can author criteria that are UNAMBIGUOUS.

What is deliberately not adopted is the verifier itself. Their rubric replaces
test execution because environment setup does not scale; here `verdict_for` is
`Path.exists()` -- microseconds, no environment, nothing to replace. And a
rubric score is INFERENCE, the class this project killed five times with
pre-registered stopping rules. So no score, no ranking, no model, and nothing
in this module ever reaches a verdict, a verdict colour, or the sweep.

Pointed at the criteria instead of at the work, the same idea grows the
deterministic verifier's reach rather than substituting for it. The gap it
answers is measured, not hypothetical: in the 2026-08-27 Wayfinder review, 12 of
19 claims came back "nothing checkable in the claim" -- every one describing
real work that a named file would have made checkable. The verifier was right
every time. It was handed nothing to verify.

This module writes PROPOSALS and nothing else. It cannot tick, cannot edit a
protected field, and cannot refuse a claim: a template is advice about how to
phrase a finished claim, and a claim that ignores it is still recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One definition of "path-shaped", shared with the claim verifier. If the two
# ever disagree, `sharpen` would recommend a citation that `verdict_for` then
# refuses to read -- advice that fails the only test that matters.
from .claims import _PATHY, looks_like_a_file

# Words that say the finished state is demonstrated by a TEST, so the template
# asks for the test's id as well as the file. Deliberately small: over-reaching
# here produces templates asking for evidence that does not exist.
_TESTY = re.compile(r"\b(test|tests|suite|assert\w*|pytest|coverage|ci)\b", re.I)

# Splitting prose into candidate tokens. Trailing sentence punctuation is
# stripped so `README.md.` still reads as a path, while `e.g.` does not: the
# extension must be at least two characters, which is what separates a real
# suffix from an abbreviation's second letter.
_SPLIT = re.compile(r"[\s,;:()\[\]`'\"]+")


def names_an_artifact(text: str, strict: bool = False) -> str:
    """The first path-shaped token in a criterion, or "".

    A criterion that already names a file can be claimed checkably as it
    stands, so it needs no template. This is the whole test for "is this
    ambiguous" -- mechanical, no judgement about whether the wording is GOOD.

    `strict` narrows path-shaped to FILE-shaped (`claims.looks_like_a_file`).
    C18's own use stays loose on purpose: a false positive there costs one
    piece of unoffered advice, while in C19 eligibility it asserts that a
    context-free session could prove the item finished. Same scanner, one
    place, two tolerances -- stated rather than two copies of the rule.
    """
    for word in _SPLIT.split(text or ""):
        word = word.rstrip(".,;:!?")
        if len(word) <= 2:
            continue
        if looks_like_a_file(word) if strict else _PATHY.match(word):
            return word
    return ""


@dataclass
class Sharpening:
    """One criterion that cannot be claimed checkably, and how to fix that."""
    kind: str          # "done-when" | "item"
    ref: str           # the item id, or the criterion's 1-based position
    text: str          # the criterion, verbatim -- never rewritten here
    template: str      # what a finished claim about it must cite

    def as_proposal(self) -> str:
        """The text of the `[+]` row a human will accept or ignore."""
        where = (f"DONE-WHEN #{self.ref}" if self.kind == "done-when"
                 else f"item {self.ref}")
        return (f"sharpen {where}: “{self.text[:90]}” names no file, so "
                f"finishing it cannot be checked. Claim it as — {self.template}")


def template_for(text: str) -> str:
    """The claim shape that would make finishing this criterion checkable.

    Note what this does NOT do: it does not rewrite the criterion, and it does
    not invent a path. Inventing one is the failure mode the stopping rule
    names -- a template that teaches agents to cite any nearby existing file
    inflates backed-ness without adding truth, which is strictly worse than
    the silence it replaces. So the template is a SHAPE with blanks the author
    fills from the work they actually did.
    """
    if _TESTY.search(text or ""):
        return "done: <what changed> (path/to/file.py, tests/test_x.py::test_y)"
    return "done: <what changed> (path/to/file)"


def sharpenings(mission, limit: int = 0) -> list[Sharpening]:
    """Every criterion and open item that could not be claimed checkably.

    Ordered done-when first: a success criterion outlives every item under it,
    so sharpening one is worth more than sharpening a task that is nearly over.
    """
    out: list[Sharpening] = []
    for n, c in enumerate(mission.success_criteria, 1):
        if not names_an_artifact(c):
            out.append(Sharpening("done-when", str(n), c, template_for(c)))
    # A branch is finished when its leaves are, so nobody ever claims one --
    # "Store and identity" is a heading, not work, and asking for a file to
    # cite for it is advice that cannot be taken.
    branches = {i.parent for i in mission.items if i.parent}
    for i in mission.items:
        # Open work only. A finished item's phrasing is history, and an
        # unaccepted proposal is not the plan yet.
        if i.done or not i.accepted or i.id in branches:
            continue
        if not names_an_artifact(i.text):
            out.append(Sharpening("item", i.id, i.text, template_for(i.text)))
    return out[:limit] if limit else out
