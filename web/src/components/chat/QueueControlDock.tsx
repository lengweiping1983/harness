import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ListOrdered, Pause, Play } from 'lucide-react'
import { changeQueueControl, readQueueControl, QueueControlUnknown } from '@/lib/queueControl'
import { useChatStore, useChat } from '@/store/chat'
import { useAppStore } from '@/store/app'
import { Button } from '@/components/ui/primitives'

/** Visible where messages are written and in the queue panel on compact screens. */
export function QueueControlDock({ sessionId, onToggleQueue, expanded }: { sessionId: string; onToggleQueue?: () => void; expanded?: boolean }) {
  useLocale()

  const chat = useChat(sessionId)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [unknown, setUnknown] = useState(false)
  const working = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  async function control(verify: boolean) {
    if (working.current) return
    working.current = true
    setBusy(true); setError('')
    try {
      const state = verify ? await readQueueControl(sessionId) : await changeQueueControl(sessionId, !chat.queuePaused)
      useChatStore.getState().apply(sessionId, { type: 'queue_state', paused: state.paused, pause_reason: state.pause_reason, updated_at: state.updated_at })
      if (alive.current) { setUnknown(false); setError('') }
    } catch (failure) {
      if (alive.current) {
        setError(failureText(failure))
        if (failure instanceof QueueControlUnknown) setUnknown(true)
      }
    } finally {
      working.current = false
      if (alive.current) setBusy(false)
    }
  }

  if (!chat.queuePaused && !chat.queued && !unknown && !error) return null
  return <div className={onToggleQueue ? "space-y-2 bg-raised/60 px-3 py-2" : "mb-2 space-y-2 rounded-xl border border-line bg-raised/60 px-3 py-2"} aria-label={t("ui.components.chat.QueueControlDock.3e27c3641b8e")}>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {chat.queuePaused ? <Pause className="h-3.5 w-3.5 shrink-0 text-warn" /> : <ListOrdered className="h-3.5 w-3.5 shrink-0 text-muted" />}
      <span className="min-w-0 flex-1 font-medium">{chat.queuePaused ? t("ui.components.chat.QueueControlDock.d0ca08d60455") : chat.queueWaitingReason === 'environment' ? t('ui.queue.environment.heading') : t("ui.components.chat.QueueControlDock.4f021f59121c")}{chat.queued > 0 ? t("ui.components.chat.QueueControlDock.05d7f9e34eb5", {p0: (chat.queued)}) : ''}</span>
      <Button size="xs" variant="ghost" aria-expanded={onToggleQueue ? expanded : undefined} onClick={onToggleQueue ?? (() => useAppStore.getState().setInspectorTab('queue'))}>{t(onToggleQueue && expanded ? 'ui.queue.collapse' : "ui.components.chat.QueueControlDock.f9c57473d6a0")}</Button>
      <Button size="xs" variant={chat.queuePaused ? 'primary' : 'ghost'} disabled={busy || chat.stopping || chat.connection !== 'open'} icon={chat.queuePaused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />} onClick={() => void control(unknown)}>{busy ? t("ui.components.chat.QueueControlDock.4799ce593fcd") : unknown ? t("ui.components.chat.QueueControlDock.1fb8b832b330") : chat.queuePaused ? t("ui.components.chat.QueueControlDock.c4bf814baf3d") : t("ui.components.chat.QueueControlDock.e90e4d4b3a66")}</Button>
    </div>
    {chat.queuePaused ? <p role="status" className="text-xs leading-5 text-muted">{chat.queuePauseReason === 'stopped' ? t("ui.components.chat.QueueControlDock.8d170f623eb4") : t("ui.components.chat.QueueControlDock.e27655ddbc19")}{t("ui.components.chat.QueueControlDock.c226e65e4933")}</p> : null}
    {!chat.queuePaused && chat.queueWaitingReason === 'environment' && <p role="status" className="text-xs leading-5 text-muted">{t('ui.queue.environment.description')}</p>}
    {error ? <p role="alert" className="break-words text-xs leading-5 text-danger">{noticeText(error)}</p> : null}
  </div>
}
