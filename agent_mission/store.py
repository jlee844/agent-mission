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
LIST_FIELDS = {"success_criteria", "constraints", "non_goals", "checklist",
               "decisions", "evidence", "notes"}


class ProtectedFieldError(PermissionError):
    """Raised when an agent tries to write a field the human owns."""


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
        return len(self.leaves)

    @property
    def complete(self) -> bool:
        """A branch is done when its leaves are; a leaf when it is ticked."""
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

    @property
    def total_count(self) -> int:
        return len(self.leaves)

    @property
    def pending(self) -> list[Item]:
        return [i for i in self.items if not i.done]

    @property
    def unaccepted(self) -> list[Item]:
        """Agent-proposed items you have not signed off."""
        return [i for i in self.items if not i.accepted]

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

    # ── writing ──────────────────────────────────────────────────────────
    def _append(self, kind: str, by: str, /, **detail: Any) -> dict:
        # kind/by are positional-only so a payload may legitimately carry a
        # field called "kind" -- and the envelope is written LAST so it wins.
        # Without the ordering, such a payload overwrites the event's own kind
        # and the event becomes unfindable by the reader.
        ev = {**detail, "kind": kind, "by": by, "at": time.time()}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")
        return ev

    def create(self, session_id: str, cwd: str, objective: str, by: str,
               parent_session: str = "", parent_item: str = "") -> dict:
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
        return self._append("created", by, session_id=session_id, cwd=cwd,
                            objective=objective, parent_session=parent_session,
                            parent_item=parent_item)

    def set_protected(self, fieldname: str, value: Any, by: str) -> dict:
        """Only a human may write a protected field. No agent path exists."""
        if fieldname not in PROTECTED_FIELDS:
            raise ValueError(f"{fieldname} is not protected")
        if by != "human":
            raise ProtectedFieldError(
                f"{fieldname} is yours; the agent cannot set it")
        return self._append("set", by, field=fieldname, value=value)

    def propose(self, text: str, by: str = "agent",
                parent: str | None = None) -> dict:
        """Suggest a node. Inert until accepted. `parent` nests it under another."""
        return self._append("proposed", by, item_id=uuid.uuid4().hex[:8],
                            text=text, parent=parent)

    def _require(self, item_id: str) -> None:
        m = self.load()
        if m is None or not any(d["id"] == item_id for d in m.checklist):
            raise NoSuchItemError(item_id)

    def accept(self, item_id: str, by: str) -> dict:
        if by != "human":
            raise ProtectedFieldError("only you can accept a proposal")
        self._require(item_id)
        return self._append("accepted", by, item_id=item_id)

    def complete(self, item_id: str, by: str) -> dict:
        """Marking work done is a judgement, so it stays with the human."""
        if by != "human":
            raise ProtectedFieldError(
                "the agent may record evidence, not declare an item done")
        self._require(item_id)
        return self._append("completed", by, item_id=item_id)

    def remove(self, item_id: str, by: str) -> dict:
        """Drop an item from the plan. A soft delete: the event log keeps it.

        Append-only is about not losing history, not about being unable to
        change your mind. A plan you cannot prune stops being a plan.
        """
        if by != "human":
            raise ProtectedFieldError("only you can remove an item")
        self._require(item_id)
        return self._append("removed", by, item_id=item_id)

    def observe(self, fieldname: str, text: str, by: str = "agent") -> dict:
        if FIELD_AUTHORITY.get(fieldname) is not Authority.OBSERVABLE:
            raise ValueError(f"{fieldname} is not observable")
        return self._append("observed", by, field=fieldname, value=text)

    # ── reading ──────────────────────────────────────────────────────────
    def events(self) -> Iterator[dict]:
        if not self.log.exists():
            return iter(())
        return (json.loads(l) for l in
                self.log.read_text(encoding="utf-8").splitlines() if l.strip())

    def load(self) -> Mission | None:
        m: Mission | None = None
        for ev in self.events():
            k = ev.get("kind")
            if k == "created":
                m = Mission(id=self.root.name, session_id=ev.get("session_id", ""),
                            cwd=ev.get("cwd", ""), objective=ev.get("objective", ""),
                            parent_session=ev.get("parent_session", ""),
                            parent_item=ev.get("parent_item", ""),
                            created=ev.get("at", 0.0))
            elif m is None:
                continue
            elif k == "set":
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
            elif k == "completed":
                for d in m.checklist:
                    if d["id"] == ev["item_id"]:
                        d["done"] = True
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
            elif k == "observed":
                getattr(m, ev["field"]).append(ev["value"])
        return m

    def why(self, fieldname: str) -> list[dict]:
        """Every event that touched this field, in order. Provenance is the log."""
        return [e for e in self.events()
                if e.get("field") == fieldname or e.get("kind") == "created"]


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
