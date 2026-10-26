"""An item needs a SHORT name, or nothing can refer to it briefly.

`Item.text` is a paragraph -- real items on live goals run past 300
characters -- so every surface truncates mid-sentence and conversation falls
back to the 8-character id, which the human then cannot find on the board.
Reported 2026-10-25 after one `mission` call printed 26 paragraphs.

The label is DERIVED when not authored, from the text up to the first colon
or em-dash, because that already reads as a title on most rows ("Build and
upload", "Mode-switch routing", "DEPLOY BLOCKER for navigation"). It is a
display concern only: `text` is untouched, and `--full` still prints it.
"""
from agent_mission.store import Item, label_of


def _l(text: str, label: str = "") -> str:
    return label_of(Item("abcd1234", text, label=label))


def test_an_authored_label_wins():
    assert _l("a long rambling description", label="Short name") == "Short name"


def test_it_cuts_at_an_em_dash():
    assert _l("Mode-switch routing — the actual trip-optimization win") \
        == "Mode-switch routing"


def test_it_cuts_at_a_colon():
    assert _l("DEPLOY BLOCKER for navigation: /directions is committed but "
              "not deployed") == "DEPLOY BLOCKER for navigation"


def test_a_short_text_is_its_own_label():
    assert _l("Build and upload") == "Build and upload"


def test_no_delimiter_falls_back_to_the_first_few_words():
    got = _l("Pay down the i18n debt baselined in the divergence json file "
             "with thirteen files and sixteen keys")
    assert got.startswith("Pay down the i18n debt")
    assert len(got) <= 52, got
    assert got.endswith("…")


def test_a_hyphenated_word_is_not_a_delimiter():
    """`-` inside a word is not a dash separator: cutting there would turn
    'Mode-switch routing' into 'Mode'."""
    assert _l("Mode-switch routing") == "Mode-switch routing"


def test_the_label_never_exceeds_the_cap():
    assert len(_l("x" * 300)) <= 52


def test_a_newline_ends_the_label():
    assert _l("First line here\nsecond paragraph") == "First line here"


def test_an_empty_text_is_empty_not_an_error():
    assert _l("") == ""


def test_the_truncation_marker_is_not_doubled(tmp_path, monkeypatch, capsys):
    """Seen in the first render: 'across every… …' -- the cap adds the
    ellipsis and the row added another."""
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s")
    f = tmp_path / "m.mission"
    f.write_text("NAME: P\n\nOBJECTIVE: p\n\nCHECKLIST:\n- " +
                 "check dark mode and mobile widths across every screen that "
                 "will appear in the store screenshots\n")
    main(["init", "--on", "g", "--from-file", str(f), "--no-edit", "--no-board"])
    capsys.readouterr()
    main(["show", "--on", "g"])
    out = capsys.readouterr().out
    assert "… …" not in out, out
    assert "…" in out, "it must still mark that text was cut"


def test_long_restores_the_full_text(tmp_path, monkeypatch, capsys):
    from agent_mission.__main__ import main
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MISSION_I_AM_HUMAN", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s")
    text = ("Mode-switch routing — the actual trip-optimization win that "
            "segments a day by transport node")
    f = tmp_path / "m.mission"
    f.write_text(f"NAME: P\n\nOBJECTIVE: p\n\nCHECKLIST:\n- {text}\n")
    main(["init", "--on", "g", "--from-file", str(f), "--no-edit", "--no-board"])
    capsys.readouterr()
    main(["show", "--on", "g"])
    assert "trip-optimization" not in capsys.readouterr().out
    main(["show", "--on", "g", "--long"])
    assert "trip-optimization" in capsys.readouterr().out
