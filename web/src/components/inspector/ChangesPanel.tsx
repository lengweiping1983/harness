import { useAppStore } from '@/store/app'
import { sessionDirectory } from '@/lib/workspaces'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import type { ChangeReviewTurn } from '@/lib/changeReview'
import { FileTypeIcon } from '@/components/ui/FileTypeIcon'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Folder } from 'lucide-react'
import { t, useLocale, getLocale } from '@/i18n'
import { DiffView } from '@/components/chat/blocks/DiffView'
import { FileRestore } from './FileRestore'
import { Button, RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import type { ComparisonOptions, ComparisonPatch } from '@/lib/protocol.generated'
import type { ChangesTarget } from '@/lib/turnChanges'
import { relativizeToRoot } from '@/lib/deliverables'
import { changeTree, readComparison, readComparisonOptions, readComparisonPatch, type ChangeFile, type ChangeSnapshot, type ChangeTreeNode, type ComparisonQuery, type ComparisonScope } from '@/lib/comparison'

function turnSummaryPreview(summary: string) {
  const characters = Array.from(summary.replace(/\s+/gu, ' ').trim())
  return characters.length > 40 ? characters.slice(0, 40).join('') + '…' : characters.join('')
}

function Counts({ added, deleted }: { added: number | null; deleted: number | null }) {
  return added !== null && deleted !== null ? <span className="changes-counts"><span className="text-ok">+{added.toLocaleString(getLocale())}</span> <span className="text-danger">−{deleted.toLocaleString(getLocale())}</span></span> : null
}
function statusName(status: string) {
  return t(status === 'A' ? 'ui.changes.added' : status === 'D' ? 'ui.changes.deleted' : status === 'R' ? 'ui.changes.renamed' : status === 'U' ? 'ui.changes.conflict' : status === '?' ? 'ui.changes.unavailable' : 'ui.changes.modified')
}

function FileDiff({ id, query, snapshot, file, expanded, onToggle, onWorkspace, active }: { active: boolean; id: string; query: ComparisonQuery; snapshot: ChangeSnapshot; file: ChangeFile; expanded: boolean; onToggle: () => void; onWorkspace: () => void }) {
  useLocale()
  const [patch, setPatch] = useState<ComparisonPatch | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!active || !expanded) return
    const controller = new AbortController()
    setPatch(null); setError('')
    void readComparisonPatch(id, query, snapshot, file, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPatch(value) })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failureText(failure)) })
    return () => controller.abort()
  }, [id, query, snapshot, file, retry, expanded, active])
  const unavailable = file.history && !file.history.available
  const displayPath = relativizeToRoot(file.path, snapshot.root)
  return <article className="changes-file" data-change-file={file.path}>
    <header><button type="button" className="changes-file-heading w-full text-left" aria-label={t('ui.changes.fileLabel', {path: displayPath, status: statusName(file.status)})} aria-expanded={expanded} onClick={onToggle}>{expanded ? <ChevronDown aria-hidden className="h-3 w-3 shrink-0" /> : <ChevronRight aria-hidden className="h-3 w-3 shrink-0" />}<FileTypeIcon path={file.path} /><span className="min-w-0 flex-1 break-all" title={file.path}>{displayPath}</span><span className="text-muted">{statusName(file.status)}</span><Counts added={file.added} deleted={file.deleted} /></button></header>
    {expanded && file.old_path ? <p className="px-3 py-1 text-xs text-muted">{file.old_path} → {relativizeToRoot(file.path, snapshot.root)}</p> : null}
    {expanded ? <div className="changes-file-body">
      {error ? <div role="alert" className="space-y-2 p-3 text-xs"><p className="text-danger">{unavailable ? t('ui.changes.historyUnavailable') : noticeText(error)}</p>{unavailable ? <Button size="xs" onClick={onWorkspace}>{t('ui.changes.workspace')}</Button> : <Button size="xs" onClick={() => setRetry(n => n + 1)}>{t('ui.changes.retry')}</Button>}</div>
        : !patch ? <p role="status" className="p-3 text-xs text-muted">{t('ui.changes.loadingPatch')}</p>
        : <>
          {patch.truncated ? <p className="p-2 text-xs text-warn">{t('ui.changes.truncated')}</p> : null}
          {patch.conflict ? <p className="p-2 text-xs text-warn">{t('ui.changes.conflictHint')}</p> : null}
          {patch.binary ? <p className="p-3 text-xs text-muted">{t('ui.changes.binary')}</p> : patch.patch ? <DiffView output={patch.patch} display={{ diff: patch.patch }} path={file.path} /> : <p className="p-3 text-xs text-muted">{t('ui.changes.noText')}</p>}
          {snapshot.historyTarget && snapshot.through !== undefined && file.history?.available && !patch.truncated ? <div className="p-2"><FileRestore target={snapshot.historyTarget} file={file.history} through={snapshot.through} /></div> : null}
        </>}
    </div> : null}
  </article>
}

function TreeNodes({ nodes, selected, select, collapsed, toggle, filtering }: { nodes: ChangeTreeNode[]; selected: string; select: (path: string) => void; collapsed: Set<string>; toggle: (path: string) => void; filtering: boolean }) {
  return <ul>{nodes.map(node => <li key={node.path}>{node.file
    ? <button type="button" className="changes-tree-file" aria-label={t('ui.changes.fileLabel', {path: node.file.path, status: statusName(node.file.status)})} aria-current={selected === node.file.path ? 'true' : undefined} onClick={() => select(node.file!.path)} title={node.file.path}><FileTypeIcon path={node.file.path} /><span className="min-w-0 flex-1 truncate">{node.name}</span><span className="text-muted" title={statusName(node.file.status)}>{node.file.status}</span><Counts added={node.file.added} deleted={node.file.deleted} /></button>
    : <><button type="button" className="changes-tree-directory" aria-expanded={filtering || !collapsed.has(node.path)} onClick={() => toggle(node.path)}>{!filtering && collapsed.has(node.path) ? <ChevronRight aria-hidden className="h-3 w-3 shrink-0" /> : <ChevronDown aria-hidden className="h-3 w-3 shrink-0" />}<Folder aria-hidden className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{node.name}</span></button>{filtering || !collapsed.has(node.path) ? <div className="changes-tree-children"><TreeNodes nodes={node.children} selected={selected} select={select} collapsed={collapsed} toggle={toggle} filtering={filtering} /></div> : null}</>}
  </li>)}</ul>
}

export function ChangesPanel({ sessionId, historyTarget, initialHistory = false, historyChoices = [], active = true }: { active?: boolean; sessionId: string | null; historyTarget: ChangesTarget | null; initialHistory?: boolean; historyChoices?: ChangeReviewTurn[] }) {
  useLocale()
  const [scope, setScope] = useState<ComparisonScope>(initialHistory && historyTarget ? 'history' : 'uncommitted')
  const [historyTurn, setHistoryTurn] = useState(historyTarget?.turn ?? 0)
  const activeHistoryTarget = useMemo(() => historyTarget && historyTurn === historyTarget.turn ? historyTarget : sessionId && historyTurn > 0 ? { sessionId, turn: historyTurn, seq: 0 } : null, [historyTarget, sessionId, historyTurn])
  const taskChoices = [...historyChoices]
  for (const turn of [historyTarget?.turn, historyTurn]) {
    if (turn && !taskChoices.some(choice => choice.turn === turn)) taskChoices.push({ turn, summary: '', hasChanges: false })
  }
  taskChoices.sort((a, b) => b.turn - a.turn)
  const [base, setBase] = useState('')
  const [workspaceBase, setWorkspaceBase] = useState('')
  const [target, setTarget] = useState('')
  const [commit, setCommit] = useState('')
  const [search, setSearch] = useState('')
  const [options, setOptions] = useState<ComparisonOptions | null>(null)
  const [optionsError, setOptionsError] = useState<ResourceText>('')
  const [optionsBusy, setOptionsBusy] = useState(false)
  const [optionsMoreError, setOptionsMoreError] = useState<ResourceText>('')
  const [optionsOffset, setOptionsOffset] = useState<number | null>(null)
  const [snapshot, setSnapshot] = useState<ChangeSnapshot | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState('')
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const diffsRef = useRef<HTMLDivElement>(null)
  const [treeOpen, setTreeOpen] = useState(false)
  const queryBase = scope === 'branch' ? base : scope === 'uncommitted' ? workspaceBase : ''
  const queryTarget = scope === 'commit' ? commit : scope === 'branch' ? target : ''
  const query = useMemo<ComparisonQuery>(() => ({ scope, base: queryBase, target: queryTarget }), [scope, queryBase, queryTarget])
  const cwd = useAppStore(state => sessionDirectory(state.sessions.find(session => session.id === sessionId) ?? null))
  const repositoryScope = sessionId + '\0' + (cwd ?? '')
  const [gitScope, setGitScope] = useState<string | null>(null)
  const gitReady = gitScope === repositoryScope
  useEffect(() => {
    if (!active || !sessionId || scope === 'history' || !gitReady) return
    const controller = new AbortController()
    setOptionsBusy(true); setOptionsError(''); setOptionsMoreError('')
    const timer = setTimeout(() => {
      void readComparisonOptions(sessionId, search, optionsOffset ?? 0, controller.signal)
        .then(value => {
          if (controller.signal.aborted) return
          setOptions(previous => optionsOffset && previous ? { ...value, commits: [...previous.commits, ...value.commits.filter(c => !previous.commits.some(p => p.id === c.id))] } : value)
          setBase(previous => previous || value.upstream || '')
          setTarget(previous => previous || value.current)
        })
        .catch((failure: Error) => { if (!controller.signal.aborted) { if (optionsOffset) setOptionsMoreError(failureText(failure)); else setOptionsError(failureText(failure)) } })
        .finally(() => { if (!controller.signal.aborted) setOptionsBusy(false) })
    }, search ? 250 : 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [sessionId, search, optionsOffset, refresh, active, gitReady, scope])
  useEffect(() => {
    if (!active) return
    setSnapshot(null); setError(''); setFilter(''); setTreeOpen(false)
    const needsGitChoice = scope === 'branch' && (!base || !target) || scope === 'commit' && !commit
    if (!sessionId || needsGitChoice && gitReady) return
    const controller = new AbortController()
    void readComparison(sessionId, needsGitChoice ? {scope: 'uncommitted', base: '', target: ''} : query, activeHistoryTarget, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return
        setSnapshot(value)
        if (!value.historyTarget) setGitScope(repositoryScope)
        setExpandedFiles(new Set([value.files.find(file => file.path === activeHistoryTarget?.path)?.path ?? value.files[0]?.path].filter((path): path is string => !!path)))
        setSelected(value.files.find(file => file.path === activeHistoryTarget?.path)?.path ?? value.files[0]?.path ?? '')
      })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failureText(failure)) })
    return () => controller.abort()
  }, [sessionId, query, activeHistoryTarget, refresh, active, repositoryScope])
  const nodes = useMemo(() => changeTree(snapshot?.files ?? [], filter, snapshot?.root), [snapshot, filter])
  const files = snapshot?.files ?? []
  const needsChoice = scope === 'branch' && (!base || !target) || scope === 'commit' && !commit
  const label = (side: 'left' | 'right') => {
    if (scope === 'history') return t(side === 'left' ? 'ui.changes.turnBefore' : snapshot?.historyEnded === false ? 'ui.changes.turnCurrent' : 'ui.changes.turnAfter', { turn: activeHistoryTarget?.turn ?? historyTurn })
    const value = snapshot?.[side]
    if (value === 'WORKTREE') return t('ui.changes.workingTree')
    if (value === 'INDEX') return t('ui.changes.index')
    if (scope === 'branch') return (side === 'left' ? base : target).replace(/^refs\/(heads|remotes)\//, '')
    return value ? value.slice(0, 8) : side === 'left' ? 'HEAD' : t('ui.changes.workingTree')
  }
  return <section className="changes-panel refresh-action-container" aria-label={t('ui.changes.title')}>
    <header className="changes-toolbar">
      <select data-panel-initial-focus aria-label={t('ui.changes.scope')} value={scope === 'history' ? `history:${historyTurn}` : scope} onChange={event => { const value = event.target.value; if (value.startsWith('history:')) { setHistoryTurn(Number(value.slice(8))); setScope('history') } else setScope(value as ComparisonScope); setOptionsOffset(null) }}>
        {taskChoices.length ? taskChoices.map(choice => <option key={choice.turn} value={`history:${choice.turn}`} title={choice.summary}>{t('ui.changes.turn', { turn: choice.turn })}{choice.summary ? ` · ${turnSummaryPreview(choice.summary)}` : ''}</option>) : <option value="history:0" disabled>{t('ui.changes.noHistory')}</option>}
        <option value="uncommitted" disabled={!!optionsError} title={noticeText(optionsError)}>{t('ui.changes.uncommitted')}</option><option value="unstaged" disabled={!!optionsError} title={noticeText(optionsError)}>{t('ui.changes.unstaged')}</option><option value="staged" disabled={!!optionsError} title={noticeText(optionsError)}>{t('ui.changes.staged')}</option><option value="commit" disabled={!!optionsError} title={noticeText(optionsError)}>{t('ui.changes.commit')}</option><option value="branch" disabled={!!optionsError} title={noticeText(optionsError)}>{t('ui.changes.branch')}</option>
      </select>
      {scope === 'uncommitted' ? <div className="changes-endpoints changes-compare-picker"><select aria-label={t('ui.changes.base')} value={workspaceBase} onChange={e => setWorkspaceBase(e.target.value)}><option value="">HEAD</option>{options?.branches.map(ref => <option key={ref} value={ref}>{ref.replace(/^refs\/(heads|remotes)\//, '')}</option>)}{options?.commits.map(c => <option key={c.id} value={c.id}>{c.id.slice(0, 8)} {c.subject}</option>)}</select><span aria-hidden>→</span><span>{t('ui.changes.workingTree')}</span></div> : scope !== 'branch' && !needsChoice ? <span className="changes-endpoints">{label('left')} → {label('right')}</span> : null}
      {scope === 'branch' ? <div className="changes-compare-picker"><select aria-label={t('ui.changes.base')} value={base} onChange={e => setBase(e.target.value)}><option value="">{t('ui.changes.chooseBase')}</option>{options?.branches.map(ref => <option key={ref} value={ref}>{ref.replace(/^refs\/(heads|remotes)\//, '')}</option>)}</select><span aria-hidden>→</span><select aria-label={t('ui.changes.target')} value={target} onChange={e => setTarget(e.target.value)}><option value="">{t('ui.changes.chooseTarget')}</option>{options?.current === 'HEAD' ? <option value="HEAD">HEAD</option> : null}{options?.branches.map(ref => <option key={ref} value={ref}>{ref.replace(/^refs\/(heads|remotes)\//, '')}</option>)}</select></div> : null}
      {snapshot ? <Counts added={snapshot.added} deleted={snapshot.deleted} /> : null}
      <RefreshButton size="xs" label={t('ui.changes.refresh')} onClick={() => { setOptionsOffset(null); setRefresh(n => n + 1) }} />
    </header>

    {scope === 'commit' ? <div className="changes-commit-picker"><input aria-label={t('ui.changes.searchCommits')} placeholder={t('ui.changes.searchCommits')} value={search} onChange={e => { setSearch(e.target.value); setOptionsOffset(null) }} /><select aria-label={t('ui.changes.chooseCommit')} value={commit} onChange={e => setCommit(e.target.value)}><option value="">{t('ui.changes.chooseCommit')}</option>{commit && !options?.commits.some(c => c.id === commit) ? <option value={commit}>{commit.slice(0, 8)}</option> : null}{options?.commits.map(c => <option key={c.id} value={c.id}>{c.id.slice(0, 8)} {c.subject}</option>)}</select>{options?.next_offset !== null && options?.next_offset !== undefined ? <ShowMoreControl className="w-auto" disabled={optionsBusy} onClick={() => setOptionsOffset(options.next_offset)} /> : null}{optionsBusy ? <p role="status" className="text-xs text-muted">{t('ui.changes.loading')}</p> : null}{optionsMoreError ? <p role="alert" className="text-xs text-danger">{noticeText(optionsMoreError)}</p> : null}</div> : null}
    {optionsError ? <p role="status" className="text-xs text-muted">{noticeText(optionsError)}</p> : null}
    {!sessionId ? <p className="changes-hint">{t('ui.changes.noSession')}</p> : error ? <div role="alert" className="changes-hint text-danger"><p>{noticeText(error)}</p></div> : needsChoice ? <p className="changes-hint">{t(scope === 'branch' ? 'ui.changes.chooseBase' : 'ui.changes.chooseCommit')}</p> : !snapshot ? <p role="status" className="changes-hint">{t('ui.changes.loading')}</p> : !snapshot.total ? <p className="changes-hint">{t('ui.changes.empty')}</p> : <>
      <div className="changes-summary"><span>{t(snapshot.total === 1 ? 'ui.changes.oneFile' : 'ui.changes.files', { count: snapshot.total })}</span><button className="changes-tree-toggle" type="button" aria-expanded={treeOpen} onClick={() => setTreeOpen(value => !value)}>{t('ui.changes.chooseFile')}</button></div>
      <div className="changes-layout">
        <div ref={diffsRef} className="changes-diffs">{files.map(file => <FileDiff active={active} key={snapshot.revision + ':' + file.path} id={sessionId} query={query} snapshot={snapshot} file={file} expanded={expandedFiles.has(file.path)} onToggle={() => setExpandedFiles(previous => { const next = new Set(previous); if (next.has(file.path)) next.delete(file.path); else next.add(file.path); return next })} onWorkspace={() => setScope('uncommitted')} />)}</div>
        <aside className={'changes-tree' + (treeOpen ? ' changes-tree-open' : '')} aria-label={t('ui.changes.fileTree')}><input aria-label={t('ui.changes.filter')} placeholder={t('ui.changes.filter')} value={filter} onChange={e => setFilter(e.target.value)} />{nodes.length ? <TreeNodes nodes={nodes} selected={selected} select={path => { setSelected(path); setExpandedFiles(previous => new Set(previous).add(path)); setTreeOpen(false); requestAnimationFrame(() => { const article = Array.from(diffsRef.current?.querySelectorAll<HTMLElement>('[data-change-file]') ?? []).find(element => element.dataset.changeFile === path); article?.scrollIntoView({ block: 'start' }) }) }} collapsed={collapsed} toggle={path => setCollapsed(previous => { const next = new Set(previous); if (next.has(path)) next.delete(path); else next.add(path); return next })} filtering={!!filter} /> : <p className="p-3 text-xs text-muted">{t('ui.changes.noMatches')}</p>}</aside>
      </div>
    </>}
  </section>
}
