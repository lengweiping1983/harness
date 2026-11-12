import { listVisibleCount } from './listPagination'
import { t } from '@/i18n'
import type { SessionSummary, WorkspaceInfo } from '@/types'
import {
  basenameOf,
  groupSessionsByWorkspace,
  isAncestor,
  matchesQuery,
  normalizePath,
} from './workspaces'

/**
 * 侧栏的分组编排。
 *
 * 刻意做成**纯函数模块**：三种分组方式（按项目 / 项目树 / 扁平）的折叠、
 * 搜索过滤、父子嵌套都是可单测的逻辑，组件只负责画。这样"分组错了"这类
 * 问题可以在没有浏览器的情况下精确定位，而不是靠肉眼在 300 行 JSX 里找。
 *
 * 分组方式
 * --------
* ``workspace``：每个注册项目一栏，未归组的进 Ungrouped 桶（默认，最常用）。
* ``tree``：项目按父子路径嵌套 —— ``D:\work`` 下面挂 ``D:\work\harness``。
* ``flat``：一栏到底。会话数量少或用户就是想全局搜索时最省事。
 */

export type GroupMode = 'workspace' | 'tree' | 'flat'

export const GROUP_MODES: Array<{ id: GroupMode; label: string }> = [
  { id: 'workspace', get label() { return t("ui.lib.sidebarGroups.85c489da893d") } },
  { id: 'tree', get label() { return t("ui.lib.sidebarGroups.514664132c03") } },
  { id: 'flat', get label() { return t("ui.lib.sidebarGroups.1b15dbc4df67") } },
]

export interface SidebarGroup {
  /** 稳定 key（归一化路径；未归组为 ''）。 */
  key: string
  workspaceId: string | null
  title: string
  path: string
  color: string | null
  /** 树形模式下的缩进层级；其他模式恒为 0。 */
  depth: number
  /** 有子项目时为 true —— 渲染成可展开的父节点。 */
  hasChildren: boolean
  collapsed: boolean
  sessions: SessionSummary[]
}

function sortByName(a: WorkspaceInfo, b: WorkspaceInfo): number {
  return a.name.localeCompare(b.name)
}

/** 按项目分组（默认视图）。 */
function workspaceGroups(
  sessions: SessionSummary[],
  workspaces: WorkspaceInfo[],
  collapsed: Set<string>,
): SidebarGroup[] {
  const buckets = groupSessionsByWorkspace(sessions, workspaces)
  return buckets.map((bucket) => ({
    key: bucket.key,
    workspaceId: bucket.workspace?.id ?? null,
    title: bucket.workspace?.name ?? t("ui.lib.sidebarGroups.097dcbb620db"),
    path: bucket.workspace?.path ?? '',
    color: bucket.workspace?.color ?? null,
    depth: 0,
    hasChildren: false,
    collapsed: collapsed.has(bucket.key),
    sessions: bucket.sessions,
  }))
}

/**
 * 寻找 target 在已注册项目中的最近祖先项目（最长匹配）。
 *
 * 保证中间没有注册的分类目录（如 ``apps/``）不会让深层子项目断链，
 * 同时依然保持"祖先必须是已注册项目"的边界 —— 否则每个项目都会挂到 ``D:\`` 变成巨树。
 */
export function findNearestAncestorWorkspace(
  target: WorkspaceInfo,
  workspaces: WorkspaceInfo[],
): WorkspaceInfo | null {
  const targetKey = normalizePath(target.path)
  if (!targetKey) return null
  let best: WorkspaceInfo | null = null
  let bestKey = ''
  for (const ws of workspaces) {
    if (ws.id === target.id) continue
    const candidateKey = normalizePath(ws.path)
    if (!candidateKey || candidateKey === targetKey) continue
    if (isAncestor(candidateKey, targetKey)) {
      if (candidateKey.length > bestKey.length) {
        best = ws
        bestKey = candidateKey
      }
    }
  }
  return best
}

/**
 * 项目树：把 ``D:\work`` 与 ``D:\work\harness`` 排成父子。
 *
 * 只有当**祖先目录本身也是注册过的项目**时才嵌进去。仅仅"路径上有个父目录"
 * 不算 —— 否则每个项目都会挂到 ``D:\`` 下面变成一棵没有意义的巨树。
 */
function treeGroups(
  sessions: SessionSummary[],
  workspaces: WorkspaceInfo[],
  collapsed: Set<string>,
): SidebarGroup[] {
  const childrenOf = new Map<string | null, WorkspaceInfo[]>()
  for (const ws of workspaces) {
    const parent = findNearestAncestorWorkspace(ws, workspaces)
    const parentKey = parent ? normalizePath(parent.path) : null
    const list = childrenOf.get(parentKey)
    if (list) list.push(ws)
    else childrenOf.set(parentKey, [ws])
  }

  const buckets = new Map<string, SessionSummary[]>()
  for (const bucket of groupSessionsByWorkspace(sessions, workspaces)) {
    buckets.set(bucket.key, bucket.sessions)
  }
  // 未归组会话单独成桶，挂在最后。
  const orphans: SessionSummary[] = []
  for (const [key, list] of buckets) {
    if (key === '') orphans.push(...list)
  }

  const out: SidebarGroup[] = []
  const walk = (parentKey: string | null, depth: number) => {
    const nodes = (childrenOf.get(parentKey) ?? []).slice().sort(sortByName)
    for (const ws of nodes) {
      const key = normalizePath(ws.path)
      const isCollapsed = collapsed.has(key)
      out.push({
        key,
        workspaceId: ws.id,
        title: ws.name,
        path: ws.path,
        color: ws.color,
        depth,
        hasChildren: (childrenOf.get(key) ?? []).length > 0,
        collapsed: isCollapsed,
        sessions: buckets.get(key) ?? [],
      })
      // 折叠的父节点不递归 —— 树形折叠必须把整棵子树（含子项目）一起隐藏，
      // 否则子项目仍独立显示在下面，折叠在树视图里就失去了意义。
      if (!isCollapsed) walk(key, depth + 1)
    }
  }
  walk(null, 0)

  if (orphans.length) {
    out.push({
      key: '',
      workspaceId: null,
      title: t("ui.lib.sidebarGroups.097dcbb620db"),
      path: '',
      color: null,
      depth: 0,
      hasChildren: false,
      collapsed: collapsed.has(''),
      sessions: orphans,
    })
  }
  return out
}

function flatGroups(sessions: SessionSummary[]): SidebarGroup[] {
  return [
    {
      key: '__all__',
      workspaceId: null,
      title: t("ui.lib.sidebarGroups.19c6c208f94e"),
      path: '',
      color: null,
      depth: 0,
      hasChildren: false,
      collapsed: false,
      sessions: sessions,
    },
  ]
}

/**
 * 编排出最终要渲染的分组列表。
 *
 * ``collapsed`` 里带 ``'__all__'`` 会被忽略 —— "全部会话"折叠起来毫无意义。
 */
export function buildGroups(
  sessions: SessionSummary[],
  workspaces: WorkspaceInfo[],
  mode: GroupMode,
  collapsed: Set<string> = new Set(),
): SidebarGroup[] {
  if (mode === 'flat') return flatGroups(sessions)
  if (mode === 'tree') return treeGroups(sessions, workspaces, collapsed)
  return workspaceGroups(sessions, workspaces, collapsed)
}

/** 搜索使用完整项目树，不改写用户保存的折叠状态。 */
export function searchSidebarGroups(
  sessions: SessionSummary[], workspaces: WorkspaceInfo[], mode: GroupMode,
  collapsed: Set<string>, query: string,
): SidebarGroup[] {
  return filterGroups(buildGroups(sessions, workspaces, mode, query.trim() ? new Set() : collapsed), query)
}

/**
 * 搜索过滤。
 *
 * 两条匹配路径：会话本身命中，或者**分组名**命中。后者很关键 —— 用户搜
 * "harness" 时想看的是整个项目，而不是项目里恰好标题带 harness 的那一段。
 * 组名命中时保留该组全部会话，否则搜项目会只看到零星几条。
 */
export function filterGroups(groups: SidebarGroup[], query: string): SidebarGroup[] {
  const q = query.trim()
  if (!q) return groups
  const out: SidebarGroup[] = []
  for (const group of groups) {
    if (group.title.toLowerCase().includes(q.toLowerCase()) || group.path.toLowerCase().includes(q.toLowerCase())) {
      out.push({ ...group, collapsed: false })
      continue
    }
    const sessions = group.sessions.filter((s) => matchesQuery(s, q))
    if (sessions.length) out.push({ ...group, sessions, collapsed: false })
  }
  return out
}

/** 折叠状态默认显示前 limit 条，不足五条的尾数直接并入。 */
export function collapseSessions(
  sessions: SessionSummary[],
  collapsed: boolean,
  limit: number,
  activeId?: string | null,
): { shown: SessionSummary[]; overflow: number } {
  if (!collapsed || listVisibleCount(sessions.length, limit) === sessions.length) {
    return { shown: sessions, overflow: 0 }
  }
  const shown = sessions.slice(0, limit)
  const active = activeId ? sessions.find(session => session.id === activeId) : undefined
  if (active && !shown.includes(active)) shown.push(active)
  return sessions.length - shown.length < 5 ? { shown: sessions, overflow: 0 } : { shown, overflow: sessions.length - shown.length }
}

export { basenameOf }
