import {useLayoutEffect, useRef, useState} from 'react'
import {useAppStore} from '@/store/app'

/** 已受理的新对话创建仍完成，面板打开只属于原挂载实例。 */
export function useSessionPanelOpener(activeSessionId: string | null) {
  const mounted = useRef(false)
  const [request, setRequest] = useState<{sessionId: string; serial: number} | null>(null)
  useLayoutEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  useLayoutEffect(() => setRequest(current => current?.sessionId === activeSessionId ? current : null), [activeSessionId])
  const open = async () => {
    const origin = useAppStore.getState().newSessionRevision
    if (!activeSessionId && !await useAppStore.getState().materializeSession()) return
    if (!mounted.current) return
    const latest = useAppStore.getState()
    const sessionId = activeSessionId ?? latest.activeSessionId
    if (!sessionId || latest.activeSessionId !== sessionId || !activeSessionId && latest.newSessionRevision !== origin) return
    setRequest(current => ({sessionId, serial: (current?.serial ?? 0) + 1}))
  }
  return {request, open, close: () => setRequest(null)}
}
