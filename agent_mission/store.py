"""The mission: what you own, what the agent may propose, what it just records.

Three levels of authority, and they are the point:

    PROTECTED   objective, success criteria, constraints, non-goals.
                Yours. The agent cannot write them. There is no API for it
                to try — the absence is the mechanism, not a permission check.
    PROPOSED    milestones, strategy. The agent suggests; you accept.
    OBSERVABLE  progress, evidence, decisions. The agent records freely,
                because recording is not deciding.

Storage is an append-only event log. State is a fold over it, so "why does the
mission say this" is answerable by reading, and nothing is silently rewritten.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


class Authority(str, Enum):
    PROTECTED = "protected"
    PROPOSED = "proposed"
    OBSERVABLE = "observable"


FIELD_AUTHORITY: dict[str, Authority] = {
    "name": Authority.PROTECTED,
    "objective": Authority.PROTECTED,
    "success_criteria": Authority.PROTECTED,
    "constraints": Authority.PROTECTED,
    "non_goals": Authority.PROTECTED,
    "checklist": Authority.PROPOSED,
    "strategy": Authority.PROPOSED,
    "decisions": Authority.OBSERVABLE,
    "evidence": Authority.OBSERVABLE,
    "notes": Authority.OBSERVABLE,
}
PROTECTED_FIELDS = {f for f, a in FIELD_AUTHORITY.items() if a is Authority.PROTECTED}

# How long a lease survives without being renewed. A session that dies
# mid-item must not park it forever; 90 minutes is long enough that a real
# turn never loses its hold, short enough that a crash costs one lunch break
# rather than a day. Module-level because BOTH the store (when refusing a
# second holder) and the Mission (when deciding the lane) have to agree --
# two copies is how an item reads "in progress" on the board while the store
# is handing it to somebody else.
LEASE_TTL = 90 * 60

# How long an AGREED item may sit with no session ever touching it before the
# board asks about it. Pre-registered, and the measurement is the point: on
# the live board the accepted-ages of untouched ready items are 0.0–0.1 days
# or 43.0–45.1 days, with NOTHING in between, so every threshold in 1..43
# partitions this board identically and the number is doing almost no work
# today. Two weeks is chosen inside that empty region -- long enough that a
# session would have leased, claimed or released real work by now, short
# enough to catch the next one before it reaches 45 days unnoticed.
UNTOUCHED_DAYS = 14
LIST_FIELDS = {"success_criteria", "constraints", "non_goals", "checklist",
               "decisions", "evidence", "notes"}


class ProtectedFieldError(PermissionError):
    """Raised when an agent tries to write a field the human owns."""


class NoMissionError(LookupError):
    """Raised when a write is attempted on a session that has no mission.

    Every write except `create` folds over a log that begins with a `created`
    event. Without one, `propose` used to append happily and the item was then
    unreadable forever -- load() returns None, so the event was on disk and
    invisible. `add`, which proposes and accepts in one step, turned the same
    bug into a NoSuchItemError traceback on the id it had just written.
    """


class NoSuchItemError(KeyError):
    """Raised for an item id that is not in the plan.

    Appending an event for an unknown id used to succeed silently, so a typo'd
    id reported "done" and changed nothing.
    """


@dataclass
class Item:
    """A node in the plan. `done` is set by a human; evidence is measured.

    A plan is a tree, not a list: an objective breaks into subgoals, and those
    into work. Flattening it loses which piece a task belongs to, which is the
    first thing you want to know when three sessions are running.
    """
    id: str
    text: str
    done: bool = False
    proposed_by: str = "agent"
    accepted: bool = False
    parent: str | None = None
    # C17: the agent's claim that this item is finished -- the legible-claim
    # text, so the verifier can bind a disk verdict to this exact row. A
    # SUGGESTION, never a state change: `done` still has one writer.
    claimed_done: str = ""
    # WHEN the human accepted it. Needed because "the named file exists" was
    # the whole of "disk agrees", so `done: rewrote auth (README.md)` came
    # back backed in any repo that has a README -- and the one-click "confirm
    # all backed" sweep then ticked it `by=human`. A file untouched since the
    # work was agreed is not evidence the work happened. 0.0 = never
    # accepted, or an older log with no timestamp: unknown, not stale.
    accepted_at: float = 0.0
    # ── routing (all OBSERVABLE: coordination, never authority) ───────────
    # Which session is holding this item, and since when. Taking an item
    # changes neither what the plan is nor whether anything is done, so it
    # needs no human ruling -- but without it two sessions pick the same
    # first item, because the selection rule is deterministic.
    leased_by: str = ""
    leased_at: float = 0.0
    # A verifier's reason this is NOT finished. Non-empty means the item is
    # back in the ready queue carrying why. It does NOT un-accept the item:
    # the human already agreed to this work, so a defect in it is the same
    # work continuing, not new work needing a second acceptance. That is what
    # lets a finding route straight to a worker with nobody in the middle.
    rework: str = ""
    rework_by: str = ""
    rework_at: float = 0.0
    # When a session handed it back, and why. Needed because "rework first"
    # became a LIVELOCK: an item sent back by a verifier sorted ahead of
    # everything, so a session that took it, found it undoable and released
    # it left it sorting first again -- and the next session, and the next,
    # each burned a turn rediscovering the same wall. Seen live on
    # a64d9eca, which a worker correctly refused and which would then have
    # been handed to all nine idle peers in turn.
    released_at: float = 0.0
    release_note: str = ""
    # Which session made the claim, so the checker can be refused if it is
    # the same one. Without it, self-grading is indistinguishable from a
    # second reading.
    claimed_by: str = ""
    # A second session's opinion that the claim holds. NOT evidence: see
    # MissionStore.checked. Cleared by a new claim or a finding, because both
    # mean the thing that was checked is no longer what is on offer.
    checked_note: str = ""
    checked_by: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Node:
    """An Item plus its children, with progress rolled up from the leaves."""
    item: Item
    children: list["Node"] = field(default_factory=list)

    @property
    def leaves(self) -> list[Item]:
        if not self.children:
            return [self.item]
        return [lf for c in self.children for lf in c.leaves]

    @property
    def done_count(self) -> int:
        return sum(1 for lf in self.leaves if lf.done)

    @property
    def total(self) -> int:
        # Same rule as Mission.total_count: agreed work only.
        return sum(1 for lf in self.leaves if lf.accepted)

    @property
    def complete(self) -> bool:
        """A branch is done when its agreed leaves are; a leaf when ticked."""
        return self.done_count == self.total and self.total > 0


@dataclass
class Mission:
    id: str
    session_id: str = ""
    cwd: str = ""
    # Where this mission came from, when it is a delegated slice of another.
    # A subagent has no session id of its own, so without this its work is
    # invisible from the parent's board and its own card floats unattached.
    parent_session: str = ""
    parent_item: str = ""
    name: str = ""            # a short title; the objective is the sentence
    objective: str = ""
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    checklist: list[dict] = field(default_factory=list)
    strategy: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created: float = 0.0
    # True when every protected field was entered by a person at a keyboard.
    # False means an agent transcribed at least one of them -- permitted, and
    # worth showing, because "is this really your goal" is the question this
    # whole tool exists to keep answerable.
    typed_by_human: bool = True
    # Declared side quests, innermost last. Never inferred: F34 killed five
    # attempts to detect drift, and the honest residue is that the agent says
    # when it is going off, rather than a detector guessing.
    detours: list[str] = field(default_factory=list)
    archived: bool = False

    @property
    def items(self) -> list[Item]:
        return [Item(**d) for d in self.checklist]

    def tree(self) -> list[Node]:
        """The plan as a forest: what is left first, what is finished below it.

        Within each of those two groups the order is the order you wrote them,
        so the plan still reads as a sequence — only the completed work sinks.
        The remaining work is the part you act on, and it should not have to be
        found among ticked boxes.

        An item whose parent is missing is treated as a root rather than
        dropped: losing a task because its parent was deleted is worse than
        showing it slightly out of place.
        """
        by_id = {i.id: Node(i) for i in self.items}
        roots: list[Node] = []
        for i in self.items:
            node = by_id[i.id]
            parent = by_id.get(i.parent) if i.parent else None
            if parent is None or parent is node:
                roots.append(node)
            else:
                parent.children.append(node)

        def sink_done(nodes: list[Node]) -> list[Node]:
            for n in nodes:
                n.children = sink_done(n.children)
            # sorted() is stable, so insertion order survives inside each group.
            # A branch counts as done only when every leaf under it is.
            return sorted(nodes, key=lambda n: n.complete)

        return sink_done(roots)

    @property
    def title(self) -> str:
        """What to call this session. Falls back to the objective, trimmed at a
        word boundary — a heading cut mid-word reads as a bug."""
        if self.name:
            return self.name
        o = self.objective.strip()
        if len(o) <= 72:
            return o
        return o[:69].rsplit(" ", 1)[0] + "…"

    @property
    def leaves(self) -> list[Item]:
        """Only leaves are work. A subgoal is a container, and counting it as a
        task both inflates the total and can never be ticked honestly."""
        return [lf for n in self.tree() for lf in n.leaves]

    @property
    def done_count(self) -> int:
        return sum(1 for lf in self.leaves if lf.done)

    LEASE_TTL = LEASE_TTL          # the module constant, not a second copy

    def lane_of(self, lf: Item, now: float | None = None) -> str:
        """Which of the four lanes this item is in.

        One function, because the lanes have to partition: an item that
        appeared in two of them, or none, is a row a person cannot act on.
        Order of tests IS the precedence.
        """
        import time as _t
        now = _t.time() if now is None else now
        if not lf.accepted:
            return "waiting"              # a proposal is yours, not a worker's
        if lf.done:
            return "done"
        if lf.claimed_done:
            return "review"               # a claim awaiting a check
        if lf.leased_by and (now - lf.leased_at) < self.LEASE_TTL:
            return "progress"
        return "ready"                    # includes rework: claim was cleared

    def lanes(self, now: float | None = None) -> dict:
        out: dict = {"ready": [], "progress": [], "review": [],
                     "waiting": [], "done": []}
        for lf in self.leaves:
            out[self.lane_of(lf, now)].append(lf)
        return out

    def next_ready(self, now: float | None = None) -> Item | None:
        """The item a session should take: first ready leaf in TREE ORDER.

        Mechanical, and deliberately the same rule `whereami` already prints.
        Rework first, because a defect in agreed work outranks starting
        something new -- that is the only ordering judgement here and it is
        stated rather than inferred.
        """
        ready = self.lanes(now)["ready"]
        # Rework first -- but NOT rework a session already tried and handed
        # back. That combination is the livelock: it outranks everything
        # forever, and every session that takes it hits the same wall. Once
        # handed back it stays available at its tree-order position, so the
        # work is not lost; it just stops blocking the queue, and the reason
        # it was returned is the human's to rule on.
        fresh_rework = [i for i in ready
                        if i.rework and not (i.released_at > i.rework_at)]
        return (fresh_rework[0] if fresh_rework
                else ready[0] if ready else None)

    @property
    def total_count(self) -> int:
        """Only AGREED work counts. A proposal is not yet part of the plan.

        Counting proposals here meant the agent could move the human's progress
        backwards by suggesting things: one real board read 8/10 with every
        agreed task finished and two suggestions outstanding. The pending count
        is reported separately, which is where an unanswered proposal belongs.
        """
        return sum(1 for lf in self.leaves if lf.accepted)

    @property
    def pending(self) -> list[Item]:
        return [i for i in self.items if not i.done]

    @property
    def unaccepted(self) -> list[Item]:
        """Agent-proposed items you have not signed off."""
        return [i for i in self.items if not i.accepted]

    def untouched(self, days: float = UNTOUCHED_DAYS,
                  now: float | None = None) -> list[Item]:
        """Ready items agreed long ago that NO session has ever touched.

        This is the hole the four lanes left, and it was costing real work:
        five items on the live board had been agreed 43–45 days, and every
        one of them was already BUILT -- nobody had said so, so they sat in
        the hand-out queue. `dispatch` nearly sent a peer to rebuild
        `claims-done`, which has shipped for weeks.

        What this is NOT: a judgement that the work is finished. Reading the
        repo to decide that is the closed non-goal, and it cannot be
        mechanised here anyway -- measured: 0 of 11 ready items name an
        artifact that resolves on disk, so there is no file whose mtime could
        speak for them. What IS measured is the item's own history: agreed at
        a known time, and never leased, claimed, released or sent back. The
        honest reading of that pair is "nobody has touched this since you
        agreed to it", which is a question for a human or a session, not a
        verdict about the code.

        `accepted_at == 0` is an older log with no timestamp: unknown, which
        is not the same as old, so it is excluded rather than assumed.
        """
        import time as _t
        now = _t.time() if now is None else now
        cutoff = days * 86400
        return [i for i in self.lanes(now)["ready"]
                if i.accepted_at
                and (now - i.accepted_at) > cutoff
                and not i.leased_at and not i.released_at
                and not i.claimed_done and not i.rework]

    @staticmethod
    def days_since_accepted(item: Item, now: float | None = None) -> float:
        """Age in days, or -1.0 when the log never recorded an accept time."""
        import time as _t
        if not item.accepted_at:
            return -1.0
        now = _t.time() if now is None else now
        return (now - item.accepted_at) / 86400.0

    @property
    def suggested(self) -> list[Item]:
        """C17: agreed work the agent claims is finished, awaiting your tick."""
        return [i for i in self.items
                if i.accepted and not i.done and i.claimed_done]

    def to_dict(self) -> dict:
        return asdict(self)


class MissionStore:
    """Append-only. Every change is an event; the mission is the fold."""

    def __init__(self, root: Path):
        # The directory is created on the first WRITE, not on construction:
        # reading a session that has no mission should leave no trace, and an
        # empty directory reads as "a mission exists" to anything scanning.
        self.root = Path(root)
        self.log = self.root / "events.jsonl"
        # Where the writer stood, and HOW this mission was chosen. Set by the
        # CLI before a write. Yesterday's forensics could not answer either
        # question for anything but `init`, so "which session wrote this, from
        # where, and why did it land here" was unanswerable for 52 events.
        self.context_cwd = ""
        self.context_via = ""
        # Lines the last read could not use. Surfaced rather than swallowed:
        # silently skipping damage is how a log stops being evidence.
        self.damaged = 0
        # ...and WHAT was wrong with each, so `doctor` can say something more
        # useful than a count. A number tells you to look; this tells you where.
        self.damage: list[str] = []

    # ── writing ──────────────────────────────────────────────────────────
    def _append(self, kind: str, by: str, /, typed_by: str | None = None,
                **detail: Any) -> dict:
        # kind/by are positional-only so a payload may legitimately carry a
        # field called "kind" -- and the envelope is written LAST so it wins.
        # Without the ordering, such a payload overwrites the event's own kind
        # and the event becomes unfindable by the reader.
        # `by` is AUTHORITY -- whose field this is. `typed_by` is who actually
        # ran the command. They differ in the one case the design permits: the
        # agent transcribing an interview through `init --from-file`. Recording
        # only `by` made that indistinguishable from a person typing, so
        # `why objective` said "human" about a goal an agent had written.
        ev = {**detail, "kind": kind, "by": by,
              "typed_by": typed_by or by, "at": time.time()}
        # Stamped only on human AUTHORITY, because that is the only claim
        # anyone would want to forge, and only when there is something to say
        # -- see whoami.provenance(). This is what makes a `script(1)` pty
        # readable after the fact instead of indistinguishable from a person.
        if by == "human":
            from . import whoami
            for k, v in whoami.provenance().items():
                ev.setdefault(k, v)
        if self.context_cwd:
            ev.setdefault("cwd", self.context_cwd)
        if self.context_via:
            # explicit | env | cwd | board -- which ROUTE this write came
            # through, beside `by` (whose field it is) and `typed_by` (who
            # ran it). A board click and a typed command were identical in
            # the log until `board` joined this list.
            ev["via"] = self.context_via
        self.root.mkdir(parents=True, exist_ok=True)
        _append_line(self.log, json.dumps(ev) + "\n")
        return ev

    def create(self, session_id: str, cwd: str, objective: str, by: str,
               parent_session: str = "", parent_item: str = "",
               typed_by: str | None = None) -> dict:
        """A mission is authored by a person.

        The one exception is a DELEGATED mission, where the objective is not
        authored at all: it is copied verbatim from an item the human already
        accepted in the parent plan. The agent chooses nothing, so letting it
        create one adds no authority -- and refusing would only push it into
        working with no recorded goal at all, which is the failure this whole
        tool exists to prevent.
        """
        if by != "human" and not parent_item:
            raise ProtectedFieldError(
                "a mission is created by the person, not the agent")
        return self._append("created", by, typed_by=typed_by,
                            session_id=session_id, cwd=cwd,
                            objective=objective, parent_session=parent_session,
                            parent_item=parent_item)

    def discard(self, by: str) -> dict:
        """Deliberately end this mission so a new one can begin.

        Separate from `create` so that starting over is an explicit act rather
        than a side effect of writing a second `created` event.
        """
        if by != "human":
            raise ProtectedFieldError("only you can discard a mission")
        return self._append("discarded", by)

    def set_protected(self, fieldname: str, value: Any, by: str,
                      typed_by: str | None = None) -> dict:
        """Only a human may write a protected field. No agent path exists."""
        self._require_mission()
        if fieldname not in PROTECTED_FIELDS:
            raise ValueError(f"{fieldname} is not protected")
        if by != "human":
            raise ProtectedFieldError(
                f"{fieldname} is yours; the agent cannot set it")
        return self._append("set", by, typed_by=typed_by, field=fieldname,
                            value=value)

    def _require_mission(self) -> None:
        if self.load() is None:
            raise NoMissionError(self.root.name)

    def propose(self, text: str, by: str = "agent", parent: str | None = None,
                from_session: str = "") -> dict:
        """Suggest a node. Inert until accepted. `parent` nests it under another.

        `from_session` records a proposal that came from ANOTHER session, so
        `why` can say where a suggestion on your plan came from.
        """
        self._require_mission()
        extra = {"from_session": from_session} if from_session else {}
        return self._append("proposed", by, item_id=uuid.uuid4().hex[:8],
                            text=text, parent=parent, **extra)

    def _require(self, item_id: str) -> None:
        m = self.load()
        if m is None or not any(d["id"] == item_id for d in m.checklist):
            raise NoSuchItemError(item_id)

    def accept(self, item_id: str, by: str) -> dict:
        if by != "human":
            raise ProtectedFieldError("only you can accept a proposal")
        with _LogLock(self.root):
            self._require(item_id)
            return self._append("accepted", by, item_id=item_id)

    def complete(self, item_id: str, by: str) -> dict:
        """Marking work done is a judgement, so it stays with the human."""
        if by != "human":
            raise ProtectedFieldError(
                "the agent may record evidence, not declare an item done")
        with _LogLock(self.root):
            self._require(item_id)
            return self._append("completed", by, item_id=item_id)

    def claim_done(self, item_id: str, text: str, by: str = "agent",
                   session_id: str = "") -> dict:
        """C17: the agent says an item is finished; the human still ticks.

        Observable tier -- recording a claim is not deciding -- but only on an
        item that is ACCEPTED and OPEN: claiming completion of a proposal
        nobody agreed to would smuggle it toward the counter, and re-claiming
        a finished item is noise. Idempotent per item: a new claim replaces
        the old one, so a corrected claim does not stack."""
        with _LogLock(self.root):
            m = self.load()
            if m is None:
                raise NoMissionError(self.root.name)
            d = next((d for d in m.checklist if d["id"] == item_id), None)
            if d is None:
                raise NoSuchItemError(item_id)
            if not d.get("accepted"):
                raise ValueError("not accepted — propose/accept it first; "
                                 "claims-done is for agreed work")
            if d.get("done"):
                raise ValueError("already ticked — nothing to suggest")
            return self._append("claims_done", by, item_id=item_id, text=text,
                                session_id=session_id)

    def remove(self, item_id: str, by: str) -> dict:
        """Drop an item from the plan. A soft delete: the event log keeps it.

        Append-only is about not losing history, not about being unable to
        change your mind. A plan you cannot prune stops being a plan.
        """
        if by != "human":
            raise ProtectedFieldError("only you can remove an item")
        with _LogLock(self.root):
            self._require(item_id)
            return self._append("removed", by, item_id=item_id)

    def lease(self, item_id: str, session_id: str,
              by: str = "agent") -> dict:
        """Take an item. Observable: coordination, not a ruling.

        Refuses an unaccepted item -- leasing a proposal would let a session
        start work the human has not agreed to, which is the move the whole
        design exists to prevent -- and refuses one another LIVE lease holds,
        because the point of the lease is that two sessions cannot both have
        it. An EXPIRED lease is taken over silently: a dead holder is not a
        holder.
        """
        with _LogLock(self.root):
            m = self.load()
            if m is None:
                raise NoMissionError(self.root.name)
            d = next((d for d in m.checklist if d["id"] == item_id), None)
            if d is None:
                raise NoSuchItemError(item_id)
            if not d.get("accepted"):
                raise ValueError("not accepted — a session may only take work "
                                 "the human agreed to")
            if d.get("done"):
                raise ValueError("already ticked")
            held = d.get("leased_by") or ""
            fresh = (time.time() - (d.get("leased_at") or 0)) < LEASE_TTL
            if held and held != session_id and fresh:
                raise ValueError(f"held by {held[:8]} — leases expire after "
                                 f"{LEASE_TTL // 60} min")
            # The SAME session re-taking its own item renews it. A 90-minute
            # lease that cannot be renewed silently expires under a long
            # task, and the item becomes takeable by someone else while the
            # original worker is still in it -- the lease's one guarantee,
            # lost to the clock rather than to a conflict. Re-taking is the
            # renewal, so a worker that checks back keeps its claim and one
            # that has genuinely stopped lets it lapse.
            return self._append("leased", by, item_id=item_id,
                                session_id=session_id)

    def release(self, item_id: str, note: str = "",
                by: str = "agent") -> dict:
        """Hand an item back without claiming it is finished.

        The note matters more than it looks: a handback with no reason is
        indistinguishable from a crashed session, and the next taker learns
        nothing. The first real worker to hit this had to file a separate
        proposal to explain itself, which is the wrong shape.
        """
        with _LogLock(self.root):
            self._require(item_id)
            return self._append("released", by, item_id=item_id, text=note)

    def finding(self, item_id: str, text: str, session_id: str = "",
                by: str = "agent") -> dict:
        """A verifier's reason an item is NOT done. Observable.

        This is the routing primitive: it CLEARS the agent's claims-done and
        the lease, so the row returns to the ready queue carrying why, and
        the next session to ask for work gets it. No human ruling is needed
        because none is being made -- the item was accepted long ago, the
        claim was only a suggestion, and nothing here ticks anything.

        A finding on an item with no claim is still useful (a defect found by
        reading, not by checking a claim), so that is allowed; a finding on a
        TICKED item is not -- reopening the human's own judgement is a
        different act, and it belongs to them.
        """
        if not (text or "").strip():
            raise ValueError("a finding needs a reason — it is what routes "
                             "the work, and an empty one is a silent reopen")
        with _LogLock(self.root):
            m = self.load()
            if m is None:
                raise NoMissionError(self.root.name)
            d = next((d for d in m.checklist if d["id"] == item_id), None)
            if d is None:
                raise NoSuchItemError(item_id)
            if not d.get("accepted"):
                raise ValueError("not accepted — nothing to send back")
            if d.get("done"):
                raise ValueError("already ticked by the human — a finding "
                                 "cannot reopen their judgement; say it with "
                                 "`mission observe` instead")
            return self._append("finding", by, item_id=item_id, text=text,
                                session_id=session_id)

    def checked(self, item_id: str, note: str, session_id: str = "",
                by: str = "agent") -> dict:
        """A second session re-derived this claim and agrees. Observable.

        The counterpart to `finding`, and deliberately WEAKER than it. A
        finding routes work, so it changes the lane. This changes nothing:
        the item stays in review and the human's confirm is still the only
        thing that ticks it. All it adds is a line saying somebody other than
        the claimant looked.

        ⚠️ It is an OPINION, not evidence, and every surface that prints it
        has to say so. The verifier is the same model reading the same disk;
        what makes it worth anything is a fresh context with no access to the
        doer's reasoning, which is a weaker claim than independence and must
        not be displayed as the stronger one.
        """
        if not (note or "").strip():
            raise ValueError("say what you checked — an unexplained 'agrees' "
                             "is the rubber stamp this exists to avoid")
        with _LogLock(self.root):
            m = self.load()
            if m is None:
                raise NoMissionError(self.root.name)
            d = next((d for d in m.checklist if d["id"] == item_id), None)
            if d is None:
                raise NoSuchItemError(item_id)
            if not d.get("claimed_done"):
                raise ValueError("nothing claimed on this item — there is no "
                                 "claim to check")
            if session_id and session_id == (d.get("claimed_by") or ""):
                # Self-grading is the failure this whole role exists to
                # avoid, and it is worth refusing rather than labelling.
                raise ValueError("the session that claimed it cannot be the "
                                 "one that checks it")
            return self._append("checked", by, item_id=item_id, text=note,
                                session_id=session_id)

    def detour(self, label: str, by: str = "agent") -> dict:
        """Declare a side quest. Observable: recording is not deciding."""
        self._require_mission()
        return self._append("detour", by, label=label)

    def ret(self, by: str = "agent") -> dict:
        """Close the innermost detour. A no-op on an empty stack, not an error."""
        self._require_mission()
        return self._append("returned", by)

    def archive(self, by: str, undo: bool = False) -> dict:
        """Take a finished goal off the board. Nothing is deleted.

        Append-only means the log stays; archiving is a statement about
        attention, not about history. A one-off test mission with four
        unaccepted proposals otherwise outranks live work forever.
        """
        if by != "human":
            raise ProtectedFieldError("only you can archive a mission")
        self._require_mission()
        return self._append("unarchived" if undo else "archived", by)

    def acknowledge(self, finding: str, by: str) -> dict:
        """"I have read this." Clears a finding without pretending it changed.

        A duplicate `created` from two days ago is not undone by acknowledging
        it -- but it should not shout forever either, or the lane it lives in
        becomes the thing you learn to ignore.
        """
        if by != "human":
            raise ProtectedFieldError("reading it is yours to say")
        self._require_mission()
        return self._append("acknowledged", by, finding=finding)

    def observe(self, fieldname: str, text: str, by: str = "agent") -> dict:
        if FIELD_AUTHORITY.get(fieldname) is not Authority.OBSERVABLE:
            raise ValueError(f"{fieldname} is not observable")
        self._require_mission()
        return self._append("observed", by, field=fieldname, value=text)

    # ── reading ──────────────────────────────────────────────────────────
    def events(self) -> Iterator[dict]:
        """Every event, skipping any line that is not a whole event.

        A log is appended to by processes that can be killed mid-write, so a
        truncated final line is the normal failure, not an exotic one. Parsing
        it strictly made ONE bad byte destroy the entire mission -- and, because
        the board loads every session on its first request, one corrupt log
        anywhere took the board down for every session at once.

        An append-only log has to be readable up to the damage. A skipped line
        loses that one event; raising loses all of them.
        """
        if not self.log.exists():
            return
        for line in self.log.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                self.damaged += 1
                continue
            if isinstance(ev, dict):
                yield ev

    def load(self) -> Mission | None:
        m: Mission | None = None
        self.damaged = 0
        self.damage = []
        for ev in self.events():
            # A line that PARSES but is missing a field it needs was not
            # handled at all, and the consequences were worse than a torn
            # line: appending {"kind":"accepted","by":"human"} -- no
            # item_id -- gave a KeyError traceback from `mission show`
            # AND from `mission doctor`, the tool whose whole job is to
            # diagnose a damaged log. `whereami` went silent and the board
            # dropped the card with no message at all.
            #
            # SECURITY.md said damage is "surfaced, not swallowed". That
            # was true only for lines that fail to parse as JSON. A bad
            # event is now counted exactly like a torn one -- the same
            # counter, so one number means "lines this fold could not
            # use" rather than two kinds of damage with one name.
            try:
                k = ev.get("kind")
                if k == "created" and m is not None:
                    # A SECOND `created` is a bug, not a reset. Folding from the
                    # last one meant a stray init wiped a live plan: on 2026-08-19
                    # a session working in transcript-audit appended one to the
                    # Wayfinder mission with a chat message as the objective, and 52
                    # events -- a 26-item plan and 13 pending proposals -- went
                    # invisible in one line. Starting over is now something you
                    # SAY (`discarded`), not something a duplicate implies.
                    continue
                if k == "created":
                    m = Mission(id=self.root.name, session_id=ev.get("session_id", ""),
                                cwd=ev.get("cwd", ""), objective=ev.get("objective", ""),
                                parent_session=ev.get("parent_session", ""),
                                parent_item=ev.get("parent_item", ""),
                                created=ev.get("at", 0.0),
                                typed_by_human=ev.get("typed_by", "human") == "human")
                elif m is None:
                    continue
                elif k == "set":
                    if ev.get("typed_by", "human") != "human":
                        m.typed_by_human = False
                    f, v = ev["field"], ev["value"]
                    setattr(m, f, list(v) if f in LIST_FIELDS and isinstance(v, list) else v)
                elif k == "proposed":
                    m.checklist.append(Item(ev["item_id"], ev["text"],
                                            proposed_by=ev.get("by", "agent"),
                                            parent=ev.get("parent")).to_dict())
                elif k == "accepted":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            d["accepted"] = True
                            d["accepted_at"] = ev.get("at", 0.0)
                elif k == "completed":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            d["done"] = True
                            # The human's real tick supersedes the suggestion
                            # -- and the verifier's objection to it. A person
                            # ticking an item over a finding is allowed to be
                            # the last word; that is what one writer means.
                            d["claimed_done"] = ""
                            d["rework"] = ""
                            d["leased_by"] = ""
                            d["checked_note"] = ""
                elif k == "claims_done":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"] and not d["done"]:
                            d["claimed_done"] = ev.get("text", "")
                            d["claimed_by"] = ev.get("session_id", "")
                            # A fresh claim answers the last finding: the row
                            # moves out of the ready queue and into review,
                            # which is where a re-done item belongs. Any
                            # earlier check goes with it -- it was a check of
                            # a different claim.
                            d["rework"] = ""
                            d["checked_note"] = ""
                            d["checked_by"] = ""
                elif k == "leased":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            d["leased_by"] = ev.get("session_id", "")
                            d["leased_at"] = ev.get("at", 0.0)
                elif k == "released":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            d["leased_by"] = ""
                            d["leased_at"] = 0.0
                            d["released_at"] = ev.get("at", 0.0)
                            d["release_note"] = ev.get("text", "")
                elif k == "finding":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            # The claim was only ever a SUGGESTION, so a
                            # verifier clearing it takes nothing away from
                            # anyone: `done` still has exactly one writer and
                            # this item was never ticked. The lease goes too,
                            # or the row would sit "in progress" under a
                            # session that has already finished with it.
                            d["claimed_done"] = ""
                            d["leased_by"] = ""
                            d["leased_at"] = 0.0
                            d["rework"] = ev.get("text", "")
                            d["rework_by"] = ev.get("session_id", "")
                            d["rework_at"] = ev.get("at", 0.0)
                            # A new finding is a new reason, so an earlier
                            # handback no longer describes it.
                            d["released_at"] = 0.0
                            d["release_note"] = ""
                            d["checked_note"] = ""
                            d["checked_by"] = ""
                elif k == "checked":
                    for d in m.checklist:
                        if d["id"] == ev["item_id"]:
                            d["checked_note"] = ev.get("text", "")
                            d["checked_by"] = ev.get("session_id", "")
                elif k == "archived":
                    m.archived = True
                elif k == "unarchived":
                    m.archived = False
                elif k == "discarded":
                    # The only way to start over: written by
                    # `init --force --discard-plan`, explicitly, by a person.
                    m = None
                elif k == "removed":
                    gone = {ev["item_id"]}
                    # a removed subgoal takes its subtree with it
                    changed = True
                    while changed:
                        changed = False
                        for d in m.checklist:
                            if d.get("parent") in gone and d["id"] not in gone:
                                gone.add(d["id"])
                                changed = True
                    m.checklist = [d for d in m.checklist if d["id"] not in gone]
                elif k == "detour":
                    m.detours.append(ev["label"])
                elif k == "returned":
                    if m.detours:
                        m.detours.pop()
                elif k == "observed":
                    getattr(m, ev["field"]).append(ev["value"])
            except (KeyError, TypeError, AttributeError, ValueError,
                    IndexError) as exc:
                self.damaged += 1
                self.damage.append(f"{ev.get('kind', '?')}: "
                                   f"{type(exc).__name__}: {exc}")
                continue
        return m

    def why(self, fieldname: str) -> list[dict]:
        """Every event that touched this field, in order. Provenance is the log."""
        return [e for e in self.events()
                if e.get("field") == fieldname or e.get("kind") == "created"]


def _append_line(log: Path, line: str) -> None:
    """One line, one `os.write`, on an O_APPEND fd.

    It was `open("a", encoding="utf-8").write(...)` -- Python's buffered text
    writer, which makes no promise about how many `write(2)` calls it
    becomes. Two sessions appending at the same instant could therefore
    interleave MID-LINE and tear both events. "Concurrency is fine in
    practice" was the documented answer and nothing tested it.
    #
    A single `os.write` to an `O_APPEND` descriptor is atomic on POSIX for a
    payload up to the pipe/disk-block guarantee, and the kernel does the
    seek-to-end, so two writers cannot overwrite each other. Events here are
    small; a very long one (a many-line objective) is still one syscall,
    which is the strongest thing available without a lock on every append.
    """
    data = line.encode("utf-8")
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
    finally:
        os.close(fd)


class _LogLock:
    """An advisory lock around check-then-append.

    `_append_line` stops a line being TORN. It does nothing about the other
    race: every write method does `load()` -> check -> append, so two
    sessions can both read "not yet done", both pass the check, and both
    append -- or one can `claim_done` an item while a person `remove`s it.
    The fold tolerates most of that, which is why it was never urgent, and
    "the fold tolerates it" is not the same claim as "it cannot happen".

    Advisory, and on a sidecar file rather than the log itself: a reader that
    does not take the lock is unaffected, which keeps `show` and the board
    free of it.
    """

    def __init__(self, root: Path):
        self.path = Path(root) / ".lock"
        self.fd = -1

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        except OSError:
            self.fd = -1
            return self
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            # Windows has no flock; `msvcrt.locking` is the equivalent and is
            # not exercised by any platform this tool is tested on, so it is
            # attempted and never allowed to fail the write.
            try:
                import msvcrt
                msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        if self.fd >= 0:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        return False


def root_for(session_id: str, base: Path | None = None) -> Path:
    base = base or Path(os.environ.get(
        "AGENT_MISSION_HOME", Path.home() / ".agent-mission"))
    return Path(base) / session_id


def children_of(session_id: str, base: Path | None = None) -> dict[str, Mission]:
    """Delegated missions of this session, keyed by the item they came from.

    Scanning is fine: this is a directory of small files, and the alternative
    -- an index the parent writes -- would go stale the moment a child is
    created from anywhere else.
    """
    home = Path(base or os.environ.get(
        "AGENT_MISSION_HOME", Path.home() / ".agent-mission"))
    out: dict[str, Mission] = {}
    if not home.exists():
        return out
    for d in sorted(home.iterdir()):
        if not d.is_dir() or not (d / "events.jsonl").exists():
            continue
        m = MissionStore(d).load()
        if m and m.parent_session == session_id and m.parent_item:
            out[m.parent_item] = m
    return out
