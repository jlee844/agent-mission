"""C16: the claim verifier -- ported, not pasted, and adaptive by design.

Every fixture here is synthetic. Porting mission-layer's tests verbatim could
ship fragments of real private transcripts into a public repo, so transcripts
are built by _transcript() below and a guard test proves nothing real leaked.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _transcript(path: Path, blocks):
    """A minimal Claude-Code-shaped transcript from synthetic parts."""
    lines = []
    for role, content in blocks:
        lines.append(json.dumps({"message": {"role": role, "content": content}}))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _tool_use(name, tid, **inp):
    return {"type": "tool_use", "name": name, "id": tid, "input": inp}


def _result(tid, error=False):
    return {"type": "tool_result", "tool_use_id": tid, "is_error": error}


def _text(t):
    return {"type": "text", "text": t}


def test_the_ported_module_has_no_authors_directory_convention():
    """`parent.name == "builds"` made root detection work on exactly one
    machine in the world. The .git walk is the only rule left."""
    src = (ROOT / "agent_mission" / "claims.py").read_text()
    assert '"builds"' not in src and "'builds'" not in src


def test_fixtures_contain_no_real_transcript_content():
    """The grep-guard the spec asks for: no author paths, no real session ids,
    in this file or in the ported module."""
    # Built by concatenation so this list does not match itself.
    leaks = ["jonathan" + "lee", "be17" + "144b", "6942" + "6e5a",
             "5fd9" + "8e2e", "Documents/" + "cowork"]
    for f in ("tests/test_claims.py", "agent_mission/claims.py"):
        body = (ROOT / f).read_text()
        for leak in leaks:
            assert leak not in body, f"{f} carries real-corpus content: {leak}"


def test_hook_exits_zero_on_every_broken_input(tmp_path, monkeypatch, capsys):
    """A verifier that can break a turn is worse than no verifier."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))

    cases = [
        "",                                              # empty stdin
        "not json at all",                               # corrupt payload
        json.dumps({}),                                  # no transcript
        json.dumps({"transcript_path": str(tmp_path / "gone.jsonl")}),
        json.dumps({"transcript_path": str(
            _transcript(tmp_path / "foreign.jsonl",
                        [("assistant", "some totally foreign format")])),
            "session_id": "syn-1"}),
    ]
    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_bytes(b"\xff\xfe not even utf8 {]")
    cases.append(json.dumps({"transcript_path": str(corrupt),
                             "session_id": "syn-2"}))
    for payload in cases:
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
        assert main(["claims"]) == 0, f"non-zero exit on: {payload[:60]}"
    capsys.readouterr()


def test_a_legible_claim_round_trips(tmp_path, monkeypatch):
    """The contract format: `done: <what> (<artifact>)`. Extracted with no
    dependence on phrasing, verified against a temp file -- the whole reason
    the format exists."""
    from agent_mission.claims import iter_claims, verify
    real = tmp_path / "shipped.py"
    real.write_text("x = 1\n")
    tp = _transcript(tmp_path / "t.jsonl", [
        ("assistant", [_text(f"done: the widget renders ({real})")]),
        ("assistant", [_text(f"done: imaginary work ({tmp_path}/never.py)")]),
    ])
    claims = list(iter_claims(tp, "syn-3"))
    assert len(claims) == 2
    verified = [verify(c) for c in claims]
    assert verified[0].status == "backed"
    assert verified[1].status == "unbacked"
    assert "never.py" in verified[1].detail


def test_a_relative_artifact_resolves_against_the_sessions_cwd(tmp_path):
    from agent_mission.claims import iter_claims, verify
    (tmp_path / "lib.py").write_text("ok\n")
    tp = _transcript(tmp_path / "t.jsonl", [
        ("assistant", [_text("done: helper extracted (lib.py)")])])
    c = list(iter_claims(tp, "syn-4"))[0]
    assert verify(c, cwd=str(tmp_path)).status == "backed"


def test_prose_claims_still_verify_against_tool_calls(tmp_path):
    """The fallback path: a write errored, was never repeated, and the file
    on disk does not contain the attempted change -- yet the wrap-up says
    everything is done. (No test words in the claim: pytest's own tmp dir
    contains `test_`, which the test-command matcher rightly matches.)"""
    from agent_mission.claims import iter_claims, verify
    stale = tmp_path / "a.py"
    stale.write_text("old body, unchanged\n")
    tp = _transcript(tmp_path / "t.jsonl", [
        ("assistant", [_tool_use("Write", "t1",
                                 file_path=str(stale),
                                 content="the attempted change")]),
        ("user", [_result("t1", error=True)]),
        ("assistant", [_text("The refactor is complete and everything is done.")]),
    ])
    v = [verify(c) for c in iter_claims(tp, "syn-5")]
    assert v and v[0].status == "unbacked"
    assert "does NOT contain" in v[0].detail


def test_the_patterns_file_extends_extraction(tmp_path, monkeypatch):
    """C16b-5: human-gated extension. A phrasing the shipped templates miss
    is caught after one line in the local patterns file -- and a garbage
    line is skipped, never fatal."""
    from agent_mission import claims as C
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    tp = _transcript(tmp_path / "t.jsonl", [
        ("assistant", [_text("the widget saga concluded triumphantly")])])
    assert list(C.iter_claims(tp, "syn-6")) == [], "not a shipped template"

    (tmp_path / "claim-patterns.txt").write_text(
        "concluded triumphantly\n[broken(regex\n", encoding="utf-8")
    got = list(C.iter_claims(tp, "syn-6"))
    assert len(got) == 1, "one patterns line, and the phrasing is a claim now"


def test_doctor_reports_extractor_blindness(tmp_path, monkeypatch):
    """A verifier that extracts nothing looks identical to one that found
    nothing wrong. doctor says which it is, on the user's own corpus."""
    from agent_mission import doctor, missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("g", "/repo", "the goal", by="human", typed_by="human")
    M.attach("syn-blind", "g", by="human")

    big = _transcript(tmp_path / "blind.jsonl",
                      [("assistant", [_text("wrap-up phrased unusually " * 40)])
                       for _ in range(200)])          # > the 100 KB size gate
    import agent_mission.session as S
    monkeypatch.setattr(S, "transcript_for", lambda sid: big)

    hits = [f for f in doctor.findings()
            if f["what"] == "no claims extracted"]
    assert hits and "claim-patterns.txt" in hits[0]["detail"]


def test_acceptance_one_true_claim_one_lie_one_grey_line(tmp_path, monkeypatch):
    """The C16 acceptance test, verbatim from the hand-off: a clean fake
    home, a synthetic transcript carrying one true claim and one lie, and
    the board data showing exactly one grey line."""
    from agent_mission import missions as M
    from agent_mission.store import MissionStore
    import agent_mission.board as B
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))

    st = MissionStore(M.missions_root() / "demo")
    st.create("demo", str(tmp_path), "the goal", by="human", typed_by="human")
    M.attach("syn-live", "demo", by="human")

    true_file = tmp_path / "real.py"
    true_file.write_text("shipped\n")
    filler = [("user", [_text("padding " * 60)]) for _ in range(20)]
    tp = _transcript(tmp_path / "live.jsonl", filler + [
        ("assistant", [_text(f"done: the real half shipped ({true_file})")]),
        ("assistant", [_text(f"done: the invented half shipped "
                             f"({tmp_path}/fiction.py)")]),
    ])                                     # padded past the 2 KB liveness gate

    # One live process whose project dir holds this transcript.
    monkeypatch.setattr(B, "live",
                        lambda: [{"cwd": str(tmp_path), "procs": 1}])
    projects = tmp_path / "proj"
    monkeypatch.setattr(B, "PROJECTS", projects)
    d = projects / B._slug(str(tmp_path))
    d.mkdir(parents=True)
    (d / "syn-live.jsonl").write_text(tp.read_text(), encoding="utf-8")
    monkeypatch.setattr(B, "transcript_for", lambda sid: tp)
    monkeypatch.setattr(B, "activity", lambda p: None)

    demo = next(r for r in B.mission_rows() if r["id"] == "demo")
    assert demo["claims_checked"] == 2, "both claims were checked"
    assert len(demo["claims_bad"]) == 1, "exactly one grey line"
    assert "fiction.py" in demo["claims_bad"][0]["detail"]
