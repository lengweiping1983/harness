/**
 * 项目 ↔ 会话的归组逻辑（纯函数）。
 *
 * 单独抽出来有两个原因：
 *   1. 这层判定最容易出错 —— 路径大小写、尾部分隔符、盘符、子目录，
 *      Windows 上一个反斜杠就能让整个分组塌掉，靠肉眼验成本极高；
 *   2. 前端测试环境不含 .tsx，所有组件逻辑必须落到 .ts 才能被覆盖。
 *
 * 归组键**必须与后端 :func:`normalize_id` 一致**，否则侧栏分出来的组
 * 和服务端认的项目对不上，文件浏览会直接 403。
 */

import type { SessionHeader, SessionSummary, WorkspaceInfo } from '@/types'

/** Browsers cannot resolve filesystem aliases such as Windows 8.3 paths. */
export function sessionDirectory(session: SessionHeader | null | undefined): string | null {
  return session?.resolved_cwd?.trim() || session?.cwd || null
}

/** 工作树对话归入来源项目，执行和媒体仍使用真实检出目录。 */
export function sessionProjectDirectory(session: SessionHeader | null | undefined): string | null {
  const source = session?.metadata?.project_cwd
  return typeof source === 'string' && source ? source : sessionDirectory(session)
}

/** 未选择或项目已失效时必须显式选择，不能隐式使用宿主启动目录。 */
export function newSessionDirectory(workspaces: WorkspaceInfo[], activeWorkspaceId: string | null): string | undefined {
  return workspaces.find(workspace => workspace.id === activeWorkspaceId && workspace.exists !== false)?.path
}

/** 归一化路径：解析 `.` / `..`、统一分隔符、大小写折叠。 */
export function normalizePath(raw: string | null | undefined): string {
  const text = (raw ?? '').trim()
  if (!text) return ''
  if (text === '/') return '/'
  const unified = text.replace(/\\/g, '/').replace(/\/+/g, '/').replace(/\/+$/, '')

  // 先摘出根前缀，再折叠中间段。顺序不能反：混在一起算的时候，
  // ``D:/../../x`` 里的 ``..`` 会把盘符本身弹出栈，最后剩下 ``x`` ——
  // 一个绝对路径被归一化成相对路径，于是它可能和别的项目的相对 key 撞上，
  // 侧栏就会把会话挂到完全不相干的项目下面。
  let prefix = ''
  let rest = unified
  const drive = /^([a-zA-Z]:)\//.exec(unified)
  if (drive) {
    prefix = drive[1] + '/'
    rest = unified.slice(drive[0].length)
  } else if (unified.startsWith('/')) {
    prefix = '/'
    rest = unified.slice(1)
  }

  const isAbsolute = prefix !== ''
  const stack: string[] = []
  for (const part of rest.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') {
      // 绝对路径不许越过根；相对路径（没有 prefix）要保留 leading '..'。
      if (stack.length && stack[stack.length - 1] !== '..') stack.pop()
      else if (!isAbsolute) stack.push('..')
      continue
    }
    stack.push(part)
  }

  let joined = stack.join('/')
  if (prefix) joined = prefix + joined
  // Windows 上路径不区分大小写；Linux 上区分。用启发式而不是平台嗅探：
  // 侧栏分组错了也只是多一个 Ungrouped，不该让真路径对不上。
  return /^[a-zA-Z]/.test(prefix) ? joined.toLowerCase() : joined
}

/**
 * 反查一个目录归属哪个项目：**最长前缀优先**。
 *
 * 精确相等是不够的 —— 用户在 ``D:\work\harness\web`` 里起的会话，
 * 精确匹配会把它甩进"未归组"，而用户明明注册过 ``D:\work\harness``。
 * 这类"明明加了项目却还是未归组"的困惑几乎无法自查。
 *
 * 最长优先是必须的：``D:\work`` 与 ``D:\work\harness`` 常常同时注册，
 * 取短的会让内层项目的会话全部沉到外层项目里，内层项目永远空着。
 */
export function matchWorkspaceFor(
  path: string | null | undefined,
  workspaces: WorkspaceInfo[],
): WorkspaceInfo | null {
  const key = normalizePath(path)
  if (!key) return null
  let best: WorkspaceInfo | null = null
  let bestKey = ''
  for (const ws of workspaces) {
    const candidate = normalizePath(ws.path) || ws.id
    if (!candidate) continue
    const hit =
      key === candidate || key.startsWith(candidate.endsWith('/') ? candidate : candidate + '/')
    if (!hit) continue
    if (candidate.length > bestKey.length) {
      best = ws
      bestKey = candidate
    }
  }
  return best
}

export interface WorkspaceGroup {
  /** 空串 = 未归组（会话的工作目录不在任何已注册项目里）。 */
  key: string
  workspace: WorkspaceInfo | null
  sessions: SessionSummary[]
}

/**
 * 把会话按项目分组。
 *
 * 项目解析规则：
 *   - 先按已注册项目匹配 ``session.cwd``；
 *   - 一个项目都没注册时全部落进未归组桶，**不会丢失会话**；
 *   - 组内按最近活跃排序，组间保持注册表顺序（用户排好的顺序要保留）。
 */
export function groupSessionsByWorkspace(
  sessions: SessionSummary[],
  workspaces: WorkspaceInfo[],
): WorkspaceGroup[] {
  const buckets = new Map<string, SessionSummary[]>()
  for (const session of sessions) {
    const matched = matchWorkspaceFor(sessionProjectDirectory(session), workspaces)
    const bucketKey = matched ? normalizePath(matched.path) || matched.id : ''
    const list = buckets.get(bucketKey)
    if (list) list.push(session)
    else buckets.set(bucketKey, [session])
  }

  const groups: WorkspaceGroup[] = []
  for (const ws of workspaces) {
    const key = normalizePath(ws.path) || ws.id
    const list = buckets.get(key)
    buckets.delete(key)
    groups.push({ key, workspace: ws, sessions: list ? sortSessions(list) : [] })
  }

  // 注册表里没有的目录 + 没有 cwd 的会话，合并进同一个未归组桶。
  const orphans: SessionSummary[] = []
  for (const list of buckets.values()) orphans.push(...list)
  if (orphans.length) {
    groups.push({ key: '', workspace: null, sessions: sortSessions(orphans) })
  }
  return groups
}

/** 最近活跃优先；时间戳相同的按 id 兜底，保证顺序稳定不跳。 */
export function sortSessions(list: SessionSummary[]): SessionSummary[] {
  return [...list].sort((a, b) => {
    const ta = a.last_activity ?? a.created_at ?? 0
    const tb = b.last_activity ?? b.created_at ?? 0
    return tb - ta || a.id.localeCompare(b.id)
  })
}

/** 会话所选模型：会话级覆盖优先，其次全局默认；不代表已发出请求的模型。 */
export function sessionModelOf(
  session: Pick<SessionSummary, 'model'> | null | undefined,
  fallback: string,
): string {
  return session?.model || fallback
}

/** 侧栏搜索：标题 / 摘要 / cwd / 模型，全部小写匹配。 */
export function matchesQuery(session: SessionSummary, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [session.title, session.summary, session.cwd, session.resolved_cwd, session.id, session.model].some((value) =>
    String(value ?? '').toLowerCase().includes(q),
  )
}

/** 目录名：``D:\\work\\harness`` -> ``harness``；根目录回落成盘符。 */
export function basenameOf(path: string): string {
  const unified = (path ?? '').replace(/\\/g, '/').replace(/\/+$/, '')
  const tail = unified.split('/').pop()
  return tail || unified || path
}

/** 父目录；已经在根则返回 null。 */
export function parentOf(path: string): string | null {
  const unified = (path ?? '').replace(/\\/g, '/').replace(/\/+$/, '')
  const cut = unified.lastIndexOf('/')
  if (cut <= 0) return null
  return unified.slice(0, cut)
}

/** 该路径是否落在 root 之内（用于文件面板的祖先判定）。 */
export function isAncestor(root: string, path: string): boolean {
  const a = normalizePath(root)
  const b = normalizePath(path)
  if (!a || !b) return false
  const lowered = a.toLowerCase()
  const target = b.toLowerCase()
  return b === a || target.startsWith(lowered.endsWith('/') ? lowered : lowered + '/')
}
