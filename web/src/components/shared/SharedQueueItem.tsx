import {isUnconfirmedWrite} from '@/lib/http'
import {collaborationFeedbackMessage, type CollaborationFeedback} from '@/lib/collaborationError'
import {useEffect, useRef, useState} from 'react'
import {Button} from '@/components/ui/primitives'
import {collaboration} from '@/lib/collaboration'
import type {QueueEntry} from '@/lib/protocol.generated'
import {t} from '@/i18n'

export function SharedQueueItem({row, sessionId, editable, refresh}: {row: QueueEntry; sessionId: string; editable: boolean; refresh: () => Promise<void>}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(row.text)
  const [version, setVersion] = useState(row.edit_version)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<CollaborationFeedback>(null)
  const [unknown, setUnknown] = useState(false)
  const alive = useRef(true)
  const lock = useRef(false)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  async function update(remove: boolean) {
    if (lock.current || unknown) return
    lock.current = true; setBusy(true); setError(null)
    const result = remove ? await collaboration.remove(sessionId, row.id, row.edit_version) : await collaboration.edit(sessionId, row.id, text, version)
    // 成功和未知都回查原运行，不自动重放修改或删除。
    const current = await collaboration.run(sessionId, row.id)
    await refresh()
    lock.current = false
    if (!alive.current) return
    setBusy(false)
    if (result.ok || current.ok && (remove ? current.data.status === 'interrupted' && current.data.error?.code === 'run/removed' : current.data.text === text)) {
      setEditing(false); setUnknown(false)
    } else {
      setError(result.ok ? {key: 'ui.shared.queueUnknown'} : result)
      setUnknown(!current.ok || isUnconfirmedWrite(result))
    }
  }
  async function check() {
    const current = await collaboration.run(sessionId, row.id)
    await refresh()
    if (!alive.current) return
    if (current.ok) {setUnknown(false); setError(null); setEditing(false)}
    else setError(current)
  }
  return <li className="space-y-2 break-words border-b border-line py-2">
    <div><span className="text-muted">{row.requester?.display_name || t('ui.shared.user')} · </span>{row.text}</div>
    {error && <p role="alert" className="text-danger">{collaborationFeedbackMessage(error)}</p>}
    {unknown ? <Button size="sm" variant="ghost" onClick={() => void check()}>{t('ui.shared.checkReceipt')}</Button> : editable && <>
      {editing ? <><textarea aria-label={t('ui.shared.editQueued')} value={text} disabled={busy} onChange={event => setText(event.target.value)} className="w-full rounded border border-line bg-surface p-2 text-sm" />
        <Button size="sm" disabled={busy || !text.trim()} onClick={() => void update(false)}>{t('ui.shared.saveQueued')}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>{t('ui.shared.cancelQueued')}</Button></> : <>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => {setText(row.text); setVersion(row.edit_version); setError(null); setEditing(true)}}>{t('ui.shared.editQueued')}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void update(true)}>{t('ui.shared.removeQueued')}</Button></>}
    </>}
  </li>
}
