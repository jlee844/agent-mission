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
import re
import secrets
import threading

from .store import MissionStore, root_for

ACTIONS = ("accept", "done", "remove", "note", "setup", "ack",
           "archive", "sweep", "sweepall")


class Unauthorised(PermissionError):
    """Wrong code, or a read-only board."""


# A short code is retypeable; 24 bits is also only ~16.7M values. A local
# process can try them, and the deny rules do not help: they match shell
# commands like `mission accept`, not an HTTP POST from a python one-liner.
# On loopback that is hours, not years -- a realistic window for a board left
# running through a long session. So wrong guesses have to cost something.
MAX_WRONG = 5

# A mission name, as `init` mints them: lowercase, digits, hyphens. Anchored
# with fullmatch at the call site, so no separator, dot or NUL can appear.
_VALID_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class Session:
    """The board's write capability. Absent = read-only."""

    def __init__(self, enabled: bool, verify=None):
        self.enabled = enabled
        # ThreadingHTTPServer runs every request in its own thread, and
        # check-then-increment was neither atomic nor ordered. With a saved
        # passcode each check costs one scrypt/PBKDF2 call, so N parallel
        # guesses ALL pass the `wrong >= MAX_WRONG` test before any of them
        # increments -- the lockout counted 1 against 20 attempts. The lock
        # serialises the whole decision, and the increment happens BEFORE
        # the expensive verify so a concurrent caller sees the cost already
        # charged.
        self._lock = threading.Lock()
        # A passcode the person SET is checked by `verify` and never held here
        # in the clear; without one the board mints a throwaway. 6 hex chars:
        # short enough for a person to retype from a terminal.
        self.verify = verify
        self.code = "" if (verify or not enabled) else secrets.token_hex(3)
        self.wrong = 0

    @property
    def saved(self) -> bool:
        """True when writes are gated on the person's own passcode."""
        return self.verify is not None

    def check(self, given: str) -> None:
        if not self.enabled:
            raise Unauthorised("this board is read-only — run `mission board` "
                               "yourself in a terminal to enable writes")
        with self._lock:
            if self.wrong >= MAX_WRONG:
                raise Unauthorised(
                    f"locked after {MAX_WRONG} wrong codes — restart "
                    f"`mission board` "
                    + ("to try again" if self.saved else "for a new one"))
            # Charge the attempt first, refund on success. The other order is
            # what let parallel guesses run free.
            self.wrong += 1
            ok = (self.verify(given) if self.verify is not None
                  else bool(given) and hmac.compare_digest(given, self.code))
            if ok:
                self.wrong = 0
                return
            left = MAX_WRONG - self.wrong
            what = "passcode" if self.saved else "code"
            raise Unauthorised(f"wrong {what} ({left} attempt"
                               f"{'' if left == 1 else 's'} left)")


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

    # The board addresses cards by MISSION id -- missions/<id>/ -- and this
    # resolved only the pre-inversion session layout, so every Accept clicked
    # on a migrated mission answered "no mission". Third member of the same
    # family: doctor audited the abandoned twins, choices() forgot the legacy
    # stores, and now this. One rule everywhere: missions/ first, legacy as
    # the fallback.
    from . import missions as _M
    # The id comes from a POST body and was used as a PATH COMPONENT with no
    # validation, so `../../x` resolved outside the store. It needs the write
    # code and a parseable mission at the target, so the impact is low -- and
    # it is the first thing anyone looks for, which is reason enough.
    #
    # Two gates, because neither alone is enough: the shape (no separators,
    # no dots, so nothing can traverse) and MEMBERSHIP (it has to be a
    # mission this store actually lists, so a well-shaped name cannot reach
    # a directory that merely happens to parse).
    if not _VALID_ID.fullmatch(sid or ""):
        raise ValueError(f"not a mission name: {sid!r}")
    d = _M.missions_root() / sid
    legacy = root_for(sid)
    if not d.exists() and not legacy.exists():
        raise ValueError(f"no mission for {sid}")
    st = MissionStore(d if d.exists() else legacy)
    if st.load() is None:
        raise ValueError(f"no mission for {sid}")

    # The board is a DIFFERENT write path from the terminal, and the log could
    # not tell them apart: an accept clicked on the board recorded
    # {"by":"human","typed_by":"human"}, identical to one typed by hand. The
    # authority is the same -- a person ruled either way -- but "which surface
    # did this come through" is a question `mission why` should be able to
    # answer, and it is how a forged POST would be distinguished from a tty.
    st.context_via = "board"

    if action == "archive":
        st.archive(by="human")
        return {"ok": True, "did": "archive"}

    if action == "sweep":
        # C17b: confirm-all-backed. The client sends NO ids on purpose -- the
        # server recomputes each verdict and ticks only what the DISK fully
        # corroborates, so a tampered page cannot smuggle an unbacked row
        # into the sweep. Unbacked rows are exactly the ones that need the
        # human's eyes, so they never sweep; by=human because the code holder
        # IS the human -- the click is the judgement, arriving pre-evidenced.
        from .claims import verdict_for
        m = st.load()
        swept = []
        for i in m.suggested:
            if verdict_for(i.claimed_done, cwd=m.cwd or "",
                           accepted_at=i.accepted_at).get("ok"):
                st.complete(i.id, by="human")
                swept.append(i.id)
        return {"ok": True, "did": "sweep", "ids": swept,
                "skipped": len(m.suggested) - len(swept)}

    if action == "sweepall":
        # CONFIRM ALL, INCLUDING WHAT THE DISK CANNOT CORROBORATE.
        #
        # The tier model says a HUMAN confirms and the disk supplies evidence;
        # evidence informs the judgement, it does not own it. Refusing to let
        # the person tick their own unbacked rows would make the verifier the
        # authority, which is not the design. Most unbacked rows are not false
        # -- the commonest cause is a claim written as prose with no artifact
        # to check, which is a phrasing failure, not a lying agent.
        #
        # So it is allowed, and it is RECORDED. Every item swept without
        # evidence is named in one observed note, so `mission why` and the log
        # can always answer "was this confirmed on evidence or on trust?".
        # Silent equivalence between the two is the only outcome ruled out.
        from .claims import verdict_for
        m = st.load()
        backed, blind = [], []
        for i in m.suggested:
            (backed if verdict_for(i.claimed_done, cwd=m.cwd or "",
                                   accepted_at=i.accepted_at).get("ok")
             else blind).append(i)
        if blind:
            # Written BEFORE the ticks: if completing raises partway, the
            # record still says what was about to happen on trust alone.
            st.observe("notes", "confirmed without disk evidence ("
                       + str(len(blind)) + " item(s), by the person at the "
                       "board): " + ", ".join(i.id for i in blind), by="human")
        for i in backed + blind:
            st.complete(i.id, by="human")
        return {"ok": True, "did": "sweepall",
                "ids": [i.id for i in backed + blind],
                "backed": len(backed), "unevidenced": len(blind)}

    if action == "ack":
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
