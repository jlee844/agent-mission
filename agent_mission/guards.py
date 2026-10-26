"""A visible record of the guardrails firing.

The authority split, the tty gate and the claim verifier all work by REFUSING
things, which means their successes are invisible: nothing on disk said how
often they had caught something, so "is this necessary?" could only be
answered from memory. This is the ledger -- append-only, local, one line per
activation.

⚠️ It is EVIDENCE, not enforcement. Nothing reads it to make a decision, and
recording is best-effort by design: a guard firing is already a failure path,
so if the ledger itself threw it would turn a clean refusal into a crash.
Every public call here swallows its own errors.

⚠️ It is also not a security log. The event log is a plain file and so is
this; a determined agent can append to it or truncate it. What it is good for
is the honest question of whether the guards earn their complexity.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# The kinds that exist. Not enforced -- an unknown kind is still recorded,
# because a guard nobody thought to name is exactly the one worth seeing.
KINDS = (
    "refused_protected_field",   # an agent tried to write a field you own
    "refused_no_human",          # the tty gate turned down a human-only verb
    "claim_unverified",          # claims-done could not check the artifact
    "wrong_write_code",          # a bad board passcode
)


def _home() -> Path:
    # Honours AGENT_MISSION_HOME at CALL time, not import time -- every test
    # sets it per-test, and a module constant would read one tmpdir for the
    # whole session. Same reason as heartbeat._home.
    return Path(os.environ.get("AGENT_MISSION_HOME")
                or Path.home() / ".agent-mission")


def _path() -> Path:
    return _home() / "guards.jsonl"


def record(kind: str, detail: str = "") -> None:
    """Append one activation. Never raises -- see the module docstring."""
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": time.time(), "kind": str(kind), "detail": str(detail)[:400]}
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        # Deliberately silent. A print here would land in the middle of the
        # refusal message the user is reading.
        pass


def _rows() -> list[dict]:
    try:
        text = _path().read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            # A half-written line must not blank the count: the file is
            # appended to from several processes and a crash mid-write is
            # ordinary, not exceptional.
            continue
        if isinstance(row, dict) and row.get("kind"):
            out.append(row)
    return out


def tally() -> dict[str, int]:
    """How many times each kind has fired. {} when nothing has."""
    out: dict[str, int] = {}
    for row in _rows():
        k = str(row["kind"])
        out[k] = out.get(k, 0) + 1
    return out


def recent(n: int = 10) -> list[dict]:
    """The newest activations first, at most n of them."""
    rows = _rows()
    rows.sort(key=lambda r: r.get("at") or 0.0, reverse=True)
    return rows[:max(0, n)]
