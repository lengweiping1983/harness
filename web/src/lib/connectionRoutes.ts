import type { ResourceText } from '@/types'
import { failureText } from './resourceError'
import { useEffect, useState } from 'react'
import { runtimeRequest } from './http'

export type RouteKey = { id: string; mask: string; available: boolean; current: boolean; in_use: boolean; value?: string }
export type RouteModel = { id: string; model: string; base_url: string; enabled: boolean; context_window?: number | null; max_output_tokens?: number | null; capabilities?: { context_window?: number | null; max_output_tokens?: number | null; confirmed?: boolean; sources?: string[] }; keys: RouteKey[] }
export type Routing = { models: RouteModel[]; current: { model_id: string; model: string; key_id: string } | null; cycle: number; retry_in_seconds?: number; state_save_failed?: boolean; error?: ResourceText; retiring?: Array<{ model: string; mask: string }> }
let latestRouting: Routing | null = null
const listeners = new Set<() => void>()
let revision = 0
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
function publish(data: Routing) {
  latestRouting = data
  listeners.forEach(listener => listener())
}
function refresh() {
  clearTimeout(timer)
  if (document.hidden || !listeners.size) return
  controller?.abort()
  controller = new AbortController()
  const current = ++revision
  void runtimeRequest<Routing>('/connection', 'GET', undefined, { signal: controller.signal }).then(data => {
    if (current === revision) publish(data)
  }).catch((failure: Error) => {
    if (current === revision) publish({ ...(latestRouting ?? { models: [], current: null, cycle: 1 }), error: failureText(failure) })
  }).finally(() => {
    if (current === revision && listeners.size && !document.hidden) timer = setTimeout(refresh, 2000)
  })
}
function visibilityChanged() {
  if (document.hidden) { ++revision; controller?.abort(); clearTimeout(timer) }
  else refresh()
}
export function useConnectionRoutes() {
  const [routing, setRouting] = useState<Routing | null>(latestRouting)
  useEffect(() => {
    const listener = () => setRouting(latestRouting)
    listeners.add(listener)
    listener()
    if (listeners.size === 1) {
      document.addEventListener('visibilitychange', visibilityChanged)
      window.addEventListener('connection-routes-changed', refresh)
      refresh()
    }
    return () => {
      listeners.delete(listener)
      if (!listeners.size) {
        ++revision; controller?.abort(); clearTimeout(timer)
        document.removeEventListener('visibilitychange', visibilityChanged)
        window.removeEventListener('connection-routes-changed', refresh)
      }
    }
  }, [])
  return routing
}
export function connectionChanged(data?: Routing) {
  ++revision; controller?.abort(); clearTimeout(timer)
  if (data) publish(data)
  window.dispatchEvent(new Event('connection-routes-changed'))
}
