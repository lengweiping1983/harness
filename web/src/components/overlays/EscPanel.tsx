import type { ResourceText } from '@/types'
import { failureText, ResourceError } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { ConfirmRemoval } from '@/components/ui/ConfirmRemoval'
import type { AgentMountState } from '@/lib/agentMount'
import type { SkillRow, AgentRow, ConnectorRow, Overview, MountState } from '@/lib/extensionCatalog'
import { CatalogToolbar, InstallDisclosure, ScopeSelect, Switch, matchesCatalog, catalogOrigin, type CatalogScope } from './ExtensionCatalogControls'
import { ConnectorsTab } from './ConnectorsTab'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Blocks,
  Plug,
  RefreshCw,
  Sparkles,
  Trash2,
  Users,
  UserRound,
  Wand2,
} from 'lucide-react'
import { removeAgentEditDrafts } from '@/lib/agentDraft'
import { ApiError, isUnconfirmedWrite, pluginRequest } from '@/lib/http'
import { AgentDefinitionCreator } from './AgentDefinitionCreator'
import { AgentCreator } from './AgentCreator'
import { TeamTemplates } from './TeamTemplates'
import { DirectoryField } from '@/components/ui/DirectoryField'
import { runtimeRequest } from '@/lib/http'
import { useAppStore } from '@/store/app'
import { useChatStore } from '@/store/chat'
import { useDraftStore } from '@/store/drafts'
import { selectComposerSkill } from '@/lib/composerSkills'
import { teamReferenceIssues } from '@/lib/teamReferences'
import { Badge, Button, EmptyHint, RefreshButton, Segmented, Spinner } from '@/components/ui/primitives'

/**
 * Agent·技能·连接器（ESC）管理面板。
 *
 * 三类能力的管理行为：
 *
 * * **Agent** —— 会话级召唤/退场（agent 型常驻人格，team 型支持当前会话常驻及命令现场
 *   编排）、全局启停、目录安装/卸载；
 * * **技能** —— 全局启停（下一回合目录自动过滤）、目录安装/卸载；
 * * **连接器** —— 声明式 MCP 服务管理：启停、绑定（连接）/解绑、增删。
 *
 * 数据纪律：所有写操作完成后**重取目录**而不是本地拼凑状态——后端才是
 * 单一事实源（后端配置与注册目录）。
 */

/* ============================ 主面板 ============================ */

type Tab = 'agents' | 'skills' | 'connectors'

export function SkillsPage() {
  return <EscPanel catalog="skills" />
}

export function ConnectorsPage() {
  return <EscPanel catalog="connectors" />
}

export function EscPanel({ initialAgentKind = 'all', catalog = 'all' }: { initialAgentKind?: 'all' | 'agent' | 'team'; catalog?: 'all' | 'agents' | 'skills' | 'connectors' } = {}) {
  const locale = useLocale()

  const sessionId = useAppStore((s) => s.activeSessionId)
  const toast = useAppStore((s) => s.toast)
  const [tab, setTab] = useState<Tab>(catalog === 'skills' || catalog === 'connectors' ? catalog : 'agents')
  useEffect(() => setTab(catalog === 'skills' || catalog === 'connectors' ? catalog : 'agents'), [catalog])
  const [overview, setOverview] = useState<Overview | null>(null)
  const [mount, setMount] = useState<MountState | null>(null)
  const [envMcp, setEnvMcp] = useState<Array<{ server_name: string; transport: string }>>([])
  const [error, setError] = useState<ResourceText>('')
  const [busy, setBusy] = useState('')
  const actionLock = useRef(false)
  const [refreshing, setRefreshing] = useState(false)

  const scopeIdentity = useMemo(() => ({}), [locale, sessionId, catalog])
  const currentScope = useRef<object | null>(scopeIdentity)
  currentScope.current = scopeIdentity
  const generation = useRef(0)
  const loadAbort = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    const identity = scopeIdentity
    if (currentScope.current !== identity) return
    loadAbort.current?.abort()
    const controller = new AbortController()
    loadAbort.current = controller
    const options = { signal: controller.signal }
    const request = ++generation.current
    const active = () => currentScope.current === identity && generation.current === request
    const query = sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''
    const empty: Overview = { skills: [], agents: [], connectors: [], enabled: { skills: false, agents: false, connectors: false } }
    const overviewRequest = catalog === 'skills'
      ? pluginRequest<{ skills: SkillRow[]; enabled: boolean }>('/skills' + query, 'GET', undefined, options).then(value => ({ ...empty, skills: value.skills, enabled: { ...empty.enabled, skills: value.enabled } }))
      : catalog === 'connectors'
        ? pluginRequest<{ connectors: ConnectorRow[]; enabled: boolean; environment: Array<{ server_name: string; transport: string }> }>('/connectors', 'GET', undefined, options).then(value => ({ ...empty, connectors: value.connectors, environment: value.environment, enabled: { ...empty.enabled, connectors: value.enabled } }))
        : pluginRequest<Overview>(query, 'GET', undefined, options)
    const [data, settings, mounted] = await Promise.allSettled([
      overviewRequest,
      catalog === 'all' ? runtimeRequest<{ config: { mcp_servers?: Array<{ server_name: string; transport: string }> } }>('/settings', 'GET', undefined, options) : Promise.resolve(null),
      sessionId && (catalog === 'agents' || catalog === 'all') ? pluginRequest<{ mount: MountState }>('/agent-mount?session_id=' + encodeURIComponent(sessionId), 'GET', undefined, options) : Promise.resolve(null),
    ])
    if (!active()) return
    if (data.status === 'rejected') throw data.reason
    setEnvMcp(data.value.environment ?? (settings.status === 'fulfilled' ? settings.value?.config.mcp_servers ?? [] : []))
    if (sessionId && mounted.status === 'fulfilled' && mounted.value) {
      setMount(mounted.value.mount)
      useChatStore.getState().setAgentMount(sessionId, mounted.value.mount)
    } else setMount(null)
    // 配置齐备后显示可操作列表，避免迟到的挂载配置覆盖用户刚调整的开关。
    setOverview(data.value)
  }, [sessionId, scopeIdentity, catalog])

  const currentLoad = useRef<(() => Promise<void>) | null>(load)
  currentLoad.current = load

  const reloadCatalog = useCallback(() => {
    setRefreshing(true); setError('')
    void load()
      .catch((failure: Error) => { if (currentScope.current === scopeIdentity) setError(failureText(failure)) })
      .finally(() => { if (currentScope.current === scopeIdentity) setRefreshing(false) })
  }, [load, scopeIdentity])

  useEffect(() => {
    currentScope.current = scopeIdentity
    currentLoad.current = load
    setOverview(null); setMount(null); setError(''); setRefreshing(false)
    void load().catch((failure: Error) => { if (currentScope.current === scopeIdentity) setError(failureText(failure)) })
    return () => { loadAbort.current?.abort(); loadAbort.current = null; generation.current += 1; currentScope.current = null; currentLoad.current = null }
  }, [load])

  const perform = useCallback(
    async (key: string, operation: () => Promise<unknown>, receipt: ResourceText, onApplied?: (result: unknown) => void) => {
      if (actionLock.current) return false
      actionLock.current = true
      setBusy(key)
      setError('')
      try {
        const result = await operation()
        if (currentScope.current !== scopeIdentity) {
          try { await currentLoad.current?.() } catch { /* 旧操作只回查当前目录，不向新作用域发布反馈。 */ }
          return false
        }
        let refreshIssue: ResourceText | undefined
        try { await load() }
        catch (failure) { refreshIssue = {template: {key: 'ui.esc.operationAppliedRefreshFailed', params: {reason: failureText(failure)}}} }
        if (currentScope.current !== scopeIdentity) return false
        onApplied?.(result)
        if (refreshIssue) setError(refreshIssue)
        toast({ title: receipt, description: refreshIssue, tone: refreshIssue ? 'warn' : 'success' })
        return true
      } catch (failure) {
        if (failure instanceof ApiError && isUnconfirmedWrite(failure.failure)) {
          try { await currentLoad.current?.() } catch { /* 保留原始未知结果，由用户再次刷新。 */ }
        }
        if (currentScope.current === scopeIdentity) setError(failureText(failure))
        return false
      } finally {
        actionLock.current = false
        if (currentScope.current) setBusy('')
      }
    },
    [load, toast, scopeIdentity],
  )

  const counts = useMemo(
    () => ({
      agents: overview?.agents.length ?? 0,
      skills: overview?.skills.length ?? 0,
      connectors: overview?.connectors.length ?? 0,
    }),
    [overview, locale],
  )

  if (!overview) {
    if (error) return <div role="alert" className="space-y-3 rounded-xl border border-danger/25 bg-danger/5 p-4">
      <p className="text-sm font-medium text-ink">{t('ui.esc.catalogLoadFailed')}</p>
      <p className="text-xs text-danger [overflow-wrap:anywhere]">{noticeText(error)}</p>
      <Button size="sm" variant="secondary" onClick={reloadCatalog} icon={<RefreshCw className="h-3.5 w-3.5" />}>{t('ui.esc.retryCatalog')}</Button>
    </div>
    return (
      <p role="status" className="flex items-center gap-2 py-6 text-xs text-muted">
        <Spinner className="h-3.5 w-5" />{t("ui.components.overlays.EscPanel.54b311d42036")}
      </p>
    )
  }

  return (
    <div className="refresh-action-container mx-auto max-w-4xl space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="ui-section-title">{t(catalog === 'agents' ? 'ui.agents.directory' : catalog === 'skills' ? 'ui.extensions.skills' : catalog === 'connectors' ? 'ui.extensions.connectors' : 'ui.components.overlays.EscPanel.0ce77028966d')}</h3>
          <p className="mt-0.5 text-2xs leading-relaxed text-faint">{t(catalog === 'agents' ? 'ui.agents.directoryHint' : catalog === 'skills' ? 'ui.extensions.skillsHint' : catalog === 'connectors' ? 'ui.extensions.connectorsHint' : 'ui.components.overlays.EscPanel.1372842ad3f0')}</p>
        </div>
        <RefreshButton size="xs" label={t("ui.components.overlays.EscPanel.ae93d47bc513")} disabled={refreshing} spinning={refreshing} onClick={reloadCatalog} />
      </header>

      {catalog === 'all' && <Segmented<Tab>
        value={tab}
        onChange={setTab}
        options={[
          ...([{  value: 'agents' as const, label: t("ui.components.overlays.EscPanel.c3a73c5eecea"), icon: <UserRound className="h-3 w-3" />, badge: <Badge>{counts.agents}</Badge> }]),
          { value: 'skills', label: t("ui.components.overlays.EscPanel.99aea2f9131a"), icon: <Blocks className="h-3 w-3" />, badge: <Badge>{counts.skills}</Badge> },
          { value: 'connectors', label: t("ui.components.overlays.EscPanel.7587a6fe20a9"), icon: <Plug className="h-3 w-3" />, badge: <Badge>{counts.connectors}</Badge> },
        ]}
      />}

      {error ? (
        <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 p-3 text-xs text-danger [overflow-wrap:anywhere]">
          {noticeText(error)}
        </p>
      ) : null}

      {tab === 'agents' ? (
        <AgentsTab key={sessionId ?? "shared"} initialKind={initialAgentKind}
          overview={overview}
          mount={mount}
          sessionId={sessionId}
          busy={busy}
          perform={perform}
          refresh={load}
        />
      ) : null}
      {tab === 'skills' ? (
        <SkillsTab key={sessionId ?? "shared"} overview={overview} sessionId={sessionId} busy={busy} perform={perform} />
      ) : null}
      {tab === 'connectors' ? (
        <ConnectorsTab key={sessionId ?? 'shared'} overview={overview} envMcp={envMcp} busy={busy} perform={perform} />
      ) : null}
    </div>
  )
}

/* ============================ Agent ============================ */

function AgentsTab({
  initialKind,
  overview,
  mount,
  sessionId,
  busy,
  perform,
  refresh,
}: {
  initialKind: 'all' | 'agent' | 'team'
  refresh: () => Promise<void>
  overview: Overview
  mount: MountState | null
  sessionId: string | null
  busy: string
  perform: (key: string, op: () => Promise<unknown>, receipt: ResourceText, onApplied?: (result: unknown) => void) => Promise<boolean>
}) {
  useLocale()

  const draft = useDraftStore(state => sessionId ? state.bySession[sessionId]?.text ?? '' : '')
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState(initialKind === 'all' ? 'agent' : initialKind)
  useEffect(() => setKind(initialKind === 'all' ? 'agent' : initialKind), [initialKind])
  const filtered = overview.agents.filter(agent => (agent.agent_type === kind) && matchesCatalog(query, [agent.name, agent.display_name, agent.profession, agent.description, ...(agent.members ?? []).flatMap(member => [member.name, member.display_name, member.agent, member.runner])]))

  const [details, setDetails] = useState<AgentRow | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)
  const [seed, setSeed] = useState<AgentRow | null>(null)
  const [installPath, setInstallPath] = useState('')
  const [scope, setScope] = useState<'user' | 'project'>('user')

  if (!overview.enabled.agents) {
    return <EmptyHint icon={<Sparkles className="h-4 w-4" />} title={t("ui.components.overlays.EscPanel.efc9526645eb")} description={t("ui.components.overlays.EscPanel.355b11364f16")} />
  }

  const mountedName = mount?.agent ?? null

  return (
    <div className="space-y-3">
      <AgentDefinitionCreator key={sessionId ?? "shared"} sessionId={sessionId} seed={seed} viewSeed={details} onCloseView={() => setDetails(null)} agents={overview.agents} refresh={refresh} initialKind={kind === 'team' ? 'team' : 'agent'} />
      {/* 当前会话的挂载状态条 */}
      <div className="flex items-center gap-3 rounded-xl border border-brand/25 bg-brand/5 px-3 py-2.5">
        <Wand2 className="h-4 w-4 shrink-0 text-brand" />
        <div className="min-w-0 flex-1">
          {mountedName ? (
            <>
              <p className="text-xs font-medium text-ink">{t('ui.agents.summoned', { name: mount?.display_name || mountedName })}
              </p>
              <p className="text-2xs text-faint [overflow-wrap:anywhere]">
                {mount?.description || t("ui.components.overlays.EscPanel.2989158e025f")}
                {mount?.model ? t("ui.components.overlays.EscPanel.043dc6c7a40c", {p0: (mount.model)}) : ''}
              </p>
            </>
          ) : (
            <p className="text-xs text-muted">
              {sessionId
                ? t("ui.components.overlays.EscPanel.8b110273127f")
                : t("ui.components.overlays.EscPanel.e0dc8feee54d")}
            </p>
          )}
        </div>
        {mountedName && sessionId ? (
          <Button
            size="xs"
            variant="secondary"
            disabled={busy === 'unmount'}
            onClick={() =>
              void perform(
                'unmount',
                () => pluginRequest('/agent-mount', 'POST', { session_id: sessionId, name: null }),
                {key: "ui.components.overlays.EscPanel.e747d528c619"},
              )
            }
          >{t("ui.components.overlays.EscPanel.b043df704af8")}</Button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented value={kind} onChange={setKind} options={[
          { value: 'agent', label: t('ui.agent.author.agentsLabel'), icon: <UserRound className="h-4 w-4" /> },
          { value: 'team', label: t('ui.agent.author.teamsLabel'), icon: <Users className="h-4 w-4" /> },
        ]} />
      </div>
      <CatalogToolbar query={query} onQuery={setQuery} count={filtered.length} total={overview.agents.filter(agent => agent.agent_type === kind).length} label={t('ui.esc.search.agents')} />
      {kind === 'team' && <TeamTemplates agents={overview.agents} disabled={!!busy} onChoose={setSeed} />}
      {filtered.length === 0 ? (
        <div className="space-y-3 rounded-xl border border-dashed border-line p-4">
          <EmptyHint
            icon={kind === 'team' ? <Users className="h-4 w-4" /> : <UserRound className="h-4 w-4" />}
            title={t(query.trim() ? 'ui.esc.search.empty' : 'ui.agent.catalog.empty.all.' + kind)}
            description={t(query.trim() ? 'ui.esc.search.emptyHint' : 'ui.agent.catalog.createHint.' + kind)}
            action={query.trim() ? <Button size="sm" variant="secondary" onClick={() => setQuery('')}>{t('ui.agent.catalog.showAll.' + kind)}</Button> : undefined}
          />

        </div>
      ) : (
        filtered.map((agent) => {
          const key = 'agent:' + agent.name
          const isMounted = mountedName === agent.name
          const isTeam = agent.agent_type === 'team'
          const referenceIssues = isTeam ? teamReferenceIssues(agent.members, overview.agents) : []
          return (
            <article key={agent.name} className="space-y-2 rounded-xl border border-line px-3 py-2.5">
              <div className="flex items-start gap-3">
                <AgentAvatar className="h-12 w-12" src={agent.avatar ? '/api/v1/plugins' + agent.avatar + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '') : null} />
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-base font-semibold text-ink">
                    {agent.display_name || agent.name}
                    <Badge tone={isTeam ? 'brand' : 'neutral'} icon={isTeam ? <Users className="h-2.5 w-2.5" /> : <UserRound className="h-2.5 w-2.5" />}>
                      {isTeam ? t("ui.components.overlays.EscPanel.24d26d908128", {p0: (agent.members?.length ?? 0)}) : t("ui.components.overlays.EscPanel.b3924b576cfb")}
                    </Badge>
                    {isMounted ? <Badge tone="ok">{t("ui.components.overlays.EscPanel.ce4c0f69ebcd")}</Badge> : null}
                  </p>
                  {agent.profession ? <p className="mt-1 text-sm text-muted">{agent.profession}</p> : null}
                  <p className="mt-2 text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">
                    {agent.description || t("ui.components.overlays.EscPanel.0042f1887704")}
                  </p>
                  {isTeam && agent.members?.length ? (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {agent.members.map((member) => (
                        <span
                          key={member.name}
                          className="inline-flex items-center gap-1 rounded-md border border-line bg-raised/40 px-1.5 py-0.5 font-mono text-2xs text-muted"
                          title={member.agent ? t("ui.components.overlays.EscPanel.2f88e1c19abe", {p0: (member.agent)}) : member.prompt || member.name}
                        >
                          {member.display_name?.trim() || overview.agents.find(row => row.name === member.agent)?.display_name || member.agent || member.name}
                          <span className={member.role === 'lead' ? 'text-brand' : 'text-faint'}>
                            {member.role === 'lead' ? t("ui.components.overlays.EscPanel.ed99dc5cfe5d") : t("ui.components.overlays.EscPanel.6e6d6ddbb7c1")}
                          </span>
                          {member.model ? <span className="text-faint">{member.model}</span> : null}
                          {member.runner ? <span className="text-faint">· {member.runner === 'codex' ? 'Codex' : member.runner === 'claude' ? 'Claude Code' : 'OpenCode'}</span> : null}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <Switch
                  checked={agent.enabled}
                  disabled={busy === key + ':toggle'}
                  onChange={(next) =>
                    void perform(
                      key + ':toggle',
                      () => pluginRequest('/agents/' + encodeURIComponent(agent.name) + '/enabled', 'POST', { enabled: next, session_id: sessionId }),
                      next ? {key: "ui.components.overlays.EscPanel.17d2594b6241", params: {p0: (agent.name)}} : {key: "ui.components.overlays.EscPanel.dad663a8630a", params: {p0: (agent.name)}},
                    )
                  }
                  label={t(agent.enabled ? 'ui.catalog.disableNamed' : 'ui.catalog.enableNamed', { name: agent.name })}
                />
              </div>
              {referenceIssues.length > 0 && <ul className="space-y-1 text-xs text-warn [overflow-wrap:anywhere]" aria-label={t('ui.agent.team.referenceIssues')}>
                {referenceIssues.map(issue => <li key={issue.member}>{t('ui.agent.team.reference.' + issue.reason, { member: issue.member, agent: issue.agent })}</li>)}
              </ul>}
              <div className="flex flex-wrap items-center gap-2">
                {isTeam && sessionId && agent.enabled ? (
                  <Button size="xs" variant="primary"
                    disabled={!!busy || referenceIssues.length > 0 || overview.capabilities?.persistent_team_selection !== true}
                    onClick={() => {
                      const isolated = isMounted && mount?.team_worktrees === true
                      const submittedDraft = useDraftStore.getState().bySession[sessionId]?.text ?? ''
                      void perform(key + ':select', async () => {
                        const result = await pluginRequest<{ mount: AgentMountState }>('/agent-selection', 'POST', {
                          session_id: sessionId, name: agent.name,
                          ...(isolated ? { team_worktrees: true, team_working_copy: mount?.team_working_copy === true } : {}),
                        })
                        useChatStore.getState().setAgentMount(sessionId, result.mount)
                      }, {key: 'ui.agent.teamBound'}).then(confirmed => {
                        if (!confirmed || useAppStore.getState().activeSessionId !== sessionId) return
                        const drafts = useDraftStore.getState()
                        if ((drafts.bySession[sessionId]?.text ?? '') === submittedDraft) drafts.setText(sessionId, draft)
                        useAppStore.getState().setSettingsOpen(false)
                        // 等待弹窗归还焦点后回到正文，避免再点一次输入框。
                        requestAnimationFrame(() => requestAnimationFrame(() => {
                          const state = useAppStore.getState()
                          if (state.activeSessionId === sessionId && !state.settingsOpen) {
                            document.querySelector<HTMLTextAreaElement>('textarea[aria-autocomplete="list"]')?.focus()
                          }
                        }))
                      })
                    }}
                  >{t(draft.trim() ? 'ui.agent.author.chatWithDraft' : 'ui.agent.author.chatWithTeam')}</Button>
                ) : null}
                {isTeam && sessionId && agent.enabled && overview.capabilities?.persistent_team_selection !== true ? <p className="text-2xs text-muted">{t('ui.composerAgents.upgradeTeam')}</p> : null}
                {!isTeam && sessionId && agent.enabled && !isMounted ? (
                  <Button
                    size="xs"
                    variant="primary"
                    disabled={!!busy}
                    onClick={() => void perform(key + ':mount',
                      () => pluginRequest('/agent-mount', 'POST', { session_id: sessionId, name: agent.name }),
                      {key: 'ui.components.overlays.EscPanel.bda6a13ef909', params: { p0: agent.display_name || agent.name }}
                    ).then(confirmed => {
                      if (confirmed && useAppStore.getState().activeSessionId === sessionId) {
                        useAppStore.getState().setSettingsOpen(false)
                        requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-autocomplete="list"]')?.focus())
                      }
                    })}
                  >{t('ui.agent.author.chatWithAgent')}</Button>
                ) : null}
                <Button size="xs" variant="ghost" onClick={() => setDetails(agent)}>{t('ui.agent.author.details')}</Button>
                <details className="text-2xs text-muted"><summary className="inline-flex h-6 cursor-pointer items-center gap-2 rounded-md px-2 font-medium hover:bg-raised hover:text-ink">{t('ui.personality.moreActions')}</summary><div className="mt-2 flex flex-wrap gap-2 rounded-lg border border-line p-2">
                <Button size="xs" variant="ghost" onClick={() => setSeed({ ...agent })}>{t("ui.agent.author.copy")}</Button>
                <Button size="xs" variant="ghost" onClick={() => void perform(key + ":export", async () => {
                  const data = await pluginRequest<{ markdown: string }>("/agents/" + encodeURIComponent(agent.name) + "/export" + (sessionId ? "?session_id=" + encodeURIComponent(sessionId) : ""))
                  const url = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown;charset=utf-8" }))
                  const link = document.createElement("a"); link.href = url; link.download = agent.name + "-AGENTS.md"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
                }, {key: "ui.agent.author.exported"})}>{t("ui.agent.author.export")}</Button>
                {agent.edit_scope && <Button size="xs" variant="ghost" disabled={busy === key + ':edit'} onClick={() => void perform(key + ':edit', async () => {
                  const data = await pluginRequest<{ agent: AgentRow; revision: string }>('/agents/' + encodeURIComponent(agent.name) + '/edit' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''))
                  if (data.agent?.name !== agent.name || typeof data.revision !== 'string' || !/^[a-f0-9]{64}$/.test(data.revision)) throw new ResourceError({key: 'ui.http.invalidResponse'})
                  return { ...data.agent, edit_revision: data.revision }
                }, {key: 'ui.agent.author.editLoaded'}, result => setSeed(result as AgentRow))}>{t('ui.agent.author.edit')}</Button>}
                {agent.edit_scope && <Button size="xs" variant="ghost" disabled={!!busy} onClick={() => setRemoving(agent.name)}><Trash2 className="h-3 w-3" />{t('ui.components.overlays.EscPanel.2f9daa828907')}</Button>}
                </div></details>

              </div>
              {removing === agent.name && <div role="alert" className="space-y-3 rounded-lg border border-line bg-raised p-3"><p className="text-sm">{t('ui.agent.author.deleteConfirm', {name: agent.display_name || agent.name})}</p><div className="flex gap-2"><Button size="sm" disabled={!!busy} onClick={() => void perform(key + ':remove', async () => {
                await pluginRequest('/agents/' + encodeURIComponent(agent.name) + '?scope=' + agent.edit_scope + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), 'DELETE')
                try { if (agent.edit_scope) removeAgentEditDrafts(sessionStorage, agent.edit_scope, agent.name) }
                catch { return false }
                return true
              }, {key: 'ui.components.overlays.EscPanel.14026bb4d35a', params: {p0: agent.name}}, cleaned => {
                if (!cleaned) useAppStore.getState().toast({title: {key: 'ui.agent.author.draftCleanupFailed'}, tone: 'warn'})
                setSeed(current => current === seed && current?.name === agent.name ? null : current)
                setRemoving(current => current === agent.name ? null : current)
              })}>{t('ui.agent.author.deleteAction')}</Button><Button size="sm" variant="secondary" disabled={!!busy} onClick={() => setRemoving(null)}>{t('ui.personality.cancelDelete')}</Button></div></div>}

            </article>
          )
        })
      )}

      <InstallDisclosure title={t(kind === 'team' ? 'ui.agent.install.teamTitle' : "ui.components.overlays.EscPanel.039b583b88a3")}>
        {(close) => (
          <>
            <DirectoryField value={installPath} onChange={setInstallPath} label={t(kind === 'team' ? 'ui.agent.install.teamDirectory' : "ui.components.overlays.EscPanel.508c384318d7")} disabled={!!busy} />
            <ScopeSelect value={scope} onChange={setScope} />
            <Button
              size="xs"
              disabled={!installPath.trim() || busy === 'agent:install'}
              onClick={() =>
                void perform(
                  'agent:install',
                  () => pluginRequest('/agents/install', 'POST', { path: installPath.trim(), scope, session_id: sessionId }),
                  {key: "ui.components.overlays.EscPanel.3c008a7f5405"},
                  () => { setInstallPath(''); close() },
                )
              }
            >{t("ui.components.overlays.EscPanel.e8f88f51ccb0")}</Button>
          </>
        )}
      </InstallDisclosure>
    </div>
  )
}

/* ============================ 技能 ============================ */

function SkillsTab({
  overview,
  sessionId,
  busy,
  perform,
}: {
  overview: Overview
  sessionId: string | null
  busy: string
  perform: (key: string, op: () => Promise<unknown>, receipt: ResourceText, onApplied?: (result: unknown) => void) => Promise<boolean>
}) {
  useLocale()

  const [query, setQuery] = useState('')
  const [origin, setOrigin] = useState<CatalogScope>('all')
  const filtered = overview.skills.filter(skill => (origin === 'all' || catalogOrigin(skill.source) === origin) && matchesCatalog(query, [skill.name, skill.interface?.display_name, skill.interface?.short_description, skill.description, skill.when_to_use]))

  const [installPath, setInstallPath] = useState('')
  const [scope, setScope] = useState<'user' | 'project'>('user')

  if (!overview.enabled.skills) {
    return <EmptyHint icon={<Blocks className="h-4 w-4" />} title={t("ui.components.overlays.EscPanel.2651fe939f4a")} description={t("ui.components.overlays.EscPanel.e61958782a7a")} />
  }

  return (
    <div className="space-y-3">
      <p className="rounded-lg bg-raised/40 p-3 text-xs leading-relaxed text-muted">{t('ui.extensions.skillDirectories')}</p>
      <AgentCreator kind="skill" sessionId={sessionId} disabled={!!busy} />
      <CatalogToolbar query={query} onQuery={setQuery} scope={origin} onScope={setOrigin} count={filtered.length} total={overview.skills.length} label={t("ui.esc.search.skills")} />
      {filtered.length === 0 ? (
        <EmptyHint
          icon={<Blocks className="h-4 w-4" />}
          title={t(overview.skills.length ? "ui.esc.search.empty" : "ui.components.overlays.EscPanel.d3033c6a6bb0")}
          description={t(overview.skills.length ? "ui.esc.search.emptyHint" : "ui.components.overlays.EscPanel.7cfbb67c77e1")}
        />
      ) : (
        filtered.map((skill) => {
          const key = 'skill:' + skill.name
          return (
            <div key={skill.name} className="flex items-start gap-3 rounded-xl border border-line px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-ink">
                  {skill.interface?.display_name || skill.name}
                  {skill.model_invocable === false ? <Badge title={t("ui.components.overlays.EscPanel.5b58ecc81f5c")}>{t("ui.components.overlays.EscPanel.46e152919d00")}</Badge> : null}
                  {skill.user_invocable === false ? <Badge title={t("ui.components.overlays.EscPanel.186b65bff6f5")}>{t("ui.components.overlays.EscPanel.3edd177b9b74")}</Badge> : null}
                </p>
                {skill.interface?.display_name && skill.interface.display_name !== skill.name ? <p className="font-mono text-2xs text-faint">{skill.name}</p> : null}
                <p className="mt-0.5 text-2xs leading-relaxed text-faint [overflow-wrap:anywhere]">
                  {skill.interface?.short_description || skill.description || t("ui.components.overlays.EscPanel.0042f1887704")}
                </p>
                {skill.configuration_errors?.map((error, index) => <p key={index} role="alert" className="mt-1 text-xs text-warn [overflow-wrap:anywhere]">{error}</p>)}
                {skill.when_to_use ? (
                  <p className="mt-0.5 text-2xs text-faint [overflow-wrap:anywhere]">{t("ui.components.overlays.EscPanel.57351f9e602d")}{skill.when_to_use}
                  </p>
                ) : null}
                <p className="mt-0.5 text-2xs text-faint"><span title={skill.path ?? skill.source}>{t('ui.esc.search.' + catalogOrigin(skill.source))}</span></p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {sessionId && skill.enabled && skill.user_invocable !== false ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    title={t("ui.components.overlays.EscPanel.03931b6888f7")}
                    onClick={() => {
                      const drafts = useDraftStore.getState()
                      drafts.setText(sessionId, selectComposerSkill(skill, drafts.bySession[sessionId]?.text ?? ''))
                      useAppStore.getState().setSettingsOpen(false)
                    }}
                  >
                    <Wand2 className="h-3 w-3" />
                    /skill
                  </Button>
                ) : null}
                {skill.removal_scope ? <ConfirmRemoval key={JSON.stringify([sessionId, skill.name, skill.removal_scope, skill.path])} name={skill.name}
                  prompt={t('ui.catalog.removeSkillConfirm', { name: skill.name })} disabled={!!busy}
                  onConfirm={() => perform(key + ':remove',
                    () => pluginRequest('/skills/' + encodeURIComponent(skill.name) + '?scope=' + skill.removal_scope + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), 'DELETE'),
                    {key: "ui.components.overlays.EscPanel.14026bb4d35a", params: {p0: skill.name}})} /> : null}
                <Switch
                  checked={skill.enabled}
                  disabled={busy === key + ':toggle' || skill.plugin_enabled === false}
                  onChange={(next) =>
                    void perform(
                      key + ':toggle',
                      () => pluginRequest('/skills/' + encodeURIComponent(skill.name) + '/enabled', 'POST', { enabled: next, session_id: sessionId }),
                      next ? {key: "ui.components.overlays.EscPanel.17d2594b6241", params: {p0: (skill.name)}} : {key: "ui.components.overlays.EscPanel.dad663a8630a", params: {p0: (skill.name)}},
                    )
                  }
                  label={t(skill.enabled ? 'ui.catalog.disableNamed' : 'ui.catalog.enableNamed', { name: skill.name })}
                />
              </div>
            </div>
          )
        })
      )}

      <InstallDisclosure title={t("ui.components.overlays.EscPanel.24c447c7e9d3")}>
        {(close) => (
          <>
            <DirectoryField value={installPath} onChange={setInstallPath} label={t("ui.components.overlays.EscPanel.d8c6a7b11788")} disabled={!!busy} />
            <ScopeSelect value={scope} onChange={setScope} />
            <Button
              size="xs"
              disabled={!installPath.trim() || busy === 'skill:install'}
              onClick={() =>
                void perform(
                  'skill:install',
                  () => pluginRequest('/skills/install', 'POST', { path: installPath.trim(), scope, session_id: sessionId }),
                  {key: "ui.components.overlays.EscPanel.c4e6a6e3fabe"},
                  () => { setInstallPath(''); close() },
                )
              }
            >{t("ui.components.overlays.EscPanel.e8f88f51ccb0")}</Button>
          </>
        )}
      </InstallDisclosure>
    </div>
  )
}
