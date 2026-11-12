import { FileTypeIcon } from '@/components/ui/FileTypeIcon'
import {INSPECTOR_GROUPS, INSPECTOR_PANELS} from '@/lib/inspectorPanels'
import {WorkspaceTabsContext, type WorkspaceTab, type WorkspaceTabRegistry} from './WorkspaceTabs'
import {TerminalTypeSelect} from './TerminalTypeSelect'
import { t, useLocale, getLocale } from '@/i18n'
import { DeferredPanel } from '@/components/ui/DeferredPanel'
import { loadRequests, loadContext, loadPlans, loadFile, loadTasks, loadRuntime, loadGitWorkspace, loadAcademy } from './panelLoaders'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  MessageSquare,
  BookOpen,
  Activity,
  AlertTriangle,
  ChevronRight,
  CheckCircle2,
  Circle,
  CircleDot,
  File,
  FileCode,
  Folder,
  Gauge,
  Globe,
  GitBranch,
  ListTodo,
  ListOrdered,
  Loader2,
  ScrollText,
  Search,
  Terminal,
  Users,
  Wrench,
} from 'lucide-react'
import type { InspectorTab, ResourceText, WorkspacePayload } from '@/types'
import { api } from '@/lib/api'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { cn, formatBytes } from '@/lib/utils'
import { sessionTitle } from '@/lib/sessionTitle'
import { useActiveSession, useAppStore } from '@/store/app'
import { fileUrl } from '@/lib/fileLinks'
import { basenameOf, parentOf, sessionDirectory } from '@/lib/workspaces'
import { ensureFileWorkspace } from '@/lib/fileWorkspace'
import { useChat } from '@/store/chat'
import { usePageVisible } from '@/hooks/usePageVisible'
import { Badge, Button, EmptyHint, RefreshButton, Section, ShowMoreControl } from '@/components/ui/primitives'
import { WorkspaceAction } from '@/components/ui/WorkspaceAction'
import { BrowserTabs } from '@/components/ui/BrowserTabs'
import { DeferredWorkspace } from './DeferredWorkspace'
import { TemporaryGrants } from './TemporaryGrants'
import { EMPTY_FILE_HISTORY, filePreviewScope, sameFileReference } from '@/lib/filePreviewHistory'
const PANEL_ICONS = {home: Wrench, academy: BookOpen, todos: ListTodo, queue: ListOrdered, changes: FileCode, git: GitBranch,
  files: Folder, terminal: Terminal, browser: Globe, runtime: Activity, context: Gauge, prompts: ScrollText}
const loadMemberDetail = () => import('./MemberDetailPanel').then(module => ({default: module.MemberDetailPanel}))

/**
 * 右侧检查器。
 *
 * 检查器按用途提供独立面板：
 *   Runtime —— 常驻终端 / 子代理 / 后台作业，模型在对话流之外做的事
 *   Todos   —— 当前任务清单（来自 `todo/write`）
 *   Context —— 已提交消息、压缩审计与缓存用量，估算与供应商计量分别展示
 *   Files   —— 工作区只读浏览，点击走 `/api/v1/file` 直链打开
 *   Trace   —— 请求、工具和直属子代理的统一时间线（内部沿用 prompts 页签标识）
 *
 */

export function Inspector({ visible = true }: { visible?: boolean }) {
  useLocale()
  const [workspaceTabs, setWorkspaceTabs] = useState<WorkspaceTabRegistry>({})
  const pageVisible = usePageVisible()
  const observing = visible && pageVisible

  const tab = useAppStore((s) => s.inspectorTab)
  const setTab = useAppStore((s) => s.setInspectorTab)
  const sessionId = useAppStore((s) => s.activeSessionId)
  const title = sessionTitle(useActiveSession())
  const expanded = useAppStore((s) => s.inspectorExpanded)
  const toggleExpanded = useAppStore((s) => s.toggleInspectorExpanded)

  const [openedTabs, setOpenedTabs] = useState<InspectorTab[]>(() => [tab])
  // 页签必须在绘制前入列，避免快速键盘切换丢失刚显示的页签。
  useLayoutEffect(() => {
    setOpenedTabs(current => {
      const retained = tab === 'home' ? current : current.filter(value => value !== 'home')
      return retained.includes(tab) ? retained : [...retained, tab]
    })
  }, [tab])

  const panels = INSPECTOR_PANELS.map(panel => {
    const Icon = PANEL_ICONS[panel.value]
    return {...panel, label: t(panel.labelKey), icon: <Icon className="h-3 w-3" />}
  })

  const memberTabs = useAppStore(state => state.inspectorMemberTabs)
  const sessionMembers = Object.entries(memberTabs).filter(([, entry]) => entry.target.owner === sessionId && entry.target.role !== 'lead')
  const allPanels = [...panels, ...sessionMembers.map(([value, entry]) => ({ value: value as InspectorTab, label: entry.target.name, icon: <Users className="h-3 w-3" /> }))]
  useEffect(() => {
    if (tab.startsWith('member:') && (memberTabs[tab]?.target.owner !== sessionId || memberTabs[tab]?.target.role === 'lead')) setTab('home')
  }, [tab, sessionId, memberTabs, setTab])
  const visiblePanels = [...new Set([...openedTabs, ...sessionMembers.map(([value]) => value as InspectorTab), tab])].filter(value => value !== 'home' || tab === 'home').flatMap(value => { const panel = allPanels.find(panel => panel.value === value); return panel ? [{ ...panel, closable: true, closeLabel: t('ui.deferred.close') + ' · ' + panel.label }] : [] })

  const registered = (kind: 'browser' | 'terminal') => workspaceTabs[kind]?.sessionId === sessionId ? workspaceTabs[kind] : undefined
  const previousWorkspaceTabs = useRef<{sessionId: string | null; browser: number; terminal: number}>({sessionId, browser: 0, terminal: 0})
  useEffect(() => {
    const previous = previousWorkspaceTabs.current
    const counts = {sessionId, browser: registered('browser')?.options.length ?? 0, terminal: registered('terminal')?.options.length ?? 0}
    previousWorkspaceTabs.current = counts
    if (previous.sessionId !== sessionId) return
    // Keep the browser entry available for navigation and restoring its last closed tab.
    const closed = (['terminal'] as const).filter(kind => previous[kind] > 0 && counts[kind] === 0 && registered(kind))
    if (!closed.length) return
    setOpenedTabs(current => current.filter(value => !closed.some(kind => kind === value)))
    if (closed.some(kind => kind === tab)) {
      const index = visiblePanels.findIndex(panel => panel.value === tab)
      const remaining = visiblePanels.filter(panel => !closed.some(kind => kind === panel.value))
      const neighbour = visiblePanels.slice(index + 1).find(panel => remaining.includes(panel))
        ?? visiblePanels.slice(0, index).reverse().find(panel => remaining.includes(panel))
      setTab(neighbour?.value ?? 'home')
    }
  }, [workspaceTabs, sessionId, tab])
  const headerPanels = visiblePanels.flatMap<WorkspaceTab>(panel => {
    if (panel.value !== 'browser' && panel.value !== 'terminal') return [panel]
    const group = registered(panel.value)
    return group?.options.length ? group.options.map(option => ({...option, value: `${panel.value}/${option.value}`})) : [panel]
  })
  const activeGroup = tab === 'browser' || tab === 'terminal' ? registered(tab) : undefined
  const headerValue = activeGroup?.options.some(option => option.value === activeGroup.selected) ? `${tab}/${activeGroup.selected}` : tab
  const resolveWorkspaceTab = (value: string) => {
    const kind: 'browser' | 'terminal' | null = value.startsWith('browser/') ? 'browser' : value.startsWith('terminal/') ? 'terminal' : null
    return kind ? {kind, id: value.slice(kind.length + 1), group: registered(kind)} : null
  }

  const launchPanel = (panel: typeof panels[number]) => <div key={panel.value} className={cn("flex min-w-0 items-center overflow-hidden rounded-lg border border-line bg-surface transition-colors hover:bg-raised focus-within:bg-raised", panel.value === 'terminal' && 'col-span-2 flex-wrap')}>
    <button type="button" onClick={() => {
      if (panel.value === 'browser' || panel.value === 'terminal') {
        const group = registered(panel.value)
        if (group?.options.length) group.create()
      }
      setTab(panel.value)
    }} className={cn("flex min-h-12 min-w-0 flex-1 items-center gap-2 self-stretch rounded-lg px-3 py-3 text-left text-sm text-ink focus-visible:-outline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand", panel.value === 'terminal' && 'min-w-fit')}>
      <span aria-hidden="true" className="shrink-0">{panel.icon}</span><span className={cn("min-w-0", panel.value === 'terminal' && 'whitespace-nowrap')}>{panel.label}</span>
    </button>
    {panel.value === 'terminal' && <div className="ml-auto min-w-0 max-w-full px-3 py-2"><TerminalTypeSelect active={observing && tab === 'home'} /></div>}
  </div>

  return (
    /* 透明根节点：轨道底色交给 styles.css 的 rail 层，这里只画分隔线。
       之前根节点刷 bg-surface，把 L0/L1/L2 三级纵深压成了一片同色。 */
    <WorkspaceTabsContext.Provider value={setWorkspaceTabs}><div className="flex h-full w-full flex-col">
      <div className="inspector-tab-header">
        <div className="flex items-center border-b border-line">
        <BrowserTabs<string>
          label={t("ui.components.inspector.Inspector.fc48a7f2334a")}
          panelId="inspector-tab-content"
          value={headerValue}
          onChange={value => {
            const workspace = resolveWorkspaceTab(value)
            if (workspace) {workspace.group?.select(workspace.id); setTab(workspace.kind); return}
            if (value === 'conversation') {
              useAppStore.setState({ inspectorExpanded: false, drawerIntent: 'none' })
              requestAnimationFrame(() => document.querySelector<HTMLElement>('.conversation-tab-header [role="tab"]')?.focus())
            } else setTab(value as InspectorTab)
          }}
          options={expanded ? [{ value: 'conversation', label: title, title: title, icon: <MessageSquare className="h-3 w-3" />, closable: false, panelId: 'conversation-content' }, ...headerPanels] : headerPanels}
          className="min-w-0 max-w-full"
          trailing={<WorkspaceAction action="new-tab" label={t('ui.workspace.launcher.open')} onClick={() => setTab('home')} />}
          onClose={value => {
            const workspace = resolveWorkspaceTab(value)
            if (workspace) {workspace.group?.close(workspace.id); return}
            if (value === 'conversation') return
            const index = visiblePanels.findIndex(panel => panel.value === value)
            const neighbour = visiblePanels[index + 1] ?? visiblePanels[index - 1]
            if (value === tab) {
              setTab(neighbour?.value ?? 'home')
              if (!neighbour) {
                useAppStore.getState().toggleInspector()
                requestAnimationFrame(() => document.querySelector<HTMLElement>('.conversation-tab-header [role="tab"]')?.focus())
              }
            }
            setOpenedTabs(current => current.filter(item => item !== value))
            if (value.startsWith('member:')) useAppStore.getState().closeMemberTab(value as InspectorTab)
          }}
        />
        <div className="ml-auto flex shrink-0 items-center">
        <WorkspaceAction action={expanded ? 'restore' : 'expand'} label={expanded ? t('ui.components.layout.TitleBar.d4b92ab915c0') : t('ui.components.layout.TitleBar.14eec98c7682')} aria-pressed={expanded} onClick={toggleExpanded} />
        <WorkspaceAction action="collapse-right" label={t('ui.components.layout.TitleBar.808549568cb3')} onClick={() => useAppStore.getState().toggleInspector()} />
        </div>
        </div>
      </div>
      <div className="inspector-section-switcher border-b border-line bg-surface px-3 py-2">
        <select aria-label={t('ui.workspace.switchSection')} className="ui-field w-full min-w-0 bg-surface" value={panels.some(panel => panel.value === tab) ? tab : ''} onChange={event => {
          const next = event.target.value as InspectorTab
          setOpenedTabs(current => current.filter(value => value !== tab || value === 'terminal' || value === 'browser' || value.startsWith('member:')))
          setTab(next)
        }}>
          <option value="" disabled>{t('ui.workspace.switchSection')}</option>
          <option value="home">{t('ui.trace.hub')}</option>
          {INSPECTOR_GROUPS.map(group => <optgroup key={group.id} label={t(group.labelKey)}>
            {panels.filter(panel => (group.panels as readonly string[]).includes(panel.value)).map(panel => <option key={panel.value} value={panel.value}>{panel.label}</option>)}
          </optgroup>)}
        </select>
      </div>
      <div id="inspector-tab-content" data-panel={tab} role="tabpanel" aria-label={allPanels.find(panel => panel.value === tab)?.label} tabIndex={0} className={cn('inspector-tab-content no-scrollbar min-h-0 flex-1', tab.startsWith('member:') ? 'overflow-hidden' : tab === 'browser' ? 'overflow-hidden' : tab === 'terminal' ? 'terminal-tab-content' : 'overflow-y-auto px-3 py-3', tab === 'files' && 'files-tab-content')}>
        {sessionMembers.map(([value, entry]) => <RetainedWorkspace key={value} active={visible && tab === value}><DeferredPanel load={loadMemberDetail} panelProps={{target: entry.target, active: observing && tab === value}} label={entry.target.name} /></RetainedWorkspace>)}
        {tab === 'home' && <section className="space-y-4 p-1" aria-label={t('ui.trace.hub')}>
          {INSPECTOR_GROUPS.map(group => <details key={group.id} open className="group/launcher border-t border-line pt-3">
            <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-lg px-1 text-sm text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand [&::-webkit-details-marker]:hidden">
              <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 transition-transform group-open/launcher:rotate-90" />
              <h3 className="ui-group-title text-muted">{t(group.labelKey)}</h3>
            </summary>
            <nav aria-label={t(group.labelKey)} className="mt-2 grid grid-cols-2 gap-2">{panels.filter(panel => (group.panels as readonly string[]).includes(panel.value)).map(launchPanel)}</nav>
          </details>)}
        </section>}
        <RetainedWorkspace key="academy" active={visible && tab === 'academy'}><DeferredPanel load={loadAcademy} panelProps={{active: observing && tab === 'academy'}} label={t('ui.academy.title')} /></RetainedWorkspace>
        {tab === 'queue' || tab === 'changes' ? <DeferredPanel key={tab} load={loadTasks} panelProps={{mode: tab, active: observing}} label={t(tab === 'queue' ? 'ui.projectBoard.tasks' : 'ui.components.inspector.Inspector.582a8e42e44e')} /> : null}
        {tab === 'runtime' ? <>{sessionId ? <TemporaryGrants key={sessionId} sessionId={sessionId} active={observing} /> : null}<DeferredPanel load={loadRuntime} panelProps={{active: observing, showAgents: false}} label={t('ui.components.inspector.Inspector.75b269496f69')} /></> : null}
        <RetainedWorkspace key={'git:' + sessionId} active={visible && tab === 'git'}><DeferredPanel skeleton load={loadGitWorkspace} panelProps={{active: observing && tab === 'git'}} label={t('ui.workspace.git.title')} /></RetainedWorkspace>
        <RetainedWorkspace key={'terminal:' + sessionId} active={visible && tab === 'terminal'}><DeferredWorkspace kind="terminal" sessionId={sessionId} active={observing && tab === 'terminal'} /></RetainedWorkspace>
        <RetainedWorkspace key={'browser:' + sessionId} active={visible && tab === 'browser'}><DeferredWorkspace kind="browser" sessionId={sessionId} active={observing && tab === 'browser'} /></RetainedWorkspace>
        {tab === 'todos' ? <PlansTab active={observing} /> : null}
        {tab === 'context' ? <ContextTab active={observing} /> : null}
        <RetainedWorkspace key={'files:' + sessionId} active={visible && tab === 'files'}><FilesTab active={observing && tab === 'files'} /></RetainedWorkspace>
        {tab === 'prompts' ? <><DeferredPanel key={sessionId} load={loadRequests} panelProps={{ sessionId, active: observing }} label={t('ui.trace.title')} /><details className="mt-4 border-t border-line pt-3"><summary className="cursor-pointer text-xs text-muted">{t("ui.components.inspector.Inspector.926c503599d7")}</summary><div className="pt-3"><PromptsTab /></div></details></> : null}
      </div>
    </div></WorkspaceTabsContext.Provider>
  )
}

/** Keep visited workspace views until their chat changes. Hidden views suspend observation. */
function RetainedWorkspace({ active, children }: { active: boolean; children: ReactNode }) {
  const [opened, setOpened] = useState(false)
  useEffect(() => { if (active) setOpened(true) }, [active])
  return active || opened ? <div hidden={!active} className="h-full min-h-0">{children}</div> : null
}

/* ============================ Todos ============================ */

function PlansTab({ active }: { active: boolean }) {
  useLocale()

  const sessionId = useAppStore((s) => s.activeSessionId)
  return <div className="space-y-5"><DeferredPanel key={sessionId} load={loadPlans} panelProps={{ sessionId, active }} label={t('ui.inspector.plansAndTodos')} /><TodosTab /></div>
}

function TodosTab() {
  useLocale()

  const sessionId = useAppStore((s) => s.activeSessionId)
  const chat = useChat(sessionId)
  const todos = chat.todos ?? []
  const done = todos.filter((t) => t.status === 'completed').length
  const summary = t('ui.inspector.checklist.progress', {done, total: todos.length})

  if (!todos.length) {
    return <EmptyHint icon={<ListTodo className="h-5 w-5" />} title={t("ui.components.inspector.Inspector.246849f731db")} description={t("ui.components.inspector.Inspector.1657d6691c8f")} />
  }

  return (
    <Section
      title={t("ui.components.inspector.Inspector.a4ea7d95390b")}
      right={
        <Badge tone={done === todos.length ? 'ok' : 'neutral'}>
          {summary}
        </Badge>
      }
    >
      <div role="progressbar" aria-label={t('ui.components.inspector.Inspector.a4ea7d95390b')} aria-valuemin={0} aria-valuemax={todos.length} aria-valuenow={done} aria-valuetext={summary} className="mb-2 h-1 overflow-hidden rounded-full bg-raised">
        <div
          className="h-full rounded-full bg-brand motion-safe:transition-width motion-safe:duration-500 ease-swift"
          style={{ width: `${(done / todos.length) * 100}%` }}
        />
      </div>
      <p className="mb-2 text-2xs leading-relaxed text-faint">{t("ui.components.inspector.Inspector.e0506dfda60a")}</p>
      <ul className="divide-y divide-line/60">
        {todos.map((todo, index) => {
          const completed = todo.status === 'completed'
          const running = todo.status === 'in_progress'
          const pending = !todo.status || todo.status === 'pending'
          const Icon = completed ? CheckCircle2 : running ? CircleDot : Circle
          const status = t(completed ? 'ui.inspector.checklist.completed' : running ? 'ui.inspector.checklist.inProgress' : pending ? 'ui.inspector.checklist.pending' : 'ui.inspector.checklist.unknown')
          return <li key={todo.id ?? index} className={cn('flex items-start gap-2 rounded-md px-2 py-2.5 text-compact', running && 'bg-brand/[0.06]')}>
            <Icon aria-hidden="true" className={cn('mt-0.5 h-4 w-4 shrink-0', completed ? 'text-ok' : running ? 'text-brand' : 'text-faint')} />
            <div className="min-w-0 flex-1 space-y-1">
              <p className={cn('leading-relaxed [overflow-wrap:anywhere]', completed ? 'text-muted line-through' : 'text-ink')}>{todo.content}</p>
              <p className={cn('text-2xs', running ? 'text-brand' : 'text-muted')}>{status}</p>
            </div>
          </li>
        })}
      </ul>
    </Section>
  )
}

/* ============================ Context ============================ */

function ContextTab({ active }: { active: boolean }) {
  const sessionId = useAppStore((s) => s.activeSessionId)
  return sessionId ? <DeferredPanel key={sessionId} load={loadContext} panelProps={{ sessionId, active }} label={t('ui.components.inspector.Inspector.6fc38ef3602f')} /> : <EmptyHint icon={<Gauge className="h-5 w-5" />} title={t('ui.context.noSession.title')} description={t('ui.context.noSession.description')} />
}

/* ============================ Files ============================ */

/**
 * 工作区文件浏览器。
 *
 * 支持目录导航：面包屑跳级、点目录进入、点文件走 `/api/v1/file` 直链，
 * 过滤框在服务端查找当前目录全部名称；每页最多 500 项，按需追加，迟到响应不跨目录。
 */
function FilesTab({ active }: { active: boolean }) {
  useLocale()

  const sessionId = useAppStore((s) => s.activeSessionId)
  const recent = useAppStore((s) => s.inspectorFileHistory[filePreviewScope(sessionId)] ?? EMPTY_FILE_HISTORY)
  const reveal = useAppStore((s) => s.revealInFiles)
  const closeTab = useAppStore((s) => s.closeFileTab)
  const fallbackRoot = useAppStore((s) => s.workspace?.root ?? null)
  const chatRoot = useAppStore(s => sessionDirectory(s.sessions.find(item => item.id === s.activeSessionId) ?? null))
  const workspaces = useAppStore((s) => s.workspaces)
  const activeWorkspaceId = useAppStore((s) => s.activeWorkspaceId)
  const target = useAppStore((s) => s.inspectorFileTarget)

  // 文件面板必须跟着侧栏选中的项目走。之前这里固定用服务端默认根，于是
  // 多项目并行时会出现"侧栏在项目 B，文件面板还停在项目 A"的割裂感 ——
  // 而且用户完全看不出错在哪，只能怀疑自己。
  const activeWorkspace =
    (activeWorkspaceId ? workspaces.find((w) => w.id === activeWorkspaceId) : null) ?? null
  const root = activeWorkspace?.path ?? chatRoot ?? fallbackRoot

  const [retry, setRetry] = useState(0)
  const [pageError, setPageError] = useState<ResourceText | null>(null)
  const paging = useRef(false)
  const [path, setPath] = useState('')
  const [data, setData] = useState<WorkspacePayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ResourceText | null>(null)
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<{ path: string; root: string | null; lineRange?: { start: number; end?: number }; navigationId?: number; focusContent?: boolean } | null>(null)
  const localFileTarget = useRef<number | null>(null)

  // 保留完成的目录及分页，切回工作区无需重复加载；刷新使用新的请求键。
  const loadedKey = useRef<string | null>(null)
  const loadKey = JSON.stringify([path, root, retry, query])
  // 目录切换竞态：快速连点面包屑时，旧请求晚回来会覆盖新目录。
  const reqId = useRef(0)
  const directoryRead = useRef<AbortController>()
  useEffect(() => {
    if (!active || root === null || loadedKey.current === loadKey) {
      if (!active) { paging.current = false; setLoading(false) }
      return () => { reqId.current++; directoryRead.current?.abort() }
    }
    const ticket = ++reqId.current
    const controller = new AbortController()
    directoryRead.current = controller
    setLoading(true)
    setData(null)
    setError(null)
    setPageError(null)
    paging.current = false
    let alive = true
    const timer = setTimeout(() => { void ensureFileWorkspace(root)
      .then(() => alive && ticket === reqId.current ? api.workspace(path, root, {query: query.trim(), signal: controller.signal}) : null)
      .then((result) => {
        if (!alive || ticket !== reqId.current || !result) return
        if (result.ok) {
          loadedKey.current = loadKey
          setData(result.data)
          // 服务端说这个路径不是目录（不存在 / 是文件），退回根目录而不是留白屏。
          if (!result.data.is_dir) {
            setPath('')
          } else if ((result.data.parents ?? []).join('/') !== path) {
            setPath((result.data.parents ?? []).join('/'))
          }
        } else {
          setData(null)
          setError(result.copy ?? result.message)
        }
      })
      .catch(error => {
        if (alive && ticket === reqId.current) { setData(null); setError(failureText(error)) }
      })
      .finally(() => {
        if (alive && ticket === reqId.current) setLoading(false)
      }) }, query.trim() ? 180 : 0)
    return () => {
      alive = false
      controller.abort()
      directoryRead.current?.abort()
      clearTimeout(timer)
      reqId.current++
    }
  }, [path, root, retry, query, active, loadKey])

  async function loadMore() {
    if (!active || !data || data.next_offset == null || loading || paging.current || (data.query ?? '') !== query.trim()) return
    paging.current = true
    const ticket = ++reqId.current
    setLoading(true)
    setPageError(null)
    try {
      const controller = new AbortController()
      directoryRead.current = controller
      const result = await api.workspace(data.path, data.root, {offset: data.next_offset, query: data.query, signal: controller.signal})
      if (ticket !== reqId.current) return
      if (!result.ok) { setPageError(result.copy ?? result.message); return }
      const next = result.data
      if (next.root !== data.root || next.path !== data.path || (next.query ?? '') !== (data.query ?? '')) {
        setPageError({key: 'ui.http.invalidResponse'})
        return
      }
      setData(current => {
        if (!current) return next
        const seen = new Set(current.entries.map(item => item.path))
        return {...next, entries: [...current.entries, ...next.entries.filter(item => !seen.has(item.path))]}
      })
    } catch (error) {
      if (ticket === reqId.current) setPageError(failureText(error))
    } finally {
      if (ticket === reqId.current) { paging.current = false; setLoading(false) }
    }
  }

  // 换项目 = 换整棵树，之前停在 A/src/b 的位置对 B 没有意义。
  useEffect(() => {
    setPath('')
    setQuery('')
    setPreview(null)
  }, [root])

  // 交付物卡片 / diff 投递"看这个文件"：定位到它所在的目录。
  // 用 seq 而不是 path 判定，连续点同一个文件两次也要重新定位。
  useEffect(() => {
    if (!target) { setPreview(null); return }
    if (target.sessionId !== sessionId) return
    if (target.seq !== localFileTarget.current) setQuery('')
    if (target.directory) { setPreview(null); setPath(target.path) }
    else {
      setPreview({ path: target.path, root: target.root === undefined ? root : target.root, lineRange: target.lineRange, navigationId: target.seq, focusContent: target.focusContent })
      setPath(target.path.includes('/') ? parentOf(target.path) ?? '' : '')
    }
  }, [target?.seq])

  const entries = data?.entries ?? []
  const q = query.trim().toLowerCase()
  const shown = entries
  const dirCount = entries.filter((e) => e.type === 'directory').length
  const fileCount = entries.length - dirCount
  const tabKey = (reference: typeof recent[number]) => JSON.stringify([reference.root, reference.path, reference.lineRange])
  const activeFile = preview ? recent.find((reference) => sameFileReference(reference, preview)) : undefined

  if (root === null) {
    return <EmptyHint icon={<Folder className="h-5 w-5" />} title={t("ui.components.inspector.Inspector.7e95bb1f091e")} />
  }
  if (data && !data.exists) {
    return (
      <EmptyHint
        icon={<Folder className="h-5 w-5" />}
        title={t("ui.components.inspector.Inspector.85c016964ce0")}
        description={path || root}
      />
    )
  }

  return (
    <div className="files-workspace" role="region" aria-label={t("ui.components.inspector.Inspector.8e8343f9178e")}>
      <div className="files-content-pane min-w-0 min-h-0">
      {recent.length ? <div className="file-tabs-panel">
        <BrowserTabs
          label={t("ui.components.inspector.Inspector.7d0d4f1e85fa")}
          panelId="file-tab-content"
          className="file-tabs"
          value={activeFile ? tabKey(activeFile) : null}
          onChange={(key) => {
            const reference = recent.find((reference) => tabKey(reference) === key)!
            reveal(reference.path, reference.lineRange, { root: reference.root, focusContent: false })
          }}
          onClose={(key) => closeTab(recent.find((reference) => tabKey(reference) === key)!)}
          options={recent.map((reference) => {
            const name = reference.path.split(/[\\/]/).pop() ?? reference.path
            const line = reference.lineRange ? `:${reference.lineRange.start}${reference.lineRange.end ? '-' + reference.lineRange.end : ''}` : ''
            return { value: tabKey(reference), label: `${name}${line}`, icon: <FileTypeIcon path={reference.path} />, title: `${reference.root ?? t("ui.components.inspector.Inspector.9775da628cb9")} / ${reference.path}${line}`, closeLabel: t("ui.components.inspector.Inspector.5f8f99b6d263", {p0: (name), p1: (line)}) }
          })}
        />
        {preview ? <div id="file-tab-content" role="tabpanel" aria-label={preview.path.split(/[\\/]/).pop()}>
          <DeferredPanel key={preview.root + '|' + preview.path} load={loadFile} label={t('ui.components.inspector.Inspector.39932f24fe11')} panelProps={{ active, path: preview.path, root: preview.root, lineRange: preview.lineRange, navigationId: preview.navigationId, focusContent: preview.focusContent }} />
        </div> : <div id="file-tab-content" role="tabpanel" aria-label={t("ui.components.inspector.Inspector.f3c5fe4dd2da")} className="px-3 py-4 text-xs text-muted">{t("ui.components.inspector.Inspector.dc0b76d9b2b3")}</div>}
      </div> : <div className="files-empty-preview"><EmptyHint icon={<File className="h-5 w-5" />} title={t("ui.components.inspector.Inspector.22616b55b949")} description={t("ui.components.inspector.Inspector.1a6fc2f8d79b")} /></div>}
      </div>
      <aside aria-label={t("ui.components.inspector.Inspector.9b041cb431fc")} className="files-tree-pane refresh-action-container min-w-0 min-h-0 space-y-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        {/* 面包屑：根 / a / b，点哪一级跳哪一级 */}
        <nav aria-label={t("ui.components.inspector.Inspector.7f5d4daa8297")} className="flex min-w-0 flex-1 flex-wrap items-center gap-0.5 text-2xs">
          <button
            type="button"
            onClick={() => {
              setPath('')
              setQuery('')
            }}
            aria-current={path === '' ? 'page' : undefined}
            className={cn(
              'max-w-40 truncate rounded px-1 py-0.5 transition-colors hover:bg-raised',
              path === '' ? 'font-semibold text-ink' : 'text-muted',
            )}
            title={root}
          >
            {activeWorkspace?.name ?? basenameOf(root)}
          </button>
          {(data?.parents ?? []).map((part, index) => {
            const target = data!.parents.slice(0, index + 1).join('/')
            const last = index === data!.parents.length - 1
            return (
              <span key={target} className="flex items-center gap-0.5">
                <ChevronRight className="h-3 w-3 text-faint" />
                <button
                  type="button"
                  onClick={() => {
                    setPath(target)
                    setQuery('')
                  }}
                  aria-current={last ? 'page' : undefined}
                  className={cn(
                    'max-w-32 truncate rounded px-1 py-0.5 transition-colors hover:bg-raised',
                    last ? 'font-semibold text-ink' : 'text-muted',
                  )}
                  title={target}
                >
                  {part}
                </button>
              </span>
            )
          })}
        </nav>
        <div className="flex shrink-0 items-center gap-1">
        <RefreshButton size="xs" label={t('ui.worktrees.refresh')} disabled={loading || !active} onClick={() => setRetry(value => value + 1)} />
        {loading ? (
          <Badge tone="brand" className="shrink-0">{t("ui.components.inspector.Inspector.465c03b272ea")}</Badge>
        ) : (
          <Badge tone="neutral" className="shrink-0">
            {t('ui.files.directorySummary', { directories: dirCount, files: fileCount })}</Badge>
        )}
        </div>
      </div>

      <Button size="xs" className="w-full" disabled={!sessionId} onClick={() => useAppStore.getState().setQuickOpen(true)}><Search className="h-3 w-3" />{t('ui.quickOpen.title')}</Button>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-faint" />
        <input
          value={query}
          maxLength={256}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("ui.components.inspector.Inspector.741393f205b1")}
          aria-label={t("ui.components.inspector.Inspector.bf84258390d6")}
          className="h-7 w-full rounded-md border border-line bg-raised/50 pl-7 pr-2 text-2xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
        />
      </div>

      {error ? (
        <div><EmptyHint icon={<AlertTriangle className="h-5 w-5" />} title={t("ui.components.inspector.Inspector.6aa27f14bff1")} description={noticeText(error)} /><Button size="xs" onClick={() => setRetry(value => value + 1)}>{t("ui.App.b8784c8dd563")}</Button></div>
      ) : loading && !data ? (
        <p role="status" className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.files.loading")}</p>
      ) : shown.length === 0 ? (
        <EmptyHint
          icon={<Folder className="h-5 w-5" />}
          title={q ? t("ui.components.inspector.Inspector.90df9f728c26") : t("ui.components.inspector.Inspector.effa59591f89")}
          description={q ? t("ui.components.inspector.Inspector.0071d03fee04", {p0: (query)}) : undefined}
        />
      ) : (
        <ul className="space-y-0.5">
          {shown.map((entry) => {
            const isDir = entry.type === 'directory'
            // 目录用 <button>、文件用 <a href>：无 href 的 <a> 语义会退化成 generic，
            // 键盘不可聚焦，读屏也拿不到"可进入目录"的信息。
            const inner = (
              <>
                {isDir ? <Folder aria-hidden className="h-4 w-4 shrink-0 text-brand" /> : <FileTypeIcon path={entry.name} />}
                <span
                  className={cn(
                    'min-w-0 flex-1 truncate text-left',
                    isDir ? 'font-medium text-ink' : 'text-muted',
                  )}
                  title={entry.name}
                >
                  {entry.name}
                </span>
                {isDir ? (
                  <ChevronRight className="h-3 w-3 shrink-0 text-faint" />
                ) : entry.size != null ? (
                  <span className="shrink-0 tabular-nums text-faint" title={`${entry.size.toLocaleString()} B`}>
                    {formatBytes(entry.size, 'KiB')}
                  </span>
                ) : null}
              </>
            )
            const rowClass =
              'flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-2xs transition-colors hover:bg-raised/70'
            return (
              <li key={entry.path}>
                {isDir ? (
                  <button
                    type="button"
                    onClick={() => {
                      setQuery('')
                      setPath(entry.path)
                    }}
                    aria-label={t("ui.components.inspector.Inspector.59171fb1d86d", {p0: (entry.name)})}
                    className={rowClass}
                  >
                    {inner}
                  </button>
                ) : (
                  <a
                    href={fileUrl(entry.path, { root })}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(event) => { event.preventDefault(); reveal(entry.path, undefined, { root }); localFileTarget.current = useAppStore.getState().inspectorFileTarget?.seq ?? null }}
                    aria-label={t("ui.components.inspector.Inspector.2a2e54497bb9", {p0: (entry.name)})}
                    aria-current={preview?.root === root && preview.path === entry.path ? 'true' : undefined}
                    className={cn(rowClass, preview?.root === root && preview.path === entry.path && 'bg-brand/10 text-brand')}
                  >
                    {inner}
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {pageError && <p role="alert" className="break-words text-xs text-danger">{noticeText(pageError)}</p>}
      {data?.next_offset != null ? <div className="space-y-2">
        <p className="text-2xs text-faint">{t("ui.files.loaded", {count: entries.length})}</p>
        <ShowMoreControl disabled={loading} onClick={() => void loadMore()}>{loading ? <Loader2 className="h-3 w-3 animate-spin" /> : null}{pageError ? t("ui.App.b8784c8dd563") : t("ui.showMore")}</ShowMoreControl>
      </div> : null}
      </aside>
    </div>
  )
}

/* ============================ Prompts ============================ */

const PROMPT_LABEL: Record<string, string> = {
  get system() { return t("ui.components.inspector.Inspector.0894b78b866e") },
  get developer() { return t("ui.lib.requestTraces.e6e6e10ae4af") },
}

/**
 * 本次会话实际发给模型的系统/开发者提示词。
 *
 * 它**不在对话流里**——对话是人和模型的交流，灌进来的 harness 指令不该占一屏。
 * 但排问题的时候它又是最常被怀疑的一环（"为什么它不调这个工具"、"为什么它以为
 * 自己是 Windows"），所以给一个只读出口而不是彻底藏起来。
 */
function PromptsTab() {
  useLocale()

  const sessionId = useAppStore((s) => s.activeSessionId)
  const chat = useChat(sessionId)
  const prompts = chat.prompts ?? []

  if (!prompts.length) {
    return (
      <EmptyHint
        icon={<ScrollText className="h-5 w-5" />}
        title={t("ui.components.inspector.Inspector.f6587bb57896")}
        description={t("ui.components.inspector.Inspector.7e138f395cee")}
      />
    )
  }

  return (
    <div className="space-y-4">
      <p className="px-0.5 text-2xs leading-relaxed text-faint">{t('ui.prompts.summary', { count: prompts.length, characters: prompts.reduce((sum, prompt) => sum + Array.from(prompt.text).length, 0).toLocaleString(getLocale()) })}</p>
      {prompts.map((prompt, index) => (
        <Section
          key={`${prompt.role}-${index}`}
          title={Object.hasOwn(PROMPT_LABEL, prompt.role) ? PROMPT_LABEL[prompt.role] : prompt.role}
          right={<Badge tone="neutral">{t('ui.prompts.characters', { count: Array.from(prompt.text).length.toLocaleString(getLocale()) })}</Badge>}
        >
          <pre className="max-h-panel overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-raised/40 px-2.5 py-2 font-mono text-2xs leading-relaxed text-muted">
            {prompt.text}
          </pre>
        </Section>
      ))}
    </div>
  )
}
