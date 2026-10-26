import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Goal, Snapshot } from '../types'

const PANE = 'mission'
const EMPTY: Snapshot = { goals: [], readAt: 0, error: '' }

const board = atom({ plugin: 'mission-pane', key: 'snapshot' } as const, EMPTY)

/** How many waiting lines one goal may contribute before the rest are counted. */
const PER_GOAL = 3

type Row = { id?: unknown; text?: unknown; label?: unknown; claim?: unknown; held_by?: unknown }
type Lanes = { ready?: Row[]; progress?: Row[]; review?: Row[]; waiting?: Row[] }

function rows(lane: Row[] | undefined): Row[] {
  return Array.isArray(lane) ? lane : []
}

/**
 * The row's label, AS THE BOARD COMPUTED IT.
 *
 * ⚠️ This used to derive its own -- cut at " — " then ". ", 68 characters --
 * while the CLI's `label_of` cut at a different set of delimiters and 52. Two
 * copies of one rule had diverged on the day the second was written, so the
 * pane and the board named the same item differently. The label now travels
 * in the snapshot and nothing here recomputes it. `text` is the fallback only
 * for an older board that predates the field.
 */
function label(r: Row): string {
  const id = typeof r.id === 'string' ? r.id : '????????'
  const name = typeof r.label === 'string' && r.label
    ? r.label
    : (typeof r.text === 'string' ? r.text.slice(0, 52) : '')
  return `${name}  ${id.slice(0, 8)}`
}

/**
 * Read the board through its OWN payload.
 *
 * `mission snapshot` prints the same `snapshot()` the web page consumes. The
 * alternative was parsing `queue`'s prose, which is written for a person and
 * gets reformatted whenever a person reads it badly, or folding the event log
 * a second time in TypeScript. A second fold is the defect this codebase has
 * hit repeatedly, and here it would be the worst instance: the pane would
 * disagree with the board about what is waiting and nothing would say which
 * was right.
 */
async function reload($: EngineInterface): Promise<void> {
  let out
  try {
    // ⚠️ Pass the session id EXPLICITLY. `attached` -- which goal is THIS
    // session's -- is computed by the child from CLAUDE_CODE_SESSION_ID, and
    // a child sees only the host process's environment, which need not carry
    // it. Read fresh on every reload: after /clear the process goes on under
    // a NEW id and no session.start fires, so an id cached at start would
    // keep pointing at a conversation that has ended.
    const sid = await $.session.id()
    out = await $.process.run(['mission', 'snapshot'], {
      timeoutMs: 15000,
      env: sid ? { CLAUDE_CODE_SESSION_ID: sid } : {},
    })
  } catch (err) {
    await update($, board, (s: Snapshot) => ({
      ...s,
      error: `could not run \`mission snapshot\`: ${String(err).slice(0, 120)}`,
    }))
    return
  }
  if (out.exitCode !== 0) {
    await update($, board, (s: Snapshot) => ({
      ...s,
      error: `mission snapshot exited ${out.exitCode}: ${(out.stderr || '').slice(0, 120)}`,
    }))
    return
  }
  let raw: unknown
  try {
    raw = JSON.parse(out.stdout)
  } catch {
    // A partial read is reported, never drawn as an empty board: "no goals"
    // and "I could not read the goals" are opposite claims.
    await update($, board, (s: Snapshot) => ({ ...s, error: 'snapshot was not JSON' }))
    return
  }
  const list = Array.isArray(raw) ? raw : []
  const goals: Goal[] = list.map(g => {
    const q: Lanes = (g && typeof g === 'object' && (g as any).queue) || {}
    const review = rows(q.review)
    const waiting = rows(q.waiting)
    return {
      id: String((g as any)?.id ?? ''),
      title: String((g as any)?.title ?? (g as any)?.id ?? ''),
      done: Number((g as any)?.done ?? 0),
      total: Number((g as any)?.total ?? 0),
      pendingAccept: Number((g as any)?.pending_accept ?? 0),
      review: review.length,
      progress: rows(q.progress).length,
      ready: rows(q.ready).length,
      needsYou: Boolean((g as any)?.needs_you),
      attached: Boolean((g as any)?.attached),
      procs: Number((g as any)?.procs ?? 0),
      // Review first: a claim awaiting a ruling is the thing this tool is for.
      // Ready work is the fallback, so the goal you are ON still says
      // something useful on a day when nothing awaits a ruling.
      waiting: [...review, ...waiting, ...rows(q.ready)].slice(0, PER_GOAL).map(label),
    }
  })
  const now = await $.clock.now()
  await update($, board, () => ({ goals, readAt: now, error: '' }))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'mission',
      description: 'Open the mission pane and re-read the board',
    })
    // Deliberately NOT an unconditional open. The first version opened the
    // pane on every session.start, so a pane about one goal appeared in every
    // window whether or not it had anything to say — "all sessions are
    // opening the mod". The board's own rule applies here: silence means on
    // track, so it opens itself only when the goal THIS session is attached
    // to is actually waiting on the human. `/mission` opens it any time.
    await reload($)
    const s = await read($, board)
    const mine = s.goals.find(g => g.attached)
    if (mine && mine.needsYou) {
      void $.ui.open({ id: PANE, title: 'Mission' })
    }

    return next(e)
  })

  on('command.run', { command: 'mission' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Mission' })
    await reload($)
    const s = await read($, board)
    const waiting = s.goals.filter(g => g.needsYou).length

    return {
      text: s.error
        ? `Mission pane opened, but the board could not be read: ${s.error}`
        : `Mission pane opened. ${waiting} of ${s.goals.length} goal(s) await you.`,
    }
  })

  // Edge-driven, not a timer. You want the queue at the moment you are about
  // to act, and this is the same cadence the signal hook already runs at, so
  // it adds one short read per prompt rather than a standing poll.
  on('prompt.submit', async ($, e, next) => {
    void reload($)

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const s = await read($, board)

    if (s.error) {
      return (
        <Box flexDirection="column">
          <Text color="red">Could not read the board.</Text>
          <Text dimColor>{s.error}</Text>
        </Box>
      )
    }
    if (s.readAt === 0) {
      return <Text dimColor>Reading the board…</Text>
    }
    if (s.goals.length === 0) {
      return <Text dimColor>No goals yet. `mission init` starts one.</Text>
    }

    // WHERE YOU ARE COMES FIRST, always, even with nothing waiting.
    // Sorting by "awaits you" alone buried the goal this session is working
    // in behind three others and read as a pane about somebody else's work.
    const here = s.goals.find(g => g.attached)
    const others = s.goals.filter(g => g.needsYou && !g.attached)
    const quiet = s.goals.filter(g => !g.needsYou && !g.attached).length
    const room = Math.max(6, (e.viewport?.rows ?? 24) - 4)
    const lines: RenderChildren[] = []
    let used = 0

    const put = (node: RenderChildren) => {
      if (used < room) {
        lines.push(node)
        used += 1
      }
    }
    const goal = (g: Goal) => {
      put(
        <Text bold>
          {g.title.slice(0, 54)} {g.done}/{g.total}
          {g.procs > 0 ? `  ${g.procs} live` : ''}
        </Text>,
      )
      if (g.review > 0) put(<Text color="yellow"> {g.review} claim(s) await your confirm</Text>)
      if (g.pendingAccept > 0) put(<Text color="yellow"> {g.pendingAccept} proposal(s) await your accept</Text>)
      for (const w of g.waiting) put(<Text dimColor> {w}</Text>)
    }

    put(<Text dimColor>THIS SESSION</Text>)
    if (!here) {
      // ⚠️ Offering only `attach` steers every session toward a goal that
      // already exists, which is how a project ran for weeks with no goal at
      // all. Same defect as the CLI's NOT ATTACHED message, fixed the same
      // way: attach first, a new goal as the fallback, and unattached stated
      // as a fine end state for a one-off.
      put(<Text dimColor> not on the board</Text>)
      put(<Text dimColor>   joins one: `mission attach &lt;name&gt;`</Text>)
      put(<Text dimColor>   or, if this project belongs there: `mission init --on &lt;slug&gt;`</Text>)
      put(<Text dimColor>   a one-off needs no goal</Text>)
    } else {
      goal(here)
      if (!here.needsYou) put(<Text color="green"> nothing waiting on you here</Text>)
    }
    if (others.length > 0) {
      put(<Text dimColor> </Text>)
      put(<Text dimColor>WAITING ON YOU ELSEWHERE</Text>)
      for (const g of others) goal(g)
    }

    return (
      <Box flexDirection="column">
        {lines}
        {quiet > 0 && <Text dimColor>+ {quiet} other goal(s), nothing waiting</Text>}
      </Box>
    )
  })
}
