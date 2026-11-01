import {useAppStore} from '@/store/app'
import {failureText} from '@/lib/resourceError'
import { t, useLocale } from '@/i18n'
import { useLayoutEffect, useRef, useState } from 'react'
import type { SteeringReceipt, SteeringStatus } from '@/lib/protocol.generated'
import { runtimeRequest } from '@/lib/http'
import { noticeText } from '@/lib/noticeText'
import type { ResourceText } from '@/types'
import { useDraftStore } from '@/store/drafts'
import { Button } from '@/components/ui/primitives'

export function SteeringDock({ sessionId, receipts, onRestored }: {
  sessionId: string; receipts: SteeringReceipt[]; onRestored: () => void
}) {
  useLocale()

  const [reading, setReading] = useState<string | null>(null)
  const [error, setError] = useState<ResourceText | null>(null)
  const alive = useRef(true)
  const scope = useRef(0)
  useLayoutEffect(() => {
    alive.current = true
    let owner = useAppStore.getState().activeSessionId
    const unsubscribe = useAppStore.subscribe(state => {
      if (state.activeSessionId !== owner) { owner = state.activeSessionId; scope.current++ }
    })
    return () => { alive.current = false; scope.current++; unsubscribe() }
  }, [])
  const unapplied = receipts.filter(receipt => receipt.status === 'unapplied')
  async function restore(requestId: string) {
    if (reading) return
    const operationScope = scope.current
    setReading(requestId); setError(null)
    try {
      const { receipt } = await runtimeRequest<SteeringStatus>('/sessions/' + sessionId + '/steering/' + encodeURIComponent(requestId))
      if (!alive.current) return
      const expected = unapplied.find(item => item.request_id === requestId)
      if (!receipt || !expected || receipt.request_id !== requestId || receipt.turn !== expected.turn
          || receipt.accepted_seq !== expected.accepted_seq || receipt.text_truncated !== false
          || typeof receipt.text !== 'string' || receipt.status !== 'unapplied') { setError({key: 'ui.components.chat.SteeringDock.d590a5909a83'}); return }
      const store = useDraftStore.getState(), draft = store.bySession[sessionId]
      if (draft?.text || draft?.attachments.length) { setError({key: 'ui.components.chat.SteeringDock.8679e3e31533'}); return }
      if (!store.restoreSubmission(sessionId, requestId)) store.setText(sessionId, receipt.text)
      if (operationScope === scope.current && useAppStore.getState().activeSessionId === sessionId) onRestored()
    } catch (failure) { if (alive.current) setError(failureText(failure)) }
    finally { if (alive.current) setReading(null) }
  }
  if (!unapplied.length) return null
  return <div role="alert" className="mb-2 space-y-2 rounded-xl border border-warn/30 px-3 py-2 text-xs">
    <p className="text-warn">{t("ui.components.chat.SteeringDock.ba8a49611c39", {p0: unapplied.length})}</p>
    {unapplied.map(receipt => <div key={receipt.request_id} data-steering-id={receipt.request_id} className="space-y-1">
      <p className="line-clamp-2 whitespace-pre-wrap break-words leading-5">{receipt.text}</p>
      {receipt.reason && <p className="text-muted">{receipt.reason}</p>}
      <Button size="xs" disabled={reading !== null} onClick={() => void restore(receipt.request_id)}>{reading === receipt.request_id ? t("ui.components.chat.SteeringDock.86b6d0d63062") : t("ui.components.chat.SteeringDock.42f3afc667d4")}</Button>
    </div>)}
    {error && <p className="text-danger">{noticeText(error)}</p>}
  </div>
}
