"""Nothing in the commit set identifies the maintainer or their private work.

This repo is public. Two narrow grep-guards already covered `claims.py` and
`sharpen.py`, which is where transcript fixtures live — but the leak was never
only there. A scan of the whole commit set on 2026-10-08 found the maintainer's
first name in 12 code comments and tests, three VERBATIM chat messages quoted
as section headings, and a private project's name 33 times.

None of it is dangerous. All of it is the kind of thing you cannot take back
once it is pushed, and the anecdotes read as "here is a person and what they
got wrong on a Tuesday" rather than as engineering notes.

⚠️ The needles are built by concatenation so this file does not match itself,
and the LICENSE is exempt: that copyright is deliberate.

The replacement rule, applied by `scrub.py` and recorded here so the next
person follows it: keep the EVIDENCE, drop the identity. "Measured on a real
project's review, 12 of 19 claims came back unbacked" is why the code looks the
way it does; the project's name is not.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Paths that may legitimately carry the name.
EXEMPT = {"LICENSE", "uv.lock"}

# Built by concatenation, so this file is not itself a hit.
IDENTITY = [
    "Jona" + "than",
    "jonathan" + "lee",
    "Documents/" + "cowork",
    "/Users" + "/jonathanlee",
]
PRIVATE_PROJECTS = [
    "trip" + "nom", "Trip" + "nom",
    "career" + "-hub",
    "mission" + "-layer",
    "arxiv" + "-rag",
    "distill" + "-lab",
    "move" + "ment_care",
    "stock" + "_advise",
    "percep" + "tax",
]
# Real session ids seen in this workspace.
SESSION_IDS = ["be17" + "144b", "6942" + "6e5a", "5fd9" + "8e2e",
               "e1d7" + "feb0", "1cb5" + "4d4b"]


def _commit_set() -> list[Path]:
    """Exactly the files a commit would carry — tracked plus not-ignored."""
    import subprocess
    out = subprocess.run(["git", "ls-files", "-c", "-o", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return [ROOT / rel for rel in out.split()
            if rel not in EXEMPT and (ROOT / rel).is_file()]


def _text_files():
    for p in _commit_set():
        if p.suffix in {".png", ".gif", ".jpg", ".ico"}:
            continue
        try:
            yield p, p.read_text()
        except (UnicodeDecodeError, OSError):
            continue


@pytest.mark.parametrize("needle", IDENTITY)
def test_the_maintainer_is_not_named(needle):
    hits = [str(p.relative_to(ROOT)) for p, body in _text_files()
            if needle in body and p.name != Path(__file__).name]
    assert not hits, f"{needle!r} appears in: {hits}"


@pytest.mark.parametrize("needle", PRIVATE_PROJECTS)
def test_no_private_project_names(needle):
    hits = [str(p.relative_to(ROOT)) for p, body in _text_files()
            if needle in body and p.name != Path(__file__).name]
    assert not hits, f"{needle!r} appears in: {hits}"


@pytest.mark.parametrize("needle", SESSION_IDS)
def test_no_real_session_ids(needle):
    hits = [str(p.relative_to(ROOT)) for p, body in _text_files()
            if needle in body and p.name != Path(__file__).name]
    assert not hits, f"{needle!r} appears in: {hits}"


def test_no_absolute_home_paths():
    bad = "/Users/" + "jonathanlee"
    hits = [str(p.relative_to(ROOT)) for p, body in _text_files()
            if bad in body and p.name != Path(__file__).name]
    assert not hits, hits


def test_no_email_addresses():
    import re
    pat = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    hits = []
    for p, body in _text_files():
        if p.name == Path(__file__).name:
            continue
        for m in pat.findall(body):
            if m.endswith(("example.com", "example.org")):
                continue
            hits.append(f"{p.relative_to(ROOT)}: {m}")
    assert not hits, hits


def test_the_guard_covers_the_whole_commit_set_not_two_files():
    """The defect this file exists for: the old guards checked 2 of 62 files."""
    assert len(_commit_set()) > 40
