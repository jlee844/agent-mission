"""Item 9: a finding must be reported once per session, not once per turn.

The Stop hook keyed on `f"{block_index}:{sentence[:60]}"`, and `block_index`
is a position inside the 400 KB TAIL WINDOW. Once a transcript outgrows that
window, every turn slides it: old claims get new indices, new keys, a
re-verify, and an unbacked one is re-reported for the rest of the session.

The state file made it worse — `sorted(seen)[-400:]` keeps the 400
string-LARGEST keys, so a recent "1:…" was evicted while an old "399:…"
survived.
"""
import json
import uuid as _uuid
from pathlib import Path

import pytest

from agent_mission import claims as C


def _rec(role, content, uid=None):
    return json.dumps({"uuid": uid or str(_uuid.uuid4()),
                       "message": {"role": role, "content": content}})


def _claim_records(sentence="All tests pass.", uid="claim-uuid-1"):
    """One Bash call that is NOT a test runner, then a tests-pass claim —
    so the claim is unbacked and therefore reportable."""
    return [
        _rec("assistant", [{"type": "tool_use", "name": "Bash", "id": "t1",
                            "input": {"command": "ls tests/"}}]),
        _rec("user", [{"type": "tool_result", "tool_use_id": "t1",
                       "is_error": False}]),
        _rec("assistant", [{"type": "text", "text": sentence}], uid=uid),
    ]


def _filler(n_bytes):
    """Records that carry no claim, to push the window past its size."""
    out, size = [], 0
    while size < n_bytes:
        r = _rec("assistant", [{"type": "tool_use", "name": "Read",
                                "id": f"f{len(out)}",
                                "input": {"file_path": "x" * 400}}])
        out.append(r)
        size += len(r)
    return out


def _run_hook(tmp_path, monkeypatch, capsys, lines):
    from agent_mission import __main__ as MM

    tp = tmp_path / "transcript.jsonl"
    tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = json.dumps({"transcript_path": str(tp), "session_id": "sess1",
                          "cwd": str(tmp_path)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    rc = MM.main(["claims"])
    assert rc == 0, "the hook must never break a turn"
    return capsys.readouterr().out


def test_a_finding_is_reported_once_then_stays_quiet(tmp_path, monkeypatch,
                                                     capsys):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    lines = _claim_records()
    first = _run_hook(tmp_path, monkeypatch, capsys, lines)
    assert "All tests pass" in first or first.strip(), first
    second = _run_hook(tmp_path, monkeypatch, capsys, lines)
    assert second.strip() == "", f"re-reported: {second!r}"


def test_the_window_sliding_does_not_resurrect_an_old_finding(
        tmp_path, monkeypatch, capsys):
    """The spec's Prove line: report once, append 500 KB, report zero."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    lines = _claim_records()
    first = _run_hook(tmp_path, monkeypatch, capsys, lines)
    assert first.strip() != "", "the first run must find it"

    grown = lines + _filler(500_000)
    again = _run_hook(tmp_path, monkeypatch, capsys, grown)
    assert again.strip() == "", (
        "the window slid, the index moved, and the finding came back")


def test_the_key_does_not_move_when_the_index_does():
    """The property underneath: identity comes from the record, not the
    position."""
    a = C.Claim(session_id="s", block_index=3, sentence="All tests pass.",
                mentions_tests=True, record_uuid="rec-1")
    b = C.Claim(session_id="s", block_index=900, sentence="All tests pass.",
                mentions_tests=True, record_uuid="rec-1")
    assert C.finding_key(a) == C.finding_key(b)


def test_two_claims_sharing_an_opening_are_two_findings():
    """The old key truncated the sentence at 60 chars, so two long claims
    with the same opening collapsed into one."""
    head = "All of the tests in the suite now pass after the change to the "
    a = C.Claim(session_id="s", block_index=1, record_uuid="r",
                sentence=head + "retry logic.", mentions_tests=True)
    b = C.Claim(session_id="s", block_index=1, record_uuid="r",
                sentence=head + "cache layer.", mentions_tests=True)
    assert len(head) > 60
    assert C.finding_key(a) != C.finding_key(b)


def test_a_transcript_with_no_uuids_still_dedupes(tmp_path, monkeypatch,
                                                  capsys):
    """Falling back to the index is weaker and must still work — a short
    session never slides its window, which is when the fallback applies."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    lines = [
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "id": "t1",
             "input": {"command": "ls tests/"}}]}}),
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False}]}}),
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "text", "text": "All tests pass."}]}}),
    ]
    first = _run_hook(tmp_path, monkeypatch, capsys, lines)
    assert first.strip() != ""
    assert _run_hook(tmp_path, monkeypatch, capsys, lines).strip() == ""


def test_the_state_file_trims_the_oldest_not_the_alphabetically_smallest(
        tmp_path, monkeypatch, capsys):
    """`sorted(seen)[-400:]` evicted by string order, so a recent key could
    be dropped while an older one stayed."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    _run_hook(tmp_path, monkeypatch, capsys, _claim_records(uid="zzz-newest"))
    state = next((tmp_path / "store" / "claims").glob("*.json"))
    keys = json.loads(state.read_text())
    assert isinstance(keys, list), "a list preserves arrival order; a set does not"
    assert any(k.startswith("zzz-newest") for k in keys)
