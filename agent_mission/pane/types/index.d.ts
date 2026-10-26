/** One goal, reduced to what a pane can show without scrolling. */
export type Goal = {
  id: string
  title: string
  done: number
  total: number
  /** Proposals awaiting the human's accept. */
  pendingAccept: number
  /** Claims awaiting the human's confirm — the lane the project exists for. */
  review: number
  /** Items a session holds right now. */
  progress: number
  ready: number
  /** The board's own judgement that something awaits the human. */
  needsYou: boolean
  /** True for the goal THIS session is attached to — where you are, which is
   *  not derivable from what is waiting. */
  attached: boolean
  procs: number
  /** The first few waiting items, newest judgement first, for the pane body. */
  waiting: string[]
}

export type Snapshot = {
  goals: Goal[]
  /** Epoch ms of the last successful read, 0 before the first. */
  readAt: number
  /** Why the last read failed, '' when it did not. */
  error: string
}

declare module 'claude-code' {
  interface PluginState {
    'mission-pane': { snapshot: Snapshot }
  }
}
