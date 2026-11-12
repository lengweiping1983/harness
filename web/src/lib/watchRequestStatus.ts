import type { ResourceText } from '@/types'
import type { ApiResult } from './api'
import type { RequestTrace } from './requestTraces'

/** Metadata only, one request at a time, paused while the page is hidden. */
export function watchRequestStatus(
  read: (signal: AbortSignal) => Promise<ApiResult<RequestTrace>>,
  receive: (row: RequestTrace) => void,
  reportError: (message: ResourceText) => void,
  initialStatus: string,
  visibility = {
    hidden: () => document.hidden,
    subscribe: (listener: () => void) => {
      document.addEventListener('visibilitychange', listener)
      return () => document.removeEventListener('visibilitychange', listener)
    },
  },
) {
  let disposed = false
  let terminal = initialStatus !== 'started'
  let delay = 3000
  let timer: ReturnType<typeof setTimeout> | undefined
  let active: AbortController | undefined
  function clear() { clearTimeout(timer); timer = undefined }
  function schedule(ms: number) {
    clear()
    if (!disposed && !terminal && !visibility.hidden()) timer = setTimeout(() => { void poll() }, ms)
  }
  async function poll() {
    if (disposed || visibility.hidden() || active) return
    const controller = new AbortController()
    active = controller
    try {
      const result = await read(controller.signal)
      if (disposed || controller.signal.aborted) return
      if (result.ok) {
        terminal = result.data.status !== 'started'
        receive(result.data)
        reportError('')
      } else {
        reportError(result.copy ?? result.message)
        if (result.status === 404 || result.status === 403) terminal = true
      }
    } catch {
      if (!disposed && !controller.signal.aborted) reportError({key: 'ui.lib.watchRequestStatus.cc2cc93dc9f8'})
    } finally {
      active = undefined
      delay = Math.min(delay * 2, 30000)
      schedule(delay)
    }
  }
  const unsubscribe = visibility.subscribe(() => {
    if (visibility.hidden()) { clear(); active?.abort() }
    else { delay = 3000; schedule(0) }
  })
  schedule(delay)
  return {
    refresh() {
      if (disposed || active) return
      terminal = false
      delay = 3000
      schedule(0)
    },
    dispose() { disposed = true; clear(); active?.abort(); unsubscribe() },
  }
}
