import type { ResourceText } from '@/types'
import { failureText } from './resourceError'
/** Ordered, bounded keyboard batches. An uncertain write is never replayed. */
export function terminalInputQueue(write: (text: string) => Promise<ResourceText | null>, fail: (message: ResourceText) => void) {
  let pending = '', active = false, stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  async function flush() {
    timer = undefined
    if (active || stopped || !pending) return
    active = true
    while (pending && !stopped) {
      // JS UTF-16 slices must not split a supplementary Unicode character.
      let count = Math.min(16000, pending.length)
      if (count < pending.length && /[\uD800-\uDBFF]/.test(pending[count - 1]!)) count--
      const text = pending.slice(0, count)
      pending = pending.slice(count)
      try {
        const error = await write(text)
        if (error) { stopped = true; pending = ''; fail(error) }
      } catch (error) {
        stopped = true; pending = ''; fail(failureText(error))
      }
    }
    active = false
  }
  return {
    push(text: string) {
      if (stopped) return
      if (pending.length + text.length > 64000) {
        stopped = true; pending = ''; fail({key: "ui.lib.terminalInput.f77b1174e9f3"}); return
      }
      pending += text
      if (!active && !timer) timer = setTimeout(() => void flush(), 16)
    },
    resume() { if (active) return false; stopped = false; return true },
    dispose() { stopped = true; pending = ''; if (timer) clearTimeout(timer) },
  }
}

/** Keep every input surface closed until observation confirms a live terminal. */
export function terminalInputDisabled(state: { pipe: boolean; connected: boolean; ended: boolean; blocked: boolean; controlling: boolean }) {
  return state.pipe || !state.connected || state.ended || state.blocked || state.controlling
}
