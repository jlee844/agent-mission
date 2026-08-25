"""Check what the agent told you against what is on disk.

Ported from mission-layer's `claims.py` (the private repo where it found a
four-week-old config bug by checking one claim against the filesystem), then
adapted for a clean clone — not pasted. The differences from the origin are
deliberate and listed here because each was a works-only-for-the-author bug:

  * the author's `builds/` directory convention is gone from root detection;
    only a `.git` walk remains
  * extraction has a CONTRACT format first (see `_LEGIBLE`) and treats the
    English templates as fallback — the templates were tuned on one person's
    corpus, and any accuracy number they produced is a one-corpus result
  * a local patterns file (`~/.agent-mission/claim-patterns.txt`) extends
    extraction; a person edits it, agents may only PROPOSE lines for it
  * fixtures in tests are synthetic; no fragment of a real transcript ships

The module still does not judge. It resolves each completion claim to the
tool calls that should back it and asks questions with mechanical answers:
did the supporting call succeed, does the artifact exist, does it contain
what was attempted. Ground truth is the filesystem — a judge can be wrong
about whether work happened; `open()` cannot. Nothing here is learned,
scored, or statistical: mechanical or marked, never inferred.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

LOOKBACK = 25          # tool calls before a claim that could back it
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# The contract format, and the reason this can work on corpora the author has
# never seen: commands/mission.md tells agents to state completion by naming
# the artifact -- `done: 237 tests pass (tests/test_claims.py)`. A claim
# shaped like that is trivially extractable and trivially checkable, with no
# dependence on anyone's phrasing. We ship the verifier AND the contract that
# shapes the speech it reads.
_LEGIBLE = re.compile(r"\bdone:\s*(?P<what>[^()\n]{3,200}?)\s*"
                      r"\((?P<artifact>[^()\n]{1,200})\)", re.I)

# Fallback: English completion templates. A claim asserts STATE; narration
# does not, and conflating the two is how false alarms happen.
_CLAIM = re.compile(
    r"\b("
    r"(?:is|are|was|were|now)\s+(?:all\s+)?(?:done|complete|completed|fixed|"
    r"working|verified|live|set|shipped|in place|green|passing)"
    r"|all (?:tests? )?(?:pass|passing|passed|clean|green)"
    r"|tests? (?:pass|passing|passed)"
    r"|(?:done|fixed|shipped|complete)[.!]"
    r"|verified\b"
    r"|no errors\b"
    r")", re.I)

# Sentences that describe what is about to happen are never claims.
_NARRATION = re.compile(r"^\s*(now|next|let me|i'?ll|then|first|adding|"
                        r"building|writing|checking|running|starting)\b", re.I)

# "Did anything test-shaped run" -- not "did pytest run".
_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|"
                       r"unittest|tox|rspec)\b|test_[\w-]+\.\w+|"
                       r"[\w-]+_test\.\w+|\btests?[/.]|\btest_[\w-]+", re.I)

_DELEGATED = "Agent"    # work in a subagent's transcript is invisible here

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")

# Statuses worth telling a person about. Everything else is silence.
REPORT = ("unbacked", "contradicted", "unverified_tests")


def _extra_patterns() -> list[re.Pattern]:
    """One regex per line from the local patterns file, human-maintained.

    This is the whole extension mechanism: when `mission doctor` shows your
    agent's claims going undetected, you (or an agent, via a PROPOSAL you
    accept) add a line here. The verifier that audits agents is never edited
    silently by one. A bad line is skipped, not fatal.
    """
    p = Path(os.environ.get("AGENT_MISSION_HOME",
                            Path.home() / ".agent-mission")) / "claim-patterns.txt"
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(re.compile(line, re.I))
            except re.error:
                continue
    except OSError:
        pass
    return out


@dataclass
class Support:
    tool: str
    target: str
    ok: bool
    attempted: str = ""      # new_string / content, for the on-disk check


@dataclass
class Claim:
    session_id: str
    block_index: int
    sentence: str
    mentions_tests: bool
    support: list = field(default_factory=list)
    artifact: str = ""       # set for contract-format claims
    status: str = "unchecked"
    detail: str = ""

    @property
    def failed_writes(self) -> list:
        ok_targets = {s.target for s in self.support if s.ok and s.target}
        return [s for s in self.support
                if not s.ok and s.tool in WRITE_TOOLS and s.target
                and s.target not in ok_targets]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failed_writes"] = [asdict(s) for s in self.failed_writes]
        return d


def _target(inp: dict) -> str:
    for k in ("file_path", "notebook_path", "command"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _attempted(inp: dict) -> str:
    for k in ("new_string", "content"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _read_lines(path: Path, tail_bytes) -> list[str]:
    """Whole file, or just the end -- a live check only cares about what was
    just claimed, and a 90 MB session costs 0.5s to read in full."""
    if tail_bytes is None:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()          # discard the partial line
        raw = fh.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def iter_claims(path: Path, session_id: str, tail_bytes=None,
                since_block: int = 0) -> Iterator[Claim]:
    extra = _extra_patterns()
    stream: list = []
    for line in _read_lines(path, tail_bytes):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        role, content = msg.get("role"), msg.get("content")
        if isinstance(content, str):
            stream.append((role, "text", content))
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "tool_use",
                                                         "tool_result"):
                stream.append((role, b["type"], b))

    errored: dict = {}
    for _, kind, blk in stream:
        if kind == "tool_result" and isinstance(blk, dict):
            errored[blk.get("tool_use_id")] = bool(blk.get("is_error"))

    for i, (role, kind, blk) in enumerate(stream):
        if role != "assistant" or kind != "text" or i < since_block:
            continue
        text = (blk.get("text") if isinstance(blk, dict) else str(blk)) or ""

        found: list[tuple[str, str]] = []          # (sentence, artifact)
        for m in _LEGIBLE.finditer(text):
            found.append((" ".join(m.group(0).split())[:300],
                          m.group("artifact").strip()))
        for sent in _SENT.split(text):
            s = " ".join(sent.split())
            if not s or len(s) > 400 or _NARRATION.match(s):
                continue
            if _LEGIBLE.search(s):
                continue                            # already captured above
            if not (_CLAIM.search(s) or any(p.search(s) for p in extra)):
                continue
            found.append((s[:300], ""))

        if not found:
            continue
        support: list = []
        for _, k2, p2 in reversed(stream[max(0, i - LOOKBACK * 3):i]):
            if k2 != "tool_use" or not isinstance(p2, dict):
                continue
            inp = p2.get("input") or {}
            support.append(Support(
                tool=p2.get("name") or "?", target=_target(inp),
                ok=not errored.get(p2.get("id"), False),
                attempted=_attempted(inp)[:2000],
            ))
            if len(support) >= LOOKBACK:
                break
        support = list(reversed(support))

        for s, artifact in found:
            yield Claim(
                session_id=session_id, block_index=i, sentence=s,
                mentions_tests=bool(re.search(r"\btests?\b", s, re.I)),
                support=list(support), artifact=artifact,
            )


def verify(claim: Claim, cwd: str = "") -> Claim:
    """Resolve a claim against disk. Never guesses; says so when it cannot tell."""
    # Contract-format claims name their own artifact, so they are checkable
    # even with no supporting call -- that is the point of the format.
    if claim.artifact:
        p = Path(claim.artifact)
        if not p.is_absolute() and cwd:
            p = Path(cwd) / p
        if p.exists():
            claim.status, claim.detail = "backed", f"artifact exists: {p.name}"
        else:
            claim.status = "unbacked"
            # The NAME end of the path, not the front: scan() truncates
            # details, and a deep tmp or home prefix ate the one part a
            # person needs to recognise -- which file the claim invented.
            tail = "/".join(str(p).rsplit("/", 2)[-2:])
            claim.detail = f"named artifact does not exist: {tail}"
        return claim

    if not claim.support:
        claim.status, claim.detail = "no_support", "no tool call precedes this claim"
        return claim

    if claim.mentions_tests:
        ran = [s for s in claim.support if _TEST_CMD.search(s.target)]
        if not ran:
            if any(s.tool == _DELEGATED for s in claim.support):
                claim.status = "delegated"
                claim.detail = "a subagent did this work; not visible from here"
                return claim
            claim.status = "unverified_tests"
            claim.detail = "claims tests pass; no test command ran beforehand"
            return claim
        if all(not s.ok for s in ran):
            claim.status = "contradicted"
            claim.detail = f"the test command failed: {ran[-1].target[:120]}"
            return claim

    bad = claim.failed_writes
    if not bad:
        claim.status, claim.detail = "backed", "supporting calls succeeded"
        return claim

    still_missing: list = []
    relocated: list = []
    search_root = _root_of(bad[0].target)
    for s in bad:
        p = Path(s.target)
        if not p.is_absolute() or not s.attempted:
            still_missing.append(f"{p.name} (cannot check)")
            continue
        if not p.exists():
            # A project rename moves the file without touching the transcript.
            # Found elsewhere with the change in it is a MOVE, not a lie.
            moved = _find_moved(p, s.attempted, search_root)
            if moved:
                relocated.append(f"{p.name} -> {moved}")
            else:
                still_missing.append(f"{p.name} does not exist")
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            still_missing.append(f"{p.name} unreadable: {exc}")
            continue
        probe = " ".join(s.attempted.split())[:80]
        if probe and probe not in " ".join(body.split()):
            still_missing.append(f"{p.name} does NOT contain the attempted change")

    if still_missing:
        claim.status = "unbacked"
        claim.detail = "; ".join(still_missing[:3])
    elif relocated:
        claim.status = "moved"
        claim.detail = "write failed at the recorded path; found elsewhere: " \
                       + "; ".join(relocated[:2])
    else:
        claim.status = "landed_later"
        claim.detail = "write failed, but the change is on disk now"
    return claim


def _root_of(path: str):
    """Nearest .git upward. The origin also accepted the author's `builds/`
    directory convention here, which made the bounded search work on exactly
    one machine in the world."""
    p = Path(path)
    for parent in p.parents:
        if (parent / ".git").exists():
            return parent
    return p.parents[2] if len(p.parents) > 2 else None


def _find_moved(missing: Path, attempted: str, root):
    """Same basename elsewhere under root, containing the attempted change."""
    if root is None or not root.exists() or not attempted:
        return None
    probe = " ".join(attempted.split())[:80]
    for cand in list(root.rglob(missing.name))[:40]:
        if "__pycache__" in cand.parts or cand.suffix == ".pyc":
            continue
        try:
            body = " ".join(cand.read_text(encoding="utf-8",
                                           errors="replace").split())
        except OSError:
            continue
        if probe in body:
            return str(cand.relative_to(root))
    return None


def scan(path: Path, session_id: str, cwd: str = "",
         tail_bytes: int = 400_000) -> dict:
    """One pass over a transcript tail: counts, and the reportable findings.

    The board calls this per live session, so it has to be cheap and it has
    to never raise -- a transcript in a format this module does not know is
    a zero, not an error.
    """
    out = {"checked": 0, "reportable": [], "extracted": 0}
    try:
        for c in iter_claims(path, session_id, tail_bytes=tail_bytes):
            out["extracted"] += 1
            v = verify(c, cwd=cwd)
            out["checked"] += 1
            if v.status in REPORT:
                out["reportable"].append(
                    {"sentence": v.sentence[:140], "status": v.status,
                     "detail": v.detail[:140]})
    except Exception:
        pass
    return out


def verdict_for(text: str, cwd: str = "") -> dict:
    """C17: the disk's verdict on one claims-done suggestion.

    The text is the legible format, possibly several `done: ... (artifact)`
    clauses. Every named artifact is checked; a suggestion with NO checkable
    artifact is honestly 'unchecked', never counted as backed -- an empty
    claim sweeping into the counter would be C16's whole argument, lost.
    """
    arts = [m.group("artifact").strip() for m in _LEGIBLE.finditer(text or "")]
    backed, unbacked = [], []
    for a in arts:
        p = Path(a)
        if not p.is_absolute() and cwd:
            p = Path(cwd) / a
        (backed if p.exists() else unbacked).append(a)
    return {"backed": len(backed), "unbacked": unbacked,
            "ok": bool(backed) and not unbacked}
