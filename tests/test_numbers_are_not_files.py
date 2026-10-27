"""A number with a unit, or a version, is not a file.

queue told the person a career item "names 3.9s, so a fresh session can claim
it checkably" -- a timing quoted in the text, read as an artifact. The rule is
shared with the claim verifier, so it is fixed once, there.
"""
import pytest

from agent_mission.claims import looks_like_a_file
from agent_mission.sharpen import names_an_artifact


@pytest.mark.parametrize("tok", ["3.9s", "0.5s", "1.2x", "v0.2", "2.1.308",
                                 "98.5%", "51.0ms"])
def test_numbers_and_versions_are_not_files(tok):
    assert not looks_like_a_file(tok)


@pytest.mark.parametrize("tok", ["CLAUDE.md", "a.py", "1.py",
                                 "docs/v1.2/notes.md", "tests/"])
def test_real_files_still_are(tok):
    assert looks_like_a_file(tok)


def test_the_live_case():
    text = ("Review the board changes: click latency 3.9s to 51ms; "
            "817 tests. Sources: docs/VERIFIER-ACCURACY.md")
    assert names_an_artifact(text, strict=True) == "docs/VERIFIER-ACCURACY.md"
    assert names_an_artifact("latency fell from 3.9s to 51ms",
                             strict=True) == ""
