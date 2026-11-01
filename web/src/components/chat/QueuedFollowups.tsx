import {useAppStore} from '@/store/app'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { ConfirmRemoval } from '@/components/ui/ConfirmRemoval'
import {QueueControlDock} from './QueueControlDock'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, ArrowDown, CornerDownRight, ListEnd, Loader2, Pencil } from 'lucide-react'
import { useChat, useChatStore } from '@/store/chat'
import { useQueueEditStore } from '@/store/queueEdits'
import { MessageAttachments } from './blocks/MessageBlocks'
import { QueuedRecipients } from './QueuedRecipients'
import { QueuedInputEditor } from './QueuedInputEditor'
import { Button, RefreshButton, Tooltip } from '@/components/ui/primitives'
import { competingQueueEntry, moveQueuedInput, readQueuedInputs, removeQueuedInput, withdrawQueuedInput, verifyWithdrawnInput, QueueWithdrawalUnknown, steerQueuedInput, type QueueEntry } from '@/lib/queueEdits'

/** Pending prompts sit above the task checklist and composer, like Codex. */
export function QueuedFollowups({ sessionId, disabled, preserveDraft, onRestored, onWorking }: {
  sessionId: string; disabled: boolean; preserveDraft: boolean; onRestored: (entry: QueueEntry, restoreFocus: boolean) => void; onWorking: (busy: boolean) => void
}) {
  useLocale()

  const chat = useChat(sessionId)
  const edit = useQueueEditStore(state => state.bySession[sessionId])
  const section = useRef<HTMLElement | null>(null)
  const [ready, setReady] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const withdrawal = useQueueEditStore(state => state.withdrawals[sessionId])
  const [entries, setEntries] = useState<QueueEntry[]>([])
  const [error, setError] = useState<ResourceText>('')
  const [readError, setReadError] = useState<ResourceText>('')
  const [busy, setBusy] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const working = useRef(false)
  const alive = useRef(true)
  const revision = useRef(0)
  const scope = useRef(0)
  useLayoutEffect(() => {
    alive.current = true
    let owner = useAppStore.getState().activeSessionId
    const unsubscribe = useAppStore.subscribe(state => {
      if (state.activeSessionId !== owner) { owner = state.activeSessionId; scope.current++ }
    })
    return () => { alive.current = false; scope.current++; unsubscribe() }
  }, [])

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    async function read() {
      if (!active) return
      if (!document.hidden && !working.current) {
        const ticket = revision.current
        try {
          const next = await readQueuedInputs(sessionId, controller.signal)
          if (active && ticket === revision.current) { setEntries(next); setReady(true); setReadError('') }
        } catch (failure) {
          if (active && ticket === revision.current) { setReady(false); setReadError(failureText(failure)) }
        }
      }
      if (active && (chat.running || chat.queued > 0 || chat.queuePaused)) timer = setTimeout(read, 1500)
    }
    const visible = () => { if (!document.hidden) { clearTimeout(timer); setRefresh((value) => value + 1) } }
    document.addEventListener('visibilitychange', visible)
    void read()
    return () => { active = false; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visible) }
  }, [sessionId, chat.running, chat.queued, chat.queuePaused, refresh])

  function closeEditor(entry: QueueEntry) {
    requestAnimationFrame(() => Array.from(section.current?.querySelectorAll<HTMLElement>('[data-queued-id]') ?? []).find(row => row.dataset.queuedId === entry.id && row.dataset.queuedKind === entry.kind)?.querySelector<HTMLButtonElement>('[data-queue-edit]')?.focus())
  }

  async function act(entry: QueueEntry, action: 'guide' | 'edit' | 'delete' | 'up' | 'down') {
    if (working.current || disabled) return false
    const operationScope = scope.current
    working.current = true
    revision.current++ // Discard snapshots read before the action.
    setBusy(entry.id); setError(''); onWorking(true)
    try {
      if (action === 'up' || action === 'down') {
        const next = await moveQueuedInput(sessionId, entries, entry, action === 'up' ? -1 : 1)
        if (alive.current) setEntries(next)
      } else if (action === 'guide') {
        const receipt = await steerQueuedInput(sessionId, entry, chat.turn)
        useChatStore.getState().apply(sessionId, { type: 'steer/state', receipt })
      } else if (action === 'edit') {
        if (preserveDraft && !withdrawal) {
          useQueueEditStore.getState().setEdit(sessionId, {entry, draft: entry.text})
          return true
        }
        if (withdrawal) await verifyWithdrawnInput(sessionId, withdrawal)
        else {
          useQueueEditStore.getState().setWithdrawal(sessionId, entry)
          await withdrawQueuedInput(sessionId, entry)
        }
        onRestored(withdrawal ?? entry, alive.current && operationScope === scope.current && useAppStore.getState().activeSessionId === sessionId)
        useQueueEditStore.getState().setWithdrawal(sessionId, null)
      } else await removeQueuedInput(sessionId, entry)
      if (alive.current && action !== 'up' && action !== 'down') setEntries((values) => values.filter((item) => item.id !== entry.id))
      return true
    } catch (failure) {
      if (action === 'edit' && !(failure instanceof QueueWithdrawalUnknown)) useQueueEditStore.getState().setWithdrawal(sessionId, null)
      if (alive.current) {
        setError(failureText(failure))
        const current = competingQueueEntry(failure, entry)
        if (current) setEntries((values) => values.map((item) => item.id === entry.id ? current : item))
      }
      return false
    } finally {
      working.current = false
      onWorking(false)
      if (alive.current) { setBusy(null); setRefresh((value) => value + 1) }
    }
  }

  const visibleEntries = withdrawal && !entries.some(entry => entry.id === withdrawal.id && entry.kind === withdrawal.kind)
    ? [...entries, withdrawal] : entries
  const issue = error ? noticeText(error) : readError ? t("ui.components.chat.QueuedFollowups.82fe2ddfd2ce", { detail: noticeText(readError) }) : ''
  if (!visibleEntries.length && !edit && !issue && chat.queued === 0 && !chat.queuePaused) return null
  return <section ref={section} aria-label={t("ui.components.chat.QueuedFollowups.626b1167406f")} className="mb-2 w-full min-w-0 overflow-hidden rounded-xl border border-line bg-surface/90">
    <QueueControlDock sessionId={sessionId} expanded={expanded} onToggleQueue={() => setExpanded(value => !value)} />
    <div hidden={!expanded}>
    <div className="max-h-queue overflow-y-auto">
      {visibleEntries.map((entry, index) => <div key={entry.kind + ':' + entry.id} data-queued-id={entry.id} data-queued-kind={entry.kind}
        className="flex flex-wrap items-start gap-x-2 gap-y-1 border-b border-line/70 px-3 py-2.5 last:border-b-0 sm:flex-nowrap">
        <ListEnd aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-faint" />
        <div className="flex min-w-0 flex-1 items-start gap-2">
          {!!entry.attachment_refs?.length && <div className="max-w-attachment shrink-0"><MessageAttachments items={entry.attachment_refs} /></div>}
        <p className="min-w-0 flex-1 whitespace-pre-wrap text-compact leading-5 [overflow-wrap:anywhere] line-clamp-3" title={entry.text}>
          {entry.team_route && <QueuedRecipients route={entry.team_route} mount={chat.agentMount} />}
          {entry.text || t("ui.components.chat.QueuedFollowups.402c881d6d0b")}
        </p>
        </div>
        <div className="flex w-full shrink-0 items-center justify-end gap-0.5 sm:w-auto">
          {([-1, 1] as const).map(direction => {
            const neighbour = visibleEntries[index + direction]
            const blocked = !neighbour || neighbour.kind !== entry.kind || Boolean(neighbour.goal_id) !== Boolean(entry.goal_id)
            const label = t(direction === -1 ? 'ui.queue.moveUp' : 'ui.queue.moveDown')
            const Icon = direction === -1 ? ArrowUp : ArrowDown
            return <Button key={direction} size="xs" variant="ghost" aria-label={label} title={label}
              disabled={disabled || Boolean(busy) || Boolean(edit) || Boolean(withdrawal) || blocked}
              onClick={() => void act(entry, direction === -1 ? 'up' : 'down')}><Icon className="h-3.5 w-3.5" aria-hidden="true" /></Button>
          })}

          <Tooltip content={t("ui.components.chat.QueuedFollowups.bea504124378")}>
            <Button size="xs" variant="ghost" aria-label={t("ui.components.chat.QueuedFollowups.c4e5db2860e5")} disabled={disabled || Boolean(busy) || Boolean(edit) || Boolean(withdrawal) || !chat.running || Boolean(entry.team_route)}
              icon={<CornerDownRight className="h-3.5 w-3.5" />}
              onClick={() => void act(entry, 'guide')}>{t("ui.components.chat.QueuedFollowups.4084045d9cf7")}</Button>
          </Tooltip>
          <Tooltip content={t(preserveDraft ? 'ui.components.chat.QueuedInputEditor.9fd71ecb87c7' : 'ui.components.chat.QueuedFollowups.3132e7dc0eca')}>
            <Button data-queue-edit size="xs" variant="ghost" aria-label={t("ui.components.chat.QueuedFollowups.9fd71ecb87c7")} disabled={disabled || Boolean(busy) || Boolean(edit) || Boolean(withdrawal && (entry.id !== withdrawal.id || entry.kind !== withdrawal.kind))}
              icon={busy === entry.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
              onClick={() => void act(entry, 'edit')}>{withdrawal?.id === entry.id && withdrawal.kind === entry.kind ? t("ui.components.chat.QueuedFollowups.verifyWithdrawal") : t("ui.components.chat.QueuedFollowups.edit")}</Button>
          </Tooltip>
          <ConfirmRemoval key={JSON.stringify([sessionId, entry.kind, entry.id, entry.edit_version])}
            name={entry.text.slice(0, 120) || entry.id}
            prompt={t('ui.queue.confirmRemove')}
            disabled={disabled || Boolean(busy) || Boolean(edit) || Boolean(withdrawal)} onConfirm={() => act(entry, 'delete')} />
        </div>
      </div>)}
      {!entries.length && chat.queued > 0 ? <p role="status" className="px-3 py-2 text-xs text-muted">{t("ui.components.chat.QueuedFollowups.60a728dde58d")}</p> : null}
    </div>
    {edit && <><p role="status" className="border-t border-line px-3 py-2 text-xs text-muted">{t('ui.queueEdit.preserveDraft')}</p><QueuedInputEditor sessionId={sessionId} entries={entries} ready={ready} disabled={disabled || Boolean(busy)} onWorking={onWorking}
      onChanged={current => { if (current) setEntries(values => values.map(row => row.kind === current.kind && row.id === current.id ? current : row)); setRefresh(value => value + 1) }} onClosed={closeEditor} /></>}
    </div>
    {withdrawal && !busy && !issue ? <p role="status" className="border-t border-line px-3 py-2 text-xs text-muted">{t("ui.lib.queueEdits.withdrawUnknown")}</p> : null}
    {issue ? <div role="alert" className="space-y-1 border-t border-line px-3 py-2 text-xs text-danger">
      <p className="[overflow-wrap:anywhere]">{issue}</p>
      <RefreshButton size="xs" variant="ghost" label={t("ui.components.chat.QueuedFollowups.aee887434131")} disabled={Boolean(busy)} onClick={() => { setError(''); setReadError(''); setRefresh((value) => value + 1) }} />
    </div> : null}
  </section>
}
