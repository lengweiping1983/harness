import {useSessionNotificationStore} from '@/store/sessionNotifications'
import {SessionListSkeleton} from './SessionListSkeleton'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {SessionRunningIndicator} from '@/components/chat/SessionRunningIndicator'
import {useSessionRunning} from '@/store/sessionActivity'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Download,
  Folder,
  FolderOpen,
  FolderPlus,
  Layers,
  MessageSquare,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Settings,
  Trash2,
  X,
} from 'lucide-react'
import { WorkspaceAction } from '@/components/ui/WorkspaceAction'
import { sessionTitle } from '@/lib/sessionTitle'
import type { SessionSummary } from '@/types'
import { cn, formatRelativeTime } from '@/lib/utils'
import { HOTKEYS, formatHotkey } from '@/lib/hotkeyCatalog'
import {
  GROUP_MODES,
  searchSidebarGroups,
  collapseSessions,
  type GroupMode,
  type SidebarGroup,
} from '@/lib/sidebarGroups'
import { recoverProjectCatalog, useAppStore } from '@/store/app'
import { ContextActionMenu, type ContextMenuAnchor } from '@/components/ui/ContextActionMenu'
import { RenameDialog } from '@/components/overlays/RenameDialog'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import {createInflightOperations} from '@/lib/inflightOperations'
import { Button, EmptyHint, IconButton, Kbd, ShowMoreControl, Tooltip } from '@/components/ui/primitives'

/**
 * 项目侧栏。
 *
 * 这一层不是"把会话列表换个皮肤"，而是把 **Workspace → Session** 的领域模型
 * 真正摆到界面上：
 *
 *   - 会话按项目分组；项目有文件夹图标、计数、自己的菜单（在此新建 / 重命名 / 移除）；
 *   - 三种分组方式：按项目 / 项目树 / 扁平，选择持久化；
 *   - 折叠的组默认露 5 条，剩余不足 5 条一并显示，其余交给"显示更多"；
 *   - 搜索同时匹配会话与**项目名**——搜"harness"应该看到整个 harness 项目；
 *   - 底部固定设置入口。
 *
 * 会话使用单行标题与小号时间，减少装饰，让侧栏能容纳更多对话。
 */

type MenuEvent = React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>

const operateOnce = createInflightOperations()

const GROUP_BY_KEY = 'harness.sidebar.group-by'
const COLLAPSED_KEY = 'harness.sidebar.collapsed'
const DEFAULT_VISIBLE = 5

function readGroupMode(): {value: GroupMode; writable: boolean} {
  try {
    const saved = localStorage.getItem(GROUP_BY_KEY)
    if (saved === null) return {value: 'workspace', writable: true}
    if (saved === 'workspace' || saved === 'tree' || saved === 'flat') return {value: saved, writable: true}
  } catch { /* 保留不可读的原始偏好 */ }
  return {value: 'workspace', writable: false}
}

function readCollapsed(): {value: Set<string>; writable: boolean} {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY)
    const list: unknown = raw === null ? [] : JSON.parse(raw)
    if (Array.isArray(list) && list.every(item => typeof item === 'string')) {
      return {value: new Set(list), writable: true}
    }
  } catch { /* 保留损坏的原始偏好 */ }
  return {value: new Set(), writable: false}
}

export function ProjectSidebar() {
  const locale = useLocale()

  const sessions = useAppStore((s) => s.sessions)
  const workspaces = useAppStore((s) => s.workspaces)
  const workspacesError = useAppStore((s) => s.workspacesError)
  const workspacesLoading = useAppStore((s) => s.workspacesLoading)
  const loading = useAppStore((s) => s.sessionsLoading || s.workspacesLoading)
  const activeId = useAppStore((s) => s.activeSessionId)
  const activeWorkspaceId = useAppStore((s) => s.activeWorkspaceId)
  const query = useAppStore((s) => s.sessionQuery)
  const setQuery = useAppStore((s) => s.setSessionQuery)

  const select = useAppStore((s) => s.selectSession)
  const create = useAppStore((s) => s.startNewSession)
  const refreshSessions = useAppStore((s) => s.refreshSessions)
  const refreshWorkspaces = useAppStore((s) => s.refreshWorkspaces)
  const removeSession = useAppStore((s) => s.removeSession)
  const removeWorkspace = useAppStore((s) => s.removeWorkspace)
  const setSettingsOpen = useAppStore((s) => s.setSettingsOpen)
  const setWorkspacePickerOpen = useAppStore((s) => s.setWorkspacePickerOpen)
  const toast = useAppStore((s) => s.toast)

  const [storedMode] = useState(readGroupMode)
  const [storedCollapsed] = useState(readCollapsed)
  const [storageError, setStorageError] = useState(!storedMode.writable || !storedCollapsed.writable)
  const [mode, setMode] = useState<GroupMode>(storedMode.value)
  const [showArchived, setShowArchived] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<string>>(storedCollapsed.value)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [menu, setMenu] = useState<ContextMenuAnchor | null>(null)
  const [renaming, setRenaming] = useState<{ kind: 'session' | 'workspace'; id: string; value: string } | null>(
    null,
  )

  const groups = useMemo(
    () => searchSidebarGroups(sessions.filter((s) => Boolean(s.metadata?.archived) === showArchived), workspaces,
      !workspaces.length && (workspacesError || workspacesLoading) ? 'flat' : mode, collapsed, query),
    [sessions, workspaces, workspacesError, workspacesLoading, mode, collapsed, query, showArchived, locale],
  )

  useEffect(() => {
    const recover = () => {void recoverProjectCatalog()}
    window.addEventListener('online', recover)
    window.addEventListener('focus', recover)
    document.addEventListener('visibilitychange', recover)
    return () => {
      window.removeEventListener('online', recover)
      window.removeEventListener('focus', recover)
      document.removeEventListener('visibilitychange', recover)
    }
  }, [])

  useEffect(() => {
    try {
      if (storedMode.writable) localStorage.setItem(GROUP_BY_KEY, mode)
      if (storedCollapsed.writable) localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]))
    } catch {
      setStorageError(true)
    }
  }, [mode, collapsed, storedMode, storedCollapsed])

  const toggleCollapse = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const toggleExpanded = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const openMenu = useCallback((event: MenuEvent, id: string) => {
    event.preventDefault()
    event.stopPropagation()
    const origin = event.currentTarget
    const rect = origin.getBoundingClientRect()
    const pointer = 'clientX' in event && event.detail !== 0
    setMenu({ id, origin, x: pointer ? event.clientX : rect.left, y: pointer ? event.clientY : rect.bottom + 6, initial: event.type === 'keydown' && (event as React.KeyboardEvent).key === 'ArrowUp' ? 'last' : 'first' })
  }, [])

  const startRename = useCallback((kind: 'session' | 'workspace', id: string, value: string) => {
    setRenaming({ kind, id, value })
    setMenu(null)
  }, [])

  const exportSession = useCallback(
    (session: SessionSummary) => {
      const url = '/api/v1/sessions/' + encodeURIComponent(session.id) + '/export'
      window.open(url, '_blank', 'noopener')
      toast({ title: {key: "ui.components.sidebar.ProjectSidebar.9aaf67962338"}, description: session.title ?? session.id, tone: 'info' })
    },
    [toast],
  )

  const deleteSession = useCallback(
    async (session: SessionSummary) => {
      setMenu(null)
      if (!window.confirm(t("ui.components.sidebar.ProjectSidebar.0734a38f0f7e", {p0: (session.title ?? session.id)}))) return
      await removeSession(session.id)
    },
    [removeSession],
  )

  return (
    /* 根节点必须**透明**：轨道层的底色由 styles.css 的
       `.app-shell > .panel { background: rail }` 提供。这里再刷一层
       bg-surface 会把三级表面体系压平成一片同色，侧栏和中区就没法分层了。 */
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶栏：收起、刷新和新会话。 */}
      <header className="sidebar-tab-header flex items-center gap-1 px-2">
        <WorkspaceAction action="collapse-left" label={t('ui.sidebar.collapse')} data-panel-toggle="left" aria-controls="project-sidebar" onClick={() => useAppStore.getState().toggleSidebar()} />
        <IconButton
          label={t("ui.components.sidebar.ProjectSidebar.aee887434131")}
          disabled={loading}
          onClick={() => {
            void refreshSessions()
            void refreshWorkspaces()
          }}
        >
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
        </IconButton>
        <div className="ml-auto flex items-center gap-1">
          {!showArchived ? <Tooltip content={t("ui.components.sidebar.ProjectSidebar.d48816a87262")}>
            <Button size="sm" variant="ghost" className="sidebar-header-new" aria-label={t("ui.components.sidebar.ProjectSidebar.c57c30bc05f7")} onClick={() => void create()} icon={<MessageSquarePlus className="h-3.5 w-3.5" />}><span>{t("ui.components.sidebar.ProjectSidebar.c57c30bc05f7")}</span></Button>
          </Tooltip> : null}
        </div>
      </header>

      {storageError ? <p role="alert" className="px-3 pb-2 text-xs text-warn">{t('ui.sidebar.preferencesUnconfirmed')}</p> : null}
      {workspacesError ? <div role={workspacesLoading ? 'status' : 'alert'} className="mx-3 mb-2 space-y-2 rounded-lg bg-raised p-3 text-xs">
        <p className="text-muted">{t(workspacesLoading ? 'ui.sidebar.projectsRetrying' : 'ui.store.app.a2de8299587b')}</p>
        {!workspacesLoading ? <><p tabIndex={0} className="max-h-16 overflow-y-auto text-warn [overflow-wrap:anywhere]">{noticeText(workspacesError)}</p><Button size="sm" onClick={() => void refreshWorkspaces()}>{t('ui.sidebar.retryProjects')}</Button></> : null}
      </div> : null}

      {/* 搜索 + 分组方式 */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("ui.components.sidebar.ProjectSidebar.a4f2b4a51bb8")}
            aria-label={t("ui.components.sidebar.ProjectSidebar.84cdbe74d867")}
            className="w-full rounded-lg border border-line bg-raised py-1.5 pl-8 pr-7 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-faint hover:text-ink"
              aria-label={t("ui.components.sidebar.ProjectSidebar.ee32f25f7050")}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>

        <div className="mt-2 flex items-center gap-1">
          <Layers className="h-3 w-3 shrink-0 text-faint" />
          <span className="text-2xs text-faint">{t("ui.components.sidebar.ProjectSidebar.399ee6934772")}</span>
          <div role="group" aria-label={t('ui.components.sidebar.ProjectSidebar.399ee6934772')} className="ml-auto flex items-center gap-0.5 rounded-lg border border-line bg-raised p-0.5">
            {GROUP_MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setMode(item.id)}
                aria-pressed={mode === item.id}
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-2xs transition-colors',
                  mode === item.id ? 'bg-brand/15 text-brand' : 'text-muted hover:text-ink',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 分组列表 */}
      <div className="mx-3 my-1 flex items-center justify-between gap-1">
        <button type="button" className="min-w-0 rounded px-2 py-1 text-left text-xs text-muted hover:bg-raised" onClick={() => setShowArchived(!showArchived)}>{showArchived ? t("ui.components.sidebar.ProjectSidebar.aae94d2480a3") : t("ui.components.sidebar.ProjectSidebar.71a19dcd9d14")}</button>
        <Tooltip content={t("ui.components.sidebar.ProjectSidebar.9c08ce3f5eae")}>
          <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setWorkspacePickerOpen(true)} icon={<FolderPlus className="h-3.5 w-3.5" />}>{t("ui.components.sidebar.ProjectSidebar.79f326be4409")}</Button>
        </Tooltip>
      </div>
      <div aria-busy={loading} className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading && !sessions.length && !workspaces.length ? <SessionListSkeleton /> : groups.length === 0 ? (
          <EmptyHint
            className="mx-1 mt-4"
            icon={<MessageSquare className="h-5 w-5" />}
            title={query ? t("ui.components.sidebar.ProjectSidebar.43a937d227a0") : t("ui.components.sidebar.ProjectSidebar.6c5e17bc9823")}
            description={query ? t("ui.components.sidebar.ProjectSidebar.6f0e8a0ebde2") : t("ui.components.sidebar.ProjectSidebar.8715fe9d17c9")}
          />
        ) : (
          groups.map((group) => (
            <GroupSection
              key={group.key}
              group={group}
              active={activeId}
              activeWorkspaceId={activeWorkspaceId}
              expanded={expandedGroups}
              searching={!!query.trim()}
              menuId={menu?.id ?? null}
              onToggleCollapse={toggleCollapse}
              onToggleExpanded={toggleExpanded}
              onOpenMenu={openMenu}
              onSelect={select}
              onCreate={showArchived ? undefined : create}
            />
          ))
        )}
      </div>

      {/* 底部：设置入口钉死 */}
      <footer className="border-t border-line px-3 py-2">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-muted transition-colors hover:bg-raised hover:text-ink"
        >
          <Settings className="h-3.5 w-3.5" />{t("ui.components.sidebar.ProjectSidebar.df3d58c7d84b")}<span className="ml-auto text-2xs text-faint">
            {formatHotkey(HOTKEYS.settings).split('+').map(key => <Kbd key={key}>{key}</Kbd>)}
          </span>
        </button>
      </footer>

      {menu ? (
        <ContextMenu
          menu={menu}
          groups={groups}
          onStartRename={startRename}
          onExport={exportSession}
          onDelete={deleteSession}
          onCreate={showArchived ? undefined : (workspaceId) => {
            setMenu(null)
            void create(workspaceId)
          }}
          onRemoveWorkspace={(id) => {
            setMenu(null)
            void removeWorkspace(id)
          }}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {renaming ? <RenameDialog key={JSON.stringify([renaming.kind, renaming.id])} target={renaming} onClose={() => setRenaming(null)} /> : null}
    </div>
  )
}

/* ============================ 分组 ============================ */

interface GroupSectionProps {
  searching: boolean
  group: SidebarGroup
  active: string | null
  activeWorkspaceId: string | null
  expanded: Set<string>
  menuId: string | null
  onToggleCollapse: (key: string) => void
  onToggleExpanded: (key: string) => void
  onOpenMenu: (event: MenuEvent, id: string) => void
  onSelect: (id: string) => void
  onCreate?: (workspaceId?: string | null) => void
}

function GroupSection(props: GroupSectionProps) {
  useLocale()

  const {
    group,
    active,
    activeWorkspaceId,
    expanded,
    searching,
    menuId,
    onToggleCollapse,
    onToggleExpanded,
    onOpenMenu,
    onSelect,
    onCreate,
  } = props

  const showAll = searching || expanded.has(group.key)
  const missingDirectory = useAppStore(state => state.workspaces.some(workspace => workspace.id === group.workspaceId && workspace.exists === false))
  // 没有子项目的组永远不折叠：折叠一个只有 2 条会话的组是纯粹的噪音。
  const foldable = !searching && !group.hasChildren && group.sessions.length > DEFAULT_VISIBLE
  const { shown, overflow } = collapseSessions(group.sessions, !showAll, DEFAULT_VISIBLE, active)

  return (
    <section className="group/project mt-1.5">
      <div
        className={cn(
          'group/head flex h-8 items-center gap-1 rounded-lg px-1.5 transition-colors hover:bg-ink/[0.04]',
          group.workspaceId && group.workspaceId === activeWorkspaceId && 'bg-ink/[0.05]',
        )}
        style={{ paddingLeft: 6 + group.depth * 12 }}
      >
        <button
          type="button"
          onClick={() => (!searching && (foldable || group.hasChildren) ? onToggleCollapse(group.key) : undefined)}
          className={cn(
            'flex min-w-0 flex-1 items-center gap-2 text-left',
            (searching || !foldable && !group.hasChildren) && 'cursor-default',
          )}
          aria-expanded={!group.collapsed}
          title={group.path || group.title}
        >
          {group.collapsed ? (
            <Folder className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          ) : (
            <FolderOpen className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          )}
          <span
            className={cn(
              'min-w-0 flex-1 truncate text-compact font-medium',
              group.workspaceId && group.workspaceId === activeWorkspaceId ? 'text-ink' : 'text-muted',
            )}
          >
            {group.title}
          </span>
          {missingDirectory ? <span className="shrink-0 text-2xs text-warn">{t('ui.workspacePicker.missingDirectory')}</span> : null}
          <span className="shrink-0 text-2xs text-faint">{group.sessions.length || ''}</span>
        </button>

        <IconButton
          label={t("ui.components.sidebar.ProjectSidebar.ef430e30fb08")}
          aria-haspopup="menu" aria-expanded={menuId === (group.workspaceId ?? group.key)}
          onKeyDown={(event) => { if (['ArrowDown', 'ArrowUp'].includes(event.key) && !event.nativeEvent.isComposing && event.keyCode !== 229) onOpenMenu(event, group.workspaceId ?? group.key) }}
          className={cn('opacity-0 group-hover/head:opacity-100 group-focus-within/head:opacity-100', menuId === (group.workspaceId ?? group.key) && 'opacity-100')}
          onClick={(event) => onOpenMenu(event, group.workspaceId ?? group.key)}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </IconButton>
      </div>

      {group.collapsed ? null : (
        <div className="mt-0.5 space-y-px">
          {shown.length === 0 ? (onCreate && !missingDirectory ? (
            <button
              type="button"
              onClick={() => void onCreate(group.workspaceId)}
              className="ml-6 rounded-lg px-2 py-1.5 text-left text-2xs text-faint transition-colors hover:bg-raised/60 hover:text-brand"
            >{t("ui.components.sidebar.ProjectSidebar.e5fc62fa56e3")}</button>
          ) : null) : (
            <>
              {shown.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  active={session.id === active}
                  depth={group.depth}
                  menuId={menuId}
                  onSelect={onSelect}
                  onOpenMenu={onOpenMenu}
                />
              ))}
              {overflow > 0 && !showAll ? (
                <ShowMoreControl onClick={() => onToggleExpanded(group.key)} aria-expanded={false} className="ml-6 w-auto text-2xs" />
              ) : null}
              {showAll && !searching && group.sessions.length > DEFAULT_VISIBLE ? (
                <ShowMoreControl onClick={() => onToggleExpanded(group.key)} expanded aria-expanded={true} className="ml-6 w-auto text-2xs">{t("ui.components.sidebar.ProjectSidebar.afd4b783536b")}</ShowMoreControl>
              ) : null}
            </>
          )}
        </div>
      )}
    </section>
  )
}

/* ============================ 会话行 ============================ */

interface SessionRowProps {
  menuId: string | null
  session: SessionSummary
  active: boolean
  depth: number
  onSelect: (id: string) => void
  onOpenMenu: (event: MenuEvent, id: string) => void
}

function SessionRow(props: SessionRowProps) {
  useLocale()

  const {
    menuId,
    session,
    active,
    depth,
    onSelect,
    onOpenMenu,
  } = props

  const title = sessionTitle(session)
  const running = useSessionRunning(session.id)
  const unread = useSessionNotificationStore(state => !active && state.unread.includes(session.id))

  return (
    <div
      onClick={() => onSelect(session.id)}
      onContextMenu={(event) => onOpenMenu(event, session.id)}
      className={cn(
        'group/session relative flex h-8 min-w-0 cursor-pointer items-center gap-2 rounded-lg pl-3 pr-2',
        'transition-colors duration-150 ease-swift',
        active
          ? 'bg-ink/[0.07]'
          : 'hover:bg-ink/[0.04]',
      )}
      style={{ marginLeft: 18 + depth * 12 }}
      role="button"
      tabIndex={0}
      aria-label={title}
      aria-current={active ? 'page' : undefined}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || event.nativeEvent.isComposing || event.keyCode === 229) return
        if (event.key === 'ContextMenu' || event.key === 'F10' && event.shiftKey) onOpenMenu(event, session.id)
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(session.id) }
      }}
      title={session.cwd ? `${title}\n${session.cwd}` : title}
    >
      {unread ? <span role="img" aria-label={t('ui.notifications.unread')} title={t('ui.notifications.unread')} className="h-2 w-2 shrink-0 rounded-full bg-unread" /> : null}
      <span className={cn('min-w-0 flex-1 truncate text-compact leading-5', running && 'pr-6', active ? 'font-medium text-ink' : 'text-ink/85')}>
        {title}
      </span>
      {running ? <SessionRunningIndicator sessionId={session.id} /> : <span className={cn(
        'shrink-0 whitespace-nowrap text-xs leading-4 tabular-nums text-faint transition-opacity',
        'group-hover/session:opacity-0 group-focus-within/session:opacity-0',
        menuId === session.id && 'opacity-0',
      )}>
        {formatRelativeTime(session.last_activity ?? session.updated_at ?? session.created_at)}
      </span>}
      <div className={cn('absolute top-1/2 -translate-y-1/2', running ? 'right-7' : 'right-1')}>
        <IconButton
          label={t("ui.components.sidebar.ProjectSidebar.8968ac45a626")}
          aria-haspopup="menu" aria-expanded={menuId === session.id}
          onKeyDown={(event) => { if (['ArrowDown', 'ArrowUp'].includes(event.key) && !event.nativeEvent.isComposing && event.keyCode !== 229) onOpenMenu(event, session.id) }}
          className={cn('opacity-0 group-hover/session:opacity-100 group-focus-within/session:opacity-100', menuId === session.id && 'opacity-100')}
          onClick={(event) => onOpenMenu(event, session.id)}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </IconButton>
      </div>
    </div>
  )
}

/* ============================ 右键菜单 ============================ */

function ContextMenu(props: {
  menu: ContextMenuAnchor
  groups: SidebarGroup[]
  onStartRename: (kind: 'session' | 'workspace', id: string, value: string) => void
  onExport: (session: SessionSummary) => void
  onDelete: (session: SessionSummary) => void
  onCreate?: (workspaceId: string | null) => void
  onRemoveWorkspace: (id: string) => void
  onClose: () => void
}) {
  useLocale()

  const { menu, groups, onClose } = props
  const sessions = useAppStore((s) => s.sessions)

  const group = groups.find((g) => g.key === menu.id || g.workspaceId === menu.id)
  const missingDirectory = useAppStore(state => state.workspaces.some(workspace => workspace.id === group?.workspaceId && workspace.exists === false))
  const session = !group ? sessions.find((s) => s.id === menu.id) : null

  const items: Array<{ id: string; label: string; icon: React.ReactNode; run: () => void; danger?: boolean }> = []
  if (group) {
    if (props.onCreate && !missingDirectory) items.push({ id: "b4e31cb234ba", label: t("ui.components.sidebar.ProjectSidebar.b4e31cb234ba"), icon: <MessageSquarePlus className="h-3.5 w-3.5" />, run: () => props.onCreate?.(group.workspaceId) })
    if (group.workspaceId) {
      items.push({
        id: "rename-workspace", label: t("ui.components.sidebar.ProjectSidebar.84ba75cf3f85"),
        icon: <Pencil className="h-3.5 w-3.5" />,
        run: () => props.onStartRename('workspace', group.workspaceId!, group.title),
      })
      items.push({
        id: "remove-workspace", label: t("ui.components.sidebar.ProjectSidebar.7b749d300b7a"),
        icon: <Trash2 className="h-3.5 w-3.5" />,
        run: () => props.onRemoveWorkspace(group.workspaceId!),
        danger: true,
      })
    }
  } else if (session) {
    const operate = (path: string, body?: unknown) => {
      const origin = useAppStore.getState()
      return operateOnce(JSON.stringify([session.id, path]), async () => {
        try {
          const data = await runtimeRequest<{ session?: { id: string } }>('/sessions/' + session.id + path, 'POST', body)
          await useAppStore.getState().refreshSessions()
          const latest = useAppStore.getState()
          if (path === '/fork' && data.session && latest.activeSessionId === origin.activeSessionId &&
              latest.newSessionRevision === origin.newSessionRevision && latest.activeWorkspaceId === origin.activeWorkspaceId) {
            latest.selectSession(data.session.id)
          }
        } catch (e) {
          const unconfirmed = e instanceof ApiError && isUnconfirmedWrite(e.failure)
          if (unconfirmed) await useAppStore.getState().refreshSessions()
          useAppStore.getState().toast({ title: {key: unconfirmed ? 'ui.sidebar.operationUnconfirmed' : 'ui.components.sidebar.ProjectSidebar.0c3b4cf7aa25'}, description: failureText(e), tone: 'danger' })
        }
      })
    }
    items.push({ id: "90d990618b19", label: t("ui.components.sidebar.ProjectSidebar.90d990618b19"), icon: <Layers className="h-3.5 w-3.5" />, run: () => void operate('/fork') })
    items.push({ id: "ba801e773984", label: t("ui.components.sidebar.ProjectSidebar.ba801e773984"), icon: <RefreshCw className="h-3.5 w-3.5" />, run: () => void operate('/continue') })
    items.push({ id: "archive", label: session.metadata?.archived ? t("ui.components.sidebar.ProjectSidebar.c4ead787b3f4") : t("ui.components.sidebar.ProjectSidebar.3eecd09d0bae"), icon: <Layers className="h-3.5 w-3.5" />, run: () => void operate('/archive', { archived: !session.metadata?.archived }) })
    items.push({ id: "ac8b027cc1e3", label: t("ui.components.sidebar.ProjectSidebar.ac8b027cc1e3"), icon: <Download className="h-3.5 w-3.5" />, run: () => props.onExport(session) })
    items.push({ id: "0d0cbac2eee5", label: t("ui.components.sidebar.ProjectSidebar.0d0cbac2eee5"), icon: <Pencil className="h-3.5 w-3.5" />, run: () => props.onStartRename('session', session.id, sessionTitle(session)) })
    items.push({ id: "d51250d14a51", label: t("ui.components.sidebar.ProjectSidebar.d51250d14a51"), icon: <Trash2 className="h-3.5 w-3.5" />, run: () => props.onDelete(session), danger: true })
  }

  if (items.length === 0) return null

  return <ContextActionMenu anchor={menu} label={group ? t("ui.components.sidebar.ProjectSidebar.adf049f03279") : t("ui.components.sidebar.ProjectSidebar.e6f6d875638e")} items={items} onClose={onClose} />
}
