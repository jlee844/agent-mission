"""The board's 4.5 GB RSS, with an 8.4 GB peak, on a 15-day-old process.

Two causes, both in `session.py`:

1. `memo_by_file` called `cache.clear()` on every miss, so it held ONE entry
   for the whole process rather than one per file. The board polls N
   transcripts in a loop, so each call evicted the previous one and the hit
   rate was zero — every poll re-parsed every byte it could see (1.58 GB
   every 4 seconds on the author's own board).

2. `activity()` did `read_text().splitlines()`: the whole file as one string
   AND a list of every line, about 2x the file size before any JSON was
   parsed. On a 1.09 GB transcript that is the multi-gigabyte spike.
"""
import json

import pytest

from agent_mission import session as S


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both caches are module-level and outlive a test."""
    S.activity.cache.clear()
    S._RESUME.clear()
    yield
    S.activity.cache.clear()
    S._RESUME.clear()


def _rec(**kw):
    return json.dumps(kw)


def _tool_call(tid, command="pytest -q", file_path=None):
    inp = {"command": command} if file_path is None else {"file_path": file_path}
    return _rec(message={"role": "assistant", "content": [
        {"type": "tool_use", "name": "Write" if file_path else "Bash",
         "id": tid, "input": inp}]})


def _error(tid):
    return _rec(message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": True}]})


def _ask(text):
    return _rec(message={"role": "user", "content": [
        {"type": "text", "text": text}]})


def _write(path, lines, newline_at_end=True):
    body = "\n".join(lines)
    path.write_text(body + ("\n" if newline_at_end else ""), encoding="utf-8")
    return path


# ── cause 1: the cache that cached nothing ───────────────────────────────

def test_polling_several_transcripts_keeps_them_all_cached(tmp_path):
    """Measured before the fix: 1 entry for 3 files across two full rounds."""
    paths = []
    for n in range(3):
        paths.append(_write(tmp_path / f"s{n}.jsonl", [_tool_call(f"t{n}")]))

    for _ in range(2):
        for p in paths:
            S.activity(p)
    assert len(S.activity.cache) == 3, \
        "one entry per file, not one entry per process"


def test_an_unchanged_transcript_is_not_reparsed(tmp_path, monkeypatch):
    p = _write(tmp_path / "s.jsonl", [_tool_call("t1")])
    first = S.activity(p)

    def boom(*a, **k):
        raise AssertionError("re-parsed a file whose fingerprint had not moved")

    monkeypatch.setattr(S, "_consume", boom)
    assert S.activity(p).calls == first.calls


def test_the_cache_is_bounded(tmp_path):
    """A board running for weeks must not hold an entry per session that ever
    existed — but the bound must be far above a realistic board."""
    for n in range(S.MEMO_MAX + 12):
        S.activity(_write(tmp_path / f"s{n}.jsonl", [_tool_call("t")]))
    assert len(S.activity.cache) <= S.MEMO_MAX


# ── cause 2: no whole-file read, ever ────────────────────────────────────

def test_activity_never_reads_the_whole_file_as_one_string(tmp_path,
                                                           monkeypatch):
    """The structural assertion. Peak RSS is not portable to assert; "this
    code path does not exist any more" is."""
    p = _write(tmp_path / "s.jsonl", [_tool_call("t1"), _error("t1")])

    def no_read_text(*a, **k):
        raise AssertionError("read_text() on a transcript")

    monkeypatch.setattr(type(p), "read_text", no_read_text)
    got = S.activity(p)
    assert got.calls == 1 and got.failures == 1


def test_no_dict_entry_is_kept_per_tool_call(tmp_path):
    """`pending[b["id"]] = True` was written for every tool call in the file
    and never read — hundreds of thousands of entries on a large transcript,
    held for the length of the parse, for nothing."""
    # Comments stripped first: the prose explaining why the dict is gone
    # names it, and a guard that matches its own rationale is the fourth
    # instance of that failure in this repo.
    body = "\n".join(ln for ln in open(S.__file__, encoding="utf-8")
                      if not ln.lstrip().startswith("#"))
    assert "pending" not in body, "the write-only dict is back"


# ── the parse must still be correct ──────────────────────────────────────

def test_incremental_equals_a_fresh_full_parse(tmp_path):
    """The property the resume rests on: a transcript is append-only, so
    folding the delta must equal folding the whole thing."""
    lines = [_tool_call("t1"), _error("t1"), _ask("please fix the retry logic"),
             _tool_call("t2", file_path="/repo/a.py")]
    p = _write(tmp_path / "s.jsonl", lines[:2])
    S.activity(p)                                  # partial parse

    _write(tmp_path / "s.jsonl", lines)            # grew
    resumed = S.activity(p)

    S.activity.cache.clear()
    S._RESUME.clear()
    fresh = S.activity(_write(tmp_path / "other.jsonl", lines))

    assert (resumed.calls, resumed.tests, resumed.failures) == \
           (fresh.calls, fresh.tests, fresh.failures)
    assert resumed.files == fresh.files
    assert resumed.last_asks == fresh.last_asks


def test_a_record_still_being_written_is_not_lost(tmp_path):
    """A final line with no newline is a record mid-write. Folding it would
    parse a truncated JSON object; skipping it AND advancing the offset past
    it would lose the record for good."""
    p = tmp_path / "s.jsonl"
    full = _tool_call("t1")
    p.write_text(full + "\n" + _tool_call("t2")[:40], encoding="utf-8")
    assert S.activity(p).calls == 1, "the half-written record was folded"

    p.write_text(full + "\n" + _tool_call("t2") + "\n", encoding="utf-8")
    assert S.activity(p).calls == 2, "the completed record was never folded"


def test_a_truncated_or_rotated_file_restarts(tmp_path):
    """Accumulated totals would otherwise describe bytes that are gone."""
    p = _write(tmp_path / "s.jsonl", [_tool_call(f"t{n}") for n in range(5)])
    assert S.activity(p).calls == 5
    _write(tmp_path / "s.jsonl", [_tool_call("new")])
    assert S.activity(p).calls == 1, "stale totals survived a rotation"


def test_last_asks_keeps_the_newest_three_across_increments(tmp_path):
    p = _write(tmp_path / "s.jsonl", [_ask("first question here please")])
    S.activity(p)
    _write(tmp_path / "s.jsonl",
           [_ask(f"question number {n} with enough length") for n in range(6)])
    got = S.activity(p)
    assert len(got.last_asks) == S.ASKS_KEPT
    assert "number 5" in got.last_asks[-1]


def test_the_returned_activity_is_a_snapshot(tmp_path):
    """A caller holding the result must not see it change on the next poll,
    and must not be able to mutate the accumulator."""
    p = _write(tmp_path / "s.jsonl", [_tool_call("t1")])
    first = S.activity(p)
    first.files["injected"] = 99
    _write(tmp_path / "s.jsonl", [_tool_call("t1"), _tool_call("t2")])
    second = S.activity(p)
    assert first.calls == 1 and second.calls == 2
    assert "injected" not in second.files


def test_a_missing_or_unreadable_transcript_is_zero_not_an_error(tmp_path):
    got = S.activity(tmp_path / "nope.jsonl")
    assert got.calls == 0 and got.files == {}


def test_a_torn_line_is_skipped_not_fatal(tmp_path):
    p = _write(tmp_path / "s.jsonl",
               [_tool_call("t1"), "{not json", _tool_call("t2")])
    assert S.activity(p).calls == 2
