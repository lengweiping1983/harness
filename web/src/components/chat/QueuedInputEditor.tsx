import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { t, useLocale } from '@/i18n'
import { useEffect, useId, useRef, useState } from 'react'
import { QueueSaveUnknown, saveQueuedInput, verifyQueuedInput, competingQueueEntry, type QueueEntry } from '@/lib/queueEdits'
import { useQueueEditStore } from '@/store/queueEdits'
import { Button } from '@/components/ui/primitives'

/** Edits stay in the tab's durable queue draft; a lost PATCH is only read back. */
export function QueuedInputEditor({ sessionId, entries, ready, disabled, onWorking, onChanged, onClosed }: {
  sessionId: string; entries: QueueEntry[]; ready: boolean; disabled: boolean
  onWorking: (working: boolean) => void; onChanged: (entry?: QueueEntry) => void
  onClosed: (entry: QueueEntry) => void
}) {
  useLocale()

  const edit = useQueueEditStore(state => state.bySession[sessionId] ?? null)
  const storageError = useQueueEditStore(state => state.storageError)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const alive = useRef(true)
  const lock = useRef(false)
  const id = useId()
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { setError('') }, [edit?.entry.id, edit?.entry.kind])
  if (!edit) return null
  const latest = entries.find(entry => entry.kind === edit.entry.kind && entry.id === edit.entry.id)
  const changed = latest && latest.edit_version !== edit.entry.edit_version
  const body = edit.draft
  const attached = Boolean(edit.entry.attachment_refs?.length)
  const setEdit = (value: typeof edit | null) => useQueueEditStore.getState().setEdit(sessionId, value)

  async function save(verify = false) {
    if (!edit || lock.current || disabled) return
    lock.current = true; setBusy(true); setError(''); onWorking(true)
    const pending = verify ? edit : { ...edit, unconfirmed: { entry: edit.entry, text: edit.draft } }
    if (!verify) setEdit(pending)
    try {
      if (verify && pending.unconfirmed) await verifyQueuedInput(sessionId, pending.unconfirmed.entry, pending.unconfirmed.text)
      else await saveQueuedInput(sessionId, edit.entry, edit.draft)
      // Another surface may have opened a new editor while this request was pending.
      if (useQueueEditStore.getState().bySession[sessionId] === pending) { setEdit(null); if (alive.current) onClosed(edit.entry) }
    } catch (failure) {
      if (!(failure instanceof QueueSaveUnknown) && useQueueEditStore.getState().bySession[sessionId] === pending) setEdit(edit)
      if (alive.current) {
        setError(failureText(failure))
        onChanged(competingQueueEntry(failure, edit.entry) ?? undefined)
      }
    } finally {
      lock.current = false
      onWorking(false)
      if (alive.current) { setBusy(false); onChanged() }
    }
  }
  return <form aria-label={t("ui.components.chat.QueuedInputEditor.9fd71ecb87c7")} className="space-y-2 border-t border-line px-3 py-3" onSubmit={event => {
    event.preventDefault()
    if (!disabled && !busy && ready && latest && !changed && !edit.unconfirmed && (body.trim() || attached)) void save()
  }}>
    <label htmlFor={id} className="text-xs font-medium">{t("ui.components.chat.QueuedInputEditor.9fd71ecb87c7")}{attached ? <span className="ml-2 font-normal text-muted">{t("ui.components.chat.QueuedInputEditor.6f6153ce5a74")}</span> : null}</label>
    <textarea id={id} autoFocus rows={3} value={body} disabled={disabled || busy || !!edit.unconfirmed}
      className="block w-full resize-y rounded-lg border border-line bg-base p-2 text-sm leading-5 text-ink focus:outline-brand"
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'Escape' && !busy && !edit.unconfirmed) { event.preventDefault(); event.stopPropagation(); setEdit(null); onClosed(edit.entry) }
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); event.currentTarget.form?.requestSubmit() }
      }}
      onChange={event => setEdit({ ...edit, draft: event.target.value })} />
    {storageError && <p role="alert" className="text-xs text-warn">{noticeText(storageError)}</p>}
    {changed && !edit.unconfirmed && <div role="status" className="space-y-2 text-xs leading-5">
      <p className="text-warn">{t("ui.components.chat.QueuedInputEditor.1f47fd5cbd61")}</p>
      <p className="whitespace-pre-wrap break-words rounded-md bg-raised p-2 text-muted">{latest.text}</p>
      <Button size="xs" disabled={busy || disabled} onClick={() => { setEdit({ ...edit, entry: latest, draft: body }); setError('') }}>{t("ui.components.chat.QueuedInputEditor.ffd3959f4a5e")}</Button>
    </div>}
    {edit.unconfirmed && <p role="status" className="text-xs leading-5 text-warn">{t("ui.components.chat.QueuedInputEditor.8c908e098589")}</p>}
    {!ready ? <p role="status" className="text-xs text-muted">{t("ui.components.chat.QueuedInputEditor.747846a0795b")}</p> : !latest && <p role="status" className="text-xs leading-5 text-warn">{t("ui.components.chat.QueuedInputEditor.4e6133982ed9")}</p>}
    {error && <p role="alert" className="break-words text-xs leading-5 text-danger">{noticeText(error)}</p>}
    <div className="flex flex-wrap gap-2">
      {edit.unconfirmed ? <Button size="sm" disabled={disabled} loading={busy} onClick={() => void save(true)}>{t("ui.components.chat.QueuedInputEditor.9b75ad5a4769")}</Button> :
        <Button type="submit" size="sm" variant="primary" loading={busy} disabled={disabled || !ready || !latest || !!changed || !(body.trim() || attached)}>{t("ui.components.chat.QueuedInputEditor.756b121cc6b1")}</Button>}
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEdit(null); setError(''); onClosed(edit.entry) }}>{edit.unconfirmed ? t("ui.components.chat.QueuedInputEditor.64280f0409a6") : t("ui.components.chat.QueuedInputEditor.4b29ce8e4093")}</Button>
    </div>
  </form>
}
