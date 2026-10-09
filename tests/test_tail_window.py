"""The scan window measures TURNS, not bytes — and doctor stops guessing why.

Measured 2026-10-08 on the real board. `doctor` reported two sessions as
"zero extractable claims — the extractor may be blind to its phrasing". It
was wrong about both, in opposite directions:

  * Session A: a 1,154 MB transcript. The 400 KB tail held 43 LINES and THREE
    assistant text blocks, because that session's records are megabytes each
    (large tool results). `scan` returned 0 in 0.00s — it never read a
    sentence. Widening by turns: 0.4 MB -> 3 blocks, 2 MB -> 18, 8 MB -> 24,
    32 MB -> 253. After the fix the same file extracts 4 claims, all backed.
  * Session B: 0.8 MB, 150 lines, THREE assistant messages in total. There was
    genuinely nothing to claim, so the finding was a false positive created by
    measuring the file's size.

Bytes on disk say nothing about how much an agent said.
"""
import json
from pathlib import Path

import pytest

from agent_mission import claims as C

ROOT = Path(__file__).resolve().parents[1]


def _rec(text: str, filler: int = 0) -> str:
    """One assistant record, optionally padded to look like a big tool result."""
    rec = {"type": "assistant", "uuid": f"u{abs(hash(text)) % 10**8}",
           "message": {"role": "assistant",
                       "content": [{"type": "text", "text": text}]}}
    if filler:
        rec["padding"] = "x" * filler
    return json.dumps(rec)


def _write(tmp_path, lines) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


# ── the window widens until it holds turns ───────────────────────────────

def test_a_claim_behind_huge_records_is_still_found(tmp_path):
    """The real shape: one claim, then megabytes of tool-result padding."""
    lines = [_rec("done: fixed the fold (agent_mission/store.py)")]
    lines += [_rec(f"padding turn {n}", filler=300_000) for n in range(6)]
    p = _write(tmp_path, lines)
    found = list(C.iter_claims(p, "sid", tail_bytes=400_000))
    assert found, "the claim sat outside a byte window and was never read"
    assert any("fixed the fold" in c.sentence for c in found)


def test_the_window_stops_at_the_ceiling(tmp_path, monkeypatch):
    """A pathological transcript must not make the board read a gigabyte."""
    monkeypatch.setattr(C, "MAX_TAIL_BYTES", 50_000)
    monkeypatch.setattr(C, "MIN_ASSISTANT_BLOCKS", 999)
    lines = [_rec(f"turn {n}", filler=20_000) for n in range(20)]
    p = _write(tmp_path, lines)
    got = C._read_lines(p, 10_000)
    assert len(got) < 20, "it read the whole file despite the ceiling"


def test_a_file_smaller_than_the_window_is_not_re_read(tmp_path):
    """The loop stops once the window covers the file, or it never ends."""
    p = _write(tmp_path, [_rec("hello")])
    assert len(C._read_lines(p, 400_000)) == 1


def test_an_empty_file_terminates(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("")
    assert C._read_lines(p, 400_000) == []


def test_tail_bytes_none_still_reads_everything(tmp_path):
    lines = [_rec(f"turn {n}") for n in range(5)]
    p = _write(tmp_path, lines)
    assert len(C._read_lines(p, None)) == 5


def test_the_count_sees_both_content_shapes(tmp_path):
    """Content is a list of blocks, or a bare string. Both are a turn."""
    plain = json.dumps({"type": "assistant",
                        "message": {"role": "assistant", "content": "hi"}})
    assert C._count_assistant_text([plain]) == 1
    assert C._count_assistant_text([_rec("hi")]) == 1
    assert C._count_assistant_text(["{not json"]) == 0


def test_a_tool_result_block_is_not_counted_as_a_turn(tmp_path):
    """Only text blocks can carry a claim; counting results would defeat it."""
    rec = json.dumps({"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": {}},
                    {"type": "tool_result", "content": "x" * 500}]}})
    assert C._count_assistant_text([rec]) == 0


# ── doctor reports what it measured ──────────────────────────────────────

def test_doctor_does_not_flag_a_session_that_barely_spoke(tmp_path,
                                                          monkeypatch):
    """0.8 MB of giant records with three messages is not extractor blindness."""
    import agent_mission.doctor as D
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "h"))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "Ship it", by="human", typed_by="human")
    M.attach("s1", "g")
    p = _write(tmp_path, [_rec("just three", filler=300_000),
                          _rec("messages here", filler=300_000),
                          _rec("and nothing claimed", filler=300_000)])
    monkeypatch.setattr(D, "transcript_for", lambda sid: p, raising=False)
    import agent_mission.session as S
    monkeypatch.setattr(S, "transcript_for", lambda sid: p)
    found = [f for f in D.review() if f["what"] == "no claims extracted"]
    assert not found, "flagged a session with 3 assistant messages"


def test_doctors_wording_no_longer_asserts_the_cause():
    src = (ROOT / "agent_mission" / "doctor.py").read_text()
    assert "may be blind to its" not in src
    assert "Either it claimed" in src, \
        "the finding must offer both causes, having measured neither"
    assert "assistant " in src and "scanned window" in src, \
        "it must report the number it measured, not the file size"
