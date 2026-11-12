import type {ResourceText} from '@/types'
import {ResourceError, failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { GitFileStatus } from './GitFileStatus'
import { useCallback, useEffect, useRef, useState } from 'react'
import { GitBranch } from 'lucide-react'
import { WorktreeEnvironment } from './WorktreeEnvironment'
import { t, useLocale } from '@/i18n'
import { runtimeRequest, ApiError } from '@/lib/http'
import { api, unwrap } from '@/lib/api'
import { useAppStore } from '@/store/app'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { basenameOf, normalizePath, sessionDirectory } from '@/lib/workspaces'
import { WorktreeReview } from './WorktreeReview'
import {GitStaging} from './GitStaging'
import {gitFailureText, type GitRepository} from '@/lib/gitStaging'
import { worktreeCatalog, type ManagedWorktree, type WorktreeStatusFilter } from '@/lib/worktreeCatalog'
import { prepareCreatedWorktree } from '@/lib/environmentImport'
import { useWorktreeCatalogFilters } from '@/store/worktreeCatalog'
import { usePageVisible } from '@/hooks/usePageVisible'

type Worktree = ManagedWorktree
type Repository = GitRepository

export function WorktreesPanel({ active = true }: { active?: boolean }) {
  useLocale()
  const visible = usePageVisible() && active
  const sessionId = useAppStore(s => s.activeSessionId)
  const sessions = useAppStore(s => s.sessions)
  const cwd = sessionDirectory(sessions.find(s => s.id === sessionId) ?? null)
  const [rows, setRows] = useState<Worktree[]>([])
  const scope = sessionId + '\0' + (cwd ?? '')
  const [repositoryData, setRepositoryData] = useState<{ scope: string; repository: Repository | null } | null>(null)
  const repository = repositoryData?.scope === scope ? repositoryData.repository : null
  const [ref, setRef] = useState('HEAD')
  const [busy, setBusy] = useState(false)
  const operationScopes = useRef(new Set<string>())
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [prepareEnvironment, setPrepareEnvironment] = useState(true)
  const [environmentFocus, setEnvironmentFocus] = useState<{scope: string; id: string} | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<ResourceText>('')
  const [operationError, setOperationError] = useState<ResourceText>('')
  const [uninitialized, setUninitialized] = useState<{scope: string; cwd: string} | null>(null)
  const query = useWorktreeCatalogFilters(state => state.query)
  const statusFilter = useWorktreeCatalogFilters(state => state.status)
  const currentProjectOnly = useWorktreeCatalogFilters(state => state.currentProjectOnly)
  const updateFilters = useWorktreeCatalogFilters(state => state.update)
  const catalog = worktreeCatalog(rows, sessions, { cwd, repositoryRoot: repository?.root, query, status: statusFilter, currentProjectOnly })
  const generation = useRef(0)
  const readController = useRef<AbortController>()
  const load = useCallback(async () => {
    if (!visible) return
    readController.current?.abort()
    const controller = new AbortController()
    readController.current = controller
    const version = ++generation.current
    setLoading(true); setLoadError('')
    const [worktrees, git] = await Promise.allSettled([
      runtimeRequest<{ worktrees: Worktree[] }>('/worktrees', 'GET', undefined, {signal: controller.signal}),
      sessionId ? runtimeRequest<Repository>(`/sessions/${encodeURIComponent(sessionId)}/git`, 'GET', undefined, {signal: controller.signal}).then(value => {
        if (generation.current === version && !controller.signal.aborted) {
          setRepositoryData({scope, repository: value})
          setUninitialized(null)
        }
        return value
      }) : Promise.resolve(null),
    ])
    if (generation.current !== version || controller.signal.aborted) return
    if (worktrees.status === 'fulfilled') setRows(worktrees.value.worktrees)
    else { setRows([]); setLoadError(failureText(worktrees.reason)) }
    setUninitialized(null)
    if (git.status === 'fulfilled') setRepositoryData({ scope, repository: git.value })
    else {
      setRepositoryData({ scope, repository: null })
      const failure = git.reason
      const details = failure instanceof ApiError ? failure.details as {cwd?: unknown} | undefined : undefined
      if (failure instanceof ApiError && failure.code === 'git/not-repository' && typeof details?.cwd === 'string') setUninitialized({scope, cwd: details.cwd})
      else setLoadError(gitFailureText(failure))
    }
    setLoading(false)
  }, [sessionId, cwd, scope, visible])
  useEffect(() => { setRef('HEAD'); setBusy(operationScopes.current.has(scope)); setOperationError(''); setLoadError('') }, [scope])
  useEffect(() => {
    if (visible && !busy) void load()
    return () => { generation.current++; readController.current?.abort() }
  }, [load, visible, busy])
  const open = async (row: Worktree) => {
    if (currentScope.current !== scope) return
    const store = useAppStore.getState()
    const existing = store.sessions.find(s => normalizePath(sessionDirectory(s)) === normalizePath(row.path))
    const id = existing?.id ?? (await unwrap(api.createSession({ cwd: row.path }))).session.id
    await store.refreshSessions()
    const current = useAppStore.getState()
    if (current.activeSessionId === sessionId) {
      current.selectSession(id); current.setSettingsOpen(false)
    }
  }
  const act = async (operation: () => Promise<void>) => {
    if (operationScopes.current.has(scope)) return
    operationScopes.current.add(scope)
    generation.current++
    setBusy(true); setOperationError('')
    try {
      await operation()
      useAppStore.getState().toast({ title: {key: 'ui.worktrees.saved'}, tone: 'success' })
    } catch (e) { if (currentScope.current === scope) setOperationError(gitFailureText(e)) }
    finally { operationScopes.current.delete(scope); if (currentScope.current === scope) setBusy(false) }
  }
  const create = async () => {
    if (!repository) return
    const row = await runtimeRequest<Worktree>('/worktrees', 'POST', { repo: cwd ?? repository.root, ref: ref.trim() || 'HEAD' })
    if (currentScope.current === scope) setRows(previous => [...previous, row])
    if (prepareEnvironment && sessionId) {
      try { await prepareCreatedWorktree(sessionId, row.id) }
      catch (e) {
        if (currentScope.current === scope) setEnvironmentFocus({scope, id: row.id})
        throw new ResourceError({template: {key: 'ui.worktrees.environmentPartial', params: {reason: failureText(e)}}})
      }
    }
    try { await open(row) }
    catch (e) { throw new ResourceError({template: {key: 'ui.worktrees.partial', params: {reason: failureText(e)}}}) }
  }
  const restoreAndOpen = async (row: Worktree) => {
    const restored = await runtimeRequest<Worktree>(`/worktrees/${row.id}/restore`, 'POST')
    if (currentScope.current === scope) setRows(previous => previous.map(item => item.id === row.id ? restored : item))
    try { await open(restored) }
    catch (e) { throw new ResourceError({template: {key: 'ui.worktrees.restorePartial', params: {reason: failureText(e)}}}) }
  }
  return <section className="refresh-action-container space-y-5" aria-label={t('ui.worktrees.heading')}>
    <div className="flex items-start justify-between gap-3">
      <div><h3 className="font-medium">{t('ui.worktrees.heading')}</h3><p className="mt-1 text-xs text-muted">{t('ui.worktrees.description')}</p></div>
      <RefreshButton size="sm" label={t('ui.worktrees.refresh')} disabled={busy || loading} onClick={() => void load()} />
    </div>
    {loading && !repository && !uninitialized && <div role="status" aria-label={t('ui.changes.loading')} className="space-y-3 rounded-xl border border-line p-4 motion-safe:animate-pulse"><div aria-hidden="true" className="h-4 w-1/2 rounded bg-raised" /><div aria-hidden="true" className="h-20 rounded bg-raised" /><p className="text-xs text-muted">{t('ui.changes.loading')}</p></div>}
    {loadError && <p role="alert" className="text-sm text-danger">{noticeText(loadError)}</p>}
    {operationError && <p role="alert" className="text-sm text-danger">{noticeText(operationError)}</p>}
    {!sessionId && <p className="text-sm text-muted">{t('ui.worktrees.noSession')}</p>}
    {sessionId && uninitialized?.scope === scope && <div className="space-y-3 rounded-lg border border-line bg-raised/50 p-4" data-git-onboarding="true">
      <div className="flex items-center gap-2 text-sm font-medium"><GitBranch className="h-4 w-4 text-brand" />{t('ui.git.initializeTitle')}</div>
      <p className="text-xs leading-5 text-muted">{t('ui.git.initializeDescription')}</p>
      <p className="break-all font-mono text-2xs text-muted">{uninitialized.cwd}</p>
      <Button variant="primary" loading={busy} disabled={loading || busy} onClick={() => void act(async () => {
        const next = await runtimeRequest<Repository>(`/sessions/${encodeURIComponent(sessionId)}/git/init`, 'POST', {expected_cwd: uninitialized.cwd})
        if (currentScope.current === scope) { setRepositoryData({scope, repository: next}); setUninitialized(null) }
      })}>{t('ui.git.initialize')}</Button>
    </div>}
    {repository && <div className="space-y-3 border-l-2 border-brand pl-4">
      <p className="break-all text-xs text-muted">{repository.root}</p>
      <p className="flex items-center gap-2 text-sm font-medium"><GitBranch className="h-4 w-4" />{repository.branch ?? t('ui.worktrees.detached')}</p>
      <p className="text-xs text-muted">{repository.files.length ? t('ui.worktrees.changes', { count: repository.files.length }) : t('ui.worktrees.clean')}</p>
      {repository.files.length > 0 && <details><summary className="cursor-pointer text-xs text-muted">{t('ui.worktrees.changes', { count: repository.files.length })}</summary><ul className="mt-2 max-h-40 space-y-1 overflow-auto">{repository.files.map(file => <li key={file.path} className="flex gap-3 text-xs"><GitFileStatus status={file.status} /><span className="break-all">{file.original_path ? `${file.original_path} → ` : ''}{file.path}</span></li>)}</ul></details>}
      {sessionId && <GitStaging key={scope} active={visible} sessionId={sessionId} repository={repository} busy={busy || loading} onBusy={value => { if (value) operationScopes.current.add(scope); else operationScopes.current.delete(scope); if (currentScope.current === scope) { if (value) generation.current++; setBusy(value) } }} onUpdate={value => setRepositoryData({ scope, repository: value })} />}
      <label className="block space-y-1 text-xs"><span>{t('ui.worktrees.ref')}</span><input value={ref} disabled={busy} list="worktree-refs" onChange={e => setRef(e.target.value)} className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm" /><datalist id="worktree-refs"><option value="HEAD" />{repository.refs.map(value => <option key={value} value={value} />)}</datalist></label>
      <p className="text-xs text-muted">{t('ui.worktrees.isolated')}</p>
      <label className="flex items-start gap-2 text-xs"><Switch className="mt-0.5 shrink-0" checked={prepareEnvironment} disabled={busy} onChange={event => setPrepareEnvironment(event.target.checked)} /><span>{t('ui.worktrees.prepareEnvironment')}</span></label>
      {!repository.head && <p className="text-xs text-warn">{t('ui.git.firstCommit')}</p>}
      <Button variant="primary" loading={busy} disabled={loading || !ref.trim() || (!repository.head && ref.trim() === 'HEAD')} icon={<GitBranch className="h-4 w-4" />} onClick={() => void act(create)}>{t('ui.worktrees.create')}</Button>
    </div>}
    <div className="space-y-3" aria-busy={loading}>
      {rows.length > 0 && <div className="space-y-2">
        <label className="block space-y-1 text-xs"><span>{t('ui.worktrees.search')}</span><input type="search" value={query} onChange={event => updateFilters({ query: event.target.value })} placeholder={t('ui.worktrees.searchPlaceholder')} className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm" /></label>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs"><span>{t('ui.worktrees.statusFilter')}</span><select aria-label={t('ui.worktrees.statusFilter')} value={statusFilter} onChange={event => updateFilters({ status: event.target.value as WorktreeStatusFilter })} className="rounded-lg border border-line bg-canvas px-2 py-1.5"><option value="all">{t('ui.worktrees.allStatuses')}</option><option value="active">{t('ui.worktrees.active')}</option><option value="archived">{t('ui.worktrees.archived')}</option></select></label>
          <label className="flex items-center gap-2 text-xs"><Switch checked={currentProjectOnly} onChange={event => updateFilters({ currentProjectOnly: event.target.checked })} />{t('ui.worktrees.currentProjectOnly')}</label>
        </div>
        <p className="text-2xs text-faint" role="status">{t('ui.worktrees.filteredCount', { count: catalog.length, total: rows.length })}</p>
      </div>}
      {!rows.length && !loading && <p className="py-4 text-sm text-muted">{t('ui.worktrees.empty')}</p>}
      {rows.length > 0 && !catalog.length && !loading && <p className="py-4 text-sm text-muted">{t('ui.worktrees.noMatches')}</p>}
      {catalog.map(({ row, chat, current }) => {
        const title = chat?.title?.trim() || row.branch || t('ui.worktrees.detached')
        return <article key={row.id} className="space-y-2 rounded-lg border border-line p-3">
        <div className="flex items-center justify-between gap-2"><p className="flex min-w-0 items-center gap-2 text-sm"><GitBranch className="h-3.5 w-3.5 shrink-0" /><span className="truncate" title={title}>{title}</span></p><span className="shrink-0 text-xs text-muted">{t(row.status === 'active' ? 'ui.worktrees.active' : 'ui.worktrees.archived')}</span></div>
        {current && <p className="text-2xs font-medium text-brand">{t('ui.worktrees.currentCheckout')}</p>}
        <p className="break-all text-xs text-muted" title={row.repo}>{t('ui.worktrees.sourceProject', { name: basenameOf(row.repo) })}</p>
        {chat?.title?.trim() && <p className="break-all font-mono text-2xs text-faint">{row.branch ?? t('ui.worktrees.detached')}</p>}
        <details><summary className="cursor-pointer text-2xs text-faint">{t('ui.worktrees.locations')}</summary><p className="mt-1 break-all text-xs text-muted">{row.path}</p><p className="mt-1 break-all text-2xs text-faint">{row.repo}</p></details>
        <div className="flex flex-wrap gap-2">
          {row.status === 'active' && <Button size="sm" disabled={busy} onClick={() => void act(() => open(row))}>{t('ui.worktrees.open')}</Button>}
          <Button size="sm" disabled={busy} onClick={() => void act(row.status === 'active' ? async () => { await runtimeRequest(`/worktrees/${row.id}/archive`, 'POST') } : () => restoreAndOpen(row))}>{t(row.status === 'active' ? 'ui.worktrees.archive' : 'ui.worktrees.restoreAndOpen')}</Button>
        </div>
        {row.status === 'active' && <WorktreeReview id={row.id} active={visible} />}
        {row.status === 'active' && sessionId && <WorktreeEnvironment active={visible} key={scope + '\0' + row.id + '\0' + (environmentFocus?.scope === scope && environmentFocus.id === row.id ? 'review' : '')} id={row.id} sessionId={sessionId} disabled={busy} defaultOpen={environmentFocus?.scope === scope && environmentFocus.id === row.id} />}
      </article>
      })}
    </div>
  </section>
}
