"""A passcode YOU choose, so a lost terminal is not a lockout.

The board's write code was `secrets.token_hex(3)`, freshly minted per board and
kept only in memory. That is the strongest possible secret -- nothing on disk,
nothing on any endpoint -- and it has one failure mode that turned out to
matter more than the threat it prevents: the code exists in exactly one place,
a terminal, and terminals die. A board started from `.zshrc` in a window that
later closed goes on serving with its code unreadable by anyone, including the
person who owns it. That happened, and it left the board read-only-in-practice
with nineteen proposals waiting on it.

So the code may now be something you SET and reuse. It is stored the way a
password is stored -- scrypt over a random salt, never the text -- so reading
the file does not yield the passcode.

WHAT THIS DOES NOT CHANGE, and the reason is the whole design:

    A board is still writable ONLY when a person started it at a tty.

Knowing the passcode is not, and cannot be, a substitute for that. The agent
runs as you, so it can read this file -- and, more decisively, it can WRITE
one. If a passcode alone made a board writable, an agent could store a hash of
a passcode it chose, start its own board with no terminal anywhere, and post
whatever it liked. The hash protects the secret; it cannot protect the
question "is a human here", and only the tty answers that.

What the passcode buys is recovery and quiet:
  * a board restart no longer invalidates what the browser has stored, so you
    stop re-pasting a new six-hex string after every upgrade;
  * the code lives in your head or your password manager, not on a screen that
    can scroll away or a terminal that can close.

The KDF is scrypt where the interpreter has it and PBKDF2-HMAC-SHA256 where it
does not; see `_HAS_SCRYPT` for why that branch exists and what it costs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

# scrypt parameters. The realistic attack is OFFLINE -- an agent reads this
# file and grinds it -- so the cost is set to hurt that, while staying under
# ~100ms for the one check a click performs.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32

# `hashlib.scrypt` exists only when Python was built against OpenSSL. macOS
# ships its own `/usr/bin/python3` (3.9.6) linked against LibreSSL, where the
# attribute is ABSENT -- so this module raised AttributeError on the platform
# the tool primarily targets, and `mission passcode` could not run at all.
# CI is Ubuntu-only and stayed green, which is how it went unnoticed.
#
# pbkdf2_hmac is always present. It is weaker per unit of time than scrypt
# (no memory hardness, so a GPU or ASIC parallelises it far better), which is
# why scrypt stays the default wherever it exists and the fallback compensates
# with a high iteration count rather than pretending the two are equivalent.
_HAS_SCRYPT = hasattr(hashlib, "scrypt")

# OWASP's floor for PBKDF2-HMAC-SHA256 at the time of writing. Costs ~0.2s on
# the 3.9.6 interpreter this path exists for -- the same order as one scrypt
# call, which is the budget a single click has.
_PBKDF2_ROUNDS = 600_000

# Ceilings on the cost parameters READ BACK from passcode.json. The record is
# a plain file in the agent-writable mission home, and `verifier` took its
# word: a planted `{"rounds": 10_000_000_000}` makes the board hang on its
# own startup check, and scrypt's n/r/p do the same in memory before
# `maxmem` can refuse. Nobody steals anything by it; the board simply stops,
# which is a denial of service against the one surface the human writes from.
#
# Set an order of magnitude above the defaults so a future cost increase is
# not silently capped back down, and REFUSE rather than clamp: a record
# asking for something this far outside the range was not written by `set_on`,
# and quietly verifying it at a lower cost would be the wrong answer computed
# confidently.
_MAX_ROUNDS = 10_000_000
_MAX_N = 1 << 20
_MAX_R = 64
_MAX_P = 16

SCRYPT = "scrypt"
PBKDF2 = "pbkdf2_sha256"


def _algo() -> str:
    """The strongest KDF this interpreter actually has."""
    return SCRYPT if _HAS_SCRYPT else PBKDF2

# Short enough to type from memory, long enough that grinding the hash is not
# a weekend. The five-wrong lockout covers ONLINE guessing; this covers the
# case where the file itself has been read.
MIN_LEN = 8


def _home() -> Path:
    return Path(os.environ.get("AGENT_MISSION_HOME",
                               Path.home() / ".agent-mission"))


def path() -> Path:
    return _home() / "passcode.json"


def is_set() -> bool:
    return path().exists()


def _derive(text: str, salt: bytes, algo: str = None,
            n: int = _N, r: int = _R, p: int = _P,
            rounds: int = _PBKDF2_ROUNDS, dklen: int = _DKLEN) -> bytes:
    """Derive a key with the NAMED algorithm, not with whatever is available.

    Every parameter is passed in rather than read from the module, because a
    record written by one interpreter is verified by another: a stored record
    is the authority on how it was made, and the module constants are only the
    defaults for a NEW one. Reading the globals here would silently verify an
    old record with today's cost and fail every check.
    """
    algo = algo or _algo()
    pw = text.encode("utf-8")
    if algo == SCRYPT:
        if not _HAS_SCRYPT:
            raise ValueError("this interpreter has no scrypt")
        return hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=dklen)
    if algo == PBKDF2:
        return hashlib.pbkdf2_hmac("sha256", pw, salt, rounds, dklen=dklen)
    raise ValueError(f"unknown algo {algo!r}")


def save(text: str) -> None:
    """Store a passcode. Raises ValueError on anything too weak to store."""
    text = (text or "").strip()
    if len(text) < MIN_LEN:
        raise ValueError(f"at least {MIN_LEN} characters — this one is the "
                         f"only thing between an agent and the write buttons")
    salt = secrets.token_bytes(16)
    algo = _algo()
    rec = {"algo": algo, "salt": salt.hex(), "n": _N, "r": _R, "p": _P,
           "rounds": _PBKDF2_ROUNDS,
           "hash": _derive(text, salt, algo).hex()}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)      # not a boundary (same uid), but not sloppy
    except OSError:
        pass
    tmp.replace(p)


def clear() -> bool:
    p = path()
    if not p.exists():
        return False
    p.unlink()
    return True


def load() -> dict | None:
    """Read the stored record, or None.

    The board calls this ONCE, at startup, and holds the result. That is
    deliberate: re-reading per request would mean a file rewritten mid-session
    takes effect against a board a person already started, which hands an
    agent a way to change the lock on a door that is already open.
    """
    try:
        rec = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not all(k in rec for k in ("salt", "hash", "n", "r", "p")):
        return None
    # A record written before the fallback existed names no algo and was
    # necessarily made with scrypt -- defaulting to scrypt keeps it verifiable
    # rather than quietly rejecting a passcode that still works.
    rec.setdefault("algo", SCRYPT)
    return rec


def verifier(rec: dict):
    """A callable answering 'is this the passcode', in constant time."""
    want = bytes.fromhex(rec["hash"])
    salt = bytes.fromhex(rec["salt"])
    # THE RECORD decides the algorithm, never this interpreter. A passcode set
    # on a Homebrew build (scrypt) must still verify under macOS's system
    # python (no scrypt) -- and there it cannot, so `check` returns False and
    # says nothing rather than claiming a wrong passcode.
    algo = rec.get("algo", SCRYPT)

    def check(given: str) -> bool:
        if not given:
            return False
        try:
            n, r, pp = int(rec["n"]), int(rec["r"]), int(rec["p"])
            rounds = int(rec.get("rounds", _PBKDF2_ROUNDS))
        except (ValueError, TypeError, KeyError):
            return False
        # Read, then bound, then derive. Checking AFTER the derive would be
        # no check at all: the hang happens inside it.
        if not (0 < n <= _MAX_N and 0 < r <= _MAX_R and 0 < pp <= _MAX_P
                and 0 < rounds <= _MAX_ROUNDS):
            return False
        try:
            got = _derive(given, salt, algo, n=n, r=r, p=pp,
                          rounds=rounds, dklen=len(want))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got, want)

    return check


MAX_TRIES = 3


def set_on() -> str:
    """When the record was last written, as a date a person can recognise.

    Printed in the board's banner so a passcode file that changed when you did
    not change it is VISIBLE. An agent rewriting the lock is the hole this
    guards; a date is what makes the rewrite noticeable.
    """
    try:
        import datetime
        ts = path().stat().st_mtime
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def confirm_at_terminal(rec: dict, ask=None, tries: int = MAX_TRIES) -> bool:
    """Prove the person who STARTED this board knows the stored passcode.

    The hole this closes is the simple one the module docstring missed. The
    agent runs as you, so it can WRITE `passcode.json` -- a hash of a passcode
    it chose. The tty gate stops it starting its own writable board, but it
    does not stop it waiting: the next time a person types `mission board`,
    THEIR writable board loaded the agent's record, and the agent's passcode
    worked on it. The banner even said the browser "should already have it and
    you may not need to type anything".
    `verify` alone could not tell the two apart, because both are just a hash
    on disk. Asking once, in the terminal that already proved a person is
    there, is what binds the record to that person.
    """
    import getpass
    ask = ask or getpass.getpass
    check = verifier(rec)
    when = set_on()
    print(f"\n  this board uses the passcode you set"
          f"{' on ' + when if when else ''}.")
    for left in range(tries, 0, -1):
        try:
            given = ask("  board passcode: ")
        except (EOFError, KeyboardInterrupt):
            print("\n  no passcode given — starting READ-ONLY.\n")
            return False
        if check(given):
            return True
        print(f"  that is not it — {left - 1} attempt(s) left."
              if left > 1 else "  that is not it.")
    print("\n  starting READ-ONLY. If you did not set this passcode, an agent"
          "\n  may have written it: `mission passcode --clear`, then set"
          "\n  your own.\n")
    return False
