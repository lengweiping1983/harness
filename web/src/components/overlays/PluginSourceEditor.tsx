import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import type { PluginSourceDirectory, PluginSourceFile } from '@/lib/protocol.generated'
import { Button, Spinner } from '@/components/ui/primitives'
import { guardSourceDrafts, sourceDirty, sourceKey, usePluginSourceDrafts } from '@/store/pluginSourceDrafts'

export function PluginSourceEditor({ root, onReview, onChanged }: { root: string; onReview: (root: string) => void; onChanged: () => void }) {
  useLocale()
  const state = usePluginSourceDrafts()
  const [directory, setDirectory] = useState<PluginSourceDirectory | null>(null)
  const [file, setFile] = useState('')
  const [newFile, setNewFile] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const busy = loading || saving
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [removing, setRemoving] = useState(false)
  const epoch = useRef(0)
  const alive = useRef(true)
  const gate = useRef(false)
  const key = sourceKey(directory?.root ?? root, file)
  const draft = state.drafts[key]
  const dirty = !!draft && sourceDirty(draft)
  const otherDirty = directory && Object.values(state.drafts).some(item => item.original.root === directory.root && sourceDirty(item))
  const url = (file?: string) => '/plugin-packages/source' + (file ? '/file' : '') + '?path=' + encodeURIComponent(directory?.root ?? root) + (file ? '&file=' + encodeURIComponent(file) : '')
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    guardSourceDrafts()
    const request = ++epoch.current, controller = new AbortController()
    setDirectory(null); setFile(''); setLoading(true); setError('')
    void runtimeRequest<PluginSourceDirectory>('/plugin-packages/source?path=' + encodeURIComponent(root), 'GET', undefined, { signal: controller.signal }).then(result => {
      if (request !== epoch.current) return
      setDirectory(result)
      const selected = usePluginSourceDrafts.getState().selected[result.root]
      setFile(selected && (result.files.includes(selected) || usePluginSourceDrafts.getState().drafts[sourceKey(result.root, selected)]) ? selected : result.files[0] ?? '')
    }).catch(failure => { if (request === epoch.current) setError(failureText(failure)) }).finally(() => { if (request === epoch.current) setLoading(false) })
    return () => { epoch.current++; controller.abort() }
  }, [root])
  useEffect(() => {
    if (!directory || !file || usePluginSourceDrafts.getState().drafts[sourceKey(directory.root, file)]) return
    const request = ++epoch.current, controller = new AbortController()
    setLoading(true); setError('')
    void runtimeRequest<PluginSourceFile>(url(file), 'GET', undefined, { signal: controller.signal }).then(result => {
      if (request === epoch.current) usePluginSourceDrafts.getState().load(result)
    }).catch(failure => { if (request === epoch.current) setError(failureText(failure)) }).finally(() => { if (request === epoch.current) setLoading(false) })
    return () => { epoch.current++; controller.abort() }
  }, [directory, file])
  const choose = (name: string) => { if (directory) { state.select(directory.root, name); setFile(name); setError(''); setNotice(''); setRemoving(false) } }
  const run = async (action: () => Promise<void>) => {
    if (gate.current) return
    gate.current = true; setSaving(true); setError(''); setNotice('')
    try { await action() } catch (failure) { if (alive.current) setError(failure instanceof ApiError && isUnconfirmedWrite(failure.failure) ? {template: {key: 'ui.extensions.unknownResult', params: {reason: failureText(failure)}}} : failureText(failure)) }
    finally { gate.current = false; if (alive.current) setSaving(false) }
  }
  const save = () => {
    if (!draft || !directory) return
    const original = draft.original, submitted = { path: directory.root, file, revision: original.revision, content: draft.content, executable: draft.executable }
    void run(async () => {
      let saved: PluginSourceFile
      try {
        saved = await runtimeRequest<PluginSourceFile>('/plugin-packages/source/file', 'PATCH', submitted, { timeout: 120_000 })
        if (saved.root !== submitted.path || saved.file !== submitted.file ||
            saved.content.replace(/\r\n?/g, '\n') !== submitted.content || saved.executable !== submitted.executable) {
          throw new ApiError({ok: false, status: 200, code: 'response/invalid-identity',
            message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}})
        }
      }
      catch (failure) {
        if (failure instanceof ApiError && isUnconfirmedWrite(failure.failure)) {
          let actual: PluginSourceFile | undefined
          try { actual = await runtimeRequest<PluginSourceFile>(url(file)) }
          catch { /* 未知结果只读回，不重放；保留本地草稿。 */ }
          if (!actual || actual.root !== submitted.path || actual.file !== submitted.file ||
              actual.content.replace(/\r\n?/g, '\n') !== submitted.content || actual.executable !== submitted.executable) throw failure
          saved = actual
        } else throw failure
      }
      usePluginSourceDrafts.getState().saved(saved, submitted.revision)
      if (alive.current) { onChanged(); setNotice({key: 'ui.extensions.sourceSaved'}) }
    })
  }
  const remove = () => {
    if (!draft || !directory) return
    void run(async () => {
      let result: PluginSourceDirectory
      try { result = await runtimeRequest<PluginSourceDirectory>('/plugin-packages/source/file', 'DELETE', { path: directory.root, file, revision: draft.original.revision }, { timeout: 120_000 }) }
      catch (failure) {
        if (!(failure instanceof ApiError) || !isUnconfirmedWrite(failure.failure)) throw failure
        result = await runtimeRequest<PluginSourceDirectory>(url())
        if (result.root !== directory.root || result.files.includes(file)) throw failure
      }
      state.removed(directory.root, file, draft.original.revision)
      if (alive.current) { setDirectory(result); setRemoving(false); choose(result.files[0] ?? ''); onChanged() }
    })
  }
  const choices = [...new Set([...(directory?.files ?? []), ...Object.values(state.drafts).filter(item => item.original.root === directory?.root).map(item => item.original.file)])].sort()
  return <section aria-label={t('ui.extensions.sourceEditor')} className="min-w-0 space-y-3 rounded-xl border border-line p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium text-ink">{t('ui.extensions.sourceEditor')}</h4><Button size="sm" variant="ghost" disabled={busy} onClick={() => state.open('')}>{t('ui.extensions.closeEditor')}</Button></div>
    <p className="break-all font-mono text-2xs text-muted">{directory?.root ?? root}</p>
    <p className="text-xs leading-relaxed text-muted">{t(directory?.read_only ? 'ui.extensions.cacheReadOnly' : 'ui.extensions.sourceEditorHint')}</p>
    {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
    {notice && <p role="status" className="text-xs text-brand">{noticeText(notice)}</p>}
    {busy && <p role="status" className="flex items-center gap-2 text-xs text-muted"><Spinner className="h-3.5 w-3.5" />{t('ui.extensions.loading')}</p>}
    {directory && <>
      <select aria-label={t('ui.extensions.sourceFile')} className="ui-field font-mono text-xs" value={file} disabled={busy} onChange={event => choose(event.target.value)}>{choices.map(name => <option key={name} value={name}>{name}{state.drafts[sourceKey(directory.root, name)] && sourceDirty(state.drafts[sourceKey(directory.root, name)]!) ? ' *' : ''}</option>)}</select>
      {!directory.read_only && <form className="flex flex-wrap gap-2" onSubmit={event => {
        event.preventDefault()
        if (!newFile.trim()) return
        const name = newFile.trim()
        if (name.includes('\\') || name.includes(':') || name.split('/').some(part => !part || part === '.' || part === '..')) { setError({key: 'ui.extensions.relativeSourceFile'}); return }
        if (choices.includes(name)) { choose(name); setNewFile(''); return }
        state.load({ root: directory.root, file: name, content: '', revision: 'missing', executable: false, read_only: false })
        choose(name); setNewFile(''); onChanged()
      }}><input aria-label={t('ui.extensions.newSourceFile')} className="ui-field min-w-0 flex-1 font-mono text-xs" value={newFile} disabled={busy} onChange={event => setNewFile(event.target.value)} placeholder="skills/my-workflow/SKILL.md" /><Button size="sm" type="submit" disabled={busy || !newFile.trim()}>{t('ui.extensions.addSourceFile')}</Button></form>}
      {draft && <><textarea aria-label={t('ui.extensions.sourceContent')} spellCheck={false} className="ui-field min-h-64 w-full resize-y font-mono text-xs leading-relaxed" value={draft.content} readOnly={directory.read_only} disabled={busy} onChange={event => { state.edit(key, { content: event.target.value }); onChanged() }} />
        <label className="flex items-center gap-2 text-xs text-muted"><Switch checked={draft.executable} disabled={busy || directory.read_only || !directory.executable_supported} onChange={event => { state.edit(key, { executable: event.target.checked }); onChanged() }} />{t('ui.extensions.sourceExecutable')}</label>
        <div className="flex flex-wrap gap-2">
          {!directory.read_only && <Button size="sm" disabled={busy || !dirty} onClick={save}>{t('ui.extensions.saveSource')}</Button>}
          <Button size="sm" variant="secondary" disabled={busy || !!otherDirty || directory.read_only} onClick={() => onReview(directory.root)}>{t('ui.extensions.reviewSource')}</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => { state.discard(directory.root, file); if (draft.original.revision === 'missing') choose(directory.files[0] ?? ''); setDirectory({ ...directory }); setRemoving(false) }}>{t('ui.extensions.reloadSource')}</Button>
          {!directory.read_only && !['plugin.json', '.codex-plugin/plugin.json'].includes(file) && <Button size="sm" variant="ghost" disabled={busy || dirty} onClick={() => setRemoving(true)}>{t('ui.extensions.deleteSource')}</Button>}
        </div>
        {removing && <div className="flex flex-wrap items-center gap-2 text-xs text-warn"><p>{t('ui.extensions.deleteSourceConfirm', { file })}</p><Button size="sm" variant="danger" disabled={busy} onClick={remove}>{t('ui.extensions.confirmRemove')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => setRemoving(false)}>{t('ui.extensions.cancel')}</Button></div>}
      </>}
    </>}
  </section>
}
