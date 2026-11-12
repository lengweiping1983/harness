import type { ResourceText } from '@/types'
import { failureText } from './resourceError'
export interface TerminalSize { cols: number; rows: number }

/** 串行同步最新尺寸；尺寸更新可重试，键盘输入不可按此方式重放。 */
export function terminalResizeQueue(write: (size: TerminalSize) => Promise<ResourceText | null>, report: (error: ResourceText | null) => void) {
  let desired: TerminalSize | null = null
  let confirmed: TerminalSize | null = null
  let active = false, writing = false, disposed = false
  let activation = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const same = (a: TerminalSize | null, b: TerminalSize | null) => a?.cols === b?.cols && a?.rows === b?.rows
  const cancel = () => { clearTimeout(timer); timer = undefined }
  const schedule = (delay: number) => {
    cancel()
    if (!active || writing || disposed || !desired || same(desired, confirmed)) return
    timer = setTimeout(() => { timer = undefined; void flush() }, delay)
  }
  async function flush() {
    if (!active || writing || disposed || !desired) return
    writing = true
    const size = desired
    const started = activation
    let error: ResourceText | null
    try { error = await write(size) } catch (cause) { error = failureText(cause) }
    writing = false
    if (disposed) return
    if (!active || started !== activation) { schedule(100); return }
    if (!error && started === activation) confirmed = size
    report(error)
    schedule(error && same(size, desired) ? 1500 : 100)
  }
  return {
    resize(cols: number, rows: number) {
      if (disposed || cols < 2 || rows < 1) return
      const next = {cols: Math.min(500, cols), rows: Math.min(300, rows)}
      if (same(next, desired)) return
      desired = next
      schedule(100)
    },
    setActive(value: boolean) {
      if (value && !active) { confirmed = null; activation++ }
      active = value
      if (active) schedule(100)
      else cancel()
    },
    dispose() { disposed = true; cancel() },
  }
}
