"""C18: sharpening CRITERIA, and the guarantee that it never grades WORK.

The gap being closed is measured, not hypothetical: in the 2026-08-27 Wayfinder
review, 12 of 19 claims-done rows came back "nothing checkable in the claim".
Every one described real work that a named file would have made checkable.

The frozen 12 are checked in below as a FIXTURE WITH SYNTHETIC PATHS. The real
texts stay in the append-only event log where they were written; copying real
repo paths into a public test is the leak C16's grep-guard exists to catch, and
that guard is re-asserted here.
"""
import re
from pathlib import Path

import pytest

from agent_mission import missions as M
from agent_mission.store import MissionStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "home"))
    s = MissionStore(M.missions_root() / "g")
    s.create("g", str(tmp_path), "ship the thing", by="human", typed_by="human")
    s.set_protected("success_criteria",
          ["every public repo passes a fresh-eyes stranger review",
           "the parser is covered by tests/test_parse.py"],
          by="human", typed_by="human")
    return s


def _accepted(s, text):
    ev = s.propose(text, by="agent")
    s.accept(ev["item_id"], by="human")
    return ev["item_id"]


# ---- what counts as sharpenable -------------------------------------------

def test_a_criterion_naming_a_file_needs_no_template(st):
    """It can already be claimed checkably, so proposing a template for it is
    noise -- and the inbox is the scarcest surface this tool has."""
    from agent_mission.sharpen import sharpenings
    found = sharpenings(st.load())
    refs = {(s.kind, s.text) for s in found}
    assert ("done-when", "every public repo passes a fresh-eyes stranger review") \
        in refs, "an ambiguous criterion is the whole point"
    assert not any("tests/test_parse.py" in s.text for s in found), \
        "a criterion that already names a file is left alone"


def test_a_branch_is_never_sharpened(st):
    """A branch is finished when its leaves are, so nobody ever claims one.
    Asking for a file to cite for the heading 'Backend' is advice that cannot
    be taken."""
    from agent_mission.sharpen import sharpenings
    parent = _accepted(st, "Backend")
    child = st.propose("wire the endpoint", by="agent", parent=parent)
    st.accept(child["item_id"], by="human")
    texts = {s.text for s in sharpenings(st.load())}
    assert "wire the endpoint" in texts
    assert "Backend" not in texts


def test_finished_and_unaccepted_work_is_out_of_scope(st):
    from agent_mission.sharpen import sharpenings
    done_id = _accepted(st, "already shipped")
    st.complete(done_id, by="human")
    st.propose("not accepted yet", by="agent")
    texts = {s.text for s in sharpenings(st.load())}
    assert "already shipped" not in texts, "finished phrasing is history"
    assert "not accepted yet" not in texts, "a proposal is not the plan yet"


def test_a_test_bearing_criterion_asks_for_the_test_id(st):
    from agent_mission.sharpen import template_for
    assert "::test_" in template_for("the suite covers the importer")
    assert "::test_" not in template_for("the login screen is translated")


# ---- the authority guarantees ---------------------------------------------

def test_sharpen_emits_proposals_and_nothing_else(st, monkeypatch, capsys):
    """No protected write, no tick, no verdict mutation. The agent tier may
    suggest; everything else stays where it was."""
    from agent_mission.__main__ import main
    before = st.load()
    assert main(["sharpen", "--propose", "--on", "g"]) == 0
    after = st.load()

    assert after.success_criteria == before.success_criteria, \
        "a protected field must be byte-identical after a sharpen pass"
    assert after.objective == before.objective
    assert after.done_count == before.done_count, "sharpen never ticks"
    assert all(not i.claimed_done for i in after.items), \
        "sharpen never touches a claim or its verdict"
    new = [i for i in after.items if not i.accepted]
    assert new and all(i.proposed_by == "agent" for i in new)
    # Every event the pass wrote is a proposal. Asserting on the LOG, not just
    # the folded state, is what catches a write that happens to fold to the
    # same value.
    written = [e for e in st.events() if e.get("kind") not in
               ("created", "set", "proposed", "accepted", "completed")]
    assert not written, f"sharpen wrote something other than proposals: {written}"
    sets = [e for e in st.events() if e.get("kind") == "set"]
    assert len(sets) == 1, "only the fixture's own set(), none from sharpen"


def test_an_ignored_proposal_leaves_the_criterion_byte_identical(st):
    """Acceptance is the only path. Ignoring is the common case and must be
    free -- a criterion that quietly drifts because an agent suggested
    something is the failure the protected tier exists to prevent."""
    from agent_mission.__main__ import main
    original = list(st.load().success_criteria)
    main(["sharpen", "--propose", "--on", "g"])
    # the human does nothing at all
    assert st.load().success_criteria == original


def test_a_template_is_advice_and_never_a_refusal(st):
    """Templates are advisory: a claim that ignores one is still recorded.
    A verifier that refuses input teaches people to stop reporting."""
    item = _accepted(st, "translate the share page")
    st.claim_done(item, "I did it, no file named anywhere", by="agent")
    assert st.load().suggested, "the claim is recorded regardless of shape"


def test_the_board_serves_no_sharpen_route(st):
    """C10c: the absence of an ENDPOINT, not the absence of a button."""
    from agent_mission import actions
    assert "sharpen" not in actions.ACTIONS
    sess = actions.Session(enabled=True)
    with pytest.raises(ValueError):
        actions.apply(sess, sess.code, "sharpen", "g", ids=[])


# ---- the frozen 12, with synthetic paths ----------------------------------
# Shapes taken from the 2026-08-27 review; paths invented. Each pair is
# (claim as written, the same claim rewritten against a template).
FROZEN = [
    ("All three public share pages now route through t(): PageOne (8 calls), "
     "PageTwo (8), and PageThree which was the last holdout at ZERO",
     "done: three share pages routed through t() (src/pages/share.tsx)"),
    ("catalog.test.tsx:221 asserts every key carries all 8 locales",
     "done: locale completeness asserted (src/lib/catalog.test.tsx)"),
    ("Both permissions traced to their source and REMOVED from the build",
     "done: permissions removed (app/config.json)"),
    ("All four bug classes now scanned in catalog.test.tsx",
     "done: four bug classes scanned (src/lib/catalog.test.tsx)"),
    ("pseudoLocalize in both web and mobile, with 2 tests",
     "done: pseudo-localisation added (src/lib/i18n.ts, app/lib/i18n.ts)"),
    ("Same file, lines 193-203: three tests assert neither catalog drifts",
     "done: catalog mirror asserted (src/lib/catalog.test.tsx)"),
    ("BOTH halves now exist: a setup file plus five suites",
     "done: mobile harness wired (app/jest.setup.js)"),
    # The Expo Router shape: parentheses INSIDE the path.
    ("the auth screen now has 16 t() calls, was 0",
     "done: auth screen localised (app/(auth)/index.tsx)"),
    ("All FOUR synthesised labels removed from the importer",
     "done: synthesised labels removed (api/routers/trips.py)"),
    ("DELETE /auth/me deleting across all 18 tables",
     "done: account deletion shipped (api/routers/auth.py, "
     "api/tests/test_deletion.py)"),
    ("Web parity: the profile page calls the same endpoint",
     "done: web parity (src/pages/Profile.tsx)"),
    ("Location insights ported and verified on device",
     "done: insights ported (app/components/Insights.tsx)"),
]


def test_the_frozen_set_carries_no_real_paths():
    """C16's leak guard, applied to this file. The real claims name real
    directories in a private workspace; the fixture must not."""
    src = Path(__file__).read_text(encoding="utf-8")
    # Built from fragments so this assertion cannot match itself.
    for leak in ("/Users" + "/jonathan" + "lee", "builds" + "/trip" + "nom",
                 "cow" + "ork"):
        assert leak not in src, f"real path {leak!r} leaked into the fixture"


def test_rewriting_against_a_template_makes_claims_checkable(tmp_path):
    """C18c's stopping rule, as a test: the format change must move the
    population, and it must never manufacture backing.

    The pre-registered threshold was 6 of 12. Here every path exists because
    the fixture creates it -- so this asserts the weaker, exact thing: a
    rewrite is EXTRACTABLE where the original was silent. Whether the file is
    really there stays `Path.exists()`'s question, never the template's.
    """
    from agent_mission.claims import _artifacts, verdict_for
    silent_before = extractable_after = 0
    for original, rewrite in FROZEN:
        if not _artifacts(original):
            silent_before += 1
        arts = _artifacts(rewrite)
        if arts:
            extractable_after += 1
            for a in arts:
                (tmp_path / a).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / a).write_text("x", encoding="utf-8")

    assert silent_before == 12, "all twelve were silent as written"
    assert extractable_after >= 6, \
        f"kill rule: only {extractable_after} of 12 became checkable"
    assert extractable_after == 12, \
        "every one of these names a file its author already knew"

    # And the second kill rule: nothing reads backed unless the disk says so.
    for _, rewrite in FROZEN:
        assert verdict_for(rewrite, cwd=str(tmp_path))["ok"]
    missing = "done: invented (src/does/not/exist.ts)"
    v = verdict_for(missing, cwd=str(tmp_path))
    assert not v["ok"] and v["unbacked"], \
        "a template must never make an absent file read as backed"


def test_a_parenthesised_path_is_extractable(tmp_path):
    """Expo Router names route groups `(auth)` / `(tabs)`, so parentheses sit
    inside the path of every routed screen. The extractor's `([^()]*)` could
    not match a group containing one, so those claims went SILENT while the
    file sat on disk -- silence reads as 'cited nothing', the opposite of what
    the author did."""
    from agent_mission.claims import _artifacts, verdict_for
    p = tmp_path / "app" / "(auth)" / "index.tsx"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    text = "done: auth screen localised (app/(auth)/index.tsx)"
    assert _artifacts(text) == ["app/(auth)/index.tsx"]
    assert verdict_for(text, cwd=str(tmp_path))["ok"]


def test_prose_without_an_artifact_still_yields_nothing(tmp_path):
    """C16's guarantee, which the parens fix must not erode: a claim naming
    nothing checkable is honestly unchecked, never counted as backed."""
    from agent_mission.claims import _artifacts, verdict_for
    for text in ("done: finished everything, trust me",
                 "done: shipped it (all of the pages)",
                 "done: unbalanced (src/a.ts"):
        assert _artifacts(text) == [], text
        assert not verdict_for(text, cwd=str(tmp_path))["ok"]


def test_the_readme_lists_sharpen():
    """The commands table is the contract; a command missing from it is a
    command nobody finds."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"\|\s*`sharpen`", readme), "sharpen is not in the table"
