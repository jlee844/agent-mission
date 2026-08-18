"""Read a plan somebody else wrote and land it on the board.

Every planner in this space ends at a markdown file — `writing-plans` writes
`docs/superpowers/plans/*.md`, GSD writes `.planning/ROADMAP.md`, Kiro writes
`.kiro/specs/<feature>/tasks.md`, Spec Kit writes `specs/*/tasks.md`. They are
good at decomposition and they all stop at a file nothing reads back.

So this parses the shape they share — headings and nested checkboxes — into a
tree, and the CLI lands it as **proposals**. That makes those tools inputs
rather than competitors, and it costs no context: the parsing is a subprocess,
not a prompt.

Two rules that are design, not convenience:

  * A `[x]` in the source is NOT imported as done. Ticking is the human's
    judgement, and a file is not a human. The count is reported so they can
    tick in one command if the source was right.
  * Re-importing DIFFS. A replan that duplicated the tree every time would be
    read once and then ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_CHECK = re.compile(r"^\[([ xX])\]\s*(.*)$")
# Numbering the source already carries -- "1.", "2.3", "Task 4:" -- is noise
# once the tree provides the ordering, and it makes diffing brittle: renumber
# a plan and every item looks new.
_LEADING_NUM = re.compile(r"^(?:task\s+)?\d+(?:\.\d+)*[.):]?\s+", re.I)
_EMPH = re.compile(r"\*\*|__|`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


@dataclass(frozen=True)
class Row:
    depth: int
    text: str
    checked: bool


def _clean(s: str) -> str:
    s = _LINK.sub(r"\1", s)
    s = _EMPH.sub("", s)
    s = _LEADING_NUM.sub("", s.strip())
    return " ".join(s.split())


def norm(s: str) -> str:
    """The key two items are 'the same item' by. Case and punctuation drift
    between replans; the words do not."""
    return re.sub(r"[^a-z0-9 ]+", "", _clean(s).lower()).strip()


def _unwrap(lines: list[str]) -> list[str]:
    """Rejoin bullets that wrap across lines.

    Found by importing a real plan file rather than one written for this
    parser: prose wrapped at 90 characters, and every long task arrived cut
    mid-sentence — "A Stop hook that runs the verifier over". Half a task is
    worse than no task, because it looks complete.

    A continuation is a non-blank line that is not itself a bullet, a heading
    or a fence. Anything else ends the bullet.
    """
    out: list[str] = []
    open_bullet = False
    for raw in lines:
        stripped = raw.strip()
        starts_block = (not stripped or _FENCE.match(raw) or _HEADING.match(raw)
                        or _BULLET.match(raw))
        if open_bullet and not starts_block:
            out[-1] = out[-1].rstrip() + " " + stripped
            continue
        out.append(raw)
        open_bullet = bool(_BULLET.match(raw))
    return out


def parse(text: str) -> list[Row]:
    """Headings and nested bullets, as a flat list of (depth, text, checked).

    Headings set the branch; bullets hang under the last heading, nested by
    their own indentation. A file with no headings is just its bullets.
    """
    rows: list[Row] = []
    fence = False
    head_levels: list[int] = []      # heading levels seen, as a stack
    bullet_indents: list[int] = []   # bullet indents under the current heading
    for raw in _unwrap(text.splitlines()):
        if _FENCE.match(raw):
            fence = not fence
            continue
        if fence or not raw.strip():
            continue

        h = _HEADING.match(raw)
        if h:
            level = len(h.group(1))
            while head_levels and head_levels[-1] >= level:
                head_levels.pop()
            head_levels.append(level)
            bullet_indents = []
            t = _clean(h.group(2))
            if t:
                rows.append(Row(len(head_levels) - 1, t, False))
            continue

        b = _BULLET.match(raw)
        if not b:
            continue
        indent = len(b.group(1).expandtabs(4))
        body = b.group(2)
        checked = False
        c = _CHECK.match(body)
        if c:
            checked = c.group(1).lower() == "x"
            body = c.group(2)
        t = _clean(body)
        if not t:
            continue
        while bullet_indents and bullet_indents[-1] >= indent:
            bullet_indents.pop()
        bullet_indents.append(indent)
        rows.append(Row(len(head_levels) + len(bullet_indents) - 1, t, checked))
    return _tighten(rows)


def _tighten(rows: list[Row]) -> list[Row]:
    """Collapse depth gaps.

    A file whose only heading is `# Plan` followed by top-level bullets would
    otherwise start the tree at depth 1 under a single root, and a jump from
    depth 0 to depth 3 would produce a child with no parent at depth 1 or 2.
    """
    out: list[Row] = []
    stack: list[int] = []
    for r in rows:
        while stack and stack[-1] >= r.depth:
            stack.pop()
        stack.append(r.depth)
        out.append(Row(len(stack) - 1, r.text, r.checked))
    return out
