"""Codex (GPT) threads are board sessions like Claude Code sessions.

Codex exports CODEX_THREAD_ID to the commands it runs, runs them with no
terminal, and sandboxes them so `ps` is refused -- which blinds the ancestor
walk. These pin that a Codex thread is identified, is recognised as an agent
from its environment alone, cannot accept or tick, and that the installer
for its hooks never clobbers anything already there.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_mission import codex as CX
from agent_mission import whoami as W
from agent_mission.session import current_session_id

ROOT = Path(__file__).resolve().parents[1]


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "CODEX_", "ANTHROPIC_"))
           and k not in ("AI_AGENT", "AGENT_MISSION_I_AM_HUMAN")}
    env.update(extra)
    return env


def test_a_codex_thread_has_a_session_id(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "01a1-codex")
    assert current_session_id() == "01a1-codex"


def test_claude_wins_when_codex_runs_inside_a_claude_session(monkeypatch):
    """A Codex started from a Claude session inherits both ids; that
    session's hooks record the Claude one."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-1")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-1")
    assert current_session_id() == "claude-1"


def test_codex_is_an_agent_from_its_environment_alone(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("CLAUDE", "CODEX_", "ANTHROPIC_")) or k == "AI_AGENT":
            monkeypatch.delenv(k)
    monkeypatch.setenv("CODEX_THREAD_ID", "x")
    assert "CODEX_THREAD_ID" in W.agent_env()


def test_a_person_with_codex_home_set_is_not_an_agent(monkeypatch):
    """A false positive refuses a real person's write."""
    for k in list(os.environ):
        if k.startswith(("CLAUDE", "CODEX_", "ANTHROPIC_")) or k == "AI_AGENT":
            monkeypatch.delenv(k)
    monkeypatch.setenv("CODEX_HOME", "/somewhere")
    assert W.agent_env() == []


def test_codex_in_an_ancestor_command_is_an_agent():
    assert W._looks_like_agent("/Applications/ChatGPT.app/x/codex exec y")
    assert not W._looks_like_agent("/usr/bin/codexify")


def _cli(args, env):
    return subprocess.run([sys.executable, "-m", "agent_mission", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def test_a_codex_thread_can_propose_attach_and_take_but_not_rule(tmp_path):
    home = tmp_path / "store"
    human = _clean_env(AGENT_MISSION_HOME=str(home),
                       AGENT_MISSION_I_AM_HUMAN="1")
    r = _cli(["init", "--on", "border", "--from-file", "-"], human) \
        if False else None
    # Build the goal through the store as a person would have.
    code = ("from agent_mission import missions as M\n"
            "from agent_mission.store import MissionStore\n"
            "import os\n"
            "root = M.missions_root() / 'border'\n"
            "st = MissionStore(root)\n"
            "st.create('human-tty', '/w', 'Ship the game', by='human', "
            "typed_by='human')\n"
            "st.set_protected('name', 'Border', by='human', typed_by='human')\n")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                   env=dict(human))

    codex = _clean_env(AGENT_MISSION_HOME=str(home),
                       CODEX_THREAD_ID="01a1-codex-thread",
                       CODEX_SANDBOX="seatbelt")
    assert _cli(["attach", "border"], codex).returncode == 0
    att = json.loads((home / "attachments.json").read_text()) \
        if (home / "attachments.json").exists() else None
    from agent_mission import missions as M
    os.environ["AGENT_MISSION_HOME"] = str(home)
    try:
        assert M.attachments().get("01a1-codex-thread") == "border"
    finally:
        os.environ.pop("AGENT_MISSION_HOME", None)

    p = _cli(["propose", "Add the radar trigger", "--on", "border"], codex)
    assert p.returncode == 0, p.stdout + p.stderr
    item = p.stdout.split("proposed ")[1].split()[0]

    acc = _cli(["accept", item, "--on", "border"], codex)
    assert acc.returncode != 0, "a Codex thread must not accept its own work"

    assert _cli(["accept", item, "--on", "border"], human).returncode == 0
    t = _cli(["take", item, "--on", "border"], codex)
    assert t.returncode == 0, t.stdout + t.stderr
    d = _cli(["done", item, "--on", "border"], codex)
    assert d.returncode != 0, "nor tick it"


def test_setup_codex_refuses_without_a_person(tmp_path):
    env = _clean_env(CODEX_HOME=str(tmp_path / "codex"),
                     CODEX_THREAD_ID="t")
    r = _cli(["setup", "--codex"], env)
    assert r.returncode == 1 and "person's keyboard" in r.stdout
    assert not (tmp_path / "codex" / "hooks.json").exists()


def test_install_is_idempotent_and_keeps_other_hooks(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    theirs = {"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "python3 flywheel.py"}]}]}}
    (home / "hooks.json").write_text(json.dumps(theirs))
    (home / "AGENTS.md").write_text("# My rules\n\nBe brief.\n")
    monkeypatch.setenv("CODEX_HOME", str(home))

    did = CX.install(cmd="/bin/mission")
    assert did, "first install changes both files"
    data = json.loads((home / "hooks.json").read_text())
    ups = [h["command"] for g in data["hooks"]["UserPromptSubmit"]
           for h in g["hooks"]]
    assert "python3 flywheel.py" in ups and "/bin/mission signal" in ups
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == \
        "/bin/mission whereami --full"
    agents = (home / "AGENTS.md").read_text()
    assert agents.startswith("# My rules") and CX.MARK_BEGIN in agents
    assert list(home.glob("*.bak-mission-*")), "backups taken"

    assert CX.install(cmd="/bin/mission") == [], "second run changes nothing"
    assert (home / "AGENTS.md").read_text().count(CX.MARK_BEGIN) == 1


def test_fresh_install_uses_codex_hooks_wrapper_and_status_validates_it(
        tmp_path, monkeypatch):
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))

    CX.install(cmd="/bin/mission")

    path = home / "hooks.json"
    data = json.loads(path.read_text())
    assert set(data) == {"hooks"}
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit"}
    assert CX.status(cmd="/bin/mission")["hooks"]


def test_install_migrates_the_old_flat_hooks_file_and_keeps_description(
        tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    old = {
        "description": "my hooks",
        "SessionStart": [{"hooks": [{"type": "command",
                                       "command": "existing startup"}]}],
    }
    path = home / "hooks.json"
    path.write_text(json.dumps(old))
    monkeypatch.setenv("CODEX_HOME", str(home))

    CX.install(cmd="/bin/mission")

    data = json.loads(path.read_text())
    assert data["description"] == "my hooks"
    startup = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert startup == "existing startup"
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == \
        "/bin/mission signal"
    assert list(home.glob("hooks.json.bak-mission-*"))
    assert CX.status(cmd="/bin/mission")["hooks"]


def test_status_rejects_old_flat_schema_until_installer_migrates_it(
        tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "hooks.json").write_text(json.dumps(CX.wanted_hooks("/bin/mission")))
    monkeypatch.setenv("CODEX_HOME", str(home))

    status = CX.status()
    assert not status["hooks"]
    assert "top-level hooks object" in status["hooks_error"]


def test_codex_check_explains_invalid_hook_schema(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "hooks.json").write_text(json.dumps(
        CX.wanted_hooks("/bin/mission")))
    (home / "AGENTS.md").write_text(CX.AGENTS_BLOCK)
    env = _clean_env(CODEX_HOME=str(home), CODEX_THREAD_ID="t")

    result = _cli(["setup", "--codex", "--check"], env)

    assert result.returncode == 1
    assert "hooks.json      invalid" in result.stdout
    assert "expected top-level hooks object" in result.stdout


def test_a_moved_mission_binary_replaces_the_old_hook(tmp_path, monkeypatch):
    """Two signal hooks print every line twice."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    CX.install(cmd="/old/mission")
    CX.install(cmd="/new/mission")
    data = json.loads((tmp_path / "hooks.json").read_text())
    cmds = [h["command"] for g in data["hooks"]["UserPromptSubmit"]
            for h in g["hooks"]]
    assert cmds == ["/new/mission signal"]


def test_the_sandbox_line_is_printed_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("model = 'x'\n")
    CX.install(cmd="mission")
    assert (tmp_path / "config.toml").read_text() == "model = 'x'\n"
    assert any("writable_roots" in s for s in CX.next_steps("/s"))
    data = json.loads((tmp_path / "hooks.json").read_text())
    assert "SessionStart" in data["hooks"]


def test_migration_keeps_another_tools_flat_hooks(tmp_path, monkeypatch):
    """An old flat file holding a third party's PreToolUse hook used to be
    refused, because the migration knew only the two events we install."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    theirs = [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "guard.sh"}]}]
    (tmp_path / "hooks.json").write_text(json.dumps({"PreToolUse": theirs}))
    CX.install(cmd="/bin/mission")
    data = json.loads((tmp_path / "hooks.json").read_text())
    assert data["hooks"]["PreToolUse"] == theirs
    assert "UserPromptSubmit" in data["hooks"] and set(data) == {"hooks"}


def test_a_sandbox_blocked_write_is_a_plain_message(tmp_path, monkeypatch,
                                                   capsys):
    """Inside Codex's sandbox a refused board write said nothing useful; it
    must name the cause and the one line that fixes it, and never suggest
    full access."""
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")

    def blocked(a):
        raise PermissionError(1, "Operation not permitted",
                              str(tmp_path / "store" / "missions" / "g" /
                                  "events.jsonl"))
    monkeypatch.setattr(MM, "cmd_show", blocked)
    assert MM.main(["show", "--on", "g"]) == 1
    out = capsys.readouterr().out
    assert "board is fine" in out and "writable_roots" in out
    assert str(tmp_path / "store") in out
    assert "Do not ask for full access" in out


def test_a_permission_error_elsewhere_stays_a_traceback(tmp_path, monkeypatch):
    from agent_mission import __main__ as MM
    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path / "store"))

    def blocked(a):
        raise PermissionError(13, "Permission denied", "/etc/elsewhere")
    monkeypatch.setattr(MM, "cmd_show", blocked)
    with pytest.raises(PermissionError):
        MM.main(["show", "--on", "g"])


def test_the_sandbox_line_check_reads_config_toml():
    store = "/Users/x/.agent-mission"
    assert CX.sandbox_allows(store, '[sandbox_workspace_write]\n'
                             'writable_roots = ["/tmp", "/Users/x/.agent-mission/"]\n')
    assert not CX.sandbox_allows(store, 'model = "o3"\n')
    assert not CX.sandbox_allows(store, 'writable_roots = ["/tmp"]\n')


def test_the_agents_block_says_what_to_do_when_refused():
    assert "Never ask for full access" in CX.AGENTS_BLOCK
    assert "writable_roots" in CX.AGENTS_BLOCK


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_really_unwritable_store_gives_the_message(tmp_path):
    home = tmp_path / "store"
    human = _clean_env(AGENT_MISSION_HOME=str(home),
                       AGENT_MISSION_I_AM_HUMAN="1")
    subprocess.run([sys.executable, "-c",
                    "from agent_mission import missions as M\n"
                    "from agent_mission.store import MissionStore\n"
                    "MissionStore(M.missions_root() / 'g').create("
                    "'h', '/w', 'x', by='human', typed_by='human')"],
                   cwd=ROOT, check=True, env=human)
    paths = [home, *home.rglob("*")]
    for p in paths:
        p.chmod(p.stat().st_mode & ~0o222)
    try:
        codex = _clean_env(AGENT_MISSION_HOME=str(home),
                           CODEX_THREAD_ID="t", CODEX_SANDBOX="seatbelt")
        r = _cli(["propose", "y", "--on", "g"], codex)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "writable_roots" in r.stdout, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert _cli(["queue", "--on", "g"], codex).returncode == 0, \
            "reading still works"
    finally:
        for p in paths:
            p.chmod(p.stat().st_mode | 0o200)
