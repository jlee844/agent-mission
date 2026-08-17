"""Session health: facts about how the session ran. Not drift detection."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.health import EXACT, Health, collisions, inspect   # noqa: E402


def _t(tmp_path, rows):
    p = tmp_path / "s.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def _reply(text, model="claude-opus-5"):
    return {"message": {"role": "assistant", "model": model,
                        "content": [{"type": "text", "text": text}]}}


LONG = ("This is a substantial reply about retrieval evaluation and the way "
        "hybrid scoring combines sparse and dense signals across a corpus of "
        "documents, with enough distinct vocabulary to be measurable. ") * 3


def test_a_model_swap_mid_session_is_recorded(tmp_path):
    """Behaviour changing after an update is not imagination — it was a
    different model, and the transcript says so."""
    p = _t(tmp_path, [_reply(LONG, "claude-sonnet-4-6"),
                      _reply(LONG + "x", "claude-opus-4-8"),
                      _reply(LONG + "y", "claude-opus-5")])
    h = inspect(p)
    assert h.model_changed
    assert h.model_order == ["claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-5"]


def test_one_model_throughout_is_not_flagged(tmp_path):
    h = inspect(_t(tmp_path, [_reply(LONG), _reply(LONG + "z")]))
    assert not h.model_changed


def test_synthetic_model_entries_are_ignored(tmp_path):
    p = _t(tmp_path, [_reply(LONG), _reply(LONG + "q", "<synthetic>")])
    assert inspect(_t(tmp_path, [_reply(LONG), _reply(LONG + "q", "<synthetic>")])).model_order == ["claude-opus-5"]


def test_a_verbatim_repeat_is_caught(tmp_path):
    """The reported failure: the agent answers a turn it already answered.
    Found in a real session at cosine 1.000, thirteen replies apart."""
    rows = [_reply(LONG + f" tail sentinel {'alpha beta gamma delta'.split()[i % 4]} "
                   f"{'one two three four five'.split()[i % 5]} index {i}")
            for i in range(25)]
    rows.append(_reply(LONG + " tail sentinel delta four index 3"))   # repeat of #3
    h = inspect(_t(tmp_path, rows))
    assert h.exact_repeats >= 1
    top = h.repeats[0]
    assert top["sim"] >= EXACT and top["gap"] > 1


def test_replies_differing_only_in_numbers_are_not_verbatim_repeats(tmp_path):
    """"40/62" versus "41/62" is a different answer. A word-only tokenizer
    scores them 1.000 and calls it a repeat."""
    rows = [_reply(LONG + f" the score is {40 + i} out of 62 today") for i in range(24)]
    h = inspect(_t(tmp_path, rows))
    assert h.exact_repeats == 0


def test_short_replies_do_not_count_as_repeats(tmp_path):
    """"Done." repeats innocently and would drown the signal."""
    rows = [_reply("Done.") for _ in range(30)]
    assert inspect(_t(tmp_path, rows)).repeats == []


def test_a_short_session_is_not_scored(tmp_path):
    """Under twenty replies there is no distribution to be unusual against."""
    h = inspect(_t(tmp_path, [_reply(LONG), _reply(LONG)]))
    assert h.repeats == [] and h.n_responses == 2


def test_an_unreadable_transcript_returns_empty_not_an_error(tmp_path):
    assert inspect(tmp_path / "missing.jsonl") == Health()


# ── collisions ───────────────────────────────────────────────────────────────

def test_a_file_two_sessions_write_is_a_collision():
    """Nothing inside either session can see the other."""
    c = collisions({"a": {"NEXT.md": 3, "x.py": 1}, "b": {"NEXT.md": 2}})
    assert [x["file"] for x in c] == ["NEXT.md"]
    assert set(c[0]["sessions"]) == {"a", "b"}


def test_one_session_writing_alone_is_not_a_collision():
    assert collisions({"a": {"x.py": 9}}) == []


def test_collisions_are_ranked_by_how_many_sessions_share_the_file():
    c = collisions({"a": {"n": 1, "r": 1}, "b": {"n": 1, "r": 1}, "c": {"n": 1}})
    assert c[0]["file"] == "n" and len(c[0]["sessions"]) == 3
