"""`mission setup` installs the pane for EVERY session.

The pane used to live in a session-scoped dev folder, so it loaded only in
the session that wrote it. `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of
the user settings loads a plugin folder in every session, including ones the
desktop app starts -- and never from a project's settings, so this is a user
setting by construction.

⚠️ That variable is SHARED with every other plugin folder the person names.
So install APPENDS to it and never replaces it: overwriting would silently
unload someone else's plugins, which is a far worse failure than not
installing ours.
"""
import json
import os

from agent_mission import setup_surfaces as S


def _settings(tmp_path, data=None):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data if data is not None else {}))
    return str(p)


def _dirs(path):
    env = json.loads(open(path).read()).get("env", {})
    return [d for d in env.get("CLAUDE_CODE_PLUGIN_DIRS", "").split(os.pathsep) if d]


def test_the_pane_is_a_default_surface():
    assert "pane" in S.SURFACES


def test_the_pane_folder_ships_with_the_tool():
    d = S.pane_dir()
    assert (d / ".claude-plugin" / "plugin.json").is_file(), d
    assert (d / "hooks" / "register.tsx").is_file(), d


def test_install_adds_the_pane_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    sp = _settings(tmp_path)
    assert S.plan("pane", settings=sp)["changes"]
    S.install("pane", settings=sp)
    assert str(S.pane_dir()) in _dirs(sp)


def test_install_KEEPS_other_plugin_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    theirs = os.pathsep.join(["/opt/a-plugin", "/opt/b-plugin"])
    sp = _settings(tmp_path, {"env": {"CLAUDE_CODE_PLUGIN_DIRS": theirs,
                                      "OTHER": "kept"}})
    S.install("pane", settings=sp)
    got = _dirs(sp)
    assert "/opt/a-plugin" in got and "/opt/b-plugin" in got, got
    assert str(S.pane_dir()) in got
    assert json.loads(open(sp).read())["env"]["OTHER"] == "kept"


def test_install_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    sp = _settings(tmp_path)
    S.install("pane", settings=sp)
    S.install("pane", settings=sp)
    assert _dirs(sp).count(str(S.pane_dir())) == 1
    assert S.plan("pane", settings=sp)["why"] == "already current"


def test_status_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    sp = _settings(tmp_path)
    row = next(r for r in S.status(settings=sp) if r["name"] == "pane")
    assert row["ok"] is False
    S.install("pane", settings=sp)
    row = next(r for r in S.status(settings=sp) if r["name"] == "pane")
    assert row["ok"] is True
