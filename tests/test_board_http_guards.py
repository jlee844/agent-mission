"""Item 5: only the board's own page may talk to the board.

Binding 127.0.0.1 is not an access control. Any web page in any open tab can
reach a loopback server, and a hostname that resolves to 127.0.0.1 makes that
page same-origin with it. Both halves were demonstrated against a live board
before this file existed.
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from agent_mission import board as B
from agent_mission.actions import MAX_WRONG, Session, Unauthorised


@pytest.fixture
def live_board(tmp_path, monkeypatch):
    """A real writable board on a real socket, so the headers are real."""
    from http.server import ThreadingHTTPServer

    monkeypatch.setenv("AGENT_MISSION_HOME", str(tmp_path))
    from agent_mission.store import MissionStore, root_for
    st = MissionStore(root_for("demo"))
    st.create("s1", str(tmp_path), "a goal nobody else should read",
              by="human")

    # `/data` otherwise walks every real Claude Code transcript on this
    # machine -- 124 s for eight requests. The guards under test are header
    # checks that run before any of that, so the scan is stubbed out.
    monkeypatch.setattr(B, "live", lambda: [])
    monkeypatch.setattr(B, "activity", lambda *a, **k: None)
    monkeypatch.setattr(B, "WRITES", Session(enabled=True))
    B.CACHE.invalidate("")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), B._H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _req(port, path="/", host=None, method="GET", body=None,
         ctype="application/json", origin=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Host", host or f"127.0.0.1:{port}")
    if data is not None:
        r.add_header("Content-Type", ctype)
    if origin:
        r.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_a_foreign_host_header_cannot_read_the_rows(live_board):
    """`GET /data` with `Host: attacker.example` returned every objective,
    cwd path and claim — a DNS-rebinding page's whole payload."""
    _, port = live_board
    code, body = _req(port, "/data", host="attacker.example")
    assert code == 421
    assert b"a goal nobody else should read" not in body


def test_the_boards_own_host_still_reads_the_rows(live_board):
    _, port = live_board
    code, body = _req(port, "/data")
    assert code == 200
    assert b"a goal nobody else should read" in body
    code, _ = _req(port, "/data", host=f"localhost:{port}")
    assert code == 200, "localhost is the same board"


def test_identity_is_also_host_checked(live_board):
    """It names the store's path on disk, so it is not a free endpoint."""
    _, port = live_board
    assert _req(port, "/api/identity", host="attacker.example")[0] == 421
    assert _req(port, "/api/identity")[0] == 200


def test_a_cross_origin_post_is_refused_and_spends_no_attempt(live_board):
    """A form POST from any page can send text/plain cross-site with NO
    preflight. Five of those locked the board until restart."""
    _, port = live_board
    code, _ = _req(port, "/", method="POST",
                   body={"code": "guess", "action": "accept",
                         "session": "demo", "ids": []},
                   ctype="text/plain", origin="https://evil.example")
    assert code == 403
    assert B.WRITES.wrong == 0, "a refused cross-origin POST must cost nothing"


def test_a_plain_text_post_is_refused_even_with_no_origin(live_board):
    """Requiring JSON is what forces a cross-site POST to preflight."""
    _, port = live_board
    code, _ = _req(port, "/", method="POST", body={"code": "x"},
                   ctype="text/plain")
    assert code == 403
    assert B.WRITES.wrong == 0


def test_the_pages_own_post_still_works_and_does_cost_an_attempt(live_board):
    """The guard must not make the real board unusable: a same-origin JSON
    POST with a wrong code is a genuine wrong guess."""
    _, port = live_board
    code, _ = _req(port, "/", method="POST",
                   body={"code": "wrong", "action": "accept",
                         "session": "demo", "ids": []},
                   origin=f"http://127.0.0.1:{port}")
    assert code == 403
    assert B.WRITES.wrong == 1


def test_concurrent_wrong_guesses_cannot_outrun_the_lockout():
    """With a saved passcode each check costs one KDF call, so N threads all
    passed `wrong >= MAX_WRONG` before any of them incremented."""
    calls = []
    gate = threading.Barrier(20)

    def slow_verify(given):
        calls.append(given)
        return False

    s = Session(enabled=True, verify=slow_verify)

    def guess():
        gate.wait()
        try:
            s.check("nope")
        except Unauthorised:
            pass

    threads = [threading.Thread(target=guess) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) <= MAX_WRONG, (
        f"{len(calls)} verifications performed for a {MAX_WRONG}-attempt "
        f"budget — the counter is not atomic")
    assert s.wrong >= MAX_WRONG


def test_the_right_code_still_resets_the_counter():
    s = Session(enabled=True)
    for _ in range(MAX_WRONG - 1):
        with pytest.raises(Unauthorised):
            s.check("wrong")
    assert s.wrong == MAX_WRONG - 1
    s.check(s.code)
    assert s.wrong == 0, "a correct code refunds every charged attempt"
