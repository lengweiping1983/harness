import {collaborationFeedbackMessage, type CollaborationFeedback} from '@/lib/collaborationError'
import {ComposerDock, ComposerCard, ComposerEditor, ComposerToolbar, ComposerSendButton} from '../chat/ComposerSurface'
import {useComposerEditor} from '@/hooks/useComposerEditor'
import {useComposerKeyboard} from '@/hooks/useComposerKeyboard'
import {SharedQueueItem} from './SharedQueueItem'
import {useCallback, useEffect, useRef, useState} from 'react'
import {Button} from '@/components/ui/primitives'
import {collaboration} from '@/lib/collaboration'
import {requestKey} from '@/lib/sharedAccess'
import type {QueueEntry} from '@/lib/protocol.generated'
import {useDraftStore} from '@/store/drafts'
import {t} from '@/i18n'
import {usePageVisible} from '@/hooks/usePageVisible'

export function SharedTaskInput({sessionId, subject, initialContribute, onCapabilities}: {sessionId: string; subject: string; initialContribute: boolean; onCapabilities?: (capabilities: string[]) => void}) {
  const visible = usePageVisible()
  const scope = JSON.stringify(['shared', sessionId, subject])
  const text = useDraftStore(state => state.bySession[scope]?.text ?? '')
  const pending = useDraftStore(state => state.submissions[scope]?.find(row => row.status === 'sending' || row.status === 'unknown'))
  const [canContribute, setCanContribute] = useState(initialContribute)
  const [queue, setQueue] = useState<QueueEntry[]>([])
  const [busy, setBusy] = useState(false)
  const [receiptMissing, setReceiptMissing] = useState(false)
  const [failure, setFailure] = useState<CollaborationFeedback>(null)
  const alive = useRef(true)
  const lock = useRef(false)
  const refreshGeneration = useRef(0)
  const refreshRead = useRef<AbortController | null>(null)
  const {taRef, dockRef, wrapRef} = useComposerEditor(text, scope, canContribute)
  const refresh = useCallback(async () => {
    const ticket = ++refreshGeneration.current
    refreshRead.current?.abort()
    if (!alive.current || document.hidden) return
    const controller = new AbortController()
    refreshRead.current = controller
    try {
      const [state, tasks] = await Promise.all([collaboration.state(sessionId, controller.signal), collaboration.queue(sessionId, controller.signal)])
      if (!alive.current || controller.signal.aborted || document.hidden || ticket !== refreshGeneration.current) return
      if (state.ok) {
        const capabilities = state.data.access.members[subject] ?? []
        setCanContribute(capabilities.includes('contribute'))
        onCapabilities?.(capabilities)
      } else if (state.status === 401 || state.status === 403) {setCanContribute(false); onCapabilities?.([])}
      if (tasks.ok) setQueue(tasks.data.runs)
      else setFailure(tasks)
    } finally {
      if (refreshRead.current === controller) refreshRead.current = null
    }
  }, [sessionId, subject, onCapabilities])
  const settle = useCallback((key: string, submittedText: string) => {
    const drafts = useDraftStore.getState()
    drafts.settleSubmission(scope, key)
    if (drafts.bySession[scope]?.text === submittedText) drafts.setText(scope, '')
    if (alive.current) setFailure(null)
  }, [scope])
  const check = useCallback(async (key: string, submittedText: string, signal?: AbortSignal) => {
    const receipt = await collaboration.receipt(sessionId, key, signal)
    if (signal?.aborted) return receipt
    if (receipt.ok && receipt.data.run) settle(key, submittedText)
    else if (alive.current) setReceiptMissing(receipt.ok && !receipt.data.run)
    return receipt
  }, [sessionId, settle])
  useEffect(() => {
    alive.current = true
    void refresh()
    const changed = () => {
      if (document.hidden) {refreshGeneration.current++; refreshRead.current?.abort()}
      else void refresh()
    }
    document.addEventListener('visibilitychange', changed)
    const timer = window.setInterval(() => {if (!document.hidden && !refreshRead.current) void refresh()}, 5000)
    return () => {
      alive.current = false; refreshGeneration.current++; refreshRead.current?.abort()
      clearInterval(timer); document.removeEventListener('visibilitychange', changed)
    }
  }, [refresh])
  useEffect(() => {
    if (!pending || !visible) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    // 页面恢复与超时只回查原请求，绝不在后台重放提交。
    const poll = async () => {
      await check(pending.id, pending.text, controller.signal)
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000)
    }
    void poll()
    return () => {controller.abort(); clearTimeout(timer)}
  }, [pending?.id, check, visible])
  async function submit(retry = false) {
    if (lock.current || (pending && !retry) || (retry && (!pending || !receiptMissing)) || !text.trim() || !canContribute) return
    lock.current = true; setBusy(true); setReceiptMissing(false); setFailure(null)
    const key = retry && pending ? pending.id : requestKey(), submittedText = retry && pending ? pending.text : text
    if (!retry) useDraftStore.getState().rememberSubmission(scope, {id: key, text: submittedText, attachments: [], action: 'send', status: 'sending'})
    const result = await collaboration.submit(sessionId, submittedText, key)
    if (result.ok) settle(key, submittedText)
    else {
      const receipt = await check(key, submittedText)
      if (!(receipt.ok && receipt.data.run)) {
        if (result.status >= 400 && result.status < 500 && result.status !== 408 && receipt.ok && !receipt.data.run) {
          useDraftStore.getState().settleSubmission(scope, key)
          if (alive.current) setFailure(result)
        } else {
          useDraftStore.getState().markUnknown(scope)
          if (alive.current) setFailure({key: 'ui.shared.submitUnknown'})
        }
      }
    }
    lock.current = false
    if (alive.current) {setBusy(false); void refresh()}
  }
  const onKeyDown = useComposerKeyboard({running: false, shortcutsOpen: false, setShortcutsOpen: () => undefined,
    onCancel: () => undefined, submit: () => submit(), handleCompletionKey: () => false})
  if (!canContribute && !queue.length && !failure && !pending) return null
  return <ComposerDock ref={dockRef}>
    {queue.length > 0 && <details><summary className="cursor-pointer text-muted">{t('ui.shared.queued')} ({queue.length})</summary><ul className="max-h-32 space-y-2 overflow-y-auto py-2">{queue.map(row => <SharedQueueItem key={row.id} row={row} sessionId={sessionId} editable={canContribute && row.requester?.subject === subject} refresh={refresh} />)}</ul></details>}
    {failure && <p role="alert" className="text-danger">{collaborationFeedbackMessage(failure)}</p>}
    {pending && <div role="status" className="flex flex-wrap items-center gap-2 text-muted">{t('ui.shared.submitUnknown')}<Button size="sm" variant="ghost" onClick={() => void check(pending.id, pending.text)}>{t('ui.shared.checkReceipt')}</Button>{receiptMissing && canContribute && <Button size="sm" disabled={busy} onClick={() => void submit(true)}>{t('ui.shared.retrySameRequest')}</Button>}</div>}
    {canContribute && <ComposerCard ref={wrapRef}>
      <ComposerEditor ref={taRef} placeholder={t('ui.components.chat.Composer.dc2f7127b291')} value={text} disabled={busy || Boolean(pending)} onKeyDown={onKeyDown} onChange={event => useDraftStore.getState().setText(scope, event.target.value)} />
      <ComposerToolbar><div className="ml-auto flex items-center gap-2"><ComposerSendButton disabled={busy || Boolean(pending) || !text.trim()} onClick={() => void submit()} /></div></ComposerToolbar>
    </ComposerCard>}
  </ComposerDock>
}
