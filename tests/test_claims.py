"""C16: the claim verifier -- ported, not pasted, and adaptive by design.

Every fixture here is synthetic. Porting probe-lab's tests verbatim could
ship fragments of real private transcripts into a public repo, so transcripts
are built by _transcript() below and a guard test proves nothing real leaked.
"""
import json
import sys
from pathlib import Path

import os

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


# ── item 8: "a test ran" must mean a RUNNER ran ───────────────────────────

def _claims_for(tmp_path, commands, sentence="All tests pass.", errors=()):
    """A transcript where each command runs, then the claim is made."""
    from agent_mission import claims as C

    blocks, ids = [], []
    for n, cmd in enumerate(commands):
        tid = f"t{n}"
        ids.append(tid)
        blocks.append(("assistant", [_tool_use("Bash", tid, command=cmd)]))
        blocks.append(("user", [_result(tid, error=(n in errors))]))
    blocks.append(("assistant", [_text(sentence)]))
    tp = _transcript(tmp_path / "s.jsonl", blocks)
    out = [C.verify(c) for c in C.iter_claims(tp, "s")]
    return out


@pytest.mark.parametrize("command", [
    "ls tests/",
    "cat tests/test_x.py",
    "grep -n foo test_api.py",
    "git add tests/",
])
def test_reading_a_test_file_is_not_running_the_suite(tmp_path, command):
    """All four counted as "a test ran", so "all tests pass" after `ls tests/`
    came back BACKED. Reading a test file is the opposite of running it."""
    got = _claims_for(tmp_path, [command])
    assert got, "the claim should have been extracted"
    assert got[-1].status == "unverified_tests", \
        f"{command!r} was accepted as evidence a suite passed"


@pytest.mark.parametrize("command", [
    "python -m pytest -q",
    "cd /repo && pytest tests/",
    "uv run pytest -q",
    ".venv/bin/pytest",
    "npm test",
    "npm run test:unit",
    "go test ./...",
    "cargo test",
    "make test",
    "env CI=1 pytest -x",
    "pytest -q | tail -3",
])
def test_a_real_runner_still_counts(tmp_path, command):
    """The narrowing must not break the claims it exists to allow."""
    got = _claims_for(tmp_path, [command])
    assert got[-1].status not in ("unverified_tests", "contradicted"), \
        f"{command!r} is a real test run and was rejected"


def test_a_pass_then_a_fail_is_contradicted_not_backed(tmp_path):
    """`all(not s.ok for s in ran)` meant you only had to have succeeded
    once, ever, in the window — so a passing run followed by a failing one
    came back backed. The LAST run is the current state of the suite."""
    got = _claims_for(tmp_path, ["pytest -q", "pytest -q"], errors={1})
    assert got[-1].status == "contradicted", got[-1].detail


def test_a_fail_then_a_pass_is_not_contradicted(tmp_path):
    """The other direction: fixing a failure and re-running is the normal
    loop, and must not be reported as a contradiction."""
    got = _claims_for(tmp_path, ["pytest -q", "pytest -q"], errors={0})
    assert got[-1].status != "contradicted", got[-1].detail


def test_a_single_failing_run_is_still_contradicted(tmp_path):
    got = _claims_for(tmp_path, ["pytest -q"], errors={0})
    assert got[-1].status == "contradicted"


def test_a_test_path_beside_a_real_runner_is_fine(tmp_path):
    """Dropping the path-only alternatives must not reject a runner that
    happens to name a path."""
    got = _claims_for(tmp_path, ["pytest tests/test_api.py::test_one"])
    assert got[-1].status not in ("unverified_tests", "contradicted")


def test_space_separated_artifacts_are_all_extracted():
    """Taking only the first silently under-checked a claim.

    Seen live on 2026-10-08: a peer claimed four paths separated by SPACES
    and `mission verify` reported "1 artifact(s) present", having looked at
    the README alone. A verdict carried by one file out of four is reported
    more confidently than it was earned.
    """
    from agent_mission.claims import _artifacts
    got = _artifacts("done: cleaned up (a/README.md b/EVAL.md c/eval.py "
                     "d/out.json)")
    assert got == ["a/README.md", "b/EVAL.md", "c/eval.py", "d/out.json"]


def test_trailing_prose_after_a_path_is_still_skipped():
    """The behaviour the first-only rule existed for must survive."""
    from agent_mission.claims import _artifacts
    assert _artifacts("done: gap gone (career_index.json gaps)") == \
        ["career_index.json"]


def test_commas_and_spaces_mix():
    from agent_mission.claims import _artifacts
    assert _artifacts("done: x (a/one.py, b/two.py c/three.py and d/four.py)") \
        == ["a/one.py", "b/two.py", "c/three.py", "d/four.py"]


# ── the directory-artifact hole (bd9bbcb9) ───────────────────────────────
#
# Demonstrated by a second reader on 2026-10-08: `done: rewrote the auth flow
# (./)` resolves to the mission's own folder, `_touched_since` walks it, an
# unrelated file written after acceptance makes it fresh, and "confirm all
# backed" then ticks the item `by=human`. Naming everything is not naming an
# artifact.

def _store_at(tmp_path):
    (tmp_path / "README.md").write_text("real")
    return str(tmp_path)


def test_the_mission_root_cannot_back_a_claim(tmp_path):
    from agent_mission.claims import verdict_for
    cwd = _store_at(tmp_path)
    (tmp_path / "unrelated.log").write_text("noise")   # after acceptance
    v = verdict_for("done: rewrote the auth flow (./)", cwd=cwd,
                    accepted_at=1.0)
    assert v["ok"] is False
    assert v["exists"] is False
    assert v["too_broad"] == ["./"]
    assert v["unbacked"] == [], "it IS on disk; saying otherwise is false"


def test_the_filesystem_root_cannot_back_a_claim(tmp_path):
    from agent_mission.claims import verdict_for
    v = verdict_for("done: everything (/)", cwd=_store_at(tmp_path),
                    accepted_at=1.0)
    assert v["ok"] is False and v["too_broad"] == ["/"]


def test_an_ancestor_of_the_mission_root_cannot_back_a_claim(tmp_path):
    from agent_mission.claims import verdict_for
    inner = tmp_path / "goal"
    inner.mkdir()
    (inner / "README.md").write_text("x")
    v = verdict_for("done: work (../)", cwd=str(inner), accepted_at=1.0)
    assert v["ok"] is False and v["too_broad"] == ["../"]


def test_a_named_subdirectory_still_counts(tmp_path):
    """Deliberately narrow: `tests/` is a real answer to 'what changed'."""
    import time
    from agent_mission.claims import verdict_for
    cwd = _store_at(tmp_path)
    sub = tmp_path / "tests"
    sub.mkdir()
    (sub / "test_x.py").write_text("x")
    v = verdict_for("done: added coverage (tests/)", cwd=cwd,
                    accepted_at=time.time() - 60)
    assert v["ok"] is True and v["too_broad"] == []


def test_a_real_file_is_unaffected(tmp_path):
    import time
    from agent_mission.claims import verdict_for
    v = verdict_for("done: real work (README.md)", cwd=_store_at(tmp_path),
                    accepted_at=time.time() - 60)
    assert v["ok"] is True and v["backed"] == 1


def test_the_backed_only_sweep_cannot_tick_a_too_broad_claim(tmp_path,
                                                             monkeypatch):
    """The sweep reads `ok`, so closing the verdict closes the button.

    `sweep` is the backed-only button. `sweepall` DELIBERATELY ticks what the
    disk cannot corroborate and writes a note saying it did — that is the
    human's judgement arriving without evidence, which is a different claim
    and not this hole. The first version of this test used sweepall and was
    asserting the wrong guarantee.
    """
    from agent_mission import actions, missions as M
    from agent_mission.store import MissionStore
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "Ship it", by="human", typed_by="human")
    i = st.propose("do the thing", by="agent")["item_id"]
    st.accept(i, by="human")
    (tmp_path / "unrelated.log").write_text("noise")
    st.claim_done(i, "done: did it (./)", by="agent")
    sess = actions.Session(enabled=True)
    out = actions.apply(sess, sess.code, "sweep", "g", ids=[])
    assert i not in out.get("ids", []), "a claim naming everything was swept"
    assert out["skipped"] == 1
    assert not next(x for x in st.load().items if x.id == i).done


def test_a_cache_write_does_not_make_a_directory_fresh(tmp_path):
    """`tests/` went sweepable the moment pytest wrote __pycache__.

    Found by a second reader on 2026-10-08. The directory rule was already
    the generous half of `too_broad`; letting build droppings satisfy it made
    "changed since you agreed" mean "someone ran the suite".
    """
    import time
    from agent_mission.claims import verdict_for
    cwd = tmp_path
    (cwd / "README.md").write_text("x")
    sub = cwd / "tests"
    (sub / "__pycache__").mkdir(parents=True)
    old = time.time() - 600
    (sub / "test_x.py").write_text("x")
    os.utime(sub / "test_x.py", (old, old))
    accepted = time.time() - 300
    (sub / "__pycache__" / "test_x.pyc").write_text("compiled")   # after
    v = verdict_for("done: added coverage (tests/)", cwd=str(cwd),
                    accepted_at=accepted)
    assert v["stale"] == ["tests/"], "a .pyc counted as the work"

    (sub / "test_new.py").write_text("real work")                 # after
    v2 = verdict_for("done: added coverage (tests/)", cwd=str(cwd),
                     accepted_at=accepted)
    assert v2["ok"] is True, "a real new file must still count"


def test_dot_directories_are_not_evidence(tmp_path):
    import time
    from agent_mission.claims import verdict_for
    (tmp_path / "README.md").write_text("x")
    pkg = tmp_path / "pkg"
    (pkg / ".git").mkdir(parents=True)
    old = time.time() - 600
    (pkg / "mod.py").write_text("x")
    os.utime(pkg / "mod.py", (old, old))
    accepted = time.time() - 300
    (pkg / ".git" / "HEAD").write_text("ref: refs/heads/main")
    v = verdict_for("done: changed it (pkg/)", cwd=str(tmp_path),
                    accepted_at=accepted)
    assert v["stale"] == ["pkg/"]
