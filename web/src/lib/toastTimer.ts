export const toastDuration = (toast: { duration?: number; tone?: string }) => toast.duration ?? (toast.tone === 'danger' ? 8000 : 4000)

/** One timer per notification; pausing preserves its remaining reading time. */
export function createToastTimer(duration: number, expire: () => void) {
  let remaining = Math.max(0, duration)
  let timer: ReturnType<typeof setTimeout> | null = null
  let started = 0
  let disposed = false
  const pause = () => {
    if (timer === null) return
    clearTimeout(timer)
    timer = null
    remaining = Math.max(0, remaining - (Date.now() - started))
  }
  return {
    pause,
    resume: () => {
      if (disposed || timer !== null) return
      started = Date.now()
      timer = setTimeout(() => { timer = null; disposed = true; expire() }, remaining)
    },
    cancel: () => { pause(); disposed = true },
  }
}
