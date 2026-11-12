import { ResourceError, failureText } from '@/lib/resourceError'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Pin, Upload } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { runtimeRequest } from '@/lib/http'
import { parseMemoryEvidence, updateMemoryEvidenceNote } from '@/lib/memoryEvidence'
import { download } from '@/lib/utils'
import { useAppStore } from '@/store/app'
import { Button, RefreshButton } from '@/components/ui/primitives'
import {MemoryRecallPreview} from './MemoryRecallPreview'

type Memory = { id: string; content: string; topic: string; pinned: boolean; source: string; evidence: string; revision: number }

function Evidence({raw}: {raw: string}) {
  const value = parseMemoryEvidence(raw)
  if (!value) return raw ? <p className="whitespace-pre-wrap break-words text-xs text-muted">{raw}</p> : null
  return <div className="space-y-2 text-xs text-muted">
    {value.note && <p className="whitespace-pre-wrap break-words">{value.note}</p>}
    <p>{t(value.kind === 'files' ? 'ui.memory.fileSources' : 'ui.memory.consolidationInputs')}</p>
    <ul className="space-y-1 pl-4">{value.kind === 'files'
      ? value.files.map((file, index) => <li key={index} className="break-all font-mono">{file.path} · {file.version}</li>)
      : value.inputs.map(input => <li key={input.id} className="break-all font-mono">{input.id} · {t('ui.memory.revision', {revision: input.revision})}</li>)}</ul>
    <details><summary className="cursor-pointer rounded focus-visible:outline-brand">{t('ui.memory.rawEvidence')}</summary><pre className="mt-2 whitespace-pre-wrap break-all font-mono">{raw}</pre></details>
  </div>
}

export function MemoryPanel() {
  useLocale()
  const sessionId = useAppStore(s => s.activeSessionId)
  const [memoryScope, setMemoryScope] = useState<'project' | 'user' | 'session'>('project')
  const [rows, setRows] = useState<Memory[]>([])
  const [rowsScope, setRowsScope] = useState('')
  const [path, setPath] = useState('')
  const [query, setQuery] = useState('')
  const [content, setContent] = useState('')
  const [topic, setTopic] = useState('project')
  const [evidence, setEvidence] = useState('')
  const [pinned, setPinned] = useState(false)
  const [editing, setEditing] = useState<Memory | null>(null)
  const scopeKey = JSON.stringify([sessionId, memoryScope])
  const [deleting, setDeleting] = useState<{scope: string; id: string; revision: number} | null>(null)
  const operationScope = useRef<number | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const generation = useRef(0)
  const reading = useRef<AbortController>()
  const scope = useRef(0)
  const mounted = useRef(false)
  const queryRef = useRef(query)
  queryRef.current = query
  const upload = useRef<HTMLInputElement>(null)
  const contentInput = useRef<HTMLTextAreaElement>(null)
  const base = `/sessions/${encodeURIComponent(sessionId ?? '')}/memory`
  const url = useCallback((suffix = '') => `${base}${suffix}?scope=${memoryScope}`, [base, memoryScope])
  const reset = () => { setContent(''); setTopic('project'); setEvidence(''); setPinned(false); setEditing(null) }
  const load = useCallback(async () => {
    if (!mounted.current || useAppStore.getState().activeSessionId !== sessionId) return
    reading.current?.abort()
    const controller = new AbortController()
    reading.current = controller
    const version = ++generation.current
    setError('')
    if (!sessionId) { setRows([]); setPath(''); return }
    setLoading(true)
    try {
      const result = await runtimeRequest<{ memories: Memory[]; path: string }>(url() + (queryRef.current.trim() ? '&q=' + encodeURIComponent(queryRef.current.trim()) : ''), 'GET', undefined, {signal: controller.signal})
      if (generation.current === version) { setRows(result.memories); setRowsScope(scopeKey); setPath(result.path) }
    } catch (e) { if (generation.current === version) setError(failureText(e)) }
    finally { if (generation.current === version) setLoading(false) }
  }, [sessionId, url, scopeKey])
  useEffect(() => {
    mounted.current = true
    reset(); setDeleting(null); setBusy(false); setNotice(''); setQuery(''); queryRef.current = ''; setRows([]); setPath('')
    return () => { mounted.current = false; scope.current++; generation.current++; reading.current?.abort() }
  }, [load])
  useEffect(() => {
    generation.current++; setLoading(Boolean(sessionId))
    const timer = setTimeout(() => void load(), query.trim() ? 180 : 0)
    return () => { clearTimeout(timer); generation.current++; reading.current?.abort() }
  }, [query, load, sessionId])
  const act = async (operation: (current: () => boolean) => Promise<ResourceText | void>) => {
    const ticket = scope.current
    const current = () => mounted.current && scope.current === ticket && useAppStore.getState().activeSessionId === sessionId
    if (!current() || operationScope.current === ticket) return
    operationScope.current = ticket
    setBusy(true); setError(''); setNotice('')
    try {
      const message = await operation(current)
      if (!current()) return
      await load()
      if (current()) setNotice(message || {key: 'ui.memory.saved'})
    } catch (e) {
      if (current()) {
        await load()
        if (current()) setError(failureText(e))
      }
    }
    finally {
      if (operationScope.current === ticket) operationScope.current = null
      if (current()) setBusy(false)
    }
  }
  const edit = (row: Memory) => {
    setEditing(row); setContent(row.content); setTopic(row.topic); setEvidence(row.evidence); setPinned(row.pinned)
    contentInput.current?.focus()
  }
  const save = async (current: () => boolean) => {
    await runtimeRequest(url(editing ? `/${editing.id}` : ''), editing ? 'PATCH' : 'POST', {
      content, topic, evidence, pinned, ...(editing ? { expected_revision: editing.revision } : {}),
    })
    if (current()) reset()
  }
  const importFile = (file: File) => act(async current => {
    if (file.size > 32 * 1024 * 1024) throw new ResourceError({key: 'ui.memory.fileLarge'})
    const text = await file.text()
    let data: unknown
    try { data = JSON.parse(text) } catch (error) {
      if (error instanceof SyntaxError) throw new ResourceError({key: 'ui.memory.invalidImportJson'})
      throw error
    }
    if (!current()) return
    const result = await runtimeRequest<{ imported: number; duplicates: number }>(url('/import'), 'POST', data)
    return {key: 'ui.memory.imported', params: {count: result.imported, duplicates: result.duplicates}}
  })
  const visible = rowsScope === scopeKey ? rows : []
  const structuredEvidence = parseMemoryEvidence(evidence)
  const evidenceTooLarge = Array.from(evidence).length > 1000
  const field = 'w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm'
  if (!sessionId) return <p className="text-sm text-muted">{t('ui.memory.noSession')}</p>
  return <section className="refresh-action-container space-y-4" aria-label={t('ui.memory.heading')}>
    <div className="flex items-start justify-between gap-3"><div><h3 className="ui-section-title">{t('ui.memory.heading')}</h3><p className="mt-1 text-xs text-muted">{t('ui.memory.description')}</p></div><RefreshButton size="sm" label={t('ui.memory.refresh')} disabled={busy || loading} onClick={() => void load()} /></div>
    <label className="block space-y-1 text-xs"><span>{t('ui.memory.scope')}</span><select aria-label={t('ui.memory.scope')} className={field} disabled={busy} value={memoryScope} onChange={event => { const next = event.target.value as typeof memoryScope; if (next === memoryScope) return; generation.current++; scope.current++; setRows([]); setPath(''); setMemoryScope(next) }}><option value="project">{t('ui.memory.scope.project')}</option><option value="user">{t('ui.memory.scope.user')}</option><option value="session">{t('ui.memory.scope.session')}</option></select></label>
    <p className="text-xs text-muted">{t(`ui.memory.scope.${memoryScope}.hint`)}</p>
    <MemoryRecallPreview key={`${sessionId}:${memoryScope}`} sessionId={sessionId} />
    {error && <p role="alert" className="break-words text-sm text-danger">{noticeText(error)}</p>}
    {notice && <p role="status" className="break-words text-xs text-brand">{noticeText(notice)}</p>}
    {path && <p className="break-all text-xs text-muted">{path}</p>}
    <form className="space-y-3 border-l-2 border-brand pl-4" onSubmit={event => { event.preventDefault(); void act(save) }}>
      <label className="block space-y-1 text-xs"><span>{t('ui.memory.content')}</span><textarea ref={contentInput} aria-label={t('ui.memory.content')} required disabled={busy || loading} className={field} rows={3} maxLength={4000} placeholder={t('ui.memory.contentHint')} value={content} onChange={event => setContent(event.target.value)} /></label>
      <div className="grid gap-3 sm:grid-cols-2"><label className="block space-y-1 text-xs"><span>{t('ui.memory.topic')}</span><input required disabled={busy} className={field} maxLength={100} value={topic} onChange={event => setTopic(event.target.value)} /></label><label className="block space-y-1 text-xs"><span>{t('ui.memory.evidence')}</span><input disabled={busy} className={field} maxLength={1000} value={structuredEvidence?.note ?? evidence} onChange={event => setEvidence(updateMemoryEvidenceNote(evidence, event.target.value))} /></label></div>
      {structuredEvidence && <p className="text-xs text-muted">{t('ui.memory.keepReferences')}</p>}
      {evidenceTooLarge && <p role="alert" className="text-xs text-danger">{t('ui.memory.evidenceTooLarge')}</p>}
      <label className="flex items-center gap-2 text-xs"><Switch disabled={busy} checked={pinned} onChange={event => setPinned(event.target.checked)} />{t('ui.memory.pinned')}</label>
      <p className="text-xs text-muted">{t('ui.memory.privacy')}</p>
      <div className="flex flex-wrap gap-2"><Button type="submit" variant="primary" loading={busy} disabled={loading || evidenceTooLarge || !content.trim() || !topic.trim()}>{t(editing ? 'ui.memory.update' : 'ui.memory.save')}</Button>{editing && <Button disabled={busy} onClick={reset}>{t('ui.memory.cancel')}</Button>}</div>
    </form>
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" disabled={busy || loading} icon={<Download className="h-3.5 w-3.5" />} onClick={() => void act(async current => { const data = await runtimeRequest(url('/export')); if (!current()) return; download(`${memoryScope}-memory.json`, JSON.stringify(data, null, 2), 'application/json'); return {key: 'ui.memory.exported'} })}>{t('ui.memory.export')}</Button>
      <Button size="sm" disabled={busy || loading} icon={<Upload className="h-3.5 w-3.5" />} onClick={() => upload.current?.click()}>{t('ui.memory.import')}</Button>
      <input ref={upload} hidden type="file" accept=".json,application/json" aria-label={t('ui.memory.import')} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file) }} />
      {!loading && visible.length > 0 ? <span className="text-xs text-muted">{t('ui.memory.count', { count: visible.length })}</span> : null}
    </div>
    <input aria-label={t('ui.memory.search')} placeholder={t('ui.memory.search')} className={field} value={query} maxLength={500} onChange={event => { generation.current++; setRows([]); setQuery(event.target.value) }} />
    {loading && <p role="status" className="text-sm text-muted">{t('ui.memory.loading')}</p>}
    {!loading && !error && !visible.length && <p className="py-4 text-sm text-muted">{t(query.trim() ? 'ui.memory.noResults' : 'ui.memory.empty')}</p>}
    <div className="divide-y divide-line" aria-busy={loading}>{visible.map(row => <article key={row.id} className="space-y-2 py-3">
      <p className="flex items-center gap-2 text-xs text-muted">{row.pinned && <Pin className="h-3.5 w-3.5" />}{row.topic}</p>
      <p className="whitespace-pre-wrap break-words text-sm">{row.content}</p>
      <Evidence raw={row.evidence} />
      <p className="break-all text-xs text-faint">{t('ui.memory.source', { source: row.source || 'import' })}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy} onClick={() => edit(row)}>{t('ui.memory.edit')}</Button>
        <Button size="sm" disabled={busy || editing?.id === row.id} onClick={() => void act(async () => { await runtimeRequest(url(`/${row.id}`), 'PATCH', { ...row, pinned: !row.pinned, expected_revision: row.revision }) })}>{t(row.pinned ? 'ui.memory.unpin' : 'ui.memory.pin')}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDeleting({scope: scopeKey, id: row.id, revision: row.revision})}>{t('ui.memory.delete')}</Button>
      </div>
      {deleting?.scope === scopeKey && deleting.id === row.id && deleting.revision === row.revision ? <div role="group" aria-label={t('ui.memory.deleteConfirm')} className="space-y-2 rounded-lg border border-danger/30 bg-danger/5 p-3">
        <p className="text-xs text-danger">{t('ui.memory.deleteConfirm')}</p>
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy} onClick={() => void act(async current => {
          if (!current()) return
          await runtimeRequest(url(`/${row.id}`), 'DELETE', { expected_revision: row.revision })
          if (current()) { setDeleting(null); if (editing?.id === row.id) reset() }
        })}>{t('ui.memory.confirmDelete')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => setDeleting(null)}>{t('ui.memory.cancelDelete')}</Button></div>
      </div> : null}
    </article>)}</div>
  </section>
}
