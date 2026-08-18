"""Landing somebody else's plan on the board, and re-landing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_mission.__main__ import main                    # noqa: E402
from agent_mission.importer import norm, parse             # noqa: E402
from agent_mission.store import MissionStore, root_for     # noqa: E402

PLAN = """\
# Implementation Plan

## Task 1: Storage layer
- [x] **Step 1: Write the failing test**
- [ ] Step 2: Implement `SpotList` types

## Task 2: Sync
- [ ] 2.1 Wire the [sheet component](./ui.md)
  - [ ] verify both directions

```bash
- [ ] inside a fence
```
"""


def _mission(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(root_for("s"))
    st.create("s", "/tmp", "obj", by="human")
    return st


def test_headings_and_bullets_become_one_tree():
    rows = parse(PLAN)
    assert [(r.depth, r.text) for r in rows] == [
        (0, "Implementation Plan"),
        (1, "Storage layer"),
        (2, "Step 1: Write the failing test"),
        (2, "Step 2: Implement SpotList types"),
        (1, "Sync"),
        (2, "Wire the sheet component"),
        (3, "verify both directions"),
    ]


def test_a_fenced_block_is_not_a_plan():
    """A plan file explains itself with example commands. Importing them adds
    tasks nobody wrote."""
    assert all("fence" not in r.text for r in parse(PLAN))


def test_numbering_the_source_carries_is_dropped():
    """'2.1 Wire' and '3.1 Wire' are the same task renumbered. Keeping the
    number makes every item look new after a replan."""
    assert norm("2.1 Wire the sheet") == norm("Task 3: wire the SHEET.")


def test_a_ticked_source_item_is_imported_unticked(tmp_path, monkeypatch, capsys):
    """The file says it is finished. The file is not the human."""
    _mission(tmp_path, monkeypatch)
    (tmp_path / "p.md").write_text(PLAN)
    main(["import", str(tmp_path / "p.md"), "--session", "s"])
    out = capsys.readouterr().out
    m = MissionStore(root_for("s")).load()
    assert m.done_count == 0
    assert "1 was ticked in the source" in out
    assert all(not d["accepted"] for d in m.checklist), "proposals, not accepted"


def test_re_importing_diffs_instead_of_duplicating(tmp_path, monkeypatch, capsys):
    """A replan that duplicated the tree every time is read once, then ignored."""
    _mission(tmp_path, monkeypatch)
    p = tmp_path / "p.md"
    p.write_text(PLAN)
    main(["import", str(p), "--session", "s"])
    before = len(MissionStore(root_for("s")).load().checklist)
    capsys.readouterr()

    p.write_text(PLAN + "\n## Task 3: Rollout\n- [ ] Feature flag\n")
    main(["import", str(p), "--session", "s"])
    out = capsys.readouterr().out
    m = MissionStore(root_for("s")).load()
    assert len(m.checklist) == before + 2, "only the new rows"
    assert f"{before} already in the plan" in out


def test_what_vanished_is_reported_never_removed(tmp_path, monkeypatch, capsys):
    """Removing is the human's call. A tool that silently prunes the plan
    because a file changed is one you stop trusting with the plan."""
    _mission(tmp_path, monkeypatch)
    p = tmp_path / "p.md"
    p.write_text(PLAN)
    main(["import", str(p), "--session", "s"])
    n = len(MissionStore(root_for("s")).load().checklist)
    capsys.readouterr()

    p.write_text("# Implementation Plan\n\n## Task 2: Sync\n- [ ] 2.1 Wire the sheet component\n")
    main(["import", str(p), "--session", "s", "--strict"])
    out = capsys.readouterr().out
    assert "Storage layer" in out and "not removed" in out
    assert len(MissionStore(root_for("s")).load().checklist) == n


def test_a_huge_file_is_refused_rather_than_dumped(tmp_path, monkeypatch, capsys):
    _mission(tmp_path, monkeypatch)
    p = tmp_path / "big.md"
    p.write_text("\n".join(f"- item {i}" for i in range(200)))
    assert main(["import", str(p), "--session", "s"]) == 1
    assert "the cap is 60" in capsys.readouterr().out
    assert MissionStore(root_for("s")).load().checklist == []


def test_a_file_with_no_plan_in_it_says_so(tmp_path, monkeypatch, capsys):
    _mission(tmp_path, monkeypatch)
    p = tmp_path / "prose.md"
    p.write_text("Just some prose about the design.\n\nNo tasks here.\n")
    assert main(["import", str(p), "--session", "s"]) == 1
    assert "nothing plan-shaped" in capsys.readouterr().out


def test_why_shows_when_a_protected_field_changed(tmp_path, monkeypatch, capsys):
    st = _mission(tmp_path, monkeypatch)
    st.set_protected("objective", "first", by="human")
    st.set_protected("objective", "second", by="human")
    main(["why", "objective", "--session", "s"])
    out = capsys.readouterr().out
    assert "first" in out and "second" in out and out.index("first") < out.index("second")


def test_a_bullet_that_wraps_is_rejoined():
    """Found by importing a real plan instead of one written for this parser:
    prose wrapped at 90 characters and every long task arrived cut mid
    sentence. Half a task is worse than none, because it looks complete."""
    rows = parse("""\
## Plan
- A Stop hook that runs the verifier over the session file
  and prints only the unbacked claims.
- Second item
""")
    assert rows[1].text == ("A Stop hook that runs the verifier over the "
                           "session file and prints only the unbacked claims.")
    assert rows[2].text == "Second item"


def test_a_wrapped_line_does_not_swallow_the_next_heading():
    rows = parse("## One\n- a task\n  continued here\n## Two\n- another\n")
    assert [r.text for r in rows] == ["One", "a task continued here",
                                      "Two", "another"]
