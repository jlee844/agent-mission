"""Board layout: the header's controls mean one thing, everywhere.

A fresh-eyes layout review (2026-10-25), run against a copy of the real store
and checked against this file line by line, found the board confusing for
three concrete reasons. Each is pinned here; each was invisible to every
test that existed, because none of them looked at what the page shows.

1. "Waiting on you" had THREE definitions on one page. The server computes
   `needs_you` and the page never read it. The work view counted proposals
   only -- so a goal with a claim awaiting confirm read "0 waiting on you" --
   and filed that claim in a lane titled "in review — a claim awaiting a
   check", which reads like a job for a verifier. The `needs you` filter
   hid that goal. The inbox, alone, said 1.
2. Controls were shown where they do nothing. The filter row is `hidden`
   outside the goals view, but `.chips{display:flex}` beats the `hidden`
   attribute -- so all/live/needs you/ended showed in every view, worked in
   one, and still lit up and changed the counter when clicked elsewhere.
3. The strip's `show` jumped into the goals grid, which is hidden in the
   other two views, so it did nothing in the view the board opens on.
"""
from agent_mission.board import PAGE


def test_hidden_means_hidden_everywhere():
    """An author `display` rule beats the `hidden` attribute. It broke the
    filter row, and an hour earlier the `▸ more` control the same way."""
    assert "[hidden]{display:none!important}" in PAGE


def test_one_definition_of_waiting_on_you():
    assert "const waitingOn = rows =>" in PAGE
    # every surface that counts it uses it
    for use in ("waitingOn(all)",):
        assert PAGE.count(use) >= 3, "strip, work view, inbox and the tab"


def test_the_needs_you_filter_uses_the_servers_answer():
    """The server's `_needs_you` counts claims awaiting confirm. The page's
    own predicate did not, and hid exactly the goal waiting on him."""
    assert "FILTER === 'todo'  && s.needs_you" in PAGE


def test_waiting_comes_first_in_the_work_view():
    i_accept = PAGE.index("lane('waiting','waiting for your accept'")
    i_confirm = PAGE.index("lane('review','waiting for your confirm'")
    i_ready = PAGE.index("lane('ready','ready to pick up'")
    assert i_accept < i_ready and i_confirm < i_ready, \
        "what waits on him leads; ready work is last"
    assert "a claim awaiting a check" not in PAGE, \
        "it awaits HIS confirm, not a verifier's check"


def test_show_goes_to_the_inbox():
    assert "document.querySelector('.chip[data-v=inbox]').click()" in PAGE


def test_the_inbox_tab_carries_the_count():
    assert "id=inboxn" in PAGE


def test_the_header_counts_goals_not_sessions():
    """It printed `${all.length} sessions` -- rows are GOALS."""
    assert "${all.length} sessions" not in PAGE
    assert "${all.length} goals" in PAGE


def test_amber_only_means_waiting_on_you_in_the_work_view_too():
    """The existing guard only reads the inbox's CSS, so the amber
    "N may be done already" in the work view slipped past it."""
    assert "<span class=warn><b>${stale}</b> may be done already" not in PAGE


# ── acting from the work view ────────────────────────────────────────────
#
# Once the work view led with "waiting for your accept / confirm", it showed
# the items that need you first and offered no way to act on them: its rows
# had no buttons, by original design, when it was only a status page. The
# owner chose the same buttons the inbox has, on the waiting lanes only;
# ready rows stay read-only, since sessions take those.

def test_waiting_rows_carry_accept_and_decline():
    i = PAGE.index("function laneRow(")
    body = PAGE[i:PAGE.index("\n}\n", i)]
    assert "kind === 'waiting'" in body
    assert "data-do=accept" in body and "data-do=decline" in body


def test_confirm_rows_carry_confirm():
    i = PAGE.index("function laneRow(")
    body = PAGE[i:PAGE.index("\n}\n", i)]
    assert "kind === 'review'" in body and "data-do=done" in body


def test_ready_rows_stay_read_only():
    i = PAGE.index("function laneRow(")
    body = PAGE[i:PAGE.index("\n}\n", i)]
    assert "kind === 'ready'" not in body.split("const acts")[1].split("const chip")[0], \
        "ready rows get no action buttons"


def test_work_view_buttons_are_visible_without_hover():
    """`.act` is opacity:0 until hovered everywhere but the inbox. In the
    work view that made the new buttons clickable but invisible -- and on a
    touch screen there is no hover, so they would never appear."""
    assert "#work .qrow .act{opacity:1" in PAGE


def test_buttons_only_with_a_write_code():
    i = PAGE.index("function laneRow(")
    body = PAGE[i:PAGE.index("\n}\n", i)]
    assert "WRITABLE && CODE()" in body


# ── tier 2 (review R6, R8, R9, R10) ──────────────────────────────────────

def _fn(name):
    i = PAGE.index(f"function {name}(")
    return PAGE[i:PAGE.index("\n}\n", i)]


def test_search_narrows_the_work_view():
    """R6. Search only narrowed the goals grid; the work queue -- where you
    look for a half-remembered task -- ignored it."""
    body = _fn("renderWork")
    assert "QUERY" in body and "lanes[k] = lanes[k].filter(" in body


def test_search_narrows_the_inbox():
    body = _fn("renderInbox")
    assert "QUERY" in body


def test_view_tabs_look_unlike_filters():
    """R8. Tabs, filters, toggles and a debug button were one identical chip."""
    assert '<div class="chips tabs">' in PAGE
    assert ".tabs .chip" in PAGE, "tabs get their own style"


def test_compact_lives_with_the_goals_only_filters():
    """R8. `compact` only acts on the goals grid, so it sits in the group
    that is hidden outside goals."""
    fchips = PAGE[PAGE.index("id=fchips"):]
    fchips = fchips[:fchips.index("</div>")]
    assert "id=dense" in fchips


def test_report_is_a_quiet_link_not_a_view_button():
    assert "report a problem" in PAGE
    assert 'class=chip id=report' not in PAGE


def test_renames():
    """R9."""
    assert ">active</button>" in PAGE and "data-f=live aria-pressed=false>live<" not in PAGE
    assert "d untouched</span>" not in PAGE and "untouched ${" in PAGE


def test_o_opens_a_goal_that_exists():
    """R10. `o open goal` was advertised -- and ticked done -- while nothing
    on the page matched its target."""
    assert "data-goal=" in _fn("renderInbox")
    assert "data-card=" in PAGE
    assert "openGoal(" in PAGE


def test_write_keys_are_hinted_only_when_they_can_work():
    """R10. a/c/x need a write code; on a read-only board they do nothing."""
    body = _fn("renderInbox")
    assert "WRITABLE && CODE()" in body.split("class=keys")[0][-400:] or \
           "keysHint" in body


def _work():
    """renderWork through to renderInbox. `_fn` stops at the first column-0
    `}`, and renderWork holds a nested function that closes that way."""
    return PAGE[PAGE.index("function renderWork("):PAGE.index("function renderInbox(")]


def test_an_emptied_search_says_nothing_matched():
    """With a search active, "Nothing queued, nothing waiting" claimed that
    81 queued items did not exist."""
    assert "Nothing matches “${esc(QUERY)}”." in _work()


def test_the_stats_line_counts_the_whole_board():
    """It mixed scales: "4 waiting on you" (all) beside "0 ready" (matches)."""
    body = _work()
    assert body.index("const all_n") < body.index("if (QUERY)")
    assert "${all_n.ready}</b> ready to pick up" in body
