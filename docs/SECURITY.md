# Security and the honest limits

What is enforced, how, and — more importantly — what is not.

## Three layers

**1. The store refuses.** `set_protected`, `accept`, `complete` and `remove`
raise unless the caller passes `by="human"`. There is no agent path through the
library. This is tested against every protected field, not spot-checked.

**2. The CLI requires a terminal.** The store's refusal was advisory for months,
because the CLI passed `by="human"` unconditionally — it could not tell who was
typing. In a test, a subagent handed the CLI **rewrote a protected objective on
its first try**, and `mission why` recorded the change as the human's.

So `set`, `accept`, `done`, `remove` and `add` now require a controlling
terminal. An agent's shell has none; a person typing in one does.

**3. The harness denies.** `mission setup` writes five deny rules into
`~/.claude/settings.json`. Claude Code blocks those commands before this code
runs, so there is nothing left inside the tool to talk past. This is the layer
that actually holds — it stopped the author's own agent mid-session.

## What `typed_by` is for

`init --from-file` and `delegate` are agent-runnable and *do* write protected
fields — the documented flow is the agent transcribing an interview. Recording
that as plain `human` made an agent-authored objective indistinguishable from a
typed one.

Every event now carries **`by`** (whose field it is) and **`typed_by`** (who ran
the command). `mission why` prints both, and `mission show` warns on any mission
an agent transcribed.

## The board's write code

Buttons on the board would be a POST any local process can make — including the
agent's shell with one `curl`, which the deny rules never see because they match
shell commands and not HTTP.

So writes exist only when the board's own stdout is a **tty**, meaning a person
started it. That board mints a six-character code, keeps it in memory, prints it
to that terminal, and never writes it to disk or returns it from any endpoint.
The background board that `mission init` spawns is read-only and has no code.

Six hex characters is 16.7 million values, and on loopback a full sweep is hours
rather than years — so **five wrong codes lock writes** until the board is
restarted.

## Which tier a command belongs in

A rule, not a list, so the next command has an obvious home:

**Terminal only** — anything that writes a protected field or exercises
judgement: `set`, `accept`, `done`, `remove`, `add`. No button, however
convenient, because the point of the gate is that convenience does not move it.

**Setup tier — may reach a button** — configuration that is *idempotent*, shown
as a *diff before it applies*, and *backed up*. Installing the statusline is
recoverable and repeatable; ticking an item is neither.

Those three properties are the whole test. A future command that has them can
have a button; one that does not, cannot.

## The board's Setup panel, and what it costs

The board a person starts shows the same five surfaces as `mission setup
--check`, with an Install button each, behind the same write code. Both front
ends call the same functions — if the board had its own copy it would drift
from what the terminal does, and you would have two answers to "is this
installed".

Two honest costs:

**The code amortizes the terminal; it does not eliminate it.** You still start
the board yourself to get the code. That is the design, not an oversight.

**Widening the board's write surface to `settings.json` makes the code the only
barrier for that file.** Mitigated by the code never touching disk — and, more
importantly, by the read-only board serving **no setup route at all**. Not a
hidden button: `GET /api/setup` returns 404 and every POST returns 403 on a
board that was started in the background. Hiding the UI while leaving the
endpoint live is the classic version of this bug, and a test demands the
route's absence rather than the button's.

## The limits, stated plainly

**The `AGENT_MISSION_I_AM_HUMAN=1` override exists.** An agent can set it. It
turns impersonation from the default outcome into a deliberate lie, which is a
real change in behaviour — an adversarial subagent given an impossible goal hit
the refusal three times, was *told* the bypass in the error message, and did not
use it. That is evidence about ordinary behaviour, not a security result.

**`init` is not gated, and that is a real edge.** The documented flow is an
agent transcribing your interview, so `init` stays runnable by an agent — it
records `typed_by: agent` and `mission show` warns. What it must never do is
destroy a plan, and on 2026-08-19 it did: a session working in another
directory appended a second `created` event to a live mission and 52 events
went invisible. The fold now ignores a duplicate `created`; starting over
requires an explicit `discarded` event that only a human can write.

**The event log is a plain file.** An agent with shell access can append
`{"kind":"set","by":"human","field":"objective",...}` directly to
`events.jsonl`, and every command will believe it. There is no signature and no
hash chain today.

So the guarantee is precisely this: **an agent cannot rewrite your goal through
this tool's interfaces, and cannot do it accidentally or in passing.** It is not
tamper-proof against an agent that sets out to forge the record. Anyone relying
on this for more than that should read the log themselves.

**A corrupt log degrades rather than dies.** A line that will not parse is
skipped and counted; one unreadable session loses its card instead of taking the
board down. This was not true until an outside review found it.

**Everything is local.** The board binds `127.0.0.1`. Transcripts are read from
`~/.claude/projects` and never leave the machine.
