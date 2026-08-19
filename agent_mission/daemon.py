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


def identify(port: int, timeout: float = 0.6) -> dict | None:
    """Is OUR board on this port, serving THIS store?

    A bare TCP probe answers "something is listening", which is not the
    question. It made a live board with a lost record invisible (so a second
    one spawned on the next port) and made any foreign service on the recorded
    port look like the board (so the URL pointed at a stranger).
    """
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/identity", timeout=timeout) as r:
            data = _json.loads(r.read() or b"{}")
    except Exception:
        return None
    if not data.get("mission_board"):
        return None
    if str(data.get("home", "")) != str(_home()):
        return None                     # a board, but for a different store
    return data


def running() -> dict | None:
    """The board everyone shares, if it is actually up.

    A recorded port whose process died is worse than no record: it sends you to
    a dead URL. So the port is probed for IDENTITY, not merely for a listener.
    """
    try:
        rec = json.loads(_record().read_text(encoding="utf-8"))
    except Exception:
        rec = None
    if rec and identify(int(rec.get("port", 0))):
        return rec
    if rec:
        # Stale or pointing at a stranger. Drop the record; never signal the
        # pid it names -- that pid may belong to someone else now.
        _record().unlink(missing_ok=True)

    # The record is a cache, the port is the lock. A board with a lost record
    # is still the board: adopt it rather than starting a second one.
    for p in range(DEFAULT_PORT, DEFAULT_PORT + 12):
        found = identify(p)
        if found:
            adopted = {"port": p, "pid": found.get("pid", 0),
                       "started": time.time(), "adopted": True}
            try:
                _home().mkdir(parents=True, exist_ok=True)
                _record().write_text(json.dumps(adopted), encoding="utf-8")
            except OSError:
                pass
            return adopted
    return None


def claim(port: int) -> None:
    """Called by the board itself once it has bound. Records port and OWN pid."""
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    _record().write_text(json.dumps(
        {"port": port, "pid": os.getpid(), "started": time.time()}),
        encoding="utf-8")
    write_bookmark(port)


BOOKMARK = """<!doctype html><meta charset=utf-8><title>Mission board</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;margin:15vh auto;
max-width:30rem;padding:0 1.5rem;color:#16191B}
@media(prefers-color-scheme:dark){body{background:#0F1313;color:#E7ECE9}}
code{font-family:ui-monospace,Menlo,monospace;background:#8882;padding:.1rem .3rem;
border-radius:3px}a{color:inherit}</style>
<p id=m>Looking for the mission board…</p>
<script>
// A bookmark you keep. The port can move; this page re-checks every time it is
// opened, so the link is correct forever instead of correct until a restart.
//
// no-cors because this page is opened from file:// -- the origin is "null",
// so a normal fetch is refused before it ever reaches the board. An opaque
// response tells us nothing about the body, which is fine: the only question
// here is whether anything answered.
const URL_ = "http://127.0.0.1:__PORT__/";
fetch(URL_, {mode: "no-cors", cache: "no-store"})
  .then(() => location.replace(URL_))
  .catch(() => {
    document.getElementById("m").innerHTML =
      "The board is not running.<br><br>Start it with <code>mission board</code>" +
      " in a terminal — run it yourself and it prints a write code, which turns" +
      " on the accept and tick buttons.<br><br>Then reload this page.";
  });
</script>
"""


def write_bookmark(port: int) -> Path:
    """A file:// page that always points at the live board.

    Bookmark it once. It survives a port change, works with no session open,
    and says the board is down rather than sending you to a dead URL.
    """
    home = _home()
    p = home / "board.html"
    try:
        # mkdir first: this swallowed the OSError when the directory did not
        # exist yet and wrote nothing at all, silently. claim() happens to
        # mkdir beforehand, so the gap only showed when called on its own.
        home.mkdir(parents=True, exist_ok=True)
        p.write_text(BOOKMARK.replace("__PORT__", str(port)), encoding="utf-8")
    except OSError:
        pass
    return p


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
    """Can the SERVER bind here? Match its options, or the answer is wrong.

    HTTPServer sets allow_reuse_address, so it binds through TIME_WAIT. This
    probe did not, so for about a minute after a stop it reported 8976 busy and
    the board hopped to 8977 for no reason -- the "random port jump".
    """
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
