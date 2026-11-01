import {useEffect, useRef} from 'react'
import {createCompletionObserver} from '@/lib/completionObserver'
import {listenForTaskNotificationOpen, loadSessionUnread, markSessionRead, notifySessionCompletion, sessionUnreadStorageKey} from './sessionNotifications'
import type {SessionCompletion} from '@/lib/protocol.generated'
import {create} from 'zustand'
import {api} from '@/lib/api'
import {useAppStore} from '@/store/app'
import {useChatStore} from '@/store/chat'

export const useSessionActivityStore = create<{running: string[] }>(() => ({running: []}))

let observeLive: ((row: SessionCompletion) => void) | undefined
export function observeSessionCompletion(sessionId: string, raw: Record<string, unknown>) {
  if (raw.type !== 'run/state' || raw.kind === 'context_compact' || raw.started_at == null ||
      typeof raw.id !== 'string' || !raw.id || typeof raw.status !== 'string' || !['completed', 'failed', 'interrupted'].includes(raw.status)) return
  observeLive?.({session_id: sessionId, run_id: raw.id, status: raw.status as SessionCompletion['status'], seq: 1})
}

/** 全局只保留通知所需的轻量观察；隐藏时暂停目录刷新。 */
export function useSessionActivity(conversationVisible = true) {
  const visible = useRef(conversationVisible)
  visible.current = conversationVisible
  const isVisible = (id: string) => visible.current && !document.hidden && document.hasFocus() && useAppStore.getState().activeSessionId === id
  const observer = useRef<ReturnType<typeof createCompletionObserver>>()
  if (!observer.current) observer.current = createCompletionObserver(row => {
    if (isVisible(row.session_id)) markSessionRead(row.session_id)
    else notifySessionCompletion(row)
  }, () => useAppStore.getState().toast({title: {key: 'ui.notifications.gap'}, tone: 'warn'}))
  useEffect(() => {
    const active = useAppStore.getState().activeSessionId
    if (active && isVisible(active)) markSessionRead(active)
  }, [conversationVisible])
  useEffect(() => {
    let disposed = false
    let reading = false
    let catalogRevision: number | undefined
    let timer: ReturnType<typeof setTimeout>
    let request: AbortController | undefined
    loadSessionUnread()
    const live = observer.current!.live
    const receiveLive = (row: SessionCompletion) => {if (!disposed) live(row)}
    observeLive = receiveLive
    const readVisible = () => {
      const id = useAppStore.getState().activeSessionId
      if (id && isVisible(id)) markSessionRead(id)
    }
    const unsubscribe = useAppStore.subscribe((state, previous) => {
      if (state.activeSessionId !== previous.activeSessionId) readVisible()
    })
    const unlisten = listenForTaskNotificationOpen()
    const storageChanged = (event: StorageEvent) => {if (event.key === sessionUnreadStorageKey) {loadSessionUnread(); readVisible()}}
    window.addEventListener('storage', storageChanged)
    window.addEventListener('focus', readVisible)
    document.addEventListener('visibilitychange', readVisible)
    readVisible()
    const refresh = async () => {
      if (disposed || reading) return
      clearTimeout(timer)
      reading = true
      request = new AbortController()
      try {
        const result = await api.sessionActivity(request.signal, observer.current!.cursor())
        if (!disposed && result.ok && Array.isArray(result.data.running)) {
          observer.current!.receive(result.data)
          useSessionActivityStore.setState({running: result.data.running})
          const revision = result.data.catalog_revision
          if (!document.hidden && typeof revision === 'number' && revision !== catalogRevision && useAppStore.getState().booted) {
            if (await useAppStore.getState().refreshSessions()) catalogRevision = revision
          }
        }
      } catch { /* 暂时断网时等待下一次快照。 */ }
      finally {reading = false}
      if (!disposed) timer = setTimeout(refresh, 2000)
    }
    void refresh()
    document.addEventListener('visibilitychange', refresh)
    return () => {
      disposed = true
      clearTimeout(timer)
      request?.abort()
      document.removeEventListener('visibilitychange', refresh)
      document.removeEventListener('visibilitychange', readVisible)
      window.removeEventListener('focus', readVisible)
      window.removeEventListener('storage', storageChanged)
      unsubscribe()
      unlisten()
      if (observeLive === receiveLive) observeLive = undefined
    }
  }, [])
}

export function useSessionRunning(sessionId: string | null) {
  const active = useAppStore(state => state.activeSessionId === sessionId)
  const live = useChatStore(state => {
    const chat = sessionId ? state.sessions[sessionId] : undefined
    return chat?.connection === 'open' ? chat.running : undefined
  })
  const snapshot = useSessionActivityStore(state => Boolean(sessionId && state.running.includes(sessionId)))
  return active && live !== undefined ? live : snapshot
}
