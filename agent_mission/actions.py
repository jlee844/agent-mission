"""Writes from the board, gated on a code only the person can have seen.

The board is a plain localhost server. A naive Accept button would be a POST
any local process could make -- including the agent, with one `curl` -- and the
Claude Code deny rules would never see it, because they match shell commands
and not HTTP. So the obvious version does not merely fail to help: it removes
the protection the tty gate and the deny rules provide.

What the agent cannot reach is the human's terminal. So:

  * writes are enabled ONLY when the board's own stdout is a tty, which means
    a person ran `mission board` themselves. The board that `mission init`
    starts in the background is read-only, always.
  * that board prints a short code to that terminal and keeps it in memory. It
    is never written to disk, never returned by any endpoint, and never
    appears in the log file a background board would write.
  * every write carries the code.

An agent can still get the code if the person pastes their terminal into the
chat. That is a person choosing to share it, which is the same shape as the
`AGENT_MISSION_I_AM_HUMAN` override: a decision, not an accident.
"""

from __future__ import annotations

import hmac
import secrets

from .store import MissionStore, root_for

ACTIONS = ("accept", "done", "remove", "note")


class Unauthorised(PermissionError):
    """Wrong code, or a read-only board."""


class Session:
    """The board's write capability. Absent = read-only."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        # 6 hex chars: short enough to retype, and the attack it defends
        # against is a process on this machine guessing, not a network.
        self.code = secrets.token_hex(3) if enabled else ""

    def check(self, given: str) -> None:
        if not self.enabled:
            raise Unauthorised("this board is read-only — run `mission board` "
                               "yourself in a terminal to enable writes")
        if not given or not hmac.compare_digest(given, self.code):
            raise Unauthorised("wrong code")


def apply(session: Session, code: str, action: str, sid: str,
          ids: list[str], text: str = "") -> dict:
    """Perform one board action. Raises rather than reporting failure inline."""
    session.check(code)
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    st = MissionStore(root_for(sid))
    if st.load() is None:
        raise ValueError(f"no mission for {sid}")

    if action == "note":
        if not text.strip():
            raise ValueError("empty note")
        st.observe("notes", text.strip(), by="human")
        return {"ok": True, "did": "note"}

    fn = {"accept": st.accept, "done": st.complete, "remove": st.remove}[action]
    done = []
    for i in ids:
        fn(i, by="human")
        done.append(i)
    return {"ok": True, "did": action, "ids": done}
