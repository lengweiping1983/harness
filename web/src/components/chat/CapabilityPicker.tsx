import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { ComposerRemoveButton } from './ComposerRemoveButton'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Search, Sparkles, X, Loader2, RefreshCw, Check } from 'lucide-react'
import { ApiError, pluginRequest } from '@/lib/http'
import { useDraftStore } from '@/store/drafts'
import { useChatStore } from '@/store/chat'
import { useAppStore } from '@/store/app'
import type { AgentMountState } from '@/lib/agentMount'
import { cn } from '@/lib/utils'
import { JsonSource } from '@/components/ui/JsonSource'
import { filterComposerAgents, type ComposerAgent } from '@/lib/composerAgents'
import { filterComposerSkills, skillDisplayName, skillDescription, selectedSkillName, selectComposerSkill, removeComposerSkill, type ComposerSkill } from '@/lib/composerSkills'
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu'

/** Agent 选择常驻会话，技能写入草稿；搜索和请求都按会话隔离。 */
export function CapabilityPicker({ capabilityKind, sessionId, draft, onChange, focusEditor, open, setOpen, menuAnchor }: {
  capabilityKind: 'agents' | 'skills'; sessionId: string | null; draft: string; onChange: (text: string) => void; focusEditor: () => void
  open: boolean; setOpen: (open: boolean) => void; menuAnchor: React.RefObject<HTMLButtonElement>
}) {
  const locale = useLocale()
  const labels = capabilityKind === 'agents' ? 'ui.composerAgents' : 'ui.composerSkills'
  const skillMode = capabilityKind === 'skills'
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<'all' | 'agent' | 'team'>('all')
  const [catalog, setCatalog] = useState<Array<ComposerSkill & Partial<ComposerAgent>>>([])
  const [loading, setLoading] = useState(false)
  const [supportsTeamSessions, setSupportsTeamSessions] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const style = useAnchoredMenu(menuAnchor, open, 360)
  const selected = skillMode ? selectedSkillName(draft) : null
  const pendingAgent = useDraftStore(state => !sessionId && !skillMode ? state.bySession.__unsaved__?.agentSelection : undefined)
  const mount = useChatStore(state => sessionId ? state.sessions[sessionId]?.agentMount : null)
  const mounted = !skillMode && mount?.available ? mount.agent : null
  const shown = selected ?? pendingAgent?.name ?? mounted
  const shownAgent = catalog.find(agent => agent.name === shown)
  const shownLabel = pendingAgent?.display_name || (!selected && mount?.display_name) || (shownAgent && skillMode ? skillDisplayName(shownAgent) : shownAgent?.display_name) || shown
  const [selecting, setSelecting] = useState<string | null>(null)
  const avatarUrl = (name: string) => '/api/v1/plugins/agents/' + encodeURIComponent(name) + '/avatar' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '')
  const activeTeam = !selected && (pendingAgent?.agent_type ?? mount?.agent_type) === 'team'
  const [removing, setRemoving] = useState<string | null>(null)
  const [mountError, setMountError] = useState<{ owner: string; message: ResourceText; code?: string; details?: unknown } | null>(null)
  const showMountError = !skillMode && !open && mountError?.owner === (sessionId ?? '__unsaved__')
  const errorStyle = useAnchoredMenu(menuAnchor, showMountError, 288)
  const alive = useRef(true)
  const operationGeneration = useRef(0)
  useLayoutEffect(() => {
    alive.current = true
    let owner = useAppStore.getState().activeSessionId
    const unsubscribe = useAppStore.subscribe(state => {
      if (state.activeSessionId !== owner) { owner = state.activeSessionId; operationGeneration.current++ }
    })
    return () => { alive.current = false; operationGeneration.current++; unsubscribe() }
  }, [])
  const items: Array<ComposerSkill & Partial<ComposerAgent>> = skillMode ? filterComposerSkills(catalog, query) : filterComposerAgents(catalog as ComposerAgent[], query, kind)
  const listId = useId()
  const [active, setActive] = useState(0)
  const ownerScope = JSON.stringify([sessionId, capabilityKind])
  useLayoutEffect(() => { operationGeneration.current++ }, [ownerScope, open])
  const currentOwnerScope = useRef(ownerScope)
  currentOwnerScope.current = ownerScope
  const scope = JSON.stringify([sessionId, locale, capabilityKind])
  const currentScope = useRef(scope)
  currentScope.current = scope

  useEffect(() => { setOpen(false); setCatalog([]); setSupportsTeamSessions(false); setError(''); setMountError(null); setQuery(''); setKind('all') }, [ownerScope])
  useEffect(() => {
    if (!open && !selected) return
    setQuery(''); setKind('all')
    const controller = new AbortController()
    let live = true
    const owner = scope
    setLoading(true); setError(''); setCatalog([]); setSupportsTeamSessions(false)
    void pluginRequest<Partial<Record<'agents' | 'skills', Array<ComposerSkill & Partial<ComposerAgent>>>> & { capabilities?: { persistent_team_selection?: boolean } }>('/' + capabilityKind + '?view=picker' + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), 'GET', undefined, { signal: controller.signal })
      .then(data => { if (live && currentScope.current === owner) {
        setCatalog(data[capabilityKind] ?? [])
        setSupportsTeamSessions(data.capabilities?.persistent_team_selection === true)
      } })
      .catch(failure => { if (live && currentScope.current === owner) setError(failureText(failure)) })
      .finally(() => { if (live && currentScope.current === owner) setLoading(false) })
    return () => { live = false; controller.abort() }
  }, [open, scope, retry, sessionId, capabilityKind, selected])
  useEffect(() => { setActive(0) }, [query, kind, catalog])
  useEffect(() => {
    if (!open) return
    const frame = requestAnimationFrame(() => input.current?.focus())
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !anchor.current?.contains(event.target) && !menuAnchor.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss, true)
    return () => { cancelAnimationFrame(frame); document.removeEventListener('pointerdown', dismiss, true) }
  }, [open, menuAnchor, setOpen])
  useEffect(() => { menu.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [active])
  async function pick(agent: ComposerSkill & Partial<ComposerAgent>) {
    if (skillMode) { onChange(selectComposerSkill(agent, draft)); setOpen(false); focusEditor(); return }
    if (selecting || removing) return
    const selectionScope = ownerScope, initialOwner = sessionId ?? '__unsaved__'
    const generation = operationGeneration.current
    if (agent.agent_type === 'team' && !supportsTeamSessions) {
      setMountError({ owner: initialOwner, message: {key: 'ui.composerAgents.upgradeTeam'} }); return
    }
    setSelecting(initialOwner); setMountError(null)
    let owner = sessionId
    try {
      if (!owner) {
        useDraftStore.getState().setAgentSelection('__unsaved__', {name: agent.name, display_name: agent.display_name, agent_type: agent.agent_type === 'team' ? 'team' : 'agent'})
        setOpen(false); focusEditor()
        return
      }
      const current = useChatStore.getState().sessions[owner]?.agentMount
      const sameTeam = agent.agent_type === 'team' && current?.available && current.agent_type === 'team' && current.agent === agent.name
      const result = await pluginRequest<{ mount: AgentMountState }>(supportsTeamSessions ? '/agent-selection' : '/agent-mount', 'POST', {
        session_id: owner, name: agent.name,
        ...(sameTeam ? {team_worktrees: current.team_worktrees === true, team_working_copy: current.team_worktrees === true && current.team_working_copy === true} : {}),
      })
      useChatStore.getState().setAgentMount(owner, result.mount)
      if (alive.current && operationGeneration.current === generation && (currentOwnerScope.current === selectionScope || (!sessionId && useAppStore.getState().activeSessionId === owner))) {
        setOpen(false); focusEditor()
      }
    } catch (failure) {
      if (alive.current && operationGeneration.current === generation && (currentOwnerScope.current === selectionScope || (!sessionId && useAppStore.getState().activeSessionId === owner))) {
        setMountError({ owner: owner ?? initialOwner, message: failureText(failure), ...(failure instanceof ApiError ? { code: failure.code, details: failure.details } : {}) })
        setOpen(false); focusEditor()
      }
    } finally { if (alive.current) setSelecting(null) }
  }
  function close() { setOpen(false); (anchor.current ?? menuAnchor.current)?.focus() }
  async function remove() {
    if (skillMode && selected) { onChange(removeComposerSkill(draft)); focusEditor(); return }
    if (!skillMode && !sessionId) { useDraftStore.getState().setAgentSelection('__unsaved__'); focusEditor(); return }
    if (!sessionId || !mounted || removing || selecting) return
    const owner = sessionId
    const generation = operationGeneration.current
    setRemoving(owner); setMountError(null)
    try {
      const result = await pluginRequest<{ mount: AgentMountState }>('/agent-mount', 'POST', { session_id: owner, name: null })
      useChatStore.getState().setAgentMount(owner, result.mount)
      if (alive.current && operationGeneration.current === generation && useAppStore.getState().activeSessionId === owner) focusEditor()
    } catch (failure) {
      if (alive.current && operationGeneration.current === generation) setMountError({ owner, message: failureText(failure), ...(failure instanceof ApiError ? { code: failure.code, details: failure.details } : {}) })
    } finally { if (alive.current) setRemoving(current => current === owner ? null : current) }
  }
  return <div className="relative inline-flex min-w-0 max-w-full flex-wrap items-center gap-0.5">
    {shown ? <span className="group/composer-item relative inline-flex min-w-0 max-w-full"><button ref={anchor} type="button" aria-haspopup="dialog" aria-expanded={open}
      aria-label={t(labels + '.choose')} onClick={() => { setOpen(!open); setQuery('') }}
      title={!selected ? t(mount?.selection_unavailable ? 'ui.composerAgents.teamUnavailableHint' : activeTeam ? 'ui.composerAgents.teamHint' : 'ui.composerAgents.mountedHint') + ' · ' + shown : shown}
      className="inline-flex h-8 max-w-60 items-center gap-1.5 rounded-xl bg-brand/10 px-2.5 text-xs font-medium text-brand hover:bg-raised focus-visible:outline-brand">
      {skillMode ? <Sparkles className="h-4 w-4 shrink-0" /> : <AgentAvatar src={avatarUrl(shown)} team={activeTeam} loading="eager" className="h-5 w-5 rounded-md" />}<span className="truncate">{shownLabel}</span>
    </button>
    <ComposerRemoveButton disabled={!!removing || !!selecting} aria-label={t(selected ? labels + '.remove' : 'ui.composerAgents.unmount')} className="-right-1 -top-1"
      onClick={() => void remove()}>{!selected && removing !== null && removing === sessionId ? <Loader2 className="h-3 w-3 animate-spin" /> : undefined}</ComposerRemoveButton></span> : null}
    {showMountError && mountError ? createPortal(<div role="alert" style={errorStyle} className="z-popover rounded-xl border border-danger/20 bg-surface p-3 text-xs text-danger shadow-pop">
      <div className="flex items-start gap-2"><p className="min-w-0 flex-1 [overflow-wrap:anywhere]">{noticeText(mountError.message)}</p><button type="button" aria-label={t('ui.deferred.close')} onClick={() => { setMountError(null); focusEditor() }} className="shrink-0 rounded p-1 text-muted hover:bg-raised focus-visible:outline-brand"><X className="h-3 w-3" /></button></div>
      {mountError.code || mountError.details ? <details className="mt-2 text-2xs text-muted"><summary className="cursor-pointer focus-visible:outline-brand">{t('ui.agent.author.details')}</summary>{mountError.code ? <code className="mt-1 block break-all">{mountError.code}</code> : null}{mountError.details ? <JsonSource text={JSON.stringify(mountError.details, null, 2) ?? 'null'} className="mt-1 max-h-32" /> : null}</details> : null}
    </div>, document.body) : null}
    {open ? createPortal(<div ref={menu} style={{ ...style, minWidth: 'min(360px, calc(100vw - 24px))' }} role="dialog" aria-label={t(labels + '.choose')}
      className="z-menu overflow-hidden rounded-xl border border-line bg-surface shadow-pop"
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() }
        if (event.target === input.current && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault(); setActive(index => items.length ? (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length : 0)
        }
        if (event.target === input.current && event.key === 'Enter' && items[active]) { event.preventDefault(); void pick(items[active]!) }
      }}>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2"><Search className="h-4 w-4 text-faint" />
        <input ref={input} role="combobox" aria-expanded="true" aria-haspopup="listbox" aria-autocomplete="list" value={query} onChange={event => setQuery(event.target.value)} placeholder={t(labels + '.search')} aria-label={t(labels + '.search')}
          className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none" aria-controls={listId} aria-activedescendant={items[active] ? `${listId}-${active}` : undefined} />
        <button type="button" aria-label={t(labels + '.close')} onClick={close} className="rounded p-1 hover:bg-raised"><X className="h-4 w-4 text-muted" /></button>
      </div>
      {!skillMode ? <div className="flex gap-1 px-2 py-2">{(['all', 'agent', 'team'] as const).map(value => <button key={value} type="button" aria-pressed={kind === value}
        onClick={() => setKind(value)} className={cn('rounded-lg px-3 py-1 text-xs', kind === value ? 'bg-brand/10 text-brand' : 'text-muted hover:bg-raised')}>{t('ui.composerAgents.' + value)}</button>)}</div> : null}
      <div id={listId} role="listbox" aria-label={t(labels + '.choose')} className="max-h-64 overflow-y-auto overscroll-contain p-1">
        {selecting ? <p role="status" className="flex items-center gap-2 p-3 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t('ui.composerAgents.selecting')}</p> : null}
        {loading ? <p role="status" className="flex items-center gap-2 p-3 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t(labels + '.loading')}</p>
          : error ? <div role="alert" className="space-y-2 p-3 text-xs text-danger"><p>{noticeText(error)}</p><button type="button" onClick={() => setRetry(value => value + 1)} className="flex items-center gap-1 text-brand"><RefreshCw className="h-3 w-3" />{t('ui.composerAgents.retry')}</button></div>
          : items.length ? items.map((agent, index) => <button id={`${listId}-${index}`} key={agent.name} type="button" role="option" aria-selected={active === index}
            onFocus={() => setActive(index)} onPointerMove={() => setActive(index)} disabled={!!selecting || !!removing} onClick={() => void pick(agent)}
            className={cn('flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left', active === index ? 'bg-raised' : 'hover:bg-raised')}>
            {skillMode ? <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand" /> : <AgentAvatar src={avatarUrl(agent.name)} team={agent.agent_type === 'team'} className="mt-0.5 h-7 w-7 rounded-lg" />}
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{skillMode ? skillDisplayName(agent) : agent.display_name || agent.name}{agent.profession ? ' · ' + agent.profession : ''}</span>{(skillMode ? skillDisplayName(agent) !== agent.name : agent.display_name && agent.display_name !== agent.name) ? <span className="block truncate font-mono text-2xs text-faint">{agent.name}</span> : null}<span className="mt-0.5 line-clamp-2 text-2xs text-muted">{skillMode ? skillDescription(agent) : agent.description}</span></span>
            {shown === agent.name ? <Check className="h-4 w-4 shrink-0 text-brand" /> : null}
          </button>) : <p role="status" className="p-3 text-xs text-muted">{t(labels + '.empty')}</p>}
      </div>
      <p className="border-t border-line px-3 py-2 text-2xs text-muted">{t(labels + '.hint')}</p>
    </div>, document.body) : null}
  </div>
}
