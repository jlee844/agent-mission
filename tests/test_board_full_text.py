"""Every item's full text must be reachable on the board.

Reported 2026-10-25 from the board itself: a review row ended mid-word at
"Measured 2026-" with no way to read the rest. Three separate defects stood
behind that, each found by driving the board in a browser -- not by a test,
which is why each is pinned here:

1. the queue payload sent `i.text[:160]`, so the board never had the rest
   and no amount of UI could show it;
2. once rows could be tapped open, the list around them kept its height cap
   and scrolled, cutting the NEXT row off mid-line;
3. the click handler was bound to the goals and inbox views but not to the
   work view -- the view the board opens on -- so tapping did nothing there.
   It went unnoticed at desktop width, where those rows are not clamped.
"""
from pathlib import Path

from agent_mission import missions as M
from agent_mission.store import MissionStore

SRC = (Path(__file__).resolve().parents[1] / "agent_mission" / "board.py").read_text()
LONG = "x " * 200 + "THE-END"


def test_the_queue_payload_carries_the_whole_text(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission.board import snapshot
    st = MissionStore(M.missions_root() / "g")
    st.create("s", str(tmp_path), "goal", by="human")
    ev = st.propose(LONG.strip(), by="human")
    st.accept(ev["item_id"], by="human")
    rows = [r for g in snapshot() for lane in g["queue"].values() for r in lane]
    row = next(r for r in rows if r["id"] == ev["item_id"])
    assert row["text"].endswith("THE-END"), "the payload truncated the item"


def test_the_work_view_receives_clicks():
    assert "getElementById('work').addEventListener('click', onBoardClick)" in SRC


def test_an_opened_row_lifts_its_lists_height_cap():
    assert ".chk:has(.txt.open){max-height:none}" in SRC


def test_open_rows_survive_the_redraw():
    """The board redraws every few seconds; an opened row that snaps shut
    while you read it is worse than one that never opened."""
    assert "const OPEN = new Set();" in SRC
    assert SRC.count("OPEN.has(") >= 2, "both row templates must consult it"


def test_a_long_row_has_the_boards_own_expand_control():
    """`▸ more` / `▾ less`, the same control as the `▸ N finished` fold,
    rather than an invisible tap target."""
    assert "const moreBtn = id =>" in SRC
    assert SRC.count("${moreBtn(") >= 2, "both row templates carry it"
    assert '.more::before{content:"▸ "' in SRC
    assert '.more[aria-expanded="true"]::before{content:"▾ "}' in SRC


def test_the_control_is_only_shown_where_text_is_cut_off():
    """A `more` that expands nothing is a control that lies. Clipping depends
    on width, so it is measured after every draw of every view and on
    resize, not decided in the template."""
    assert "function markClipped(root)" in SRC
    for view in ("'work'", "'inbox'"):
        assert f"markClipped(document.getElementById({view}))" in SRC
    assert "markClipped(g);" in SRC
    assert "addEventListener('resize'" in SRC


def test_no_backslash_escape_reaches_the_page_css():
    """The page lives inside a Python string, so a CSS escape is read by
    PYTHON first. `content:"\\25B8"` became an octal \\25 plus a literal
    "B8", and the control rendered as "B8 MORE". Use the character itself,
    as the existing fold does.

    ⚠️ This must inspect the page Python PRODUCES, not board.py's source. The
    first version grepped the source text, where the bad escape is a harmless
    backslash and two digits; with the bug put back it still passed. A check
    that cannot fail is worse than none -- it reports a check never made.
    """
    import re
    from agent_mission.board import PAGE
    bad = re.findall(r'content:"[^"]*[\x00-\x1f][^"]*"', PAGE)
    assert not bad, f"control character in CSS content -- an escape went wrong: {bad}"
