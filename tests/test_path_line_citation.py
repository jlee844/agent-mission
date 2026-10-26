"""A `path:line` citation must resolve to the path, not vanish.

Measured 2026-10-24 while filing a batch of claims: `_PATHY`'s character
class has no colon, so `(interview-prep/lessons/transformers.yaml:517)` did
not match and was dropped -- leaving `backed=0` with an EMPTY `unbacked`
list, i.e. "nothing checkable" rather than "that file is missing". The most
precise citation form was the one that failed, and it failed SILENTLY: the
author is told to name a file in parentheses when they just did.
"""
import pathlib

from agent_mission import claims


def _cwd(tmp_path) -> str:
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "thing.yaml").write_text("x: 1\n")
    return str(tmp_path)


def test_a_line_number_still_resolves_to_the_file(tmp_path):
    v = claims.verdict_for("done: wrote it (pkg/thing.yaml:517)", _cwd(tmp_path))
    assert v["backed"] == 1, v
    assert v["ok"] is True, v


def test_a_line_range_still_resolves(tmp_path):
    v = claims.verdict_for("done: wrote it (pkg/thing.yaml:1114-1124)", _cwd(tmp_path))
    assert v["backed"] == 1, v


def test_a_missing_file_with_a_line_number_is_unbacked_not_silent(tmp_path):
    """The failure mode this fixes: absence must be REPORTED, never dropped."""
    v = claims.verdict_for("done: wrote it (pkg/gone.yaml:9)", _cwd(tmp_path))
    assert v["unbacked"] == ["pkg/gone.yaml"], v
    assert v["backed"] == 0, v


def test_a_bare_path_is_unchanged(tmp_path):
    v = claims.verdict_for("done: wrote it (pkg/thing.yaml)", _cwd(tmp_path))
    assert v["backed"] == 1, v


def test_prose_with_a_colon_is_still_not_a_path(tmp_path):
    """Stripping a numeric suffix must not turn ordinary prose into a path."""
    v = claims.verdict_for("done: shipped (ratio 3:2 and note:see below)",
                           _cwd(tmp_path))
    assert v["backed"] == 0, v
    assert v["unbacked"] == [], v
