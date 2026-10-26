import { expect, test } from 'claude-code/testing'

// Everything beneath the plugin is played by these hooks: a call on `$` is
// answered `{ value }`, an event by its own result.
//
// ⚠️ The first version of this file was written without ever being run --
// with `vitest`, which cannot resolve 'claude-code/testing' -- and when it was
// finally run through `claude plugin test` it failed 2 of 2: it called
// session.start with no cwd and stood in for none of the engine. A test that
// has never executed is a claim, not a test.

const PANE = {
  plugin: 'mission-pane',
  component: 'Pane',
  requestId: 'mission',
  props: { title: 'Mission', isFocused: false, bodyColumns: 60,
           placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

const SURFACES = ['terminal', 'desktop'] as const

type G = Record<string, unknown>
const goal = (id: string, over: G = {}): G => ({
  id, title: id.replace(/-/g, ' '), done: 1, total: 4, pending_accept: 0,
  needs_you: false, attached: false, procs: 0,
  queue: { ready: [], progress: [], review: [], waiting: [] }, ...over,
})

// Stand in for the engine. `board` is what `mission snapshot` prints;
// `seen` records the env the pane handed the child, so a test can check it.
function engine(on: any, board: unknown, opts: { exit?: number; sid?: string } = {}) {
  const seen: { env?: Record<string, string> } = {}
  on('command.register', () => ({ value: { command: 'mission' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: opts.sid ?? 'sess-here' }))
  on('clock.now', () => ({ value: 1000 }))
  on('process.run', (_: unknown, e: any) => {
    seen.env = e?.init?.env ?? e?.env
    return { value: { exitCode: opts.exit ?? 0,
                      stdout: typeof board === 'string' ? board : JSON.stringify(board),
                      stderr: opts.exit ? 'boom' : '' } }
  })
  return seen
}

async function texts($: any, surface: (typeof SURFACES)[number]) {
  const ui = await $.ui.mount({ ...PANE, surface })
  const all = (await ui.findAll({ type: 'Text' })).map((t: any) => t.text)
  await ui.unmount()
  return all as string[]
}

const start = ($: any) =>
  $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

test('ON the board: this session\'s goal comes first, before others', async ($, on) => {
  engine(on, [
    goal('other-goal', { needs_you: true, pending_accept: 2 }),
    goal('my-goal', { attached: true }),
  ])
  await start($)
  for (const s of SURFACES) {
    const t = await texts($, s)
    const here = t.findIndex(x => /THIS SESSION/.test(x))
    const mine = t.findIndex(x => /my goal/.test(x))
    const else_ = t.findIndex(x => /WAITING ON YOU ELSEWHERE/.test(x))
    expect(here).toBeGreaterThan(-1)
    expect(mine).toBeGreaterThan(here)
    // Where you are comes first even when something ELSE needs you.
    expect(else_).toBeGreaterThan(mine)
    expect(t.some(x => /not on the board/.test(x))).toBe(false)
  }
})

test('NOT on the board: offers joining AND creating, and allows neither', async ($, on) => {
  engine(on, [goal('some-goal'), goal('another-goal')])
  await start($)
  for (const s of SURFACES) {
    const t = await texts($, s).then(a => a.join('\n'))
    expect(t).toMatch(/not on the board/)
    expect(t).toMatch(/mission attach/)
    // The defect: offering only `attach` steered every session into a goal
    // that already existed, so a project ran for weeks unlisted.
    expect(t).toMatch(/mission init --on/)
    expect(t).toMatch(/one-off needs no goal/)
    expect(t.indexOf('mission attach')).toBeLessThan(t.indexOf('mission init'))
  }
})

test('the session id is handed to the child explicitly', async ($, on) => {
  // The child computes `attached` from CLAUDE_CODE_SESSION_ID and sees only
  // the host's environment, which need not carry it.
  const seen = engine(on, [goal('g', { attached: true })], { sid: 'sess-xyz' })
  await start($)
  expect(seen.env?.CLAUDE_CODE_SESSION_ID).toBe('sess-xyz')
})

test('an unreadable board says so rather than drawing an empty one', async ($, on) => {
  // "No goals" and "I could not read the goals" are opposite claims.
  engine(on, '', { exit: 1 })
  await start($)
  for (const s of SURFACES) {
    const t = (await texts($, s)).join('\n')
    expect(t).toMatch(/Could not read the board/)
    expect(t).not.toMatch(/No goals yet/)
    expect(t).not.toMatch(/nothing waiting/)
  }
})

test('a row is named by the board\'s label, never re-derived here', async ($, on) => {
  engine(on, [goal('g', { attached: true, queue: {
    ready: [{ id: 'abcd1234', text: 'Mode-switch routing — the long why',
              label: 'BOARD LABEL' }],
    progress: [], review: [], waiting: [] } })])
  await start($)
  const t = (await texts($, 'desktop')).join('\n')
  expect(t).toMatch(/BOARD LABEL/)
  expect(t).not.toMatch(/the long why/)
})

test('/mission is registered and reports what awaits you', async ($, on) => {
  engine(on, [goal('a', { needs_you: true }), goal('b')])
  await start($)
  const said = await $.command.run({ command: 'mission', args: '' })
  expect(JSON.stringify(said)).toMatch(/1 of 2 goal/)
})
