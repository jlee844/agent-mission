"""Check what the agent told you against what is on disk.

Ported from probe-lab's `claims.py` (the private repo where it found a
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

# "Did a test RUNNER run" -- which is not the same as "a test path appeared
# somewhere in a command". The path-only alternatives made all four of these
# count as evidence that the suite passed:
#
#     ls tests/
#     cat tests/test_x.py
#     grep -n foo test_api.py
#     git add tests/
#
# so a claim "all tests pass" after `ls tests/` came back BACKED. Reading a
# test file is the opposite of running it.
#
# So a runner token has to START a command segment. Segments split on &&, ;
# and |, and a leading `cd …`, `env X=…`, `uv run`, `poetry run`, `python -m`
# or `npx` is stepped over, because those legitimately precede the runner.
_RUNNERS = (r"pytest|py\.test|jest|vitest|mocha|tox|nox|rspec|phpunit|"
            r"gradle|mvn|ctest|unittest|nextest")
_SEGMENT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
_PREFIX = re.compile(
    r"^(?:(?:cd|pushd)\s+\S+|env(?:\s+\w+=\S*)+|sudo|time|"
    r"(?:uv|poetry|pipenv|pdm|hatch|rye)\s+run|npx|pnpm(?:\s+(?:run|exec))?|"
    r"yarn(?:\s+run)?|npm\s+(?:run|exec)|bun(?:\s+run)?|"
    # `make` is NOT a prefix to peel: peeling it left a bare `test`, so
    # `make test` -- a real runner invocation -- came back False. It is
    # handled by its own rule below instead.
    r"python3?(?:\.\d+)?\s+-m|poetry)\s+", re.I)


def ran_a_test_runner(command: str) -> bool:
    """Did this command line actually invoke a test runner?"""
    if not command:
        return False
    for seg in _SEGMENT.split(command):
        seg = seg.strip()
        # Peel the prefixes that legitimately sit in front of a runner.
        for _ in range(4):
            stripped = _PREFIX.sub("", seg, count=1)
            if stripped == seg:
                break
            seg = stripped.strip()
        if not seg:
            continue
        head = seg.split()[0]
        head = head.rsplit("/", 1)[-1]          # .venv/bin/pytest -> pytest
        if re.fullmatch(_RUNNERS, head, re.I):
            return True
        # `go test ./...`, `cargo test`, `npm test`, `make test`,
        # `dotnet test`, `swift test`, `bazel test //...`
        if re.match(r"^(go|cargo|npm|yarn|pnpm|bun|dotnet|swift|bazel|"
                    r"gradlew|\./gradlew)\s+test\b", seg, re.I):
            return True
        # A named script whose name IS a test target: `npm run test:unit`,
        # `yarn test:e2e`. The prefix peeler has already removed `npm run`,
        # so this sees the script name on its own.
        if re.match(r"^test[:\-]\S+", seg, re.I):
            return True
        if re.match(r"^make\s+(test|check)\b", seg, re.I):
            return True
    return False


# Kept for the places that want the loose "is this test-shaped" question --
# never for deciding whether a suite ran.
_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|"
                       r"npm (run )?test|unittest|tox|rspec)\b", re.I)

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
    # The transcript record this claim was written in. Stable across window
    # slides, unlike `block_index`, which is a position in the tail window.
    record_uuid: str = ""
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


# What the tail is actually for: the recent ASSISTANT turns, because that is
# where a claim can be. Bytes were a proxy for that and the proxy broke --
# measured on a real 1,154 MB transcript, a 400 KB tail held 43 lines and
# THREE assistant text blocks, because that session's records are megabytes
# each (large tool results). The scan returned zero claims in 0.0 seconds and
# `doctor` reported it as "the extractor may be blind to its phrasing", which
# was wrong: the extractor never saw a sentence.
#
# So the window grows until it holds enough turns, with a hard byte ceiling
# so a pathological transcript cannot make the board read a gigabyte. On the
# same file: 0.4 MB -> 3 blocks, 2 MB -> 18, 8 MB -> 24, 32 MB -> 253.
MIN_ASSISTANT_BLOCKS = 12
MAX_TAIL_BYTES = 16_000_000


def _count_assistant_text(lines: list[str]) -> int:
    """How many assistant text blocks a window actually contains."""
    n = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            n += 1
        elif isinstance(content, list):
            n += sum(1 for b in content
                     if isinstance(b, dict) and b.get("type") == "text")
    return n


def _tail(path: Path, tail_bytes: int) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()          # discard the partial line
        raw = fh.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def _read_lines(path: Path, tail_bytes) -> list[str]:
    """Whole file, or a tail big enough to hold some assistant turns.

    A 90 MB session costs 0.5s to read in full, which is why the board reads
    a tail instead; the tail now widens by doubling until it has turns in it
    or hits MAX_TAIL_BYTES. A file smaller than the window is read once --
    the loop cannot re-read it, because the window stops growing as soon as
    it covers the file.
    """
    if tail_bytes is None:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    size = path.stat().st_size
    window = max(1, int(tail_bytes))
    lines = _tail(path, window)
    while (window < size and window < MAX_TAIL_BYTES
           and _count_assistant_text(lines) < MIN_ASSISTANT_BLOCKS):
        window = min(window * 4, MAX_TAIL_BYTES)
        lines = _tail(path, window)
    return lines


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
        # The RECORD's own uuid. `block_index` is a position in the tail
        # WINDOW, so once a transcript passes the window size every turn
        # shifts it and every old claim gets a new index -- a new key, a
        # re-verify, and an already-reported finding reported again. A uuid
        # does not move when the window slides.
        uid = rec.get("uuid") or rec.get("requestId") or ""
        if isinstance(content, str):
            stream.append((role, "text", content, uid))
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "tool_use",
                                                         "tool_result"):
                stream.append((role, b["type"], b, uid))

    errored: dict = {}
    for _, kind, blk, _uid in stream:
        if kind == "tool_result" and isinstance(blk, dict):
            errored[blk.get("tool_use_id")] = bool(blk.get("is_error"))

    for i, (role, kind, blk, uid) in enumerate(stream):
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
        for _, k2, p2, _u2 in reversed(stream[max(0, i - LOOKBACK * 3):i]):
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
                session_id=session_id, block_index=i, record_uuid=uid,
                sentence=s,
                mentions_tests=bool(re.search(r"\btests?\b", s, re.I)),
                support=list(support), artifact=artifact,
            )


def finding_key(claim) -> str:
    """The identity of a finding, for "have I already reported this?".

    Was `f"{block_index}:{sentence[:60]}"`. `block_index` counts blocks
    inside the 400 KB TAIL WINDOW, so the moment a transcript outgrows the
    window every turn shifts every index -- new keys for old claims, and
    unbacked ones re-reported on every turn for the rest of the session.

    The record's uuid does not move. The sentence is hashed rather than
    truncated so two long claims that share an opening are still two
    findings. Falls back to the index when a transcript carries no uuid,
    which keeps short sessions working and is honest about being weaker.
    """
    import hashlib
    body = hashlib.sha1(
        (claim.sentence or "").encode("utf-8", "replace")).hexdigest()[:12]
    anchor = claim.record_uuid or f"b{claim.block_index}"
    return f"{anchor}:{body}"


def resolve(path: str, mission_cwd: str = "", session_cwd: str = "") -> Path:
    """The ONE place a relative artifact path becomes an absolute one.

    There were three, and they disagreed, so the same
    `done: X (src/a.py)` could read "backed" where the agent wrote it and
    "NOT on disk" on the board:

      * the Stop hook passed the SESSION's cwd (the hook payload),
      * `claims-done` passed the AGENT PROCESS's cwd (`Path.cwd()`),
      * the board and the sweep passed the MISSION's cwd,
      * and both verifiers fell back to the server process's own cwd when
        given "" -- whatever directory the board happened to be started in.

    Order: absolute wins; then the MISSION's cwd, because the mission is what
    the claim is about and it is the same answer from every surface; then the
    session's, for a claim made before any mission names a directory. Never
    the process cwd -- that is the one that made the verdict depend on who
    was asking. The author's own ten claims came back NOT backed through
    exactly this, and the verifier was right.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    base = mission_cwd or session_cwd
    return (Path(base) / p) if base else p


def verify(claim: Claim, cwd: str = "", mission_cwd: str = "") -> Claim:
    """Resolve a claim against disk. Never guesses; says so when it cannot tell."""
    # Contract-format claims name their own artifact, so they are checkable
    # even with no supporting call -- that is the point of the format.
    if claim.artifact:
        p = resolve(claim.artifact, mission_cwd=mission_cwd, session_cwd=cwd)
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
        ran = [s for s in claim.support if ran_a_test_runner(s.target)]
        if not ran:
            if any(s.tool == _DELEGATED for s in claim.support):
                claim.status = "delegated"
                claim.detail = "a subagent did this work; not visible from here"
                return claim
            claim.status = "unverified_tests"
            claim.detail = "claims tests pass; no test command ran beforehand"
            return claim
        # The MOST RECENT run decides. `all(...)` meant a passing run
        # followed by a FAILING one came back backed -- you only had to have
        # succeeded once, ever, in the window. That is the wrong way round:
        # the last thing that happened is the current state of the suite.
        if not ran[-1].ok:
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
         mission_cwd: str = "", tail_bytes: int = 400_000) -> dict:
    """One pass over a transcript tail: counts, and the reportable findings.

    The board calls this per live session, so it has to be cheap and it has
    to never raise -- a transcript in a format this module does not know is
    a zero, not an error.
    """
    out = {"checked": 0, "reportable": [], "extracted": 0}
    try:
        for c in iter_claims(path, session_id, tail_bytes=tail_bytes):
            out["extracted"] += 1
            v = verify(c, cwd=cwd, mission_cwd=mission_cwd)
            out["checked"] += 1
            if v.status in REPORT:
                out["reportable"].append(
                    {"sentence": v.sentence[:140], "status": v.status,
                     "detail": v.detail[:140]})
    except Exception:
        pass
    return out



# C18. Artifact extraction used to be `\(([^()]{1,200})\)` immediately after the
# description, which produced FALSE NEGATIVES on three shapes seen in one real
# batch -- 17 of 18 suggestions came back unchecked or unbacked while every file
# named existed:
#
#   "done: LLM map (five call sites) present (wayfinder.md)"
#        -> grabbed "five call sites" and reported it UNBACKED
#   "done: gap gone (career_index.json gaps)"
#        -> treated the whole clause as one path
#   "done: extractor (snippets.py, lock.json)"
#        -> treated two artifacts as one path
#
# Reporting "the disk disagrees" without having asked the disk is worse than
# staying silent, because the board renders it as evidence against the claim.
# So: take the LAST parenthesised group of each `done:` clause (descriptions may
# contain parentheses, artifacts are what the author put at the end), split it on
# separators, and keep only the path-shaped tokens. Prose-only claims still yield
# nothing, which is the C16 guarantee and is covered by its own test.
_CLAUSE = re.compile(r"\bdone:\s*(?P<body>[^\n]{3,400})", re.I)
# A path or a filename: has a separator, or a bare name with an extension.
# Parentheses are legal INSIDE a path -- Expo Router names route groups
# `(auth)` / `(tabs)`, so they appear in every React Native app in this
# workspace.
_PATHY = re.compile(r"^[\w.()@+-]*[/\\][\w./\\()@+-]*$"
                    r"|^[\w@+-]+\.[A-Za-z0-9_]{1,12}$")


def _last_group(body: str) -> str:
    """The last top-level (...) group, tolerating parens nested inside it.

    This was `\\(([^()\\n]{1,200})\\)`, which cannot match a group containing a
    parenthesis -- so `done: login localised (mobile/app/(auth)/index.tsx)`
    extracted NOTHING and the row went silent, while the file sat right there
    on disk. Silence reads as "the author cited nothing", which is the opposite
    of what happened, and Expo Router puts parentheses in the path of every
    routed screen. Scanned from the right because the artifact is what the
    author put at the END; descriptions may contain parentheses of their own.
    """
    depth, end = 0, -1
    for i in range(len(body) - 1, -1, -1):
        if body[i] == ")":
            if depth == 0:
                end = i
            depth += 1
        elif body[i] == "(":
            depth -= 1
            if depth == 0 and end != -1:
                return body[i + 1:end]
            if depth < 0:                 # unbalanced prose; give up cleanly
                return ""
    return ""


# C19: narrower than `_PATHY`, and the narrowing is MEASURED, not tidied.
# `_PATHY` is applied by `_artifacts` to the last parenthesised group of a
# `done:` clause -- a place where the author has already said "this is the
# artifact" -- so it can afford to be loose. Pointed at free prose it reads
# ordinary writing as paths: on the live board, 3 of 6 ready items it called
# artifact-naming were `Item.sessions` (an attribute), `j/k` (two keys) and
# `accepted/unleased/not-done` (a list). Each would have told a human that a
# context-free session could prove the item finished, which is the one claim
# this must not get wrong.
#
# So a file-shaped token is a path-shaped one whose LAST SEGMENT is either
# empty (it ends in a separator, i.e. a directory) or carries a suffix short
# enough to be an extension. A length test rather than a whitelist, because a
# whitelist of extensions is a list someone has to remember to extend --
# exactly the failure mode `DEFENCE_CO` keeps demonstrating elsewhere.
_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def looks_like_a_file(tok: str) -> bool:
    """Path-shaped AND plausibly a file or directory. One definition."""
    tok = (tok or "").strip()
    if not _PATHY.match(tok):
        return False
    if tok.endswith(("/", "\\")):
        return True
    last = tok.replace("\\", "/").split("/")[-1]
    return bool(_SUFFIX.search(last))


def _artifacts(text: str) -> list[str]:
    """Every checkable artifact named in the legible `done: ... (...)` format."""
    out: list[str] = []
    for clause in _CLAUSE.finditer(text):
        group = _last_group(clause.group("body"))
        if not group:
            continue
        for tok in re.split(r"[,;]| and ", group):
            tok = tok.strip().strip("`'\"")
            # Trailing prose after a path ("career_index.json gaps") is common,
            # so prose words are skipped rather than discarding the clause --
            # but EVERY path-shaped word is kept, not just the first.
            #
            # ⚠️ Taking only the first silently dropped artifacts whenever the
            # author separated them with SPACES rather than commas. Seen live
            # on 2026-10-08: a peer claimed four paths space-separated and the
            # verdict read "1 artifact(s) present", having checked only the
            # README. The claim looked corroborated on the strength of one
            # file out of four, which is a verdict reported more confidently
            # than it was earned.
            for w in tok.split():
                w = w.strip("`'\"")
                if _PATHY.match(w):
                    out.append(w)
    return out


# Directories whose contents move without anybody doing work.
_NOT_EVIDENCE = {"__pycache__", "node_modules", ".git", ".pytest_cache",
                 ".mypy_cache", ".ruff_cache", ".venv", "venv", ".tox",
                 "dist", "build", ".DS_Store", ".idea", ".vscode"}


def _is_evidence(child: Path) -> bool:
    """A regular file whose change could plausibly BE the work claimed."""
    if not child.is_file():
        return False
    for part in child.parts:
        if part in _NOT_EVIDENCE or (part.startswith(".") and len(part) > 1):
            return False
    return True


def _touched_since(p: Path, since: float) -> bool:
    """Has this artifact changed since the work was agreed?

    A directory counts if ANYTHING inside it has -- naming a package as the
    artifact of a change inside it is normal and honest, and requiring the
    directory's own mtime to move would reject it on most filesystems.
    Bounded walk: a claim naming a huge tree must not stall the Stop hook.
    """
    try:
        if not p.is_dir():
            return p.stat().st_mtime > since
        # ⚠️ A DIRECTORY's own mtime is not evidence: it moves when anything
        # is created inside it, including the caches excluded below. Checking
        # it first short-circuited the whole filter -- `pkg/` came back fresh
        # because `.git/` had been created in it. So for a directory, only a
        # qualifying FILE inside can make it fresh.
        seen = 0
        for child in p.rglob("*"):
            seen += 1
            if seen > 2000:
                return False          # give up rather than hang; stays "stale"
            # ⚠️ Build droppings are not work. `tests/` went fresh — and so
            # sweepable — the moment pytest wrote `tests/__pycache__`, with
            # nobody having touched a test. Regular files only, and no cache
            # or dot-directory on the path: those change for reasons that
            # have nothing to do with the claim.
            if not _is_evidence(child):
                continue
            try:
                if child.stat().st_mtime > since:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _too_broad(p: Path, cwd: str) -> bool:
    """Is this artifact so wide that "something under it changed" proves nothing?

    The hole a second reader demonstrated on 2026-10-08: `done: rewrote the
    auth flow (./)` resolves to the mission's own folder, `_touched_since`
    walks it, an unrelated `unrelated.log` written after acceptance makes it
    fresh -- and "confirm all backed" then ticks the item `by=human`. The
    same goes for `/` and the home directory. Naming everything is not
    naming an artifact.

    Deliberately narrow: a named SUBdirectory still counts. `tests/` is a
    real answer to "what did you change", and rejecting it would push
    authors back to prose, which is the state C16 exists to improve on. The
    line is drawn at the mission root and anything containing it, because
    those are the paths that cannot fail.
    """
    try:
        p = p.resolve()
    except OSError:
        return False
    if p == Path(p.anchor) or p == Path.home().resolve():
        return True
    if not cwd:
        return False
    try:
        root = Path(cwd).resolve()
    except OSError:
        return False
    # The mission root itself, or any ancestor of it.
    return p == root or p in root.parents


def verdict_for(text: str, cwd: str = "", accepted_at: float = 0.0) -> dict:
    """C17: the disk's verdict on one claims-done suggestion.

    The text is the legible format, possibly several `done: ... (artifact)`
    clauses. Every named artifact is checked; a suggestion with NO checkable
    artifact is honestly 'unchecked', never counted as backed -- an empty
    claim sweeping into the counter would be C16's whole argument, lost.

    `ok` ALSO requires every artifact to have changed since `accepted_at`.
    Existence alone made `done: rewrote auth (README.md)` backed in any repo
    with a README, and "confirm all backed" then ticked it `by=human` --
    which is the sweep the README calls "pre-evidenced". Existence is a
    weaker claim than the button was making, so the two are now separate
    fields and the board says which one it has.

    `accepted_at=0.0` (not accepted, or an older log with no timestamp)
    means freshness is UNKNOWN, not false: nothing is marked stale on the
    strength of a missing timestamp.
    """
    backed, unbacked, stale, broad = [], [], [], []
    for a in _artifacts(text or ""):
        p = resolve(a, mission_cwd=cwd)
        if _too_broad(p, cwd):
            # NOT "unbacked": the path is really there, and saying it is not
            # on disk would be a false statement about the filesystem. It is
            # a claim that cannot be checked, which is its own answer.
            broad.append(a)
            continue
        if not p.exists():
            unbacked.append(a)
            continue
        backed.append(a)
        if accepted_at and not _touched_since(p, accepted_at):
            stale.append(a)
    exists = bool(backed) and not unbacked and not broad
    return {"backed": len(backed), "unbacked": unbacked,
            "stale": stale, "too_broad": broad,
            # Kept as its own field: the board still wants to say "the named
            # file exists" where that is all it knows.
            "exists": exists,
            "ok": exists and not stale}
