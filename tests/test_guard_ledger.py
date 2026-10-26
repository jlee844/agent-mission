"""The guards fire constantly and leave no trace.

Every refusal, every unverifiable claim, every wrong write-code is the
evidence for why the authority split exists -- and none of it was recorded,
so "is this necessary?" could only be answered from memory. One session on
2026-10-24 tripped the privacy guard three times and the claim verifier four
times; the ledger is what makes that countable instead of anecdotal.
"""
import json

from agent_mission import guards


def test_a_recorded_activation_is_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    guards.record("refused_protected_field", "only you can accept a proposal")
    guards.record("refused_protected_field", "only you can tick an item done")
    guards.record("claim_unverified", "nothing checkable")
    t = guards.tally()
    assert t["refused_protected_field"] == 2, t
    assert t["claim_unverified"] == 1, t


def test_an_empty_ledger_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    assert guards.tally() == {}
    assert guards.recent() == []


def test_recording_never_raises_even_when_the_home_is_unwritable(
        tmp_path, monkeypatch):
    """A guard firing is already a failure path. If the ledger itself throws,
    it converts a clean refusal into a crash -- so it must swallow everything.
    """
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "x"))
    (tmp_path / "x").write_text("i am a file, not a directory")
    guards.record("refused_protected_field", "whatever")   # must not raise


def test_recent_is_newest_first_and_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    for i in range(12):
        guards.record("claim_unverified", f"detail {i}")
    got = guards.recent(5)
    assert len(got) == 5
    assert got[0]["detail"] == "detail 11", got[0]
    assert got[-1]["detail"] == "detail 7", got[-1]


def test_the_ledger_is_append_only_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    guards.record("wrong_write_code", "attempt 1")
    guards.record("wrong_write_code", "attempt 2")
    lines = (tmp_path / "guards.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == "wrong_write_code"
    assert "at" in json.loads(lines[0])


def test_a_corrupt_line_does_not_break_the_tally(tmp_path, monkeypatch):
    """The log is a plain file; a half-written line must not blank the count."""
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    guards.record("claim_unverified", "good")
    with open(tmp_path / "guards.jsonl", "a") as fh:
        fh.write("{not json\n")
    guards.record("claim_unverified", "also good")
    assert guards.tally() == {"claim_unverified": 2}


def test_the_cli_surfaces_the_tally(tmp_path, monkeypatch, capsys):
    """Counting is half the item; the other half is that a human can see it."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    guards.record("refused_protected_field", "only you can accept a proposal")
    guards.record("claim_unverified", "not on disk: README.md")
    assert main(["guards"]) == 0
    out = capsys.readouterr().out
    assert "2" in out
    assert "protected" in out.lower() and "claim" in out.lower()
    assert "README.md" in out, "the detail is the evidence, not just the count"


def test_the_cli_says_nothing_has_fired_rather_than_printing_nothing(
        tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    assert main(["guards"]) == 0
    out = capsys.readouterr().out
    assert out.strip(), "an empty screen reads as a broken command"


def test_a_protected_write_is_recorded_end_to_end(tmp_path, monkeypatch):
    """Not a unit test of record(): the refusal path itself must log."""
    from agent_mission.store import ProtectedFieldError
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    try:
        raise ProtectedFieldError("only you can tick an item done")
    except ProtectedFieldError:
        pass
    assert guards.tally().get("refused_protected_field") == 1, guards.tally()
