import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { GitFileStatus } from './GitFileStatus'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import {gitFailureText, readGitRepository, readGitPatch, writeGitOnce, type GitRepository, type GitPatch} from '@/lib/gitStaging'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { DiffView } from '@/components/chat/blocks/DiffView'
import { gitDraftScope, useGitDraftStore } from '@/store/gitDrafts'

type Props = {sessionId: string; repository: GitRepository; busy: boolean; onBusy: (value: boolean) => void; onUpdate: (value: GitRepository) => void; active?: boolean}
export function GitStaging(props: Props) {
  return <GitStagingContent key={gitDraftScope(props.sessionId, props.repository.root)} {...props} />
}
function GitStagingContent({ sessionId, repository, busy, onBusy, onUpdate, active = true }: Props) {
  useLocale()
  const draftScope = gitDraftScope(sessionId, repository.root)
  const message = useGitDraftStore(state => state.byScope[draftScope] ?? '')
  const storageError = useGitDraftStore(state => state.storageError)
  const storageCorrupt = useGitDraftStore(state => state.storageCorrupt)
  const setMessage = (value: string) => useGitDraftStore.getState().setMessage(draftScope, value)
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const [patch, setPatch] = useState<GitPatch | null>(null)
  const [loading, setLoading] = useState(false)
  const [selection, setSelection] = useState<{scope: string; paths: string[]}>({scope: draftScope, paths: []})
  const selected = selection.scope === draftScope ? selection.paths.filter(path => repository.files.some(file => file.path === path)) : []
  const stagedSelection = selected.filter(path => repository.staged_files.some(file => file.path === path))
  const mounted = useRef(false)
  const operation = useRef(false)
  useLayoutEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  const generation = useRef(0)
  const reading = useRef<AbortController>()
  useEffect(() => {
    if (!active) {reading.current?.abort(); setLoading(false)}
    return () => reading.current?.abort()
  }, [active])
  const base = '/sessions/' + encodeURIComponent(sessionId) + '/git'
  useEffect(() => { setPatch(null); setLoading(false); return () => { generation.current++; reading.current?.abort() } }, [draftScope, repository.index_revision])
  useEffect(() => {
    setSelection(current => {
      const paths = current.scope === draftScope ? current.paths.filter(path => repository.files.some(file => file.path === path)) : []
      return current.scope === draftScope && paths.length === current.paths.length ? current : {scope: draftScope, paths}
    })
  }, [draftScope, repository.files])
  async function mutate(request: {action: 'commit'; message: string} | {action: 'stage' | 'unstage'; paths: string[]}) {
    const {action, ...payload} = request
    if (!active || busy || loading || operation.current) return
    operation.current = true
    generation.current++; reading.current?.abort()
    onBusy(true); setError(''); setNotice('')
    const submitted = request.action === 'commit' ? request.message : null
    const submittedRevision = useGitDraftStore.getState().revisionByScope[draftScope] ?? 0
    const write = writeGitOnce(base, repository.root, action, {...payload, expected_revision: repository.index_revision})
    try {
      const result = await write.result
      if (write.started && result.confirmed && result.commit && submitted !== null && useGitDraftStore.getState().byScope[draftScope] === submitted && (useGitDraftStore.getState().revisionByScope[draftScope] ?? 0) === submittedRevision) setMessage('')
      if (!mounted.current) return
      if (result.repository) onUpdate(result.repository)
      if (result.error) setError(result.error)
      else if (!write.started) setNotice({key: 'ui.git.operationObserved'})
      else if (result.commit) setNotice({key: 'ui.git.saved', params: {commit: result.commit.slice(0, 12)}})
      if (write.started && result.confirmed) {
        const paths = request.action === 'commit' ? [] : request.paths
        setSelection(current => ({...current, paths: current.paths.filter(path => !paths.includes(path))}))
      }
    } finally { operation.current = false; onBusy(false) }
  }
  async function inspect(path: string) {
    if (!active || operation.current || busy) return
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    const ticket = ++generation.current
    setLoading(true); setError(''); setPatch(null)
    try {
      const result = await readGitPatch(base, path, repository.index_revision, controller.signal)
      if (!controller.signal.aborted && generation.current === ticket) setPatch(result)
    } catch (failure) { if (!controller.signal.aborted && generation.current === ticket) setError(gitFailureText(failure)) }
    finally { if (!controller.signal.aborted && generation.current === ticket) setLoading(false) }
  }
  async function refresh() {
    if (!active || operation.current || busy) return
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    const ticket = ++generation.current
    setLoading(true); setError('')
    try { const result = await readGitRepository(base, repository.root, controller.signal); if (!controller.signal.aborted && generation.current === ticket) onUpdate(result) }
    catch (failure) { if (!controller.signal.aborted && generation.current === ticket) setError(gitFailureText(failure)) }
    finally { if (!controller.signal.aborted && generation.current === ticket) setLoading(false) }
  }
  return <section aria-label={t('ui.git.heading')} className="refresh-action-container min-w-0 space-y-3 border-t border-line pt-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium">{t('ui.git.heading')}</h4><RefreshButton size="xs" label={t('ui.worktrees.refresh')} disabled={busy || loading} onClick={() => void refresh()} /></div>
    <p className="text-xs text-muted">{t('ui.git.explain')}</p>
    {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
    {notice && <p role="status" className="break-words text-xs text-ok">{noticeText(notice)}</p>}
    {repository.conflicts && <p className="text-xs text-danger">{t('ui.git.conflicts')}</p>}
    {Boolean(repository.files.length) && <><div className="flex flex-wrap gap-2"><Button size="xs" disabled={busy || loading} onClick={() => void mutate({action: 'stage', paths: repository.files.map(file => file.path)})}>{t('ui.git.stageAll')}</Button><Button size="xs" disabled={busy || loading || !selected.length} onClick={() => void mutate({action: 'stage', paths: selected})}>{t('ui.git.stageSelected', {count: selected.length})}</Button><Button size="xs" disabled={busy || loading || !stagedSelection.length} onClick={() => void mutate({action: 'unstage', paths: stagedSelection})}>{t('ui.git.unstageSelected', {count: stagedSelection.length})}</Button>{selected.length > 0 && <Button size="xs" variant="ghost" disabled={busy || loading} onClick={() => setSelection({scope: draftScope, paths: []})}>{t('ui.git.clearSelection')}</Button>}</div><div className="max-h-60 space-y-2 overflow-auto">{repository.files.map(file => <div key={file.path} className="min-w-0 rounded border border-line p-2"><label className="flex min-w-0 items-start gap-2"><input type="checkbox" className="mt-0.5 shrink-0" disabled={busy || loading} aria-label={t('ui.git.selectFile', {path: file.path})} checked={selected.includes(file.path)} onChange={event => setSelection({scope: draftScope, paths: event.target.checked ? [...selected, file.path] : selected.filter(path => path !== file.path)})}/><GitFileStatus status={file.status} /><span className="min-w-0 break-all font-mono text-2xs">{file.original_path ? file.original_path + ' → ' : ''}{file.path}</span></label>
      {file.status !== '??' && file.status[0] !== ' ' && file.status[1] !== ' ' && <p className="mt-1 text-2xs text-warn">{t('ui.git.partial')}</p>}
      <div className="mt-1 flex flex-wrap gap-2"><Button size="xs" disabled={busy || loading} onClick={() => void mutate({action: 'stage', paths:[file.path]})}>{t('ui.git.stage', {path:file.path})}</Button>{file.status !== '??' && file.status[0] !== ' ' && <Button size="xs" disabled={busy || loading} onClick={() => void mutate({action: 'unstage', paths:[file.path]})}>{t('ui.git.unstage', {path:file.path})}</Button>}</div>
    </div>)}</div></>}
    <p className="text-xs font-medium">{t('ui.git.staged', {count:repository.staged_files.length})}</p>
    <div className="flex flex-wrap gap-2">{repository.staged_files.map(file => <Button key={file.path} size="xs" disabled={busy || loading} onClick={() => void inspect(file.path)}>{t('ui.git.inspect', {path:file.path})}</Button>)}</div>
    {loading && <p role="status" className="text-xs text-muted">{t('ui.worktree.review.loading')}</p>}
    {patch && <div className="min-w-0 space-y-2"><p className="break-all text-2xs text-muted">{patch.path}</p>{patch.truncated && <p className="text-xs text-warn">{t('ui.git.truncated')}</p>}{patch.binary ? <p className="text-xs text-muted">{t('ui.git.binary')}</p> : <DiffView output={{diff:patch.diff}} path={patch.path} />}</div>}
    <label className="block space-y-1 text-xs"><span>{t('ui.git.message')}</span><textarea aria-label={t('ui.git.message')} rows={2} maxLength={8000} disabled={busy} value={message} onChange={event => setMessage(event.target.value)} className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm" /></label>
    {storageError && <div role="alert" className="space-y-1 text-xs text-warn"><p>{t(storageCorrupt ? 'ui.git.draftCorrupt' : 'ui.git.draftUnpersisted')}</p>{!storageCorrupt && <Button size="xs" variant="ghost" onClick={() => useGitDraftStore.getState().retryStorage()}>{t('ui.git.retryDraft')}</Button>}</div>}
    <Button size="sm" variant="primary" disabled={busy || loading || !message.trim() || !repository.staged_files.length || repository.conflicts} onClick={() => void mutate({action: 'commit', message})}>{t('ui.git.commit')}</Button>
  </section>
}
