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

ACTIONS = ("accept", "done", "remove", "note", "setup", "ack")


class Unauthorised(PermissionError):
    """Wrong code, or a read-only board."""


# A short code is retypeable; 24 bits is also only ~16.7M values. A local
# process can try them, and the deny rules do not help: they match shell
# commands like `mission accept`, not an HTTP POST from a python one-liner.
# On loopback that is hours, not years -- a realistic window for a board left
# running through a long session. So wrong guesses have to cost something.
MAX_WRONG = 5


class Session:
    """The board's write capability. Absent = read-only."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        # 6 hex chars: short enough for a person to retype from a terminal.
        self.code = secrets.token_hex(3) if enabled else ""
        self.wrong = 0

    def check(self, given: str) -> None:
        if not self.enabled:
            raise Unauthorised("this board is read-only — run `mission board` "
                               "yourself in a terminal to enable writes")
        if self.wrong >= MAX_WRONG:
            raise Unauthorised(
                f"locked after {MAX_WRONG} wrong codes — restart `mission board` "
                "for a new one")
        if not given or not hmac.compare_digest(given, self.code):
            self.wrong += 1
            left = MAX_WRONG - self.wrong
            raise Unauthorised(f"wrong code ({left} attempt"
                               f"{'' if left == 1 else 's'} left)")
        self.wrong = 0


def apply(session: Session, code: str, action: str, sid: str,
          ids: list[str], text: str = "") -> dict:
    """Perform one board action. Raises rather than reporting failure inline."""
    session.check(code)
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if action == "setup":
        # SETUP TIER. Config work reaches a button because it is idempotent,
        # shown as a diff before it applies, and backed up -- not because it is
        # unimportant. Protected fields and judgement (set/accept/done/remove)
        # stay terminal-only however convenient a button would be.
        from . import setup_surfaces as S
        return S.install(text)

    st = MissionStore(root_for(sid))
    if st.load() is None:
        raise ValueError(f"no mission for {sid}")

    if action == "ack":
        st = MissionStore(root_for(sid))
        if st.load() is None:
            raise ValueError(f"no mission for {sid}")
        st.acknowledge(text, by="human")
        return {"ok": True, "did": "ack", "finding": text}

    if action == "note":
        if not text.strip():
            raise ValueError("empty note")
        st.observe("notes", text.strip(), by="human")
        return {"ok": True, "did": "note"}

    fn = {"accept": st.accept, "done": st.complete, "remove": st.remove}[action]
    # Per-id, like the CLI's _apply(): each call writes its own event
    # immediately, so raising partway through left a prefix already applied and
    # told the caller only "400". A bad id in a batch must not hide the ones
    # that worked.
    done, failed = [], []
    for i in ids:
        try:
            fn(i, by="human")
        except Exception as e:
            failed.append({"id": i, "why": type(e).__name__})
        else:
            done.append(i)
    return {"ok": not failed, "did": action, "ids": done, "failed": failed}
