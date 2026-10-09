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

**3. The harness denies.** `mission setup` writes deny rules into
`~/.claude/settings.json` covering the write commands, the two commands that
hand out write power (`board`, `passcode`), all three spellings of the CLI
(`mission …`, `python -m agent_mission …`, `python3 -m …`), and `script`.
Claude Code blocks those before this code runs. This is the strongest of the
three layers — it stopped the author's own agent mid-session.

**It is not complete, and this document used to say it was.** The rules are
prefix matches on a command string, so they cover the spellings named above
and not every spelling that exists: a copied binary under another name, a
shell function, or an interpreter invoked by path all route around them. Round
4 in [adversarial-testing.md](adversarial-testing.md) is exactly this.

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

So writes exist only when the board's own stdout is a **tty** — and a tty
means a person *probably* started it, not that one did. `script(1)` hands any
process a tty, and a double fork plus `pty.spawn` does it with no `script` at
all; §"What a tty does not prove" below is the honest version. That board mints
a six-character code, keeps it in memory, prints it to that terminal, and never
writes it to disk or returns it from any endpoint.
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

## What a tty does not prove

`isatty()` answers "is there a terminal", never "is there a person". Five
rounds of attacks are written up in
[adversarial-testing.md](adversarial-testing.md); **four of them got through**,
and this section exists because this document used to describe none of it while
another document claimed it did.

What is checked, and what each check is worth:

| Layer | Stops | Does not stop |
|---|---|---|
| tty required for writes | an agent's ordinary non-tty shell | `script -q /dev/null …`, or any pty |
| parent-process walk (`whoami.py`) | a pty opened directly under the agent | a process that orphans itself first — a double fork, or a backgrounded subshell that exits. **`setsid` and `nohup` do NOT reparent**; only an orphaning does |
| agent environment markers | forgetting to clear the environment | `env -u` of the variables, which is one flag |
| deny rules | `mission set`, `python -m agent_mission`, `script` by name | `python3 some_file.py`, an interpreter called by path, `open x.command` |
| passcode (if set) | a code read off the board's own stdout | an agent that rewrites `passcode.json` and then types its own passcode |

**The stamp used to share the gate's detector**, so the bypass that defeated
one erased the record of the other. `provenance` now also records `orphaned`
and `session_leader` / `tty_without_shell` — the last of which is
`os.getsid(0) == os.getpid()`, measured rather than reasoned about.
⚠️ The first version of this stamp used `getppid() <= 1` and MISSED the very
bypass it was written for: the writing process is a child of the orphan, not
the orphan. A second reader re-ran the attack and measured it; the claim that
"the bypass now leaves a trace" was false in four documents for several hours,
and the unit test passed throughout because it mocked `getppid` and so proved
my model instead of the code. There is now an end-to-end test that performs
the attack (`tests/test_round5_endtoend.py`) and fails if the write is
unmarked. It is not a gate: a person's own `nohup … &` looks the
same, so the log states it and a human reads it.

**What would make this a boundary** is a separate process the agent cannot
write as — a daemon owned by another user, or a confirmation on a device the
agent has no path to. Nothing here is that, and nothing here should be
described as that.

## The board's Setup panel, and what it costs

The board a person starts shows the same surfaces as `mission setup --check`
— six of them now (slash command, deny rules, statusline, re-anchor hook,
attention hook, claim hook) — with an Install button each, behind the same
write code. Both front
ends call the same functions — if the board had its own copy it would drift
from what the terminal does, and you would have two answers to "is this
installed".

Two honest costs:

**The code amortizes the terminal; it does not eliminate it.** You still start
the board yourself to get the code. That is the design, not an oversight.

**Widening the board's write surface to `settings.json` makes the code the only
barrier for that file.** Mitigated by the MINTED code never touching disk (a
passcode you set does: `~/.agent-mission/passcode.json`, a salted hash the
agent can read and rewrite, which is why the board prints the date it was last
written) — and, more
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

So the guarantee is precisely this: **an agent cannot rewrite your goal
accidentally or in passing, and a deliberate rewrite through a terminal it
manufactured is marked on the event.** It is NOT that the interfaces cannot be
driven — they can, and the repro is in
[adversarial-testing.md](adversarial-testing.md) round 5. It is not
tamper-proof against an agent that sets out to forge the record. Anyone relying
on this for more than that should read the log themselves.

**A corrupt log degrades rather than dies.** A line that will not parse is
skipped and counted; one unreadable session loses its card instead of taking the
board down. This was not true until an outside review found it.

**Everything is local.** The board binds `127.0.0.1`. Transcripts are read from
`~/.claude/projects` and never leave the machine.

## Claims-done, and what a disk verdict does not prove

C17 lets an agent suggest an item is finished, carrying a claim the verifier
checks against the filesystem. The honest limits:

- **Disk corroboration is evidence, not proof.** A claim can name an artifact
  that exists and still not satisfy the criterion — a file can be present and
  wrong. The grey verdict tells you the agent's story is *consistent with*
  the disk, never that the work is right.
- **The sweep button is a convenience over your judgment, not a substitute.**
  "Confirm all backed" ticks only rows whose every named artifact the server
  itself re-verified — the page's opinion is not trusted — and unbacked rows
  never sweep, because those are precisely the ones that need your eyes.
- **No auto-confirm, ever. No timer promotes a suggestion.** `done` has one
  writer, and the counter's meaning — *the human assessed this* — is the
  product. A suggestion that ages is still a suggestion.
- **The read-only board serves no confirm route.** The write endpoint refuses
  before any action is parsed, so this is the absence of an endpoint, not the
  absence of a button (the C10c rule).
