# Docs truth pass — do the docs match the code?

Item `0d65f6ef`. Audited 2026-10-12 against the working tree at HEAD
`7388f2c` (== `origin/main`). Every claim below was checked by reading the
code or by `git show HEAD:<file>`, not from memory.

## Fixed

| # | Where | Was | Now |
|---|---|---|---|
| 1 | `docs/adversarial-testing.md:4` | "two of the three" attacks succeeded | "four of the five" — the file itself documents five rounds, and README:232 and SECURITY.md:80 already said four of five. The header contradicted its own body. |
| 2 | `docs/SECURITY.md` §"Which tier a command belongs in" | `set, accept, done, remove, add` are terminal-only, "No button, however convenient" | Terminal-only is the OBJECTIVE and its criteria, plus starting a WRITABLE board. The `ACTIONS` tuple in `actions.py` has had accept / decline / confirm / sweep / notes buttons for weeks, so the old text was false on its face — and it is the sentence that had been sending the maintainer to a terminal for work the board already does. |
| 3 | `agent_mission/board.py:1136` (UI hint) | "The agent never sees this, which is why these buttons are yours." | "Printed only in the terminal that started this board." |
| 4 | `agent_mission/board.py:2189` (terminal print) | "only this terminal has it, which is why the agent cannot use these buttons" | "...this terminal is the only place it appears. A speed bump, not a wall: paste this terminal into a chat and you have shared it." |
| 5 | `career/outputs/walkthroughs/agent-mission.md` header | "Almost everything after commit 5dacb47 is uncommitted" | 5dacb47 and 7388f2c are both PUSHED; names the four things that are still local. |

3 and 4 are the "agents cannot forge approval" family this item exists to
remove. SECURITY.md has always said the code is reachable if a person pastes
their terminal into a chat; the UI and the startup banner asserted the
opposite, which is the worse place to be wrong because it is what a user
actually reads.

## Checked and already correct — no change

- **Test count.** No doc hardcodes one. Suite is 676 passed / 2 skipped on
  system Python 3.9.6. (`blindspot` has a test forbidding a hardcoded count;
  this repo simply never added one.)
- **`watch` / `dispatch`.** README:154 documents `watch`, and `cmd_watch` is
  absent at HEAD — but the README line is ALSO uncommitted (`git show
  HEAD:README.md` has no `watch` row), so the pushed README does not document
  a command the pushed code lacks. Doc and code are inconsistent together,
  which is the harmless direction. `dispatch` is committed and documented.
- **`docs/SECURITY.md:165**, "not tamper-proof against an agent that sets out
  to forge the record" — already the honest phrasing.

## Still true, and still worth reading as limits

- The tty gate is a speed bump. Four of five attack rounds got through.
- **The stamp is evadable too**, which is newer than SECURITY.md's framing: a
  FORKING shell under a pty (`sh -c "writer; true"`, or any pipeline) leaves
  the writer as a child, so `sid != pid` and nothing is stamped. A
  single-command `sh -c` execs and IS stamped — so testing the obvious repro
  gives a false pass. Filed as proposal `08ab21de`; not fixed here.
- "The named file exists and changed after you agreed" is evidence, not
  proof. Two holes found by a second reader on 2026-10-11: a directory under
  the goal went green on an unrelated log file, and `(/var/log/system.log)`
  still goes green because something under `/var/log` changes every minute.
  The evidence rule is proposal `4ec20b29`; the superseding design (declared
  evidence roots + blob hash at accept) is in the goal's decision log.

## Not audited

`DESIGN.md` was not read line by line this pass — the item named it, and I
checked only that it is not contradicted by the five fixes above. A full
DESIGN.md pass is still outstanding.
