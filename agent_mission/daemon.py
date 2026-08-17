"""One board, shared by every session.

`mission init` in a second session must not start a second server or a second
page — it should appear as another card on the board already open. So the port
is recorded in a file, and any session either finds a live board or starts the
one everybody uses.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PORT = 8976


def _home() -> Path:
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


def _record() -> Path:
    return _home() / "board.json"


def _responding(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def running() -> dict | None:
    """The board everyone shares, if it is actually up.

    A recorded port whose process died is worse than no record: it sends you to
    a dead URL. So the port is probed, not trusted.
    """
    try:
        rec = json.loads(_record().read_text(encoding="utf-8"))
    except Exception:
        return None
    if _responding(int(rec.get("port", 0))):
        return rec
    _record().unlink(missing_ok=True)
    return None


def claim(port: int) -> None:
    """Called by the board itself once it has bound. Records port and OWN pid."""
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    _record().write_text(json.dumps(
        {"port": port, "pid": os.getpid(), "started": time.time()}),
        encoding="utf-8")


def release(port: int) -> None:
    """Drop the record on the way out, but only if it is still ours."""
    try:
        rec = json.loads(_record().read_text(encoding="utf-8"))
    except Exception:
        return
    if rec.get("pid") == os.getpid():
        _record().unlink(missing_ok=True)


def _is_board(pid: int) -> bool:
    """Is this pid actually our board? A pid is recycled the moment it dies,
    so signalling one because a file once named it can hit anything."""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return False
    return "agent_mission" in out and "board" in out


def _free(port: int) -> bool:
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def ensure(port: int = DEFAULT_PORT, quiet: bool = False) -> str | None:
    """Return the URL of the shared board, starting it if nobody has."""
    rec = running()
    if rec:
        return f"http://127.0.0.1:{rec['port']}"

    for p in range(port, port + 12):
        if _free(p):
            port = p
            break
    else:
        return None

    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    log = home / "board.log"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_mission", "board", "--port", str(port),
             "--foreground"],
            stdout=log.open("a"), stderr=subprocess.STDOUT,
            start_new_session=True,          # survives the session that spawned it
            env={**os.environ, "AGENT_MISSION_HOME": str(home)},
        )
    except Exception:
        return None

    for _ in range(40):                      # up to ~4s for it to bind
        if _responding(port):
            # The board writes its own record (daemon.claim) -- see there for
            # why the launcher must not.
            return f"http://127.0.0.1:{port}"
        time.sleep(0.1)
    return None


def stop() -> bool:
    rec = running()
    if not rec:
        return False
    pid = int(rec.get("pid", 0))
    if not _is_board(pid):
        # The record names something that is not a board. Do not signal it.
        _record().unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    _record().unlink(missing_ok=True)
    return True
