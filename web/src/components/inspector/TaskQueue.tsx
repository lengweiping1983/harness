import {usePageVisible} from '@/hooks/usePageVisible'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { QueuedRecipients } from '@/components/chat/QueuedRecipients'
import { MessageAttachments } from '@/components/chat/blocks/MessageBlocks'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, ListOrdered, Loader2 } from 'lucide-react'
import { useAppStore } from '@/store/app'
import { useChat } from '@/store/chat'
import type { RunPage, RunRecord } from '@/lib/protocol.generated'
import { runtimeRequest, ApiError } from '@/lib/http'
import { copyText } from '@/lib/utils'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { RunHistoryCard } from './RunHistoryCard'
import { RecoveryReview } from './RecoveryReview'
import { RunPreview } from './RunPreview'
import { QueueSaveUnknown, competingQueueEntry, saveQueuedInput, verifyQueuedInput, removeQueuedInput, type QueueEntry } from '@/lib/queueEdits'
import { useQueueEditStore, type QueueEditDraft } from '@/store/queueEdits'
import { QueueControlDock } from '@/components/chat/QueueControlDock'

type Entry = QueueEntry
type Snapshot = { cursor: string | null; entries: Entry[]; page: RunPage }
const entryKey = (entry: Entry) => entry.kind + ':' + entry.id
/** Parent keys this component by session: drafts and late responses never cross tasks. */
export function TaskQueue({ sessionId, active = true }: { sessionId: string; active?: boolean }) {
  useLocale()
  const visible = usePageVisible() && active

  const chat = useChat(sessionId)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const cursor = cursors[cursors.length - 1]
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [reading, setReading] = useState(false)
  const [readError, setReadError] = useState<ResourceText>('')
  const [actionError, setActionError] = useState<ResourceText>('')
  const [cleanupBlocked, setCleanupBlocked] = useState(false)
  const [busy, setBusy] = useState(false)
  const edit = useQueueEditStore((state) => state.bySession[sessionId] ?? null)
  const storageError = useQueueEditStore((state) => state.storageError)
  const setEdit = (value: QueueEditDraft | null) => useQueueEditStore.getState().setEdit(sessionId, value)
  const unconfirmed = edit?.unconfirmed
  const reveal = useAppStore((state) => state.inspectorRunTarget)
  const target = reveal?.sessionId === sessionId ? reveal : null
  const [review, setReview] = useState<RunRecord | null>(null)
  const busyRef = useRef(false)
  const revision = useRef(0)
  const mounted = useRef(true)
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  useEffect(() => {
    if (!visible) return
    let alive = true
    let inFlight = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    async function poll() {
      if (!alive || inFlight) return
      if (document.hidden || busyRef.current) { timer = setTimeout(poll, 2000); return }
      inFlight = true
      const ticket = revision.current
      setReading(true)
      try {
        const suffix = cursor ? '&before=' + encodeURIComponent(cursor) : ''
        const options = { signal: controller.signal }
        const replies = await Promise.allSettled([
          runtimeRequest<{ runs: Omit<Entry, 'kind'>[] }>('/sessions/' + sessionId + '/queue', 'GET', undefined, options),
          runtimeRequest<RunPage>('/sessions/' + sessionId + '/runs?limit=25&include_queued=0&summary=1' + suffix, 'GET', undefined, options),
        ])
        const queueReply = replies[0]
        const historyReply = replies[1]
        const queueFailure = queueReply.status === 'rejected' ? queueReply.reason : null
        const historyFailure = historyReply.status === 'rejected' ? historyReply.reason : null
        if (queueFailure && historyFailure) throw queueFailure
        if (alive && ticket === revision.current) {
          setSnapshot(previous => ({
            cursor,
            page: historyReply.status === 'fulfilled' ? historyReply.value : previous?.page ?? {runs: [], has_more: false, next_before: null},
            entries: queueReply.status === 'fulfilled' ? queueReply.value.runs.map((entry) => ({ ...entry, kind: 'runs' as const })) : previous?.entries ?? [],
          }))
          const partialFailure = queueFailure ?? historyFailure
          setReadError(partialFailure ? failureText(partialFailure) : '')
        }
      } catch (failure) {
        if (alive && ticket === revision.current) setReadError(failureText(failure))
      } finally {
        inFlight = false
        if (alive) { setReading(false); timer = setTimeout(poll, 2000) }
      }
    }
    const visibilityChanged = () => { if (!document.hidden && !inFlight) { clearTimeout(timer); void poll() } }
    document.addEventListener('visibilitychange', visibilityChanged)
    void poll()
    return () => { alive = false; clearTimeout(timer); controller.abort(); document.removeEventListener('visibilitychange', visibilityChanged) }
  }, [sessionId, cursor, refresh, visible])

  const reload = () => setRefresh((value) => value + 1)
  function focusAfterEdit(key: string | null) {
    requestAnimationFrame(() => {
      if (!mounted.current || useQueueEditStore.getState().bySession[sessionId]) return
      const button = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('[data-edit-key]') ?? [])
        .find((value) => value.dataset.editKey === key)
      if (button && !button.disabled) button.focus()
      else panel.current?.querySelector<HTMLHeadingElement>('[data-queue-heading]')?.focus()
    })
  }
  function closeEdit() {
    const key = edit ? entryKey(edit.entry) : null
    setEdit(null)
    setActionError('')
    focusAfterEdit(key)
  }
  function settleEdit(expected: QueueEditDraft) {
    if (useQueueEditStore.getState().bySession[sessionId] !== expected) return
    setEdit(null)
    if (mounted.current) { setActionError(''); focusAfterEdit(entryKey(expected.entry)) }
  }
  async function act(operation: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    revision.current++ // Invalidate a read started before the mutation.
    setBusy(true); setActionError(''); setCleanupBlocked(false)
    try { await operation() }
    catch (failure) {
      if (mounted.current) {
        setActionError(failureText(failure))
        setCleanupBlocked(failure instanceof ApiError && failure.code === 'process/cleanup-failed')
      }
    } finally {
      busyRef.current = false
      if (mounted.current) { setBusy(false); reload() }
    }
  }
  async function change(entry: Entry, remove: boolean) {
    if (remove) {
      try { await removeQueuedInput(sessionId, entry) }
      catch (failure) {
        showCompetingEntry(failure, entry)
        throw failure
      }
      return
    }
    if (!edit) return
    const text = edit.draft
    const pendingEdit = { ...edit, unconfirmed: { entry, text } }
    // Persist before sending so a refresh during PATCH restores a read-only
    // verification intent, rather than offering another save.
    setEdit(pendingEdit)
    try { await saveQueuedInput(sessionId, entry, text) }
    catch (failure) {
      if (!(failure instanceof QueueSaveUnknown) && useQueueEditStore.getState().bySession[sessionId] === pendingEdit) setEdit(edit)
      showCompetingEntry(failure, entry)
      throw failure
    }
    settleEdit(pendingEdit)
    if (mounted.current) useAppStore.getState().toast({ title: {key: "ui.components.inspector.TaskQueue.220107f131b5"}, tone: 'success' })
  }
  function showCompetingEntry(failure: unknown, entry: Entry) {
    const current = competingQueueEntry(failure, entry)
    if (current && mounted.current) setSnapshot((value) => value ? { ...value, entries: value.entries.map((item) => entryKey(item) === entryKey(entry) ? current : item) } : value)
  }
  async function verifySave() {
    if (!unconfirmed) return
    await verifyQueuedInput(sessionId, unconfirmed.entry, unconfirmed.text)
    if (edit) settleEdit(edit)
    if (mounted.current) useAppStore.getState().toast({ title: {key: "ui.components.inspector.TaskQueue.d31dd3908461"}, tone: 'success' })
  }
  function resumed(submitted: RunRecord) {
    if (mounted.current) {
      setReview(null)
      setCursors([null])
      useAppStore.getState().toast({ title: {key: submitted.recovery?.mode === 'inspect' ? "ui.components.inspector.TaskQueue.09d2a418153b" : "ui.components.inspector.TaskQueue.f9ece99f71df"}, description: {key: "ui.components.inspector.TaskQueue.5604333a9208"}, tone: 'success' })
      // Keep the current queue and history visible; the next poll reconciles the accepted record.
      if (submitted.status === 'failed' || submitted.status === 'interrupted') useAppStore.getState().revealRun(sessionId, { runId: submitted.id })
    }
  }
  function inspect(runId: string) {
    useAppStore.getState().revealRun(sessionId, { runId })
  }
  function closePreview() {
    useAppStore.getState().dismissRunPreview()
    requestAnimationFrame(() => panel.current?.querySelector<HTMLHeadingElement>('[data-queue-heading]')?.focus())
  }
  const current = snapshot?.cursor === cursor ? snapshot : null
  const entries = snapshot?.entries ?? []
  const latest = edit ? entries.find((entry) => entryKey(entry) === entryKey(edit.entry)) : undefined
  const editable = Boolean(latest)
  const changed = edit && latest && latest.edit_version !== edit.entry.edit_version
  const waiting = chat.questions.length ? (chat.questions.some((question) => question.questions.some((item) => item.id === 'permission')) ? 'approval' : 'answer') : null
  const context = { stopping: chat.stopping, waiting } as const

  return <div ref={panel} className="refresh-action-container min-w-0 space-y-5" aria-label={t("ui.components.inspector.TaskQueue.1f541bc1cc2e")}>
    <QueueControlDock sessionId={sessionId} />
    <section aria-label={t("ui.components.inspector.TaskQueue.9d18bfe48c0b")} className="space-y-2">
      <div className="flex items-center justify-between gap-2"><h3 data-queue-heading tabIndex={-1} className="flex items-center gap-2 text-sm font-medium"><ListOrdered className="h-4 w-4 shrink-0 text-muted" /><span>{snapshot ? t(entries.length === 1 ? 'ui.queue.pendingOne' : 'ui.queue.pendingCount', {count: entries.length}) : t("ui.components.inspector.TaskQueue.9d18bfe48c0b")}</span></h3><RefreshButton size="xs" label={t("ui.components.inspector.TaskQueue.aee887434131")} disabled={reading || busy} onClick={reload} /></div>
      {readError ? <div role="alert" className="space-y-2 text-xs text-danger"><p className="break-words">{t('ui.queue.readFailed', {detail: noticeText(readError)})}</p><Button size="xs" disabled={busy} onClick={reload}>{t("ui.components.inspector.TaskQueue.1133d6759e10")}</Button></div> : null}
      {actionError ? <div role="alert" className="space-y-2 text-xs text-danger"><p className="break-words">{noticeText(actionError)}</p>{cleanupBlocked ? <Button size="xs" onClick={() => useAppStore.getState().setInspectorTab('runtime')}>{t("ui.components.inspector.TaskQueue.b7fe32982b6e")}</Button> : null}</div> : null}
      {!snapshot ? readError ? <p className="text-xs text-muted">{t("ui.components.inspector.TaskQueue.cdd9e838b758")}</p> : <p role="status" className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.components.inspector.TaskQueue.d320ec7b7ac6")}</p> : entries.length === 0 ? <p className="text-xs leading-5 text-muted">{t("ui.components.inspector.TaskQueue.778d7570b4c8")}</p> : <div>{entries.map((entry) => <div key={entryKey(entry)} className="space-y-2 border-b border-line/75 py-3 last:border-b-0">
        {entry.team_route && <QueuedRecipients route={entry.team_route} mount={chat.agentMount} />}
        {!!entry.attachment_refs?.length && <MessageAttachments items={entry.attachment_refs} />}
        <p className="whitespace-pre-wrap text-compact leading-5 [overflow-wrap:anywhere] line-clamp-3">{entry.text || t('ui.components.chat.QueuedFollowups.402c881d6d0b')}</p>
        <div className="flex gap-1"><Button data-edit-key={entryKey(entry)} size="xs" variant="ghost" disabled={busy || Boolean(edit)} onClick={() => setEdit({ entry, draft: entry.text })}>{t("ui.components.inspector.TaskQueue.051836569928")}</Button><Button size="xs" variant="ghost" disabled={busy || Boolean(edit)} onClick={() => void act(() => change(entry, true))}>{t("ui.components.inspector.TaskQueue.6135d4159e89")}</Button></div>
      </div>)}</div>}
      {edit ? <div className="space-y-2 rounded-lg border border-line bg-canvas p-3">
        {storageError ? <p role="alert" className="text-xs leading-5 text-warn">{noticeText(storageError)}</p> : null}
        <label className="block text-xs font-medium" htmlFor="queue-edit">{t("ui.components.inspector.TaskQueue.702a25c4d3db")}{!!edit.entry.attachment_refs?.length && <span className="ml-2 font-normal text-muted">{t('ui.components.chat.QueuedInputEditor.6f6153ce5a74')}</span>}</label>
        <textarea id="queue-edit" autoFocus className="min-h-24 w-full resize-y rounded-md border border-line bg-canvas p-2 text-sm leading-5" value={edit.draft} onChange={(event) => setEdit({ ...edit, draft: event.target.value })} disabled={busy || Boolean(unconfirmed)} />
        {changed && !unconfirmed ? <div role="status" className="space-y-2 text-xs leading-5"><p className="text-warn">{t("ui.components.inspector.TaskQueue.157e0d0fb87d")}</p><p className="font-medium text-muted">{t("ui.components.inspector.TaskQueue.281649f90f23")}</p><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-raised p-2 text-ink">{latest.text}</pre><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEdit({ ...edit, entry: latest }); setActionError('') }}>{t("ui.components.inspector.TaskQueue.ffd3959f4a5e")}</Button></div> : null}
        {unconfirmed ? <div role="status" className="space-y-2 text-xs leading-5 text-warn"><p>{t("ui.components.inspector.TaskQueue.9a757bd7cea3")}</p><Button size="sm" disabled={busy} onClick={() => void act(verifySave)}>{t("ui.components.inspector.TaskQueue.9b75ad5a4769")}</Button></div> : null}
        {!editable ? <p role="status" className="text-xs leading-5 text-warn">{t("ui.components.inspector.TaskQueue.65030949bc96")}</p> : null}
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy || !editable || Boolean(changed) || Boolean(unconfirmed) || (!edit.draft.trim() && !edit.entry.attachment_refs?.length)} onClick={() => void act(() => change(edit.entry, false))}>{t("ui.components.inspector.TaskQueue.a3030bf8f16d")}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={closeEdit}>{unconfirmed ? t("ui.components.inspector.TaskQueue.64280f0409a6") : t("ui.components.inspector.TaskQueue.4b29ce8e4093")}</Button><Button size="sm" variant="ghost" onClick={() => void copyText(edit.draft)}>{t("ui.components.inspector.TaskQueue.a5e4b7597482")}</Button></div>
      </div> : null}
    </section>
    {target ? <RunPreview key={target.seq} target={target} busy={busy} active={visible} onClose={closePreview} onContinue={setReview} onOriginal={inspect} /> : null}
    <section aria-label={t("ui.components.inspector.TaskQueue.02451fd58cd3")}>
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium">{cursor ? t("ui.components.inspector.TaskQueue.0c6dcdfe0be3") : t("ui.components.inspector.TaskQueue.03a3925c64d1")}</h3>{cursor ? <Button size="xs" variant="ghost" disabled={busy} onClick={() => setCursors([null])}>{t("ui.components.inspector.TaskQueue.1c59875d3a15")}</Button> : null}</div>
      <p className="mt-1 text-xs leading-5 text-muted">{t("ui.components.inspector.TaskQueue.abcde1e97b0e")}</p>
      {!current ? readError ? <p className="mt-3 text-xs text-muted">{t("ui.components.inspector.TaskQueue.2d5b5d7f6a8e")}</p> : <p role="status" className="mt-3 flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.components.inspector.TaskQueue.01cec024e0c5")}</p> : !current.page.runs.length ? <p className="mt-3 text-xs text-muted">{t("ui.components.inspector.TaskQueue.b2e762480725")}</p> : current.page.runs.map((record) => <RunHistoryCard key={record.id} record={record} sessionId={sessionId} busy={busy} active={visible} context={context} onContinue={setReview} onOriginal={inspect} />)}
      <nav aria-label={t("ui.components.inspector.TaskQueue.1b2971cb2614")} className="mt-3 flex flex-wrap items-center justify-between gap-2"><Button size="sm" variant="ghost" icon={<ChevronLeft className="h-3.5 w-3.5" />} disabled={busy || cursors.length === 1} onClick={() => setCursors((values) => values.slice(0, -1))}>{t("ui.components.inspector.TaskQueue.7683ccb55d72")}</Button><span className="text-xs text-faint">{t('ui.queue.pageIndicator', {page: cursors.length})}</span><Button size="sm" variant="ghost" iconRight={<ChevronRight className="h-3.5 w-3.5" />} disabled={busy || !current?.page.next_before} onClick={() => { const next = current?.page.next_before; if (next) setCursors((values) => [...values, next]) }}>{t("ui.components.inspector.TaskQueue.dea367b006bb")}</Button></nav>
    </section>
    {review ? <RecoveryReview key={review.id} sessionId={sessionId} record={review} onClose={() => setReview(null)} onSubmitted={resumed} onOriginal={(id) => { setReview(null); inspect(id) }} /> : null}
  </div>
}
