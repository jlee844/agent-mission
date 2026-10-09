"""Item 13: the log's concurrency was "fine in practice" with no test.

Writes went through Python's buffered text writer (`open("a").write(...)`),
which makes no promise about how many `write(2)` calls it becomes — so two
sessions appending at the same instant could interleave MID-LINE and tear
both events. And every write method does `load()` -> check -> append, so two
callers could both pass the same check.
"""
import json
import multiprocessing as mp
import os
from pathlib import Path

import pytest

from agent_mission import missions as M
from agent_mission.store import MissionStore


def _writer(args):
    """A separate PROCESS, because threads share the GIL and would hide the
    very interleaving this is testing."""
    home, name, tag, n, payload_size = args
    os.environ["AGENT_MISSION_HOME"] = home
    st = MissionStore(Path(home) / "missions" / name)
    blob = "x" * payload_size
    for i in range(n):
        st.observe("notes", f"{tag}-{i}-{blob}", by="agent")
    return n


@pytest.mark.parametrize("payload_size", [20_000])
def test_eight_processes_writing_large_events_tear_nothing(
        tmp_path, payload_size):
    """8 writers x 100 events x 20 KB. Every line must still parse, and the
    fold must report zero damage."""
    home = str(tmp_path)
    os.environ["AGENT_MISSION_HOME"] = home
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "the goal", by="human")

    jobs = [(home, "g", f"w{i}", 100, payload_size) for i in range(8)]
    ctx = mp.get_context("spawn")
    with ctx.Pool(8) as pool:
        assert sum(pool.map(_writer, jobs)) == 800

    raw = (st.root / "events.jsonl").read_text(encoding="utf-8")
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    bad = []
    for ln in lines:
        try:
            json.loads(ln)
        except json.JSONDecodeError as e:
            bad.append((ln[:60], str(e)))
    assert not bad, f"{len(bad)} torn line(s): {bad[:2]}"

    fresh = MissionStore(st.root)
    m = fresh.load()
    assert m is not None
    assert fresh.damaged == 0, fresh.damage[:3]
    # 800 observed events plus the mission's own creation events.
    assert len(m.notes) == 800, f"{len(m.notes)} of 800 survived"


def _ticker(args):
    """Two processes racing to tick the SAME item."""
    home, name, iid = args
    os.environ["AGENT_MISSION_HOME"] = home
    st = MissionStore(Path(home) / "missions" / name)
    try:
        st.complete(iid, by="human")
        return "ok"
    except Exception as e:
        return type(e).__name__


def test_check_then_append_is_serialised(tmp_path):
    """Both callers used to read "not yet done", both pass the check, both
    append. The lock makes the decision one at a time; the fold is
    idempotent either way, so what this pins is that nothing RAISES and the
    item ends up done exactly once in state."""
    home = str(tmp_path)
    os.environ["AGENT_MISSION_HOME"] = home
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    iid = st.propose("work", by="agent")["item_id"]
    st.accept(iid, by="human")

    ctx = mp.get_context("spawn")
    with ctx.Pool(2) as pool:
        out = pool.map(_ticker, [(home, "g", iid)] * 2)
    assert all(r in ("ok", "NoSuchItemError") for r in out), out

    fresh = MissionStore(st.root)
    m = fresh.load()
    assert fresh.damaged == 0
    assert [i for i in m.items if i.id == iid][0].done is True


def test_one_line_is_one_write_syscall(tmp_path, monkeypatch):
    """The property underneath: a torn line is only possible if the writer
    splits a line across syscalls."""
    from agent_mission import store as S

    calls = []
    real = os.write

    def counting_write(fd, data):
        calls.append(len(data))
        return real(fd, data)

    monkeypatch.setattr(S.os, "write", counting_write)
    log = tmp_path / "events.jsonl"
    line = json.dumps({"kind": "observed", "value": "y" * 30_000}) + "\n"
    S._append_line(log, line)
    assert len(calls) == 1, f"{len(calls)} syscalls for one line"
    assert log.read_text().count("\n") == 1


def test_the_lock_never_breaks_a_write_when_flock_is_unavailable(
        tmp_path, monkeypatch):
    """Advisory means advisory: if the platform cannot lock, the write still
    happens. A guard that can refuse a write is worse than the race."""
    import builtins

    real_import = builtins.__import__

    def no_fcntl(name, *a, **k):
        if name in ("fcntl", "msvcrt"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    iid = st.propose("work", by="agent")["item_id"]

    monkeypatch.setattr(builtins, "__import__", no_fcntl)
    st.accept(iid, by="human")
    monkeypatch.setattr(builtins, "__import__", real_import)

    assert MissionStore(st.root).load().items[0].accepted is True


def test_the_lock_file_is_not_the_log(tmp_path):
    """Locking the log itself would block readers that take no lock."""
    monkeypatch_home = str(tmp_path)
    os.environ["AGENT_MISSION_HOME"] = monkeypatch_home
    st = MissionStore(M.missions_root() / "g")
    st.create("s1", str(tmp_path), "goal", by="human")
    iid = st.propose("work", by="agent")["item_id"]
    st.accept(iid, by="human")
    assert (st.root / ".lock").exists()
    assert (st.root / "events.jsonl").exists()
