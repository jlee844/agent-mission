# Every unusual claim, and where to inspect it

Claims about this project appear in READMEs, resumes, and conversations. This
file maps each one to the artifact that substantiates it — the experiment, the
test, or the code — so no claim has to be taken on anyone's word, including the
author's.

| claim | inspect |
|---|---|
| Three-tier authority: agent proposes, disk corroborates, human confirms | the tier table in [README](../README.md); enforcement layers in [SECURITY.md](SECURITY.md#three-layers); deny rules written by `setup` in `agent_mission/setup_surfaces.py` |
| An adversarial test rewrote a protected objective; the fix was structural | [adversarial-testing.md](adversarial-testing.md) — round 1; `typed_by` in [SECURITY.md](SECURITY.md#what-typed_by-is-for) |
| An agent given an impossible goal did not route around refusal | [adversarial-testing.md](adversarial-testing.md) — round 2 |
| Completion claims are checked against the filesystem in the turn they are made | `agent_mission/claims.py`; the acceptance test `test_acceptance_one_true_claim_one_lie_one_grey_line` in `tests/test_claims.py` |
| Suggested ticks sweep only what the disk corroborates, server-side | the `sweep` branch in `agent_mission/actions.py` — the client sends no ids; tests in `tests/test_claims_done.py` |
| Usage data (61 of 152 proposals stranded) drove the one-click redesign | [DESIGN.md](DESIGN.md) — the friction measurements; the write-code flow in [SECURITY.md](SECURITY.md#the-boards-write-code) |
| Drift detection was tried five ways and rejected | [DESIGN.md](DESIGN.md) — the five dead detectors, each with its pre-registered stopping rule |
| The cwd router misrouted a real write and was deleted | [DESIGN.md](DESIGN.md) — the post-mortem; the guard tests in `tests/test_troubleshoot.py` |
| Test and demo stores cannot leak into the real board or resolver | `tests/test_missions.py`; the identity check in `agent_mission/daemon.py` |
| No real transcript content ships in test fixtures | the leak grep-guard in `tests/test_claims.py` — which caught its own source file on first run |
| The test count and Python matrix | the CI badge is live — [tests.yml](../.github/workflows/tests.yml) runs the suite on 3.9–3.14 per push; no count is hardcoded in prose (a test enforces this) |

If a claim you encountered is not in this table, treat that as a bug in this
file and check the git log — every design change here lands with its reasoning
in the commit message.
